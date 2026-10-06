# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Studio: Blender as a live SAAP endpoint (role ``blender``, capability ``author``) for agent modelling.

* ``main.py`` - entry script (``blender -b --python main.py -- request.json``), serve or one-shot;
* ``world.py`` - the world: ``author.call``/``author.methods`` through ``satk.studio.core``;
* ``pump.py`` - runs requests on Blender's main thread (blocking loop in ``-b``, timers in the GUI);
* ``stats.py`` / ``snapshot.py`` - per-step numbers of the evaluated meshes and Workbench JPEG snapshots;
* ``methods/`` - plug-ins with ``METHODS`` dicts (contract K3 in ``satk.studio.core``).
"""
