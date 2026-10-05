"""Fixtures for satk.blender tests (synthetic RW/COL bytes are built here, never stored as files)."""

from __future__ import annotations

import struct

import pytest


def rw_chunk(t: int, payload: bytes, libid: int = 0x1803FFFF) -> bytes:
    """One RenderWare chunk (type, size, library id = 3.6.0.3)."""
    return struct.pack("<III", t, len(payload), libid) + payload


def fake_dff(tag: bytes = b"") -> bytes:
    """A tiny clump-shaped RW blob (enough for ``rw_payload_size``), padded like an IMG entry."""
    body = rw_chunk(0x10, rw_chunk(0x01, struct.pack("<III", 0, 0, 0)) + tag)
    return body + b"\0" * (2048 - len(body) % 2048)


def fake_txd(tag: bytes = b"") -> bytes:
    body = rw_chunk(0x16, rw_chunk(0x01, struct.pack("<HH", 0, 2)) + tag)
    return body + b"\0" * (2048 - len(body) % 2048)


def coll_v1(name: str, model_id: int = 0) -> bytes:
    """A minimal COLL (v1) record: bounds + five empty counted arrays."""
    body = struct.pack("<10f", 1.0, 0, 0, 0, -1, -1, -1, 1, 1, 1) + struct.pack("<5I", 0, 0, 0, 0, 0)
    head = name.encode("latin-1")[:22].ljust(22, b"\0") + struct.pack("<H", model_id)
    return b"COLL" + struct.pack("<I", len(head) + len(body)) + head + body


@pytest.fixture
def blobs():
    return {"dff": fake_dff, "txd": fake_txd, "coll": coll_v1, "chunk": rw_chunk}


# --------------------------------------------------------------------------- game-ready sources (M2-08)


def write_png(path, w: int, h: int, pixel) -> None:
    """RGBA PNG (stdlib zlib); ``pixel(x, y) -> (r, g, b, a)``, row 0 at the top."""
    import zlib

    raw = b"".join(b"\0" + b"".join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    path.write_bytes(data)


def checker(x: int, y: int) -> tuple[int, int, int, int]:
    """A brown/beige 8-pixel checker (a "crate")."""
    return (150, 96, 40, 255) if ((x // 8) + (y // 8)) % 2 else (225, 190, 120, 255)


def textured_cube_obj(d, name: str = "crate", half: float = 1.0, tex: int = 96) -> "Path":
    """``<name>.obj`` + ``.mtl`` + ``.png``: a cube of side ``2*half`` with one texture (``tex`` px,
    deliberately not a power of two: game-ready scales it to 64)."""
    from pathlib import Path

    d = Path(d)
    write_png(d / f"{name}.png", tex, tex, checker)
    (d / f"{name}.mtl").write_text(f"newmtl {name}_mat\nKd 0.8 0.8 0.8\nmap_Kd {name}.png\n", encoding="utf-8")
    h = half
    v = [(-h, -h, -h), (h, -h, -h), (h, h, -h), (-h, h, -h), (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    lines = [f"mtllib {name}.mtl", f"o {name}"] + [f"v {x} {y} {z}" for x, y, z in v]
    lines += ["vt 0 0", "vt 1 0", "vt 1 1", "vt 0 1", f"usemtl {name}_mat"]
    lines += ["f " + " ".join(f"{i + 1}/{k + 1}" for k, i in enumerate(f)) for f in faces]
    p = d / f"{name}.obj"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def sphere_obj(d, name: str = "ball", seg: int = 64, rings: int = 40, r: float = 1.5) -> "Path":
    """A UV sphere without UVs or textures (``seg * (rings - 1) * 2`` triangles, 4 992 by default)."""
    import math
    from pathlib import Path

    verts = [(0.0, 0.0, r)]
    for i in range(1, rings):
        t = math.pi * i / rings
        for j in range(seg):
            p = 2 * math.pi * j / seg
            verts.append((r * math.sin(t) * math.cos(p), r * math.sin(t) * math.sin(p), r * math.cos(t)))
    verts.append((0.0, 0.0, -r))
    faces = []
    for j in range(seg):
        faces.append((0, 1 + (j + 1) % seg, 1 + j))
    for i in range(rings - 2):
        a, b = 1 + i * seg, 1 + (i + 1) * seg
        for j in range(seg):
            j2 = (j + 1) % seg
            faces += [(a + j, a + j2, b + j2), (a + j, b + j2, b + j)]
    last = len(verts) - 1
    a = 1 + (rings - 2) * seg
    for j in range(seg):
        faces.append((last, a + j, a + (j + 1) % seg))
    (Path(d) / f"{name}.mtl").write_text(f"newmtl {name}_mat\nKd 0.2 0.5 0.8\n", encoding="utf-8")
    lines = [f"mtllib {name}.mtl", f"o {name}"] + [f"v {x:.5f} {y:.5f} {z:.5f}" for x, y, z in verts]
    # the loops above wind clockwise seen from outside: reversed, every normal points outwards
    lines += [f"usemtl {name}_mat"] + ["f " + " ".join(str(i + 1) for i in reversed(f)) for f in faces]
    p = Path(d) / f"{name}.obj"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def gr_sources():
    return {"cube": textured_cube_obj, "sphere": sphere_obj, "png": write_png, "checker": checker}
