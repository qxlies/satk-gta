"""SAAP/1 client and discovery files (SPEC §4.8.3–4.8.4).

* :class:`SaapClient` — one authenticated connection; :meth:`SaapClient.call` returns the
  ``result`` or raises :class:`~satk.core.errors.SatkError` with the SAAP error code;
* :func:`connect` — find a running endpoint by role via ``work/run/endpoints/<role>.json`` +
  ``work/run/sessions/<role>.json`` (pid must be alive) and say ``hello``;
* helpers to read/write/remove the discovery files and to check that a pid is alive
  (Windows: ``OpenProcess`` + ``GetExitCodeProcess``; never ``os.kill``, which terminates);
* :func:`verify_pid` — is the live process with that pid really the endpoint the discovery
  files describe? Windows reuses pids, so a file left behind by a crashed viewer can name an
  unrelated process. The check compares the image path (``exe``) and the process start time
  (``pid_created``, else ``started_at``) with the files.

Example::

    with connect("mock") as c:
        c.call("camera.set", {"pose": {"pos": [2495, -1720, 60], "look": [2495, -1670, 15]}})
"""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Any

from ..core.errors import SatkError
from . import frame as F
from .protocol import CLOSING_CODES, PROTOCOL_NAME

__all__ = [
    "SaapClient",
    "connect",
    "endpoints_dir",
    "sessions_dir",
    "read_endpoint",
    "read_session",
    "write_endpoint",
    "write_session",
    "remove_discovery",
    "pid_alive",
    "process_info",
    "pid_created",
    "verify_pid",
    "endpoint_alive",
    "check_paths",
    "list_endpoints",
]


# --------------------------------------------------------------------------- processes


def pid_alive(pid: int | None) -> bool:
    """True if a process of the current user with ``pid`` exists and has not exited."""
    if not pid or int(pid) <= 0:
        return False
    pid = int(pid)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            # Access denied means another user's/system process (e.g. a reused pid now owned by
            # svchost): our endpoints always run as the current user, so treat it as gone.
            return False
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            if code.value != 259:  # STILL_ACTIVE
                return False
            # An elevated caller (an administrator console, CI runners) may open processes of
            # other users too: compare the token owner, a SYSTEM/svchost pid is never ours.
            own = _token_user_sid(k32.GetCurrentProcess())
            return own is None or _token_user_sid(h) == own
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _token_user_sid(handle) -> bytes | None:
    """Binary user SID of the process ``handle`` (Windows), None if the token is not readable."""
    import ctypes
    from ctypes import wintypes

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    adv.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD))
    adv.GetLengthSid.argtypes = (ctypes.c_void_p,)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    tok = wintypes.HANDLE()
    if not adv.OpenProcessToken(handle, 0x0008, ctypes.byref(tok)):  # TOKEN_QUERY
        return None
    try:
        size = wintypes.DWORD()
        adv.GetTokenInformation(tok, 1, None, 0, ctypes.byref(size))  # TokenUser
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(tok, 1, buf, size, ctypes.byref(size)):
            return None
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]  # TOKEN_USER.User.Sid
        return ctypes.string_at(psid, adv.GetLengthSid(psid))
    finally:
        k32.CloseHandle(tok)


#: FILETIME epoch (1601-01-01) -> Unix epoch, seconds.
_FT_UNIX = 11644473600.0
#: Allowed difference between a recorded ``pid_created`` and the live process start time (s).
PID_CREATED_TOL = 1.0
#: A process that started later than ``started_at`` + this (s) cannot be the endpoint.
STARTED_AT_TOL = 2.0


