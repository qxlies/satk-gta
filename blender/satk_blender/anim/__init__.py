# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in the parent directory.
"""IFP animations in Blender (``satk anim to-blender`` / ``from-blender``).

* :mod:`.apply` - IFP key frames -> an action on the armature DragonFF builds for a skinned DFF, and an action
  back to IFP key frames (the engine's bone binding: tag -> HAnim ``bone_id``, untagged -> engine bone name);
* :mod:`.job` - the headless entry (``blender -b [file.blend] --python job.py -- request.json``), always
  writing ``response.json``.

The key-frame codec itself is satk's MIT ``satk.anim`` (pure Python, imported from the satk sources).
"""
