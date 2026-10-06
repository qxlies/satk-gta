"""``data/popcycle.dat``: ped/car densities and ped-group mix per zone type, day and 2-hour slot. Stdlib only.

The engine (``CPopCycle::Initialise``) reads 20 zone types x (weekday, weekend) x 12 two-hour slots = 480
data lines in order (lines starting with ``/`` and empty lines are skipped), each with
``sscanf("%hhu" x 24)``: max peds, max cars, % dealers, % gang, % cops, % other, then the 18 ped groups of
"other" (:data:`GROUPS`). Values are bytes (0..255); numbers after the 24th are ignored (the stock lines carry
26). A zone's type comes from its zone-info record (set by scripts), not from ``info.zon``.

Patches replace only the changed number tokens; everything else in the file stays byte for byte.
"""

from __future__ import annotations

import re

from .common import split_lines

__all__ = ["ZONE_TYPES", "FIELDS", "GROUPS", "PopRow", "PopcycleFile", "DAYS", "SLOTS"]

#: ``eZonePopulationType`` in file order
ZONE_TYPES = ("BUSINESS", "DESERT", "ENTERTAINMENT", "COUNTRYSIDE", "RESIDENTIAL_RICH", "RESIDENTIAL_AVERAGE",
              "RESIDENTIAL_POOR", "GANGLAND", "BEACH", "SHOPPING", "PARK", "INDUSTRY", "ENTERTAINMENT_BUSY",
              "SHOPPING_BUSY", "SHOPPING_POSH", "RESIDENTIAL_RICH_SECLUDED", "AIRPORT", "GOLF_CLUB",
              "OUT_OF_TOWN_FACTORY", "AIRPORT_RUNWAY")
#: the 18 "other" ped groups (``ePopcycleGroupPerc``)
GROUPS = ("workers", "business", "clubbers", "farmers", "beachfolk", "parkfolk", "casual_rich", "casual_average",
          "casual_poor", "prostitutes", "criminals", "golfers", "servants", "aircrew", "entertainers",
          "out_of_town_factory", "desert_folk", "aircrew_runway")
FIELDS = ("max_peds", "max_cars", "dealers", "gang", "cops", "other") + GROUPS
DAYS = ("weekday", "weekend")
SLOTS = 12
_TOK = re.compile(r"\S+")


def _tokens(content: str) -> list[tuple[int, int, int]]:
    """Leading whitespace-separated numbers as ``sscanf`` reads them (it stops at the first other token)."""
    out = []
    for m in _TOK.finditer(content):
        if not m.group().isdigit():
            break
        out.append((m.start(), m.end(), int(m.group())))
    return out


class PopRow:
    __slots__ = ("zone", "day", "slot", "line", "values", "nread")

    def __init__(self, zone: int, day: int, slot: int, line: int, values: list[int], nread: int):
        self.zone, self.day, self.slot, self.line, self.values, self.nread = zone, day, slot, line, values, nread

    @property
    def hours(self) -> str:
        return f"{self.slot * 2:02d}-{self.slot * 2 + 2:02d}"

    def as_dict(self) -> dict:
        return dict(zip(FIELDS, self.values))


class PopcycleFile:
    def __init__(self, text: str):
        self.lines = split_lines(text)
        self.rows: list[PopRow] = []
        self.errors: list[tuple[int, str]] = []
        self._new: dict[int, str] = {}
        k = 0
        for n, (c, _e) in enumerate(self.lines, 1):
            if not c.strip() or c.lstrip().startswith("/"):
                continue
            nums = [v for _a, _b, v in _tokens(c)]
            zone, rest = divmod(k, 2 * SLOTS)
            day, slot = divmod(rest, SLOTS)
            if len(nums) < len(FIELDS):
                self.errors.append((n, f"{len(nums)} of {len(FIELDS)} numbers; the rest stay 0 in the engine"))
            vals = (nums + [0] * len(FIELDS))[:len(FIELDS)]
            self.rows.append(PopRow(zone, day, slot, n, vals, min(len(nums), len(FIELDS))))
            k += 1
        if k != len(ZONE_TYPES) * 2 * SLOTS:
            self.errors.append((0, f"{k} data lines; the engine reads {len(ZONE_TYPES) * 2 * SLOTS}"))

    def select(self, zone: str | None, day: str | None, hour: str | None) -> list[PopRow]:
        sel = self.rows
        if zone and zone.lower() != "all":
            z = zone.strip().upper()
            idx = int(z) if z.isdigit() else (ZONE_TYPES.index(z) if z in ZONE_TYPES else None)
            if idx is None:
                import difflib

                cands = [t for t in ZONE_TYPES if z in t] or difflib.get_close_matches(z, ZONE_TYPES, n=4, cutoff=0.4)
                raise LookupError(f"no zone type {zone!r}", cands or list(ZONE_TYPES))
            if not 0 <= idx < len(ZONE_TYPES):
                raise LookupError(f"zone type index {idx} outside 0..{len(ZONE_TYPES) - 1}", list(ZONE_TYPES))
            sel = [r for r in sel if r.zone == idx]
        if day and day.lower() != "all":
            d = day.strip().lower()
            if d not in DAYS:
                raise LookupError(f"bad day {day!r}", list(DAYS))
            sel = [r for r in sel if r.day == DAYS.index(d)]
        if hour is not None and str(hour).lower() != "all":
            slots = set()
            for part in str(hour).split(","):
                p = part.strip().lower().removesuffix("h")
                if not p.isdigit() or not 0 <= int(p) <= 23:
                    raise LookupError(f"bad hour {hour!r} (0..23; a slot covers two hours)", [])
                slots.add(int(p) // 2)
            sel = [r for r in sel if r.slot in slots]
        return sel

    def set(self, row: PopRow, changes: dict[int, int]) -> None:
        i = row.line - 1
        content = self._new.get(i, self.lines[i][0])
        spans = [(a, b) for a, b, _v in _tokens(content)][:len(FIELDS)]
        extra = [s for s in changes if s >= len(spans)]
        if extra:
            vals = list(row.values)
            for s, v in changes.items():
                vals[s] = v
            pad = content[spans[-1][1]:] if spans else ""
            content = content[:spans[-1][1]] if spans else content
            content += "".join(f" {vals[s]}" for s in range(len(spans), max(extra) + 1)) + pad
            spans = [(a, b) for a, b, _v in _tokens(content)][:len(FIELDS)]
            changes = {s: v for s, v in changes.items() if s < len(spans)}
        out, last = [], 0
        for s in sorted(changes):
            a, b = spans[s]
            out.append(content[last:a])
            out.append(str(changes[s]))
            last = b
        out.append(content[last:])
        self._new[i] = "".join(out)

    def render(self) -> bytes:
        return "".join(self._new.get(i, c) + e for i, (c, e) in enumerate(self.lines)).encode("latin-1")

    def changed_lines(self) -> list[int]:
        return sorted(i + 1 for i, c in self._new.items() if c != self.lines[i][0])
