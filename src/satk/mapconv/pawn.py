"""SA-MP / open.mp Pawn maps: a literal-only reader and a writer (report 23 §2, §7.1).

The reader never runs code. It scans the token stream for the calls that describe maps
(:data:`FUNCS`): ``CreateObject``, ``CreatePlayerObject``, ``CreateDynamicObject(Ex)``,
``Set(Player|Dynamic)ObjectMaterial(Text)``, ``RemoveBuildingForPlayer``,
``AddSimpleModel(Timed)``, ``AddCharModel``. A call is taken only when every argument it needs is a
literal (number, string, ``{...}`` array of numbers, ``true``/``false``, a known constant such as
``OBJECT_MATERIAL_SIZE_256x128`` or ``STREAMER_OBJECT_SD``); otherwise it is skipped with a
``NOT_LITERAL`` diagnostic carrying its line. Object handles are tracked through assignments
(``tmpobjid = CreateDynamicObject(...)``, ``new o = ...``, ``objs[3] = ...``) so material calls
attach to the right object; declarations (``native``/``forward``/``stock``/``public`` or a body
``{`` after the call) are ignored.

Argument order traps (report 23 §2.1): ``SetObjectMaterialText(obj, text, index, ...)`` but
``SetDynamicObjectMaterialText(obj, index, text, ...)``. Colours are ARGB, stored unsigned.

Limits (like Neon's ``CSampMapParser``): 32 MB of source, 100 000 objects, 1 024-byte strings.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .scene import CustomModel, Material, MaterialText, MapObject, Removal, Scene, num

__all__ = ["FUNCS", "CONSTANTS", "MATERIAL_SIZES", "read_pawn", "write_pawn", "tokenize", "PawnSyntaxError",
           "MAX_SOURCE", "MAX_OBJECTS", "PAWN_FUNCS"]

MAX_SOURCE = 32 * 1024 * 1024
MAX_OBJECTS = 100_000
MAX_STRING = 1024

#: ``materialsize`` values (open.mp ``materialtextsizes.md``).
MATERIAL_SIZES: dict[int, str] = {10: "32x32", 20: "64x32", 30: "64x64", 40: "128x32", 50: "128x64", 60: "128x128",
                                  70: "256x32", 80: "256x64", 90: "256x128", 100: "256x256", 110: "512x64",
                                  120: "512x128", 130: "512x256", 140: "512x512"}

_DEFAULT = object()      # "the streamer default" (STREAMER_OBJECT_SD -> stream None)
_REQ = object()          # required parameter

#: Named constants a literal-only map may use.
CONSTANTS: dict[str, Any] = {
    "true": 1, "false": 0,
    "STREAMER_OBJECT_SD": _DEFAULT, "STREAMER_OBJECT_DD": 0.0,
    "OBJECT_MATERIAL_TEXT_ALIGN_LEFT": 0, "OBJECT_MATERIAL_TEXT_ALIGN_CENTER": 1, "OBJECT_MATERIAL_TEXT_ALIGN_CENTRE": 1,
    "OBJECT_MATERIAL_TEXT_ALIGN_RIGHT": 2,
    **{f"OBJECT_MATERIAL_SIZE_{v}": k for k, v in MATERIAL_SIZES.items()},
}


@dataclass(frozen=True)
class _P:
    name: str
    type: str          # int | float | str | ref | arr
    default: Any = _REQ


def _o(*extra: _P, player: bool = False) -> tuple[_P, ...]:
    head = (_P("player", "ref"),) if player else ()
    return (*head, _P("model", "int"), _P("x", "float"), _P("y", "float"), _P("z", "float"),
            _P("rx", "float"), _P("ry", "float"), _P("rz", "float"), *extra)


_TEXT_TAIL = (_P("size", "int", 90), _P("font", "str", "Arial"), _P("font_size", "int", 24), _P("bold", "int", 1),
              _P("color", "int", 0xFFFFFFFF), _P("back", "int", 0), _P("align", "int", 0))
_MAT = (_P("slot", "int"), _P("model", "int"), _P("txd", "str"), _P("tex", "str"), _P("color", "int", 0))

#: function -> (kind, parameters). Signatures: open.mp docs and streamer.inc v2.9.6 (report 23 §2.1).
FUNCS: dict[str, tuple[str, tuple[_P, ...]]] = {
    "CreateObject": ("object", _o(_P("draw", "float", 0.0))),
    "CreatePlayerObject": ("object", _o(_P("draw", "float", 0.0), player=True)),
    "CreateDynamicObject": ("object", _o(
        _P("world", "int", -1), _P("interior", "int", -1), _P("player_id", "int", -1), _P("stream", "float", _DEFAULT),
        _P("draw", "float", 0.0), _P("area", "int", -1), _P("priority", "int", 0))),
    "CreateDynamicObjectEx": ("object", _o(
        _P("stream", "float", _DEFAULT), _P("draw", "float", 0.0), _P("worlds", "arr", (-1,)),
        _P("interiors", "arr", (-1,)), _P("players", "arr", (-1,)), _P("areas", "arr", (-1,)), _P("priority", "int", 0),
        _P("maxworlds", "int", None), _P("maxinteriors", "int", None), _P("maxplayers", "int", None),
        _P("maxareas", "int", None))),
    "SetObjectMaterial": ("material", (_P("obj", "ref"), *_MAT)),
    "SetDynamicObjectMaterial": ("material", (_P("obj", "ref"), *_MAT)),
    "SetPlayerObjectMaterial": ("material", (_P("player", "ref"), _P("obj", "ref"), *_MAT)),
    "SetObjectMaterialText": ("text", (_P("obj", "ref"), _P("text", "str"), _P("slot", "int", 0), *_TEXT_TAIL)),
    "SetPlayerObjectMaterialText": ("text", (_P("player", "ref"), _P("obj", "ref"), _P("text", "str"),
                                             _P("slot", "int", 0), *_TEXT_TAIL)),
    "SetDynamicObjectMaterialText": ("text", (_P("obj", "ref"), _P("slot", "int"), _P("text", "str"), *_TEXT_TAIL)),
    "RemoveBuildingForPlayer": ("remove", (_P("player", "ref"), _P("model", "int"), _P("x", "float"),
                                           _P("y", "float"), _P("z", "float"), _P("radius", "float"))),
    "AddSimpleModel": ("model", (_P("world", "int"), _P("base", "int"), _P("id", "int"), _P("dff", "str"),
                                 _P("txd", "str"))),
    "AddSimpleModelTimed": ("model", (_P("world", "int"), _P("base", "int"), _P("id", "int"), _P("dff", "str"),
                                      _P("txd", "str"), _P("time_on", "int"), _P("time_off", "int"))),
    "AddCharModel": ("model", (_P("base", "int"), _P("id", "int"), _P("dff", "str"), _P("txd", "str"))),
}
#: Object-creating functions a writer can emit.
PAWN_FUNCS = ("CreateObject", "CreateDynamicObject", "CreateDynamicObjectEx")
_DECL = frozenset({"native", "forward", "stock", "public", "static", "operator", "define", "hook"})


class PawnSyntaxError(ValueError):
    """Unterminated string or comment; ``line`` is 1-based."""

    def __init__(self, line: int, msg: str):
        super().__init__(f"line {line}: {msg}")
        self.line = line


# --------------------------------------------------------------------------- tokenizer

_TOKEN = re.compile(
    r"(?P<nl>\n)"
    r"|(?P<ws>[ \t\r\f\v\ufeff]+)"
    r"|(?P<lc>//[^\n]*)"
    r"|(?P<bc>/\*.*?\*/)"
    r"|(?P<bcu>/\*)"
    r'|(?P<str>"(?:\\.|[^"\\\n])*")'
    r'|(?P<stru>")'
    r"|(?P<chr>'(?:\\.|[^'\\\n])')"
    r"|(?P<num>0[xX][0-9a-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.[\d_]*)?(?:[eE][+-]?\d+)?)"
    r"|(?P<id>[A-Za-z_@][A-Za-z0-9_@]*)"
    r"|(?P<pp>\#)"
    r"|(?P<op><<=|>>=|>>>|==|!=|<=|>=|&&|\|\||\+\+|--|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<|>>|::|\.\.\.|.)",
    re.S)
_ESC = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "f": "\f", "v": "\v", "\\": "\\",
        "'": "'", '"': '"', "%": "%", "0": "\0"}


@dataclass(frozen=True, slots=True)
class Tok:
    kind: str   # num | str | id | op
    text: str
    value: Any
    line: int


def _unescape(body: str) -> str:
    if "\\" not in body:
        return body
    out: list[str] = []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nx = body[i + 1]
        m = re.match(r"\d{1,3};?", body[i + 1:]) if nx.isdigit() and nx != "0" else None
        if m:  # \ddd; decimal character code
            out.append(chr(int(m.group(0).rstrip(";"))))
            i += 1 + len(m.group(0))
            continue
        out.append(_ESC.get(nx, nx))
        i += 2
    return "".join(out)


def _number(text: str) -> int | float:
    t = text.replace("_", "")
    low = t.lower()
    if low.startswith("0x"):
        return int(t[2:], 16)
    if low.startswith("0b"):
        return int(t[2:], 2)
    if "." in t or "e" in low:
        return float(t)
    return int(t)


def tokenize(text: str) -> list[Tok]:
    """Pawn tokens (comments and preprocessor lines dropped).

    Raises :class:`PawnSyntaxError` on an unterminated string or ``/*`` comment.
    """
    toks: list[Tok] = []
    line, pos, n = 1, 0, len(text)
    bol = True  # only whitespace since the start of the line
    match = _TOKEN.match
    while pos < n:
        m = match(text, pos)
        kind, s = m.lastgroup, m.group()
        pos = m.end()
        if kind == "nl":
            line += 1
            bol = True
            continue
        if kind in ("ws", "lc"):
            continue
        if kind == "bc":
            line += s.count("\n")
            continue
        if kind == "bcu":
            raise PawnSyntaxError(line, "unterminated /* comment")
        if kind == "stru":
            raise PawnSyntaxError(line, "unterminated string")
        if kind == "pp" and bol:  # a directive with "\" continuations: not map data
            while True:
                j = text.find("\n", pos)
                j = n if j < 0 else j
                cont = text[pos:j].rstrip("\r").endswith("\\")
                pos = j
                if not cont or pos >= n:
                    break
                line += 1
                pos += 1
            continue
        bol = False
        if kind == "str":
            toks.append(Tok("str", s, _unescape(s[1:-1]), line))
            line += s.count("\n")  # "\" line continuation inside a string
        elif kind == "chr":
            toks.append(Tok("num", s, ord(_unescape(s[1:-1])[:1] or "\0"), line))
        elif kind == "num":
            try:
                val: Any = _number(s)
            except ValueError:
                val = None
            toks.append(Tok("num", s, val, line))
        elif kind == "id":
            toks.append(Tok("id", s, s, line))
        else:  # operators, punctuation, a "#" inside a line
            toks.append(Tok("op", s, s, line))
    return toks


# --------------------------------------------------------------------------- argument parsing


class _NotLiteral(Exception):
    pass


def _split_args(toks: list[Tok], start: int) -> tuple[list[list[Tok]], int]:
    """``toks[start]`` is ``(``: the argument token lists and the index after the matching ``)``."""
    depth = 0
    args: list[list[Tok]] = [[]]
    i = start
    while i < len(toks):
        t = toks[i]
        if t.kind == "op" and t.text in "([{":
            depth += 1
            if depth > 1:
                args[-1].append(t)
        elif t.kind == "op" and t.text in ")]}":
            depth -= 1
            if depth == 0:
                if len(args) == 1 and not args[0]:
                    args = []
                return args, i + 1
            args[-1].append(t)
        elif t.kind == "op" and t.text == "," and depth == 1:
            args.append([])
        elif t.kind == "op" and t.text == ";" and depth <= 1:
            break  # a ';' inside a call: broken source; stop here
        else:
            args[-1].append(t)
        i += 1
    raise _NotLiteral("unbalanced parentheses")


def _strip_tag(arg: list[Tok]) -> list[Tok]:
    """``Float:1.0`` / ``STREAMER_TAG_AREA:-1`` -> the value tokens."""
    if len(arg) >= 3 and arg[0].kind == "id" and arg[1].kind == "op" and arg[1].text == ":":
        return arg[2:]
    return arg


def _scalar(arg: list[Tok]) -> Any:
    arg = _strip_tag(arg)
    sign = 1
    while arg and arg[0].kind == "op" and arg[0].text in "+-":
        if arg[0].text == "-":
            sign = -sign
        arg = arg[1:]
    if len(arg) == 1:
        t = arg[0]
        if t.kind == "num" and t.value is not None:
            return sign * t.value
        if t.kind == "id" and t.text in CONSTANTS:
            v = CONSTANTS[t.text]
            if v is _DEFAULT:
                if sign < 0:
                    raise _NotLiteral(f"-{t.text}")
                return v
            return sign * v
    raise _NotLiteral(" ".join(x.text for x in arg) or "<empty>")


def _value(p: _P, arg: list[Tok]) -> Any:
    if p.type == "ref":
        core = _strip_tag(arg)
        if core and core[0].kind == "id":
            return "".join(t.text for t in core)  # handle text: "tmpobjid", "objs[3]", "playerid"
        if len(core) == 1 and core[0].kind == "num":
            return str(core[0].value)
        raise _NotLiteral(" ".join(t.text for t in arg) or "<empty>")
    if p.type == "str":
        core = arg[1:] if arg and arg[0].kind == "op" and arg[0].text == "!" else arg  # packed string
        if len(core) == 1 and core[0].kind == "str":
            return core[0].value
        raise _NotLiteral(" ".join(t.text for t in arg) or "<empty>")
    if p.type == "arr":
        core = _strip_tag(arg)
        if len(core) >= 2 and core[0].text == "{" and core[-1].text == "}":
            items: list[list[Tok]] = [[]]
            for t in core[1:-1]:
                if t.kind == "op" and t.text == ",":
                    items.append([])
                else:
                    items[-1].append(t)
            vals = [_scalar(x) for x in items if x]
            if all(isinstance(v, int) for v in vals):
                return tuple(vals)
        raise _NotLiteral(" ".join(t.text for t in arg) or "<empty>")
    v = _scalar(arg)
    if v is _DEFAULT:
        if p.default is _DEFAULT:
            return v
        raise _NotLiteral("STREAMER_OBJECT_SD")
    if p.type == "int":
        if isinstance(v, float):
            if not v.is_integer():
                raise _NotLiteral(f"{v} (an integer is expected)")
            v = int(v)
        return v
    if not math.isfinite(float(v)):
        raise _NotLiteral(str(v))
    return float(v)


def _bind(params: tuple[_P, ...], args: list[list[Tok]]) -> dict[str, Any]:
    if len(args) > len(params):
        raise _NotLiteral(f"{len(args)} arguments, at most {len(params)} expected")
    out: dict[str, Any] = {}
    for k, p in enumerate(params):
        if k < len(args):
            out[p.name] = _value(p, args[k])
        elif p.default is _REQ:
            raise _NotLiteral(f"missing argument '{p.name}'")
        else:
            out[p.name] = p.default
    return out


def _target(toks: list[Tok], i: int) -> str | None:
    """Handle the call at ``toks[i]`` is assigned to: ``x = F(...)`` / ``a[1] = F(...)`` -> ``"x"`` / ``"a[1]"``."""
    j = i - 1
    if j < 0 or toks[j].kind != "op" or toks[j].text != "=":
        return None
    j -= 1
    if j < 0:
        return None
    if toks[j].kind == "op" and toks[j].text == "]":
        depth, k = 0, j
        while k >= 0:
            if toks[k].text == "]":
                depth += 1
            elif toks[k].text == "[":
                depth -= 1
                if depth == 0:
                    break
            k -= 1
        if k <= 0 or toks[k - 1].kind != "id":
            return None
        return "".join(t.text for t in toks[k - 1:j + 1])
    return toks[j].text if toks[j].kind == "id" else None


def _decode(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace"), "cp1251"  # Russian SA-MP sources are ANSI


# --------------------------------------------------------------------------- reader


def read_pawn(data: bytes | str, source: str = "") -> Scene:
    """Parse a Pawn map (bytes: UTF-8, else cp1251) into a :class:`Scene` (never executes anything)."""
    if isinstance(data, bytes):
        if len(data) > MAX_SOURCE:
            raise ValueError(f"source is {len(data)} bytes, the limit is {MAX_SOURCE}")
        text, enc = _decode(data)
    else:
        text, enc = data, "utf-8"
    sc = Scene(source=source, fmt="pawn", encoding=enc)
    try:
        toks = tokenize(text)
    except PawnSyntaxError as e:
        sc.issue("error", "SYNTAX", f"line {e.line}", str(e).split(": ", 1)[1])
        return sc
    handles: dict[str, int] = {}
    n = len(toks)
    i = 0
    while i < n:
        t = toks[i]
        if t.kind != "id" or t.text not in FUNCS or i + 1 >= n or toks[i + 1].text != "(":
            i += 1
            continue
        name = t.text
        kind, params = FUNCS[name]
        prev = toks[i - 1] if i else None
        try:
            args, end = _split_args(toks, i + 1)
        except _NotLiteral as e:
            sc.issue("warn", "NOT_LITERAL", f"line {t.line}", f"{name}: {e}")
            i += 2
            continue
        if (prev is not None and prev.kind == "id" and prev.text in _DECL) or (end < n and toks[end].text == "{"):
            i = end  # a declaration or definition of the function itself
            continue
        try:
            vals = _bind(params, args)
        except _NotLiteral as e:
            sc.issue("warn", "NOT_LITERAL", f"line {t.line}", f"{name}: skipped, not a literal: {e}")
            i = end
            continue
        target = _target(toks, i)
        if kind == "object":
            _add_object(sc, name, vals, t.line, target, handles)
        elif kind in ("material", "text"):
            _add_material(sc, name, kind, vals, t.line, handles)
        elif kind == "remove":
            r = Removal(vals["model"], (vals["x"], vals["y"], vals["z"]), vals["radius"], line=t.line)
            sc.removals.append(r)
        else:
            k = "char" if name == "AddCharModel" else "timed" if name.endswith("Timed") else "simple"
            sc.models.append(CustomModel(k, vals["base"], vals["id"], vals["dff"], vals["txd"],
                                         world=vals.get("world", -1), time_on=vals.get("time_on"),
                                         time_off=vals.get("time_off"), line=t.line))
        i = end
    if sc.models:
        sc.issue("info", "CUSTOM_MODELS", "file",
                 f"{len(sc.models)} AddSimpleModel/AddCharModel definitions (kept in the report; MTA .map and IPL "
                 "have no equivalent)")
    return sc


def _add_object(sc: Scene, name: str, v: dict, line: int, target: str | None, handles: dict[str, int]) -> None:
    if len(sc.objects) >= MAX_OBJECTS:
        if not sc.skipped.get("objects over the limit"):
            sc.issue("error", "LIMIT", f"line {line}", f"more than {MAX_OBJECTS} objects: the rest are skipped")
        sc.skip("objects over the limit")
        return
    stream = None if v.get("stream", _DEFAULT) is _DEFAULT else v["stream"]
    world, interior = v.get("world", -1), v.get("interior", -1)
    if name == "CreateDynamicObjectEx":
        worlds, interiors = v["worlds"], v["interiors"]
        world = worlds[0] if worlds else -1
        interior = interiors[0] if interiors else -1
        if len(worlds) > 1 or len(interiors) > 1:
            sc.issue("warn", "EX_ARRAYS", f"line {line}",
                     f"CreateDynamicObjectEx: worlds {list(worlds)} / interiors {list(interiors)}: only the first is kept")
        if v["players"] != (-1,) or v["areas"] != (-1,):
            sc.issue("warn", "EX_ARRAYS", f"line {line}", "CreateDynamicObjectEx: players/areas filters are dropped")
    elif name == "CreateDynamicObject":
        if v["player_id"] != -1 or v["area"] != -1:
            sc.issue("warn", "STREAMER_FILTER", f"line {line}",
                     f"CreateDynamicObject: playerid {v['player_id']} / areaid {v['area']} filters are dropped")
    if name == "CreatePlayerObject":
        sc.issue("info", "PLAYER_OBJECT", f"line {line}", "CreatePlayerObject: converted as a global object")
    o = MapObject(model=v["model"], pos=(v["x"], v["y"], v["z"]), rot=(v["rx"], v["ry"], v["rz"]),
                  interior=interior, world=world, draw=v.get("draw", 0.0), stream=stream, func=name, line=line)
    sc.objects.append(o)
    if target:
        handles[target] = len(sc.objects) - 1


def _add_material(sc: Scene, name: str, kind: str, v: dict, line: int, handles: dict[str, int]) -> None:
    ref = v["obj"]
    idx = handles.get(ref)
    if idx is None:
        sc.issue("warn", "UNKNOWN_HANDLE", f"line {line}",
                 f"{name}: object handle '{ref}' is not assigned from a literal Create*Object call; skipped")
        return
    o = sc.objects[idx]
    if kind == "material":
        m = Material(v["slot"], v["model"], v["txd"], v["tex"], v["color"] & 0xFFFFFFFF, line=line)
        replaced = o.set_material(m)
    else:
        if len(v["text"]) > 2048:
            sc.issue("warn", "TEXT_LONG", f"line {line}", f"{name}: text is {len(v['text'])} chars, SA-MP keeps 2048")
        m = MaterialText(v["slot"], v["text"], v["size"], v["font"], v["font_size"], v["bold"],
                         v["color"] & 0xFFFFFFFF, v["back"] & 0xFFFFFFFF, v["align"], line=line)
        replaced = o.set_text(m)
    if replaced:
        sc.issue("info", "SLOT_REPLACED", f"line {line}", f"{name}: slot {v['slot']} set again (the last call wins)")


# --------------------------------------------------------------------------- writer


def _f(v: float) -> str:
    """Pawn Float literal: always with a decimal point (no tag-mismatch warnings)."""
    s = num(v)
    return s if ("." in s or "e" in s) else s + ".0"


def _q(s: str) -> str:
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{out}"'


def _hex(v: int) -> str:
    return f"0x{v & 0xFFFFFFFF:08X}"


def _trim(args: list[str], defaults: list[str], keep: int) -> list[str]:
    """Drop trailing arguments equal to their defaults (never below ``keep``)."""
    while len(args) > keep and args[-1] == defaults[len(args) - 1]:
        args.pop()
    return args


def _create(o: MapObject, func: str) -> str:
    head = [str(o.model), *(_f(c) for c in o.pos), *(_f(c) for c in o.rot)]
    if func == "CreateObject":
        args = _trim(head + [_f(o.draw)], head + ["0.0"], 7)
    elif func == "CreateDynamicObject":
        sd = "STREAMER_OBJECT_SD" if o.stream is None else _f(o.stream)
        tail = [str(o.world), str(o.interior), "-1", sd, _f(o.draw)]
        args = _trim(head + tail, head + ["-1", "-1", "-1", "STREAMER_OBJECT_SD", "0.0"], 7)
    else:
        sd = "STREAMER_OBJECT_SD" if o.stream is None else _f(o.stream)
        tail = [sd, _f(o.draw), f"{{{o.world}}}", f"{{{o.interior}}}"]
        args = _trim(head + tail, head + ["STREAMER_OBJECT_SD", "0.0", "{-1}", "{-1}"], 7)
    return f"{func}({', '.join(args)})"


def write_pawn(sc: Scene, func: str = "auto", header: str | None = None) -> str:
    """Pawn text of a scene (Texture Studio style). ``func``: ``auto`` (each object's own creating
    function, else ``CreateDynamicObject``) or one of :data:`PAWN_FUNCS` for all objects."""
    lines: list[str] = []
    if header:
        lines += [f"// {h}" for h in header.splitlines()]
    for r in sc.removals:
        args = [str(r.model), *(_f(c) for c in r.pos), _f(r.radius)]
        lines.append(f"RemoveBuildingForPlayer(playerid, {', '.join(args)});")
        if r.lod_model is not None and r.lod_model != r.model:
            args[0] = str(r.lod_model)
            lines.append(f"RemoveBuildingForPlayer(playerid, {', '.join(args)});")
    for m in sc.models:
        if m.kind == "char":
            lines.append(f"AddCharModel({m.base}, {m.id}, {_q(m.dff)}, {_q(m.txd)});")
        elif m.kind == "timed":
            lines.append(f"AddSimpleModelTimed({m.world}, {m.base}, {m.id}, {_q(m.dff)}, {_q(m.txd)}, "
                         f"{m.time_on if m.time_on is not None else 0}, {m.time_off if m.time_off is not None else 24});")
        else:
            lines.append(f"AddSimpleModel({m.world}, {m.base}, {m.id}, {_q(m.dff)}, {_q(m.txd)});")
    if sc.objects:
        lines.append("new tmpobjid;")
    for o in sc.objects:
        fn = func if func != "auto" else (o.func if o.func in PAWN_FUNCS else "CreateDynamicObject")
        dyn = fn != "CreateObject"
        call = _create(o, fn)
        has_mat = bool(o.materials or o.texts)
        note = f" // {o.name}" if o.name else ""
        lines.append(f"{'tmpobjid = ' if has_mat else ''}{call};{note}")
        for m in sorted(o.materials, key=lambda m: m.slot):
            fn_m = "SetDynamicObjectMaterial" if dyn else "SetObjectMaterial"
            lines.append(f"{fn_m}(tmpobjid, {m.slot}, {m.model}, {_q(m.txd)}, {_q(m.tex)}, {_hex(m.color)});")
        for t in sorted(o.texts, key=lambda t: t.slot):
            tail = f"{t.size}, {_q(t.font)}, {t.font_size}, {t.bold}, {_hex(t.color)}, {_hex(t.back)}, {t.align}"
            if dyn:
                lines.append(f"SetDynamicObjectMaterialText(tmpobjid, {t.slot}, {_q(t.text)}, {tail});")
            else:
                lines.append(f"SetObjectMaterialText(tmpobjid, {_q(t.text)}, {t.slot}, {tail});")
    return "\n".join(lines) + "\n"
