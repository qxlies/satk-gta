# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Cameras, lights and renders (Workbench / EEVEE) plus the object-ID pass.

The object-ID pass is a second EEVEE render: every object with ``satk_sid`` gets a unique flat
colour (object colour, no lighting, ``Raw`` view transform), the PNG is decoded back to SIDs and
the pixel share of each visible SID is reported. Untagged meshes write zero IDs and keep occluding.
EEVEE/Workbench have no object-index pass, and Cycles would be far slower.
"""

from __future__ import annotations

import math
import os

import bpy
from mathutils import Euler, Vector

from . import shading

CAM = "SATK_cam"
SUN = "SATK_sun"
WORLD = "SATK_world"
_ENGINES = {"workbench": ("BLENDER_WORKBENCH",), "eevee": ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT")}


def _sky(balance: float) -> tuple[float, float, float]:
    day = (0.55, 0.65, 0.80)
    night = (0.02, 0.025, 0.05)
    return tuple(d + (n - d) * balance for d, n in zip(day, night))  # type: ignore[return-value]


def setup_world(scene, balance: float = 0.0):
    w = bpy.data.worlds.get(WORLD) or bpy.data.worlds.new(WORLD)
    scene.world = w
    sky = _sky(balance)
    w.color = sky
    try:
        w.use_nodes = True  # deprecated in 5.x (removed in 6.0); still needed for the EEVEE background
    except AttributeError:
        pass
    nt = w.node_tree
    if nt is not None:
        bg = next((n for n in nt.nodes if n.type == "BACKGROUND"), None)
        if bg is not None:
            bg.inputs[0].default_value = (*sky, 1.0)
            bg.inputs[1].default_value = 1.0
    sun = bpy.data.objects.get(SUN)
    if sun is None:
        ld = bpy.data.lights.new(SUN, "SUN")
        sun = bpy.data.objects.new(SUN, ld)
        scene.collection.objects.link(sun)
    sun.data.energy = 3.0 * (1.0 - 0.85 * balance)
    sun.rotation_euler = (math.radians(50), 0.0, math.radians(30))
    return w


def camera(scene, pos, forward, *, roll: float = 0.0, fov_h_deg: float = 70.0, clip_end: float = 3000.0):
    """Place ``SATK_cam``: ``fov_h_deg`` is the horizontal field of view (as SAAP poses)."""
    cam = bpy.data.objects.get(CAM)
    if cam is None or cam.type != "CAMERA":
        cd = bpy.data.cameras.new(CAM)
        cam = bpy.data.objects.new(CAM, cd)
        scene.collection.objects.link(cam)
    cd = cam.data
    cd.sensor_fit = "HORIZONTAL"
    cd.angle = math.radians(max(1.0, min(179.0, fov_h_deg)))
    cd.clip_start = 0.1
    cd.clip_end = clip_end
    cam.location = Vector(pos)
    q = Vector(forward).to_track_quat("-Z", "Y")
    if roll:
        q = q @ Euler((0.0, 0.0, -math.radians(roll))).to_quaternion()
    cam.rotation_mode = "XYZ"
    cam.rotation_euler = q.to_euler()
    scene.camera = cam
    return cam


def pose_camera(scene, pose: dict, fov_default: float = 70.0):
    pos = Vector(pose["pos"])
    if "look" in pose:
        fwd = Vector(pose["look"]) - pos
        roll = 0.0
    else:
        yaw, pitch, roll = (math.radians(v) for v in pose["ypr"])
        fwd = Vector((-math.sin(yaw) * math.cos(pitch), math.cos(yaw) * math.cos(pitch), math.sin(pitch)))
        roll = math.degrees(roll)
    return camera(scene, pos, fwd.normalized(), roll=roll, fov_h_deg=pose.get("fov_h_deg") or fov_default)


def _fractional_alpha(scene) -> bool:
    """Workbench ignores blended material alpha; use EEVEE when visible materials need it."""
    import numpy as np

    materials = {s.material for o in scene.objects if o.type == "MESH" and o.visible_get() and not o.hide_render
                 for s in o.material_slots if s.material}
    images = set()
    for mat in materials:
        if mat.diffuse_color[3] < 1.0:
            return True
        if mat.node_tree is None:
            continue
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                alpha = node.inputs.get("Alpha")
                if alpha is not None and not alpha.is_linked and alpha.default_value < 1.0:
                    return True
            if node.type == "TEX_IMAGE" and node.image is not None:
                images.add(node.image)
    for img in images:
        if img.alpha_mode == "NONE":
            continue
        try:
            # Packed images are lazy after open_mainfile. Accessing pixels loads them;
            # has_data (and channels/size before loading) cannot decide whether to scan.
            source = img.pixels
            count = len(source)
            if not count or img.channels < 4 or min(img.size) <= 0:
                continue
            pixels = np.empty(count, dtype=np.float32)
            source.foreach_get(pixels)
        except (OSError, RuntimeError):  # missing/unreadable external image
            continue
        alpha = pixels[3::4]
        if np.any((alpha > 0.0) & (alpha < 1.0)):
            return True
    return False


def set_engine(scene, engine: str) -> str:
    """Select the requested engine, using EEVEE for alpha that Workbench cannot composite."""
    if engine == "workbench" and _fractional_alpha(scene):
        engine = "eevee"
    for eng in _ENGINES[engine]:
        try:
            scene.render.engine = eng
            break
        except TypeError:
            continue
    else:
        raise RuntimeError(f"render engine {engine} not available")
    if scene.render.engine == "BLENDER_WORKBENCH":
        sh = scene.display.shading
        sh.light = "STUDIO"
        sh.color_type = "TEXTURE"
        sh.show_shadows = False
        sh.show_cavity = False
        sh.show_object_outline = False
        sh.show_specular_highlight = False
        scene.display.render_aa = "8"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    return scene.render.engine


def engine_warnings(requested: str, actual: str) -> list[str]:
    if requested == "workbench" and actual != "BLENDER_WORKBENCH":
        return ["EEVEE_ALPHA: used EEVEE to composite blended textures/materials"]
    return []


def render_png(scene, path: str, size: tuple[int, int]) -> str:
    r = scene.render
    r.resolution_x, r.resolution_y = int(size[0]), int(size[1])
    r.resolution_percentage = 100
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGB"
    r.image_settings.color_depth = "8"
    r.filepath = path
    r.use_file_extension = False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.render.render(write_still=True)
    return path


def _pixels(path: str):
    import numpy as np

    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        a = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(a)
        return a.reshape(h, w, 4), img
    except Exception:
        bpy.data.images.remove(img)
        raise


def image_stats(path: str) -> dict:
    """Mean brightness (0..1) and standard deviation (0..255) of the RGB channels."""
    a, img = _pixels(path)
    try:
        rgb = a[:, :, :3]
        lum = rgb.mean(axis=2)
        return {"mean": round(float(lum.mean()), 4), "std": round(float((rgb * 255.0).std()), 2)}
    finally:
        bpy.data.images.remove(img)


# --------------------------------------------------------------------------- object-ID pass


def _id_color(i: int) -> tuple[int, int, int]:
    v = (i * 0x9E3779) & 0xFFFFFF  # spread neighbouring ids over the colour cube
    if v == 0:
        v = 0x010101
    return (v >> 16) & 255, (v >> 8) & 255, v & 255


def _id_material(mat) -> None:
    """Make ``mat`` emit the object colour (Object Info) with the texture alpha clipped at 0.5."""
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None) or         next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        out = nt.nodes.new("ShaderNodeOutputMaterial")
    img = next((n for n in nt.nodes if n.type == "TEX_IMAGE" and n.image is not None), None)
    oi = nt.nodes.new("ShaderNodeObjectInfo")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 1.0
    nt.links.new(oi.outputs["Color"], em.inputs["Color"])
    if img is not None:
        gt = nt.nodes.new("ShaderNodeMath")
        gt.operation = "GREATER_THAN"
        gt.inputs[1].default_value = 0.5
        nt.links.new(img.outputs["Alpha"], gt.inputs[0])
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        mix = nt.nodes.new("ShaderNodeMixShader")
        nt.links.new(gt.outputs[0], mix.inputs[0])
        nt.links.new(tr.outputs[0], mix.inputs[1])
        nt.links.new(em.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], out.inputs["Surface"])
    else:
        nt.links.new(em.outputs[0], out.inputs["Surface"])


def objindex(scene, path: str, size: tuple[int, int], min_px: int = 4) -> list[list]:
    """Render the ID pass to ``path`` and return ``[[sid, name, share], ...]`` (share of the image).

    EEVEE, one sample, ``Raw`` view: every material emits its object's colour (Object Info), texture
    alpha < 0.5 is cut (foliage, fences, wires), so the pass sees what the colour render sees.
    Call it after the colour render: materials are changed in memory (the job never saves them).
    """
    import numpy as np

    meshes = [o for o in scene.objects if o.type == "MESH" and o.visible_get() and not o.hide_render]
    objs = [o for o in meshes if o.get("satk_sid")]
    colors: dict[tuple[int, int, int], object] = {}
    for i, o in enumerate(objs, 1):
        c = _id_color(i)
        colors[c] = o
        o.color = (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, 1.0)
    blank = bpy.data.materials.new("satk_id_blank")
    blank.use_nodes = True
    for o in meshes:
        if not o.get("satk_sid"):
            o.color = (0.0, 0.0, 0.0, 1.0)  # background ID, with the mesh's depth and texture alpha
        if not o.data.materials:
            o.data.materials.append(blank)
        for slot in o.material_slots:
            if slot.material is None:
                slot.material = blank
    for m in {s.material for o in meshes for s in o.material_slots if s.material}:
        m.use_nodes = True
        if m.node_tree is not None:
            _id_material(m)
    _id_material(blank)
    set_engine(scene, "eevee")
    ee = scene.eevee
    for attr, val in (("taa_render_samples", 1), ("use_gtao", False), ("use_bloom", False)):
        if hasattr(ee, attr):
            setattr(ee, attr, val)
    if hasattr(scene.render, "filter_size"):
        scene.render.filter_size = 0.0
    scene.render.film_transparent = True
    scene.render.dither_intensity = 0.0
    scene.view_settings.view_transform = "Raw"
    scene.view_settings.look = "None"
    scene.render.image_settings.color_mode = "RGBA"
    render_png(scene, path, size)
    scene.render.image_settings.color_mode = "RGB"
    a, img = _pixels(path)
    try:
        px = np.rint(a * 255.0).astype(np.int32)
    finally:
        bpy.data.images.remove(img)
    mask = px[:, :, 3] > 127
    keys = (px[:, :, 0] << 16) | (px[:, :, 1] << 8) | px[:, :, 2]
    vals, counts = np.unique(keys[mask], return_counts=True)
    total = float(px.shape[0] * px.shape[1])
    lut = {(k >> 16 & 255, k >> 8 & 255, k & 255): o for k, o in ((c[0] << 16 | c[1] << 8 | c[2], o) for c, o in colors.items())}
    pal = np.array([[c[0], c[1], c[2]] for c in colors], dtype=np.int32) if colors else None
    pal_objs = list(colors.values())
    by_sid: dict[str, list] = {}
    for v, n in zip(vals.tolist(), counts.tolist()):
        if v == 0:
            continue  # reserve zero even if a generated colour happens to be very close to black
        c = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
        o = lut.get(c)
        if o is None and pal is not None:  # rounding: nearest assigned colour within 3 levels
            d = np.abs(pal - np.array(c)).max(axis=1)
            j = int(d.argmin())
            o = pal_objs[j] if d[j] <= 3 else None
        if o is None:
            continue
        sid = str(o["satk_sid"])
        e = by_sid.setdefault(sid, [sid, o.name.split("@")[0], 0])
        e[2] += n
    rows = [[s, nm, round(n / total, 4)] for s, nm, n in by_sid.values() if n >= min_px]
    rows.sort(key=lambda r: (-r[2], r[0]))
    return rows


# --------------------------------------------------------------------------- framing / sheets


def visible_points(objs, max_points: int = 200_000) -> list:
    """World-space vertices of the rendered meshes of ``objs`` (bounding-box corners for meshes
    beyond ``max_points`` in total): what framing has to keep inside the image."""
    import numpy as np

    pts: list = []
    budget = max_points
    for o in objs:
        if o.type != "MESH" or o.hide_render:
            continue
        mw = o.matrix_world
        n = len(o.data.vertices)
        if 0 < n <= budget:
            co = np.empty(n * 3, dtype=np.float64)
            o.data.vertices.foreach_get("co", co)
            m = np.array(mw, dtype=np.float64)
            w = co.reshape(n, 3) @ m[:3, :3].T + m[:3, 3]
            pts.extend(Vector(v) for v in w.tolist())
            budget -= n
        else:
            pts.extend(mw @ Vector(c) for c in o.bound_box)
    return pts


def visible_bounds(objs):
    pts = visible_points(objs)
    if not pts:
        return Vector((0, 0, 0)), 1.0
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    c = (lo + hi) / 2
    return c, max((hi - lo).length / 2, 0.5)


def fit_view(pts, d, fov_h_deg: float, aspect: float = 1.0, margin: float = 0.08):
    """Camera target and distance that keep every point of ``pts`` inside the frame.

    The camera looks along ``-d`` (``d`` = unit vector from the target to the camera). The target is
    the centre of the points' extent across the view; the distance is the smallest one at which each
    point fits the horizontal and vertical half-angles reduced by ``margin`` (a share of the frame on
    each side). Returns ``(target, distance)``.
    """
    import numpy as np

    d = Vector(d).normalized()
    fwd = -d
    right = fwd.cross(Vector((0.0, 0.0, 1.0)))
    if right.length < 1e-6:
        right = Vector((1.0, 0.0, 0.0))
    right.normalize()
    up = right.cross(fwd).normalized()
    P = np.array([tuple(p) for p in pts], dtype=np.float64).reshape(-1, 3)
    R, U, F = (np.array(tuple(v)) for v in (right, up, fwd))
    xs, ys, zs = P @ R, P @ U, P @ F
    # view-space coordinates of the target (centre of the extent); the camera sits at depth -dist
    tx, ty, tz = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2, (zs.min() + zs.max()) / 2
    th = math.tan(math.radians(fov_h_deg) / 2) * (1.0 - margin)  # sensor_fit HORIZONTAL (camera())
    tv = th / aspect  # aspect = width / height

    def _dist(tx, ty, tz):
        x, y, z = np.abs(xs - tx), np.abs(ys - ty), zs - tz
        # depth of a point from a camera at target - fwd * D is D + z: need |x| <= th (D + z), |y| <= tv (D + z)
        return float(max(0.5, (x / th - z).max(), (y / tv - z).max(), (0.1 - z).max()))

    dist = _dist(tx, ty, tz)
    for _ in range(4):  # perspective: centre the projected extent (near parts look bigger), then refit
        depth = dist + (zs - tz)
        px, py = (xs - tx) / depth, (ys - ty) / depth
        tx += (px.min() + px.max()) / 2 * dist
        ty += (py.min() + py.max()) / 2 * dist
        dist = _dist(tx, ty, tz)
    target = right * tx + up * ty + fwd * tz
    return target, dist


def edge_margin(scene, cam, pts) -> float:
    """Smallest distance (share of the frame, 0..0.5) from a projected point to the frame edge;
    negative when a point falls outside the image (it would be cut off)."""
    from bpy_extras.object_utils import world_to_camera_view

    m = 0.5
    for p in pts:
        v = world_to_camera_view(scene, cam, p)
        m = min(m, v.x, 1.0 - v.x, v.y, 1.0 - v.y)
    return round(m, 4)


def orbit_views(scene, objs, out_dir: str, *, views: int, size: int, engine: str, prefix: str = "view",
                margins: list | None = None) -> list[str]:
    """``views`` renders around the objects (azimuth 45/135/225/315, elevation 25); every view is
    framed on the bounding-box corners of the rendered meshes with a margin (nothing is cut off)."""
    bpy.context.view_layer.update()
    pts = visible_points(objs) or [Vector((0, 0, 0))]
    fov = 40.0
    az = [45.0, 135.0, 225.0, 315.0][:views] if views > 1 else [45.0]
    out = []
    set_engine(scene, engine)
    for k, a in enumerate(az):
        el = math.radians(25.0)
        d = Vector((-math.sin(math.radians(a)) * math.cos(el), math.cos(math.radians(a)) * math.cos(el), math.sin(el)))
        target, dist = fit_view(pts, d, fov)
        cam = camera(scene, target + d * dist, -d, fov_h_deg=fov, clip_end=dist * 4 + 100)
        out.append(render_png(scene, os.path.join(out_dir, f"{prefix}_{k}.png"), (size, size)))
        if margins is not None:
            bpy.context.view_layer.update()
            margins.append(edge_margin(scene, cam, pts))
    return out


def sheet(paths: list[str], out: str, cols: int = 2) -> str:
    """Compose renders into one PNG grid (all the same size)."""
    import numpy as np

    arrs = []
    for p in paths:
        a, img = _pixels(p)
        arrs.append(a.copy())
        bpy.data.images.remove(img)
    h, w = arrs[0].shape[:2]
    rows = (len(arrs) + cols - 1) // cols
    canvas = np.zeros((rows * h, cols * w, 4), dtype=np.float32)
    canvas[:, :, 3] = 1.0
    for i, a in enumerate(arrs):
        r, c = divmod(i, cols)
        # Blender pixel rows start at the bottom: row 0 of the grid is the top band
        y0 = (rows - 1 - r) * h
        canvas[y0:y0 + h, c * w:(c + 1) * w] = a
    img = bpy.data.images.new("satk_sheet", cols * w, rows * h, alpha=False)
    img.pixels.foreach_set(canvas.ravel())
    img.filepath_raw = out
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)
    return out


def default_area_camera(scene, plan: dict) -> None:
    """A camera looking at the area centre from the south, above (so a fresh .blend renders)."""
    q = plan.get("query") or {}
    c = q.get("center") or [0, 0]
    zs = [i["pos"][2] for i in plan.get("insts", [])] or [0.0]
    zs.sort()
    z = zs[len(zs) // 2]
    span = 100.0
    if q.get("box"):
        b = q["box"]
        span = max(b[2] - b[0], b[3] - b[1])
    elif q.get("r"):
        span = 2 * q["r"]
    look = Vector((c[0], c[1], z))
    pos = look + Vector((0.0, -0.9 * span, 0.6 * span))
    camera(scene, pos, (look - pos).normalized(), fov_h_deg=70.0, clip_end=max(3000.0, span * 6))


def apply_time(balance: float, scene=None) -> None:
    scene = scene or bpy.context.scene
    if bpy.data.node_groups.get(shading.GROUP) is not None:
        shading.set_time(balance)
    setup_world(scene, balance)


__all__ = ["camera", "pose_camera", "set_engine", "render_png", "image_stats", "objindex", "orbit_views", "sheet",
           "setup_world", "default_area_camera", "apply_time", "visible_bounds", "visible_points", "fit_view",
           "edge_margin"]
