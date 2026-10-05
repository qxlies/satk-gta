"""Fixtures of the satk.texmod tests (M2-03): synthetic images, PNGs, TXDs and IMG archives.

Nothing here reads game files; everything is generated in the test's ``tmp_path``. With
``--import-mode=importlib`` a test module cannot import this file: helpers come through the ``tm`` fixture.
"""

from __future__ import annotations

import struct
import types
from pathlib import Path

import numpy as np
import pytest


def image(kind: str, w: int = 64, h: int = 64, seed: int = 7) -> np.ndarray:
    """Synthetic RGBA test images (uint8 (h, w, 4))."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    img = np.zeros((h, w, 4), dtype=np.uint8)
    img[..., 3] = 255
    if kind in ("smooth", "smooth_alpha", "binary_alpha"):
        img[..., 0] = np.clip(255 * xx / max(1, w - 1), 0, 255)
        img[..., 1] = np.clip(255 * yy / max(1, h - 1), 0, 255)
        img[..., 2] = np.clip(128 + 100 * np.sin((xx + yy) / 9.0), 0, 255)
    elif kind == "noise":
        img[..., :3] = np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)
    elif kind == "shapes":  # flat regions with hard edges, like signs and decals
        img[..., :3] = (40, 60, 90)
        img[(xx - w / 2) ** 2 + (yy - h / 2) ** 2 < (min(w, h) / 3) ** 2, :3] = (230, 200, 30)
        img[(yy // 8) % 4 == 0, :3] = (200, 30, 40)
    else:
        raise ValueError(kind)
    if kind == "smooth_alpha":
        img[..., 3] = np.clip(255 * xx / max(1, w - 1), 0, 255)
    elif kind == "binary_alpha":
        img[..., 3] = np.where(((xx // 8) + (yy // 8)) % 2 == 0, 255, 0)
    return img


def png_bytes(img: np.ndarray) -> bytes:
    from satk.media import png as _png

    h, w = img.shape[:2]
    return _png.encode(w, h, img.tobytes())


def write_png(path: Path, img: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes(img))
    return path


def decode(data: bytes, t) -> np.ndarray:
    """mip 0 of TexInfo ``t`` in TXD ``data`` as an (h, w, 4) array."""
    from satk.formats.dxt import decode_rgba
    from satk.formats.txd import mip0_bytes, palette_bytes

    out = decode_rgba(t, mip0_bytes(data, t), palette_bytes(data, t))
    return np.frombuffer(out, dtype=np.uint8).reshape(t.h, t.w, 4)


def native(name: str, img: np.ndarray, fmt: str, *, mips: int = 1, alpha: bool | None = None, mask: str = "",
           filt: int = 6) -> bytes:
    from satk.texmod.encode import alpha_kind, encode_level, mip_chain
    from satk.texmod.txdwrite import NativeSpec, native_chunk

    if alpha is None:
        alpha = alpha_kind(img) != "opaque"
    lv = tuple(encode_level(x, fmt, alpha=alpha) for x in mip_chain(img, mips))
    h, w = img.shape[:2]
    return native_chunk(NativeSpec(name, fmt, w, h, lv, alpha, mask=mask, filter=filt))


def make_txd(textures: list[tuple], device_id: int = 2) -> bytes:
    """``[(name, img, fmt, mips), ...]`` -> TXD bytes."""
    from satk.texmod.txdwrite import txd_chunk

    return txd_chunk([native(n, img, fmt, mips=m) for n, img, fmt, m in textures], device_id=device_id)


def sample_txd() -> bytes:
    """Three textures: X8R8G8B8 64x64, DXT1 32x32 with a full mip chain, A8R8G8B8 16x16."""
    return make_txd([("wall", image("shapes", 64, 64), "X8R8G8B8", 1),
                     ("Floor_Tiles", image("smooth", 32, 32), "DXT1", 6),
                     ("glass", image("smooth_alpha", 16, 16), "A8R8G8B8", 1)])


def make_img(path: Path, entries: dict[str, bytes]) -> Path:
    """A VER2 IMG archive with ``entries`` (name -> bytes), sector aligned."""
    names = list(entries)
    dir_sectors = (8 + 32 * len(names) + 2047) // 2048
    head = bytearray(b"VER2" + struct.pack("<I", len(names)))
    body = bytearray()
    pos = dir_sectors
    for n in names:
        data = entries[n]
        sec = (len(data) + 2047) // 2048
        head += struct.pack("<IHH24s", pos, sec, 0, n.encode("ascii"))
        body += data + b"\0" * (sec * 2048 - len(data))
        pos += sec
    head += b"\0" * (dir_sectors * 2048 - len(head))
    path.write_bytes(bytes(head + body))
    return path


@pytest.fixture
def tm():
    """Helpers: ``image``, ``png_bytes``, ``write_png``, ``decode``, ``native``, ``make_txd``, ``sample_txd``,
    ``make_img``."""
    return types.SimpleNamespace(image=image, png_bytes=png_bytes, write_png=write_png, decode=decode,
                                 native=native, make_txd=make_txd, sample_txd=sample_txd, make_img=make_img)
