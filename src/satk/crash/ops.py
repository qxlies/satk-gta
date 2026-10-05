"""Operations of satk.crash — crash dumps, crash logs, known crashes, culprits (M2-07, M3 C1; report 19).

CLI: ``satk crash analyze|info|list|sample|known|logs|bisect``. All CLI-only (``mcp=False``); MCP
reaches them through the generic operation tool. Module-level imports are stdlib-only.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Literal

from ..core.envelope import clamp_limit, obj, table
from ..core.errors import SatkError
from ..core.registry import op

__all__ = ["crash_analyze", "crash_info", "crash_list", "crash_sample", "crash_known", "crash_logs", "crash_bisect"]

#: a dump written within this many seconds before the newest log is the same crash (MTA: dump + core.log)
_PAIR_S = 300


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    if cursor.startswith("o:") and cursor[2:].isdigit():
        return int(cursor[2:])
    raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}", hint="pass the 'next' value of the previous page")


def _dirs(dirs: list[str] | None) -> list[tuple[Path, str]]:
    from .find import default_dirs

    if dirs:
        return [(Path(d), "dir") for d in dirs]
    return default_dirs()


def _latest(dirs: list[str] | None) -> str:
    """Newest crash: a dump beats a log written with it; samples only when nothing else is there."""
    from .find import find_files

    files = find_files(_dirs(dirs))
    real = [f for f in files if f["origin"] != "sample"] or files
    if not real:
        raise SatkError("NOT_FOUND", "no crash dumps or crash logs found",
                        hint="satk crash list (searched dirs) | satk crash sample (a synthetic one) | give a path")
    top = real[0]
    if top["kind"] != "dump":
        dump = next((f for f in real if f["kind"] == "dump" and f["mtime"] >= top["mtime"] - _PAIR_S), None)
        if dump is not None:
            top = dump                        # a dump says more than its core.log entry
    return str(top["path"])


def _path(path: str | None, last: bool, dirs: list[str] | None) -> str:
    if path and last:
        raise SatkError("BAD_PARAMS", "give a path or --last, not both")
    if path:
        return path
    if last:
        return _latest(dirs)
    raise SatkError("BAD_PARAMS", "give a dump/log path or --last", hint="satk crash analyze --last")


@op("crash.analyze", mcp=False, group="re",
    summary="Analyze a crash: minidump, MTA core.log or a single-player log (modloader.log, SA-MP). Stack with "
            "gta_sa.exe functions, MTA patches, the known CrashInfo entry and its solution, suspects (model IDs "
            "from registers/scrlog -> mod) and the culprit. <= 30 lines.",
    summary_ru="Разбор краша: стек и функции, известная запись CrashInfo с решением, подозреваемые и виновный мод",
    examples=("satk crash analyze --last", "satk crash analyze client_1.6_gtasa_0013bf09_5.dmp",
              "satk crash analyze modloader/modloader.log", "satk crash analyze crash.dmp --game C:/Games/GTASA"))
def crash_analyze(path: str | None, last: bool = False, limit: int = 10, thread: int | None = None,
                  block: int = 0, images: list[str] | None = None, dir: list[str] | None = None,  # noqa: A002
                  scan_kb: int = 64, game: str | None = None, profile: str | None = None) -> dict:
    """Crash report (frames table + crash summary + advice).

    Frames come from the instruction pointer, the EBP chain and a stack scan for return addresses
    preceded by a CALL (``via``: ip, ebp, scan; ``?`` = code bytes unknown, unverified). Advice keys:
    ``known`` (CrashInfo entries), ``solution``, ``suspects``, ``culprit``, ``logs``.

    Args:
        path: .dmp minidump, MTA crash log (core.log, server_pending_upload.log, cdb output) or a
            single-player log with a crash report (modloader.log, SA-MP/mod_sa logs, a pasted report).
        last: analyze the newest dump/log of 'satk crash list' instead of a path.
        limit: frames shown.
        thread: thread id to walk (default: the crashing thread).
        block: crash report of a file with several (0 = auto: newest MTA block, first single-player report;
            -1 = newest, 1 = oldest).
        images: directories with module files of the crashed build (for exports and CALL checks).
        dir: directories searched by --last (default: those of 'satk crash list').
        scan_kb: KiB of stack scanned for return addresses.
        game: the game folder that crashed (its modloader folder and logs give suspects and the culprit;
            default: the folder the file lies in, or the folder of gta_sa.exe named in a non-MTA dump).
        profile: index profile used to attribute model IDs (default: the profile whose root is the game folder).
    """
    from .analyze import analyze_file

    if not 1 <= scan_kb <= 4096:
        raise SatkError("BAD_PARAMS", "scan_kb must be in 1..4096")
    return analyze_file(_path(path, last, dir), limit=clamp_limit(limit, default=10, maximum=200), images=images,
                        thread=thread, block=block, scan_kb=scan_kb, game=game, profile=profile)


@op("crash.info", mcp=False, group="re",
    summary="Parts of a minidump or crash log as tables: summary, modules (PDB ids, matching images), threads, "
            "streams, memory, MTA sections (pools, logs, report.log) and one section, raw stack scan, text streams; "
            "logs: summary, blocks.",
    summary_ru="Части дампа/лога: модули, потоки, потоки данных, память, секции MTA, стек, текст",
    examples=("satk crash info --last", "satk crash info --last --part modules",
              "satk crash info dump.dmp --part section --section POL", "satk crash info core.log --part blocks"))
def crash_info(path: str | None, last: bool = False,
               part: Literal["summary", "modules", "threads", "streams", "memory", "sections", "section", "stack",
                             "text", "blocks"] = "summary",
               section: str | None = None, limit: int = 50, cursor: str | None = None, thread: int | None = None,
               block: int = -1, images: list[str] | None = None, dir: list[str] | None = None) -> dict:  # noqa: A002
    """Details of a dump or log.

    Args:
        path: .dmp minidump or a text crash log.
        last: use the newest dump/log of 'satk crash list'.
        part: what to show.
        section: for --part section: MTA section tag (POL, LOG, REP, MEM, MSC, CAS, D3D, DXI, CAT), TAG:N for the N-th.
        limit: rows (or text lines) per page.
        cursor: page cursor from 'next'.
        thread: thread id for --part stack.
        block: crash block of a log (-1 = newest).
        images: directories with module files of the crashed build.
        dir: directories searched by --last.
    """
    from .info import info_file

    return info_file(_path(path, last, dir), part, section=section, limit=clamp_limit(limit, default=50),
                     offset=_offset(cursor), images=images, thread=thread, block=block)


@op("crash.list", mcp=False, group="re",
    summary="List crash dumps and crash logs, newest first: work/dumps, the MTA fork's Bin (client and server), "
            "installed MTA builds (registry), modloader.log with a crash report in the configured game, samples.",
    summary_ru="Список дампов и крэш-логов (новые сверху): work/dumps, Bin форка MTA, MTA, modloader.log игры, примеры",
    examples=("satk crash list", "satk crash list --dir C:/MTA/mta/dumps/private"))
def crash_list(dir: list[str] | None = None, limit: int = 20, cursor: str | None = None) -> dict:  # noqa: A002
    """Dumps and logs.

    Args:
        dir: directories to search instead of the defaults.
    """
    from ..core.paths import jpath
    from .find import find_files

    dirs = _dirs(dir)
    files = find_files(dirs)
    off = _offset(cursor)
    lim = clamp_limit(limit, default=20)
    rows = []
    for f in files[off:off + lim]:
        when = _dt.datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M")
        rows.append([jpath(f["path"]), f["kind"], f["size"], when, f["origin"], f.get("module"), f.get("offset"),
                     f.get("code")])
    env = table(["path", "kind", "size", "modified", "origin", "module", "offset", "code"], rows, total=len(files),
                next=f"o:{off + lim}" if len(files) > off + lim else None)
    if not files:
        env["searched"] = [jpath(d) for d, _o in dirs if d.is_dir()] or "no existing dump directory"
        env["hint"] = "satk crash sample writes a synthetic dump; ProcDump/cdb dumps go to work/dumps"
    return env


@op("crash.sample", mcp=False, group="re",
    summary="Write a generated sample crash for trying 'crash analyze': --kind mta (x86 minidump with MTA streams "
            "and sections, plus core.log) or --kind sp (single-player game folder: dump, modloader, logs). "
            "Deterministic.",
    summary_ru="Записать синтетический краш для проб: MTA (дамп и core.log) или одиночная игра (папка с логами)",
    examples=("satk crash sample", "satk crash sample --kind sp"))
def crash_sample(kind: Literal["mta", "sp"] = "mta") -> dict:
    """Generated sample files (invented bytes, nothing from the game or MTA).

    Args:
        kind: mta = MTA client crash in work/out/crash/sample; sp = single-player game folder in
            work/out/crash/sample-sp (pickup of a disabled mod's model, CrashInfo entry 0x00456809).
    """
    from ..core.paths import atomic_write, work
    from .find import SAMPLE_DIR_PARTS, SP_SAMPLE_DIR_PARTS

    if kind == "sp":
        from .synth import sample_sp_files

        d = work(*SP_SAMPLE_DIR_PARTS)
        out = {}
        for rel, data in sample_sp_files().items():
            p = d.joinpath(*rel.split("/"))
            p.parent.mkdir(parents=True, exist_ok=True)
            out[rel] = atomic_write(p, data)
        dmp = Path(out["gta_sa_sample.dmp"]).as_posix()
        mlog = Path(out["modloader/modloader.log"]).as_posix()
        return obj(None, game=Path(d).as_posix(), dump=dmp, log=mlog, files=len(out),
                   hint=f'satk crash analyze --last | satk crash analyze "{mlog}" | satk crash logs --game '
                        f'"{Path(d).as_posix()}" | satk crash bisect "{Path(d).as_posix()}/modloader"')
    from .synth import sample_dump, sample_log

    d = work(*SAMPLE_DIR_PARTS)
    dmp = atomic_write(d / "client_sample_gtasa_0000e6a3_5.dmp", sample_dump())
    log = atomic_write(d / "core.log", sample_log())
    return obj(None, dump=dmp, log=log, hint=f'satk crash analyze "{Path(dmp).as_posix()}" | satk crash analyze --last')


@op("crash.known", mcp=False, group="re",
    summary="Look up the CrashInfo crash list (GTA SA 1.0 US, 2026-10-02, MIT): by crash address (0x00456809, "
            "gta_sa.exe+0x56809), SCRLog opcode (038B), script:<name>, module (CLEO.asi) or words. One hit -> "
            "problem and solution in full.",
    summary_ru="Поиск в списке крэшей CrashInfo: по адресу, опкоду, скрипту, модулю или словам; решение целиком",
    examples=("satk crash known 0x00456809", "satk crash known 038B", "satk crash known CLEO.asi",
              "satk crash known pickup model", "satk crash known"))
def crash_known(query: str | None, kind: Literal["auto", "addr", "command", "script", "module", "text"] = "auto",
                limit: int = 20, cursor: str | None = None) -> dict:
    """CrashInfo list entries.

    Args:
        query: crash address, opcode, script:<name>, module file name or words (all must occur); empty = list info.
        kind: how to read the query (auto guesses).
    """
    from . import crashlist

    entries, meta = crashlist.load()
    src = meta["source"]
    label = crashlist.source_label(meta)
    if not (query or "").strip():
        return obj(None, list=label, game=src.get("game"), entries=len(entries), sections=meta["sections"],
                   revision=src.get("revision"), license=src.get("license"), notice=f"data/{src.get('notice')}",
                   hint="satk crash known 0x00456809 | satk crash known 038B | satk crash known pickup")
    hits = crashlist.lookup(query, kind=kind)
    if len(hits) == 1:
        e, how = hits[0]
        out = obj(None, entry=e.n, head=e.head, section=e.section, match=how, line=e.line, **e.as_dict())
        if e.backtrace and e.addrs:
            out["on_stack"] = [f"0x{a:08X}" for a in e.backtrace]
        out["list"] = label
        return out
    off = _offset(cursor)
    lim = clamp_limit(limit, default=20)
    rows = [[e.n, e.head[:60], e.section, how, (e.title() or "")[:100]] for e, how in hits[off:off + lim]]
    env = table(["n", "head", "section", "match", "title"], rows, total=len(hits),
                next=f"o:{off + lim}" if len(hits) > off + lim else None)
    env["list"] = label
    if not hits:
        env["result"] = f"not in the list: {query!r}"
        env["hint"] = "satk crash known <words> searches the texts"
    elif len(hits) > 1:
        env["hint"] = f"satk crash known {hits[0][0].head.split(',')[0]!s} | narrow with more words"
    return env


@op("crash.logs", mcp=False, group="re",
    summary="Digest of a single-player game's logs: modloader.log (version, mods, errors, crash reports), "
            "scrlog.log (last script and command, model IDs requested) and the CLEO log (scripts, errors). "
            "Give log files or --game DIR.",
    summary_ru="Сводка логов одиночной игры: modloader.log, scrlog.log и лог CLEO (моды, скрипты, ошибки, крэши)",
    examples=("satk crash logs --game C:/Games/GTASA", "satk crash logs scrlog.log"))
def crash_logs(paths: list[str] | None, game: str | None = None,
               kind: Literal["modloader", "scrlog", "cleo"] | None = None) -> dict:
    """Parsed game logs.

    Args:
        paths: log files (kind detected from the name or the content).
        game: game folder: finds modloader/modloader.log, scrlog.log and cleo.log there
            (default: the configured game folder when no paths are given).
        kind: force the kind of the given files.
    """
    from ..core.paths import cfg, jpath
    from .gamelogs import find_logs, parse_any

    files: list[tuple[str | None, Path]] = [(kind, Path(p)) for p in paths or []]
    where = None
    if game or not files:
        g = Path(game) if game else (cfg().paths.get("game_root") or cfg().paths.get("installed"))
        if g is None or not Path(g).is_dir():
            raise SatkError("NOT_FOUND", f"no such game folder: {jpath(Path(g)) if g else '(not configured)'}",
                            hint="satk crash logs --game <GTA San Andreas folder> | satk crash logs FILE")
        where = jpath(Path(g))
        found = find_logs(g)
        if not found and not files:
            raise SatkError("NOT_FOUND", f"no modloader.log, scrlog.log or cleo.log in {where}",
                            hint="give the log files")
        files += [(k, p) for k, p in found.items()]
    out = obj(None)
    if where:
        out["game"] = where
    for k, p in files:
        res = parse_any(p, k)
        key = res.pop("kind")
        name = key if key not in out else f"{key}_{sum(1 for x in out if x.startswith(key)) + 1}"
        out[name] = res
    return out


@op("crash.bisect", mcp=False, group="re",
    summary="Find the modloader mod behind a crash by halving: prints the [Profiles.<P>.IgnoreMods] section of "
            "modloader.ini for the next test run (the user pastes it), then takes the outcomes "
            "(--results crash,ok,...). Never writes into the game.",
    summary_ru="Бисекция модов modloader: секция IgnoreMods для следующего запуска, результаты --results crash,ok",
    examples=("satk crash bisect C:/Games/GTASA/modloader", "satk crash bisect C:/Games/GTASA --results ok,crash"))
def crash_bisect(path: str | None, results: list[str] | None = None) -> dict:
    """Next bisection step.

    Args:
        path: modloader folder or the game folder (default: the configured game folder).
        results: outcomes of the test runs so far, in order: crash or ok (comma-separated or repeated).
    """
    from .bisect import plan

    return plan(path, results)
