"""Numbers of the SA game look, shared by every preview renderer (Blender and the soft renderer).

Stdlib only: imported by satk (CPython 3.12+) and inside Blender (Python 3.13). Each rule names the engine
function it follows (``gta_sa.exe`` 1.0 US addresses). Values that the engine computes at run time from
things a preview does not have (the contact surface under a car, the player's position) are fixed
constants here, marked *uncalibrated*: they will be tuned against in-game captures.

Colour chain of one pixel (display space, 0..1, as the D3D9 fixed-function pipeline computes it):

* lit models (vehicles, peds, objects): ``tex * mat * (amb_obj * L * surf_ambient + dir * L * surf_diffuse *
  max(0, N.L))`` then the env pass (``lerp`` towards the env texture by ``WAVE_ALPHA`` on UV2 for ``x*`` env
  textures, else ``+ env * min(1, coef * SPEC_INTENSITY)``) and ``+ specular``;
* prelit models (map buildings): ``tex * mat * (lerp(day, night, balance) + amb * surf_ambient)``;
* the whole frame then gets the timecyc colour filter (:func:`grade_factors`) and the default display gamma
  (:data:`DISPLAY_GAMMA`).

Example::

    from satk.look import gamelook as G
    env = G.env_at("21:30")            # timecyc light at 21:30 (EXTRASUNNY_LA), from the index or built in
    G.dirt_rgb((120, 100, 80), 2)      # -> (238, 236, 233): dirt level 2 of a vehiclegrunge256 texel
    G.grade_factors(env)               # -> (fr, fg, fb): colour filter multipliers
"""

from __future__ import annotations

import math
from typing import Any

__all__ = [
    "DIRT_DEFAULT", "DIRT_MAX", "DIRT_SPAWN_MAX", "DIRT_TEXTURE", "LIGHTS_TEXTURE", "LIGHTS_ON_TEXTURE",
    "LAMP_KEYS", "PAINT_KEYS", "PAINT_DEFAULT", "LIT_MULT", "LIT_NIGHT", "lit_mult", "LIGHT_DIR", "SPEC_INTENSITY", "WAVE_ALPHA", "LIGHT_SPECULAR", "DISPLAY_GAMMA",
    "WEATHERS", "DEFAULT_WEATHER", "HOURS", "dirt_rgb", "dirt_t", "parse_time", "day_night_balance", "env_at",
    "default_env", "grade_factors", "display_curve", "lamp_index", "paint_slot", "is_gunflash", "LOOKS",
]

#: Looks every preview renderer knows.
LOOKS = ("game", "clay", "raw", "wire")

# --------------------------------------------------------------------------- vehicles

