"""The ``modloader/`` folder: which mods load, with which priority, in which order.

A port of Mod Loader 0.3's folder and profile rules (``src/core/{folder,profiles,modinfo}.cpp``, MIT):

* every sub-folder of ``modloader/`` is a mod; names starting with ``.`` are skipped (``.data``,
  ``.profiles``); a mod's name is its folder name in lower case;
* ``modloader.ini`` selects the profile (``[Folder.Config] Profile``, default ``Default``) whose
  sections ``[Profiles.<name>.Priority|IgnoreMods|IgnoreFiles|IncludeMods|ExclusiveMods|Config]`` apply,
  with ``Parents`` inheritance; ``.profiles/*.ini`` add profiles;
* priority 1..100 (default 50, ``PriorityLimit`` caps it), 0 = ignored; ``IgnoreAllMods`` ignores all;
  ``ExcludeAllMods`` keeps only ``IncludeMods``; ``ExclusiveMods`` of another profile are ignored;
* install order: priority ascending, then name by **length** and then bytes (``modloader::compare``).
  For files that replace each other the last installed wins - the highest priority, and on a tie
  the longest name (then the alphabetically last one). Mergeable data files enter the merge in
  this order (the earliest wins dominance ties).

Stdlib only.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..core.paths import jpath, open_ro

__all__ = ["ModEntry", "Folder", "read_folder", "mods_from_paths", "install_order_key", "DEFAULT_PRIORITY"]

DEFAULT_PRIORITY = 50
_DEFAULT_LIMIT = 100


def install_order_key(priority: int, name: str) -> tuple:
    """``PriorityPred``: priority, then ``modloader::compare`` (shorter name first, then byte order)."""
    n = name.lower().replace("/", "\\")
    return (priority, len(n), n)


@dataclass
class ModEntry:
    """A mod as Mod Loader sees it."""

    name: str              # folder (or archive) name as on disk
    path: Path
    priority: int = DEFAULT_PRIORITY
    ignored: str = ""      # why it is not loaded ("" = loaded)

    @property
    def lname(self) -> str:
        return self.name.lower()


@dataclass
class Folder:
    """``modloader/`` of a game root."""

    path: Path
    profile: str = "Default"
    mods: list[ModEntry] = field(default_factory=list)
    ignore_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def loaded(self) -> list[ModEntry]:
        """Loaded mods in install order."""
        return sorted((m for m in self.mods if not m.ignored), key=lambda m: install_order_key(m.priority, m.name))

    def file_ignored(self, rel: str) -> bool:
        """``Profile::IsFilePathIgnored``: the file name or its path inside the mod matches IgnoreFiles."""
        lrel = rel.lower().replace("\\", "/")
        name = lrel.rsplit("/", 1)[-1]
        return any(_wild(name, w) or _wild(lrel, w) for w in self.ignore_files)


def _wild(text: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(text, pattern.lower().replace("\\", "/"))


# --------------------------------------------------------------------------- ini


def _read_ini(path: Path) -> dict[str, list[tuple[str, str | None]]]:
    """Sections (lower-case names) -> ``(key, value or None for bare lines)`` in order; ``;``/``#`` comments."""
    out: dict[str, list[tuple[str, str | None]]] = {}
    try:
        with open_ro(path) as f:
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return out
    sec: list | None = None
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            sec = out.setdefault(line[1:-1].strip().lower(), [])
            continue
        if sec is None:
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            sec.append((k.strip(), v.strip()))
        else:
            sec.append((line, None))
    return out


@dataclass
class _Profile:
    name: str
    data: dict[str, list[tuple[str, str | None]]]

    def items(self, what: str) -> list[tuple[str, str | None]]:
        return self.data.get(f"profiles.{self.name.lower()}.{what.lower()}", [])

    def config(self, key: str) -> str | None:
        for k, v in self.items("config"):
            if k.lower() == key.lower():
                return v
        return None


def _profiles(sections: dict) -> dict[str, _Profile]:
    out: dict[str, _Profile] = {}
    for s in sections:
        parts = s.split(".")
        if len(parts) >= 3 and parts[0] == "profiles":
            name = ".".join(parts[1:-1])
            out.setdefault(name, _Profile(name, sections))
    return out


