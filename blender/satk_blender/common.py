# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Shared helpers: DragonFF loading, faster TXD images, scene hygiene, statistics."""

from __future__ import annotations

import os
import re
import sys

import bpy

_DFF = None  # the imported dragonff package

#: Image names Blender creates by itself.
_SYSTEM_IMAGES = {"Render Result", "Viewer Node"}
#: Objects that are vehicle damage/low-detail variants (hidden like the game does).
_DAM_RE = re.compile(r"_(dam|vlo)$", re.IGNORECASE)


class CIDict(dict):
    """Case-insensitive texture lookup for DragonFF (DFF texture names and TXD names differ in case)."""

    def __init__(self, *a, **kw):
        super().__init__()
        for k, v in dict(*a, **kw).items():
            self[k] = v

    def __setitem__(self, k, v):
        super().__setitem__(str(k).lower(), v)

    def __getitem__(self, k):
        return super().__getitem__(str(k).lower())

    def __contains__(self, k):
        return super().__contains__(str(k).lower())

    def get(self, k, default=None):
        return super().get(str(k).lower(), default)

    def setdefault(self, k, default=None):
        return super().setdefault(str(k).lower(), default)


# --------------------------------------------------------------------------- DragonFF


def _fast_create_image(name, rgba, width, height, pack=False):
    """Drop-in for ``txd_importer._create_image``: numpy + ``foreach_set`` instead of a Python
    list of floats (an order of magnitude faster); the pixels are identical (rows flipped)."""
    import numpy as np

    buf = np.frombuffer(bytes(rgba), dtype=np.uint8)
    image = bpy.data.images.new(name, width, height, alpha=True)
    if buf.size == width * height * 4:
        px = buf.reshape(height, width, 4)[::-1].astype(np.float32) / 255.0
        image.pixels.foreach_set(px.ravel())
    if pack:
        image.pack()
    return image


def default_dragonff_root() -> str | None:
    """Folder holding the ``dragonff`` copy made by ``satk blender doctor``: ``SATK_DRAGONFF``, else
    ``<work>/blender`` of the satk configuration (needs the satk sources on ``sys.path``)."""
    env = os.environ.get("SATK_DRAGONFF")
    if env:
        return env
    try:
        from satk.core.paths import cfg
    except ImportError:
        return None
    try:
        return os.path.join(os.path.abspath(cfg().paths.work), "blender")
    except Exception:  # noqa: BLE001 - no workspace configured
        return None


def load_dragonff(path: str | None = None):
    """Import and register DragonFF from ``path`` (the directory containing ``dragonff``).

    An installed DragonFF extension (``bl_ext.*.dragonff``) is used when present (GUI sessions);
    headless jobs always pass ``work/blender``. Without either, :func:`default_dragonff_root`.
    """
    global _DFF
    if _DFF is not None:
        return _DFF
    mod = None
    if path is None:
        for name, m in list(sys.modules.items()):
            if name.startswith("bl_ext.") and name.endswith(".dragonff"):
                mod = m
                break
    if mod is None:
        if path is None:
            path = default_dragonff_root()
        if not path:
            raise RuntimeError("DragonFF not found: install the DragonFF extension, or set the add-on preference "
                               "'DragonFF parent' (or SATK_DRAGONFF) to <work>/blender after 'satk blender doctor'")
        if path not in sys.path:
            sys.path.insert(0, path)
        try:
            import dragonff as mod  # noqa: PLC0415
        except ImportError as e:
            raise RuntimeError(f"DragonFF not found in {path}: run 'satk blender doctor' (extracts DragonFF "
                               "b3bd7aa there) or install the DragonFF extension") from e
        if not hasattr(bpy.types.Object, "dff"):
            mod.register()
    from importlib import import_module

    txd_importer = import_module(mod.__name__ + ".ops.txd_importer")
    txd_importer.txd_importer._create_image = _fast_create_image
    _patch_txd_import(txd_importer)
    _DFF = mod
    return mod


def _satk_txd_images(path: str, pack: bool) -> dict | None:
    """``{texture name: [image]}`` of a PC TXD decoded by ``satk.formats`` (mip 0, DragonFF's image names
    ``<txd>/<texture>/0``); ``None`` when a texture is not a PC/D3D texture satk decodes."""
    from satk.formats.dxt import decode_rgba
    from satk.formats.txd import mip0_bytes, palette_bytes

    with open(path, "rb") as f:
        data = f.read()
    txd_name = os.path.basename(path).lower()
    decoded = []
    for source, tex in _txd_textures(data):
        if tex.unsupported:
            return None
        decoded.append((tex, decode_rgba(tex, mip0_bytes(source, tex), palette_bytes(source, tex))))
    images: dict = {}
    for tex, rgba in decoded:
        name = f"{txd_name}/{tex.name}/0"
        img = bpy.data.images.get(name) or _fast_create_image(name, rgba, tex.w, tex.h, pack=pack)
        images[tex.name] = [img]
    return images


