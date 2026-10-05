"""Data-file records the way Mod Loader's std.data reads and compares them.

A Python port of the ``data_traits`` of Mod Loader 0.3 for GTA SA (``src/plugins/gta3/std.data``,
MIT, LINK/2012; see ``NOTICE-modloader.txt``). Each trait turns a data file into keyed records:

* ``handling.cfg`` - key ``(car|boat|bike|plane, handling id)`` (id case-sensitive) and
  ``(anim, group id)``; before merging, the boat/bike/plane line of an id is bundled with its
  standard line (``premerge``), so a vehicle's lines win or lose together;
* ``carcols.dat`` - ``col`` lines keyed by their index, ``car``/``car4`` lines by model name;
* ``*.ide`` - model sections keyed by model ID, ``txdp`` by child TXD, ``2dfx`` by (ID, position, type);
* ``gta.dat``/``default.dat`` - by (directive, path);
* ``object.dat`` - by model name (stops at a ``*`` line).

Other merged files fall back to :class:`LineTrait` (a line is its own key: added/removed lines only).

Mod Loader's reading rules that matter for the result (all ported):

* every line goes through ``trim_config_line``: ``#`` and ``;`` start a comment anywhere, commas
  and control characters become spaces - so ``;the end`` is just a comment for Mod Loader and
  lines after it are still read (the game itself stops there);
* a line is kept when its leading fields parse; extra trailing tokens are ignored;
* the first line of a key wins inside one merged store (``std::map::emplace``);
* numbers compare as 32-bit floats with a relative epsilon (``FLT_EPSILON``), strings exactly
  (model and texture names case-insensitively).

``engine=True`` reads a file the way the game does when Mod Loader only overrides it (one mod,
no readme lines): the last line of a key wins and ``handling.cfg`` stops at ``;the end``.

Stdlib only.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field, replace

__all__ = [
    "Rec", "Store", "Trait", "HandlingTrait", "CarcolsTrait", "IdeTrait", "GtaDatTrait", "ObjectTrait", "LineTrait",
    "trait_for", "trim_config_line", "cells_equal", "recs_equal", "f32", "feq", "FLT_EPS", "PORTED",
]

FLT_EPS = 2.0 ** -23
_NUM_INT = re.compile(r"^[+-]?\d+$")
_NUM_FLOAT = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_HEX = re.compile(r"^[+-]?(?:0[xX])?[0-9A-Fa-f]+$")


def trim_config_line(line: str, remove_separators: bool = True) -> str:
    """``datalib::gta3::trim_config_line``: cut ``#``/``;`` comments, turn ``,`` and control chars into spaces, trim."""
    out = []
    for ch in line:
        if ch in "#;":
            break
        if ch <= " " or (ch == "," and remove_separators):
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out).strip(" ")


def f32(x: float) -> float:
    """``x`` rounded to a 32-bit float (overflow -> +-inf)."""
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def feq(a: float, b: float) -> bool:
    """``floating_point_comparer::relative_epsilon<float>``: equal within FLT_EPSILON (absolute or relative)."""
    a, b = f32(a), f32(b)
    if a == b:
        return True
    d = abs(a - b)
    return d <= FLT_EPS or d <= max(abs(a), abs(b)) * FLT_EPS


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def cells_equal(a: tuple, b: tuple) -> bool:
    """Element-wise equality of parsed values (numbers with :func:`feq` when one side is a float)."""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if _is_num(x) and _is_num(y):
            if isinstance(x, float) or isinstance(y, float):
                if not feq(float(x), float(y)):
                    return False
            elif x != y:
                return False
        elif isinstance(x, tuple) and isinstance(y, tuple):
            if not cells_equal(x, y):
                return False
        elif x != y:
            return False
    return True


