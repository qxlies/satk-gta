"""Synthetic bytes for satk.pack tests: RenderWare files, a tiny game root for the census. No game files.

The DFF is one clump with one prelit, textured quad (4 vertices, 2 triangles, no normals), night colours and
optionally a 2dEffect light; the TXD is a list of small DXT1 textures with a mip chain.
"""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

LIB = 0x1803FFFF  # RW 3.6.0.3
SECTOR = 2048


def chunk(t: int, payload: bytes) -> bytes:
    return struct.pack("<III", t, len(payload), LIB) + payload


def rw_string(s: str) -> bytes:
    raw = s.encode("latin-1") + bytes(1)
    return chunk(0x02, raw + bytes(-len(raw) % 4))


def material(tex: str = "wall") -> bytes:
    st = struct.pack("<I4BII3f", 0, 255, 255, 255, 255, 0, 1, 1.0, 1.0, 1.0)
    texture = chunk(0x06, chunk(0x01, struct.pack("<HH", 0x1106, 0)) + rw_string(tex) + rw_string("")
                    + chunk(0x03, b""))
    return chunk(0x07, chunk(0x01, st) + texture + chunk(0x03, b""))


def light_effect(x=0.5, y=0.25, z=1.0, rgba=(250, 200, 100, 255), far=80.0, rng=12.0, csize=1.5, flags=(7, 3)) -> bytes:
    """One 2dEffect entry of type 0 (light) with the 76-byte layout."""
    d = bytes(rgba) + struct.pack("<4f", far, rng, csize, 0.0)
    d += bytes((0, 0, 0, 1, flags[0]))                          # flash, reflection, flare, shadow multiplier, flag 1
    d += b"coronastar".ljust(24, bytes(1)) + b"shad_exp".ljust(24, bytes(1))
    d += bytes((5, flags[1])) + bytes(1)                        # shadow z distance, flag 2, pad
    assert len(d) == 76
    return struct.pack("<3f", x, y, z) + struct.pack("<II", 0, len(d)) + d


