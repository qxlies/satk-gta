# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""The preview job behind ``satk blender preview`` (cold job ``preview``) and the studio method ``look.preview``.

Spec (built by ``satk.look.ops``)::

    {"entries": [{"key": "e0", "label": "premier.dff", "plan": {<model plan>}}, {"key": "scene", "scene": true}],
     "views": ["3q", "side"], "passes": ["game", "clay"], "states": ["ok", "dam"], "size": 256,
     "dirt": 2, "time": "12:00", "lights": "off", "env": {...}, "gap": 0.8}

Every entry with a ``plan`` is imported (``satk_blender.importer``) under an empty ``SATK_look_entry.<key>``;
an entry ``scene`` is the scene as it is (a studio session): its wheel is put on every wheel dummy and its
paint keys get the like vehicle's colours (``paint``) for the render only, the way an imported vehicle is
shown. Several entries stand side by side across each view at the same scale (a lineup), on one ground.
Cells: one row per (state, pass), one column per view; each cell is a PNG (lossless; the satk side composes
the JPEG sheet). Imported entries are removed again in a session (``cleanup``), and the look is restored.
A building's LOD slot (``satk_slot`` = ``lod``) is the ``vlo`` state, never drawn in ``ok``.
"""

from __future__ import annotations

import fnmatch
import json
import math
import os
import time

import bpy
from mathutils import Vector

from satk.look import gamelook as G

from . import api
from . import lights as Lt
from . import materials as M

__all__ = ["STATES", "run", "set_state", "is_col", "is_low", "default_hidden", "col_proxies", "remove_proxies"]

STATES = ("ok", "dam", "vlo", "col")
ENTRY = "SATK_look_entry"


_COL_NAMES = ("ColMesh", "ShadowMesh", "ColBox", "ColSphere")  # the names DragonFF gives imported COL objects


def is_col(o) -> bool:
    if o.get("satk_col_proxy"):
        return True
    try:
        return o.dff.type in ("COL", "SHA")
    except AttributeError:  # DragonFF is not registered: the import names tell
        return any(p in _COL_NAMES for p in o.name.split("."))


def col_proxies(objs) -> list:
    """Temporary meshes for the collision boxes and spheres (DragonFF imports them as empties), so the
    ``col`` state shows them; tagged ``satk_col_proxy`` (:func:`remove_proxies` deletes them)."""
    import bmesh

    out = []
    for o in objs:
        if o.type != "EMPTY" or not is_col(o) or o.empty_display_type not in ("CUBE", "SPHERE"):
            continue
        bm = bmesh.new()
        try:
            r = float(o.empty_display_size or 1.0)
            if o.empty_display_type == "CUBE":
                bmesh.ops.create_cube(bm, size=2.0 * r)
            else:
                bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=r)
            me = bpy.data.meshes.new(f"{o.name}.proxy")
            bm.to_mesh(me)
        finally:
            bm.free()
        p = bpy.data.objects.new(f"{o.name}.proxy", me)
        bpy.context.scene.collection.objects.link(p)
        p.parent = o  # follows the entry when the lineup moves it
        p.matrix_parent_inverse.identity()
        p.matrix_basis.identity()
        p["satk_col_proxy"] = 1
        if o.get("satk_look_entry"):
            p["satk_look_entry"] = o["satk_look_entry"]
        p.hide_render = True
        out.append(p)
    return out


def remove_proxies() -> int:
    objs = [o for o in bpy.data.objects if o.get("satk_col_proxy")]
    meshes = [o.data for o in objs]
    if objs:
        bpy.data.batch_remove(objs)
    dead = [m for m in meshes if m is not None and m.users == 0]
    if dead:
        bpy.data.batch_remove(dead)
    return len(objs)


def _base(name: str) -> str:
    return name.split(".")[0].lower()


def _entry_objects(key: str) -> list:
    return [o for o in bpy.data.objects if o.get("satk_look_entry") == key]


def _scene_objects(scene) -> list:
    return [o for o in scene.objects if not o.get("satk_look_aux") and not o.get("satk_ghost")
            and not o.get("satk_look_entry") and o.type in ("MESH", "EMPTY", "ARMATURE", "FONT", "CURVE")]


def is_helper(o) -> bool:
    """A 2D effect helper of DragonFF (light, particle, ped attractor arrow, text): the game draws no mesh."""
    try:
        return o.dff.type == "2DFX"
    except AttributeError:
        return False


def is_low(o) -> bool:
    """The low LOD: a vehicle's ``*_vlo`` part or a building's LOD slot of a studio kit."""
    return _base(o.name).endswith("_vlo") or o.get("satk_slot") == "lod"


def default_hidden(o) -> bool:
    """Hidden in the ``ok`` state: damage/LOD variants, collision, breakables, gun flashes, 2DFX helpers."""
    n = _base(o.name)
    return (n.endswith("_dam") or is_low(o) or is_col(o) or bool(o.get("satk_breakable"))
            or G.is_gunflash(o.name) or is_helper(o))


def set_state(objs, state: str) -> dict:
    """Show what the game shows in ``state`` (ok | dam | vlo | col); returns ``{"shown", "missing"?}``."""
    for o in objs:  # 2DFX helpers that are not meshes (text, curves) never render
        if o.type not in ("MESH", "EMPTY", "ARMATURE") and is_helper(o):
            o.hide_render = True
    meshes = [o for o in objs if o.type == "MESH"]
    names = {_base(o.name) for o in meshes}
    shown = 0
    missing = False
    for o in meshes:
        n = _base(o.name)
        if state == "ok":
            vis = not default_hidden(o)
        elif state == "dam":
            if n.endswith("_dam"):
                vis = True
            elif n.endswith("_ok") and (n[:-3] + "_dam") in names:
                vis = False
            else:
                vis = not default_hidden(o)
        elif state == "vlo":
            vis = is_low(o)  # the low LOD carries its own wheels
        else:  # col
            vis = is_col(o) and not is_helper(o)
        o.hide_render = not vis
        if vis:
            try:
                o.hide_set(False)
            except RuntimeError:
                pass
        shown += int(vis)
    if state == "dam" and not any(n.endswith("_dam") for n in names):
        missing = True
    if state == "vlo" and not any(is_low(o) for o in meshes):
        missing = True
    if state == "col" and not any(is_col(o) for o in meshes):
        missing = True
    out = {"shown": shown}
    if missing:
        out["missing"] = True
    return out


# --------------------------------------------------------------------------- entries


def _import(entry: dict, first: bool, args: dict) -> list:
    from .. import importer

    plan = {"model": entry["plan"], "source": entry.get("source") or "file", "profile": entry.get("profile")}
    before = {o.name for o in bpy.data.objects}
    r = importer.import_model(plan, {"clean": first, "col": bool(args.get("col")),
                                     "balance": float(args.get("balance") or 0.0)})
    new = [o for o in bpy.data.objects if o.name not in before]
    key = entry["key"]
    root = bpy.data.objects.new(f"{ENTRY}.{key}", None)
    bpy.context.scene.collection.objects.link(root)
    root["satk_look_entry"] = key
    root["satk_look_root"] = 1
    if entry["plan"].get("sec") == "peds" or any(o.type == "ARMATURE" for o in new):
        # DragonFF imports a ped in its bind pose lying along +X (head), facing +Y: stand it up on +Z
        root.rotation_euler = (0.0, -math.pi / 2, 0.0)
    paint = (entry["plan"].get("vehicle") or {}).get("colors")
    for o in new:
        o["satk_look_entry"] = key
        if paint:
            o["satk_paint"] = json.dumps(paint)
        if o.parent is None:
            o.parent = root
    return [w for w in (r.get("warnings") or [])]


def _scene_prepare(scene, ent: dict) -> tuple[list, list[str]]:
    """A session scene as the game shows it, for the render only: the wheel on every wheel dummy and the
    like vehicle's paint (``ent["paint"]``). Returns ``(temporary objects, objects given a paint)``."""
    from .. import importer

    objs = _scene_objects(scene)
    before = {o.name for o in bpy.data.objects}
    try:
        importer.clone_wheels(objs)
    except Exception:  # noqa: BLE001 - a preview without the extra wheels is still a preview
        pass
    tmp = [o for o in bpy.data.objects if o.name not in before]
    for o in tmp:
        o["satk_look_tmp"] = 1
    painted: list[str] = []
    if ent.get("paint"):
        v = json.dumps(ent["paint"])
        for o in objs + tmp:
            if o.type == "MESH" and "satk_paint" not in o:
                o["satk_paint"] = v
                painted.append(o.name)
    return tmp, painted


