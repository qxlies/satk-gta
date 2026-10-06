# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``scene.*``: objects and collections - look, add empties, delete, rename, parent, transform, duplicate
(optionally mirrored), join, set the origin, custom properties. Objects are addressed by name; ``objects``
accepts globs (``wheel_*``). Rotations are in degrees, GTA axes (vehicles face +Y, Z up).
"""

from __future__ import annotations

import fnmatch
import math

import bpy
from mathutils import Matrix, Vector

from satk.core.errors import SatkError
from satk.studio.core import readonly

from ..stats import GEOMETRY_TYPES, mesh_counts
from . import _util as U


def _r2(v: float) -> float:
    return round(float(v) + 0.0, 2)


@readonly
def info(ctx, p: dict) -> dict:
    """List the objects of the scene: name, type, triangles, dimensions, parent (match = glob, limit)."""
    limit = int(p.get("limit", 50))
    objs = ctx.objects(str(p.get("match") or "*"))
    dg = bpy.context.evaluated_depsgraph_get()
    rows = []
    for o in objs[:max(0, limit)]:
        row: dict = {"name": o.name, "type": o.type}
        if o.type in GEOMETRY_TYPES:
            row["tris"] = mesh_counts(o, dg)[0]
        row["dims"] = [_r2(x) for x in o.dimensions]
        if o.parent is not None:
            row["parent"] = o.parent.name
        if o.get("satk_ghost"):
            row["ghost"] = True
        if o.get("satk_ref"):
            row["ref"] = True
        rows.append(row)
    out: dict = {"objects": rows, "total": len(objs)}
    if bpy.data.filepath:
        out["file"] = bpy.data.filepath.replace("\\", "/")
    return out


@readonly
def obj(ctx, p: dict) -> dict:
    """One object in detail: transform (deg), dimensions, parent, children, collections, modifiers, materials, props."""
    o = ctx.obj(U.text(p, "object", "scene.object", required=True))
    out: dict = {"name": o.name, "type": o.type, "location": U.r3(o.location),
                 "rotation": U.r3([math.degrees(a) for a in o.rotation_euler]), "scale": U.r3(o.scale),
                 "dims": [_r2(x) for x in o.dimensions], "collections": [c.name for c in o.users_collection]}
    if o.parent is not None:
        out["parent"] = o.parent.name
    if o.children:
        out["children"] = sorted(c.name for c in o.children)[:30]
    if o.modifiers:
        out["modifiers"] = [f"{m.name}:{m.type}" for m in o.modifiers]
    if getattr(o, "material_slots", None):
        out["materials"] = [s.material.name if s.material else "" for s in o.material_slots]
    if o.type in GEOMETRY_TYPES:
        out["tris"] = mesh_counts(o)[0]
    props = {k: o[k] for k in o.keys() if not k.startswith("_") and isinstance(o[k], (int, float, str))}
    if props:
        out["props"] = props
    if o.hide_render or o.hide_get():
        out["hidden"] = True
    return out


def clear(ctx, p: dict) -> dict:
    """Delete objects (all, or those whose name matches the glob 'match') and the data nothing uses any more."""
    objs = ctx.objects(str(p.get("match") or "*"))
    n = len(objs)
    if objs:
        bpy.data.batch_remove(objs)
    _purge()
    return {"removed": n}


def _purge() -> None:
    for coll in (bpy.data.meshes, bpy.data.curves, bpy.data.cameras, bpy.data.lights):
        dead = [x for x in coll if x.users == 0]
        if dead:
            bpy.data.batch_remove(dead)


def delete(ctx, p: dict) -> dict:
    """Delete the named objects (globs allowed); children keep their world position."""
    objs = U.resolve(ctx, p, "scene.delete")
    names = [o.name for o in objs]
    for o in objs:
        for c in o.children:
            mw = c.matrix_world.copy()
            c.parent = None
            c.matrix_world = mw
    bpy.data.batch_remove(objs)
    _purge()
    return {"removed": names}


def rename(ctx, p: dict) -> dict:
    """Rename an object (and its mesh data); NOT_READY if the new name is taken."""
    M = "scene.rename"
    o = ctx.obj(U.text(p, "object", M, required=True))
    new = U.text(p, "name", M, required=True)
    if len(new) > 63:
        raise U.bad(M, "names have at most 63 characters (Blender); GTA frame names at most 23")
    if new != o.name and bpy.data.objects.get(new) is not None:
        raise SatkError("EXISTS", f"{M}: an object named {new!r} exists")
    old = o.name
    o.name = new
    if o.data is not None and getattr(o.data, "users", 2) == 1:
        o.data.name = new
    if len(new) > 23:
        ctx.warn(f"STYLE: {new!r} is longer than 23 characters; a DFF frame name is cut there")
    return {"object": o.name, "was": old, "changed": [o.name]}


def parent(ctx, p: dict) -> dict:
    """Parent objects to 'parent' (null = unparent), keeping their world transform."""
    M = "scene.parent"
    objs = U.resolve(ctx, p, M)
    par = p.get("parent")
    target = ctx.obj(par) if par else None
    for o in objs:
        if target is not None and (o is target or target in o.children_recursive):
            raise U.bad(M, f"{target.name} cannot be the parent of {o.name} (a cycle)")
        mw = o.matrix_world.copy()
        o.parent = target
        if target is not None:
            o.matrix_parent_inverse = Matrix.Identity(4)
            o.matrix_world = mw
        else:
            o.matrix_world = mw
    return {"objects": [o.name for o in objs], "parent": target.name if target else None,
            "changed": [o.name for o in objs]}


def transform(ctx, p: dict) -> dict:
    """Set or offset location/rotation(deg)/scale of objects; apply=true bakes rotation+scale into the mesh."""
    M = "scene.transform"
    objs = U.resolve(ctx, p, M)
    loc, rot, scl = U.vec(p, "location", M), U.vec(p, "rotation", M), U.vec(p, "scale", M)
    delta = U.flag(p, "delta", M, False)
    for o in objs:
        if loc is not None:
            o.location = (o.location + Vector(loc)) if delta else Vector(loc)
        if rot is not None:
            r = [math.radians(a) for a in rot]
            o.rotation_euler = [a + b for a, b in zip(o.rotation_euler, r)] if delta else r
        if scl is not None:
            o.scale = [a * b for a, b in zip(o.scale, scl)] if delta else scl
    if U.flag(p, "apply", M, False):
        _apply(objs, location=U.flag(p, "apply_location", M, False))
    return {"objects": [o.name for o in objs], "changed": [o.name for o in objs]}


def _apply(objs, *, location: bool = False) -> None:
    meshes = [o for o in objs if o.type == "MESH"]
    if not meshes:
        return
    shared = [o.name for o in meshes if o.data.users > 1]
    if shared:
        raise SatkError("BAD_PARAMS", f"cannot apply a transform to shared mesh data: {', '.join(shared[:5])}",
                        hint="scene.duplicate with linked=false makes single-user copies")
    with bpy.context.temp_override(selected_editable_objects=meshes, selected_objects=meshes,
                                   active_object=meshes[0], object=meshes[0]):
        bpy.ops.object.transform_apply(location=location, rotation=True, scale=True)


def duplicate(ctx, p: dict) -> dict:
    """Copy an object (name, offset [x,y,z], mirror x|y|z flips it across the origin plane; linked shares the mesh)."""
    M = "scene.duplicate"
    o = ctx.obj(U.text(p, "object", M, required=True))
    linked = U.flag(p, "linked", M, False)
    mirror = U.text(p, "mirror", M, None, choices=("x", "y", "z"))
    if mirror and linked:
        raise U.bad(M, "a mirrored copy cannot share its mesh (linked=false)")
    new = o.copy()
    if o.data is not None and not linked:
        new.data = o.data.copy()
    new.name = U.text(p, "name", M, f"{o.name}_copy")
    for c in o.users_collection or [ctx.scene.collection]:
        c.objects.link(new)
    off = U.vec(p, "offset", M)
    if mirror:
        # world copy = F @ world; the mesh takes a local reflection S (normals flipped) so that the object
        # transform F @ MW @ S keeps a positive scale
        ax = "xyz".index(mirror)
        f = Matrix.Diagonal([(-1.0 if i == ax else 1.0) for i in range(3)] + [1.0])
        if new.type == "MESH":
            new.data.transform(f)
            new.data.flip_normals()
            new.matrix_world = f @ o.matrix_world @ f
        else:
            new.matrix_world = f @ o.matrix_world @ f
    if off is not None:
        new.location = new.location + Vector(off)
    return {"object": new.name, "changed": [new.name]}


def join(ctx, p: dict) -> dict:
    """Join mesh objects into the first one ('objects'; or 'into' + 'objects')."""
    M = "scene.join"
    objs = U.resolve(ctx, p, M, types=("MESH",))
    into = ctx.obj(p["into"]) if p.get("into") else objs[0]
    if into.type != "MESH":
        raise U.bad(M, f"{into.name} is not a mesh")
    rest = [o for o in objs if o is not into]
    if not rest:
        raise U.bad(M, "nothing to join: give at least two mesh objects")
    names = [o.name for o in rest]
    with bpy.context.temp_override(active_object=into, object=into, selected_objects=[into, *rest],
                                   selected_editable_objects=[into, *rest]):
        bpy.ops.object.join()
    return {"object": into.name, "joined": names, "changed": [into.name]}


def empty(ctx, p: dict) -> dict:
    """Add an empty (frame/dummy): name, location, rotation (deg), parent, display (plain_axes|cube|sphere), size."""
    M = "scene.empty"
    name = U.text(p, "name", M, required=True)
    ob = bpy.data.objects.new(name, None)
    ob.empty_display_type = {"plain_axes": "PLAIN_AXES", "cube": "CUBE", "sphere": "SPHERE", "arrows": "ARROWS"}[
        U.text(p, "display", M, "plain_axes", choices=("plain_axes", "cube", "sphere", "arrows"))]
    ob.empty_display_size = U.num(p, "size", M, 0.1, lo=0.001)
    U.link_new(ctx, ob, p.get("collection"))
    if p.get("parent"):
        ob.parent = ctx.obj(p["parent"])
    ob.location = Vector(U.vec(p, "location", M, [0.0, 0.0, 0.0]))
    ob.rotation_euler = [math.radians(a) for a in U.vec(p, "rotation", M, [0.0, 0.0, 0.0])]
    return {"object": ob.name, "changed": [ob.name]}


def collection(ctx, p: dict) -> dict:
    """Create a collection (under 'parent') and/or move objects into it (objects; link=true keeps old links)."""
    M = "scene.collection"
    name = U.text(p, "name", M, required=True)
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name)
        par = bpy.data.collections.get(p["parent"]) if p.get("parent") else ctx.scene.collection
        if par is None:
            raise SatkError("NOT_FOUND", f"{M}: no collection {p['parent']!r}")
        par.children.link(c)
    moved = []
    if p.get("objects") is not None:
        for o in U.resolve(ctx, {"objects": p["objects"]}, M):
            if not U.flag(p, "link", M, False):
                for old in list(o.users_collection):
                    if old is not c:
                        old.objects.unlink(o)
            if o.name not in c.objects:
                c.objects.link(o)
            moved.append(o.name)
    return {"collection": c.name, "objects": len(c.objects), "moved": moved, "changed": moved}


def origin(ctx, p: dict) -> dict:
    """Move an object's origin without moving its mesh: to bounds centre, bottom centre, or a point [x,y,z]."""
    M = "scene.origin"
    o = ctx.obj(U.text(p, "object", M, required=True))
    if o.type != "MESH":
        raise U.bad(M, f"{o.name} is not a mesh")
    to = p.get("to", "bounds")
    me = o.data
    if not me.vertices:
        raise U.bad(M, f"{o.name} has no vertices")
    if isinstance(to, list):
        target_w = Vector(U.vec({"v": to}, "v", M))
        target = o.matrix_world.inverted() @ target_w
    else:
        lo = Vector([min(v.co[i] for v in me.vertices) for i in range(3)])
        hi = Vector([max(v.co[i] for v in me.vertices) for i in range(3)])
        target = (lo + hi) / 2
        if to == "bottom":
            target.z = lo.z
        elif to != "bounds":
            raise U.bad(M, "'to' must be bounds, bottom or [x, y, z] (world)")
    if me.users > 1:
        raise U.bad(M, f"{o.name} shares its mesh; make it single-user first")
    me.transform(Matrix.Translation(-target))
    o.matrix_world = o.matrix_world @ Matrix.Translation(target)
    for c in o.children:
        c.matrix_parent_inverse = Matrix.Translation(-target) @ c.matrix_parent_inverse
    return {"object": o.name, "origin": U.r3(o.matrix_world.translation), "changed": [o.name]}


