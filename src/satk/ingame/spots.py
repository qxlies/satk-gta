"""Test spots, camera presets and the geometry of the in-game checks (sent to the game in the manifest).

Coordinates are vanilla San Andreas world metres; headings are GTA headings in degrees (0 = north/+y,
90 = west/-x, 270 = east/+x). The check areas lie on the two parallel runways of the Los Santos
airport (surface z = 12.55, ``lasrnway1..6_LAS``): runway 1 (centre line y = -2494, x 1360..2173) for
speed runs, runway 2 (y = -2593, x 1357..1887) for the pads and the crash wall. Props are vanilla
objects the server places when a spot is used: the wall ``shbbyhswall06_lvs`` (8650, a 30.5 x 0.8 x
2.24 m box collision, centred) and the ramp ``landjump2`` (1634, base 1.09 m below its origin, rising
towards local +y). Stdlib only.
"""

from __future__ import annotations

from typing import Any

__all__ = ["SPOTS", "GEOM", "CAMERAS", "spot", "parse_at", "rows"]

_RUNWAY_Z = 12.55

#: name -> {pos, h, note, env?, props?}; props are [model, x, y, z, rx, ry, rz].
SPOTS: dict[str, dict[str, Any]] = {
    "grove": {"pos": [2494.25, -1676.38, 13.25], "h": 90.0, "note": "Grove Street, LS"},
    "runway": {"pos": [1400.0, -2494.0, 13.6], "h": 270.0,
               "note": "LS airport runway 1: 770 m straight to the east for speed runs"},
    "wall": {"pos": [1740.0, -2593.0, 13.6], "h": 270.0, "note": "crash wall 60 m ahead (LS airport runway 2)",
             "props": [[8650, 1800.4, -2593.0, round(_RUNWAY_Z + 1.12, 2), 0.0, 0.0, 0.0]]},
    "ramp": {"pos": [1500.0, -2494.0, 13.6], "h": 270.0, "note": "jump ramp 200 m ahead (LS airport runway 1)",
             "props": [[1634, 1700.0, -2494.0, round(_RUNWAY_Z + 1.09, 2), 0.0, 0.0, 270.0]]},
    "pad": {"pos": [1600.0, -2593.0, 13.6], "h": 270.0, "note": "flat test pad (LS airport runway 2)"},
    "night": {"pos": [395.5, 2550.62, 16.38], "h": 90.0, "env": {"time": "00:00", "weather": 0},
              "note": "dark airstrip (Verdant Meadows) at midnight"},
    "airport": {"pos": [1961.62, -2166.38, 13.38], "h": 270.0, "note": "LS airport terminal road"},
    "vinewood": {"pos": [1244.12, -1144.88, 23.5], "h": 90.0, "note": "Vinewood street"},
    "mulholland": {"pos": [1416.0, -852.5, 47.5], "h": 90.0, "note": "Mulholland hills road"},
    "beach": {"pos": [369.62, -1785.12, 9.25], "h": 180.0, "note": "Santa Maria beach road"},
    "sf": {"pos": [-2006.38, 178.25, 27.5], "h": 180.0, "note": "San Fierro, Doherty"},
    "lv": {"pos": [2048.5, 1453.12, 10.62], "h": 0.0, "note": "Las Venturas strip"},
    "desert": {"pos": [395.5, 2550.62, 16.38], "h": 90.0, "note": "Verdant Meadows airstrip"},
}

#: Check areas: origin x/y, surface z, heading h, lane spacing and per-check numbers; park = [along, side]
#: of the parked player relative to the origin.
GEOM: dict[str, dict[str, Any]] = {
    "pad": {"x": 1600.0, "y": -2593.0, "z": _RUNWAY_Z, "h": 270.0, "spacing": 8.0, "park": [-50.0, 30.0],
            "lod_shots": [20, 60, 140]},
    "runway": {"x": 1400.0, "y": -2494.0, "z": _RUNWAY_Z, "h": 270.0, "spacing": 12.0, "park": [20.0, -25.0],
               "loop_at": 600.0, "loop_back": 520.0, "brake_room": 250.0, "max_s": 45.0, "lost": 9.0},
    "wall": {"x": 1800.0, "y": -2593.0, "z": _RUNWAY_Z, "h": 270.0, "spacing": 10.0, "park": [-80.0, 30.0],
             "start": 22.0, "speed": 50.0, "wall_model": 8650, "wall_half": 0.4, "wall_z": 1.12},
    "obj": {"x": 1500.0, "y": -2593.0, "z": _RUNWAY_Z, "h": 270.0, "spacing": 16.0, "park": [-50.0, 30.0],
            "car": 426, "car_speed": 30.0},
    "ped": {"x": 1450.0, "y": -2593.0, "z": _RUNWAY_Z, "h": 270.0, "spacing": 3.0, "park": [-40.0, 25.0],
            "skin": 7},
}

#: Camera presets of ``ingame spawn|drive|shot`` (client.lua PRESETS; chase = the game's own camera).
CAMERAS: dict[str, str] = {
    "chase": "the game camera behind the player (released)",
    "front": "in front of the target, slightly to the right",
    "rear": "behind the target",
    "side": "from the right side",
    "three_quarter": "front-right three-quarter view from above",
    "rear_quarter": "rear-right three-quarter view from above",
    "top": "from above",
    "wheel": "low, at the front wheel",
}


def spot(name: str) -> dict[str, Any]:
    """One spot by name (``BAD_PARAMS`` with suggestions otherwise)."""
    from ..core.errors import SatkError

    s = SPOTS.get(str(name).lower())
    if s is None:
        import difflib

        raise SatkError("BAD_PARAMS", f"unknown spot {name!r}", hint="satk ingame spots",
                        did_you_mean=difflib.get_close_matches(str(name).lower(), list(SPOTS), n=3, cutoff=0.5))
    return s


def parse_at(at: str | None) -> dict[str, Any]:
    """``"here"`` / a spot name / ``"x,y,z"`` -> ``{}`` / ``{"spot": name}`` / ``{"pos": [x, y, z]}``."""
    from ..core.errors import SatkError

    if at is None or str(at).strip().lower() in ("", "here"):
        return {}
    s = str(at).strip()
    if "," in s:
        try:
            xyz = [float(v) for v in s.split(",")]
        except ValueError:
            xyz = []
        if len(xyz) != 3:
            raise SatkError("BAD_PARAMS", f"at: expected 'x,y,z' or a spot name, got {at!r}")
        return {"pos": xyz}
    spot(s)
    return {"spot": s.lower()}


def rows() -> list[list]:
    """Rows ``[name, kind, pos, h, note]`` of every spot, check area and camera preset."""
    out: list[list] = []
    for name, s in SPOTS.items():
        note = s["note"]
        if s.get("env"):
            note += f" ({s['env']['time']}, weather {s['env']['weather']})"
        if s.get("props"):
            note += f"; props {', '.join(str(p[0]) for p in s['props'])}"
        out.append([name, "spot", s["pos"], s["h"], note])
    for name, g in GEOM.items():
        out.append([name, "check area", [g["x"], g["y"], g["z"]], g["h"], f"lanes {g['spacing']:g} m apart"])
    for name, note in CAMERAS.items():
        out.append([name, "camera", "", "", note])
    return out
