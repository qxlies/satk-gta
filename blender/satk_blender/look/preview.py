# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""The preview job behind ``satk blender preview`` (cold job ``preview``) and the studio method ``look.preview``.

``spec["ref"]`` (``--ref``): a true-view photo placed behind the subject for its view (``side``, ``front`` or
``rear``) at 30 %, in the game pass only; its object box spans the subject's length (or width) and stands on the
subject's ground (:mod:`.silhouette`).

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

__all__ = ["STATES", "run", "set_state", "is_col", "is_low", "default_hidden", "col_proxies", "remove_proxies",
           "scene_prepare", "scene_restore"]

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
            and not o.get("satk_look_entry") and not o.get("satk_ref")
            and o.type in ("MESH", "EMPTY", "ARMATURE", "FONT", "CURVE")]


def is_helper(o) -> bool:
    """A 2D effect helper of DragonFF (light, particle, ped attractor arrow, text): the game draws no mesh."""
    try:
        return o.dff.type == "2DFX"
    except AttributeError:
        return False


def is_low(o) -> bool:
    """The low LOD: a vehicle's ``*_vlo`` part or a building's LOD slot of a studio kit."""
    return _base(o.name).endswith("_vlo") or o.get("satk_slot") == "lod"


#: Atomics the game shows only while a rotor or a propeller spins fast (the blurred disc); a parked vehicle shows
#: its static rotor or prop instead.
_SPINNING = ("moving_rotor", "moving_rotor2", "moving_prop", "moving_prop2")


def is_spinning(o) -> bool:
    return _base(o.name) in _SPINNING


def default_hidden(o) -> bool:
    """Hidden in the ``ok`` state: damage/LOD variants, collision, breakables, gun flashes, 2DFX helpers and the
    spinning-rotor discs (the parked state)."""
    n = _base(o.name)
    return (n.endswith("_dam") or is_low(o) or is_col(o) or bool(o.get("satk_breakable"))
            or G.is_gunflash(o.name) or is_helper(o) or n in _SPINNING)


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
    if first:
        # clean before taking the names: a part named like a startup object (the weapon "camera") gets the name of
        # the deleted startup object and would not count as new
        from .. import common

        common.clean_scene()
    before = {o.name for o in bpy.data.objects}
    r = importer.import_model(plan, {"clean": False, "col": bool(args.get("col")),
                                     "balance": float(args.get("balance") or 0.0)})
    new = [o for o in bpy.data.objects if o.name not in before]
    if entry.get("lod_of"):
        # the LOD model of a map model: part of its subject, drawn only in the vlo state (satk_slot lod)
        owner = entry["lod_of"]
        root = bpy.data.objects.get(f"{ENTRY}.{owner}")
        for o in new:
            o["satk_look_entry"] = owner
            o["satk_slot"] = "lod"
            o.hide_render = True
            if o.parent is None and root is not None:
                o.parent = root
        return [w for w in (r.get("warnings") or [])]
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


def scene_prepare(scene, ent: dict) -> tuple[list, list[str]]:
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


