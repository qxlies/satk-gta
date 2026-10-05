"""Typed codecs for the RW structs ``satk.rw`` edits (librw stream layouts). Stdlib only.

Every codec is a pair ``decode_x(payload, ...) -> XData`` / ``encode_x(data, ...) -> bytes`` with
``encode(decode(p)) == p`` for well-formed input: bytes the layout does not explain (vendor padding,
garbage after a string's NUL, unknown trailing data) are kept in ``tail``/``pad`` fields. Float arrays
stay ``bytes``/``array('f')`` so that no float is converted to Python and back (NaN payloads survive).

Layouts (librw ``geometry.cpp``, ``clump.cpp``, ``skin.cpp``, ``matfx.cpp``):

* Geometry struct: ``u16 flags, u8 numUV, u8 native, i32 tris, verts, morphs``; RW < 3.4 (``0x34000``)
  adds 12 bytes of surface properties; then (non-native) prelit RGBA, UV sets, triangles
  ``u16 v1, v0, mat, v2``; per morph target: bounding sphere, ``i32 hasVerts, hasNormals``, arrays.
* Material struct: ``u32 flags, RGBA, u32 unused, i32 textured`` + 3 floats (RW >= ``0x30400``).
* Clump struct: ``i32 atomics`` + ``i32 lights, cameras`` when RW > ``0x33000``.
* Skin PLG: new format (RW >= 3.4) ``numBones, numUsed, maxWeights, pad, used[]``, indices, weights,
  matrices, split data; old format (``numUsed == 0``): a ``0xDEADDEAD`` word before every matrix,
  no used list and no split data.
* MatFX material: ``u32 type`` + two effect slots, slots may embed whole Texture chunks.
"""

from __future__ import annotations

import struct
from array import array
from dataclasses import dataclass, field

from .chunk import Chunk, parse_chunk

__all__ = [
    "CodecError", "GEO_TRISTRIP", "GEO_POSITIONS", "GEO_TEXTURED", "GEO_PRELIT", "GEO_NORMALS", "GEO_LIGHT",
    "GEO_MODULATE", "GEO_TEXTURED2", "MorphData", "GeometryData", "decode_geometry", "encode_geometry",
    "MaterialData", "decode_material", "encode_material", "ClumpData", "decode_clump", "encode_clump",
    "SkinData", "decode_skin", "encode_skin", "NightData", "decode_night", "encode_night", "MatFxData",
    "decode_matfx", "encode_matfx", "SpecularData", "decode_specular", "encode_specular", "BinMeshData",
    "decode_binmesh", "encode_binmesh", "Fx2dData", "decode_2dfx", "encode_2dfx", "FrameListData",
    "decode_framelist", "encode_framelist", "StringData", "decode_string", "encode_string", "make_string",
]

GEO_TRISTRIP, GEO_POSITIONS, GEO_TEXTURED, GEO_PRELIT = 0x01, 0x02, 0x04, 0x08
GEO_NORMALS, GEO_LIGHT, GEO_MODULATE, GEO_TEXTURED2 = 0x10, 0x20, 0x40, 0x80
_DEADDEAD = b"\xad\xde\xad\xde"


class CodecError(ValueError):
    """The payload does not have the layout the codec expects."""


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise CodecError(msg)


# ============================================================================ strings


@dataclass
class StringData:
    """RW String chunk: ``text`` up to the first NUL, ``pad`` = the bytes after it (NUL included)."""

    text: str
    pad: bytes


def decode_string(payload: bytes) -> StringData:
    z = payload.find(b"\0")
    if z < 0:
        return StringData(payload.decode("latin-1"), b"")
    return StringData(payload[:z].decode("latin-1"), payload[z:])


def encode_string(s: StringData) -> bytes:
    return s.text.encode("latin-1") + s.pad


def make_string(text: str) -> bytes:
    """Canonical RW string payload (librw): text, NUL, zero padding to a multiple of 4."""
    raw = text.encode("ascii") + b"\0"
    return raw + b"\0" * (-len(raw) % 4)


# ============================================================================ geometry


@dataclass
class MorphData:
    sphere: bytes                    # 4 floats
    has_verts: int
    has_normals: int
    verts: bytes = b""               # 12 * numVerts
    normals: bytes = b""


