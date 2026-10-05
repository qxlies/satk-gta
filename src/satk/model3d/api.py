"""Python API of ``satk.model3d`` (SPEC §4.4, WP-05): model export and previews.

* :func:`export` — ``glb`` | ``obj`` | ``png`` (the model's textures) | ``raw`` (DFF + TXD chain + COL as in
  the game) into ``work/out/models/<name>/`` (``raw``: ``work/cache/raw/<profile>/``);
* :func:`image` — preview sheet of a model; backends ``soft`` (default, numpy, deterministic), ``ariane``
  (running viewer, SAAP ``asset.render``) and ``blender`` (WP-10 runner); ``ariane``/``blender`` fall back
  to ``soft`` with a warning when they cannot render. Cached by content:
  ``work/cache/model/<dff_hash>-<txdchain_hash>-<backend>-<views>-<size>.png`` (+ ``.json`` sidecar);
* :func:`satk.model3d.batch.thumbnails` — the same for every model (``satk model image --all``).

Example::

    from satk.model3d import api
    r = api.image("model:411", views=4, size=384)        # r["file"] -> 768x768 PNG
    r = api.export("model:411", "glb")                    # r["files"][0] -> work/out/models/infernus/infernus.glb
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path

from ..core import paths
from ..core.envelope import obj
from ..core.errors import SatkError
from ..formats.rw import FormatError, rw_payload_size
from .mesh import ModelScene, build_scene
from .resolve import ModelSource, open_db, resolve
from .textures import MatInfo, TxdChain, decode, resolve_materials, vehicle_colours

__all__ = ["FORMATS", "BACKENDS", "Loaded", "load", "export", "image", "cache_key", "out_dir", "render_soft_png"]

FORMATS = ("glb", "obj", "png", "raw")
BACKENDS = ("auto", "soft", "ariane", "blender")


@dataclass
class Loaded:
    """A model with its bytes, decoded scene and resolved materials."""

    src: ModelSource
    dff: bytes | None
    txds: list[tuple[str, bytes]]
    scene: ModelScene | None
    chain: TxdChain
    mats: list[list[MatInfo]]
    colours: dict | None


def _stem(name: str) -> str:
    return name.rsplit(".", 1)[0].lower()


def _safe(name: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_." else "_" for c in name.lower())
    return out.strip(".") or "model"


def _read(db, src: ModelSource) -> tuple[bytes | None, list[tuple[str, bytes]]]:
    dff = db.read_blob(src.dff) if src.dff is not None else None
    txds = [(_stem(b.name), db.read_blob(b)) for b in src.txd_chain]
    return dff, txds


def _colours(src: ModelSource) -> dict | None:
    if src.sec != "cars":
        return None
    try:
        root = paths.profile_root(src.profile)
    except SatkError:
        root = None
    return vehicle_colours(src.name, root)


def _scene(src: ModelSource, dff: bytes) -> ModelScene:
    try:
        return build_scene(dff, name=src.name, sec=src.sec)
    except FormatError as e:
        raise SatkError("UNSUPPORTED", f"{src.sid} ({src.name}): cannot decode the DFF: {e}",
                        hint=f"satk formats dump {src.dff.sid if src.dff else src.sid}") from None


def load(ident: str, profile: str = "vanilla", *, db=None) -> Loaded:
    """Resolve ``ident`` and read/decode everything (``scene`` ``None`` when the game has no DFF)."""
    db = db or open_db(profile)
    src = resolve(ident, profile, db)
    dff, txds = _read(db, src)
    chain = TxdChain(txds)
    colours = _colours(src)
    scene = _scene(src, dff) if dff is not None else None
    mats = resolve_materials(scene.materials, chain, colours) if scene is not None else []
    if chain.errors:
        src.notes.extend(f"TXD skipped: {e}" for e in chain.errors)
    return Loaded(src, dff, txds, scene, chain, mats, colours)


# ----------------------------------------------------------------------------- output dirs
def _within(child: Path, root: Path) -> bool:
    c, r = os.path.normcase(os.path.abspath(child)), os.path.normcase(os.path.abspath(root))
    try:
        return os.path.commonpath([c, r]) == r
    except ValueError:
        return False


def out_dir(out: str | None, default: tuple[str, ...]) -> Path:
    """``out`` (must be inside the work directory) or ``work/<default...>``; created."""
    if out is None:
        return paths.work(*default)
    p = Path(os.path.abspath(os.fspath(out)))
    work = Path(os.path.abspath(paths.cfg().paths.work))
    if not _within(p, work):
        raise SatkError("BAD_PARAMS", f"--out must be inside the work directory {paths.jpath(work)}",
                        hint=f"omit --out (default {paths.jpath(work.joinpath(*default))})")
    paths.ensure_writable(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write(path: Path, data: bytes | str) -> Path:
    """Atomic write that skips identical existing content (repeat = no rewrite)."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    try:
        if path.stat().st_size == len(raw) and path.read_bytes() == raw:
            return path
    except OSError:
        pass
    return paths.atomic_write(path, raw)


