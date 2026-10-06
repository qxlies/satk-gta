"""Synthetic fixtures for the ``satk.fx2d`` tests (no game files).

``build_dff`` assembles a small DFF (one quad per geometry, one frame and atomic per geometry) with optional
2dEffect chunks given as raw ``(pos, type, data)`` entries; ``raw_entries`` has one entry of every type the
game reads, with Rockstar's quirks (garbage after string terminators, padding bytes, ``_``-padded sign text).
``make_txd`` writes a minimal D3D9 texture dictionary and ``FXP`` is a two-system ``effects.fxp``.
"""

from __future__ import annotations

import struct

import pytest

from satk.rw import codecs as C
from satk.rw.chunk import (ATOMIC, BINMESH, CLUMP, EXTENSION, FRAMELIST, FRAMENAME, FX2D, GEOMETRY, GEOMLIST,
                           MATERIAL, MATLIST, STRING, STRUCT, TEXTURE, Chunk, RwStream, pack_libid)

SA = pack_libid(0x36003)


def pos(x: float, y: float, z: float) -> bytes:
    return struct.pack("<3f", x, y, z)


def light(color=(255, 200, 100, 200), corona=b"coronastar", shadow=b"shad_exp", size: int = 80,
          garbage: bool = True) -> bytes:
    d = bytearray(size)
    d[0:4] = bytes(color)
    struct.pack_into("<4f", d, 4, 100.0, 12.0, 2.5, 8.0)
    d[20:25] = bytes((0, 1, 0, 40, 0x42))                       # flash, reflection, flare, shadow mult, flags1
    d[25:49] = corona.ljust(24, b"\0")
    d[49:73] = shadow.ljust(24, b"\0")
    if garbage:
        d[25 + len(corona) + 1] = 0xCD                             # junk after the corona terminator
        d[49 + 23] = 0x41                                          # junk at the end of the shadow slot
    d[73], d[74] = 0, 4                                            # shadow z, flags2 (update_height...)
    if size == 80:
        d[75:78] = struct.pack("<3b", 0, 0, 100)
        if garbage:
            d[78:80] = b"\x67\x68"                                 # padding garbage
    else:
        d[75] = 0x99 if garbage else 0
    return bytes(d)


def particle(name: bytes = b"vent", garbage: bool = True) -> bytes:
    d = bytearray(name.ljust(24, b"\0"))
    if garbage:
        d[20] = 0x3F
    return bytes(d)


def attractor(atype: int = 1, script: bytes = b"none", prob: int = 75) -> bytes:
    return (struct.pack("<b3x9f", atype, 0, 1, 0, 0, 0, 1, 1, 0, 0) + script.ljust(8, b"\0")
            + struct.pack("<i4B", prob, 0x23, 0, 1, 0))


def enex(name: bytes = b"LAHS1A", size: int = 44) -> bytes:
    d = struct.pack("<f2f3ffhBB", 3.14159, 2.0, 2.0, 0.0, 2.2, 0.0, 180.0, 0, 6, 0) + name.ljust(8, b"\0")
    if size == 44:
        d += bytes((23, 6, 0x14, 0x69))
    return d


def roadsign(lines=(b"Las_Brujas", b"Ghost_Town_<", b"", b""), layout: int = 2 | 0 << 2 | 3 << 4) -> bytes:
    d = struct.pack("<2f3fH", 4.0, 2.3, 0.0, 0.0, 90.0, layout)
    d += b"".join(t.ljust(16, b"_") for t in lines)
    return d + b"\x52\x49"                                          # padding garbage


def raw_entries() -> list[tuple[bytes, int, bytes]]:
    """One entry of every type the DFF reader of the game knows, with Rockstar's quirks."""
    return [
        (pos(0.5, 0, 3.2), 0, light()),
        (pos(0, 0, 1), 0, light(size=76)),
        (pos(0, 0, 2), 1, particle()),
        (pos(1, 1, 0), 3, attractor()),
        (pos(2, 0, 0), 6, enex()),
        (pos(2, 0, 0.5), 6, enex(b"SFHSM2", size=40)),
        (pos(1600.5, -2050.25, 28.0), 7, roadsign()),
        (pos(0, 1, 1), 8, struct.pack("<i", 3)),
        (pos(1, 0, 0), 9, struct.pack("<2fb3x", 0.0, 1.0, 2)),
        (pos(0, 0, 0), 10, struct.pack("<9fB3x", 0, 4, -3, 0, -4, 3, 0, -6, 3, 1)),
    ]


def _fx2d(entries) -> Chunk:
    return Chunk(FX2D, SA, data=C.encode_2dfx(C.Fx2dData(list(entries))))


