# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.blank`` and ``kit.blank_split``: body blanks in the live session (geometry: ``satk.kit.blanks``).

``kit.blank`` builds the pieces of a plan as ordinary mesh objects named ``<model>_<piece>``: the half body
(``MIRROR`` modifier on X, clipping and merging on) carries the vanilla part boundaries as the face attribute
``satk_part`` (index into the ``part_names`` of the object property ``satk_blank``), material slots follow the kit
presets (``<model>.<role>``), UV layers ``UVMap``/``UVMap2`` are the paint mapping on ``vehiclegrunge256`` (one
continuous island in its clean part) and the shared atlas regions, faces are smooth with hard edges on material
borders, and vehicle pieces carry ``satk_sec`` (the game look classifies them as a vehicle). ``kit.blank_split`` applies the mirror, cuts the parts out along that attribute (doors per
side) into ``<model>_<part>`` objects and, with ``fill``, moves them into the kit slots (``kit.fill``).
"""

from __future__ import annotations

import json

import bmesh
import bpy

from satk.core.errors import SatkError

from . import materials as M
from . import util as U

__all__ = ["blank_method", "split_method", "build_piece", "blank_objects"]

#: IDE section of the vehicle blank kinds (the look and the export read ``satk_sec``).
_SEC = {"automobile": "cars", "bike": "cars", "boat": "cars", "heli": "cars", "plane": "cars"}
#: A mirrored lamp keeps the right-hand key after the mirror; the left side swaps it when the parts are cut out.
_MIRROR_ROLE = {"lamp_fr": "lamp_fl", "lamp_rr": "lamp_rl", "lamp_fl": "lamp_fr", "lamp_rl": "lamp_rr"}


def _model_name(plan: dict, requested: str | None) -> str:
    """The kit model whose materials the blank uses: the requested one, else the plan name, else the only kit model."""
    name = str(requested or plan["name"])
    names = {str(c.get("satk_name", "")).lower() for c in bpy.data.collections
             if c.get("satk_kit") and not c.get("satk_lod_of") and len(c.objects)}
    if name.lower() in names or not names:
        return name
    if len(names) == 1 and not requested:
        return next(iter(names))
    return name


def blank_objects(model: str) -> list:
    """The pieces of the blank of ``model`` (objects tagged ``satk_blank``), sorted by name."""
    return sorted((o for o in bpy.data.objects if o.get("satk_blank") and str(o.get("satk_blank_model", "")).lower()
                   == model.lower()), key=lambda o: o.name)


def _remove_blank(model: str) -> int:
    n = 0
    for o in blank_objects(model) + [o for o in bpy.data.objects if str(o.get("satk_blank_part_of", "")).lower()
                                     == model.lower()]:
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        n += 1
        if data is not None and getattr(data, "users", 1) == 0 and isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
    return n


def build_piece(piece: dict, spec: dict, model: str, ctx=None, primary: bool = False):
    """One mesh object from a spec piece (see :func:`satk.kit.blanks.mesh_spec`)."""
    from satk.kit import kinds as K

    name = f"{model}_{piece['name']}"
    me = bpy.data.meshes.new(name)
    verts = [tuple(v) for v in piece["verts"]]
    faces = [tuple(f) for f in piece["faces"]]
    me.from_pydata(verts, [], faces)
    roles: list[str] = []
    for r in piece["role"]:
        if r not in roles:
            roles.append(r)
    mats = {r: M.ensure(model, K.preset(r, model)) for r in roles}
    for r in roles:
        me.materials.append(mats[r])
    slot = {r: i for i, r in enumerate(roles)}
    for poly, r in zip(me.polygons, piece["role"]):
        poly.material_index = slot[r]
        poly.use_smooth = True                # soft from the start; the hard edges are the spec's "sharp" list
    if piece.get("uv"):
        for lname in ("UVMap", "UVMap2"):
            layer = me.uv_layers.new(name=lname)
            for poly, uv in zip(me.polygons, piece["uv"]):
                for li, (u, v) in zip(poly.loop_indices, uv):
                    layer.data[li].uv = (u, v)
    attr = me.attributes.new("satk_part", "INT", "FACE")
    attr.data.foreach_set("value", piece["part"])
    seams = {tuple(sorted(e)) for e in piece.get("seam") or []}
    sharp = {tuple(sorted(e)) for e in piece.get("sharp") or []}
    for e in me.edges:
        key = tuple(sorted(e.vertices))
        if key in seams:
            e.use_seam = True
        if key in sharp:
            e.use_edge_sharp = True
    me.update()
    o = bpy.data.objects.new(name, me)
    (ctx.collection(None) if ctx is not None else bpy.context.scene.collection).objects.link(o)
    if piece.get("mirror", True):
        mod = o.modifiers.new("satk_mirror", "MIRROR")
        mod.use_axis = (True, False, False)
        mod.use_clip = True
        mod.use_mirror_merge = True
        mod.merge_threshold = 0.05        # vertices nudged off the plane by a step still merge across it
    o["satk_blank"] = json.dumps({"kind": spec["kind"], "piece": piece["name"], "primary": primary,
                                  "part_names": piece["part_names"], "parts": spec["parts"], "tier": spec["tier"]})
    o["satk_blank_model"] = model
    sec = _SEC.get(str(spec.get("kind")))
    if sec:
        o["satk_sec"] = sec                   # the game look classifies the session as a vehicle
    return o


def blank_method(ctx, p: dict) -> dict:
    """Build the blank of a plan file (kit.blank op): the half body with a MIRROR modifier, the part boundaries as a face attribute, an interior piece; split=true cuts the parts out at once."""
    from satk.kit import blanks as B

    path = p.get("plan")
    plan = U.read_json(str(path)) if isinstance(path, str) else p.get("plan_data")
    if not isinstance(plan, dict):
        raise U.bad("kit.blank", "'plan' (a plan file written by the kit.blank op) is required")
    model = _model_name(plan, p.get("model"))
    spec = B.mesh_spec(plan)
    existing = blank_objects(model)
    if existing and not p.get("replace"):
        raise SatkError("EXISTS", f"the blank of {model!r} is already in the scene",
                        hint="pass replace=true to rebuild it, or another name")
    _remove_blank(model)
    objs = [build_piece(piece, spec, model, ctx, primary=(i == 0)) for i, piece in enumerate(spec["pieces"])]
    bpy.context.view_layer.update()
    res = {"model": model, "kind": spec["kind"], "tier": spec["tier"],
           "objects": [[o.name, U.tris(o)] for o in objs], "tris": sum(U.tris(o) for o in objs),
           "changed": [o.name for o in objs]}
    if p.get("split"):
        res["split"] = split_method(ctx, {"model": model, "fill": bool(p.get("fill"))})
        res["changed"] = res["split"].get("changed", res["changed"])
    return res


def _read_mesh(me) -> dict:
    """The mesh as plain arrays: positions, polygons, material indices, UV layers, edges, seams, the part attribute."""
    verts = [tuple(v.co) for v in me.vertices]
    polys = [tuple(p.vertices) for p in me.polygons]
    loops = [tuple(p.loop_indices) for p in me.polygons]
    uvs = {u.name: [tuple(d.uv) for d in u.data] for u in me.uv_layers}
    attr = me.attributes.get("satk_part")
    part = [int(d.value) for d in attr.data] if attr is not None else [0] * len(polys)
    return {"verts": verts, "polys": polys, "loops": loops, "mat": [p.material_index for p in me.polygons],
            "uv": uvs, "part": part, "seam": {tuple(sorted(e.vertices)) for e in me.edges if e.use_seam},
            "sharp": {tuple(sorted(e.vertices)) for e in me.edges if e.use_edge_sharp},
            "smooth": [p.use_smooth for p in me.polygons],
            # corner normals of the whole shell: the parts keep them (a cut must not show in the shading)
            "cn": [tuple(c.vector) for c in me.corner_normals] if hasattr(me, "corner_normals") else None,
            # the evaluated mesh points at evaluated copies of the materials: take the originals by name
            "materials": [bpy.data.materials.get(m.name) if m is not None else None for m in me.materials]}


def _classify(a: dict, part_names: list[str], parts: list[dict]) -> dict:
    """``{(part, side): [polygon index]}``: polygons by their ``satk_part`` attribute; sided parts split at x = 0."""
    sided = {p["name"] for p in parts if p.get("sided")}
    out: dict = {}
    for i, f in enumerate(a["polys"]):
        idx = a["part"][i]
        name = part_names[idx] if 0 <= idx < len(part_names) else part_names[0]
        side = None
        if name in sided:
            side = "l" if sum(a["verts"][v][0] for v in f) / len(f) < 0 else "r"
        out.setdefault((name, side), []).append(i)
    return out


def _part_mesh(a: dict, faces: list[int], name: str, role_swap=None):
    """A new mesh of the polygons ``faces`` of ``a`` (materials, UV layers, seams kept)."""
    remap: dict[int, int] = {}
    verts: list[tuple] = []
    polys: list[tuple] = []
    for i in faces:
        row = []
        for v in a["polys"][i]:
            if v not in remap:
                remap[v] = len(verts)
                verts.append(a["verts"][v])
            row.append(remap[v])
        polys.append(tuple(row))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], polys)
    used = sorted({a["mat"][i] for i in faces})
    slot = {m: k for k, m in enumerate(used)}
    for m in used:
        me.materials.append(a["materials"][m] if m < len(a["materials"]) else None)
    for poly, i in zip(me.polygons, faces):
        poly.material_index = slot[a["mat"][i]]
        poly.use_smooth = bool(a["smooth"][i]) if a.get("smooth") else True
        if role_swap is not None:
            sw = role_swap(a["materials"][a["mat"][i]] if a["mat"][i] < len(a["materials"]) else None)
            if sw is not None:
                if sw.name not in [m.name for m in me.materials if m]:
                    me.materials.append(sw)
                poly.material_index = [m.name for m in me.materials if m].index(sw.name)
    for lname, uv in a["uv"].items():
        layer = me.uv_layers.new(name=lname)
        for poly, i in zip(me.polygons, faces):
            for li, src in zip(poly.loop_indices, a["loops"][i]):
                layer.data[li].uv = uv[src]
    inv = {new: old for old, new in remap.items()}
    for e in me.edges:
        x, y = e.vertices
        key = tuple(sorted((inv[x], inv[y])))
        if key in a["seam"]:
            e.use_seam = True
        if key in a.get("sharp", ()):
            e.use_edge_sharp = True
    me.update()
    cn = a.get("cn")
    if cn:
        # the shell's own corner normals: along the cut a part's border vertex would otherwise average only its own
        # faces (a bonnet edge pointing straight up next to a wing side pointing out: a hard crease on every panel
        # line); with the shell's normals the panels shade as the one surface they were cut from
        normals = [(0.0, 0.0, 1.0)] * len(me.loops)
        for poly, i in zip(me.polygons, faces):
            for li, src in zip(poly.loop_indices, a["loops"][i]):
                normals[li] = cn[src]
        me.normals_split_custom_set(normals)
        me["satk_normals"] = "shell"
    return me


def split_method(ctx, p: dict) -> dict:
    """Apply the MIRROR modifier of a blank body and cut it into its parts along the face attribute (doors per side) as <model>_<part> objects; fill=true moves them into the kit slots (kit.fill)."""
    from satk.kit import blanks as B
    from satk.kit import kinds as K

    model = str(p.get("model") or "")
    cands = [o for o in bpy.data.objects if o.get("satk_blank") and o.type == "MESH" and
             (not model or str(o.get("satk_blank_model", "")).lower() == model.lower())]
    if p.get("object"):
        src = U.obj(str(p["object"]), "kit.blank_split")
    else:
        bodies = [o for o in cands if json.loads(o["satk_blank"]).get("primary")]
        if len(bodies) != 1:
            raise SatkError("NOT_FOUND" if not bodies else "AMBIGUOUS",
                            "kit.blank_split: " + ("no blank body in the scene (kit.blank first)" if not bodies
                                                   else "several blank bodies: give 'model' or 'object'"),
                            data={"bodies": [o.name for o in bodies][:6]} if bodies else None)
        src = bodies[0]
    meta = json.loads(src["satk_blank"])
    model = str(src.get("satk_blank_model") or model)
    names, parts = meta["part_names"], meta["parts"]
    slot_of = {pt["name"]: pt for pt in parts}
    me, free = U.evaluated_mesh(src)          # the mirrored mesh
    try:
        arr = _read_mesh(me)
    finally:
        free()
    groups = _classify(arr, names, parts)
    _remove_split(model)
    src_name = src.name
    src.name = src_name + "_src"                    # <model>_<part> may equal the name of the source

    def swap(mat):
        sw = _MIRROR_ROLE.get(str(mat.get("satk_role"))) if mat is not None else None
        return M.ensure(model, K.preset(sw, model)) if sw else None

    made: list = []
    for (part, side), faces in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        nm = f"{model}_{part}" + (f"_{side}" if side else "")
        o = bpy.data.objects.new(nm, _part_mesh(arr, faces, nm, swap if side == "l" else None))
        ctx.collection(None).objects.link(o)
        pt = slot_of.get(part, {"slot": part})
        o["satk_blank_part_of"] = model
        o["satk_blank_part"] = part
        if src.get("satk_sec"):
            o["satk_sec"] = src["satk_sec"]
        o["satk_blank_slot"] = _resolve_slot(pt, side, model)
        made.append(o)
    if not p.get("keep"):
        d = src.data
        bpy.data.objects.remove(src, do_unlink=True)    # the interior piece stays: it joins the chassis slot
        if d is not None and d.users == 0:
            bpy.data.meshes.remove(d)
    bpy.context.view_layer.update()                 # new objects must be in the depsgraph before they are evaluated
    res: dict = {"model": model, "parts": [[o.name, o["satk_blank_slot"], U.tris(o)] for o in made],
                 "tris": sum(U.tris(o) for o in made), "changed": [o.name for o in made]}
    if p.get("fill"):
        res["fill"] = _fill_slots(ctx, model, made, res)
    # the panel lines shade as the one surface they were cut from (bonnet, doors, boot next to the body)
    from .shade import match_seams

    bpy.context.view_layer.update()
    parts = [o for o in made if _alive(o)]
    if p.get("fill"):
        parts = [bpy.data.objects[s] for s, _t in res["fill"] if bpy.data.objects.get(s) is not None]
    sm = match_seams(parts)
    if sm.get("corners"):
        res["seams"] = {"objects": sm["objects"], "corners": sm["corners"]}
    return res


def _alive(o) -> bool:
    try:
        return bool(o.name)
    except ReferenceError:
        return False


def _resolve_slot(part: dict, side: str | None, model: str) -> str:
    """The kit slot a part fills: its own slot, else the first of its alternatives (``alt``) that exists in the scene."""
    from satk.kit import blanks as B

    first = B.part_slot(part, side, model)
    if bpy.data.objects.get(first) is not None:
        return first
    for alt in part.get("alt") or []:
        name = B.part_slot({"slot": alt}, side, model)
        if bpy.data.objects.get(name) is not None:
            return name
    return first


def _remove_split(model: str) -> None:
    for o in [o for o in bpy.data.objects if str(o.get("satk_blank_part_of", "")).lower() == model.lower()]:
        d = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if d is not None and d.users == 0 and isinstance(d, bpy.types.Mesh):
            bpy.data.meshes.remove(d)


def _fill_slots(ctx, model: str, made: list, res: dict) -> list:
    """Move the cut parts into the kit slots of ``model`` (a part without a slot stays an object)."""
    from .methods import fill

    by_slot: dict[str, list] = {}
    for o in made:
        by_slot.setdefault(str(o["satk_blank_slot"]), []).append(o)
    # the other pieces of the blank (the interior of the chassis, the wheels of a bike) go to the slot of their part
    from satk.kit import blanks as B

    for o in bpy.data.objects:
        if not (o.get("satk_blank") and str(o.get("satk_blank_model", "")).lower() == model.lower()):
            continue
        meta = json.loads(o["satk_blank"])
        if meta.get("primary"):
            continue
        part = meta["part_names"][0]
        pt = next((x for x in meta["parts"] if x["name"] == part), {"slot": part})
        by_slot.setdefault(_resolve_slot(pt, None, model), []).append(o)
    filled, missing = [], []
    for slot, objs in sorted(by_slot.items()):
        so = bpy.data.objects.get(slot)
        if so is None or so.get("satk_role") not in ("part", "root", "lod", "col", "shadow"):
            missing.append(slot)
            continue
        bpy.context.view_layer.update()             # the depsgraph must know the previous fill (deleted sources)
        r = fill(ctx, {"slot": slot, "objects": [o.name for o in objs]})
        filled.append([slot, r.get("tris")])
    if missing:
        res.setdefault("warn", []).append(f"NOT_FOUND: no kit slot for {', '.join(missing[:8])} (kit.template first); "
                                          "the parts stay objects")
    return filled
