# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.export`` (Blender side): DFF (+ embedded or file COL) and the own-texture PNGs of a kit model.

The export works on temporary copies, so the agent's scene is never changed: frame objects are copied
with their parenting (an empty slot becomes a frame-only dummy), the COL skeleton goes into a collection
named like the COL model (``<name>_col`` embedded for vehicles, ``<name>`` for a world ``.col`` file),
DragonFF writes the DFF from the evaluated meshes (modifiers applied, split normals), and the bounding
spheres are recomputed in frame-local space. Own textures (not ``vehicle.txd`` atlases) are saved as PNGs
for the TXD the satk side packs. The scene state is restored afterwards, also on a DragonFF error.

Map models (group ``world``) also get their **prelight**: day and night vertex colours baked on the exported copies
(ray-cast ambient occlusion x sun and sky, a warm night; ``satk_blender.gameready.prelight``), unless the mesh already
carries colour attributes (painted by hand: kept) or ``prelight="none"``; pickups get no night set. The manifest
carries the ``shape`` of the model (size, closed volume) so the satk side can pick a primitive collision, and the
LOD model is exported from its own prelit copy.
"""

from __future__ import annotations

import os

import bpy

from satk.core.errors import SatkError

from . import util as U

__all__ = ["export_model", "save_textures", "model_shape"]

_SHARED = None


def _shared() -> frozenset:
    global _SHARED
    if _SHARED is None:
        try:
            from satk.kit.kinds import SHARED_TEXTURES
        except Exception:  # noqa: BLE001
            SHARED_TEXTURES = frozenset()
        _SHARED = SHARED_TEXTURES
    return _SHARED


def _texname(mat) -> tuple[str | None, object]:
    if mat is None or not mat.node_tree:
        return None, None
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None or not bsdf.inputs["Base Color"].is_linked:
        return None, None
    node = bsdf.inputs["Base Color"].links[0].from_node
    if node.type != "TEX_IMAGE" or node.image is None:
        return None, None
    img = node.image
    name = node.label if node.label and node.label in img.name else img.name
    parts = name.split("/")
    if len(parts) >= 3 and parts[-1].isdigit():  # satk/DragonFF TXD import: "<txd>/<texture>/<mip>"
        name = parts[-2]
        if parts[-3].lower() in ("vehicle", "vehicle.txd"):
            img["satk_shared"] = True
    else:
        name = parts[-1]
    return (name.rsplit(".", 1)[0] if name.lower().endswith((".png", ".tga", ".bmp")) else name), img


def save_textures(objs, out_dir: str, warn: list[str], skip: set[str] | frozenset = frozenset()) -> list[dict]:
    """PNG of every own (not shared) texture of ``objs`` into ``out_dir/tex``; ``skip`` = texture names already saved."""
    import numpy as np

    rows: list[dict] = []
    done: set[str] = {n.lower() for n in skip}
    tex_dir = os.path.join(out_dir, "tex")
    for o in objs:
        if o.type != "MESH":
            continue
        for slot in o.material_slots:
            name, img = _texname(slot.material)
            if not name or name.lower() in done:
                continue
            done.add(name.lower())
            if name.lower() in _shared() or (img is not None and img.get("satk_shared")):
                continue
            w, h = img.size
            if w < 1 or h < 1:
                warn.append(f"TEX_EMPTY: texture {name} has no pixels; not exported")
                continue
            os.makedirs(tex_dir, exist_ok=True)
            png = os.path.join(tex_dir, f"{name.lower()}.png")
            px = np.empty(w * h * 4, dtype=np.float32)
            img.pixels.foreach_get(px)
            alpha = bool((px[3::4] < 0.999).any())
            tmp = bpy.data.images.new("satk_kit_tex_out", w, h, alpha=True)
            try:
                tmp.colorspace_settings.name = img.colorspace_settings.name
                tmp.pixels.foreach_set(px)
                tmp.filepath_raw = png
                tmp.file_format = "PNG"
                tmp.save()
            finally:
                bpy.data.images.remove(tmp)
            rows.append({"name": name.lower(), "png": U.fwd(png), "w": w, "h": h, "alpha": alpha})
    return rows


class _MatsOf:
    """An object seen with the given materials in its slots (``save_textures`` reads ``type`` and ``material_slots``)."""

    type = "MESH"

    def __init__(self, obj, mats):
        self.material_slots = [type("Slot", (), {"material": m})() for m in mats] or list(obj.material_slots)


def name_of(coll) -> str:
    return str(coll.get("satk_name") or coll.name[:-4])


def _lod_slot(name: str):
    """``(object, [])`` of the LOD slot of model ``name`` with a filled mesh, else ``(None, [])``."""
    lc = next((c for c in bpy.data.collections if str(c.get("satk_lod_of", "")).lower() == name.lower()), None)
    lo = next((o for o in lc.objects if o.type == "MESH" and len(o.data.polygons)), None) if lc is not None else None
    return lo, []


def _night_wanted(kind: str) -> bool:
    """Night colours unless the kind's game-ready class has none (pickups)."""
    try:
        from satk.kit import kinds as K

        return K.game_ready_class(K.get(kind)["game_ready_class"]).get("night") != "none"
    except Exception:  # noqa: BLE001 - a kind without a class keeps the default
        return True


