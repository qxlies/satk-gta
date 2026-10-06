"""2dEffect entries <-> JSON (``satk.fx2d.schema``). Stdlib only.

One entry of the 2dEffect geometry plugin (``0x253F2F8``) is ``float pos[3]; u32 type; u32 size;`` followed by
``size`` bytes of type data. :func:`decode_entry` turns it into a JSON object, :func:`encode_entry` turns the object
back into ``(pos, type, data)``; ``encode_entry(decode_entry(e)) == e`` for every entry, byte for byte.

Layouts (the game's DFF reader, ``Rwt2dEffectPluginDataChunkReadCallBack`` 0x6F9FD0; sizes it accepts):

* 0 ``light`` (80 or 76): RGBA; floats far clip, point-light range, corona size, shadow size; u8 flash, reflection,
  flare, shadow multiplier, flags (low byte); char corona[24], shadow[24]; u8 shadow z distance, flags (high byte);
  80 only: int8 look direction x, y, z + 2 padding bytes (76: 1 padding byte).
* 1 ``particle`` (24): char name[24] (an effect system of ``effects.fxp``).
* 3 ``attractor`` (56): int8 type + 3 padding; float queue, use, forward directions [3]; char script[8];
  i32 probability; u8 unk1, pad, flags, pad.
* 4 ``sun_glare`` (0): no data (the IDE ``2dfx`` section uses it; the DFF reader does not).
* 6 ``enex`` (44 or 40): float enter angle, radius x, y, exit x, y, z (relative), exit angle; i16 interior;
  u8 flags (low byte), sky colour; char interior name[8]; 44 only: u8 time on, time off, flags (high byte), pad.
* 7 ``roadsign`` (88): float size w, h, rotation x, y, z; u16 layout (bits 0-1 lines, 2-3 characters per
  line, 4-5 colour); char text[4][16] (no terminator, ``_`` is a space); 2 padding bytes.
* 8 ``trigger_point`` (4): i32 id (slot machine wheels). 9 ``cover_point`` (12): float dir x, y; int8 usage +
  3 padding. 10 ``escalator`` (40): float bottom, top, end [3]; u8 direction (1 = up) + 3 padding.

Types 2 and 5 (and any other number or a size the layout does not have) are kept as raw ``"data"`` hex.

Bytes the game ignores (the rest of a string slot after its terminator, padding) go into ``"keep"`` as hex,
only when they are not the default filler (zeros; ``_`` after road-sign text). They exist so that an
unchanged JSON writes back the identical file; deleting ``keep`` is always safe.

Floats are printed with the fewest digits that give back the same float32 bits (``0.1``, not
``0.10000000149011612``); NaN/inf are written as ``"0x7FC00000"`` (raw bits; accepted on input for any float).
"""

from __future__ import annotations

import difflib
import math
import struct
from typing import Any

__all__ = [
    "FORMAT", "TYPE_NAMES", "TYPE_IDS", "GAME_DFF_TYPES", "LIGHT_FLAGS", "ENEX_FLAGS", "FLASH", "ATTRACTOR",
    "COVER", "Fx2dError", "decode_entry", "encode_entry", "type_code", "type_name", "f32_json", "summary",
    "field_names", "SPECS",
]

FORMAT = "satk.fx2d/1"

TYPE_NAMES: dict[int, str] = {
    0: "light", 1: "particle", 2: "unknown2", 3: "attractor", 4: "sun_glare", 5: "interior", 6: "enex",
    7: "roadsign", 8: "trigger_point", 9: "cover_point", 10: "escalator",
}
TYPE_IDS: dict[str, int] = {v: k for k, v in TYPE_NAMES.items()}
#: Types the game's DFF reader understands (the others are skipped or break the reader).
GAME_DFF_TYPES = frozenset({0, 1, 3, 6, 7, 8, 9, 10})

LIGHT_FLAGS = ("check_obstacles", "fog1", "fog2", "without_corona", "only_long_distance", "at_day", "at_night",
               "blinking1", "only_from_below", "blinking2", "update_height_above_ground", "check_direction",
               "blinking3")
