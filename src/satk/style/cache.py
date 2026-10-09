"""Style cache: per-model metrics of a profile and their percentiles per peer set (``style.build``).

The cache is lazy: the first ``style.profile`` / ``asset.check`` / ``style.card`` builds it (vanilla:
about 10-20 s with a process pool), never ``index build``. Two files under ``<work>/style/``:

* ``<profile>-<index hash>-<sig>.json`` - profiles: for every peer set (class, parent class, ``<class>@<bucket>``)
  ``{metric: [p10, p50, p90, n]}``, exemplars, vehicle frame tables, and ``models`` = ``{id: [name, class,
  bucket]}``;
* ``<profile>-<index hash>-<sig>.models.json`` - the metrics of every model (leave-one-out validation).

``<index hash>`` is the index ``content_hash`` (a rebuilt index makes a new cache); ``<sig>`` hashes
:data:`CACHE_VERSION` and ``data/style/peers.json`` (a new class map makes a new cache). A model whose DFF
does not decode is counted in ``errors`` and skipped; the build never fails on one bad file.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from ..core import paths, resources
from ..core.errors import SatkError
from ..core.registry import report_progress
from . import classes as C

__all__ = ["CACHE_VERSION", "StyleCache", "cache_paths", "build", "load", "percentiles", "aggregate", "facts"]

#: Bump when :mod:`satk.style.measure` or the aggregation changes meaning (old caches are ignored).
CACHE_VERSION = 4
#: Below this many models the build runs in-process (a pool costs ~1 s to start on Windows).
POOL_MIN = 400
#: Metrics that are lists or bboxes: kept per model, never aggregated.
_NON_SCALAR = ("bbox", "geo.thirds")

_lock = threading.RLock()
_loaded: dict[str, "StyleCache"] = {}


def _sig() -> str:
    h = hashlib.sha1(f"satk.style.cache/{CACHE_VERSION}".encode())
    h.update(resources.read_bytes("style", "peers.json"))
    return h.hexdigest()[:8]


def _open_db(profile: str):
    from ..index.api import open_index

    return open_index(profile)


def cache_paths(profile: str = "vanilla", db=None) -> tuple[Path, Path]:
    """``(profiles file, models file)`` of a profile's current index (not created)."""
    db = db or _open_db(profile)
    h = (db.meta().get("content_hash") or "nohash")[:12]
    base = Path(os.path.abspath(paths.cfg().paths.work)) / "style" / f"{db.profile}-{h}-{_sig()}"
    return base.with_suffix(".json"), base.with_name(base.name + ".models.json")


