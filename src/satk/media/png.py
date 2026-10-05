"""PNG writing and reading for ``satk.media`` (SPEC §4.4): Pillow when installed, stdlib ``zlib`` otherwise.

* :func:`encode_png` / :func:`decode_png` -- pure stdlib codec (8-bit gray/RGB/RGBA, no interlace);
  the decoder understands all five PNG row filters, so it reads Pillow's files too;
* :func:`encode` -- RGBA buffer -> PNG bytes with the best available backend; fully opaque images are
  stored as RGB (smaller, same pixels);
* :func:`write_file` -- guarded atomic write (``paths.ensure_writable`` + unique temp + ``os.replace``),
  safe when several processes produce the same content-addressed file at once;
* :func:`load_rgba` -- any PNG -> ``(w, h, rgba)``;
* :func:`png_size` -- width/height from the IHDR without decoding.

No metadata (time, gamma, text) is written, so equal pixels give equal bytes (SPEC §3.6).
Pillow is imported only inside functions (SPEC §2.3).
"""

from __future__ import annotations

import io
import itertools
import os
import struct
import threading
import zlib
from functools import lru_cache
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import ensure_writable

__all__ = ["PNG_SIG", "have_pillow", "backend", "encode_png", "decode_png", "encode", "write_file",
           "load_rgba", "png_size", "is_opaque", "COMPRESS_LEVEL"]

PNG_SIG = b"\x89PNG\r\n\x1a\n"
#: zlib level for every PNG we write (Pillow's default as well).
COMPRESS_LEVEL = 6
_COLOR_CH = {0: 1, 2: 3, 4: 2, 6: 4}  # PNG colour type -> channels
_tmp_counter = itertools.count()


@lru_cache(maxsize=1)
def have_pillow() -> bool:
    """True if Pillow can be imported (checked once)."""
    try:
        import PIL.Image  # noqa: F401
    except ImportError:
        return False
    return True


def backend() -> str:
    """``"pillow"`` or ``"zlib"``: what :func:`encode` uses."""
    if os.environ.get("SATK_MEDIA_NO_PILLOW", "").strip() in ("1", "true", "yes"):
        return "zlib"
    return "pillow" if have_pillow() else "zlib"


def is_opaque(rgba: bytes | bytearray | memoryview) -> bool:
    """True if every alpha byte of an RGBA buffer is 255."""
    a = bytes(rgba[3::4])
    return a.count(255) == len(a)


def _strip_alpha(rgba: bytes | bytearray) -> bytes:
    n = len(rgba) // 4
    out = bytearray(n * 3)
    out[0::3] = rgba[0::4]
    out[1::3] = rgba[1::4]
    out[2::3] = rgba[2::4]
    return bytes(out)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_png(w: int, h: int, data: bytes | bytearray, channels: int = 4, level: int = COMPRESS_LEVEL) -> bytes:
    """Stdlib PNG encoder (filter 0 on every row).

    Args:
        w, h: image size.
        data: ``w*h*channels`` bytes, rows top to bottom.
        channels: 1 (gray), 3 (RGB) or 4 (RGBA).
        level: zlib level 0..9.
    """
    ctype = {1: 0, 3: 2, 4: 6}.get(channels)
    if ctype is None:
        raise ValueError(f"channels must be 1, 3 or 4, got {channels}")
    stride = w * channels
    if len(data) != stride * h:
        raise ValueError(f"expected {stride * h} bytes for {w}x{h}x{channels}, got {len(data)}")
    mv = memoryview(bytes(data))
    raw = b"".join(b"\x00" + mv[y * stride:(y + 1) * stride].tobytes() for y in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0)
    return PNG_SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, level)) + _chunk(b"IEND", b"")


