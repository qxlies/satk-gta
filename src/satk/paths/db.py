"""The path database ``work/index/paths-<profile>.sqlite``: import and read queries. Stdlib only.

Import (:func:`import_profile`):

1. the IMG archives of the profile in registration order (:func:`satk.formats.layout.archives`,
   namespace ``main``); the first archive that holds ``nodes<N>.dat`` wins, like the engine's
   streaming directory; files are opened read-only (:func:`satk.core.paths.open_ro`);
2. every region is split by the vendored gta-flow codec (lossless, sector padding included) and
   checked by its independent byte oracle;
3. the rows go into a fresh database file that atomically replaces the old one. The original region
   bytes are kept (table ``area``, column ``data``): ``satk paths compile`` rebuilds from them.

A repeated import with unchanged archives (size and mtime of every ``main`` archive) reuses the
database. Readers open it ``mode=ro``; :func:`open_paths` imports on first use.

Schema (``PRAGMA user_version`` = :data:`SCHEMA_VERSION`)::

    meta(key, value)                     schema, profile, root, img_order, sources (JSON), network_sha256
    area(area, source, entry, size, sha256, nodes, vehicle, ped, navis, links, trailing, data)
    node(area, idx, kind, x, y, z, base, degree, width, flood, flags, spawn, behaviour)
    navi(area, idx, x, y, at_area, at_idx, dx, dy, width, lanes_to, lanes_away, light_dir, light, bridge)
    link(area, k, node, to_area, to_idx, navi_area, navi_idx, dist, inter)
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..core.errors import SatkError
from ..core.paths import cfg, ensure_writable, jpath, work
from . import records as R

__all__ = ["SCHEMA_VERSION", "DDL", "Region", "db_path", "locate_regions", "import_profile", "PathsDB",
           "open_paths", "source_signature", "is_fresh", "dist2"]

SCHEMA_VERSION = 1
_NODES_RE = re.compile(r"^nodes(\d{1,3})\.dat$")
_PROFILE_RE = re.compile(r"[a-z0-9][a-z0-9_\-]*")

DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE area(
    area INTEGER PRIMARY KEY, source TEXT NOT NULL, entry TEXT NOT NULL, size INTEGER NOT NULL,
    sha256 TEXT NOT NULL, nodes INTEGER NOT NULL, vehicle INTEGER NOT NULL, ped INTEGER NOT NULL,
    navis INTEGER NOT NULL, links INTEGER NOT NULL, trailing INTEGER NOT NULL, data BLOB NOT NULL);
CREATE TABLE node(
    area INTEGER NOT NULL, idx INTEGER NOT NULL, kind TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL,
    z REAL NOT NULL, base INTEGER NOT NULL, degree INTEGER NOT NULL, width REAL NOT NULL,
    flood INTEGER NOT NULL, flags INTEGER NOT NULL, spawn INTEGER NOT NULL, behaviour INTEGER NOT NULL,
    PRIMARY KEY(area, idx)) WITHOUT ROWID;
CREATE INDEX node_xy ON node(x, y);
CREATE TABLE navi(
    area INTEGER NOT NULL, idx INTEGER NOT NULL, x REAL NOT NULL, y REAL NOT NULL, at_area INTEGER NOT NULL,
    at_idx INTEGER NOT NULL, dx REAL NOT NULL, dy REAL NOT NULL, width REAL NOT NULL,
    lanes_to INTEGER NOT NULL, lanes_away INTEGER NOT NULL, light_dir INTEGER NOT NULL,
    light INTEGER NOT NULL, bridge INTEGER NOT NULL, PRIMARY KEY(area, idx)) WITHOUT ROWID;
CREATE TABLE link(
    area INTEGER NOT NULL, k INTEGER NOT NULL, node INTEGER NOT NULL, to_area INTEGER NOT NULL,
    to_idx INTEGER NOT NULL, navi_area INTEGER, navi_idx INTEGER, dist INTEGER NOT NULL,
    inter INTEGER NOT NULL, PRIMARY KEY(area, k)) WITHOUT ROWID;
CREATE INDEX link_node ON link(area, node);
"""


