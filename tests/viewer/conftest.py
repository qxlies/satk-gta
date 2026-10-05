"""Fixtures for satk.viewer tests (owner WP-07).

* ``fake_ariane`` — a tiny ``ARIANE_IPC/1`` server speaking the fork's bridge format (auth prefix,
  ``ARIANE_IPC/1`` lines, JSON replies, one request per connection) with canned answers, so the
  legacy adapter is tested without the real viewer;
* the in-process mock world is reset before every test.
"""

from __future__ import annotations

import json
import math
import secrets
import socket
import struct
import threading
from pathlib import Path

import pytest

from satk.saap import png as P


@pytest.fixture(autouse=True)
def _fresh_mock_world():
    from satk.viewer.backends.mock import reset_world

    reset_world()
    yield
    reset_world()


INSTANCES = [
    {"instance_id": 101, "object_key": "runtime:101", "model_id": 17613, "name": "lae2_roads89",
     "position": [2489.3, -1668.5, 12.3], "rotation": [0, 0, 0], "q_file": [0.0, 0.0, 0.0, 1.0], "ipl": "lae2_stream0",
     "ipl_kind": "binary", "ipl_index": 4, "area": 0, "is_lod": False, "lod_instance_id": 202,
     "lod": {"instance_id": 202, "model_id": 17858, "ipl": "lae2", "ipl_kind": "text", "ipl_index": 198}},
    {"instance_id": 202, "object_key": "runtime:202", "model_id": 17858, "name": "lodlae2_roads89",
     "position": [2489.3, -1668.5, 12.3], "rotation": [0, 0, 0], "q_file": [0.0, 0.0, 0.7071, 0.7071], "ipl": "lae2",
     "ipl_kind": "text", "ipl_index": 198, "ipl_file": "DATA\\MAPS\\LA\\LAE2.IPL", "area": 0, "is_lod": True,
     "lod_instance_id": -1},
    {"instance_id": 303, "object_key": "runtime:303", "model_id": 1280, "name": "bench", "position": [2495.0, -1680.0, 12.8],
     "rotation": [0, 0, 0], "ipl": None, "ipl_kind": "none", "ipl_index": -1, "area": 0, "is_lod": False,
     "lod_instance_id": -1},
]


