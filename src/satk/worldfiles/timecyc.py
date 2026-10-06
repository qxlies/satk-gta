"""``data/timecyc.dat``: named values per weather and hour, token-preserving patches, diffs. Stdlib only.

Reading follows :mod:`satk.formats.timecyc` (the engine's ``sscanf`` over 23 weathers x 8 hours). A patch
replaces only the number tokens of the changed slots, so every other byte of the file (comments, tabs, the
mixed line ends of the stock file) stays as it was: patching and patching back gives the original bytes. A
line the engine reads short (vanilla line 320, ``RAINY_COUNTRYSIDE`` 8 PM, starts with a lone ``255``) is
rewritten whole in the stock layout when one of its values changes (the values the engine actually used are
written out; the warning names the line).

Storage in the engine (``CTimeCycle``), used for range warnings: colours, shadows, alphas are ``uint8``;
``sun_size``, ``sprite_size``, ``sprite_bright`` and ``light_on_ground`` are stored x10 in a byte
(0..12.7 / 0..25.5); ``far_clip`` and ``fog_start`` are ``int16``.
"""

from __future__ import annotations

import fnmatch
import re

from ..formats.timecyc import GROUPS, HOURS, N_REQUIRED, SLOTS, WEATHERS, TimecycRow, parse_timecyc
from .common import fmt_num, split_lines

__all__ = ["TimecycFile", "FIELDS", "COMPONENTS", "slot_index", "select_rows", "parse_value", "RANGES"]

#: group name -> (first slot, count, type)
FIELDS: dict[str, tuple[int, int, str]] = {}
_i = 0
for _name, _t, _k in GROUPS:
    FIELDS[_name] = (_i, _k, _t)
    _i += _k
#: component letters of multi-value groups
COMPONENTS = {3: "rgb", 4: "rgba"}
_COMP_OVERRIDE = {"postfx1": "argb", "postfx2": "argb"}
#: (min, max) the engine can store, per group
RANGES: dict[str, tuple[float, float]] = {g: (0, 255) for g, _t, _k in GROUPS}
RANGES.update({"sun_size": (0, 12.7), "sprite_size": (0, 12.7), "sprite_bright": (0, 12.7),
               "light_on_ground": (0, 25.5), "far_clip": (-32768, 32767), "fog_start": (-32768, 32767),
               "dir_mult": (0, 2.55)})
