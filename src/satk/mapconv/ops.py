"""Operations of ``satk.mapconv`` (M2-04, report 23 §13 P0; B4), CLI only (``mcp=False``; MCP reaches them
through the generic operation tool):

* ``map.convert``  -> ``satk map convert <src> --to mta|pawn|ipl|json [--out PATH]``;
* ``map.validate`` -> ``satk map validate <src> [--profile vanilla]``;
* ``ipl.decompile`` / ``ipl.compile`` -> ``satk ipl decompile|compile`` (binary IPL, :mod:`satk.mapconv.bnry`);
* ``map.clean``    -> ``satk map clean <area> [--to ipl lua pawn]`` (:mod:`satk.mapconv.clean`).
"""

from __future__ import annotations

import os
from typing import Literal

from ..core.envelope import clamp_limit, dumps, obj, table, with_warn
from ..core.errors import SatkError
from ..core.paths import atomic_write, jpath, work
from ..core.registry import op

Fmt = Literal["pawn", "mta", "ipl", "json"]
SrcFmt = Literal["auto", "pawn", "mta", "ipl", "json"]
ISSUE_COLS = ["sev", "code", "where", "msg"]


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    if not (cursor.startswith("o") and cursor[1:].isdigit()):
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}", hint="pass the 'next' value of the previous page")
    return int(cursor[1:])


@op("map.convert", mcp=False,
    summary="Convert a map between SA-MP Pawn (literal CreateObject/CreateDynamicObject/materials/removals), MTA .map, "
            "text IPL and satk JSON; Euler<->IPL quaternion; writes work/out/mapconv/ plus a JSON report of losses.",
    summary_ru="Конвертер маппинга: Pawn SA-MP (только литералы) ↔ MTA .map ↔ текстовый IPL ↔ JSON satk; "
               "материалы и потери — в отчёт; выход в work/out/mapconv/.",
    examples=("satk map convert my_map.pwn --to mta", "satk map convert ipl:lae2 --to mta",
              "satk map convert interior.map --to ipl --out interior.ipl"))
def map_convert(src: str, to: Fmt | None = None, out: str | None = None, src_format: SrcFmt = "auto",
                pawn_func: Literal["auto", "CreateObject", "CreateDynamicObject", "CreateDynamicObjectEx"] = "auto",
                materials: Literal["embed", "drop"] = "embed", tilt: Literal["warn", "dontstream"] = "warn",
                names: bool = True, force: bool = False, limit: int = 20, profile: str = "vanilla") -> dict:
    """Read a map in one format and write it in another.

    Args:
        src: input file (.pwn/.inc/.txt, .map, .ipl text or bnry, .json) or an ipl:<name> SID.
        to: output format; default: from the --out extension.
        out: output file; relative paths and the default go under work/out/mapconv/.
        src_format: input format (auto: by extension, then by content).
        pawn_func: Pawn creating function (auto: the source one, else CreateDynamicObject).
        materials: .map: embed <material>/<materialText> children (satk extension) or drop them.
        tilt: IPL: warn about tilts < 5.73 deg the game drops, or set dont_stream to keep them.
        names: look model names up in the index (element ids, IPL names, Pawn comments).
        force: overwrite an existing file outside work/.
        limit: how many issues to return (all are in the report file).
        profile: index profile for ipl: SIDs and model names.
    """
    from .convert import EXT, check_losses, load_source, model_names, out_path, read_scene, report_rows, \
        under_work, write_scene

    lim = clamp_limit(limit)
    if to is None:
        if not out:
            raise SatkError("BAD_PARAMS", "give --to (pawn|mta|ipl|json) or an --out file with that extension",
                            hint=f"satk map convert {src} --to mta")
        ext = "." + out.rsplit(".", 1)[-1].lower() if "." in out else ""
        inv = {v: k for k, v in EXT.items()} | {".inc": "pawn", ".txt": "pawn", ".p": "pawn"}
        to = inv.get(ext)  # type: ignore[assignment]
        if to is None:
            raise SatkError("BAD_PARAMS", f"cannot tell the output format from {out!r}",
                            hint="add --to mta|pawn|ipl|json")
    source = load_source(src, profile)
    pre_names: dict[int, str] = {}
    sc = read_scene(source, src_format, None)
    warn: list[str] = []
    if names and (sc.objects or sc.removals):
        pre_names, why = model_names([o.model for o in sc.objects] + [r.model for r in sc.removals], profile)
        if why:
            warn.append(f"NO_NAMES: model names not looked up ({why})")
        for o in sc.objects:
            if o.name is None and o.model in pre_names:
                o.name = pre_names[o.model]
    dst = out_path(out, source.stem, to)
    if source.path is not None and dst.exists() and os.path.samefile(source.path, dst):
        raise SatkError("BAD_PARAMS", "the output file is the input file", hint="give another --out")
    if dst.exists() and not force and not under_work(dst):
        raise SatkError("EXISTS", f"{jpath(dst)} exists", hint="add --force to overwrite it")
    header = f"satk map convert: {source.label} ({sc.fmt}) -> {to}"
    check_losses(sc, to, pawn_func=pawn_func, materials=materials == "embed")
    text = write_scene(sc, to, names=pre_names, pawn_func=pawn_func, materials=materials == "embed", tilt=tilt,
                       header=header)
    # IPL: the game's 8-bit text; Pawn: keep an ANSI (cp1251) source ANSI for the SA-MP compiler; else UTF-8
    enc = {"ipl": "latin-1", "pawn": "cp1251" if sc.encoding == "cp1251" else "utf-8"}.get(to, "utf-8")
    try:
        payload = text.encode(enc)
    except UnicodeEncodeError:
        payload = text.encode("utf-8")
        warn.append(f"ENCODING: text does not fit {enc}; written as UTF-8")
    atomic_write(dst, payload)
    counts = sc.counts()
    issues = [i.row() for i in sorted(sc.issues, key=lambda i: {"error": 0, "warn": 1}.get(i.sev, 2))]
    rep = {"src": source.label, "from": sc.fmt, "to": to, "file": jpath(dst), **counts,
           "skipped": dict(sorted(sc.skipped.items())), "issues": {"cols": ISSUE_COLS, "rows": issues},
           **report_rows(sc)}
    rpath = work("out", "mapconv", f"{dst.stem}.{to}.report.json")
    atomic_write(rpath, dumps(rep, pretty=True) + "\n")
    env = obj(file=jpath(dst), report=jpath(rpath), src=source.label, **{"from": sc.fmt}, to=to, **counts,
              skipped=dict(sorted(sc.skipped.items())), issues=issues[:lim],
              issues_total=len(issues) if len(issues) > lim else None)
    if counts["errors"]:
        warn.append(f"ERRORS: {counts['errors']} error issues (see issues / the report)")
    return with_warn(env, *warn)


