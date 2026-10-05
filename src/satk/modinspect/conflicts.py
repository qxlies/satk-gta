"""``mod conflicts``: places where two or more mods change the same thing, and who wins.

Rows ``target, kind, winner, losers, rule``:

* replaced files (streamed DFF/TXD/COL..., FX, sprites, texts, override-only data files, text IPL):
  the last mod in install order wins - the highest priority, on a tie the longest name, then the
  alphabetically last (``modloader::compare``);
* merged data files, per record: Mod Loader's dominance (:mod:`.merge`) - a game record that one
  mod's file lacks is removed; among different values the least common wins; ties go to the mod
  installed first;
* IDE IDs defined by two mods in different IDE files (``id-clash``): both load; the IDE read later wins.

Stdlib only.
"""

from __future__ import annotations

from .inspect import _sid
from .merge import merge
from .traits import IdeTrait, LineTrait, recs_equal
from .world import World

__all__ = ["conflict_rows", "COLS"]

COLS = ["target", "kind", "winner", "losers", "rule"]


def _names(world: World, mods: list[int]) -> str:
    seen: list[str] = []
    for m in mods:
        n = world.label(m)
        if n not in seen:
            seen.append(n)
    return ",".join(seen)


def _claim_rule(world: World, mods: list[int]) -> str:
    w = world.mods[mods[-1]]
    others = [world.mods[m] for m in dict.fromkeys(mods[:-1]) if m != mods[-1]]
    top = max(o.priority for o in others)
    if w.priority > top:
        return f"priority {w.priority} > {top}"
    return (f"same priority {w.priority}: install order by name length, then name - the last "
            f"({w.name}) wins")


def _claim_target(key: str) -> tuple[str, str]:
    kind, _, name = key.partition(":")
    if kind == "stream":
        name = name.split("#", 1)[0]
        ext = name.rsplit(".", 1)[-1]
        return _sid(ext, name), ext
    if kind == "ipl":
        return f"file:{name}", "ipl"
    if kind == "data":
        return f"file:data/{name}", "data"
    if kind == "imgfile":
        return f"file:{name}", "img"
    return f"file:{name}", kind


def conflict_rows(world: World) -> tuple[list[list], list[str]]:
    """``(rows, warnings)``; warnings name mod files whose missing lines remove game records (``DROPS``)."""
    rows: list[list] = []
    warn: list[str] = []
    # ---- replaced files
    for key, xs in sorted(world.claims().items()):
        mods = [x.mod for x in xs]
        if len(set(mods)) < 2:
            continue
        target, kind = _claim_target(key)
        winner = mods[-1]
        losers = [m for m in mods if m != winner]
        rows.append([target, kind, world.label(winner), _names(world, losers), _claim_rule(world, mods)])
    # ---- merged data files
    for fs, files in sorted(world.data_files().items()):
        lines = [r for r in world.readme if r.fs == fs]
        contributors = {x.mod for x in files} | {r.mod for r in lines}
        if len(contributors) < 2:
            continue
        rows += _data_conflicts(world, fs, files, lines, warn)
    # ---- the same new ID from different IDE files
    rows += _id_clashes(world)
    return rows, warn


