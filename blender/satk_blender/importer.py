# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Import a model or an area through DragonFF's importer functions (called directly: the map
operator is modal and does nothing in ``-b``, report 11 §3.3).

* one DragonFF import per model; TXD chain from the plan (own -> txdp parents -> vehicle), packed;
* area: prototypes in ``SATK_models`` (excluded from the view layer), an editable empty per placement
  with linked mesh children in ``SATK_area`` (LODs in ``SATK_lod``), world matrix from the position and the
  **conjugated** IPL quaternion (the plan already carries the world quaternion), custom properties
  ``satk_sid`` (``inst:``/``model:``), ``satk_model``, ``satk_lod``;
* vehicles: ``_dam``/``_vlo`` hidden (``hide_render``), ``wheel`` cloned onto every other
  ``wheel_*_dummy`` (left side turned 180 degrees about Z, as the game renders it), paint colours from
  ``carcols.dat``;
* collisions only when asked; COL materials deduplicated by surface.
"""

from __future__ import annotations

import math
import re
import time

import bpy
from mathutils import Matrix, Quaternion

from . import common, shading

MODELS = "SATK_models"
AREA = "SATK_area"
LOD = "SATK_lod"
COLS = "SATK_col"
_WHEEL_DUMMY = re.compile(r"^wheel_([lr])([fbm])_dummy$", re.IGNORECASE)


def _import_dff(path: str, images) -> object:
    di = common.dff_module("ops.dff_importer")
    return di.import_dff({
        "file_name": path, "txd_images": images, "image_ext": None, "connect_bones": False,
        "use_mat_split": False, "remove_doubles": False, "create_backfaces": True, "group_materials": False,
        "import_normals": True, "materials_naming": "DEF", "hide_damage_parts": False, "import_breakable": True,
    })


def _base_collection(imp):
    c = imp.current_collection
    # multi-clump files: current_collection is the last clump; its parent is the file collection
    for p in bpy.data.collections:
        if c.name in p.children and p.name.lower().endswith(".dff"):
            return p
    return c


def _objects(coll) -> list:
    return list(coll.all_objects)


def _kind(sec: str | None) -> str:
    return {"cars": "vehicle", "peds": "ped"}.get(sec or "", "building")


def _tag_scene(plan: dict) -> None:
    """The game profile of the import (an export completes old scenes from that profile's data)."""
    if plan.get("profile"):
        bpy.context.scene["satk_profile"] = str(plan["profile"])


def _tag_model_collection(coll, m: dict) -> None:
    """``satk_sid`` and the IDE definition data (``satk_ide``: draw distance, flags, tobj times) that an
    export writes back into its IDE fragment."""
    coll["satk_sid"] = m["sid"]
    if m.get("ide"):
        coll["satk_ide"] = {k: v for k, v in m["ide"].items() if v is not None}


# --------------------------------------------------------------------------- vehicles


def hide_damage(objs) -> int:
    n = 0
    for o in objs:
        if common.is_damage_part(o.name):
            o.hide_render = True
            o.hide_set(True)
            n += 1
    return n


def clone_wheels(objs, sid: str | None = None) -> int:
    """Put the ``wheel`` mesh on every wheel dummy that has none; returns the number of wheels."""
    by_name = {o.name.split(".")[0].lower(): o for o in objs}
    main = by_name.get("wheel")
    if main is None or main.type != "MESH":
        return 0
    main_dummy = main.parent
    main_side = None
    if main_dummy is not None:
        m = _WHEEL_DUMMY.match(main_dummy.name.split(".")[0])
        main_side = m.group(1).lower() if m else None
    n = 1
    coll = main.users_collection[0] if main.users_collection else bpy.context.scene.collection
    for o in sorted(objs, key=lambda x: x.name):
        m = _WHEEL_DUMMY.match(o.name.split(".")[0])
        if not m or o == main_dummy or o.type != "EMPTY":
            continue
        if any(ch.type == "MESH" for ch in o.children):
            n += 1
            continue
        w = bpy.data.objects.new(f"wheel_{m.group(1)}{m.group(2)}", main.data)
        coll.objects.link(w)
        w.parent = o
        w.matrix_parent_inverse = main.matrix_parent_inverse.copy()
        basis = main.matrix_basis.copy()
        if main_side is not None and m.group(1).lower() != main_side:
            basis = Matrix.Rotation(math.pi, 4, "Z") @ basis
        w.matrix_basis = basis
        try:
            w.dff.type = main.dff.type
        except AttributeError:
            pass
        common.tag(w, satk_sid=sid, satk_generated="wheel")
        n += 1
    return n


# --------------------------------------------------------------------------- collisions


#: COL surface materials shared by all collision objects of a job (DragonFF makes one per surface
#: of every object: 22 255 materials for 319 placements in report 11).
_COL_MATS: dict[tuple, object] = {}


def _dedupe_col_materials(objs) -> int:
    """One material per COL surface across the whole scene."""
    keep = _COL_MATS
    removed = 0
    for o in objs:
        if o.type != "MESH":
            continue
        mats = o.data.materials
        for i, m in enumerate(mats):
            if m is None:
                continue
            d = m.dff
            key = (m.name.split(".")[0], d.col_mat_index, d.col_flags, d.col_brightness, d.col_day_light, d.col_night_light)
            k = keep.get(key)
            if k is not None:
                try:
                    _ = k.name
                except ReferenceError:  # removed with an earlier scene
                    k = None
            if k is None:
                keep[key] = m
            elif k != m:
                mats[i] = k
    for m in list(bpy.data.materials):
        if m.users == 0:
            bpy.data.materials.remove(m)
            removed += 1
    return removed


def _import_col(path: str, prefix: str) -> list:
    ci = common.dff_module("ops.col_importer")
    colls = ci.import_col_file(path, prefix)
    objs = []
    for c in colls:
        objs.extend(c.all_objects)
    return colls, objs


def _hide_col(objs, viewport: bool) -> int:
    n = 0
    for o in objs:
        try:
            t = o.dff.type
        except AttributeError:
            continue
        if t in ("COL", "SHA"):
            o.hide_render = True
            if viewport:
                o.hide_set(True)
            n += 1
    return n


# --------------------------------------------------------------------------- import_model


def import_model(plan: dict, args: dict) -> dict:
    """One model from a plan (``satk.blender.resolve.plan_model``). Returns stats and object list."""
    t0 = time.perf_counter()
    if args.get("clean", True):
        common.clean_scene()
    m = plan["model"]
    _tag_scene(plan)
    images = common.load_txd_chain(m.get("txd") or [])
    brk = common.breakables_before()
    imp = _import_dff(m["dff"], images)
    coll = _base_collection(imp)
    _tag_model_collection(coll, m)
    objs = _objects(coll)
    for o in objs:
        common.tag(o, satk_sid=m["sid"], satk_model=m["sid"], satk_sec=m.get("sec"))
    n_brk = common.adopt_breakables(coll, brk, satk_sid=m["sid"], satk_model=m["sid"], satk_sec=m.get("sec"))
    stats: dict = {"model": m["sid"], "name": m["name"], "sec": m.get("sec")}
    if n_brk:
        stats["breakables"] = n_brk
    kind = _kind(m.get("sec"))
    col_objs = []
    if args.get("col") and m.get("col"):
        colls, col_objs = _import_col(m["col"], f"{m['name']}.col")
        for c in colls:
            common.move_collection(c, coll)
        _dedupe_col_materials(col_objs)
    stats["col_objects"] = _hide_col(objs + col_objs, viewport=not args.get("col"))
    if kind == "vehicle":
        stats["hidden_dam"] = hide_damage(objs) > 0
        stats["wheels"] = clone_wheels(objs, m["sid"])
        objs = _objects(coll)
    sh = shading.apply(objs, balance=args.get("balance", 0.0), kind=kind,
                       vehicle_colors=(m.get("vehicle") or {}).get("colors"))
    if m.get("vehicle"):
        stats["paint"] = m["vehicle"]["combo"]
    stats["paint_slots"] = sh.get("paint", 0)
    stats["objects"] = len(bpy.data.objects)
    stats["meshes"] = len([me for me in bpy.data.meshes if me.users])
    stats["materials"] = len(bpy.data.materials)
    # images: textures the model's materials use (= what a saved .blend keeps); txd_images: all
    # textures loaded from its TXD chain (infernus: 3 own + 19 vehicle.txd = 22, 13 of them used)
    stats["images"] = len(common.used_images(_objects(coll)))
    stats["txd_images"] = len(common.txd_image_names(m.get("txd") or []))
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    stats["armatures"] = len(arms)
    if arms:
        stats["bones"] = max(len(a.data.bones) for a in arms)
    missing = common.missing_textures(objs)
    stats["missing_tex"] = len(missing)
    stats["import_s"] = round(time.perf_counter() - t0, 2)
    warn = []
    if missing:
        warn.append("MISSING_TEX: " + ", ".join(missing[:12]))
    if imp.warning:
        warn.append(f"DRAGONFF: {imp.warning}")
    return {"stats": stats, "warnings": warn, "collection": coll.name, "kind": kind}


# --------------------------------------------------------------------------- import_area


def _inst_matrix(pos, q) -> Matrix:
    x, y, z, w = q
    return Matrix.Translation(pos) @ Quaternion((w, x, y, z)).to_matrix().to_4x4()


def import_area(plan: dict, args: dict) -> dict:
    """Placements from a plan (``satk.blender.resolve.plan_area``)."""
    t0 = time.perf_counter()
    if args.get("clean", True):
        common.clean_scene()
    _tag_scene(plan)
    models_c = common.ensure_collection(MODELS)
    area_c = common.ensure_collection(AREA)
    lod_c = common.ensure_collection(LOD)
    col_c = common.ensure_collection(COLS) if args.get("col") else None
    protos: dict[int, list] = {}
    col_protos: dict[int, list] = {}
    warn: list[str] = []
    missing: set[str] = set()
    t_models = time.perf_counter()
    txd_paths: list[str] = []
    n_brk = 0
    for m in plan["models"]:
        images = common.load_txd_chain(m.get("txd") or [])
        txd_paths += [p for p in m.get("txd") or [] if p not in txd_paths]
        brk = common.breakables_before()
        try:
            imp = _import_dff(m["dff"], images)
        except Exception as e:  # noqa: BLE001 - one broken DFF must not stop the area
            warn.append(f"DFF_FAILED: {m['sid']} {m['name']}: {type(e).__name__}: {str(e)[:120]}")
            continue
        coll = _base_collection(imp)
        _tag_model_collection(coll, m)
        common.move_collection(coll, models_c)
        objs = _objects(coll)
        for o in objs:
            common.tag(o, satk_sid=m["sid"], satk_model=m["sid"], satk_sec=m.get("sec"), satk_proto=1)
        # breakable meshes go with their prototype into the hidden SATK_models (not to the world origin)
        n_brk += common.adopt_breakables(coll, brk, satk_sid=m["sid"], satk_model=m["sid"], satk_sec=m.get("sec"),
                                         satk_proto=1)
        _hide_col(objs, viewport=True)
        missing.update(common.missing_textures(objs))
        shading.apply(objs, balance=args.get("balance", 0.0), kind=_kind(m.get("sec")),
                      vehicle_colors=(m.get("vehicle") or {}).get("colors"))
        protos[m["id"]] = [(o, common.world_matrix(o)) for o in objs
                           if o.type == "MESH" and getattr(o.dff, "type", "OBJ") == "OBJ" and not common.is_damage_part(o.name)]
        if col_c is not None and m.get("col"):
            colls, cobjs = _import_col(m["col"], f"{m['name']}.col")
            for c in colls:
                common.move_collection(c, models_c)
            _dedupe_col_materials(cobjs)
            col_protos[m["id"]] = [(o, common.world_matrix(o)) for o in cobjs if o.type == "MESH"]
    t_models = time.perf_counter() - t_models
    lc = common.layer_collection(models_c)
    if lc is not None:
        lc.exclude = True
    n_hd = n_lod = 0
    names_by_model = {m["id"]: m["name"] for m in plan["models"]}
    for inst in plan["insts"]:
        parts = protos.get(inst["model"])
        if not parts:
            continue
        M = _inst_matrix(inst["pos"], inst["q"])
        target = lod_c if inst.get("is_lod") else area_c
        base = f"{names_by_model.get(inst['model'], inst['model'])}@{inst['sid'][5:]}"
        # Move/rotate this root to edit the placement as a whole. Mesh and COL children retain
        # their prototype-local transforms; provenance tags are never the exported transform.
        root = bpy.data.objects.new(f"placement@{inst['sid'][5:]}", None)
        root.empty_display_type = "PLAIN_AXES"
        root.empty_display_size = 1.0
        target.objects.link(root)
        root.matrix_world = M
        props = dict(satk_sid=inst["sid"], satk_model=f"model:{inst['model']}",
                     satk_lod=int(bool(inst.get("is_lod"))), satk_area=inst.get("area"),
                     satk_iflags=inst.get("iflags"), satk_pos=list(inst["pos"]), satk_q=list(inst["q"]))
        common.tag(root, **props, satk_instance_root=1)
        for k, (proto, pw) in enumerate(parts):
            o = bpy.data.objects.new(base if k == 0 else f"{base}.{k}", proto.data)
            target.objects.link(o)
            o.parent = root
            o.matrix_basis = pw
            common.tag(o, **props, satk_proto_matrix=[v for row in o.matrix_basis for v in row])
            if inst.get("lod"):
                o["satk_lod_of"] = inst["lod"]
            try:
                o.dff.type = "OBJ"
            except AttributeError:
                pass
        for proto, pw in col_protos.get(inst["model"], ()):
            o = bpy.data.objects.new(f"col@{inst['sid'][5:]}", proto.data)
            col_c.objects.link(o)
            o.parent = root
            o.matrix_basis = pw
            o.hide_render = True
            common.tag(o, satk_sid=inst["sid"], satk_model=f"model:{inst['model']}")
            try:
                o.dff.type = "COL"
            except AttributeError:
                pass
        if inst.get("is_lod"):
            n_lod += 1
        else:
            n_hd += 1
    # LODs overlap their HD objects: hide them unless only LODs were asked for
    if n_hd and n_lod and args.get("lod") != "lod":
        lod_c.hide_render = True
        lc = common.layer_collection(lod_c)
        if lc is not None:
            lc.hide_viewport = True
    if col_c is not None:
        lc = common.layer_collection(col_c)
        if lc is not None:
            lc.hide_viewport = True
    render_meshes = {o.data.name for c in (area_c, lod_c) for o in c.objects if o.type == "MESH"}
    stats = {
        "instances": n_hd + n_lod, "hd": n_hd, "lod": n_lod,
        "models": len(protos), "meshes": len(render_meshes),
        # images: textures used by the imported materials (= what the saved .blend keeps);
        # txd_images: everything loaded from the TXDs of these models (most of a shared TXD is unused)
        "images": len(common.used_images(models_c.all_objects)),
        "txd_images": len(common.txd_image_names(txd_paths)),
        "materials": len(bpy.data.materials),
        "objects": len(bpy.data.objects), "missing_tex": len(missing),
        "models_s": round(t_models, 2), "import_s": round(time.perf_counter() - t0, 2),
    }
    if n_brk:
        stats["breakables"] = n_brk
    if col_c is not None:
        stats["col_instances"] = len(col_c.objects)
    if missing:
        warn.append("MISSING_TEX: " + ", ".join(sorted(missing)[:12]) + (" ..." if len(missing) > 12 else ""))
    return {"stats": stats, "warnings": warn}
