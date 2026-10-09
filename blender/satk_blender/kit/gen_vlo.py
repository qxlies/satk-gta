# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.vlo``: the vehicle's ``*_vlo`` slot from the undamaged body (chassis + ``*_ok`` parts, wheels left out)
in the vlo frame's space, at most 130 triangles (vanilla sedans 55-72-92).

``method=sections`` (default): a closed low loft that keeps the silhouette: 7 stations along the length, at each the
outline of the body (top centre, three heights on each side, the belly), dark faces where the body has glass, paint
UVs in the clean part of ``vehiclegrunge256``; about 100 triangles. ``method=decimate``: collapse decimation of the
evaluated meshes (small closed islands dropped when the budget needs it; it loses the silhouette of detailed
bodies). :func:`merged_mesh` and :func:`decimate_to` are shared with ``kit.lod``.
"""

from __future__ import annotations

import bmesh
import bpy

from satk.core.errors import SatkError

from . import util as U

__all__ = ["merged_mesh", "decimate_to", "replace_mesh", "vlo_method"]


def merged_mesh(sources: list, target, name: str):
    """One mesh of the evaluated ``sources`` in ``target``'s object space (materials merged by identity)."""
    inv = target.matrix_world.inverted_safe()
    bm = bmesh.new()
    mats: list = []
    try:
        for o in sources:
            me, free = U.evaluated_mesh(o)
            try:
                me.transform(inv @ o.matrix_world)
                remap = []
                # an evaluated mesh points at evaluated copies of the materials: use the originals (by name)
                for m in [bpy.data.materials.get(x.name) if x is not None else None for x in me.materials]:
                    if m not in mats:
                        mats.append(m)
                    remap.append(mats.index(m))
                for poly in me.polygons:
                    poly.material_index = remap[poly.material_index] if remap else 0
                bm.from_mesh(me)
            finally:
                free()
        out = bpy.data.meshes.new(name)
        bm.to_mesh(out)
    finally:
        bm.free()
    for m in mats:
        out.materials.append(m)
    return out


def _tris(me) -> int:
    return sum(len(p.vertices) - 2 for p in me.polygons)


