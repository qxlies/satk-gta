"""MTA ``.map`` files (XML, EDF ``editor_main``; report 23 §4): reader and writer.

Standard attributes of ``<object>``: ``id model posX posY posZ rotX rotY rotZ interior dimension alpha
scale doublesided collisions breakable frozen`` (MTA server ``CObject::ReadSpecialData``:
``dimension="-1"`` = all dimensions; ``interior`` is a byte). ``<removeWorldObject>``: ``model lodModel
radius interior posX posY posZ`` (``interior="-1"`` = all).

satk extensions (vanilla MTA keeps unknown attributes as element data and unknown child elements as
inert elements; report 23 §4 proposes exactly this for materials):

* ``<material slot model txd tex color/>`` and ``<materialText slot text size font fontSize bold color
  back align/>`` children of ``<object>`` - SA-MP ``Set(Dynamic)ObjectMaterial(Text)``;
* ``allInteriors="true"`` - the SA-MP object was in every interior (``-1``); MTA has no such
  thing, ``interior="0"`` is written;
* ``drawDistance``, ``streamDistance`` - SA-MP per-instance draw distance and streamer distance;
* ``lodIndex``, ``iplFlags`` - IPL LOD link (index of another object of this map) and instance flags.

The reader uses expat without namespace processing (MTA files use ``edf:definitions`` without an
``xmlns``) and refuses DOCTYPE/entity declarations.
"""

from __future__ import annotations

import math
import re
import xml.parsers.expat
from xml.sax.saxutils import quoteattr

from .scene import Material, MaterialText, MapObject, Removal, Scene, num

__all__ = ["read_mta", "write_mta", "MtaSyntaxError", "OTHER_ELEMENTS"]

#: Elements of editor maps that are not objects (counted in ``Scene.skipped``).
OTHER_ELEMENTS = ("vehicle", "pickup", "marker", "ped", "spawnpoint", "racepickup", "checkpoint", "blip",
                  "radararea", "water", "colshape")
_ID = re.compile(r"^(?:object|removeWorldObject) \((.+)\) \(\d+\)$")
_DECL = re.compile("^(?:" + chr(0xFEFF) + r")?\s*<\?xml[^>]*\?>")
_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}


class MtaSyntaxError(ValueError):
    """Malformed XML or a forbidden construct (DOCTYPE)."""

    def __init__(self, line: int, msg: str):
        super().__init__(f"line {line}: {msg}")
        self.line = line


class _Bad(Exception):
    pass


def _num(a: dict, key: str, default: float | None = None) -> float:
    v = a.get(key)
    if v is None or v.strip() == "":
        if default is None:
            raise _Bad(f"missing '{key}'")
        return default
    try:
        f = float(v)
    except ValueError:
        raise _Bad(f"'{key}' is not a number: {v!r}") from None
    if not math.isfinite(f):
        raise _Bad(f"'{key}' is not finite: {v!r}")
    return f


def _int(a: dict, key: str, default: int | None = None) -> int:
    v = a.get(key)
    if v is None or v.strip() == "":
        if default is None:
            raise _Bad(f"missing '{key}'")
        return default
    s = v.strip()
    try:
        return int(s, 16) if s.lower().startswith(("0x", "-0x")) else int(float(s)) if "." in s else int(s)
    except ValueError:
        raise _Bad(f"'{key}' is not an integer: {v!r}") from None


def _bool(a: dict, key: str, default: bool | None) -> bool | None:
    v = a.get(key)
    if v is None:
        return default
    s = v.strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise _Bad(f"'{key}' is not true/false: {v!r}")


def _name_of(label: str | None) -> str | None:
    """Model name from an editor id ``object (name) (n)``; ``None`` for a bare model number."""
    m = _ID.match(label or "")
    if not m or m.group(1).lstrip("-").isdigit():
        return None
    return m.group(1)


