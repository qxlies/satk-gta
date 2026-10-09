"""Operations of satk.kit (CLI ``satk kit kinds|template|export|scene-spec``); all ``mcp=False``.

MCP clients reach them through ``satk_ops``/``satk_op``. Module-level imports are stdlib only; Blender runs in
a studio session or a one-shot job (``satk.studio.api``, contract K4).
"""

from __future__ import annotations

from typing import Literal

from ..core.envelope import obj, table
from ..core.errors import SatkError
from ..core.registry import op

Tier = Literal["vanilla", "sa_plus"]


@op("kit.kinds",
    summary="Asset kinds catalog for authoring any GTA:SA asset (cars, bikes, boats, planes, helis, trailers, trains, "
            "props, buildings+LOD, interiors, weapons, peds, pickups, upgrades): vanilla exemplar, frames, slots, "
            "material presets, COL/LOD/TXD recipes, atlas regions, game-ready classes.",
    summary_ru="Каталог видов ассетов для создания (машины, мотоциклы, лодки, самолёты, вертолёты, прицепы, поезда, "
               "пропы, здания+LOD, интерьеры, оружие, педы, пикапы, тюнинг): образец, кадры, слоты, пресеты.",
    mcp=False, group="blender",
    examples=("satk kit kinds", "satk kit kinds --kind automobile", "satk kit kinds --materials",
              "satk kit kinds --atlas", "satk kit kinds --classes"))
def kit_kinds(kind: str | None = None, materials: bool = False, atlas: bool = False, classes: bool = False,
              limit: int = 40) -> dict:
    """List asset kinds, or the details of one kind, the material presets, the atlas regions or the classes.

    Args:
        kind: one kind (automobile, bike, boat, plane, heli, prop, building, weapon, ped, ...) with its frames,
            slots, material roles, collision, LOD, TXD and data lines.
        materials: list the material presets (role, colour, texture, env/specular/reflection).
        atlas: list the named regions of the shared vehicle atlases with their measured vanilla support.
        classes: list the blender.game_ready classes (texel density, draw, collision, LOD).
        limit: rows to return.
    """
    from . import kinds as K

    if materials:
        rows = []
        for r, p in K.materials()["roles"].items():
            fx = "+".join(x for x in ("env", "specular", "reflection") if p.get(x))
            rows.append([r, p.get("group"), p.get("rgba"), p.get("texture") or "-", fx or "-", p.get("summary", "")[:90]])
        return table(["role", "group", "rgba", "texture", "fx", "what"], rows[:limit], total=len(rows))
    if atlas:
        from . import atlas as A

        rows = [[k, v["texture"], v["rect"], v["support"], v["usage"], v["kind"]] for k, v in A.regions().items()]
        return table(["region", "texture", "rect_uv", "support", "usage", "kind"], rows[:limit], total=len(rows),
                     warn=["INFO: rect = [u0, v0, u1, v1], v down (DFF); Blender v = 1 - v"])
    if classes:
        cs = K.classes()["classes"]
        rows = [[k, v.get("texel_px_m") or "by size", v.get("draw"), v.get("col"), v.get("lod"), v.get("summary")]
                for k, v in cs.items()]
        return table(["class", "texel_px_m", "draw", "col", "lod", "what"], rows, total=len(rows))
    if kind:
        k = K.get(kind)
        out = {k2: v for k2, v in k.items() if k2 not in ("examples",)}
        fr = dict(out.get("frames") or {})
        if isinstance(fr.get("optional"), dict) and len(fr["optional"]) > 12:
            fr["optional"] = dict(list(fr["optional"].items())[:12])
        out["frames"] = fr
        if K.canonical(kind) != "ped":
            out.pop("bones", None)
        return obj(None, kind=K.canonical(kind), **out)
    rows = [[n, v["group"], v.get("like"), v.get("like_name"), v.get("peers"), v.get("summary", "")[:80]]
            for n, v in K.catalog()["kinds"].items()]
    return table(["kind", "group", "like", "like_name", "peers", "what"], rows[:limit], total=len(rows))


@op("kit.template",
    summary="Create (scaffold) a new asset in Blender for any kind: frames and dummies in vanilla order from --like SID "
            "or --kind, empty part slots, material presets, COL skeleton, LOD pair, optional vanilla ghost; names "
            "and numbers only. Session or one-shot .blend.",
    summary_ru="Создать заготовку нового ассета в Blender любого вида: кадры и пустышки в порядке ванили (--like или "
               "--kind), пустые слоты деталей, пресеты материалов, скелет COL, пара LOD, призрак ванили.",
    mcp=False, group="blender", long_running=True,
    examples=("satk kit template --like model:426 --name mycar --dims 5.9,2.3,1.5",
              "satk kit template --kind bike --name mybike --session default",
              "satk kit template --kind prop --name mybin --plan-only",
              "satk kit template --kind prop --name mybin --lod --session default"))
