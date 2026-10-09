"""The bench of ``satk ingame bench``: the run loop, the result file and the A/B comparison rules.

Flow of a run: ``hello`` (what the client build has: ``getEngineStats`` and friends are feature-detected in the
resource) -> console lines (``sae_preset``, ``sae_set``) -> for each stage of the scene: its own console lines,
``stage`` (the client Lua runs warm-up and sample), poll ``status`` until idle, ``result`` -> ``finish``.
Everything the client reports is data. The result is one JSON file ``work/out/bench/<run>/<scene>-<label>.json``
(schema ``satk-bench/1``); :func:`compare` applies the rules of the design to two of them.

Frame-time statistics are computed on the client from the per-frame ``timeSlice`` (exact percentiles of the sorted
frames, ``q`` = the 0..100th percentile); :func:`frame_stats` is the same arithmetic in Python (tests keep them
equal). Stdlib only.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Callable, Protocol

from ..core import paths
from ..core.errors import SatkError
from . import bench_scenes as BS

__all__ = ["SCHEMA", "YELLOW_MIB", "P50_TOL", "P99_TOL", "frame_stats", "pooled_percentile", "stage_metrics", "summarize",
           "result_path", "bench_dir", "load_result", "new_run_id", "run_scene", "compare", "BenchClient", "IncompleteRun",
           "parse_set", "build_document", "slug"]

SCHEMA = "satk-bench/1"
YELLOW_MIB = BS.YELLOW_MIB
P50_TOL = 5.0      # percent
P99_TOL = 10.0     # percent
MIN_FRAMES = 30    # fewer frames in a sample is not a measurement


class _Client(Protocol):
    def call(self, cmd: str, args: dict | None = None) -> dict: ...


# --------------------------------------------------------------------------- statistics


def frame_stats(frames: list[float], wall_ms: float | None = None) -> dict[str, Any]:
    """The statistics of the client (``stats.lua``) for a list of frame times in ms: ``n``, ``avg_ms``, ``p50_ms``,
    ``p95_ms``, ``p99_ms``, ``p999_ms``, ``max_ms``, ``fps_avg``, ``low1_fps`` (FPS of the mean of the slowest 1 %),
    ``q`` (101 percentiles)."""
    n = len(frames)
    if n == 0:
        return {"n": 0}
    s = sorted(frames)
    q = [round(s[int(math.floor(p / 100 * (n - 1) + 0.5))], 3) for p in range(101)]
    k = max(1, math.ceil(n * 0.01))
    tail = sum(s[n - k:]) / k
    avg = sum(s) / n
    out: dict[str, Any] = {
        "n": n, "avg_ms": round(avg, 3), "max_ms": round(s[-1], 3), "p50_ms": q[50], "p95_ms": q[95], "p99_ms": q[99],
        "p999_ms": round(s[int(math.floor(0.999 * (n - 1) + 0.5))], 3), "q": q,
        "fps_avg": round(1000 / avg, 2) if avg > 0 else None, "low1_fps": round(1000 / tail, 2) if tail > 0 else None,
        "integer_share": round(sum(1 for v in frames if v == int(v)) / n, 3)}
    if wall_ms:
        out["fps_wall"] = round(n * 1000 / wall_ms, 2)
    return out


def pooled_percentile(stages: list[dict[str, Any]], p: float) -> float | None:
    """A percentile over several stages: each stage's 101 stored percentiles weigh ``n / 101`` (approximate)."""
    pts: list[tuple[float, float]] = []
    for st in stages:
        f = st.get("frames") or {}
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


