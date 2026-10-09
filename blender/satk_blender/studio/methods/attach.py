# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``mesh.attach``: put a part ON its parent instead of next to it (vanilla: details touch, shells are welded).

* ``snap`` moves the contact vertices of ``object`` onto the surface of ``to`` (nearest point, or along
  ``direction``), ``offset`` above it; ``rigid`` first moves the whole part by the median gap. The contact is the
  ``select`` region, else every vertex within ``max_dist`` of the target. A mirror stalk, a handle, a rail foot.
* ``weld`` joins ``object`` into ``to`` and merges each open-border vertex of the part into the nearest open-border
  vertex of the target within ``max_dist``: two shells built apart (a greenhouse on a body, a lofted fender) become
  one welded shell.
* ``bridge`` joins and fills the gap between the part's open border and the nearest open border of the target
  with a strip of faces (loops of different sizes are zipped).

Nothing changes when no contact is found (``NOT_FOUND`` names the real gap). For code that joins meshes itself
(``kit.fill``), :func:`weld_boundaries` merges coincident open-border vertices of one object::

    from satk_blender.studio.methods.attach import weld_boundaries
    merged = weld_boundaries(slot_obj, dist=1e-4)     # -> number of vertices merged
"""

from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from satk.core.errors import SatkError

from . import _util as U

__all__ = ["weld_boundaries", "target_bvh", "boundary_loops", "zip_loops", "orient_like_neighbours", "METHODS"]


def weld_boundaries(o, dist: float = 1e-4, *, verts=None) -> int:
    """Merge the open-border vertices of mesh object ``o`` that lie within ``dist`` of each other (seams left by a
    join of parts cut from one shell). ``verts`` (vertex indices) limits the candidates. Returns the number merged."""
    with U.edit_mesh(o) as bm:
        bm.verts.ensure_lookup_table()
        if verts is not None:
            cand = [bm.verts[i] for i in verts if 0 <= i < len(bm.verts) and bm.verts[i].is_boundary]
        else:
            cand = [v for v in bm.verts if v.is_boundary]
        n0 = len(bm.verts)
        if cand:
            bmesh.ops.remove_doubles(bm, verts=cand, dist=float(dist))
        return n0 - len(bm.verts)


def target_bvh(t):
    """BVH of the evaluated mesh of ``t`` (modifiers applied) in world space."""
    dg = bpy.context.evaluated_depsgraph_get()
    te = t.evaluated_get(dg)
    me = te.to_mesh()
    try:
        mw = t.matrix_world
        verts = [mw @ v.co for v in me.vertices]
        polys = [tuple(p.vertices) for p in me.polygons]
    finally:
        te.to_mesh_clear()
    if not polys:
        raise SatkError("NOT_FOUND", f"{t.name} has no faces to attach to")
    return BVHTree.FromPolygons(verts, polys)


def _hit(bvh, P: Vector, direction: Vector | None, max_dist: float):
    """``(location, normal, distance)`` of the target surface from ``P`` (nearest, or along +-direction)."""
    if direction is None:
        loc, nrm, _i, d = bvh.find_nearest(P)
        return (loc, nrm, d) if loc is not None else None
    best = None
    for sgn in (1.0, -1.0):
        loc, nrm, _i, d = bvh.ray_cast(P, direction * sgn, max(max_dist, 1e-6) * 4)
        if loc is not None and (best is None or d < best[2]):
            best = (loc, nrm, d)
    return best


def boundary_loops(bm, verts=None) -> list[tuple[list, bool]]:
    """Ordered open-border vertex chains of ``bm`` as ``[(verts, closed)]`` (only borders made of ``verts``)."""
    allowed = set(verts) if verts is not None else None
    edges = [e for e in bm.edges if e.is_boundary and (allowed is None or all(v in allowed for v in e.verts))]
    nb: dict = {}
    for e in edges:
        a, b = e.verts
        nb.setdefault(a, []).append(b)
        nb.setdefault(b, []).append(a)
    seen: set = set()
    out = []
    starts = [v for v, n in nb.items() if len(n) == 1] + list(nb)
    for s in starts:
        if s in seen:
            continue
        chain = [s]
        seen.add(s)
        prev, cur = None, s
        while True:
            nxt = [w for w in nb[cur] if w is not prev and w not in seen]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            seen.add(cur)
            chain.append(cur)
        closed = len(chain) > 2 and s in nb.get(chain[-1], [])
        out.append((chain, closed))
    return out


def _params(chain: list[Vector], closed: bool) -> list[float]:
    seq = chain + ([chain[0]] if closed else [])
    acc = [0.0]
    for a, b in zip(seq, seq[1:]):
        acc.append(acc[-1] + (b - a).length)
    tot = acc[-1] or 1.0
    return [x / tot for x in acc]


def zip_loops(bm, A: list, B: list, closed: bool) -> list:
    """Faces between two vertex chains walked by arc length (quads where both advance, triangles elsewhere)."""
    pa = _params([v.co for v in A], closed)
    pb = _params([v.co for v in B], closed)
    a_seq = A + ([A[0]] if closed else [])
    b_seq = B + ([B[0]] if closed else [])
    i = j = 0
    faces = []
    while i < len(a_seq) - 1 or j < len(b_seq) - 1:
        na = pa[i + 1] if i < len(a_seq) - 1 else math.inf
        nbp = pb[j + 1] if j < len(b_seq) - 1 else math.inf
        if abs(na - nbp) < 0.15 * min(1.0 / max(1, len(A)), 1.0 / max(1, len(B))) and na < math.inf:
            vs = [a_seq[i], a_seq[i + 1], b_seq[j + 1], b_seq[j]]
            i, j = i + 1, j + 1
        elif na <= nbp:
            vs = [a_seq[i], a_seq[i + 1], b_seq[j]]
            i += 1
        else:
            vs = [a_seq[i], b_seq[j + 1], b_seq[j]]
            j += 1
        if len(set(vs)) < 3:
            continue
        uniq = []
        for v in vs:
            if v not in uniq:
                uniq.append(v)
        try:
            faces.append(bm.faces.new(uniq))
        except ValueError:
            continue
    return faces


def _agrees(f, g, e) -> bool:
    """``f`` and ``g`` wind consistently across their shared edge ``e`` (they walk it in opposite directions)."""
    fl = next(x for x in f.loops if x.edge is e)
    gl = next(x for x in g.loops if x.edge is e)
    return fl.vert is not gl.vert


def orient_like_neighbours(faces) -> None:
    """Give new ``faces`` the winding of the existing faces they touch, spreading across the new region."""
    new = [f for f in faces if f.is_valid]
    todo = set(new)
    done: set = set()
    frontier = []
    # never flip a face while walking an edge's face list: the flip rewires that list (an endless walk)
    for f in new:
        for e in list(f.edges):
            old = [g for g in e.link_faces if g is not f and g not in todo]
            if old:
                if not _agrees(f, old[0], e):
                    f.normal_flip()
                done.add(f)
                frontier.append(f)
                break
    if not frontier and new:
        done.add(new[0])
        frontier.append(new[0])
    while frontier:
        g = frontier.pop()
        pending = []
        for e in list(g.edges):
            for f in list(e.link_faces):
                if f in todo and f not in done:
                    done.add(f)
                    pending.append((f, e))
        for f, e in pending:
            if not _agrees(f, g, e):
                f.normal_flip()
            frontier.append(f)


def _join(t, o) -> int:
    """Join ``o`` into ``t`` (``o`` is gone afterwards); returns the vertex count ``t`` had before (the part's
    vertices follow)."""
    n0 = len(t.data.vertices)
    with bpy.context.temp_override(active_object=t, object=t, selected_objects=[t, o],
                                   selected_editable_objects=[t, o]):
        r = bpy.ops.object.join()
    if "FINISHED" not in r:
        raise SatkError("EXTERNAL_TOOL", f"mesh.attach: Blender could not join {o.name} into {t.name}")
    return n0


def attach(ctx, p: dict) -> dict:
    """Attach a part to a target: mode snap (contact vertices onto its surface), weld (join + merge open borders within max_dist) or bridge (join + fill the gap between open borders).

    ``object`` = the part, ``to`` = the target (its evaluated surface for snap). ``select`` picks the part's
    contact faces (default: every vertex within ``max_dist`` of the target); only contact vertices within
    ``max_dist`` move (the rest are counted as ``far``). ``offset`` (m) keeps them above the surface; ``direction``
    [x, y, z] snaps along that line instead of to the nearest point; ``rigid`` first moves the whole part by the
    median gap of its contact. Vertices on a MIRROR plane stay on it. weld/bridge join the part into ``to`` (the
    part object goes away); ``boundary_only`` (default true) merges only into the target's open-border vertices.
    """
    M = "mesh.attach"
    o = U.mesh_obj(ctx, p, M)
    t = ctx.obj(U.text(p, "to", M, required=True))
    if t.type != "MESH" or t is o:
        raise U.bad(M, f"'to' must be another MESH object, got {t.name} ({t.type})")
    mode = U.text(p, "mode", M, "snap", choices=("snap", "weld", "bridge"))
    max_dist = U.num(p, "max_dist", M, 0.05, lo=0.0)
    offset = U.num(p, "offset", M, 0.0)
    dv = U.vec(p, "direction", M)
    direction = Vector(dv).normalized() if dv is not None and Vector(dv).length > 1e-9 else None
    bpy.context.view_layer.update()         # objects placed earlier in the same batch: fresh matrix_world
    mw, inv = o.matrix_world.copy(), o.matrix_world.inverted()
    if mode == "snap":
        return _snap(ctx, p, M, o, t, mw, inv, max_dist, offset, direction)
    if mode == "weld":
        return _weld(ctx, p, M, o, t, mw, max_dist)
    return _bridge(ctx, p, M, o, t, mw, max_dist)


def _mirror_axes(o) -> list[int]:
    """Axes of the object's MIRROR modifiers: vertices on those planes must stay on them."""
    out = set()
    for m in o.modifiers:
        if m.type == "MIRROR":
            out |= {i for i in range(3) if m.use_axis[i]}
    return sorted(out)


