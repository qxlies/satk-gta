# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Material presets (``data/kit/materials.json``) as export-ready Blender materials.

A preset material is what DragonFF writes back: Principled Base Color ``default_value`` = the DFF colour
(byte / 255, alpha = material alpha), an image node linked to Base Color whose image name (and node label)
is the texture name, ``mat.dff`` surface properties, MatFX env map, specular and reflection plug-ins.
Shared textures (``vehicle.txd`` atlases) get the vanilla pixels when the plan carries their PNG, else a
small grey placeholder; own textures are blank images of the preset size that the agent paints or bakes.
"""

from __future__ import annotations

import os

import bpy

__all__ = ["make", "ensure", "set_image", "apply_preset"]

_PLACEHOLDER = 8


def _principled(mat):
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None) or nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None) or nt.nodes.new("ShaderNodeBsdfPrincipled")
    if not out.inputs["Surface"].is_linked:
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return bsdf


def _image(name: str, size, png: str | None, shared: bool, alpha: bool):
    img = bpy.data.images.get(name)
    if img is not None:
        return img
    if png and os.path.isfile(png):
        img = bpy.data.images.load(png, check_existing=False)
        img.name = name
        img.pack()
    else:
        w, h = (int(size[0]), int(size[1])) if size else (_PLACEHOLDER, _PLACEHOLDER)
        if shared:
            w = h = _PLACEHOLDER
        img = bpy.data.images.new(name, w, h, alpha=alpha)
        grey = 0.35 if not shared else 0.6
        img.generated_color = (grey, grey, grey, 1.0)
    img["satk_shared"] = bool(shared)
    img["satk_texture"] = name
    return img


def set_image(mat, img) -> None:
    """Link ``img`` to the Base Color of ``mat`` (one image node labelled with the texture name)."""
    nt = mat.node_tree
    bsdf = _principled(mat)
    node = next((n for n in nt.nodes if n.type == "TEX_IMAGE" and n.get("satk_base")), None)
    if node is None:
        node = nt.nodes.new("ShaderNodeTexImage")
        node["satk_base"] = True
        node.location = (bsdf.location.x - 320, bsdf.location.y)
    node.image = img
    node.label = str(img.get("satk_texture") or img.name)
    nt.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
    mat["satk_texture"] = node.label


def apply_preset(mat, preset: dict, textures: dict | None = None) -> None:
    """Set colour, texture and DragonFF plug-in values of ``mat`` from ``preset``."""
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        try:
            mat.use_nodes = True
        except AttributeError:  # Blender 5.x: always node-based
            pass
    bsdf = _principled(mat)
    r, g, b, a = (float(x) / 255.0 for x in preset.get("rgba") or (255, 255, 255, 255))
    bsdf.inputs["Base Color"].default_value = (r, g, b, a)
    if "Alpha" in bsdf.inputs:
        bsdf.inputs["Alpha"].default_value = a
    mat.diffuse_color = (r, g, b, a)
    if a < 1.0 or preset.get("alpha"):
        for attr, val in (("blend_method", "BLEND"), ("surface_render_method", "BLENDED")):
            try:
                setattr(mat, attr, val)
            except (AttributeError, TypeError):
                pass
    tex = preset.get("texture")
    if tex:
        png = (textures or {}).get(str(tex).lower())
        img = _image(str(tex).lower(), preset.get("size"), png, bool(preset.get("shared")), bool(preset.get("alpha")))
        set_image(mat, img)
    d = mat.dff
    amb, spec, dif = (preset.get("surface") or (1.0, 0.0, 1.0))
    d.ambient, d.specular, d.diffuse = float(amb), float(spec), float(dif)
    env = preset.get("env")
    d.export_env_map = bool(env)
    if env:
        d.env_map_tex = str(env.get("texture", "xvehicleenv128"))
        d.env_map_coef = float(env.get("coef", 1.0))
    sp = preset.get("specular")
    d.export_specular = bool(sp)
    if sp:
        d.specular_level = float(sp.get("level", 0.2))
        d.specular_texture = str(sp.get("texture", "vehiclespecdot64"))
    rf = preset.get("reflection")
    d.export_reflection = bool(rf)
    if rf:
        d.reflection_intensity = float(rf.get("intensity", 0.08))
        d.reflection_scale_x = d.reflection_scale_y = 1.0
        d.reflection_offset_x = d.reflection_offset_y = 1.0
    mat["satk_role"] = preset.get("role", "")
    mat["satk_shared"] = bool(preset.get("shared"))
    if preset.get("atlas"):
        mat["satk_atlas"] = str(preset["atlas"])


def make(name: str, preset: dict, textures: dict | None = None):
    mat = bpy.data.materials.new(name)
    apply_preset(mat, preset, textures)
    return mat


def ensure(model: str, preset: dict, textures: dict | None = None):
    """The model's material of ``preset['role']`` (``<model>.<role>``), created once."""
    name = f"{model}.{preset.get('role', 'mat')}"
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = make(name, preset, textures)
        mat["satk_model"] = model
    return mat