def stage_metrics(st: dict[str, Any]) -> dict[str, Any]:
    """Flat metrics of one stage (the numbers bench-compare and the summary table use)."""
    f = st.get("frames") or {}
    va = st.get("va") or {}
    clock = st.get("clock") or {}
    probe = st.get("probe") or {}
    out = {
        "n": f.get("n") or 0, "p50_ms": f.get("p50_ms"), "p95_ms": f.get("p95_ms"), "p99_ms": f.get("p99_ms"),
        "max_ms": f.get("max_ms"), "fps_avg": f.get("fps_avg"), "low1_fps": f.get("low1_fps"),
        "va_peak_mib": va.get("peak_mib") if va.get("peak_mib") is not None else va.get("end_mib"),
        "va_end_mib": va.get("end_mib"), "va_growth_mib": va.get("growth_mib"),
        "stream_peak_mib": (st.get("stream") or {}).get("peak_mib"), "clock_ratio": clock.get("ratio"),
        "frame_counter_hz": clock.get("frame_counter_hz"), "timer_mode": clock.get("timer_mode"),
        "shots_per_s": probe.get("shots_per_s") if probe.get("kind") == "weapon" else None,
    }
    if out["p50_ms"] is None and isinstance(f.get("q"), list) and len(f["q"]) == 101:
        out["p50_ms"], out["p95_ms"], out["p99_ms"] = f["q"][50], f["q"][95], f["q"][99]
    return out


def summarize(stages: list[dict[str, Any]]) -> dict[str, Any]:
    """Whole-run numbers: frames, FPS, pooled percentiles, worst low, peak VA and streaming, clock and probe values."""
    ms = [stage_metrics(s) for s in stages]
    frames = sum(m["n"] for m in ms)
    secs = sum((s.get("frames") or {}).get("sum_ms") or 0.0 for s in stages) / 1000.0
    out: dict[str, Any] = {"stages": len(stages), "frames": frames}
    if frames:
        out["fps_avg"] = round(frames / secs, 2) if secs > 0 else None
        for key, p in (("p50_ms", 50), ("p95_ms", 95), ("p99_ms", 99)):
            v = pooled_percentile(stages, p)
            out[key] = round(v, 3) if v is not None else None
        out["max_ms"] = max((m["max_ms"] for m in ms if m["max_ms"] is not None), default=None)
        lows = [m["low1_fps"] for m in ms if m["low1_fps"] is not None]
        out["low1_fps"] = min(lows) if lows else None
        if len(stages) > 1:
            out["pooled"] = True
    vas = [m["va_peak_mib"] for m in ms if m["va_peak_mib"] is not None]
    if vas:
        out["va_peak_mib"] = max(vas)
        out["va_growth_mib"] = round(sum(m["va_growth_mib"] or 0.0 for m in ms), 1)
    sp = [m["stream_peak_mib"] for m in ms if m["stream_peak_mib"] is not None]
    if sp:
        out["stream_peak_mib"] = max(sp)
    ratios = [m["clock_ratio"] for m in ms if m["clock_ratio"] is not None]
    if ratios:
        out["clock_ratio_min"], out["clock_ratio_max"] = min(ratios), max(ratios)
    shots = [m["shots_per_s"] for m in ms if m["shots_per_s"] is not None]
    if shots:
        out["shots_per_s"] = shots[0]
    return {k: v for k, v in out.items() if v is not None}


# --------------------------------------------------------------------------- files


def bench_dir(run: str | None = None) -> Path:
    return paths.work("out", "bench", *([run] if run else []))


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("._") or "x"


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def result_path(run: str, scene: str, label: str) -> Path:
    return bench_dir(slug(run)) / f"{str(scene).upper()}-{slug(label)}.json"