ENEX_FLAGS = ("unknown_interior", "unknown_pairing", "create_linked_pair", "reward_interior", "used_reward_entrance",
              "cars_and_aircraft", "bikes_and_motorcycles", "disable_on_foot", "accept_npc_group", "food_date_flag",
              "unknown_burglary", "disable_exit", "burglary_access", "entered_without_exit", "enable_access",
              "delete_enex")
FLASH = {i: n for i, n in enumerate((
    "default", "random", "random_when_wet", "anim_speed_4x", "anim_speed_2x", "anim_speed_1x", "unknown6",
    "traffic_light", "train_crossing", "unused9", "only_rain", "on5_off5", "on6_off4", "on4_off6"))}
ATTRACTOR = {-1: "undefined", 0: "atm", 1: "seat", 2: "stop", 3: "pizza", 4: "shelter", 5: "trigger_script",
             6: "look_at", 7: "scripted", 8: "park", 9: "step"}
COVER = {0: "low_cover", 1: "wall_to_left", 2: "wall_to_right"}
_LINES = {1: 1, 2: 2, 3: 3, 0: 4}          # layout bits 0-1 -> number of lines
_CHARS = {1: 2, 2: 4, 3: 8, 0: 16}         # layout bits 2-3 -> characters per line


class Fx2dError(ValueError):
    """A JSON entry that cannot be encoded; ``where`` is a JSON path (``effects[2].color``)."""

    def __init__(self, msg: str, where: str = "", did_you_mean: list[str] | None = None):
        super().__init__(f"{where}: {msg}" if where else msg)
        self.where = where
        self.did_you_mean = did_you_mean or []


# ============================================================================ floats


def f32_json(raw: bytes) -> float | str:
    """The shortest decimal that packs back to the same float32 bits (raw bits as hex for NaN/inf)."""
    (v,) = struct.unpack("<f", raw)
    if not math.isfinite(v):
        return f"0x{struct.unpack('<I', raw)[0]:08X}"
    for p in range(1, 10):
        s = float(f"{v:.{p}g}")
        try:
            if struct.pack("<f", s) == raw:
                return s
        except OverflowError:
            continue
    return v


def _f32_bytes(v: Any, where: str) -> bytes:
    if isinstance(v, str):
        t = v.strip().lower()
        if t.startswith("0x"):
            try:
                return struct.pack("<I", int(t, 16))
            except (ValueError, struct.error):
                pass
        raise Fx2dError(f"expected a number (or raw float bits '0x7FC00000'), got {v!r}", where)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise Fx2dError(f"expected a number, got {v!r}", where)
    try:
        return struct.pack("<f", float(v))
    except OverflowError:
        raise Fx2dError(f"{v!r} is out of the float32 range", where) from None


# ============================================================================ field layouts


class _F:
    """One JSON key of a type: ``kind`` decides the binary form at ``off``."""

    __slots__ = ("key", "kind", "off", "n", "names", "hi", "since")

    def __init__(self, key: str, kind: str, off: int, n: int = 0, names: Any = None, hi: int = -1, since: int = 0):
        self.key, self.kind, self.off, self.n, self.names, self.hi, self.since = key, kind, off, n, names, hi, since

    def present(self, size: int) -> bool:
        return size >= self.since and self.off < size


class _Spec:
    __slots__ = ("code", "name", "sizes", "fields", "defaults")

    def __init__(self, code: int, sizes: tuple[int, ...], fields: tuple[_F, ...], defaults: dict):
        self.code, self.name, self.sizes, self.fields, self.defaults = code, TYPE_NAMES[code], sizes, fields, defaults

    def keys(self) -> list[str]:
        out: list[str] = []
        for f in self.fields:
            out += ["lines", "chars", "color", "flags_extra"] if f.kind == "layout" else [f.key]
        return out


