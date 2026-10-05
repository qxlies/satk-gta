"""Item placements (``*.ipl``): text and binary ``bnry`` (SPEC §4.2 ``satk.formats.ipl``). Stdlib only.

Text ``inst``: ``id, name, interior, x, y, z, qx, qy, qz, qw, lod``.
Binary (``bnry``, inside ``gta3.img``/``gta_int.img``): header 0x4C bytes
(``tBinaryIplFile``: magic + 6 counts + 6 x (offset, size)), ``inst`` records of 40 bytes
``<7f3i>`` (pos, quat xyzw, model, interior, lod) and car generators of 48 bytes ``<4f8i>``.

Pitfalls (SPEC §4.2 #10, #11):

* **The quaternion is conjugated by the engine** before ``SetRotate`` (gta-reversed
  ``FileLoader.cpp:1036-1049``). :class:`Inst` keeps ``q`` EXACTLY as in the file; the world
  rotation is :func:`world_quat` (``(-x, -y, -z, w)``); :func:`rz_deg` gives the world heading
  for Z-only rotations with the engine's own rule.
* ``interior`` = area (low byte, :func:`area`) + flags (``>> 8``, :func:`iflags`):
  1 redundant_stream, 2 dont_stream, 4 underwater, 8 tunnel, 16 tunnel_transition.
* ``lod`` in a ``bnry`` is an index into the ``inst`` list of the text IPL with the same prefix
  (``xxx_streamN`` -> ``xxx``, :func:`stream_base`); in a text IPL it indexes the same file.

Example::

    insts, items = parse_ipl_text(read_text(path))
    binsts, cars = parse_ipl_binary(img.read(img.find("lae2_stream0.ipl")))
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass

from .ide import iter_sections
from .rw import FormatError

__all__ = [
    "Inst",
    "parse_ipl_text",
    "parse_ipl_binary",
    "world_quat",
    "rz_deg",
    "area",
    "iflags",
    "IFLAG_NAMES",
    "stream_base",
    "BNRY_HEADER_SIZE",
    "BNRY_INST_SIZE",
    "BNRY_CAR_SIZE",
]

BNRY_HEADER_SIZE = 0x4C
BNRY_INST_SIZE = 40
BNRY_CAR_SIZE = 48
_HDR = struct.Struct("<4s6I12I")
_INST = struct.Struct("<7f3i")
_CAR = struct.Struct("<4f8i")
_CAR_KEYS = ("x", "y", "z", "angle", "model_id", "col1", "col2", "flags", "alarm", "door_lock",
             "min_delay", "max_delay")

#: ``interior >> 8`` bit -> name.
IFLAG_NAMES = {1: "redundant_stream", 2: "dont_stream", 4: "underwater", 8: "tunnel", 16: "tunnel_transition"}

#: Text sections -> slice of the fields that hold a position (None = no position).
_ITEM_POS = {
    "cull": (0, 3), "occl": (0, 3), "grge": (0, 3), "enex": (0, 3), "pick": (0, 3), "jump": (0, 3),
    "tcyc": (0, 3), "cars": (0, 3), "auzo": (3, 6), "zone": (2, 5), "mult": None, "path": None,
}


@dataclass(frozen=True, slots=True)
class Inst:
    """One placement. ``q`` is the quaternion (x, y, z, w) EXACTLY as stored in the file;
    the world rotation is its conjugate (:func:`world_quat`). ``name`` is ``None`` in ``bnry``."""

    idx: int
    model_id: int
    name: str | None
    interior: int
    pos: tuple[float, float, float]
    q: tuple[float, float, float, float]
    lod: int


def area(interior: int) -> int:
    """Area code (interior number): low byte of the ``interior`` field."""
    return interior & 0xFF


def iflags(interior: int) -> int:
    """Instance flags: ``interior >> 8`` (see :data:`IFLAG_NAMES`)."""
    return (interior >> 8) & 0xFFFFFF


def world_quat(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """World rotation quaternion (x, y, z, w) of a file quaternion: the conjugate (V9)."""
    x, y, z, w = q
    return (-x, -y, -z, w)


def rz_deg(q: tuple[float, float, float, float], interior: int = 0) -> float | None:
    """World heading in degrees (-180, 180] if the engine treats the placement as a Z-only
    rotation (``|qx|, |qy| <= 0.05``, not a dont_stream object with qx, qy != 0), else ``None``.

    Same rule as ``CFileLoader::LoadObjectInstance``: heading = ``acos(w) * (2 if z < 0 else -2)``.
    """
    x, y, z, w = q
    if abs(x) > 0.05 or abs(y) > 0.05 or ((iflags(interior) & 2) and x != 0.0 and y != 0.0):
        return None
    a = math.degrees(math.acos(max(-1.0, min(1.0, w))) * (2.0 if z < 0.0 else -2.0))
    a = math.fmod(a, 360.0)
    if a <= -180.0:
        a += 360.0
    elif a > 180.0:
        a -= 360.0
    return a + 0.0  # no negative zero


_STREAM = re.compile(r"^(.+?)_stream\d*$", re.IGNORECASE)


def stream_base(name: str) -> str | None:
    """``"lae2_stream0"`` -> ``"lae2"`` (lower-case); ``None`` if not a ``_streamN`` name."""
    stem = name.rsplit("/", 1)[-1]
    if stem.lower().endswith(".ipl"):
        stem = stem[:-4]
    m = _STREAM.match(stem)
    return m.group(1).lower() if m else None


def _inst_text(f: list[str], idx: int) -> Inst:
    if len(f) < 10:
        raise ValueError(f"inst: {len(f)} fields, need 10-11")
    p = tuple(float(v) for v in f[3:6])
    q = tuple(float(v) for v in f[6:10])
    lod = int(f[10]) if len(f) > 10 else -1
    return Inst(idx, int(f[0]), f[1], int(f[2]), p, q, lod)  # type: ignore[arg-type]


def parse_ipl_text(
    text: str, *, strict: bool = False, errors: list | None = None
) -> tuple[list[Inst], dict[str, list[dict]]]:
    """Parse a text IPL.

    Args:
        text: file contents (latin-1 decoded).
        strict: raise ``FormatError(kind="ipl", offset=<line>)`` on a malformed line.
        errors: if given, ``(line, message)`` of skipped lines are appended.

    Returns:
        ``(inst, items)``: placements (``idx`` = position in this file's inst list, the LOD
        target space) and other sections as ``{sec: [{"idx", "line", "pos"?, "fields"}]}``.
    """
    insts: list[Inst] = []
    items: dict[str, list[dict]] = {}
    for sec, f, n in iter_sections(text):
        try:
            if sec == "inst":
                insts.append(_inst_text(f, len(insts)))
                continue
            lst = items.setdefault(sec, [])
            it: dict = {"idx": len(lst), "line": n, "fields": f}
            rng = _ITEM_POS.get(sec)
            if rng is not None and len(f) >= rng[1]:
                try:
                    it["pos"] = tuple(float(v) for v in f[rng[0]:rng[1]])
                except ValueError:
                    pass
            lst.append(it)
        except (ValueError, IndexError, OverflowError) as e:
            if strict:
                raise FormatError("ipl", n, str(e)) from None
            if errors is not None:
                errors.append((n, str(e)))
    return insts, items


def parse_ipl_binary(buf) -> tuple[list[Inst], list[dict]]:
    """Parse a binary IPL (``bnry``). Returns ``(inst, cars)``; cars are dicts with
    ``x y z angle model_id col1 col2 flags alarm door_lock min_delay max_delay``."""
    n = len(buf)
    if n < BNRY_HEADER_SIZE:
        raise FormatError("ipl", 0, f"bnry header needs {BNRY_HEADER_SIZE} bytes, have {n}")
    h = _HDR.unpack_from(buf, 0)
    if h[0] != b"bnry":
        raise FormatError("ipl", 0, f"not a binary IPL (magic {h[0]!r})")
    n_inst, n_cars = h[1], h[5]
    inst_off, cars_off = h[7], h[15]
    if n_inst and (inst_off < BNRY_HEADER_SIZE or inst_off + n_inst * BNRY_INST_SIZE > n):
        raise FormatError("ipl", 4, f"inst table ({n_inst} x 40 at {inst_off}) outside the file ({n})")
    if n_cars and (cars_off < BNRY_HEADER_SIZE or cars_off + n_cars * BNRY_CAR_SIZE > n):
        raise FormatError("ipl", 20, f"car table ({n_cars} x 48 at {cars_off}) outside the file ({n})")
    insts = []
    for i in range(n_inst):
        x, y, z, qx, qy, qz, qw, mid, interior, lod = _INST.unpack_from(buf, inst_off + i * BNRY_INST_SIZE)
        insts.append(Inst(i, mid, None, interior & 0xFFFFFFFF, (x, y, z), (qx, qy, qz, qw), lod))
    cars = [dict(zip(_CAR_KEYS, _CAR.unpack_from(buf, cars_off + i * BNRY_CAR_SIZE))) for i in range(n_cars)]
    return insts, cars