def _scene_restore(tmp: list, painted: list[str]) -> None:
    alive = [o for o in tmp if _alive(o)]
    if alive:
        bpy.data.batch_remove(alive)
    for n in painted:
        o = bpy.data.objects.get(n)
        if o is not None and "satk_paint" in o:
            del o["satk_paint"]


def _groups(scene, entries: list) -> list[tuple[str, list]]:
    out = []
    for e in entries:
        if e.get("scene"):
            out.append((e["key"], _scene_objects(scene)))
        else:
            out.append((e["key"], _entry_objects(e["key"])))
    return out


def _root(key: str):
    return bpy.data.objects.get(f"{ENTRY}.{key}")


def _layout(groups: list, view, gap: float) -> None:
    """Put the imported entries side by side across ``view`` (the scene entry stays where it is)."""
    import numpy as np

    if len(groups) < 2:
        return
    d, _ortho, _az = api._view_dir(view)
    fwd = -d
    up_hint = Vector((0.0, 1.0, 0.0)) if abs(d.z) > 0.999 else Vector((0.0, 0.0, 1.0))
    right = fwd.cross(up_hint)
    right.z = 0.0
    if right.length < 1e-6:
        right = Vector((1.0, 0.0, 0.0))
    right.normalize()
    R = np.array(tuple(right))
    # extents along `right` with every root back at the origin
    for key, _objs in groups:
        r = _root(key)
        if r is not None:
            r.location = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    ext = []
    zmin = []
    for _key, objs in groups:
        vis = [o for o in objs if o.type == "MESH" and not o.hide_render]
        pts = api.frame_points(vis, max_points=20_000)
        if not pts:
            ext.append((0.0, 0.0, Vector((0.0, 0.0, 0.0))))
            zmin.append(None)
            continue
        P = np.concatenate(pts)
        s = P @ R
        c = (P.min(axis=0) + P.max(axis=0)) / 2
        ext.append((float(s.min()), float(s.max()), Vector((float(c[0]), float(c[1]), 0.0))))
        zmin.append(float(P[:, 2].min()))
    base = next((z for z in zmin if z is not None), 0.0)  # every entry stands on the first one's ground
    perp = Vector((-right.y, right.x, 0.0))
    # the first entry stays; the others follow it along `right`, on its line and its ground
    a0, b0, c0 = ext[0]
    line = c0.dot(perp)
    edge = b0
    for (key, _objs), (a, b, c), z in list(zip(groups, ext, zmin))[1:]:
        r = _root(key)
        if r is None:
            continue
        loc = right * (edge + gap - a) + perp * (line - c.dot(perp))
        loc.z = (base - z) if z is not None else 0.0
        r.location = loc
        edge += gap + (b - a)
    bpy.context.view_layer.update()


