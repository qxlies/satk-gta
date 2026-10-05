"""Binary IPL (``bnry``) <-> text IPL, bit for bit (B4; report 30 G4).

A ``bnry`` file (``*_streamN.ipl`` inside ``gta3.img``/``gta_int.img``) is a 0x4C-byte header
(``tBinaryIplFile``: magic, six counts, six ``(offset, size)`` pairs), ``inst`` records of 40 bytes
``<7f3i>`` (position, quaternion as stored, model, interior, lod) and car generators of 48 bytes
``<4f8i>``. Only ``inst`` and ``cars`` exist in binary form.

:func:`decompile` writes a standard text IPL (``inst`` + ``cars`` sections) the game's text loader
could also read; :func:`compile_ipl` turns such a text back into ``bnry``. The round trip is exact:

* floats are written as the shortest decimal that parses back to the same float32 bits
  (``-0`` stays ``-0``);
* the header layout and the padding are detected and kept in a directive comment
  ``# satk-bnry: style=vanilla pad=sector``:

  * ``style=vanilla`` - the size fields are 0 and the car offset is 0 without cars (every vanilla file);
  * ``style=sizes`` - the size fields are filled (some third-party tools);
  * ``pad=sector`` - zero padding to a multiple of 2048 bytes (an IMG entry as extracted);
    ``pad=none`` - no padding.

``lod`` of a ``bnry`` placement indexes the ``inst`` section of the parent text IPL (``lae2_stream0`` ->
``lae2.ipl``), not the binary file itself; the value is copied as is. Names are not stored in
``bnry``: :func:`decompile` takes them from ``names`` (the index) or writes ``model<id>``.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from decimal import Decimal

from ..formats.ide import iter_sections

__all__ = ["HEADER_SIZE", "SECTOR", "STYLES", "PADS", "BnryInfo", "is_bnry", "f32_text", "decompile", "compile_ipl",
           "parse_bnry", "encode_bnry", "first_difference"]

HEADER_SIZE = 0x4C
SECTOR = 2048
STYLES = ("vanilla", "sizes")
PADS = ("sector", "none")
_HDR = struct.Struct("<4s6I12I")
_INST = struct.Struct("<7f3i")
_CAR = struct.Struct("<4f8i")
_DIRECTIVE = re.compile(r"^\s*#\s*satk-bnry:(.*)$", re.IGNORECASE)
_NAME_BAD = re.compile(r"[\s,#]+")
_I32 = (-(1 << 31), (1 << 31) - 1)


@dataclass
class BnryInfo:
    """What :func:`parse_bnry` found: records as raw tuples plus the detected layout.

    ``style``/``pad`` are ``None`` when the file does not follow a known layout (then a compile
    cannot reproduce it, :attr:`why` says why).
    """

    insts: list[tuple] = field(default_factory=list)   # (x, y, z, qx, qy, qz, qw, model, interior, lod)
    cars: list[tuple] = field(default_factory=list)    # (x, y, z, angle, model, col1, col2, flags, alarm, lock, min, max)
    style: str | None = "vanilla"
    pad: str | None = "sector"
    size: int = 0
    why: list[str] = field(default_factory=list)


def is_bnry(data: bytes) -> bool:
    return data[:4] == b"bnry"


def f32_text(v: float) -> str:
    """Shortest positional decimal that parses back to the same float32 bits (``-0`` kept)."""
    if not math.isfinite(v):
        return repr(v)
    raw = struct.pack("<f", v)
    s = repr(v)
    for p in range(1, 10):
        t = f"{v:.{p}g}"
        try:
            if struct.pack("<f", float(t)) == raw:
                s = t
                break
        except OverflowError:
            continue
    if "e" in s or "E" in s:
        s = format(Decimal(s), "f")
    return s


def _header(style: str, n_inst: int, n_cars: int) -> bytes:
    inst_off = HEADER_SIZE
    after = HEADER_SIZE + 40 * n_inst
    if style == "sizes":
        return _HDR.pack(b"bnry", n_inst, 0, 0, 0, n_cars, 0, inst_off, 40 * n_inst, 0, 0, 0, 0, 0, 0,
                         after, 48 * n_cars, 0, 0)
    return _HDR.pack(b"bnry", n_inst, 0, 0, 0, n_cars, 0, inst_off, 0, 0, 0, 0, 0, 0, 0,
                     after if n_cars else 0, 0, 0, 0)


def encode_bnry(insts: list[tuple], cars: list[tuple], style: str = "vanilla", pad: str = "sector") -> bytes:
    """Binary IPL bytes of raw records (see :class:`BnryInfo`)."""
    if style not in STYLES:
        raise ValueError(f"unknown bnry style {style!r} (one of {', '.join(STYLES)})")
    if pad not in PADS:
        raise ValueError(f"unknown bnry padding {pad!r} (one of {', '.join(PADS)})")
    parts = [_header(style, len(insts), len(cars))]
    parts += [_INST.pack(*r) for r in insts]
    parts += [_CAR.pack(*r) for r in cars]
    out = b"".join(parts)
    if pad == "sector" and len(out) % SECTOR:
        out += b"\0" * (SECTOR - len(out) % SECTOR)
    return out


def parse_bnry(data: bytes) -> BnryInfo:
    """Records and layout of a ``bnry`` file (``ValueError`` on a broken one)."""
    n = len(data)
    if n < HEADER_SIZE:
        raise ValueError(f"bnry header needs {HEADER_SIZE} bytes, have {n}")
    h = _HDR.unpack_from(data, 0)
    if h[0] != b"bnry":
        raise ValueError(f"not a binary IPL (magic {h[0]!r})")
    n_inst, n_cars, inst_off, cars_off = h[1], h[5], h[7], h[15]
    if n_inst and (inst_off < HEADER_SIZE or inst_off + 40 * n_inst > n):
        raise ValueError(f"inst table ({n_inst} x 40 at {inst_off}) outside the file ({n} bytes)")
    if n_cars and (cars_off < HEADER_SIZE or cars_off + 48 * n_cars > n):
        raise ValueError(f"car table ({n_cars} x 48 at {cars_off}) outside the file ({n} bytes)")
    info = BnryInfo(size=n)
    info.insts = [_INST.unpack_from(data, inst_off + 40 * i) for i in range(n_inst)]
    info.cars = [_CAR.unpack_from(data, cars_off + 48 * i) for i in range(n_cars)]
    info.style = next((s for s in STYLES if _header(s, n_inst, n_cars) == data[:HEADER_SIZE]), None)
    if info.style is None:
        info.why.append("the header has fields satk does not reproduce (unused counts/offsets or another layout)")
    end = HEADER_SIZE + 40 * n_inst + 48 * n_cars
    tail = data[end:]
    if not tail:
        info.pad = "none"
    elif not any(tail) and n % SECTOR == 0 and len(tail) < SECTOR:
        info.pad = "sector"
    else:
        info.pad = None
        info.why.append(f"{len(tail)} bytes after the records are not zero padding to 2048")
    if any(not math.isfinite(v) for r in info.insts for v in r[:7]) or \
            any(not math.isfinite(v) for r in info.cars for v in r[:4]):
        info.why.append("non-finite float values (NaN payloads are not kept)")
    return info


def _u32(v: int) -> int:
    return v & 0xFFFFFFFF


def decompile(data: bytes, *, names: dict[int, str] | None = None, label: str = "", parent: str | None = None
              ) -> tuple[str, BnryInfo]:
    """Text IPL of a ``bnry`` file and its :class:`BnryInfo` (``ValueError`` on a broken file)."""
    info = parse_bnry(data)
    names = names or {}
    style = info.style or "vanilla"
    pad = info.pad or "sector"
    out = [f"# satk ipl decompile: {label or 'bnry'} ({len(info.insts)} inst, {len(info.cars)} cars)",
           "# inst: id, name, interior, x, y, z, qx, qy, qz, qw, lod"]
    if parent:
        out.append(f"# lod indexes the inst section of the parent text IPL {parent}.ipl, not this file")
    out.append("# satk ipl compile turns this text back into the binary file; keep the next line")
    out.append(f"# satk-bnry: style={style} pad={pad}")
    out.append("inst")
    for x, y, z, qx, qy, qz, qw, model, interior, lod in info.insts:
        name = _NAME_BAD.sub("_", names.get(model) or f"model{model}")
        f = ", ".join(f32_text(v) for v in (x, y, z, qx, qy, qz, qw))
        out.append(f"{model}, {name}, {_u32(interior)}, {f}, {lod}")
    out.append("end")
    if info.cars:
        out.append("cars")
        for c in info.cars:
            out.append(", ".join([f32_text(v) for v in c[:4]] + [str(v) for v in c[4:]]))
        out.append("end")
    return "\n".join(out) + "\n", info


def _directives(text: str) -> dict[str, str]:
    d: dict[str, str] = {}
    for raw in text.splitlines():
        m = _DIRECTIVE.match(raw)
        if m:
            for kv in m.group(1).split():
                k, _, v = kv.partition("=")
                d[k.strip().lower()] = v.strip().lower()
    return d


def _int(tok: str, what: str, lo: int = _I32[0], hi: int = _I32[1]) -> int:
    try:
        v = int(tok)
    except ValueError:
        try:
            v = int(tok, 16) if tok.lower().startswith(("0x", "-0x")) else int(float(tok))
        except ValueError:
            raise ValueError(f"{what} is not an integer: {tok!r}") from None
    if not lo <= v <= hi:
        raise ValueError(f"{what} {v} does not fit 32 bits")
    return v


def _float(tok: str, what: str) -> float:
    try:
        v = float(tok)
    except ValueError:
        raise ValueError(f"{what} is not a number: {tok!r}") from None
    try:
        struct.pack("<f", v)
    except OverflowError:
        raise ValueError(f"{what} {tok} does not fit a float32") from None
    return v


def compile_ipl(text: str, *, style: str = "auto", pad: str = "auto") -> tuple[bytes, dict]:
    """``bnry`` bytes of a text IPL plus a report ``{inst, cars, style, pad, skipped, lods}``.

    ``style``/``pad`` = ``auto`` take the ``# satk-bnry:`` directive (written by :func:`decompile`),
    else ``vanilla``/``sector``. Sections other than ``inst``/``cars`` cannot be stored in ``bnry``:
    they are counted in ``skipped``. A malformed line raises ``ValueError("line N: ...")``.
    """
    d = _directives(text)
    st = d.get("style", "vanilla") if style == "auto" else style
    pd = d.get("pad", "sector") if pad == "auto" else pad
    if st not in STYLES:
        raise ValueError(f"unknown bnry style {st!r} (one of {', '.join(STYLES)})")
    if pd not in PADS:
        raise ValueError(f"unknown bnry padding {pd!r} (one of {', '.join(PADS)})")
    insts: list[tuple] = []
    cars: list[tuple] = []
    skipped: dict[str, int] = {}
    lods = 0
    for sec, f, n in iter_sections(text):
        try:
            if sec == "inst":
                if len(f) not in (10, 11):
                    raise ValueError(f"inst needs 10-11 fields (id, name, interior, x, y, z, qx, qy, qz, qw, lod), "
                                     f"got {len(f)}")
                model = _int(f[0], "model id")
                interior = _int(f[2], "interior", _I32[0], 0xFFFFFFFF)
                if interior > _I32[1]:
                    interior -= 1 << 32
                vals = [_float(t, "coordinate") for t in f[3:10]]
                lod = _int(f[10], "lod") if len(f) > 10 else -1
                lods += lod >= 0
                insts.append((*vals, model, interior, lod))
            elif sec == "cars":
                if len(f) != 12:
                    raise ValueError(f"cars needs 12 fields (x, y, z, angle, model, col1, col2, flags, alarm, "
                                     f"door_lock, min_delay, max_delay), got {len(f)}")
                cars.append((*(_float(t, "coordinate") for t in f[:4]), *(_int(t, "car field") for t in f[4:])))
            else:
                skipped[sec] = skipped.get(sec, 0) + 1
        except ValueError as e:
            raise ValueError(f"line {n}: {e}") from None
    data = encode_bnry(insts, cars, st, pd)
    return data, {"inst": len(insts), "cars": len(cars), "style": st, "pad": pd, "skipped": dict(sorted(skipped.items())),
                  "lods": lods, "directive": bool(d)}


def first_difference(a: bytes, b: bytes) -> int | None:
    """Offset of the first differing byte (or the shorter length), ``None`` if equal."""
    if a == b:
        return None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))
