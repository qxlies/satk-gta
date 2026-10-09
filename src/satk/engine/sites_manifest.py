"""Patch-site manifest of the sa-engine fork: model, TOML loader and structural validation.

The manifest (``docs/sae/patch-sites.toml`` in the fork, schema 1) lists every exe byte range the
sa-engine patcher may touch, with the stock bytes that must be there first. This module parses it
into plain dataclasses and runs the checks that need no game data (schema, ids, kinds, golden
window). The checks against the stock exe and the research data live in :mod:`.sites_check`; the C++
header comes from :mod:`.sites_gen`. Stdlib only.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "SCHEMA",
    "KINDS",
    "PHASES",
    "REPORT_ID_RANGE",
    "READONLY_GROUPS",
    "Finding",
    "Site",
    "Group",
    "Manifest",
    "parse_hex_bytes",
    "load_manifest",
    "parse_manifest",
    "text_sha256",
    "validate_structure",
    "cpp_ident",
]

SCHEMA = 1
KINDS = ("imm8", "imm32", "f32", "absref", "code", "hook", "data32")
PHASES = ("ctor", "hooks", "runtime")
REPORT_ID_RANGE = (91000, 91999)
#: Groups whose sites are only read (never written): exempt from the write-overlap checks.
READONLY_GROUPS = frozenset({"id.anchors"})
_ID_RE = re.compile(r"^[a-z0-9_.]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX_TOKEN = re.compile(r"^(?:[0-9A-Fa-f]{2}|xx|XX)$")
_CPP_KEYWORDS = frozenset(
    "alignas alignof and and_eq asm auto bitand bitor bool break case catch char char8_t char16_t char32_t class "
    "compl concept const consteval constexpr constinit const_cast continue co_await co_return co_yield decltype "
    "default delete do double dynamic_cast else enum explicit export extern false float for friend goto if inline "
    "int long mutable namespace new noexcept not not_eq nullptr operator or or_eq private protected public register "
    "reinterpret_cast requires return short signed sizeof static static_assert static_cast struct switch template "
    "this thread_local throw true try typedef typeid typename union unsigned using virtual void volatile wchar_t "
    "while xor xor_eq COUNT".split())
_TOP_KEYS = {"schema", "exe_sha256", "group"}
_GROUP_KEYS = {"id", "report_id", "phase", "lane", "note", "site"}
_SITE_KEYS = {"id", "va", "len", "kind", "golden_va", "golden", "alt", "array", "accept_mask", "accept_value",
              "allow_overlap", "readonly", "note"}


@dataclass(frozen=True, slots=True)
class Finding:
    """One problem found by a check; ``check`` is the stable code (``SITE_IN_RELOCATED_FUNCTION`` ...)."""

    check: str
    where: str
    msg: str
    va: int | None = None

    def row(self) -> list:
        return [self.check, self.where, None if self.va is None else f"0x{self.va:X}", self.msg]


@dataclass(slots=True)
class Site:
    id: str
    va: int
    len: int
    kind: str
    golden_va: int = 0
    golden: bytes = b""
    alt: tuple[int | None, ...] = ()  # byte or None for ``xx``
    array: tuple[int, int, int] | None = None  # base, size, stride (absref only)
    accept_mask: int = 0
    accept_value: int = 0
    allow_overlap: tuple[str, ...] = ()
    readonly: bool = False
    note: str = ""
    group: str = ""

    @property
    def end(self) -> int:
        return self.va + self.len

    @property
    def key(self) -> str:
        return f"{self.group}/{self.id}"


@dataclass(slots=True)
class Group:
    id: str
    report_id: int
    phase: str
    lane: str = ""
    note: str = ""
    sites: list[Site] = field(default_factory=list)

    @property
    def readonly(self) -> bool:
        return self.id in READONLY_GROUPS


@dataclass(slots=True)
class Manifest:
    schema: int
    exe_sha256: str
    groups: list[Group]
    sha256: str  # of the TOML text with normalised line endings (the header comment)
    path: Path | None = None

    def sites(self):
        for g in self.groups:
            yield from g.sites


def text_sha256(raw: bytes) -> str:
    """SHA-256 of the manifest text with CRLF/CR folded to LF (a checkout with autocrlf hashes the same)."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()


