"""Water polygons ``data/water.dat`` and ``data/water1.dat`` (``CWaterLevel``), SPEC §4.2. Stdlib only.

The engine reads ``water.dat`` (or ``water1.dat`` when a script switches the water configuration to 1)
line by line. Text format: an optional first line ``processed``, then one polygon per line: three
(triangle) or four (quad) vertices of seven numbers each, then an optional integer ``flags``::

    processed
    -1584.0 -1826.0 0.0 0.0 0.0 0.051 0.102   -1360.0 -1826.0 ... 0.102   ...  1

Vertex fields: ``x y z flow_x flow_y big_waves small_waves`` (``x``/``y`` are truncated to integers
by the engine; ``flow`` is packed as ``int8(flow * 64)``). ``flags``: bit 0 visible, bit 1 shallow
water (limited depth). Vanilla ``water.dat`` has 301 quads + 6 triangles, all with ``flags``;
``water1.dat`` has 261 + 6 and no ``flags`` column. Commas count as spaces (``CFileLoader::LoadLine``).

Example::

    polys = parse_water(read_text(resolve_ci(root, "data/water.dat")))
    quads = [p for p in polys if p.kind == "quad"]
"""

from __future__ import annotations

from dataclasses import dataclass

from .rw import FormatError

__all__ = ["WaterVertex", "WaterPoly", "parse_water", "VERTEX_FIELDS", "FLAG_VISIBLE", "FLAG_SHALLOW"]

#: Names of the seven numbers of a vertex, in file order.
VERTEX_FIELDS = ("x", "y", "z", "flow_x", "flow_y", "big_waves", "small_waves")
FLAG_VISIBLE = 1
FLAG_SHALLOW = 2


@dataclass(frozen=True, slots=True)
class WaterVertex:
    """One vertex as stored in the file (floats, not yet truncated like the engine does)."""

    x: float
    y: float
    z: float
    flow_x: float
    flow_y: float
    big_waves: float
    small_waves: float

    def as_list(self) -> list[float]:
        return [self.x, self.y, self.z, self.flow_x, self.flow_y, self.big_waves, self.small_waves]


@dataclass(frozen=True, slots=True)
class WaterPoly:
    """A water quad (4 vertices) or triangle (3). ``idx`` is the 0-based polygon order in the file,
    ``line`` the 1-based line number, ``flags`` ``None`` when the line has no flags column."""

    idx: int
    line: int
    verts: tuple[WaterVertex, ...]
    flags: int | None

    @property
    def kind(self) -> str:
        return "quad" if len(self.verts) == 4 else "tri"

    @property
    def bbox(self) -> tuple[float, float, float, float, float, float]:
        """``(minx, miny, minz, maxx, maxy, maxz)`` over the vertices."""
        xs = [v.x for v in self.verts]
        ys = [v.y for v in self.verts]
        zs = [v.z for v in self.verts]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _float(tok: str) -> float:
    v = float(tok)
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"not a finite number: {tok!r}")
    return v


def parse_water(text: str, *, strict: bool = False, errors: list | None = None) -> list[WaterPoly]:
    """Water polygons of ``water.dat``/``water1.dat`` in file order.

    Lines that do not start with a vertex (the ``processed`` header, blank lines) are skipped like
    the engine skips them. A line with fewer than three complete vertices, or with junk after the
    vertices and the flags, is malformed.

    Args:
        text: file contents (latin-1 decoded, see ``dat.read_text``).
        strict: raise ``FormatError(kind="water", offset=<line>)`` on a malformed line.
        errors: if given, ``(line, message)`` of skipped lines are appended.
    """
    out: list[WaterPoly] = []
    for n, raw in enumerate(text.splitlines(), 1):
        toks = raw.replace(",", " ").split()
        if not toks:
            continue
        try:
            first = [_float(t) for t in toks[:7]] if len(toks) >= 7 else None
        except ValueError:
            first = None
        if first is None:
            if toks[0].lower() == "processed" and len(toks) == 1:
                continue
            msg = f"water: no vertex at the start of the line ({len(toks)} fields)"
            if strict:
                raise FormatError("water", n, msg)
            if errors is not None:
                errors.append((n, msg))
            continue
        try:
            nv = min(4, len(toks) // 7)
            verts = []
            for i in range(nv):
                vals = [_float(t) for t in toks[i * 7:(i + 1) * 7]]
                verts.append(WaterVertex(*vals))
            rest = toks[nv * 7:]
            if nv < 3:
                raise ValueError(f"water: {nv} vertices, need 3 or 4")
            if len(rest) > 1:
                raise ValueError(f"water: {len(rest)} extra fields after {nv} vertices")
            flags = int(float(rest[0])) if rest else None
        except (ValueError, OverflowError) as e:
            if strict:
                raise FormatError("water", n, str(e)) from None
            if errors is not None:
                errors.append((n, str(e)))
            continue
        out.append(WaterPoly(len(out), n, tuple(verts), flags))
    return out
