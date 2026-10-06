# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``modifier.*``: a whitelisted, non-destructive modifier stack (it stays live until export: the stats and
DragonFF both read the evaluated mesh).

Types and their parameters (angles in degrees, objects by name):

* ``MIRROR``: axis (x|y|z or a list), bisect, clip, merge, merge_threshold, mirror_object
* ``SUBSURF``: levels, render_levels, simple, use_creases, quality, boundary_smooth (all|keep_corners)
* ``BEVEL``: width, segments, limit (none|angle|weight), angle, profile, harden_normals, clamp
* ``SOLIDIFY``: thickness, offset, even, rim, flip
* ``SHRINKWRAP``: target, method (nearest|project|nearest_vertex|target_normal), mode, offset
* ``WEIGHTED_NORMAL``: mode (face_area|corner_angle|face_area_with_angle), weight, threshold, keep_sharp
* ``DECIMATE``: kind (planar|collapse|unsubdiv), ratio, angle, iterations
* ``TRIANGULATE``: quad (beauty|fixed|shortest|longest), ngon (beauty|clip), min_vertices, keep_normals
* ``ARRAY``: count, relative [x,y,z], constant [x,y,z], merge, merge_threshold, fit_length, curve
* ``CURVE``: curve, axis (x|y|z|-x|-y|-z)
* ``WELD``: threshold
* ``SMOOTH_BY_ANGLE``: angle, ignore_sharp (Blender's 'Smooth by Angle' node group)
"""

from __future__ import annotations

import math

import bpy

from satk.core.errors import SatkError
from satk.studio.core import readonly

from . import _util as U


def _axes(v, method: str) -> list[bool]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not v or not all(a in ("x", "y", "z") for a in v):
        raise U.bad(method, "'axis' must be x, y, z or a list of them")
    return [a in v for a in "xyz"]


def _target(ctx, v, method: str, types=None):
    o = ctx.obj(v)
    if types and o.type not in types:
        raise U.bad(method, f"{o.name} must be a {'/'.join(types)}")
    return o


def _set_mirror(ctx, m, p, M):
    if "axis" in p:
        m.use_axis = _axes(p["axis"], M)
    if "bisect" in p:
        m.use_bisect_axis = _axes(p["bisect"], M) if not isinstance(p["bisect"], bool) else \
            [p["bisect"] and a for a in m.use_axis]
    if "clip" in p:
        m.use_clip = U.flag(p, "clip", M)
    if "merge" in p:
        m.use_mirror_merge = U.flag(p, "merge", M)
    if "merge_threshold" in p:
        m.merge_threshold = U.num(p, "merge_threshold", M, lo=0.0)
    if "mirror_object" in p:
        m.mirror_object = _target(ctx, p["mirror_object"], M) if p["mirror_object"] else None


def _set_subsurf(ctx, m, p, M):
    if "levels" in p:
        m.levels = U.integer(p, "levels", M, lo=0, hi=4)
        if "render_levels" not in p:
            m.render_levels = m.levels
    if "render_levels" in p:
        m.render_levels = U.integer(p, "render_levels", M, lo=0, hi=4)
    if "simple" in p:
        m.subdivision_type = "SIMPLE" if U.flag(p, "simple", M) else "CATMULL_CLARK"
    if "use_creases" in p:
        m.use_creases = U.flag(p, "use_creases", M)
    if "quality" in p:
        m.quality = U.integer(p, "quality", M, lo=1, hi=10)
    if "boundary_smooth" in p:
        m.boundary_smooth = {"all": "ALL", "keep_corners": "PRESERVE_CORNERS"}[
            U.text(p, "boundary_smooth", M, choices=("all", "keep_corners"))]


def _set_bevel(ctx, m, p, M):
    if "width" in p:
        m.width = U.num(p, "width", M, lo=0.0)
    if "segments" in p:
        m.segments = U.integer(p, "segments", M, lo=1, hi=32)
    if "limit" in p:
        m.limit_method = U.text(p, "limit", M, choices=("none", "angle", "weight")).upper()
    if "angle" in p:
        m.angle_limit = U.deg(p, "angle", M, 30.0)
    if "profile" in p:
        m.profile = U.num(p, "profile", M, lo=0.0, hi=1.0)
    if "harden_normals" in p:
        m.harden_normals = U.flag(p, "harden_normals", M)
    if "clamp" in p:
        m.use_clamp_overlap = U.flag(p, "clamp", M)


def _set_solidify(ctx, m, p, M):
    if "thickness" in p:
        m.thickness = U.num(p, "thickness", M)
    if "offset" in p:
        m.offset = U.num(p, "offset", M, lo=-1.0, hi=1.0)
    if "even" in p:
        m.use_even_offset = U.flag(p, "even", M)
    if "rim" in p:
        m.use_rim = U.flag(p, "rim", M)
    if "flip" in p:
        m.use_flip_normals = U.flag(p, "flip", M)


def _set_shrinkwrap(ctx, m, p, M):
    if "target" in p:
        m.target = _target(ctx, p["target"], M, ("MESH",))
    if "method" in p:
        m.wrap_method = {"nearest": "NEAREST_SURFACEPOINT", "project": "PROJECT", "nearest_vertex": "NEAREST_VERTEX",
                         "target_normal": "TARGET_PROJECT"}[
            U.text(p, "method", M, choices=("nearest", "project", "nearest_vertex", "target_normal"))]
    if "mode" in p:
        m.wrap_mode = U.text(p, "mode", M, choices=("on_surface", "inside", "outside", "outside_surface",
                                                    "above_surface")).upper()
    if "offset" in p:
        m.offset = U.num(p, "offset", M)


def _set_wn(ctx, m, p, M):
    if "mode" in p:
        m.mode = U.text(p, "mode", M, choices=("face_area", "corner_angle", "face_area_with_angle")).upper()
    if "weight" in p:
        m.weight = U.integer(p, "weight", M, lo=1, hi=100)
    if "threshold" in p:
        m.thresh = U.num(p, "threshold", M, lo=0.0, hi=10.0)
    if "keep_sharp" in p:
        m.keep_sharp = U.flag(p, "keep_sharp", M)


def _set_decimate(ctx, m, p, M):
    if "kind" in p:
        m.decimate_type = {"planar": "DISSOLVE", "collapse": "COLLAPSE", "unsubdiv": "UNSUBDIV"}[
            U.text(p, "kind", M, choices=("planar", "collapse", "unsubdiv"))]
    if "ratio" in p:
        m.ratio = U.num(p, "ratio", M, lo=0.0, hi=1.0)
    if "angle" in p:
        m.angle_limit = U.deg(p, "angle", M, 1.0)
    if "iterations" in p:
        m.iterations = U.integer(p, "iterations", M, lo=0, hi=32)
    if m.decimate_type == "COLLAPSE":
        ctx.warn("STYLE: collapse decimation scatters triangles; author at the target density "
                 "(planar decimation is safe)")


def _set_triangulate(ctx, m, p, M):
    if "quad" in p:
        m.quad_method = {"beauty": "BEAUTY", "fixed": "FIXED", "shortest": "SHORTEST_DIAGONAL",
                         "longest": "LONGEST_DIAGONAL"}[
            U.text(p, "quad", M, choices=("beauty", "fixed", "shortest", "longest"))]
    if "ngon" in p:
        m.ngon_method = U.text(p, "ngon", M, choices=("beauty", "clip")).upper()
    if "min_vertices" in p:
        m.min_vertices = U.integer(p, "min_vertices", M, lo=4, hi=1000)
    if "keep_normals" in p and hasattr(m, "keep_custom_normals"):
        m.keep_custom_normals = U.flag(p, "keep_normals", M)


def _set_array(ctx, m, p, M):
    if "count" in p:
        m.fit_type = "FIXED_COUNT"
        m.count = U.integer(p, "count", M, lo=1, hi=1000)
    if "relative" in p:
        m.use_relative_offset = True
        m.relative_offset_displace = U.vec(p, "relative", M)
    if "constant" in p:
        m.use_constant_offset = True
        m.constant_offset_displace = U.vec(p, "constant", M)
        if "relative" not in p:
            m.use_relative_offset = False
    if "merge" in p:
        m.use_merge_vertices = U.flag(p, "merge", M)
    if "merge_threshold" in p:
        m.merge_threshold = U.num(p, "merge_threshold", M, lo=0.0)
    if "fit_length" in p:
        m.fit_type = "FIT_LENGTH"
        m.fit_length = U.num(p, "fit_length", M, lo=0.0)
    if "curve" in p:
        m.fit_type = "FIT_CURVE"
        m.curve = _target(ctx, p["curve"], M, ("CURVE",))


def _set_curve(ctx, m, p, M):
    if "curve" in p:
        m.object = _target(ctx, p["curve"], M, ("CURVE",))
    if "axis" in p:
        a = U.text(p, "axis", M, choices=("x", "y", "z", "-x", "-y", "-z"))
        m.deform_axis = ("NEG_" + a[1:].upper()) if a.startswith("-") else ("POS_" + a.upper())


def _set_weld(ctx, m, p, M):
    if "threshold" in p:
        m.merge_threshold = U.num(p, "threshold", M, lo=0.0)


def _set_sba(ctx, m, p, M):
    ng = m.node_group
    for item in getattr(getattr(ng, "interface", None), "items_tree", []):
        if getattr(item, "in_out", None) != "INPUT" or not hasattr(item, "identifier"):
            continue
        if item.name == "Angle" and "angle" in p:
            m[item.identifier] = U.deg(p, "angle", M, 30.0)
        elif item.name == "Ignore Sharpness" and "ignore_sharp" in p:
            m[item.identifier] = U.flag(p, "ignore_sharp", M)


#: type -> (Blender type, setter, allowed keys)
TYPES = {
    "MIRROR": ("MIRROR", _set_mirror, {"axis", "bisect", "clip", "merge", "merge_threshold", "mirror_object"}),
    "SUBSURF": ("SUBSURF", _set_subsurf, {"levels", "render_levels", "simple", "use_creases", "quality",
                                          "boundary_smooth"}),
    "BEVEL": ("BEVEL", _set_bevel, {"width", "segments", "limit", "angle", "profile", "harden_normals", "clamp"}),
    "SOLIDIFY": ("SOLIDIFY", _set_solidify, {"thickness", "offset", "even", "rim", "flip"}),
    "SHRINKWRAP": ("SHRINKWRAP", _set_shrinkwrap, {"target", "method", "mode", "offset"}),
    "WEIGHTED_NORMAL": ("WEIGHTED_NORMAL", _set_wn, {"mode", "weight", "threshold", "keep_sharp"}),
    "DECIMATE": ("DECIMATE", _set_decimate, {"kind", "ratio", "angle", "iterations"}),
    "TRIANGULATE": ("TRIANGULATE", _set_triangulate, {"quad", "ngon", "min_vertices", "keep_normals"}),
    "ARRAY": ("ARRAY", _set_array, {"count", "relative", "constant", "merge", "merge_threshold", "fit_length",
                                    "curve"}),
    "CURVE": ("CURVE", _set_curve, {"curve", "axis"}),
    "WELD": ("WELD", _set_weld, {"threshold"}),
    "SMOOTH_BY_ANGLE": ("NODES", _set_sba, {"angle", "ignore_sharp"}),
}
_DEFAULTS = {"MIRROR": {"axis": "x", "clip": True, "merge": True}, "SUBSURF": {"levels": 1},
             "BEVEL": {"width": 0.02, "segments": 1, "limit": "angle", "angle": 30.0},
             "WEIGHTED_NORMAL": {"keep_sharp": True}, "DECIMATE": {"kind": "planar", "angle": 1.0},
             "SMOOTH_BY_ANGLE": {"angle": 30.0}}


def _kind_of(m) -> str:
    if m.type == "NODES":
        return "SMOOTH_BY_ANGLE" if m.node_group is not None and "Smooth by Angle" in m.node_group.name else "NODES"
    return m.type


def _settings(ctx, m, p: dict, M: str) -> None:
    kind = _kind_of(m)
    if kind not in TYPES:
        raise U.bad(M, f"{m.name} is a {m.type} modifier; satk only edits {', '.join(TYPES)}")
    _bt, setter, keys = TYPES[kind]
    extra = set(p) - keys
    if extra:
        raise U.bad(M, f"{kind}: unknown setting(s) {sorted(extra)}", data={"allowed": sorted(keys)})
    setter(ctx, m, p, M)


def _mod(o, name: str, M: str):
    m = o.modifiers.get(name)
    if m is None:
        raise SatkError("NOT_FOUND", f"{M}: {o.name} has no modifier {name!r}",
                        data={"modifiers": [x.name for x in o.modifiers]})
    return m


def _add_sba(o, M: str):
    before = {m.name for m in o.modifiers}
    with bpy.context.temp_override(object=o, active_object=o, selected_objects=[o], selected_editable_objects=[o]):
        r = bpy.ops.object.shade_auto_smooth(use_auto_smooth=True, angle=math.radians(30.0))
    new = [m for m in o.modifiers if m.name not in before]
    if "FINISHED" not in r or not new:
        raise SatkError("EXTERNAL_TOOL", f"{M}: Blender did not add 'Smooth by Angle'")
    return new[0]


def add(ctx, p: dict) -> dict:
    """Add a whitelisted modifier (type, settings, name, index) to objects; see the module doc for the settings."""
    M = "modifier.add"
    kind = U.text(p, "type", M, required=True, choices=tuple(TYPES)).upper()
    objs = U.resolve(ctx, p, M)
    sets = p.get("settings") or {}
    if not isinstance(sets, dict):
        raise U.bad(M, "'settings' must be an object")
    sets = {**_DEFAULTS.get(kind, {}), **sets}
    btype = TYPES[kind][0]
    out = []
    for o in objs:
        if o.type not in ("MESH", "CURVE"):
            raise U.bad(M, f"{o.name} is a {o.type}; modifiers go on meshes")
        if kind == "SMOOTH_BY_ANGLE":
            m = _add_sba(o, M)
            if p.get("name"):
                m.name = str(p["name"])
        else:
            m = o.modifiers.new(str(p.get("name") or kind.lower()), btype)
        try:
            _settings(ctx, m, sets, M)
        except Exception:
            o.modifiers.remove(m)
            raise
        if p.get("index") is not None:
            i = U.integer(p, "index", M, lo=0, hi=len(o.modifiers) - 1)
            o.modifiers.move(len(o.modifiers) - 1, i)
        out.append(f"{o.name}:{m.name}")
    if kind == "SUBSURF" and (sets.get("levels") or 0) > 1:
        ctx.warn("STYLE: subdivision level > 1 multiplies triangles by 16+; check tris against the class band")
    return {"added": out, "changed": [o.name for o in objs]}


def set_(ctx, p: dict) -> dict:
    """Change settings of a modifier (object, name, settings) or move it in the stack (index)."""
    M = "modifier.set"
    o = ctx.obj(U.text(p, "object", M, required=True))
    m = _mod(o, U.text(p, "name", M, required=True), M)
    if p.get("settings"):
        if not isinstance(p["settings"], dict):
            raise U.bad(M, "'settings' must be an object")
        _settings(ctx, m, p["settings"], M)
    if p.get("index") is not None:
        cur = list(o.modifiers).index(m)
        o.modifiers.move(cur, U.integer(p, "index", M, lo=0, hi=len(o.modifiers) - 1))
    if "show" in p:
        m.show_viewport = m.show_render = U.flag(p, "show", M)
    return {"object": o.name, "stack": [x.name for x in o.modifiers], "changed": [o.name]}


def apply(ctx, p: dict) -> dict:
    """Apply a modifier (name) or the whole stack (all=true) to the mesh; shared meshes are refused."""
    M = "modifier.apply"
    objs = U.resolve(ctx, p, M, types=("MESH",))
    done = []
    for o in objs:
        if o.data.users > 1:
            raise U.bad(M, f"{o.name} shares its mesh with another object", hint="scene.duplicate linked=false")
        names = [m.name for m in o.modifiers] if U.flag(p, "all", M, False) else \
            [_mod(o, U.text(p, "name", M, required=True), M).name]
        for n in names:
            with bpy.context.temp_override(object=o, active_object=o, selected_objects=[o],
                                           selected_editable_objects=[o]):
                r = bpy.ops.object.modifier_apply(modifier=n)
            if "FINISHED" not in r:
                raise SatkError("EXTERNAL_TOOL", f"{M}: Blender could not apply {n!r} on {o.name}")
            done.append(f"{o.name}:{n}")
    return {"applied": done, "changed": [o.name for o in objs]}


def remove(ctx, p: dict) -> dict:
    """Remove a modifier (name) or all of them (all=true) from objects."""
    M = "modifier.remove"
    objs = U.resolve(ctx, p, M)
    gone = []
    for o in objs:
        mods = list(o.modifiers) if U.flag(p, "all", M, False) else [_mod(o, U.text(p, "name", M, required=True), M)]
        for m in mods:
            gone.append(f"{o.name}:{m.name}")
            o.modifiers.remove(m)
    return {"removed": gone, "changed": [o.name for o in objs]}


@readonly
def list_(ctx, p: dict) -> dict:
    """The modifier stack of an object: name, type and the main settings."""
    M = "modifier.list"
    o = ctx.obj(U.text(p, "object", M, required=True))
    rows = []
    for m in o.modifiers:
        row: dict = {"name": m.name, "type": _kind_of(m)}
        for attr, key in (("levels", "levels"), ("width", "width"), ("segments", "segments"),
                          ("thickness", "thickness"), ("count", "count"), ("ratio", "ratio"), ("mode", "mode")):
            if hasattr(m, attr):
                v = getattr(m, attr)
                row[key] = round(v, 4) if isinstance(v, float) else v
        if not m.show_viewport:
            row["hidden"] = True
        rows.append(row)
    return {"object": o.name, "stack": rows}


METHODS = {"modifier.add": add, "modifier.set": set_, "modifier.apply": apply, "modifier.remove": remove,
           "modifier.list": list_}
