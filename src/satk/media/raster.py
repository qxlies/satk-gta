"""Minimal 2D drawing for sheets and maps: one interface, two backends (SPEC §4.4).

* :class:`PilPainter` -- Pillow ``ImageDraw`` on an opaque RGB canvas with alpha blending (default when
  Pillow is installed);
* :class:`PyPainter` -- a stdlib RGBA ``bytearray`` (rectangles blend through ``bytes.translate``
  tables, so fills run at C speed; only per-pixel alpha images are composited in Python).

Both are deterministic. Text is drawn with the built-in bitmap font (:mod:`satk.media.font`), so
numbers look the same with either backend. :func:`resize_rgba` scales an RGBA buffer (Pillow LANCZOS
with premultiplied alpha, or a stdlib area average).

Coordinates: ``(x0, y0)`` inclusive, ``(x1, y1)`` exclusive, clipped to the canvas.
"""

from __future__ import annotations

from typing import Sequence

from . import font
from . import png as _png

__all__ = ["Painter", "PilPainter", "PyPainter", "new_painter", "resize_rgba", "fit_size", "Color"]

Color = tuple[int, int, int, int]


def fit_size(w: int, h: int, limit: int) -> tuple[int, int]:
    """Size of ``(w, h)`` scaled so the longest side is ``limit`` (never upscales)."""
    if limit <= 0 or max(w, h) <= limit:
        return w, h
    if w >= h:
        return limit, max(1, round(h * limit / w))
    return max(1, round(w * limit / h)), limit


def _rgba(c: Sequence[int]) -> Color:
    if len(c) == 3:
        return int(c[0]), int(c[1]), int(c[2]), 255
    return int(c[0]), int(c[1]), int(c[2]), int(c[3])


class Painter:
    """Drawing interface used by the sheet and map renderers."""

    w: int
    h: int

    def rect(self, x0: int, y0: int, x1: int, y1: int, fill: Sequence[int] | None = None,
             outline: Sequence[int] | None = None, width: int = 1) -> None:
        raise NotImplementedError

    def image(self, x: int, y: int, w: int, h: int, rgba: bytes) -> None:
        """Composite an RGBA image (alpha over) with its top-left corner at ``(x, y)``."""
        raise NotImplementedError

    def mask(self, x: int, y: int, w: int, h: int, mask: bytes, color: Sequence[int]) -> None:
        """Paint ``color`` where the 8-bit ``mask`` is non-zero."""
        raise NotImplementedError

    def rgba(self) -> bytes:
        raise NotImplementedError

    # ---- shared helpers

    def text(self, x: int, y: int, s: str, color: Sequence[int], scale: int = 1) -> tuple[int, int]:
        """Draw one line of text at ``(x, y)`` (top-left); returns its size."""
        w, h, m = font.text_mask(s, scale)
        if w:
            self.mask(x, y, w, h, m, color)
        return w, h

    def badge(self, x: int, y: int, s: str, *, scale: int = 2, fg: Sequence[int] = (255, 255, 255, 255),
              bg: Sequence[int] = (0, 0, 0, 255), border: Sequence[int] | None = None, pad: int | None = None,
              center: bool = False) -> tuple[int, int, int, int]:
        """A filled box with text (map/sheet numbers). Returns the box ``(x0, y0, x1, y1)``."""
        tw, th = font.text_size(s, scale)
        p = scale if pad is None else pad
        bw, bh = tw + 2 * p + 2, th + 2 * p + 2
        if center:
            x, y = x - bw // 2, y - bh // 2
        self.rect(x, y, x + bw, y + bh, fill=bg, outline=border)
        self.text(x + p + 1, y + p + 1, s, fg, scale)
        return x, y, x + bw, y + bh

    def line_h(self, x0: int, x1: int, y: int, color: Sequence[int], width: int = 1) -> None:
        self.rect(min(x0, x1), y, max(x0, x1) + 1, y + width, fill=color)

    def line_v(self, x: int, y0: int, y1: int, color: Sequence[int], width: int = 1) -> None:
        self.rect(x, min(y0, y1), x + width, max(y0, y1) + 1, fill=color)

    def png(self) -> bytes:
        """The canvas as PNG bytes (RGB when fully opaque)."""
        return _png.encode(self.w, self.h, self.rgba())


# --------------------------------------------------------------------------- Pillow backend


