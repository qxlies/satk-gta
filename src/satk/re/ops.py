"""Operations of satk.re — the sa-re symbol DB (SPEC §4.6, §4.7 tools 20-23, §4.9; owner WP-09).

CLI: ``satk re build|addr|find|src|patches|limits|nodes|export``; MCP: ``re_addr``, ``re_find``,
``re_src``, ``re_patches``. Also registers the ``fn``/``g``/``vt``/``patch`` SID provider, the
``re`` status section and the ``re_symdb`` doctor check. Module-level imports are stdlib-only.
"""

from __future__ import annotations

import re
import sys
from typing import Literal

from ..core.envelope import clamp_limit, obj
from ..core.errors import SatkError
from ..core.ids import register_provider
from ..core.registry import doctor_check, op, report_progress, status_provider
from .provider import ReProvider

__all__ = ["re_build", "re_addr", "re_find", "re_src", "re_patches", "re_limits", "re_nodes", "re_export"]

_PROVIDER = ReProvider()
register_provider(_PROVIDER, replace=True)


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    if cursor.startswith("o:") and cursor[2:].isdigit():
        return int(cursor[2:])
    raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}")


@op("re.build", summary="Rebuild the sa-re symbol DB (work/re/symdb.sqlite) from gta-reversed, plugin-sdk, "
                        "gta_sa.exe and MTA sources (read-only git, no network).",
    summary_ru="Пересобрать символьную БД sa-re из gta-reversed, plugin-sdk, gta_sa.exe и исходников MTA",
    mcp=False, long_running=True, examples=("satk re build", "satk re build --no-trunk"))
def re_build(exe: str | None = None, trunk: bool = True, ghidra_functions: str | None = None) -> dict:
    """Build the symbol DB (about 20-40 s).

    Args:
        exe: gta_sa.exe to analyse (default: <paths.game>/gta_sa.exe).
        trunk: also scan our MTA fork (engine/mtasa) when it differs from upstream.
        ghidra_functions: optional functions.jsonl export with exact bounds (inclusive ranges on input).
    """
    from .build import build_db, default_sources
    from .db import db_path, reset_cache

    srcs = default_sources(exe=exe, trunk=trunk, ghidra_functions=ghidra_functions)
    out = db_path()
    from ..core.paths import ensure_writable

    ensure_writable(out)
    reset_cache()
    stats = build_db(out, srcs, progress=lambda name: report_progress(0, None, name))
    brief = {k: v for k, v in stats.items() if (not k.endswith("_scan") or k == "patch_scan") and k != "timings"}
    return obj(None, path=out, seconds=stats.get("seconds"), stats=brief, timings=stats.get("timings"))


@op("re.addr", mcp="re_addr",
    summary="Resolve gta_sa.exe addresses (0x53BF09, gta_sa.exe+0x13BF09) or an MTA crash log to "
            "function+offset, gta-reversed file:line, thunks and MTA patches. One address -> object, "
            "several -> table.",
    summary_ru="Адрес gta_sa.exe или крэш-лог MTA -> функция+смещение, file:line, thunk, патчи MTA",
    examples=("satk re addr 0x53BF09", "satk re addr gta_sa.exe+0x13E4FA", "satk re addr --text-file crash.txt"))
def re_addr(text: list[str] | None, text_file: str | None = None, limit: int = 50) -> dict:
    """Address -> function.

    Args:
        text: addresses or crash-log text ('-' reads stdin).
        text_file: file with that text (crash dump, log).
    """
    from .api import resolve_text
    from .db import open_db

    parts: list[str] = []
    for t in text or []:
        if t == "-":
            parts.append(sys.stdin.read())
        else:
            parts.append(t)
    if text_file:
        try:
            with open(text_file, "r", encoding="utf-8", errors="replace") as f:
                parts.append(f.read())
        except OSError as e:
            raise SatkError("NOT_FOUND", f"cannot read {text_file}: {e}") from None
    if not parts:
        raise SatkError("BAD_PARAMS", "give addresses, --text-file or '-'", hint="satk re addr 0x53BF09")
    # A drive-letter path (C:\\...) is not a Key: value crash-log line.
    log_lines = text_file or any(
        "\n" in p or re.match(r"^\s*[A-Za-z][\w -]*\s*(?:=|:(?![\\/]))", p) for p in parts
    )
    joined = ("\n" if log_lines else " ").join(parts)
    db = open_db()
    out = resolve_text(db, joined, limit=clamp_limit(limit, default=50))
    warns = _exe_layout_warnings(db)
    if warns:
        out.setdefault("warn", []).extend(warns)
    return out


