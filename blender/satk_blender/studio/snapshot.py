# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Small Workbench snapshots of the scene (JPEG, at most 1024 px) for the agent to look at.

Views (GTA axes: the front of a vehicle faces +Y): ``front`` (camera on +Y), ``rear``, ``left`` (camera
on -X), ``right``, ``top`` (orthographic) and ``3q`` (front-right, 25 deg above, perspective). The name of
a camera object in the scene works too. ``views`` (2-4 of them) makes a 2x2 sheet of half-size tiles.

Looks: ``clay`` (grey studio light with a thin wire overlay of the edges: the density is visible),
``raw`` (material colours and image textures), ``wire`` (edges only, x-ray) and ``game`` (the SA game
look of ``satk_blender.look.api``: EEVEE, alpha glass, white lamps, dirt 2, noon light, a ground; what the
game draws up close, so ghosts, reference planes, damage parts, low LODs and collision are left out; about
0.3-1 s). Ghosts (``satk_ghost``) are drawn as a blue wire ``overlay``,
``lineup`` beside the model, or ``hide``. Reference planes (``satk_ref``) of the shot's view are drawn
behind the model, which is blended over them with ``ref_alpha``. Render settings, object colours and
visibility are restored afterwards; temporary objects are deleted.
"""

from __future__ import annotations

import math
import os
from contextlib import contextmanager

import bpy
from mathutils import Euler, Vector

from .stats import geometry_objects

__all__ = ["VIEWS", "LOOKS", "bounds", "frame", "take", "warm_up"]

#: view -> (azimuth from +Y toward +X in degrees, elevation in degrees, orthographic)
VIEWS = {"3q": (45.0, 25.0, False), "front": (0.0, 0.0, True), "rear": (180.0, 0.0, True),
         "left": (-90.0, 0.0, True), "right": (90.0, 0.0, True), "top": (0.0, 90.0, True)}
LOOKS = ("clay", "raw", "wire", "game")
FOV_DEG = 40.0
MARGIN = 1.08
CLAY = (0.72, 0.72, 0.72, 1.0)
WIRE = (0.08, 0.08, 0.09, 1.0)
GHOST_WIRE = (0.15, 0.45, 0.95, 1.0)
GHOST_SOLID = (0.55, 0.68, 0.85, 1.0)
WIRE_PX = 0.9  # wire overlay thickness in pixels
QUALITY = 82


def bounds(objs) -> tuple[Vector, Vector]:
    dg = bpy.context.evaluated_depsgraph_get()
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for o in objs:
        ev = o.evaluated_get(dg)
        for c in ev.bound_box:
            w = ev.matrix_world @ Vector(c)
            for i in range(3):
                lo[i] = min(lo[i], w[i])
                hi[i] = max(hi[i], w[i])
    if lo.x == math.inf:
        return Vector((-1.0, -1.0, -1.0)), Vector((1.0, 1.0, 1.0))
    return lo, hi


def frame(cam, view: str, lo: Vector, hi: Vector, margin: float = MARGIN) -> float:
    """Place ``cam`` for ``view`` around the box; returns the framed width in metres (for the wire size)."""
    az, el, ortho = VIEWS[view]
    center = (lo + hi) / 2
    ext = hi - lo
    radius = max(ext.length / 2, 0.05)
    a, e = math.radians(az), math.radians(el)
    d = Vector((math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e)))
    if ortho:
        cam.data.type = "ORTHO"
        if view == "top":
            w, h = ext.x, ext.y
        elif view in ("front", "rear"):
            w, h = ext.x, ext.z
        else:
            w, h = ext.y, ext.z
        cam.data.ortho_scale = max(w, h, 0.05) * margin
        dist = radius * 3 + 1.0
        width = cam.data.ortho_scale
    else:
        cam.data.type = "PERSP"
        cam.data.angle = math.radians(FOV_DEG)
        dist = radius / math.sin(math.radians(FOV_DEG) / 2) * margin
        width = 2 * dist * math.tan(math.radians(FOV_DEG) / 2)
    cam.location = center + d * dist
    if view == "top":
        cam.rotation_euler = Euler((0.0, 0.0, 0.0))
    else:
        cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    cam.data.clip_start = max(dist - radius * 2, dist * 0.01, 0.001)
    cam.data.clip_end = dist + radius * 4 + 1.0
    return width


def _cam_width(cam, objs) -> float:
    if cam.data.type == "ORTHO":
        return cam.data.ortho_scale
    lo, hi = bounds(objs) if objs else (Vector((-1, -1, -1)), Vector((1, 1, 1)))
    d = ((lo + hi) / 2 - cam.matrix_world.translation).length
    return 2 * d * math.tan(cam.data.angle / 2)


_RENDER = ("engine", "resolution_x", "resolution_y", "resolution_percentage", "filepath", "film_transparent",
           "use_file_extension")
_IMAGE = ("file_format", "quality", "color_mode")
_SHADING = ("light", "color_type", "single_color", "show_object_outline", "show_cavity", "background_type")


def warm_up() -> None:
    """Render one 16 px Workbench frame per colour mode (no file): the first real snapshot then takes ms."""
    scene = bpy.context.scene
    r = scene.render
    saved = (r.engine, r.resolution_x, r.resolution_y, r.resolution_percentage, scene.camera,
             scene.display.shading.color_type)
    data = bpy.data.cameras.new("satk_warmup")
    cam = bpy.data.objects.new("satk_warmup", data)
    scene.collection.objects.link(cam)
    try:
        scene.camera = cam
        r.engine = "BLENDER_WORKBENCH"
        r.resolution_x = r.resolution_y = 16
        r.resolution_percentage = 100
        for ct in ("OBJECT", "TEXTURE"):
            scene.display.shading.color_type = ct
            bpy.ops.render.render(write_still=False)
    finally:
        (r.engine, r.resolution_x, r.resolution_y, r.resolution_percentage, scene.camera,
         scene.display.shading.color_type) = saved
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.cameras.remove(data)


@contextmanager
def _kept(scene):
    """Restore render/shading settings, the camera, object visibility and colours afterwards."""
    r, img, sh = scene.render, scene.render.image_settings, scene.display.shading
    saved = ({k: getattr(r, k) for k in _RENDER}, {k: getattr(img, k) for k in _IMAGE},
             {k: (tuple(v) if k == "single_color" else v) for k, v in ((k, getattr(sh, k)) for k in _SHADING)},
             scene.camera, scene.view_settings.view_transform)
    objs = list(scene.objects)
    vis = {o.name: (o.hide_render, tuple(o.color), o.matrix_world.copy()) for o in objs}
    temp: list = []
    try:
        yield temp
    finally:
        for o in temp:
            try:
                bpy.data.objects.remove(o, do_unlink=True)
            except (ReferenceError, RuntimeError):
                pass
        for o in objs:
            try:
                hr, col, _mw = vis[o.name]
                o.hide_render = hr
                o.color = col
            except (ReferenceError, KeyError):
                pass
        for k, v in saved[0].items():
            setattr(r, k, v)
        for k, v in saved[1].items():
            setattr(img, k, v)
        for k, v in saved[2].items():
            setattr(sh, k, v)
        scene.camera = saved[3]
        scene.view_settings.view_transform = saved[4]


def _wire_copy(o, thickness: float, color, temp: list):
    w = o.copy()
    bpy.context.scene.collection.objects.link(w)
    w.parent = o.parent
    w.matrix_world = o.matrix_world
    m = w.modifiers.new("satk_wire", "WIREFRAME")
    m.thickness = thickness
    m.use_replace = True
    m.use_even_offset = False
    m.use_relative_offset = False
    w.color = color
    w.hide_render = False
    temp.append(w)
    return w


def _render(scene, path: str, size_xy: tuple[int, int], *, fmt: str = "JPEG", transparent: bool = False) -> None:
    r, img = scene.render, scene.render.image_settings
    r.engine = "BLENDER_WORKBENCH"
    r.resolution_x, r.resolution_y = size_xy
    r.resolution_percentage = 100
    r.film_transparent = transparent
    r.use_file_extension = False
    img.file_format = fmt
    img.color_mode = "RGBA" if fmt != "JPEG" else "RGB"
    if fmt == "JPEG":
        img.quality = QUALITY
    r.filepath = path
    bpy.ops.render.render(write_still=True)


def _read(path: str):
    """``(h, w, 4)`` float array of an image file (display values, top row first); the file is removed."""
    import numpy as np

    im = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = im.size
        px = np.empty(w * h * 4, dtype=np.float32)
        im.pixels.foreach_get(px)
    finally:
        bpy.data.images.remove(im)
        try:
            os.remove(path)
        except OSError:
            pass
    return px.reshape(h, w, 4)[::-1]


def _write_jpeg(arr, path: str) -> None:
    import numpy as np

    h, w = arr.shape[:2]
    rgba = np.ones((h, w, 4), dtype=np.float32)
    rgba[..., :3] = arr[..., :3]
    im = bpy.data.images.new("satk_snapshot_out", w, h, alpha=False)
    try:
        im.pixels.foreach_set(rgba[::-1].ravel())
        im.filepath_raw = path
        im.file_format = "JPEG"
        try:
            im.save(filepath=path, quality=QUALITY)
        except TypeError:  # older signature
            im.save()
    finally:
        bpy.data.images.remove(im)


class _Shot:
    """One tile: which objects, camera, look; renders to a file or an array."""

    def __init__(self, scene, view: str, look: str, objects, wire: bool, refs: bool, ref_alpha, ghost: str,
                 warn: list | None):
        self.scene, self.view, self.look, self.wire = scene, view, look, wire
        self.refs_on, self.ref_alpha, self.ghost, self.warn = refs, ref_alpha, ghost, warn
        cam_obj = bpy.data.objects.get(view)
        if cam_obj is not None and cam_obj.type != "CAMERA":
            cam_obj = None
        if cam_obj is None and view not in VIEWS:
            from satk.core.errors import SatkError

            raise SatkError("BAD_PARAMS", f"unknown view {view!r}",
                            data={"views": sorted(VIEWS), "cameras": sorted(o.name for o in scene.objects
                                                                           if o.type == "CAMERA")[:10]})
        self.cam_obj = cam_obj
        geo = [o for o in geometry_objects(scene) if not o.get("satk_ref") and not o.hide_render]
        self.model = geo
        if objects:
            self.frame_objs = [bpy.data.objects[n] for n in objects if n in bpy.data.objects]
            missing = [n for n in objects if n not in bpy.data.objects]
            if missing and warn is not None:
                warn.append(f"NOT_FOUND: snapshot: no object(s) {missing[:5]}")
        else:
            self.frame_objs = list(geo)
        self.ghosts = [o for o in scene.objects if o.get("satk_ghost") and o.type in ("MESH", "CURVE")
                       and not o.hide_render]
        ref_view = cam_obj.get("satk_ref_view") if cam_obj is not None else (view if view in VIEWS else None)
        self.refs = [o for o in scene.objects if o.get("satk_ref") and o.type == "MESH" and ref_view
                     and o.get("satk_ref_view") == ref_view and refs]
        if cam_obj is None and not self.frame_objs and not self.ghosts:
            from satk.core.errors import SatkError

            raise SatkError("NOT_FOUND", "nothing to frame: the scene has no visible geometry")

    def _prepare(self, temp: list, tile: int):
        scene = self.scene
        for o in scene.objects:  # everything off; the parts of this shot are switched on below
            if o.type in ("MESH", "CURVE", "SURFACE", "FONT", "META"):
                o.hide_render = True
        model = [o for o in self.model if not o.get("satk_ghost")]
        ghosts = self.ghosts if self.ghost != "hide" else []
        if self.ghost == "lineup" and ghosts and model:
            self._lineup(model, ghosts)
        cam = self.cam_obj
        if cam is None:
            data = bpy.data.cameras.new("satk_snapshot")
            cam = bpy.data.objects.new("satk_snapshot", data)
            scene.collection.objects.link(cam)
            temp.append(cam)
            framed = self.frame_objs or model
            if self.ghost in ("overlay", "lineup"):
                framed = framed + ghosts
            lo, hi = bounds(framed)
            width = frame(cam, self.view, lo, hi)
        else:
            width = _cam_width(cam, self.frame_objs or model)
        scene.camera = cam
        thick = max(width / max(tile, 16) * WIRE_PX, 1e-5)
        look = self.look
        for o in model:
            o.hide_render = look == "wire"
            o.color = CLAY
        if look in ("clay", "wire") and (self.wire or look == "wire"):
            for o in model:
                if o.type == "MESH":
                    _wire_copy(o, thick, WIRE, temp)
        for g in ghosts:
            if self.ghost == "lineup":
                g.hide_render = False
                g.color = GHOST_SOLID
            elif g.type == "MESH":
                _wire_copy(g, thick, GHOST_WIRE, temp)
        sh = scene.display.shading
        sh.light = "STUDIO"
        sh.color_type = "OBJECT" if look in ("clay", "wire") else "TEXTURE"
        sh.show_object_outline = look != "wire"
        sh.show_cavity = False
        sh.background_type = "WORLD"
        scene.view_settings.view_transform = "Standard"

    def _lineup(self, model, ghosts) -> None:
        ax = 1 if self.view in ("left", "right") else 0
        mlo, mhi = bounds(model)
        glo, ghi = bounds(ghosts)
        gap = 0.15 * max((mhi - mlo)[ax], (ghi - glo)[ax], 0.1)
        shift = mhi[ax] + gap - glo[ax]
        if ax == 1:  # ghosts go behind the model's rear so both read left-to-right in a side view
            shift = mlo[ax] - gap - ghi[ax]
        moved = set()
        for g in ghosts:
            root = g
            while root.parent is not None and root.parent.get("satk_ghost"):
                root = root.parent
            if root.name in moved:
                continue
            moved.add(root.name)
            loc = root.location.copy()
            loc[ax] += shift
            root.location = loc
        bpy.context.view_layer.update()

    def render(self, path: str, tile: int, *, as_array: bool):
        """Render the tile; returns an array (``as_array``) or writes the JPEG ``path``."""
        scene = self.scene
        ghost_locs = {o.name: o.location.copy() for o in self.ghosts}
        with _kept(scene) as temp:
            try:
                self._prepare(temp, tile)
                if not self.refs:
                    if as_array:
                        tmp = path + ".tga"
                        _render(scene, tmp, (tile, tile), fmt="TARGA_RAW")
                        return _read(tmp)
                    _render(scene, path, (tile, tile))
                    return None
                tmp_a, tmp_b = path + ".a.tga", path + ".b.tga"
                _render(scene, tmp_a, (tile, tile), fmt="TARGA_RAW", transparent=True)
                shown = {o.name: o.hide_render for o in scene.objects}
                for o in scene.objects:
                    o.hide_render = o not in self.refs
                sh = scene.display.shading
                sh.color_type, sh.light, sh.show_object_outline = "TEXTURE", "FLAT", False
                _render(scene, tmp_b, (tile, tile), fmt="TARGA_RAW")
                for o in scene.objects:
                    if o.name in shown:
                        o.hide_render = shown[o.name]
                a, b = _read(tmp_a), _read(tmp_b)
                alpha = self.ref_alpha if self.ref_alpha is not None else float(self.refs[0].get("satk_ref_alpha", 0.6))
                k = a[..., 3:4] * alpha
                out = b.copy()
                out[..., :3] = b[..., :3] * (1 - k) + a[..., :3] * k
                if as_array:
                    return out
                _write_jpeg(out, path)
                return None
            finally:
                for o in self.ghosts:
                    if o.name in ghost_locs:
                        o.location = ghost_locs[o.name]


def take(path: str, *, view: str = "3q", size: int = 512, look: str = "clay", objects: list[str] | None = None,
         warn: list[str] | None = None, wire: bool | None = None, refs: bool = True, ref_alpha: float | None = None,
         ghost: str = "overlay", views: list[str] | None = None) -> str:
    """Render ``path`` (JPEG) and return it. ``views`` (2-4) makes a 2x2 sheet of ``size/2`` tiles."""
    from satk.core.errors import SatkError

    scene = bpy.context.scene
    if look not in LOOKS:
        raise SatkError("BAD_PARAMS", f"unknown look {look!r}", data={"looks": list(LOOKS)})
    if ghost not in ("overlay", "lineup", "hide"):
        raise SatkError("BAD_PARAMS", f"ghost must be overlay, lineup or hide, got {ghost!r}")
    wire = (look == "clay") if wire is None else bool(wire)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if look == "game":
        return _game(path, views=views or [view], size=int(size), objects=objects, warn=warn)
    if views:
        if not 2 <= len(views) <= 4:
            raise SatkError("BAD_PARAMS", "views: give 2-4 views for a sheet")
        import numpy as np

        tile = max(64, int(size) // 2)
        tiles = [_Shot(scene, v, look, objects, wire, refs, ref_alpha, ghost, warn).render(path, tile, as_array=True)
                 for v in views]
        rows = (len(tiles) + 1) // 2
        sheet = np.ones((rows * tile, 2 * tile, 4), dtype=np.float32) * 0.25
        for i, t in enumerate(tiles):
            y, x = (i // 2) * tile, (i % 2) * tile
            sheet[y:y + tile, x:x + tile] = t
            sheet[y:y + tile, x:x + 1, :3] = 0.1
            sheet[y:y + 1, x:x + tile, :3] = 0.1
        _write_jpeg(sheet, path)
    else:
        _Shot(scene, view, look, objects, wire, refs, ref_alpha, ghost, warn).render(path, int(size), as_array=False)
    if not os.path.isfile(path):
        raise SatkError("INTERNAL", "the snapshot was not written", data={"path": path.replace("\\", "/")})
    return os.path.abspath(path).replace("\\", "/")


def _game(path: str, *, views: list[str], size: int, objects: list[str] | None, warn: list | None) -> str:
    """A snapshot in the SA game look (``satk_blender.look.api``); 2-4 views make a 2x2 sheet."""
    from satk.core.errors import SatkError

    try:
        from satk_blender.look import api as look_api
        from satk_blender.look import preview as look_preview
    except Exception as e:  # noqa: BLE001 - the look plug-in is part of satk_blender; say why it is missing
        raise SatkError("UNSUPPORTED", f"look 'game' needs satk_blender.look: {type(e).__name__}: {e}"[:200]) from None
    if len(views) > 4:
        raise SatkError("BAD_PARAMS", "views: give 2-4 views for a sheet")
    scene = bpy.context.scene
    for v in views:
        cam = bpy.data.objects.get(v)
        if v not in VIEWS and (cam is None or cam.type != "CAMERA"):
            raise SatkError("BAD_PARAMS", f"unknown view {v!r}", data={"views": sorted(VIEWS)})
    tile = size if len(views) == 1 else max(64, size // 2)
    tmp_dir = os.path.join(os.path.dirname(path), "_game")
    out: list[str] = []
    with _kept(scene):
        hidden = [o for o in scene.objects if (o.get("satk_ghost") or o.get("satk_ref")) and not o.hide_render]
        for o in hidden:
            o.hide_render = True
        # what the game draws up close: no damage parts, low LODs or collision (preview state "ok")
        for o in geometry_objects(scene):
            if o.type == "MESH" and look_preview.default_hidden(o):
                o.hide_render = True
        frame_objs = [bpy.data.objects[n] for n in objects or () if n in bpy.data.objects] or None
        if objects and frame_objs is None and warn is not None:
            warn.append(f"NOT_FOUND: snapshot: no object(s) {list(objects)[:5]}")
        model = [o for o in geometry_objects(scene) if not o.get("satk_ref") and not o.hide_render]
        if not model:
            raise SatkError("NOT_FOUND", "nothing to frame: the scene has no visible geometry")
        try:
            look_api.apply(scene, "game", objects=[o for o in model if o.type == "MESH"])
            out = look_api.render_views(scene, views, tile, fmt="jpg", out_dir=tmp_dir, prefix="snap",
                                        objects=[o for o in (frame_objs or model) if o.type == "MESH"] or None)
        finally:
            look_api.restore(scene)
    try:
        if len(out) == 1:
            os.replace(out[0], path)
        else:
            look_api.sheet(out, cols=2, out=path)
    finally:
        for f in out:
            try:
                os.remove(f)
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
    if not os.path.isfile(path):
        raise SatkError("INTERNAL", "the snapshot was not written", data={"path": path.replace("\\", "/")})
    return os.path.abspath(path).replace("\\", "/")
