"""Move a mod's model ids to another range, in a copy (``satk id remap``).

The plan (:func:`plan`) maps every id the mod defines (IDE lines and IDE-like readme lines) to the next free
id of the target ranges, old ids in ascending order. Ids where the mod redefines a base-game model under the
same name are replacements and stay (unless ``only`` names them). ``explicit`` pairs ``old=new`` win.

The rewrite (:func:`rewrite`) copies the mod folder and changes only the id tokens, keeping every other
byte (spacing, comments, line ends):

* ``*.ide`` - the first field of ``objs tobj anim cars peds weap hier`` and ``2dfx`` lines;
* text ``*.ipl`` - the model of ``inst`` lines and of ``cars`` (car generator) lines;
* binary ``*.ipl`` (``bnry``) - the model fields of the inst and car records, patched in place;
* ``*.txt`` - readme lines whose first field is an old id and second field that model's name.

IMG archives inside the mod are copied unchanged (``NOT_REWRITTEN``), and scripts (CLEO ``.cs``, ``.cm``,
``.lua``, ``.pwn``) may hold ids satk cannot see (``SCRIPTS``).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import atomic_write, open_ro
from ..formats.ide import MODEL_SECTIONS, iter_sections
from .ranges import iter_range
from .scan import Def, mod_files

__all__ = ["Plan", "plan", "rewrite", "rewrite_bytes", "parse_pairs", "SCRIPT_EXT"]

SCRIPT_EXT = (".cs", ".cm", ".cleo", ".lua", ".pwn", ".inc", ".scm", ".s")
_TOKEN = re.compile(r"[^,\s]+")


@dataclass
class Plan:
    mapping: dict[int, int] = field(default_factory=dict)   # old -> new
    names: dict[int, str] = field(default_factory=dict)     # old id -> model name in the mod
    kept: dict[int, str] = field(default_factory=dict)      # old id -> why it stays
    problems: list[str] = field(default_factory=list)


def parse_pairs(spec: str | None) -> dict[int, int]:
    """``"15000=16000, 15001=16001"`` -> ``{15000: 16000, 15001: 16001}``."""
    out: dict[int, int] = {}
    for part in [p for p in re.split(r"[,;\s]+", str(spec or "")) if p]:
        m = re.fullmatch(r"(-?\d+)[=:](-?\d+)", part)
        if not m:
            raise SatkError("BAD_PARAMS", f"bad id pair {part!r}", hint="--map 15000=16000,15001=16001")
        out[int(m.group(1))] = int(m.group(2))
    return out


def plan(mod_defs: list[Def], taken: dict[int, str], ranges: list[tuple[int, int]], *,
         base: dict[int, set[str]], explicit: dict[int, int] | None = None, only: set[int] | None = None) -> Plan:
    """Old -> new ids for the mod (see the module docstring); ``taken`` = ids not to hand out."""
    p = Plan()
    for d in mod_defs:
        p.names.setdefault(d.id, d.name)
    explicit = dict(explicit or {})
    unknown = sorted(set(explicit) - set(p.names))
    if unknown:
        p.problems.append(f"--map names ids the mod does not define: {', '.join(map(str, unknown[:10]))}")
    move: list[int] = []
    why_not = "not in --ids"
    if not ranges and only is None:  # only explicit pairs, nothing to allocate
        only, why_not = set(), "not in --map"
    for old in sorted(p.names):
        if old in explicit:
            continue
        if only is not None and old not in only:
            p.kept[old] = why_not
            continue
        if only is None and p.names[old].lower() in base.get(old, set()):
            p.kept[old] = "replaces the base-game model of the same name"
            continue
        move.append(old)
    used = set(taken) | set(p.names) | set(explicit.values())
    for old, new in sorted(explicit.items()):
        if old not in p.names:
            continue
        if new in taken and new not in p.names:
            p.problems.append(f"--map {old}={new}: {new} is taken ({taken[new]})")
        p.mapping[old] = new
    free = [i for a, b in ranges for i in iter_range(a, b) if i not in used]
    if len(free) < len(move):
        raise SatkError("BAD_PARAMS", f"the target range has {len(free)} free ids, the mod needs {len(move)}",
                        hint=f"give a wider --to range (satk id free --kind object --count {len(move)} --contiguous)")
    for old, new in zip(move, free):
        p.mapping[old] = new
    return p


def _replace_token(line: str, k: int, new: str) -> str:
    end = len(line.rstrip("\r\n"))
    h = line.find("#")
    stop = end if h < 0 else min(h, end)
    toks = list(_TOKEN.finditer(line[:stop]))
    a, b = toks[k].span()
    return line[:a] + new + line[b:]


def _int(tok: str) -> int | None:
    try:
        return int(tok)
    except ValueError:
        return None


def _rewrite_text(text: str, ext: str, mapping: dict[int, int], names: dict[int, str]) -> tuple[str, int]:
    lines = text.splitlines(keepends=True)
    hits: list[tuple[int, int, int]] = []  # (line index, token index, new id)
    if ext in (".ide", ".ipl"):
        for sec, f, n in iter_sections(text):
            k = None
            if ext == ".ide" and (sec in MODEL_SECTIONS or sec == "2dfx"):
                k = 0
            elif ext == ".ipl" and sec == "inst":
                k = 0
            elif ext == ".ipl" and sec == "cars" and len(f) >= 5:
                k = 4
            if k is None or k >= len(f):
                continue
            v = _int(f[k])
            if v is not None and v in mapping:
                hits.append((n - 1, k, mapping[v]))
    else:  # readme
        for n, raw in enumerate(lines):
            f = raw.split("#", 1)[0].replace(",", " ").split()
            if len(f) >= 2:
                v = _int(f[0])
                if v is not None and v in mapping and f[1].lower() == names.get(v, "").lower():
                    hits.append((n, 0, mapping[v]))
    for n, k, new in hits:
        lines[n] = _replace_token(lines[n], k, str(new))
    return "".join(lines), len(hits)


def _rewrite_bnry(data: bytes, mapping: dict[int, int]) -> tuple[bytes, int]:
    from ..mapconv.bnry import parse_bnry

    info = parse_bnry(data)
    inst_off = struct.unpack_from("<I", data, 28)[0]
    cars_off = struct.unpack_from("<I", data, 60)[0]
    buf = bytearray(data)
    n = 0
    for i, r in enumerate(info.insts):
        if r[7] in mapping:
            struct.pack_into("<i", buf, inst_off + 40 * i + 28, mapping[r[7]])
            n += 1
    for i, r in enumerate(info.cars):
        if r[4] in mapping:
            struct.pack_into("<i", buf, cars_off + 48 * i + 16, mapping[r[4]])
            n += 1
    return bytes(buf), n


def rewrite_bytes(data: bytes, ext: str, mapping: dict[int, int], names: dict[int, str]) -> tuple[bytes, int, str]:
    """``(new bytes, ids changed, kind)`` of one file; kind ``ide|ipl|bnry|readme|copy``."""
    ext = ext.lower()
    if ext == ".ipl" and data[:4] == b"bnry":
        try:
            out, n = _rewrite_bnry(data, mapping)
        except ValueError:
            return data, 0, "copy"
        return out, n, "bnry"
    if ext in (".ide", ".ipl", ".txt"):
        text, n = _rewrite_text(data.decode("latin-1"), ext, mapping, names)
        return text.encode("latin-1"), n, {".ide": "ide", ".ipl": "ipl", ".txt": "readme"}[ext]
    return data, 0, "copy"


def rewrite(src: Path, dst: Path, p: Plan, *, changed_only: bool = False, dry_run: bool = False
            ) -> tuple[list[list], list[str]]:
    """Copy ``src`` (a folder or one file) to ``dst`` with the ids remapped: rows ``[file, kind, ids]``."""
    rows: list[list] = []
    warn: list[str] = []
    base = src.parent if src.is_file() else src
    imgs: list[str] = []
    scripts: list[str] = []
    for f in mod_files(src):
        rel = f.relative_to(base).as_posix()
        ext = f.suffix.lower()
        with open_ro(f) as fh:
            data = fh.read()
        new, n, kind = rewrite_bytes(data, ext, p.mapping, p.names)
        if ext == ".img":
            imgs.append(rel)
        elif ext in SCRIPT_EXT:
            scripts.append(rel)
        if n:
            rows.append([rel, kind, n])
        if not dry_run and (n or not changed_only):
            atomic_write(dst / Path(*rel.split("/")), new)
    if imgs:
        warn.append(f"NOT_REWRITTEN: IMG archives are copied unchanged; ids inside them (binary IPLs) stay: "
                    f"{', '.join(imgs[:5])}")
    if scripts:
        warn.append(f"SCRIPTS: scripts may use model ids satk cannot rewrite: {', '.join(scripts[:5])}")
    return rows, warn