class FakeAriane:
    """Canned ``ARIANE_IPC/1`` endpoint (see module docstring)."""

    def __init__(self, token: str, *, window=(1280, 720), settle: bool = True, quit_cmd: bool = True,
                 raycast: bool = True):
        self.token = token
        self.window = window
        self.settle = settle
        self.quit_cmd = quit_cmd
        self.raycast = raycast
        self.cam = {"position": [0.0, 0.0, 100.0], "target": [0.0, 50.0, 0.0], "fov": 70.0}
        self.cam_rev = 0
        self.scene_rev = 0
        self.env = [12, 0, 0, 0, 0.0]
        self.log: list[list[str]] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.stopped = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    # transport ------------------------------------------------------------------------------
    def _loop(self):
        self.sock.settimeout(0.2)
        while not self.stopped.is_set():
            try:
                c, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with c:
                c.settimeout(5)
                try:
                    hdr = self._recv(c, 4)
                    (n,) = struct.unpack("!I", hdr)
                    text = self._recv(c, n).decode("utf-8")
                except OSError:
                    continue
                prefix = f"ARIANE_AUTH/1 {self.token}\n"
                if not text.startswith(prefix):
                    body = {"protocol_version": 1, "request_id": "unknown", "ok": False, "error": "authentication failed"}
                else:
                    lines = text[len(prefix):].split("\n")
                    assert lines[0] == "ARIANE_IPC/1"
                    rid, cmd, fields = lines[1], lines[2], lines[3:]
                    self.log.append([cmd, *fields])
                    try:
                        body = {"ok": True, **self.handle(cmd, fields)}
                    except ValueError as e:
                        body = {"ok": False, "error": str(e)}
                    body = {"protocol_version": 1, "scene_revision": self.scene_rev, "request_id": rid, **body}
                data = json.dumps(body).encode("utf-8")
                c.sendall(struct.pack("!I", len(data)) + data)

    @staticmethod
    def _recv(c, n):
        buf = b""
        while len(buf) < n:
            chunk = c.recv(n - len(buf))
            if not chunk:
                raise OSError("closed")
            buf += chunk
        return buf

    def stop(self):
        self.stopped.set()
        self.sock.close()

    # commands -------------------------------------------------------------------------------
    def commands(self):
        cmds = ["camera_context", "camera", "capture", "capture_pose", "screen_to_world",
                "inspect_zone_page", "asset_detail", "asset_preview", "environment", "satk_stats"]
        if self.raycast:
            cmds.append("raycast_segment")
        if self.settle:
            cmds.append("settle")
        if self.quit_cmd:
            cmds.append("quit")
        return cmds

    def handle(self, cmd, f):
        W, H = self.window
        if cmd == "ping":
            return {"result": "pong"}
        if cmd == "capabilities":
            return {"engine": "ariane", "build_id": "ariane-fnv1a64-test", "commands": self.commands(),
                    "satk": {"fork": "ariane-satk", "patches": ["V0-1", "V0-6", "V0-8"], "game_dir": "D:\\G\\clean",
                             "window": [W, H]}}
        if cmd == "camera_context":
            # basis exactly like agentbridge.cpp (m_up = +Z)
            fwd = _norm([t - p for t, p in zip(self.cam["target"], self.cam["position"])])
            right = _norm(_cross(fwd, [0.0, 0.0, 1.0]))
            up = _norm(_cross(right, fwd))
            return {"camera": {**self.cam, "forward": fwd, "right": right, "up": up, "viewport": [W, H],
                               "aspect_ratio": W / H, "near_plane": 0.1, "far_plane": 2000.0,
                               "camera_revision": self.cam_rev}}
        if cmd == "camera":
            v = [float(x) for x in f[:7]]
            self.cam = {"position": v[0:3], "target": v[3:6], "fov": v[6]}
            self.cam_rev += 1
            return {"result": "camera_set", "camera_revision": self.cam_rev}
        if cmd in ("capture", "capture_pose"):
            path = f[0]
            settled = True
            if cmd == "capture_pose":
                v = [float(x) for x in f[2:12]]
                self.cam = {"position": v[0:3], "target": v[3:6], "fov": v[9]}
                self.cam_rev += 1
                sf = f[13:]
            else:
                sf = f[2:]
            if self.settle and sf and sf[0] == "0":
                settled = False
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(P.encode(W // 40, H // 40, bytes([90, 120, 160]) * ((W // 40) * (H // 40))))
            r = {"path": path, "actual_pose": {**self.cam, "up": [0, 0, 1]}, "camera_revision": self.cam_rev,
                 "restored": False, "msaa": 1}
            if self.settle:
                r.update(settled=settled, frames_waited=3, pending_models=0)
            return r
        if cmd == "settle":
            return {"settled": True, "frames_waited": 5, "pending_models": 0}
        if cmd == "screen_to_world":
            px, py, w, h = float(f[0]), float(f[1]), int(f[2]), int(f[3])
            if (w, h) != (W, H):
                raise ValueError("invalid or stale screen_to_world viewport or pixel")
            if abs(px - W / 2) < 50 and abs(py - H / 2) < 50:
                # like the real bridge: the render-list search reports the LOD that shares the hit point
                return {"hit": True, "source": "geometry", "point": [2489.0, -1669.0, 12.9], "depth": 70.0,
                        "camera_revision": self.cam_rev, **INSTANCES[1]}
            return {"hit": True, "source": "ground_fallback", "point": [2400.0, -1600.0, 13.0], "normal": [0, 0, 1],
                    "depth": 150.0, "camera_revision": self.cam_rev}
        if cmd == "raycast_segment" and self.raycast:
            s, t = [float(x) for x in f[0:3]], [float(x) for x in f[3:6]]
            d = math.dist(s, t)
            if t[2] >= s[2]:  # looking up: sky
                return {"ray": {"visible": True, "distance": d, "hit": None}}
            hit = {k: v for k, v in INSTANCES[0].items() if k not in ("position", "rotation", "q_file", "object_key")}
            return {"ray": {"visible": False, "distance": d, "hit": {"point": [2489.0, -1669.0, 12.9],
                                                                     "distance": 42.0, "fraction": 42.0 / d, **hit}}}
        if cmd == "inspect_zone_page":
            x, y, r, off, lim = float(f[0]), float(f[1]), float(f[2]), int(f[3]), int(f[4])
            lods = len(f) > 5 and f[5] == "1"
            items = [i for i in INSTANCES if (lods or not i["is_lod"])
                     and math.hypot(i["position"][0] - x, i["position"][1] - y) <= r]
            page = items[off:off + lim]
            nxt = off + lim if off + lim < len(items) else None
            return {"items": page, "page": {"offset": off, "limit": lim, "returned": len(page), "total": len(items),
                                             "next_offset": nxt}}
        if cmd == "asset_detail":
            mid = int(f[0])
            name = {17613: "lae2_roads89", 17858: "lodlae2_roads89", 1280: "bench"}.get(mid)
            if not name:
                raise ValueError("unknown model")
            return {"asset": {"id": mid, "name": name, "draw_distance": 150.0}}
        if cmd == "asset_preview":
            if f[0] not in ("17613", "lae2_roads89"):
                raise ValueError(f"unknown model: {f[0]}")
            size = int(f[3])
            Path(f[1]).write_bytes(P.encode(size, size, bytes(size * size * 3)))
            return {"path": f[1], "model_id": 17613, "angle_radians": float(f[2])}
        if cmd == "environment":
            if len(f) == 5:
                self.env = [int(f[0]), int(f[1]), int(f[2]), int(f[3]), float(f[4])]
                self.scene_rev += 1
            h, m, a, b, bl = self.env
            return {"hour": h, "minute": m, "weather_a": a, "weather_b": b, "blend": bl,
                    "weathers": [{"id": i, "name": f"w{i}"} for i in range(23)]}
        if cmd == "satk_stats":
            return {"frames": 1234, "fps": 144.0, "pending_models": 0}
        if cmd == "quit" and self.quit_cmd:
            self.stopped.set()
            return {"result": "quitting"}
        raise ValueError("unknown command")


def _norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return [c / n for c in v]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


@pytest.fixture
def make_fake_ariane(satk_home):
    made: list[FakeAriane] = []

    def make(**kw) -> FakeAriane:
        srv = FakeAriane(secrets.token_hex(32), **kw)
        made.append(srv)
        return srv

    yield make
    for srv in made:
        srv.stop()


@pytest.fixture
def fake_ariane(make_fake_ariane):
    return make_fake_ariane()


@pytest.fixture
def ariane_backend(fake_ariane):
    from satk.viewer.backends.ariane_legacy import ArianeLegacyBackend

    return ArianeLegacyBackend("ariane", "ariane", fake_ariane.port, fake_ariane.token, timeout=5)
