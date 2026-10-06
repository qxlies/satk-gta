"""Operations of satk.mta: MTA:SA developer tools (``satk mta ...``).

All are ``mcp=False`` (agents reach them through ``satk_ops``/``satk_op``). Module-level imports are stdlib
only; the work lives in the sibling modules.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from ..core.envelope import clamp_limit, obj, table, with_warn
from ..core.errors import SatkError
from ..core.registry import op

__all__ = ["mta_lint", "mta_resource_new", "mta_pack", "mta_logs", "mta_ref", "mta_server_check"]

Side = Literal["auto", "client", "server", "shared"]


def _out_base() -> Path:
    from ..core.paths import work

    return work("out", "mta")


def _find(path: str, what: str = "resource") -> Path:
    p = Path(path)
    if p.exists():
        return Path(os.path.abspath(p))
    tried = [p]
    if not p.is_absolute():
        c = _out_base() / p
        tried.append(c)
        if c.exists():
            return Path(os.path.abspath(c))
    from ..core.paths import jpath

    raise SatkError("NOT_FOUND", f"no {what} {path!r}",
                    hint="give a path; a relative name is also looked up in work/out/mta/",
                    data={"tried": [jpath(t) if t.is_absolute() else str(t) for t in tried]})


def _page(rows: list[list], limit: int, cursor: str | None) -> tuple[list[list], int, str | None]:
    lim = clamp_limit(limit)
    if cursor and not cursor.isdigit():
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}", hint="pass the 'next' value of the previous page")
    off = int(cursor) if cursor else 0
    return rows[off:off + lim], len(rows), (str(off + lim) if off + lim < len(rows) else None)


def _out_dir(name: str, out: str | None, force: bool) -> Path:
    from ..core.paths import ensure_writable
    from .pack import validate_name

    validate_name(name)
    if out:
        d = Path(out)
        d = d if d.is_absolute() else _out_base() / d
    else:
        d = _out_base() / name
    d = Path(os.path.abspath(d))
    if d.exists() and not d.is_dir():
        raise SatkError("BAD_PARAMS", f"output path {d} is a file", hint="choose a resource directory")
    if d.exists() and any(d.iterdir()) and not force:
        from ..core.paths import jpath

        raise SatkError("EXISTS", f"{jpath(d)} already exists", hint="pass --force to overwrite the generated files "
                                                                     "(other files are kept) or choose another name")
    ensure_writable(d / "meta.xml")
    return d


def _lint_summary(path: Path, ref_spec: str, budget_mb: float = 20.0) -> dict:
    from .api import load_reference
    from .lint import lint_path

    ref, _w = load_reference(ref_spec)
    r = lint_path(path, ref, budget_mb=budget_mb)
    counts: dict[str, int] = {}
    for f in r.findings:
        counts[f.sev] = counts.get(f.sev, 0) + 1
    out = {"clean": not counts.get("error") and not counts.get("warn"), "counts": counts}
    bad = [f"{f.sev} {f.at} {f.code}: {f.msg}" for f in r.findings if f.sev in ("error", "warn")][:5]
    if bad:
        out["first"] = bad
    return out


# --------------------------------------------------------------------------- lint


@op("mta.lint", group="engine", mcp=False,
    summary="Check an MTA:SA resource (folder, .zip or meta.xml) or one .lua file: Lua 5.1 syntax errors with "
            "line:col, unknown/wrong-side/deprecated MTA functions with did_you_mean, argument counts and types, "
            "events never added, render-loop and OOP mistakes, missing files, download size.",
    summary_ru="Проверить ресурс MTA:SA (meta.xml и скрипты) или .lua: синтаксис Lua 5.1 со строкой и колонкой, "
               "неизвестные, не той стороны и устаревшие функции, аргументы, события, ошибки рендера и OOP, "
               "файлы, объём загрузки.",
    examples=("satk mta lint my-resource", "satk mta lint my-resource/client.lua --side client",
              "satk mta lint my-resource --severity warn --codes UNKNOWN_FUNCTION,WRONG_SIDE"))
def mta_lint(path: str, side: Side = "auto", ref: str = "auto", severity: Literal["error", "warn", "info"] = "info",
             codes: list[str] | None = None, budget_mb: float = 20.0, limit: int = 20,
             cursor: str | None = None) -> dict:
    """Lint an MTA resource or a Lua file.

    Args:
        path: resource folder (with meta.xml), its meta.xml, a resource .zip, or one .lua file; relative names are
            also looked up in work/out/mta/.
        side: for a single file or a folder without meta.xml: client, server, shared, or auto (from the file
            name: client/c_/_c, server/s_/_s; else checked against both sides). meta.xml types win.
        ref: MTA reference: auto (knowledge base, else the MTA source tree), kb, source, none (Lua checks only) or
            a JSON file written by satk mta ref.
        severity: lowest severity shown (error, warn, info).
        codes: show only these codes (e.g. UNKNOWN_FUNCTION,WRONG_SIDE); the counts cover all.
        budget_mb: warn when players would download more than this many MB.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from ..core.paths import jpath
    from .api import load_reference
    from .lint import CODES, SEVERITIES, lint_path
    from .pack import positive_number

    if codes:
        bad = [c for c in codes if c.upper() not in CODES]
        if bad:
            import difflib

            raise SatkError("BAD_PARAMS", f"unknown code {bad[0]!r}",
                            did_you_mean=difflib.get_close_matches(bad[0].upper(), CODES, n=3, cutoff=0.5),
                            hint="valid codes are in error.data.codes (and in the mta docs page)",
                            data={"codes": list(CODES)})
    budget_mb = positive_number(budget_mb, "budget_mb")
    p = _find(path)
    r_ref, warn = load_reference(ref)
    res = lint_path(p, r_ref, side=side, budget_mb=budget_mb)
    counts = {s: 0 for s in SEVERITIES}
    for f in res.findings:
        counts[f.sev] += 1
    keep = SEVERITIES[:SEVERITIES.index(severity) + 1]
    want = {c.upper() for c in codes} if codes else None
    rows = [[f.sev, f.at, f.code, f.msg] for f in res.findings
            if f.sev in keep and (want is None or f.code in want)]
    page, total, nxt = _page(rows, limit, cursor)
    env = table(["sev", "at", "code", "msg"], page, total=total, next=nxt, warn=warn)
    env["target"] = jpath(p)
    if res.resource is not None:
        env["resource"] = res.resource.name
    env["scripts"] = len(res.units)
    env["sides"] = res.sides
    env["counts"] = {k: v for k, v in counts.items() if v}
    env["clean"] = counts["error"] == 0 and counts["warn"] == 0
    if res.downloads:
        env["download_kb"] = -(-sum(b for _r, b, _k in res.downloads) // 1024)
    env["ref"] = r_ref.describe()
    return env


@op("mta.ref", group="engine", mcp=False,
    summary="Show (or write as JSON with --out) the MTA:SA reference that mta lint uses: functions per side with "
            "argument lists, events, classes, enums, deprecated names; the JSON lets lint run without the "
            "knowledge base (--ref file.json).",
    summary_ru="Показать или записать в JSON справочник MTA:SA, которым пользуется mta lint (функции по сторонам, "
               "аргументы, события, классы, enum, устаревшие имена); JSON позволяет проверять без базы знаний.",
    examples=("satk mta ref", "satk mta ref --out mta-ref.json"))
def mta_ref(ref: str = "auto", out: str | None = None) -> dict:
    """The reference mta lint uses.

    Args:
        ref: auto, kb, source or a JSON file.
        out: write the reference as JSON (relative paths go under work/out/mta/).
    """
    import json

    from ..core.paths import atomic_write, jpath
    from .api import load_reference

    r, warn = load_reference(ref)
    if not r:
        raise SatkError("NOT_READY", "no MTA reference available", hint="satk kb build (needs an MTA source tree)",
                        data={"warn": warn})
    d = r.describe()
    d["classes"] = len(r.classes)
    d["enums"] = len(r.enums)
    d["deprecated"] = {s: len(v) for s, v in r.deprecated.items()}
    d["neon_only"] = sum(1 for sides in r.funcs.values() if all(s.origin == "neon" for s in sides.values()))
    if out:
        p = Path(out)
        p = p if p.is_absolute() else _out_base() / p
        atomic_write(p, json.dumps(r.to_json(), ensure_ascii=False, separators=(",", ":")))
        d["file"] = jpath(p)
        d["bytes"] = p.stat().st_size
    return with_warn(obj(None, **d), *warn)


# --------------------------------------------------------------------------- scaffolding


@op("mta.resource.new", group="engine", mcp=False,
    summary="Create a starter MTA:SA resource in work/out/mta/<name>/: script (client+server+shared, events, a "
            "command, a render handler), map, shader (texture replace), vehicle-pack, skin-pack or object-pack "
            "(model loader); meta.xml included, checked with mta lint.",
    summary_ru="Создать заготовку ресурса MTA:SA в work/out/mta/<имя>/: script, map, shader, vehicle-pack, "
               "skin-pack или object-pack; meta.xml и проверка mta lint.",
    examples=("satk mta resource new my-panel", "satk mta resource new my-map --kind map --model 1337 --pos 0 0 3",
              "satk mta resource new my-cars --kind vehicle-pack",
              "satk mta resource new grass --kind shader --texture vehiclegrunge256"))
def mta_resource_new(name: str, kind: Literal["script", "map", "vehicle-pack", "skin-pack", "object-pack",
                                              "shader"] = "script",
                     author: str = "satk", oop: bool = False, model: int = 1337, pos: list[float] | None = None,
                     texture: str = "vehiclegrunge256", out: str | None = None, force: bool = False) -> dict:
    """Scaffold a resource.

    Args:
        name: resource (folder) name: letters, digits, _ and -.
        kind: script, map, shader, vehicle-pack, skin-pack or object-pack.
        author: the author attribute of <info>.
        oop: add <oop>true</oop> (script kind).
        model: object model of the map kind.
        pos: x y z of the map object.
        texture: world texture name (or pattern) the shader kind replaces.
        out: output folder (default work/out/mta/<name>; relative paths go under work/out/mta/).
        force: overwrite the generated files of an existing folder (other files are kept).
    """
    from ..core.paths import atomic_write, jpath
    from .scaffold import render

    d = _out_dir(name, out, force)
    files = render(kind, name, author=author, oop=oop, model=model, pos=pos, texture=texture)
    written = []
    for rel, content in sorted(files.items()):
        atomic_write(d / rel, content)
        written.append(rel)
    chk = _lint_summary(d, "auto")
    nxt = {"script": f"satk mta lint {name}",
           "map": f"satk mta lint {name}",
           "shader": f"satk mta lint {name}"}.get(kind, f"satk mta pack <folder with .dff/.txd> --kind "
                                                      f"{kind.split('-')[0]} --name {name} --new-id --force")
    return obj(None, dir=jpath(d), kind=kind, files=written, lint=chk,
               install=f"copy the folder {name} into <server>/mods/deathmatch/resources/ and 'start {name}' in the "
                       "server console (satk never writes into a server or the game)",
               next=nxt)


@op("mta.pack", group="engine", mcp=False,
    summary="Make an MTA:SA resource that loads custom models from a folder of DFF/TXD/COL (or a JSON list): "
            "replace stock ids (--replace, or by file name) or add new ids (--new-id, engineRequestModel); "
            "COL->TXD->DFF order, LOD distance, meta.xml, download size report.",
    summary_ru="Собрать ресурс MTA:SA, загружающий модели из папки DFF/TXD/COL или JSON: замена штатных id или "
               "новые id (engineRequestModel); порядок COL->TXD->DFF, LOD, meta.xml, отчёт о размере загрузки.",
    examples=("satk mta pack mods/cars --kind vehicle", "satk mta pack mods/cars --kind vehicle --new-id --name my-cars",
              "satk mta pack mods/house.dff --kind object --new-id --lod 300",
              "satk mta pack models.json --kind skin --replace 7 --replace 9"))
def mta_pack(src: str, kind: Literal["vehicle", "skin", "object"] | None = None, name: str | None = None,
             new_id: bool = False, replace: list[str] | None = None, parent: str | None = None,
             lod: float | None = None, budget_mb: float = 20.0, profile: str = "vanilla", out: str | None = None,
             force: bool = False) -> dict:
    """Pack models into a resource.

    Args:
        src: folder with .dff/.txd/.col (grouped by file name), one .dff, or a JSON list
            [{"dff","txd","col","name","replace","parent","lod"}].
        kind: vehicle, skin or object (default: from the stock models the file names match).
        name: resource name (default: the folder or file name).
        new_id: add new model ids with engineRequestModel (MTA 1.6) instead of replacing stock ones.
        replace: stock model per model in name order: 411, model:411 or model:infernus (default: the file name).
        parent: base model of new ids (default: vehicle 400, skin 7, object 1337, or the stock model of the same
            name).
        lod: draw distance (engineSetModelLODDistance) for every model.
        budget_mb: warn when the download is bigger.
        profile: index used to resolve model names.
        out: output folder (default work/out/mta/<name>).
        force: overwrite the generated files of an existing folder.
    """
    from ..core.paths import atomic_write, ensure_writable, jpath
    from .pack import collect_models, copy_file, plan_files, positive_number, render_pack, resolve_ids, rw_info

    warn: list[str] = []
    budget_mb = positive_number(budget_mb, "budget_mb")
    if lod is not None:
        lod = positive_number(lod, "lod")
    s = _find(src, "model folder or list")
    models = collect_models(s, warn)
    if kind is None:
        kind = _infer_kind(models, profile)
    nm = name or _default_name(s)
    if lod is not None:
        for m in models:
            m.lod = lod
    elif kind == "object" and new_id:
        for m in models:
            if m.lod is None:
                m.lod = 300.0
    resolve_ids(models, kind, new_id, replace, parent, profile, warn)
    d = _out_dir(nm, out, force)
    rows, copies = plan_files(models)
    texts = render_pack(nm, kind, rows, new_id, files=list(copies))
    for rel in (*copies, *texts):
        ensure_writable(d / rel)
        if (d / rel).is_dir():
            raise SatkError("BAD_PARAMS", f"output file {rel} is an existing directory")
    report: list[list] = []
    for rel, p in copies.items():
        kind_file = p.suffix[1:].lower()
        size = p.stat().st_size
        report.append([rel, kind_file, size, _file_note(kind_file, p, size, rw_info)])
        copy_file(p, d / rel)
    for rel, text in texts.items():
        atomic_write(d / rel, text)
        if rel in ("client.lua", "models.lua"):
            report.append([rel, "script", len(text.encode("utf-8")), ""])
    total = sum(r[2] for r in report)
    budget = budget_mb * 1048576
    if total > budget:
        warn.append(f"DOWNLOAD_SIZE: players download {total / 1048576:.1f} MB (budget {budget_mb:g} MB)")
    advice = _advice(s, report, total, budget)
    chk = _lint_summary(d, "auto", budget_mb)
    env = obj(None, dir=jpath(d), kind=kind, mode="new-id" if new_id else "replace",
              models=[{"name": m.name, **({"replace": m.replace} if m.replace is not None else {"parent": m.parent}),
                       **({"lod": m.lod} if m.lod else {}), **({"note": "; ".join(m.note)} if m.note else {})}
                      for m in models],
              download={"files": len(report), "bytes": total, "mb": round(total / 1048576, 2), "budget_mb": budget_mb,
                        "cols": ["file", "kind", "bytes", "note"],
                        "rows": sorted(report, key=lambda r: -r[2])[:20]},
              advice=advice, lint=chk, scripts=sorted(k for k in texts if k.endswith(".lua")),
              install=f"copy the folder {nm} into <server>/mods/deathmatch/resources/ and 'start {nm}'"
                      + (f"; spawn with exports['{nm}']:{_create_fn(kind)}(...) or /{_cmd(nm)} (admins)"
                         if new_id else ""))
    return with_warn(env, *warn)


def _create_fn(kind: str) -> str:
    return {"vehicle": "createPackVehicle", "skin": "setPackSkin", "object": "createPackObject"}[kind]


def _cmd(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]", "", name.lower())[:24] or "pack"


def _default_name(s: Path) -> str:
    import re

    base = s.stem if s.is_file() else s.name
    n = re.sub(r"[^A-Za-z0-9_-]+", "-", base).strip("-")[:64]
    return n or "models"


def _infer_kind(models, profile: str) -> str:
    from .pack import KINDS, _model_id

    found = set()
    for m in models:
        try:
            _mid, sec = _model_id(m.name, profile)
        except SatkError:
            continue
        for k, (_t, _p, secs) in KINDS.items():
            if sec in secs:
                found.add(k)
    if len(found) == 1:
        return found.pop()
    raise SatkError("BAD_PARAMS", "cannot tell the kind of these models", hint="pass --kind vehicle, skin or object",
                    data={"matched": sorted(found)})


def _file_note(kind: str, p: Path, size: int, rw_info) -> str:
    notes = []
    if kind in ("dff", "txd"):
        info = rw_info(p)
        want = 0x10 if kind == "dff" else 0x16
        if info is None or info[0] != want:
            notes.append(f"not a RenderWare {'clump' if kind == 'dff' else 'texture dictionary'}")
        elif info[1] != 0x36003:
            from .pack import _ver_text

            notes.append(f"RW {_ver_text(info[1])} (GTA SA uses 3.6.0.3)")
    elif kind == "col":
        try:
            with open(p, "rb") as f:
                if f.read(4) not in (b"COLL", b"COL2", b"COL3", b"COL4"):
                    notes.append("not a COL file")
        except OSError:
            pass
    if kind == "txd" and size > 4 * 1048576:
        notes.append("big TXD: compress (DXT) and cap texture size")
    if kind == "dff" and size > 2 * 1048576:
        notes.append("big DFF: check the polygon count")
    return "; ".join(notes)


def _advice(src: Path, report: list, total: int, budget: float) -> list[str]:
    out = []
    txd = sum(r[2] for r in report if r[1] == "txd")
    if total and txd > total / 2:
        out.append(f"textures are {txd * 100 // max(total, 1)}% of the download: satk texture audit "
                   f"{src.name if src.is_dir() else src.parent.name} shows what DXT compression and smaller sizes "
                   "save; satk texture optimize writes compressed copies")
    if total > budget:
        out.append("over budget: split optional models into a second resource, or list them with "
                   "download=\"false\" and fetch on demand with downloadFile")
    out.append("MTA caches downloads on each client; files are sent as they are (satk never encrypts them)")
    return out


# --------------------------------------------------------------------------- logs


@op("mta.logs", group="engine", mcp=False,
    summary="Parse MTA:SA logs (server.log, clientscript.log, server console output, text copied from /debugscript) "
            "into rows: level, resource, file:line, message, repeat count and a fix hint (signature of the function "
            "for Bad argument, side or spelling for attempt to call nil).",
    summary_ru="Разобрать логи MTA:SA (server.log, clientscript.log, вывод консоли, текст /debugscript) в строки: "
               "уровень, ресурс, файл:строка, сообщение, повторы и подсказка по исправлению.",
    examples=("satk mta logs server.log", "satk mta logs clientscript.log --resource my-panel --level error",
              "satk mta logs debug.txt --level all --no-group"))
def mta_logs(file: str, level: Literal["error", "warning", "info", "all"] = "warning", resource: str | None = None,
             grep: str | None = None, group: bool = True, ref: str = "auto", limit: int = 20,
             cursor: str | None = None) -> dict:
    """Parse an MTA log.

    Args:
        file: server.log, clientscript.log, a saved console output or debug window text (the last 32 MB are read).
        level: lowest level shown: error, warning, info or all (all includes plain server lines).
        resource: only rows of this resource.
        grep: only rows whose message contains this text (case-insensitive).
        group: merge repeated messages (same level, place and text) into one row with a count.
        ref: MTA reference for hints: auto, kb, source, none or a JSON file.
        limit: rows per page (max 500).
        cursor: value of `next` from the previous page.
    """
    from ..core.paths import jpath
    from .api import load_reference
    from .logs import LEVELS, hint_for, parse_lines, read_tail

    p = _find(file, "log file")
    if p.is_dir():
        for cand in ("server.log", "clientscript.log", "logs/server.log", "logs/clientscript.log"):
            if (p / cand).is_file():
                p = p / cand
                break
        else:
            raise SatkError("NOT_FOUND", f"no server.log or clientscript.log in {jpath(p)}",
                            hint="give the log file itself")
    lines, first, truncated = read_tail(p)
    rows = parse_lines(lines, first)
    warn: list[str] = []
    if truncated:
        warn.append("TRUNCATED: only the last 32 MB of the log were read")
    try:
        r_ref, w2 = load_reference(ref)
    except SatkError as e:
        r_ref, w2 = None, [f"{e.code}: {e.msg}"]
    if r_ref is not None and not r_ref:
        r_ref = None
        if w2:
            warn.append("NOT_READY: no MTA reference: only built-in log hints are available; "
                        "build the reference with: satk kb build")
    else:
        warn.extend(w2)
    counts: dict[str, int] = {}
    per_res: dict[str, int] = {}
    for r in rows:
        counts[r.level] = counts.get(r.level, 0) + r.count
        if r.resource and r.level in ("error", "warning"):
            per_res[r.resource] = per_res.get(r.resource, 0) + r.count
    keep = LEVELS if level == "all" else LEVELS[:LEVELS.index(level) + 1]
    sel = [r for r in rows if r.level in keep and (resource is None or r.resource.lower() == resource.lower())
           and (grep is None or grep.lower() in r.msg.lower())]
    if group:
        merged: dict[tuple, object] = {}
        order = []
        for r in sel:
            k = (r.level, r.resource, r.at, r.msg)
            if k in merged:
                merged[k].count += r.count
            else:
                merged[k] = r
                order.append(k)
        sel = [merged[k] for k in order]
        rank = {lv: i for i, lv in enumerate(LEVELS)}
        sel.sort(key=lambda r: (rank[r.level], -r.count, r.line))
    out_rows = []
    for r in sel:
        out_rows.append([r.line, r.level, r.resource, r.at, r.msg, r.count, hint_for(r, r_ref)])
    page, total, nxt = _page(out_rows, limit, cursor)
    env = table(["line", "level", "res", "at", "msg", "n", "hint"], page, total=total, next=nxt, warn=warn)
    env["file"] = jpath(p)
    env["lines"] = len(lines)
    env["counts"] = counts
    if per_res:
        env["by_resource"] = dict(sorted(per_res.items(), key=lambda kv: -kv[1])[:10])
    return env


# --------------------------------------------------------------------------- server check


@op("mta.server_check", group="engine", mcp=False, long_running=True,
    summary="Load a resource in the built MTA server (x64, 127.0.0.1 only, no client, private copy under "
            "work/mta/check): start it, collect the server's messages about it (load failures, script errors, "
            "warnings), stop. The real loader's verdict.",
    summary_ru="Загрузить ресурс в собранный сервер MTA (x64, только 127.0.0.1, без клиента, копия в work/mta/check): "
               "запустить, собрать сообщения сервера о ресурсе (ошибки загрузки и скриптов), остановить.",
    examples=("satk mta server-check my-panel", "satk mta server-check my-cars --timeout 90"))
def mta_server_check(path: str, timeout: float = 60, wait: float = 3) -> dict:
    """Start the built server with only this resource and report what it says.

    Args:
        path: resource folder (with meta.xml); relative names are also looked up in work/out/mta/.
        timeout: seconds to wait for the server to be ready.
        wait: seconds to keep the server running after start (timers and onResourceStart output).
    """
    from .servercheck import server_check

    p = _find(path)
    if not (p / "meta.xml").is_file():
        raise SatkError("BAD_PARAMS", f"{p.name} has no meta.xml", hint="satk mta lint shows what is missing")
    if timeout <= 0 or wait < 0 or wait > 60:
        raise SatkError("BAD_PARAMS", "timeout must be positive and wait 0..60 seconds")
    return server_check(p, timeout=timeout, wait=wait)
