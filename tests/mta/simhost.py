"""Host for ``tests/mta/sim.lua``: the real satk-agent scripts in a simulated MTA, served over HTTP.

``SimHost`` loads ``shared.lua`` + ``server.lua`` into the simulated server side and ``shared.lua`` +
``client.lua`` into the client side of one Lua 5.1 state (the fork's ``lua5.1.dll`` via
:mod:`luart`), answers ``POST /satk-agent/call/rpc`` like MTA's HTTP server does (JSON array of
arguments in, JSON array of results out) and pumps frames in a thread. PNGs for
``dxConvertPixels`` come from :func:`satk.saap.png.encode` through a C callback.
"""

from __future__ import annotations

import ctypes
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from luart import LUA_GLOBALSINDEX, Lua

HERE = Path(__file__).resolve().parent
LUA_CF = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)


def to_lua(v) -> str:
    """Python JSON value -> Lua literal (like MTA's JSON -> Lua conversion; null -> nil)."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v) if math.isfinite(v) else "nil"
    if isinstance(v, str):
        out = []
        for ch in v.encode("utf-8"):
            c = chr(ch)
            out.append(c if 32 <= ch < 127 and c not in "\\\"" else f"\\{ch:03d}")
        return "\"" + "".join(out) + "\""
    if isinstance(v, list):
        return "{" + ",".join(to_lua(x) for x in v) + "}"
    if isinstance(v, dict):
        return "{" + ",".join(f"[{to_lua(str(k))}]={to_lua(x)}" for k, x in v.items()) + "}"
    raise TypeError(type(v))


class SimHost:
    def __init__(self, dll: Path, resource: Path, server_root: Path, token: str, *, client: bool = True,
                 W: int = 1280, H: int = 720, K: float = 1.15, pump_ms: float = 3.0):
        self.lock = threading.RLock()
        self.lua = Lua(dll)
        self.res_dir = server_root / "mods" / "deathmatch" / "resources" / "satk-agent"
        (self.res_dir / "captures").mkdir(parents=True, exist_ok=True)
        self._png_cb = LUA_CF(self._py_png)
        d = self.lua.d
        d.lua_pushcclosure.argtypes = [ctypes.c_void_p, LUA_CF, ctypes.c_int]
        d.lua_setfield.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p]
        d.lua_tonumber.argtypes = [ctypes.c_void_p, ctypes.c_int]
        d.lua_tonumber.restype = ctypes.c_double
        d.lua_pushlstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        d.lua_pushcclosure(self.lua.L, self._png_cb, 0)
        d.lua_setfield(self.lua.L, LUA_GLOBALSINDEX, b"py_png")
        self.run((HERE / "sim.lua").read_text(encoding="utf-8"), "sim.lua")
        self.run(f"Sim.resourceDir = {to_lua(str(self.res_dir).replace(chr(92), '/'))}; Sim.token = {to_lua(token)};"
                 f" Sim.W = {int(W)}; Sim.H = {int(H)}; Sim.K = {float(K)!r}")
        for side, files in (("server", ("shared.lua", "server.lua")), ("client", ("shared.lua", "client.lua"))):
            for f in files:
                code = (resource / f).read_text(encoding="utf-8")
                self.run(f"Sim.load(Sim.{side}, {to_lua(code)}, {to_lua(f)})")
        self.run("Sim.startServer()")
        if client:
            self.run("Sim.startClient()")
        self.pump(3)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.httpd.server_address[1]
        self._stop = threading.Event()
        self.pump_ms = pump_ms
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        threading.Thread(target=self._pump_loop, daemon=True).start()

    # Lua access (serialized) -----------------------------------------------------------------

    def run(self, code: str, name: str = "host") -> str | None:
        with self.lock:
            return self.lua.run(code, name)

    def eval(self, expr: str):
        """Value of a Lua expression, through the simulator's JSON encoder."""
        return json.loads(self.run(f"return Sim.encode({expr})"))

    def pump(self, n: int = 1) -> None:
        for _ in range(n):
            self.run("Sim.pump()")

    def _pump_loop(self) -> None:
        while not self._stop.is_set():
            self.pump()
            time.sleep(self.pump_ms / 1000.0)

    def _py_png(self, L) -> int:
        from satk.saap import png as P

        d = self.lua.d
        w, h = int(d.lua_tonumber(L, 1)), int(d.lua_tonumber(L, 2))
        px = bytes(((x * 7 + y * 3) & 0xFF) for y in range(h) for x in range(w) for _ in range(3))
        data = P.encode(w, h, px)
        d.lua_pushlstring(L, data, len(data))
        return 1

    def rpc_text(self, req: dict) -> str:
        return self.run(f"return Sim.http({to_lua(req)})")

    def _handler(self):
        host = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                if self.path != "/satk-agent/call/rpc":
                    out = b"error: not found"
                else:
                    try:
                        args = json.loads(body.decode("utf-8"))
                        out = host.rpc_text(args[0]).encode("utf-8")
                    except Exception as e:  # noqa: BLE001 - the harness answers like MTA: a 200 with text
                        out = f"error: {e}".encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        return H

    def close(self) -> None:
        self._stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        time.sleep(0.02)
        with self.lock:
            self.lua.close()