#: The body paint texture the dirt system swaps (``CCarFXRenderer::SetDirtTextures`` 0x5D5DB0).
DIRT_TEXTURE = "vehiclegrunge256"
#: Dirt levels 0..15 (``InitialiseDirtTexture`` 0x5D5BC0 builds 16 textures); a spawned car gets 0..14
#: (``CVehicle::CVehicle``); level 16 is the raw ``vehiclegrunge256`` (dirtier than any car in the game).
DIRT_MAX = 16
DIRT_SPAWN_MAX = 14
#: Default dirt of previews: a lightly used car (the raw texture is level 16).
DIRT_DEFAULT = 2
#: Lamp texture and its lights-on twin (``CVehicleModelInfo::SetEditableMaterialsCB``).
LIGHTS_TEXTURE = "vehiclelights128"
LIGHTS_ON_TEXTURE = "vehiclelightson128"
#: Lamp key colours -> light index (front left, front right, rear left, rear right). Every material on
#: ``vehiclelights128`` is drawn white; a keyed one swaps to ``vehiclelightson128`` when its light is on.
#: A key colour on any other texture stays as it is (that lamp never lights up).
LAMP_KEYS = {(255, 175, 0): 0, (0, 255, 200): 1, (185, 255, 0): 2, (255, 60, 0): 3}
#: Paint key colours -> carcols slot 1..4.
PAINT_KEYS = {(60, 255, 0): 1, (255, 0, 175): 2, (0, 255, 255): 3, (255, 0, 255): 4}
#: Paint of slots 1..4 when a car has no carcols entry (a preview of a new model): blue, white, greys.
PAINT_DEFAULT = ((42, 119, 161), (245, 245, 245), (88, 89, 90), (88, 89, 90))
#: ``gSpecIntensity * 1.85`` (``CCustomCarEnvMapPipeline::CustomPipeRenderCB`` 0x5D9900; gSpecIntensity = 1).
SPEC_INTENSITY = 1.85
#: Blend factor of the ``x*`` env texture (``xvehicleenv128``) on UV2: ``min(255, SPEC_INTENSITY * 24) / 255``.
WAVE_ALPHA = min(255, int(SPEC_INTENSITY * 24.0)) / 255.0
#: Specular colour of the car light (``g_GameLight.Specular``); material specular = min(1, 2 * SPEC_INTENSITY *
#: level), power = 100 * level.
LIGHT_SPECULAR = 0.65
#: Light multiplier of peds, cars and objects (``CPhysical::GetLightingFromCol``: ambient + the COL face light
#: under the entity). *Uncalibrated*: 0.5 is a lit street by day.
LIT_MULT = 0.5
#: Share of :data:`LIT_MULT` left at night (the night face light of a street is lower). *Uncalibrated*.
LIT_NIGHT = 0.5
#: Direction towards the one preview light (model/world space, a vehicle faces +Y): front-right, above.
#: Every renderer uses it, so a lineup and the soft and Blender previews get the same light.
LIGHT_DIR = tuple(v / math.sqrt(0.30 ** 2 + 0.55 ** 2 + 0.78 ** 2) for v in (0.30, 0.55, 0.78))
#: Default display gamma of the PC game (``CGamma::SetGamma`` 0x747200 with the default brightness 256:
#: ``power = 1 - 0.35 + 0.2``; applied to the frame like the D3D gamma ramp).
DISPLAY_GAMMA = 0.85


def lit_mult(balance: float = 0.0) -> float:
    """Light multiplier of lit models at a day/night ``balance`` (0 day .. 1 night)."""
    b = max(0.0, min(1.0, float(balance)))
    return LIT_MULT * (1.0 + (LIT_NIGHT - 1.0) * b)


def dirt_t(level: float) -> float:
    """Share of the grunge texel at dirt ``level`` (0 = clean white, 16 = the raw texture)."""
    lv = max(0.0, min(float(DIRT_MAX), float(level)))
    return lv / DIRT_MAX


def dirt_rgb(c, level: float) -> tuple[int, int, int]:
    """RGB of a ``vehiclegrunge256`` texel ``c`` at dirt ``level``: ``c * i / 16 + 255 * (16 - i) / 16``
    (``InitialiseDirtTexture`` 0x5D5BC0; alpha is kept)."""
    t = dirt_t(level)
    return tuple(int(round(float(v) * t + 255.0 * (1.0 - t))) for v in c[:3])  # type: ignore[return-value]


def lamp_index(rgb) -> int | None:
    """Light index 0..3 of a lamp key colour (alpha ignored), else ``None``."""
    return LAMP_KEYS.get(tuple(int(v) for v in rgb[:3]))


def paint_slot(rgb) -> int | None:
    """Carcols slot 1..4 of a paint key colour (alpha ignored), else ``None``."""
    return PAINT_KEYS.get(tuple(int(v) for v in rgb[:3]))


def is_gunflash(name: str) -> bool:
    """Weapon muzzle-flash atomics (``gunflash``, ``gunflash.001``...): the game draws them only when firing."""
    return str(name).split(".")[0].lower().startswith("gunflash")


# --------------------------------------------------------------------------- time of day

#: Clock hours of the 8 timecyc points (``TimeCycle.h``).
HOURS = (0, 5, 6, 7, 12, 19, 20, 22)
WEATHERS = ("EXTRASUNNY_LA", "SUNNY_LA", "EXTRASUNNY_SMOG_LA", "SUNNY_SMOG_LA", "CLOUDY_LA", "SUNNY_SF",
            "EXTRASUNNY_SF", "CLOUDY_SF", "RAINY_SF", "FOGGY_SF", "SUNNY_VEGAS", "EXTRASUNNY_VEGAS", "CLOUDY_VEGAS",
            "EXTRASUNNY_COUNTRYSIDE", "SUNNY_COUNTRYSIDE", "CLOUDY_COUNTRYSIDE", "RAINY_COUNTRYSIDE",
            "EXTRASUNNY_DESERT", "SUNNY_DESERT", "SANDSTORM_DESERT", "UNDERWATER", "EXTRACOLOURS_1", "EXTRACOLOURS_2")
