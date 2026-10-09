"""Reference photos for a modelling session: ``ref.import`` (small, EXIF-free JPEGs) and ``ref.board`` (one sheet).

A photo is rotated by its EXIF orientation, shrunk to at most 1,600 px and saved as JPEG without any metadata
(location, camera) into ``<project>/refs/`` (or ``work/refs/``), with a small sidecar ``<stem>.ref.json`` that keeps
its ``view``. References are DESCRIBED, not measured: global proportions come from the spec sheet, the design
features of every photo go into ``refs/features.md`` (``docs/agent/style/references.md``). Only a true elevation
(``view`` side, front, rear or top: far away, both wheels round) may go on ``ref.plane`` behind the model, scaled by
the object's real length, or be compared with ``look.silhouette`` / ``blender preview --ref``. A labelled pixel
grid copy is opt-in (``grid=true``) and never needed for three-quarter or detail photos. Names carry a hash of
the source: a repeat writes nothing new.

``ref.board`` puts 2-12 references into one labelled JPEG (at most about 1 megapixel) for a review round.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from ..core import paths
from ..core.errors import SatkError, require_module

__all__ = ["MAX_PX", "GRID_PX", "VIEWS", "import_photo", "board"]

MAX_PX = 1600
GRID_PX = 100
QUALITY = 88
#: Views of a reference photo: true elevations (may be scaled and compared) and photos that are only described.
VIEWS = ("side", "front", "rear", "top", "3q", "detail")
ELEVATIONS = ("side", "front", "rear", "top")
_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")
_DOC = "docs/agent/style/references.md"
BOARD_PIXELS = 1_000_000


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


def next_hint(view: str | None, image: str = "<image>") -> str:
    """What to do with a reference of ``view``: describe it; scale and compare only a true elevation."""
    if view in ELEVATIONS:
        plane_view = {"side": "left"}.get(view, view)
        return (f"a true elevation: write its design features into refs/features.md ({_DOC}); behind the model: "
                f"blender.call ref.plane {{\"view\": \"{plane_view}\", \"image\": \"{image}\", \"length\": <the real "
                "length (width for front/rear) in m>}; outline check: blender preview session:<name> --ref "
                f"{image} --ref-view {view if view != 'top' else 'side'} or blender.call look.silhouette")
    if view in ("3q", "detail"):
        return (f"describe, do not measure: write the design features of this photo into refs/features.md ({_DOC}); "
                "never read coordinates, fit a camera or project pixels from a three-quarter or detail photo")
    return (f"describe the design features into refs/features.md ({_DOC}); give --view side|front|rear|top for a "
            "true elevation (far away, wheels round) or 3q|detail for any other photo")


def import_photo(src: str | os.PathLike, *, project: str | None = None, max_px: int = MAX_PX, grid: bool = False,
                 grid_step: int = GRID_PX, view: str | None = None) -> dict:
    """Prepare one photo; returns ``{image, size, source_size, scale, view?, grid?, exif_removed?, next}``."""
    require_module("PIL", pip="Pillow", purpose="ref.import")
    from PIL import Image, ImageOps

    sp = Path(os.path.abspath(os.fspath(src)))
    if not sp.is_file():
        raise SatkError("NOT_FOUND", f"no image {paths.jpath(sp)}")
    if sp.suffix.lower() not in _EXTS:
        raise SatkError("BAD_PARAMS", f"{sp.name}: not a photo ({', '.join(_EXTS)})")
    if not 256 <= int(max_px) <= 4096:
        raise SatkError("BAD_PARAMS", "max_px must be 256-4096")
    if not 10 <= int(grid_step) <= 1000:
        raise SatkError("BAD_PARAMS", "grid_step must be 10-1000 px")
    if view is not None and view not in VIEWS:
        raise SatkError("BAD_PARAMS", f"view must be one of {', '.join(VIEWS)}, got {view!r}")
    raw = sp.read_bytes()
    h8 = hashlib.sha256(raw + f"|{max_px}".encode()).hexdigest()[:8]
    if project:
        from .project import load

        pdir, _data = load(project)
        dest = pdir / "refs"
    else:
        dest = paths.work("refs")
    stem = f"{_stem(sp)}-{h8}"
    out, out_grid, side = dest / f"{stem}.jpg", dest / f"{stem}.grid.jpg", dest / f"{stem}.ref.json"
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
    reused = out.is_file()
    import io

    if not reused:
        paths.ensure_writable(dest).mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=QUALITY, optimize=True)  # no exif= : nothing of the source metadata
        paths.atomic_write(out, buf.getvalue())
    meta = {"view": view, "source": sp.name, "size": list(im.size)}
    if view is not None or not side.is_file():
        old = side.read_text(encoding="utf-8") if side.is_file() else None
        new = json.dumps(meta, sort_keys=True) + "\n"
        if old != new:
            paths.ensure_writable(dest).mkdir(parents=True, exist_ok=True)
            paths.atomic_write(side, new)
    else:
        try:
            view = json.loads(side.read_text(encoding="utf-8")).get("view")
        except (OSError, ValueError):
            view = None
    res = {"image": paths.jpath(out), "size": list(im.size), "source_size": src_size, "scale": round(scale, 4),
           "kb": round(out.stat().st_size / 1024, 1)}
    if view:
        res["view"] = view
    if grid:
        gstem = f"{stem}.grid{int(grid_step)}" if int(grid_step) != GRID_PX else f"{stem}.grid"
        out_grid = dest / f"{gstem}.jpg"
        if not out_grid.is_file():
            buf = io.BytesIO()
            _grid(im, int(grid_step)).save(buf, "JPEG", quality=80, optimize=True)
            paths.atomic_write(out_grid, buf.getvalue())
        res["grid"] = paths.jpath(out_grid)
        res["grid_step_px"] = int(grid_step)
    if had_exif:
        res["exif_removed"] = True
    if reused:
        res["reused"] = True
    res["next"] = next_hint(view, res["image"])
    return res


def _refs_of(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*.jpg") if not re.search(r"\.grid\d*$", p.stem) and not p.stem.startswith("board"))


def _view_of(p: Path) -> str | None:
    side = p.with_name(p.stem + ".ref.json")
    try:
        return json.loads(side.read_text(encoding="utf-8")).get("view")
    except (OSError, ValueError):
        return None


def board(images: list[str] | None = None, *, project: str | None = None, labels: list[str] | None = None,
          cols: int | None = None, max_pixels: int = BOARD_PIXELS) -> dict:
    """One labelled JPEG of the references (default: every reference of the project / ``work/refs``)."""
    require_module("PIL", pip="Pillow", purpose="ref.board")
    from PIL import Image, ImageDraw, ImageFont

    if project:
        from .project import load

        folder = load(project)[0] / "refs"
    else:
        folder = paths.work("refs")
    files = [Path(os.path.abspath(x)) for x in images] if images else _refs_of(folder)
    if not files:
        raise SatkError("NOT_FOUND", f"no reference photos in {paths.jpath(folder)}",
                        hint="satk ref import <photo> --view side|front|rear|top|3q|detail")
    if len(files) > 12:
        raise SatkError("BAD_PARAMS", f"{len(files)} images: a board takes at most 12")
    for f in files:
        if not f.is_file():
            raise SatkError("NOT_FOUND", f"no image {paths.jpath(f)}")
    if labels is not None and len(labels) != len(files):
        raise SatkError("BAD_PARAMS", f"{len(labels)} labels for {len(files)} images")
    n = len(files)
    cols = int(cols) if cols else (1 if n == 1 else 2 if n <= 4 else 3)
    rows = (n + cols - 1) // cols
    # tiles of one size: the board stays under max_pixels
    import math

    tile_w = int(math.sqrt(max_pixels / (cols * rows * 0.75)))
    tile_h = int(tile_w * 0.75)
    bar = 18
    sheet = Image.new("RGB", (cols * tile_w, rows * (tile_h + bar)), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    used = []
    for i, f in enumerate(files):
        r, c = divmod(i, cols)
        with Image.open(f) as im:
            im = im.convert("RGB")
            im.thumbnail((tile_w, tile_h), Image.LANCZOS)
            x = c * tile_w + (tile_w - im.width) // 2
            y = r * (tile_h + bar) + bar + (tile_h - im.height) // 2
            sheet.paste(im, (x, y))
        view = _view_of(f)
        lab = labels[i] if labels else f"{i + 1}. {view or 'view?'} - {f.stem[:40]}"
        d.text((c * tile_w + 4, r * (tile_h + bar) + 3), lab, fill=(255, 230, 120), font=font)
        used.append([i + 1, view or "", paths.jpath(f)])
    import io

    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=85, optimize=True)
    data = buf.getvalue()
    h8 = hashlib.sha256(data).hexdigest()[:8]
    out = folder / f"board-{h8}.jpg"
    if not out.is_file():
        paths.ensure_writable(folder).mkdir(parents=True, exist_ok=True)
        paths.atomic_write(out, data)
    return {"file": paths.jpath(out), "size": [sheet.width, sheet.height], "kb": round(len(data) / 1024, 1),
            "cols": ["n", "view", "image"], "rows": used,
            "next": f"describe each photo's design features in refs/features.md ({_DOC}); review the model next to "
                    "this board, not against its pixels"}