# ----------------------------------------------------------------------------- export
def export(ident: str, fmt: str = "glb", out: str | None = None, profile: str = "vanilla", *, db=None) -> dict:
    """Export a model (see module docstring). Returns ``{files, stats, ...}`` (object envelope)."""
    if fmt not in FORMATS:
        raise SatkError("BAD_PARAMS", f"unknown format {fmt!r}", did_you_mean=list(FORMATS))
    t0 = time.perf_counter()
    db = db or open_db(profile)
    if fmt == "raw":
        return _export_raw(ident, out, profile, db, t0)
    L = load(ident, profile, db=db)
    src = L.src
    if L.scene is None:
        raise SatkError("NOT_FOUND", f"{src.sid} ({src.name}, {src.sec}) has no DFF in profile {profile!r}",
                        hint=f"satk asset get {src.sid}")
    name = _safe(src.name)
    d = out_dir(out, ("out", "models", name))
    files: list[Path] = []
    warn = list(src.notes)
    if fmt == "glb":
        from .gltf import build_glb

        glb, stats = build_glb(L.scene, L.mats)
        files.append(_write(d / f"{name}.glb", glb))
    elif fmt == "obj":
        from .obj import build_obj

        text, mtl, pngs, stats = build_obj(L.scene, L.mats, name)
        files.append(_write(d / f"{name}.obj", text))
        files.append(_write(d / f"{name}.mtl", mtl))
        for fn in sorted(pngs):
            files.append(_write(d / fn, pngs[fn]))
    else:  # png: the model's resolved textures
        from .png import encode_png

        done: set[str] = set()
        stats = {"textures": 0, "tex_missing": 0}
        missing: set[str] = set()
        for ms in L.mats:
            for mi in ms:
                if mi.tex is None:
                    if mi.missing:
                        missing.add(mi.tex_name)
                    continue
                if mi.tex.name in done:
                    continue
                done.add(mi.tex.name)
                img = decode(mi.tex)
                files.append(_write(d / f"{_safe(mi.tex.name)}.png", encode_png(img.w, img.h, img.rgba, 4)))
        stats.update(textures=len(done), tex_missing=len(missing))
        stats["tris"] = L.scene.tris
    missing_names = sorted({mi.tex_name for ms in L.mats for mi in ms if mi.missing})
    if missing_names:
        warn.append(f"TEX_MISSING: {', '.join(missing_names[:10])}" + (" ..." if len(missing_names) > 10 else ""))
    return obj(src.sid, name=src.name, format=fmt, dir=paths.jpath(d), files=[paths.jpath(f) for f in files],
               stats=stats, seconds=round(time.perf_counter() - t0, 3), warn=warn)


