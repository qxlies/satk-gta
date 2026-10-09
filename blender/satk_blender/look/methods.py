# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Studio methods ``look.*`` (contract K3): SA-like looks and previews inside a live session.

* ``look.apply {look, dirt, time, lights, weather}`` - give the scene a look (stays until ``look.restore``);
* ``look.restore {}`` - back to the materials as they were;
* ``look.render {views, size, look, dirt, time, lights, colors}`` - render views in a look (restored afterwards),
  one JPEG sheet; like the preview it puts the wheel on every wheel dummy and paints the paint keys (``colors``,
  default the new-model blue) for the render only;
* ``look.preview {spec}`` - the preview job (``satk blender preview session:NAME``): the session scene next to
  imported class peers; the peers are removed again. The full answer is written to a JSON file;
* ``look.silhouette {ref, view, model, flip, box, stations}`` - the model's silhouette against a true side/front/
  rear photo: overlap and the top line along the length (information for a critic), an overlay PNG;
* ``look.leak {kind, regions, views, size, package}`` - the light-leak check of the session scene (contract K8,
  :mod:`.leak`): located gaps from the review cameras of the kind, one cell per camera, ``<package>/checks/leak.json``
  when a package folder is given.
"""

from __future__ import annotations

import json
import os

from satk.core.errors import SatkError
from satk.look import gamelook as G
from satk.studio.core import readonly

from . import api, preview

__all__ = ["METHODS"]


def _opts(p: dict) -> dict:
    look = str(p.get("look") or "game")
    if look not in G.LOOKS:
        raise SatkError("BAD_PARAMS", f"look must be one of {'|'.join(G.LOOKS)}, got {look!r}")
    lights = str(p.get("lights") or "off")
    if lights not in ("off", "on"):
        raise SatkError("BAD_PARAMS", "lights must be off or on")
    try:
        dirt = float(p.get("dirt", G.DIRT_DEFAULT))
        G.parse_time(p.get("time") or "12:00")
    except (TypeError, ValueError) as e:
        raise SatkError("BAD_PARAMS", str(e)) from None
    if not 0 <= dirt <= G.DIRT_MAX:
        raise SatkError("BAD_PARAMS", f"dirt must be 0..{G.DIRT_MAX}")
    return {"look": look, "dirt": dirt, "time": p.get("time") or "12:00", "lights": lights,
            "weather": str(p.get("weather") or G.DEFAULT_WEATHER)}


def _apply(ctx, p: dict) -> dict:
    """Give the scene an SA look: game (glass, white lamps, dirt 0-16, env, prelight), clay, wire or raw."""
    o = _opts(p)
    try:
        return api.apply(ctx.scene, o["look"], dirt=o["dirt"], time=o["time"], lights=o["lights"],
                         weather=o["weather"])
    except ValueError as e:
        raise SatkError("BAD_PARAMS", str(e)) from None


def _restore(ctx, p: dict) -> dict:
    """Undo look.apply: original materials, visibility, no ground."""
    return api.restore(ctx.scene)


def _out(ctx, sub: str) -> str:
    d = os.path.join(ctx.out_dir, "look", f"{ctx.n:04d}-{sub}")
    os.makedirs(d, exist_ok=True)
    return d


@readonly
def _render(ctx, p: dict) -> dict:
    """Render views (3q rear3q front rear side top) in a look into one JPEG sheet; the look is restored."""
    o = _opts(p)
    views = p.get("views") or ["3q", "side"]
    if isinstance(views, str):
        views = [v for v in views.split(",") if v]
    size = int(p.get("size") or 384)
    if not 64 <= size <= 1024:
        raise SatkError("BAD_PARAMS", "size must be 64..1024")
    d = _out(ctx, "render")
    paint = p.get("colors")
    # the scene as the game shows it, for this render only: the wheel on every wheel dummy, paint colours
    tmp, painted = preview.scene_prepare(ctx.scene, {"paint": paint} if paint else {})
    wheels = sum(1 for o in tmp if o.type == "MESH")
    try:
        api.apply(ctx.scene, o["look"], dirt=o["dirt"], time=o["time"], lights=o["lights"], weather=o["weather"])
        paths = api.render_views(ctx.scene, views, size, fmt="jpg", out_dir=d)
        sheet = api.sheet(paths, cols=min(len(paths), 2), out=os.path.join(d, "sheet.jpg")) if len(paths) > 1 else paths[0]
    except (KeyError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"{type(e).__name__}: {e}") from None
    finally:
        api.restore(ctx.scene)
        preview.scene_restore(tmp, painted)
    out = {"file": sheet, "views": len(paths)}
    if wheels:
        out["wheels_added"] = wheels
    return out


@readonly
def _preview(ctx, p: dict) -> dict:
    """Preview job (satk blender preview session:NAME): the scene next to imported peers, cells as PNG files."""
    spec = p.get("spec")
    if not isinstance(spec, dict):
        raise SatkError("BAD_PARAMS", "look.preview needs 'spec' (built by satk blender preview)")
    d = _out(ctx, "preview")
    try:
        res = preview.run(spec, d, session=True)
    except (KeyError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"{type(e).__name__}: {e}") from None
    path = os.path.join(d, "preview.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    return {"json": path.replace("\\", "/"), "cells": len(res["cells"]), "seconds": res["seconds"]["total"]}


@readonly
def _silhouette(ctx, p: dict) -> dict:
    """Compare the model with a TRUE side/front/rear photo: silhouette overlap (IoU) and the top line at stations along the length in metres (info for a critic, never a gate); ref, view side|front|rear, model, flip, box px, stations."""
    from . import silhouette as SIL

    ref = str(p.get("ref") or "")
    if not ref or not os.path.isfile(ref):
        raise SatkError("BAD_PARAMS", f"look.silhouette: no image {ref!r}", hint="'ref': a true side/front/rear photo")
    view = str(p.get("view") or "side")
    if view not in SIL.VIEW_AXES:
        raise SatkError("BAD_PARAMS", f"look.silhouette: view {view!r}", hint="side | front | rear (true elevations only)")
    objs = _model_objects(ctx, p.get("model"))
    tmp, painted = preview.scene_prepare(ctx.scene, {})
    try:
        objs = objs + [o for o in tmp if o.type == "MESH"]
        box = p.get("box")
        rep = SIL.compare_photo(objs, ref, view, res=int(p.get("size") or 480), stations=int(p.get("stations") or 20),
                                flip=bool(p.get("flip")), box=box, out_dir=_out(ctx, "silhouette"))
    except ValueError as e:
        raise SatkError("BAD_PARAMS", str(e)) from None
    finally:
        preview.scene_restore(tmp, painted)
    rep["note"] = "info for a critic: only meaningful for a true elevation photo (far away, both wheels round)"
    rep["cols"] = ["t", "model_top_m", "photo_top_m", "dev_m"]
    return rep


def _model_objects(ctx, model) -> list:
    """The meshes the game shows of a kit model (``model``) or of the whole scene (no refs, ghosts, damage, LOD, COL)."""
    import bpy

    if model:
        coll = next((c for c in bpy.data.collections if str(c.get("satk_name", "")).lower() == str(model).lower()
                     and not c.get("satk_lod_of")), None)
        if coll is None:
            raise SatkError("NOT_FOUND", f"look.silhouette: no kit model {model!r}")
        objs = list(coll.all_objects)
    else:
        objs = preview._scene_objects(ctx.scene)  # noqa: SLF001
    return [o for o in objs if o.type == "MESH" and not o.get("satk_ref") and not preview.default_hidden(o)
            and not o.hide_render]


def _scene_kind(scene) -> str:
    """The kit kind of the session scene: a kit collection's ``satk_kit``, else vehicle -> automobile, else prop."""
    import bpy

    for c in bpy.data.collections:
        if c.get("satk_kit") and not c.get("satk_lod_of"):
            return str(c["satk_kit"])
    from . import materials as M

    objs = preview._scene_objects(scene)  # noqa: SLF001
    return "automobile" if M.classify(api.class_objects(objs, scene)) == "vehicle" else "prop"


