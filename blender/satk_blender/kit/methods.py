# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Kit methods (contract K6), discovered by the studio (K3): ``satk blender methods --query kit``.

Every method takes ``(ctx, params)`` and returns a small dict (``changed`` = the objects it built or edited);
the MIT ops ``kit.template`` and ``kit.export`` call ``kit.template`` / ``kit.export`` through K4.
"""

from __future__ import annotations

import os

import bpy

from satk.core.errors import SatkError
from satk.studio.core import checkpointed, readonly

from . import util as U
from .adopt import adopt_method
from .bake import bake_method
from .blanks import blank_method, split_method
from .col import col_method
from .gen_damage import damage_method
from .gen_lod import lod_method
from .gen_vlo import vlo_method
from .gen_wheel import wheel_method
from .uv import uv_region_method

__all__ = ["METHODS"]


def template(ctx, p: dict) -> dict:
    """Scaffold a new asset from a template plan file (kit.template op): frames in vanilla order, empty slots, presets, COL skeleton."""
    from .template import build

    path = U.need(p, "plan", "kit.template")
    plan = U.read_json(str(path))
    res = build(plan, plan_path=str(path), replace=bool(p.get("replace")), textures=p.get("textures"))
    res["changed"] = [res["root"]] if res.get("root") else []
    return res


def material_preset(ctx, p: dict) -> dict:
    """Assign a material preset (paint1, glass, lamp_fl, map, ...) to an object's slot; image= sets an own texture from a PNG."""
    from satk.kit import kinds as K

    from . import materials as M

    o = U.obj(U.need(p, "object", "kit.material_preset"), "kit.material_preset")
    role = str(U.need(p, "role", "kit.material_preset"))
    coll = next((c for c in o.users_collection if c.get("satk_name")), None)
    model = str(p.get("model") or (coll.get("satk_name") if coll is not None else o.name))
    mat = M.ensure(model, K.preset(role, model))
    if p.get("image"):
        png = str(p["image"])
        if not os.path.isfile(png):
            raise SatkError("NOT_FOUND", f"kit.material_preset: no image {png}")
        tex = str(mat.get("satk_texture") or role)
        img = bpy.data.images.load(png, check_existing=False)
        old = bpy.data.images.get(tex)
        if old is not None and old is not img:
            old.name = tex + "_old"
        img.name = tex
        img.pack()
        img["satk_shared"] = False
        img["satk_texture"] = tex
        M.set_image(mat, img)
        if old is not None and old.users == 0:
            bpy.data.images.remove(old)
    if o.type == "MESH":
        slot = p.get("slot", "append")
        mats = o.data.materials
        if slot == "append":
            if mat.name not in [m.name for m in mats if m]:
                mats.append(mat)
        else:
            i = int(slot)
            while len(mats) <= i:
                mats.append(None)
            mats[i] = mat
    return {"object": o.name, "material": mat.name, "texture": mat.get("satk_texture"), "changed": [o.name]}