@dataclass
class GeometryData:
    flags: int
    num_uv_raw: int
    native: int
    num_tris: int
    num_verts: int
    surf: bytes | None               # 12 bytes for RW < 0x34000
    prelit: bytes | None
    uvs: list[bytes]
    tris: bytes                      # 8 * numTris: u16 v1, v0, mat, v2
    morphs: list[MorphData]
    tail: bytes = b""

    @property
    def is_native(self) -> bool:
        return bool(self.native & 1)

    @property
    def num_uv(self) -> int:
        return uv_sets(self.flags, self.num_uv_raw)

    def positions(self, morph: int = 0) -> array:
        a = array("f")
        a.frombytes(self.morphs[morph].verts)
        return a

    def triangles(self) -> array:
        """Triangle words ``v1, v0, mat, v2`` (array of u16)."""
        a = array("H")
        a.frombytes(self.tris)
        return a


_GEO = struct.Struct("<HBBiii")


def uv_sets(flags: int, raw: int) -> int:
    """Number of UV sets (librw: the u8 field, else derived from TEXTURED/TEXTURED2)."""
    if raw:
        return raw
    if flags & GEO_TEXTURED2:
        return 2
    return 1 if flags & GEO_TEXTURED else 0


def decode_geometry(p: bytes, version: int) -> GeometryData:
    n = len(p)
    _need(n >= 16, "geometry struct shorter than 16 bytes")
    flags, nuv, native, ntri, nvert, nmorph = _GEO.unpack_from(p, 0)
    _need(ntri >= 0 and nvert >= 0 and 0 <= nmorph <= 4096, f"implausible geometry counts {ntri}/{nvert}/{nmorph}")
    o = 16
    surf = None
    if version < 0x34000:
        _need(o + 12 <= n, "geometry surface properties truncated")
        surf, o = p[o:o + 12], o + 12
    is_native = bool(native & 1)
    prelit = None
    uvs: list[bytes] = []
    tris = b""
    if not is_native:
        if flags & GEO_PRELIT:
            _need(o + 4 * nvert <= n, "prelit colours truncated")
            prelit, o = p[o:o + 4 * nvert], o + 4 * nvert
        for _ in range(uv_sets(flags, nuv)):
            _need(o + 8 * nvert <= n, "UV set truncated")
            uvs.append(p[o:o + 8 * nvert])
            o += 8 * nvert
        _need(o + 8 * ntri <= n, "triangles truncated")
        tris, o = p[o:o + 8 * ntri], o + 8 * ntri
    morphs = []
    for _ in range(nmorph):
        _need(o + 24 <= n, "morph target header truncated")
        sphere = p[o:o + 16]
        hv, hn = struct.unpack_from("<ii", p, o + 16)
        o += 24
        m = MorphData(sphere, hv, hn)
        if not is_native:
            if hv:
                _need(o + 12 * nvert <= n, "vertices truncated")
                m.verts, o = p[o:o + 12 * nvert], o + 12 * nvert
            if hn:
                _need(o + 12 * nvert <= n, "normals truncated")
                m.normals, o = p[o:o + 12 * nvert], o + 12 * nvert
        morphs.append(m)
    return GeometryData(flags, nuv, native, ntri, nvert, surf, prelit, uvs, tris, morphs, p[o:])


def encode_geometry(g: GeometryData, version: int) -> bytes:
    out = [_GEO.pack(g.flags, g.num_uv_raw, g.native, g.num_tris, g.num_verts, len(g.morphs))]
    if version < 0x34000:
        out.append(g.surf if g.surf is not None and len(g.surf) == 12 else struct.pack("<3f", 1.0, 1.0, 1.0))
    if not g.is_native:
        if g.flags & GEO_PRELIT:
            out.append(g.prelit if g.prelit is not None else b"\xff" * (4 * g.num_verts))
        out.extend(g.uvs[:g.num_uv])
        out.append(g.tris)
    for m in g.morphs:
        out.append(m.sphere)
        out.append(struct.pack("<ii", m.has_verts, m.has_normals))
        if not g.is_native:
            if m.has_verts:
                out.append(m.verts)
            if m.has_normals:
                out.append(m.normals)
    out.append(g.tail)
    return b"".join(out)


