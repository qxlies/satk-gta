"""128 px thumbnails of the catalog (owner M2-09), taken from the media/model3d caches.

* textures: the mip0 PNG of the content-addressed texture cache (``work/cache/tex/<hh>/<pix>.png``,
  written by :func:`satk.media.texture.png_for_ref` when missing), scaled to 128 px ->
  ``<catalog>/t/x/<hh>/<pix>.<ext>`` (named by pixel hash, so a rebuild converts only new images);
* models: the soft-renderer preview of :func:`satk.model3d.batch.render_chunk` (1 view, 128 px, cached in
  ``work/cache/model``) -> ``<catalog>/t/m/<id >> 7>/<id>.<ext>``. The cache key of each thumbnail is kept
  in the catalog manifest; when the index hash and renderer version are unchanged, existing files are
  reused without touching the model cache at all.

``<ext>`` is ``webp`` (Pillow with WebP; ~2 KB per texture, ~1 KB per model) or ``png`` (stdlib fallback).
Big batches run in a process pool (``spawn``), small ones in-process. Pillow is imported lazily.
"""

from __future__ import annotations

import io
import os
import time
from concurrent.futures import as_completed
from pathlib import Path

from ..core.errors import SatkError
from ..core.registry import report_progress
from .files import write_if_changed

__all__ = ["THUMB", "QUALITY", "POOL_MIN", "image_format", "encode_thumb", "texture_thumbs", "model_thumbs",
           "model_thumb_path", "texture_thumb_path"]

#: Longest side of a thumbnail, px.
THUMB = 128
#: WebP quality.
QUALITY = 80
#: Below this many items a batch runs in-process (spawning workers costs ~1 s on Windows).
POOL_MIN = 64
_CHUNK = 40


def image_format(fmt: str = "auto") -> str:
    """``webp`` or ``png`` for ``auto|webp|png`` (``webp`` needs Pillow built with WebP)."""
    if fmt not in ("auto", "webp", "png"):
        raise SatkError("BAD_PARAMS", f"thumbnail format must be auto, webp or png, got {fmt!r}")
    if fmt == "png":
        return "png"
    try:
        from PIL import features

        ok = bool(features.check("webp"))
    except ImportError:
        ok = False
    if fmt == "webp" and not ok:
        raise SatkError("UNSUPPORTED", "WebP needs Pillow with WebP support", hint="--fmt png (or --fmt auto)")
    return "webp" if ok else "png"


def texture_thumb_path(root: Path, pix: str, ext: str) -> Path:
    return root / "t" / "x" / pix[:2] / f"{pix}.{ext}"


def model_thumb_path(root: Path, model_id: int, ext: str) -> Path:
    return root / "t" / "m" / str(int(model_id) >> 7) / f"{int(model_id)}.{ext}"


def encode_thumb(src: Path, ext: str) -> bytes:
    """A PNG file -> thumbnail bytes (longest side <= :data:`THUMB`, never upscaled)."""
    from ..media.raster import fit_size

    if ext == "webp":
        from PIL import Image

        with Image.open(src) as im:
            im.load()
            img = im.convert("RGBA")
        if img.getchannel("A").getextrema()[0] == 255:
            img = img.convert("RGB")
        nw, nh = fit_size(img.width, img.height, THUMB)
        if (nw, nh) != img.size:
            img = img.resize((nw, nh), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=QUALITY, method=4)
        return buf.getvalue()
    from ..media import png as _png
    from ..media.raster import resize_rgba

    w, h, rgba = _png.load_rgba(src)
    nw, nh = fit_size(w, h, THUMB)
    if (nw, nh) == (w, h):
        return Path(src).read_bytes()
    return _png.encode(nw, nh, resize_rgba(w, h, rgba, nw, nh))


# --------------------------------------------------------------------------- pool plumbing


def _run(worker, chunks: list, args: tuple, jobs: int, total: int, what: str) -> list:
    """Run ``worker(chunk, *args)`` over ``chunks`` (pool when big enough); results concatenated."""
    out: list = []
    done = 0
    report_progress(0, total, what)
    if jobs <= 1 or total < POOL_MIN or len(chunks) <= 1:
        for ch in chunks:
            out.extend(worker(ch, *args))
            done += len(ch)
            report_progress(done, total, what)
        return out
    from ..core.config import SRC_ROOT

    old = os.environ.get("PYTHONPATH")
    src = str(SRC_ROOT)
    os.environ["PYTHONPATH"] = src + (os.pathsep + old if old else "")
    try:
        from ..core.procpool import pool

        with pool(max(1, min(jobs, 61, len(chunks))), spawn=True) as ex:
            futs = {ex.submit(worker, ch, *args): len(ch) for ch in chunks}
            for f in as_completed(futs):
                out.extend(f.result())
                done += futs[f]
                report_progress(done, total, what)
    finally:
        if old is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = old
    return out