_LAYOUT_MEMO: dict[tuple, dict | None] = {}


def _exe_info(path) -> dict | None:
    """``satk.core.detect.identify_exe`` memoized by (path, size, mtime); ``None`` if unreadable."""
    import os

    from ..core.detect import identify_exe

    try:
        st = os.stat(path)
    except OSError:
        return None
    k = (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime_ns)
    if k not in _LAYOUT_MEMO:
        try:
            _LAYOUT_MEMO[k] = identify_exe(path)
        except OSError:
            _LAYOUT_MEMO[k] = None
    return _LAYOUT_MEMO[k]


def _exe_layout_warnings(db) -> list[str]:
    """``UNSUPPORTED_EXE`` when the symbol DB's exe or the user's game exe is not the 1.0 US layout."""
    from pathlib import Path

    from ..core.detect import exe_versions
    from ..core.paths import cfg

    out: list[str] = []
    try:
        meta = db.meta
    except Exception:  # noqa: BLE001 - fake/partial DBs in tests
        return out
    sha = meta.get("exe_sha256")
    known = {v["sha256"]: v for v in exe_versions().get("variants", [])}
    built_from = Path(meta["exe_path"]) if meta.get("exe_path") else None
    if sha and sha in known and not known[sha].get("supported"):
        out.append(f"UNSUPPORTED_EXE: the symbol DB was built from {known[sha]['variant']}; addresses assume 1.0 US")
    elif sha and sha not in known and built_from is not None and built_from.is_file():
        info = _exe_info(built_from)
        if info is not None and not info["supported"]:
            out.append(f"UNSUPPORTED_EXE: the symbol DB was built from {info['variant']} ({built_from.name}); "
                       "satk re needs the 1.0 US layout")
    gr = cfg().paths.get("game_root")
    exe = next((gr / n for n in ("gta_sa.exe", "gta-sa.exe") if gr is not None and (gr / n).is_file()), None)
    sizes_ok = {s["size"] for s in exe_versions().get("sizes", []) if s.get("supported")}
    if exe is not None and not (built_from is not None and _same(exe, built_from)) \
            and exe.stat().st_size not in sizes_ok:  # a 1.0 US-sized exe is not read on every call
        info = _exe_info(exe)
        if info is not None and not info["supported"]:
            out.append(f"UNSUPPORTED_EXE: your game executable {exe.name} is {info['variant']}: its crash addresses "
                       "do not match the 1.0 US symbol DB")
    return out


def _same(a, b) -> bool:
    import os

    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


@op("re.find", mcp="re_find",
    summary="Find gta_sa.exe symbols by name: functions, globals, vtables, struct sizes. Exact match first "
            "(CPed::Update), then ::member, prefix, substring.",
    summary_ru="Поиск символов gta_sa.exe по имени (точное, затем префикс, затем подстрока)",
    examples=("satk re find CPed::Update", "satk re find gGameState --kind global"))
def re_find(name: str, kind: Literal["func", "global", "vtable", "struct"] | None = None, limit: int = 10,
            cursor: str | None = None) -> dict:
    """Symbol search.

    Args:
        name: name or part of it (case-insensitive).
    """
    from .api import find
    from .db import open_db

    return find(open_db(), name, kind, clamp_limit(limit, default=10), _offset(cursor))


