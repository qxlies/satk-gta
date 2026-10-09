# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in this directory.
"""Headless entry point (contract ``satk-blender/1``, ``satk.blender.contract``)::

    blender.exe -b [scene.blend] --factory-startup --python-exit-code 1 \\
        --python agent_cli.py -- <out_dir>/request.json

Reads the request, runs the command and ALWAYS writes ``response.json`` (also on failure); the
exit code is 0 on success and 1 otherwise. Never writes outside ``out_dir`` / the export folder
chosen by satk.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback

sys.dont_write_bytecode = True


def _argv_request() -> str:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        raise SystemExit("usage: blender -b --python agent_cli.py -- <request.json>")
    return argv[0]


def _bootstrap(req: dict) -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (req.get("satk_src"), os.path.dirname(here)):
        if p and p not in sys.path:
            sys.path.insert(0, p)
    _prefs()


def _prefs() -> None:
    """Before anything is saved: no thumbnails (Blender on Windows writes them to the user's
    ``.thumbnails`` folder whatever XDG_CACHE_HOME says), no ``.blend1`` backups, no autosave."""
    try:
        import bpy
    except ImportError:  # pragma: no cover - outside Blender
        return
    fp = bpy.context.preferences.filepaths
    for k, v in (("file_preview_type", "NONE"), ("save_version", 0), ("use_auto_save_temporary_files", False)):
        try:
            setattr(fp, k, v)
        except (AttributeError, TypeError):
            pass


def _balance(args: dict) -> float:
    from satk.blender.contract import day_night_balance, parse_time

    h, m = parse_time(args.get("time"))
    return day_night_balance(h, m)


def _engines(scene) -> list[str]:
    cur = scene.render.engine
    out = []
    for e in ("BLENDER_WORKBENCH", "BLENDER_EEVEE", "CYCLES"):
        try:
            scene.render.engine = e
            out.append(e)
        except TypeError:
            pass
    scene.render.engine = cur
    return out


def cmd_doctor(req: dict, args: dict) -> dict:
    import bpy

    from satk_blender import common

    common.load_dragonff(req.get("dragonff"))
    prof = {k: common.fwd(bpy.utils.user_resource(k)) for k in ("CONFIG", "SCRIPTS", "EXTENSIONS")}
    want = os.environ.get("BLENDER_USER_RESOURCES")
    isolated = bool(want) and all(v.lower().startswith(common.fwd(want).lower()) for v in prof.values())
    try:
        import numpy
        np_ver = numpy.__version__
    except ImportError:  # pragma: no cover
        np_ver = None
    addons = sorted(a.module for a in bpy.context.preferences.addons)
    dff_path = os.path.join(req.get("dragonff") or "", "dragonff")
    stats = {
        "blender": bpy.app.version_string.split()[0], "python": sys.version.split()[0], "numpy": np_ver,
        "dragonff": {"commit": common.dragonff_commit(req.get("dragonff")), "path": common.fwd(dff_path),
                     "registered": hasattr(bpy.types.Object, "dff")},
        "profile": {"isolated": isolated, "factory_startup": bool(bpy.app.factory_startup), **{k.lower(): v for k, v in prof.items()}},
        "addons": addons,
        "engines": _engines(bpy.context.scene),
    }
    warn = [] if isolated else ["PROFILE_NOT_ISOLATED: Blender uses the user profile (run through satk)"]
    return {"stats": stats, "warnings": warn, "files": {}}


def _purge_and_count(stats: dict) -> None:
    """Drop what saving would drop, then count what the .blend holds: ``images`` (textures in the
    file, all packed) next to ``txd_images`` (loaded from the TXDs in this session)."""
    from satk_blender import common

    stats["unused_images"] = common.purge_unused()
    stats["images"] = len(common.real_images())


def cmd_import_model(req: dict, args: dict) -> dict:
    import bpy

    from satk_blender import common, importer, render

    common.load_dragonff(req.get("dragonff"))
    plan = req.get("plan")
    warn: list[str] = []
    if plan is None:
        from satk.blender.resolve import plan_model

        plan = plan_model(args["id"], profile=req.get("profile", "vanilla"), col=args.get("col", False))
    warn += plan.get("warnings", [])
    a = dict(args, balance=_balance(args))
    r = importer.import_model(plan, a)
    warn += r["warnings"]
    out = req["out_dir"]
    scene = bpy.context.scene
    render.setup_world(scene, a["balance"])
    files: dict = {"png": []}
    stats = r["stats"]
    _purge_and_count(stats)
    if args.get("save", True):
        files["blend"] = common.fwd(common.save_blend(os.path.join(out, "scene.blend")))
    if args.get("render"):
        t = time.perf_counter()
        objs = [o for o in bpy.data.objects if o.type == "MESH" and o.visible_get() and not o.hide_render]
        margins: list = []
        shots = render.orbit_views(scene, objs, out, views=int(args.get("views", 1)), size=int(args.get("size", 768)),
                                   engine=args.get("engine", "workbench"), margins=margins)
        stats["frame_margin"] = min(margins) if margins else None  # > 0: the model is not cut off
        png = os.path.join(out, "model.png")
        if len(shots) > 1:
            render.sheet(shots, png, cols=2)
        else:
            os.replace(shots[0], png)
        files["png"].append(common.fwd(png))
        st = render.image_stats(png)
        stats["png_mean"], stats["png_std"] = st["mean"], st["std"]
        stats["render_s"] = round(time.perf_counter() - t, 2)
        stats["engine"] = scene.render.engine
        warn += render.engine_warnings(args.get("engine", "workbench"), stats["engine"])
    stats["source"] = plan.get("source")
    return {"stats": stats, "warnings": warn, "files": files}


def cmd_import_area(req: dict, args: dict) -> dict:
    import bpy

    from satk_blender import common, importer, render

    common.load_dragonff(req.get("dragonff"))
    plan = req.get("plan")
    warn: list[str] = []
    if plan is None:
        from satk.blender.resolve import plan_area

        plan = plan_area(center=args["center"], r=args.get("r"), box=args.get("box"), match=args["match"],
                         area=args["area"], lod=args["lod"], col=args["col"], limit=args["limit"],
                         profile=req.get("profile", "vanilla"))
    warn += plan.get("warnings", [])
    a = dict(args, balance=_balance(args))
    r = importer.import_area(plan, a)
    warn += r["warnings"]
    scene = bpy.context.scene
    render.setup_world(scene, a["balance"])
    render.default_area_camera(scene, plan)
    files: dict = {"png": []}
    stats = r["stats"]
    _purge_and_count(stats)
    if args.get("save", True):
        files["blend"] = common.fwd(common.save_blend(os.path.join(req["out_dir"], "scene.blend")))
    stats["packed"] = sum(1 for i in common.real_images() if i.packed_file is not None)
    stats["source"] = plan.get("source")
    if plan.get("truncated"):
        stats["truncated"] = True
    return {"stats": stats, "warnings": warn, "files": files}


def cmd_render(req: dict, args: dict) -> dict:
    import bpy

    from satk_blender import common, render

    common.load_dragonff(req.get("dragonff"))
    scene = bpy.context.scene
    bal = _balance(args)
    render.apply_time(bal, scene)
    pose = args.get("pose")
    if pose is None and args.get("bm"):
        from satk.viewer import store

        pose = dict(store.get(str(args["bm"]))["pose"])
    if pose is not None:
        render.pose_camera(scene, pose, fov_default=float(args.get("fov") or 70.0))
    elif scene.camera is None:
        from satk.blender.contract import ContractError

        raise ContractError("render needs 'pose' or 'bm' (the scene has no camera)")
    eng = render.set_engine(scene, args.get("engine", "workbench"))
    size = tuple(args.get("size") or (960, 540))
    out = req["out_dir"]
    t = time.perf_counter()
    png = render.render_png(scene, os.path.join(out, "render.png"), size)
    stats = {"engine": eng, "size": list(size), "render_s": round(time.perf_counter() - t, 2), "balance": round(bal, 3)}
    stats.update({f"png_{k}": v for k, v in render.image_stats(png).items()})
    files: dict = {"png": [common.fwd(png)]}
    extra: dict = {}
    if args.get("objindex"):
        ids = os.path.join(out, "render_ids.png")
        cam = scene.camera
        pose_now = _cam_pose(cam) if cam is not None else None
        rows = render.objindex(scene, ids, size)
        files["ids"] = common.fwd(ids)
        visible = {"cols": ["id", "name", "share"], "rows": rows}
        vpath = os.path.join(out, "visible.json")
        with open(vpath, "w", encoding="utf-8", newline="\n") as f:
            json.dump(visible, f, ensure_ascii=False, indent=0)
        files["visible"] = common.fwd(vpath)
        extra["visible"] = visible
        stats["visible"] = len(rows)
        if pose_now:
            extra["pose"] = pose_now
    if "pose" not in extra and scene.camera is not None:
        extra["pose"] = _cam_pose(scene.camera)
    return {"stats": stats, "warnings": render.engine_warnings(args.get("engine", "workbench"), eng),
            "files": files, "extra": extra}


def _cam_pose(cam) -> dict:
    import math

    from mathutils import Vector

    fwd = cam.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
    p = cam.matrix_world.translation
    return {"pos": [round(v, 2) for v in p], "look": [round(p[i] + fwd[i] * 10.0, 2) for i in range(3)],
            "fov_h_deg": round(math.degrees(cam.data.angle), 2)}


def cmd_export(req: dict, args: dict) -> dict:
    from satk_blender import common, exporter

    import bpy

    common.load_dragonff(req.get("dragonff"))
    out = args.get("out") or os.path.join(req["out_dir"], "export")
    r = exporter.export(args, out)
    manifest = {"blend": common.fwd(args["blend"]), "target": args["target"], "name": args.get("name"),
                "profile": bpy.context.scene.get("satk_profile") or req.get("profile") or "vanilla",
                "out": common.fwd(out), "models": r["models"]}
    mpath = os.path.join(out, "export.json")
    with open(mpath, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
        f.write("\n")
    files = {"dir": common.fwd(out), "json": common.fwd(mpath)}
    for m in r["models"]:
        for k, p in m["files"].items():
            files.setdefault(k, []).append(p)
    stats = {"models": len(r["models"]), "insts": sum(len(m["insts"]) for m in r["models"])}
    return {"stats": stats, "warnings": r["warnings"], "files": files}


def cmd_game_ready(req: dict, args: dict) -> dict:
    """``make_game_ready`` on ``src`` (opened by the runner when it is a .blend) into ``args["out"]``."""
    import bpy

    from satk_blender import common, gameready, render

    common.load_dragonff(req.get("dragonff"))
    out = args.get("out") or os.path.join(req["out_dir"], "out")
    r = gameready.make_game_ready(args, out)
    man = r["manifest"]
    files = dict(man["files"])
    files["dir"] = common.fwd(out)
    files["json"] = common.fwd(os.path.join(out, "gameready.json"))
    if man["textures"]:
        files["png"] = [t["png"] for t in man["textures"]]
    stats = dict(man["stats"])
    warn = list(r["warnings"])
    scene = bpy.context.scene
    if args.get("render"):
        from satk_blender import shading

        t = time.perf_counter()
        hd = r["objects"]["hd"]
        # the game look: texture x material colour x day prelight, unlit (the saved .blend keeps it)
        shading.apply([hd], balance=0.0, kind="building")
        render.setup_world(scene, 0.0)
        shots = render.orbit_views(scene, [hd], req["out_dir"], views=1, size=512, engine="eevee", prefix="preview")
        png = os.path.join(req["out_dir"], "preview.png")
        os.replace(shots[0], png)
        files["preview"] = common.fwd(png)
        stats["render_s"] = round(time.perf_counter() - t, 2)
    if args.get("save", True):
        files["blend"] = common.fwd(common.save_blend(os.path.join(req["out_dir"], "scene.blend")))
    return {"stats": stats, "warnings": warn, "files": files, "extra": {"name": man["name"], "lod": man["lod"]}}


def cmd_preview(req: dict, args: dict) -> dict:
    """SA-look preview cells (``satk_blender.look.preview``); the satk side composes the sheet."""
    from satk_blender import common
    from satk_blender.look import preview

    common.load_dragonff(req.get("dragonff"))
    res = preview.run(args["spec"], os.path.join(req["out_dir"], "cells"))
    files: dict = {"cells": [c[2] for c in res["cells"]]}
    if args.get("save"):
        files["blend"] = common.fwd(common.save_blend(os.path.join(req["out_dir"], "scene.blend")))
    stats = {"seconds_import": res["seconds"]["import"], "seconds_render": res["seconds"]["render"],
             "cells": len(res["cells"]), "cell_s": res["seconds"].get("cells")}
    return {"stats": stats, "warnings": res["warnings"], "files": files,
            "extra": {"preview": {k: res[k] for k in ("cells", "rows", "cols", "stats", "env", "seconds", "views", "leak")
                                  if k in res}}}


HANDLERS = {"doctor": cmd_doctor, "import_model": cmd_import_model, "import_area": cmd_import_area,
            "render": cmd_render, "export": cmd_export, "game_ready": cmd_game_ready, "preview": cmd_preview}


def main() -> int:
    t0 = time.perf_counter()
    req_path = _argv_request()
    with open(req_path, encoding="utf-8") as f:
        req = json.load(f)
    _bootstrap(req)
    from satk.blender import contract as C

    out_dir = req.get("out_dir") or os.path.dirname(os.path.abspath(req_path))
    resp_path = req.get("response") or os.path.join(out_dir, "response.json")
    log = os.path.join(out_dir, "blender.log").replace("\\", "/")
    cmd = req.get("cmd")
    try:
        args = C.normalize_args(cmd, req.get("args"))
        res = HANDLERS[cmd](req, args)
        stats = dict(res.get("stats") or {})
        stats["seconds"] = round(time.perf_counter() - t0, 2)
        resp = C.ok_response(cmd, files=res.get("files"), stats=stats, warnings=res.get("warnings"), log=log,
                             **(res.get("extra") or {}))
        code = 0
    except Exception as e:  # noqa: BLE001 - every failure must end up in response.json
        ecode = getattr(e, "code", None)
        if not isinstance(ecode, str) or not ecode.isupper():
            ecode = "EXTERNAL_TOOL"
        msg = getattr(e, "msg", None) or f"{type(e).__name__}: {e}"
        data = {"traceback": traceback.format_exc()[-1800:]} if ecode == "EXTERNAL_TOOL" else None
        if getattr(e, "did_you_mean", None):
            data = dict(data or {}, did_you_mean=list(e.did_you_mean))
        resp = C.error_response(cmd, ecode, str(msg), hint=getattr(e, "hint", None), log=log, data=data)
        traceback.print_exc()
        code = 1
    C.write_json(resp_path, resp)
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    _rc = main()
    if _rc:
        import bpy  # noqa: F401 - make --python-exit-code see the failure

        raise SystemExit(_rc)
