# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.uv_region``: project faces into a named region of the shared vehicle atlases (``satk.kit.atlas``).

Faces are chosen by material role (``faces: "role:glass"``), material index, or all faces. Their UVs come
from a planar projection (``project: x|y|z|auto|keep``; ``x`` = side view, u along the length and v up the
height, which is how vanilla paints ``vehiclegrunge256``) and are fitted into the region rectangle keeping
the aspect ratio. The region's texture is assigned when the faces' material has none. For the UV2 env sheen
the second UV layer gets a copy (vehicle bodies carry 2 UV sets).
"""

from __future__ import annotations

import bmesh

from satk.core.errors import SatkError

from . import util as U

__all__ = ["uv_region_method", "project_faces"]


def _axis_uv(co, axis: str) -> tuple[float, float]:
    if axis == "x":
        return co.y, co.z
    if axis == "y":
        return co.x, co.z
    return co.x, co.y


def project_faces(bm, faces, uv_layer, project: str) -> tuple[float, float, float, float]:
    """Planar UVs of ``faces`` (or keep them); returns their bounding box ``(u0, v0, u1, v1)``."""
    us, vs = [], []
    for f in faces:
        axis = project
        if project == "auto":
            n = f.normal
            a = max(range(3), key=lambda k: abs(n[k]))
            axis = "xyz"[a]
        for loop in f.loops:
            if project != "keep":
                loop[uv_layer].uv = _axis_uv(loop.vert.co, axis)
            u, v = loop[uv_layer].uv
            us.append(u)
            vs.append(v)
    if not us:
        return 0.0, 0.0, 1.0, 1.0
    return min(us), min(vs), max(us), max(vs)


def _select(me, spec) -> set[int]:
    if spec in (None, "", "all"):
        return set(range(len(me.polygons)))
    spec = str(spec)
    if spec.startswith("role:"):
        role = spec[5:].lower()
        idx = {i for i, m in enumerate(me.materials) if m is not None and str(m.get("satk_role", "")).lower() == role}
        return {p.index for p in me.polygons if p.material_index in idx}
    if spec.startswith("material:"):
        try:
            mi = int(spec[9:])
        except ValueError:
            raise SatkError("BAD_PARAMS", f"kit.uv_region: faces {spec!r}: material:<index>") from None
        return {p.index for p in me.polygons if p.material_index == mi}
    if spec == "selected":
        return {p.index for p in me.polygons if p.select}
    raise SatkError("BAD_PARAMS", f"kit.uv_region: faces {spec!r}", hint="all | role:<role> | material:<i> | selected")


def uv_region_method(ctx, p: dict) -> dict:
    """Project faces (all, role:<role>, material:<i>) into a named atlas region (satk kit kinds --atlas) at its measured place."""
    from satk.kit import atlas

    o = U.obj(U.need(p, "object", "kit.uv_region"), "kit.uv_region")
    if o.type != "MESH":
        raise SatkError("BAD_PARAMS", f"kit.uv_region: {o.name} is not a mesh")
    reg = atlas.region(U.need(p, "region", "kit.uv_region"))
    project = str(p.get("project") or "auto").lower()
    if project not in ("x", "y", "z", "auto", "keep"):
        raise U.bad("kit.uv_region", f"project {project!r}", hint="x | y | z | auto | keep")
    margin = U.num(p, "margin", 0.03, "kit.uv_region", 0.0, 0.45)
    me = o.data
    if not me.uv_layers:
        me.uv_layers.new(name="UVMap")
    sel = _select(me, p.get("faces"))
    if not sel:
        raise SatkError("NOT_FOUND", f"kit.uv_region: no faces match {p.get('faces')!r} on {o.name}")
    bm = bmesh.new()
    try:
        bm.from_mesh(me)
        bm.faces.ensure_lookup_table()
        uvl = bm.loops.layers.uv.get(me.uv_layers[0].name) or bm.loops.layers.uv.active
        faces = [bm.faces[i] for i in sorted(sel)]
        box = project_faces(bm, faces, uvl, project)
        su, sv, ou, ov = atlas.fit(box, reg["rect"], margin)
        for f in faces:
            for loop in f.loops:
                u, v = loop[uvl].uv
                loop[uvl].uv = (u * su + ou, v * sv + ov)
        uv2 = None
        if len(me.uv_layers) > 1:
            uv2 = bm.loops.layers.uv.get(me.uv_layers[1].name)
            if uv2 is not None:
                for f in faces:
                    for loop in f.loops:
                        loop[uv2].uv = loop[uvl].uv
        bm.to_mesh(me)
    finally:
        bm.free()
    me.update()
    return {"object": o.name, "faces": len(sel), "region": reg["name"], "texture": reg["texture"],
            "support": reg.get("support"), "changed": [o.name]}
