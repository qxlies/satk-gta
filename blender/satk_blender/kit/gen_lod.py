# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.lod``: a map model's LOD (``lod<name>``) from its HD mesh at about 0.21 of the triangles
(vanilla LOD/HD p50 0.208, LOD draw 800); LOD budgets do not grow with the tier. A prop scaffolded without a LOD
slot gets one on the first call (the slot is made from the HD materials), so nothing is re-scaffolded.

The LOD keeps the HD outline: the budget never drops below 12 triangles per closed piece (a box each), and after
the decimation the outline is compared from the side, the front and above (rays on a grid); when the LOD covers less
than :data:`MIN_COVER` of the HD outline the budget is raised, and at last every piece becomes its bounding box (a
simple building: one box per block). The answer names how it was made and the coverage.
"""

from __future__ import annotations

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from satk.core.errors import SatkError

from . import util as U
from .gen_vlo import _tris, decimate_to, merged_mesh, replace_mesh

__all__ = ["lod_method", "coverage"]

#: The LOD must cover this share of the HD outline from each side (side, front, top).
MIN_COVER = 0.8


def _pieces(me) -> list[list[int]]:
    """Vertex indices of every connected piece of a mesh."""
    bm = bmesh.new()
    bm.from_mesh(me)
    try:
        bm.verts.ensure_lookup_table()
        seen: set = set()
        out = []
        for v in bm.verts:
            if v.index in seen or not v.link_faces:
                continue
            stack, comp = [v], []
            seen.add(v.index)
            while stack:
                x = stack.pop()
                comp.append(x.index)
                for e in x.link_edges:
                    o = e.other_vert(x)
                    if o.index not in seen:
                        seen.add(o.index)
                        stack.append(o)
            out.append(comp)
        return out
    finally:
        bm.free()


def _bvh(me):
    bm = bmesh.new()
    bm.from_mesh(me)
    try:
        return BVHTree.FromBMesh(bm)
    finally:
        bm.free()


def coverage(hd, lod, n: int = 40) -> float:
    """The least share of the HD outline the LOD covers, seen along X, Y and Z (rays on an ``n x n`` grid)."""
    if not len(hd.vertices) or not len(lod.vertices):
        return 0.0
    co = [v.co for v in hd.vertices]
    lo = Vector((min(c[i] for c in co) for i in range(3)))
    hi = Vector((max(c[i] for c in co) for i in range(3)))
    pad = max(hi - lo) * 0.05 + 0.01
    th, tl = _bvh(hd), _bvh(lod)
    worst = 1.0
    for ax in range(3):
        a, b = [i for i in range(3) if i != ax]
        if hi[a] - lo[a] < 1e-4 or hi[b] - lo[b] < 1e-4:
            continue
        nh = both = 0
        d = Vector((0.0, 0.0, 0.0))
        d[ax] = 1.0
        for i in range(n):
            for j in range(n):
                o = Vector((0.0, 0.0, 0.0))
                o[a] = lo[a] + (hi[a] - lo[a]) * (i + 0.5) / n
                o[b] = lo[b] + (hi[b] - lo[b]) * (j + 0.5) / n
                o[ax] = lo[ax] - pad
                if th.ray_cast(o, d, (hi[ax] - lo[ax]) + 2 * pad)[0] is None:
                    continue
                nh += 1
                if tl.ray_cast(o, d, (hi[ax] - lo[ax]) + 2 * pad)[0] is not None:
                    both += 1
        if nh >= 20:
            worst = min(worst, both / nh)
    return worst


def _boxes(me, name: str):
    """One axis-aligned box (12 triangles) per connected piece of ``me``, the pieces' materials kept."""
    bm = bmesh.new()
    try:
        for comp in _pieces(me):
            cs = [me.vertices[i].co for i in comp]
            lo = [min(c[k] for c in cs) for k in range(3)]
            hi = [max(c[k] for c in cs) for k in range(3)]
            if any(hi[k] - lo[k] < 1e-3 for k in range(3)):
                hi = [max(hi[k], lo[k] + 0.02) for k in range(3)]
            res = bmesh.ops.create_cube(bm, size=1.0)
            for v in res["verts"]:
                v.co = Vector(tuple(lo[k] + (hi[k] - lo[k]) * (v.co[k] + 0.5) for k in range(3)))
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        out = bpy.data.meshes.new(name)
        bm.to_mesh(out)
    finally:
        bm.free()
    for m in me.materials:
        out.materials.append(m)
    return out


def lod_method(ctx, p: dict) -> dict:
    """Fill a building's LOD slot (lod<name>) from the HD mesh at about 0.21 of its triangles, keeping its outline."""
    coll = U.clump(p.get("model"))
    name = str(coll.get("satk_name"))
    lc = next((c for c in bpy.data.collections if str(c.get("satk_lod_of", "")).lower() == name.lower()), None)
    ratio = U.num(p, "ratio", 0.21, "kit.lod", 0.01, 1.0)
    src = [o for o in coll.objects if o.type == "MESH" and len(o.data.polygons) and o.get("satk_role") in ("root", "part")]
    made = False
    if lc is None:
        if str(coll.get("satk_group")) != "world":
            raise SatkError("NOT_FOUND", f"kit.lod: {name} has no LOD slot",
                            hint="vehicles carry _vlo parts (kit.vlo); map models get a LOD slot")
        if not src:
            raise SatkError("BAD_PARAMS", "kit.lod: model the HD mesh first")
        from satk.kit.plan import lod_name

        from .template import make_lod_slot

        mats = []
        for o in src:
            for m in o.data.materials:
                if m is not None and m not in mats:
                    mats.append(m)
        make_lod_slot(name, lod_name(name), str(coll.get("satk_kit")), mats)
        lc = next(c for c in bpy.data.collections if str(c.get("satk_lod_of", "")).lower() == name.lower())
        made = True
    slot = next((o for o in lc.objects if o.type == "MESH"), None)
    if not src or slot is None:
        raise SatkError("BAD_PARAMS", "kit.lod: model the HD mesh first")
    hd = merged_mesh(src, slot, slot.name + "_satk_hd")
    t = _tris(hd)
    floor = min(t, 12 * len(_pieces(hd)))                 # a box per closed piece at the least
    target = max(12, int(round(t * ratio)), floor)
    best = None
    how = "decimate"
    for k in (1.0, 1.6, 2.5, 4.0):
        cand = decimate_to(hd.copy(), min(t, int(target * k)), slot.name)
        cov = coverage(hd, cand)
        if best is None or cov > best[1]:
            if best is not None:
                bpy.data.meshes.remove(best[0])
            best = (cand, cov)
        else:
            bpy.data.meshes.remove(cand)
        if cov >= MIN_COVER or int(target * k) >= t:
            break
    if best[1] < MIN_COVER:
        boxes = _boxes(hd, slot.name)
        cov = coverage(hd, boxes)
        if cov > best[1] and _tris(boxes) <= max(t, 12):
            bpy.data.meshes.remove(best[0])
            best = (boxes, cov)
            how = "boxes"
        else:
            bpy.data.meshes.remove(boxes)
    me, cov = best
    bpy.data.meshes.remove(hd)
    replace_mesh(slot, me)
    out = {"object": slot.name, "tris": U.tris(slot), "hd_tris": t, "ratio": round(U.tris(slot) / max(1, t), 3),
           "coverage": round(cov, 3), "how": how, "changed": [slot.name]}
    if cov < MIN_COVER:
        ctx.warn(f"OUTLINE: the LOD covers only {cov:.0%} of the HD outline: shape it by hand (closed blocks)")
    if made:
        out["slot"] = "created"
    return out
