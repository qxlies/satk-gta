# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``python``: run a piece of Python in the session (the escape hatch; prefer the named methods).

The code sees ``bpy``, ``bmesh``, ``mathutils``, ``Vector``, ``ctx`` (the method context), ``args``
(the ``args`` parameter) and ``result`` (a dict to fill; it is returned as ``value``). It may set
``changed = [...]``; otherwise the objects it added count as changed. The code and its sha256 are in
the session journal, and a ``.blend`` checkpoint follows every run.

``value`` comes back inline up to :data:`VALUE_MAX` characters of JSON. A bigger value is written whole to
``<session>/python/<step>.json`` and the reply carries its path (``out``) and the first characters; ``out``
given as a parameter (a ``.json`` path under the work folder) always writes the file and returns only its path.
"""

from __future__ import annotations

import hashlib
import json
import os
import traceback

import bmesh
import bpy
import mathutils

from satk.core.errors import SatkError
from satk.studio.core import checkpointed, jsonable, reply_budget

MAX_CODE = 65536
#: Characters of ``value`` (as compact JSON) returned inline.
VALUE_MAX = 4000
_FILE = "<studio-python>"


def _write(ctx, path: str, value) -> str:
    host = getattr(ctx, "host", None)
    p = os.path.abspath(path)
    if host is not None and hasattr(host, "_check_write"):
        p = host._check_write(p, "python out")  # noqa: SLF001 - the host's own write guard
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = f"{p}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)
    return p.replace("\\", "/")


@checkpointed
@reply_budget(VALUE_MAX + 1120)
def run(ctx, p: dict) -> dict:
    """Run Python code in the scene (bpy, bmesh, mathutils, ctx, args; fill 'result'); value inline up to 4,000 chars, the rest (or out=<file.json>) into a file; journaled + checkpoint."""
    code = p.get("code")
    if not isinstance(code, str) or not code.strip():
        raise SatkError("BAD_PARAMS", "python: 'code' (a string) is required")
    if len(code) > MAX_CODE:
        raise SatkError("BAD_PARAMS", f"python: code is longer than {MAX_CODE} characters")
    out_path = p.get("out")
    if out_path is not None and (not isinstance(out_path, str) or not out_path.lower().endswith(".json")):
        raise SatkError("BAD_PARAMS", f"python: 'out' must be a .json file path, got {out_path!r}")
    g = {"__name__": "__studio__", "bpy": bpy, "bmesh": bmesh, "mathutils": mathutils, "Vector": mathutils.Vector,
         "ctx": ctx, "args": p.get("args") or {}, "result": {}}
    before = {o.name for o in bpy.data.objects}
    try:
        exec(compile(code, _FILE, "exec"), g)  # noqa: S102 - the journaled escape hatch of a local session
    except SatkError:
        raise
    except KeyboardInterrupt:
        raise
    except BaseException as e:  # noqa: BLE001 - SystemExit included: the session must survive
        line = None
        if isinstance(e, SyntaxError):
            line = e.lineno
        else:
            for fr in traceback.extract_tb(e.__traceback__):
                if fr.filename == _FILE:
                    line = fr.lineno
        raise SatkError("BAD_PARAMS", f"python: {type(e).__name__}: {e}"[:400],
                        data={"line": line} if line else None) from None
    after = {o.name for o in bpy.data.objects}
    added, removed = sorted(after - before), sorted(before - after)
    changed = g.get("changed")
    if not isinstance(changed, (list, tuple)):
        changed = [n for n in added if n in bpy.context.scene.objects]
    out: dict = {"sha256": hashlib.sha256(code.encode("utf-8")).hexdigest()[:16]}
    if g.get("result") not in (None, {}):
        value = jsonable(g["result"])
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if out_path:
            out["out"] = _write(ctx, out_path, value)
            out["value_chars"] = len(text)
            if isinstance(value, dict):
                out["keys"] = sorted(value)[:20]
        elif len(text) > VALUE_MAX:
            folder = os.path.join(getattr(ctx, "out_dir", None) or os.getcwd(), "python")
            out["out"] = _write(ctx, os.path.join(folder, f"{getattr(ctx, 'n', 0):04d}.json"), value)
            out["value_chars"] = len(text)
            out["value"] = text[: VALUE_MAX - 400] + "..."
            out["note"] = "value too long for the reply: the whole value is in 'out' (read that file)"
        else:
            out["value"] = value
    if added:
        out["added"] = added[:20]
    if removed:
        out["removed"] = removed[:20]
    out["changed"] = [str(x) for x in changed]
    return out


METHODS = {"python": run}
