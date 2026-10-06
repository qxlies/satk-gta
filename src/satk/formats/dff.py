"""Models (``*.dff``), SPEC §4.2 ``satk.formats.dff``. Stdlib only. Milestone F2.

A DFF is one or more RenderWare ``Clump`` chunks (optionally preceded by a ``UVAnimDict 0x2B``)::

    Clump 0x10
      Struct            u32 numAtomics [, u32 numLights, u32 numCameras]
      FrameList 0x0E    Struct: u32 n + n x 56 B (rot 3x3, pos, i32 parent, u32 flags); n x Extension
                        (FrameName 0x253F2FE, HAnim 0x11E)
      GeometryList 0x1A Struct: u32 n; n x Geometry 0x0F
        Geometry          Struct (format, tris, verts, morphs; [3 floats if RW < 0x34000]; prelit, UV sets,
                          triangles, morph targets with bounding sphere/positions/normals),
                          MaterialList 0x08 (Struct: n slots, -1 = new material) + Material 0x07 chunks,
                          Extension: BinMesh 0x50E, Skin 0x116, night colours 0x253F2F9, 2dEffect 0x253F2F8,
                          Breakable 0x253F2FD
      Atomic 0x14       Struct: u32 frame, u32 geometry, u32 flags, u32 unused; Extension
      Extension         CollisionModel 0x253F2FA (embedded COL3 of vehicles)

Pitfalls handled (SPEC §4.2 #3, #4, #8): data ends at the RW chunk, not at the sector (``player.img``
has 3 clumps in a row, all of them are read; indices of frames/geometries are global across clumps);
RW 0x35000 DFFs; the Geometry struct of RW < 0x34000 carries 12 extra bytes; 98 % of the geometries are
tristrips (alternate winding, degenerate triangles dropped); normals only in 26 %; 2 UV sets; night
colours; 2dEffects; UV-anim dictionary before the clump; embedded collision in cars.

:func:`scan_dff` reads only headers, materials and index buffers (for the exact triangle count after
strip expansion) plus the position arrays for the bounding box; :func:`decode_geometry` builds full
vertex/index arrays (``array`` module, no numpy).

Example::

    info = scan_dff(blob)
    print(info.clumps, info.atomics, len(info.geoms), info.tris, sorted({m.texture for m in info.materials}))
    mesh = decode_geometry(blob, info.geoms[0].geom_off)
"""

from __future__ import annotations

import math
import struct
import sys
from array import array
from dataclasses import dataclass, field
from itertools import compress
from typing import NamedTuple

from .rw import FormatError, rw_version

__all__ = [
    "Material", "Frame", "GeomInfo", "Effect2D", "DffInfo", "Mesh", "scan_dff", "decode_geometry",
    "decode_geometries", "find_embedded_col", "strip_to_triangles", "count_strip_triangles", "model_matrices",
    "FLAG_NAMES", "FX_NAMES", "EFFECT_TYPES", "IDENTITY",
]

# ----------------------------------------------------------------------------- chunk ids
_STRUCT, _STRING, _EXT = 0x01, 0x02, 0x03
_TEXTURE, _MATERIAL, _MATLIST = 0x06, 0x07, 0x08
_FRAMELIST, _GEOMETRY, _CLUMP, _ATOMIC, _GEOMLIST, _UVANIMDICT = 0x0E, 0x0F, 0x10, 0x14, 0x1A, 0x2B
_SKIN, _HANIM, _MATFX, _UVANIM, _BINMESH = 0x116, 0x11E, 0x120, 0x135, 0x50E
_SPECULAR, _2DFX, _NIGHT, _COLMODEL = 0x253F2F6, 0x253F2F8, 0x253F2F9, 0x253F2FA
_REFLECTION, _BREAKABLE, _FRAMENAME = 0x253F2FC, 0x253F2FD, 0x253F2FE

# ----------------------------------------------------------------------------- flag bits
#: ``DffInfo.flags`` bits, identical to the index DDL ``dff.flags``.
FLAG_NAMES: dict[int, str] = {
    1: "skin", 2: "hanim", 4: "prelit", 8: "night", 16: "normals", 32: "2dfx", 64: "embedded_col",
    128: "uvanim", 256: "matfx", 512: "reflection", 1024: "specular", 2048: "breakable", 4096: "multi_clump",
}
F_SKIN, F_HANIM, F_PRELIT, F_NIGHT, F_NORMALS, F_2DFX, F_COL = 1, 2, 4, 8, 16, 32, 64
F_UVANIM, F_MATFX, F_REFLECTION, F_SPECULAR, F_BREAKABLE, F_MULTI = 128, 256, 512, 1024, 2048, 4096
#: ``Material.fx`` bits (index DDL ``dff_mat.fx``).
FX_NAMES: dict[int, str] = {1: "env", 2: "bump", 4: "dual", 8: "uvanim", 16: "reflection", 32: "specular"}
FX_ENV, FX_BUMP, FX_DUAL, FX_UVANIM, FX_REFLECTION, FX_SPECULAR = 1, 2, 4, 8, 16, 32
#: RpMatFX effect type -> ``fx`` bits (1 bump, 2 env, 3 bump+env, 4 dual, 5 uv transform, 6 dual+uv).
_MATFX_BITS = {1: FX_BUMP, 2: FX_ENV, 3: FX_BUMP | FX_ENV, 4: FX_DUAL, 5: FX_UVANIM, 6: FX_DUAL | FX_UVANIM}
#: Vehicle colour keys (RGB, alpha ignored) -> ``color_slot`` (``CVehicleModelInfo::SetEditableMaterialsCB``).
_COLOR_SLOTS = {(60, 255, 0): 1, (255, 0, 175): 2, (0, 255, 255): 3, (255, 0, 255): 4}
#: 2dEffect entry types.
EFFECT_TYPES: dict[int, str] = {
    0: "light", 1: "particle", 2: "unknown2", 3: "attractor", 4: "sun_glare", 5: "interior", 6: "enex",
    7: "roadsign", 8: "trigger_point", 9: "cover_point", 10: "escalator",
}

# Geometry format bits
GEO_TRISTRIP, GEO_POSITIONS, GEO_TEXTURED, GEO_PRELIT = 0x01, 0x02, 0x04, 0x08
GEO_NORMALS, GEO_LIGHT, GEO_MODULATE, GEO_TEXTURED2 = 0x10, 0x20, 0x40, 0x80
GEO_NATIVE = 0x01000000

_HDR = struct.Struct("<III")
_GSTRUCT = struct.Struct("<IiiI")
_MORPH = struct.Struct("<4fII")
_FRAME = struct.Struct("<12fiI")
_BIG = sys.byteorder == "big"


