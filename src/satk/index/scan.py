"""Step 3 of ``index build``: scan blobs (worker processes). Owner: WP-03.

A job is a slice of one source file (an IMG archive or a loose asset) as a list of
:class:`BlobJob`; :func:`scan_job` reads the bytes READ-ONLY (``open_ro``), computes
``rw_size`` and the blob hash, and parses the payload by extension:

* ``txd`` -> TXD header, textures (absolute mip0/palette offsets), pixel hashes;
* ``dff`` -> ``scan_dff`` summary (frames, geometries, materials, 2DFX) + embedded COL models;
* ``col`` -> collision models; ``ifp`` -> animations; ``ipl`` -> binary IPL placements/cars.

Results are plain picklable tuples/dataclasses. Parsers: ``satk.formats`` (F1-F3, WP-02) plus
:mod:`satk.index.rwx` for the count of empty texture names.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any

from ..core.paths import open_ro
from ..formats.col import iter_col
from ..formats.dff import find_embedded_col, scan_dff
from ..formats.dxt import mean_rgba
from ..formats.ifp import parse_ifp
from ..formats.ipl import parse_ipl_binary
from ..formats.rw import FormatError, rw_payload_size
from ..formats.txd import palette_bytes, parse_txd, texture_hash
from . import rwx

__all__ = ["BlobJob", "BlobResult", "scan_job", "impl_names", "RW_EXTS", "BLOB_HASH_SIZE"]

#: Extensions whose payload is a RenderWare stream (``blob.rw_size``).
RW_EXTS = frozenset({"dff", "txd"})
BLOB_HASH_SIZE = 16


@dataclass(frozen=True, slots=True)
class BlobJob:
    """One blob to scan: ``abs_off``/``size`` inside ``path``."""

    key: int          # caller's blob id
    path: str
    ext: str
    abs_off: int
    size: int
    ns: str


@dataclass(slots=True)
class BlobResult:
    key: int
    rw_size: int | None = None
    hash: bytes | None = None
    error: str | None = None
    txd: tuple | None = None            # (rw_version, device_id, count)
    textures: list = field(default_factory=list)   # (idx,name,mask,platform,raster,d3dfmt,w,h,depth,levels,alpha,filter,u,v,data_off,data_size,pal_off,hash,nbytes,mean)
    dff: Any = None                     # DffInfo
    cols: list = field(default_factory=list)       # ColModel (from .col or embedded)
    ifp: tuple | None = None            # (format, pack, [(anim, bones, frames)])
    ipl: tuple | None = None            # (insts, cars)
    empty_tex: int = 0                  # DFF: Texture chunks with an empty name
    warn: str | None = None             # non-fatal problem (the blob is still indexed)


# --------------------------------------------------------------------------- implementations


def impl_names() -> dict[str, str]:
    """Which module implements each parser (recorded in ``meta.formats_impl``)."""
    return {"scan_dff": scan_dff.__module__, "iter_col": iter_col.__module__, "parse_ifp": parse_ifp.__module__,
            "mean_rgba": mean_rgba.__module__, "empty_texture_names": rwx.__name__}


# --------------------------------------------------------------------------- scanning


def _scan_txd(buf: bytes, r: BlobResult, base: int) -> None:
    t = parse_txd(buf)
    r.txd = (t.rw_version, t.device_id, t.count)
    for x in t.textures:
        h = texture_hash(buf, x) if not x.unsupported else hashlib.blake2b(b"", digest_size=12).digest()
        m = None
        if not x.unsupported:
            try:
                m = int(mean_rgba(x, buf[x.mip0_off:x.mip0_off + x.mip0_size], palette_bytes(buf, x)))
            except (FormatError, ValueError, IndexError):  # the preview colour is optional
                m = None
        r.textures.append((x.idx, x.name, x.mask or None, x.platform, x.raster_fmt, x.d3dfmt, x.w, x.h, x.depth,
                           x.levels, int(bool(x.alpha)), x.filter, x.uaddr, x.vaddr, base + x.mip0_off, x.mip0_size,
                           None if x.pal_off is None else base + x.pal_off, h, x.mip0_size + x.pal_size, m))


def _scan_one(buf: bytes, job: BlobJob) -> BlobResult:
    r = BlobResult(job.key)
    ext = job.ext
    if ext in RW_EXTS:
        r.rw_size = rw_payload_size(buf)
    n = r.rw_size or len(buf)
    r.hash = hashlib.blake2b(memoryview(buf)[:n], digest_size=BLOB_HASH_SIZE).digest()
    try:
        if ext == "txd":
            _scan_txd(buf, r, job.abs_off)
        elif ext == "dff":
            r.dff = scan_dff(buf)
            r.empty_tex = rwx.empty_texture_names(buf)
            if r.dff.flags & 64:
                emb = find_embedded_col(buf)
                if emb:
                    try:
                        r.cols = list(iter_col(buf[emb[0]:emb[0] + emb[1]]))
                    except FormatError as e:  # bloodrb.dff/rccam.dff: the plugin holds no COL; keep the DFF
                        r.warn = f"embedded collision ignored: {e}"
        elif ext == "col":
            r.cols = list(iter_col(buf))
        elif ext == "ifp":
            r.ifp = parse_ifp(buf)
        elif ext == "ipl" and buf[:4] == b"bnry":
            r.ipl = parse_ipl_binary(buf)
    except FormatError as e:
        r.error = f"{type(e).__name__}: {e}"
    except (ValueError, IndexError, KeyError, OverflowError, MemoryError) as e:  # defensive: never kill the build
        r.error = f"{type(e).__name__}: {e}"
    return r


def scan_job(jobs: list[BlobJob]) -> list[BlobResult]:
    """Scan a batch of blobs of ONE file (sorted by offset for sequential reads)."""
    out: list[BlobResult] = []
    if not jobs:
        return out
    path = jobs[0].path
    with open_ro(path) as f:
        for job in sorted(jobs, key=lambda j: j.abs_off):
            try:
                f.seek(job.abs_off)
                buf = f.read(job.size)
            except OSError as e:
                out.append(BlobResult(job.key, error=f"read error: {e}"))
                continue
            if len(buf) != job.size:
                r = BlobResult(job.key, error=f"short read {len(buf)} of {job.size}")
                out.append(r)
                continue
            out.append(_scan_one(buf, job))
    return out


def default_jobs() -> int:
    """Scan workers: ``SATK_JOBS`` when set (``1`` = in-process), else min(12, CPUs)."""
    from ..core.procpool import jobs_from_env

    return jobs_from_env() or max(1, min(12, (os.cpu_count() or 2)))
