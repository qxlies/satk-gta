# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``make_game_ready``: any mesh -> a GTA:SA model.

Steps (every one works on copies: the source objects and their materials are never changed):

1. **Source** - the open ``.blend`` (``objects`` or every visible mesh) or an imported ``.obj``/``.glb``/
   ``.gltf``/``.fbx``/``.ply``/``.stl``; meshes are evaluated (modifiers applied), joined into one object
   in model space and normalised: ``height``/``scale``, origin at the base centre (``base``), the bounding
   box centre (``center``) or as is (``keep``); doubles merged, loose parts and degenerate faces removed.
2. **Decimate** to the triangle ``budget``: a planar dissolve (1°, keeps material/UV seams), then
   ``COLLAPSE`` passes; the result is triangulated (DragonFF writes triangles).
3. **UV** - ``keep`` the source UVs, ``smart`` project, or ``box`` (cube projection, 32 px/m at
   ``tex_size``); ``auto`` keeps them when there are any.
4. **Textures** - materials with an image texture keep it (scaled to a power of two <= ``tex_size``),
   plain colours stay material colours; anything else (procedural, vertex colours, a re-unwrapped
   textured mesh) is **baked** (Cycles ``DIFFUSE``/``COLOR``, selected-to-active) into one
   ``tex_size`` texture named after the model on a fresh smart UV.
5. **Prelight** day/night into two corner colour attributes (DragonFF exports the first two as the
   prelight and the night colours): ``bake`` = Cycles AO with a ground plane, ``simple`` = normals only;
   day = AO x (sky + sun), night = AO x dark blue ambient.
6. **COL** - ``hull`` (convex hull of the decimated mesh), ``box`` (AABB primitive), ``mesh`` (the
   decimated mesh) or ``none``; surface ``surface`` (eSurfaceType); COL model name = model name.
7. **LOD** - a ``lod`` share of the triangles (``lod<name>``, same textures, own prelight); skipped for
   meshes under :data:`LOD_MIN_TRIS` triangles.
8. **Export** with DragonFF: ``<name>.dff`` (RW 3.6.0.3), ``lod<name>.dff``, ``<name>.col`` (COL3) and
   ``tex/<texture>.png`` + ``gameready.json`` into ``out``; the satk side packs the TXD
   (``satk.blender.gameready.finalize``).
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import common

RESULT = "SATK_gameready"
#: Below this many HD triangles a LOD model is pointless (it is skipped with LOD_SKIPPED).
LOD_MIN_TRIS = 48
#: Box UVs without a class: texel density (px/m); with ``asset_class`` the vanilla size-bucket density is used.
BOX_DENSITY = 32.0
DAY_ATTR, NIGHT_ATTR, AO_ATTR = "satk_day", "satk_night", "satk_ao"
#: Prelight light model (sRGB 0..1): sky ambient and sun for the day colours, ambient for the night.
#: Calibrated on 400 random gta3.img models: vanilla day prelight p50 82/255 (p10 36, p90 131),
#: night p50 26/255 (p90 64) - the engine adds its own ambient and directional light on top.
SKY_DAY = (0.40, 0.41, 0.44)
SUN_DAY = (0.32, 0.30, 0.26)
#: Night ambient: darker and WARM like vanilla (unlit night vertices average (30, 27, 23), 50 % of night-coloured
#: models R > B, 10 % cool); the old blue (0.16, 0.17, 0.24) glowed next to vanilla.
AMB_NIGHT = (0.15, 0.135, 0.115)
#: COL face light nibbles from the mean prelit brightness 0..255 (data/colgen/tex_surface.json fit).
FACE_LIGHT = {"day": (0.0809, 2.385), "night": (0.0889, 0.8793)}
#: A convex hull with more triangles is decimated to this many (round shapes give ~1 hull face per face).
HULL_MAX_TRIS = 256
SUN_DIR = Vector((-0.45, -0.55, 0.70)).normalized()
_IMPORTERS = {".obj": ("wm", "obj_import"), ".ply": ("wm", "ply_import"), ".stl": ("wm", "stl_import"),
              ".fbx": ("import_scene", "fbx"), ".glb": ("import_scene", "gltf"), ".gltf": ("import_scene", "gltf")}
_GEOMETRY = ("MESH", "CURVE", "SURFACE", "META", "FONT")


class GameReadyError(Exception):
    def __init__(self, code: str, msg: str, hint: str | None = None, did_you_mean=()):
        super().__init__(msg)
        self.code, self.msg, self.hint, self.did_you_mean = code, msg, hint, list(did_you_mean)


# --------------------------------------------------------------------------- small helpers


def _tris(me) -> int:
    return sum(len(p.vertices) - 2 for p in me.polygons)


def _select_only(objs, active=None) -> None:
    vl = bpy.context.view_layer
    for o in vl.objects:
        o.select_set(False)
    for o in objs:
        o.hide_set(False)
        o.select_set(True)
    vl.objects.active = active or (objs[0] if objs else None)


