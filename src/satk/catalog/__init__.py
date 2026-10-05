"""satk.catalog — offline HTML catalog of an index for humans (``satk catalog build``, owner M2-09).

``work/out/catalog/index.html`` opens from disk in any modern browser (no server, no external URLs):
search over models, textures, TXDs and zones with 128 px thumbnails, model pages (DFF/COL/IDE fields,
TXD chain, textures, placements on a map), texture, TXD and zone pages.

Modules: :mod:`.collect` (index -> :class:`~satk.catalog.collect.Data`), :mod:`.thumbs` (thumbnails from
the media/model3d caches), :mod:`.worldmap` (background map), :mod:`.site` (``data/*.js`` chunks and
``index.html`` from ``templates/``), :mod:`.build` (the whole build), :mod:`.ops` (the CLI operation).

Python API::

    from satk.catalog import build
    env = build(limit=60, out="catalog-demo")   # -> {"file": ".../work/out/catalog-demo/index.html", ...}
"""

from __future__ import annotations

__all__ = ["build"]


def __getattr__(name: str):  # lazy: keep `import satk.catalog` cheap
    if name == "build":
        from .build import build

        return build
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
