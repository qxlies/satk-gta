"""satk.formats.dxt: the three backends agree, alpha semantics, raw/paletted formats (spike S2, milestone F3)."""

from __future__ import annotations

import random
import struct

import pytest

from satk.core.errors import SatkError
from satk.formats import dxt
from satk.formats.dxt import available_backends, decode_rgba, mean_rgba, preview_rgba, resolve_backend
from satk.formats.rw import FormatError
from satk.formats.txd import TexInfo

FAST = [b for b in available_backends() if b != "python"]


def tex(fmt: str, w: int = 4, h: int = 4, *, alpha: bool = False, raster: int = 0, platform: int = 9,
        unsupported: bool = False) -> TexInfo:
    return TexInfo(0, "t", "", platform, 6, 1, 1, raster, fmt, w, h, 32, 1, alpha, 0, 0, None, 0, unsupported)


def c565(r: int, g: int, b: int) -> int:
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def dxt1_block(c0: int, c1: int, idx: list[int]) -> bytes:
    bits = sum(v << (2 * i) for i, v in enumerate(idx))
    return struct.pack("<HHI", c0, c1, bits)


RED, BLUE = c565(255, 0, 0), c565(0, 0, 255)


def px(rgba: bytes, w: int, x: int, y: int) -> tuple:
    o = (y * w + x) * 4
    return tuple(rgba[o:o + 4])


# ----------------------------------------------------------------------------- DXT semantics
@pytest.mark.parametrize("backend", ["python", *FAST])
def test_dxt1_four_colour_block(backend):
    data = dxt1_block(RED, BLUE, [0, 1, 2, 3] * 4)
    out = decode_rgba(tex("DXT1"), data, backend=backend)
    assert px(out, 4, 0, 0) == (255, 0, 0, 255) and px(out, 4, 1, 0) == (0, 0, 255, 255)
    assert px(out, 4, 2, 0) == (170, 0, 85, 255) and px(out, 4, 3, 0) == (85, 0, 170, 255)


@pytest.mark.parametrize("backend", ["python", *FAST])
def test_dxt1_565_is_opaque_and_1555_punches_through(backend):
    """Pitfall #6: index 3 of a c0 <= c1 block is black; transparent only with 1-bit alpha (1555)."""
    data = dxt1_block(BLUE, RED, [3, 2, 0, 1] * 4)            # c0 < c1: three colours + index 3
    opaque = decode_rgba(tex("DXT1", alpha=False, raster=0x200), data, backend=backend)
    assert px(opaque, 4, 0, 0) == (0, 0, 0, 255)
    assert px(opaque, 4, 1, 0) == (127, 0, 127, 255)
    assert all(a == 255 for a in opaque[3::4])
    punch = decode_rgba(tex("DXT1", alpha=True, raster=0x100), data, backend=backend)
    assert px(punch, 4, 0, 0) == (0, 0, 0, 0) and px(punch, 4, 2, 0) == (0, 0, 255, 255)


@pytest.mark.parametrize("backend", ["python", *FAST])
def test_dxt3_dxt5_alpha(backend):
    a3 = struct.pack("<Q", sum((i % 16) << (4 * i) for i in range(16)))
    out = decode_rgba(tex("DXT3", alpha=True), a3 + dxt1_block(RED, BLUE, [0] * 16), backend=backend)
    assert list(out[3::4]) == [i * 17 for i in range(16)]
    # DXT5 a0 > a1: 8 interpolated values; a0 <= a1: 6 + 0 and 255
    for a0, a1 in ((200, 20), (20, 200)):
        bits = sum((i % 8) << (3 * i) for i in range(16)).to_bytes(6, "little")
        blk = bytes((a0, a1)) + bits + dxt1_block(RED, BLUE, [1] * 16)
        out = decode_rgba(tex("DXT5", alpha=True), blk, backend=backend)
        if a0 > a1:
            pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
        else:
            pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
        assert list(out[3::4]) == [pal[i % 8] for i in range(16)]
        assert px(out, 4, 0, 0)[:3] == (0, 0, 255)          # DXT3/5 colour blocks are always 4-colour


