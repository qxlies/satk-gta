# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Review regions (contract K8) as cameras: region definitions (``data/kit/regions/<kind>.json``) -> views.

A region has ``cameras`` (``az`` degrees from +Y towards +X or ``"out"`` = the side its frame is on, ``az_off``,
``el``, ``ortho``, ``fov``; ``inside`` + ``at`` = a camera standing inside the model at a box position) and
what they frame: ``box`` (fractions of the reference box per axis: x 0 = left/-X .. 1 = right/+X, y 0 = rear ..
1 = front, z 0 = bottom .. 1 = top) or ``frames`` (name patterns of frames: one view per matching frame, framing
the geometry within ``radius_h`` x the reference height of it). ``ref`` (file or region) = frame patterns whose
geometry is the reference box (default: every visible mesh). Render options: ``hide`` (object patterns),
``glass: hide``, ``ground: false``, ``states`` and ``leak: false`` (not a leak camera).
"""

from __future__ import annotations

import fnmatch
import math

import bpy

__all__ = ["expand", "objects_matching"]


def _base(o) -> str:
    return str(o.get("satk_frame") or o.name).split(".")[0].lower()


def objects_matching(objs, patterns) -> list:
    pats = [str(p).lower() for p in (patterns or [])]
    return [o for o in objs if any(fnmatch.fnmatchcase(_base(o), p) for p in pats)]


def _points(objs):
    import numpy as np

    from . import api

    pts = api.frame_points([o for o in objs if o.type == "MESH" and not o.hide_render], max_points=150_000)
    return np.concatenate(pts) if pts else np.zeros((0, 3))


def surface_points(objs, max_points: int = 200_000):
    """World points spread over the triangles of the visible meshes (vertices, and a barycentric grid of points
    inside every triangle): a box region sees the faces that pass through it, not only the vertices in it (a bin
    whose walls run from the base to the rim has no vertex at mid height)."""
    import numpy as np

    dg = bpy.context.evaluated_depsgraph_get()
    out = []
    budget = max_points
    bary = np.array([[1 / 3, 1 / 3, 1 / 3], [0.6, 0.2, 0.2], [0.2, 0.6, 0.2], [0.2, 0.2, 0.6],
                     [0.45, 0.45, 0.1], [0.1, 0.45, 0.45], [0.45, 0.1, 0.45]])
    for o in objs:
        if o.type != "MESH" or o.hide_render:
            continue
        ev = o.evaluated_get(dg)
        try:
            me = ev.to_mesh()
        except RuntimeError:
            continue
        try:
            me.calc_loop_triangles()
            n, t = len(me.vertices), len(me.loop_triangles)
            if not n or not t:
                continue
            mw = np.array(ev.matrix_world, dtype=np.float64)
            co = np.empty(n * 3, dtype=np.float64)
            me.vertices.foreach_get("co", co)
            V = co.reshape(n, 3) @ mw[:3, :3].T + mw[:3, 3]
            tri = np.empty(t * 3, dtype=np.int64)
            me.loop_triangles.foreach_get("vertices", tri)
            T = V[tri.reshape(t, 3)]
            k = len(bary) if t * len(bary) <= budget else 1
            S = np.einsum("bk,tkd->tbd", bary[:k], T).reshape(-1, 3)
            out.append(np.concatenate([V, S]))
            budget -= len(S)
        finally:
            ev.to_mesh_clear()
    return np.concatenate(out) if out else np.zeros((0, 3))


def room_anchors(objs, *, max_rooms: int = 6, cell: float = 1.0, eye: float = 1.6, min_area: float = 3.0):
    """Camera positions inside the rooms of an interior: floor faces (facing up) with a ceiling above them
    (a face facing down within 8 m), grouped by storey and by touching 1 m cells; one position per room at eye
    height over a floor cell near the room's middle, largest rooms first."""
    import numpy as np
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    dg = bpy.context.evaluated_depsgraph_get()
    verts: list = []
    tris: list = []
    for o in objs:
        if o.type != "MESH" or o.hide_render:
            continue
        ev = o.evaluated_get(dg)
        try:
            me = ev.to_mesh()
        except RuntimeError:
            continue
        try:
            me.calc_loop_triangles()
            n, t = len(me.vertices), len(me.loop_triangles)
            if not n or not t:
                continue
            mw = np.array(ev.matrix_world, dtype=np.float64)
            co = np.empty(n * 3, dtype=np.float64)
            me.vertices.foreach_get("co", co)
            V = co.reshape(n, 3) @ mw[:3, :3].T + mw[:3, 3]
            tri = np.empty(t * 3, dtype=np.int64)
            me.loop_triangles.foreach_get("vertices", tri)
            base = len(verts)
            verts.extend(map(tuple, V.tolist()))
            tris.extend((tri.reshape(t, 3) + base).tolist())
        finally:
            ev.to_mesh_clear()
    if not tris:
        return []
    V = np.array(verts)
    T = np.array(tris)
    A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    N = np.cross(B - A, C - A)
    area = np.linalg.norm(N, axis=1) / 2
    ok = area > 1e-6
    nz = np.where(ok, N[:, 2] / np.maximum(2 * area, 1e-12), 0.0)
    floor = ok & (nz > 0.7) & (area > 0.01)
    if not floor.any():
        return []
    bvh = BVHTree.FromPolygons(verts, tris, all_triangles=True)
    cen = (A + B + C)[floor] / 3.0
    farea = area[floor]
    covered = []
    for p_, a_ in zip(cen.tolist(), farea.tolist()):
        hit = bvh.ray_cast(Vector((p_[0], p_[1], p_[2] + 0.05)), Vector((0.0, 0.0, 1.0)), 8.0)
        if hit[0] is not None and hit[1] is not None and hit[1][2] < -0.3:
            covered.append((p_[0], p_[1], p_[2], hit[0][2] - p_[2], a_))
    if not covered:
        return []
    F = np.array(covered)
    # storeys: floor heights split where they jump by more than 1.5 m
    zs = np.sort(F[:, 2])
    cuts = [zs[0] - 1.0] + [float((a + b) / 2) for a, b in zip(zs[:-1], zs[1:]) if b - a > 1.5] + [zs[-1] + 1.0]
    rooms = []
    for z0, z1 in zip(cuts[:-1], cuts[1:]):
        S = F[(F[:, 2] > z0) & (F[:, 2] <= z1)]
        if not len(S):
            continue
        cells: dict = {}
        for x, y, z, h, a_ in S.tolist():
            k = (int(np.floor(x / cell)), int(np.floor(y / cell)))
            c = cells.setdefault(k, [0.0, 0.0, 0.0, 0.0, 0.0])
            c[0] += x * a_
            c[1] += y * a_
            c[2] += z * a_
            c[3] = max(c[3], h)
            c[4] += a_
        seen: set = set()
        for k0 in sorted(cells):
            if k0 in seen:
                continue
            stack, members = [k0], []
            seen.add(k0)
            while stack:
                k = stack.pop()
                members.append(k)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nk = (k[0] + dx, k[1] + dy)
                    if nk in cells and nk not in seen:
                        seen.add(nk)
                        stack.append(nk)
            tot = sum(cells[m][4] for m in members)
            if tot < min_area:
                continue
            mx = sum(cells[m][0] for m in members) / tot
            my = sum(cells[m][1] for m in members) / tot
            # the most open cell near the room's middle (an L-shaped room's middle may lie in a wall): the largest
            # free distance to the walls in four directions at eye height, among the cells nearest the middle
            near = sorted(members, key=lambda m: (cells[m][0] / cells[m][4] - mx) ** 2
                          + (cells[m][1] / cells[m][4] - my) ** 2)[:40]

            def spot(m):
                c = cells[m]
                fz = c[2] / c[4]
                h = max(0.5, min(eye, 0.6 * c[3]))
                return [c[0] / c[4], c[1] / c[4], fz + h]

            def room_at(m):
                pos = spot(m)
                free = []
                for d in ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)):
                    hit = bvh.ray_cast(Vector(pos), Vector((d[0], d[1], 0.0)), 50.0)
                    free.append(hit[3] if hit[0] is not None else 50.0)
                return min(free)

            best = max(near, key=room_at)
            rooms.append((tot, spot(best)))
    rooms.sort(key=lambda r: -r[0])
    return [r[1] for r in rooms[:max_rooms]]


