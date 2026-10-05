"""Start, stop and inspect viewer endpoints (SPEC §4.10.5).

``start("ariane")``:

1. free loopback port, token ``secrets.token_hex(32)``;
2. env ``ARIANE_ENGINE_TCP_PORT`` / ``ARIANE_ENGINE_TOKEN`` (the ``ARIANE_IPC/1`` bridge) and, for the
   fork's native SAAP/1 endpoint (P1, M2-11), ``SATK_AGENT_PORT=0`` / ``SATK_AGENT_TOKEN`` (the same
   token) / ``SATK_AGENT_DESCRIPTOR`` / ``SATK_AGENT_OUT_ROOT`` (the work directory);
3. ``ariane.exe --game-dir <profile root> --data-dir work\\viewer --window 1280x720+0+0 --no-vsync``
   (stdout/stderr -> ``work\\logs\\viewer-ariane.log``);
4. wait for ``ping`` (≤ 90 s). A build with the native endpoint (``ariane.build.json``:
   ``saap_endpoint``) writes ``work\\run\\endpoints\\ariane.json`` itself (``protocol: "saap/1"``,
   ``impl: "ariane-satk"``: any capture size, ID buffer, depth, visible picks, ``view.set``); for an
   older build the launcher writes it (``protocol: "ariane-ipc/1"``, ``impl: "ariane-legacy"``).
   ``sessions\\ariane.json`` is always the launcher's.

``start("mock")`` spawns ``python -m satk view mock --port 0`` (it writes its own discovery
files). ``stop`` sends ``quit`` (Ariane: the ``quit`` command or ``WM_CLOSE``), waits 10 s,
then terminates the process. ``status`` reports ``up``/``proto``/``caps``/``pid``/``window``
and ``NOT_READY`` with a hint when the Ariane window is minimized (D3D9 does not draw then).

The discovery files record the image path (``exe``) and the process start time
(``pid_created``). Windows reuses pids, so before trusting, closing or terminating a pid the
launcher checks both (:func:`satk.saap.client.verify_pid`); files that name another process are
stale — ``start`` replaces them, ``stop`` only deletes them.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..core import paths
from ..core.errors import SatkError
from ..core.log import get_logger
from ..saap import client as C

__all__ = ["start", "stop", "status", "free_port", "windows_of", "is_minimized", "close_windows", "terminate",
           "ariane_build_info"]

log = get_logger("viewer.launcher")

ARIANE_WAIT_S = 90.0
STOP_WAIT_S = 10.0


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


# --------------------------------------------------------------------------- Win32 helpers


def _user32():
    import ctypes
    from ctypes import wintypes

    u = ctypes.WinDLL("user32", use_last_error=True)
    u.EnumWindows.argtypes = (ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM)
    u.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    u.IsWindowVisible.argtypes = (wintypes.HWND,)
    u.IsIconic.argtypes = (wintypes.HWND,)
    u.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    u.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    u.GetWindow.restype = wintypes.HWND
    return u


def windows_of(pid: int) -> list[int]:
    """Visible top-level windows (HWND ints) of a process; ``[]`` off Windows."""
    if os.name != "nt" or not pid:
        return []
    import ctypes
    from ctypes import wintypes

    u = _user32()
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(hwnd) and not u.GetWindow(hwnd, 4):  # GW_OWNER
            found.append(int(hwnd))
        return True

    u.EnumWindows(cb, 0)
    return found


def is_minimized(pid: int) -> bool:
    if os.name != "nt":
        return False
    u = _user32()
    wins = windows_of(pid)
    return bool(wins) and all(u.IsIconic(h) for h in wins)


def close_windows(pid: int) -> int:
    """Post ``WM_CLOSE`` to the process windows; returns how many were found."""
    if os.name != "nt":
        return 0
    u = _user32()
    wins = windows_of(pid)
    for h in wins:
        u.PostMessageW(h, 0x0010, 0, 0)
    return len(wins)


def terminate(pid: int) -> bool:
    """``TerminateProcess`` (Windows) / ``SIGTERM``; True if the call was made."""
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        h = k.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
        if not h:
            return False
        try:
            return bool(k.TerminateProcess(h, 1))
        finally:
            k.CloseHandle(h)
    import signal

    try:
        os.kill(int(pid), signal.SIGTERM)
        return True
    except OSError:
        return False


def _wait_dead(pid: int, seconds: float) -> bool:
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        if not C.pid_alive(pid):
            return True
        time.sleep(0.2)
    return not C.pid_alive(pid)


# --------------------------------------------------------------------------- ariane


def ariane_build_info(exe: Path) -> dict:
    p = Path(exe).with_name("ariane.build.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _parse_window(window: str | None) -> str:
    w = window or str(paths.cfg().get("viewer.window", "1280x720"))
    if "+" not in w:
        w += "+0+0"
    import re

    if not re.match(r"^\d{2,5}x\d{2,5}\+-?\d+\+-?\d+$", w):
        raise SatkError("BAD_PARAMS", f"window must look like 1280x720[+X+Y], got {w!r}")
    return w


def _start_ariane(profile: str, window: str | None, wait: float) -> dict:
    from .backends.ariane_legacy import ArianeLegacyBackend

    cfg = paths.cfg()
    exe = Path(cfg.paths.viewer)
    if not exe.is_file():
        raise SatkError("NOT_READY", f"viewer not built: {paths.jpath(exe)}",
                        hint="powershell -ExecutionPolicy Bypass -File "
                        + str(Path(cfg.paths.workspace) / "viewer" / "ariane" / "satk" / "build.ps1"))
    game_dir = cfg.profile(profile).root
    if not (game_dir / "gta_sa.exe").is_file():
        raise SatkError("NOT_READY", f"game root of profile {profile!r} not found: {paths.jpath(game_dir)}")
    data_dir = paths.work("viewer")
    port = free_port()
    token = secrets.token_hex(32)
    win = _parse_window(window)
    args = [str(exe), "--game-dir", str(game_dir), "--data-dir", str(data_dir), "--window", win, "--no-vsync"]
    env = dict(os.environ)
    env["ARIANE_ENGINE_TCP_PORT"] = str(port)
    env["ARIANE_ENGINE_TOKEN"] = token
    env.pop("ARIANE_GAME_DIR", None)
    # Native SAAP/1 endpoint (fork P1): ephemeral port; the endpoint writes its own descriptor and
    # refuses capture paths outside the work directory.
    env.update({"SATK_AGENT_PORT": "0", "SATK_AGENT_TOKEN": token, "SATK_AGENT_ROLE": "ariane",
                "SATK_AGENT_DESCRIPTOR": str(C.endpoints_dir() / "ariane.json"),
                "SATK_AGENT_OUT_ROOT": paths.jpath(cfg.paths.work)})
    build = ariane_build_info(exe)
    logf = paths.work("logs", "viewer-ariane.log")
    paths.ensure_writable(logf)
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP  # Ctrl+C in this console must not kill the viewer
    with open(logf, "ab") as lf:
        lf.write(f"\n=== {_now()} satk view start: {' '.join(args)}\n".encode("utf-8"))
        lf.flush()
        proc = subprocess.Popen(args, cwd=str(game_dir), env=env, stdin=subprocess.DEVNULL, stdout=lf,
                                stderr=subprocess.STDOUT, creationflags=flags, close_fds=True)
    sess = {"role": "ariane", "pid": proc.pid, "token": token, "log": paths.jpath(logf),
            "launched_by": "satk view start", "args": args[1:], "started_at": _now(), "port": port,
            "profile": profile, "exe": paths.jpath(exe), "pid_created": C.pid_created(proc.pid)}
    C.write_session("ariane", sess)
    t0 = time.monotonic()
    last_err = ""
    backend = ArianeLegacyBackend("ariane", "ariane", port, token, pid=proc.pid, exe=exe)
    while time.monotonic() - t0 < wait:
        if proc.poll() is not None:
            C.remove_discovery("ariane", proc.pid)
            raise SatkError("EXTERNAL_TOOL", f"ariane.exe exited with code {proc.returncode} during start-up",
                            hint=f"see {paths.jpath(logf)}", data={"log": paths.jpath(logf)})
        try:
            backend.ping()
            break
        except SatkError as e:
            last_err = e.msg
            time.sleep(0.5)
    else:
        terminate(proc.pid)
        C.remove_discovery("ariane", proc.pid)
        raise SatkError("TIMEOUT", f"Ariane did not answer ping within {wait:g} s ({last_err})",
                        hint=f"see {paths.jpath(logf)}")
    native = _native_endpoint(proc.pid, bool(build.get("saap_endpoint")))
    if native is not None:
        return _started_native(native, sess, logf, t0)
    hello = backend.call("hello", {})
    ep = {"protocol": "ariane-ipc/1", "role": "ariane", "impl": "ariane-legacy",
          "impl_version": build.get("ariane_commit", "")[:9], "pid": proc.pid, "port": port,
          "build": hello.get("build") or {}, "caps": hello.get("caps") or [], "started_at": sess["started_at"],
          "exe": paths.jpath(exe), "pid_created": sess["pid_created"], "window": win}
    C.write_endpoint("ariane", ep)
    return {"target": "ariane", "up": True, "proto": "ariane-ipc/1", "impl": "ariane-legacy", "pid": proc.pid,
            "port": port, "caps": ep["caps"], "window": _window_of(hello),
            "seconds": round(time.monotonic() - t0, 1), "log": paths.jpath(logf)}


#: Extra wait for the native descriptor after the bridge answered (builds with ``saap_endpoint``).
NATIVE_WAIT_S = 5.0


def _native_endpoint(pid: int, expected: bool) -> dict | None:
    """Descriptor of the fork's native SAAP/1 endpoint of ``pid``, or ``None`` (an older build).

    Bridge and endpoint start listening in the same frame, so after the bridge's first ``ping`` the
    descriptor is normally there; a build that announces the endpoint gets a few more seconds.
    """
    t_end = time.monotonic() + (NATIVE_WAIT_S if expected else 0.0)
    while True:
        ep = C.read_endpoint("ariane")
        if ep and ep.get("protocol") == "saap/1" and ep.get("pid") == pid and ep.get("port"):
            return ep
        if time.monotonic() >= t_end:
            return None
        time.sleep(0.2)


def _started_native(ep: dict, sess: dict, logf: Path, t0: float) -> dict:
    from .backends.saap_native import SaapNativeBackend

    C.write_session("ariane", dict(sess, saap_port=int(ep["port"])))
    b = SaapNativeBackend("ariane", "ariane", timeout=30.0)
    try:
        hello = b.hello
    finally:
        b.close()
    return {"target": "ariane", "up": True, "proto": "saap/1", "impl": str(ep.get("impl") or "ariane-satk"),
            "pid": int(ep["pid"]), "port": int(ep["port"]), "caps": hello.get("caps") or ep.get("caps") or [],
            "window": _window_of(hello), "seconds": round(time.monotonic() - t0, 1), "log": paths.jpath(logf)}


# --------------------------------------------------------------------------- mock


def _start_mock(wait: float) -> dict:
    port = 0
    token = secrets.token_hex(32)
    env = dict(os.environ)
    env["SATK_AGENT_TOKEN"] = token
    env["SATK_LAUNCHED_BY"] = "satk view start"
    env["PYTHONUTF8"] = "1"
    from ..core.config import SRC_ROOT

    env["PYTHONPATH"] = str(SRC_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    logf = paths.work("logs", "viewer-mock.log")
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000  # CREATE_NO_WINDOW
    args = [sys.executable, "-X", "utf8", "-m", "satk", "view", "mock", "--port", str(port)]
    with open(logf, "ab") as lf:
        proc = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                                creationflags=flags, close_fds=True)
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait:
        ep = C.read_endpoint("mock")
        sess = C.read_session("mock") or {}
        # match by token: the venv python.exe on Windows is a launcher, so the pid differs
        if ep and sess.get("token") == token and ep.get("pid") == sess.get("pid") and C.pid_alive(ep.get("pid")):
            c = C.connect("mock", timeout=10)
            caps, win = c.caps, _window_of(c.hello)
            c.close()
            return {"target": "mock", "up": True, "proto": "saap/1", "impl": ep.get("impl"), "pid": ep.get("pid"),
                    "port": ep.get("port"), "caps": caps, "window": win, "seconds": round(time.monotonic() - t0, 1),
                    "log": paths.jpath(logf)}
        if proc.poll() is not None:
            raise SatkError("EXTERNAL_TOOL", f"mock endpoint exited with code {proc.returncode}",
                            hint=f"see {paths.jpath(logf)}")
        time.sleep(0.1)
    terminate(proc.pid)
    raise SatkError("TIMEOUT", f"mock endpoint did not start within {wait:g} s")


# --------------------------------------------------------------------------- public


def _window_of(hello: dict | None) -> list[int] | None:
    """``[w, h]`` of the endpoint viewport: the one ``window`` shape of start, status and reuse."""
    vp = (hello or {}).get("viewport") or {}
    if isinstance(vp, dict) and vp.get("w") and vp.get("h"):
        return [int(vp["w"]), int(vp["h"])]
    return None


def _discovery(target: str) -> tuple[dict | None, dict | None, int | None, str, str]:
    """``(endpoint, session, pid, state, reason)``; state as :func:`satk.saap.client.verify_pid`."""
    ep = C.read_endpoint(target)
    sess = C.read_session(target)
    pid = (ep or {}).get("pid") or (sess or {}).get("pid")
    if not pid:
        return ep, sess, None, "dead", "no discovery files"
    state, why = C.verify_pid(ep, sess)
    return ep, sess, int(pid), state, why


def start(target: str = "ariane", *, profile: str = "vanilla", window: str | None = None,
          wait: float | None = None) -> dict:
    """Start the endpoint of ``target`` (``ariane`` or ``mock``); reuse a running one.

    Discovery files whose pid is gone or now belongs to another process (pid reuse after a
    crash) are stale: they are replaced and a new endpoint is started (``warn``).
    """
    if target not in ("ariane", "mock"):
        raise SatkError("UNSUPPORTED", f"satk cannot start target {target!r} (start it yourself)")
    ep, sess, pid, state, why = _discovery(target)
    if ep is not None and state in ("ok", "unknown"):
        st = status(target)
        if st.get("up") or state == "ok":
            st["reused"] = True
            if not st.get("up"):
                st["hint"] = (f"pid {pid} is the running {target} but it does not answer: "
                              f"satk view stop --target {target}, then start again")
            return st
        # Files without exe/start time whose endpoint does not answer: nothing proves the pid
        # is ours, and nobody can use those files -> stale.
        state, why = "mismatch", f"pid {pid} does not answer and cannot be verified as {target} ({why})"
    warn = []
    if state == "mismatch":
        warn.append(f"NOT_READY: replaced stale discovery files; {why}")
        log.info("view start %s: %s", target, why)
    C.remove_discovery(target)
    if target == "ariane":
        out = _start_ariane(profile, window, ARIANE_WAIT_S if wait is None else wait)
    else:
        out = _start_mock(20.0 if wait is None else wait)
    if warn:
        out["warn"] = warn
    return out


def stop(target: str = "ariane") -> dict:
    """Stop ``target``: ``quit`` -> wait 10 s -> ``WM_CLOSE`` -> terminate. Removes the discovery files.

    ``WM_CLOSE`` and termination only ever go to a pid verified as the endpoint
    (:func:`satk.saap.client.verify_pid`: image path and start time). Stale files (the pid is
    gone or reused by another program) are just deleted; the process is not touched.
    """
    ep, sess, pid, state, why = _discovery(target)
    if state == "dead":
        C.remove_discovery(target)
        return {"target": target, "up": False, "stopped": False, "note": "not running"}
    if state == "mismatch":
        C.remove_discovery(target)
        log.info("view stop %s: %s", target, why)
        return {"target": target, "up": False, "stopped": False, "stale": True,
                "note": f"not running; removed stale discovery files, pid {pid} was not touched. {why}"}
    verified = state == "ok"
    how = "quit"
    try:
        from .backends import get_backend

        b = get_backend(target, allow_inprocess_mock=False)
        try:
            b.call("quit", {})
        finally:
            b.close()
    except SatkError as e:
        log.info("quit failed (%s)", e.msg)
        if not verified:
            C.remove_discovery(target)
            return {"target": target, "pid": pid, "up": False, "stopped": False, "stale": True,
                    "note": f"no answer to quit and {why}: discovery files removed, pid {pid} was not "
                            f"touched. {e.msg}"}
        how = "wm_close" if close_windows(pid) else "none"
    out: dict[str, Any] = {"target": target, "pid": pid}
    if not _wait_dead(pid, STOP_WAIT_S):
        # Force only a pid that is still provably ours (re-checked: the pid may have died and
        # been reused while we waited).
        if verified and C.verify_pid(ep, sess)[0] == "ok":
            if how == "quit" and close_windows(pid):
                how = "wm_close"
                _wait_dead(pid, 3.0)
            if C.verify_pid(ep, sess)[0] == "ok":
                terminate(pid)
                how = "terminated"
                _wait_dead(pid, 5.0)
        elif not verified:
            out["warn"] = [f"NOT_READY: pid {pid} did not exit and is not terminated: {why}"]
    alive = C.verify_pid(ep, sess)[0] in ("ok", "unknown")
    if not alive:
        C.remove_discovery(target)
    out.update({"up": alive, "stopped": not alive, "how": how})
    return out


def status(target: str = "ariane", *, probe: bool = True) -> dict:
    """``{target, up, proto, impl, caps, pid, window}``; ``NOT_READY`` if the window is minimized.

    Files whose pid is gone or reused: ``{up: false, stale: true, note}``.
    """
    ep, sess, pid, state, why = _discovery(target)
    if ep is None or state in ("dead", "mismatch"):
        out: dict[str, Any] = {"target": target, "up": False}
        if ep is not None:
            out["stale"] = True
            out["note"] = why
            out["hint"] = f"satk view start --target {target} (replaces the stale files)"
        return out
    out = {"target": target, "up": True, "proto": ep.get("protocol"), "impl": ep.get("impl"), "pid": ep.get("pid"),
           "port": ep.get("port"), "caps": ep.get("caps") or []}
    if target == "ariane" and state == "ok" and is_minimized(int(ep["pid"])):
        raise SatkError("NOT_READY", "the Ariane window is minimized: D3D9 does not render, captures would be empty",
                        hint="restore the Ariane window (click it in the taskbar)", data=out)
    if probe:
        from .backends import get_backend

        try:
            b = get_backend(target, allow_inprocess_mock=False)
            try:
                h = b.hello
                out["caps"] = h.get("caps") or out["caps"]
                win = _window_of(h)
                if win:
                    out["window"] = win
                out["build"] = (h.get("build") or {}).get("id")
            finally:
                b.close()
        except SatkError as e:
            out["up"] = False
            out["error"] = {"code": e.code, "msg": e.msg}
            out["hint"] = (f"satk view stop --target {target}, then start again" if state == "ok" else
                           f"satk view start --target {target} (replaces the files: {why})")
    return {k: v for k, v in out.items() if v is not None}
