# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Form methods: authored rounded sections and profiles instead of boxes and rigid slab moves.

* ``mesh.loft`` skins sections along an axis. A section is raw ``points`` or a ``shape`` (a superellipse with a
  crowned top, tumblehome, a belt line and an optional flat floor; :mod:`satk.studio.shapes`). Between sections
  ``steps`` extra rings are interpolated (``interp`` smooth = a monotone spline through the shape parameters or
  points, no overshoot). ``half`` builds only x >= 0 with a MIRROR modifier; ``parts`` writes the face attribute
  ``satk_part`` that ``kit.blank_split`` cuts along. The result is welded, smooth-shaded, with cylindrical UVs.
* ``mesh.sweep`` runs a profile (points or a shape) along a path (points or a curve object): bumpers wrapping
  arch to arch, mouldings, sills, rails, pipes; ``scale`` and ``twist`` vary along the path.
* ``mesh.relax`` evens out a selection (Laplacian with volume keeping); ``mesh.deform`` bends, tapers, twists,
  casts or lattice-deforms a mesh, live (a modifier) or applied.

Example (a soft car body half: crowned roof, tumblehome, barrel sides)::

    {"method": "mesh.loft", "params": {"name": "body", "axis": "y", "samples": 28, "half": true, "steps": 2,
     "sections": [{"at": -2.3, "shape": {"w": 0.80, "h": 0.55, "z": 0.30, "exp": 3}},
                  {"at": -1.0, "shape": {"w": 0.95, "h": 1.15, "z": 0.25, "exp_top": 2.6, "exp_bottom": 3.5,
                                          "mid": 0.5, "crown": 0.06, "tumble": 0.75, "flat_bottom": true}},
                  {"at": 2.3, "shape": {"w": 0.85, "h": 0.55, "z": 0.30, "exp": 3}}]}}
