# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Studio method plug-ins: every submodule exposes ``METHODS = {"group.name": fn(ctx, params) -> dict}``.

``satk.studio.core.discover_methods`` scans this package (and ``satk_blender.kit.methods`` /
``satk_blender.look.methods`` when present). A method validates its own parameters and raises
``SatkError("BAD_PARAMS", ...)`` with a hint; it returns a small dict, with ``changed`` (object names)
when it changed the scene.
"""
