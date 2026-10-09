"""The bench scenes S1-S9 of ``satk ingame bench`` as plain data (sent to the ``satk-bench`` resource one stage at a time).

A scene is a title, a purpose and a list of **stages**. A stage is what the client Lua runs between two result
rows: an environment (frozen time and weather, or a time-lapse), a camera (``fixed`` pose or a ``path`` of waypoint
dicts ``{u, x, y, z}`` with ``u`` = progress 0..1 of the sample, so ``--duration`` rescales it), client-side
vehicles and peds, ``warmup`` and ``sample`` seconds, optional ``console`` lines satk executes before the stage
(``sae_set fps_limit 144``), a ``server_fps`` (``setFPSLimit`` on the server before the stage, put back after the scene) and an optional ``probe`` (``weapon`` or ``loader``). Positions reuse
:mod:`satk.ingame.spots`; the six R1 viewpoints are the ones of the design document (V1-V6). Heading convention
of GTA: 0 = north, 90 = west, 270 = east. Stdlib only.
"""

from __future__ import annotations

import copy
import difflib
import math
from typing import Any

from . import spots as SP

__all__ = ["SCENES", "VIEWPOINTS", "YELLOW_MIB", "scene_ids", "scene", "resolve", "total_seconds", "rows", "make_path"]

#: Used-VA yellow line (3.0 GiB) of the VA guard; the loader scene stops there, bench-compare enforces it.
YELLOW_MIB = 3072.0
WARMUP_S = 5.0
SAMPLE_S = 20.0
_PED_SKIN = 7
_CAR_MODEL = 426  # premier: 4.9 x 1.9 m, the car of the pad checks


def make_path(xy: list[tuple[float, float]], z: float) -> list[dict[str, float]]:
    """Waypoints ``{u, x, y, z}`` through ``xy`` at height ``z``; ``u`` = share of the horizontal path length."""
    if len(xy) < 2:
        raise ValueError("a path needs two points")
    seg = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(xy, xy[1:])]
    total = sum(seg) or 1.0
    out, acc = [], 0.0
    for i, (x, y) in enumerate(xy):
        out.append({"u": round(acc / total, 5), "x": round(float(x), 2), "y": round(float(y), 2), "z": float(z)})
        if i < len(seg):
            acc += seg[i]
    return out


def _spot(name: str) -> list[float]:
    return list(SP.SPOTS[name]["pos"])


#: R1 viewpoints (design 4.6): id -> (camera, look at, weather, what it checks).
VIEWPOINTS: dict[str, dict[str, Any]] = {
    "v1": {"pos": [-2324.0, -1636.0, 500.0], "look": [1500.0, -1500.0, 40.0], "weather": 1,
           "checks": "Mount Chiliad summit to LS: LOD reach, far clip cap, fog edge"},
    "v2": {"pos": [1550.0, -1300.0, 250.0], "look": [1100.0, -1800.0, 20.0], "weather": 1,
           "checks": "above LS downtown: HD wedge (Dnorm), visible list high-water"},
    "v3": {"pos": [2040.0, 1100.0, 60.0], "look": [2040.0, 2400.0, 20.0], "weather": 1,
           "checks": "LV strip: dense HD and neon, alpha lists"},
    "v4": {"pos": [-2680.0, 1550.0, 80.0], "look": [-1900.0, 650.0, 40.0], "weather": 1,
           "checks": "Gant bridge to SF downtown: water, LOD fade"},
    "v5": {"pos": [-300.0, 2200.0, 120.0], "look": [2000.0, 1500.0, 20.0], "weather": 1,
           "checks": "desert to LV: countryside far clip"},
    "v6": {"pos": [-2680.0, 1550.0, 80.0], "look": [-1900.0, 650.0, 40.0], "weather": 9,
           "checks": "V4 in foggy weather: fog preserved, effective far clip <= 1.1 x vanilla"},
}


def _env(time: str = "12:00", weather: int = 1, **extra: Any) -> dict[str, Any]:
    return {"time": time, "weather": weather, "freeze": True, **extra}


def _fixed(pos: list[float], look: list[float], fov: float = 70.0, **extra: Any) -> dict[str, Any]:
    return {"kind": "fixed", "pos": [round(v, 2) for v in pos], "look": [round(v, 2) for v in look], "fov": fov, **extra}


def _grid(n_along: int, n_side: int, along: float, side: float, origin: list[float], heading: float) -> list[tuple]:
    """Points of an ``n_along`` x ``n_side`` grid centred on ``origin`` along the heading (east for 270)."""
    rad = math.radians(heading)
    fx, fy = -math.sin(rad), math.cos(rad)          # forward unit vector of the GTA heading
    rx, ry = fy, -fx                                # to the right
    pts = []
    for i in range(n_along):
        for j in range(n_side):
            a = (i - (n_along - 1) / 2.0) * along
            s = (j - (n_side - 1) / 2.0) * side
            pts.append((round(origin[0] + fx * a + rx * s, 2), round(origin[1] + fy * a + ry * s, 2)))
    return pts


