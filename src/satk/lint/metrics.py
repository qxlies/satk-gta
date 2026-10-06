"""Shading metrics for the lint rules (``satk.lint.metrics``).

The canonical definitions belong to ``satk.style.metrics`` (contract K1: ``mesh_metrics(pos, tris,
corner_normals=None, uv=None, mat=None, *, normals=None)``). When that module and numpy are importable
they are used; otherwise this module computes the shading keys with the same definitions in pure Python:

* ``shade.normal_bend`` -- area-weighted mean angle (degrees) between a triangle's corner normals and its
  face normal; flat shading gives 0, vanilla cars about 9-18 (premier 10.64);
* ``shade.flat_share`` -- share of the non-degenerate triangles whose three corner normals are within
  1 degree of the face normal;
* ``dff.verts_per_tri`` -- vertices / triangles of one geometry (flat-shaded exports split every corner:
  2-3; vanilla car chassis 1.15-1.38).

:func:`model_shading` pools the HD geometries of a DFF (frames ending in ``_dam`` or ``_vlo`` are left
out), area-weighted over every geometry with normals, like ``satk.style.dffmesh.dff_metrics``.

Example::

    m = mesh_metrics(mesh.positions, mesh.tris, normals=mesh.normals)
    print(m["shade.normal_bend"], m["shade.flat_share"])
"""

from __future__ import annotations

import math

__all__ = ["mesh_metrics", "shade_sums", "model_shading", "geometry_bend", "FLAT_DEG"]

#: A corner normal within this many degrees of the face normal counts as flat (K1 ``FLAT_DEG``).
FLAT_DEG = 1.0
_FLAT_COS = math.cos(math.radians(FLAT_DEG))
_K1: list = []          # [module or None], resolved once


def shade_sums(pos, tris, normals) -> tuple[float, float, int, int]:
    """``(area, area x bend, flat triangles, non-degenerate triangles)`` of one geometry (pure Python).

    ``normals`` are per vertex (xyz interleaved, like ``pos``); ``tris`` is a flat index list.
    """
    area_sum = bend_sum = 0.0
    flat = n = 0
    nt = len(tris) // 3
    acos, sqrt = math.acos, math.sqrt
    for t in range(nt):
        ia, ib, ic = tris[3 * t], tris[3 * t + 1], tris[3 * t + 2]
        ax, ay, az = pos[3 * ia], pos[3 * ia + 1], pos[3 * ia + 2]
        ux, uy, uz = pos[3 * ib] - ax, pos[3 * ib + 1] - ay, pos[3 * ib + 2] - az
        vx, vy, vz = pos[3 * ic] - ax, pos[3 * ic + 1] - ay, pos[3 * ic + 2] - az
        fx, fy, fz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = sqrt(fx * fx + fy * fy + fz * fz)
        if not ln > 1e-12 or not math.isfinite(ln):
            continue
        fx, fy, fz = fx / ln, fy / ln, fz / ln
        s = 0.0
        is_flat = True
        for i in (ia, ib, ic):
            nx, ny, nz = normals[3 * i], normals[3 * i + 1], normals[3 * i + 2]
            nl = sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            d = (fx * nx + fy * ny + fz * nz) / nl
            d = 1.0 if d > 1.0 else (-1.0 if d < -1.0 else d)
            if d < _FLAT_COS:
                is_flat = False
            s += acos(d)
        a = ln / 2
        area_sum += a
        bend_sum += a * math.degrees(s / 3)
        flat += is_flat
        n += 1
    return area_sum, bend_sum, flat, n


def _k1():
    """``satk.style.metrics`` when it and numpy import, else ``None`` (checked once)."""
    if not _K1:
        try:
            import numpy  # noqa: F401
            from ..style import metrics as k1  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001 - K1 or numpy missing (a stdlib-only install) -> pure Python
            k1 = None
        _K1.append(k1)
    return _K1[0]


def mesh_metrics(pos, tris, corner_normals=None, uv=None, mat=None, *, normals=None) -> dict:
    """K1 keys of one mesh (``satk.style.metrics`` when present, else the shading keys computed here).

    ``pos``/``normals`` per vertex and ``tris`` may be flat (xyz interleaved) or shaped; ``corner_normals``
    are per triangle corner in ``tris`` order (only K1 reads them).
    """
    k1 = _k1()
    if k1 is not None:
        try:
            return dict(k1.mesh_metrics(pos, tris, corner_normals=corner_normals, uv=uv, mat=mat, normals=normals))
        except (ValueError, TypeError, IndexError):
            pass                                   # malformed input for K1: fall back to the local numbers
    nv, nt = len(pos) // 3, len(tris) // 3
    out: dict = {"geo.tris": nt}
    if nt:
        out["dff.verts_per_tri"] = round(nv / nt, 4)
    if normals is not None and len(normals) == len(pos) and nt:
        a, b, f, n = shade_sums(pos, tris, normals)
        if a and n:
            out["shade.normal_bend"] = round(b / a, 3)
            out["shade.flat_share"] = round(f / n, 4)
    return out


def geometry_bend(pos, tris, normals) -> float:
    """``shade.normal_bend`` (degrees) of one geometry with per-vertex normals (0 when undefined)."""
    return float(mesh_metrics(pos, tris, normals=normals).get("shade.normal_bend", 0.0))


def model_shading(meshes, frame_names: dict[int, str]) -> dict | None:
    """Pooled ``shade.*`` over the HD geometries with normals of a decoded DFF (``None`` if none).

    ``meshes``: :func:`satk.formats.dff.decode_geometries` output; ``frame_names``: frame index -> name.
    Geometries on ``*_dam``/``*_vlo`` frames are skipped (the undamaged high-detail look). Returns
    ``{"shade.normal_bend", "shade.flat_share", "tris", "source": "k1"|"local"}``, with K1 also
    ``geo.median_dihedral`` (the median fold between neighbouring faces: low = mostly flat panels, which
    smoothing the normals cannot round).
    """
    parts = []
    for m in meshes:
        if m.normals is None or not len(m.tris):
            continue
        name = frame_names.get(m.frame, "")
        if name.endswith("_dam") or name.endswith("_vlo"):
            continue
        parts.append(m)
    if not parts:
        return None
    k1 = _k1()
    if k1 is not None:
        try:
            got = k1.mesh_metrics(**k1.concat([{"pos": m.positions, "tris": m.tris, "normals": m.normals}
                                                for m in parts]))
        except (ValueError, TypeError, IndexError):
            got = None
        if got and "shade.normal_bend" in got:
            out = {"shade.normal_bend": round(got["shade.normal_bend"], 2),
                   "shade.flat_share": round(got["shade.flat_share"], 3), "tris": got["geo.tris"], "source": "k1"}
            if "geo.median_dihedral" in got:
                out["geo.median_dihedral"] = round(got["geo.median_dihedral"], 2)
            return out
    a_sum = b_sum = 0.0
    flat = tris = 0
    for m in parts:
        a, b, f, n = shade_sums(m.positions, m.tris, m.normals)
        a_sum += a
        b_sum += b
        flat += f
        tris += n
    if not tris or not a_sum:
        return None
    return {"shade.normal_bend": round(b_sum / a_sum, 2), "shade.flat_share": round(flat / tris, 3), "tris": tris,
            "source": "local"}