def parse_hex_bytes(s: str, *, allow_any: bool = False) -> tuple[int | None, ...]:
    """``"68 8C 00"`` -> ``(0x68, 0x8C, 0x00)``; with ``allow_any`` the token ``xx`` gives ``None``."""
    out: list[int | None] = []
    for tok in str(s).split():
        if not _HEX_TOKEN.match(tok):
            raise ValueError(f"bad byte token {tok!r}")
        if tok.lower() == "xx":
            if not allow_any:
                raise ValueError("'xx' is only allowed in alt")
            out.append(None)
        else:
            out.append(int(tok, 16))
    return tuple(out)


def cpp_ident(s: str) -> str:
    """Dots to underscores (``cap.pools`` -> ``cap_pools``)."""
    return s.replace(".", "_")


def _int(v, what: str, errs: list[Finding], where: str) -> int | None:
    if isinstance(v, bool) or not isinstance(v, int):
        errs.append(Finding("SCHEMA", where, f"{what} must be an integer"))
        return None
    return v


def parse_manifest(raw: bytes, path: Path | None = None) -> tuple[Manifest | None, list[Finding]]:
    """Parse TOML bytes into a :class:`Manifest`; schema problems come back as findings."""
    errs: list[Finding] = []
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        return None, [Finding("TOML_PARSE", "manifest", str(e))]
    for k in doc:
        if k not in _TOP_KEYS:
            errs.append(Finding("SCHEMA", "manifest", f"unknown top-level key {k!r}"))
    schema = doc.get("schema")
    if schema != SCHEMA:
        errs.append(Finding("SCHEMA", "manifest", f"schema must be {SCHEMA}, got {schema!r}"))
    sha = doc.get("exe_sha256")
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        errs.append(Finding("SCHEMA", "manifest", "exe_sha256 must be 64 lowercase hex digits"))
        sha = str(sha)
    groups: list[Group] = []
    raw_groups = doc.get("group", [])
    if not isinstance(raw_groups, list) or not raw_groups:
        errs.append(Finding("SCHEMA", "manifest", "at least one [[group]] is required"))
        raw_groups = []
    for gi, rg in enumerate(raw_groups):
        gid = rg.get("id") if isinstance(rg, dict) else None
        where = f"group[{gi}]" if not isinstance(gid, str) else f"group {gid}"
        if not isinstance(rg, dict):
            errs.append(Finding("SCHEMA", where, "group must be a table"))
            continue
        for k in rg:
            if k not in _GROUP_KEYS:
                errs.append(Finding("SCHEMA", where, f"unknown group key {k!r}"))
        rid = _int(rg.get("report_id"), "report_id", errs, where) if "report_id" in rg else None
        if "report_id" not in rg:
            errs.append(Finding("SCHEMA", where, "report_id is required"))
        phase = rg.get("phase")
        if phase not in PHASES:
            errs.append(Finding("SCHEMA", where, f"phase must be one of {'|'.join(PHASES)}, got {phase!r}"))
        if not isinstance(gid, str):
            errs.append(Finding("SCHEMA", where, "group id must be a string"))
            gid = f"?{gi}"
        grp = Group(gid, rid if rid is not None else -1, str(phase), str(rg.get("lane", "")), str(rg.get("note", "")))
        sites = rg.get("site", [])
        if not isinstance(sites, list) or not sites:
            errs.append(Finding("SCHEMA", where, "a group needs at least one [[group.site]]"))
            sites = []
        for si, rs in enumerate(sites):
            sid = rs.get("id") if isinstance(rs, dict) else None
            swhere = f"{gid}/{sid}" if isinstance(sid, str) else f"{gid}/site[{si}]"
            if not isinstance(rs, dict):
                errs.append(Finding("SCHEMA", swhere, "site must be a table"))
                continue
            for k in rs:
                if k not in _SITE_KEYS:
                    errs.append(Finding("SCHEMA", swhere, f"unknown site key {k!r}"))
            if not isinstance(sid, str):
                errs.append(Finding("SCHEMA", swhere, "site id must be a string"))
                sid = f"?{si}"
            kind = rs.get("kind")
            if kind not in KINDS:
                errs.append(Finding("SCHEMA", swhere, f"kind must be one of {'|'.join(KINDS)}, got {kind!r}"))
            va = _int(rs.get("va"), "va", errs, swhere) if "va" in rs else None
            ln = _int(rs.get("len"), "len", errs, swhere) if "len" in rs else None
            for req in ("va", "len", "kind"):
                if req not in rs:
                    errs.append(Finding("SCHEMA", swhere, f"{req} is required"))
            site = Site(sid, va if va is not None else 0, ln if ln is not None else 0, str(kind), group=gid)
            if "golden_va" in rs:
                gv = _int(rs["golden_va"], "golden_va", errs, swhere)
                site.golden_va = gv if gv is not None else 0
            if "golden" in rs:
                try:
                    g = parse_hex_bytes(rs["golden"])
                    site.golden = bytes(g)  # type: ignore[arg-type]
                except (ValueError, TypeError) as e:
                    errs.append(Finding("SCHEMA", swhere, f"golden: {e}"))
            if "alt" in rs:
                try:
                    site.alt = parse_hex_bytes(rs["alt"], allow_any=True)
                except (ValueError, TypeError) as e:
                    errs.append(Finding("SCHEMA", swhere, f"alt: {e}"))
            if "array" in rs:
                a = rs["array"]
                if (isinstance(a, dict) and set(a) == {"base", "size", "stride"}
                        and all(isinstance(a[k], int) and not isinstance(a[k], bool) for k in a)):
                    site.array = (a["base"], a["size"], a["stride"])
                else:
                    errs.append(Finding("SCHEMA", swhere, "array must be { base = N, size = N, stride = N }"))
            for k in ("accept_mask", "accept_value"):
                if k in rs:
                    v = _int(rs[k], k, errs, swhere)
                    if v is not None:
                        setattr(site, k, v)
            if "allow_overlap" in rs:
                ao = rs["allow_overlap"]
                if isinstance(ao, list) and all(isinstance(x, str) for x in ao):
                    site.allow_overlap = tuple(ao)
                else:
                    errs.append(Finding("SCHEMA", swhere, "allow_overlap must be a list of strings"))
            if "readonly" in rs:
                if isinstance(rs["readonly"], bool):
                    site.readonly = rs["readonly"]
                else:
                    errs.append(Finding("SCHEMA", swhere, "readonly must be true or false"))
            site.note = str(rs.get("note", ""))
            grp.sites.append(site)
        groups.append(grp)
    man = Manifest(schema if isinstance(schema, int) else -1, str(sha), groups, text_sha256(raw), path)
    return man, errs


