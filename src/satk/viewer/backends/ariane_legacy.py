"""SAAP/1 on top of Ariane's ``ARIANE_IPC/1`` bridge (SPEC §4.8.8). Written from the format
description and the fork's ``agentbridge.cpp`` (read for interoperability only).

Transport: one TCP connection per command to 127.0.0.1:``ARIANE_ENGINE_TCP_PORT``; request
frame = ``!I`` length + ``ARIANE_AUTH/1 <token>\\nARIANE_IPC/1\\n<request id>\\n<command>\\n<fields…>``;
response frame = JSON (``protocol_version``, ``scene_revision``, ``request_id``, ``ok``, …,
``error`` as a string). Limits: request 1 MiB, response 4 MiB.

Mapping (SPEC §4.8.8): ``hello``/``status`` -> ``capabilities`` + ``camera_context`` (+
``environment``, ``satk_stats``); ``camera.set`` -> ``camera``; ``capture`` -> ``capture_pose`` /
``capture`` with ``settle_frames`` (patch V0-6; without it a warm-up capture is taken first);
``world.settle`` -> ``settle``; ``pick`` -> ``raycast_segment`` along our own per-pixel rays
(both spaces; ``screen_to_world`` only on bridges without it: it can report LOD instances);
``raycast`` -> ``raycast_segment``; ``entity.query`` -> ``inspect_zone_page``; ``entity.inspect`` ->
``inspect_zone_page`` + ``asset_detail``; ``env.*`` -> ``environment``; ``asset.render`` ->
``asset_preview`` (one view per call); ``quit`` -> ``quit`` or ``WM_CLOSE``. Everything else is
``UNSUPPORTED``. Capture size is the window size (no ``capture.size``).

FOV: librw's ``Camera::setFOV`` treats Ariane's ``fov`` as the horizontal FOV of a 4:3 image and
scales it to the window aspect with quarter angles; :func:`ariane_fov` / :func:`hfov_of`
convert exactly between that and SAAP's ``fov_h_deg``.

Runtime refs are ``"i<instance_id>@<x>,<y>"`` so that ``entity.inspect`` can find the instance
again with a 1 m ``inspect_zone_page`` query.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import re
import socket
import struct
import time
from pathlib import Path
from typing import Any

from ...core.errors import SatkError
from ...saap import frame as F
from ...saap import geom as G
from ...saap import png as P
from ...saap.server import precheck
from . import Backend

__all__ = ["ArianeLegacyBackend", "ariane_fov", "hfov_of", "entity_from_instance", "map_error"]

AR_43 = 4.0 / 3.0
MAX_RESPONSE = 4 * 1024 * 1024
_ids = itertools.count(1)
_WEATHER_MAX = 255


def ariane_fov(hfov_deg: float, aspect: float) -> float:
    """Ariane ``fov`` for an effective horizontal FOV at ``aspect`` (inverse of :func:`hfov_of`)."""
    h = math.radians(hfov_deg)
    return math.degrees(4.0 * math.atan(math.tan(h / 4.0) * AR_43 / aspect))


def hfov_of(fov_deg: float, aspect: float) -> float:
    """Effective horizontal FOV of Ariane ``fov`` at window ``aspect`` (librw ``setFOV``)."""
    f = math.radians(fov_deg)
    return math.degrees(4.0 * math.atan(math.tan(f / 4.0) * aspect / AR_43))


def map_error(msg: str) -> str:
    """SAAP error code for an Ariane error string."""
    m = (msg or "").lower()
    if "authentication" in m:
        return "AUTH"
    if m == "unknown command":
        return "UNKNOWN_METHOD"
    if "revision changed" in m:
        return "REVISION"
    if m.startswith("unknown model") or "does not belong" in m or "no ground surface" in m:
        return "NOT_FOUND"
    if "invalid" in m or "requires" in m or "must" in m or "accepts" in m or "stale" in m:
        return "BAD_PARAMS"
    if "busy" in m or "pending" in m:
        return "BUSY"
    return "INTERNAL"


def _f(x: float) -> str:
    s = f"{float(x):.6f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _r4(v) -> list[float]:
    return [round(float(c), 4) for c in v]


RAY_LEN = 3000.0


def _instance_at(hit: dict) -> dict:
    """Instance fields of a ``raycast_segment`` hit; the hit point locates the ref (no position)."""
    d = {k: v for k, v in hit.items() if v is not None and k not in ("point", "distance", "fraction")}
    pt = hit.get("point")
    if pt and "position" not in d:
        d["_locator"] = pt
    return d


def entity_from_instance(d: dict) -> dict:
    """SAAP EntityRef from an Ariane instance object (``screen_to_world``/``inspect_zone_page``)."""
    iid = d.get("instance_id")
    pos = d.get("position")
    loc = pos or d.get("_locator")
    ref = f"i{iid}" + (f"@{loc[0]:.2f},{loc[1]:.2f}" if loc else "")
    e: dict[str, Any] = {"ref": ref, "kind": "building"}
    if d.get("model_id") is not None:
        e["model_id"] = int(d["model_id"])
    if d.get("name"):
        e["model_name"] = str(d["name"]).lower()
    if pos:
        e["pos"] = _r4(pos)
    q = d.get("q_file")
    if isinstance(q, list) and len(q) == 4:  # IPL quaternion is conjugated in the world (SPEC V9)
        e["rot"] = {"q_world": [round(-q[0], 4) or 0.0, round(-q[1], 4) or 0.0, round(-q[2], 4) or 0.0, round(q[3], 4)]}
    if d.get("area") is not None:
        e["area"] = int(d["area"])
    if d.get("is_lod") is not None:
        e["lod"] = bool(d["is_lod"])
    kind, ipl, idx = d.get("ipl_kind"), d.get("ipl"), d.get("ipl_index")
    if kind == "binary" and ipl and idx is not None and int(idx) >= 0:
        e["src"] = {"kind": "ipl_bin", "ipl": str(ipl).lower(), "idx": int(idx)}
    elif kind == "text" and idx is not None and int(idx) >= 0 and (d.get("ipl_file") or ipl):
        f = str(d.get("ipl_file") or f"{ipl}.ipl").replace("\\", "/").lower()
        e["src"] = {"kind": "ipl_text", "file": f, "idx": int(idx)}
    else:
        e["src"] = {"kind": "model_pos"}
    return e


class ArianeLegacyBackend(Backend):
    """``call(method, params)`` with SAAP semantics on top of ``ARIANE_IPC/1``."""

    proto = "ariane-ipc/1"
    impl = "ariane-legacy"

    def __init__(self, target: str, role: str, port: int, token: str, *, pid: int | None = None,
                 exe: Path | None = None, timeout: float = 15.0, game_dir: str | None = None):
        super().__init__()
        self.target, self.role = target, role
        self.port, self.token, self.pid = int(port), token, pid
        self.exe = Path(exe) if exe else None
        self.timeout = timeout
        self.game_dir = game_dir
        self._caps_cache: dict | None = None

    @classmethod
    def from_discovery(cls, target: str, role: str) -> "ArianeLegacyBackend":
        from ...saap import client as C

        ep = C.read_endpoint(role) or {}
        sess = C.read_session(role) or {}
        if not sess.get("token"):
            raise SatkError("AUTH", f"no session token for {role!r} (work/run/sessions/{role}.json)")
        args = sess.get("args") or []
        gd = args[args.index("--game-dir") + 1] if "--game-dir" in args else None
        return cls(target, role, int(ep["port"]), str(sess["token"]), pid=ep.get("pid"),
                   exe=Path(ep["exe"]) if ep.get("exe") else None, game_dir=gd)

    # -- transport ---------------------------------------------------------------------------

    def cmd(self, command: str, *fields: Any, timeout: float | None = None) -> dict:
        """Send one ``ARIANE_IPC/1`` command; returns the JSON response (``ok`` true) or raises."""
        rid = f"{os.getpid():x}{next(_ids):06x}"
        parts = []
        for f in fields:
            s = _f(f) if isinstance(f, float) else str(f)
            if "\n" in s or "\r" in s:
                raise SatkError("BAD_PARAMS", f"field of {command} contains a line break")
            parts.append(s)
        payload = f"ARIANE_AUTH/1 {self.token}\nARIANE_IPC/1\n{rid}\n{command}" + "".join("\n" + p for p in parts)
        data = payload.encode("utf-8")
        if len(data) > F.MAX_REQUEST:
            raise SatkError("BAD_PARAMS", f"{command}: request exceeds 1 MiB")
        to = timeout or self.timeout
        try:
            s = socket.create_connection(("127.0.0.1", self.port), timeout=min(to, 5.0))
        except OSError as e:
            raise SatkError("NOT_READY", f"Ariane bridge is not reachable on 127.0.0.1:{self.port}: {e}",
                            hint="satk view start --target ariane") from None
        try:
            s.settimeout(to)
            s.sendall(struct.pack("!I", len(data)) + data)
            raw = F.read_frame(s, MAX_RESPONSE)
        except F.FrameError as e:
            if e.code == "TIMEOUT":
                raise SatkError("TIMEOUT", f"Ariane did not answer {command} within {to:g} s",
                                hint="is the Ariane window minimized or busy loading?") from None
            raise SatkError("NOT_READY", f"Ariane closed the connection during {command}: {e.msg}") from None
        except OSError as e:
            raise SatkError("NOT_READY", f"Ariane connection error during {command}: {e}") from None
        finally:
            s.close()
        try:
            resp = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise SatkError("PROTOCOL", f"Ariane sent invalid JSON for {command}: {e}") from None
        if not isinstance(resp, dict):
            raise SatkError("PROTOCOL", f"Ariane response to {command} is not an object")
        if not resp.get("ok"):
            msg = str(resp.get("error") or "error")
            data = {k: v for k, v in resp.items() if k not in ("ok", "error", "protocol_version", "request_id")}
            data["ariane_command"] = command
            raise SatkError(map_error(msg), f"ariane {command}: {msg}", data=data)
        return resp

    def ping(self) -> dict:
        return self.cmd("ping", timeout=3.0)

    # -- capabilities ------------------------------------------------------------------------

    def _capabilities(self) -> dict:
        if self._caps_cache is None:
            self._caps_cache = self.cmd("capabilities")
        return self._caps_cache

    def _commands(self) -> set[str]:
        return set(self._capabilities().get("commands") or [])

    def _has_settle(self) -> bool:
        return "settle" in self._commands()

    def _saap_caps(self) -> list[str]:
        caps = ["core", "camera", "capture", "pick", "entity.query", "entity.inspect", "env", "asset.render"]
        if self._has_settle():
            caps.insert(2, "world.settle")
        return caps

    def _context(self) -> dict:
        return self.cmd("camera_context")

    @staticmethod
    def _aspect(cam: dict) -> float:
        vp = cam.get("viewport") or [0, 0]
        if vp[0] and vp[1]:
            return float(vp[0]) / float(vp[1])
        return float(cam.get("aspect_ratio") or AR_43)

    def _pose_from(self, cam: dict, aspect: float) -> dict:
        pos = cam.get("position") or [0, 0, 0]
        look = cam.get("target") or pos
        out = {"pos": _r4(pos), "look": _r4(look)}
        if cam.get("fov") is not None:
            out["fov_h_deg"] = round(hfov_of(float(cam["fov"]), aspect), 3)
        return out

    # -- dispatch ----------------------------------------------------------------------------

    def call(self, method: str, params: dict | None = None) -> dict:
        params = params or {}
        if method == "hello":
            return self._m_hello(params)
        precheck(method, params, self._saap_caps())
        fn = getattr(self, "_m_" + method.replace(".", "_"))
        return fn(params)

    # -- core --------------------------------------------------------------------------------

    def _m_hello(self, p: dict) -> dict:
        if self._hello is not None:
            return self._hello
        cap = self._capabilities()
        cam = self._context().get("camera") or {}
        vp = cam.get("viewport") or (cap.get("satk") or {}).get("window") or [0, 0]
        build = {"id": str(cap.get("build_id") or "")}
        info = {}
        if self.exe is not None:
            from ..launcher import ariane_build_info

            info = ariane_build_info(self.exe)
            if info.get("ariane_commit"):
                build["git"] = str(info["ariane_commit"])[:9]
        satk = cap.get("satk") or {}
        game: dict[str, Any] = {"version": "1.0us"}
        root = satk.get("game_dir") or self.game_dir
        if root:
            game["root"] = str(root).replace("\\", "/")
        out = {"saap": 1, "role": self.role, "impl": self.impl, "impl_version": str(info.get("ariane_commit", ""))[:9],
               "build": build, "caps": self._saap_caps(), "game": game, "world": {"units": "m", "up": "z"},
               "limits": {"max_request": F.MAX_REQUEST, "max_response": MAX_RESPONSE},
               "viewport": {"w": int(vp[0] or 1), "h": int(vp[1] or 1)},
               "ariane": {"patches": satk.get("patches") or [], "commands": sorted(self._commands())}}
        self._hello = out
        return out

    def _m_ping(self, p: dict) -> dict:
        self.ping()
        return {"t_ms": round(time.monotonic() * 1000, 3)}

    def _m_status(self, p: dict) -> dict:
        ctx = self._context()
        cam = ctx.get("camera") or {}
        aspect = self._aspect(cam)
        env = self._m_env_get({})
        env.pop("weathers", None)
        out: dict[str, Any] = {"frame": 0, "fps": 0.0, "pose": self._pose_from(cam, aspect), "env": env,
                               "streaming": {"pending": 0},
                               "rev": {"scene": int(ctx.get("scene_revision") or 0),
                                       "camera": int(cam.get("camera_revision") or 0)}}
        if "satk_stats" in self._commands():
            st = self.cmd("satk_stats")
            out["frame"] = int(st.get("frames") or 0)
            out["fps"] = float(st.get("fps") or 0.0)
            out["streaming"] = {"pending": int(st.get("pending_models") or 0)}
        vp = cam.get("viewport") or [0, 0]
        from ..launcher import is_minimized

        out["window"] = {"w": int(vp[0]), "h": int(vp[1]), "minimized": bool(self.pid and is_minimized(int(self.pid)))}
        return out

    def _m_quit(self, p: dict) -> dict:
        if "quit" in self._commands():
            self.cmd("quit")
            return {}
        from ...saap import client as C
        from ..launcher import close_windows

        verified = self.pid and C.verify_pid(C.read_endpoint(self.role), C.read_session(self.role))[0] == "ok"
        if verified and close_windows(int(self.pid)):
            return {}
        raise SatkError("UNSUPPORTED", "this Ariane build has no quit command and no window to close")

    # -- camera ------------------------------------------------------------------------------

    def _m_camera_get(self, p: dict) -> dict:
        cam = self._context().get("camera") or {}
        aspect = self._aspect(cam)
        pose = self._pose_from(cam, aspect)
        return {"pose": pose, "fov_h_deg": pose.get("fov_h_deg", 70.0), "near": float(cam.get("near_plane") or 0.1),
                "far": float(cam.get("far_plane") or 3000.0), "rev": int(cam.get("camera_revision") or 0)}

    def _set_camera(self, pose: G.Pose, fov_h: float | None, cam: dict) -> int:
        aspect = self._aspect(cam)
        fov = ariane_fov(fov_h, aspect) if fov_h is not None else float(cam.get("fov") or 70.0)
        look = pose.look
        r = self.cmd("camera", *pose.pos, *look, fov)
        return int(r.get("camera_revision") or 0)

    def _m_camera_set(self, p: dict) -> dict:
        cam = self._context().get("camera") or {}
        if "expect_rev" in p and int(cam.get("camera_revision") or 0) != int(p["expect_rev"]):
            rev = int(cam.get("camera_revision") or 0)
            raise SatkError("REVISION", f"camera revision is {rev}, not {p['expect_rev']}", data={"rev": rev})
        pose = G.parse_pose(p["pose"], p.get("fov_h_deg"))
        if pose.roll:
            self.warnings.append("ariane camera: roll is ignored by the 'camera' command")
        rev = self._set_camera(pose, pose.fov_h_deg, cam)
        out: dict[str, Any] = {"pose": self._m_camera_get({})["pose"], "rev": rev}
        stream = p.get("stream", "none")
        if stream != "none":
            if not self._has_settle():
                raise SatkError("UNSUPPORTED", "this Ariane build has no settle command (patch V0-6)",
                                data={"capability": "world.settle"})
            out["settled"] = bool(self.cmd("settle", 120, 2, timeout=60.0).get("settled"))
        return out

    def _m_camera_release(self, p: dict) -> dict:
        return {}  # the viewer camera simply stays where it is; the user can fly again

    def _m_world_settle(self, p: dict) -> dict:
        mf, qf = int(p.get("max_frames", 120)), int(p.get("quiet_frames", 2))
        to = max(5.0, int(p.get("timeout_ms", 5000)) / 1000.0 + 5.0, mf / 20.0)
        r = self.cmd("settle", mf, qf, timeout=to)
        return {"settled": bool(r.get("settled")), "frames": int(r.get("frames_waited") or 0),
                "pending": int(r.get("pending_models") or 0)}

    # -- capture -----------------------------------------------------------------------------

    def _m_capture(self, p: dict) -> dict:
        layers = list(p.get("layers") or ["color"])
        for layer in layers:
            if layer in ("ids", "depth"):
                raise SatkError("UNSUPPORTED", f"capability 'capture.{layer}' not available (Ariane legacy bridge)",
                                data={"capability": f"capture.{layer}"})
            if layer != "color":
                raise SatkError("UNSUPPORTED", f"layer {layer!r} is not supported", data={"layer": layer})
        ctx = self._context()
        cam = ctx.get("camera") or {}
        vp = cam.get("viewport") or [0, 0]
        if ("w" in p and int(p["w"]) != int(vp[0])) or ("h" in p and int(p["h"]) != int(vp[1])):
            raise SatkError("UNSUPPORTED", f"capture size is the window size {vp[0]}x{vp[1]} (no capture.size)",
                            data={"capability": "capture.size", "window": vp})
        prefix = p["path_prefix"]
        if not os.path.isabs(prefix):
            raise SatkError("BAD_PARAMS", "path_prefix must be an absolute path")
        path = (prefix + ".png").replace("\\", "/")
        from ...core import paths

        paths.ensure_writable(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        settle = p.get("settle", {"max_frames": 120, "quiet_frames": 2})
        mf, qf = int(settle.get("max_frames", 120)), int(settle.get("quiet_frames", 2))
        has_settle = self._has_settle()
        old_env = None
        if p.get("env"):
            old_env = self._m_env_get({})
            self._m_env_set(p["env"])
        try:
            label = "satk"
            to = max(30.0, mf / 15.0 + 10.0)

            def shoot() -> dict:
                sf = [mf, qf] if has_settle else []
                if p.get("pose") is not None:
                    pose = G.parse_pose(p["pose"])
                    aspect = self._aspect(cam)
                    c = G.Camera(pose, int(vp[0] or 1), int(vp[1] or 1), pose.fov_h_deg or 70.0)
                    fov = ariane_fov(pose.fov_h_deg, aspect) if pose.fov_h_deg else float(cam.get("fov") or 70.0)
                    return self.cmd("capture_pose", path, label, *pose.pos, *pose.look, *c.u, fov,
                                    "", *sf, timeout=to)
                return self.cmd("capture", path, label, *sf, timeout=to)

            if not has_settle and mf > 0:
                shoot()  # warm-up: lets the streamer load what the pose shows (no V0-6 patch)
            r = shoot()
            if p.get("pose") is not None and not r.get("restored", False):
                # Ariane fails closed when its live-camera tracker bumped the revision during
                # settling; restore the previous camera ourselves (stateless capture).
                self.cmd("camera", *cam["position"], *cam["target"], float(cam.get("fov") or 70.0))
        finally:
            if old_env is not None:
                self._m_env_set({k: old_env[k] for k in ("time", "weather", "weather_b", "blend") if k in old_env})
        data = Path(path).read_bytes()
        size = P.size(path) or (int(vp[0]), int(vp[1]))
        ap = r.get("actual_pose") or {}
        aspect = size[0] / size[1] if size[1] else AR_43
        pose_out = {"pos": _r4(ap.get("position") or [0, 0, 0]), "look": _r4(ap.get("target") or [0, 0, 1])}
        if ap.get("fov") is not None:
            pose_out["fov_h_deg"] = round(hfov_of(float(ap["fov"]), aspect), 3)
        return {"files": {"color": path}, "w": int(size[0]), "h": int(size[1]), "pose": pose_out,
                "frame": 0, "settled": bool(r.get("settled", False)),
                "pending": int(r.get("pending_models") or 0), "frames_waited": int(r.get("frames_waited") or 0),
                "sha256": hashlib.sha256(data).hexdigest()}

    # -- pick --------------------------------------------------------------------------------

    def _hit(self, r: dict, px: float, py: float) -> dict:
        out: dict[str, Any] = {"px": px, "py": py, "hit": bool(r.get("hit"))}
        if not r.get("hit"):
            return out
        if r.get("point"):
            out["pos"] = _r4(r["point"])
        if r.get("normal"):
            out["normal"] = [round(float(c), 6) for c in r["normal"]]
        if r.get("depth") is not None:
            out["dist"] = round(max(0.0, float(r["depth"])), 4)
        if r.get("source") == "geometry" and r.get("instance_id") is not None:
            out["entity"] = entity_from_instance(r)
        return out

    @staticmethod
    def _window_camera(cam: dict, W: int, H: int) -> G.Camera:
        """The live window camera with exactly the rays of Ariane's ``screen_to_world``.

        Same pixel centres and FOV (librw ``viewWindow`` = :func:`hfov_of`); the basis is taken
        from ``camera_context`` (``forward``/``right``/``up``), so a rolled camera is honoured.
        """
        pose = G.parse_pose({"pos": cam["position"], "look": cam["target"]})
        c = G.Camera(pose, W, H, hfov_of(float(cam.get("fov") or 70.0), W / H))
        basis = [cam.get(k) for k in ("forward", "right", "up")]
        if all(isinstance(v, (list, tuple)) and len(v) == 3 for v in basis):
            f, r, u = (tuple(float(x) for x in v) for v in basis)
            if min(G.length(f), G.length(r), G.length(u)) > 0.5:
                c.f, c.r, c.u = f, r, u
        return c

    def _m_pick(self, p: dict) -> dict:
        """Collision picks with ``raycast_segment`` along our own per-pixel rays.

        Both spaces use the same path: ``capture`` builds the rays for a ``w×h`` image at the
        request pose (stateless), ``window`` for the live camera. ``screen_to_world`` is only a
        fallback for bridges without ``raycast_segment``: it searches the render list, and where
        a LOD and its HD model share the hit point it reports the LOD instance (live: Grove
        Street at 82 m gave ``lod1carlshou1_lae`` instead of ``carlshou1_lae2``), whereas
        ``raycast_segment`` skips LOD parents (``m_numChildren > 0``).
        """
        if p.get("mode") == "visible":
            raise SatkError("UNSUPPORTED", "capability 'pick.visible' not available (collision picking only)",
                            data={"capability": "pick.visible"})
        ctx = self._context()
        cam = ctx.get("camera") or {}
        W, H = (int(v) for v in (cam.get("viewport") or [0, 0]))
        if not W or not H:
            raise SatkError("NOT_READY", "Ariane reported an empty viewport")
        hits = []
        if p.get("space", "window") == "capture":
            w, h = int(p.get("w", W)), int(p.get("h", H))
            if p.get("pose") is not None:
                pose = G.parse_pose(p["pose"])
            else:
                pose = G.parse_pose({"pos": cam["position"], "look": cam["target"]})
            fov = pose.fov_h_deg or hfov_of(float(cam.get("fov") or 70.0), self._aspect(cam))
            c = G.Camera(pose, w, h, fov)
        elif "raycast_segment" in self._commands():
            w, h = W, H
            c = self._window_camera(cam, W, H)
        else:
            for px, py in p["points"]:
                if not (0 <= float(px) < W and 0 <= float(py) < H):
                    hits.append({"px": px, "py": py, "hit": False})
                    continue
                r = self.cmd("screen_to_world", float(px), float(py), W, H)
                hits.append(self._hit(r, px, py))
            return {"hits": hits}
        for px, py in p["points"]:
            if not (0 <= float(px) < w and 0 <= float(py) < h):
                hits.append({"px": px, "py": py, "hit": False})
                continue
            o, d = c.ray(float(px), float(py))
            r = self.cmd("raycast_segment", *o, *G.add(o, G.mul(d, RAY_LEN)))
            hit = (r.get("ray") or {}).get("hit")
            out: dict[str, Any] = {"px": px, "py": py, "hit": bool(hit)}
            if hit:
                out["pos"] = _r4(hit.get("point") or o)
                out["dist"] = round(float(hit.get("distance") or 0.0), 4)
                if hit.get("instance_id") is not None:
                    out["entity"] = entity_from_instance(_instance_at(hit))
            hits.append(out)
        return {"hits": hits}

    def _m_raycast(self, p: dict) -> dict:
        a, b = p["from"], p["to"]
        if math.dist(a, b) < 1e-3:
            raise SatkError("BAD_PARAMS", "raycast endpoints must differ")
        r = self.cmd("raycast_segment", *map(float, a), *map(float, b))
        hit = (r.get("ray") or {}).get("hit")
        if not hit:
            return {"hit": False}
        out: dict[str, Any] = {"hit": True, "pos": _r4(hit.get("point") or a), "dist": round(float(hit.get("distance") or 0), 4)}
        if hit.get("instance_id") is not None:
            out["entity"] = entity_from_instance(_instance_at(hit))
        return out

    # -- entities ----------------------------------------------------------------------------

    def _m_entity_query(self, p: dict) -> dict:
        cx, cy = float(p["center"][0]), float(p["center"][1])
        r = float(p["r"])
        lim = int(p.get("limit", 50))
        try:
            off = int(p.get("cursor") or 0)
        except ValueError:
            raise SatkError("BAD_PARAMS", "bad cursor") from None
        kinds = set(p.get("kinds") or [])
        model = p.get("model")
        lods = "1" if p.get("include_lod") else "0"
        filt = bool(kinds) or model is not None
        items: list[dict] = []
        total = None
        nxt: int | None = off
        while nxt is not None and len(items) < lim:
            page = self.cmd("inspect_zone_page", cx, cy, r, nxt, 256 if filt else lim, lods)
            info = page.get("page") or {}
            total = info.get("total", total)
            for it in page.get("items") or []:
                e = entity_from_instance(it)
                if kinds and e.get("kind") not in kinds:
                    continue
                if model is not None and e.get("model_id") != model:
                    continue
                items.append(e)
            nxt = info.get("next_offset")
            if not filt:
                break
        items = items[:lim]
        out: dict[str, Any] = {"items": items}
        if total is not None and not filt:
            out["total"] = int(total)
        if nxt is not None:
            out["next"] = str(nxt)
        return out

    def _find_ref(self, ref: str) -> dict:
        m = re.match(r"^i(-?\d+)@(-?[\d.]+),(-?[\d.]+)$", ref or "")
        if not m:
            raise SatkError("NOT_FOUND", f"no entity {ref!r} (Ariane refs look like i<id>@<x>,<y>)")
        iid, x, y = int(m.group(1)), float(m.group(2)), float(m.group(3))
        for radius in (1.0, 250.0):  # the locator is the instance position or (raycast) a hit point
            off: int | None = 0
            while off is not None:
                page = self.cmd("inspect_zone_page", x, y, radius, off, 256, "1")
                for it in page.get("items") or []:
                    if int(it.get("instance_id", -1)) == iid:
                        return it
                off = (page.get("page") or {}).get("next_offset")
        raise SatkError("NOT_FOUND", f"entity {ref!r} no longer exists")

    def _m_entity_inspect(self, p: dict) -> dict:
        it = self._find_ref(p["ref"])
        e = entity_from_instance(it)
        model: dict[str, Any] = {"id": int(it.get("model_id", -1))}
        try:
            a = self.cmd("asset_detail", int(it.get("model_id", -1))).get("asset") or {}
            model.update({"name": str(a.get("name", "")).lower(), "draw": float(a.get("draw_distance") or 0)})
        except SatkError:
            pass
        lod: dict[str, Any] = {"children": []}
        if isinstance(it.get("lod"), dict):
            L = it["lod"]
            lod["parent"] = entity_from_instance({"instance_id": L.get("instance_id"), "model_id": L.get("model_id"),
                                                  "ipl": L.get("ipl"), "ipl_kind": L.get("ipl_kind"),
                                                  "ipl_index": L.get("ipl_index"), "is_lod": True})
        runtime = {k: it[k] for k in ("instance_id", "object_key", "rotation", "agent_scene", "added", "dirty")
                   if k in it}
        return {"entity": e, "model": model, "lod": lod, "runtime": runtime}

    # -- env ---------------------------------------------------------------------------------

    def _m_env_get(self, p: dict) -> dict:
        r = self.cmd("environment")
        return {"time": f"{int(r.get('hour', 12)):02d}:{int(r.get('minute', 0)):02d}",
                "weather": int(r.get("weather_a", 0)), "weather_b": int(r.get("weather_b", 0)),
                "blend": round(float(r.get("blend", 0.0)), 4), "freeze": False,
                "weathers": [{"id": int(w.get("id", i)), "name": str(w.get("name", ""))}
                             for i, w in enumerate(r.get("weathers") or [])]}

    def _m_env_set(self, p: dict) -> dict:
        cur = self._m_env_get({})
        t = p.get("time", cur["time"])
        h, m = (int(x) for x in t.split(":"))
        wa = int(p.get("weather", cur["weather"]))
        wb = int(p.get("weather_b", wa if "weather" in p else cur["weather_b"]))
        bl = float(p.get("blend", 0.0 if "weather" in p else cur["blend"]))
        if p.get("freeze"):
            self.warnings.append("ariane environment: freeze is not supported (time does not advance anyway)")
        self.cmd("environment", h, m, wa, wb, bl)
        out = self._m_env_get({})
        out.pop("weathers", None)
        return out

    # -- assets ------------------------------------------------------------------------------

    def _m_asset_render(self, p: dict) -> dict:
        prefix = p["path_prefix"]
        if not os.path.isabs(prefix):
            raise SatkError("BAD_PARAMS", "path_prefix must be an absolute path")
        from ...core import paths

        size = int(p.get("size", 256))
        if float(p.get("el", 25)) != 25 or p.get("bg", "transparent") != "transparent":
            self.warnings.append("ariane asset_preview: elevation and background are fixed")
        files = []
        for az in p.get("views") or [45, 135, 225, 315]:
            name = f"{int(az)}" if float(az).is_integer() else f"{az}"
            path = (prefix + f"_{name}.png").replace("\\", "/")
            paths.ensure_writable(path)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.cmd("asset_preview", p["model"], path, math.radians(float(az)), size, timeout=60.0)
            files.append(path)
        return {"files": files}