def _unfilter(raw: bytes, w: int, h: int, bpp: int) -> bytearray:
    stride = w * bpp
    out = bytearray(stride * h)
    prev = bytearray(stride)
    pos = 0
    for y in range(h):
        ft = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if ft == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:  # Up
            line = bytearray(map(lambda a, b: (a + b) & 0xFF, line, prev))
        elif ft == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        elif ft != 0:
            raise ValueError(f"bad PNG filter type {ft} in row {y}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out


def decode_png(data: bytes) -> tuple[int, int, bytes]:
    """Stdlib PNG decoder -> ``(w, h, rgba)``. 8-bit gray/gray+alpha/RGB/RGBA, non-interlaced."""
    if data[:8] != PNG_SIG:
        raise ValueError("not a PNG file")
    pos = 8
    w = h = ctype = 0
    idat = []
    while pos + 8 <= len(data):
        (n,) = struct.unpack_from(">I", data, pos)
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            w, h, depth, ctype, _cm, _fm, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or ctype not in _COLOR_CH or interlace:
                raise ValueError(f"unsupported PNG (depth {depth}, colour type {ctype}, interlace {interlace})")
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    ch = _COLOR_CH.get(ctype)
    if not w or not h or ch is None:
        raise ValueError("PNG without a usable IHDR")
    px = _unfilter(zlib.decompress(b"".join(idat)), w, h, ch)
    n = w * h
    if ch == 4:
        return w, h, bytes(px)
    out = bytearray(n * 4)
    if ch == 3:
        out[0::4], out[1::4], out[2::4] = px[0::3], px[1::3], px[2::3]
        out[3::4] = b"\xff" * n
    elif ch == 1:
        out[0::4] = out[1::4] = out[2::4] = px
        out[3::4] = b"\xff" * n
    else:  # gray + alpha
        g = px[0::2]
        out[0::4] = out[1::4] = out[2::4] = g
        out[3::4] = px[1::2]
    return w, h, bytes(out)


def encode(w: int, h: int, rgba: bytes | bytearray, *, opaque: bool | None = None, image=None) -> bytes:
    """RGBA -> PNG bytes (Pillow if available, else :func:`encode_png`). Opaque images become RGB.

    ``image`` may pass a ready Pillow image instead of ``rgba`` (used by the Pillow painter).
    """
    if image is not None:
        im = image if image.mode in ("RGB", "RGBA") else image.convert("RGBA")
        if opaque and im.mode == "RGBA":
            im = im.convert("RGB")
        bio = io.BytesIO()
        im.save(bio, "PNG", compress_level=COMPRESS_LEVEL)
        return bio.getvalue()
    if opaque is None:
        opaque = is_opaque(rgba)
    if backend() == "pillow":
        from PIL import Image

        im = Image.frombuffer("RGBA", (w, h), bytes(rgba), "raw", "RGBA", 0, 1)
        if opaque:
            im = im.convert("RGB")
        bio = io.BytesIO()
        im.save(bio, "PNG", compress_level=COMPRESS_LEVEL)
        return bio.getvalue()
    if opaque:
        return encode_png(w, h, _strip_alpha(rgba), 3)
    return encode_png(w, h, rgba, 4)


def write_file(path: str | os.PathLike, data: bytes) -> Path:
    """Atomically write ``data`` to ``path`` after :func:`~satk.core.paths.ensure_writable`.

    The temp name is unique per process and thread. If another writer replaced the file
    first and Windows refuses the rename, an existing destination counts as success
    (all our files are content-addressed or deterministic).
    """
    p = ensure_writable(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.{next(_tmp_counter)}.tmp")
    ensure_writable(tmp)
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        os.replace(tmp, p)
    except PermissionError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        if not p.is_file():
            raise
    return p


def load_rgba(path: str | os.PathLike) -> tuple[int, int, bytes]:
    """Read a PNG as ``(w, h, rgba)``; ``NOT_FOUND`` if missing."""
    try:
        if backend() == "pillow":
            from PIL import Image

            with Image.open(path) as im:
                im = im.convert("RGBA")
                return im.width, im.height, im.tobytes()
        with open(path, "rb") as f:
            return decode_png(f.read())
    except FileNotFoundError:
        raise SatkError("NOT_FOUND", f"no such image: {os.fspath(path)}") from None


def png_size(path: str | os.PathLike) -> tuple[int, int] | None:
    """``(w, h)`` from the IHDR chunk, ``None`` if the file is not a PNG."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != PNG_SIG or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])
