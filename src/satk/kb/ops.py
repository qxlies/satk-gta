"""Operations of satk.kb — knowledge base about GTA:SA internals (owner M2-02).

CLI: ``satk kb build|search|sym|struct|opcode|fact``. All operations are ``mcp=False``; MCP
clients reach them through the generic operation tool. Also registers the ``kb`` status section
and the ``kb`` doctor check. Module-level imports are stdlib-only.
"""

from __future__ import annotations

from typing import Literal

from ..core.envelope import clamp_limit, obj
from ..core.errors import SatkError
from ..core.registry import doctor_check, op, report_progress, status_provider

__all__ = ["kb_build", "kb_search", "kb_sym", "kb_struct", "kb_opcode", "kb_fact"]

Source = Literal["gta-reversed", "plugin-sdk", "mta-upstream", "mta-neon", "cleo-ai", "research", "facts"]


@op("kb.build", group="re", mcp=False, long_running=True,
    summary="Rebuild the knowledge base work/kb/kb.sqlite from gta-reversed, plugin-sdk, MTA upstream and Neon, the "
            "cleo-ai opcode reference, research reports and curated facts (read-only git, no network, ~30-60 s).",
    summary_ru="Пересобрать базу знаний work/kb/kb.sqlite из gta-reversed, plugin-sdk, MTA (upstream и Neon), "
               "справочника опкодов cleo-ai, отчётов и проверенных фактов",
    examples=("satk kb build",))
def kb_build(research: bool = True) -> dict:
    """Build the KB (replaces the old file atomically; sources stay untouched).

    Args:
        research: also index the workspace research reports (docs/research/*.md).
    """
    from .build import build_kb, default_inputs
    from .query import kb_path

    inp = default_inputs(research=research)
    out = kb_path()
    st = build_kb(out, inp, progress=lambda name: report_progress(0, None, name))
    structs = {k: v.get("structs") for k, v in st.items() if isinstance(v, dict) and "structs" in v}
    env = obj(None, path=out, seconds=st.get("seconds"), bytes=st.get("bytes"), counts=st.get("counts"),
              facts={k: v for k, v in (st.get("facts") or {}).items() if k != "facts"},
              layouts={k: f"{v['ok']} ok / {v['mismatch']} mismatch / {v['unverified']} unverified / {v['partial']} partial"
                       for k, v in structs.items() if v},
              skipped=st.get("skipped"))
    if st.get("skipped"):
        env.setdefault("warn", []).extend(f"SKIPPED: {k}: {v}" for k, v in sorted(st["skipped"].items()))
    if st.get("scan_errors_total"):
        env.setdefault("warn", []).append(f"SCAN: {st['scan_errors_total']} file(s) indexed as text only, "
                                          f"first: {st['scan_errors'][0]}")
    if (st.get("facts") or {}).get("mismatch"):
        env.setdefault("warn", []).append("FACTS: some curated facts no longer match: satk kb fact --status mismatch")
    return env


@op("kb.search", group="re", mcp=False,
    summary="Full-text search of the knowledge base: symbols (functions with signature and file:line, globals, "
            "structs, constants, MTA defines), facts, opcodes and source/doc lines. An address (0x5B8E64) lists "
            "every symbol and source line that mentions it.",
    summary_ru="Полнотекстовый поиск по базе знаний: символы с file:line, факты, опкоды, строки исходников; "
               "адрес 0x... — все упоминания",
    examples=('satk kb search "CStreaming RequestModel"', "satk kb search 0x5B8E64",
              'satk kb search "streaming memory" --kind code --source mta-upstream'))
def kb_search(query: str, source: Source | None = None,
              kind: Literal["sym", "code", "opcode", "fact"] | None = None,
              limit: int = 20, cursor: str | None = None) -> dict:
    """Search (words must all match, each as a prefix; camelCase splits into words).

    Args:
        query: words, a qualified name or a gta_sa.exe address.
        source: restrict to one source.
        kind: sym | code | opcode | fact.
        limit: rows per page.
        cursor: page cursor from 'next'.
    """
    from .query import search

    return search(query, source=source, kind=kind, limit=clamp_limit(limit), cursor=cursor)


@op("kb.sym", group="re", mcp=False,
    summary="Look up symbols by name or address across sources: exact, then ::member, prefix, substring. "
            "Kinds: func, global, struct, vtable, limit, const, enum, define, hookpos.",
    summary_ru="Символы по имени или адресу во всех источниках (точное, ::член, префикс, подстрока)",
    examples=("satk kb sym CStreaming::RequestModel", "satk kb sym MAX_BUILDINGS --kind const", "satk kb sym 0x8A5A80"))
