"""DFF <-> fx2d JSON documents (``satk.fx2d.doc``). Stdlib only.

A document lists the geometries that carry a 2dEffect chunk::

    {"format": "satk.fx2d/1", "source": "models/gta3.img/lamppost1.dff", "geometry_count": 1,
     "geometries": [{"geometry": 0, "frame": "lamppost1", "effects": [{"type": "light", "pos": [...], ...}]}]}

:func:`read_doc` builds it from DFF bytes; :func:`apply_doc` writes effects into a DFF on the lossless chunk tree of
:mod:`satk.rw` (untouched chunks stay byte-identical, only the sizes on the path to the root change). The 2dEffect
chunk of a geometry that had none is appended to the end of its Extension, where Rockstar's files keep it.
"""

from __future__ import annotations

import difflib
import struct
from dataclasses import dataclass, field
from typing import Any

from ..rw import codecs as C
from ..rw.chunk import ATOMIC, EXTENSION, FRAMELIST, FRAMENAME, FX2D, GEOMETRY, GEOMLIST, STRUCT, Chunk
from ..rw.dff import DffDoc, GeomRef, PatchError
from .schema import FORMAT, Fx2dError, decode_entry, encode_entry

__all__ = ["Item", "ApplyReport", "parse_dff", "geometries", "frame_names", "fx_chunk", "read_doc", "normalize",
           "apply_doc", "spheres"]

_TOP_KEYS = ("format", "source", "geometry_count", "geometries", "geometry", "frame", "effects", "keep")


@dataclass
class Item:
    """Effects for one geometry (``geometry`` is ``None`` for a bare effect list: the caller picks it)."""

    geometry: int | None
    effects: list
    keep: dict | None = None
    frame: str | None = None
    where: str = "effects"


@dataclass
class ApplyReport:
    geometries: list[int] = field(default_factory=list)        # geometries whose effects were written
    removed: list[int] = field(default_factory=list)           # geometries whose 2dEffect chunk was removed
    created: list[int] = field(default_factory=list)           # geometries that got a new 2dEffect chunk
    entries: int = 0                                           # entries written (all touched geometries)
    warn: list[str] = field(default_factory=list)


def parse_dff(data: bytes) -> DffDoc:
    try:
        return DffDoc.parse(data)
    except PatchError as e:
        raise Fx2dError(f"not a readable DFF: {e}") from None


def geometries(doc: DffDoc) -> list[GeomRef]:
    """``DffDoc.geometries`` with its error as :class:`Fx2dError`."""
    try:
        return doc.geometries()
    except PatchError as e:
        raise Fx2dError(f"not a readable DFF: {e}") from None


def frame_names(doc: DffDoc) -> dict[int, str]:
    """Global geometry index (``DffDoc.geometries`` order) -> name of the first frame an atomic puts it on."""
    out: dict[int, str] = {}
    base = 0
    for cl in doc.clumps():
        names: list[str | None] = []
        fl = cl.child(FRAMELIST)
        if fl is not None and fl.kids is not None:
            for e in fl.children(EXTENSION):
                fn = e.child(FRAMENAME) if e.kids is not None else None
                names.append((fn.data or b"").split(b"\0")[0].decode("latin-1") if fn is not None else None)
        list_start, n = None, 0
        for k in cl.kids:
            if k.type == GEOMLIST and k.kids is not None:
                list_start = base + n
                n += len(k.children(GEOMETRY))
            elif k.type == ATOMIC and k.kids is not None:
                st = k.child(STRUCT)
                if st is None or len(st.data or b"") < 8:
                    continue
                fi, gi = struct.unpack_from("<II", st.data, 0)
                if k.child(GEOMETRY) is not None:            # geometry stored inside the atomic
                    g = base + n
                    n += 1
                elif list_start is not None:
                    g = list_start + gi
                else:
                    continue
                if fi < len(names) and names[fi] and g not in out:
                    out[g] = names[fi]
        base += n
    return out


def fx_chunk(g: GeomRef) -> Chunk | None:
    ext = g.node.child(EXTENSION)
    if ext is None or ext.kids is None:
        return None
    return ext.child(FX2D)


def _decode_raw(g: GeomRef, ch: Chunk) -> C.Fx2dData:
    try:
        return C.decode_2dfx(ch.data or b"")
    except C.CodecError as e:
        raise Fx2dError(f"geometry {g.index}: broken 2dEffect chunk ({e})") from None


def read_doc(data: bytes, *, source: str | None = None, keep: bool = True) -> dict:
    """The fx2d document of a DFF (geometries without a 2dEffect chunk are left out)."""
    doc = parse_dff(data)
    geos = geometries(doc)
    names = frame_names(doc)
    items = []
    for g in geos:
        ch = fx_chunk(g)
        if ch is None:
            continue
        raw = _decode_raw(g, ch)
        it: dict[str, Any] = {"geometry": g.index}
        if names.get(g.index):
            it["frame"] = names[g.index]
        it["effects"] = [decode_entry(p, t, d, keep=keep) for p, t, d in raw.entries]
        if raw.tail and keep:
            it["keep"] = {"tail": raw.tail.hex()}
        items.append(it)
    out: dict[str, Any] = {"format": FORMAT}
    if source:
        out["source"] = source
    out["geometry_count"] = len(geos)
    out["geometries"] = items
    return out


def spheres(data: bytes) -> dict[int, tuple[float, float, float, float]]:
    """Bounding sphere (x, y, z, r) of every non-native geometry (for the distance check)."""
    doc = parse_dff(data)
    out = {}
    for g in geometries(doc):
        try:
            gd = g.data()
        except (C.CodecError, ValueError):
            continue
        if gd.morphs and len(gd.morphs[0].sphere) == 16:
            out[g.index] = struct.unpack("<4f", gd.morphs[0].sphere)
    return out


