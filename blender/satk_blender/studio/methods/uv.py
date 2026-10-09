# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``uv.*``: UV maps - unwrap (smart, seams, cube/cylinder/sphere, planar along an axis), fit a selection into
an atlas rectangle, a second UV map (the env-map UV of vehicles), and a texel-density report (px/m).

``cylinder`` is computed here (not with the Blender operator): ``u`` runs once around the axis (0..1), ``v`` is the
height at the mean radius (square texels at that radius). The seam sits on a vertex angle, so no face spans the
whole ``u`` range: a face next to the seam ends at ``u = 1``, the next one starts at ``u = 0``. A vertex on the axis
takes the ``u`` of its face, and a cap (a face that encircles the axis) gets a planar disc map. :func:`cylinder_uvs`
is shared with ``mesh.lathe``.
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

from satk.core.errors import SatkError
from satk.studio.core import readonly

from . import _util as U

METHODS_UV = ("smart", "seams", "cube", "cylinder", "sphere", "planar")


def _layer(o, name: str | None, method: str, *, create: bool = True):
    me = o.data
    if name:
        uvl = me.uv_layers.get(name)
        if uvl is None:
            if not create:
                raise SatkError("NOT_FOUND", f"{method}: {o.name} has no UV map {name!r}",
                                data={"uv": [u.name for u in me.uv_layers]})
            uvl = me.uv_layers.new(name=name)
    else:
        uvl = me.uv_layers.active or me.uv_layers.new(name="UVMap")
    me.uv_layers.active = uvl
    return uvl


def _select_only(o, faces_idx: set[int]) -> None:
    for poly in o.data.polygons:
        poly.select = poly.index in faces_idx
    for v in o.data.vertices:
        v.select = False
    for e in o.data.edges:
        e.select = False
    keys = set()
    for poly in o.data.polygons:
        if poly.select:
            for vi in poly.vertices:
                o.data.vertices[vi].select = True
            keys.update(poly.edge_keys)
    if keys:
        for e in o.data.edges:
            if e.key in keys:
                e.select = True


def _run_edit_op(o, fn) -> None:
    """Run UV operators in edit mode of ``o`` (made the only selected, active object; restored afterwards). The
    edit runs in face select mode: in vertex mode Blender flushes the vertex selection up and a face between
    selected faces (or next to them) would join the unwrap."""
    vl = bpy.context.view_layer
    ts = bpy.context.scene.tool_settings
    prev_mode = tuple(ts.mesh_select_mode)
    prev_active = vl.objects.active
    prev_sel = [x for x in vl.objects if x.select_get()]
    for x in prev_sel:
        x.select_set(False)
    o.select_set(True)
    vl.objects.active = o
    try:
        ts.mesh_select_mode = (False, False, True)
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            fn()
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
    finally:
        try:
            ts.mesh_select_mode = prev_mode
        except (TypeError, ValueError, RuntimeError):
            pass
        o.select_set(False)
        for x in prev_sel:
            try:
                x.select_set(True)
            except (ReferenceError, RuntimeError):
                pass
        vl.objects.active = prev_active


def _wrap(x: float) -> float:
    """``x`` wrapped into [-0.5, 0.5)."""
    return x - math.floor(x + 0.5)


def _seam(angles: list[float]) -> float:
    """The angle of the seam: a vertex angle. A mesh that goes all the way round gets the smallest angle; an arc (a
    partial lathe) is cut at the start of the arc, after the biggest empty gap."""
    if not angles:
        return 0.0
    a = sorted({round(x, 7) for x in angles})
    if len(a) < 2:
        return a[0]
    tau = 2 * math.pi
    gaps = [(a[(i + 1) % len(a)] - a[i]) % tau or tau for i in range(len(a))]
    top = max(gaps)
    if top - min(gaps) < 1e-4:
        return a[0]
    return a[(gaps.index(top) + 1) % len(a)]


