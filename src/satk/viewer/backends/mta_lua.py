"""SAAP/1 on the real game: MTA client + server with the Lua resource ``satk-agent`` (G0, WP-13/M2-14).

Protocol ``mta-lua/1`` (SPEC §4.12.3, report 26 §12; Russian guide: ``docs/ru/mta-agent.md``):

* the server resource exports ``rpc`` over MTA's HTTP server: ``POST
  http://127.0.0.1:<httpport>/satk-agent/call/rpc`` with the body ``[{"token", "id", "method",
  "params"}]``; the reply is ``[{"ok": true, "result": {...}}]``, ``[{"ok": false, "error": {code,
  message, data}}]`` (SAAP/1 codes) or ``[{"ok": true, "pending": "<rid>"}]`` for methods that run
  on the game client; those are polled with ``_poll {rid}``;
* the token (64 hex) lives in the private server's ``settings.xml`` (``@satk-agent.token``) and in
  ``work/run/sessions/game.json``; the server listens on 127.0.0.1 only and Lua checks the address;
* the client runs a request as a coroutine resumed once per frame: camera (``setCameraMatrix``,
  FOV measured with ``getWorldFromScreenPosition``), capture (``dxCreateScreenSource`` ->
  ``dxGetTexturePixels`` -> ``dxConvertPixels("png")`` -> latent event -> file in the resource,
  moved here to ``path_prefix.png``), picking (``processLineOfSight`` with building info ->
  ``src.model_pos`` -> SID via the index), ``setTime``/``setWeather``, the ``onDebugMessage`` ring
  and ``lua.exec`` on either side.

:class:`MtaLuaBackend` is what :func:`satk.viewer.backends.get_backend` returns for an endpoint
whose discovery file says ``"protocol": "mta-lua/1"``. The rest of this module runs the private
loopback server (:func:`start_server`, :func:`stop`, :func:`status`) under
``<work>/mta/server`` - binaries copied from the fork's ``Bin/server``, generated
``satk-agent.conf``/``satk-acl.xml``/``settings.xml`` - and the fork's client
(:func:`start_client` after :func:`client_preflight`). Command line (there is no satk operation
yet; ``satk view start --target game`` belongs to the viewer package)::

    python -m satk.viewer.backends.mta_lua up [--client] | client | status | down | preflight

G0 limits: one client; ``world.settle`` waits frames after ``enginePreloadWorldArea`` (the
streaming queue is invisible: ``pending`` is 0); no ID buffer, depth, ``entity.*`` or ``view.set``.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import http.client
import json
import math
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ...core.errors import SatkError
from ...saap import geom as G
from ...saap import png as P
from ...saap.server import precheck
from . import Backend

__all__ = ["PROTOCOL", "IMPL", "RESOURCE", "CAPS", "MtaLuaBackend", "server_config", "acl_xml", "settings_xml",
           "resource_source", "prepare_server", "start_server", "start_client", "client_preflight", "status",
           "loopback_port_free", "stop", "main"]

PROTOCOL = "mta-lua/1"
IMPL = "mta-lua"
RESOURCE = "satk-agent"
ROLE = "game"
#: SAAP capabilities of the G0 endpoint (SPEC §4.8.7 column "MTA Lua G0").
CAPS = ["core", "camera", "world.settle", "capture", "capture.size", "pick", "env", "log", "console", "lua"]
RAY_LEN = 3000.0
MAX_REQUEST = 1 << 20
MAX_RESPONSE = 8 << 20
#: Seconds a relayed request may take (capture: settle + PNG transfer).
TIMEOUTS = {"capture": 90.0, "world.settle": 70.0, "camera.set": 30.0, "_rays": 30.0, "pick": 20.0}
DEFAULT_TIMEOUT = 20.0
READY_LINE = "Server started and is ready to accept connections!"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB: survive the caller's job object when allowed


# --------------------------------------------------------------------------- helpers


def _obj(x: Any) -> dict:
    """Lua empty tables arrive as ``[]``; objects are dicts."""
    return x if isinstance(x, dict) else {}


def _list(x: Any) -> list:
    return x if isinstance(x, list) else []


def _vec(v: Any, nd: int = 4) -> list[float] | None:
    if isinstance(v, list) and len(v) == 3 and all(isinstance(c, (int, float)) for c in v):
        return [round(float(c), nd) or 0.0 for c in v]
    return None


def _pose(d: Any) -> dict | None:
    d = _obj(d)
    pos, look = _vec(d.get("pos")), _vec(d.get("look"))
    if pos is None or look is None:
        return None
    out: dict[str, Any] = {"pos": pos, "look": look}
    f = d.get("fov_h_deg")
    if isinstance(f, (int, float)) and 0 < f < 180:
        out["fov_h_deg"] = round(float(f), 3)
    return out


def _entity(e: Any) -> dict | None:
    e = _obj(e)
    if not e.get("ref"):
        return None
    out: dict[str, Any] = {"ref": str(e["ref"])[:128], "kind": e.get("kind") or "building"}
    if isinstance(e.get("model_id"), (int, float)):
        out["model_id"] = int(e["model_id"])
    if e.get("model_name"):
        out["model_name"] = str(e["model_name"])
    if _vec(e.get("pos")):
        out["pos"] = _vec(e["pos"])
    src = _obj(e.get("src"))
    if src.get("kind") == "runtime" and src.get("type") and src.get("id") not in (None, ""):
        out["src"] = {"kind": "runtime", "type": str(src["type"]), "id": str(src["id"])}
    else:
        out["src"] = {"kind": "model_pos"}
    return out


def _hit(h: Any, px: float | None = None, py: float | None = None) -> dict:
    h = _obj(h)
    out: dict[str, Any] = {}
    if px is not None:
        out["px"], out["py"] = px, py
    elif "px" in h:
        out["px"], out["py"] = h.get("px"), h.get("py")
    out["hit"] = bool(h.get("hit"))
    if out["hit"]:
        if _vec(h.get("pos")):
            out["pos"] = _vec(h["pos"])
        if _vec(h.get("normal"), 6):
            out["normal"] = _vec(h["normal"], 6)
        if isinstance(h.get("dist"), (int, float)):
            out["dist"] = round(max(0.0, float(h["dist"])), 4)
        e = _entity(h.get("entity"))
        if e:
            out["entity"] = e
    return out


def _iso(t: float) -> str:
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- backend


class MtaLuaBackend(Backend):
    """``call(method, params)`` with SAAP/1 semantics on top of the ``satk-agent`` rpc export."""

    proto = PROTOCOL
    impl = IMPL

    def __init__(self, target: str, role: str, port: int, token: str, *, pid: int | None = None,
                 server_root: str | Path | None = None, session: dict | None = None, timeout: float | None = None):
        super().__init__()
        self.target, self.role = target, role
        self.port, self.token, self.pid = int(port), str(token), pid
        self.server_root = Path(server_root) if server_root else None
        self.session = dict(session or {})
        self.timeout = timeout
        self._n = 0
        self._settle_warned = False

    @classmethod
    def from_discovery(cls, target: str, role: str) -> "MtaLuaBackend":
        from ...saap import client as C

        ep = C.read_endpoint(role) or {}
        sess = C.read_session(role) or {}
        if not sess.get("token"):
            raise SatkError("AUTH", f"no session token for {role!r} (work/run/sessions/{role}.json)")
        return cls(target, role, int(ep["port"]), str(sess["token"]), pid=ep.get("pid"),
                   server_root=sess.get("server_root") or ep.get("server_root"), session=sess)

    # -- transport ---------------------------------------------------------------------------

    def _post(self, req: dict, timeout: float) -> dict:
        body = json.dumps([req], ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_REQUEST:
            raise SatkError("BAD_PARAMS", f"{req.get('method')}: request exceeds 1 MiB")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.request("POST", f"/{RESOURCE}/call/rpc", body,
                         {"Content-Type": "application/json", "Connection": "close"})
            resp = conn.getresponse()
            data = resp.read(MAX_RESPONSE + 1)
        except socket.timeout:
            raise SatkError("TIMEOUT", f"the MTA server did not answer {req.get('method')} within {timeout:g} s") from None
        except OSError as e:
            raise SatkError("NOT_READY", f"the MTA server is not reachable on 127.0.0.1:{self.port}: {e}",
                            hint="python -m satk.viewer.backends.mta_lua status") from None
        finally:
            conn.close()
        if resp.status == 401:
            raise SatkError("AUTH", "the MTA HTTP server refused the rpc call (ACL: resource.satk-agent.http)")
        if resp.status != 200:
            raise SatkError("PROTOCOL", f"the MTA HTTP server answered {resp.status} {resp.reason}")
        if len(data) > MAX_RESPONSE:
            raise SatkError("PROTOCOL", "rpc response exceeds 8 MiB")
        text = data.decode("utf-8", errors="replace").strip()
        if text.startswith("error:"):
            raise SatkError("NOT_READY", f"satk-agent: {text[6:].strip()}",
                            hint="the resource is not running: see work/mta/server/mods/deathmatch/logs")
        try:
            out = json.loads(text)
        except ValueError:
            raise SatkError("PROTOCOL", f"rpc answered non-JSON: {text[:120]!r}") from None
        if isinstance(out, list) and len(out) == 1 and isinstance(out[0], dict):
            return out[0]
        raise SatkError("PROTOCOL", f"rpc answered an unexpected shape: {text[:120]!r}")

    def rpc(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> Any:
        """One agent request; waits for relayed (client) requests. Returns ``result``."""
        self._n += 1
        to = timeout or self.timeout or TIMEOUTS.get(method, DEFAULT_TIMEOUT)
        req = {"token": self.token, "id": f"p{os.getpid()}-{self._n}", "method": method, "params": params or {}}
        r = self._post(req, min(to, 30.0))
        deadline = time.monotonic() + to
        delay = 0.01
        while r.get("ok") and "pending" in r and "result" not in r:
            rid = str(r["pending"])
            if time.monotonic() > deadline:
                try:
                    self._post({"token": self.token, "method": "_cancel", "params": {"rid": rid}}, 5.0)
                except SatkError:
                    pass
                raise SatkError("TIMEOUT", f"the game client did not answer {method} within {to:g} s",
                                hint="is the MTA window minimized? (D3D9 does not render then)",
                                data={"method": method})
            time.sleep(delay)
            delay = min(0.05, delay * 1.5)
            r = self._post({"token": self.token, "method": "_poll", "params": {"rid": rid}}, 10.0)
        if not r.get("ok"):
            err = _obj(r.get("error"))
            code = str(err.get("code") or "INTERNAL")
            data = _obj(err.get("data"))
            hint = data.pop("hint", None)
            raise SatkError(code, str(err.get("message") or code), hint=hint, data=data or None)
        return r.get("result")

    # -- dispatch ----------------------------------------------------------------------------

    def call(self, method: str, params: dict | None = None) -> dict:
        params = params or {}
        if method == "hello":
            return self._m_hello(params)
        precheck(method, params, CAPS)
        fn = getattr(self, "_m_" + method.replace(".", "_"))
        return fn(params)

    def _client(self) -> dict:
        return _obj(self.hello.get("mta", {}).get("client"))

    # -- core --------------------------------------------------------------------------------

    def _m_hello(self, p: dict) -> dict:
        if self._hello is not None:
            return self._hello
        h = _obj(self.rpc("hello"))
        cl = _obj(h.get("client"))
        srv = _obj(h.get("server"))
        game: dict[str, Any] = {"version": "1.0us"}
        if self.session.get("gta_path"):
            game["root"] = str(self.session["gta_path"]).replace("\\", "/")
        out = {"saap": 1, "role": self.role, "impl": IMPL, "impl_version": str(h.get("agent_version") or ""),
               "build": {"id": f"mta-server-{srv.get('version') or 'unknown'}"}, "caps": list(CAPS), "game": game,
               "world": {"units": "m", "up": "z"}, "limits": {"max_request": MAX_REQUEST, "max_response": MAX_RESPONSE},
               "viewport": {"w": int(cl.get("w") or 1), "h": int(cl.get("h") or 1)},
               "mta": {"server": srv, "client": cl or None, "players": _list(h.get("players"))}}
        if not cl:
            self.warnings.append("NOT_READY: no game client has joined the agent server; camera, capture and pick "
                                 "fail until one does")
        self._hello = out
        return out

    def _m_ping(self, p: dict) -> dict:
        self.rpc("ping")
        return {"t_ms": round(time.monotonic() * 1000, 3)}

    def _m_status(self, p: dict) -> dict:
        r = _obj(self.rpc("status"))
        rev = _obj(r.get("rev"))
        out: dict[str, Any] = {"frame": int(r.get("frame") or 0), "fps": round(float(r.get("fps") or 0), 1),
                               "streaming": {"pending": int(_obj(r.get("streaming")).get("pending") or 0)},
                               "rev": {"scene": int(rev.get("scene") or 0), "camera": int(rev.get("camera") or 0)}}
        pose = _pose(r.get("pose"))
        if pose:
            out["pose"] = pose
        env = self._env(r.get("env"))
        if env:
            out["env"] = env
        win = _obj(r.get("window"))
        if win:
            out["window"] = {"w": int(win.get("w") or 0), "h": int(win.get("h") or 0),
                             "minimized": self._minimized()}
        if r.get("client") is False:
            out["client"] = False
            self.warnings.append("NOT_READY: no game client: status of the server only")
        return out

    def _minimized(self) -> bool:
        pid = self.session.get("client_pid")
        if not pid:
            return False
        try:
            from ..launcher import is_minimized

            return bool(is_minimized(int(pid)))
        except (ImportError, OSError, ValueError):
            return False

    def _m_quit(self, p: dict) -> dict:
        try:
            self.rpc("quit", timeout=10.0)
        finally:
            _stop_client(self.session)
        return {}

    # -- camera ------------------------------------------------------------------------------

    def _m_camera_get(self, p: dict) -> dict:
        r = _obj(self.rpc("camera.get"))
        pose = _pose(r.get("pose")) or {"pos": [0.0, 0.0, 0.0], "look": [0.0, 1.0, 0.0]}
        out = {"pose": pose, "fov_h_deg": float(r.get("fov_h_deg") or pose.get("fov_h_deg") or G.DEFAULT_FOV),
               "rev": int(r.get("rev") or 0)}
        for k in ("near", "far"):
            if isinstance(r.get(k), (int, float)):
                out[k] = float(r[k])
        return out

    def _m_camera_set(self, p: dict) -> dict:
        pose = G.parse_pose(p["pose"], p.get("fov_h_deg"))
        q: dict[str, Any] = {"pos": list(pose.pos), "look": list(pose.look), "roll": pose.roll,
                             "stream": p.get("stream", "none")}
        if pose.fov_h_deg is not None:
            q["fov_h_deg"] = pose.fov_h_deg
        if "expect_rev" in p:
            q["expect_rev"] = int(p["expect_rev"])
        if q["stream"] != "none":
            self._warn_settle()
        r = _obj(self.rpc("camera.set", q))
        out: dict[str, Any] = {"pose": _pose(r.get("pose")) or G.pose_dict(pose), "rev": int(r.get("rev") or 0)}
        if "settled" in r:
            out["settled"] = bool(r["settled"])
        return out

    def _m_camera_release(self, p: dict) -> dict:
        self.rpc("camera.release")
        return {}

    def _warn_settle(self) -> None:
        if not self._settle_warned:
            self._settle_warned = True
            self.warnings.append("world.settle (G0) waits frames after enginePreloadWorldArea; the streaming "
                                 "queue is not visible, so pending is always 0")

    def _m_world_settle(self, p: dict) -> dict:
        self._warn_settle()
        to = int(p.get("timeout_ms", 5000)) / 1000.0 + 15.0
        r = _obj(self.rpc("world.settle", {k: p[k] for k in ("max_frames", "quiet_frames", "timeout_ms") if k in p},
                          timeout=to))
        return {"settled": bool(r.get("settled")), "frames": int(r.get("frames") or 0),
                "pending": int(r.get("pending") or 0)}

    # -- capture -----------------------------------------------------------------------------

    def _agent_file(self, rel: str) -> Path:
        if not self.server_root:
            raise SatkError("NOT_READY", "the session has no server_root: cannot find the capture file")
        if not re.fullmatch(r"captures/[\w-]+\.png", rel or ""):
            raise SatkError("PROTOCOL", f"satk-agent returned an unexpected file name {rel!r}")
        return self.server_root / "mods" / "deathmatch" / "resources" / RESOURCE / rel

    def _m_capture(self, p: dict) -> dict:
        layers = list(p.get("layers") or ["color"])
        for layer in layers:
            if layer in ("ids", "depth"):
                raise SatkError("UNSUPPORTED", f"capability 'capture.{layer}' not available (MTA Lua G0)",
                                data={"capability": f"capture.{layer}"})
            if layer != "color":
                raise SatkError("UNSUPPORTED", f"layer {layer!r} is not supported", data={"layer": layer})
        prefix = p["path_prefix"]
        if not os.path.isabs(prefix):
            raise SatkError("BAD_PARAMS", "path_prefix must be an absolute path")
        from ...core import paths

        dst = paths.ensure_writable((prefix + ".png").replace("\\", "/"))
        q: dict[str, Any] = {}
        for k in ("w", "h", "env", "settle"):
            if k in p:
                q[k] = p[k]
        if p.get("pose") is not None:
            pose = G.parse_pose(p["pose"])
            q.update(pos=list(pose.pos), look=list(pose.look), roll=pose.roll)
            if pose.fov_h_deg is not None:
                q["fov_h_deg"] = pose.fov_h_deg
        if "hide" in p and "hud" not in (p.get("hide") or []):
            q["keep_hud"] = True
        r = _obj(self.rpc("capture", q))
        src = self._agent_file(str(r.get("file") or ""))
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(src, dst)
        except OSError:
            shutil.copyfile(src, dst)
            try:
                src.unlink()
            except OSError:
                pass
        data = dst.read_bytes()
        size = P.size(dst)
        w, h = int(r.get("w") or 0), int(r.get("h") or 0)
        if size is None or tuple(size) != (w, h):
            raise SatkError("PROTOCOL", f"capture file is {size} but the client reported {w}x{h}",
                            data={"file": paths.jpath(dst)})
        out: dict[str, Any] = {"files": {"color": paths.jpath(dst)}, "w": w, "h": h,
                               "pose": _pose(r.get("pose")) or {"pos": [0.0, 0.0, 0.0], "look": [0.0, 1.0, 0.0]},
                               "frame": int(r.get("frame") or 0), "settled": bool(r.get("settled")), "pending": 0,
                               "frames_waited": int(r.get("frames_waited") or 0),
                               "sha256": hashlib.sha256(data).hexdigest()}
        return out

    # -- pick --------------------------------------------------------------------------------

    def _m_pick(self, p: dict) -> dict:
        if p.get("mode") == "visible":
            raise SatkError("UNSUPPORTED", "capability 'pick.visible' not available (collision picking only)",
                            data={"capability": "pick.visible"})
        pts = [[float(a), float(b)] for a, b in p["points"]]
        if p.get("space", "window") != "capture":
            r = _obj(self.rpc("pick", {"points": pts}))
            hits = _list(r.get("hits"))
            return {"hits": [_hit(hits[i] if i < len(hits) else {}, px, py) for i, (px, py) in enumerate(pts)]}
        vw, vh = self.viewport
        w, h = int(p.get("w", vw or 1)), int(p.get("h", vh or 1))
        if p.get("pose") is not None:
            pose = G.parse_pose(p["pose"])
            fov = pose.fov_h_deg
        else:
            cam = self._m_camera_get({})
            pose = G.parse_pose(cam["pose"])
            fov = cam.get("fov_h_deg")
        if fov is None:
            fov = self._m_camera_get({}).get("fov_h_deg") or G.DEFAULT_FOV
        c = G.Camera(pose, w, h, fov)
        rays, idx = [], []
        for i, (px, py) in enumerate(pts):
            if 0 <= px < w and 0 <= py < h:
                o, d = c.ray(px, py)
                e = G.add(o, G.mul(d, RAY_LEN))
                rays.append([*o, *e])
                idx.append(i)
        hits: list[dict] = [{"px": px, "py": py, "hit": False} for px, py in pts]
        if rays:
            r = _obj(self.rpc("_rays", {"rays": rays, "preload": list(pose.pos)}))
            got = _list(r.get("hits"))
            for k, i in enumerate(idx):
                hits[i] = _hit(got[k] if k < len(got) else {}, pts[i][0], pts[i][1])
        return {"hits": hits}

    def _m_raycast(self, p: dict) -> dict:
        a, b = [float(x) for x in p["from"]], [float(x) for x in p["to"]]
        if math.dist(a, b) < 1e-3:
            raise SatkError("BAD_PARAMS", "raycast endpoints must differ")
        r = _obj(self.rpc("_rays", {"rays": [[*a, *b]], "preload": a}))
        hits = _list(r.get("hits"))
        h = _hit(hits[0] if hits else {})
        h.pop("px", None)
        h.pop("py", None)
        return h

    # -- env / log / console / lua -------------------------------------------------------------

    @staticmethod
    def _env(e: Any) -> dict:
        e = _obj(e)
        if not isinstance(e.get("time"), str):
            return {}
        out: dict[str, Any] = {"time": e["time"], "weather": int(e.get("weather") or 0)}
        if isinstance(e.get("weather_b"), (int, float)):
            out["weather_b"] = int(e["weather_b"])
        if "freeze" in e:
            out["freeze"] = bool(e["freeze"])
        return out

    def _m_env_get(self, p: dict) -> dict:
        return self._env(self.rpc("env.get"))

    def _m_env_set(self, p: dict) -> dict:
        if "blend" in p:
            self.warnings.append("env.set: blend cannot be set in MTA (weather_b blends over game time)")
        return self._env(self.rpc("env.set", {k: v for k, v in p.items() if k != "blend"}))

    def _m_log_poll(self, p: dict) -> dict:
        r = _obj(self.rpc("log.poll", p))
        items = []
        for it in _list(r.get("items")):
            it = _obj(it)
            if not isinstance(it.get("seq"), (int, float)):
                continue
            d: dict[str, Any] = {"seq": int(it["seq"]), "t": round(float(it.get("t") or 0), 3),
                                 "stream": str(it.get("stream") or "server"),
                                 "level": it.get("level") if it.get("level") in ("debug", "info", "warn", "error")
                                 else "info", "msg": str(it.get("msg") or "")}
            if it.get("file"):
                d["file"] = str(it["file"])
            if isinstance(it.get("line"), (int, float)) and it["line"] >= 0:
                d["line"] = int(it["line"])
            if it.get("resource"):
                d["resource"] = str(it["resource"])
            items.append(d)
        return {"items": items, "next_seq": int(r.get("next_seq") or 0), "dropped": int(r.get("dropped") or 0)}

    def _m_console_exec(self, p: dict) -> dict:
        r = _obj(self.rpc("console.exec", {"line": p["line"]}))
        return {"accepted": bool(r.get("accepted"))}

    def _m_lua_exec(self, p: dict) -> dict:
        side = p.get("side") or ("client" if self._client() else "server")
        q = {"code": p["code"], "side": side}
        if p.get("resource"):
            q["resource"] = p["resource"]
        to = int(p.get("timeout_ms", 5000)) / 1000.0 + 10.0
        r = _obj(self.rpc("lua.exec", q, timeout=to))
        values = []
        for v in _list(r.get("values_json")):
            try:
                values.append(json.loads(v))
            except (TypeError, ValueError):
                values.append(v)
        out: dict[str, Any] = {"values": values, "prints": [str(x) for x in _list(r.get("prints"))],
                               "side": str(r.get("side") or side)}
        err = _obj(r.get("error"))
        if err.get("msg"):
            e: dict[str, Any] = {"msg": str(err["msg"])}
            if err.get("file"):
                e["file"] = str(err["file"])
            if isinstance(err.get("line"), (int, float)):
                e["line"] = int(err["line"])
            out["error"] = e
        return out


# --------------------------------------------------------------------------- private server


def resource_source() -> Path:
    """``mta-resources/satk-agent`` of this checkout."""
    from ...core.config import REPO_ROOT

    return REPO_ROOT / "mta-resources" / RESOURCE


#: Values written over the shipped mtaserver.conf (loopback only, no LAN/ASE, quiet logs).
_SETTINGS = {
    "servername": "satk agent (loopback)",
    "serverip": "127.0.0.1",
    "maxplayers": "4",
    "httpserver": "1",
    "httpdownloadurl": "",
    "http_dos_exclude": "127.0.0.1",
    "httpdosthreshold": "10000",
    "ase": "0",
    "donotbroadcastlan": "1",
    "minclientversion_auto_update": "0",
    "crash_dump_upload": "0",
    "backup_interval": "0",
    "backup_copies": "0",
    "voice": "0",
    "check_duplicate_serials": "0",
    "auth_serial_http": "0",
    "password": "",
    "acl": "satk-acl.xml",
    "logfile": "logs/satk-agent.log",
    "authfile": "logs/satk-agent-auth.log",
    "dbfile": "logs/satk-agent-db.log",
    "loadstringfile": "",
    "scriptdebuglogfile": "logs/satk-agent-scripts.log",
    "scriptdebugloglevel": "3",
}


def server_config(template: str, port: int, httpport: int, extra: tuple[str, ...] | list[str] = ()) -> str:
    """``satk-agent.conf`` from a shipped ``mtaserver.conf``: loopback ports and only ``satk-agent``.

    ``extra`` names further resources (already in the server's ``resources`` folder) that start after
    ``satk-agent``, e.g. ``satk-testdrive`` of :mod:`satk.ingame`.
    """
    root = ET.fromstring(template)
    for el in list(root):
        if el.tag in ("resource", "module"):
            root.remove(el)
    values = dict(_SETTINGS, serverport=str(port), httpport=str(httpport))
    for key, val in values.items():
        el = root.find(key)
        if el is None:
            el = ET.SubElement(root, key)
        el.text = val
    for name in (RESOURCE, *extra):
        res = ET.SubElement(root, "resource")
        res.set("src", name)
        res.set("startup", "1")
        res.set("protected", "0")
    ET.indent(root, space="    ")
    return ("<!-- generated by satk (satk.viewer.backends.mta_lua); loopback only, resource satk-agent -->\n"
            + ET.tostring(root, encoding="unicode") + "\n")


def acl_xml() -> str:
    """A small ACL: guests may call the export; satk-agent may shut the server down."""
    return """<!-- generated by satk (satk.viewer.backends.mta_lua) -->
<acl>
    <group name="Everyone">
        <acl name="Default"/>
        <object name="user.*"/>
        <object name="resource.*"/>
    </group>
    <group name="SatkAgent">
        <acl name="SatkAgent"/>
        <object name="resource.satk-agent"/>
    </group>
    <acl name="Default">
        <right name="general.http" access="false"/>
        <right name="resource.satk-agent.http" access="true"/>
        <right name="function.shutdown" access="false"/>
    </acl>
    <acl name="SatkAgent">
        <right name="general.http" access="true"/>
        <right name="function.shutdown" access="true"/>
        <right name="function.loadstring" access="true"/>
    </acl>
</acl>
"""


def settings_xml(token: str) -> str:
    """Server ``settings.xml`` with the private ``@satk-agent.token``."""
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise SatkError("BAD_PARAMS", "the agent token must be 64 hex characters")
    return ("<settings>\n"
            f"    <setting name=\"@{RESOURCE}.token\" value=\"{token}\"/>\n"
            "</settings>\n")


def _engine_bin() -> Path:
    from ...engine.common import layout

    return layout().bin


def _copy_if_changed(src: Path, dst: Path) -> bool:
    from ...core import paths

    try:
        s, d = src.stat(), dst.stat()
        if s.st_size == d.st_size and int(s.st_mtime) == int(d.st_mtime):
            return False
    except FileNotFoundError:
        pass
    paths.ensure_writable(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)
    return True


def server_root() -> Path:
    """``<work>/mta/server``: the private server tree."""
    from ...core import paths

    return paths.work("mta", "server")


def prepare_server(port: int, httpport: int, token: str, *, root: Path | None = None,
                   extra: tuple[str, ...] | list[str] = ()) -> dict:
    """Copy the server binaries, write the configs and install the resource into ``root``.

    ``extra``: further startup resources for the config (the caller installs them).
    """
    from ...core import paths

    root = root or server_root()
    srv = _engine_bin() / "server"
    exe = srv / "MTA Server64.exe"
    if not exe.is_file():
        raise SatkError("NOT_READY", f"{paths.jpath(exe)} is not built", hint="satk engine build --project server")
    if not (srv / "x64" / "net.dll").is_file():
        raise SatkError("NOT_READY", "Bin/server/x64/net.dll is missing", hint="satk engine setup --deps")
    copied = int(_copy_if_changed(exe, root / exe.name))
    for f in sorted((srv / "x64").glob("*.dll")):
        copied += int(_copy_if_changed(f, root / "x64" / f.name))
    dm = root / "mods" / "deathmatch"
    tpl = srv / "mods" / "deathmatch" / "mtaserver.conf"
    if not tpl.is_file():
        from ...engine.common import layout

        tpl = layout().fork / "Server" / "mods" / "deathmatch" / "mtaserver.conf"
    if not tpl.is_file():
        raise SatkError("NOT_READY", "no mtaserver.conf template in the fork", hint="satk engine build --project server")
    paths.atomic_write(dm / "satk-agent.conf",
                       server_config(tpl.read_text(encoding="utf-8-sig", errors="replace"), port, httpport, extra))
    paths.atomic_write(dm / "satk-acl.xml", acl_xml())
    paths.atomic_write(dm / "settings.xml", settings_xml(token))
    src = resource_source()
    if not (src / "meta.xml").is_file():
        raise SatkError("NOT_READY", f"resource source missing: {paths.jpath(src)}")
    dst = dm / "resources" / RESOURCE
    if dst.exists():
        paths.ensure_removable(dst)
        shutil.rmtree(dst)
    paths.ensure_writable(dst)
    shutil.copytree(src, dst)
    (dst / "captures").mkdir(exist_ok=True)
    (dm / "logs").mkdir(parents=True, exist_ok=True)
    return {"root": paths.jpath(root), "exe": paths.jpath(root / exe.name), "copied": copied,
            "config": paths.jpath(dm / "satk-agent.conf")}


def _free_port(kind: int) -> int:
    s = socket.socket(socket.AF_INET, kind)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def loopback_port_free(port: int, *, udp: bool = False) -> bool:
    """True when ``port`` on 127.0.0.1 can be bound (UDP: the game port, TCP: the HTTP port)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _ready_event() -> int:
    import ctypes

    class _SA(ctypes.Structure):
        _fields_ = [("nLength", ctypes.c_ulong), ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", ctypes.c_int)]

    k32 = ctypes.windll.kernel32
    k32.CreateEventW.restype = ctypes.c_void_p
    sa = _SA(ctypes.sizeof(_SA), None, 1)
    h = k32.CreateEventW(ctypes.byref(sa), True, False, None)
    if not h:
        raise SatkError("INTERNAL", "CreateEventW failed")
    return int(h)


def _close_handle(h: int) -> None:
    import ctypes

    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(h))


def _popen(cmd: list[str], **kw) -> subprocess.Popen:
    """Popen that tries to leave the caller's job object (so the process outlives the CLI)."""
    flags = kw.pop("creationflags", 0)
    try:
        return subprocess.Popen(cmd, creationflags=flags | _BREAKAWAY, **kw)
    except OSError:
        return subprocess.Popen(cmd, creationflags=flags, **kw)


def _tail(path: Path, n: int = 12) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def _discovery() -> tuple[dict | None, dict | None, str]:
    from ...saap import client as C

    ep, sess = C.read_endpoint(ROLE), C.read_session(ROLE)
    if ep is None:
        return None, sess, "dead"
    return ep, sess, C.verify_pid(ep, sess)[0]


def status(*, probe: bool = True) -> dict:
    """``{up, pid, port, server_port, client, ...}`` of the game endpoint."""
    ep, sess, state = _discovery()
    if ep is None or state in ("dead", "mismatch"):
        out: dict[str, Any] = {"target": ROLE, "up": False}
        if ep is not None:
            out["stale"] = True
        return out
    out = {"target": ROLE, "up": True, "proto": ep.get("protocol"), "pid": ep.get("pid"), "port": ep.get("port"),
           "server_port": ep.get("server_port"), "root": ep.get("server_root"), "caps": ep.get("caps")}
    if probe and ep.get("protocol") == PROTOCOL:
        try:
            h = MtaLuaBackend.from_discovery(ROLE, ROLE).hello
            out["client"] = h.get("mta", {}).get("client")
            out["players"] = h.get("mta", {}).get("players")
            out["agent"] = h.get("impl_version")
        except SatkError as e:
            out["up"] = False
            out["error"] = {"code": e.code, "msg": e.msg}
    if sess and sess.get("client_pid"):
        from ...saap import client as C

        out["client_pid"] = sess["client_pid"]
        out["client_alive"] = C.pid_alive(int(sess["client_pid"]))
    return out


def start_server(*, timeout: float = 60.0, port: int | None = None, httpport: int | None = None,
                 extra: tuple[str, ...] | list[str] = ()) -> dict:
    """Start (or reuse) the private loopback server with ``satk-agent``; writes the discovery files.

    ``extra``: further startup resources (see :func:`server_config`); a reused server keeps its own.
    """
    from ...core import paths
    from ...saap import client as C

    ep, sess, state = _discovery()
    if ep is not None and state in ("ok", "unknown"):
        st = status()
        if st.get("up"):
            st["reused"] = True
            return st
    C.remove_discovery(ROLE)
    port = port or _free_port(socket.SOCK_DGRAM)
    httpport = httpport or _free_port(socket.SOCK_STREAM)
    token = secrets.token_hex(32)
    root = server_root()
    prep = prepare_server(port, httpport, token, root=root, extra=extra)
    logs = root / "mods" / "deathmatch" / "logs"
    out_log = paths.ensure_writable(logs / "satk-agent.stdout.log")
    exe = root / "MTA Server64.exe"
    cmd = [str(exe), "--child-process", "--config", "satk-agent.conf", "--ip", "127.0.0.1",
           "--port", str(port), "--httpport", str(httpport)]
    h = _ready_event()
    r_fd, w_fd = os.pipe()
    try:
        os.write(w_fd, struct.pack("<Q", h))
    finally:
        os.close(w_fd)
    si = subprocess.STARTUPINFO()
    si.lpAttributeList = {"handle_list": [h]}
    t0 = time.time()
    try:
        with open(out_log, "ab") as lf:
            proc = _popen(cmd, cwd=str(root), stdin=r_fd, stdout=lf, stderr=subprocess.STDOUT, startupinfo=si,
                          creationflags=_NO_WINDOW | _NEW_GROUP)
    except OSError as e:
        raise SatkError("EXTERNAL_TOOL", f"cannot start the MTA server: {e}") from None
    finally:
        os.close(r_fd)
        _close_handle(h)
    endpoint = {"role": ROLE, "protocol": PROTOCOL, "impl": IMPL, "port": httpport, "pid": proc.pid,
                "exe": paths.jpath(exe), "pid_created": C.pid_created(proc.pid), "started_at": _iso(t0),
                "caps": list(CAPS), "server_port": port, "server_root": paths.jpath(root)}
    session = {"role": ROLE, "token": token, "pid": proc.pid, "profile": "vanilla", "server_root": paths.jpath(root),
               "server_port": port, "started_at": _iso(t0)}
    b = MtaLuaBackend(ROLE, ROLE, httpport, token, pid=proc.pid, server_root=root, session=session)
    deadline = time.monotonic() + timeout
    last = ""
    while True:
        if proc.poll() is not None:
            raise SatkError("EXTERNAL_TOOL", f"the MTA server exited with code {proc.returncode} during start",
                            hint=f"see {paths.jpath(logs)}",
                            data={"stdout": _tail(out_log), "server_log": _tail(logs / "satk-agent.log")})
        try:
            b.rpc("ping", timeout=3.0)
            break
        except SatkError as e:
            last = f"{e.code}: {e.msg}"
        if time.monotonic() > deadline:
            _kill(proc.pid)
            raise SatkError("TIMEOUT", f"satk-agent did not answer within {timeout:g} s ({last})",
                            hint=f"see {paths.jpath(logs)}",
                            data={"stdout": _tail(out_log), "scripts": _tail(logs / "satk-agent-scripts.log")})
        time.sleep(0.25)
    C.write_endpoint(ROLE, endpoint)
    C.write_session(ROLE, session)
    return {"target": ROLE, "up": True, "pid": proc.pid, "port": httpport, "server_port": port,
            "ready_seconds": round(time.time() - t0, 2), "root": prep["root"], "copied": prep["copied"],
            "connect": f"mtasa://127.0.0.1:{port}"}


def _kill(pid: int) -> None:
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, creationflags=_NO_WINDOW)


