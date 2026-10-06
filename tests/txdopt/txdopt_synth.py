"""Synthetic builders for satk.txdopt tests: images, TXDs, DFFs, IMG archives, a tiny game root and a bloated mod.

No game files: every byte is generated here (RW chunks after the format, textures through satk.texmod).

``make_game(root)``: a game root the index builder accepts (DAT, IDE, IPL, ``models/gta3.img``, loose
``models/generic/vehicle.txd``, ``MANIFEST.sha256``). Models: 400 ``testcar`` (TXD ``testcar`` -> vehicle),
1000 ``box1`` and 1001 ``box2`` (TXD ``boxes``: ``boxtex`` used by both, ``oldtex`` used by none), 1002 ``rifle1``
(weap, TXD ``rifle1``: ``rifle1icon`` unused by the DFF). Placements: box1 at (100, 200), box2 at (110, 200),
box1 at (900, 900).

``make_mod(root)``: a bloated map mod: ``bigmap.ide`` (2000 big1 + 2001 big2 -> ``bigtxd``, 2002 small1 -> ``smalltxd``,
2003 small2 -> ``smalltxd2``), DFFs, ``bigmap.ipl`` (placements near (100, 200)) and three TXDs with large raw
textures, an unused texture, a same-name duplicate, cross-TXD duplicates and an opaque DXT5.
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
from pathlib import Path

import numpy as np

LIBID = 0x1803FFFF
SECTOR = 2048


# --------------------------------------------------------------------------- images


def smooth(w: int, h: int, seed: int = 0, alpha: str = "opaque") -> np.ndarray:
    """A smooth RGBA image (gradients + low-frequency waves); ``alpha``: opaque | binary | smooth."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float64)
    u, v = x / max(1, w - 1), y / max(1, h - 1)
    k = 1 + seed % 3
    img = np.empty((h, w, 4), dtype=np.float64)
    img[..., 0] = 128 + 100 * np.sin(2 * math.pi * (u * k + 0.1 * seed))
    img[..., 1] = 40 + 180 * v
    img[..., 2] = 128 + 90 * np.cos(2 * math.pi * (v * k + u * 0.5))
    if alpha == "opaque":
        img[..., 3] = 255
    elif alpha == "binary":
        img[..., 3] = np.where((np.sin(2 * math.pi * u * 3) > 0), 255, 0)
    else:
        img[..., 3] = 30 + 200 * u
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- RW chunks


def chunk(ctype: int, payload: bytes, libid: int = LIBID) -> bytes:
    return struct.pack("<III", ctype, len(payload), libid) + payload


def rw_string(s: str) -> bytes:
    b = s.encode("latin-1") + b"\0"
    b += b"\0" * (-len(b) % 4)
    return chunk(0x02, b)


def native(name: str, img: np.ndarray, fmt: str = "A8R8G8B8", *, alpha: bool | None = None, mips: bool = False) -> bytes:
    """A D3D9 TextureNative of ``img`` in ``fmt`` (through satk.texmod)."""
    from satk.texmod.encode import encode_level, full_levels, mip_chain
    from satk.texmod.txdwrite import NativeSpec, native_chunk

    h, w = img.shape[:2]
    a = (fmt in ("A8R8G8B8", "DXT3", "DXT5")) if alpha is None else alpha
    n = full_levels(w, h) if mips else 1
    levels = tuple(encode_level(lv, fmt, alpha=a) for lv in mip_chain(img, n))
    return native_chunk(NativeSpec(name, fmt, w, h, levels, alpha=a))


def raw_native(name: str, body: bytes, *, platform: int = 9) -> bytes:
    """A TextureNative with a hand-made struct (``body`` after the platform field)."""
    return chunk(0x15, chunk(0x01, struct.pack("<I", platform) + body) + chunk(0x03, b""))