@dataclass(frozen=True, slots=True)
class Rec:
    """One keyed record of a data file.

    Attributes:
        key: hashable key (trait specific).
        section: section the line belongs to (``car``, ``cars``, ``IDE`` ...; ``""`` if none).
        cells: parsed values compared by :func:`recs_equal`.
        line: 1-based line number in its file.
        text: the trimmed line as Mod Loader sees it.
        parts: bundled records (handling: boat, bike, plane lines of the same id, or ``None``).
    """

    key: tuple
    section: str
    cells: tuple
    line: int
    text: str
    parts: tuple = ()


def recs_equal(a: Rec | None, b: Rec | None) -> bool:
    if a is None or b is None:
        return a is b
    if a.section != b.section or not cells_equal(a.cells, b.cells) or len(a.parts) != len(b.parts):
        return False
    return all(recs_equal(x, y) for x, y in zip(a.parts, b.parts))


@dataclass
class Store:
    """A parsed data file (or the readme store): records in insertion order.

    Attributes:
        label: who provides it (``default`` | mod name | ``readme``).
        origin: display path (``data/handling.cfg`` or ``<mod>/<path>``).
        is_default: the game's own file (dominance never prefers its values).
        recs: key -> record.
        failed: line numbers that did not parse (dropped, as Mod Loader does).
        dups: lines whose key was already present.
    """

    label: str
    origin: str
    is_default: bool
    recs: dict = field(default_factory=dict)
    failed: list = field(default_factory=list)
    dups: int = 0


# --------------------------------------------------------------------------- typed field specs


def _cell(tok: str, typ: str):
    """Convert one token by type code; ``None`` when it does not parse.

    Codes: ``f`` float, ``i`` int, ``x`` hex int, ``c`` single char, ``s`` string, ``S`` string
    (case-insensitive), ``F`` float or string (``fixtok`` - the ``0.1s`` of RCRAIDER), ``n`` number
    or string (auto, for loosely typed lines).
    """
    if typ == "f":
        return f32(float(tok)) if _NUM_FLOAT.match(tok) else None
    if typ == "i":
        return int(tok) if _NUM_INT.match(tok) else None
    if typ == "x":
        return int(tok, 16) if _HEX.match(tok) else None
    if typ == "c":
        return tok if len(tok) == 1 else None
    if typ == "s":
        return tok
    if typ == "S":
        return tok.lower()
    if typ == "F":
        return f32(float(tok)) if _NUM_FLOAT.match(tok) else tok
    return _auto(tok)


def _auto(tok: str):
    if _NUM_INT.match(tok):
        return int(tok)
    if _NUM_FLOAT.match(tok):
        return f32(float(tok))
    return tok


def _parse_spec(toks: list[str], spec: str) -> tuple | None:
    """Parse the leading tokens by ``spec``; ``None`` if a required field is missing or malformed."""
    if len(toks) < len(spec):
        return None
    out = []
    for tok, typ in zip(toks, spec):
        v = _cell(tok, typ)
        if v is None:
            return None
        out.append(v)
    return tuple(out)


# --------------------------------------------------------------------------- traits


