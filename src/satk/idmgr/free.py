"""Taken and free model ids across profiles (``satk id free``).

An id is taken when any built profile defines it (any layer, loaded or not: see :mod:`satk.idmgr.scan`),
when the engine uses it without an IDE line (:data:`~satk.idmgr.ranges.ENGINE_RESERVED`), when SA-MP
takes it (:data:`~satk.idmgr.ranges.SAMP_RESERVED`, unless ``samp=False``) or when an extra mod folder
defines it. SA-MP DL ids (``target="samp-dl"``) live in their own spaces and are taken only by the
``artconfig.txt`` files given.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import cfg, jpath, open_ro
from .ranges import ENGINE_RESERVED, MAX_ID, SAMP_RESERVED, STORES, f92_max_id, iter_range
from .scan import Def, profile_defs, rows

__all__ = ["Taken", "open_profiles", "taken_ids", "free_ids", "artconfig_ids", "ARTCONFIG_RE"]

#: open.mp ``Server/Components/CustomModels/models.cpp`` line grammar (report 23 §8).
ARTCONFIG_RE = (
    re.compile(r"AddCharModel\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*\"(.+)\"\s*,\s*\"(.+)\"\s*\)\s*;*"),
    re.compile(r"AddSimpleModel\s*\(\s*(-?\d+)\s*,\s*(\d+)\s*,\s*(-\d+)\s*,\s*\"(.+)\"\s*,\s*\"(.+)\"\s*\)\s*;*"),
    re.compile(r"AddSimpleModelTimed\s*\(\s*(-?\d+)\s*,\s*(\d+)\s*,\s*(-\d+)\s*,\s*\"(.+)\"\s*,\s*\"(.+)\"\s*,"
               r"\s*(\d+)\s*,\s*(\d+)\s*\)\s*;*"),
)


@dataclass
class Taken:
    """Taken ids with the first reason each, the profiles read and the id limit."""

    ids: dict[int, str] = field(default_factory=dict)
    profiles: list[str] = field(default_factory=list)
    max_id: int = MAX_ID
    max_from: str = "engine"
    used: dict[str, int] = field(default_factory=dict)   # sec -> most distinct ids defined in one profile
    hints: list[str] = field(default_factory=list)
    defs: dict[str, list[Def]] = field(default_factory=dict)

    def add(self, i: int, why: str) -> None:
        self.ids.setdefault(i, why)


def open_profiles(names: list[str] | None, skipped: list[tuple[str, str]] | None = None) -> list[tuple[str, object]]:
    """``(profile, IndexDB)`` of the given profiles, or of every configured profile whose index is built.

    Profiles left out because their index is missing or has an old schema are appended to ``skipped`` as
    ``(profile, reason)`` so callers can warn that ids taken only there were not considered.
    """
    from ..index.api import open_index

    c = cfg()
    explicit = bool(names)
    cand = list(names or sorted({c.canonical_profile(p) for p in [*c.profiles, *c.aliases]}))
    out: list[tuple[str, object]] = []
    seen: set = set()
    for p in cand:
        try:
            db = open_index(p)
        except SatkError as e:
            if explicit or e.code not in ("INDEX_MISSING", "BAD_PARAMS", "NOT_READY"):
                raise
            if skipped is not None:
                skipped.append((p, f"{e.code}: {e.msg}"))
            continue
        key = str(getattr(db, "path", "")) if str(getattr(db, "path", "")) != ":memory:" else id(db)
        if key in seen:
            continue
        seen.add(key)
        out.append((p, db))
    if not out:
        raise SatkError("INDEX_MISSING", "no profile index is built",
                        hint="satk index build --all (or satk index build --profile installed)")
    return out


def _f92(db) -> tuple[int | None, str | None]:
    for (rel,) in rows(db, "SELECT relpath FROM source WHERE lower(relpath) LIKE '%fastman92limitadjust%.ini' "
                           "ORDER BY relpath"):
        p = Path(db.root) / Path(*str(rel).split("/"))
        try:
            with open_ro(p) as f:
                n = f92_max_id(f.read(1 << 20).decode("latin-1"))
        except OSError:
            continue
        if n is not None:
            return n, str(rel)
    return None, None


def taken_ids(dbs: list[tuple[str, object]], *, samp: bool = True, extra: list[Def] = (),
              max_id: int | None = None) -> Taken:
    """Union of the taken ids of ``dbs`` (see the module docstring)."""
    t = Taken(profiles=[p for p, _ in dbs])
    f92: list[tuple[str, int | None, str | None]] = []
    for prof, db in dbs:
        defs = profile_defs(db)
        t.defs[prof] = defs
        slots: dict[str, set[int]] = {}
        for d in defs:
            t.add(d.id, f"{prof}: {d.name} ({d.sec}, {d.owner}, {d.where})")
            if d.loaded and d.owner != "samp":  # SA-MP raises its own limits
                slots.setdefault(d.sec, set()).add(d.id)
        for sec, ids in slots.items():
            t.used[sec] = max(t.used.get(sec, 0), len(ids))
        f92.append((prof, *_f92(db)))
    for a, b, why in ENGINE_RESERVED:
        for i in iter_range(a, b):
            t.add(i, why)
    if samp:
        for a, b, why in SAMP_RESERVED:
            for i in iter_range(a, b):
                t.add(i, why)
    for d in extra:
        t.add(d.id, f"{d.owner}: {d.name} ({d.sec}, {d.where})")
    if max_id is not None:
        t.max_id, t.max_from = int(max_id), "--max-id"
    elif f92 and all(n is not None for _p, n, _r in f92):
        t.max_id = min(n for _p, n, _r in f92)  # type: ignore[type-var]
        t.max_from = "fastman92 LA ini: " + ", ".join(f"{p} {r}" for p, _n, r in f92)
    else:
        for p, n, r in f92:
            if n is not None:
                t.hints.append(f"F92: profile {p} has fastman92 LA ({r}) allowing ids up to {n}; other profiles "
                               f"stop at {MAX_ID} (--max-id {n} to use them)")
    return t


def free_ids(taken: dict[int, str], ranges: list[tuple[int, int]], count: int, contiguous: bool = False
             ) -> tuple[list[int], int]:
    """First ``count`` free ids of ``ranges`` (in range order) and the number free in all of them."""
    ids: list[int] = []
    total = 0
    run: list[int] = []
    for a, b in ranges:
        run = []
        for i in iter_range(a, b):
            if i in taken:
                run = []
                continue
            total += 1
            if contiguous:
                if len(ids) < count:
                    run.append(i)
                    if len(run) == count:
                        ids = list(run)
            elif len(ids) < count:
                ids.append(i)
    return ids, total


def artconfig_ids(paths: list[str]) -> dict[int, str]:
    """SA-MP DL ids used by ``artconfig.txt`` files: ``{id: "file:line"}``."""
    out: dict[int, str] = {}
    for p in paths:
        path = Path(p)
        try:
            with open_ro(path) as f:
                text = f.read().decode("latin-1")
        except OSError as e:
            raise SatkError("NOT_FOUND", f"cannot read {jpath(path)}: {e.strerror or e}") from None
        for n, line in enumerate(text.splitlines(), 1):
            for k, rx in enumerate(ARTCONFIG_RE):
                m = rx.search(line)
                if m:
                    new = int(m.group(2)) if k == 0 else int(m.group(3))
                    out.setdefault(new, f"{path.name}:{n}")
                    break
    return out


def capacity(kind: str, used: dict[str, int]) -> dict:
    """Stock store size of a kind vs the most definitions in one profile."""
    size, secs = STORES[kind]
    return {"store": size, "used": sum(used.get(s, 0) for s in secs)}
