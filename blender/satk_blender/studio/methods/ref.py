# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``ref.*``: reference photos as image planes behind the model, with a matched orthographic camera.

Prepare a photo with ``satk ref import`` first (at most 1,600 px, no EXIF, a pixel grid sheet to read
coordinates from). Scale comes from ``width`` (metres the full image width spans) or from two pixel
points ``points`` and their real ``distance`` in metres (wheel centres = wheelbase). ``origin_px`` is the
pixel that lies on the model origin (default: the image centre). The plane is placed behind the model
for its view (``front``: the photo looks at the front, camera on +Y; ``left``: camera on -X; ...).

Snapshots taken from the matched camera ``ref_cam_<view>`` (or the named view) show the photo with the
model drawn over it (``ref_alpha``). Planes are tagged ``satk_ref`` and are never counted in stats.
"""

from __future__ import annotations

import os

import bpy
from mathutils import Vector

from satk.core.errors import SatkError
from satk.studio.core import readonly

from .. import snapshot as S
from ..stats import geometry_objects
from . import _util as U

#: view -> (right axis of the image, up axis of the image, direction toward the camera)
FRAMES = {
    "front": (Vector((-1, 0, 0)), Vector((0, 0, 1)), Vector((0, 1, 0))),
    "rear": (Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, -1, 0))),
    "left": (Vector((0, -1, 0)), Vector((0, 0, 1)), Vector((-1, 0, 0))),
    "right": (Vector((0, 1, 0)), Vector((0, 0, 1)), Vector((1, 0, 0))),
    "top": (Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))),
}
COLLECTION = "satk_refs"


def _material(img, name: str):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    tex = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None) or nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        if "Emission Color" in bsdf.inputs:
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
            bsdf.inputs["Emission Strength"].default_value = 1.0
    nt.nodes.active = tex
    return mat


def _scene_extent(view: str) -> float:
    objs = [o for o in geometry_objects() if not o.get("satk_ref")]
    if not objs:
        return 3.0
    lo, hi = S.bounds(objs)
    away = FRAMES[view][2]
    # distance behind the origin along -camera direction that clears the model
    far = max(-(lo.dot(away)), -(hi.dot(away)), 0.0)
    for x in (lo.x, hi.x):
        for y in (lo.y, hi.y):
            for z in (lo.z, hi.z):
                far = max(far, -Vector((x, y, z)).dot(away))
    return far + 0.25


def plane(ctx, p: dict) -> dict:
    """Put a photo behind the model for a view (front|rear|left|right|top): image, width m or points+distance, origin_px."""
    M = "ref.plane"
    view = U.text(p, "view", M, required=True, choices=tuple(FRAMES))
    path = os.path.abspath(U.text(p, "image", M, required=True))
    if not os.path.isfile(path):
        raise SatkError("NOT_FOUND", f"{M}: no image {path.replace(os.sep, '/')}", hint="satk ref import <photo>")
    img = bpy.data.images.load(path, check_existing=True)
    w, h = img.size
    if not w or not h:
        raise U.bad(M, f"cannot read {path.replace(os.sep, '/')} as an image")
    if p.get("width") is not None:
        s = U.num(p, "width", M, lo=0.01) / w
    elif p.get("points") is not None:
        pts = U.points2(p, "points", M, min_n=2, max_n=2)
        dpx = (Vector(pts[1]) - Vector(pts[0])).length
        if dpx < 1:
            raise U.bad(M, "the two points are the same pixel")
        s = U.num(p, "distance", M, lo=0.001, required=True) / dpx
    else:
        raise U.bad(M, "give 'width' (m) or 'points' [[x1, y1], [x2, y2]] (px) and 'distance' (m)")
    ox, oy = U.vec(p, "origin_px", M, [w / 2, h / 2], n=2)
    right, up, toward = FRAMES[view]
    off = U.num(p, "offset", M) if p.get("offset") is not None else _scene_extent(view)
    back = -toward * off

    def at(px: float, py: float) -> Vector:
        return right * ((px - ox) * s) + up * ((oy - py) * s) + back

    name = U.text(p, "name", M, f"ref_{view}")
    old = bpy.data.objects.get(name)
    if old is not None:
        if not old.get("satk_ref"):
            raise SatkError("EXISTS", f"{M}: an object {name!r} exists and is not a reference plane")
        bpy.data.objects.remove(old, do_unlink=True)
    me = bpy.data.meshes.new(name)
    me.from_pydata([at(0, h), at(w, h), at(w, 0), at(0, 0)], [], [(0, 1, 2, 3)])
    uv = me.uv_layers.new(name="UVMap")
    for li, co in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
        uv.data[li].uv = co
    me.materials.append(_material(img, f"{name}_mat"))
    ob = bpy.data.objects.new(name, me)
    ctx.collection(COLLECTION).objects.link(ob)
    ob["satk_ref"] = 1
    ob["satk_ref_view"] = view
    ob["satk_ref_alpha"] = U.num(p, "alpha", M, 0.6, lo=0.0, hi=1.0)
    out: dict = {"object": ob.name, "view": view, "size_m": [round(w * s, 3), round(h * s, 3)],
                 "m_per_px": round(s, 5), "image": img.name}
    if U.flag(p, "camera", M, True):
        cam_name = f"ref_cam_{view}" if name == f"ref_{view}" else f"{name}_cam"
        cam = bpy.data.objects.get(cam_name)
        if cam is None:
            cam = bpy.data.objects.new(cam_name, bpy.data.cameras.new(cam_name))
            ctx.collection(COLLECTION).objects.link(cam)
        centre = at(w / 2, h / 2) - back
        dist = off + 50.0
        cam.location = centre + toward * dist
        if view == "top":
            cam.rotation_euler = (0.0, 0.0, 0.0)  # looks down -Z, image up = +Y
        else:
            cam.rotation_euler = (-toward).to_track_quat("-Z", "Y").to_euler()
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = max(w, h) * s
        cam.data.clip_start = 0.1
        cam.data.clip_end = dist + off + 10.0
        cam["satk_view"] = view
        cam["satk_ref_view"] = view
        try:
            cam.data.show_background_images = True
            bg = cam.data.background_images[0] if cam.data.background_images else cam.data.background_images.new()
            bg.image = img
            bg.alpha = 0.5
        except (AttributeError, RuntimeError):
            pass
        out["camera"] = cam.name
    out["changed"] = [ob.name]
    return out


@readonly
def list_(ctx, p: dict) -> dict:
    """Reference planes: name, view, image, size in metres."""
    rows = []
    for o in sorted((o for o in ctx.scene.objects if o.get("satk_ref")), key=lambda o: o.name):
        row = {"name": o.name, "view": o.get("satk_ref_view", ""), "dims": [round(x, 3) for x in o.dimensions]}
        mat = o.active_material
        tex = next((n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE"), None) if mat and mat.node_tree else None
        if tex is not None and tex.image is not None:
            row["image"] = tex.image.name
        rows.append(row)
    return {"refs": rows}


METHODS = {"ref.plane": plane, "ref.list": list_}
