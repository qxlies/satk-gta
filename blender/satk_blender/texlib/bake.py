# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 satk authors. See ../LICENSE for the full license.
"""Cycles emission bakes of the original node recipes in data/texlib.

UV sine/cosine coordinates make every noise field periodic in both directions.
Emission records the material's colour and painted occlusion, independent of lights
and view transforms. No external images, add-ons, or game files are used.
"""

from __future__ import annotations

import json
import math
import random
import sys
import time
from pathlib import Path

import bpy


class Graph:
    def __init__(self, tree, seed):
        self.tree = tree
        self.rng = random.Random(seed)
        uv = self.node("ShaderNodeTexCoord").outputs["UV"]
        sep = self.node("ShaderNodeSeparateXYZ")
        self.link(uv, sep.inputs[0])
        self.u, self.v = sep.outputs["X"], sep.outputs["Y"]
        u = self.math("MULTIPLY", self.u, math.tau)
        v = self.math("MULTIPLY", self.v, math.tau)
        combine = self.node("ShaderNodeCombineXYZ")
        for socket, x in zip(combine.inputs, (self.math("COSINE", u), self.math("SINE", u),
                                             self.math("COSINE", v))):
            self.link(self.math("ADD", x, self.rng.uniform(-20, 20)), socket)
        self.vector = combine.outputs[0]
        self.w = self.math("ADD", self.math("SINE", v), self.rng.uniform(-20, 20))

    def node(self, kind):
        return self.tree.nodes.new(kind)

    def link(self, value, socket):
        if isinstance(value, (float, int, tuple, list)):
            socket.default_value = value
        else:
            self.tree.links.new(value, socket)

    def math(self, operation, a, b=0):
        n = self.node("ShaderNodeMath")
        n.operation = operation
        self.link(a, n.inputs[0])
        self.link(b, n.inputs[1])
        return n.outputs[0]

    def mix(self, factor, a, b, mode="MIX"):
        n = self.node("ShaderNodeMixRGB")
        n.blend_type = mode
        self.link(factor, n.inputs[0])
        self.link(a, n.inputs[1])
        self.link(b, n.inputs[2])
        return n.outputs[0]

    def noise(self, scale, detail=3, roughness=0.7):
        n = self.node("ShaderNodeTexNoise")
        n.noise_dimensions = "4D"
        self.link(self.vector, n.inputs["Vector"])
        self.link(self.w, n.inputs["W"])
        n.inputs["Scale"].default_value = scale
        n.inputs["Detail"].default_value = detail
        n.inputs["Roughness"].default_value = roughness
        return n.outputs["Fac"]

    def remap(self, value, lo, hi):
        n = self.node("ShaderNodeMapRange")
        n.interpolation_type = "SMOOTHSTEP"
        n.clamp = True
        self.link(value, n.inputs["Value"])
        n.inputs["From Min"].default_value = lo
        n.inputs["From Max"].default_value = hi
        return n.outputs[0]

    def wave(self, coord, count, warp=0):
        phase = self.math("ADD", self.math("MULTIPLY", coord, math.tau * count), warp)
        return self.math("ADD", self.math("MULTIPLY", self.math("SINE", phase), 0.5), 0.5)

    def grid(self, columns, rows, stagger=True):
        y = self.math("MULTIPLY", self.v, rows)
        shift = self.math("MULTIPLY", self.math("MODULO", self.math("FLOOR", y), 2), 0.5) if stagger else 0
        x = self.math("FRACT", self.math("ADD", self.math("MULTIPLY", self.u, columns), shift))
        y = self.math("FRACT", y)
        dx = self.math("MINIMUM", x, self.math("SUBTRACT", 1, x))
        dy = self.math("MINIMUM", y, self.math("SUBTRACT", 1, y))
        # Soft bevel into the joint; integer cell counts close across both tile edges.
        face = self.remap(self.math("MINIMUM", dx, dy), 0.018, 0.055)
        return face, x, y