def _chunks(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


# --------------------------------------------------------------------------- textures


def tex_worker(items: list, root: str, ext: str) -> list[tuple]:
    """Worker: ``[(pix, TexRef)]`` -> ``[(pix, error | None)]`` (writes ``t/x/<hh>/<pix>.<ext>``)."""
    from ..media.texture import Reader, cache_path, png_for_ref

    out = []
    base = Path(root)
    with Reader() as rd:
        for pix, ref in items:
            try:
                src = cache_path(pix)
                if not src.is_file():
                    src = png_for_ref(ref, None, rd)
                write_if_changed(texture_thumb_path(base, pix, ext), encode_thumb(src, ext))
                out.append((pix, None))
            except SatkError as e:
                out.append((pix, f"{e.code}: {e.msg}"))
            except (OSError, ValueError) as e:  # corrupt cache file, disk full ...
                out.append((pix, f"INTERNAL: {type(e).__name__}: {e}"))
    return out


def texture_thumbs(db, images: list[str], root: Path, ext: str, jobs: int) -> tuple[set[str], dict]:
    """Thumbnails of every image hash; returns ``(hashes with a thumbnail, stats)``."""
    t0 = time.perf_counter()
    ok: set[str] = set()
    todo: list[tuple[str, object]] = []
    errors: list[list[str]] = []
    unsupported = 0
    for pix in images:
        if texture_thumb_path(root, pix, ext).is_file():
            ok.add(pix)
            continue
        try:
            ref = db.texture_ref(f"pix:{pix}")
        except SatkError as e:
            errors.append([f"pix:{pix}", f"{e.code}: {e.msg}"])
            continue
        if ref.platform not in (8, 9) or ref.w <= 0 or ref.h <= 0:
            unsupported += 1
            continue
        todo.append((pix, ref))
    kept = len(ok)
    todo.sort(key=lambda it: (os.fspath(it[1].path).lower(), it[1].data_off))
    res = _run(tex_worker, _chunks(todo, max(16, min(200, -(-len(todo) // max(1, jobs * 6))))),
               (os.fspath(root), ext), jobs, len(todo), "catalog: texture thumbnails")
    for pix, err in res:
        if err is None:
            ok.add(pix)
        else:
            errors.append([f"pix:{pix}", err])
    errors.sort()
    stats = {"kept": kept, "written": len(ok) - kept, "failed": len(errors),
             "seconds": round(time.perf_counter() - t0, 2)}
    if unsupported:
        stats["unsupported"] = unsupported
    if errors:
        stats["errors"] = errors[:20]
    return ok, stats


# --------------------------------------------------------------------------- models


def model_worker(ids: list[int], root: str, ext: str, profile: str) -> list[tuple]:
    """Worker: render (or reuse) 128 px previews and convert them -> ``[(id, cache key | None, error)]``."""
    from ..model3d.batch import render_chunk

    out = []
    base = Path(root)
    for mid, _name, _status, file, err, _ms, _blank in render_chunk(ids, 1, THUMB, profile, False, 25.0, None):
        if not file:
            out.append((mid, None, err or "no image"))
            continue
        try:
            write_if_changed(model_thumb_path(base, mid, ext), encode_thumb(Path(file), ext))
            out.append((mid, Path(file).stem, None))
        except (OSError, ValueError) as e:
            out.append((mid, None, f"INTERNAL: {type(e).__name__}: {e}"))
    return out


def render_stamp(profile: str, index_hash: str, ext: str) -> str:
    """What the model thumbnails depend on besides the cache key: index content + renderer + format."""
    from ..model3d.softrender import RENDER_VERSION

    return f"{profile}/{index_hash}/r{RENDER_VERSION}/{THUMB}/{ext}"


def model_thumbs(models: list[dict], root: Path, ext: str, jobs: int, profile: str, *,
                 stamp: str, previous: dict) -> tuple[dict[int, str], dict]:
    """Thumbnails of every model with a DFF; returns ``({model id: cache key}, stats)``.

    ``previous`` = ``{"stamp": ..., "keys": {"<id>": key}}`` of the last build: with the same ``stamp`` an
    existing file is reused as is.
    """
    t0 = time.perf_counter()
    same = previous.get("stamp") == stamp
    old = previous.get("keys") or {}
    keys: dict[int, str] = {}
    todo: list[dict] = []
    for m in models:
        if m.get("dff") is None:
            continue
        k = old.get(str(m["id"])) if same else None
        if k and model_thumb_path(root, m["id"], ext).is_file():
            keys[m["id"]] = k
        else:
            todo.append(m)
    kept = len(keys)
    # models sharing a TXD land in one chunk: a worker decodes it once
    todo.sort(key=lambda m: ((m.get("txd") or "").lower(), m["id"]))
    ids = [m["id"] for m in todo]
    n = max(4, min(_CHUNK, -(-len(ids) // max(1, jobs * 4))))
    res = _run(model_worker, _chunks(ids, n), (os.fspath(root), ext, profile), jobs, len(ids),
               "catalog: model thumbnails")
    errors = []
    for mid, key, err in sorted(res, key=lambda r: r[0]):
        if key:
            keys[mid] = key
        else:
            errors.append([f"model:{mid}", err])
    stats = {"kept": kept, "converted": len(ids) - len(errors), "failed": len(errors),
             "seconds": round(time.perf_counter() - t0, 2)}
    if errors:
        stats["errors"] = errors[:20]
    return keys, stats