def _snap(ctx, p, M, o, t, mw, inv, max_dist, offset, direction) -> dict:
    bvh = target_bvh(t)
    rigid = U.flag(p, "rigid", M, False)
    lock = _mirror_axes(o)
    reach = math.inf if direction is None else max(max_dist, 1e-6) * 20
    with U.edit_mesh(o) as bm:
        if p.get("select") is not None:
            faces, verts = U.select_verts(o, bm, p.get("select"), M)
            U.require_faces(faces, M, p.get("select"))
        else:
            verts = []
            nearest = math.inf
            for v in bm.verts:
                h = _hit(bvh, mw @ v.co, direction, max_dist)
                if h is not None:
                    nearest = min(nearest, h[2])
                    if h[2] <= max_dist:
                        verts.append(v)
            if not verts:
                gap = f"{nearest:.3f} m" if nearest < math.inf else "out of reach"
                raise SatkError("NOT_FOUND", f"{M}: no vertex of {o.name} is within max_dist {max_dist:g} m of "
                                f"{t.name} (nearest: {gap})", data={"gap_m": round(nearest, 4)} if nearest < math.inf
                                else None, hint="move the part closer, raise max_dist, or give 'select' (the "
                                                "contact faces) with rigid=true")
        first = {v: _hit(bvh, mw @ v.co, direction, reach) for v in verts}
        shift = None
        if rigid:   # the whole part moves by the median gap of its contact first
            ds = sorted((h[0] + h[1] * offset - mw @ v.co for v, h in first.items() if h is not None),
                        key=lambda d: d.length)
            if ds:
                shift = ds[len(ds) // 2]
                local = inv.to_3x3() @ shift
                for i in lock:
                    local[i] = 0.0
                for v in bm.verts:
                    v.co += local
        moved, far, gaps = 0, 0, []
        for v in verts:
            h = _hit(bvh, mw @ v.co, direction, max_dist)
            if h is None or h[2] > max_dist:
                far += 1
                continue
            new = inv @ (h[0] + h[1] * offset)
            for i in lock:
                if abs(v.co[i]) < 1e-5:
                    new[i] = v.co[i]
            g0 = first.get(v)
            gaps.append(g0[2] if g0 is not None else h[2])
            v.co = new
            moved += 1
        if not moved:
            raise SatkError("NOT_FOUND", f"{M}: the contact of {o.name} is farther than max_dist {max_dist:g} m from "
                            f"{t.name}", hint="raise max_dist or use rigid=true to move the part first")
        if moved == len(bm.verts) and moved > 4:
            ctx.warn(f"STYLE: all {moved} vertices of {o.name} were snapped: the part is flattened onto {t.name}; "
                     "select only its contact end (the selector works in object space)")
    out = {"object": o.name, "to": t.name, "mode": "snap", "verts": moved,
           "gap_mm": round(max(gaps) * 1000, 1), "changed": [o.name]}
    if shift is not None:
        out["moved_mm"] = round(shift.length * 1000, 1)
    if far:
        out["far"] = far
    return out


def _world_boundary(obj) -> tuple[list[int], list[Vector], bool]:
    """Open-border vertex indices and world positions of ``obj``'s mesh, and whether it had open borders at all."""
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        idx = [v.index for v in bm.verts if v.is_boundary]
        mw = obj.matrix_world
        return idx, [mw @ bm.verts[i].co for i in idx], bool(idx)
    finally:
        bm.free()


def _weld(ctx, p, M, o, t, mw, max_dist) -> dict:
    boundary_only = U.flag(p, "boundary_only", M, True)
    pidx, ppos, _ = _world_boundary(o)
    if p.get("select") is not None:
        bm = bmesh.new()
        try:
            bm.from_mesh(o.data)
            bm.verts.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
            _faces, verts = U.select_verts(o, bm, p.get("select"), M)
            want = {v.index for v in verts}
        finally:
            bm.free()
        keep = [k for k, i in enumerate(pidx) if i in want]
        pidx, ppos = [pidx[k] for k in keep], [ppos[k] for k in keep]
    if not pidx:
        raise SatkError("NOT_FOUND", f"{M}: {o.name} has no open border to weld", hint="weld joins open shells; for a "
                        "closed part use mode snap (it touches) or mesh.delete its contact faces first")
    if boundary_only:
        tidx, tpos, _ = _world_boundary(t)
    else:
        tm = t.matrix_world
        tidx = list(range(len(t.data.vertices)))
        tpos = [tm @ v.co for v in t.data.vertices]
    if not tidx:
        raise SatkError("NOT_FOUND", f"{M}: {t.name} has no open border to weld into",
                        hint="boundary_only=false merges into any target vertex")
    kd = KDTree(len(tpos))
    for i, co in enumerate(tpos):
        kd.insert(co, i)
    kd.balance()
    pairs, nearest = [], math.inf
    for i, co in zip(pidx, ppos):
        _c, j, d = kd.find(co)
        nearest = min(nearest, d)
        if d <= max_dist:
            pairs.append((i, tidx[j], d))
    if not pairs:
        raise SatkError("NOT_FOUND", f"{M}: no open-border vertex of {o.name} is within max_dist {max_dist:g} m of "
                        f"{t.name}'s border (nearest: {nearest:.3f} m)", data={"gap_m": round(nearest, 4)},
                        hint="snap the part first (mode snap), or raise max_dist")
    if any(m.type == "MIRROR" for m in t.modifiers) and any(co.x < -1e-4 for co in ppos):
        ctx.warn(f"STYLE: {o.name} lies at x < 0 but {t.name} is a mirrored half; the weld lands on the base half")
    joined = o.name
    n0 = _join(t, o)
    with U.edit_mesh(t) as bm:
        bm.verts.ensure_lookup_table()
        tmap = {}
        for i, j, _d in pairs:
            a, b = bm.verts[n0 + i], bm.verts[j]
            if a is not b:
                tmap[a] = b
        bmesh.ops.weld_verts(bm, targetmap=tmap)
    return {"object": t.name, "joined": joined, "mode": "weld", "merged": len(pairs),
            "unmatched": len(pidx) - len(pairs), "gap_mm": round(max(d for *_x, d in pairs) * 1000, 1),
            "changed": [t.name]}


def _bridge(ctx, p, M, o, t, mw, max_dist) -> dict:
    # find the loops in world space first: nothing changes when no pair is close enough
    def loops_world(obj):
        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            out = []
            for chain, closed in boundary_loops(bm):
                out.append(([v.index for v in chain], [obj.matrix_world @ v.co for v in chain], closed))
            return out
        finally:
            bm.free()

    pl, tl = loops_world(o), loops_world(t)
    if not pl:
        raise SatkError("NOT_FOUND", f"{M}: {o.name} has no open border to bridge",
                        hint="mesh.delete the faces where it should meet the target")
    if not tl:
        raise SatkError("NOT_FOUND", f"{M}: {t.name} has no open border to bridge to")
    best = None
    for pi, (pidx, ppos, pc) in enumerate(pl):
        kd = KDTree(sum(len(x[1]) for x in tl))
        owner = []
        for ti, (_tidx, tpos, _tc) in enumerate(tl):
            for k, co in enumerate(tpos):
                kd.insert(co, len(owner))
                owner.append((ti, k))
        kd.balance()
        votes: dict = {}
        dsum = 0.0
        for co in ppos:
            _c, j, d = kd.find(co)
            dsum += d
            votes[owner[j][0]] = votes.get(owner[j][0], 0) + 1
        ti = max(votes, key=votes.get)
        gap = dsum / len(ppos)
        if best is None or gap < best[0]:
            best = (gap, pi, ti)
    gap, pi, ti = best
    if gap > max_dist:
        raise SatkError("NOT_FOUND", f"{M}: the open borders of {o.name} and {t.name} are {gap:.3f} m apart "
                        f"(max_dist {max_dist:g})", data={"gap_m": round(gap, 4)}, hint="raise max_dist")
    pidx, ppos, pclosed = pl[pi]
    tidx, tpos, tclosed = tl[ti]
    # the target span that faces the part: all of a closed target loop of similar length, else the stretch
    # between the points nearest to the part chain's ends
    if not (pclosed and tclosed):
        def nearest(co):
            return min(range(len(tpos)), key=lambda k: (tpos[k] - co).length)
        a, b = nearest(ppos[0]), nearest(ppos[-1])
        if tclosed:
            fwd = [(a + k) % len(tidx) for k in range((b - a) % len(tidx) + 1)]
            back = [(a - k) % len(tidx) for k in range((a - b) % len(tidx) + 1)]
            span = fwd if len(fwd) <= len(back) else back
        else:
            span = list(range(a, b + 1)) if a <= b else list(range(a, b - 1, -1))
        tidx = [tidx[k] for k in span]
        tpos = [tpos[k] for k in span]
        closed = False
    else:
        k0 = min(range(len(tpos)), key=lambda k: (tpos[k] - ppos[0]).length)
        tidx = tidx[k0:] + tidx[:k0]
        tpos = tpos[k0:] + tpos[:k0]
        if len(tpos) > 2 and (tpos[-1] - ppos[1]).length < (tpos[1] - ppos[1]).length:
            tidx = [tidx[0]] + tidx[1:][::-1]
            tpos = [tpos[0]] + tpos[1:][::-1]
        closed = True
    joined = o.name
    n0 = _join(t, o)
    with U.edit_mesh(t) as bm:
        bm.verts.ensure_lookup_table()
        A = [bm.verts[n0 + i] for i in pidx]
        B = [bm.verts[i] for i in tidx]
        faces = zip_loops(bm, A, B, closed)
        orient_like_neighbours(faces)
        for f in faces:
            f.smooth = True
    return {"object": t.name, "joined": joined, "mode": "bridge", "faces": len(faces),
            "gap_mm": round(gap * 1000, 1), "changed": [t.name]}


METHODS = {"mesh.attach": attach}
