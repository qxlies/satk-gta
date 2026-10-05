"""Operations of satk.blender (owner WP-10, SPEC §4.6 group blender, §4.11).

CLI: ``satk blender doctor|import-model|import-area|render|export|addon-build`` (convenience
commands) and ``satk blender run <cmd> --args '<json>'`` = MCP tool ``blender_job``.
Module-level imports are stdlib only; Blender runs as a subprocess (``satk.blender.runner``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from ..core.envelope import obj
from ..core.errors import SatkError
from ..core.paths import atomic_write, cfg, jpath, work
from ..core.registry import doctor_check, get_op, op, report_progress, status_provider

Engine = Literal["workbench", "eevee"]
Source = Literal["auto", "index", "direct"]


def _payload(resp: dict, limit: int = 20, **extra: Any) -> dict:
    """Op result from a Blender response: files, stats, warn (envelope name), log, job.

    ``visible`` (render --objindex) is cut to ``limit`` rows (the full table is ``files.visible``).
    """
    fields: dict[str, Any] = {
        "cmd": resp.get("cmd"), "job": resp.get("job"), "files": resp.get("files"), "stats": resp.get("stats"),
    }
    if "visible" in resp:
        v = resp["visible"]
        rows = v.get("rows") or []
        fields["visible"] = {"cols": v.get("cols"), "rows": rows[:limit], "n": min(len(rows), limit), "total": len(rows)}
    if "pose" in resp:
        fields["pose"] = resp["pose"]
    fields.update(extra)
    fields["warn"] = list(resp.get("warnings") or [])
    fields["log"] = resp.get("log")
    return obj(None, **fields)


def _contract(cmd: str, args: dict) -> dict:
    from . import contract as C

    try:
        return C.normalize_args(cmd, args)
    except C.ContractError as e:
        raise SatkError(e.code, str(e), hint=e.hint) from None


# --------------------------------------------------------------------------- doctor


@op("blender.doctor",
    summary="Check headless Blender: version, Python, DragonFF b3bd7aa in work/blender/dragonff, isolated profile.",
    summary_ru="Проверить Blender: версия, Python, DragonFF b3bd7aa в work/blender/dragonff, изолированный профиль.",
    mcp=False, examples=("satk blender doctor", "satk blender doctor --quick"))
def blender_doctor(quick: bool = False, timeout: float = 120) -> dict:
    """Blender toolchain check (starts Blender once unless --quick).

    Args:
        quick: do not start Blender (only paths, version resource and the DragonFF export).
        timeout: seconds to wait for Blender.
    """
    from . import runner
    from ..runtime.sysinfo import file_version

    exe = runner.blender_exe()
    info = runner.ensure_dragonff()
    out: dict[str, Any] = {
        "exe": jpath(exe), "blender": file_version(exe),
        "dragonff": {"commit": info.get("commit"), "path": info["path"]},
        "profile": {"dir": jpath(runner.profile_dir())},
        "addon": jpath(runner.addon_dir()),
    }
    if quick:
        return obj(None, **out)
    resp = runner.run_job("doctor", {}, timeout=timeout)
    st = resp.get("stats") or {}
    out["blender"] = st.get("blender", out["blender"])
    out["python"] = st.get("python")
    out["numpy"] = st.get("numpy")
    out["dragonff"].update({"registered": (st.get("dragonff") or {}).get("registered")})
    prof = st.get("profile") or {}
    out["profile"].update({"isolated": prof.get("isolated"), "config": prof.get("config"),
                           "extensions": prof.get("extensions")})
    out["engines"] = st.get("engines")
    out["seconds"] = st.get("seconds")
    out["job"] = resp.get("job")
    out["warn"] = resp.get("warnings") or []
    return obj(None, **out)


# --------------------------------------------------------------------------- import


@op("blender.import_model",
    summary="Import one model (DFF + TXD chain read from the IMGs) into a new .blend via DragonFF; "
            "vehicles get hidden _dam/_vlo, 4 wheels and carcols paint; --render adds a PNG.",
    summary_ru="Импорт модели (DFF + цепочка TXD из IMG) в новый .blend через DragonFF; у машин скрыты "
               "_dam/_vlo, 4 колеса, цвета carcols; --render добавляет PNG.",
    mcp=False, long_running=True,
    examples=("satk blender import-model model:411 --render", "satk blender import-model bfori"))
def blender_import_model(id: str, render: bool = False, views: int = 1, size: int = 768,  # noqa: A002
                         engine: Engine = "workbench", col: bool = False, time: str = "12:00",
                         profile: str = "vanilla", source: Source = "auto", timeout: float = 600) -> dict:
    """Import a model.

    Args:
        id: model:411, model:infernus, 411 or a model name.
        render: also render a PNG (views 1 or 4 -> 2x2 sheet).
        views: 1 or 4 views around the model.
        size: PNG size of one view (pixels).
        engine: workbench (uses EEVEE for blended alpha) or eevee (game shading).
        col: also import the COL of the model (vehicles always carry theirs).
        time: game time HH:MM for the day/night prelight balance.
        profile: game profile.
        source: auto (index if built, else direct DAT/IDE/IPL parsing), index or direct.
        timeout: seconds to wait for Blender.
    """
    from . import resolve, runner

    a = _contract("import_model", {"id": id, "render": render, "views": views, "size": size, "engine": engine,
                                   "col": col, "time": time})
    report_progress(0, 3, "resolving the model")
    plan = resolve.plan_model(a["id"], profile=profile, col=col, source=source)
    report_progress(1, 3, "Blender import")
    resp = runner.run_job("import_model", a, profile=profile, plan=plan, timeout=timeout)
    report_progress(3, 3, "done")
    return _payload(resp, model=plan["model"]["sid"], name=plan["model"]["name"], source=plan["source"])


def _area_box(center: list[float] | None, box: list[float] | None, r: float | None) -> tuple[list[float], Any, Any]:
    if not center or len(center) not in (2, 3):
        raise SatkError("BAD_PARAMS", "--center X,Y is required", hint="satk blender import-area --center 2495,-1687 --box 100")
    if box is not None and len(box) not in (1, 4):
        raise SatkError("BAD_PARAMS", "--box takes a side length (100) or x0,y0,x1,y1")
    b: Any = None if box is None else (box[0] if len(box) == 1 else list(box))
    return list(center[:2]), b, r


@op("blender.import_area",
    summary="Import the placements of an area (box side or radius around X,Y) into a .blend: one mesh per "
            "model, linked duplicates per placement (conjugated IPL quaternion), packed textures.",
    summary_ru="Импорт района (коробка или радиус вокруг X,Y) в .blend: меш на модель, связанные дубликаты на "
               "расстановку (сопряжённый кватернион), упакованные текстуры.",
    mcp=False, long_running=True,
    examples=("satk blender import-area --center 2495,-1687 --box 100 --match center --lod all --area any",
              "satk blender import-area --center 2495,-1687 --r 60"))
def blender_import_area(center: list[float] | None = None, box: list[float] | None = None, r: float | None = None,
                        match: Literal["aabb", "center"] = "aabb", area: str = "0",
                        lod: Literal["hd", "lod", "all"] = "hd", col: bool = False, limit: int = 2000,
                        time: str = "12:00", profile: str = "vanilla", source: Source = "auto",
                        timeout: float = 600) -> dict:
    """Import an area.

    Args:
        center: X,Y of the area centre.
        box: side length of a square around the centre, or x0,y0,x1,y1.
        r: radius around the centre (instead of box).
        match: aabb (bounding box touches the area) or center (position inside).
        area: interior/area number or 'any'.
        lod: hd (default), lod (only LOD models) or all.
        col: also import collisions (slower; hidden in renders).
        limit: maximum placements.
        time: game time HH:MM for the day/night prelight balance.
        profile: game profile.
        source: auto (index if built, else direct DAT/IDE/IPL parsing), index or direct.
        timeout: seconds to wait for Blender.
    """
    from . import resolve, runner

    c, b, rr = _area_box(center, box, r)
    a = _contract("import_area", {"center": c, "box": b, "r": rr, "match": match, "area": area, "lod": lod,
                                  "col": col, "limit": limit, "time": time})
    report_progress(0, 3, "selecting placements")
    plan = resolve.plan_area(center=a["center"], r=a["r"], box=a["box"], match=a["match"], area=a["area"],
                             lod=a["lod"], col=a["col"], limit=a["limit"], profile=profile, source=source)
    if not plan["insts"]:
        raise SatkError("NOT_FOUND", "no placements in this area with these filters",
                        hint="try --lod all --area any --match aabb or a larger --box")
    report_progress(1, 3, f"Blender import of {len(plan['insts'])} placements")
    resp = runner.run_job("import_area", a, profile=profile, plan=plan, timeout=timeout)
    report_progress(3, 3, "done")
    return _payload(resp, source=plan["source"], query={k: v for k, v in plan["query"].items() if v is not None})


# --------------------------------------------------------------------------- render


def _pose(pos, look, ypr, bm, fov) -> tuple[dict | None, str | None]:
    if bm is not None and pos is not None:
        raise SatkError("BAD_PARAMS", "give either --bm or --pos, not both")
    if bm is not None:
        from ..viewer import store

        name = bm[3:] if bm.startswith("bm:") else bm
        b = store.get(name)
        return dict(b["pose"]), name  # its own fov_h_deg (if any) wins over --fov
    if pos is None:
        if look is not None or ypr is not None:
            raise SatkError("BAD_PARAMS", "--look/--ypr need --pos")
        return None, None
    if look is not None and ypr is not None:
        raise SatkError("BAD_PARAMS", "give either --look or --ypr, not both")
    pose: dict[str, Any] = {"pos": list(pos)}
    if look is not None:
        pose["look"] = list(look)
    else:
        pose["ypr"] = list(ypr) if ypr is not None else [0.0, -20.0, 0.0]
    return pose, None


@op("blender.render",
    summary="Render a .blend from a camera pose or bookmark (workbench|eevee, game time); --objindex also "
            "returns the visible SIDs with their pixel share.",
    summary_ru="Рендер .blend с позы камеры или закладки (workbench|eevee, время суток); --objindex — видимые "
               "SID и их доля пикселей.",
    mcp=False, long_running=True,
    examples=("satk blender render --blend <workspace>/work/blender/jobs/<job>/scene.blend --pos 2495,-1740,40 "
              "--look 2495,-1687,13 --objindex",))
def blender_render(blend: str | None = None, pos: list[float] | None = None, look: list[float] | None = None,
                   ypr: list[float] | None = None, bm: str | None = None, time: str = "12:00",
                   size: str = "960x540", engine: Engine = "workbench", objindex: bool = False,
                   fov: float = 70.0, limit: int = 20, timeout: float = 600) -> dict:
    """Render a .blend.

    Args:
        blend: the .blend (from import-model / import-area).
        pos: camera position X,Y,Z.
        look: point the camera looks at X,Y,Z.
        ypr: yaw,pitch,roll in degrees (SAAP convention) instead of look.
        bm: viewer bookmark name (satk view bookmark list) instead of pos.
        time: game time HH:MM (day/night prelight, sky).
        size: WxH or one number.
        engine: workbench (uses EEVEE for blended alpha) or eevee.
        objindex: also render the object-ID pass and return the visible SIDs.
        fov: horizontal field of view in degrees.
        limit: visible SIDs to return (largest pixel share first; all of them in files.visible).
        timeout: seconds to wait for Blender.
    """
    from . import runner

    if not blend:
        raise SatkError("BAD_PARAMS", "--blend is required", hint="satk blender import-area ... returns files.blend")
    bp = Path(blend)
    if not bp.is_file():
        raise SatkError("NOT_FOUND", f"no .blend at {jpath(bp)}")
    pose, bm_name = _pose(pos, look, ypr, bm, fov)
    if pose is not None:
        pose.setdefault("fov_h_deg", fov)
    a = _contract("render", {"blend": jpath(bp), "pose": pose, "time": time, "size": size, "engine": engine,
                             "objindex": objindex, "fov": fov})
    resp = runner.run_job("render", a, blend=bp, timeout=timeout)
    return _payload(resp, limit=max(1, int(limit)), bm=f"bm:{bm_name}" if bm_name else None)


# --------------------------------------------------------------------------- export


@op("blender.export",
    summary="Export models of a .blend with DragonFF (DFF, TXD RGBA8888, COL) plus IDE/IPL and, for "
            "mta-resource, meta.xml + client.lua; only into work/out/exports, never into an IMG.",
    summary_ru="Экспорт моделей .blend через DragonFF (DFF, TXD RGBA8888, COL) + IDE/IPL, для mta-resource — "
               "meta.xml и client.lua; только в work/out/exports, никогда в IMG.",
    mcp=False, long_running=True,
    examples=("satk blender export --blend <scene.blend> --objects lae2_roads89 --target mta-resource",))
def blender_export(blend: str | None = None, objects: list[str] | None = None,
                   target: Literal["mta-resource", "modloader"] = "mta-resource", out: str | None = None,
                   name: str | None = None, timeout: float = 600) -> dict:
    """Export.

    Args:
        blend: the .blend to export from.
        objects: object names, model names or model:/inst: SIDs.
        target: mta-resource or modloader.
        out: output folder under work/ (default work/out/exports/<name>).
        name: resource/folder name (default: the first object's model name).
        timeout: seconds to wait for Blender.
    """
    from . import packaging, resolve, runner

    if not blend:
        raise SatkError("BAD_PARAMS", "--blend is required")
    if not objects:
        raise SatkError("BAD_PARAMS", "--objects is required (object/model names or SIDs)")
    nm = name if name is not None else packaging.default_name(objects[0])
    # name and folder are checked before anything is created; the Blender side makes the folder only
    # after it has resolved the objects (a NOT_FOUND leaves nothing behind)
    d = packaging.export_dir(nm, out)
    bp = Path(blend)
    if not bp.is_file():
        raise SatkError("NOT_FOUND", f"no .blend at {jpath(bp)}")
    a = _contract("export", {"blend": jpath(bp), "objects": objects, "target": target, "out": jpath(d), "name": nm})
    existed = d.exists()
    try:
        resp = runner.run_job("export", a, blend=bp, timeout=timeout)
    except SatkError:
        if not existed and d.is_dir() and not any(d.iterdir()):
            d.rmdir()  # an empty folder of this failed export
        raise
    manifest_p = d / "export.json"
    manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
    # .blend files imported before the IDE data was kept: complete it from the game data
    warn = resolve.fill_export_defs(manifest.get("models") or [], profile=manifest.get("profile") or "vanilla")
    atomic_write(manifest_p, json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    pkg = packaging.write_package(d, target, nm)
    files = dict(resp.get("files") or {})
    files["text"] = pkg["files"]
    resp["files"] = files
    resp["warnings"] = list(resp.get("warnings") or []) + warn + pkg["warn"]
    return _payload(resp, name=nm, target=target, out=jpath(d))


# --------------------------------------------------------------------------- game-ready (M2-08)


@op("blender.game_ready",
    summary="Make a mesh (.blend/.obj/.glb/.fbx/.ply/.stl) game-ready: decimate to a triangle budget, UV, bake "
            "day/night prelight, COL hull/box/mesh, LOD; DFF + COL via DragonFF (+ TXD or PNGs) into "
            "work/out/blender/<name>, checked with satk.formats.",
    summary_ru="Сделать меш (.blend/.obj/.glb/.fbx/.ply/.stl) игровым: decimate под бюджет, UV, prelight день/ночь, "
               "COL (оболочка/бокс/меш), LOD; DFF + COL через DragonFF (+ TXD или PNG) в work/out/blender/<имя>.",
    mcp=False, long_running=True,
    examples=("satk blender game-ready <workspace>/work/tmp/crate.obj --name crate --col box",
              "satk blender game-ready scene.blend --objects Statue --budget 600 --height 2.5 --render"))
def blender_game_ready(src: str, name: str | None = None, objects: list[str] | None = None, budget: int = 1040,
                       height: float | None = None, scale: float = 1.0,
                       origin: Literal["base", "center", "keep"] = "base",
                       uv: Literal["auto", "keep", "smart", "box"] = "auto",
                       bake: Literal["auto", "always", "never"] = "auto", tex_size: int = 256,
                       prelight: Literal["bake", "simple", "none"] = "bake",
                       col: Literal["hull", "box", "mesh", "none"] = "hull", surface: int = 0, lod: float = 0.25,
                       draw: float = 150.0, out: str | None = None, render: bool = False,
                       timeout: float = 600) -> dict:
    """Make a model game-ready (report 22 §7: budget, UV, prelight, COL, LOD, export).

    Args:
        src: source file: .blend (opened read-only, saved elsewhere), .obj, .glb, .gltf, .fbx, .ply or .stl.
        name: model name, 1-21 characters a-z 0-9 _ (default: the file name); also the TXD and COL name.
        objects: objects of a .blend to take (default: every visible mesh); children are included.
        budget: HD triangle budget (vanilla map models: p50 216, p90 1040).
        height: scale the model to this height in metres (instead of scale).
        scale: uniform scale factor.
        origin: base (bottom centre), center (bounding box centre) or keep.
        uv: auto (keep existing UVs, else smart), keep, smart or box (cube projection, 32 px/m).
        bake: auto (bake procedural/vertex colours or re-unwrapped textures into one texture), always, never.
        tex_size: largest texture side (power of two, 16..1024).
        prelight: bake (Cycles AO + sun/sky, day and night), simple (normals only) or none.
        col: hull (convex hull), box (AABB), mesh (the decimated mesh) or none.
        surface: COL surface type 0..178 (eSurfaceType: 0 default, 4 pavement, 43 solid wood, 51 metal plate).
        lod: share of triangles in the LOD model lod<name> (0 = no LOD).
        draw: IDE draw distance of the model (the LOD gets 800).
        out: output folder under work/ (default work/out/blender/<name>).
        render: also render a preview PNG of the result.
        timeout: seconds to wait for Blender.
    """
    from . import contract as C
    from . import gameready, runner

    sp = Path(src).expanduser()
    if not sp.is_file():
        raise SatkError("NOT_FOUND", f"no source file at {jpath(sp)}",
                        hint="a .blend, .obj, .glb, .gltf, .fbx, .ply or .stl file")
    a = _contract("game_ready", {"src": jpath(sp.resolve()), "name": name, "objects": objects, "budget": budget,
                                 "height": height, "scale": scale, "origin": origin, "uv": uv, "bake": bake,
                                 "tex_size": tex_size, "prelight": prelight, "col": col, "surface": surface,
                                 "lod": lod, "draw": draw, "render": render})
    d = gameready.out_dir(a["name"], out)  # PROTECTED_PATH before Blender starts; nothing is created
    a["out"] = jpath(d)
    blend = sp.resolve() if sp.suffix.lower() == ".blend" else None
    report_progress(0, 2, "Blender: make game-ready")
    existed = d.exists()
    try:
        resp = runner.run_job("game_ready", a, blend=blend, timeout=timeout)
    except SatkError:
        if not existed and d.is_dir() and not any(d.iterdir()):
            d.rmdir()  # an empty folder of this failed job
        raise
    report_progress(1, 2, "TXD, IDE and checks")
    fin = gameready.finalize(d)
    files = dict(resp.get("files") or {})
    files.update(fin["files"])
    resp["files"] = files
    resp["stats"] = {**(resp.get("stats") or {}), **fin["stats"]}
    resp["warnings"] = list(resp.get("warnings") or []) + fin["warn"]
    report_progress(2, 2, "done")
    return _payload(resp, name=a["name"], lod=resp.get("lod"), out=jpath(d),
                    checks={"cols": ["check", "status", "detail"], "rows": fin["checks"]})


# --------------------------------------------------------------------------- add-on


@op("blender.addon_build",
    summary="Build the satk_blender extension zip (blender --command extension build) into work/out/blender.",
    summary_ru="Собрать zip расширения satk_blender (blender --command extension build) в work/out/blender.",
    mcp=False, examples=("satk blender addon-build",))
def blender_addon_build(timeout: float = 120) -> dict:
    """Build the add-on package (installing it into a Blender profile is the user's decision, D11).

    Args:
        timeout: seconds to wait for Blender.
    """
    from . import runner

    src = runner.addon_dir()
    out = work("out", "blender")
    _jid, jdir = runner.new_job("addon_build")
    log = jdir / "blender.log"
    before = {p.name for p in out.glob("*.zip")}
    code, secs = runner.run_blender(["--factory-startup", "--command", "extension", "build", "--source-dir", str(src),
                                     "--output-dir", str(out)], log=log, timeout=timeout, env=runner.blender_env(jdir))
    zips = sorted(out.glob("satk_blender-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if code != 0 or not zips:
        raise SatkError("EXTERNAL_TOOL", f"extension build failed (exit {code})", hint=f"see {jpath(log)}",
                        data={"log": jpath(log), "tail": runner.log_tail(log, 12)})
    z = zips[0]
    return obj(None, zip=jpath(z), size=z.stat().st_size, new=z.name not in before, seconds=round(secs, 2),
               log=jpath(log), install_hint="Blender > Preferences > Get Extensions > Install from Disk (your own profile: D11)")


# --------------------------------------------------------------------------- MCP: blender_job


_RUN = {"doctor": "blender.doctor", "import_model": "blender.import_model", "import_area": "blender.import_area",
        "render": "blender.render", "export": "blender.export"}


@op("blender.run", mcp="blender_job", mcp_group="blender", long_running=True,
    summary="Headless Blender: import a model/area to .blend, render it, export it; args: satk_help('blender').",
    summary_ru="Задание Blender: doctor, import_model, import_area, render, export (аргументы JSON).",
    examples=("satk blender run import_model --args '{\"id\":\"model:411\",\"render\":true}'",
              "satk blender run import_area --args '{\"center\":[2495,-1687],\"box\":100,\"lod\":\"all\"}'"))
def blender_run(cmd: Literal["doctor", "import_model", "import_area", "render", "export"],
                args: dict[str, Any] | None = None) -> dict:
    """Dispatch to the blender commands with JSON arguments.

    (``cmd`` and ``args`` carry no MCP descriptions on purpose: the enum names the commands and
    satk_help('blender') the arguments; tools/list is budgeted.)
    """
    a = dict(args or {})
    if cmd == "import_area" and "box" in a and isinstance(a["box"], (int, float)):
        a["box"] = [a["box"]]
    if cmd == "render" and isinstance(a.get("pose"), dict):
        p = a.pop("pose")
        a.setdefault("pos", p.get("pos"))
        if "look" in p:
            a.setdefault("look", p["look"])
        if "ypr" in p:
            a.setdefault("ypr", p["ypr"])
        if p.get("fov_h_deg") is not None:
            a.setdefault("fov", p["fov_h_deg"])
    if cmd == "render" and isinstance(a.get("size"), list):
        a["size"] = "x".join(str(int(v)) for v in a["size"])
    return get_op(_RUN[cmd]).call(a)


# --------------------------------------------------------------------------- doctor / status


@doctor_check("dragonff")
def _dragonff_doctor() -> dict:
    from . import runner

    info = runner.dragonff_info()
    if not info["ok"]:
        return {"status": "warn", "msg": f"DragonFF not extracted: {info['path']}",
                "fix": "satk blender doctor (extracts DragonFF b3bd7aa from src/DragonFF with git archive)"}
    if info.get("commit") != runner.DRAGONFF_COMMIT:
        return {"status": "warn", "msg": f"DragonFF at {info['path']} is {info.get('commit')}, expected {runner.DRAGONFF_COMMIT}",
                "fix": "delete work/blender/dragonff and run satk blender doctor"}
    return {"status": "ok", "msg": f"DragonFF {info['commit']}: {info['path']}", "fix": None}


@status_provider("blender")
def _blender_status(deep: bool) -> dict:
    from . import runner
    from ..runtime.sysinfo import file_version

    exe = cfg().paths.get("blender")
    ok = bool(exe and Path(exe).is_file())
    out: dict[str, Any] = {"ok": ok}
    if ok:
        out["version"] = file_version(exe)
    info = runner.dragonff_info()
    out["dragonff"] = info.get("commit") if info["ok"] else None
    jobs = Path(os.path.abspath(cfg().paths.work)) / "blender" / "jobs"
    if jobs.is_dir():
        names = sorted(p.name for p in jobs.iterdir() if p.is_dir())
        out["jobs"] = len(names)
        if names:
            out["last_job"] = names[-1]
    if not ok:
        out["warn"] = ["Blender not found (paths.blender)"]
    return out
