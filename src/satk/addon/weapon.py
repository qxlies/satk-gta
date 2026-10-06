"""``weapon.dat`` (GTA San Andreas): gun, melee and aim lines with their exact text.

Line types (the first character; ``CWeaponInfo::LoadWeaponData``):

* ``$`` gun data: weapon type, fire type, ranges, models, slot, anim group, clip, damage, fire offset, skill
  level, required stat, accuracy, move speed, two animation loops, breakout time, hex flags and four
  optional projectile values (speed, radius, lifespan, spread); one line per skill level of the 11 skill
  weapons (PISTOL .. TEC9: 0 poor, 1 std, 2 pro, 3 cop), other guns use one line;
* ``£`` (byte 0xA3) melee data: type, fire type, ranges, models, slot, base combo, combo count, hex flags,
  stealth anim group;
* ``%`` aim offsets of a gun anim group.

``#`` lines are comments, a line containing ``ENDWEAPONDATA`` ends the data. A later line with the same
key (type + skill for guns, type for melee, anim group for aim) replaces the earlier one. Values are kept
in file units (frames, metres, hex flags). Text is latin-1 (the pound sign is ``\\xa3``).

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.errors import SatkError
from . import fields as F
from .tokline import TokLine, fmt_number, parse_typed, split_eol

__all__ = ["WRec", "WFile", "parse", "PREFIX", "KIND_OF", "skill_types", "type_id", "mta_calls", "value_note"]

MELEE = "£"
PREFIX = {"gun": "$", "melee": MELEE, "aim": "%"}
KIND_OF = {v: k for k, v in PREFIX.items()}


def _fields(kind: str) -> list[dict]:
    return F.table("weapon")[kind]


@dataclass
class WRec:
    """One weapon.dat line. ``key`` = ``(kind, NAME)`` (+ skill for guns); ``values`` in field order."""

    kind: str
    line: int
    tl: TokLine
    first: int                     # token index of field A
    values: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return str(self.values["type" if self.kind != "aim" else "anim_group"])

    @property
    def skill(self) -> int | None:
        return self.values.get("skill") if self.kind == "gun" else None

    @property
    def key(self) -> tuple:
        return (self.kind, self.name.upper(), self.skill)

    @property
    def text(self) -> str:
        return self.tl.text(eol=False)

    def patched(self, changes: dict[str, tuple[object, str | None]]) -> "WRec":
        tl = self.tl.copy()
        keys = [f["key"] for f in _fields(self.kind)]
        types = {f["key"]: f["type"] for f in _fields(self.kind)}
        vals = dict(self.values)
        for key, (val, given) in changes.items():
            i = self.first + keys.index(key)
            if i >= len(tl):
                raise SatkError("BAD_PARAMS", f"{key} is an optional value this line does not have",
                                hint="only projectile and area-effect lines carry speed/radius/lifespan/spread")
            tl.set(i, fmt_number(val, types[key], tl.get(i), given))
            vals[key] = val
        return WRec(self.kind, self.line, tl, self.first, vals)


def _rec(raw: str, n: int) -> WRec | None:
    tl = TokLine.parse(raw)
    toks = tl.toks
    if not toks:
        return None
    kind = KIND_OF.get(toks[0][0])
    if kind is None:
        return None
    first = 1
    if len(toks[0]) > 1:          # "$PISTOL" without a space
        raise ValueError(f"{kind} line: put a space after {toks[0][0]!r}")
    flist = _fields(kind)
    need = [f for f in flist if not f.get("optional")]
    if len(toks) - first < len(need):
        raise ValueError(f"{kind} line has {len(toks) - first} values, needs {len(need)}")
    vals = {}
    for f, tok in zip(flist, toks[first:]):
        vals[f["key"]] = parse_typed(tok, f["type"])
    return WRec(kind, n, tl, first, vals)


@dataclass
class WFile:
    lines: list[str]
    recs: dict[tuple, WRec]
    errors: list[tuple[int, str]] = field(default_factory=list)
    end: int | None = None

    def by_type(self, name: str) -> list[WRec]:
        """Gun or melee lines of one weapon type, skill order."""
        n = name.upper()
        out = [r for r in self.recs.values() if r.kind != "aim" and r.name.upper() == n]
        return sorted(out, key=lambda r: (r.skill if r.skill is not None else -1))

    def aim(self, group: str) -> WRec | None:
        return self.recs.get(("aim", group.upper(), None))

    def types(self) -> list[str]:
        seen: dict[str, None] = {}
        for r in self.recs.values():
            if r.kind != "aim":
                seen.setdefault(r.name.upper())
        return list(seen)

    def render(self, replace: dict[int, str] | None = None, append_before_end: list[str] = ()) -> str:
        out = []
        for n, raw in enumerate(self.lines, 1):
            body, eol = split_eol(raw)
            if append_before_end and n == self.end:
                out += [ln + (eol or "\r\n") for ln in append_before_end]
            out.append(replace[n] + eol if replace and n in replace else raw)
        if append_before_end and self.end is None:
            out += [ln + "\r\n" for ln in append_before_end]
        return "".join(out)


def parse(text: str) -> WFile:
    """Parse weapon.dat text (latin-1 decoded), keeping every line."""
    lines = text.splitlines(keepends=True)
    recs: dict[tuple, WRec] = {}
    errors: list[tuple[int, str]] = []
    end = None
    for n, raw in enumerate(lines, 1):
        if "ENDWEAPONDATA" in raw:
            end = n
            break
        s = raw.lstrip()
        if not s or s[0] == "#":
            continue
        try:
            r = _rec(raw, n)
        except (ValueError, OverflowError) as e:
            errors.append((n, str(e)))
            continue
        if r is None:
            continue
        recs.pop(r.key, None)
        recs[r.key] = r
    return WFile(lines, recs, errors, end)


def skill_types() -> range:
    a, b = F.table("weapon")["skill_types"]
    return range(a, b + 1)


def type_id(name: str) -> int | None:
    return F.table("weapon")["types"].get(name.upper())


def value_note(f: dict, v) -> str:
    if f.get("flags"):
        names = F.flag_names("weapon", f["flags"], int(v))
        return "|".join(names) if names else "none"
    if f["key"] == "skill":
        return F.table("weapon")["skills"].get(str(v), "?")
    return f.get("unit", "")


def mta_calls(rec: WRec, changes: dict[str, object]) -> tuple[list[str], list[str]]:
    """Lua ``setWeaponProperty`` lines for ``changes`` of a gun/melee line, plus warnings."""
    warn: list[str] = []
    calls: list[str] = []
    wid = type_id(rec.name)
    if wid is None:
        return [], [f"MTA_UNSUPPORTED: {rec.name} is not a stock weapon type"]
    if rec.kind == "aim":
        return [], ["MTA_UNSUPPORTED: MTA cannot set the aim offsets of weapon.dat"]
    skill = F.table("weapon")["skills"].get(str(rec.skill if rec.skill is not None else 1), "std")
    if rec.kind == "gun" and wid not in skill_types():
        skill = "std"
    if skill == "cop":
        return [], ["MTA_UNSUPPORTED: MTA has no 'cop' skill level (the pistol line with skill 3)"]
    for key, v in changes.items():
        f = next(x for x in _fields(rec.kind) if x["key"] == key)
        prop = f.get("mta")
        if not prop or f.get("mta_set") is False:
            warn.append(f"MTA_UNSUPPORTED: setWeaponProperty cannot set {f['name']}")
            continue
        if f.get("mta_scale"):
            arg = repr(round(float(v) * f["mta_scale"], 6))
            note = f"{v} frames"
        elif f["type"] == "x":
            arg, note = f"0x{int(v):X}", f["name"]
        else:
            arg, note = (str(int(v)) if f["type"] == "i" else repr(float(v))), f["name"]
        calls.append(f'setWeaponProperty({wid}, "{skill}", "{prop}", {arg}) -- {rec.name} {note}')
    return calls, warn