def material(recipe, seed):
    mat = bpy.data.materials.new("satk_texlib_recipe")
    if mat.node_tree is None:
        mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    g = Graph(tree, seed)
    base = (*recipe["base"], 1.0)
    second = (*recipe["secondary"], 1.0)
    coarse = g.noise(recipe["scale"], 4)
    fine = g.noise(45, 2)
    kind = recipe["pattern"]
    colour = g.mix(g.remap(coarse, 0.15, 0.85), second, base)
    mask = None
    if kind in ("brick", "tiles", "wood", "roof"):
        face, x, y = g.grid(*recipe["grid"], stagger=kind != "tiles")
        colour = g.mix(face, second, colour)
        if kind == "wood":
            grain = g.wave(g.u, 25, g.math("MULTIPLY", coarse, 12))
            colour = g.mix(0.4, colour, grain, "MULTIPLY")
        elif kind == "roof":
            rib = g.math("ADD", 0.4, g.math("MULTIPLY", g.math("SINE", g.math("MULTIPLY", x, math.pi)), 0.6))
            overlap = g.math("ADD", 0.55, g.math("MULTIPLY", y, 0.45))
            colour = g.mix(0.7, colour, g.math("MULTIPLY", rib, overlap), "MULTIPLY")
    elif kind in ("rust", "paint"):
        islands = g.remap(coarse, 0.40 if kind == "rust" else 0.51, 0.60 if kind == "rust" else 0.62)
        colour = g.mix(islands, base, second)
        colour = g.mix(0.3, colour, fine, "MULTIPLY")
    elif kind == "corrugated":
        rib = g.wave(g.u, 12)
        colour = g.mix(0.8, colour, g.math("ADD", 0.30, g.math("MULTIPLY", rib, 0.70)), "MULTIPLY")
    elif kind == "sand":
        ripple = g.wave(g.v, 12, g.math("MULTIPLY", coarse, 8))
        colour = g.mix(0.3, colour, ripple, "MULTIPLY")
    elif kind == "grass":
        blades = g.wave(g.u, 55, g.math("MULTIPLY", g.noise(16, 2), 20))
        colour = g.mix(0.45, colour, blades, "MULTIPLY")
    elif kind == "fabric":
        weave = g.math("MULTIPLY", g.wave(g.u, 64), g.wave(g.v, 64))
        folds = g.wave(g.v, 4, g.math("MULTIPLY", coarse, 2))
        colour = g.mix(0.35, colour, weave, "MULTIPLY")
        colour = g.mix(0.55, colour, folds, "MULTIPLY")
    elif kind == "leather":
        crease = g.remap(g.noise(26, 1), 0.40, 0.54)
        colour = g.mix(0.65, colour, crease, "MULTIPLY")
    elif kind == "rubber":
        groove = g.remap(g.wave(g.u, 8), 0.06, 0.20)
        colour = g.mix(0.45, colour, groove, "MULTIPLY")
    elif kind == "glass":
        streak = g.wave(g.u, 21, g.math("MULTIPLY", coarse, 4))
        colour = g.mix(0.35, colour, streak, "MULTIPLY")
        mask = g.math("ADD", 0.15, g.math("MULTIPLY", g.math("MULTIPLY", streak, coarse), 0.75))
    elif kind == "grime":
        mask = g.remap(coarse, 0.40, 0.68)
    elif kind == "decal":
        stripes = g.remap(g.wave(g.math("ADD", g.u, g.v), 4), 0.42, 0.55)
        chips = g.remap(g.noise(28, 2), 0.34, 0.53)
        mask = g.math("MULTIPLY", stripes, chips)
    else:
        # Aggregate, plaster and plastic use different spatial scales and pore thresholds.
        pores = g.remap(fine, {"asphalt": 0.4, "dirt": 0.28, "plastic": 0.45}.get(kind, 0.25), 0.7)
        strength = {"asphalt": 0.65, "dirt": 0.5, "concrete": 0.4, "plaster": 0.22, "plastic": 0.18}[kind]
        colour = g.mix(strength, colour, pores, "MULTIPLY")
    colour = g.mix(0.14, colour, fine, "MULTIPLY")
    em = g.node("ShaderNodeEmission")
    out = g.node("ShaderNodeOutputMaterial")
    g.link(colour, em.inputs["Color"])
    g.link(em.outputs[0], out.inputs["Surface"])
    return mat, em, colour, mask


def bake(request):
    from satk.core import paths

    started = time.perf_counter()
    recipe, size = request["recipe"], request["size"]
    scene = bpy.context.scene
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.object
    mat, emission, colour, mask = material(recipe, request["seed"])
    plane.data.materials.append(mat)
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 1
    scene.cycles.seed = request["seed"] & 0x7FFFFFFF
    scene.render.bake.target = "IMAGE_TEXTURES"
    scene.render.bake.margin = 0
    scene.render.bake.use_selected_to_active = False
    scene.render.bake.use_clear = True
    result = {"ok": True, "engine": "CYCLES", "blender": bpy.app.version_string}
    for label, socket in (("colour", colour), ("mask", mask)):
        if socket is None:
            continue
        img = bpy.data.images.new("satk_texlib_" + label, size * 4, size * 4, alpha=False, float_buffer=False)
        img.colorspace_settings.name = "Non-Color"
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        mat.node_tree.nodes.active = tex
        mat.node_tree.links.new(socket, emission.inputs["Color"])
        bpy.ops.object.bake(type="EMIT")
        destination = paths.ensure_writable(Path(request["out"]) / (label + ".png"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        img.filepath_raw = str(destination)
        img.file_format = "PNG"
        img.save()
        result[label] = paths.jpath(destination)
        mat.node_tree.nodes.remove(tex)
        bpy.data.images.remove(img)
    result["bake_seconds"] = round(time.perf_counter() - started, 4)
    result["node_types"] = sorted({n.bl_idname for n in mat.node_tree.nodes})
    return result


def main():
    # Load the MIT package from this checkout; no user profile or installed add-on is involved.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
    from satk.core import paths

    request_path = Path(sys.argv[sys.argv.index("--") + 1])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    result = bake(request)
    paths.atomic_write(request_path.with_name("response.json"), json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
