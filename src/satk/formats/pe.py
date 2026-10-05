"""PE sections of ``gta_sa.exe`` (shared by ``satk.game`` and ``satk.re``), SPEC §4.2. Stdlib only.

Implemented early (F1) because WP-01/WP-09 need it; the contract is the F2 one.

Example::

    secs = pe_sections(exe_bytes)
    off = va_to_off(secs, 0x53BF09)       # file offset of CGame::Process+0x29
"""

from __future__ import annotations

import struct

from .rw import FormatError

__all__ = ["pe_sections", "va_to_off", "image_base"]

_SEC = struct.Struct("<8sIIII")  # name, vsize, va, raw_size, raw_off


def _nt(buf) -> tuple[int, int, int]:
    """(nt header offset, number of sections, optional header size)."""
    n = len(buf)
    if n < 0x40 or bytes(buf[:2]) != b"MZ":
        raise FormatError("pe", 0, "not an MZ executable")
    (e_lfanew,) = struct.unpack_from("<I", buf, 0x3C)
    if e_lfanew + 24 > n or bytes(buf[e_lfanew:e_lfanew + 4]) != b"PE\0\0":
        raise FormatError("pe", 0x3C, "PE signature not found")
    nsec, = struct.unpack_from("<H", buf, e_lfanew + 6)
    opt_size, = struct.unpack_from("<H", buf, e_lfanew + 20)
    return e_lfanew, nsec, opt_size


def image_base(buf) -> int:
    """``OptionalHeader.ImageBase`` (0x400000 for ``gta_sa.exe``)."""
    nt, _nsec, opt_size = _nt(buf)
    opt = nt + 24
    if opt + 2 > len(buf):
        raise FormatError("pe", opt, "optional header truncated")
    (magic,) = struct.unpack_from("<H", buf, opt)
    if magic == 0x10B:  # PE32
        if opt + 32 > len(buf):
            raise FormatError("pe", opt, "optional header truncated")
        return struct.unpack_from("<I", buf, opt + 28)[0]
    if magic == 0x20B:  # PE32+
        if opt + 32 > len(buf):
            raise FormatError("pe", opt, "optional header truncated")
        return struct.unpack_from("<Q", buf, opt + 24)[0]
    raise FormatError("pe", opt, f"unknown optional header magic 0x{magic:X}")


def pe_sections(buf) -> list[tuple[str, int, int, int, int]]:
    """``[(name, va, vsize, raw_off, raw_size)]``; ``va`` is relative to the image base."""
    nt, nsec, opt_size = _nt(buf)
    tbl = nt + 24 + opt_size
    if nsec > 96 or tbl + nsec * 40 > len(buf):
        raise FormatError("pe", tbl, f"section table ({nsec} entries) outside the file")
    out = []
    for i in range(nsec):
        name, vsize, va, raw_size, raw_off = _SEC.unpack_from(buf, tbl + i * 40)
        out.append((name.split(b"\0", 1)[0].decode("latin-1"), va, vsize, raw_off, raw_size))
    return out


def va_to_off(sections, va: int, image_base: int = 0x400000) -> int | None:  # noqa: A002
    """File offset of an absolute virtual address, ``None`` if it is not backed by file data."""
    rva = va - image_base
    for _name, sva, vsize, raw_off, raw_size in sections:
        if sva <= rva < sva + max(vsize, raw_size):
            delta = rva - sva
            return raw_off + delta if delta < raw_size else None
    return None
