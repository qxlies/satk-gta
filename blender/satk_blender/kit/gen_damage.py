# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""``kit.damage``: ``*_dam`` slots from their ``*_ok`` twins - dents, hinge sag and shattered glass.

Each damaged part is a copy of the evaluated undamaged part (same topology, so ``_dam``/``_ok`` triangles =
1.0, vanilla 0.8-1.1), pushed in by smooth dents (deterministic: the seed and the part name pick the dent
centres) and rotated about its hinge (doors droop, bonnet and boot buckle, bumpers hang at one corner).
Glass becomes the ``shatter`` preset. Dents grow until the vertex displacement p90 reaches ``min_p90``
(vanilla p90 at least 12 cm) or ``max_depth``.
"""

from __future__ import annotations

import math
import zlib

import bpy
from mathutils import Matrix, Vector

from satk.core.errors import SatkError

from . import materials as M
from . import util as U

__all__ = ["damage_part", "damage_method"]


def _hinge(part: str, sag: float) -> Matrix:
    p = part.lower()
    if p.startswith("door"):
        return Matrix.Rotation(math.radians(-sag), 4, "X")
    if p.startswith(("bonnet", "boot")):
        return Matrix.Rotation(math.radians(sag * 0.5), 4, "X")
    if p.startswith("bump"):
        return Matrix.Rotation(math.radians(sag * 0.5), 4, "Y")
    return Matrix.Identity(4)


def _p90(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(0.9 * (len(s) - 1) + 0.5))]


def damage_part(ok, dam, *, depth: float, sag: float, seed: int, min_p90: float, max_depth: float,
                shatter_mat) -> dict:
    """Fill ``dam`` from ``ok``. Returns the numbers of this part."""
    me, free = U.evaluated_mesh(ok)
    try:
        new = me.copy()
    finally:
        free()
    new.transform(dam.matrix_world.inverted_safe() @ ok.matrix_world)
    new.name = dam.name
    base = [v.co.copy() for v in new.vertices]
    nrm = [v.normal.copy() for v in new.vertices]
    n = len(base)
    if n == 0:
        raise SatkError("BAD_PARAMS", f"kit.damage: {ok.name} is empty")
    lo = Vector((min(v.x for v in base), min(v.y for v in base), min(v.z for v in base)))
    hi = Vector((max(v.x for v in base), max(v.y for v in base), max(v.z for v in base)))
    diag = max((hi - lo).length, 1e-3)
    h = zlib.crc32(f"{seed}:{dam.name}".encode())
    centres = [base[(h >> (8 * k)) % n] for k in range(2)]
    rad = 0.38 * diag
    rot = _hinge(str(dam.get("satk_part") or dam.name), sag)
    d = depth
    for _ in range(8):
        disp = []
        for i, co in enumerate(base):
            w = 0.0
            for c in centres:
                x = (co - c).length / rad
                if x < 1.0:
                    w = max(w, (1.0 - x * x) ** 2)
            p = rot @ (co - nrm[i] * (d * w))
            new.vertices[i].co = p
            disp.append((p - co).length)
        p90 = _p90(disp)
        if p90 >= min_p90 or d >= max_depth:
            break
        d = min(max_depth, d * max(1.2, min_p90 / max(p90, 1e-4)))
    shattered = 0
    if shatter_mat is not None:
        for si, m in enumerate(list(new.materials)):
            if m is not None and str(m.get("satk_role", "")) == "glass":
                new.materials[si] = shatter_mat
                shattered += 1
    new.update()
    old = dam.data
    dam.data = new
    if old is not None and old.users == 0:
        bpy.data.meshes.remove(old)
    for flag in ("export_split_normals", "uv_map1", "uv_map2", "export_normals"):
        setattr(dam.dff, flag, getattr(ok.dff, flag))
    ok_t, dam_t = U.tris(ok), U.tris(dam)
    if ok.modifiers:
        me2, free2 = U.evaluated_mesh(ok)
        ok_t = sum(len(pp.vertices) - 2 for pp in me2.polygons)
        free2()
    return {"part": dam.name, "ratio": round(dam_t / max(1, ok_t), 3), "p90_m": round(p90, 3),
            "depth_m": round(d, 3), "glass": shattered}


def damage_method(ctx, p: dict) -> dict:
    """Fill every *_dam slot from its *_ok twin: smooth dents, hinge sag, shattered glass; _dam/_ok triangles 1.0, displacement p90 >= 0.12 m."""
    coll = U.clump(p.get("model"))
    name = str(coll.get("satk_name"))
    depth = U.num(p, "depth", 0.14, "kit.damage", 0.0, 1.0)
    sag = U.num(p, "sag", 6.0, "kit.damage", 0.0, 45.0)
    seed = int(U.num(p, "seed", 1, "kit.damage", 0, 2 ** 31))
    min_p90 = U.num(p, "min_p90", 0.12, "kit.damage", 0.0, 1.0)
    max_depth = U.num(p, "max_depth", 0.35, "kit.damage", 0.0, 2.0)
    only = {str(x).lower() for x in (p.get("parts") or [])}
    by_part: dict[str, dict] = {}
    for o in coll.objects:
        if o.type == "MESH" and o.get("satk_slot") in ("ok", "dam"):
            by_part.setdefault(str(o.get("satk_part")).lower(), {})[str(o["satk_slot"])] = o
    from satk.kit import kinds as K

    shatter = M.ensure(name, K.preset("shatter", name))
    rows, skipped = [], []
    for part, d in sorted(by_part.items()):
        if only and part not in only:
            continue
        ok, dam = d.get("ok"), d.get("dam")
        if ok is None or dam is None:
            continue
        if not len(ok.data.polygons):
            skipped.append(part)
            continue
        rows.append(damage_part(ok, dam, depth=depth, sag=sag, seed=seed, min_p90=min_p90, max_depth=max_depth,
                                shatter_mat=shatter))
    if not rows:
        raise SatkError("BAD_PARAMS", "kit.damage: no *_ok part with geometry has a *_dam slot",
                        hint="model the *_ok parts (doors, bonnet, bumpers) first", data={"skipped": skipped[:10]})
    from .shade import shade_object

    for r in rows:
        shade_object(bpy.data.objects[r["part"]], cls="vehicle")
    worst = min(rows, key=lambda r: r["p90_m"])
    return {"parts": len(rows), "ratio": [min(r["ratio"] for r in rows), max(r["ratio"] for r in rows)],
            "p90_m_min": worst["p90_m"], "rows": [[r["part"], r["ratio"], r["p90_m"]] for r in rows],
            "skipped": skipped, "changed": [r["part"] for r in rows]}
