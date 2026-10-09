"""Operations of ``satk.texmod`` (owner M2-03), all CLI only (``mcp=False``):

* ``texture.extract`` -> ``satk texture extract <txd>``: PNGs + ``texmod.json`` in ``work/out/texmod/<txd>/``;
* ``texture.pack``    -> ``satk texture pack <folder>``: images -> ``work/out/mods/<name>/<file>.txd``;
* ``texture.replace`` -> ``satk texture replace <txd> <tex>=<image>...``: a TXD copy with swapped textures;
* ``texture.finish``  -> ``satk texture finish <image> --preset interior``: the SA look without photographs;
* ``texture.new``     -> ``satk texture new <name> --size 256 128 --color #7a7c7c``: a flat, gradient or banded base image.

Module-level imports stay stdlib/satk only (SPEC §2.3).
"""

from __future__ import annotations

from typing import Literal

from ..core.errors import SatkError
from ..core.registry import op

AssetClass = Literal["vehicle", "ped", "weapon", "map", "lod"]
Format = Literal["auto", "dxt1", "dxt3", "dxt5", "a8r8g8b8", "x8r8g8b8", "r5g6b5", "a1r5g5b5", "a4r4g4b4"]
Quality = Literal["fast", "normal", "high"]
Profile = Literal["vanilla", "installed", "samp"]


def _limits(mips: int | None, max_size: int) -> None:
    if mips is not None and mips < 0:
        raise SatkError("BAD_PARAMS", f"mips must be >= 0, got {mips}", hint="0 = no mips, omit for the default")
    if max_size < 0 or max_size > 8192:
        raise SatkError("BAD_PARAMS", f"max_size must be 0..8192, got {max_size}")


@op("texture.pack", mcp=False,
    summary="Pack a folder of images into a TXD at work/out/mods/<name>/<file>.txd (or --out <mod folder>); "
            "--asset-class vehicle|ped|weapon|map|lod picks formats and mip levels like the vanilla game "
            "(vehicles: DXT1/DXT3, 1 level, never DXT5). Round-trip checked, rows carry PSNR.",
    summary_ru="Собрать TXD из папки картинок в work/out/mods/<name>/ (или --out <папка мода>); --asset-class "
               "задаёт форматы и мипы как в игре (машины: DXT1/DXT3, 1 уровень). Результат перечитывается.",
    examples=("satk texture pack bistro --name bistro_hd", "satk texture pack signs --name signs --format dxt5 --max-size 512",
              "satk texture pack mycar_tex --asset-class vehicle --file premier.txd --out mymod/premier"))
def texture_pack(folder: str, name: str | None = None, file: str | None = None, format: Format = "auto",  # noqa: A002
                 mips: int | None = None, quality: Quality = "normal", pot: bool = True, max_size: int = 0,
                 asset_class: AssetClass | None = None, out: str | None = None) -> dict:
    """Images (one texture each, named by the file) -> TXD in the modloader layout.

    Args:
        folder: image folder: absolute, relative to the current folder or to work/out/texmod (texture extract).
        name: mod folder under work/out/mods (default: the folder name).
        file: TXD file name (default: <name>.txd).
        format: auto = DXT1 opaque, DXT1+1-bit alpha, DXT5 smooth alpha (or as in texmod.json); with
            --asset-class: the class formats (DXT3, never DXT5, for smooth alpha; X8R8G8B8/A8R8G8B8 for peds).
        mips: levels; omit = full chain (or as extracted, or as the class), 0 = none.
        quality: DXT endpoint search: fast | normal | high.
        pot: resize sides to powers of two.
        max_size: shrink the longest side to this (0 = keep).
        asset_class: vehicle|ped|weapon|map|lod: formats and mip levels as in the vanilla game (vehicle, ped,
            weapon, lod: one level; map: a full chain from 256 px).
        out: write <out>/<file> into this folder (absolute or relative to the current folder; a mod folder),
            without a README, instead of work/out/mods/<name>/.
    """
    from .api import pack

    _limits(mips, max_size)
    return pack(folder, name=name, file=file, choice=format, mips=mips, quality=quality, pot=pot, max_size=max_size,
                asset_class=asset_class, out=out)


