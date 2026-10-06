"""``handling.cfg`` records with their exact text: read, change fields, write back byte for byte.

:func:`parse` keeps every line of the file (:class:`HFile`); each data line becomes an :class:`HRec` with
its :class:`~satk.addon.tokline.TokLine`, kind (``car``, ``bike`` ``!``, ``boat`` ``%``, ``flying`` ``$``;
anim-group ``^`` lines are kept as text) and typed values. The game's reading rules follow
:mod:`satk.formats.handling` (``;`` comment lines, ``;the end`` stops the data, a later line of the same
id and kind replaces an earlier one, the ``0.1s`` quirk of ``$ RCRAIDER``).

* :meth:`HRec.patched` returns a copy with new values, formatted like the old ones (:func:`fmt_number`);
* :meth:`HFile.render` with replacements gives the whole file with only those lines changed;
* :func:`mta_calls` turns field changes into MTA ``setModelHandling`` calls.

Stdlib only.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..core.errors import SatkError
from ..formats.handling import KINDS, PREFIX_KIND
from . import fields as F
from .tokline import TokLine, f32, fmt_number, parse_typed, split_eol

__all__ = ["HRec", "HFile", "parse", "field_table", "PREFIX", "value_note", "mta_calls", "check_value"]

#: kind -> line prefix.
PREFIX = {"car": "", "bike": "!", "boat": "%", "flying": "$"}
_FLOAT_PREFIX = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


def field_table(kind: str) -> list[dict]:
    return F.table("handling")[kind]


@dataclass
class HRec:
    """One handling record line (``kind`` car|bike|boat|flying) with its token positions."""

    kind: str
    hid: str
    line: int                 # 1-based line number in the file
    tl: TokLine
    first: int                # token index of the first value (after the id; after prefix + id for ! % $)
    values: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.tl.text(eol=False)

    def token(self, key: str) -> str:
        i = [f["key"] for f in field_table(self.kind)].index(key)
        return self.tl.get(self.first + i)

    def patched(self, changes: dict[str, tuple[object, str | None]], *, new_id: str | None = None) -> "HRec":
        """A copy with ``changes`` = ``{key: (value, text the user typed)}`` and optionally a new id."""
        tl = self.tl.copy()
        keys = [f["key"] for f in field_table(self.kind)]
        types = {f["key"]: f["type"] for f in field_table(self.kind)}
        vals = dict(self.values)
        for key, (val, given) in changes.items():
            i = self.first + keys.index(key)
            tl.set(i, fmt_number(val, types[key], tl.get(i), given))
            vals[key] = val
        hid = self.hid
        if new_id is not None:
            idx = self.first - 1
            tok = tl.get(idx)
            if self.kind != "car" and len(tok) > 1 and tok[0] == PREFIX[self.kind]:
                tl.set(idx, PREFIX[self.kind] + new_id)     # "%COASTG" written without a space
            else:
                tl.set(idx, new_id)
            hid = new_id
        return HRec(self.kind, hid, self.line, tl, self.first, vals)


def _value(tok: str, typ: str, skip_char: bool):
    if typ == "f":
        m = _FLOAT_PREFIX.match(tok)
        if not m or len(tok) - m.end() > (1 if skip_char else 0):
            raise ValueError(f"not a number: {tok!r}")
        return float(m.group(0))
    if typ == "c":
        if len(tok) != 1:
            raise ValueError(f"expected one character, got {tok!r}")
        return tok.upper()
    return parse_typed(tok, typ)


def _rec(raw: str, n: int) -> HRec | None:
    tl = TokLine.parse(raw)
    toks = tl.toks
    if not toks:
        return None
    kind = PREFIX_KIND.get(toks[0][0], "car")
    if kind == "car":
        first = 1
        hid = toks[0]
    elif len(toks[0]) > 1:
        first, hid = 1, toks[0][1:]
    else:
        if len(toks) < 2:
            raise ValueError(f"{kind} line without an id")
        first, hid = 2, toks[1]
    specs = KINDS[kind]
    if len(toks) - first < len(specs):
        raise ValueError(f"{kind} line has {len(toks) - first} values, needs {len(specs)}")
    vals = {}
    for (name, typ), tok in zip(specs, toks[first:]):
        vals[name] = _value(tok, typ, kind == "flying" and name == "wind_mult")
    return HRec(kind, hid, n, tl, first, vals)


@dataclass
class HFile:
    """A parsed ``handling.cfg``: every raw line plus the records the game uses."""

    lines: list[str]                                   # raw lines with their end-of-line characters
    recs: dict[tuple[str, str], HRec]                  # (lower id, kind) -> winning record
    errors: list[tuple[int, str]] = field(default_factory=list)
    end: int | None = None                             # line number of ';the end'

    def get(self, hid: str, kind: str = "car") -> HRec | None:
        return self.recs.get((hid.lower(), kind))

    def kinds_of(self, hid: str) -> list[HRec]:
        """The records of one handling id in the order car, bike, boat, flying."""
        return [r for k in ("car", "bike", "boat", "flying") if (r := self.get(hid, k)) is not None]

    def ids(self) -> list[str]:
        return [r.hid for (_, k), r in self.recs.items() if k == "car"]

    def render(self, replace: dict[int, str] | None = None) -> str:
        """The file text with ``replace`` = ``{line number: new text without end of line}``."""
        out = []
        for n, raw in enumerate(self.lines, 1):
            if replace and n in replace:
                _, eol = split_eol(raw)
                out.append(replace[n] + eol)
            else:
                out.append(raw)
        return "".join(out)


def parse(text: str) -> HFile:
    """Parse ``handling.cfg`` text (latin-1 decoded), keeping every line."""
    lines = text.splitlines(keepends=True)
    recs: dict[tuple[str, str], HRec] = {}
    errors: list[tuple[int, str]] = []
    end = None
    for n, raw in enumerate(lines, 1):
        s = raw.strip()
        if not s:
            continue
        if s.startswith(";"):
            if s.lower() == ";the end":
                end = n
                break
            continue
        if s[0] == "^":
            continue
        try:
            r = _rec(raw, n)
        except (ValueError, OverflowError) as e:
            errors.append((n, str(e)))
            continue
        if r is None:
            continue
        key = (r.hid.lower(), r.kind)
        recs.pop(key, None)
        recs[key] = r
    return HFile(lines, recs, errors, end)


def check_value(f: dict, text: str):
    """Parse and check a user value for field ``f``; returns ``(value, warning or None)``."""
    typ = f["type"]
    t = text.strip()
    if typ == "x" and t.lower().startswith("0x"):
        t = t[2:]
    try:
        v = parse_typed(t, typ)
    except ValueError as e:
        raise SatkError("BAD_PARAMS", f"{f['name']}: {e}", hint=f"satk data explain handling {f['name']}") from None
    if "choices" in f:
        ch = f["choices"]
        if (v.upper() if isinstance(v, str) else v) not in ch:
            raise SatkError("BAD_PARAMS", f"{f['name']} must be one of {', '.join(map(str, ch))}, got {text!r}",
                            hint=f"satk data explain handling {f['name']}")
    warn = None
    lim = f.get("limit")
    if lim and not lim[0] <= float(v) <= lim[1]:
        warn = f"OUT_OF_RANGE: {f['name']}={text} is outside {lim[0]:g}..{lim[1]:g}"
    elif typ == "f" and not math.isfinite(f32(float(v))):
        raise SatkError("BAD_PARAMS", f"{f['name']}={text} does not fit a 32-bit float")
    return v, warn


def value_note(kind: str, f: dict, v) -> str:
    """Short unit or meaning for the ``unit`` column of ``data get``."""
    enums = F.table("handling")["enums"]
    if f.get("flags"):
        names = F.flag_names("handling", f["flags"], int(v))
        return "|".join(names) if names else "none"
    if f["key"] == "drive":
        return enums["drive"].get(str(v), "?")
    if f["key"] == "engine":
        return enums["engine"].get(str(v), "?")
    if f.get("enum") == "lights":
        return enums["lights"].get(str(v), "?")
    return f.get("unit", "")


def mta_calls(rec: HRec, changes: dict[str, object], models: list[tuple[int, str]]) -> tuple[list[str], list[str]]:
    """Lua lines (``setModelHandling``) for ``changes`` of a car record, plus warnings.

    ``centerOfMass`` is one vector property: a change of any component writes all three (the others from
    ``rec``). Fields MTA cannot set (monetary, lights) and bike/boat/flying fields become comments.
    """
    enums = F.table("handling")["enums"]
    calls: list[tuple[str, str]] = []
    warn: list[str] = []
    done_com = False
    vals = dict(rec.values)
    vals.update(changes)
    for key in changes:
        f = next(x for x in field_table(rec.kind) if x["key"] == key)
        if rec.kind != "car":
            warn.append(f"MTA_UNSUPPORTED: {f['name']} ({rec.kind} line): MTA has no API for {rec.kind} handling")
            continue
        prop = f.get("mta")
        if not prop or f.get("mta_set") is False:
            warn.append(f"MTA_UNSUPPORTED: setModelHandling cannot set {f['name']} ({prop or 'no property'})")
            continue
        v = changes[key]
        if prop == "centerOfMass":
            if done_com:
                continue
            done_com = True
            arg = "{%s}" % ", ".join(_lua_num(vals[k]) for k in ("com_x", "com_y", "com_z"))
        elif key == "drive":
            arg = f'"{enums["mta_drive"][str(v)]}"'
        elif key == "engine":
            arg = f'"{enums["mta_engine"][str(v)]}"'
        elif key == "abs":
            arg = "true" if int(v) else "false"
        elif f["type"] == "x":
            arg = f"0x{int(v):X}"
        else:
            arg = _lua_num(v)
        calls.append((prop, arg))
    lines = [f'setModelHandling({mid}, "{prop}", {arg}) -- {name}' for mid, name in models for prop, arg in calls]
    return lines, warn


def _lua_num(v) -> str:
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    s = repr(float(v))
    return s[:-2] if s.endswith(".0") else s