def scene_restore(tmp: list, painted: list[str]) -> None:
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
        if e.get("lod_of"):
            continue                    # imported into its owner's group
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
        if p not in G.LOOKS + ("leak",):
            raise ValueError(f"unknown pass {p!r}; one of {', '.join(G.LOOKS + ('leak',))}")
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
                scene_tmp, painted = scene_prepare(scene, ent)
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
            if not ent.get("lod_of"):
                imported.append(ent["key"])
            first = False
        groups = _groups(scene, entries)
        if "col" in states:
            bpy.context.view_layer.update()
            groups = [(k, objs + col_proxies(objs)) for k, objs in groups]
    t_imp = time.perf_counter() - t_imp
    all_objs = [o for _k, objs in groups for o in objs]
    ref = spec.get("ref") if not area else None
    ref_ob = None
    if ref:
        from . import silhouette as SIL

        subj = [o for o in groups[0][1] if o.type == "MESH" and not default_hidden(o) and not o.get("satk_ref")]
        try:
            ref_ob = SIL.ref_plane(scene, subj, ref)
        except (RuntimeError, ValueError, KeyError) as e:
            warn.append(f"BAD_PARAMS: the reference photo could not be placed ({type(e).__name__}: {e})"[:200])
        if ref_ob is not None:
            ref_ob.hide_render = True
    saved_vis = {o.name: o.hide_render for o in all_objs}  # a session scene gets its visibility back
    cells: list = []
    cell_s: list = []
    stats: dict = {}
    rows: list[str] = []
    regions = spec.get("regions") if not area else None
    leak_cfg = dict(spec.get("leak") or {})
    leak_views: list[dict] = []
    view_meta: list[dict] = []
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
        if regions:
            from . import regions as RG

            for key, objs in groups:
                set_state(objs, "ok")
            subj = groups[0][1]
            vis0 = [o for o in subj if o.type == "MESH" and not o.hide_render]
            views, notes = RG.expand(regions, vis0, ref=spec.get("region_ref"), frame_objs=subj)
            warn += notes
            if not views:
                raise ValueError("no region view could be framed")
            # a region may ask for more states (a building's lod region: ok and vlo); a state the subject does not
            # have is left out with a note, never drawn as an empty cell
            extra = [x for v in views for x in (v.get("states") or []) if x not in states]
            for x in dict.fromkeys(extra):
                if set_state(subj, x).get("missing"):
                    warn.append(f"NOT_FOUND: {entries[0].get('label') or 'the subject'} has no {x} model: the "
                                "region shows the other states only")
                    views = [dict(v, states=[s_ for s_ in v["states"] if s_ != x]) if v.get("states") else v
                             for v in views]
                else:
                    states = list(states) + [x]
            set_state(subj, "ok")
            view_meta = [{k: v[k] for k in ("label", "region", "az", "el", "ortho", "fov", "inside", "leak")
                          if k in v} for v in views]
        cols = [v if isinstance(v, str) else (str(v.get("label")) if isinstance(v, dict) and v.get("label")
                                              else f"v{i}") for i, v in enumerate(views)]
        ri = 0
        for st in states:
            if not area:
                for key, objs in groups:
                    set_state(objs, st)
            leak_sc = None
            for ps in passes:
                for tm, e in zip(times, envs):
                    rows.append("/".join([st, ps] + ([e.get("time") or str(tm)] if len(times) > 1 else [])))
                    if ps != "leak":
                        api.apply(scene, ps, dirt=dirt, lights=lights, env=e, ground=False,
                                  objects=None if area else [o for o in all_objs if o.type == "MESH"],
                                  groups=None if area else [objs for _k, objs in groups])
                    for ci, v in enumerate(views):
                        vst = v.get("states") if isinstance(v, dict) else None
                        if vst and st not in vst or (not vst and regions and st not in (spec.get("states") or ["ok"])):
                            continue
                        if ps == "leak" and isinstance(v, dict) and v.get("leak") is False:
                            continue
                        if not area:
                            _layout(groups, v, gap)
                        if ref_ob is not None:                 # the photo only behind its own view, game look
                            ref_ob.hide_render = not (ps == "game" and _ref_view(v) == ref.get("view"))
                        hidden = _view_hide(groups[0][1] if groups else [], v)
                        glass = _view_glass(v) if ps == "game" else []
                        try:
                            vis = [o for _k, objs in groups for o in objs if o.type == "MESH" and not o.hide_render]
                            tc = time.perf_counter()
                            if ps == "leak":
                                if leak_sc is None or len(groups) > 1:   # a lineup moves the entries per view
                                    leak_sc = _leak_scene(groups, leak_cfg)
                                p, lv = _leak_cell(scene, leak_sc, v, groups, size, out_dir, f"r{ri:02d}c{ci:02d}",
                                                   leak_cfg)
                                lv.update(view=cols[ci], state=st)
                                if isinstance(v, dict) and v.get("region"):
                                    lv["region"] = v["region"]
                                leak_views.append(lv)
                            else:
                                if ps == "game" and not area and not (isinstance(v, dict) and v.get("ground") is False):
                                    Lt.ground(scene, api.footprints(vis), M.ground_material(e),
                                              shadows=(ps == "game" and st != "col"))
                                elif ps == "game":
                                    Lt.remove_ground()
                                p = api.render_views(scene, [v], size, fmt="png", out_dir=out_dir,
                                                     prefix=f"r{ri:02d}c{ci:02d}", objects=vis,
                                                     extra_points=extra_pts)[0]
                        finally:
                            _unhide(hidden)
                            _unglass(glass)
                        cells.append([ri, ci, p])
                        cell_s.append(round(time.perf_counter() - tc, 2))
                    ri += 1
    finally:
        if ref_ob is not None:
            from . import silhouette as SIL

            SIL.remove_ref(ref_ob)
        api.restore(scene)
        remove_proxies()
        scene_restore(scene_tmp, painted)
        all_objs = [o for o in all_objs if _alive(o)]
        for o in all_objs:
            if o.name in saved_vis:
                try:
                    o.hide_render = saved_vis[o.name]
                except ReferenceError:
                    pass
        if session:
            _cleanup(imported)
    out_extra: dict = {}
    if view_meta:
        out_extra["views"] = view_meta
    if leak_views:
        from . import leak as LK

        out_extra["leak"] = {"views": [{k: v for k, v in lv.items() if k != "cell"} for lv in leak_views],
                             "gaps": LK.merge(leak_views), "info": LK.merge(leak_views, blocking=False),
                             "params": leak_cfg}
    return {**out_extra, "cells": cells, "rows": rows, "cols": cols, "stats": stats, "warnings": warn,
            "seconds": {"import": round(t_imp, 2), "render": round(time.perf_counter() - t_render, 2),
                        "total": round(time.perf_counter() - t0, 2), "cells": cell_s},
            "env": [{k: e.get(k) for k in ("time", "balance", "weather", "source")} for e in envs]}


