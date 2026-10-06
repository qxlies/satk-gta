"""Which textures of a TXD the game can use: the models that load the TXD and the names in their DFFs.

A TXD's *users* are the models whose IDE definition names it, directly or through ``txdp`` children (the child
TXD falls back to its parent). They come from the IDE files of the bundle (a mod) and from the profile index
(``model.txd``, ``txd.parent``). For each user the DFF is read from the bundle when the mod ships one, otherwise
from the game through the index; every printable NUL-terminated string of the DFF counts as a referenced name.
That covers material textures and masks, MatFX environment/dual textures (Texture chunks inside the effect
data) and the ``char[32]`` names of the Breakable plugin, without parsing each plugin.

A TXD is *undecided* (every texture kept) when no model uses it (code-, script- or paintjob-loaded TXDs such as
``particle``, ``hud``, ``models/txd/*`` or ``elegy1``), when it is the implicit parent of all vehicles
(``vehicle``), when weapon, ped or cutscene (``hier``) models use it (the game finds ``<weapon>ICON``, crosshairs and
clothes by name), or when a user's DFF cannot be found. Names starting with ``#`` or ``remap`` (vehicle recolouring)
are always kept; ``keep`` adds glob patterns. SA-MP ``SetObjectMaterial``, MTA shaders and scripts reference
textures by name without a DFF: keep such names with ``keep``.

Stdlib only.
"""

from __future__ import annotations

import fnmatch
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.errors import SatkError
from .inputs import Bundle

__all__ = ["ALWAYS_KEEP", "CODE_SECTIONS", "ONE_LEVEL_SECTIONS", "UPGRADE_IDE", "Verdict", "Usage", "dff_strings",
           "index_conn", "keep_matcher"]

#: Texture-name globs never dropped (vehicle remap textures are renamed to ``#...`` at load time).
ALWAYS_KEEP = ("#*", "remap*")
#: IDE sections whose TXDs are never pruned: the game looks textures up by name (``<weapon>ICON``, crosshairs,
#: clothes of the player and of the cutscene player ``csplay`` in ``hier``) besides the DFF materials.
CODE_SECTIONS = ("weap", "peds", "hier")
#: IDE sections whose vanilla textures have one level only (0 of 593 vehicle, 289 ped and 114 weapon textures have
#: mips): a missing chain there is the game's own style, not an issue (``txd.mips_missing`` is info for them too).
ONE_LEVEL_SECTIONS = {"cars": "vehicle", "peds": "ped", "weap": "weapon"}
#: IDE file of vehicle upgrades (their textures follow the vehicles).
UPGRADE_IDE = "veh_mods.ide"
_RUN = re.compile(rb"(?<![\x20-\x7e])([\x20-\x7e]{1,63})\x00")


def dff_strings(buf: bytes) -> set[str]:
    """Lower-case printable NUL-terminated strings of a DFF and all their suffixes.

    The suffixes cover names in fixed-size fields whose preceding byte happens to be printable (the corona and
    shadow names of a 2dEffect light follow flag bytes); a suffix match keeps a texture, never drops one.
    """
    out: set[str] = set()
    for m in _RUN.finditer(bytes(buf)):
        s = m.group(1).decode("latin-1").lower()
        out.update(s[i:] for i in range(len(s)))
    return out


def keep_matcher(patterns: list[str] | None) -> Callable[[str], bool]:
    """``name -> True`` when the name matches :data:`ALWAYS_KEEP` or one of ``patterns`` (globs, any case)."""
    pats = [p.lower() for p in (*ALWAYS_KEEP, *(patterns or [])) if p]

    def match(name: str) -> bool:
        n = name.lower()
        return any(fnmatch.fnmatchcase(n, p) for p in pats)

    return match


