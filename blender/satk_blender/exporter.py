# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Export models of a satk scene with DragonFF: ``<name>.dff`` (RW 3.6.0.3), ``<name>.txd``
(RGBA8888 for now - DragonFF has no DXT encoder) and ``<name>.col`` (COL3).

Targets are given as object names, model names or SIDs (``model:``/``inst:``); an instance maps to
the prototype of its model (the model in its own coordinates). Generated helper objects (cloned
wheels) are never exported. A model without collision objects gets a COL built from its render mesh
(warning ``COL_FROM_MESH``). Vehicles keep their embedded collision inside the DFF.

The Blender job only writes binary files plus ``export.json`` (models, files, placements with the
world quaternion and MTA ``ZXY`` Euler angles); ``meta.xml``, ``client.lua`` and IDE/IPL fragments
are written by ``satk.blender.packaging`` on the satk side.
"""

from __future__ import annotations

import difflib
import math
import os

import bpy
from mathutils import Matrix

from . import common, shading


class ExportError(Exception):
    def __init__(self, code: str, msg: str, hint: str | None = None, did_you_mean=()):
        super().__init__(msg)
        self.code, self.msg, self.hint, self.did_you_mean = code, msg, hint, list(did_you_mean)


def _model_collections() -> dict[str, object]:
    """``{lower model name: DragonFF collection}`` (collections named ``<model>.dff``)."""
    out = {}
    for c in bpy.data.collections:
        if c.name.lower().endswith(".dff"):
            out[c.name[:-4].lower()] = c
    return out


def _coll_of_sid(sid: str):
    for c in bpy.data.collections:
        if c.get("satk_sid") == sid and c.name.lower().endswith(".dff"):
            return c
    return None


def resolve_targets(names: list[str]) -> list:
    """Model collections for object names / model names / SIDs (deduplicated, in order)."""
    models = _model_collections()
    out: list = []
    for raw in names:
        n = raw.strip()
        c = None
        o = bpy.data.objects.get(n)
        if o is not None:
            if o.get("satk_proto") or o.get("satk_generated"):
                c = next((u for u in o.users_collection if u.name.lower().endswith(".dff")), None)
            if c is None and o.get("satk_model"):
                c = _coll_of_sid(str(o["satk_model"]))
            if c is None:
                c = next((u for u in o.users_collection if u.name.lower().endswith(".dff")), None)
        elif n.lower().startswith("model:"):
            c = _coll_of_sid(n.lower())
            if c is None:
                c = models.get(n[6:].lower())
        elif n.lower().startswith("inst:"):
            inst = next((x for x in bpy.data.objects if x.get("satk_sid") == n.lower()), None)
            if inst is not None and inst.get("satk_model"):
                c = _coll_of_sid(str(inst["satk_model"]))
        else:
            c = models.get(n.lower())
        if c is None:
            pool = [x.name for x in bpy.data.objects] + list(models)
            raise ExportError("NOT_FOUND", f"nothing to export for {n!r}",
                              hint="pass an object name, a model name or a model:/inst: SID from the import",
                              did_you_mean=difflib.get_close_matches(n, pool, 5, 0.5))
        if c not in out:
            out.append(c)
    return out


def _unexclude_all(lc=None) -> None:
    lc = lc or bpy.context.view_layer.layer_collection
    for ch in lc.children:
        ch.exclude = False
        ch.hide_viewport = False
        _unexclude_all(ch)


def _layer_collections(lc=None, out=None) -> list:
    lc = lc or bpy.context.view_layer.layer_collection
    out = [] if out is None else out
    for ch in lc.children:
        out.append(ch)
        _layer_collections(ch, out)
    return out


def _link_src(sock):
    """``(node name, output identifier)`` linked into an input socket, or ``None``."""
    if sock is None or not sock.is_linked:
        return None
    lk = sock.links[0]
    return lk.from_node.name, lk.from_socket.identifier


def _relink(nt, sock, src) -> None:
    if sock is None:
        return
    if src is None:
        for lk in list(sock.links):
            nt.links.remove(lk)
        return
    node = nt.nodes.get(src[0])
    out = next((s for s in node.outputs if s.identifier == src[1]), None) if node is not None else None
    if out is not None:
        nt.links.new(out, sock)


class SceneState:
    """What an export changes in the open scene, put back afterwards: the add-on exports from the
    user's live scene (headless jobs never save it). Covers collection exclusion/visibility, object
    visibility, selection and the active object, and the shader links/nodes of the exported materials."""

    def __init__(self):
        vl = bpy.context.view_layer
        self.lcs = {lc.name: (lc.exclude, lc.hide_viewport) for lc in _layer_collections()}
        self.active = vl.objects.active.name if vl.objects.active else None
        self.selected = {o.name for o in vl.objects if o.select_get()}
        self.hidden = {o.name for o in vl.objects if o.hide_get()}
        self.hide_viewport = {o.name: o.hide_viewport for o in bpy.data.objects}
        self.mats: dict[str, tuple] = {}

    def keep_materials(self, mats) -> None:
        """Remember node names and the Surface/Base Color links of ``mats`` (before they are changed)."""
        for m in mats:
            if m is None or not m.node_tree or m.name in self.mats:
                continue
            nt = m.node_tree
            out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
            bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
            self.mats[m.name] = ({n.name for n in nt.nodes},
                                 _link_src(out.inputs["Surface"]) if out else None,
                                 _link_src(bsdf.inputs["Base Color"]) if bsdf else None)

    def restore(self) -> None:
        for name, (nodes, surf, base) in self.mats.items():
            m = bpy.data.materials.get(name)
            if m is None or not m.node_tree:
                continue
            nt = m.node_tree
            for n in list(nt.nodes):
                if n.name not in nodes:  # added by DragonFF's material wrapper during the export
                    nt.nodes.remove(n)
            out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
            bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
            _relink(nt, out.inputs["Surface"] if out else None, surf)
            _relink(nt, bsdf.inputs["Base Color"] if bsdf else None, base)
        for o in bpy.data.objects:
            hv = self.hide_viewport.get(o.name)
            if hv is not None and o.hide_viewport != hv:
                o.hide_viewport = hv
        for lc in _layer_collections():
            st = self.lcs.get(lc.name)
            if st is not None:
                lc.hide_viewport = st[1]
                lc.exclude = st[0]
        vl = bpy.context.view_layer
        for o in vl.objects:
            o.hide_set(o.name in self.hidden)
            o.select_set(o.name in self.selected)
        vl.objects.active = bpy.data.objects.get(self.active) if self.active else None
        vl.update()


def _select_only(objs) -> None:
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.hide_set(False)
        o.hide_viewport = False
        o.select_set(True)


def _placement_matrix(o, prototypes):
    """Remove a part's original model-space transform from its current world transform."""
    matrix = o.matrix_world.copy()
    if o.get("satk_instance_root"):
        return matrix
    local = o.get("satk_proto_matrix")
    if local is not None and len(local) == 16:
        local = Matrix([local[i:i + 4] for i in range(0, 16, 4)])
    else:
        # Older scenes have no placement roots/local tags; their linked mesh datablock
        # identifies the hidden prototype whose local transform was baked at import.
        local = prototypes.get(o.data) if o.type == "MESH" else None
    return matrix @ local.inverted_safe() if local is not None else matrix


