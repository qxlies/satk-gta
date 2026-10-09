# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.shade`` (sa_shade): vanilla-like smooth shading in the one order that survives a DragonFF export.

1. weld by distance (glass and double-sided faces are skipped);
2. ``use_smooth`` on every face;
3. hard edges by the vanilla rule (``mode=seams``, the default): an edge is hard where the look changes or where the
   author drew a line, never because a corner is steep. Hard: material borders, UV seams that fold more than
   ``seam_angle`` (default 20 deg), named creases (edges the agent marked sharp with ``mesh.mark``, Blender edge
   creases, the edge attribute ``satk_crease``) and real fold-backs above ``hard`` (default 110 deg). Everything
   else is smooth, also 60-90 deg corners (vanilla keeps 22-45 % of them smooth and builds big turns from 2-3 soft
   steps); edges in the attribute ``satk_soft`` stay smooth even on a UV seam. Bumpers and wheels: only material
   borders. ``mode=angle`` is the old dihedral rule (smooth below ``smooth``, hard above ``hard``, marked edges in
   between). Edges this step made hard are recorded (``satk_auto_sharp``) so a re-run starts from the author's own
   marks;
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

__all__ = ["RULES", "shade_object", "dff_shade_metrics", "mesh_k1", "match_seams"]

#: Dihedral rule per class (degrees): below ``smooth`` always smooth, above ``hard`` always sharp, between
#: them an edge is sharp only when the agent marked it (``design``).
RULES = {
    "vehicle": {"smooth": 30.0, "hard": 60.0, "seams": True, "materials": True},
    "ped": {"smooth": 180.0, "hard": 180.0, "seams": False, "materials": False},
    "weapon": {"smooth": 20.0, "hard": 40.0, "seams": True, "materials": True},
    "world": {"smooth": 30.0, "hard": 50.0, "seams": True, "materials": True},
}
#: The vanilla (``seams``) rule per class: ``hard`` = fold-back above which an edge is always hard, ``seam_angle`` =
#: the fold a UV seam needs to be hard (a seam across a flat or gently curved panel stays smooth).
SEAM_RULES = {
    "vehicle": {"hard": 110.0, "seam_angle": 20.0, "seams": True, "materials": True},
    "ped": {"hard": 180.0, "seam_angle": 180.0, "seams": False, "materials": False},
    "weapon": {"hard": 75.0, "seam_angle": 15.0, "seams": True, "materials": True},
    "world": {"hard": 80.0, "seam_angle": 20.0, "seams": True, "materials": True},
}
MODES = ("seams", "angle")
_AUTO = "satk_auto_sharp"
#: Parts that are fully smooth (only material borders and seams stay hard).
_SMOOTH_PARTS = ("bump_", "wheel", "tyre", "tire")
_NO_WELD_ROLES = ("glass", "map_alpha", "gunflash")
_K1_KEYS = ("shade.normal_bend", "shade.flat_share", "shade.hard_edge_share", "dff.verts_per_tri", "geo.tris")


def _rule(o, cls: str | None, mode: str = "seams") -> dict:
    table = SEAM_RULES if mode == "seams" else RULES
    if cls and cls in table:
        r = dict(table[cls])
    else:
        coll = next((c for c in o.users_collection if c.get("satk_group")), None)
        grp = str(coll.get("satk_group")) if coll is not None else "vehicle"
        kind = str(coll.get("satk_kit")) if coll is not None else ""
        r = dict(table["ped" if kind == "ped" else "weapon" if kind in ("weapon", "weapon_melee") else
                       grp if grp in table else "vehicle"])
    part = str(o.get("satk_part") or o.name).lower()
    if any(part.startswith(p) for p in _SMOOTH_PARTS):
        if mode == "seams":
            r["hard"], r["seam_angle"] = 180.0, 180.0
        else:
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


def _edge_flags(me, name: str) -> set:
    """Indices of the edges whose boolean (or float > 0) edge attribute ``name`` is set."""
    a = me.attributes.get(name)
    if a is None or a.domain != "EDGE":
        return set()
    return {i for i, d in enumerate(a.data) if d.value}


def _crease_edges(me) -> set:
    """Edges with a Blender edge crease above 0 (``crease_edge``) or the ``satk_crease`` attribute."""
    out = _edge_flags(me, "satk_crease")
    a = me.attributes.get("crease_edge")
    if a is not None and a.domain == "EDGE":
        out |= {i for i, d in enumerate(a.data) if d.value > 0.0}
    return out


