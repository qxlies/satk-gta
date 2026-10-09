"""Local PDB symbols through Windows DbgHelp, loaded only when a frame has a PDB candidate.

The dump's CodeView id (or the local image's for logs) must match the loaded PDB's GUID/signature and age.
PDBs are loaded explicitly: an old absolute path embedded by the linker cannot override a
matching sidecar supplied with --images. No symbol servers, downloads, or module execution.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from functools import cache
from pathlib import Path

from .minidump import Module


def dbghelp_paths() -> list[Path]:
    """DbgHelp of the VS install used by engine, then the Windows system copy, in host bitness."""
    import struct

    from ..engine.common import find_msbuild

    out = []
    msbuild = find_msbuild()
    if msbuild:
        vs = next((p.parent for p in msbuild.parents if p.name.lower() == "msbuild"), None)
        if vs:
            vstest = vs / "Common7/IDE/CommonExtensions/Microsoft/TestWindow/VsTest"
            out.append(vstest / ("x64" if struct.calcsize("P") == 8 else "x86") / "dbghelp.dll")
    windows = Path(os.environ.get("SystemRoot", os.environ.get("WINDIR", r"C:\Windows")))
    out.append(windows / "System32" / "dbghelp.dll")
    return out


def _make_lock():
    # DbgHelp is single threaded, including options shared by all its symbol sessions.
    import threading

    return threading.RLock()


_LOCK = _make_lock()  # module import serializes creation even when two reports start together


@contextmanager
def _options(dll):
    with _LOCK:
        previous = dll.SymGetOptions()
        # LOAD_LINES | UNDNAME | LOAD_ANYTHING, plus local, noninteractive operation.
        dll.SymSetOptions(0x10 | 0x2 | 0x40 | 0x200 | 0x1000 | 0x80000 | 0x2000000)
        try:
            yield
        finally:
            dll.SymSetOptions(previous)


@cache
def _api():
    import ctypes as ct
    from ctypes import wintypes as wt
    from types import SimpleNamespace

    if os.name != "nt":
        raise OSError("PDB symbolization needs Windows DbgHelp")

    class SYMBOL_INFOW(ct.Structure):
        _fields_ = [("SizeOfStruct", wt.ULONG), ("TypeIndex", wt.ULONG), ("Reserved", ct.c_uint64 * 2),
                    ("Index", wt.ULONG), ("Size", wt.ULONG), ("ModBase", ct.c_uint64), ("Flags", wt.ULONG),
                    ("Value", ct.c_uint64), ("Address", ct.c_uint64), ("Register", wt.ULONG),
                    ("Scope", wt.ULONG), ("Tag", wt.ULONG), ("NameLen", wt.ULONG), ("MaxNameLen", wt.ULONG),
                    ("Name", wt.WCHAR * 1)]

    class IMAGEHLP_LINEW64(ct.Structure):
        _fields_ = [("SizeOfStruct", wt.DWORD), ("Key", ct.c_void_p), ("LineNumber", wt.DWORD),
                    ("FileName", wt.LPWSTR), ("Address", ct.c_uint64)]

    class IMAGEHLP_MODULEW64(ct.Structure):
        _fields_ = [("SizeOfStruct", wt.DWORD), ("BaseOfImage", ct.c_uint64), ("ImageSize", wt.DWORD),
                    ("TimeDateStamp", wt.DWORD), ("CheckSum", wt.DWORD), ("NumSyms", wt.DWORD),
                    ("SymType", wt.DWORD), ("ModuleName", wt.WCHAR * 32), ("ImageName", wt.WCHAR * 256),
                    ("LoadedImageName", wt.WCHAR * 256), ("LoadedPdbName", wt.WCHAR * 256),
                    ("CVSig", wt.DWORD), ("CVData", wt.WCHAR * 780), ("PdbSig", wt.DWORD),
                    ("PdbSig70", ct.c_ubyte * 16), ("PdbAge", wt.DWORD), ("PdbUnmatched", wt.BOOL),
                    ("DbgUnmatched", wt.BOOL), ("LineNumbers", wt.BOOL), ("GlobalSymbols", wt.BOOL),
                    ("TypeInfo", wt.BOOL), ("SourceIndexed", wt.BOOL), ("Publics", wt.BOOL),
                    ("MachineType", wt.DWORD), ("Reserved", wt.DWORD)]

    prototypes = {
        "SymGetOptions": (wt.DWORD, []),
        "SymSetOptions": (wt.DWORD, [wt.DWORD]),
        "SymInitializeW": (wt.BOOL, [wt.HANDLE, wt.LPCWSTR, wt.BOOL]),
        "SymCleanup": (wt.BOOL, [wt.HANDLE]),
        "SymLoadModuleExW": (ct.c_uint64, [wt.HANDLE, wt.HANDLE, wt.LPCWSTR, wt.LPCWSTR, ct.c_uint64,
                                          wt.DWORD, ct.c_void_p, wt.DWORD]),
        "SymGetModuleInfoW64": (wt.BOOL, [wt.HANDLE, ct.c_uint64, ct.POINTER(IMAGEHLP_MODULEW64)]),
        "SymFromAddrW": (wt.BOOL, [wt.HANDLE, ct.c_uint64, ct.POINTER(ct.c_uint64), ct.POINTER(SYMBOL_INFOW)]),
        "SymGetLineFromAddrW64": (wt.BOOL, [wt.HANDLE, ct.c_uint64, ct.POINTER(wt.DWORD),
                                          ct.POINTER(IMAGEHLP_LINEW64)]),
    }
    errors = []
    for path in dbghelp_paths():
        try:
            dll = ct.WinDLL(str(path), use_last_error=True)
            for name, (restype, argtypes) in prototypes.items():
                fn = getattr(dll, name)
                fn.restype, fn.argtypes = restype, argtypes
            return SimpleNamespace(dll=dll, symbol=SYMBOL_INFOW, line=IMAGEHLP_LINEW64, module=IMAGEHLP_MODULEW64)
        except (OSError, AttributeError) as e:
            errors.append(f"{path}: {e}")
    raise OSError("cannot load DbgHelp: " + "; ".join(errors))


def _pdb_id(guid: bytes, signature: int, age: int) -> str:
    from uuid import UUID

    prefix = UUID(bytes_le=guid).hex.upper() if any(guid) else f"{signature:08X}"
    return f"{prefix}{age:X}"


class _Session:
    """One explicitly loaded PDB at a private base; no dependence on the crashed process's bitness."""

    def __init__(self, pdb: Path, size: int):
        import ctypes as ct
        from ctypes import wintypes as wt

        self.api = _api()
        self.handle = wt.HANDLE(id(self))  # pointer-sized, unique while this session is alive
        self.opened = False
        self.base = 0
        dll = self.api.dll
        with _options(dll):
            try:
                if not dll.SymInitializeW(self.handle, str(pdb.parent), False):
                    raise OSError(ct.get_last_error(), "SymInitializeW failed")
                self.opened = True
                # Loading the PDB itself requires a nonzero base AND the module's SizeOfImage.
                self.base = dll.SymLoadModuleExW(self.handle, None, str(pdb), None, 0x10000000, size, None, 0)
                if not self.base:
                    raise OSError(ct.get_last_error(), f"SymLoadModuleExW failed for {pdb.name}")
                info = self.api.module()
                info.SizeOfStruct = ct.sizeof(info)
                if not dll.SymGetModuleInfoW64(self.handle, self.base, ct.byref(info)):
                    raise OSError(ct.get_last_error(), f"SymGetModuleInfoW64 failed for {pdb.name}")
                self.pdb_id = _pdb_id(bytes(info.PdbSig70), info.PdbSig, info.PdbAge)
                # Direct PDB loads set PdbUnmatched even for the correct PDB: no image was given to
                # DbgHelp. The caller verifies the actual id against the dump or matching PE instead.
                self.is_pdb = info.SymType in (3, 7) and bool(info.LoadedPdbName)  # SymPdb / SymDia
            except BaseException:
                self.close()
                raise

    def resolve(self, off: int, *, ret: bool = False) -> dict:
        import ctypes as ct
        from ctypes import wintypes as wt

        adjust = int(ret and off > 0)
        addr = self.base + off - adjust
        # SizeOfStruct is sizeof(SYMBOL_INFOW with WCHAR Name[1]), INCLUDING its tail padding.
        # Extending Name in the structure and subtracting characters gives the wrong ABI size.
        max_name = 4096
        buf = ct.create_string_buffer(ct.sizeof(self.api.symbol) + (max_name - 1) * ct.sizeof(wt.WCHAR))
        symbol = ct.cast(buf, ct.POINTER(self.api.symbol)).contents
        symbol.SizeOfStruct = ct.sizeof(self.api.symbol)
        symbol.MaxNameLen = max_name
        displacement = ct.c_uint64()
        out = {}
        with _options(self.api.dll):
            if self.api.dll.SymFromAddrW(self.handle, addr, ct.byref(displacement), ct.byref(symbol)):
                if 0 < symbol.NameLen < max_name:
                    name = ct.wstring_at(ct.addressof(buf) + self.api.symbol.Name.offset, symbol.NameLen)
                    delta = displacement.value + adjust
                    out = {"fn": f"{name}+0x{delta:x}" if delta else name, "via": "pdb"}
            line = self.api.line()
            line.SizeOfStruct = ct.sizeof(line)
            line_disp = wt.DWORD()
            if out and self.api.dll.SymGetLineFromAddrW64(self.handle, addr, ct.byref(line_disp), ct.byref(line)):
                if line.FileName and line.LineNumber:
                    filename = line.FileName.replace("\\", "/")
                    out["src"] = f"{filename}:{line.LineNumber}"
        return out

    def close(self) -> None:
        with _LOCK:
            if self.opened:
                self.api.dll.SymCleanup(self.handle)
                self.opened = False


