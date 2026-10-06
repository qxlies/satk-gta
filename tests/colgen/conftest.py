"""Synthetic DFF fixtures for the ``satk.colgen`` tests (no game files).

:func:`build_model` writes a complete SA DFF (RW 0x36003) chunk by chunk with the ``satk.rw`` codecs: one
frame + atomic + geometry per part (triangle lists split per material by a BinMesh), texture names, prelit
colours and frame names, so the vehicle rules (``chassis_dummy``, ``wheel_lf_dummy``, ``*_dam``) can be
exercised. Shapes: :func:`box_mesh`, :func:`cylinder_mesh`, :func:`plane_mesh`.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

import pytest

from satk.rw import codecs as C
from satk.rw.chunk import (ATOMIC, BINMESH, CLUMP, EXTENSION, FRAMELIST, FRAMENAME, GEOMETRY, GEOMLIST, MATERIAL,
                           MATLIST, STRING, STRUCT, TEXTURE, Chunk, RwStream, pack_libid)

SA = 0x36003


@dataclass
class Part:
    name: str
    verts: list                      # [(x, y, z)]
    tris: list                       # [(a, b, c, material slot)], counter-clockwise from outside
    textures: list = field(default_factory=lambda: ["wall"])
    pos: tuple = (0.0, 0.0, 0.0)
    parent: int = 0                  # frame index (0 = root)
    prelit: tuple | None = (200, 200, 200, 255)
    atomic: bool = True


def box_mesh(lo=(-1.0, -1.0, 0.0), hi=(1.0, 1.0, 2.0), mat: int = 0) -> tuple[list, list]:
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (0, 4, 7, 3), (1, 2, 6, 5)]
    t = []
    for a, b, c, d in quads:
        t += [(a, b, c, mat), (a, c, d, mat)]
    return v, t


def cylinder_mesh(r: float = 0.5, h: float = 1.0, n: int = 16, mat: int = 0) -> tuple[list, list]:
    v = []
    for k in range(n):
        a = 2 * math.pi * k / n
        v += [(r * math.cos(a), r * math.sin(a), 0.0), (r * math.cos(a), r * math.sin(a), h)]
    v += [(0.0, 0.0, 0.0), (0.0, 0.0, h)]
    bot, top = 2 * n, 2 * n + 1
    t = []
    for k in range(n):
        a0, a1 = 2 * k, 2 * k + 1
        b0, b1 = 2 * ((k + 1) % n), 2 * ((k + 1) % n) + 1
        t += [(a0, b0, b1, mat), (a0, b1, a1, mat), (bot, b0, a0, mat), (top, a1, b1, mat)]
    return v, t


def plane_mesh(size: float = 10.0, n: int = 10, z: float = 0.0, mats: int = 1) -> tuple[list, list]:
    """A flat grid (``n`` x ``n`` quads); materials alternate by column halves when ``mats`` > 1."""
    v = [(-size / 2 + size * i / n, -size / 2 + size * j / n, z) for j in range(n + 1) for i in range(n + 1)]
    t = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            m = 0 if mats == 1 or i < n // 2 else 1
            t += [(a, a + 1, a + n + 2, m), (a, a + n + 2, a + n + 1, m)]
    return v, t


def _texture(libid: int, name: str) -> Chunk:
    return Chunk(TEXTURE, libid, kids=[Chunk(STRUCT, libid, data=struct.pack("<I", 0x1106)),
                                       Chunk(STRING, libid, data=C.make_string(name)),
                                       Chunk(STRING, libid, data=C.make_string("")),
                                       Chunk(EXTENSION, libid, kids=[])])


def _geometry(libid: int, p: Part) -> Chunk:
    nv, nt = len(p.verts), len(p.tris)
    flags = C.GEO_POSITIONS | C.GEO_TEXTURED | (C.GEO_PRELIT if p.prelit else 0)
    tris = b"".join(struct.pack("<4H", b, a, m, c) for a, b, c, m in p.tris)
    xs = [q[0] for q in p.verts]
    ys = [q[1] for q in p.verts]
    zs = [q[2] for q in p.verts]
    cx, cy, cz = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2
    rad = max(math.dist((cx, cy, cz), q) for q in p.verts)
    g = C.GeometryData(flags, 1, 0, nt, nv, None, bytes(p.prelit) * nv if p.prelit else None,
                       [struct.pack(f"<{2 * nv}f", *([0.0] * 2 * nv))], tris,
                       [C.MorphData(struct.pack("<4f", cx, cy, cz, rad), 1, 0,
                                    b"".join(struct.pack("<3f", *q) for q in p.verts), b"")])
    mats = []
    for tex in p.textures:
        ms = C.encode_material(C.MaterialData(0, b"\xff\xff\xff\xff", 0, 1, struct.pack("<3f", 1, 1, 1)), SA)
        mats.append(Chunk(MATERIAL, libid, kids=[Chunk(STRUCT, libid, data=ms), _texture(libid, tex),
                                                 Chunk(EXTENSION, libid, kids=[])]))
    n = len(p.textures)
    matlist = Chunk(MATLIST, libid, kids=[Chunk(STRUCT, libid, data=struct.pack(f"<i{n}i", n, *([-1] * n))), *mats])
    meshes = []
    for m in range(n):
        idx = [i for a, b, c, mm in p.tris if mm == m for i in (a, b, c)]
        if idx:
            meshes.append((m, struct.pack(f"<{len(idx)}I", *idx)))
    binmesh = C.encode_binmesh(C.BinMeshData(0, sum(len(x[1]) // 4 for x in meshes), meshes))
    return Chunk(GEOMETRY, libid, kids=[Chunk(STRUCT, libid, data=C.encode_geometry(g, SA)), matlist,
                                        Chunk(EXTENSION, libid, kids=[Chunk(BINMESH, libid, data=binmesh)])])


def build_model(parts: list[Part], *, frames: list[tuple[str, tuple, int]] | None = None) -> bytes:
    """A DFF: frame 0 = root, then ``frames`` (name, pos, parent) dummies, then one frame per part."""
    libid = pack_libid(SA)
    fr = [("root", (0.0, 0.0, 0.0), -1)] + list(frames or [])
    part_frames = []
    for p in parts:
        part_frames.append(len(fr))
        fr.append((p.name, p.pos, p.parent))
    raw = [struct.pack("<9f3fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, *pos, parent, 0) for _n, pos, parent in fr]
    ext = [Chunk(EXTENSION, libid, kids=[Chunk(FRAMENAME, libid, data=name.encode())]) for name, _p, _q in fr]
    atomics = [Chunk(ATOMIC, libid, kids=[Chunk(STRUCT, libid, data=struct.pack("<4I", f, g, 5, 0)),
                                          Chunk(EXTENSION, libid, kids=[])])
               for g, (f, p) in enumerate(zip(part_frames, parts)) if p.atomic]
    clump = Chunk(CLUMP, libid, kids=[
        Chunk(STRUCT, libid, data=C.encode_clump(C.ClumpData(len(atomics), 0, 0), SA)),
        Chunk(FRAMELIST, libid, kids=[Chunk(STRUCT, libid, data=C.encode_framelist(C.FrameListData(raw))), *ext]),
        Chunk(GEOMLIST, libid, kids=[Chunk(STRUCT, libid, data=struct.pack("<i", len(parts))),
                                     *[_geometry(libid, p) for p in parts]]),
        *atomics,
        Chunk(EXTENSION, libid, kids=[]),
    ])
    return RwStream([clump]).to_bytes()


def crate_dff(tex: str = "wood_crate01", lo=(-0.5, -0.5, 0.0), hi=(0.5, 0.5, 1.0)) -> bytes:
    v, t = box_mesh(lo, hi)
    return build_model([Part("crate", v, t, [tex])])


def barrel_dff(n: int = 16, tex: str = "metal_barrel") -> bytes:
    v, t = cylinder_mesh(0.4, 1.0, n)
    return build_model([Part("barrel", v, t, [tex])])


def ground_dff(n: int = 12, textures=("grass_lawn", "tarmac_road")) -> bytes:
    v, t = plane_mesh(20.0, n, mats=len(textures))
    return build_model([Part("ground", v, t, list(textures))])


def car_dff() -> bytes:
    """A box car: chassis, two doors, bumpers, a damage state, a wheel on its dummy."""
    body_v, body_t = box_mesh((-1.0, -2.2, -0.3), (1.0, 2.2, 0.8))
    door_l = box_mesh((-1.05, -0.6, -0.2), (-0.95, 0.9, 0.6))
    door_r = box_mesh((0.95, -0.6, -0.2), (1.05, 0.9, 0.6))
    bf = box_mesh((-0.9, 2.1, -0.3), (0.9, 2.4, 0.1))
    br = box_mesh((-0.9, -2.4, -0.3), (0.9, -2.1, 0.1))
    wheel = cylinder_mesh(0.35, 0.25, 12)
    frames = [("chassis_dummy", (0.0, 0.0, 0.0), 0), ("wheel_lf_dummy", (-0.9, 1.4, -0.4), 0),
              ("wheel_rf_dummy", (0.9, 1.4, -0.4), 0), ("wheel_lb_dummy", (-0.9, -1.4, -0.4), 0),
              ("wheel_rb_dummy", (0.9, -1.4, -0.4), 0)]
    tex = ["vehiclegeneric256"]
    return build_model([
        Part("chassis", body_v, body_t, tex, parent=1),
        Part("door_lf_ok", *door_l, tex, parent=1),
        Part("door_rf_ok", *door_r, tex, parent=1),
        Part("door_lf_dam", *door_l, tex, parent=1),
        Part("bump_front_ok", *bf, tex, parent=1),
        Part("bump_rear_ok", *br, tex, parent=1),
        Part("wheel", *wheel, ["wheel_tyre"], parent=2),
    ], frames=frames)


@pytest.fixture
def dffs():
    """Namespace of the builders."""
    class _N:
        build_model = staticmethod(build_model)
        Part = Part
        box_mesh = staticmethod(box_mesh)
        cylinder_mesh = staticmethod(cylinder_mesh)
        plane_mesh = staticmethod(plane_mesh)
        crate = staticmethod(crate_dff)
        barrel = staticmethod(barrel_dff)
        ground = staticmethod(ground_dff)
        car = staticmethod(car_dff)
    return _N