def _s1() -> dict[str, Any]:
    gx, gy, gz = _spot("grove")
    peds = [{"model": _PED_SKIN, "x": round(gx + dx, 2), "y": round(gy + dy, 2), "z": gz, "h": h}
            for dx, dy, h in ((3.0, -2.0, 90.0), (3.5, 0.5, 270.0), (5.0, 2.0, 180.0), (6.5, -1.0, 0.0), (7.5, 1.5, 90.0))]
    stages = []
    for fps in (60, 144, 165, 240):
        stages.append({
            "id": f"fps{fps}", "title": f"Grove Street, fps_limit {fps}", "env": _env(),
            "camera": _fixed([gx - 6.0, gy - 4.0, gz + 3.0], [gx + 5.0, gy, gz + 0.8], follow_drop=2.0),
            "peds": peds, "fps_limit": fps, "server_fps": fps})
    return {"title": "Grove Street baseline", "purpose": "baseline frame time, clock drift (gameTimeMs vs getTickCount) and "
            "frameCounter rate at fps_limit 60/144/165/240 (server setFPSLimit per step; launch the client with "
            "--cvar fps_limit=0 vsync=0); repeat per timer mode with --cvar",
            "stages": stages}


#: LS flyover (S2): beach, Market, downtown (V2 camera xy), Grove, the airport, Vinewood, Mulholland.
_S2_XY = [tuple(_spot("beach")[:2]), (1000.0, -1750.0), (1550.0, -1300.0), tuple(_spot("grove")[:2]),
          tuple(_spot("airport")[:2]), tuple(_spot("vinewood")[:2]), tuple(_spot("mulholland")[:2])]
#: SF -> LV (S3): Doherty, the SF hills, the desert (V5 camera xy), Bone County, the strip.
_S3_XY = [tuple(_spot("sf")[:2]), (-1000.0, 450.0), (-300.0, 2200.0), (800.0, 1900.0), tuple(_spot("lv")[:2])]


def _s2() -> dict[str, Any]:
    return {"title": "LS flyover", "purpose": "streaming, draw-distance cost and VA growth over dense Los Santos; "
            "180 s by default, --duration 300 for R0", "stages": [{
                "id": "flyover", "title": "LS flyover at 120 m", "env": _env(),
                "camera": {"kind": "path", "points": make_path(_S2_XY, 120.0), "agl": 120.0, "ahead": 0.02, "drop": 35.0,
                           "fov": 70.0},
                "warmup": WARMUP_S, "sample": 180.0}]}


def _s3() -> dict[str, Any]:
    return {"title": "SF to LV flight", "purpose": "cold/warm streaming over the whole map (IMG buffering on/off)",
            "stages": [{"id": "sf-lv", "title": "SF to LV at 60 m", "env": _env(),
                        "camera": {"kind": "path", "points": make_path(_S3_XY, 60.0), "agl": 60.0, "ahead": 0.02,
                                   "drop": 20.0, "fov": 70.0},
                        "warmup": WARMUP_S, "sample": 300.0}]}


def _s4() -> dict[str, Any]:
    v = VIEWPOINTS["v2"]
    return {"title": "Twilight time-lapse", "purpose": "day/night prelight spikes: 18:00 to 19:30 over LS in one sample",
            "stages": [{"id": "twilight", "title": "LS 18:00-19:30", "env": _env("18:00", 1, timelapse={"from": "18:00", "to": "19:30"}),
                        "camera": _fixed(v["pos"], v["look"]), "warmup": WARMUP_S, "sample": 90.0}]}


_PAD = _spot("pad")


def _pad_camera(high: float) -> dict[str, Any]:
    return _fixed([_PAD[0] - 18.0, _PAD[1] + 22.0, _PAD[2] + high], [_PAD[0], _PAD[1], _PAD[2]], follow_drop=3.0)


def _s5() -> dict[str, Any]:
    pts = _grid(8, 8, 4.8, 2.0, _PAD, 270.0)
    cars = [{"model": _CAR_MODEL, "x": x, "y": y, "z": round(_PAD[2], 2), "h": 270.0} for x, y in pts]
    return {"title": "64 touching vehicles", "purpose": "vehicle-vehicle collision cost on the LS airport pad",
            "stages": [{"id": "cars64", "title": "64 vehicles, bumper to bumper", "env": _env(), "camera": _pad_camera(14.0),
                        "vehicles": cars, "warmup": WARMUP_S, "sample": SAMPLE_S}]}


def _s6() -> dict[str, Any]:
    pts = _grid(11, 10, 1.6, 1.6, _PAD, 270.0)
    peds = [{"model": _PED_SKIN, "x": x, "y": y, "z": round(_PAD[2], 2), "h": float((i * 37) % 360)} for i, (x, y) in enumerate(pts)]
    return {"title": "110 walking peds", "purpose": "ped simulation cost on the LS airport pad",
            "stages": [{"id": "peds110", "title": "110 peds walking", "env": _env(), "camera": _pad_camera(16.0),
                        "peds": peds, "walk": True, "center": [round(_PAD[0], 2), round(_PAD[1], 2), _PAD[2]],
                        "radius": 30.0, "turn_ms": 3000, "seed": 7, "warmup": WARMUP_S, "sample": SAMPLE_S}]}