def cylinder_uvs(bm, faces, uvl, ax: int, *, u_scale: float = 1.0, v_of=None) -> dict:
    """Cylindrical UVs of ``faces`` around axis ``ax`` (0 x, 1 y, 2 z); see the module docstring.

    ``u_scale`` stretches ``u`` (``2 pi / sweep`` for a partial lathe); ``v_of(r, h)`` gives ``v`` (default: the
    height over ``2 pi R`` from the lowest vertex, ``R`` = the mean radius). Returns ``{"seam", "caps", "poles"}``.
    """
    a, b = [i for i in range(3) if i != ax]
    verts = {v for f in faces for v in f.verts}
    if not verts:
        return {"seam": 0.0, "caps": 0, "poles": 0}
    ext = max((max(v.co[i] for v in verts) - min(v.co[i] for v in verts)) for i in range(3))
    eps = max(ext, 1e-6) * 1e-5
    ang, rad, pole = {}, {}, set()
    for v in verts:
        x, y = v.co[a], v.co[b]
        r = math.hypot(x, y)
        rad[v] = r
        if r <= eps:
            pole.add(v)
        else:
            ang[v] = math.atan2(y, x) % (2 * math.pi)
    rs = [rad[v] for v in verts if v not in pole]
    mean_r = sum(rs) / len(rs) if rs else 1.0
    seam = _seam(sorted(ang.values()))
    h0 = min(v.co[ax] for v in verts)
    tau = 2 * math.pi

    def vv(v) -> float:
        if v_of is not None:
            return v_of(rad[v], v.co[ax])
        return (v.co[ax] - h0) / (tau * mean_r)

    caps = 0
    for f in faces:
        loops = list(f.loops)
        real = [lp for lp in loops if lp.vert not in pole]
        # a face that winds around the axis (a cap) has no cylindrical map: planar disc
        total = 0.0
        for i, lp in enumerate(real):
            d = ang[real[(i + 1) % len(real)].vert] - ang[lp.vert]
            total += d - tau * round(d / tau)
        if len(real) >= 3 and (abs(total) > math.pi or (not real and len(loops) >= 3)):
            rmax = max(rad[v] for v in f.verts) or 1.0
            for lp in loops:
                lp[uvl].uv = (0.5 + lp.vert.co[a] / (2 * rmax), 0.5 + lp.vert.co[b] / (2 * rmax))
            caps += 1
            continue
        if not real:  # a face made only of axis vertices: nothing to map
            for lp in loops:
                lp[uvl].uv = (0.0, vv(lp.vert))
            continue
        t0 = ((ang[real[0].vert] - seam) % tau) / tau
        ts: dict = {}
        for lp in real:
            t = ((ang[lp.vert] - seam) % tau) / tau
            ts[lp] = t0 + _wrap(t - t0)
        mean_t = sum(ts.values()) / len(ts)
        shift = -math.floor(mean_t + 1e-9)
        mid = sum(ts.values()) / len(ts) + shift
        for lp in real:
            t = ts[lp] + shift
            t = 0.0 if abs(t) < 1e-7 else (1.0 if abs(t - 1.0) < 1e-7 else t)
            ts[lp] = t
        for lp in loops:
            t = ts.get(lp, mid)
            lp[uvl].uv = (t * u_scale, vv(lp.vert))
    return {"seam": round(math.degrees(seam), 2), "caps": caps, "poles": len(pole)}