def _cube(c, r):
    import numpy as np

    return np.array([[c[0] + sx * r, c[1] + sy * r, c[2] + sz * r] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])


def expand(regions: list[dict], objs, *, ref=None, frame_objs=None) -> tuple[list[dict], list[str]]:
    """Views of ``regions`` for the subject ``objs`` (its visible meshes and its dummies ``frame_objs``).

    Returns ``(views, notes)``; a view is ``{"label", "region", "az", "el", "ortho", "fov"?, "points": [array],
    "inside"?, "pos"?, "hide"?, "glass"?, "ground", "states"?, "leak"}``.
    """
    import numpy as np

    meshes = [o for o in objs if o.type == "MESH"]
    P = _points(meshes)
    notes: list[str] = []
    if not len(P):
        return [], ["NOT_FOUND: the subject has no visible geometry to frame"]
    SP = None                                   # surface samples, made once for the box regions
    ROOMS = None                                # camera positions inside the rooms (regions with "rooms")
    every = list(objs) + [o for o in (frame_objs or []) if o not in objs]
    out: list[dict] = []
    for reg in regions:
        name = str(reg["name"])
        rpats = reg.get("ref") or ref
        rp = _points(objects_matching(meshes, rpats)) if rpats else P
        if not len(rp):
            rp = P
        lo, hi = rp.min(axis=0), rp.max(axis=0)
        span = np.maximum(hi - lo, 1e-3)
        common = {"region": name, "ground": bool(reg.get("ground", True)), "leak": bool(reg.get("leak", True))}
        if reg.get("leak_inside") is False:
            common["leak_inside"] = False
        for k in ("hide", "glass", "states"):
            if reg.get(k):
                common[k] = reg[k]
        targets: list[tuple[str, object, np.ndarray]] = []  # (suffix, frame object or None, framing points)
        if reg.get("frames"):
            fr = sorted(objects_matching(every, reg["frames"]), key=_base)
            seen = set()
            radius = float(reg.get("radius_h") or 0.4) * float(span[2])
            for f in fr:
                b = _base(f)
                if b in seen:
                    continue
                seen.add(b)
                c = np.array(f.matrix_world.translation)
                near = P[np.linalg.norm(P - c, axis=1) <= radius]
                pts = near if len(near) >= 8 else _cube(c, radius * 0.7)
                targets.append((b, f, pts))
            if not targets:
                notes.append(f"NOT_FOUND: region {name}: no frame {', '.join(reg['frames'])}")
                continue
        else:
            box = reg.get("box") or {}
            a = np.array([box.get(k, [0.0, 1.0])[0] for k in "xyz"], dtype=float)
            b = np.array([box.get(k, [0.0, 1.0])[1] for k in "xyz"], dtype=float)
            blo, bhi = lo + span * a - 1e-4, lo + span * b + 1e-4
            if SP is None:
                SP = surface_points(meshes)
                if not len(SP):
                    SP = P
            inside = SP[np.all((SP >= blo) & (SP <= bhi), axis=1)]
            if len(inside) < 4:
                if any(c.get("inside") for c in reg.get("cameras") or []):
                    inside = np.array([lo, hi])
                else:
                    # nothing of the model passes through the box: the region does not apply (a reviewer skips it)
                    notes.append(f"SKIPPED: region {name}: the model has no surface inside its box")
                    continue
            targets.append(("", None, inside))
        if reg.get("rooms") and any(c.get("inside") for c in reg.get("cameras") or []):
            if ROOMS is None:
                ROOMS = room_anchors(meshes)
                notes.append(f"NOTE: {len(ROOMS)} room(s) found for the inside cameras" if ROOMS else
                             "NOTE: no room found (no floor under a ceiling): the inside cameras stand at the "
                             "middle of the model")
        rooms = ROOMS if reg.get("rooms") else None
        for suffix, f, pts in targets:
            for ci, c in enumerate(reg.get("cameras") or []):
                if c.get("inside") and rooms:
                    for ri, pos in enumerate(rooms):
                        v = dict(common, az=float(c.get("az", 0.0)), el=float(c.get("el", 0.0)),
                                 ortho=False, points=[pts], inside=True, pos=[float(x) for x in pos],
                                 fov=float(c.get("fov") or 80.0))
                        label = f"{name}.r{ri + 1}"
                        if len(reg.get("cameras") or []) > 1:
                            label += f".{ci + 1}"
                        v["label"] = label
                        out.append(v)
                    continue
                az = c.get("az", 45.0)
                if az in ("out", "in"):
                    x = float(f.matrix_world.translation.x) if f is not None else -1.0
                    side = -1.0 if x <= 0 else 1.0
                    if az == "in":
                        side = -side
                    az = side * (90.0 - float(c.get("az_off", 0.0)))
                v = dict(common, az=float(az), el=float(c.get("el", 15.0)), ortho=bool(c.get("ortho", False)),
                         points=[pts])
                if c.get("fov"):
                    v["fov"] = float(c["fov"])
                if c.get("slit") is not None:
                    v["slit"] = bool(c["slit"])        # this camera looks for slits whatever the kind's rule
                if c.get("inside"):
                    at = np.array(c.get("at") or [0.5, 0.5, 0.35], dtype=float)
                    v["inside"] = True
                    v["pos"] = [float(x) for x in (lo + span * at)]
                    v["fov"] = float(c.get("fov") or 80.0)
                label = name + (f".{suffix}" if suffix else "")
                if len(reg.get("cameras") or []) > 1:
                    label += f".{ci + 1}"
                v["label"] = label
                out.append(v)
    return out, notes


def frame_note(view: dict) -> str:
    """A short text of a view for the index (az/el)."""
    return f"az {view['az']:g} el {view['el']:g}" + (" inside" if view.get("inside") else "") + \
        (" ortho" if view.get("ortho") else "")


def view_dir(az: float, el: float):
    a, e = math.radians(az), math.radians(el)
    return (math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e))
