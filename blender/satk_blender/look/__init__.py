# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""SA-like looks of a Blender scene (contract K5) and the previews built on them.

* :mod:`.api` - ``apply(scene, look, dirt, time, lights)``, ``restore(scene)``, ``render_views(scene, cams,
  size, fmt)`` and ``sheet(paths, cols)``;
* :mod:`.materials` - the shared node group ``SATK_look_game`` (display-space colour chain of the game) and
  the clay/wire override materials;
* :mod:`.lights` - time of day (timecyc light, colour filter, sky), the fixed light direction, ground and
  contact shadows;
* :mod:`.dirt` - the dirt rule of ``vehiclegrunge256``;
* :mod:`.preview` - the preview job: entries (imported models or the session scene), states, lineup, cells;
* :mod:`.methods` - studio plug-in methods ``look.*`` (contract K3).

The numbers come from :mod:`satk.look.gamelook` (MIT, shared with the soft renderer).
"""
