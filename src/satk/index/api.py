"""Python API of the asset index ``satk.index.api`` (SPEC §4.3.4, contract Q1). Owner: WP-03.

Q1 freezes the *shape* of this module for WP-04/05/07/10/12:

* value types :class:`BlobRef`, :class:`TexRef`, :class:`ColRef`, :class:`ModelFiles`, :class:`InstRow`;
* :class:`IndexDB` - read-only access to ``work/index/<profile>.sqlite``;
* :class:`FakeIndexDB` (``satk.index.fake``) - the same API over a small built-in data set
  (``model:411`` infernus, ``model:17613`` lae2_roads89, ``model:300`` cutobj01 and their
  files, textures, placements) for tests of neighbouring packages; no game files needed;
* :func:`open_index` - the factory every consumer should use (cached; honours
  ``SATK_INDEX_FAKE=1`` and :func:`override_index` for tests).

:class:`IndexDB` implements every method over ``work/index/<profile>.sqlite`` (built by
``satk index build``; SQL in :mod:`satk.index.queries`); :class:`FakeIndexDB` answers the same
calls identically (parity-tested) over its built-in data set. :class:`IndexNotImplemented` is kept
for compatibility with Q1-era callers. Every answer may carry ``INDEX_STALE`` warnings through the
operations layer (:meth:`IndexDB.stale_warnings`).

Search semantics (``find``): exact names first - if some names equal ``q`` only they are returned
and ``warn`` says how many more contain it; ``*``/``?`` wildcards search by pattern
(``'*tarmac*'``); ``kind="file"`` searches blob paths.

Conventions (shared by the real and the fake implementation):

* envelope methods (``get``/``find``/``refs``/``near``/``query``) return SPEC §3.3 envelopes;
  coordinates are rounded to 0.01, angles to 0.1 degree, quaternions to 1e-4;
* typed methods (``insts``/``model_files``/``texture_ref``/...) return raw, unrounded values;
* ``q_ipl`` is the quaternion exactly as stored in the IPL; the world rotation is its
  conjugate (V9) - use :func:`world_quat` / :func:`world_rz`;
* ``aabb`` tuples are ``(minx, miny, minz, maxx, maxy, maxz)`` in world space;
* ``box`` parameters are 2D ``(minx, miny, maxx, maxy)``;
* paths in value types are absolute :class:`~pathlib.Path` objects (game files: open them only
  through :func:`satk.core.paths.open_ro`, or simply call :meth:`IndexDB.read_blob`).

Stdlib only (importable from Blender's Python 3.13).
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from ..core import envelope as env
from ..core.errors import SatkError
from ..core.ids import KIND_OWNER, Sid
from ..core.paths import cfg, jpath, open_ro
from .ddl import SCHEMA_VERSION

__all__ = [
    "BlobRef", "TexRef", "ColRef", "ModelFiles", "InstRow",
    "IndexDB", "FakeIndexDB", "IndexNotImplemented",
    "open_index", "override_index", "clear_cache", "index_path",
    "INDEX_KINDS", "SEARCH_KINDS", "NEAR_KINDS", "REFS_RELS", "LOD_MODES", "MATCH_MODES",
    "DATA_KINDS", "LOCAL_KINDS", "LocalSid", "parse_sid",
    "world_quat", "world_rz", "rotate", "world_aabb",
]

# --------------------------------------------------------------------------- value types


@dataclass(frozen=True, slots=True)
class BlobRef:
    """A blob: an IMG directory entry or a loose file.

    Attributes:
        sid: ``dff:<stem>`` / ``txd:<stem>`` for the active blob, ``file:<relpath>[/<entry>]``
            otherwise (or when asked for by ``file:``).
        path: absolute path of the IMG archive or loose file.
        offset: byte offset of the blob inside ``path``.
        size: bytes to read (directory size for IMG entries, file size for loose files).
        name: entry/file name with extension, e.g. ``infernus.dff``.
    """

    sid: str
    path: Path
    offset: int
    size: int
    name: str


@dataclass(frozen=True, slots=True)
class TexRef:
    """Where the mip0 pixels of one texture live (enough to decode without parsing the TXD).

    ``data_off``/``pal_off`` are absolute offsets inside ``path``; ``pix`` is ``pix:<24 hex>``.
    ``alpha`` is the effective alpha flag (DXT1 + raster 565 => False).
    """

    sid: str
    pix: str
    path: Path
    data_off: int
    data_size: int
    pal_off: int | None
    d3dfmt: str
    raster_fmt: int
    platform: int
    w: int
    h: int
    levels: int
    alpha: bool


@dataclass(frozen=True, slots=True)
class ColRef:
    """A collision model: ``blob`` holds it at model index ``idx``; ``via`` is ``colfile``|``embedded``."""

    sid: str
    blob: BlobRef
    idx: int
    name: str
    via: str


@dataclass(frozen=True, slots=True)
class ModelFiles:
    """Files of the active definition of a model.

    ``txd_chain`` = [own TXD, txdp parents..., ``vehicle`` for ``cars``]; missing TXDs are
    skipped. ``dff``/``col`` are ``None`` when the game has none (e.g. cutscene placeholders).
    """

    model_id: int
    name: str
    sec: str
    dff: BlobRef | None
    txd_chain: list[BlobRef]
    col: ColRef | None


@dataclass(frozen=True, slots=True)
class InstRow:
    """One IPL placement (raw values).

    ``q_ipl`` = quaternion ``(x, y, z, w)`` as stored in the IPL (world = conjugate);
    ``aabb`` = world AABB ``(minx, miny, minz, maxx, maxy, maxz)``.
    """

    sid: str
    model_id: int
    name: str
    pos: tuple[float, float, float]
    q_ipl: tuple[float, float, float, float]
    area: int
    iflags: int
    lod_sid: str | None
    is_lod: bool
    aabb: tuple[float, float, float, float, float, float]


# --------------------------------------------------------------------------- constants

#: SID kinds served by the index (``@layer`` allowed on all of them).
INDEX_KINDS: tuple[str, ...] = tuple(k for k, o in KIND_OWNER.items() if o == "index")
#: Kinds of the schema-v3 data tables: ``water:<idx>`` | ``water:water1/<idx>`` (``water_quad``),
#: ``tcyc:<weather>[/<hour>]`` (``timecyc``), ``handling:<id>`` (``handling``).
DATA_KINDS: tuple[str, ...] = ("water", "tcyc", "handling")
#: The data kinds that ``satk.core.ids`` does not list (yet): the frozen core is extended by its owner;
#: until then :func:`parse_sid` accepts them for the index API and ``asset get/refs/find``.
LOCAL_KINDS: tuple[str, ...] = tuple(k for k in DATA_KINDS if k not in KIND_OWNER)
#: Kinds covered by name search (``fts_name``).
SEARCH_KINDS: tuple[str, ...] = ("model", "dff", "txd", "tex", "col", "ipl", "zone", "ifp", "anim", "handling", "tcyc",
                                 "file")
#: Kinds returned by ``near``.
NEAR_KINDS: tuple[str, ...] = ("inst", "item", "zone", "water")
LOD_MODES: tuple[str, ...] = ("hd", "lod", "all")
MATCH_MODES: tuple[str, ...] = ("aabb", "center")

#: Relations for ``refs`` / ``asset_refs`` (SPEC §4.3.5; ``dff``/``col``/``ifp`` are additive).
REFS_RELS: dict[str, tuple[str, ...]] = {
    "model": ("inst", "tex", "dff", "txd", "col", "ide", "overrides"),
    "tex": ("models", "same_pixels", "txd"),
    "pix": ("textures",),
    "txd": ("textures", "models", "parent", "children", "file"),
    "dff": ("models", "file"),
    "col": ("models", "file"),
    "inst": ("model", "lod", "hd_children", "ipl", "near"),
    "file": ("parsed", "shadowed_by", "shadows"),
    "ipl": ("inst",),
    "ide": ("models",),
    "item": ("ipl",),
    "zone": ("inst",),
    "ifp": ("anims",),
    "anim": ("ifp",),
    "handling": ("models",),
    "tcyc": ("hours",),
    "water": (),
}

#: Columns of ``refs``/``near`` tables whose rows are placements.
INST_COLS: list[str] = ["id", "model", "name", "pos", "rz", "area", "is_lod"]
#: Columns of ``refs model:N --rel tex``.
MODEL_TEX_COLS: list[str] = ["id", "texture", "via", "uses", "pix"]
#: Columns of other ``refs`` tables and of ``find``.
GENERIC_COLS: list[str] = ["id", "name", "info"]
FIND_COLS: list[str] = ["id", "kind", "name", "info"]
NEAR_COLS: list[str] = ["id", "model", "name", "pos", "rz", "area", "is_lod", "dist"]

QUERY_DEFAULT_LIMIT = 200
_QUERY_COUNT_CAP = 1_000_000


# --------------------------------------------------------------------------- errors


class IndexNotImplemented(SatkError, NotImplementedError):
    """Raised by Q1 stubs: ``isinstance(e, NotImplementedError)`` and ``e.code == "NOT_READY"``."""

    def __init__(self, what: str):
        SatkError.__init__(
            self, "NOT_READY", f"satk.index: {what} is not implemented yet (stub)",
            hint="use satk.index.api.FakeIndexDB in tests until the index builder lands",
            data={"method": what},
        )


# --------------------------------------------------------------------------- geometry helpers


def world_quat(q_ipl: Sequence[float]) -> tuple[float, float, float, float]:
    """World rotation ``(x, y, z, w)`` of an IPL quaternion: the conjugate (V9)."""
    x, y, z, w = (float(c) for c in q_ipl)
    return (-x, -y, -z, w)


def world_rz(q_ipl: Sequence[float], eps: float = 1e-5) -> float | None:
    """World heading in degrees ``[0, 360)`` if the rotation is about Z only, else ``None``."""
    x, y, z, w = world_quat(q_ipl)
    if abs(x) > eps or abs(y) > eps:
        return None
    deg = math.degrees(2.0 * math.atan2(z, w)) % 360.0
    if deg >= 359.95:  # rounds to 360.0 -> report 0.0
        deg = 0.0
    return deg


def rotate(q: Sequence[float], v: Sequence[float]) -> tuple[float, float, float]:
    """Rotate vector ``v`` by unit quaternion ``q = (x, y, z, w)``."""
    qx, qy, qz, qw = (float(c) for c in q)
    vx, vy, vz = (float(c) for c in v)
    # t = 2 * cross(q.xyz, v); v' = v + w*t + cross(q.xyz, t)
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )


def world_aabb(bmin: Sequence[float], bmax: Sequence[float], pos: Sequence[float],
               q_ipl: Sequence[float]) -> tuple[float, float, float, float, float, float]:
    """World AABB of a local box placed at ``pos`` with IPL quaternion ``q_ipl`` (conjugated)."""
    q = world_quat(q_ipl)
    xs, ys, zs = [], [], []
    for cx in (bmin[0], bmax[0]):
        for cy in (bmin[1], bmax[1]):
            for cz in (bmin[2], bmax[2]):
                rx, ry, rz = rotate(q, (cx, cy, cz))
                xs.append(rx + pos[0])
                ys.append(ry + pos[1])
                zs.append(rz + pos[2])
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


# --------------------------------------------------------------------------- paging / validation helpers


def cursor_offset(cursor: str | None) -> int:
    """Offset encoded in an opaque cursor (``None`` -> 0); ``BAD_PARAMS`` if malformed."""
    if cursor is None or cursor == "":
        return 0
    m = re.fullmatch(r"o(\d+)", str(cursor))
    if not m:
        raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}", hint="pass the 'next' value of the previous page")
    return int(m.group(1))


def page(rows: list[list], limit: int, cursor: str | None) -> tuple[list[list], int, str | None]:
    """Slice ``rows`` for a page: ``(page_rows, total, next_cursor)``."""
    off = cursor_offset(cursor)
    chunk = rows[off:off + limit]
    nxt = f"o{off + limit}" if off + limit < len(rows) else None
    return chunk, len(rows), nxt


def check_choice(name: str, value: str, choices: Sequence[str]) -> str:
    if value not in choices:
        raise SatkError("BAD_PARAMS", f"{name} must be one of {', '.join(choices)}, got {value!r}",
                        data={"choices": list(choices)})
    return value


@dataclass(frozen=True, slots=True)
class LocalSid:
    """A SID of a :data:`LOCAL_KINDS` kind (same shape as :class:`~satk.core.ids.Sid`, never layered)."""

    kind: str
    key: str
    layer: None = None

    def __str__(self) -> str:
        return f"{self.kind}:{self.key}"

    @property
    def num(self) -> None:
        return None


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def parse_sid(sid: "str | Sid | LocalSid") -> "Sid | LocalSid":
    """:meth:`Sid.parse`, plus the :data:`LOCAL_KINDS` (``water:``, ``tcyc:``, ``handling:``)."""
    if isinstance(sid, (Sid, LocalSid)):
        return sid
    if isinstance(sid, str):
        kind, sep, key = sid.strip().partition(":")
        kind = kind.strip().lower()
        if sep and kind in LOCAL_KINDS:
            key = key.strip().lower()
            if not key or "@" in key or _CTRL.search(key):
                raise SatkError("BAD_ID", f"bad {kind} SID {sid!r}: the key must be non-empty, without '@'",
                                hint="satk help ids", data={"input": sid})
            return LocalSid(kind, key)
    return Sid.parse(sid)


def as_sid(sid: "str | Sid | LocalSid", kinds: Sequence[str] | None = None) -> "Sid | LocalSid":
    """Parse ``sid`` and check it is one of ``kinds`` (default: any index kind, data kinds included)."""
    s = parse_sid(sid)
    allowed = tuple(kinds) if kinds else INDEX_KINDS + LOCAL_KINDS
    if s.kind not in allowed:
        raise SatkError("BAD_ID", f"expected a SID of kind {'|'.join(allowed)}, got {str(s)!r}",
                        hint="satk help ids", data={"kinds": list(allowed)})
    return s


# --------------------------------------------------------------------------- read-only SQL


_SQLITE_PRAGMA = getattr(sqlite3, "SQLITE_PRAGMA", 19)
_ALLOWED_ACTIONS = frozenset(
    getattr(sqlite3, n) for n in ("SQLITE_SELECT", "SQLITE_READ", "SQLITE_FUNCTION", "SQLITE_RECURSIVE")
    if hasattr(sqlite3, n)
)
#: introspection pragmas that take a table/index name as argument
_INFO_PRAGMAS = frozenset({"table_info", "table_xinfo", "index_list", "index_info", "index_xinfo",
                           "foreign_key_list", "table_list"})
#: value pragmas allowed only without an argument (reading, never setting); FTS5 reads data_version
_VALUE_PRAGMAS = frozenset({"user_version", "schema_version", "data_version", "compile_options"})
_FIRST_WORD = re.compile(r"^\s*(?:--[^\n]*\n\s*|/\*.*?\*/\s*)*([A-Za-z]+)", re.S)
_READ_VERBS = frozenset({"select", "with", "values", "pragma", "explain"})


def _authorizer(action: int, arg1, arg2, dbname, source) -> int:
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == _SQLITE_PRAGMA:
        name = str(arg1).lower()
        if name in _INFO_PRAGMAS or (name in _VALUE_PRAGMAS and arg2 is None):
            return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _cell(v: Any) -> Any:
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return v


def run_readonly_query(conn: sqlite3.Connection, sql: str, params: Sequence = (), limit: int = QUERY_DEFAULT_LIMIT,
                       lock: threading.RLock | None = None) -> dict:
    """Run one read-only statement on ``conn`` and return a table envelope.

    Only ``SELECT``/``WITH``/``VALUES``/read-only ``PRAGMA`` pass the authorizer; anything else
    raises ``READ_ONLY``. BLOB cells are returned as hex strings. ``total`` counts all rows
    (up to 1 000 000); only the first ``limit`` are returned.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise SatkError("BAD_PARAMS", "empty SQL")
    lim = env.clamp_limit(limit, default=QUERY_DEFAULT_LIMIT, maximum=env.MAX_LIMIT)
    m = _FIRST_WORD.match(sql)
    verb = m.group(1).lower() if m else ""
    if verb not in _READ_VERBS:
        raise SatkError("READ_ONLY", f"the index is read-only: only SELECT is allowed (got {verb.upper() or '?'})",
                        hint="SELECT ... FROM v_model / v_inst / v_tex / v_file")
    lk = lock or threading.RLock()
    with lk:
        conn.set_authorizer(_authorizer)
        try:
            try:
                cur = conn.execute(sql, list(params or ()))
            except sqlite3.DatabaseError as e:
                text = str(e)
                if "not authorized" in text or "readonly" in text or "read-only" in text:
                    raise SatkError("READ_ONLY", f"the index is read-only: {text}",
                                    hint="only SELECT statements are allowed") from None
                if isinstance(e, sqlite3.ProgrammingError):
                    raise SatkError("BAD_PARAMS", f"SQL error: {text}") from None
                raise SatkError("BAD_PARAMS", f"SQL error: {text}",
                                hint="satk index query \"SELECT name, sql FROM sqlite_master\"") from None
            except sqlite3.Warning as e:  # e.g. "You can only execute one statement at a time"
                raise SatkError("BAD_PARAMS", f"SQL error: {e}") from None
            cols = [d[0] for d in (cur.description or [])]
            rows: list[list] = []
            total = 0
            warn: list[str] = []
            for r in cur:
                total += 1
                if len(rows) < lim:
                    rows.append([_cell(v) for v in r])
                if total >= _QUERY_COUNT_CAP:
                    warn.append(f"TRUNCATED: stopped counting at {_QUERY_COUNT_CAP} rows")
                    break
        finally:
            conn.set_authorizer(None)
    if total > lim:
        warn.append(f"LIMIT: {total} rows, {lim} returned (add LIMIT/OFFSET or raise limit)")
    return env.table(cols, rows, total=total, warn=warn)


