# SPDX-License-Identifier: GPL-3.0-or-later
"""Cycles selected-to-active base colour/alpha baking into small target atlases."""

from __future__ import annotations

import math
from contextlib import contextmanager

import bpy

from satk.core.errors import SatkError

from . import common as C


@contextmanager
def _scene(hi, low, samples):
    scene = bpy.context.scene
    engine = scene.render.engine
    active = bpy.context.view_layer.objects.active
    visibility = [(o, o.hide_render, o.hide_get(), o.select_get()) for o in scene.objects]
    settings = {k: getattr(scene.render.bake, k) for k in ("use_selected_to_active", "cage_extrusion",
                "max_ray_distance", "use_pass_direct", "use_pass_indirect", "use_pass_color", "target", "margin")}
    cycles = {k: getattr(scene.cycles, k) for k in ("samples", "device", "seed")}
    try:
        for o, *_ in visibility:
            o.hide_render = o not in (hi, low)
            o.select_set(False)
        hi.hide_set(False)
        low.hide_set(False)
        hi.select_set(True)
        low.select_set(True)
        bpy.context.view_layer.objects.active = low
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = samples
        scene.cycles.seed = 0
        bake = scene.render.bake
        bake.use_selected_to_active = True
        diagonal = max(low.dimensions.length, 0.01)
        bake.cage_extrusion = diagonal * 0.04
        bake.max_ray_distance = diagonal * 0.2
        bake.use_pass_direct = bake.use_pass_indirect = False
        bake.use_pass_color = True
        bake.target = "IMAGE_TEXTURES"
        bake.margin = 4
        yield
    finally:
        for k, v in settings.items():
            setattr(scene.render.bake, k, v)
        for k, v in cycles.items():
            setattr(scene.cycles, k, v)
        scene.render.engine = engine
        for o, render, hidden, selected in visibility:
            o.hide_render = render
            o.hide_set(hidden)
            o.select_set(selected)
        bpy.context.view_layer.objects.active = active


