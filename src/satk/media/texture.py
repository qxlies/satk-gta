"""Texture pixels and the content-addressed PNG cache (SPEC §4.4, §3.6). Owner WP-04.

* ``work/cache/tex/<hh>/<pix>.png`` -- mip0 of one unique pixel content (``pix:<24 hex>``);
* ``work/cache/tex/<hh>/<pix>@<size>.png`` -- the same, longest side scaled down to ``size``.

Paths are derived from the pixel hash only, so a file is computed once and never rewritten
(different TXDs with the same pixels share it). Pixels are read straight from the game files
through the index (:class:`~satk.index.api.TexRef`: archive path + absolute mip0 offset, via
``paths.open_ro``) and decoded with :func:`satk.formats.dxt.decode_rgba` (``backend="auto"``:
Pillow ``bcn`` -> numpy -> pure Python).

:func:`export_all` fills the cache for every unique image of a profile with a process pool.
"""

from __future__ import annotations

import math
import os
import time
from concurrent.futures import as_completed
from pathlib import Path
from typing import Iterable, Sequence

from ..core.errors import SatkError
from ..core.ids import Sid
from ..core.paths import jpath, open_ro, work
from ..core.procpool import pool
from ..core.registry import report_progress
from ..formats.rw import FormatError
from ..formats.txd import TexInfo
from . import png as _png
from .raster import fit_size, resize_rgba

__all__ = [
    "TEXTURE_KINDS", "MAX_SIZE", "cache_dir", "cache_path", "pix_hex", "tex_info", "Reader", "decode_ref", "dxt_backend",
    "png_for_ref", "texture_png", "resolve_refs", "describe", "export_all", "list_pix",
]

#: SID kinds accepted by :func:`resolve_refs` (``tex``/``pix`` = one texture, ``txd``/``model`` = several).
TEXTURE_KINDS = ("tex", "pix", "txd", "model")
#: Largest ``size`` accepted (the biggest vanilla texture is 1024 px).
MAX_SIZE = 4096
_PAL_BYTES = {"PAL8": 256 * 4, "PAL4": 32 * 4}  # RW stores 256 / 32 RGBA entries
_SQL_PAGE = 500
#: Below this many textures to write, ``export_all`` works in-process (spawning workers costs ~1 s on Windows).
POOL_MIN = 64


# --------------------------------------------------------------------------- cache paths


def cache_dir() -> Path:
    """``<work>/cache/tex`` (created)."""
    return work("cache", "tex")


def pix_hex(pix: str) -> str:
    """``"pix:3fa2…"`` or ``"3fa2…"`` -> the 24-hex key (validated by :class:`Sid`)."""
    s = str(pix)
    return Sid.parse(s if s.startswith("pix:") else f"pix:{s}").key


def cache_path(pix: str, size: int | None = None, base: Path | None = None) -> Path:
    """Cache file of a pixel hash (``size`` -> the ``@<size>`` variant). Nothing is created."""
    h = pix_hex(pix)
    root = base if base is not None else cache_dir()
    name = f"{h}.png" if not size else f"{h}@{int(size)}.png"
    return root / h[:2] / name


# --------------------------------------------------------------------------- decoding


def tex_info(ref) -> TexInfo:
    """A :class:`~satk.formats.txd.TexInfo` for a :class:`~satk.index.api.TexRef` (offsets relative to the
    buffers returned by :class:`Reader`: mip0 at 0, palette separate)."""
    pal = _PAL_BYTES.get(ref.d3dfmt, 0)
    unsupported = ref.platform not in (8, 9) or ref.w <= 0 or ref.h <= 0
    return TexInfo(
        idx=0, name=str(ref.sid).rsplit("/", 1)[-1], mask="", platform=ref.platform, filter=0, uaddr=0, vaddr=0,
        raster_fmt=ref.raster_fmt, d3dfmt=ref.d3dfmt, w=ref.w, h=ref.h, depth=0, levels=ref.levels,
        alpha=bool(ref.alpha), mip0_off=0, mip0_size=ref.data_size, pal_off=0 if pal else None, pal_size=pal,
        unsupported=unsupported,
    )