class _C(NamedTuple):
    """Chunk header as a tuple (fast; same fields as :class:`satk.formats.rw.Chunk`)."""

    type: int
    size: int
    libid: int
    data_off: int
    end: int


_tnew = tuple.__new__
_unpack_hdr = _HDR.unpack_from


def _kids(buf, start: int, end: int) -> list:
    """Children of ``[start, end)``; a child overrunning its parent -> ``FormatError`` (SPEC §4.2)."""
    out = []
    off = start
    while off + 12 <= end:
        t, s, v = _unpack_hdr(buf, off)
        d = off + 12
        e = d + s
        if e > end:
            raise FormatError("rw", off, f"child chunk 0x{t:X} size {s} overruns its parent by {e - end}")
        out.append(_tnew(_C, (t, s, v, d, e)))
        off = e
    return out


def _top(buf, off: int) -> _C:
    t, s, v = _unpack_hdr(buf, off)
    e = off + 12 + s
    if e > len(buf):
        raise FormatError("rw", off, f"chunk 0x{t:X} size {s} overruns the buffer by {e - len(buf)}")
    return _tnew(_C, (t, s, v, off + 12, e))


# ----------------------------------------------------------------------------- public dataclasses
@dataclass(frozen=True, slots=True)
class Material:
    """A material slot of a geometry (slots reusing an earlier material are repeated).

    ``rgba`` = ``0xRRGGBBAA``; ``texture``/``mask`` ``None`` when absent or empty (6 vanilla materials
    have a Texture chunk with an empty name); ``fx`` = :data:`FX_NAMES` bits; ``color_slot`` 1..4 when the
    RGB is a vehicle recolour key (60,255,0 / 255,0,175 / 0,255,255 / 255,0,255; meaningful for ``cars``).

    ``effects`` holds the decoded values (keys only when present; not part of equality/hash):

    * ``surface``: ``[ambient, specular, diffuse]`` of the Material struct (RW >= 3.4);
    * ``env``: MatFX env map ``{"coef", "fb_alpha", "tex"}``; ``bump``: ``{"coef", "tex", "bumped_tex"}``;
      ``dual``: ``{"src_blend", "dst_blend", "tex"}``; ``uv_transform``: ``True``;
    * ``reflection``: the 24-byte Rockstar env material 0x253F2FC (``CCustomCarEnvMapPipeline``)
      ``{"scale": [x, y], "offset": [x, y], "intensity"}`` (engine names Scale, TranslationScale,
      Shininess; the car sheen needs ``intensity`` > 0 and the MatFX env texture);
    * ``specular``: the 28-byte specular material 0x253F2F6 ``{"level", "tex"}``.
    """

    geom: int
    idx: int
    rgba: int
    texture: str | None
    mask: str | None
    fx: int
    color_slot: int | None
    effects: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True, slots=True)
class Frame:
    """A frame (global index across clumps). ``parent`` -1 for a root; ``atomic``: an atomic uses it.

    ``matrix`` = the local transform relative to the parent as 12 floats ``(rx, ry, rz, ux, uy, uz, ax, ay,
    az, px, py, pz)`` (RW columns right, up, at and the position: a point maps to ``x*right + y*up + z*at +
    pos``). :func:`model_matrices` gives the model-space matrices of all frames.
    """

    idx: int
    parent: int
    name: str | None
    atomic: bool
    matrix: tuple = ()


@dataclass(frozen=True, slots=True)
class GeomInfo:
    """Geometry header.

    ``rw_flags`` = the raw Geometry ``format`` word (low 16 bits rpGEOMETRY flags: 1 tristrip, 2 positions,
    4 textured, 8 prelit, 16 normals, 32 light, 64 modulate, 128 textured2; bits 16-23 UV set count;
    0x01000000 native). ``tris`` = triangles after strip expansion (degenerates dropped; lists as-is).
    ``frame`` = frame of the first atomic using it (-1 if none). ``geom_off`` = offset of the Geometry
    chunk header in the buffer given to :func:`scan_dff` (pass it to :func:`decode_geometry`). ``bbox`` =
    ``(minx, miny, minz, maxx, maxy, maxz)`` of the vertex positions in the geometry's own (frame) space,
    ``None`` without positions. ``uv_sets`` counts the UV sets (2 on vehicle parts: set 1 is read by the
    ``x*`` env texture).
    """

    idx: int
    rw_flags: int
    verts: int
    tris: int
    uv_sets: int
    strip: bool
    frame: int
    bsphere: tuple[float, float, float, float]
    geom_off: int
    bbox: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class Effect2D:
    """A 2dEffect of the DFF (``type_name`` from :data:`EFFECT_TYPES`; ``data`` = decoded fields)."""

    idx: int
    type: int
    type_name: str
    pos: tuple[float, float, float]
    data: dict


@dataclass(frozen=True, slots=True)
class DffInfo:
    """Summary of a DFF. ``flags`` = :data:`FLAG_NAMES` bits; ``plugins`` = every Extension child chunk
    id seen; ``bbox`` = ``(minx, miny, minz, maxx, maxy, maxz)`` in model space (root frame = identity,
    child frames applied; geometries used by atomics); ``bsphere`` = ``(x, y, z, r)`` enclosing them."""

    rw_version: int
    clumps: int
    atomics: int
    frames: list[Frame]
    geoms: list[GeomInfo]
    materials: list[Material]
    verts: int
    tris: int
    flags: int
    plugins: frozenset[int]
    effects: list[Effect2D]
    bbox: tuple[float, ...] | None
    bsphere: tuple[float, float, float, float] | None


@dataclass(frozen=True, slots=True)
class Mesh:
    """Decoded geometry. ``tris`` is a triangle list (3 indices per triangle), ``mat_ids`` one material
    slot per triangle; ``positions``/``normals`` xyz interleaved, ``uv`` one ``array('f')`` (uv interleaved)
    per UV set, ``prelit``/``night`` RGBA bytes per vertex (``None`` when absent)."""

    positions: array          # array('f'), xyz interleaved
    normals: array | None     # array('f') or None
    uv: list[array]           # one array('f') (uv interleaved) per UV set
    prelit: array | None      # array('B') rgba
    night: array | None       # array('B') rgba (plugin 0x253F2F9)
    tris: array               # array('I'), triangle list (strips already converted)
    mat_ids: array            # array('H'), one per triangle
    frame: int


# ----------------------------------------------------------------------------- strips
# 58 % of the strip windows of the vanilla game are degenerate (strip joints), so the work is done on
# whole index buffers: equal neighbours are found with big-int XOR over the raw 4-byte indices and the
# triangles are filtered with ``itertools.compress`` -- no Python loop per index or per triangle.
_ZERO_IS_ONE = b"\x01" + bytes(255)        # bytes.translate: 0 -> 1, anything else -> 0
_NOT = b"\x01\x00" + bytes(254)             # bytes.translate on a 0/1 mask: logical not