def unwrap(ctx, p: dict) -> dict:
    """UV-unwrap the selected faces: method smart|seams|cube|cylinder|sphere|planar (axis x|y|z for planar and cylinder, size m), layer, margin; cylinder keeps every face off the seam."""
    M = "uv.unwrap"
    o = U.mesh_obj(ctx, p, M)
    how = U.text(p, "method", M, "smart", choices=METHODS_UV)
    uv_name = _layer(o, p.get("layer"), M).name
    with U.edit_mesh(o) as bm:
        faces = U.require_faces(U.select_faces(o, bm, p.get("select"), M), M, p.get("select"))
        idx = {f.index for f in faces}
        info = {}
        if how == "planar":
            ax = U.axis(p, "axis", M, "z")
            size = U.num(p, "size", M, 1.0, lo=1e-4)
            a, b = [i for i in range(3) if i != ax]
            layer = bm.loops.layers.uv.get(uv_name)
            for f in faces:
                for lp in f.loops:
                    lp[layer].uv = (lp.vert.co[a] / size, lp.vert.co[b] / size)
        elif how == "cylinder":
            info = cylinder_uvs(bm, faces, bm.loops.layers.uv.get(uv_name), U.axis(p, "axis", M, "z"))
    if how not in ("planar", "cylinder"):
        margin = U.num(p, "margin", M, 0.02, lo=0.0, hi=0.5)
        _select_only(o, idx)

        def go():
            if how == "smart":
                bpy.ops.uv.smart_project(angle_limit=U.deg(p, "angle", M, 66.0, lo=1.0, hi=89.0),
                                         island_margin=margin, scale_to_bounds=False)
            elif how == "seams":
                bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=margin)
            elif how == "cube":
                bpy.ops.uv.cube_project(cube_size=U.num(p, "size", M, 1.0, lo=1e-4), scale_to_bounds=False)
            else:
                bpy.ops.uv.sphere_project(direction="ALIGN_TO_OBJECT", scale_to_bounds=False)

        _run_edit_op(o, go)
    out = {"object": o.name, "layer": uv_name, "faces": len(idx), "method": how, "changed": [o.name]}
    if how == "cylinder":
        out.update(seam_deg=info["seam"], **({"caps": info["caps"]} if info["caps"] else {}))
    return out


def fit(ctx, p: dict) -> dict:
    """Scale and move the UVs of the selected faces into rect [u0, v0, u1, v1] (an atlas region); keep_aspect=true keeps the shape (default false: the island fills the rect)."""
    M = "uv.fit"
    o = U.mesh_obj(ctx, p, M)
    rect = U.vec(p, "rect", M, n=4, required=True)
    u0, v0, u1, v1 = rect
    if not (u1 > u0 and v1 > v0):
        raise U.bad(M, "'rect' must be [u0, v0, u1, v1] with u1 > u0 and v1 > v0")
    keep = U.flag(p, "keep_aspect", M, False)
    uv_name = _layer(o, p.get("layer"), M, create=False).name
    with U.edit_mesh(o) as bm:
        layer = bm.loops.layers.uv.get(uv_name)
        faces = U.require_faces(U.select_faces(o, bm, p.get("select"), M), M, p.get("select"))
        loops = [lp for f in faces for lp in f.loops]
        us = [lp[layer].uv.x for lp in loops]
        vs = [lp[layer].uv.y for lp in loops]
        lo_u, hi_u, lo_v, hi_v = min(us), max(us), min(vs), max(vs)
        su = (u1 - u0) / max(hi_u - lo_u, 1e-9)
        sv = (v1 - v0) / max(hi_v - lo_v, 1e-9)
        if keep:
            su = sv = min(su, sv)
        for lp in loops:
            uv = lp[layer].uv
            lp[layer].uv = (u0 + (uv.x - lo_u) * su, v0 + (uv.y - lo_v) * sv)
    return {"object": o.name, "layer": uv_name, "scale": [round(su, 4), round(sv, 4)], "changed": [o.name]}


def layers(ctx, p: dict) -> dict:
    """Add, rename, remove or activate UV maps (add=name, remove=name, active=name); vehicles use a 2nd map for env."""
    M = "uv.layers"
    o = U.mesh_obj(ctx, p, M)
    me = o.data
    if p.get("add"):
        name = U.text(p, "add", M)
        if me.uv_layers.get(name) is None:
            src = me.uv_layers.active
            new = me.uv_layers.new(name=name, do_init=True)
            if src is not None and U.flag(p, "copy", M, True):
                for a, b in zip(new.data, src.data):
                    a.uv = b.uv
    if p.get("remove"):
        uvl = me.uv_layers.get(str(p["remove"]))
        if uvl is None:
            raise SatkError("NOT_FOUND", f"{M}: no UV map {p['remove']!r}")
        me.uv_layers.remove(uvl)
    if p.get("active"):
        _layer(o, str(p["active"]), M, create=False)
    return {"object": o.name, "uv": [u.name for u in me.uv_layers],
            "active": me.uv_layers.active.name if me.uv_layers.active else None, "changed": [o.name]}


