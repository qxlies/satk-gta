"""The bench JSON ``sae-bench/1``: validation, summary, and converters from ``satk-bench/1`` and from JSONL streams.

``sae-bench/1`` is the one bench schema of the in-game bench (``satk ingame bench`` writes ``satk-bench/1``;
:func:`convert_satk_bench` turns such a file into this schema without loss: unmapped members are kept in ``ext`` and the
client tables in ``raw``) and of the later trace and fan-out bench packages. Stdlib only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..core.errors import SatkError
from . import schemas
from .schemas import BENCH_SCHEMA, LEGACY_BENCH_SCHEMA
from .stats import check_frame_stats, pooled_percentile
from .stream import Accumulator, Problem, Report, accumulate, stage_from_acc

__all__ = ["load_json", "stage_metrics", "summarize", "validate", "convert_satk_bench", "doc_from_records", "table_rows",
           "TABLE_COLS", "MIN_FRAMES", "detect_schema"]

MIN_FRAMES = 30
_STAT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")
TABLE_COLS = ["stage", "n", "p50_ms", "p95_ms", "p99_ms", "max_ms", "fps_avg", "low1_fps", "va_peak_mib", "stream_peak_mib", "clock_ratio"]


def load_json(path: Path | str) -> Any:
    """Read a JSON file (UTF-8, BOM tolerated); ``BAD_PARAMS`` with the position on a syntax error."""
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise SatkError("BAD_PARAMS", f"{p.name} is not JSON: {e}") from None


def detect_schema(doc: Any) -> str | None:
    return doc.get("schema") if isinstance(doc, dict) and isinstance(doc.get("schema"), str) else None


# --------------------------------------------------------------------------- metrics


def stage_metrics(st: dict[str, Any]) -> dict[str, Any]:
    """Flat numbers of one stage (a ``sae-bench/1`` stage or a ``satk-bench/1`` stage: the members they share)."""
    f = st.get("frames") or {}
    va = st.get("va") or {}
    clock = st.get("clock") or {}
    probe = st.get("probe") or {}
    out = {
        "n": f.get("n") or 0, "p50_ms": f.get("p50_ms"), "p95_ms": f.get("p95_ms"), "p99_ms": f.get("p99_ms"),
        "max_ms": f.get("max_ms"), "fps_avg": f.get("fps_avg"), "low1_fps": f.get("low1_fps"),
        "va_peak_mib": va.get("peak_mib") if va.get("peak_mib") is not None else va.get("end_mib"),
        "va_growth_mib": va.get("growth_mib"), "stream_peak_mib": (st.get("stream") or {}).get("peak_mib"),
        "clock_ratio": clock.get("ratio"), "frame_counter_hz": clock.get("frame_counter_hz"),
        "shots_per_s": probe.get("shots_per_s") if probe.get("kind") == "weapon" else None,
    }
    if out["p50_ms"] is None and isinstance(f.get("q"), list) and len(f["q"]) == 101:
        out["p50_ms"], out["p95_ms"], out["p99_ms"] = f["q"][50], f["q"][95], f["q"][99]
    return out


def summarize(stages: list[dict[str, Any]]) -> dict[str, Any]:
    """Whole-run numbers (the same arithmetic as ``satk-bench/1``): frames, FPS, pooled percentiles, worst low, peak VA and
    streaming, clock and probe values, plus ``ticks_per_frame`` and ``dropped_ms`` when the stages carry tick data."""
    ms = [stage_metrics(s) for s in stages]
    frames = sum(m["n"] for m in ms)
    secs = sum((s.get("frames") or {}).get("sum_ms") or 0.0 for s in stages) / 1000.0
    out: dict[str, Any] = {"stages": len(stages), "frames": frames}
    if frames:
        out["fps_avg"] = round(frames / secs, 2) if secs > 0 else None
        blocks = [s.get("frames") or {} for s in stages]
        for key, p in (("p50_ms", 50), ("p95_ms", 95), ("p99_ms", 99)):
            v = pooled_percentile(blocks, p)
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
    with_ticks = [s for s in stages if isinstance(s.get("ticks"), dict) and s["ticks"].get("n") is not None]
    if with_ticks:
        n_frames = sum((s.get("frames") or {}).get("n") or 0 for s in with_ticks)
        if n_frames:
            out["ticks_per_frame"] = round(sum(s["ticks"]["n"] for s in with_ticks) / n_frames, 3)
        dropped = [s["ticks"]["dropped_ms"] for s in with_ticks if s["ticks"].get("dropped_ms") is not None]
        if dropped:
            out["dropped_ms"] = round(sum(dropped), 3)
    return {k: v for k, v in out.items() if v is not None}


def table_rows(doc: dict[str, Any]) -> list[list[Any]]:
    """One row per stage in :data:`TABLE_COLS` order, then a ``(all)`` row from the summary."""
    rows = []
    for st in doc.get("stages") or []:
        m = stage_metrics(st)
        rows.append([st.get("id"), m["n"], m["p50_ms"], m["p95_ms"], m["p99_ms"], m["max_ms"], m["fps_avg"], m["low1_fps"],
                     m["va_peak_mib"], m["stream_peak_mib"], m["clock_ratio"]])
    s = doc.get("summary") or {}
    if len(rows) > 1 and s:
        rows.append(["(all)", s.get("frames"), s.get("p50_ms"), s.get("p95_ms"), s.get("p99_ms"), s.get("max_ms"), s.get("fps_avg"),
                     s.get("low1_fps"), s.get("va_peak_mib"), s.get("stream_peak_mib"), s.get("clock_ratio_min")])
    return rows


# --------------------------------------------------------------------------- validation


def validate(doc: Any, *, strict: bool = False, max_problems: int = 100) -> Report:
    """Schema and consistency checks of a ``sae-bench/1`` document.

    Codes: ``SCHEMA``, ``STAGE_ID`` (duplicate id), ``STATS`` (inconsistent frame statistics), ``FEW_FRAMES`` (warning:
    fewer than 30 frames is no measurement), ``SERIES`` (columns and rows disagree), ``VA`` (peak below start or end),
    ``SUMMARY`` (the summary does not match the stages), ``INCOMPLETE`` (warning).
    """
    rep = Report()

    def add(where: str, sev: str, code: str, msg: str) -> None:
        key = ("E:" if sev == "error" else "W:") + code
        rep.by_code[key] = rep.by_code.get(key, 0) + 1
        if len(rep.problems) < max_problems:
            rep.problems.append(Problem(where, sev, code, msg))
        else:
            rep.truncated = True

    rep.records = 1
    if not isinstance(doc, dict):
        add("$", "error", "SCHEMA", "a bench result must be a JSON object")
        return rep
    sch = doc.get("schema")
    if sch == LEGACY_BENCH_SCHEMA:
        add("schema", "error", "SCHEMA", f"this is a {LEGACY_BENCH_SCHEMA} file; convert it with 'satk obs convert' first")
        return rep
    for i in schemas.check_bench(doc, strict=strict):
        add(i.path, "error", "SCHEMA", i.msg)
    if rep.by_code.get("E:SCHEMA") and not isinstance(doc.get("stages"), list):
        return rep
    stages = [s for s in doc.get("stages") or [] if isinstance(s, dict)]
    seen: set[Any] = set()
    for i, st in enumerate(stages):
        where = f"stages[{i}]" + (f" ({st.get('id')})" if st.get("id") is not None else "")
        if st.get("id") in seen:
            add(where, "error", "STAGE_ID", f"duplicate stage id {st.get('id')!r}")
        seen.add(st.get("id"))
        fr = st.get("frames") or {}
        for msg in check_frame_stats(fr):
            add(where + ".frames", "error", "STATS", msg)
        if 0 < (fr.get("n") or 0) < MIN_FRAMES or fr.get("n") == 0:
            add(where + ".frames", "warn", "FEW_FRAMES", f"{fr.get('n') or 0} frames: fewer than {MIN_FRAMES} is no measurement")
        series = st.get("series")
        if isinstance(series, dict) and isinstance(series.get("cols"), list):
            cols, rows = series["cols"], series.get("rows") or []
            if cols and cols[0] != "t_s":
                add(where + ".series", "error", "SERIES", f"the first column must be t_s, not {cols[0]!r}")
            last_t = None
            for j, row in enumerate(rows):
                if not isinstance(row, list) or len(row) != len(cols):
                    add(where + f".series.rows[{j}]", "error", "SERIES", f"{len(row) if isinstance(row, list) else '?'} cells for {len(cols)} columns")
                    break
                if isinstance(row[0], (int, float)):
                    if last_t is not None and row[0] < last_t:
                        add(where + f".series.rows[{j}]", "error", "SERIES", "t_s goes back")
                        break
                    last_t = row[0]
        va = st.get("va") or {}
        if all(isinstance(va.get(k), (int, float)) for k in ("peak_mib", "start_mib", "end_mib")):
            if va["peak_mib"] + 1e-6 < max(va["start_mib"], va["end_mib"]):
                add(where + ".va", "warn", "VA", "peak_mib is below start_mib or end_mib")
    summ = doc.get("summary")
    if isinstance(summ, dict):
        calc = summarize(stages)
        if summ.get("stages") != len(stages):
            add("summary.stages", "error", "SUMMARY", f"summary says {summ.get('stages')} stages, the file has {len(stages)}")
        if summ.get("frames") != calc.get("frames", 0):
            add("summary.frames", "error", "SUMMARY", f"summary says {summ.get('frames')} frames, the stages hold {calc.get('frames', 0)}")
        for k in ("max_ms", "va_peak_mib", "stream_peak_mib", "p50_ms", "p95_ms", "p99_ms", "fps_avg", "low1_fps"):
            if k in summ and k in calc and abs(summ[k] - calc[k]) > 0.0015 + 0.001 * abs(calc[k]):
                add(f"summary.{k}", "error" if k in ("max_ms", "va_peak_mib", "stream_peak_mib") else "warn", "SUMMARY",
                    f"summary {k} = {summ[k]} but the stages give {calc[k]}")
    planned = doc.get("planned_stages")
    if isinstance(planned, int):
        if planned < len(stages):
            add("planned_stages", "warn", "INCOMPLETE", f"planned_stages {planned} is below the {len(stages)} stages present")
        elif planned > len(stages) and not doc.get("incomplete"):
            add("planned_stages", "warn", "INCOMPLETE", f"{planned} stages planned, {len(stages)} present, but incomplete is not set")
    return rep


# --------------------------------------------------------------------------- satk-bench/1 -> sae-bench/1


def _flatten_numbers(src: Any, prefix: str = "", depth: int = 6) -> dict[str, float]:
    out: dict[str, float] = {}
    if depth <= 0:
        return out
    if isinstance(src, dict):
        for k, v in src.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out[key] = v
            elif isinstance(v, (dict, list)):
                out.update(_flatten_numbers(v, key, depth - 1))
    elif isinstance(src, list):
        for i, v in enumerate(src):
            key = f"{prefix}.{i + 1}"
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[key] = v
            elif isinstance(v, (dict, list)):
                out.update(_flatten_numbers(v, key, depth - 1))
    return out


def _num(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def _snapshot(raw: Any) -> dict[str, Any]:
    """The normalised engine snapshot (``game``, ``mem``, ``stream``) of a ``satk-bench/1`` ``start``/``finish`` table."""
    if not isinstance(raw, dict):
        return {}
    eng = raw.get("engine") if isinstance(raw.get("engine"), dict) else {}
    mem = raw.get("mem") if isinstance(raw.get("mem"), dict) else {}
    game: dict[str, Any] = {}
    if _num(eng.get("gameTimeMs")) is not None and eng["gameTimeMs"] >= 0:
        game["time_ms"] = eng["gameTimeMs"]
    if eng.get("timerMode") in ("real", "stock", "off"):
        game["timer_mode"] = eng["timerMode"]
    if _num(eng.get("frameCounter")) is not None and eng["frameCounter"] >= 0:
        game["frame_counter"] = eng["frameCounter"]
    m: dict[str, Any] = {}
    va = _num(eng.get("vaUsedMiB"))
    va = va if va is not None else _num(raw.get("va_mib"))
    va = va if va is not None else _num(mem.get("virtual_mib"))
    if va is not None and va >= 0:
        m["va_used_mib"] = va
    if _num(mem.get("private_mib")) is not None and mem["private_mib"] >= 0:
        m["private_mib"] = mem["private_mib"]
    if _num(mem.get("resident_mib")) is not None and mem["resident_mib"] >= 0:
        m["working_set_mib"] = mem["resident_mib"]
    if eng.get("vaGuard") in ("ok", "yellow", "red", "critical"):
        m["va_guard"] = eng["vaGuard"]
    if isinstance(eng.get("cefLoaded"), bool):
        m["cef_loaded"] = eng["cefLoaded"]
    out: dict[str, Any] = {}
    if game:
        out["game"] = game
    if m:
        out["mem"] = m
    sm = _num(raw.get("stream_mib"))
    if sm is not None and sm >= 0:
        out["stream"] = {"used_mib": sm}
    return out


_DIRECT = ("id", "index", "title", "warmup_s", "sample_s", "frames", "va", "stream", "warn", "counts", "probe", "camera", "fps_limit",
           "console")
_MAPPED = {"clock", "series", "limits_max", "limits_last", "start", "finish"}


def _convert_stage(st: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {k: st[k] for k in _DIRECT if k in st and st[k] is not None}
    clock = st.get("clock")
    if isinstance(clock, dict):
        c = {k: v for k, v in clock.items() if k != "tick_ms"}
        if "tick_ms" in clock:
            c["tick_count_ms"] = clock["tick_ms"]
        out["clock"] = c
    series = st.get("series")
    if isinstance(series, list):
        width = {len(r) for r in series if isinstance(r, list)}
        if width <= {6}:
            out["series"] = {"cols": ["t_s", "frames", "avg_ms", "max_ms", "va_mib", "stream_mib"], "rows": series}
        else:
            out.setdefault("ext", {})["series"] = series
    lmax = _flatten_numbers(st.get("limits_max"))
    llast = _flatten_numbers(st.get("limits_last"))
    gauge: dict[str, Any] = {}
    leftovers: dict[str, Any] = {}
    for name in sorted(set(lmax) | set(llast)):
        if not _STAT_NAME.match(name):
            leftovers[name] = {"max": lmax.get(name), "last": llast.get(name)}
            continue
        entry = {k: v for k, v in (("last", llast.get(name)), ("max", lmax.get(name))) if v is not None}
        gauge[name] = entry
    if gauge:
        out["gauge"] = gauge
    if leftovers:
        out.setdefault("ext", {})["limits"] = leftovers
    for src, dst in (("start", "snap_start"), ("finish", "snap_end")):
        snap = _snapshot(st.get(src))
        if snap:
            out[dst] = snap
    raw = {k: st[k] for k in ("start", "finish") if isinstance(st.get(k), dict)}
    if raw:
        out["raw"] = raw
    rest = {k: v for k, v in st.items() if k not in _DIRECT and k not in _MAPPED}
    if rest:
        out.setdefault("ext", {}).update(rest)
    return out


def convert_satk_bench(doc: dict[str, Any]) -> dict[str, Any]:
    """A ``satk-bench/1`` result as a ``sae-bench/1`` document (nothing is lost: see ``ext`` and ``raw`` of the stages)."""
    if not isinstance(doc, dict) or doc.get("schema") != LEGACY_BENCH_SCHEMA:
        raise SatkError("BAD_PARAMS", f"not a {LEGACY_BENCH_SCHEMA} result", hint="a file written by 'satk ingame bench'")
    client = doc.get("client") if isinstance(doc.get("client"), dict) else {}
    env: dict[str, Any] = {"role": "client"}
    for k in ("sae", "version", "preset", "profile", "features", "screen", "settings"):
        if client.get(k) is not None:
            env[k] = client[k]
    stages = [_convert_stage(s) for s in doc.get("stages") or [] if isinstance(s, dict)]
    out: dict[str, Any] = {
        "schema": BENCH_SCHEMA, "run": str(doc.get("run", "")), "scene": str(doc.get("scene", "")),
        "label": str(doc.get("label", "")),
    }
    for k in ("title", "started"):
        if doc.get(k):
            out[k] = doc[k]
    out["source"] = {"tool": "satk-ingame-bench", "converted_from": LEGACY_BENCH_SCHEMA}
    if isinstance(doc.get("request"), dict):
        out["request"] = doc["request"]
    out["env"] = env
    out["stages"] = stages
    out["summary"] = summarize(stages)
    if doc.get("planned_stages") is not None:
        out["planned_stages"] = doc["planned_stages"]
    for k in ("incomplete", "error", "warn"):
        if doc.get(k):
            out[k] = doc[k]
    return out


# --------------------------------------------------------------------------- stream -> sae-bench/1


def doc_from_records(records: list[dict[str, Any]] | Any, *, run: str, scene: str = "trace", label: str | None = None,
                     skip_s: float = 0.0, until_s: float | None = None, tool: str = "obs-convert") -> dict[str, Any]:
    """A ``sae-bench/1`` document from stream records: one stage per node that has frame records."""
    accs = accumulate(records)
    stages: list[dict[str, Any]] = []
    first_hdr: dict[str, Any] | None = None
    for name, acc in accs.items():
        if acc.hdr and first_hdr is None:
            first_hdr = acc.hdr
        if not acc.frames:
            continue
        st = stage_from_acc(acc, stage_id=name, title=f"trace of {name}", skip_s=skip_s, until_s=until_s)
        st["index"] = len(stages) + 1
        stages.append(st)
    if not stages:
        raise SatkError("BAD_PARAMS", "no node of the stream has frame records", hint="satk obs summarize <file> shows what it holds")
    hdr = first_hdr or {}
    env: dict[str, Any] = {"role": hdr.get("role", "client")}
    eng = hdr.get("engine") or {}
    for k_dst, v in (("build", eng.get("build")), ("preset", eng.get("preset")), ("profile", eng.get("profile")), ("sim", hdr.get("sim")),
                     ("pulse", hdr.get("pulse"))):
        if v is not None:
            env[k_dst] = v
    out: dict[str, Any] = {
        "schema": BENCH_SCHEMA, "run": run, "scene": scene, "label": label or hdr.get("node") or "trace",
        "title": f"{scene} from a sae-obs/1 stream", "source": {"tool": tool, "converted_from": "sae-obs/1"}, "env": env,
        "key": {"first": stages[0]["key"]["start"], "last": stages[-1]["key"]["end"]}, "stages": stages,
        "summary": summarize(stages), "planned_stages": len(stages)}
    if out["key"]["first"] is None or any(k not in out["key"]["first"] for k in ("t_us", "tick", "sub", "frame")):
        del out["key"]
    return out
