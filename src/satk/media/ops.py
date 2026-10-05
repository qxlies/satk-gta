"""Operations of ``satk.media`` (owner WP-04, SPEC §4.4, §4.6, §4.7):

* ``texture.image``      -> ``satk texture image <sid...>``  / MCP ``texture_image``;
* ``texture.export_all`` -> ``satk texture export-all``      (CLI only, long-running);
* ``map.image``          -> ``satk map image --center X,Y``  / MCP ``map_image`` (``x``, ``y``).

Images are returned as absolute file paths (``files``/``file``); the MCP server attaches them only
with ``inline=true``. Module-level imports stay stdlib/satk only (SPEC §2.3).
"""

from __future__ import annotations

import os
from typing import Literal

from ..core.envelope import obj, with_warn
from ..core.errors import SatkError
from ..core.paths import jpath
from ..core.registry import doctor_check, op, status_provider

#: ``texture_image(mode="png")`` writes at most this many files per call.
PNG_MAX = 64
#: ``texture_image(mode="sheet")`` renders at most this many sheets (64 cells each).
SHEETS_MAX = 4


@op("texture.image", mcp_group="media",
    summary="Texture PNGs or one numbered contact sheet (mode=sheet); paths + legend [n, sid, 'WxH FMT'].",
    summary_ru="PNG текстур или один пронумерованный контакт-лист (mode=sheet) по SID tex/pix/txd/model; "
               "возвращает пути и легенду [n, sid, \"WxH FMT\"].",
    examples=("satk texture image tex:bistro/vent_64", "satk texture image txd:lawest1 --mode sheet",
              "satk texture image model:411 --mode sheet --labels"))
def texture_image(ids: list[str], mode: Literal["png", "sheet"] = "png", size: int | None = None,
                  labels: bool = False, inline: bool = False, profile: str = "vanilla") -> dict:
    """Texture images from the PNG cache, or contact sheets.

    Args:
        ids: tex/pix/txd/model SIDs (txd/model expand).
        mode: png: file per texture; sheet: numbered 8x8 sheets.
        size: png: max side (256, 0=orig); sheet: cell (128).
        labels: sheet: names under cells.
    """
    from ..index.api import open_index
    from .sheet import MAX_CELLS, render_sheet
    from .texture import Reader, describe, png_for_ref, resolve_refs

    if not ids:
        raise SatkError("BAD_PARAMS", "no SIDs given", hint="satk texture image tex:bistro/vent_64")
    refs, warn = resolve_refs(open_index(profile), ids)
    if not refs:
        raise SatkError("NOT_FOUND", f"no textures for {', '.join(map(str, ids))}",
                        hint="satk asset refs <model> --rel tex")
    if mode == "png":
        if len(refs) > PNG_MAX:
            warn.append(f"TRUNCATED: {len(refs)} textures, first {PNG_MAX} written (use mode=sheet)")
            refs = refs[:PNG_MAX]
        lim = 256 if size is None else size
        files: list[str] = []
        with Reader() as rd:
            for r in refs:
                files.append(jpath(png_for_ref(r, lim or None, rd)))
        legend = [[i + 1, str(r.sid), describe(r)] for i, r in enumerate(refs)]
        return with_warn(obj(files=files, legend=legend), *warn)

    cell = 128 if not size else size
    cap = MAX_CELLS * SHEETS_MAX
    if len(refs) > cap:
        warn.append(f"TRUNCATED: {len(refs)} textures, first {cap} on {SHEETS_MAX} sheets")
        refs = refs[:cap]
    name = str(ids[0]) if len(ids) == 1 else f"{ids[0]}_{len(refs)}"
    files, legend = [], []
    with Reader() as rd:
        for k in range(0, len(refs), MAX_CELLS):
            part = refs[k:k + MAX_CELLS]
            path, leg = render_sheet(part, cell=cell, labels=labels, start=k + 1,
                                     name=name if k == 0 else f"{name}_p{k // MAX_CELLS + 1}", reader=rd)
            files.append(jpath(path))
            legend.extend(leg)
    if len(files) > 1:
        warn.append(f"PAGES: {len(refs)} textures on {len(files)} sheets of up to {MAX_CELLS}")
    return with_warn(obj(files=files, legend=legend), *warn)