"""

from __future__ import annotations

import json
import math

import bmesh
import bpy
from mathutils import Matrix, Vector

from satk.core.errors import SatkError
from satk.studio import shapes as S

from . import _util as U

#: Part names a car body loft usually carries, with their kit slots (``kit.blank_split`` reads ``slot``/``sided``).
_KNOWN_PARTS = {"chassis": ("chassis", False), "bonnet": ("bonnet_ok", False), "boot": ("boot_ok", False),
                "windscreen": ("windscreen_ok", False), "door_f": ("door_{s}f_ok", True),
                "door_r": ("door_{s}r_ok", True), "bump_front": ("bump_front_ok", False),
                "bump_rear": ("bump_rear_ok", False)}


def _plane_pt(ax: int, at: float, a: float, b: float) -> Vector:
    """A 2D section point (a, b) on the plane ``axis = at``: y -> (x, z), x -> (y, z), z -> (x, y)."""
    if ax == 1:
        return Vector((a, at, b))
    if ax == 0:
        return Vector((at, a, b))
    return Vector((a, b, at))


def _section_ab(ax: int, co: Vector) -> tuple[float, float, float]:
    """``(a, b, at)`` of a 3D point for sections along ``ax``."""
    if ax == 1:
        return co.x, co.z, co.y
    if ax == 0:
        return co.y, co.z, co.x
    return co.x, co.y, co.z


def _steps_list(p: dict, M: str, gaps: int) -> list[int]:
    v = p.get("steps", 0)
    if isinstance(v, list):
        if len(v) != gaps or not all(U.is_num(x) and int(x) == x and 0 <= x <= 64 for x in v):
            raise U.bad(M, f"'steps' as a list needs {gaps} integers 0-64 (one per gap between sections)")
        return [int(x) for x in v]
    return [U.integer(p, "steps", M, 0, lo=0, hi=64)] * gaps


def _read_sections(p: dict, M: str, *, half_profile: bool) -> list[dict]:
    secs = U.require(p, "sections", M, hint="a list of {\"at\": y, \"shape\": {...}} or {\"at\": y, \"points\": [...]}")
    if not isinstance(secs, list) or not 2 <= len(secs) <= 256:
        raise U.bad(M, "'sections' must be a list of 2-256 {\"at\": number, \"points\" | \"shape\": ...}",
                    hint="axis y: a = x (across), b = z (up); a shape is {\"w\", \"h\", \"z\", \"exp\", ...}")
    out = []
    last_at = None
    for i, s in enumerate(secs):
        if not isinstance(s, dict):
            raise U.bad(M, f"section {i + 1} must be an object")
        extra = set(s) - {"at", "points", "shape", "interp"}
        if extra:
            raise U.bad(M, f"section {i + 1}: unknown key(s) {sorted(extra)}", hint="at + points or at + shape")
        gap = U.text(s, "interp", M, None, choices=("smooth", "linear"))
        at = U.num(s, "at", M, required=True)
        if last_at is not None and at == last_at:
            raise U.bad(M, f"sections {i} and {i + 1} are at the same position {at}")
        last_at = at
        if (s.get("points") is None) == (s.get("shape") is None):
            raise U.bad(M, f"section {i + 1} needs 'points' or 'shape' (one of them)")
        if s.get("shape") is not None:
            out.append({"at": at, "shape": S.check_shape(s["shape"], f"{M} section {i + 1}")})
        else:
            out.append({"at": at, "points": U.points2(s, "points", M, min_n=2 if half_profile else 3)})
        if gap:
            out[-1]["interp"] = gap
    return out


def _monotonic(ats: list[float], M: str) -> None:
    inc = all(b > a for a, b in zip(ats, ats[1:]))
    dec = all(b < a for a, b in zip(ats, ats[1:]))
    if not (inc or dec):
        raise U.bad(M, "with steps the section positions 'at' must run one way (all increasing or all decreasing)")


def _rings(p: dict, M: str, secs: list[dict], *, samples: int, half: bool, mirror: bool, closed: bool,
           turn: float, interp: str, steps: list[int]) -> list[dict]:
    """Rings ``{at, pts: [(a, b)], zc}`` of the loft (sections plus interpolated rings)."""
    ats = [s["at"] for s in secs]
    all_shape = all("shape" in s for s in secs)
    half_profile = half or mirror
    n_half = samples // 2
    extra = [(i, k, steps[i]) for i in range(len(secs) - 1) for k in range(1, steps[i] + 1)]
    if extra:
        _monotonic(ats, M)

    def pos(i: int, k: int, n: int) -> float:
        return ats[i] + (ats[i + 1] - ats[i]) * k / (n + 1)

    order = []  # (at, section index or None, (i, k, n) or None)
    for i in range(len(secs)):
        order.append((ats[i], i, None))
        if i < len(secs) - 1:
            for k in range(1, steps[i] + 1):
                order.append((pos(i, k, steps[i]), None, (i, k, steps[i])))
    rings = []
    if all_shape:
        if samples % 2 or samples < 4:
            raise U.bad(M, f"shape sections need an even 'samples' >= 4 (points around the whole loop), got {samples}")
        keys = ("w", "h", "z", "et", "eb", "mid", "crown", "tumble", "shoulder", "flat")
        fs = S.pchip_many(ats, [[s["shape"][k] for k in keys] for s in secs]) if extra else None

        def shape_at(t, ik):
            i, k, n = ik
            if secs[i].get("interp", interp) == "smooth":
                return dict(zip(keys, fs(t)))
            return S.lerp_shapes(secs[i]["shape"], secs[i + 1]["shape"], k / (n + 1))
        for at, si, ik in order:
            sh = secs[si]["shape"] if si is not None else shape_at(at, ik)
            sh["flat"] = min(1.0, max(0.0, sh["flat"]))
            sh["tumble"] = max(0.05, sh["tumble"])
            h = S.shape_half(sh, n_half, turn=turn)
            rings.append({"at": at, "half": h, "zc": S.shape_center(sh)})
    else:
        if half_profile and samples % 2 and half:
            raise U.bad(M, f"half needs an even 'samples', got {samples}")
        base = []
        for s in secs:
            if "shape" in s:
                pts = S.shape_half(s["shape"], 48, turn=0.0)
                if not half_profile:
                    pts = S.half_to_loop(pts)
            else:
                pts = list(s["points"])
            if half:
                ring = S.resample(pts, n_half + 1, turn=turn)
            elif mirror:
                ring = S.resample(S.half_to_loop(pts), samples, closed=True, turn=turn)
            else:
                ring = S.resample(pts, samples, closed=closed, turn=turn)
            base.append([tuple(q) for q in ring])
        if extra:
            flat_rows = [[c for q in r for c in q] for r in base]
            f = S.pchip_many(ats, flat_rows)

            def ring_at(t, ik):
                i, k, n = ik
                if secs[i].get("interp", interp) == "smooth":
                    return f(t)
                u = k / (n + 1)
                return [a + (b - a) * u for a, b in zip(flat_rows[i], flat_rows[i + 1])]
        for at, si, ik in order:
            if si is not None:
                pts = base[si]
            else:
                flat = ring_at(at, ik)
                pts = [(flat[j], flat[j + 1]) for j in range(0, len(flat), 2)]
            bs = [q[1] for q in pts]
            rings.append({"at": at, ("half" if half else "pts"): pts, "zc": (min(bs) + max(bs)) / 2})
    for r in rings:
        if "half" in r and not half:
            loop = S.half_to_loop(r.pop("half"))
            if all_shape:   # start the loop (and the UV seam) at the bottom centre
                loop = loop[n_half:] + loop[:n_half]
            r["pts"] = loop
    return rings


def _parts(p: dict, M: str) -> tuple[list[str], list[dict], list[dict]]:
    """``(names, kit part specs, rules)``: names[0] is the base part."""
    raw = p.get("parts")
    if raw is None:
        return [], [], []
    if not isinstance(raw, list) or not 1 <= len(raw) <= 32:
        raise U.bad(M, "'parts' must be a list of 1-32 {\"name\", \"at\": [a, b], \"angle\": [deg0, deg1]}")
    base = U.text(p, "base_part", M, "chassis")
    names = [base]
    rules = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict) or set(r) - {"name", "at", "angle", "slot", "sided"}:
            raise U.bad(M, f"part {i + 1} must be {{\"name\", \"at\"?, \"angle\"?, \"slot\"?, \"sided\"?}}")
        name = U.text(r, "name", M, required=True)
        if len(name) > 32 or not name.replace("_", "").isalnum():
            raise U.bad(M, f"part name {name!r}: letters, digits and '_' (at most 32)")
        rng = []
        for key, lo, hi in (("at", -1e9, 1e9), ("angle", 0.0, 180.0)):
            v = r.get(key)
            if v is None:
                rng.append((lo, hi))
                continue
            if not isinstance(v, list) or len(v) != 2 or not all(U.is_num(x) for x in v):
                raise U.bad(M, f"part {name}: '{key}' must be [from, to]")
            a, b = sorted(float(x) for x in v)
            rng.append((a, b))
        if name not in names:
            names.append(name)
        rules.append({"index": names.index(name), "at": rng[0], "angle": rng[1]})
    specs = []
    for n in names:
        slot, sided = _KNOWN_PARTS.get(n, (n, False))
        raw_r = next((r for r in raw if r.get("name") == n), {})
        slot = str(raw_r.get("slot") or slot)
        sided = bool(raw_r.get("sided", sided))
        specs.append({"name": n, "slot": slot, **({"sided": True} if sided else {})})
    return names, specs, rules


def _orient_outward(bm, ax: int, rings: list[dict], faces) -> None:
    """Flip all faces when most of them point towards the loft's centre line (open halves, odd loops)."""
    score = 0.0
    for f in faces:
        c = f.calc_center_median()
        a, b, at = _section_ab(ax, c)
        zc = _zc_at(rings, at)
        out = _plane_pt(ax, at, a, b - zc) - _plane_pt(ax, at, 0.0, 0.0)
        score += f.normal.dot(out) * f.calc_area()
    if score < 0:
        bmesh.ops.reverse_faces(bm, faces=list(faces))