def index_conn(profile: str) -> tuple[object, sqlite3.Connection]:
    """``(IndexDB, read-only sqlite3 connection)`` of the profile's index; ``INDEX_MISSING`` without one."""
    from ..index.api import open_index

    db = open_index(profile)
    path = getattr(db, "path", None)
    if path is None or not Path(path).is_file():
        raise SatkError("INDEX_MISSING", f"profile {profile!r} has no index file (a fake or in-memory index)",
                        hint=f"satk index build --profile {profile}")
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    con.execute("PRAGMA query_only = 1")
    return db, con


@dataclass
class Verdict:
    """Users of one TXD. ``names`` is ``None`` when undecided (keep everything); ``why`` says why;
    ``secs`` = IDE sections of the user models."""

    stem: str
    models: list[str] = field(default_factory=list)
    names: set[str] | None = None
    why: str = ""
    secs: frozenset[str] = frozenset()


class Usage:
    """Resolves the users of TXDs for one bundle against one profile (index optional)."""

    def __init__(self, bundle: Bundle, profile: str = "vanilla", *, use_index: bool = True):
        from ..formats.ide import parse_ide

        self.bundle = bundle
        self.profile = profile
        self.warn: list[str] = []
        self.mod_models: dict[str, list[tuple[str, str]]] = {}   # txd stem -> (model name, IDE section)
        self.mod_parent: dict[str, str] = {}            # child txd -> parent txd (bundle IDEs)
        self.mod_ide_of: dict[str, str] = {}            # model name -> IDE rel that defines it
        self.dffs: dict[str, object] = {}               # dff stem -> Item (bundle)
        for it in bundle.of("dff"):
            self.dffs.setdefault(it.stem, it)
        for it in bundle.of("ide"):
            try:
                text = it.read().decode("latin-1")
            except SatkError as e:
                self.warn.append(f"UNREADABLE: {it.rel}: {e.msg}")
                continue
            defs, txdp, _fx = parse_ide(text)
            for d in defs:
                if d.txd:
                    self.mod_models.setdefault(d.txd.lower(), []).append((d.name.lower(), d.sec))
                self.mod_ide_of.setdefault(d.name.lower(), it.rel)
            for child, parent in txdp:
                if child.lower() != parent.lower():
                    self.mod_parent.setdefault(child.lower(), parent.lower())
        self.db = None
        self.con: sqlite3.Connection | None = None
        if use_index:
            try:
                self.db, self.con = index_conn(profile)
            except SatkError as e:
                self.warn.append(f"{e.code}: no {profile} index: only the bundle's IDE/DFF files decide which "
                                 "textures are used")
        self._strings: dict[str, set[str] | None] = {}
        self._cache: dict[str, Verdict] = {}

    def close(self) -> None:
        if self.con is not None:
            self.con.close()
            self.con = None

    # ------------------------------------------------------------------ index lookups

    def _rows(self, sql: str, params: tuple) -> list[tuple]:
        if self.con is None:
            return []
        return self.con.execute(sql, params).fetchall()

    def index_models(self, stem: str) -> list[tuple[str, str]]:
        """``(model name, IDE section)`` of the active models whose TXD is ``stem``."""
        return [(r[0].lower(), r[1]) for r in self._rows("SELECT name, sec FROM model WHERE active = 1 AND txd = ? "
                                                          "ORDER BY id", (stem,))]

    def index_children(self, stem: str) -> list[str]:
        return [r[0].lower() for r in self._rows(
            "SELECT DISTINCT t.name FROM txd t JOIN blob b ON b.id = t.blob_id WHERE b.active = 1 "
            "AND t.parent = ? AND t.parent_via = 'txdp' ORDER BY t.name", (stem,))]

    def index_parent(self, stem: str) -> str | None:
        r = self._rows("SELECT t.parent FROM txd t JOIN blob b ON b.id = t.blob_id WHERE b.active = 1 AND "
                       "t.name = ? AND t.parent_via = 'txdp' LIMIT 1", (stem,))
        return r[0][0].lower() if r and r[0][0] else None

    def txd_exists(self, stem: str) -> bool:
        """A TXD of this name exists in the index (any layer) or in the bundle's IDE files."""
        return stem in self.mod_models or bool(self._rows("SELECT 1 FROM txd WHERE name = ? LIMIT 1", (stem,)))

    def vehicle_parent(self, stem: str) -> bool:
        if stem == "vehicle":
            return True
        return bool(self._rows("SELECT 1 FROM txd WHERE parent = ? AND parent_via = 'vehicle' LIMIT 1", (stem,)))

    def parent_of(self, stem: str) -> str | None:
        return self.mod_parent.get(stem) or self.index_parent(stem)

    def one_level_class(self, stem: str) -> str | None:
        """``vehicle``/``ped``/``weapon``/``upgrade`` when the TXD belongs to a class whose vanilla textures have
        exactly one level (no mip chain), else ``None``. Decided by its users (bundle IDEs, then the index) and
        by ``vehicle.txd``; a mod TXD named like a vanilla one takes that one's users (a replacement)."""
        stem = stem.lower()
        if self.vehicle_parent(stem):
            return "vehicle"
        users = self.mod_models.get(stem, []) + self.index_models(stem)
        secs = {sec for _m, sec in users}
        for sec, cls in ONE_LEVEL_SECTIONS.items():
            if sec in secs:
                return cls
        if any(UPGRADE_IDE in self.mod_ide_of.get(m, "").lower().replace("\\", "/") for m, _s in users):
            return "upgrade"
        return None

    # ------------------------------------------------------------------ DFF names

    def strings_of(self, model: str) -> set[str] | None:
        """Names in the DFF of ``model`` (bundle first, then the game); ``None`` if no DFF is found."""
        if model in self._strings:
            return self._strings[model]
        out: set[str] | None = None
        it = self.dffs.get(model)
        if it is not None:
            try:
                out = dff_strings(it.read())
            except SatkError as e:
                self.warn.append(f"UNREADABLE: {it.rel}: {e.msg}")
        elif self.db is not None:
            from ..index.api import read_blob_bytes

            try:
                out = dff_strings(read_blob_bytes(self.db.blob_ref(f"dff:{model}")))
            except SatkError:
                out = None
        self._strings[model] = out
        return out

    # ------------------------------------------------------------------ verdict

    def verdict(self, stem: str) -> Verdict:
        stem = stem.lower()
        v = self._cache.get(stem)
        if v is None:
            v = self._cache[stem] = self._verdict(stem)
        return v

    def _family(self, stem: str) -> list[str]:
        """``stem`` and every TXD that falls back to it through ``txdp`` (bundle IDEs + index)."""
        out, todo = [stem], [stem]
        while todo:
            cur = todo.pop()
            kids = [c for c, p in self.mod_parent.items() if p == cur] + self.index_children(cur)
            for k in kids:
                if k not in out and len(out) < 4096:
                    out.append(k)
                    todo.append(k)
        return out

    def _verdict(self, stem: str) -> Verdict:
        if self.vehicle_parent(stem):
            return Verdict(stem, why="parent of every vehicle (vehicle.txd): textures are looked up by code")
        models: list[str] = []
        secs: set[str] = set()
        for s in self._family(stem):
            for m, sec in self.mod_models.get(s, []) + self.index_models(s):
                secs.add(sec)
                if m not in models:
                    models.append(m)
        if not models:
            return Verdict(stem, why="no model loads it (code, script, paintjob or an IDE outside the input)")
        code = sorted(secs & set(CODE_SECTIONS))
        if code:
            return Verdict(stem, models, None, f"used by {'/'.join(code)} models: the game also finds their textures "
                                               "by name (weapon icons and crosshairs, player clothes)", frozenset(secs))
        names: set[str] = set()
        for m in models:
            got = self.strings_of(m)
            if got is None:
                return Verdict(stem, models, None, f"no DFF found for user model {m!r}", frozenset(secs))
            names |= got
        return Verdict(stem, models, names, f"{len(models)} model(s)", frozenset(secs))