def _bounds(me) -> tuple[Vector, Vector]:
    import numpy as np

    n = len(me.vertices)
    if not n:
        return Vector((0, 0, 0)), Vector((0, 0, 0))
    co = np.empty(n * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(n, 3)
    return Vector(co.min(axis=0).tolist()), Vector(co.max(axis=0).tolist())


def _result_collection():
    return common.ensure_collection(RESULT)


def _apply_decimate(obj, **props) -> None:
    """Apply a Decimate modifier through the evaluated mesh (no operator context needed)."""
    m = obj.modifiers.new("satk_decimate", "DECIMATE")
    for k, v in props.items():
        setattr(m, k, v)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    new = bpy.data.meshes.new_from_object(obj.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    obj.modifiers.remove(m)
    old = obj.data
    obj.data = new
    new.name = old.name
    bpy.data.meshes.remove(old)


def _triangulate(me) -> None:
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()


def _texname(s: str, used: set[str]) -> str:
    """Texture name: ``[a-z0-9_]``, <= 31 characters (RW texture names), unique in ``used``."""
    base = re.sub(r"[^a-z0-9_]+", "_", os.path.splitext(s)[0].lower()).strip("_")[:31] or "tex"
    name, k = base, 1
    while name in used:
        k += 1
        suf = f"_{k}"
        name = base[:31 - len(suf)] + suf
    used.add(name)
    return name


def _pow2_floor(n: int) -> int:
    return 1 << max(0, int(n).bit_length() - 1)


# --------------------------------------------------------------------------- 1. source


def _import_file(path: str) -> list:
    ext = os.path.splitext(path)[1].lower()
    mod, fn = _IMPORTERS[ext]
    op = getattr(getattr(bpy.ops, mod), fn, None)
    try:
        ok = op is not None and op.poll()
    except RuntimeError:
        ok = False
    if not ok:
        raise GameReadyError("UNSUPPORTED", f"this Blender has no importer for {ext} (bpy.ops.{mod}.{fn})",
                             hint="export the mesh as .obj or .blend")
    before = set(bpy.data.objects)
    op(filepath=path)
    return [o for o in bpy.data.objects if o not in before]


def source_objects(src: str | None, objects: list[str] | None) -> list:
    """Geometry objects to make game-ready (from the open scene or an imported file)."""
    if src and not src.lower().endswith(".blend"):
        common.clean_scene()
        pool = _import_file(src)
    else:
        if src and os.path.normcase(os.path.abspath(bpy.data.filepath or "")) != os.path.normcase(os.path.abspath(src)):
            bpy.ops.wm.open_mainfile(filepath=src)
        pool = list(bpy.context.scene.objects)
    if objects:
        out = []
        for n in objects:
            o = bpy.data.objects.get(n)
            if o is None:
                import difflib

                names = [x.name for x in bpy.data.objects if x.type in _GEOMETRY]
                raise GameReadyError("NOT_FOUND", f"no object {n!r} in the scene", hint="pass mesh object names",
                                     did_you_mean=difflib.get_close_matches(n, names, 5, 0.5))
            out += [o] + [c for c in o.children_recursive if c.type in _GEOMETRY]
        objs = [o for o in dict.fromkeys(out) if o.type in _GEOMETRY]
    else:
        vl = bpy.context.view_layer
        objs = [o for o in pool if o.type in _GEOMETRY and not o.hide_render and o.name in vl.objects
                and not str(o.get("satk_gameready", "")) and not any(c.name == RESULT for c in o.users_collection)]
    if not objs:
        raise GameReadyError("NOT_FOUND", "no mesh to make game-ready" + (f" in {src}" if src else ""),
                             hint="the source needs at least one visible mesh object (or pass objects)")
    return objs


def join_source(objs: list, name: str) -> object:
    """One mesh object (world space, modifiers applied, materials of the object slots) in RESULT."""
    coll = _result_collection()
    dg = bpy.context.evaluated_depsgraph_get()
    parts = []
    for o in objs:
        me = bpy.data.meshes.new_from_object(o.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
        me.transform(o.matrix_world)
        if o.matrix_world.determinant() < 0:
            me.flip_normals()
        for i, slot in enumerate(o.material_slots):  # object-linked slots are not in the mesh copy
            if i < len(me.materials):
                me.materials[i] = slot.material
        if me.uv_layers:
            act = next((u for u in me.uv_layers if u.active_render), me.uv_layers[0])
            if "UVMap" in me.uv_layers and act.name != "UVMap":
                me.uv_layers["UVMap"].name = "UVMap_src"
            act.name = "UVMap"
        if not me.polygons:
            bpy.data.meshes.remove(me)
            continue
        p = bpy.data.objects.new(f"{name}_src", me)
        coll.objects.link(p)
        parts.append(p)
    if not parts:
        raise GameReadyError("NOT_FOUND", "the source objects have no faces", hint="a game model needs faces")
    if len(parts) > 1:
        _select_only(parts, parts[0])
        bpy.ops.object.join()
    hi = parts[0]
    hi.name = f"{name}_src"
    hi["satk_gameready"] = "source"
    hi["satk_gr_name"] = name
    return hi


def normalize(hi, *, height, scale: float, origin: str) -> dict:
    """Scale and origin of the joined copy; merge doubles, drop loose and degenerate geometry."""
    me = hi.data
    lo, up = _bounds(me)
    size = up - lo
    s = float(scale)
    if height:
        if size.z <= 1e-6:
            raise GameReadyError("BAD_PARAMS", "height: the mesh is flat (zero height)", hint="use scale instead")
        s = float(height) / size.z
    if origin == "base":
        t = Vector(((lo.x + up.x) / 2, (lo.y + up.y) / 2, lo.z))
    elif origin == "center":
        t = (lo + up) / 2
    else:
        t = Vector((0, 0, 0))
    me.transform(Matrix.Scale(s, 4) @ Matrix.Translation(-t))
    bm = bmesh.new()
    bm.from_mesh(me)
    diag = max((size * s).length, 1e-3)
    n0 = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=min(1e-4, diag * 1e-5))
    bmesh.ops.dissolve_degenerate(bm, dist=diag * 1e-6, edges=bm.edges[:])
    loose_e = [e for e in bm.edges if not e.link_faces]
    if loose_e:
        bmesh.ops.delete(bm, geom=loose_e, context="EDGES")
    loose_v = [v for v in bm.verts if not v.link_faces]
    if loose_v:
        bmesh.ops.delete(bm, geom=loose_v, context="VERTS")
    merged = n0 - len(bm.verts)
    bm.to_mesh(me)
    bm.free()
    me.update()
    lo, up = _bounds(me)
    return {"scale": round(s, 6), "origin": origin, "size": [round(v, 3) for v in (up - lo)], "merged_verts": merged}


# --------------------------------------------------------------------------- 2. decimate


def decimate(obj, budget: int) -> dict:
    """Triangulated mesh with at most ``budget`` triangles (planar dissolve, then collapse passes)."""
    me = obj.data
    _triangulate(me)
    n0 = _tris(obj.data)
    st = {"src_tris": n0}
    if n0 > budget:
        _apply_decimate(obj, decimate_type="DISSOLVE", angle_limit=math.radians(1.0),
                        delimit={"MATERIAL", "SEAM", "UV"})
        _triangulate(obj.data)
        st["planar_tris"] = _tris(obj.data)
        for _ in range(4):
            n = _tris(obj.data)
            if n <= budget:
                break
            _apply_decimate(obj, decimate_type="COLLAPSE", ratio=max(0.001, budget / n * 0.995),
                            use_collapse_triangulate=True)
            _triangulate(obj.data)
    st["tris"] = _tris(obj.data)
    st["verts"] = len(obj.data.vertices)
    return st


# --------------------------------------------------------------------------- 3. UV


def _in_edit(obj, fn) -> None:
    _select_only([obj], obj)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bpy.ops.mesh.select_all(action="SELECT")
        fn()
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def class_spec() -> dict:
    """The ``game_ready_class`` of the job request (``blender.game_ready --asset-class``), ``{}`` without one."""
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    for a in argv:
        if a.lower().endswith(".json") and os.path.isfile(a):
            try:
                with open(a, encoding="utf-8") as f:
                    req = json.load(f)
            except (OSError, ValueError):
                return {}
            return dict((req.get("plan") or {}).get("game_ready_class") or {})
    return {}


def texel_target(spec: dict, size_m: float) -> float:
    """Texel density (px/m) of the class for a model whose largest side is ``size_m``."""
    if not spec:
        return BOX_DENSITY
    base = spec.get("texel_px_m")
    if not base:
        rows = spec.get("texel_by_size") or []
        base = rows[-1]["px_m"] if rows else BOX_DENSITY
        for r in rows:
            if size_m <= float(r["max_m"]):
                base = r["px_m"]
                break
    return float(base) * float(spec.get("tier_factor") or 1.0)


def texel_density(me, tex_size: int) -> float:
    """Achieved texel density (px/m) of UV layer 0 for a ``tex_size`` square texture."""
    if not me.uv_layers:
        return 0.0
    me.calc_loop_triangles()
    uvl = me.uv_layers[0].data
    ua = wa = 0.0
    for t in me.loop_triangles:
        a, b, c = (uvl[i].uv for i in t.loops)
        ua += abs((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)) / 2
        wa += t.area
    return math.sqrt(ua * tex_size * tex_size / wa) if wa > 1e-12 else 0.0


def unwrap(obj, mode: str, tex_size: int, density: float = BOX_DENSITY) -> str:
    """New single UV layer by ``smart`` projection or ``box`` (cube) projection; returns the mode."""
    me = obj.data
    for uv in list(me.uv_layers):
        me.uv_layers.remove(uv)
    me.uv_layers.new(name="UVMap")
    if mode == "box":
        _in_edit(obj, lambda: bpy.ops.uv.cube_project(cube_size=tex_size / density, correct_aspect=True,
                                                       scale_to_bounds=False))
    else:
        _in_edit(obj, lambda: (bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.02),
                               bpy.ops.uv.pack_islands(margin=0.01)))
    return mode


# --------------------------------------------------------------------------- 4. materials / textures


def _principled(mat):
    if mat is None or not mat.node_tree:
        return None, None
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None) or \
        next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is not None and out.inputs["Surface"].is_linked:
        n = out.inputs["Surface"].links[0].from_node
        if n.type == "BSDF_PRINCIPLED":
            return nt, n
    return nt, next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)


