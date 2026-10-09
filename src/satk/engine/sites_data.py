"""Research data that the site tools read: HOODLUM map, Ghidra export, symdb trunk patches.

All readers take explicit paths (tests pass fixtures); :func:`workspace_inputs` fills them from the
satk workspace (``work/re/ghidra/...``, ``work/re/symdb.sqlite``, ``[paths] game``). Read-only.
Stdlib only.
"""

from __future__ import annotations

import bisect
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import cfg, jpath
from ..re.pe import PeImage
from .securom import KeyScan, load_keys

__all__ = [
    "Stolen",
    "RelocFunc",
    "TrunkPatch",
    "FuncInfo",
    "SiteInputs",
    "ghidra_dir",
    "load_hoodlum_map",
    "load_functions",
    "load_callers",
    "reloc_extents",
    "load_trunk_patches",
    "build_inputs",
    "workspace_inputs",
    "stock_exe_path",
]


@dataclass(frozen=True, slots=True)
class Stolen:
    """A SecuROM stolen-instruction site: ``length`` bytes at ``jmp_at`` became ``jmp stub`` + nops."""

    jmp_at: int
    length: int
    stub: int
    kind: str = ""


@dataclass(frozen=True, slots=True)
class RelocFunc:
    """A function relocated into ``.HOODLUM``: its ``.text`` slot is ``[entry, end)``."""

    entry: int
    end: int
    body: int
    name: str = ""


@dataclass(frozen=True, slots=True)
class TrunkPatch:
    addr: int
    len: int
    kind: str
    symbol: str
    src: str

    @property
    def end(self) -> int:
        return self.addr + max(self.len, 1)


@dataclass(frozen=True, slots=True)
class FuncInfo:
    entry: int
    end: int
    name: str
    source: str


@dataclass
class SiteInputs:
    """Everything ``sites-check`` needs besides the manifest (fixtures build it directly)."""

    image: PeImage
    keys: KeyScan
    stolen: list[Stolen] = field(default_factory=list)
    relocs: list[RelocFunc] = field(default_factory=list)
    trunk: list[TrunkPatch] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def ghidra_dir() -> Path:
    return Path(cfg().paths.work) / "re" / "ghidra"


def stock_exe_path() -> Path:
    return Path(cfg().paths.game) / "gta_sa.exe"


def _hex(v) -> int:
    return int(v, 16) if isinstance(v, str) else int(v)


