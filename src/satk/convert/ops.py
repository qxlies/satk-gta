"""Conversion operations; discovery is stdlib-only and adds no dedicated MCP tools."""

from __future__ import annotations

from typing import Literal

from ..core.envelope import obj
from ..core.registry import op

Tier = Literal["sa_plus", "vanilla"]


@op("asset.convert", group="blender", mcp=False, long_running=True,
    summary="Convert a GLB/glTF, FBX, OBJ, DAE or blend into an SA asset: class scale and budgets, seam-aware "
            "reduction, Cycles texture bake and SA finish, kit shading, prelight, COL/LOD, kit package, check and lineup.",
    summary_ru="Преобразовать трёхмерную модель в ассет SA: масштаб, упрощение, запекание текстур, освещение, "
               "коллизия, уровни детализации, упаковка и проверка.",
    examples=("satk asset convert model.glb --kind prop", "satk asset convert car.fbx --kind vehicle --like model:426"))
def asset_convert(model: str, kind: str = "prop", like: str | None = None, tier: Tier = "sa_plus",
                  dims: list[float] | None = None, out: str | None = None, name: str | None = None,
                  session: str | None = None, profile: str = "vanilla", timeout: float = 600.0) -> dict:
    """Convert a rigid model and return its package, measured checks and preview sheet.

    Args:
        model: local .glb, .gltf, .fbx, .obj, .dae or .blend; external textures stay read-only.
        kind: kit kind, including prop, building, vehicle (automobile), weapon, interior and pickup.
        like: vanilla model SID for the frame scaffold, scale anchors and style peer set.
        tier: measured vanilla budgets or the sa_plus proposal; visual rules remain the same.
        dims: target L,W,H in metres (Blender Y,X,Z); omitted = class anchors, or the like model's dimensions.
        out: private output folder under work; default work/out/convert/<name>-<content hash>.
        name: new model name; default a safe form of the source file stem.
        session: running studio session; omitted starts and stops a private headless session.
        profile: index and style profile used for the scaffold and lineup.
        timeout: seconds allowed per Blender call.
    """
    from .pipeline import run

    return obj(None, **run(model, kind=kind, like=like, tier=tier, dims=dims, out=out, name=name,
                           session=session, profile=profile, timeout=timeout))


@op("convert.prepare", group="blender", mcp=False, long_running=True,
    summary="Write a conversion plan from style and kit data. With --session, load convert.* K3 methods into "
            "the studio for import, normalize, clean, reduce, bake and assemble steps.",
    summary_ru="Подготовить план преобразования по данным стиля и заготовки; загрузить пошаговые методы в сессию.",
    examples=("satk convert prepare model.glb --kind prop --session modelling",))
def convert_prepare(model: str, kind: str = "prop", like: str | None = None, tier: Tier = "sa_plus",
                    dims: list[float] | None = None, out: str | None = None, name: str | None = None,
                    session: str | None = None, profile: str = "vanilla", timeout: float = 120.0) -> dict:
    """Prepare a plan without converting geometry.

    Args:
        model: local source model.
        kind: kit kind (vehicle is an alias for automobile).
        like: reference model SID.
        tier: SA style tier.
        dims: target L,W,H in metres.
        out: private folder under work.
        name: new model name.
        session: load the conversion methods into this running session through a journaled bootstrap.
        profile: index and style profile.
        timeout: seconds for loading the session methods.
    """
    from . import plan
    from .pipeline import install

    p, path = plan.prepare(model, kind=kind, like=like, tier=tier, dims=dims, out=out, name=name, profile=profile)
    if session:
        install(session, timeout)
    return obj(None, plan=str(path).replace("\\", "/"), name=p["name"], kind=p["kind"],
               dims=p["dims"], budget=p["budget"], session=session, warn=p.get("warn", []))


@op("convert.finish", group="blender", mcp=False, long_running=True,
    summary="Finish the baked textures of a conversion plan using texture.finish and measured style.texture "
            "role targets. Run between the convert.bake and convert.assemble studio methods.",
    summary_ru="Обработать запечённые текстуры по измеренным требованиям стиля между запеканием и сборкой.",
    examples=("satk convert finish conversion/plan.json",))
def convert_finish(plan: str) -> dict:
    """Finish a conversion's Cycles bakes.

    Args:
        plan: plan.json returned by convert.prepare.
    """
    from .finish import run

    return obj(None, **run(plan))