def classify(mat) -> tuple[str, object]:
    """``("image", image)`` / ``("color", rgba)`` / ``("complex", None)`` for a source material."""
    if mat is None:
        return "color", (1.0, 1.0, 1.0, 1.0)
    nt, bsdf = _principled(mat)
    if bsdf is None:
        if nt is not None and any(n.type == "TEX_IMAGE" for n in nt.nodes):
            return "complex", None
        c = mat.diffuse_color
        return "color", (c[0], c[1], c[2], c[3])
    base = bsdf.inputs["Base Color"]
    if not base.is_linked:
        c = base.default_value
        return "color", (c[0], c[1], c[2], 1.0)
    src = base.links[0].from_node
    if src.type == "TEX_IMAGE" and src.image is not None and src.image.size[0] > 0:
        return "image", src.image
    return "complex", None


def _lin2srgb(v: float) -> float:
    v = max(0.0, min(1.0, v))
    return v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


def _new_material(name: str, *, image=None, rgba=(1.0, 1.0, 1.0, 1.0), texname: str | None = None):
    """A Principled material DragonFF exports as intended: ``image`` -> base colour texture named
    ``texname`` with a white material colour; else the sRGB material colour ``rgba``."""
    mat = bpy.data.materials.new(name)
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    # DragonFF writes the Base Color default value x 255 as the RW material colour (sRGB bytes)
    bsdf.inputs["Base Color"].default_value = rgba if image is None else (1.0, 1.0, 1.0, 1.0)
    mat.diffuse_color = bsdf.inputs["Base Color"].default_value
    if image is not None:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = image
        tex.label = texname or image.name
        tex.location = (bsdf.location[0] - 320, bsdf.location[1])
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        nt.nodes.active = tex
    mat["satk_gameready"] = "material"
    return mat


