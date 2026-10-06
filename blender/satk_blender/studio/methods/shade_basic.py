# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``shade.basic``: smooth or flat shading of whole objects, or smooth-by-angle (sharp edges above the angle).

This is the quick blockout switch. The vanilla-faithful order (weld, smooth every face, mark sharp by the
class rule, weighted normals last, verify on the re-read DFF) is the kit's ``kit.shade`` (``sa_shade``);
the stats of every step report ``shade.normal_bend`` and ``shade.flat_share`` either way.
"""

from __future__ import annotations

import math

import bpy

from . import _util as U


def basic(ctx, p: dict) -> dict:
    """Shade objects smooth, flat, or smooth by angle (mode, angle deg default 30, keep_sharp keeps marked edges)."""
    M = "shade.basic"
    objs = U.resolve(ctx, p, M, types=("MESH",))
    mode = U.text(p, "mode", M, "smooth", choices=("smooth", "flat", "angle"))
    ang = U.num(p, "angle", M, 30.0, lo=0.0, hi=180.0)
    keep = U.flag(p, "keep_sharp", M, True)
    with bpy.context.temp_override(object=objs[0], active_object=objs[0], selected_objects=objs,
                                   selected_editable_objects=objs):
        if mode == "flat":
            bpy.ops.object.shade_flat(keep_sharp_edges=keep)
        elif mode == "smooth":
            bpy.ops.object.shade_smooth(keep_sharp_edges=keep)
        else:
            bpy.ops.object.shade_smooth_by_angle(angle=math.radians(ang), keep_sharp_edges=keep)
    out = {"objects": [o.name for o in objs], "mode": mode, "changed": [o.name for o in objs]}
    if "kit.shade" in (getattr(ctx, "methods", None) or {}):
        out["note"] = "kit.shade applies the vanilla shading order before export"
    return out


METHODS = {"shade.basic": basic}
