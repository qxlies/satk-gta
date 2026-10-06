# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""SATK add-on: N-panel "SATK" in the 3D viewport (for people; agents use ``agent_cli.py``).

* search the satk index (``asset_find``) and import a model;
* import an area by coordinates, zone or viewer bookmark;
* "Game shading" switch and a game-time slider (day/night prelight);
* hide/show vehicle ``_dam``/``_vlo`` parts;
* export the selection as an MTA resource or a modloader folder (``work/out/exports``).

It needs the satk sources (preference ``satk_src``) and DragonFF (an installed DragonFF extension
or the ``work/blender`` copy made by ``satk blender doctor``). Installing into a Blender profile is
the user's decision; build the zip with ``satk blender addon-build``.
"""

from __future__ import annotations

import os
import sys

# Blender runs Python with ignore_environment (sys.flags.dont_write_bytecode stays 0), so honour
# PYTHONDONTWRITEBYTECODE (set by satk's runner and tests) by hand before the submodules, satk and
# DragonFF are imported: no __pycache__ in the tools checkout or in work/blender/dragonff.
if os.environ.get("PYTHONDONTWRITEBYTECODE"):
    sys.dont_write_bytecode = True

import bpy  # noqa: E402
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, StringProperty  # noqa: E402

def _default_src() -> str:
    """``SATK_SRC``, else ``<checkout>/src`` when the add-on runs from a satk checkout
    (``<checkout>/blender/satk_blender``), else empty (set the preference)."""
    env = os.environ.get("SATK_SRC")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(os.path.dirname(os.path.dirname(here)), "src")
    return src if os.path.isfile(os.path.join(src, "satk", "__init__.py")) else ""


_DEF_SRC = _default_src()
#: Empty = the satk default ``<work>/blender`` (``common.default_dragonff_root``) or ``SATK_DRAGONFF``.
_DEF_DFF = os.environ.get("SATK_DRAGONFF", "")


def _prefs():
    pkg = __package__ or "satk_blender"
    a = bpy.context.preferences.addons.get(pkg)
    return a.preferences if a else None


def _setup():
    p = _prefs()
    src = (p.satk_src if p else "") or _DEF_SRC
    if src and src not in sys.path:
        sys.path.insert(0, src)
    try:
        import satk  # noqa: F401
    except ImportError:
        raise RuntimeError("satk sources not found: set the add-on preference 'satk src' to <checkout>/src "
                           "(or SATK_SRC)") from None
    from . import common

    dff = (p.dragonff_dir if p else "") or _DEF_DFF
    common.load_dragonff(None if _installed_dragonff() else (dff or common.default_dragonff_root()))
    return common


def _installed_dragonff() -> bool:
    return any(n.startswith("bl_ext.") and n.endswith(".dragonff") for n in sys.modules)


def _profile() -> str:
    p = _prefs()
    return p.profile if p else "vanilla"


def _balance(hours: float) -> float:
    h = int(hours) % 24
    m = int(round((hours - int(hours)) * 60)) % 60
    try:
        from satk.blender.contract import day_night_balance
    except ImportError:
        return 0.0
    return day_night_balance(h, m)


def _report_error(op, e: Exception) -> set:
    code = getattr(e, "code", type(e).__name__)
    op.report({"ERROR"}, f"{code}: {getattr(e, 'msg', None) or e}")
    return {"CANCELLED"}


# --------------------------------------------------------------------------- preferences / properties


class SATK_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__ or "satk_blender"

    satk_src: StringProperty(name="satk src", subtype="DIR_PATH", default=_DEF_SRC,
                             description="src folder of the satk checkout (MIT library, stdlib only); "
                                         "empty = the checkout this add-on runs from, or SATK_SRC")
    dragonff_dir: StringProperty(name="DragonFF parent", subtype="DIR_PATH", default=_DEF_DFF,
                                 description="Folder that contains the 'dragonff' package (satk blender doctor makes "
                                             "it); empty = <work>/blender of the satk workspace, or SATK_DRAGONFF")
    profile: StringProperty(name="Profile", default="vanilla", description="satk load profile (vanilla|installed|samp)")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "satk_src")
        col.prop(self, "dragonff_dir")
        col.prop(self, "profile")


class SATK_SearchItem(bpy.types.PropertyGroup):
    sid: StringProperty()
    kind: StringProperty()
    info: StringProperty()


def _upd_shading(self, context):
    try:
        from . import shading

        shading.set_game_shading(self.game_shading)
    except Exception:  # noqa: BLE001 - UI callback
        pass


def _upd_time(self, context):
    try:
        from . import render, shading

        b = _balance(self.time)
        if bpy.data.node_groups.get(shading.GROUP):
            shading.set_time(b)
        render.setup_world(context.scene, b)
    except Exception:  # noqa: BLE001 - UI callback
        pass


class SATK_Props(bpy.types.PropertyGroup):
    query: StringProperty(name="Search", description="Name to look for in the satk index (3+ characters)")
    kind: EnumProperty(name="Kind", items=[("model", "Models", ""), ("tex", "Textures", ""), ("ipl", "IPL", ""),
                                           ("zone", "Zones", ""), ("any", "Any", "")], default="model")
    results: CollectionProperty(type=SATK_SearchItem)
    model_id: StringProperty(name="Model", default="model:411", description="model:411, model:infernus, 411 or a name")
    center_mode: EnumProperty(name="Centre", items=[("xy", "Coordinates", ""), ("zone", "Zone", ""), ("bm", "Bookmark", "")])
    x: FloatProperty(name="X", default=2495.0)
    y: FloatProperty(name="Y", default=-1687.0)
    zone: StringProperty(name="Zone", default="GAN1",
                         description="Zone name (index zone: SID or data/info.zon); the box is centred on the zone")
    bookmark: StringProperty(name="Bookmark", default="grove_center")
    size: FloatProperty(name="Box", default=100.0, min=1.0, max=2000.0, description="Side of the square (m)")
    lod: EnumProperty(name="LOD", items=[("hd", "HD", ""), ("lod", "LOD", ""), ("all", "All", "")], default="hd")
    any_area: BoolProperty(name="Any interior", default=False)
    area: IntProperty(name="Area", default=0, min=0, max=255)
    col: BoolProperty(name="Collisions", default=False)
    game_shading: BoolProperty(name="Game shading", default=True, update=_upd_shading)
    time: FloatProperty(name="Time", default=12.0, min=0.0, max=23.99, update=_upd_time, description="Game time (hours)")
    target: EnumProperty(name="Target", items=[("mta-resource", "MTA resource", ""), ("modloader", "modloader", "")])
    export_name: StringProperty(name="Name", default="", description="Folder/resource name (default: first model)")
    gr_name: StringProperty(name="Name", default="",
                            description="Model name, 1-21 characters a-z 0-9 _ (default: the active object's name)")
    gr_budget: IntProperty(name="Budget", default=1040, min=12, max=60000,
                           description="HD triangle budget (vanilla map models: p50 216, p90 1040)")
    gr_tex: EnumProperty(name="Texture", default="256", description="Largest texture side",
                         items=[(s, s, "") for s in ("64", "128", "256", "512", "1024")])
    gr_prelight: EnumProperty(name="Prelight", default="bake", items=[
        ("bake", "Bake AO", "Cycles AO + sky/sun (day) and dark ambient (night)"),
        ("simple", "Simple", "From normals only (fast)"), ("none", "None", "No vertex colours")])
    gr_col: EnumProperty(name="COL", default="hull", items=[
        ("hull", "Hull", "Convex hull"), ("box", "Box", "Axis-aligned box"), ("mesh", "Mesh", "The decimated mesh"),
        ("none", "None", "No collision")])
    gr_surface: IntProperty(name="Surface", default=0, min=0, max=178, description="COL surface (eSurfaceType)")
    gr_lod: FloatProperty(name="LOD", default=0.25, min=0.0, max=0.99, description="Share of triangles in the LOD (0 = none)")


# --------------------------------------------------------------------------- operators


class SATK_OT_search(bpy.types.Operator):
    bl_idname = "satk.search"
    bl_label = "Search"
    bl_description = "Search the satk index"

    def execute(self, context):
        p = context.scene.satk
        try:
            _setup()
            from satk.index.api import open_index

            env = open_index(_profile()).find(p.query, None if p.kind == "any" else p.kind, limit=20)
        except Exception as e:  # noqa: BLE001
            return _report_error(self, e)
        p.results.clear()
        cols = env.get("cols", [])
        for row in env.get("rows", []):
            d = dict(zip(cols, row))
            it = p.results.add()
            it.sid, it.kind, it.info = str(d.get("id")), str(d.get("kind", "")), f"{d.get('name', '')} {d.get('info', '')}"
        self.report({"INFO"}, f"{len(p.results)} result(s)")
        return {"FINISHED"}


class SATK_OT_import_model(bpy.types.Operator):
    bl_idname = "satk.import_model"
    bl_label = "Import Model"
    bl_description = "Import one model (DFF + TXD chain from the game IMGs) into the current scene"
    bl_options = {"REGISTER", "UNDO"}

    sid: StringProperty(default="")

    def execute(self, context):
        p = context.scene.satk
        try:
            _setup()
            from satk.blender.resolve import plan_model

            from . import importer

            plan = plan_model(self.sid or p.model_id, profile=_profile(), col=p.col)
            r = importer.import_model(plan, {"clean": False, "col": p.col, "balance": _balance(p.time)})
        except Exception as e:  # noqa: BLE001
            return _report_error(self, e)
        st = r["stats"]
        self.report({"INFO"}, f"{st['name']}: {st['objects']} objects, {st['images']} images, missing tex {st['missing_tex']}")
        return {"FINISHED"}


class SATK_OT_import_area(bpy.types.Operator):
    bl_idname = "satk.import_area"
    bl_label = "Import Area"
    bl_description = "Import the placements of an area (linked duplicates, packed textures)"
    bl_options = {"REGISTER", "UNDO"}

    def _center(self, p):
        if p.center_mode == "xy":
            return [p.x, p.y]
        if p.center_mode == "bm":
            from satk.viewer import store

            pos = store.get(p.bookmark)["pose"]["pos"]
            return [pos[0], pos[1]]
        from satk.blender.resolve import zone_rect

        # zone:<name> of the index (min/max), or data/info.zon + map.zon when there is no index
        return zone_rect(p.zone, profile=_profile())["center"]

    def execute(self, context):
        p = context.scene.satk
        try:
            _setup()
            from satk.blender.contract import normalize_args
            from satk.blender.resolve import plan_area

            from . import importer

            a = normalize_args("import_area", {"center": self._center(p), "box": p.size, "lod": p.lod,
                                               "area": None if p.any_area else p.area, "col": p.col})
            plan = plan_area(center=a["center"], box=a["box"], match=a["match"], area=a["area"], lod=a["lod"],
                             col=a["col"], limit=a["limit"], profile=_profile())
            r = importer.import_area(plan, dict(a, clean=False, balance=_balance(p.time)))
        except Exception as e:  # noqa: BLE001
            return _report_error(self, e)
        st = r["stats"]
        self.report({"INFO"}, f"{st['instances']} placements, {st['models']} models, missing tex {st['missing_tex']}")
        return {"FINISHED"}


class SATK_OT_toggle_damage(bpy.types.Operator):
    bl_idname = "satk.toggle_damage"
    bl_label = "Hide/Show _dam/_vlo"
    bl_description = "Toggle vehicle damage and low-detail parts (the game hides them)"

    def execute(self, context):
        from . import common

        parts = [o for o in bpy.data.objects if common.is_damage_part(o.name)]
        hide = not all(o.hide_render for o in parts) if parts else True
        for o in parts:
            o.hide_render = hide
            o.hide_set(hide)
        self.report({"INFO"}, f"{len(parts)} part(s) {'hidden' if hide else 'shown'}")
        return {"FINISHED"}


class SATK_OT_export(bpy.types.Operator):
    bl_idname = "satk.export"
    bl_label = "Export Selection"
    bl_description = "Export the selected models (DFF/TXD/COL + IDE/IPL, MTA meta.xml/client.lua) to work/out/exports"

    def execute(self, context):
        p = context.scene.satk
        names = [o.name for o in context.selected_objects]
        if not names:
            self.report({"WARNING"}, "select the objects to export")
            return {"CANCELLED"}
        try:
            _setup()
            import json

            from satk.blender import packaging, resolve
            from satk.core.paths import atomic_write

            from . import exporter

            nm = p.export_name.strip() or packaging.default_name(names[0])
            out = packaging.export_dir(nm)  # checks the name; nothing is created before the objects resolve
            # the exporter restores the scene (visibility, selection, shading links) when it is done
            r = exporter.export({"objects": names}, str(out))
            prof = context.scene.get("satk_profile") or _profile()
            warn = resolve.fill_export_defs(r["models"], profile=prof)
            warn += packaging.pack_txds(out, r["models"])      # the TXD of the own textures: DXT through satk.texmod
            atomic_write(out / "export.json", json.dumps({"target": p.target, "name": nm, "profile": prof,
                                                          "models": r["models"]}, ensure_ascii=False, indent=1) + "\n")
            warn += packaging.write_package(out, p.target, nm)["warn"]
        except Exception as e:  # noqa: BLE001
            return _report_error(self, e)
        for w in warn:
            self.report({"WARNING"}, w)
        self.report({"INFO"}, f"exported {len(r['models'])} model(s) to {out}")
        return {"FINISHED"}


class SATK_OT_make_game_ready(bpy.types.Operator):
    bl_idname = "satk.make_game_ready"
    bl_label = "Make Game-Ready"
    bl_description = ("Selected meshes -> SA model: decimate to the budget, UV, day/night prelight, COL, LOD; "
                      "DFF + COL (+ TXD or PNG textures) into work/out/blender/<name>. The selection is not changed")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        p = context.scene.satk
        geo = ("MESH", "CURVE", "SURFACE", "META", "FONT")
        # results of earlier runs (collection SATK_gameready) are never sources
        objs = [o for o in context.selected_objects if o.type in geo and not o.get("satk_gameready")]
        if not objs:
            self.report({"WARNING"}, "select the mesh objects to make game-ready")
            return {"CANCELLED"}
        vl = context.view_layer
        act = vl.objects.active
        selected = [o.name for o in context.selected_objects]
        first = act if act in objs else objs[0]
        try:
            _setup()
            from satk.blender import gameready as mit
            from satk.blender.contract import model_name, normalize_args

            from . import gameready

            a = normalize_args("game_ready", {
                "objects": [first.name] + [o.name for o in objs if o != first],
                "name": p.gr_name.strip() or model_name(first.name), "budget": p.gr_budget,
                "tex_size": int(p.gr_tex), "prelight": p.gr_prelight, "col": p.gr_col, "surface": p.gr_surface,
                "lod": p.gr_lod})
            out = mit.out_dir(a["name"])
            a["out"] = str(out)
            r = gameready.make_game_ready(a, str(out), hide_sources="none")
            fin = mit.finalize(out)
        except Exception as e:  # noqa: BLE001
            return _report_error(self, e)
        finally:  # the selection and the active object are the user's: put them back
            for o in vl.objects:
                o.select_set(o.name in selected)
            vl.objects.active = bpy.data.objects.get(act.name) if act is not None else None
        for w in r["warnings"] + fin["warn"]:
            self.report({"WARNING"}, w)
        st = fin["stats"]
        lod = f", LOD {st['lod_tris']}" if st.get("lod_tris") is not None else ""
        self.report({"INFO"}, f"{a['name']}: {st.get('tris')} tris{lod}, COL {r['manifest']['stats'].get('col')} -> {out}")
        return {"FINISHED"}


# --------------------------------------------------------------------------- panel


class SATK_PT_panel(bpy.types.Panel):
    bl_label = "SATK"
    bl_idname = "SATK_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SATK"

    def draw(self, context):
        p = context.scene.satk
        lay = self.layout
        box = lay.box()
        box.label(text="Index search")
        row = box.row(align=True)
        row.prop(p, "query", text="")
        row.prop(p, "kind", text="")
        row.operator("satk.search", text="", icon="VIEWZOOM")
        for it in p.results[:12]:
            r = box.row(align=True)
            r.label(text=f"{it.sid}  {it.info}"[:60])
            if it.kind == "model" or it.sid.startswith("model:"):
                r.operator("satk.import_model", text="", icon="IMPORT").sid = it.sid
        box = lay.box()
        box.label(text="Import")
        r = box.row(align=True)
        r.prop(p, "model_id", text="")
        r.operator("satk.import_model", text="Model").sid = ""
        box.prop(p, "center_mode", expand=True)
        if p.center_mode == "xy":
            r = box.row(align=True)
            r.prop(p, "x")
            r.prop(p, "y")
        elif p.center_mode == "zone":
            box.prop(p, "zone")
        else:
            box.prop(p, "bookmark")
        r = box.row(align=True)
        r.prop(p, "size")
        r.prop(p, "lod", text="")
        r = box.row(align=True)
        r.prop(p, "any_area")
        if not p.any_area:
            r.prop(p, "area")
        box.prop(p, "col")
        box.operator("satk.import_area", icon="WORLD")
        box = lay.box()
        box.label(text="View")
        box.prop(p, "game_shading")
        box.prop(p, "time", slider=True)
        box.operator("satk.toggle_damage", icon="MOD_PHYSICS")
        box = lay.box()
        box.label(text="Export")
        box.prop(p, "target")
        box.prop(p, "export_name")
        box.operator("satk.export", icon="EXPORT")
        box = lay.box()
        box.label(text="Game-ready (selection)")
        box.prop(p, "gr_name")
        r = box.row(align=True)
        r.prop(p, "gr_budget")
        r.prop(p, "gr_lod")
        r = box.row(align=True)
        r.prop(p, "gr_col", text="")
        r.prop(p, "gr_surface")
        r = box.row(align=True)
        r.prop(p, "gr_prelight", text="")
        r.prop(p, "gr_tex", text="")
        box.operator("satk.make_game_ready", icon="MODIFIER")


_CLASSES = (SATK_AddonPreferences, SATK_SearchItem, SATK_Props, SATK_OT_search, SATK_OT_import_model,
            SATK_OT_import_area, SATK_OT_toggle_damage, SATK_OT_export, SATK_OT_make_game_ready, SATK_PT_panel)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.satk = bpy.props.PointerProperty(type=SATK_Props)


def unregister():
    del bpy.types.Scene.satk
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
