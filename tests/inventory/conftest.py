"""Synthetic fixtures of the inventory tests (no game files): a small DFF with named frames, one quad each."""

from __future__ import annotations

import struct

from satk.rw import codecs as C
from satk.rw.chunk import (ATOMIC, BINMESH, CLUMP, EXTENSION, FRAMELIST, FRAMENAME, GEOMETRY, GEOMLIST, MATERIAL,
                           MATLIST, STRING, STRUCT, TEXTURE, Chunk, RwStream, pack_libid)

SA = pack_libid(0x36003)


def _geometry() -> Chunk:
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
    mesh = C.BinMeshData(0, 6, [(0, struct.pack("<6I", 0, 1, 2, 0, 2, 3))])
    return Chunk(GEOMETRY, SA, kids=[
        Chunk(STRUCT, SA, data=C.encode_geometry(g, 0x36003)),
        Chunk(MATLIST, SA, kids=[Chunk(STRUCT, SA, data=struct.pack("<ii", 1, -1)), mat]),
        Chunk(EXTENSION, SA, kids=[Chunk(BINMESH, SA, data=C.encode_binmesh(mesh))])])


def build_dff(*names: str) -> bytes:
    """A DFF with one frame and one atomic (a 2-triangle quad) per name."""
    frames = C.encode_framelist(C.FrameListData([struct.pack("<9f3fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, -1, 0)
                                                 for _ in names]))
    clump = Chunk(CLUMP, SA, kids=[
        Chunk(STRUCT, SA, data=struct.pack("<3i", len(names), 0, 0)),
        Chunk(FRAMELIST, SA, kids=[Chunk(STRUCT, SA, data=frames)] + [
            Chunk(EXTENSION, SA, kids=[Chunk(FRAMENAME, SA, data=n.encode())]) for n in names]),
        Chunk(GEOMLIST, SA, kids=[Chunk(STRUCT, SA, data=struct.pack("<i", len(names)))]
              + [_geometry() for _ in names]),
        *[Chunk(ATOMIC, SA, kids=[Chunk(STRUCT, SA, data=struct.pack("<4I", i, i, 5, 0)),
                                  Chunk(EXTENSION, SA, kids=[])]) for i in range(len(names))],
        Chunk(EXTENSION, SA, kids=[]),
    ])
    return RwStream([clump]).to_bytes()