def process_info(pid: int | None) -> dict | None:
    """``{"exe": image path, "created": start time in Unix seconds}`` of a live process.

    ``None`` when the process does not exist (or is not ours); a field is ``None`` when the OS
    does not tell. Windows: ``QueryFullProcessImageNameW`` + ``GetProcessTimes``; Linux: ``/proc``.
    """
    if not pid_alive(pid):
        return None
    pid = int(pid)  # type: ignore[arg-type]
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                   ctypes.POINTER(wintypes.DWORD))
        k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            exe = None
            buf = ctypes.create_unicode_buffer(32768)
            n = wintypes.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                exe = buf.value
            created = None
            ft = [wintypes.FILETIME() for _ in range(4)]
            if k32.GetProcessTimes(h, *(ctypes.byref(f) for f in ft)):
                v = (int(ft[0].dwHighDateTime) << 32) | int(ft[0].dwLowDateTime)
                if v:
                    created = v / 1e7 - _FT_UNIX
            return {"exe": exe, "created": created}
        finally:
            k32.CloseHandle(h)
    exe = created = None
    try:
        exe = os.readlink(f"/proc/{pid}/exe")
    except OSError:
        pass
    try:
        ticks = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
        btime = next(int(ln.split()[1]) for ln in Path("/proc/stat").read_text().splitlines()
                     if ln.startswith("btime "))
        created = btime + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, StopIteration, IndexError):
        pass
    return {"exe": exe, "created": created}


def pid_created(pid: int | None) -> float | None:
    """Start time of a live process (Unix seconds, 3 decimals) for the ``pid_created`` field."""
    info = process_info(pid)
    c = (info or {}).get("created")
    return round(float(c), 3) if c is not None else None


