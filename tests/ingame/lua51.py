"""Lua 5.1 for the satk-testdrive tests: the MTA fork's own ``Bin/server/x64/lua5.1.dll`` through ctypes.

No Lua is installed on the machine; the DLL is the one the MTA server runs scripts with, so a script
that compiles and runs here compiles in MTA. Tests using it are marked ``engine`` (skipped without
the fork's server build).
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


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
            d.lua_close.argtypes = [ctypes.c_void_p]
            Lua._dll = d
        self.d = Lua._dll
        self.L = self.d.luaL_newstate()
        self.d.luaL_openlibs(self.L)

    def _top(self) -> str | None:
        n = ctypes.c_size_t()
        p = self.d.lua_tolstring(self.L, -1, ctypes.byref(n))
        return ctypes.string_at(p, n.value).decode("utf-8", errors="replace") if p else None

    def compile(self, code: str, name: str) -> str | None:
        """Syntax check: ``None`` or the error message."""
        data = code.encode("utf-8")
        rc = self.d.luaL_loadbuffer(self.L, data, len(data), ("@" + name).encode())
        msg = self._top() if rc else None
        self.d.lua_settop(self.L, 0)
        return msg

    def run(self, code: str, name: str = "test") -> str | None:
        """Run a chunk; its first result as a string, or ``RuntimeError`` with the Lua error."""
        data = code.encode("utf-8")
        rc = self.d.luaL_loadbuffer(self.L, data, len(data), ("@" + name).encode())
        if rc == 0:
            rc = self.d.lua_pcall(self.L, 0, 1, 0)
        out = self._top()
        self.d.lua_settop(self.L, 0)
        if rc:
            raise RuntimeError(out)
        return out

    def close(self) -> None:
        if self.L:
            self.d.lua_close(self.L)
            self.L = None
