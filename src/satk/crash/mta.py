"""MTA:SA crash artefacts: dump user streams, trailing sections, ``core.log`` text, dump file names.

Formats as written by the MTA client (``Client/core/CCrashDumpWriter.cpp``) and server
(``Server/core/CCrashHandler.cpp``); described here from reading that code, no code copied:

* user streams ``0x10000`` exception summary, ``0x10001`` stack trace, ``0x10002`` registers,
  ``0x10003`` captured C++ exception (NUL-terminated text) and ``CommentStreamA`` additional info;
* trailing sections appended after the minidump, each ``u32 0, u32 magicBegin, u32 size, data,
  u32 size, u32 magicEnd, u32 0``; the magics are MSVC multi-char constants (``'POLs'`` is stored
  as the bytes ``sLOP``). Tags: ``POL`` pools, ``D3D`` device call, ``CAS`` crash-averted stats,
  ``LOG`` event log (binary) and ``logfile.txt`` (text, same tag), ``DXI``, ``MSC``, ``MEM``,
  ``REP`` (``report.log``), ``CAT`` animation task;
* ``core.log`` / ``server_pending_upload.log``: blocks opened by ``** -- Unhandled exception -- **``
  with ``Version/Module/Code/Offset`` lines, registers and a ``Stack trace:`` list.

Binary section payloads use MTA ``CBuffer`` streams: little-endian ints, strings as ``u16 length +
bytes``. Decoders are lenient: an unknown layout falls back to "text" or "binary".
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

__all__ = ["USER_STREAMS", "POOL_NAMES", "Section", "trailing_sections", "decode_section", "LogFrame",
           "LogBlock", "parse_log", "parse_frame_line", "parse_dump_name", "EXCEPTION_NAMES", "exception_name",
           "GTA_MODULES", "GTA_BASE", "GTA_END", "is_gta"]

USER_STREAMS = {0x10000: "summary", 0x10001: "stack", 0x10002: "registers", 0x10003: "captured_exception"}

#: ``ePools`` order of MTA (client ``CPools.h``); the POL section stores (index, capacity, used).
POOL_NAMES = ("building", "ped", "object", "dummy", "vehicle", "col_model", "task", "event", "task_allocator",
              "ped_intelligence", "ped_attractor", "entry_info_node", "node_route", "patrol_route", "point_route",
              "ptr_node_double", "ptr_node_single", "env_map_material", "env_map_atomic", "spec_map_material")

EXCEPTION_NAMES = {
    0xC0000005: "ACCESS_VIOLATION", 0xC0000006: "IN_PAGE_ERROR", 0xC0000008: "INVALID_HANDLE",
    0xC0000017: "NO_MEMORY", 0xC000001D: "ILLEGAL_INSTRUCTION", 0xC0000025: "NONCONTINUABLE_EXCEPTION",
    0xC000008C: "ARRAY_BOUNDS_EXCEEDED", 0xC000008D: "FLT_DENORMAL_OPERAND", 0xC000008E: "FLT_DIVIDE_BY_ZERO",
    0xC000008F: "FLT_INEXACT_RESULT", 0xC0000090: "FLT_INVALID_OPERATION", 0xC0000091: "FLT_OVERFLOW",
    0xC0000092: "FLT_STACK_CHECK", 0xC0000093: "FLT_UNDERFLOW", 0xC0000094: "INT_DIVIDE_BY_ZERO",
    0xC0000095: "INT_OVERFLOW", 0xC0000096: "PRIV_INSTRUCTION", 0xC00000FD: "STACK_OVERFLOW",
    0xC0000194: "POSSIBLE_DEADLOCK", 0xC0000374: "HEAP_CORRUPTION", 0xC0000409: "STACK_BUFFER_OVERRUN",
    0xC0000417: "INVALID_CRUNTIME_PARAMETER", 0xC000041D: "FATAL_USER_CALLBACK_EXCEPTION",
    0xC0000420: "ASSERTION_FAILURE", 0x80000001: "GUARD_PAGE", 0x80000002: "DATATYPE_MISALIGNMENT",
    0x80000003: "BREAKPOINT", 0x80000004: "SINGLE_STEP", 0x40000015: "FATAL_APP_EXIT",
    0xE06D7363: "CPP_EXCEPTION", 0xE03B10C0: "MTA_OUT_OF_MEMORY", 0xE0000001: "MTA_WATCHDOG_TIMEOUT",
}

#: Names the GTA executable has inside MTA (the patched copy, older proxies) and in tools (cdb: no extension).
GTA_MODULES = frozenset({"gta_sa.exe", "gta_sa_mta.exe", "gta-sa.exe", "proxy_sa.exe", "gta_sa"})
GTA_BASE = 0x400000
GTA_END = 0x1577000


def is_gta(module: str | None) -> bool:
    return bool(module) and module.lower() in GTA_MODULES


def exception_name(code: int | None) -> str | None:
    if code is None:
        return None
    return EXCEPTION_NAMES.get(code & 0xFFFFFFFF)


# --------------------------------------------------------------------------- trailing sections


@dataclass(slots=True)
class Section:
    tag: str            # "POL", "LOG", ...
    offset: int         # file offset of the payload
    size: int
    data: bytes


_TAG = re.compile(rb"^[A-Za-z0-9]{3}$")


def trailing_sections(data, *, limit: int = 64) -> tuple[list[Section], int]:
    """MTA sections appended after the minidump, in file order; and the offset where they start.

    Parsed backwards from the end of the file, so the minidump size need not be known. Stops at the
    first footer that does not match (a dump without sections returns ``([], len(data))``).
    """
    end = len(data)
    out: list[Section] = []
    while len(out) < limit and end >= 24:
        size, magic_end, zero = struct.unpack_from("<III", data, end - 12)
        if zero != 0 or size > end - 24:
            break
        start = end - 12 - size
        zero0, magic_begin, size0 = struct.unpack_from("<III", data, start - 12)
        if zero0 != 0 or size0 != size:
            break
        b = struct.pack(">I", magic_begin)       # 'POLs' as written by MSVC -> b"POLs"
        e = struct.pack(">I", magic_end)
        if not (b[3:] == b"s" and e[3:] == b"e" and b[:3] == e[:3] and _TAG.match(b[:3])):
            break
        out.append(Section(b[:3].decode("ascii"), start, size, bytes(data[start:start + size])))
        end = start - 12
    out.reverse()
    return out, end


class _Reader:
    def __init__(self, b: bytes):
        self.b, self.o = b, 0

    def i32(self) -> int:
        (v,) = struct.unpack_from("<i", self.b, self.o)
        self.o += 4
        return v

    def u32(self) -> int:
        (v,) = struct.unpack_from("<I", self.b, self.o)
        self.o += 4
        return v

    def u8(self) -> int:
        v = self.b[self.o]
        self.o += 1
        return v

    def string(self) -> str:
        (n,) = struct.unpack_from("<H", self.b, self.o)
        self.o += 2
        if self.o + n > len(self.b):
            raise ValueError("string beyond section")
        s = self.b[self.o:self.o + n].decode("utf-8", "replace")
        self.o += n
        return s


def _text(b: bytes) -> str:
    return b.replace(b"\0", b"").decode("utf-8", "replace")


def _looks_text(b: bytes) -> bool:
    if not b:
        return False
    sample = b[:4096]
    printable = sum(1 for c in sample if c in (9, 10, 13) or 32 <= c < 127 or c >= 0x80)
    return printable >= 0.95 * len(sample)


def decode_section(sec: Section) -> dict:
    """Decoded payload: ``{"kind": "pools"|"log"|"text"|"misc"|"memory"|"crash_averted"|"binary", ...}``."""
    try:
        if sec.tag == "POL":
            return _pools(sec.data)
        if sec.tag == "LOG" and not _looks_text(sec.data[:8]):
            return _log_events(sec.data)
        if sec.tag == "CAS":
            r = _Reader(sec.data)
            ver, n = r.i32(), r.u32()
            rows = [[r.u32(), r.u32(), r.u32()] for _ in range(min(n, 1000))]   # age ms, id, count
            return {"kind": "crash_averted", "version": ver, "cols": ["age_ms", "id", "count"], "rows": rows}
        if sec.tag == "MSC":
            r = _Reader(sec.data)
            ver = r.i32()
            a, b = r.u8(), r.u8()
            return {"kind": "misc", "version": ver, "bytes_748add": f"{a:02x} {b:02x}", "crash_zone": r.u32()}
        if sec.tag == "MEM":
            return _memory(sec.data)
        if sec.tag == "CAT":
            r = _Reader(sec.data)
            r.i32()
            parts = []
            while r.o + 2 <= len(sec.data):
                parts.append(r.string())
            return {"kind": "text", "text": "".join(parts)}
    except (struct.error, ValueError, IndexError):
        pass
    if _looks_text(sec.data):
        return {"kind": "text", "text": _text(sec.data)}
    return {"kind": "binary", "size": sec.size}


def _pools(b: bytes) -> dict:
    r = _Reader(b)
    ver, n = r.i32(), r.i32()
    if not (0 <= n <= 256):
        raise ValueError("bad pool count")
    rows = []
    for _ in range(n):
        idx, cap, used = r.i32(), r.i32(), r.i32()
        name = POOL_NAMES[idx] if 0 <= idx < len(POOL_NAMES) else f"pool{idx}"
        rows.append([name, cap, used if used >= 0 else None])
    return {"kind": "pools", "version": ver, "cols": ["pool", "capacity", "used"], "rows": rows}


def _log_events(b: bytes) -> dict:
    r = _Reader(b)
    ver, n = r.i32(), r.u32()
    if n > 100000:
        raise ValueError("bad log count")
    rows = []
    for _ in range(n):
        age = r.u32()
        rows.append([age, r.string(), r.string(), r.string()])
    return {"kind": "log", "version": ver, "cols": ["age_ms", "type", "context", "body"], "rows": rows}


def _memory(b: bytes) -> dict:
    r = _Reader(b)
    ver = r.i32()
    n = r.i32()
    if not (0 <= n <= 64):
        raise ValueError("bad d3d memory count")
    names = ("static_vb", "dynamic_vb", "static_ib", "dynamic_ib", "static_tex", "dynamic_tex")
    d3d = {}
    for i in range(n):
        locked, created, destroyed, cur, cur_bytes = (r.i32() for _ in range(5))
        d3d[names[i] if i < len(names) else f"res{i}"] = {"count": cur, "bytes": cur_bytes}
    out = {"kind": "memory", "version": ver, "d3d": d3d}
    m = r.i32()
    if m >= 3:
        out.update(process_kb=r.i32(), streaming_used=r.i32(), streaming_available=r.i32())
    return out


# --------------------------------------------------------------------------- text logs


@dataclass(slots=True)
class LogFrame:
    raw: str
    module: str | None          # base name, "gta_sa.exe" for the game, None if unknown
    off: int | None             # module-relative offset
    va: int | None              # absolute address when the line has one ([0x...] or a gta_sa.exe offset)
    symbol: str | None = None   # symbol printed by MTA (only when it had PDBs)


@dataclass(slots=True)
class LogBlock:
    """One crash report of a ``core.log``-style file."""

    fields: dict[str, str] = field(default_factory=dict)
    regs: dict[str, int] = field(default_factory=dict)
    frames: list[LogFrame] = field(default_factory=list)
    reason: str | None = None
    text: str = ""

    @property
    def code(self) -> int | None:
        for k in ("Code", "Exception Code"):
            v = self.fields.get(k)
            if v:
                try:
                    return int(v.split()[0], 16)
                except ValueError:
                    pass
        return None

    @property
    def module(self) -> str | None:
        for k in ("Module", "Resolved Module", "Resolved Module Path", "Module Path"):
            v = self.fields.get(k)
            if v:
                return v.replace("/", "\\").rsplit("\\", 1)[-1].strip().strip('"') or None
        return None

    @property
    def offset(self) -> int | None:
        for k in ("Offset", "Module Offset", "Resolved RVA", "Resolved Module Offset"):
            v = self.fields.get(k)
            if v:
                try:
                    return int(v.split()[0], 16)
                except ValueError:
                    pass
        return None


_BLOCK = re.compile(r"\*\*\s*--\s*Unhandled exception\s*--\s*\*\*", re.I)
_FIELD = re.compile(r"^\s*(Version|Time|Module|Code|Offset|Exception Code|Exception Address|Description|"
                    r"Exception Type|Exception|Module Path|Module Offset|Resolved Module|Resolved Module Path|"
                    r"Resolved Module Base|Resolved RVA|Resolved Module Offset|Resolved IDA Address|Thread ID|"
                    r"ThreadId|Process ID|CrashZone|Crash dump file|Precise crash time|Fatal Exception)"
                    r"\s*[=:]\s*(.*?)\s*$", re.I)
_REG = re.compile(r"\b(EAX|EBX|ECX|EDX|ESI|EDI|EBP|ESP|EIP|RAX|RBX|RCX|RDX|RSI|RDI|RBP|RSP|RIP|R8|R9|R1[0-5]|"
                  r"FLG|EFLAGS)\s*=\s*(?:0x)?([0-9A-Fa-f]{1,16})\b")
_REASON = re.compile(r"^\s*Reason:\s*(.*)$")
_STACK_HEAD = re.compile(r"^\s*(?:Stack trace|Stack Trace)\b[^:]*:\s*$", re.I)
_FRAME_MODPLUS = re.compile(r"(?<![\w.!])([A-Za-z0-9_\-.]+?)(\.(?:exe|dll|asi|so))?\s*\+\s*(0x[0-9A-Fa-f]+|[0-9A-Fa-f]+)\b")
_FRAME_ABS = re.compile(r"\[(?:0x)?([0-9A-Fa-f]{1,16})\]")
_FRAME_BANG = re.compile(r"(?<![\w.])([A-Za-z0-9_\-.]+?)!([^\s+\[\]()]+)(?:\s*\+\s*(0x[0-9A-Fa-f]+|[0-9A-Fa-f]+))?")
_FRAME_NUM = re.compile(r"^\s*(?:-->\s*)?(?:#\d+\s*[-:]?\s*|\d+\s*[-:]\s+)")
_SYMBOL = re.compile(r"^[A-Za-z_?~][^\s\[(]*")
_HEXWORD = re.compile(r"^[0-9A-Fa-f`]+$")


def _symbol(head: str) -> str | None:
    m = _SYMBOL.match(head.strip())
    if not m or _HEXWORD.match(m.group(0)):
        return None
    return m.group(0)


def _mod_name(stem: str, ext: str | None) -> str:
    name = stem + (ext or "")
    return "gta_sa.exe" if is_gta(name) else name


def parse_frame_line(line: str) -> LogFrame | None:
    """One stack-trace line of MTA, cdb (``gta_sa+0x13bf09``) or a server log (``#0 - ...``)."""
    s = _FRAME_NUM.sub("", line.strip(), count=1)
    s = s.replace("<-- FREEZE POINT", "").strip()
    if not s or s.lower().startswith(("eip=", "esp=", "last known")):
        return None
    va = None
    am = _FRAME_ABS.search(s)
    if am:
        va = int(am.group(1), 16)
    module = off = symbol = None
    mm = None
    for m in _FRAME_MODPLUS.finditer(s):
        if m.group(2) or is_gta(m.group(1)) or "!" not in s[:m.start()]:
            mm = m
            break
    bm = _FRAME_BANG.search(s)
    if mm and not (bm and bm.start() <= mm.start() < bm.end()):
        module, off = _mod_name(mm.group(1), mm.group(2)), int(mm.group(3), 16)
        symbol = _symbol(s[:mm.start()])
    elif bm:
        module = _mod_name(bm.group(1), None)
        symbol = bm.group(2) + (f"+{bm.group(3)}" if bm.group(3) else "")
    else:
        head = s.split("[")[0].strip()
        if re.fullmatch(r"(?:0x)?[0-9A-Fa-f]{1,16}", head):
            va = int(head, 16) if va is None else va
        else:
            symbol = _symbol(head)
    if module is None and va is None:
        return None
    if module == "gta_sa.exe" and off is not None and va is None:
        va = GTA_BASE + off
    if module is None and va is not None and GTA_BASE <= va < GTA_END:
        module, off = "gta_sa.exe", va - GTA_BASE
    return LogFrame(line.strip(), module, off, va, symbol)


def parse_log(text: str) -> list[LogBlock]:
    """Crash blocks of a ``core.log``-like text (a text without block markers is one block)."""
    starts = [m.start() for m in _BLOCK.finditer(text)]
    chunks = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])] if starts else [text]
    return [_block(c) for c in chunks]


