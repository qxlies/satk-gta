"""Synthetic RenderWare bytes for satk.batch tests: a small DFF, a TXD and a VER2 IMG (no game files).

The DFF is one clump with one frame, one atomic and one prelit quad with night colours and one textured
material: ``satk asset lint`` reports nothing on it but the info ``link.dff_orphan`` (no IDE names it).
"""

from __future__ import annotations

import struct

LIB = 0x1803FFFF  # RW 3.6.0.3
SECTOR = 2048


def chunk(t: int, payload: bytes) -> bytes:
    return struct.pack("<III", t, len(payload), LIB) + payload


def rw_string(s: str) -> bytes:
    raw = s.encode("latin-1") + b"\0"
    return chunk(0x02, raw + b"\0" * (-len(raw) % 4))


def material(tex: str = "gm_wall") -> bytes:
    st = struct.pack("<I4BII3f", 0, 255, 255, 255, 255, 0, 1, 1.0, 1.0, 1.0)
    texture = chunk(0x06, chunk(0x01, struct.pack("<HH", 0x1106, 0)) + rw_string(tex) + rw_string("")
                    + chunk(0x03, b""))
    return chunk(0x07, chunk(0x01, st) + texture + chunk(0x03, b""))


def geometry() -> bytes:
    pos = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 1.0)]
    tris = [(0, 1, 2, 0), (2, 1, 3, 0)]
    flags = 0x02 | 0x20 | 0x08 | 0x04  # tristrip-less: positions, lit, prelit, 1 UV set
    st = struct.pack("<IiiI", flags | (1 << 16), len(tris), len(pos), 1)
    st += bytes((120, 110, 100, 255)) * len(pos)
    st += b"".join(struct.pack("<2f", (i % 2) * 1.0, (i // 2) * 1.0) for i in range(len(pos)))
    st += b"".join(struct.pack("<4H", b, a, m, c) for a, b, c, m in tris)
    st += struct.pack("<4fII", 0.5, 0.5, 0.0, 1.0, 1, 0)
    st += b"".join(struct.pack("<3f", *p) for p in pos)
    mats = chunk(0x08, chunk(0x01, struct.pack("<Ii", 1, -1)) + material())
    night = chunk(0x253F2F9, struct.pack("<I", 1) + bytes((40, 40, 60, 255)) * len(pos))
    return chunk(0x0F, chunk(0x01, st) + mats + chunk(0x03, night))


def dff() -> bytes:
    """A lint-clean single-quad clump."""
    body = chunk(0x01, struct.pack("<III", 1, 0, 0))
    frames = struct.pack("<I", 1) + struct.pack("<12fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, -1, 0)
    body += chunk(0x0E, chunk(0x01, frames) + chunk(0x03, chunk(0x253F2FE, b"root")))
    body += chunk(0x1A, chunk(0x01, struct.pack("<I", 1)) + geometry())
    body += chunk(0x14, chunk(0x01, struct.pack("<4I", 0, 0, 5, 0)) + chunk(0x03, b""))
    body += chunk(0x03, b"")
    return chunk(0x10, body)


def broken_dff(kind: int = 0) -> bytes:
    """Three ways to break a DFF: truncated, garbage, a wrong top-level chunk."""
    if kind == 0:
        return dff()[:50]
    if kind == 1:
        return b"definitely not a renderware file"
    return chunk(0x16, b"\0" * 8)


def txd(names: tuple[str, ...] = ("wall", "roof")) -> bytes:
    """A TXD of 8x8 DXT1 textures with full mip chains."""
    natives = b""
    for name in names:
        levels = [bytes(8 * max(1, (s + 3) // 4) ** 2) for s in (8, 4, 2, 1)]
        st = struct.pack("<IBBH32s32sII", 9, 6, 0x11, 0, name.encode(), b"", 0x200, struct.unpack("<I", b"DXT1")[0])
        st += struct.pack("<HHBBBB", 8, 8, 16, len(levels), 4, 8)
        st += b"".join(struct.pack("<I", len(lv)) + lv for lv in levels)
        natives += chunk(0x15, chunk(0x01, st) + chunk(0x03, b""))
    return chunk(0x16, chunk(0x01, struct.pack("<HH", len(names), 2)) + natives + chunk(0x03, b""))


def img(files: list[tuple[str, bytes]]) -> bytes:
    """A VER2 IMG archive with these entries."""
    def pad(d: bytes) -> bytes:
        return d + b"\0" * (-len(d) % SECTOR)

    n = len(files)
    dir_sectors = max(1, -(-(8 + n * 32) // SECTOR))
    head = b"VER2" + struct.pack("<I", n)
    body = b""
    off = dir_sectors
    for name, data in files:
        d = pad(data)
        secs = len(d) // SECTOR
        head += struct.pack("<IHH24s", off, secs, 0, name.encode())
        body += d
        off += secs
    return pad(head) + body


#: Inputs 7, 23 and 41 of the 50 (1-based) of the ``dff_dir`` fixture are broken in three different ways.
BROKEN = (7, 23, 41)
