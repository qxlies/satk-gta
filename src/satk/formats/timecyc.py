"""Time cycle ``data/timecyc.dat`` (``CTimeCycle::Initialise``), SPEC §4.2. Stdlib only.

The engine reads 23 weathers x 8 hours = 184 data lines in order (weather-major); every line that
starts with ``/`` is a comment. Hours of the 8 points: 0, 5, 6, 7, 12, 19, 20, 22 (:data:`HOURS`).
A 24-hour file (552 data lines, e.g. *timecycle24*) is read with hours 0..23. Weather names come from
the ``//////////// EXTRASUNNY_LA`` comment above each block (fallback: :data:`WEATHERS`).

One line = 51 numbers (+ an optional 52nd ``dir_mult``), read with one ``sscanf``
(:data:`SLOTS`): ``%d`` for colours and shadows, ``%f`` for sizes, distances, water and post-FX colours.
The parser repeats the engine's reading exactly: values are consumed left to right with the slot type;
when a slot cannot be read the rest of the line is ignored and **those slots keep the values of the
previous line** (the engine's locals live outside the loop). Vanilla line 320 (``RAINY_COUNTRYSIDE``
8 PM) starts with a lone ``255`` instead of three ambient values: the engine reads 20 values shifted
by two slots and repeats the rest from 7 PM (``TimecycRow.nread = 20``).

Example::

    rows = parse_timecyc(read_text(resolve_ci(root, "data/timecyc.dat")))
    noon = next(r for r in rows if r.weather_name == "SUNNY_LA" and r.hour == 12)
    noon.values["sky_top"]  # [30, 117, 210]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .rw import FormatError

__all__ = ["TimecycRow", "parse_timecyc", "HOURS", "WEATHERS", "SLOTS", "GROUPS", "N_REQUIRED"]

#: Clock hours of the 8 time points of every weather (``TimeCycle.h``).
HOURS = (0, 5, 6, 7, 12, 19, 20, 22)
#: ``eWeatherType`` names in file order (used when a block has no name comment).
WEATHERS = (
    "EXTRASUNNY_LA", "SUNNY_LA", "EXTRASUNNY_SMOG_LA", "SUNNY_SMOG_LA", "CLOUDY_LA", "SUNNY_SF", "EXTRASUNNY_SF",
    "CLOUDY_SF", "RAINY_SF", "FOGGY_SF", "SUNNY_VEGAS", "EXTRASUNNY_VEGAS", "CLOUDY_VEGAS", "EXTRASUNNY_COUNTRYSIDE",
    "SUNNY_COUNTRYSIDE", "CLOUDY_COUNTRYSIDE", "RAINY_COUNTRYSIDE", "EXTRASUNNY_DESERT", "SUNNY_DESERT",
    "SANDSTORM_DESERT", "UNDERWATER", "EXTRACOLOURS_1", "EXTRACOLOURS_2",
)

#: Named groups of slots: ``(name, sscanf type, count)``. Colours are ``[r, g, b]``; ``water`` is
#: ``[r, g, b, a]``; ``postfx1``/``postfx2`` (colour correction) are ``[a, r, g, b]``.
GROUPS: tuple[tuple[str, str, int], ...] = (
    ("amb", "d", 3), ("amb_obj", "d", 3), ("dir", "d", 3), ("sky_top", "d", 3), ("sky_bot", "d", 3),
    ("sun_core", "d", 3), ("sun_corona", "d", 3),
    ("sun_size", "f", 1), ("sprite_size", "f", 1), ("sprite_bright", "f", 1),
    ("shadow", "d", 1), ("light_shadow", "d", 1), ("pole_shadow", "d", 1),
    ("far_clip", "f", 1), ("fog_start", "f", 1), ("light_on_ground", "f", 1),
    ("low_clouds", "d", 3), ("bottom_clouds", "d", 3),
    ("water", "f", 4), ("postfx1", "f", 4), ("postfx2", "f", 4),
    ("cloud_alpha", "f", 1), ("high_light_min", "d", 1), ("water_fog_alpha", "d", 1), ("dir_mult", "f", 1),
)
#: Flat list of ``(group, type)`` per sscanf slot (52).
SLOTS: tuple[tuple[str, str], ...] = tuple((g, t) for g, t, k in GROUPS for _ in range(k))
#: Slots a complete line has (``dir_mult`` is optional; the engine warns below 51).
N_REQUIRED = len(SLOTS) - 1

_WEATHER_HDR = re.compile(r"^/{4,}\s*([A-Za-z0-9_]+)\s*$")
_INT = re.compile(r"\s*([+-]?\d+)")
_FLT = re.compile(r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")


@dataclass(frozen=True, slots=True)
class TimecycRow:
    """One weather x hour line. ``values``: group name -> number or list (as the engine reads them);
    ``nread`` = slots read from this line (51 or 52 normally; fewer = carried over from the line before)."""

    weather: int
    weather_name: str
    hour: int
    line: int
    nread: int
    values: dict = field(default_factory=dict)


def _scan(line: str) -> list:
    """sscanf with :data:`SLOTS`: values read until the first slot that does not match."""
    out: list = []
    pos = 0
    for _g, t in SLOTS:
        m = (_INT if t == "d" else _FLT).match(line, pos)
        if not m:
            break
        out.append(int(m.group(1)) if t == "d" else float(m.group(1)))
        pos = m.end()
    return out


def _group(flat: list) -> dict:
    vals: dict = {}
    i = 0
    for name, _t, k in GROUPS:
        part = flat[i:i + k]
        i += k
        vals[name] = part[0] if k == 1 else list(part)
    return vals


def parse_timecyc(text: str, *, strict: bool = False, errors: list | None = None) -> list[TimecycRow]:
    """Rows of ``timecyc.dat`` in file order (weather-major).

    Args:
        text: file contents (latin-1 decoded).
        strict: raise ``FormatError(kind="timecyc", offset=<line>)`` on a short line or an unexpected
            number of data lines instead of reporting them.
        errors: if given, ``(line, message)`` of short lines are appended (the row is still returned,
            completed from the previous line like the engine does).
    """
    data: list[tuple[int, str, str | None]] = []  # (line, text, weather header seen before it)
    pending: str | None = None
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if not s:
            continue
        if s.startswith("/"):
            m = _WEATHER_HDR.match(s)
            if m:
                pending = m.group(1)
            continue
        data.append((n, s, pending))
        pending = None
    per = 24 if len(data) == len(WEATHERS) * 24 else len(HOURS)
    hours = tuple(range(24)) if per == 24 else HOURS
    if len(data) != len(WEATHERS) * per:
        msg = f"timecyc: {len(data)} data lines, the engine reads {len(WEATHERS) * len(HOURS)}"
        if strict:
            raise FormatError("timecyc", data[-1][0] if data else 0, msg)
        if errors is not None:
            errors.append((data[-1][0] if data else 0, msg))
    out: list[TimecycRow] = []
    prev: list = [None] * len(SLOTS)
    names: dict[int, str] = {}
    for i, (n, s, hdr) in enumerate(data):
        w, h = divmod(i, per)
        if hdr and w not in names:
            names[w] = hdr
        flat = _scan(s)
        if len(flat) < N_REQUIRED:
            msg = (f"timecyc: {len(flat)} of {N_REQUIRED} values readable; the engine keeps the previous "
                   f"line's values for the rest")
            if strict:
                raise FormatError("timecyc", n, msg)
            if errors is not None:
                errors.append((n, msg))
        cur = flat + prev[len(flat):]
        if len(flat) == N_REQUIRED:
            cur[-1] = None  # no dir_mult on this line (not carried over: the engine ignores it below 52)
        prev = cur
        wname = names.get(w) or (WEATHERS[w] if w < len(WEATHERS) else f"WEATHER{w}")
        out.append(TimecycRow(w, wname, hours[h] if h < len(hours) else h, n, len(flat), _group(cur)))
    return out
