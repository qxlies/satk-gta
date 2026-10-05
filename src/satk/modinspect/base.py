"""The game under the mods: what a load profile's root holds before Mod Loader changes anything.

:class:`BaseGame` reads the profile root READ-ONLY (``open_ro``) and lazily caches what the mod
inspector compares against:

* IMG directory names of every registered archive (``satk.formats.layout.archives``) -> which
  streamed file a mod replaces, and in which archive;
* the IDE and IPL lines of the profile's DAT files (Mod Loader's level-file entries) and the model
  definitions they load (ID -> name, TXD, section, file) -> new/overridden IDs;
* the game's data files (``data/handling.cfg`` ...) -> the "default store" of every merge.

It does not need an index build. Stdlib only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import cfg, jpath, open_ro
from ..formats.dat import canon_relpath, parse_dat, read_text, resolve_ci
from ..formats.ide import parse_ide
from ..formats.img import ImgArchive
from ..formats.layout import archives
from ..formats.rw import FormatError
from .traits import norm_path

__all__ = ["BaseGame", "ModelDef", "data_candidates", "VANILLA_MAX_ID"]

#: Model slots of the unmodified game (``CModelInfo::ms_modelInfoPtrs[20000]``); higher IDs need a limit adjuster.
VANILLA_MAX_ID = 19999


@dataclass(frozen=True, slots=True)
class ModelDef:
    id: int
    name: str
    txd: str | None
    sec: str
    ide: str          # canonical relpath of the IDE
    handling: str | None = None


def data_candidates(fs_name: str) -> list[str]:
    """Where the game keeps a data file Mod Loader handles by name (``handling.cfg`` -> ``data/handling.cfg``)."""
    n = fs_name.lower()
    return [f"data/{n}", f"data/paths/{n}", n, f"data/decision/allowed/{n}"]


class BaseGame:
    """Read-only view of a profile root.

    Args:
        profile: load profile (``vanilla``, ``installed``, ``game``, ...).
        root, dat, img_order: overrides (tests use synthetic roots).
    """

    def __init__(self, profile: str = "vanilla", *, root: Path | None = None, dat: list[str] | None = None,
                 img_order: str | None = None):
        if root is None:
            p = cfg().profile(profile)
            root, dat, img_order = p.root, list(dat or p.dat), img_order or p.img_order
        self.profile = profile
        self.root = Path(os.path.abspath(root))
        self.dat = list(dat or ["data/default.dat", "data/gta.dat"])
        self.img_order = img_order or "engine"
        self.warnings: list[str] = []
        if not self.root.is_dir():
            raise SatkError("NOT_FOUND", f"profile {profile!r}: game root not found: {jpath(self.root)}",
                            hint="satk config show (profiles); a single-install user usually wants --profile game")

    # ---- IMG archives

    @cached_property
    def img_names(self) -> dict[str, tuple[str, str, int, int]]:
        """``{lower entry name: (archive relpath, ns, byte offset, size)}``; the first registered archive wins."""
        out: dict[str, tuple[str, str, int, int]] = {}
        try:
            specs = archives(self.root, self.dat, self.img_order)
        except FormatError as e:
            self.warnings.append(f"BASE: {e}")
            return out
        for a in specs:
            p = resolve_ci(self.root, a.relpath)
            if p is None:
                continue
            try:
                with ImgArchive.open(p) as img:
                    for e in img.entries:
                        out.setdefault(e.name.lower(), (a.relpath, a.ns, e.abs_offset, e.size))
            except (FormatError, OSError) as e:
                self.warnings.append(f"BASE: {a.relpath}: {e}")
        return out

    def read_entry(self, name: str) -> bytes | None:
        """Bytes of the winning IMG entry ``name`` (sector padded), or ``None``."""
        hit = self.img_names.get(name.lower())
        p = resolve_ci(self.root, hit[0]) if hit else None
        if p is None:
            return None
        with open_ro(p) as f:
            f.seek(hit[2])
            return f.read(hit[3])

    # ---- level files

    @cached_property
    def level_entries(self) -> list[tuple[str, str, str]]:
        """``(directive, canonical path, dat relpath:line)`` of every DAT line, in load order."""
        out = []
        for d in self.dat:
            p = resolve_ci(self.root, d)
            if p is None:
                self.warnings.append(f"BASE: {d} not found")
                continue
            for ln in parse_dat(read_text(p)):
                out.append((ln.key, norm_path(ln.path), f"{canon_relpath(d)}:{ln.line}"))
        return out

    @cached_property
    def ide_paths(self) -> list[str]:
        seen: dict[str, None] = {}
        for k, path, _ in self.level_entries:
            if k == "IDE":
                seen.setdefault(path)
        return list(seen)

    @cached_property
    def ipl_paths(self) -> list[str]:
        seen: dict[str, None] = {}
        for k, path, _ in self.level_entries:
            if k == "IPL":
                seen.setdefault(path)
        return list(seen)

    # ---- model definitions

    @cached_property
    def models(self) -> dict[int, ModelDef]:
        """Active model definitions: a later IDE line of the same ID replaces an earlier one (engine order)."""
        out: dict[int, ModelDef] = {}
        for rel in self.ide_paths:
            p = resolve_ci(self.root, rel)
            if p is None:
                continue
            try:
                defs, _txdp, _fx = parse_ide(read_text(p))
            except OSError as e:
                self.warnings.append(f"BASE: {rel}: {e}")
                continue
            for d in defs:
                h = d.extra.get("handling") if d.sec == "cars" else None
                out[d.id] = ModelDef(d.id, d.name, d.txd, d.sec, rel, str(h) if h is not None else None)
        return out

    @cached_property
    def by_name(self) -> dict[str, ModelDef]:
        out: dict[str, ModelDef] = {}
        for m in self.models.values():
            out.setdefault(m.name.lower(), m)
        return out

    @cached_property
    def txd_users(self) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for m in self.models.values():
            if m.txd:
                out.setdefault(m.txd.lower(), []).append(m.id)
        return out

    # ---- files

    def file(self, rel: str) -> Path | None:
        """A file of the root by canonical relpath (case-insensitive)."""
        return resolve_ci(self.root, rel)

    def data_file(self, fs_name: str) -> tuple[str, Path] | None:
        """``(relpath, path)`` of the game's copy of a data file handled by name, or ``None``."""
        for rel in data_candidates(fs_name):
            p = resolve_ci(self.root, rel)
            if p is not None and p.is_file():
                return canon_relpath(rel), p
        return None

    def text(self, path: Path) -> str:
        return read_text(path)

    def match_gta_path(self, vpath: str, kind: str, extra: list[str] = ()) -> str | None:
        """Mod Loader's ``ProcessGtaDatEntries``: the level-file path an IDE/IPL of a mod loads as.

        ``vpath`` (``data/...`` from :func:`satk.modinspect.classify.gta_path`) is used when a level
        entry has exactly that path; otherwise the file name must name exactly one entry; else the
        file is not loaded (``None``). ``extra`` adds entries from mods' own ``gta.dat`` files.
        """
        entries = (self.ide_paths if kind == "ide" else self.ipl_paths) + list(extra)
        if vpath in entries:
            return vpath
        fname = vpath.rsplit("/", 1)[-1]
        hits = sorted({e for e in entries if e.rsplit("/", 1)[-1] == fname})
        if len(hits) == 1:
            return hits[0]
        return None
