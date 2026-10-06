# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.col``: the editable collision of a kit model in Blender (the DragonFF fallback of ``col.gen``).

* vehicles: a closed shadow mesh (convex hull of the undamaged body, decimated to the vanilla face count,
  150-350 for a sedan) next to the sphere skeleton of the template;
* world models: a hull, box or decimated mesh with the surface id and the face light (day/night nibbles
  from the mean prelight, never 0 = ambient only).

``kit.export`` prefers ``col.gen`` on the exported DFF when it is installed (better sphere fits, surfaces
from textures); ``col: kit`` keeps this collision.
"""

from __future__ import annotations

import bmesh
import bpy

from satk.core.errors import SatkError

from . import util as U
from .gen_vlo import decimate_to, merged_mesh

__all__ = ["col_method", "face_light"]


def face_light(o, coeffs=((0.0809, 2.385), (0.0889, 0.8793))) -> tuple[int, int]:
    """Day/night light nibbles from the mean prelight (colour attributes 0/1) of ``o``; (15, 3) without."""
    me = o.data if o is not None and o.type == "MESH" else None
    if me is None or not len(me.color_attributes):
        return 15, 3

    def mean(attr) -> float:
        n = len(attr.data)
        if not n:
            return -1.0
        import numpy as np

        buf = np.empty(n * 4, dtype=np.float32)
        attr.data.foreach_get("color_srgb", buf)
        rgb = buf.reshape(n, 4)[:, :3]
        return float((rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)).mean() * 255.0)

    attrs = list(me.color_attributes)
    day = mean(attrs[0])
    night = mean(attrs[1]) if len(attrs) > 1 else -1.0
    d = int(min(15, max(1, round(coeffs[0][0] * day + coeffs[0][1])))) if day >= 0 else 15
    n = int(min(15, max(0, round(coeffs[1][0] * night + coeffs[1][1])))) if night >= 0 else 3
    return d, n


def _hull(me) -> object:
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        res = bmesh.ops.convex_hull(bm, input=bm.verts[:], use_existing_faces=False)
        junk = set(res.get("geom_interior", [])) | set(res.get("geom_unused", []))
        bmesh.ops.delete(bm, geom=[g for g in junk if isinstance(g, bmesh.types.BMVert)], context="VERTS")
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        out = bpy.data.meshes.new("satk_hull")
        bm.to_mesh(out)
    finally:
        bm.free()
    return out


def _col_coll(name: str):
    c = next((x for x in bpy.data.collections if str(x.get("satk_col_of", "")).lower() == name.lower()), None)
    if c is None:
        parent = next((x for x in bpy.data.collections if str(x.get("satk_name", "")).lower() == name.lower()
                       and not x.get("satk_lod_of")), None)
        c = bpy.data.collections.new(f"{name}_col")
        (parent or bpy.context.scene.collection).children.link(c)
        c["satk_col_of"] = name
    return c


def _slot(c, name: str, typ: str, role: str):
    o = next((x for x in c.objects if x.get("satk_role") == role and x.type == "MESH"), None)
    if o is None:
        o = bpy.data.objects.new(name, bpy.data.meshes.new(name))
        o.dff.type = typ
        o["satk_role"] = role
        c.objects.link(o)
    o.hide_render = True
    o.display_type = "WIRE"
    return o


def _put(o, me) -> None:
    old = o.data
    me.name = o.name
    o.data = me
    o.matrix_world.identity()
    if old is not None and old.users == 0:
        bpy.data.meshes.remove(old)


def col_method(ctx, p: dict) -> dict:
    """Collision in Blender: a closed shadow mesh for vehicles, hull/box/mesh with face light for world models."""
    coll = U.clump(p.get("model"))
    name = str(coll.get("satk_name"))
    group = str(coll.get("satk_group") or "vehicle")
    root = U.kit_root(coll)
    src = [o for o in coll.objects if o.type == "MESH" and len(o.data.polygons)
           and o.get("satk_slot") in ("ok", "hd")]
    if not src:
        raise SatkError("BAD_PARAMS", "kit.col: model the undamaged parts first")
    c = _col_coll(name)
    out: dict = {"model": name}
    if group == "vehicle":
        faces = int(U.num(p, "shadow_faces", 280, "kit.col", 12, 5000))
        sh = _slot(c, f"{name}_shadow", "SHA", "shadow")
        base = root if root is not None else src[0]
        me = merged_mesh(src, base, sh.name)
        hull = _hull(me)
        bpy.data.meshes.remove(me)
        hull.transform(base.matrix_world)
        hull = decimate_to(hull, faces, sh.name)
        for mat in list(hull.materials):
            hull.materials.pop()
        _put(sh, hull)
        out.update(shadow_faces=len(hull.polygons))
        out["changed"] = [sh.name]
    else:
        mode = str(p.get("mode") or "hull").lower()
        if mode not in ("hull", "box", "mesh"):
            raise U.bad("kit.col", f"mode {mode!r}", hint="hull | box | mesh")
        surface = int(U.num(p, "surface", 0, "kit.col", 0, 178))
        day, night = face_light(src[0])
        cm = _slot(c, f"{name}_colmesh", "COL", "col")
        me = merged_mesh(src, src[0], cm.name)
        me.transform(src[0].matrix_world)
        if mode == "hull":
            new = _hull(me)
            bpy.data.meshes.remove(me)
            me = decimate_to(new, int(U.num(p, "max_faces", 256, "kit.col", 4, 20000)), cm.name)
        elif mode == "mesh":
            me = decimate_to(me, int(U.num(p, "max_faces", 1000, "kit.col", 4, 20000)), cm.name)
        else:
            lo = [min(v.co[k] for v in me.vertices) for k in range(3)]
            hi = [max(v.co[k] for v in me.vertices) for k in range(3)]
            bpy.data.meshes.remove(me)
            bm = bmesh.new()
            bmesh.ops.create_cube(bm, size=1.0)
            for v in bm.verts:
                v.co = [lo[k] + (v.co[k] + 0.5) * (hi[k] - lo[k]) for k in range(3)]
            bmesh.ops.triangulate(bm, faces=bm.faces[:])
            me = bpy.data.meshes.new(cm.name)
            bm.to_mesh(me)
            bm.free()
        while len(me.materials):
            me.materials.pop()
        cmat = bpy.data.materials.get(f"{name}.col") or bpy.data.materials.new(f"{name}.col")
        cmat.dff.col_mat_index = surface
        cmat.dff.col_day_light = day
        cmat.dff.col_night_light = night
        cmat["satk_role"] = "col"
        me.materials.append(cmat)
        _put(cm, me)
        out.update(mode=mode, faces=len(me.polygons), surface=surface, light=[day, night])
        out["changed"] = [cm.name]
    return out