@op("map.validate", mcp=False,
    summary="Check a map (Pawn/.map/IPL/JSON): model ids exist in the index, coordinates inside the world, "
            "duplicates, removals that hit nothing or leave LODs, material slots/textures. Table sev/code/where/msg.",
    summary_ru="Проверка карты: ID моделей есть в индексе, координаты в мире, дубли, удаления без цели и без LOD, "
               "слоты и текстуры материалов; таблица sev/code/where/msg.",
    examples=("satk map validate my_map.pwn", "satk map validate ipl:lae2", "satk map validate samp.pwn --profile samp"))
def map_validate(src: str, profile: str = "vanilla", src_format: SrcFmt = "auto", strict: bool = False,
                 limit: int = 20, cursor: str | None = None) -> dict:
    """Validate a map; ok=true with counts, or CHECK_FAILED with --strict when there are errors.

    Args:
        src: input file (.pwn/.inc/.txt, .map, .ipl, .json) or an ipl:<name> SID.
        profile: index profile to check model ids, textures and removals against.
        src_format: input format (auto: by extension, then by content).
        strict: fail (CHECK_FAILED) if any issue has sev=error.
        limit: rows per page.
        cursor: next page (the 'next' value of the previous answer).
    """
    from .convert import load_source, read_scene
    from .validate import validate_scene

    lim = clamp_limit(limit)
    sc = read_scene(load_source(src, profile), src_format, None)
    issues, why = validate_scene(sc, profile)
    rows = [i.row() for i in issues]
    off = _offset(cursor)
    page = rows[off:off + lim]
    nxt = f"o{off + lim}" if off + lim < len(rows) else None
    sev = [i.sev for i in issues]
    warn = [f"NO_INDEX: model/texture/removal checks skipped ({why})"] if why else []
    env = table(ISSUE_COLS, page, total=len(rows), next=nxt, warn=warn)
    env.update({"src": sc.source, "format": sc.fmt, "profile": profile, "objects": len(sc.objects),
                "removals": len(sc.removals), "materials": sc.n_materials, "errors": sev.count("error"),
                "warnings": sev.count("warn")})
    if strict and env["errors"]:
        raise SatkError("CHECK_FAILED", f"{env['errors']} errors in {sc.source}",
                        hint=f"satk map validate {src} --profile {profile}",
                        data={"cols": ISSUE_COLS, "rows": [r for r in rows if r[0] == "error"][:lim]})
    return env


