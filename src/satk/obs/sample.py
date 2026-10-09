"""Deterministic example streams: the data of the docs, the tests and the golden fixtures. No game data, no clocks.

The numbers are synthetic (a small linear congruential generator with a fixed seed), but the streams obey every rule of
the specification, so they validate cleanly and are the reference for writers in other languages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import key as K
from .container import pack_records
from .stats import frame_stats
from .stream import write_jsonl

__all__ = ["client_stream", "server_stream", "net_stream", "with_end", "write_samples", "SESSION", "FENCES"]

SESSION = "5a3e0001"
FENCES = ["F0", "F1", "F1a", "F2", "F3a", "F3b", "F4", "F5", "F6", "F7"]
START_US = 600_000_000


class _Rng:
    """A fixed LCG: the same seed gives the same numbers on every Python version."""

    def __init__(self, seed: int):
        self.s = (seed & 0x7FFFFFFF) or 1

    def next(self) -> float:
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s / 0x80000000

    def around(self, mid: float, spread: float) -> float:
        return mid + (self.next() - 0.5) * 2 * spread


def _r(v: float, nd: int = 3) -> float:
    return round(v, nd)


def with_end(records: list[dict[str, Any]], *, node: str | None = None, clean: bool = True) -> list[dict[str, Any]]:
    """``records`` plus the ``end`` footer for the rows of ``node`` (all rows when None); the footer takes the last key."""
    rows = [r for r in records if r.get("rec") not in ("hdr", "end") and (node is None or r.get("node", node) == node)]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["rec"]] = counts.get(r["rec"], 0) + 1
    last = rows[-1] if rows else {"t_us": 0, "tick": K.NONE, "sub": K.NONE, "frame": K.NONE}
    end: dict[str, Any] = {"rec": "end", **K.of(last), "counts": dict(sorted(counts.items())), "dropped_records": 0, "clean": clean}
    if node is not None and any("node" in r for r in rows):
        end["node"] = node
    return [*records, end]


def client_stream(*, node: str = "client-1", sim: str = "fixed", pulse: str = "standard", seconds: float = 3.0, fps: int = 60,
                  tick_hz: int = 30, sub_n: int = 2, seed: int = 1, start_us: int = START_US, end: bool = True,
                  max_ticks: int = 4) -> list[dict[str, Any]]:
    """A client stream: header, per-frame records (and per-tick records in fixed mode), a stats record per second, footer."""
    rng = _Rng(seed)
    dt_s = 1_000_000 // tick_hz
    hdr: dict[str, Any] = {
        "rec": "hdr", "schema": "sae-obs/1", "minor": 0, "role": "client", "node": node, "session": SESSION,
        "clock": {"dt_s_us": dt_s, "sub_n": sub_n, "domain": "server"}, "sim": sim, "pulse": pulse, "fences": FENCES,
        "engine": {"build": "sae-sample", "arch": "x86", "preset": "classic"}, "producer": {"name": "satk-obs-sample", "version": "1"}}
    out: list[dict[str, Any]] = [hdr]
    base_ms = 1000.0 / fps
    t = start_us
    frame = 100
    k_last = K.sub_of(t, dt_s, sub_n)
    window: list[float] = []
    ctr = {"sim.dropped_ms": 0.0, "timer.reentry": 0}
    next_stats = t + 1_000_000
    n_frames = int(seconds * fps)
    prev_t = None
    dt_c_us = dt_s / sub_n
    for i in range(n_frames):
        spike = i > 0 and i % 97 == 0
        step_ms = base_ms * (5.0 if spike else 1.0) + rng.around(0, 0.15)
        if i > 0:
            t += int(round(step_ms * 1000))
        frame_ms = _r((t - prev_t) / 1000.0, 3) if prev_t is not None else _r(base_ms, 3)
        prev_t = t
        ticks = 0
        dropped = 0.0
        tick_rows: list[dict[str, Any]] = []
        if sim == "fixed":
            k_grid = K.sub_of(t, dt_s, sub_n)
            due = k_grid - k_last
            if due > max_ticks:
                dropped = _r((due - max_ticks) * dt_c_us / 1000.0, 3)
                k_last = k_grid - max_ticks
                due = max_ticks
            for _ in range(due):
                k_last += 1
                tt = K.tick_time_us(k_last, dt_s, sub_n)
                tick_rows.append({"rec": "tick", "t_us": tt, "tick": K.tick_of(tt, dt_s), "sub": k_last, "frame": frame,
                                  "tsrc": "est", "tick_ms": _r(rng.around(0.55, 0.1)), "dt_us": int(round(dt_c_us))})
            ticks = due
        else:
            ticks = 1 if i > 0 else 0
        ctr["sim.dropped_ms"] = _r(ctr["sim.dropped_ms"] + dropped, 3)
        if spike:
            ctr["timer.reentry"] += 1
        out.extend(tick_rows)
        fence = {"F0": _r(rng.around(0.06, 0.02)), "F1": _r(0.1 + ticks * 0.05), "F1a": _r(rng.around(0.2, 0.05)),
                 "F2": _r(ticks * rng.around(0.5, 0.1)), "F3a": _r(rng.around(0.4, 0.1)), "F3b": _r(rng.around(0.3, 0.05)),
                 "F4": _r(rng.around(0.6, 0.1)), "F5": _r(rng.around(1.5, 0.3)), "F6": _r(rng.around(0.3, 0.05)),
                 "F7": _r(rng.around(0.1, 0.02))}
        fr: dict[str, Any] = {
            "rec": "frame", **K.make(t, dt_s_us=dt_s, sub_n=sub_n, frame=frame, fixed=sim == "fixed", tsrc="est"),
            "frame_ms": frame_ms, "work_ms": _r(min(frame_ms * 0.9, sum(fence.values()) + 0.5)), "ticks": ticks,
            "dropped_ms": dropped, "fence_ms": fence, "ctr": dict(ctr),
            "gauge": {"visible_entities": 200 + (i * 7) % 60, "lod_list": 80 + (i * 3) % 20}}
        if sim == "fixed":
            fr["sub"] = k_last
        if i % 50 == 25:
            fr["gpu_ms"] = {"scene": _r(rng.around(3.0, 0.5)), "post": _r(rng.around(0.8, 0.2))}
        out.append(fr)
        window.append(frame_ms)
        window = window[-1024:]
        if t >= next_stats:
            sec = (t - start_us) / 1e6
            fs = frame_stats(window)
            out.append({
                "rec": "stats", **K.make(t, dt_s_us=dt_s, sub_n=sub_n, frame=frame, fixed=sim == "fixed", tsrc="est"),
                "frames": {k: fs[k] for k in ("n", "avg_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms", "fps_avg", "low1_fps")},
                "game": {"time_ms": _r(sec * 1000.0, 1), "timer_mode": "real", "frame_counter": int(sec * 30),
                         "frame_counter_hz": 30.0, "clock_ratio": 1.0},
                "mem": {"va_used_mib": _r(1500 + 0.5 * sec, 1), "va_largest_free_mib": 900.0, "private_mib": 900.0,
                        "working_set_mib": 600.0, "va_guard": "ok", "cef_loaded": False},
                "stream": {"used_mib": _r(200 + 0.1 * sec, 1), "budget_mib": 256.0},
                "va_owner_mib": {"streaming": _r(200 + 0.1 * sec, 1), "textures": 410.0}})
            if sim == "fixed":
                out[-1]["sub"] = k_last
            next_stats += 1_000_000
        frame += 1
    if sim == "fixed" and out[-1]["rec"] == "frame":
        out[-1]["sub"] = k_last
    if n_frames > 97:
        # a clock re-base event after the first spike, in key order (it carries the key of that frame)
        idx = next(j for j, r in enumerate(out) if r["rec"] == "frame" and r["frame"] == 100 + 97)
        out.insert(idx + 1, {"rec": "event", **K.of(out[idx]), "name": "sae.clock.rebase", "level": "info", "data": {"reason": "stall", "ms": 83.3}})
    return with_end(out, clean=True) if end else out


def server_stream(*, node: str = "server", seconds: float = 3.0, tick_hz: int = 30, seed: int = 2, start_us: int = START_US - 400_000,
                  end: bool = True) -> list[dict[str, Any]]:
    """A server stream: one tick record per server tick, no frames."""
    rng = _Rng(seed)
    dt_s = 1_000_000 // tick_hz
    out: list[dict[str, Any]] = [{
        "rec": "hdr", "schema": "sae-obs/1", "minor": 0, "role": "server", "node": node, "session": SESSION,
        "clock": {"dt_s_us": dt_s, "domain": "server"}, "engine": {"build": "sae-sample", "arch": "x64"},
        "producer": {"name": "satk-obs-sample", "version": "1"}}]
    first = -(-start_us // dt_s)
    for T in range(first, first + int(seconds * tick_hz)):
        t = T * dt_s
        out.append({"rec": "tick", "t_us": t, "tick": T, "sub": K.NONE, "frame": K.NONE, "tsrc": "server",
                    "tick_ms": _r(rng.around(1.2, 0.3)), "dt_us": dt_s, "ctr": {"net.tx_packets": (T - first + 1) * 12}})
    return with_end(out) if end else out


def net_stream(*, node: str = "client-1", rows: int = 40, tick_hz: int = 30, seed: int = 3, start_us: int = START_US,
               end: bool = True) -> list[dict[str, Any]]:
    """A transport-trace stream (``.saenet``): net rows of a client talking to a server."""
    rng = _Rng(seed)
    dt_s = 1_000_000 // tick_hz
    out: list[dict[str, Any]] = [{
        "rec": "hdr", "schema": "sae-obs/1", "minor": 0, "role": "client", "node": node, "session": SESSION,
        "clock": {"dt_s_us": dt_s, "domain": "server"}, "pulse": "standard", "producer": {"name": "satk-obs-sample", "version": "1"}}]
    t = start_us
    frame = 100
    for i in range(rows):
        t += int(rng.around(8000, 3000))
        if i % 2 == 0:
            frame += 1
        tx = i % 3 != 0
        out.append({"rec": "net", **K.make(t, dt_s_us=dt_s, frame=frame, tsrc="est"), "dir": "tx" if tx else "rx", "peer": "server",
                    "element_id": 17 + i % 4 if i % 5 else K.NONE, "packet_id": 152 if i % 4 == 0 else 6,
                    **({"subtype": i % 3} if i % 4 == 0 else {}), "bytes": 24 + int(rng.next() * 90), "lane": 0, "seq": i})
    return with_end(out) if end else out


def write_samples(directory: Path) -> dict[str, Path]:
    """Write the example files into ``directory``: JSONL streams, a merged stream, one container of each kind and the bench
    document made from the client stream. Returns ``{name: path}``."""
    from .bench import doc_from_records
    from .stream import merge_streams

    directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    fixed = client_stream()
    classic = client_stream(node="client-2", sim="classic", seed=5)
    server = server_stream()
    for name, recs in (("client-fixed.jsonl", fixed), ("client-classic.jsonl", classic), ("server.jsonl", server)):
        p = directory / name
        write_jsonl(p, recs)
        files[name] = p
    merged = list(merge_streams([("client-fixed", fixed), ("server", server)]))
    p = directory / "merged.jsonl"
    write_jsonl(p, merged)
    files["merged.jsonl"] = p
    p = directory / "trace.saenet"
    pack_records(net_stream(), p, chunk_records=16)
    files["trace.saenet"] = p
    p = directory / "session.saerec"
    pack_records(fixed, p, chunk_records=64)
    files["session.saerec"] = p
    import json
    p = directory / "client-fixed.bench.json"
    p.write_text(json.dumps(doc_from_records(fixed, run="sample", scene="S0", label="fixed"), indent=1) + "\n", encoding="utf-8", newline="\n")
    files["client-fixed.bench.json"] = p
    return files