def _image_rgba(img):
    import numpy as np

    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    return px, w, h


def _export_image(src, texname: str, w: int, h: int, png: str):
    """A packed copy of ``src`` scaled to ``w`` x ``h`` named ``texname``, saved as ``png``."""
    tmp = src.copy()
    try:
        if tuple(tmp.size) != (w, h):
            tmp.scale(w, h)
        px, _w, _h = _image_rgba(tmp)
    finally:
        bpy.data.images.remove(tmp)
    img = bpy.data.images.new(texname, w, h, alpha=True)
    img.pixels.foreach_set(px)
    _save_png(img, png)
    return img, bool((px[3::4] < 0.999).any())


def _save_png(img, png: str) -> None:
    os.makedirs(os.path.dirname(png), exist_ok=True)
    img.filepath_raw = png
    img.file_format = "PNG"
    img.save()
    img.pack()


def _render_only(keep: list):
    """Hide every other object from renders/bakes; returns the restore function."""
    saved = {o.name: o.hide_render for o in bpy.data.objects}
    keep_names = {o.name for o in keep}
    for o in bpy.data.objects:
        o.hide_render = o.name not in keep_names
    for o in keep:
        o.hide_render = False

    def restore():
        for o in bpy.data.objects:
            if o.name in saved:
                o.hide_render = saved[o.name]
    return restore


def _cycles(scene, samples: int) -> None:
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    try:
        scene.cycles.use_denoising = False
    except AttributeError:
        pass


def bake_diffuse(hi, lo, name: str, size: int, png: str, diag: float):
    """Cycles DIFFUSE colour of ``hi`` (its own materials and UVs) onto ``lo``'s UVs: one texture."""
    scene = bpy.context.scene
    img = bpy.data.images.new(name, size, size, alpha=True)
    mat = _new_material(name, image=img, texname=name)
    lo.data.materials.clear()
    lo.data.materials.append(mat)
    for p in lo.data.polygons:
        p.material_index = 0
    restore = _render_only([hi, lo])
    engine = scene.render.engine
    try:
        _cycles(scene, 4)
        b = scene.render.bake
        b.use_selected_to_active = True
        b.cage_extrusion = max(0.002, 0.02 * diag)
        b.max_ray_distance = max(0.01, 0.1 * diag)
        b.use_pass_direct = b.use_pass_indirect = False
        b.use_pass_color = True
        b.target = "IMAGE_TEXTURES"
        b.margin = 4
        _select_only([hi, lo], lo)
        bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, use_clear=True, margin=4)
    finally:
        restore()
        scene.render.engine = engine
    # texels outside the UV islands come out transparent: the texture is opaque (DXT1 in a TXD)
    px, _w, _h = _image_rgba(img)
    px[3::4] = 1.0
    img.pixels.foreach_set(px)
    _save_png(img, png)
    return mat, img


