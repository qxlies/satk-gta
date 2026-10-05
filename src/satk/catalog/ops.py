"""Operations of ``satk.catalog`` (owner M2-09): ``satk catalog build`` (CLI only, ``mcp=False``).

Module-level imports are stdlib/satk only; the work lives in :mod:`satk.catalog.build`.
"""

from __future__ import annotations

from typing import Literal

from ..core.registry import op


@op("catalog.build", mcp=False, long_running=True, group="asset",
    summary="Build the offline HTML catalog (work/out/catalog/index.html): search over models, textures, TXDs and "
            "zones with 128 px thumbnails; model pages with textures and placements on a map. No external URLs.",
    summary_ru="Собрать офлайн-каталог для людей (work/out/catalog/index.html): поиск по моделям, текстурам, TXD и "
               "зонам с миниатюрами 128 px, страницы моделей с текстурами и местами на карте; без внешних CDN.",
    examples=("satk catalog build", "satk catalog build --limit 60 --out catalog-demo",
              "satk catalog build --profile samp --jobs 8"))
def catalog_build(out: str | None = None, limit: int | None = None, jobs: int = 12, thumbs: bool = True,
                  fmt: Literal["auto", "webp", "png"] = "auto", profile: str = "vanilla") -> dict:
    """Build or update the catalog; a repeat with the same index rewrites nothing.

    Thumbnails come from the texture and model caches (missing ones are generated); open ``file`` in a
    browser, no server needed.

    Args:
        out: directory name under work/out (default catalog, catalog-<profile>, ...-sample with limit) or a
            path inside work.
        limit: only the first N models (by id) and the textures/TXDs they use.
        jobs: worker processes for thumbnails (0 = all CPUs).
        thumbs: generate missing thumbnails (--no-thumbs: reuse existing only).
        fmt: thumbnail format: auto = webp with Pillow, else png.
        profile: index profile.
    """
    from .build import build

    return build(out=out, limit=limit, jobs=jobs, thumbs=thumbs, fmt=fmt, profile=profile)