@op("texture.new", mcp=False,
    summary="Make a flat, gradient or banded base image of any size (a multiple of 4) for an own texture: a base "
            "colour, an optional two-colour gradient and UV rectangles (u0:v0:u1:v1=#hex, v up like uv.fit); "
            "writes work/out/texmod/new/<name>.png. No Pillow script needed.",
    summary_ru="Плоская, градиентная или поясная базовая картинка любого размера (кратного 4) для своей текстуры: "
               "цвет, градиент и прямоугольники в UV (u0:v0:u1:v1=#hex); work/out/texmod/new/<имя>.png.",
    examples=("satk texture new bin_base --size 256 128 --color #7a7c7c --rect 0:0:1:0.6=#5c7a68",
              "satk texture new wall --size 128 --color #6a6c6c --color2 #8a8c8c --gradient v"))
def texture_new(name: str, size: list[int] | None = None, color: str = "#808080", color2: str | None = None,
                gradient: Literal["none", "u", "v"] = "none", rect: list[str] | None = None, alpha: int = 255,
                out: str | None = None) -> dict:
    """Paint a base image: colour, gradient, rectangles (deterministic).

    Args:
        name: texture name = file stem (a-z 0-9 _ -, at most 31 characters).
        size: W H in pixels, or one number for a square (each side a multiple of 4; default 256).
        color: base colour: #rrggbb, #rgb, #rrggbbaa or r,g,b[,a].
        color2: far-end colour of a gradient.
        gradient: none, v (colour at the bottom v=0 to color2 at the top v=1) or u (left to right).
        rect: rectangles painted over it in order, each u0:v0:u1:v1=#rrggbb in UV space (0..1, v up: the
            rectangles uv.fit and kit.uv_region use).
        alpha: base alpha 0-255 (255 = opaque).
        out: output folder (default work/out/texmod/new).
    """
    from .newimg import new_image

    return new_image(name, size=size, color=color, color2=color2, gradient=gradient, rects=rect, alpha=alpha, out=out)


@op("texture.finish", mcp=False,
    summary="Give a flat, drawn or 4x-painted image the soft SA texture look: photo-like variation (--photo: "
            "tonal drift, hue drift, light gradient; no grime) or soft noise, AO from a mask, optional grime and "
            "edge wear, a soft filter, a supersampled path; lands in its role's vanilla band. PNG + DXT1 preview.",
    summary_ru="Придать плоской или нарисованной картинке вид текстур SA без фото: шум двух масштабов, AO по "
               "маске, потёртые края по маске краёв, обесцвечивание, грязь; значения подгоняются под полосу "
               "ванили для роли. PNG и превью DXT1.",
    examples=("satk texture finish interior.png --preset interior",
              "satk texture finish seat_512.png --preset interior --supersample 4",
              "satk texture finish wall.png --preset wall --mask wall_ao.png",
              "satk texture finish bin.png --mask bin_ao.png --edge bin_edge.png --role prop",
              "satk texture finish bin_paint_1024.png --supersample 4 --photo 0.6 --role prop"))
def texture_finish(image: str, preset: Literal["photo_like", "interior", "wheel", "wall"] = "photo_like",
                   mask: str | None = None, out: str | None = None, edge: str | None = None, role: str = "auto",
                   profile: Profile = "vanilla", grime: float | None = None, wear: float | None = None,
                   grain: float | None = None, soft: float = 0.5, supersample: int = 1, photo: float = 0.0) -> dict:
    """Finish one image for the SA look (deterministic: the same image and preset give the same file).

    Args:
        image: PNG (or any image Pillow reads): absolute, relative to the current folder or to work/out/texmod.
        preset: photo_like (keep the value) | interior (dark: vanilla value 0.09-0.12-0.26) | wheel | wall (tiles).
        mask: grey image for ambient occlusion (white = open, black = occluded); another size is resized.
        out: output folder (default work/out/texmod/finish).
        edge: grey image of worn edges (white = edge; kit.bake writes <object>_edge.png): edges turn lighter.
        role: style role whose vanilla band the result lands in (interior wheel decal body ped weapon wall ground
            prop generic); auto = the preset's, else guessed from the name, else prop.
        profile: profile whose vanilla textures give the role bands.
        grime: 0..1 darker blotches heavier at the bottom; default the preset's, 0 for vehicle roles (interior,
            wheel, decal, body: the engine's dirt level dirties a car).
        wear: 0..1 strength of the edge wear from --edge; default 0.35, 0 for vehicle roles.
        grain: 0..1 share of fine grain in the noise (default the preset's); lower is softer.
        soft: blur of the soft filter in output pixels (0 = sharp; default 0.5: slightly out of focus like SA).
        supersample: the image was painted at 1, 2, 4 or 8 times the texture size; the finish works at that size
            and downsizes with the soft filter (paint at 4x, e.g. 512 px for a 128 px texture).
        photo: 0..1 photo-like variation instead of the noise: soft low-frequency tonal drift, mottling and a soft
            grain, a slight hue drift and a soft light gradient (none on wall and ground), landed on the role's
            vanilla fine tonal variation (p10 + photo x (p50 - p10)); keeps the painted structure, grime 0 by
            default. 0 = off. Use it on clean paint that style.texture calls flat/CG-clean (0.6 is a good start).
    """
    from .finish import finish

    return finish(image, preset=preset, mask=mask, out=out, edge=edge, role=role, profile=profile, grime=grime,
                  wear=wear, grain=grain, soft=soft, supersample=supersample, photo=photo)


