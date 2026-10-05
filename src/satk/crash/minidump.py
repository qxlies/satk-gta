"""Minidump (``MDMP``) reader: streams, modules, threads, exception, memory. Standard library only.

Layout facts follow the public ``minidumpapiset.h`` structures (all little-endian, packed to 4):
header 32 bytes, directory entries 12, ``MINIDUMP_MODULE`` 108, ``MINIDUMP_THREAD`` 48,
``MINIDUMP_EXCEPTION_STREAM`` 168, ``MINIDUMP_SYSTEM_INFO`` 56; x86 ``CONTEXT`` is 716 bytes, AMD64 1232.

:class:`Minidump` accepts ``bytes`` or opens a file through ``mmap`` (full dumps can be gigabytes;
nothing is read into memory up front). A damaged header raises :class:`DumpError`; a damaged
stream is skipped and reported in :attr:`Minidump.warnings` (``DUMP_DAMAGED: ...``), so a partly
broken dump still yields everything that is readable.
"""

from __future__ import annotations

import bisect
import mmap
import os
import struct
from dataclasses import dataclass, field

__all__ = ["DumpError", "Module", "Context", "Thread", "ExceptionInfo", "SystemInfo", "Minidump",
           "STREAM_NAMES", "MDMP_SIGNATURE", "is_minidump"]

MDMP_SIGNATURE = b"MDMP"

#: ``MINIDUMP_STREAM_TYPE`` names (0x10000+ are application streams; MTA uses 0x10000..0x10003).
STREAM_NAMES = {
    0: "Unused", 3: "ThreadList", 4: "ModuleList", 5: "MemoryList", 6: "Exception", 7: "SystemInfo",
    8: "ThreadExList", 9: "Memory64List", 10: "CommentA", 11: "CommentW", 12: "HandleData",
    13: "FunctionTable", 14: "UnloadedModuleList", 15: "MiscInfo", 16: "MemoryInfoList",
    17: "ThreadInfoList", 18: "HandleOperationList", 19: "Token", 20: "JavaScriptData",
    21: "SystemMemoryInfo", 22: "ProcessVmCounters", 23: "IptTrace", 24: "ThreadNames",
}

_ARCH = {0: "x86", 9: "amd64", 5: "arm", 6: "ia64", 12: "arm64"}
_MAX_COUNT = 1 << 20          # sanity cap for counts read from the file
_MAX_STRING = 1 << 16         # bytes of a MINIDUMP_STRING


class DumpError(ValueError):
    """The file is not a readable minidump (bad signature, truncated header or directory)."""

    def __init__(self, msg: str, offset: int | None = None):
        super().__init__(msg)
        self.offset = offset


@dataclass(frozen=True, slots=True)
class Module:
    base: int
    size: int
    path: str
    timestamp: int = 0
    checksum: int = 0
    version: str | None = None
    pdb: str | None = None          # CodeView PDB file name (RSDS/NB10)
    pdb_id: str | None = None       # GUID+age (symstore key) for RSDS

    @property
    def name(self) -> str:
        """Base name (``gta_sa.exe``)."""
        return self.path.replace("/", "\\").rsplit("\\", 1)[-1]

    @property
    def end(self) -> int:
        return self.base + self.size

    def contains(self, va: int) -> bool:
        return self.base <= va < self.base + self.size


@dataclass(slots=True)
class Context:
    """Registers of one thread (only what the analysis needs plus the general registers)."""

    arch: str                      # "x86" | "amd64"
    ip: int
    sp: int
    fp: int
    regs: dict[str, int] = field(default_factory=dict)
    flags: int = 0


@dataclass(slots=True)
class Thread:
    tid: int
    suspend: int = 0
    priority: int = 0
    teb: int = 0
    stack_start: int = 0
    stack_size: int = 0
    context: Context | None = None
    name: str | None = None


@dataclass(slots=True)
class ExceptionInfo:
    tid: int
    code: int
    flags: int
    address: int
    params: list[int]
    context: Context | None = None


@dataclass(slots=True)
class SystemInfo:
    arch: str
    cpus: int
    os: str                        # "10.0.26100"
    product_type: int = 0


