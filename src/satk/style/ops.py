"""Operations of satk.style (all ``mcp=False``: agents reach them through ``satk_ops``/``satk_op``).

* ``style.build``       -> ``satk style build [--force] [--validate] [--textures]`` (lazy cache of vanilla metrics);
* ``style.profile``     -> ``satk style profile <class|SID> [--like SID] [--tier vanilla|sa_plus] [--metrics ...]``;
* ``style.card``        -> ``satk style card <class> [--tier] [--md | --lint-preset]``;
* ``style.texture``     -> ``satk style texture <png|txd|folder|tex:SID> [--role auto]``;
* ``style.brief_check`` -> ``satk style brief-check <brief.md> [--cls car]``;
* ``asset.check``       -> ``satk asset check <dff|folder|SID> [--like SID] [--cls auto] [--tier] [--md]``;
* ``asset.anatomy``     -> ``satk asset anatomy <dff|SID> [--md]``.

Help topics: every ``data/style/topics/<topic>.md`` (at most 6,000 characters) is registered with
``satk help <topic>``; missing files are skipped. Module-level imports stay stdlib/satk only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from ..core import paths, resources
from ..core.envelope import clamp_limit, table
from ..core.errors import SatkError
from ..core.registry import op

Tier = Literal["vanilla", "sa_plus"]


# ----------------------------------------------------------------------------- help topics (K8)
def register_topics(folder: Path | None = None) -> list[str]:
    """Register ``<folder>/*.md`` (default ``data/style/topics``) as help topics; returns their names."""
    names: list[str] = []
    try:
        files = [Path(folder) / f for f in sorted(os.listdir(folder))] if folder is not None else \
            [resources.data_path(*f.split("/")) for f in resources.list_files("style", "topics", pattern="*.md")]
    except (OSError, SatkError):
        return names
    if not files:
        return names
    from ..runtime import help as H

    for p in files:
        p = Path(p)
        if p.suffix.lower() != ".md" or not p.stem.isidentifier():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.strip() or len(text) > H.MAX_TOPIC_CHARS:
            continue
        first = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.strip()), p.stem)
        H.register_topic(p.stem, first[:80], text)
        names.append(p.stem)
    return names


register_topics()


# ----------------------------------------------------------------------------- helpers
def _md(topic: str, text: str, **extra) -> dict:
    return {"ok": True, "topic": topic, "text": text, **extra}


def _cell(v):
    if isinstance(v, float):
        return round(v, 4)
    return v


# ----------------------------------------------------------------------------- style.build
@op("style.build", summary="Build the style cache: metrics of every vanilla model (triangles, shading, UVs, dims, "
                           "texel density, prelight, collision) and p10/p50/p90 per class and peer set (vehicles by "
                           "type and body, map models by class and size). Other style ops build it on first use.",
    summary_ru="Построить кэш стиля: метрики всех моделей ванили и p10/p50/p90 по классам (машины по типу и кузову, "
               "карта по классу и размеру). Остальные операции стиля строят его сами при первом вызове.",
    mcp=False, group="asset", long_running=True,
    examples=("satk style build", "satk style build --validate", "satk style build --force --textures"))
def style_build(profile: str = "vanilla", force: bool = False, validate: bool = False, textures: bool = False,
                workers: int | None = None) -> dict:
    """Build (or reuse) the style cache of a profile.

    Args:
        profile: profile whose index is measured (vanilla by default).
        force: rebuild even when the cache of this index exists.
        validate: run leave-one-out validation (share of vanilla models with 0-1 out-of-band rows, per family).
        textures: also build the vanilla texture distributions of style.texture.
        workers: worker processes (default: CPU count - 1, at most 8; 1 = in-process).
    """
    from . import cache

    if workers is not None and not 1 <= workers <= 32:
        raise SatkError("BAD_PARAMS", f"--workers must be in 1..32, got {workers}")
    r = cache.build(profile, force=force, workers=workers)
    c = cache.load(profile)
    out = {"ok": True, "file": paths.jpath(r["file"]), "built": r["built"], "models": len(c.data["models"]),
           "peer_sets": len(c.peers), "errors": c.data.get("n_errors", 0)}
    if r.get("seconds") is not None:
        out["seconds"] = r["seconds"]
    if validate:
        from . import validate as V

        lo = V.loo(c)
        out["loo"] = {"max_rows": lo["max_rows"], "families": lo["families"],
                      "weak_peer_sets": {k: v for k, v in lo["peer_sets"].items()
                                         if v["pass_share"] < 0.9 and v["models"] >= 8}}
    if textures or validate:
        from . import texture as T

        d = T.vanilla(profile)
        out["textures"] = {k: v["n"] for k, v in d["roles"].items()}
        if validate:
            out["textures_loo"] = {k: v["in_share"] for k, v in T.loo(d).items()}
    return out


# ----------------------------------------------------------------------------- style.profile
@op("style.profile", summary="Vanilla reference numbers of a class or a model's peer set: p10/p50/p90 of "
                             "triangles, shading, UVs, dims, texel density, prelight, collision; exemplars and scale "
                             "anchors. Reference only, never targets. Classes: car.sedan, bike, prop@1-2m, ped.",
    summary_ru="Ваниль SA в числах для класса или модели: p10/p50/p90 и полоса уровня детализации (vanilla или "
               "sa_plus) для треугольников, сглаживания, UV, размеров, текселей, prelight, коллизии; эталоны.",
    mcp=False, group="asset",
    examples=("satk style profile car.sedan --tier vanilla", "satk style profile --like model:426",
              "satk style profile prop@1-2m --metrics geo.tris uv.texel_px_m", "satk style profile --classes"))
def style_profile(target: str | None, like: str | None = None, tier: Tier | None = None,
                  metrics: list[str] | None = None, classes: bool = False, profile: str = "vanilla") -> dict:
    """Profile of a class or of a model's peer set.

    Args:
        target: class (car.sedan, sedan, bike, prop@1-2m, building, ped, weapon, ...) or a model (model:426, premier).
        like: a model whose peer set to use (same as giving the model as target).
        tier: vanilla or sa_plus (default for new assets); changes only texture density references.
        metrics: metric names or globs (default: the family's profile list; '*' = all, 'part.tris[*]').
        classes: list the classes and peer sets with their model counts instead.
        profile: profile whose style cache is used.
    """
    from . import cache, classes as C
    from .profile import describe

    c = cache.load(profile)
    if classes:
        rows = []
        for key, p in sorted(c.peers.items()):
            base = C.split_peer(key)[0]
            rows.append([key, p["n"], C.family(base), C.taxonomy()["classes"][base]["what"] if "@" not in key else ""])
        return table(["peer_set", "n", "family", "what"], rows)
    ident = like or target
    if not ident:
        raise SatkError("BAD_PARAMS", "give a class or a model", hint="satk style profile car.sedan  |  --like model:426")
    d = describe(ident, tier, metrics=metrics, profile_name=profile)
    env = table(d["cols"], [[_cell(x) for x in r] for r in d["rows"]])
    tg = d["target"]
    env["peer_set"] = f"{tg['peer_set']} ({d['n']} vanilla models)"
    if tg.get("like"):
        env["like"] = f"{tg['like']['sid']} {tg['like']['name']}"
    if tg.get("fallback"):
        env["fallback"] = tg["fallback"]
    env["tier"] = f"{d['tier']} ({d['tier_status']})"
    env["percentiles"] = d["percentiles"]
    if d.get("exemplars"):
        env["exemplars"] = d["exemplars"]
    if d.get("anchors"):
        env["anchors"] = d["anchors"]
    if d.get("tier_note"):
        env["tier_note"] = d["tier_note"]
    return env


# ----------------------------------------------------------------------------- style.card
@op("style.card", summary="Generated style card of a class: a Markdown table of vanilla reference numbers (metric, "
                          "unit, p10/p50/p90, definition) for guides, or with --lint-preset a lint config (shading "
                          "hints from the profiles; no triangle budgets) for 'satk asset lint --config'.",
    summary_ru="Карточка стиля класса: таблица Markdown (метрика, единица, p10/p50/p90, полоса, определение) или "
               "с --lint-preset конфиг линтера (бюджеты и пороги сглаживания из профилей).",
    mcp=False, group="asset",
    examples=("satk style card car.sedan --tier vanilla --md", "satk style card --lint-preset --tier sa_plus",
              "satk style card prop@1-2m --md"))
def style_card(target: str | None, tier: Tier | None = None, md: bool = True, lint_preset: bool = False,
               out: str | None = None, profile: str = "vanilla") -> dict:
    """Style card or lint preset.

    Args:
        target: class or peer set (car.sedan, bike, prop@1-2m, ped) or a model; not needed with --lint-preset.
        tier: vanilla or sa_plus (default sa_plus).
        md: Markdown card (default).
        lint_preset: write a lint config (JSON) for every lint class instead of a card.
        out: write the result to this file under the work folder (default: print it).
        profile: profile whose style cache is used.
    """
    from . import card as CD

    if lint_preset:
        data = CD.lint_preset(tier, profile_name=profile)
        text = json.dumps(data, indent=1) + "\n"
    else:
        if not target:
            raise SatkError("BAD_PARAMS", "give a class (or --lint-preset)", hint="satk style card car.sedan --md")
        text = CD.card_md(target, tier, profile_name=profile)
    if out:
        p = Path(out)
        if not p.is_absolute():
            p = Path(os.path.abspath(paths.cfg().paths.work)) / "out" / "style" / p
        paths.atomic_write(p, text)
        return {"ok": True, "file": paths.jpath(p), "bytes": len(text.encode("utf-8"))}
    if lint_preset:
        return {"ok": True, **data}
    return _md("style.card", text)


# ----------------------------------------------------------------------------- asset.check
@op("asset.check", summary="Check a model against vanilla SA: engine (hard rules), form (floating, loose body, corners, "
                           "arches, wasted density), mesh (cracks, z-fighting, flipped, stray, UV, collision), fit, "
                           "symmetry, coverage, texture look, reference. --strict: done true/false + the blocking list.",
    summary_ru="Проверка модели против ванили SA по разделам: engine (жёсткие правила движка), form (висящие детали, "
               "рыхлый кузов, жёсткие углы, сквозные арки), fit (арки, дамми, петли, руль и седок байка), symmetry, "
               "coverage (чек-лист деталей), reference (числа ванили, справочно).",
    mcp=False, group="asset",
    examples=("satk asset check mymod/premier.dff", "satk asset check mymod --like model:426 --tier sa_plus",
              "satk asset check model:426 --full", "satk asset check barrel.dff --cls prop --md",
              "satk asset check work/out/kit/mycar/modloader/mycar --strict"))
def asset_check(target: str, like: str | None = None, cls: str = "auto", tier: Tier | None = None,
                md: bool = False, full: bool = False, limit: int = 50, profile: str = "vanilla",
                strict: bool = False, project: str | None = None, session: str | None = None) -> dict:
    """Engine rules, composition, mesh defects, fit, symmetry and detail coverage of one model (or every .dff of a
    folder).

    Verdict: an engine error -> fail; a form/fit/mesh defect (beyond the vanilla range of the class) -> review; else
    pass (warnings are real flaws at the place given, listed). Reference numbers are information only; the texture
    section (the look of the own textures: flat/CG-clean, too sharp) is advice. ``--strict``
    adds ``done`` (true only when nothing blocks) and ``blocking``: engine errors and warnings, every form/fit/mesh
    defect and warning, required checklist items missing, inventory items not built (``design/inventory.json``)
    and light-leak gaps (``<package>/checks/<stem>.leak.json`` of a full recorded run, model and LOD); warnings
    within the vanilla range are ``advice``; an agent is finished only when ``done`` is true.

    Args:
        target: .dff file, mod folder (every .dff in it) or SID (model:426, premier).
        like: vanilla model to compare with (its peer set, frames, parts and rider pose); default: the file name when
            it is a vanilla model name, else asset.json, else a guess from frames and size.
        cls: style class (car.sedan, bike, prop, building@16-32m, ped, weapon, ...) or auto.
        tier: vanilla or sa_plus (default: asset.json tier, else sa_plus); changes only texture references.
        md: Markdown report instead of the table.
        full: also show info, present and reference rows (hidden by default).
        limit: rows to show (max 500).
        profile: profile whose index and style cache are used.
        strict: the definition of done: answer done true/false and the blocking list.
        project: the asset project (name or folder) whose design/inventory.json is checked (default: found from the
            .dff folder upwards).
        session: the Blender session the inventory report reads object tags from (default: the DFF only).
    """
    from . import cache, check as CK, subject

    lim = clamp_limit(limit, default=50)
    c = cache.load(profile)
    subs = subject.load(target, profile)
    results = [CK.check_subject(s, c, cls=None if cls == "auto" else cls, like=like, tier=tier, all_rows=full,
                                profile=profile) for s in subs]
    hide = () if full else CK.HIDDEN
    rows = []
    for r in results:
        name = Path(r["target"]).stem if len(results) > 1 else ""
        for row in r["rows"]:
            if row[6] in hide:
                continue
            row = [_cell(x) for x in row]
            if name:
                row[1] = f"{name}/{row[1]}" if row[1] else name
            rows.append(row)
    sd = None
    if strict:
        sts = [CK.strict_status(s_, r, project=project, session=session) for s_, r in zip(subs, results)]
        blocking = []
        for r, st in zip(results, sts):
            name = Path(r["target"]).stem if len(results) > 1 else ""
            blocking += [f"{name}: {b}" if name else b for b in st["blocking"]]
        sd = {"done": all(st["done"] for st in sts), "blocking": blocking}
        adv = []
        for r, st in zip(results, sts):
            name = Path(r["target"]).stem if len(results) > 1 else ""
            adv += [f"{name}: {b}" if name else b for b in st.get("advice") or []]
        if adv:
            sd["advice"] = adv[:30]
        if len(sts) == 1:
            for k in ("inventory", "leak", "lod_of"):
                if sts[0].get(k):
                    sd[k] = sts[0][k]
    if md:
        return _md("asset.check", _check_md(results, rows, sd), **({"done": sd["done"]} if sd else {}))
    env = table(CK.COLS, rows[:lim], total=len(rows), warn=[n for r in results for n in r["notes"]][:10])
    if sd is not None:
        env["done"] = sd["done"]
        env["blocking"] = sd["blocking"][:60]
        if len(sd["blocking"]) > 60:
            env["blocking_total"] = len(sd["blocking"])
        for k in ("inventory", "leak", "lod_of", "advice"):
            if sd.get(k):
                env[k] = sd[k]
    if len(results) == 1:
        r = results[0]
        env.update(target=r["target"], cls=r["class"], peer_set=r["peer_set"], how=r["how"], tier=r["tier"],
                   verdict=r["verdict"], counts=r["counts"], sections=r["sections"], defects=r["defects"])
        if r.get("form"):
            env["form"] = r["form"]
        if r.get("like"):
            env["like"] = r["like"]
    else:
        env["models"] = [[Path(r["target"]).name, r["peer_set"], r["verdict"],
                          ",".join(f"{k} {v}" for k, v in sorted(r["counts"].items()) if k not in CK.HIDDEN)]
                         for r in results]
        env["verdict"] = "fail" if any(r["verdict"] == "fail" for r in results) else \
            ("review" if any(r["verdict"] == "review" for r in results) else "pass")
    return env


def _check_md(results: list[dict], rows: list[list], strict: dict | None = None) -> str:
    from . import check as CK

    L = ["# asset.check", ""]
    if strict is not None:
        L.append(f"- done: {str(strict['done']).lower()}" + ("" if strict["done"] else
                                                               f" ({len(strict['blocking'])} blocking)"))
        L += [f"  - {b}" for b in strict["blocking"][:40]]
    for r in results:
        cnt = ", ".join(f"{k} {v}" for k, v in sorted(r["counts"].items()) if k not in CK.HIDDEN)
        L.append(f"- `{r['target']}`: {r['verdict']} - peer set `{r['peer_set']}` ({r['how']}), tier {r['tier']}"
                 + (f"; {cnt}" if cnt else ""))
    for sec in CK.SECTIONS:
        srows = [row for row in rows if row[9] == sec]
        if not srows:
            continue
        L += ["", f"## {sec}", "", "| check | part | value | p10 | p50 | p90 | verdict | hint | ref |",
              "|---|---|---|---|---|---|---|---|---|"]
        for row in srows:
            L.append("| " + " | ".join("" if x is None else str(x).replace("|", "/") for x in row[:9]) + " |")
    if not rows:
        L += ["", "(no findings)"]
    L += ["", "Verdicts: error fails; defect (form, mesh, fit: beyond the vanilla range of the class) asks for a "
              "review; warn (a real flaw at the place given) and missing (a checklist detail not found) are worth a "
              "fix and block --strict; info (with the vanilla reference numbers, never targets) and present show "
              "with --full; texture warn rows are advice on the look of the own textures (flat/CG-clean, too sharp: "
              "never blocking). "
              "Rules: satk help style_construction / style_vehicle / style_shading / style_texture."]
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------------------- asset.anatomy
@op("asset.anatomy", summary="Compact anatomy of one model: frame tree with model-space positions, parts (triangles, "
                             "materials, UV sets, normals/prelight flags), materials by role (paint keys, lamp keys, "
                             "glass) with env/reflection/specular values, COL summary, TXD rows. SID or .dff path.",
    summary_ru="Анатомия модели: дерево фреймов с позициями, части (треугольники, материалы, UV, флаги), материалы "
               "по ролям с env/reflection/specular, сводка COL, строки TXD. SID или путь к .dff.",
    mcp=False, group="asset",
    examples=("satk asset anatomy model:426 --md", "satk asset anatomy mymod/premier.dff"))
def asset_anatomy(target: str, md: bool = False, profile: str = "vanilla") -> dict:
    """Anatomy of one model.

    Args:
        target: SID (model:426, premier) or a .dff path (its .txd, .col and .ide next to it are read too).
        md: Markdown (compact; a 51-frame car stays under 4 KB).
        profile: profile whose index resolves SIDs.
    """
    from . import anatomy as A, subject

    subs = subject.load(target, profile)
    a = A.anatomy(subs[0])
    if md:
        return _md("asset.anatomy", A.to_markdown(a))
    return {"ok": True, **a}


# ----------------------------------------------------------------------------- style.texture
@op("style.texture", summary="Does a texture look like vanilla SA for its role (interior, wheel, decal, body, ped, "
                             "weapon, wall, ground, prop)? Colours, saturation, value and detail against the vanilla "
                             "distribution, plus look advice: flat/CG-clean (no tonal variation) or too sharp. PNG, "
                             "TXD, folder or tex: SID.",
    summary_ru="Похожа ли текстура на ваниль SA для своей роли? Число цветов, насыщенность, яркость и мелкие детали "
               "против распределения ванили: ловит векторные текстуры и светлые салоны.",
    mcp=False, group="texture",
    examples=("satk style texture mymod/premier.txd", "satk style texture interior.png --role interior",
              "satk style texture tex:premier/premier92interior128", "satk style texture mybin.txd --cls prop --full"))
def style_texture(target: str, role: str = "auto", full: bool = False, limit: int = 50,
                  profile: str = "vanilla", cls: str | None = None) -> dict:
    """Judge textures against the vanilla distribution of their role, and give look advice (flat/CG-clean, too
    sharp) against the same role.

    Args:
        target: .png, .txd, folder of them, tex:<txd>/<name> or txd:<name>.
        role: auto (from the texture name) or interior|wheel|decal|body|ped|weapon|wall|ground|prop|generic.
        full: one row per band metric instead of one row per texture; the look metrics come in look_rows.
        limit: rows to show (max 500).
        profile: profile of the vanilla distributions.
        cls: style class of the model that uses the textures (prop, building, car, ...): with role auto the name
            picks a role of that family, else the class default (a prop's ls_bin_paint is a prop texture).
    """
    from . import texture as T

    rr = T.roles()["roles"]
    if role != "auto" and role not in rr:
        raise SatkError("BAD_PARAMS", f"unknown texture role {role!r}", hint="roles: auto, " + ", ".join(rr))
    lim = clamp_limit(limit, default=50)
    dist = T.vanilla(profile)
    rows = []
    n_out = 0
    advice: list[str] = []
    look_rows: list[list] = []
    n_look = 0
    for name, w, h, rgba in T.load_images(target):
        st = T.texture_stats(rgba, w, h)
        j = T.judge_texture(name, st, dist, (T.role_for(name, cls) if cls else T.role_of(name)) if role == "auto"
                            else role)
        n_out += j["verdict"] == "out"
        n_look += bool(j["look"])
        advice += [f"{name}: {a['look']}: {a['why']}: {a['fix']}" for a in j["look"]]
        if full:
            for m, v, p10, p50, p90, verdict in j["rows"]:
                rows.append([name, j["used_role"], m, _cell(v), p10, p50, p90, verdict])
            for m, v, p10, p50, p90, verdict in j["look_rows"]:
                look_rows.append([name, j["used_role"], m, _cell(v), p10, p50, p90, verdict])
        else:
            bad = [f"{m} {_cell(v)} ({verdict}; vanilla {p10}..{p90})" for m, v, p10, _p50, p90, verdict in j["rows"]
                   if verdict != "ok"]
            rows.append([name, j["used_role"], j["size"], j["verdict"], "; ".join(bad),
                         ", ".join(a["look"] for a in j["look"])])
    cols = ["texture", "role", "metric", "value", "p10", "p50", "p90", "verdict"] if full else \
        ["texture", "role", "size", "verdict", "outside the vanilla band", "look"]
    env = table(cols, rows[:lim], total=len(rows))
    env["out"] = n_out
    env["look"] = n_look
    if look_rows:                       # the look metrics apart from the band rows (advice, never a band verdict)
        env["look_rows"] = {"cols": ["texture", "role", "metric", "value", "p10", "p50", "p90", "verdict"],
                            "rows": look_rows[:lim]}
    if advice:
        env["advice"] = advice[:20]
    env["rule"] = ("out = a metric beyond its fence (colour counts in log space), or every metric outside "
                   "p10..p90; look (advice) = flat/CG-clean (fine tonal variation under the role's p5 or dead-flat "
                   "patches over its p95) or too sharp (crisp marks over its p95); vanilla per role: "
                   + ", ".join(f"{k} {v['n']}" for k, v in dist["roles"].items()))
    return env


# ----------------------------------------------------------------------------- style.brief_check
@op("style.brief_check", summary="Check a task brief for wording that leads to known mistakes of agent-made SA "
                                 "assets: box words (boxy, slab, squared-off, flat sides, voxel), 'style = numbers' "
                                 "phrases, triangle numbers and budgets, low-poly, pixel-measured photos, painted grime, "
                                 "'crisp', 'clean shapes', 'flat colour'.",
    summary_ru="Проверка задания (брифа) на формулировки, ведущие к известным ошибкам: слова-коробки (boxy, slab, "
               "squared-off), 'стиль = числа', числа треугольников и бюджеты, low-poly, измерение фото по пикселям.",
    mcp=False, group="asset",
    examples=("satk style brief-check BRIEF.md",))
def style_brief_check(brief: str, cls: str = "auto", profile: str = "vanilla") -> dict:
    """Flag wording in a brief (Markdown or text) that leads to known mistakes; every row has the fix.

    Args:
        brief: the brief file (.md or .txt).
        cls: kept for older callers; the check no longer depends on the class.
        profile: kept for older callers.
    """
    from . import brief as B

    p = Path(brief)
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"no file {brief!r}", hint="satk style brief-check <brief.md>")
    text = p.read_text(encoding="utf-8", errors="replace")
    rows = B.check_text(text, label=p.name)
    env = table(["term", "where", "excerpt", "why", "fix", "ref"], rows)
    env["flags"] = len(rows)
    return env