def _zc_at(rings: list[dict], at: float) -> float:
    best = min(rings, key=lambda r: abs(r["at"] - at))
    return best["zc"]


def loft(ctx, p: dict) -> dict:
    """Skin a mesh through sections along an axis: sections=[{at, points | shape}], samples, steps, interp, half, parts, mirror, cap; welded, smooth, cylindrical UVs.

    A section is ``{"at": y, "points": [[a, b], ...]}`` or ``{"at": y, "shape": {...}}`` (axis y: a = x across,
    b = z up). ``shape`` = {``w`` half width, ``h`` height, ``z`` bottom, ``exp`` (or ``exp_top``/``exp_bottom``)
    superellipse exponent (2 round, 3-4 soft box), ``mid`` belt height share, ``crown`` m, ``tumble`` top/belt
    width ratio, ``shoulder`` m (a beltline ledge), ``flat_bottom``}. ``samples`` = points around the whole loop (even for shapes and half).
    ``steps`` = rings added between neighbouring sections (a number or one per gap); ``interp`` smooth (monotone
    spline through the shape parameters or points) or linear (planes: a windscreen); a section's own ``interp``
    sets the gap after it. ``half`` = only x >= 0 (open on the mirror plane)
    with a MIRROR modifier (``mirror_modifier`` false skips it). ``mirror`` = point sections give the +x half and
    the mesh is the whole loop. ``spacing`` turn (default for shapes: corners get the points) or even (arc
    length, default for points). ``smooth`` (default: true for shape sections, false for point sections).
    ``parts`` = [{name, at: [a, b], angle: [deg0, deg1]}] (``angle`` = which way the
    face looks across the loft: 0 up, 90 sideways, 180 down; later parts win; ``base_part`` = the rest, default
    chassis) -> face attribute ``satk_part`` for ``kit.blank_split`` (``model`` names the kit model; a part may
    give ``slot`` and ``sided``). ``cap`` true|false|start|end.
    """
    M = "mesh.loft"
    ax = U.axis(p, "axis", M, "y")
    samples = U.integer(p, "samples", M, lo=3, hi=512, required=True)
    closed = U.flag(p, "closed", M, True)
    half = U.flag(p, "half", M, False)
    mirror = U.flag(p, "mirror", M, False)
    secs = _read_sections(p, M, half_profile=half or mirror)
    all_shape = all("shape" in s for s in secs)
    if half:
        closed = False
    capv = p.get("cap")
    if capv is None:
        capv = closed or half
    if capv not in (True, False, "start", "end"):
        raise U.bad(M, f"'cap' must be true, false, start or end, got {capv!r}")
    if capv and not (closed or half):
        capv = False
    cap_ends = {"start": (True, False), "end": (False, True), True: (True, True), False: (False, False)}[capv]
    name = U.text(p, "name", M, "loft")
    interp = U.text(p, "interp", M, "smooth", choices=("smooth", "linear"))
    spacing = U.text(p, "spacing", M, None, choices=("turn", "even")) or ("turn" if all_shape else "even")
    # shape lofts are smooth; point lofts keep their old flat blockout shading unless smooth is given
    smooth = U.flag(p, "smooth", M) if p.get("smooth") is not None else all_shape
    steps = _steps_list(p, M, len(secs) - 1)
    pnames, pspecs, prules = _parts(p, M)
    rings = _rings(p, M, secs, samples=samples, half=half, mirror=mirror, closed=closed,
                   turn=S.TURN_WEIGHT if spacing == "turn" else 0.0, interp=interp, steps=steps)
    key = "half" if half else "pts"
    n = len(rings[0][key])
    bm = bmesh.new()
    try:
        uvl = bm.loops.layers.uv.new("UVMap")  # before any element: a new layer invalidates element references
        pl = bm.faces.layers.int.new("satk_part") if pnames else None
        vrings = [[bm.verts.new(_plane_pt(ax, r["at"], a, b)) for a, b in r[key]] for r in rings]
        # UV: u = arc length around (the whole loop = 1), v = distance along the axis over the mean perimeter
        perim = []
        for r in rings:
            pts = r[key]
            L = S.polyline_length(pts, closed=not half and closed)
            perim.append(2 * L if half else L)
        mean_p = max(sum(perim) / len(perim), 1e-6)
        vpos = [0.0]
        for r0, r1 in zip(rings, rings[1:]):
            vpos.append(vpos[-1] + abs(r1["at"] - r0["at"]) / mean_p)
        us = []
        for r, P in zip(rings, perim):
            pts = r[key]
            acc, row = 0.0, [0.0]
            for q0, q1 in zip(pts, pts[1:]):
                acc += math.dist(q0, q1)
                row.append(acc / max(P, 1e-9))
            us.append(row + ([1.0] if closed and not half else []))
        wrap = closed and not half
        side_faces = []
        for i, (r0, r1) in enumerate(zip(vrings, vrings[1:])):
            for k in range(n if wrap else n - 1):
                k1 = (k + 1) % n
                f = bm.faces.new((r0[k], r0[k1], r1[k1], r1[k]))
                f.smooth = smooth
                ku = k + 1
                for lp, uv in zip(f.loops, ((us[i][k], vpos[i]), (us[i][ku], vpos[i]),
                                            (us[i + 1][ku], vpos[i + 1]), (us[i + 1][k], vpos[i + 1]))):
                    lp[uvl].uv = uv
                side_faces.append(f)
        caps = []
        for want, ring in zip(cap_ends, (vrings[0], vrings[-1])):
            if not want or len({v.co.to_tuple(6) for v in ring}) < 3:
                continue
            try:
                f = bm.faces.new(ring)
            except ValueError:
                continue
            f.smooth = False
            ext = [v.co for v in ring]
            a_i, b_i = [i for i in range(3) if i != ax]
            lo = [min(c[i] for c in ext) for i in range(3)]
            hi = [max(c[i] for c in ext) for i in range(3)]
            for lp in f.loops:
                lp[uvl].uv = ((lp.vert.co[a_i] - lo[a_i]) / max(hi[a_i] - lo[a_i], 1e-9),
                              (lp.vert.co[b_i] - lo[b_i]) / max(hi[b_i] - lo[b_i], 1e-9))
            caps.append(f)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)
        bm.faces.ensure_lookup_table()
        if wrap:
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        else:   # an open sheet or half: sides point away from the centre line, caps away from the middle
            _orient_outward(bm, ax, rings, [f for f in bm.faces if f.is_valid and f not in caps])
            mid_at = (rings[0]["at"] + rings[-1]["at"]) / 2
            for f in caps:
                if f.is_valid:
                    _a, _b, at = _section_ab(ax, f.calc_center_median())
                    if f.normal[ax] * (1.0 if at > mid_at else -1.0) < 0:
                        f.normal_flip()
        if smooth:              # a flat cap on a smooth body: its border is a hard edge
            for f in caps:
                if f.is_valid:
                    for e in f.edges:
                        e.smooth = False
        counts_parts: dict = {}
        if pl is not None:
            bm.normal_update()
            for f in bm.faces:
                _a, _b, at = _section_ab(ax, f.calc_center_median())
                na, nb, _nat = _section_ab(ax, f.normal)
                # which way the face looks across the loft: 0 up, 90 sideways, 180 down (caps count as sideways)
                ang = 90.0 if f in caps or math.hypot(na, nb) < 1e-6 else math.degrees(math.atan2(abs(na), nb))
                idx = 0
                for rule in prules:
                    if rule["at"][0] - 1e-6 <= at <= rule["at"][1] + 1e-6 and \
                            rule["angle"][0] - 1e-6 <= ang <= rule["angle"][1] + 1e-6:
                        idx = rule["index"]
                f[pl] = idx
                counts_parts[pnames[idx]] = counts_parts.get(pnames[idx], 0) + 1
        ob = U.new_mesh_object(ctx, name, bm, p.get("collection"))
    finally:
        bm.free()
    out: dict = {"object": ob.name, **U.counts(ob), "rings": len(rings)}
    if half and U.flag(p, "mirror_modifier", M, True):
        mod = ob.modifiers.new("mirror", "MIRROR")
        mod.use_axis = (ax != 0, ax == 0, False)
        mod.use_clip = True
        mod.use_mirror_merge = True
        mod.merge_threshold = 1e-4
        out["mirror"] = mod.name
    if pnames:
        ob["satk_part_names"] = json.dumps(pnames)
        ob["satk_blank"] = json.dumps({"kind": "loft", "piece": ob.name, "primary": True, "part_names": pnames,
                                       "parts": pspecs, "tier": None})
        ob["satk_blank_model"] = U.text(p, "model", M) or ob.name
        out["parts"] = counts_parts
    out["changed"] = [ob.name]
    return out


