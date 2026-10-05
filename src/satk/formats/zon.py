"""Zone files (``data/map.zon``, ``data/info.zon``; also ``zone`` sections of text IPLs), SPEC §4.2. F2.

Text format (``gta.dat``: ``IPL DATA\\MAP.ZON``, ``IPL DATA\\INFO.ZON``)::

    zone
    GAN1, 0, 2222.56, -1722.33, -10.0, 2632.83, -1628.33, 200.0, 1, GAN1
    end

Fields: name, type (0 info zone, 3 map zone), min x/y/z, max x/y/z, level, GXT label. ``level`` is kept as
stored: ``map.zon`` uses 1 LS, 2 SF, 3 LV (:data:`LEVELS`); vanilla ``info.zon`` has 1 on every line.
Commas and/or spaces, ``#`` comments, ``end`` closes the section (``ide.iter_sections``).

Example::

    for z in parse_zon(read_text(resolve_ci(root, "data/info.zon"))):
        print(z["name"], z["label"], z["min"], z["max"])
"""

from __future__ import annotations

from .ide import iter_sections
from .rw import FormatError

__all__ = ["parse_zon", "LEVELS"]

#: ``level`` -> island, as used by ``map.zon``.
LEVELS = {0: "none", 1: "ls", 2: "sf", 3: "lv"}


def parse_zon(text: str, *, strict: bool = False, errors: list | None = None) -> list[dict]:
    """Zones as dicts ``{name, label, type, level, min:(x,y,z), max:(x,y,z), line}``.

    Args:
        text: file contents (latin-1 decoded).
        strict: raise ``FormatError(kind="zon", offset=<line>)`` on a malformed line.
        errors: if given, ``(line, message)`` of skipped lines are appended.
    """
    out: list[dict] = []
    for sec, f, n in iter_sections(text):
        if sec != "zone":
            continue
        try:
            if len(f) < 9:
                raise ValueError(f"zone line has {len(f)} fields, need >= 9")
            x0, y0, z0, x1, y1, z1 = (float(v) for v in f[2:8])
            out.append({"name": f[0], "label": f[9] if len(f) > 9 else None, "type": int(f[1]),
                        "level": int(f[8]), "min": (x0, y0, z0), "max": (x1, y1, z1), "line": n})
        except (ValueError, OverflowError) as e:
            if strict:
                raise FormatError("zon", n, str(e)) from None
            if errors is not None:
                errors.append((n, str(e)))
    return out
