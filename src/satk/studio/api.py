"""MIT client of the studio (contract K4): one call = one Blender method.

``call(method, params)`` uses the running session (``session``, default ``"default"``) over SAAP
(``author.call``, a few ms); with no session running and ``session`` not given it falls back to a
one-shot cold run of the same world: a fresh headless Blender (``blend`` opened, ``save`` written),
2-5 s. An explicitly named session that is not running is ``NOT_READY``.

``timeout`` is the step's time budget: the session stops a method that overruns it and answers
``TIMEOUT`` (the session stays usable); the socket waits :data:`ANSWER_GRACE_S` longer, so the answer
comes from Blender. A session that died during the call is reported as ``EXTERNAL_TOOL`` with its crash
report, never as a bare connection error.

Example::

    from satk.studio import api
    api.call("mesh.primitive", {"kind": "cylinder", "segments": 12, "radius": 0.3, "depth": 1.0, "name": "post"},
             snapshot="3q")
    # {"method": "mesh.primitive", "n": 3, "result": {"object": "post"}, "changed": ["post"],
    #  "stats": {"objects": {"post": {"tris": 44, "verts": 24, "dims": [0.6, 0.6, 1.0]}}, "scene": {...}},
    #  "snapshot": "<workspace>/work/studio/default/snaps/0003-3q.jpg", "ms": 21.4, "session": "default"}
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.errors import ERROR_CODES, SatkError
from ..saap import client as C
from . import launcher as L

__all__ = ["DEFAULT_TIMEOUT", "PATH_KEYS", "PREPARE", "call", "methods", "cold", "close_all", "prepare", "snapshot_spec", "SHEET_VIEWS"]

DEFAULT_TIMEOUT = 120.0
#: The socket waits this much longer than the step's budget (the session answers TIMEOUT itself).
ANSWER_GRACE_S = 10.0

#: A cached connection idle for longer is replaced (the endpoint drops silent connections after 600 s).
REUSE_S = 300.0

_lock = threading.Lock()
_clients: dict[str, list] = {}  # name -> [endpoint key, SaapClient, last use (monotonic)]


def close_all() -> None:
    """Close the cached connections (tests, shutdown)."""
    with _lock:
        for _, c, _t in _clients.values():
            c.close()
        _clients.clear()


def _lift(e: SatkError, **extra: Any) -> SatkError:
    """A SAAP error as a satk error: the original code, hint and did_you_mean out of ``data``."""
    data = dict(e.data or {})
    data.pop("meta", None)
    code = data.pop("satk_code", None)
    code = code if code in ERROR_CODES else e.code
    hint = data.pop("hint", None) or e.hint
    near = data.pop("did_you_mean", None) or e.did_you_mean
    data.update({k: v for k, v in extra.items() if v is not None})
    return SatkError(code, e.msg, hint=hint, did_you_mean=near or (), data=data or None)


def _client(name: str, timeout: float) -> C.SaapClient | None:
    """A connected client of session ``name`` (cached per endpoint), ``None`` if it is not running."""
    r = L.role(name)
    ep, sess = C.read_endpoint(r), C.read_session(r)
    if ep is None or sess is None:
        return None
    key = (ep.get("pid"), ep.get("port"), sess.get("token"))
    now = time.monotonic()
    with _lock:
        cached = _clients.get(name)
        if cached is not None:
            if cached[0] == key and cached[1].sock is not None and now - cached[2] < REUSE_S:
                cached[1].timeout = timeout
                cached[2] = now
                return cached[1]
            cached[1].close()
            del _clients[name]
    if C.verify_pid(ep, sess)[0] not in ("ok", "unknown"):
        return None
    c = C.connect(r, timeout=timeout, name="satk-studio")
    with _lock:
        _clients[name] = [key, c, now]
    return c


def _died(name: str, what: str, e: SatkError) -> SatkError | None:
    """``EXTERNAL_TOOL`` with the crash report when the session's Blender is gone, else ``None``."""
    r = L.role(name)
    ep, sess = C.read_endpoint(r), C.read_session(r)
    pid = (ep or {}).get("pid") or (sess or {}).get("pid")
    if not pid or C.pid_alive(int(pid)):
        return None
    d = L.session_dir(name)
    data: dict[str, Any] = {"session": name, "pid": pid}
    crash = d / "tmp" / "blender.crash.txt"
    if crash.is_file():
        data["crash_report"] = paths.jpath(crash)
    log = (sess or {}).get("log")
    if log:
        data["log"] = log
    proj = (sess or {}).get("project")
    again = f"satk blender session start --project {Path(proj).name}" if proj else         f"satk blender session start --name {name}"
    return SatkError("EXTERNAL_TOOL", f"the Blender session {name!r} died during {what} ({e.code}: {e.msg})"[:300],
                     hint=f"{again} (a project session resumes from its newest checkpoint); the crash report "
                          "names the Blender function", data=data)