class PilPainter(Painter):
    def __init__(self, w: int, h: int, bg: Sequence[int] = (0, 0, 0, 255)):
        from PIL import Image, ImageDraw

        self.w, self.h = int(w), int(h)
        self._Image = Image
        # an opaque RGB canvas: ImageDraw blends RGBA fills only into RGB images
        self.im = Image.new("RGB", (self.w, self.h), _rgba(bg)[:3])
        self.draw = ImageDraw.Draw(self.im, "RGBA")

    def rect(self, x0, y0, x1, y1, fill=None, outline=None, width=1):
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        if x1 <= x0 or y1 <= y0 or x1 <= 0 or y1 <= 0 or x0 >= self.w or y0 >= self.h:
            return
        xy = [x0, y0, x1 - 1, y1 - 1]
        if fill is not None:
            self.draw.rectangle(xy, fill=_rgba(fill))
        if outline is not None and width > 0:
            self.draw.rectangle(xy, outline=_rgba(outline), width=int(width))

    def image(self, x, y, w, h, rgba):
        if w <= 0 or h <= 0:
            return
        tile = self._Image.frombuffer("RGBA", (w, h), bytes(rgba), "raw", "RGBA", 0, 1)
        # alpha_composite needs the tile inside the canvas: crop what sticks out
        cx0, cy0 = max(0, -x), max(0, -y)
        cx1, cy1 = min(w, self.w - x), min(h, self.h - y)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        if (cx0, cy0, cx1, cy1) != (0, 0, w, h):
            tile = tile.crop((cx0, cy0, cx1, cy1))
        if _png.is_opaque(tile.tobytes()):
            self.im.paste(tile.convert("RGB"), (x + cx0, y + cy0))
        else:
            self.im.paste(tile, (x + cx0, y + cy0), tile)  # alpha over

    def mask(self, x, y, w, h, mask, color):
        if w <= 0 or h <= 0:
            return
        m = self._Image.frombuffer("L", (w, h), bytes(mask), "raw", "L", 0, 1)
        layer = self._Image.new("RGBA", (w, h), _rgba(color))
        cx0, cy0 = max(0, -x), max(0, -y)
        cx1, cy1 = min(w, self.w - x), min(h, self.h - y)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        if (cx0, cy0, cx1, cy1) != (0, 0, w, h):
            m = m.crop((cx0, cy0, cx1, cy1))
            layer = layer.crop((cx0, cy0, cx1, cy1))
        self.im.paste(layer, (x + cx0, y + cy0), m)

    def rgba(self) -> bytes:
        return self.im.convert("RGBA").tobytes()

    def png(self) -> bytes:
        return _png.encode(self.w, self.h, b"", opaque=True, image=self.im)


# --------------------------------------------------------------------------- stdlib backend