def load_manifest(path: Path) -> tuple[Manifest | None, list[Finding]]:
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return None, [Finding("TOML_PARSE", "manifest", f"cannot read {Path(path).as_posix()}: {e}")]
    return parse_manifest(raw, Path(path))


def site_is_readonly(g: Group, s: Site) -> bool:
    """Sites of read-only groups (and sites marked ``readonly = true``) are never written."""
    return g.readonly or s.readonly


def validate_structure(man: Manifest) -> list[Finding]:
    """Checks that need no game data: ids, report ids, kinds, golden window, array, alt, accept."""
    f: list[Finding] = []
    seen_g: dict[str, int] = {}
    seen_r: dict[int, str] = {}
    seen_cpp_g: dict[str, str] = {}
    for g in man.groups:
        if g.id in seen_g:
            f.append(Finding("DUP_GROUP_ID", g.id, "group id is not unique"))
        seen_g[g.id] = 1
        if not _ID_RE.match(g.id):
            f.append(Finding("BAD_ID", g.id, "group id must match [a-z0-9_.]+"))
        else:
            ci = cpp_ident(g.id)
            if ci[0].isdigit() or ci in _CPP_KEYWORDS:
                f.append(Finding("BAD_ID", g.id, f"group id gives the invalid C++ identifier {ci!r}"))
            elif ci in seen_cpp_g and seen_cpp_g[ci] != g.id:
                f.append(Finding("DUP_GROUP_ID", g.id, f"collides with {seen_cpp_g[ci]!r} as C++ identifier {ci!r}"))
            seen_cpp_g[ci] = g.id
        if g.report_id in seen_r:
            f.append(Finding("DUP_REPORT_ID", g.id, f"report_id {g.report_id} is also used by {seen_r[g.report_id]}"))
        seen_r[g.report_id] = g.id
        lo, hi = REPORT_ID_RANGE
        if not lo <= g.report_id <= hi:
            f.append(Finding("REPORT_ID_RANGE", g.id, f"report_id {g.report_id} outside {lo}..{hi}"))
        seen_s: dict[str, int] = {}
        seen_cpp_s: dict[str, str] = {}
        for s in g.sites:
            w = s.key
            if s.id in seen_s:
                f.append(Finding("DUP_SITE_ID", w, "site id is not unique within its group", s.va))
            seen_s[s.id] = 1
            if not _ID_RE.match(s.id):
                f.append(Finding("BAD_ID", w, "site id must match [a-z0-9_.]+", s.va))
            else:
                ci = cpp_ident(s.id)
                if ci[0].isdigit() or ci in _CPP_KEYWORDS:
                    f.append(Finding("BAD_ID", w, f"site id gives the invalid C++ identifier {ci!r}", s.va))
                elif ci in seen_cpp_s and seen_cpp_s[ci] != s.id:
                    f.append(Finding("DUP_SITE_ID", w, f"collides with {seen_cpp_s[ci]!r} as C++ identifier {ci!r}", s.va))
                seen_cpp_s[ci] = s.id
            f.extend(_validate_site(s))
    return f


