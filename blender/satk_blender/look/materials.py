# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Materials of the looks.

**Game** (node group ``SATK_look_game`` and its slimmer variants, shared by every material, so EEVEE compiles a
handful of shaders). A material takes the variant that has only the branches it uses (``_opaque`` without the
transparent mix, ``_lit`` without the prelit day/night branch, ``_noenv``, ``_nospec``): EEVEE syncs every
material on every render at a cost that grows with its node graph (~1.7 ms for the full group, ~0.5 ms for
the plain one), which is what decides the speed of a preview in a session.
All maths is in display space, like the D3D9 fixed-function pipeline of the game, and the scene renders
with the ``Raw`` view transform: the images of the looked materials are read as ``Non-Color`` (their bytes
as they are; :func:`display_images`), and the byte vertex colours, which Blender always hands to shaders
decoded to linear, are encoded back with the exact sRGB curve (node group ``SATK_look_srgb``)::

    base  = mix(tex, white, dirt) * material            # dirt: share of white (vehiclegrunge256 only)
    light = prelit ? lerp(day, night, balance) + amb     # buildings (CustomBuildingDNPipeline)
                   : (amb_obj + dir * max(0, N.L)) * LIT_MULT   # vehicles, peds, objects
    col   = unlit ? base : base * light                  # lamps with lights on
    col   = env_lerp ? mix(col, env, f) : col + env * f  # MatFX env (UV2 'x' texture or reflection map)
    col  += spec * LIGHT_SPECULAR * max(0, N.H) ** power
    alpha = tex_alpha * material_alpha                   # clip at 0.5 for cut-outs, blended for glass

The DragonFF Principled BSDF stays in every material (its exporter reads it): :func:`apply_game` only adds
nodes named ``SATK look*`` and relinks the Material Output; :func:`restore` puts everything back.

