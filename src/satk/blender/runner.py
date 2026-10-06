"""Headless Blender runner (SPEC §4.11). Owner: WP-10. MIT, no ``bpy``; stdlib only.

* DragonFF comes from ``git -C src/DragonFF archive b3bd7aa`` into ``work/blender/dragonff``
  (the clone in ``src`` is only read; :func:`ensure_dragonff`).
* Blender runs with an isolated profile: ``BLENDER_USER_CONFIG/SCRIPTS/EXTENSIONS/DATAFILES/RESOURCES``
  under ``work/blender/profile``, ``--factory-startup``, ``PYTHONDONTWRITEBYTECODE=1``, TEMP on D:.
  The user's own Blender profile is never touched.
* A job = ``work/blender/jobs/<yyyymmdd-HHMMSS-xxxx>/`` with ``request.json``, ``response.json``,
  ``blender.log`` and the outputs (``scene.blend``, PNG, exports). The result is read from
  ``response.json``, never from Blender's stdout.
* Threads: ``-t N`` from ``SATK_BLENDER_THREADS`` or ``[blender] threads`` of ``satk.toml``
  (:func:`thread_args`; unset or 0 = all cores), so parallel jobs can share a machine.

Call::

    blender.exe -b [scene.blend] --factory-startup --python-exit-code 1 \\
        --python <checkout>/blender/satk_blender/agent_cli.py -- <job>/request.json
"""

from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import subprocess
import tarfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.config import REPO_ROOT
from ..core.errors import SatkError
from ..core.paths import cfg, ensure_writable, jpath, work
from . import contract as C

__all__ = [
    "DRAGONFF_COMMIT", "DRAGONFF_SHA", "blender_exe", "addon_dir", "agent_cli", "satk_src", "dragonff_root",
    "dragonff_dir", "dragonff_info", "ensure_dragonff", "profile_dir", "blender_env", "new_job", "run_job",
    "run_blender", "log_tail", "DEFAULT_TIMEOUT", "blender_threads", "thread_args",
]

#: DragonFF commit used by satk (SPEC §4.11; master is 74 commits ahead of the store build).
DRAGONFF_COMMIT = "b3bd7aa"
DRAGONFF_SHA = "b3bd7aa65979faa125555c794fc483980080dbff"
_MARKER = ".satk-dragonff.json"
DEFAULT_TIMEOUT = 600.0
_CREATE_NO_WINDOW = 0x08000000


# --------------------------------------------------------------------------- locations


def blender_exe() -> Path:
    """``paths.blender`` from the config; ``NOT_READY`` if it does not exist."""
    p = cfg().paths.get("blender")
    if not p or not Path(p).is_file():
        raise SatkError("NOT_READY", f"Blender not found: {jpath(p) if p else '(paths.blender unset)'}",
                        hint="install Blender 5.1 or set paths.blender in satk.toml / SATK_PATHS_BLENDER")
    return Path(p)


def addon_dir() -> Path:
    """``<checkout>/blender/satk_blender`` (GPL add-on + headless entry)."""
    return REPO_ROOT / "blender" / "satk_blender"


def agent_cli() -> Path:
    return addon_dir() / "agent_cli.py"


def satk_src() -> Path:
    return REPO_ROOT / "src"


def dragonff_root() -> Path:
    """Directory that contains the ``dragonff`` package (put on ``sys.path`` in Blender)."""
    return Path(os.path.abspath(cfg().paths.work)) / "blender"


def dragonff_dir() -> Path:
    return dragonff_root() / "dragonff"


def profile_dir() -> Path:
    return Path(os.path.abspath(cfg().paths.work)) / "blender" / "profile"


# --------------------------------------------------------------------------- DragonFF


