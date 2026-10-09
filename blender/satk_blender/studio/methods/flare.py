# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``mesh.flare``: a wheel arch grown from the shell's own faces, the vanilla way.

Vanilla SA arches are holes cut into the welded side with 5-8 segments over the top, a short return face and a
flat liner plate inside, so you never see through the body; SUVs add a raised band 5-8 cm wide INSIDE the fender
surface whose outer edge is chamfered into the body (never a separate horseshoe slab with a gap).

With ``arch`` {``center`` [x, y, z] (x picks the side), ``radius``} the side faces are cut along a polygon with
``segments`` over the top (straight down below the centre), and two more loops at ``radius + width`` and
``radius + width + chamfer`` give the band a clean edge. With ``select`` the selected faces are the opening.
Then: the band within ``width`` of the rim stands ``out`` proud along the surface normal and blends back over
``chamfer``; the rim rolls inward by ``lip`` (the return) and a tunnel runs ``depth`` inward to a flat ``liner``
plate. Everything stays one welded mesh; the liner faces can take ``liner_material`` and are saved as the vertex
group ``liner_group``.
"""

from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Vector

from satk.core.errors import SatkError

from . import _util as U
from .attach import boundary_loops, orient_like_neighbours
from .mesh import _fill_uvs


def _poly(cy: float, cz: float, r: float, segs: int, zlow: float) -> list[tuple[float, float]]:
    """The arch outline in (y, z): straight up from below the body, ``segs`` segments over the top, straight down."""
    pts = [(cy + r, zlow)]
    pts += [(cy + r * math.cos(math.pi * k / segs), cz + r * math.sin(math.pi * k / segs)) for k in range(segs + 1)]
    pts.append((cy - r, zlow))
    return pts


def _inside(pt: tuple[float, float], poly: list[tuple[float, float]]) -> bool:
    """Point in the closed polygon (the outline closed along its bottom)."""
    y, z = pt
    inside = False
    n = len(poly)
    for i in range(n):
        (y0, z0), (y1, z1) = poly[i], poly[(i + 1) % n]
        if (z0 > z) != (z1 > z):
            yc = y0 + (z - z0) * (y1 - y0) / (z1 - z0)
            if y < yc:
                inside = not inside
    return inside


def _dist_poly(pt: tuple[float, float], poly: list[tuple[float, float]]) -> float:
    """Distance to the outline (the open polyline, without its closing bottom line)."""
    y, z = pt
    best = math.inf
    for (y0, z0), (y1, z1) in zip(poly, poly[1:]):
        dy, dz = y1 - y0, z1 - z0
        L2 = dy * dy + dz * dz
        t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, ((y - y0) * dy + (z - z0) * dz) / L2))
        best = min(best, math.hypot(y - (y0 + dy * t), z - (z0 + dz * t)))
    return best


def _side_faces(bm, sgn: float, box) -> list:
    (ylo, yhi), (zlo, zhi) = box
    out = []
    for f in bm.faces:
        n = f.normal
        if n.x * sgn < 0.2:
            continue
        c = f.calc_center_median()
        if c.x * sgn <= 0:
            continue
        ys = [v.co.y for v in f.verts]
        zs = [v.co.z for v in f.verts]
        if max(ys) < ylo or min(ys) > yhi or max(zs) < zlo or min(zs) > zhi:
            continue
        out.append(f)
    return out


def _cut(bm, sgn: float, poly: list[tuple[float, float]], tag) -> int:
    """Bisect the side faces along every segment of ``poly`` (only faces whose box touches the segment); the new
    edges get ``tag`` = 1 (split edges inherit it), so the overshoot can be dissolved afterwards."""
    cuts = 0
    for (y0, z0), (y1, z1) in zip(poly, poly[1:]):
        dy, dz = y1 - y0, z1 - z0
        L = math.hypot(dy, dz)
        if L < 1e-9:
            continue
        m = 1e-4
        box = ((min(y0, y1) - m, max(y0, y1) + m), (min(z0, z1) - m, max(z0, z1) + m))
        faces = _side_faces(bm, sgn, box)
        if not faces:
            continue
        geom = list({v for f in faces for v in f.verts}) + U.edges_of(faces) + faces
        ret = bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-6, plane_co=Vector((0.0, y0, z0)),
                                     plane_no=Vector((0.0, dz / L, -dy / L)))
        for g in ret["geom_cut"]:
            if isinstance(g, bmesh.types.BMEdge):
                g[tag] = 1
        cuts += 1
    bm.normal_update()
    return cuts


def _dissolve_overshoot(bm, tag, outlines: list) -> int:
    """Dissolve cut edges that run past their outline (a bisect cuts whole faces, not only along the segment)."""
    extra = []
    for e in bm.edges:
        if not e[tag] or len(e.link_faces) != 2:
            continue
        mid = (e.verts[0].co + e.verts[1].co) / 2
        if all(_dist_poly((mid.y, mid.z), o) > 2e-3 for o in outlines):
            extra.append(e)
    if extra:
        bmesh.ops.dissolve_edges(bm, edges=extra, use_verts=True, use_face_split=False)
    return len(extra)


def _material(o, name: str | None, color=(0.06, 0.06, 0.06, 1.0)) -> int | None:
    if not name:
        return None
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = color
    for i, s in enumerate(o.material_slots):
        if s.material is not None and s.material.name == name:
            return i
    o.data.materials.append(mat)
    return len(o.data.materials) - 1


def _profile(d: float, width: float, chamfer: float) -> float:
    """Share of ``out`` at distance ``d`` from the rim: full over the band, a smooth blend over the chamfer."""
    if d <= width + 1e-6:
        return 1.0
    if chamfer <= 1e-9 or d >= width + chamfer:
        return 0.0
    t = (d - width) / chamfer
    return 1.0 - t * t * (3.0 - 2.0 * t)


def flare(ctx, p: dict) -> dict:
    """Cut a wheel arch into the shell (arch {center, radius} + segments, or select = the opening) with a welded raised band (width, out, chamfer), a return lip and a liner (depth, liner_material).

    ``arch.center`` [x, y, z] in object space (x > 0: the +x side; ``both`` cuts both sides of a full body),
    ``arch.radius`` (vanilla 1.15-1.3 x the wheel radius); ``segments`` over the top (vanilla 5-8). ``out`` = 0 is
    a plain sedan cut; ``out`` 0.02-0.05 with ``width`` 0.05-0.08 an SUV flare. ``lip`` = the return depth (m),
    ``depth`` = how far inside the liner plate sits (``liner`` false leaves the tunnel open). ``sharp_rim`` marks
    the rim edges sharp. Vertex groups ``flare_group`` (the band) and ``liner_group`` (return + liner).
    """
    M = "mesh.flare"
    o = U.mesh_obj(ctx, p, M)
    width = U.num(p, "width", M, 0.06, lo=0.0)
    out = U.num(p, "out", M, 0.03)
    chamfer = U.num(p, "chamfer", M, None, lo=0.0)
    if chamfer is None:
        chamfer = width / 2 if width else 0.03
    lip = U.num(p, "lip", M, 0.04, lo=0.0)
    depth = U.num(p, "depth", M, 0.25, lo=0.0)
    liner = U.flag(p, "liner", M, True)
    sharp = U.flag(p, "sharp_rim", M, True)
    arch = p.get("arch")
    if (arch is None) == (p.get("select") is None):
        raise U.bad(M, "give 'arch' {center, radius} (+ segments) or 'select' (the faces of the opening)")
    fg = U.text(p, "flare_group", M, "arch_flare")
    lg = U.text(p, "liner_group", M, "arch_liner")
    li = _material(o, U.text(p, "liner_material", M))
    sides = []
    if arch is not None:
        if not isinstance(arch, dict) or set(arch) - {"center", "radius"}:
            raise U.bad(M, "'arch' must be {\"center\": [x, y, z], \"radius\": m}")
        c = U.vec(arch, "center", M, required=True)
        R = U.num(arch, "radius", M, required=True, lo=0.01)
        segs = U.integer(p, "segments", M, lo=2, hi=64, required=True)
        sgn = 1.0 if c[0] >= 0 else -1.0
        sides = [sgn, -sgn] if U.flag(p, "both", M, False) else [sgn]
    res: dict = {"object": o.name}
    band_verts, liner_verts, rims = set(), set(), 0
    with U.edit_mesh(o) as bm:
        if not bm.faces:
            raise SatkError("NOT_FOUND", f"{M}: {o.name} has no faces")
        bm.verts.layers.deform.verify()     # now: adding the layer later would invalidate the collected vertices
        zlow = min(v.co.z for v in bm.verts) - 0.05
        for sgn in sides or [None]:
            if sgn is not None:
                cy, cz = c[1], c[2]
                region = R + width + chamfer + 0.05
                outline = _poly(cy, cz, R, segs, zlow)
                cand = _side_faces(bm, sgn, ((cy - region, cy + region), (zlow, cz + region)))
                if not cand:
                    raise SatkError("NOT_FOUND", f"{M}: no side faces of {o.name} around the arch at y {cy:g}, z {cz:g}",
                                    hint="the center is in object space; x picks the side (+x for a half body)")
                rings = [R] + ([R + width, R + width + chamfer] if abs(out) > 1e-9 and width > 0 else [])
                tag = bm.edges.layers.int.get("satk_arch_cut") or bm.edges.layers.int.new("satk_arch_cut")
                for e in bm.edges:
                    e[tag] = 0
                outlines = [_poly(cy, cz, r, segs, zlow) for r in rings]
                for o_ in outlines:
                    _cut(bm, sgn, o_, tag)
                _dissolve_overshoot(bm, tag, outlines)
                bm.edges.layers.int.remove(tag)
                bm.normal_update()
                side = _side_faces(bm, sgn, ((cy - region, cy + region), (zlow, cz + region)))
                gone = [f for f in side if _inside((f.calc_center_median().y, f.calc_center_median().z), outline)]
                if not gone:
                    raise SatkError("NOT_FOUND", f"{M}: the arch does not cover any side face of {o.name}",
                                    hint="check arch.center (object space) and radius")
                normal_in = Vector((-sgn, 0.0, 0.0))
                if abs(out) > 1e-9:
                    vn = {}
                    for f in side:
                        if f in gone:
                            continue
                        for v in f.verts:
                            d = _dist_poly((v.co.y, v.co.z), outline)
                            if _inside((v.co.y, v.co.z), outline) and d > 1e-5:
                                continue
                            k = _profile(d, width, chamfer)
                            if k > 0:
                                vn[v] = k
                    for v, k in vn.items():        # the band stands out sideways, as vanilla flares do
                        v.co += -normal_in * out * k
                        if k >= 0.999:
                            band_verts.add(v)
            else:
                gone = U.require_faces(U.select_faces(o, bm, p.get("select"), M), M, p.get("select"))
                nsum = sum((f.normal * f.calc_area() for f in gone), Vector())
                normal_in = -(nsum.normalized() if nsum.length > 1e-9 else Vector((1.0, 0.0, 0.0)))
                rim_v = {v for e in U.boundary_edges(gone) if any(g not in gone for g in e.link_faces)
                         for v in e.verts}
                if abs(out) > 1e-9 and rim_v:
                    from mathutils.kdtree import KDTree

                    kd = KDTree(len(rim_v))
                    for i, v in enumerate(rim_v):
                        kd.insert(v.co, i)
                    kd.balance()
                    keep = set(gone)
                    for v in {v for f in bm.faces if f not in keep for v in f.verts}:
                        if any(f in keep for f in v.link_faces) and v not in rim_v:
                            continue
                        _co, _i, d = kd.find(v.co)
                        k = _profile(d, width, chamfer)
                        if k > 0:
                            v.co += (v.normal if v.normal.length > 1e-6 else -normal_in).normalized() * out * k
                            if k >= 0.999:
                                band_verts.add(v)
            was_open = {e for e in bm.edges if e.is_boundary}
            bmesh.ops.delete(bm, geom=list(set(gone)), context="FACES")
            bm.normal_update()
            # the rim: the open edges the cut left (kept faces that touched a removed face)
            rim_edges = [e for e in bm.edges if e.is_boundary and e not in was_open]
            if not rim_edges:
                raise SatkError("INTERNAL", f"{M}: the cut left no rim on {o.name}")
            rim_vs = {v for e in rim_edges for v in e.verts}
            chains = [ch for ch in boundary_loops(bm, rim_vs) if len(ch[0]) >= 2]
            if sharp:
                for e in rim_edges:
                    e.smooth = False
            made = []
            for chain, closed in chains:
                rims += 1
                steps = []
                if lip > 0:
                    steps.append(min(lip, depth) if depth > 0 else lip)
                if depth > lip:
                    steps.append(depth)
                prev = chain
                for dd in steps:
                    ring = [bm.verts.new(v.co + normal_in * dd) for v in chain]
                    pairs = list(zip(range(len(chain) - 1), range(1, len(chain)))) + \
                        ([(len(chain) - 1, 0)] if closed else [])
                    for a, b in pairs:
                        try:
                            made.append(bm.faces.new((prev[a], prev[b], ring[b], ring[a])))
                        except ValueError:
                            pass
                    liner_verts.update(ring)
                    prev = ring
                if liner and prev is not chain and len(prev) >= 3:
                    try:
                        made.append(bm.faces.new(prev))
                    except ValueError:
                        pass
            if made:
                orient_like_neighbours(made)
                for f in made:
                    f.smooth = False
                    if li is not None:
                        f.material_index = li
                liner_verts.update(v for f in made for v in f.verts if v not in rim_vs)
                bm.normal_update()
                _fill_uvs(bm, made)
        bm.verts.index_update()
        if band_verts:
            U.save_group(o, bm, [v for v in band_verts if v.is_valid], fg, M)
        if liner_verts:
            U.save_group(o, bm, [v for v in liner_verts if v.is_valid], lg, M)
    res.update(U.counts(o))
    res["rims"] = rims
    if band_verts:
        res["band_verts"] = len(band_verts)
    res["changed"] = [o.name]
    return res


METHODS = {"mesh.flare": flare}
