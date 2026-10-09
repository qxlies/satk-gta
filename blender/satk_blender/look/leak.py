# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""The light-leak check (contract K8): where does a model let the background through?

Every pixel of a review camera is a ray through the visible meshes (a BVH of the evaluated meshes in world
space). The march along the ray follows the engine: glass and alpha-textured materials let the ray through;
a back face stops it on a vehicle (the engine draws vehicles double-sided) and is skipped on everything else
(world objects, peds and weapons are drawn with back faces culled, unless the IDE flag 0x200000 is set).
The pixel classes (:data:`CLASSES`):

* ``front`` / ``back`` - the first visible surface faces the camera / faces away (a vehicle shows the inside
  of a shell: missing liner or inner panel, flipped faces);
* ``glass`` - only glass along the ray (a window: an opening by design);
* ``culled`` - culled back faces only: the game shows the background where geometry is modelled facing away;
* ``through`` - nothing hit, but the ray entered the model's box above its floor: it passed between parts;
* ``under`` - nothing hit, below the floor (under a car, between the legs of a bench);
* ``outside`` - the ray misses the model's box;
* ``escape`` - an inside camera (a room) whose ray leaves the shell.

Gaps (:func:`clusters`): connected groups of ``through`` pixels that never touch ``outside`` (enclosed by the
model: a see-through arch, a crack in a shell), *slits*: narrow lanes of background between two surfaces at about
the same depth (a seam between parts, a wing root, a gap under a roof slab; found by closing the model's
silhouette, at most ``slit_max_m`` wide, longer than wide, never next to a wheel), groups of ``culled`` pixels (a
hole that shows the back of a shell blocks; the back of a one-sided flat plane - a fence, a facade card - seen from
behind is information), groups of ``back`` pixels (vehicles, when the kind checks them) and groups of ``escape``
pixels. Back faces met at a grazing angle (an eave seen edge-on) are passed, not counted. Each gap has its view,
pixel box, area, an approximate 3D point (the depth of the surfaces around it) and the nearest object.
The cell image is the model black on a bright background, gaps painted in signal colours with numbered boxes.
"""

from __future__ import annotations

import math
import os
from collections import deque

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

__all__ = ["CLASSES", "Scene", "build", "camera_rays", "classify", "clusters", "cell_image", "run_view", "merge"]

OUTSIDE, UNDER, THROUGH, FRONT, BACK, GLASS, CULLED, ESCAPE = range(8)
CLASSES = ("outside", "under", "through", "front", "back", "glass", "culled", "escape")
#: Gap kinds by pixel class.
GAP_KIND = {THROUGH: "gap", CULLED: "see_through", BACK: "inside", ESCAPE: "escape"}
#: An inner surface at least this far (m) behind an opening is listed (information below the kind's blocking rule).
INFO_DEPTH = 0.25
#: A back face met at an angle with |cos| below this (about 7 degrees off edge-on) is passed (eave speckle).
GRAZE_COS = 0.12
#: A mesh piece thinner than this (m, along its thinnest principal axis) is a flat one-sided plane.
FLAT_M = 0.02
#: Culled back faces this far (m; at most a tenth of the model's size) behind the rim of the opening they are seen
#: through are a hole into a shell (blocking); nearer, they are the back of an open surface seen from behind (an
#: eave, a terrain skirt: information).
HOLE_DEPTH = 0.3
#: A front face within this distance (m) of a back face the ray met is the same card drawn double-sided.
COINCIDENT_M = 2e-4
#: Slits: at most this wide (m); the silhouette is closed over this radius (m) to find them.
SLIT_MAX_M = 0.035
SLIT_CLOSE_M = 0.02
#: Cell colours (8-bit sRGB): the model black, the background bright, gaps in signal colours.
_COL = {OUTSIDE: (236, 242, 250), UNDER: (196, 204, 214), THROUGH: (236, 242, 250), FRONT: (18, 18, 20),
        BACK: (62, 40, 28), GLASS: (34, 52, 92), CULLED: (236, 242, 250), ESCAPE: (236, 242, 250)}
_MARK = {"gap": (235, 20, 20), "see_through": (230, 0, 200), "inside": (255, 140, 0), "escape": (235, 20, 20)}
_EPS = 1e-4
_MAX_STEPS = 24


def _base(name: str) -> str:
    return name.split(".")[0].lower()


def _alpha_material(mat) -> bool:
    """Glass (material alpha below 1) or a texture with transparent texels: the ray passes through."""
    if mat is None:
        return False
    try:
        if float(mat.diffuse_color[3]) < 0.995:
            return True
    except (AttributeError, IndexError, TypeError):
        pass
    nt = mat.node_tree if getattr(mat, "use_nodes", True) else None
    if nt is None:
        return False
    for n in nt.nodes:
        if n.type == "BSDF_PRINCIPLED":
            a = n.inputs.get("Alpha")
            if a is not None and not a.is_linked and float(a.default_value) < 0.995:
                return True
    from . import materials as M

    img = M._image_node(nt)  # noqa: SLF001 - the look's own image lookup (first non-look image node)
    if img is not None and img.image is not None and img.image.alpha_mode != "NONE":
        return M._has_alpha(img.image)  # noqa: SLF001
    return False


class Scene:
    """The meshes of one subject as a BVH (world space) with per-polygon facts."""

    def __init__(self, objs, *, cull: bool, floor_exclude=("wheel*",), floor_pct: float = 1.0,
                 inside_exclude=("wheel*",)):
        import fnmatch

        import numpy as np

        dg = bpy.context.evaluated_depsgraph_get()
        verts: list = []
        polys: list = []
        self.poly_obj: list[int] = []
        self.poly_alpha: list[bool] = []
        self.poly_flat: list[bool] = []
        self.names: list[str] = []
        self.open_inside: list[bool] = []
        self.open_boxes: list = []
        zs: list = []
        areas: list = []
        floor_z: list = []
        floor_a: list = []
        alpha_cache: dict = {}
        for o in objs:
            if o.type != "MESH":
                continue
            ev = o.evaluated_get(dg)
            try:
                me = ev.to_mesh()
            except RuntimeError:
                continue
            try:
                n = len(me.vertices)
                if not n or not len(me.polygons):
                    continue
                mw = np.array(ev.matrix_world, dtype=np.float64)
                co = np.empty(n * 3, dtype=np.float64)
                me.vertices.foreach_get("co", co)
                w = co.reshape(n, 3) @ mw[:3, :3].T + mw[:3, 3]
                flip = np.linalg.det(mw[:3, :3]) < 0      # a mirrored object: keep the faces' visible side
                base = len(verts)
                verts.extend(map(tuple, w.tolist()))
                oi = len(self.names)
                self.names.append(str(o.get("satk_frame") or o.name))
                mats = [m for m in me.materials]
                excluded = any(fnmatch.fnmatchcase(_base(self.names[-1]), p) for p in floor_exclude)
                self.open_inside.append(any(fnmatch.fnmatchcase(_base(self.names[-1]), p) for p in inside_exclude))
                if self.open_inside[-1]:
                    self.open_boxes.append((w.min(axis=0) - 0.01, w.max(axis=0) + 0.01))
                flat = _flat_pieces(me, w)
                for p in me.polygons:
                    vs = [base + v for v in p.vertices]
                    polys.append(vs[::-1] if flip else vs)
                    self.poly_flat.append(flat[p.vertices[0]])
                    self.poly_obj.append(oi)
                    mi = p.material_index
                    m = mats[mi] if mi < len(mats) else None
                    key = m.name if m is not None else ""
                    if key not in alpha_cache:
                        alpha_cache[key] = _alpha_material(bpy.data.materials.get(key) if key else None)
                    self.poly_alpha.append(alpha_cache[key])
                    if not excluded:
                        c = w[list(p.vertices)].mean(axis=0)
                        floor_z.append(float(c[2]))
                        floor_a.append(float(p.area))
                zs.append(w)
            finally:
                ev.to_mesh_clear()
        if not polys:
            raise ValueError("the subject has no visible faces")
        self.bvh = BVHTree.FromPolygons(verts, polys, all_triangles=False, epsilon=0.0)
        P = np.concatenate(zs)
        self.lo = P.min(axis=0)
        self.hi = P.max(axis=0)
        self.cull = bool(cull)
        self.ground = float(self.lo[2])
        if floor_z:
            order = np.argsort(floor_z)
            fz = np.array(floor_z)[order]
            fa = np.cumsum(np.array(floor_a)[order])
            k = int(np.searchsorted(fa, fa[-1] * max(0.0, float(floor_pct)) / 100.0))
            self.floor = float(fz[min(k, len(fz) - 1)])
        else:
            self.floor = self.ground
        self.floor = max(self.floor, self.ground)

    def in_open_part(self, p) -> bool:
        """Is ``p`` inside the box of an excluded part (a wheel: its spokes are openings by design)?"""
        return any(all(lo[k] <= p[k] <= hi[k] for k in range(3)) for lo, hi in self.open_boxes)

    def size(self) -> float:
        return float(max(self.hi - self.lo))

    def nearest(self, p) -> str | None:
        r = self.bvh.find_nearest(Vector(p))
        if r is None or r[2] is None:
            return None
        return self.names[self.poly_obj[r[2]]]


def _flat_pieces(me, w) -> list[bool]:
    """Per vertex: does it belong to a flat piece (a connected part thinner than :data:`FLAT_M` along its thinnest
    principal axis: a one-sided card, a fence, a banner)?"""
    import numpy as np

    n = len(me.vertices)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in me.polygons:
        vs = p.vertices
        r0 = find(vs[0])
        for v in vs[1:]:
            r = find(v)
            if r != r0:
                parent[r] = r0
    roots = np.array([find(i) for i in range(n)])
    out = np.zeros(n, dtype=bool)
    for r in np.unique(roots):
        idx = np.nonzero(roots == r)[0]
        P = w[idx]
        if len(P) < 4:
            out[idx] = True
            continue
        Q = P - P.mean(axis=0)
        try:
            _u, _s, vt = np.linalg.svd(Q, full_matrices=False)
        except np.linalg.LinAlgError:
            continue
        thick = float(np.ptp(Q @ vt[-1]))
        out[idx] = thick < FLAT_M
    return out.tolist()


def build(objs, *, cull: bool, floor_exclude=("wheel*",), floor_pct: float = 1.0, inside_exclude=("wheel*",)) -> Scene:
    return Scene(objs, cull=cull, floor_exclude=floor_exclude, floor_pct=floor_pct, inside_exclude=inside_exclude)


def camera_rays(cam, w: int, h: int):
    """``(origins, dirs)`` as ``(h*w, 3)`` arrays, row-major from the top-left pixel (sensor fit horizontal)."""
    import numpy as np

    cd = cam.data
    R = np.array(cam.matrix_world.to_3x3(), dtype=np.float64)
    loc = np.array(cam.matrix_world.translation, dtype=np.float64)
    u = ((np.arange(w) + 0.5) / w) * 2.0 - 1.0
    v = 1.0 - ((np.arange(h) + 0.5) / h) * 2.0
    U, V = np.meshgrid(u, v)
    U, V = U.ravel(), V.ravel()
    ar = h / w
    if cd.type == "ORTHO":
        half = cd.ortho_scale / 2.0
        oc = np.stack([U * half, V * half * ar, np.zeros_like(U)], axis=1)
        origins = oc @ R.T + loc
        d = R @ np.array([0.0, 0.0, -1.0])
        dirs = np.tile(d, (len(U), 1))
    else:
        t = math.tan(cd.angle / 2.0)
        dc = np.stack([U * t, V * t * ar, -np.ones_like(U)], axis=1)
        dirs = dc @ R.T
        dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
        origins = np.tile(loc, (len(U), 1))
    return origins, dirs


def _box_entry(origins, dirs, lo, hi):
    """Slab test: ``(hits, t_enter, t_exit)`` of every ray against the box ``lo..hi``."""
    import numpy as np

    with np.errstate(divide="ignore", invalid="ignore"):
        inv = 1.0 / dirs
        t1 = (lo - origins) * inv
        t2 = (hi - origins) * inv
    tmin = np.nanmax(np.minimum(t1, t2), axis=1)
    tmax = np.nanmin(np.maximum(t1, t2), axis=1)
    hit = (tmax >= np.maximum(tmin, 0.0))
    return hit, np.maximum(tmin, 0.0), tmax


def classify(sc: Scene, origins, dirs, *, inside: bool = False):
    """Per ray: class (uint8), depth of the first visible surface (or of the surface the ray passed last), the
    polygon index of that surface (-1), and where the ray enters and leaves the model's box."""
    import numpy as np

    n = len(origins)
    cls = np.full(n, OUTSIDE, dtype=np.uint8)
    depth = np.full(n, np.nan, dtype=np.float32)
    poly = np.full(n, -1, dtype=np.int64)
    pad = max(0.02, 0.01 * sc.size())
    lo, hi = sc.lo - pad, sc.hi + pad
    hit_box, t_in, t_out = _box_entry(origins, dirs, lo, hi)
    if inside:
        hit_box[:] = True
        t_in[:] = 0.0
    idx = np.nonzero(hit_box)[0]
    alpha = sc.poly_alpha
    cull = sc.cull
    ray_cast = sc.bvh.ray_cast
    near_range = sc.bvh.find_nearest_range
    O = origins[idx].tolist()
    D = dirs[idx].tolist()
    T0 = t_in[idx].tolist()
    T1 = t_out[idx].tolist()
    far = float(np.linalg.norm(hi - lo)) * 4.0 + 10.0
    for k, i in enumerate(idx.tolist()):
        ox, oy, oz = O[k]
        dx, dy, dz = D[k]
        t = max(0.0, T0[k] - pad)
        glass = backs = False
        res = None
        steps = 0
        last_t = None
        last_p = -1
        while steps < _MAX_STEPS:
            steps += 1
            loc, nrm, pi, dist = ray_cast((ox + dx * t, oy + dy * t, oz + dz * t), (dx, dy, dz), far)
            if loc is None:
                break
            t += dist
            front = (nrm[0] * dx + nrm[1] * dy + nrm[2] * dz) < 0.0
            if alpha[pi]:
                glass = True
                last_t, last_p = t, pi
                t += _EPS
                continue
            if not front and cull and not alpha[pi]:
                # a card modelled double-sided (a front face on the back face): the front one is drawn
                for _co, n2, p2, _d2 in near_range(loc, COINCIDENT_M):
                    if p2 is not None and not alpha[p2] and (n2[0] * dx + n2[1] * dy + n2[2] * dz) < 0.0:
                        front = True
                        pi = p2
                        break
            if front or not cull:
                # a back face seen through glass is the cabin seen through a window: by design
                res = FRONT if front else (GLASS if glass else BACK)
                if res == BACK and not inside and min(oz + dz * T0[k], oz + dz * t) <= sc.floor + 0.01:
                    res = UNDER  # the inside of a skirt or a chassis rail seen from under the body
                depth[i] = t
                poly[i] = pi
                break
            if abs(nrm[0] * dx + nrm[1] * dy + nrm[2] * dz) >= GRAZE_COS:
                backs = True                   # a back face met edge-on is a sliver, not a hole
                last_t, last_p = t, pi
            t += _EPS
        if res is not None:
            cls[i] = res
            continue
        if last_t is not None:
            depth[i] = last_t
            poly[i] = last_p
        if inside:
            cls[i] = GLASS if glass and not backs else ESCAPE
        elif backs:
            cls[i] = CULLED
        elif glass:
            cls[i] = GLASS
        else:
            # nothing hit: a ray that stays above the floor while it crosses the model's box passed between
            # parts; one that dips below the floor passed under the model (under a car, between legs)
            z_lo = min(oz + dz * T0[k], oz + dz * T1[k])
            cls[i] = THROUGH if z_lo > sc.floor + 0.01 else UNDER
    return cls, depth, poly, t_in, t_out


