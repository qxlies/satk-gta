"""Generic SAAP/1 endpoint server (threaded, 127.0.0.1 only) used by the mock (SPEC §4.8).

The server owns the transport rules: framing limits, ``hello`` authentication (constant-time
token compare), ``PROTOCOL``/``AUTH`` errors that close the connection, ``UNKNOWN_METHOD``,
capability gating (``UNSUPPORTED``), params validation against the schemas (``BAD_PARAMS``),
one request in flight per connection, and the discovery descriptor. Methods are executed
one at a time under a lock, like an endpoint that dispatches on its main thread.

A *world* object supplies the behaviour::

    class World:
        caps: list[str]
        def hello_info(self) -> dict: ...                  # role/impl/build/... for hello
        def handle(self, method: str, params: dict) -> dict: ...   # raise SatkError on failure
        def meta(self) -> dict: ...                        # {"frame", "rev": {...}}
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from ..core.errors import SatkError
from ..core.log import get_logger
from . import frame as F
from . import schema as S
from .protocol import CLOSING_CODES, ERROR_CODES, PROTOCOL_NAME, RETRYABLE

__all__ = ["World", "SaapServer", "to_saap_error", "error_envelope", "precheck"]

log = get_logger("saap.server")

#: After an error that closes the connection: discard at most this many incoming bytes ...
DRAIN_BYTES = 4 * F.MAX_REQUEST
#: ... for at most this long (s) ...
DRAIN_S = 2.0
#: ... and stop after this much silence (s).
DRAIN_IDLE_S = 0.25

#: satk error codes that are not SAAP codes -> SAAP code.
_CODE_MAP = {
    "BAD_ID": "BAD_PARAMS", "AMBIGUOUS": "BAD_PARAMS", "INDEX_MISSING": "NOT_READY", "DEPENDENCY": "NOT_READY",
    "READ_ONLY": "BAD_PARAMS", "PROTECTED_PATH": "BAD_PARAMS", "EXISTS": "BAD_PARAMS",
    "EXTERNAL_TOOL": "INTERNAL", "CONSENT_REQUIRED": "UNSUPPORTED",
}


class World(Protocol):
    caps: list[str]

    def hello_info(self) -> dict: ...

    def handle(self, method: str, params: dict) -> dict: ...

    def meta(self) -> dict: ...


def to_saap_error(e: SatkError) -> dict:
    code = e.code if e.code in ERROR_CODES else _CODE_MAP.get(e.code, "INTERNAL")
    data = dict(e.data or {})
    if code != e.code:
        data.setdefault("satk_code", e.code)
    if e.hint:
        data.setdefault("hint", e.hint)
    return {"code": code, "message": e.msg, "retryable": code in RETRYABLE, "data": data}


def precheck(method: str, params: dict, caps: list[str], *, validate: bool = True) -> None:
    """Protocol checks before a method runs: ``UNKNOWN_METHOD``, ``UNSUPPORTED``, ``BAD_PARAMS``."""
    methods = S.methods()
    if method not in methods:
        raise SatkError("UNKNOWN_METHOD", f"unknown method {method!r}")
    cap = methods[method]
    if cap not in caps:
        raise SatkError("UNSUPPORTED", f"capability {cap!r} not available", data={"capability": cap})
    if validate and method != "hello":
        errs = S.validate_params(method, params)
        if errs:
            raise SatkError("BAD_PARAMS", errs[0], data={"errors": errs[:10]})


def error_envelope(req_id: str, code: str, message: str, data: dict | None = None, meta: dict | None = None) -> dict:
    env = {"saap": 1, "id": req_id, "ok": False,
           "error": {"code": code, "message": message, "retryable": code in RETRYABLE, "data": data or {}}}
    if meta:
        env["meta"] = meta
    return env


class _Conn(threading.Thread):
    def __init__(self, server: "SaapServer", sock: socket.socket, addr):
        super().__init__(name=f"saap-conn-{addr[1]}", daemon=True)
        self.server = server
        self.sock = sock
        self.addr = addr
        self.authed = False

    def _send(self, obj: dict) -> None:
        try:
            data = F.encode_json(obj, F.MAX_RESPONSE)
        except F.FrameError:
            data = F.encode_json(error_envelope(obj.get("id", ""), "INTERNAL", "response exceeds the 8 MiB limit"))
        self.sock.sendall(data)

    def _close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _linger_close(self) -> None:
        """Close after an error reply without losing it (SPEC §4.8.1 "error and disconnect").

        Closing a socket whose receive buffer still holds unread bytes (the body of an oversize
        frame, pipelined requests) makes the OS send RST, and the client's ``recv`` then fails
        with "connection reset" before it reads the error frame. So: half-close (FIN after the
        reply), read and discard what the peer still sends (≤ ``DRAIN_BYTES``, ≤ ``DRAIN_S``,
        stop after ``DRAIN_IDLE_S`` of silence or at EOF), then close.
        """
        try:
            self.sock.shutdown(socket.SHUT_WR)
        except OSError:
            self._close()
            return
        deadline = time.monotonic() + DRAIN_S
        got = 0
        try:
            while got < DRAIN_BYTES:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self.sock.settimeout(min(DRAIN_IDLE_S, left))
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                got += len(chunk)
        except OSError:  # timeout (peer is silent) or reset
            pass
        self._close()

    def run(self) -> None:
        srv = self.server
        linger = False  # an error reply was sent: let the client read it before the socket goes

        def fail(rid_s: str, msg: str, data: dict | None = None) -> None:
            nonlocal linger
            self._send(error_envelope(rid_s, "PROTOCOL", msg, data))
            linger = True

        try:
            self.sock.settimeout(srv.idle_timeout)
            while not srv.stopping.is_set():
                try:
                    payload = F.read_frame(self.sock, F.MAX_REQUEST)
                except F.FrameError as e:
                    if e.closed:
                        return
                    if e.code == "TIMEOUT":
                        return
                    fail("", e.msg, {"length": e.length} if e.length is not None else None)
                    return
                try:
                    req = F.decode_json(payload)
                except F.FrameError as e:
                    fail("", e.msg)
                    return
                rid = req.get("id")
                rid_s = rid if isinstance(rid, str) else ""
                if req.get("saap") != 1:
                    fail(rid_s, f"unsupported protocol version {req.get('saap')!r}")
                    return
                if not isinstance(rid, str) or not rid or len(rid) > 64 or not isinstance(req.get("method"), str):
                    fail(rid_s, "request needs a string 'id' and 'method'")
                    return
                params = req.get("params", {})
                if params is None:
                    params = {}
                if not isinstance(params, dict):
                    fail(rid_s, "'params' must be an object")
                    return
                resp, close = srv.dispatch(self, rid, req["method"], params)
                self._send(resp)
                if close:
                    linger = True
                    return
                if req["method"] == "quit" and resp.get("ok"):
                    linger = True
                    srv.request_stop()
                    return
        except OSError:
            linger = False
            return
        finally:
            if linger:
                self._linger_close()
            else:
                self._close()
            srv._forget(self)


class SaapServer:
    """Threaded SAAP endpoint on 127.0.0.1.

    Args:
        world: behaviour (see module docstring).
        token: expected token; ``None`` only with ``insecure=True`` (role ``mock``).
        role: endpoint role for discovery (``mock``, ``ariane``, ...).
        port: TCP port, 0 = ephemeral.
        validate: validate params against the schemas before calling the world.
    """

    def __init__(self, world: Any, *, token: str | None, role: str = "mock", port: int = 0,
                 insecure: bool = False, validate: bool = True, idle_timeout: float = 600.0):
        if token is None and not insecure:
            raise SatkError("AUTH", "a SAAP endpoint needs a token (SATK_AGENT_TOKEN)")
        if token is not None and not F.token_ok(token):
            raise SatkError("BAD_PARAMS", "token must be 32-256 bytes without CR/LF")
        self.world = world
        self.token = token
        self.insecure = insecure
        self.role = role
        self.validate = validate
        self.idle_timeout = idle_timeout
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.started_at = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._conns: set[_Conn] = set()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt":
            self._sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", 0x4), 1)
        self._sock.bind(("127.0.0.1", int(port)))
        self._sock.listen(16)
        self.port = self._sock.getsockname()[1]
        self._thread: threading.Thread | None = None
        self.descriptor_path: Path | None = None
        self.requests = 0

    # -- descriptor --------------------------------------------------------------------------

    def descriptor(self) -> dict:
        from .client import process_info

        info = self.world.hello_info()
        d = {"protocol": PROTOCOL_NAME, "role": self.role, "impl": info.get("impl"),
             "impl_version": info.get("impl_version"), "pid": os.getpid(), "port": self.port,
             "build": info.get("build") or {}, "caps": list(self.world.caps), "started_at": self.started_at}
        me = process_info(os.getpid()) or {}
        if me.get("exe"):
            d["exe"] = str(me["exe"]).replace("\\", "/")  # the real image (a venv python.exe is a launcher)
        if me.get("created") is not None:
            d["pid_created"] = round(float(me["created"]), 3)
        return d

    def write_descriptor(self, directory: Path) -> Path:
        """Write ``<directory>/<role>.json`` atomically (no token)."""
        from ..core.paths import atomic_write

        p = Path(directory) / f"{self.role}.json"
        atomic_write(p, json.dumps(self.descriptor(), ensure_ascii=False, indent=1))
        self.descriptor_path = p
        return p

    # -- dispatch ----------------------------------------------------------------------------

    def _meta(self, t0: float) -> dict:
        try:
            m = dict(self.world.meta())
        except Exception:  # noqa: BLE001
            m = {}
        m["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return m

    def dispatch(self, conn: _Conn | None, rid: str, method: str, params: dict) -> tuple[dict, bool]:
        """(response envelope, close connection?)."""
        t0 = time.perf_counter()
        authed = conn.authed if conn is not None else True
        if method == "hello":
            tok = params.get("token")
            good = F.token_ok(tok) and (self.insecure or F.token_equal(self.token or "", tok))
            if not good:
                log.warning("saap: authentication failed from %s", conn.addr if conn else "?")
                return error_envelope(rid, "AUTH", "authentication failed", meta=self._meta(t0)), True
            if conn is not None:
                conn.authed = True
        elif not authed:
            return error_envelope(rid, "AUTH", "the first request must be hello", meta=self._meta(t0)), True
        try:
            precheck(method, params, list(self.world.caps), validate=self.validate)
        except SatkError as e:
            return error_envelope(rid, e.code, e.msg, e.data, self._meta(t0)), False
        with self.lock:
            self.requests += 1
            try:
                if method == "hello":
                    result = self.world.handle("hello", params)
                else:
                    result = self.world.handle(method, params)
            except SatkError as e:
                err = to_saap_error(e)
                env = {"saap": 1, "id": rid, "ok": False, "error": err, "meta": self._meta(t0)}
                return env, err["code"] in CLOSING_CODES
            except Exception as e:  # noqa: BLE001
                log.exception("saap: %s failed", method)
                return error_envelope(rid, "INTERNAL", f"{type(e).__name__}: {e}", meta=self._meta(t0)), False
            return {"saap": 1, "id": rid, "ok": True, "result": result, "meta": self._meta(t0)}, False

    # -- lifecycle ---------------------------------------------------------------------------

    def _forget(self, c: _Conn) -> None:
        with self.lock:
            self._conns.discard(c)

    def _accept_loop(self) -> None:
        self._sock.settimeout(0.2)
        while not self.stopping.is_set():
            try:
                s, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if addr[0] != "127.0.0.1":  # pragma: no cover - bound to loopback anyway
                s.close()
                continue
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            c = _Conn(self, s, addr)
            with self.lock:
                self._conns.add(c)
            c.start()
        try:
            self._sock.close()
        except OSError:
            pass

    def start(self) -> "SaapServer":
        """Start accepting in a background thread; returns ``self``."""
        self._thread = threading.Thread(target=self._accept_loop, name=f"saap-{self.role}", daemon=True)
        self._thread.start()
        return self

    def serve_forever(self, poll: float = 0.25) -> None:
        """Block until ``quit`` or :meth:`stop` (Ctrl+C also stops)."""
        if self._thread is None:
            self.start()
        try:
            while not self.stopping.wait(poll):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def request_stop(self) -> None:
        self.stopping.set()

    def stop(self) -> None:
        self.stopping.set()
        try:
            self._sock.close()
        except OSError:
            pass
        with self.lock:
            conns = list(self._conns)
        for c in conns:
            c._close()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        if self.descriptor_path is not None:
            try:
                d = json.loads(self.descriptor_path.read_text(encoding="utf-8"))
                if d.get("pid") == os.getpid() and d.get("port") == self.port:
                    self.descriptor_path.unlink()
            except (OSError, ValueError):
                pass

    def __enter__(self) -> "SaapServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