# ============================================================================ material


@dataclass
class MaterialData:
    flags: int
    color: bytes                     # RGBA
    unused: int
    textured: int
    surf: bytes | None               # 12 bytes for RW >= 0x30400
    tail: bytes = b""


def decode_material(p: bytes, version: int) -> MaterialData:
    _need(len(p) >= 16, "material struct shorter than 16 bytes")
    (flags,) = struct.unpack_from("<I", p, 0)
    unused, textured = struct.unpack_from("<Ii", p, 8)
    o = 16
    surf = None
    if version >= 0x30400 and len(p) >= 28:
        surf, o = p[16:28], 28
    return MaterialData(flags, p[4:8], unused, textured, surf, p[o:])


def encode_material(m: MaterialData, version: int) -> bytes:
    out = struct.pack("<I", m.flags) + m.color + struct.pack("<Ii", m.unused, m.textured)
    if version >= 0x30400:
        out += m.surf if m.surf is not None else struct.pack("<3f", 1.0, 1.0, 1.0)   # librw default
    return out + m.tail


# ============================================================================ clump


@dataclass
class ClumpData:
    atomics: int
    lights: int
    cameras: int
    tail: bytes = b""
    short: bool = False              # only the atomic count was stored (327 vanilla SA files do this)


def decode_clump(p: bytes, version: int) -> ClumpData:
    _need(len(p) >= 4, "clump struct shorter than 4 bytes")
    (na,) = struct.unpack_from("<i", p, 0)
    if version > 0x33000 and len(p) >= 12:
        nl, nc = struct.unpack_from("<ii", p, 4)
        return ClumpData(na, nl, nc, p[12:])
    return ClumpData(na, 0, 0, p[4:], short=True)


def encode_clump(c: ClumpData, version: int) -> bytes:
    if version > 0x33000 and not c.short:
        return struct.pack("<iii", c.atomics, c.lights, c.cameras) + c.tail
    return struct.pack("<i", c.atomics) + c.tail


# ============================================================================ frames


@dataclass
class FrameListData:
    frames: list[bytes]              # 56 bytes each: rot 3x3, pos, i32 parent, u32 flags
    tail: bytes = b""


def decode_framelist(p: bytes) -> FrameListData:
    _need(len(p) >= 4, "frame list struct shorter than 4 bytes")
    (n,) = struct.unpack_from("<i", p, 0)
    _need(0 <= n and 4 + 56 * n <= len(p), f"frame list of {n} frames truncated")
    return FrameListData([p[4 + 56 * i:60 + 56 * i] for i in range(n)], p[4 + 56 * n:])


def encode_framelist(f: FrameListData) -> bytes:
    return struct.pack("<i", len(f.frames)) + b"".join(f.frames) + f.tail


# ============================================================================ skin


@dataclass
class SkinData:
    num_bones: int
    num_used: int
    max_weights: int
    pad: int
    used: bytes
    indices: bytes                   # 4 * numVerts
    weights: bytes                   # 16 * numVerts (floats)
    matrices: list[bytes]            # 64 bytes each
    marks: list[bytes]               # old format: the word before every matrix
    split: bytes                     # new format: boneLimit, numMeshes, rleSize + data
    tail: bytes = b""

    @property
    def old_format(self) -> bool:
        return self.num_used == 0


