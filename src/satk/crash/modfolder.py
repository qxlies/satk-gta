"""A modloader folder as the crash tools need it: which mods load, and which mod defines a model.

``modloader.ini`` (the folder config of Mod Loader 0.3.x; section names checked against the default
file that Mod Loader 0.3.7 ships)::

    [Folder.Config]                  Profile = Default
    [Profiles.<P>.Config]            IgnoreAllMods = false, ExcludeAllMods = false
    [Profiles.<P>.Priority]          MyMod = 50          (1..100, 0 = ignored like IgnoreMods)
    [Profiles.<P>.IgnoreFiles]       wildcards of files
    [Profiles.<P>.IgnoreMods]        wildcards of mod folders (one per line)
    [Profiles.<P>.IncludeMods]       loaded even with ExcludeAllMods = true
    [Profiles.<P>.ExclusiveMods]     loaded ONLY in profile <P>

A mod is a top-level folder of ``modloader/`` (folders starting with ``.`` belong to Mod Loader
itself). :func:`read_folder` returns the mods with their load state; :func:`scan_models` reads the
``*.ide`` files of the mods (model IDs) and the names of their ``*.dff``/``*.txd`` files. This is
the minimum the crash attribution needs, not modloader's merge semantics (that is ``satk mod``).
Everything is read with ``open_ro``; nothing is written. Stdlib only.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Mod", "Folder", "ModelDef", "ModScan", "read_folder", "find_folder", "scan_models", "parse_ini",
           "INI_NAME"]

INI_NAME = "modloader.ini"
_MAX_FILES = 200_000
_MAX_IDE = 4 << 20


@dataclass(slots=True)
class Mod:
    name: str
    path: Path
    loaded: bool
    why: str | None = None          # why it is not loaded: ignore-mods, priority-0, ignore-all, exclude-all, exclusive
    priority: int = 50
    nested: bool = False            # has its own modloader.ini (a folder of mods)


@dataclass(slots=True)
class Folder:
    path: Path
    ini: Path | None
    profile: str
    sections: dict[str, list[str]]       # lower-case section name -> raw lines (comments kept)
    names: dict[str, str]                # lower-case section name -> section name as written
    mods: list[Mod]
    ignore_mods: list[str]

    def section_name(self, kind: str) -> str:
        """``[Profiles.<P>.<kind>]`` as written in the file (or the canonical spelling)."""
        want = f"profiles.{self.profile}.{kind}".lower()
        return self.names.get(want, f"Profiles.{self.profile}.{kind}")

    @property
    def loaded(self) -> list[Mod]:
        return [m for m in self.mods if m.loaded]


def parse_ini(text: str) -> tuple[dict[str, list[str]], dict[str, str]]:
    """``({section_lower: [raw lines]}, {section_lower: name as written})``."""
    sections: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    cur: str | None = None
    for raw in text.splitlines():
        s = raw.strip()
        m = re.fullmatch(r"\[\s*([^\]]+?)\s*\]\s*(?:;.*)?", s)
        if m:
            cur = m.group(1).lower()
            names.setdefault(cur, m.group(1))
            sections.setdefault(cur, [])
            continue
        if cur is not None:
            sections[cur].append(raw)
    return sections, names


def _entries(lines: list[str]) -> list[str]:
    """Non-comment entries of a list section (``;`` starts a comment)."""
    out = []
    for raw in lines:
        s = raw.split(";", 1)[0].strip()
        if s:
            out.append(s)
    return out


def _kv(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for e in _entries(lines):
        if "=" in e:
            k, v = e.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def _match(name: str, patterns: list[str]) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, p.lower().replace("[", "[[]")) for p in patterns)


def find_folder(path: str | os.PathLike) -> Path | None:
    """The modloader folder for a path: the folder itself, ``<game>/modloader``, or ``None``."""
    p = Path(path)
    if p.is_file():
        p = p.parent
    if (p / INI_NAME).is_file() or p.name.lower() == "modloader":
        return p if p.is_dir() else None
    for cand in (p / "modloader", p / "Modloader", p / "ModLoader"):
        if cand.is_dir():
            return cand
    return None


def read_folder(mdir: str | os.PathLike) -> Folder:
    """Mods of a modloader folder with their load state (``modloader.ini`` rules)."""
    from ..core.paths import open_ro

    mdir = Path(mdir)
    ini = mdir / INI_NAME
    text = ""
    if ini.is_file():
        with open_ro(ini) as f:
            text = f.read().decode("utf-8", "replace")
    sections, names = parse_ini(text)
    profile = _kv(sections.get("folder.config", [])).get("profile") or "Default"
    profile = profile.strip().strip('"') or "Default"
    pre = f"profiles.{profile.lower()}."
    conf = _kv(sections.get(pre + "config", []))
    prio_raw = _kv(sections.get(pre + "priority", []))
    prio: dict[str, int] = {}
    for k, v in prio_raw.items():
        try:
            prio[k] = int(v.split()[0])
        except (ValueError, IndexError):
            pass
    ignore = _entries(sections.get(pre + "ignoremods", []))
    include = _entries(sections.get(pre + "includemods", []))
    exclusive_other: list[str] = []
    for sec, lines in sections.items():
        if sec.startswith("profiles.") and sec.endswith(".exclusivemods") and not sec.startswith(pre):
            exclusive_other += _entries(lines)
    ignore_all, exclude_all = _truthy(conf.get("ignoreallmods")), _truthy(conf.get("excludeallmods"))
    mods: list[Mod] = []
    try:
        entries = sorted((e for e in os.scandir(mdir) if e.is_dir() and not e.name.startswith(".")),
                         key=lambda e: e.name.lower())
    except OSError:
        entries = []
    for e in entries:
        name = e.name
        pr = prio.get(name.lower(), 50)
        why = None
        if ignore_all:
            why = "ignore-all"
        elif _match(name, ignore):
            why = "ignore-mods"
        elif pr == 0:
            why = "priority-0"
        elif exclude_all and not _match(name, include):
            why = "exclude-all"
        elif _match(name, exclusive_other):
            why = "exclusive"
        nested = (Path(e.path) / INI_NAME).is_file()
        mods.append(Mod(name, Path(e.path), why is None, why, pr, nested))
    return Folder(mdir, ini if ini.is_file() else None, profile, sections, names, mods, ignore)


# --------------------------------------------------------------------------- models of mods


@dataclass(frozen=True, slots=True)
class ModelDef:
    mod: str
    relpath: str        # relative to the modloader folder, forward slashes
    line: int
    name: str
    sec: str


@dataclass(slots=True)
class ModScan:
    folder: Folder
    defs: dict[int, list[ModelDef]] = field(default_factory=dict)       # model ID -> definitions
    files: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # lower stem -> [(mod, relpath)]
    loaded: dict[str, bool] = field(default_factory=dict)              # mod -> loaded
    truncated: bool = False

    def defined(self, mid: int) -> list[ModelDef]:
        return self.defs.get(mid, [])

    def replacing(self, name: str | None) -> list[tuple[str, str]]:
        return self.files.get((name or "").lower(), []) if name else []


def scan_models(folder: Folder) -> ModScan:
    """IDE model definitions and DFF/TXD file names of every mod (loaded or not)."""
    from ..core.paths import open_ro
    from ..formats.ide import parse_ide

    out = ModScan(folder)
    n = 0
    for mod in folder.mods:
        out.loaded[mod.name] = mod.loaded
        for dp, dn, fn in os.walk(mod.path):
            dn[:] = sorted(d for d in dn if not d.startswith("."))
            for f in sorted(fn):
                n += 1
                if n > _MAX_FILES:
                    out.truncated = True
                    return out
                low = f.lower()
                ext = low.rsplit(".", 1)[-1] if "." in low else ""
                full = Path(dp) / f
                rel = full.relative_to(folder.path).as_posix()
                if ext in ("dff", "txd"):
                    out.files.setdefault(low.rsplit(".", 1)[0], []).append((mod.name, rel))
                elif ext == "ide":
                    try:
                        if full.stat().st_size > _MAX_IDE:
                            continue
                        with open_ro(full) as fh:
                            text = fh.read().decode("latin-1")
                    except OSError:
                        continue
                    defs, _txdp, _fx = parse_ide(text)
                    for d in defs:
                        out.defs.setdefault(d.id, []).append(ModelDef(mod.name, rel, d.line, d.name, d.sec))
    return out