class Trait:
    """Base trait: how a data file is read, keyed, compared, ordered and printed."""

    name = ""
    title = ""
    ported = True
    #: SID kind for targets (``handling:INFERNUS``); ``""`` -> ``<file>:<key>``.
    sid_kind = ""
    sections: tuple[str, ...] = ()
    #: each line selects its own section (gta.dat, handling.cfg) instead of header/``end`` blocks.
    per_line = False

    def parse(self, text: str, *, label: str = "default", origin: str = "", is_default: bool = False,
              engine: bool = False) -> Store:
        st = Store(label, origin, is_default)
        state: dict = {}
        section: str | None = None
        for n, raw in enumerate(text.splitlines(), 1):
            if engine and self.engine_eof(raw):
                break
            line = trim_config_line(raw)
            if not line:
                continue
            if self.sections and not self.per_line:
                if section is None:
                    section = line if line in self.sections else None
                    continue
                if line == "end":
                    section = None
                    continue
            if self.stop(line, state):
                break
            rec = self.parse_line(line, n, section, state)
            if rec is None:
                st.failed.append(n)
                continue
            if rec.key in st.recs:
                st.dups += 1
                if not engine:
                    continue  # std::map::emplace keeps the first line of a key
                del st.recs[rec.key]
            st.recs[rec.key] = rec
        return st

    def engine_eof(self, raw: str) -> bool:
        """The game stops reading at this raw line (override mode only)."""
        return False

    def stop(self, line: str, state: dict) -> bool:
        """Mod Loader's ``has_eof_string``: everything from this trimmed line on is ignored."""
        return False

    def parse_line(self, line: str, n: int, section: str | None, state: dict) -> Rec | None:
        raise NotImplementedError

    def premerge(self, recs: dict) -> dict:
        """Transform a store's records before merging (default: unchanged)."""
        return recs

    def final_key(self, key: tuple) -> tuple:
        """Key a raw record has after :meth:`premerge`."""
        return key

    def target(self, key: tuple) -> str:
        return f"{self.name}:{'/'.join(str(k) for k in key[1:]) if len(key) > 1 else key[0]}"

    def order(self, key: tuple, first_seen: int) -> tuple:
        return (first_seen,)

    def render(self, rec: Rec) -> str:
        return "\n".join([rec.text] + [p.text for p in rec.parts if p is not None])

    def readme_rec(self, line: str, n: int, models: frozenset[str]) -> Rec | None:
        """A readme line (already trimmed) as a record of this file, if this trait's readme reader takes it."""
        return None


# ---- handling.cfg

#: ``data_slice`` fields of handling.cfg for GTA SA (``handling_traits``).
_H_MAIN = "s" + "fff" + "fff" + "i" + "fff" + "i" + "fff" + "cc" + "ff" + "c" + "fff" + "f" + "ffff" + "ff" + "i" + "xx" + "cc" + "i"
_H_BOAT = "cs" + "ff" + "fffff" + "fff" + "fff" + "f"
_H_BIKE = "cs" + "f" * 15
_H_PLANE = "cs" + "f" * 11 + "ff" + "F" + "f" + "f" + "f" + "f" + "fff"
_H_ANIM = "ciii" + "i" * 18 + "f" * 13 + "i"
_H_PREFIX = {"%": ("boat", _H_BOAT), "!": ("bike", _H_BIKE), "$": ("plane", _H_PLANE), "^": ("anim", _H_ANIM)}
#: order of handling sections in the merged file (main, boat, bike, plane, anim).
_H_ORDER = {"car": 0, "boat": 1, "bike": 2, "plane": 3, "anim": 4}


class HandlingTrait(Trait):
    name = "handling.cfg"
    title = "vehicle handling"
    sid_kind = "handling"
    per_line = True

    def engine_eof(self, raw: str) -> bool:
        return raw.strip().lower().startswith(";the end")

    def parse_line(self, line: str, n: int, section: str | None, state: dict) -> Rec | None:
        toks = line.split()
        sec, spec = _H_PREFIX.get(line[0], ("car", _H_MAIN))
        if sec != "car" and len(toks[0]) > 1:      # "%COASTG ..." -> "%", "COASTG"
            toks = [toks[0][0], toks[0][1:]] + toks[1:]
        cells = _parse_spec(toks, spec)
        if cells is None:
            return None
        if sec == "car":
            key = ("car", cells[0])
        elif sec == "anim":
            key = ("anim", cells[1])
        else:
            key = (sec, cells[1])
        return Rec(key, sec, cells, n, " ".join(toks))

    def premerge(self, recs: dict) -> dict:
        """Bundle each standard line with the boat/bike/plane line of the same id; anim lines stay alone."""
        out: dict = {}
        for k, r in recs.items():
            if k[0] == "car":
                parts = tuple(recs.get((s, k[1])) for s in ("boat", "bike", "plane"))
                out[("veh", k[1])] = replace(r, key=("veh", k[1]), section="veh", parts=parts)
        for k, r in recs.items():
            if k[0] == "anim":
                out[k] = r
        return out

    def final_key(self, key: tuple) -> tuple:
        return key if key[0] == "anim" else ("veh", key[1])

    def target(self, key: tuple) -> str:
        if key[0] == "anim":
            return f"handling:^{key[1]}"
        return f"handling:{key[1]}"

    def order(self, key: tuple, first_seen: int) -> tuple:
        return (1 if key[0] == "anim" else 0, first_seen)

    def readme_rec(self, line: str, n: int, models: frozenset[str]) -> Rec | None:
        rec = self.parse_line(line, n, None, {})
        if rec is None:
            return None
        # a readme line must look like data, not prose: the standard line needs 36 fields exactly
        toks = rec.text.split()
        want = {"car": _H_MAIN, "boat": _H_BOAT, "bike": _H_BIKE, "plane": _H_PLANE, "anim": _H_ANIM}[rec.section]
        return rec if len(toks) == len(want) else None