**Clay** and **wire** are view-layer material overrides (grey, lit by the same light; wire adds the
triangle edges).
"""

from __future__ import annotations

import bpy

from satk.look import gamelook as G

from . import dirt as D
from .lights import LIGHT_DIR

__all__ = ["GROUP", "CLAY", "WIRE", "PREFIX", "game_group", "set_env", "apply_game", "restore", "restore_all",
           "override_material", "classify", "ground_material", "default_material", "display_images",
           "restore_images", "clear_caches"]

GROUP = "SATK_look_game"
SRGB = "SATK_look_srgb"
CLAY = "SATK_look_clay"
WIRE = "SATK_look_wire"
GROUND_MAT = "SATK_look_ground_mat"
PREFIX = "SATK look"
_INPUTS = (
    ("Texture", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)), ("Texture Alpha", "NodeSocketFloat", 1.0),
    ("Has Texture", "NodeSocketFloat", 0.0), ("Material", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0)),
    ("Material Alpha", "NodeSocketFloat", 1.0), ("Dirt", "NodeSocketFloat", 0.0),
    ("Unlit", "NodeSocketFloat", 0.0), ("Prelit", "NodeSocketFloat", 0.0), ("Has Night", "NodeSocketFloat", 0.0),
    ("Env", "NodeSocketColor", (0.0, 0.0, 0.0, 1.0)), ("Env Factor", "NodeSocketFloat", 0.0),
    ("Env Lerp", "NodeSocketFloat", 0.0), ("Spec", "NodeSocketFloat", 0.0), ("Spec Power", "NodeSocketFloat", 20.0),
    ("Clip", "NodeSocketFloat", 0.0),
)


# --------------------------------------------------------------------------- node helpers


class _B:
    """Tiny node-graph builder (``nt`` = a node tree)."""

    def __init__(self, nt):
        self.nt = nt

    def node(self, kind: str, **props):
        n = self.nt.nodes.new(kind)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def link(self, a, b):
        self.nt.links.new(a, b)

    def math(self, op: str, a, b=None, *, clamp: bool = False):
        n = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        self._in(n.inputs[0], a)
        if b is not None:
            self._in(n.inputs[1], b)
        return n.outputs[0]

    def vmath(self, op: str, a, b=None):
        n = self.node("ShaderNodeVectorMath", operation=op)
        self._in(n.inputs[0], a)
        if b is not None:
            if op == "SCALE":
                self._in(n.inputs["Scale"], b)
            else:
                self._in(n.inputs[1], b)
        return n.outputs["Value"] if op in ("DOT_PRODUCT", "LENGTH", "DISTANCE") else n.outputs["Vector"]

    def mix(self, a, b, fac, blend: str = "MIX"):
        """Colour mix ``a`` -> ``b`` by ``fac`` (``ShaderNodeMix`` RGBA)."""
        n = self.node("ShaderNodeMix", data_type="RGBA", blend_type=blend, clamp_result=False)
        self._in(n.inputs[0], fac)
        self._in(n.inputs[6], a)
        self._in(n.inputs[7], b)
        return n.outputs[2]

    def _in(self, sock, v):
        if hasattr(v, "is_output"):
            self.link(v, sock)
        elif isinstance(v, (tuple, list)):
            dv = sock.default_value
            n = len(dv)
            sock.default_value = tuple(v)[:n] + tuple(dv)[len(v):] if len(v) < n else tuple(v)[:n]
        else:
            sock.default_value = v

    def const_vec(self, v):
        n = self.node("ShaderNodeCombineXYZ")
        for i, x in enumerate(v):
            n.inputs[i].default_value = float(x)
        return n.outputs[0]

    def srgb(self, col):
        """Linear -> sRGB-encoded (display) colour, the exact piecewise curve."""
        n = self.node("ShaderNodeGroup")
        n.node_tree = srgb_group()
        self._in(n.inputs[0], col)
        return n.outputs[0]


def srgb_group():
    """``SATK_look_srgb``: linear colour in, sRGB-encoded colour out (per channel)."""
    ng = bpy.data.node_groups.get(SRGB)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(SRGB, "ShaderNodeTree")
    ng.interface.new_socket("Color", in_out="INPUT", socket_type="NodeSocketColor")
    ng.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    b = _B(ng)
    gi, go = b.node("NodeGroupInput"), b.node("NodeGroupOutput")
    sep = b.node("ShaderNodeSeparateColor")
    comb = b.node("ShaderNodeCombineColor")
    b.link(gi.outputs[0], sep.inputs[0])
    for i in range(3):
        x = sep.outputs[i]
        lo = b.math("MULTIPLY", x, 12.92)
        hi = b.math("SUBTRACT", b.math("MULTIPLY", b.math("POWER", b.math("MAXIMUM", x, 0.0), 1.0 / 2.4), 1.055), 0.055)
        t = b.math("GREATER_THAN", x, 0.0031308)
        b.link(b.math("ADD", lo, b.math("MULTIPLY", t, b.math("SUBTRACT", hi, lo))), comb.inputs[i])
    b.link(comb.outputs[0], go.inputs[0])
    return ng


def _nodes_on(m) -> None:
    try:
        m.use_nodes = True  # deprecated in 5.x (always on)
    except (AttributeError, TypeError):
        pass


def _lambert(b: _B):
    geo = b.node("ShaderNodeNewGeometry")
    ndl = b.vmath("DOT_PRODUCT", geo.outputs["Normal"], b.const_vec(LIGHT_DIR))
    return geo, b.math("MAXIMUM", ndl, 0.0)


# --------------------------------------------------------------------------- the game group


def group_name(alpha: bool = True, prelit: bool = True, env: bool = True, spec: bool = True) -> str:
    """Name of the group variant with the given branches (all on = ``SATK_look_game``)."""
    off = ((not alpha, "_opaque"), (not prelit, "_lit"), (not env, "_noenv"), (not spec, "_nospec"))
    return GROUP + "".join(sfx for gone, sfx in off if gone)


def game_group(alpha: bool = True, prelit: bool = True, env: bool = True, spec: bool = True):
    """A shared node group (built once per Blender file and variant). Every variant has the same inputs and
    output; the flags leave out the transparent mix (``alpha``), the day/night vertex-colour light
    (``prelit``), the MatFX env term (``env``) and the specular highlight (``spec``)."""
    name = group_name(alpha, prelit, env, spec)
    ng = bpy.data.node_groups.get(name)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(name, "ShaderNodeTree")
    _build_group(ng, alpha, prelit, env, spec)
    if _ENV is not None:
        _write_env(ng, _ENV)
    return ng


def _build_group(ng, alpha: bool, prelit: bool, env: bool, spec: bool) -> None:
    for sock_name, typ, default in _INPUTS:
        sock = ng.interface.new_socket(sock_name, in_out="INPUT", socket_type=typ)
        sock.default_value = default
    ng.interface.new_socket("Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    b = _B(ng)
    gi = b.node("NodeGroupInput")
    go = b.node("NodeGroupOutput")
    I = gi.outputs  # noqa: E741
    # time-of-day values (set_env)
    vals = {}
    for name, default in (("balance", 0.0), ("lit_mult", G.LIT_MULT)):
        v = b.node("ShaderNodeValue", name=name, label=name)
        v.outputs[0].default_value = default
        vals[name] = v.outputs[0]
    for name in ("amb", "amb_obj", "dir"):
        c = b.node("ShaderNodeRGB", name=name, label=name)
        c.outputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
        vals[name] = c.outputs[0]
    # texture in display space (white without a texture), dirt, material colour
    tex = b.mix((1.0, 1.0, 1.0, 1.0), I["Texture"], I["Has Texture"])  # Non-Color images: display values
    tex = b.mix(tex, (1.0, 1.0, 1.0, 1.0), I["Dirt"])
    base = b.mix(tex, I["Material"], 1.0, blend="MULTIPLY")
    # light of lit models
    geo, ndl = _lambert(b)
    dirl = b.vmath("SCALE", vals["dir"], ndl)
    lit = b.vmath("SCALE", b.vmath("ADD", vals["amb_obj"], dirl), vals["lit_mult"])
    light = lit
    if prelit:  # light of prelit models (day/night vertex colours + ambient)
        day = b.node("ShaderNodeAttribute", attribute_name="satk_day")
        night = b.node("ShaderNodeAttribute", attribute_name="satk_night")
        t = b.math("MULTIPLY", vals["balance"], I["Has Night"])
        pre = b.mix(b.srgb(day.outputs["Color"]), b.srgb(night.outputs["Color"]), t)
        pre = b.vmath("ADD", pre, vals["amb"])
        light = b.mix(lit, pre, I["Prelit"])
    col = b.mix(base, light, 1.0, blend="MULTIPLY")
    col = b.mix(col, base, I["Unlit"])
    if env:  # MatFX env: add (reflection map) or lerp (UV2 'x' texture)
        env_c = I["Env"]
        added = b.vmath("ADD", col, b.vmath("SCALE", env_c, I["Env Factor"]))
        lerped = b.mix(col, env_c, I["Env Factor"])
        col = b.mix(added, lerped, I["Env Lerp"])
    if spec:  # Blinn-Phong highlight of the preview light
        h = b.vmath("NORMALIZE", b.vmath("ADD", b.const_vec(LIGHT_DIR), geo.outputs["Incoming"]))
        ndh = b.math("MAXIMUM", b.vmath("DOT_PRODUCT", geo.outputs["Normal"], h), 0.0)
        hl = b.math("MULTIPLY", b.math("POWER", ndh, I["Spec Power"]), b.math("MULTIPLY", I["Spec"], G.LIGHT_SPECULAR))
        col = b.vmath("ADD", col, b.vmath("SCALE", b.const_vec((1.0, 1.0, 1.0)), hl))
    em = b.node("ShaderNodeEmission")
    b.link(col, em.inputs["Color"])
    em.inputs["Strength"].default_value = 1.0
    if not alpha:
        b.link(em.outputs[0], go.inputs["Shader"])
        return
    # alpha: texture (if any) x material; cut-outs clip at 0.5
    tex_a = b.math("ADD", b.math("MULTIPLY", I["Texture Alpha"], I["Has Texture"]),
                   b.math("SUBTRACT", 1.0, I["Has Texture"]))
    alpha_v = b.math("MULTIPLY", tex_a, I["Material Alpha"])
    clipped = b.math("GREATER_THAN", alpha_v, 0.5)
    a_sel = b.node("ShaderNodeMix", data_type="FLOAT")
    b.link(I["Clip"], a_sel.inputs[0])
    b.link(alpha_v, a_sel.inputs[2])
    b.link(clipped, a_sel.inputs[3])
    tr = b.node("ShaderNodeBsdfTransparent")
    ms = b.node("ShaderNodeMixShader")
    b.link(a_sel.outputs[0], ms.inputs[0])
    b.link(tr.outputs[0], ms.inputs[1])
    b.link(em.outputs[0], ms.inputs[2])
    b.link(ms.outputs[0], go.inputs["Shader"])


_ENV: dict | None = None  # the light of the last set_env (a variant built later starts with it)


def _write_env(ng, e: dict) -> None:
    ng.nodes["balance"].outputs[0].default_value = float(e.get("balance") or 0.0)
    ng.nodes["lit_mult"].outputs[0].default_value = float(G.lit_mult(e.get("balance") or 0.0))
    for k in ("amb", "amb_obj", "dir"):
        c = list(e.get(k) or (1.0, 1.0, 1.0))[:3]
        ng.nodes[k].outputs[0].default_value = (c[0], c[1], c[2], 1.0)


def set_env(e: dict) -> None:
    """Write the timecyc light of ``e`` (``gamelook.env_at``) into every group variant."""
    global _ENV
    _ENV = dict(e)
    game_group()
    for ng in bpy.data.node_groups:
        if ng.name == GROUP or ng.name.startswith(GROUP + "_"):
            _write_env(ng, _ENV)


# --------------------------------------------------------------------------- per material


def _find(nt, typ: str):
    return next((n for n in nt.nodes if n.type == typ and not n.name.startswith(PREFIX)), None)


def _output(nt):
    outs = [n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"]
    return next((n for n in outs if n.is_active_output), None) or (outs[0] if outs else None)


def _image_node(nt):
    return next((n for n in nt.nodes if n.type == "TEX_IMAGE" and n.image is not None
                 and not n.name.startswith(PREFIX)), None)


def _image_by_name(name: str | None):
    if not name:
        return None
    want = str(name).lower()
    for img in bpy.data.images:
        n = img.name.lower()
        if n == want or n.split(".")[0] == want or n.endswith("/" + want + "/0"):
            return img
    return None


#: Kit kinds and blank kinds that are vehicles (a kit collection's ``satk_kit``, a blank's ``satk_blank`` kind).
_VEHICLE_KINDS = ("automobile", "bike", "boat", "heli", "plane", "trailer", "mtruck", "quad", "bmx", "train")


def _kit_kind(o) -> str:
    for c in getattr(o, "users_collection", ()) or ():
        if c.get("satk_kit"):
            return str(c.get("satk_group") or "") if c.get("satk_group") == "vehicle" else str(c.get("satk_kit"))
    blank = o.get("satk_blank")
    if isinstance(blank, str) and '"kind"' in blank:
        try:
            import json

            return str(json.loads(blank).get("kind") or "")
        except ValueError:
            return ""
    return ""


def classify(objs) -> str:
    """``vehicle`` | ``ped`` | ``building`` | ``object`` of a model from its objects' tags and data: ``satk_sec``,
    a kit collection's group or kind, a vehicle blank, armatures, vehicle dummy names, prelight."""
    secs = {str(o.get("satk_sec") or "") for o in objs}
    if "cars" in secs:
        return "vehicle"
    if "peds" in secs or any(o.type == "ARMATURE" for o in objs):
        return "ped"
    kinds = {_kit_kind(o) for o in objs}
    if "vehicle" in kinds or kinds & set(_VEHICLE_KINDS):
        return "vehicle"
    if "ped" in kinds:
        return "ped"
    names = {o.name.split(".")[0].lower() for o in objs}
    if "chassis_dummy" in names or any(n.startswith("wheel_") and n.endswith("_dummy") for n in names):
        return "vehicle"
    for o in objs:
        if o.type == "MESH" and o.data is not None and len(o.data.color_attributes):
            return "building"
    return "object"


