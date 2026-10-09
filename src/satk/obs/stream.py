"""The JSONL stream ``sae-obs/1``: reading, semantic validation, summaries and merging. Stdlib only.

A stream is a header record (``rec: "hdr"``), then records of the kinds ``frame``, ``tick``, ``stats``, ``event``, ``net``,
``input``, ``seed``, ``snap`` and a footer ``end``, one JSON object per line. The same records travel inside the ``OBSJ``
chunks of a ``.saenet`` / ``.saerec`` container (:mod:`.container`).

* :class:`StreamChecker` is the validator: the schema of every record (``data/obs``) plus the rules JSON Schema cannot
  state (order of keys, the tick grid, mode consistency, counters, the footer).
* :class:`Accumulator` collects the numbers of one node's records; :func:`summarize_records` renders them and
  :func:`stage_from_acc` turns them into a ``sae-bench/1`` stage.
* :func:`merge_streams` joins several node streams by the timeline key.
"""

from __future__ import annotations

import heapq
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from ..core.errors import SatkError
from . import key as K
from . import schemas
from .stats import check_frame_stats, frame_stats, ms_stats

__all__ = ["SUPPORTED_MAJOR", "SUPPORTED_MINOR", "MAX_LINE", "Problem", "Report", "StreamChecker", "iter_jsonl", "check_records",
           "check_file", "Accumulator", "summarize_records", "stage_from_acc", "merge_streams", "dump_line", "write_jsonl",
           "read_jsonl"]

SUPPORTED_MAJOR = 1
SUPPORTED_MINOR = 0
MAX_LINE = 4 * 1024 * 1024
_SCHEMA_ID = re.compile(r"^sae-obs/([1-9][0-9]*)$")
_KINDS_WITH_KEY = ("frame", "tick", "stats", "event", "net", "input", "seed", "snap", "end")
_COUNTED = ("frame", "tick", "stats", "event", "net", "input", "seed", "snap")


# --------------------------------------------------------------------------- files


def dump_line(rec: dict[str, Any]) -> str:
    """One canonical JSONL line (compact separators, insertion order, no trailing newline)."""
    return json.dumps(rec, separators=(",", ":"), ensure_ascii=False)


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    """Write records as JSONL (UTF-8, ``\\n``); returns the number of lines."""
    n = 0
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(dump_line(r) + "\n")
            n += 1
    return n


def iter_jsonl(path: Path | str) -> Iterator[tuple[int, Any, str | None]]:
    """Yield ``(line number, record, error)`` for every non-empty line; ``record`` is ``None`` when the line is not JSON
    (``error`` says why). A line longer than :data:`MAX_LINE` is an error and is skipped."""
    with open(path, "rb") as f:
        lineno = 0
        while True:
            raw = f.readline(MAX_LINE + 1)
            if not raw:
                return
            lineno += 1
            if len(raw) > MAX_LINE:
                while raw and not raw.endswith(b"\n"):
                    raw = f.readline(MAX_LINE)
                yield lineno, None, f"line longer than {MAX_LINE} bytes"
                continue
            if lineno == 1 and raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            text = raw.strip()
            if not text:
                yield lineno, None, "empty line"
                continue
            try:
                yield lineno, json.loads(text.decode("utf-8")), None
            except UnicodeDecodeError as e:
                yield lineno, None, f"not UTF-8: {e.reason}"
            except ValueError as e:
                yield lineno, None, f"not JSON: {e}"