def _tex_size(o, slot_index: int) -> float | None:
    """Effective side of the slot's texture: ``sqrt(w * h)`` (texel density follows the pixel count, so a 256x128
    texture counts as 181)."""
    if slot_index >= len(o.material_slots):
        return None
    mat = o.material_slots[slot_index].material
    if mat is None or mat.node_tree is None:
        return None
    for n in mat.node_tree.nodes:
        if n.type == "TEX_IMAGE" and n.image is not None and n.image.size[0]:
            return math.sqrt(n.image.size[0] * n.image.size[1])
    return None


def _pct(vals: list[float], q: float) -> float:
    s = sorted(vals)
    if not s:
        return 0.0
    k = (len(s) - 1) * q
    i = int(math.floor(k))
    j = min(i + 1, len(s) - 1)
    return s[i] + (s[j] - s[i]) * (k - i)


def _px(v, method: str) -> float | None:
    """Effective texture side of ``px``: ``256``, ``[256, 128]`` or ``"256x128"`` (non-square: ``sqrt(w * h)``)."""
    import re

    if v is None:
        return None
    if isinstance(v, str):
        v = [x for x in re.split(r"[x,\s]+", v.strip().lower()) if x]
    if isinstance(v, (list, tuple)):
        if len(v) == 1:
            v = v[0]
        elif len(v) == 2 and all(U.is_num(x) for x in v) and all(1 <= float(x) <= 8192 for x in v):
            return math.sqrt(float(v[0]) * float(v[1]))
        else:
            raise U.bad(method, "'px' must be a number, [w, h] or 'WxH'")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise U.bad(method, f"'px' must be a number, [w, h] or 'WxH', got {v!r}") from None
    if not 1 <= f <= 8192:
        raise U.bad(method, f"'px' must be 1..8192, got {v!r}")
    return f


@readonly
def texel(ctx, p: dict) -> dict:
    """Texel density of an object in px/m (p10/p50/p90 over faces, area-weighted); texture size from its image or 'px' (N, [w, h] or 'WxH')."""
    M = "uv.texel"
    o = U.mesh_obj(ctx, p, M)
    px = _px(p.get("px"), M)
    me = o.data
    uvl = me.uv_layers.active
    if uvl is None:
        raise SatkError("NOT_FOUND", f"{M}: {o.name} has no UV map", hint="uv.unwrap first")
    me.calc_loop_triangles()
    sc = o.matrix_world.to_scale()
    rows: list[tuple[float, float]] = []
    zero = 0
    for t in me.loop_triangles:
        size = px or _tex_size(o, t.material_index)
        if not size:
            continue
        a, b, c = (o.matrix_world @ me.vertices[i].co for i in t.vertices)
        area3 = ((b - a).cross(c - a)).length / 2
        ua, ub, uc = (Vector(uvl.data[li].uv) for li in t.loops)
        area2 = abs((ub - ua).x * (uc - ua).y - (ub - ua).y * (uc - ua).x) / 2
        if area3 < 1e-10:
            continue
        if area2 < 1e-12:
            zero += 1
            continue
        rows.append((size * math.sqrt(area2 / area3), area3))
    del sc
    if not rows:
        raise SatkError("NOT_FOUND", f"{M}: no textured triangles", hint="give 'px' (texture size) or assign an "
                        "image texture")
    total = sum(a for _, a in rows)
    vals = []
    for d, a in rows:  # area-weighted sample: repeat by a share of 200 buckets
        vals.extend([d] * max(1, int(round(200 * a / total))))
    out = {"object": o.name, "px_per_m": {"p10": round(_pct(vals, 0.1), 1), "p50": round(_pct(vals, 0.5), 1),
                                          "p90": round(_pct(vals, 0.9), 1)}, "tris": len(rows)}
    if zero:
        out["zero_area_tris"] = zero
    return out


METHODS = {"uv.unwrap": unwrap, "uv.fit": fit, "uv.layers": layers, "uv.texel": texel}