def _eq_shift(raw: bytes, n: int, k: int) -> bytes:
    """0/1 mask of ``ix[i] == ix[i + k]`` for ``0 <= i < n - k``; ``raw`` = ``n`` 4-byte indices.

    Only equality matters, so the byte order of ``raw`` is irrelevant.
    """
    m = n - k
    x = (int.from_bytes(raw[:4 * m], "little") ^ int.from_bytes(raw[4 * k:4 * n], "little")).to_bytes(4 * m, "little")
    nz = (int.from_bytes(x[0::4], "little") | int.from_bytes(x[1::4], "little")
          | int.from_bytes(x[2::4], "little") | int.from_bytes(x[3::4], "little"))
    return nz.to_bytes(m, "little").translate(_ZERO_IS_ONE)


def _bad_windows(raw: bytes, n: int) -> bytes:
    """0/1 mask (``n - 2`` bytes, ``n >= 3``): window ``(ix[i], ix[i+1], ix[i+2])`` repeats a vertex."""
    e1 = _eq_shift(raw, n, 1)
    e2 = _eq_shift(raw, n, 2)
    return (int.from_bytes(e1[:-1], "little") | int.from_bytes(e1[1:], "little")
            | int.from_bytes(e2, "little")).to_bytes(n - 2, "little")


def _raw4(ix) -> bytes:
    if not (isinstance(ix, array) and ix.itemsize == 4):
        ix = array("I", ix)
    return ix.tobytes()


def _join_strips(buf, meshes, *, even: bool) -> tuple[bytes, int, list[tuple[int, int]]]:
    """Index buffers of BinMesh ``meshes`` back to back: ``(raw, total, [(start, n)])``.

    ``even=True`` pads a 4-byte zero before a strip that would start at an odd position, so that the
    global window parity equals the parity inside each strip (winding swap of odd triangles).
    """
    pieces = []
    spans = []
    pos = 0
    for _m, o, n in meshes:
        if even and pos & 1:
            pieces.append(b"\0\0\0\0")
            pos += 1
        pieces.append(buf[o:o + 4 * n])
        spans.append((pos, n))
        pos += n
    return b"".join(pieces), pos, spans


def _strip_counts(raw: bytes, total: int, spans) -> list[int]:
    """Non-degenerate triangles of each strip (windows across two strips are ignored)."""
    if total < 3:
        return [0] * len(spans)
    bad = _bad_windows(raw, total)
    return [n - 2 - bad.count(1, s, s + n - 2) if n >= 3 else 0 for s, n in spans]


def _strip_triangles(ix: array, spans) -> tuple[array, list[int]]:
    """Triangle list of the strips stored back to back in ``ix`` (every ``start`` even) and the number
    of triangles of each strip. Odd windows swap their first two vertices; windows with a repeated
    index, and windows that do not lie inside one strip, are dropped."""
    total = len(ix)
    if total < 3:
        return array("I"), [0] * len(spans)
    w = total - 2
    keep = bytearray(_bad_windows(ix.tobytes(), total).translate(_NOT))
    pos = 0
    for s, n in spans:                            # zero the windows between strips (joins, padding)
        if s > pos:
            keep[pos:s] = bytes(s - pos)
        pos = max(pos, s + n - 2)
    if w > pos:
        keep[pos:w] = bytes(w - pos)
    counts = [keep.count(1, s, s + n - 2) if n >= 3 else 0 for s, n in spans]
    a, b, c = ix[:-2], ix[1:-1], ix[2:]
    odd_a = a[1::2]
    a[1::2] = b[1::2]
    b[1::2] = odd_a
    if sum(counts) != w:
        a = array("I", compress(a, keep))
        b = array("I", compress(b, keep))
        c = array("I", compress(c, keep))
    out = array("I", bytes(12 * len(a)))
    out[0::3] = a
    out[1::3] = b
    out[2::3] = c
    return out, counts


def count_strip_triangles(ix) -> int:
    """Non-degenerate triangles of a triangle strip (C-speed, no per-triangle Python)."""
    n = len(ix)
    if n < 3:
        return 0
    return n - 2 - _bad_windows(_raw4(ix), n).count(1)


def strip_to_triangles(ix) -> array:
    """Triangle strip -> triangle list ``array('I')``.

    Odd triangles swap their first two vertices (consistent winding); triangles with a repeated index
    (strip joints) are dropped. Vectorised (big-int equality masks, slicing, ``itertools.compress``).
    """
    if len(ix) < 3:
        return array("I")
    if not isinstance(ix, array) or ix.typecode != "I":
        ix = array("I", ix)
    return _strip_triangles(ix, [(0, len(ix))])[0]


# ----------------------------------------------------------------------------- internal model
@dataclass(slots=True)
class _Geo:
    off: int                      # Geometry chunk header offset
    fmt: int = 0
    nv: int = 0
    ntri: int = 0
    nts: int = 0
    native: bool = False
    prelit_off: int = -1
    uv_offs: list = field(default_factory=list)
    tri_off: int = -1
    bsphere: tuple = (0.0, 0.0, 0.0, 0.0)
    pos_off: int = -1
    nrm_off: int = -1
    mats: list = field(default_factory=list)       # (rgba, tex, mask, fx, effects)
    mesh_strip: bool | None = None                 # BinMesh mode, None = no BinMesh
    meshes: list = field(default_factory=list)     # (mat, idx_off, n)
    night_off: int = -1
    night_flag: bool = False
    effects: list = field(default_factory=list)    # (type, pos, data)
    skin: bool = False
    breakable: bool = False
    pbox: tuple | None = None                      # cached position bbox (see _pos_bbox)
    pbox_done: bool = False


@dataclass(slots=True)
class _Clump:
    off: int
    version: int
    natomics: int | None = None
    frames: list = field(default_factory=list)     # (rot9, pos3, parent, name, hanim)
    geos: list = field(default_factory=list)       # _Geo
    atomics: list = field(default_factory=list)    # (frame, geom, matfx)
    ext: set = field(default_factory=set)
    col: tuple | None = None                       # (data_off, size) of CollisionModel


def _cstr(buf, a: int, n: int) -> str:
    return bytes(buf[a:a + n]).split(b"\0", 1)[0].decode("latin-1")


def _need(cond: bool, off: int, msg: str) -> None:
    if not cond:
        raise FormatError("dff", off, msg)