def _export_raw(ident: str, out: str | None, profile: str, db, t0: float) -> dict:
    src = resolve(ident, profile, db)
    d = out_dir(out, ("cache", "raw", profile))
    files: list[Path] = []
    warn = list(src.notes)
    refs = ([src.dff] if src.dff is not None else []) + list(src.txd_chain)
    for ref in refs:
        data = db.read_blob(ref)
        n = rw_payload_size(data)
        files.append(_write(d / _safe(ref.name), data[:n] if n else data))
    col = None
    if src.col is not None:
        if src.col.via == "embedded":
            col = f"embedded in {src.dff.name if src.dff else src.name}"
        else:
            data = db.read_blob(src.col.blob)
            p = _write(d / _safe(src.col.blob.name), data)
            files.append(p)
            col = f"{src.col.blob.name}#{src.col.idx}"
    if src.dff is None:
        warn.append(f"NO_DFF: {src.sid} ({src.name}, {src.sec}) has no DFF in profile {profile!r}")
    return obj(src.sid, name=src.name, format="raw", dir=paths.jpath(d), files=[paths.jpath(f) for f in files],
               col=col, seconds=round(time.perf_counter() - t0, 3), warn=warn)


# ----------------------------------------------------------------------------- previews
def _bg(bg) -> tuple[int, int, int]:
    from .softrender import DEFAULT_BG

    if bg is None:
        return DEFAULT_BG
    if isinstance(bg, str):
        s = bg.lstrip("#")
        if len(s) != 6:
            raise SatkError("BAD_PARAMS", f"bg must be #rrggbb, got {bg!r}")
        try:
            return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        except ValueError:
            raise SatkError("BAD_PARAMS", f"bg must be #rrggbb, got {bg!r}") from None
    return tuple(int(c) for c in bg)[:3]


def cache_key(dff: bytes, txds: list[tuple[str, bytes]], backend: str, views: int, size: int, *,
              el: float = 25.0, bg=None, colours: dict | None = None) -> str:
    """``<dff_hash>-<txdchain_hash>-<backend>-<views>-<size>`` (txdchain hash also covers render settings)."""
    from .softrender import RENDER_VERSION

    hd = hashlib.blake2b(dff, digest_size=6).hexdigest()
    h = hashlib.blake2b(digest_size=6)
    h.update(f"satk-model3d/{backend}/r{RENDER_VERSION}/el{float(el):g}/bg{_bg(bg)}/c{sorted((colours or {}).items())}"
             .encode())
    for name, data in txds:
        h.update(name.encode() + b"\0" + hashlib.blake2b(data, digest_size=16).digest())
    return f"{hd}-{h.hexdigest()}-{backend}-{int(views)}-{int(size)}"


def _legend(angles: list[tuple[float, float]]) -> list[list]:
    return [[i + 1, round(az, 1), round(el, 1)] for i, (az, el) in enumerate(angles)]


def render_soft_png(src: ModelSource, dff: bytes, txds: list[tuple[str, bytes]], views: int, size: int, *,
                    el: float = 25.0, bg=None) -> tuple[bytes, dict]:
    """Render with the ``soft`` backend: ``(png_bytes, stats)``."""
    from . import softrender as SR

    scene = _scene(src, dff)
    chain = TxdChain(txds)
    mats = resolve_materials(scene.materials, chain, _colours(src))
    prep = SR.prepare(scene, mats)
    angles = SR.view_angles(views, el)
    imgs, cover = SR.render(prep, angles, size, bg=_bg(bg))
    png = SR.sheet_png(imgs, bg=_bg(bg))
    stats = {"tris": scene.tris, "drawn": prep.tris, "tex_missing": len(prep.tex_missing),
             "cover": [round(c, 3) for c in cover]}
    blank_views = [i + 1 for i, c in enumerate(cover) if c == 0.0]
    if blank_views:
        stats["blank_views"] = blank_views
    if prep.tex_missing:
        stats["missing"] = prep.tex_missing[:20]
    return png, stats


