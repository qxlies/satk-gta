"""COL checks (``col.*`` rules): names, ±256 m vertices, boxes, spheres, flags, bounds, surfaces, COLFILE size.

:func:`satk.formats.col.iter_col` validates the records and counts primitives; this module additionally
decodes the primitives it needs (spheres, boxes, vertex extents) straight from the record, following
``ColHelpers.h`` of gta-reversed:

* COLL (v1): bounds ``radius, center[3], min[3], max[3]``; sphere = ``radius, center[3], surface[4]``;
  box = ``min[3], max[3], surface[4]``; vertices are floats (compressed to int16/128 on load);
* COL2+: bounds ``min[3], max[3], center[3], radius``; sphere = ``center[3], radius, surface[4]``
  (``CSphere``), vertices ``int16[3] / 128``; offsets are relative to ``fourcc + 4``.

Boxes in a COL file are axis-aligned by format (``CColBox = CBox{min, max}``), so a rotated box cannot be
stored: an exporter that ignores rotation (DragonFF, research 22 sec. 7.1 #4) or mirrors a box shows up
as ``min > max`` or as primitives outside the model bounds.
"""

from __future__ import annotations

import math
import struct
from array import array
from dataclasses import dataclass
from typing import Iterator

from ..formats.col import ColModel, iter_col
from ..formats.rw import FormatError
from .rules import Collector

__all__ = ["ColFacts", "check_col", "col_models"]

_V2 = struct.Struct("<10f3HBxI6I")
_SPH = struct.Struct("<4f4B")
_BOX = struct.Struct("<6f4B")
_LIMIT_EPS = 1.0 / 128.0


@dataclass(frozen=True, slots=True)
class ColFacts:
    """What the link checks need from one collision model."""

    name: str
    label: str
    bbox: tuple[float, ...]
    embedded: bool
    prims: int = 0            # spheres + boxes + mesh faces (0 = no collision at all)


@dataclass(slots=True)
class _Prims:
    flags: int | None
    spheres: list           # (cx, cy, cz, r)
    boxes: list             # (min3, max3)
    vmin: tuple | None      # vertex extents (model space) or None
    vmax: tuple | None
    v1_verts: array | None  # COLL float vertices (for the ±256 m check)
    verts: array | None = None   # all vertices, xyz interleaved, in units of ``scale`` metres
    scale: float = 1.0
    face_light: bytes | None = None  # COL2+: the lighting byte of every mesh face (low nibble day, high night)


def _finite(vals) -> bool:
    try:
        return math.isfinite(math.fsum(vals))
    except (OverflowError, ValueError):
        return False


def _extents(a: array, n: int) -> tuple[tuple, tuple] | None:
    if not n:
        return None
    xs, ys, zs = a[0::3], a[1::3], a[2::3]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _decode(buf, rec: int, m: ColModel) -> _Prims:
    """Primitives of a model whose body :func:`iter_col` accepted (offsets/counts are in range)."""
    o = rec + 32
    spheres: list = []
    boxes: list = []
    if m.version == 1:
        p = o + 40
        (ns,) = struct.unpack_from("<I", buf, p)
        p += 4
        for i in range(ns):
            r, cx, cy, cz = _SPH.unpack_from(buf, p + 20 * i)[:4]
            spheres.append((cx, cy, cz, r))
        p += 20 * ns
        (nl,) = struct.unpack_from("<I", buf, p)
        p += 4 + 24 * nl
        (nb,) = struct.unpack_from("<I", buf, p)
        p += 4
        for i in range(nb):
            v = _BOX.unpack_from(buf, p + 28 * i)
            boxes.append((v[0:3], v[3:6]))
        p += 28 * nb
        (nv,) = struct.unpack_from("<I", buf, p)
        p += 4
        verts = array("f")
        verts.frombytes(bytes(buf[p:p + 12 * nv]))
        if verts.itemsize != 4:  # pragma: no cover - exotic platforms
            verts = array("f", struct.unpack_from(f"<{3 * nv}f", buf, p))
        ext = _extents(verts, nv)
        return _Prims(None, spheres, boxes, ext[0] if ext else None, ext[1] if ext else None, verts, verts, 1.0)
    v = _V2.unpack_from(buf, o)
    flags = v[14]
    o_sph, o_box, _o_lin, o_vrt, o_face, _o_pln = v[15:21]
    base = rec + 4
    for i in range(m.spheres):
        cx, cy, cz, r = _SPH.unpack_from(buf, base + o_sph + 20 * i)[:4]
        spheres.append((cx, cy, cz, r))
    for i in range(m.boxes):
        b = _BOX.unpack_from(buf, base + o_box + 28 * i)
        boxes.append((b[0:3], b[3:6]))
    vmin = vmax = None
    raw = None
    light = None
    if m.faces:
        fb = bytes(buf[base + o_face:base + o_face + 8 * m.faces])
        light = fb[7::8]
    if m.verts:
        raw = array("h")
        raw.frombytes(bytes(buf[base + o_vrt:base + o_vrt + 6 * m.verts]))
        ext = _extents(raw, m.verts)
        if ext:
            vmin = tuple(x / 128.0 for x in ext[0])
            vmax = tuple(x / 128.0 for x in ext[1])
    return _Prims(flags, spheres, boxes, vmin, vmax, None, raw, 1.0 / 128.0, light)