def _store_auto(me, auto: set) -> None:
    a = me.attributes.get(_AUTO)
    if a is None:
        a = me.attributes.new(_AUTO, "BOOLEAN", "EDGE")
    a.data.foreach_set("value", [i in auto for i in range(len(me.edges))])


def shade_object(o, *, cls: str | None = None, weld: float = 0.0005, weighted: bool = True,
                 smooth: float | None = None, hard: float | None = None, seams: bool | None = None,
                 materials: bool | None = None, warn: list[str] | None = None, mode: str = "seams",
                 seam_angle: float | None = None) -> dict:
    """Steps 1-4 on ``o`` (its base mesh; modifiers stay live). Returns counts."""
    warn = warn if warn is not None else []
    if o.type != "MESH":
        raise SatkError("BAD_PARAMS", f"kit.shade: {o.name} is not a mesh")
    if mode not in MODES:
        raise SatkError("BAD_PARAMS", f"kit.shade: mode {mode!r}", hint="seams (vanilla rule) | angle")
    rule = _rule(o, cls, mode)
    if smooth is not None:
        rule["smooth"] = float(smooth)
    if hard is not None:
        rule["hard"] = float(hard)
    if seam_angle is not None:
        rule["seam_angle"] = float(seam_angle)
    if seams is not None:
        rule["seams"] = bool(seams)
    if materials is not None:
        rule["materials"] = bool(materials)
    me = o.data
    if not len(me.polygons):
        return {"object": o.name, "skipped": "empty mesh"}
    # the author's own marks: sharp edges this step did not make (a previous run recorded its own)
    auto_before = _edge_flags(me, _AUTO)
    marked = {tuple(sorted(e.vertices)) for e in me.edges if e.use_edge_sharp and e.index not in auto_before}
    creased = {tuple(sorted(me.edges[i].vertices)) for i in _crease_edges(me)}
    soft = {tuple(sorted(me.edges[i].vertices)) for i in _edge_flags(me, "satk_soft")}
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
        hi = math.radians(rule["hard"])
        lo = math.radians(rule.get("smooth", 30.0))
        seam_lim = math.radians(rule.get("seam_angle", 0.0))
        n_sharp = n_design = n_seam = 0
        auto: set = set()
        for e in bm.edges:
            if len(e.link_faces) != 2:
                e.smooth = True
                continue
            f1, f2 = e.link_faces
            ang = f1.normal.angle(f2.normal, 0.0)
            key = tuple(sorted(v.index for v in e.verts))
            sharp = by_rule = False
            if rule["materials"] and f1.material_index != f2.material_index:
                sharp = by_rule = True
            elif mode == "seams":
                if key in marked or key in creased:            # a named crease: the author's line
                    sharp = ang > math.radians(1.0)
                    n_design += sharp
                elif key in soft:
                    sharp = False
                elif e.index in seam_edges and ang >= seam_lim:
                    sharp = by_rule = True
                    n_seam += 1
                elif ang > hi:
                    sharp = by_rule = True
            else:
                if e.index in seam_edges:
                    sharp = by_rule = True
                elif ang > hi:
                    sharp = by_rule = True
                elif ang >= lo and key in marked:  # the agent's design mark in the 30-60 band
                    sharp = True
                    n_design += 1
            e.smooth = not sharp
            n_sharp += sharp
            if sharp and by_rule:
                auto.add(key)
        bm.to_mesh(me)
    finally:
        bm.free()
    me.update()
    _store_auto(me, {e.index for e in me.edges if tuple(sorted(e.vertices)) in auto})
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
    out = {"object": o.name, "welded": welded, "sharp": n_sharp, "design": n_design, "mode": mode,
           "rule": ([rule["hard"], rule["seam_angle"]] if mode == "seams" else [rule["smooth"], rule["hard"]])}
    if n_seam:
        out["seam_hard"] = n_seam
    return out


#: Two parts meet at a seam when their border vertices lie this close (m).
SEAM_DIST = 0.001
#: Normals on the two sides of a seam closer than this (degrees) are one surface (a panel cut); wider = a crease
#: (kit.shade passes the class's hard fold angle, 110 for vehicles: a cut along a 90 deg body corner stays soft).
SEAM_MATCH_DEG = 110.0


