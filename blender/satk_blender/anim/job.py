# SPDX-License-Identifier: GPL-3.0-or-later
# satk_blender - Blender side of satk (San Andreas ToolKit). Copyright (C) 2026 satk authors.
# This program is free software: you can redistribute it and/or modify it under the terms of the
# GNU General Public License as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. See LICENSE in the parent directory.
"""Headless entry of the animation jobs (``satk.anim.blender`` builds the request)::

    blender.exe -b [scene.blend] --factory-startup --python-exit-code 1 \\
        --python <checkout>/blender/satk_blender/anim/job.py -- <job>/request.json

Commands:

* ``to_blender``: import the skin (``plan``, DragonFF through ``satk_blender.importer``), apply the chosen IFP
  animations as actions on its armature, save ``scene.blend``; ``roundtrip`` exports the actions again to
  ``roundtrip.json`` (``anim/1``), ``render`` writes a 4-frame contact sheet ``anim.png``;
* ``from_blender``: export actions of the opened ``.blend`` to ``anims.json``.

Always writes ``response.json`` (``{"ok": true, "stats", "files", "warnings"}`` or ``{"ok": false, "error"}``);
writes nothing outside the job folder.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback

sys.dont_write_bytecode = True


def _request_path() -> str:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        raise SystemExit("usage: blender -b --python job.py -- <request.json>")
    return argv[0]


def _bootstrap(req: dict) -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (req.get("satk_src"), os.path.dirname(os.path.dirname(here))):
        if p and p not in sys.path:
            sys.path.insert(0, p)


def _fwd(p: str) -> str:
    return os.path.abspath(p).replace("\\", "/")


def _write_json(path: str, obj) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    os.replace(tmp, path)


def _mesh_points(objs, step: int = 7) -> list:
    """World positions of (every ``step``-th) evaluated vertex: the deformed, animated meshes."""
    import bpy

    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        eo = o.evaluated_get(dg)
        me = eo.to_mesh()
        mw = eo.matrix_world.copy()
        verts = me.vertices
        pts.extend(mw @ verts[i].co for i in range(0, len(verts), step))
        eo.to_mesh_clear()
    return pts


def _render_sheet(scene, action, out_dir: str, size: int) -> tuple[str, list[int]]:
    from mathutils import Vector

    from satk_blender import render

    objs = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
    f0, f1 = action.frame_range
    frames = [int(round(f0 + (f1 - f0) * k / 4.0)) for k in range(4)]
    pts = []
    for f in frames:
        scene.frame_set(f)
        pts += _mesh_points(objs)
    if not pts:
        raise RuntimeError("nothing to render: the skin has no visible mesh")
    render.setup_world(scene, 0.0)
    d = Vector((1.3, 1.0, 0.3)).normalized()     # three-quarter front view: SA peds face +Y
    fov = 35.0
    target, dist = render.fit_view(pts, d, fov)
    render.camera(scene, target + d * dist, -d, fov_h_deg=fov, clip_end=dist * 4 + 50)
    render.set_engine(scene, "workbench")
    shots = []
    for k, f in enumerate(frames):
        scene.frame_set(f)
        shots.append(render.render_png(scene, os.path.join(out_dir, f"frame_{k}.png"), (size, size)))
    png = os.path.join(out_dir, "anim.png")
    render.sheet(shots, png, cols=4)
    for s in shots:
        try:
            os.remove(s)
        except OSError:
            pass
    return png, frames


def cmd_to_blender(req: dict, args: dict) -> dict:
    import bpy

    from satk.anim.ifp import read_ifp
    from satk_blender import common, importer

    from . import apply

    common.load_dragonff(req.get("dragonff"))
    plan = args["plan"]
    warn: list[str] = list(plan.get("warnings") or [])
    r = importer.import_model(plan, {"clean": True, "col": False, "balance": 0.0})
    warn += r["warnings"]
    arm = apply.pick_armature()
    with open(args["source_ifp_path"], "rb") as f:
        ifp = read_ifp(f.read())
    sel = [ifp.anims[i] for i in args["indices"]]
    fps = int(args.get("fps") or apply.pick_fps(sel))
    scene = bpy.context.scene
    scene.render.fps = fps
    scene.render.fps_base = 1.0
    src = {"ifp": args.get("label"), "pack": ifp.pack, "format": ifp.format}
    rows = []
    actions = []
    for i, a in enumerate(sel):
        res = apply.apply_anim(arm, a, fps=fps, action_name=a.name, source={**src, "index": args["indices"][i]})
        actions.append(res["action"])
        rows.append([a.name, res["action"].name, len(a.seqs), res["mapped"], res["unmapped"], round(res["frames"], 2)])
        if res["nudged"]:
            warn.append(f"SAME_TIME: {a.name}: {res['nudged']} repeated, decreasing or close key time(s) separated "
                        "in Blender; unchanged key positions export with their original times")
        if any(s.scale for s in a.seqs):
            warn.append(f"SCALE_IGNORED: {a.name}: ANPK scale keys are not applied in Blender (the game ignores them)")
    first = actions[int(args.get("lead") or 0)]
    for pb in arm.pose.bones:       # bones the lead action does not key show their rest pose, not a leftover
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)
    arm.animation_data.action = first
    scene.frame_start = 0
    scene.frame_end = int(math.ceil(first.frame_range[1]))
    scene.frame_set(0)
    files: dict = {}
    stats: dict = {"skin": plan["model"].get("sid"), "armature": arm.name, "bones": len(arm.data.bones), "fps": fps,
                   "actions": rows, "skinned": apply.skeleton_of(arm).skinned}
    if args.get("roundtrip"):
        out = {"satk": "anim/1", "format": ifp.format, "pack": ifp.pack, "anims": []}
        for act in actions:
            arm.animation_data.action = act
            a, w = apply.export_action(arm, act)
            out["anims"].append(a)
            warn += w
        arm.animation_data.action = first
        p = os.path.join(req["out_dir"], "roundtrip.json")
        _write_json(p, out)
        files["roundtrip"] = _fwd(p)
    if args.get("render"):
        t = time.perf_counter()
        png, frames = _render_sheet(scene, first, req["out_dir"], int(args.get("size") or 320))
        files["png"] = _fwd(png)
        stats["render_frames"] = frames
        stats["render_s"] = round(time.perf_counter() - t, 2)
        scene.frame_set(0)
    if args.get("save", True):
        files["blend"] = _fwd(common.save_blend(os.path.join(req["out_dir"], "scene.blend")))
    return {"stats": stats, "files": files, "warnings": warn}


def _actions_for(arm, names: list[str] | None) -> list:
    import bpy

    from . import apply

    if names:
        out = []
        for n in names:
            act = bpy.data.actions.get(n)
            if act is None:
                raise LookupError(f"no action {n!r} (have: {', '.join(a.name for a in bpy.data.actions) or 'none'})")
            out.append(act)
        return out
    acts = [a for a in bpy.data.actions if a.get(apply.PROP)]
    if acts:
        return sorted(acts, key=lambda a: (json.loads(a[apply.PROP]).get("index", 1 << 30), a.name))
    cur = arm.animation_data.action if arm.animation_data else None
    if cur is not None:
        return [cur]
    return [a for a in bpy.data.actions if a.slots and any(s.target_id_type == "OBJECT" for s in a.slots)]


def cmd_from_blender(req: dict, args: dict) -> dict:
    import json as _json

    from . import apply

    arm = apply.pick_armature(args.get("armature"))
    acts = _actions_for(arm, args.get("actions"))
    if not acts:
        raise LookupError("no action to export: animate the armature (or name actions with --action)")
    warn: list[str] = []
    anims = []
    rows = []
    meta0: dict = {}
    keep = arm.animation_data.action if arm.animation_data else None
    for act in acts:
        if arm.animation_data is None:
            arm.animation_data_create()
        arm.animation_data.action = act
        a, w = apply.export_action(arm, act, fps=args.get("fps"), compressed=args.get("compressed"),
                                   bake=bool(args.get("bake")), use_meta=not args.get("fresh"))
        warn += w
        if not a["bones"]:
            warn.append(f"EMPTY: action {act.name!r} animates no bone of {arm.name}")
            continue
        anims.append(a)
        rows.append([a["name"], act.name, len(a["bones"]), max(len(b["keys"]) for b in a["bones"])])
        if not meta0:
            meta0 = _json.loads(act.get(apply.PROP, "{}") or "{}")
    if arm.animation_data is not None:
        arm.animation_data.action = keep
    if not anims:
        raise LookupError(f"the selected actions animate no bones of {arm.name}")
    doc = {"satk": "anim/1", "format": meta0.get("format", "ANP3"), "pack": meta0.get("pack", "custom"), "anims": anims}
    p = os.path.join(req["out_dir"], "anims.json")
    _write_json(p, doc)
    return {"stats": {"armature": arm.name, "actions": rows}, "files": {"json": _fwd(p)}, "warnings": warn}


HANDLERS = {"to_blender": cmd_to_blender, "from_blender": cmd_from_blender}


def main() -> int:
    t0 = time.perf_counter()
    req_path = _request_path()
    with open(req_path, encoding="utf-8") as f:
        req = json.load(f)
    _bootstrap(req)
    out_dir = req.get("out_dir") or os.path.dirname(os.path.abspath(req_path))
    resp_path = os.path.join(out_dir, "response.json")
    cmd = req.get("cmd")
    try:
        if cmd not in HANDLERS:
            raise ValueError(f"unknown command {cmd!r} ({', '.join(HANDLERS)})")
        res = HANDLERS[cmd](req, dict(req.get("args") or {}))
        stats = dict(res.get("stats") or {})
        stats["seconds"] = round(time.perf_counter() - t0, 2)
        resp = {"ok": True, "cmd": cmd, "stats": stats, "files": res.get("files") or {},
                "warnings": res.get("warnings") or []}
        code = 0
    except Exception as e:  # noqa: BLE001 - every failure must end up in response.json
        code_s = getattr(e, "code", None)
        if not isinstance(code_s, str) or not code_s.isupper():
            code_s = "NOT_FOUND" if isinstance(e, LookupError) else "EXTERNAL_TOOL"
        resp = {"ok": False, "cmd": cmd, "error": {"code": code_s, "msg": f"{type(e).__name__}: {e}"
                                                   if code_s == "EXTERNAL_TOOL" else str(e).strip("'\""),
                                                   "data": {"traceback": traceback.format_exc()[-1800:]}}}
        traceback.print_exc()
        code = 1
    _write_json(resp_path, resp)
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    if __package__ in (None, ""):
        # run as a script by Blender: re-import as satk_blender.anim.job so relative imports work
        _here = os.path.dirname(os.path.abspath(__file__))
        _root = os.path.dirname(os.path.dirname(_here))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from satk_blender.anim import job as _job

        _rc = _job.main()
    else:  # pragma: no cover
        _rc = main()
    if _rc:
        raise SystemExit(_rc)