def geometry(*, seed: int = 0, lights: bool = False, night: bool = True, normals: bool = False,
             tex: str = "wall", night_raw: bytes | None = None) -> bytes:
    pos = [(0.0, 0.0, 0.0 + seed), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 1.0)]
    tris = [(0, 1, 2, 0), (2, 1, 3, 0)]
    flags = 0x02 | 0x20 | 0x08 | 0x04 | (0x10 if normals else 0)
    st = struct.pack("<IiiI", flags | (1 << 16), len(tris), len(pos), 1)
    st += bytes((120, 110, 100, 255)) * len(pos)
    st += b"".join(struct.pack("<2f", (i % 2) * 1.0, (i // 2) * 1.0) for i in range(len(pos)))
    st += b"".join(struct.pack("<4H", b, a, m, c) for a, b, c, m in tris)
    st += struct.pack("<4fII", 0.5, 0.5, 0.0, 1.0, 1, 1 if normals else 0)
    st += b"".join(struct.pack("<3f", *p) for p in pos)
    if normals:
        st += b"".join(struct.pack("<3f", 0.0, 0.0, 1.0) for _ in pos)
    mats = chunk(0x08, chunk(0x01, struct.pack("<Ii", 1, -1)) + material(tex))
    ext = b""
    if night_raw is not None:
        ext += chunk(0x253F2F9, night_raw)
    elif night:
        ext += chunk(0x253F2F9, struct.pack("<I", 1) + bytes((40, 40, 60, 255)) * len(pos))
    if lights:
        ext += chunk(0x253F2F8, struct.pack("<I", 2) + light_effect() + light_effect(x=2.0, rgba=(1, 2, 3, 4)))
    return chunk(0x0F, chunk(0x01, st) + mats + chunk(0x03, ext))


def dff(**kw) -> bytes:
    """A single-quad clump (see :func:`geometry` for the options)."""
    body = chunk(0x01, struct.pack("<III", 1, 0, 0))
    frames = struct.pack("<I", 1) + struct.pack("<12fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, -1, 0)
    body += chunk(0x0E, chunk(0x01, frames) + chunk(0x03, chunk(0x253F2FE, b"root")))
    body += chunk(0x1A, chunk(0x01, struct.pack("<I", 1)) + geometry(**kw))
    body += chunk(0x14, chunk(0x01, struct.pack("<4I", 0, 0, 5, 0)) + chunk(0x03, b""))
    body += chunk(0x03, b"")
    return chunk(0x10, body)


def native(name: str, seed: int, size: int = 8, tail: int = 0) -> bytes:
    """A D3D9 DXT1 TextureNative whose texel data depends on ``seed`` (the same seed gives the same base level).

    ``tail`` changes only the levels below the base level: the same base level with another tail has the same
    base-level hash but a different mip chain.
    """
    levels = []
    s = size
    while True:
        n = 8 * max(1, (s + 3) // 4) ** 2
        extra = tail * 5 if s != size else 0
        levels.append(bytes((seed * 7 + i * 13 + s + extra) & 0xFF for i in range(n)))
        if s == 1:
            break
        s //= 2
    st = struct.pack("<IBBH32s32sII", 9, 6, 0x11, 0, name.encode(), b"", 0x200, struct.unpack("<I", b"DXT1")[0])
    st += struct.pack("<HHBBBB", size, size, 16, len(levels), 4, 8)
    st += b"".join(struct.pack("<I", len(lv)) + lv for lv in levels)
    return chunk(0x15, chunk(0x01, st) + chunk(0x03, b""))


def txd(textures: list[tuple], size: int = 8) -> bytes:
    """A TXD of DXT1 textures ``(name, seed)`` or ``(name, seed, tail)``."""
    natives = b"".join(native(*t[:2], size, *t[2:]) for t in textures)
    return chunk(0x16, chunk(0x01, struct.pack("<HH", len(textures), 2)) + natives + chunk(0x03, b""))


def img_v2(entries: list[tuple[str, bytes]]) -> bytes:
    n = len(entries)
    dir_sectors = -(-(8 + 32 * n) // SECTOR)
    head = b"VER2" + struct.pack("<I", n)
    data = b""
    off = dir_sectors
    for name, payload in entries:
        sectors = max(1, -(-len(payload) // SECTOR))
        head += struct.pack("<IHH24s", off, sectors, 0, name.encode())
        data += payload.ljust(sectors * SECTOR, bytes(1))
        off += sectors
    return head.ljust(dir_sectors * SECTOR, bytes(1)) + data


def write(root: Path, rel: str, data: bytes | str) -> Path:
    p = root.joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode("latin-1") if isinstance(data, str) else data)
    return p


#: The textures of the census game: (txd, [(name, texel seed)]). Chosen so every dedup case occurs.
#:  - "wall" seed 1 in boxes and shops: the same name and texels in two TXDs (shared by the name-preserving key)
#:  - "brick" seed 1 in shops: another name with the same texels as "wall" (shared only by the texel-only key)
#:  - "wall" seed 2 in sheds: the same name, different texels
#:  - "wall" twice in shops: a same-name duplicate inside one TXD
#:  - "wall" seed 1 with another mip tail in stubs: equal base level, different lower levels (only --exact sees it)
CENSUS_TXDS = {
    "boxes": [("wall", 1), ("roof", 2), ("door", 3)],
    "shops": [("wall", 1), ("brick", 1), ("sign", 4), ("wall", 1)],
    "sheds": [("wall", 2), ("roof", 2)],
    "lone": [("unique", 9)],
    "stubs": [("wall", 1, 1)],
}


def make_game(root: Path) -> Path:
    """A game root the index builder accepts: a few models and the TXDs of :data:`CENSUS_TXDS`."""
    root.mkdir(parents=True, exist_ok=True)
    write(root, "data/default.dat", "IDE DATA\\DEFAULT.IDE\nIDE DATA\\VEHICLES.IDE\n")
    write(root, "data/gta.dat", "IDE DATA\\MAPS\\TEST\\TEST.IDE\nIPL DATA\\MAPS\\TEST\\TEST.IPL\n")
    write(root, "data/default.ide", "objs\nend\n")
    write(root, "data/vehicles.ide", "cars\nend\n")
    write(root, "data/maps/test/test.ide", "objs\n1000, box1, boxes, 150, 0\n1001, shop1, shops, 150, 0\n"
                                           "1002, shed1, sheds, 150, 0\n1003, lone1, lone, 150, 0\n1004, stub1, stubs, 150, 0\nend\n")
    write(root, "data/maps/test/test.ipl", "inst\n1000, box1, 0, 100, 200, 10, 0, 0, 0, 1, -1\nend\n")
    entries = [(f"{m}.dff", dff()) for m in ("box1", "shop1", "shed1", "lone1", "stub1")]
    entries += [(f"{n}.txd", txd(t)) for n, t in CENSUS_TXDS.items()]
    write(root, "models/gta3.img", img_v2(entries))
    write(root, "gta_sa.exe", b"MZ synthetic")
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256")
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {os.path.relpath(p, root).replace('/', chr(92))}"
             for p in files]
    write(root, "MANIFEST.sha256", "\n".join(lines) + "\n")
    return root
