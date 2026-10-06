"""Replay a studio journal into a session: rebuild a scene from its steps (``blender.session replay``).

The journal (``journal.jsonl`` of a session or project) lists every step with its parameters; ``python``
steps carry their code. :func:`plan_actions` turns it into actions:

* ``call`` - a mutating method with its parameters (read-only and failed steps are skipped);
* ``open`` - load a ``.blend``: a step that opened a file, a ``session.restore``, an ``external`` (GUI)
  edit, or a session start that resumed from a checkpoint the replay has not reached;
* ``clear`` - a session that started on an empty scene in the middle of the journal.

:func:`run` sends them to a session in batches (one ``author.call`` per batch, so stats and checkpoints
come once per batch) and measures the result with ``scene.stats``. ``start`` (a checkpoint step or tag)
begins from that checkpoint instead of step 0.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.errors import SatkError
from .core import MAX_STEPS, Checkpoints, Journal

__all__ = ["SKIP", "plan_actions", "run"]

#: Journal methods that never replay (bookkeeping).
SKIP = frozenset({"session.end", "session.checkpoint", "session.checkpoints", "session.prune", "session.journal"})
BATCH = 50


def plan_actions(entries: list[dict], *, after: int = 0, upto: int | None = None) -> list[dict]:
    """Actions for the journal steps ``after < n <= upto`` (see the module doc)."""
    acts: list[dict] = []
    applied = after  # the last step whose effect the replayed scene holds
    first_start = True
    for e in entries:
        n = e.get("n")
        if not isinstance(n, int) or n <= after or (upto is not None and n > upto):
            if e.get("method") == "session.start" and isinstance(n, int) and n <= after:
                first_start = False
            continue
        m = e.get("method")
        if m == "session.start":
            if e.get("open") and e.get("resume_n") is not None:
                if applied < int(e["resume_n"]):
                    acts.append({"op": "open", "path": e["open"], "n": n, "why": "resumed session"})
                    applied = int(e["resume_n"])
            elif e.get("open"):
                acts.append({"op": "open", "path": e["open"], "n": n, "why": "session opened a file"})
            elif not first_start or applied > after:
                acts.append({"op": "clear", "n": n, "why": "new session on an empty scene"})
            first_start = False
            continue
        if m in SKIP:
            continue
        if e.get("open"):  # the file was opened before the method ran, even a read-only or failed one
            acts.append({"op": "open", "path": e["open"], "n": n, "why": "the step opened a file"})
            applied = n
        if e.get("ro") or e.get("ok") is False:
            applied = n  # nothing changed: the replayed scene is the journal's scene at this step
            continue
        if m == "session.restore":
            if not e.get("restore_path"):
                raise SatkError("NOT_FOUND", f"step {n}: session.restore without a checkpoint path")
            acts.append({"op": "open", "path": e["restore_path"], "n": n, "why": f"restore {e.get('restore')}"})
            applied = n
            continue
        if m == "external":
            if not e.get("checkpoint"):
                raise SatkError("NOT_FOUND", f"step {n}: a GUI edit without a checkpoint cannot be replayed")
            acts.append({"op": "open", "path": e["checkpoint"], "n": n, "why": "edit in the Blender window"})
            applied = n
            continue
        acts.append({"op": "call", "method": m, "params": e.get("params") or {}, "n": n})
        applied = n
    return acts


def _journal_path(src: str) -> Path:
    p = Path(os.path.abspath(src))
    if p.is_dir():
        p = p / "journal.jsonl"
    if p.is_file():
        return p
    try:
        from .project import project_dir

        q = project_dir(src) / "journal.jsonl"
        if q.is_file():
            return q
    except SatkError:
        pass
    from . import launcher as L

    try:
        q = L.session_dir(src) / "journal.jsonl"
        if q.is_file():
            return q
    except SatkError:
        pass
    raise SatkError("NOT_FOUND", f"no journal at {src!r}", hint="give a journal.jsonl, a project name or a session name")


def run(source: str, *, session: str, start: Any = None, upto: int | None = None, objects: str = "*",
        timeout: float = 600.0) -> dict:
    """Replay ``source`` (journal file, project or session name) into the running ``session``."""
    from . import api

    jp = _journal_path(source)
    entries = Journal(jp).entries()
    if not entries:
        raise SatkError("NOT_FOUND", f"{paths.jpath(jp)} is empty")
    after = 0
    pre: list[dict] = []
    if start is not None:
        hit = Checkpoints(jp.parent / "checkpoints", "blend", lambda _p: None, lambda _p: None).find(start)
        pre.append({"op": "open", "path": hit["path"], "n": hit["n"], "why": f"start at checkpoint {hit['n']}"})
        after = int(hit["n"])
    acts = plan_actions(entries, after=after, upto=upto)
    if not acts:
        raise SatkError("NOT_FOUND", "nothing to replay in that range")
    if not pre and acts[0]["op"] != "open":  # from step 0: the scene starts empty, as the journal's did
        pre.append({"op": "clear", "n": 0, "why": "replay starts on an empty scene"})
    acts = pre + acts
    sent = calls = opens = 0
    warn: list[str] = []
    pending: list[dict] = []

    def flush() -> None:
        nonlocal sent, calls
        if not pending:
            return
        steps = [{"method": a["method"], "params": a["params"]} for a in pending]
        for i in range(0, len(steps), min(BATCH, MAX_STEPS)):
            chunk = steps[i:i + BATCH]
            try:
                res = api.call("batch", {"steps": chunk}, session=session, stats="none", timeout=timeout)
            except SatkError as e:
                step = (e.data or {}).get("step")
                n = pending[i + step - 1]["n"] if isinstance(step, int) and 0 < step <= len(chunk) else None
                raise SatkError(e.code, f"replay stopped at journal step {n}: {e.msg}", hint=e.hint,
                                data={"journal_step": n, **(e.data or {})}) from None
            warn.extend(res.get("warn") or [])
            calls += 1
        sent += len(steps)
        pending.clear()

    for a in acts:
        if a["op"] == "call":
            pending.append(a)
            continue
        flush()
        if a["op"] == "open":
            if not os.path.isfile(a["path"]):
                raise SatkError("NOT_FOUND", f"journal step {a['n']}: {a['path']} is gone ({a['why']})",
                                hint="pruned checkpoints cannot be replayed; start from a later one")
            api.call("scene.info", {"limit": 0}, session=session, blend=a["path"], stats="none", timeout=timeout)
        else:
            api.call("scene.clear", {}, session=session, stats="none", timeout=timeout)
        opens += 1
    flush()
    meas = api.call("scene.stats", {"objects": objects, "precise": True, "file": True}, session=session,
                    stats="none", timeout=timeout)
    res = meas.get("result") or {}
    out: dict = {"journal": paths.jpath(jp), "session": session, "steps": sent, "batches": calls, "loads": opens,
                 "scene": res.get("scene"), "stats_file": res.get("file")}
    if after:
        out["from"] = after
    if warn:
        out["warn"] = warn[:10]
    return out
