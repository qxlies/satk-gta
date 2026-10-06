"""Operations of ``satk.shader`` (all CLI only, ``mcp=False``; MCP reaches them through ``satk_op``):

* ``shader.textures`` -> ``satk shader textures [PATTERN...] [--kind K] [--model SID] [--area x y r]``: world
  texture names for ``engineApplyShaderToWorldTexture`` from the index, with counts and a short exact pattern list;
* ``shader.new``      -> ``satk shader new <template> --name X``: a ready MTA resource (``meta.xml``, client Lua glue,
  ``.fx``) in ``work/out/shader/<name>/``, compiled with fxc when available;
* ``shader.check``    -> ``satk shader check <file.fx|resource folder>``: fxc compile plus MTA-specific checks and a
  cross-check of the resource's client Lua.

Module-level imports stay stdlib/satk only (fast imports).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from ..core.envelope import clamp_limit, obj, table, with_warn
from ..core.errors import SatkError
from ..core.registry import op

Kind = Literal["road", "pavement", "grass", "dirt", "sand", "rock", "glass", "water", "tree", "lod", "vehicle",
               "vehicle_paint", "ped"]
Template = Literal["car_paint", "texture_replace", "wet_roads", "water", "sky_fog", "ped_shading", "post_process"]
PLAN_COLS = ["op", "pattern", "covers", "extra", "sample"]


def _db(profile: str):
    from ..index.api import open_index

    return open_index(profile)


def _selection(profile: str, patterns, kind, model, area, exclude):
    from . import names as N

    db = _db(profile)
    w = N.load_world(db)
    sel = N.select(db, w, patterns=patterns, kind=kind, model=model, area=area, exclude=exclude)
    return db, w, sel


def _plan_dict(plan, limit: int) -> tuple[dict, list[list]]:
    d = {"exact": plan.exact, "patterns": len(plan.picks), "apply": plan.apply, "remove": plan.remove}
    if plan.extra:
        d["extra"] = plan.extra
    return d, [p.row() for p in plan.picks[:limit]]


def _hint(kind: str | None) -> str | None:
    if not kind:
        return None
    from .names import kinds

    et = kinds().get(kind, {}).get("element_types")
    if et:
        return (f"kind {kind}: a shader created with dxCreateShader(file, 0, 0, false, \"{et}\") and applied to \"*\" "
                f"reaches every {et} texture with one pattern")
    return None


@op("shader.textures", mcp=False, group="texture",
    summary="World texture names for MTA engineApplyShaderToWorldTexture from the index: by glob, --kind (road, glass, "
            "water, tree, vehicle, ped ...), --model or --area; counts, TXDs, models, spill, and a short exact "
            "apply/remove pattern list with unintended hits per pattern.",
    summary_ru="Имена текстур мира для engineApplyShaderToWorldTexture из индекса: по маске, --kind, --model, --area; "
               "счётчики, TXD, модели и короткий точный список масок apply/remove.",
    examples=("satk shader textures *road*", "satk shader textures --kind road",
              "satk shader textures --model model:411", "satk shader textures --kind road --area 2495 -1666 150",
              "satk shader textures --kind glass --exclude *lod* --max-extra 2"))
def shader_textures(patterns: list[str] | None, kind: Kind | None = None, model: str | None = None,
                    area: list[float] | None = None, exclude: list[str] | None = None, max_extra: int = 0,
                    profile: str = "vanilla", limit: int = 20, cursor: str | None = None) -> dict:
    """List world texture names and a pattern plan.

    Args:
        patterns: MTA texture name patterns (* and ?, any case), e.g. *road* tar_*; several are OR-ed.
        kind: a texture kind from data/shader/kinds.json (heuristic: name rules, collision surfaces, IDE flags).
        model: model:<id|name>, a model name or id, or txd:<name>: the textures that model or TXD uses.
        area: x y r: textures of the models placed within r of (x, y).
        exclude: patterns removed from the selection.
        max_extra: 0 = exact plan (apply + remove patterns); N = apply patterns only, each may hit N unwanted names.
        profile: index profile (vanilla = what MTA players have).
        limit: rows per page (max 500); also the rows of the plan table.
        cursor: value of `next` from the previous page.
    """
    from . import names as N
    from .wild import cover, match_names

    if max_extra < 0:
        raise SatkError("BAD_PARAMS", f"--max-extra must be >= 0, got {max_extra}", hint="--max-extra 2")
    lim = clamp_limit(limit)
    _db_, w, sel = _selection(profile, patterns, kind, model, area, exclude)
    cols, rows, spill = N.rows(w, sel)
    start = int(cursor) if cursor and cursor.isdigit() else 0
    page = rows[start:start + lim]
    nxt = str(start + lim) if start + lim < len(rows) else None
    env = table(cols, page, total=len(rows), next=nxt, warn=sel.warn)
    plan_d: dict = {}
    if sel.names:
        only_patterns = patterns and not (kind or model or area or exclude)
        if only_patterns:
            # the user's patterns already are the plan; show what each one hits
            plan_rows = [["apply", p, len(match_names(p, w.names)), 0, ""] for p in patterns]
            plan_d = {"exact": True, "patterns": len(patterns), "apply": list(patterns), "remove": []}
        else:
            plan = cover(sel.names, w.names, max_extra=max_extra)
            plan_d, plan_rows = _plan_dict(plan, lim)
        env["plan"] = plan_d
        env["plan_cols"] = PLAN_COLS
        env["plan_rows"] = plan_rows
    env["select"] = sel.how + [f"profile {w.profile}: {len(w.names)} texture names"]
    if spill:
        env["spill"] = spill
    h = _hint(kind)
    if h:
        env["hint"] = h
    if not sel.names:
        with_warn(env, "EMPTY: the selectors match no texture name")
    return env


# ============================================================================ new


def _new_selection(t: dict, template: str, textures, kind, model, area, exclude, profile: str,
                   warn: list[str]) -> tuple[list[str], list[str], str, int | None, bool]:
    """``(apply, remove, description, names, exact)`` for the Lua glue."""
    from . import names as N
    from .wild import cover, match_names

    default = t.get("select")
    given = bool(textures or kind or model or area)
    if default == "screen":
        if given or exclude:
            warn.append(f"UNUSED: template {template} draws the whole screen; texture selectors are ignored")
        return [], [], "the screen (post-process)", None, True
    if not given:
        if default is None:
            raise SatkError("BAD_PARAMS", f"template {template} needs the world textures to change",
                            hint=f"satk shader new {template} --textures *road* | --kind road | --model model:3458")
        if "apply" in default:
            apply = list(default["apply"])
            desc = f"{', '.join(apply)} (element types {t['element_types']})"
            if exclude:
                desc += f" minus {', '.join(exclude)}"
            return apply, list(exclude or []), desc, None, True
        if "names" in default:
            textures = list(default["names"])
        else:
            kind = default.get("kind")
    if textures and not (kind or model or area or exclude):
        try:
            db = _db(profile)
            w = N.load_world(db)
            for p in textures:
                if not match_names(p, w.names):
                    warn.append(f"NO_MATCH: pattern {p!r} matches no texture name of profile {profile}")
        except SatkError as e:
            if e.code != "INDEX_MISSING":
                raise
        return list(textures), [], "patterns " + ", ".join(textures), None, True
    try:
        _db_, w, sel = _selection(profile, textures, kind, model, area, exclude)
    except SatkError as e:
        if e.code != "INDEX_MISSING" or not kind or model or area:
            raise
        fb = N.kinds()[kind].get("fallback") or {"apply": N.kinds()[kind].get("names") or ["*"],
                                                 "remove": N.kinds()[kind].get("exclude") or []}
        warn.append(f"INDEX_MISSING: no index for profile {profile}; kind {kind} uses its fixed fallback patterns "
                    f"(satk index build --profile {profile} for the exact list)")
        return list(fb["apply"]), list(fb.get("remove", [])), f"kind {kind} (fallback patterns, no index)", None, False
    warn += sel.warn
    if not sel.names:
        raise SatkError("NOT_FOUND", "the selectors match no texture name", hint="satk shader textures ... to try them")
    et = t.get("element_types", "")
    scope = et if et in ("vehicle", "ped") else None
    # A shader limited to vehicles (or peds) never touches other textures: plan against that scope only.
    plan = cover(sel.names, sorted(N.scope_names(w, scope)) if scope else w.names)
    desc = (f"{len(sel.names)} names of profile {profile}, {'exact' if plan.exact else 'approximate'} pattern list"
            f"{f' for {et} textures' if scope else ''}; review: "
            f"{_textures_cmd(textures, kind, model, area, exclude, profile)}")
    return plan.apply, plan.remove, desc, len(sel.names), plan.exact


def _textures_cmd(textures, kind, model, area, exclude, profile: str) -> str:
    parts = ["satk shader textures"]
    parts += list(textures or [])
    if kind:
        parts.append(f"--kind {kind}")
    if model:
        parts.append(f"--model {model}")
    if area:
        parts.append("--area " + " ".join(f"{float(v):g}" for v in area))
    if exclude:
        parts.append("--exclude " + " ".join(exclude))
    if profile != "vanilla":
        parts.append(f"--profile {profile}")
    return " ".join(parts)


@op("shader.new", mcp=False, group="texture",
    summary="Create an MTA shader resource from a template (car_paint, texture_replace, wet_roads, water, sky_fog, "
            "ped_shading, post_process): meta.xml, client Lua glue with the selected world-texture patterns and the "
            ".fx, in work/out/shader/<name>/, compiled with fxc when found.",
    summary_ru="Создать MTA-ресурс шейдера по шаблону (car_paint, texture_replace, wet_roads, water, sky_fog, "
               "ped_shading, post_process): meta.xml, Lua-обвязка с масками текстур и .fx в work/out/shader/<имя>/.",
    examples=("satk shader new wet_roads --name wetroads", "satk shader new car_paint --name carpaint",
              "satk shader new texture_replace --name grove --textures sf_road5 --image my.png",
              "satk shader new water --name nicewater", "satk shader new post_process --name grading"))
def shader_new(template: Template, name: str | None = None, textures: list[str] | None = None,
               kind: Kind | None = None, model: str | None = None, area: list[float] | None = None,
               exclude: list[str] | None = None, image: str | None = None, profile: str = "vanilla",
               fxc: str | None = None, check: bool = True) -> dict:
    """Write a shader resource.

    Args:
        template: car_paint (vehicle paint reflection), texture_replace (one image instead of the selected textures),
            wet_roads (rain-dependent road shine), water (animated water), sky_fog (world tint, haze and sky
            gradient), ped_shading (colour tweak of peds), post_process (screen colour grading and vignette).
        name: resource and folder name (letters, digits, _ -); default: the template name.
        textures: world texture patterns to use instead of the template's default selection.
        kind: texture kind instead of the default (see satk shader textures --kind).
        model: model:<id|name> or txd:<name>: the textures of that model or TXD.
        area: x y r: the textures of the models placed there.
        exclude: patterns to leave out.
        image: PNG for texture_replace (default: a checker placeholder).
        profile: index profile for the selection.
        fxc: path of fxc.exe, or none to skip compiling (default: found automatically).
        check: compile and check the written resource.
    """
    from ..core.paths import atomic_write, jpath, work
    from .templates import check_name, checker_png, render, templates

    warn: list[str] = []
    nm = check_name(name or template)
    t = templates()[template]
    img_name = None
    img_bytes = None
    if t.get("image"):
        if image:
            p = Path(image)
            if not p.is_file():
                raise SatkError("NOT_FOUND", f"--image {image!r}: no such file", hint="a PNG, DDS or JPEG file")
            img_bytes = p.read_bytes()
            ext = p.suffix.lower()
            if ext not in (".png", ".dds", ".jpg", ".jpeg"):
                raise SatkError("BAD_PARAMS", f"--image {p.name}: MTA dxCreateTexture reads png, dds and jpg",
                                hint="convert it to PNG")
            img_name = "texture" + (".jpg" if ext == ".jpeg" else ext)
        else:
            img_name = "texture.png"
            img_bytes = checker_png()
            warn.append("PLACEHOLDER: no --image: texture.png is a checker placeholder; replace it")
    elif image:
        warn.append("UNUSED: --image is only used by texture_replace")
    apply, remove, desc, n_names, exact = _new_selection(t, template, textures, kind, model, area, exclude, profile,
                                                         warn)
    files = render(template, nm, apply=apply, remove=remove, selection=desc, image=img_name)
    out = work("out", "shader", nm)
    written: list[str] = []
    for fname, text in files.items():
        atomic_write(out / fname, text)
        written.append(fname)
    if img_name and img_bytes is not None:
        atomic_write(out / img_name, img_bytes)
        written.append(img_name)
    stale = sorted(p.name for p in out.iterdir() if p.name not in written and not p.name.startswith("."))
    if stale:
        warn.append(f"STALE: files of an earlier run stay in the folder (nothing is deleted): {', '.join(stale[:5])}")
    res = obj(None, dir=jpath(out), template=template, files=written,
              textures={"apply": len(apply), "remove": len(remove), "names": n_names, "exact": exact,
                        "select": desc},
              install=f"copy the folder {nm} into <MTA server>/mods/deathmatch/resources/ and run 'start {nm}' in "
                      f"the server console; in game /{nm.lower()} toggles it (satk writes nothing outside work)")
    if check:
        res["check"] = _check_summary(out / t["fx"], fxc, profile, warn)
    return with_warn(res, *warn)


def _check_summary(fx: Path, fxc: str | None, profile: str, warn: list[str]) -> dict:
    r = _check_one(fx, fxc=fxc, lua=None, define=None, profile=profile)
    s = {"status": r["status"], "compiler": r["compiler"]["tool"], "errors": r["errors"], "warnings": r["warnings"]}
    if r["compiler"].get("note"):
        s["note"] = r["compiler"]["note"]
    for i in r["issues"]:
        if i.sev in ("error", "warn"):
            warn.append(f"{i.code}: {i.file}:{i.line}: {i.msg}")
    return s


# ============================================================================ check


def _check_one(fx: Path, *, fxc: str | None, lua: list[str] | None, define: list[str] | None, profile: str) -> dict:
    from . import check as C
    from .fx import load

    eff = load(fx)
    issues = list(eff.issues)
    hit = C.find_fxc(fxc)
    compiler: dict = {}
    compiled = None
    if hit.path is not None:
        r = C.compile_fx(eff, hit.path, defines=define)
        labels = {}
        for f in eff.files:
            try:
                lab = f.relative_to(eff.root).as_posix()
            except ValueError:
                lab = f.name
            labels[lab.lower()] = lab
        for i in r["issues"]:
            i.file = labels.get(i.file.replace("\\", "/").lower(), i.file)
        issues += r["issues"]
        compiled = r["ok"]
        compiler = {"tool": "fxc", "path": str(hit.path).replace("\\", "/"), "sdk": C.sdk_label(hit.path),
                    "profile": "fx_2_0", "seconds": r["seconds"],
                    "note": "fxc uses D3DCompiler_47; MTA compiles with D3DX9 at run time (rare differences)"}
    else:
        compiler = {"tool": "none", "tried": hit.tried,
                    "note": ("compiling skipped (--fxc none)" if hit.source == "disabled" else
                             "fxc.exe not found (install the Windows 10/11 SDK or pass --fxc): only the static syntax "
                             "and MTA checks ran")}
    mta_issues, params = C.mta_checks(eff)
    issues += mta_issues
    files = [Path(p) for p in lua] if lua else C.client_scripts(eff.root, eff.path)
    luainfo: dict = {}
    if files:
        refs = C.lua_refs(files)
        universe = None
        try:
            from .names import load_world

            universe = load_world(_db(profile)).names
        except SatkError:
            pass
        li, luainfo = C.lua_checks(eff, refs, universe=universe)
        issues += li
        luainfo["files"] = [f.name for f in files]
    order = {"error": 0, "warn": 1, "info": 2}
    issues.sort(key=lambda i: (order.get(i.sev, 3), i.file, i.line))
    errs = sum(1 for i in issues if i.sev == "error")
    warns = sum(1 for i in issues if i.sev == "warn")
    status = "fail" if errs or compiled is False else ("warn" if warns else "ok")
    techs = [f"{t.name}: " + ", ".join(
        "/".join(x for x in ((ps.vs or ("", ""))[0], (ps.ps or ("", ""))[0]) if x) or "fixed-function"
        for ps in t.passes) for t in eff.techniques]
    return {"file": eff.path, "status": status, "compiled": compiled, "compiler": compiler, "issues": issues,
            "errors": errs, "warnings": warns, "techniques": techs, "params": params, "lua": luainfo,
            "includes": [i.text for i in eff.includes]}


@op("shader.check", mcp=False, group="texture",
    summary="Check an MTA shader (.fx or a resource folder): compile with fxc (fx_2_0) when found, else a syntax "
            "check; MTA rules (techniques, D3D9 profiles, state annotations, semantics, includes) and the client Lua "
            "(dxSetShaderValue names, textures never set, world-texture patterns).",
    summary_ru="Проверить MTA-шейдер (.fx или папку ресурса): компиляция fxc (fx_2_0) при наличии, иначе синтаксис; "
               "правила MTA и проверка клиентского Lua (имена dxSetShaderValue, маски текстур).",
    examples=("satk shader check myshader.fx", "satk shader check mods/deathmatch/resources/wetroads",
              "satk shader check water.fx --define QUALITY=2 --fxc none"))
def shader_check(target: str, lua: list[str] | None = None, define: list[str] | None = None,
                 fxc: str | None = None, strict: bool = False, profile: str = "vanilla", limit: int = 50) -> dict:
    """Check effect files.

    Args:
        target: a .fx file, a folder (every .fx inside, e.g. an MTA resource) or the name of a resource written by
            satk shader new (work/out/shader/<name>).
        lua: client Lua files to cross-check (default: client scripts of meta.xml, else *.lua next to the .fx).
        define: NAME=VALUE macros as dxCreateShader passes them (MTA itself defines IS_DEPTHBUFFER_RAWZ).
        fxc: path of fxc.exe, or none to skip compiling (default: found automatically).
        strict: fail with CHECK_FAILED when there is an error.
        profile: index profile for the world-texture pattern check.
        limit: table rows (errors first; max 500).
    """
    from ..core.paths import jpath

    from ..core.paths import cfg

    p = Path(target)
    if not p.exists() and re.match(r"^[A-Za-z0-9_-]{1,40}$", target):
        made = Path(cfg().paths.work) / "out" / "shader" / target   # a resource written by satk shader new
        if made.is_dir():
            p = made
    if not p.exists():
        raise SatkError("NOT_FOUND", f"{target!r}: no such file or folder",
                        hint="satk shader check <file.fx|folder|name of a satk shader new resource>")
    fxs = sorted(p.rglob("*.fx")) if p.is_dir() else [p]
    if not fxs:
        raise SatkError("NOT_FOUND", f"no .fx file in {jpath(p)}", hint="satk shader check <file.fx>")
    if len(fxs) > 200:
        raise SatkError("BAD_PARAMS", f"{len(fxs)} .fx files under {jpath(p)}: point at one resource folder")
    results = [_check_one(f, fxc=fxc, lua=lua, define=define, profile=profile) for f in fxs]
    lim = clamp_limit(limit)
    rows: list[list] = []
    for r in results:
        name = r["file"].name
        for i in r["issues"]:
            row = i.row()
            if len(results) > 1 and not row[2].startswith(name):
                row[2] = f"{name} > {row[2]}"
            rows.append(row)
    rows.sort(key=lambda r: {"error": 0, "warn": 1, "info": 2}.get(r[0], 3))
    env = table(["sev", "code", "where", "msg"], rows[:lim], total=len(rows))
    errs = sum(r["errors"] for r in results)
    status = "fail" if any(r["status"] == "fail" for r in results) else (
        "warn" if any(r["status"] == "warn" for r in results) else "ok")
    env["status"] = status
    env["compiler"] = results[0]["compiler"]
    if len(results) == 1:
        r = results[0]
        extra = obj(None, file=jpath(r["file"]), compiled=r["compiled"], techniques=r["techniques"],
                    params=r["params"], lua=r["lua"], includes=r["includes"])
        env.update({k: v for k, v in extra.items() if k != "ok"})
    else:
        env["files"] = [[jpath(r["file"]), r["status"], r["compiled"], r["errors"], r["warnings"]] for r in results]
        env["files_cols"] = ["file", "status", "compiled", "errors", "warnings"]
    if strict and (errs or status == "fail"):
        raise SatkError("CHECK_FAILED", f"{errs} error(s) in {len(results)} effect file(s)",
                        hint="the rows say what to fix", data={k: v for k, v in env.items() if k != "ok"})
    return env
