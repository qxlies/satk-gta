"""A tiny Lua 5.1 runtime for tests: the fork's own ``Bin/server/x64/lua5.1.dll`` through ctypes.

Used to compile the satk-agent scripts with the exact Lua MTA uses and to run them against stub
MTA functions (``tests/mta/stubs.lua``). No Lua is installed on the machine; the DLL comes from the
MTA fork build (tests using it are marked ``engine`` and skip without it).
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

LUA_GLOBALSINDEX = -10002


def dll_path() -> Path | None:
    from satk.core import config as _config

    eng = _config.build().paths.get("engine")
    if not eng:
        return None
    p = Path(eng) / "Bin" / "server" / "x64" / "lua5.1.dll"
    return p if p.is_file() else None


class Lua:
    """One ``lua_State`` with the standard libraries."""

    _dll = None

    def __init__(self, path: Path):
        if Lua._dll is None:
            os.add_dll_directory(str(path.parent))
            d = ctypes.CDLL(str(path))
            d.luaL_newstate.restype = ctypes.c_void_p
            d.luaL_openlibs.argtypes = [ctypes.c_void_p]
            d.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
            d.luaL_loadbuffer.restype = ctypes.c_int
            d.lua_pcall.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            d.lua_pcall.restype = ctypes.c_int
            d.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_size_t)]
            d.lua_tolstring.restype = ctypes.c_void_p
            d.lua_settop.argtypes = [ctypes.c_void_p, ctypes.c_int]
            d.lua_gettop.argtypes = [ctypes.c_void_p]
            d.lua_gettop.restype = ctypes.c_int
            d.lua_close.argtypes = [ctypes.c_void_p]
            Lua._dll = d
        self.d = Lua._dll
        self.L = self.d.luaL_newstate()
        self.d.luaL_openlibs(self.L)

    def _top_string(self) -> str | None:
        n = ctypes.c_size_t()
        p = self.d.lua_tolstring(self.L, -1, ctypes.byref(n))
        if not p:
            return None
        return ctypes.string_at(p, n.value).decode("utf-8", errors="replace")

    def compile(self, code: str, name: str) -> str | None:
        """Syntax check: ``None`` or the error message."""
        data = code.encode("utf-8")
        rc = self.d.luaL_loadbuffer(self.L, data, len(data), ("@" + name).encode())
        msg = self._top_string() if rc else None
        self.d.lua_settop(self.L, 0)
        return msg

    def run(self, code: str, name: str = "test") -> str | None:
        """Run a chunk; returns its first result as a string (``tostring``) or raises ``RuntimeError``."""
        data = code.encode("utf-8")
        rc = self.d.luaL_loadbuffer(self.L, data, len(data), ("@" + name).encode())
        if rc == 0:
            rc = self.d.lua_pcall(self.L, 0, 1, 0)
        out = self._top_string()
        self.d.lua_settop(self.L, 0)
        if rc:
            raise RuntimeError(out)
        return out

    def close(self) -> None:
        if self.L:
            self.d.lua_close(self.L)
            self.L = None