_INT = re.compile(r"\s*([+-]?\d+)")
_FLT = re.compile(r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
#: stock layout: groups of slots separated by tabs
_LAYOUT = (3, 3, 3, 3, 3, 3, 3, 6, 6, 3, 4, 4, 4, 3)


def comps(group: str) -> str:
    return _COMP_OVERRIDE.get(group, COMPONENTS.get(FIELDS[group][1], ""))


def slot_index(field: str) -> list[tuple[int, str]]:
    """``sky_top`` -> its slots; ``sky_top.g`` / ``sky_top[1]`` -> one slot. ``KeyError`` with suggestions."""
    m = re.fullmatch(r"([a-z_0-9]+)(?:\.([a-z])|\[(\d)\])?", field.strip().lower())
    if not m or m.group(1) not in FIELDS:
        raise KeyError(field)
    g = m.group(1)
    first, k, _t = FIELDS[g]
    if m.group(2) or m.group(3) is not None:
        if k == 1:
            raise KeyError(field)
        idx = comps(g).find(m.group(2)) if m.group(2) else int(m.group(3))
        if not 0 <= idx < k:
            raise KeyError(field)
        return [(first + idx, f"{g}.{comps(g)[idx]}")]
    return [(first + i, f"{g}.{comps(g)[i]}" if k > 1 else g) for i in range(k)]


def parse_value(text: str, n: int) -> list[float]:
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if len(parts) not in (1, n):
        raise ValueError(f"expected 1 or {n} numbers, got {len(parts)} ({text!r})")
    vals = [float(p) for p in parts]
    return vals * n if len(vals) == 1 else vals


def select_rows(rows: list[TimecycRow], weather: str | None, hour: str | None) -> list[TimecycRow]:
    """Rows of a weather (name, glob, index or ``all``) and an hour (number or ``all``)."""
    sel = rows
    if weather and weather.lower() != "all":
        w = weather.strip()
        if w.isdigit():
            sel = [r for r in sel if r.weather == int(w)]
        else:
            pat = w.upper()
            sel = [r for r in sel if fnmatch.fnmatchcase(r.weather_name.upper(), pat)]
        if not sel:
            names = sorted({r.weather_name for r in rows})
            raise LookupError(f"no weather {weather!r}", names)
    if hour is not None and str(hour).lower() != "all":
        hs = []
        for part in str(hour).split(","):
            part = part.strip().lower().removesuffix("h")
            if not part.isdigit():
                raise LookupError(f"bad hour {hour!r}", [str(h) for h in sorted({r.hour for r in rows})])
            hs.append(int(part))
        avail = sorted({r.hour for r in rows})
        missing = [h for h in hs if h not in avail]
        if missing:
            raise LookupError(f"hour {missing[0]} is not a time point of this file (the engine interpolates "
                              f"between {', '.join(map(str, avail))})", [str(h) for h in avail])
        sel = [r for r in sel if r.hour in hs]
    return sel


def _flat(row: TimecycRow) -> list:
    out = []
    for g, _t, k in GROUPS:
        v = row.values[g]
        out += (v if k > 1 else [v])
    return out


class TimecycFile:
    """One timecyc text with its rows; :meth:`set` changes slots, :meth:`render` gives the bytes."""

    def __init__(self, text: str):
        self.text = text
        self.lines = split_lines(text)
        self.errors: list = []
        self.rows = parse_timecyc(text, errors=self.errors)
        self.per = 24 if len(self.rows) == len(WEATHERS) * 24 else len(HOURS)
        self._new: dict[int, str] = {}                 # 0-based line index -> new content
        self.rewritten: list[int] = []

    def flat(self, row: TimecycRow) -> list:
        return _flat(row)

    def _spans(self, content: str) -> list[tuple[int, int]]:
        spans = []
        pos = 0
        for _g, t in SLOTS:
            m = (_INT if t == "d" else _FLT).match(content, pos)
            if not m:
                break
            spans.append(m.span(1))
            pos = m.end()
        return spans

    def set(self, row: TimecycRow, changes: dict[int, float]) -> None:
        """Change slots (index -> value) of ``row``; the file keeps all other bytes."""
        i = row.line - 1
        content = self._new.get(i, self.lines[i][0])
        spans = self._spans(content)
        flat = _flat(row)
        if len(spans) >= N_REQUIRED and all(s < len(spans) for s in changes):
            parts = []
            last = 0
            for s in sorted(changes):
                a, b = spans[s]
                parts.append(content[last:a])
                parts.append(self._fmt(s, changes[s], content[a:b]))
                last = b
            parts.append(content[last:])
            self._new[i] = "".join(parts)
            return
        # short or anomalous line: write every value the engine used, in the stock layout
        for s, v in changes.items():
            if s < len(flat):
                flat[s] = v
            else:
                flat += [None] * (s - len(flat)) + [v]
        if flat[-1] is None:
            flat = flat[:-1]
        self._new[i] = self._layout(flat)
        if row.line not in self.rewritten:
            self.rewritten.append(row.line)

    def _fmt(self, slot: int, v: float, old: str = "") -> str:
        t = SLOTS[slot][1]
        if t == "d":
            return str(int(round(v)))
        if "." in old:
            dec = len(old.split(".", 1)[1])
            s = f"{v:.{dec}f}"
            return s if float(s) == v else fmt_num(v, dec)
        if float(v).is_integer():
            return str(int(v))
        return fmt_num(v, 2)

    def _layout(self, flat: list) -> str:
        out, k = [], 0
        for n in _LAYOUT:
            grp = flat[k:k + n]
            k += n
            if grp:
                out.append(" ".join(self._fmt_layout(k - n + j, v) for j, v in enumerate(grp)))
        if len(flat) > k:
            out.append(" ".join(self._fmt_layout(k + j, v) for j, v in enumerate(flat[k:])))
        return "\t".join(out)

    def _fmt_layout(self, slot: int, v) -> str:
        g, t = SLOTS[slot]
        if t == "d":
            return str(int(v))
        if g in ("sun_size", "sprite_size", "sprite_bright", "far_clip", "fog_start", "light_on_ground"):
            return fmt_num(float(v), 2)
        return self._fmt(slot, float(v))

    def render(self) -> bytes:
        out = []
        for i, (c, e) in enumerate(self.lines):
            out.append(self._new.get(i, c) + e)
        return "".join(out).encode("latin-1")

    def changed_lines(self) -> list[int]:
        return sorted(i + 1 for i, c in self._new.items() if c != self.lines[i][0])