class Reader:
    """Reads byte ranges of game files through ``open_ro``, keeping the handles open (one per file)."""

    def __init__(self) -> None:
        self._files: dict[str, object] = {}

    def read(self, path: Path, off: int, size: int, what: str) -> bytes:
        key = os.fspath(path)
        f = self._files.get(key)
        if f is None:
            try:
                f = open_ro(path)
            except FileNotFoundError:
                raise SatkError("NOT_FOUND", f"game file of {what} not found: {jpath(path)}",
                                hint="satk index build (the game files changed?)") from None
            self._files[key] = f
        f.seek(off)  # type: ignore[attr-defined]
        data = f.read(size)  # type: ignore[attr-defined]
        if len(data) != size:
            raise SatkError("NOT_FOUND", f"{what}: short read ({len(data)} of {size} bytes) in {jpath(path)}",
                            hint="the index is stale: satk index build")
        return data

    def close(self) -> None:
        for f in self._files.values():
            try:
                f.close()  # type: ignore[attr-defined]
            except OSError:  # pragma: no cover
                pass
        self._files.clear()

    def __enter__(self) -> "Reader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def dxt_backend() -> str:
    """DXT decoder for this process: ``formats.dxt`` ``auto`` (Pillow -> numpy -> python), or without
    Pillow when ``SATK_MEDIA_NO_PILLOW=1`` (measures the fallback path, SPEC §5.3 WP-04 acc. 2)."""
    from ..formats.dxt import available_backends, resolve_backend

    if _png.backend() == "zlib":
        return next(b for b in available_backends() if b != "pillow")
    return resolve_backend("auto")


def decode_ref(ref, reader: Reader | None = None, backend: str | None = None) -> tuple[int, int, bytes]:
    """Decode mip0 of a :class:`~satk.index.api.TexRef` -> ``(w, h, rgba)``.

    Raises ``UNSUPPORTED`` for console platforms / unknown formats, ``NOT_FOUND`` if the game file is gone.
    """
    from ..formats.dxt import decode_rgba

    if backend is None:
        backend = dxt_backend()
    if ref.d3dfmt == "PNG":  # a loose PNG file (refs_from_path)
        return _png.load_rgba(ref.path)

    t = tex_info(ref)
    if t.unsupported:
        raise SatkError("UNSUPPORTED", f"{ref.sid}: platform {ref.platform} / {ref.d3dfmt} has no PC pixels",
                        data={"platform": ref.platform, "fmt": ref.d3dfmt})
    own = reader is None
    rd = reader or Reader()
    try:
        mip0 = rd.read(ref.path, ref.data_off, ref.data_size, str(ref.sid))
        pal = rd.read(ref.path, ref.pal_off, t.pal_size, str(ref.sid)) if t.pal_size and ref.pal_off is not None \
            else None
    finally:
        if own:
            rd.close()
    try:
        return t.w, t.h, decode_rgba(t, mip0, pal, backend=backend)
    except FormatError as e:
        raise SatkError("UNSUPPORTED", f"cannot decode {ref.sid} ({ref.d3dfmt} {ref.w}x{ref.h}): {e}",
                        data={"fmt": ref.d3dfmt}) from None


def _check_size(size: int | None) -> int | None:
    if size is None or size == 0:
        return None
    n = int(size)
    if n < 1 or n > MAX_SIZE:
        raise SatkError("BAD_PARAMS", f"size must be 1..{MAX_SIZE} (0 = full size), got {size}")
    return n


def png_for_ref(ref, size: int | None = 256, reader: Reader | None = None) -> Path:
    """The cached PNG of a texture (written on first use). ``size`` = longest side, ``None`` = mip0."""
    lim = _check_size(size)
    if lim is not None and max(ref.w, ref.h) > lim:
        p = cache_path(ref.pix, lim)
        if p.is_file():
            return p
        w, h, rgba = decode_ref(ref, reader)
        nw, nh = fit_size(w, h, lim)
        _png.write_file(p, _png.encode(nw, nh, resize_rgba(w, h, rgba, nw, nh)))
        return p
    p = cache_path(ref.pix)
    if p.is_file():
        return p
    w, h, rgba = decode_ref(ref, reader)
    _png.write_file(p, _png.encode(w, h, rgba))
    return p


# --------------------------------------------------------------------------- SIDs -> textures


def describe(ref) -> str:
    """Legend text of a texture: ``"128x128 DXT1"``."""
    return f"{ref.w}x{ref.h} {ref.d3dfmt}"


