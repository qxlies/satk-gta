# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``look.silhouette`` and the reference plane of ``blender preview --ref``: a true-view photo against the model.

The model's silhouette is its triangles projected orthographically for the view (``side`` = seen from the left, the
front on the image's left like the preview's side view; ``front``; ``rear``) and filled into a mask; the photo's
silhouette and the comparison come from :mod:`satk.look.silhouette`. Wheels are put on every wheel dummy first, so
a session that holds one wheel compares like the game shows it.
"""

from __future__ import annotations

import os

import bpy

from satk.look import silhouette as S

__all__ = ["model_mask", "image_rgb", "ref_plane", "VIEW_AXES"]

#: view -> (image u = sign * axis, the depth axis): side from -X (u = -y), front from +Y (u = -x), rear (u = x).
VIEW_AXES = {"side": (1, -1.0, 0), "front": (0, -1.0, 1), "rear": (0, 1.0, 1)}


def _tris(objs):
    """World triangles ``(n, 3, 3)`` of the evaluated meshes ``objs``."""
    import numpy as np

    dg = bpy.context.evaluated_depsgraph_get()
    out = []
    for o in objs:
        ev = o.evaluated_get(dg)
        try:
            me = ev.to_mesh()
        except RuntimeError:
            continue
        try:
            me.calc_loop_triangles()
            n = len(me.loop_triangles)
            if not n:
                continue
            co = np.empty(len(me.vertices) * 3)
            me.vertices.foreach_get("co", co)
            co = co.reshape(-1, 3)
            mw = np.array(ev.matrix_world)
            co = co @ mw[:3, :3].T + mw[:3, 3]
            tri = np.empty(n * 3, dtype=np.int64)
            me.loop_triangles.foreach_get("vertices", tri)
            out.append(co[tri.reshape(n, 3)])
        finally:
            ev.to_mesh_clear()
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def model_mask(objs, view: str, res: int = 480):
    """``(mask, (length_m, height_m))``: the filled silhouette of ``objs`` for ``view`` (rows top-down), ``res``
    pixels along the longer side."""
    import numpy as np

    ax, sign, _depth = VIEW_AXES[view]
    T = _tris(objs)
    if not len(T):
        raise ValueError("look.silhouette: no geometry to project")
    U = sign * T[:, :, ax]
    V = T[:, :, 2]
    u0, u1, v0, v1 = float(U.min()), float(U.max()), float(V.min()), float(V.max())
    span = max(u1 - u0, v1 - v0, 1e-6)
    px = span / res
    w, h = max(1, int(round((u1 - u0) / px))), max(1, int(round((v1 - v0) / px)))
    X = (U - u0) / px
    Y = (v1 - V) / px
    mask = np.zeros((h, w), dtype=bool)
    for (xa, xb, xc), (ya, yb, yc) in zip(X, Y):
        x_lo, x_hi = int(max(0, np.floor(min(xa, xb, xc)))), int(min(w - 1, np.ceil(max(xa, xb, xc))))
        y_lo, y_hi = int(max(0, np.floor(min(ya, yb, yc)))), int(min(h - 1, np.ceil(max(ya, yb, yc))))
        if x_hi < x_lo or y_hi < y_lo:
            continue
        gx, gy = np.meshgrid(np.arange(x_lo, x_hi + 1) + 0.5, np.arange(y_lo, y_hi + 1) + 0.5)
        d = (yb - yc) * (xa - xc) + (xc - xb) * (ya - yc)
        if abs(d) < 1e-12:
            continue
        l1 = ((yb - yc) * (gx - xc) + (xc - xb) * (gy - yc)) / d
        l2 = ((yc - ya) * (gx - xc) + (xa - xc) * (gy - yc)) / d
        inside = (l1 >= -1e-6) & (l2 >= -1e-6) & (1 - l1 - l2 >= -1e-6)
        mask[y_lo:y_hi + 1, x_lo:x_hi + 1] |= inside
    return mask, (u1 - u0, v1 - v0)


def image_rgb(path: str):
    """An image file as ``(h, w, 3)`` uint8, rows top-down."""
    import numpy as np

    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        a = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(a)
    finally:
        bpy.data.images.remove(img)
    a = a.reshape(h, w, 4)[::-1, :, :3]
    return np.clip(np.rint(a * 255.0), 0, 255).astype(np.uint8)


def save_overlay(model, photo, path: str) -> str:
    """The model (red) over the photo silhouette (blue), both aligned (``model`` and ``photo`` masks of one size)."""
    import numpy as np

    h, w = model.shape
    rgb = np.full((h, w, 4), 1.0, dtype=np.float32)
    rgb[photo] = (0.35, 0.55, 1.0, 1.0)
    rgb[model] = (1.0, 0.35, 0.3, 1.0)
    rgb[model & photo] = (0.55, 0.25, 0.6, 1.0)
    img = bpy.data.images.new("satk_silhouette", w, h, alpha=False)
    try:
        img.pixels.foreach_set(rgb[::-1].ravel())
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
    finally:
        bpy.data.images.remove(img)
    return path.replace("\\", "/")


def ref_plane(scene, objs, ref: dict):
    """A photo plane behind ``objs`` for the view ``ref["view"]``: the object's box in the photo (``ref["box"]`` px)
    spans the model's length (or width) and stands on the model's lowest point; drawn at ``ref["alpha"]`` (30 %).
    Returns the plane object (tagged ``satk_ref``; hidden in other views by the caller)."""
    import numpy as np
    from mathutils import Vector

    view = ref.get("view") or "side"
    ax, sign, depth = VIEW_AXES[view]
    T = _tris(objs)
    if not len(T):
        return None
    P = T.reshape(-1, 3)
    lo, hi = P.min(axis=0), P.max(axis=0)
    img = bpy.data.images.load(str(ref["image"]), check_existing=True)
    iw, ih = img.size
    x0, y0, x1, y1 = ref.get("box") or (0, 0, iw, ih)
    if ref.get("flip"):
        x0, x1 = iw - x1, iw - x0
    s = float(hi[ax] - lo[ax]) / max(1.0, x1 - x0)           # metres per photo pixel
    # image u grows along sign * axis; the box's left edge sits on the model's min of u
    u_min = float(min(sign * lo[ax], sign * hi[ax]))
    z_ground = float(lo[2])
    cam_side = {"side": -1.0, "front": 1.0, "rear": -1.0}[view]   # the camera's side along the depth axis
    far = (float(hi[depth]) + 0.6) if cam_side < 0 else (float(lo[depth]) - 0.6)

    def at(px: float, py: float) -> Vector:
        u = u_min + (px - x0) * s
        z = z_ground + (y1 - py) * s
        p = [0.0, 0.0, z]
        p[ax] = u * sign
        p[depth] = far
        return Vector(p)

    me = bpy.data.meshes.new("SATK_look_ref")
    corners = [at(0, ih), at(iw, ih), at(iw, 0), at(0, 0)]
    me.from_pydata(corners, [], [(0, 1, 2, 3)])
    uv = me.uv_layers.new(name="UVMap")
    us = [(0, 0), (1, 0), (1, 1), (0, 1)]
    if ref.get("flip"):
        us = [(1 - a, b) for a, b in us]
    for li, c in enumerate(us):
        uv.data[li].uv = c
    mat = bpy.data.materials.new("SATK_look_ref")
    nt = mat.node_tree if mat.node_tree is not None else None
    if nt is None:
        mat.use_nodes = True
        nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    em = nt.nodes.new("ShaderNodeEmission")
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.inputs[0].default_value = float(ref.get("alpha", 0.3))
    nt.links.new(tex.outputs["Color"], em.inputs["Color"])
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(em.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
        try:
            setattr(mat, attr, val)
        except (AttributeError, TypeError):
            pass
    me.materials.append(mat)
    ob = bpy.data.objects.new("SATK_look_ref", me)
    scene.collection.objects.link(ob)
    ob["satk_ref"] = 1             # not satk_look_aux: a look's restore removes those
    ob["satk_look_refplane"] = 1
    return ob


def remove_ref(ob) -> None:
    if ob is None:
        return
    try:
        me, mats = ob.data, list(ob.data.materials)
        img = None
        for m in mats:
            if m is not None and m.node_tree is not None:
                img = next((n.image for n in m.node_tree.nodes if n.type == "TEX_IMAGE"), img)
        bpy.data.objects.remove(ob, do_unlink=True)
        bpy.data.meshes.remove(me)
        for m in mats:
            if m is not None and m.users == 0:
                bpy.data.materials.remove(m)
        if img is not None and img.users == 0:
            bpy.data.images.remove(img)
    except ReferenceError:
        pass


def compare_photo(objs, ref_path: str, view: str, *, res: int = 480, stations: int = 20, flip: bool = False,
                  box=None, out_dir: str | None = None) -> dict:
    """``look.silhouette``: the model's silhouette against the photo's (see :func:`satk.look.silhouette.compare`)."""
    import numpy as np

    mask, (length, height) = model_mask(objs, view, res)
    rgb = image_rgb(ref_path)
    pm = S.photo_mask(rgb)
    rep = S.compare(mask, pm, length_m=length, height_m=height, stations=stations, photo_box_px=box, flip=flip)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        mb = S.mask_box(mask, 0.0)
        M = mask[mb[1]:mb[3], mb[0]:mb[2]]
        P = S._crop_resize(pm[:, ::-1] if flip else pm, rep["box_px"], M.shape[1], M.shape[0])  # noqa: SLF001
        rep["overlay"] = save_overlay(M, P, os.path.join(out_dir, "overlay.png"))
    rep["dims_m"] = [round(length, 3), round(height, 3)]
    del np
    return rep
