# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Per-step numbers of the *evaluated* meshes (modifiers applied), as an export would see them.

``collect(names)`` -> ``{"scene": {objects, tris, verts, bbox, dims, vs_target?, edge?, out?},
"objects": {name: {tris, verts, dims, <K1 metrics>, edge?, out?}}}``.

When ``satk.style.metrics.mesh_metrics`` (contract K1) imports, the rows of the changed objects get its
``shade.*`` / ``dff.verts_per_tri`` / ``geo.*`` / ``uv.zero_area_share`` values, so the numbers equal
``asset.check``. With a project, ``bands`` (``{metric: {"s": [p10, p50, p90, n], "lo", "hi", "status",
"cap"?, "parent"?}}``: the tier band of the project's peer set, built by ``satk.studio.project``) are judged
by ``satk.style.profile.judge``, the function ``asset.check`` uses: a value between the tier band and its
fence is listed under ``edge``, one beyond the fence under ``out`` (``"geo.tris>562"``: the band edge it
crossed). ``target_dims`` (``[x, y, z]`` metres) gives ``vs_target`` ratios. Ghosts (``satk_ghost``) and
reference planes (``satk_ref``) are never counted.
"""

from __future__ import annotations

import bpy
from mathutils import Vector

__all__ = ["GEOMETRY_TYPES", "METRIC_KEYS", "geometry_objects", "mesh_counts", "mesh_arrays", "collect",
           "configure"]

#: Object types that evaluate to a mesh.
GEOMETRY_TYPES = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META"})
#: K1 keys copied into the per-object rows (when the metrics module is there).
METRIC_KEYS = ("shade.normal_bend", "shade.flat_share", "dff.verts_per_tri", "geo.pieces", "geo.open_edges",
               "geo.nonmanifold_edges", "uv.zero_area_share")
#: Band keys checked per object (the rest - geo.tris, dims.* - against the scene).
_OBJECT_BANDS = ("shade.", "dff.", "uv.")

_metrics_fn: list = []  # [callable | None], resolved once
_judge_fn: list = []    # [callable | None]: satk.style.profile.judge
_cfg: dict = {"bands": {}, "target_dims": None}


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def configure(*, bands: dict | None = None, target_dims=None) -> None:
    """Tier bands and target dimensions of the project (set by the world at start). A band is
    ``{"s": [p10, p50, p90, n], "lo", "hi", "status", "cap"?, "parent"?}``; a bare ``[p10, p50, p90]`` means
    the measured band p10..p90."""
    good = {}
    for k, v in (bands or {}).items():
        if isinstance(v, dict) and isinstance(v.get("s"), list) and len(v["s"]) >= 3 \
                and all(_num(x) for x in v["s"][:3]) and _num(v.get("lo")) and _num(v.get("hi")):
            good[str(k)] = dict(v)
        elif isinstance(v, (list, tuple)) and len(v) == 3 and all(_num(x) for x in v):
            good[str(k)] = {"s": [float(x) for x in v] + [0], "lo": float(v[0]), "hi": float(v[2]),
                            "status": "measured"}
    _cfg["bands"] = good
    td = target_dims
    _cfg["target_dims"] = [float(x) for x in td] if isinstance(td, (list, tuple)) and len(td) == 3 and all(
        isinstance(x, (int, float)) and x > 0 for x in td) else None


def _metrics():
    if not _metrics_fn:
        try:
            from satk.style.metrics import mesh_metrics  # K1 (lane style); optional
        except Exception:  # noqa: BLE001 - absent before the style package lands
            mesh_metrics = None
        _metrics_fn.append(mesh_metrics)
    return _metrics_fn[0]


def geometry_objects(scene=None) -> list:
    """Meshes and other geometry of the scene, without ghosts and reference planes."""
    scene = scene or bpy.context.scene
    return [o for o in scene.objects if o.type in GEOMETRY_TYPES and not o.get("satk_ghost") and not o.get("satk_ref")]


def _r2(v: float) -> float:
    return round(float(v) + 0.0, 2)


def mesh_arrays(me):
    """``(pos, tris, corner_normals, uv, mat)`` numpy arrays of an evaluated mesh (loop triangles computed)."""
    import numpy as np

    pos = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", pos)
    nt = len(me.loop_triangles)
    tris = np.empty(nt * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", tris)
    loops = np.empty(nt * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("loops", loops)
    cn = np.empty(len(me.corner_normals) * 3, dtype=np.float64)
    me.corner_normals.foreach_get("vector", cn)
    corner = cn.reshape(-1, 3)[loops].reshape(-1, 3, 3)
    uv = None
    if me.uv_layers.active is not None:
        u = np.empty(len(me.loops) * 2, dtype=np.float64)
        me.uv_layers.active.data.foreach_get("uv", u)
        uv = u.reshape(-1, 2)[loops].reshape(-1, 3, 2)
    mat = np.empty(nt, dtype=np.int64)
    me.loop_triangles.foreach_get("material_index", mat)
    return pos.reshape(-1, 3), tris.reshape(-1, 3), corner, uv, mat


def _k1(me) -> dict:
    fn = _metrics()
    if fn is None or not len(me.loop_triangles):
        return {}
    pos, tris, corner, uv, mat = mesh_arrays(me)
    m = fn(pos, tris, corner_normals=corner, uv=uv, mat=mat)
    out = {}
    for k in METRIC_KEYS:
        v = m.get(k) if isinstance(m, dict) else None
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            out[k] = v
        elif isinstance(v, float):
            out[k] = round(v, 3)
    return out


def mesh_counts(o, depsgraph=None, *, metrics: bool = False) -> tuple[int, int, list, dict]:
    """``(tris, verts, world bbox corners, K1 metrics)`` of the evaluated object."""
    dg = depsgraph or bpy.context.evaluated_depsgraph_get()
    ev = o.evaluated_get(dg)
    try:
        me = ev.to_mesh()
    except RuntimeError:
        return 0, 0, [], {}
    try:
        if me is None:
            return 0, 0, [], {}
        me.calc_loop_triangles()
        mw = ev.matrix_world
        corners = [mw @ Vector(c) for c in ev.bound_box] if len(me.vertices) else []
        extra = {}
        if metrics:
            try:
                extra = _k1(me)
            except Exception as e:  # noqa: BLE001 - metrics are advisory: warn once, then stay off
                _metrics_fn[:] = [None]
                extra = {"warn": f"INTERNAL: style metrics failed and are off for this session: "
                                 f"{type(e).__name__}: {e}"[:300]}
        return len(me.loop_triangles), len(me.vertices), corners, extra
    finally:
        ev.to_mesh_clear()


def _judge():
    if not _judge_fn:
        try:
            from satk.style.profile import judge  # the verdict of asset.check (lane style)
        except Exception:  # noqa: BLE001 - without it: inside the band or not
            judge = None
        _judge_fn.append(judge)
    return _judge_fn[0]


def _show(x: float):
    x = float(x)
    return int(x) if x == int(x) and abs(x) >= 10 else round(x, 3)


def _verdict(key: str, v, band) -> tuple[str, str] | None:
    """``("edge" | "out", "key>hi")`` for a value outside its tier band, else ``None``."""
    if band is None or not _num(v):
        return None
    lo, hi = float(band["lo"]), float(band["hi"])
    verdict = None
    fn = _judge()
    if fn is not None:
        try:
            verdict = fn(float(v), band["s"], band, band.get("parent"))
        except Exception:  # noqa: BLE001
            verdict = None
    if verdict is None:
        verdict = "ok" if lo <= v <= hi else "high"
    if verdict == "ok":
        return None
    text = f"{key}<{_show(lo)}" if v < lo else f"{key}>{_show(hi)}"
    return ("edge" if verdict == "edge" else "out"), text


def _flag(row: dict, hit: tuple[str, str] | None) -> None:
    if hit:
        row.setdefault(hit[0], []).append(hit[1])


def collect(names: list[str] | None = None) -> dict:
    """Scene totals and rows for ``names`` (``None`` = no per-object rows, totals only)."""
    dg = bpy.context.evaluated_depsgraph_get()
    want = set(names) if names is not None else set()
    bands = _cfg["bands"]
    tris = verts = n = 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    rows: dict = {}
    warn: list[str] = []
    for o in geometry_objects():
        t, v, corners, extra = mesh_counts(o, dg, metrics=o.name in want)
        n += 1
        tris += t
        verts += v
        for c in corners:
            for i in range(3):
                lo[i] = min(lo[i], c[i])
                hi[i] = max(hi[i], c[i])
        if o.name in want:
            row = {"tris": t, "verts": v, "dims": [_r2(x) for x in o.dimensions]}
            w = extra.pop("warn", None)
            if w and w not in warn:
                warn.append(w)
            row.update(extra)
            low = o.get("satk_slot") in ("lod", "vlo") or o.name.lower().endswith("_vlo")
            for k in [k for k in row if k.startswith(_OBJECT_BANDS) and not low]:  # bands describe the HD model
                _flag(row, _verdict(k, row.get(k), bands.get(k)))
            part = bands.get(f"part.tris[{o.name}]")
            if part is not None:
                _flag(row, _verdict(f"part.tris[{o.name}]", t, part))
            rows[o.name] = row
    for name in sorted(want - set(rows)):
        o = bpy.data.objects.get(name)
        if o is not None and o.name in bpy.context.scene.objects:
            rows[name] = {"type": o.type, "dims": [_r2(x) for x in o.dimensions]}
    scene: dict = {"objects": n, "tris": tris, "verts": verts}
    if n and lo[0] != float("inf"):
        scene["bbox"] = [[_r2(x) for x in lo], [_r2(x) for x in hi]]
        dims = [hi[i] - lo[i] for i in range(3)]
        scene["dims"] = [_r2(x) for x in dims]
        td = _cfg["target_dims"]
        if td:
            scene["vs_target"] = [round(d / t, 3) for d, t in zip(dims, td)]
        _flag(scene, _verdict("geo.tris", tris, bands.get("geo.tris")))
        for i, k in enumerate(("dims.W", "dims.L", "dims.H")):
            _flag(scene, _verdict(k, dims[i], bands.get(k)))
    out: dict = {"scene": scene}
    if rows:
        out["objects"] = rows
    if warn:
        out["warn"] = warn
    return out
