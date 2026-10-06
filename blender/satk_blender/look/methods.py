# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Studio methods ``look.*`` (contract K3): SA-like looks and previews inside a live session.

* ``look.apply {look, dirt, time, lights, weather}`` - give the scene a look (stays until ``look.restore``);
* ``look.restore {}`` - back to the materials as they were;
* ``look.render {views, size, look, dirt, time, lights}`` - render views in a look (restored afterwards), one
  JPEG sheet;
* ``look.preview {spec}`` - the preview job (``satk blender preview session:NAME``): the session scene next to
  imported class peers; the peers are removed again. The full answer is written to a JSON file.
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
    try:
        api.apply(ctx.scene, o["look"], dirt=o["dirt"], time=o["time"], lights=o["lights"], weather=o["weather"])
        paths = api.render_views(ctx.scene, views, size, fmt="jpg", out_dir=d)
        sheet = api.sheet(paths, cols=min(len(paths), 2), out=os.path.join(d, "sheet.jpg")) if len(paths) > 1 else paths[0]
    except (KeyError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"{type(e).__name__}: {e}") from None
    finally:
        api.restore(ctx.scene)
    return {"file": sheet, "views": len(paths)}


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


METHODS = {"look.apply": _apply, "look.restore": _restore, "look.render": _render, "look.preview": _preview}