# --------------------------------------------------------------------------- sweep


def _curve_points(ctx, name: str, M: str) -> tuple[list, bool]:
    o = ctx.obj(name)
    if o.type != "CURVE":
        raise U.bad(M, f"path {o.name} is a {o.type}, not a CURVE", hint="give points or a mesh.curve object")
    bpy.context.view_layer.update()
    sp = o.data.splines[0] if len(o.data.splines) else None
    if sp is None:
        raise U.bad(M, f"curve {o.name} has no spline")
    mw = o.matrix_world
    pts = []
    if sp.type == "BEZIER":
        from mathutils.geometry import interpolate_bezier

        bp = list(sp.bezier_points)
        segs = list(zip(bp, bp[1:])) + ([(bp[-1], bp[0])] if sp.use_cyclic_u else [])
        res = max(2, o.data.resolution_u)
        for a, b in segs:
            seg = interpolate_bezier(a.co, a.handle_right, b.handle_left, b.co, res + 1)
            pts += [mw @ v for v in seg[:-1]]
        if not sp.use_cyclic_u:
            pts.append(mw @ bp[-1].co)
    else:
        pts = [mw @ Vector(q.co[:3]) for q in sp.points]
    return [tuple(v) for v in pts], bool(sp.use_cyclic_u)


def _profile(p: dict, M: str) -> tuple[list[tuple[float, float]], bool]:
    prof = p.get("profile")
    psamp = U.integer(p, "profile_samples", M, None, lo=3, hi=256)
    if isinstance(prof, dict):
        sh = dict(prof)
        if "z" not in sh and U.is_num(sh.get("h")):
            sh["z"] = -float(sh["h"]) / 2       # a shape profile is centred on the path unless z is given
        s = S.check_shape(sh, f"{M} profile")
        n = psamp or 12
        if n % 2:
            raise U.bad(M, f"a shape profile needs an even 'profile_samples', got {n}")
        loop = S.half_to_loop(S.shape_half(s, n // 2))
        nh = n // 2
        return loop[nh:] + loop[:nh], True
    pts = U.points2(p, "profile", M, min_n=2)
    pclosed = U.flag(p, "profile_closed", M, len(pts) >= 3)
    if psamp:
        pts = [tuple(q) for q in S.resample(pts, psamp, closed=pclosed)]
    return pts, pclosed


def _scale_fn(p: dict, M: str):
    v = p.get("scale")
    if v is None:
        return lambda t: (1.0, 1.0)
    if U.is_num(v):
        return lambda t: (float(v), float(v))
    if not isinstance(v, list) or not v:
        raise U.bad(M, "'scale' must be a number, a list of numbers or a list of [sa, sb] along the path")
    rows = []
    for x in v:
        if U.is_num(x):
            rows.append([float(x), float(x)])
        elif isinstance(x, list) and len(x) == 2 and all(U.is_num(y) for y in x):
            rows.append([float(x[0]), float(x[1])])
        else:
            raise U.bad(M, f"'scale' entry {x!r}: a number or [sa, sb]")
    if len(rows) == 1:
        return lambda t: tuple(rows[0])
    f = S.pchip_many([i / (len(rows) - 1) for i in range(len(rows))], rows)
    return lambda t: tuple(f(t))


def sweep(ctx, p: dict) -> dict:
    """Sweep a profile (points [[a, b]] or a shape) along a path (points or a curve object): bumpers, mouldings, sills, rails, pipes; samples, smooth, closed, scale, twist, cap.

    The profile lies in the plane across the path: ``a`` to the LEFT of the path seen from above (``up`` x
    tangent; ``flip`` swaps), ``b`` along ``up`` (default [0, 0, 1]). A shape profile is centred on the path
    unless it gives ``z``; ``profile_samples`` = its points (even, default 12). ``samples`` = stations spaced
    evenly along the path (a smooth curve through the points with ``smooth``, default; omitted = the points as
    given). ``closed`` makes the path a loop; ``profile_closed`` (default true) a tube. ``scale`` (a number, a
    list along the path, or [sa, sb] pairs) tapers it; ``twist`` = degrees from start to end. ``cap`` true|false|
    start|end closes the ends. ``half``: the path starts on the x = 0 plane (a bumper or moulding of a mirrored
    half body): the start leaves along x, stays open and a MIRROR modifier makes the other half. Welded,
    smooth-shaded, UVs u around the profile and v along the path.
    """
    M = "mesh.sweep"
    name = U.text(p, "name", M, "sweep")
    path = U.require(p, "path", M, hint="a list of [x, y, z] or the name of a curve object")
    closed = U.flag(p, "closed", M, False)
    if isinstance(path, str):
        pts, cyc = _curve_points(ctx, path, M)
        closed = closed or cyc
    else:
        if not isinstance(path, list) or not 2 <= len(path) <= 4096:
            raise U.bad(M, "'path' must be a curve object name or a list of 2-4096 [x, y, z]")
        pts = [tuple(U.vec({"v": q}, "v", M)) for q in path]
    samples = U.integer(p, "samples", M, None, lo=2, hi=4096)
    stations = S.sweep_path(pts, samples, closed=closed, smooth=U.flag(p, "smooth", M, True))
    if len(stations) < 2:
        raise U.bad(M, "the path needs two or more distinct points")
    prof, pclosed = _profile(p, M)
    if U.flag(p, "flip", M, False):
        prof = [(-a, b) for a, b in reversed(prof)]
    up = Vector(U.vec(p, "up", M, [0.0, 0.0, 1.0]))
    if up.length < 1e-9:
        raise U.bad(M, "'up' must not be zero")
    up.normalize()
    twist = math.radians(U.num(p, "twist", M, 0.0))
    scale_at = _scale_fn(p, M)
    capv = p.get("cap", True)
    if capv not in (True, False, "start", "end"):
        raise U.bad(M, f"'cap' must be true, false, start or end, got {capv!r}")
    half = U.flag(p, "half", M, False)
    if half:
        if closed or abs(stations[0][0]) > 1e-4:
            raise U.bad(M, f"half: the path must start on the mirror plane x = 0 (it starts at x = "
                        f"{stations[0][0]:g}) and be open", hint="start the path at [0, y, z]")
        capv = {True: "end", "start": False}.get(capv, capv)
    P = [Vector(s) for s in stations]
    m = len(P)
    seg = [(P[(i + 1) % m] - P[i]).length for i in range(m if closed else m - 1)]
    total = sum(seg[: m - 1]) or 1.0
    tpos = [0.0]
    for i in range(m - 1):
        tpos.append(tpos[-1] + seg[i])
    tnorm = [x / total for x in tpos]
    tang = []
    for i in range(m):
        if closed:
            d = P[(i + 1) % m] - P[i - 1]
        elif i == 0:
            d = P[1] - P[0]
        elif i == m - 1:
            d = P[-1] - P[-2]
        else:
            d = (P[i + 1] - P[i]).normalized() + (P[i] - P[i - 1]).normalized()
        tang.append(d.normalized() if d.length > 1e-12 else Vector((0.0, 1.0, 0.0)))
    if half:    # leave the mirror plane at a right angle, so the two halves meet without a kink
        tang[0] = Vector((1.0 if P[1].x >= P[0].x else -1.0, 0.0, 0.0))
    frames = []
    last_n = None
    for t in tang:
        nv = up.cross(t)
        if nv.length < 1e-6:
            nv = last_n.copy() if last_n is not None else (Vector((1.0, 0.0, 0.0)) if abs(t.x) < 0.9 else Vector((0.0, 1.0, 0.0)))
            nv = (nv - t * nv.dot(t))
        nv.normalize()
        bv = t.cross(nv).normalized()
        frames.append((nv, bv))
        last_n = nv
    plen = S.polyline_length(prof, closed=pclosed) or 1.0
    pu = [0.0]
    for q0, q1 in zip(prof, prof[1:]):
        pu.append(pu[-1] + math.dist(q0, q1) / plen)
    if pclosed:
        pu.append(1.0)
    k = len(prof)
    bm = bmesh.new()
    try:
        uvl = bm.loops.layers.uv.new("UVMap")
        rings = []
        for i in range(m):
            nv, bv = frames[i]
            sa, sb = scale_at(tnorm[i])
            ang = twist * tnorm[i]
            ca, sn = math.cos(ang), math.sin(ang)
            ring = []
            for a, b in prof:
                ra, rb = (a * ca - b * sn) * sa, (a * sn + b * ca) * sb
                ring.append(bm.verts.new(P[i] + nv * ra + bv * rb))
            rings.append(ring)
        vv = [x / plen for x in tpos] + ([(tpos[-1] + seg[-1]) / plen] if closed else [])
        pairs = list(zip(range(m - 1), range(1, m))) + ([(m - 1, 0)] if closed else [])
        for j, (i0, i1) in enumerate(pairs):
            r0, r1 = rings[i0], rings[i1]
            for q in range(k if pclosed else k - 1):
                q1 = (q + 1) % k
                f = bm.faces.new((r0[q], r0[q1], r1[q1], r1[q]))
                f.smooth = True
                for lp, uv in zip(f.loops, ((pu[q], vv[j]), (pu[q + 1], vv[j]), (pu[q + 1], vv[j + 1]),
                                            (pu[q], vv[j + 1]))):
                    lp[uvl].uv = uv
        caps = []
        if pclosed and not closed and capv:
            for want, ring in ((capv in (True, "start"), rings[0]), (capv in (True, "end"), rings[-1])):
                if want:
                    try:
                        f = bm.faces.new(ring)
                    except ValueError:
                        continue
                    for lp in f.loops:
                        lp[uvl].uv = (0.5, 0.5)
                    caps.append(f)
        if half:
            for v in rings[0]:
                v.co.x = 0.0
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)
        faces = [f for f in bm.faces if f.is_valid]
        score = 0.0
        for f in faces:
            if f in caps:
                continue
            c = f.calc_center_median()
            near = min(range(m), key=lambda i: (P[i] - c).length_squared)
            score += f.normal.dot(c - P[near]) * f.calc_area()
        if score < 0:
            bmesh.ops.reverse_faces(bm, faces=[f for f in faces if f not in caps])
        for f in caps:
            if not f.is_valid:
                continue
            c = f.calc_center_median()
            i = min(range(m), key=lambda j: (P[j] - c).length_squared)
            want = -tang[0] if i < m / 2 else tang[-1]
            if f.normal.dot(want) < 0:
                f.normal_flip()
            f.smooth = False
            for e in f.edges:
                e.smooth = False
        ob = U.new_mesh_object(ctx, name, bm, p.get("collection"))
    finally:
        bm.free()
    out = {"object": ob.name, **U.counts(ob), "stations": m}
    if half:
        mod = ob.modifiers.new("mirror", "MIRROR")
        mod.use_axis = (True, False, False)
        mod.use_clip = True
        mod.use_mirror_merge = True
        mod.merge_threshold = 1e-4
        out["mirror"] = mod.name
    out["changed"] = [ob.name]
    return out


# --------------------------------------------------------------------------- relax


def relax(ctx, p: dict) -> dict:
    """Relax (smooth) the selected vertices: iterations, factor 0-1; keep_boundary leaves open borders, keep_volume (Taubin) stops shrinking.

    Evens out lumps and kinks after edits without flattening the form: every pass moves each vertex towards the
    average of its neighbours by ``factor``; with ``keep_volume`` (default) a second, negative pass keeps the size.
    Vertices on the mirror plane of a MIRROR modifier stay on it.
    """
    M = "mesh.relax"
    o = U.mesh_obj(ctx, p, M)
    it = U.integer(p, "iterations", M, 5, lo=1, hi=200)
    lam = U.num(p, "factor", M, 0.5, lo=0.0, hi=1.0)
    keep_b = U.flag(p, "keep_boundary", M, True)
    keep_v = U.flag(p, "keep_volume", M, True)
    lock = [False, False, False]
    for m in o.modifiers:
        if m.type == "MIRROR":
            for i in range(3):
                lock[i] = lock[i] or bool(m.use_axis[i])
    with U.edit_mesh(o) as bm:
        faces, verts = U.select_verts(o, bm, p.get("select"), M)
        U.require_faces(faces, M, p.get("select"))
        movable = [v for v in verts if not (keep_b and v.is_boundary)]
        nbr = {v: [e.other_vert(v) for e in v.link_edges] for v in movable}
        start = {v: v.co.copy() for v in movable}
        mu = -lam / (1.0 - 0.1 * lam) if keep_v else 0.0

        def step(f: float) -> None:
            new = {}
            for v in movable:
                ns = nbr[v]
                if not ns:
                    continue
                avg = sum((w.co for w in ns), Vector()) / len(ns)
                d = (avg - v.co) * f
                for i in range(3):
                    if lock[i] and abs(v.co[i]) < 1e-5:
                        d[i] = 0.0
                new[v] = v.co + d
            for v, c in new.items():
                v.co = c

        for _ in range(it):
            step(lam)
            if keep_v:
                step(mu)
        moved = max(((v.co - start[v]).length for v in movable), default=0.0)
    return {"verts": len(movable), "max_move_mm": round(moved * 1000, 1), "changed": [o.name]}


# --------------------------------------------------------------------------- deform

_DEFORM = ("taper", "bend", "twist", "cast", "lattice")


def _origin_empty(o, origin, name: str):
    e = bpy.data.objects.new(name, None)
    for c in o.users_collection:
        c.objects.link(e)
        break
    e.empty_display_size = 0.2
    e["satk_helper"] = o.name
    e.matrix_world = o.matrix_world @ Matrix.Translation(Vector(origin))
    return e


def _apply_mod(o, mod, M: str) -> None:
    with bpy.context.temp_override(object=o, active_object=o, selected_objects=[o], selected_editable_objects=[o]):
        bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=0)
        r = bpy.ops.object.modifier_apply(modifier=mod.name)
    if "FINISHED" not in r:
        raise SatkError("EXTERNAL_TOOL", f"{M}: Blender could not apply the deform on {o.name}")