@op("re.src", mcp="re_src",
    summary="Show gta-reversed C++ of a function (by name or address): install line and definition, "
            "read-only from the local clone; never copy it into files.",
    summary_ru="Показать C++ функции из gta-reversed (только чтение, не копировать в файлы)",
    examples=("satk re src CDoor::Process", "satk re src 0x6F4040 --context 60"))
def re_src(fn: str, context: int = 30) -> dict:
    """Source of a function.

    ``context`` is 0..400 lines. A function the symbol DB has no source line for (not hooked in
    gta-reversed, often a stub) is answered from the knowledge base: its file:line with
    ``status: not reversed``, plus the lines when the clone is readable (``via: kb``).

    Args:
        fn: function name (CDoor::Process) or an address inside it.
        context: lines after the definition.
    """
    from .api import _kb_location, _kb_src, src
    from .db import open_db

    try:
        db = open_db()
    except SatkError as e:
        if e.code != "NOT_READY" or not 0 <= context <= 400:
            raise
        hit = _kb_location(fn.strip()[3:] if fn.strip().lower().startswith("fn:") else fn.strip())
        if hit is None:
            raise
        env = _kb_src(None, hit, context)
        env["warn"] = [f"NO_SYMDB: {e.msg}; answered from the knowledge base (satk re build adds hooks, thunks and "
                       "MTA patches)"]
        return env
    return src(db, fn, context)


@op("re.patches", mcp="re_patches",
    summary="MTA patch sites by function or address range, with file:line.",
    summary_ru="Патчи MTA в gta_sa.exe по функции или диапазону адресов, с file:line",
    examples=("satk re patches --fn CGame::Process", "satk re patches --range 0x53BF00-0x53C000 --origin neon"))
def re_patches(fn: str | None = None, range: str | None = None,  # noqa: A002 - SPEC name
               origin: Literal["upstream", "trunk", "neon", "satk", "all"] = "all",
               kind: Literal["hookpos", "hookinstall", "hookinstallcall", "memput", "memset", "memcpy",
                             "vtable_slot", "reloc_manifest", "code_mover"] | None = None,
               limit: int = 50, cursor: str | None = None) -> dict:
    """MTA patch sites.

    Upstream sites repeated by trunk are omitted from the combined listing.
    ``raw_total`` counts them; ``--origin upstream`` lists raw upstream rows.

    Args:
        fn: function name or an address inside it.
        range: address range 0xA-0xB (inclusive).
        origin: which MTA tree.
    """
    from .api import patches
    from .db import open_db

    return patches(open_db(), fn, range, origin, kind, clamp_limit(limit, default=50), _offset(cursor))


@op("re.limits", mcp=False,
    summary="Engine limits from gta-reversed: arrays (StaticRef with length), pools, stores, id ranges.",
    summary_ru="Лимиты движка: массивы, пулы, хранилища, диапазоны ID (из gta-reversed)",
    examples=("satk re limits", "satk re limits --kind pool", "satk re limits --match Corona"))
def re_limits(kind: Literal["pool", "array", "id_range", "streaming", "store", "world"] | None = None, match: str | None = None,
              limit: int = 20, cursor: str | None = None, summary: bool = False) -> dict:
    """Limit table (limit_def).

    Args:
        kind: restrict to one kind.
        match: substring of the name.
        limit: rows per page.
        cursor: page cursor from 'next'.
        summary: only the counts per kind (no rows).
    """
    from .api import limits
    from .db import open_db

    return limits(open_db(), kind, match, clamp_limit(limit, default=20), _offset(cursor), summary=summary)


@op("re.nodes", mcp=False,
    summary="Frame names the engine looks up per vehicle type (automobile, mtruck, quad, heli, plane, boat, train, "
            "fheli, fplane, bike, bmx, trailer) and for peds, with ids, part/dummy/extra role and flags, read from "
            "gta_sa.exe. Use it to name the frames, dummies and extras of a new model.",
    summary_ru="Имена фреймов, которые движок ищет у каждого типа транспорта и у педов: id, роль (деталь, "
               "дамми, экстра) и флаги, прочитанные из gta_sa.exe.",
    examples=("satk re nodes", "satk re nodes --type automobile", "satk re nodes --type bike",
              "satk re nodes --type ped"))
