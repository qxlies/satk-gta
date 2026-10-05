"""Operations of satk.describe (M2-13): ``satk describe import|status|clear``.

All CLI-only (``mcp=False``); agents reach them through the generic MCP tool. The imported text
is found with the existing tools: ``asset_find(query, kind="note")`` and ``note list model:<id>``.
Module-level imports are stdlib/satk only.
"""

from __future__ import annotations

import time
from typing import Literal

from satk.core.envelope import obj
from satk.core.errors import SatkError
from satk.core.paths import cfg, jpath
from satk.core.registry import op

_NOTICE = "data/notices/gta-scout.txt"


def _sample(recs, n: int) -> list[list]:
    return [[r.sid, r.text if len(r.text) <= 80 else r.text[:79] + "…"] for r in recs[: max(0, n)]]


@op("describe.import",
    summary="Import gta-scout model descriptions (MIT) into notes: an entry is bound to a model only when the "
            "profile's DFF bytes have the published SHA-256. Author import:gta-scout, tag desc; re-import "
            "changes nothing. Then asset_find(query, kind=\"note\").",
    summary_ru="Импорт описаний моделей gta-scout (MIT) в заметки: привязка только по SHA-256 DFF профиля, "
               "автор import:gta-scout, тег desc; повторный импорт ничего не меняет.",
    mcp=False, group="note",
    examples=("satk describe import --dry-run", "satk describe import",
              "satk asset find bench --kind note", "satk note list --tag desc --limit 5"))
def describe_import(pack: str | None = None, profile: str | None = None, text: Literal["en", "full"] = "en",
                    prune: bool = False, dry_run: bool = False, verify: bool = True, sample: int = 3) -> dict:
    """Bind the pack to the profile's DFFs and sync the descriptions into work/notes.sqlite.

    Args:
        pack: pack file or directory (default: newest sa-*.json in <paths.src>/gta-scout/data/annotations).
        profile: load profile whose DFFs are hashed (default: [index] default_profile).
        text: en = English part of FR/EN texts; full = the publisher text as is.
        prune: also remove imported notes of models that are no longer bound.
        dry_run: only count; write nothing.
        verify: check the pack digest (--no-verify for a locally edited pack).
        sample: how many bound notes to show.
    """
    from satk.index.api import open_index
    from satk.notes import db as notes

    from .bind import bind
    from .pack import find_pack, load_pack
    from .store import records, sync

    t0 = time.perf_counter()
    prof = profile or cfg().default_profile
    p = load_pack(find_pack(pack), verify=verify)
    db = open_index(prof)
    b = bind(p, db)
    recs = records(b.descs, p, text)
    res = sync(recs, prune=prune, dry_run=dry_run)
    warn = list(p.warn) + list(b.warn) + list(res.pop("warn", []))
    try:
        warn += list(db.stale_warnings())
    except (AttributeError, SatkError, OSError):  # pragma: no cover - fake/odd index
        pass
    if not recs:
        warn.append(f"NOTHING_BOUND: no DFF of profile {prof!r} has a published hash (modded DFFs, or another "
                    "game version?)")
    return obj(profile=getattr(db, "profile", prof), pack=p.summary(), notes_db=jpath(notes.db_file()),
               **b.stats, notes=len(recs), **res, dry_run=True if dry_run else None,
               sample=_sample(recs, sample), seconds=round(time.perf_counter() - t0, 2),
               license=f"MIT (Dryxio), notice {_NOTICE}", warn=warn)


@op("describe.status",
    summary="gta-scout description pack (file, version, entries, digest) and the number of imported "
            "description notes.",
    summary_ru="Пакет описаний gta-scout (файл, версия, записи, контрольная сумма) и сколько описаний уже "
               "импортировано в заметки.",
    mcp=False, group="note",
    examples=("satk describe status",))
def describe_status(pack: str | None = None) -> dict:
    """Show the pack and the imported notes.

    Args:
        pack: pack file or directory (default: newest sa-*.json in <paths.src>/gta-scout/data/annotations).
    """
    from satk.notes import db as notes

    from .pack import find_pack, load_pack
    from .store import imported

    warn: list[str] = []
    summary = None
    try:
        p = load_pack(find_pack(pack))
        summary = p.summary()
        warn += p.warn
    except SatkError as e:
        warn.append(f"{e.code}: {e.msg}" + (f" ({e.hint})" if e.hint else ""))
    have = imported()
    return obj(pack=summary, imported=len(have), models=len({n.sid for n in have}),
               notes_db=jpath(notes.db_file()), license=f"MIT (Dryxio), notice {_NOTICE}", warn=warn)


def _cli_only(reason: str, *, consent: bool = False):
    """``satk.mcp.generic.cli_only`` when the generic MCP access (M2-01) is present, else a no-op."""
    try:
        from satk.mcp.generic import cli_only
    except ImportError:  # pragma: no cover - older checkout without satk_op
        return lambda fn: fn
    return cli_only(reason, consent=consent)


@_cli_only("it deletes every imported description note", consent=True)
@op("describe.clear",
    summary="Remove every imported gta-scout description note (author import:gta-scout, tag desc); other "
            "notes stay.",
    summary_ru="Удалить все импортированные описания gta-scout (автор import:gta-scout, тег desc); "
               "остальные заметки не трогаются.",
    mcp=False, group="note",
    examples=("satk describe clear --dry-run",))
def describe_clear(dry_run: bool = False) -> dict:
    """Remove imported descriptions.

    Args:
        dry_run: only count.
    """
    from satk.notes import db as notes

    from .store import clear

    return obj(**clear(dry_run=dry_run), notes_db=jpath(notes.db_file()), dry_run=True if dry_run else None)