def _top_chunks(buf) -> tuple[list, bool]:
    """Top-level Clump chunks (in order) and whether a UVAnimDict precedes them."""
    n = len(buf)
    off = 0
    clumps = []
    uvanim = False
    while off + 12 <= n:
        t = _HDR.unpack_from(buf, off)[0]
        if t == _UVANIMDICT:
            uvanim = True
            off = _top(buf, off).end
            continue
        if t != _CLUMP:
            break
        ch = _top(buf, off)
        clumps.append(ch)
        off = ch.end
    if not clumps:
        head = bytes(buf[:4]).hex() if n else "empty"
        raise FormatError("dff", off, f"no Clump chunk (first bytes {head})")
    return clumps, uvanim


def _ext_ids(buf, ch, plugins: set) -> list:
    kids = _kids(buf, ch.data_off, ch.end)
    for k in kids:
        plugins.add(k.type)
    return kids


def _parse_frames(buf, ch, cl: _Clump, plugins: set) -> None:
    kids = iter(_kids(buf, ch.data_off, ch.end))
    st = next(kids, None)
    _need(st is not None and st.type == _STRUCT and st.size >= 4, ch.data_off, "FrameList without Struct")
    (n,) = struct.unpack_from("<I", buf, st.data_off)
    _need(4 + 56 * n <= st.size, st.data_off, f"FrameList struct too short for {n} frames")
    frames = []
    p = st.data_off + 4
    for i in range(n):
        v = _FRAME.unpack_from(buf, p)
        p += 56
        parent = v[12]
        _need(-1 <= parent < n, p - 8, f"frame {i} has parent {parent} outside 0..{n - 1}")
        frames.append([v[:9], v[9:12], parent, None, False])
    i = 0
    for e in kids:
        if e.type != _EXT:
            continue
        if i >= n:
            break
        for k in _ext_ids(buf, e, plugins):
            if k.type == _FRAMENAME:
                frames[i][3] = _cstr(buf, k.data_off, k.size)
            elif k.type == _HANIM:
                frames[i][4] = True
        i += 1
    cl.frames = frames


def _parse_texture(buf, ch, plugins: set) -> tuple[str | None, str | None]:
    names = []
    for k in _kids(buf, ch.data_off, ch.end):
        if k.type == _STRING and len(names) < 2:
            names.append(_cstr(buf, k.data_off, k.size))
        elif k.type == _EXT:
            _ext_ids(buf, k, plugins)
    tex = names[0] if names and names[0] else None    # 6 vanilla materials have an empty name
    mask = names[1] if len(names) > 1 and names[1] else None
    return tex, mask


def _f6(v: float) -> float:
    """A stored f32 as a short decimal (``0.09000000357`` -> ``0.09``); non-finite values stay as they are."""
    return round(v, 6) if math.isfinite(v) else v


def _tex_at(buf, p: int, end: int, plugins: set) -> tuple[str | None, int]:
    """The Texture chunk at ``p`` inside a MatFX stream -> ``(name, offset after it)``."""
    _need(p + 12 <= end, p, "MatFX texture header overruns the chunk")
    t, s, v = _unpack_hdr(buf, p)
    e = p + 12 + s
    _need(t == _TEXTURE and e <= end, p, f"MatFX expects a Texture chunk, got 0x{t:X}")
    tex, _mask = _parse_texture(buf, _tnew(_C, (t, s, v, p + 12, e)), plugins)
    return tex, e


def _matfx_values(buf, e, plugins: set, out: dict) -> None:
    """Decode the RpMatFX material stream (``u32 type`` + up to two effects) into ``out``.

    Effect records (librw ``readMaterialMatFX`` layout): 1 bump ``f32 coef, i32 has, [Texture], i32 has,
    [Texture]``; 2 env ``f32 coef, i32 fb_alpha, i32 has, [Texture]``; 4 dual ``i32 src, i32 dst, i32 has,
    [Texture]``; 5 UV transform (no data); 0 nothing. Malformed data keeps what was read so far.
    """
    p, end = e.data_off + 4, e.end
    u = struct.unpack_from
    try:
        for _ in range(2):
            if p + 4 > end:
                return
            (kind,) = u("<I", buf, p)
            p += 4
            if kind == 1:
                coef, has = u("<fi", buf, p)
                p += 8
                bumped = tex = None
                if has:
                    bumped, p = _tex_at(buf, p, end, plugins)
                (has2,) = u("<i", buf, p)
                p += 4
                if has2:
                    tex, p = _tex_at(buf, p, end, plugins)
                out["bump"] = {"coef": _f6(coef), "tex": tex, "bumped_tex": bumped}
            elif kind == 2:
                coef, fb, has = u("<fii", buf, p)
                p += 12
                tex = None
                if has:
                    tex, p = _tex_at(buf, p, end, plugins)
                out["env"] = {"coef": _f6(coef), "fb_alpha": bool(fb), "tex": tex}
            elif kind == 4:
                src, dst, has = u("<iii", buf, p)
                p += 12
                tex = None
                if has:
                    tex, p = _tex_at(buf, p, end, plugins)
                out["dual"] = {"src_blend": src, "dst_blend": dst, "tex": tex}
            elif kind == 5:
                out["uv_transform"] = True
            elif kind != 0:
                return
    except (struct.error, IndexError, FormatError):
        return


def _parse_material(buf, ch, plugins: set) -> tuple:
    rgba = 0xFFFFFFFF
    tex = mask = None
    fx = 0
    vals: dict = {}
    for k in _kids(buf, ch.data_off, ch.end):
        if k.type == _STRUCT:
            _need(k.size >= 16, k.data_off, "Material struct shorter than 16 bytes")
            r, g, b, a = buf[k.data_off + 4:k.data_off + 8]
            rgba = (r << 24) | (g << 16) | (b << 8) | a
            if k.size >= 28:                       # ambient, specular, diffuse (RW >= 3.4)
                vals["surface"] = [_f6(x) for x in struct.unpack_from("<3f", buf, k.data_off + 16)]
        elif k.type == _TEXTURE:
            tex, mask = _parse_texture(buf, k, plugins)
        elif k.type == _EXT:
            for e in _ext_ids(buf, k, plugins):
                if e.type == _MATFX and e.size >= 4:
                    fx |= _MATFX_BITS.get(struct.unpack_from("<I", buf, e.data_off)[0], 0)
                    _matfx_values(buf, e, plugins, vals)
                elif e.type == _UVANIM:
                    fx |= FX_UVANIM
                elif e.type == _REFLECTION:
                    fx |= FX_REFLECTION
                    if e.size >= 20:
                        sx, sy, ox, oy, inten = struct.unpack_from("<5f", buf, e.data_off)
                        vals["reflection"] = {"scale": [_f6(sx), _f6(sy)], "offset": [_f6(ox), _f6(oy)],
                                              "intensity": _f6(inten)}
                elif e.type == _SPECULAR:
                    fx |= FX_SPECULAR
                    if e.size >= 4:
                        (lvl,) = struct.unpack_from("<f", buf, e.data_off)
                        name = _cstr(buf, e.data_off + 4, min(24, e.size - 4)) if e.size > 4 else ""
                        vals["specular"] = {"level": _f6(lvl), "tex": name or None}
    return rgba, tex, mask, fx, vals