def re_nodes(type: Literal["automobile", "mtruck", "quad", "heli", "plane", "boat", "train", "fheli", "fplane",  # noqa: A002
                           "bike", "bmx", "trailer", "ped"] | None = None,
             exe: str | None = None, limit: int = 100) -> dict:
    """Frame-name tables of the executable (no symbol DB needed; node names need the knowledge base).

    Without ``type``: one row per table (12 vehicle types and ped) with the counts of parts, dummies
    and extras. With ``type``: name, id (node number, dummy position index, 0 for extras), role,
    flags and flag words, and the gta-reversed node name when ``satk kb build`` ran.

    Args:
        type: vehicle type or ped.
        exe: gta_sa.exe 1.0 US to read (default: the clean copy, else the game).
        limit: rows of one table.
    """
    from .nodes import nodes

    return nodes(type, exe, clamp_limit(limit, default=100))


@op("re.export", mcp=False,
    summary="Export symbols for other tools: Ghidra script (ApplySaSymbols.py), x32dbg database (.dd32) or JSON, "
            "into work/re/export.",
    summary_ru="Экспорт символов: скрипт Ghidra, база x32dbg или JSON (в work/re/export)",
    examples=("satk re export json", "satk re export ghidra", "satk re export x32dbg"))
def re_export(format: Literal["ghidra", "x32dbg", "json"], out: str | None = None) -> dict:  # noqa: A002
    """Write export files.

    Args:
        format: ghidra | x32dbg | json.
        out: output directory (default work/re/export).
    """
    from .db import open_db
    from .export import export

    res = export(open_db(), format, out)
    return obj(None, format=format, files=res["files"], counts=res["counts"])


# --------------------------------------------------------------------------- status / doctor


@status_provider("re")
def _status(deep: bool) -> dict:
    from .db import db_path, open_db

    try:
        db = open_db(required=False)
    except SatkError as e:
        return {"built": False, "error": e.msg, "hint": "satk re build"}
    if db is None:
        return {"built": False, "path": db_path().as_posix(), "hint": "satk re build"}
    st = db.stats
    out = {"built": True, "funcs": st.get("named_starts"), "starts": st.get("starts_total"),
           "globals": st.get("global"), "vtables": st.get("vtable"),
           "patches": {o: v.get("total") for o, v in (st.get("patch") or {}).items()},
           "built_at": db.meta.get("built_at"), "path": db.path.as_posix()}
    if deep:
        out["stale"] = _stale(db)
    return out


def _stale(db) -> list[str]:
    """Sources whose revision changed since the build (deep status only)."""
    from pathlib import Path

    from ..core.paths import cfg
    from .gitsrc import GitTree

    refs = {"gta-reversed": "origin/master", "plugin-sdk": "HEAD", "mta-upstream": "refs/remotes/upstream/master",
            "neon": "HEAD", "mta-trunk": "HEAD"}
    out = []
    for s in db.sources():
        if s["kind"] == "exe" or s["kind"] not in refs:
            continue
        repo = Path(s["repo"])
        if not repo.is_absolute():
            repo = cfg().paths.workspace / repo
        try:
            cur = GitTree(repo, refs[s["kind"]]).rev
        except SatkError:
            continue
        if cur != s["rev"]:
            out.append(f"{s['kind']}: {s['rev'][:9]} -> {cur[:9]}")
    return out


@doctor_check("re_symdb")
def _doctor() -> dict:
    from .db import db_path, open_db

    try:
        db = open_db(required=False)
    except SatkError as e:
        return {"status": "warn", "msg": f"symbol DB unusable: {e.msg}", "fix": "satk re build"}
    if db is None:
        return {"status": "warn", "msg": f"symbol DB not built ({db_path().as_posix()})", "fix": "satk re build"}
    st = db.stats
    return {"status": "ok", "msg": f"symdb: {st.get('named_starts')} named functions, {st.get('global')} globals, "
                                   f"built {db.meta.get('built_at')}", "fix": None}
