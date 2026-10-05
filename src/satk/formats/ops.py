"""Operations of ``satk.formats`` (owner WP-02): ``formats selftest|ls|dump`` (CLI only, SPEC §4.6).

``dump`` understands IMG, TXD, DFF, COL, IFP, IDE, IPL (text and ``bnry``), ZON, DAT and falls back to the
raw RW chunk tree.

Module-level imports stay stdlib/satk only (SPEC §2.3).
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Literal

from ..core.envelope import clamp_limit, obj, table
from ..core.errors import SatkError
from ..core.ids import Sid
from ..core.paths import jpath, open_ro, profile_root
from ..core.registry import op
from . import selftest as _selftest
from .col import iter_col
from .dat import parse_dat, resolve_ci, split_dos_path
from .dff import FLAG_NAMES, FX_NAMES, find_embedded_col, scan_dff
from .ide import parse_ide
from .ifp import parse_ifp
from .img import ImgArchive
from .ipl import parse_ipl_binary, parse_ipl_text, rz_deg, world_quat
from .rw import FormatError, iter_children, rw_payload_size, rw_version
from .txd import parse_txd, texture_hash
from .zon import parse_zon

Profile = Literal["vanilla", "installed", "samp"]


def _fmt_error(e: FormatError, what: str) -> SatkError:
    return SatkError("UNSUPPORTED", f"cannot parse {what}: {e}", data={"kind": e.kind, "offset": e.offset})


def _resolve(path: str, profile: str) -> Path:
    """Absolute path as given, else relative to the profile root (case-insensitive)."""
    p = Path(path)
    if p.is_absolute():
        if p.exists():
            return p
        raise SatkError("NOT_FOUND", f"no such file: {jpath(p)}")
    root = profile_root(profile)
    hit = resolve_ci(root, path)
    if hit is None:
        raise SatkError("NOT_FOUND", f"no such file under the {profile} root: {path}",
                        hint=f"satk formats ls <img> --profile {profile}")
    return hit


def _identity(path: Path, entry: str, profile: str) -> dict[str, str]:
    """How a dump names its target (SPEC 3.2: SID keys never hold absolute paths).

    A file under the profile root -> ``{"id": "file:<relpath>[/<entry>]"}``, the canonical SID (lower case,
    forward slashes, relative to the root, real on-disk names), whatever spelling the target used.
    Anything else -> ``{"path": <absolute path>, "entry": <entry>}``.
    """
    try:
        rel = os.path.relpath(path, profile_root(profile))
    except (SatkError, ValueError, OSError):      # other drive, profile not configured
        rel = None
    parts = split_dos_path(rel) if rel else None  # None for "..\\..." (outside the root)
    if parts:
        try:
            return {"id": str(Sid("file", "/".join(parts + [entry] if entry else parts)))}
        except SatkError:                         # e.g. '@' in a file name: not representable as a SID
            pass
    out = {"path": jpath(path)}
    if entry:
        out["entry"] = entry
    return out


@op("formats.selftest", summary="Parse a game root with satk.formats and compare counters with the golden numbers.",
    summary_ru="Самопроверка парсеров форматов по эталонным числам (Приложение A).", mcp=False,
    examples=("satk formats selftest --profile vanilla", "satk formats selftest --root \"<game folder>\" --profile installed"))
def formats_selftest(root: str | None = None, profile: Profile = "vanilla", bench: bool = False,
                     quick: bool = False, geometry: bool = False, dxt: bool = False) -> dict:
    """Self-test of the format parsers.

    Args:
        root: game root (default: the profile root from satk.toml).
        profile: golden set and load order: vanilla|installed|samp.
        bench: add per-stage timings.
        quick: skip the TXD and DFF stages (and the texture-reference check).
        geometry: also decode every gta3/gta_int geometry (full vertex/index arrays).
        dxt: also run spike S2: DXT backend parity on the reference textures + Mpix/s.
    """
    r = Path(root) if root else profile_root(profile)
    if not (r / "data").is_dir():
        raise SatkError("NOT_FOUND", f"not a game root (no data/): {jpath(r)}")
    try:
        res = _selftest.run(r, profile, quick=quick, bench=bench, geometry=geometry, dxt=dxt)
    except FormatError as e:
        raise _fmt_error(e, jpath(r)) from None
    res["root"] = jpath(r)
    if not res["ok"]:
        res["error"] = {"code": "INTERNAL", "msg": f"{len(res['failed'])} counter(s) differ from the golden numbers"}
    return res


@op("formats.ls", summary="List the entries of an IMG archive.", summary_ru="Список записей IMG-архива.",
    mcp=False, examples=("satk formats ls models/gta3.img --name infernus", "satk formats ls models/player.img --ext txd"))
def formats_ls(img: str, name: str | None = None, ext: str | None = None, limit: int = 20, cursor: str | None = None,
               profile: Profile = "vanilla") -> dict:
    """List IMG entries.

    Args:
        img: archive path (absolute, or relative to the profile root, any case).
        name: case-insensitive substring of the entry name.
        ext: only this extension (dff, txd, col, ipl, ifp, ...).
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
        profile: profile whose root resolves relative paths.
    """
    p = _resolve(img, profile)
    lim = clamp_limit(limit)
    try:
        start = int(cursor) if cursor else 0
    except ValueError:
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}") from None
    try:
        with ImgArchive.open(p) as a:
            ents = a.entries
            ver = a.version
    except FormatError as e:
        raise _fmt_error(e, jpath(p)) from None
    if name:
        nl = name.lower()
        ents = [e for e in ents if nl in e.name.lower()]
    if ext:
        el = ext.lower().lstrip(".")
        ents = [e for e in ents if e.ext == el]
    page = ents[start:start + lim]
    nxt = str(start + lim) if start + lim < len(ents) else None
    env = table(["idx", "name", "offset", "size", "stream_sectors", "archive_sectors"],
                [[e.idx, e.name, e.abs_offset, e.size, e.stream_sectors, e.archive_sectors] for e in page],
                total=len(ents), next=nxt)
    env["path"] = jpath(p)
    env["version"] = ver
    return env


def _load_target(target: str, profile: str) -> tuple[str, bytes | None, Path, str]:
    """-> (label, data or None for IMG itself, file path, entry name or '')."""
    t = target[5:] if target.lower().startswith("file:") else target
    norm = t.replace("\\", "/")
    low = norm.lower()
    cut = low.find(".img/")
    if cut > 0:
        arc, entry = norm[:cut + 4], norm[cut + 5:]
        p = _resolve(arc, profile)
        try:
            with ImgArchive.open(p) as a:
                e = a.find(entry)
                if e is None:
                    raise SatkError("NOT_FOUND", f"no entry {entry!r} in {jpath(p)}",
                                    hint=f"satk formats ls {arc} --name {entry.rsplit('.', 1)[0]}")
                return f"{arc}/{e.name}", a.read(e), p, e.name
        except FormatError as ex:
            raise _fmt_error(ex, jpath(p)) from None
    p = _resolve(t, profile)
    if p.suffix.lower() in (".img", ".dir"):
        return t, None, p, ""
    with open_ro(p) as f:
        return t, f.read(), p, ""


def _rw_tree(buf: bytes, limit: int) -> list[list]:
    rows: list[list] = []
    end = rw_payload_size(buf) or 0

    def walk(start: int, stop: int, depth: int) -> None:
        for ch in iter_children(buf, start, stop, strict=False):
            if len(rows) >= limit:
                return
            rows.append([depth, ch.name, ch.size, f"0x{rw_version(ch.libid):X}", ch.data_off - 12])
            if ch.type in (0x10, 0x0E, 0x1A, 0x0F, 0x08, 0x07, 0x06, 0x14, 0x16, 0x15, 0x03, 0x2B):
                walk(ch.data_off, ch.end, depth + 1)

    walk(0, end, 0)
    return rows


@op("formats.dump", summary="Describe one game file or IMG entry (IMG, TXD, DFF, COL, IFP, IDE, IPL, ZON, DAT, RW tree).",
    summary_ru="Разбор одного файла или записи IMG: IMG, TXD, DFF, COL, IFP, IDE, IPL, ZON, DAT, дерево RW.", mcp=False,
    examples=("satk formats dump models/gta3.img/infernus.dff --level full", "satk formats dump models/gta3.img/infernus.txd --level full", "satk formats dump data/maps/la/lae2.ipl"))
def formats_dump(target: str, level: Literal["stats", "full", "tree"] = "stats", limit: int = 20,
                 profile: Profile = "vanilla") -> dict:
    """Dump a file.

    Args:
        target: path (absolute or relative to the profile root), `<img>/<entry>` or a `file:` SID.
        level: stats = counters only; full = also the rows (textures, materials, definitions, placements);
            tree = the raw RenderWare chunk tree (any RW file, also a broken one).
        limit: max rows for level=full (max 500).
        profile: profile whose root resolves relative paths.
    """
    lim = clamp_limit(limit)
    label, data, path, entry = _load_target(target, profile)
    ident = _identity(path, entry, profile)
    ext = (entry or path.name).rsplit(".", 1)[-1].lower()
    full = level == "full"
    try:
        if level == "tree" and data is not None:
            return _dump_tree(data, label, lim, ident)
        if data is None:  # IMG archive itself
            with ImgArchive.open(path) as a:
                exts = Counter(e.ext for e in a.entries)
                return obj(**ident, kind="img", version=a.version, entries=len(a.entries),
                           size=a.file_size, by_ext=dict(exts.most_common()),
                           size_in_archive_nonzero=sum(1 for e in a.entries if e.archive_sectors))
        if data[:4] == b"bnry":
            insts, cars = parse_ipl_binary(data)
            env = obj(**ident, kind="ipl", format="binary", inst=len(insts), cars=len(cars))
            if full:
                env.update(_inst_rows(insts, lim))
            return env
        if ext == "txd" or (len(data) >= 4 and data[:4] == b"\x16\x00\x00\x00"):
            t = parse_txd(data)
            env = obj(**ident, kind="txd", count=t.count, textures=len(t.textures), device_id=t.device_id,
                      rw_version=f"0x{t.rw_version:X}", rw_size=rw_payload_size(data),
                      by_fmt=dict(Counter(x.d3dfmt for x in t.textures).most_common()))
            if full:
                env["cols"] = ["idx", "name", "mask", "fmt", "w", "h", "levels", "alpha", "platform", "hash"]
                env["rows"] = [[x.idx, x.name, x.mask, x.d3dfmt, x.w, x.h, x.levels, x.alpha, x.platform,
                                texture_hash(data, x).hex()] for x in t.textures[:lim]]
            return env
        head = data[:4]
        if ext == "dff" or (len(data) >= 12 and head in (b"\x10\x00\x00\x00", b"\x2b\x00\x00\x00")):
            return _dump_dff(data, ident, full, lim)
        if ext == "col" or head in (b"COLL", b"COL2", b"COL3", b"COL4"):
            return _dump_col(data, ident, full, lim)
        if ext == "ifp" or head in (b"ANP3", b"ANP2", b"ANPK"):
            fmt, pack, anims = parse_ifp(data)
            env = obj(**ident, kind="ifp", format=fmt, pack=pack, anims=len(anims),
                      bones_max=max((a[1] for a in anims), default=0), frames_max=max((a[2] for a in anims), default=0))
            if full:
                env["cols"] = ["idx", "anim", "bones", "frames"]
                env["rows"] = [[i, n, b, f] for i, (n, b, f) in enumerate(anims[:lim])]
            return env
        if ext == "zon":
            errs: list = []
            zones = parse_zon(data.decode("latin-1"), errors=errs)
            env = obj(**ident, kind="zon", zones=len(zones), by_level=dict(Counter(z["level"] for z in zones)),
                      errors=[f"{n}: {m}" for n, m in errs[:10]])
            if full:
                env["cols"] = ["line", "name", "label", "type", "level", "min", "max"]
                env["rows"] = [[z["line"], z["name"], z["label"], z["type"], z["level"],
                                [round(v, 2) for v in z["min"]], [round(v, 2) for v in z["max"]]] for z in zones[:lim]]
            return env
        if ext == "ide":
            errs = []
            defs, txdp, fx = parse_ide(data.decode("latin-1"), errors=errs)
            env = obj(**ident, kind="ide", defs=len(defs), by_sec=dict(Counter(d.sec for d in defs)),
                      txdp=len(txdp), fx2d=len(fx), errors=[f"{n}: {m}" for n, m in errs[:10]])
            if full:
                env["cols"] = ["line", "sec", "id", "name", "txd", "draw", "flags"]
                env["rows"] = [[d.line, d.sec, d.id, d.name, d.txd, d.draw, d.flags] for d in defs[:lim]]
            return env
        if ext == "ipl":
            errs = []
            insts, items = parse_ipl_text(data.decode("latin-1"), errors=errs)
            env = obj(**ident, kind="ipl", format="text", inst=len(insts),
                      items={k: len(v) for k, v in items.items()}, errors=[f"{n}: {m}" for n, m in errs[:10]])
            if full:
                env.update(_inst_rows(insts, lim))
            return env
        if ext in ("dat", "two") and not entry:
            lines = parse_dat(data.decode("latin-1"))
            env = obj(**ident, kind="dat", lines=len(lines), by_key=dict(Counter(x.key for x in lines)))
            if full:
                env["cols"] = ["line", "key", "arg"]
                env["rows"] = [[x.line, x.key, x.arg] for x in lines[:lim]]
            return env
        if rw_payload_size(data):
            return _dump_tree(data, label, lim, ident)
    except FormatError as e:
        err = _fmt_error(e, label)
        if rw_payload_size(data or b""):
            err.hint = f"satk formats dump {target} --level tree"
        raise err from None
    raise SatkError("UNSUPPORTED", f"no dumper for {label!r} (img, txd, dff, col, ifp, ide, ipl, zon, dat, RW tree)",
                    data={"magic": data[:4].hex()})


def _r2(v) -> list:
    return [round(x, 2) for x in v]


def _dump_dff(data: bytes, ident: dict, full: bool, lim: int) -> dict:
    info = scan_dff(data)
    col = find_embedded_col(data)
    col_name = None
    if col is not None:
        try:
            col_name = next(iter_col(data[col[0]:col[0] + col[1]])).name
        except (FormatError, StopIteration):
            col_name = "?"
    textures = sorted({m.texture for m in info.materials if m.texture}, key=str.lower)
    env = obj(**ident, kind="dff", rw_version=f"0x{info.rw_version:X}", clumps=info.clumps,
              atomics=info.atomics, frames=len(info.frames), geoms=len(info.geoms), materials=len(info.materials),
              verts=info.verts, tris=info.tris, flags=[n for b, n in FLAG_NAMES.items() if info.flags & b],
              effects=len(info.effects), textures=textures[:lim], textures_total=len(textures),
              bbox=_r2(info.bbox) if info.bbox else None, bsphere=_r2(info.bsphere) if info.bsphere else None,
              embedded_col=col_name, plugins=sorted(f"0x{p:x}" for p in info.plugins))
    if full:
        env["cols"] = ["geom", "idx", "texture", "mask", "rgba", "fx", "color_slot"]
        env["rows"] = [[m.geom, m.idx, m.texture, m.mask, f"{m.rgba:08x}",
                        "|".join(n for b, n in FX_NAMES.items() if m.fx & b) or None, m.color_slot]
                       for m in info.materials[:lim]]
        env["geom_rows"] = [[g.idx, g.verts, g.tris, g.uv_sets, g.strip, g.frame,
                             info.frames[g.frame].name if 0 <= g.frame < len(info.frames) else None]
                            for g in info.geoms[:lim]]
        env["geom_cols"] = ["idx", "verts", "tris", "uv_sets", "strip", "frame", "frame_name"]
        env["frame_names"] = [f.name for f in info.frames[:lim]]
        if info.effects:
            env["effects_list"] = [[e.idx, e.type_name, _r2(e.pos)] for e in info.effects[:lim]]
    return env


def _dump_col(data: bytes, ident: dict, full: bool, lim: int) -> dict:
    errs: list = []
    models = list(iter_col(data, errors=errs))
    env = obj(**ident, kind="col", models=len(models),
              by_version=dict(Counter(f"COL{m.version}" if m.version > 1 else "COLL" for m in models)),
              faces=sum(m.faces for m in models), errors=[f"#{i}: {m}" for i, m in errs[:10]])
    if full:
        env["cols"] = ["idx", "name", "version", "spheres", "boxes", "verts", "faces", "shadow_faces", "bbox"]
        env["rows"] = [[m.idx, m.name, m.version, m.spheres, m.boxes, m.verts, m.faces, m.shadow_faces, _r2(m.bbox)]
                       for m in models[:lim]]
    return env


def _dump_tree(data: bytes, label: str, lim: int, ident: dict) -> dict:
    size = rw_payload_size(data)
    if not size:
        raise SatkError("UNSUPPORTED", f"{label!r} is not a RenderWare file", data={"magic": data[:4].hex()})
    rows = _rw_tree(data, lim)
    env = obj(**ident, kind="rw", rw_size=size, top=rows[0][1] if rows else None)
    env["cols"] = ["depth", "chunk", "size", "version", "offset"]
    env["rows"] = rows
    return env


def _inst_rows(insts, lim: int) -> dict:
    rows = []
    for i in insts[:lim]:
        rz = rz_deg(i.q, i.interior)
        rows.append([i.idx, i.model_id, i.name, i.interior & 0xFF, i.interior >> 8,
                     [round(v, 2) for v in i.pos],
                     round(rz, 1) if rz is not None else [round(v, 4) for v in world_quat(i.q)], i.lod])
    return {"cols": ["idx", "model", "name", "area", "iflags", "pos", "rz_or_world_q", "lod"], "rows": rows}