_INT = {"u8": ("<B", 0, 255), "i8": ("<b", -128, 127), "u16": ("<H", 0, 0xFFFF), "i16": ("<h", -0x8000, 0x7FFF),
        "i32": ("<i", -0x80000000, 0x7FFFFFFF), "bool8": ("<B", 0, 255)}

#: Missing fields of a hand-written light take the values of the street lamp of ``lamppost1`` (model 1297).
_LIGHT_DEFAULTS = {"color": [249, 145, 34, 200], "corona": "coronastar", "shadow": "shad_exp", "corona_size": 2.5,
                   "far_clip": 100.0, "range": 12.0, "shadow_size": 8.0, "shadow_mult": 40, "shadow_z": 0,
                   "flash": "default", "reflection": True, "flare": 0,
                   "flags": ["fog1", "at_night", "update_height_above_ground"], "look_dir": [0, 0, 100]}

SPECS: dict[int, _Spec] = {s.code: s for s in (
    _Spec(0, (80, 76), (
        _F("color", "rgba", 0), _F("corona", "cstr", 25, 24), _F("shadow", "cstr", 49, 24),
        _F("corona_size", "f32", 12), _F("far_clip", "f32", 4), _F("range", "f32", 8), _F("shadow_size", "f32", 16),
        _F("shadow_mult", "u8", 23), _F("shadow_z", "u8", 73), _F("flash", "enum8", 20, names=FLASH),
        _F("reflection", "bool8", 21), _F("flare", "u8", 22), _F("flags", "flags", 24, names=LIGHT_FLAGS, hi=74),
        _F("look_dir", "i8vec", 75, 3, since=80)), _LIGHT_DEFAULTS),
    _Spec(1, (24,), (_F("name", "cstr", 0, 24),), {}),
    _Spec(3, (56,), (
        _F("atype", "enum8s", 0, names=ATTRACTOR), _F("queue_dir", "vec", 4, 3), _F("use_dir", "vec", 16, 3),
        _F("fwd_dir", "vec", 28, 3), _F("script", "cstr", 40, 8), _F("probability", "i32", 48),
        _F("unk1", "u8", 52), _F("flags", "u8", 54)),
        {"queue_dir": [0.0, 1.0, 0.0], "use_dir": [0.0, 1.0, 0.0], "fwd_dir": [0.0, 1.0, 0.0], "script": "none",
         "probability": 75, "unk1": 0, "flags": 0}),
    _Spec(4, (0,), (), {}),
    _Spec(6, (44, 40), (
        _F("name", "cstr", 32, 8), _F("enter_angle", "f32", 0), _F("radius", "vec", 4, 2), _F("exit", "vec", 12, 3),
        _F("exit_angle", "f32", 24), _F("interior", "i16", 28), _F("flags", "flags", 30, names=ENEX_FLAGS, hi=42),
        _F("sky", "u8", 31), _F("time_on", "u8", 40, since=44), _F("time_off", "u8", 41, since=44)),
        {"enter_angle": 0.0, "radius": [2.0, 2.0], "exit_angle": 0.0, "interior": 0, "flags": [], "sky": 0,
         "time_on": 0, "time_off": 24}),
    _Spec(7, (88,), (
        _F("text", "rstrs", 22, 4), _F("size", "vec", 0, 2), _F("rot", "vec", 8, 3), _F("layout", "layout", 20)),
        {"rot": [0.0, 0.0, 0.0], "lines": 4, "chars": 16, "color": 0, "flags_extra": 0}),
    _Spec(8, (4,), (_F("id", "i32", 0),), {}),
    _Spec(9, (12,), (_F("dir", "vec", 0, 2), _F("usage", "enum8", 8, names=COVER)), {"usage": "low_cover"}),
    _Spec(10, (40,), (
        _F("bottom", "vec", 0, 3), _F("top", "vec", 12, 3), _F("end", "vec", 24, 3), _F("up", "bool8", 36)),
        {"up": True}),
)}