def _block(text: str) -> LogBlock:
    blk = LogBlock(text=text)
    in_stack = False
    for line in text.splitlines():
        if _STACK_HEAD.match(line):
            in_stack = True
            continue
        if in_stack:
            st = line.strip()
            if not st or st.startswith(("===", "**")):
                in_stack = False
                continue
            fr = parse_frame_line(line)
            if fr is not None:
                blk.frames.append(fr)
                continue
            in_stack = False                     # not a frame: parse the line as a field below
        rm = _REASON.match(line)
        if rm:
            blk.reason = rm.group(1).strip()
            continue
        fm = _FIELD.match(line)
        if fm:
            key = next(k for k in _FIELD_KEYS if k.lower() == fm.group(1).lower())
            blk.fields.setdefault(key, fm.group(2))
            continue
        for k, v in _REG.findall(line):
            blk.regs.setdefault(k.lower().replace("flg", "eflags"), int(v, 16))
    return blk


_FIELD_KEYS = ("Version", "Time", "Module", "Code", "Offset", "Exception Code", "Exception Address", "Description",
               "Exception Type", "Exception", "Module Path", "Module Offset", "Resolved Module", "Resolved Module Path",
               "Resolved Module Base", "Resolved RVA", "Resolved Module Offset", "Resolved IDA Address", "Thread ID",
               "ThreadId", "Process ID", "CrashZone", "Crash dump file", "Precise crash time", "Fatal Exception")