def read_jsonl(path: Path | str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """The records of a JSONL file; raises ``BAD_PARAMS`` on the first bad line."""
    out: list[dict[str, Any]] = []
    for lineno, rec, err in iter_jsonl(path):
        if err == "empty line":
            continue
        if err or not isinstance(rec, dict):
            raise SatkError("BAD_PARAMS", f"{Path(path).name} line {lineno}: {err or 'not a JSON object'}",
                            hint="satk obs validate " + str(path))
        out.append(rec)
        if limit and len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- the validator


@dataclass(frozen=True)
class Problem:
    where: str
    severity: str  # "error" | "warn"
    code: str
    msg: str

    def row(self) -> list[Any]:
        return [self.where, self.severity, self.code, self.msg]


@dataclass
class Report:
    """Result of a validation: the problems (capped), how many there were per code, and what was seen."""

    problems: list[Problem] = field(default_factory=list)
    by_code: dict[str, int] = field(default_factory=dict)
    records: int = 0
    kinds: dict[str, int] = field(default_factory=dict)
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    truncated: bool = False

    @property
    def errors(self) -> int:
        return sum(n for c, n in self.by_code.items() if c.startswith("E:"))

    @property
    def warnings(self) -> int:
        return sum(n for c, n in self.by_code.items() if c.startswith("W:"))

    @property
    def ok(self) -> bool:
        return self.errors == 0


@dataclass
class _Node:
    hdr: dict[str, Any] | None = None
    last_t: int = -1
    last_frame: int = -1
    last_sub: int = -1
    ticks_seen: int = 0
    rows: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    ctr: dict[str, float] = field(default_factory=dict)
    ended: bool = False

    @property
    def dt(self) -> int:
        return int(((self.hdr or {}).get("clock") or {}).get("dt_s_us") or 0)

    @property
    def sub_n(self) -> int:
        return int(((self.hdr or {}).get("clock") or {}).get("sub_n") or 0)

    @property
    def sim(self) -> str | None:
        return (self.hdr or {}).get("sim")


class StreamChecker:
    """Feed records in stream order with :meth:`feed`; :meth:`finish` returns the :class:`Report`.

    Problem codes (errors unless noted): ``JSON`` (a line is not a JSON object), ``SCHEMA`` (a record breaks its
    schema), ``HDR_FIRST`` / ``HDR_LATE`` / ``HDR_DUP`` (header placement), ``VERSION`` (major above 1; a newer minor is
    a warning), ``NODE`` (which node a record belongs to), ``ORDER`` (``t_us`` goes back), ``FRAME_ORDER`` /
    ``TICK_ORDER`` (frame or sub-tick counters do not advance), ``KEY_TICK`` / ``KEY_SUB`` (the key disagrees with the
    grid of the header), ``KEY_FRAME`` (a frame record without a frame number), ``MODE`` (a record contradicts the
    simulation mode of the header), ``CTR_DECREASE`` (a cumulative counter went down), ``END_COUNT`` / ``AFTER_END``
    (footer), ``EMPTY``; warnings: ``NO_END`` (no footer: the stream was cut), ``WORK`` (work above the frame
    interval), ``FENCE`` (a fence the header does not list), ``STATS`` (inconsistent statistics block), ``BLANK``.
    """

    def __init__(self, *, strict: bool = False, max_problems: int = 100):
        self.strict = strict
        self.max_problems = max_problems
        self.report = Report()
        self._nodes: dict[str, _Node] = {}
        self._order: list[str] = []
        self._rows_seen = False
        self._headerless = False

    # ------------------------------------------------------------------ problems

    def _add(self, where: str, severity: str, code: str, msg: str) -> None:
        rep = self.report
        key = ("E:" if severity == "error" else "W:") + code
        rep.by_code[key] = rep.by_code.get(key, 0) + 1
        if len(rep.problems) < self.max_problems:
            rep.problems.append(Problem(where, severity, code, msg))
        else:
            rep.truncated = True

    def error(self, where: str, code: str, msg: str) -> None:
        self._add(where, "error", code, msg)

    def warn(self, where: str, code: str, msg: str) -> None:
        self._add(where, "warn", code, msg)

    # ------------------------------------------------------------------ feeding

    def feed_bad_line(self, where: str, msg: str) -> None:
        if msg == "empty line":
            self.warn(where, "BLANK", "empty line")
        else:
            self.error(where, "JSON", msg)

    def feed(self, where: str, rec: Any) -> None:
        rep = self.report
        if not isinstance(rec, dict):
            self.error(where, "JSON", "a record must be a JSON object")
            return
        rep.records += 1
        kind = rec.get("rec")
        if isinstance(kind, str):
            rep.kinds[kind] = rep.kinds.get(kind, 0) + 1
        issues = schemas.check_record(rec, strict=self.strict)
        for i in issues[:4]:
            self.error(where, "SCHEMA", f"{kind or '?'}: {i.text()}")
        if issues:
            if kind == "hdr":
                self._note_hdr_attempt(where, rec)
            else:
                self._count_only(kind, rec)    # a record that fails its schema still counts, so the footer does not cascade
            return
        if kind == "hdr":
            self._hdr(where, rec)
        else:
            self._row(where, kind, rec)

    def _note_hdr_attempt(self, where: str, rec: dict[str, Any]) -> None:
        if self._rows_seen:
            self.error(where, "HDR_LATE", "a header after records")

    def _count_only(self, kind: Any, rec: dict[str, Any]) -> None:
        if kind not in _COUNTED or not self._nodes:
            return
        name = rec.get("node")
        st = self._nodes.get(name) if isinstance(name, str) else (next(iter(self._nodes.values())) if len(self._nodes) == 1 else None)
        if st is not None:
            st.counts[kind] = st.counts.get(kind, 0) + 1

    def _hdr(self, where: str, rec: dict[str, Any]) -> None:
        if self._rows_seen:
            self.error(where, "HDR_LATE", "a header after records: all headers must come before the first record")
        m = _SCHEMA_ID.match(rec["schema"])
        if m and int(m.group(1)) != SUPPORTED_MAJOR:
            self.error(where, "VERSION", f"major version {m.group(1)} is not supported (this reader knows {SUPPORTED_MAJOR})")
        if int(rec.get("minor", 0)) > SUPPORTED_MINOR:
            self.warn(where, "VERSION", f"minor version {rec['minor']} is newer than {SUPPORTED_MINOR}: unknown members are ignored")
        node = rec["node"]
        if node in self._nodes:
            self.error(where, "HDR_DUP", f"a second header for node {node!r}")
            return
        self._nodes[node] = _Node(hdr=rec)
        self._order.append(node)
        self.report.nodes[node] = {k: rec.get(k) for k in ("role", "sim", "pulse", "session") if rec.get(k) is not None} | {
            "dt_s_us": rec["clock"]["dt_s_us"]}

    def _node_of(self, where: str, rec: dict[str, Any]) -> _Node | None:
        if not self._nodes:
            if not self._headerless:
                self._headerless = True
                self.error(where, "HDR_FIRST", "a record before any header: the first record of a stream must be a 'hdr'")
            return self._nodes.setdefault("?", _Node())
        name = rec.get("node")
        if name is None:
            if len(self._nodes) == 1:
                return next(iter(self._nodes.values()))
            self.error(where, "NODE", "this stream has several headers: every record needs a 'node'")
            return None
        st = self._nodes.get(name)
        if st is None:
            self.error(where, "NODE", f"node {name!r} has no header")
        return st

    def _row(self, where: str, kind: str, rec: dict[str, Any]) -> None:
        self._rows_seen = True
        st = self._node_of(where, rec)
        if st is None:
            return
        st.rows += 1
        if st.ended:
            self.error(where, "AFTER_END", "a record after the footer of its node")
        t, tick, sub, frame = int(rec["t_us"]), int(rec["tick"]), int(rec["sub"]), int(rec["frame"])
        if t < st.last_t:
            self.error(where, "ORDER", f"t_us {t} is before the previous record's {st.last_t}")
        st.last_t = max(st.last_t, t)
        dt = st.dt
        if dt > 0:
            if tick != K.NONE and tick != t // dt:
                self.error(where, "KEY_TICK", f"tick {tick} is not t_us // dt_s_us = {t // dt}")
        elif tick != K.NONE:
            self.error(where, "KEY_TICK", f"tick {tick} although the header has no tick grid (dt_s_us 0)")
        sim = st.sim
        if sim == "classic":
            if sub != K.NONE:
                self.error(where, "MODE", "sub is set in a classic-mode stream (it must be 4294967295)")
            if kind == "tick":
                self.error(where, "MODE", "a tick record in a classic-mode stream")
        elif sim == "fixed" and kind == "tick" and (sub == K.NONE or tick == K.NONE):
            self.error(where, "MODE", "a tick record needs tick and sub")
        if sub != K.NONE and dt > 0 and st.sub_n > 0:
            grid = t * st.sub_n // dt
            if kind == "tick" and sub != grid:
                self.error(where, "KEY_SUB", f"sub {sub} is not t_us * sub_n // dt_s_us = {grid}")
            elif kind != "tick" and sub > grid:
                self.error(where, "KEY_SUB", f"sub {sub} is ahead of the grid ({grid})")
        if kind == "frame":
            if frame == K.NONE:
                self.error(where, "KEY_FRAME", "a frame record without a frame number")
            elif frame <= st.last_frame:
                self.error(where, "FRAME_ORDER", f"frame {frame} does not follow {st.last_frame}")
            else:
                st.last_frame = frame
            if sim == "classic" and rec["ticks"] > 1:
                self.error(where, "MODE", f"{rec['ticks']} ticks in a classic-mode frame (at most 1)")
            work = rec.get("work_ms")
            if work is not None and work > rec["frame_ms"] * 1.01 + 0.5:
                self.warn(where, "WORK", f"work_ms {work} exceeds frame_ms {rec['frame_ms']}")
            listed = (st.hdr or {}).get("fences")
            if listed and rec.get("fence_ms"):
                extra = sorted(set(rec["fence_ms"]) - set(listed))
                if extra:
                    self.warn(where, "FENCE", f"fences {', '.join(extra)} are not listed in the header")
        elif kind == "tick":
            if sub != K.NONE:
                if sub <= st.last_sub:
                    self.error(where, "TICK_ORDER", f"sub {sub} does not follow {st.last_sub}")
                st.last_sub = sub
        elif kind == "stats":
            for msg in check_frame_stats(rec["frames"]):
                self.warn(where, "STATS", msg)
        if kind in ("frame", "tick") and rec.get("ctr"):
            for name, v in rec["ctr"].items():
                prev = st.ctr.get(name)
                if prev is not None and v < prev - 1e-9:
                    self.error(where, "CTR_DECREASE", f"counter {name} went from {prev} to {v}")
                st.ctr[name] = v
        if kind == "end":
            st.ended = True
            counts = rec["counts"]
            for k in _COUNTED:
                if int(counts.get(k, 0)) != st.counts.get(k, 0):
                    self.error(where, "END_COUNT", f"footer counts {k} = {counts.get(k, 0)}, the stream has {st.counts.get(k, 0)}")
        else:
            st.counts[kind] = st.counts.get(kind, 0) + 1

    # ------------------------------------------------------------------ result

    def finish(self, *, require_records: bool = True) -> Report:
        rep = self.report
        if rep.records == 0 and require_records:
            self.error("file", "EMPTY", "no records")
        for name in self._order:
            st = self._nodes[name]
            if not st.ended:
                self.warn("file", "NO_END", f"node {name!r} has no footer: the stream was cut or is still being written")
        return rep


def check_records(records: Iterable[Any], *, strict: bool = False, max_problems: int = 100,
                  where: Callable[[int], str] = lambda i: f"record {i}") -> Report:
    """Validate an in-memory sequence of records."""
    ck = StreamChecker(strict=strict, max_problems=max_problems)
    for i, rec in enumerate(records, 1):
        ck.feed(where(i), rec)
    return ck.finish()


def check_file(path: Path | str, *, strict: bool = False, max_problems: int = 100) -> Report:
    """Validate a JSONL file."""
    ck = StreamChecker(strict=strict, max_problems=max_problems)
    for lineno, rec, err in iter_jsonl(path):
        where = f"line {lineno}"
        if err:
            ck.feed_bad_line(where, err)
        else:
            ck.feed(where, rec)
    return ck.finish()


# --------------------------------------------------------------------------- accumulation


class Accumulator:
    """The numbers of one node's records, collected in one pass (:meth:`add`)."""

    def __init__(self, hdr: dict[str, Any] | None = None):
        self.hdr = hdr
        self.kinds: dict[str, int] = {}
        self.t_first: int | None = None
        self.t_last: int | None = None
        self.tick_range: list[int] = []
        self.frame_range: list[int] = []
        self.frames: list[dict[str, Any]] = []
        self.tick_rows: list[tuple[int, float]] = []
        self.tick_records = 0
        self.stats_records: list[dict[str, Any]] = []
        self.events: dict[str, int] = {}
        self.net = {"tx": [0, 0], "rx": [0, 0]}
        self.end: dict[str, Any] | None = None

    def add(self, rec: dict[str, Any]) -> None:
        kind = rec.get("rec")
        self.kinds[kind] = self.kinds.get(kind, 0) + 1
        if kind == "hdr":
            self.hdr = self.hdr or rec
            return
        t = rec.get("t_us")
        if isinstance(t, int):
            self.t_first = t if self.t_first is None else min(self.t_first, t)
            self.t_last = t if self.t_last is None else max(self.t_last, t)
        for name, rng in (("tick", self.tick_range), ("frame", self.frame_range)):
            v = rec.get(name)
            if isinstance(v, int) and v != K.NONE:
                if not rng:
                    rng.extend([v, v])
                else:
                    rng[0], rng[1] = min(rng[0], v), max(rng[1], v)
        if kind == "frame":
            self.frames.append(rec)
        elif kind == "tick":
            self.tick_records += 1
            self.tick_rows.append((int(rec["t_us"]), float(rec["tick_ms"])))
        elif kind == "stats":
            self.stats_records.append(rec)
        elif kind == "event":
            self.events[rec["name"]] = self.events.get(rec["name"], 0) + 1
        elif kind == "net":
            d = self.net.get(rec.get("dir"))
            if d is not None:
                d[0] += 1
                d[1] += int(rec.get("bytes", 0))
        elif kind == "end":
            self.end = rec


def _fence_series(frames: list[dict[str, Any]], field_name: str = "fence_ms") -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for fr in frames:
        for name, v in (fr.get(field_name) or {}).items():
            out.setdefault(name, []).append(float(v))
    return out


def _last_max(frames: list[dict[str, Any]], member: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for fr in frames:
        for name, v in (fr.get(member) or {}).items():
            cur = out.get(name)
            if cur is None:
                out[name] = {"last": v, "max": v, "min": v}
            else:
                cur["last"] = v
                cur["max"] = max(cur["max"], v)
                cur["min"] = min(cur["min"], v)
    return out


def _round(v: Any, nd: int = 3) -> Any:
    return round(v, nd) if isinstance(v, float) else v


def _slim(block: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in block.items() if k in ("n", "avg_ms", "p99_ms", "max_ms")}


def summarize_acc(acc: Accumulator) -> dict[str, Any]:
    """A compact summary of one node (no ``q`` arrays)."""
    hdr = acc.hdr or {}
    out: dict[str, Any] = {"node": hdr.get("node"), "role": hdr.get("role"), "sim": hdr.get("sim"), "pulse": hdr.get("pulse"),
                           "dt_s_us": (hdr.get("clock") or {}).get("dt_s_us"), "engine": hdr.get("engine"),
                           "records": dict(sorted(acc.kinds.items()))}
    if acc.t_first is not None:
        out["span"] = {"t_first_us": acc.t_first, "t_last_us": acc.t_last, "seconds": round((acc.t_last - acc.t_first) / 1e6, 3)}
        if acc.tick_range:
            out["span"]["tick"] = acc.tick_range
        if acc.frame_range:
            out["span"]["frame"] = acc.frame_range
    fr = acc.frames
    if fr:
        fs = frame_stats(r["frame_ms"] for r in fr)
        fs.pop("q", None)
        out["frames"] = fs
        works = [r["work_ms"] for r in fr if "work_ms" in r]
        if works:
            out["work_ms"] = _slim(ms_stats(works))
        ticks = [int(r["ticks"]) for r in fr]
        out["ticks"] = {"n": sum(ticks), "per_frame_avg": round(sum(ticks) / len(ticks), 3), "max_per_frame": max(ticks),
                        "frames_over_1": sum(1 for v in ticks if v > 1), "frames_without": sum(1 for v in ticks if v == 0)}
        dropped = [float(r.get("dropped_ms", 0)) for r in fr]
        out["dropped_ms"] = {"total": round(sum(dropped), 3), "frames": sum(1 for v in dropped if v > 0), "max": round(max(dropped), 3)}
        fences = {k: _slim(ms_stats(v)) for k, v in sorted(_fence_series(fr).items())}
        if fences:
            out["fences"] = fences
        gpu = {k: _slim(ms_stats(v)) for k, v in sorted(_fence_series(fr, "gpu_ms").items())}
        if gpu:
            out["gpu"] = gpu
        ctr = _last_max(fr, "ctr")
        if ctr:
            out["ctr"] = {k: v["last"] for k, v in sorted(ctr.items())}
        gauge = _last_max(fr, "gauge")
        if gauge:
            out["gauge"] = {k: {"last": v["last"], "max": v["max"]} for k, v in sorted(gauge.items())}
        flags: dict[str, int] = {}
        for r in fr:
            for f in r.get("flags") or []:
                flags[f] = flags.get(f, 0) + 1
        if flags:
            out["flags"] = dict(sorted(flags.items()))
    if acc.tick_rows:
        out["tick_ms"] = _slim(ms_stats(v for _, v in acc.tick_rows))
    if acc.stats_records:
        last = acc.stats_records[-1]
        snap = {k: last[k] for k in ("game", "mem", "stream", "va_owner_mib") if k in last}
        if snap:
            out["last_stats"] = snap
        out["last_stats_frames"] = {k: last["frames"].get(k) for k in ("n", "avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms", "fps_avg", "low1_fps")
                                    if last["frames"].get(k) is not None}
    if acc.events:
        out["events"] = dict(sorted(acc.events.items()))
    if acc.net["tx"][0] or acc.net["rx"][0]:
        out["net"] = {d: {"rows": v[0], "bytes": v[1]} for d, v in acc.net.items() if v[0]}
    if acc.end:
        out["end"] = {k: acc.end.get(k) for k in ("counts", "dropped_records", "clean") if acc.end.get(k) is not None}
    return {k: v for k, v in out.items() if v is not None}


def accumulate(records: Iterable[dict[str, Any]]) -> dict[str, Accumulator]:
    """One :class:`Accumulator` per node (the header's ``node``, or ``node`` of each record in a merged stream)."""
    accs: dict[str, Accumulator] = {}
    only: str | None = None
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if rec.get("rec") == "hdr":
            name = rec.get("node", "?")
            accs.setdefault(name, Accumulator(rec))
            only = name if len(accs) == 1 else None
            continue
        name = rec.get("node") or only or "?"
        acc = accs.get(name)
        if acc is None:
            acc = accs[name] = Accumulator()
        acc.add(rec)
    return accs


def summarize_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summary of a record sequence: ``{"nodes": [per-node summary, ...]}``."""
    accs = accumulate(records)
    return {"nodes": [summarize_acc(a) for a in accs.values()]}


# --------------------------------------------------------------------------- stream -> bench stage


def stage_from_acc(acc: Accumulator, *, stage_id: str = "trace", title: str | None = None, skip_s: float = 0.0,
                   until_s: float | None = None) -> dict[str, Any]:
    """A ``sae-bench/1`` stage from the frame, tick and stats records of one node.

    ``skip_s`` drops the first seconds (warm-up) and ``until_s`` cuts the end, both counted from the first frame record.
    """
    frames = acc.frames
    if not frames:
        raise SatkError("BAD_PARAMS", "the stream has no frame records", hint="a bench stage needs 'frame' records")
    t0 = frames[0]["t_us"]
    lo = t0 + int(skip_s * 1e6)
    hi = t0 + int(until_s * 1e6) if until_s is not None else None
    sel = [f for f in frames if f["t_us"] >= lo and (hi is None or f["t_us"] <= hi)]
    if not sel:
        raise SatkError("BAD_PARAMS", "no frame record in the selected window", hint="lower --skip-s or raise --until-s")
    t_a, t_b = sel[0]["t_us"], sel[-1]["t_us"]
    span_ms = (t_b - t_a) / 1000.0 + sel[-1]["frame_ms"]
    stage: dict[str, Any] = {"id": stage_id, "index": 1, "title": title or "trace summary", "warmup_s": round(skip_s, 3),
                             "sample_s": round(span_ms / 1000.0, 3)}
    stage["key"] = {"start": K.of(sel[0]), "end": K.of(sel[-1])}
    stage["frames"] = frame_stats((f["frame_ms"] for f in sel), wall_ms=span_ms)
    ticks = [int(f["ticks"]) for f in sel]
    tk: dict[str, Any] = {"n": sum(ticks), "per_frame_avg": round(sum(ticks) / len(ticks), 3), "max_per_frame": max(ticks),
                          "dropped_ms": round(sum(float(f.get("dropped_ms", 0)) for f in sel), 3)}
    t_end_us = t_b + int(sel[-1]["frame_ms"] * 1000)
    tick_ms = [v for t, v in acc.tick_rows if t_a <= t <= t_end_us]
    if tick_ms:
        tk["tick_ms"] = ms_stats(tick_ms)
    stage["ticks"] = tk
    fences = {k: ms_stats(v) for k, v in sorted(_fence_series(sel).items())}
    if fences:
        stage["fences"] = fences
    gpu = {k: ms_stats(v) for k, v in sorted(_fence_series(sel, "gpu_ms").items())}
    if gpu:
        stage["gpu"] = gpu
    ctr = _last_max(sel, "ctr")
    if ctr:
        stage["ctr"] = {k: {"last": v["last"], "max": v["max"]} for k, v in sorted(ctr.items())}
    gauge = _last_max(sel, "gauge")
    if gauge:
        stage["gauge"] = {k: dict(v) for k, v in sorted(gauge.items())}
    in_win = [s for s in acc.stats_records if t_a <= s["t_us"] <= t_end_us]
    series_rows = _series(sel, in_win, t_a)
    stage["series"] = {"cols": ["t_s", "frames", "avg_ms", "max_ms", "ticks", "dropped_ms", "va_mib", "stream_mib"], "rows": series_rows}
    va = [s["mem"]["va_used_mib"] for s in in_win if "va_used_mib" in (s.get("mem") or {})]
    if va:
        stage["va"] = {"start_mib": va[0], "end_mib": va[-1], "peak_mib": max(va), "growth_mib": round(va[-1] - va[0], 3)}
    st = [s["stream"]["used_mib"] for s in in_win if "used_mib" in (s.get("stream") or {})]
    if st:
        stage["stream"] = {"start_mib": st[0], "end_mib": st[-1], "peak_mib": max(st)}
    if in_win:
        g = in_win[-1].get("game")
        if g:
            clock = {k: g[k] for k in ("clock_ratio", "frame_counter", "frame_counter_hz", "timer_mode") if k in g}
            if "clock_ratio" in clock:
                clock["ratio"] = clock.pop("clock_ratio")
            if "time_ms" in g:
                clock["game_ms"] = g["time_ms"]
            stage["clock"] = clock
        for member, snap in (("snap_start", in_win[0]), ("snap_end", in_win[-1])):
            body = {k: snap[k] for k in ("game", "mem", "stream", "va_owner_mib", "gpu_ms", "ctr", "gauge") if k in snap}
            if body:
                stage[member] = body
    return stage


def _series(frames: list[dict[str, Any]], stats: list[dict[str, Any]], t0: int) -> list[list[Any]]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for f in frames:
        buckets.setdefault((f["t_us"] - t0) // 1_000_000, []).append(f)
    srows = sorted(stats, key=lambda s: s["t_us"])
    rows: list[list[Any]] = []
    si = 0
    va = st = None
    for sec in sorted(buckets):
        end_us = t0 + (sec + 1) * 1_000_000
        while si < len(srows) and srows[si]["t_us"] < end_us:
            s = srows[si]
            va = (s.get("mem") or {}).get("va_used_mib", va)
            st = (s.get("stream") or {}).get("used_mib", st)
            si += 1
        fs = buckets[sec]
        ms = [f["frame_ms"] for f in fs]
        rows.append([sec + 1, len(fs), round(sum(ms) / len(ms), 3), round(max(ms), 3), sum(int(f["ticks"]) for f in fs),
                     round(sum(float(f.get("dropped_ms", 0)) for f in fs), 3), va, st])
    return rows


# --------------------------------------------------------------------------- merge


def merge_streams(streams: list[tuple[str, Iterable[dict[str, Any]]]]) -> Iterator[dict[str, Any]]:
    """Merge node streams into one by the timeline key.

    ``streams`` are ``(label, records)``; every stream starts with its header. The output has all headers first (each
    marked ``merged: true``), then the records of all nodes ordered by ``(t_us, tick, sub, frame)`` with ties broken by
    the order of ``streams``, each record carrying its ``node``. Raises ``BAD_PARAMS`` on a duplicate node id.
    """
    headers: list[dict[str, Any]] = []
    iters: list[Iterator[tuple[tuple[int, int, int, int], int, int, dict[str, Any]]]] = []
    seen: set[str] = set()

    def rows(idx: int, node: str, it: Iterator[dict[str, Any]]) -> Iterator[tuple[tuple[int, int, int, int], int, int, dict[str, Any]]]:
        for n, rec in enumerate(it):
            r = dict(rec)
            r.setdefault("node", node)
            yield K.sort_key(r), idx, n, r

    for idx, (label, recs) in enumerate(streams):
        it = iter(recs)
        first = next(it, None)
        if not isinstance(first, dict) or first.get("rec") != "hdr":
            raise SatkError("BAD_PARAMS", f"{label}: the first record is not a header", hint="satk obs validate " + label)
        node = first["node"]
        if node in seen:
            raise SatkError("BAD_PARAMS", f"node id {node!r} appears in two inputs ({label})", hint="give every producer its own node id")
        seen.add(node)
        hdr = dict(first)
        hdr["merged"] = True
        headers.append(hdr)
        iters.append(rows(idx, node, it))
    yield from headers
    for _, _, _, rec in heapq.merge(*iters, key=lambda x: (x[0], x[1], x[2])):
        yield rec