def _patch_txd_import(mod) -> None:
    """Route DragonFF's ``import_txd`` (its DFF/TXD import operator and map importer) through
    ``satk.formats.dxt``: DragonFF b3bd7aa decodes DXT3 colour blocks as DXT1 (report 11). With mipmaps
    asked for, or a TXD satk cannot decode (PS2/Xbox, broken), DragonFF's own decoder runs."""
    orig = getattr(mod.import_txd, "_satk_orig", None) or mod.import_txd

    def import_txd(options):
        if options.get("skip_mipmaps", True):
            try:
                images = _satk_txd_images(options["file_name"], bool(options.get("pack", True)))
            except Exception:  # noqa: BLE001 - not a TXD satk reads: DragonFF decodes it
                images = None
            if images is not None:
                cls = mod.txd_importer
                cls._init()
                cls.file_name = options["file_name"]
                cls.images = images
                return cls
        return orig(options)

    import_txd._satk_orig = orig
    import_txd._satk = True
    mod.import_txd = import_txd


def dff_module(sub: str):
    """``dragonff.<sub>`` (e.g. ``ops.dff_importer``) of the loaded DragonFF."""
    from importlib import import_module

    return import_module(load_dragonff().__name__ + "." + sub)


def dragonff_commit(path: str | None) -> str | None:
    import json

    if not path:
        return None
    try:
        with open(os.path.join(path, "dragonff", ".satk-dragonff.json"), encoding="utf-8") as f:
            return json.load(f).get("commit")
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- textures


_txd_cache: dict[str, CIDict] = {}


def _txd_textures(data):
    """Yield (buffer, TexInfo), isolating malformed natives if whole-TXD parsing fails."""
    import struct

    from satk.formats.rw import FormatError, iter_children
    from satk.formats.txd import parse_txd

    try:
        textures = parse_txd(data).textures
    except FormatError:
        # A file cut inside its last native also overstates the dictionary size.
        # Clamp to the available bytes; only complete, independently parsed natives survive.
        top = next(iter_children(data, 0, len(data), strict=False), None)
        if top is None or top.type != 0x16:
            raise
        # Keep the normal path unchanged. For a broken TXD, frame each native as its
        # own dictionary so the public parser can reject it without losing siblings.
        for ch in iter_children(data, top.data_off, top.end, strict=False):
            if ch.type != 0x15:
                continue
            native = data[ch.data_off - 12:ch.end]
            single = struct.pack("<III", 0x16, len(native), top.libid) + native
            try:
                textures = parse_txd(single).textures
            except FormatError:
                continue
            for tex in textures:
                yield single, tex
    else:
        for tex in textures:
            yield data, tex


def load_txd(path: str) -> CIDict:
    """Images of one PC TXD (mip 0, packed), cached per path for the session.

    Decode native pixels with satk: DragonFF b3bd7aa treats BC2 colour blocks with c0 <= c1
    as BC1, which corrupts DXT3 colours. Keep DragonFF's name -> [image] lookup interface.
    """
    from satk.core.paths import open_ro
    from satk.formats.dxt import decode_rgba
    from satk.formats.rw import FormatError
    from satk.formats.txd import mip0_bytes, palette_bytes

    key = os.path.normcase(os.path.abspath(path))
    if key in _txd_cache:
        return _txd_cache[key]
    with open_ro(path) as f:
        data = f.read()
    images = CIDict()
    for source, tex in _txd_textures(data):
        if tex.unsupported:
            continue  # missing_textures reports materials referring to unsupported natives
        try:
            rgba = decode_rgba(tex, mip0_bytes(source, tex), palette_bytes(source, tex))
        except FormatError:
            continue  # one bad/unsupported texture must not abort the whole model or area
        images[tex.name] = [_fast_create_image(tex.name, rgba, tex.w, tex.h, pack=True)]
    _txd_cache[key] = images
    return images


def txd_image_names(paths: list[str]) -> set[str]:
    """Blender names of every image loaded from the TXDs ``paths`` (name clashes included).
    DragonFF maps a texture name to its list of images (mip 0 only here: ``skip_mipmaps``)."""
    return {img.name for p in paths for imgs in load_txd(p).values() for img in imgs if img is not None}


def load_txd_chain(paths: list[str]) -> CIDict:
    """Merged images of a TXD chain; the first TXD (the model's own) wins name clashes."""
    out = CIDict()
    for p in paths:
        for k, v in load_txd(p).items():
            out.setdefault(k, v)
    return out


