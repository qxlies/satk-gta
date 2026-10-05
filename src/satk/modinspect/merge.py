"""Mod Loader's data-file merge: which line of which file ends up in the game.

A port of ``datalib::find_dominant_data`` (``dominance.hpp``, BSL-1.0) and of
``DataPlugin::GetMergedData`` / ``store_merger::merge`` (std.data, MIT) - see ``NOTICE-modloader.txt``.

How Mod Loader builds one data file (``handling.cfg``, an IDE, ...):

1. no mod has the file and no readme line targets it -> the game's file (``default``);
2. exactly one mod file and no readme lines -> that file is used as a whole (``override``; the game
   reads it with its own rules);
3. otherwise (``merge``) the stores are: the game's file (the *default* store, if it exists), each
   mod file in Mod Loader's install order, and a *readme* store = a copy of the first store with all
   readme lines force-merged into it (always present, never default). For every key:

   * if the key is in the default store and at least one custom store lacks it, the key is
     **removed** (``flag_RemoveIfNotExistInOneCustomButInDefault``);
   * otherwise the distinct values are counted; a value the default store has never wins over a
     custom one; among custom values the **least common** wins; a tie goes to the value seen
     **first** (the earliest store in install order).

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .traits import Rec, Store, Trait, recs_equal

__all__ = ["Value", "Outcome", "dominant", "merge", "Plan", "plan_stores", "readme_store"]


@dataclass
class Value:
    """A distinct value of a key across stores."""

    rec: Rec
    count: int = 0
    in_default: bool = False
    stores: list[int] = field(default_factory=list)


@dataclass
class Outcome:
    """The merged result for one key.

    Attributes:
        key: record key.
        rec: winning record (``None`` when removed or absent).
        winner: index of the first store holding the winning value.
        rule: ``only`` (one store), ``custom`` (the only non-default value), ``less-common``,
            ``tie-first``, ``default`` (only the game's value exists), ``removed``.
        values: distinct values in order of first appearance.
        missing: custom stores without the key.
    """

    key: tuple
    rec: Rec | None
    winner: int | None
    rule: str
    values: list[Value]
    missing: list[int]


def dominant(stores: list[Store], key: tuple, remove_missing: bool = True) -> Outcome:
    """``find_dominant_data`` for one key over ``stores`` (in order)."""
    if not stores:
        return Outcome(key, None, None, "absent", [], [])
    if len(stores) == 1:
        r = stores[0].recs.get(key)
        vals = [Value(r, 1, stores[0].is_default, [0])] if r is not None else []
        return Outcome(key, r, 0 if r is not None else None, "only" if r is not None else "absent", vals, [])
    counter: list[Value] = []
    has_any_custom = False
    missing: list[int] = []
    in_default = False
    for i, st in enumerate(stores):
        has_any_custom |= not st.is_default
        r = st.recs.get(key)
        if r is None:
            if not st.is_default:
                missing.append(i)
            continue
        v = next((v for v in counter if recs_equal(v.rec, r)), None)
        if v is None:
            v = Value(r)
            counter.append(v)
        v.count += 1
        v.in_default |= st.is_default
        v.stores.append(i)
        in_default |= st.is_default
    if not counter:
        return Outcome(key, None, None, "absent", counter, missing)
    if remove_missing and has_any_custom and in_default and missing:
        return Outcome(key, None, None, "removed", counter, missing)
    # std::min_element with the comparator of find_dominant_data (first minimum wins)
    best = counter[0]
    for v in counter[1:]:
        if (v.in_default or best.in_default) and best.in_default:
            best = v
        elif not (v.in_default or best.in_default) and v.count < best.count:
            best = v
    customs = [v for v in counter if not v.in_default]
    if best.in_default:
        rule = "default"
    elif len(customs) == 1:
        rule = "custom"
    elif sum(1 for v in customs if v.count == best.count) > 1:
        rule = "tie-first"
    else:
        rule = "less-common"
    return Outcome(key, best.rec, best.stores[0], rule, counter, missing)


def merge(stores: list[Store], trait: Trait) -> list[Outcome]:
    """Merge ``stores`` (already premerged) key by key, in the merged file's line order."""
    first: dict[tuple, int] = {}
    for st in stores:
        for k in st.recs:
            first.setdefault(k, len(first))
    keys = sorted(first, key=lambda k: trait.order(k, first[k]))
    return [dominant(stores, k) for k in keys]


def readme_store(base: Store, readme: list[tuple[str, Rec]]) -> Store:
    """``caching_stream::MakeReadmeStore``: a copy of the first store plus every readme line (later lines win)."""
    st = Store("readme", "readme lines", False, dict(base.recs))
    for _origin, rec in readme:
        st.recs[rec.key] = rec   # force_merge: replace or add
    return st


@dataclass
class Plan:
    """How Mod Loader builds one data file and the stores it merges.

    Attributes:
        mode: ``default`` | ``override`` | ``merge``.
        stores: premerged stores in dominance order (``default`` mode: just the game's file).
        default: index of the game's store in ``stores`` or ``None`` (no game file / override).
        sources: per store, ``(label, origin)`` for reports; the readme store lists its lines.
    """

    mode: str
    stores: list[Store]
    default: int | None
    readme_lines: list[tuple[str, Rec]] = field(default_factory=list)


def plan_stores(trait: Trait, default: Store | None, mods: list[Store], readme: list[tuple[str, Rec]],
                engine_mods: list[Store] | None = None) -> Plan:
    """Select Mod Loader's mode and build the store list (stores are premerged here).

    Args:
        trait: the file's trait.
        default: the game's file parsed with Mod Loader rules (``is_default=True``) or ``None``.
        mods: each mod's file parsed with Mod Loader rules, in install order.
        readme: ``(origin, raw record)`` readme lines for this file, in reading order.
        engine_mods: the same mod files parsed with the game's rules (used for ``override``).
    """
    if not mods and not readme:
        stores = [default] if default is not None else []
        return Plan("default", [_pre(trait, s) for s in stores], 0 if stores else None)
    if len(mods) == 1 and not readme:
        st = (engine_mods or mods)[0]
        return Plan("override", [_pre(trait, st)], None)
    raw = ([default] if default is not None else []) + list(mods)
    rs = readme_store(raw[0], readme)
    stores = [_pre(trait, s) for s in raw + [rs]]
    return Plan("merge", stores, 0 if default is not None else None, list(readme))


def _pre(trait: Trait, st: Store) -> Store:
    recs = trait.premerge(st.recs)
    if recs is st.recs:
        return st
    return Store(st.label, st.origin, st.is_default, recs, st.failed, st.dups)
