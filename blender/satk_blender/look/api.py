# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Contract K5: SA-like looks of a scene and small renders of it.

::

    from satk_blender.look import api
    info = api.apply(scene, look="game", dirt=2, time="21:30", lights="on")
    paths = api.render_views(scene, ["3q", "side"], 384, fmt="jpg", out_dir=d)
    api.sheet(paths, cols=2, out=d + "/sheet.jpg")
    api.restore(scene)

Looks: ``game`` (the engine's colour chain, :mod:`.materials`; EEVEE, ``Raw`` view, colour filter and
display gamma applied to the pixels), ``clay`` (grey, lit), ``wire`` (clay + triangle edges) and ``raw``
(DragonFF materials as imported, Workbench texture colour). The game look hides ``gunflash`` atomics;
:func:`restore` undoes everything (materials, visibility, overrides, ground, world).
"""

from __future__ import annotations

import json
import math
import os

import bpy
from mathutils import Vector

from satk.look import gamelook as G

from . import lights as Lt
from . import materials as M

__all__ = ["LOOKS", "VIEWS", "STD_VIEWS", "apply", "restore", "render_views", "sheet", "frame_points",
           "footprints", "model_groups", "class_objects"]

LOOKS = G.LOOKS
#: view -> (azimuth from +Y toward +X in degrees, elevation in degrees, orthographic)
VIEWS = {"3q": (45.0, 22.0, False), "rear3q": (225.0, 22.0, False), "front": (0.0, 0.0, True),
         "rear": (180.0, 0.0, True), "side": (-90.0, 0.0, True), "left": (-90.0, 0.0, True),
         "right": (90.0, 0.0, True), "top": (0.0, 90.0, True)}
STD_VIEWS = ("3q", "rear3q", "side", "top")
FOV = 30.0
MARGIN = 0.06
_KEY = "satk_look_state"


def _meshes(scene) -> list:
    return [o for o in scene.objects if o.type == "MESH" and not o.get("satk_look_aux")]


def _visible(scene) -> list:
    return [o for o in _meshes(scene) if not o.hide_render and o.visible_get()]


def model_groups(objs) -> dict:
    """Objects grouped by model (``satk_look_entry``/``satk_model`` tags, else the root of the parent chain).

    When no object carries a tag (a studio session building one asset) the whole set is one model."""
    out: dict = {}
    objs = list(objs)
    if objs and not any(o.get("satk_look_entry") or o.get("satk_model") for o in objs):
        return {"scene": objs}
    for o in objs:
        key = o.get("satk_look_entry") or o.get("satk_model")
        if not key:
            r = o
            while r.parent is not None:
                r = r.parent
            key = r.name
        out.setdefault(str(key), []).append(o)
    return out


def class_objects(objs, scene=None) -> list:
    """The objects that tell the class of a model group: the group itself, its parent chain (kit roots, dummies)
    and, for a group of untagged meshes (a studio session), the empties of the scene and of the meshes'
    collections. Meshes alone never say "vehicle": the tags live on roots and dummies."""
    out: dict = {}
    for o in objs:
        out[o.name] = o
        r = o.parent
        while r is not None:
            out.setdefault(r.name, r)
            r = r.parent
    if not any(o.get("satk_look_entry") for o in objs):
        colls = {c for o in objs for c in o.users_collection}
        for c in colls:
            for o in c.all_objects:
                if o.type == "EMPTY" and not o.get("satk_ghost") and not o.get("satk_look_aux"):
                    out.setdefault(o.name, o)
        if scene is not None and not any(o.get("satk_model") for o in objs):
            for o in scene.objects:
                if o.type == "EMPTY" and not o.get("satk_ghost") and not o.get("satk_look_entry")                         and not o.get("satk_look_aux"):
                    out.setdefault(o.name, o)
    return list(out.values())


def _remember_hidden(o) -> None:
    if "satk_look_hid" not in o:
        o["satk_look_hid"] = int(bool(o.hide_render))


def ensure_dragonff() -> bool:
    """Register DragonFF (object ``dff`` kinds: collision, shadow, 2DFX; material MatFX env and specular).
    A session that has not imported a model yet has not loaded it; False when it is not available."""
    from .. import common

    try:
        common.load_dragonff()
        return True
    except (RuntimeError, ImportError):
        return False


def apply(scene=None, look: str = "game", dirt: float = G.DIRT_DEFAULT, time="12:00", lights: str = "off", *,
          objects=None, env: dict | None = None, colors=None, weather: str = G.DEFAULT_WEATHER,
          profile: str = "vanilla", ground: bool = True, groups: list | None = None) -> dict:
    """Give ``scene`` a look. ``objects`` limits the material changes (default: every mesh).

    Args:
        look: game | clay | wire | raw.
        dirt: dirt level 0..16 of vehiclegrunge256 (game look; the game spawns 0..14).
        time: game time HH:MM (timecyc light, day/night prelight, sky).
        lights: off | on (lamp keys on vehiclelights128 use vehiclelightson128 and are drawn unlit).
        env: a ready timecyc light (``gamelook.env_at``), else computed from ``time``.
        colors: paint colours ``[[r, g, b] x4]`` (0..255) for the paint keys of vehicles.
        ground: add a ground plane and contact shadows (game look).
        groups: the models as lists of objects (default: :func:`model_groups`); each is classified on its
            own (vehicle, ped, building, object).
    Returns ``{"look", "materials", "flags": {...}, "env": {...}}``.
    """
    scene = scene or bpy.context.scene
    if look not in LOOKS:
        raise ValueError(f"look must be one of {'|'.join(LOOKS)}, got {look!r}")
    if lights not in ("off", "on"):
        raise ValueError("lights must be off or on")
    if not 0 <= float(dirt) <= G.DIRT_MAX:
        raise ValueError(f"dirt must be 0..{G.DIRT_MAX}")
    restore(scene)
    ensure_dragonff()
    e = dict(env) if env else Lt.env(time, weather, profile)
    objs = list(objects) if objects is not None else _meshes(scene)
    flags: dict = {}
    nmat = 0
    vl = bpy.context.view_layer
    if look == "game":
        from .. import shading

        M.set_env(e)
        M.clear_caches()
        if groups is not None:
            by_model = {str(i): list(g) for i, g in enumerate(groups)}
        else:
            by_model = model_groups([o for o in objs if o.type == "MESH"])
        seen: set = set()
        for _key, gall in sorted(by_model.items()):
            kind = M.classify(class_objects(gall, scene))
            gobjs = [o for o in gall if o.type == "MESH"]
            flags[f"kind_{kind}"] = flags.get(f"kind_{kind}", 0) + 1
            for o in gobjs:
                if G.is_gunflash(o.name) and not o.hide_render:
                    _remember_hidden(o)
                    o.hide_render = True
                    flags["gunflash_hidden"] = flags.get("gunflash_hidden", 0) + 1
                me = o.data
                if me is None:
                    continue
                if not any(m is not None for m in me.materials) and "satk_look_addmat" not in me:
                    # untextured white, like the engine; restore() takes it away again
                    me["satk_look_addmat"] = len(me.materials)
                    if len(me.materials):
                        me.materials[0] = M.default_material()
                    else:
                        me.materials.append(M.default_material())
                prelit = has_night = False
                if kind == "building" and len(me.color_attributes):
                    prelit, has_night = shading.prepare_prelit(me)
                cols = colors
                if cols is None:
                    v = o.get("satk_paint")
                    cols = json.loads(v) if isinstance(v, str) else None
                for m in me.materials:
                    if m is None or m.name in seen:
                        continue
                    seen.add(m.name)
                    for k in M.apply_game(m, kind=kind, prelit=prelit, has_night=has_night, dirt=float(dirt),
                                          lights=lights, colors=cols):
                        flags[k] = flags.get(k, 0) + 1
                    nmat += 1
        M.display_images([m for m in bpy.data.materials if m.get("satk_look")])
        vl.material_override = None
    elif look in ("clay", "wire"):
        vl.material_override = M.override_material(look)
    else:
        vl.material_override = None
    aux = []
    if ground and look == "game":
        aux = Lt.ground(scene, footprints(_visible(scene)), M.ground_material(e), shadows=look == "game")
    Lt.world(scene, e, grade=look == "game")
    scene[_KEY] = json.dumps({"look": look, "env": e, "dirt": float(dirt), "lights": lights})
    return {"look": look, "materials": nmat, "flags": flags, "aux": len(aux),
            "env": {k: e[k] for k in ("time", "balance", "weather", "source")}}


def restore(scene=None) -> dict:
    """Undo :func:`apply`: materials, hidden gunflash, override, ground and contact shadows."""
    scene = scene or bpy.context.scene
    for me in bpy.data.meshes:
        if "satk_look_addmat" in me:
            keep = int(me["satk_look_addmat"])
            try:
                if keep:
                    me.materials[0] = None
                else:
                    while len(me.materials):
                        me.materials.pop()
            except (RuntimeError, ReferenceError):
                pass
            del me["satk_look_addmat"]
    n = M.restore_all()
    unhid = 0
    for o in bpy.data.objects:
        if "satk_look_hid" in o:
            o.hide_render = bool(o["satk_look_hid"])
            del o["satk_look_hid"]
            unhid += 1
    try:
        bpy.context.view_layer.material_override = None
    except AttributeError:
        pass
    aux = Lt.remove_ground()
    if _KEY in scene:
        del scene[_KEY]
    return {"materials": n, "unhidden": unhid, "aux": aux}


def state(scene=None) -> dict | None:
    scene = scene or bpy.context.scene
    v = scene.get(_KEY)
    return json.loads(v) if isinstance(v, str) else None


# --------------------------------------------------------------------------- framing


def frame_points(objs, max_points: int = 120_000) -> list:
    """World points of the evaluated meshes (bounding-box corners beyond ``max_points``)."""
    import numpy as np

    dg = bpy.context.evaluated_depsgraph_get()
    pts: list = []
    budget = max_points
    for o in objs:
        ev = o.evaluated_get(dg)
        mw = np.array(ev.matrix_world, dtype=np.float64)
        try:
            me = ev.to_mesh()
        except RuntimeError:
            me = None
        try:
            n = len(me.vertices) if me is not None else 0
            if 0 < n <= budget:
                co = np.empty(n * 3, dtype=np.float64)
                me.vertices.foreach_get("co", co)
                w = co.reshape(n, 3) @ mw[:3, :3].T + mw[:3, 3]
                pts.append(w)
                budget -= n
            else:
                c = np.array([tuple(v) for v in ev.bound_box], dtype=np.float64)
                pts.append(c @ mw[:3, :3].T + mw[:3, 3])
        finally:
            if me is not None:
                ev.to_mesh_clear()
    return [p for p in pts if len(p)]


def footprints(objs) -> list:
    """``[(xmin, ymin, xmax, ymax, zmin)]`` per model group of ``objs``."""
    import numpy as np

    out = []
    for _k, g in sorted(model_groups(objs).items()):
        pts = frame_points(g, max_points=20_000)
        if not pts:
            continue
        P = np.concatenate(pts)
        lo, hi = P.min(axis=0), P.max(axis=0)
        out.append((float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1]), float(lo[2])))
    return out


def _view_dir(view) -> tuple[Vector, bool, float]:
    if isinstance(view, dict):
        az, el, ortho = float(view.get("az", 45.0)), float(view.get("el", 22.0)), bool(view.get("ortho", False))
    else:
        az, el, ortho = VIEWS[view]
    a, e = math.radians(az), math.radians(el)
    return Vector((math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e))), ortho, az


def _camera(scene):
    cam = bpy.data.objects.get("SATK_look_cam")
    if cam is None or cam.type != "CAMERA":
        cd = bpy.data.cameras.new("SATK_look_cam")
        cam = bpy.data.objects.new("SATK_look_cam", cd)
        cam["satk_look_aux"] = 1
        scene.collection.objects.link(cam)
    return cam


def place_camera(scene, view, pts, aspect: float = 1.0):
    """Frame the points ``pts`` (list of ``(n, 3)`` arrays) from ``view``; returns the camera."""
    import numpy as np

    d, ortho, _az = _view_dir(view)
    if isinstance(view, dict) and view.get("inside"):
        return _inside_camera(scene, view, d)
    if abs(d.z) > 0.999:
        up_hint = Vector((0.0, 1.0, 0.0))
    else:
        up_hint = Vector((0.0, 0.0, 1.0))
    fwd = -d
    right = fwd.cross(up_hint).normalized()
    up = right.cross(fwd).normalized()
    P = np.concatenate(pts) if pts else np.zeros((1, 3))
    R, U, F = (np.array(tuple(v)) for v in (right, up, fwd))
    xs, ys, zs = P @ R, P @ U, P @ F
    cx, cy, cz = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2, (zs.min() + zs.max()) / 2
    cam = _camera(scene)
    cd = cam.data
    cd.sensor_fit = "HORIZONTAL"
    depth = float(zs.max() - zs.min())
    if ortho:
        w = float(xs.max() - xs.min())
        h = float(ys.max() - ys.min())
        cd.type = "ORTHO"
        cd.ortho_scale = max(w, h * aspect, 0.05) / (1.0 - 2 * MARGIN)
        dist = depth + 10.0
        target = right * cx + up * cy + fwd * cz
    else:
        fov = float(view.get("fov") or FOV) if isinstance(view, dict) else FOV
        cd.type = "PERSP"
        cd.angle = math.radians(fov)
        th = math.tan(math.radians(fov) / 2) * (1.0 - 2 * MARGIN)
        tv = th / aspect
        tx, ty = cx, cy
        dist = 1.0
        for _ in range(4):
            x, y, z = np.abs(xs - tx), np.abs(ys - ty), zs - cz
            dist = float(max(0.5, (x / th - z).max(), (y / tv - z).max(), (0.1 - z).max()))
            dep = dist + (zs - cz)
            px, py = (xs - tx) / dep, (ys - ty) / dep
            tx += (px.min() + px.max()) / 2 * dist
            ty += (py.min() + py.max()) / 2 * dist
        target = right * tx + up * ty + fwd * cz
    cam.location = target + d * dist
    cam.rotation_euler = fwd.to_track_quat("-Z", "Y").to_euler() if abs(d.z) <= 0.999 else (0.0, 0.0, 0.0)
    cd.clip_start = max(0.01, dist - depth - 1.0) if ortho else max(0.02, dist * 0.02)
    cd.clip_end = dist + depth * 2 + 50.0
    scene.camera = cam
    return cam


def _inside_camera(scene, view: dict, d: Vector):
    """A camera standing at ``view["pos"]`` and looking along az/el (a room seen from inside)."""
    cam = _camera(scene)
    cd = cam.data
    cd.sensor_fit = "HORIZONTAL"
    cd.type = "PERSP"
    cd.angle = math.radians(float(view.get("fov") or 80.0))
    cam.location = Vector(view["pos"])
    fwd = d.normalized()
    cam.rotation_euler = fwd.to_track_quat("-Z", "Y").to_euler() if abs(fwd.z) <= 0.999 else         ((0.0, 0.0, 0.0) if fwd.z < 0 else (math.pi, 0.0, 0.0))
    cd.clip_start = 0.02
    cd.clip_end = 2000.0
    scene.camera = cam
    return cam


# --------------------------------------------------------------------------- rendering


def _engine(scene, look: str) -> None:
    r = scene.render
    if look == "raw":
        r.engine = "BLENDER_WORKBENCH"
        sh = scene.display.shading
        sh.light = "STUDIO"
        sh.color_type = "TEXTURE"
        sh.show_shadows = False
        sh.show_cavity = False
        sh.show_object_outline = False
        scene.display.render_aa = "8"
        scene.view_settings.view_transform = "Standard"
    else:
        for eng in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
            try:
                r.engine = eng
                break
            except TypeError:
                continue
        ee = scene.eevee
        for attr, val in (("taa_render_samples", 16), ("use_gtao", False), ("use_bloom", False),
                          ("use_shadows", False), ("use_raytracing", False)):
            if hasattr(ee, attr):
                try:
                    setattr(ee, attr, val)
                except (AttributeError, TypeError):
                    pass
        scene.view_settings.view_transform = "Raw"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    r.film_transparent = False
    r.resolution_percentage = 100
    r.use_file_extension = False
    if hasattr(r, "filter_size"):
        r.filter_size = 1.2
    r.dither_intensity = 0.0


def _write(scene, path: str, size: tuple[int, int], fmt: str, grade_env: dict | None) -> str:
    r = scene.render
    r.resolution_x, r.resolution_y = int(size[0]), int(size[1])
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    s = r.image_settings
    if grade_env is None:
        s.file_format = "JPEG" if fmt == "jpg" else "PNG"
        s.color_mode = "RGB"
        if fmt == "jpg":
            s.quality = 88
        else:
            s.color_depth = "8"
        r.filepath = path
        bpy.ops.render.render(write_still=True)
        return path
    tmp = path + ".raw.png"
    s.file_format = "PNG"
    s.color_mode = "RGB"
    s.color_depth = "8"
    r.filepath = tmp
    bpy.ops.render.render(write_still=True)
    _grade_file(tmp, path, grade_env, fmt)
    try:
        os.remove(tmp)
    except OSError:
        pass
    return path


def _grade_file(src: str, dst: str, e: dict, fmt: str) -> None:
    import numpy as np

    img = bpy.data.images.load(src, check_existing=False)
    try:
        w, h = img.size
        a = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(a)
    finally:
        bpy.data.images.remove(img)
    a = Lt.grade_image(a.reshape(h, w, 4), e)
    out = bpy.data.images.new("satk_look_out", w, h, alpha=False)
    try:
        out.pixels.foreach_set(a.ravel())
        out.filepath_raw = dst
        out.file_format = "JPEG" if fmt == "jpg" else "PNG"
        try:
            out.save(quality=88) if fmt == "jpg" else out.save()
        except TypeError:  # older builds: no quality argument
            out.save()
    finally:
        bpy.data.images.remove(out)


def render_views(scene=None, cams=STD_VIEWS, size=384, fmt: str = "jpg", *, out_dir: str | None = None,
                 prefix: str = "view", objects=None, extra_points=None) -> list[str]:
    """Render ``cams`` (view names, ``{az, el, ortho}`` dicts or camera object names) at ``size``
    (pixels, or ``(w, h)``) in the current look; returns the image paths (``jpg`` or ``png``)."""
    scene = scene or bpy.context.scene
    st = state(scene) or {"look": "raw"}
    look = st.get("look", "raw")
    fmt = "png" if str(fmt).lower() == "png" else "jpg"
    wh = (int(size), int(size)) if isinstance(size, (int, float)) else (int(size[0]), int(size[1]))
    out_dir = out_dir or os.path.join(bpy.app.tempdir or ".", "satk_look")
    objs = list(objects) if objects is not None else _visible(scene)
    pts = frame_points(objs) + list(extra_points or [])
    saved_cam = scene.camera
    _engine(scene, look)
    out = []
    try:
        for i, v in enumerate(cams):
            cam_obj = bpy.data.objects.get(v) if isinstance(v, str) and v not in VIEWS else None
            if cam_obj is not None and cam_obj.type == "CAMERA":
                scene.camera = cam_obj
            else:
                vp = v.get("points") if isinstance(v, dict) and v.get("points") is not None else pts
                place_camera(scene, v, vp, aspect=wh[0] / wh[1])
            name = v if isinstance(v, str) else str(v.get("label") or f"v{i}") if isinstance(v, dict) else f"v{i}"
            path = os.path.join(out_dir, f"{prefix}_{i:02d}_{name}.{fmt}")
            out.append(_write(scene, path, wh, fmt, st.get("env") if look == "game" else None).replace("\\", "/"))
    finally:
        scene.camera = saved_cam
    return out


def sheet(paths: list[str], cols: int = 2, *, out: str | None = None, fmt: str | None = None) -> str:
    """Tile equally sized images row-major into one image (default next to the first, ``sheet.<fmt>``)."""
    import numpy as np

    if not paths:
        raise ValueError("sheet: no images")
    arrs = []
    for p in paths:
        img = bpy.data.images.load(p, check_existing=False)
        try:
            w, h = img.size
            a = np.empty(w * h * 4, dtype=np.float32)
            img.pixels.foreach_get(a)
            arrs.append(a.reshape(h, w, 4))
        finally:
            bpy.data.images.remove(img)
    h, w = arrs[0].shape[:2]
    cols = max(1, min(int(cols), len(arrs)))
    rows = (len(arrs) + cols - 1) // cols
    canvas = np.zeros((rows * h, cols * w, 4), dtype=np.float32)
    canvas[..., 3] = 1.0
    for i, a in enumerate(arrs):
        r, c = divmod(i, cols)
        y0 = (rows - 1 - r) * h  # Blender rows start at the bottom
        if a.shape[:2] == (h, w):
            canvas[y0:y0 + h, c * w:(c + 1) * w] = a
    ext = (fmt or os.path.splitext(paths[0])[1].lstrip(".") or "jpg").lower()
    out = out or os.path.join(os.path.dirname(paths[0]), f"sheet.{ext}")
    img = bpy.data.images.new("satk_look_sheet", cols * w, rows * h, alpha=False)
    try:
        img.pixels.foreach_set(canvas.ravel())
        img.filepath_raw = out
        img.file_format = "JPEG" if ext in ("jpg", "jpeg") else "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)
    return out.replace("\\", "/")
