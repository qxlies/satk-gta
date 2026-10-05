"""Engine load order knowledge (SPEC §4.2 ``satk.formats.layout``), used by index and blender.

Archive registration ("first registered archive wins" per namespace, ``Streaming.cpp:1188``):

* ``img_order="engine"`` (vanilla, installed): ``CStreaming::InitImageList`` registers
  ``MODELS\\GTA3.IMG`` (order 0) and ``MODELS\\GTA_INT.IMG`` (1) from the exe, then the ``IMG``
  lines of the DAT files in order (``FileLoader.cpp:1380``). The line ``MODELS\\GTA_INT.IMG`` is
  skipped like the engine does; a repeated archive (e.g. ``GTA3.IMG``) is registered once.
* ``img_order="samp"``: samp.dll patches ``InitImageList`` to ``RET`` (V8), so the ``IMG`` lines
  of the ``.two`` files are used strictly in order, ``GTA_INT.IMG`` at its own position
  (assumption, see :data:`ASSUMPTIONS`).

Namespaces (``ns``, as in the index DDL): ``main`` (streaming), ``player`` (``PLAYER.IMG``,
``Clothes.cpp:42``), ``anim`` (``ANIM\\ANIM.IMG``), ``cuts`` (``ANIM\\CUTS.IMG``,
``CutsceneMgr``). The three special archives follow the ``main`` ones in both orders.

Example::

    for a in archives(root, ["data/default.dat", "data/gta.dat"], "engine"):
        print(a.order, a.ns, a.relpath, a.load_ref)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .dat import canon_relpath, parse_dat, read_text, resolve_ci
from .rw import FormatError

__all__ = ["ArchiveSpec", "archives", "loose_assets", "ASSUMPTIONS", "IMG_ORDERS", "LOOSE_EXTS", "PROFILES"]

IMG_ORDERS = ("engine", "samp")
#: Extensions counted as loose assets under ``models/`` and ``anim/``.
LOOSE_EXTS = ("txd", "dff", "col", "ifp")
#: Built-in profile defaults (same as ``satk.toml`` ``[profiles.*]``; usable without a config).
PROFILES: dict[str, dict] = {
    "vanilla": {"dat": ["data/default.dat", "data/gta.dat"], "img_order": "engine"},
    "installed": {"dat": ["data/default.dat", "data/gta.dat"], "img_order": "engine"},
    "samp": {"dat": ["data/default.two", "data/gta.two"], "img_order": "samp"},
}
#: Assumptions to record in ``meta.assumptions`` of an index built with that order.
ASSUMPTIONS: dict[str, list[str]] = {
    "engine": [],
    "samp": [
        "InitImageList is patched out by samp.dll (V8): IMG lines of the .two files are registered strictly in order",
        "MODELS\\GTA_INT.IMG listed in a .two file is registered at its own position (not verified in samp.dll)",
    ],
}

_SPECIAL = (
    ("MODELS\\PLAYER.IMG", "player", "exe:CClothes::Init"),
    ("ANIM\\ANIM.IMG", "anim", "exe:anim.img"),
    ("ANIM\\CUTS.IMG", "cuts", "exe:CCutsceneMgr"),
)
_EXE_MAIN = ("MODELS\\GTA3.IMG", "MODELS\\GTA_INT.IMG")


@dataclass(frozen=True, slots=True)
class ArchiveSpec:
    """A registered IMG archive.

    ``relpath``: canonical (lower-case, forward slashes) path relative to the game root;
    ``ns``: ``main|player|anim|cuts``; ``load_ref``: ``exe:InitImageList`` or ``<dat relpath>:<line>``;
    ``order``: registration order (0 = first = wins inside its namespace).
    """

    relpath: str
    ns: str
    load_ref: str
    order: int


def archives(root: Path, dat_files: list[str], img_order: Literal["engine", "samp"] | str) -> list[ArchiveSpec]:
    """IMG archives of a load profile in registration order (see the module docstring).

    Missing DAT files raise ``FormatError(kind="layout")``; IMG lines pointing to missing
    files are still listed (the index reports them).
    """
    if img_order not in IMG_ORDERS:
        raise FormatError("layout", 0, f"unknown img_order {img_order!r}; expected one of {IMG_ORDERS}")
    root = Path(root)
    out: list[ArchiveSpec] = []
    seen: set[str] = set()

    def add(dos: str, ns: str, ref: str) -> None:
        p = resolve_ci(root, dos)
        rel = canon_relpath(os.path.relpath(p, root)) if p is not None else canon_relpath(dos)
        if rel in seen:
            return
        seen.add(rel)
        out.append(ArchiveSpec(rel, ns, ref, len(out)))

    if img_order == "engine":
        for dos in _EXE_MAIN:
            add(dos, "main", "exe:InitImageList")
    gta_int = canon_relpath(_EXE_MAIN[1])
    for dat in dat_files:
        p = resolve_ci(root, dat)
        if p is None:
            raise FormatError("layout", 0, f"DAT file not found: {dat} (root {root})")
        dat_rel = canon_relpath(os.path.relpath(p, root))
        for line in parse_dat(read_text(p)):
            if line.key != "IMG" or not line.arg:
                continue
            if img_order == "engine" and canon_relpath(line.arg) == gta_int:
                continue  # FileLoader.cpp:1380: the engine never registers GTA_INT.IMG from a DAT
            add(line.arg, "main", f"{dat_rel}:{line.line}")
    for dos, ns, ref in _SPECIAL:
        add(dos, ns, ref)
    return out


def loose_assets(root: Path, exts: tuple[str, ...] = LOOSE_EXTS) -> list[str]:
    """Loose asset files under ``models/`` and ``anim/`` (canonical relpaths, sorted).

    These are the files the engine loads directly (``models/generic/vehicle.txd``,
    ``models/coll/*.col``, ``anim/ped.ifp``, ...), outside any IMG.
    """
    root = Path(root)
    want = {"." + e.lower() for e in exts}
    out: list[str] = []
    for top in ("models", "anim"):
        d = resolve_ci(root, top)
        if d is None or not d.is_dir():
            continue
        for dp, _dn, fn in os.walk(d):
            for f in fn:
                if os.path.splitext(f)[1].lower() in want:
                    out.append(canon_relpath(os.path.relpath(os.path.join(dp, f), root)))
    return sorted(out)