def material_rgb(mat) -> tuple:
    """The material colour the DFF carries (0..1 per channel = byte / 255), read the way DragonFF exports it: the
    Principled BSDF's Base Color value when the material has one, else ``diffuse_color``. Studio materials keep a
    linear ``diffuse_color`` for the viewport (paint key 60,255,0 -> 0.045), so that is not the exported colour."""
    nt = mat.node_tree if getattr(mat, "use_nodes", True) else None
    if nt is not None:
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None:
            v = bsdf.inputs["Base Color"].default_value
            return (float(v[0]), float(v[1]), float(v[2]))
    c = mat.diffuse_color
    return (float(c[0]), float(c[1]), float(c[2]))


def _paint(mat, colors) -> tuple | None:
    slot = mat.get("satk_paint_slot")
    if slot is None:
        slot = G.paint_slot([round(v * 255) for v in material_rgb(mat)])
    if slot is None:
        return None
    colors = colors or G.PAINT_DEFAULT
    if int(slot) - 1 >= len(colors):
        return None
    c = colors[int(slot) - 1]
    return tuple(float(v) / 255.0 for v in c[:3])


def _uv2_name(mat) -> str | None:
    for me in bpy.data.meshes:
        if mat.name in [m.name for m in me.materials if m is not None] and len(me.uv_layers) > 1:
            return me.uv_layers[1].name
    return None