@dataclass
class Region:
    """One ``nodes<N>.dat`` found in the profile's archives."""

    area: int
    source: str      # archive relpath, e.g. models/gta3.img
    entry: str       # entry name as stored in the directory
    data: bytes


@dataclass
class _Located:
    profile: str
    root: Path
    img_order: str
    regions: dict[int, Region]
    sources: dict[str, list[int]]   # archive relpath -> [size, mtime_ns] (every main archive)
    warnings: list[str] = field(default_factory=list)


def _canonical(profile: str) -> str:
    if not _PROFILE_RE.fullmatch(str(profile)):
        raise SatkError("BAD_PARAMS", f"bad profile name {profile!r}")
    c = cfg()
    c.profile(profile)  # BAD_PARAMS with suggestions for unknown profiles
    return c.canonical_profile(str(profile))


def db_path(profile: str = "vanilla") -> Path:
    """``<work>/index/paths-<profile>.sqlite`` (aliases resolved; not created)."""
    return Path(os.path.abspath(cfg().paths.work)) / "index" / f"paths-{_canonical(profile)}.sqlite"


def _main_archives(profile: str):
    from ..formats.dat import resolve_ci
    from ..formats.layout import archives
    from ..formats.rw import FormatError

    p = cfg().profile(profile)
    if not p.configured:
        raise SatkError("NOT_FOUND", f"profile {profile!r}: game root not found: {jpath(p.root)}",
                        hint="satk config show --section profiles")
    try:
        specs = archives(p.root, list(p.dat), p.img_order)
    except FormatError as e:
        raise SatkError("NOT_FOUND", f"profile {profile!r}: {e}", hint="satk config show --section profiles") from None
    out = []
    for a in specs:
        if a.ns != "main":
            continue
        path = resolve_ci(p.root, a.relpath)
        out.append((a, path))
    return p, out


def is_fresh(meta: dict[str, str], profile: str) -> bool:
    """The database ``meta`` was imported from the profile's current root and archives."""
    return (meta.get("root") == jpath(cfg().profile(profile).root)
            and meta.get("sources") == json.dumps(source_signature(profile), sort_keys=True))


def source_signature(profile: str) -> dict[str, list[int]]:
    """``{archive relpath: [size, mtime_ns]}`` of the profile's ``main`` archives (missing ones skipped)."""
    _p, arcs = _main_archives(profile)
    sig: dict[str, list[int]] = {}
    for a, path in arcs:
        if path is None:
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        sig[a.relpath] = [st.st_size, st.st_mtime_ns]
    return sig


def locate_regions(profile: str = "vanilla") -> _Located:
    """Find ``nodes0.dat`` ... ``nodes63.dat`` in the profile's archives (first registered wins)."""
    from ..formats.img import ImgArchive
    from ..formats.rw import FormatError

    name = _canonical(profile)
    p, arcs = _main_archives(name)
    regions: dict[int, Region] = {}
    sources: dict[str, list[int]] = {}
    warnings: list[str] = []
    for a, path in arcs:
        if path is None or not path.is_file():
            continue
        st = os.stat(path)
        sources[a.relpath] = [st.st_size, st.st_mtime_ns]
        try:
            with ImgArchive.open(path) as arc:
                for e in arc.entries:
                    m = _NODES_RE.match(e.key)
                    if not m:
                        continue
                    n = int(m.group(1))
                    if n >= R.AREAS:
                        warnings.append(f"IGNORED: {a.relpath}/{e.name}: region {n} is outside 0..{R.AREAS - 1}")
                        continue
                    if n in regions:
                        continue  # an earlier archive (or entry) already provides this region
                    regions[n] = Region(n, a.relpath, e.name, arc.read(e))
        except FormatError as e:
            raise SatkError("UNSUPPORTED", f"cannot read archive {jpath(path)}: {e}") from None
    missing = [n for n in range(R.AREAS) if n not in regions]
    if len(missing) == R.AREAS:
        raise SatkError("NOT_FOUND", f"profile {name!r}: no nodes*.dat in any IMG archive of {jpath(p.root)}",
                        hint="the path regions normally live in models/gta3.img")
    if missing:
        warnings.append(f"MISSING: no nodes*.dat for region(s) {', '.join(map(str, missing))}")
    return _Located(name, p.root, p.img_order, regions, sources, warnings)


