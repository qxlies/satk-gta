# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.shade`` (sa_shade): vanilla-like smooth shading in the one order that survives a DragonFF export.

1. weld by distance (glass and double-sided faces are skipped);
2. ``use_smooth`` on every face;
3. sharp edges at material borders, UV seams and creases by the class dihedral rule (cars: smooth below
   30 deg, by design 30-60 deg - an edge the agent marked sharp stays sharp - hard above 60 deg; bumpers and
   wheels fully smooth; peds smooth; firearms harder);
4. normals last: a Weighted Normal modifier (keep sharp) at the end of the stack, DragonFF split normals on;
5. re-read: the object is exported alone with DragonFF, the DFF is decoded and measured with the canonical
   metrics (K1, ``satk.style.metrics``); the step fails when the normals did not change a faceted mesh.

Order rule (verified on Blender 5.1): Weighted Normal or custom normals on flat faces export fully flat,
and toggling smooth after custom normals moves them, so smoothing comes first and normals last.
"""

from __future__ import annotations

import math
import os

import bmesh
import bpy

from satk.core.errors import SatkError

from . import util as U

__all__ = ["RULES", "shade_object", "dff_shade_metrics", "mesh_k1"]

#: Dihedral rule per class (degrees): below ``smooth`` always smooth, above ``hard`` always sharp, between
#: them an edge is sharp only when the agent marked it (``design``).
RULES = {
    "vehicle": {"smooth": 30.0, "hard": 60.0, "seams": True, "materials": True},
    "ped": {"smooth": 180.0, "hard": 180.0, "seams": False, "materials": False},
    "weapon": {"smooth": 20.0, "hard": 40.0, "seams": True, "materials": True},
    "world": {"smooth": 30.0, "hard": 50.0, "seams": True, "materials": True},
}
#: Parts that are fully smooth (only material borders and seams stay hard).
_SMOOTH_PARTS = ("bump_", "wheel", "tyre", "tire")
_NO_WELD_ROLES = ("glass", "map_alpha", "gunflash")
_K1_KEYS = ("shade.normal_bend", "shade.flat_share", "shade.hard_edge_share", "dff.verts_per_tri", "geo.tris")


def _rule(o, cls: str | None) -> dict:
    if cls and cls in RULES:
        r = dict(RULES[cls])
    else:
        coll = next((c for c in o.users_collection if c.get("satk_group")), None)
        grp = str(coll.get("satk_group")) if coll is not None else "vehicle"
        kind = str(coll.get("satk_kit")) if coll is not None else ""
        r = dict(RULES["ped" if kind == "ped" else "weapon" if kind == "weapon" else
                       grp if grp in RULES else "vehicle"])
    part = str(o.get("satk_part") or o.name).lower()
    if any(part.startswith(p) for p in _SMOOTH_PARTS):
        r["smooth"] = r["hard"] = 89.0
    return r


def _no_weld_material(mat) -> bool:
    return mat is not None and (str(mat.get("satk_role", "")) in _NO_WELD_ROLES or bool(mat.get("satk_double_sided")))


def mesh_k1(me) -> dict:
    """Canonical K1 metrics of a mesh (corner normals of its loop triangles)."""
    import numpy as np

    from satk.style.metrics import mesh_metrics

    me.calc_loop_triangles()
    lt = me.loop_triangles
    if not len(lt):
        return {}
    pos = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", pos)
    tri = np.empty(len(lt) * 3, np.int64)
    lt.foreach_get("vertices", tri)
    loops = np.empty(len(lt) * 3, np.int64)
    lt.foreach_get("loops", loops)
    cn = np.empty(len(me.loops) * 3, np.float64)
    me.corner_normals.foreach_get("vector", cn)
    mat = np.empty(len(lt), np.int64)
    lt.foreach_get("material_index", mat)
    m = mesh_metrics(pos.reshape(-1, 3), tri.reshape(-1, 3), corner_normals=cn.reshape(-1, 3)[loops], mat=mat)
    return {k: m[k] for k in _K1_KEYS if k in m}


def _uv_seam_edges(bm) -> set:
    uv = bm.loops.layers.uv.active
    out = set()
    if uv is None:
        return out
    for e in bm.edges:
        if len(e.link_loops) != 2:
            continue
        uvs = []
        for lp in e.link_loops:  # the two faces' UVs at each end of the edge
            uvs.append({lp.vert.index: lp[uv].uv.copy(), lp.link_loop_next.vert.index: lp.link_loop_next[uv].uv.copy()})
        a, b = uvs
        if any(k not in b or (a[k] - b[k]).length > 1e-5 for k in a):
            out.add(e.index)
    return out


def _remove_auto_smooth(o, warn: list[str]) -> None:
    for m in list(o.modifiers):
        ng = getattr(m, "node_group", None)
        if m.type == "NODES" and ng is not None and "smooth by angle" in ng.name.lower():
            o.modifiers.remove(m)
            warn.append(f"INFO: {o.name}: removed the 'Smooth by Angle' modifier (sa_shade marks sharp edges itself)")


def shade_object(o, *, cls: str | None = None, weld: float = 0.0005, weighted: bool = True,
                 smooth: float | None = None, hard: float | None = None, seams: bool | None = None,
                 materials: bool | None = None, warn: list[str] | None = None) -> dict:
    """Steps 1-4 on ``o`` (its base mesh; modifiers stay live). Returns counts."""
    warn = warn if warn is not None else []
    if o.type != "MESH":
        raise SatkError("BAD_PARAMS", f"kit.shade: {o.name} is not a mesh")
    rule = _rule(o, cls)
    if smooth is not None:
        rule["smooth"] = float(smooth)
    if hard is not None:
        rule["hard"] = float(hard)
    if seams is not None:
        rule["seams"] = bool(seams)
    if materials is not None:
        rule["materials"] = bool(materials)
    me = o.data
    if not len(me.polygons):
        return {"object": o.name, "skipped": "empty mesh"}
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()
        mats = list(me.materials)
        # 1. weld (not glass / double-sided faces)
        keep = set()
        for f in bm.faces:
            mi = f.material_index
            if mi < len(mats) and _no_weld_material(mats[mi]):
                keep.update(v.index for v in f.verts)
        nv0 = len(bm.verts)
        if weld > 0:
            bmesh.ops.remove_doubles(bm, verts=[v for v in bm.verts if v.index not in keep], dist=float(weld))
        welded = nv0 - len(bm.verts)
        bm.edges.index_update()
        bm.faces.index_update()
        # 2. smooth faces
        for f in bm.faces:
            f.smooth = True
        # 3. sharp edges by the rule
        seam_edges = _uv_seam_edges(bm) if rule["seams"] else set()
        lo, hi = math.radians(rule["smooth"]), math.radians(rule["hard"])
        n_sharp = n_design = 0
        for e in bm.edges:
            if len(e.link_faces) != 2:
                e.smooth = True
                continue
            f1, f2 = e.link_faces
            ang = f1.normal.angle(f2.normal, 0.0)
            sharp = False
            if rule["materials"] and f1.material_index != f2.material_index:
                sharp = True
            elif e.index in seam_edges:
                sharp = True
            elif ang > hi:
                sharp = True
            elif ang >= lo and not e.smooth:  # the agent's design mark in the 30-60 band
                sharp = True
                n_design += 1
            e.smooth = not sharp
            n_sharp += sharp
        bm.to_mesh(me)
    finally:
        bm.free()
    me.update()
    # 4. normals last
    _remove_auto_smooth(o, warn)
    wn = o.modifiers.get("satk_wn")
    if weighted:
        if wn is None:
            wn = o.modifiers.new("satk_wn", "WEIGHTED_NORMAL")
        wn.mode = "FACE_AREA"
        wn.weight = 50
        wn.keep_sharp = True
        wn.thresh = 0.01
        names = [m.name for m in o.modifiers]
        if names[-1] != wn.name:
            o.modifiers.move(names.index(wn.name), len(names) - 1)
    elif wn is not None:
        o.modifiers.remove(wn)
    o.dff.export_split_normals = True
    return {"object": o.name, "welded": welded, "sharp": n_sharp, "design": n_design,
            "rule": [rule["smooth"], rule["hard"]]}


def _export_one(o, path: str) -> None:
    from satk_blender import common, exporter

    de = common.dff_module("ops.dff_exporter")
    state = exporter.SceneState()
    state.keep_materials([s.material for s in o.material_slots if s.material])
    try:
        for x in bpy.context.view_layer.objects:
            x.select_set(False)
        o.hide_set(False)
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        de.export_dff({
            "file_name": path, "directory": os.path.dirname(path), "selected": True, "mass_export": False,
            "preserve_positions": True, "preserve_rotations": True, "version": 0x36003,
            "export_coll": False, "coll_ext_type": 0, "apply_coll_trans": True,
            "export_frame_names": True, "exclude_geo_faces": False, "from_outliner": False,
        })
    finally:
        state.restore()
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise SatkError("EXTERNAL_TOOL", f"kit.shade: DragonFF wrote no DFF for {o.name}")


def dff_shade_metrics(o, out_dir: str) -> dict:
    """Export ``o`` alone with DragonFF and measure the re-read DFF (K1 keys)."""
    from satk.style.dffmesh import dff_metrics

    d = os.path.join(out_dir, "kit_shade")
    os.makedirs(d, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in o.name)[:40] or "obj"
    path = os.path.join(d, f"{safe}.dff")
    _export_one(o, path)
    with open(path, "rb") as f:
        buf = f.read()
    m = dff_metrics(buf, select="all", name=safe)["metrics"]
    return {k: m[k] for k in _K1_KEYS if k in m}