def apply_game(mat, *, kind: str, prelit: bool, has_night: bool, dirt: float, lights: str,
               colors=None) -> dict:
    """Wire the game group into ``mat`` (once; a second call only updates the inputs). Returns flags."""
    if mat is None:
        return {}
    _nodes_on(mat)
    nt = mat.node_tree
    if nt is None:
        return {}
    out = _output(nt)
    if out is None:
        return {}
    b = _B(nt)
    img_node = _image_node(nt)
    img_name = img_node.image.name if img_node is not None else ""
    low = img_name.lower()
    is_lamp = G.LIGHTS_TEXTURE in low and kind == "vehicle"
    mat_alpha = float(mat.diffuse_color[3])
    # alpha: glass is blended, texture cut-outs are clipped; everything else takes a variant without the transparent branch
    has_tex_alpha = img_node is not None and img_node.image.alpha_mode != "NONE" and _has_alpha(img_node.image)
    on_img = _image_by_name(G.LIGHTS_ON_TEXTURE) if (is_lamp and lights == "on") else None
    if on_img is not None and _has_alpha(on_img):
        has_tex_alpha = True
    d = getattr(mat, "dff", None)  # MatFX env map and specular (DragonFF keeps them on mat.dff)
    env_img = None
    if d is not None and getattr(d, "export_env_map", False):
        env_img = _image_by_name(getattr(d, "env_map_tex", ""))
    lvl = float(getattr(d, "specular_level", 0.0) or 0.0) if d is not None and getattr(d, "export_specular", False) else 0.0
    grp = game_group(alpha=mat_alpha < 0.995 or has_tex_alpha, prelit=bool(prelit), env=env_img is not None,
                     spec=lvl > 0)
    g = nt.nodes.get(PREFIX)
    if g is None:
        src = out.inputs["Surface"].links[0].from_socket if out.inputs["Surface"].links else None
        if src is not None:
            mat["satk_look_src"] = [src.node.name, src.identifier]
        mat["satk_look_rm"] = str(getattr(mat, "surface_render_method", "") or getattr(mat, "blend_method", ""))
        g = b.node("ShaderNodeGroup", name=PREFIX, label=PREFIX)
        g.location = (out.location[0] - 260, out.location[1] - 320)
    if g.node_tree is not grp:
        g.node_tree = grp
    flags: dict = {}
    if img_node is not None:
        b.link(img_node.outputs["Color"], g.inputs["Texture"])
        b.link(img_node.outputs["Alpha"], g.inputs["Texture Alpha"])
        g.inputs["Has Texture"].default_value = 1.0
    rgb = material_rgb(mat)
    unlit = 0.0
    if is_lamp:
        lamp = G.lamp_index([round(v * 255) for v in rgb])
        rgb = (1.0, 1.0, 1.0)
        flags["lamp"] = 1
        if lights == "on" and lamp is not None:
            if on_img is not None:
                n = nt.nodes.get(PREFIX + " lightson") or b.node("ShaderNodeTexImage", name=PREFIX + " lightson")
                n.image = on_img
                b.link(n.outputs["Color"], g.inputs["Texture"])
                b.link(n.outputs["Alpha"], g.inputs["Texture Alpha"])
            unlit = 1.0
            flags["lit_lamp"] = 1
    elif kind == "vehicle":
        p = _paint(mat, colors)
        if p is not None:
            rgb = p
            flags["paint"] = 1
    g.inputs["Material"].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
    g.inputs["Material Alpha"].default_value = mat_alpha
    g.inputs["Unlit"].default_value = unlit
    is_dirt = kind == "vehicle" and D.is_dirt_image(img_name)
    g.inputs["Dirt"].default_value = D.white_share(dirt) if is_dirt else 0.0
    if is_dirt:
        flags["dirt"] = 1
    g.inputs["Prelit"].default_value = 1.0 if prelit else 0.0
    g.inputs["Has Night"].default_value = 1.0 if has_night else 0.0
    g.inputs["Env Factor"].default_value = 0.0
    if env_img is not None:
        x_tex = env_img.name.lower().startswith("x")
        uv2 = _uv2_name(mat) if x_tex else None
        n = nt.nodes.get(PREFIX + " env")
        if n is None:
            n = b.node("ShaderNodeTexImage" if uv2 else "ShaderNodeTexEnvironment", name=PREFIX + " env")
        n.image = env_img
        if uv2:
            uvn = nt.nodes.get(PREFIX + " uv2") or b.node("ShaderNodeUVMap", name=PREFIX + " uv2")
            uvn.uv_map = uv2
            b.link(uvn.outputs["UV"], n.inputs["Vector"])
            g.inputs["Env Lerp"].default_value = 1.0
            g.inputs["Env Factor"].default_value = G.WAVE_ALPHA
        else:
            tc = nt.nodes.get(PREFIX + " tc") or b.node("ShaderNodeTexCoord", name=PREFIX + " tc")
            b.link(tc.outputs["Reflection"], n.inputs["Vector"])
            g.inputs["Env Lerp"].default_value = 0.0
            coef = float(getattr(d, "env_map_coef", 0.0) or 0.0)
            g.inputs["Env Factor"].default_value = min(0.5, max(0.0, coef * G.SPEC_INTENSITY * 0.5))
        b.link(n.outputs["Color"], g.inputs["Env"])
        flags["env"] = 1
    g.inputs["Spec"].default_value = min(1.0, 2.0 * G.SPEC_INTENSITY * lvl)
    g.inputs["Spec Power"].default_value = max(4.0, 100.0 * lvl)
    if lvl > 0:
        flags["spec"] = 1
    if mat_alpha < 0.995:
        g.inputs["Clip"].default_value = 0.0
        _render_method(mat, "BLENDED")
        flags["glass"] = 1
    else:
        g.inputs["Clip"].default_value = 1.0 if has_tex_alpha else 0.0
        _render_method(mat, "DITHERED")
    b.link(g.outputs["Shader"], out.inputs["Surface"])
    mat["satk_look"] = "game"
    return flags