def _components(mask):
    """4-connected components of a boolean ``(h, w)`` mask: a list of ``(ys, xs)`` index arrays."""
    import numpy as np

    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        if seen[y0, x0]:
            continue
        q = deque([(y0, x0)])
        seen[y0, x0] = True
        cy, cx = [], []
        while q:
            y, x = q.popleft()
            cy.append(y)
            cx.append(x)
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    q.append((yy, xx))
        out.append((np.array(cy), np.array(cx)))
    return out


def _touches(ys, xs, img, value, h, w) -> bool:
    import numpy as np

    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        yy, xx = ys + dy, xs + dx
        if ((yy < 0) | (yy >= h) | (xx < 0) | (xx >= w)).any():
            return True  # the image border: open
        if (img[yy, xx] == value).any():
            return True
    return False


def _probe(sc: Scene, p, dz: float):
    """The first opaque surface straight up (``dz`` = 1) or down (-1) from ``p``: ``None`` or its normal's z."""
    x, y, z = float(p[0]), float(p[1]), float(p[2])
    for _ in range(_MAX_STEPS):
        loc, nrm, pi, _d = sc.bvh.ray_cast((x, y, z), (0.0, 0.0, dz), 1.0e4)
        if loc is None:
            return None
        if not sc.poly_alpha[pi]:      # glass and alpha discs (a rotor blur) are no shell
            return float(nrm[2])
        z = loc[2] + _EPS * dz
    return None


