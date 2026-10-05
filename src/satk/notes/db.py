r"""``work/notes.sqlite``: notes, bookmarks and captures (SPEC §4.5). stdlib only.

The database is persistent knowledge: ``satk index build`` never touches it. Notes are exported
to ``<repo>/data/notes/notes.jsonl`` (our own text, committed) and imported back with
``satk note import``; bookmarks go to ``bookmarks.jsonl`` next to it. Captures are local.

Public API (used by ``satk.notes.ops``, the ``note`` SID provider and, for bookmarks and
captures, by ``satk.viewer`` / WP-07)::

    from satk.notes import db
    n, created = db.add_note("model:411", "Red sports car, seen near Grove St", tags=["car"])
    notes, total = db.list_notes(q="машин")            # FTS5 unicode61: Cyrillic prefixes work
    db.notes_for("model:411"); db.count_for("model:411")
    db.bookmark_save("grove_center", {"pos": [2495, -1687, 30], "look": [2495, -1670, 13]})
    db.capture_add("<workspace>/work/out/captures/x.png", "ariane", pose={...}, w=960, h=540)

Every function opens its own short-lived connection (safe from any thread or process).
Readers never create the file: on a fresh workspace they see an empty database.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.request import pathname2url

from satk.core.errors import SatkError
from satk.core.ids import Sid
from satk.core.paths import atomic_write, cfg, ensure_writable, jpath

__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_FILE",
    "MAX_TEXT",
    "Note",
    "db_file",
    "connect",
    "schema_sql",
    "add_note",
    "get_note",
    "list_notes",
    "notes_for",
    "count_for",
    "remove_note",
    "fts_query",
    "norm_tags",
    "default_author",
    "export_jsonl",
    "import_jsonl",
    "default_export_path",
    "counts",
    "bookmark_save",
    "bookmark_get",
    "bookmark_list",
    "bookmark_rm",
    "new_capture_id",
    "capture_add",
    "capture_get",
    "capture_list",
]

SCHEMA_VERSION = 1
SCHEMA_FILE = Path(__file__).with_name("schema.sql")
MAX_TEXT = 4000
_MAX_EVIDENCE = 500
_LANG = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})?$")
_AUTHOR = re.compile(r"^[^\x00-\x1f]{1,64}$")
_BM_NAME = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_TOKEN = re.compile(r"\w+", re.UNICODE)
_NOTE_COLS = ("id", "sid", "author", "lang", "text", "tags", "confidence", "evidence", "created_at")


# --------------------------------------------------------------------------- connection


def db_file() -> Path:
    """``<work>/notes.sqlite`` (not created)."""
    return Path(os.path.abspath(cfg().paths.work)) / "notes.sqlite"


def schema_sql() -> str:
    """The DDL of ``notes.sqlite`` (``schema.sql`` next to this module)."""
    return SCHEMA_FILE.read_text(encoding="utf-8")


def _user_version(con: sqlite3.Connection) -> int:
    return int(con.execute("PRAGMA user_version").fetchone()[0])


def _ensure_schema(con: sqlite3.Connection, path: Path) -> None:
    v = _user_version(con)
    if v == SCHEMA_VERSION:
        return
    if v > SCHEMA_VERSION:
        raise SatkError("UNSUPPORTED", f"{jpath(path)} has schema v{v}, this satk knows v{SCHEMA_VERSION}",
                        hint="update the satk checkout (git pull) or use the matching version")
    has_note = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='note'").fetchone()
    if has_note:  # pragma: no cover - only for a hand-made file without user_version
        raise SatkError("UNSUPPORTED", f"{jpath(path)}: unversioned notes database", hint="move the file away")
    try:
        con.executescript(schema_sql())
    except sqlite3.OperationalError as e:  # another process created it concurrently
        if "already exists" not in str(e) or _user_version(con) != SCHEMA_VERSION:
            raise


def connect(path: str | os.PathLike | None = None, *, create: bool = True) -> sqlite3.Connection | None:
    """Open ``notes.sqlite`` (schema applied on first use).

    With ``create=False`` a missing file gives ``None`` instead of creating it; existing
    files are opened read-only and must already have the supported schema.
    The caller closes the connection (``contextlib.closing``).
    """
    p = Path(path) if path is not None else db_file()
    if create:
        p = ensure_writable(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(p, timeout=10)
    else:
        if not p.exists():
            return None
        # pathname2url also handles extended drive/UNC names, escaping #, % and Unicode.
        uri = "file:" + pathname2url(str(p.absolute())) + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout = 10000")
        if create:
            _ensure_schema(con, p)
            if con.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                con.execute("PRAGMA journal_mode = WAL")
        else:
            version = _user_version(con)
            if version != SCHEMA_VERSION:
                raise SatkError("UNSUPPORTED", f"{jpath(p)} has schema v{version}, expected v{SCHEMA_VERSION}",
                                hint="use a notes database initialized by the matching satk version")
    except BaseException:
        con.close()
        raise
    return con


@contextmanager
def _session(write: bool, path: str | os.PathLike | None = None) -> Iterator[sqlite3.Connection | None]:
    con = connect(path, create=write)
    if con is None:
        yield None
        return
    with closing(con):
        if write:
            with con:  # transaction: commit on success, rollback on error
                yield con
        else:
            yield con


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- notes


@dataclass(frozen=True)
class Note:
    """One note row."""

    id: int
    sid: str
    author: str
    lang: str
    text: str
    tags: str | None
    confidence: float | None
    evidence: str | None
    created_at: str

    @property
    def note_sid(self) -> str:
        """``note:<id>``."""
        return f"note:{self.id}"

    @property
    def tag_list(self) -> list[str]:
        return self.tags.split() if self.tags else []

    def as_dict(self) -> dict[str, Any]:
        """JSON object (``id`` is the note SID, ``sid`` the annotated object); empty fields omitted."""
        d: dict[str, Any] = {"id": self.note_sid, "sid": self.sid, "author": self.author, "lang": self.lang,
                             "text": self.text, "tags": self.tag_list, "confidence": self.confidence,
                             "evidence": self.evidence, "created_at": self.created_at}
        return {k: v for k, v in d.items() if v not in (None, [], "")}

    def export_record(self) -> dict[str, Any]:
        """Line of ``notes.jsonl`` (no local id)."""
        d: dict[str, Any] = {"sid": self.sid, "author": self.author, "lang": self.lang, "text": self.text,
                             "tags": self.tags, "confidence": self.confidence, "evidence": self.evidence,
                             "created_at": self.created_at}
        return {k: v for k, v in d.items() if v is not None}


def _note(row: sqlite3.Row) -> Note:
    return Note(*(row[c] for c in _NOTE_COLS))


def default_author() -> str:
    """``SATK_AUTHOR`` or ``"claude"`` (satk is agent-first; humans pass ``--author human:<name>``)."""
    return (os.environ.get("SATK_AUTHOR") or "claude").strip() or "claude"


def norm_tags(tags: Iterable[str] | str | None) -> str | None:
    """Tags as a space-separated string: split on spaces/commas, ``#`` stripped, duplicates dropped."""
    if tags is None:
        return None
    if isinstance(tags, str):
        tags = [tags]
    out: list[str] = []
    for t in tags:
        for part in re.split(r"[\s,]+", str(t)):
            p = part.strip().lstrip("#")
            if p and p not in out:
                out.append(p)
    return " ".join(out) or None