def is_minidump(head: bytes) -> bool:
    return bytes(head[:4]) == MDMP_SIGNATURE


class Minidump:
    """Parsed minidump. Use as a context manager when opened from a path (closes the mmap)."""

    def __init__(self, data, *, path: str | None = None):
        self._mm: mmap.mmap | None = None
        self._fh = None
        self.path = path
        self.data = data
        self.size = len(data)
        self.warnings: list[str] = []
        self.streams: list[tuple[int, int, int]] = []        # (type, size, rva)
        self.modules: list[Module] = []
        self.unloaded: list[Module] = []
        self.threads: list[Thread] = []
        self.exception: ExceptionInfo | None = None
        self.sysinfo: SystemInfo | None = None
        self.pid: int | None = None
        self.process_created: int | None = None
        self.comments: list[str] = []
        self.user_streams: dict[int, bytes] = {}
        self._mem_starts: list[int] = []
        self._mem: list[tuple[int, int, int]] = []             # (start, size, file offset), sorted
        self.end = 0                                            # end of the last structure read
        self._mod_starts: list[int] = []
        self._parse()

    # -- construction --------------------------------------------------------------------

    @classmethod
    def open(cls, path: str | os.PathLike) -> "Minidump":
        """Map ``path`` read-only and parse it (``DumpError`` for an empty/invalid file)."""
        from ..core.paths import open_ro

        fh = open_ro(path)
        try:
            if os.fstat(fh.fileno()).st_size == 0:
                raise DumpError("empty file", 0)
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        except BaseException:
            fh.close()
            raise
        try:
            d = cls(mm, path=os.fspath(path))
        except BaseException:
            mm.close()
            fh.close()
            raise
        d._mm, d._fh = mm, fh
        return d

    def close(self) -> None:
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "Minidump":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- low-level reads -----------------------------------------------------------------

    def _need(self, off: int, n: int, what: str) -> None:
        if off < 0 or n < 0 or off + n > self.size:
            raise DumpError(f"{what} at 0x{off:x} (+{n}) is outside the file ({self.size} bytes)", off)
        self.end = max(self.end, off + n)

    def _unpack(self, fmt: str, off: int, what: str) -> tuple:
        n = struct.calcsize(fmt)
        self._need(off, n, what)
        return struct.unpack_from(fmt, self.data, off)

    def blob(self, rva: int, size: int, what: str = "data") -> bytes:
        self._need(rva, size, what)
        return bytes(self.data[rva:rva + size])

    def _string(self, rva: int) -> str:
        if rva == 0:
            return ""
        (n,) = self._unpack("<I", rva, "string length")
        if n > _MAX_STRING or n % 2:
            raise DumpError(f"bad string length {n} at 0x{rva:x}", rva)
        return self.blob(rva + 4, n, "string").decode("utf-16-le", "replace")

    # -- parsing -------------------------------------------------------------------------

    def _parse(self) -> None:
        if self.size < 32:
            raise DumpError(f"file too small for a minidump header ({self.size} bytes)", 0)
        sig = bytes(self.data[:4])
        if sig != MDMP_SIGNATURE:
            if sig in (b"PAGE",):
                raise DumpError("kernel/full-system dump (PAGEDUMP), not a user-mode minidump", 0)
            raise DumpError(f"not a minidump (signature {sig!r}, expected b'MDMP')", 0)
        _sig, ver, nstreams, dir_rva, _chk, ts, flags = struct.unpack_from("<IIIIIIQ", self.data, 0)
        self.version = ver & 0xFFFF
        self.timestamp = ts
        self.flags = flags
        if nstreams > 4096:
            raise DumpError(f"implausible stream count {nstreams}", 8)
        self._need(dir_rva, nstreams * 12, "stream directory")
        for i in range(nstreams):
            st, size, rva = struct.unpack_from("<III", self.data, dir_rva + 12 * i)
            self.streams.append((st, size, rva))
        handlers = {3: self._threads, 8: self._threads_ex, 4: self._modules, 5: self._memory, 9: self._memory64,
                    6: self._exception, 7: self._sysinfo, 10: self._comment_a, 11: self._comment_w,
                    14: self._unloaded, 15: self._misc, 24: self._thread_names}
        order = {7: 0, 4: 1, 5: 2, 9: 2, 3: 3, 8: 3, 24: 4}     # sysinfo first: it decides the CONTEXT layout
        for st, size, rva in sorted(self.streams, key=lambda s: order.get(s[0], 9)):
            if st == 0 and size == 0:
                continue
            try:
                h = handlers.get(st)
                if h is not None:
                    h(size, rva)
                elif st > 0xFFFF:
                    self.user_streams[st] = self.blob(rva, size, f"user stream 0x{st:x}")
                else:
                    self._need(rva, size, STREAM_NAMES.get(st, f"stream {st}"))
            except (DumpError, struct.error, UnicodeDecodeError) as e:
                self.warnings.append(f"DUMP_DAMAGED: {STREAM_NAMES.get(st, hex(st))} stream skipped: {e}")
        self._mod_starts = [m.base for m in self.modules]
        if self.exception is not None and self.exception.context is None:
            t = self.thread(self.exception.tid)
            if t is not None:
                self.exception.context = t.context

    @property
    def arch(self) -> str:
        if self.sysinfo is not None:
            return self.sysinfo.arch
        ctx = self.exception.context if self.exception else None
        ctx = ctx or next((t.context for t in self.threads if t.context), None)
        return ctx.arch if ctx else "unknown"

    @property
    def ptr_size(self) -> int:
        return 8 if self.arch in ("amd64", "arm64", "ia64") else 4

    def _count(self, n: int, what: str) -> int:
        if n > _MAX_COUNT:
            raise DumpError(f"implausible {what} count {n}")
        return n

    def _modules(self, size: int, rva: int) -> None:
        (n,) = self._unpack("<I", rva, "module count")
        self._count(n, "module")
        self._need(rva + 4, n * 108, "module list")
        out = []
        for i in range(n):
            o = rva + 4 + 108 * i
            base, msize, chk, ts, name_rva = struct.unpack_from("<QIIII", self.data, o)
            vi = struct.unpack_from("<13I", self.data, o + 24)
            cv_size, cv_rva = struct.unpack_from("<II", self.data, o + 76)
            try:
                path = self._string(name_rva)
            except DumpError as e:
                self.warnings.append(f"DUMP_DAMAGED: module name at 0x{name_rva:x}: {e}")
                path = f"module_{base:x}"
            version = None
            if vi[0] == 0xFEEF04BD:
                version = f"{vi[2] >> 16}.{vi[2] & 0xFFFF}.{vi[3] >> 16}.{vi[3] & 0xFFFF}"
            pdb, pdb_id = None, None
            if cv_size >= 24 and cv_rva:
                try:
                    pdb, pdb_id = _codeview(self.blob(cv_rva, min(cv_size, 1024), "CodeView record"))
                except DumpError:
                    pass
            out.append(Module(base, msize, path, ts, chk, version, pdb, pdb_id))
        out.sort(key=lambda m: m.base)
        self.modules = out

    def _unloaded(self, size: int, rva: int) -> None:
        hdr, entry, n = self._unpack("<III", rva, "unloaded module header")
        self._count(n, "unloaded module")
        if entry < 24 or hdr < 12:
            raise DumpError(f"bad unloaded module entry size {entry}")
        out = []
        for i in range(n):
            o = rva + hdr + entry * i
            base, msize, chk, ts, name_rva = self._unpack("<QIIII", o, "unloaded module")
            out.append(Module(base, msize, self._string(name_rva), ts, chk))
        self.unloaded = out

    def _context(self, size: int, rva: int) -> Context | None:
        if size == 0 or rva == 0:
            return None
        arch = self.sysinfo.arch if self.sysinfo else ("amd64" if size >= 1232 else "x86")
        if arch == "x86":
            if size < 0xCC:
                raise DumpError(f"x86 CONTEXT too small ({size} bytes)", rva)
            _gs, _fs, _es, _ds, edi, esi, ebx, edx, ecx, eax, ebp, eip, _cs, efl, esp, _ss = \
                self._unpack("<16I", rva + 0x8C, "x86 CONTEXT")
            regs = {"eax": eax, "ebx": ebx, "ecx": ecx, "edx": edx, "esi": esi, "edi": edi, "ebp": ebp,
                    "esp": esp, "eip": eip}
            return Context("x86", eip, esp, ebp, regs, efl)
        if arch == "amd64":
            if size < 0x100:
                raise DumpError(f"AMD64 CONTEXT too small ({size} bytes)", rva)
            (efl,) = self._unpack("<I", rva + 0x44, "AMD64 CONTEXT")
            g = self._unpack("<17Q", rva + 0x78, "AMD64 CONTEXT")
            names = ("rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi", "r8", "r9", "r10", "r11", "r12",
                     "r13", "r14", "r15", "rip")
            regs = dict(zip(names, g))
            return Context("amd64", regs["rip"], regs["rsp"], regs["rbp"], regs, efl)
        return None

    def _thread_list(self, size: int, rva: int, entry: int) -> None:
        (n,) = self._unpack("<I", rva, "thread count")
        self._count(n, "thread")
        self._need(rva + 4, n * entry, "thread list")
        out = []
        for i in range(n):
            o = rva + 4 + entry * i
            tid, sus, _pcls, prio, teb, st_start, st_size, _st_rva, ctx_size, ctx_rva = \
                struct.unpack_from("<IIIIQQIIII", self.data, o)
            t = Thread(tid, sus, prio, teb, st_start, st_size)
            try:
                t.context = self._context(ctx_size, ctx_rva)
            except DumpError as e:
                self.warnings.append(f"DUMP_DAMAGED: context of thread {tid}: {e}")
            out.append(t)
        self.threads = out

    def _threads(self, size: int, rva: int) -> None:
        self._thread_list(size, rva, 48)

    def _threads_ex(self, size: int, rva: int) -> None:
        if not self.threads:
            self._thread_list(size, rva, 64)

    def _thread_names(self, size: int, rva: int) -> None:
        (n,) = self._unpack("<I", rva, "thread name count")
        self._count(n, "thread name")
        by_tid = {t.tid: t for t in self.threads}
        for i in range(n):
            tid, name_rva = self._unpack("<IQ", rva + 4 + 12 * i, "thread name")
            if tid in by_tid and name_rva < self.size:
                by_tid[tid].name = self._string(name_rva) or None

    def _add_ranges(self, ranges: list[tuple[int, int, int]]) -> None:
        merged = sorted(self._mem + ranges)
        self._mem = merged
        self._mem_starts = [r[0] for r in merged]

    def _memory(self, size: int, rva: int) -> None:
        (n,) = self._unpack("<I", rva, "memory range count")
        self._count(n, "memory range")
        self._need(rva + 4, n * 16, "memory list")
        out = []
        for i in range(n):
            start, dsize, drva = struct.unpack_from("<QII", self.data, rva + 4 + 16 * i)
            if drva + dsize > self.size:
                self.warnings.append(f"DUMP_DAMAGED: memory range 0x{start:x} (+{dsize}) is outside the file")
                continue
            out.append((start, dsize, drva))
        self._add_ranges(out)

    def _memory64(self, size: int, rva: int) -> None:
        n, base_rva = self._unpack("<QQ", rva, "memory64 header")
        self._count(n, "memory64 range")
        self._need(rva + 16, n * 16, "memory64 list")
        out, off = [], base_rva
        for i in range(n):
            start, dsize = struct.unpack_from("<QQ", self.data, rva + 16 + 16 * i)
            if off + dsize > self.size:
                self.warnings.append(f"DUMP_DAMAGED: memory64 data ends outside the file at range {i}")
                break
            out.append((start, dsize, off))
            off += dsize
        self.end = max(self.end, off)
        self._add_ranges(out)

    def _exception(self, size: int, rva: int) -> None:
        tid, _al, code, eflags, _rec, addr, nparams, _un = self._unpack("<IIIIQQII", rva, "exception stream")
        params = list(self._unpack("<15Q", rva + 40, "exception parameters"))[:min(nparams, 15)]
        ctx_size, ctx_rva = self._unpack("<II", rva + 160, "exception context")
        ctx = None
        try:
            ctx = self._context(ctx_size, ctx_rva)
        except DumpError as e:
            self.warnings.append(f"DUMP_DAMAGED: exception context: {e}")
        self.exception = ExceptionInfo(tid, code, eflags, addr, params, ctx)

    def _sysinfo(self, size: int, rva: int) -> None:
        arch, _lvl, _rev, ncpu, ptype, major, minor, build = self._unpack("<HHHBBIII", rva, "system info")
        self.sysinfo = SystemInfo(_ARCH.get(arch, f"arch{arch}"), ncpu, f"{major}.{minor}.{build}", ptype)

    def _misc(self, size: int, rva: int) -> None:
        if size < 24:
            raise DumpError(f"misc info too small ({size} bytes)", rva)
        _sz, flags1, pid, created = self._unpack("<IIII", rva, "misc info")
        if flags1 & 1:
            self.pid = pid
        if flags1 & 2:
            self.process_created = created

    def _comment_a(self, size: int, rva: int) -> None:
        self.comments.append(self.blob(rva, size, "comment").split(b"\0", 1)[0].decode("utf-8", "replace"))

    def _comment_w(self, size: int, rva: int) -> None:
        raw = self.blob(rva, size - size % 2, "comment")
        self.comments.append(raw.decode("utf-16-le", "replace").split("\0", 1)[0])

    # -- queries -------------------------------------------------------------------------

    def thread(self, tid: int) -> Thread | None:
        return next((t for t in self.threads if t.tid == tid), None)

    def module_at(self, va: int) -> Module | None:
        i = bisect.bisect_right(self._mod_starts, va) - 1
        if i >= 0 and self.modules[i].contains(va):
            return self.modules[i]
        return None

    def module_named(self, name: str) -> Module | None:
        n = name.lower()
        return next((m for m in self.modules if m.name.lower() == n), None)

    @property
    def memory_ranges(self) -> list[tuple[int, int, int]]:
        return list(self._mem)

    def read(self, va: int, n: int) -> bytes | None:
        """``n`` bytes of process memory at ``va`` (adjacent ranges are joined); ``None`` if not captured."""
        if n <= 0:
            return b""
        out = bytearray()
        cur = va
        while len(out) < n:
            i = bisect.bisect_right(self._mem_starts, cur) - 1
            if i < 0:
                return None
            start, size, off = self._mem[i]
            if not (start <= cur < start + size):
                return None
            take = min(n - len(out), start + size - cur)
            out += self.data[off + (cur - start):off + (cur - start) + take]
            cur += take
        return bytes(out)

    def stack_bytes(self, t: Thread, sp: int | None = None) -> tuple[int, bytes]:
        """``(start va, bytes)`` of the thread's captured stack from ``sp`` (or its start) upwards."""
        lo = t.stack_start
        hi = t.stack_start + t.stack_size
        s = sp if sp is not None and lo <= sp < hi else lo
        data = self.read(s, hi - s) if hi > s else None
        if data is None and sp is not None:
            i = bisect.bisect_right(self._mem_starts, sp) - 1     # stack captured in the memory list only
            if i >= 0:
                start, size, _off = self._mem[i]
                if start <= sp < start + size:
                    return sp, self.read(sp, start + size - sp) or b""
        return s, data or b""


def _codeview(b: bytes) -> tuple[str | None, str | None]:
    """``(pdb name, symstore id)`` of an RSDS or NB10 CodeView record."""
    if b[:4] == b"RSDS" and len(b) >= 24:
        d1, d2, d3 = struct.unpack_from("<IHH", b, 4)
        d4 = b[12:20]
        (age,) = struct.unpack_from("<I", b, 20)
        name = b[24:].split(b"\0", 1)[0].decode("utf-8", "replace")
        guid = f"{d1:08X}{d2:04X}{d3:04X}{d4.hex().upper()}"
        return name.replace("/", "\\").rsplit("\\", 1)[-1] or None, f"{guid}{age:X}"
    if b[:4] == b"NB10" and len(b) >= 16:
        _off, sig, age = struct.unpack_from("<III", b, 4)
        name = b[16:].split(b"\0", 1)[0].decode("utf-8", "replace")
        return name.replace("/", "\\").rsplit("\\", 1)[-1] or None, f"{sig:08X}{age:X}"
    return None, None