@op("texture.replace", mcp=False,
    summary="Copy a TXD with textures swapped for images (<tex>=<image>), keeping the rest byte for byte; "
            "writes work/out/mods/<name>/<txd file> + README; rows carry PSNR.",
    summary_ru="Копия TXD с заменой текстур на картинки (<текстура>=<картинка>), остальное байт в байт; "
               "пишет work/out/mods/<name>/<файл TXD> и README; в строках PSNR.",
    examples=("satk texture replace txd:bistro Plate=bistro/Marble.png --name bistro_swap",
              "satk texture replace models/gta3.img/bistro.txd Plate=plate.png Panel=panel.png --format dxt1"))
def texture_replace(txd: str, swaps: list[str], name: str | None = None, file: str | None = None,
                    format: Format = "auto", mips: int | None = None, quality: Quality = "normal",  # noqa: A002
                    pot: bool = True, max_size: int = 0, add: bool = False, profile: Profile = "vanilla") -> dict:
    """Replace (or with --add, append) textures of a TXD.

    Args:
        txd: txd:<name>, file:<relpath>, <img>/<entry>, or a .txd path (absolute or under the profile root).
        swaps: <texture>=<image> pairs (images: absolute, current folder or work/out/texmod).
        name: mod folder under work/out/mods (default: the TXD name).
        file: output file name (default: the TXD's own, so modloader replaces it).
        format: auto = keep the family of the replaced texture (DXT stays DXT, raw stays raw).
        mips: levels; omit = like the replaced texture, 0 = none.
        quality: DXT endpoint search: fast | normal | high.
        pot: resize sides to powers of two.
        max_size: shrink the longest side to this (0 = keep).
        add: append textures that the TXD does not have.
        profile: profile whose root and index resolve the TXD.
    """
    from .api import replace

    _limits(mips, max_size)
    return replace(txd, swaps, name=name, file=file, choice=format, mips=mips, quality=quality, pot=pot,
                   max_size=max_size, add=add, profile=profile)


@op("texture.extract", mcp=False,
    summary="Write every texture of a TXD (mip 0) as <name>.png plus texmod.json to work/out/texmod/<txd>/ "
            "for editing and texture pack; edited PNGs are kept.",
    summary_ru="Выгрузить все текстуры TXD (mip 0) в <имя>.png и texmod.json в work/out/texmod/<txd>/ для "
               "правки и texture pack; изменённые PNG не перезаписываются.",
    examples=("satk texture extract txd:bistro", "satk texture extract models/gta3.img/bistro.txd --force"))
def texture_extract(txd: str, out: str | None = None, force: bool = False, profile: Profile = "vanilla") -> dict:
    """TXD -> PNG files named after the textures.

    Args:
        txd: txd:<name>, file:<relpath>, <img>/<entry>, or a .txd path.
        out: folder (default work/out/texmod/<txd>).
        force: overwrite PNGs that were edited since the last extract.
        profile: profile whose root and index resolve the TXD.
    """
    from .api import extract

    return extract(txd, out=out, force=force, profile=profile)
