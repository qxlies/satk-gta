"""A synthetic DFF of a chosen size for the VA loader scene (S9) of ``satk ingame bench``.

The bench resource loads copies of one DFF with ``engineLoadDFF`` to measure how much address space custom models
cost. The repository must not carry a model, so the file is generated when the resource is installed into the
test server: a flat grid mesh (positions, normals, UVs, prelit colours, one untextured material and a triangle-list
BinMesh) of about 2 MiB. Deterministic: the same size gives the same bytes. Pillow/numpy are not used; the RW chunk
writers of :mod:`satk.rw` are imported inside the function.
"""

from __future__ import annotations

import struct

__all__ = ["synthetic_dff", "DEFAULT_SIZE"]

DEFAULT_SIZE = 2 * 1024 * 1024
SA = 0x36003


def _build(g: int) -> bytes:
    from ..rw import codecs as C
    from ..rw.chunk import (ATOMIC, BINMESH, CLUMP, EXTENSION, FRAMELIST, FRAMENAME, GEOMETRY, GEOMLIST, MATERIAL,
                            MATLIST, STRUCT, Chunk, RwStream, pack_libid)

    libid = pack_libid(SA)
    nv = g * g
    pos = bytearray()
    nrm = struct.pack("<3f", 0.0, 0.0, 1.0) * nv
    uv = bytearray()
    for j in range(g):
        for i in range(g):
            pos += struct.pack("<3f", float(i), float(j), ((i * 7 + j * 13) % 11) * 0.05)
            uv += struct.pack("<2f", i / (g - 1), j / (g - 1))
    tri_words = bytearray()
    idx = bytearray()
    for j in range(g - 1):
        for i in range(g - 1):
            a, b, c, d = j * g + i, j * g + i + 1, (j + 1) * g + i, (j + 1) * g + i + 1
            for v0, v1, v2 in ((a, c, b), (b, c, d)):
                tri_words += struct.pack("<4H", v1, v0, 0, v2)
                idx += struct.pack("<3I", v0, v1, v2)
    ntri = len(tri_words) // 8
    half = (g - 1) / 2.0
    sphere = struct.pack("<4f", half, half, 0.3, half * 1.5)
    flags = C.GEO_POSITIONS | C.GEO_TEXTURED | C.GEO_LIGHT | C.GEO_PRELIT | C.GEO_NORMALS
    geo = C.GeometryData(flags, 1, 0, ntri, nv, None, b"\xff" * (4 * nv), [bytes(uv)], bytes(tri_words),
                         [C.MorphData(sphere, 1, 1, bytes(pos), nrm)])
    mat = C.encode_material(C.MaterialData(0, b"\xff\xff\xff\xff", 0, 0, struct.pack("<3f", 1, 1, 1)), SA)
    material = Chunk(MATERIAL, libid, kids=[Chunk(STRUCT, libid, data=mat), Chunk(EXTENSION, libid, kids=[])])
    matlist = Chunk(MATLIST, libid, kids=[Chunk(STRUCT, libid, data=struct.pack("<ii", 1, -1)), material])
    binmesh = C.encode_binmesh(C.BinMeshData(0, len(idx) // 4, [(0, bytes(idx))]))
    geometry = Chunk(GEOMETRY, libid, kids=[Chunk(STRUCT, libid, data=C.encode_geometry(geo, SA)), matlist,
                                            Chunk(EXTENSION, libid, kids=[Chunk(BINMESH, libid, data=binmesh)])])
    frames = C.encode_framelist(C.FrameListData([struct.pack("<9f3fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, -1, 0)]))
    clump = Chunk(CLUMP, libid, kids=[
        Chunk(STRUCT, libid, data=C.encode_clump(C.ClumpData(1, 0, 0), SA)),
        Chunk(FRAMELIST, libid, kids=[Chunk(STRUCT, libid, data=frames),
                                      Chunk(EXTENSION, libid, kids=[Chunk(FRAMENAME, libid, data=b"satkbench")])]),
        Chunk(GEOMLIST, libid, kids=[Chunk(STRUCT, libid, data=struct.pack("<i", 1)), geometry]),
        Chunk(ATOMIC, libid, kids=[Chunk(STRUCT, libid, data=struct.pack("<4I", 0, 0, 5, 0)),
                                   Chunk(EXTENSION, libid, kids=[])]),
        Chunk(EXTENSION, libid, kids=[]),
    ])
    return RwStream([clump]).to_bytes()


def synthetic_dff(size: int = DEFAULT_SIZE) -> bytes:
    """A valid San Andreas DFF close to ``size`` bytes (at most 65 535 vertices, so 16-bit indices)."""
    from ..core.errors import SatkError

    if not 4096 <= size <= 4 * 1024 * 1024:
        raise SatkError("BAD_PARAMS", "synthetic DFF size must be 4 KiB..4 MiB")
    g = max(4, int((size / 76.0) ** 0.5))
    data = _build(g)
    for _ in range(4):                       # the estimate is within a few percent: refine twice
        want = int(g * (size / len(data)) ** 0.5)
        want = max(4, min(255, want))
        if want == g:
            break
        g = want
        data = _build(g)
    return data