def _ref_view(v) -> str | None:
    """The reference view a preview view shows (``side``/``left`` = side, ``front``, ``rear``)."""
    if not isinstance(v, str):
        return None
    return {"side": "side", "left": "side", "front": "front", "rear": "rear"}.get(v)


def _view_hide(objs, v) -> list:
    """Hide the objects a region view names in ``hide`` (doors of an interior cutaway); returns them."""
    if not isinstance(v, dict) or not v.get("hide"):
        return []
    from .regions import objects_matching

    out = [o for o in objects_matching(objs, v["hide"]) if not o.hide_render]
    for o in out:
        o.hide_render = True
    return out


def _unhide(objs) -> None:
    for o in objs:
        try:
            o.hide_render = False
        except ReferenceError:
            pass


def _view_glass(v) -> list:
    """``glass: hide`` of a region view: glass materials of the game look drawn fully transparent."""
    if not isinstance(v, dict) or v.get("glass") != "hide":
        return []
    out = []
    for m in bpy.data.materials:
        g = m.node_tree.nodes.get(M.PREFIX) if m.get("satk_look") and m.node_tree else None
        if g is None or "Material Alpha" not in g.inputs:
            continue
        a = g.inputs["Material Alpha"]
        if float(a.default_value) < 0.995:
            out.append((m, float(a.default_value)))
            a.default_value = 0.0
    return out


def _unglass(saved) -> None:
    for m, val in saved:
        try:
            m.node_tree.nodes[M.PREFIX].inputs["Material Alpha"].default_value = val
        except (KeyError, ReferenceError, AttributeError):
            pass


def _leak_scene(groups, cfg: dict):
    from . import leak as LK

    subj = [o for o in groups[0][1] if o.type == "MESH" and not o.hide_render and not is_helper(o)]
    return LK.build(subj, cull=bool(cfg.get("cull")), floor_exclude=tuple(cfg.get("floor_exclude") or ()),
                    floor_pct=float(cfg.get("floor_pct", 1.0)),
                    inside_exclude=tuple(cfg.get("inside_exclude", ("wheel*",)) or ()))


def _leak_cell(scene, sc, v, groups, size, out_dir: str, prefix: str, cfg: dict) -> tuple[str, dict]:
    """The leak cell of one view (the subject only, framed as in the other passes) and its gaps."""
    from . import leak as LK

    subj = [o for o in groups[0][1] if o.type == "MESH" and not o.hide_render]
    pts = v.get("points") if isinstance(v, dict) and v.get("points") is not None else api.frame_points(subj)
    cam = api.place_camera(scene, v, pts, aspect=size[0] / size[1])
    bpy.context.view_layer.update()            # matrix_world of the moved camera
    name = v if isinstance(v, str) else str(v.get("label") or "view") if isinstance(v, dict) else "view"
    path = os.path.join(out_dir, f"{prefix}_leak_{name}.png")
    os.makedirs(out_dir, exist_ok=True)
    vin = not (isinstance(v, dict) and v.get("leak_inside") is False)
    region = v.get("region") if isinstance(v, dict) else None
    kinds = {"gap": bool(cfg.get("gap", True)), "inside": bool(cfg.get("inside", False)) and not sc.cull and vin,
             "slit": (bool(v["slit"]) if isinstance(v, dict) and v.get("slit") is not None else
                      bool(cfg.get("slit", True)) and not (isinstance(v, dict) and (v.get("inside")
                                                                                    or float(v.get("el", 0)) < -10))),
             "slit_max_m": cfg.get("slit_max_m"), "slit_min_cm2": cfg.get("slit_min_cm2"),
             "inside_block": region in (cfg.get("inside_regions") or ()),
             "see_through_block": bool(cfg.get("see_through_block", True)),
             "escape_max_m": cfg.get("escape_max_m"),
             "see_through": sc.cull, "inside_depth": float(cfg.get("inside_depth", 1.0)),
             "inside_cm2": float(cfg.get("inside_cm2", 60.0))}
    r = LK.run_view(scene, sc, cam, size, inside=bool(isinstance(v, dict) and v.get("inside")), kinds=kinds,
                    min_px=int(cfg.get("min_px", 6)), min_cm2=float(cfg.get("min_cm2", 2.0)), path=path)
    return r["cell"], r


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
