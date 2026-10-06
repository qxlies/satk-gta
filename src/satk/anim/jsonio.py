"""The editable JSON form of IFP animations (``"satk": "anim/1"``). Stdlib only.

Shape::

    {"satk": "anim/1", "format": "ANP3", "pack": "ped",
     "anims": [{"name": "WALK_civi", "index": 262, "compressed": true, "duration": 1.133,
                "bones": [{"name": "Root", "tag": 0, "trans": true,
                           "keys": [[0.0, 0.044922, -0.018066, 0.685791, 0.725830, -0.018555, 0.0, -0.068359],
                                    ...]},
                          {"name": " Pelvis", "tag": 1, "keys": [[0.0, -0.498291, ...], ...]}]}]}

* a key is ``[t, qx, qy, qz, qw]`` (rotation only), ``[t, qx, qy, qz, qw, tx, ty, tz]`` (with translation) or,
  ANPK only, ``+ [sx, sy, sz]``; ``t`` = absolute time in seconds, ``q`` = the engine's quaternion, metres;
* ``tag`` = bone id (``satk anim skeleton``), -1 = matched by name; ``trans`` defaults to the key width;
* ``compressed`` (default true, the SA style): values are quantized when written (rotation 1/4096, time 1/60 s,
  translation 1/1024 m, +-32 m); compressed values are printed rounded to 6 decimals, which re-quantize exactly;
  float values print the shortest decimal that gives the same float32;
* ``"f32:7fc00000"`` = the exact bits of a float32 key value that is NaN or infinite (a few vanilla cutscene
  keys are);
* ``index`` restores source animation order when combining extracts; ``duration`` and ``source`` are informational;
* ``*_raw``/``extra``/``kf_tail``/``name_pad`` (hex) keep byte details of the source so a full extract writes
  back bit-exact; editors can drop them.
"""

from __future__ import annotations

import json
import struct
from typing import Any

from .ifp import FORMATS, Q_SCALE, T_SCALE, TIME_SCALE, Anim, Ifp, Seq, quantize_seq

__all__ = ["SCHEMA", "ifp_to_json", "anim_to_json", "json_to_ifp", "dumps", "JsonError"]

SCHEMA = "anim/1"
_F32 = struct.Struct("<f")


class JsonError(ValueError):
    """A JSON animation document that cannot be turned into an IFP (``where`` = JSON path)."""

    def __init__(self, where: str, msg: str):
        self.where = where
        super().__init__(f"{where}: {msg}")


def _f32(v: float) -> float | str:
    """A short decimal that packs to the same float32 as ``v`` (8 significant digits, else 9: always exact);
    NaN and infinities (some vanilla cutscene keys) as ``"f32:<8 hex digits>"`` so their bits survive."""
    v = float(v)
    if v != v or v in (float("inf"), float("-inf")):
        return "f32:" + _F32.pack(v)[::-1].hex()
    r = float("%.8g" % v)
    return r if _F32.pack(r) == _F32.pack(v) else float("%.9g" % v)


def _r6(v: float) -> float:
    r = round(v, 6)
    return 0.0 if r == 0 else r


def _seq_json(s: Seq, anim_compressed: bool) -> dict:
    d: dict[str, Any] = {"name": s.name, "tag": int(s.tag)}
    if s.trans:
        d["trans"] = True
    if s.compressed != anim_compressed:
        d["compressed"] = s.compressed
    if s.scale:
        d["scale"] = True
    for k in ("raw_name", "extra", "kf_tail"):
        v = getattr(s, k)
        if v is not None:
            d["name_raw" if k == "raw_name" else k] = v.hex()
    keys = []
    if s.compressed:
        n = 8 if s.trans else 5
        for k in s.keys:
            row = [_r6(k[4] / TIME_SCALE)] + [_r6(c / Q_SCALE) for c in k[:4]]
            if n == 8:
                row += [_r6(c / T_SCALE) for c in k[5:8]]
            keys.append(row)
    else:
        for k in s.keys:
            keys.append([_f32(k[4])] + [_f32(c) for c in k[:4]] + [_f32(c) for c in k[5:]])
    d["keys"] = keys
    return d


