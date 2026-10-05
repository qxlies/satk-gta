"""Model ids and names defined twice (``satk id conflicts``).

Input: the loaded definitions of a profile (game layers and modloader folders, :func:`satk.idmgr.scan.profile_defs`)
plus optional mod folders (:func:`satk.idmgr.scan.scan_mod`). Owners: ``vanilla`` and ``samp`` are the base
game; everything else (``modded``, ``modloader:<mod>``, ``mod:<folder>``) is a mod.

Findings (``sev``, ``code``):

* ``error CLASH`` - two mods define one id with different model names: one of them loses its model;
* ``warn REPLACES`` - a mod puts another model name on a base-game id (a replacement under a new name);
* ``warn DUPLICATE`` - two mods define the same id and name (the same add-on twice?);
* ``warn DUP_IN_MOD`` - one owner defines one id twice with different names;
* ``warn NAME_TWICE`` - one model name on two ids from different owners (their DFF/TXD files collide in
  modloader);
* ``info REDEFINES`` - a mod rewrites a base-game line with the same name (normal for data mods);
* ``info BASE`` - the base layers disagree (``samp`` reusing stock ids 300-311 for skins).
"""

from __future__ import annotations

from collections import defaultdict

from .scan import Def

__all__ = ["BASE_OWNERS", "COLS", "conflicts"]

BASE_OWNERS = frozenset({"vanilla", "samp"})
COLS = ["sev", "code", "id", "name", "where", "other"]
_ORDER = {"error": 0, "warn": 1, "info": 2}


def _label(d: Def) -> str:
    return f"{d.where} ({d.owner})" if not d.owner.startswith("mod:") else d.where


def _other(d: Def) -> str:
    return f"{d.name} @ {_label(d)}"


def conflicts(defs: list[Def]) -> list[list]:
    """Rows ``[sev, code, id, name, where, other]`` sorted by severity, id, name."""
    out: list[list] = []
    live = [d for d in defs if d.loaded]
    by_id: dict[int, list[Def]] = defaultdict(list)
    for d in live:
        by_id[d.id].append(d)
    for i, ds in sorted(by_id.items()):
        owners: dict[str, list[Def]] = defaultdict(list)
        for d in ds:
            owners[d.owner].append(d)
        for own, lst in owners.items():
            names = {d.name.lower() for d in lst}
            if len(names) > 1:
                out.append(["warn", "DUP_IN_MOD", i, lst[0].name, _label(lst[0]), _other(lst[1])])
        if len(owners) < 2:
            continue
        firsts = [lst[0] for _o, lst in sorted(owners.items())]
        base = sorted((d for d in firsts if d.owner in BASE_OWNERS), key=lambda d: d.owner != "vanilla")
        mods = [d for d in firsts if d.owner not in BASE_OWNERS]
        for k, a in enumerate(mods):
            for b in mods[k + 1:]:
                if a.name.lower() == b.name.lower():
                    out.append(["warn", "DUPLICATE", i, a.name, _label(a), _other(b)])
                else:
                    out.append(["error", "CLASH", i, a.name, _label(a), _other(b)])
            for b in base:
                if a.name.lower() == b.name.lower():
                    out.append(["info", "REDEFINES", i, a.name, _label(a), _other(b)])
                else:
                    out.append(["warn", "REPLACES", i, a.name, _label(a), _other(b)])
        if len(base) > 1 and len({d.name.lower() for d in base}) > 1:
            out.append(["info", "BASE", i, base[-1].name, _label(base[-1]), _other(base[0])])
    by_name: dict[str, list[Def]] = defaultdict(list)
    for d in live:
        by_name[d.name.lower()].append(d)
    for name, ds in sorted(by_name.items()):
        ids_by_owner: dict[int, Def] = {}
        for d in ds:
            ids_by_owner.setdefault(d.id, d)
        if len(ids_by_owner) < 2:
            continue
        lst = sorted(ids_by_owner.values(), key=lambda d: d.id)
        if len({d.owner for d in lst}) < 2 or all(d.owner in BASE_OWNERS for d in lst):
            continue  # one owner's own repeats (vanilla clothes01 x10) and base-only overlaps
        a = next((d for d in lst if d.owner not in BASE_OWNERS), lst[0])
        for b in lst:
            if b is not a and b.owner != a.owner:
                out.append(["warn", "NAME_TWICE", a.id, a.name, _label(a), f"id {b.id} @ {_label(b)}"])
    out.sort(key=lambda r: (_ORDER[r[0]], r[2], r[1], r[3].lower(), r[4]))
    return out