def _beyond_sphere(pr: _Prims, sphere) -> tuple[float, str]:
    """How far the farthest primitive point lies outside ``sphere`` = ``(x, y, z, r)`` and which one."""
    cx, cy, cz, r = sphere
    worst, what = 0.0, ""
    for i, (x, y, z, sr) in enumerate(pr.spheres):
        d = math.dist((x, y, z), (cx, cy, cz)) + max(sr, 0.0) - r
        if d > worst:
            worst, what = d, f"sphere {i}"
    for i, (lo, hi) in enumerate(pr.boxes):
        far = max(math.dist((cx, cy, cz), (x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2]))
        if far - r > worst:
            worst, what = far - r, f"box {i}"
    v = pr.verts
    if v is not None and len(v):
        k = pr.scale
        ux, uy, uz = cx / k, cy / k, cz / k
        d2 = max((x - ux) ** 2 + (y - uy) ** 2 + (z - uz) ** 2 for x, y, z in zip(v[0::3], v[1::3], v[2::3]))
        far = math.sqrt(d2) * k
        if far - r > worst:
            worst, what = far - r, "the mesh"
    return worst, what


def col_models(buf) -> Iterator[tuple[int, ColModel, str | None]]:
    """``(record offset, model, body error or None)`` for every model of a collision blob.

    Raises ``FormatError`` when the blob is not a collision file at all (like :func:`iter_col`).
    """
    errs: list = []
    off = 0
    for m in iter_col(buf, errors=errs):
        err = None
        if errs and errs[-1][0] == m.idx:
            err = errs[-1][1]
        yield off, m, err
        off += m.size


def _axes(lo, hi) -> list[str]:
    return [a for a, x0, x1 in zip("xyz", lo, hi) if x0 > x1]


def _outside(bbox, lo, hi) -> float:
    """How far the box ``lo..hi`` sticks out of ``bbox`` (0 if inside)."""
    d = 0.0
    for i in range(3):
        d = max(d, bbox[i] - lo[i], hi[i] - bbox[3 + i])
    return d