def _blend_tables(c: Color) -> tuple[bytes, bytes, bytes, bytes]:
    r, g, b, a = c
    ia = 255 - a
    tr = bytes((v * ia + r * a + 127) // 255 for v in range(256))
    tg = bytes((v * ia + g * a + 127) // 255 for v in range(256))
    tb = bytes((v * ia + b * a + 127) // 255 for v in range(256))
    ta = bytes(min(255, a + (v * ia + 127) // 255) for v in range(256))
    return tr, tg, tb, ta


class PyPainter(Painter):
    def __init__(self, w: int, h: int, bg: Sequence[int] = (0, 0, 0, 255)):
        self.w, self.h = int(w), int(h)
        self.buf = bytearray(bytes(_rgba(bg)) * (self.w * self.h))
        self._tables: dict[Color, tuple[bytes, bytes, bytes, bytes]] = {}

    def _fill(self, x0: int, y0: int, x1: int, y1: int, c: Color) -> None:
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.w, x1), min(self.h, y1)
        if x1 <= x0 or y1 <= y0:
            return
        n = x1 - x0
        if c[3] == 255:
            row = bytes(c) * n
            for y in range(y0, y1):
                o = (y * self.w + x0) * 4
                self.buf[o:o + 4 * n] = row
            return
        if c[3] == 0:
            return
        t = self._tables.get(c)
        if t is None:
            t = self._tables[c] = _blend_tables(c)
        for y in range(y0, y1):
            o = (y * self.w + x0) * 4
            e = o + 4 * n
            for k in range(4):
                self.buf[o + k:e:4] = bytes(self.buf[o + k:e:4]).translate(t[k])

    def rect(self, x0, y0, x1, y1, fill=None, outline=None, width=1):
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        if x1 <= x0 or y1 <= y0:
            return
        if fill is not None:
            self._fill(x0, y0, x1, y1, _rgba(fill))
        if outline is not None and width > 0:
            c = _rgba(outline)
            wd = min(int(width), (x1 - x0 + 1) // 2, (y1 - y0 + 1) // 2) or 1
            self._fill(x0, y0, x1, y0 + wd, c)
            self._fill(x0, y1 - wd, x1, y1, c)
            self._fill(x0, y0 + wd, x0 + wd, y1 - wd, c)
            self._fill(x1 - wd, y0 + wd, x1, y1 - wd, c)

    def image(self, x, y, w, h, rgba):
        src = bytes(rgba)
        cx0, cy0 = max(0, -x), max(0, -y)
        cx1, cy1 = min(w, self.w - x), min(h, self.h - y)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        opaque = _png.is_opaque(src)
        for sy in range(cy0, cy1):
            so = (sy * w + cx0) * 4
            se = (sy * w + cx1) * 4
            do = ((y + sy) * self.w + x + cx0) * 4
            if opaque:
                self.buf[do:do + (se - so)] = src[so:se]
                continue
            for i in range(so, se, 4):
                a = src[i + 3]
                if a == 0:
                    pass
                elif a == 255:
                    self.buf[do:do + 4] = src[i:i + 4]
                else:
                    ia = 255 - a
                    d = self.buf
                    d[do] = (src[i] * a + d[do] * ia + 127) // 255
                    d[do + 1] = (src[i + 1] * a + d[do + 1] * ia + 127) // 255
                    d[do + 2] = (src[i + 2] * a + d[do + 2] * ia + 127) // 255
                    d[do + 3] = min(255, a + (d[do + 3] * ia + 127) // 255)
                do += 4

    def mask(self, x, y, w, h, mask, color):
        c = bytes(_rgba(color))
        for my in range(h):
            yy = y + my
            if yy < 0 or yy >= self.h:
                continue
            row = mask[my * w:(my + 1) * w]
            for mx, v in enumerate(row):
                xx = x + mx
                if v and 0 <= xx < self.w:
                    o = (yy * self.w + xx) * 4
                    self.buf[o:o + 4] = c

    def rgba(self) -> bytes:
        return bytes(self.buf)


def new_painter(w: int, h: int, bg: Sequence[int] = (0, 0, 0, 255)) -> Painter:
    """A painter for the active PNG backend (:func:`satk.media.png.backend`)."""
    if _png.backend() == "pillow":
        return PilPainter(w, h, bg)
    return PyPainter(w, h, bg)


# --------------------------------------------------------------------------- resizing


def _resize_py(w: int, h: int, rgba: bytes, nw: int, nh: int) -> bytes:
    """Area average (exact box filter for integer factors), alpha-weighted colour."""
    out = bytearray(nw * nh * 4)
    xs = [(x * w // nw, max(x * w // nw + 1, (x + 1) * w // nw)) for x in range(nw)]
    o = 0
    for y in range(nh):
        y0 = y * h // nh
        y1 = max(y0 + 1, (y + 1) * h // nh)
        for x0, x1 in xs:
            sr = sg = sb = sa = 0
            cnt = 0
            for yy in range(y0, y1):
                base = (yy * w) * 4
                for xx in range(x0, x1):
                    i = base + xx * 4
                    a = rgba[i + 3]
                    sr += rgba[i] * a
                    sg += rgba[i + 1] * a
                    sb += rgba[i + 2] * a
                    sa += a
                    cnt += 1
            if sa:
                out[o] = (sr + sa // 2) // sa
                out[o + 1] = (sg + sa // 2) // sa
                out[o + 2] = (sb + sa // 2) // sa
            out[o + 3] = (sa + cnt // 2) // cnt
            o += 4
    return bytes(out)


def resize_rgba(w: int, h: int, rgba: bytes, nw: int, nh: int) -> bytes:
    """Scale an RGBA buffer to ``(nw, nh)``; returns the new buffer (a copy when unchanged)."""
    if (nw, nh) == (w, h):
        return bytes(rgba)
    if _png.backend() == "pillow":
        from PIL import Image

        im = Image.frombuffer("RGBA", (w, h), bytes(rgba), "raw", "RGBA", 0, 1)
        return im.resize((nw, nh), Image.Resampling.LANCZOS).tobytes()
    return _resize_py(w, h, bytes(rgba), nw, nh)