FILE_EXTS = (".txd", ".png")


def is_texture_path(raw: str) -> bool:
    """True for the path of a ``.txd`` or ``.png`` file (a texture file of one's own, not a SID)."""
    s = str(raw).strip().strip('"')
    return s.lower().endswith(FILE_EXTS) and ("/" in s or "\\" in s or Path(s).is_file())


def refs_from_path(raw: str) -> list:
    """Texture refs of a loose ``.txd`` (its PC textures, TXD order) or ``.png`` file (read with ``open_ro``)."""
    import hashlib

    from ..formats.txd import parse_txd, texture_hash
    from ..index.api import TexRef

    p = Path(str(raw).strip().strip('"')).expanduser()
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"no texture file at {jpath(p)}", hint="a .txd or .png file, or a tex:/txd: SID")
    with open_ro(p) as f:
        data = f.read()
    if p.suffix.lower() == ".png":
        w, h, rgba = _png.load_rgba(p)
        pix = "pix:" + hashlib.blake2b(rgba + f"{w}x{h}".encode(), digest_size=12).hexdigest()
        return [TexRef(f"file:{p.name.lower()}", pix, p, 0, len(data), None, "PNG", 0, 0, w, h, 1,
                       not _png.is_opaque(rgba))]
    try:
        txd = parse_txd(data)
    except FormatError as e:
        raise SatkError("UNSUPPORTED", f"{p.name}: not a readable TXD: {e}", hint=f"satk formats dump {jpath(p)}") from None
    out = []
    for t in txd.textures:
        out.append(TexRef(f"file:{p.name.lower()}/{t.name.lower()}", "pix:" + texture_hash(data, t).hex(), p,
                          t.mip0_off, t.mip0_size, t.pal_off, t.d3dfmt, t.raster_fmt, t.platform, t.w, t.h,
                          t.levels, bool(t.alpha)))
    return out


def resolve_refs(db, ids: Iterable[str]) -> tuple[list, list[str]]:
    """Expand SIDs to texture refs: ``tex:``/``pix:`` -> 1, ``txd:`` -> its textures (TXD order),
    ``model:`` -> its resolved material textures; a ``.txd``/``.png`` file path -> its textures.
    Duplicates are dropped (first occurrence wins).

    Returns ``(refs, warnings)``; raises ``BAD_ID`` for other kinds and ``NOT_FOUND`` from the index.
    """
    refs: list = []
    seen: set[str] = set()
    warn: list[str] = []
    for raw in ids:
        if is_texture_path(raw):
            for r in refs_from_path(raw):
                if r.sid not in seen:
                    seen.add(r.sid)
                    refs.append(r)
            continue
        s = Sid.parse(str(raw).strip())
        if s.kind not in TEXTURE_KINDS:
            raise SatkError("BAD_ID", f"expected a tex:/pix:/txd:/model: SID, got {str(s)!r}",
                            hint="satk asset find <name> --kind tex", data={"kinds": list(TEXTURE_KINDS)})
        if s.kind in ("tex", "pix"):
            got = [db.texture_ref(str(s))]
        else:
            got = list(db.textures_of(str(s)))
            if not got:
                warn.append(f"EMPTY: {s} has no textures")
        for r in got:
            if r.sid in seen:
                continue
            seen.add(r.sid)
            refs.append(r)
    return refs, warn


def texture_png(sid: str, size: int | None = 256, profile: str = "vanilla") -> Path:
    """PNG of one texture (``tex:txd/name`` or ``pix:<hex>``) from the cache, written on first use.

    Args:
        sid: texture SID.
        size: longest side in px (smaller textures are not upscaled); ``None``/0 = mip0 as is.
        profile: index profile.
    """
    from ..index.api import open_index

    s = Sid.parse(sid)
    if s.kind not in ("tex", "pix"):
        raise SatkError("BAD_ID", f"texture_png needs a tex:/pix: SID, got {str(s)!r}",
                        hint="for txd:/model: use contact_sheet() or textures_of()")
    return png_for_ref(open_index(profile).texture_ref(str(s)), size)


# --------------------------------------------------------------------------- export_all