_alpha_cache: dict = {}


def clear_caches() -> None:
    """Forget per-image facts (a session changes its images between calls)."""
    _alpha_cache.clear()


def _has_alpha(img) -> bool:
    """True if any texel of ``img`` is not fully opaque (cached per image name)."""
    key = img.name
    if key in _alpha_cache:
        return _alpha_cache[key]
    import numpy as np

    ok = False
    try:
        n = len(img.pixels)
        if n and img.channels >= 4:
            px = np.empty(n, dtype=np.float32)
            img.pixels.foreach_get(px)
            ok = bool((px[3::4] < 0.999).any())
    except (RuntimeError, OSError):
        ok = False
    _alpha_cache[key] = ok
    return ok


def _render_method(mat, method: str) -> None:
    if hasattr(mat, "surface_render_method"):
        try:
            mat.surface_render_method = method
            return
        except (TypeError, ValueError):
            pass
    try:
        mat.blend_method = "BLEND" if method == "BLENDED" else "CLIP"
    except (AttributeError, TypeError):
        pass


def restore(mat) -> bool:
    """Undo :func:`apply_game` on ``mat``: relink the original surface, delete the ``SATK look`` nodes."""
    if mat is None or mat.get("satk_look") is None or mat.node_tree is None:
        return False
    nt = mat.node_tree
    out = _output(nt)
    src = mat.get("satk_look_src")
    for n in [n for n in nt.nodes if n.name.startswith(PREFIX)]:
        nt.nodes.remove(n)
    if out is not None and src:
        node = nt.nodes.get(str(src[0]))
        if node is not None:
            sock = next((s for s in node.outputs if s.identifier == src[1]), None)
            if sock is not None:
                nt.links.new(sock, out.inputs["Surface"])
    rm = mat.get("satk_look_rm")
    if rm:
        try:
            if hasattr(mat, "surface_render_method") and rm in ("DITHERED", "BLENDED"):
                mat.surface_render_method = rm
            elif rm in ("CLIP", "BLEND", "HASHED", "OPAQUE"):
                mat.blend_method = rm
        except (AttributeError, TypeError, ValueError):
            pass
    for k in ("satk_look", "satk_look_src", "satk_look_rm"):
        if k in mat:
            del mat[k]
    return True