def _warm(name: str, method: str, p: dict, timeout: float) -> dict | None:
    """The result over the running session; ``None`` when there is none (or it just went away)."""
    try:
        c = _client(name, timeout)
    except SatkError as e:
        if e.code in ("NOT_READY", "AUTH"):
            return None
        raise
    if c is None:
        return None
    try:
        return c.call(method, p, timeout=timeout)
    except SatkError as e:
        if e.code in ("NOT_READY", "PROTOCOL", "TIMEOUT") and c.sock is None:
            with _lock:
                _clients.pop(name, None)
        if e.code in ("PROTOCOL", "TIMEOUT"):
            dead = _died(name, str(p.get("method") or method), e)
            if dead is not None:
                raise dead from None
        if e.code == "TIMEOUT" and not (e.data or {}).get("satk_code") and "no response within" in e.msg:
            raise SatkError("TIMEOUT", f"session {name!r}: {e.msg}",
                            hint=f"satk blender session status --name {name} shows what it is doing; "
                                 f"satk blender session stop --name {name} ends a stuck session",
                            data={"session": name}) from None
        raise _lift(e, session=name) from None


def cold(saap_method: str, p: dict, *, blend: str | os.PathLike | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """One request in a fresh headless Blender (the same world as a session)."""
    from ..blender import runner

    jid, jdir = runner.new_job("studio")
    req = L.request("oneshot", jdir, call=p, saap_method=saap_method)
    req_path = paths.atomic_write(jdir / "request.json", json.dumps(req, ensure_ascii=False, indent=1))
    log = jdir / "blender.log"
    code, secs = runner.run_blender(L.argv(req_path, blend=blend), log=log, timeout=timeout,
                                    env=runner.blender_env(jdir))
    try:
        resp = json.loads((jdir / "response.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SatkError("EXTERNAL_TOOL", f"Blender exited with code {code} and wrote no response",
                        hint=f"see {paths.jpath(log)}",
                        data={"log": paths.jpath(log), "tail": runner.log_tail(log, 15), "job": jid}) from None
    if not resp.get("ok"):
        err = resp.get("error") or {}
        e = SatkError(str(err.get("code") or "INTERNAL"), str(err.get("message") or "the Blender call failed"),
                      data=err.get("data") or None)
        raise _lift(e, job=jid, log=paths.jpath(log))
    res = dict(resp.get("result") or {})
    res.update(mode="cold", job=jid, process_s=round(secs, 2))
    return res


#: Views of ``snapshot="sheet"`` (a 2x2 sheet).
SHEET_VIEWS = ["3q", "front", "left", "top"]


def snapshot_spec(snapshot: Any, size: int | None) -> dict | None:
    """``snapshot`` as an ``author.call`` spec: a view or camera name, ``sheet``, a JSON object (string) or dict."""
    if snapshot is None or snapshot is False:
        return None
    if snapshot is True:
        spec: dict = {}
    elif isinstance(snapshot, str):
        s = snapshot.strip()
        if s.startswith("{"):
            try:
                spec = json.loads(s)
            except ValueError as e:
                raise SatkError("BAD_PARAMS", f"snapshot: invalid JSON: {e}") from None
            if not isinstance(spec, dict):
                raise SatkError("BAD_PARAMS", "snapshot JSON must be an object")
        elif s == "sheet":
            spec = {"views": list(SHEET_VIEWS)}
        else:
            spec = {"view": s}
    elif isinstance(snapshot, dict):
        spec = dict(snapshot)
    else:
        raise SatkError("BAD_PARAMS", "snapshot must be a view name, 'sheet', an object or true")
    if size:
        spec.setdefault("size", int(size))
    return spec


def _check_snapshot(res: dict) -> None:
    """A returned snapshot path must lie inside the work directory (like capture paths)."""
    if res.get("snapshot"):
        C.check_paths({"files": [res["snapshot"]]}, "author.call")


#: Parameters holding file paths: made absolute here (Blender runs in another working directory).
PATH_KEYS = ("image", "texture", "dff", "txd")


def _abs_paths(params: dict) -> dict:
    out = dict(params)
    for k in PATH_KEYS:
        v = out.get(k)
        if isinstance(v, str) and v and not os.path.isabs(v):
            out[k] = os.path.abspath(v)
        elif isinstance(v, list):
            out[k] = [os.path.abspath(x) if isinstance(x, str) and x and not os.path.isabs(x) else x for x in v]
    return out


def _resolve_sid(params: dict) -> dict:
    """``sid`` -> ``plan`` (cached DFF/TXD chain of the profile) for the io methods: Blender has no index."""
    if not params.get("sid") or params.get("plan"):
        return params
    from ..blender.resolve import plan_model

    out = dict(params)
    sid = out.pop("sid")
    prof = str(out.pop("profile", "vanilla") or "vanilla")
    pl = plan_model(sid, profile=prof)
    out["plan"] = {"model": pl["model"], "profile": prof}
    return out


#: Client-side preparation of method parameters (things only the satk side can do).
PREPARE = {"io.ghost": _resolve_sid, "io.import": _resolve_sid}


def prepare(method: str, params: dict | None) -> dict | None:
    """Parameters as Blender needs them: absolute paths, SIDs resolved; batches step by step."""
    if not params:
        return params
    if method == "batch" and isinstance(params.get("steps"), list):
        steps = []
        for st in params["steps"]:
            if isinstance(st, dict) and isinstance(st.get("method"), str) and isinstance(st.get("params"), dict):
                st = dict(st, params=prepare(st["method"], st["params"]))
            steps.append(st)
        return dict(params, steps=steps)
    out = _abs_paths(params)
    fn = PREPARE.get(method)
    return fn(out) if fn is not None else out


#: Plug-in methods whose results the project manifest keeps (K7: ``last_export`` / ``last_check``).
RECORD = {"kit.export": "export", "kit.check": "check", "asset.check": "check"}


def _record(session: str, method: str, res: dict) -> None:
    """Keep the result of an export or check run in a project session in ``asset.json`` (best effort)."""
    key = RECORD.get(method)
    if key is None or not isinstance(res.get("result"), dict):
        return
    proj = (C.read_session(L.role(session)) or {}).get("project")
    if not proj:
        return
    from . import project as P

    summary = {k: v for k, v in res["result"].items() if isinstance(v, (str, int, float, bool))}
    summary["n"] = res.get("n")
    try:
        P.record(proj, {key: summary})
    except SatkError:
        pass


def call(method: str, params: dict | None = None, *, session: str | None = None,
         blend: str | os.PathLike | None = None, save: str | os.PathLike | None = None, snapshot: Any = None,
         size: int | None = None, stats: str | None = None, checkpoint: bool | None = None,
         timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Run studio ``method`` with ``params``; returns the ``author.call`` result (+ ``session`` or ``mode: cold``).

    Args:
        method: studio method (``satk blender methods`` lists them).
        params: the method's parameters.
        session: session name; ``None`` = ``"default"`` when running, else a cold one-shot run.
        blend: ``.blend`` to open first (cold: Blender opens it; warm: the session loads it).
        save: ``.blend`` to save the scene to after the step (under the work directory).
        snapshot: view name (``3q front rear left right top`` or a camera), ``sheet`` or
            ``{"view"|"views", "size", "look", "wire", "ghost", "refs", "ref_alpha"}``.
        size: snapshot size in pixels (64-1024, default 512).
        stats: ``auto`` (changed objects after a mutating step), ``none`` or ``scene``.
        checkpoint: ``True`` saves a ``.blend`` checkpoint after the step, ``False`` never; ``None`` = the
            session's rule (every 5th mutating step, ``python`` steps, and once after a multi-step batch).
        timeout: the step's time budget in seconds (the session stops a method that overruns it).
    """
    if not isinstance(method, str) or not method:
        raise SatkError("BAD_PARAMS", "method is required")
    p: dict[str, Any] = {"method": method}
    if params:
        if not isinstance(params, dict):
            raise SatkError("BAD_PARAMS", "params must be an object")
        p["params"] = prepare(method, params)
    if stats and stats != "auto":
        p["stats"] = stats
    spec = snapshot_spec(snapshot, size)
    if spec is not None:
        p["snapshot"] = spec
    if checkpoint is not None:
        p["checkpoint"] = bool(checkpoint)
    if save:
        sp = Path(save).absolute()
        if sp.suffix.lower() != ".blend":
            raise SatkError("BAD_PARAMS", f"save: {paths.jpath(sp)} must end with .blend")
        p["save"] = str(paths.ensure_writable(sp))
    if blend:
        bp = Path(blend).absolute()
        if bp.suffix.lower() != ".blend" or not bp.is_file():
            raise SatkError("NOT_FOUND", f"no .blend file {paths.jpath(bp)}")
        blend = str(bp)
    if not 1 <= float(timeout) <= 7200:
        raise SatkError("BAD_PARAMS", f"timeout must be 1..7200 s, got {timeout:g}")
    name = L.check_name(session)
    warm_p = dict(p, open=blend) if blend else p
    warm_p["timeout"] = float(timeout)
    res = _warm(name, "author.call", warm_p, float(timeout) + ANSWER_GRACE_S)
    if res is not None:
        _check_snapshot(res)
        res["session"] = name
        _record(name, method, res)
        return res
    if session is not None:
        raise SatkError("NOT_READY", f"no Blender session {name!r} is running",
                        hint=f"satk blender session start --name {name}")
    res = cold("author.call", p, blend=blend, timeout=timeout)
    _check_snapshot(res)
    return res


def methods(query: str | None = None, *, session: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """``{"methods": [{name, doc, readonly?}], "total", "errors"?}`` of the session (or a cold run)."""
    p: dict[str, Any] = {}
    if query:
        p["query"] = query
    name = L.check_name(session)
    res = _warm(name, "author.methods", p, timeout)
    if res is not None:
        res["session"] = name
        return res
    if session is not None:
        raise SatkError("NOT_READY", f"no Blender session {name!r} is running",
                        hint=f"satk blender session start --name {name}")
    return cold("author.methods", p, timeout=timeout)