def shade(ctx, p: dict) -> dict:
    """sa_shade: weld, smooth, hard edges by the vanilla rule (mode seams: material borders, UV seams folding over seam_angle, marked creases, fold-backs over hard; corners stay soft; mode angle = the old dihedral rule), Weighted Normal last, panel cuts between parts shaded as one surface (match_seams, default on), DFF re-read check."""
    from .shade import dff_shade_metrics, match_seams, mesh_k1, shade_object

    if p.get("objects"):
        targets = U.objs(p["objects"], "kit.shade")
    else:
        coll = U.clump(p.get("model"))
        targets = [o for o in coll.objects if o.type == "MESH" and len(o.data.polygons)]
        targets += [o for c in bpy.data.collections if str(c.get("satk_lod_of", "")).lower()
                    == str(coll.get("satk_name")).lower() for o in c.objects if o.type == "MESH" and len(o.data.polygons)]
    if not targets:
        raise SatkError("BAD_PARAMS", "kit.shade: nothing to shade (empty slots)")
    check = bool(p.get("check", True))
    warn: list[str] = []
    rows = []
    failed = []
    for o in targets:
        me, free = U.evaluated_mesh(o)
        try:
            before = mesh_k1(me)
        finally:
            free()
        r = shade_object(o, cls=p.get("class"), weld=U.num(p, "weld", 0.0005, "kit.shade", 0.0, 0.1),
                         weighted=bool(p.get("weighted", True)), smooth=p.get("smooth"), hard=p.get("hard"),
                         seams=p.get("seams"), materials=p.get("materials"), warn=warn,
                         mode=str(p.get("mode") or "seams"), seam_angle=p.get("seam_angle"))
        row = {"o": o.name, "before": [before.get("shade.normal_bend"), before.get("shade.flat_share")],
               "welded": r.get("welded", 0)}
        if check:
            bpy.context.view_layer.update()
            me, free = U.evaluated_mesh(o)
            try:
                shaded = mesh_k1(me)                      # what the scene shows after this step
            finally:
                free()
            m = dff_shade_metrics(o, ctx.out_dir)
            row["dff"] = [m.get("shade.normal_bend"), m.get("shade.flat_share"), m.get("dff.verts_per_tri")]
            s_bend, s_flat = shaded.get("shade.normal_bend") or 0.0, shaded.get("shade.flat_share") or 0.0
            d_bend, d_flat = m.get("shade.normal_bend") or 0.0, m.get("shade.flat_share") or 0.0
            # the DFF lost the normals: it is flatter than the shaded scene mesh by a clear margin
            if d_flat > s_flat + 0.1 and d_bend < 0.7 * s_bend and r.get("sharp", 0) < len(o.data.edges):
                failed.append(o.name)
        rows.append(row)
    seams = None
    if p.get("match_seams", True) and len(targets) > 1:
        bpy.context.view_layer.update()
        from .shade import _rule

        # a fold between two parts is soft below the class's hard angle, as a fold inside one mesh (vanilla rule)
        hard = float(_rule(targets[0], p.get("class"), str(p.get("mode") or "seams")).get("hard", 110.0))
        seams = match_seams(targets, max_deg=U.num(p, "seam_deg", min(hard, 120.0), "kit.shade", 1.0, 179.0))
    if failed:
        raise SatkError("CHECK_FAILED", f"kit.shade: the normals did not reach the DFF of {', '.join(failed[:5])}",
                        hint="every face must be smooth before the normals step; check modifiers after satk_wn",
                        data={"rows": rows[:8]})
    res = {"objects": len(rows), "rows": [[r["o"]] + r["before"] + (r.get("dff") or []) for r in rows][:12],
           "cols": ["object", "bend_before", "flat_before"] + (["bend_dff", "flat_dff", "verts_per_tri"] if check else []),
           "changed": [r["o"] for r in rows][:12]}
    if seams and seams.get("objects"):
        res["seams"] = {k: v for k, v in seams.items() if v}
    if warn:
        res["warn"] = warn[:6]
    return res


def weld_boundary(me, dist: float) -> int:
    """Merge the open-boundary vertices of ``me`` that lie within ``dist`` of each other (pieces joined edge to edge
    become one welded surface; vertices inside a surface never move). Returns the number of merged vertices."""
    import bmesh

    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        verts = [v for v in bm.verts if any(e.is_boundary for e in v.link_edges)]
        n0 = len(bm.verts)
        if verts:
            bmesh.ops.remove_doubles(bm, verts=verts, dist=float(dist))
        n = n0 - len(bm.verts)
        if n:
            bm.to_mesh(me)
            me.update()
        return n
    finally:
        bm.free()


