"""``mod effective``: the record (or whole file) the game gets after Mod Loader's merge.

``effective(world, "handling", "411")`` -> the winning ``handling.cfg`` line(s) of the vehicle with
model 411, where it comes from, why it won and every candidate value; without a key -> a summary of
the merged file, optionally written to ``work/out/mods/effective/<profile>/<file>``.

Stdlib only.
"""

from __future__ import annotations

from ..core.errors import SatkError
from .fields import diff_detail, short
from .merge import Outcome, Plan, merge
from .traits import LineTrait, Trait, recs_equal, trait_for
from .world import World

__all__ = ["resolve_file", "effective_key", "effective_file", "render_file", "FILE_ALIASES"]

#: Short names accepted for data files.
FILE_ALIASES = {"handling": "handling.cfg", "carcols": "carcols.dat", "object": "object.dat", "gta": "gta.dat",
                "default": "default.dat", "ide": "ide"}


def resolve_file(world: World, name: str, key: str | None) -> list[str]:
    """Data-file keys (``handling.cfg``, ``ide:data/vehicles.ide`` ...) that ``name`` (and ``key``) select."""
    n = FILE_ALIASES.get(name.lower(), name.lower().replace("\\", "/"))
    if n == "ide" or n.endswith(".ide"):
        touched = [k[4:] for k in world.data_files() if k.startswith("ide:")]
        cands = list(dict.fromkeys(world.base.ide_paths + touched))
        mid = _model_key(world, key) if (n == "ide" and key is not None) else None
        if mid is not None and mid in world.base.models:   # the model's own IDE plus the IDEs mods touch
            cands = list(dict.fromkeys([world.base.models[mid].ide] + touched))
        if n.endswith(".ide"):
            hits = [c for c in cands if c == n or c.rsplit("/", 1)[-1] == n.rsplit("/", 1)[-1]]
            if not hits:
                raise SatkError("NOT_FOUND", f"no IDE {name!r} is loaded by the game or the mods",
                                hint="satk mod effective ide 411 (search every IDE for a model ID)")
            return [f"ide:{h}" for h in hits]
        if key is None:
            raise SatkError("BAD_PARAMS", "give a model ID for 'ide' (or an IDE file name)",
                            hint="satk mod effective ide 411 | satk mod effective vehicles.ide")
        return [f"ide:{c}" for c in cands]
    return [n]


def _model_key(world: World, key: str) -> int | None:
    if key.isdigit():
        return int(key)
    m = world.base.by_name.get(key.lower())
    return m.id if m else None


def _handling_id(world: World, key: str) -> tuple[str, str | None]:
    """A handling id from ``INFERNUS``, ``411`` or ``infernus`` (via the merged vehicles.ide line)."""
    mid = _model_key(world, key)
    if mid is None:
        return key, None
    for fs in resolve_file(world, "ide", str(mid)):
        trait, plan, _ = world.plan(fs)
        out = [o for o in merge(plan.stores, trait) if o.key == ("id", mid)]
        if out and out[0].rec is not None and out[0].rec.section == "cars" and len(out[0].rec.cells) > 4:
            return str(out[0].rec.cells[4]), f"model {mid} {out[0].rec.cells[1]} uses handling id " \
                                               f"{out[0].rec.cells[4]} ({fs[4:]})"
    if key.isdigit():
        raise SatkError("NOT_FOUND", f"model {mid} is not a vehicle (no 'cars' line)",
                        hint="satk mod effective handling INFERNUS (a handling id)")
    return key, None


def _match(trait: Trait, outs: list[Outcome], world: World, key: str) -> list[Outcome]:
    if trait.name == "handling.cfg":
        want = key.lstrip("^")
        hits = [o for o in outs if o.key[0] == "veh" and o.key[1] == want] or \
               [o for o in outs if o.key[0] == "anim" and str(o.key[1]) == want and key.startswith("^")]
        if not hits:   # handling ids are case-sensitive in Mod Loader: suggest the right case
            hits = [o for o in outs if o.key[0] == "veh" and str(o.key[1]).lower() == want.lower()]
        return hits
    if trait.name == "*.ide":
        mid = _model_key(world, key)
        return [o for o in outs if o.key == ("id", mid)] if mid is not None else \
            [o for o in outs if o.key[0] == "txdp" and o.key[1] == key.lower()]
    if trait.name == "carcols.dat":
        if key.isdigit() and _model_key(world, key) is None:
            return [o for o in outs if o.key == ("col", int(key))]
        mid = _model_key(world, key)
        name = world.base.models[mid].name.lower() if mid is not None and mid in world.base.models else key.lower()
        return [o for o in outs if o.key == ("car", name)]
    if trait.name in ("gta.dat", "default.dat"):
        k = key.replace("\\", "/").lower()
        return [o for o in outs if o.key[1] == k or o.key[1].endswith("/" + k)]
    if trait.name == "object.dat":
        return [o for o in outs if o.key[1] == key.lower()]
    k = " ".join(key.split())
    return [o for o in outs if k.lower() in o.key[1].lower()]


def _store_name(world: World, plan: Plan, fs: str, idx: int | None, o: Outcome) -> str:
    if idx is None:
        return ""
    st = plan.stores[idx]
    if plan.mode == "merge" and idx == len(plan.stores) - 1:
        trait_key = o.key
        tr = trait_for(fs[4:] if fs.startswith("ide:") else fs)
        origins = [org for org, rec in plan.readme_lines if tr.final_key(rec.key) == trait_key]
        return origins[-1] if origins else "readme store (copy of the first file)"
    return "game" if st.is_default else st.origin