def _parse_matlist(buf, ch, plugins: set) -> list:
    kids = iter(_kids(buf, ch.data_off, ch.end))
    st = next(kids, None)
    _need(st is not None and st.type == _STRUCT and st.size >= 4, ch.data_off, "MaterialList without Struct")
    (n,) = struct.unpack_from("<I", buf, st.data_off)
    _need(4 + 4 * n <= st.size, st.data_off, f"MaterialList struct too short for {n} slots")
    refs = struct.unpack_from(f"<{n}i", buf, st.data_off + 4)
    parsed = [_parse_material(buf, k, plugins) for k in kids if k.type == _MATERIAL]
    out: list = []
    j = 0
    for i, r in enumerate(refs):
        if r < 0:
            _need(j < len(parsed), ch.data_off, f"MaterialList slot {i} has no Material chunk")
            out.append(parsed[j])
            j += 1
        else:
            _need(r < i, ch.data_off, f"MaterialList slot {i} references slot {r}")
            out.append(out[r])
    return out


def _fx_data(buf, p: int, size: int, typ: int) -> dict:
    """Decode the fields of a 2dEffect entry (unknown/short entries -> ``{"size": n}``)."""
    f = struct.unpack_from
    if typ == 0 and size >= 76:                               # light
        r, g, b, a = buf[p:p + 4]
        far, rng, csize, ssize = f("<4f", buf, p + 4)
        flash, refl, flare, smul, fl1 = buf[p + 20:p + 25]
        zdist, fl2 = buf[p + 73:p + 75]
        return {"rgba": (r << 24) | (g << 16) | (b << 8) | a, "corona": _cstr(buf, p + 25, 24),
                "shadow": _cstr(buf, p + 49, 24), "range": rng, "corona_size": csize, "far_clip": far,
                "shadow_size": ssize, "flash": flash, "reflection": refl, "flare": flare,
                "shadow_mult": smul, "shadow_z": zdist, "flags": fl1 | (fl2 << 8)}
    if typ == 1 and size >= 24:                               # particle
        return {"name": _cstr(buf, p, 24)}
    if typ == 3 and size >= 56:                               # ped attractor
        at = struct.unpack_from("<b", buf, p)[0]
        return {"atype": at, "queue_dir": list(f("<3f", buf, p + 4)), "use_dir": list(f("<3f", buf, p + 16)),
                "fwd_dir": list(f("<3f", buf, p + 28)), "script": _cstr(buf, p + 40, 8),
                "probability": f("<i", buf, p + 48)[0]}
    if typ == 6 and size >= 40:                               # entry/exit
        d = {"enter_angle": f("<f", buf, p)[0], "radius": list(f("<2f", buf, p + 4)),
             "exit": list(f("<3f", buf, p + 12)), "exit_angle": f("<f", buf, p + 24)[0],
             "interior": f("<h", buf, p + 28)[0], "flags": buf[p + 30], "sky": buf[p + 31],
             "name": _cstr(buf, p + 32, 8)}
        if size >= 43:
            d.update(time_on=buf[p + 40], time_off=buf[p + 41], flags2=buf[p + 42])
        return d
    if typ == 7 and size >= 86:                               # roadsign
        return {"size": list(f("<2f", buf, p)), "rot": list(f("<3f", buf, p + 8)), "flags": f("<H", buf, p + 20)[0],
                "text": [_cstr(buf, p + 22 + 16 * k, 16) for k in range(4)]}
    if typ == 8 and size >= 4:                                # trigger point (slot machine wheel)
        return {"id": f("<i", buf, p)[0]}
    if typ == 9 and size >= 12:                               # cover point
        return {"dir": list(f("<2f", buf, p)), "usage": f("<I", buf, p + 8)[0]}
    if typ == 10 and size >= 37:                              # escalator
        return {"bottom": list(f("<3f", buf, p)), "top": list(f("<3f", buf, p + 12)),
                "end": list(f("<3f", buf, p + 24)), "up": bool(buf[p + 36])}
    if typ == 4:                                              # sun glare: no data
        return {}
    return {"size": size}


def _parse_effects(buf, ch) -> list:
    _need(ch.size >= 4, ch.data_off, "2dEffect chunk shorter than 4 bytes")
    (n,) = struct.unpack_from("<I", buf, ch.data_off)
    p = ch.data_off + 4
    end = ch.end
    out = []
    for i in range(n):
        _need(p + 20 <= end, p, f"2dEffect entry {i} header overruns the chunk")
        x, y, z, typ, size = struct.unpack_from("<3fII", buf, p)
        p += 20
        _need(p + size <= end, p, f"2dEffect entry {i} ({size} bytes) overruns the chunk")
        out.append((typ, (x, y, z), _fx_data(buf, p, size, typ)))
        p += size
    return out


def _parse_geo_struct(buf, st, g: _Geo) -> None:
    o, end = st.data_off, st.end
    _need(st.size >= 16, o, "Geometry struct shorter than 16 bytes")
    fmt, ntri, nv, nmorph = _GSTRUCT.unpack_from(buf, o)
    _need(ntri >= 0 and nv >= 0, o, f"negative counts (tris {ntri}, verts {nv})")
    p = o + 16
    if rw_version(st.libid) < 0x34000:
        p += 12                                   # ambient, specular, diffuse (pitfall #4)
    flags = fmt & 0xFFFF
    nts = (fmt >> 16) & 0xFF or (2 if flags & GEO_TEXTURED2 else 1 if flags & GEO_TEXTURED else 0)
    g.fmt, g.nv, g.ntri, g.nts, g.native = fmt, nv, ntri, nts, bool(fmt & GEO_NATIVE)
    if not g.native:
        if flags & GEO_PRELIT:
            g.prelit_off = p
            p += 4 * nv
        for _ in range(nts):
            g.uv_offs.append(p)
            p += 8 * nv
        g.tri_off = p
        p += 8 * ntri
        _need(p <= end, o, f"geometry arrays ({nv} verts, {ntri} tris) overrun the struct")
    if nmorph and not g.native:
        _need(p + 24 <= end, p, "morph target header overruns the struct")
        x, y, z, r, has_v, has_n = _MORPH.unpack_from(buf, p)
        g.bsphere = (x, y, z, r)
        p += 24
        if has_v:
            g.pos_off = p
            p += 12 * nv
        if has_n:
            g.nrm_off = p
            p += 12 * nv
        _need(p <= end, o, "morph target arrays overrun the struct")
    elif nmorph and st.size >= p - o + 16:        # native: bounding sphere may still be there
        g.bsphere = struct.unpack_from("<4f", buf, p)