def _stats(objs) -> dict:
    """K1 numbers of the visible evaluated meshes of one entry (studio stats when available)."""
    try:
        from ..studio import stats as S  # noqa: PLC0415 - the studio lane's K1 stats
    except ImportError:
        return {}
    vis = [o for o in objs if o.type == "MESH" and not o.hide_render]
    dg = bpy.context.evaluated_depsgraph_get()
    tris = verts = 0
    rows = {}
    for o in vis:
        t, v, _c, extra = S.mesh_counts(o, dg, metrics=True)
        tris += t
        verts += v
        if extra and "warn" not in extra:
            rows[o.name] = (t, extra)
    out: dict = {"geo.tris": tris}
    # tris-weighted means of the per-object shading numbers
    for k in ("shade.normal_bend", "shade.flat_share", "dff.verts_per_tri", "uv.zero_area_share"):
        vals = [(t, e[k]) for t, e in rows.values() if k in e]
        if vals:
            out[k] = round(sum(t * v for t, v in vals) / max(1, sum(t for t, _v in vals)), 3)
    pts = api.frame_points(vis, max_points=20_000)
    if pts:
        import numpy as np

        P = np.concatenate(pts)
        out["dims"] = [round(float(x), 2) for x in (P.max(axis=0) - P.min(axis=0))]
    return out


# --------------------------------------------------------------------------- the job