def in_shell(sc: Scene, p) -> bool:
    """Is the point ``p`` inside the model's shell? Straight up it meets a surface from behind (a roof, a bonnet or
    a deck seen from below: its outer side faces up), and straight down it meets a floor from behind or nothing (an
    open engine bay). Above a roof, under a roof rack or a spoiler one of the two fails."""
    up = _probe(sc, p, 1.0)
    if up is None or up <= 0.0:
        return False
    down = _probe(sc, p, -1.0)
    return down is None or down < 0.0


def _through_body(sc: Scene, ys, xs, w: int, origins, dirs, t_box, samples: int = 24, depth_img=None) -> float:
    """Share of a cluster's rays that pass inside the shell (5 points along the part of each ray inside the model's
    box, or before the surface it meets when ``depth_img`` is given)."""
    import numpy as np

    t_in, t_out = t_box
    if depth_img is not None:
        t_out = np.where(np.isfinite(depth_img.ravel()), depth_img.ravel() - 0.02, t_out)
    n = len(ys)
    pick = np.linspace(0, n - 1, min(n, samples)).astype(int)
    hit = 0
    for k in pick.tolist():
        i = int(ys[k] * w + xs[k])
        a, b = float(t_in[i]), float(t_out[i])
        if not b > a:
            continue
        if any(in_shell(sc, origins[i] + dirs[i] * (a + (b - a) * f)) for f in (0.15, 0.3, 0.5, 0.7, 0.85)):
            hit += 1
    return hit / max(1, len(pick))