def _stop_client(sess: dict) -> bool:
    """Close the client the launcher started (pid verified by image path and start time)."""
    from ...saap import client as C

    pid = sess.get("client_pid")
    if not pid:
        return False
    desc = {"pid": pid, "exe": sess.get("client_exe"), "pid_created": sess.get("client_created")}
    if C.verify_pid(desc)[0] != "ok":
        return False
    _kill(int(pid))
    return True


def stop() -> dict:
    """Ask the server to shut down (``quit``), close the client, remove the discovery files."""
    from ...saap import client as C

    ep, sess, state = _discovery()
    if ep is None or state in ("dead", "mismatch"):
        C.remove_discovery(ROLE)
        return {"target": ROLE, "up": False, "stopped": False, "note": "not running"}
    pid = int(ep["pid"])
    how = "quit"
    try:
        MtaLuaBackend.from_discovery(ROLE, ROLE).call("quit", {})
    except SatkError:
        how = "killed"
        _stop_client(sess or {})
    t_end = time.monotonic() + 20.0
    while time.monotonic() < t_end and C.pid_alive(pid):
        time.sleep(0.2)
    if C.pid_alive(pid) and C.verify_pid(ep, sess)[0] == "ok":
        _kill(pid)
        how = "killed"
        time.sleep(0.5)
    alive = C.pid_alive(pid)
    if not alive:
        C.remove_discovery(ROLE)
    return {"target": ROLE, "pid": pid, "up": alive, "stopped": not alive, "how": how}