def _check(spec: dict) -> tuple[list, list, list, list]:
    views = list(spec.get("views") or api.STD_VIEWS)
    for v in views:
        if isinstance(v, str) and v not in api.VIEWS and not (bpy.data.objects.get(v) and bpy.data.objects[v].type == "CAMERA"):
            raise ValueError(f"unknown view {v!r}; one of {', '.join(api.VIEWS)} or a camera name")
    passes = list(spec.get("passes") or ["game"])
    states = list(spec.get("states") or ["ok"])
    for s in states:
        if s not in STATES:
            raise ValueError(f"unknown state {s!r}; one of {', '.join(STATES)}")
    for p in passes:
        if p not in G.LOOKS:
            raise ValueError(f"unknown pass {p!r}; one of {', '.join(G.LOOKS)}")
    times = list(spec.get("times") or [spec.get("time") or "12:00"])
    return views, passes, states, times


def _envs(spec: dict, times: list) -> list[dict]:
    given = spec.get("envs") or []
    out = []
    for i, t in enumerate(times):
        e = given[i] if i < len(given) and isinstance(given[i], dict) else None
        out.append(e or Lt.env(t, spec.get("weather") or G.DEFAULT_WEATHER, spec.get("profile") or "vanilla"))
    return out


def _area(spec: dict, warn: list) -> tuple[list, list]:
    """Import the map around a placement; returns ``(focus objects, context box points)``."""
    import numpy as np

    from .. import importer

    a = spec["area"]
    r = importer.import_area(a["plan"], {"clean": True, "balance": 0.0, "lod": "hd"})
    warn += r.get("warnings") or []
    focus = str(a.get("focus") or "")
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH" and str(o.get("satk_sid") or "") == focus
            and not o.hide_render]
    if not objs:
        raise ValueError(f"the area import has no visible object of {focus}")
    pts = api.frame_points(objs, max_points=20_000)
    P = np.concatenate(pts)
    lo, hi = P.min(axis=0), P.max(axis=0)
    m = float(a.get("margin") or 12.0)
    box = np.array([[x, y, z] for x in (lo[0] - m, hi[0] + m) for y in (lo[1] - m, hi[1] + m)
                    for z in (lo[2], hi[2])], dtype=np.float64)
    return objs, [box]