@pytest.mark.parametrize("fmt", ["DXT1", "DXT3", "DXT5"])
@pytest.mark.parametrize("wh", [(4, 4), (8, 12), (6, 5), (2, 2), (1, 1), (12, 4)])
def test_backends_agree_on_random_blocks(fmt, wh):
    """S2 parity on random data: every backend equals the stdlib reference (<= 1 per channel; here exact)."""
    rng = random.Random(hash((fmt, wh)) & 0xFFFF)
    w, h = wh
    nb = max(1, (w + 3) // 4) * max(1, (h + 3) // 4)
    data = bytes(rng.randrange(256) for _ in range(nb * (8 if fmt == "DXT1" else 16)))
    for alpha in (False, True):
        t = tex(fmt, w, h, alpha=alpha, raster=0x100 if alpha else 0x200)
        ref = decode_rgba(t, data, backend="python")
        assert len(ref) == w * h * 4
        for be in FAST:
            out = decode_rgba(t, data, backend=be)
            assert max(abs(x - y) for x, y in zip(ref, out)) <= 1 and len(out) == len(ref), be


# ----------------------------------------------------------------------------- raw / paletted
RAW_CASES = [
    ("A8R8G8B8", bytes((10, 20, 30, 40)), (30, 20, 10, 40)),
    ("X8R8G8B8", bytes((10, 20, 30, 0)), (30, 20, 10, 255)),
    ("R8G8B8", bytes((10, 20, 30)), (30, 20, 10, 255)),
    ("R5G6B5", struct.pack("<H", c565(255, 0, 255)), (255, 0, 255, 255)),
    ("A1R5G5B5", struct.pack("<H", 0x8000 | (31 << 10)), (255, 0, 0, 255)),
    ("A1R5G5B5", struct.pack("<H", 31), (0, 0, 255, 0)),
    ("X1R5G5B5", struct.pack("<H", 31 << 5), (0, 255, 0, 255)),
    ("A4R4G4B4", struct.pack("<H", 0x8F21), (255, 34, 17, 136)),
    ("L8", bytes((77,)), (77, 77, 77, 255)),
    ("A8L8", bytes((77, 9)), (77, 77, 77, 9)),
    ("A8", bytes((99,)), (0, 0, 0, 99)),
]


@pytest.mark.parametrize("fmt,pixel,want", RAW_CASES, ids=[c[0] + str(i) for i, c in enumerate(RAW_CASES)])
@pytest.mark.parametrize("backend", ["auto", "python"])
def test_raw_formats(fmt, pixel, want, backend):
    out = decode_rgba(tex(fmt, 2, 2, alpha=True), pixel * 4, backend=backend)
    assert [px(out, 2, x, y) for x, y in ((0, 0), (1, 1))] == [want, want]


def test_pal8_pal4():
    pal = b"".join(bytes((i, 255 - i, 7, 100 + i % 100)) for i in range(256))
    t = tex("PAL8", 4, 1, alpha=True, raster=0x2600, platform=8)
    out = decode_rgba(t, bytes((0, 1, 200, 255)), pal)
    assert px(out, 4, 2, 0) == (200, 55, 7, 100) and px(out, 4, 3, 0) == (255, 0, 7, 155)
    opaque = decode_rgba(tex("PAL8", 4, 1, alpha=False, platform=8), bytes((0, 1, 200, 255)), pal)
    assert all(a == 255 for a in opaque[3::4])
    t4 = tex("PAL4", 4, 1, alpha=True, raster=0x4000)
    out4 = decode_rgba(t4, bytes((0x21, 0x43)), pal[:64])     # low nibble first
    assert [px(out4, 4, x, 0)[0] for x in range(4)] == [1, 2, 3, 4]
    with pytest.raises(FormatError, match="palette"):
        decode_rgba(t, bytes(4))


def test_errors_and_backend_selection(monkeypatch):
    with pytest.raises(FormatError, match="needs"):
        decode_rgba(tex("DXT1", 8, 8), bytes(8))
    with pytest.raises(FormatError, match="not supported"):
        decode_rgba(tex("DXT1", unsupported=True, platform=0x325350), bytes(8))
    with pytest.raises(FormatError, match="not supported"):
        decode_rgba(tex("0x7F"), bytes(64))
    with pytest.raises(FormatError, match="bad size"):
        decode_rgba(tex("A8R8G8B8", 0, 4), b"")
    with pytest.raises(ValueError):
        decode_rgba(tex("DXT1"), bytes(8), backend="gpu")
    assert resolve_backend("auto") == available_backends()[0] and resolve_backend("python") == "python"
    monkeypatch.setattr(dxt, "available_backends", lambda: ["python"])
    with pytest.raises(SatkError) as ei:
        resolve_backend("pillow")
    assert ei.value.code == "DEPENDENCY"


# ----------------------------------------------------------------------------- preview / mean
def test_preview_and_mean_dxt1():
    w = h = 8
    blocks = [dxt1_block(RED, RED, [0] * 16), dxt1_block(BLUE, BLUE, [0] * 16),
              dxt1_block(RED, BLUE, [0] * 16), dxt1_block(c565(0, 255, 0), c565(0, 255, 0), [0] * 16)]
    t = tex("DXT1", w, h)
    pw, ph, pv = preview_rgba(t, b"".join(blocks))
    assert (pw, ph) == (2, 2)
    assert [tuple(pv[i:i + 4]) for i in range(0, 16, 4)] == [(255, 0, 0, 255), (0, 0, 255, 255), (127, 0, 127, 255),
                                                             (0, 255, 0, 255)]
    m = mean_rgba(t, b"".join(blocks))
    assert m == (round(255 * 3 / 8) << 24) | (round(255 * 2 / 8) << 16) | (round(255 * 3 / 8) << 8) | 255


def test_preview_and_mean_dxt3_dxt5_raw():
    a3 = struct.pack("<Q", 0xFFFFFFFFFFFFFFFF)
    t3 = tex("DXT3", 4, 4, alpha=True)
    assert preview_rgba(t3, a3 + dxt1_block(RED, RED, [0] * 16))[2] == bytes((255, 0, 0, 255))
    assert mean_rgba(t3, a3 + dxt1_block(RED, RED, [0] * 16)) == 0xFF0000FF
    t5 = tex("DXT5", 4, 4, alpha=True)
    blk = bytes((100, 50)) + bytes(6) + dxt1_block(BLUE, BLUE, [0] * 16)
    assert preview_rgba(t5, blk)[2] == bytes((0, 0, 255, 75)) and mean_rgba(t5, blk) == 0x0000FF4B
    traw = tex("A8R8G8B8", 8, 4, alpha=True)
    data = bytes((0, 0, 255, 255)) * 32
    pw, ph, pv = preview_rgba(traw, data)
    assert (pw, ph, pv) == (2, 1, bytes((255, 0, 0, 255)) * 2)
    assert mean_rgba(traw, data) == 0xFF0000FF


@pytest.mark.parametrize("fmt", ["DXT1", "DXT3", "DXT5"])
def test_mean_close_to_full_decode_mean(fmt):
    """The endpoint mean is a cheap estimate: within a few levels of the decoded mean on smooth data."""
    rng = random.Random(3)
    blocks = []
    for _ in range(16):
        c = c565(rng.randrange(256), rng.randrange(256), rng.randrange(256))
        alpha = bytes((200, 200)) + bytes(6) if fmt == "DXT5" else (b"\xcc" * 8 if fmt == "DXT3" else b"")
        blocks.append(alpha + dxt1_block(c, c, [0] * 16))
    t = tex(fmt, 16, 16, alpha=fmt != "DXT1")
    data = b"".join(blocks)
    full = decode_rgba(t, data, backend="python")
    m = mean_rgba(t, data)
    for ch in range(4):
        avg = sum(full[ch::4]) / 256
        assert abs(((m >> (24 - 8 * ch)) & 255) - avg) <= 1
