"""Synthetic RW fixtures for the ``satk.rw`` tests (no game files).

``build_dff`` assembles a small but complete DFF chunk by chunk (layouts as in librw's stream code), with
the quirks of Rockstar's files that the writers must keep: garbage after the NUL of texture names, a
pointer value as the night-colour flag, MatFX environment textures, a specular texture, skinning.
"""

from __future__ import annotations

import struct

import pytest

from satk.rw import codecs as C
from satk.rw.chunk import (ATOMIC, BINMESH, BREAKABLE, CLUMP, EXTENSION, FRAMELIST, FRAMENAME, GEOMETRY, GEOMLIST,
                           MATERIAL, MATFX, MATLIST, NIGHT, SKIN, SPECULAR, STRING, STRUCT, TEXTURE, Chunk, RwStream,
                           pack_libid)

SA = 0x36003


def _texture(libid: int, name: str, mask: str = "", garbage: bool = True) -> Chunk:
    raw = name.encode() + b"\0"
    raw += (b"\xcd" if garbage else b"\0") * (-len(raw) % 4 or 4)        # Rockstar: junk after the NUL
    return Chunk(TEXTURE, libid, kids=[
        Chunk(STRUCT, libid, data=struct.pack("<I", 0x11106)),
        Chunk(STRING, libid, data=raw),
        Chunk(STRING, libid, data=C.make_string(mask)),
        Chunk(EXTENSION, libid, kids=[]),
    ])


def _geometry(libid: int, version: int, *, tex: str, mask: str, prelit: bool, normals: bool, night: bool,
              skin: bool, matfx: str | None, specular: str | None, instanced: bool) -> Chunk:
    nv = 4
    pos = struct.pack("<12f", 0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0)
    flags = C.GEO_POSITIONS | C.GEO_TEXTURED | C.GEO_LIGHT | (C.GEO_PRELIT if prelit else 0) | \
        (C.GEO_NORMALS if normals else 0)
    tris = struct.pack("<8H", 1, 0, 0, 2, 2, 0, 0, 3)          # (v1, v0, mat, v2): 0-1-2, 0-2-3
    g = C.GeometryData(flags, 1, 0, 2, nv, None, bytes(range(16)) if prelit else None,
                       [struct.pack("<8f", 0, 0, 1, 0, 1, 1, 0, 1)], tris,
                       [C.MorphData(struct.pack("<4f", 0.5, 0.5, 0, 0.8), 1, 1 if normals else 0, pos,
                                    struct.pack("<12f", *(0, 0, -1) * 4) if normals else b"")])
    mat_ext = []
    if matfx:
        body = struct.pack("<I", 2) + struct.pack("<IfII", 2, 0.5, 0, 1)
        body += _texture(libid, matfx).to_bytes() + struct.pack("<I", 0)
        mat_ext.append(Chunk(MATFX, libid, data=body))
    if specular:
        mat_ext.append(Chunk(SPECULAR, libid, data=struct.pack("<f", 0.3) + specular.encode().ljust(24, b"\0")))
    mat_struct = C.encode_material(C.MaterialData(0, b"\xff\xff\xff\xff", 0x1C2B24, 1,
                                                  struct.pack("<3f", 1, 1, 1)), version)
    material = Chunk(MATERIAL, libid, kids=[Chunk(STRUCT, libid, data=mat_struct), _texture(libid, tex, mask),
                                            Chunk(EXTENSION, libid, kids=mat_ext)])
    slots = [-1, 0] if instanced else [-1]
    matlist = Chunk(MATLIST, libid, kids=[Chunk(STRUCT, libid, data=struct.pack(f"<i{len(slots)}i", len(slots), *slots)),
                                          material])
    ext = [Chunk(BINMESH, libid, data=C.encode_binmesh(C.BinMeshData(0, 6, [(0, struct.pack("<6I", 0, 1, 2, 0, 2, 3))]))),
           Chunk(BREAKABLE, libid, data=struct.pack("<I", 0))]
    if skin:
        s = C.SkinData(3, 2, 2, 0, bytes((2, 0)), bytes((0, 2, 0, 0)) * nv,
                       struct.pack("<4f", 0.75, 0.25, 0, 0) * nv, [struct.pack("<16f", *range(16))] * 3, [],
                       b"\0" * 12)
        ext.append(Chunk(SKIN, libid, data=C.encode_skin(s)))
    if night:
        ext.append(Chunk(NIGHT, libid, data=struct.pack("<I", 0x2E5EFF70) + bytes(range(100, 116))))
    return Chunk(GEOMETRY, libid, kids=[Chunk(STRUCT, libid, data=C.encode_geometry(g, version)), matlist,
                                        Chunk(EXTENSION, libid, kids=ext)])


def build_dff(*, version: int = SA, tex: str = "wall", mask: str = "", prelit: bool = True, normals: bool = False,
              night: bool = True, skin: bool = False, matfx: str | None = None, specular: str | None = None,
              instanced: bool = False, short_clump: bool = False, frame_name: str = "box") -> bytes:
    """A one-frame, one-atomic DFF (a textured quad) at RW ``version``."""
    libid = pack_libid(version)
    frames = C.encode_framelist(C.FrameListData([struct.pack("<9f3fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, -1, 0)]))
    clump_struct = C.encode_clump(C.ClumpData(1, 0, 0, short=short_clump), version)
    clump = Chunk(CLUMP, libid, kids=[
        Chunk(STRUCT, libid, data=clump_struct),
        Chunk(FRAMELIST, libid, kids=[Chunk(STRUCT, libid, data=frames), Chunk(EXTENSION, libid, kids=[
            Chunk(FRAMENAME, libid, data=frame_name.encode())])]),
        Chunk(GEOMLIST, libid, kids=[Chunk(STRUCT, libid, data=struct.pack("<i", 1)),
                                     _geometry(libid, version, tex=tex, mask=mask, prelit=prelit, normals=normals,
                                               night=night, skin=skin, matfx=matfx, specular=specular,
                                               instanced=instanced)]),
        Chunk(ATOMIC, libid, kids=[Chunk(STRUCT, libid, data=struct.pack("<4I", 0, 0, 5, 0)),
                                   Chunk(EXTENSION, libid, kids=[])]),
        Chunk(EXTENSION, libid, kids=[]),
    ])
    return RwStream([clump]).to_bytes()


@pytest.fixture
def make_dff():
    return build_dff
