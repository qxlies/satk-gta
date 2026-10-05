"""Formats, mip chain, raw encoders and the auto format choice (satk.texmod.encode, M2-03)."""

from __future__ import annotations

import numpy as np
import pytest

from satk.formats.dxt import decode_rgba
from satk.formats.txd import TexInfo
from satk.texmod.encode import (FORMATS, alpha_kind, auto_format, encode_level, full_levels, level_dims, level_size,
                                mip_chain, psnr)


def _raw_info(fmt: str, w: int, h: int, alpha: bool) -> TexInfo:
    return TexInfo(idx=0, name="t", mask="", platform=9, filter=6, uaddr=1, vaddr=1, raster_fmt=0, d3dfmt=fmt,
                   w=w, h=h, depth=32, levels=1, alpha=alpha, mip0_off=0, mip0_size=level_size(fmt, w, h),
                   pal_off=None, pal_size=0, unsupported=False)


def test_level_geometry():
    assert full_levels(256, 256) == 9 and full_levels(128, 32) == 8 and full_levels(1, 1) == 1
    assert full_levels(100, 60) == 7
    assert [level_dims(128, 32, i) for i in (0, 2, 5, 6, 7)] == [(128, 32), (32, 8), (4, 1), (2, 1), (1, 1)]
    assert level_size("DXT1", 2, 2) == 8 and level_size("DXT5", 1, 1) == 16 and level_size("DXT1", 8, 2) == 16
    assert level_size("A8R8G8B8", 3, 2) == 24 and level_size("R5G6B5", 4, 4) == 32


def test_mip_chain_box_filter():
    img = np.zeros((4, 8, 4), dtype=np.uint8)
    img[..., 3] = 255
    img[:, 0::2, 0] = 200                       # columns 200/0 -> 100 after one level
    chain = mip_chain(img, full_levels(8, 4))
    assert [c.shape[:2] for c in chain] == [(4, 8), (2, 4), (1, 2), (1, 1)]
    assert (chain[1][..., 0] == 100).all() and (chain[3][..., 0] == 100).all()
    assert chain[0] is img


def test_mip_chain_alpha_weighted():
    img = np.zeros((2, 2, 4), dtype=np.uint8)
    img[0, 0] = (255, 0, 0, 0)                  # invisible red must not tint the next level
    img[0, 1] = img[1, 0] = img[1, 1] = (0, 0, 255, 255)
    lvl = mip_chain(img, 2)[1][0, 0]
    assert tuple(lvl) == (0, 0, 255, 191)
    flat = mip_chain(img, 2, alpha_weighted=False)[1][0, 0]
    assert flat[0] == 64


def test_mip_chain_odd_sides(tm):
    img = tm.image("smooth", 6, 5)
    chain = mip_chain(img, 3)
    assert [c.shape[:2] for c in chain] == [(5, 6), (2, 3), (1, 1)]
    assert abs(int(chain[2][0, 0, 0]) - int(img[..., 0].mean())) <= 2


@pytest.mark.parametrize("fmt,tol", [("A8R8G8B8", 0), ("X8R8G8B8", 0), ("R5G6B5", 4), ("A1R5G5B5", 4),
                                     ("A4R4G4B4", 8)])
def test_raw_formats_round_trip(tm, fmt, tol):
    img = tm.image("binary_alpha" if fmt == "A1R5G5B5" else "smooth_alpha", 16, 8)
    alpha = fmt in ("A8R8G8B8", "A1R5G5B5", "A4R4G4B4")
    data = encode_level(img, fmt)
    assert len(data) == level_size(fmt, 16, 8)
    dec = np.frombuffer(decode_rgba(_raw_info(fmt, 16, 8, alpha), data), dtype=np.uint8).reshape(8, 16, 4)
    assert np.abs(dec[..., :3].astype(int) - img[..., :3]).max() <= tol
    if alpha:
        assert np.abs(dec[..., 3].astype(int) - img[..., 3]).max() <= tol
    else:
        assert (dec[..., 3] == 255).all()


def test_alpha_kind_and_auto_format(tm):
    opaque, binary, smooth = tm.image("smooth", 16, 16), tm.image("binary_alpha", 16, 16), tm.image("smooth_alpha", 16, 16)
    assert [alpha_kind(x) for x in (opaque, binary, smooth)] == ["opaque", "binary", "smooth"]
    assert auto_format(opaque) == ("DXT1", False)
    assert auto_format(binary) == ("DXT1", True)
    assert auto_format(smooth) == ("DXT5", True)
    assert auto_format(smooth, "DXT3") == ("DXT3", True)
    assert auto_format(opaque, "DXT3") == ("DXT1", False)
    assert auto_format(opaque, "X8R8G8B8") == ("X8R8G8B8", False)
    assert auto_format(smooth, "X8R8G8B8") == ("A8R8G8B8", True)
    assert auto_format(opaque, "A8R8G8B8") == ("A8R8G8B8", True)
    assert auto_format(binary, "A1R5G5B5") == ("A1R5G5B5", True)
    assert auto_format(smooth, "A1R5G5B5") == ("A8R8G8B8", True)
    assert auto_format(opaque, "PAL8") == ("DXT1", False)
    assert auto_format(opaque, "L8") == ("X8R8G8B8", False)
    assert auto_format(tm.image("smooth", 6, 8)) == ("X8R8G8B8", False)        # not whole 4x4 blocks
    assert auto_format(tm.image("smooth_alpha", 6, 8)) == ("A8R8G8B8", True)


def test_every_format_encodes_a_tiny_level(tm):
    img = tm.image("smooth", 1, 1)
    for fmt in FORMATS:
        assert len(encode_level(img, fmt)) == level_size(fmt, 1, 1)
    with pytest.raises(ValueError):
        encode_level(img, "PAL8")


def test_psnr_alpha_premultiplied(tm):
    img = tm.image("binary_alpha", 16, 16)
    other = img.copy()
    other[img[..., 3] == 0, :3] = 0
    assert psnr(img, other, alpha=True) == 100.0
    assert psnr(img, other, alpha=False) < 100.0