def fill(ctx, p: dict) -> dict:
    """Move modelling objects into a kit slot (joined in the slot's space, modifiers applied); weld=true (or a distance in m) merges the boundary vertices where pieces meet; sources are deleted unless keep=true."""
    from .gen_vlo import merged_mesh, replace_mesh

    slot = U.obj(U.need(p, "slot", "kit.fill"), "kit.fill")
    if slot.type != "MESH" or slot.get("satk_role") not in ("part", "root", "lod", "col", "shadow"):
        raise SatkError("BAD_PARAMS", f"kit.fill: {slot.name} is not a kit slot (a MESH made by kit.template)")
    src = U.objs(U.need(p, "objects", "kit.fill"), "kit.fill")
    if slot in src:
        raise U.bad("kit.fill", "the slot cannot be one of its sources")
    sources = ([slot] if p.get("append") and len(slot.data.polygons) else []) + src
    me = merged_mesh(sources, slot, slot.name)
    keep_mats = list(slot.data.materials) if not p.get("append") else []
    replace_mesh(slot, me)
    for m in keep_mats:
        if m is not None and m.name not in [x.name for x in slot.data.materials if x]:
            slot.data.materials.append(m)
    while len(slot.data.uv_layers) < (2 if slot.dff.uv_map2 else 1):
        slot.data.uv_layers.new(name=f"UVMap{len(slot.data.uv_layers) + 1}")
    weld = p.get("weld", False)
    welded = 0
    if weld is not False and weld is not None:
        dist = 0.002 if weld is True else U.num(p, "weld", 0.002, "kit.fill", 0.0, 0.2)
        welded = weld_boundary(slot.data, dist) if dist > 0 else 0
    if not p.get("keep"):
        for o in src:
            data = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if data is not None and getattr(data, "users", 1) == 0 and isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
    out = {"slot": slot.name, "tris": U.tris(slot), "sources": len(src), "changed": [slot.name]}
    if weld is not False and weld is not None:
        out["welded"] = welded
    return out


def _frames_table(coll, limit: int) -> dict:
    """Frames of a kit model for refitting: world position of every frame object and dummy, its parent, its slot
    triangles (empty = 0) and, for a part, the world bounding box of its geometry; plus the ghost's part sizes."""
    rows = []
    objs = sorted((o for o in coll.objects if o.get("satk_role") in ("root", "dummy", "part", "bone")),
                  key=lambda o: o.dff.frame_index)
    for o in objs:
        w = o.matrix_world.translation
        row = [o.name, str(o.get("satk_role")), o.parent.name if o.parent is not None else "",
               [round(w.x, 3), round(w.y, 3), round(w.z, 3)]]
        if o.type == "MESH" and len(o.data.polygons):
            pts = [o.matrix_world @ v.co for v in o.data.vertices]
            lo = [round(min(q[k] for q in pts), 3) for k in range(3)]
            hi = [round(max(q[k] for q in pts), 3) for k in range(3)]
            row += [U.tris(o), [lo, hi]]
        elif o.type == "MESH":
            row += [0]
        rows.append(row)
    out = {"frames": {"cols": ["name", "role", "parent", "world", "tris", "bbox"], "rows": rows[:limit],
                      "total": len(rows)}}
    ghost = [o for o in bpy.data.objects if o.get("satk_ghost") and o.type == "MESH" and len(o.data.polygons)]
    if ghost:
        gr = []
        for o in sorted(ghost, key=lambda x: x.name):
            pts = [o.matrix_world @ v.co for v in o.data.vertices]
            lo = [min(q[k] for q in pts) for k in range(3)]
            hi = [max(q[k] for q in pts) for k in range(3)]
            gr.append([o.name.split(".")[0], [round(hi[k] - lo[k], 3) for k in range(3)],
                       [round((hi[k] + lo[k]) / 2, 3) for k in range(3)]])
        out["ghost"] = {"cols": ["part", "size", "centre"], "rows": gr[:limit], "total": len(gr)}
    return out


