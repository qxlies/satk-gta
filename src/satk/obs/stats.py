"""Statistics of duration series (milliseconds) with the arithmetic of ``satk-bench/1``. Stdlib only.

:func:`frame_stats` reproduces ``satk.ingame.bench.frame_stats`` and the client script ``stats.lua`` of the bench
resource (a test keeps the first two equal): exact percentiles of the sorted frames, ``q`` = the 0th..100th percentile.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

__all__ = ["frame_stats", "ms_stats", "percentile", "pooled_percentile", "check_frame_stats", "fps_of"]


def percentile(sorted_vals: Sequence[float], p: float) -> float:
    """The ``p``-th percentile (0..100) of an ascending list: the nearest rank used by the bench client."""
    n = len(sorted_vals)
    return sorted_vals[int(math.floor(p / 100 * (n - 1) + 0.5))]


def fps_of(avg_ms: float) -> float | None:
    return round(1000 / avg_ms, 2) if avg_ms > 0 else None


def frame_stats(frames: Iterable[float], wall_ms: float | None = None) -> dict[str, Any]:
    """``n``, ``sum_ms``, ``avg_ms``, ``p50/p95/p99/p999/max``, ``fps_avg``, ``low1_fps`` (FPS of the mean of the slowest
    1 %), ``integer_share``, ``q`` (101 percentiles); ``wall_ms`` and ``fps_wall`` when the real time is given."""
    vals = [float(v) for v in frames]
    n = len(vals)
    if n == 0:
        return {"n": 0}
    s = sorted(vals)
    q = [round(percentile(s, p), 3) for p in range(101)]
    k = max(1, math.ceil(n * 0.01))
    tail = sum(s[n - k:]) / k
    total = sum(s)
    avg = total / n
    out: dict[str, Any] = {
        "n": n, "sum_ms": round(total, 1), "avg_ms": round(avg, 3), "max_ms": round(s[-1], 3), "p50_ms": q[50],
        "p95_ms": q[95], "p99_ms": q[99], "p999_ms": round(percentile(s, 99.9), 3), "q": q,
        "fps_avg": fps_of(avg), "low1_fps": round(1000 / tail, 2) if tail > 0 else None,
        "integer_share": round(sum(1 for v in vals if v == int(v)) / n, 3)}
    if wall_ms and wall_ms > 0:
        out["wall_ms"] = round(wall_ms, 1)
        out["fps_wall"] = round(n * 1000 / wall_ms, 2)
    return {k2: v for k2, v in out.items() if v is not None}


def ms_stats(values: Iterable[float]) -> dict[str, Any]:
    """The short block: ``n``, ``avg_ms``, ``p50_ms``, ``p95_ms``, ``p99_ms``, ``max_ms``, ``sum_ms``."""
    s = sorted(float(v) for v in values)
    n = len(s)
    if n == 0:
        return {"n": 0}
    total = sum(s)
    return {"n": n, "avg_ms": round(total / n, 3), "p50_ms": round(percentile(s, 50), 3),
            "p95_ms": round(percentile(s, 95), 3), "p99_ms": round(percentile(s, 99), 3), "max_ms": round(s[-1], 3),
            "sum_ms": round(total, 3)}


def pooled_percentile(blocks: Sequence[dict[str, Any]], p: float) -> float | None:
    """A percentile over several frame-statistics blocks: each block's 101 stored percentiles weigh ``n / 101``."""
    pts: list[tuple[float, float]] = []
    for f in blocks:
        q = f.get("q")
        if isinstance(q, list) and q and f.get("n"):
            w = f["n"] / len(q)
            pts += [(float(v), w) for v in q]
    if not pts:
        return None
    pts.sort()
    total = sum(w for _, w in pts)
    acc = 0.0
    for v, w in pts:
        acc += w
        if acc >= total * p / 100.0 - 1e-9:
            return v
    return pts[-1][0]


def check_frame_stats(fs: dict[str, Any], *, tol: float = 0.0015) -> list[str]:
    """Consistency problems of a frame-statistics block (empty = consistent): percentile order, ``q`` against the named
    percentiles, FPS against the average."""
    out: list[str] = []
    n = fs.get("n")
    if not isinstance(n, int) or n <= 0:
        return out
    named = [fs.get(k) for k in ("p50_ms", "p95_ms", "p99_ms", "max_ms")]
    if all(isinstance(v, (int, float)) for v in named) and not (
            named[0] <= named[1] + 1e-9 and named[1] <= named[2] + 1e-9 and named[2] <= named[3] + 1e-9):
        out.append("p50_ms <= p95_ms <= p99_ms <= max_ms does not hold")
    avg, mx = fs.get("avg_ms"), fs.get("max_ms")
    if isinstance(avg, (int, float)) and isinstance(mx, (int, float)) and avg > mx + 1e-6:
        out.append("avg_ms is above max_ms")
    q = fs.get("q")
    if isinstance(q, list) and len(q) == 101:
        if any(q[i] > q[i + 1] + 1e-9 for i in range(100)):
            out.append("q is not ascending")
        for idx, key in ((50, "p50_ms"), (95, "p95_ms"), (99, "p99_ms"), (100, "max_ms")):
            v = fs.get(key)
            if isinstance(v, (int, float)) and abs(q[idx] - v) > 1e-6 + tol * abs(v):
                out.append(f"q[{idx}] = {q[idx]} differs from {key} = {v}")
    fps = fs.get("fps_avg")
    if isinstance(fps, (int, float)) and isinstance(avg, (int, float)) and avg > 0 and fps > 0:
        if abs(fps - 1000 / avg) > 0.01 + 0.005 * fps:
            out.append(f"fps_avg {fps} differs from 1000 / avg_ms = {1000 / avg:.2f}")
    return out