def restore_all() -> int:
    n = sum(1 for m in bpy.data.materials if restore(m))
    restore_images()
    return n


def display_images(materials) -> int:
    """Read the images of ``materials`` as ``Non-Color`` (display bytes); :func:`restore_images` undoes it."""
    n = 0
    for m in materials:
        if m is None or m.node_tree is None:
            continue
        for node in m.node_tree.nodes:
            img = getattr(node, "image", None) if node.type in ("TEX_IMAGE", "TEX_ENVIRONMENT") else None
            if img is None or "satk_look_cs" in img:
                continue
            try:
                img["satk_look_cs"] = img.colorspace_settings.name
                img.colorspace_settings.name = "Non-Color"
                n += 1
            except (TypeError, AttributeError, RuntimeError):
                pass
    return n


def restore_images() -> int:
    n = 0
    for img in bpy.data.images:
        if "satk_look_cs" in img:
            try:
                img.colorspace_settings.name = str(img["satk_look_cs"])
            except (TypeError, AttributeError, RuntimeError):
                pass
            del img["satk_look_cs"]
            n += 1
    return n


# --------------------------------------------------------------------------- clay / wire / ground


def override_material(kind: str):
    """``clay`` or ``wire`` override material (grey, lit by the preview light; wire adds triangle edges)."""
    name = CLAY if kind == "clay" else WIRE
    m = bpy.data.materials.get(name)
    if m is not None:
        return m
    m = bpy.data.materials.new(name)
    _nodes_on(m)
    nt = m.node_tree
    nt.nodes.clear()
    b = _B(nt)
    out = b.node("ShaderNodeOutputMaterial")
    geo, ndl = _lambert(b)
    # facing ratio (readable in every view) plus a little of the preview light for depth
    ndv = b.math("ABSOLUTE", b.vmath("DOT_PRODUCT", geo.outputs["Normal"], geo.outputs["Incoming"]))
    shade = b.math("ADD", b.math("ADD", b.math("MULTIPLY", ndv, 0.55), b.math("MULTIPLY", ndl, 0.15)), 0.22)
    grey = b.vmath("SCALE", b.const_vec((0.80, 0.79, 0.77)), shade)
    if kind == "wire":
        wf = b.node("ShaderNodeWireframe", use_pixel_size=True)
        wf.inputs["Size"].default_value = 1.0
        edge = b.math("MULTIPLY", wf.outputs[0], 0.7)
        grey = b.mix(grey, (0.16, 0.18, 0.22, 1.0), edge)
    em = b.node("ShaderNodeEmission")
    b.link(grey, em.inputs["Color"])
    b.link(em.outputs[0], out.inputs["Surface"])
    return m


def default_material():
    """White material for meshes without one (the engine draws them untextured white)."""
    m = bpy.data.materials.get("SATK_look_default")
    if m is None:
        m = bpy.data.materials.new("SATK_look_default")
        _nodes_on(m)
        m.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    return m


def ground_material(e: dict | None = None):
    """The ground plane: the group with a flat colour, lit like an object; with ``e`` (a timecyc light) the
    colour is chosen so that the lit and graded ground is the neutral grey of :data:`lights.GROUND_RGB`."""
    from .lights import ground_rgb

    m = bpy.data.materials.get(GROUND_MAT)
    if m is None:
        m = bpy.data.materials.new(GROUND_MAT)
        _nodes_on(m)
        nt = m.node_tree
        nt.nodes.clear()
        b = _B(nt)
        out = b.node("ShaderNodeOutputMaterial")
        g = b.node("ShaderNodeGroup", name=PREFIX + " ground")
        g.node_tree = game_group(alpha=False, prelit=False, env=False, spec=False)
        b.link(g.outputs["Shader"], out.inputs["Surface"])
    g = m.node_tree.nodes.get(PREFIX + " ground")
    if g is not None:
        g.inputs["Material"].default_value = (*ground_rgb(e), 1.0)
    return m