# --------------------------------------------------------------------------- dump file names

_CLIENT_NAME = re.compile(r"^client_(?P<ver>.+?)_(?P<mod>[A-Za-z0-9]+)_(?P<off>[0-9a-fA-F]{8})_(?P<code>[0-9a-fA-F]{1,4})"
                          r"(?:_(?P<rest>.*))?\.dmp$")
_SERVER_NAME = re.compile(r"^server_(?P<ver>.+?)_(?P<mod>[A-Za-z0-9]+)_(?P<off>[0-9a-fA-F]{8})_(?P<code>[0-9a-fA-F]{1,4})_"
                          r"(?P<date>\d{8})_\d{4}\.dmp$")
_DATE = re.compile(r"_(\d{8})_(\d{4})\.(?:dmp|log)$")


def parse_dump_name(name: str) -> dict:
    """What an MTA dump file name says: ``{side, version, module, offset, code_low16, date}`` (best effort)."""
    out: dict = {}
    for side, rx in (("client", _CLIENT_NAME), ("server", _SERVER_NAME)):
        m = rx.match(name)
        if m:
            out.update(side=side, version=m.group("ver"), module=m.group("mod"), offset=f"0x{int(m.group('off'), 16):x}",
                       code=f"0x{int(m.group('code'), 16):04x}")
            break
    else:
        if name.lower().startswith(("client_", "failfast_")):
            out["side"] = "client"
        elif name.lower().startswith("server_"):
            out["side"] = "server"
    d = _DATE.search(name)
    if d:
        a, b = d.group(1), d.group(2)
        out["date"] = f"{a[:4]}-{a[4:6]}-{a[6:]} {b[:2]}:{b[2:]}"
    return out