def _parse_binmesh(buf, ch, g: _Geo) -> None:
    o, end = ch.data_off, ch.end
    _need(ch.size >= 12, o, "BinMesh chunk shorter than 12 bytes")
    mflags, nmesh, _nidx = struct.unpack_from("<III", buf, o)
    q = o + 12
    meshes = []
    for i in range(nmesh):
        _need(q + 8 <= end, q, f"BinMesh mesh {i} header overruns the chunk")
        n, mat = struct.unpack_from("<II", buf, q)
        q += 8
        if not g.native:
            _need(q + 4 * n <= end, q, f"BinMesh mesh {i} indices ({n}) overrun the chunk")
        meshes.append((mat, q, n))
        if not g.native:
            q += 4 * n
    g.mesh_strip = mflags == 1
    g.meshes = meshes


def _parse_geometry(buf, ch, plugins: set) -> _Geo:
    g = _Geo(ch.data_off - 12)
    seen_struct = False
    for k in _kids(buf, ch.data_off, ch.end):
        if k.type == _STRUCT and not seen_struct:
            seen_struct = True
            _parse_geo_struct(buf, k, g)
        elif k.type == _MATLIST:
            g.mats = _parse_matlist(buf, k, plugins)
        elif k.type == _EXT:
            for e in _ext_ids(buf, k, plugins):
                if e.type == _BINMESH:
                    _parse_binmesh(buf, e, g)
                elif e.type == _NIGHT and e.size >= 4:
                    if struct.unpack_from("<I", buf, e.data_off)[0]:
                        g.night_flag = True
                        if e.size >= 4 + 4 * g.nv:
                            g.night_off = e.data_off + 4
                elif e.type == _2DFX:
                    g.effects = _parse_effects(buf, e)
                elif e.type == _SKIN:
                    g.skin = True
                elif e.type == _BREAKABLE and e.size >= 4:
                    g.breakable = struct.unpack_from("<I", buf, e.data_off)[0] != 0
    _need(seen_struct, ch.data_off, "Geometry without Struct")
    return g


def _parse_clump(buf, ch, plugins: set) -> _Clump:
    cl = _Clump(ch.data_off - 12, rw_version(ch.libid))
    seen = set()
    for k in _kids(buf, ch.data_off, ch.end):
        t = k.type
        seen.add(t)
        if t == _STRUCT and cl.natomics is None:
            if k.size >= 4:
                cl.natomics = struct.unpack_from("<I", buf, k.data_off)[0]
        elif t == _FRAMELIST:
            _parse_frames(buf, k, cl, plugins)
        elif t == _GEOMLIST:
            kids = iter(_kids(buf, k.data_off, k.end))
            st = next(kids, None)
            _need(st is not None and st.type == _STRUCT and st.size >= 4, k.data_off, "GeometryList without Struct")
            cl.geos = [_parse_geometry(buf, gk, plugins) for gk in kids if gk.type == _GEOMETRY]
        elif t == _ATOMIC:
            fi = gi = None
            matfx = False
            for a in _kids(buf, k.data_off, k.end):
                if a.type == _STRUCT and fi is None:
                    _need(a.size >= 8, a.data_off, "Atomic struct shorter than 8 bytes")
                    fi, gi = struct.unpack_from("<II", buf, a.data_off)
                elif a.type == _GEOMETRY:                       # geometry stored inside the atomic
                    cl.geos.append(_parse_geometry(buf, a, plugins))
                    gi = len(cl.geos) - 1
                elif a.type == _EXT:
                    for e in _ext_ids(buf, a, plugins):
                        if e.type == _MATFX and e.size >= 4 and struct.unpack_from("<I", buf, e.data_off)[0]:
                            matfx = True
            _need(fi is not None, k.data_off, "Atomic without Struct")
            cl.atomics.append((fi, gi, matfx))
        elif t == _EXT:
            for e in _ext_ids(buf, k, plugins):
                cl.ext.add(e.type)
                if e.type == _COLMODEL and cl.col is None:
                    cl.col = (e.data_off, e.size)
    _need(_FRAMELIST in seen and (_GEOMLIST in seen or not cl.atomics or cl.geos), cl.off,
          "Clump without FrameList/GeometryList")
    nf, ng = len(cl.frames), len(cl.geos)
    for fi, gi, _m in cl.atomics:
        _need(fi < nf and gi < ng, cl.off, f"atomic references frame {fi}/{nf} geometry {gi}/{ng}")
    return cl


def _walk(buf) -> tuple[list[_Clump], bool, set]:
    plugins: set = set()
    tops, uvanim = _top_chunks(buf)
    return [_parse_clump(buf, ch, plugins) for ch in tops], uvanim, plugins


def _index_array(buf, off: int, n: int) -> array:
    ix = array("I")
    ix.frombytes(bytes(buf[off:off + 4 * n]))
    if _BIG:
        ix.byteswap()
    return ix


def _geo_tris(buf, g: _Geo) -> int:
    if g.mesh_strip is None or g.native:
        return g.ntri
    if not g.mesh_strip:
        return sum(n // 3 for _m, _o, n in g.meshes)
    raw, total, spans = _join_strips(buf, g.meshes, even=False)
    return sum(_strip_counts(raw, total, spans))


# ----------------------------------------------------------------------------- matrices / bounds
_IDENT = ((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0))


def _model_matrices(frames: list) -> list:
    """Model-space (rot9, pos3) per frame; roots (and forward-referencing parents) are identity."""
    out: list = []
    for i, (rot, pos, parent, _n, _h) in enumerate(frames):
        if parent < 0 or parent >= i:
            out.append(_IDENT)
            continue
        pr, pp = out[parent]
        # columns right/up/at: p' = x*right + y*up + z*at + pos
        r = []
        for c in range(3):
            vx, vy, vz = rot[3 * c], rot[3 * c + 1], rot[3 * c + 2]
            r += [vx * pr[0] + vy * pr[3] + vz * pr[6], vx * pr[1] + vy * pr[4] + vz * pr[7],
                  vx * pr[2] + vy * pr[5] + vz * pr[8]]
        x, y, z = pos
        t = (x * pr[0] + y * pr[3] + z * pr[6] + pp[0], x * pr[1] + y * pr[4] + z * pr[7] + pp[1],
             x * pr[2] + y * pr[5] + z * pr[8] + pp[2])
        out.append((tuple(r), t))
    return out


def _xform(m, x: float, y: float, z: float) -> tuple[float, float, float]:
    r, t = m
    return (x * r[0] + y * r[3] + z * r[6] + t[0], x * r[1] + y * r[4] + z * r[7] + t[1],
            x * r[2] + y * r[5] + z * r[8] + t[2])


#: Identity as a 12-float frame matrix (``Frame.matrix`` layout).
IDENTITY: tuple = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)


