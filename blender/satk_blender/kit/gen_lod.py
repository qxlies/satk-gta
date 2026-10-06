# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.lod``: a map model's LOD (``lod<name[3:]>``) from its HD mesh at about 0.21 of the triangles
(vanilla LOD/HD p50 0.208, LOD draw 800); LOD budgets do not grow with the tier. A prop scaffolded without a LOD
slot gets one on the first call (the slot is made from the HD materials), so nothing is re-scaffolded.
"""

from __future__ import annotations

import bpy

from satk.core.errors import SatkError

from . import util as U
from .gen_vlo import _tris, decimate_to, merged_mesh, replace_mesh

__all__ = ["lod_method"]


def lod_method(ctx, p: dict) -> dict:
    """Fill a building's LOD slot (lod<name[3:]>) from the HD mesh at about 0.21 of its triangles."""
    coll = U.clump(p.get("model"))
    name = str(coll.get("satk_name"))
    lc = next((c for c in bpy.data.collections if str(c.get("satk_lod_of", "")).lower() == name.lower()), None)
    ratio = U.num(p, "ratio", 0.21, "kit.lod", 0.01, 1.0)
    src = [o for o in coll.objects if o.type == "MESH" and len(o.data.polygons) and o.get("satk_role") in ("root", "part")]
    made = False
    if lc is None:
        if str(coll.get("satk_group")) != "world":
            raise SatkError("NOT_FOUND", f"kit.lod: {name} has no LOD slot",
                            hint="vehicles carry _vlo parts (kit.vlo); map models get a LOD slot")
        if not src:
            raise SatkError("BAD_PARAMS", "kit.lod: model the HD mesh first")
        from satk.kit.plan import lod_name

        from .template import make_lod_slot

        mats = []
        for o in src:
            for m in o.data.materials:
                if m is not None and m not in mats:
                    mats.append(m)
        make_lod_slot(name, lod_name(name), str(coll.get("satk_kit")), mats)
        lc = next(c for c in bpy.data.collections if str(c.get("satk_lod_of", "")).lower() == name.lower())
        made = True
    slot = next((o for o in lc.objects if o.type == "MESH"), None)
    if not src or slot is None:
        raise SatkError("BAD_PARAMS", "kit.lod: model the HD mesh first")
    me = merged_mesh(src, slot, slot.name)
    t = _tris(me)
    me = decimate_to(me, max(12, int(round(t * ratio))), slot.name)
    replace_mesh(slot, me)
    out = {"object": slot.name, "tris": U.tris(slot), "hd_tris": t, "ratio": round(U.tris(slot) / max(1, t), 3),
           "changed": [slot.name]}
    if made:
        out["slot"] = "created"
    return out