def _same_placement(a, b) -> bool:
    # Blender matrices are float32; in world coordinates ~3000, roundoff exceeds 1e-4.
    return all(abs(a[r][c] - b[r][c]) <= (1e-3 if c == 3 else 1e-5) for r in range(4) for c in range(4))


def _placements(model_sid: str) -> list[dict]:
    """Instances of a model in the scene: world position, world quaternion and MTA ZXY Euler."""
    bpy.context.view_layer.update()
    candidates: dict[str, list] = {}
    prototypes = {}
    for o in bpy.data.objects:
        if o.get("satk_model") == model_sid and o.get("satk_proto") and o.type == "MESH":
            prototypes.setdefault(o.data, common.world_matrix(o))
        if o.get("satk_model") != model_sid or o.get("satk_proto") or o.get("satk_generated"):
            continue
        sid = str(o.get("satk_sid") or "")
        if sid.startswith("inst:") and getattr(getattr(o, "dff", None), "type", "OBJ") not in ("COL", "SHA"):
            candidates.setdefault(sid, []).append(o)
    rows = []
    for sid, objects in sorted(candidates.items()):
        o = min(objects, key=lambda ob: (not ob.get("satk_instance_root"), ob.type != "MESH", ob.name))
        matrix = _placement_matrix(o, prototypes)
        parts = [p for p in objects if p.type == "MESH" and p.get("satk_proto_matrix") is not None]
        if o.get("satk_instance_root") and parts:
            current = [_placement_matrix(p, prototypes) for p in sorted(parts, key=lambda p: p.name)]
            if any(not _same_placement(current[0], m) for m in current[1:]):
                raise ExportError("BAD_PARAMS", f"{sid}: mesh parts have incompatible placement transforms",
                                  hint=f"move/rotate {o.name!r} to edit the whole placement; edit its prototype "
                                       "to change model geometry")
            if not _same_placement(matrix, current[0]):
                matrix = current[0]  # direct mesh edits also define a placement when all parts agree
        loc, rot, _scale = matrix.decompose()
        e = rot.to_euler("YXZ")  # R = Rz * Rx * Ry  == MTA object order "ZXY"
        row = {"sid": sid, "pos": [round(v, 4) for v in loc],
               "q": [round(rot.x, 6), round(rot.y, 6), round(rot.z, 6), round(rot.w, 6)],
               "rot_zxy_deg": [round(math.degrees(e.x), 4), round(math.degrees(e.y), 4), round(math.degrees(e.z), 4)],
               "area": int(o.get("satk_area") or 0), "lod": bool(o.get("satk_lod"))}
        if o.get("satk_iflags") is not None:  # absent in scenes imported before it was kept (satk fills it)
            row["iflags"] = int(o["satk_iflags"])
        rows.append(row)
    return rows