def textures(hi, lo, args: dict, out: str, uv: str, warn: list[str]) -> dict:
    """Materials of ``lo`` replaced by export-ready copies; PNGs into ``out/tex``. ``uv`` = how the UVs
    of ``lo`` were made (keep/smart/box). Returns ``{"baked", "uv", "materials", "textures": rows}``
    with rows ``{name, png, w, h, alpha}``."""
    name, size = args["name"], int(args["tex_size"])
    uv_new = uv in ("smart", "box")
    mats = list(lo.data.materials) or [None]
    kinds = [classify(m) for m in mats]
    complex_ = any(k == "complex" for k, _ in kinds)
    images = any(k == "image" for k, _ in kinds)
    mode = args["bake"]
    need = mode == "always" or (mode == "auto" and (complex_ or (uv_new and images)))
    lo_b, up_b = _bounds(lo.data)
    diag = max((up_b - lo_b).length, 1e-3)
    tex_dir = os.path.join(out, "tex")
    rows: list[dict] = []
    if need:
        # baking needs non-overlapping UVs in 0..1: a fresh smart projection unless uv=keep was asked for
        if args["uv"] != "keep" and uv != "smart":
            uv = unwrap(lo, "smart", size)
        png = os.path.join(tex_dir, f"{name}.png")
        bake_diffuse(hi, lo, name, size, png, diag)
        rows.append({"name": name, "png": common.fwd(png), "w": size, "h": size, "alpha": False})
        return {"baked": True, "uv": uv, "textures": rows, "materials": 1}
    if complex_:
        warn.append("TEX_FLAT: material(s) with procedural/vertex colour shading exported as plain colours (bake=never)")
    if uv_new and images:
        warn.append("TEX_UV_MISMATCH: image textures on new UVs without baking (bake=never)")
    used: set[str] = set()
    done: dict[str, tuple] = {}
    new_mats = []
    for i, (m, (kind, val)) in enumerate(zip(mats, kinds)):
        if kind == "image":
            img = val
            key = img.name
            if key not in done:
                tn = _texname(img.name, used)
                w0, h0 = img.size
                w, h = min(size, max(4, _pow2_floor(w0))), min(size, max(4, _pow2_floor(h0)))
                png = os.path.join(tex_dir, f"{tn}.png")
                new_img, alpha = _export_image(img, tn, w, h, png)
                done[key] = (new_img, tn)
                rows.append({"name": tn, "png": common.fwd(png), "w": w, "h": h, "alpha": alpha,
                             "src": [w0, h0]})
            new_img, tn = done[key]
            new_mats.append(_new_material(f"{name}_{i}", image=new_img, texname=tn))
        else:
            rgba = val if kind == "color" else (1.0, 1.0, 1.0, 1.0)
            if m is not None and kind != "color":
                c = m.diffuse_color
                rgba = (c[0], c[1], c[2], 1.0)
            srgb = tuple(_lin2srgb(v) for v in rgba[:3]) + (1.0,)
            new_mats.append(_new_material(f"{name}_{i}", rgba=srgb))
    lo.data.materials.clear()
    for m in new_mats:
        lo.data.materials.append(m)
    return {"baked": False, "uv": uv, "textures": rows, "materials": len(new_mats)}


# --------------------------------------------------------------------------- 5. prelight


def _ground(z: float, size: float, cx: float = 0.0, cy: float = 0.0):
    me = bpy.data.meshes.new("satk_gr_ground")
    s = size
    me.from_pydata([(cx - s, cy - s, z), (cx + s, cy - s, z), (cx + s, cy + s, z), (cx - s, cy + s, z)], [],
                   [(0, 1, 2, 3)])
    o = bpy.data.objects.new("satk_gr_ground", me)
    bpy.context.scene.collection.objects.link(o)
    return o