def field_names(code: int) -> list[str]:
    """JSON keys of a type (without ``type``/``pos``/``bytes``/``keep``)."""
    s = SPECS.get(code)
    return s.keys() if s else []


def type_name(code: int) -> str | int:
    return TYPE_NAMES.get(code, code)


def type_code(v: Any, where: str = "type") -> int:
    """``"light"`` / ``0`` -> 0 (``Fx2dError`` with suggestions otherwise)."""
    if isinstance(v, bool):
        raise Fx2dError(f"bad type {v!r}", where)
    if isinstance(v, int):
        if 0 <= v <= 0xFFFFFFFF:
            return v
        raise Fx2dError(f"type {v} out of range", where)
    if isinstance(v, str):
        k = v.strip().lower()
        if k in TYPE_IDS:
            return TYPE_IDS[k]
        if k.isdigit():
            return int(k)
        raise Fx2dError(f"unknown type {v!r}", where, difflib.get_close_matches(k, list(TYPE_IDS), 3, 0.5)
                        or sorted(TYPE_IDS))
    raise Fx2dError(f"bad type {v!r}", where)


# ============================================================================ decode


def _cstr_decode(raw: bytes, fill: bytes) -> tuple[str, bytes | None]:
    """``(text, kept tail)``: C string (``fill`` NUL) or road-sign line (``fill`` ``_``, trailing ``_`` = spaces)."""
    z = raw.find(b"\0")
    head = raw if z < 0 else raw[:z]
    if fill == b"_":
        head = head.rstrip(b"_")
    tail = raw[len(head):]
    return head.decode("latin-1"), (None if tail == fill * len(tail) else tail)


def _vec(data: bytes, off: int, n: int) -> list:
    return [f32_json(data[off + 4 * i:off + 4 * i + 4]) for i in range(n)]


def _enum_out(v: int, names: dict) -> str | int:
    return names.get(v, v)


def _flags_out(v: int, names: tuple) -> list:
    return [names[i] if i < len(names) else f"bit{i}" for i in range(16) if v >> i & 1]


def decode_entry(pos: bytes, code: int, data: bytes, *, keep: bool = True) -> dict:
    """One 2dEffect entry -> JSON object (``pos`` = the 12 position bytes)."""
    d: dict[str, Any] = {"type": type_name(code), "pos": _vec(pos, 0, 3)}
    spec = SPECS.get(code)
    size = len(data)
    if spec is None or size not in spec.sizes:
        d["data"] = data.hex()
        return d
    covered = bytearray(size)
    kept: dict[str, str] = {}

    def cover(a: int, n: int) -> None:
        covered[a:a + n] = b"\1" * n

    for f in spec.fields:
        if not f.present(size):
            continue
        k, o = f.kind, f.off
        if k == "f32":
            d[f.key] = f32_json(data[o:o + 4])
            cover(o, 4)
        elif k == "vec":
            d[f.key] = _vec(data, o, f.n)
            cover(o, 4 * f.n)
        elif k in _INT:
            fmt = _INT[k][0]
            (v,) = struct.unpack_from(fmt, data, o)
            d[f.key] = bool(v) if k == "bool8" and v in (0, 1) else v
            cover(o, struct.calcsize(fmt))
        elif k in ("enum8", "enum8s"):
            (v,) = struct.unpack_from("<b" if k == "enum8s" else "<B", data, o)
            d[f.key] = _enum_out(v, f.names)
            cover(o, 1)
        elif k == "rgba":
            d[f.key] = list(data[o:o + 4])
            cover(o, 4)
        elif k == "i8vec":
            d[f.key] = list(struct.unpack_from(f"<{f.n}b", data, o))
            cover(o, f.n)
        elif k == "cstr":
            text, tail = _cstr_decode(data[o:o + f.n], b"\0")
            d[f.key] = text
            if tail is not None:
                kept[f.key] = tail.hex()
            cover(o, f.n)
        elif k == "rstrs":
            lines = []
            for i in range(f.n):
                text, tail = _cstr_decode(data[o + 16 * i:o + 16 * i + 16], b"_")
                lines.append(text)
                if tail is not None:
                    kept[f"{f.key}{i}"] = tail.hex()
            d[f.key] = lines
            cover(o, 16 * f.n)
        elif k == "flags":
            v = data[o]
            cover(o, 1)
            if 0 <= f.hi < size:
                v |= data[f.hi] << 8
                cover(f.hi, 1)
            d[f.key] = _flags_out(v, f.names)
        elif k == "layout":
            (v,) = struct.unpack_from("<H", data, o)
            d["lines"] = _LINES[v & 3]
            d["chars"] = _CHARS[v >> 2 & 3]
            d["color"] = v >> 4 & 3
            if v >> 6:
                d["flags_extra"] = v >> 6
            cover(o, 2)
    if size != spec.sizes[0]:
        d["bytes"] = size
    pad = bytes(b for b, c in zip(data, covered) if not c)
    if any(pad):
        kept["pad"] = pad.hex()
    if kept and keep:
        d["keep"] = kept
    return d