def decode_skin(p: bytes, nverts: int) -> SkinData:
    _need(len(p) >= 4, "skin shorter than 4 bytes")
    nb, nu, mw, pad = p[0], p[1], p[2], p[3]
    old = nu == 0                                   # librw: oldFormat = header[1] == 0
    o = 4
    used = b""
    if not old:
        used, o = p[o:o + nu], o + nu
    _need(o + 20 * nverts <= len(p), "skin vertex data truncated")
    idx, o = p[o:o + 4 * nverts], o + 4 * nverts
    wts, o = p[o:o + 16 * nverts], o + 16 * nverts
    mats, marks = [], []
    for _ in range(nb):
        if old:
            _need(o + 4 <= len(p), "skin matrix marker truncated")
            marks.append(p[o:o + 4])
            o += 4
        _need(o + 64 <= len(p), "skin matrix truncated")
        mats.append(p[o:o + 64])
        o += 64
    split = b""
    if not old:
        _need(o + 12 <= len(p), "skin split data truncated")
        _limit, nmesh, rle = struct.unpack_from("<iii", p, o)
        size = 12 + (nb + 2 * (nmesh + rle) if nmesh else 0)
        _need(nmesh >= 0 and rle >= 0 and o + size <= len(p), "skin split data overruns the plugin")
        split, o = p[o:o + size], o + size
    return SkinData(nb, nu, mw, pad, used, idx, wts, mats, marks, split, p[o:])


def encode_skin(s: SkinData) -> bytes:
    out = [bytes((s.num_bones, s.num_used, s.max_weights, s.pad))]
    if not s.old_format:
        out.append(s.used)
    out += [s.indices, s.weights]
    for i, m in enumerate(s.matrices):
        if s.old_format:
            out.append(s.marks[i] if i < len(s.marks) else _DEADDEAD)
        out.append(m)
    if not s.old_format:
        out.append(s.split or b"\0" * 12)
    out.append(s.tail)
    return b"".join(out)


def skin_to_old(s: SkinData) -> SkinData:
    """librw write for RW < 3.4: no used-bone list, no split data, ``0xDEADDEAD`` before each matrix."""
    return SkinData(s.num_bones, 0, 0, 0, b"", s.indices, s.weights, list(s.matrices),
                    [_DEADDEAD] * s.num_bones, b"", s.tail)


def skin_to_new(s: SkinData, nverts: int) -> SkinData:
    """librw ``findNumWeights`` + ``findUsedBones``; empty split data."""
    if not s.old_format:
        return s
    w = array("f")
    w.frombytes(s.weights)
    nw = 1
    for v in range(nverts):
        while nw < 4 and w[4 * v + nw] != 0.0:
            nw += 1
        if nw == 4:
            break
    used = set()
    for v in range(nverts):
        for i in range(nw):
            if w[4 * v + i] != 0.0:
                used.add(s.indices[4 * v + i])
    ub = bytes(sorted(used))
    if not ub:                                      # keep the new format recognisable (numUsed != 0)
        ub = b"\0"
    return SkinData(s.num_bones, len(ub), nw, 0, ub, s.indices, s.weights, list(s.matrices), [], b"\0" * 12,
                    s.tail)


# ============================================================================ night colours


@dataclass
class NightData:
    magic: int                       # "has colours" word (a pointer value in Rockstar's files)
    colors: bytes                    # 4 * numVerts RGBA
    tail: bytes = b""


def decode_night(p: bytes, nverts: int) -> NightData:
    _need(len(p) >= 4, "night colours shorter than 4 bytes")
    (magic,) = struct.unpack_from("<I", p, 0)
    if not magic:
        return NightData(0, b"", p[4:])
    _need(4 + 4 * nverts <= len(p), "night colours truncated")
    return NightData(magic, p[4:4 + 4 * nverts], p[4 + 4 * nverts:])


def encode_night(nd: NightData) -> bytes:
    return struct.pack("<I", nd.magic) + nd.colors + nd.tail


# ============================================================================ MatFX (material)


@dataclass
class MatFxData:
    """``parts``: raw ``bytes`` and embedded Texture ``Chunk`` nodes, in stream order."""

    parts: list = field(default_factory=list)

    def textures(self) -> list[Chunk]:
        return [x for x in self.parts if isinstance(x, Chunk)]


