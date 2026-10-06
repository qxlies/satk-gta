"""Zone files ``data/info.zon`` and ``data/map.zon``: documents, canonical writer, engine checks. Stdlib only.

A line ``NAME, type, x0, y0, z0, x1, y1, z1, level, LABEL`` inside ``zone`` ... ``end`` is read by the engine
with ``sscanf("%s %d %f %f %f %f %f %f %d %s")`` (commas count as spaces) and handed to ``CTheZones::CreateZone``:

* ``NAME`` (the zone-info label: zones with the same name share one population/gang record) and ``LABEL``
  (the GXT key of the shown name) are copied upper-cased into ``char[8]``: at most 7 characters;
* type 0 = navigation zone (the name shown when entering), 1 = local navigation zone, 3 = map zone
  (``map.zon``: island of the level); type 2 has no array and must not be used;
* coordinates are truncated to ``int16`` and min/max are swapped when given the wrong way round;
* the pools are fixed: 380 navigation zones, 39 map zones, 380 zone-info records. The engine itself creates
  ``SAN_AND`` (navigation, the whole map) and ``THEMAP`` (map) first, so vanilla ``info.zon`` (378 zones) leaves
  one free slot. There is no bounds check: a longer file overwrites memory.

The shown zone name at a point is the navigation zone with the smallest ``width + height`` that contains the
point (the first one on a tie; ``SAN_AND`` when none does).

Writer: ``NAME, type, x0, ..., level, LABEL`` with the stock number format (:func:`common.fmt_g`, the C++
stream default the Rockstar tools wrote: ``200.0``, ``-4.57764e-005``), CRLF. Reading vanilla ``map.zon`` and
writing it back is bit-exact; ``info.zon`` too, because a zone keeps its original line (``raw``) as long as
its values do not change (one vanilla line, ``VERO2\\t, 0, ...``, has a stray tab the canonical form drops).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .common import fmt_g, split_lines

__all__ = ["FORMAT", "Zone", "ZonDoc", "parse_zon_doc", "write_zon", "doc_to_json", "doc_from_json",
           "zone_errors", "relations", "zones_at", "limits", "NAVI_MAX", "MAP_MAX", "INFO_MAX", "LABEL_MAX",
           "TYPES", "ENGINE_NAVI", "ENGINE_MAP"]

FORMAT = "satk-zon/1"
NAVI_MAX = 380
MAP_MAX = 39
INFO_MAX = 380
LABEL_MAX = 7
#: valid ``type`` values -> meaning
TYPES = {0: "navigation", 1: "local navigation", 3: "map"}
#: zones ``CTheZones::Init`` creates before the files are read
ENGINE_NAVI = ("SAN_AND", 0, (-3000, -3000, -2000), (3000, 3000, 2000))
ENGINE_MAP = ("THEMAP", 3, (-3000, -3000, -2000), (3000, 3000, 2000))
_LABEL = re.compile(r"^[A-Za-z0-9_]+$")
_EOL = {"crlf": "\r\n", "lf": "\n"}


@dataclass
class Zone:
    name: str
    type: int
    min: tuple[float, float, float]
    max: tuple[float, float, float]
    level: int
    label: str
    raw: str | None = None
    before: list[str] = field(default_factory=list)
    line: int = 0

    def values(self) -> tuple:
        return (self.name, self.type, tuple(map(float, self.min)), tuple(map(float, self.max)), self.level,
                self.label)

    def canonical(self) -> str:
        nums = [fmt_g(v) for v in (*self.min, *self.max)]
        return ", ".join([self.name, str(self.type), *nums, str(self.level), self.label])

    def text(self) -> str:
        if self.raw is not None:
            z = _parse_line(self.raw)
            if z is not None and z.values() == self.values():
                return self.raw
        return self.canonical()

    def ibox(self) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        """The box as the engine stores it: int16-truncated, min/max sorted."""
        a = [int(v) for v in self.min]
        b = [int(v) for v in self.max]
        lo = tuple(min(x, y) for x, y in zip(a, b))
        hi = tuple(max(x, y) for x, y in zip(a, b))
        return lo, hi  # type: ignore[return-value]


@dataclass
class ZonDoc:
    zones: list[Zone] = field(default_factory=list)
    tail: list[str] = field(default_factory=lambda: ["end"])
    eol: str = "\r\n"
    final_eol: bool = True


def _fields(line: str) -> list[str]:
    return line.split("#", 1)[0].replace(",", " ").split()


def _parse_line(line: str) -> Zone | None:
    f = _fields(line)
    if len(f) < 9:
        return None
    try:
        x0, y0, z0, x1, y1, z1 = (float(v) for v in f[2:8])
        return Zone(f[0], int(f[1]), (x0, y0, z0), (x1, y1, z1), int(f[8]), f[9] if len(f) > 9 else "")
    except (ValueError, OverflowError):
        return None


def parse_zon_doc(text: str, errors: list | None = None) -> ZonDoc:
    """A document of a ``.zon`` (or a text IPL's ``zone`` section); other lines are kept as they are."""
    doc = ZonDoc(tail=[])
    lines = split_lines(text)
    eols = [e for _c, e in lines if e]
    doc.eol = max(set(eols), key=eols.count) if eols else "\r\n"
    doc.final_eol = bool(lines) and bool(lines[-1][1])
    pending: list[str] = []
    in_zone = False
    for n, (content, _eol) in enumerate(lines, 1):
        s = content.strip().lower()
        if not in_zone:
            pending.append(content)
            if s == "zone":
                in_zone = True
            continue
        if s == "end":
            in_zone = False
            pending.append(content)
            continue
        if not s or s.startswith("#"):
            pending.append(content)
            continue
        z = _parse_line(content)
        if z is None:
            if errors is not None:
                errors.append((n, f"not a zone line: {content.strip()[:60]!r}"))
            pending.append(content)
            continue
        if errors is not None and len(_fields(content)) != 10:
            errors.append((n, f"{len(_fields(content))} fields; the engine reads exactly 10"))
        z.raw, z.before, z.line = content, pending, n
        pending = []
        doc.zones.append(z)
    doc.tail = pending
    return doc


def write_zon(doc: ZonDoc) -> bytes:
    out: list[str] = []
    for z in doc.zones:
        out += z.before
        out.append(z.text())
    out += doc.tail
    body = doc.eol.join(out) + (doc.eol if doc.final_eol and out else "")
    return body.encode("latin-1")


# --------------------------------------------------------------------------- JSON


def doc_to_json(doc: ZonDoc, file: str | None = None) -> dict:
    zones = []
    for z in doc.zones:
        d: dict = {"name": z.name, "type": z.type, "min": list(z.min), "max": list(z.max), "level": z.level,
                   "label": z.label}
        if z.raw is not None and z.raw != z.canonical():
            d["raw"] = z.raw
        if z.before and z is not doc.zones[0]:
            d["before"] = z.before
        zones.append(d)
    out: dict = {"format": FORMAT}
    if file:
        out["file"] = file
    out["eol"] = "crlf" if doc.eol == "\r\n" else "lf"
    out["head"] = doc.zones[0].before if doc.zones else []
    out["zones"] = zones
    out["tail"] = doc.tail
    return out


def doc_from_json(obj) -> ZonDoc:
    if isinstance(obj, list):
        obj = {"zones": obj}
    if not isinstance(obj, dict) or not isinstance(obj.get("zones"), list):
        raise ValueError("a zone JSON document is {\"zones\": [...]} (satk-zon/1)")
    fmt = obj.get("format", FORMAT)
    if fmt != FORMAT:
        raise ValueError(f"unknown format {fmt!r}; expected {FORMAT}")
    doc = ZonDoc(eol=_EOL.get(str(obj.get("eol", "crlf")).lower(), "\r\n"),
                 tail=[str(x) for x in obj.get("tail", ["end"])])
    for i, d in enumerate(obj["zones"]):
        try:
            mn, mx = d["min"], d["max"]
            if len(mn) != 3 or len(mx) != 3:
                raise ValueError("min and max need 3 numbers")
            z = Zone(str(d["name"]), int(d.get("type", 0)), tuple(float(v) for v in mn),
                     tuple(float(v) for v in mx), int(d.get("level", 0)), str(d.get("label", d["name"])))
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"zone {i}: {e}") from None
        z.raw = d.get("raw")
        z.before = [str(x) for x in d.get("before", [])]
        doc.zones.append(z)
    if doc.zones:
        doc.zones[0].before = [str(x) for x in obj.get("head", ["zone"])]
    return doc