def _tile_files(files: list[str], size: int, bg) -> bytes:
    from . import softrender as SR
    from .png import read_png_rgb

    np = SR._np()
    tiles = []
    for f in files:
        w, h, rgb = read_png_rgb(f, _bg(bg), size)
        tiles.append(np.frombuffer(rgb, dtype=np.uint8).reshape(h, w, 3))
    return SR.sheet_png(tiles, bg=_bg(bg))


def _render_ariane(src: ModelSource, views: int, size: int, el: float, bg, key: str) -> tuple[bytes, dict, list[str]]:
    from ..viewer.backends import get_backend
    from .softrender import view_angles

    b = get_backend("ariane")
    try:
        b.require("asset.render", "model previews")
        prefix = paths.work("cache", "model", "ariane", key)
        r, g, bl = _bg(bg)
        angles = [az for az, _e in view_angles(views, el)]
        if getattr(b, "proto", None) == "ariane-ipc/1":
            # Adapt the legacy wire command here: its angle is measured from +X in radians.
            # Going straight to asset_preview avoids depending on (or double-correcting) the
            # separate viewer adapter's azimuth conversion. Native SAAP keeps its +Y convention.
            files = []
            for az in angles:
                path = paths.ensure_writable(prefix / f"view_{az:g}.png")
                b.cmd("asset_preview", src.model_id if src.model_id is not None else src.name,
                      paths.jpath(path), math.radians((az + 90.0) % 360.0), size, timeout=60.0)
                files.append(paths.jpath(path))
            res = {"files": files}
            b.warnings.append("ariane asset_preview: elevation and background are fixed")
        else:
            res = b.call("asset.render", {"model": src.model_id if src.model_id is not None else src.name,
                                          "views": angles, "el": el, "size": size,
                                          "path_prefix": paths.jpath(prefix / "view"), "bg": f"#{r:02x}{g:02x}{bl:02x}"})
        files = list(res.get("files") or [])
        if len(files) != views:
            raise SatkError("PROTOCOL", f"ariane asset.render returned {len(files)} files for {views} views")
        png = _tile_files(files, size, bg)
        stats = dict(res.get("stats") or {})
        return png, stats, list(getattr(b, "warnings", []) or [])
    finally:
        try:
            b.close()
        except Exception:  # noqa: BLE001
            pass


def _render_blender(src: ModelSource, views: int, size: int, el: float, bg, profile: str) -> tuple[bytes, dict, list[str]]:
    """WP-10 ``satk-blender/1`` ``import_model {id, render: true, views: 1|4, size}`` (one PNG sheet back)."""
    try:
        from ..blender import runner  # WP-10 (SPEC §4.11)
        run_job = runner.run_job
    except (ImportError, AttributeError):
        raise SatkError("NOT_READY", "the Blender runner (satk.blender) is not available",
                        hint="satk blender doctor") from None
    if views not in (1, 4):
        raise SatkError("UNSUPPORTED", f"the blender backend renders 1 or 4 views, not {views}")
    res = run_job("import_model", {"id": src.sid, "render": True, "views": views, "size": size, "save": False},
                  profile=profile)
    files = (res or {}).get("files") or {}
    pngs = list(files.get("png") or []) if isinstance(files, dict) else list(files)
    warn = list(res.get("warnings") or [])
    if float(el) != 25.0:
        warn.append("blender backend: elevation is fixed at 25 deg")
    if len(pngs) == 1:                                  # the sheet itself (WP-10 composes 2x2)
        from .png import encode_png, read_png_rgb

        w, h, rgb = read_png_rgb(pngs[0], _bg(bg))
        png = encode_png(w, h, rgb, 3)
    elif len(pngs) == views:
        png = _tile_files(pngs, size, bg)
    else:
        raise SatkError("EXTERNAL_TOOL", f"blender returned {len(pngs)} renders for {views} views",
                        data={"log": res.get("log")})
    return png, dict(res.get("stats") or {}), warn