def prelight(obj, mode: str, ao=None) -> dict:
    """Day/night prelight in ``satk_day``/``satk_night`` (CORNER, byte colours, the only colour attributes).

    ``ao`` = ambient occlusion per loop (0 covered .. 1 open) from the caller (``kit.export`` ray casts it, so a
    session never starts a Cycles bake); without it ``bake`` bakes with Cycles."""
    import numpy as np

    me = obj.data
    for a in list(me.color_attributes):
        me.color_attributes.remove(a)
    if mode == "none":
        obj.dff.day_cols = obj.dff.night_cols = False
        return {"prelight": "none"}
    n = len(me.loops)
    given = ao is not None
    if given:
        ao = np.asarray(ao, dtype=np.float32)
        if ao.shape != (n,):
            raise GameReadyError("BAD_PARAMS", f"prelight: ao has {ao.shape} values for {n} loops")
    else:
        ao = np.ones(n, dtype=np.float32)
    if mode == "bake" and not given:
        scene = bpy.context.scene
        lo_b, up_b = _bounds(me)
        diag = max((up_b - lo_b).length, 1e-3)
        ca = me.color_attributes.new(AO_ATTR, "BYTE_COLOR", "CORNER")
        me.color_attributes.active_color = ca
        # the ground sits under the model in WORLD space (a kit part hangs on a frame that may be moved)
        mw = obj.matrix_world
        corners = [mw @ Vector((x, y, z)) for x in (lo_b.x, up_b.x) for y in (lo_b.y, up_b.y) for z in (lo_b.z, up_b.z)]
        ground = _ground(min(c.z for c in corners) - diag * 0.002, diag * 4,
                         sum(c.x for c in corners) / 8, sum(c.y for c in corners) / 8)
        if scene.world is None:
            scene.world = bpy.data.worlds.new("satk_world")
        dist = scene.world.light_settings.distance
        scene.world.light_settings.distance = max(0.05, diag * 0.5)
        restore = _render_only([obj, ground])
        engine = scene.render.engine
        try:
            _cycles(scene, 64)
            b = scene.render.bake
            b.use_selected_to_active = False
            b.target = "VERTEX_COLORS"
            b.margin = 0
            _select_only([obj], obj)
            bpy.ops.object.bake(type="AO")
        finally:
            restore()
            scene.render.engine = engine
            scene.world.light_settings.distance = dist
            gm = ground.data
            bpy.data.objects.remove(ground, do_unlink=True)
            bpy.data.meshes.remove(gm)
        buf = np.empty(n * 4, dtype=np.float32)
        ca.data.foreach_get("color", buf)
        ao = buf.reshape(n, 4)[:, 0].copy()
        me.color_attributes.remove(ca)
    nrm = np.empty(n * 3, dtype=np.float32)
    me.corner_normals.foreach_get("vector", nrm)
    nrm = nrm.reshape(n, 3)
    lam = np.clip(nrm @ np.array(SUN_DIR, dtype=np.float32), 0.0, 1.0)
    sky = 0.75 + 0.25 * np.clip(nrm[:, 2], -1.0, 1.0)
    aof = (0.4 + 0.6 * np.clip(ao, 0.0, 1.0))[:, None]
    day = np.clip(aof * (np.array(SKY_DAY) * sky[:, None] + np.array(SUN_DAY) * lam[:, None]), 0.0, 1.0)
    night = np.clip(aof * np.array(AMB_NIGHT) * sky[:, None], 0.0, 1.0)
    for attr, rgb in ((DAY_ATTR, day), (NIGHT_ATTR, night)):
        a = me.color_attributes.new(attr, "BYTE_COLOR", "CORNER")
        rgba = np.concatenate([rgb, np.ones((n, 1))], axis=1).astype(np.float32)
        a.data.foreach_set("color_srgb", rgba.ravel())
    me.color_attributes.active_color = me.color_attributes[DAY_ATTR]
    obj.dff.day_cols = obj.dff.night_cols = True
    return {"prelight": mode, "ao_mean": round(float(ao.mean()), 3), "day_mean": round(float(day.mean()), 3),
            "night_mean": round(float(night.mean()), 3)}


# --------------------------------------------------------------------------- 6. COL


def face_light(day: float | None, night: float | None) -> tuple[int, int]:
    """Day/night COL light nibbles from mean prelit colours (0..1); never 0 by day (0 = ambient only)."""
    a, b = FACE_LIGHT["day"]
    d = 15 if day is None else int(min(15, max(1, round(a * day * 255 + b))))
    a, b = FACE_LIGHT["night"]
    n = 3 if night is None else int(min(15, max(0, round(a * night * 255 + b))))
    return d, n


def _col_material(surface: int, light: tuple[int, int] = (15, 3)):
    m = bpy.data.materials.new(f"satk_col_{surface}")
    m.dff.col_mat_index = surface
    m.dff.col_day_light, m.dff.col_night_light = int(light[0]), int(light[1])
    m["satk_gameready"] = "col"
    return m