def check_col(c: Collector, label: str, buf, *, colfile: bool = False, only_idx: int | None = None,
              embedded: bool = False) -> list[ColFacts]:
    """Run the ``col.*`` rules on one collision blob; returns the facts of its models.

    Args:
        c: finding collector.
        label: file label for the findings.
        buf: the blob (a ``.col`` file, an IMG entry or a DFF's embedded collision).
        colfile: the blob is a loose ``.col`` the game loads through ``COLFILE`` (32 KB buffer rule).
        only_idx: check only this model (``col:`` SID targets).
        embedded: a vehicle's embedded collision (bound by the DFF, not by name).
    """
    out: list[ColFacts] = []
    try:
        models = list(col_models(buf))
    except FormatError as e:
        c.add("col.parse", label, err=str(e))
        return out
    tol = float(c.rules.param("col.outside_bounds", "tol", 0.5))
    stol = float(c.rules.param("col.bsphere", "tol", 0.5))
    smax = int(c.rules.param("col.surface", "max", 178))
    nmax = int(c.rules.param("col.name_len", "max", 21))
    vlim = float(c.rules.param("col.vertex_range", "max", 256.0))
    cbuf = int(c.rules.param("col.colfile_buffer", "max", 32768))
    for rec, m, err in models:
        if only_idx is not None and m.idx != only_idx:
            continue
        name = m.name
        shown = name or f"#{m.idx}"
        if not name:
            c.add("col.name_empty", label, idx=m.idx)
        elif len(name) > nmax:
            c.add("col.name_len", label, name=name, n=len(name))
        if colfile and m.size - 32 > cbuf:
            c.add("col.colfile_buffer", label, name=shown, size=m.size - 32)
        out.append(ColFacts(name, label, tuple(m.bbox), embedded, m.spheres + m.boxes + m.faces))
        if err is not None:
            c.add("col.body", label, idx=m.idx, err=err)
            continue
        if not _finite(m.bbox) or not _finite(m.bsphere):
            c.add("col.nan", label, name=shown, what="the bounds")
            continue
        try:
            pr = _decode(buf, rec, m)
        except (struct.error, IndexError, ValueError) as e:  # iter_col validated the spans; be defensive
            c.add("col.body", label, idx=m.idx, err=f"{type(e).__name__}: {e}")
            continue
        nprim = m.spheres + m.boxes + m.faces
        if nprim == 0:
            c.add("col.empty", label, name=shown)
        elif pr.flags is not None and not pr.flags & 2:
            c.add("col.empty_flag", label, name=shown, version=m.version, prims=nprim)
        bad = []
        if not _finite(v for s in pr.spheres for v in s):
            bad.append("spheres")
        if not _finite(v for b in pr.boxes for part in b for v in part):
            bad.append("boxes")
        if pr.v1_verts is not None and not _finite(pr.v1_verts):
            bad.append("vertices")
        if bad:
            c.add("col.nan", label, name=shown, what=", ".join(bad))
            continue
        if pr.v1_verts is not None and len(pr.v1_verts):
            worst = max(-min(pr.v1_verts), max(pr.v1_verts))
            if worst > vlim - _LIMIT_EPS:
                c.add("col.vertex_range", label, name=shown, v=round(worst, 2))
        for i, (lo, hi) in enumerate(pr.boxes):
            ax = _axes(lo, hi)
            if ax:
                c.add("col.box_inverted", label, name=shown, i=i, axes="".join(ax))
        for i, (_x, _y, _z, r) in enumerate(pr.spheres):
            if r <= 0:
                c.add("col.sphere_radius", label, name=shown, i=i, r=round(r, 3))
        bb = m.bbox
        worst_d, worst_what = 0.0, ""
        for i, (x, y, z, r) in enumerate(pr.spheres):
            d = _outside(bb, (x - r, y - r, z - r), (x + r, y + r, z + r))
            if d > worst_d:
                worst_d, worst_what = d, f"sphere {i}"
        for i, (lo, hi) in enumerate(pr.boxes):
            d = _outside(bb, tuple(min(a, b) for a, b in zip(lo, hi)), tuple(max(a, b) for a, b in zip(lo, hi)))
            if d > worst_d:
                worst_d, worst_what = d, f"box {i}"
        if pr.vmin is not None:
            d = _outside(bb, pr.vmin, pr.vmax)
            if d > worst_d:
                worst_d, worst_what = d, "the mesh"
        if worst_d > tol:
            c.add("col.outside_bounds", label, name=shown, what=worst_what, d=round(worst_d, 2))
        if nprim and c.on("col.bsphere"):
            d, what = _beyond_sphere(pr, m.bsphere)
            if d > stol:
                c.add("col.bsphere", label, name=shown, what=what, d=round(d, 2))
        if pr.face_light is not None and not embedded:
            c.seen("col.face_light_zero")
            if not any(pr.face_light):
                c.add("col.face_light_zero", label, name=shown, n=m.faces)
        odd = {k: v for k, v in m.surfaces.items() if k > smax}
        for mat, n in sorted(odd.items()):
            c.add("col.surface", label, name=shown, mat=mat, n=n)
    return out
