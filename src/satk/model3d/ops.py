"""Operations of satk.model3d (owner WP-05): ``satk model image`` / ``model_image`` and
``satk asset export`` / ``asset_export`` (SPEC §4.4, §4.6, §4.7).

Module-level imports are stdlib/satk only; the work lives in :mod:`satk.model3d.api`.
"""

from __future__ import annotations

from typing import Literal

from ..core.registry import doctor_check, op


@op("model.image",
    summary="Preview PNG of a model (path): 4 views = 2x2 sheet; soft renderer, or ariane/blender (fallback: "
            "soft).",
    summary_ru="Превью модели (PNG): ракурсы 2×2 программным рендером или через Ariane/Blender; --all — миниатюры всех моделей.",
    mcp_group="media", long_running=True,
    examples=("satk model image model:411", "satk model image model:17613 --views 1 --size 256",
              "satk model image --all --size 128 --views 1 --jobs 12"))
def model_image(id: str | None, views: int = 4, size: int = 384,  # noqa: A002 - SPEC parameter name
                backend: Literal["auto", "soft", "ariane", "blender"] = "auto", inline: bool = False,
                all: bool = False, jobs: int = 12, force: bool = False,  # noqa: A002
                profile: str = "vanilla") -> dict:
    """Render a model preview (or, with --all, thumbnails of every model with a DFF).

    views: 1..16; size: px per view; backend auto = soft; inline: also return the image; all: thumbnails of all
    models (CLI batch, jobs = processes); force: ignore the cache.

    Args:
        id: model:411|infernus|dff:NAME|inst:IPL#N
    """
    from ..core.errors import SatkError

    if all:
        from .batch import thumbnails

        if id:
            raise SatkError("BAD_PARAMS", "give either an id or --all, not both")
        if backend not in ("auto", "soft"):
            raise SatkError("BAD_PARAMS", "--all supports only the soft backend (auto or soft)")
        if inline:
            raise SatkError("BAD_PARAMS", "inline is not supported for --all; use the thumbnail manifest")
        return thumbnails(size, views, jobs, profile, force=force)
    if not id:
        raise SatkError("BAD_PARAMS", "model id required (or --all)", hint="satk model image model:411")
    from . import api

    return api.image(id, views, size, backend, profile, force=force)


@op("asset.export",
    summary="Export a model to work/out/models/<name>/: glb, obj (+mtl+png), png (its textures) or raw "
            "(DFF+TXD+COL as in the game).",
    summary_ru="Экспорт модели: glb, obj (+mtl+png), png (текстуры) или raw (DFF+TXD+COL как в игре).",
    mcp_group="media",
    examples=("satk asset export model:411 --format glb", "satk asset export model:411 --format obj",
              "satk asset export model:411 --format raw"))
def asset_export(id: str, format: Literal["glb", "obj", "png", "raw"] = "glb",  # noqa: A002 - SPEC names
                 out: str | None = None, profile: str = "vanilla") -> dict:
    """Export a model.

    glb = glTF 2.0 with embedded PNG textures and prelit COLOR_0; raw goes to work/cache/raw/<profile>/;
    out must be a directory inside work/.

    Args:
        id: model:411|infernus|dff:NAME|inst:IPL#N
    """
    from . import api

    return api.export(id, format, out, profile)


@doctor_check("model3d")
def _doctor() -> dict:
    """The soft renderer needs numpy; PNG encoding is faster with Pillow."""
    from ..core.errors import bootstrap_hint

    try:
        import numpy  # noqa: F401
    except ImportError:
        return {"status": "warn", "msg": "numpy missing: model previews (soft) unavailable, exports still work",
                "fix": bootstrap_hint()}
    try:
        import PIL  # noqa: F401
        pil = "Pillow"
    except ImportError:
        pil = "stdlib zlib"
    return {"status": "ok", "msg": f"soft renderer ready (numpy {numpy.__version__}, PNG via {pil})", "fix": None}