def decode_matfx(p: bytes) -> MatFxData:
    _need(len(p) >= 4, "MatFX shorter than 4 bytes")
    parts: list = []
    o = 4
    raw_from = 0

    def word(at: int) -> int:
        _need(at + 4 <= len(p), "MatFX slot truncated")
        return struct.unpack_from("<i", p, at)[0]

    def texture(at: int) -> int:
        nonlocal raw_from
        parts.append(p[raw_from:at])
        try:
            ch = parse_chunk(p, at)
        except ValueError as e:
            raise CodecError(f"MatFX texture: {e}") from None
        _need(ch.type == 0x06 and not ch.opaque, f"MatFX slot holds chunk 0x{ch.type:X}, not a Texture")
        parts.append(ch)
        end = at + 12 + len(ch.payload())
        raw_from = end
        return end

    for _slot in range(2):
        if o + 4 > len(p):
            break
        typ = word(o)
        o += 4
        if typ == 1:                                    # bump map
            o += 4
            if word(o):
                o = texture(o + 4)
            else:
                o += 4
            if word(o):
                o = texture(o + 4)
            else:
                o += 4
        elif typ == 2:                                  # environment map
            o += 8
            if word(o):
                o = texture(o + 4)
            else:
                o += 4
        elif typ == 4:                                  # dual texture
            o += 8
            if word(o):
                o = texture(o + 4)
            else:
                o += 4
        elif typ in (0, 5):                             # none / UV transform: no data
            pass
        else:
            raise CodecError(f"unknown MatFX effect type {typ}")
        _need(o <= len(p), "MatFX slot overruns the plugin")
    parts.append(p[raw_from:])
    return MatFxData([x for x in parts if not (isinstance(x, bytes) and not x)])


def encode_matfx(m: MatFxData) -> bytes:
    return b"".join(x.to_bytes() if isinstance(x, Chunk) else x for x in m.parts)


# ============================================================================ specular


@dataclass
class SpecularData:
    level: bytes                     # f32
    name: bytes                      # 24 bytes, NUL padded
    tail: bytes = b""

    @property
    def texture(self) -> str:
        return self.name.split(b"\0", 1)[0].decode("latin-1")


def decode_specular(p: bytes) -> SpecularData:
    _need(len(p) >= 28, "specular material shorter than 28 bytes")
    return SpecularData(p[:4], p[4:28], p[28:])


def encode_specular(s: SpecularData) -> bytes:
    return s.level + s.name + s.tail


# ============================================================================ BinMesh


@dataclass
class BinMeshData:
    flags: int
    total: int
    meshes: list[tuple[int, bytes]]  # (material index, u32 indices)
    tail: bytes = b""


def decode_binmesh(p: bytes) -> BinMeshData:
    _need(len(p) >= 12, "BinMesh shorter than 12 bytes")
    flags, n, total = struct.unpack_from("<III", p, 0)
    o = 12
    meshes = []
    for _ in range(n):
        _need(o + 8 <= len(p), "BinMesh split header truncated")
        ni, mat = struct.unpack_from("<II", p, o)
        o += 8
        _need(o + 4 * ni <= len(p), "BinMesh indices truncated")
        meshes.append((mat, p[o:o + 4 * ni]))
        o += 4 * ni
    return BinMeshData(flags, total, meshes, p[o:])


def encode_binmesh(b: BinMeshData) -> bytes:
    out = [struct.pack("<III", b.flags, len(b.meshes), b.total)]
    for mat, ix in b.meshes:
        out.append(struct.pack("<II", len(ix) // 4, mat))
        out.append(ix)
    out.append(b.tail)
    return b"".join(out)


# ============================================================================ 2dEffect


@dataclass
class Fx2dData:
    entries: list[tuple[bytes, int, bytes]]  # (position 12 B, type, data)
    tail: bytes = b""


def decode_2dfx(p: bytes) -> Fx2dData:
    _need(len(p) >= 4, "2dEffect shorter than 4 bytes")
    (n,) = struct.unpack_from("<I", p, 0)
    o = 4
    out = []
    for _ in range(n):
        _need(o + 20 <= len(p), "2dEffect entry header truncated")
        typ, size = struct.unpack_from("<II", p, o + 12)
        _need(o + 20 + size <= len(p), "2dEffect entry data truncated")
        out.append((p[o:o + 12], typ, p[o + 20:o + 20 + size]))
        o += 20 + size
    return Fx2dData(out, p[o:])


def encode_2dfx(f: Fx2dData) -> bytes:
    out = [struct.pack("<I", len(f.entries))]
    for pos, typ, data in f.entries:
        out.append(pos + struct.pack("<II", typ, len(data)) + data)
    out.append(f.tail)
    return b"".join(out)
