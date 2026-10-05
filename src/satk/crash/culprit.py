"""Who is behind a crash: model IDs (registers, SCRLog commands) -> definition -> the mod.

A model ID is attributed with two sources, both read-only:

* the asset index of the crashed game's profile (``satk.index.api``): the active definition and
  its layer (``vanilla``, ``modded``, ``samp``, ``modloader:<mod>``); without a profile for that
  folder the default profile's index only supplies vanilla model names;
* the game's ``modloader`` folder (:mod:`.modfolder`): mods whose ``*.ide`` define the ID, mods
  that ship ``<name>.dff``/``<name>.txd`` of the model, and mods that modloader.ini disables.

Strength of a candidate: ``hint`` (the matching CrashInfo entry names the register, e.g. EAX for
0x00456809), ``log`` (SCRLog ``REQUEST_MODEL``/``CREATE_*`` right before the crash) and ``reg``
(any other general register; reported only when a mod defines that ID). Stdlib only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import SatkError
from .modfolder import Folder, ModelDef, ModScan, find_folder, read_folder, scan_models

__all__ = ["Candidate", "Owner", "Resolver", "attribute", "REGS", "MAX_MODEL_ID"]

REGS = ("eax", "ebx", "ecx", "edx", "esi", "edi")
MAX_MODEL_ID = 0xFFFF
_RANK = {"hint": 0, "log": 1, "reg": 2}


@dataclass(slots=True)
class Candidate:
    mid: int
    via: str            # "EAX=0x4e85" | "scrlog REQUEST_MODEL (script coolpk)" | "ECX=0x4e85"
    strength: str       # hint | log | reg


@dataclass(slots=True)
class Owner:
    mid: int
    name: str | None = None
    layer: str | None = None                              # layer of the active definition (profile index)
    in_index: bool | None = None                          # None: no index for this game
    defs: list[ModelDef] = field(default_factory=list)    # loaded mods defining the ID
    defs_off: list[ModelDef] = field(default_factory=list)  # disabled mods defining it
    replaced: list[tuple[str, str]] = field(default_factory=list)   # loaded mods with <name>.dff/.txd


def _norm(p: Path) -> str:
    return os.path.normcase(os.path.abspath(p))


class Resolver:
    """Model ID -> :class:`Owner` for one game folder (and/or index profile)."""

    def __init__(self, game: Path | None, profile: str | None = None):
        self.warn: list[str] = []
        self.folder: Folder | None = None
        self.scan: ModScan | None = None
        self.db = None
        self.layers = False            # the index belongs to this game: its layers attribute models
        self.profile: str | None = None
        if game is not None:
            mdir = find_folder(game)
            if mdir is not None:
                self.folder = read_folder(mdir)
                self.scan = scan_models(self.folder)
                if self.scan.truncated:
                    self.warn.append("TRUNCATED: the modloader folder has too many files; the scan stopped early")
        self._open(game, profile)

    def _open(self, game: Path | None, profile: str | None) -> None:
        from ..core.paths import cfg
        from ..index.api import open_index

        c = cfg()
        names: list[tuple[str, bool]] = []
        if profile:
            names.append((profile, True))
        else:
            if game is not None:
                g = _norm(Path(game))
                for name, p in sorted(c.profiles.items()):
                    if _norm(p.root) == g:
                        names.append((name, True))
            names.append((c.default_profile, False))
        tried = []
        for name, own in names:
            try:
                self.db = open_index(name)
            except SatkError as e:
                tried.append(f"{name} ({e.code})")
                if profile:
                    self.warn.append(f"{e.code}: index of profile {name!r} unusable ({e.msg}); "
                                     "model names come from the modloader folder only")
                continue
            self.layers, self.profile = own, name
            return
        if self.folder is None and tried:
            self.warn.append("NOT_READY: no asset index and no modloader folder: model IDs are not attributed "
                             f"(tried {', '.join(tried)}; satk index build or --game DIR)")

    @property
    def usable(self) -> bool:
        return self.db is not None or self.scan is not None

    def owner(self, mid: int) -> Owner:
        o = Owner(mid)
        if self.db is not None:
            try:
                g = self.db.get(f"model:{mid}")
                o.name, o.in_index = g.get("name"), True
                o.layer = g.get("layer") if self.layers else None
            except SatkError as e:
                if e.code == "NOT_FOUND":
                    # only the game's own index proves that the game does not define the model
                    o.in_index = False if self.layers else None
                else:
                    self.warn.append(f"{e.code}: model lookup failed ({e.msg})")
                    self.db = None
        if self.scan is not None:
            for d in self.scan.defined(mid):
                (o.defs if self.scan.loaded.get(d.mod, True) else o.defs_off).append(d)
            if o.name is None and (o.defs or o.defs_off):
                o.name = (o.defs or o.defs_off)[0].name
            if o.in_index and not o.defs:
                o.replaced = [(m, r) for m, r in self.scan.replacing(o.name) if self.scan.loaded.get(m, True)]
            if not self.layers and o.in_index and not o.defs:
                o.layer = "vanilla?"   # name from the default profile only
        return o


def _mods(defs: list[ModelDef]) -> list[str]:
    return list(dict.fromkeys(d.mod for d in defs))


def _where(d: ModelDef) -> str:
    return f"{d.relpath}:{d.line}"


def _describe(o: Owner, c: Candidate) -> tuple[str | None, str | None, int]:
    """``(suspect line, culprit or None, weight)`` for one candidate; ``(None, None, 0)`` = not worth showing."""
    nm = f" '{o.name}'" if o.name else ""
    head = f"{c.via} -> model {o.mid}{nm}"
    if o.defs:
        mods = _mods(o.defs)
        if len(mods) > 1:
            return (f"{head}: ID conflict, defined by mods {', '.join(mods)} "
                    f"({', '.join(_where(d) for d in o.defs[:3])})",
                    f"ID conflict on model {o.mid}: mods {', '.join(mods)} define it", 3)
        extra = " (overrides a vanilla ID)" if o.in_index and o.layer in ("vanilla", "vanilla?") else ""
        return f"{head}: defined by mod {mods[0]}{extra} ({_where(o.defs[0])})", \
            f"{mods[0]} (modloader mod defining model {o.mid}, {c.via.split(' ')[0]})", 3
    if o.defs_off:
        mods = _mods(o.defs_off)
        return (f"{head}: defined only by disabled mod {', '.join(mods)} ({_where(o.defs_off[0])})",
                f"model {o.mid} is missing: only the disabled mod {', '.join(mods)} defines it", 2)
    if o.layer and o.layer not in ("vanilla", "vanilla?"):
        return f"{head}: defined in layer {o.layer}", f"{o.layer} (layer defining model {o.mid})", 2
    if o.replaced and c.strength != "reg":
        mods = list(dict.fromkeys(m for m, _r in o.replaced))
        return (f"{head}: vanilla model replaced by mod {', '.join(mods)} ({o.replaced[0][1]})",
                f"{mods[0]} (replaces model {o.mid}{nm})" if len(mods) == 1 else None, 1)
    if o.in_index is False or (o.in_index is None and c.strength == "hint"):
        if o.in_index is None and o.name is None and c.strength == "hint":
            return f"{head}: not defined by any modloader mod (no index to check the game files)", None, 1
        return (f"{head}: not defined by the game or any modloader mod (a removed mod?)",
                f"model {o.mid} does not exist: the mod that added it was removed (or its IDE is not loaded)", 2)
    if c.strength == "hint":
        kind = "vanilla" if (o.layer or "").startswith("vanilla") else (o.layer or "game")
        return f"{head}: {kind} model, no mod touches it", None, 0
    return None, None, 0


def attribute(cands: list[Candidate], res: Resolver, *, limit: int = 3) -> tuple[list[str], str | None]:
    """Suspect lines (strongest first) and the culprit (from the strongest attributable candidate)."""
    best: dict[int, Candidate] = {}
    for c in cands:
        if not 0 < c.mid <= MAX_MODEL_ID:
            continue
        old = best.get(c.mid)
        if old is None or _RANK[c.strength] < _RANK[old.strength]:
            best[c.mid] = c
    ordered = sorted(best.values(), key=lambda c: (_RANK[c.strength], cands.index(c)))
    lines: list[str] = []
    culprit: str | None = None
    culprit_rank = 99
    for c in ordered:
        o = res.owner(c.mid)
        if c.strength == "reg" and not o.defs:
            continue
        line, who, weight = _describe(o, c)
        if line is None:
            continue
        lines.append(line)
        if who and weight >= 1 and c.strength != "reg":          # a stray register value is a suspect only
            rank = _RANK[c.strength] * 10 - weight
            if rank < culprit_rank:
                culprit, culprit_rank = who, rank
        if len(lines) >= limit:
            break
    return lines, culprit


def reg_candidates(regs: dict[str, int], hinted: list[str]) -> list[Candidate]:
    """Hinted registers first (strength ``hint``), then the other general registers (``reg``)."""
    out: list[Candidate] = []
    for r in hinted:
        v = regs.get(r)
        if v is not None:
            out.append(Candidate(v, f"{r.upper()}=0x{v:x}", "hint"))
    for r in REGS:
        if r in hinted:
            continue
        v = regs.get(r)
        if v is not None and 0 < v <= MAX_MODEL_ID:
            out.append(Candidate(v, f"{r.upper()}=0x{v:x}", "reg"))
    return out


def game_dir_of(path: Path) -> Path | None:
    """The game folder a log or dump lies in (``<game>/modloader/modloader.log`` -> ``<game>``)."""
    d = path if path.is_dir() else path.parent
    for cand in (d, d.parent, d.parent.parent):
        try:
            if (cand / "gta_sa.exe").is_file() or (cand / "modloader").is_dir():
                return cand
        except OSError:
            continue
    return None
