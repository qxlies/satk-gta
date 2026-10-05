"""Item definitions (``*.ide``), SPEC §4.2 ``satk.formats.ide``. Stdlib only.

Text format: sections ``objs tobj anim cars peds weap hier txdp 2dfx path ...`` closed by ``end``;
fields separated by commas and/or spaces (the engine turns ``,`` into spaces), ``#`` comments.
Pitfall #9: the old ``objs`` form with a mesh count (``320, airtrain_vlo, generic, 1, 2000, 0``)
and the 7/8-field variants with several draw distances; duplicate ``hier`` names.

Example::

    defs, txdp, fx = parse_ide(read_text(path))
    cars = [d for d in defs if d.sec == "cars"]
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .rw import FormatError

__all__ = ["IdeRec", "parse_ide", "MODEL_SECTIONS", "iter_sections"]

#: Sections that define a model ID (``model.sec`` in the index DDL).
MODEL_SECTIONS = ("objs", "tobj", "anim", "cars", "peds", "weap", "hier")
_KNOWN = frozenset(MODEL_SECTIONS + ("txdp", "2dfx", "path", "inst", "cull", "occl", "grge", "enex",
                                      "pick", "jump", "tcyc", "auzo", "mult", "zone", "mzon"))


@dataclass(frozen=True, slots=True)
class IdeRec:
    """A model definition. ``line`` is 1-based; ``extra`` holds section-specific fields."""

    sec: str
    line: int
    id: int
    name: str
    txd: str | None
    draw: float | None
    flags: int | None
    time_on: int | None
    time_off: int | None
    anim: str | None
    extra: dict = field(default_factory=dict)


def iter_sections(text: str):
    """Yield ``(section, fields, line_no)`` for GTA text data files (IDE and text IPL).

    A section starts with a line whose first token is a known section name and ends at ``end``.
    """
    sec: str | None = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].replace(",", " ").strip()
        if not line:
            continue
        fields = line.split()
        if sec is None:
            # like the engine: only known headers open a section, anything else is ignored
            if fields[0].lower() in _KNOWN:
                sec = fields[0].lower()
            continue
        if fields[0].lower() == "end":
            sec = None
            continue
        yield sec, fields, n


def _int(s: str) -> int:
    return int(s, 0) if s[:2].lower() == "0x" else int(float(s)) if "." in s else int(s)


def _hex(s: str) -> int:
    return int(s, 16)


def _num(s: str):
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


def _objs(sec: str, f: list[str], n: int) -> IdeRec:
    # objs: id name txd [meshes] draw[1..3] flags ; tobj adds time_on time_off
    tail = 2 if sec == "tobj" else 0
    body = f[: len(f) - tail] if tail else f
    nb = len(body)
    if nb < 5:
        raise ValueError(f"{sec}: {len(f)} fields")
    extra: dict = {}
    if nb == 5:
        draw = float(body[3])
    else:
        meshes = int(body[3])
        draws = [float(x) for x in body[4:-1]]
        if not draws:
            raise ValueError(f"{sec}: no draw distance")
        draw = draws[0]
        extra["meshes"] = meshes
        if len(draws) > 1:
            extra["draws"] = draws
    flags = _int(body[-1])
    ton = toff = None
    if tail:
        if len(f) < 7:
            raise ValueError("tobj: time fields missing")
        ton, toff = int(f[-2]), int(f[-1])
    return IdeRec(sec, n, int(f[0]), f[1], f[2], draw, flags, ton, toff, None, extra)


_CARS_EXTRA = ("type", "handling", "gxt", "anims", "class", "freq", "flags", "comprules",
               "wheel_id", "wheel_scale_f", "wheel_scale_r", "wheel_upgrade")
_PEDS_EXTRA = ("pedtype", "stat", "animgroup", "cars_mask", "flags", "ifp", "radio1", "radio2",
               "voice_type", "voice1", "voice2")


def _rec(sec: str, f: list[str], n: int) -> IdeRec:
    if sec in ("objs", "tobj"):
        return _objs(sec, f, n)
    mid, name = int(f[0]), f[1]
    txd = f[2] if len(f) > 2 else None
    if sec == "anim":  # id name txd ifp draw flags
        if len(f) < 6:
            raise ValueError(f"anim: {len(f)} fields")
        return IdeRec(sec, n, mid, name, txd, float(f[4]), _int(f[5]), None, None, f[3], {})
    if sec == "weap":  # id name txd anim meshes draw flags
        if len(f) < 7:
            raise ValueError(f"weap: {len(f)} fields")
        return IdeRec(sec, n, mid, name, txd, float(f[5]), _int(f[6]), None, None, f[3], {"meshes": int(f[4])})
    if sec == "cars":
        if len(f) < 11:
            raise ValueError(f"cars: {len(f)} fields")
        extra = {k: _num(v) for k, v in zip(_CARS_EXTRA, f[3:])}
        extra["comprules"] = f[10]
        flags = _int(f[9])
        return IdeRec(sec, n, mid, name, txd, None, flags, None, None, None, extra)
    if sec == "peds":
        if len(f) < 8:
            raise ValueError(f"peds: {len(f)} fields")
        extra = {k: (v if k in ("cars_mask", "flags") else _num(v)) for k, v in zip(_PEDS_EXTRA, f[3:])}
        ifp = f[8] if len(f) > 8 and f[8].lower() != "null" else None
        return IdeRec(sec, n, mid, name, txd, None, _hex(f[7]), None, None, ifp, extra)
    # hier: id name txd [...]
    if len(f) < 2:
        raise ValueError(f"{sec}: {len(f)} fields")
    return IdeRec(sec, n, mid, name, txd, None, None, None, None, None, {"fields": f[3:]} if len(f) > 3 else {})


def parse_ide(
    text: str, *, strict: bool = False, errors: list | None = None
) -> tuple[list[IdeRec], list[tuple[str, str]], list[dict]]:
    """Parse an IDE file.

    Args:
        text: file contents (decode with latin-1, see ``dat.read_text``).
        strict: raise ``FormatError(kind="ide", offset=<line>)`` on a malformed line instead of
            skipping it.
        errors: if given, ``(line, message)`` of skipped lines are appended.

    Returns:
        ``(defs, txdp, fx2d)``: model definitions in file order, ``(child, parent)`` TXD pairs,
        and the ``2dfx`` lines as ``{"line", "id", "pos", "fields"}`` dicts.
    """
    defs: list[IdeRec] = []
    txdp: list[tuple[str, str]] = []
    fx: list[dict] = []
    for sec, f, n in iter_sections(text):
        try:
            if sec in MODEL_SECTIONS:
                defs.append(_rec(sec, f, n))
            elif sec == "txdp":
                if len(f) < 2:
                    raise ValueError("txdp: need child and parent")
                txdp.append((f[0], f[1]))
            elif sec == "2dfx":
                if len(f) < 4:
                    raise ValueError("2dfx: need id x y z")
                fx.append({"line": n, "id": int(f[0]), "pos": (float(f[1]), float(f[2]), float(f[3])),
                           "fields": f[4:]})
            # other sections (legacy 'path', unknown) are ignored
        except (ValueError, IndexError, OverflowError) as e:
            if strict:
                raise FormatError("ide", n, str(e)) from None
            if errors is not None:
                errors.append((n, str(e)))
    return defs, txdp, fx
