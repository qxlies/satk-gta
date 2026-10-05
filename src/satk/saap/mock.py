"""SAAP/1 mock endpoint: a small deterministic synthetic world near Grove Street (SPEC §4.8.7).

Implements every capability of SAAP/1 with stdlib only, so the protocol, the client, the
viewer operations, overlays and conformance can be tested without the game or Ariane:

* the world is a ground plane at z = 12 plus axis-aligned boxes (one per entity); entity
  ``m1`` is model 17613 ``lae2_roads89`` at (2489.3, -1668.5, 12.3) — the real position of
  ``inst:lae2_stream0#4`` — reported with ``src:{"kind":"model_pos"}`` so satk must resolve it
  through the index (SPEC WP-07 acceptance 4); other entities exercise ``ipl_bin``,
  ``ipl_text``, ``runtime`` sources, a LOD parent and an ambiguous pair;
* rendering projects each box to its screen rectangle and paints far-to-near (colour, ID buffer,
  linear depth); equal requests give byte-identical PNGs;
* streaming is simulated: after a camera move newly visible entities load 3 per frame, so
  ``settle`` matters (``settle:{max_frames:0}`` captures with holes and ``settled:false``).

Run as an endpoint: ``satk view mock --port 0`` (writes ``work/run/endpoints/mock.json`` and
``sessions/mock.json``) or in-process via :class:`MockWorld` (``satk.viewer.backends.mock``).
"""

from __future__ import annotations

import array
import ast
import hashlib
import math
import operator
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import __version__
from ..core.errors import SatkError
from . import geom as G
from . import png as P
from .protocol import CAPABILITIES, OVERLAYS

__all__ = ["MockEntity", "MockWorld", "ENTITIES", "MODELS", "WEATHERS", "DEFAULT_POSE", "serve"]

GROUND_Z = 12.0
VIEWPORT = (800, 600)
DEFAULT_POSE = {"pos": [2489.3, -1720.0, 60.0], "look": [2489.3, -1668.5, 12.3]}
DEFAULT_FOV = 70.0
DRAW_DIST = 400.0
LOADS_PER_FRAME = 3

WEATHERS = ("extrasunny_la", "sunny_la", "extrasunny_smog_la", "sunny_smog_la", "cloudy_la", "sunny_sf",
            "extrasunny_sf", "cloudy_sf", "rainy_sf", "foggy_sf", "sunny_vegas", "extrasunny_vegas",
            "cloudy_vegas", "extrasunny_countryside", "sunny_countryside", "cloudy_countryside",
            "rainy_countryside", "extrasunny_desert", "sunny_desert", "sandstorm_desert", "underwater",
            "extracolours_1", "extracolours_2")

#: model id -> (name, txd)
MODELS: dict[int, tuple[str, str]] = {
    17613: ("lae2_roads89", "lae2roads"),
    17858: ("lodlae2_roads89", "laeast2_lod"),
    17500: ("mock_grove_house_a", "mock_grove"),
    17501: ("mock_grove_house_b", "mock_grove"),
    17502: ("mock_grove_house_c", "mock_grove"),
    1280: ("mock_bench", "mock_props"),
    411: ("infernus", "infernus"),
    0: ("cj", "player"),
    300: ("cutobj01", "cutobj01"),
}


@dataclass(frozen=True)
class MockEntity:
    ref: str
    kind: str
    model_id: int
    pos: tuple[float, float, float]
    half: tuple[float, float, float]
    src: dict
    lod: bool = False
    lod_parent: str | None = None
    area: int = 0
    heading: float = 0.0  # degrees, only reported (boxes stay axis-aligned)

    @property
    def name(self) -> str:
        return MODELS.get(self.model_id, (f"model{self.model_id}", ""))[0]

    @property
    def lo(self) -> tuple[float, float, float]:
        return (self.pos[0] - self.half[0], self.pos[1] - self.half[1], self.pos[2] - self.half[2])

    @property
    def hi(self) -> tuple[float, float, float]:
        return (self.pos[0] + self.half[0], self.pos[1] + self.half[1], self.pos[2] + self.half[2])