def model_matrices(frames: list) -> list[tuple]:
    """Model-space 12-float matrices of :attr:`DffInfo.frames` (same layout as :attr:`Frame.matrix`).

    A root frame (and a frame whose parent comes later, which the engine does not support) is the model
    origin: identity, like the engine's model info, which ignores the root transform. Children are
    ``parent * local``. Example: ``mm = model_matrices(info.frames); x, y, z = mm[i][9:12]``.
    """
    out: list[tuple] = []
    for i, f in enumerate(frames):
        m = f.matrix if len(f.matrix) == 12 else IDENTITY
        if f.parent < 0 or f.parent >= i:
            out.append(IDENTITY)
            continue
        a = out[f.parent]
        r = []
        for c in range(3):
            x, y, z = m[3 * c], m[3 * c + 1], m[3 * c + 2]
            r += [x * a[0] + y * a[3] + z * a[6], x * a[1] + y * a[4] + z * a[7], x * a[2] + y * a[5] + z * a[8]]
        x, y, z = m[9], m[10], m[11]
        r += [x * a[0] + y * a[3] + z * a[6] + a[9], x * a[1] + y * a[4] + z * a[7] + a[10],
              x * a[2] + y * a[5] + z * a[8] + a[11]]
        out.append(tuple(r))
    return out