# ============================================================================ encode


def _int(v: Any, kind: str, where: str) -> int:
    _fmt, lo, hi = _INT[kind]
    if kind == "bool8" and isinstance(v, bool):
        return int(v)
    if isinstance(v, bool) or not isinstance(v, int):
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        else:
            raise Fx2dError(f"expected an integer, got {v!r}", where)
    if not lo <= v <= hi:
        raise Fx2dError(f"{v} is out of range {lo}..{hi}", where)
    return v


def _enum_in(v: Any, names: dict, signed: bool, where: str) -> int:
    if isinstance(v, str):
        k = v.strip().lower()
        for num, n in names.items():
            if n == k:
                return num
        raise Fx2dError(f"unknown value {v!r}", where, difflib.get_close_matches(k, list(names.values()), 3, 0.5)
                        or list(names.values()))
    return _int(v, "i8" if signed else "u8", where)


def _flags_in(v: Any, names: tuple, where: str) -> int:
    if isinstance(v, int) and not isinstance(v, bool):
        if 0 <= v <= 0xFFFF:
            return v
        raise Fx2dError(f"flags {v} out of range 0..65535", where)
    if not isinstance(v, list):
        raise Fx2dError(f"expected a list of flag names, got {v!r}", where)
    out = 0
    for i, n in enumerate(v):
        k = str(n).strip().lower()
        if k in names:
            out |= 1 << names.index(k)
        elif k.startswith("bit") and k[3:].isdigit() and int(k[3:]) < 16:
            out |= 1 << int(k[3:])
        else:
            raise Fx2dError(f"unknown flag {n!r}", f"{where}[{i}]",
                            difflib.get_close_matches(k, list(names), 3, 0.5) or list(names))
    return out


def _numbers(v: Any, n: int, where: str) -> list:
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise Fx2dError(f"expected a list of {n} numbers, got {v!r}", where)
    return list(v)


def _str_bytes(v: Any, n: int, where: str) -> bytes:
    if not isinstance(v, str):
        raise Fx2dError(f"expected a string, got {v!r}", where)
    try:
        b = v.encode("latin-1")
    except UnicodeEncodeError:
        raise Fx2dError(f"{v!r} has characters outside latin-1", where) from None
    if len(b) > n:
        raise Fx2dError(f"{v!r} is longer than {n} bytes", where)
    return b


def _kept(keep: dict, key: str, room: int, where: str) -> bytes | None:
    h = keep.get(key)
    if h is None:
        return None
    try:
        b = bytes.fromhex(h)
    except (TypeError, ValueError):
        raise Fx2dError(f"keep.{key} must be hex, got {h!r}", where) from None
    return b if len(b) == room else None


