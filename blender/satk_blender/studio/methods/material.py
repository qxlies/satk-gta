# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``material.*``: GTA-style materials - an RGBA colour (0-255, the DFF material colour), an optional image
texture (its name becomes the DFF texture name), assignment to objects or to a face selection.

``material.create`` with ``preset`` hands over to the kit's ``kit.material_preset`` (paint keys, lamps,
glass, chrome, ...) when the kit plug-in is loaded. DragonFF exports the Principled *Base Color* value
times 255, so colours are stored unconverted.
"""

from __future__ import annotations

import os

import bpy

from satk.core.errors import SatkError
from satk.studio.core import readonly

from . import _util as U


def _rgba(v, method: str) -> list[float]:
    """``#RRGGBB[AA]`` or [r, g, b(, a)] in 0-255 -> four floats 0-1."""
    if isinstance(v, str):
        s = v.lstrip("#")
        if len(s) not in (6, 8) or any(c not in "0123456789abcdefABCDEF" for c in s):
            raise U.bad(method, f"colour {v!r} is not #RRGGBB or #RRGGBBAA")
        vals = [int(s[i:i + 2], 16) for i in range(0, len(s), 2)]
    elif isinstance(v, (list, tuple)) and len(v) in (3, 4) and all(U.is_num(x) and 0 <= x <= 255 for x in v):
        vals = [float(x) for x in v]
    else:
        raise U.bad(method, f"colour must be #RRGGBB or [r, g, b(, a)] in 0-255, got {v!r}")
    if len(vals) == 3:
        vals.append(255)
    return [x / 255.0 for x in vals]


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _bsdf(mat):
    nt = mat.node_tree
    for n in nt.nodes:
        if n.type == "BSDF_PRINCIPLED":
            return n
    raise SatkError("BAD_PARAMS", f"material {mat.name!r} has no Principled BSDF node")


def _new_material(name: str):
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:  # Blender < 5: node trees are opt-in
        mat.use_nodes = True
    return mat


def _set_color(mat, rgba: list[float]) -> None:
    b = _bsdf(mat)
    b.inputs["Base Color"].default_value = rgba
    b.inputs["Alpha"].default_value = rgba[3]
    mat.diffuse_color = [_srgb_to_linear(c) for c in rgba[:3]] + [rgba[3]]
    if rgba[3] < 0.999:
        for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
            if hasattr(mat, attr):
                try:
                    setattr(mat, attr, val)
                except (TypeError, ValueError):
                    pass


def _set_texture(mat, path: str, method: str) -> str:
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        raise SatkError("NOT_FOUND", f"{method}: no image {p.replace(os.sep, '/')}")
    img = bpy.data.images.load(p, check_existing=True)
    stem = os.path.splitext(os.path.basename(p))[0]
    if img.name != stem and bpy.data.images.get(stem) is None:
        img.name = stem
    nt = mat.node_tree
    tex = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None) or nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.label = stem
    nt.links.new(tex.outputs["Color"], _bsdf(mat).inputs["Base Color"])
    nt.nodes.active = tex
    return img.name


def create(ctx, p: dict) -> dict:
    """Create (or update) a material: name, color (#RRGGBB or 0-255 list), alpha 0-255, texture (image path), preset."""
    M = "material.create"
    name = U.text(p, "name", M, required=True)
    if p.get("preset"):
        if "kit.material_preset" not in (getattr(ctx, "methods", None) or {}):
            raise SatkError("UNSUPPORTED", f"{M}: presets come from the kit plug-in, which is not loaded",
                            hint="create the material with color/texture, or update satk (kit.material_preset)")
        out = ctx.call("kit.material_preset", {k: v for k, v in p.items() if k != "preset"} | {"role": p["preset"]})
        out.setdefault("material", name)
        return out
    mat = bpy.data.materials.get(name) or _new_material(name)
    rgba = _rgba(p.get("color", [200, 200, 200]), M)
    if p.get("alpha") is not None:
        rgba[3] = U.num(p, "alpha", M, lo=0, hi=255) / 255.0
    _set_color(mat, rgba)
    out: dict = {"material": mat.name, "color": [round(c * 255) for c in rgba]}
    if p.get("texture"):
        out["texture"] = _set_texture(mat, str(p["texture"]), M)
    if p.get("objects") or p.get("object"):
        out.update(assign(ctx, {k: p[k] for k in ("object", "objects", "select") if k in p} | {"material": mat.name}))
    return out