def make_col(lo, kind: str, surface: int, name: str, warn: list[str], light: tuple[int, int] = (15, 3)) -> list:
    """COL objects (``dff.type = 'COL'``) in model space."""
    if kind == "none":
        return []
    coll = _result_collection()
    lo_b, up_b = _bounds(lo.data)
    if kind == "hull":
        bm = bmesh.new()
        bm.from_mesh(lo.data)
        ok = False
        try:
            res = bmesh.ops.convex_hull(bm, input=bm.verts[:], use_existing_faces=False)
            junk = set(res.get("geom_interior", [])) | set(res.get("geom_unused", []))
            bmesh.ops.delete(bm, geom=[g for g in junk if isinstance(g, bmesh.types.BMVert)], context="VERTS")
            bmesh.ops.triangulate(bm, faces=bm.faces[:])
            ok = len(bm.faces) >= 4
            if ok:
                me = bpy.data.meshes.new(f"{name}_col")
                bm.to_mesh(me)
        except (RuntimeError, ValueError):  # degenerate input (coplanar or too few points)
            ok = False
        finally:
            bm.free()
        if ok and len(me.polygons) > HULL_MAX_TRIS:
            tmp = bpy.data.objects.new(f"{name}_hull_tmp", me)
            coll.objects.link(tmp)
            decimate(tmp, HULL_MAX_TRIS)
            me = tmp.data
            bpy.data.objects.remove(tmp, do_unlink=True)
        if not ok:
            warn.append("COL_HULL_FLAT: the mesh is flat, the collision is a box")
            kind = "box"
    if kind == "box":
        o = bpy.data.objects.new(f"{name}_colbox", None)
        o.empty_display_type = "CUBE"
        c = (lo_b + up_b) / 2
        half = Vector((max((up_b.x - lo_b.x) / 2, 0.025), max((up_b.y - lo_b.y) / 2, 0.025),
                       max((up_b.z - lo_b.z) / 2, 0.025)))
        o.location = c
        o.scale = half
        o.dff.type = "COL"
        o.dff.col_material = surface
        o.dff.col_day_light, o.dff.col_night_light = int(light[0]), int(light[1])
    else:
        if kind == "mesh":
            me = lo.data.copy()
            me.name = f"{name}_col"
            for uv in list(me.uv_layers):
                me.uv_layers.remove(uv)
            for a in list(me.color_attributes):
                me.color_attributes.remove(a)
        me.materials.clear()
        me.materials.append(_col_material(surface, light))
        for p in me.polygons:
            p.material_index = 0
        o = bpy.data.objects.new(f"{name}_col", me)
        o.dff.type = "COL"
    coll.objects.link(o)
    o["satk_gameready"] = "col"
    o["satk_gr_name"] = name
    o["satk_col_kind"] = kind
    o.hide_render = True
    return [o]


def _export_col(name: str, objs: list, path: str) -> None:
    ce = common.dff_module("ops.col_exporter")
    other = bpy.data.collections.get(name)
    if other is not None:
        other.name = name + "_satk_tmp_renamed"
    tmp = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(tmp)
    try:
        for o in objs:
            tmp.objects.link(o)
        bpy.context.view_layer.update()
        ce.export_col({"file_name": path, "version": 3, "collection": tmp, "apply_transformations": True,
                       "only_selected": False, "clean_mesh": True, "export_face_groups": False})
    finally:
        for o in list(tmp.objects):
            tmp.objects.unlink(o)
        bpy.data.collections.remove(tmp)
        if other is not None:
            other.name = name


# --------------------------------------------------------------------------- 8. export


def _export_dff(obj, path: str) -> None:
    de = common.dff_module("ops.dff_exporter")
    obj.dff.type = "OBJ"
    # corner normals split the vertices DragonFF writes where faces meet at an angle, so every face
    # keeps its own prelight colours (DragonFF merges corners by position, normal and UV only)
    obj.dff.export_split_normals = True
    _select_only([obj], obj)
    de.export_dff({
        "file_name": path, "directory": os.path.dirname(path), "selected": True, "mass_export": False,
        "preserve_positions": True, "preserve_rotations": True, "version": 0x36003,
        "export_coll": False, "coll_ext_type": 0, "apply_coll_trans": True,
        "export_frame_names": True, "exclude_geo_faces": False, "from_outliner": False,
    })
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise GameReadyError("EXTERNAL_TOOL", f"DragonFF wrote no {os.path.basename(path)}")
    from .exporter import fix_bspheres

    fix_bspheres(path)


# --------------------------------------------------------------------------- main


def _drop_previous(name: str, keep: list) -> int:
    """Remove the results of an earlier run for ``name`` (add-on re-runs) except ``keep``."""
    keep_names = {o.name for o in keep}
    old = [o for o in bpy.data.objects if o.get("satk_gr_name") == name and o.name not in keep_names]
    for o in old:
        me = o.data if o.type == "MESH" else None
        bpy.data.objects.remove(o, do_unlink=True)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)
    return len(old)


