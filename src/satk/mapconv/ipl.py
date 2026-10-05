"""IPL placements for ``satk.mapconv``: text/binary IPL -> scene, scene -> text IPL ``inst``.

Reading uses :mod:`satk.formats.ipl` and applies the engine's own rotation rule
(:func:`satk.mapconv.rot.ipl_euler`): the file quaternion is conjugated, and a placement with
``|qx|, |qy| <= 0.05`` keeps only its heading, so the converted Euler angles show what the game shows.
``interior`` = area code (low byte), ``iflags`` = ``interior >> 8``, ``lod`` = index in the same file.

Writing produces a text IPL with one ``inst`` section: ``id, name, interior, x, y, z, qx, qy, qz, qw,
lod`` where the quaternion is the conjugate of the world rotation (:func:`satk.mapconv.rot.ipl_quat`).
Objects tilted by less than about 5.73 degrees would lose the tilt in the game (``TILT_LOST``);
``tilt="dontstream"`` sets the ``dont_stream`` flag on those whose ``qx`` and ``qy`` are both non-zero,
which makes the loader keep the full rotation (the flag's other effects are not documented).
"""

from __future__ import annotations

import re

from ..formats.ipl import BNRY_HEADER_SIZE, area, iflags, parse_ipl_binary, parse_ipl_text
from .rot import DONT_STREAM, TILT_EPS, ipl_euler, ipl_heading_only, ipl_quat
from .scene import MapObject, Scene, num

__all__ = ["read_ipl", "write_ipl", "is_binary_ipl"]

_NAME_BAD = re.compile(r"[\s,#]+")  # an IPL name is one whitespace-free token


def is_binary_ipl(data: bytes) -> bool:
    return data[:4] == b"bnry"


def read_ipl(data: bytes, source: str = "", names: dict[int, str] | None = None) -> Scene:
    """Text or binary (``bnry``) IPL -> :class:`Scene`. ``names`` fills model names of binary IPLs."""
    sc = Scene(source=source, fmt="ipl", encoding="latin-1")
    binary = is_binary_ipl(data)
    if binary:
        if len(data) < BNRY_HEADER_SIZE:
            raise ValueError("truncated binary IPL header")
        insts, cars = parse_ipl_binary(data)
        if cars:
            sc.skip("cars", len(cars))
    else:
        errors: list = []
        insts, items = parse_ipl_text(data.decode("latin-1"), errors=errors)
        for sec, lst in sorted(items.items()):
            sc.skip(sec, len(lst))
        for line, msg in errors:
            sc.issue("warn", "BAD_LINE", f"line {line}", f"skipped: {msg}")
    dropped = 0
    for it in insts:
        fl = iflags(it.interior)
        rot, lost = ipl_euler(it.q, fl)
        dropped += lost
        name = it.name or (names or {}).get(it.model_id)
        sc.objects.append(MapObject(
            model=it.model_id, pos=tuple(float(c) for c in it.pos),  # type: ignore[arg-type]
            rot=tuple(round(a, 4) + 0.0 for a in rot), interior=area(it.interior), world=0,  # type: ignore[arg-type]
            lod=it.lod, iflags=fl, name=name))
    n = len(sc.objects)
    external = [i for i, o in enumerate(sc.objects) if o.lod >= 0 and (binary or o.lod >= n)]
    for i in external:
        sc.objects[i].lod = -1
    if external:
        sc.issue("info", "LOD_EXTERNAL", "file",
                 f"{len(external)} lod links point outside this file (a bnry/_stream IPL links into its text IPL); "
                 "dropped")
    if dropped:
        sc.issue("info", "TILT_IGNORED", "file",
                 f"{dropped} inst have a small tilt (|qx|,|qy| <= {TILT_EPS}) the engine ignores; "
                 "converted heading-only, as the game shows them")
    return sc


def write_ipl(sc: Scene, names: dict[int, str] | None = None, tilt: str = "warn", header: str | None = None) -> str:
    """Text IPL (``inst`` section) of a scene; adds ``TILT_LOST``/``INTERIOR_ALL`` issues to ``sc``."""
    names = names or {}
    out: list[str] = []
    if header:
        out += [f"# {h}" for h in header.splitlines()]
    out.append("inst")
    lost: list[int] = []
    for i, o in enumerate(sc.objects):
        q = ipl_quat(*o.rot)
        fl = o.iflags
        tilted = abs(o.rot[0]) > 1e-6 or abs(o.rot[1]) > 1e-6
        if tilted and ipl_heading_only(q, fl):
            if tilt == "dontstream" and q[0] != 0.0 and q[1] != 0.0:
                fl |= DONT_STREAM
            else:
                lost.append(i)
        area_code = 0 if o.interior < 0 else o.interior & 0xFF
        name = _NAME_BAD.sub("_", names.get(o.model) or o.name or f"model{o.model}")
        q_txt = ", ".join(num(c, 8) for c in q)
        out.append(f"{o.model}, {name}, {area_code | (fl << 8)}, {num(o.pos[0])}, {num(o.pos[1])}, {num(o.pos[2])}, "
                   f"{q_txt}, {o.lod}")
    out.append("end")
    if lost:
        shown = ", ".join(f"obj {i}" for i in lost[:10]) + (" ..." if len(lost) > 10 else "")
        sc.issue("warn", "TILT_LOST", "file",
                 f"{len(lost)} objects are tilted less than ~5.73 deg: the game keeps only their heading ({shown}); "
                 "keep them dynamic or use --tilt dontstream")
    n_all = sum(1 for o in sc.objects if o.interior < 0)
    if n_all:
        sc.issue("info", "INTERIOR_ALL", "file",
                 f"{n_all} objects were in every interior (-1): written with area 0 (13 = visible in all areas)")
    return "\n".join(out) + "\n"
