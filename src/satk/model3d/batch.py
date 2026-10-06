"""Thumbnails of every model (``satk model image --all``, SPEC §4.4 / WP-05 acceptance 6).

Every active model with a DFF (``model_link.dff_id``) is rendered by the ``soft`` backend into the
content-addressed cache (``work/cache/model/<key>.png``); already cached thumbnails are skipped. Models
are sorted by TXD and split into chunks so that one worker process decodes a shared TXD (``vehicle``,
``generic`` ...) once. The manifest ``work/out/models/thumbs-<profile>-<size>x<views>.json`` maps every
model SID to its PNG; failures are listed there and in the result (never more than the first 50).
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import as_completed

from ..core import paths
from ..core.envelope import obj
from ..core.errors import SatkError
from ..core.registry import report_progress

__all__ = ["thumbnails", "render_chunk"]

_CHUNK = 40


def render_chunk(ids: list[int], views: int, size: int, profile: str, force: bool, el: float, bg) -> list[tuple]:
    """Worker: ``[(model_id, name, status, file | None, error | None, ms, blank_views)]`` with status
    ``written`` | ``cached`` | ``error``."""
    from . import api

    out = []
    db = api.open_db(profile)
    for mid in ids:
        t0 = time.perf_counter()
        try:
            r = api.image(f"model:{mid}", views, size, "soft", profile, el=el, bg=bg, force=force, db=db)
            out.append((mid, r.get("name"), "cached" if r.get("cached") else "written", r.get("file"), None,
                        round((time.perf_counter() - t0) * 1000, 1), (r.get("stats") or {}).get("blank_views", [])))
        except SatkError as e:
            out.append((mid, None, "error", None, f"{e.code}: {e.msg}", round((time.perf_counter() - t0) * 1000, 1), []))
        except Exception as e:  # noqa: BLE001 - one bad model must not stop the batch
            out.append((mid, None, "error", None, f"INTERNAL: {type(e).__name__}: {e}",
                        round((time.perf_counter() - t0) * 1000, 1), []))
    return out


def _chunks(models: list[tuple[int, str, str, str]], n: int) -> list[list[int]]:
    out: list[list[int]] = []
    cur: list[int] = []
    last_txd = None
    for mid, _name, _sec, txd in models:
        if len(cur) >= n and txd != last_txd:
            out.append(cur)
            cur = []
        cur.append(mid)
        last_txd = txd
        if len(cur) >= 4 * n:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def thumbnails(size: int = 128, views: int = 1, jobs: int = 12, profile: str = "vanilla", *, limit: int | None = None,
               force: bool = False, el: float = 25.0, bg=None) -> dict:
    """Render (or reuse) thumbnails of all models; returns counts, time, manifest path and failures."""
    from .resolve import list_models, open_db

    t0 = time.perf_counter()
    db = open_db(profile)
    models = list_models(db)
    total_models = len(models)
    if limit is not None:
        if limit < 1:
            raise SatkError("BAD_PARAMS", "limit must be >= 1")
        models = models[:limit]
    jobs = max(1, min(int(jobs), 61))
    chunks = _chunks(models, max(1, min(_CHUNK, -(-len(models) // (4 * jobs)))))
    results: list[tuple] = []
    jobs = max(1, min(jobs, len(chunks) or 1))
    report_progress(0, len(models), "thumbnails")
    if jobs == 1:
        for ch in chunks:
            results.extend(render_chunk(ch, views, size, profile, force, el, bg))
            report_progress(len(results), len(models), "thumbnails")
    else:
        from ..core.config import SRC_ROOT

        old = os.environ.get("PYTHONPATH")
        src = str(SRC_ROOT)
        os.environ["PYTHONPATH"] = src + (os.pathsep + old if old else "")
        try:
            from ..core.procpool import pool

            with pool(jobs, spawn=True) as ex:
                futs = [ex.submit(render_chunk, ch, views, size, profile, force, el, bg) for ch in chunks]
                for f in as_completed(futs):
                    results.extend(f.result())
                    report_progress(len(results), len(models), "thumbnails")
        finally:
            if old is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = old
    results.sort(key=lambda r: r[0])
    written = sum(1 for r in results if r[2] == "written")
    cached = sum(1 for r in results if r[2] == "cached")
    failed = [r for r in results if r[2] == "error"]
    blank_views = {f"model:{r[0]}": r[6] for r in results if r[6]}
    blank = sum(1 for empty in blank_views.values() if len(empty) == views)
    seconds = round(time.perf_counter() - t0, 1)
    manifest = paths.work("out", "models", f"thumbs-{profile}-{size}x{views}.json")
    doc = {"profile": profile, "size": size, "views": views, "models_with_dff": total_models, "rendered": len(results),
           "written": written, "cached": cached, "failed": len(failed), "blank": blank,
           "blank_views": blank_views, "seconds": seconds, "jobs": jobs,
           "thumbs": {f"model:{r[0]}": r[3] for r in results if r[3]},
           "errors": {f"model:{r[0]}": r[4] for r in failed}}
    paths.atomic_write(manifest, json.dumps(doc, indent=0, sort_keys=True, ensure_ascii=False))
    ms = sorted(r[5] for r in results)
    warn = []
    if failed:
        share = len(failed) / max(1, len(results))
        warn.append(f"FAILED: {len(failed)} models ({share:.2%}); see errors in the manifest")
    if blank_views:
        warn.append(f"BLANK_PREVIEW: {blank} completely blank models; {len(blank_views)} with empty views "
                    "(see blank_views in the manifest)")
    return obj(None, models=total_models, rendered=len(results), written=written, skipped=cached, failed=len(failed),
               blank=blank,
               seconds=seconds, jobs=jobs, ms_median=ms[len(ms) // 2] if ms else None, ms_max=ms[-1] if ms else None,
               manifest=paths.jpath(manifest), errors=[[f"model:{r[0]}", r[4]] for r in failed[:50]], warn=warn)
