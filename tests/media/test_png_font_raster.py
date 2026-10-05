"""Low-level pieces of satk.media: stdlib PNG codec, bitmap font, painters, resizing (WP-04)."""

from __future__ import annotations

import hashlib
import io

import pytest

from satk.core.errors import SatkError
from satk.media import font
from satk.media import png as P
from satk.media import raster as R


def px(rgba: bytes, w: int, x: int, y: int) -> tuple[int, int, int, int]:
    o = (y * w + x) * 4
    return tuple(rgba[o:o + 4])


def _gradient(w: int, h: int, alpha: bool = True) -> bytes:
    out = bytearray()
    for y in range(h):
        for x in range(w):
            out += bytes(((x * 7) & 255, (y * 5) & 255, (x * y) & 255, (x + y * 3) & 255 if alpha else 255))
    return bytes(out)


# --------------------------------------------------------------------------- png


@pytest.mark.parametrize("channels", [1, 3, 4])
def test_stdlib_png_roundtrip(channels):
    w, h = 13, 7
    rgba = _gradient(w, h)
    data = bytes(rgba[i] for i in range(len(rgba)) if channels == 4 or (i % 4) < channels)
    if channels == 1:
        data = rgba[0::4]
    elif channels == 3:
        data = bytes(b for i, b in enumerate(rgba) if i % 4 != 3)
    png = P.encode_png(w, h, data, channels)
    assert png[:8] == P.PNG_SIG
    dw, dh, out = P.decode_png(png)
    assert (dw, dh) == (w, h)
    if channels == 4:
        assert out == rgba
    elif channels == 3:
        assert out[0::4] == rgba[0::4] and out[1::4] == rgba[1::4] and out[3::4] == b"\xff" * (w * h)
    else:
        assert out[0::4] == out[1::4] == out[2::4] == rgba[0::4]


def test_decoder_reads_pillow_files_with_all_filters():
    from PIL import Image

    w, h = 64, 48
    rgba = _gradient(w, h)
    for mode in ("RGBA", "RGB", "L", "LA"):
        im = Image.frombytes("RGBA", (w, h), rgba).convert(mode)
        bio = io.BytesIO()
        im.save(bio, "PNG", optimize=True)  # optimize -> adaptive filters (Sub/Up/Avg/Paeth)
        dw, dh, out = P.decode_png(bio.getvalue())
        assert (dw, dh) == (w, h)
        assert out == im.convert("RGBA").tobytes(), mode


def test_encode_is_deterministic_and_rgb_when_opaque(monkeypatch):
    w, h = 32, 32
    opaque = _gradient(w, h, alpha=False)
    a = P.encode(w, h, opaque)
    assert a == P.encode(w, h, opaque)
    assert a[25] == 2  # IHDR colour type: RGB
    assert P.encode(w, h, _gradient(w, h))[25] == 6  # RGBA kept
    monkeypatch.setenv("SATK_MEDIA_NO_PILLOW", "1")
    assert P.backend() == "zlib"
    b = P.encode(w, h, opaque)
    assert b[25] == 2 and P.decode_png(b)[2] == P.decode_png(a)[2]


def test_write_file_guard_and_png_size(satk_home, tmp_path):
    p = P.write_file(satk_home / "work" / "x" / "a.png", P.encode_png(3, 2, bytes(24), 4))
    assert P.png_size(p) == (3, 2)
    assert P.png_size(tmp_path / "missing.png") is None
    assert [q.name for q in p.parent.iterdir()] == ["a.png"]  # no temp files left
    guarded = satk_home / "src" / "satk-media-guard.png"  # src\ of the isolated workspace is protected
    with pytest.raises(SatkError) as e:
        P.write_file(guarded, b"x")
    assert e.value.code == "PROTECTED_PATH"
    assert not guarded.exists()


def test_load_rgba_missing(satk_home):
    with pytest.raises(SatkError) as e:
        P.load_rgba(satk_home / "nope.png")
    assert e.value.code == "NOT_FOUND"


# --------------------------------------------------------------------------- font


