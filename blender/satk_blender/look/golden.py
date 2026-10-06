# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Golden numbers of the game look, measured inside Blender (``tests/look/test_live_look.py``)::

    blender -b --factory-startup --python <checkout>/blender/satk_blender/look/golden.py -- <request.json>

Request: ``{"satk_src", "addon_parent", "dragonff", "out", "car": <plan of model:426>, "prelit": {sid: plan}}``.
Writes ``<out>/measure.json``. Numbers only: glass see-through, lamp materials white, dirt lighter than the
raw texture, mean luminance of prelit models.
"""

import json
import os
import sys

sys.dont_write_bytecode = True

if __name__ == "__main__":
    REQ = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
    for _p in (REQ["satk_src"], REQ["addon_parent"]):
        if _p not in sys.path:
            sys.path.insert(0, _p)

import bpy  # noqa: E402
import numpy as np  # noqa: E402

from satk_blender import common, importer  # noqa: E402
from satk_blender.look import api  # noqa: E402
from satk_blender.look import materials as M  # noqa: E402


def _load(path: str):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        a = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(a)
        return a.reshape(h, w, 4)[..., :3] * 255.0
    finally:
        bpy.data.images.remove(img)


def _lum(a):
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def _shot(scene, out: str, name: str, view: str, *, dirt: float = 2.0, tweak=None, size: int = 200):
    api.apply(scene, "game", dirt=dirt, time="12:00", ground=False)
    if tweak:
        tweak()
    try:
        p = api.render_views(scene, [view], size, fmt="png", out_dir=out, prefix=name)[0]
    finally:
        api.restore(scene)
    return _load(p)


def _import(plan: dict) -> None:
    importer.import_model({"model": plan, "source": "test"}, {"clean": True})
    paint = (plan.get("vehicle") or {}).get("colors")
    if paint:
        for o in bpy.data.objects:
            o["satk_paint"] = json.dumps(paint)


def _opaque_glass() -> None:
    for m in bpy.data.materials:
        g = m.node_tree.nodes.get(M.PREFIX) if m.node_tree else None
        if g is not None and g.inputs["Material Alpha"].default_value < 0.995:
            g.inputs["Material Alpha"].default_value = 1.0


def measure(req: dict) -> dict:
    """Measure the golden numbers (see the module docstring); returns them and writes ``measure.json``."""
    common.load_dragonff(req["dragonff"])
    out = req["out"]
    scene = bpy.context.scene
    res: dict = {}
    # vehicle: glass, lamps, dirt
    _import(req["car"])
    a = _shot(scene, out, "glass", "front")
    b = _shot(scene, out, "glass_opaque", "front", tweak=_opaque_glass)
    mask = np.abs(a - b).max(axis=2) > 8
    res["glass_px"] = int(mask.sum())
    res["glass_lum"] = round(float(_lum(a)[mask].mean()), 1) if mask.any() else None
    res["glass_lum_opaque"] = round(float(_lum(b)[mask].mean()), 1) if mask.any() else None
    api.apply(scene, "game", dirt=2.0, ground=False)
    lamps = [m for m in bpy.data.materials if m.get("satk_look") and m.node_tree and m.node_tree.nodes.get(M.PREFIX)
             and any(n.type == "TEX_IMAGE" and n.image is not None and "vehiclelights128" in n.image.name.lower()
                     for n in m.node_tree.nodes)]
    res["lamp_materials"] = len(lamps)
    res["lamp_white"] = sum(1 for m in lamps
                            if tuple(m.node_tree.nodes[M.PREFIX].inputs["Material"].default_value)[:3] == (1.0, 1.0, 1.0))
    api.restore(scene)
    d2 = _lum(_shot(scene, out, "dirt2", "3q", dirt=2.0))
    d16 = _lum(_shot(scene, out, "dirt16", "3q", dirt=16.0))
    dm = np.abs(d2 - d16) > 1.0  # the body pixels on vehiclegrunge256
    res["dirt_px"] = int(dm.sum())
    res["dirt2_minus_raw"] = round(float((d2 - d16)[dm].mean()), 2) if dm.any() else 0.0
    # prelit map models: mean luminance of the covered pixels
    res["prelit"] = {}
    for sid, plan in sorted((req.get("prelit") or {}).items()):
        _import(plan)
        img = _shot(scene, out, "prelit_" + sid.replace(":", "_"), "3q")
        cov = np.abs(img - img[0, 0]).max(axis=2) > 3
        res["prelit"][sid] = round(float(_lum(img)[cov].mean()), 1) if cov.any() else None
    with open(os.path.join(out, "measure.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    return res


if __name__ == "__main__":
    measure(REQ)