# ---- carcols.dat


_CARCOLS_README = re.compile(r"^(\w+)\s*(?:((?: (?: \d+){2})+)|((?: (?: \d+){4})+))\s*$", re.A)


class CarcolsTrait(Trait):
    name = "carcols.dat"
    title = "vehicle colours"
    sid_kind = "carcols"
    sections = ("col", "car", "car4")

    def parse_line(self, line: str, n: int, section: str | None, state: dict) -> Rec | None:
        if section == "col":
            toks = line.replace(".", " ").split()      # R* wrote '77.93,96' in one colour line
            cells = _parse_spec(toks, "iii")
            if cells is None:
                return None
            idx = state.get("col", 0)
            state["col"] = idx + 1
            return Rec(("col", idx), "col", cells, n, line)
        toks = line.split()
        width = 2 if section == "car" else 4
        nums = []
        for t in toks[1:]:
            if not _NUM_INT.match(t):
                break
            nums.append(int(t))
        nums = nums[: len(nums) // width * width]
        return Rec(("car", toks[0].lower()), section or "car", (toks[0].lower(), tuple(nums)), n, line)

    def target(self, key: tuple) -> str:
        return f"carcols:col/{key[1]}" if key[0] == "col" else f"carcols:{key[1]}"

    def order(self, key: tuple, first_seen: int) -> tuple:
        return (0 if key[0] == "col" else 1, key[1] if key[0] == "col" else 0, first_seen)

    def readme_rec(self, line: str, n: int, models: frozenset[str]) -> Rec | None:
        m = _CARCOLS_README.fullmatch(line)
        if not m or m.group(1).lower() not in models:
            return None
        return self.parse_line(line, n, "car" if m.group(2) else "car4", {})


# ---- *.ide


_IDE_SECTIONS = ("objs", "tobj", "hier", "anim", "weap", "cars", "peds", "txdp", "2dfx", "path")
#: minimum tokens per section (GTA SA data_slice of ide_traits).
_IDE_MIN = {"objs": 5, "tobj": 7, "hier": 3, "anim": 6, "weap": 6, "cars": 11, "peds": 14, "txdp": 2, "2dfx": 5}


def _fregex(fmt: str) -> re.Pattern:
    """``make_fregex``: ``%d %f %s %x %c %{a|b}`` and spaces (``\\s+``) to a regex, trailing ``\\s*``."""
    out: list[str] = []
    i = 0
    was_space = False
    pats = {"x": r"[+-]?(?:0[xX])?[\dA-Fa-f]+", "X": r"[+-]?(?:0[xX])?[\dA-Fa-f]+", "d": r"[+-]?\d+",
            "f": r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", "s": r"\S+", "c": r"."}
    while i < len(fmt):
        ch = fmt[i]
        if ch == " ":
            if not was_space:
                out.append(r"\s+")
            was_space = True
            i += 1
            continue
        was_space = False
        if ch == "%" and i + 1 < len(fmt):
            c = fmt[i + 1]
            if c in pats:
                out.append(pats[c])
                i += 2
                continue
            if c == "%":
                out.append("%")
                i += 2
                continue
            if c == "{":
                j = fmt.index("}", i + 2)
                out.append("(?:" + fmt[i + 2:j] + ")")
                i = j + 1
                continue
        out.append(ch)
        i += 1
    out.append(r"\s*")
    return re.compile("".join(out), re.A)


_README_CARS = _fregex(
    "^%d %s %s %{car|mtruck|quad|heli|f_heli|plane|f_plane|boat|train|bike|bmx|trailer} %s %s %s "
    "%{normal|special|poorfamily|richfamily|executive|worker|big|taxi|moped|motorbike|leisureboat|workerboat|"
    "bicycle|ignore} %d %d %x(?: %d)?(?: %f)?(?: %f)?(?: %d)?$")
_README_VEHMODS = _fregex(
    r"^%d %{hydralics|stereo|wheel_\w+|nto_\w+|bnt_\w+|chss_\w+|exh_\w+|bntl_\w+|bntr_\w+|spl_\w+|wg_l_\w+|wg_r_\w+|"
    r"fbb_\w+|bbb_\w+|lgt_\w+|rf_\w+|fbmp_\w+|rbmp_\w+|misc_a_\w+|misc_b_\w+|misc_c_\w+} %s %d %d$")
_README_PEDS = _fregex(
    r"^%d %s %s %{CIVMALE|CIVFEMALE|COP|GANG\d+|PLAYER\d+|PLAYER_NETWORK|PLAYER_UNUSED|DEALER|MEDIC|EMERGENCY|"
    r"FIREMAN|CRIMINAL|BUM|PROSTITUTE|SPECIAL|MISSION\d+} %{STAT_\w+} %s %x %x %s %d %d %{PED_TYPE_\w+} "
    r"%{VOICE_\w+} %{VOICE_\w+}$")
#: IDE file names that accept readme lines, and the section they accept (``query_readme_data``).
IDE_README_FILES = {"vehicles.ide": "cars", "peds.ide": "peds", "veh_mods.ide": "objs"}


class IdeTrait(Trait):
    name = "*.ide"
    title = "item definitions"
    sid_kind = "model"
    sections = _IDE_SECTIONS

    def parse_line(self, line: str, n: int, section: str | None, state: dict) -> Rec | None:
        if section is None or section == "path":     # 'path' is GTA III only: SA lines there fail
            return None
        toks = line.split()
        if len(toks) < _IDE_MIN[section]:
            return None
        if section == "txdp":
            return Rec(("txdp", toks[0].lower()), section, (toks[0].lower(), toks[1].lower()), n, line)
        if not _NUM_INT.match(toks[0]):
            return None
        mid = int(toks[0])
        if section == "2dfx":
            pos = tuple(_cell(t, "f") for t in toks[1:4])
            if None in pos or not _NUM_INT.match(toks[4]):
                return None
            cells = (mid, pos, int(toks[4])) + tuple(_auto(t) for t in toks[5:])
            return Rec(("2dfx", mid, pos, int(toks[4])), section, cells, n, line)
        cells = (mid, toks[1].lower(), toks[2].lower()) + tuple(_auto(t) for t in toks[3:])
        return Rec(("id", mid), section, cells, n, line)

    def target(self, key: tuple) -> str:
        if key[0] == "id":
            return f"model:{key[1]}"
        if key[0] == "txdp":
            return f"txdp:{key[1]}"
        return f"2dfx:{key[1]}@{','.join(f'{v:g}' for v in key[2])}/{key[3]}"

    def order(self, key: tuple, first_seen: int) -> tuple:
        return (first_seen,)

    def readme_section(self, line: str) -> str | None:
        if _README_CARS.fullmatch(line):
            return "cars"
        if _README_VEHMODS.fullmatch(line):
            return "objs"
        if _README_PEDS.fullmatch(line):
            return "peds"
        return None

    def readme_rec(self, line: str, n: int, models: frozenset[str]) -> Rec | None:
        sec = self.readme_section(line)
        return self.parse_line(line, n, sec, {}) if sec else None


# ---- gta.dat / default.dat

_GTADAT_SECTIONS = ("IMG", "CDIMAGE", "TEXDICTION", "MODELFILE", "IDE", "COLFILE", "MAPZONE", "IPL", "HIERFILE",
                    "SPLASH", "EXIT")
_GTADAT_README = re.compile(r"^(?:IDE|IPL|IMG|CDIMAGE|COLFILE \d|TEXDICTION|MODELFILE|HIERFILE) \w+[\\/].*"
                            r"\.(?:IDE|IPL|ZON|IMG|COL|TXD|DFF)\s*$", re.I | re.A)


def norm_path(p: str) -> str:
    """Mod Loader's ``NormalizePath`` for comparisons, in satk's form: lower case, forward slashes."""
    return p.strip().replace("\\", "/").lower()


class GtaDatTrait(Trait):
    name = "gta.dat"
    title = "level file"
    per_line = True

    def __init__(self, name: str = "gta.dat"):
        self.name = name

    def parse_line(self, line: str, n: int, section: str | None, state: dict) -> Rec | None:
        sec = next((s for s in _GTADAT_SECTIONS if line.startswith(s)), None)
        if sec is None:
            return None
        rest = line[len(sec):].lstrip(" ")
        level = 0
        if sec == "COLFILE":
            first, _, rest = rest.partition(" ")
            level = int(first) if _NUM_INT.match(first) else 0
            rest = rest.lstrip(" ")
        if not rest:
            return None
        path = norm_path(rest)
        return Rec((sec, path), sec, (level,), n, line)

    def target(self, key: tuple) -> str:
        return f"{self.name}:{key[0]} {key[1]}"

    def order(self, key: tuple, first_seen: int) -> tuple:
        return (_GTADAT_SECTIONS.index(key[0]), first_seen)

    def readme_rec(self, line: str, n: int, models: frozenset[str]) -> Rec | None:
        if self.name != "gta.dat" or not _GTADAT_README.fullmatch(line):
            return None
        return self.parse_line(line, n, None, {})


# ---- object.dat


class ObjectTrait(Trait):
    name = "object.dat"
    title = "object physics"
    sid_kind = "object"

    def stop(self, line: str, state: dict) -> bool:
        return line.startswith("*")

    def parse_line(self, line: str, n: int, section: str | None, state: dict) -> Rec | None:
        toks = line.split()
        if len(toks) < 13:
            return None
        cells = (toks[0].lower(),) + tuple(_auto(t) for t in toks[1:])
        if not all(_is_num(c) for c in cells[1:11]):
            return None
        return Rec(("obj", toks[0].lower()), "", cells, n, line)

    def target(self, key: tuple) -> str:
        return f"object.dat:{key[1]}"


# ---- fallback


class LineTrait(Trait):
    """Unported merge rules: every distinct trimmed line is a record (diffs show added/removed lines)."""

    ported = False

    def __init__(self, name: str):
        self.name = name

    def parse_line(self, line: str, n: int, section: str | None, state: dict) -> Rec | None:
        norm = " ".join(line.split())
        return Rec(("line", norm), "", (norm,), n, line)

    def target(self, key: tuple) -> str:
        return f"{self.name}:{key[1][:60]}"


_TRAITS: dict[str, Trait] = {}
#: Data files whose merge rules are ported (keys/values as Mod Loader computes them).
PORTED = ("handling.cfg", "carcols.dat", "*.ide", "gta.dat", "default.dat", "object.dat")


def trait_for(fs_name: str) -> Trait:
    """Trait of a data file: ``handling.cfg``, ``carcols.dat``, ``x.ide`` (any IDE), ``gta.dat`` ..."""
    n = fs_name.lower().rsplit("/", 1)[-1]
    if n.endswith(".ide"):
        n = "*.ide"
    t = _TRAITS.get(n)
    if t is None:
        t = {"handling.cfg": HandlingTrait, "carcols.dat": CarcolsTrait, "*.ide": IdeTrait,
             "object.dat": ObjectTrait}.get(n, lambda: None)()
        if t is None:
            t = GtaDatTrait(n) if n in ("gta.dat", "default.dat") else LineTrait(n)
        _TRAITS[n] = t
    return t
