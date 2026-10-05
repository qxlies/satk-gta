"""``satk catalog build``: the offline HTML catalog of a profile (owner M2-09).

Steps: read the index (:mod:`.collect`) -> thumbnails from the media/model3d caches (:mod:`.thumbs`) ->
background map (:mod:`.worldmap`) -> ``data/*.js`` + ``index.html`` (:mod:`.site`) -> ``catalog.json``
(manifest: what the next build may reuse) -> removal of files the new build no longer references.
Everything is written under the catalog directory (default ``work/out/catalog``); a repeat with the same
index rewrites nothing.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from ..core import paths
from ..core.envelope import obj
from ..core.errors import SatkError
from .collect import WORLD, collect
from .files import dir_size, remove_stale, write_if_changed

__all__ = ["MANIFEST", "out_root", "build"]

#: Manifest file in the catalog root; also marks a directory as a catalog (safe to clean).
MANIFEST = "catalog.json"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")


def _within(child: Path, root: Path) -> bool:
    c, r = os.path.normcase(os.path.abspath(child)), os.path.normcase(os.path.abspath(root))
    try:
        return os.path.commonpath([c, r]) == r
    except ValueError:
        return False


def out_root(out: str | None, profile: str, limited: bool) -> Path:
    """Catalog directory: ``work/out/<name>`` for a bare name (default ``catalog``, ``catalog-<profile>``,
    ``…-sample`` with ``--limit``), or a path inside the work directory. Not created."""
    work = Path(os.path.abspath(paths.cfg().paths.work))
    if out is None:
        name = "catalog" + ("" if profile == "vanilla" else f"-{profile}") + ("-sample" if limited else "")
        return work / "out" / name
    s = str(out).strip()
    if _NAME.fullmatch(s) and s not in (".", ".."):
        return work / "out" / s
    p = Path(os.path.abspath(s))
    if not _within(p, work) or os.path.normcase(str(p)) == os.path.normcase(str(work)):
        raise SatkError("BAD_PARAMS", f"--out must be a name (-> work/out/<name>) or a directory inside "
                                      f"{paths.jpath(work)}", hint="satk catalog build --out catalog-demo")
    return p


def _manifest(root: Path) -> dict:
    """The previous manifest, ``{}`` for a new directory; refuses a non-empty directory without one."""
    f = root / MANIFEST
    if f.is_file():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}
    if root.is_dir() and any(root.iterdir()):
        raise SatkError("BAD_PARAMS", f"{paths.jpath(root)} is not empty and is not a catalog (no {MANIFEST})",
                        hint="choose another --out (the catalog removes files it does not know)")
    return {}


def _full_base(root: Path) -> str | None:
    """Relative URL from the catalog to the full-size texture cache (``../../cache/tex/``), if any."""
    from ..media.texture import cache_dir

    try:
        rel = os.path.relpath(cache_dir(), root)
    except ValueError:  # another drive
        return None
    return rel.replace(os.sep, "/").rstrip("/") + "/"


def build(out: str | None = None, limit: int | None = None, jobs: int = 12, thumbs: bool = True,
          fmt: str = "auto", profile: str = "vanilla") -> dict:
    """Build (or update) the catalog; returns the object envelope of ``catalog.build``."""
    import satk

    from ..index.api import open_index
    from .site import render_index, write_data
    from .thumbs import image_format, model_thumb_path, model_thumbs, render_stamp, texture_thumb_path, texture_thumbs
    from .worldmap import render_world

    if jobs < 0:
        raise SatkError("BAD_PARAMS", f"jobs must be >= 0, got {jobs}")
    if limit is not None and limit < 1:
        raise SatkError("BAD_PARAMS", f"limit must be >= 1, got {limit}")
    jobs = int(jobs) or (os.cpu_count() or 1)
    t0 = time.perf_counter()
    timing: dict[str, float] = {}
    ext = image_format(fmt)
    root = out_root(out, profile, limit is not None)
    prev = _manifest(root)
    db = open_index(profile)
    warn: list[str] = []
    try:
        warn.extend(db.stale_warnings())
    except (AttributeError, SatkError):  # pragma: no cover - fake index
        pass
    paths.ensure_writable(root)
    root.mkdir(parents=True, exist_ok=True)

    data = collect(db, profile, limit)
    timing["collect"] = round(time.perf_counter() - t0, 2)

    stamp = render_stamp(profile, data.index_hash, ext)
    previous = prev.get("models") if prev.get("ext") == ext else None
    previous = previous if isinstance(previous, dict) else {}
    t1 = time.perf_counter()
    if thumbs:
        tex_ok, tstats = texture_thumbs(db, data.images, root, ext, jobs)
        timing["textures"] = round(time.perf_counter() - t1, 2)
        t1 = time.perf_counter()
        model_keys, mstats = model_thumbs(data.models, root, ext, jobs, profile, stamp=stamp, previous=previous)
        timing["models"] = round(time.perf_counter() - t1, 2)
    else:  # reuse whatever exists, render nothing
        tex_ok = {h for h in data.images if texture_thumb_path(root, h, ext).is_file()}
        old = previous.get("keys") or {}
        model_keys = {m["id"]: old.get(str(m["id"]), "") for m in data.models
                      if model_thumb_path(root, m["id"], ext).is_file()}
        tstats = {"kept": len(tex_ok), "skipped": len(data.images) - len(tex_ok)}
        mstats = {"kept": len(model_keys)}
    for what, st in (("texture", tstats), ("model", mstats)):
        if st.get("failed"):
            first = "; ".join(f"{e[0]} {e[1]}" for e in st.get("errors", [])[:3])
            warn.append(f"THUMB_FAILED: {st['failed']} {what} thumbnails ({first})")

    t1 = time.perf_counter()
    map_bytes = render_world(data.footprints, ext, WORLD)
    map_file = f"map.{ext}" if map_bytes else None
    if map_bytes:
        write_if_changed(root / map_file, map_bytes)
    else:
        warn.append("NO_MAP: numpy is missing, the catalog has no background map")
    timing["map"] = round(time.perf_counter() - t1, 2)

    t1 = time.perf_counter()
    data_files, changed = write_data(data, root, ext=ext, tex_ok=tex_ok, model_keys=model_keys, map_file=map_file,
                                     full_base=_full_base(root), satk_version=satk.__version__)
    index = root / "index.html"
    changed += write_if_changed(index, render_index(profile))
    manifest = {"v": 1, "tool": "satk catalog build", "profile": profile, "index_hash": data.index_hash,
                "limited": data.limited, "ext": ext, "counts": data.counts,
                "models": {"stamp": stamp, "keys": {str(k): v for k, v in sorted(model_keys.items())}}}
    changed += write_if_changed(root / MANIFEST, json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                                            separators=(",", ":")) + "\n")
    timing["site"] = round(time.perf_counter() - t1, 2)

    # what this build does not reference any more
    t1 = time.perf_counter()
    removed = remove_stale(root / "data", data_files, (".js",))
    keep = [texture_thumb_path(root, h, ext) for h in tex_ok] + [model_thumb_path(root, m, ext) for m in model_keys]
    removed += remove_stale(root / "t", keep, (".webp", ".png"))
    for other in ("map.webp", "map.png"):
        if other != map_file and (root / other).is_file():
            paths.ensure_removable(root / other)
            (root / other).unlink()
            removed += 1
    timing["cleanup"] = round(time.perf_counter() - t1, 2)

    files, size = dir_size(root)
    thumbs_out = {"textures": tstats, "models": mstats}
    return obj(None, file=paths.jpath(index), dir=paths.jpath(root), profile=profile, counts=data.counts,
               thumbs=thumbs_out, format=ext, map=bool(map_bytes), files=files, mb=round(size / 1e6, 1),
               changed=changed, removed=removed, seconds=round(time.perf_counter() - t0, 1), timing=timing,
               warn=warn)
