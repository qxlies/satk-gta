# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Time of day, sky, ground and the frame grade of the game look.

* :func:`env` - the timecyc light of a game time (``satk.look.gamelook.env_at``: the profile's
  ``timecyc.dat`` from the index, else built-in vanilla EXTRASUNNY_LA);
* :data:`LIGHT_DIR` - one fixed directional light for every preview (world space, towards the light), so
  a lineup of models gets identical light;
* :func:`world` - the sky colour, chosen *before* the colour filter so the graded frame shows a light day
  sky or a dark night sky;
* :func:`ground` - a ground plane under the models and one soft contact shadow per footprint;
* :func:`grade_image` - the colour filter (``CPostEffects::ColourFilter``) and display gamma, applied to the
  rendered pixels with numpy (the engine applies them to the whole frame).
"""

from __future__ import annotations

import bpy

from satk.look import gamelook as G

__all__ = ["LIGHT_DIR", "env", "sky_target", "world", "ground", "ground_rgb", "remove_ground", "grade_image",
           "GROUND", "SHADOW"]

#: Direction towards the preview light: front-right, above (a vehicle faces +Y); shared with the soft renderer.
LIGHT_DIR = G.LIGHT_DIR
WORLD = "SATK_look_world"
GROUND = "SATK_look_ground"
SHADOW = "SATK_look_shadow"
#: Ground colour of the graded frame (display space; grey asphalt) by day, and the contact shadow
#: strength (share of light removed).
GROUND_RGB = (0.47, 0.465, 0.45)
SHADOW_ALPHA = 0.62
#: Sky colours of the *graded* frame (display space): day and night.
SKY_DAY = (0.74, 0.78, 0.82)
SKY_NIGHT = (0.10, 0.11, 0.16)


def env(time="12:00", weather: str = G.DEFAULT_WEATHER, profile: str = "vanilla") -> dict:
    """Timecyc light of ``time`` (see :func:`satk.look.gamelook.env_at`); built-in table on any failure."""
    try:
        return G.env_at(time, weather, profile)
    except ValueError:
        raise
    except Exception:  # noqa: BLE001 - no index inside this Blender: the built-in table
        return G.default_env(time)


def sky_target(e: dict) -> tuple[float, float, float]:
    b = float(e.get("balance") or 0.0)
    return tuple(d + (n - d) * b for d, n in zip(SKY_DAY, SKY_NIGHT))  # type: ignore[return-value]


def ground_rgb(e: dict | None) -> tuple[float, float, float]:
    """Material colour of the ground: :data:`GROUND_RGB` divided by its light (top face) and the colour filter,
    so the ground stays a neutral grey whatever the time (darker at night with the light)."""
    if not e:
        return GROUND_RGB
    f = G.grade_factors(e)
    k = G.lit_mult(0.0)  # the day light: at night the group's lower multiplier darkens the ground
    out = []
    for i in range(3):
        light = (float(e["amb_obj"][i]) + float(e["dir"][i]) * LIGHT_DIR[2]) * k
        out.append(min(1.0, GROUND_RGB[i] / max(light * f[i], 1e-3)))
    return tuple(out)  # type: ignore[return-value]


def world(scene, e: dict, *, grade: bool = True):
    """World colour = the sky target divided by the colour filter (the grade brings it back)."""
    f = G.grade_factors(e) if grade else (1.0, 1.0, 1.0)
    rgb = tuple(min(1.0, max(0.0, t / max(k, 1e-3))) for t, k in zip(sky_target(e), f))
    w = bpy.data.worlds.get(WORLD) or bpy.data.worlds.new(WORLD)
    w.color = rgb
    try:
        w.use_nodes = True  # deprecated in 5.x (always on); older builds need it for the background node
    except (AttributeError, TypeError):
        pass
    nt = w.node_tree
    if nt is not None:
        bg = next((n for n in nt.nodes if n.type == "BACKGROUND"), None)
        if bg is None:
            bg = nt.nodes.new("ShaderNodeBackground")
            out = next((n for n in nt.nodes if n.type == "OUTPUT_WORLD"), None) or nt.nodes.new("ShaderNodeOutputWorld")
            nt.links.new(bg.outputs[0], out.inputs["Surface"])
        bg.inputs[0].default_value = (*rgb, 1.0)
        bg.inputs[1].default_value = 1.0
    scene.world = w
    return w


# --------------------------------------------------------------------------- ground and contact shadows


def _shadow_material():
    m = bpy.data.materials.get(SHADOW)
    if m is not None:
        return m
    m = bpy.data.materials.new(SHADOW)
    try:
        m.use_nodes = True
    except (AttributeError, TypeError):
        pass
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])
    # rounded-box falloff in the plane's 0..1 coordinates: 1 inside the inner 60 %, 0 at the border
    def edge(sock):
        a = nt.nodes.new("ShaderNodeMath")
        a.operation = "SUBTRACT"
        nt.links.new(sock, a.inputs[0])
        a.inputs[1].default_value = 0.5
        b = nt.nodes.new("ShaderNodeMath")
        b.operation = "ABSOLUTE"
        nt.links.new(a.outputs[0], b.inputs[0])
        r = nt.nodes.new("ShaderNodeMapRange")
        r.inputs["From Min"].default_value = 0.30
        r.inputs["From Max"].default_value = 0.5
        r.inputs["To Min"].default_value = 1.0
        r.inputs["To Max"].default_value = 0.0
        r.interpolation_type = "SMOOTHSTEP"
        nt.links.new(b.outputs[0], r.inputs["Value"])
        return r.outputs[0]

    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    nt.links.new(edge(sep.outputs["X"]), mul.inputs[0])
    nt.links.new(edge(sep.outputs["Y"]), mul.inputs[1])
    k = nt.nodes.new("ShaderNodeMath")
    k.operation = "MULTIPLY"
    k.inputs[1].default_value = SHADOW_ALPHA
    nt.links.new(mul.outputs[0], k.inputs[0])
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(k.outputs[0], mix.inputs[0])
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(em.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    _blended(m)
    return m


def _blended(m) -> None:
    for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
        try:
            setattr(m, attr, val)
            return
        except (AttributeError, TypeError):
            continue


def _plane(name: str, size_x: float, size_y: float, loc, mat, coll):
    me = bpy.data.meshes.new(name)
    hx, hy = size_x / 2, size_y / 2
    me.from_pydata([(-hx, -hy, 0.0), (hx, -hy, 0.0), (hx, hy, 0.0), (-hx, hy, 0.0)], [], [(0, 1, 2, 3)])
    me.materials.append(mat)
    o = bpy.data.objects.new(name, me)
    coll.objects.link(o)
    o.location = loc
    o["satk_look_aux"] = 1
    o["satk_ghost"] = 1  # never counted, never exported
    return o


def ground(scene, footprints: list, ground_mat, *, shadows: bool = True) -> list:
    """A ground plane under ``footprints`` (``[(xmin, ymin, xmax, ymax, zmin)]``) plus contact shadows.

    Returns the created objects (tagged ``satk_look_aux``; :func:`remove_ground` deletes them).
    """
    remove_ground()
    if not footprints:
        return []
    coll = scene.collection
    x0 = min(f[0] for f in footprints)
    y0 = min(f[1] for f in footprints)
    x1 = max(f[2] for f in footprints)
    y1 = max(f[3] for f in footprints)
    z = min(f[4] for f in footprints)
    span = max(x1 - x0, y1 - y0, 1.0)
    out = [_plane(GROUND, span * 6 + 20, span * 6 + 20, ((x0 + x1) / 2, (y0 + y1) / 2, z - 0.002), ground_mat, coll)]
    if shadows:
        sm = _shadow_material()
        for i, (a, b, c, d, zf) in enumerate(footprints):
            w, h = (c - a), (d - b)
            out.append(_plane(f"{SHADOW}.{i}", w * 1.35 + 0.3, h * 1.25 + 0.3, ((a + c) / 2, (b + d) / 2, zf + 0.004),
                              sm, coll))
    return out


def remove_ground() -> int:
    objs = [o for o in bpy.data.objects if o.get("satk_look_aux")]
    meshes = [o.data for o in objs if o.type == "MESH"]
    if objs:
        bpy.data.batch_remove(objs)
    dead = [m for m in meshes if m.users == 0]
    if dead:
        bpy.data.batch_remove(dead)
    return len(objs)


# --------------------------------------------------------------------------- grade


def grade_image(pixels, e: dict | None, *, grade: bool = True):
    """Colour filter + display gamma on float RGB(A) pixels (``numpy`` array, rows any order).

    ``out = curve(clip(S * factors))``; alpha is left alone. ``e=None`` or ``grade=False`` leaves the
    colours as they are.
    """
    import numpy as np

    if e is None or not grade:
        return pixels
    f = np.array(G.grade_factors(e), dtype=np.float32)
    rgb = np.clip(pixels[..., :3] * f, 0.0, 1.0)
    rgb = np.minimum(1.0, np.power((rgb * 255.0 + 1.0) / 256.0, G.DISPLAY_GAMMA))
    out = pixels.copy()
    out[..., :3] = rgb
    return out