def _s7() -> dict[str, Any]:
    stages = []
    for vid, v in VIEWPOINTS.items():
        stages.append({"id": vid, "title": f"{vid.upper()}: {v['checks']}", "env": _env("12:00", v["weather"]),
                       "camera": _fixed(v["pos"], v["look"], follow_drop=5.0), "warmup": WARMUP_S, "sample": 10.0})
    return {"title": "Draw-distance viewpoints", "purpose": "visible counts and high-water marks at the six R1 viewpoints "
            "(design 4.6); take frames with 'ingame shot' per viewpoint", "stages": stages}


def _s8() -> dict[str, Any]:
    p = _PAD
    return {"title": "Weapon probe", "purpose": "a client-side ped fires an M4 for the sample: shots per real second and per "
            "game second show the clock bias of gameplay timers",
            "stages": [{"id": "m4", "title": "M4 for 20 s", "env": _env(),
                        "camera": _fixed([p[0] - 4.0, p[1] + 5.0, p[2] + 2.5], [p[0] + 3.0, p[1], p[2] + 1.0], follow_drop=3.0),
                        "probe": {"kind": "weapon", "weapon": 31, "slot": 5, "ammo": 99999, "skin": _PED_SKIN,
                                  "pos": [round(p[0], 2), round(p[1], 2), round(p[2], 2)], "h": 270.0,
                                  "target": [round(p[0] + 60.0, 2), round(p[1], 2), round(p[2] + 1.2, 2)]},
                        "warmup": 3.0, "sample": SAMPLE_S}]}


def _s9() -> dict[str, Any]:
    return {"title": "VA loader", "purpose": "VA per MiB of custom content: 4 MiB dxCreateTexture + 2 MiB DFF per step until "
            "the VA yellow line (3 GiB), then the guard reaction and the release",
            "stages": [{"id": "loader", "title": "textures and DFF until VA yellow", "env": _env(),
                        "camera": {"kind": "player"},
                        "probe": {"kind": "loader", "tex_side": 1024, "tex_mib": 4, "tex_per_step": 1, "dff_mib": 2,
                                  "dff_per_step": 1, "dff_path": "gen/probe.dff", "step_ms": 100, "yellow_mib": YELLOW_MIB,
                                  "max_steps": 800, "hold_ms": 3000, "release_ms": 2000},
                        "warmup": 2.0, "sample": 240.0}]}


_BUILDERS = {"S1": _s1, "S2": _s2, "S3": _s3, "S4": _s4, "S5": _s5, "S6": _s6, "S7": _s7, "S8": _s8, "S9": _s9}

#: scene id -> {title, purpose, stages}
SCENES: dict[str, dict[str, Any]] = {k: b() for k, b in _BUILDERS.items()}


def scene_ids() -> list[str]:
    return list(SCENES)


def scene(name: str) -> dict[str, Any]:
    """One scene by id (case-insensitive; ``BAD_PARAMS`` with suggestions otherwise)."""
    from ..core.errors import SatkError

    key = str(name or "").strip().upper()
    if key not in SCENES:
        raise SatkError("BAD_PARAMS", f"unknown bench scene {name!r}", hint="satk ingame bench-scenes",
                        did_you_mean=difflib.get_close_matches(key, list(SCENES), n=3, cutoff=0.4) or list(SCENES)[:3])
    return SCENES[key]


def resolve(name: str, *, duration: float | None = None, warmup: float | None = None) -> list[dict[str, Any]]:
    """The stages of scene ``name`` (a deep copy) with ``--duration`` as the sample seconds and ``--warmup`` applied."""
    from ..core.errors import SatkError

    if duration is not None and not 1.0 <= float(duration) <= 3600.0:
        raise SatkError("BAD_PARAMS", "duration must be 1..3600 seconds")
    if warmup is not None and not 0.0 <= float(warmup) <= 120.0:
        raise SatkError("BAD_PARAMS", "warmup must be 0..120 seconds")
    stages = copy.deepcopy(scene(name)["stages"])
    for st in stages:
        st.setdefault("warmup", WARMUP_S)
        st.setdefault("sample", SAMPLE_S)
        if duration is not None:
            st["sample"] = float(duration)
        if warmup is not None:
            st["warmup"] = float(warmup)
    return stages


def total_seconds(stages: list[dict[str, Any]]) -> float:
    """Planned run time of a stage list (warm-up + sample of each stage)."""
    return sum(float(s.get("warmup", WARMUP_S)) + float(s.get("sample", SAMPLE_S)) for s in stages)


def rows() -> list[list]:
    """Rows ``[scene, stages, seconds, title, purpose]`` for ``ingame bench-scenes``."""
    out = []
    for k, sc in SCENES.items():
        stages = resolve(k)
        out.append([k, len(stages), round(total_seconds(stages), 1), sc["title"], sc["purpose"]])
    return out
