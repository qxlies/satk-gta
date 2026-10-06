"""Starter CLEO scripts (``satk script new``): ``data/script/templates/<name>.txt`` with placeholders.

``index.json`` lists every template with what it does, the options it reads (``uses``) and their defaults.
:func:`render` fills ``{{NAME}} {{SNAME}} {{CHEAT}} {{KEY}} {{MODEL}} {{X}} {{Y}} {{Z}} {{TEXT}}`` after
checking the values, so the result always assembles. Stdlib only.
"""

from __future__ import annotations

import re

from ..core.errors import SatkError
from .scm import esc_bytes, fmt_float

__all__ = ["templates", "TEMPLATES", "render", "script_name"]

#: Template names (the ``Literal`` of ``satk script new``; checked against ``index.json`` by the tests).
TEMPLATES = ("hello", "cheat", "spawn_car", "teleport", "text", "mission")
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_CHEAT = re.compile(r"^[A-Za-z0-9]{2,30}$")


def templates() -> dict[str, dict]:
    from ..core import resources

    return resources.read_json("script", "templates", "index.json")["templates"]


def script_name(name: str) -> str:
    """The 03A4 name for a file name: letters, digits and _ only, upper case, at most 7 characters."""
    s = re.sub(r"[^A-Za-z0-9_]", "", name).upper()[:7]
    return s or "SCRIPT"


def _float(v: float) -> str:
    import struct

    return fmt_float(int.from_bytes(struct.pack("<f", float(v)), "little"))


def render(template: str, name: str, *, cheat: str | None = None, key: int | None = None,
           model: int | None = None, pos: list[float] | None = None, text: str | None = None,
           warn: list[str] | None = None) -> str:
    """Text of ``template`` for a script called ``name`` (``BAD_PARAMS`` on a bad value)."""
    from ..core import resources

    idx = templates()
    if template not in idx:
        raise SatkError("BAD_PARAMS", f"unknown template {template!r}", did_you_mean=sorted(idx))
    if not _NAME.match(name):
        raise SatkError("BAD_PARAMS", f"bad script name {name!r}", hint="letters, digits, _ and -, at most 40 characters")
    t = idx[template]
    uses = set(t["uses"])
    given = {"cheat": cheat, "key": key, "model": model, "pos": pos, "text": text}
    for k, v in given.items():
        if v is not None and k not in uses and warn is not None:
            warn.append(f"UNUSED: --{k} is not used by template {template} (it uses {', '.join(t['uses'])})")
    val = {k: (given[k] if given[k] is not None else t["defaults"].get(k)) for k in uses}
    sname = script_name(name)
    subs = {"NAME": name, "SNAME": sname}
    if "cheat" in uses:
        c = str(val["cheat"])
        if not _CHEAT.match(c):
            raise SatkError("BAD_PARAMS", f"bad cheat code {c!r}", hint="2-30 letters or digits, e.g. --cheat MYCAR")
        subs["CHEAT"] = c.upper()
    if "key" in uses:
        k = int(val["key"])
        if not 1 <= k <= 254:
            raise SatkError("BAD_PARAMS", f"bad key code {k}", hint="a Windows virtual key code 1..254 (116 = F5)")
        subs["KEY"] = str(k)
    if "model" in uses:
        m = int(val["model"])
        if not 0 <= m <= 32767:
            raise SatkError("BAD_PARAMS", f"bad model id {m}", hint="satk asset find infernus --kind model")
        subs["MODEL"] = str(m)
    if "pos" in uses:
        p = list(val["pos"])
        if len(p) != 3:
            raise SatkError("BAD_PARAMS", f"--pos takes 3 numbers, got {len(p)}", hint="--pos 2495 -1687.5 13.5")
        subs["X"], subs["Y"], subs["Z"] = (_float(x) for x in p)
    if "text" in uses:
        s = str(val["text"]).replace("{{NAME}}", name)
        try:
            b = s.encode("latin-1")
        except UnicodeEncodeError:
            raise SatkError("BAD_PARAMS", "--text: use latin-1 characters (the game fonts have no others)") from None
        if len(b) > 200:
            raise SatkError("BAD_PARAMS", f"--text is {len(b)} bytes, at most 200")
        subs["TEXT"] = esc_bytes(b, '"')[1:-1]
    src = resources.read_text("script", "templates", f"{template}.txt")
    out = re.sub(r"\{\{([A-Z]+)\}\}", lambda m: subs.get(m.group(1), m.group(0)), src)
    left = re.findall(r"\{\{[A-Z]+\}\}", out)
    if left:  # pragma: no cover - index.json and the template disagree
        raise SatkError("INTERNAL", f"template {template}: unfilled {left[0]}")
    return out
