"""Who defines which model id: profile indexes and mod folders (read-only).

A :class:`Def` is one definition of a model id: an IDE line (``objs tobj anim cars peds weap hier``) or a
modloader readme line (a ``.txt`` line whose fields look like a ``vehicles.ide`` / ``peds.ide`` line,
which modloader loads like an IDE line).

* :func:`profile_defs` - everything a built profile index knows: the IDE lines the game loads (``model``
  table), IDE files that are present but not loaded by the DAT files (modloader folders, ``SAMP/samp.ide``
  in a single-player profile, stray copies) and readme lines inside modloader folders;
* :func:`scan_mod` - the same for a mod folder (or one file) on disk, e.g. a download not installed yet.

``owner`` groups definitions for conflict checks: the index layer (``vanilla``, ``samp``, ``modded``,
``modloader:<mod>``) or ``mod:<folder>`` for :func:`scan_mod`; ``loaded`` is ``False`` for files nothing
loads (they still count as taken ids, but not as conflicts).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import jpath, open_ro
from ..formats.ide import parse_ide

__all__ = ["Def", "VEHICLE_TYPES", "readme_defs", "ide_defs", "profile_defs", "scan_mod", "scan_mods", "mod_files", "rows",
           "MAX_TEXT"]

#: ``vehicles.ide`` types (4th field) that make a readme line a vehicle definition.
VEHICLE_TYPES = frozenset({"car", "mtruck", "quad", "heli", "plane", "boat", "train", "f_heli", "f_plane", "bike",
                           "bmx", "trailer"})
MAX_TEXT = 8 * 1024 * 1024
_PAGE = 500
_PEDTYPES = frozenset({"player1", "player2", "player_network", "player_unused", "civmale", "civfemale", "cop",
                       "gang1", "gang2", "gang3", "gang4", "gang5", "gang6", "gang7", "gang8", "gang9", "gang10",
                       "dealer", "medic", "fireman", "criminal", "bum", "prostitute", "special", "mission1",
                       "mission2", "mission3", "mission4", "mission5", "mission6", "mission7", "mission8"})


@dataclass(frozen=True)
class Def:
    """One definition of a model id (see the module docstring)."""

    id: int
    name: str
    sec: str
    owner: str
    where: str          # "<relpath>:<line>" (relpath inside the game root or the mod folder)
    loaded: bool = True
    via: str = "ide"    # ide | readme


def rows(db, sql: str, params: list | tuple = ()) -> list[list]:
    """All rows of a read-only query, paged through ``IndexDB.query``."""
    out: list[list] = []
    off = 0
    while True:
        env = db.query(f"{sql} LIMIT {_PAGE} OFFSET {off}", list(params), limit=_PAGE)
        out.extend(env["rows"])
        if len(env["rows"]) < _PAGE:
            return out
        off += _PAGE


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_TEXT:
            return None
        with open_ro(path) as f:
            return f.read().decode("latin-1")
    except OSError:
        return None


def ide_defs(text: str, owner: str, rel: str, loaded: bool = True) -> list[Def]:
    """Model definitions of an IDE text."""
    defs, _txdp, _fx = parse_ide(text)
    return [Def(d.id, d.name, d.sec, owner, f"{rel}:{d.line}", loaded) for d in defs]


def readme_defs(text: str, owner: str, rel: str, loaded: bool = True) -> list[Def]:
    """IDE-like lines of a readme text (modloader loads ``vehicles.ide`` and ``peds.ide`` lines from readmes)."""
    out: list[Def] = []
    for n, raw in enumerate(text.splitlines(), 1):
        f = raw.split("#", 1)[0].replace(",", " ").split()
        if len(f) < 8 or not re.fullmatch(r"\d+", f[0]) or not re.fullmatch(r"[A-Za-z0-9_\-]+", f[1]):
            continue
        if len(f) >= 11 and f[3].lower() in VEHICLE_TYPES:
            out.append(Def(int(f[0]), f[1], "cars", owner, f"{rel}:{n}", loaded, "readme"))
        elif f[3].lower() in _PEDTYPES:
            out.append(Def(int(f[0]), f[1], "peds", owner, f"{rel}:{n}", loaded, "readme"))
    return out


def profile_defs(db) -> list[Def]:
    """Definitions known to a profile index (loaded lines, unloaded IDE files, modloader readme lines)."""
    out = [Def(int(r[0]), str(r[1]), str(r[2]), str(r[3]), f"{r[4]}:{r[5]}", True) for r in rows(
        db, "SELECT m.id, m.name, m.sec, l.name, s.relpath, m.line FROM model m JOIN layer l ON l.id = m.layer_id "
            "JOIN ide d ON d.id = m.ide_id JOIN source s ON s.id = d.source_id ORDER BY m.rid")]
    files = rows(db, "SELECT s.relpath, l.name, s.kind FROM source s JOIN layer l ON l.id = s.layer_id "
                     "WHERE (s.kind = 'ide' AND s.load_ref IS NULL) OR (l.kind = 'modloader' AND "
                     "lower(s.relpath) LIKE '%.txt') ORDER BY s.relpath")
    root = Path(db.root)
    for rel, layer, kind in files:
        text = _read(root / Path(*str(rel).split("/")))
        if text is None:
            continue
        loaded = str(layer).startswith("modloader:")
        if kind == "ide":
            out += ide_defs(text, str(layer), str(rel), loaded)
        else:
            out += readme_defs(text, str(layer), str(rel), loaded)
    return out


def mod_files(path: Path) -> list[Path]:
    """Files of a mod folder (sorted, case-insensitively) or the file itself."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise SatkError("NOT_FOUND", f"no such mod folder or file: {jpath(path)}",
                        hint="give the folder of the mod (as it would go into modloader/)")
    out: list[Path] = []
    for dp, dns, fns in os.walk(path):
        dns.sort(key=str.lower)
        out += [Path(dp) / f for f in sorted(fns, key=str.lower)]
    return out


def scan_mod(path: str | os.PathLike, owner: str | None = None) -> list[Def]:
    """Definitions in a mod folder or file: ``*.ide`` files and IDE-like readme lines of ``*.txt`` files."""
    p = Path(os.path.abspath(os.fspath(path)))
    own = owner or f"mod:{p.name}"
    base = p.parent if p.is_file() else p
    label = own[4:] if own.startswith("mod:") else p.name
    out: list[Def] = []
    for f in mod_files(p):
        ext = f.suffix.lower()
        if ext not in (".ide", ".txt"):
            continue
        text = _read(f)
        if text is None:
            continue
        rel = f.relative_to(base).as_posix() if p.is_file() else f"{label}/{f.relative_to(base).as_posix()}"
        out += ide_defs(text, own, rel) if ext == ".ide" else readme_defs(text, own, rel)
    return out


def scan_mods(paths: list[str]) -> list[Def]:
    """:func:`scan_mod` of several mods; owners stay unique (``mod:name``, ``mod:name#2``)."""
    out: list[Def] = []
    seen: dict[str, int] = {}
    for m in paths:
        name = Path(os.path.abspath(os.fspath(m))).name
        k = seen.get(name.lower(), 0) + 1
        seen[name.lower()] = k
        out += scan_mod(m, f"mod:{name}" + (f"#{k}" if k > 1 else ""))
    return out

