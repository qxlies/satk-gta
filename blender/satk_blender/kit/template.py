# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.template``: the scaffold of a new asset from a template plan (``satk.kit.plan``).

Builds collection ``<name>.dff`` with one object per vanilla frame in the vanilla order (EMPTY dummies,
empty MESH slots with the part's preset materials and UV layers), the COL skeleton (sphere/box empties,
empty COL/shadow meshes) in ``<name>_col``, the LOD slot of a building in ``lod<name[3:]>.dff`` and an
optional read-only ghost of the vanilla model (``satk_ghost``, never exported). No vanilla vertex reaches
the scaffold: slots start empty.
"""

from __future__ import annotations

import os

import bpy

from satk.core.errors import SatkError

from . import materials as M
from . import util as U

__all__ = ["build", "remove_model", "make_lod_slot"]


def remove_model(name: str) -> int:
    """Delete a kit model's collections and objects (the ghost too). Returns the number of objects."""
    n = 0
    lname = name.lower()
    for c in list(bpy.data.collections):
        if str(c.get("satk_name", "")).lower() == lname or str(c.get("satk_lod_of", "")).lower() == lname \
                or str(c.get("satk_ghost_of", "")).lower() == lname or str(c.get("satk_col_of", "")).lower() == lname:
            for o in list(c.objects):
                data = o.data
                bpy.data.objects.remove(o, do_unlink=True)
                n += 1
                if data is not None and getattr(data, "users", 1) == 0:
                    if isinstance(data, bpy.types.Mesh):
                        bpy.data.meshes.remove(data)
            bpy.data.collections.remove(c)
    return n


def _new_collection(name: str, parent=None):
    c = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(c)
    return c


def _uv_layers(me, n: int) -> None:
    for i in range(max(1, n)):
        nm = "UVMap" if i == 0 else f"UVMap{i + 1}"
        if me.uv_layers.get(nm) is None:
            me.uv_layers.new(name=nm)


def _frame_object(fr: dict, plan: dict, mats: dict, coll):
    name = fr["name"] or plan["name"]
    group = plan["group"]
    if fr.get("part"):
        me = bpy.data.meshes.new(name)
        _uv_layers(me, int(fr.get("uv_sets") or 1))
        for role in fr.get("roles") or []:
            if role in mats:
                me.materials.append(mats[role])
        o = bpy.data.objects.new(name, me)
        o.dff.is_frame = True
        o.dff.atomic_index = int(fr.get("atomic_index", 0))
        o.dff.export_split_normals = True
        o.dff.uv_map1 = True
        o.dff.uv_map2 = int(fr.get("uv_sets") or 1) > 1
        lit = group != "world"
        o.dff.export_normals = lit
        o.dff.light = True
        o["satk_part"] = fr["part"]
        o["satk_slot"] = fr.get("slot", "hd")
        if fr.get("vanilla_tris"):
            o["satk_vanilla_tris"] = int(fr["vanilla_tris"])
        if fr.get("slot") in ("dam", "vlo") or fr.get("part") == "gunflash":
            o.hide_set(False)
    else:
        o = bpy.data.objects.new(name, None)
        o.empty_display_type = "PLAIN_AXES"
        o.empty_display_size = 0.08 if group == "vehicle" else 0.04
        if name.startswith("wheel_") and name.endswith("_dummy") and plan.get("anchors", {}).get("wheel_scale"):
            o.empty_display_type = "CIRCLE"
            o.empty_display_size = float(plan["anchors"]["wheel_scale"]) / 2
            o.rotation_mode = "XYZ"
    o.dff.type = "OBJ"
    o["satk_frame"] = name
    o.dff.frame_index = int(fr["i"])
    o.dff.export_frame_name = True
    o["satk_role"] = fr["role"]
    if fr.get("bone_id") is not None:
        o["satk_bone_id"] = int(fr["bone_id"])
    coll.objects.link(o)
    return o


def _col_skeleton(plan: dict, parent_coll) -> tuple[object | None, int]:
    col = plan.get("col") or {}
    sph = col.get("spheres") or []
    boxes = col.get("boxes") or []
    want_mesh = int(col.get("mesh_faces") or 0) > 0
    want_shadow = int(col.get("shadow_faces") or 0) > 0
    if not (sph or boxes or want_mesh or want_shadow):
        return None, 0
    name = plan["name"]
    c = _new_collection(f"{name}_col", parent_coll)
    c["satk_col_of"] = name
    c["satk_col_name"] = col.get("name") or name
    c["satk_col_embedded"] = bool(col.get("embedded"))
    made = 0

    def surface(o, s):
        o.dff.col_material = int(s[0])
        o.dff.col_flags = int(s[1])
        o.dff.col_brightness = int(s[2])
        o.dff.col_day_light = int(s[3]) & 15
        o.dff.col_night_light = (int(s[3]) >> 4) & 15

    for i, s in enumerate(sph):
        o = bpy.data.objects.new(f"{name}_cs{i:02d}", None)
        o.empty_display_type = "SPHERE"
        o.empty_display_size = float(s["r"])
        o.location = s["c"]
        o.dff.type = "COL"
        surface(o, s.get("surface") or (0, 0, 0, 0))
        o["satk_role"] = "col"
        c.objects.link(o)
        made += 1
    for i, b in enumerate(boxes):
        lo, hi = b["min"], b["max"]
        o = bpy.data.objects.new(f"{name}_cb{i:02d}", None)
        o.empty_display_type = "CUBE"
        o.location = [(lo[k] + hi[k]) / 2 for k in range(3)]
        o.scale = [max((hi[k] - lo[k]) / 2, 0.01) for k in range(3)]
        o.dff.type = "COL"
        surface(o, b.get("surface") or (0, 0, 0, 0))
        o["satk_role"] = "col"
        c.objects.link(o)
        made += 1
    for want, nm, typ, role in ((want_mesh, f"{name}_colmesh", "COL", "col"),
                                (want_shadow, f"{name}_shadow", "SHA", "shadow")):
        if not want:
            continue
        me = bpy.data.meshes.new(nm)
        o = bpy.data.objects.new(nm, me)
        o.dff.type = typ
        o["satk_role"] = role
        c.objects.link(o)
        made += 1
    for o in c.objects:
        o.hide_render = True
        if o.type == "MESH":
            o.display_type = "WIRE"
    return c, made


