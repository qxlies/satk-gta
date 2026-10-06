# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Entry of the studio world (written by ``satk.studio.launcher``)::

    blender.exe [-b] [scene.blend] --factory-startup --python-exit-code 1 \\
        --python <checkout>/blender/satk_blender/studio/main.py -- <dir>/request.json

``request.json`` ``mode``: ``serve`` (a named session: SAAP endpoint until ``quit``, idle timeout or the
owner process exits; the token comes in ``SATK_AGENT_TOKEN``) or ``oneshot`` (one ``author.call`` /
``author.methods``, the answer goes to ``response.json``).
"""

from __future__ import annotations

import json
import os
import sys

# Blender ignores PYTHONDONTWRITEBYTECODE (ignore_environment): no __pycache__ in the checkout.
sys.dont_write_bytecode = True


def _request() -> dict:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        raise SystemExit("usage: blender -b --python main.py -- <request.json>")
    with open(argv[0], encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    req = _request()
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (req.get("satk_src"), os.path.dirname(os.path.dirname(here))):
        if p and p not in sys.path:
            sys.path.insert(0, p)
    from satk_blender.studio import world

    world.main(req)


main()