class PdbSymbols:
    """Cache module sessions for one report; failed/missing symbols leave the normal export fallback intact."""

    def __init__(self, images):
        self.images = images
        self.sessions: dict[Module, _Session | None] = {}

    def _load(self, mod: Module) -> _Session | None:
        pe = self.images.pe(mod)
        if pe is None and not mod.pdb_id:
            return None
        candidates = self.images.pdb_candidates(mod, pe)
        if not candidates:
            return None
        expected = pe.codeview()[1] if pe is not None else None
        if mod.pdb_id and expected and mod.pdb_id.upper() != expected.upper():
            self.images.warn.append(f"REVISION: {mod.name} image PDB id {expected} does not match the dump's "
                                    f"{mod.pdb_id} (GUID/signature + age); symbols ignored")
            return None
        expected = mod.pdb_id or expected
        if not expected:
            self.images.warn.append(f"REVISION: {mod.name} has no CodeView GUID/signature + age to verify its PDB; "
                                    "symbols ignored")
            return None
        for pdb in candidates:
            try:
                session = _Session(pdb, mod.size)
            except OSError as e:
                self.images.warn.append(f"EXTERNAL_TOOL: cannot read PDB {pdb.as_posix()}: {e}")
                continue
            if session.is_pdb and session.pdb_id.upper() == expected.upper():
                return session
            session.close()
            self.images.warn.append(f"REVISION: PDB {pdb.as_posix()} does not match {mod.name} "
                                    f"(GUID/signature + age: expected {expected}, found {session.pdb_id}); "
                                    "symbols ignored")
        return None

    def resolve(self, mod: Module, off: int, *, ret: bool = False) -> dict:
        if not 0 <= off < mod.size:
            return {}
        if mod not in self.sessions:
            self.sessions[mod] = self._load(mod)
        session = self.sessions[mod]
        if session is not None:
            try:
                return session.resolve(off, ret=ret)
            except OSError as e:
                self.images.warn.append(f"EXTERNAL_TOOL: cannot resolve PDB symbols for {mod.name}: {e}")
                session.close()
                self.sessions[mod] = None
        return {}

    def close(self) -> None:
        for session in self.sessions.values():
            if session is not None:
                session.close()
        self.sessions.clear()
