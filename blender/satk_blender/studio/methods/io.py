# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``io.*``: bring DFF models into the session (DragonFF) - your own files to keep working on, or a vanilla
model as a *ghost*: a reference tagged ``satk_ghost`` that snapshots draw as a wire overlay or line up beside
the model, that stats never count and that an export never writes.

``io.ghost`` takes ``sid`` (``model:426``): the satk client resolves it to the cached DFF/TXD of the
vanilla profile before the call reaches Blender (``plan``). ``io.import`` takes a ``dff`` path (+ ``txd``).
"""

from __future__ import annotations

import os

import bpy
from mathutils import Vector

from satk.core.errors import SatkError

from . import _util as U

GHOSTS = "satk_ghost"


def _import(plan: dict, ctx) -> tuple[list, dict]:
    from ... import importer  # GPL importer of satk_blender (DragonFF)
    from ... import common

    common.load_dragonff(getattr(ctx.host, "dragonff", None) or None)
    before = set(bpy.data.objects)
    res = importer.import_model(plan, {"clean": False, "col": False})
    new = [o for o in bpy.data.objects if o not in before]
    return new, res


def _plan_for_file(dff: str, txd: list[str], method: str) -> dict:
    p = os.path.abspath(dff)
    if not p.lower().endswith(".dff") or not os.path.isfile(p):
        raise SatkError("NOT_FOUND", f"{method}: no DFF file {p.replace(os.sep, '/')}")
    chain = []
    for t in txd:
        tp = os.path.abspath(t)
        if not os.path.isfile(tp):
            raise SatkError("NOT_FOUND", f"{method}: no TXD file {tp.replace(os.sep, '/')}")
        chain.append(tp)
    if not chain:
        sib = os.path.splitext(p)[0] + ".txd"
        if os.path.isfile(sib):
            chain.append(sib)
    stem = os.path.splitext(os.path.basename(p))[0]
    return {"model": {"sid": f"file:{stem}", "name": stem, "sec": None, "dff": p, "txd": chain}}


def _plan(p: dict, method: str) -> dict:
    if isinstance(p.get("plan"), dict) and isinstance(p["plan"].get("model"), dict):
        return p["plan"]
    if p.get("dff"):
        txd = p.get("txd") or []
        if isinstance(txd, str):
            txd = [txd]
        return _plan_for_file(str(p["dff"]), list(txd), method)
    if p.get("sid"):
        raise SatkError("NOT_READY", f"{method}: 'sid' is resolved by the satk client",
                        hint="call it through satk blender call / satk.studio.api, or pass 'dff'")
    raise U.bad(method, "'sid' (model:426) or 'dff' (a path) is required")


def import_(ctx, p: dict) -> dict:
    """Import a DFF (+TXD; the sibling .txd is found) to keep editing it: dff path, txd path(s), sid via the client."""
    M = "io.import"
    plan = _plan(p, M)
    new, res = _import(plan, ctx)
    names = sorted(o.name for o in new)
    out = {"collection": res.get("collection"), "objects": len(new), "kind": res.get("kind")}
    st = res.get("stats") or {}
    for k in ("wheels", "missing_tex", "import_s"):
        if st.get(k):
            out[k] = st[k]
    if res.get("warnings"):
        out["warn"] = res["warnings"][:3]
    out["changed"] = [n for n in names if bpy.data.objects[n].type in ("MESH", "EMPTY")][:60]
    return out


def ghost(ctx, p: dict) -> dict:
    """Import a vanilla model (sid) or a DFF as a ghost reference (never counted or exported); offset, replace."""
    M = "io.ghost"
    plan = _plan(p, M)
    if U.flag(p, "replace", M, True):
        old = [o for o in bpy.data.objects if o.get("satk_ghost")]
        if old:
            bpy.data.batch_remove(old)
    new, res = _import(plan, ctx)
    root_coll = ctx.collection(GHOSTS)
    coll = bpy.data.collections.get(res.get("collection") or "")
    if coll is not None and coll.name not in root_coll.children:
        for parent in [c for c in bpy.data.collections if coll.name in c.children] + [ctx.scene.collection]:
            if coll.name in parent.children:
                parent.children.unlink(coll)
        root_coll.children.link(coll)
    sid = (plan.get("model") or {}).get("sid")
    for o in new:
        o["satk_ghost"] = 1
        if sid:
            o["satk_ghost_of"] = str(sid)
        if o.name.split(".")[0].lower().endswith("_vlo"):  # the ghost shows the full-detail parts only
            o.hide_render = True
            o.hide_set(True)
    off = U.vec(p, "offset", M)
    if off is not None:
        for o in new:
            if o.parent is None:
                o.location = o.location + Vector(off)
    bpy.context.view_layer.update()  # matrices of the parented parts (wheels) are stale until now
    shown = [o for o in new if o.type == "MESH" and not o.hide_render and o.visible_get()]
    corners = [o.matrix_world @ Vector(b) for o in shown for b in o.bound_box]
    lo = [min((c[i] for c in corners), default=0.0) for i in range(3)]
    hi = [max((c[i] for c in corners), default=0.0) for i in range(3)]
    out = {"ghost": sid or (plan.get("model") or {}).get("name"), "objects": len(new),
           "dims": [round(b - a, 3) for a, b in zip(lo, hi)], "bbox": [U.r3(lo), U.r3(hi)]}
    if res.get("warnings"):
        out["warn"] = res["warnings"][:3]
    return out


METHODS = {"io.import": import_, "io.ghost": ghost}
