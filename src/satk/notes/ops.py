"""Operations of satk.notes (owner WP-06): the MCP tool ``note`` and ``satk note add|list|rm|export|import``.

Also registers the SID provider for ``note`` (``asset_find(kind="note")``) and the status
section ``notes``. Module-level imports are stdlib/satk only.
"""

from __future__ import annotations

from typing import Literal

from satk.core.envelope import clamp_limit, obj, table
from satk.core.errors import SatkError
from satk.core.ids import register_provider
from satk.core.paths import jpath
from satk.core.registry import op, status_provider

from . import db
from .provider import NoteProvider, next_cursor, parse_cursor

_PROVIDER = NoteProvider()
try:
    register_provider(_PROVIDER)
except ValueError:  # pragma: no cover - another package already serves 'note'
    pass

_LIST_COLS = ["id", "sid", "text", "tags", "author", "date"]


def _rows(notes: list[db.Note]) -> list[list]:
    return [[n.note_sid, n.sid, n.text, n.tags or "", n.author, n.created_at[:10]] for n in notes]


def _list(sid: str | None, q: str | None, author: str | None, tag: str | None, limit: int,
          cursor: str | None) -> dict:
    lim = clamp_limit(limit)
    off = parse_cursor(cursor)
    notes, total = db.list_notes(sid, q=q, author=author, tag=tag, limit=lim, offset=off)
    return table(_LIST_COLS, _rows(notes), total=total, next=next_cursor(off, len(notes), total))


def _add(sid: str, text: str, tags: list[str] | None, lang: str, author: str | None = None,
         confidence: float | None = None, evidence: str | None = None) -> dict:
    n, created = db.add_note(sid, text, tags=tags, lang=lang, author=author, confidence=confidence,
                             evidence=evidence)
    out = obj(note=n.note_sid, sid=n.sid, author=n.author, tags=n.tag_list)
    if not created:
        out["warn"] = [f"EXISTS: the same note already stored as {n.note_sid}"]
    return out


@op("note",
    summary="Persistent notes on any SID, kept across sessions. add: id, text, tags. list: id and/or query (full "
            "text, word prefixes, Cyrillic ok); also asset_find(query, kind=\"note\").",
    summary_ru="Заметки к любому SID (сохраняются между сессиями): add — добавить, list — по SID или тексту.",
    mcp="note", group="note",
    examples=('satk note --action add --id model:411 --text "Red sports car, seen near Grove St" --tags car',
              "satk note --action list --query машин"))
def note(action: Literal["add", "list"] = "add", id: str | None = None,  # noqa: A002 - MCP name from SPEC §4.7
         text: str | None = None, tags: list[str] | None = None, lang: str = "ru", query: str | None = None,
         limit: int = 20, cursor: str | None = None) -> dict:
    """Add or list notes.

    Args:
        id: SID (add: target; list: filter).
        query: full text (list).
    """
    if action == "add":
        missing = [k for k, v in (("id", id), ("text", text)) if not v]
        if missing:
            raise SatkError("BAD_PARAMS", f"note add needs {' and '.join(missing)}",
                            hint='note(action="add", id="model:411", text="...")')
        return _add(id, text, tags, lang)  # type: ignore[arg-type]
    return _list(id, query, None, None, limit, cursor)


@op("note.add", summary="Add a note to a SID (stored in work/notes.sqlite; exported by 'satk note export').",
    summary_ru="Добавить заметку к SID (work/notes.sqlite; экспорт — satk note export).", mcp=False,
    examples=('satk note add model:411 "Red sports car, seen near Grove St" --tags car',))
def note_add(sid: str, text: str, tags: list[str] | None = None, lang: str = "ru", author: str | None = None,
             confidence: float | None = None, evidence: str | None = None) -> dict:
    """Add a note.

    Args:
        sid: the object the note is about (any SID: model:411, tex:txd/name, fn:0x53bf09 ...).
        text: note text.
        tags: tags (space/comma separated).
        lang: language code of the text.
        author: 'claude' (default, or SATK_AUTHOR) or 'human:<name>'.
        confidence: 0..1, how sure the author is.
        evidence: capture id, png sha256 or file:line backing the note.
    """
    return _add(sid, text, tags, lang, author, confidence, evidence)


@op("note.list", summary="List notes, optionally for one SID or matching a full-text query (FTS5, Cyrillic ok).",
    summary_ru="Список заметок: все, по SID или по полнотекстовому запросу (кириллица работает).", mcp=False,
    examples=("satk note list", "satk note list model:411", "satk note list --query машин"))
def note_list(sid: str | None, query: str | None = None, author: str | None = None, tag: str | None = None,
              limit: int = 20, cursor: str | None = None) -> dict:
    """List notes.

    Args:
        sid: only notes about this SID.
        query: full-text query over text and tags (word prefixes).
        author: only this author.
        tag: only notes with this tag.
        limit: page size.
        cursor: 'next' value of the previous page.
    """
    return _list(sid, query, author, tag, limit, cursor)


@op("note.rm", summary="Delete a note by its id (note:N).", summary_ru="Удалить заметку по id (note:N).", mcp=False,
    examples=("satk note rm note:17",))
def note_rm(id: str) -> dict:  # noqa: A002
    """Delete a note.

    Args:
        id: note SID (note:17) or number.
    """
    n = db.remove_note(id)
    if n is None:
        raise SatkError("NOT_FOUND", f"no note {id!r}", hint="satk note list")
    return obj(removed=n.note_sid, sid=n.sid, text=n.text)


@op("note.export",
    summary="Export all notes to data/notes/notes.jsonl of this checkout (sorted; bookmarks to bookmarks.jsonl).",
    summary_ru="Экспорт заметок в data/notes/notes.jsonl (сортировано; закладки — в bookmarks.jsonl).", mcp=False,
    examples=("satk note export",))
def note_export(out: str | None = None) -> dict:
    """Export notes.

    Args:
        out: target .jsonl file (default: <repo>/data/notes/notes.jsonl).
    """
    return obj(**db.export_jsonl(out))


@op("note.import", summary="Import notes from data/notes/notes.jsonl (no duplicates; existing notes are kept).",
    summary_ru="Импорт заметок из data/notes/notes.jsonl (без дублей; существующие сохраняются).", mcp=False,
    examples=("satk note import",))
def note_import(file: str | None = None) -> dict:
    """Import notes.

    Args:
        file: source .jsonl file (default: <repo>/data/notes/notes.jsonl).
    """
    return obj(**db.import_jsonl(file))


@status_provider("notes")
def _status(deep: bool) -> dict:
    c = db.counts()
    f = db.db_file()
    if c is None:
        return {"db": jpath(f), "exists": False}
    return {"db": jpath(f), **c}