def ao_loops(obj, rays: int = 32) -> "object":
    """Ambient occlusion per loop of ``obj`` (0 covered .. 1 open) by ray casting against the model and a ground plane
    under it: ``rays`` cosine-weighted directions per corner (a fixed set, so the result is the same every time),
    rays as long as half the model's diagonal. Corners that share a vertex and a normal share one answer. Cycles is not
    used: a bake inside a long-lived session left the view layer in a state that sometimes hung the next update."""
    import math

    import numpy as np
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    me = obj.data
    nv, nl = len(me.vertices), len(me.loops)
    co = np.empty(nv * 3)
    me.vertices.foreach_get("co", co)
    m = np.array(obj.matrix_world)
    wco = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
    me.calc_loop_triangles()
    tri = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", tri)
    lo, hi = wco.min(0), wco.max(0)
    diag = max(float(np.linalg.norm(hi - lo)), 1e-3)
    big, z = diag * 4.0, float(lo[2]) - diag * 0.002
    cx, cy = float((lo[0] + hi[0]) / 2), float((lo[1] + hi[1]) / 2)
    verts = [Vector(v) for v in wco.tolist()] + [Vector((cx + sx * big, cy + sy * big, z))
                                                  for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    polys = [tuple(t) for t in tri.reshape(-1, 3).tolist()] + [(nv, nv + 1, nv + 2), (nv, nv + 2, nv + 3)]
    bvh = BVHTree.FromPolygons(verts, polys)
    nrm = np.empty(nl * 3)
    me.corner_normals.foreach_get("vector", nrm)
    nrm = nrm.reshape(-1, 3) @ m[:3, :3].T
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    vi = np.empty(nl, dtype=np.int64)
    me.loops.foreach_get("vertex_index", vi)
    n_rays = rays if nl <= 20000 else max(8, rays // 2)
    gold = math.pi * (3.0 - math.sqrt(5.0))
    local = [(math.sqrt((k + 0.5) / n_rays) * math.cos(k * gold), math.sqrt((k + 0.5) / n_rays) * math.sin(k * gold),
              math.sqrt(1.0 - (k + 0.5) / n_rays)) for k in range(n_rays)]
    reach, eps = diag * 0.5, diag * 1e-4
    out = np.ones(nl, dtype=np.float32)
    seen: dict = {}
    for i in range(nl):
        key = (int(vi[i]), round(float(nrm[i, 0]), 2), round(float(nrm[i, 1]), 2), round(float(nrm[i, 2]), 2))
        hit = seen.get(key)
        if hit is None:
            n = Vector(nrm[i].tolist())
            helper = Vector((0.0, 0.0, 1.0)) if abs(n.z) < 0.9 else Vector((1.0, 0.0, 0.0))
            t = n.cross(helper)
            t.normalize()
            b = n.cross(t)
            org = Vector(wco[vi[i]].tolist()) + n * eps
            blocked = 0
            for lx, ly, lz in local:
                if bvh.ray_cast(org, t * lx + b * ly + n * lz, reach)[0] is not None:
                    blocked += 1
            hit = 1.0 - blocked / n_rays
            seen[key] = hit
        out[i] = hit
    return out


def _prelight_objects(objs, mode: str, night: bool, meshes: list, warn: list[str]) -> dict:
    """Day/night colours on temporary copies (``objs`` own their mesh data after this); ``mode`` auto|bake|simple|none."""
    from satk_blender import gameready as GR

    rows = []
    kept = 0
    for c in objs:
        c.data = c.data.copy()
        meshes.append(c.data)
        if mode == "none":
            for a in list(c.data.color_attributes):
                c.data.color_attributes.remove(a)
            c.dff.day_cols = c.dff.night_cols = False
            continue
        if mode == "auto" and len(c.data.color_attributes):   # painted by hand: keep what is there
            c.dff.day_cols = True
            c.dff.night_cols = night and len(c.data.color_attributes) > 1
            kept += 1
            continue
        try:
            rows.append(GR.prelight(c, "simple", None) if mode == "simple" else GR.prelight(c, "bake", ao_loops(c)))
        except Exception as e:  # noqa: BLE001 - a failed bake must not lose the export
            warn.append(f"PRELIGHT_FAILED: {c.name}: {type(e).__name__}: {e}; exported without prelight")
            continue
        if not night:
            c.dff.night_cols = False
    out: dict = {"mode": mode, "objects": len(objs), "kept": kept, "night": night}
    if rows:
        out["day_mean"] = round(sum(r.get("day_mean", 0.0) for r in rows) / len(rows), 3)
        out["night_mean"] = round(sum(r.get("night_mean", 0.0) for r in rows) / len(rows), 3)
    return out


def model_shape(objs) -> dict:
    """Size and closed volume of the world-space union of ``objs`` (modifiers applied): ``{"bbox", "size", "volume"?}``.

    ``volume`` is the signed volume of the triangles and is given only when every mesh is closed (every edge
    belongs to two faces); the satk side uses it to tell a solid block from a shell or a frame."""
    import numpy as np

    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    vol = 0.0
    closed = True
    tris = 0
    for c in objs:
        me, free = U.evaluated_mesh(c)
        try:
            n = len(me.vertices)
            if not n or not len(me.polygons):
                continue
            co = np.empty(n * 3)
            me.vertices.foreach_get("co", co)
            co = co.reshape(n, 3)
            m = np.array(c.matrix_world)
            co = co @ m[:3, :3].T + m[:3, 3]
            lo = np.minimum(lo, co.min(0))
            hi = np.maximum(hi, co.max(0))
            me.calc_loop_triangles()
            t = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
            me.loop_triangles.foreach_get("vertices", t)
            t = t.reshape(-1, 3)
            tris += len(t)
            a, b, d = co[t[:, 0]], co[t[:, 1]], co[t[:, 2]]
            vol += float(np.einsum("ij,ij->i", a, np.cross(b, d)).sum() / 6.0)
            le = np.empty(len(me.loops), dtype=np.int64)
            me.loops.foreach_get("edge_index", le)
            cnt = np.bincount(le, minlength=len(me.edges))
            closed = closed and bool((cnt == 2).all())
        finally:
            free()
    if not np.isfinite(lo).all():
        return {}
    out = {"bbox": [[round(float(x), 3) for x in lo], [round(float(x), 3) for x in hi]],
           "size": round(float((hi - lo).max()), 3), "tris": tris, "closed": closed}
    if closed:
        out["volume"] = round(abs(vol), 4)
    return out


def _copy(o, coll, as_empty: bool, i: int):
    # DragonFF writes the object name up to its last '.': "<frame>.kx<i>" exports as "<frame>"
    nm = f"{o.get('satk_frame') or o.name}.kx{i}"
    if as_empty:
        c = bpy.data.objects.new(nm, None)
        c.empty_display_type = "PLAIN_AXES"
        for k in ("type", "frame_index", "export_frame_name"):
            setattr(c.dff, k, getattr(o.dff, k))
    else:
        c = o.copy()
        c.name = nm
    c["satk_generated"] = True
    coll.objects.link(c)
    return c


def export_model(coll, out_dir: str, *, stem: str | None = None, col: str = "auto", textures: bool = True,
                 prelight: str = "auto") -> dict:
    """Export the kit clump ``coll`` into ``out_dir``: ``<stem>.dff`` (+ ``<stem>.col``) and texture PNGs.

    ``prelight`` (map models): auto (bake unless the mesh has colour attributes), bake, simple or none."""
    from satk_blender import common, exporter

    de = common.dff_module("ops.dff_exporter")
    name = str(coll.get("satk_name") or coll.name[:-4])
    stem = (stem or name).lower()
    group = str(coll.get("satk_group") or "vehicle")
    warn: list[str] = []
    frames = sorted((o for o in coll.objects if o.get("satk_role") in ("root", "dummy", "part", "bone")),
                    key=lambda o: o.dff.frame_index)
    if not frames:
        raise SatkError("NOT_FOUND", f"kit model {name!r} has no frame objects", hint="kit.template first")
    os.makedirs(out_dir, exist_ok=True)
    from satk_blender import shading

    state = exporter.SceneState()
    mats = {s.material for o in frames if o.type == "MESH" for s in o.material_slots if s.material}
    # The LOD slot shares the HD materials. The export rewrites their node trees (DragonFF's wrapper, the game-shading
    # relink and its undo); a scene object that still uses a material while that happens left the depsgraph in a state
    # whose next update spun forever (reproducible: a second kit.export, or the checkpoint save after the first). So the
    # slot lets go of its materials for the length of the export and gets them back after the scene is restored.
    lod_slot, lod_mats = _lod_slot(name_of(coll))
    if lod_slot is not None:
        lod_mats = [sl.material for sl in lod_slot.material_slots]
        if all(m is not None for m in lod_mats):
            lod_slot.data.materials.clear()
        else:
            lod_slot, lod_mats = None, []
    state.keep_materials(mats)
    # imported models carry the satk game look: DragonFF reads the plain Principled links
    shading.set_game_shading(False, mats)
    shading.restore_for_export(mats)
    tmp = bpy.data.collections.new("satk_kit_export")
    bpy.context.scene.collection.children.link(tmp)
    ctmp = None
    made: list = []
    meshes: list = []     # temporary mesh copies made for the prelight
    dropped: list[str] = []
    shape: dict = {}
    pre: dict = {}
    try:
        exporter._unexclude_all()
        bpy.context.view_layer.update()
        mapping: dict = {}
        for i, o in enumerate(frames):
            empty = o.type == "MESH" and len(o.data.polygons) == 0
            if empty and o.get("satk_role") in ("part", "root"):
                dropped.append(o.name)
            c = _copy(o, tmp, empty or o.type != "MESH", i)
            mapping[o] = c
            made.append(c)
        for o, c in mapping.items():
            if o.parent is not None and o.parent in mapping:
                c.parent = mapping[o.parent]
                c.matrix_parent_inverse = o.matrix_parent_inverse.copy()
            c.matrix_basis = o.matrix_basis.copy()
        n_atomics = sum(1 for c in made if c.type == "MESH")
        if n_atomics == 0:
            raise SatkError("BAD_PARAMS", f"kit.export: every slot of {name!r} is empty",
                            hint="model at least one part (chassis, the main mesh) before exporting")
        if group == "world":
            atoms = [c for c in made if c.type == "MESH" and len(c.data.polygons)]
            if prelight != "none" and atoms:
                pre = _prelight_objects(atoms, prelight, _night_wanted(str(coll.get("satk_kit"))), meshes, warn)
            elif atoms:
                pre = _prelight_objects(atoms, "none", True, meshes, warn)
            bpy.context.view_layer.update()
            shape = model_shape(atoms)
        # collision skeleton -> a collection named like the COL model
        col_src = next((c for c in bpy.data.collections if str(c.get("satk_col_of", "")).lower() == name.lower()), None)
        col_objs = []
        if col_src is not None and col != "none":
            for o in col_src.objects:
                if o.type == "MESH" and not len(o.data.polygons):
                    continue
                col_objs.append(o)
        embedded = group == "vehicle" and str(coll.get("satk_kit")) != "vehicle_upgrade"
        files: dict = {}
        if col_objs:
            cname = f"{stem}_col" if embedded else stem
            other = bpy.data.collections.get(cname)
            if other is not None:
                other.name = cname + "_satk_tmp_renamed"
            ctmp = bpy.data.collections.new(cname)
            bpy.context.scene.collection.children.link(ctmp)
            for o in col_objs:
                c = o.copy()
                c["satk_generated"] = True
                ctmp.objects.link(c)
                made.append(c)
        bpy.context.view_layer.update()
        exp = [c for c in made if c.users_collection and c.users_collection[0] is tmp]
        col_copies = [c for c in made if ctmp is not None and c.users_collection and c.users_collection[0] is ctmp]
        for x in bpy.context.view_layer.objects:
            x.select_set(False)
        for c in exp + (col_copies if embedded else []):
            c.hide_viewport = False
            c.hide_set(False)
            c.select_set(True)
        dff_path = os.path.join(out_dir, f"{stem}.dff")
        de.dff_exporter.collection = None
        de.export_dff({
            "file_name": dff_path, "directory": out_dir, "selected": True, "mass_export": False,
            "preserve_positions": True, "preserve_rotations": True, "version": 0x36003,
            "export_coll": bool(embedded and col_copies), "coll_ext_type": 39056122, "apply_coll_trans": True,
            "export_frame_names": True, "exclude_geo_faces": False, "from_outliner": False,
        })
        if not os.path.isfile(dff_path) or os.path.getsize(dff_path) == 0:
            raise SatkError("EXTERNAL_TOOL", f"DragonFF wrote no DFF for {name}")
        files["dff"] = U.fwd(dff_path)
        bs = exporter.fix_bspheres(dff_path)
        if col_copies and not embedded and col in ("auto", "kit", "file"):
            col_path = os.path.join(out_dir, f"{stem}.col")
            ce = common.dff_module("ops.col_exporter")
            ce.export_col({"file_name": col_path, "version": 3, "collection": ctmp, "apply_transformations": True,
                           "only_selected": False, "clean_mesh": True, "export_face_groups": False})
            if os.path.isfile(col_path) and os.path.getsize(col_path):
                files["col_kit"] = U.fwd(col_path)
        texrows = save_textures([o for o in frames if o.type == "MESH" and len(o.data.polygons)], out_dir, warn) \
            if textures else []
        lod = None
        lc = next((c for c in bpy.data.collections if str(c.get("satk_lod_of", "")).lower() == name.lower()), None)
        if lc is not None:
            lo = next((o for o in lc.objects if o.type == "MESH" and len(o.data.polygons)), None)
            if lo is not None:
                lod = _export_lod(lo, out_dir, de, exporter, tmp, prelight if group == "world" else "none",
                                  _night_wanted(str(coll.get("satk_kit"))), meshes, warn, lod_mats)
                files["lod_dff"] = lod["dff"]
                texrows += save_textures([_MatsOf(lo, lod_mats)], out_dir, warn,
                                         skip={t["name"] for t in texrows})            # only what the HD lacks
        return {"model": name, "stem": stem, "plan": str(coll.get("satk_plan") or ""), "files": files, "textures": texrows,
                "frames": [str(o.name) for o in frames], "atomics": n_atomics, "dropped_slots": dropped,
                "col": {"objects": len(col_objs), "embedded": bool(embedded and col_copies)}, "bsphere": bs,
                "lod": lod, "group": group, "kind": str(coll.get("satk_kit")), "shape": shape, "prelight": pre,
                "warn": warn}
    finally:
        for c in made:
            if c.name in bpy.data.objects:
                bpy.data.objects.remove(c, do_unlink=True)
        for me in meshes:
            try:
                if me.users == 0:
                    bpy.data.meshes.remove(me)
            except ReferenceError:
                pass
        bpy.data.collections.remove(tmp)
        if ctmp is not None:
            nm = ctmp.name
            bpy.data.collections.remove(ctmp)
            other = bpy.data.collections.get(nm + "_satk_tmp_renamed")
            if other is not None:
                other.name = nm
        state.restore()
        if lod_slot is not None:
            for m in lod_mats:
                lod_slot.data.materials.append(m)


def _export_lod(lo, out_dir: str, de, exporter, tmp, prelight: str, night: bool, meshes: list, warn: list[str],
                mats=()) -> dict:
    """The LOD model from a temporary copy (own mesh data, prelit like the HD for map models). ``mats``: the materials
    of the slot (it has none while the export runs, see :func:`export_model`)."""
    base = lo.name.split(".")[0].lower()
    c = lo.copy()
    c.name = f"{base}.kxlod"           # DragonFF names the frame up to the last '.'
    c["satk_generated"] = True
    tmp.objects.link(c)
    pre: dict = {}
    try:
        if mats and not len(lo.data.materials):
            c.data = lo.data.copy()
            meshes.append(c.data)
            for m in mats:
                c.data.materials.append(m)
        if prelight != "none" or len(lo.data.color_attributes):
            pre = _prelight_objects([c], prelight, night, meshes, warn)
        bpy.context.view_layer.update()
        path = os.path.join(out_dir, f"{base}.dff")
        for x in bpy.context.view_layer.objects:
            x.select_set(False)
        c.hide_set(False)
        c.select_set(True)
        de.export_dff({
            "file_name": path, "directory": out_dir, "selected": True, "mass_export": False,
            "preserve_positions": True, "preserve_rotations": True, "version": 0x36003,
            "export_coll": False, "coll_ext_type": 0, "apply_coll_trans": True,
            "export_frame_names": True, "exclude_geo_faces": False, "from_outliner": False,
        })
        exporter.fix_bspheres(path)
        out = {"name": base, "dff": U.fwd(path), "tris": U.tris(lo)}
        if pre:
            out["prelight"] = pre
        return out
    finally:
        if c.name in bpy.data.objects:
            bpy.data.objects.remove(c, do_unlink=True)