def anim_to_json(a: Anim, index: int | None = None) -> dict:
    """One animation as a JSON object."""
    comp = a.compressed
    d: dict[str, Any] = {"name": a.name}
    if index is not None:
        d["index"] = index
    d["compressed"] = comp
    if a.flags != (1 if comp else 0):
        d["flags"] = a.flags
    d["duration"] = round(a.duration, 4)
    if a.raw_name is not None:
        d["name_raw"] = a.raw_name.hex()
    if a.info_extra is not None:
        d["info_raw"] = a.info_extra.hex()
    if a.name_pad is not None:
        d["name_pad"] = a.name_pad.hex()
    d["bones"] = [_seq_json(s, comp) for s in a.seqs]
    return d


def ifp_to_json(ifp: Ifp, anims: list[Anim] | None = None, source: str | None = None) -> dict:
    """An IFP (or a selection of its animations) as a JSON document; ``index`` = position in ``ifp``."""
    pos = {id(a): i for i, a in enumerate(ifp.anims)}
    doc: dict[str, Any] = {"satk": SCHEMA, "format": ifp.format, "pack": ifp.pack}
    if source:
        doc["source"] = source
    if ifp.raw_pack is not None:
        doc["pack_raw"] = ifp.raw_pack.hex()
    doc["anims"] = [anim_to_json(a, pos.get(id(a))) for a in (ifp.anims if anims is None else anims)]
    return doc


# --------------------------------------------------------------------------- JSON -> IFP


def _hex(d: dict, key: str, where: str) -> bytes | None:
    v = d.get(key)
    if v is None:
        return None
    try:
        return bytes.fromhex(str(v))
    except ValueError:
        raise JsonError(f"{where}.{key}", "not a hex string") from None


def _num(v: Any, where: str) -> float:
    if isinstance(v, str) and v.startswith("f32:") and len(v) == 12:
        try:
            return _F32.unpack(bytes.fromhex(v[4:])[::-1])[0]
        except ValueError:
            pass
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise JsonError(where, f"expected a number, got {v!r}")
    try:
        return float(v)
    except OverflowError:
        raise JsonError(where, "number is outside the float32 range") from None


def _bool(d: dict, key: str, default: bool, where: str) -> bool:
    v = d.get(key, default)
    if not isinstance(v, bool):
        raise JsonError(f"{where}.{key}", f"expected true or false, got {v!r}")
    return v


def _seq_from(d: Any, where: str, anim_comp: bool) -> Seq:
    if not isinstance(d, dict):
        raise JsonError(where, "a bone must be an object with name, tag, keys")
    name = d.get("name")
    if not isinstance(name, str):
        raise JsonError(f"{where}.name", "missing bone name")
    tag = d.get("tag", -1)
    if isinstance(tag, bool) or not isinstance(tag, int) or not -(1 << 31) <= tag < (1 << 31):
        raise JsonError(f"{where}.tag", f"expected an integer bone id (-1 = by name), got {tag!r}")
    raw_keys = d.get("keys", [])
    if not isinstance(raw_keys, list):
        raise JsonError(f"{where}.keys", "expected a list of [t, qx, qy, qz, qw(, tx, ty, tz)]")
    scale = _bool(d, "scale", False, where)
    widths = {len(k) for k in raw_keys if isinstance(k, list)}
    trans = _bool(d, "trans", bool(widths) and min(widths) >= 8, where) or scale
    want = 11 if scale else (8 if trans else 5)
    comp = _bool(d, "compressed", anim_comp, where)
    keys = []
    for i, k in enumerate(raw_keys):
        w = f"{where}.keys[{i}]"
        if not isinstance(k, list) or len(k) != want:
            raise JsonError(w, f"expected {want} numbers [t, qx, qy, qz, qw"
                               + (", tx, ty, tz" if trans else "") + (", sx, sy, sz" if scale else "") + f"], got {k!r}")
        v = [_num(x, w) for x in k]
        keys.append(tuple(v[1:5]) + (v[0],) + tuple(v[5:]))
    s = Seq(name, int(tag), trans, False, keys, scale=scale, raw_name=_hex(d, "name_raw", where),
            extra=_hex(d, "extra", where), kf_tail=_hex(d, "kf_tail", where))
    if comp:
        if scale:
            raise JsonError(where, "scale keys exist only in uncompressed ANPK files; set \"compressed\": false")
        try:
            s = quantize_seq(s)
        except ValueError as e:
            raise JsonError(where, f"{e}; write this animation uncompressed (\"compressed\": false)") from None
    return s


