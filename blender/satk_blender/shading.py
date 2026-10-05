# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Game shading: what the SA building pipeline shows, approximated with shader nodes.

* Buildings (meshes with prelight): node group ``SATK_SA_Building`` = texture x material colour x
  lerp(day prelight, night prelight, balance), unlit (emission), alpha from texture x material.
  ``balance`` is one Value node inside the group (shared by all materials), set from the game time
  with the engine curve (``satk.blender.contract.day_night_balance``: 1 before 06:00 -> 0 at 07:00,
  0 until 20:00 -> 1 at 21:00).
* Vehicles: the paint slots (material colours 60,255,0 / 255,0,175 / 0,255,255 / 255,0,255) get the
  first ``carcols.dat`` combination; base colour = texture x colour (lit, Principled BSDF).
* DragonFF's Principled BSDF stays in every material (its exporter reads it); toggling the game
  shading only relinks the Material Output.
"""

from __future__ import annotations

import bpy

GROUP = "SATK_SA_Building"
DAY_ATTR = "satk_day"
NIGHT_ATTR = "satk_night"
SLOT_COLORS = ((60, 255, 0), (255, 0, 175), (0, 255, 255), (255, 0, 255))


# --------------------------------------------------------------------------- node helpers


def _vmath(nt, op, a=None, b=None, loc=(0, 0)):
    n = nt.nodes.new("ShaderNodeVectorMath")
    n.operation = op
    n.location = loc
    if a is not None:
        nt.links.new(a, n.inputs[0])
    if b is not None:
        nt.links.new(b, n.inputs[1])
    return n


def _lerp(nt, a, b, t, loc=(0, 0)):
    """a + (b - a) * t for colours (as vectors)."""
    sub = _vmath(nt, "SUBTRACT", b, a, (loc[0], loc[1]))
    sc = nt.nodes.new("ShaderNodeVectorMath")
    sc.operation = "SCALE"
    sc.location = (loc[0] + 180, loc[1])
    nt.links.new(sub.outputs[0], sc.inputs[0])
    nt.links.new(t, sc.inputs["Scale"])
    add = _vmath(nt, "ADD", a, sc.outputs[0], (loc[0] + 360, loc[1]))
    return add.outputs[0]


def _math(nt, op, a, b=None, loc=(0, 0), val=None):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    n.location = loc
    nt.links.new(a, n.inputs[0])
    if b is not None:
        nt.links.new(b, n.inputs[1])
    elif val is not None:
        n.inputs[1].default_value = val
    return n.outputs[0]


def building_group():
    """The shared node group (created on first use)."""
    ng = bpy.data.node_groups.get(GROUP)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(GROUP, "ShaderNodeTree")
    iface = ng.interface
    for name, typ, default in (("Texture", "NodeSocketColor", (1, 1, 1, 1)), ("Texture Alpha", "NodeSocketFloat", 1.0),
                               ("Material", "NodeSocketColor", (1, 1, 1, 1)), ("Material Alpha", "NodeSocketFloat", 1.0),
                               ("Has Prelit", "NodeSocketFloat", 1.0), ("Has Night", "NodeSocketFloat", 1.0)):
        s = iface.new_socket(name, in_out="INPUT", socket_type=typ)
        s.default_value = default
    iface.new_socket("Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    iface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    nt = ng
    gi = nt.nodes.new("NodeGroupInput")
    gi.location = (-900, 0)
    go = nt.nodes.new("NodeGroupOutput")
    go.location = (900, 0)
    bal = nt.nodes.new("ShaderNodeValue")
    bal.name = bal.label = "balance"
    bal.outputs[0].default_value = 0.0
    bal.location = (-900, -400)
    day = nt.nodes.new("ShaderNodeAttribute")
    day.attribute_name = DAY_ATTR
    day.location = (-700, 300)
    night = nt.nodes.new("ShaderNodeAttribute")
    night.attribute_name = NIGHT_ATTR
    night.location = (-700, 100)
    t = _math(nt, "MULTIPLY", bal.outputs[0], gi.outputs["Has Night"], (-600, -300))
    prelit = _lerp(nt, day.outputs["Color"], night.outputs["Color"], t, (-450, 200))
    white = nt.nodes.new("ShaderNodeRGB")
    white.outputs[0].default_value = (1, 1, 1, 1)
    white.location = (-450, 450)
    light = _lerp(nt, white.outputs[0], prelit, gi.outputs["Has Prelit"], (0, 300))
    texmat = _vmath(nt, "MULTIPLY", gi.outputs["Texture"], gi.outputs["Material"], (-300, -100))
    col = _vmath(nt, "MULTIPLY", texmat.outputs[0], light, (550, 100))
    alpha = _math(nt, "MULTIPLY", gi.outputs["Texture Alpha"], gi.outputs["Material Alpha"], (300, -300))
    em = nt.nodes.new("ShaderNodeEmission")
    em.location = (700, 150)
    nt.links.new(col.outputs[0], em.inputs["Color"])
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    tr.location = (700, -50)
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (800, 0)
    nt.links.new(alpha, mix.inputs[0])
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(em.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], go.inputs["Shader"])
    nt.links.new(col.outputs[0], go.inputs["Color"])
    return ng


def set_time(balance: float) -> None:
    ng = building_group()
    ng.nodes["balance"].outputs[0].default_value = float(balance)


def _nodes(mat):
    nt = mat.node_tree
    img = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None)
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None) or \
        next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    return nt, img, bsdf, out


# --------------------------------------------------------------------------- meshes and materials


def prepare_prelit(mesh) -> tuple[bool, bool]:
    """Name the colour attributes ``satk_day``/``satk_night`` (DragonFF creates day first, then night)."""
    attrs = list(mesh.color_attributes)
    if not attrs:
        return False, False
    if attrs[0].name != DAY_ATTR and DAY_ATTR not in mesh.color_attributes:
        attrs[0].name = DAY_ATTR
    if len(attrs) > 1 and attrs[1].name != NIGHT_ATTR and NIGHT_ATTR not in mesh.color_attributes:
        attrs[1].name = NIGHT_ATTR
    return True, len(attrs) > 1


def building_material(mat, has_prelit: bool, has_night: bool) -> bool:
    """Add the game-shading group to a DragonFF material; returns False if it has no node tree."""
    if mat is None or not mat.use_nodes or mat.get("satk_shading"):
        return False
    nt, img, _bsdf, out = _nodes(mat)
    if out is None:
        return False
    g = nt.nodes.new("ShaderNodeGroup")
    g.node_tree = building_group()
    g.name = g.label = "SATK game"
    g.location = (out.location[0] - 250, out.location[1] - 250)
    if img is not None:
        nt.links.new(img.outputs["Color"], g.inputs["Texture"])
        nt.links.new(img.outputs["Alpha"], g.inputs["Texture Alpha"])
        nt.nodes.active = img
    c = mat.diffuse_color
    g.inputs["Material"].default_value = (c[0], c[1], c[2], 1.0)
    g.inputs["Material Alpha"].default_value = c[3]
    g.inputs["Has Prelit"].default_value = 1.0 if has_prelit else 0.0
    g.inputs["Has Night"].default_value = 1.0 if has_night else 0.0
    nt.links.new(g.outputs["Shader"], out.inputs["Surface"])
    mat["satk_shading"] = "game"
    return True


def _slot(c) -> int | None:
    rgb = tuple(int(round(v * 255)) for v in c[:3])
    for i, s in enumerate(SLOT_COLORS):
        if all(abs(a - b) <= 1 for a, b in zip(rgb, s)):
            return i
    return None


def _srgb_to_linear(v: float) -> float:
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def vehicle_material(mat, colors: list | None) -> int | None:
    """Texture x (paint) colour into the Principled base colour; returns the paint slot or None."""
    if mat is None or not mat.use_nodes or mat.get("satk_shading"):
        return None
    nt, img, bsdf, _out = _nodes(mat)
    if bsdf is None:
        return None
    c = mat.diffuse_color
    slot = _slot(c)
    rgb = (c[0], c[1], c[2])
    if slot is not None and colors and slot < len(colors):
        # only the node colour changes: DragonFF exports the Principled/diffuse colour (the slot marker)
        rgb = tuple(_srgb_to_linear(v / 255.0) for v in colors[slot])
    col = nt.nodes.new("ShaderNodeRGB")
    col.outputs[0].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    col.location = (bsdf.location[0] - 450, bsdf.location[1] - 300)
    col.label = "SATK paint" if slot is not None else "SATK colour"
    if img is not None:
        mul = _vmath(nt, "MULTIPLY", img.outputs["Color"], col.outputs[0], (bsdf.location[0] - 250, bsdf.location[1] - 200))
        nt.links.new(mul.outputs[0], bsdf.inputs["Base Color"])
        nt.nodes.active = img
    else:
        nt.links.new(col.outputs[0], bsdf.inputs["Base Color"])
    mat["satk_shading"] = "vehicle"
    if slot is not None:
        mat["satk_paint_slot"] = slot + 1
    return slot


def restore_for_export(materials) -> int:
    """Undo the vehicle relinking (texture -> Base Color) so DragonFF's exporter finds the texture."""
    n = 0
    for mat in materials:
        if mat is None or mat.get("satk_shading") != "vehicle" or not mat.node_tree:
            continue
        nt, img, bsdf, _out = _nodes(mat)
        if img is not None and bsdf is not None:
            nt.links.new(img.outputs["Color"], bsdf.inputs["Base Color"])
            n += 1
    return n