def encode_entry(e: Any, where: str = "effect") -> tuple[bytes, int, bytes]:
    """JSON object -> ``(pos 12 bytes, type, data)``. Missing fields take the type's defaults (see the docs)."""
    if not isinstance(e, dict):
        raise Fx2dError(f"expected an object, got {type(e).__name__}", where)
    if "type" not in e:
        raise Fx2dError("missing 'type'", where, sorted(TYPE_IDS))
    code = type_code(e["type"], f"{where}.type")
    if "pos" not in e:
        raise Fx2dError("missing 'pos' ([x, y, z] relative to the model)", where)
    pos = b"".join(_f32_bytes(x, f"{where}.pos[{i}]") for i, x in enumerate(_numbers(e["pos"], 3, f"{where}.pos")))
    spec = SPECS.get(code)
    if "data" in e or spec is None:
        extra = sorted(set(e) - {"type", "pos", "data"})
        if extra:
            raise Fx2dError(f"a raw entry has only type, pos and data; got {extra}", where)
        if "data" not in e:
            raise Fx2dError(f"type {e['type']!r} has no known layout: give its bytes as 'data' (hex)", where)
        try:
            return pos, code, bytes.fromhex(str(e["data"]))
        except ValueError:
            raise Fx2dError("data must be hex", f"{where}.data") from None
    allowed = set(spec.keys()) | {"type", "pos", "bytes", "keep"}
    for k in e:
        if k not in allowed:
            raise Fx2dError(f"unknown key {k!r} for a {spec.name}", where,
                            difflib.get_close_matches(k, sorted(allowed), 3, 0.5) or sorted(allowed - {"keep"}))
    size = e.get("bytes", spec.sizes[0])
    if isinstance(size, bool) or size not in spec.sizes:
        raise Fx2dError(f"a {spec.name} has {' or '.join(map(str, spec.sizes))} data bytes, got {size!r}",
                        f"{where}.bytes")
    keep = e.get("keep") or {}
    if not isinstance(keep, dict):
        raise Fx2dError("keep must be an object of hex strings", f"{where}.keep")
    buf = bytearray(size)
    covered = bytearray(size)

    def put(o: int, b: bytes) -> None:
        buf[o:o + len(b)] = b
        covered[o:o + len(b)] = b"\1" * len(b)

    def get(key: str) -> Any:
        if key in e:
            return e[key]
        if key in spec.defaults:
            return spec.defaults[key]
        raise Fx2dError(f"missing {key!r} (a {spec.name} needs it)", where)

    for f in spec.fields:
        w = f"{where}.{f.key}"
        k, o = f.kind, f.off
        if not f.present(size):
            if f.key in e:
                raise Fx2dError(f"needs \"bytes\": {f.since}", w)
            continue
        if k == "f32":
            put(o, _f32_bytes(get(f.key), w))
        elif k == "vec":
            put(o, b"".join(_f32_bytes(x, f"{w}[{i}]") for i, x in enumerate(_numbers(get(f.key), f.n, w))))
        elif k in _INT:
            put(o, struct.pack(_INT[k][0], _int(get(f.key), k, w)))
        elif k in ("enum8", "enum8s"):
            put(o, struct.pack("<b" if k == "enum8s" else "<B", _enum_in(get(f.key), f.names, k == "enum8s", w)))
        elif k == "rgba":
            put(o, bytes(_int(x, "u8", f"{w}[{i}]") for i, x in enumerate(_numbers(get(f.key), 4, w))))
        elif k == "i8vec":
            put(o, struct.pack(f"<{f.n}b", *(_int(x, "i8", f"{w}[{i}]")
                                              for i, x in enumerate(_numbers(get(f.key), f.n, w)))))
        elif k == "cstr":
            b = _str_bytes(get(f.key), f.n, w)
            tail = _kept(keep, f.key, f.n - len(b), w)
            put(o, b + (tail if tail is not None else b"\0" * (f.n - len(b))))
        elif k == "rstrs":
            lines = get(f.key)
            if not isinstance(lines, list) or len(lines) > f.n:
                raise Fx2dError(f"expected a list of up to {f.n} strings", w)
            for i in range(f.n):
                b = _str_bytes(lines[i] if i < len(lines) else "", 16, f"{w}[{i}]")
                tail = _kept(keep, f"{f.key}{i}", 16 - len(b), w)
                put(o + 16 * i, b + (tail if tail is not None else b"_" * (16 - len(b))))
        elif k == "flags":
            v = _flags_in(get(f.key), f.names, w)
            if not 0 <= f.hi < size and v >> 8:
                raise Fx2dError(f"flags above bit 7 need \"bytes\": {spec.sizes[0]}", w)
            put(o, bytes([v & 0xFF]))
            if 0 <= f.hi < size:
                put(f.hi, bytes([v >> 8]))
        elif k == "layout":
            lines, chars = get("lines"), get("chars")
            rl = {v: b for b, v in _LINES.items()}
            rc = {v: b for b, v in _CHARS.items()}
            if isinstance(lines, bool) or not isinstance(lines, int) or lines not in rl:
                raise Fx2dError(f"lines must be 1..4, got {lines!r}", f"{where}.lines")
            if isinstance(chars, bool) or not isinstance(chars, int) or chars not in rc:
                raise Fx2dError(f"chars must be 2, 4, 8 or 16, got {chars!r}", f"{where}.chars")
            color = _int(get("color"), "u8", f"{where}.color")
            if color > 3:
                raise Fx2dError(f"color must be 0..3, got {color}", f"{where}.color")
            extra = _int(get("flags_extra"), "u16", f"{where}.flags_extra")
            if extra >> 10:
                raise Fx2dError(f"flags_extra must be below 1024, got {extra}", f"{where}.flags_extra")
            put(o, struct.pack("<H", rl[lines] | rc[chars] << 2 | color << 4 | extra << 6))
    holes = [i for i in range(size) if not covered[i]]
    pad = _kept(keep, "pad", len(holes), where)
    if pad is not None:
        for i, b in zip(holes, pad):
            buf[i] = b
    for k in keep:
        if k != "pad" and k not in allowed and not (k.startswith("text") and k[4:].isdigit()):
            raise Fx2dError(f"unknown keep key {k!r}", f"{where}.keep")
    return pos, code, bytes(buf)


