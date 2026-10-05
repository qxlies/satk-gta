"""``FakeIndexDB``: the full ``satk.index.api`` over a tiny built-in data set (SPEC §4.3.4, Q1).

For tests of WP-04/05/07/10/12 before (and without) a real index. No game files are read.

Data set (names, numbers and links modelled on the vanilla profile; offsets, sizes, pixel
hashes and bounding boxes are synthetic - golden numbers live in ``tests/golden``):

* ``model:411`` ``infernus`` (cars): ``dff:infernus``, TXD chain ``txd:infernus`` -> ``txd:vehicle``
  (implicit parent of cars, loose ``models/generic/vehicle.txd``), embedded COL, 13 material
  textures, all resolved;
* ``model:17613`` ``lae2_roads89`` (objs): ``txd:lae2roadshub``, ``col:lae2_roads89`` (``lae2_4.col``),
  placement ``inst:lae2_stream0#4`` @ (2489.30, -1668.50, 12.30) with LOD ``inst:lae2#198``
  (``model:17858`` ``lodlae2_roads89``);
* ``model:300`` ``cutobj01`` (hier, no DFF/TXD/COL in the game - a cutscene placeholder);
  in profile ``samp`` it is overridden by ``lapdna`` (layer ``samp``) and ``model:300@vanilla``
  is the inactive ``cutobj01``;
* ``file:models/gta_int.img/lawest1.txd`` is shadowed by ``file:models/gta3.img/lawest1.txd``;
* ``txd:bistro`` with ``tex:bistro/vent_64`` (X8R8G8B8 64x64) and ``tex:bistro/sw_wallbrick_01`` (DXT1).

Profiles: ``vanilla`` (default), ``installed`` (same data) and ``samp``.

Bytes: by default the archive paths point into a non-existent ``root`` and ``read_blob``
raises ``NOT_FOUND``. Pass ``payloads={sid: bytes}`` (``dff:``/``txd:``/``file:`` for blobs,
``tex:`` for mip0 pixels) and a writable ``root``: the fake then lays out and writes small
synthetic archives there, so ``BlobRef``/``TexRef`` offsets are real and readable::

    db = FakeIndexDB(root=tmp_path, payloads={"dff:infernus": my_synthetic_dff})
    assert db.read_blob(db.model_files(411).dff) == my_synthetic_dff

``query()`` runs against an in-memory SQLite database with the full schema v1 filled with
the same data (``to_sqlite(path)`` writes it to a file, e.g. to test :class:`IndexDB`).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ..core import envelope as env
from ..core.errors import SatkError
from ..core.ids import Sid
from ..core.paths import ensure_writable, jpath
from . import api as _api
from .api import (
    FIND_COLS, GENERIC_COLS, INST_COLS, LOD_MODES, MATCH_MODES, MODEL_TEX_COLS, NEAR_COLS, NEAR_KINDS,
    REFS_RELS, SEARCH_KINDS, BlobRef, ColRef, IndexDB, InstRow, ModelFiles, TexRef,
    as_sid, check_choice, page, read_blob_bytes, run_readonly_query, world_aabb, world_quat, world_rz,
)
from .ddl import SCHEMA_VERSION, create_schema

__all__ = ["FakeIndexDB", "FAKE_PROFILES"]

FAKE_PROFILES = ("vanilla", "installed", "samp")
_SECTOR = 2048
_NO_ROOT = Path(tempfile.gettempdir()) / "satk-fake-index" / "no-files-here"

# --------------------------------------------------------------------------- records


@dataclass
class _Layer:
    id: int
    name: str
    kind: str
    priority: int


@dataclass
class _Source:
    id: int
    layer: _Layer
    relpath: str
    kind: str
    load_ref: str | None
    load_order: int | None
    size: int = 0  # computed by the layout


@dataclass
class _Blob:
    id: int
    source: _Source
    idx: int
    name: str
    size: int
    ns: str
    active: bool = True
    shadowed_by: "_Blob | None" = None
    off: int = 0  # computed by the layout

    @property
    def stem(self) -> str:
        return self.name.rsplit(".", 1)[0]

    @property
    def ext(self) -> str:
        return self.name.rsplit(".", 1)[1]

    @property
    def file_key(self) -> str:
        s = self.source
        return f"{s.relpath}/{self.name}" if s.kind == "img" else s.relpath


@dataclass
class _Tex:
    id: int
    txd: "_Txd"
    idx: int
    name: str
    d3dfmt: str
    raster_fmt: int
    w: int
    h: int
    alpha: bool
    platform: int = 9
    levels: int = 1
    data_size: int = 0
    data_off: int = 0  # computed by the layout
    pal_off: int | None = None
    hash: str = ""


@dataclass
class _Txd:
    id: int
    blob: _Blob
    parent: str | None
    parent_via: str | None
    textures: list[_Tex] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.blob.stem


@dataclass
class _Dff:
    id: int
    blob: _Blob
    verts: int
    tris: int
    geoms: int
    bmin: tuple[float, float, float]
    bmax: tuple[float, float, float]
    bs: tuple[float, float, float, float]
    flags: int
    mats: list[tuple[str, int]]  # (texture, uses)

    @property
    def name(self) -> str:
        return self.blob.stem


@dataclass
class _Col:
    id: int
    blob: _Blob
    idx: int
    name: str
    version: int
    bmin: tuple[float, float, float]
    bmax: tuple[float, float, float]
    via: str


@dataclass
class _Ide:
    id: int
    source: _Source


@dataclass
class _Model:
    rid: int
    id: int
    layer: _Layer
    name: str
    txd: str | None
    sec: str
    ide: _Ide
    line: int
    draw: float | None
    flags: int | None
    extra: dict | None
    active: bool = True


@dataclass
class _Ipl:
    id: int
    name: str
    kind: str
    layer: _Layer
    source: _Source | None
    blob: _Blob | None
    parent: "_Ipl | None"
    loaded: bool = True


@dataclass
class _Inst:
    id: int
    ipl: _Ipl
    idx: int
    model_id: int
    area: int
    iflags: int
    pos: tuple[float, float, float]
    q: tuple[float, float, float, float]
    lod_idx: int
    lod: "_Inst | None" = None
    is_lod: bool = False
    bbox_src: str = "point"
    aabb: tuple[float, float, float, float, float, float] = (0.0,) * 6

    @property
    def sid(self) -> str:
        return f"inst:{self.ipl.name}#{self.idx}"


def _pix(txd: str, name: str) -> str:
    return hashlib.blake2b(f"satk-fake:{txd}/{name}".encode(), digest_size=12).hexdigest()


def _mip0_size(fmt: str, w: int, h: int) -> int:
    if fmt == "DXT1":
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 8
    if fmt in ("DXT3", "DXT5"):
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16
    if fmt in ("A8R8G8B8", "X8R8G8B8"):
        return w * h * 4
    if fmt in ("PAL8", "L8"):
        return w * h
    return w * h * 2


# raster formats (RW): 0x200 = 565, 0x300 = 4444, 0x500 = 8888, 0x600 = 888
_VEHICLE_TEX = [
    ("xvehicleenv128", 128, 128, "X8R8G8B8"), ("vehicletyres128", 64, 128, "X8R8G8B8"),
    ("vehiclesteering128", 128, 128, "A8R8G8B8"), ("vehiclespecdot64", 256, 256, "X8R8G8B8"),
    ("vehicleshatter128", 128, 128, "A8R8G8B8"), ("vehiclescratch64", 64, 64, "A8R8G8B8"),
    ("vehiclepoldecals128", 128, 128, "A8R8G8B8"), ("vehiclelightson128", 128, 128, "X8R8G8B8"),
    ("vehiclelights128", 128, 128, "X8R8G8B8"), ("vehiclegrunge256", 256, 256, "X8R8G8B8"),
    ("vehiclegeneric256", 256, 256, "X8R8G8B8"), ("vehicleenvmap128", 128, 128, "X8R8G8B8"),
    ("vehicledash32", 32, 32, "A8R8G8B8"), ("platecharset", 32, 256, "X8R8G8B8"),
    ("plateback3", 64, 32, "X8R8G8B8"), ("plateback2", 64, 32, "X8R8G8B8"),
    ("plateback1", 64, 32, "X8R8G8B8"), ("carplate", 16, 16, "X8R8G8B8"), ("carpback", 16, 16, "X8R8G8B8"),
]
_RASTER = {"DXT1": 0x200, "DXT3": 0x300, "A8R8G8B8": 0x500, "X8R8G8B8": 0x600}


class FakeIndexDB(IndexDB):
    """Complete in-memory implementation of the :class:`~satk.index.api.IndexDB` API.

    Args:
        profile: ``vanilla`` (default), ``installed`` or ``samp``.
        path: ignored (accepted for signature compatibility).
        root: directory the fake archive paths live in (default: a non-existent temp path).
        payloads: ``{sid: bytes}`` for blobs (``dff:``/``txd:``/``file:``) and mip0 pixels
            (``tex:``); requires ``root``, which then receives small synthetic archives.
    """

    def __init__(self, profile: str = "vanilla", path: Path | None = None, *,
                 root: Path | str | None = None, payloads: dict[str, bytes] | None = None):
        if profile not in FAKE_PROFILES:
            raise SatkError("BAD_PARAMS", f"unknown profile {profile!r}",
                            did_you_mean=difflib.get_close_matches(str(profile), FAKE_PROFILES, n=2, cutoff=0.4),
                            data={"profiles": list(FAKE_PROFILES)})
        if payloads and root is None:
            raise SatkError("BAD_PARAMS", "FakeIndexDB(payloads=...) needs a writable root directory")
        self.profile = profile
        self.root = Path(os.path.abspath(os.fspath(root))) if root is not None else _NO_ROOT
        self.path = Path(":memory:")
        self._lock = threading.RLock()
        self._mtime_ns = 0
        self._build_data()
        self._resolve()
        self._layout(payloads or {})
        if payloads:
            self._write_files(payloads)
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._fill_sqlite(self._db)

    def __repr__(self) -> str:
        return f"FakeIndexDB(profile={self.profile!r}, root={jpath(self.root)!r})"

    # ================================================================== data set

    def _build_data(self) -> None:
        samp = self.profile == "samp"
        self.layers: list[_Layer] = [_Layer(1, "vanilla", "vanilla", 0)]
        if samp:
            self.layers.append(_Layer(2, "samp", "samp", 10))
        lv = self.layers[0]
        ls = self.layers[1] if samp else None
        dat0, dat1 = ("data/default.two", "data/gta.two") if samp else ("data/default.dat", "data/gta.dat")
        o = 2 if samp else 0  # V8: SA-MP registers samp/*.img before gta3/gta_int

        self.sources: list[_Source] = []

        def src(layer, rel, kind, ref, order=None) -> _Source:
            s = _Source(len(self.sources) + 1, layer, rel, kind, ref, order)
            self.sources.append(s)
            return s

        gta3 = src(lv, "models/gta3.img", "img", "exe:InitImageList" if not samp else f"{dat0}:9", o + 0)
        gint = src(lv, "models/gta_int.img", "img", "exe:InitImageList" if not samp else f"{dat0}:10", o + 1)
        veh = src(lv, "models/generic/vehicle.txd", "txd", "exe:LoadVehicleTxd")
        s_vide = src(lv, "data/vehicles.ide", "ide", f"{dat0}:15")
        s_dide = src(lv, "data/default.ide", "ide", f"{dat0}:14")
        s_lide = src(lv, "data/maps/la/lae2.ide", "ide", f"{dat1}:38")
        s_lipl = src(lv, "data/maps/la/lae2.ipl", "ipl", f"{dat1}:120")
        s_simg = s_side = None
        if samp:
            s_simg = src(ls, "samp/samp.img", "img", f"{dat0}:8", 1)
            s_side = src(ls, "samp/samp.ide", "ide", f"{dat0}:16")

        self.blobs: list[_Blob] = []

        def blob(source, name, size, ns="main") -> _Blob:
            idx = sum(1 for b in self.blobs if b.source is source)
            b = _Blob(len(self.blobs) + 1, source, idx, name, size, ns)
            self.blobs.append(b)
            return b

        b_inf_dff = blob(gta3, "infernus.dff", 215040)
        b_inf_txd = blob(gta3, "infernus.txd", 10240)
        b_road_dff = blob(gta3, "lae2_roads89.dff", 12288)
        b_lod_dff = blob(gta3, "lodlae2_roads89.dff", 4096)
        b_road_txd = blob(gta3, "lae2roadshub.txd", 970752)
        b_lod_txd = blob(gta3, "laeast2_lod.txd", 133120)
        b_col = blob(gta3, "lae2_4.col", 139264)
        b_ipl = blob(gta3, "lae2_stream0.ipl", 16384)
        b_lw3 = blob(gta3, "lawest1.txd", 106496)
        b_bistro = blob(gta3, "bistro.txd", 1773568)
        b_lwi = blob(gint, "lawest1.txd", 10240)
        b_veh = blob(veh, "vehicle.txd", 1054720, ns="loose")
        b_lapdna_dff = b_lapdna_txd = None
        if samp:
            b_lapdna_dff = blob(s_simg, "lapdna.dff", 61440)
            b_lapdna_txd = blob(s_simg, "lapdna.txd", 133120)

        # ---- textures
        self.txds: list[_Txd] = []
        self.textures: list[_Tex] = []

        def txd(b, parent=None, via=None, texs=()) -> _Txd:
            t = _Txd(len(self.txds) + 1, b, parent, via)
            self.txds.append(t)
            for name, w, h, fmt, *rest in texs:
                raster = rest[0] if rest else _RASTER[fmt]
                alpha = fmt in ("DXT3", "A8R8G8B8") or (fmt == "DXT1" and raster == 0x100)
                x = _Tex(len(self.textures) + 1, t, len(t.textures), name, fmt, raster, w, h, alpha,
                         data_size=_mip0_size(fmt, w, h), hash=_pix(t.name, name))
                t.textures.append(x)
                self.textures.append(x)
            return t

        txd(b_inf_txd, "vehicle", "vehicle", [
            ("infernus92wheel32", 32, 32, "DXT1"), ("infernus92interior128", 128, 128, "DXT1"),
            ("infernus92handle32", 32, 16, "DXT3"),
        ])
        txd(b_veh, texs=_VEHICLE_TEX)
        txd(b_road_txd, texs=[
            ("plaintarmac1", 512, 512, "DXT1"), ("sidewgrass2", 256, 256, "DXT1"),
            ("sidewgrass3", 256, 256, "DXT1"), ("sidewgrass1", 256, 256, "DXT1"),
            ("dt_road_stoplinea", 512, 512, "DXT1"), ("dt_road", 512, 512, "DXT1"),
        ])
        txd(b_lod_txd)
        txd(b_lw3)
        txd(b_lwi)
        txd(b_bistro, texs=[("vent_64", 64, 64, "X8R8G8B8"), ("sw_wallbrick_01", 128, 128, "DXT1")])
        if samp:
            txd(b_lapdna_txd)

        # ---- models (DFF)
        self.dffs: list[_Dff] = []

        def dff(b, verts, tris, geoms, bmin, bmax, bs, flags, mats=()) -> _Dff:
            d = _Dff(len(self.dffs) + 1, b, verts, tris, geoms, bmin, bmax, bs, flags, list(mats))
            self.dffs.append(d)
            return d

        dff(b_inf_dff, 3573, 3072, 15, (-1.05, -2.41, -0.62), (1.05, 2.41, 0.74), (0.0, 0.0, 0.0, 2.6), 64 | 256 | 16, [
            ("carpback", 1), ("carplate", 1), ("infernus92handle32", 4), ("infernus92interior128", 6),
            ("infernus92wheel32", 1), ("vehicledash32", 1), ("vehiclegeneric256", 16), ("vehiclegrunge256", 12),
            ("vehiclelights128", 11), ("vehiclescratch64", 4), ("vehicleshatter128", 3), ("vehiclesteering128", 1),
            ("vehicletyres128", 1),
        ])
        dff(b_road_dff, 232, 225, 1, (-31.5, -24.0, -0.6), (31.5, 24.0, 0.4), (0.0, 0.0, -0.1, 39.4), 4 | 16, [
            ("dt_road_stoplinea", 1), ("plaintarmac1", 1), ("sidewgrass1", 1), ("sidewgrass2", 1), ("sidewgrass3", 1),
        ])
        dff(b_lod_dff, 64, 48, 1, (-62.0, -48.0, -1.0), (62.0, 48.0, 1.0), (0.0, 0.0, 0.0, 78.0), 4)
        if samp:
            dff(b_lapdna_dff, 1210, 1650, 1, (-0.6, -0.3, -1.0), (0.6, 0.3, 0.9), (0.0, 0.0, 0.0, 1.1), 1 | 2)

        # ---- collisions
        self.cols: list[_Col] = [
            _Col(1, b_col, 37, "lae2_roads89", 3, (-31.5, -24.0, -0.6), (31.5, 24.0, 0.4), "colfile"),
            _Col(2, b_inf_dff, 0, "infernus", 3, (-1.05, -2.41, -0.62), (1.05, 2.41, 0.74), "embedded"),
        ]

        # ---- definitions
        self.ides: list[_Ide] = [_Ide(i + 1, s) for i, s in enumerate([s_vide, s_dide, s_lide] + ([s_side] if samp else []))]
        i_veh, i_def, i_lae = self.ides[0], self.ides[1], self.ides[2]
        self.models: list[_Model] = [
            _Model(1, 411, lv, "infernus", "infernus", "cars", i_veh, 15, None, None,
                   {"type": "car", "handling": "infernus", "game_name": "INFERNUS", "class": "executive"}),
            _Model(2, 17613, lv, "lae2_roads89", "lae2roadshub", "objs", i_lae, 230, 150.0, 1, None),
            _Model(3, 17858, lv, "lodlae2_roads89", "laeast2_lod", "objs", i_lae, 475, 800.0, 128, None),
            _Model(4, 300, lv, "cutobj01", "generic", "hier", i_def, 41, None, None, None),
        ]
        if samp:
            self.models[3].active = False
            self.models.append(_Model(5, 300, ls, "lapdna", "lapdna", "peds", self.ides[3], 7, None, None,
                                      {"type": "cop", "behaviour": "STAT_COP", "anim_group": "man"}))

        # ---- world
        ipl_text = _Ipl(1, "lae2", "text", lv, s_lipl, None, None)
        ipl_bin = _Ipl(2, "lae2_stream0", "binary", lv, None, b_ipl, ipl_text)
        self.ipls: list[_Ipl] = [ipl_text, ipl_bin]
        pos = (2489.296875, -1668.5, 12.296875)
        lod = _Inst(1, ipl_text, 198, 17858, 0, 0, pos, (0.0, 0.0, 0.0, 1.0), -1, bbox_src="dff")
        hd = _Inst(2, ipl_bin, 4, 17613, 0, 0, pos, (0.0, 0.0, 0.0, 1.0), 198, bbox_src="col")
        self.insts_: list[_Inst] = [lod, hd]

    # ================================================================== derived links

    def _resolve(self) -> None:
        # blobs: first registered archive wins in (ns, stem, ext)
        def order(b: _Blob) -> tuple:
            lo = b.source.load_order
            return (0 if lo is not None else 1, lo if lo is not None else 0, b.source.id, b.idx)

        winners: dict[tuple[str, str, str], _Blob] = {}
        for b in sorted(self.blobs, key=order):
            key = ("main" if b.ns == "loose" else b.ns, b.stem, b.ext)
            w = winners.get(key)
            if w is None:
                winners[key] = b
                b.active, b.shadowed_by = True, None
            else:
                b.active, b.shadowed_by = False, w
        self._blob_by_key = winners
        self._blob_by_file = {b.file_key: b for b in self.blobs}
        self._source_by_rel = {s.relpath: s for s in self.sources}
        self._txd_by_blob = {t.blob.id: t for t in self.txds}
        self._dff_by_blob = {d.blob.id: d for d in self.dffs}
        self._col_by_name = {c.name: c for c in self.cols}
        self._model_active = {m.id: m for m in self.models if m.active}
        self._ipl_by_name = {p.name: p for p in self.ipls}
        self._inst_by_key = {(i.ipl.name, i.idx): i for i in self.insts_}
        self._tex_by_hash: dict[str, list[_Tex]] = {}
        for x in self.textures:
            self._tex_by_hash.setdefault(x.hash, []).append(x)

        # LOD links: lod_idx indexes the inst array of the parent text IPL
        for i in self.insts_:
            if i.lod_idx >= 0:
                parent = i.ipl.parent or i.ipl
                i.lod = self._inst_by_key.get((parent.name, i.lod_idx))
                if i.lod is not None:
                    i.lod.is_lod = True
        # world AABBs
        for i in self.insts_:
            m = self._model_active.get(i.model_id)
            c = self._col_by_name.get(m.name) if m else None
            d = self._dff_of(m) if m else None
            if i.bbox_src == "col" and c is not None:
                i.aabb = world_aabb(c.bmin, c.bmax, i.pos, i.q)
            elif d is not None:
                bx, by, bz, br = d.bs
                cx, cy, cz = (_api.rotate(world_quat(i.q), (bx, by, bz)))
                i.bbox_src = "dff"
                i.aabb = (i.pos[0] + cx - br, i.pos[1] + cy - br, i.pos[2] + cz - br,
                          i.pos[0] + cx + br, i.pos[1] + cy + br, i.pos[2] + cz + br)
            else:
                i.bbox_src = "point"
                i.aabb = (*i.pos, *i.pos)

        # model_tex / model_link for active models
        self._model_tex: dict[int, list[tuple[str, _Tex | None, str, int]]] = {}
        for m in self._model_active.values():
            chain = self._txd_chain(m)
            rows = []
            d = self._dff_of(m)
            for tex_name, uses in sorted(d.mats if d else [], key=lambda t: t[0]):
                found, via = None, "missing"
                for t, how in chain:
                    found = next((x for x in t.textures if x.name == tex_name), None)
                    if found is not None:
                        via = how
                        break
                rows.append((tex_name, found, via, uses))
            self._model_tex[m.id] = rows

    def _dff_of(self, m: _Model) -> _Dff | None:
        b = self._blob_by_key.get(("main", m.name, "dff"))
        return self._dff_by_blob.get(b.id) if b else None

    def _active_txd(self, name: str | None) -> _Txd | None:
        if not name:
            return None
        b = self._blob_by_key.get(("main", name.lower(), "txd"))
        return self._txd_by_blob.get(b.id) if b else None

    def _txd_chain(self, m: _Model) -> list[tuple[_Txd, str]]:
        out: list[tuple[_Txd, str]] = []
        t = self._active_txd(m.txd)
        how = "own"
        seen: set[int] = set()
        while t is not None and t.id not in seen:
            seen.add(t.id)
            out.append((t, how))
            how = t.parent_via or "txdp"
            t = self._active_txd(t.parent)
        if m.sec == "cars":
            v = self._active_txd("vehicle")
            if v is not None and v.id not in seen:
                out.append((v, "vehicle"))
        return out

    def _col_of(self, m: _Model) -> _Col | None:
        return self._col_by_name.get(m.name)

    # ================================================================== layout & files

    def _layout(self, payloads: dict[str, bytes]) -> None:
        blob_pl: dict[int, bytes] = {}
        tex_pl: dict[int, bytes] = {}
        for sid, data in payloads.items():
            s = Sid.parse(sid)
            if s.kind == "tex":
                tex_pl[self._tex(s).id] = bytes(data)
            elif s.kind in ("dff", "txd", "file"):
                blob_pl[self._blob(s).id] = bytes(data)
            else:
                raise SatkError("BAD_PARAMS", f"payloads accept dff:/txd:/file:/tex: SIDs, got {sid!r}")
        self._blob_payloads, self._tex_payloads = blob_pl, tex_pl
        for x in self.textures:
            if x.id in tex_pl:
                x.data_size = len(tex_pl[x.id])
        for b in self.blobs:
            if b.id in blob_pl:
                b.size = len(blob_pl[b.id])
            t = self._txd_by_blob.get(b.id)
            if t is not None and b.id not in blob_pl:
                need = 0x100 + sum(x.data_size + 0x100 for x in t.textures)
                b.size = max(b.size, need)
        for s in self.sources:
            if s.kind == "img":
                off = _SECTOR * 4  # room for a fake directory
                for b in (b for b in self.blobs if b.source is s):
                    b.off = off
                    off += -(-b.size // _SECTOR) * _SECTOR
                s.size = off
            else:
                for b in (b for b in self.blobs if b.source is s):
                    b.off = 0
                    s.size = b.size
                if not any(b.source is s for b in self.blobs):
                    s.size = 4096
        for t in self.txds:
            off = t.blob.off + 0x100
            for x in t.textures:
                x.data_off = off
                off += x.data_size + 0x100

    def _write_files(self, payloads: dict[str, bytes]) -> None:
        for s in self.sources:
            blobs = [b for b in self.blobs if b.source is s]
            pls = [b for b in blobs if b.id in self._blob_payloads]
            texs = [x for t in self.txds if t.blob.source is s for x in t.textures if x.id in self._tex_payloads]
            if not pls and not texs:
                continue
            p = ensure_writable(self.root / s.relpath)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "wb") as f:
                f.truncate(s.size)
                for b in pls:
                    f.seek(b.off)
                    f.write(self._blob_payloads[b.id])
                for x in texs:
                    f.seek(x.data_off)
                    f.write(self._tex_payloads[x.id])

    def _path(self, s: _Source) -> Path:
        return self.root / Path(*s.relpath.split("/"))

    # ================================================================== lookups

    def _not_found(self, s: Sid, names: Sequence[str]) -> SatkError:
        close = difflib.get_close_matches(s.key, list(names), n=3, cutoff=0.6)
        return SatkError("NOT_FOUND", f"no {s.kind} {s.key!r}" + (f"@{s.layer}" if s.layer else ""),
                         hint=f"satk asset find {s.key.split('/')[-1].split('#')[0]} --kind {s.kind}",
                         did_you_mean=[f"{s.kind}:{c}" for c in close])

    def _model(self, s: Sid) -> _Model:
        key = s.key
        if s.num is not None:
            cands = [m for m in self.models if m.id == s.num]
        else:
            cands = [m for m in self.models if m.name == key]
        if s.layer:
            cands = [m for m in cands if m.layer.name == s.layer]
        else:
            cands = [m for m in cands if m.active]
        if not cands:
            names = [str(m.id) for m in self.models] + [m.name for m in self.models]
            raise self._not_found(s, names)
        return cands[0]

    def _blob(self, s: Sid) -> _Blob:
        if s.kind == "file":
            b = self._blob_by_file.get(s.key)
            if b is None:
                raise self._not_found(s, list(self._blob_by_file))
            return b
        ext = s.kind  # dff / txd
        if s.layer:
            same = [b for b in self.blobs if b.stem == s.key and b.ext == ext and b.source.layer.name == s.layer]
            same.sort(key=lambda b: b.active)  # prefer the non-winning version
            if same:
                return same[0]
        else:
            b = self._blob_by_key.get(("main", s.key, ext))
            if b is not None:
                return b
        raise self._not_found(s, [b.stem for b in self.blobs if b.ext == ext])

    def _txd(self, s: Sid) -> _Txd:
        b = self._blob(s if s.kind in ("txd", "file") else Sid("txd", s.key, s.layer))
        t = self._txd_by_blob.get(b.id)
        if t is None:
            raise SatkError("NOT_FOUND", f"{s} is not a TXD")
        return t

    def _tex(self, s: Sid) -> _Tex:
        txd_name, _, tex_name = s.key.partition("/")
        try:
            t = self._txd(Sid("txd", txd_name, s.layer))
        except SatkError:
            raise self._not_found(s, [self._tex_key(x) for x in self.textures]) from None
        x = next((x for x in t.textures if x.name == tex_name), None)
        if x is None:
            raise self._not_found(s, [self._tex_key(x) for x in t.textures])
        return x

    def _inst(self, s: Sid) -> _Inst:
        ipl, _, idx = s.key.partition("#")
        i = self._inst_by_key.get((ipl, int(idx)))
        if i is None:
            raise self._not_found(s, [f"{i.ipl.name}#{i.idx}" for i in self.insts_])
        return i

    # ================================================================== SID formatting

    def _layer_suffix(self, layer: _Layer, active: bool) -> str:
        return "" if active else f"@{layer.name}"

    def _model_sid(self, m: _Model) -> str:
        return f"model:{m.id}" + self._layer_suffix(m.layer, m.active)

    def _blob_sid(self, b: _Blob) -> str:
        if b.active and b.ext in ("dff", "txd"):
            return f"{b.ext}:{b.stem}"
        return f"file:{b.file_key}"

    @staticmethod
    def _tex_key(x: _Tex) -> str:
        return f"{x.txd.name}/{x.name}"

    @staticmethod
    def _variant(b: _Blob) -> str | None:
        """SID suffix of a blob version: '' (winner), '@layer' (loser from another layer) or None
        (loser from the winner's own layer: only ``file:`` can address it)."""
        if b.active:
            return ""
        w = b.shadowed_by
        if w is not None and w.source.layer is b.source.layer:
            return None
        return f"@{b.source.layer.name}"

    def _tex_sid(self, x: _Tex) -> str:
        v = self._variant(x.txd.blob)
        return f"tex:{self._tex_key(x)}" + (v if v is not None else f"@{x.txd.blob.source.layer.name}")

    def _txd_sid(self, t: _Txd) -> str:
        v = self._variant(t.blob)
        return f"txd:{t.name}{v}" if v is not None else f"file:{t.blob.file_key}"

    def _blob_ref(self, b: _Blob, sid: str | None = None) -> BlobRef:
        return BlobRef(sid or self._blob_sid(b), self._path(b.source), b.off, b.size, b.name)

    def _tex_ref(self, x: _Tex) -> TexRef:
        return TexRef(self._tex_sid(x), f"pix:{x.hash}", self._path(x.txd.blob.source), x.data_off, x.data_size,
                      x.pal_off, x.d3dfmt, x.raster_fmt, x.platform, x.w, x.h, x.levels, bool(x.alpha))

    def _inst_row(self, i: _Inst) -> InstRow:
        m = self._model_active.get(i.model_id)
        return InstRow(i.sid, i.model_id, m.name if m else "", i.pos, i.q, i.area, i.iflags,
                       i.lod.sid if i.lod else None, i.is_lod, i.aabb)

    def _inst_cells(self, i: _Inst) -> list:
        m = self._model_active.get(i.model_id)
        rz = world_rz(i.q)
        return [i.sid, f"model:{i.model_id}", m.name if m else None, env.round_pos(i.pos),
                None if rz is None else env.round_angle(rz), i.area, i.is_lod]

    # ================================================================== get

    def get(self, sid: str, fields: list[str] | None = None) -> dict:
        s = as_sid(sid)
        fn = getattr(self, f"_get_{s.kind}", None)
        if fn is None:
            raise SatkError("NOT_FOUND", f"the fake index has no {s.kind} objects", data={"kind": s.kind})
        return env.select_fields(fn(s), fields)

    def _get_model(self, s: Sid) -> dict:
        m = self._model(s)
        links: dict[str, Any] = {"ide": f"ide:{m.ide.source.relpath}"}
        out: dict[str, Any] = {}
        if m.active:
            d = self._dff_of(m)
            c = self._col_of(m)
            rows = self._model_tex.get(m.id, [])
            links.update(
                dff=self._blob_sid(d.blob) if d else None,
                txd_chain=[self._txd_sid(t) for t, _ in self._txd_chain(m)],
                col=f"col:{c.name}" if c else None, col_via=c.via if c else None,
                overrides=[self._model_sid(o) for o in self.models if o.id == m.id and o is not m],
            )
            out["n_inst"] = sum(1 for i in self.insts_ if i.model_id == m.id)
            out["tex"] = {"total": len(rows), "missing": sum(1 for r in rows if r[1] is None)}
            if d is not None:
                out["geo"] = {"verts": d.verts, "tris": d.tris, "bs": env.round_pos(d.bs)}
        else:
            out["active"] = False
            links["overridden_by"] = f"model:{m.id}"
        return env.obj(self._model_sid(m), name=m.name, sec=m.sec, txd=m.txd, layer=m.layer.name,
                       draw=m.draw, flags=m.flags, extra=m.extra, line=m.line, **out, links=links)

    def _blob_obj(self, b: _Blob, sid: str, **extra) -> dict:
        return env.obj(sid, name=b.name, file=f"file:{b.file_key}", layer=b.source.layer.name,
                       active=b.active, shadowed_by=f"file:{b.shadowed_by.file_key}" if b.shadowed_by else None,
                       ns=b.ns, size=b.size, path=jpath(self._path(b.source)), offset=b.off, **extra)

    def _get_dff(self, s: Sid) -> dict:
        b = self._blob(s)
        d = self._dff_by_blob[b.id]
        users = [f"model:{m.id}" for m in self._model_active.values() if self._dff_of(m) is d]
        return self._blob_obj(b, self._blob_sid(b) if not s.layer else str(s), verts=d.verts, tris=d.tris,
                              geoms=d.geoms, materials=len(d.mats), flags=d.flags,
                              bbox=env.round_pos(d.bmin) + env.round_pos(d.bmax),
                              bsphere=env.round_pos(d.bs), models=users)

    def _get_txd(self, s: Sid) -> dict:
        t = self._txd(s)
        return self._blob_obj(t.blob, self._txd_sid(t), tex_count=len(t.textures),
                              parent=f"txd:{t.parent}" if t.parent else None, parent_via=t.parent_via)

    def _get_tex(self, s: Sid) -> dict:
        x = self._tex(s)
        return env.obj(self._tex_sid(x), name=x.name, txd=self._txd_sid(x.txd), w=x.w, h=x.h, d3dfmt=x.d3dfmt,
                       raster_fmt=x.raster_fmt, platform=x.platform, levels=x.levels, alpha=bool(x.alpha),
                       pix=f"pix:{x.hash}", active=x.txd.blob.active)

    def _get_pix(self, s: Sid) -> dict:
        xs = self._tex_by_hash.get(s.key)
        if not xs:
            raise self._not_found(s, list(self._tex_by_hash))
        x = xs[0]
        return env.obj(str(s), w=x.w, h=x.h, d3dfmt=x.d3dfmt, nbytes=x.data_size,
                       textures=[self._tex_sid(t) for t in xs])

    def _get_file(self, s: Sid) -> dict:
        src = self._source_by_rel.get(s.key)
        if src is not None:
            n = sum(1 for b in self.blobs if b.source is src)
            if not (src.kind != "img" and n == 1):
                return env.obj(f"file:{src.relpath}", kind=src.kind, layer=src.layer.name, size=src.size,
                               path=jpath(self._path(src)), load_ref=src.load_ref, load_order=src.load_order,
                               entries=n if src.kind == "img" else None)
        b = self._blob(s)
        parsed = None
        if b.id in self._txd_by_blob:
            parsed = self._txd_sid(self._txd_by_blob[b.id])
        elif b.id in self._dff_by_blob:
            parsed = self._blob_sid(b) if b.active else None
        elif b.ext == "ipl":
            parsed = next((f"ipl:{p.name}" for p in self.ipls if p.blob is b), None)
        sid = f"file:{b.file_key}"
        return self._blob_obj(b, sid, parsed=None if parsed == sid else parsed, rw_size=b.size)

    def _get_col(self, s: Sid) -> dict:
        c = self._col_by_name.get(s.key)
        if c is None:
            raise self._not_found(s, list(self._col_by_name))
        return env.obj(f"col:{c.name}", name=c.name, version=c.version, via=c.via, idx=c.idx,
                       file=f"file:{c.blob.file_key}", bbox=env.round_pos(c.bmin) + env.round_pos(c.bmax), active=True,
                       models=[f"model:{m.id}" for m in self._model_active.values() if m.name == c.name])

    def _get_ide(self, s: Sid) -> dict:
        ide = next((i for i in self.ides if i.source.relpath == s.key), None)
        if ide is None:
            raise self._not_found(s, [i.source.relpath for i in self.ides])
        return env.obj(f"ide:{s.key}", layer=ide.source.layer.name, load_ref=ide.source.load_ref,
                       models=sum(1 for m in self.models if m.ide is ide))

    def _get_ipl(self, s: Sid) -> dict:
        p = self._ipl_by_name.get(s.key)
        if p is None:
            raise self._not_found(s, list(self._ipl_by_name))
        f = f"file:{p.source.relpath}" if p.source else f"file:{p.blob.file_key}"
        return env.obj(f"ipl:{p.name}", kind=p.kind, file=f, parent=f"ipl:{p.parent.name}" if p.parent else None,
                       loaded=p.loaded, layer=p.layer.name, n_inst=sum(1 for i in self.insts_ if i.ipl is p))

    def _get_inst(self, s: Sid) -> dict:
        i = self._inst(s)
        m = self._model_active.get(i.model_id)
        rz = world_rz(i.q)
        rot: dict[str, Any] = {"rz": env.round_angle(rz)} if rz is not None else {"q": env.round_quat(world_quat(i.q))}
        return env.obj(i.sid, model=f"model:{i.model_id}", name=m.name if m else None, pos=env.round_pos(i.pos),
                       **rot, area=i.area, iflags=i.iflags, lod=i.lod.sid if i.lod else None, is_lod=i.is_lod,
                       ipl=f"ipl:{i.ipl.name}", layer=i.ipl.layer.name, aabb=env.round_pos(i.aabb),
                       bbox_src=i.bbox_src)

    # ================================================================== find

    def _search_entries(self) -> list[tuple[str, str, str, str]]:
        """(kind, sid, name, info) for everything searchable (info as in SPEC §4.7: ``"objs lae2.ide"``)."""
        out: list[tuple[str, str, str, str]] = []
        for m in self.models:
            out.append(("model", self._model_sid(m), m.name, f"{m.sec} {m.ide.source.relpath.rsplit('/', 1)[-1]}"))
        for d in self.dffs:
            out.append(("dff", self._blob_sid(d.blob) if d.blob.active else f"dff:{d.name}@{d.blob.source.layer.name}",
                        d.name, f"{d.tris} tris"))
        for t in self.txds:
            out.append(("txd", self._txd_sid(t), t.name, f"{len(t.textures)} tex"))
        for x in self.textures:
            out.append(("tex", self._tex_sid(x), x.name, f"{x.d3dfmt} {x.w}x{x.h}"))
        for c in self.cols:
            out.append(("col", f"col:{c.name}", c.name, f"COL{c.version} {c.via}"))
        for p in self.ipls:
            out.append(("ipl", f"ipl:{p.name}", p.name, p.kind))
        return out

    def find(self, q: str, kind: str | None = None, limit: int = 20, cursor: str | None = None) -> dict:
        """Exact names first: if some names equal ``q`` only they are returned (``warn`` counts the
        others that contain it); ``*``/``?`` wildcards search by pattern (``'*tarmac*'``)."""
        import fnmatch

        if kind is not None:
            check_choice("kind", kind, SEARCH_KINDS)
        lim = env.clamp_limit(limit)
        needle = str(q or "").strip().lower()
        if not needle:
            raise SatkError("BAD_PARAMS", "empty search string")
        order = {k: n for n, k in enumerate(SEARCH_KINDS)}
        entries = self._search_entries() + [
            ("file", f"file:{b.file_key}", b.name, f"{b.size} B {b.source.layer.name}" + ("" if b.active else " shadowed"))
            for b in self.blobs]
        hits = []
        warn: list[str] = []
        pattern = "*" in needle or "?" in needle
        for k, sid, name, info in entries:
            if (kind is None and k == "file") or (kind is not None and k != kind):
                continue
            n = name.lower()
            if k == "file":
                ok = needle.strip("*") in sid.lower()
                rank = 0 if n == needle.strip("*") else 1
            elif pattern:
                ok, rank = fnmatch.fnmatchcase(n, needle), 0
            elif n == needle:
                ok, rank = True, 0
            elif n.startswith(needle):
                ok, rank = True, 1
            else:
                ok, rank = (len(needle) >= 3 and needle in n), 2
            if ok:
                hits.append((rank, order[k], n, sid.startswith("file:") or "@" in sid, sid, [sid, k, name, info]))
        if not pattern and kind != "file" and any(h[0] == 0 for h in hits):
            more = sum(1 for h in hits if h[0] != 0)
            hits = [h for h in hits if h[0] == 0]
            if more:
                warn.append(f"MORE: {more} other names contain {q!r}: asset find '*{q}*'")
        hits.sort(key=lambda h: h[:5])
        rows, total, nxt = page([h[5] for h in hits], lim, cursor)
        return env.table(FIND_COLS, rows, total=total, next=nxt, warn=warn)

    # ================================================================== refs

    def refs(self, sid: str, rel: str | None = None, limit: int = 50, cursor: str | None = None) -> dict:
        s = as_sid(sid)
        rels = REFS_RELS.get(s.kind, ())
        lim = env.clamp_limit(limit, default=50)
        if rel is None:  # SPEC §4.7: counts per relation, then fetch only what is needed
            counts = {r: len(self._refs(s, r)[1]) for r in rels}
            return env.obj(str(s) if s.kind != "model" else self._model_sid(self._model(s)), rels=counts)
        if rel not in rels:
            raise SatkError("BAD_PARAMS", f"rel for {s.kind} must be one of {', '.join(rels)}, got {rel!r}",
                            data={"rels": list(rels)})
        cols, rows = self._refs(s, rel)
        chunk, total, nxt = page(rows, lim, cursor)
        return env.table(cols, chunk, total=total, next=nxt)

    def _generic(self, items: list[tuple[str, str, str]]) -> tuple[list[str], list[list]]:
        return GENERIC_COLS, [[a, b, c] for a, b, c in items]

    def _refs(self, s: Sid, rel: str) -> tuple[list[str], list[list]]:  # noqa: C901 - one branch per relation
        k = s.kind
        if k == "model":
            m = self._model(s)
            if rel == "inst":
                return INST_COLS, [self._inst_cells(i) for i in self.insts_ if i.model_id == m.id and m.active]
            if rel == "tex":
                rows = [[self._tex_sid(x) if x else None, name, via, uses, f"pix:{x.hash}" if x else None]
                        for name, x, via, uses in self._model_tex.get(m.id, [])] if m.active else []
                return MODEL_TEX_COLS, rows
            if rel == "dff":
                d = self._dff_of(m) if m.active else None
                return self._generic([(self._blob_sid(d.blob), d.blob.name, f"{d.tris} tris")] if d else [])
            if rel == "txd":
                return self._generic([(self._txd_sid(t), t.name, how) for t, how in self._txd_chain(m)] if m.active else [])
            if rel == "col":
                c = self._col_of(m) if m.active else None
                return self._generic([(f"col:{c.name}", c.name, c.via)] if c else [])
            if rel == "ide":
                return self._generic([(f"ide:{m.ide.source.relpath}", m.ide.source.relpath, f"line {m.line}")])
            if rel == "overrides":
                return self._generic([(self._model_sid(o), o.name, o.layer.name)
                                      for o in self.models if o.id == m.id and o is not m])
        if k == "tex":
            x = self._tex(s)
            if rel == "models":
                ms = [mid for mid, rows in self._model_tex.items() if any(r[1] is x for r in rows)]
                return self._generic([(f"model:{mid}", self._model_active[mid].name, self._model_active[mid].sec)
                                      for mid in sorted(ms)])
            if rel == "same_pixels":
                return self._generic([(self._tex_sid(t), t.name, self._txd_sid(t.txd))
                                      for t in self._tex_by_hash.get(x.hash, []) if t is not x])
            if rel == "txd":
                return self._generic([(self._txd_sid(x.txd), x.txd.name, "")])
        if k == "pix":
            xs = self._tex_by_hash.get(s.key, [])
            return self._generic([(self._tex_sid(t), t.name, f"{t.w}x{t.h} {t.d3dfmt}") for t in xs])
        if k in ("txd", "dff", "col") and rel == "file":
            b = self._col_by_name[s.key].blob if k == "col" and s.key in self._col_by_name else self._blob(s)
            return self._generic([(f"file:{b.file_key}", b.name, b.source.layer.name)])
        if k == "txd":
            t = self._txd(s)
            if rel == "textures":
                return self._generic([(self._tex_sid(x), x.name, f"{x.w}x{x.h} {x.d3dfmt}") for x in t.textures])
            if rel == "models":
                return self._generic([(f"model:{m.id}", m.name, m.sec) for m in self._model_active.values()
                                      if m.txd and m.txd.lower() == t.name])
            if rel == "parent":
                p = self._active_txd(t.parent)
                return self._generic([(self._txd_sid(p), p.name, t.parent_via or "")] if p else [])
            if rel == "children":
                return self._generic([(self._txd_sid(c), c.name, c.parent_via or "")
                                      for c in self.txds if c.parent == t.name and c.blob.active])
        if k == "dff" and rel == "models":
            b = self._blob(s)
            return self._generic([(f"model:{m.id}", m.name, m.sec) for m in self._model_active.values()
                                  if (d := self._dff_of(m)) is not None and d.blob is b])
        if k == "col" and rel == "models":
            return self._generic([(f"model:{m.id}", m.name, m.sec) for m in self._model_active.values()
                                  if m.name == s.key])
        if k == "inst":
            i = self._inst(s)
            if rel == "model":
                m = self._model_active.get(i.model_id)
                return self._generic([(f"model:{i.model_id}", m.name if m else "", m.sec if m else "")])
            if rel == "lod":
                return INST_COLS, [self._inst_cells(i.lod)] if i.lod else []
            if rel == "hd_children":
                return INST_COLS, [self._inst_cells(c) for c in self.insts_ if c.lod is i]
            if rel == "ipl":
                return self._generic([(f"ipl:{i.ipl.name}", i.ipl.name, i.ipl.kind)])
            if rel == "near":
                cx = (i.aabb[0] + i.aabb[3]) / 2
                cy = (i.aabb[1] + i.aabb[4]) / 2
                near = self._near_insts(cx, cy, None, 50.0, None, "aabb", None, "all")
                return INST_COLS, [self._inst_cells(j) for _, j in near if j is not i]
        if k == "file":
            src = self._source_by_rel.get(s.key)
            if src is not None and src.kind == "img":
                return self._generic([])  # an archive: no parsed object, never shadowed as a whole
            b = self._blob(s)
            if rel == "parsed":
                obj = self._get_file(s)
                return self._generic([(obj["parsed"], b.stem, b.ext)] if obj.get("parsed") else [])
            if rel == "shadowed_by":
                w = b.shadowed_by
                return self._generic([(f"file:{w.file_key}", w.name, w.source.layer.name)] if w else [])
            if rel == "shadows":
                return self._generic([(f"file:{o.file_key}", o.name, o.source.layer.name)
                                      for o in self.blobs if o.shadowed_by is b])
        if k == "ipl" and rel == "inst":
            p = self._ipl_by_name.get(s.key)
            if p is None:
                raise self._not_found(s, list(self._ipl_by_name))
            return INST_COLS, [self._inst_cells(i) for i in self.insts_ if i.ipl is p]
        if k == "ide" and rel == "models":
            return self._generic([(self._model_sid(m), m.name, m.sec) for m in self.models
                                  if m.ide.source.relpath == s.key])
        if k in ("item", "zone", "ifp", "anim"):
            raise SatkError("NOT_FOUND", f"the fake index has no {k} objects")
        return self._generic([])  # pragma: no cover - every (kind, rel) of REFS_RELS is handled above

    # ================================================================== spatial

    def _near_insts(self, x: float | None, y: float | None, z: float | None, r: float | None, box, match: str,
                    area: int | None, lod: str) -> list[tuple[float, _Inst]]:
        out: list[tuple[float, _Inst]] = []
        if box is not None:
            bx0, by0, bx1, by1 = (float(v) for v in box)
            bx0, bx1 = min(bx0, bx1), max(bx0, bx1)
            by0, by1 = min(by0, by1), max(by0, by1)
            if x is None or y is None:
                x, y = (bx0 + bx1) / 2, (by0 + by1) / 2
        for i in self.insts_:
            if area is not None and i.area != area:
                continue
            if lod == "hd" and i.is_lod or lod == "lod" and not i.is_lod:
                continue
            px, py, pz = i.pos
            a = i.aabb
            if box is not None:
                if match == "center":
                    ok = bx0 <= px <= bx1 and by0 <= py <= by1
                else:
                    ok = a[0] <= bx1 and a[3] >= bx0 and a[1] <= by1 and a[4] >= by0
            else:
                if match == "center":
                    d = math.dist((px, py) if z is None else (px, py, pz), (x, y) if z is None else (x, y, z))
                else:
                    dx = max(a[0] - x, 0.0, x - a[3])
                    dy = max(a[1] - y, 0.0, y - a[4])
                    dz = 0.0 if z is None else max(a[2] - z, 0.0, z - a[5])
                    d = math.sqrt(dx * dx + dy * dy + dz * dz)
                ok = d <= float(r)
            if ok:
                dist = math.dist((px, py) if z is None else (px, py, pz), (x, y) if z is None else (x, y, z))
                out.append((dist, i))
        out.sort(key=lambda t: (t[0], t[1].sid))
        return out

    def near(self, x: float, y: float, z: float | None = None, r: float = 50.0,
             box: tuple[float, float, float, float] | None = None, match: str = "aabb",
             kinds: tuple[str, ...] = ("inst",), area: int | None = 0, lod: str = "hd", limit: int = 50) -> dict:
        check_choice("match", match, MATCH_MODES)
        check_choice("lod", lod, LOD_MODES)
        for k in kinds:
            check_choice("kinds", k, NEAR_KINDS)
        if box is not None and len(tuple(box)) != 4:
            raise SatkError("BAD_PARAMS", "box must be (minx, miny, maxx, maxy)")
        if box is None and (r is None or float(r) < 0):
            raise SatkError("BAD_PARAMS", "r must be >= 0")
        lim = env.clamp_limit(limit, default=50)
        hits = self._near_insts(x, y, z, r, box, match, area, lod) if "inst" in kinds else []
        rows = [self._inst_cells(i) + [env.round_pos(d)] for d, i in hits]
        return env.table(NEAR_COLS, rows[:lim], total=len(rows))

    def insts(self, *, model: int | None = None, box=None, center=None, r=None, area: int | None = 0,
              lod: str = "hd", match: str = "aabb", limit: int | None = None) -> list[InstRow]:
        check_choice("match", match, MATCH_MODES)
        check_choice("lod", lod, LOD_MODES)
        if box is not None or center is not None:
            cx = cy = cz = None
            if center is not None:
                c = tuple(float(v) for v in center)
                cx, cy = c[0], c[1]
                cz = c[2] if len(c) > 2 else None
            hits = [i for _, i in self._near_insts(cx, cy, cz, 50.0 if r is None else r, box, match, area, lod)]
        else:
            hits = [i for i in self.insts_ if (area is None or i.area == area)
                    and not (lod == "hd" and i.is_lod or lod == "lod" and not i.is_lod)]
            hits.sort(key=lambda i: i.sid)
        if model is not None:
            hits = [i for i in hits if i.model_id == int(model)]
        if limit is not None:
            hits = hits[: int(limit)]
        return [self._inst_row(i) for i in hits]

    def match_runtime(self, model_id: int, pos, tol: float = 0.05) -> list[str]:
        p = tuple(float(v) for v in pos)
        if len(p) != 3:
            raise SatkError("BAD_PARAMS", "pos must be (x, y, z)")
        hits = sorted(((math.dist(i.pos, p), i.sid) for i in self.insts_
                       if i.model_id == int(model_id) and math.dist(i.pos, p) <= float(tol)))
        return [sid for _, sid in hits]

    # ================================================================== typed lookups

    def model_files(self, model: int | str) -> ModelFiles:
        if isinstance(model, int):
            s = Sid("model", str(model))
        else:
            text = str(model)
            s = Sid.parse(text) if ":" in text else Sid("model", text)
            s = as_sid(s, ("model",))
        m = self._model(s)
        d = self._dff_of(m) if m.active else None
        c = self._col_of(m) if m.active else None
        col = None
        if c is not None:
            col = ColRef(f"col:{c.name}", self._blob_ref(c.blob, f"file:{c.blob.file_key}"), c.idx, c.name, c.via)
        chain = [self._blob_ref(t.blob) for t, _ in self._txd_chain(m)] if m.active else []
        return ModelFiles(m.id, m.name, m.sec, self._blob_ref(d.blob) if d else None, chain, col)

    def texture_ref(self, sid: str) -> TexRef:
        s = as_sid(sid, ("tex", "pix"))
        if s.kind == "pix":
            xs = self._tex_by_hash.get(s.key)
            if not xs:
                raise self._not_found(s, list(self._tex_by_hash))
            xs = sorted(xs, key=lambda t: (not t.txd.blob.active, t.id))
            return self._tex_ref(xs[0])
        return self._tex_ref(self._tex(s))

    def textures_of(self, sid: str) -> list[TexRef]:
        s = as_sid(sid, ("txd", "model"))
        if s.kind == "txd":
            return [self._tex_ref(x) for x in self._txd(s).textures]
        m = self._model(s)
        return [self._tex_ref(x) for _, x, _, _ in self._model_tex.get(m.id, []) if x is not None]

    def blob_ref(self, sid: str) -> BlobRef:
        s = as_sid(sid, ("dff", "txd", "file"))
        b = self._blob(s)
        return self._blob_ref(b, f"file:{b.file_key}" if s.kind == "file" else (str(s) if s.layer else None))

    def read_blob(self, ref: BlobRef) -> bytes:
        return read_blob_bytes(ref)

    # ================================================================== SQL

    def meta(self) -> dict[str, str]:
        return dict(self._meta_rows())

    def query(self, sql: str, params: list = (), limit: int = 200) -> dict:
        return run_readonly_query(self._db, sql, params, limit, lock=self._lock)

    def close(self) -> None:
        pass  # in-memory; nothing to release (kept usable for cached reuse)

    def to_sqlite(self, path: Path | str) -> Path:
        """Write the fake index as a real schema-v1 SQLite file (atomic replace)."""
        p = ensure_writable(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        if tmp.exists():
            tmp.unlink()
        dst = sqlite3.connect(tmp)
        try:
            with self._lock:
                self._db.backup(dst)
        finally:
            dst.close()
        os.replace(tmp, p)
        return p

    def _meta_rows(self) -> list[tuple[str, str]]:
        order = sorted((s for s in self.sources if s.kind == "img" and s.load_order is not None),
                       key=lambda s: s.load_order)
        return [
            ("assumptions", json.dumps(["fake data set (satk.index.fake)"])),
            ("build_seconds", "0"),
            ("built_at", "2026-10-04T00:00:00Z"),
            ("content_hash", hashlib.sha256(f"satk-fake:{self.profile}".encode()).hexdigest()),
            ("dat_files", json.dumps(["data/default.two", "data/gta.two"] if self.profile == "samp"
                                     else ["data/default.dat", "data/gta.dat"])),
            ("game_exe_sha256", "a559aa772fd136379155efa71f00c47aad34bbfeae6196b0fe1047d0645cbd26"),
            ("img_order", json.dumps([s.relpath for s in order])),
            ("profile", self.profile),
            ("root", jpath(self.root)),
            ("satk_version", "0.1.0"),
            ("schema_version", str(SCHEMA_VERSION)),
        ]

    def _fill_sqlite(self, c: sqlite3.Connection) -> None:  # noqa: C901 - straight inserts
        create_schema(c)
        ins = c.execute
        for k, v in self._meta_rows():
            ins("INSERT INTO meta VALUES (?,?)", (k, v))
        for la in self.layers:
            ins("INSERT INTO layer VALUES (?,?,?,?,?)", (la.id, la.name, la.kind, la.priority, jpath(self.root)))
        for s in self.sources:
            ins("INSERT INTO source(id,layer_id,relpath,kind,size,mtime_ns,sha256,load_ref,load_order) "
                "VALUES (?,?,?,?,?,?,?,?,?)", (s.id, s.layer.id, s.relpath, s.kind, s.size, 0, None, s.load_ref,
                                               s.load_order))
        for b in self.blobs:
            h = hashlib.blake2b(f"satk-fake-blob:{b.file_key}".encode(), digest_size=16).digest()
            sectors = -(-b.size // _SECTOR) if b.source.kind == "img" else None
            ins("INSERT INTO blob VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (b.id, b.source.id, b.idx, b.name, b.stem, b.ext, b.off, b.size, b.size, sectors,
                 0 if sectors else None, h, b.ns, int(b.active), b.shadowed_by.id if b.shadowed_by else None))
        seen_img: set[str] = set()
        for t in self.txds:
            ins("INSERT INTO txd VALUES (?,?,?,?,?,?,?,?)",
                (t.id, t.blob.id, t.name, 0x1803FFFF, 0, len(t.textures), t.parent, t.parent_via))
            for x in t.textures:
                if x.hash not in seen_img:
                    seen_img.add(x.hash)
                    ins("INSERT INTO image VALUES (?,?,?,?,?,?)",
                        (bytes.fromhex(x.hash), x.w, x.h, x.d3dfmt, x.data_size, None))
                ins("INSERT INTO texture(id,txd_id,idx,name,mask,platform,raster_fmt,d3dfmt,w,h,depth,levels,alpha,"
                    "filter,uaddr,vaddr,data_off,data_size,pal_off,hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (x.id, t.id, x.idx, x.name, None, x.platform, x.raster_fmt, x.d3dfmt, x.w, x.h,
                     16 if x.d3dfmt == "DXT1" else 32, x.levels, int(x.alpha), 6, 1, 1, x.data_off, x.data_size,
                     x.pal_off, bytes.fromhex(x.hash)))
        for d in self.dffs:
            ins("INSERT INTO dff VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d.id, d.blob.id, d.name, 0x36003, 1, d.geoms, d.geoms + 1, d.geoms, len(d.mats), d.verts, d.tris,
                 *d.bmin, *d.bmax, *d.bs, d.flags, None))
            for gi in range(d.geoms):
                ins("INSERT INTO dff_geom VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (d.id, gi, 0x7F, None, None, 1, 1, gi + 1, *d.bs, 0x40 + gi))
            for mi, (tex, _uses) in enumerate(d.mats):
                ins("INSERT INTO dff_mat VALUES (?,?,?,?,?,?,?,?)", (d.id, 0, mi, 0xFFFFFFFF, tex, None, 0, None))
        for col in self.cols:
            ins("INSERT INTO col(id,blob_id,idx,version,name,hdr_model_id,bmin_x,bmin_y,bmin_z,bmax_x,bmax_y,bmax_z,"
                "active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (col.id, col.blob.id, col.idx, col.version, col.name, None, *col.bmin, *col.bmax, 1))
        for ide in self.ides:
            ins("INSERT INTO ide VALUES (?,?)", (ide.id, ide.source.id))
        for m in self.models:
            ins("INSERT INTO model(rid,id,layer_id,name,txd,sec,ide_id,line,draw,flags,extra,active) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (m.rid, m.id, m.layer.id, m.name, m.txd, m.sec, m.ide.id, m.line, m.draw, m.flags,
                 json.dumps(m.extra, separators=(",", ":")) if m.extra else None, int(m.active)))
        for m in self._model_active.values():
            d, cm, rows = self._dff_of(m), self._col_of(m), self._model_tex.get(m.id, [])
            t = self._active_txd(m.txd)
            ins("INSERT INTO model_link VALUES (?,?,?,?,?,?,?,?)",
                (m.id, d.id if d else None, t.id if t else None, cm.id if cm else None, cm.via if cm else None,
                 sum(1 for i in self.insts_ if i.model_id == m.id), len(rows), sum(1 for r in rows if r[1] is None)))
            for name, x, via, uses in rows:
                ins("INSERT INTO model_tex VALUES (?,?,?,?,?)", (m.id, name, x.id if x else None, via, uses))
        for p in self.ipls:
            ins("INSERT INTO ipl VALUES (?,?,?,?,?,?,?,?)",
                (p.id, p.name, p.kind, p.source.id if p.source else None, p.blob.id if p.blob else None,
                 p.parent.id if p.parent else None, int(p.loaded), p.layer.id))
        for i in self.insts_:
            ins("INSERT INTO inst VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (i.id, i.ipl.id, i.idx, i.model_id, i.area, i.iflags, *i.pos, *i.q, i.lod_idx,
                 i.lod.id if i.lod else None, int(i.is_lod), i.bbox_src))
            a = i.aabb
            ins("INSERT INTO inst_rtree VALUES (?,?,?,?,?,?,?)", (i.id, a[0], a[3], a[1], a[4], a[2], a[5]))
        for k, sid, name, _info in self._search_entries():
            ins("INSERT INTO fts_name(sid, kind, name) VALUES (?,?,?)", (sid, k, name))
        c.commit()
