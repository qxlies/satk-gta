"""Water polygons ``data/water.dat`` / ``water1.dat``: documents, canonical writer, engine limits. Stdlib only.

Line format (parsed by :mod:`satk.formats.water`): ``processed`` header, then one polygon per line, 3 or 4
vertices ``x y z flow_x flow_y big_waves small_waves`` and an optional ``flags`` (bit 0 visible, bit 1
shallow). The stock files use ``%.1f`` for x/y, ``%.5f`` for the other five numbers, four spaces between
vertices, two before the flags, LF line ends; :func:`write_water` writes exactly that, so both vanilla files
round-trip byte for byte (a line keeps its original text while its values are unchanged, for files written
by other tools).

Engine rules (``CWaterLevel``): x/y are truncated to integers and clamped to the world square (+-3000; a
clamped vertex loses its wave/flow values); a quad is an axis-aligned rectangle (two x and two y values;
vertex order bottom-left, bottom-right, top-left, top-right); vertices with equal x, y, z are shared. Fixed
pools: 301 quads, 6 triangles, 1021 vertices -- vanilla ``water.dat`` already uses all 301 quads and all 6
triangles, so adding water to the single-player game needs a limit adjuster or removing other polygons.
MTA:SA replaces the pools (512 quads, 32 triangles, 1536 vertices, vanilla water included) and adds water
from Lua with ``createWater`` (same vertex order; it requires a proper rectangle inside +-3000).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .common import fmt_num, split_lines

__all__ = ["FORMAT", "WPoly", "WaterDoc", "parse_water_doc", "write_water", "doc_to_json", "doc_from_json",
           "poly_issues", "counts", "limit_warnings", "rect_poly", "QUAD_MAX", "TRI_MAX", "VERT_MAX",
           "MTA_QUAD_MAX", "MTA_TRI_MAX", "MTA_VERT_MAX", "WORLD"]

FORMAT = "satk-water/1"
QUAD_MAX, TRI_MAX, VERT_MAX = 301, 6, 1021
MTA_QUAD_MAX, MTA_TRI_MAX, MTA_VERT_MAX = 512, 32, 1536
WORLD = 3000.0
FIELDS = ("x", "y", "z", "flow_x", "flow_y", "big_waves", "small_waves")


@dataclass
class WPoly:
    verts: list[list[float]]
    flags: int | None = 1
    raw: str | None = None
    before: list[str] = field(default_factory=list)
    line: int = 0

    @property
    def kind(self) -> str:
        return "quad" if len(self.verts) == 4 else "tri"

    def canonical(self) -> str:
        s = "    ".join(" ".join([fmt_num(float(v[0]), 1), fmt_num(float(v[1]), 1)] +
                                 [fmt_num(float(x), 5) for x in v[2:7]]) for v in self.verts)
        return s + (f"  {self.flags}" if self.flags is not None else "")

    def values(self) -> tuple:
        return tuple(tuple(float(x) for x in v) for v in self.verts), self.flags

    def text(self) -> str:
        if self.raw is not None:
            p = _parse_line(self.raw)
            if p is not None and p.values() == self.values():
                return self.raw
        return self.canonical()

    def bbox(self) -> tuple[float, float, float, float]:
        xs = [v[0] for v in self.verts]
        ys = [v[1] for v in self.verts]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass
class WaterDoc:
    polys: list[WPoly] = field(default_factory=list)
    head: list[str] = field(default_factory=lambda: ["processed"])
    tail: list[str] = field(default_factory=list)
    eol: str = "\n"
    final_eol: bool = True


def _parse_line(line: str) -> WPoly | None:
    from ..formats.water import parse_water

    errs: list = []
    ps = parse_water(line, errors=errs)
    if len(ps) != 1 or errs:
        return None
    p = ps[0]
    return WPoly([v.as_list() for v in p.verts], p.flags)


def parse_water_doc(text: str, errors: list | None = None) -> WaterDoc:
    from ..formats.water import parse_water

    doc = WaterDoc(head=[])
    lines = split_lines(text)
    eols = [e for _c, e in lines if e]
    doc.eol = max(set(eols), key=eols.count) if eols else "\n"
    doc.final_eol = bool(lines) and bool(lines[-1][1])
    pending: list[str] = []
    first = True
    for n, (content, _e) in enumerate(lines, 1):
        errs: list = []
        ps = parse_water(content, errors=errs)
        if len(ps) == 1 and not errs:
            p = ps[0]
            wp = WPoly([v.as_list() for v in p.verts], p.flags, raw=content, line=n)
            if first:
                doc.head, pending, first = pending, [], False
            else:
                wp.before, pending = pending, []
            doc.polys.append(wp)
            continue
        if errs and errors is not None:
            errors.append((n, errs[0][1]))
        pending.append(content)
    if first:
        doc.head, pending = pending, []
    doc.tail = pending
    return doc


def write_water(doc: WaterDoc) -> bytes:
    out = list(doc.head)
    for p in doc.polys:
        out += p.before
        out.append(p.text())
    out += doc.tail
    return (doc.eol.join(out) + (doc.eol if doc.final_eol and out else "")).encode("latin-1")


def doc_to_json(doc: WaterDoc, file: str | None = None) -> dict:
    polys = []
    for p in doc.polys:
        d: dict = {"verts": [list(v) for v in p.verts]}
        if p.flags is not None:
            d["flags"] = p.flags
        if p.raw is not None and p.raw != p.canonical():
            d["raw"] = p.raw
        if p.before:
            d["before"] = p.before
        polys.append(d)
    out: dict = {"format": FORMAT}
    if file:
        out["file"] = file
    out.update({"fields": list(FIELDS), "eol": "lf" if doc.eol == "\n" else "crlf", "head": doc.head,
                "polys": polys})
    if doc.tail:
        out["tail"] = doc.tail
    return out


def doc_from_json(obj) -> WaterDoc:
    if isinstance(obj, list):
        obj = {"polys": obj}
    if not isinstance(obj, dict) or not isinstance(obj.get("polys"), list):
        raise ValueError('a water JSON document is {"polys": [{"verts": [[x, y, z, fx, fy, big, small], ...], '
                         '"flags": 1}, ...]} (satk-water/1)')
    fmt = obj.get("format", FORMAT)
    if fmt != FORMAT:
        raise ValueError(f"unknown format {fmt!r}; expected {FORMAT}")
    doc = WaterDoc(head=[str(x) for x in obj.get("head", ["processed"])],
                   tail=[str(x) for x in obj.get("tail", [])],
                   eol="\r\n" if str(obj.get("eol", "lf")).lower() == "crlf" else "\n")
    for i, d in enumerate(obj["polys"]):
        try:
            verts = [[float(x) for x in v] for v in d["verts"]]
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"poly {i}: {e}") from None
        if len(verts) not in (3, 4) or any(len(v) != 7 for v in verts):
            raise ValueError(f"poly {i}: 3 or 4 vertices of 7 numbers ({', '.join(FIELDS)})")
        flags = d.get("flags")
        doc.polys.append(WPoly(verts, None if flags is None else int(flags), raw=d.get("raw"),
                               before=[str(x) for x in d.get("before", [])]))
    return doc


def rect_poly(x0: float, y0: float, x1: float, y1: float, z: float, flow: tuple[float, float] = (0.0, 0.0),
              waves: tuple[float, float] = (0.0, 0.0), flags: int = 1) -> WPoly:
    """An axis-aligned quad in the engine's vertex order (bottom-left, bottom-right, top-left, top-right)."""
    xa, xb = sorted((x0, x1))
    ya, yb = sorted((y0, y1))
    rest = [z, flow[0], flow[1], waves[0], waves[1]]
    return WPoly([[xa, ya, *rest], [xb, ya, *rest], [xa, yb, *rest], [xb, yb, *rest]], flags)


