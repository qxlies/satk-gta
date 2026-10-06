"""Reference photos for a modelling session (``ref.import``): small, EXIF-free JPEGs plus a pixel-grid sheet.

A photo is rotated by its EXIF orientation, shrunk to at most 1,600 px, saved as JPEG without any
metadata (location, camera) into ``<project>/refs/`` (or ``work/refs/``), and a second copy gets a
labelled pixel grid so pixel coordinates (wheel centres, roof line) can be read off for
``ref.plane`` (``points`` + ``distance``). Names carry a hash of the source: a repeat writes nothing new.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from ..core import paths
from ..core.errors import SatkError, require_module

__all__ = ["MAX_PX", "GRID_PX", "import_photo"]

MAX_PX = 1600
GRID_PX = 100
QUALITY = 88
_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")


def _stem(p: Path) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "_", p.stem.lower()).strip("_")
    return (s or "ref")[:40]


def _grid(im, step: int):
    from PIL import ImageDraw, ImageFont

    g = im.convert("RGB").copy()
    d = ImageDraw.Draw(g, "RGBA")
    w, h = g.size
    font = ImageFont.load_default()
    for x in range(0, w, step):
        major = x % (step * 5) == 0
        d.line([(x, 0), (x, h)], fill=(255, 40, 40, 170 if major else 90), width=1)
        d.text((x + 2, 2), str(x), fill=(255, 255, 0, 255), font=font)
    for y in range(0, h, step):
        major = y % (step * 5) == 0
        d.line([(0, y), (w, y)], fill=(40, 200, 255, 170 if major else 90), width=1)
        d.text((2, y + 2), str(y), fill=(255, 255, 0, 255), font=font)
    return g


def import_photo(src: str | os.PathLike, *, project: str | None = None, max_px: int = MAX_PX,
                 grid: int = GRID_PX) -> dict:
    """Prepare one photo; returns ``{image, grid, size, source_size, scale, exif_removed}``."""
    require_module("PIL", pip="Pillow", purpose="ref.import")
    from PIL import Image, ImageOps

    sp = Path(os.path.abspath(os.fspath(src)))
    if not sp.is_file():
        raise SatkError("NOT_FOUND", f"no image {paths.jpath(sp)}")
    if sp.suffix.lower() not in _EXTS:
        raise SatkError("BAD_PARAMS", f"{sp.name}: not a photo ({', '.join(_EXTS)})")
    if not 256 <= int(max_px) <= 4096:
        raise SatkError("BAD_PARAMS", "max_px must be 256-4096")
    if not 10 <= int(grid) <= 1000:
        raise SatkError("BAD_PARAMS", "grid must be 10-1000 px")
    raw = sp.read_bytes()
    h8 = hashlib.sha256(raw + f"|{max_px}|{grid}".encode()).hexdigest()[:8]
    if project:
        from .project import load

        pdir, _data = load(project)
        dest = pdir / "refs"
    else:
        dest = paths.work("refs")
    stem = f"{_stem(sp)}-{h8}"
    out, out_grid = dest / f"{stem}.jpg", dest / f"{stem}.grid.jpg"
    try:
        with Image.open(sp) as im0:
            src_size = list(im0.size)
            had_exif = bool(im0.info.get("exif")) or bool(getattr(im0, "getexif", lambda: {})())
            im = ImageOps.exif_transpose(im0)
            im = im.convert("RGB")
    except (OSError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"cannot read {sp.name}: {e}") from None
    w, h = im.size
    scale = min(1.0, float(max_px) / max(w, h))
    if scale < 1.0:
        im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    reused = out.is_file() and out_grid.is_file()
    if not reused:
        paths.ensure_writable(dest).mkdir(parents=True, exist_ok=True)
        import io

        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=QUALITY, optimize=True)  # no exif= : nothing of the source metadata
        paths.atomic_write(out, buf.getvalue())
        buf = io.BytesIO()
        _grid(im, int(grid)).save(buf, "JPEG", quality=80, optimize=True)
        paths.atomic_write(out_grid, buf.getvalue())
    res = {"image": paths.jpath(out), "grid": paths.jpath(out_grid), "size": list(im.size),
           "source_size": src_size, "scale": round(scale, 4), "kb": round(out.stat().st_size / 1024, 1),
           "grid_step_px": int(grid)}
    if had_exif:
        res["exif_removed"] = True
    if reused:
        res["reused"] = True
    res["next"] = ("blender.call ref.plane {\"view\": \"left\", \"image\": \"<image>\", \"points\": [[x1, y1], "
                   "[x2, y2]], \"distance\": <metres>} - read the pixels from the grid copy")
    return res
