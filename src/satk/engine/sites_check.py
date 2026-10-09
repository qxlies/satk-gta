"""``satk engine sites-check``: every check of the patch-site manifest against the stock exe.

Checks (finding codes in brackets):

* TOML and schema 1 [TOML_PARSE, SCHEMA], unique group/site/report ids, id syntax, report-id block
  [DUP_GROUP_ID, DUP_SITE_ID, DUP_REPORT_ID, BAD_ID, REPORT_ID_RANGE];
* kind/length consistency [KIND_LEN], golden window covers the written range [GOLDEN_WINDOW],
  alt/array/accept fields [ALT_BAD, ARRAY_BAD, ACCEPT_BAD];
* the manifest ``exe_sha256`` is the stock exe [EXE_SHA]; golden bytes equal the stock exe
  [GOLDEN_MISMATCH, GOLDEN_UNBACKED]; absref operands lie inside ``[base, base+size+stride]`` [ARRAY_RANGE];
* no written byte overlaps a SecuROM key window [OVERLAP_KEY], a HOODLUM stolen-instruction site
  [OVERLAP_STOLEN], the ``.text`` slot of a function relocated into ``.HOODLUM``
  [SITE_IN_RELOCATED_FUNCTION], a trunk patch of the fork [OVERLAP_TRUNK] (unless ``allow_overlap`` names it)
  or another site [OVERLAP_SITE];
* the generated header is present and fresh [HEADER_MISSING, HEADER_STALE].

Sites of read-only groups (``id.anchors``) and sites marked ``readonly = true`` are only read by the
patcher: they get the structural and golden checks but not the write-overlap checks.
Stdlib only.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from pathlib import Path

from .securom import KEY_WINDOW
from .sites_data import SiteInputs
from .sites_gen import generate_header
from .sites_manifest import Finding, Manifest, Site, site_is_readonly, validate_structure

__all__ = ["CheckResult", "check_manifest", "parse_allow"]

_ALLOW_RE = re.compile(r"^(?:(trunk|key|stolen|reloc):(0x[0-9A-Fa-f]+)|site:([a-z0-9_.]+/[a-z0-9_.]+))$")


@dataclass
class CheckResult:
    errors: list[Finding]
    warnings: list[Finding]
    groups: int
    sites: int
    written_sites: int

    @property
    def ok(self) -> bool:
        return not self.errors


class _Intervals:
    """Sorted half-open intervals with an overlap query."""

    def __init__(self, items: list[tuple[int, int, object]]):
        self.items = sorted(items, key=lambda x: (x[0], x[1]))
        self.starts = [i[0] for i in self.items]
        self.maxlen = max((hi - lo for lo, hi, _ in self.items), default=0)

    def overlapping(self, lo: int, hi: int) -> list[tuple[int, int, object]]:
        i = bisect.bisect_left(self.starts, lo - self.maxlen)
        out = []
        while i < len(self.items) and self.items[i][0] < hi:
            a, b, p = self.items[i]
            if a < hi and lo < b:
                out.append(self.items[i])
            i += 1
        return out


def parse_allow(tokens) -> list[tuple[str, int | str]]:
    """``["trunk:0x5534F9", "site:g/s"]`` -> ``[("trunk", 0x5534F9), ("site", "g/s")]`` (bad tokens raise ValueError)."""
    out: list[tuple[str, int | str]] = []
    for t in tokens:
        m = _ALLOW_RE.match(t)
        if not m:
            raise ValueError(f"bad allow_overlap entry {t!r} (use trunk:0xADDR, key:0xADDR, stolen:0xADDR, "
                             f"reloc:0xADDR or site:group/site)")
        if m.group(3):
            out.append(("site", m.group(3)))
        else:
            out.append((m.group(1), int(m.group(2), 16)))
    return out


def _hexs(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def check_manifest(man: Manifest, inputs: SiteInputs, header_text: str | None, *,
                   header_label: str = "SaeSites.gen.h", check_header: bool = True) -> CheckResult:
    """Run all checks; ``header_text`` is the current header file content (``None`` = missing)."""
    errors: list[Finding] = list(validate_structure(man))
    warnings: list[Finding] = []
    img = inputs.image
    if man.exe_sha256 != img.sha256:
        errors.append(Finding("EXE_SHA", "manifest",
                              f"exe_sha256 {man.exe_sha256[:12]}... is not the checked exe {img.sha256[:12]}..."))

    keys = _Intervals([(lo, hi, lo) for lo, hi in inputs.keys.windows()])
    stolen = _Intervals([(s.jmp_at, s.jmp_at + s.length, s) for s in inputs.stolen])
    relocs = _Intervals([(r.entry, r.end, r) for r in inputs.relocs])
    trunk = _Intervals([(t.addr, t.end, t) for t in inputs.trunk])

    written: list[tuple[Site, str]] = []  # (site, group id)
    used_allow: dict[str, set[tuple[str, int | str]]] = {}
    n_sites = 0
    for g in man.groups:
        for s in g.sites:
            n_sites += 1
            w = s.key
            try:
                allow = parse_allow(s.allow_overlap)
            except ValueError as e:
                errors.append(Finding("SCHEMA", w, str(e), s.va))
                allow = []
            allow_set = set(allow)
            used = used_allow.setdefault(w, set())

            def allowed(kind: str, ident) -> bool:
                if (kind, ident) in allow_set:
                    used.add((kind, ident))
                    return True
                return False

            # ---- golden bytes against the stock exe
            gl = len(s.golden)
            if gl:
                found = img.read(s.golden_va, gl)
                if len(found) != gl:
                    if s.kind != "data32":
                        errors.append(Finding("GOLDEN_UNBACKED", w,
                                              f"golden window 0x{s.golden_va:X}+{gl} is not backed by the exe file", s.va))
                elif found != s.golden:
                    errors.append(Finding("GOLDEN_MISMATCH", w,
                                          f"golden at 0x{s.golden_va:X}: manifest {_hexs(s.golden)}, exe {_hexs(found)}",
                                          s.va))
            # ---- absref operand inside the array window
            if s.kind == "absref" and s.array is not None and s.len == 4:
                op = img.u32(s.va)
                base, size, stride = s.array
                if op is None:
                    errors.append(Finding("GOLDEN_UNBACKED", w, "absref operand is not backed by the exe file", s.va))
                elif not base <= op <= base + size + stride:
                    errors.append(Finding("ARRAY_RANGE", w,
                                          f"stock operand 0x{op:X} outside [0x{base:X}, 0x{base + size + stride:X}]", s.va))
            if site_is_readonly(g, s) or s.len <= 0:
                continue
            lo, hi = s.va, s.va + s.len
            written.append((s, g.id))
            # ---- SecuROM key windows
            for a, b, _ in keys.overlapping(lo, hi):
                if not allowed("key", a):
                    errors.append(Finding("OVERLAP_KEY", w,
                                          f"[0x{lo:X}, 0x{hi:X}) overlaps the SecuROM key window 0x{a:X}..0x{b - 1:X} "
                                          f"({KEY_WINDOW} bytes)", s.va))
            # ---- stolen-instruction sites
            for a, b, st in stolen.overlapping(lo, hi):
                if not allowed("stolen", a):
                    errors.append(Finding("OVERLAP_STOLEN", w,
                                          f"[0x{lo:X}, 0x{hi:X}) overlaps the HOODLUM stolen site 0x{a:X} "
                                          f"(jmp to stub 0x{st.stub:X}, {st.length} bytes)", s.va))
            # ---- relocated functions
            for a, b, r in relocs.overlapping(lo, hi):
                if not allowed("reloc", a):
                    nm = f" {r.name}" if r.name else ""
                    errors.append(Finding("SITE_IN_RELOCATED_FUNCTION", w,
                                          f"[0x{lo:X}, 0x{hi:X}) lies in the .text slot 0x{a:X}..0x{b - 1:X} of the function"
                                          f"{nm} relocated into .HOODLUM (body 0x{r.body:X}); the bytes here are a dead "
                                          f"leftover, patch the body instead", s.va))
            # ---- trunk patches
            for a, b, t in trunk.overlapping(lo, hi):
                if not allowed("trunk", a):
                    errors.append(Finding("OVERLAP_TRUNK", w,
                                          f"[0x{lo:X}, 0x{hi:X}) overlaps the trunk patch {t.symbol or t.kind} at 0x{a:X} "
                                          f"(len {t.len or '?'}, {t.src}); name it in allow_overlap = [\"trunk:0x{a:X}\"] "
                                          f"if intended", s.va))
    # ---- sites against each other
    wi = _Intervals([(s.va, s.end, (s, gid)) for s, gid in written])
    seen_pairs: set[frozenset] = set()
    for s, gid in written:
        for a, b, (o, ogid) in wi.overlapping(s.va, s.end):
            if o is s:
                continue
            pair = frozenset((s.key, o.key))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            if (("site", o.key) in parse_allow_safe(s.allow_overlap)) or (("site", s.key) in parse_allow_safe(o.allow_overlap)):
                used_allow.setdefault(s.key, set()).add(("site", o.key))
                used_allow.setdefault(o.key, set()).add(("site", s.key))
                continue
            scope = "groups" if ogid != gid else "group"
            errors.append(Finding("OVERLAP_SITE", s.key,
                                  f"[0x{s.va:X}, 0x{s.end:X}) overlaps {o.key} [0x{o.va:X}, 0x{o.end:X}) "
                                  f"(two {scope}: every written byte belongs to one site)", s.va))
    # ---- allow_overlap entries that matched nothing
    for g in man.groups:
        for s in g.sites:
            try:
                allow = parse_allow(s.allow_overlap)
            except ValueError:
                continue
            for tok in allow:
                if tok not in used_allow.get(s.key, set()):
                    warnings.append(Finding("ALLOW_UNUSED", s.key,
                                            f"allow_overlap entry {tok[0]}:{tok[1] if isinstance(tok[1], str) else hex(tok[1])} "
                                            f"matches no overlap (stale?)", s.va))
    # ---- generated header
    if check_header:
        if header_text is None:
            errors.append(Finding("HEADER_MISSING", header_label,
                                  "generated header not found; run `satk engine sites-gen`"))
        else:
            want = generate_header(man)
            if header_text.replace("\r\n", "\n") != want:
                errors.append(Finding("HEADER_STALE", header_label, _first_diff(header_text, want)))
    return CheckResult(errors, warnings, len(man.groups), n_sites, len(written))


def parse_allow_safe(tokens):
    try:
        return set(parse_allow(tokens))
    except ValueError:
        return set()


def _first_diff(have: str, want: str) -> str:
    a = have.replace("\r\n", "\n").split("\n")
    b = want.split("\n")
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else "<end of file>"
        y = b[i] if i < len(b) else "<end of file>"
        if x != y:
            return f"differs from the manifest at line {i + 1}; run `satk engine sites-gen`"
    return "differs from the manifest; run `satk engine sites-gen`"


def read_header(path: Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
