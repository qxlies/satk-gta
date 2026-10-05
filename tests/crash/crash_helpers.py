"""Helpers for satk.crash tests (M2-07): synthetic x86 minidumps for the synthetic sa-re world.

Importable from test modules (``tests/crash`` is put on ``sys.path`` by ``conftest.py``).
"""

from __future__ import annotations

import struct
from pathlib import Path

from satk.crash.synth import DumpBuilder, mta_section

GTA_TS = 0x427101CA          # TimeDateStamp written by re_synth.make_pe
GTA_SIZE = 0x6000            # SizeOfImage of the synthetic exe
CORE = 0x10000000
STACK = 0x0019F000


def gta_dump(exe: Path | None, *, ip: int = 0x401301, words: dict[int, int] | None = None, ebp: int = 0,
             code: dict[int, bytes] | None = None, sections: bool = False, code_fault: int = 0xC0000005,
             params: tuple = (0, 0x10), user: dict[int, str] | None = None) -> bytes:
    """An x86 dump: gta_sa.exe (the synthetic exe's identity) + core.dll, one crashing thread.

    ``words`` = {offset from esp: value}; ``code`` = {va: bytes} captured memory.
    """
    b = DumpBuilder("x86")
    b.add_module(str(exe) if exe else r"C:\x\gta_sa.exe", 0x400000, GTA_SIZE, timestamp=GTA_TS)
    b.add_module(r"C:\nowhere\mta\core.dll", CORE, 0x10000, timestamp=0x11112222, pdb="core.pdb")
    stack = bytearray(0x800)
    esp = STACK + 0x100
    for off, v in (words or {}).items():
        struct.pack_into("<I", stack, 0x100 + off, v)
    regs = {"eip": ip, "esp": esp, "ebp": ebp, "eax": 0, "ecx": 0x1234}
    b.add_thread(77, regs, STACK, bytes(stack), name="main")
    b.add_thread(78, {"eip": CORE + 0x2000, "esp": STACK + 0x700, "ebp": 0}, STACK + 0x600, bytes(0x100))
    for va, data in (code or {}).items():
        b.add_memory(va, data)
    b.set_exception(77, code_fault, ip, list(params))
    for k, v in (user or {}).items():
        b.add_user_stream(k, v)
    data = b.build()
    if sections:
        pools = struct.pack("<ii", 1, 2) + struct.pack("<iii", 1, 140, 140) + struct.pack("<iii", 4, 110, 3)
        data += mta_section("POL", pools) + mta_section("REP", b"line one\nline two\n")
    return data


def write(tmp_path: Path, name: str, data: bytes | str) -> Path:
    p = tmp_path / name
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8", newline="\n")
    else:
        p.write_bytes(data)
    return p


def make_dll(path: Path, base: int, exports: dict[str, int], code: dict[int, bytes]) -> tuple[int, int]:
    """A PE32 DLL with an export table (``exports`` = {name: rva}); returns ``(timestamp, size_of_image)``."""
    text = bytearray(b"\x90" * 0x400)
    for rva, b in code.items():
        text[rva - 0x1000:rva - 0x1000 + len(b)] = b
    names = sorted(exports)
    n = len(names)
    edata = bytearray(40)
    funcs_rva = 0x2000 + 40
    names_rva = funcs_rva + 4 * n
    ords_rva = names_rva + 4 * n
    strs_rva = ords_rva + 2 * n
    strs = b""
    name_ptrs = []
    for nm in names:
        name_ptrs.append(strs_rva + len(strs))
        strs += nm.encode() + b"\0"
    struct.pack_into("<IIHHIIIIIII", edata, 0, 0, 0, 0, 0, 0, 1, n, n, funcs_rva, names_rva, ords_rva)
    edata += b"".join(struct.pack("<I", exports[nm]) for nm in names)
    edata += b"".join(struct.pack("<I", p) for p in name_ptrs)
    edata += b"".join(struct.pack("<H", i) for i in range(n))
    edata += strs
    size = ((0x2000 + len(edata)) + 0xFFF) & ~0xFFF
    ts = 0x5A5A0001
    hdr = bytearray(0x400)
    hdr[0:2] = b"MZ"
    struct.pack_into("<I", hdr, 0x3C, 0x80)
    hdr[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", hdr, 0x84, 0x14C, 2, ts, 0, 0, 224, 0x2102)
    opt = 0x98
    struct.pack_into("<H", hdr, opt, 0x10B)
    struct.pack_into("<I", hdr, opt + 28, base)
    struct.pack_into("<II", hdr, opt + 32, 0x1000, 0x200)
    struct.pack_into("<II", hdr, opt + 56, size, 0x400)
    struct.pack_into("<I", hdr, opt + 92, 16)
    struct.pack_into("<II", hdr, opt + 96, 0x2000, len(edata))
    secs = [(b".text", 0x1000, bytes(text), 0x60000020), (b".edata", 0x2000, bytes(edata), 0x40000040)]
    off, blobs = 0x400, b""
    for i, (name, rva, data, flags) in enumerate(secs):
        rsize = (len(data) + 0x1FF) & ~0x1FF
        struct.pack_into("<8sIIII12xI", hdr, opt + 224 + 40 * i, name, len(data), rva, rsize, off, flags)
        blobs += data.ljust(rsize, b"\0")
        off += rsize
    path.write_bytes(bytes(hdr) + blobs)
    return ts, size