def match_seams(objs, *, dist: float = SEAM_DIST, max_deg: float = SEAM_MATCH_DEG) -> dict:
    """Shade the cuts between parts as one surface (a bonnet, a door or a boot cut out of the shell).

    A part's border vertex averages only its own faces, so along every panel line the two sides tilt apart and the
    panels read as faceted lids. Here the border corners of every object that coincide (``dist``) with border
    corners of another object take the mean of the normals on both sides that lie within ``max_deg`` of their own
    (a real crease stays hard). The evaluated normals (Weighted Normal included) are baked into custom normals and
    the ``satk_wn`` modifier is removed on the objects that changed (re-run kit.shade after editing them).
    Objects with other modifiers are left alone. Returns ``{"objects", "corners", "skipped"}``.
    """
    import numpy as np
    from mathutils import Vector
    from mathutils.kdtree import KDTree

    dg = bpy.context.evaluated_depsgraph_get()
    data = []
    skipped = []
    for o in objs:
        if o.type != "MESH" or not len(o.data.polygons):
            continue
        if any(m.type != "WEIGHTED_NORMAL" for m in o.modifiers):
            skipped.append(o.name)
            continue
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        try:
            mw = o.matrix_world
            r3 = mw.to_3x3()
            nrm_m = r3.inverted_safe().transposed()
            cn = [(nrm_m @ Vector(c.vector)).normalized() for c in me.corner_normals]
            pos = [mw @ v.co for v in me.vertices]
            border = set()
            ecount: dict = {}
            for poly in me.polygons:
                for ek in poly.edge_keys:
                    ecount[ek] = ecount.get(ek, 0) + 1
            for (a, b), n in ecount.items():
                if n == 1:
                    border.add(a)
                    border.add(b)
            loops_of: dict = {}
            for l in me.loops:
                loops_of.setdefault(l.vertex_index, []).append(l.index)
            data.append({"o": o, "cn": cn, "pos": pos, "loops_of": loops_of, "border": border, "nl": len(me.loops)})
        finally:
            ev.to_mesh_clear()
    if len(data) < 2:
        return {"objects": 0, "corners": 0, "skipped": skipped}
    pts = [(k, v) for k, d in enumerate(data) for v in d["loops_of"]]
    kd = KDTree(len(pts))
    for i, (k, v) in enumerate(pts):
        kd.insert(data[k]["pos"][v], i)
    kd.balance()
    lim = math.radians(max_deg)
    new: dict = {}
    done: set = set()
    for k, d in enumerate(data):
        for v in d["border"]:
            if (k, v) in done:
                continue
            group = [pts[j] for _co, j, _dd in kd.find_range(d["pos"][v], dist)]
            if len({kk for kk, _vv in group}) < 2:
                continue  # an open border of one part only
            done.update(group)
            normals = [(kk, li, data[kk]["cn"][li]) for kk, vv in group for li in data[kk]["loops_of"][vv]]
            # union-find: a corner joins the closest corner of every other part within the limit, and the corners of
            # its own fan; each group takes its mean (both sides of a cut get the same normal, own creases stay)
            parent = list(range(len(normals)))

            def root(i: int) -> int:
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i

            for i, (ka, _la, na) in enumerate(normals):
                best: dict = {}
                for j, (kb, _lb, nb) in enumerate(normals):
                    if j == i:
                        continue
                    a = na.angle(nb, math.pi)
                    if kb == ka:
                        if a < 1e-3:
                            parent[root(j)] = root(i)
                    elif a < lim and a < best.get(kb, (math.pi, -1))[0]:
                        best[kb] = (a, j)
                for _a, j in best.values():
                    parent[root(j)] = root(i)
            comp: dict = {}
            for i in range(len(normals)):
                comp.setdefault(root(i), []).append(i)
            for members in comp.values():
                if len({normals[i][0] for i in members}) < 2:
                    continue
                acc = Vector((0.0, 0.0, 0.0))
                for i in members:
                    acc += normals[i][2]
                if acc.length < 1e-6:
                    continue
                m = acc.normalized()
                for i in members:
                    kk, li, n = normals[i]
                    if (m - n).length > 1e-5:
                        new.setdefault(kk, {})[li] = m
    changed = 0
    corners = 0
    for k, repl in new.items():
        d = data[k]
        o = d["o"]
        if len(o.data.loops) != d["nl"]:
            skipped.append(o.name)
            continue
        inv = o.matrix_world.to_3x3().transposed()     # world -> object for a rotation (and uniform scale)
        out = []
        for li, n in enumerate(d["cn"]):
            w = repl.get(li, n)
            out.append(tuple((inv @ w).normalized()))
        wn = o.modifiers.get("satk_wn")
        if wn is not None:
            o.modifiers.remove(wn)
        o.data.normals_split_custom_set(out)
        o.data["satk_normals"] = "seams"
        o.dff.export_split_normals = True
        changed += 1
        corners += len(repl)
    return {"objects": changed, "corners": corners, "skipped": skipped}


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