def _collapsed(tmp, ratio: float):
    """The evaluated collapse-decimated mesh of ``tmp`` at ``ratio`` (a new mesh)."""
    mod = tmp.modifiers.new("dec", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    try:
        return bpy.data.meshes.new_from_object(tmp.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    finally:
        tmp.modifiers.remove(mod)


def _drop_small_islands(me, max_tris: int):
    """``me`` without its smallest connected islands (by area, the largest always stays) until it has at most ``max_tris`` triangles; ``satk_dropped_islands`` records the count."""
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    seen: set = set()
    islands = []
    for f in bm.faces:
        if f.index in seen:
            continue
        seen.add(f.index)
        stack, faces = [f], []
        while stack:
            x = stack.pop()
            faces.append(x)
            for e in x.edges:
                for g in e.link_faces:
                    if g.index not in seen:
                        seen.add(g.index)
                        stack.append(g)
        islands.append((sum(x.calc_area() for x in faces), faces[0].index, faces))
    islands.sort(key=lambda t: (t[0], t[1]))
    tris = sum(len(f.verts) - 2 for f in bm.faces)
    drop: list = []
    dropped = 0
    for _area, _first, faces in islands[:-1]:
        if tris <= max_tris:
            break
        drop += faces
        dropped += 1
        tris -= sum(len(f.verts) - 2 for f in faces)
    if drop:
        bmesh.ops.delete(bm, geom=drop, context="FACES")
    bm.to_mesh(me)
    bm.free()
    me["satk_dropped_islands"] = dropped
    return me


def decimate_to(me, max_tris: int, name: str):
    """Collapse-decimate ``me`` (consumed) until it has at most ``max_tris`` triangles; returns the new mesh."""
    t = _tris(me)
    if t <= max_tris:
        bm = bmesh.new()
        bm.from_mesh(me)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bm.to_mesh(me)
        bm.free()
        return me
    tmp = bpy.data.objects.new(f"{name}_satk_dec", me)
    bpy.context.scene.collection.objects.link(tmp)
    try:
        ratio = max_tris / t * 0.98
        for _ in range(7):
            mod = tmp.modifiers.new("dec", "DECIMATE")
            mod.decimate_type = "COLLAPSE"
            mod.ratio = max(0.001, min(1.0, ratio))
            mod.use_collapse_triangulate = True
            dg = bpy.context.evaluated_depsgraph_get()
            ev = tmp.evaluated_get(dg)
            new = bpy.data.meshes.new_from_object(ev)
            tmp.modifiers.remove(mod)
            if _tris(new) <= max_tris:
                break
            bpy.data.meshes.remove(new)
            ratio *= 0.7
        else:
            # many closed islands (interior boxes, underbody, small parts) keep a floor of triangles each
            # (about 8): drop the smallest islands of the most reduced mesh until the budget holds
            new = _drop_small_islands(_collapsed(tmp, 0.001), max_tris)
            if _tris(new) > max_tris:
                raise SatkError("INTERNAL", f"decimation of {name} did not reach {max_tris} triangles")
    finally:
        bpy.data.objects.remove(tmp, do_unlink=True)
    bm = bmesh.new()
    bm.from_mesh(new)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(new)
    bm.free()
    if me.users == 0:
        bpy.data.meshes.remove(me)
    return new


def _role(m) -> str:
    return str(m.get("satk_role", "")) if m is not None else ""


def section_lod(me, name: str, stations: int = 7, levels=(0.86, 0.52, 0.14)):
    """A closed sectioned loft of ``me`` (consumed): see the module docstring. Returns a new triangulated mesh."""
    import numpy as np

    n = len(me.vertices)
    co = np.empty(n * 3)
    me.vertices.foreach_get("co", co)
    P = co.reshape(n, 3)
    mats = list(me.materials)
    # vertices of glass faces: where the low model gets its dark window band
    glass_idx = {i for i, m in enumerate(mats) if _role(m) == "glass"}
    gv = sorted({v for p in me.polygons if p.material_index in glass_idx for v in p.vertices})
    G = P[gv] if gv else np.zeros((0, 3))
    paint = next((m for m in mats if _role(m) == "paint1"), None) or next((m for m in mats if m is not None), None)
    dark = next((m for m in mats if _role(m) in ("black", "trim")), None) or paint
    lo, hi = P.min(axis=0), P.max(axis=0)
    L = float(hi[1] - lo[1])
    ys = np.linspace(lo[1] + 0.012 * L, hi[1] - 0.012 * L, stations)
    win = L / (2.0 * (stations - 1)) + 1e-6
    rings, glassy = [], []
    for y in ys:
        sl = P[np.abs(P[:, 1] - y) <= win]
        if not len(sl):
            sl = P
        z0, z1 = float(sl[:, 2].min()), float(sl[:, 2].max())
        hz = max(z1 - z0, 1e-3)
        half = []
        for f in levels:
            z = z0 + f * hz
            band = sl[np.abs(sl[:, 2] - z) <= 0.12 * hz]
            half.append((float(np.abs(band[:, 0]).max()) if len(band) else 0.3, z))
        # top centre, the right side top to bottom, the belly centre, the left side bottom to top
        ring = [(0.0, z1)] + [(x, z) for x, z in half] + [(0.0, z0)] + [(-x, z) for x, z in reversed(half)]
        rings.append([(x, float(y), z) for x, z in ring])
        gs = G[np.abs(G[:, 1] - y) <= win] if len(G) else G
        glassy.append(bool(len(gs)) and float(gs[:, 2].mean()) > z0 + 0.45 * hz)
    import bmesh

    bm = bmesh.new()
    try:
        uvl = bm.loops.layers.uv.new("UVMap")
        vs = [[bm.verts.new(p) for p in ring] for ring in rings]
        R = len(rings[0])
        vrow = [0.97, 0.9, 0.62, 0.3, 0.04, 0.3, 0.62, 0.9]
        for i in range(len(vs) - 1):
            for k in range(R):
                k2 = (k + 1) % R
                f = bm.faces.new((vs[i][k], vs[i + 1][k], vs[i + 1][k2], vs[i][k2]))
                window = (k in (1, R - 2)) and glassy[i] and glassy[i + 1]
                f.material_index = 1 if window else 0
                for lp, (ii, kk) in zip(f.loops, ((i, k), (i + 1, k), (i + 1, k2), (i, k2))):
                    lp[uvl].uv = (0.03 + 0.2 * ii / (len(vs) - 1), vrow[kk % len(vrow)])
        for ring, sign in ((vs[0], -1.0), (vs[-1], 1.0)):
            f = bm.faces.new(ring if sign > 0 else list(reversed(ring)))
            for lp in f.loops:
                lp[uvl].uv = (0.1, 0.9)
        bm.normal_update()
        for f in bm.faces:                       # outward: away from the body's centre line
            c = f.calc_center_median()
            out_dir = c - type(c)((0.0, c.y, (lo[2] + hi[2]) / 2.0))
            if abs(f.normal.y) > 0.9:            # the end caps
                out_dir = type(c)((0.0, c.y - (lo[1] + hi[1]) / 2.0, 0.0))
            if f.normal.dot(out_dir) < 0:
                f.normal_flip()
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        out = bpy.data.meshes.new(name)
        bm.to_mesh(out)
    finally:
        bm.free()
    out.materials.append(paint)
    out.materials.append(dark)
    if me.users == 0:
        bpy.data.meshes.remove(me)
    return out


def replace_mesh(slot, me) -> None:
    old = slot.data
    me.name = slot.name
    slot.data = me
    if old is not None and old.users == 0:
        bpy.data.meshes.remove(old)


def vlo_method(ctx, p: dict) -> dict:
    """Fill the vehicle *_vlo slot from the undamaged body (chassis + *_ok parts, no wheels): method sections (default: a closed low loft that keeps the silhouette, dark window band, about 100 triangles) or decimate; at most tris_max (130)."""
    coll = U.clump(p.get("model"))
    target_name = str(p.get("target") or "")
    slots = [o for o in coll.objects if o.type == "MESH" and o.get("satk_slot") == "vlo"]
    if target_name:
        slots = [o for o in slots if str(o.get("satk_frame", o.name)).lower() == target_name.lower()]
    if not slots:
        raise SatkError("NOT_FOUND", "kit.vlo: no *_vlo slot in the kit model", hint="vehicles have chassis_vlo")
    slot = slots[0]
    max_tris = int(U.num(p, "tris_max", 130, "kit.vlo", 12, 2000))
    names = p.get("sources")
    if names:
        src = U.objs(names, "kit.vlo")
    else:
        src = [o for o in coll.objects if o.type == "MESH" and len(o.data.polygons)
               and o.get("satk_slot") in ("ok", "hd") and o.get("satk_part") not in ("wheel",)
               and not str(o.get("satk_part", "")).startswith("wheel")]
    if not src:
        raise SatkError("BAD_PARAMS", "kit.vlo: nothing to build from: model the chassis first")
    method = str(p.get("method") or "sections")
    if method not in ("sections", "decimate"):
        raise U.bad("kit.vlo", f"method {method!r}", hint="sections | decimate")
    me = merged_mesh(src, slot, slot.name)
    src_tris = _tris(me)
    if method == "sections":
        st = int(U.num(p, "stations", 7, "kit.vlo", 3, 16))
        me = section_lod(me, slot.name, stations=st)
        if _tris(me) > max_tris:
            me = decimate_to(me, max_tris, slot.name)
    else:
        me = decimate_to(me, max_tris, slot.name)
    dropped = int(me.get("satk_dropped_islands", 0))
    replace_mesh(slot, me)
    if method == "sections":
        while len(slot.data.uv_layers) < (2 if slot.dff.uv_map2 else 1):
            slot.data.uv_layers.new(name=f"UVMap{len(slot.data.uv_layers) + 1}")
    res = {"object": slot.name, "tris": U.tris(slot), "from_tris": src_tris, "sources": len(src), "method": method,
           "changed": [slot.name]}
    if dropped:
        res["dropped_islands"] = dropped      # small closed islands that collapse could not reduce below the budget
    return res