def _canon_sid(sid: str | Sid) -> str:
    return str(Sid.parse(sid))


def _check_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise SatkError("BAD_PARAMS", "note text is empty")
    t = text.strip()
    if len(t) > MAX_TEXT:
        raise SatkError("BAD_PARAMS", f"note text is {len(t)} chars, max {MAX_TEXT}",
                        hint="split it into several notes or point to a file in 'evidence'")
    return t


def _check_meta(author: str, lang: str, confidence: float | None, evidence: str | None) -> tuple[str, str]:
    a = str(author).strip()
    if not _AUTHOR.match(a):
        raise SatkError("BAD_PARAMS", f"bad author {author!r} (e.g. 'claude', 'human:<name>')")
    lg = str(lang).strip().lower()
    if not _LANG.match(lg):
        raise SatkError("BAD_PARAMS", f"bad language code {lang!r} (e.g. 'ru', 'en')")
    if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
        raise SatkError("BAD_PARAMS", f"confidence must be in 0..1, got {confidence}")
    if evidence is not None and len(evidence) > _MAX_EVIDENCE:
        raise SatkError("BAD_PARAMS", f"evidence is longer than {_MAX_EVIDENCE} chars")
    return a, lg


def add_note(sid: str | Sid, text: str, *, author: str | None = None, lang: str = "ru",
             tags: Iterable[str] | str | None = None, confidence: float | None = None,
             evidence: str | None = None, created_at: str | None = None,
             path: str | os.PathLike | None = None) -> tuple[Note, bool]:
    """Add a note about ``sid``; returns ``(note, created)``.

    The same (sid, author, text) is stored once: a repeated add returns the existing note
    with ``created=False``.
    """
    s = _canon_sid(sid)
    t = _check_text(text)
    a, lg = _check_meta(author or default_author(), lang, confidence, evidence)
    tg = norm_tags(tags)
    with _session(True, path) as con:
        assert con is not None
        cur = con.execute(
            "INSERT OR IGNORE INTO note(sid, author, lang, text, tags, confidence, evidence, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (s, a, lg, t, tg, None if confidence is None else float(confidence), evidence, created_at or _now()))
        created = cur.rowcount == 1
        row = con.execute("SELECT * FROM note WHERE sid=? AND author=? AND text=?", (s, a, t)).fetchone()
    return _note(row), created


def get_note(note_id: int | str, *, path: str | os.PathLike | None = None) -> Note | None:
    """Note by integer id or ``note:<id>``; ``None`` if absent."""
    nid = _note_id(note_id)
    with _session(False, path) as con:
        if con is None:
            return None
        row = con.execute("SELECT * FROM note WHERE id=?", (nid,)).fetchone()
    return _note(row) if row else None


def _note_id(note_id: int | str) -> int:
    if isinstance(note_id, int) and not isinstance(note_id, bool):
        return note_id
    s = str(note_id).strip()
    if s.isdigit():
        return int(s)
    sid = Sid.parse(s)
    if sid.kind != "note" or sid.num is None:
        raise SatkError("BAD_ID", f"expected note:<id>, got {s!r}", hint="satk note list")
    return sid.num


def fts_query(q: str) -> str:
    """FTS5 expression for free text: every word becomes a quoted prefix term (AND).

    ``"машин"`` -> ``"машин"*`` (finds «машина»); punctuation is ignored, so user input
    can never inject FTS syntax.
    """
    toks = _TOKEN.findall(q or "")
    if not toks:
        raise SatkError("BAD_PARAMS", f"nothing to search for in {q!r}", hint="give at least one word")
    return " ".join('"' + t.replace('"', "") + '"*' for t in toks)


def _sid_or_none(q: str) -> str | None:
    if ":" not in q or " " in q.strip():
        return None
    try:
        return str(Sid.parse(q))
    except SatkError:
        return None


def list_notes(sid: str | Sid | None = None, *, q: str | None = None, author: str | None = None,
               tag: str | None = None, limit: int = 20, offset: int = 0,
               path: str | os.PathLike | None = None) -> tuple[list[Note], int]:
    """Notes filtered by SID, free text (FTS5, ranked), author and tag; returns ``(page, total)``.

    A ``q`` that is itself a SID (``model:411``) filters by that SID instead of searching text.
    """
    where: list[str] = []
    params: list[Any] = []
    if q is not None and q.strip():
        as_sid = _sid_or_none(q)
        if as_sid is not None:
            sid = sid or as_sid
            q = None
    if sid is not None:
        where.append("n.sid = ?")
        params.append(_canon_sid(sid))
    if author:
        where.append("n.author = ?")
        params.append(author.strip())
    if tag:
        where.append("(' ' || COALESCE(n.tags, '') || ' ') LIKE ?")
        params.append(f"% {tag.strip().lstrip('#')} %")
    with _session(False, path) as con:
        if con is None:
            return [], 0
        if q is not None and q.strip():
            match = fts_query(q)
            cond = " AND ".join(["note_fts MATCH ?"] + where)
            base = f"FROM note_fts JOIN note n ON n.id = note_fts.rowid WHERE {cond}"
            args = [match] + params
            total = con.execute(f"SELECT count(*) {base}", args).fetchone()[0]
            rows = con.execute(f"SELECT n.* {base} ORDER BY note_fts.rank, n.id LIMIT ? OFFSET ?",
                               args + [int(limit), int(offset)]).fetchall()
        else:
            cond = (" WHERE " + " AND ".join(where)) if where else ""
            total = con.execute(f"SELECT count(*) FROM note n{cond}", params).fetchone()[0]
            rows = con.execute(f"SELECT n.* FROM note n{cond} ORDER BY n.id LIMIT ? OFFSET ?",
                               params + [int(limit), int(offset)]).fetchall()
    return [_note(r) for r in rows], int(total)


def notes_for(sid: str | Sid, *, limit: int = 50, path: str | os.PathLike | None = None) -> list[Note]:
    """Notes attached to ``sid`` (oldest first). For providers that show notes in ``asset_get``."""
    notes, _ = list_notes(sid, limit=limit, path=path)
    return notes


def count_for(sid: str | Sid, *, path: str | os.PathLike | None = None) -> int:
    """Number of notes attached to ``sid`` (for ``asset_refs`` rel counts)."""
    with _session(False, path) as con:
        if con is None:
            return 0
        return int(con.execute("SELECT count(*) FROM note WHERE sid=?", (_canon_sid(sid),)).fetchone()[0])


def remove_note(note_id: int | str, *, path: str | os.PathLike | None = None) -> Note | None:
    """Delete a note; returns the removed note or ``None``."""
    nid = _note_id(note_id)
    with _session(True, path) as con:
        assert con is not None
        row = con.execute("SELECT * FROM note WHERE id=?", (nid,)).fetchone()
        if row is None:
            return None
        con.execute("DELETE FROM note WHERE id=?", (nid,))
    return _note(row)


def counts(path: str | os.PathLike | None = None) -> dict[str, int] | None:
    """``{"notes": n, "bookmarks": n, "captures": n}`` or ``None`` when the database does not exist."""
    with _session(False, path) as con:
        if con is None:
            return None
        return {t + "s": int(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
                for t in ("note", "bookmark", "capture")}


# --------------------------------------------------------------------------- export / import


def default_export_path() -> Path:
    """``<repo>/data/notes/notes.jsonl`` of the checkout this code runs from."""
    from satk.core.config import REPO_ROOT

    return REPO_ROOT / "data" / "notes" / "notes.jsonl"


def _jsonl(records: Iterable[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records)


def export_jsonl(out: str | os.PathLike | None = None, *, path: str | os.PathLike | None = None) -> dict:
    """Write all notes to ``out`` (sorted, deterministic) and bookmarks to ``bookmarks.jsonl`` beside it.

    ``bookmarks.jsonl`` is written when there are bookmarks or the file already exists.
    """
    target = Path(out) if out is not None else default_export_path()
    with _session(False, path) as con:
        notes = [_note(r) for r in con.execute("SELECT * FROM note").fetchall()] if con else []
        bms = [dict(r) for r in con.execute("SELECT * FROM bookmark ORDER BY name").fetchall()] if con else []
    notes.sort(key=lambda n: (n.sid, n.created_at, n.author, n.text))
    atomic_write(target, _jsonl(n.export_record() for n in notes))
    res: dict[str, Any] = {"file": jpath(target), "notes": len(notes)}
    bm_file = target.with_name("bookmarks.jsonl")
    if bms or bm_file.exists():
        recs = []
        for b in bms:
            r = {"name": b["name"], "pose": json.loads(b["pose"]), "env": json.loads(b["env"]) if b["env"] else None,
                 "note": b["note"], "created_at": b["created_at"]}
            recs.append({k: v for k, v in r.items() if v is not None})
        atomic_write(bm_file, _jsonl(recs))
        res["bookmarks_file"] = jpath(bm_file)
        res["bookmarks"] = len(bms)
    return res


def import_jsonl(src: str | os.PathLike | None = None, *, path: str | os.PathLike | None = None) -> dict:
    """Import notes (and ``bookmarks.jsonl`` beside them) without duplicates; local data wins."""
    source = Path(src) if src is not None else default_export_path()
    if not source.is_file():
        raise SatkError("NOT_FOUND", f"no such file: {jpath(source)}", hint="satk note export")
    added = skipped = 0
    bad: list[str] = []
    lines = source.read_text(encoding="utf-8").splitlines()
    with _session(True, path) as con:
        assert con is not None
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                s = _canon_sid(r["sid"])
                t = _check_text(r["text"])
                a, lg = _check_meta(r.get("author") or "import", r.get("lang") or "ru", r.get("confidence"),
                                    r.get("evidence"))
                cur = con.execute(
                    "INSERT OR IGNORE INTO note(sid, author, lang, text, tags, confidence, evidence, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (s, a, lg, t, norm_tags(r.get("tags")), r.get("confidence"), r.get("evidence"),
                     r.get("created_at") or _now()))
                if cur.rowcount == 1:
                    added += 1
                else:
                    skipped += 1
            except (ValueError, KeyError, TypeError, SatkError) as e:
                bad.append(f"{source.name}:{i}: {getattr(e, 'msg', e)}")
        bm_added = bm_skipped = 0
        bm_file = source.with_name("bookmarks.jsonl")
        if bm_file.is_file():
            for i, line in enumerate(bm_file.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    name = _bm_name(r["name"])
                    cur = con.execute(
                        "INSERT OR IGNORE INTO bookmark(name, pose, env, note, created_at) VALUES (?,?,?,?,?)",
                        (name, json.dumps(r["pose"], separators=(",", ":")),
                         json.dumps(r["env"], separators=(",", ":")) if r.get("env") is not None else None,
                         r.get("note"), r.get("created_at") or _now()))
                    if cur.rowcount == 1:
                        bm_added += 1
                    else:
                        bm_skipped += 1
                except (ValueError, KeyError, TypeError, SatkError) as e:
                    bad.append(f"{bm_file.name}:{i}: {getattr(e, 'msg', e)}")
    res: dict[str, Any] = {"file": jpath(source), "added": added, "skipped": skipped}
    if bm_file.is_file():
        res["bookmarks_added"] = bm_added
        res["bookmarks_skipped"] = bm_skipped
    if bad:
        res["warn"] = bad[:20]
    return res


# --------------------------------------------------------------------------- bookmarks


def _bm_name(name: str) -> str:
    n = str(name).strip().lower()
    if n.startswith("bm:"):
        n = n[3:]
    if not _BM_NAME.match(n):
        raise SatkError("BAD_PARAMS", f"bad bookmark name {name!r} (lower-case letters, digits, _ . -; max 64)")
    return n


def _json_col(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        try:
            json.loads(v)
            return v
        except ValueError:
            raise SatkError("BAD_PARAMS", f"not JSON: {v[:60]!r}") from None
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _bm(row: sqlite3.Row) -> dict:
    d = {"id": f"bm:{row['name']}", "name": row["name"], "pose": json.loads(row["pose"]),
         "env": json.loads(row["env"]) if row["env"] else None, "note": row["note"], "created_at": row["created_at"]}
    return {k: v for k, v in d.items() if v is not None}


def bookmark_save(name: str, pose: Any, *, env: Any = None, note: str | None = None, replace: bool = True,
                  path: str | os.PathLike | None = None) -> dict:
    """Save a named camera pose (JSON SAAP ``Pose``); ``EXISTS`` if present and ``replace=False``."""
    n = _bm_name(name)
    if pose is None:
        raise SatkError("BAD_PARAMS", "bookmark needs a pose")
    p = _json_col(pose)
    with _session(True, path) as con:
        assert con is not None
        if not replace and con.execute("SELECT 1 FROM bookmark WHERE name=?", (n,)).fetchone():
            raise SatkError("EXISTS", f"bookmark {n!r} exists", hint=f"satk view bookmark rm {n}")
        con.execute("INSERT OR REPLACE INTO bookmark(name, pose, env, note, created_at) VALUES (?,?,?,?,?)",
                    (n, p, _json_col(env), note, _now()))
        row = con.execute("SELECT * FROM bookmark WHERE name=?", (n,)).fetchone()
    return _bm(row)


def bookmark_get(name: str, *, path: str | os.PathLike | None = None) -> dict | None:
    """Bookmark ``{id, name, pose, env?, note?, created_at}`` or ``None``."""
    n = _bm_name(name)
    with _session(False, path) as con:
        if con is None:
            return None
        row = con.execute("SELECT * FROM bookmark WHERE name=?", (n,)).fetchone()
    return _bm(row) if row else None


def bookmark_list(q: str | None = None, *, limit: int = 50, offset: int = 0,
                  path: str | os.PathLike | None = None) -> tuple[list[dict], int]:
    """Bookmarks by name (``q`` = substring); returns ``(page, total)``."""
    cond, params = "", []
    if q:
        cond, params = " WHERE name LIKE ?", [f"%{q.strip().lower()}%"]
    with _session(False, path) as con:
        if con is None:
            return [], 0
        total = con.execute(f"SELECT count(*) FROM bookmark{cond}", params).fetchone()[0]
        rows = con.execute(f"SELECT * FROM bookmark{cond} ORDER BY name LIMIT ? OFFSET ?",
                           params + [int(limit), int(offset)]).fetchall()
    return [_bm(r) for r in rows], int(total)


def bookmark_rm(name: str, *, path: str | os.PathLike | None = None) -> bool:
    """Delete a bookmark; ``True`` if it existed."""
    n = _bm_name(name)
    with _session(True, path) as con:
        assert con is not None
        return con.execute("DELETE FROM bookmark WHERE name=?", (n,)).rowcount == 1


# --------------------------------------------------------------------------- captures


def new_capture_id(now: _dt.datetime | None = None) -> str:
    """``cap:YYYYMMDD-HHMMSS-xxxx`` (local time, 4 random hex digits)."""
    t = now or _dt.datetime.now()
    return f"cap:{t:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def _cap_id(cid: str) -> str:
    s = str(cid).strip().lower()
    if not s.startswith("cap:"):
        s = "cap:" + s
    return str(Sid.parse(s))


def _cap(row: sqlite3.Row) -> dict:
    d = {"id": row["id"], "path": row["path"], "target": row["target"], "pose": json.loads(row["pose"]),
         "env": json.loads(row["env"]) if row["env"] else None, "backend_build": row["backend_build"],
         "w": row["w"], "h": row["h"], "created_at": row["created_at"]}
    return {k: v for k, v in d.items() if v is not None}


def capture_add(file: str | os.PathLike, target: str, pose: Any, *, id: str | None = None,  # noqa: A002
                env: Any = None, backend_build: str | None = None, w: int | None = None, h: int | None = None,
                created_at: str | None = None, path: str | os.PathLike | None = None) -> dict:
    """Record a captured frame (the PNG itself lives under ``work/``); returns the row with its ``id``."""
    cid = _cap_id(id) if id else new_capture_id()
    if pose is None:
        raise SatkError("BAD_PARAMS", "capture needs a pose")
    with _session(True, path) as con:
        assert con is not None
        con.execute("INSERT OR REPLACE INTO capture(id, path, target, pose, env, backend_build, w, h, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (cid, jpath(file), str(target), _json_col(pose), _json_col(env), backend_build,
                     None if w is None else int(w), None if h is None else int(h), created_at or _now()))
        row = con.execute("SELECT * FROM capture WHERE id=?", (cid,)).fetchone()
    return _cap(row)


def capture_get(cid: str, *, path: str | os.PathLike | None = None) -> dict | None:
    """Capture row by ``cap:<id>`` or ``None``."""
    c = _cap_id(cid)
    with _session(False, path) as con:
        if con is None:
            return None
        row = con.execute("SELECT * FROM capture WHERE id=?", (c,)).fetchone()
    return _cap(row) if row else None


def capture_list(target: str | None = None, *, limit: int = 20, offset: int = 0,
                 path: str | os.PathLike | None = None) -> tuple[list[dict], int]:
    """Captures, newest first; returns ``(page, total)``."""
    cond, params = "", []
    if target:
        cond, params = " WHERE target = ?", [target]
    with _session(False, path) as con:
        if con is None:
            return [], 0
        total = con.execute(f"SELECT count(*) FROM capture{cond}", params).fetchone()[0]
        rows = con.execute(f"SELECT * FROM capture{cond} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                           params + [int(limit), int(offset)]).fetchall()
    return [_cap(r) for r in rows], int(total)