def make_lod_slot(model: str, lod_name: str, kind: str, mats) -> object:
    """The LOD slot of a map model: collection ``<lod_name>.dff`` (``satk_lod_of`` = ``model``) with one empty mesh
    object that carries ``mats`` (the HD materials). Returns the object."""
    lc = _new_collection(f"{lod_name}.dff")
    lc["satk_kit"] = kind
    lc["satk_lod_of"] = model
    lc["satk_name"] = lod_name
    me = bpy.data.meshes.new(lod_name)
    _uv_layers(me, 1)
    for m in mats:
        me.materials.append(m)
    slot = bpy.data.objects.new(lod_name, me)
    slot.dff.type = "OBJ"
    slot.dff.export_normals = False
    slot["satk_role"] = "lod"
    slot["satk_slot"] = "lod"
    lc.objects.link(slot)
    return slot


def _ghost(plan: dict, warn: list[str]) -> int:
    g = plan.get("ghost") or {}
    dff = g.get("dff")
    if not dff or not os.path.isfile(dff):
        warn.append("NOT_FOUND: no ghost DFF in the plan (kit.template --ghost)")
        return 0
    from satk_blender import common

    di = common.dff_module("ops.dff_importer")
    before = set(bpy.data.collections)
    imp = di.import_dff({
        "file_name": dff, "txd_images": {}, "image_ext": None, "connect_bones": False, "use_mat_split": False,
        "remove_doubles": False, "create_backfaces": False, "group_materials": False, "import_normals": True,
        "materials_naming": "DEF", "hide_damage_parts": True, "import_breakable": False,
    })
    new = [c for c in bpy.data.collections if c not in before]
    top = getattr(imp, "current_collection", None) or (new[0] if new else None)
    n = 0
    for c in new:
        for o in c.all_objects:
            o["satk_ghost"] = True
            o.hide_render = True
            if o.type == "MESH":
                o.display_type = "WIRE"
            n += 1
    if top is not None:
        top.name = f"{plan['like']['name']}_ghost"
        top["satk_ghost_of"] = plan["name"]
    return n


def build(plan: dict, *, plan_path: str | None = None, replace: bool = False, textures: dict | None = None) -> dict:
    """Build the scaffold of ``plan``. Returns counts and the collection names."""
    name = plan["name"]
    warn: list[str] = []
    existing = [c for c in bpy.data.collections if str(c.get("satk_name", "")).lower() == name.lower()]
    if existing and not any(len(c.objects) for c in existing):
        remove_model(name)  # left empty by scene.clear
        existing = []
    if existing:
        if not replace:
            raise SatkError("EXISTS", f"kit model {name!r} is already in the scene",
                            hint="pass replace=true to rebuild it, or another name")
        remove_model(name)
    coll = _new_collection(f"{name}.dff")
    coll["satk_kit"] = plan["kind"]
    coll["satk_name"] = name
    coll["satk_tier"] = plan["tier"]
    coll["satk_group"] = plan["group"]
    coll["satk_like"] = plan["like"]["sid"]
    if plan_path:
        coll["satk_plan"] = U.fwd(plan_path)
    tex = dict(textures or {})
    tex.update({k.lower(): v for k, v in (plan.get("textures") or {}).items()})
    mats = {role: M.ensure(name, dict(p, role=role), tex) for role, p in (plan.get("presets") or {}).items()}
    objs: dict[int, object] = {}
    for fr in plan["frames"]:
        objs[fr["i"]] = _frame_object(fr, plan, mats, coll)
    for fr in plan["frames"]:
        o = objs[fr["i"]]
        if fr["parent"] >= 0:
            o.parent = objs[fr["parent"]]
        o.matrix_parent_inverse.identity()
        o.matrix_basis = U.rw_matrix(fr["matrix"])
    root = objs.get(plan["frames"][0]["i"]) if plan["frames"] else None
    if root is not None:
        root["satk_sec"] = (plan.get("ide") or {}).get("sec") or ""
        root["satk_kind"] = plan["kind"]
    ccol, ncol = _col_skeleton(plan, coll)
    lod_slot = None
    lod = plan.get("lod") or {}
    if lod.get("name"):  # buildings always, props when the template asked for it (--lod)
        lod_slot = make_lod_slot(name, lod["name"], plan["kind"], list(mats.values()))
    nghost = _ghost(plan, warn) if plan.get("ghost") else 0
    bpy.context.view_layer.update()
    return {
        "model": name, "kind": plan["kind"], "tier": plan["tier"], "collection": coll.name,
        "frames": len(objs), "slots": sum(1 for o in objs.values() if o.type == "MESH"),
        "dummies": sum(1 for o in objs.values() if o.type == "EMPTY"), "materials": len(mats),
        "col": ncol, "lod": lod_slot.name if lod_slot is not None else None, "ghost": nghost,
        "warn": warn, "root": root.name if root is not None else None,
    }
