"""DFF parts -> canonical metrics (:mod:`satk.style.metrics`) in model space.

The parts come from :func:`satk.model3d.mesh.build_scene` (one per atomic, plus geometries no atomic
uses), with the model-space matrix of their frame. ``select="hd"`` keeps what renders undamaged at full
detail (:func:`satk.style.metrics.is_hd_part`: no ``*_dam``, no ``*_vlo``; a vehicle's single ``wheel``
atomic counts once, so the triangles equal ``veh.hd_tris``).

Example::

    from satk.style.dffmesh import dff_metrics
    m = dff_metrics(dff_bytes)                 # {"parts": [...], "metrics": {"shade.normal_bend": ...}}
"""

from __future__ import annotations

from typing import Callable

from ..model3d.mesh import build_scene
from .metrics import concat, is_hd_part, mesh_metrics

__all__ = ["dff_parts", "dff_metrics"]


def _mat4(m) -> list[list[float]]:
    """RW 12-float matrix (right, up, at, pos) -> 4x4 with column vectors."""
    rx, ry, rz, ux, uy, uz, ax, ay, az, px, py, pz = m
    return [[rx, ux, ax, px], [ry, uy, ay, py], [rz, uz, az, pz], [0.0, 0.0, 0.0, 1.0]]


def dff_parts(buf, *, select: str | Callable[[str], bool] = "hd", name: str = "model",
              sec: str | None = None) -> list[dict]:
    """Parts of a DFF as dicts for :func:`satk.style.metrics.concat` (plus ``name``).

    Args:
        buf: the DFF bytes.
        select: ``"hd"`` (no ``*_dam``/``*_vlo``), ``"all"``, or a predicate on the part name.
        name: model name (names frameless geometries).
        sec: IDE section (``"cars"`` for vehicles; only used by previews, kept for symmetry).
    """
    keep = is_hd_part if select == "hd" else (lambda _n: True) if select == "all" else select
    if not callable(keep):
        raise ValueError(f"select: {select!r} is not 'hd', 'all' or a callable")
    scene = build_scene(bytes(buf), name=name, sec=sec)
    out = []
    for p in scene.parts:
        if not keep(p.name):
            continue
        m = scene.meshes[p.geom]
        if not m.tris:
            continue
        out.append({
            "name": p.name,
            "pos": m.positions,
            "tris": m.tris,
            "normals": m.normals,
            "uv": m.uv[0] if m.uv else None,
            "mat": m.mat_ids,
            "matrix": _mat4(p.matrix),
        })
    return out


def dff_metrics(buf, *, select: str | Callable[[str], bool] = "hd", per_part: bool = False,
                name: str = "model") -> dict:
    """Metrics of the selected parts of a DFF, merged in model space.

    Returns ``{"parts": [names], "metrics": {...}}``; with ``per_part`` also ``"by_part": {name: {...}}``
    (each part in its own frame space). Shading and shares are pooled over all selected triangles
    (``shade.normal_bend`` area-weighted), so a model number is not a mean of part numbers.
    """
    parts = dff_parts(buf, select=select, name=name)
    res: dict = {"parts": [p["name"] for p in parts], "metrics": mesh_metrics(**concat(parts))}
    if per_part:
        by: dict = {}
        for p in parts:
            m = mesh_metrics(p["pos"], p["tris"], normals=p["normals"], uv=p["uv"], mat=p["mat"])
            key, i = p["name"], 2
            while key in by:
                key, i = f"{p['name']}#{i}", i + 1
            by[key] = m
        res["by_part"] = by
    return res