# --------------------------------------------------------------------------- binary IPL and map clean (B4)


def _ipl_names(models, profile: str, names: bool, warn: list[str]) -> dict[int, str]:
    if not names:
        return {}
    from .convert import model_names

    got, why = model_names(models, profile)
    if why:
        warn.append(f"NO_NAMES: model names not looked up ({why}); written as model<id>")
    return got


def _out_file(out: str | None, sub: str, stem: str):
    """``out`` or ``work/out/mapconv/<sub>/<stem>.ipl``; relative paths go under ``work/out/mapconv/<sub>/``."""
    from pathlib import Path

    from .convert import out_path

    if out is None:
        return work("out", "mapconv", sub, f"{stem}.ipl")
    return out_path(out if Path(out).is_absolute() else str(Path(sub) / out), stem, "ipl")


def _check_dst(dst, source, force: bool) -> None:
    from .convert import under_work

    if source.path is not None and dst.exists() and os.path.samefile(source.path, dst):
        raise SatkError("BAD_PARAMS", "the output file is the input file", hint="give another --out")
    if dst.exists() and not force and not under_work(dst):
        raise SatkError("EXISTS", f"{jpath(dst)} exists", hint="add --force to overwrite it")


@op("ipl.decompile", mcp=False, group="map",
    summary="Binary IPL (bnry: ipl:lae2_stream0 or a *_streamN.ipl file) -> text IPL (inst + cars) with model names; "
            "exact: 'ipl compile' of the text gives the same bytes back (shortest float32 text, layout directive).",
    summary_ru="Бинарный IPL (bnry) → текстовый IPL (inst + cars) с именами моделей; обратная сборка "
               "'ipl compile' даёт те же байты.",
    examples=("satk ipl decompile ipl:lae2_stream0", "satk ipl decompile lae2_stream0.ipl --out lae2_stream0.txt"))
def ipl_decompile(src: str, out: str | None = None, names: bool = True, profile: str = "vanilla",
                  force: bool = False) -> dict:
    """Decompile a binary IPL into text.

    Args:
        src: a bnry file or an ipl:<name> SID of a binary IPL in the index.
        out: output file; relative paths and the default go under work/out/mapconv/decompiled/.
        names: look model names up in the index (else model<id>).
        profile: index profile for ipl: SIDs and names.
        force: overwrite an existing file outside work/.
    """
    from ..formats.ipl import stream_base
    from .bnry import compile_ipl, decompile, is_bnry, parse_bnry
    from .convert import load_source

    source = load_source(src, profile)
    if not is_bnry(source.data):
        raise SatkError("BAD_PARAMS", f"{source.label} is not a binary IPL (no 'bnry' magic)",
                        hint=f"a text IPL converts with: satk map convert {src} --to json")
    try:
        models = {r[7] for r in parse_bnry(source.data).insts}
    except ValueError as e:
        raise SatkError("BAD_PARAMS", f"{source.label}: broken binary IPL: {e}") from None
    warn: list[str] = []
    nm = _ipl_names(models, profile, names, warn)
    text, info = decompile(source.data, names=nm, label=source.label, parent=stream_base(source.stem))
    try:
        exact = compile_ipl(text)[0] == source.data
    except ValueError:
        exact = False
    if not exact:
        warn.append("NOT_EXACT: compiling the text back will not give the same bytes ("
                    + ("; ".join(info.why) or "unknown layout") + ")")
    dst = _out_file(out, "decompiled", source.stem)
    _check_dst(dst, source, force)
    atomic_write(dst, text.encode("latin-1", "replace"))
    env = obj(file=jpath(dst), src=source.label, inst=len(info.insts), cars=len(info.cars), bytes=len(source.data),
              style=info.style, pad=info.pad, exact=exact)
    return with_warn(env, *warn)


@op("ipl.compile", mcp=False, group="map",
    summary="Text IPL (inst + cars, e.g. from 'ipl decompile') -> binary IPL (bnry) for an IMG or a modloader folder; "
            "keeps the decompiled layout; --compare FILE|ipl:<name> checks it byte for byte.",
    summary_ru="Текстовый IPL (inst + cars) → бинарный IPL (bnry); --compare сверяет результат с файлом или "
               "ipl:<имя> бит в бит.",
    examples=("satk ipl compile my_stream0.ipl",
              "satk ipl compile work/out/mapconv/decompiled/lae2_stream0.ipl --compare ipl:lae2_stream0"))
