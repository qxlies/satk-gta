"""Blender jobs of ``satk.anim`` (MIT side, no ``bpy``): run ``blender/satk_blender/anim/job.py`` headless.

Uses the public runner of :mod:`satk.blender.runner` read-only (DragonFF copy, isolated Blender profile, job
folders under ``<work>/blender/jobs``); the request/response are this package's own (``anim/1``), not the
``satk-blender/1`` contract. :func:`compare` measures how far exported key frames are from the source ones.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import atomic_write, jpath

__all__ = ["job_script", "run_anim_job", "compare"]

DEFAULT_TIMEOUT = 600.0


def job_script() -> Path:
    from ..blender.runner import addon_dir

    return addon_dir() / "anim" / "job.py"


def run_anim_job(cmd: str, args: dict, *, blend: str | Path | None = None, profile: str = "vanilla",
                 files: dict[str, bytes] | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Run one ``job.py`` command in a fresh headless Blender; returns the response (``job``, ``dir`` added).

    ``files`` are written into the job folder first; each one's path goes to ``args`` as ``<name>_path`` with
    dots turned into underscores (``{"source.ifp": data}`` -> ``args["source_ifp_path"]``).
    """
    from ..blender import runner

    if not math.isfinite(timeout) or timeout <= 0:
        raise SatkError("BAD_PARAMS", "Blender timeout must be a positive, finite number of seconds")

    runner.blender_exe()                                  # NOT_READY early when Blender is missing
    runner.ensure_dragonff()
    jid, jdir = runner.new_job(f"anim_{cmd}")
    jdir.mkdir(parents=True, exist_ok=True)
    args = dict(args)
    for name, data in (files or {}).items():
        p = jdir / name
        atomic_write(p, data)
        args[name.replace(".", "_") + "_path"] = str(p)
    req = {"contract": "anim/1", "cmd": cmd, "args": args, "profile": profile, "job": jid,
           "out_dir": jpath(jdir), "satk_src": jpath(runner.satk_src()), "dragonff": jpath(runner.dragonff_root())}
    req_path = jdir / "request.json"
    atomic_write(req_path, json.dumps(req, ensure_ascii=False, indent=1) + "\n")
    log = jdir / "blender.log"
    argv = ["-b"]
    if blend is not None:
        argv.append(str(blend))
    argv += ["--factory-startup", "--python-exit-code", "1", "--python", str(job_script()), "--", str(req_path)]
    code, secs = runner.run_blender(argv, log=log, timeout=timeout, env=runner.blender_env(jdir))
    try:
        resp = json.loads((jdir / "response.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SatkError("EXTERNAL_TOOL", f"Blender exited with code {code} and wrote no response",
                        hint=f"see {jpath(log)}",
                        data={"log": jpath(log), "tail": runner.log_tail(log, 15), "job": jid}) from None
    if not isinstance(resp, dict) or not isinstance(resp.get("stats", {}), dict):
        raise SatkError("EXTERNAL_TOOL", "Blender wrote an invalid response", hint=f"see {jpath(log)}",
                        data={"job": jid})
    resp["job"] = jid
    resp["dir"] = jpath(jdir)
    resp.setdefault("stats", {})["process_s"] = round(secs, 2)
    if not resp.get("ok"):
        err = resp.get("error") or {}
        data = dict(err.get("data") or {})
        data.update({"log": jpath(log), "job": jid})
        if err.get("code") in (None, "EXTERNAL_TOOL"):
            data.setdefault("tail", runner.log_tail(log, 12))
        raise SatkError(err.get("code") or "EXTERNAL_TOOL", err.get("msg") or f"Blender job failed (exit {code})",
                        hint=err.get("hint") or f"see {jpath(log)}", data=data)
    if code != 0:
        raise SatkError("EXTERNAL_TOOL", f"Blender reported success but exited with code {code}",
                        hint=f"see {jpath(log)}", data={"log": jpath(log), "job": jid})
    return resp


def _qdist(a: tuple, b: tuple) -> float:
    """Angle in degrees between two rotations (sign-independent)."""
    n = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b)) or 1.0
    d = min(1.0, abs(sum(x * y for x, y in zip(a, b))) / n)
    return math.degrees(2.0 * math.acos(d))


def compare(src_anim, out_anim) -> dict:
    """Largest differences between two :class:`~satk.anim.ifp.Anim`: rotation (degrees), translation (m),
    time (s); ``exact`` = the same key frames as stored (ints for compressed). Sequences pair up in order.
    ``within_tolerance`` uses unrounded errors (0.05 degrees, 0.001 m, 0.001 s) and requires matching structure
    and finite, nonzero rotations; missing sequences must never pass as zero numerical error."""
    from .ifp import quantize_seq

    worst = {"rot_deg": 0.0, "trans_m": 0.0, "time_s": 0.0}
    exact = len(src_anim.seqs) == len(out_anim.seqs)
    keys = 0
    problems: list[str] = []
    if src_anim.name != out_anim.name:
        problems.append(f"animation {src_anim.name!r} -> {out_anim.name!r}")
    if len(src_anim.seqs) != len(out_anim.seqs):
        problems.append(f"sequence count {len(src_anim.seqs)} -> {len(out_anim.seqs)}")
    for s, o in zip(src_anim.seqs, out_anim.seqs):
        if (s.name, s.tag, s.trans, s.scale) != (o.name, o.tag, o.trans, o.scale) or len(s.keys) != len(o.keys):
            problems.append(f"{s.name}: {len(s.keys)} keys, tag {s.tag} -> {o.name}: {len(o.keys)} keys, tag {o.tag}")
            exact = False
            continue
        for i, ((t0, q0, r0), (t1, q1, r1)) in enumerate(zip(s.frames(), o.frames())):
            keys += 1
            scale0 = s.keys[i][8:] if s.scale else ()
            scale1 = o.keys[i][8:] if o.scale else ()
            if not all(math.isfinite(v) for v in (t0, t1, *q0, *q1, *(r0 or ()), *(r1 or ()), *scale0, *scale1)):
                problems.append(f"{s.name}: key {i} contains a non-finite value")
                continue
            if scale0 != scale1:
                problems.append(f"{s.name}: key {i} scale changed")
            if min(sum(v * v for v in q0), sum(v * v for v in q1)) < 1e-16:
                problems.append(f"{s.name}: key {i} has a zero rotation quaternion")
                continue
            worst["time_s"] = max(worst["time_s"], abs(t0 - t1))
            worst["rot_deg"] = max(worst["rot_deg"], _qdist(q0, q1))
            if r0 is not None and r1 is not None:
                worst["trans_m"] = max(worst["trans_m"], max(abs(a - b) for a, b in zip(r0, r1)))
        if s.compressed:
            try:
                exact = exact and (quantize_seq(o).keys if not o.compressed else o.keys) == s.keys
            except ValueError:
                exact = False
        else:
            exact = exact and o.keys == s.keys
    exact = exact and not problems
    close = not problems and (worst["rot_deg"] <= 0.05 and worst["trans_m"] <= 0.001
                             and worst["time_s"] <= 0.001)
    out = {"keys": keys, "rot_deg": round(worst["rot_deg"], 4), "trans_m": round(worst["trans_m"], 5),
           "time_s": round(worst["time_s"], 5), "exact": exact, "within_tolerance": close}
    if problems:
        out["problems"] = problems[:5]
    return out
