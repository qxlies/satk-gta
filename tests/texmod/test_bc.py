"""DXT1/DXT3/DXT5 encoder (satk.texmod.bc): bit-exact with satk.formats.dxt, quality, alpha modes (M2-03)."""

from __future__ import annotations

import struct
import numpy as np
import pytest

from satk.formats.dxt import available_backends, decode_rgba
from satk.formats.txd import TexInfo
from satk.texmod.bc import ALPHA_CUT, dxt_size, encode_dxt, to_blocks
from satk.texmod.encode import psnr


def _info(fmt: str, w: int, h: int, alpha: bool) -> TexInfo:
    return TexInfo(idx=0, name="t", mask="", platform=9, filter=6, uaddr=1, vaddr=1,
                   raster_fmt=0x100 if alpha else 0x200, d3dfmt=fmt, w=w, h=h, depth=16, levels=1, alpha=alpha,
                   mip0_off=0, mip0_size=dxt_size(fmt, w, h), pal_off=None, pal_size=0, unsupported=False)


def _dec(data: bytes, fmt: str, w: int, h: int, alpha: bool, backend: str = "auto") -> np.ndarray:
    out = decode_rgba(_info(fmt, w, h, alpha), data, backend=backend)
    return np.frombuffer(out, dtype=np.uint8).reshape(h, w, 4)


def test_block_layout_round_trip(tm):
    img = tm.image("noise", 12, 8)
    blocks = to_blocks(img)
    assert blocks.shape == (6, 16, 4)
    assert np.array_equal(blocks[1].reshape(4, 4, 4), img[0:4, 4:8])     # block (0, 1)
    assert np.array_equal(blocks[3].reshape(4, 4, 4), img[4:8, 0:4])     # block (1, 0)
    odd = to_blocks(tm.image("noise", 6, 5))                             # padded by repeating the edge
    assert odd.shape == (4, 16, 4)
    assert np.array_equal(odd[3].reshape(4, 4, 4)[1, 3], tm.image("noise", 6, 5)[4, 5])


@pytest.mark.parametrize("fmt", ["DXT1", "DXT3", "DXT5"])
def test_solid_565_colour_is_exact(fmt):
    img = np.zeros((8, 8, 4), dtype=np.uint8)
    img[...] = (132, 130, 33, 255)          # exactly representable in 565: (16, 32, 4)
    data = encode_dxt(img, fmt)
    assert len(data) == dxt_size(fmt, 8, 8)
    assert np.array_equal(_dec(data, fmt, 8, 8, fmt != "DXT1"), img)


def test_dxt1_quality_on_smooth_and_shapes(tm):
    for kind, floor in (("smooth", 36.0), ("shapes", 40.0)):  # 2D gradients are the hard case for DXT1
        img = tm.image(kind, 128, 64)
        got = psnr(img, _dec(encode_dxt(img, "DXT1"), "DXT1", 128, 64, False), alpha=False)
        assert got >= floor, (kind, got)


def test_quality_levels_never_get_worse(tm):
    img = tm.image("noise", 32, 32)
    scores = [psnr(img, _dec(encode_dxt(img, "DXT1", quality=q), "DXT1", 32, 32, False), alpha=False)
              for q in ("fast", "normal", "high")]
    assert scores[0] <= scores[1] <= scores[2], scores
    assert scores[2] > scores[0]


def test_deterministic(tm):
    img = tm.image("smooth_alpha", 64, 32)
    for fmt in ("DXT1", "DXT3", "DXT5"):
        assert encode_dxt(img, fmt, alpha=True) == encode_dxt(img.copy(), fmt, alpha=True)


def test_dxt1_opaque_blocks_use_four_colour_mode(tm):
    data = encode_dxt(tm.image("noise", 64, 64), "DXT1", alpha=False)
    for k in range(0, len(data), 8):
        c0, c1, bits = struct.unpack_from("<HHI", data, k)
        assert c0 > c1 or (c0 == c1 and bits == 0), (k, c0, c1, bits)
    # even read as a 1-bit-alpha texture nothing is transparent
    assert (_dec(data, "DXT1", 64, 64, True)[..., 3] == 255).all()