def kb_sym(name: str, kind: Literal["func", "global", "struct", "vtable", "limit", "const", "enum", "define",
                                    "hookpos"] | None = None,
           source: Source | None = None, limit: int = 20, cursor: str | None = None) -> dict:
    """Symbol lookup.

    Args:
        name: name (case-insensitive) or address 0x....
        kind: restrict to one kind.
        source: restrict to one source.
        limit: rows per page.
        cursor: page cursor from 'next'.
    """
    from .query import sym

    return sym(name, kind=kind, source=source, limit=clamp_limit(limit), cursor=cursor)


@op("kb.struct", group="re", mcp=False,
    summary="Class/struct layout: asserted and computed size, bases, fields with offsets (from VALIDATE_OFFSET or "
            "computed for 32-bit MSVC). --at 0x540 names the field at an offset (bases and nested structs included).",
    summary_ru="Раскладка класса: размер, базы, поля со смещениями; --at 0x540 — какое поле по смещению",
    examples=("satk kb struct CPed", "satk kb struct CPed --at 0x540", "satk kb struct CVehicle --match health",
              "satk kb struct CPedSAInterface --source mta-upstream"))
def kb_struct(name: str, source: Source | None = None, at: str | None = None, match: str | None = None,
              inherited: bool = False, bits: bool = False, limit: int = 100) -> dict:
    """Struct layout.

    Args:
        name: class name (CPed; MTA names *SAInterface are tried too).
        source: which source's definition (default: gta-reversed, then plugin-sdk, MTA).
        at: byte offset (0x540) -> the field(s) covering it.
        match: only fields whose name contains this text.
        inherited: include the fields of base classes.
        bits: one row per bit-field instead of one row per storage unit.
        limit: max field rows.
    """
    from .query import struct

    return struct(name, source=source, at=at, match=match, inherited=inherited, bits=bits,
                  limit=clamp_limit(limit, default=100))


@op("kb.opcode", group="re", mcp=False,
    summary="SCM/CLEO opcode reference (cleo-ai / Sanny Builder Library): by id (0A8C), name (WRITE_MEMORY) or words; "
            "parameters, description and the gta-reversed handler.",
    summary_ru="Справочник опкодов SCM/CLEO: по номеру, имени или словам; параметры, описание, обработчик в gta-reversed",
    examples=("satk kb opcode 0A8C", "satk kb opcode SET_CHAR_HEALTH", 'satk kb opcode "car coordinates"'))
def kb_opcode(query: str, ext: str | None = None, limit: int = 20) -> dict:
    """Opcode lookup.

    Args:
        query: opcode id, command name or words.
        ext: extension (default, CLEO, CLEO+, SAMPFUNCS, ...).
        limit: rows when several match.
    """
    from .query import opcode

    return opcode(query, ext=ext, limit=clamp_limit(limit))


@op("kb.fact", group="re", mcp=False,
    summary="Curated engine facts (pools, limits, streaming, world, scripts, sizes, entry points) with confidence and "
            "automatic checks against the KB and gta_sa.exe bytes. A key gives one fact, a topic or words a table.",
    summary_ru="Проверенные факты о движке (пулы, лимиты, стриминг, мир, скрипты, размеры) с уровнем доверия "
               "и автоматическими проверками",
    examples=("satk kb fact", "satk kb fact streaming.memory", "satk kb fact pools"))
def kb_fact(key: str | None, status: Literal["verified", "mismatch", "unchecked"] | None = None,
            limit: int = 50) -> dict:
    """Facts.

    Args:
        key: fact key (streaming.memory), topic (pools) or words; empty lists all facts.
        status: only facts with this check status.
        limit: rows of a list.
    """
    from .query import fact

    return fact(key, status_filter=status, limit=clamp_limit(limit, default=50))


# --------------------------------------------------------------------------- status / doctor


@status_provider("kb")
def _status(deep: bool) -> dict:
    from .query import status

    try:
        return status(deep=deep)
    except SatkError as e:
        return {"built": False, "error": e.msg, "hint": "satk kb build"}


@doctor_check("kb")
def _doctor() -> dict:
    from .query import status

    try:
        st = status()
    except SatkError as e:
        return {"status": "warn", "msg": f"knowledge base unusable: {e.msg}", "fix": "satk kb build"}
    if not st.get("built"):
        return {"status": "warn", "msg": f"knowledge base not built ({st.get('path')})", "fix": "satk kb build"}
    c = st.get("counts") or {}
    f = st.get("facts") or {}
    msg = f"kb: {c.get('sym')} symbols, {c.get('struct')} structs, {c.get('opcode')} opcodes, " \
          f"facts {f.get('verified')} verified/{f.get('mismatch')} mismatch, built {st.get('built_at')}"
    return {"status": "warn" if f.get("mismatch") else "ok", "msg": msg,
            "fix": "satk kb fact --status mismatch" if f.get("mismatch") else None}