def _validate_site(s: Site) -> list[Finding]:
    f: list[Finding] = []
    w = s.key
    k = s.kind
    if k not in KINDS:
        return f
    if not 1 <= s.len <= 16:
        f.append(Finding("KIND_LEN", w, f"len {s.len} outside 1..16", s.va))
    if k == "imm8" and s.len != 1:
        f.append(Finding("KIND_LEN", w, f"imm8 needs len 1, got {s.len}", s.va))
    if k in ("imm32", "f32", "absref", "data32") and s.len != 4:
        f.append(Finding("KIND_LEN", w, f"{k} needs len 4, got {s.len}", s.va))
    if k == "hook" and s.len < 5:
        f.append(Finding("KIND_LEN", w, f"hook needs len >= 5 (jmp rel32), got {s.len}", s.va))
    gl = len(s.golden)
    if gl > 24:
        f.append(Finding("GOLDEN_WINDOW", w, f"golden has {gl} bytes, the limit is 24", s.va))
    if k == "data32":
        if gl == 0 and not s.accept_mask:
            f.append(Finding("ACCEPT_BAD", w, "data32 needs a golden window or accept_mask/accept_value", s.va))
    elif gl == 0:
        f.append(Finding("GOLDEN_WINDOW", w, "golden bytes are required for this kind", s.va))
    if gl:
        if s.golden_va == 0 or s.golden_va > s.va or s.va + s.len > s.golden_va + gl:
            f.append(Finding("GOLDEN_WINDOW", w,
                             f"golden window [0x{s.golden_va:X}, +{gl}) does not cover the written range "
                             f"[0x{s.va:X}, +{s.len})", s.va))
    if s.alt:
        if not gl:
            f.append(Finding("ALT_BAD", w, "alt needs golden", s.va))
        elif len(s.alt) != gl:
            f.append(Finding("ALT_BAD", w, f"alt has {len(s.alt)} tokens, golden has {gl}", s.va))
    if k == "absref" and s.array is not None:
        base, size, stride = s.array
        if size <= 0 or stride <= 0 or base <= 0:
            f.append(Finding("ARRAY_BAD", w, "array base, size and stride must be positive", s.va))
    if k != "absref" and s.array is not None:
        f.append(Finding("ARRAY_BAD", w, "array is only valid for kind absref", s.va))
    if k == "data32":
        if s.accept_value & ~s.accept_mask & 0xFFFFFFFF:
            f.append(Finding("ACCEPT_BAD", w, "accept_value has bits outside accept_mask", s.va))
        if not 0 <= s.accept_mask <= 0xFFFFFFFF or not 0 <= s.accept_value <= 0xFFFFFFFF:
            f.append(Finding("ACCEPT_BAD", w, "accept_mask/accept_value must fit 32 bits", s.va))
    elif s.accept_mask or s.accept_value:
        f.append(Finding("ACCEPT_BAD", w, "accept_mask/accept_value are only valid for kind data32", s.va))
    if not 0 < s.va < 1 << 32:
        f.append(Finding("SCHEMA", w, "va outside the 32-bit address space", s.va))
    return f
