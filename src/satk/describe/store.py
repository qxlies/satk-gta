"""Idempotent sync of bound descriptions into ``work/notes.sqlite`` through :mod:`satk.notes.db`.

Each bound description becomes one note::

    sid        model:<id> (or model:<name>, see satk.describe.bind)
    author     import:gta-scout                       (source attribution)
    lang/text  English part of the publisher text     (--text full keeps FR+EN)
    tags       desc gta-scout <publisher tags>
    confidence publisher confidence (0.6..1)
    evidence   gta-scout:<version>#<entry id 12> <archive>/<entry>.dff sha256:<64 hex>
    created_at the pack date (deterministic: re-exports are byte-identical)

Only notes with author ``import:gta-scout`` and tag ``desc`` are ever changed or removed; notes of
people and agents are never touched. A repeated import adds nothing (``unchanged``); a changed
text, tags or confidence for the same model replaces the old note (``updated``/``removed``); with
``prune`` imported notes of models that are no longer bound are removed too.

New notes are written in one transaction with :func:`satk.notes.db.import_jsonl` (from a private
temp file), removals with :func:`satk.notes.db.remove_note`.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from satk.core.ids import Sid
from satk.core.paths import ensure_removable, tmp
from satk.core.registry import report_progress
from satk.notes import db as notes

from .bind import Desc
from .pack import AUTHOR, Pack, created_at, note_text

__all__ = ["TAG", "NoteRec", "records", "imported", "sync", "clear"]

#: Tag of every imported description note.
TAG = "desc"
_TMP_ID = "describe"


@dataclass(frozen=True)
class NoteRec:
    """A note as it should be stored."""

    sid: str
    text: str
    lang: str
    tags: str
    confidence: float | None
    evidence: str
    created_at: str | None

    @property
    def key(self) -> tuple[str, str]:
        return self.sid, self.text

    def same(self, n: notes.Note) -> bool:
        """``n`` already carries this text with the same language, tags and confidence."""
        return (n.sid, n.text, n.lang, n.tags or "") == (self.sid, self.text, self.lang, self.tags) and \
            _conf(n.confidence) == _conf(self.confidence)

    def record(self) -> dict:
        """Line of the JSONL handed to :func:`satk.notes.db.import_jsonl`."""
        d = {"sid": self.sid, "author": AUTHOR, "lang": self.lang, "text": self.text, "tags": self.tags,
             "confidence": self.confidence, "evidence": self.evidence, "created_at": self.created_at}
        return {k: v for k, v in d.items() if v is not None}


def _conf(v: float | None) -> float | None:
    return None if v is None else round(float(v), 4)


def _sort_key(sid: str) -> tuple[int, str]:
    s = Sid.parse(sid)
    return (s.num if s.num is not None else 1 << 62, sid)


def records(descs: list[Desc], pack: Pack, text_mode: str = "en") -> list[NoteRec]:
    """Notes for ``descs`` (deterministic order; duplicates of the same sid+text dropped)."""
    out: dict[tuple[str, str], NoteRec] = {}
    when = created_at(pack.version)
    for d in descs:
        text, lang = note_text(d.entry.description, text_mode)
        tags = notes.norm_tags([TAG, "gta-scout", *d.entry.tags]) or TAG
        ev = f"{pack.source}#{d.entry.id[:12]} {d.entry.dff_ref} sha256:{d.entry.dff_sha256}"
        if not d.by_name:
            ev += f" (published as {d.entry.name})"
        rec = NoteRec(sid=str(Sid.parse(d.sid)), text=text[: notes.MAX_TEXT], lang=lang, tags=tags,
                      confidence=d.entry.confidence, evidence=ev[:500], created_at=when)
        out.setdefault(rec.key, rec)
    return sorted(out.values(), key=lambda r: (_sort_key(r.sid), r.text))


def imported(path: str | os.PathLike | None = None) -> list[notes.Note]:
    """All notes written by this importer (author ``import:gta-scout``, tag ``desc``)."""
    found, _total = notes.list_notes(author=AUTHOR, tag=TAG, limit=1 << 30, path=path)
    return found


def _write(recs: list[NoteRec], path: str | os.PathLike | None) -> dict:
    """Insert ``recs`` in one transaction via ``notes.import_jsonl`` (private temp JSONL)."""
    if not recs:
        return {"added": 0, "skipped": 0}
    d = Path(tempfile.mkdtemp(prefix="import-", dir=tmp(_TMP_ID)))
    try:
        f = d / "notes.jsonl"
        f.write_text("".join(json.dumps(r.record(), ensure_ascii=False, separators=(",", ":")) + "\n"
                             for r in recs), encoding="utf-8")
        return notes.import_jsonl(f, path=path)
    finally:
        shutil.rmtree(ensure_removable(d), ignore_errors=True)


def _remove(ids: list[int], path: str | os.PathLike | None, what: str) -> int:
    n = 0
    for i, nid in enumerate(ids, 1):
        if notes.remove_note(nid, path=path) is not None:
            n += 1
        if i % 200 == 0:
            report_progress(i, len(ids), what)
    return n


def sync(recs: list[NoteRec], *, prune: bool = False, dry_run: bool = False,
         path: str | os.PathLike | None = None) -> dict:
    """Make the imported notes equal ``recs``; returns counts (see the module docstring)."""
    have = imported(path)
    want = {r.key: r for r in recs}
    want_sids = {r.sid for r in recs}
    unchanged: set[tuple[str, str]] = set()
    stale: list[int] = []  # replaced text of a bound model, or changed tags/confidence
    pruned: list[int] = []  # model no longer bound (only with prune)
    for n in have:
        r = want.get((n.sid, n.text))
        if r is not None and r.same(n) and r.key not in unchanged:
            unchanged.add(r.key)
        elif n.sid in want_sids:
            stale.append(n.id)
        elif prune:
            pruned.append(n.id)
    to_add = [r for r in recs if r.key not in unchanged]
    out = {"added": len(to_add), "unchanged": len(unchanged), "replaced": len(stale), "pruned": len(pruned),
           "kept_unbound": len(have) - len(unchanged) - len(stale) - len(pruned)}
    if dry_run:
        return out
    removed = _remove(stale + pruned, path, "removing old descriptions")
    res = _write(to_add, path)
    out["added"] = int(res.get("added", 0))
    if res.get("skipped"):
        out["already_present"] = int(res["skipped"])
    if res.get("warn"):
        out["warn"] = list(res["warn"])
    if removed != len(stale) + len(pruned):  # pragma: no cover - concurrent removal
        out["removed_concurrently"] = len(stale) + len(pruned) - removed
    return out


def clear(*, dry_run: bool = False, path: str | os.PathLike | None = None) -> dict:
    """Remove every imported description note (other notes stay)."""
    ids = [n.id for n in imported(path)]
    if dry_run:
        return {"removed": 0, "would_remove": len(ids)}
    return {"removed": _remove(ids, path, "removing imported descriptions")}