# --------------------------------------------------------------------------- engine rules


def zone_errors(z: Zone) -> list[str]:
    """What the engine cannot take (crash or silent truncation)."""
    errs = []
    for what, v in (("name", z.name), ("label", z.label)):
        if not v or len(v) > LABEL_MAX or not _LABEL.match(v):
            errs.append(f"{what} {v!r}: 1..{LABEL_MAX} letters, digits or '_' (char[8] in CZone)")
    if z.type not in TYPES:
        errs.append(f"type {z.type}: use 0 (navigation), 1 (local navigation) or 3 (map); 2 has no zone array")
    if not 0 <= z.level <= 3:
        errs.append(f"level {z.level}: 0 none, 1 LS, 2 SF, 3 LV")
    for v in (*z.min, *z.max):
        if not -32768 <= v <= 32767:
            errs.append(f"coordinate {v} does not fit int16")
            break
    return errs


def relations(a: Zone, b: Zone) -> str | None:
    """``inside`` (a in b), ``contains``, ``same``, ``partial`` (boxes share volume but neither holds the other)
    or ``None`` (no shared volume); on the engine's int16 boxes."""
    (a0, a1), (b0, b1) = a.ibox(), b.ibox()
    if any(min(a1[i], b1[i]) <= max(a0[i], b0[i]) for i in range(3)):
        return None
    ain = all(a0[i] >= b0[i] and a1[i] <= b1[i] for i in range(3))
    bin_ = all(b0[i] >= a0[i] and b1[i] <= a1[i] for i in range(3))
    if ain and bin_:
        return "same"
    if ain:
        return "inside"
    if bin_:
        return "contains"
    return "partial"