def _object(a: dict, line: int) -> MapObject:
    pos = (_num(a, "posX"), _num(a, "posY"), _num(a, "posZ"))
    rot = (_num(a, "rotX", 0.0), _num(a, "rotY", 0.0), _num(a, "rotZ", 0.0))
    scale = _num(a, "scale", 1.0)
    sxyz = [a.get(k) for k in ("scaleX", "scaleY", "scaleZ")]
    if any(v is not None for v in sxyz):
        vals = {_num(a, k, scale) for k in ("scaleX", "scaleY", "scaleZ")}
        if len(vals) > 1:
            raise _Bad("non-uniform scaleX/Y/Z is not supported")
        scale = vals.pop()
    interior = _int(a, "interior", 0)
    if _bool(a, "allInteriors", False):
        interior = -1
    stream = a.get("streamDistance")
    return MapObject(
        model=_int(a, "model"), pos=pos, rot=rot, interior=interior, world=_int(a, "dimension", 0),
        draw=_num(a, "drawDistance", 0.0), stream=None if stream is None else _num(a, "streamDistance"),
        scale=scale, alpha=_int(a, "alpha", 255), doublesided=bool(_bool(a, "doublesided", False)),
        collisions=bool(_bool(a, "collisions", True)), breakable=_bool(a, "breakable", None),
        frozen=bool(_bool(a, "frozen", False)), lod=_int(a, "lodIndex", -1), iflags=_int(a, "iplFlags", 0),
        name=_name_of(a.get("id")), label=a.get("id"), line=line)


def read_mta(data: bytes | str, source: str = "") -> Scene:
    """Parse an MTA ``.map`` into a :class:`Scene` (objects, removals, satk material extensions)."""
    sc = Scene(source=source, fmt="mta", encoding="utf-8")
    stack: list[tuple[str, MapObject | None]] = []
    seen_root = [False]
    p = xml.parsers.expat.ParserCreate()

    def no_doctype(*_a):
        raise MtaSyntaxError(p.CurrentLineNumber, "DOCTYPE/entity declarations are not allowed in a .map")

    def start(tag: str, attrs: dict) -> None:
        line = p.CurrentLineNumber
        parent = stack[-1][1] if stack else None
        cur: MapObject | None = None
        if not stack:
            seen_root[0] = True
            if tag != "map":
                sc.issue("warn", "ROOT", f"line {line}", f"root element is <{tag}>, expected <map>")
            stack.append((tag, None))
            return
        try:
            if tag == "object":
                cur = _object(attrs, line)
                sc.objects.append(cur)
            elif tag == "removeWorldObject":
                lod = _int(attrs, "lodModel", 0)
                sc.removals.append(Removal(
                    model=_int(attrs, "model"), pos=(_num(attrs, "posX"), _num(attrs, "posY"), _num(attrs, "posZ")),
                    radius=_num(attrs, "radius"), lod_model=lod if lod > 0 else None,
                    interior=_int(attrs, "interior", -1), name=_name_of(attrs.get("id")), label=attrs.get("id"),
                    line=line))
            elif tag == "material" and parent is not None:
                m = Material(_int(attrs, "slot"), _int(attrs, "model", -1), attrs.get("txd", "none"),
                             attrs.get("tex", "none"), _int(attrs, "color", 0) & 0xFFFFFFFF, line=line)
                if parent.set_material(m):
                    sc.issue("info", "SLOT_REPLACED", f"line {line}", f"material slot {m.slot} given twice")
            elif tag == "materialText" and parent is not None:
                t = MaterialText(_int(attrs, "slot", 0), attrs.get("text", ""), _int(attrs, "size", 90),
                                 attrs.get("font", "Arial"), _int(attrs, "fontSize", 24), _int(attrs, "bold", 1),
                                 _int(attrs, "color", 0xFFFFFFFF) & 0xFFFFFFFF, _int(attrs, "back", 0) & 0xFFFFFFFF,
                                 _int(attrs, "align", 0), line=line)
                if parent.set_text(t):
                    sc.issue("info", "SLOT_REPLACED", f"line {line}", f"materialText slot {t.slot} given twice")
            elif tag in ("material", "materialText"):
                sc.issue("warn", "ORPHAN", f"line {line}", f"<{tag}> outside an <object> is ignored")
            else:
                sc.skip(tag)
        except _Bad as e:
            sc.issue("error", "BAD_ELEMENT", f"line {line}", f"<{tag}>: {e}; element skipped")
        stack.append((tag, cur))

    def end(_tag: str) -> None:
        stack.pop()

    p.StartElementHandler = start
    p.EndElementHandler = end
    p.StartDoctypeDeclHandler = no_doctype
    p.EntityDeclHandler = no_doctype
    try:
        if isinstance(data, str):  # already decoded: drop an encoding declaration, feed UTF-8
            p.Parse(_DECL.sub("", data, count=1).encode("utf-8"), True)
        else:
            p.Parse(data, True)
    except xml.parsers.expat.ExpatError as e:
        if isinstance(data, bytes):
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:  # an ANSI (cp1251) file without an encoding declaration
                return read_mta(data.decode("cp1251"), source)
        raise MtaSyntaxError(e.lineno, xml.parsers.expat.ErrorString(e.code)) from None
    if not seen_root[0]:
        raise MtaSyntaxError(1, "no root element")
    return sc