def _read_json(path: Path, what: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        raise SatkError("NOT_READY", f"{what} not found: {jpath(path)}",
                        hint="run the Ghidra export (work/re/ghidra/README.md)") from None
    except json.JSONDecodeError as e:
        raise SatkError("NOT_READY", f"{what} is not valid JSON: {e}", hint=jpath(path)) from None


def load_hoodlum_map(path: Path) -> tuple[list[tuple[int, int]], list[Stolen]]:
    """``(reloc [(entry, body)], stolen sites)`` from ``hoodlum_map.json``."""
    d = _read_json(path, "HOODLUM map")
    relocs = sorted((_hex(r["entry"]), _hex(r["body"])) for r in d.get("reloc", []))
    stolen = []
    for s in d.get("sites", []):
        jmp = _hex(s.get("jmp_at", s.get("site")))
        stolen.append(Stolen(jmp, 5 + int(s.get("nop_pad") or 0), _hex(s["stub"]), str(s.get("kind", ""))))
    stolen.sort(key=lambda x: x.jmp_at)
    return relocs, stolen


def load_functions(path: Path) -> dict[int, FuncInfo]:
    """``{entry: FuncInfo}`` from ``functions.jsonl`` (one JSON object per line)."""
    out: dict[int, FuncInfo] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                e = _hex(r["entry"])
                out[e] = FuncInfo(e, _hex(r["end"]), str(r.get("name") or ""), str(r.get("source") or ""))
    except OSError:
        raise SatkError("NOT_READY", f"Ghidra functions export not found: {jpath(path)}",
                        hint="run the Ghidra export (work/re/ghidra/README.md)") from None
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        raise SatkError("NOT_READY", f"functions.jsonl is malformed: {e}", hint=jpath(path)) from None
    return out


def load_callers(path: Path) -> dict[int, set[str]]:
    """``{callee entry: {edge kinds}}`` from ``calls.jsonl`` (edges with a known caller only)."""
    out: dict[int, set[str]] = defaultdict(set)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r.get("caller") is None or r.get("callee") is None:
                    continue
                out[_hex(r["callee"])].add(str(r.get("kind", "call")))
    except OSError:
        raise SatkError("NOT_READY", f"Ghidra calls export not found: {jpath(path)}",
                        hint="run the Ghidra export (work/re/ghidra/README.md)") from None
    return out


def reloc_extents(relocs: list[tuple[int, int]], funcs: dict[int, FuncInfo],
                  callers: dict[int, set[str]] | None = None, text: tuple[int, int] = (0x401000, 0x857000)) -> list[RelocFunc]:
    """``.text`` slot of every relocated function.

    The entry is a 5-byte ``jmp`` into ``.HOODLUM``; the bytes behind it up to the next *real* function
    start are a dead leftover (SecuROM keeps stolen-instruction stubs there). A real start is a reloc
    entry, a function named from gta-reversed/plugin-sdk, or a function with a ``call`` edge pointing at
    it (Ghidra also makes pseudo functions out of stubs and junk; those are skipped).
    """
    ents = {e for e, _ in relocs}
    callers = callers or {}
    starts = sorted(e for e, fi in funcs.items()
                    if text[0] <= e < text[1] and (e in ents or fi.source not in ("", "ghidra")
                                                    or "call" in callers.get(e, ())))
    out = []
    for entry, body in relocs:
        k = bisect.bisect_right(starts, entry)
        end = starts[k] if k < len(starts) else text[1]
        end = max(end, entry + 5)
        fi = funcs.get(entry)
        out.append(RelocFunc(entry, end, body, fi.name if fi else ""))
    return out


def load_trunk_patches(db, origin: str = "trunk") -> list[TrunkPatch]:
    """Rows of the symdb ``patch`` table with the given origin, sorted by address."""
    rows = db.con.execute(
        "SELECT addr, len, kind, coalesce(symbol, ''), src_file || ':' || src_line FROM patch WHERE origin = ? "
        "ORDER BY addr, kind", (origin,))
    return [TrunkPatch(int(a), int(ln) if ln is not None else 0, str(k), str(sym), str(src))
            for a, ln, k, sym, src in rows]


def build_inputs(image: PeImage, ghidra: Path, symdb: Path | None = None, keys_cache: Path | None = None) -> SiteInputs:
    """Inputs from explicit locations: Ghidra export folder, symbol DB file, SecuROM key cache."""
    from ..re.db import open_db

    relocs_raw, stolen = load_hoodlum_map(ghidra / "symbols" / "hoodlum_map.json")
    funcs = load_functions(ghidra / "export" / "functions.jsonl")
    callers = load_callers(ghidra / "export" / "calls.jsonl")
    keys = load_keys(image, keys_cache)
    db = open_db(symdb, required=True)
    return SiteInputs(image, keys, stolen, reloc_extents(relocs_raw, funcs, callers), load_trunk_patches(db))


def workspace_inputs(exe: Path | None = None) -> SiteInputs:
    """Inputs from the satk workspace: stock exe, SecuROM keys, HOODLUM map, function ranges, symdb."""
    exe_path = Path(exe) if exe else stock_exe_path()
    if not exe_path.is_file():
        raise SatkError("NOT_READY", f"stock exe not found: {jpath(exe_path)}",
                        hint="satk init --game <folder> --clean-copy")
    return build_inputs(PeImage.open(exe_path), ghidra_dir())