def test_dxt1_one_bit_alpha(tm):
    img = tm.image("binary_alpha", 64, 64)
    dec = _dec(encode_dxt(img, "DXT1", alpha=True), "DXT1", 64, 64, True)
    assert np.array_equal(dec[..., 3] == 255, img[..., 3] >= ALPHA_CUT)
    assert (dec[img[..., 3] < ALPHA_CUT][:, :3] == 0).all()       # transparent texels are black
    assert psnr(img, dec, alpha=True) >= 38
    clear = np.zeros((8, 8, 4), dtype=np.uint8)                    # fully transparent
    assert (_dec(encode_dxt(clear, "DXT1", alpha=True), "DXT1", 8, 8, True)[..., 3] == 0).all()


def test_dxt3_alpha_is_4_bit(tm):
    img = tm.image("smooth_alpha", 64, 16)
    dec = _dec(encode_dxt(img, "DXT3"), "DXT3", 64, 16, True)
    assert np.abs(dec[..., 3].astype(int) - img[..., 3]).max() <= 8
    assert np.array_equal(dec[..., 3] % 17, np.zeros_like(dec[..., 3]))


def test_dxt5_alpha_modes(tm):
    img = tm.image("smooth_alpha", 64, 16)
    dec = _dec(encode_dxt(img, "DXT5"), "DXT5", 64, 16, True)
    assert np.abs(dec[..., 3].astype(int) - img[..., 3]).max() <= 3
    # a block with exact 0 and 255 plus a mid value needs the 6-value mode to keep the extremes
    blk = np.zeros((4, 4, 4), dtype=np.uint8)
    blk[..., :3] = 90
    blk[..., 3] = np.array([0, 255, 120, 125] * 4).reshape(4, 4)
    data = encode_dxt(blk, "DXT5")
    assert data[0] <= data[1]                                         # 6-value mode chosen
    out = _dec(data, "DXT5", 4, 4, True)[..., 3]
    assert out[0, 0] == 0 and out[0, 1] == 255 and abs(int(out[0, 2]) - 120) <= 3


@pytest.mark.parametrize("w,h", [(4, 4), (8, 4), (6, 10), (1, 1), (2, 2)])
def test_odd_and_tiny_sizes(tm, w, h):
    img = tm.image("smooth", w, h)
    for fmt in ("DXT1", "DXT5"):
        data = encode_dxt(img, fmt)
        assert len(data) == dxt_size(fmt, w, h)
        assert _dec(data, fmt, w, h, fmt != "DXT1").shape == (h, w, 4)


def test_all_decoder_backends_agree(tm):
    img = tm.image("smooth_alpha", 32, 32)
    for fmt, alpha in (("DXT1", True), ("DXT3", True), ("DXT5", True)):
        data = encode_dxt(img, fmt, alpha=alpha)
        ref = _dec(data, fmt, 32, 32, alpha, backend="python")
        for b in available_backends():
            assert np.array_equal(_dec(data, fmt, 32, 32, alpha, backend=b), ref), (fmt, b)


def test_bad_arguments(tm):
    img = tm.image("smooth", 8, 8)
    with pytest.raises(ValueError):
        encode_dxt(img, "DXT2")
    with pytest.raises(ValueError):
        encode_dxt(img, "DXT1", quality="best")


def test_psnr_helper():
    a = np.zeros((4, 4, 4), dtype=np.uint8)
    assert psnr(a, a, alpha=False) == 100.0
    b = a.copy()
    b[..., :3] = 1                                                    # MSE 1 -> 48.13 dB
    assert psnr(a, b, alpha=False) == pytest.approx(48.13, abs=0.01)
    c = a.copy()
    c[..., :3] = 200                                                  # colour under alpha 0 does not count
    assert psnr(a, c, alpha=True) == 100.0