def kit_template(like: str | None = None, kind: str | None = None, name: str | None = None,
                 dims: list[float] | None = None, tier: Tier = "sa_plus", ghost: bool = False,
                 session: str | None = None, out: str | None = None, plan_only: bool = False, replace: bool = False,
                 textures: bool = True, profile: str = "vanilla", timeout: float = 300.0, lod: bool = False) -> dict:
    """Template plan + Blender scaffold.

    Args:
        like: vanilla model whose frame tree, dummies and numbers are copied (model:426, premier).
        kind: asset kind (satk kit kinds); without --like its catalog exemplar is used.
        name: new model name (a-z 0-9 _; vehicles <= 17 characters); default: the like model's name (a replacement).
        dims: L,W,H in metres; frame positions scale per axis (wheels keep wheel_scale).
        tier: sa_plus (default for new assets) or vanilla (blends into traffic).
        ghost: also import the vanilla model as a read-only wire ghost (never exported).
        session: studio session name; omitted = 'default' when it runs, else a one-shot Blender that saves a .blend.
        out: output folder under work/ (default work/out/kit/<name>).
        plan_only: write the plan JSON only, no Blender.
        replace: rebuild the model when it is already in the scene.
        textures: give shared atlas textures their vanilla pixels in the scene (preview only, never exported).
        profile: game profile.
        timeout: seconds to wait for Blender.
        lod: give a map model that is not a building (a prop) a LOD slot too (lod<name>, filled by kit.lod).
    """
    import json
    import time

    from ..core import paths
    from . import plan as P
    from .export import out_dir

    t0 = time.perf_counter()
    plan = P.template_plan(like=like, kind=kind, name=name, dims=dims, tier=tier, profile=profile, ghost=ghost,
                           lod=lod)
    d = out_dir(plan["name"], out)
    d.mkdir(parents=True, exist_ok=True)
    warn = list(plan.get("warn") or [])
    if textures and not plan_only:
        plan["textures"] = _shared_pngs(plan, profile, warn)
    path = paths.atomic_write(d / "template.json", json.dumps(plan, ensure_ascii=False, indent=1))
    res: dict = {"kind": plan["kind"], "name": plan["name"], "tier": tier, "like": plan["like"]["sid"],
                 "plan": paths.jpath(path), "counts": plan["counts"], "dims": plan["dims"]["target"]}
    if plan_only:
        return obj(None, **res, warn=warn)
    from ..studio import api

    blend = d / f"{plan['name']}.blend"
    r = api.call("kit.template", {"plan": str(path), "replace": replace}, session=session, save=str(blend),
                 timeout=timeout, stats="none")
    br = r.get("result") or {}
    warn += [str(w) for w in (br.get("warn") or [])] + [str(w) for w in (r.get("warn") or [])]
    res.update(blend=r.get("saved") or paths.jpath(blend), mode=r.get("mode") or "session",
               session=r.get("session"), blender={k: br.get(k) for k in ("frames", "slots", "dummies", "materials",
                                                                         "col", "lod", "ghost", "collection")},
               seconds=round(time.perf_counter() - t0, 2))
    return obj(None, **res, warn=warn)


def _shared_pngs(plan: dict, profile: str, warn: list[str]) -> dict:
    """``{texture: png}`` of the shared textures the presets use (vanilla pixels for previews only)."""
    from ..core.registry import get_op
    from . import kinds as K

    names = sorted({str(p.get("texture")).lower() for p in plan.get("presets", {}).values()
                    if p.get("shared") and p.get("texture") and K.is_shared(p.get("texture"))})
    if not names or plan.get("group") != "vehicle":
        return {}
    try:
        spec = get_op("texture.image")
        r = spec.call({"ids": [f"tex:vehicle/{n}" for n in names], "mode": "png", "profile": profile})
    except SatkError as e:
        warn.append(f"{e.code}: shared textures stay grey placeholders ({e.msg})")
        return {}
    files = r.get("files") or []
    return {n: f for n, f in zip(names, files)} if len(files) == len(names) else {}


