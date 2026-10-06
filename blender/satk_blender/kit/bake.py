# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.bake``: Cycles bakes for photo-like textures without photos - ambient occlusion and an edge mask.

The edge mask is ``1 - dot(Bevel normal, true normal)``, scaled and clamped, baked as emission: worn edges
for grime and paint chips (Pointiness is useless at SA polygon counts; EEVEE cannot bake). The object
needs non-overlapping UVs (its first UV layer). Images go to ``<session>/bake/<object>_{ao,edge}.png``;
feed them to ``texture.finish --mask`` (AO) and ``--edge`` (edge mask). ``size`` is one number (square) or
``[w, h]`` / ``"WxH"``: bake at the texture's own size (a 256x128 texture gets 256x128 masks).
"""

from __future__ import annotations

import os
import re
import time

import bpy

from satk.core.errors import SatkError

from . import util as U

__all__ = ["bake_method", "parse_size"]


def parse_size(v, method: str = "kit.bake") -> tuple[int, int]:
    """``(w, h)`` of ``256``, ``[256, 128]`` or ``"256x128"`` (each side 16-2048)."""
    try:
        if v is None:
            w = h = 256
        elif isinstance(v, bool):
            raise ValueError
        elif isinstance(v, (int, float)):
            w = h = int(v)
        else:
            parts = [int(float(x)) for x in (re.split(r"[x,\s]+", v.strip().lower()) if isinstance(v, str) else v)
                     if str(x) != ""]
            w, h = (parts[0], parts[0]) if len(parts) == 1 else (parts[0], parts[1])
            if len(parts) > 2:
                raise ValueError
    except (ValueError, TypeError, IndexError):
        raise U.bad(method, f"'size' must be a number, [w, h] or 'WxH', got {v!r}") from None
    if not (16 <= w <= 2048 and 16 <= h <= 2048):
        raise U.bad(method, f"'size' sides must be 16..2048, got {w}x{h}")
    return w, h


def _bake_material(kind: str, img, radius: float, gain: float):
    m = bpy.data.materials.new(f"satk_bake_{kind}")
    nt = m.node_tree if m.node_tree else None
    if nt is None:
        m.use_nodes = True
        nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    nt.nodes.active = tex
    if kind == "edge":
        bevel = nt.nodes.new("ShaderNodeBevel")
        bevel.inputs["Radius"].default_value = radius
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        dot = nt.nodes.new("ShaderNodeVectorMath")
        dot.operation = "DOT_PRODUCT"
        inv = nt.nodes.new("ShaderNodeMath")
        inv.operation = "SUBTRACT"
        inv.inputs[0].default_value = 1.0
        mul = nt.nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        mul.use_clamp = True
        mul.inputs[1].default_value = gain
        em = nt.nodes.new("ShaderNodeEmission")
        nt.links.new(bevel.outputs["Normal"], dot.inputs[0])
        nt.links.new(geo.outputs["Normal"], dot.inputs[1])
        nt.links.new(dot.outputs["Value"], inv.inputs[1])
        nt.links.new(inv.outputs["Value"], mul.inputs[0])
        nt.links.new(mul.outputs["Value"], em.inputs["Color"])
        nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    else:
        bsdf = nt.nodes.new("ShaderNodeBsdfDiffuse")
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m


def _save(img, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()


def bake_method(ctx, p: dict) -> dict:
    """Cycles bake of ambient occlusion and a Bevel-node edge mask into PNGs (object UVs; size = N, [w, h] or 'WxH' px)."""
    o = U.obj(U.need(p, "object", "kit.bake"), "kit.bake")
    if o.type != "MESH" or not len(o.data.polygons):
        raise SatkError("BAD_PARAMS", f"kit.bake: {o.name} is not a mesh with faces")
    if not o.data.uv_layers:
        raise SatkError("BAD_PARAMS", f"kit.bake: {o.name} has no UVs", hint="unwrap it (uv.* or kit.uv_region) first")
    w, h = parse_size(p.get("size", 256))
    samples = int(U.num(p, "samples", 16, "kit.bake", 1, 1024))
    radius = U.num(p, "radius", 0.03, "kit.bake", 0.001, 1.0)
    gain = U.num(p, "gain", 6.0, "kit.bake", 0.1, 100.0)
    kinds = [k for k in ("ao", "edge") if p.get(k, True)]
    if not kinds:
        raise U.bad("kit.bake", "nothing to bake (ao and edge are false)")
    scene = ctx.scene
    old_engine = scene.render.engine
    old_mats = [s.material for s in o.material_slots]
    added_slot = not o.material_slots
    if added_slot:
        o.data.materials.append(None)
    out_dir = os.path.join(ctx.out_dir, "bake")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in o.name)[:40]
    files: dict = {}
    t0 = time.perf_counter()
    made = []
    try:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = samples
        scene.cycles.device = "CPU"
        bk = scene.render.bake
        bk.use_selected_to_active = False
        bk.target = "IMAGE_TEXTURES"
        bk.margin = 4
        bpy.context.view_layer.update()   # an object made by the previous call is not in the view layer yet
        for x in bpy.context.view_layer.objects:
            x.select_set(False)
        o.hide_set(False)
        o.select_set(True)
        bpy.context.view_layer.objects.active = o
        for kind in kinds:
            img = bpy.data.images.new(f"satk_bake_{safe}_{kind}", w, h, alpha=False)
            made.append(img)
            mat = _bake_material(kind, img, radius, gain)
            made.append(mat)
            for s in o.material_slots:
                s.material = mat
            bpy.ops.object.bake(type="AO" if kind == "ao" else "EMIT")
            path = os.path.join(out_dir, f"{safe}_{kind}.png")
            _save(img, path)
            files[kind] = U.fwd(path)
    finally:
        for s, m in zip(o.material_slots, old_mats):
            s.material = m
        if added_slot:
            o.data.materials.pop()
        scene.render.engine = old_engine
        for x in made:
            if isinstance(x, bpy.types.Image):
                bpy.data.images.remove(x)
            else:
                bpy.data.materials.remove(x)
    return {"object": o.name, "size": [w, h], "files": files, "s": round(time.perf_counter() - t0, 2)}
