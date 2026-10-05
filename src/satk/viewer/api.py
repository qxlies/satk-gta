"""Viewer operations behind ``satk view …`` / ``view_*`` MCP tools (SPEC §4.6, §4.7, §4.10.5).

Every function talks to a target through :func:`satk.viewer.backends.get_backend`, so the same
code drives the mock, Ariane (legacy bridge or native SAAP) and, later, the game.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.envelope import obj, round_pos, table
from ..core.errors import SatkError
from ..core.ids import Sid
from ..saap import geom as G
from . import overlays as O
from . import sidecar as SC
from . import store
from .backends import get_backend
from .resolve import IndexAccess, link_entity

__all__ = ["goto", "capture", "pick", "set_view", "bookmark", "replay", "conformance", "control", "normalize_points",
           "pose_from_args"]

TARGETS = ("ariane", "game", "mock")


# --------------------------------------------------------------------------- helpers


def normalize_points(points: Any) -> list[list[float]]:
    """``[[x, y], …]`` from ``[[x, y]]``, ``[[x], [y]]`` (CLI ``400 300``) or ``[x, y, …]``."""
    if not points:
        return []
    flat: list[float] = []

    def walk(v):
        if isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        else:
            flat.append(float(v))

    walk(points)
    if len(flat) % 2:
        raise SatkError("BAD_PARAMS", f"points need x,y pairs, got {len(flat)} numbers")
    return [[flat[i], flat[i + 1]] for i in range(0, len(flat), 2)]


def _vec(v, name: str) -> list[float] | None:
    if v is None:
        return None
    if len(v) != 3:
        raise SatkError("BAD_PARAMS", f"{name} needs 3 numbers x,y,z")
    return [float(c) for c in v]


def pose_from_args(pose: dict | None = None, pos=None, look=None, ypr=None, fov: float | None = None) -> dict | None:
    """SAAP pose from a ``pose`` dict or ``pos`` + ``look``/``ypr`` (validated)."""
    if pose is None and pos is None:
        if look is not None or ypr is not None:
            raise SatkError("BAD_PARAMS", "--look/--ypr need --pos")
        return None
    if pose is None:
        p = {"pos": _vec(pos, "pos")}
        if look is not None and ypr is not None:
            raise SatkError("BAD_PARAMS", "give either --look or --ypr, not both")
        if look is not None:
            p["look"] = _vec(look, "look")
        elif ypr is not None:
            p["ypr"] = _vec(ypr, "ypr")
        else:
            p["ypr"] = [0.0, -20.0, 0.0]
        pose = p
    pose = dict(pose)
    if fov is not None:
        pose["fov_h_deg"] = float(fov)
    G.parse_pose(pose)  # validate
    return pose


def _profile(target: str, profile: str | None) -> str:
    """Index profile for SIDs: explicit, else the one the running viewer was started with, else vanilla."""
    if profile:
        return profile
    from ..saap import client as C
    from .backends import role_of

    try:
        p = (C.read_session(role_of(target)) or {}).get("profile")
    except SatkError:
        p = None
    return p if isinstance(p, str) and p else "vanilla"


def _close(b) -> None:
    try:
        b.close()
    except Exception:  # noqa: BLE001
        pass


def _pose_of_sid(sid: str, idx: IndexAccess, fov: float | None, dist: float | None) -> tuple[dict, dict]:
    s = Sid.parse(sid)
    if s.kind == "bm":
        b = store.get(s.key)
        return dict(b["pose"]), {"bm": f"bm:{b['name']}"}
    if s.kind == "cap":
        sc = SC.load(str(s))
        return dict(sc["pose"]), {"cap": str(s)}
    center, radius, o = idx.locate(str(s))
    if dist is not None:
        radius = max(0.0, (float(dist) - 5.0) / 2.5)
    pose = G.frame_sphere(center, radius, fov_h_deg=fov)
    return pose, {"framed": str(o.get("id", s)), "radius": round(radius, 2)}


# --------------------------------------------------------------------------- control


def control(action: str | None = "status", target: str = "ariane", profile: str = "vanilla",
            window: str | None = None, wait: float | None = None) -> dict:
    from . import launcher

    action = action or "status"
    if action == "start":
        return launcher.start(target, profile=profile, window=window, wait=wait)
    if action == "stop":
        return launcher.stop(target)
    if action == "status":
        return launcher.status(target)
    raise SatkError("BAD_PARAMS", f"unknown action {action!r} (status|start|stop)")


# --------------------------------------------------------------------------- goto


def goto(target: str = "ariane", id: str | None = None, pos=None, look=None, ypr=None, bm: str | None = None,  # noqa: A002
         fov: float | None = 70.0, dist: float | None = None, profile: str | None = None) -> dict:
    profile = _profile(target, profile)
    idx = IndexAccess(profile)
    extra: dict[str, Any] = {}
    if sum(x is not None for x in (id, pos, bm)) != 1:
        raise SatkError("BAD_PARAMS", "give exactly one of --id SID, --pos X,Y,Z or --bm NAME")
    if bm is not None:
        pose, extra = _pose_of_sid(f"bm:{bm}" if not str(bm).startswith("bm:") else bm, idx, fov, dist)
    elif id is not None:
        pose, extra = _pose_of_sid(id, idx, fov, dist)
    else:
        pose = pose_from_args(None, pos, look, ypr, None)
    if fov is not None:
        pose["fov_h_deg"] = float(fov)
    b = get_backend(target)
    try:
        r = b.call("camera.set", {"pose": pose})
        warn = list(b.warnings) + idx.warnings
    finally:
        _close(b)
    out = obj(None, target=target, pose=r.get("pose"), rev=r.get("rev"), **extra)
    if warn:
        out["warn"] = warn
    return out


# --------------------------------------------------------------------------- capture


def _resolve_compare(ref: str) -> Path:
    s = str(ref)
    if s.startswith("cap:") or Path(s).suffix.lower() == ".json":
        sc = SC.load(s)
        return Path(sc["files"]["color"])
    p = Path(s)
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"compare_to: no such capture or PNG: {ref}")
    return p


def _approx_marks(idx: IndexAccess, pose: dict, w: int, h: int, n: int) -> list[dict]:
    cam = G.Camera.from_pose(pose, w, h)
    c = cam.pose.pos
    reach = 350.0
    ahead = G.add(c, G.mul(cam.f, reach / 2))
    rows = idx.insts(center=(ahead[0], ahead[1]), r=reach, area=0, lod="hd")
    boxes = []
    for row in rows:
        a = row.aabb
        pb = cam.project_box(a[:3], a[3:])
        if pb is None:
            continue
        x0, y0, x1, y1, zmin = pb
        boxes.append({"sid": row.sid, "name": row.name, "rect": (x0, y0, x1, y1),
                      "depth": G.length(G.sub(row.pos, c))})
    return O.marks_from_boxes(boxes, w, h, n) if boxes else []


def _pick_grid_marks(b, pose: dict, w: int, h: int, n: int, idx: IndexAccess, cols: int = 16,
                     rows: int = 9) -> list[dict]:
    """Approximate marks without an ID buffer: pick a ``cols×rows`` grid at the capture pose.

    Occlusion-aware (it samples what is actually hit), exact SIDs where the endpoint reports
    ``src``; the share is the fraction of grid samples, the centre the sample nearest to the
    centroid of the entity's samples.
    """
    pts = [[round((i + 0.5) * w / cols, 1), round((j + 0.5) * h / rows, 1)] for j in range(rows) for i in range(cols)]
    r = b.call("pick", {"points": pts, "space": "capture", "w": w, "h": h, "pose": pose, "mode": "collision"})
    groups: dict[str, dict] = {}
    for (x, y), hit in zip(pts, r.get("hits") or []):
        e = hit.get("entity")
        if not e:
            continue
        ln = link_entity(e, idx)
        # group by stable id: opaque refs may differ per hit for the same entity (Ariane raycast refs)
        key = ln["id"] or str(e.get("ref"))
        g = groups.setdefault(key, {"entity": e, "sid": key, "pts": []})
        g["pts"].append((x, y))
    ranked = sorted(groups.values(), key=lambda g: (-len(g["pts"]), g["sid"]))[:n]
    out = []
    for k, g in enumerate(ranked, 1):
        cx = sum(p[0] for p in g["pts"]) / len(g["pts"])
        cy = sum(p[1] for p in g["pts"]) / len(g["pts"])
        x, y = min(g["pts"], key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
        out.append({"n": k, "x": x, "y": y, "sid": g["sid"], "name": g["entity"].get("model_name") or "",
                    "share": round(len(g["pts"]) / len(pts), 4)})
    return out


def capture(target: str = "ariane", pose: dict | None = None, bm: str | None = None, w: int = 960, h: int = 540,
            layers: list[str] | None = None, marks: int = 0, grid: bool = False, settle: bool = True,
            env: dict | None = None, compare_to: str | None = None, profile: str | None = None,
            cap_id: str | None = None) -> dict:
    """Capture a frame (+ marks/grid/compare) and write the sidecar."""
    if marks < 0 or marks > 64:
        raise SatkError("BAD_PARAMS", "marks must be 0..64")
    if bm is not None:
        rec = store.get(bm)
        pose = pose or rec["pose"]
        env = env or rec.get("env")
    layers = list(layers or ["color"])
    if "color" not in layers:
        layers.insert(0, "color")
    cap = cap_id or SC.new_capture_id()
    cp = SC.capture_paths(cap)
    profile = _profile(target, profile)
    idx = IndexAccess(profile)
    b = get_backend(target)
    warn: list[str] = []
    try:
        params: dict[str, Any] = {"path_prefix": paths.jpath(cp["prefix"])}
        auto_ids = marks > 0 and b.has("capture.ids") and "ids" not in layers
        params["layers"] = layers + (["ids"] if auto_ids else [])
        if b.has("capture.size"):
            params["w"], params["h"] = int(w), int(h)
        else:
            vw, vh = b.viewport
            if (vw, vh) != (int(w), int(h)):
                warn.append(f"capture size is the window size {vw}x{vh} (target has no capture.size)")
        if pose is not None:
            params["pose"] = pose
        if env:
            params["env"] = env
        if b.has("world.settle"):
            params["settle"] = {"max_frames": 120 if settle else 0, "quiet_frames": 2}
        r = b.call("capture", params)
        files = r["files"]
        W, H = int(r["w"]), int(r["h"])
        legend: list[list] = []
        approx = None
        ms: list[dict] = []
        if marks > 0:
            if files.get("ids") and r.get("ids_legend") is not None:
                ms = O.marks_from_ids(files["ids"], r["ids_legend"], marks)
                for n, m in enumerate(ms, 1):
                    ln = link_entity(m["entity"], idx)
                    m["n"] = n
                    legend.append([n, ln["id"], m["entity"].get("model_name") or "", m["share"]])
            elif b.has("pick"):
                approx = "pick_grid"
                ms = _pick_grid_marks(b, r["pose"], W, H, marks, idx)
                legend = [[m["n"], m["sid"], m["name"], m["share"]] for m in ms]
            else:
                approx = "index_aabb"
                ms = _approx_marks(idx, r["pose"], W, H, marks)
                for n, m in enumerate(ms, 1):
                    m["n"] = n
                    legend.append([n, m["sid"], m["name"], m["share"]])
        env_now = None
        if b.has("env"):
            try:
                env_now = b.call("env.get", {})
                env_now.pop("weathers", None)
            except SatkError:
                env_now = None
        if env:
            env_now = {**(env_now or {}), **env}
        build = b.build_id
        proto, impl = b.proto, b.impl or b.hello.get("impl")
        warn = list(b.warnings) + warn
    finally:
        _close(b)
    out: dict[str, Any] = {"id": f"cap:{cap}", "file": files["color"]}
    if marks > 0:
        out["marks_file"] = paths.jpath(O.draw_marks(files["color"], ms, cp["marks"]))
    if grid:
        out["grid_file"] = paths.jpath(O.draw_grid(files["color"], cp["grid"]))
    if compare_to:
        other = _resolve_compare(compare_to)
        cmpr = O.compare(other, files["color"], cp["cmp"])
        out["diff"] = {"file": paths.jpath(cmpr["file"]), "ssim": cmpr["ssim"], "mad": cmpr["mad"],
                       "against": paths.jpath(other)}
    warn.extend(w_ for w_ in idx.warnings if w_ not in warn)
    req_pose = dict(pose) if pose is not None else None
    if req_pose is not None and "fov_h_deg" not in req_pose and (r.get("pose") or {}).get("fov_h_deg"):
        req_pose["fov_h_deg"] = r["pose"]["fov_h_deg"]  # replay with the FOV actually used
    sc = {"target": target, "proto": proto, "impl": impl, "build": build,
          "request": {"pose": req_pose, "env": env, "w": int(w), "h": int(h), "layers": layers, "marks": marks,
                      "grid": grid, "settle": settle, "profile": profile},
          "pose": r.get("pose"), "env": env_now, "w": W, "h": H, "settled": r.get("settled"),
          "pending": r.get("pending"), "frames_waited": r.get("frames_waited"),
          "files": {**files, **({"marks": out["marks_file"]} if "marks_file" in out else {}),
                    **({"grid": out["grid_file"]} if "grid_file" in out else {})},
          "sha256": r.get("sha256"), "legend": legend, "approx": approx,
          "ids_legend": [[e["id"], e["entity"]] for e in r.get("ids_legend") or []] or None, "warn": warn or None}
    out["sidecar"] = paths.jpath(SC.write(cap, sc))
    out.update({"legend": legend, "settled": r.get("settled"), "pending": r.get("pending"), "pose": r.get("pose"),
                "w": W, "h": H, "sha256": r.get("sha256")})
    if approx:
        out["approx"] = True
        out["marks_method"] = approx
    if files.get("ids"):
        out["ids_file"] = files["ids"]
    if files.get("depth"):
        out["depth_file"] = files["depth"]
    cid = out.pop("id")
    res = obj(cid, **out)
    if warn:
        res["warn"] = warn
    return res


# --------------------------------------------------------------------------- pick


def pick(points=None, cells: list[str] | None = None, capture_ref: str | None = None, target: str = "ariane",
         mode: str = "auto", profile: str | None = None) -> dict:
    pts = normalize_points(points)
    idx = IndexAccess(_profile(target, profile))
    b = get_backend(target)
    try:
        params: dict[str, Any] = {}
        if capture_ref:
            sc = SC.load(capture_ref)
            W, H = int(sc["w"]), int(sc["h"])
            params.update(space="capture", w=W, h=H)
            req_pose = (sc.get("request") or {}).get("pose")
            params["pose"] = req_pose or sc["pose"]
        else:
            W, H = b.viewport
            params["space"] = "window"
        for c in cells or []:
            x, y = O.cell_center(c, W, H)
            pts.append([round(x, 1), round(y, 1)])
        if not pts:
            raise SatkError("BAD_PARAMS", "give pixel coordinates (PX PY) or --cells D3")
        m = mode
        if m == "auto":
            m = "visible" if b.has("pick.visible") else "collision"
        params["points"] = pts
        params["mode"] = m
        r = b.call("pick", params)
        warn = list(b.warnings)
    finally:
        _close(b)
    rows, cands, lods = [], {}, []
    for i, hit in enumerate(r.get("hits") or []):
        pos = hit.get("pos") or [None, None, None]
        e = hit.get("entity")
        ln = link_entity(e, idx) if e else {"id": None, "link": "ground" if hit.get("hit") else "none"}
        if ln.get("candidates"):
            cands[str(i)] = ln["candidates"]
        if e and e.get("lod"):
            lods.append(i)
        model = f"model:{e['model_id']}" if e and e.get("model_id") is not None else None
        rows.append([hit.get("px"), hit.get("py"), ln["id"], model, (e or {}).get("model_name"),
                     *(round_pos(c) if c is not None else None for c in pos[:3]),
                     round(hit["dist"], 2) if hit.get("dist") is not None else None, ln["link"]])
    if lods:
        warn.append(f"LOD: row(s) {','.join(map(str, lods))} hit a LOD instance (low-detail stand-in), "
                    "not the HD model")
    out = table(["px", "py", "id", "model", "name", "x", "y", "z", "dist", "link"], rows,
                warn=warn + [w_ for w_ in idx.warnings if w_ not in warn])
    if cands:
        out["candidates"] = cands
    if lods:
        out["lod_rows"] = lods
    out["target"] = target
    out["mode"] = m
    return out


# --------------------------------------------------------------------------- set


def _refs_for(b, items: list[str], idx: IndexAccess) -> list[str]:
    refs: list[str] = []
    for it in items:
        s = str(it)
        if ":" not in s:
            refs.append(s)
            continue
        sid = Sid.parse(s)
        if sid.kind != "inst":
            raise SatkError("UNSUPPORTED", f"hide/highlight take inst: SIDs or runtime refs, got {s}")
        o = idx.get(str(sid))
        mid = int(str(o["model"]).split(":")[1])
        q = b.call("entity.query", {"center": o["pos"], "r": 2.0, "model": mid, "include_lod": True, "limit": 16})
        found = [e["ref"] for e in q.get("items") or [] if e.get("pos") and math.dist(e["pos"], o["pos"]) <= 0.1]
        if not found:
            raise SatkError("NOT_FOUND", f"{s} is not present in the {b.target} world")
        refs.extend(found)
    return refs


def set_view(target: str = "ariane", time: str | None = None, weather: int | None = None,
             overlays: list[str] | None = None, lod: str | None = None, draw_dist: float | None = None,
             hide: list[str] | None = None, highlight: list[str] | None = None, postfx: bool | None = None,
             wireframe: bool | None = None, area: int | None = None, profile: str | None = None) -> dict:
    env = {k: v for k, v in (("time", time), ("weather", weather)) if v is not None}
    view = {k: v for k, v in (("overlays", overlays), ("lod_mode", lod), ("draw_dist_mul", draw_dist),
                               ("postfx", postfx), ("wireframe", wireframe), ("area", area)) if v is not None}
    if not env and not view and hide is None and highlight is None:
        raise SatkError("BAD_PARAMS", "nothing to set (--time, --weather, --overlays, --lod, --hide, …)")
    idx = IndexAccess(_profile(target, profile))
    b = get_backend(target)
    out: dict[str, Any] = {"target": target}
    try:
        if env:
            b.require("env", "--time/--weather")
            out["env"] = b.call("env.set", env)
        if view or hide is not None or highlight is not None:
            b.require("view", ", ".join(list(view) + (["hide"] if hide is not None else []) +
                                        (["highlight"] if highlight is not None else [])))
            if hide is not None:
                view["hide"] = _refs_for(b, hide, idx)
            if highlight is not None:
                view["highlight"] = _refs_for(b, highlight, idx)
            out["view"] = b.call("view.set", view)["applied"]
        warn = list(b.warnings) + idx.warnings
    finally:
        _close(b)
    res = obj(None, **out)
    if warn:
        res["warn"] = warn
    return res


# --------------------------------------------------------------------------- bookmarks


def bookmark(action: str | None = "list", name: str | None = None, pose: dict | None = None, note: str | None = None,
             target: str = "ariane") -> dict:
    action = action or "list"
    if action == "list":
        rows = []
        for bm in store.list_all():
            p = bm["pose"]
            rows.append([f"bm:{bm['name']}", p.get("pos"), p.get("look") or p.get("ypr"), p.get("fov_h_deg"),
                         bm.get("note"), bm.get("created_at")])
        t = table(["id", "pos", "look", "fov", "note", "created"], rows)
        t["store"] = store.backend_name()
        return t
    if not name:
        raise SatkError("BAD_PARAMS", f"bookmark {action} needs a NAME")
    if action == "rm":
        if not store.remove(name):
            raise SatkError("NOT_FOUND", f"no bookmark {name!r}", hint="satk view bookmark list")
        return obj(f"bm:{name.lower().removeprefix('bm:')}", removed=True)
    if action != "save":
        raise SatkError("BAD_PARAMS", f"unknown action {action!r} (list|save|rm)")
    env = None
    if pose is None:
        b = get_backend(target)
        try:
            cam = b.call("camera.get", {})
            pose = dict(cam["pose"])
            pose.setdefault("fov_h_deg", cam.get("fov_h_deg"))
            if b.has("env"):
                env = b.call("env.get", {})
                env.pop("weathers", None)
        finally:
            _close(b)
    else:
        G.parse_pose(pose)
    rec = store.save(name, pose, env, note)
    return obj(f"bm:{rec['name']}", pose=pose, env=env, note=note, store=store.backend_name())


# --------------------------------------------------------------------------- replay / conformance


def replay(ref: str) -> dict:
    sc = SC.load(ref)
    req = sc.get("request") or {}
    pose = req.get("pose") or sc.get("pose")
    new = capture(target=sc["target"], pose=pose, env=sc.get("env") or req.get("env"), w=int(req.get("w") or sc["w"]),
                  h=int(req.get("h") or sc["h"]), layers=req.get("layers"), marks=int(req.get("marks") or 0),
                  grid=bool(req.get("grid")), settle=bool(req.get("settle", True)),
                  profile=req.get("profile"))
    same = new.get("sha256") == sc.get("sha256")
    out: dict[str, Any] = {"same": same, "sha256": new.get("sha256"), "original": sc.get("sha256"),
                           "capture": new["id"], "file": new["file"], "of": sc["id"]}
    if not same:
        try:
            cmpr = O.compare(sc["files"]["color"], new["file"], SC.capture_paths(new["id"])["cmp"])
            out["ssim"] = cmpr["ssim"]
            out["diff_file"] = paths.jpath(cmpr["file"])
        except SatkError as e:
            out["warn"] = [f"{e.code}: {e.msg}"]
    return obj(None, **out)


def conformance(target: str = "mock", only: str | None = None, files: list[str] | None = None,
                show: str = "failed") -> dict:
    from ..saap import client as C
    from ..saap import conformance as CF
    from .backends import role_of

    cases, errs = CF.load_cases(files)
    if errs:
        raise SatkError("BAD_PARAMS", f"conformance files are broken: {errs[0]}", data={"errors": errs[:10]})
    role = role_of(target)
    ep = C.read_endpoint(role)
    warn: list[str] = []
    backend = None
    if ep is not None and ep.get("protocol") == "saap/1" and C.endpoint_alive(ep, C.read_session(role)):
        sess = C.read_session(role) or {}
        if not sess.get("token"):
            raise SatkError("AUTH", f"no session token for {role!r}")
        drv: Any = CF.SaapDriver("127.0.0.1", int(ep["port"]), str(sess["token"]))
        impl = ep.get("impl")
    else:
        backend = get_backend(target)
        warn.extend(backend.warnings)
        drv = CF.BackendDriver(backend)
        impl = backend.impl
        if target == "mock":
            warn.append("ran in-process: transport/auth cases need a running endpoint (satk view start --target mock)")
    try:
        rep = CF.run(drv, cases, only=only, token=getattr(drv, "token", None))
    finally:
        if backend is not None:
            _close(backend)
    rows = [r for r in rep["results"] if show == "all" or r[2] != "pass"]
    t = table(["case", "caps", "result", "ms", "detail"], rows, total=rep["total"], warn=warn)
    t.update({"target": target, "impl": impl, "driver": drv.kind, "pass": rep["pass"], "fail": rep["fail"],
              "skip": rep["skip"], "percent": rep["percent"], "caps": rep["caps"]})
    if rep["fail"]:
        t["ok"] = True  # the run itself worked; failures are data
    return t