# ============================================================================ one-line summaries


def _num(v: Any) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def summary(e: dict) -> str:
    """A short human/agent-readable description of a decoded entry (for tables)."""
    t = e.get("type")
    if "data" in e:
        return f"raw {len(e['data']) // 2} B"
    if t == "light":
        when = [f for f in e.get("flags", []) if f in ("at_day", "at_night")]
        s = (f"{e['corona'] or '-'} rgba {','.join(map(str, e['color']))} size {_num(e['corona_size'])} "
             f"far {_num(e['far_clip'])} range {_num(e['range'])}")
        if e.get("shadow_size"):
            s += f" shadow {_num(e['shadow_size'])}"
        if e.get("flash") not in (None, "default"):
            s += f" {e['flash']}"
        return s + (f" {'+'.join(when)}" if when else " never")
    if t == "particle":
        return e["name"] or "(empty)"
    if t == "attractor":
        return f"{e['atype']} script={e['script']} p={e['probability']}"
    if t == "enex":
        s = f"{e['name']} r={','.join(map(_num, e['radius']))}"
        if "time_on" in e and (e["time_on"], e["time_off"]) not in ((0, 24), (0, 0)):
            s += f" {e['time_on']}-{e['time_off']}h"
        return s
    if t == "roadsign":
        lines = (x.replace("_", " ").strip() for x in e["text"][:e["lines"]])
        return " | ".join(x for x in lines if x) or "(blank)"
    if t == "trigger_point":
        return f"id {e['id']}"
    if t == "cover_point":
        d = ",".join(_num(round(x, 2) + 0.0) if isinstance(x, float) else str(x) for x in e["dir"])
        return f"{e['usage']} dir {d}"
    if t == "escalator":
        return "up" if e["up"] is True else "down" if e["up"] is False else f"direction {e['up']}"
    return ""
