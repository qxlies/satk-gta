"""Operations of ``satk.txdopt``, all CLI only (``mcp=False``; MCP reaches them through ``satk_op``):

* ``texture.optimize`` -> ``satk texture optimize <txd|dir|mod>``: smaller TXD copies in ``work/out/txdopt/<out>/``;
* ``texture.audit``    -> ``satk texture audit <txd|dir|mod>``: what is oversized, uncompressed, NPOT, mip-less,
  alpha-wrong, duplicated or unused, with a per-issue summary;
* ``texture.budget``   -> ``satk texture budget [--profile P|<mod>] [--area x y r]``: streaming bytes of DFF + TXD
  against vanilla and the default streaming memory limit.

Module-level imports stay stdlib/satk only (fast imports).
"""

from __future__ import annotations

from typing import Literal

from ..core.envelope import clamp_limit
from ..core.errors import SatkError
from ..core.registry import op

Dxt = Literal["auto", "dxt1", "dxt5", "keep"]
Quality = Literal["fast", "normal", "high"]


def _max(value: int) -> int:
    if value < 0 or value > 8192:
        raise SatkError("BAD_PARAMS", f"--max must be 0..8192 (0 = keep sizes), got {value}", hint="--max 512")
    return int(value)


@op("texture.optimize", mcp=False, long_running=True,
    summary="Optimise the TXDs of a TXD, mod folder, .zip or .img into work/out/txdopt/<out>/: drop textures no DFF "
            "uses, dedupe, cap size to a power of two, DXT1/DXT5 for uncompressed, lossless DXT3/5->DXT1, mips; "
            "rows carry PSNR.",
    summary_ru="Оптимизировать TXD (файл, папка мода, .zip, .img) в work/out/txdopt/<out>/: убрать неиспользуемые "
               "текстуры и дубли, ограничить размер степенью двойки, DXT1/DXT5 вместо несжатых, мипы; в строках PSNR.",
    examples=("satk texture optimize txd:bistro --max 128",
              "satk texture optimize mymod --max 512 --drop-unused --dedupe",
              "satk texture optimize mymap.img --max 1024 --mips --share --out mymap_small"))
def texture_optimize(target: str, max: int = 0, mips: bool = False, dxt: Dxt = "auto",  # noqa: A002
                     drop_unused: bool = False, dedupe: bool = False, share: bool = False,
                     keep: list[str] | None = None, out: str | None = None, pot: bool = True,
                     quality: Quality = "normal", full: bool = False, profile: str = "vanilla",
                     limit: int = 20) -> dict:
    """Write optimised copies of the TXDs of a target (the target is only read).

    Args:
        target: txd:<name>, <img>/<entry>, a .txd, mod folder, .zip or .img (absolute, current folder or profile root).
        max: longest side after optimising (0 = keep sizes); halving keeps the aspect ratio.
        mips: add a full mip chain to textures that have one level.
        dxt: auto = DXT1 opaque / DXT1+1-bit alpha / DXT5 smooth alpha for uncompressed textures and opaque
            DXT3/5 -> DXT1; dxt1 or dxt5 force the alpha variant; keep = no format changes.
        drop_unused: drop textures that no DFF of the TXD's models names (mod IDE/DFF + the profile index).
        dedupe: drop later textures with the same name and pixels in one TXD.
        share: also move textures identical in several TXDs into one new parent TXD + txdp lines (implies dedupe).
        keep: texture-name globs never dropped (names used by scripts, SA-MP or MTA shaders).
        out: output folder under work/out/txdopt (default: the target's name).
        pot: resize sides to powers of two (nearest, upscaling at most 30 %).
        quality: DXT endpoint search: fast | normal | high.
        full: also copy the target's other files, so the output folder is a complete mod.
        profile: profile whose root resolves relative paths and whose index knows the models.
        limit: rows shown (all rows are in txdopt.json).
    """
    from .optimize import Opts, optimize

    o = Opts(max=_max(max), pot=pot, mips=mips, dxt=dxt, drop_unused=drop_unused, dedupe=dedupe, share=share,
             keep=tuple(keep or ()), quality=quality, profile=profile, full=full)
    return optimize(target, o, out=out, limit=clamp_limit(limit))


@op("texture.audit", mcp=False, long_running=True,
    summary="Audit the TXDs of a TXD, mod folder, .zip or .img: oversized, uncompressed, non-power-of-two, missing "
            "mips, DXT1 holes, unused alpha, duplicates, unused textures; findings by bytes saved plus a per-issue "
            "summary with the fix.",
    summary_ru="Аудит TXD (файл, папка мода, .zip, .img): слишком большие, несжатые, не степень двойки, без мипов, "
               "дыры DXT1, лишняя альфа, дубли, неиспользуемые; находки по экономии и сводка по проблемам.",
    examples=("satk texture audit txd:bistro", "satk texture audit mymod --unused",
              "satk texture audit models/gta3.img --issue uncompressed --limit 50"))
def texture_audit(target: str, max: int = 1024, issue: list[str] | None = None,  # noqa: A002
                  unused: bool = False, keep: list[str] | None = None, profile: str = "vanilla",
                  limit: int = 20, cursor: str | None = None) -> dict:
    """Audit textures.

    Args:
        target: txd:<name>, <img>/<entry>, a .txd, mod folder, .zip or .img (absolute, current folder or profile root).
        max: a longer side than this is 'oversized'.
        issue: show only these issues (the summary always counts all).
        unused: also find textures no DFF of the TXD's models names (reads the DFFs; needs the index for game models).
        keep: texture-name globs never reported as unused.
        profile: profile whose root resolves relative paths and whose index knows the models.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from .audit import audit

    return audit(target, max_side=_max(max), issues=list(issue or []), unused=unused, keep=keep, profile=profile,
                 limit=clamp_limit(limit), cursor=cursor)


@op("texture.budget", mcp=False,
    summary="Streaming memory (IMG bytes of DFF + TXD chains) of a profile or a mod overlaid on vanilla, for all "
            "models or the placements near --area x y r; top offenders with vanilla sizes and the 50 MiB default "
            "limit from the kb.",
    summary_ru="Память стриминга (байты DFF + цепочек TXD) профиля или мода поверх ванили, для всех моделей или "
               "у точки --area x y r; крупнейшие ресурсы с ванильными размерами и лимит 50 МиБ из kb.",
    examples=("satk texture budget", "satk texture budget --area 2495 -1666 150",
              "satk texture budget --profile modloader/mymap --area 1500 -1600 200 --sort delta"))
def texture_budget(profile: str = "vanilla", area: list[float] | None = None,
                   lod: Literal["all", "hd", "lod"] = "all", kind: Literal["all", "txd", "dff"] = "all",
                   sort: Literal["size", "delta"] = "size", base: str = "vanilla", limit: int = 20,
                   cursor: str | None = None) -> dict:
    """Streaming budget.

    Args:
        profile: a profile (vanilla, installed, samp) or a mod folder/.zip/.img overlaid on --base.
        area: x y r: only placements (exterior) whose bounding box reaches this circle.
        lod: placements counted in --area: all, hd or lod.
        kind: rows of this kind only (the totals count both).
        sort: size (largest first) or delta (largest growth against vanilla first).
        base: profile a mod is overlaid on.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from .budget import budget

    return budget(profile, area=area, lod=lod, kind=kind, sort=sort, base=base, limit=clamp_limit(limit),
                  cursor=cursor)
