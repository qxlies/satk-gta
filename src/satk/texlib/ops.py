"""Texture library operations; heavy dependencies are loaded only when called."""

from __future__ import annotations

from ..core.envelope import obj
from ..core.registry import op


@op("texlib.make", mcp=False, group="texture", long_running=True,
    summary="Bake a tileable procedural SA material in headless Cycles, finish against vanilla role bands, "
            "and write a PNG, DXT1/DXT3 preview and packing manifest. Repeat inputs reuse verified outputs.",
    summary_ru="Запечь бесшовный процедурный материал в Cycles, обработать по нормам SA и сохранить PNG с предпросмотром DXT.",
    examples=("satk texlib make brick --size 256 --seed 7",))
def texlib_make(preset: str, size: int = 256, seed: int = 0, tint: str | None = None,
                out: str | None = None) -> dict:
    """Bake and check one procedural preset.

    Args:
        preset: preset name from texlib list (hyphens and underscores are interchangeable).
        size: square texture size, 128 or 256 pixels.
        seed: deterministic variation, integer from 0 to 4294967295.
        tint: colour name or #RRGGBB; hue is retained within the role's saturation and value range.
        out: new output directory under work; default work/out/texlib/<preset>-<content hash>.
    """
    from .build import make

    return obj(**make(preset, size=size, seed=seed, tint=tint, out=out))


@op("texlib.list", mcp=False, group="texture", long_running=True,
    summary="List the procedural SA texture presets with their roles and a labelled sheet of baked DXT previews. "
            "Use --no-previews for a metadata-only list without Blender.",
    summary_ru="Список процедурных текстур SA с ролями и листом запечённых образцов; --no-previews выводит только описание.",
    examples=("satk texlib list --no-previews", "satk texlib list"))
def texlib_list(previews: bool = True, size: int = 128, seed: int = 0, out: str | None = None) -> dict:
    """List all presets, optionally baking a contact sheet.

    Args:
        previews: bake missing previews and return one labelled PNG sheet.
        size: native size of each preview, 128 or 256 pixels.
        seed: deterministic variation shared by the preview cells.
        out: output directory under work; default work/out/texlib/sheets/<content hash>.
    """
    from .build import listing

    return listing(previews=previews, size=size, seed=seed, out=out)


@op("texlib.vanilla", mcp=False, group="texture",
    summary="Find vanilla world textures by name, role and approximate indexed colour. Returns tex SIDs, "
            "IDE TXD names, vanilla:<txd>/<tex> presets and streaming constraints without copying pixels.",
    summary_ru="Найти текстуры мира по имени, роли и примерному цвету; вернуть SID, имя TXD для IDE и ограничения без копирования пикселей.",
    examples=("satk texlib vanilla brick --role wall", "satk texlib vanilla asphalt --role road --colour grey"))
def texlib_vanilla(query: str, role: str = "auto", colour: str | None = None,
                   limit: int = 20, cursor: str | None = None) -> dict:
    """Search the vanilla index for reusable map-object textures.

    Args:
        query: name words or * / ? wildcard; a colour word can be combined with a material word.
        role: auto, wall, road, ground, roof, floor, wood, metal, glass, vegetation, fabric, prop or decal.
        colour: colour name or #RRGGBB; rank by the index's approximate mean RGB without decoding textures.
        limit: maximum rows to return, 1 to 500 (default 20).
        cursor: next-page cursor returned by the previous search.
    """
    from .vanilla import search

    return search(query, role=role, colour=colour, limit=limit, cursor=cursor)
