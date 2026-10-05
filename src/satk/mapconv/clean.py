"""``satk map clean``: remove the placements of an area together with their LODs (B4; report 29 №9, 30 G4).

Selection (from the asset index of a profile):

1. every placement whose position is inside the area (a box ``x0,y0,x1,y1``, a circle ``x,y,r`` or a
   zone by name, label or in-game title), in interior ``interior`` (``-1`` = all), optionally within
   ``z0,z1`` and restricted to some models;
2. the LOD parents of the selected placements (followed up the chain, wherever they stand);
3. an LOD is removed only when all of its HD children are removed: an LOD shared with a placement
   that stays is kept (``shared_lods=True`` removes it anyway). An LOD inside the area whose children
   are all outside stays too.

Outputs (all under ``work/out/mapconv/clean/<name>/``):

* ``modloader/<file>.ipl`` - full copies of every text and binary IPL that holds a removed placement,
  with the same number of ``inst`` lines in the same order: a removed line keeps its model, rotation,
  flags and LOD index and is moved underground (``z = -1000``). Deleting lines would shift the LOD
  indexes of the rest of the file and of the ``*_streamN`` files that point into it. Binary files are
  patched in place (only the ``z`` floats change), text files keep their bytes except the ``z`` token;
* ``<name>.lua`` - an MTA server script: ``removeWorldModel`` on resource start, ``restoreWorldModel``
  on stop (MTA matches model + 3D distance + interior and does not follow LOD links, so LODs get
  their own calls);
* ``<name>.pwn`` - SA-MP ``RemoveBuildingForPlayer`` calls in a ``stock`` to call from
  ``OnPlayerConnect`` (no interior parameter; SA-MP keeps about 1000 removals per player);
* ``report.json`` - the removed placements and the LOD check.

Calls of one model (and area code) are merged into one sphere when no placement of that model that
stays lies within the radius (checked in 2D against every placement of the model in the profile, so
both 2D and 3D matching are safe); otherwise the group is split, down to single placements with
a radius of at most 0.25 m.

LOD check: the copies are parsed back and compared row by row with the index (model, LOD index,
position; removed rows only differ in ``z``), and every remaining placement whose LOD would be gone
is counted (``lod_check.hidden_lods``).
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field

from ..core.errors import SatkError
from .scene import num

__all__ = ["HIDE_Z", "MAX_RADIUS_SINGLE", "Area", "Placement", "Call", "parse_area", "select", "removal_calls",
           "write_lua", "write_pawn", "hide_in_text", "hide_in_bnry", "check_copy", "CleanResult", "clean"]

HIDE_Z = -1000.0
MAX_RADIUS_SINGLE = 0.25
_PAGE = 500
_CHUNK = 400
_SEL = ("SELECT i.id, lower(p.name), p.kind, i.idx, i.model_id, i.x, i.y, i.z, i.area, i.iflags, i.lod_idx, i.lod_id, "
        "i.is_lod, i.ipl_id FROM inst i JOIN ipl p ON p.id = i.ipl_id ")
_TOKEN = re.compile(r"[^,\s]+")
_STEM = re.compile(r"^[a-z0-9_]+$")


def _rows(db, sql: str, params: list | tuple = ()) -> list[list]:
    """All rows of a read-only query, paged through ``IndexDB.query`` (500 rows per call)."""
    out: list[list] = []
    off = 0
    while True:
        env = db.query(f"{sql} LIMIT {_PAGE} OFFSET {off}", list(params), limit=_PAGE)
        rows = env["rows"]
        out.extend(rows)
        if len(rows) < _PAGE:
            return out
        off += _PAGE


def _chunks(seq, n: int = _CHUNK):
    seq = list(seq)
    for k in range(0, len(seq), n):
        yield seq[k:k + n]


# --------------------------------------------------------------------------- area


@dataclass
class Area:
    """``boxes`` (x0, y0, x1, y1) or ``circle`` (x, y, r) in world XY; ``label`` for file names."""

    label: str
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    circle: tuple[float, float, float] | None = None

    def contains(self, x: float, y: float) -> bool:
        if self.circle is not None:
            cx, cy, r = self.circle
            return math.hypot(x - cx, y - cy) <= r
        return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in self.boxes)

    def bbox(self) -> tuple[float, float, float, float]:
        if self.circle is not None:
            cx, cy, r = self.circle
            return cx - r, cy - r, cx + r, cy + r
        return (min(b[0] for b in self.boxes), min(b[1] for b in self.boxes),
                max(b[2] for b in self.boxes), max(b[3] for b in self.boxes))


def _floats(spec: str) -> list[float] | None:
    parts = [p for p in re.split(r"[,\s;]+", spec.strip()) if p]
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    return vals if all(math.isfinite(v) for v in vals) else None


def parse_area(spec: str, db=None) -> Area:
    """``x0,y0,x1,y1`` (box), ``x,y,r`` (circle) or a zone name/label/title of the index."""
    s = str(spec or "").strip()
    if not s:
        raise SatkError("BAD_PARAMS", "no area given", hint="satk map clean 2400,-1700,2500,-1600")
    vals = _floats(s)
    if vals is not None:
        if len(vals) == 4:
            x0, y0, x1, y1 = vals
            box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            if box[0] == box[2] or box[1] == box[3]:
                raise SatkError("BAD_PARAMS", f"empty box {s!r}", hint="x0,y0,x1,y1 with x0 != x1 and y0 != y1")
            return Area("box_" + "_".join(num(v, 0) for v in box), boxes=[box])
        if len(vals) == 3:
            if vals[2] <= 0:
                raise SatkError("BAD_PARAMS", f"radius must be positive: {s!r}")
            return Area("r_" + "_".join(num(v, 0) for v in vals), circle=(vals[0], vals[1], vals[2]))
        raise SatkError("BAD_PARAMS", f"area {s!r}: give 4 numbers (box x0,y0,x1,y1), 3 (circle x,y,r) or a zone name")
    if db is None:
        raise SatkError("INDEX_MISSING", "zone names need the asset index", hint="satk index build")
    rows = _rows(db, "SELECT name, minx, miny, maxx, maxy FROM zone WHERE name = ? COLLATE NOCASE OR "
                     "label = ? COLLATE NOCASE OR title = ? COLLATE NOCASE ORDER BY id", (s, s, s))
    if not rows:
        like = _rows(db, "SELECT DISTINCT coalesce(title, name) FROM zone WHERE name LIKE ? OR title LIKE ? "
                         "ORDER BY 1", (f"%{s}%", f"%{s}%"))
        raise SatkError("NOT_FOUND", f"no zone {s!r} in the index",
                        hint="a box x0,y0,x1,y1, a circle x,y,r or a zone name (satk index query "
                             "\"SELECT name, title FROM zone\")",
                        did_you_mean=[r[0] for r in like[:5]])
    label = re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_") or "zone"
    return Area(label, boxes=[(float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows])


# --------------------------------------------------------------------------- selection


@dataclass
class Placement:
    """One ``inst`` row of the index (``rid`` = ``inst.id``); ``role``: ``hd`` | ``lod`` | ``obj``."""

    rid: int
    ipl: str
    kind: str
    idx: int
    model: int
    pos: tuple[float, float, float]
    area: int
    iflags: int
    lod_idx: int
    lod_rid: int | None
    is_lod: bool
    ipl_id: int = 0
    role: str = ""
    name: str | None = None

    @property
    def sid(self) -> str:
        return f"inst:{self.ipl}#{self.idx}"


def _placement(r: list) -> Placement:
    return Placement(int(r[0]), str(r[1]), str(r[2]), int(r[3]), int(r[4]), (float(r[5]), float(r[6]), float(r[7])),
                     int(r[8]), int(r[9]), int(r[10]), None if r[11] is None else int(r[11]), bool(r[12]), int(r[13]))


def _by_rid(db, rids) -> dict[int, Placement]:
    out: dict[int, Placement] = {}
    for part in _chunks(sorted(set(rids))):
        for r in _rows(db, _SEL + f"WHERE i.id IN ({','.join('?' * len(part))}) ORDER BY i.id", part):
            out[int(r[0])] = _placement(r)
    return out


def _children(db, lod_rids) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for part in _chunks(sorted(set(lod_rids))):
        for r in _rows(db, f"SELECT id, lod_id FROM inst WHERE lod_id IN ({','.join('?' * len(part))}) ORDER BY id",
                       part):
            out.setdefault(int(r[1]), []).append(int(r[0]))
    return out


def resolve_models(db, models: list[str] | None) -> set[int] | None:
    """Model ids from ids and name patterns (``*``/``?`` wildcards); ``None`` = no filter."""
    if not models:
        return None
    ids: set[int] = set()
    for m in models:
        for tok in [t for t in re.split(r"[,\s]+", str(m)) if t]:
            if re.fullmatch(r"-?\d+", tok):
                ids.add(int(tok))
                continue
            pat = tok.replace("\\", "").replace("%", r"\%").replace("_", r"\_").replace("*", "%").replace("?", "_")
            hit = _rows(db, "SELECT id FROM v_model WHERE name LIKE ? ESCAPE '\\' ORDER BY id", (pat,))
            if not hit:
                raise SatkError("NOT_FOUND", f"no model matches {tok!r}", hint="model ids or names with * and ?")
            ids.update(int(r[0]) for r in hit)
    return ids


@dataclass
class Selection:
    removed: dict[int, Placement] = field(default_factory=dict)
    kept_shared: list[Placement] = field(default_factory=list)     # LOD parents kept because a child stays
    kept_area_lods: int = 0                                         # LODs in the area serving outside HDs
    candidates: int = 0


def select(db, area: Area, *, interior: int = 0, z: tuple[float, float] | None = None,
           models: set[int] | None = None, shared_lods: bool = False) -> Selection:
    """Placements to remove (see the module docstring)."""
    x0, y0, x1, y1 = area.bbox()
    sql = _SEL + "WHERE i.x BETWEEN ? AND ? AND i.y BETWEEN ? AND ?"
    params: list = [x0, x1, y0, y1]
    if interior >= 0:
        sql += " AND i.area = ?"
        params.append(int(interior))
    if z is not None:
        sql += " AND i.z BETWEEN ? AND ?"
        params += [min(z), max(z)]
    base = [_placement(r) for r in _rows(db, sql + " ORDER BY i.id", params)]
    base = [p for p in base if area.contains(p.pos[0], p.pos[1]) and (models is None or p.model in models)]
    sel = Selection(candidates=len(base))
    known: dict[int, Placement] = {p.rid: p for p in base}
    removed: set[int] = {p.rid for p in base if not p.is_lod}
    area_lods = {p.rid for p in base if p.is_lod}
    # LOD parents (up the chain), wherever they stand
    frontier = {p.lod_rid for p in base if p.lod_rid is not None}
    parents: set[int] = set()
    while frontier:
        new = frontier - parents - set(known)
        known.update(_by_rid(db, new))
        parents |= frontier
        frontier = {known[r].lod_rid for r in frontier if r in known and known[r].lod_rid is not None} - parents
    lods = parents | area_lods
    kids = _children(db, lods)
    changed = True
    while changed:  # fixpoint: an LOD goes when all (or, with shared_lods, any) of its children go
        changed = False
        for lod in sorted(lods - removed):
            ch = kids.get(lod, [])
            gone = [c in removed for c in ch]
            if ch and (all(gone) or (shared_lods and any(gone))):
                removed.add(lod)
                changed = True
    for rid in sorted(removed):
        p = known[rid]
        p.role = "lod" if p.is_lod else ("hd" if p.lod_rid is not None else "obj")
        sel.removed[rid] = p
    sel.kept_shared = [known[r] for r in sorted(parents - removed) if r in known]
    sel.kept_area_lods = len(area_lods - removed - parents)
    return sel


def model_names(db, ids) -> dict[int, str]:
    out: dict[int, str] = {}
    for part in _chunks(sorted(set(ids))):
        for r in _rows(db, f"SELECT id, name FROM v_model WHERE id IN ({','.join('?' * len(part))}) ORDER BY id", part):
            out[int(r[0])] = str(r[1])
    return out


# --------------------------------------------------------------------------- removal calls


@dataclass
class Call:
    """One ``removeWorldModel``/``RemoveBuildingForPlayer``: model, centre, radius, area, what it removes."""

    model: int
    pos: tuple[float, float, float]
    radius: float
    area: int
    removes: list[Placement]

    @property
    def lod(self) -> bool:
        return all(p.is_lod for p in self.removes)


def _all_positions(db, model: int) -> list[tuple[int, float, float, float]]:
    return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]))
            for r in _rows(db, "SELECT id, x, y, z FROM inst WHERE model_id = ? ORDER BY id", (model,))]


def _sphere(pts: list[Placement]) -> tuple[tuple[float, float, float], float]:
    xs, ys, zs = zip(*(p.pos for p in pts))
    c = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    c = tuple(round(v, 3) for v in c)
    return c, max(math.dist(c, p.pos) for p in pts)  # type: ignore[return-value]


def _cover(pts: list[Placement], others: list[tuple[float, float]], warn: list[str]) -> list[tuple]:
    """Spheres (centre, radius, placements) covering ``pts`` and no point of ``others`` (2D test).

    A sub-group's sphere lies within 3 radii of its parent's centre, so only those ``others`` are passed down.
    """
    if len(pts) == 1:
        p = pts[0]
        c = tuple(round(v, 3) for v in p.pos)
        near = min((math.hypot(c[0] - ox, c[1] - oy) for ox, oy in others), default=math.inf)
        r = min(MAX_RADIUS_SINGLE, near / 2)
        if r < 0.01:
            warn.append(f"TWIN_KEPT: {p.sid} (model {p.model}) has a twin that stays within {num(near, 3)} m; "
                        "radius-based removal may take both")
            r = 0.01
        return [(c, round(r, 3), pts)]
    c, r = _sphere(pts)
    r = round(r + 0.05, 2)
    others = [(ox, oy) for ox, oy in others if math.hypot(c[0] - ox, c[1] - oy) <= 3 * r + 1.0]
    if all(math.hypot(c[0] - ox, c[1] - oy) > r + 0.05 for ox, oy in others):
        return [(c, r, pts)]
    xs = [p.pos[0] for p in pts]
    ys = [p.pos[1] for p in pts]
    axis = 0 if max(xs) - min(xs) >= max(ys) - min(ys) else 1
    srt = sorted(pts, key=lambda p: (p.pos[axis], p.rid))
    h = len(srt) // 2
    return _cover(srt[:h], others, warn) + _cover(srt[h:], others, warn)


def removal_calls(db, sel: Selection, warn: list[str] | None = None, merge: bool = True) -> list[Call]:
    """Calls removing exactly the selected placements (merged per model and area when safe)."""
    warn = warn if warn is not None else []
    groups: dict[tuple[int, int], list[Placement]] = {}
    for p in sel.removed.values():
        groups.setdefault((p.model, p.area), []).append(p)
    calls: list[Call] = []
    cache: dict[int, list] = {}
    for (model, area), pts in sorted(groups.items()):
        if model not in cache:
            cache[model] = _all_positions(db, model)
        others = [(x, y) for rid, x, y, _z in cache[model] if rid not in sel.removed]
        pts = sorted(pts, key=lambda p: p.rid)
        spheres = _cover(pts, others, warn) if merge else [_cover([p], others, warn)[0] for p in pts]
        for c, r, members in spheres:
            calls.append(Call(model, c, r, area, members))
    calls.sort(key=lambda c: (c.lod, c.model, c.pos))
    return calls


def _fmt(v: float) -> str:
    return num(v, 3)


def write_lua(calls: list[Call], names: dict[int, str], header: str) -> str:
    """MTA server script (removeWorldModel on start, restoreWorldModel on stop)."""
    out = [f"-- {h}" for h in header.splitlines()]
    out += ["-- MTA server script: removes the buildings on resource start and restores them on stop.",
            "-- {model, radius, x, y, z, interior}; LODs are separate entries (MTA does not follow LOD links).",
            "local removals = {"]
    for c in calls:
        what = f"{'LOD ' if c.lod else ''}{names.get(c.model, c.model)}" + \
               (f" x{len(c.removes)}" if len(c.removes) > 1 else f" {c.removes[0].sid}")
        out.append(f"    {{{c.model}, {_fmt(c.radius)}, {_fmt(c.pos[0])}, {_fmt(c.pos[1])}, {_fmt(c.pos[2])}, "
                   f"{c.area}}}, -- {what}")
    out += ["}", "",
            'addEventHandler("onResourceStart", resourceRoot, function()',
            "    for _, r in ipairs(removals) do removeWorldModel(r[1], r[2], r[3], r[4], r[5], r[6]) end",
            "end)", "",
            'addEventHandler("onResourceStop", resourceRoot, function()',
            "    for _, r in ipairs(removals) do restoreWorldModel(r[1], r[2], r[3], r[4], r[5], r[6]) end",
            "end)"]
    return "\n".join(out) + "\n"


def write_pawn(calls: list[Call], names: dict[int, str], header: str, func: str) -> str:
    """SA-MP ``stock`` with ``RemoveBuildingForPlayer`` lines (call it from OnPlayerConnect)."""
    out = [f"// {h}" for h in header.splitlines()]
    out += ["// SA-MP: call from OnPlayerConnect. RemoveBuildingForPlayer has no interior parameter; SA-MP keeps",
            "// about 1000 removals per player. LODs are separate lines.",
            f"stock {func}(playerid)", "{"]
    for c in calls:
        what = f"{'LOD ' if c.lod else ''}{names.get(c.model, c.model)}" + \
               (f" x{len(c.removes)}" if len(c.removes) > 1 else "")
        out.append(f"    RemoveBuildingForPlayer(playerid, {c.model}, {_fmt(c.pos[0])}, {_fmt(c.pos[1])}, "
                   f"{_fmt(c.pos[2])}, {_fmt(c.radius)}); // {what}")
    out += ["    return 1;", "}"]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- IPL copies


def _inst_ok(f: list[str]) -> bool:
    """The line parses as an ``inst`` row exactly like :func:`satk.formats.ipl.parse_ipl_text` counts it."""
    if len(f) < 10:
        return False
    try:
        int(f[0])
        int(f[2])
        [float(v) for v in f[3:10]]
        if len(f) > 10:
            int(f[10])
    except (ValueError, OverflowError):
        return False
    return True


def inst_lines(text: str) -> list[int]:
    """1-based line number of every ``inst`` row, in index order."""
    from ..formats.ide import iter_sections

    return [n for sec, f, n in iter_sections(text) if sec == "inst" and _inst_ok(f)]


def hide_in_text(data: bytes, idxs: set[int]) -> tuple[bytes, int]:
    """Text IPL bytes with the ``z`` token of the given ``inst`` rows set to :data:`HIDE_Z`."""
    text = data.decode("latin-1")
    rows = inst_lines(text)
    bad = sorted(i for i in idxs if i >= len(rows))
    if bad:
        raise SatkError("REVISION", f"inst #{bad[0]} is not in the file ({len(rows)} rows): the index is stale",
                        hint="satk index build")
    lines = text.splitlines(keepends=True)
    zt = num(HIDE_Z)
    for i in sorted(idxs):
        n = rows[i] - 1
        raw = lines[n]
        body_end = len(raw.rstrip("\r\n"))
        hash_at = raw.find("#")
        stop = body_end if hash_at < 0 else min(hash_at, body_end)
        toks = list(_TOKEN.finditer(raw[:stop]))
        a, b = toks[5].span()
        lines[n] = raw[:a] + zt + raw[b:]
    return "".join(lines).encode("latin-1"), len(idxs)


def hide_in_bnry(data: bytes, idxs: set[int]) -> tuple[bytes, int]:
    """``bnry`` bytes with the ``z`` float of the given ``inst`` records set to :data:`HIDE_Z`."""
    from .bnry import parse_bnry

    info = parse_bnry(data)
    off = struct.unpack_from("<I", data, 28)[0]  # inst table offset (header field 7)
    bad = sorted(i for i in idxs if i >= len(info.insts))
    if bad:
        raise SatkError("REVISION", f"inst #{bad[0]} is not in the file ({len(info.insts)} records): the index is stale",
                        hint="satk index build")
    buf = bytearray(data)
    for i in idxs:
        struct.pack_into("<f", buf, off + 40 * i + 8, HIDE_Z)
    return bytes(buf), len(idxs)


def _parsed(data: bytes, kind: str) -> list[tuple[int, int, int, tuple[float, float, float]]]:
    """``(model, lod, interior, pos)`` per inst row of a text or binary IPL."""
    if kind == "binary":
        from .bnry import parse_bnry

        return [(r[7], r[9], r[8] & 0xFFFFFFFF, (r[0], r[1], r[2])) for r in parse_bnry(data).insts]
    from ..formats.ipl import parse_ipl_text

    insts, _ = parse_ipl_text(data.decode("latin-1"))
    return [(i.model_id, i.lod, i.interior & 0xFFFFFFFF, tuple(i.pos)) for i in insts]  # type: ignore[misc]


def check_copy(db, ipl: str, ipl_id: int, kind: str, before: bytes, after: bytes, hidden: set[int]) -> dict:
    """Compare the copy with the original and the index: ``{rows, kept, hidden, problems}``."""
    old = _parsed(before, kind)
    new = _parsed(after, kind)
    idx_rows = _rows(db, "SELECT idx, model_id, lod_idx FROM inst WHERE ipl_id = ? ORDER BY idx", (ipl_id,))
    problems: list[str] = []
    if len(new) != len(old):
        problems.append(f"{ipl}: {len(new)} rows after, {len(old)} before")
    if len(idx_rows) != len(old):
        problems.append(f"{ipl}: the index has {len(idx_rows)} rows, the file {len(old)} (stale index?)")
    for (i, model, lod), o in zip(idx_rows, old):
        if (int(model), int(lod)) != (o[0], o[1]):
            problems.append(f"{ipl}#{i}: index model/lod {model}/{lod}, file {o[0]}/{o[1]}")
            break
    for i, (o, n) in enumerate(zip(old, new)):
        same_head = (o[0], o[1], o[2]) == (n[0], n[1], n[2])
        if i in hidden:
            ok = same_head and o[3][:2] == n[3][:2] and n[3][2] == HIDE_Z
        else:
            ok = same_head and o[3] == n[3]
        if not ok:
            problems.append(f"{ipl}#{i}: row changed unexpectedly")
    return {"rows": len(new), "kept": len(new) - len(hidden), "hidden": len(hidden), "problems": problems}


# --------------------------------------------------------------------------- driver


@dataclass
class CleanResult:
    area: Area
    selection: Selection
    calls: list[Call]
    names: dict[int, str]
    copies: dict[str, tuple[str, bytes, int]]  # ipl name -> (file name, bytes, rows hidden)
    lod_check: dict
    warn: list[str]


def clean(db, area: Area, *, profile: str, interior: int = 0, z: tuple[float, float] | None = None,
          models: set[int] | None = None, shared_lods: bool = False, want_ipl: bool = True,
          merge: bool = True) -> CleanResult:
    """Select, build the calls and (``want_ipl``) the patched IPL copies with their LOD check."""
    from .convert import load_source

    warn: list[str] = []
    sel = select(db, area, interior=interior, z=z, models=models, shared_lods=shared_lods)
    names = model_names(db, {p.model for p in sel.removed.values()})
    for p in sel.removed.values():
        p.name = names.get(p.model)
    calls = removal_calls(db, sel, warn, merge=merge)
    copies: dict[str, tuple[str, bytes, int]] = {}
    check = {"files": 0, "rows": 0, "kept": 0, "hidden": 0, "hidden_lods": 0, "bad": 0}
    problems: list[str] = []
    if want_ipl and sel.removed:
        by_ipl: dict[str, set[int]] = {}
        kinds: dict[str, tuple[str, int]] = {}
        for p in sel.removed.values():
            by_ipl.setdefault(p.ipl, set()).add(p.idx)
            kinds[p.ipl] = (p.kind, p.ipl_id)
        for ipl in sorted(by_ipl):
            src = load_source(f"ipl:{ipl}", profile, db=db)
            kind, ipl_id = kinds[ipl]
            hide = hide_in_bnry if kind == "binary" else hide_in_text
            data, n = hide(src.data, by_ipl[ipl])
            fname = src.path.name if src.path is not None else f"{ipl}.ipl"
            copies[ipl] = (fname, data, n)
            c = check_copy(db, ipl, ipl_id, kind, src.data, data, by_ipl[ipl])
            problems += c.pop("problems")
            check["files"] += 1
            for k in ("rows", "kept", "hidden"):
                check[k] += c[k]
        # kept placements whose LOD parent is hidden (only possible with shared_lods)
        lod_rids = {p.rid for p in sel.removed.values() if p.is_lod}
        kids = _children(db, lod_rids)
        check["hidden_lods"] = sum(1 for lod, ch in kids.items() for c in ch if c not in sel.removed)
    check["bad"] = len(problems)
    if problems:
        check["problems"] = problems[:10]
    if sel.kept_shared:
        warn.append(f"LOD_SHARED: {len(sel.kept_shared)} LOD placements also serve placements outside the "
                    f"selection and stay (e.g. {sel.kept_shared[0].sid}); --shared-lods removes them too")
    if check["hidden_lods"]:
        warn.append(f"LOD_HIDDEN: {check['hidden_lods']} remaining placements lose their LOD (--shared-lods)")
    return CleanResult(area, sel, calls, names, copies, check, warn)