def deform(ctx, p: dict) -> dict:
    """Deform a whole mesh or a selection: kind taper|bend|twist|cast|lattice with params; apply=false keeps a live modifier.

    ``params``: taper {axis z, factor, limits [0, 1], origin [x, y, z]}; bend {axis, angle deg, limits, origin};
    twist {axis, angle deg, limits, origin}; cast {type sphere|cylinder|cuboid, factor, radius, axes "xyz",
    origin}; lattice {resolution [u, v, w], moves: [{point: [i, j, k] | near: [x, y, z], radius?, offset:
    [dx, dy, dz] m}], interpolation bspline|linear}. ``select`` limits it to those vertices (a vertex group).
    Origins and lattices are helper objects ``<object>.deform*`` (removed when applied).
    """
    M = "mesh.deform"
    o = U.mesh_obj(ctx, p, M)
    kind = U.text(p, "kind", M, required=True, choices=_DEFORM)
    prm = p.get("params") or {}
    if not isinstance(prm, dict):
        raise U.bad(M, "'params' must be an object")
    apply = U.flag(p, "apply", M, False)
    bpy.context.view_layer.update()
    if apply and o.data.users > 1:
        raise U.bad(M, f"{o.name} shares its mesh with another object", hint="scene.duplicate linked=false")
    helpers = []
    group = None
    if p.get("select") is not None:
        with U.edit_mesh(o) as bm:
            faces, verts = U.select_verts(o, bm, p.get("select"), M)
            U.require_faces(faces, M, p.get("select"))
            group = f"deform_{len(o.modifiers)}"
            U.save_group(o, bm, verts, group, M)
    ax_name = U.text(prm, "axis", M, "z", choices=("x", "y", "z")).upper()
    if kind in ("taper", "bend", "twist"):
        mod = o.modifiers.new(f"deform_{kind}", "SIMPLE_DEFORM")
        mod.deform_method = kind.upper()
        mod.deform_axis = ax_name
        if kind == "taper":
            mod.factor = U.num(prm, "factor", M, 0.3, lo=-10.0, hi=10.0)
        else:
            mod.angle = math.radians(U.num(prm, "angle", M, 30.0 if kind == "bend" else 45.0, lo=-3600.0, hi=3600.0))
        lim = U.vec(prm, "limits", M, [0.0, 1.0], n=2)
        mod.limits = (max(0.0, min(lim)), min(1.0, max(lim)))
        if prm.get("origin") is not None:
            e = _origin_empty(o, U.vec(prm, "origin", M), f"{o.name}.deform_origin")
            mod.origin = e
            helpers.append(e)
    elif kind == "cast":
        mod = o.modifiers.new("deform_cast", "CAST")
        mod.cast_type = U.text(prm, "type", M, "sphere", choices=("sphere", "cylinder", "cuboid")).upper()
        mod.factor = U.num(prm, "factor", M, 0.5, lo=-10.0, hi=10.0)
        mod.radius = U.num(prm, "radius", M, 0.0, lo=0.0)
        axes = U.text(prm, "axes", M, "xyz")
        if not set(axes) <= set("xyz"):
            raise U.bad(M, "params.axes must be letters of xyz")
        mod.use_x, mod.use_y, mod.use_z = ("x" in axes, "y" in axes, "z" in axes)
        if prm.get("origin") is not None:
            e = _origin_empty(o, U.vec(prm, "origin", M), f"{o.name}.deform_origin")
            mod.object = e
            mod.use_transform = True
            helpers.append(e)
    else:
        res = U.vec(prm, "resolution", M, [2.0, 2.0, 2.0])
        if not all(int(x) == x and 2 <= x <= 32 for x in res):
            raise U.bad(M, "params.resolution must be 3 integers 2-32")
        lat = bpy.data.lattices.new(f"{o.name}.deform_lattice")
        lat.points_u, lat.points_v, lat.points_w = (int(x) for x in res)
        interp = "KEY_" + U.text(prm, "interpolation", M, "bspline", choices=("bspline", "linear", "cardinal")).upper()
        lat.interpolation_type_u = lat.interpolation_type_v = lat.interpolation_type_w = interp
        lo = Vector([min(v.co[i] for v in o.data.vertices) for i in range(3)])
        hi = Vector([max(v.co[i] for v in o.data.vertices) for i in range(3)])
        size = Vector([max(hi[i] - lo[i], 1e-3) * 1.02 for i in range(3)])
        lo_ = (lo + hi) / 2
        lo_o = bpy.data.objects.new(lat.name, lat)
        for c in o.users_collection:
            c.objects.link(lo_o)
            break
        lo_o.matrix_world = o.matrix_world @ Matrix.Translation(lo_) @ Matrix.Diagonal((*size, 1.0))
        lo_o["satk_helper"] = o.name
        helpers.append(lo_o)
        moves = prm.get("moves") or []
        if not isinstance(moves, list):
            raise U.bad(M, "params.moves must be a list")
        pu, pv, pw = lat.points_u, lat.points_v, lat.points_w
        moved = 0
        for mv in moves:
            if not isinstance(mv, dict):
                raise U.bad(M, "a lattice move is {point: [i, j, k] | near: [x, y, z], radius?, offset: [dx, dy, dz]}")
            off = Vector(U.vec(mv, "offset", M, required=True))
            off_local = Vector([off[i] / size[i] for i in range(3)])
            idx = []
            if mv.get("point") is not None:
                ijk = U.vec(mv, "point", M)
                i, j, k = (int(x) for x in ijk)
                if not (0 <= i < pu and 0 <= j < pv and 0 <= k < pw):
                    raise U.bad(M, f"lattice point {ijk} is outside {pu}x{pv}x{pw}")
                idx = [i + j * pu + k * pu * pv]
            elif mv.get("near") is not None:
                c = Vector(U.vec(mv, "near", M))
                rad = U.num(mv, "radius", M, max(size) / max(pu, pv, pw))
                for n_, pt in enumerate(lat.points):
                    world = lo_ + Vector([pt.co_deform[i] * size[i] for i in range(3)])
                    if (world - c).length <= rad:
                        idx.append(n_)
            else:
                raise U.bad(M, "a lattice move needs 'point' or 'near'")
            for n_ in idx:
                lat.points[n_].co_deform = lat.points[n_].co_deform + off_local
            moved += len(idx)
        mod = o.modifiers.new("deform_lattice", "LATTICE")
        mod.object = lo_o
    if group and hasattr(mod, "vertex_group"):
        mod.vertex_group = group
    out: dict = {"object": o.name, "kind": kind}
    if apply:
        bpy.context.view_layer.update()
        _apply_mod(o, mod, M)
        for h in helpers:
            data = h.data
            bpy.data.objects.remove(h, do_unlink=True)
            if data is not None and getattr(data, "users", 1) == 0 and isinstance(data, bpy.types.Lattice):
                bpy.data.lattices.remove(data)
        if group:
            vg = o.vertex_groups.get(group)
            if vg is not None:
                o.vertex_groups.remove(vg)
        out["applied"] = True
    else:
        out["modifier"] = mod.name
        if helpers:
            out["helpers"] = [h.name for h in helpers]
    out["changed"] = [o.name]
    return out


METHODS = {"mesh.loft": loft, "mesh.sweep": sweep, "mesh.relax": relax, "mesh.deform": deform}