ENTITIES: tuple[MockEntity, ...] = (
    MockEntity("m1", "building", 17613, (2489.3, -1668.5, 12.3), (12.0, 12.0, 0.6), {"kind": "model_pos"},
               lod_parent="m7"),
    MockEntity("m2", "building", 17500, (2468.0, -1650.0, 17.0), (8.0, 6.0, 5.0),
               {"kind": "ipl_bin", "ipl": "mock_stream0", "idx": 1}),
    MockEntity("m3", "building", 17501, (2511.0, -1650.0, 17.0), (8.0, 6.0, 5.0),
               {"kind": "ipl_text", "file": "data/maps/mock/mock.ipl", "idx": 3}),
    MockEntity("m4", "object", 1280, (2495.0, -1680.0, 12.8), (1.0, 0.5, 0.5), {"kind": "model_pos"}),
    MockEntity("m5", "vehicle", 411, (2480.0, -1676.0, 13.0), (1.0, 2.3, 0.65),
               {"kind": "runtime", "type": "vehicle", "id": 7}, heading=90.0),
    MockEntity("m6", "building", 17502, (2440.0, -1660.0, 16.0), (10.0, 7.0, 4.0),
               {"kind": "ipl_bin", "ipl": "mock_stream0", "idx": 2}),
    MockEntity("m7", "building", 17858, (2489.3, -1668.5, 12.3), (40.0, 40.0, 1.0),
               {"kind": "ipl_text", "file": "data/maps/la/lae2.ipl", "idx": 198}, lod=True),
    MockEntity("m8", "player", 0, (2497.0, -1671.0, 13.0), (0.4, 0.4, 1.0), {"kind": "runtime", "type": "player", "id": 1}),
    MockEntity("m9", "object", 300, (2520.0, -1690.0, 13.0), (1.5, 1.5, 1.0), {"kind": "model_pos"}),
)

_ENTITY_BY_REF = {e.ref: e for e in ENTITIES}


def _q_heading(deg: float) -> list[float]:
    a = math.radians(deg) / 2
    return [0.0, 0.0, round(math.sin(a), 4) or 0.0, round(math.cos(a), 4)]


def entity_ref(e: MockEntity) -> dict:
    """SAAP EntityRef of a mock entity."""
    return {"ref": e.ref, "kind": e.kind, "model_id": e.model_id, "model_name": e.name,
            "pos": [round(c, 4) for c in e.pos], "rot": {"q_world": _q_heading(e.heading)},
            "area": e.area, "lod": e.lod, "src": dict(e.src)}


# --------------------------------------------------------------------------- colours


def _hsv(h: float, s: float, v: float) -> tuple[int, int, int]:
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
    return int(r * 255), int(g * 255), int(b * 255)


def _model_color(model_id: int) -> tuple[int, int, int]:
    return _hsv((model_id * 0.6180339887) % 1.0, 0.45, 0.85)


def _mix(a, b, t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (int(round(a[0] + (b[0] - a[0]) * t)), int(round(a[1] + (b[1] - a[1]) * t)),
            int(round(a[2] + (b[2] - a[2]) * t)))


def _light(time_s: str) -> float:
    h, m = (int(x) for x in time_s.split(":"))
    t = h + m / 60.0
    if 7 <= t <= 19:
        return 1.0
    if t >= 21 or t <= 5:
        return 0.35
    if t < 7:
        return 0.35 + 0.65 * (t - 5) / 2
    return 1.0 - 0.65 * (t - 19) / 2


# --------------------------------------------------------------------------- mini Lua


_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Mod: operator.mod, ast.Pow: operator.pow, ast.FloorDiv: operator.floordiv}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Gt: operator.gt, ast.GtE: operator.ge}