def test_glyph_table_shape():
    for ch, g in font._GLYPHS.items():
        rows = g.split(" ")
        assert len(rows) == font.GLYPH_H, ch
        assert all(len(r) == font.GLYPH_W and set(r) <= {"#", "."} for r in rows), ch
    for ch in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_-./:#":
        assert font.has_glyph(ch)
    assert font.has_glyph("a")  # lowercase -> uppercase glyph


def test_text_mask_sizes_and_ink():
    assert font.text_size("", 2) == (0, 14)
    w, h, m = font.text_mask("16", 2)
    assert (w, h) == ((2 * 6 - 1) * 2, 14) and len(m) == w * h
    one = font.text_mask("1", 1)[2]
    assert one.count(255) == sum(r.count("#") for r in font._GLYPHS["1"].split(" "))
    assert font.text_mask("é", 1) == font.text_mask("?", 1)  # unknown -> '?'
    assert font.text_mask("abc", 1) == font.text_mask("ABC", 1)


# --------------------------------------------------------------------------- painters


def test_fit_size():
    assert R.fit_size(64, 64, 256) == (64, 64)
    assert R.fit_size(512, 256, 128) == (128, 64)
    assert R.fit_size(32, 256, 64) == (8, 64)
    assert R.fit_size(1000, 1, 10) == (10, 1)
    assert R.fit_size(50, 40, 0) == (50, 40)


def _draw(p):
    p.rect(2, 2, 30, 20, fill=(200, 10, 10, 255), outline=(0, 255, 0, 255), width=2)
    p.rect(-5, -5, 4, 4, fill=(0, 0, 255, 255))
    p.text(5, 22, "42", (255, 255, 0, 255), 1)
    p.badge(20, 24, "7", scale=2)
    tile = bytes((10, 20, 30, 255)) * 16
    p.image(36, 30, 4, 4, tile)
    p.image(-2, 38, 4, 4, tile)  # clipped


def test_painters_agree_on_opaque_drawing():
    a, b = R.PilPainter(48, 44, (5, 6, 7, 255)), R.PyPainter(48, 44, (5, 6, 7, 255))
    _draw(a)
    _draw(b)
    assert a.rgba() == b.rgba()
    assert P.decode_png(a.png())[2] == P.decode_png(b.png())[2]


def test_painters_blend_alpha_close():
    a, b = R.PilPainter(8, 8, (100, 100, 100, 255)), R.PyPainter(8, 8, (100, 100, 100, 255))
    for p in (a, b):
        p.rect(0, 0, 8, 8, fill=(200, 0, 0, 128))
        p.image(0, 0, 2, 1, bytes((0, 255, 0, 64, 0, 0, 255, 0)))
    ra, rb = a.rgba(), b.rgba()
    assert max(abs(x - y) for x, y in zip(ra, rb)) <= 2
    r, g, bl, al = px(rb, 8, 4, 4)
    assert abs(r - 150) <= 1 and abs(g - 50) <= 1 and al == 255
    assert px(rb, 8, 1, 0) == px(rb, 8, 2, 0)  # alpha 0 leaves the canvas


def test_resize_area_average_and_pillow():
    w, h = 4, 2
    rgba = bytes([0, 0, 0, 255, 100, 100, 100, 255, 10, 20, 30, 255, 30, 40, 50, 255] * 2)
    out = R._resize_py(w, h, rgba, 2, 1)
    assert out == bytes([50, 50, 50, 255, 20, 30, 40, 255])
    # fully transparent pixels do not darken the colour
    tr = bytes([255, 0, 0, 255, 0, 0, 0, 0])
    assert R._resize_py(2, 1, tr, 1, 1) == bytes([255, 0, 0, 128])
    big = _gradient(64, 32)
    small = R.resize_rgba(64, 32, big, 16, 8)
    assert len(small) == 16 * 8 * 4
    assert R.resize_rgba(64, 32, big, 64, 32) == big


def test_png_bytes_stable_hash():
    p = R.PyPainter(16, 16, (1, 2, 3, 255))
    p.text(1, 1, "N", (255, 255, 255, 255))
    h1 = hashlib.sha256(p.png()).hexdigest()
    q = R.PyPainter(16, 16, (1, 2, 3, 255))
    q.text(1, 1, "N", (255, 255, 255, 255))
    assert hashlib.sha256(q.png()).hexdigest() == h1