def read_folder(root: Path, profile: str | None = None) -> Folder:
    """Read ``<root>/modloader``: mods, the active profile's priorities and ignore lists.

    Args:
        root: game root.
        profile: profile name to use instead of ``[Folder.Config] Profile``.
    """
    ml = Path(root) / "modloader"
    if not ml.is_dir():
        ml = next((p for p in Path(root).iterdir() if p.is_dir() and p.name.lower() == "modloader"), ml) \
            if Path(root).is_dir() else ml
    folder = Folder(ml)
    if not ml.is_dir():
        folder.warnings.append(f"NO_MODLOADER: {jpath(ml)} does not exist (no Mod Loader mods)")
        return folder
    sections = _read_ini(ml / "modloader.ini")
    profs = _profiles(sections)
    pdir = ml / ".profiles"
    if pdir.is_dir():
        for f in sorted(pdir.glob("*.ini")):
            for name, p in _profiles(_read_ini(f)).items():
                profs.setdefault(name, p)
    limit = _DEFAULT_LIMIT
    for k, v in sections.get("folder.config", []):
        if k.lower() == "prioritylimit" and v:
            try:
                limit = int(v, 0)
            except ValueError:
                pass
    wanted = profile
    if wanted is None:
        wanted = next((v for k, v in sections.get("folder.config", []) if k.lower() == "profile" and v), "Default")
    folder.profile = wanted
    active = profs.get(wanted.lower())
    if active is None and profs:
        folder.warnings.append(f"PROFILE: profile {wanted!r} not found in modloader.ini; using defaults")
    chain = _chain(active, profs) if active is not None else []

    def items(what: str) -> list[tuple[str, str | None]]:
        out: list[tuple[str, str | None]] = []
        for p in chain:
            out += p.items(what)
        return out

    def flag(key: str) -> bool:
        for p in chain:
            v = p.config(key)
            if v is not None:
                return _bool(v)
        return False

    prio: dict[str, int] = {}
    for p in reversed(chain):                       # the active profile overrides its parents
        for k, v in p.items("priority"):
            if v is None:
                continue
            try:
                prio[k.lower()] = max(0, min(int(v, 0), limit))
            except ValueError:
                folder.warnings.append(f"PRIORITY: bad value {k}={v} in modloader.ini")
    ignore_mods = [k.lower() for k, v in items("ignoremods") if v is None]
    include = [k.lower() for k, v in items("includemods") if v is None]
    exclusive_mine = [k.lower() for k, v in items("exclusivemods") if v is None]
    exclusive_other = [k.lower() for name, p in profs.items() if p not in chain
                       for k, v in p.items("exclusivemods") if v is None]
    folder.ignore_files = [k.lower() for k, v in items("ignorefiles") if v is None]
    ignore_all = flag("IgnoreAllMods") or flag("IgnoreAllFiles")
    exclude_all = flag("ExcludeAllMods")
    for d in sorted(ml.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        n = d.name.lower()
        m = ModEntry(d.name, d, prio.get(n, DEFAULT_PRIORITY))
        if ignore_all:
            m.ignored = "IgnoreAllMods"
        elif any(fnmatch.fnmatchcase(n, w) for w in exclusive_other) \
                and not any(fnmatch.fnmatchcase(n, w) for w in exclusive_mine):
            m.ignored = "exclusive to another profile"
        elif exclude_all:
            if not any(fnmatch.fnmatchcase(n, w) for w in include):
                m.ignored = "ExcludeAllMods (not in IncludeMods)"
        elif any(fnmatch.fnmatchcase(n, w) for w in ignore_mods):
            m.ignored = "IgnoreMods"
        elif m.priority == 0:
            m.ignored = "priority 0"
        folder.mods.append(m)
    return folder


def _bool(v: str) -> bool:
    s = v.strip()
    if len(s) == 1:
        return s != "0"
    return s.lower() != "false"


def _chain(active: _Profile, profs: dict[str, _Profile]) -> list[_Profile]:
    """Active profile first, then its ``Parents`` (``$None`` stops inheritance), depth-first, no cycles."""
    out: list[_Profile] = []
    todo = [active]
    while todo:
        p = todo.pop(0)
        if p in out:
            continue
        out.append(p)
        parents = [x.strip() for x in (p.config("Parents") or "").split(",") if x.strip()]
        if any(x.lower() == "$none" for x in parents):
            continue
        todo += [profs[x.lower()] for x in parents if x.lower() in profs]
    return out


def mods_from_paths(paths: list[str], priorities: dict[str, int] | None = None) -> list[ModEntry]:
    """Mods given on the command line (folders or zips), default priority 50 unless ``name=N`` is given."""
    pr = {k.lower(): v for k, v in (priorities or {}).items()}
    out = []
    for raw in paths:
        p = Path(os.path.abspath(raw))
        name = p.stem if p.is_file() else p.name
        out.append(ModEntry(name, p, pr.get(name.lower(), DEFAULT_PRIORITY)))
    return out
