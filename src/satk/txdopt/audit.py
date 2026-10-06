"""``satk texture audit``: what makes the TXDs of a target big or wrong, with a per-issue summary.

Issues (``saving`` = bytes the fix removes; negative = the fix costs bytes):

* ``oversized`` -- the long side is over ``max``; saving at the capped size;
* ``uncompressed`` -- raw or paletted (D3D9 expands palettes to 32 bit); saving as DXT1/DXT5 by alpha;
* ``npot`` -- a side is not a power of two;
* ``no_mips`` -- one level and a side of 64 or more (a full chain costs about a third more, but stops shimmer);
  never for TXDs of vehicles, peds, weapons and vehicle upgrades: their vanilla textures have one level only
  (the envelope counts them in ``one_level``);
* ``dxt1_holes`` -- DXT1 with transparent texels while the alpha flag is off: they render black;
* ``alpha_unused`` -- alpha flag on but every texel opaque (DXT3/DXT5 -> DXT1 is lossless and halves it);
* ``duplicate`` -- the same pixels (mip 0 + palette hash) as an earlier texture of the target;
* ``dup_name`` -- a second texture with the same name in one TXD (the game sees one of them);
* ``unused`` -- with ``unused``: no DFF of the TXD's user models names it (:mod:`.usage`);
* ``platform`` -- a PS2/Xbox/other native the PC game cannot use.
"""

from __future__ import annotations

from ..core.errors import SatkError
from .analyze import RAW, chain_bytes, facts, full_levels, is_pow2
from .optimize import target_dims

__all__ = ["ISSUES", "COLS", "SUMMARY_COLS", "audit"]

#: issue -> what fixes it (shown in the summary table)
ISSUES: dict[str, str] = {
    "oversized": "texture optimize --max N",
    "uncompressed": "texture optimize (--dxt auto)",
    "npot": "texture optimize (powers of two by default)",
    "no_mips": "texture optimize --mips",
    "dxt1_holes": "re-export with alpha (DXT1+a or DXT5) or fill the holes",
    "alpha_unused": "texture optimize (opaque DXT3/DXT5 -> DXT1, lossless)",
    "duplicate": "texture optimize --dedupe (same TXD) or --share (txdp parent)",
    "dup_name": "rename or remove one of them",
    "unused": "texture optimize --drop-unused",
    "platform": "convert to a PC (D3D9) texture",
}
COLS = ["txd", "texture", "issue", "format", "bytes", "saving", "detail"]
SUMMARY_COLS = ["issue", "count", "bytes", "saving", "fix"]
MIPS_MIN = 64


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    if not str(cursor).isdigit():
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}", hint="pass the 'next' value of the previous page")
    return int(cursor)


def audit(target: str, *, max_side: int = 1024, issues: list[str] | None = None, unused: bool = False,
          keep: list[str] | None = None, profile: str = "vanilla", limit: int = 20, cursor: str | None = None) -> dict:
    from ..core.envelope import table
    from ..core.registry import report_progress
    from ..formats.rw import FormatError, rw_payload_size
    from ..formats.txd import parse_txd
    from .inputs import load_bundle
    from .txdedit import native_spans
    from .usage import Usage, keep_matcher

    want = set(issues or [])
    bad = sorted(want - set(ISSUES))
    if bad:
        raise SatkError("BAD_PARAMS", f"unknown issue {bad[0]!r}", data={"issues": list(ISSUES)},
                        hint="one of " + ", ".join(ISSUES))
    if unused and want and "unused" not in want:
        unused = False
    if "unused" in want:
        unused = True
    off = _offset(cursor)
    findings: list[list] = []
    undecided: list[str] = []
    seen_hash: dict[str, str] = {}
    n_tex = 0
    total_bytes = 0
    with load_bundle(target, profile) as bundle:
        warn = list(bundle.warn)
        items = sorted(bundle.of("txd"), key=lambda x: x.rel.lower())
        if not items:
            raise SatkError("NOT_FOUND", f"no TXD in {bundle.label}", hint="give a .txd, a mod folder, .zip or .img")
        usage = Usage(bundle, profile)              # users decide unused textures and the one-level classes
        keepm = keep_matcher(keep)
        one_level: dict[str, int] = {}
        try:
            for i, it in enumerate(items):
                report_progress(i, len(items), it.rel)
                try:
                    raw = it.read()
                except SatkError as e:
                    warn.append(f"UNREADABLE: {it.rel}: {e.msg}")
                    continue
                n = rw_payload_size(raw)
                data = bytes(raw[:n]) if n else bytes(raw)
                try:
                    txd = parse_txd(data)
                    spans = native_spans(data)
                except FormatError as e:
                    warn.append(f"UNSUPPORTED: {it.rel}: not a parsable TXD ({e})")
                    continue
                total_bytes += len(data)
                names = usage.verdict(it.stem).names if unused else None
                if unused and names is None:
                    undecided.append(it.stem)
                cls = usage.one_level_class(it.stem)
                if cls:
                    one_level[cls] = one_level.get(cls, 0) + 1
                seen_names: set[str] = set()
                for t in txd.textures:
                    n_tex += 1
                    s, e = spans[t.idx]
                    f = facts(data, t, e - s)
                    findings += _issues(it.rel, f, max_side, seen_hash, seen_names, names, keepm, mips=not cls)
        finally:
            if unused:
                warn.extend(usage.warn)
                if undecided:
                    warn.append(f"KEPT_WHOLE: {len(undecided)} TXD(s) without known users are not checked for unused "
                                f"textures ({usage.verdict(undecided[0]).why}): {', '.join(undecided[:8])}"
                                + (" ..." if len(undecided) > 8 else ""))
            usage.close()
        label = bundle.label
    summary: dict[str, list] = {}
    for r in findings:
        s = summary.setdefault(r[2], [r[2], 0, 0, 0, ISSUES[r[2]]])
        s[1] += 1
        s[2] += r[4]
        s[3] += r[5]
    srows = sorted(summary.values(), key=lambda r: (-r[3], -r[1], r[0]))
    shown = [r for r in findings if not want or r[2] in want]
    shown.sort(key=lambda r: (-r[5], -r[4], r[0].lower(), r[1].lower(), r[2]))
    lim = max(1, int(limit))
    page = shown[off:off + lim]
    nxt = str(off + lim) if off + lim < len(shown) else None
    env = table(COLS, page, total=len(shown), next=nxt, warn=list(dict.fromkeys(warn))[:20])
    env["summary"] = {"cols": SUMMARY_COLS, "rows": srows}
    env.update(source=label, txds=len(items), textures=n_tex, bytes=total_bytes)
    if one_level:
        env["one_level"] = dict(sorted(one_level.items()))
    if not findings:
        env["hint"] = "no issue found"
    return env


