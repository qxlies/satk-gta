# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Small helpers of the kit methods: parameters, objects, collections, matrices, mesh numbers."""

from __future__ import annotations

import difflib
import json
import os

import bpy
from mathutils import Matrix

from satk.core.errors import SatkError

__all__ = ["ensure_dff", "bad", "need", "num", "obj", "objs", "kit_root", "clump", "rw_matrix", "tris", "mesh_objects",
           "read_json", "link", "evaluated_mesh", "fwd"]


def ensure_dff() -> None:
    """Register DragonFF (its ``.dff`` properties) once per Blender process."""
    if not hasattr(bpy.types.Object, "dff"):
        from satk_blender import common

        common.load_dragonff(os.environ.get("SATK_DRAGONFF") or None)


def fwd(p: str) -> str:
    return os.path.abspath(p).replace("\\", "/")


def bad(method: str, msg: str, hint: str | None = None) -> SatkError:
    return SatkError("BAD_PARAMS", f"{method}: {msg}", hint=hint)


def need(p: dict, key: str, method: str):
    if key not in p or p[key] in (None, ""):
        raise bad(method, f"'{key}' is required")
    return p[key]


def num(p: dict, key: str, default: float, method: str, lo: float | None = None, hi: float | None = None) -> float:
    v = p.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise bad(method, f"'{key}' must be a number, got {v!r}")
    v = float(v)
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise bad(method, f"'{key}' must be in {lo}..{hi}, got {v}")
    return v


def obj(name: str, method: str = "kit"):
    o = bpy.data.objects.get(str(name))
    if o is None:
        near = difflib.get_close_matches(str(name), [x.name for x in bpy.data.objects], n=3, cutoff=0.5)
        raise SatkError("NOT_FOUND", f"{method}: no object {name!r}", data={"did_you_mean": near} if near else None)
    return o


def objs(names, method: str = "kit") -> list:
    if isinstance(names, str):
        names = [names]
    return [obj(n, method) for n in names or []]


def clump(name: str | None = None):
    """The kit clump collection (``<name>.dff`` tagged ``satk_kit``); the only one when ``name`` is omitted."""
    cs = [c for c in bpy.data.collections if c.get("satk_kit") and not c.get("satk_lod_of") and len(c.objects)]
    if name:
        n = str(name).lower()
        for c in cs:
            if str(c.get("satk_name", "")).lower() == n or c.name.lower() == f"{n}.dff":
                return c
        raise SatkError("NOT_FOUND", f"no kit model {name!r} in the scene",
                        hint="kit.template first", data={"did_you_mean": [str(c.get('satk_name')) for c in cs][:5]})
    if not cs:
        raise SatkError("NOT_FOUND", "no kit model in the scene", hint="run kit.template first")
    if len(cs) > 1:
        raise SatkError("AMBIGUOUS", f"{len(cs)} kit models in the scene: give 'model'",
                        data={"models": [str(c.get('satk_name')) for c in cs][:10]})
    return cs[0]


def kit_root(coll):
    return next((o for o in coll.objects if o.get("satk_role") == "root"), None)


def rw_matrix(m12) -> Matrix:
    """RW 12 floats (right, up, at, pos) -> Blender 4x4."""
    rx, ry, rz, ux, uy, uz, ax, ay, az, px, py, pz = (float(v) for v in m12)
    return Matrix(((rx, ux, ax, px), (ry, uy, ay, py), (rz, uz, az, pz), (0.0, 0.0, 0.0, 1.0)))


def tris(o) -> int:
    if o.type != "MESH":
        return 0
    return sum(max(0, len(p.vertices) - 2) for p in o.data.polygons)


def mesh_objects(coll) -> list:
    return [o for o in coll.all_objects if o.type == "MESH" and o.get("satk_role") in ("part", "root", "lod")]


def read_json(path: str) -> dict:
    if not path or not os.path.isfile(path):
        raise SatkError("NOT_FOUND", f"no plan file {path!r}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def link(o, coll) -> None:
    if o.name not in coll.objects:
        coll.objects.link(o)


def evaluated_mesh(o):
    """``(mesh, free)``: the evaluated mesh of ``o`` (modifiers applied) and a function that frees it."""
    dg = bpy.context.evaluated_depsgraph_get()
    ev = o.evaluated_get(dg)
    me = ev.to_mesh()
    return me, ev.to_mesh_clear