# --------------------------------------------------------------------------- client


def _mta_major() -> str:
    from ...engine.common import layout

    try:
        text = (layout().fork / "Shared" / "sdk" / "version.h").read_text(encoding="utf-8", errors="replace")
        ma = re.search(r"#define\s+MTASA_VERSION_MAJOR\s+(\d+)", text)
        mi = re.search(r"#define\s+MTASA_VERSION_MINOR\s+(\d+)", text)
        if ma and mi:
            return f"{ma.group(1)}.{mi.group(1)}"
    except OSError:
        pass
    return "1.7"


def _running(image: str) -> list[int]:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, timeout=20, creationflags=_NO_WINDOW, errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == image.lower() and parts[1].strip('"').isdigit():
            pids.append(int(parts[1].strip('"')))
    return pids


def client_preflight() -> dict:
    """Can the fork's client start here without admin rights or dialogs? Reads only.

    The MTA loader keeps all its settings in ``HKLM\\SOFTWARE\\WOW6432Node\\Multi Theft Auto: San
    Andreas All`` (the NSIS installer creates it with an ACL that lets users write). Without that
    key a standard user gets "Error [U01]: Multi Theft Auto has not been installed properly" (the
    loader cannot store ``Last Run Location``; measured 2026-10-04); the loader is ``asInvoker``,
    so there is no registry virtualization.
    """
    from ...core import paths

    checks: list[list] = []
    blockers: list[str] = []

    def add(name: str, ok: bool, msg: str) -> None:
        checks.append([name, "ok" if ok else "blocker", msg])
        if not ok:
            blockers.append(f"{name}: {msg}")

    exe = _engine_bin() / "Multi Theft Auto.exe"
    add("client_exe", exe.is_file(), paths.jpath(exe))
    major = _mta_major()
    key = r"SOFTWARE\Multi Theft Auto: San Andreas All"
    shown = r"SOFTWARE\WOW6432Node\Multi Theft Auto: San Andreas All"  # the 32-bit view the loader uses
    gta = None
    writable = False
    why = ""
    if os.name == "nt":
        import winreg

        view = winreg.KEY_WOW64_32KEY
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key + r"\Common", 0, winreg.KEY_READ | view) as k:
                gta = str(winreg.QueryValueEx(k, "GTA:SA Path")[0])
        except OSError:
            gta = None
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_READ | winreg.KEY_WRITE | view):
                writable = True
        except FileNotFoundError:
            why = "the key does not exist (MTA was never installed or registered on this PC)"
        except PermissionError:
            why = "the key exists but a standard user cannot write it"
    else:
        why = "Windows only"
    clean = Path(paths.cfg().paths.game)
    gta_ok = bool(gta) and (Path(gta) / "gta_sa.exe").is_file()
    add("registry_gta_path", gta_ok,
        f"HKLM\\{shown}\\Common 'GTA:SA Path' = {gta!r}" if gta else
        f"HKLM\\{shown}\\Common 'GTA:SA Path' is missing: the loader would ask for the GTA folder")
    if gta_ok:
        same = os.path.normcase(os.path.abspath(gta)) == os.path.normcase(os.path.abspath(clean))
        add("registry_is_clean_copy", same, f"the client may run only against {paths.jpath(clean)}, "
                                            f"the registry names {gta}")
    add("registry_writable", writable, f"HKLM\\{shown} writable by this user" if writable else
        f"HKLM\\{shown}: {why}; the loader cannot store 'Last Run Location' and stops with 'Error [U01]: Multi "
        "Theft Auto has not been installed properly'")
    pd = Path(os.environ.get("ProgramData", "C:/ProgramData")) / "MTA San Andreas All"
    for sub in ("Common", major):
        d = pd / sub
        add(f"programdata_{sub}", d.is_dir(), paths.jpath(d) + (" exists" if d.is_dir() else
                                                                 " is missing ('[Data directory not present]')"))
    running = _running("gta_sa.exe")
    add("gta_not_running", not running, "no gta_sa.exe running" if not running else
        f"gta_sa.exe is running (pid {running}); the loader would offer to kill it")
    return {"ready": not blockers, "mta_version": major, "checks": checks, "blockers": blockers}


