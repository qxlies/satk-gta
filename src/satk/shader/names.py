"""World texture names of a profile index for MTA shaders (``satk shader textures``). Stdlib only.

MTA applies a world-texture shader to every texture of every loaded TXD whose (lower-case) name matches, so the
universe is the set of texture names of all active TXDs of the profile: map objects, vehicles, peds, ``player.img``
clothes and the loose TXDs (``particle.txd`` holds the sea texture ``waterclear256``). :class:`World` loads what the
selectors need from the index once per index file (read-only SQLite):

* names -> TXDs, alpha flag; models -> section, IDE flags, TXD, placements; ``model_tex`` uses;
* LOD models (placements other placements use as LOD) and recolourable vehicle paint materials (``dff_mat``);
* the collision surface of each texture from ``data/colgen/tex_surface.json`` (for kinds such as ``road``).

:func:`select` intersects the selectors (patterns, kind, model/TXD, area) and drops ``exclude`` globs;
:func:`rows` adds counts (TXDs, models, placements) and, for a model or area selection, the *spill*: uses of the
same names elsewhere, which a name-based shader also hits.
"""

from __future__ import annotations

import sqlite3
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import SatkError
from . import wild

__all__ = ["World", "load_world", "kinds", "kind_names", "scope_names", "Selection", "select", "rows", "ROW_COLS"]

WORLD_SECS = ("objs", "tobj", "anim")
ROW_COLS = ["name", "txd", "txds", "models", "inst"]
_LOCK = threading.Lock()
_CACHE: dict[str, tuple[int, "World"]] = {}


@dataclass
class World:
    profile: str
    names: list[str] = field(default_factory=list)                 # sorted universe (lower case)
    tex_txds: dict[str, set[str]] = field(default_factory=dict)    # name -> TXD names
    alpha: set[str] = field(default_factory=set)
    txd_ns: dict[str, str] = field(default_factory=dict)
    txd_parent: dict[str, tuple[str, str]] = field(default_factory=dict)   # txd -> (parent, via)
    # model id -> (name, sec, flags, txd, n_inst)
    models: dict[int, tuple[str, str, int, str, int]] = field(default_factory=dict)
    uses: dict[str, list[int]] = field(default_factory=dict)       # name -> model ids (model_tex)
    model_tex: dict[int, list[str]] = field(default_factory=dict)  # model id -> names
    lod_models: set[int] = field(default_factory=set)
    paint: set[str] = field(default_factory=set)

    def inst(self, name: str) -> int:
        return sum(self.models[m][4] for m in self.uses.get(name, ()) if m in self.models)


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    con.execute("PRAGMA query_only = 1")
    return con


def load_world(db) -> World:
    """The :class:`World` of an open :class:`satk.index.api.IndexDB` (cached per file and mtime)."""
    path = getattr(db, "path", None)
    if path is None or not Path(path).is_file():
        raise SatkError("INDEX_MISSING", f"profile {getattr(db, 'profile', '?')!r} has no index file",
                        hint=f"satk index build --profile {getattr(db, 'profile', 'vanilla')}")
    p = Path(path)
    key = str(p).lower()
    mt = p.stat().st_mtime_ns
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] == mt:
            return hit[1]
    w = _read(p, str(getattr(db, "profile", "vanilla")))
    with _LOCK:
        _CACHE[key] = (mt, w)
    return w


def _read(path: Path, profile: str) -> World:
    w = World(profile)
    con = _connect(path)
    try:
        for name, txd, ns, alpha in con.execute(
                "SELECT lower(t.name), lower(x.name), b.ns, t.alpha FROM texture t JOIN txd x ON x.id = t.txd_id "
                "JOIN blob b ON b.id = x.blob_id WHERE b.active = 1"):
            if not name:
                continue
            w.tex_txds.setdefault(name, set()).add(txd)
            w.txd_ns[txd] = ns
            if alpha:
                w.alpha.add(name)
        for txd, parent, via in con.execute(
                "SELECT lower(x.name), lower(x.parent), x.parent_via FROM txd x JOIN blob b ON b.id = x.blob_id "
                "WHERE b.active = 1 AND x.parent IS NOT NULL"):
            w.txd_parent[txd] = (parent, via)
        for mid, name, sec, flags, txd, n in con.execute(
                "SELECT m.id, lower(m.name), m.sec, coalesce(m.flags, 0), lower(coalesce(m.txd, '')), "
                "coalesce(l.n_inst, 0) FROM model m LEFT JOIN model_link l ON l.id = m.id WHERE m.active = 1"):
            w.models[int(mid)] = (name, sec, int(flags), txd, int(n))
        for mid, tex in con.execute("SELECT model_id, lower(texture) FROM model_tex ORDER BY model_id, texture"):
            if tex:
                w.uses.setdefault(tex, []).append(int(mid))
                w.model_tex.setdefault(int(mid), []).append(tex)
        w.lod_models = {int(r[0]) for r in con.execute("SELECT DISTINCT model_id FROM inst WHERE is_lod = 1")}
        w.paint = {r[0] for r in con.execute(
            "SELECT DISTINCT lower(dm.texture) FROM dff_mat dm JOIN model_link ml ON ml.dff_id = dm.dff_id "
            "JOIN model m ON m.id = ml.id AND m.active = 1 AND m.sec = 'cars' "
            "WHERE dm.color_slot IS NOT NULL AND dm.texture IS NOT NULL AND dm.texture != ''")}
    except sqlite3.Error as e:
        raise SatkError("INDEX_MISSING", f"index {path.name} is not readable as a satk index: {e}",
                        hint=f"satk index build --profile {profile}") from None
    finally:
        con.close()
    w.names = sorted(w.tex_txds)
    return w