@op("kit.export",
    summary="Export a kit model from Blender to a game-ready package: DFF (frame-local spheres; map models with baked "
            "prelight), COL via col.gen (primitives for small props), TXD of own textures (DXT), LOD model, Mod "
            "Loader folder (--replace) or mod.add (--add), re-import diff, lint, asset.check.",
    summary_ru="Экспорт модели из Blender в готовый пакет: DFF (локальные сферы, COL <model>_col), COL через col.gen, "
               "TXD только своих текстур, папка Mod Loader (--replace) или mod.add (--add), сверка, lint, check.",
    mcp=False, group="blender", long_running=True,
    examples=("satk kit export --replace model:426", "satk kit export --model mycar --add",
              "satk kit export --blend <workspace>/work/out/kit/mycar/mycar.blend --replace premier"))
def kit_export(model: str | None = None, replace: str | None = None, add: bool = False, name: str | None = None,
               session: str | None = None, blend: str | None = None, out: str | None = None,
               col: Literal["auto", "kit", "none", "box", "boxes", "spheres", "hull", "mesh"] = "auto",
               lint: bool = True, check: bool = True, profile: str = "vanilla", timeout: float = 600.0,
               prelight: Literal["auto", "bake", "simple", "none"] = "auto", place: list[float] | None = None) -> dict:
    """Export and package a kit model.

    Args:
        model: kit model name in the scene (default: the only one).
        replace: vanilla model to replace (model:426, premier): files are named after it, Mod Loader folder.
        add: make a NEW model with mod.add (free id, data lines like the template's model).
        name: file name for a new model (default: the kit model name).
        session: studio session name; omitted = 'default' when it runs, else a one-shot Blender on --blend.
        blend: .blend to open (one-shot mode; kit.template saved one in work/out/kit/<name>/).
        out: output folder under work/ (default work/out/kit/<stem>).
        col: auto (col.gen on the DFF, kit collision as fallback; small props get primitives by the class rule),
            kit (the Blender collision), none, or a col.gen mode for a map model: box, boxes, spheres, hull, mesh.
        lint: run asset.lint on the package.
        check: run asset.check when installed.
        profile: game profile that resolves --replace.
        timeout: seconds to wait for Blender.
        prelight: map models: day and night vertex colours on the exported DFF: auto (bake, unless the mesh already
            has colour attributes), bake, simple (normals only) or none.
        place: --add of a map model with a LOD: X,Y,Z; mod.add also writes the placement (IPL) that links the LOD.
    """
    from .export import run

    res = run(model=model, session=session, blend=blend, replace=replace, add=add, name=name, out=out, col=col,
              lint=lint, check=check, profile=profile, timeout=timeout, prelight=prelight, place=place)
    warn = res.pop("warn", [])
    return obj(None, **res, warn=warn)


@op("kit.scene_spec",
    summary="The Blender scene contract of kit models and exports (collections, frame/slot objects, satk_* tags, "
            "DragonFF properties for materials, COL spheres/boxes, LOD, export rules), optionally with the frames "
            "and roles of one kind. Read this instead of the DragonFF source.",
    summary_ru="Контракт сцены Blender для моделей kit и экспорта (коллекции, объекты кадров и слотов, теги satk_*, "
               "свойства DragonFF, сферы и боксы COL, LOD), с кадрами и ролями вида.",
    mcp=False, group="blender", examples=("satk kit scene-spec", "satk kit scene-spec --kind automobile"))
def kit_scene_spec(kind: str | None = None) -> dict:
    """Scene contract.

    Args:
        kind: add the frames, slots, material roles, collision, LOD and TXD rules of this kind.
    """
    from . import kinds as K

    return obj(None, **K.scene_spec(kind))


@op("kit.blank",
    summary="Optional quick-start body blank (car: sedan coupe sports suv van hatchback wagon suv_boxy pickup; "
            "bike: sport scooter; boat, heli, plane, props, building): soft-shaded quads from dimensions and the "
            "like model's frames, round arches with liners, part regions, clean paint UVs. Session or .blend.",
    summary_ru="Заготовка кузова любого вида (машина, мотоцикл, лодка, вертолёт, самолёт, коробка, цилиндр, здание): "
               "чистая низкополигональная квадовая сетка по габаритам и якорям, петли у арок, пояса и бамперов, "
               "зеркало, границы деталей, швы UV.",
    mcp=False, group="blender", long_running=True,
    examples=("satk kit blank", "satk kit blank --kind automobile --like model:426 --name mycar --session default",
              "satk kit blank --kind automobile --body hatchback --dims 4.6,2.1,1.5 --wheel-d 0.66 --plan-only",
              "satk kit blank --kind bike --body scooter --like model:462 --plan-only",
              "satk kit blank --kind prop_cyl --dims 0.6,0.6,1.1 --plan-only"))