def dragonff_info() -> dict:
    """``{"commit", "path", "ok"}`` of the extracted DragonFF (no side effects)."""
    d = dragonff_dir()
    info: dict[str, Any] = {"path": jpath(d), "ok": (d / "__init__.py").is_file()}
    try:
        m = json.loads((d / _MARKER).read_text(encoding="utf-8"))
        info["commit"] = str(m.get("commit", ""))[:7]
        info["source"] = m.get("source")
    except (OSError, ValueError):
        info["commit"] = None
    return info


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_NO_LAZY_FETCH"] = "1"  # never let a blobless clone in src/ reach the network (R16)
    git = shutil.which("git") or "git"
    return subprocess.run([git, *args], cwd=cwd, env=env, capture_output=True, timeout=120,
                          creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0)


def ensure_dragonff(force: bool = False) -> dict:
    """Extract DragonFF ``b3bd7aa`` with ``git archive`` into ``work/blender/dragonff`` (idempotent).

    The clone ``src/DragonFF`` is only read (``git archive`` does not touch the work tree or refs).
    """
    info = dragonff_info()
    if info["ok"] and info.get("commit") == DRAGONFF_COMMIT and not force:
        return info
    src = Path(cfg().paths.src) / "DragonFF"
    if not (src / ".git").exists():
        raise SatkError("NOT_READY", f"DragonFF clone not found: {jpath(src)}",
                        hint=f"the reference clones live in {jpath(cfg().paths.src)} (read-only)")
    r = _git(["-C", str(src), "archive", "--format=tar", DRAGONFF_SHA])
    if r.returncode != 0:
        raise SatkError("EXTERNAL_TOOL", f"git archive {DRAGONFF_COMMIT} failed: "
                        f"{r.stderr.decode('utf-8', 'replace').strip()[:300]}",
                        hint=f"git -C {jpath(src)} cat-file -t {DRAGONFF_SHA}")
    root = dragonff_root()
    dst = dragonff_dir()
    stage = root / f".dragonff.{os.getpid()}.tmp"
    ensure_writable(stage)
    ensure_writable(dst)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(r.stdout), mode="r:") as tf:
        members = [m for m in tf.getmembers() if m.isfile() or m.isdir()]
        for m in members:
            if m.name.startswith(("/", "..")) or ".." in Path(m.name).parts:
                raise SatkError("EXTERNAL_TOOL", f"unsafe path in DragonFF archive: {m.name}")
        tf.extractall(stage, members=members, filter="data")
    (stage / _MARKER).write_text(json.dumps({"commit": DRAGONFF_COMMIT, "sha": DRAGONFF_SHA,
                                             "source": jpath(src), "files": sum(1 for m in members if m.isfile())},
                                            indent=1) + "\n", encoding="utf-8")
    if dst.exists():
        shutil.rmtree(dst)
    os.replace(stage, dst)
    return dragonff_info()


# --------------------------------------------------------------------------- environment