def _lua_expr(src: str) -> Any:
    s = src.strip()
    s = re.sub(r"\bnil\b", "None", s)
    s = re.sub(r"\btrue\b", "True", s)
    s = re.sub(r"\bfalse\b", "False", s)
    s = s.replace("~=", "!=")
    tree = ast.parse(s, mode="eval")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float, str, bool, type(None))):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            return _BIN[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and type(n.ops[0]) in _CMP:
            return _CMP[type(n.ops[0])](ev(n.left), ev(n.comparators[0]))
        raise ValueError("unsupported expression")

    v = ev(tree)
    if isinstance(v, float) and v.is_integer() and "/" not in s and "." not in s:
        v = int(v)
    return v


def _split_top(s: str) -> list[str]:
    out, depth, cur, q = [], 0, "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
            continue
        if ch in "'\"":
            q = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def mini_lua(code: str) -> dict:
    """Evaluate ``print(expr)`` statements and a final ``return expr, ...`` (mock ``lua.exec``)."""
    prints: list[str] = []
    rest = code.strip()
    try:
        while rest:
            rest = rest.lstrip("; \t\r\n")
            if not rest:
                break
            if rest.startswith("print("):
                depth, i = 0, len("print")
                for i in range(len("print"), len(rest)):
                    if rest[i] == "(":
                        depth += 1
                    elif rest[i] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                inner = rest[len("print("):i]
                prints.append("\t".join(_lua_str(_lua_expr(x)) for x in _split_top(inner)))
                rest = rest[i + 1:]
                continue
            if rest.startswith("return"):
                body = rest[len("return"):].strip()
                vals = [_lua_expr(x) for x in _split_top(body)] if body else []
                return {"values": vals, "prints": prints}
            raise ValueError("the mock only runs print(...) and return ...")
        return {"values": [], "prints": prints}
    except (ValueError, SyntaxError, ZeroDivisionError, TypeError) as e:
        return {"values": [], "prints": prints, "error": {"msg": f"mock lua: {e}", "file": "chunk", "line": 1}}


def _lua_str(v: Any) -> str:
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# --------------------------------------------------------------------------- world


@dataclass
class _Frame:
    w: int
    h: int
    owner: bytearray
    color: bytearray | None
    depth: array.array | None
    drawn: list[MockEntity] = field(default_factory=list)


class MockWorld:
    """State and behaviour of the mock endpoint (no I/O except the files it is asked to write)."""

    role = "mock"
    impl = "satk-mock"

    def __init__(self, caps: list[str] | None = None, *, viewport: tuple[int, int] = VIEWPORT):
        self.caps = list(caps or CAPABILITIES)
        self.viewport = viewport
        self.t0 = time.monotonic()
        self.frame = 0
        self.cam_rev = 0
        self.scene_rev = 0
        self.pose = G.parse_pose(DEFAULT_POSE)
        self.fov = DEFAULT_FOV
        self.env = {"time": "12:00", "weather": 0, "weather_b": 0, "blend": 0.0, "freeze": False}
        self.view = {"overlays": [], "lod_mode": "normal", "draw_dist_mul": 1.0, "area": 0, "hide": [],
                     "highlight": [], "wireframe": False, "postfx": True}
        self.loaded: set[str] = set()
        self.log: list[dict] = []
        self.log_seq = 0
        self.log_max = 1000
        self._owner_cache: dict[tuple, _Frame] = {}
        self._log("console", "info", "mock endpoint started")

    # -- server hooks ------------------------------------------------------------------------

    def hello_info(self) -> dict:
        return {"role": self.role, "impl": self.impl, "impl_version": __version__,
                "build": {"id": f"satk-mock-{__version__}"}}

    def meta(self) -> dict:
        return {"frame": self.frame, "rev": {"scene": self.scene_rev, "camera": self.cam_rev}}

    def handle(self, method: str, params: dict) -> dict:
        fn = getattr(self, "_m_" + method.replace(".", "_"), None)
        if fn is None:
            raise SatkError("UNKNOWN_METHOD", f"unknown method {method!r}")
        self._tick(1)
        return fn(params or {})

    # -- helpers -----------------------------------------------------------------------------

    def _log(self, stream: str, level: str, msg: str) -> None:
        self.log_seq += 1
        self.log.append({"seq": self.log_seq, "t": round((time.monotonic() - self.t0) * 1000, 1),
                         "stream": stream, "level": level, "msg": msg})
        if len(self.log) > self.log_max:
            del self.log[: len(self.log) - self.log_max]

    def _drawable(self) -> list[MockEntity]:
        mode = self.view["lod_mode"]
        out = []
        for e in ENTITIES:
            if e.ref in self.view["hide"]:
                continue
            if mode == "lod" and not e.lod and e.lod_parent:
                continue
            if mode != "lod" and e.lod:
                continue
            if self.view["area"] not in (-1, e.area):
                continue
            out.append(e)
        return out

    def _visible(self, cam: G.Camera) -> list[MockEntity]:
        lim = DRAW_DIST * float(self.view["draw_dist_mul"])
        out = []
        for e in self._drawable():
            if G.dist(e.pos, cam.pose.pos) > lim + max(e.half):
                continue
            if cam.project_box(e.lo, e.hi) is not None:
                out.append(e)
        return out

    def _pending(self) -> list[MockEntity]:
        cam = G.Camera(self.pose, *self.viewport, self.fov)
        vis = self._visible(cam)
        return sorted((e for e in vis if e.ref not in self.loaded), key=lambda e: G.dist(e.pos, self.pose.pos))

    def _tick(self, n: int) -> None:
        for _ in range(n):
            self.frame += 1
            for e in self._pending()[:LOADS_PER_FRAME]:
                self.loaded.add(e.ref)

    def _settle(self, max_frames: int, quiet_frames: int) -> dict:
        frames = 0
        quiet = 0
        while True:
            pending = len(self._pending())
            quiet = quiet + 1 if pending == 0 else 0
            if quiet >= quiet_frames or frames >= max_frames:
                return {"settled": pending == 0 and quiet >= quiet_frames, "frames": frames, "pending": pending}
            self._tick(1)
            frames += 1

    def _camera(self, pose: dict | None, w: int, h: int, fov: float | None = None) -> G.Camera:
        if pose is None:
            return G.Camera(self.pose, w, h, fov or self.fov)
        p = G.parse_pose(pose)
        return G.Camera(p, w, h, fov or p.fov_h_deg or self.fov)

    def _pose_out(self, p: G.Pose | None = None, fov: float | None = None) -> dict:
        return G.pose_dict(p or self.pose, fov if fov is not None else self.fov)

    def _out_path(self, prefix: str, suffix: str) -> Path:
        from ..core import paths

        if not isinstance(prefix, str) or not os.path.isabs(prefix):
            raise SatkError("BAD_PARAMS", "path_prefix must be an absolute path")
        work = paths.cfg().paths.work
        p = Path(prefix + suffix)
        try:
            inside = os.path.commonpath([os.path.normcase(os.path.abspath(p)),
                                         os.path.normcase(os.path.abspath(work))]) == os.path.normcase(os.path.abspath(work))
        except ValueError:
            inside = False
        if not inside:
            raise SatkError("BAD_PARAMS", f"path_prefix must be inside the work directory {paths.jpath(work)}")
        return paths.ensure_writable(p)

    # -- rendering ---------------------------------------------------------------------------

    def _render(self, cam: G.Camera, *, color: bool, depth: bool, env: dict | None = None,
                only_loaded: bool = True) -> _Frame:
        w, h = cam.w, cam.h
        env = env or self.env
        owner = bytearray(w * h)
        img = bytearray(w * h * 3) if color else None
        dep = array.array("f", [math.inf]) * (w * h) if depth else None
        light = _light(env["time"])
        rainy = env.get("weather") in (8, 9, 16, 19)
        sky_top, sky_hor = (70, 120, 200), (190, 210, 235)
        ground_c = (96, 112, 88)
        if rainy:
            sky_top, sky_hor = (110, 115, 125), (160, 165, 170)
        sky_top, sky_hor, ground_c = (_mix((0, 0, 0), c, light) for c in (sky_top, sky_hor, ground_c))
        fog = sky_hor
        # background rows (sky / ground)
        for y in range(h):
            origin, d = cam.ray(w / 2.0 - 0.5, float(y))
            t = (GROUND_Z - origin[2]) / d[2] if d[2] < -1e-6 else -1.0
            if t > 0:
                c = _mix(ground_c, fog, min(1.0, t / (DRAW_DIST * 1.5)) * 0.7)
                if dep is not None:
                    dz = t * G.dot(d, cam.f)
                    dep[y * w:(y + 1) * w] = array.array("f", [dz]) * w
            else:
                c = _mix(sky_hor, sky_top, min(1.0, max(0.0, d[2]) * 3))
            if img is not None:
                img[y * w * 3:(y + 1) * w * 3] = bytes(c) * w
        ents = self._visible(cam)
        if only_loaded:
            ents = [e for e in ents if e.ref in self.loaded]
        ents.sort(key=lambda e: (-G.length(G.sub(e.pos, cam.pose.pos)), e.ref))
        drawn: list[MockEntity] = []
        for e in ents:
            box = cam.project_box(e.lo, e.hi)
            if box is None:
                continue
            x0, y0, x1, y1, zmin = box
            ix0, iy0 = int(math.floor(x0)), int(math.floor(y0))
            ix1, iy1 = int(math.ceil(x1)), int(math.ceil(y1))
            ix0, iy0, ix1, iy1 = max(0, ix0), max(0, iy0), min(w, ix1), min(h, iy1)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            drawn.append(e)
            k = len(drawn)
            if k > 255:
                break
            zc = max(G.NEAR, cam.to_cam(e.pos)[2])
            base = (255, 220, 0) if e.ref in self.view["highlight"] else _model_color(e.model_id)
            col = _mix(_mix((0, 0, 0), base, light), fog, min(1.0, zc / (DRAW_DIST * 1.5)) * 0.6)
            edge = _mix(col, (0, 0, 0), 0.45)
            n = ix1 - ix0
            kb = bytes([k]) * n
            for y in range(iy0, iy1):
                o = y * w
                if self.view["wireframe"] and y not in (iy0, iy1 - 1):
                    for x in (ix0, ix1 - 1):
                        owner[o + x] = k
                        if img is not None:
                            img[(o + x) * 3:(o + x) * 3 + 3] = bytes(edge)
                    continue
                owner[o + ix0:o + ix1] = kb
                if img is not None:
                    if y in (iy0, iy1 - 1):
                        img[(o + ix0) * 3:(o + ix1) * 3] = bytes(edge) * n
                    else:
                        img[(o + ix0) * 3:(o + ix1) * 3] = bytes(col) * n
                        img[(o + ix0) * 3:(o + ix0) * 3 + 3] = bytes(edge)
                        img[(o + ix1 - 1) * 3:(o + ix1) * 3] = bytes(edge)
                if dep is not None:
                    dep[o + ix0:o + ix1] = array.array("f", [zc]) * n
        return _Frame(w, h, owner, img, dep, drawn)

    def _owner_frame(self, cam: G.Camera) -> _Frame:
        key = (cam.pose, cam.w, cam.h, cam.fov, self.scene_rev, frozenset(self.loaded))
        fr = self._owner_cache.get(key)
        if fr is None:
            if len(self._owner_cache) > 8:
                self._owner_cache.clear()
            fr = self._owner_cache[key] = self._render(cam, color=False, depth=False)
        return fr

    def _hit(self, cam: G.Camera, fr: _Frame, px: float, py: float, mode: str) -> dict:
        out: dict[str, Any] = {"px": px, "py": py, "hit": False}
        ix, iy = int(math.floor(px)), int(math.floor(py))
        if not (0 <= ix < fr.w and 0 <= iy < fr.h):
            return out
        origin, d = cam.ray(px, py)  # integer coordinates address pixel centres
        k = fr.owner[iy * fr.w + ix]
        if k:
            e = fr.drawn[k - 1]
            r = G.ray_aabb(origin, d, e.lo, e.hi)
            if r is not None:
                t, normal = r
            else:
                t, normal = G.length(G.sub(e.pos, origin)), (0.0, 0.0, 1.0)
            pos = G.add(origin, G.mul(d, t))
            out.update(hit=True, pos=[round(c, 4) for c in pos], normal=list(normal), dist=round(t, 4),
                       entity=entity_ref(e))
            return out
        if d[2] < -1e-6:
            t = (GROUND_Z - origin[2]) / d[2]
            if t > 0:
                pos = G.add(origin, G.mul(d, t))
                out.update(hit=True, pos=[round(c, 4) for c in pos], normal=[0.0, 0.0, 1.0], dist=round(t, 4))
        return out

    # -- core --------------------------------------------------------------------------------

    def _m_hello(self, p: dict) -> dict:
        info = self.hello_info()
        return {"saap": 1, **info, "caps": list(self.caps), "game": {"version": "1.0us"},
                "world": {"units": "m", "up": "z"}, "limits": {"max_request": 1048576, "max_response": 8388608},
                "viewport": {"w": self.viewport[0], "h": self.viewport[1]}}

    def _m_ping(self, p: dict) -> dict:
        return {"t_ms": round((time.monotonic() - self.t0) * 1000, 3)}

    def _m_status(self, p: dict) -> dict:
        return {"frame": self.frame, "fps": 60.0, "pose": self._pose_out(), "env": dict(self.env),
                "streaming": {"pending": len(self._pending())},
                "rev": {"scene": self.scene_rev, "camera": self.cam_rev},
                "window": {"w": self.viewport[0], "h": self.viewport[1], "minimized": False}}

    def _m_quit(self, p: dict) -> dict:
        self._log("console", "info", "quit requested")
        return {}

    # -- camera ------------------------------------------------------------------------------

    def _m_camera_get(self, p: dict) -> dict:
        return {"pose": self._pose_out(), "fov_h_deg": self.fov, "near": G.NEAR, "far": 3000.0, "rev": self.cam_rev}

    def _m_camera_set(self, p: dict) -> dict:
        if "expect_rev" in p and p["expect_rev"] != self.cam_rev:
            raise SatkError("REVISION", f"camera revision is {self.cam_rev}, not {p['expect_rev']}",
                            data={"rev": self.cam_rev})
        pose = G.parse_pose(p["pose"], p.get("fov_h_deg"))
        stream = p.get("stream", "none")
        if stream != "none" and "world.settle" not in self.caps:
            raise SatkError("UNSUPPORTED", "stream needs capability 'world.settle'", data={"capability": "world.settle"})
        self.pose = pose
        if pose.fov_h_deg is not None:
            self.fov = pose.fov_h_deg
        self.cam_rev += 1
        out = {"pose": self._pose_out(), "rev": self.cam_rev}
        if stream != "none":
            out["settled"] = self._settle(120, 2)["settled"]
        return out

    def _m_camera_release(self, p: dict) -> dict:
        self.pose = G.parse_pose(DEFAULT_POSE)
        self.fov = DEFAULT_FOV
        self.cam_rev += 1
        return {}

    def _m_world_settle(self, p: dict) -> dict:
        return self._settle(int(p.get("max_frames", 120)), int(p.get("quiet_frames", 2)))

    # -- capture -----------------------------------------------------------------------------

    def _m_capture(self, p: dict) -> dict:
        layers = list(p.get("layers") or ["color"])
        for layer in layers:
            if layer not in ("color", "ids", "depth"):
                raise SatkError("UNSUPPORTED", f"layer {layer!r} is not supported", data={"layer": layer})
            if layer in ("ids", "depth") and f"capture.{layer}" not in self.caps:
                raise SatkError("UNSUPPORTED", f"capability 'capture.{layer}' not available",
                                data={"capability": f"capture.{layer}"})
        if "color" not in layers:
            layers.insert(0, "color")
        w, h = self.viewport
        if "w" in p or "h" in p:
            if "capture.size" not in self.caps:
                raise SatkError("UNSUPPORTED", "capability 'capture.size' not available", data={"capability": "capture.size"})
            w, h = int(p.get("w", w)), int(p.get("h", h))
        color_path = self._out_path(p["path_prefix"], ".png")
        saved_pose, saved_fov, saved_env = self.pose, self.fov, dict(self.env)
        try:
            if p.get("pose") is not None:
                pose = G.parse_pose(p["pose"])
                self.pose = pose
                if pose.fov_h_deg is not None:
                    self.fov = pose.fov_h_deg
                self.cam_rev += 1
            if p.get("env"):
                self.env.update(p["env"])
            settle = p.get("settle", {"max_frames": 120, "quiet_frames": 2})
            mf = int(settle.get("max_frames", 120)) if "world.settle" in self.caps else 0
            st = self._settle(mf, int(settle.get("quiet_frames", 2))) if mf > 0 else \
                {"settled": not self._pending(), "frames": 0, "pending": len(self._pending())}
            cam = G.Camera(self.pose, w, h, self.fov)
            fr = self._render(cam, color=True, depth="depth" in layers)
            data = P.encode(w, h, fr.color)
            from ..core.paths import atomic_write

            atomic_write(color_path, data)
            files = {"color": str(color_path).replace("\\", "/")}
            out: dict[str, Any] = {}
            if "ids" in layers:
                ids = bytearray(w * h * 3)
                ids[2::3] = fr.owner
                ip = self._out_path(p["path_prefix"], ".ids.png")
                atomic_write(ip, P.encode(w, h, ids))
                files["ids"] = str(ip).replace("\\", "/")
                legend = []
                for k, e in enumerate(fr.drawn, 1):
                    n = fr.owner.count(k)
                    if n:
                        legend.append({"id": k, "entity": entity_ref(e), "px": n})
                out["ids_legend"] = legend
            if "depth" in layers:
                dp = self._out_path(p["path_prefix"], ".depth.f32")
                arr = fr.depth
                if sys.byteorder != "little":  # pragma: no cover
                    arr = array.array("f", arr)
                    arr.byteswap()
                atomic_write(dp, arr.tobytes())
                files["depth"] = str(dp).replace("\\", "/")
            result_pose = self._pose_out()
            self._log("console", "info", f"capture {w}x{h} -> {files['color']}")
            return {"files": files, **out, "w": w, "h": h, "pose": result_pose, "frame": self.frame,
                    "settled": bool(st["settled"]), "pending": int(st["pending"]),
                    "frames_waited": int(st["frames"]), "sha256": hashlib.sha256(data).hexdigest()}
        finally:
            if p.get("pose") is not None:
                self.pose, self.fov = saved_pose, saved_fov
                self.cam_rev += 1
            if p.get("env"):
                self.env = saved_env

    # -- pick --------------------------------------------------------------------------------

    def _m_pick(self, p: dict) -> dict:
        mode = p.get("mode", "collision")
        if mode == "visible" and "pick.visible" not in self.caps:
            raise SatkError("UNSUPPORTED", "capability 'pick.visible' not available", data={"capability": "pick.visible"})
        if p.get("space", "window") == "capture":
            w, h = int(p.get("w", self.viewport[0])), int(p.get("h", self.viewport[1]))
            cam = self._camera(p.get("pose"), w, h)
        else:
            cam = self._camera(None, *self.viewport)
        fr = self._owner_frame(cam)
        hits = [self._hit(cam, fr, float(px), float(py), mode) for px, py in p["points"]]
        return {"hits": hits}

    def _m_raycast(self, p: dict) -> dict:
        a, b = tuple(map(float, p["from"])), tuple(map(float, p["to"]))
        seg = G.sub(b, a)
        L = G.length(seg)
        if L < 1e-3:
            raise SatkError("BAD_PARAMS", "raycast endpoints must differ")
        d = G.mul(seg, 1.0 / L)
        best = None
        for e in ENTITIES:
            if e.lod or e.ref in self.view["hide"]:
                continue
            r = G.ray_aabb(a, d, e.lo, e.hi, L)
            if r is not None and (best is None or r[0] < best[0]):
                best = (r[0], r[1], e)
        if d[2] < -1e-6:
            t = (GROUND_Z - a[2]) / d[2]
            if 0 < t <= L and (best is None or t < best[0]):
                best = (t, (0.0, 0.0, 1.0), None)
        if best is None:
            return {"hit": False}
        t, normal, e = best
        out = {"hit": True, "pos": [round(c, 4) for c in G.add(a, G.mul(d, t))], "normal": list(normal), "dist": round(t, 4)}
        if e is not None:
            out["entity"] = entity_ref(e)
        return out

    # -- entities ----------------------------------------------------------------------------

    def _m_entity_query(self, p: dict) -> dict:
        cx, cy = float(p["center"][0]), float(p["center"][1])
        r = float(p["r"])
        kinds = set(p.get("kinds") or [])
        model = p.get("model")
        rows = []
        for e in ENTITIES:
            if e.lod and not p.get("include_lod"):
                continue
            if kinds and e.kind not in kinds:
                continue
            if model is not None and e.model_id != model:
                continue
            dd = math.hypot(e.pos[0] - cx, e.pos[1] - cy)
            if dd <= r:
                rows.append((round(dd, 6), e.ref, e))
        rows.sort(key=lambda t: (t[0], t[1]))
        try:
            off = int(p.get("cursor") or 0)
        except ValueError:
            raise SatkError("BAD_PARAMS", "bad cursor") from None
        lim = int(p.get("limit", 50))
        page = rows[off:off + lim]
        out: dict[str, Any] = {"items": [entity_ref(e) for _, _, e in page], "total": len(rows)}
        if off + lim < len(rows):
            out["next"] = str(off + lim)
        return out

    def _m_entity_inspect(self, p: dict) -> dict:
        e = _ENTITY_BY_REF.get(p["ref"])
        if e is None:
            raise SatkError("NOT_FOUND", f"no entity {p['ref']!r}")
        name, txd = MODELS.get(e.model_id, (e.name, ""))
        lod: dict[str, Any] = {"children": [entity_ref(c) for c in ENTITIES if c.lod_parent == e.ref]}
        if e.lod_parent:
            lod["parent"] = entity_ref(_ENTITY_BY_REF[e.lod_parent])
        return {"entity": entity_ref(e), "model": {"id": e.model_id, "name": name, "txd": txd, "flags": 0, "draw": 300.0},
                "lod": lod, "runtime": {"loaded": e.ref in self.loaded, "aabb": [*e.lo, *e.hi]}}

    # -- env / view --------------------------------------------------------------------------

    def _m_env_get(self, p: dict) -> dict:
        return {**self.env, "weathers": [{"id": i, "name": n} for i, n in enumerate(WEATHERS)]}

    def _m_env_set(self, p: dict) -> dict:
        for k in ("weather", "weather_b"):
            if k in p and not 0 <= int(p[k]) < len(WEATHERS):
                raise SatkError("BAD_PARAMS", f"{k} must be 0..{len(WEATHERS) - 1}")
        if "weather" in p and "weather_b" not in p:
            self.env["weather_b"] = int(p["weather"])
        self.env.update({k: v for k, v in p.items() if k in ("time", "weather", "weather_b", "blend", "freeze")})
        self.scene_rev += 1
        return dict(self.env)

    def _m_view_set(self, p: dict) -> dict:
        if "overlays" in p:
            bad = [o for o in p["overlays"] if o not in OVERLAYS]
            if bad:
                raise SatkError("UNSUPPORTED", f"overlay {bad[0]!r} is not supported", data={"overlay": bad[0]})
        for k in ("hide", "highlight"):
            missing = [r for r in p.get(k) or [] if r not in _ENTITY_BY_REF]
            if missing:
                raise SatkError("NOT_FOUND", f"no entity {missing[0]!r}")
        for k in ("overlays", "lod_mode", "draw_dist_mul", "area", "hide", "highlight", "wireframe", "postfx"):
            if k in p:
                self.view[k] = list(p[k]) if isinstance(p[k], list) else p[k]
        self.scene_rev += 1
        return {"applied": {k: (list(v) if isinstance(v, list) else v) for k, v in self.view.items()}}

    def _m_asset_render(self, p: dict) -> dict:
        m = p["model"]
        mid = None
        if isinstance(m, int):
            mid = m if m in MODELS else None
        else:
            s = str(m).lower()
            mid = next((k for k, (n, _) in MODELS.items() if n == s or str(k) == s), None)
        if mid is None:
            raise SatkError("NOT_FOUND", f"unknown model {m!r}")
        half = next((e.half for e in ENTITIES if e.model_id == mid), (1.0, 1.0, 1.0))
        size = int(p.get("size", 256))
        el = float(p.get("el", 25))
        bg = p.get("bg", "transparent")
        bgc = (0, 0, 0, 0) if bg == "transparent" else (int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16), 255)
        files = []
        radius = math.sqrt(sum(c * c for c in half))
        for az in p.get("views") or [45, 135, 225, 315]:
            pose = G.frame_sphere((0.0, 0.0, 0.0), radius, yaw=float(az) + 180.0, elevation=el)
            cam = G.Camera.from_pose(pose, size, size, 40.0)
            img = bytearray(bytes(bgc) * (size * size))
            box = cam.project_box(tuple(-c for c in half), half)
            if box is not None:
                x0, y0, x1, y1, _ = (int(round(v)) for v in box)
                col = (*_model_color(mid), 255)
                edge = (*_mix(_model_color(mid), (0, 0, 0), 0.5), 255)
                for y in range(max(0, y0), min(size, y1)):
                    rowc = edge if y in (y0, y1 - 1) else col
                    img[(y * size + max(0, x0)) * 4:(y * size + min(size, x1)) * 4] = bytes(rowc) * (min(size, x1) - max(0, x0))
            path = self._out_path(p["path_prefix"], f"_{int(az) if float(az).is_integer() else az}.png")
            from ..core.paths import atomic_write

            atomic_write(path, P.encode(size, size, img, 4))
            files.append(str(path).replace("\\", "/"))
        return {"files": files, "stats": {"tris": 12, "tex_missing": 0}}

    # -- dev ---------------------------------------------------------------------------------

    def _m_log_poll(self, p: dict) -> dict:
        since = int(p.get("since", 0))
        mx = int(p.get("max", 100))
        streams = set(p.get("streams") or ["console", "script", "server", "debug"])
        levels = ["debug", "info", "warn", "error"]
        minl = levels.index(p.get("min_level", "debug"))
        first = self.log[0]["seq"] if self.log else self.log_seq + 1
        dropped = max(0, first - 1 - since)
        items, last = [], since
        for it in self.log:
            if it["seq"] <= since:
                continue
            if len(items) >= mx:
                break
            last = it["seq"]
            if it["stream"] in streams and levels.index(it["level"]) >= minl:
                items.append(dict(it))
        return {"items": items, "next_seq": max(last, since), "dropped": dropped}

    def _m_console_exec(self, p: dict) -> dict:
        line = p["line"].strip()
        self._log("console", "info", f"> {line}")
        cmd, _, arg = line.partition(" ")
        if cmd == "help":
            out = ["mock console: help, echo <text>, time, set_time HH:MM"]
        elif cmd == "echo":
            out = [arg]
        elif cmd == "time":
            out = [self.env["time"]]
        elif cmd == "set_time" and re.match(r"^([01][0-9]|2[0-3]):[0-5][0-9]$", arg.strip()):
            self.env["time"] = arg.strip()
            self.scene_rev += 1
            out = [f"time set to {arg.strip()}"]
        else:
            return {"accepted": False, "output": [f"unknown command: {cmd}"]}
        return {"accepted": True, "output": out}

    def _m_lua_exec(self, p: dict) -> dict:
        if p.get("side", "client") not in ("client", "server"):
            raise SatkError("BAD_PARAMS", "side must be client or server")
        return mini_lua(p["code"])

    def _m_mem_read(self, p: dict) -> dict:
        a = p["addr"]
        addr = int(a, 16) if isinstance(a, str) else int(a)
        n = int(p["len"])
        if addr < 0x400000 or addr + n > 0xC00000:
            raise SatkError("NOT_FOUND", f"address 0x{addr:x} is not mapped in the mock")
        hdr = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"
        out = bytearray()
        for i in range(addr, addr + n):
            off = i - 0x400000
            out.append(hdr[off] if off < len(hdr) else ((i * 2654435761) >> 13) & 0xFF)
        return {"hex": out.hex()}


