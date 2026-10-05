"""Read access to ``work/re/symdb.sqlite`` (owner WP-09).

:func:`open_db` caches a read-only SQLite snapshot in memory, refreshed when the file changes.
The on-disk connection is closed after the backup, so even an idle MCP server cannot block a
Windows rebuild. Calls already using an old snapshot can finish consistently after a replace.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from ..core.errors import SatkError
from .resolve import AddrMap, SectionInfo

__all__ = ["db_path", "open_db", "SymDb", "hx", "parse_addr"]

_lock = threading.Lock()
_cache: dict[str, tuple[int, int, "SymDb"]] = {}


def db_path() -> Path:
    """``<work>/re/symdb.sqlite`` (not created)."""
    from ..core.paths import cfg

    return Path(os.path.abspath(cfg().paths.work)) / "re" / "symdb.sqlite"


def hx(a: int | None) -> str | None:
    return None if a is None else (f"-0x{-a:x}" if a < 0 else f"0x{a:x}")


def parse_addr(s: str) -> int:
    """``0x53BF09``, ``53bf09``, ``gta_sa.exe+0x13BF09`` -> int (``BAD_PARAMS`` otherwise)."""
    t = str(s).strip().lower().replace("`", "")
    base = 0
    if "+" in t:
        mod, _, t = t.partition("+")
        if mod.strip() not in ("gta_sa.exe", "gta_sa", "gta-sa.exe", "gta_sa_mta.exe", "proxy_sa.exe"):
            raise SatkError("BAD_PARAMS", f"not a gta_sa.exe address: {s!r}")
        base = 0x400000
    t = t.strip()
    try:
        v = int(t, 16)
    except ValueError:
        raise SatkError("BAD_PARAMS", f"not an address: {s!r}", hint="use 0x53BF09 or gta_sa.exe+0x13BF09") from None
    return base + v


class SymDb:
    """Thin query layer over an immutable snapshot of one symdb file."""

    def __init__(self, path: Path):
        self.path = path
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            with closing(sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)) as source:
                source.backup(self.con)
            self.con.execute("PRAGMA query_only = ON")
        except BaseException:
            self.con.close()
            raise
        self.con.row_factory = sqlite3.Row
        self._closed = False
        self._amap: AddrMap | None = None
        self._meta: dict[str, str] | None = None

    def close(self) -> None:
        self._closed = True
        try:
            self.con.close()
        except sqlite3.Error:  # pragma: no cover
            pass

    # -- meta ------------------------------------------------------------------------------

    @property
    def meta(self) -> dict[str, str]:
        if self._meta is None:
            self._meta = {r["key"]: r["value"] for r in self.con.execute("SELECT key, value FROM meta")}
        return self._meta

    @property
    def stats(self) -> dict:
        try:
            return json.loads(self.meta.get("stats", "{}"))
        except json.JSONDecodeError:  # pragma: no cover
            return {}

    def sources(self) -> list[dict]:
        return [dict(r) for r in self.con.execute("SELECT kind, repo, rev FROM source_rev ORDER BY id")]

    def rev_of(self, kind: str) -> str | None:
        r = self.con.execute("SELECT rev FROM source_rev WHERE kind=?", (kind,)).fetchone()
        return r["rev"] if r else None

    # -- address map ---------------------------------------------------------------------

    @property
    def amap(self) -> AddrMap:
        if self._amap is None:
            secs = [SectionInfo(r["name"], r["va_start"], r["va_end"], r["flags"])
                    for r in self.con.execute("SELECT * FROM section")]
            starts: dict[int, bool] = {}
            ghidra: set[int] = set()
            tentative: set[int] = set()
            ends: dict[int, int] = {}
            for r in self.con.execute("SELECT addr, end_addr, qual IS NOT NULL AS named, bounds, origin FROM func"):
                starts[r[0]] = bool(r[2])
                if r[3] == "ghidra":
                    ghidra.add(r[0])
                if r[1] is not None:
                    ends[r[0]] = r[1]
                if r[4] in ("gta_reversed", "thunk_scan"):
                    tentative.add(r[0])
            thunks = [(r[0], r[1], r[2]) for r in self.con.execute("SELECT addr, target, kind FROM thunk")]
            ranges = None
            if self.con.execute("SELECT 1 FROM sqlite_master WHERE name='func_range'").fetchone():
                ranges = [tuple(r) for r in self.con.execute("SELECT start_addr,end_addr,func_addr,body_addr FROM func_range")]
                if not ranges:
                    ranges = None  # legacy/manual ghidra end_addr rows can still supply one interval
            self._amap = AddrMap(secs, starts, thunks, ghidra=ghidra, ends=ends, tentative=tentative, exact_ranges=ranges)
        return self._amap

    # -- rows ------------------------------------------------------------------------------

    def func(self, addr: int) -> sqlite3.Row | None:
        return self.con.execute("SELECT * FROM func WHERE addr=?", (addr,)).fetchone()

    def funcs_by_name(self, name: str) -> list[sqlite3.Row]:
        rows = list(self.con.execute("SELECT * FROM func WHERE qual = ? COLLATE NOCASE ORDER BY addr", (name,)))
        if not rows:
            rows = list(self.con.execute(
                "SELECT f.* FROM func_alias a JOIN func f ON f.addr=a.addr WHERE a.qual = ? COLLATE NOCASE "
                "ORDER BY f.addr", (name,)))
        return rows

    def aliases(self, addr: int) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT qual FROM func_alias WHERE addr=? ORDER BY qual", (addr,))]

    def thunk(self, addr: int) -> sqlite3.Row | None:
        return self.con.execute("SELECT * FROM thunk WHERE addr=?", (addr,)).fetchone()

    def global_at(self, a: int) -> tuple[sqlite3.Row, int] | None:
        r = self.con.execute("SELECT * FROM global WHERE addr <= ? ORDER BY addr DESC LIMIT 1", (a,)).fetchone()
        if r is None:
            return None
        size = r["byte_size"] or 1
        if a < r["addr"] + max(size, 1):
            return r, a - r["addr"]
        return None

    def vtable_at(self, a: int) -> tuple[sqlite3.Row, int] | None:
        r = self.con.execute("SELECT * FROM vtable WHERE addr <= ? ORDER BY addr DESC LIMIT 1", (a,)).fetchone()
        if r is not None and a < r["addr"] + 4 * r["slots"]:
            return r, a - r["addr"]
        return None

    def vtable_slots_for(self, target: int, limit: int = 8) -> list[str]:
        return [f"{r[0]}[{r[1]}]" for r in self.con.execute(
            "SELECT v.cls, s.slot FROM vtable_slot s JOIN vtable v ON v.addr=s.vt WHERE s.target=? "
            "ORDER BY v.cls LIMIT ?", (target, limit))]

    def patches_in(self, lo: int, hi: int, origin: str = "all", kind: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM patch WHERE addr >= ? AND addr < ?"
        args: list = [lo, hi]
        if origin != "all":
            q += " AND origin = ?"
            args.append(origin)
        if kind:
            q += " AND kind = ?"
            args.append(kind)
        return list(self.con.execute(q + " ORDER BY addr, origin, kind, src_file, src_line", args))

    def patches_covering(self, a: int, origin: str = "all") -> list[sqlite3.Row]:
        """Patches whose bytes ``[addr, addr+len)`` contain ``a`` (len unknown -> 1)."""
        q = "SELECT * FROM patch WHERE addr <= ? AND addr + max(coalesce(len, 1), 1) > ?"
        args: list = [a, a]
        if origin != "all":
            q += " AND origin = ?"
            args.append(origin)
        return list(self.con.execute(q, args))

    def count(self, table: str, where: str = "", args: tuple = ()) -> int:
        return int(self.con.execute(f"SELECT count(*) FROM {table} {where}", args).fetchone()[0])


def open_db(path: Path | None = None, *, required: bool = True) -> SymDb | None:
    """The symbol DB (cached per file version); ``NOT_READY`` if it was not built yet."""
    p = Path(path) if path else db_path()
    try:
        st = p.stat()
    except OSError:
        if not required:
            return None
        raise SatkError("NOT_READY", f"symbol DB not built: {p.as_posix()}", hint="satk re build",
                        data={"path": p.as_posix()}) from None
    key = os.path.normcase(str(p))
    with _lock:
        cur = _cache.get(key)
        if cur and cur[0] == st.st_mtime_ns and cur[1] == st.st_size and not cur[2]._closed:
            return cur[2]
        # Do not close an evicted snapshot: another thread may still be using it.
        db = None
        try:
            db = SymDb(p)
            ver = db.meta.get("schema_version")
        except sqlite3.DatabaseError as e:
            if db is not None:
                db.close()
            raise SatkError("NOT_READY", f"symbol DB is unreadable: {e}", hint="satk re build") from e
        if ver != "1":
            db.close()
            raise SatkError("NOT_READY", f"symbol DB schema {ver!r} != 1", hint="satk re build")
        _cache[key] = (st.st_mtime_ns, st.st_size, db)
        return db


def reset_cache() -> None:
    """Evict snapshots; active callers may finish using theirs before it is collected."""
    with _lock:
        _cache.clear()
