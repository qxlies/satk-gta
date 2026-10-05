"""Operations of satk.index (owner WP-03): ``index.*``, ``asset.find/get/refs``, ``world.near``.

Registered automatically by ``satk.core.registry.discover()``. The SID provider for the index
kinds is registered here too (``model:``, ``tex:``, ``inst:`` ... ->
:class:`satk.index.provider.IndexProvider`). ``asset.*`` dispatch SIDs of other owners (``note:``,
``fn:``, ``g:`` ...) to their providers.

Module-level imports are stdlib only and light (the CLI starts in < 0.6 s); the builder is
imported inside :func:`index_build`.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import statistics
import time
from pathlib import Path
from typing import Literal

from ..core import envelope as env
from ..core.errors import SatkError
from ..core.ids import KIND_OWNER, provider_for
from ..core.paths import cfg, jpath
from ..core.registry import doctor_check, op, report_progress, status_provider
from . import api
from . import provider as _provider

_provider.register()

FindKind = Literal["model", "dff", "txd", "tex", "col", "ipl", "zone", "ifp", "anim", "file", "note", "fn", "g"]
_INDEX_PROFILES = ("vanilla", "installed", "samp")


def _db(profile: str) -> api.IndexDB:
    return api.open_index(profile or "vanilla")


def _with_stale(out: dict, db) -> dict:
    try:
        w = db.stale_warnings()
    except Exception:  # noqa: BLE001 - freshness must never break a query (fake DBs, odd roots)
        w = []
    return env.with_warn(out, *w) if w else out


# =========================================================================== build / maintenance


@op("index.build",
    summary="Build the SQLite asset index of a load profile (vanilla|installed|samp): IMG/TXD/DFF/COL/IDE/IPL, "
            "winners, links, R-tree, FTS. Seconds; atomic replace.",
    summary_ru="Собрать SQLite-индекс ассетов профиля (vanilla|installed|samp): архивы, текстуры, модели, "
               "расстановки, победители, связи, R-tree, полнотекстовый поиск.",
    mcp=False, long_running=True,
    examples=("satk index build", "satk index build --profile installed", "satk index build --all"))
def index_build(profile: str | None = None, all: bool = False, jobs: int = 0) -> dict:  # noqa: A002 - CLI --all
    """Build ``work/index/<profile>.sqlite``.

    Args:
        profile: load profile to build (default: [index] default_profile, vanilla here).
        all: build every configured profile whose game root exists (vanilla, installed, samp, game).
        jobs: scan worker processes (0 = auto, up to 12; 1 = in-process).
    """
    from .build import build

    c = cfg()
    if all:
        profiles = [p for p in c.profiles if c.profiles[p].root.is_dir()]
    else:
        profiles = [profile or str(c.get("index.default_profile") or "vanilla")]
    out = []
    for i, p in enumerate(profiles):
        report_progress(i, len(profiles), f"build {p}")
        api.clear_cache()
        res = build(p, jobs=jobs or None)
        if c.canonical_profile(p) != p:  # vanilla without a clean copy
            res = env.with_warn(res, *(w for w in c.warnings if w.startswith("NO_CLEAN_COPY")))
        out.append(res)
    api.clear_cache()
    if len(out) == 1:
        return out[0]
    return {"ok": all_ok(out), "builds": out}


def all_ok(items: list[dict]) -> bool:
    return all(x.get("ok") for x in items)


def _counts(db: api.IndexDB) -> dict:
    c = db._conn()
    return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("txd", "texture", "dff", "model", "inst")}


def _profile_state(name: str, deep: bool = False) -> dict:
    path = api.index_path(name)
    if not path.is_file():
        return {"built": False, "path": jpath(path)}
    try:
        db = api.IndexDB(name)
    except SatkError as e:
        return {"built": False, "path": jpath(path), "error": e.code}
    try:
        meta = db.meta()
        stale = db.stale_warnings()
        st = {"built": True, "fresh": not stale, "built_at": meta.get("built_at"),
              "size_mb": round(path.stat().st_size / 1e6, 1), "counts": _counts(db)}
        if stale:
            st["stale"] = stale
        if deep:
            st["content_hash"] = meta.get("content_hash")
            st["build_seconds"] = float(meta.get("build_seconds") or 0)
        return st
    finally:
        db.close()


@op("index.status", summary="State of the index files per profile: built, fresh, size, row counts.",
    summary_ru="Состояние индексов по профилям: собран ли, свежий ли, размер, число строк.",
    mcp=False, examples=("satk index status",))
def index_status(deep: bool = False) -> dict:
    """Index state of every configured profile.

    Args:
        deep: also report content hashes and build times.
    """
    rows = []
    conf = cfg()
    for name, prof in conf.profiles.items():
        st = _profile_state(name, deep)
        c = st.get("counts") or {}
        state = ("built" if st.get("built") else "not built") if prof.configured else "not configured"
        rows.append([name, st.get("built", False), st.get("fresh"), st.get("size_mb"), st.get("built_at"),
                     c.get("model"), c.get("texture"), c.get("inst"), state])
    for alias, target in conf.aliases.items():
        rows.append([alias, None, None, None, None, None, None, None, f"alias of {target}"])
    return env.with_warn(env.table(["profile", "built", "fresh", "size_mb", "built_at", "models", "textures",
                                    "insts", "state"], rows), *conf.warnings)


@op("index.verify", summary="Check a built index against the golden numbers (tests/golden/index_<profile>.json).",
    summary_ru="Сверить собранный индекс с эталонными числами (tests/golden/index_<profile>.json).",
    mcp=False, examples=("satk index verify", "satk index verify --profile installed"))
def index_verify(profile: str = "vanilla", golden: str | None = None, metrics: bool = False) -> dict:
    """Verify the golden numbers of tests/golden/index_<profile>.json against a built index.

    Args:
        profile: profile whose index is checked.
        golden: alternative golden JSON file.
        metrics: include every computed metric (unresolved_texrefs_main/all are always included).
    """
    from .golden import load_golden, verify

    g = load_golden(profile, Path(golden) if golden else None)
    res = verify(_db(profile), g)
    if not metrics:
        res.pop("metrics", None)
    return res


@op("index.hash", summary="Content hash of a built index (sha256 of the key tables; equal for equal inputs).",
    summary_ru="Хэш содержимого индекса (sha256 ключевых таблиц; одинаковый при одинаковых входах).",
    mcp=False, examples=("satk index hash", "satk index hash --recompute"))
def index_hash(profile: str = "vanilla", recompute: bool = False) -> dict:
    """Content hash stored in ``meta`` (and optionally recomputed).

    Args:
        profile: profile.
        recompute: recompute the hash from the tables and compare.
    """
    db = _db(profile)
    stored = db.meta().get("content_hash")
    out = {"profile": profile, "content_hash": stored}
    if recompute:
        from .build import content_hash

        now = content_hash(db._conn())
        out.update(recomputed=now, match=now == stored)
    return env.obj(None, **out)


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    k = min(len(xs) - 1, max(0, math.ceil(p / 100 * len(xs)) - 1))
    return xs[k]


@op("index.bench", summary="Latency of get/find/refs/near (p50/p95 ms) on a built index; ok if every p95 < 50 ms.",
    summary_ru="Замер задержки get/find/refs/near (p50/p95, мс); ok, если все p95 < 50 мс.",
    mcp=False, examples=("satk index bench",))
def index_bench(profile: str = "vanilla", n: int = 60) -> dict:
    """Benchmark the query API.

    Args:
        profile: profile.
        n: calls per operation (varied inputs taken from the index).
    """
    db = _db(profile)
    c = db._conn()
    models = [r[0] for r in c.execute("SELECT id FROM model WHERE active=1 ORDER BY id")]
    insts = c.execute("SELECT x, y FROM inst ORDER BY id").fetchall()
    names = [r[0] for r in c.execute("SELECT name FROM model WHERE active=1 ORDER BY id")]
    texs = [r[0] for r in c.execute("SELECT sid FROM fts_name WHERE kind='tex' LIMIT 5000")]
    n = max(5, min(int(n), 1000))

    def pick(seq, i):
        return seq[(i * 7919) % len(seq)] if seq else None

    cases = {
        "get": lambda i: db.get(f"model:{pick(models, i)}"),
        "find": lambda i: db.find(pick(names, i)[:6] if i % 2 else pick(names, i)),
        "refs": lambda i: db.refs(f"model:{pick(models, i)}") if i % 2 else db.refs(pick(texs, i), rel="models"),
        "near_r100": lambda i: db.near(pick(insts, i)[0], pick(insts, i)[1], r=100.0),
    }
    rows = []
    ok = True
    for name, fn in cases.items():
        fn(0)  # warm-up
        ts = []
        for i in range(n):
            t0 = time.perf_counter()
            fn(i + 1)
            ts.append((time.perf_counter() - t0) * 1000)
        p95 = _pct(ts, 95)
        ok = ok and p95 < 50.0
        rows.append([name, n, round(statistics.median(ts), 2), round(p95, 2), round(max(ts), 2)])
    out = env.table(["op", "n", "p50_ms", "p95_ms", "max_ms"], rows)
    out["ok"] = ok
    return out


# =========================================================================== SQL


_NUM = re.compile(r"^-?\d+$")
_FLT = re.compile(r"^-?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


def _param(v):
    """Numeric parameters, or SQL-style quoted text (quotes are part of the input)."""
    if isinstance(v, str):
        s = v.strip()
        if len(s) >= 2 and s[0] == s[-1] == "'":
            return s[1:-1].replace("''", "'")
        if _NUM.match(s):
            return int(s)
        if _FLT.match(s):
            return float(s)
    return v


def _other_db(name: str) -> Path:
    w = Path(os.path.abspath(cfg().paths.work))
    return w / "re" / "symdb.sqlite" if name == "re" else w / "notes.sqlite"


@op("index.query", mcp="index_query",
    summary="Read-only SQL over index/re/notes. Views: v_model, v_inst, v_tex, v_file. Returns a table.",
    summary_ru="SQL только для чтения по индексу (или db=re|notes): только SELECT; начинать с представлений "
               "v_model, v_inst, v_tex, v_file.",
    mcp_group="index",
    examples=('satk index query "SELECT sid, name FROM v_model WHERE name LIKE \'infer%\'"',
              'satk index query "SELECT count(*) FROM inst WHERE model_id = ?" --params 17613'))
def index_query(sql: str, params: list[str] | None = None, db: Literal["index", "re", "notes"] = "index",
                limit: int = 200, profile: str = "vanilla") -> dict:
    """Run one read-only statement.

    Numeric parameters convert by default. Include single quotes in a parameter
    to force text: ``--params "'00123'"``. Double embedded quotes: ``'O''Brien'``.

    Args:
        sql: one SELECT/WITH statement.
        params: ? values: numbers convert; '00123' forces text.
        db: index (profile index) | re (symbol DB) | notes.
        limit: rows returned (total counts all).
    """
    ps = [_param(p) for p in (params or [])]
    if db == "index":
        d = _db(profile)
        return _with_stale(d.query(sql, ps, limit), d)
    path = _other_db(db)
    if not path.is_file():
        raise SatkError("NOT_READY", f"{db} database not built: {jpath(path)}",
                        hint="satk re build" if db == "re" else "satk note add <sid> <text>")
    conn = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = 1")
        return api.run_readonly_query(conn, sql, ps, limit)
    finally:
        conn.close()


# =========================================================================== diff


def _diff_sets(a: dict, b: dict, kind: str = "") -> tuple[list, list, list, int]:
    """(added, changed, removed, same-content-other-layer count)."""
    added = sorted(k for k in b if k not in a)
    removed = sorted(k for k in a if k not in b)
    if kind == "model":
        changed = sorted(k for k in a if k in b and a[k][:3] != b[k][:3])
        moved = sum(1 for k in a if k in b and a[k][:3] == b[k][:3] and a[k][3] != b[k][3])
    else:
        changed = sorted(k for k in a if k in b and a[k] != b[k])
        moved = 0
    return added, changed, removed, moved


def _snap(db: api.IndexDB, kind: str) -> dict[str, tuple]:
    c = db._conn()
    if kind == "model":  # compared by definition (name, txd, section); the layer is reported, not compared
        return {f"model:{i}": (n.lower(), (t or "").lower(), sec, layer) for i, n, t, sec, layer in c.execute(
            "SELECT m.id, m.name, m.txd, m.sec, l.name FROM model m JOIN layer l ON l.id=m.layer_id WHERE m.active=1")}
    if kind == "txd":
        return {f"txd:{n.lower()}": (h.hex() if h else None,) for n, h in c.execute(
            "SELECT t.name, b.hash FROM txd t JOIN blob b ON b.id=t.blob_id WHERE b.active=1 "
            "AND b.ns IN ('main','loose')")}
    if kind == "tex":
        return {f"tex:{t.lower()}/{x.lower()}": (h.hex(),) for t, x, h in c.execute(
            "SELECT t.name, x.name, x.hash FROM texture x JOIN txd t ON t.id=x.txd_id JOIN blob b ON b.id=t.blob_id "
            "WHERE b.active=1 AND b.ns IN ('main','loose')")}
    if kind == "file":
        return {f"file:{rel}" + (f"/{n.lower()}" if sk == "img" else ""): (h.hex() if h else None,)
                for rel, sk, n, h in c.execute("SELECT s.relpath, s.kind, b.name, b.hash FROM blob b "
                                               "JOIN source s ON s.id=b.source_id")}
    raise SatkError("BAD_PARAMS", f"kind must be model|txd|tex|file, got {kind!r}")


@op("index.diff", mcp="index_diff",
    summary="Compare two profile indexes (e.g. vanilla vs installed): models/TXDs/textures/files added, changed, "
            "removed. Summary + table.",
    summary_ru="Сравнить индексы двух профилей: какие модели, TXD, текстуры и файлы добавлены, изменены, удалены.",
    mcp_group="index",
    examples=("satk index diff vanilla installed --kind txd", "satk index diff vanilla samp --kind model"))
def index_diff(a: str | None, b: str | None, kind: Literal["model", "txd", "tex", "file"] | None = None,
               limit: int = 50, cursor: str | None = None) -> dict:
    """What differs between two profiles.

    Args:
        a: base profile (default vanilla).
        b: compared profile (default installed).
    """
    a, b = a or "vanilla", b or "installed"
    da, db_ = _db(a), _db(b)
    kinds = [kind] if kind else ["model", "txd", "tex", "file"]
    summary: dict[str, dict] = {}
    rows: list[list] = []
    for k in kinds:
        sa, sb = _snap(da, k), _snap(db_, k)
        added, changed, removed, moved = _diff_sets(sa, sb, k)
        label = "overridden" if k == "model" else "changed"
        summary[k] = {"added": len(added), label: len(changed), "removed": len(removed)}
        if moved:
            summary[k]["same_def_other_layer"] = moved
        for sid in added:
            rows.append([sid, k, "added", None, list(sb[sid])])
        for sid in changed:
            rows.append([sid, k, label, list(sa[sid]), list(sb[sid])])
        for sid in removed:
            rows.append([sid, k, "removed", list(sa[sid]), None])
    lim = env.clamp_limit(limit, default=50)
    off = api.cursor_offset(cursor)
    chunk = rows[off:off + lim]
    out = env.table(["id", "kind", "change", "a", "b"], chunk, total=len(rows),
                    next=f"o{off + lim}" if off + lim < len(rows) else None)
    out["summary"] = summary
    out["a"], out["b"] = a, b
    return out


# =========================================================================== asset.* / world.*


def _owner_provider(kind: str):
    try:
        return provider_for(kind)
    except SatkError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SatkError("NOT_READY", f"no provider for {kind}: {e}") from None


@op("asset.find",
    summary="Find assets by name: exact names first (warn counts names that only contain it); '*' '?' "
            "wildcards ('*tarmac*'). Table id, kind, name, info.",
    summary_ru="Поиск ассетов по имени: сначала точные совпадения, '*' — шаблон (подстрока/префикс).",
    mcp_group="index",
    examples=("satk asset find infernus", "satk asset find ws_rooftarmac1 --kind tex", "satk asset find '*grove*'"))
def asset_find(query: str, kind: FindKind | None = None, limit: int = 20, cursor: str | None = None,
               profile: str = "vanilla") -> dict:
    """Search names.

    Args:
        query: name or pattern with * and ?.
    """
    if kind in ("note", "fn", "g"):
        return _owner_provider(kind).find(query, kind, limit, cursor, profile)
    d = _db(profile)
    return _with_stale(d.find(query, kind, limit, cursor), d)


@op("asset.get",
    summary="Object for any SID (model:411, tex:<txd>/<name>, inst:<ipl>#<i>, file:..., fn:0x...) with links to "
            "related SIDs; fields reduces it.",
    summary_ru="Объект по любому SID со связями (links); fields сокращает ответ.",
    mcp_group="index",
    examples=("satk asset get model:411", "satk asset get inst:lae2_stream0#4", "satk asset get model:300@vanilla --profile samp"))
def asset_get(id: str, fields: list[str] | None = None, profile: str = "vanilla") -> dict:  # noqa: A002 - SPEC name
    """Get one object.

    Args:
        id: SID (model:infernus works too).
        fields: return only these fields.
    """
    s = api.parse_sid(id)  # + water:/tcyc:/handling: (api.LOCAL_KINDS) until satk.core.ids lists them
    if s.kind not in api.LOCAL_KINDS and KIND_OWNER.get(s.kind) != "index":
        return _owner_provider(s.kind).get(s, fields, profile)
    d = _db(profile)
    return _with_stale(d.get(str(s), fields), d)


@op("asset.refs",
    summary="Relations of a SID: without rel, counts per relation; with rel, a table of related SIDs.",
    summary_ru="Связи SID: без rel — сколько связей каждого вида; с rel — таблица связанных SID.",
    mcp_group="index",
    examples=("satk asset refs model:17613", "satk asset refs model:17613 --rel inst",
              "satk asset refs tex:bistro/vent_64 --rel models"))
def asset_refs(id: str, rel: str | None = None, limit: int = 50, cursor: str | None = None,  # noqa: A002
               profile: str = "vanilla") -> dict:
    """Follow relations.

    Args:
        rel: relation (model: inst|tex|dff|txd|col|ide|overrides; tex: models|same_pixels|txd; inst: model|lod|hd_children|ipl|near; ...).
    """
    s = api.parse_sid(id)
    if s.kind not in api.LOCAL_KINDS and KIND_OWNER.get(s.kind) != "index":
        return _owner_provider(s.kind).refs(s, rel, limit, cursor, profile)
    d = _db(profile)
    return _with_stale(d.refs(str(s), rel, limit, cursor), d)


def _area(v) -> int | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("any", "all", "*", ""):
        return None
    try:
        return int(s)
    except ValueError:
        raise SatkError("BAD_PARAMS", f"area must be a number or 'any', got {v!r}") from None


@op("world.near",
    summary="Placements near x,y within radius r, or in a box; nearest first.",
    summary_ru="Расстановки рядом с точкой (r) или в прямоугольнике box; сортировка по расстоянию.",
    mcp_group="index",
    examples=("satk world near 2495 -1687 --r 50", "satk world near --box 2445,-1737,2545,-1637 --match center "
              "--lod all --area any --limit 500"))
def world_near(x: float | None, y: float | None, z: float | None, r: float = 50.0, box: list[float] | None = None,
               match: Literal["aabb", "center"] = "aabb", kinds: list[str] | None = None, area: str = "0",
               lod: Literal["hd", "lod", "all"] = "hd", limit: int = 50, profile: str = "vanilla") -> dict:
    """Spatial query.

    Args:
        x: world X (omit with box).
        z: 3D distance when given.
        r: radius in metres.
        box: [x0, y0, x1, y1] instead of x/y/r.
        match: aabb (bounding boxes touch) | center (position inside).
        kinds: inst, item (IPL items), zone.
        area: interior area code, or 'any'.
        lod: hd (no LOD models) | lod | all.
    """
    b = None
    if box is not None:
        if len(box) != 4:
            raise SatkError("BAD_PARAMS", "box needs 4 numbers: x0,y0,x1,y1")
        b = tuple(float(v) for v in box)
    d = _db(profile)
    out = d.near(x, y, z, r, b, match, tuple(kinds or ("inst",)), _area(area), lod, limit)
    return _with_stale(out, d)


# =========================================================================== status / doctor


@status_provider("index")
def _status(deep: bool = False) -> dict:
    out = {}
    default = cfg().default_profile
    for name in cfg().profiles:
        st = _profile_state(name, deep)
        if st.get("built") or name == default:
            out[name] = st
    return out


@doctor_check("index_fresh")
def _doctor() -> dict:
    c = cfg()
    name = c.default_profile
    prof = c.profiles.get(name)
    if prof is not None and not prof.configured:
        return {"status": "warn", "msg": f"profile {name} is not configured: no game at {jpath(prof.root)}",
                "fix": "satk init"}
    st = _profile_state(name)
    if not st.get("built"):
        return {"status": "warn", "msg": f"index of profile {name} is not built", "fix": "satk index build"}
    if not st.get("fresh"):
        return {"status": "warn", "msg": f"index of profile {name} is stale: " + "; ".join(st.get("stale") or []),
                "fix": "satk index build"}
    return {"status": "ok", "msg": f"{name} index built {st.get('built_at')} ({st.get('size_mb')} MB)", "fix": None}