def _temp_collection(name: str):
    """A collection named exactly ``name`` (DragonFF names the COL model after its collection;
    the engine matches COL models to IDE objects by that name)."""
    other = bpy.data.collections.get(name)
    if other is not None:
        other.name = name + "_satk_tmp_renamed"
    tmp = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(tmp)
    return tmp, other


def _write_col(name: str, out_path: str, col_objs=None, mesh_objs=None) -> bool:
    """COL3 ``name`` from existing collision objects, or from the render meshes when there are none."""
    ce = common.dff_module("ops.col_exporter")
    tmp, other = _temp_collection(name[:22])
    made = []
    try:
        if col_objs:
            for o in col_objs:
                tmp.objects.link(o)
        else:
            for o in mesh_objs or ():
                if o.type != "MESH" or o.get("satk_generated") or common.is_damage_part(o.name):
                    continue
                me = o.data.copy()
                me.transform(common.world_matrix(o))
                co = bpy.data.objects.new(f"{name}.ColMesh", me)
                tmp.objects.link(co)
                co.dff.type = "COL"
                made.append(co)
            if not made:
                return False
        ce.export_col({"file_name": out_path, "version": 3, "collection": tmp, "apply_transformations": False,
                       "only_selected": False, "clean_mesh": False, "export_face_groups": False})
        return True
    finally:
        for o in list(tmp.objects):
            tmp.objects.unlink(o)
        for co in made:
            me = co.data
            bpy.data.objects.remove(co, do_unlink=True)
            bpy.data.meshes.remove(me)
        bpy.data.collections.remove(tmp)
        if other is not None:
            other.name = name[:22]


def _col_collection(name: str):
    want = f"{name}.col.".lower()
    for c in bpy.data.collections:
        if c.name.lower().startswith(want) and any(getattr(o.dff, "type", "") in ("COL", "SHA") for o in c.objects):
            return c
    return None


def _drop_empty_image_nodes(mats) -> None:
    for m in mats:
        if not m.node_tree:
            continue
        for n in list(m.node_tree.nodes):
            if n.type == "TEX_IMAGE" and n.image is None and not n.label:
                m.node_tree.nodes.remove(n)


def export(args: dict, out_dir: str) -> dict:
    """Export ``args["objects"]`` into ``out_dir`` (created only once the objects are resolved).

    The scene is put back as it was afterwards (:class:`SceneState`), also when DragonFF fails.
    """
    de = common.dff_module("ops.dff_exporter")
    te = common.dff_module("ops.txd_exporter")
    targets = resolve_targets(args["objects"])
    state = SceneState()
    try:
        return _export(targets, out_dir, de, te, state)
    finally:
        state.restore()


