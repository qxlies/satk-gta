# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``camera.*``: named cameras for snapshots (``snapshot: {"view": "<camera name>"}``).

Standard views (GTA axes, vehicles face +Y): ``front`` (camera on +Y), ``rear``, ``left`` (camera on -X),
``right``, ``top`` (orthographic) and ``3q`` (front-right, 25 deg up, perspective). A camera framed on
objects keeps its framing when the model changes; ``camera.views`` re-frames the standard set.
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

from satk.core.errors import SatkError
from satk.studio.core import readonly

from .. import snapshot as S
from ..stats import geometry_objects
from . import _util as U


def _fit_objects(ctx, p: dict, method: str) -> list:
    if p.get("fit") is not None:
        return U.resolve(ctx, {"objects": p["fit"]}, method)
    objs = [o for o in geometry_objects(ctx.scene) if o.visible_get() and not o.get("satk_ref")]
    if not objs:
        raise SatkError("NOT_FOUND", f"{method}: nothing to frame (no visible geometry)", hint="give 'fit'")
    return objs


def _camera(name: str):
    cam = bpy.data.objects.get(name)
    if cam is not None and cam.type != "CAMERA":
        raise SatkError("EXISTS", f"an object {name!r} exists and is not a camera")
    if cam is None:
        cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
        bpy.context.scene.collection.objects.link(cam)
    return cam


def add(ctx, p: dict) -> dict:
    """Add or re-frame a named camera: view (front|rear|left|right|top|3q) fitted to 'fit' objects, or location+target."""
    M = "camera.add"
    name = U.text(p, "name", M, required=True)
    cam = _camera(name)
    if p.get("location") is not None:
        loc = Vector(U.vec(p, "location", M))
        tgt = Vector(U.vec(p, "target", M, [0.0, 0.0, 0.0]))
        cam.location = loc
        d = tgt - loc
        if d.length < 1e-9:
            raise U.bad(M, "location and target are the same point")
        cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        cam.data.type = "ORTHO" if U.flag(p, "ortho", M, False) else "PERSP"
        if cam.data.type == "ORTHO":
            cam.data.ortho_scale = U.num(p, "ortho_scale", M, 5.0, lo=0.01)
        else:
            cam.data.lens = U.num(p, "lens", M, 50.0, lo=1.0, hi=500.0)
        cam.data.clip_end = max(1000.0, d.length * 4)
        cam["satk_view"] = "custom"
    else:
        view = U.text(p, "view", M, "3q", choices=tuple(S.VIEWS))
        lo, hi = S.bounds(_fit_objects(ctx, p, M))
        S.frame(cam, view, lo, hi, margin=U.num(p, "margin", M, S.MARGIN, lo=1.0, hi=4.0))
        cam["satk_view"] = view
    return {"camera": cam.name, "type": cam.data.type.lower(), "location": U.r3(cam.location),
            "rotation": U.r3([math.degrees(a) for a in cam.rotation_euler]), "changed": [cam.name]}


def views(ctx, p: dict) -> dict:
    """Add (or re-frame) cameras cam_<view> for views (default front, left, top, 3q) fitted to 'fit' objects."""
    M = "camera.views"
    want = p.get("views", ["front", "left", "top", "3q"])
    if not isinstance(want, list) or not want or not all(v in S.VIEWS for v in want):
        raise U.bad(M, f"'views' must be a list of {', '.join(S.VIEWS)}")
    lo, hi = S.bounds(_fit_objects(ctx, p, M))
    out = []
    for v in want:
        cam = _camera(f"{U.text(p, 'prefix', M, 'cam_')}{v}")
        S.frame(cam, v, lo, hi)
        cam["satk_view"] = v
        out.append(cam.name)
    return {"cameras": out, "changed": out}


@readonly
def list_(ctx, p: dict) -> dict:
    """Cameras of the scene with their view, type and position."""
    rows = []
    for o in sorted((o for o in ctx.scene.objects if o.type == "CAMERA"), key=lambda o: o.name):
        rows.append({"name": o.name, "view": o.get("satk_view", ""), "type": o.data.type.lower(),
                     "location": U.r3(o.location)})
    return {"cameras": rows}


METHODS = {"camera.add": add, "camera.views": views, "camera.list": list_}
