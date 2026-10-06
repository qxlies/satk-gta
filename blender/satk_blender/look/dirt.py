# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Dirt of vehicle bodies: which materials get it and how much white is mixed in.

The game builds 16 dirt textures from ``vehiclegrunge256`` (``InitialiseDirtTexture`` 0x5D5BC0): level ``i``
has ``rgb = c * i / 16 + 255 * (16 - i) / 16``. In the display-space node chain that is a mix of the texel
towards white by ``1 - i / 16`` (:func:`white_share`).
"""

from __future__ import annotations

from satk.look import gamelook as G

__all__ = ["is_dirt_image", "white_share"]


def is_dirt_image(name: str | None) -> bool:
    """True for the body texture the dirt system swaps (Blender may add ``.001`` to the name)."""
    if not name:
        return False
    n = str(name).lower().split("/")[-1]
    return n.split(".")[0] == G.DIRT_TEXTURE or n.startswith(G.DIRT_TEXTURE)


def white_share(level: float) -> float:
    """Mix factor towards white of a dirt ``level`` 0..16 (0 = clean = all white, 16 = raw texture)."""
    return 1.0 - G.dirt_t(level)