def list_pix(db) -> list[str]:
    """All unique pixel hashes (24 hex, sorted) of an index (``image`` table, paged SQL)."""
    total = int(db.query("SELECT count(*) FROM image")["rows"][0][0])
    out: list[str] = []
    while len(out) < total:
        env = db.query("SELECT lower(hex(hash)) FROM image ORDER BY hash LIMIT ? OFFSET ?",
                       [_SQL_PAGE, len(out)], limit=_SQL_PAGE)
        rows = env["rows"]
        if not rows:
            break
        out.extend(str(r[0]) for r in rows)
    return out


def _export_chunk(items: Sequence[tuple[str, object]], base: str, backend: str) -> tuple[int, int, list[list[str]]]:
    """Worker: decode + write the mip0 PNGs of ``items`` [(hex, TexRef)] -> (written, skipped, errors)."""
    root = Path(base)
    written = skipped = 0
    errors: list[list[str]] = []
    with Reader() as rd:
        for h, ref in items:
            p = root / h[:2] / f"{h}.png"
            if p.is_file():
                skipped += 1
                continue
            try:
                w, hh, rgba = decode_ref(ref, rd, backend)
                _png.write_file(p, _png.encode(w, hh, rgba))
                written += 1
            except SatkError as e:
                errors.append([str(ref.sid), e.code, e.msg])
            except (OSError, ValueError) as e:  # pragma: no cover - disk full, corrupt data ...
                errors.append([str(ref.sid), "INTERNAL", f"{type(e).__name__}: {e}"])
    return written, skipped, errors


def _chunks(items: list, n: int) -> list[list]:
    return [items[i:i + n] for i in range(0, len(items), n)]


def export_all(jobs: int = 12, profile: str = "vanilla") -> dict:
    """Write the mip0 PNG of every unique image of ``profile`` into the cache.

    Returns ``{"written", "skipped", "seconds", "backend", ...}``; ``written + skipped`` equals the number
    of unique images unless some failed (``failed`` + first ``errors``) or have no PC pixels (``unsupported``).
    """
    from ..index.api import open_index

    t0 = time.perf_counter()
    db = open_index(profile)
    n_jobs = int(jobs) if jobs else (os.cpu_count() or 1)
    n_jobs = max(1, min(n_jobs, 61))  # Windows limit of ProcessPoolExecutor
    base = cache_dir()
    dxt = dxt_backend()
    hexes = list_pix(db)
    todo_hex = [h for h in hexes if not (base / h[:2] / f"{h}.png").is_file()]
    skipped = len(hexes) - len(todo_hex)
    t_list = time.perf_counter() - t0

    todo: list[tuple[str, object]] = []
    unsupported = 0
    for h in todo_hex:
        ref = db.texture_ref(f"pix:{h}")
        if ref.platform not in (8, 9) or ref.w <= 0 or ref.h <= 0:
            unsupported += 1
            continue
        todo.append((h, ref))
    todo.sort(key=lambda it: (os.fspath(it[1].path).lower(), it[1].data_off))  # sequential reads
    t_resolve = time.perf_counter() - t0 - t_list

    written = 0
    errors: list[list[str]] = []
    total = len(todo)
    report_progress(0, total, "export textures")
    if total and (n_jobs == 1 or total < POOL_MIN):
        w_, s_, e_ = _export_chunk(todo, os.fspath(base), dxt)
        written, skipped, errors = written + w_, skipped + s_, errors + e_
        report_progress(total, total, "export textures")
    elif total:
        size = max(16, math.ceil(total / (n_jobs * 8)))
        done = 0
        with pool(n_jobs) as ex:
            futs = {ex.submit(_export_chunk, c, os.fspath(base), dxt): len(c) for c in _chunks(todo, size)}
            for f in as_completed(futs):
                w_, s_, e_ = f.result()
                written, skipped, errors = written + w_, skipped + s_, errors + e_
                done += futs[f]
                report_progress(done, total, "export textures")
    out = {
        "written": written, "skipped": skipped, "images": len(hexes), "seconds": round(time.perf_counter() - t0, 2),
        "backend": dxt, "png": _png.backend(), "jobs": n_jobs, "dir": jpath(base),
        "timing": {"list": round(t_list, 2), "resolve": round(t_resolve, 2)},
    }
    if unsupported:
        out["unsupported"] = unsupported
    if errors:
        errors.sort()
        out["failed"] = len(errors)
        out["errors"] = errors[:20]
    return out
