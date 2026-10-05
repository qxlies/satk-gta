"""satk.notes — persistent notes, bookmarks and captures in ``work/notes.sqlite`` (SPEC §4.5, owner WP-06).

* :mod:`satk.notes.db` — schema, notes (FTS5 ``unicode61``), bookmarks, captures, JSONL export/import;
* :mod:`satk.notes.provider` — SID providers (``note``; ``bm``/``cap`` ready for ``satk.viewer``);
* :mod:`satk.notes.ops` — MCP tool ``note`` and ``satk note add|list|rm|export|import``.
"""