def ipl_compile(src: str, out: str | None = None, pad: Literal["auto", "sector", "none"] = "auto",
                style: Literal["auto", "vanilla", "sizes"] = "auto", compare: str | None = None,
                profile: str = "vanilla", force: bool = False) -> dict:
    """Compile a text IPL into bnry.

    Args:
        src: text IPL file (sections other than inst and cars are skipped and counted).
        out: output file; relative paths and the default go under work/out/mapconv/compiled/.
        pad: sector = zero padding to 2048 bytes like an IMG entry, none = exact size, auto = the
            '# satk-bnry:' directive of a decompiled file, else sector.
        style: header layout: vanilla (size fields 0), sizes (filled), auto = the directive, else vanilla.
        compare: a bnry file or ipl:<name> SID to compare the result with byte for byte.
        profile: index profile for --compare ipl:<name>.
        force: overwrite an existing file outside work/.
    """
    from .bnry import compile_ipl, first_difference, is_bnry
    from .convert import load_source

    source = load_source(src, profile)
    if is_bnry(source.data):
        raise SatkError("BAD_PARAMS", f"{source.label} is already a binary IPL", hint=f"satk ipl decompile {src}")
    try:
        data, rep = compile_ipl(source.data.decode("latin-1"), style=style, pad=pad)
    except ValueError as e:
        raise SatkError("BAD_PARAMS", f"{source.label}: {e}", hint="fix that line of the text IPL") from None
    warn: list[str] = []
    if rep["skipped"]:
        warn.append("LOST_SECTIONS: a binary IPL holds only inst and cars; skipped "
                    + ", ".join(f"{k} {v}" for k, v in rep["skipped"].items()))
    if rep["lods"] and not rep["directive"]:
        warn.append(f"LOD_SPACE: {rep['lods']} lod values kept as is; in a binary IPL they index the inst section "
                    "of the parent text IPL (the name without _streamN), not this file")
    dst = _out_file(out, "compiled", source.stem)
    _check_dst(dst, source, force)
    atomic_write(dst, data)
    env = obj(file=jpath(dst), src=source.label, inst=rep["inst"], cars=rep["cars"], bytes=len(data),
              style=rep["style"], pad=rep["pad"], skipped=rep["skipped"])
    if compare:
        ref = load_source(compare, profile)
        diff = first_difference(data, ref.data)
        env["compare"] = ref.label
        env["same"] = diff is None
        if diff is not None:
            env["first_difference"] = diff
            warn.append(f"DIFFERENT: differs from {ref.label} at byte {diff} ({len(data)} vs {len(ref.data)} bytes)")
    return with_warn(env, *warn)


CLEAN_OUTPUTS = ("ipl", "lua", "pawn")
PLACEMENT_COLS = ["sid", "model", "name", "role", "pos", "area"]


@op("map.clean", mcp=False, group="map",
    summary="Remove the placements of an area (box/circle/zone) with their LODs: modloader IPL copies (same lines; "
            "removed ones moved underground, LOD indexes stay valid), MTA Lua removeWorldModel, SA-MP "
            "RemoveBuildingForPlayer; LOD check against the index.",
    summary_ru="Убрать объекты района вместе с LOD: копии IPL для modloader (строки на месте, убранные — под землю), "
               "Lua removeWorldModel для MTA, RemoveBuildingForPlayer для SA-MP; сверка LOD-индексов с индексом.",
    examples=("satk map clean Ganton", "satk map clean 2440,-1700,2520,-1640 --to lua pawn",
              "satk map clean 2495,-1690,40 --models veg_* --dry-run"))