DEFAULT_WEATHER = "EXTRASUNNY_LA"

#: Built-in fallback: the vanilla ``timecyc.dat`` values of EXTRASUNNY_LA (numbers only), used when no index
#: is built. Per hour: amb, amb_obj, dir (RGB 0..255), postfx1, postfx2 ([a, r, g, b] as the engine reads them).
_BUILTIN = {
    0: ((22, 22, 22), (220, 212, 130), (255, 255, 255), (255, 87, 87, 87), (255, 60, 121, 122)),
    5: ((22, 22, 22), (194, 194, 142), (255, 255, 255), (255, 80, 80, 80), (255, 60, 190, 190)),
    6: ((22, 22, 22), (210, 194, 182), (255, 255, 255), (255, 86, 86, 86), (255, 149, 94, 0)),
    7: ((5, 0, 0), (210, 194, 182), (255, 255, 255), (255, 133, 106, 70), (255, 96, 61, 15)),
    12: ((11, 0, 0), (210, 194, 182), (255, 255, 255), (255, 66, 66, 48), (255, 166, 129, 60)),
    19: ((8, 5, 5), (255, 255, 182), (255, 255, 255), (255, 124, 124, 107), (255, 86, 50, 10)),
    20: ((25, 14, 14), (210, 194, 182), (255, 255, 255), (255, 81, 85, 40), (255, 66, 27, 0)),
    22: ((21, 20, 20), (210, 194, 182), (255, 255, 255), (255, 209, 143, 84), (255, 76, 51, 0)),
}


def parse_time(v: Any) -> tuple[int, int]:
    """``"21:30"`` / ``21.5`` / ``None`` (noon) -> ``(21, 30)``; ``ValueError`` on a bad value."""
    if v is None or v == "":
        return 12, 0
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        m = int(round(float(v) * 60)) % (24 * 60)
        return m // 60, m % 60
    s = str(v).strip()
    hh, _, mm = s.partition(":")
    if not hh.isdigit() or (mm and not mm.isdigit()) or int(hh) > 24 or int(mm or 0) > 59:
        raise ValueError(f"time: expected HH:MM, got {v!r}")
    return int(hh) % 24, int(mm or 0)


def day_night_balance(hours: int, minutes: int = 0) -> float:
    """Prelight balance (1 = night colours): 1 before 06:00, down to 0 at 07:00, 0 until 20:00, up to 1 at 21:00
    (``CCustomBuildingDNPipeline`` / ``CustomBuildingRenderer.cpp``)."""
    t = (hours % 24) + minutes / 60.0
    if t < 6.0:
        return 1.0
    if t < 7.0:
        return 1.0 - (t - 6.0)
    if t < 20.0:
        return 0.0
    if t < 21.0:
        return t - 20.0
    return 1.0


def _bracket(t: float) -> tuple[int, int, float]:
    """The two timecyc hours around ``t`` (wrapping 22 -> 24/0) and the interpolation factor."""
    hs = list(HOURS)
    for i, h in enumerate(hs):
        nxt = hs[i + 1] if i + 1 < len(hs) else 24
        if h <= t < nxt:
            return h, (nxt % 24), (t - h) / float(nxt - h)
    return hs[-1], 0, 0.0  # pragma: no cover - t in [0, 24)


def _lerp(a, b, f: float) -> list[float]:
    return [float(x) + (float(y) - float(x)) * f for x, y in zip(a, b)]