# --------------------------------------------------------------------------- scene


def clean_scene() -> None:
    """Remove the factory-startup objects and data (cube, light, camera)."""
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights, bpy.data.collections):
        for d in list(coll):
            if d.users == 0 or coll is bpy.data.collections:
                coll.remove(d)


def ensure_collection(name: str, parent=None):
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name)
    parent = parent or bpy.context.scene.collection
    if c.name not in parent.children:
        parent.children.link(c)
    return c


def layer_collection(coll, lc=None):
    lc = lc or bpy.context.view_layer.layer_collection
    if lc.collection == coll:
        return lc
    for ch in lc.children:
        r = layer_collection(coll, ch)
        if r is not None:
            return r
    return None


def move_collection(coll, new_parent) -> None:
    for p in [bpy.context.scene.collection, *bpy.data.collections]:
        if p != new_parent and coll.name in p.children:
            p.children.unlink(coll)
    if coll.name not in new_parent.children:
        new_parent.children.link(coll)


def world_matrix(o):
    """``matrix_world`` from the parent chain (valid also for objects outside the view layer)."""
    if o.parent is None:
        return o.matrix_basis.copy()
    if o.parent_type == "BONE" and o.parent_bone:
        return o.matrix_world.copy()
    return world_matrix(o.parent) @ o.matrix_parent_inverse @ o.matrix_basis


def real_images():
    return [i for i in bpy.data.images if i.name not in _SYSTEM_IMAGES]


def used_images(objs) -> set:
    """Images on the image nodes of the materials of ``objs`` (what a saved scene keeps of them)."""
    out = set()
    for o in objs:
        for slot in getattr(o, "material_slots", ()):
            m = slot.material
            if m and m.node_tree:
                out.update(n.image for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image is not None)
    return out


def purge_unused() -> int:
    """Drop data without users (recursively), as saving does, so that the statistics of a job describe
    the saved ``.blend``: TXD textures no material uses (e.g. most of ``vehicle.txd``) are never
    written. Returns the number of images removed. The TXD cache is cleared (it held them)."""
    n0 = len(real_images())
    try:
        bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)
    except (AttributeError, TypeError):  # pragma: no cover - older Blender
        for img in list(real_images()):
            if img.users == 0 and not img.use_fake_user:
                bpy.data.images.remove(img)
    _txd_cache.clear()
    return n0 - len(real_images())


#: DragonFF links the ``*_breakable`` meshes (the broken state of a model) to this collection at the
#: scene root, at the parent's position, visible to renders.
BREAKABLE = "Breakable"


def breakables_before() -> set[str]:
    """Names in DragonFF's ``Breakable`` collection before an import (see :func:`adopt_breakables`)."""
    c = bpy.data.collections.get(BREAKABLE)
    return {o.name for o in c.objects} if c is not None else set()


def adopt_breakables(coll, before: set[str], **props) -> int:
    """Move the breakable meshes an import just created from ``Breakable`` into the model collection
    ``coll`` (they follow it: hidden prototypes of an area stay hidden), hide them in renders (the game
    shows them only once the object is smashed) and tag them. The ``Breakable`` collection is removed
    when it is left empty. Returns the number of objects moved."""
    c = bpy.data.collections.get(BREAKABLE)
    if c is None:
        return 0
    n = 0
    for o in list(c.objects):
        if o.name in before:
            continue
        if o.name not in coll.objects:
            coll.objects.link(o)
        c.objects.unlink(o)
        o.hide_render = True
        tag(o, satk_breakable=1, **props)
        n += 1
    if not c.objects and not c.children and not before:
        bpy.data.collections.remove(c)
    return n


def missing_textures(objs) -> list[str]:
    """Texture names of image nodes without an image in the materials of ``objs``."""
    out: set[str] = set()
    for o in objs:
        for slot in getattr(o, "material_slots", ()):
            m = slot.material
            if not m or not m.node_tree:
                continue
            for n in m.node_tree.nodes:
                if n.type == "TEX_IMAGE" and n.image is None:
                    out.add(n.label or m.name)
    return sorted(out)


def is_damage_part(name: str) -> bool:
    return bool(_DAM_RE.search(name.split(".")[0]))


def tag(o, **props) -> None:
    for k, v in props.items():
        if v is not None:
            o[k] = v


def save_blend(path: str) -> str:
    """Save with every image packed (``.blend`` must re-open with textures, report 11 §3.3)."""
    for img in real_images():
        if img.packed_file is None and img.has_data:
            try:
                img.pack()
            except RuntimeError:
                pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False, compress=True)
    return path


def fwd(p: str) -> str:
    s = os.path.abspath(p).replace("\\", "/")
    return s[0].upper() + s[1:] if len(s) > 1 and s[1] == ":" else s