def _nodff(src: ModelSource, views: int, size: int, bg) -> Path:
    from . import softrender as SR

    cols = max(1, math.ceil(math.sqrt(views)))
    p = paths.work("cache", "model", f"nodff-{views}-{size}.png")
    if not p.is_file():
        tiles = [SR.placeholder(size, _bg(bg)) for _ in range(views)]
        _write(p, SR.sheet_png(tiles, cols, bg=_bg(bg)))
    return p


def image(ident: str, views: int = 4, size: int = 384, backend: str = "auto", profile: str = "vanilla", *,
          el: float = 25.0, bg=None, force: bool = False, db=None) -> dict:
    """Preview sheet of one model (object envelope: ``file, backend, w, h, legend, stats, cached``)."""
    if not 1 <= int(views) <= 16:
        raise SatkError("BAD_PARAMS", f"views must be 1..16, got {views}")
    if not 16 <= int(size) <= 2048:
        raise SatkError("BAD_PARAMS", f"size must be 16..2048, got {size}")
    if backend not in BACKENDS:
        raise SatkError("BAD_PARAMS", f"unknown backend {backend!r}", did_you_mean=list(BACKENDS))
    if not -89.0 <= float(el) <= 89.0:
        raise SatkError("BAD_PARAMS", f"el must be -89..89, got {el}")
    from .softrender import view_angles

    t0 = time.perf_counter()
    db = db or open_db(profile)
    src = resolve(ident, profile, db)
    views, size = int(views), int(size)
    angles = view_angles(views, el)
    cols = max(1, math.ceil(math.sqrt(views)))
    rows = math.ceil(views / cols)
    warn = list(src.notes)
    base = dict(name=src.name, w=cols * size, h=rows * size, legend=_legend(angles))
    if src.dff is None:
        p = _nodff(src, views, size, bg)
        warn.append(f"NO_DFF: {src.sid} ({src.name}, {src.sec}) has no DFF in profile {profile!r}: placeholder image")
        return obj(src.sid, file=paths.jpath(p), backend="none", **base, stats={"tris": 0, "tex_missing": 0},
                   warn=warn)
    dff, txds = _read(db, src)
    colours = _colours(src)
    order = ["soft"] if backend in ("auto", "soft") else [backend, "soft"]
    for used in order:
        key = cache_key(dff, txds, used, views, size, el=el, bg=bg, colours=colours)
        png_path = paths.work("cache", "model", f"{key}.png")
        side = png_path.with_suffix(".json")
        if not force and png_path.is_file() and side.is_file():
            try:
                meta = json.loads(side.read_text(encoding="utf-8"))
                return obj(src.sid, file=paths.jpath(png_path), backend=used, **base, stats=meta.get("stats"),
                           cached=True, seconds=round(time.perf_counter() - t0, 3), warn=warn + meta.get("warn", []))
            except (OSError, ValueError):
                pass
        try:
            if used == "soft":
                png, stats = render_soft_png(src, dff, txds, views, size, el=el, bg=bg)
                extra: list[str] = []
                if stats.get("blank_views"):
                    empty = ", ".join(map(str, stats["blank_views"]))
                    extra.append(f"BLANK_PREVIEW: no visible pixels in views {empty} (of {views})")
            elif used == "ariane":
                png, stats, extra = _render_ariane(src, views, size, el, bg, key)
            else:
                png, stats, extra = _render_blender(src, views, size, el, bg, profile)
        except SatkError as e:
            if used == "soft":
                raise
            warn.append(f"FALLBACK: backend {used} failed ({e.code}: {e.msg}); rendered with soft")
            continue
        _write(png_path, png)
        _write(side, json.dumps({"id": src.sid, "name": src.name, "backend": used, "views": views, "size": size,
                                 "el": el, "stats": stats, "warn": extra}, sort_keys=True, separators=(",", ":")))
        return obj(src.sid, file=paths.jpath(png_path), backend=used, **base, stats=stats, cached=False,
                   seconds=round(time.perf_counter() - t0, 3), warn=warn + extra)
    raise SatkError("INTERNAL", "no backend produced an image")  # pragma: no cover