def _geometry(fx=None, ext: bool = True) -> Chunk:
    pos4 = struct.pack("<12f", 0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0)
    tris = struct.pack("<8H", 1, 0, 0, 2, 2, 0, 0, 3)
    g = C.GeometryData(C.GEO_POSITIONS | C.GEO_TEXTURED, 1, 0, 2, 4, None, None,
                       [struct.pack("<8f", 0, 0, 1, 0, 1, 1, 0, 1)], tris,
                       [C.MorphData(struct.pack("<4f", 0.5, 0.5, 0, 0.8), 1, 0, pos4, b"")])
    tex = Chunk(TEXTURE, SA, kids=[Chunk(STRUCT, SA, data=struct.pack("<I", 0x11106)),
                                   Chunk(STRING, SA, data=C.make_string("wall")),
                                   Chunk(STRING, SA, data=C.make_string("")), Chunk(EXTENSION, SA, kids=[])])
    mat = Chunk(MATERIAL, SA, kids=[
        Chunk(STRUCT, SA, data=C.encode_material(C.MaterialData(0, b"\xff\xff\xff\xff", 0, 1,
                                                                 struct.pack("<3f", 1, 1, 1)), 0x36003)),
        tex, Chunk(EXTENSION, SA, kids=[])])
    kids = [Chunk(STRUCT, SA, data=C.encode_geometry(g, 0x36003)),
            Chunk(MATLIST, SA, kids=[Chunk(STRUCT, SA, data=struct.pack("<ii", 1, -1)), mat])]
    if ext:
        mesh = C.BinMeshData(0, 6, [(0, struct.pack("<6I", 0, 1, 2, 0, 2, 3))])
        e = [Chunk(BINMESH, SA, data=C.encode_binmesh(mesh))]
        if fx is not None:
            e.append(_fx2d(fx))
        kids.append(Chunk(EXTENSION, SA, kids=e))
    return Chunk(GEOMETRY, SA, kids=kids)


def build_dff(*geometries, names: tuple[str, ...] | None = None, ext: bool = True, pad: int = 0) -> bytes:
    """A DFF with one frame + atomic per geometry; each argument is ``None`` (no 2dEffect chunk) or a list of
    raw entries. ``ext=False`` leaves the geometries without an Extension chunk; ``pad`` appends zero bytes
    (like IMG sector padding)."""
    geos = list(geometries) or [None]
    names = names or tuple(f"part{i}" for i in range(len(geos)))
    frames = C.encode_framelist(C.FrameListData([struct.pack("<9f3fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, -1, 0)
                                                 for _ in geos]))
    clump = Chunk(CLUMP, SA, kids=[
        Chunk(STRUCT, SA, data=struct.pack("<3i", len(geos), 0, 0)),
        Chunk(FRAMELIST, SA, kids=[Chunk(STRUCT, SA, data=frames)] + [
            Chunk(EXTENSION, SA, kids=[Chunk(FRAMENAME, SA, data=n.encode())]) for n in names]),
        Chunk(GEOMLIST, SA, kids=[Chunk(STRUCT, SA, data=struct.pack("<i", len(geos)))]
              + [_geometry(fx, ext) for fx in geos]),
        *[Chunk(ATOMIC, SA, kids=[Chunk(STRUCT, SA, data=struct.pack("<4I", i, i, 5, 0)),
                                  Chunk(EXTENSION, SA, kids=[])]) for i in range(len(geos))],
        Chunk(EXTENSION, SA, kids=[]),
    ])
    return RwStream([clump]).to_bytes() + b"\0" * pad


def make_txd(names: list[str]) -> bytes:
    """A D3D9 TXD with one 1x1 A8R8G8B8 texture per name."""
    texs = []
    for n in names:
        st = struct.pack("<IBBH32s32sII", 9, 2, 0x11, 0, n.encode().ljust(32, b"\0"), b"\0" * 32, 0x500, 21)
        st += struct.pack("<HHBBBB", 1, 1, 32, 1, 4, 1) + struct.pack("<I", 4) + b"\x10\x20\x30\xff"
        texs.append(Chunk(0x15, SA, kids=[Chunk(STRUCT, SA, data=st), Chunk(EXTENSION, SA, kids=[])]))
    td = Chunk(0x16, SA, kids=[Chunk(STRUCT, SA, data=struct.pack("<HH", len(texs), 2)), *texs,
                               Chunk(EXTENSION, SA, kids=[])])
    return td.to_bytes()


FXP = """FX_PROJECT_DATA:

FX_SYSTEM_DATA:
109

FILENAME: X:\\fx\\vent.fxs
NAME: vent
LENGTH: 1.000
FX_PRIM_BASE_DATA:
NAME: ParticleEmitter

FX_SYSTEM_DATA:
109

FILENAME: X:\\fx\\fire.fxs
NAME: fire
"""


@pytest.fixture
def dff_factory():
    return build_dff


@pytest.fixture
def resources(tmp_path):
    """``(effects.fxp, particle.txd)`` files with the names the synthetic entries use."""
    fxp = tmp_path / "effects.fxp"
    fxp.write_text(FXP, encoding="latin-1")
    txd = tmp_path / "particle.txd"
    txd.write_bytes(make_txd(["coronastar", "shad_exp"]))
    return fxp, txd