def dxt1_holes_native(name: str, w: int = 8, h: int = 8) -> bytes:
    """DXT1 without the alpha flag whose blocks use index 3 in the 3-colour mode (texels that render black)."""
    blocks = b"".join(struct.pack("<HHI", 0x001F, 0xF800, 0xFFFFFFFF) for _ in range((w // 4) * (h // 4)))
    hdr = struct.pack("<IBBH32s32sII", 9, 6, 0x11, 0, name.encode(), b"", 0x200, int.from_bytes(b"DXT1", "little"))
    hdr += struct.pack("<HHBBBB", w, h, 16, 1, 4, 8)
    return chunk(0x15, chunk(0x01, hdr + struct.pack("<I", len(blocks)) + blocks) + chunk(0x03, b""))


def ps2_native(name: str) -> bytes:
    """A PS2 (platform 'PS2\\0') native: kept, never decoded."""
    return chunk(0x15, chunk(0x01, struct.pack("<I", 0x00325350) + b"\x06\x11\0\0") + rw_string(name) +
                 rw_string("") + chunk(0x03, b""))


def txd(natives: list[bytes]) -> bytes:
    return chunk(0x16, chunk(0x01, struct.pack("<HH", len(natives), 2)) + b"".join(natives) + chunk(0x03, b""))


def dff(textures: list[str], *, size: float = 1.0, extra_ext: bytes = b"") -> bytes:
    """A one-geometry DFF whose materials use ``textures``."""
    verts = [(-size, -size, 0), (size, -size, 0), (size, size, 0), (-size, size, size)]
    tris = [(0, 1, 2), (0, 2, 3)]
    nv, nt = len(verts), len(tris)
    g = struct.pack("<IIII", 0x02 | 0x04 | (1 << 16), nt, nv, 1)
    g += b"\0" * (8 * nv)
    for i, (a, b, c) in enumerate(tris):
        g += struct.pack("<HHHH", b, a, i % max(1, len(textures)), c)
    g += struct.pack("<4fII", 0, 0, 0, 2 * size, 1, 0)
    for v in verts:
        g += struct.pack("<3f", *v)
    mats = b""
    for t in textures:
        tex = chunk(0x06, chunk(0x01, struct.pack("<I", 0x1106)) + rw_string(t) + rw_string("") + chunk(0x03, b""))
        mats += chunk(0x07, chunk(0x01, struct.pack("<I4BII3f", 0, 255, 255, 255, 255, 0, 1, 1.0, 1.0, 1.0)) + tex
                      + chunk(0x03, b""))
    matlist = chunk(0x08, chunk(0x01, struct.pack(f"<I{len(textures)}i", len(textures), *([-1] * len(textures))))
                    + mats)
    idx = [i for t in tris for i in t]
    binmesh = struct.pack("<III", 0, 1, len(idx)) + struct.pack("<II", len(idx), 0) + struct.pack(f"<{len(idx)}I", *idx)
    geom = chunk(0x0F, chunk(0x01, g) + matlist + chunk(0x03, chunk(0x50E, binmesh) + extra_ext))
    frame = struct.pack("<9f3fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, -1, 0)
    flist = chunk(0x0E, chunk(0x01, struct.pack("<I", 1) + frame) + chunk(0x03, chunk(0x253F2FE, b"root")))
    glist = chunk(0x1A, chunk(0x01, struct.pack("<I", 1)) + geom)
    atomic = chunk(0x14, chunk(0x01, struct.pack("<4I", 0, 0, 5, 0)) + chunk(0x03, b""))
    return chunk(0x10, chunk(0x01, struct.pack("<III", 1, 0, 0)) + flist + glist + atomic + chunk(0x03, b""))


def img_v2(entries: list[tuple[str, bytes]]) -> bytes:
    n = len(entries)
    dir_sectors = -(-(8 + 32 * n) // SECTOR)
    head = b"VER2" + struct.pack("<I", n)
    data = b""
    off = dir_sectors
    for name, payload in entries:
        sectors = max(1, -(-len(payload) // SECTOR))
        head += struct.pack("<IHH24s", off, sectors, 0, name.encode())
        data += payload.ljust(sectors * SECTOR, b"\0")
        off += sectors
    return head.ljust(dir_sectors * SECTOR, b"\0") + data


def write(root: Path, rel: str, data: bytes | str) -> Path:
    p = root.joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data.encode("latin-1") if isinstance(data, str) else data)
    return p


# --------------------------------------------------------------------------- game


def small_tex(name: str, seed: int) -> bytes:
    return native(name, smooth(16, 16, seed), "DXT1")


def make_game(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    write(root, "data/default.dat", "IDE DATA\\DEFAULT.IDE\nIDE DATA\\VEHICLES.IDE\n")
    write(root, "data/gta.dat", "IDE DATA\\MAPS\\TEST\\TEST.IDE\nIPL DATA\\MAPS\\TEST\\TEST.IPL\n")
    write(root, "data/default.ide", "objs\nend\nweap\n1002, rifle1, rifle1, null, 1, 50, 0\nend\n")
    write(root, "data/vehicles.ide", "cars\n400, testcar, testcar, car, TESTCAR, TESTCAR, null, executive, 10, 0, 0, "
                                     "-1, 0.7, 0.7, -1\nend\n")
    write(root, "data/maps/test/test.ide", "objs\n1000, box1, boxes, 150, 0\n1001, box2, boxes, 150, 0\nend\n")
    write(root, "data/maps/test/test.ipl", "inst\n1000, box1, 0, 100, 200, 10, 0, 0, 0, 1, -1\n"
                                           "1001, box2, 0, 110, 200, 10, 0, 0, 0, 1, -1\n"
                                           "1000, box1, 0, 900, 900, 10, 0, 0, 0, 1, -1\nend\n")
    gta3 = img_v2([
        ("testcar.dff", dff(["carbody", "vehiclegeneric256"])),
        ("testcar.txd", txd([small_tex("carbody", 1)])),
        ("box1.dff", dff(["boxtex"])),
        ("box2.dff", dff(["boxtex"], size=2.0)),
        ("boxes.txd", txd([small_tex("boxtex", 3), small_tex("oldtex", 4)])),
        ("rifle1.dff", dff(["rifle1body"])),
        ("rifle1.txd", txd([small_tex("rifle1body", 5), small_tex("rifle1icon", 6)])),
    ])
    write(root, "models/gta3.img", gta3)
    write(root, "models/generic/vehicle.txd", txd([small_tex("vehiclegeneric256", 7)]))
    write(root, "gta_sa.exe", b"MZ synthetic")
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256")
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {os.path.relpath(p, root).replace('/', chr(92))}"
             for p in files]
    write(root, "MANIFEST.sha256", "\n".join(lines) + "\n")
    return root


# --------------------------------------------------------------------------- the bloated mod

BIG = 1024


def mod_textures() -> dict[str, bytes]:
    """The three TXDs of the bloated mod (TXD stem -> bytes)."""
    wall = smooth(BIG, BIG, 1)
    big = txd([
        native("wall", wall, "A8R8G8B8"),                         # used by big1 + big2; also in smalltxd
        native("floor", smooth(BIG, BIG, 2, "smooth"), "A8R8G8B8"),  # smooth alpha -> DXT5
        native("roof", smooth(512, 512, 3, "binary"), "A8R8G8B8"),   # 1-bit alpha -> DXT1+a
        native("unused1", smooth(BIG, BIG, 4), "A8R8G8B8"),        # no DFF names it
        native("wall", wall, "A8R8G8B8"),                         # same name, same pixels -> dedupe
    ])
    small = txd([
        native("wall", wall, "A8R8G8B8"),
        native("sign", smooth(256, 256, 5), "DXT5"),               # opaque DXT5 -> lossless DXT1
        native("unused2", smooth(256, 256, 6), "X8R8G8B8", alpha=False),
    ])
    small2 = txd([native("wall", wall, "A8R8G8B8"), native("lamp", smooth(600, 300, 7), "X8R8G8B8", alpha=False)])
    return {"bigtxd": big, "smalltxd": small, "smalltxd2": small2}


def make_mod(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    write(root, "bigmap.ide", "objs\n2000, big1, bigtxd, 300, 0\n2001, big2, bigtxd, 300, 0\n"
                              "2002, small1, smalltxd, 300, 0\n2003, small2, smalltxd2, 300, 0\nend\n")
    write(root, "bigmap.ipl", "inst\n2000, big1, 0, 100, 210, 10, 0, 0, 0, 1, -1\n"
                              "2001, big2, 0, 120, 210, 10, 0, 0, 0, 1, -1\n"
                              "2002, small1, 0, 130, 210, 10, 0, 0, 0, 1, -1\nend\n")
    write(root, "models/big1.dff", dff(["wall", "floor"]))
    write(root, "models/big2.dff", dff(["wall", "roof"]))
    write(root, "models/small1.dff", dff(["wall", "sign"]))
    write(root, "models/small2.dff", dff(["wall", "lamp"]))
    for stem, data in mod_textures().items():
        write(root, f"models/{stem}.txd", data)
    return root


def tree_digest(root: Path) -> dict[str, str]:
    """relpath -> sha256 of every file under ``root`` (to prove a folder was not written)."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}