# --------------------------------------------------------------------------- IndexDB


def index_path(profile: str = "vanilla") -> Path:
    """``<work>/index/<profile>.sqlite`` (not created). Aliases are resolved (``vanilla`` -> ``game``
    without a clean copy), so an alias and its target share one index file."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_\-]*", str(profile)):
        raise SatkError("BAD_PARAMS", f"bad profile name {profile!r}")
    profile = cfg().canonical_profile(str(profile))
    return Path(os.path.abspath(cfg().paths.work)) / "index" / f"{profile}.sqlite"


class IndexDB:
    """Read-only access to one profile's index (``mode=ro``, ``PRAGMA query_only``).

    Thread-safe for reading: every thread gets its own SQLite connection.

    Args:
        profile: load profile (``vanilla`` | ``installed`` | ``samp`` | any configured one).
        path: explicit database file (default ``work/index/<profile>.sqlite``).

    Raises:
        SatkError: ``INDEX_MISSING`` if the file does not exist or has another schema version,
            ``BAD_PARAMS`` for an unknown profile.
    """

    profile: str
    path: Path

    def __init__(self, profile: str = "vanilla", path: Path | None = None):
        self.profile = cfg().canonical_profile(str(profile)) if path is None else str(profile)
        if path is None:
            cfg().profile(self.profile)  # BAD_PARAMS with suggestions for unknown profiles
            path = index_path(self.profile)
        self.path = Path(os.path.abspath(os.fspath(path)))
        if not self.path.is_file():
            raise SatkError(
                "INDEX_MISSING", f"no index for profile {self.profile!r}: {jpath(self.path)}",
                hint=f"satk index build --profile {self.profile}",
                data={"profile": self.profile, "path": jpath(self.path)},
            )
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._lock = threading.RLock()
        self._mtime_ns = self.path.stat().st_mtime_ns
        v = self._conn().execute("PRAGMA user_version").fetchone()[0]
        if v != SCHEMA_VERSION:
            self.close()
            raise SatkError(
                "INDEX_MISSING", f"index {jpath(self.path)} has schema v{v}, this satk needs v{SCHEMA_VERSION}",
                hint=f"satk index build --profile {self.profile}",
                data={"profile": self.profile, "schema": v},
            )

    # ---- connections

    def _connect(self) -> sqlite3.Connection:
        uri = "file:" + self.path.as_posix() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.execute("PRAGMA query_only = 1")
        return conn

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._connect()
            self._local.conn = c
            with self._lock:
                self._conns.append(c)
        return c

    def close(self) -> None:
        """Close all connections (the object must not be used afterwards)."""
        with self._lock:
            for c in self._conns:
                try:
                    c.close()
                except sqlite3.Error:  # pragma: no cover
                    pass
            self._conns.clear()
        self._local = threading.local()

    def __enter__(self) -> "IndexDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(profile={self.profile!r}, path={jpath(self.path)!r})"

    # ---- implemented in Q1

    def meta(self) -> dict[str, str]:
        """The ``meta`` table as a dict (``profile``, ``content_hash``, ``img_order`` ...)."""
        return {k: v for k, v in self._conn().execute("SELECT key, value FROM meta ORDER BY key")}

    def query(self, sql: str, params: list = (), limit: int = 200) -> dict:
        """Run one read-only SQL statement (SELECT only, enforced by an authorizer).

        Returns a table envelope; writes raise ``READ_ONLY``. Start with the views
        ``v_model``, ``v_inst``, ``v_tex``, ``v_file``.
        """
        return run_readonly_query(self._conn(), sql, params, limit)

    def read_blob(self, ref: BlobRef) -> bytes:
        """Bytes of a blob (``open_ro`` + seek); ``NOT_FOUND`` if the file is gone or short."""
        return read_blob_bytes(ref)

    # ---- queries (SQL implementation in satk.index.queries)

    def _q(self):
        from .queries import Q

        return Q(self)

    @property
    def root(self) -> Path:
        """Game root the index was built from (``meta.root``); paths of blobs live under it."""
        r = getattr(self, "_root", None)
        if r is None:
            r = Path(self.meta().get("root") or cfg().profile(self.profile).root)
            self._root = r
        return r

    @root.setter
    def root(self, value: Path) -> None:
        self._root = Path(value)

    def stale_warnings(self) -> list[str]:
        """``INDEX_STALE: ...`` warnings if a DAT/IDE/IPL/IMG file changed since the build (cached 2 s)."""
        f = getattr(self, "_fresh", None)
        if f is None:
            from .queries import Freshness

            f = Freshness(self.root, self.meta().get("sources_sig"))
            self._fresh = f
        return f.warnings()

    def get(self, sid: str, fields: list[str] | None = None) -> dict:
        """Object envelope for any index SID (``model:``, ``inst:``, ``tex:``, ``file:`` ...)."""
        return self._q().get(sid, fields)

    def find(self, q: str, kind: str | None = None, limit: int = 20, cursor: str | None = None) -> dict:
        """Name search (trigram FTS; < 3 chars -> prefix). Table ``id, kind, name, info``."""
        return self._q().find(q, kind, limit, cursor)

    def refs(self, sid: str, rel: str | None = None, limit: int = 50, cursor: str | None = None) -> dict:
        """Related objects (SPEC §4.3.5, :data:`REFS_RELS`): without ``rel`` an object
        ``{"rels": {rel: count}}``, with ``rel`` a table whose columns depend on ``rel``."""
        return self._q().refs(sid, rel, limit, cursor)

    def near(self, x: float, y: float, z: float | None = None, r: float = 50.0,
             box: tuple[float, float, float, float] | None = None, match: str = "aabb",
             kinds: tuple[str, ...] = ("inst",), area: int | None = 0, lod: str = "hd", limit: int = 50) -> dict:
        """Placements near a point (``r``) or in a 2D ``box``; table :data:`NEAR_COLS` sorted by ``dist``."""
        return self._q().near(x, y, z, r, box, match, kinds, area, lod, limit)

    def insts(self, *, model: int | None = None, box=None, center=None, r=None, area: int | None = 0,
              lod: str = "hd", match: str = "aabb", limit: int | None = None) -> list[InstRow]:
        """Placements as :class:`InstRow` (filters combine with AND)."""
        return self._q().insts(model, box, center, r, area, lod, match, limit)

    def model_files(self, model: int | str) -> ModelFiles:
        """DFF, TXD chain and COL of the active definition (by ID, name or ``model:`` SID)."""
        return self._q().model_files(model)

    def texture_ref(self, sid: str) -> TexRef:
        """Pixel location of ``tex:txd/name`` or ``pix:<hex>``."""
        return self._q().texture_ref(sid)

    def textures_of(self, sid: str) -> list[TexRef]:
        """Textures of ``txd:...`` (TXD order) or ``model:...`` (resolved material textures)."""
        return self._q().textures_of(sid)

    def blob_ref(self, sid: str) -> BlobRef:
        """Blob of ``dff:``/``txd:``/``file:`` (``@layer`` selects a non-winning version)."""
        return self._q().blob_ref_of(sid)

    def match_runtime(self, model_id: int, pos, tol: float = 0.05) -> list[str]:
        """Inst SIDs of ``model_id`` within ``tol`` (3D distance) of ``pos``, nearest first."""
        return self._q().match_runtime(model_id, pos, tol)


def read_blob_bytes(ref: BlobRef) -> bytes:
    """Read ``ref.size`` bytes at ``ref.offset`` of ``ref.path`` via ``open_ro``."""
    try:
        with open_ro(ref.path) as f:
            f.seek(ref.offset)
            data = f.read(ref.size)
    except FileNotFoundError:
        raise SatkError("NOT_FOUND", f"file of {ref.sid} not found: {jpath(ref.path)}",
                        hint="satk index build (the game files changed?)") from None
    if len(data) != ref.size:
        raise SatkError("NOT_FOUND", f"{ref.sid}: short read ({len(data)} of {ref.size} bytes) in {jpath(ref.path)}",
                        hint="the index is stale: satk index build")
    return data


# --------------------------------------------------------------------------- factory

_cache_lock = threading.RLock()
_cache: dict[tuple[str, str | None], IndexDB] = {}
_override: list[Callable[[str], IndexDB]] = []


def _fake_enabled() -> bool:
    return os.environ.get("SATK_INDEX_FAKE", "").strip().lower() in ("1", "true", "yes", "on")


def open_index(profile: str = "vanilla", path: Path | None = None) -> IndexDB:
    """The shared :class:`IndexDB` for ``profile`` (cached; reopened when the file changes).

    * inside :func:`override_index` the given factory is used;
    * with ``SATK_INDEX_FAKE=1`` a cached :class:`FakeIndexDB` is returned (no files needed).

    The cache is keyed by the profile and the *resolved* database path, so a configuration change (another workspace,
    another ``work`` folder: tests, long-running servers) never serves a database opened under the old one: the new
    path is a cache miss, and a missing file is ``INDEX_MISSING`` again.
    """
    if _override:
        return _override[-1](profile)
    if _fake_enabled():
        key = ("fake:" + str(profile), None)
        with _cache_lock:
            db = _cache.get(key)
            if db is None:
                from .fake import FakeIndexDB

                db = FakeIndexDB(profile)
                _cache[key] = db
            return db
    if path is None:
        name = cfg().canonical_profile(str(profile))
        key = (name, os.path.abspath(os.fspath(index_path(name))))
    else:
        key = (str(profile), os.path.abspath(os.fspath(path)))
    with _cache_lock:
        db = _cache.get(key)
        if db is not None:
            try:
                fresh = db.path.stat().st_mtime_ns == db._mtime_ns  # rebuilt -> atomic replace -> new mtime
            except OSError:
                fresh = False
            if fresh:
                return db
            db.close()
            del _cache[key]
        db = IndexDB(profile, path)
        _cache[key] = db
        return db


def clear_cache() -> None:
    """Close and forget all cached databases."""
    with _cache_lock:
        for db in _cache.values():
            try:
                db.close()
            except Exception:  # noqa: BLE001  # pragma: no cover
                pass
        _cache.clear()


@contextmanager
def override_index(factory: "Callable[[str], IndexDB] | IndexDB") -> Iterator[None]:
    """Make :func:`open_index` return ``factory(profile)`` (or a fixed object) in this block.

    Example (tests of other packages)::

        from satk.index.api import FakeIndexDB, override_index
        with override_index(FakeIndexDB()):
            ...  # code under test calls open_index("vanilla")
    """
    fn = factory if callable(factory) and not isinstance(factory, IndexDB) else (lambda _p, _db=factory: _db)
    with _cache_lock:
        _override.append(fn)
    try:
        yield
    finally:
        with _cache_lock:
            _override.remove(fn)


def __getattr__(name: str):  # lazy re-export: satk.index.fake imports this module
    if name == "FakeIndexDB":
        from .fake import FakeIndexDB

        return FakeIndexDB
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