def assign(ctx, p: dict) -> dict:
    """Give objects a material (all faces), or only the faces chosen by 'select' (a new slot when needed)."""
    M = "material.assign"
    mat = bpy.data.materials.get(U.text(p, "material", M, required=True))
    if mat is None:
        raise SatkError("NOT_FOUND", f"{M}: no material {p['material']!r}",
                        data={"materials": [m.name for m in bpy.data.materials][:20]})
    objs = U.resolve(ctx, p, M, types=("MESH", "CURVE"))
    sel = p.get("select")
    total = 0
    for o in objs:
        slots = [s.material for s in o.material_slots]
        if sel is None:
            o.data.materials.clear()
            o.data.materials.append(mat)
            for poly in getattr(o.data, "polygons", []):
                poly.material_index = 0
            total += len(getattr(o.data, "polygons", []))
            continue
        if o.type != "MESH":
            raise U.bad(M, "'select' works on meshes")
        if mat in slots:
            idx = slots.index(mat)
        else:
            if not slots:
                o.data.materials.append(None)
            o.data.materials.append(mat)
            idx = len(o.data.materials) - 1
        with U.edit_mesh(o) as bm:
            faces = U.require_faces(U.select_faces(o, bm, sel, M), M, sel)
            for f in faces:
                f.material_index = idx
            total += len(faces)
    return {"material": mat.name, "faces": total, "changed": [o.name for o in objs]}


def set_(ctx, p: dict) -> dict:
    """Change a material's color, alpha (0-255) or texture (image path)."""
    M = "material.set"
    mat = bpy.data.materials.get(U.text(p, "name", M, required=True))
    if mat is None:
        raise SatkError("NOT_FOUND", f"{M}: no material {p['name']!r}")
    b = _bsdf(mat)
    rgba = list(b.inputs["Base Color"].default_value)
    if p.get("color") is not None:
        rgba = _rgba(p["color"], M)[:3] + [rgba[3]]
    if p.get("alpha") is not None:
        rgba[3] = U.num(p, "alpha", M, lo=0, hi=255) / 255.0
    _set_color(mat, rgba)
    out: dict = {"material": mat.name, "color": [round(c * 255) for c in rgba]}
    if p.get("texture"):
        out["texture"] = _set_texture(mat, str(p["texture"]), M)
    users = [o.name for o in bpy.data.objects if any(s.material is mat for s in getattr(o, "material_slots", []))]
    out["changed"] = users[:50]
    return out


@readonly
def list_(ctx, p: dict) -> dict:
    """Materials of the scene (or of one object): colour 0-255, texture, users."""
    rows = []
    mats = bpy.data.materials
    if p.get("object"):
        o = ctx.obj(p["object"])
        mats = [s.material for s in o.material_slots if s.material]
    for m in mats:
        row: dict = {"name": m.name}
        try:
            b = _bsdf(m)
            row["color"] = [round(c * 255) for c in b.inputs["Base Color"].default_value]
            tex = next((n for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image), None)
            if tex is not None:
                row["texture"] = tex.image.name
                row["size"] = list(tex.image.size)
        except SatkError:
            pass
        row["users"] = m.users
        rows.append(row)
    return {"materials": rows[:100], "total": len(rows)}


METHODS = {"material.create": create, "material.assign": assign, "material.set": set_, "material.list": list_}