@readonly
def _leak(ctx, p: dict) -> dict:
    """Light-leak check of the scene: the model black on a bright background from the review cameras of its kind (kind, regions, or views), located gaps with view, box, 3D point and nearest part; package = folder for checks/<stem>.leak.json."""
    from satk.look import regions as RG
    from satk.look import review

    kind = str(p.get("kind") or _scene_kind(ctx.scene))
    try:
        defn = RG.load(kind)
    except SatkError:
        raise
    size = int(p.get("size") or 320)
    if not 96 <= size <= 768:
        raise SatkError("BAD_PARAMS", "size must be 96..768")
    cfg = RG.leak_config(defn, cull=p.get("cull"))
    spec: dict = {"entries": [{"key": "e0", "label": "scene", "scene": True}], "passes": ["leak"], "states": ["ok"],
                  "size": [size, size], "leak": cfg, "views": ["3q"]}
    views = p.get("views")
    if isinstance(views, str):
        views = [v for v in views.split(",") if v]
    if views:
        spec["views"] = list(views)
    else:
        names = p.get("regions")
        if isinstance(names, str):
            names = [v for v in names.split(",") if v]
        regs = [r for r in RG.select(defn, names) if r.get("leak", True)]
        if not regs:
            raise SatkError("BAD_PARAMS", "no leak region selected",
                            hint="regions: " + ", ".join(r["name"] for r in defn["regions"] if r.get("leak", True)))
        spec["regions"] = regs
        if defn.get("ref"):
            spec["region_ref"] = defn["ref"]
    cover = review.coverage(defn, None if views else [r["name"] for r in spec["regions"]], views=list(views or []) or None,
                            cull=p.get("cull"), size=size)
    d = _out(ctx, "leak")
    try:
        res = preview.run(spec, d, session=True)
    except (KeyError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"{type(e).__name__}: {e}") from None
    lk = res.get("leak") or {}
    cells = [c[2] for c in res.get("cells") or []]
    sheet = api.sheet(cells, cols=min(4, len(cells)), out=os.path.join(d, "leak.jpg")) if len(cells) > 1 else \
        (cells[0] if cells else None)
    doc = review.leak_report(lk, subject="session", kind=defn.get("kind", kind), sheet=sheet, cover=cover)
    out = {"kind": doc["kind"], "clean": doc["clean"], "blocking": doc["blocking"], "sheet": sheet,
           "cols": ["id", "kind", "region", "view", "area_cm2", "point", "near"],
           "rows": [[g.get("id"), g.get("kind"), g.get("region"), g.get("view"), g.get("area_cm2"), g.get("point"),
                     g.get("near")] for g in doc["gaps"][:20]]}
    if doc.get("info"):
        out["info"] = len(doc["info"])
    if p.get("package"):
        out["leak_json"] = review.write_leak(str(p["package"]), doc)
    return out


METHODS = {"look.apply": _apply, "look.restore": _restore, "look.render": _render, "look.preview": _preview,
           "look.silhouette": _silhouette, "look.leak": _leak}
