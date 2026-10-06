"""Bounding-sphere post-pass of DragonFF DFFs (stdlib only; also imported from Blender's Python).

DragonFF b3bd7aa writes each geometry's morph-target sphere as ``matrix_world @ bbox centre`` (model space) with
radius ``1.732 * max(dimensions) / 2``: parts away from the model origin get spheres that miss their own
vertices (``dff.bsphere`` lint warnings - 12 on a re-exported premier - and wrong culling). RenderWare wants the
sphere in the geometry's own (frame-local) space: :func:`fix_bspheres` recomputes it from the vertices.

Example::

    from satk.kit.rwfix import fix_bspheres
    fix_bspheres("<workspace>/work/out/kit/mycar/build/mycar.dff")   # {"geometries": 7, "fixed": 7, ...}
"""

from __future__ import annotations

import math
import os
import struct

__all__ = ["fix_bspheres", "fix_buffer"]

_CLUMP, _STRUCT, _GEOMLIST, _GEOM = 0x10, 0x01, 0x1A, 0x0F
_GEO_PRELIT, _GEO_TEX, _GEO_TEX2, _GEO_NATIVE = 0x08, 0x04, 0x80, 0x01000000


def _chunks(buf, off: int, end: int):
    while off + 12 <= end:
        t, size, lib = struct.unpack_from("<III", buf, off)
        data = off + 12
        if size > end - data:
            return
        yield t, data, data + size, lib
        off = data + size


def _fix_geometry(buf: bytearray, data: int, end: int, lib: int) -> tuple[int, float]:
    """Recompute the morph-target spheres of one Geometry struct from its own (frame-local) vertices.
    Returns ``(spheres fixed, largest centre move in metres)``."""
    if end - data < 16:
        return 0, 0.0
    fmt, ntri, nv, nmorph = struct.unpack_from("<IiiI", buf, data)
    if fmt & _GEO_NATIVE or nv <= 0 or ntri < 0:
        return 0, 0.0
    ver = ((lib >> 14) & 0x3FF00) + 0x30000 | ((lib >> 16) & 0x3F) if lib & 0xFFFF0000 else lib << 8
    p = data + 16 + (12 if ver < 0x34000 else 0)
    flags = fmt & 0xFFFF
    nts = (fmt >> 16) & 0xFF or (2 if flags & _GEO_TEX2 else 1 if flags & _GEO_TEX else 0)
    if flags & _GEO_PRELIT:
        p += 4 * nv
    p += 8 * nv * nts + 8 * ntri
    fixed, moved = 0, 0.0
    for _ in range(nmorph):
        if p + 24 > end:
            break
        x0, y0, z0, _r0, has_v, has_n = struct.unpack_from("<4fII", buf, p)
        vp = p + 24
        if has_v and vp + 12 * nv <= end:
            vs = struct.unpack_from(f"<{3 * nv}f", buf, vp)
            xs, ys, zs = vs[0::3], vs[1::3], vs[2::3]
            cx, cy, cz = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2
            r = max(math.sqrt((xs[i] - cx) ** 2 + (ys[i] - cy) ** 2 + (zs[i] - cz) ** 2) for i in range(nv))
            struct.pack_into("<4f", buf, p, cx, cy, cz, r * 1.0001 + 1e-4)
            fixed += 1
            moved = max(moved, math.sqrt((cx - x0) ** 2 + (cy - y0) ** 2 + (cz - z0) ** 2))
        p = vp + (12 * nv if has_v else 0) + (12 * nv if has_n else 0)
    return fixed, moved


def fix_buffer(buf: bytearray) -> dict:
    """Fix the spheres of every Geometry of every clump in ``buf`` in place (see :func:`fix_bspheres`)."""
    geoms = fixed = 0
    moved = 0.0
    for t, d, e, _lib in _chunks(buf, 0, len(buf)):
        if t != _CLUMP:
            continue
        for t2, d2, e2, _l2 in _chunks(buf, d, e):
            if t2 != _GEOMLIST:
                continue
            for t3, d3, e3, _l3 in _chunks(buf, d2, e2):
                if t3 != _GEOM:
                    continue
                geoms += 1
                for t4, d4, e4, l4 in _chunks(buf, d3, e3):
                    if t4 == _STRUCT:
                        n, m = _fix_geometry(buf, d4, e4, l4)
                        fixed += n
                        moved = max(moved, m)
                        break
    return {"geometries": geoms, "fixed": fixed, "max_move_m": round(moved, 3)}


def fix_bspheres(path: str) -> dict:
    """Post-pass of a DragonFF DFF: every Geometry's morph-target sphere becomes the bounding-box centre of its own
    vertices and the farthest vertex distance. Returns ``{"geometries", "fixed", "max_move_m"}``; writes in place."""
    with open(path, "rb") as f:
        buf = bytearray(f.read())
    res = fix_buffer(buf)
    if res["fixed"]:
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            f.write(buf)
        os.replace(tmp, path)
    return res