def _issues(rel: str, f, max_side: int, seen_hash: dict[str, str], seen_names: set[str], names, keepm, *,
            mips: bool = True) -> list[list]:
    t = f.t
    out: list[list] = []
    fmt = f.label

    def add(issue: str, saving: int, detail: str) -> None:
        out.append([rel, t.name, issue, fmt, f.data, int(saving), detail])

    if t.unsupported:
        add("platform", 0, f"platform 0x{t.platform:X}")
        return out
    lname = t.name.lower()
    if lname in seen_names:
        add("dup_name", 0, "another texture of this TXD has the same name")
    seen_names.add(lname)
    if names is not None and t.name and lname not in names and not keepm(t.name):
        add("unused", f.native, "no DFF of the TXD's models names it")
    if f.hash:
        first = seen_hash.get(f.hash)
        if first is not None:
            add("duplicate", f.data, f"same pixels as {first}")
        else:
            seen_hash[f.hash] = f"{rel}/{t.name}"
    if max_side and max(t.w, t.h) > max_side:
        nw, nh = target_dims(t.w, t.h, max_side, pot=is_pow2(t.w) and is_pow2(t.h))
        n = min(t.levels, full_levels(nw, nh))
        add("oversized", f.data - chain_bytes(t.d3dfmt, nw, nh, n) + 4 * n, f"{t.w}x{t.h} > {max_side}")
    if t.d3dfmt in RAW and min(t.w, t.h) >= 4:
        target = "DXT5" if f.alpha == "smooth" else "DXT1"
        saving = f.data - chain_bytes(target, t.w, t.h, t.levels) + 4 * t.levels
        if saving > 0:
            add("uncompressed", saving, f"{t.d3dfmt} -> {target}" + (" (alpha)" if f.alpha != "opaque" else ""))
    if not (is_pow2(t.w) and is_pow2(t.h)):
        add("npot", 0, f"{t.w}x{t.h}")
    if mips and t.levels <= 1 and max(t.w, t.h) >= MIPS_MIN:
        cost = chain_bytes(t.d3dfmt, t.w, t.h, full_levels(t.w, t.h)) - chain_bytes(t.d3dfmt, t.w, t.h, 1)
        add("no_mips", -cost, f"{full_levels(t.w, t.h)} levels would add {cost} bytes")
    if f.holes and not t.alpha:
        add("dxt1_holes", 0, "transparent texels render black (alpha flag off)")
    if t.alpha and f.alpha == "opaque":
        if t.d3dfmt in ("DXT3", "DXT5"):
            add("alpha_unused", f.data // 2, f"{t.d3dfmt} -> DXT1 is lossless")
        elif t.d3dfmt == "DXT1":
            add("alpha_unused", 0, "alpha flag on, no transparent texel (blending for nothing)")
    return out

