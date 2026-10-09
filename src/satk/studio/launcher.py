"""Named Blender studio sessions: start, status, list, stop (one headless Blender process per name).

A session ``<name>`` is a Blender process running ``blender/satk_blender/studio/main.py`` in ``serve`` mode:
a SAAP endpoint with role ``blender`` and capabilities ``core`` + ``author``. Files:

* ``work/run/endpoints/blender-<name>.json`` - written by Blender after ``listen()`` (port, pid, exe, caps);
* ``work/run/sessions/blender-<name>.json`` - written here (pid, token, log);
* ``work/studio/<name>/`` - ``request.json``, ``blender.log``, ``journal.jsonl``, ``snaps/``, ``checkpoints/``;
  with ``project`` the journal, snapshots and checkpoints live in the project folder instead
  (``work/assets/<name>/``, see :mod:`satk.studio.project`), and a new session resumes from the
  project's newest checkpoint.

Blender runs with satk's isolated profile (``satk.blender.runner.blender_env``), ``--factory-startup``, no
window (``--gui`` opens one). The session ends on ``stop`` (``quit``), after ``idle`` seconds without
requests, or when ``owner_pid`` exits. ``stop`` terminates only a pid verified as this session's Blender.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.config import REPO_ROOT
from ..core.errors import SatkError
from ..saap import client as C

__all__ = ["NAME_RE", "DEFAULT_NAME", "IDLE_S", "CHECKPOINT_EVERY", "role", "check_name", "main_script", "session_dir",
           "request", "argv", "start", "status", "list_sessions", "stop", "prune"]

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
DEFAULT_NAME = "default"
#: A session without requests for this long (s) ends by itself.
IDLE_S = 3600.0
START_WAIT_S = 90.0
#: A checkpoint after every N-th mutating step of a session (plus gate tags and ``python`` steps).
CHECKPOINT_EVERY = 5
STOP_WAIT_S = 10.0
#: A session stuck in a step past its time budget gets this long (s) to quit before it is terminated.
STUCK_WAIT_S = 2.0
_NO_WINDOW = 0x08000000
_NEW_GROUP = 0x00000200


def check_name(name: str | None) -> str:
    n = (name or DEFAULT_NAME).strip()
    if not NAME_RE.match(n):
        raise SatkError("BAD_PARAMS", f"bad session name {name!r} (a-z, 0-9, '-', '_'; at most 32 chars)")
    return n


def role(name: str) -> str:
    """Discovery-file stem of a session: ``blender-<name>``."""
    return f"blender-{check_name(name)}"


def main_script() -> Path:
    return REPO_ROOT / "blender" / "satk_blender" / "studio" / "main.py"


def session_dir(name: str) -> Path:
    return paths.work("studio", check_name(name))


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def request(mode: str, d: Path, *, name: str | None = None, call: dict | None = None,
            saap_method: str = "author.call", idle: float = IDLE_S, owner_pid: int | None = None,
            project: Path | None = None, project_cfg: dict | None = None, resume_n: int | None = None,
            checkpoint_every: int = CHECKPOINT_EVERY) -> dict:
    """The ``request.json`` of the Blender side (no token: it travels in ``SATK_AGENT_TOKEN``)."""
    from ..blender import runner

    home = Path(project) if project is not None else d
    req: dict[str, Any] = {"studio": 1, "mode": mode, "satk_src": str(runner.satk_src()),
                           "out_root": os.path.abspath(paths.cfg().paths.work), "dir": str(d),
                           "journal": str(home / "journal.jsonl"), "dragonff": str(runner.dragonff_root())}
    if project is not None:
        req.update(project_dir=str(project), snaps=str(project / "snaps"), checkpoints=str(project / "checkpoints"),
                   project=project_cfg or {})
    if mode == "serve":
        r = role(name or DEFAULT_NAME)
        req.update(name=check_name(name), endpoint=str(C.endpoints_dir() / f"{r}.json"),
                   session_file=str(C.sessions_dir() / f"{r}.json"), idle_s=float(idle),
                   checkpoint_every=int(checkpoint_every))
        if owner_pid:
            req["owner_pid"] = int(owner_pid)
        if resume_n is not None:
            req["resume_n"] = int(resume_n)
    else:
        req.update(saap_method=saap_method, call=call or {}, response=str(d / "response.json"))
    return req


def argv(req_path: Path, *, blend: str | os.PathLike | None = None, gui: bool = False,
         threads: int | None = None) -> list[str]:
    """Blender arguments (without the executable)."""
    out = [] if gui else ["-b"]
    if blend:
        out.append(str(Path(blend).absolute()))
    if threads:
        out += ["-t", str(int(threads))]
    return out + ["--factory-startup", "--python-exit-code", "1", "--python", str(main_script()), "--", str(req_path)]


def _threads(threads: int | None) -> int | None:
    if threads is None:
        env = os.environ.get("SATK_BLENDER_THREADS", "").strip()
        threads = int(env) if env.isdigit() else None
    if threads is not None and not 0 <= int(threads) <= 256:
        raise SatkError("BAD_PARAMS", f"threads must be 0-256 (0 = all cores), got {threads}")
    return int(threads) if threads else None


def _discovery(name: str) -> tuple[dict | None, dict | None, int | None, str, str]:
    r = role(name)
    ep, sess = C.read_endpoint(r), C.read_session(r)
    pid = (ep or {}).get("pid") or (sess or {}).get("pid")
    if not pid:
        return ep, sess, None, "dead", "no discovery files"
    state, why = C.verify_pid(ep, sess)
    return ep, sess, int(pid), state, why


def _check_blend(blend: str | os.PathLike | None) -> str | None:
    if not blend:
        return None
    p = Path(blend).absolute()
    if p.suffix.lower() != ".blend" or not p.is_file():
        raise SatkError("NOT_FOUND", f"no .blend file {paths.jpath(p)}")
    return str(p)


# --------------------------------------------------------------------------- start


def start(name: str | None = DEFAULT_NAME, *, blend: str | os.PathLike | None = None, gui: bool = False,
          idle: float = IDLE_S, owner_pid: int | None = None, wait: float = START_WAIT_S,
          project: str | None = None, threads: int | None = None, resume: bool = True,
          checkpoint_every: int = CHECKPOINT_EVERY) -> dict:
    """Start the session ``name`` (or reuse the running one). ``blend`` is opened at start.

    With ``project`` the session journals into the project, takes its target size, and
    (``resume``) opens the project's newest checkpoint when no ``blend`` is given.
    """
    from ..blender import runner

    proj_dir = proj_cfg = None
    resume_n = None
    if project:
        from . import project as P
        from .core import Checkpoints

        proj_dir, pdata = P.load(project)
        proj_cfg = P.session_config(proj_dir, pdata)
        if not name or name == DEFAULT_NAME:
            name = proj_dir.name
        if pdata.get("session") != check_name(name):
            pdata["session"] = check_name(name)
            P.save(proj_dir, pdata)
        if resume and not blend:
            cps = Checkpoints(proj_dir / "checkpoints", "blend", lambda _p: None, lambda _p: None).list()
            if cps:
                blend, resume_n = cps[-1]["path"], cps[-1]["n"]
    name = check_name(name)
    r = role(name)
    blend_s = _check_blend(blend)
    threads = _threads(threads)
    ep, sess, pid, state, why = _discovery(name)
    warn: list[str] = []
    if ep is not None and state in ("ok", "unknown"):
        st = status(name)
        if st.get("up"):
            st["reused"] = True
            if blend_s:
                st["warn"] = ["EXISTS: the session was already running; its scene was kept "
                              "(open a file with: satk blender call scene.info --blend <file>)"]
            return st
        state, why = "mismatch", f"pid {pid} does not answer ({why})"
    if state == "mismatch":
        warn.append(f"NOT_READY: replaced stale discovery files; {why}")
    C.remove_discovery(r)
    exe = runner.blender_exe()
    d = session_dir(name)
    req = request("serve", d, name=name, idle=idle, owner_pid=owner_pid, project=proj_dir, project_cfg=proj_cfg,
                  resume_n=resume_n, checkpoint_every=checkpoint_every)
    req_path = paths.atomic_write(d / "request.json", json.dumps(req, ensure_ascii=False, indent=1))
    env = runner.blender_env(d)
    token = secrets.token_hex(32)
    env["SATK_AGENT_TOKEN"] = token
    args = argv(req_path, blend=blend_s, gui=gui, threads=threads)
    logf = paths.ensure_writable(d / "blender.log")
    flags = 0
    if os.name == "nt":
        flags = _NEW_GROUP | (0 if gui else _NO_WINDOW)
    t0 = time.monotonic()
    with open(logf, "wb") as lf:
        try:
            proc = subprocess.Popen([str(exe), *args], stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=env, cwd=str(d), creationflags=flags, close_fds=True)
        except OSError as e:
            raise SatkError("EXTERNAL_TOOL", f"cannot start Blender: {e}") from None
    sess_info = {"role": "blender", "name": name, "pid": proc.pid, "token": token, "log": paths.jpath(logf),
                 "launched_by": "satk blender session start", "started_at": _now(),
                 "pid_created": C.pid_created(proc.pid), "dir": paths.jpath(d), "gui": bool(gui),
                 "starter_pid": os.getpid()}
    if owner_pid:
        sess_info["owner_pid"] = int(owner_pid)
    if proj_dir is not None:
        sess_info["project"] = paths.jpath(proj_dir)
    C.write_session(r, sess_info)
    while time.monotonic() - t0 < wait:
        ep = C.read_endpoint(r)
        if ep and ep.get("pid") == proc.pid:
            c = C.connect(r, timeout=30)
            try:
                hello = c.hello
            finally:
                c.close()
            out: dict[str, Any] = {"name": name, "up": True, "pid": proc.pid, "port": ep.get("port"),
                                   "caps": hello.get("caps") or ep.get("caps"), "methods": hello.get("methods"),
                                   "blender": hello.get("blender"), "seconds": round(time.monotonic() - t0, 2),
                                   "dir": paths.jpath(d), "log": paths.jpath(logf)}
            if blend_s:
                out["file"] = paths.jpath(blend_s)
            if proj_dir is not None:
                out["project"] = paths.jpath(proj_dir)
                if resume_n is not None:
                    out["resumed_at"] = resume_n
            if threads:
                out["threads"] = threads
            if warn:
                out["warn"] = warn
            return out
        if proc.poll() is not None:
            C.remove_discovery(r, pid=proc.pid)
            raise SatkError("EXTERNAL_TOOL", f"Blender exited with code {proc.returncode} before the session was up",
                            hint=f"see {paths.jpath(logf)}",
                            data={"log": paths.jpath(logf), "tail": runner.log_tail(logf, 12)})
        time.sleep(0.05)
    proc.kill()
    C.remove_discovery(r, pid=proc.pid)
    raise SatkError("TIMEOUT", f"the Blender session did not come up within {wait:g} s",
                    hint=f"see {paths.jpath(logf)}", data={"log": paths.jpath(logf)})


# --------------------------------------------------------------------------- status / list


def status(name: str = DEFAULT_NAME, *, probe: bool = True) -> dict:
    """``{name, up, pid, port, calls, objects, journal, ...}``; stale files give ``stale: true``."""
    name = check_name(name)
    ep, sess, pid, state, why = _discovery(name)
    if ep is None or state in ("dead", "mismatch"):
        out: dict[str, Any] = {"name": name, "up": False}
        if ep is not None or sess is not None:
            out["stale"] = True
            out["note"] = why
            out["hint"] = f"satk blender session start --name {name} (replaces the stale files)"
        else:
            out["hint"] = f"satk blender session start --name {name}"
        return out
    out = {"name": name, "up": True, "pid": pid, "port": ep.get("port"), "caps": ep.get("caps"),
           "started_at": ep.get("started_at"), "dir": ep.get("dir"), "log": (sess or {}).get("log")}
    if probe:
        try:
            c = C.connect(role(name), timeout=10)
            try:
                st = c.call("status", {})
            finally:
                c.close()
            out.update({k: v for k, v in (st.get("author") or {}).items() if k != "dir"})
        except SatkError as e:
            out["up"] = False
            out["note"] = f"pid {pid} is running but does not answer: {e.msg}"
            out["hint"] = f"satk blender session stop --name {name}"
    return out


def list_sessions() -> list[dict]:
    """Every ``blender-*`` endpoint with ``alive``."""
    out = []
    for d in C.list_endpoints():
        r = str(d.get("role") or "")
        stem = d.get("name") or ""
        if d.get("role") == "blender" and stem:
            out.append({"name": stem, "up": bool(d.get("alive")), "pid": d.get("pid"), "port": d.get("port"),
                        "started_at": d.get("started_at")})
        elif r.startswith("blender-"):
            out.append({"name": r[len("blender-"):], "up": bool(d.get("alive")), "pid": d.get("pid"),
                        "port": d.get("port"), "started_at": d.get("started_at")})
    return sorted(out, key=lambda x: x["name"])


# --------------------------------------------------------------------------- stop


def _wait_dead(pid: int, seconds: float) -> bool:
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        if not C.pid_alive(pid):
            return True
        time.sleep(0.1)
    return not C.pid_alive(pid)


def stop(name: str = DEFAULT_NAME) -> dict:
    """Stop the session: ``quit`` -> wait -> terminate (only a pid verified as this session's Blender)."""
    from ..viewer.launcher import terminate

    name = check_name(name)
    r = role(name)
    ep, sess, pid, state, why = _discovery(name)
    if state == "dead":
        C.remove_discovery(r)
        return {"name": name, "up": False, "stopped": False, "note": "not running"}
    if state == "mismatch":
        C.remove_discovery(r)
        return {"name": name, "up": False, "stopped": False, "stale": True,
                "note": f"not running; removed stale discovery files, pid {pid} was not touched. {why}"}
    verified = state == "ok"
    how = "quit"
    wait = STOP_WAIT_S
    try:
        c = C.connect(r, timeout=10)
        try:
            ans = c.call("quit", {})
        finally:
            c.close()
        busy = (ans or {}).get("busy") if isinstance(ans, dict) else None
        if busy and busy.get("over"):  # stuck in a step past its budget: it cannot reach the quit
            wait = STUCK_WAIT_S
    except SatkError as e:
        if not verified:
            C.remove_discovery(r)
            return {"name": name, "pid": pid, "up": False, "stopped": False, "stale": True,
                    "note": f"no answer to quit and {why}: discovery files removed, pid {pid} was not touched. {e.msg}"}
        how = "none"
    out: dict[str, Any] = {"name": name, "pid": pid}
    assert pid is not None
    if not _wait_dead(pid, wait):
        if verified and C.verify_pid(ep, sess)[0] == "ok":
            terminate(pid)
            how = "terminated"
            _wait_dead(pid, 5.0)
        else:
            out["warn"] = [f"NOT_READY: pid {pid} did not exit and is not terminated: {why}"]
    alive = C.verify_pid(ep, sess)[0] in ("ok", "unknown")
    if not alive:
        C.remove_discovery(r)
    out.update({"up": alive, "stopped": not alive, "how": how})
    return out


# --------------------------------------------------------------------------- prune


def prune(name: str | None = None, *, keep: int | None = None) -> dict:
    """Clean up: discovery files of dead sessions; sessions whose owner process is gone (verified pid only);
    with ``name``, old untagged checkpoints of that session or project (``keep`` the last N, default 10)."""
    from .core import KEEP_CHECKPOINTS, Checkpoints

    removed: list[str] = []
    stopped: list[str] = []
    notes: list[str] = []
    for d in C.list_endpoints():
        r = str(d.get("role") or "")
        stem = d.get("name") if d.get("role") == "blender" else (r[len("blender-"):] if r.startswith("blender-") else None)
        if not stem or not NAME_RE.match(str(stem)):
            continue
        ep, sess, pid, state, why = _discovery(stem)
        if state in ("dead", "mismatch"):
            C.remove_discovery(role(stem))
            removed.append(stem)
            continue
        owner = (sess or {}).get("owner_pid")
        if owner and not C.pid_alive(int(owner)) and state == "ok":
            res = stop(stem)
            if res.get("stopped"):
                stopped.append(stem)
            else:
                notes.append(f"{stem}: owner {owner} is gone but the session did not stop")
    out: dict[str, Any] = {"stale_removed": removed, "orphans_stopped": stopped}
    if name:
        folder = None
        try:
            from . import project as P

            folder = P.load(name)[0] / "checkpoints"
        except SatkError:
            folder = session_dir(name) / "checkpoints"
        cp = Checkpoints(folder, "blend", lambda _p: None, lambda _p: None,
                         keep=KEEP_CHECKPOINTS if keep is None else int(keep))
        gone = cp.prune(None if keep is None else int(keep))
        out["checkpoints_removed"] = len(gone)
        out["checkpoints_kept"] = len(cp.list())
        out["folder"] = paths.jpath(folder)
    if notes:
        out["warn"] = notes
    return out
