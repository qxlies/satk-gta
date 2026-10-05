"""Fixtures of the satk.media tests (WP-04): a FakeIndexDB whose textures have real synthetic pixels.

Every texture of the fake data set gets a solid colour (or a pattern) in its own format, written by
``FakeIndexDB(root=..., payloads=...)`` into small synthetic archives under ``tmp_path/game``. No game
files are read; all output goes to the isolated ``satk_home`` workspace.

With ``--import-mode=importlib`` a test module cannot import this file: helpers come through the
``mh`` fixture (``mh.payload``, ``mh.px``, ``mh.png_pixels``, ``mh.make_fake`` ...).
"""

from __future__ import annotations

import struct
import types
import zlib
from pathlib import Path

import pytest

from satk.index.api import override_index
from satk.index.fake import FakeIndexDB

MAGENTA = (255, 0, 255, 255)


def c565(r: int, g: int, b: int) -> int:
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def expand565(c: int) -> tuple[int, int, int]:
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def payload(fmt: str, w: int, h: int, rgba=MAGENTA) -> bytes:
    """mip0 bytes of a solid-colour texture in ``fmt``."""
    r, g, b, a = rgba
    if fmt in ("A8R8G8B8", "X8R8G8B8"):
        return bytes((b, g, r, a)) * (w * h)
    nb = max(1, (w + 3) // 4) * max(1, (h + 3) // 4)
    c = c565(r, g, b)
    color_block = struct.pack("<HHI", c, c, 0)
    if fmt == "DXT1":
        return color_block * nb
    if fmt == "DXT3":
        alpha = bytes([((a >> 4) << 4) | (a >> 4)]) * 8
        return (alpha + struct.pack("<HHI", c | 0x8000 if c < 0x8000 else c, c & 0x7FFF, 0)) * nb
    raise ValueError(fmt)


def texture_rows(db) -> list[list]:
    """[txd, name, fmt, w, h] of every texture of a fake index."""
    return db.query("SELECT t.name, x.name, x.d3dfmt, x.w, x.h FROM texture x JOIN txd t ON t.id = x.txd_id "
                    "ORDER BY t.name, x.idx", limit=500)["rows"]


def colour_for(i: int) -> tuple[int, int, int, int]:
    """Distinct, exactly representable (565) colours."""
    palette = [(255, 0, 255), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 0)]
    r, g, b = palette[i % len(palette)]
    return r, g, b, 255


def make_fake(root: Path, *, skip_txd: tuple[str, ...] = (), overrides: dict | None = None) -> FakeIndexDB:
    """A FakeIndexDB with solid-colour pixels for every texture (except TXDs in ``skip_txd``)."""
    base = FakeIndexDB()
    payloads: dict[str, bytes] = {}
    for i, (txd, name, fmt, w, h) in enumerate(texture_rows(base)):
        if txd in skip_txd:
            continue
        sid = f"tex:{txd}/{name}"
        payloads[sid] = (overrides or {}).get(sid) or payload(fmt, w, h, colour_for(i))
    return FakeIndexDB(root=root, payloads=payloads)


@pytest.fixture
def fake_db(satk_home, tmp_path):
    """FakeIndexDB with pixels, installed via ``override_index`` for the test."""
    db = make_fake(tmp_path / "game")
    with override_index(db):
        yield db


def png_pixels(path) -> tuple[int, int, bytes]:
    """Decode a PNG with Pillow (RGBA)."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGBA")
        return im.width, im.height, im.tobytes()


def px(rgba: bytes, w: int, x: int, y: int) -> tuple[int, int, int, int]:
    o = (y * w + x) * 4
    return tuple(rgba[o:o + 4])


def stdlib_png(w: int, h: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\0" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def ch(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))


@pytest.fixture
def mh():
    """Helper functions of this conftest."""
    return types.SimpleNamespace(MAGENTA=MAGENTA, c565=c565, expand565=expand565, payload=payload,
                                 texture_rows=texture_rows, colour_for=colour_for, make_fake=make_fake,
                                 png_pixels=png_pixels, px=px, stdlib_png=stdlib_png)