def set_game_shading(enabled: bool, materials=None) -> int:
    """Switch materials between the game group and DragonFF's Principled BSDF (relink only)."""
    n = 0
    for mat in materials or bpy.data.materials:
        if mat.get("satk_shading") != "game" or not mat.node_tree:
            continue
        nt, _img, bsdf, out = _nodes(mat)
        g = nt.nodes.get("SATK game")
        src = g.outputs["Shader"] if enabled and g else (bsdf.outputs[0] if bsdf else None)
        if src is not None and out is not None:
            nt.links.new(src, out.inputs["Surface"])
            n += 1
    return n


def apply(objs, *, balance: float = 0.0, vehicle_colors=None, kind: str = "building") -> dict:
    """Game shading for the meshes of ``objs``. ``kind``: building | vehicle | ped (ped = untouched)."""
    stats = {"materials": 0, "prelit": 0, "paint": 0}
    seen_mesh = set()
    seen_mat = set()
    for o in objs:
        if o.type != "MESH" or o.data is None:
            continue
        me = o.data
        if me.name in seen_mesh:
            continue
        seen_mesh.add(me.name)
        if kind == "building":
            has_prelit, has_night = prepare_prelit(me)
            stats["prelit"] += int(has_prelit)
        for m in me.materials:
            if m is None or m.name in seen_mat:
                continue
            seen_mat.add(m.name)
            if kind == "building":
                stats["materials"] += int(building_material(m, has_prelit, has_night))
            elif kind == "vehicle":
                s = vehicle_material(m, vehicle_colors)
                stats["materials"] += 1
                stats["paint"] += int(s is not None)
    if kind == "building":
        set_time(balance)
    return stats