def _pos_bbox(buf, g: _Geo) -> tuple | None:
    """Bounding box of the vertex positions in the geometry's own space (cached on ``g``)."""
    if not g.pbox_done:
        g.pbox_done = True
        if g.pos_off >= 0 and g.nv:
            p = array("f")
            p.frombytes(bytes(buf[g.pos_off:g.pos_off + 12 * g.nv]))
            if _BIG:
                p.byteswap()
            xs, ys, zs = p[0::3], p[1::3], p[2::3]
            g.pbox = (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
    return g.pbox


def _local_bbox(buf, g: _Geo) -> tuple | None:
    pb = _pos_bbox(buf, g)
    if pb is not None:
        return pb
    x, y, z, r = g.bsphere
    if r > 0:
        return (x - r, y - r, z - r, x + r, y + r, z + r)
    return None


def _bounds(buf, clumps: list[_Clump]) -> tuple[tuple | None, tuple | None]:
    boxes = []
    spheres = []
    for cl in clumps:
        mats = _model_matrices(cl.frames)
        used = [(gi, fi) for fi, gi, _m in cl.atomics] or [(gi, -1) for gi in range(len(cl.geos))]
        seen = set()
        for gi, fi in used:
            if (gi, fi) in seen:
                continue
            seen.add((gi, fi))
            g = cl.geos[gi]
            lb = _local_bbox(buf, g)
            if lb is None:
                continue
            m = mats[fi] if fi >= 0 else _IDENT
            sx, sy, sz, sr = g.bsphere
            if m is _IDENT:
                boxes.append(lb)
                spheres.append((sx, sy, sz, sr))
                continue
            pts = [_xform(m, x, y, z) for x in (lb[0], lb[3]) for y in (lb[1], lb[4]) for z in (lb[2], lb[5])]
            boxes.append((min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts),
                          max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts)))
            spheres.append((*_xform(m, sx, sy, sz), sr))
    if not boxes:
        return None, None
    bb = (min(b[0] for b in boxes), min(b[1] for b in boxes), min(b[2] for b in boxes),
          max(b[3] for b in boxes), max(b[4] for b in boxes), max(b[5] for b in boxes))
    if not all(math.isfinite(v) for v in bb):
        return None, None
    if len(spheres) == 1 and spheres[0][3] > 0:
        return bb, tuple(spheres[0])              # the geometry's own sphere (what the engine uses)
    cx, cy, cz = (bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2, (bb[2] + bb[5]) / 2
    return bb, (cx, cy, cz, math.dist((cx, cy, cz), (bb[0], bb[1], bb[2])))


# ----------------------------------------------------------------------------- public API
#: Exceptions turned into ``FormatError`` by the defensive net of the public functions.
_DEFENSIVE = (struct.error, IndexError, ValueError, OverflowError, MemoryError)


def _walk_checked(buf) -> tuple[list[_Clump], bool, set]:
    """The chunk walk shared by :func:`scan_dff` and :func:`decode_geometries` (``selftest`` runs both on
    one walk). Only ``FormatError`` escapes."""
    try:
        return _walk(buf)
    except FormatError:
        raise
    except _DEFENSIVE as e:  # defensive net
        raise FormatError("dff", 0, f"malformed DFF: {type(e).__name__}: {e}") from None


def _scan_walked(buf, walked) -> DffInfo:
    try:
        return _scan(buf, walked)
    except FormatError:
        raise
    except _DEFENSIVE as e:
        raise FormatError("dff", 0, f"malformed DFF: {type(e).__name__}: {e}") from None


def _decode_walked(buf, walked) -> list[Mesh]:
    try:
        return [_decode(buf, g, fr) for g, fr in _geo_frames(walked[0])]
    except FormatError:
        raise
    except _DEFENSIVE as e:
        raise FormatError("dff", 0, f"malformed DFF: {type(e).__name__}: {e}") from None


def scan_dff(buf) -> DffInfo:
    """Walk a DFF without decoding full geometry; ``flags`` = bits as in the index ``dff.flags``.

    Reads every clump (``player.img`` entries hold 3). Raises ``FormatError(kind="dff")`` on malformed
    data, never ``IndexError``/``struct.error``.
    """
    return _scan_walked(buf, _walk_checked(buf))


def _scan(buf, walked) -> DffInfo:
    clumps, uvanim, plugins = walked
    frames: list[Frame] = []
    geoms: list[GeomInfo] = []
    materials: list[Material] = []
    effects: list[Effect2D] = []
    flags = F_UVANIM if uvanim else 0
    natomics = 0
    verts = tris = 0
    for cl in clumps:
        fbase, gbase = len(frames), len(geoms)
        natomics += cl.natomics if cl.natomics is not None else len(cl.atomics)
        with_atomic = {fi for fi, _g, _m in cl.atomics}
        geo_frame: dict[int, int] = {}
        for fi, gi, matfx in cl.atomics:
            geo_frame.setdefault(gi, fi)
            if matfx:
                flags |= F_MATFX
        for i, (rot, pos, parent, name, hanim) in enumerate(cl.frames):
            frames.append(Frame(fbase + i, fbase + parent if parent >= 0 else -1, name, i in with_atomic,
                                tuple(rot) + tuple(pos)))
            if hanim:
                flags |= F_HANIM
        for gi, g in enumerate(cl.geos):
            nt = _geo_tris(buf, g)
            verts += g.nv
            tris += nt
            fr = geo_frame.get(gi)
            strip = g.mesh_strip if g.mesh_strip is not None else bool(g.fmt & GEO_TRISTRIP)
            geoms.append(GeomInfo(gbase + gi, g.fmt, g.nv, nt, g.nts, strip,
                                  fbase + fr if fr is not None else -1, tuple(g.bsphere), g.off,
                                  _pos_bbox(buf, g)))
            for mi, (rgba, tex, mask, fx, vals) in enumerate(g.mats):
                slot = _COLOR_SLOTS.get((rgba >> 24, (rgba >> 16) & 0xFF, (rgba >> 8) & 0xFF))
                materials.append(Material(gbase + gi, mi, rgba, tex, mask, fx, slot, dict(vals)))
                if fx & (FX_ENV | FX_BUMP | FX_DUAL):
                    flags |= F_MATFX
                if fx & FX_UVANIM:
                    flags |= F_UVANIM
                if fx & FX_REFLECTION:
                    flags |= F_REFLECTION
                if fx & FX_SPECULAR:
                    flags |= F_SPECULAR
            for typ, pos, data in g.effects:
                effects.append(Effect2D(len(effects), typ, EFFECT_TYPES.get(typ, f"type{typ}"), pos, data))
            if g.fmt & GEO_PRELIT:
                flags |= F_PRELIT
            if g.nrm_off >= 0:
                flags |= F_NORMALS
            if g.night_flag:
                flags |= F_NIGHT
            if g.skin:
                flags |= F_SKIN
            if g.breakable:
                flags |= F_BREAKABLE
        if cl.col is not None:
            flags |= F_COL
    if effects:
        flags |= F_2DFX
    if len(clumps) > 1:
        flags |= F_MULTI
    bbox, bsph = _bounds(buf, clumps)
    return DffInfo(clumps[0].version, len(clumps), natomics, frames, geoms, materials, verts, tris, flags,
                   frozenset(plugins), effects, bbox, bsph)


def find_embedded_col(buf) -> tuple[int, int] | None:
    """``(offset, size)`` of the embedded collision (``CollisionModel 0x253F2FA``, vehicles) or ``None``.

    The bytes ``buf[offset:offset + size]`` are a COL3 model: feed them to :func:`satk.formats.col.iter_col`.
    """
    try:
        for ch in _top_chunks(buf)[0]:
            for k in _kids(buf, ch.data_off, ch.end):
                if k.type == _EXT:
                    for e in _kids(buf, k.data_off, k.end):
                        if e.type == _COLMODEL:
                            return e.data_off, e.size
    except (struct.error, IndexError) as e:
        raise FormatError("dff", 0, f"malformed DFF: {e}") from None
    return None


def _farray(buf, off: int, n: int) -> array:
    a = array("f")
    a.frombytes(bytes(buf[off:off + 4 * n]))
    if _BIG:
        a.byteswap()
    return a


def _decode(buf, g: _Geo, frame: int) -> Mesh:
    if g.native:
        raise FormatError("dff", g.off, "native (console) geometry has no PC vertex arrays")
    nv = g.nv
    pos = _farray(buf, g.pos_off, 3 * nv) if g.pos_off >= 0 else array("f")
    nrm = _farray(buf, g.nrm_off, 3 * nv) if g.nrm_off >= 0 else None
    uv = [_farray(buf, o, 2 * nv) for o in g.uv_offs]
    prelit = array("B", bytes(buf[g.prelit_off:g.prelit_off + 4 * nv])) if g.prelit_off >= 0 else None
    if g.night_flag and g.night_off < 0:
        raise FormatError("dff", g.off, "night colour chunk shorter than 4 * verts")
    night = array("B", bytes(buf[g.night_off:g.night_off + 4 * nv])) if g.night_off >= 0 else None
    tris = array("I")
    mats = array("H")
    if g.mesh_strip is not None:
        for mat, _o, _n in g.meshes:
            _need(mat < 0x10000, g.off, f"material index {mat} too large")
        if g.mesh_strip:                          # all strips of the geometry in one pass
            raw, _total, spans = _join_strips(buf, g.meshes, even=True)
            ix = array("I")
            ix.frombytes(raw)
            if _BIG:
                ix.byteswap()
            tris, counts = _strip_triangles(ix, spans)
            for (mat, _o, _n), k in zip(g.meshes, counts):
                mats.extend(array("H", [mat]) * k)
        else:
            for mat, o, n in g.meshes:
                t = _index_array(buf, o, n)[:n - n % 3]
                tris.extend(t)
                mats.extend(array("H", [mat]) * (len(t) // 3))
    else:                                         # no BinMesh: the struct triangle list (v1, v0, mat, v2)
        raw = array("H")
        raw.frombytes(bytes(buf[g.tri_off:g.tri_off + 8 * g.ntri]))
        if _BIG:
            raw.byteswap()
        tris = array("I", bytes(12 * g.ntri))
        tris[0::3] = array("I", raw[1::4])
        tris[1::3] = array("I", raw[0::4])
        tris[2::3] = array("I", raw[3::4])
        mats = raw[2::4]
    if tris and max(tris) >= nv:
        raise FormatError("dff", g.off, f"triangle index {max(tris)} >= vertex count {nv}")
    return Mesh(pos, nrm, uv, prelit, night, tris, mats, frame)


def _geo_frames(clumps: list[_Clump]):
    """Yield ``(_Geo, global frame)`` for every geometry in order."""
    fbase = 0
    for cl in clumps:
        first: dict[int, int] = {}
        for fi, gi, _m in cl.atomics:
            first.setdefault(gi, fi)
        for gi, g in enumerate(cl.geos):
            fr = first.get(gi)
            yield g, fbase + fr if fr is not None else -1
        fbase += len(cl.frames)


def decode_geometry(buf, geom_off: int) -> Mesh:
    """Decode one geometry (``GeomInfo.geom_off``); strips -> triangles, degenerate dropped.

    ``geom_off`` is the offset of the Geometry chunk header. ``FormatError`` if there is no geometry there.
    """
    try:
        for g, fr in _geo_frames(_walk(buf)[0]):
            if g.off == geom_off:
                return _decode(buf, g, fr)
    except FormatError:
        raise
    except _DEFENSIVE as e:
        raise FormatError("dff", geom_off, f"malformed geometry: {type(e).__name__}: {e}") from None
    raise FormatError("dff", geom_off, "no Geometry chunk starts at this offset")


def decode_geometries(buf) -> list[Mesh]:
    """All geometries of a DFF in GeometryList order (all clumps)."""
    return _decode_walked(buf, _walk_checked(buf))