# ----------------------------------------------------------------------------- facts (one SQL pass)
def facts(db) -> list[dict]:
    """One dict per active model with a DFF: identity, IDE facts, placements, collision, blob location.

    Uses a private read-only connection to the index file (``db.path``).
    """
    conn = sqlite3.connect("file:" + Path(db.path).as_posix() + "?mode=ro", uri=True)
    try:
        q = lambda s, *a: conn.execute(s, a).fetchall()  # noqa: E731
        inst: dict[int, list[int]] = {}
        # area 13 is drawn in every area (the engine's "everywhere" code): an exterior placement, not an interior
        for mid, interior, n in q("SELECT model_id, area NOT IN (0, 13), count(*) FROM inst GROUP BY 1, 2"):
            inst.setdefault(mid, [0, 0])[1 if interior else 0] += n
        as_lod = dict(q("SELECT j.model_id, count(*) FROM inst i JOIN inst j ON j.id = i.lod_id GROUP BY 1"))
        objdat = {r[0].lower() for r in q("SELECT name FROM object_data")}
        tex: dict[int, dict] = {}
        for mid, name, w, h in q("SELECT mt.model_id, lower(mt.texture), x.w, x.h FROM model_tex mt "
                                 "JOIN texture x ON x.id = mt.texture_id"):
            tex.setdefault(mid, {})[name] = (w, h)
        rows = q("""
            SELECT m.id, m.name, m.sec, m.draw, m.flags, m.extra, src.relpath,
                   la.root, s.relpath, b.abs_off, b.size,
                   c.spheres, c.boxes, c.faces, c.shadow_faces, c.surfaces
            FROM model m
            JOIN model_link l ON l.id = m.id
            JOIN dff d ON d.id = l.dff_id
            JOIN blob b ON b.id = d.blob_id
            JOIN source s ON s.id = b.source_id
            JOIN layer la ON la.id = s.layer_id
            LEFT JOIN ide i ON i.id = m.ide_id
            LEFT JOIN source src ON src.id = i.source_id
            LEFT JOIN col c ON c.id = l.col_id
            WHERE m.active = 1
            ORDER BY s.relpath, b.abs_off""")
    finally:
        conn.close()
    out = []
    for (mid, name, sec, draw, flags, extra, ide, root, rel, off, size, sph, box, faces, shadow, surf) in rows:
        try:
            ex = json.loads(extra) if extra else {}
        except ValueError:
            ex = {}
        f = {"id": int(mid), "name": str(name), "sec": str(sec), "draw": draw, "flags": flags,
             "ide": (ide or "").lower(), "type": ex.get("type"), "wheel_scale": ex.get("wheel_scale_f"),
             "path": str(Path(root) / Path(*str(rel).split("/"))), "off": int(off), "size": int(size),
             "objdat": str(name).lower() in objdat, "as_lod": int(as_lod.get(mid, 0)),
             "n_ext": inst.get(mid, [0, 0])[0], "n_int": inst.get(mid, [0, 0])[1],
             "tex": tex.get(mid) or {}}
        if sph is not None:
            try:
                sd = json.loads(surf) if surf else {}
            except ValueError:
                sd = {}
            f["col"] = {"spheres": sph, "boxes": box, "faces": faces, "shadow_faces": shadow, "surfaces": sd}
        out.append(f)
    return out


# ----------------------------------------------------------------------------- measuring (worker)
def _measure_chunk(items: list[dict]) -> list[dict]:
    """Worker: facts -> records ``{"id", "name", "cls", "bucket", "m", "frames"?}`` or ``{"id", "err"}``."""
    from ..model3d.mesh import build_scene
    from .measure import frame_names, measure

    out: list[dict] = []
    handles: dict[str, object] = {}
    try:
        for f in items:
            try:
                fh = handles.get(f["path"])
                if fh is None:
                    fh = handles[f["path"]] = paths.open_ro(f["path"])
                fh.seek(f["off"])
                buf = fh.read(f["size"])
                scene = build_scene(buf, name=f["name"], sec=f["sec"])
                m = measure(scene, tex_sizes={k: tuple(v) for k, v in f["tex"].items()},
                            wheel_scale=f.get("wheel_scale"), col=f.get("col"))
                cls, bucket = C.classify({**f, "dims": (m["dims.W"], m["dims.L"], m["dims.H"])}
                                         if "dims.L" in m else f)
                rec = {"id": f["id"], "name": f["name"], "cls": cls, "m": m}
                if bucket:
                    rec["bucket"] = bucket
                if f["sec"] == "cars":
                    rec["frames"] = _frame_rows(scene, frame_names(scene))
                out.append(rec)
            except Exception as e:  # noqa: BLE001 - one bad file never breaks the build
                out.append({"id": f["id"], "name": f["name"], "err": f"{type(e).__name__}: {str(e)[:160]}"})
    finally:
        for fh in handles.values():
            fh.close()
    return out


def _frame_rows(scene, names: list[str]) -> list[list]:
    """``[[name, parent name, x, y, z, has atomic]]`` in DFF order (model-space positions)."""
    rows = []
    atom = {p.frame for p in scene.parts if p.kind == "atomic"}
    roots = {f.idx for f in scene.frames if f.parent < 0}
    for f, n in zip(scene.frames, names):
        par = "<root>" if f.parent in roots else (names[f.parent] if 0 <= f.parent < len(names) else "")
        x, y, z = f.model[9:12]
        rows.append([n, par, round(x, 3), round(y, 3), round(z, 3), 1 if f.idx in atom else 0])
    return rows