# --------------------------------------------------------------------------- import


def _decode_all(regions: dict[int, Region]):
    from .vendor import gtaflow

    gf = gtaflow()
    out = {}
    for n, reg in sorted(regions.items()):
        try:
            area = gf.codec.decode(reg.data)
        except gf.codec.TrafficError as e:
            raise SatkError("UNSUPPORTED", f"{reg.source}/{reg.entry}: {e}", data={"area": n}) from None
        for i, raw in enumerate(area.nodes):
            if int.from_bytes(raw[18:20], "little") != n:
                raise SatkError("UNSUPPORTED", f"{reg.source}/{reg.entry}: node {i} claims region "
                                               f"{int.from_bytes(raw[18:20], 'little')}", data={"area": n})
        out[n] = area
    return out


def _rows(n: int, area) -> tuple[list, list, list]:
    _total, vehicles, _peds, _nv, _nl = area.counts
    nodes = []
    for i, raw in enumerate(area.nodes):
        x = R.parse_node(raw, n, i, vehicles)
        nodes.append((n, i, x.kind, x.x, x.y, x.z, x.base, x.degree, x.width, x.flood, x.flags, x.spawn,
                      x.behaviour))
    navis = []
    for i, raw in enumerate(area.navis):
        v = R.parse_navi(raw, n, i)
        navis.append((n, i, v.x, v.y, v.at_area, v.at_idx, v.dx, v.dy, v.width, v.lanes_to, v.lanes_away,
                      v.light_dir, v.light, v.bridge))
    owner: dict[int, int] = {}
    for i, raw in enumerate(area.nodes):
        base = int.from_bytes(raw[16:18], "little", signed=True)
        for k in range(base, base + (raw[24] & 15)):
            owner.setdefault(k, i)
    links = []
    for k, (ta, ti) in enumerate(area.links):
        i = owner.get(k, -1)
        packed = area.navi_links[k]
        na, ni = R.navi_addr(packed) if 0 <= i < vehicles else (None, None)
        links.append((n, k, i, ta, ti, na, ni, area.distances[k], area.intersections[k]))
    return nodes, navis, links


def _replace(tmp: Path, dest: Path) -> None:
    deadline = time.monotonic() + 3.0
    delay = 0.01
    while True:
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise SatkError("BUSY", f"{jpath(dest)} is in use by another process",
                                hint="close other satk sessions reading the path database and retry") from None
            time.sleep(delay)
            delay = min(delay * 2, 0.1)