def _dialogs(pids: set[int]) -> list[dict]:
    """Visible top-level dialog boxes (class #32770) of ``pids`` with their texts."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    u = ctypes.WinDLL("user32", use_last_error=True)
    found: list[dict] = []
    cb_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def text(h) -> str:
        buf = ctypes.create_unicode_buffer(1024)
        u.GetWindowTextW(h, buf, 1024)
        return buf.value

    def cls(h) -> str:
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, buf, 256)
        return buf.value

    def top(h, _):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(h) and cls(h) == "#32770":
            parts: list[str] = []

            def child(c, _):
                if cls(c) == "Static" and text(c).strip():
                    parts.append(text(c).strip())
                return True

            u.EnumChildWindows(h, cb_t(child), 0)
            found.append({"pid": pid.value, "title": text(h), "text": " | ".join(parts)[:500]})
        return True

    u.EnumWindows(cb_t(top), 0)
    return found


def start_client(*, timeout: float = 180.0, force: bool = False) -> dict:
    """Start the fork's client connected to the agent server and wait until ``satk-agent`` says hello.

    Refuses (``NOT_READY``) when :func:`client_preflight` finds blockers, unless ``force``; a forced
    start that shows a dialog box is stopped and reported (title and text of the dialog).
    """
    from ...core import paths
    from ...saap import client as C

    ep, sess, state = _discovery()
    if ep is None or state not in ("ok", "unknown"):
        raise SatkError("NOT_READY", "the agent server is not running", hint="python -m satk.viewer.backends.mta_lua up")
    pre = client_preflight()
    if pre["blockers"] and not force:
        raise SatkError("NOT_READY", "the MTA client cannot start here without admin rights or dialogs: "
                        + "; ".join(pre["blockers"]), hint="docs/ru/mta-agent.md, section about the first client run",
                        data={"checks": pre["checks"]})
    exe = _engine_bin() / "Multi Theft Auto.exe"
    url = f"mtasa://127.0.0.1:{ep.get('server_port')}"
    t0 = time.time()
    proc = _popen([str(exe), url], cwd=str(exe.parent))
    sess = dict(sess or {})
    sess.update(client_pid=proc.pid, client_exe=paths.jpath(exe), client_created=C.pid_created(proc.pid),
                gta_path=paths.jpath(paths.cfg().paths.game))
    C.write_session(ROLE, sess)
    b = MtaLuaBackend.from_discovery(ROLE, ROLE)
    deadline = time.monotonic() + timeout
    while True:
        pids = {proc.pid, *_running("gta_sa.exe"), *_running("proxy_sa.exe")}
        dlg = _dialogs(pids)
        if dlg:
            _kill(proc.pid)
            for pid in pids - {proc.pid}:
                desc = C.process_info(pid) or {}
                if desc.get("created") and desc["created"] >= t0 - 1:
                    _kill(pid)
            raise SatkError("NOT_READY", f"the MTA client showed a dialog and was stopped: {dlg[0]['title']}: "
                            f"{dlg[0]['text']}", data={"dialogs": dlg, "preflight": pre["checks"]})
        if proc.poll() is not None and not _running("gta_sa.exe"):
            raise SatkError("EXTERNAL_TOOL", f"the MTA client exited with code {proc.returncode}",
                            data={"preflight": pre["checks"]})
        try:
            b._hello = None
            cl = b.hello.get("mta", {}).get("client")
            if cl:
                return {"target": ROLE, "client": cl, "client_pid": proc.pid, "url": url,
                        "ready_seconds": round(time.time() - t0, 1)}
        except SatkError:
            pass
        if time.monotonic() > deadline:
            raise SatkError("TIMEOUT", f"the MTA client did not join within {timeout:g} s", hint="look at the game window")
        time.sleep(1.0)


# --------------------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    """``python -m satk.viewer.backends.mta_lua up|down|status|client|preflight`` (JSON on stdout)."""
    import argparse

    ap = argparse.ArgumentParser(prog="python -m satk.viewer.backends.mta_lua",
                                 description="Private loopback MTA server with satk-agent and the game client.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("up", help="start the agent server (and with --client the game client)")
    up.add_argument("--client", action="store_true")
    up.add_argument("--timeout", type=float, default=60.0)
    cl = sub.add_parser("client", help="start the game client against the running agent server")
    cl.add_argument("--force", action="store_true", help="start even when the preflight finds blockers")
    cl.add_argument("--timeout", type=float, default=180.0)
    sub.add_parser("status")
    sub.add_parser("down")
    sub.add_parser("preflight")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "up":
            out = start_server(timeout=a.timeout)
            if a.client:
                out["client"] = start_client()
        elif a.cmd == "client":
            out = start_client(timeout=a.timeout, force=a.force)
        elif a.cmd == "status":
            out = status()
        elif a.cmd == "down":
            out = stop()
        else:
            out = client_preflight()
        print(json.dumps({"ok": True, **out}, ensure_ascii=False))
        return 0
    except SatkError as e:
        print(json.dumps(e.to_dict(), ensure_ascii=False))
        return e.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