# --------------------------------------------------------------------------- endpoint


def serve(port: int = 0, *, token: str | None = None, caps: list[str] | None = None, write_files: bool = True,
          on_ready=None) -> dict:
    """Run the mock endpoint until ``quit``/Ctrl+C. Writes the discovery files (§4.8.4).

    The token comes from ``token``, else ``SATK_AGENT_TOKEN``, else a fresh one (the session
    file then carries it). ``SATK_AGENT_INSECURE=1`` accepts any token.
    """
    import datetime as _dt
    import json
    import secrets

    from ..core import paths
    from .server import SaapServer

    tok = token or os.environ.get("SATK_AGENT_TOKEN") or secrets.token_hex(32)
    insecure = os.environ.get("SATK_AGENT_INSECURE") == "1"
    world = MockWorld(caps)
    srv = SaapServer(world, token=tok, role="mock", port=port, insecure=insecure)
    sess_path = None
    if write_files:
        srv.write_descriptor(paths.work("run", "endpoints"))
        sess_path = paths.work("run", "sessions") / "mock.json"
        from .client import pid_created

        sess = {"role": "mock", "pid": os.getpid(), "token": tok, "log": None,
                "launched_by": os.environ.get("SATK_LAUNCHED_BY", "satk view mock"), "args": ["--port", str(port)],
                "started_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "port": srv.port,
                "pid_created": pid_created(os.getpid())}
        paths.atomic_write(sess_path, json.dumps(sess, indent=1))
    srv.start()
    if on_ready is not None:
        on_ready(srv)
    t0 = time.monotonic()
    try:
        srv.serve_forever()
    finally:
        if sess_path is not None:
            try:
                d = json.loads(Path(sess_path).read_text(encoding="utf-8"))
                if d.get("pid") == os.getpid():
                    Path(sess_path).unlink()
            except (OSError, ValueError):
                pass
    return {"role": "mock", "port": srv.port, "requests": srv.requests, "seconds": round(time.monotonic() - t0, 1)}
