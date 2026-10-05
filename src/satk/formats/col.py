"""Collision files (``*.col``: COLL/COL2/COL3/COL4), SPEC §4.2 ``satk.formats.col``. Stdlib only. F2.

A ``.col`` holds many models back to back; each starts with a 32-byte header::

    char fourcc[4] (COLL|COL2|COL3|COL4); u32 size (bytes after this field); char name[22]; u16 modelId

* **COLL** (v1): bounds ``radius, center[3], min[3], max[3]``, then counted arrays: u32 + spheres (20 B),
  u32 + lines (24 B, skipped), u32 + boxes (28 B), u32 + vertices (12 B floats), u32 + faces (16 B).
* **COL2/3/4**: bounds ``min[3], max[3], center[3], radius``; ``u16 spheres, boxes, faces; u8 lines; pad;
  u32 flags``; offsets of spheres/boxes/lines/vertices/faces/planes (relative to ``fourcc + 4``);
  COL3+: ``u32 shadow faces`` + shadow vertex/face offsets; COL4: one more u32. Vertices are
  ``int16[3] / 128``; their count is not stored (= highest face index + 1, as the engine computes).

Models are matched to IDE definitions ONLY by name; the header ``modelId`` is not trusted (pitfall #13,
``Streaming.cpp:1162``). Zero padding (IMG sectors) or an unknown fourcc after the first model ends the
file, like ``CFileLoader::LoadCollisionFile``.

Example::

    for m in iter_col(blob):
        print(m.name, m.version, m.faces, m.bbox)
"""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass
from typing import Iterator

from .rw import FormatError

__all__ = ["ColModel", "iter_col", "FOURCC_VERSION"]

#: fourcc -> ``ColModel.version``.
FOURCC_VERSION = {b"COLL": 1, b"COL2": 2, b"COL3": 3, b"COL4": 4}
_HEAD = struct.Struct("<4sI22sH")
_V2 = struct.Struct("<10f3HBxI6I")      # bounds, counts, flags, 6 offsets (76 bytes)
_V3 = struct.Struct("<3I")               # shadow faces, shadow verts offset, shadow faces offset


@dataclass(frozen=True, slots=True)
class ColModel:
    """One collision model.

    ``idx`` = position in the file; ``size`` = bytes of the whole record (header included);
    ``bbox`` = ``(minx, miny, minz, maxx, maxy, maxz)``; ``bsphere`` = ``(x, y, z, r)``;
    ``surfaces`` = ``{surface material id: number of spheres + boxes + faces using it}``.
    """

    idx: int
    version: int
    name: str
    hdr_model_id: int
    size: int
    bbox: tuple[float, ...]
    bsphere: tuple[float, float, float, float]
    spheres: int
    boxes: int
    verts: int
    faces: int
    shadow_faces: int
    surfaces: dict[int, int]


def _need(cond: bool, off: int, msg: str) -> None:
    if not cond:
        raise FormatError("col", off, msg)


def _mat_counts(buf, start: int, count: int, stride: int, at: int, lo: int, hi: int, what: str) -> Counter:
    """Histogram of the surface byte at ``at`` of ``count`` records; records must lie in ``[lo, hi)``."""
    end = start + count * stride
    _need(start >= lo and end <= hi, start, f"{count} {what} ({stride} B each) outside the model")
    return Counter(bytes(buf[start + at:end:stride]))


def _mesh_vertices(buf, face: int, count: int, verts: int, lo: int, hi: int, prefix: str = "") -> int:
    """Infer the vertex count from validated faces and validate the compressed vertex span."""
    _need(face >= lo and face + count * 8 <= hi, face, f"{count} {prefix}faces outside the model")
    indices = struct.unpack_from(f"<{4 * count}H", buf, face)
    nv = max(max(indices[0::4]), max(indices[1::4]), max(indices[2::4])) + 1
    _need(verts >= lo and verts + nv * 6 <= hi, verts, f"{nv} {prefix}vertices outside the model")
    return nv


def _v1(buf, o: int, end: int, idx: int, name: str, mid: int) -> ColModel:
    _need(o + 40 <= end, o, "COLL bounds overrun the model")
    r, cx, cy, cz, x0, y0, z0, x1, y1, z1 = struct.unpack_from("<10f", buf, o)
    p = o + 40
    surf: Counter = Counter()
    counts = []
    for what, stride, mat_at in (("spheres", 20, 16), ("lines", 24, -1), ("boxes", 28, 24), ("vertices", 12, -1),
                                 ("faces", 16, 12)):
        _need(p + 4 <= end, p, f"COLL {what} count overruns the model")
        (n,) = struct.unpack_from("<I", buf, p)
        p += 4
        if mat_at >= 0:
            surf += _mat_counts(buf, p, n, stride, mat_at, o, end, what)
        else:
            _need(p + n * stride <= end, p, f"COLL {n} {what} overrun the model")
        p += n * stride
        counts.append(n)
    ns, _nl, nb, nv, nf = counts
    return ColModel(idx, 1, name, mid, end - o + 32, (x0, y0, z0, x1, y1, z1), (cx, cy, cz, r), ns, nb, nv, nf, 0,
                    dict(sorted(surf.items())))