def poly_issues(p: WPoly) -> list[str]:
    """What the engine (and MTA's createWater) would reject or change."""
    out = []
    xs = [int(v[0]) for v in p.verts]
    ys = [int(v[1]) for v in p.verts]
    if any(abs(v[0]) > WORLD or abs(v[1]) > WORLD for v in p.verts):
        out.append("outside +-3000: clamped, waves/flow of those vertices reset")
    if p.kind == "quad":
        if len(set(xs)) != 2 or len(set(ys)) != 2:
            out.append("not an axis-aligned rectangle (needs two x and two y values)")
        elif not (xs[0] < xs[1] and xs[2] < xs[3] and ys[0] < ys[2] and ys[1] < ys[3]):
            out.append("vertex order is not bottom-left, bottom-right, top-left, top-right (MTA createWater "
                       "refuses it)")
    elif len(set(xs)) == 1 or len(set(ys)) == 1:
        out.append("degenerate triangle (skipped by the engine)")
    return out


def counts(polys: list[WPoly]) -> dict:
    """Quads, triangles and shared vertices as the engine counts them."""
    verts = {(int(max(-WORLD, min(WORLD, v[0]))), int(max(-WORLD, min(WORLD, v[1]))), float(v[2]))
             for p in polys for v in p.verts}
    return {"quads": sum(1 for p in polys if p.kind == "quad"), "tris": sum(1 for p in polys if p.kind == "tri"),
            "verts": len(verts)}


def limit_warnings(c: dict) -> list[str]:
    w = []
    if c["quads"] > QUAD_MAX or c["tris"] > TRI_MAX or c["verts"] > VERT_MAX:
        w.append(f"LIMIT: {c['quads']} quads / {c['tris']} triangles / {c['verts']} vertices exceed the "
                 f"single-player pools ({QUAD_MAX}/{TRI_MAX}/{VERT_MAX}): needs a limit adjuster, or remove other "
                 f"polygons; MTA:SA has {MTA_QUAD_MAX}/{MTA_TRI_MAX}/{MTA_VERT_MAX} (use --target mta)")
    return w