def load_result(spec: str) -> tuple[dict[str, Any], Path]:
    """A result by file path, by ``<run>/<scene>-<label>`` under ``work/out/bench``, or ``<scene>-<label>`` in the
    newest run that has it."""
    cands = [Path(spec), Path(str(spec) + ".json")]
    root = bench_dir()
    cands += [root / spec, root / (str(spec) + ".json")]
    for c in cands:
        if c.is_file():
            try:
                d = json.loads(c.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise SatkError("BAD_PARAMS", f"{c.name} is not a bench result: {e}") from None
            if not isinstance(d, dict) or d.get("schema") != SCHEMA:
                raise SatkError("BAD_PARAMS", f"{c.name} is not a {SCHEMA} result")
            return d, c
    if "/" not in str(spec).replace("\\", "/") and root.is_dir():
        runs = sorted((r for r in root.iterdir() if r.is_dir()), key=lambda r: r.name, reverse=True)
        for r in runs:
            f = r / (str(spec) + ".json")
            if f.is_file():
                return load_result(str(f))
    raise SatkError("NOT_FOUND", f"no bench result {spec!r}", hint="a path, <run>/<scene>-<label>, or satk ingame bench ...")


# --------------------------------------------------------------------------- the client


class BenchClient:
    """Calls the ``bench`` export of the client resource through a :class:`satk.ingame.session.Bridge`."""

    def __init__(self, bridge: Any, resource: str = "satk-bench"):
        self.bridge = bridge
        self.resource = resource

    def call(self, cmd: str, args: dict | None = None) -> dict:
        return self.bridge.rcall("client", self.resource, "bench", cmd, args or {})


# --------------------------------------------------------------------------- the run


def parse_set(items: list[str] | None) -> list[tuple[str, str]]:
    out = []
    for it in items or []:
        k, sep, v = str(it).partition("=")
        if not sep or not k.strip() or not re.fullmatch(r"[A-Za-z0-9_.-]+", k.strip()) or not v.strip():
            raise SatkError("BAD_PARAMS", f"--set expects cvar=value, got {it!r}")
        out.append((k.strip(), v.strip()))
    return out


def _check_word(name: str, value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", str(value)):
        raise SatkError("BAD_PARAMS", f"{name} must be a plain word, got {value!r}")
    return str(value)


def _verify_hello(hello: dict[str, Any], preset: str | None, profile: str | None, warn: list[str], *,
                  restart_hint: bool = True) -> None:
    for key, want in (("preset", preset), ("profile", profile)):
        got = hello.get(key)
        if want and got is not None and str(got) != str(want):
            warn.append(f"NOT_APPLIED: {key} is {got!r} after the request for {want!r}"
                        + (" (the capacity profile applies after a restart of the client)"
                           if key == "profile" and restart_hint else ""))
        elif want and got is None:
            warn.append(f"UNVERIFIED: the client does not report its {key} (getEnginePatchReport is missing)")


def run_scene(client: _Client, console: Callable[[str], bool], *, scene: str, label: str, run: str, preset: str | None = None,
              profile: str | None = None, duration: float | None = None, warmup: float | None = None,
              sets: list[tuple[str, str]] | None = None, progress: Callable[[int, int, str], None] | None = None,
              poll_s: float = 1.0, grace_s: float = 60.0, sleep: Callable[[float], None] = time.sleep,
              clock: Callable[[], float] = time.monotonic, launch: dict[str, str] | None = None,
              server_fps: Callable[[int | None], bool] | None = None) -> dict[str, Any]:
    """Run scene ``scene`` and return the result document (the caller writes it).

    ``launch``: the cvars the client was **started** with (``--preset``/``--profile``/``--cvar`` of the bench: the agent
    cannot reach the core console, so the client is relaunched with them); they are recorded and checked against
    ``hello``, not sent as console lines. ``server_fps(n)``: set the server's FPS limit for a stage that has
    ``server_fps`` (``n`` = ``None`` puts the old value back after the scene); returns True when the server took it.
    """
    stages = BS.resolve(scene, duration=duration, warmup=warmup)
    sc = BS.scene(scene)
    scene = str(scene).upper()
    warn: list[str] = []
    hello = client.call("hello")
    applied: list[dict[str, Any]] = []
    lines: list[tuple[str, str]] = []
    if preset:
        lines.append(("preset", f"sae_preset {_check_word('preset', preset)}"))
    if profile:
        lines.append(("profile", f"sae_set sae_limits {_check_word('profile', profile)}"))
    for k, v in sets or []:
        lines.append(("set", f"sae_set {k} {v}"))
    if launch is not None:
        applied.extend({"line": f"{k}={v}", "accepted": True, "via": "launch"} for k, v in launch.items())
        _verify_hello(hello, preset, profile, warn, restart_hint=False)
    else:
        for kind, line in lines:
            ok = console(line)
            applied.append({"line": line, "accepted": ok})
            if not ok:
                raise SatkError("EXTERNAL_TOOL", f"the game did not accept {line!r}",
                                hint="the console command exists only in the sa-engine fork client with a command handler "
                                     "reachable by the agent; omit --preset/--profile/--set for a stock client",
                                data={"kind": kind})
        if lines:
            sleep(0.5)
            hello2 = client.call("hello")
            _verify_hello(hello2, preset, profile, warn)
            hello = hello2
    res_stages: list[dict[str, Any]] = []
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    client.call("begin", {"run": run, "scene": scene})
    error: SatkError | None = None
    fps_touched = False
    try:
        for i, st in enumerate(stages, 1):
            if progress:
                progress(i - 1, len(stages), f"{scene} {st['id']}")
            cons = []
            sfps = st.pop("server_fps", None)
            if sfps is not None:
                took = bool(server_fps(int(sfps))) if server_fps is not None else False
                fps_touched = fps_touched or server_fps is not None
                cons.append({"line": f"setFPSLimit({int(sfps)}) on the server", "accepted": took})
                if not took:
                    warn.append(f"SERVER_FPS: the server did not take setFPSLimit({int(sfps)}) (stage {st['id']} runs with "
                                "the current limit; check fpslimit in the server config and the client fps_limit/vsync)")
            for line in st.pop("console", []):
                ok = console(line)
                cons.append({"line": line, "accepted": ok})
                if not ok:
                    warn.append(f"CONSOLE: {line!r} was not accepted (stage {st['id']} runs with the current setting)")
            client.call("stage", {"stage": st, "index": i, "warmup": st["warmup"], "sample": st["sample"]})
            planned = float(st["warmup"]) + float(st["sample"])
            deadline = clock() + planned + grace_s
            while True:
                s = client.call("status")
                if s.get("state") == "error":
                    raise SatkError("EXTERNAL_TOOL", f"the bench stage {st['id']} failed in the game: {s.get('err')}")
                if not s.get("busy"):
                    break
                if clock() > deadline:
                    raise SatkError("TIMEOUT", f"stage {st['id']} did not finish within {planned + grace_s:.0f} s",
                                    hint="is the game window minimised or paused? satk ingame status")
                sleep(poll_s)
            r = client.call("result", {"index": i})
            r["index"] = i
            if cons:
                r["console"] = cons
            res_stages.append(r)
            for w in r.get("warn") or []:
                warn.append(f"{st['id']}: {w}")
    except SatkError as e:
        error = e
    finally:
        try:
            client.call("finish")
        except SatkError as e:  # the game may be gone; the partial result is still worth keeping
            warn.append(f"{e.code}: finish: {e.msg}")
        if server_fps is not None and fps_touched:
            try:
                server_fps(None)
            except SatkError as e:
                warn.append(f"{e.code}: server FPS limit not put back: {e.msg}")
    doc = build_document(scene=scene, title=sc["title"], label=label, run=run, preset=preset, profile=profile,
                         duration=duration, warmup=warmup, sets=sets or [], hello=hello, stages=res_stages,
                         planned=len(stages), console=applied, warn=warn, started=started)
    if launch:
        doc["request"]["launch"] = dict(launch)
    if error is not None:
        doc["incomplete"] = True
        doc["error"] = {"code": error.code, "msg": error.msg}
        raise IncompleteRun(error, doc)
    return doc


class IncompleteRun(Exception):
    """A run that failed after some stages: carries the partial document so the caller can still save it."""

    def __init__(self, error: SatkError, doc: dict[str, Any]):
        super().__init__(error.msg)
        self.error = error
        self.doc = doc


def build_document(*, scene: str, title: str, label: str, run: str, preset: str | None, profile: str | None,
                   duration: float | None, warmup: float | None, sets: list[tuple[str, str]], hello: dict[str, Any],
                   stages: list[dict[str, Any]], planned: int, console: list[dict[str, Any]], warn: list[str],
                   started: str) -> dict[str, Any]:
    """The result file: request, what the client build is, the stages and the summary."""
    doc: dict[str, Any] = {
        "schema": SCHEMA, "run": run, "scene": scene, "title": title, "label": label, "started": started,
        "request": {"preset": preset, "profile": profile, "duration": duration, "warmup": warmup,
                    "set": [f"{k}={v}" for k, v in sets], "console": console},
        "client": {k: hello.get(k) for k in ("version", "features", "screen", "sae", "preset", "profile", "settings")
                   if hello.get(k) is not None},
        "stages": stages, "summary": summarize(stages), "planned_stages": planned,
    }
    if warn:
        doc["warn"] = warn
    return doc


# --------------------------------------------------------------------------- compare


def _pct(a: float, b: float) -> float:
    return (b - a) / a * 100.0


def _fmt(v: Any, nd: int = 2) -> Any:
    return None if v is None else (round(v, nd) if isinstance(v, float) else v)


def compare(a: dict[str, Any], b: dict[str, Any], *, p50_tol: float = P50_TOL, p99_tol: float = P99_TOL,
            yellow_mib: float = YELLOW_MIB) -> dict[str, Any]:
    """Rules of the design for B against the reference A, per stage present in both.

    * p50 frame time of B <= A + ``p50_tol`` %;
    * p99 frame time of B <= A + ``p99_tol`` %;
    * peak used VA of B below ``yellow_mib`` (3.0 GiB);
    * a stage with fewer than 30 frames in either run is no measurement (FAIL);
    * clock ratio, frame-counter rate and weapon shots per second are shown as information (``info``).

    Returns ``{rows, verdict, failed, warn}``; rows are ``[stage, metric, A, B, delta, limit, verdict]``.
    """
    warn: list[str] = []
    if a.get("scene") != b.get("scene"):
        warn.append(f"SCENE: A is {a.get('scene')}, B is {b.get('scene')}: comparing common stage ids only")
    sa = {s.get("id"): s for s in a.get("stages") or []}
    sb = {s.get("id"): s for s in b.get("stages") or []}
    common = [i for i in sa if i in sb]
    if not common:
        raise SatkError("BAD_PARAMS", "the two results have no stage in common",
                        data={"A": list(sa), "B": list(sb)})
    for side, d, other in (("A", sa, sb), ("B", sb, sa)):
        missing = [i for i in d if i not in other]
        if missing:
            warn.append(f"STAGES: {side} has stages the other run lacks: {', '.join(map(str, missing))}")
    if a.get("incomplete") or b.get("incomplete"):
        warn.append("INCOMPLETE: a run stopped early; its missing stages are not compared")
    rows: list[list] = []
    failed: list[str] = []

    def add(stage, metric, va, vb, delta, limit, verdict):
        rows.append([stage, metric, _fmt(va), _fmt(vb), delta, limit, verdict])
        if verdict == "FAIL":
            failed.append(f"{stage}/{metric}")

    for sid in common:
        ma, mb = stage_metrics(sa[sid]), stage_metrics(sb[sid])
        if ma["n"] < MIN_FRAMES or mb["n"] < MIN_FRAMES:
            add(sid, "frames", ma["n"], mb["n"], None, f">= {MIN_FRAMES}", "FAIL")
            continue
        for key, tol in (("p50_ms", p50_tol), ("p99_ms", p99_tol)):
            va, vb = ma[key], mb[key]
            if va is None or vb is None or va <= 0:
                add(sid, key, va, vb, None, f"<= +{tol:g} %", "n/a")
                continue
            d = _pct(va, vb)
            add(sid, key, va, vb, f"{d:+.1f} %", f"<= +{tol:g} %", "pass" if d <= tol + 1e-9 else "FAIL")
        pa, pb = ma["va_peak_mib"], mb["va_peak_mib"]
        if pb is None:
            add(sid, "va_peak_mib", pa, None, None, f"< {yellow_mib:g}", "n/a")
        else:
            add(sid, "va_peak_mib", pa, pb, f"{pb - pa:+.0f} MiB" if pa is not None else None, f"< {yellow_mib:g}",
                "pass" if pb < yellow_mib else "FAIL")
        for key in ("fps_avg", "low1_fps", "p95_ms", "max_ms", "clock_ratio", "frame_counter_hz", "shots_per_s"):
            va, vb = ma[key], mb[key]
            if va is not None or vb is not None:
                add(sid, key, va, vb, f"{_pct(va, vb):+.1f} %" if va and vb is not None else None, "", "info")
    verdict = "FAIL" if failed else "pass"
    return {"rows": rows, "verdict": verdict, "failed": failed, "warn": warn}