# --------------------------------------------------------------------------- writer


def _b(v: bool) -> str:
    return "true" if v else "false"


def _attrs(pairs: list[tuple[str, str]]) -> str:
    return " ".join(f"{k}={quoteattr(v)}" for k, v in pairs)


def write_mta(sc: Scene, names: dict[int, str] | None = None, materials: bool = True) -> str:
    """``.map`` text of a scene (MTA editor attribute order; satk extensions only when needed).

    Args:
        names: model id -> model name for element ids ``object (<name>) (<n>)``.
        materials: write ``<material>``/``<materialText>`` children (False: drop them).
    """
    names = names or {}
    out = ['<map edf:definitions="editor_main">']
    for n, o in enumerate(sc.objects, 1):
        label = o.label or f"object ({names.get(o.model) or o.name or o.model}) ({n})"
        a = [("id", label), ("model", str(o.model)), ("posX", num(o.pos[0])), ("posY", num(o.pos[1])),
             ("posZ", num(o.pos[2])), ("rotX", num(o.rot[0])), ("rotY", num(o.rot[1])), ("rotZ", num(o.rot[2])),
             ("interior", str(0 if o.interior < 0 else o.interior)), ("dimension", str(o.world)),
             ("alpha", str(o.alpha)), ("scale", num(o.scale)), ("doublesided", _b(o.doublesided)),
             ("collisions", _b(o.collisions))]
        if o.breakable is not None:
            a.append(("breakable", _b(o.breakable)))
        a.append(("frozen", _b(o.frozen)))
        if o.interior < 0:
            a.append(("allInteriors", "true"))
        if o.draw:
            a.append(("drawDistance", num(o.draw)))
        if o.stream is not None:
            a.append(("streamDistance", num(o.stream)))
        if o.lod >= 0:
            a.append(("lodIndex", str(o.lod)))
        if o.iflags:
            a.append(("iplFlags", str(o.iflags)))
        kids = []
        if materials:
            for m in sorted(o.materials, key=lambda m: m.slot):
                kids.append("<material " + _attrs([
                    ("slot", str(m.slot)), ("model", str(m.model)), ("txd", m.txd), ("tex", m.tex),
                    ("color", f"0x{m.color & 0xFFFFFFFF:08X}")]) + "/>")
            for t in sorted(o.texts, key=lambda t: t.slot):
                kids.append("<materialText " + _attrs([
                    ("slot", str(t.slot)), ("text", t.text), ("size", str(t.size)), ("font", t.font),
                    ("fontSize", str(t.font_size)), ("bold", str(t.bold)), ("color", f"0x{t.color & 0xFFFFFFFF:08X}"),
                    ("back", f"0x{t.back & 0xFFFFFFFF:08X}"), ("align", str(t.align))]) + "/>")
        if kids:
            out.append(f"    <object {_attrs(a)}>")
            out.extend(f"        {k}" for k in kids)
            out.append("    </object>")
        else:
            out.append(f"    <object {_attrs(a)}/>")
    for n, r in enumerate(sc.removals, 1):
        label = r.label or f"removeWorldObject ({names.get(r.model) or r.name or r.model}) ({n})"
        a = [("id", label), ("model", str(r.model))]
        if r.lod_model is not None:
            a.append(("lodModel", str(r.lod_model)))
        a += [("radius", num(r.radius)), ("interior", str(r.interior)), ("posX", num(r.pos[0])),
              ("posY", num(r.pos[1])), ("posZ", num(r.pos[2])), ("rotX", "0"), ("rotY", "0"), ("rotZ", "0")]
        out.append(f"    <removeWorldObject {_attrs(a)}/>")
    out.append("</map>")
    return "\n".join(out) + "\n"