@contextmanager
def _source_materials(hi, alpha: bool):
    originals = list(hi.data.materials)
    temporary = []
    try:
        for original in originals or [None]:
            mat = original.copy() if original else bpy.data.materials.new("convert_default")
            temporary.append(mat)
            if hasattr(mat, "use_nodes") and not mat.use_nodes:
                mat.use_nodes = True
            nodes, links = mat.node_tree.nodes, mat.node_tree.links
            output = next((n for n in nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
            if output is None:
                output = nodes.new("ShaderNodeOutputMaterial")
            shader = output.inputs["Surface"].links[0].from_node if output.inputs["Surface"].links else None
            socket = None
            if shader:
                if shader.type == "BSDF_PRINCIPLED":
                    socket = shader.inputs["Alpha" if alpha else "Base Color"]
                elif shader.type in ("BSDF_DIFFUSE", "EMISSION") and not alpha:
                    socket = shader.inputs["Color"]
                elif not alpha:
                    raise SatkError("UNSUPPORTED", f"material {original.name}: unsupported surface shader {shader.type}",
                                    hint="connect a Principled, Diffuse or Emission shader to Material Output; "
                                         "procedural colour and image nodes are supported")
            emission = nodes.new("ShaderNodeEmission")
            if socket and socket.is_linked:
                links.new(socket.links[0].from_socket, emission.inputs["Color"])
            else:
                value = socket.default_value if socket else (1.0 if alpha else mat.diffuse_color)
                emission.inputs["Color"].default_value = (value, value, value, 1) if isinstance(value, (int, float)) else value
            links.new(emission.outputs[0], output.inputs["Surface"])
        hi.data.materials.clear()
        for mat in temporary:
            hi.data.materials.append(mat)
        yield
    finally:
        hi.data.materials.clear()
        for mat in originals:
            hi.data.materials.append(mat)
        for mat in temporary:
            bpy.data.materials.remove(mat)


def _has_alpha(hi) -> bool:
    for mat in hi.data.materials:
        if mat and mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.type == "BSDF_PRINCIPLED":
                    socket = node.inputs["Alpha"]
                    if socket.is_linked or socket.default_value < 0.999:
                        return True
    return False


def _unwrap(low, size: int) -> None:
    # The source UVs remain on the high mesh; the low mesh needs unique islands for the consolidated atlas.
    while low.data.uv_layers:
        low.data.uv_layers.remove(low.data.uv_layers[0])
    low.data.uv_layers.new(name="SA_Atlas")
    C.select(low)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=4 / size, scale_to_bounds=True)
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def _atlas_mask(mesh, size):
    """Rasterize UV coverage; Cycles may set alpha=1 even in untouched atlas pixels."""
    import numpy as np

    mesh.calc_loop_triangles()
    layer = mesh.uv_layers.active.data
    mask = np.zeros((size, size), dtype=bool)
    for triangle in mesh.loop_triangles:
        uv = np.array([tuple(layer[i].uv) for i in triangle.loops]) * size
        lo = np.maximum(0, np.floor(uv.min(axis=0)).astype(int))
        hi = np.minimum(size - 1, np.ceil(uv.max(axis=0)).astype(int))
        a, b = uv[1] - uv[0], uv[2] - uv[0]
        det = a[0] * b[1] - a[1] * b[0]
        if abs(det) < 1e-9 or (hi < lo).any():
            continue
        y, x = np.mgrid[lo[1]:hi[1] + 1, lo[0]:hi[0] + 1]
        dx, dy = x + 0.5 - uv[0, 0], y + 0.5 - uv[0, 1]
        u = (dx * b[1] - dy * b[0]) / det
        v = (a[0] * dy - a[1] * dx) / det
        mask[lo[1]:hi[1] + 1, lo[0]:hi[0] + 1] |= (u >= 0) & (v >= 0) & (u + v <= 1)
    return mask


def _fill_background(rgba, valid):
    """Extend baked colours outside the islands so atlas padding cannot bias the texture grade."""
    import numpy as np

    valid = valid.copy()
    coverage = float(valid.mean())
    if coverage < 0.01:
        raise SatkError("CHECK_FAILED", "the source bake covered less than 1% of the atlas",
                        hint="check the source normals and overlapping surfaces in the saved scene")
    rgb = rgba[..., :3]
    while not valid.all():
        neighbours = np.zeros_like(rgb)
        count = np.zeros(valid.shape, dtype=np.int16)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            mask = np.roll(valid, (dy, dx), axis=(0, 1))
            if dy:
                mask[0 if dy > 0 else -1, :] = False
            if dx:
                mask[:, 0 if dx > 0 else -1] = False
            neighbours += np.roll(rgb, (dy, dx), axis=(0, 1)) * mask[..., None]
            count += mask
        fill = ~valid & (count > 0)
        rgb[fill] = neighbours[fill] / count[fill, None]
        valid[fill] = True
    return coverage


def _bake(hi, low, role: str, size: int, samples: int, dest):
    import numpy as np

    _unwrap(low, size)
    image = bpy.data.images.new(f"convert_{role}_bake", size, size, alpha=True)
    mat = bpy.data.materials.new(f"convert_{role}_target")
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    mat.node_tree.nodes.active = node
    low.data.materials.clear()
    low.data.materials.append(mat)
    for face in low.data.polygons:
        face.material_index = 0
    transparent = _has_alpha(hi)
    with _scene(hi, low, samples):
        with _source_materials(hi, False):
            outcome = bpy.ops.object.bake("EXEC_DEFAULT", type="EMIT", use_clear=True, margin=4)
            if outcome != {"FINISHED"}:
                raise SatkError("EXTERNAL_TOOL", f"Cycles colour bake did not finish: {sorted(outcome)}")
        rgba = np.empty(size * size * 4, dtype=np.float32)
        image.pixels.foreach_get(rgba)
        coverage = _fill_background(rgba.reshape(size, size, 4), _atlas_mask(low.data, size))
        if transparent:
            with _source_materials(hi, True):
                outcome = bpy.ops.object.bake("EXEC_DEFAULT", type="EMIT", use_clear=True, margin=4)
                if outcome != {"FINISHED"}:
                    raise SatkError("EXTERNAL_TOOL", f"Cycles alpha bake did not finish: {sorted(outcome)}")
            alpha = np.empty_like(rgba)
            image.pixels.foreach_get(alpha)
            rgba[3::4] = np.clip(alpha[::4], 0, 1)
        else:
            rgba[3::4] = 1.0
    image.pixels.foreach_set(rgba)
    image.filepath_raw = str(dest)
    image.file_format = "PNG"
    image.save()
    return {"role": role, "file": dest.as_posix(), "size": size, "alpha": transparent,
            "coverage": round(coverage, 4),
            "bake": "Cycles EMIT selected-to-active (source base colour)", "uv": "unique atlas"}


def bake_method(ctx, params: dict) -> dict:
    """Bake the unreduced source materials to unique 128/256 px atlases with Cycles; plan=<plan.json>."""
    plan, folder, key, state = C.load(ctx, params, "reduce")
    bake_dir = C.writable(ctx, folder / "baked")
    bake_dir.mkdir(parents=True, exist_ok=True)
    textures = []
    for role, name in sorted(state["low"].items()):
        low = C.objects([name])[0]
        high = C.objects([state["high"][role]])[0]
        size = plan["presets"]["roles"][role][plan["tier"]]
        dest = C.writable(ctx, bake_dir / f"{plan['name']}_{role}.png")
        textures.append(_bake(high, low, role, size, plan["presets"]["bake_samples"], dest))
    C.write(ctx, folder / "bakes.json", {"key": plan["key"], "textures": textures})
    return C.save(ctx, folder, key, state, "bake", {"textures": len(textures), "engine": "CYCLES",
                                                    "changed": list(state["low"].values())})