def _v234(buf, o: int, end: int, idx: int, version: int, name: str, mid: int) -> ColModel:
    hdr = 76 + (12 if version >= 3 else 0) + (4 if version >= 4 else 0)
    _need(o + hdr <= end, o, f"COL{version} header ({hdr} B) overruns the model")
    v = _V2.unpack_from(buf, o)
    x0, y0, z0, x1, y1, z1, cx, cy, cz, r = v[:10]
    ns, nb, nf, _nl, _flags = v[10:15]
    o_sph, o_box, _o_lin, o_vrt, o_face, _o_pln = v[15:21]
    base = o - 28                       # offsets are relative to fourcc + 4 (the model starts 32 B before o)
    lo = o + hdr                       # arrays cannot point into the fixed header
    nshadow = 0
    if version >= 3:
        nshadow, o_shadow_vrt, o_shadow_face = _V3.unpack_from(buf, o + 76)
        if nshadow:
            _mesh_vertices(buf, base + o_shadow_face, nshadow, base + o_shadow_vrt, lo, end, "shadow ")
    surf: Counter = Counter()
    if ns:
        surf += _mat_counts(buf, base + o_sph, ns, 20, 16, lo, end, "spheres")
    if nb:
        surf += _mat_counts(buf, base + o_box, nb, 28, 24, lo, end, "boxes")
    nv = 0
    if nf:
        a = base + o_face
        surf += _mat_counts(buf, a, nf, 8, 6, lo, end, "faces")
        nv = _mesh_vertices(buf, a, nf, base + o_vrt, lo, end)
    return ColModel(idx, version, name, mid, end - o + 32, (x0, y0, z0, x1, y1, z1), (cx, cy, cz, r), ns, nb, nv, nf,
                    nshadow, dict(sorted(surf.items())))


def _header_only(buf, o: int, end: int, idx: int, version: int, name: str, mid: int) -> ColModel:
    """A model whose body could not be parsed: bounds when they fit, zero counts."""
    bbox: tuple = (0.0,) * 6
    bs: tuple = (0.0, 0.0, 0.0, 0.0)
    if o + 40 <= end:
        v = struct.unpack_from("<10f", buf, o)
        if version == 1:
            bs, bbox = (v[1], v[2], v[3], v[0]), v[4:10]
        else:
            bbox, bs = v[:6], v[6:10]
    return ColModel(idx, version, name, mid, end - o + 32, tuple(bbox), tuple(bs), 0, 0, 0, 0, 0, {})


def iter_col(buf, *, strict: bool = False, errors: list | None = None) -> Iterator[ColModel]:
    """Iterate the collision models of a ``.col`` blob.

    Args:
        buf: file contents (``bytes``/``memoryview``), e.g. an IMG entry or a DFF ``CollisionModel`` payload.
        strict: raise ``FormatError`` when the body of a record is inconsistent (default: the record is
            still yielded with its bounds and zero counts, and the problem goes to ``errors``).
        errors: if given, ``(model idx, message)`` of such records are appended.

    A first record that is not a collision model, or a record overrunning the buffer, always raises
    ``FormatError(kind="col")``. Vanilla example of a broken body: ``models/coll/peds.col`` (a VC leftover
    the game never loads: the first model ``cop`` claims 133 boxes and stores none).
    """
    n = len(buf)
    off = 0
    idx = 0
    while off + 8 <= n:
        fourcc = bytes(buf[off:off + 4])
        version = FOURCC_VERSION.get(fourcc)
        if version is None:
            if idx == 0:
                raise FormatError("col", off, f"not a collision file (magic {fourcc!r})")
            return                                            # padding / trailing data, like the engine
        _need(off + 32 <= n, off, "truncated collision header")
        _fc, size, raw_name, mid = _HEAD.unpack_from(buf, off)
        end = off + 8 + size
        _need(size >= 24 and end <= n, off, f"COL{version} record of {size} bytes overruns the buffer ({n})")
        name = raw_name.split(b"\0", 1)[0].decode("latin-1")
        o = off + 32
        try:
            if version == 1:
                m = _v1(buf, o, end, idx, name, mid)
            else:
                m = _v234(buf, o, end, idx, version, name, mid)
        except FormatError as e:
            if strict:
                raise
            if errors is not None:
                errors.append((idx, f"{name}: {e}"))
            m = _header_only(buf, o, end, idx, version, name, mid)
        yield m
        idx += 1
        off = end
    if idx == 0:
        raise FormatError("col", 0, "empty collision file")