def clusters(cls_img, depth_img, origins, dirs, cam, sc: Scene, *, kinds: dict, min_px: int = 6,
             min_cm2: float = 2.0, t_box=None, poly_img=None, inside_depth: float = 1.0,
             inside_cm2: float = 60.0) -> list[dict]:
    """Gaps of one view (see the module docstring). ``kinds`` = ``{"gap", "inside", "see_through"}`` booleans.

    A ``through`` cluster counts when it is enclosed (never touches ``outside``) and at least a fifth of its rays
    pass inside the shell (:func:`in_shell`): the background seen through the body, not under a roof rail.
    A ``back`` cluster (the inside of a shell seen through an opening, vehicles) is listed when the inner surface
    lies at least :data:`INFO_DEPTH` behind the rim of the opening and it is not the inside of an excluded part (a
    wheel seen through its spokes); it blocks (``blocking``) only in the views the kind names (``inside_block``:
    the wheel close-ups, where an open arch shows the far side of the body) when it is ``inside_depth`` deep and
    ``inside_cm2`` big. Every other gap blocks."""
    import numpy as np

    h, w = cls_img.shape
    out: list[dict] = []
    wanted = []
    if kinds.get("gap", True):
        wanted += [THROUGH, ESCAPE]
    if kinds.get("see_through", True):
        wanted.append(CULLED)
    if kinds.get("inside", False):
        wanted.append(BACK)
    covered = np.isin(cls_img, (FRONT, BACK, GLASS))
    model = np.isin(cls_img, (FRONT, BACK, GLASS, CULLED))
    if kinds.get("slit") and poly_img is not None:
        out += _slits(cls_img, depth_img, origins, dirs, cam, sc, model, poly_img, min_px=min_px,
                      min_cm2=max(min_cm2, float(kinds.get("slit_min_cm2") or 10.0)),
                      max_m=float(kinds.get("slit_max_m") or SLIT_MAX_M))
    for c in wanted:
        mask = cls_img == c
        if c == BACK and poly_img is not None:
            open_obj = np.array(sc.open_inside + [False], dtype=bool)
            po = np.array(sc.poly_obj + [len(sc.open_inside)], dtype=np.int64)
            pi_img = np.where(poly_img >= 0, poly_img, len(po) - 1)
            mask &= ~open_obj[po[pi_img]]
        for ys, xs in _components(mask):
            if len(ys) < min_px:
                continue
            if c == THROUGH and _touches(ys, xs, cls_img, OUTSIDE, h, w):
                continue  # open to the background around the model
            if c == THROUGH and t_box is not None and _through_body(sc, ys, xs, w, origins, dirs, t_box) < 0.2:
                continue  # between parts outside the shell: a roof rail, a spoiler, a bull bar
            if c == BACK and not _touches(ys, xs, cls_img, FRONT, h, w):
                continue  # a back face with no front face next to it: a separate plane seen from behind
            # depth of the surfaces around the cluster (the rim of the opening)
            ring = np.zeros((h, w), dtype=bool)
            for dy, dx in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1), (-1, 1), (1, -1)):
                yy = np.clip(ys + dy, 0, h - 1)
                xx = np.clip(xs + dx, 0, w - 1)
                ring[yy, xx] = True
            ring &= covered & (cls_img != c)
            rd = depth_img[ring]
            rd = rd[np.isfinite(rd)]
            own = depth_img[ys, xs]
            own = own[np.isfinite(own)]
            # the near rim (the opening itself); a deep ring part is the inside seen through it
            d = float(np.percentile(rd, 25)) if len(rd) else (float(np.median(own)) if len(own) else float("nan"))
            deep = float(np.median(own)) - d if len(own) and math.isfinite(d) else 0.0
            if c == BACK and deep < INFO_DEPTH:
                continue  # the inner surface sits right behind the opening (a grille slot, a vent)
            cy, cx = ys.mean(), xs.mean()
            k = int(np.argmin((ys - cy) ** 2 + (xs - cx) ** 2))
            pi = int(ys[k] * w + xs[k])
            px_m = _pixel_m(cam, w, d)
            area = len(ys) * px_m * px_m * 1e4 if px_m else 0.0
            if area < min_cm2:
                continue
            width = _width_px(ys, xs) * px_m
            note = None
            if c == BACK:
                blocking = bool(kinds.get("inside_block")) and deep >= inside_depth and area >= inside_cm2
            elif c == CULLED:
                blocking = bool(kinds.get("see_through_block", True))
                if _width_px(ys, xs) < 2:
                    continue                    # a one-pixel sliver along an edge
                # with a rim around it the depth behind the rim decides; without one (the opening fills the
                # silhouette) the piece's shape does (below: a flat one-sided plane is information)
                if blocking and len(rd) and not (math.isfinite(deep) and deep >= min(HOLE_DEPTH, 0.1 * sc.size())):
                    blocking = False
                    note = "the back of an open surface seen from behind, not through an opening"
                if blocking and poly_img is not None:
                    pis = poly_img[ys, xs]
                    pis = pis[pis >= 0]
                    flat = float(np.mean([sc.poly_flat[int(i)] for i in pis[:: max(1, len(pis) // 200)]])) \
                        if len(pis) else 0.0
                    if flat >= 0.6 and _open_share(ys, xs, model, h, w) > 0.2:
                        blocking = False        # the back of a one-sided flat plane (a fence, a card) from behind
                        note = "one-sided flat plane seen from behind (invisible from this side in the game)"
            elif c == ESCAPE and kinds.get("escape_max_m"):
                blocking = width <= float(kinds["escape_max_m"])  # a crack; a door or window opening is info
            else:
                blocking = True
            g = {"kind": GAP_KIND[c], "class": CLASSES[c], "blocking": blocking, "px": int(len(ys)),
                 "bbox_px": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
                 "area_cm2": round(area, 1), "width_m": round(width, 3)}
            if note:
                g["note"] = note
            if math.isfinite(d):
                p = origins[pi] + dirs[pi] * d
                if c == BACK and sc.in_open_part(p):
                    continue  # seen through the spokes of a wheel
                g["point"] = [round(float(v), 3) for v in p]
                near = sc.nearest(p)
                if near:
                    g["near"] = near
            if c in (BACK, CULLED) and len(own):
                q = origins[pi] + dirs[pi] * float(np.median(own))
                g["behind"] = [round(float(v), 3) for v in q]
                if math.isfinite(d):
                    g["deep_m"] = round(float(np.median(own)) - d, 2)
            out.append(g)
    out.sort(key=lambda g: -g["area_cm2"])
    return out


def _open_share(ys, xs, model, h: int, w: int) -> float:
    """Share of a cluster's outline next to background (not the model, not the cluster)."""
    import numpy as np

    mine = np.zeros((h, w), dtype=bool)
    mine[ys, xs] = True
    ring = np.zeros((h, w), dtype=bool)
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        yy = np.clip(ys + dy, 0, h - 1)
        xx = np.clip(xs + dx, 0, w - 1)
        ring[yy, xx] = True
    ring &= ~mine
    n = int(ring.sum())
    if not n:
        return 0.0
    return float((ring & ~model).sum()) / n


def _shift_or(m, r: int):
    """Dilation of a boolean image by a square of radius ``r``."""
    import numpy as np

    out = m.copy()
    h, w = m.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy == 0 and dx == 0:
                continue
            ys0, ys1 = max(0, dy), h + min(0, dy)
            xs0, xs1 = max(0, dx), w + min(0, dx)
            out[ys0:ys1, xs0:xs1] |= m[ys0 - dy:ys1 - dy, xs0 - dx:xs1 - dx]
    return out


def _slits(cls_img, depth_img, origins, dirs, cam, sc: Scene, model, poly_img, *, min_px: int, min_cm2: float,
           max_m: float) -> list[dict]:
    """Narrow lanes of background between two surfaces at about the same depth (see the module docstring)."""
    import numpy as np

    h, w = cls_img.shape
    fin = depth_img[np.isfinite(depth_img) & (cls_img == FRONT)]
    if not len(fin):
        return []
    px_m = _pixel_m(cam, w, float(np.median(fin)))
    if not px_m:
        return []
    r = int(max(1, min(10, round(SLIT_CLOSE_M / px_m))))
    pad = np.zeros((h + 2 * r + 2, w + 2 * r + 2), dtype=bool)  # background beyond the frame: no false lanes there
    pad[r + 1:r + 1 + h, r + 1:r + 1 + w] = model
    closed = (~_shift_or(~_shift_or(pad, r), r))[r + 1:r + 1 + h, r + 1:r + 1 + w]   # closing = erode(dilate)
    cand = closed & ~model & np.isin(cls_img, (THROUGH, UNDER))
    if not cand.any():
        return []
    wheel = np.array(sc.open_inside + [False], dtype=bool)
    po = np.array(sc.poly_obj + [len(sc.open_inside)], dtype=np.int64)
    out = []
    for ys, xs in _components(cand):
        if len(ys) < min_px:
            continue
        wpx = _width_px(ys, xs)
        if wpx * px_m > max_m or len(ys) < 4 * wpx * wpx:
            continue                                        # wide (an opening by design) or a corner, not a lane
        ring = np.zeros((h, w), dtype=bool)
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                ring[np.clip(ys + dy, 0, h - 1), np.clip(xs + dx, 0, w - 1)] = True
        ring &= model
        if not ring.any():
            continue
        pis = poly_img[ring]
        pis = np.where(pis >= 0, pis, len(po) - 1)
        if wheel[po[pis]].any():
            continue                                        # the gap between a tyre and its arch
        rd = depth_img[ring]
        rd = rd[np.isfinite(rd)]
        if len(rd) < 4 or float(np.percentile(rd, 90) - np.percentile(rd, 10)) > 1.0:
            continue                                        # a near edge in front of something far: no joint
        d = float(np.median(rd))
        area = len(ys) * px_m * px_m * 1e4
        if area < min_cm2:
            continue
        cy, cx = ys.mean(), xs.mean()
        k = int(np.argmin((ys - cy) ** 2 + (xs - cx) ** 2))
        pi = int(ys[k] * w + xs[k])
        p = origins[pi] + dirs[pi] * d
        g = {"kind": "gap", "class": "slit", "blocking": True, "px": int(len(ys)),
             "bbox_px": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())], "area_cm2": round(area, 1),
             "width_m": round(wpx * px_m, 3), "point": [round(float(v), 3) for v in p]}
        near = sc.nearest(p)
        if near:
            g["near"] = near
        out.append(g)
    return out


def _width_px(ys, xs) -> int:
    """Twice the inscribed radius (pixels) of a cluster: 4-neighbour erosions until it vanishes."""
    import numpy as np

    y0, x0 = int(ys.min()), int(xs.min())
    m = np.zeros((int(ys.max()) - y0 + 3, int(xs.max()) - x0 + 3), dtype=bool)
    m[ys - y0 + 1, xs - x0 + 1] = True
    r = 0
    while m.any():
        r += 1
        e = m.copy()
        e[1:, :] &= m[:-1, :]
        e[:-1, :] &= m[1:, :]
        e[:, 1:] &= m[:, :-1]
        e[:, :-1] &= m[:, 1:]
        m = e
    return 2 * r - 1


def _pixel_m(cam, w: int, depth: float) -> float:
    cd = cam.data
    if cd.type == "ORTHO":
        return float(cd.ortho_scale) / w
    if not math.isfinite(depth):
        return 0.0
    return 2.0 * depth * math.tan(cd.angle / 2.0) / w


def cell_image(cls_img, shade, gaps: list[dict], path: str) -> str:
    """Write the leak cell (PNG): the model black (lightly shaded), background bright, gaps marked and numbered."""
    import numpy as np

    h, w = cls_img.shape
    rgb = np.zeros((h, w, 3), dtype=np.float32)
    for c, col in _COL.items():
        rgb[cls_img == c] = col
    m = cls_img == FRONT
    rgb[m] = np.clip(rgb[m] + shade[m, None] * 58.0, 0, 255)
    for g in gaps:
        col = _MARK.get(g["kind"], (235, 20, 20))
        if not g.get("blocking", True):
            col = tuple(int(0.55 * a + 0.45 * b) for a, b in zip(col, (236, 242, 250)))  # information: pale
        x0, y0, x1, y1 = g["bbox_px"]
        sub = cls_img[y0:y1 + 1, x0:x1 + 1]
        want = {"gap": (THROUGH, UNDER) if g.get("class") == "slit" else (THROUGH,), "escape": (ESCAPE,),
                "see_through": (CULLED,), "inside": (BACK,)}[g["kind"]]
        mm = np.isin(sub, want)
        rgb[y0:y1 + 1, x0:x1 + 1][mm] = col
        # a frame two pixels outside the gap's box
        X0, Y0, X1, Y1 = max(0, x0 - 3), max(0, y0 - 3), min(w - 1, x1 + 3), min(h - 1, y1 + 3)
        rgb[Y0, X0:X1 + 1] = col
        rgb[Y1, X0:X1 + 1] = col
        rgb[Y0:Y1 + 1, X0] = col
        rgb[Y0:Y1 + 1, X1] = col
    img = bpy.data.images.new("satk_leak_cell", w, h, alpha=False)
    try:
        px = np.ones((h, w, 4), dtype=np.float32)
        px[..., :3] = rgb[::-1] / 255.0      # Blender rows start at the bottom
        img.pixels.foreach_set(px.ravel())
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)
    return path.replace("\\", "/")


def run_view(scene, sc: Scene, cam, size, *, inside: bool, kinds: dict, min_px: int, min_cm2: float,
             path: str | None = None) -> dict:
    """Classify one camera's pixels, find its gaps and (``path``) write the cell. ``size`` = ``(w, h)``."""
    import numpy as np

    w, h = int(size[0]), int(size[1])
    origins, dirs = camera_rays(cam, w, h)
    cls, depth, poly, t_in, t_out = classify(sc, origins, dirs, inside=inside)
    cls_img = cls.reshape(h, w)
    gaps = clusters(cls_img, depth.reshape(h, w), origins, dirs, cam, sc, kinds=kinds, min_px=min_px,
                    min_cm2=min_cm2, t_box=(t_in, t_out), poly_img=poly.reshape(h, w),
                    inside_depth=float(kinds.get("inside_depth", 1.0)),
                    inside_cm2=float(kinds.get("inside_cm2", 60.0)))
    shade = np.zeros((h, w), dtype=np.float32)
    dimg = depth.reshape(h, w)
    fin = np.isfinite(dimg) & (cls_img == FRONT)
    if fin.any():
        # a hint of form on the black model: edges where the depth jumps stay black, flat runs get a little light
        lo_d, hi_d = float(dimg[fin].min()), float(dimg[fin].max())
        dd = np.where(fin, dimg, hi_d)
        gy, gx = np.gradient(dd)
        step = max(1e-6, _pixel_m(cam, w, (lo_d + hi_d) / 2.0))
        shade = np.where(fin, 1.0 - np.clip(np.hypot(gx, gy) / (step * 3.0), 0.0, 1.0), 0.0).astype(np.float32)
    counts = {CLASSES[c]: int((cls == c).sum()) for c in range(len(CLASSES)) if (cls == c).any()}
    out = {"gaps": gaps, "counts": counts, "floor": round(sc.floor, 3), "px": [w, h]}
    if path:
        out["cell"] = cell_image(cls_img, shade, gaps, path)
    return out


def merge(views: list[dict], dist: float = 0.15, *, blocking: bool = True) -> list[dict]:
    """One list of the blocking (or the information) gaps over all views: gaps of the same kind whose points lie
    within ``dist`` merge (the largest view's numbers stay; ``views`` lists every view that saw it). Ids ``L1``,
    ``L2`` ... (information: ``I1`` ...) by area."""
    out: list[dict] = []
    for v in views:
        for g in v.get("gaps") or []:
            if bool(g.get("blocking", True)) != blocking:
                continue
            g = dict(g, view=v["view"], region=v.get("region"))
            p = g.get("point")
            twin = None
            if p is not None:
                for o in out:
                    q = o.get("point")
                    if q is not None and o["kind"] == g["kind"] and math.dist(p, q) <= dist:
                        twin = o
                        break
            if twin is None:
                g["views"] = [g["view"]]
                out.append(g)
            else:
                twin["views"].append(g["view"])
                if g["area_cm2"] > twin["area_cm2"]:
                    keep = twin["views"]
                    twin.clear()
                    twin.update(g, views=keep)
    out.sort(key=lambda g: (-g["area_cm2"], g["view"]))
    for i, g in enumerate(out):
        g["id"] = f"{'L' if blocking else 'I'}{i + 1}"
    return out


def write(path: str, data: dict) -> str:
    import json

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path.replace("\\", "/")