def props(ctx, p: dict) -> dict:
    """Set custom properties on objects (values: number or string; null removes the key)."""
    M = "scene.props"
    objs = U.resolve(ctx, p, M)
    vals = p.get("set")
    if not isinstance(vals, dict) or not vals:
        raise U.bad(M, "'set' must be an object of property: value")
    for o in objs:
        for k, v in vals.items():
            if not isinstance(k, str) or not k or k.startswith("_"):
                raise U.bad(M, f"bad property name {k!r}")
            if v is None:
                if k in o:
                    del o[k]
            elif isinstance(v, (int, float, str)) and not isinstance(v, bool):
                o[k] = v
            elif isinstance(v, bool):
                o[k] = int(v)
            else:
                raise U.bad(M, f"{k}: values are numbers or strings")
    return {"objects": [o.name for o in objs], "changed": [o.name for o in objs]}


def visible(ctx, p: dict) -> dict:
    """Show or hide objects in snapshots and the viewport (hide=true|false)."""
    M = "scene.visible"
    objs = U.resolve(ctx, p, M)
    hide = U.flag(p, "hide", M, False)
    for o in objs:
        o.hide_render = hide
        o.hide_set(hide)
    return {"objects": [o.name for o in objs], "hidden": hide}


@readonly
def measure(ctx, p: dict) -> dict:
    """Stats now without changing anything: tris, verts, dims, K1 shading metrics of objects (glob); file=true -> JSON."""
    from .. import stats as ST

    pat = U.text(p, "objects", "scene.stats", "*")
    names = [o.name for o in ST.geometry_objects() if fnmatch.fnmatchcase(o.name, pat)]
    out = ST.collect(names[:200])
    if U.flag(p, "precise", "scene.stats", False):
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        dg = bpy.context.evaluated_depsgraph_get()
        for o in ST.geometry_objects():
            for c in mesh_counts(o, dg)[2]:
                for i in range(3):
                    lo[i] = min(lo[i], c[i])
                    hi[i] = max(hi[i], c[i])
        if lo[0] != float("inf"):
            out["scene"]["bbox"] = [[round(x, 6) for x in lo], [round(x, 6) for x in hi]]
    if U.flag(p, "file", "scene.stats", False):  # every row, without the reply budget: into a JSON file
        import json
        import os

        path = os.path.join(ctx.out_dir, "stats", f"{ctx.n:04d}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)
        return {"file": os.path.abspath(path).replace("\\", "/"), "scene": out["scene"],
                "objects": len(out.get("objects") or {})}
    return out


METHODS = {"scene.info": info, "scene.object": obj, "scene.stats": measure, "scene.clear": clear, "scene.delete": delete,
           "scene.rename": rename, "scene.parent": parent, "scene.transform": transform,
           "scene.duplicate": duplicate, "scene.join": join, "scene.empty": empty, "scene.collection": collection,
           "scene.origin": origin, "scene.props": props, "scene.visible": visible}
