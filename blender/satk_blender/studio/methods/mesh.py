# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``mesh.*``: geometry building blocks with explicit density (no default segment counts for round shapes).

Shapes: ``primitive``, ``loft`` (sections along an axis, optionally mirrored halves), ``lathe`` (a profile
spun around an axis, with cylindrical UVs), ``curve`` (poly or smooth curves, tubes). Edits on a face selection:
``extrude``, ``inset``, ``solidify``, ``transform``, ``bevel``, ``subdivide``, ``delete``, ``normals``, ``group``, ``mark``; on the
whole mesh: ``loopcut`` (cuts at exact positions along an axis), ``bisect``, ``symmetrize``, ``bridge``,
``merge``, ``dissolve`` (limited, planar by default).

Face selector ``select`` (keys combine with AND; object space; default every face)::

    {"side": "+z", "within": 30}   {"normal": [0, 0.7, 0.7], "within": 20}   {"where": ["z>0.4", "y<-1.2"]}
    {"box": [[x0, y0, z0], [x1, y1, z1]]}   {"group": "roof"}   {"material": "glass"}   {"invert": true}

``save_group`` stores the new faces' vertices as a vertex group, so later steps select by name
(``{"group": "roof"}``) instead of by coordinates.

``inset``, ``extrude`` and ``solidify`` give their new faces real UVs: a new face with no UV area (bmesh copies the
parent's loops) is projected along its own plane at the texel density of the faces around it and anchored at the
neighbour it touches (:func:`_fill_uvs`), so ``uv.texel`` and the zero-area gate stay clean. An inset that would
collapse the faces and a zero-distance extrude are errors, not silent junk.
"""

from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

from satk.core.errors import SatkError
from satk.studio.core import readonly
from satk.studio.mock import primitive_counts, primitive_dims

from . import _util as U
from .uv import cylinder_uvs


def _vec3(p: dict, key: str, default: list[float]) -> list[float]:
    v = p.get(key, default)
    if not isinstance(v, (list, tuple)) or len(v) != 3 or not all(
            isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        raise SatkError("BAD_PARAMS", f"mesh.primitive: '{key}' must be [x, y, z], got {v!r}")
    return [float(x) for x in v]


def _fit_box(bm, dims: list[float]) -> None:
    """Scale and centre the vertices so the bounding box is exactly ``dims`` around the origin."""
    if not bm.verts:
        return
    lo = [min(v.co[i] for v in bm.verts) for i in range(3)]
    hi = [max(v.co[i] for v in bm.verts) for i in range(3)]
    for v in bm.verts:
        for i in range(3):
            ext = hi[i] - lo[i]
            c = (hi[i] + lo[i]) / 2
            v.co[i] = (v.co[i] - c) * (dims[i] / ext) if ext > 1e-9 else 0.0


def _build(bm, kind: str, p: dict, dims: list[float]) -> None:
    uv = True
    if kind in ("plane", "grid"):
        xs = int(p.get("x_segments", 1)) if kind == "grid" else 1
        ys = int(p.get("y_segments", 1)) if kind == "grid" else 1
        bmesh.ops.create_grid(bm, x_segments=xs, y_segments=ys, size=0.5, calc_uvs=uv)
        _fit_box(bm, dims)
    elif kind == "cube":
        bmesh.ops.create_cube(bm, size=1.0, calc_uvs=uv)
        _fit_box(bm, dims)
    elif kind in ("cylinder", "cone"):
        r = float(p.get("radius", 0.5))
        top = r if kind == "cylinder" else float(p.get("radius_top", 0) or 0)
        bmesh.ops.create_cone(bm, cap_ends=p.get("cap", "ngon") == "ngon", cap_tris=False, segments=int(p["segments"]),
                              radius1=r, radius2=top, depth=float(p.get("depth", 1.0)), calc_uvs=uv)
    elif kind == "uv_sphere":
        bmesh.ops.create_uvsphere(bm, u_segments=int(p["segments"]), v_segments=int(p["rings"]),
                                  radius=float(p.get("radius", 0.5)), calc_uvs=uv)
    elif kind == "ico_sphere":
        bmesh.ops.create_icosphere(bm, subdivisions=int(p["subdivisions"]), radius=float(p.get("radius", 0.5)),
                                   calc_uvs=uv)
    elif kind == "circle":
        bmesh.ops.create_circle(bm, cap_ends=p.get("cap", "ngon") == "ngon", cap_tris=False,
                                segments=int(p["segments"]), radius=float(p.get("radius", 0.5)), calc_uvs=uv)


def primitive(ctx, p: dict) -> dict:
    """Add a primitive with explicit density: plane, grid (x/y_segments), cube, cylinder/cone/circle (segments), uv_sphere (segments, rings), ico_sphere (subdivisions)."""
    kind = p.get("kind")
    want_v, want_t = primitive_counts(kind, p)  # validates kind and the density parameters
    dims = primitive_dims(kind, p)
    loc = _vec3(p, "location", [0.0, 0.0, 0.0])
    rot = _vec3(p, "rotation", [0.0, 0.0, 0.0])
    name = str(p.get("name") or kind)
    bm = bmesh.new()
    try:
        bm.loops.layers.uv.new("UVMap")
        _build(bm, kind, p, dims)
        me = bpy.data.meshes.new(name)
        bm.to_mesh(me)
    finally:
        bm.free()
    ob = bpy.data.objects.new(name, me)
    ctx.collection(p.get("collection")).objects.link(ob)
    ob.location = Vector(loc)
    ob.rotation_euler = [math.radians(a) for a in rot]
    me.calc_loop_triangles()
    if (len(me.vertices), len(me.loop_triangles)) != (want_v, want_t):
        ctx.warn(f"INTERNAL: {kind} has {len(me.vertices)} verts / {len(me.loop_triangles)} tris, "
                 f"expected {want_v} / {want_t}")
    return {"object": ob.name, "changed": [ob.name]}


# --------------------------------------------------------------------------- shapes from profiles


def _resample(pts: list[Vector], m: int, closed: bool) -> list[Vector]:
    """``m`` points spaced evenly by arc length along the polyline (from its first point)."""
    seq = list(pts) + ([pts[0]] if closed else [])
    seg = [(seq[i + 1] - seq[i]).length for i in range(len(seq) - 1)]
    total = sum(seg)
    if total < 1e-12:
        return [pts[0].copy() for _ in range(m)]
    targets = [total * k / m for k in range(m)] if closed else [total * k / (m - 1) for k in range(m)]
    out, i, acc = [], 0, 0.0
    for t in targets:
        while i < len(seg) - 1 and acc + seg[i] < t - 1e-12:
            acc += seg[i]
            i += 1
        f = 0.0 if seg[i] < 1e-12 else min(1.0, max(0.0, (t - acc) / seg[i]))
        out.append(seq[i].lerp(seq[i + 1], f))
    return out


def _plane_pt(ax: int, at: float, a: float, b: float) -> Vector:
    """A 2D section point (a, b) on the plane ``axis = at``: y -> (x, z), x -> (y, z), z -> (x, y)."""
    if ax == 1:
        return Vector((a, at, b))
    if ax == 0:
        return Vector((at, a, b))
    return Vector((a, b, at))


def loft(ctx, p: dict) -> dict:
    """Skin a new mesh through sections along an axis: sections=[{at, points:[[a,b],..]}], samples, mirror, cap.

    Axis y (default): a section lies in the x-z plane at y = at, a point is [x, z]. With ``mirror`` the
    points are the +x half from the top centre (x = 0) down to the bottom centre (x = 0); the -x half is
    mirrored. Every section is resampled to ``samples`` points by arc length from its first point, so
    sections may have different point counts. ``closed`` (default true) makes loops; ``cap`` fills the ends.
    """
    M = "mesh.loft"
    ax = U.axis(p, "axis", M, "y")
    secs = p.get("sections")
    if not isinstance(secs, list) or not 2 <= len(secs) <= 256:
        raise U.bad(M, "'sections' must be a list of 2-256 {\"at\": number, \"points\": [[a, b], ...]}",
                    hint="axis y: a = x (across), b = z (up); give the +x half with mirror=true")
    samples = U.integer(p, "samples", M, lo=3, hi=512, required=True)
    closed = U.flag(p, "closed", M, True)
    mirror = U.flag(p, "mirror", M, False)
    cap = U.flag(p, "cap", M, closed)
    name = U.text(p, "name", M, "loft")
    rings: list[list[Vector]] = []
    last_at = None
    for i, s in enumerate(secs):
        if not isinstance(s, dict):
            raise U.bad(M, f"section {i + 1} must be an object")
        at = U.num(s, "at", M, required=True)
        if last_at is not None and at == last_at:
            raise U.bad(M, f"sections {i} and {i + 1} are at the same position {at}")
        last_at = at
        pts = U.points2(s, "points", M, min_n=2 if mirror else 3)
        if mirror:
            pts = pts + [(-a, b) for a, b in reversed(pts[1:-1])]
        loop = [_plane_pt(ax, at, a, b) for a, b in pts]
        rings.append(_resample(loop, samples, closed))
    bm = bmesh.new()
    try:
        uvl = bm.loops.layers.uv.new("UVMap")  # before any element: a new layer invalidates element references
        vrings = [[bm.verts.new(v) for v in ring] for ring in rings]
        n, last = samples, max(1, len(vrings) - 1)
        for i, (r0, r1) in enumerate(zip(vrings, vrings[1:])):
            for k in range(n if closed else n - 1):
                k1 = (k + 1) % n
                f = bm.faces.new((r0[k], r0[k1], r1[k1], r1[k]))
                du = n if closed else n - 1
                for lp, uv in zip(f.loops, ((k / du, i / last), ((k + 1) / du, i / last),
                                            ((k + 1) / du, (i + 1) / last), (k / du, (i + 1) / last))):
                    lp[uvl].uv = uv  # a simple strip map: u around the section, v along the axis
        if cap and closed:
            for ring in (vrings[0], vrings[-1]):
                f = bm.faces.new(ring)
                ext = [v.co for v in ring]
                lo = [min(c[i] for c in ext) for i in range(3)]
                hi = [max(c[i] for c in ext) for i in range(3)]
                a, b = [i for i in range(3) if i != ax]
                for lp in f.loops:
                    lp[uvl].uv = ((lp.vert.co[a] - lo[a]) / max(hi[a] - lo[a], 1e-9),
                                  (lp.vert.co[b] - lo[b]) / max(hi[b] - lo[b], 1e-9))
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        ob = U.new_mesh_object(ctx, name, bm, p.get("collection"))
    finally:
        bm.free()
    return {"object": ob.name, **U.counts(ob), "changed": [ob.name]}


def _lathe_pt(ax: int, r: float, h: float) -> Vector:
    if ax == 2:
        return Vector((r, 0.0, h))
    if ax == 0:
        return Vector((h, r, 0.0))
    return Vector((0.0, h, r))


def lathe(ctx, p: dict) -> dict:
    """Spin a profile [[r, h], ...] around an axis into a new mesh (wheels, posts, barrels): segments required; UVs: u around, v along the profile."""
    M = "mesh.lathe"
    prof = U.points2(p, "profile", M)
    if any(r < 0 for r, _ in prof):
        raise U.bad(M, "profile radii must be >= 0")
    seg = U.integer(p, "segments", M, lo=3, hi=512, required=True)
    ax = U.axis(p, "axis", M, "z")
    ang = U.deg(p, "angle", M, 360.0, lo=1.0, hi=360.0)
    name = U.text(p, "name", M, "lathe")
    full = abs(ang - 2 * math.pi) < 1e-6
    bm = bmesh.new()
    try:
        vs = [bm.verts.new(_lathe_pt(ax, r, h)) for r, h in prof]
        es = [bm.edges.new((a, b)) for a, b in zip(vs, vs[1:])]
        axis_v = Vector([1.0 if i == ax else 0.0 for i in range(3)])
        bmesh.ops.spin(bm, geom=vs + es, cent=(0.0, 0.0, 0.0), axis=axis_v, steps=seg, angle=ang,
                       use_duplicate=False, use_merge=full)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)
        bm.faces.ensure_lookup_table()
        degenerate = [f for f in bm.faces if f.calc_area() < 1e-12]
        if degenerate:
            bmesh.ops.delete(bm, geom=degenerate, context="FACES_ONLY")
        if U.flag(p, "cap", M, False):
            bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=0)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        uvl = bm.loops.layers.uv.new("UVMap")
        # cylindrical UVs: u once around the axis (0..1 over the sweep), v = arc length along the profile, so a flat
        # top or rim gets its own height; caps get a planar disc map (fit them to their own rectangle)
        cum = [0.0]
        for (r0, h0), (r1, h1) in zip(prof, prof[1:]):
            cum.append(cum[-1] + math.hypot(r1 - r0, h1 - h0))
        tot = cum[-1] or 1.0
        table = {(round(r, 5), round(h, 5)): c / tot for (r, h), c in zip(prof, cum)}

        def v_of(r: float, h: float) -> float:
            hit = table.get((round(r, 5), round(h, 5)))
            if hit is not None:
                return hit
            k = min(range(len(prof)), key=lambda i: (prof[i][0] - r) ** 2 + (prof[i][1] - h) ** 2)
            return cum[k] / tot

        info = cylinder_uvs(bm, list(bm.faces), uvl, ax, u_scale=1.0 if full else 2 * math.pi / ang, v_of=v_of)
        ob = U.new_mesh_object(ctx, name, bm, p.get("collection"), U.vec(p, "location", M))
    finally:
        bm.free()
    out = {"object": ob.name, **U.counts(ob), "changed": [ob.name]}
    if info["caps"]:
        out["caps"] = info["caps"]
    return out


def curve(ctx, p: dict) -> dict:
    """Add a curve through points (kind poly|smooth, closed, bevel_depth for tubes): rails, pipes, CURVE targets."""
    M = "mesh.curve"
    pts = p.get("points")
    if not isinstance(pts, list) or not 2 <= len(pts) <= 2048:
        raise U.bad(M, "'points' must be a list of 2-2048 [x, y, z]")
    pts = [U.vec({"v": q}, "v", M) for q in pts]
    kind = U.text(p, "kind", M, "poly", choices=("poly", "smooth"))
    name = U.text(p, "name", M, "curve")
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.resolution_u = U.integer(p, "resolution", M, 4, lo=1, hi=64)
    cu.bevel_depth = U.num(p, "bevel_depth", M, 0.0, lo=0.0)
    cu.bevel_resolution = U.integer(p, "bevel_resolution", M, 2, lo=0, hi=32)
    cu.use_fill_caps = U.flag(p, "fill_caps", M, True)
    if kind == "poly":
        sp = cu.splines.new("POLY")
        sp.points.add(len(pts) - 1)
        for pt, co in zip(sp.points, pts):
            pt.co = (*co, 1.0)
    else:
        sp = cu.splines.new("BEZIER")
        sp.bezier_points.add(len(pts) - 1)
        for bp, co in zip(sp.bezier_points, pts):
            bp.co = co
            bp.handle_left_type = bp.handle_right_type = "AUTO"
    sp.use_cyclic_u = U.flag(p, "closed", M, False)
    ob = bpy.data.objects.new(name, cu)
    U.link_new(ctx, ob, p.get("collection"))
    return {"object": ob.name, "changed": [ob.name]}


def convert(ctx, p: dict) -> dict:
    """Turn a curve or text object into a mesh object of the same name (its modifiers applied)."""
    M = "mesh.convert"
    o = ctx.obj(U.text(p, "object", M, required=True))
    if o.type == "MESH":
        return {"object": o.name, "note": "already a mesh"}
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(o.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    name, mw, parent, colls = o.name, o.matrix_world.copy(), o.parent, list(o.users_collection)
    bpy.data.objects.remove(o, do_unlink=True)
    ob = bpy.data.objects.new(name, me)
    for c in colls or [ctx.scene.collection]:
        c.objects.link(ob)
    ob.parent = parent
    ob.matrix_world = mw
    return {"object": ob.name, **U.counts(ob), "changed": [ob.name]}


# --------------------------------------------------------------------------- edits on a face selection


# --------------------------------------------------------------------------- new faces: UVs and guards


def _uv_area(f, uvl) -> float:
    uv = [lp[uvl].uv for lp in f.loops]
    return sum(abs((uv[i] - uv[0]).cross(uv[i + 1] - uv[0])) / 2 for i in range(1, len(uv) - 1))


def _snapshot(bm) -> set:
    """The faces that exist now (bmesh operators clear and copy ``tag`` flags, so identity is the reliable mark)."""
    return set(bm.faces)


def _fill_uvs(bm, faces) -> int:
    """Real UVs for ``faces`` whose UV area is nearly zero: planar on the face's own plane (``v`` up the world z) at the
    median texel density (UV per metre) of the other faces, shifted so the face joins the UVs of a neighbour.
    Returns the number of faces fixed."""
    uvl = bm.loops.layers.uv.active
    if uvl is None:
        return 0
    mine = {f for f in faces if f.is_valid}
    dens = []
    for f in bm.faces:
        if f in mine:
            continue
        a3 = f.calc_area()
        a2 = _uv_area(f, uvl)
        if a3 > 1e-10 and a2 > 1e-12:
            dens.append(math.sqrt(a2 / a3))
    dens.sort()
    scale = dens[len(dens) // 2] if dens else 1.0
    fixed = 0
    for f in sorted(mine, key=lambda x: x.index):
        a3 = f.calc_area()
        if a3 < 1e-10 or _uv_area(f, uvl) > 1e-4 * a3 * scale * scale:
            continue
        n = f.normal
        up = Vector((0.0, 0.0, 1.0)) if abs(n.z) < 0.9 else Vector((0.0, 1.0, 0.0))
        t = (up - n * up.dot(n)).normalized()
        b = t.cross(n)
        c = f.calc_center_median()
        uv = {lp: Vector(((lp.vert.co - c).dot(b) * scale, (lp.vert.co - c).dot(t) * scale)) for lp in f.loops}
        anchor = None
        for e in f.edges:
            for nb in e.link_faces:
                if nb is f or nb in mine or _uv_area(nb, uvl) <= 1e-12:
                    continue
                theirs = [lp[uvl].uv.copy() for lp in nb.loops if lp.vert in e.verts]
                ours = [uv[lp] for lp in f.loops if lp.vert in e.verts]
                if len(theirs) == 2 and len(ours) == 2:
                    anchor = (theirs[0] + theirs[1]) / 2 - (ours[0] + ours[1]) / 2
                    break
            if anchor is not None:
                break
        if anchor is None:
            anchor = Vector((0.5, 0.5))
        for lp, q in uv.items():
            lp[uvl].uv = q + anchor
        fixed += 1
    return fixed


def _region_inradius(faces) -> float:
    """About the radius of the largest circle inside the region of ``faces``: the deepest face centre's distance to
    the region border (``inf`` for a region with no border, e.g. a closed mesh)."""
    border = U.boundary_edges(faces)
    if not border:
        return math.inf
    segs = [(e.verts[0].co.copy(), e.verts[1].co.copy()) for e in border]
    if len(faces) * len(segs) > 300_000:   # a huge region: the guard would cost seconds, the artist knows what they select
        return math.inf
    best = 0.0
    for f in faces:
        c = f.calc_center_median()
        d = math.inf
        for a, b in segs:
            ab = b - a
            tt = 0.0 if ab.length_squared < 1e-18 else max(0.0, min(1.0, (c - a).dot(ab) / ab.length_squared))
            d = min(d, (c - (a + ab * tt)).length)
        best = max(best, d)
    return best


def extrude(ctx, p: dict) -> dict:
    """Extrude the selected faces (select) by distance along their normal or by a vector; individual per face; a free sheet becomes a closed slab."""
    M = "mesh.extrude"
    o = U.mesh_obj(ctx, p, M)
    dist = U.num(p, "distance", M)
    vector = U.vec(p, "vector", M)
    if dist is None and vector is None:
        raise U.bad(M, "'distance' (along the normal) or 'vector' [x, y, z] is required")
    if (vector is None and abs(dist) < 1e-9) or (vector is not None and max(abs(x) for x in vector) < 1e-9):
        raise U.bad(M, "a zero extrusion makes zero-area faces", hint="give a distance such as 0.05 or a vector")
    individual = U.flag(p, "individual", M, False)
    with U.edit_mesh(o) as bm:
        faces = U.require_faces(U.select_faces(o, bm, p.get("select"), M), M, p.get("select"))
        before = _snapshot(bm)
        slab = False
        if individual:
            ret = bmesh.ops.extrude_discrete_faces(bm, faces=faces)
            new_faces = ret["faces"]
            for f in new_faces:
                bmesh.ops.translate(bm, vec=Vector(vector) if vector else f.normal * dist, verts=list(f.verts))
            new_verts = list({v for f in new_faces for v in f.verts})
        else:
            n = Vector()
            for f in faces:
                n += f.normal * f.calc_area()
            n = n.normalized() if n.length > 1e-12 else Vector((0.0, 0.0, 1.0))
            move = Vector(vector) if vector else n * dist
            # a region that is a whole free-standing sheet (every border edge is an open edge) keeps its old faces as
            # the back of a closed slab; on a solid the old faces are inside and go
            border = U.boundary_edges(faces)
            slab = bool(border) and all(len(e.link_faces) == 1 for e in border)
            ret = bmesh.ops.extrude_face_region(bm, geom=faces)
            new_verts = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMVert)]
            new_faces = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMFace)]
            old = [f for f in faces if f.is_valid]
            if old and not slab:
                bmesh.ops.delete(bm, geom=old, context="FACES_ONLY")
            bmesh.ops.translate(bm, vec=move, verts=new_verts)
            if old and slab:      # the old faces are the back of the slab: let the closed shell find its outside
                shell = old + [f for f in bm.faces if f not in before and f.is_valid]   # old + copies + walls
                bmesh.ops.recalc_face_normals(bm, faces=shell)
        bm.verts.index_update()
        U.save_group(o, bm, new_verts, p.get("save_group"), M)
        bm.faces.ensure_lookup_table()
        fixed = _fill_uvs(bm, [f for f in bm.faces if f not in before])
        nf = len(new_faces)
    out = {"faces": nf, **U.counts(o), "changed": [o.name]}
    if slab:
        out["slab"] = True
    if fixed:
        out["uv_filled"] = fixed
    return out


def inset(ctx, p: dict) -> dict:
    """Inset the selected faces by thickness (depth moves them in/out); the inner faces can be saved as a group; a thickness that would collapse the faces is an error."""
    M = "mesh.inset"
    o = U.mesh_obj(ctx, p, M)
    th = U.num(p, "thickness", M, required=True, lo=0.0)
    depth = U.num(p, "depth", M, 0.0)
    individual = U.flag(p, "individual", M, False)
    with U.edit_mesh(o) as bm:
        faces = U.require_faces(U.select_faces(o, bm, p.get("select"), M), M, p.get("select"))
        if th > 0:
            room = min(_region_inradius([f]) for f in faces) if individual else _region_inradius(faces)
            if th >= room * 0.98:
                raise U.bad(M, f"thickness {th:g} reaches the middle of the selection (about {room:.3g} m of room): "
                            "the inner face would collapse or flip",
                            hint=f"use a thickness below {room * 0.9:.3g}, or select a bigger region")
        before = _snapshot(bm)
        if individual:
            bmesh.ops.inset_individual(bm, faces=faces, thickness=th, depth=depth, use_even_offset=True)
        else:
            bmesh.ops.inset_region(bm, faces=faces, thickness=th, depth=depth, use_even_offset=True,
                                   use_boundary=True)
        bm.verts.index_update()
        U.save_group(o, bm, [v for f in faces if f.is_valid for v in f.verts], p.get("save_group"), M)
        bm.faces.ensure_lookup_table()
        fixed = _fill_uvs(bm, [f for f in bm.faces if f not in before])
    out = {"faces": len(faces), **U.counts(o), "changed": [o.name]}
    if fixed:
        out["uv_filled"] = fixed
    return out


def solidify(ctx, p: dict) -> dict:
    """Give a whole shell or free sheet thickness as real geometry (thickness m, offset -1 inward .. 1 outward, even): a closed shell with rim faces and UVs."""
    M = "mesh.solidify"
    o = U.mesh_obj(ctx, p, M)
    th = U.num(p, "thickness", M, required=True, lo=1e-5)
    offset = U.num(p, "offset", M, -1.0, lo=-1.0, hi=1.0)
    even = U.flag(p, "even", M, True)
    with U.edit_mesh(o) as bm:
        faces = U.require_faces(U.select_faces(o, bm, p.get("select"), M), M, p.get("select"))
        fs = set(faces)
        border = U.boundary_edges(faces)
        shared = [e for e in border if len(e.link_faces) > 1]
        if shared:
            raise U.bad(M, f"the selection ends on {len(shared)} edge(s) that other faces share",
                        hint="solidify takes a whole shell or a free-standing sheet; on a region of a solid use "
                        "mesh.inset then mesh.extrude")
        # per-vertex shift directions: the area-weighted normal, stretched so the wall stays 'thickness' thick
        shift: dict = {}
        for v in {v for f in faces for v in f.verts}:
            nf = [f for f in v.link_faces if f in fs]
            n = sum((f.normal * f.calc_area() for f in nf), Vector())
            n = n.normalized() if n.length > 1e-9 else Vector((0.0, 0.0, 1.0))
            k = 1.0
            if even:
                cos = sum(f.normal.dot(n) * f.calc_area() for f in nf) / max(sum(f.calc_area() for f in nf), 1e-12)
                k = 1.0 / max(cos, 0.3)
            shift[v] = n * k
        before = _snapshot(bm)
        ret = bmesh.ops.duplicate(bm, geom=list(faces))
        vmap = {a: b for a, b in ret["vert_map"].items() if isinstance(a, bmesh.types.BMVert)}
        dups = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMFace)]
        bmesh.ops.reverse_faces(bm, faces=dups)
        rim = 0
        for e in border:
            f = next(x for x in e.link_faces if x in fs)
            a, b = next((lp.vert, lp.link_loop_next.vert) for lp in f.loops if lp.edge is e)
            try:
                bm.faces.new((b, a, vmap[a], vmap[b]))
                rim += 1
            except ValueError:
                pass  # the rim face already exists
        for v, d in shift.items():
            bmesh.ops.translate(bm, vec=d * (th * (1.0 + offset) / 2.0), verts=[v])
            bmesh.ops.translate(bm, vec=d * (th * (offset - 1.0) / 2.0), verts=[vmap[v]])
        bm.normal_update()
        bm.faces.ensure_lookup_table()
        made = set(dups)
        fixed = _fill_uvs(bm, [f for f in bm.faces if f not in before and f not in made])
    out = {"rim": rim, **U.counts(o), "changed": [o.name]}
    if fixed:
        out["uv_filled"] = fixed
    return out


def _pivot(verts, how, method: str) -> Vector:
    if isinstance(how, list):
        return Vector(U.vec({"v": how}, "v", method))
    if how in (None, "median"):
        return sum((v.co for v in verts), Vector()) / max(1, len(verts))
    if how == "bounds":
        lo = Vector([min(v.co[i] for v in verts) for i in range(3)])
        hi = Vector([max(v.co[i] for v in verts) for i in range(3)])
        return (lo + hi) / 2
    if how == "origin":
        return Vector()
    raise U.bad(method, "'pivot' must be median, bounds, origin or [x, y, z]")


def transform(ctx, p: dict) -> dict:
    """Move/rotate/scale the vertices of the selected faces (translate, rotate deg, scale; pivot median|bounds|origin)."""
    M = "mesh.transform"
    o = U.mesh_obj(ctx, p, M)
    t = U.vec(p, "translate", M)
    r = U.vec(p, "rotate", M)
    s = U.vec(p, "scale", M)
    if t is None and r is None and s is None:
        raise U.bad(M, "give 'translate', 'rotate' (degrees) and/or 'scale'")
    with U.edit_mesh(o) as bm:
        faces = U.require_faces(U.select_faces(o, bm, p.get("select"), M), M, p.get("select"))
        verts = list({v for f in faces for v in f.verts})
        c = _pivot(verts, p.get("pivot"), M)
        mat = Matrix.Translation(c)
        if r is not None:
            mat = mat @ Euler([math.radians(a) for a in r]).to_matrix().to_4x4()
        if s is not None:
            mat = mat @ Matrix.Diagonal((*s, 1.0))
        mat = mat @ Matrix.Translation(-c)
        if t is not None:
            mat = Matrix.Translation(Vector(t)) @ mat
        for v in verts:
            v.co = mat @ v.co
    return {"verts": len(verts), "changed": [o.name]}


def _uv_border(bm, edges, uvl) -> list:
    out = []
    for e in edges:
        if len(e.link_faces) != 2:
            continue
        per_face = []
        for f in e.link_faces:
            d = {}
            for lp in f.loops:
                if lp.vert in e.verts:
                    d[lp.vert.index] = lp[uvl].uv.copy()
            per_face.append(d)
        a, b = per_face
        if any(k in b and (a[k] - b[k]).length > 1e-5 for k in a):
            out.append(e)
    return out


def _edges_for(o, bm, faces, p: dict, method: str, default_by: str = "angle") -> list:
    by = U.text(p, "by", method, default_by, choices=("angle", "all", "boundary", "sharp", "seam", "material", "uv"))
    edges = U.edges_of(faces)
    if by == "angle":
        lim = U.deg(p, "angle", method, 30.0)
        return [e for e in edges if len(e.link_faces) == 2 and U.dihedral(e) >= lim - 1e-6]
    if by == "boundary":
        return U.boundary_edges(faces)
    if by == "sharp":
        return [e for e in edges if not e.smooth]
    if by == "seam":
        return [e for e in edges if e.seam]
    if by == "material":
        return [e for e in edges if len(e.link_faces) == 2
                and e.link_faces[0].material_index != e.link_faces[1].material_index]
    if by == "uv":
        uvl = bm.loops.layers.uv.active
        if uvl is None:
            raise U.bad(method, f"{o.name} has no UV map", hint="uv.unwrap first")
        return _uv_border(bm, edges, uvl)
    return edges


def bevel(ctx, p: dict) -> dict:
    """Bevel edges of the selection (by angle|all|boundary|sharp|seam|material; width m, segments, profile)."""
    M = "mesh.bevel"
    o = U.mesh_obj(ctx, p, M)
    width = U.num(p, "width", M, required=True, lo=0.0)
    seg = U.integer(p, "segments", M, 1, lo=1, hi=32)
    prof = U.num(p, "profile", M, 0.5, lo=0.0, hi=1.0)
    with U.edit_mesh(o) as bm:
        faces = U.select_faces(o, bm, p.get("select"), M)
        edges = _edges_for(o, bm, faces, p, M)
        if not edges:
            raise SatkError("NOT_FOUND", f"{M}: no edges to bevel", hint="lower 'angle' or use by=all")
        ret = bmesh.ops.bevel(bm, geom=edges, offset=width, offset_type="OFFSET", segments=seg, profile=prof,
                              affect="EDGES", clamp_overlap=True)
        bm.verts.index_update()
        nf = ret.get("faces") or []
        U.save_group(o, bm, [v for f in nf for v in f.verts], p.get("save_group"), M)
    return {"edges": len(edges), **U.counts(o), "changed": [o.name]}


def subdivide(ctx, p: dict) -> dict:
    """Subdivide the edges of the selected faces (cuts, smooth 0-1); grid fill keeps quads."""
    M = "mesh.subdivide"
    o = U.mesh_obj(ctx, p, M)
    cuts = U.integer(p, "cuts", M, 1, lo=1, hi=16)
    with U.edit_mesh(o) as bm:
        faces = U.require_faces(U.select_faces(o, bm, p.get("select"), M), M, p.get("select"))
        bmesh.ops.subdivide_edges(bm, edges=U.edges_of(faces), cuts=cuts, use_grid_fill=True,
                                  smooth=U.num(p, "smooth", M, 0.0, lo=0.0, hi=1.0))
    return {**U.counts(o), "changed": [o.name]}


def delete(ctx, p: dict) -> dict:
    """Delete the selected faces (and the edges and vertices only they used)."""
    M = "mesh.delete"
    o = U.mesh_obj(ctx, p, M)
    if p.get("select") is None:
        raise U.bad(M, "'select' is required (deleting a whole object is scene.delete)")
    with U.edit_mesh(o) as bm:
        faces = U.require_faces(U.select_faces(o, bm, p.get("select"), M), M, p.get("select"))
        n = len(faces)
        bmesh.ops.delete(bm, geom=faces, context="FACES")
    return {"deleted": n, **U.counts(o), "changed": [o.name]}


def normals(ctx, p: dict) -> dict:
    """Recalculate face normals outside (default) or inside, or flip them, on the selection."""
    M = "mesh.normals"
    o = U.mesh_obj(ctx, p, M)
    mode = U.text(p, "mode", M, "outside", choices=("outside", "inside", "flip"))
    with U.edit_mesh(o) as bm:
        faces = U.require_faces(U.select_faces(o, bm, p.get("select"), M), M, p.get("select"))
        if mode != "flip":
            bmesh.ops.recalc_face_normals(bm, faces=faces)
        if mode in ("inside", "flip"):
            bmesh.ops.reverse_faces(bm, faces=faces)
    return {"faces": len(faces), "changed": [o.name]}


# --------------------------------------------------------------------------- whole-mesh edits


def loopcut(ctx, p: dict) -> dict:
    """Cut edge loops across the mesh at exact positions along an axis: at=[..] (m), fractions=[0-1] or count."""
    M = "mesh.loopcut"
    o = U.mesh_obj(ctx, p, M)
    ax = U.axis(p, "axis", M, "y")
    with U.edit_mesh(o) as bm:
        faces = U.require_faces(U.select_faces(o, bm, p.get("select"), M), M, p.get("select"))
        vs = {v for f in faces for v in f.verts}
        lo = min(v.co[ax] for v in vs)
        hi = max(v.co[ax] for v in vs)
        if p.get("at") is not None:
            at = p["at"] if isinstance(p["at"], list) else [p["at"]]
            if not all(U.is_num(x) for x in at):
                raise U.bad(M, "'at' must be a number or a list of numbers")
            pos = [float(x) for x in at]
        elif p.get("fractions") is not None:
            fr = p["fractions"]
            if not isinstance(fr, list) or not all(U.is_num(x) and 0 < x < 1 for x in fr):
                raise U.bad(M, "'fractions' must be numbers between 0 and 1")
            pos = [lo + (hi - lo) * float(x) for x in fr]
        else:
            n = U.integer(p, "count", M, lo=1, hi=64, required=True)
            pos = [lo + (hi - lo) * (k + 1) / (n + 1) for k in range(n)]
        if len(pos) > 64:
            raise U.bad(M, "at most 64 cuts per call")
        e0 = len(bm.edges)
        region = set(faces)
        done = 0
        for x in sorted(pos):
            if not lo < x < hi:
                ctx.warn(f"BAD_PARAMS: cut at {x:g} is outside the selection ({lo:g}..{hi:g}); skipped")
                continue
            co = [0.0, 0.0, 0.0]
            co[ax] = x
            no = [0.0, 0.0, 0.0]
            no[ax] = 1.0
            fs = [f for f in region if f.is_valid]
            geom = list({v for f in fs for v in f.verts}) + U.edges_of(fs) + fs
            ret = bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-6, plane_co=co, plane_no=no)
            region = {f for f in region if f.is_valid}
            region |= {g for g in ret["geom"] if isinstance(g, bmesh.types.BMFace) and g.is_valid}
            done += 1
        new_edges = len(bm.edges) - e0
    return {"cuts": done, "new_edges": new_edges, **U.counts(o), "changed": [o.name]}


def bisect(ctx, p: dict) -> dict:
    """Cut the mesh with an axis plane (axis, at) and keep '+', '-' or both sides (fill closes the cut)."""
    M = "mesh.bisect"
    o = U.mesh_obj(ctx, p, M)
    ax = U.axis(p, "axis", M, "x")
    at = U.num(p, "at", M, 0.0)
    keep = U.text(p, "keep", M, "both", choices=("+", "-", "both"))
    co = [0.0, 0.0, 0.0]
    co[ax] = at
    no = [0.0, 0.0, 0.0]
    no[ax] = 1.0
    with U.edit_mesh(o) as bm:
        geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
        ret = bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-6, plane_co=co, plane_no=no,
                                     clear_inner=keep == "+", clear_outer=keep == "-")
        if U.flag(p, "fill", M, False):
            cut = [g for g in ret["geom_cut"] if isinstance(g, bmesh.types.BMEdge) and g.is_valid]
            if cut:
                bmesh.ops.holes_fill(bm, edges=cut, sides=0)
    return {**U.counts(o), "changed": [o.name]}


#: source side -> the bmesh 'direction' of symmetrize (the side that is kept and copied over the plane)
_SYM = {"+x": "X", "-x": "-X", "+y": "Y", "-y": "-Y", "+z": "Z", "-z": "-Z"}


def symmetrize(ctx, p: dict) -> dict:
    """Make the mesh symmetric: copy the 'source' side (+x default) onto the other side of the axis plane."""
    M = "mesh.symmetrize"
    o = U.mesh_obj(ctx, p, M)
    src = U.text(p, "source", M, "+x", choices=tuple(_SYM))
    with U.edit_mesh(o) as bm:
        bmesh.ops.symmetrize(bm, input=list(bm.verts) + list(bm.edges) + list(bm.faces), direction=_SYM[src],
                             dist=U.num(p, "dist", M, 1e-4, lo=0.0))
    return {**U.counts(o), "changed": [o.name]}


def bridge(ctx, p: dict) -> dict:
    """Bridge two open edge loops of a mesh (join 'with' objects into it first); cuts adds loops in the bridge."""
    M = "mesh.bridge"
    o = U.mesh_obj(ctx, p, M)
    others = []
    if p.get("with") is not None:
        others = [x for x in U.resolve(ctx, {"objects": p["with"]}, M, types=("MESH",)) if x is not o]
    joined = [x.name for x in others]
    if others:
        with bpy.context.temp_override(active_object=o, object=o, selected_objects=[o, *others],
                                       selected_editable_objects=[o, *others]):
            bpy.ops.object.join()
    with U.edit_mesh(o) as bm:
        if p.get("select") is not None:
            faces = U.require_faces(U.select_faces(o, bm, p.get("select"), M), M, p.get("select"))
            edges = [e for e in U.edges_of(faces) if e.is_boundary]
        else:
            edges = [e for e in bm.edges if e.is_boundary or e.is_wire]  # open borders and bare edge loops
        if not edges:
            raise SatkError("NOT_FOUND", f"{M}: no open edge loops", hint="mesh.delete opens a mesh")
        try:
            ret = bmesh.ops.bridge_loops(bm, edges=edges, use_pairs=False, use_cyclic=False,
                                         twist_offset=U.integer(p, "twist", M, 0, lo=-512, hi=512))
        except (RuntimeError, ValueError) as e:
            raise U.bad(M, f"cannot bridge: {e}", hint="two open loops with the same vertex count bridge best; "
                        "narrow them with 'select'") from None
        cuts = U.integer(p, "cuts", M, 0, lo=0, hi=32)
        if cuts:
            rails = [e for e in ret.get("edges") or [] if e.is_valid]
            bmesh.ops.subdivide_edges(bm, edges=rails, cuts=cuts, use_grid_fill=True)
    out = {**U.counts(o), "changed": [o.name]}
    if joined:
        out["joined"] = joined
    return out


def merge(ctx, p: dict) -> dict:
    """Merge vertices closer than dist (m, default 0.0001) on the selection (weld)."""
    M = "mesh.merge"
    o = U.mesh_obj(ctx, p, M)
    dist = U.num(p, "dist", M, 1e-4, lo=0.0)
    with U.edit_mesh(o) as bm:
        faces = U.select_faces(o, bm, p.get("select"), M)
        verts = list({v for f in faces for v in f.verts}) if p.get("select") is not None else list(bm.verts)
        n0 = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=verts, dist=dist)
        removed = n0 - len(bm.verts)
    return {"removed": removed, **U.counts(o), "changed": [o.name]}


_DELIMIT = ("normal", "material", "seam", "sharp", "uv")


def dissolve(ctx, p: dict) -> dict:
    """Limited dissolve below angle (deg, default 1 = planar only); delimit keeps material/uv/sharp/seam borders."""
    M = "mesh.dissolve"
    o = U.mesh_obj(ctx, p, M)
    ang = U.num(p, "angle", M, 1.0, lo=0.0, hi=90.0)
    if ang > 5.0:
        ctx.warn(f"STYLE: dissolve at {ang:g} deg is not planar and changes the silhouette; author at the target "
                 "density instead")
    dl = p.get("delimit", ["material", "uv", "sharp", "seam"])
    if not isinstance(dl, list) or not all(x in _DELIMIT for x in dl):
        raise U.bad(M, f"'delimit' must be a list of {', '.join(_DELIMIT)}")
    with U.edit_mesh(o) as bm:
        faces = U.select_faces(o, bm, p.get("select"), M)
        f0 = len(bm.faces)
        bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(ang), use_dissolve_boundaries=False,
                                 verts=list({v for f in faces for v in f.verts}), edges=U.edges_of(faces),
                                 delimit={x.upper() for x in dl})
        removed = f0 - len(bm.faces)
    return {"faces_removed": removed, **U.counts(o), "changed": [o.name]}


def mark(ctx, p: dict) -> dict:
    """Mark edges sharp or seam (kind) by angle|material|uv|boundary|all on the selection; clear=true unmarks."""
    M = "mesh.mark"
    o = U.mesh_obj(ctx, p, M)
    kind = U.text(p, "kind", M, "sharp", choices=("sharp", "seam"))
    clear = U.flag(p, "clear", M, False)
    with U.edit_mesh(o) as bm:
        faces = U.select_faces(o, bm, p.get("select"), M)
        edges = _edges_for(o, bm, faces, p, M)
        for e in edges:
            if kind == "sharp":
                e.smooth = clear
            else:
                e.seam = not clear
        total = sum(1 for e in bm.edges if (not e.smooth if kind == "sharp" else e.seam))
    return {"edges": len(edges), f"{kind}_total": total, "changed": [o.name]}


def group(ctx, p: dict) -> dict:
    """Store the vertices of the selected faces as a vertex group (mode replace|add|remove) to address them later."""
    M = "mesh.group"
    o = U.mesh_obj(ctx, p, M)
    name = U.text(p, "name", M, required=True)
    mode = U.text(p, "mode", M, "replace", choices=("replace", "add", "remove"))
    with U.edit_mesh(o) as bm:
        faces = U.require_faces(U.select_faces(o, bm, p.get("select"), M), M, p.get("select"))
        bm.verts.index_update()
        idx = {v.index for f in faces for v in f.verts}
        vg = o.vertex_groups.get(name)
        dl = bm.verts.layers.deform.active
        if vg is not None and mode != "replace" and dl is not None:
            have = {v.index for v in bm.verts if vg.index in v[dl]}
            idx = (have | idx) if mode == "add" else (have - idx)
        bm.verts.ensure_lookup_table()
        U.save_group(o, bm, [bm.verts[i] for i in sorted(idx)], name, M)
        n = len(idx)
    return {"group": name, "verts": n, "changed": [o.name]}


@readonly
def info(ctx, p: dict) -> dict:
    """Topology of one mesh: counts, open/non-manifold edges, sharp/seam edges, groups, materials, UV maps, bbox."""
    M = "mesh.info"
    o = U.mesh_obj(ctx, p, M)
    me = o.data
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        open_e = sum(1 for e in bm.edges if e.is_boundary)
        nonman = sum(1 for e in bm.edges if len(e.link_faces) > 2)
        sharp = sum(1 for e in bm.edges if not e.smooth)
        seam = sum(1 for e in bm.edges if e.seam)
        dl = bm.verts.layers.deform.active
        sizes: dict = {}
        if dl is not None:
            for v in bm.verts:
                for gi in v[dl].keys():
                    sizes[gi] = sizes.get(gi, 0) + 1
        lo = [min((v.co[i] for v in bm.verts), default=0.0) for i in range(3)]
        hi = [max((v.co[i] for v in bm.verts), default=0.0) for i in range(3)]
        smooth = sum(1 for f in bm.faces if f.smooth)
        nf = len(bm.faces)
    finally:
        bm.free()
    out = {"object": o.name, **U.counts(o), "open_edges": open_e, "nonmanifold_edges": nonman,
           "sharp_edges": sharp, "seam_edges": seam, "smooth_faces": smooth, "flat_faces": nf - smooth,
           "bbox": [U.r3(lo), U.r3(hi)],
           "groups": {g.name: sizes.get(g.index, 0) for g in o.vertex_groups},
           "materials": [s.material.name if s.material else "" for s in o.material_slots],
           "uv": [u.name for u in me.uv_layers], "modifiers": [m.name for m in o.modifiers]}
    return {k: v for k, v in out.items() if v not in ([], {}, None)}


METHODS = {"mesh.primitive": primitive, "mesh.loft": loft, "mesh.lathe": lathe, "mesh.curve": curve,
           "mesh.convert": convert, "mesh.extrude": extrude, "mesh.inset": inset, "mesh.solidify": solidify,
           "mesh.transform": transform,
           "mesh.bevel": bevel, "mesh.subdivide": subdivide, "mesh.delete": delete, "mesh.normals": normals,
           "mesh.loopcut": loopcut, "mesh.bisect": bisect, "mesh.symmetrize": symmetrize, "mesh.bridge": bridge,
           "mesh.merge": merge, "mesh.dissolve": dissolve, "mesh.mark": mark, "mesh.group": group,
           "mesh.info": info}