def _run(items: list[dict], workers: int) -> list[dict]:
    total = len(items)
    report_progress(0, total, "style: measuring models")
    if workers <= 1 or total < POOL_MIN:
        out = []
        for i in range(0, total, 200):
            out.extend(_measure_chunk(items[i:i + 200]))
            report_progress(min(i + 200, total), total, "style: measuring models")
        return out
    from concurrent.futures import as_completed

    from ..core.config import SRC_ROOT
    from ..core.procpool import pool

    size = max(50, total // (workers * 6))
    chunks = [items[i:i + size] for i in range(0, total, size)]
    out: list[dict] = []
    old = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = str(SRC_ROOT) + (os.pathsep + old if old else "")
    try:
        with pool(workers, spawn=True) as ex:
            futs = {ex.submit(_measure_chunk, ch): len(ch) for ch in chunks}
            done = 0
            for fu in as_completed(futs):
                out.extend(fu.result())
                done += futs[fu]
                report_progress(done, total, "style: measuring models")
    finally:
        if old is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = old
    return out


# ----------------------------------------------------------------------------- aggregation
def percentiles(values: list[float]) -> list:
    """``[p10, p50, p90, n]`` (numpy linear interpolation), rounded for display."""
    import numpy as np

    a = np.asarray(values, dtype=np.float64)
    p = np.percentile(a, [10, 50, 90])
    nd = 0 if np.all(np.equal(np.mod(a, 1), 0)) and abs(p[1]) >= 10 else 4
    r = [float(round(float(x), nd)) for x in p]
    if nd == 0:
        r = [int(x) if x == int(x) else x for x in r]
    return [*r, int(len(a))]


def _groups(recs: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = {}
    for r in recs:
        keys = C.fallback_chain(C.peer_key(r["cls"], r.get("bucket")))
        for k in keys:
            g.setdefault(k, []).append(r)
    return g


def _main_metric(key: str) -> str:
    return "veh.hd_tris" if C.family(key) == "vehicle" else "geo.tris"


def aggregate(recs: list[dict]) -> dict:
    """Peer-set profiles from model records (``{"cls", "bucket"?, "m"}``); deterministic."""
    out: dict = {}
    for key, rs in sorted(_groups(recs).items()):
        vals: dict[str, list] = {}
        for r in rs:
            for k, v in r["m"].items():
                if k in _NON_SCALAR or not isinstance(v, (int, float)) or isinstance(v, bool) or v != v:
                    continue
                vals.setdefault(k, []).append(v)
        metrics = {k: percentiles(v) for k, v in sorted(vals.items()) if len(v) >= 3}
        main = _main_metric(key)
        ex = []
        if main in metrics:
            p50 = metrics[main][1]
            ex = sorted((abs(r["m"].get(main, 1e18) - p50), r["name"].lower(), r["id"]) for r in rs)
            ex = [[i, n] for _d, n, i in ex[:3]]
        out[key] = {"n": len(rs), "metrics": metrics, "exemplars": ex}
    return out


def _frames(recs: list[dict]) -> dict:
    """Vehicle frame tables per class: ``{class: [[name, share, parent, x, y, z, parent share], ...]}``
    (frames that at least half of the class has, in typical DFF order)."""
    import numpy as np

    out: dict = {}
    by: dict[str, list[dict]] = {}
    for r in recs:
        if "frames" in r:
            for k in C.fallback_chain(r["cls"]):
                by.setdefault(k, []).append(r)
    for key, rs in sorted(by.items()):
        seen: dict[str, list] = {}
        order: dict[str, list[int]] = {}
        for r in rs:
            names_here = set()
            for i, (n, par, x, y, z, _a) in enumerate(r["frames"]):
                if i == 0 or not n or n in names_here:
                    continue
                names_here.add(n)
                seen.setdefault(n, []).append((par, x, y, z))
                order.setdefault(n, []).append(i)
        rows = []
        for n, lst in seen.items():
            share = len(lst) / len(rs)
            if share < 0.5:
                continue
            par = max(set(p for p, *_ in lst), key=lambda p: (sum(1 for q, *_ in lst if q == p), p))
            pshare = sum(1 for q, *_ in lst if q == par) / len(lst)
            xyz = np.median(np.asarray([v[1:] for v in lst], dtype=np.float64), axis=0)
            rows.append([n, round(share, 3), par, *[round(float(c), 3) for c in xyz], round(pshare, 3),
                         int(np.median(order[n]))])
        rows.sort(key=lambda r: (r[-1], r[0]))
        out[key] = [r[:-1] for r in rows]
    return out


# ----------------------------------------------------------------------------- build / load
def build(profile: str = "vanilla", *, force: bool = False, workers: int | None = None, db=None) -> dict:
    """Measure every model of ``profile`` and write the cache files; returns a summary dict."""
    db = db or _open_db(profile)
    prof_path, models_path = cache_paths(profile, db)
    if not force and prof_path.is_file() and models_path.is_file():
        return {"file": prof_path, "models_file": models_path, "built": False}
    t0 = time.perf_counter()
    items = facts(db)
    if workers is None:
        workers = max(1, min(8, (os.cpu_count() or 2) - 1))
    recs = _run(items, workers)
    recs.sort(key=lambda r: r["id"])
    ok = [r for r in recs if "err" not in r]
    errs = [r for r in recs if "err" in r]
    profiles = aggregate(ok)
    data = {
        "format": "satk.style-cache/1", "version": CACHE_VERSION, "profile": db.profile,
        "index_hash": db.meta().get("content_hash", ""), "percentiles": "numpy.percentile, linear; [p10, p50, p90, n]",
        "models": {str(r["id"]): [r["name"], r["cls"], r.get("bucket")] for r in ok},
        "peers": profiles, "frames": _frames(ok),
        "errors": [[r["id"], r["name"], r["err"]] for r in errs][:200], "n_errors": len(errs),
    }
    paths.atomic_write(models_path, json.dumps({"format": "satk.style-models/1", "records": [
        {k: v for k, v in r.items() if k != "frames"} for r in ok]}, separators=(",", ":")))
    paths.atomic_write(prof_path, json.dumps(data, separators=(",", ":")))
    with _lock:
        _loaded.pop(str(prof_path), None)
    return {"file": prof_path, "models_file": models_path, "built": True, "models": len(ok), "errors": len(errs),
            "peer_sets": len(profiles), "seconds": round(time.perf_counter() - t0, 1), "workers": workers}


class StyleCache:
    """A loaded style cache (profiles file; the per-model file is read on demand)."""

    def __init__(self, path: Path, models_path: Path, data: dict):
        self.path, self.models_path, self.data = path, models_path, data
        self._records: list[dict] | None = None
        self._by_name: dict[str, int] | None = None

    @property
    def profile(self) -> str:
        return self.data["profile"]

    @property
    def peers(self) -> dict:
        return self.data["peers"]

    def model(self, mid: int) -> tuple[str, str, str | None] | None:
        r = self.data["models"].get(str(int(mid)))
        return tuple(r) if r else None

    def model_id(self, name: str) -> int | None:
        if self._by_name is None:
            self._by_name = {}
            for k, v in sorted(self.data["models"].items(), key=lambda kv: int(kv[0])):
                self._by_name.setdefault(v[0].lower(), int(k))
        return self._by_name.get((name or "").lower())

    def records(self) -> list[dict]:
        if self._records is None:
            self._records = json.loads(self.models_path.read_text(encoding="utf-8"))["records"]
        return self._records

    def record(self, mid: int) -> dict | None:
        for r in self.records():
            if r["id"] == int(mid):
                return r
        return None


def load(profile: str = "vanilla", *, auto_build: bool = True, db=None) -> StyleCache:
    """The style cache of ``profile`` (built on first use when ``auto_build``)."""
    db = db or _open_db(profile)
    prof_path, models_path = cache_paths(profile, db)
    with _lock:
        c = _loaded.get(str(prof_path))
        if c is not None:
            return c
        if not prof_path.is_file() or not models_path.is_file():
            if not auto_build:
                raise SatkError("NOT_FOUND", f"no style cache for profile {db.profile!r}",
                                hint=f"satk style build --profile {db.profile}")
            build(profile, db=db)
        try:
            data = json.loads(prof_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SatkError("INTERNAL", f"style cache {paths.jpath(prof_path)} is unreadable: {e}",
                            hint=f"satk style build --profile {db.profile} --force") from None
        c = StyleCache(prof_path, models_path, data)
        _loaded[str(prof_path)] = c
        return c