# --------------------------------------------------------------------------- kinds


def kinds() -> dict[str, dict]:
    """``data/shader/kinds.json`` kinds (name -> rule)."""
    from ..core import resources

    return resources.read_json("shader", "kinds.json")["kinds"]


def _surfaces() -> dict[str, list]:
    from ..core import resources

    if not resources.exists("colgen", "tex_surface.json"):
        return {}
    return {k.lower(): v for k, v in (resources.read_json("colgen", "tex_surface.json").get("textures") or {}).items()}


def scope_names(w: World, scope: str) -> set[str]:
    """Names in a kind scope: any, world, vehicle or ped (see ``data/shader/kinds.json``)."""
    if scope == "any":
        return set(w.names)
    if scope == "world":
        out: set[str] = set()
        for name, ms in w.uses.items():
            if any(w.models.get(m, ("", ""))[1] in WORLD_SECS for m in ms):
                out.add(name)
        return out & set(w.names)
    if scope in ("vehicle", "ped"):
        sec = "cars" if scope == "vehicle" else "peds"
        txds = {m[3] for m in w.models.values() if m[1] == sec and m[3]}
        if scope == "vehicle":
            txds.add("vehicle")
            txds |= {t for t, (_p, via) in w.txd_parent.items() if via == "vehicle"}
        else:
            txds |= {t for t, ns in w.txd_ns.items() if ns == "player"}
        return {n for n, ts in w.tex_txds.items() if ts & txds}
    raise SatkError("INTERNAL", f"data/shader/kinds.json: unknown scope {scope!r}")


def _share(w: World, name: str, test) -> float:
    ms = w.uses.get(name) or []
    if not ms:
        return 0.0
    return sum(1 for m in ms if test(m)) / len(ms)


def kind_names(w: World, kind: str) -> set[str]:
    """Names of one kind (rules in ``data/shader/kinds.json``)."""
    ks = kinds()
    if kind not in ks:
        import difflib

        raise SatkError("BAD_PARAMS", f"unknown kind {kind!r}", did_you_mean=difflib.get_close_matches(kind, ks, n=3),
                        hint="kinds: " + ", ".join(ks))
    r = ks[kind]
    scope = scope_names(w, r.get("scope", "any"))
    out: set[str] = set()
    if r.get("all"):
        out |= scope
    pats = [wild.matcher(p) for p in r.get("names") or ()]
    if pats:
        out |= {n for n in scope if any(m(n) is not None for m in pats)}
    sf = r.get("surfaces")
    if sf:
        table = _surfaces()
        ids = set(sf.get("ids") or ())
        for n in scope:
            v = table.get(n)
            if v and int(v[0]) in ids and v[1] >= sf.get("share", 60) and v[2] >= sf.get("votes", 0):
                out.add(n)
    mr = r.get("models")
    if mr:
        mask = int(mr.get("flags", 0))
        mpats = [wild.matcher(p) for p in mr.get("model_names") or ()]

        def test(mid: int) -> bool:
            m = w.models.get(mid)
            return bool(m and ((m[2] & mask) or any(f(m[0]) is not None for f in mpats)))

        out |= {n for n in scope if _share(w, n, test) >= float(mr.get("share", 0.5))}
    lr = r.get("lod")
    if lr:
        share = float(lr.get("share", 1.0))
        out |= {n for n in scope if _share(w, n, lambda m: m in w.lod_models) >= share - 1e-9}
    if r.get("paint"):
        out |= scope & w.paint
    ex = [wild.matcher(p) for p in r.get("exclude") or ()]
    return {n for n in out if not any(m(n) is not None for m in ex)}


# --------------------------------------------------------------------------- selection


@dataclass
class Selection:
    names: set[str]
    how: list[str]
    inside: Counter | None = None       # area: name -> placements inside the circle
    model_ids: set[int] | None = None   # model selection: the selected model(s)
    warn: list[str] = field(default_factory=list)