def run(spec: dict, out_dir: str, *, session: bool = False) -> dict:
    """Render the cells of a preview; returns ``{"cells", "rows", "cols", "stats", "warnings", "seconds"}``.

    Rows: every (state, pass, time); columns: the views. ``spec["area"]`` (``{"plan", "focus", "margin"}``)
    renders a placement in its map context instead of entries (states and lineups do not apply).
    """
    t0 = time.perf_counter()
    scene = bpy.context.scene
    api.ensure_dragonff()
    entries = list(spec.get("entries") or [])
    area = spec.get("area")
    if not entries and not area:
        raise ValueError("preview: no entries")
    views, passes, states, times = _check(spec)
    envs = _envs(spec, times)
    sz = spec.get("size") or 256
    size = (int(sz[0]), int(sz[1])) if isinstance(sz, (list, tuple)) else (int(sz), int(sz))
    gap = float(spec.get("gap") or 0.8)
    dirt = float(spec.get("dirt", G.DIRT_DEFAULT))
    lights = spec.get("lights") or "off"
    warn: list[str] = []
    imported: list[str] = []
    extra_pts: list = []
    scene_tmp: list = []
    painted: list[str] = []
    t_imp = time.perf_counter()
    if session and not area:
        for ent in entries:
            if ent.get("scene"):
                scene_tmp, painted = _scene_prepare(scene, ent)
    if area:
        focus, extra_pts = _area(spec, warn)
        groups = [("area", focus)]
        states = ["ok"]
    else:
        first = not session
        for ent in entries:
            if ent.get("scene"):
                continue
            warn += _import(ent, first, {"col": "col" in states, "balance": envs[0].get("balance", 0.0)})
            imported.append(ent["key"])
            first = False
        groups = _groups(scene, entries)
        if "col" in states:
            bpy.context.view_layer.update()
            groups = [(k, objs + col_proxies(objs)) for k, objs in groups]
    t_imp = time.perf_counter() - t_imp
    all_objs = [o for _k, objs in groups for o in objs]
    saved_vis = {o.name: o.hide_render for o in all_objs}  # a session scene gets its visibility back
    cells: list = []
    cell_s: list = []
    stats: dict = {}
    rows: list[str] = []
    cols = [v if isinstance(v, str) else f"v{i}" for i, v in enumerate(views)]
    t_render = time.perf_counter()
    try:
        if not area:
            for st in states:
                for key, objs in groups:
                    r = set_state(objs, st)
                    if r.get("missing"):
                        label = next((x.get("label") or x["key"] for x in entries if x["key"] == key), key)
                        warn.append(f"NOT_FOUND: {label} has no {st} parts")
                if st == "ok":
                    for ent, (key, objs) in zip(entries, groups):
                        if ent.get("scene"):  # files and SIDs: satk computes K1 from the DFF bytes
                            stats[key] = _stats([o for o in objs if not o.get("satk_look_tmp")])
        ri = 0
        for st in states:
            if not area:
                for key, objs in groups:
                    set_state(objs, st)
            for ps in passes:
                for tm, e in zip(times, envs):
                    rows.append("/".join([st, ps] + ([e.get("time") or str(tm)] if len(times) > 1 else [])))
                    api.apply(scene, ps, dirt=dirt, lights=lights, env=e, ground=False,
                              objects=None if area else [o for o in all_objs if o.type == "MESH"],
                              groups=None if area else [objs for _k, objs in groups])
                    for ci, v in enumerate(views):
                        if not area:
                            _layout(groups, v, gap)
                        vis = [o for _k, objs in groups for o in objs if o.type == "MESH" and not o.hide_render]
                        if ps == "game" and not area:
                            Lt.ground(scene, api.footprints(vis), M.ground_material(e),
                                      shadows=(ps == "game" and st != "col"))
                        tc = time.perf_counter()
                        p = api.render_views(scene, [v], size, fmt="png", out_dir=out_dir, prefix=f"r{ri:02d}c{ci:02d}",
                                             objects=vis, extra_points=extra_pts)[0]
                        cells.append([ri, ci, p])
                        cell_s.append(round(time.perf_counter() - tc, 2))
                    ri += 1
    finally:
        api.restore(scene)
        remove_proxies()
        _scene_restore(scene_tmp, painted)
        all_objs = [o for o in all_objs if _alive(o)]
        for o in all_objs:
            if o.name in saved_vis:
                try:
                    o.hide_render = saved_vis[o.name]
                except ReferenceError:
                    pass
        if session:
            _cleanup(imported)
    return {"cells": cells, "rows": rows, "cols": cols, "stats": stats, "warnings": warn,
            "seconds": {"import": round(t_imp, 2), "render": round(time.perf_counter() - t_render, 2),
                        "total": round(time.perf_counter() - t0, 2), "cells": cell_s},
            "env": [{k: e.get(k) for k in ("time", "balance", "weather", "source")} for e in envs]}


def _alive(o) -> bool:
    try:
        return bool(o.name)
    except ReferenceError:
        return False


def _cleanup(keys: list[str]) -> int:
    """Delete the imported entries (session mode) and their meshes, materials and images when nothing
    else uses them (the session's own data is never touched)."""
    objs = [o for o in bpy.data.objects if o.get("satk_look_entry") in keys or
            any(fnmatch.fnmatchcase(o.name, f"{ENTRY}.{k}") for k in keys)]
    colls = {c for o in objs for c in o.users_collection if c is not bpy.context.scene.collection}
    meshes = {o.data for o in objs if o.type == "MESH" and o.data is not None}
    arms = {o.data for o in objs if o.type == "ARMATURE" and o.data is not None}
    mats = {m for me in meshes for m in me.materials if m is not None}
    imgs = {n.image for m in mats if m.node_tree for n in m.node_tree.nodes
            if n.type == "TEX_IMAGE" and n.image is not None}
    n = len(objs)
    if objs:
        bpy.data.batch_remove(objs)
    for c in sorted(colls, key=lambda c: -len(c.name)):
        try:
            if not c.all_objects:
                bpy.data.collections.remove(c)
        except ReferenceError:
            pass
    for group in (meshes, arms, mats, imgs):
        dead = [x for x in group if x.users == 0]
        if dead:
            bpy.data.batch_remove(dead)
    from .. import common

    common._txd_cache.clear()  # noqa: SLF001 - it held images that are gone now
    return n