def _ts(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if not isinstance(v, str) or not v.strip():
        return None
    import datetime as _dt

    try:
        d = _dt.datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.timestamp()


def _iso(t: float) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _same_file(a: str, b: str) -> bool:
    na, nb = (os.path.normcase(os.path.normpath(os.path.abspath(x))) for x in (a, b))
    if na == nb:
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def verify_pid(*descs: dict | None) -> tuple[str, str]:
    """Is the process named by discovery files really the endpoint they describe?

    ``descs`` are the endpoint and/or the session file of a role: the first ``pid`` counts,
    ``exe``/``pid_created``/``started_at`` come from whichever file has them. Returns
    ``(state, reason)``:

    * ``"ok"`` — alive, start time (and image path, when recorded) match;
    * ``"unknown"`` — alive, but there is nothing to compare with (old files, foreign endpoint);
    * ``"mismatch"`` — alive, but another program or started after the files were written: the
      pid was reused and the files are stale. Never send signals/``WM_CLOSE`` to such a pid;
    * ``"dead"`` — no such process (of this user).
    """
    ds = [d for d in descs if isinstance(d, dict)]
    pid = next((d.get("pid") for d in ds if d.get("pid")), None)

    def first(key: str) -> Any:
        return next((d[key] for d in ds if d.get(key) not in (None, "")), None)

    if not pid_alive(pid):
        return "dead", f"pid {pid} is not running"
    info = process_info(pid)
    if info is None:
        return "dead", f"pid {pid} is not running"
    exe, created = info.get("exe"), info.get("created")
    want = first("exe")
    if want and exe and not _same_file(str(want), str(exe)):
        return "mismatch", (f"pid {pid} is now {os.path.basename(str(exe))}, not {os.path.basename(str(want))}: "
                            "the pid was reused, the discovery files are stale")
    if created is not None:
        pc = _ts(first("pid_created"))
        if pc is not None:
            if abs(created - pc) > PID_CREATED_TOL:
                return "mismatch", (f"pid {pid} was started at {_iso(created)}, the endpoint at {_iso(pc)}: "
                                    "the pid was reused, the discovery files are stale")
            return "ok", ""
        st = _ts(first("started_at"))
        if st is not None:
            if created > st + STARTED_AT_TOL:
                return "mismatch", (f"pid {pid} was started at {_iso(created)}, after the discovery files "
                                    f"({_iso(st)}): the pid was reused, the files are stale")
            return "ok", ""
    return "unknown", f"the discovery files carry no exe/start time to verify pid {pid}"


def endpoint_alive(*descs: dict | None) -> bool:
    """:func:`verify_pid` is ``ok`` or ``unknown`` (a reused pid does not count as alive)."""
    return verify_pid(*descs)[0] in ("ok", "unknown")


# --------------------------------------------------------------------------- discovery files


def endpoints_dir() -> Path:
    from ..core import paths

    return paths.work("run", "endpoints")


def sessions_dir() -> Path:
    from ..core import paths

    return paths.work("run", "sessions")


def _read(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def read_endpoint(role: str) -> dict | None:
    return _read(endpoints_dir() / f"{role}.json")


def read_session(role: str) -> dict | None:
    return _read(sessions_dir() / f"{role}.json")


def write_endpoint(role: str, data: dict) -> Path:
    from ..core import paths

    return paths.atomic_write(endpoints_dir() / f"{role}.json", json.dumps(data, ensure_ascii=False, indent=1))


def write_session(role: str, data: dict) -> Path:
    from ..core import paths

    return paths.atomic_write(sessions_dir() / f"{role}.json", json.dumps(data, ensure_ascii=False, indent=1))


def remove_discovery(role: str, pid: int | None = None) -> None:
    """Delete the endpoint/session files of ``role`` (only those of ``pid`` when given)."""
    from ..core import paths

    for p in (endpoints_dir() / f"{role}.json", sessions_dir() / f"{role}.json"):
        d = _read(p)
        if d is None and not p.exists():
            continue
        if pid is not None and d is not None and d.get("pid") not in (None, pid):
            continue
        try:
            paths.ensure_writable(p).unlink()
        except FileNotFoundError:
            pass


def list_endpoints() -> list[dict]:
    """All endpoint descriptors with ``alive`` (:func:`verify_pid`) added, sorted by role.

    A descriptor whose pid now belongs to another process gets ``alive: false`` and ``stale``
    (the reason).
    """
    out = []
    for p in sorted(endpoints_dir().glob("*.json")):
        d = _read(p)
        if d is None:
            continue
        d = dict(d)
        d.setdefault("role", p.stem)
        state, why = verify_pid(d, read_session(d["role"]))
        d["alive"] = state in ("ok", "unknown")
        if state == "mismatch":
            d["stale"] = why
        out.append(d)
    return out


def check_paths(result: Any, method: str = "") -> None:
    """``PROTOCOL`` if any returned file path lies outside the work directory (SPEC §4.8.1)."""
    from ..core import paths

    cands: list[str] = []
    files = result.get("files") if isinstance(result, dict) else None
    if isinstance(files, dict):
        cands.extend(v for v in files.values() if isinstance(v, str))
    elif isinstance(files, list):
        cands.extend(v for v in files if isinstance(v, str))
    if not cands:
        return
    try:
        work = os.path.normcase(os.path.realpath(paths.cfg().paths.work, strict=True))
    except (OSError, ValueError, RuntimeError):
        raise SatkError("PROTOCOL", "cannot resolve the work directory to validate returned paths") from None
    for c in cands:
        try:
            # These files already exist. Resolve every component, including junctions, and
            # reject paths we cannot resolve rather than falling back to lexical containment.
            a = os.path.normcase(os.path.realpath(c, strict=True))
            inside = os.path.commonpath([a, work]) == work
        except (OSError, ValueError, RuntimeError):
            inside = False
        if not inside:
            raise SatkError("PROTOCOL", f"{method or 'endpoint'} returned an unresolvable path "
                            f"or one outside the work directory: {c}",
                            data={"path": c})


# --------------------------------------------------------------------------- client


class SaapClient:
    """An authenticated SAAP/1 connection (one request in flight; thread-safe via a lock).

    Args:
        host, port: endpoint address (127.0.0.1 only).
        token: session token.
        timeout: default per-request timeout in seconds.
        name: client name sent in ``hello``.
    """

    def __init__(self, host: str, port: int, token: str, *, timeout: float = 30.0, name: str = "satk",
                 role: str | None = None, connect_timeout: float = 5.0):
        if host not in ("127.0.0.1", "localhost"):
            raise SatkError("BAD_PARAMS", "SAAP endpoints listen on 127.0.0.1 only")
        self.host, self.port, self.token = "127.0.0.1", int(port), token
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.name = name
        self.role = role
        self.sock: socket.socket | None = None
        self.hello: dict = {}
        self._n = 0
        self._lock = threading.Lock()

    # -- connection ----------------------------------------------------------------------------

    def connect(self) -> dict:
        """Open the socket and send ``hello``; returns the hello result."""
        self.close()
        try:
            s = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        except OSError as e:
            raise SatkError("NOT_READY", f"cannot connect to SAAP endpoint 127.0.0.1:{self.port}: {e}",
                            hint="satk view status") from None
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = s
        from .. import __version__

        self.hello = self._call("hello", {"token": self.token, "client": {"name": self.name, "version": __version__}},
                                self.timeout)
        return self.hello

    @property
    def caps(self) -> list[str]:
        return list(self.hello.get("caps") or [])

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self) -> "SaapClient":
        if self.sock is None:
            self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- requests ------------------------------------------------------------------------------

    def _call(self, method: str, params: dict | None, timeout: float) -> dict:
        assert self.sock is not None
        self._n += 1
        rid = f"r{self._n}"
        env: dict[str, Any] = {"saap": 1, "id": rid, "method": method}
        if params is not None:
            env["params"] = params
        self.sock.settimeout(timeout)
        try:
            self.sock.sendall(F.encode_json(env, F.MAX_REQUEST))
            resp = F.read_json(self.sock, F.MAX_RESPONSE)
        except F.FrameError as e:
            self.close()
            if e.code == "TIMEOUT":
                raise SatkError("TIMEOUT", f"{method}: no response within {timeout:g} s", data={"method": method}) from None
            raise SatkError("PROTOCOL", f"{method}: {e.msg}") from None
        except OSError as e:
            self.close()
            raise SatkError("NOT_READY", f"{method}: connection lost: {e}", hint="satk view status") from None
        if resp.get("saap") != 1 or resp.get("id") not in (rid, ""):
            self.close()
            raise SatkError("PROTOCOL", f"{method}: unexpected response envelope (id {resp.get('id')!r})")
        if not resp.get("ok"):
            err = resp.get("error") or {}
            code = str(err.get("code") or "INTERNAL")
            if code in CLOSING_CODES:
                self.close()
            data = dict(err.get("data") or {})
            if resp.get("meta"):
                data.setdefault("meta", resp["meta"])
            raise SatkError(code, str(err.get("message") or code), data=data or None)
        result = resp.get("result")
        if not isinstance(result, dict):
            raise SatkError("PROTOCOL", f"{method}: result is not an object")
        if method in ("capture", "asset.render"):
            check_paths(result, method)
        self.last_meta = resp.get("meta") or {}
        return result

    def call(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> dict:
        """Send a request and return its ``result`` (reconnects once if the socket was closed)."""
        with self._lock:
            if self.sock is None:
                self.connect()
            try:
                return self._call(method, params, timeout or self.timeout)
            except SatkError as e:
                if e.code == "NOT_READY" and "connection lost" in e.msg and method not in ("quit",):
                    self.connect()
                    return self._call(method, params, timeout or self.timeout)
                raise


def connect(role: str, *, timeout: float = 30.0, name: str = "satk") -> SaapClient:
    """Connect to the running endpoint of ``role`` (discovery files + pid check + hello)."""
    ep = read_endpoint(role)
    if ep is None:
        raise SatkError("NOT_READY", f"no endpoint for role {role!r} (work/run/endpoints/{role}.json missing)",
                        hint=f"satk view start --target {role}" if role in ("ariane", "mock") else "satk view status")
    if ep.get("protocol") != PROTOCOL_NAME:
        raise SatkError("UNSUPPORTED", f"endpoint {role!r} speaks {ep.get('protocol')!r}, not {PROTOCOL_NAME}",
                        data={"protocol": ep.get("protocol")})
    sess = read_session(role)
    state, why = verify_pid(ep, sess)
    if state == "dead":
        raise SatkError("NOT_READY", f"endpoint {role!r} (pid {ep.get('pid')}) is not running",
                        hint=f"satk view start --target {role}")
    if state == "mismatch":
        raise SatkError("NOT_READY", f"endpoint {role!r}: {why}",
                        hint=f"satk view start --target {role} (replaces the stale files)", data={"stale": True})
    if sess is None or not sess.get("token"):
        raise SatkError("AUTH", f"no session token for role {role!r} (work/run/sessions/{role}.json)")
    if sess.get("pid") not in (None, ep.get("pid")):
        raise SatkError("NOT_READY", f"session and endpoint files of {role!r} disagree on the pid",
                        hint=f"satk view stop --target {role}; satk view start --target {role}")
    c = SaapClient("127.0.0.1", int(ep["port"]), str(sess["token"]), timeout=timeout, name=name, role=role)
    c.connect()
    return c