def _area_counts(db, area: list[float]) -> tuple[Counter, int]:
    if len(area) != 3 or area[2] <= 0:
        raise SatkError("BAD_PARAMS", f"--area takes x y r with r > 0, got {area}", hint="--area 2495 -1666 150")
    x, y, r = (float(v) for v in area)
    rows = db.insts(center=(x, y), r=r, area=None, lod="all")
    return Counter(i.model_id for i in rows), len(rows)


def select(db, w: World, *, patterns: list[str] | None = None, kind: str | None = None, model: str | None = None,
           area: list[float] | None = None, exclude: list[str] | None = None) -> Selection:
    """Intersect the given selectors; at least one is required."""
    if not (patterns or kind or model or area):
        raise SatkError("BAD_PARAMS", "give a pattern, --kind, --model or --area",
                        hint="satk shader textures *road* | --kind road | --model model:411 | --area 2495 -1666 150")
    cur: set[str] | None = None
    how: list[str] = []
    sel = Selection(set(), how)

    def meet(s: set[str], label: str) -> None:
        nonlocal cur
        cur = s if cur is None else cur & s
        how.append(f"{label}: {len(s)}")

    if patterns:
        hit: set[str] = set()
        for p in patterns:
            got = set(wild.match_names(p, w.names))
            if not got:
                sel.warn.append(f"NO_MATCH: pattern {p!r} matches no texture name of profile {w.profile}")
            hit |= got
        meet(hit, "patterns")
    if kind:
        meet(kind_names(w, kind), f"kind {kind}")
    if model:
        s = str(model).strip()
        if s.lower().startswith("txd:"):
            t = s[4:].lower()
            got = {n for n, ts in w.tex_txds.items() if t in ts}
            if not got and t not in w.txd_ns:
                import difflib

                raise SatkError("NOT_FOUND", f"no TXD {t!r} in profile {w.profile}",
                                did_you_mean=["txd:" + c for c in difflib.get_close_matches(t, list(w.txd_ns), n=3)])
            meet(got, f"txd {t}")
        else:
            mf = db.model_files(s if not s.isdigit() else int(s))
            sel.model_ids = {int(mf.model_id)}
            meet(set(w.model_tex.get(int(mf.model_id), [])), f"model:{mf.model_id} {mf.name}")
    if area:
        counts, n_inst = _area_counts(db, list(area))
        inside: Counter = Counter()
        for mid, c in counts.items():
            for name in set(w.model_tex.get(mid, ())):
                inside[name] += c
        sel.inside = inside
        meet(set(inside), f"area ({n_inst} placements)")
    names = cur or set()
    missing = names - set(w.tex_txds)
    if missing:   # material names no TXD holds: the game draws them untextured, a shader cannot reach them
        names -= missing
        sel.warn.append(f"MISSING: {len(missing)} material texture name(s) are in no TXD and are left out: "
                        + ", ".join(sorted(missing)[:5]))
    if exclude:
        ex = [wild.matcher(p) for p in exclude]
        before = len(names)
        names = {n for n in names if not any(m(n) is not None for m in ex)}
        how.append(f"exclude: -{before - len(names)}")
    sel.names = names
    return sel


def rows(w: World, sel: Selection) -> tuple[list[str], list[list], dict]:
    """Table columns, rows (most placements first) and the spill summary of a selection."""
    cols = list(ROW_COLS)
    spill: dict = {}
    if sel.inside is not None:
        cols.append("inside")
    out: list[list] = []
    spill_names = 0
    spill_inst = 0
    for n in sel.names:
        txds = sorted(w.tex_txds.get(n, ()))
        txd = txds[0] if len(txds) == 1 else (f"{txds[0]} +{len(txds) - 1}" if txds else "")
        users = w.uses.get(n, [])
        inst = w.inst(n)
        row = [n, txd, len(txds), len(users), inst]
        if sel.inside is not None:
            inside = int(sel.inside.get(n, 0))
            row.append(inside)
            if inst > inside:
                spill_names += 1
                spill_inst += inst - inside
        elif sel.model_ids is not None:
            others = [m for m in users if m not in sel.model_ids]
            if others:
                spill_names += 1
                spill_inst += len(others)
        out.append(row)
    out.sort(key=lambda r: (-r[4], -r[3], r[0]))
    if sel.inside is not None and spill_names:
        spill = {"names": spill_names, "placements_outside": spill_inst,
                 "note": "MTA matches by name: these names are also drawn outside the area"}
    elif sel.model_ids is not None and spill_names:
        spill = {"names": spill_names, "other_models": spill_inst,
                 "note": "other models use the same names; pass targetElement to engineApplyShaderToWorldTexture "
                         "for script-created vehicles, peds and objects"}
    return cols, out, spill