def kit_blank(kind: str | None = None, like: str | None = None, name: str | None = None,
              dims: list[float] | None = None, tier: Tier = "sa_plus", body: str | None = None, split: bool = False,
              fill: bool = False, interior: bool = True, session: str | None = None, out: str | None = None,
              plan_only: bool = False, replace: bool = False, profile: str = "vanilla",
              timeout: float = 300.0, wheel_d: float | None = None) -> dict:
    """Plan + Blender build of a blank; without ``kind`` the catalog of blanks.

    Args:
        kind: automobile, bike, boat, heli, plane, prop_box, prop_cyl or building_box (omit to list them).
        like: vanilla model whose class dimensions, wheel dummies and wheel scale are the numbers (model:426); no vertex is read.
        name: model name (a-z 0-9 _, at most 17); the pieces are named <name>_<piece>; use the kit model's name.
        dims: L,W,H in metres (length along Y, width across X, height; the same order as kit template --dims);
            replaces the class (or --like) dimensions.
        tier: sa_plus (default) or vanilla: the density of loops and segments.
        body: automobile body (sedan, coupe, sports, suv, van, hatchback, wagon, suv_boxy, pickup) or bike body
            (sport, scooter); default from --like, else sedan / sport.
        split: cut the parts out at once (kit.blank_split) instead of keeping one shell with part regions.
        fill: with --split, move the parts into the kit slots of the template of the same name.
        interior: automobile only: also build seat and dash boxes (they join the chassis).
        session: studio session name; omitted = 'default' when it runs, else a one-shot Blender that saves a .blend.
        out: output folder under work/ (default work/out/kit/<name>).
        plan_only: write the plan JSON only, no Blender.
        replace: rebuild the blank when it is already in the scene.
        profile: game profile that resolves --like.
        timeout: seconds to wait for Blender.
        wheel_d: wheel diameter in metres (vehicles); default the like model's wheel scale or the body's.
    """
    import json
    import time

    from ..core import paths
    from . import blanks as B
    from .export import out_dir

    if kind is None:
        rows = [[n, v["group"], v.get("kit_kind"), "yes" if n == "automobile" else "-", v["summary"][:110]]
                for n, v in B.kinds()["kinds"].items()]
        return table(["kind", "group", "kit_kind", "parts_cut", "what"], rows, total=len(rows))
    t0 = time.perf_counter()
    if wheel_d is not None and not 0.2 <= float(wheel_d) <= 2.5:
        raise SatkError("BAD_PARAMS", f"wheel_d must be 0.2..2.5 m, got {wheel_d}")
    plan = B.blank_plan(kind, name=name, like=like, dims=dims, tier=tier, body=body, interior=interior,
                        profile=profile, wheel_d=wheel_d)
    d = out_dir(plan["name"], out)
    d.mkdir(parents=True, exist_ok=True)
    warn = list(plan.get("warn") or [])
    path = paths.atomic_write(d / "blank.json", json.dumps(plan, ensure_ascii=False, indent=1))
    res: dict = {"kind": plan["kind"], "name": plan["name"], "tier": tier, "dims": plan["dims"],
                 "plan": paths.jpath(path)}
    if plan.get("like"):
        res["like"] = plan["like"]["sid"]
    if plan_only:
        res["tris"] = {p["name"]: B.tri_count(p["faces"]) for p in B.mesh_spec(plan)["pieces"]}
        return obj(None, **res, warn=warn)
    from ..studio import api

    blend = d / f"{plan['name']}.blend"
    r = api.call("kit.blank", {"plan": str(path), "replace": replace, "split": split, "fill": fill},
                 session=session, save=str(blend), timeout=timeout, stats="none")
    br = r.get("result") or {}
    warn += [str(w) for w in (br.get("warn") or [])] + [str(w) for w in (r.get("warn") or [])]
    res.update(blend=r.get("saved") or paths.jpath(blend), mode=r.get("mode") or "session", session=r.get("session"),
               objects=br.get("objects"), tris=br.get("tris"), seconds=round(time.perf_counter() - t0, 2))
    if br.get("split"):
        res["parts"] = br["split"].get("parts")
    return obj(None, **res, warn=warn)