@readonly
def info(ctx, p: dict) -> dict:
    """The kit model: kind, tier, slots with triangles (empty ones listed), materials, COL objects, LOD; frames=true writes every frame and dummy (world position, parent, part bbox) and the ghost's part sizes to frames_file for refitting (limit = rows in the reply)."""
    coll = U.clump(p.get("model"))
    slots = [o for o in coll.objects if o.type == "MESH"]
    filled = [[o.name, o.get("satk_slot", ""), U.tris(o)] for o in slots if len(o.data.polygons)]
    empty = [o.name for o in slots if not len(o.data.polygons)]
    col = next((c for c in bpy.data.collections if str(c.get("satk_col_of", "")).lower()
                == str(coll.get("satk_name")).lower()), None)
    lc = next((c for c in bpy.data.collections if str(c.get("satk_lod_of", "")).lower()
               == str(coll.get("satk_name")).lower()), None)
    lod = None
    if lc is not None:
        lo = next((o for o in lc.objects if o.type == "MESH"), None)
        lod = {"name": lo.name, "tris": U.tris(lo)} if lo is not None else None
    out = {"model": coll.get("satk_name"), "kind": coll.get("satk_kit"), "tier": coll.get("satk_tier"),
           "filled": filled[:20], "empty": empty[:24], "tris": sum(r[2] for r in filled),
           "materials": len({s.material for o in slots for s in o.material_slots if s.material}),
           "col": len(col.objects) if col is not None else 0}
    if lod:
        out["lod"] = lod
    if p.get("frames"):
        # the whole table goes to a JSON file (a step reply is about 1.5 KB); the reply carries the first rows
        full = _frames_table(coll, 10_000)
        d = os.path.join(ctx.out_dir, "kit_info")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{out['model']}_frames.json")
        import json

        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(full, f, ensure_ascii=False, indent=1)
        lim = int(U.num(p, "limit", 8, "kit.info", 0, 500))
        out = {k: out[k] for k in ("model", "kind", "tier", "tris")}
        out["frames_file"] = U.fwd(path)
        out["frames"] = dict(full["frames"], rows=full["frames"]["rows"][:lim])
        if full.get("ghost"):
            out["ghost_parts"] = full["ghost"]["total"]
    return out


@checkpointed
def export(ctx, p: dict) -> dict:
    """Export the kit model: DFF (frame-local spheres, embedded COL for vehicles), own-texture PNGs (kit.export op packs the TXD)."""
    from .export import export_model

    coll = U.clump(p.get("model"))
    out = str(U.need(p, "out", "kit.export"))
    host = getattr(ctx, "host", None)
    root = getattr(host, "out_root", None)
    if root:
        a, r = os.path.normcase(os.path.realpath(out)), os.path.normcase(os.path.realpath(root))
        if os.path.commonpath([a, r]) != r:
            raise SatkError("PROTECTED_PATH", f"kit.export: {out} is outside the satk work directory")
    pre = str(p.get("prelight") or "auto")
    if pre not in ("auto", "bake", "simple", "none"):
        raise U.bad("kit.export", f"prelight {pre!r}", hint="auto | bake | simple | none")
    res = export_model(coll, out, stem=p.get("stem"), col=str(p.get("col") or "auto"),
                       textures=bool(p.get("textures", True)), prelight=pre)
    path = os.path.join(out, "kit_export.json")
    import json

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    return {"manifest": U.fwd(path), "files": res["files"], "atomics": res["atomics"], "frames": len(res["frames"]),
            "textures": len(res["textures"]), "dropped": len(res["dropped_slots"]), "bsphere": res["bsphere"],
            "prelight": res.get("prelight") or None}


def _dff(fn):
    """``fn`` with DragonFF registered first (the studio world does not load it)."""
    import functools

    @functools.wraps(fn)
    def call(ctx, p: dict) -> dict:
        U.ensure_dff()
        return fn(ctx, p)

    for flag in ("readonly", "checkpoint"):
        if getattr(fn, flag, False):
            setattr(call, flag, True)
    return call


METHODS = {
    "kit.template": _dff(template),
    "kit.adopt": _dff(adopt_method),
    "kit.material_preset": _dff(material_preset),
    "kit.shade": _dff(shade),
    "kit.fill": _dff(fill),
    "kit.info": _dff(info),
    "kit.wheel": _dff(wheel_method),
    "kit.vlo": _dff(vlo_method),
    "kit.damage": _dff(damage_method),
    "kit.lod": _dff(lod_method),
    "kit.col": _dff(col_method),
    "kit.uv_region": _dff(uv_region_method),
    "kit.bake": _dff(bake_method),
    "kit.export": _dff(export),
}

# kit.blank / kit.blank_split (body blanks, blanks.py)
METHODS.update({"kit.blank": _dff(blank_method), "kit.blank_split": _dff(split_method)})