def blender_env(job_dir: Path | None = None) -> dict[str, str]:
    """Environment for Blender: isolated user profile, no bytecode, TEMP on D:, no PYTHON* leaks."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "BLENDER_"))}
    prof = profile_dir()
    for name in ("config", "scripts", "extensions", "datafiles"):
        d = prof / name
        ensure_writable(d)
        d.mkdir(parents=True, exist_ok=True)
        env[f"BLENDER_USER_{name.upper()}"] = str(d)
    env["BLENDER_USER_RESOURCES"] = str(prof)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    tmp = (job_dir / "tmp") if job_dir is not None else Path(os.path.abspath(cfg().paths.work)) / "tmp" / "blender"
    ensure_writable(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = env["TMP"] = str(tmp)
    env["SATK_HOME"] = str(cfg().paths.workspace)
    return env


def blender_threads() -> int:
    """Render/compute threads for Blender: ``SATK_BLENDER_THREADS``, else ``[blender] threads`` (0 = all)."""
    raw = os.environ.get("SATK_BLENDER_THREADS")
    if raw is None or not str(raw).strip():
        raw = cfg().get("blender.threads", 0)
    try:
        n = int(str(raw).strip() or 0)
    except ValueError:
        raise SatkError("BAD_PARAMS", f"Blender threads must be an integer, got {raw!r}",
                        hint="SATK_BLENDER_THREADS=4 or [blender] threads = 4 in satk.toml") from None
    if not 0 <= n <= 1024:
        raise SatkError("BAD_PARAMS", f"Blender threads must be 0..1024, got {n}")
    return n


def thread_args() -> list[str]:
    """``["-t", "N"]`` for the Blender command line (empty when all cores are allowed)."""
    n = blender_threads()
    return ["-t", str(n)] if n else []


def new_job(cmd: str) -> tuple[str, Path]:
    """``(job id, job dir)`` under ``work/blender/jobs``."""
    jid = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    d = work("blender", "jobs", jid)
    return jid, d


def log_tail(path: Path, lines: int = 25) -> list[str]:
    try:
        data = path.read_bytes()[-20000:].decode("utf-8", "replace")
    except OSError:
        return []
    out = [ln.rstrip() for ln in data.splitlines() if ln.strip()]
    return out[-lines:]


def run_blender(argv: list[str], *, log: Path, timeout: float, env: dict[str, str],
                cwd: Path | None = None) -> tuple[int, float]:
    """Run Blender with ``argv`` (without the executable); stdout+stderr go to ``log``."""
    exe = blender_exe()
    ensure_writable(log)
    t0 = time.perf_counter()
    with open(log, "wb") as f:
        try:
            p = subprocess.Popen([str(exe), *argv], stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 env=env, cwd=str(cwd) if cwd else None,
                                 creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as e:
            raise SatkError("EXTERNAL_TOOL", f"cannot start Blender: {e}", data={"log": jpath(log)}) from None
        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(30)
            raise SatkError("TIMEOUT", f"Blender did not finish in {timeout:.0f} s", hint=f"see {jpath(log)}",
                            data={"log": jpath(log)}) from None
    return code, time.perf_counter() - t0


def run_job(cmd: str, args: dict | None = None, *, profile: str = "vanilla", plan: dict | None = None,
            blend: str | os.PathLike | None = None, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Run one contract command in a fresh headless Blender and return the parsed response.

    Raises ``SatkError`` with the code of an error response, ``EXTERNAL_TOOL`` (with the log tail)
    when Blender produced no response, or ``TIMEOUT``.
    """
    try:
        nargs = C.normalize_args(cmd, args)
    except C.ContractError as e:
        raise SatkError(e.code, str(e), hint=e.hint) from None
    ensure_dragonff()
    jid, jdir = new_job(cmd)
    req = C.make_request(cmd, nargs, profile=profile, out_dir=str(jdir), satk_src=str(satk_src()), job=jid,
                         dragonff=str(dragonff_root()), plan=plan)
    req_path = jdir / "request.json"
    C.write_json(req_path, req)
    log = jdir / "blender.log"
    argv = ["-b"]
    if blend is not None:
        argv.append(str(blend))
    argv += thread_args()
    argv += ["--factory-startup", "--python-exit-code", "1", "--python", str(agent_cli()), "--", str(req_path)]
    code, secs = run_blender(argv, log=log, timeout=timeout, env=blender_env(jdir))
    resp_path = jdir / "response.json"
    try:
        resp = json.loads(resp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SatkError("EXTERNAL_TOOL", f"Blender exited with code {code} and wrote no response",
                        hint=f"see {jpath(log)}", data={"log": jpath(log), "tail": log_tail(log, 15), "job": jid}) from None
    resp.setdefault("log", jpath(log))
    resp["job"] = jid
    resp.setdefault("stats", {})["process_s"] = round(secs, 2)
    if not resp.get("ok"):
        err = resp.get("error") or {}
        data = dict(err.get("data") or {})
        data.update({"log": jpath(log), "job": jid})
        if err.get("code") in (None, "EXTERNAL_TOOL", "INTERNAL"):
            data.setdefault("tail", log_tail(log, 12))
        raise SatkError(err.get("code") or "EXTERNAL_TOOL", err.get("msg") or f"Blender job failed (exit {code})",
                        hint=err.get("hint") or f"see {jpath(log)}", data=data)
    return resp
