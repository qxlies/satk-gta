# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Asset kit, Blender side (contract K6): template scaffolds, material presets, ``sa_shade``, generators
(wheel, vlo, damage, map LOD, collision), atlas UV regions, Cycles bakes and the kit export.

Every method lives in :mod:`satk_blender.kit.methods` (``METHODS``), discovered by the studio (K3) and
callable in a live session or as a one-shot job through ``satk.studio.api.call`` (K4).
"""
