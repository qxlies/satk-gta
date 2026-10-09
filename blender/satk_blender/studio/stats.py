# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Per-step numbers of the *evaluated* meshes (modifiers applied), as an export would see them, and the
composition defects of the changed objects.

``collect(names)`` -> ``{"scene": {objects, tris, verts, bbox, dims, vs_target?}, "objects": {name: {tris, verts,
dims, geo.pieces, geo.open_edges, geo.nonmanifold_edges, uv.zero_area_share}}, "form"?: {...}, "warn"?: [...]}``.

Counts and sizes are plain numbers: no band, no verdict (triangle counts are never a target). ``scene.dims`` is
``[W, L, H]`` of the high-detail geometry (no ``_dam``/``_vlo``/LOD/collision objects) with ``W`` from the body
(``chassis``) only, mirrors on doors excluded - measured the way the vanilla reference numbers are.

``form`` (``satk.style.form``, when it imports) holds DEFECTS of the changed objects only, each with its
location: ``floating`` (groups of pieces touching nothing grounded: part, pieces, tris, gap_mm, at, size),
``intersect`` (pieces deep inside another), ``loose_share`` (the body's triangles outside its largest welded
piece), ``hard_corners`` (60-100 deg folds hard without a seam, with example locations) and ``see_through``
(wheel arches without a liner). A step with a defect adds one ``FORM:`` warning. ``mesh`` (``satk.style.defects``,
the cheap subset :data:`STEP_MESH_CHECKS`) holds zero-area triangles, tiny islands and flipped faces of the changed
objects with one ``MESH:`` warning; cracks, z-fighting, UVs and collision stay in ``asset.check``. Scenes above
``FORM_MAX_TRIS`` triangles skip both blocks. ``target_dims`` (``[x, y, z]`` metres) gives ``vs_target`` ratios. Ghosts
(``satk_ghost``) and reference planes (``satk_ref``) are never counted.
"""

from __future__ import annotations

import bpy
from mathutils import Vector

__all__ = ["GEOMETRY_TYPES", "METRIC_KEYS", "FULL_KEYS", "FORM_MAX_TRIS", "geometry_objects", "mesh_counts", "mesh_arrays",
           "collect", "configure", "form_block"]

#: Object types that evaluate to a mesh.
GEOMETRY_TYPES = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META"})
#: K1 keys copied into the per-object rows of a step (when the metrics module is there): topology only.
METRIC_KEYS = ("geo.pieces", "geo.open_edges", "geo.nonmanifold_edges", "uv.zero_area_share")
#: K1 keys of an explicit ``scene.stats`` query (``collect(full=True)``): the shading numbers as reference too.
FULL_KEYS = ("shade.normal_bend", "shade.flat_share", "dff.verts_per_tri", *METRIC_KEYS)
#: Scenes with more triangles than this skip the form block (it runs after every step).
FORM_MAX_TRIS = 150_000

_metrics_fn: list = []  # [callable | None], resolved once
_form_fn: list = []     # [callable | None]: satk.style.form.analyse
_mesh_fn: list = []     # [(analyse, limits) | None]: satk.style.defects
_cfg: dict = {"target_dims": None}


def configure(*, bands: dict | None = None, target_dims=None) -> None:
    """Target dimensions of the project (set by the world at start). ``bands`` is accepted for older worlds and
    ignored: the step stats judge no number against a band."""
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


def _form():
    if not _form_fn:
        try:
            from satk.style.form import analyse  # composition checks (lane style); optional
        except Exception:  # noqa: BLE001
            analyse = None
        _form_fn.append(analyse)
    return _form_fn[0]


def _mesh():
    if not _mesh_fn:
        try:
            from satk.style.defects import analyse, limits  # mesh defects (lane style); optional
            _mesh_fn.append((analyse, limits))
        except Exception:  # noqa: BLE001
            _mesh_fn.append(None)
    return _mesh_fn[0]


#: Cheap mesh checks of the step (per changed object, no ray casts): zero-area triangles, tiny islands, faces
#: wound against their neighbours, closed pieces inside out, normals against the winding.
STEP_MESH_CHECKS = ("degenerate", "stray", "flipped")


def mesh_block(parts: list[dict], focus: list[str]) -> tuple[dict, list[str]]:
    """``(mesh defects of the changed objects, warnings)``: the cheap subset of ``asset.check`` section ``mesh``
    (:data:`STEP_MESH_CHECKS`); cracks, z-fighting, UVs and collision stay in ``asset.check``."""
    mf = _mesh()
    if mf is None:
        return {}, []
    analyse, limits = mf
    sel = [p for p in parts if p["name"] in set(focus) and p.get("role") != "wheel"]
    if not sel:
        return {}, []
    try:
        r = analyse(sel, checks=STEP_MESH_CHECKS, lim=limits({"far_m": 1e9}))
    except Exception as e:  # noqa: BLE001 - advice; a failure must not break the step
        _mesh_fn[:] = [None]
        return {}, [f"INTERNAL: mesh checks failed and are off for this session: {type(e).__name__}: {e}"[:300]]
    block = {k: r[k] for k in STEP_MESH_CHECKS if r.get(k)}
    said = []
    for x in r.get("flipped", []):
        said.append(f"{x['part']}: " + "/".join(x.get("kind", [])))
    for x in r.get("degenerate", []):
        said.append(f"{x['part']}: {x.get('zero', 0)} zero-area triangles, {x.get('unused', 0)} unused vertices")
    for x in r.get("stray", []):
        said.append(f"{x['part']}: {x['pieces']} tiny islands")
    warn = [f"MESH: {'; '.join(said[:4])}: see stats.mesh for locations"] if said else []
    return block, warn


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


def _k1(me, keys=METRIC_KEYS) -> dict:
    fn = _metrics()
    if fn is None or not len(me.loop_triangles):
        return {}
    pos, tris, corner, uv, mat = mesh_arrays(me)
    m = fn(pos, tris, corner_normals=corner, uv=uv, mat=mat)
    out = {}
    for k in keys:
        v = m.get(k) if isinstance(m, dict) else None
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            out[k] = v
        elif isinstance(v, float):
            out[k] = round(v, 3)
    return out


def _texture(mat) -> str:
    try:
        for n in mat.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.image is not None:
                return n.image.name.lower()
    except AttributeError:
        pass
    return ""


def _part(o, me, mw) -> dict:
    """A ``satk.style.form`` part dict of an evaluated mesh in world space."""
    import numpy as np

    pos, tris, corner, uv, mat = mesh_arrays(me)
    M = np.array(mw, dtype=np.float64)
    R, t = M[:3, :3], M[:3, 3]
    P = pos @ R.T + t
    N = corner @ np.linalg.inv(R)                      # inverse transpose applied to rows
    if np.linalg.det(R) < 0:
        tris = tris[:, [0, 2, 1]]
        N = N[:, [0, 2, 1]]
        uv = uv[:, [0, 2, 1]] if uv is not None else None
    mats = list(o.material_slots)
    tex = [_texture(s.material) if s.material is not None else "" for s in mats]
    alpha = [int(round(float(s.material.diffuse_color[3]) * 255)) if s.material is not None else 255 for s in mats]
    part = str(o.get("satk_part") or "").lower()
    role = "wheel" if part.startswith("wheel") or o.name.lower().startswith("wheel") else "hd"
    return {"name": o.name, "pos": P, "tris": tris, "normals": N, "uv": uv, "mat": mat, "tex": tex, "alpha": alpha,
            "role": role, "_body": part == "chassis" or o.name.lower() == "chassis"}


def _hd(o) -> bool:
    """High-detail geometry: no damage, LOD or collision object."""
    if o.get("satk_slot") in ("dam", "vlo", "lod"):
        return False
    n = o.name.lower()
    if n.endswith(("_dam", "_vlo")) or n.startswith("lod"):
        return False
    return not any(c.get("satk_col_of") for c in o.users_collection)


def mesh_counts(o, depsgraph=None, *, metrics: bool = False, arrays: bool = False, full: bool = False):
    """``(tris, verts, world bbox corners, K1 metrics)`` of the evaluated object (``full``: with the shading
    numbers); with ``arrays`` a fifth item, the ``satk.style.form`` part dict (``None`` without triangles)."""
    dg = depsgraph or bpy.context.evaluated_depsgraph_get()
    ev = o.evaluated_get(dg)
    empty = (0, 0, [], {}, None) if arrays else (0, 0, [], {})
    try:
        me = ev.to_mesh()
    except RuntimeError:
        return empty
    try:
        if me is None:
            return empty
        me.calc_loop_triangles()
        mw = ev.matrix_world
        corners = [mw @ Vector(c) for c in ev.bound_box] if len(me.vertices) else []
        extra = {}
        if metrics:
            try:
                extra = _k1(me, FULL_KEYS if full else METRIC_KEYS)
            except Exception as e:  # noqa: BLE001 - metrics are information: warn once, then stay off
                _metrics_fn[:] = [None]
                extra = {"warn": f"INTERNAL: style metrics failed and are off for this session: "
                                 f"{type(e).__name__}: {e}"[:300]}
        if not arrays:
            return len(me.loop_triangles), len(me.vertices), corners, extra
        part = _part(o, me, mw) if len(me.loop_triangles) else None
        return len(me.loop_triangles), len(me.vertices), corners, extra, part
    finally:
        ev.to_mesh_clear()


def _wheels(parts: list[dict]) -> list[dict]:
    """Car wheel dummies (empties ``wheel_*_dummy``) with the radius of the wheel mesh."""
    wheel = next((p for p in parts if p["role"] == "wheel"), None)
    if wheel is None:
        return []
    ext = wheel["pos"].max(axis=0) - wheel["pos"].min(axis=0)
    radius = float(max(ext[1], ext[2])) / 2
    out = []
    for o in bpy.context.scene.objects:
        n = o.name.lower()
        if o.type == "EMPTY" and n.startswith("wheel_") and n.endswith("_dummy") and not o.get("satk_ghost"):
            c = o.matrix_world.translation
            out.append({"name": o.name, "center": (c.x, c.y, c.z), "radius": radius})
    return out if len(out) >= 4 else []


def form_block(parts: list[dict], changed: list[str]) -> tuple[dict, list[str]]:
    """``(form defects of the changed objects, warnings)`` (empty when there are none or no form module).

    Floating, intersecting and loose pieces are judged against a body (an object ``chassis`` or a kit slot
    ``chassis``); a scene without one (free blockout primitives) gets only the hard-corner check."""
    fn = _form()
    if fn is None or not parts or not changed:
        return {}, []
    names = {p["name"] for p in parts}
    focus = [n for n in changed if n in names]
    if not focus:
        return {}, []
    body = next((p["name"] for p in parts if p.pop("_body", False)), None)
    for p in parts:
        p.pop("_body", None)
    bike = any(o.name.lower() == "forks_front" for o in bpy.context.scene.objects)
    wheels = [] if bike else _wheels(parts)
    checks = (("floating", "intersect", "loose_share") if body else ()) + ("hard_corners",) \
        + (("see_through",) if wheels and body else ())
    try:
        r = fn(parts, body=body, wheels=wheels or None, focus=focus, checks=checks, movable=not bike, step=True)
    except Exception as e:  # noqa: BLE001 - the form block is advice; a failure must not break the step
        _form_fn[:] = [None]
        return {}, [f"INTERNAL: form checks failed and are off for this session: {type(e).__name__}: {e}"[:300]]
    block = {k: r[k] for k in ("floating", "intersect", "loose_share", "hard_corners", "see_through") if r.get(k)}
    said = []
    if r.get("floating"):
        f = r["floating"]
        said.append(f"{sum(x['pieces'] for x in f)} floating piece(s) in {', '.join(sorted({x['part'] for x in f}))}")
    if r.get("loose_share"):
        said.append(f"{r['loose_share']['share']:.0%} of {r['loose_share']['part']} outside its welded shell")
    if r.get("hard_corners"):
        said.append("hard box corners in " + ", ".join(x["part"] for x in r["hard_corners"][:3]))
    if r.get("see_through"):
        said.append("see-through arch at " + ", ".join(x["wheel"] for x in r["see_through"]))
    warn = [f"FORM: {'; '.join(said)}: see stats.form for locations"] if said else []
    return block, warn


def collect(names: list[str] | None = None, *, full: bool = False) -> dict:
    """Scene totals and rows for ``names`` (``None`` = no per-object rows, totals only). ``full`` (an explicit
    ``scene.stats`` query): the rows add the shading numbers (reference) and no form block is built."""
    dg = bpy.context.evaluated_depsgraph_get()
    want = set(names) if names is not None else set()
    tris = verts = n = 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    hlo = [float("inf")] * 3
    hhi = [float("-inf")] * 3
    body_x: list[float] = []
    rows: dict = {}
    warn: list[str] = []
    parts: list[dict] = []
    objs = geometry_objects()
    form = bool(want) and not full and _form() is not None
    has_body = form and any(_hd(o) and (o.name.lower() == "chassis" or o.get("satk_part") == "chassis") for o in objs)
    for o in objs:
        hd = _hd(o)
        res = mesh_counts(o, dg, metrics=o.name in want, arrays=form and hd and (has_body or o.name in want),
                          full=full)
        t, v, corners, extra = res[:4]
        part = res[4] if len(res) > 4 else None
        n += 1
        tris += t
        verts += v
        for c in corners:
            for i in range(3):
                lo[i] = min(lo[i], c[i])
                hi[i] = max(hi[i], c[i])
                if hd:
                    hlo[i] = min(hlo[i], c[i])
                    hhi[i] = max(hhi[i], c[i])
        if hd and (o.name.lower() == "chassis" or o.get("satk_part") == "chassis") and corners:
            body_x += [c[0] for c in corners]
        if part is not None:
            parts.append(part)
        if o.name in want:
            row = {"tris": t, "verts": v, "dims": [_r2(x) for x in o.dimensions]}
            w = extra.pop("warn", None)
            if w and w not in warn:
                warn.append(w)
            row.update(extra)
            rows[o.name] = row
    for name in sorted(want - set(rows)):
        o = bpy.data.objects.get(name)
        if o is not None and o.name in bpy.context.scene.objects:
            rows[name] = {"type": o.type, "dims": [_r2(x) for x in o.dimensions]}
    scene: dict = {"objects": n, "tris": tris, "verts": verts}
    if n and lo[0] != float("inf"):
        scene["bbox"] = [[_r2(x) for x in lo], [_r2(x) for x in hi]]
        a, b = (hlo, hhi) if hlo[0] != float("inf") else (lo, hi)
        dims = [b[i] - a[i] for i in range(3)]
        if body_x:
            dims[0] = max(body_x) - min(body_x)            # the body only: mirrors on doors excluded
        scene["dims"] = [_r2(x) for x in dims]
        td = _cfg["target_dims"]
        if td:
            scene["vs_target"] = [round(d / t_, 3) for d, t_ in zip(dims, td)]
    out: dict = {"scene": scene}
    if rows:
        out["objects"] = rows
    if form and parts and tris <= FORM_MAX_TRIS:
        focus = sorted(want)
        mb, mw_ = mesh_block([dict(p) for p in parts], focus)
        block, fw = form_block(parts, focus)
        if block:
            out["form"] = block
        if mb:
            out["mesh"] = mb
        warn += fw + mw_
    if warn:
        out["warn"] = warn
    return out
