"""Field tables of the data files ``satk.addon`` reads and patches (``data/addon/*.json``).

:func:`table` loads ``handling``, ``weapon`` or ``peds``; :func:`find_field` resolves a user's field name
(satk key ``mass``, editor name ``fMass``, MTA name ``tractionMultiplier``, column letter ``B``, any case)
to ``(kind, field)``; :func:`flag_names` decodes hex bit fields with the table's flag list.

Stdlib only.
"""

from __future__ import annotations

import difflib
from functools import lru_cache

from ..core import resources
from ..core.errors import SatkError

__all__ = ["table", "kinds", "find_field", "flag_names", "flag_bits", "all_names", "norm"]

#: Record kinds per file (order = order in the answer).
_KINDS = {"handling": ("car", "bike", "boat", "flying"), "weapon": ("gun", "melee", "aim"),
          "peds": ("peds", "pedstats")}


@lru_cache(maxsize=None)
def table(file: str) -> dict:
    """The field table of ``file`` (``handling``, ``weapon``, ``peds``)."""
    if file not in _KINDS:
        raise SatkError("BAD_PARAMS", f"no field table for {file!r}", data={"files": list(_KINDS)})
    return resources.read_json("addon", f"{file}.json")


def kinds(file: str) -> tuple[str, ...]:
    return _KINDS[file]


def norm(name: str) -> str:
    """Comparison form of a field name: lower case without ``_ . -`` and spaces."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _names(f: dict) -> list[str]:
    out = [f["key"], f.get("name", "")] + list(f.get("aliases", ()))
    if f.get("mta") and f["mta"] != "centerOfMass":
        out.append(f["mta"])
    return [n for n in out if n]


def all_names(file: str, kind: str | None = None) -> list[str]:
    """Every accepted name (for did-you-mean)."""
    t = table(file)
    out: list[str] = []
    for k in kinds(file):
        if kind is None or k == kind:
            for f in t[k]:
                out += _names(f)
    return out


def find_field(file: str, name: str, kind: str | None = None) -> tuple[str, dict]:
    """``(kind, field dict)`` for a field name; ``kind`` restricts the search (``car``, ``gun`` ...).

    Column letters (``B``, ``aa``) are accepted when ``kind`` is given (they repeat across kinds).
    """
    t = table(file)
    want = norm(name)
    cands = [k for k in kinds(file) if kind is None or k == kind]
    for k in cands:
        for f in t[k]:
            if any(norm(n) == want for n in _names(f)):
                return k, f
    if kind is not None:
        for f in t[kind]:
            if f.get("col") == name.strip():
                return kind, f
    close = difflib.get_close_matches(name, all_names(file, kind), n=4, cutoff=0.6)
    raise SatkError("NOT_FOUND", f"no {file} field {name!r}" + (f" in {kind} lines" if kind else ""),
                    hint=f"satk data explain {file}", did_you_mean=close)


def flag_bits(file: str, which: str) -> list[dict]:
    return table(file)[which]


def flag_names(file: str, which: str, value: int) -> list[str]:
    """Names of the set bits of ``value`` (unknown bits as ``bit<N>``)."""
    names = {b["bit"]: b["name"] for b in table(file)[which]}
    return [names.get(bit, f"bit{bit}") for bit in range(32) if value >> bit & 1]