def _export(targets, out_dir: str, de, te, state: SceneState) -> dict:
    _unexclude_all()
    bpy.context.view_layer.update()
    os.makedirs(out_dir, exist_ok=True)
    warn: list[str] = []
    models = []
    for coll in targets:
        name = coll.name[:-4]
        stem = name.lower()
        objs = [o for o in coll.all_objects if not o.get("satk_generated")]
        sid = coll.get("satk_sid") or next((str(o["satk_sid"]) for o in objs if o.get("satk_sid")), None)
        sec = next((str(o["satk_sec"]) for o in objs if o.get("satk_sec")), None)
        vehicle = sec == "cars"
        exp_objs = [o for o in objs if getattr(o.dff, "type", "OBJ") in ("OBJ", "2DFX")
                    or (vehicle and getattr(o.dff, "type", "") in ("COL", "SHA"))]
        mats = {s.material for o in exp_objs for s in getattr(o, "material_slots", ()) if s.material}
        state.keep_materials(mats)
        # DragonFF reads the Principled BSDF linked to the Material Output (PrincipledBSDFWrapper would
        # otherwise create a fresh, untextured one): undo the game shading links before exporting
        shading.set_game_shading(False, mats)
        shading.restore_for_export(mats)
        files = {}
        # TXD first: DragonFF's DFF exporter wraps materials with PrincipledBSDFWrapper(is_readonly=False),
        # which adds empty image nodes to untextured materials - and the TXD exporter trips over those.
        _drop_empty_image_nodes(mats)
        txd_path = os.path.join(out_dir, f"{stem}.txd")
        te.txd_exporter.version = 0x36003
        te.txd_exporter.export_textures([o for o in exp_objs if o.type == "MESH"], txd_path)
        files["txd"] = common.fwd(txd_path)
        dff_path = os.path.join(out_dir, f"{stem}.dff")
        _select_only(exp_objs)
        de.export_dff({
            "file_name": dff_path, "directory": out_dir, "selected": True, "mass_export": False,
            "preserve_positions": True, "preserve_rotations": True, "version": 0x36003,
            "export_coll": vehicle, "coll_ext_type": 39056122, "apply_coll_trans": True,
            "export_frame_names": True, "exclude_geo_faces": False, "from_outliner": False,
        })
        files["dff"] = common.fwd(dff_path)
        if sec not in ("cars", "peds"):
            col_path = os.path.join(out_dir, f"{stem}.col")
            cc = _col_collection(name)
            if cc is not None:
                _write_col(stem, col_path, col_objs=[o for o in cc.objects if getattr(o.dff, "type", "") in ("COL", "SHA")])
                files["col"] = common.fwd(col_path)
            elif _write_col(stem, col_path, mesh_objs=[o for o in exp_objs if o.type == "MESH"]):
                files["col"] = common.fwd(col_path)
                warn.append(f"COL_FROM_MESH: {stem}.col was built from the render mesh (import with col=true "
                            "to keep the game collision)")
        for k, p in files.items():
            if not os.path.isfile(p) or os.path.getsize(p) == 0:
                raise ExportError("EXTERNAL_TOOL", f"DragonFF wrote no {k.upper()} for {stem}")
        model_id = None
        if sid and str(sid).startswith("model:") and str(sid)[6:].isdigit():
            model_id = int(str(sid)[6:])
        entry = {"name": stem, "sid": sid, "id": model_id, "sec": sec or "objs", "files": files,
                 "sizes": {k: os.path.getsize(p) for k, p in files.items()},
                 "insts": _placements(sid) if sid else []}
        ide = coll.get("satk_ide")
        if ide is not None:  # IDE definition data kept by the import (draw distance, flags, tobj times)
            entry["ide"] = {k: (v if isinstance(v, (str, int, float)) else list(v)) for k, v in ide.items()}
        models.append(entry)
    warn.append("TXD_RGBA8888: textures are exported uncompressed (the add-on has no DXT encoder yet)")
    return {"models": models, "warnings": warn}