def normalize(obj: Any) -> list[Item]:
    """Accept a document, ``{"effects": [...], "geometry": N}`` or a bare list of effects."""
    if isinstance(obj, list):
        return [Item(None, obj)]
    if not isinstance(obj, dict):
        raise Fx2dError(f"expected a JSON object or a list of effects, got {type(obj).__name__}")
    for k in obj:
        if k not in _TOP_KEYS:
            raise Fx2dError(f"unknown top-level key {k!r}", "", difflib.get_close_matches(k, _TOP_KEYS, 3, 0.5)
                            or list(_TOP_KEYS))
    fmt = obj.get("format")
    if fmt is not None and fmt != FORMAT:
        raise Fx2dError(f"unsupported format {fmt!r} (this satk writes and reads {FORMAT!r})", "format")
    if "geometries" in obj:
        if "effects" in obj:
            raise Fx2dError("give either 'geometries' or 'effects', not both")
        gs = obj["geometries"]
        if not isinstance(gs, list):
            raise Fx2dError("must be a list", "geometries")
        out = []
        for i, g in enumerate(gs):
            w = f"geometries[{i}]"
            if not isinstance(g, dict):
                raise Fx2dError("expected an object", w)
            bad = sorted(set(g) - {"geometry", "frame", "effects", "keep"})
            if bad:
                raise Fx2dError(f"unknown key(s) {bad}", w, ["geometry", "frame", "effects", "keep"])
            gi = g.get("geometry")
            if isinstance(gi, bool) or not isinstance(gi, int):
                raise Fx2dError("needs 'geometry' (the geometry index, 0 = first)", w)
            eff = g.get("effects")
            if not isinstance(eff, list):
                raise Fx2dError("needs 'effects' (a list)", w)
            out.append(Item(gi, eff, g.get("keep"), g.get("frame"), f"{w}.effects"))
        return out
    if "effects" in obj:
        eff = obj["effects"]
        if not isinstance(eff, list):
            raise Fx2dError("must be a list", "effects")
        gi = obj.get("geometry")
        if gi is not None and (isinstance(gi, bool) or not isinstance(gi, int)):
            raise Fx2dError("must be an integer", "geometry")
        return [Item(gi, eff, obj.get("keep"), obj.get("frame"))]
    raise Fx2dError("no 'geometries' and no 'effects' in the JSON", "", ["geometries", "effects"])


def _tail(keep: Any, where: str) -> bytes:
    if not keep:
        return b""
    if not isinstance(keep, dict) or set(keep) - {"tail"}:
        raise Fx2dError("geometry keep has only 'tail' (hex)", where)
    try:
        return bytes.fromhex(str(keep.get("tail", "")))
    except ValueError:
        raise Fx2dError("keep.tail must be hex", where) from None


def apply_doc(data: bytes, items: list[Item], *, mode: str = "replace", geometry: int | None = None,
              with_tail: bool = False) -> tuple[bytes, ApplyReport]:
    """Write ``items`` into the DFF ``data``; returns the new DFF bytes and a report.

    ``mode="replace"``: the listed geometries get exactly these effects (an empty list removes the chunk);
    ``mode="add"``: the effects are appended to the existing ones. ``geometry`` is the target of items
    without a geometry index (default 0). The bytes after the last RW chunk (IMG sector padding) are dropped
    unless ``with_tail``.
    """
    if mode not in ("replace", "add"):
        raise Fx2dError(f"mode must be replace or add, got {mode!r}")
    doc = parse_dff(data)
    geos = geometries(doc)
    names = frame_names(doc)
    rep = ApplyReport()
    seen: dict[int, str] = {}
    for it in items:
        gi = it.geometry if it.geometry is not None else (geometry if geometry is not None else 0)
        if not 0 <= gi < len(geos):
            raise Fx2dError(f"geometry {gi} does not exist: the DFF has {len(geos)} "
                            f"(0..{len(geos) - 1})", it.where)
        if gi in seen:
            raise Fx2dError(f"geometry {gi} is listed twice ({seen[gi]})", it.where)
        seen[gi] = it.where
        g = geos[gi]
        if it.frame and names.get(gi) and str(it.frame).lower() != names[gi].lower():
            rep.warn.append(f"FRAME_MISMATCH: {it.where}: the JSON was made for frame {it.frame!r}, "
                            f"geometry {gi} is on {names[gi]!r}")
        encoded = [encode_entry(e, f"{it.where}[{j}]") for j, e in enumerate(it.effects)]
        ch = fx_chunk(g)
        old = _decode_raw(g, ch) if ch is not None else None
        if mode == "add" and old is not None:
            entries, tail = old.entries + encoded, old.tail
        else:
            entries, tail = encoded, _tail(it.keep, it.where.rsplit(".", 1)[0])
        if not entries and not tail:
            if ch is not None and (old.entries or old.tail):
                ext = g.node.child(EXTENSION)
                ext.kids.remove(ch)
                rep.removed.append(gi)
            continue
        payload = C.encode_2dfx(C.Fx2dData(entries, tail))
        if ch is None:
            ext = g.ext(create=True)
            if ext.kids is None:
                raise Fx2dError(f"geometry {gi}: its Extension chunk cannot be parsed, so no 2dEffect can be added")
            ext.kids.append(Chunk(FX2D, ext.libid, data=payload))
            rep.created.append(gi)
        else:
            ch.data = payload
        rep.geometries.append(gi)
        rep.entries += len(entries)
    return doc.to_bytes(with_tail=with_tail), rep
