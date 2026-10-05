"""Operations of ``satk.texmod`` (owner M2-03), all CLI only (``mcp=False``):

* ``texture.extract`` -> ``satk texture extract <txd>``: PNGs + ``texmod.json`` in ``work/out/texmod/<txd>/``;
* ``texture.pack``    -> ``satk texture pack <folder>``: images -> ``work/out/mods/<name>/<file>.txd``;
* ``texture.replace`` -> ``satk texture replace <txd> <tex>=<image>...``: a TXD copy with swapped textures.

Module-level imports stay stdlib/satk only (SPEC §2.3).
"""

from __future__ import annotations

from typing import Literal

from ..core.errors import SatkError
from ..core.registry import op

Format = Literal["auto", "dxt1", "dxt3", "dxt5", "a8r8g8b8", "x8r8g8b8", "r5g6b5", "a1r5g5b5", "a4r4g4b4"]
Quality = Literal["fast", "normal", "high"]
Profile = Literal["vanilla", "installed", "samp"]


def _limits(mips: int | None, max_size: int) -> None:
    if mips is not None and mips < 0:
        raise SatkError("BAD_PARAMS", f"mips must be >= 0, got {mips}", hint="0 = no mips, omit for the default")
    if max_size < 0 or max_size > 8192:
        raise SatkError("BAD_PARAMS", f"max_size must be 0..8192, got {max_size}")


@op("texture.pack", mcp=False,
    summary="Pack a folder of images into a TXD (DXT1/3/5 or raw, mipmaps) at work/out/mods/<name>/<file>.txd "
            "with a README; round-trip checked, rows carry PSNR.",
    summary_ru="Собрать TXD из папки картинок (DXT1/3/5 или без сжатия, мип-уровни) в work/out/mods/<name>/ "
               "с README; результат перечитывается, в строках PSNR.",
    examples=("satk texture pack bistro --name bistro_hd", "satk texture pack signs --name signs --format dxt5 --max-size 512"))
def texture_pack(folder: str, name: str | None = None, file: str | None = None, format: Format = "auto",  # noqa: A002
                 mips: int | None = None, quality: Quality = "normal", pot: bool = True, max_size: int = 0) -> dict:
    """Images (one texture each, named by the file) -> TXD in the modloader layout.

    Args:
        folder: image folder: absolute, relative to the current folder or to work/out/texmod (texture extract).
        name: mod folder under work/out/mods (default: the folder name).
        file: TXD file name (default: <name>.txd).
        format: auto = DXT1 opaque, DXT1+1-bit alpha, DXT5 smooth alpha (or as in texmod.json).
        mips: levels; omit = full chain (or as extracted), 0 = none.
        quality: DXT endpoint search: fast | normal | high.
        pot: resize sides to powers of two.
        max_size: shrink the longest side to this (0 = keep).
    """
    from .api import pack

    _limits(mips, max_size)
    return pack(folder, name=name, file=file, choice=format, mips=mips, quality=quality, pot=pot, max_size=max_size)


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