def json_to_ifp(doc: Any, *, pack: str | None = None) -> Ifp:
    """Build an :class:`Ifp` from a JSON document (``{"anims": [...]}``, a single animation object, or a list
    of either), in the format the document declares (default ANP3; :func:`satk.anim.ifp.convert_anim` turns it
    into another). ``JsonError`` names the first bad value by its JSON path."""
    docs = doc if isinstance(doc, list) else [doc]
    anims: list[tuple[int, int, Anim]] = []
    fmt0 = pack0 = raw_pack = None
    for di, d in enumerate(docs):
        where = f"[{di}]" if isinstance(doc, list) else "$"
        if not isinstance(d, dict):
            raise JsonError(where, "expected an object")
        if "satk" in d and d["satk"] != SCHEMA:
            raise JsonError(f"{where}.satk", f"expected {SCHEMA!r}, got {d['satk']!r}")
        if "bones" in d and "anims" not in d:
            items = [d]
        else:
            items = d.get("anims")
            if not isinstance(items, list):
                raise JsonError(f"{where}.anims", "missing list of animations (or a single animation with bones)")
            fmt0 = fmt0 or d.get("format")
            pack0 = pack0 or d.get("pack")
            if raw_pack is None:
                raw_pack = _hex(d, "pack_raw", where)
        for ai, a in enumerate(items):
            aw = f"{where}.anims[{ai}]" if items is not d and "anims" in d else where
            if not isinstance(a, dict) or not isinstance(a.get("name"), str):
                raise JsonError(aw, "an animation needs a name and bones")
            bones = a.get("bones")
            if not isinstance(bones, list):
                raise JsonError(f"{aw}.bones", "expected a list of bones")
            comp = _bool(a, "compressed", True, aw)
            seqs = [_seq_from(b, f"{aw}.bones[{bi}]", comp) for bi, b in enumerate(bones)]
            any_comp = any(s.compressed for s in seqs)
            flags = a.get("flags")
            if flags is None:
                flags = 1 if any_comp else 0
            if isinstance(flags, bool) or not isinstance(flags, int) or not 0 <= flags < (1 << 32):
                raise JsonError(f"{aw}.flags", f"expected a uint32 flags word, got {flags!r}")
            an = Anim(a["name"], seqs, int(flags), _hex(a, "name_raw", aw), _hex(a, "info_raw", aw),
                      _hex(a, "name_pad", aw))
            idx = a.get("index")
            anims.append((idx if isinstance(idx, int) and not isinstance(idx, bool) else 1 << 30, len(anims), an))
    fmt = str(fmt0 or "ANP3").upper()
    if fmt not in FORMATS:
        raise JsonError("$.format", f"unknown format {fmt0!r} (ANP3, ANP2, ANPK)")
    anims.sort(key=lambda t: (t[0], t[1]))
    name = pack or pack0 or "custom"
    return Ifp(fmt, str(name), [a for _i, _j, a in anims], raw_pack if (pack is None or pack == pack0) else None)


def _open(d: dict, key: str) -> str:
    """``{"a": 1, "<key>": [`` for the fields of ``d`` other than ``key``."""
    head = json.dumps({k: v for k, v in d.items() if k != key}, ensure_ascii=False)
    return head[:-1] + (", " if len(head) > 2 else "") + json.dumps(key) + ": ["


def dumps(doc: dict) -> str:
    """The document as JSON text with one bone header and one key frame per line (diff- and editor-friendly)."""
    anims = []
    for a in doc.get("anims", []):
        bones = []
        for b in a.get("bones", []):
            keys = ",\n   ".join(json.dumps(k) for k in b.get("keys", []))
            bones.append(" " + _open(b, "keys") + ("\n   " + keys if keys else "") + "]}")
        anims.append(_open(a, "bones") + ("\n " + ",\n ".join(bones) if bones else "") + "]}")
    return _open(doc, "anims") + ("\n" + ",\n".join(anims) if anims else "") + "]}\n"