def _meta(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect("file:" + path.as_posix() + "?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                return None
            return dict(conn.execute("SELECT key, value FROM meta"))
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _summary(path: Path, meta: dict[str, str], *, reused: bool, seconds: float, warnings: list[str]) -> dict:
    counts = json.loads(meta.get("counts", "{}"))
    out = {"profile": meta.get("profile"), "db": jpath(path), "reused": reused, **counts,
           "sources": json.loads(meta.get("region_sources", "{}")),
           "check": meta.get("check"), "network_sha256": meta.get("network_sha256"),
           "seconds": round(seconds, 2)}
    if warnings:
        out["warn"] = warnings
    return out


def import_profile(profile: str = "vanilla", *, force: bool = False) -> dict:
    """Import the path regions of ``profile`` into :func:`db_path` (reused when the archives are unchanged).

    Returns a summary dict: ``db``, ``areas``, ``nodes``, ``vehicle``, ``car``, ``boat``, ``ped``,
    ``navis``, ``links``, ``sources``, ``check`` (independent oracle), ``reused``, ``seconds``.
    """
    t0 = time.perf_counter()
    name = _canonical(profile)
    dest = db_path(name)
    if not force:
        meta = _meta(dest)
        if meta is not None and is_fresh(meta, name):
            return _summary(dest, meta, reused=True, seconds=time.perf_counter() - t0,
                            warnings=json.loads(meta.get("warnings", "[]")))
    loc = locate_regions(name)
    areas = _decode_all(loc.regions)
    from .vendor import gtaflow

    oracle = gtaflow().oracle.check({n: r.data for n, r in loc.regions.items()})
    warnings = list(loc.warnings)
    if not oracle["valid"]:
        warnings.append(f"ORACLE: {len(oracle['errors'])} topology error(s), first: {oracle['errors'][0]}")
    check = f"{'valid' if oracle['valid'] else 'INVALID'}: {len(oracle['errors'])} errors, " \
            f"{len(oracle['warnings'])} warnings"

    ensure_writable(dest)
    work("index")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    counts = {"areas": 0, "nodes": 0, "vehicle": 0, "car": 0, "boat": 0, "ped": 0, "navis": 0, "links": 0}
    net = hashlib.sha256()
    try:
        conn = sqlite3.connect(tmp)
        try:
            conn.executescript(DDL)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            for n, area in sorted(areas.items()):
                reg = loc.regions[n]
                sha = hashlib.sha256(reg.data).hexdigest()
                net.update(f"{n}:{sha}\n".encode())
                total, veh, ped, nv, nl = area.counts
                conn.execute("INSERT INTO area VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             (n, reg.source, reg.entry, len(reg.data), sha, total, veh, ped, nv, nl,
                              len(area.trailing), reg.data))
                nodes, navis, links = _rows(n, area)
                conn.executemany("INSERT INTO node VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", nodes)
                conn.executemany("INSERT INTO navi VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", navis)
                conn.executemany("INSERT INTO link VALUES (?,?,?,?,?,?,?,?,?)", links)
                counts["areas"] += 1
                counts["nodes"] += total
                counts["vehicle"] += veh
                counts["ped"] += ped
                counts["navis"] += nv
                counts["links"] += nl
                counts["boat"] += sum(1 for r in nodes if r[2] == "boat")
            counts["car"] = counts["vehicle"] - counts["boat"]
            meta = {
                "schema": str(SCHEMA_VERSION), "profile": name, "root": jpath(loc.root), "img_order": loc.img_order,
                "sources": json.dumps(loc.sources, sort_keys=True),
                "region_sources": json.dumps({s: sum(1 for r in loc.regions.values() if r.source == s)
                                              for s in sorted({r.source for r in loc.regions.values()})}),
                "counts": json.dumps(counts), "check": check, "network_sha256": net.hexdigest(),
                "warnings": json.dumps(warnings),
            }
            conn.executemany("INSERT INTO meta VALUES (?,?)", sorted(meta.items()))
            conn.commit()
        finally:
            conn.close()
        _replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return _summary(dest, meta, reused=False, seconds=time.perf_counter() - t0, warnings=warnings)


# --------------------------------------------------------------------------- reading


class PathsDB:
    """Read-only access to one profile's path database (open per operation, close afterwards)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect("file:" + self.path.as_posix() + "?mode=ro", uri=True)
        self.conn.execute("PRAGMA query_only = 1")
        self.meta = dict(self.conn.execute("SELECT key, value FROM meta"))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "PathsDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- nodes and links

    def nodes_in_box(self, minx: float, miny: float, maxx: float, maxy: float,
                     kinds: Iterable[str] | None = None) -> list[sqlite3.Row]:
        """Nodes with ``minx <= x <= maxx`` and ``miny <= y <= maxy``, ordered by (area, idx)."""
        sql = "SELECT * FROM node WHERE x BETWEEN ? AND ? AND y BETWEEN ? AND ?"
        args: list = [minx, maxx, miny, maxy]
        ks = list(kinds or [])
        if ks:
            sql += f" AND kind IN ({','.join('?' * len(ks))})"
            args += ks
        cur = self.conn.execute(sql + " ORDER BY area, idx", args)
        cur.row_factory = sqlite3.Row
        return cur.fetchall()

    def node(self, area: int, idx: int) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM node WHERE area = ? AND idx = ?", (area, idx))
        cur.row_factory = sqlite3.Row
        return cur.fetchone()

    def nodes(self, keys: Iterable[tuple[int, int]]) -> dict[tuple[int, int], sqlite3.Row]:
        """Rows of several nodes by address (missing ones are absent)."""
        out: dict[tuple[int, int], sqlite3.Row] = {}
        for k in set(keys):
            r = self.node(*k)
            if r is not None:
                out[k] = r
        return out

    def links_of(self, area: int, idx: int) -> list[sqlite3.Row]:
        """Link entries of one node in file order."""
        cur = self.conn.execute("SELECT * FROM link WHERE area = ? AND node = ? ORDER BY k", (area, idx))
        cur.row_factory = sqlite3.Row
        return cur.fetchall()

    def navi(self, area: int, idx: int) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM navi WHERE area = ? AND idx = ?", (area, idx))
        cur.row_factory = sqlite3.Row
        return cur.fetchone()

    def navis_attached_to(self, area: int, idx: int) -> list[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM navi WHERE at_area = ? AND at_idx = ? ORDER BY area, idx",
                                (area, idx))
        cur.row_factory = sqlite3.Row
        return cur.fetchall()

    def area_blobs(self) -> dict[int, bytes]:
        """Original region bytes ``{area: data}`` as imported."""
        return {a: bytes(d) for a, d in self.conn.execute("SELECT area, data FROM area ORDER BY area")}

    def area_rows(self) -> list[sqlite3.Row]:
        cur = self.conn.execute("SELECT area, source, entry, size, sha256, nodes, vehicle, ped, navis, links, "
                                "trailing FROM area ORDER BY area")
        cur.row_factory = sqlite3.Row
        return cur.fetchall()


def open_paths(profile: str = "vanilla", *, auto_import: bool = True) -> tuple[PathsDB, list[str]]:
    """Open the path database of ``profile`` (``(db, warnings)``); imports it first when missing.

    ``warnings`` carries ``IMPORTED: ...`` after an automatic import and ``INDEX_STALE: ...`` when the
    archives changed after the import.
    """
    name = _canonical(profile)
    path = db_path(name)
    warnings: list[str] = []
    if _meta(path) is None:
        if not auto_import:
            raise SatkError("INDEX_MISSING", f"no path database for profile {name!r}: {jpath(path)}",
                            hint=f"satk paths import --profile {name}")
        res = import_profile(name)
        warnings.append(f"IMPORTED: {res['areas']} regions, {res['nodes']} nodes into {res['db']}")
        warnings += res.get("warn", [])
    db = PathsDB(path)
    try:
        if not is_fresh(db.meta, name):
            warnings.append(f"INDEX_STALE: the IMG archives of {name!r} changed after the import; "
                            f"satk paths import --profile {name}")
    except SatkError:
        pass  # the game root is gone: answer from the database
    return db, warnings


def dist2(x: float, y: float, z: float | None, row) -> float:
    """Distance from ``(x, y[, z])`` to a node row (2D when ``z`` is ``None``)."""
    d = (row["x"] - x) ** 2 + (row["y"] - y) ** 2
    if z is not None:
        d += (row["z"] - z) ** 2
    return math.sqrt(d)