@op("texture.export_all", mcp=False, long_running=True,
    summary="Write the mip0 PNG of every unique texture of a profile into work/cache/tex (process pool).",
    summary_ru="Выгрузить mip0 всех уникальных текстур профиля в PNG-кэш work/cache/tex (пул процессов).",
    examples=("satk texture export-all --jobs 12",))
def texture_export_all(jobs: int = 12, profile: str = "vanilla") -> dict:
    """Fill the content-addressed texture cache (existing files are skipped).

    Args:
        jobs: worker processes (0 = all CPUs).
        profile: index profile.
    """
    from .texture import export_all

    if jobs < 0:
        raise SatkError("BAD_PARAMS", f"jobs must be >= 0, got {jobs}")
    res = export_all(jobs=jobs, profile=profile)
    env = obj(**res)
    if res.get("failed"):
        with_warn(env, f"FAILED: {res['failed']} textures could not be written (see errors)")
    return env


@op("map.image", mcp_group="media",
    summary="Top-down map around x,y; biggest objects numbered, legend [n, sid, name].",
    summary_ru="Карта сверху вокруг x,y: отпечатки расстановок, сетка, масштаб; номера на крупных объектах "
               "и легенда [n, sid, name].",
    examples=("satk map image --center 2495,-1687", "satk map image --center 2495,-1687 --span 600 --layers inst,lod,zone"))
def map_image(x: float | None = None, y: float | None = None, center: list[float] | None = None, span: float = 300,
              px: int = 768, layers: list[str] | None = None, area: int = 0, labels: int = 20, inline: bool = False,
              profile: str = "vanilla") -> dict:
    """Render a map PNG of the index around a point (world ``x``, ``y``).

    Args:
        center: [x, y] (CLI: X,Y).
        span: view size, m.
        px: image side.
        layers: inst|lod|zone.
        area: interior; -1 = all.
        labels: how many to number (0-99).
    """
    from .mapplot import render_map

    if center:
        if len(center) != 2:
            raise SatkError("BAD_PARAMS", f"center must be X,Y, got {center}", hint="satk map image --center 2495,-1687")
        x, y = center
    if x is None or y is None:
        raise SatkError("BAD_PARAMS", "give x and y (or center)", hint="satk map image --center 2495,-1687")
    r = render_map(x, y, span, px, tuple(layers or ("inst",)), area, labels, profile)
    return obj(**r)


# --------------------------------------------------------------------------- status / doctor


def _count_cache(base) -> int:
    n = 0
    try:
        with os.scandir(base) as it:
            for d in it:
                if d.is_dir():
                    with os.scandir(d.path) as it2:
                        n += sum(1 for f in it2 if f.name.endswith(".png") and "@" not in f.name)
    except OSError:
        return 0
    return n


@status_provider("media")
def _status(deep: bool) -> dict:
    from ..core.paths import cfg
    from . import png as _png
    from .texture import dxt_backend

    base = cfg().paths.work / "cache" / "tex"
    out: dict = {"png": _png.backend(), "dxt": dxt_backend(), "cache": jpath(base)}
    if deep:
        out["cached"] = _count_cache(base)
    return out


@doctor_check("media_pillow")
def _doctor() -> dict:
    from ..core.errors import bootstrap_hint
    from . import png as _png

    if _png.have_pillow():
        import PIL

        return {"status": "ok", "msg": f"Pillow {PIL.__version__}: fast DXT decode, PNG and contact sheets", "fix": None}
    return {"status": "warn", "msg": "Pillow is missing: textures, sheets and maps use the slow stdlib path",
            "fix": bootstrap_hint()}