def _navi(z: Zone) -> bool:
    return z.type in (0, 1)


def zones_at(zones: list[Zone], x: float, y: float, z: float | None = None) -> tuple[list[Zone], Zone | None]:
    """Navigation zones containing the point (smallest first) and the one the game names (``None``: SAN_AND)."""
    hits = []
    for zn in zones:
        if not _navi(zn):
            continue
        lo, hi = zn.ibox()
        if lo[0] <= x <= hi[0] and lo[1] <= y <= hi[1] and (z is None or lo[2] <= z <= hi[2]):
            hits.append(zn)

    def size(zn: Zone) -> int:
        lo, hi = zn.ibox()
        return hi[0] - lo[0] + hi[1] - lo[1]

    shown = None
    best = 12000                                   # SAN_AND: 6000 + 6000
    for zn in hits:
        s = size(zn)
        if s < best:
            shown, best = zn, s
    return sorted(hits, key=lambda zn: (size(zn), zn.line)), shown


def limits(navi: int, mapz: int, infos: int) -> list[str]:
    """Warnings for the fixed pools (counts include the engine's own SAN_AND/THEMAP)."""
    w = []
    if navi > NAVI_MAX:
        w.append(f"LIMIT: {navi} navigation zones (with the engine's SAN_AND) > {NAVI_MAX}: the game overwrites "
                 f"memory; needs a limit adjuster that raises the navigation zone pool")
    if mapz > MAP_MAX:
        w.append(f"LIMIT: {mapz} map zones (with THEMAP) > {MAP_MAX}: needs a limit adjuster")
    if infos > INFO_MAX:
        w.append(f"LIMIT: {infos} zone-info records (distinct navigation zone names) > {INFO_MAX}")
    return w