def _data_conflicts(world: World, fs: str, files, lines, warn: list[str]) -> list[list]:
    trait, plan, game_rel = world.plan(fs)
    kind = "ide" if fs.startswith("ide:") else "data"
    target_file = fs[4:] if fs.startswith("ide:") else (game_rel or f"data/{fs}")
    if plan.mode != "merge":
        return []
    if isinstance(trait, LineTrait):
        mods = sorted({x.mod for x in files} | {r.mod for r in lines})
        return [[f"file:{target_file}", kind, "(merged)", _names(world, mods),
                 "merged line by line; Mod Loader's per-record rules for this file are not ported"]]
    # store index -> mod index (custom file stores); the readme store is last
    by_origin = {world.origin(x): x.mod for x in files}
    store_mod = {i: by_origin[st.origin] for i, st in enumerate(plan.stores) if st.origin in by_origin}
    default = plan.stores[plan.default] if plan.default is not None else None
    raw_default: dict = {}
    if plan.default is not None:
        d = world.default_file(fs)
        raw_default = trait.parse(d[1], is_default=True).recs if d else {}
    readme_recs = []
    for rl in lines:                                   # each readme line applied alone over the game's records
        fk = trait.final_key(rl.rec.key)
        raw = {k: v for k, v in raw_default.items() if trait.final_key(k) == fk}
        raw[rl.rec.key] = rl.rec
        readme_recs.append((rl, trait.premerge(raw)))
    rows: list[list] = []
    outs = merge(plan.stores, trait)
    dropped: dict[int, int] = {}
    for o in outs:
        if o.rule == "removed":
            for i in o.missing:
                dropped[i] = dropped.get(i, 0) + 1
    for i, n in sorted(dropped.items()):
        warn.append(f"DROPS: {plan.stores[i].origin} lacks {n} game record(s) of {target_file}; "
                    "merged by Mod Loader, they disappear from the game")
    for o in outs:
        d = default.recs.get(o.key) if default else None
        touch: list[tuple[int, object]] = []           # (mod, record or None = removes)
        for si, m in store_mod.items():
            r = plan.stores[si].recs.get(o.key)
            if r is None:
                if d is not None:
                    touch.append((m, None))
            elif d is None or not recs_equal(r, d):
                touch.append((m, r))
        for rl, prem in readme_recs:
            r = prem.get(o.key)
            if r is not None and (d is None or not recs_equal(r, d)):
                touch.append((rl.mod, r))
        if len({m for m, _ in touch}) < 2:
            continue
        vals = [r for _, r in touch]
        if all(v is not None for v in vals) and all(recs_equal(vals[0], v) for v in vals[1:]):
            continue                                    # every mod sets the same value
        target = trait.target(o.key)
        if o.rule == "removed":
            gone = [m for m, r in touch if r is None]
            keep = [m for m, r in touch if r is not None]
            rows.append([target, kind, "(removed)", _names(world, keep),
                         f"removed: {_names(world, gone)} lack(s) the game's line (a merged file must keep it)"])
            continue
        win_mods = [m for m, r in touch if r is not None and o.rec is not None and recs_equal(r, o.rec)]
        winner = _names(world, win_mods) if win_mods else ("(game)" if o.rule == "default" else "?")
        losers = [m for m, _ in touch if m not in win_mods]
        rule = {"less-common": "the least common value wins",
                "tie-first": "tie: the value of the mod installed first wins",
                "custom": "the only changed value wins",
                "default": "game value kept"}.get(o.rule, o.rule)
        counts = "/".join(str(v.count) for v in o.values if not v.in_default)
        rows.append([target, kind, winner, _names(world, losers), f"{rule} (counts {counts})"])
    return rows


def _id_clashes(world: World) -> list[list]:
    tr = IdeTrait()
    seen: dict[int, list[tuple[int, str, str]]] = {}
    for x in world.used("ide"):
        t = world.ide_target(x)
        if t is None:
            continue
        try:
            st = tr.parse(x.file.text())
        except Exception:  # noqa: BLE001 - unreadable file is reported elsewhere
            continue
        for r in st.recs.values():
            if r.key[0] == "id":
                seen.setdefault(r.key[1], []).append((x.mod, t, r.cells[1]))
    rows = []
    for mid, hits in sorted(seen.items()):
        files = {t for _, t, _ in hits}
        mods = {m for m, _, _ in hits}
        if len(files) < 2 or len(mods) < 2:
            continue
        desc = "; ".join(f"{world.label(m)}: {n} in {t}" for m, t, n in hits)
        rows.append([f"model:{mid}", "id-clash", "?", _names(world, [m for m, _, _ in hits]),
                     f"same ID in different IDE files ({desc}); the IDE loaded later in gta.dat wins"])
    return rows