def make_game_ready(args: dict, out: str, *, hide_sources: str = "all") -> dict:
    """Run every step; ``args`` = normalised ``game_ready`` arguments, ``out`` = output folder.
    ``hide_sources``: ``all`` (renders and viewport: headless jobs), ``viewport`` (the add-on) or ``none``.

    Returns ``{"manifest", "warnings", "objects": {"hd", "lod", "col"}}``; the manifest is also
    written to ``out/gameready.json``.
    """
    t0 = time.perf_counter()
    name = args["name"]
    warn: list[str] = []
    timing: dict[str, float] = {}

    def lap(k: str, t: float) -> float:
        now = time.perf_counter()
        timing[k] = round(now - t, 2)
        return now

    src_objs = source_objects(args.get("src"), args.get("objects"))
    _drop_previous(name, src_objs)
    t = lap("load_s", t0)
    stats: dict = {"source_objects": len(src_objs)}
    hi = join_source(src_objs, name)
    stats.update(normalize(hi, height=args.get("height"), scale=args.get("scale", 1.0), origin=args["origin"]))
    if hide_sources != "none":
        for o in src_objs:
            if hide_sources == "all":
                o.hide_render = True
            o.hide_set(True)
    t = lap("normalize_s", t)
    # the HD model: a copy of the joined source
    lo = hi.copy()
    lo.data = hi.data.copy()
    lo.name = name
    lo.data.name = name
    _result_collection().objects.link(lo)
    lo["satk_gameready"] = "hd"
    lo["satk_gr_name"] = name
    stats.update(decimate(lo, int(args["budget"])))
    t = lap("decimate_s", t)
    uv = args["uv"]
    has_uv = len(lo.data.uv_layers) > 0
    if uv == "auto":
        uv = "keep" if has_uv else "smart"
    if uv == "keep" and not has_uv:
        warn.append("UV_MISSING: the source has no UVs; smart UV projection used")
        uv = "smart"
    uv_new = uv in ("smart", "box")
    spec = class_spec()
    lo_b, up_b = _bounds(lo.data)
    size_m = max((up_b - lo_b).x, (up_b - lo_b).y, (up_b - lo_b).z)
    density = texel_target(spec, size_m)
    if spec:
        stats["asset_class"] = spec.get("name")
        stats["texel_target_px_m"] = round(density, 1)
    if uv_new:
        unwrap(lo, uv, int(args["tex_size"]), density)
    else:  # keep only the render UV layer (DragonFF exports up to two)
        for u in [u for u in lo.data.uv_layers if u.name != "UVMap"]:
            lo.data.uv_layers.remove(u)
    t = lap("uv_s", t)
    tx = textures(hi, lo, args, out, uv, warn)
    stats.update(uv=tx["uv"], baked=tx["baked"], materials=tx["materials"], textures=len(tx["textures"]))
    t = lap("textures_s", t)
    stats.update(prelight(lo, args["prelight"]))
    if spec.get("night") == "none" and lo.data.color_attributes:
        lo.dff.night_cols = False  # vanilla pickups: no night colours
    if spec:
        stats["texel_px_m"] = round(texel_density(lo.data, int(args["tex_size"])), 1)
    t = lap("prelight_s", t)
    light = face_light(stats.get("day_mean"), stats.get("night_mean") if lo.dff.night_cols else None)
    stats["col_light"] = list(light)
    cols = make_col(lo, args["col"], int(args["surface"]), name, warn, light)
    stats["col"] = str(cols[0]["satk_col_kind"]) if cols else "none"
    if cols and cols[0].type == "MESH":
        stats["col_tris"] = _tris(cols[0].data)
    t = lap("col_s", t)
    lod_obj = None
    lod = None
    if args["lod"] > 0:
        if stats["tris"] < LOD_MIN_TRIS:
            warn.append(f"LOD_SKIPPED: {stats['tris']} triangles are below {LOD_MIN_TRIS}, no LOD model needed")
        else:
            from satk.blender.contract import lod_name

            lod = lod_name(name)
            lod_obj = lo.copy()
            lod_obj.data = lo.data.copy()
            lod_obj.name = lod
            lod_obj.data.name = lod
            _result_collection().objects.link(lod_obj)
            lod_obj["satk_gameready"] = "lod"
            _apply_decimate(lod_obj, decimate_type="COLLAPSE", ratio=float(args["lod"]), use_collapse_triangulate=True)
            _triangulate(lod_obj.data)
            prelight(lod_obj, args["prelight"])
            stats["lod_tris"] = _tris(lod_obj.data)
    t = lap("lod_s", t)
    os.makedirs(out, exist_ok=True)
    files: dict = {"dff": common.fwd(os.path.join(out, f"{name}.dff"))}
    _export_dff(lo, files["dff"])
    if lod_obj is not None:
        files["lod_dff"] = common.fwd(os.path.join(out, f"{lod}.dff"))
        _export_dff(lod_obj, files["lod_dff"])
    if cols:
        files["col"] = common.fwd(os.path.join(out, f"{name}.col"))
        _export_col(name, cols, files["col"])
    lap("export_s", t)
    # tidy the scene: the joined source copy is no longer needed; LOD and COL stay hidden
    hme = hi.data
    bpy.data.objects.remove(hi, do_unlink=True)
    bpy.data.meshes.remove(hme)
    for o in ([lod_obj] if lod_obj is not None else []) + cols:
        o.hide_render = True
        o.hide_set(True)
    stats["timing"] = timing
    manifest = {"name": name, "lod": lod, "src": common.fwd(args["src"]) if args.get("src") else None,
                "args": {k: v for k, v in args.items() if k not in ("src", "out", "save", "render")},
                "files": files, "textures": tx["textures"], "stats": stats, "warnings": warn}
    with open(os.path.join(out, "gameready.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return {"manifest": manifest, "warnings": warn, "objects": {"hd": lo, "lod": lod_obj, "col": cols}}