def _rows_index(weather: str, profile: str) -> dict[int, tuple] | None:
    """Rows of one weather from the index (``timecyc`` table), ``None`` when unavailable."""
    try:
        import json

        from ..index.api import open_index

        db = open_index(profile)
        env = db.query("SELECT hour, amb, amb_obj, dir, data FROM timecyc WHERE weather_name = ? COLLATE NOCASE "
                       "ORDER BY hour", [weather], limit=50)
    except Exception:  # noqa: BLE001 - no index, a fake index or an old schema: the built-in table
        return None
    out: dict[int, tuple] = {}

    def rgb(v) -> tuple[int, int, int]:
        v = int(v or 0)
        return (v >> 16) & 255, (v >> 8) & 255, v & 255

    for hour, amb, amb_obj, dirc, data in env.get("rows") or []:
        try:
            d = json.loads(data or "{}")
        except ValueError:
            d = {}
        p1 = tuple(d.get("postfx1") or (0, 0, 0, 0))
        p2 = tuple(d.get("postfx2") or (0, 0, 0, 0))
        out[int(hour)] = (rgb(amb), rgb(amb_obj), rgb(dirc), p1, p2)
    return out if all(h in out for h in HOURS) else None


def default_env(time: Any = "12:00") -> dict:
    """:func:`env_at` from the built-in EXTRASUNNY_LA table (no index needed)."""
    return _env(time, _BUILTIN, DEFAULT_WEATHER, "builtin")


def env_at(time: Any = "12:00", weather: str = DEFAULT_WEATHER, profile: str = "vanilla") -> dict:
    """Light of a game time: ``{time, balance, weather, amb, amb_obj, dir, postfx1, postfx2, source}``.

    Colours are 0..1 floats interpolated between the two timecyc hours around ``time`` (as
    ``CTimeCycle::CalcColoursForPoint``); ``postfx*`` are ``[a, r, g, b]`` 0..255 as the engine reads them.
    ``source`` is ``index`` (the profile's ``timecyc.dat``) or ``builtin`` (vanilla EXTRASUNNY_LA).
    """
    w = str(weather or DEFAULT_WEATHER).upper()
    if w not in WEATHERS:
        raise ValueError(f"weather: unknown {weather!r}; one of {', '.join(WEATHERS[:5])}, ...")
    rows = _rows_index(w, profile)
    if rows is None:
        return _env(time, _BUILTIN, DEFAULT_WEATHER, "builtin")
    return _env(time, rows, w, "index")


def _env(time: Any, rows: dict, weather: str, source: str) -> dict:
    h, m = parse_time(time)
    t = h + m / 60.0
    a, b, f = _bracket(t)
    ra, rb = rows[a], rows[b]
    amb, amb_obj, dirc = (_lerp(ra[i], rb[i], f) for i in range(3))
    p1, p2 = _lerp(ra[3], rb[3], f), _lerp(ra[4], rb[4], f)
    return {
        "time": f"{h:02d}:{m:02d}", "balance": round(day_night_balance(h, m), 4), "weather": weather,
        "amb": [round(v / 255.0, 4) for v in amb], "amb_obj": [round(v / 255.0, 4) for v in amb_obj],
        "dir": [round(v / 255.0, 4) for v in dirc],
        "postfx1": [round(v, 2) for v in p1], "postfx2": [round(v, 2) for v in p2], "source": source,
    }


def _alpha(a: float) -> float:
    """Pass alpha as the engine stores it: ``uint8(a * 2)`` (``CTimeCycle::Initialise``; 255 -> 254)."""
    return (int(float(a) * 2.0) & 0xFF) / 255.0


def grade_factors(env: dict) -> tuple[float, float, float]:
    """Per-channel multipliers of the colour filter (``CPostEffects::ColourFilter``): the frame is
    added to itself twice, tinted by ``postfx1`` and ``postfx2`` (blend SRCALPHA, ONE):
    ``out = S * (1 + rgb1 * a1 + rgb2 * a2)``."""
    p1, p2 = env.get("postfx1") or (0, 0, 0, 0), env.get("postfx2") or (0, 0, 0, 0)
    a1, a2 = _alpha(p1[0]), _alpha(p2[0])
    return tuple(round(1.0 + float(p1[i + 1]) / 255.0 * a1 + float(p2[i + 1]) / 255.0 * a2, 4)
                 for i in range(3))  # type: ignore[return-value]


def display_curve(v: float, gamma: float = DISPLAY_GAMMA) -> float:
    """The default gamma ramp of the PC game on one 0..1 value (``((i + 1) / 256) ** power``)."""
    v = max(0.0, min(1.0, float(v)))
    return min(1.0, math.pow((v * 255.0 + 1.0) / 256.0, gamma))