def map_clean(area: str, to: list[str] | None = None, profile: str = "vanilla", interior: int = 0,
              z: list[float] | None = None, models: list[str] | None = None, shared_lods: bool = False,
              merge: bool = True, name: str | None = None, dry_run: bool = False, limit: int = 20) -> dict:
    """Remove an area's placements and their LODs for modloader, MTA and SA-MP.

    Args:
        area: x0,y0,x1,y1 (box), x,y,r (circle) or a zone name, label or title (Ganton, GAN1).
        to: outputs: ipl (modloader copies), lua (MTA), pawn (SA-MP); default all three.
        profile: index profile whose placements and files are used.
        interior: area code of the placements (0 = outside world, -1 = all).
        z: [z0, z1] height range.
        models: only these model ids or names (* and ? wildcards).
        shared_lods: also remove LODs that serve placements outside the selection.
        merge: merge calls of one model into one sphere when nothing else of that model is inside.
        name: output folder under work/out/mapconv/clean/ (default from the area).
        dry_run: only report what would be removed; write nothing.
        limit: rows of the removed-placement table (all are in report.json).
    """
    import re

    from ..index.api import open_index
    from .clean import clean, parse_area, resolve_models, write_lua, write_pawn

    outs = [t.lower() for t in (to or list(CLEAN_OUTPUTS))]
    bad = sorted(set(outs) - set(CLEAN_OUTPUTS))
    if bad:
        raise SatkError("BAD_PARAMS", f"unknown output {bad[0]!r}", data={"choices": list(CLEAN_OUTPUTS)})
    if z is not None and len(z) != 2:
        raise SatkError("BAD_PARAMS", "--z needs two numbers: z0 z1")
    lim = clamp_limit(limit)
    db = open_index(profile)
    ar = parse_area(area, db)
    res = clean(db, ar, profile=profile, interior=interior, z=(z[0], z[1]) if z else None,
                models=resolve_models(db, models), shared_lods=shared_lods,
                want_ipl="ipl" in outs, merge=merge)
    sel = res.selection
    removed = sorted(sel.removed.values(), key=lambda p: (p.ipl, p.idx))
    roles = [p.role for p in removed]
    rows = [[p.sid, p.model, p.name, p.role, [round(v, 2) for v in p.pos], p.area] for p in removed]
    folder = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or ar.label).strip("._") or "area"
    base = work("out", "mapconv", "clean") / folder
    warn = list(res.warn)
    if not removed:
        warn.append(f"EMPTY: no placements in {area!r} (interior {interior}, profile {profile})")
    files: dict[str, str] = {}
    if not dry_run and removed:
        header = (f"satk map clean {area} (profile {profile}, interior {interior}): {len(removed)} placements, "
                  f"{roles.count('lod')} of them LODs")
        if "ipl" in outs:
            for fname, data, _n in res.copies.values():
                atomic_write(base / "modloader" / fname, data)
            files["ipl"] = jpath(base / "modloader")
        if "lua" in outs:
            atomic_write(base / f"{folder}.lua", write_lua(res.calls, res.names, header).encode("utf-8"))
            files["lua"] = jpath(base / f"{folder}.lua")
        if "pawn" in outs:
            func = ("RemoveArea_" + re.sub(r"[^A-Za-z0-9_]", "_", folder))[:31]
            atomic_write(base / f"{folder}.pwn", write_pawn(res.calls, res.names, header, func).encode("utf-8"))
            files["pawn"] = jpath(base / f"{folder}.pwn")
            if len(res.calls) > 1000:
                warn.append(f"REMOVE_LIMIT: {len(res.calls)} RemoveBuildingForPlayer calls; SA-MP keeps about 1000 "
                            "per player")
        rep = {"area": area, "profile": profile, "interior": interior, "removed": len(removed),
               "lods": roles.count("lod"), "calls": len(res.calls), "files": dict(files),
               "lod_check": res.lod_check if "ipl" in outs else None,
               "ipl_files": {ipl: {"file": f, "hidden": n} for ipl, (f, _d, n) in sorted(res.copies.items())},
               "kept_shared_lods": [p.sid for p in sel.kept_shared],
               "placements": {"cols": PLACEMENT_COLS,
                              "rows": [[p.sid, p.model, p.name, p.role, [round(v, 3) for v in p.pos], p.area]
                                       for p in removed]}}
        atomic_write(base / "report.json", dumps(rep, pretty=True) + "\n")
        files["report"] = jpath(base / "report.json")
    env = table(PLACEMENT_COLS, rows[:lim], total=len(rows), warn=warn)
    env.update({k: v for k, v in {"area": ar.label, "profile": profile, "removed": len(removed), "hd": roles.count("hd"),
                "lods": roles.count("lod"), "objs": roles.count("obj"), "calls": len(res.calls),
                "kept_shared_lods": len(sel.kept_shared), "ipl_files": len(res.copies),
                "lod_check": res.lod_check if "ipl" in outs and removed else None,
                "files": files or None, "dry_run": True if dry_run else None}.items() if v is not None})
    return env
