"""Vehicle colours ``data/carcols.dat`` (``CVehicleModelInfo::LoadVehicleColours``), SPEC §4.2. Stdlib only.

Sections (each closed by ``end``; commas count as spaces; lines starting with ``#`` are comments):

* ``col`` - the palette: ``r, g, b  # <index> <name>  <radio name>``; the N-th line is colour N;
* ``car`` - ``<model name>, c1, c2, c1, c2, ...``: up to 8 primary/secondary variations;
* ``car4`` - ``<model name>, c1, c2, c3, c4, ...``: up to 8 four-colour variations (``camper``, ``cement``,
  ``squalo`` in vanilla).

Values are palette indexes. A model listed twice keeps the later line (the engine overwrites it).
Vanilla quirk: palette line 98 reads ``77.93,96`` (a dot instead of a comma); it is read as
``77, 93, 96`` (gta-reversed ``FIX_BUGS``) and reported through ``errors``.

Example::

    cc = parse_carcols(read_text(resolve_ci(root, "data/carcols.dat")))
    cc.palette[1].rgb      # 0xF5F5F5 (white)
    cc.cars["infernus"]    # CarColors(name='infernus', sets=((12, 1), (64, 1), ...), ...)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .rw import FormatError

__all__ = ["PaletteColor", "CarColors", "Carcols", "parse_carcols", "MAX_VARIATIONS"]

#: Variations per model the engine stores (``m_anPrimaryColors[8]``).
MAX_VARIATIONS = 8

_INTS = re.compile(r"[+-]?\d+")
_COMMENT = re.compile(r"^\s*(\d+)\s+(.*?)\s*$")


@dataclass(frozen=True, slots=True)
class PaletteColor:
    """Palette entry ``idx`` = ``0xRRGGBB``; ``name``/``radio`` from the trailing comment (if any)."""

    idx: int
    rgb: int
    line: int
    name: str | None = None
    radio: str | None = None

    @property
    def hex(self) -> str:
        return f"#{self.rgb:06x}"


@dataclass(frozen=True, slots=True)
class CarColors:
    """Colour variations of one model: ``sets`` of 2 (``car``) or 4 (``car4``) palette indexes."""

    name: str
    line: int
    sets: tuple[tuple[int, ...], ...]
    four: bool = False


@dataclass(frozen=True)
class Carcols:
    """Parsed ``carcols.dat``: ``palette`` in index order, ``cars`` by lower-case model name (winning line)."""

    palette: tuple[PaletteColor, ...]
    cars: dict[str, CarColors] = field(default_factory=dict)
    duplicates: int = 0


def _palette(body: str, comment: str | None, n: int, idx: int, errors: list | None, strict: bool) -> PaletteColor:
    vals = [int(v) for v in _INTS.findall(body)]
    if len(vals) < 3:
        raise ValueError(f"carcols: palette line needs r g b, got {body.strip()!r}")
    msg = None
    if len(vals) > 3 or not re.fullmatch(r"\s*[+-]?\d+\s+[+-]?\d+\s+[+-]?\d+\s*", body):
        msg = f"carcols: palette line {body.strip()!r} read as {vals[:3]} (malformed separators)"
    elif any(not 0 <= v <= 255 for v in vals[:3]):
        msg = f"carcols: palette line {body.strip()!r} has components outside 0..255 (clamped)"
    if msg:
        if strict:
            raise FormatError("carcols", n, msg)
        if errors is not None:
            errors.append((n, msg))
    r, g, b = (max(0, min(255, v)) for v in vals[:3])
    name = radio = None
    if comment:
        m = _COMMENT.match(comment)
        text = m.group(2) if m else comment.strip()
        parts = [p for p in re.split(r"\t+|\s{2,}", text) if p.strip()]
        name = parts[0].strip() if parts else None
        radio = parts[-1].strip() if len(parts) > 1 else None
    return PaletteColor(idx, (r << 16) | (g << 8) | b, n, name, radio)


def parse_carcols(text: str, *, strict: bool = False, errors: list | None = None) -> Carcols:
    """Parse ``carcols.dat``.

    Args:
        text: file contents (latin-1 decoded).
        strict: raise ``FormatError(kind="carcols", offset=<line>)`` on a malformed line.
        errors: if given, ``(line, message)`` of malformed or repaired lines are appended.
    """
    palette: list[PaletteColor] = []
    cars: dict[str, CarColors] = {}
    dups = 0
    mode: str | None = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body, _, comment = line.partition("#")
        body = body.replace(",", " ")
        if mode is None:
            head = body.split()[0].lower() if body.split() else ""
            if head.startswith("car4"):
                mode = "car4"
            elif head.startswith("car"):
                mode = "car"
            elif head.startswith("col"):
                mode = "col"
            continue
        if body.strip().lower().startswith("end"):
            mode = None
            continue
        try:
            if mode == "col":
                palette.append(_palette(body, comment or None, n, len(palette), errors, strict))
                continue
            toks = body.split()
            k = 4 if mode == "car4" else 2
            nums: list[int] = []
            for t in toks[1:]:
                if not re.fullmatch(r"[+-]?\d+", t):
                    break
                nums.append(int(t))
            nums = nums[:k * MAX_VARIATIONS]
            sets = tuple(tuple(nums[i:i + k]) for i in range(0, len(nums) - k + 1, k))
            if not toks or not sets:
                raise ValueError(f"carcols: {mode} line needs a model name and {k} colours")
            key = toks[0].lower()
            if key in cars:
                dups += 1
            cars[key] = CarColors(toks[0], n, sets, mode == "car4")
        except ValueError as e:
            if strict:
                raise FormatError("carcols", n, str(e)) from None
            if errors is not None:
                errors.append((n, str(e)))
    return Carcols(tuple(palette), cars, dups)
