# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.wheel``: a vehicle wheel (tyre + rim) revolved around X with the tier's side count.

Diameter = the IDE ``wheel_scale`` (the mesh diameter must match it), sides 12 (vanilla) or 16-24 (sa_plus,
default 20). Tyre UVs map ``vehicletyres128`` the way vanilla wheels do (landstal, premier): the texture is four
tyres of 32 px (one per v quarter), each a tread column (u 0-0.25) and a sidewall column (u 0.25-0.5, a radial
gradient that ends in a light rim band at the bottom). Every segment around the wheel takes the full column width
(u alternates segment by segment, mirrored like vanilla); the tread maps its width across the quarter, the
sidewall its radius (rim band at the rim, dark at the shoulder). Sampling the columns as a disk (the old mapping)
averaged the light band into the rubber: the tyre rendered beige. The rim takes the own ``<model>92wheel64``
texture. The outer face points +X (the right wheel); the engine mirrors it for the left.
"""

from __future__ import annotations

import math

import bmesh
import bpy

from satk.core.errors import SatkError

from . import materials as M
from . import util as U

__all__ = ["build_wheel", "PROFILE", "wheel_method"]

#: (x as a share of half the width, radius as a share of R): outer sidewall -> tread -> inner sidewall.
PROFILE = ((1.0, 0.64), (1.0, 0.86), (0.86, 0.97), (0.62, 1.0), (-0.62, 1.0), (-0.86, 0.97), (-1.0, 0.86),
           (-1.0, 0.64))
_TREAD_BANDS = (2, 3, 4)     # bands between profile points 2-3, 3-4, 4-5
_RIM_INSET = 0.25            # the rim face sits this share of half the width inside the outer sidewall


#: The tread and sidewall columns of a vehicletyres128 quarter (u, as vanilla: a texel inside each edge).
_TREAD_U = (0.004, 0.243)
_SIDE_U = (0.253, 0.495)
_V_PAD = 0.006               # keep inside the 32 px quarter (no bleeding into the next tyre)


def _quarter_v(q: int, f: float) -> float:
    """Blender v of the share ``f`` (0 = top row, 1 = bottom row) of tyre quarter ``q`` (DFF v runs down)."""
    v_dff = 0.25 * q + _V_PAD + (0.25 - 2 * _V_PAD) * max(0.0, min(1.0, f))
    return 1.0 - v_dff


def _tread_uv(k: int, step: int, sides: int, q: int) -> tuple[float, float]:
    """Tread: the segment spans the column width (u by the step's parity), the tread width spans the quarter."""
    x0 = PROFILE[2][0]
    f = (x0 - PROFILE[k][0]) / (2 * x0)
    return _TREAD_U[step % 2], _quarter_v(q, f)


def _side_uv(k: int, step: int, sides: int, q: int) -> tuple[float, float]:
    """Sidewall: the segment spans the column width, the radius runs from the shoulder (top) to the rim band."""
    r0, r1 = PROFILE[0][1], PROFILE[2][1]
    rho = (PROFILE[k][1] - r0) / (r1 - r0)
    return _SIDE_U[step % 2], _quarter_v(q, 1.0 - rho)


def build_wheel(name: str, *, radius: float, width: float, sides: int, mats: dict, quarter: int = 1):
    """A new mesh ``name``. ``mats``: role -> material (``tyre``, ``rim``)."""
    if sides < 6 or sides > 64:
        raise SatkError("BAD_PARAMS", f"kit.wheel: sides must be 6..64, got {sides}")
    hw = width / 2
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    try:
        uv = bm.loops.layers.uv.new("UVMap")
        rings = [[bm.verts.new((x * hw, math.cos(2 * math.pi * i / sides) * rr * radius,
                                math.sin(2 * math.pi * i / sides) * rr * radius)) for i in range(sides)]
                 for x, rr in PROFILE]
        rim_x = hw * (1.0 - _RIM_INSET)
        rim_r = PROFILE[0][1] * radius
        rim = [bm.verts.new((rim_x, math.cos(2 * math.pi * i / sides) * rim_r,
                             math.sin(2 * math.pi * i / sides) * rim_r)) for i in range(sides)]
        hub = bm.verts.new((rim_x - 0.05 * hw, 0.0, 0.0))
        for k in range(len(PROFILE) - 1):
            tread = k in _TREAD_BANDS
            for i in range(sides):
                j = (i + 1) % sides
                # winding a, d, c, b: outward normals (radially out on the tread, +X / -X on the sidewalls)
                corners = ((k, i, i), (k + 1, i, i), (k + 1, j, i + 1), (k, j, i + 1))
                f = bm.faces.new([rings[kk][ii] for kk, ii, _s in corners])
                f.material_index = 0
                for loop, (kk, _ii, step) in zip(f.loops, corners):
                    loop[uv].uv = _tread_uv(kk, step, sides, quarter) if tread else _side_uv(kk, step, sides, quarter)
        for i in range(sides):
            j = (i + 1) % sides
            f = bm.faces.new((rings[0][i], rings[0][j], rim[j], rim[i]))   # recess wall, faces the axis
            f2 = bm.faces.new((hub, rim[i], rim[j]))                        # rim face, +X
            for ff in (f, f2):
                ff.material_index = 1
                for loop in ff.loops:
                    co = loop.vert.co
                    loop[uv].uv = (0.5 + 0.5 * co.y / radius, 0.5 + 0.5 * co.z / radius)
        bm.normal_update()
        bm.to_mesh(me)
    finally:
        bm.free()
    for role in ("tyre", "rim"):
        me.materials.append(mats.get(role))
    me.update()
    return me


def wheel_method(ctx, p: dict) -> dict:
    """Build the wheel into the kit's 'wheel' slot: diameter = wheel_scale, 12 sides (vanilla) or 20 (sa_plus); tyre UVs on vehicletyres128."""
    coll = U.clump(p.get("model"))
    name = str(coll.get("satk_name"))
    tier = str(p.get("tier") or coll.get("satk_tier") or "sa_plus")
    slot_name = str(p.get("slot") or "wheel").lower()
    slot = next((o for o in coll.objects if str(o.get("satk_frame", o.name)).lower() == slot_name), None)
    if slot is None or slot.type != "MESH":
        raise SatkError("NOT_FOUND", f"kit.wheel: no mesh slot {slot_name!r} in {name}",
                        hint="vehicles with wheels have a 'wheel' slot under wheel_rf_dummy (kit.template)")
    ws = p.get("diameter")
    if ws is None:
        try:
            ws = (U.read_json(str(coll.get("satk_plan", ""))).get("anchors") or {}).get("wheel_scale")
        except SatkError:
            ws = None
    diameter = U.num({"d": ws if ws is not None else 0.7}, "d", 0.7, "kit.wheel", 0.1, 5.0)
    sides = int(U.num(p, "sides", 12 if tier == "vanilla" else 20, "kit.wheel", 6, 64))
    width = U.num(p, "width", round(diameter * 0.36, 3), "kit.wheel", 0.02, 3.0)
    quarter = int(U.num(p, "tyre", 1, "kit.wheel", 0, 3))
    from satk.kit import kinds as K

    mats = {r: M.ensure(name, K.preset(r, name)) for r in ("tyre", "rim")}
    me = build_wheel(slot.name, radius=diameter / 2, width=width, sides=sides, mats=mats, quarter=quarter)
    old = slot.data
    slot.data = me
    if old is not None and old.users == 0:
        bpy.data.meshes.remove(old)
    from .shade import shade_object

    sh = shade_object(slot, cls="vehicle", weighted=True)
    return {"object": slot.name, "sides": sides, "diameter": round(diameter, 3), "width": round(width, 3),
            "tris": U.tris(slot), "sharp": sh.get("sharp"), "changed": [slot.name]}