def effective_key(world: World, file: str, key: str) -> dict:
    """The effective record of ``key`` in ``file`` (object envelope fields)."""
    note = None
    if FILE_ALIASES.get(file.lower(), file.lower()) == "handling.cfg":
        key, note = _handling_id(world, key)
    found: list[dict] = []
    for fs in resolve_file(world, file, key):
        trait, plan, game_rel = world.plan(fs)
        outs = merge(plan.stores, trait)
        _t, dplan, _g = world.plan(fs, upto=0)
        default = dplan.stores[0] if dplan.stores else None
        if plan.mode == "override" and default is not None:   # game records the replacing file lacks
            got = {o.key for o in outs}
            outs += [Outcome(k, None, None, "removed", [], [0]) for k in default.recs if k not in got]
        for o in _match(trait, outs, world, key):
            d = default.recs.get(o.key) if default else None
            item: dict = {"file": fs[4:] if fs.startswith("ide:") else (game_rel or f"data/{fs}"),
                          "target": trait.target(o.key), "mode": plan.mode}
            if o.rec is None:
                item["result"] = "removed"
                gone = [plan.stores[i].origin for i in o.missing]
                item["why"] = (f"{gone[0]} replaces the game's file and lacks it" if plan.mode == "override" else
                               f"in the game's file but missing from {', '.join(gone)}: Mod Loader drops it")
            else:
                item["line"] = trait.render(o.rec)
                item["from"] = _store_name(world, plan, fs, o.winner, o)
                item["result"] = "game" if d is not None and recs_equal(o.rec, d) else \
                    ("changed" if d is not None else "added")
                item["why"] = _why(plan, o)
                if d is not None and not recs_equal(o.rec, d):
                    item["changes"] = diff_detail(trait, d, o.rec, limit=8)
            cands = []
            for v in o.values:
                srcs = [_store_name(world, plan, fs, i, o) for i in v.stores]
                cands.append([", ".join(dict.fromkeys(srcs)), v.count, "game" if v.in_default else "mod",
                              short(" | ".join(trait.render(v.rec).splitlines()), 100)])
            if len(cands) > 1 or plan.mode == "merge":
                item["candidates"] = {"cols": ["from", "count", "side", "line"], "rows": cands}
            if o.missing and o.rec is not None:
                item["missing_in"] = [plan.stores[i].origin for i in o.missing]
            found.append(item)
    if not found:
        raise SatkError("NOT_FOUND", f"{key!r} is not in {file} (game or mods)",
                        hint="ids are case-sensitive in handling.cfg (INFERNUS); IDE keys are model IDs")
    out: dict = {"key": key}
    if note:
        out["resolved"] = note
    if len(found) == 1:
        out.update(found[0])
    else:
        out["matches"] = found
    return out


def _why(plan: Plan, o: Outcome) -> str:
    if plan.mode == "default":
        return "no mod touches this file"
    if plan.mode == "override":
        return "one mod file and no readme lines: the mod's file replaces the game's"
    return {"only": "only one store has it", "custom": "the only changed value wins over the game's",
            "less-common": "the least common changed value wins", "tie-first":
            "tie between changed values: the store installed first wins", "default": "no mod changes it"
            }.get(o.rule, o.rule)


def effective_file(world: World, file: str) -> tuple[list[list], dict, list[tuple[str, str]]]:
    """Summary rows of every merged file ``file`` selects: ``(rows, info, rendered files)``."""
    rows: list[list] = []
    rendered: list[tuple[str, str]] = []
    info: dict = {}
    for fs in resolve_file(world, file, None):
        trait, plan, game_rel = world.plan(fs)
        outs = merge(plan.stores, trait)
        _t, dplan, _g = world.plan(fs, upto=0)
        default = dplan.stores[0] if dplan.stores else None
        kept = changed = added = removed = 0
        for o in outs:
            d = default.recs.get(o.key) if default else None
            if o.rec is None:
                removed += d is not None
            elif d is None:
                added += 1
            elif recs_equal(o.rec, d):
                kept += 1
            else:
                changed += 1
        if plan.mode == "override" and default is not None:
            got = {o.key for o in outs}
            removed += sum(1 for k in default.recs if k not in got)
        name = fs[4:] if fs.startswith("ide:") else (game_rel or f"data/{fs}")
        sources = ", ".join(st.origin for st in plan.stores if not st.is_default and st.label != "readme")
        rows.append([name, plan.mode, kept, changed, added, removed, sources or "-"])
        rendered.append((name, render_file(trait, outs)))
        if isinstance(trait, LineTrait):
            info.setdefault("not_ported", []).append(name)
    return rows, info, rendered


def render_file(trait: Trait, outs: list[Outcome]) -> str:
    """The merged file as text (records in Mod Loader's output order; sections reopened as needed)."""
    lines: list[str] = []
    cur: str | None = None
    sectioned = bool(trait.sections) and not trait.per_line
    for o in outs:
        if o.rec is None:
            continue
        if sectioned and o.rec.section != cur:
            if cur is not None:
                lines.append("end")
            cur = o.rec.section
            lines.append(cur)
        lines += trait.render(o.rec).splitlines()
    if sectioned and cur is not None:
        lines.append("end")
    if trait.name == "handling.cfg":
        lines.append(";the end")
    return "\r\n".join(lines) + "\r\n"
