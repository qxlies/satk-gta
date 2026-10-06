"""Link checks (``link.*`` rules): IDE definition <-> DFF / TXD chain / COL by name, orphans, pivots.

Names are matched case-insensitively, like the engine (``CKeyGen::GetUppercaseKey``). Everything is looked
up in the linted set first; with an :class:`IndexView` the profile's index answers for names the set does
not contain (a mod that reuses vanilla TXDs or adds collision for vanilla models).

TXD chain of a model = own TXD, then ``txdp`` parents, then ``vehicle`` for ``cars``
(the rule of ``satk.index.resolve.txd_parents``, ``FileLoader.cpp:1834``).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from ..core.errors import SatkError
from ..core.ids import Sid
from .col import ColFacts
from .dff import MAP_LIKE, DffFacts, ModelDef, model_class
from .rules import Collector
from .txd import TxdFacts

__all__ = ["IndexView", "Catalog", "check_links"]


class IndexView:
    """Cached name lookups in a profile index; every method answers ``None`` when there is no index."""

    def __init__(self, db=None):
        self.db = db
        self._models: dict[str, dict | None] = {}
        self._blobs: dict[str, bool] = {}
        self._tex: dict[str, frozenset | None] = {}
        self._parent: dict[str, str | None] = {}
        self._cols: dict[str, tuple | None] = {}
        self._bulk_txds: set[str] | None = None
        self._frames: dict[str, frozenset | None] = {}
        self._alpha: dict[str, frozenset | None] = {}
        self._prims: dict[str, int | None] = {}

    def preload(self, page: int = 500) -> bool:
        """Load every model name of the index in a few paged queries (for large targets).

        Afterwards :meth:`model` answers from memory (absent names cost nothing). Returns ``False`` (and keeps
        the per-name lookups) when the index has no ``v_model`` view.
        """
        if self.db is None:
            return False
        if self._bulk_txds is not None:
            return True
        models: dict[str, dict] = {}
        txds: set[str] = set()
        off = 0
        sql = ("SELECT m.name, m.id, m.sec, m.txd, m.draw, m.flags, m.extra, s.relpath FROM model m "
               "JOIN ide i ON i.id = m.ide_id JOIN source s ON s.id = i.source_id WHERE m.active = 1 "
               "ORDER BY m.id LIMIT ? OFFSET ?")
        try:
            while True:
                env = self.db.query(sql, [page, off], limit=page)
                rows = env.get("rows") or []
                for name, mid, sec, txd, draw, flags, extra, ide in rows:
                    if name:
                        models[str(name).lower()] = {"id": f"model:{mid}", "name": name, "sec": sec, "txd": txd,
                                                     "draw": draw, "flags": flags, "extra": extra,
                                                     "links": {"ide": f"ide:{ide}"} if ide else {}}
                    if txd:
                        txds.add(str(txd).lower())
                if len(rows) < page:
                    break
                off += page
        except (SatkError, ValueError, TypeError):
            return False
        self._models.update(models)
        self._loaded_all = True
        self._bulk_txds = txds
        return True

    def txd_used(self, stem: str) -> bool:
        """True if some index model uses TXD ``stem`` directly or through a ``txdp`` child."""
        if self.db is None:
            return False
        k = stem.lower()
        if self._bulk_txds is not None and k in self._bulk_txds:
            return True
        sid = self._sid("txd", k)
        if not sid:
            return False
        try:
            rels = self.db.refs(sid).get("rels") or {}
        except SatkError:
            return False
        return bool(rels.get("models") or rels.get("children"))

    @property
    def available(self) -> bool:
        return self.db is not None

    @staticmethod
    def _sid(kind: str, key: str) -> str | None:
        try:
            return str(Sid(kind, key.lower()))
        except SatkError:
            return None

    def model(self, name: str) -> dict | None:
        """``asset get model:<name>`` of the index (``None`` if absent)."""
        k = name.lower()
        if self.db is None:
            return None
        if k not in self._models and getattr(self, "_loaded_all", False):
            return None
        if k not in self._models:
            sid = self._sid("model", k)
            try:
                self._models[k] = self.db.get(sid) if sid else None
            except SatkError:
                self._models[k] = None
        return self._models[k]

    def has_blob(self, kind: str, name: str) -> bool | None:
        if self.db is None:
            return None
        k = f"{kind}:{name.lower()}"
        if k not in self._blobs:
            sid = self._sid(kind, name)
            try:
                self._blobs[k] = bool(sid) and self.db.blob_ref(sid) is not None
            except SatkError:
                self._blobs[k] = False
        return self._blobs[k]

    def txd_textures(self, name: str) -> frozenset | None:
        """Lower-case texture names of ``txd:<name>`` in the index (``None`` if absent or no index)."""
        if self.db is None:
            return None
        k = name.lower()
        if k not in self._tex:
            sid = self._sid("txd", k)
            try:
                refs = self.db.textures_of(sid) if sid else None
                self._tex[k] = None if refs is None else frozenset(
                    r.sid.split("/", 1)[1].split("@", 1)[0].lower() for r in refs if "/" in r.sid)
            except SatkError:
                self._tex[k] = None
        return self._tex[k]

    def txd_parent(self, name: str) -> str | None:
        """``txdp`` parent of a TXD in the index (not the implicit ``vehicle``)."""
        if self.db is None:
            return None
        k = name.lower()
        if k not in self._parent:
            sid = self._sid("txd", k)
            par = None
            try:
                env = self.db.refs(sid, rel="parent", limit=5) if sid else None
                for row in (env or {}).get("rows") or []:
                    if len(row) >= 3 and row[2] != "vehicle":
                        par = str(row[1]).lower()
                        break
            except SatkError:
                par = None
            self._parent[k] = par
        return self._parent[k]

    def col_bbox(self, name: str) -> tuple | None:
        """``(bbox,)`` of ``col:<name>`` in the index, ``()`` if it has no bbox, ``None`` if absent."""
        if self.db is None:
            return None
        k = name.lower()
        if k not in self._cols:
            sid = self._sid("col", k)
            try:
                env = self.db.get(sid) if sid else None
                self._cols[k] = None if env is None else ((tuple(env["bbox"]),) if env.get("bbox") else ())
            except SatkError:
                self._cols[k] = None
        return self._cols[k]


    def _rows(self, sql: str, params: list, limit: int = 500) -> list | None:
        try:
            return self.db.query(sql, params, limit=limit).get("rows") or []
        except (SatkError, ValueError, TypeError):
            return None

    def ref_frames(self, dff: str) -> frozenset | None:
        """Lower-case frame names of ``dff:<name>`` in the index (``None`` if absent or no index): the
        reference a replacement model is compared with (upgrade frames, muzzle flash)."""
        if self.db is None:
            return None
        k = dff.lower()
        if k not in self._frames:
            rows = self._rows("SELECT f.name FROM dff_frame f JOIN dff d ON d.id = f.dff_id WHERE d.name = ?", [k])
            self._frames[k] = None if not rows else frozenset(str(r[0]).lower() for r in rows if r[0])
        return self._frames[k]

    def txd_alpha(self, name: str) -> frozenset | None:
        """Lower-case names of the textures of ``txd:<name>`` with blended alpha (``None``: unknown)."""
        if self.db is None:
            return None
        k = name.lower()
        if k not in self._alpha:
            from .txd import BLEND_ALPHA

            fmts = sorted(BLEND_ALPHA)
            rows = self._rows("SELECT t.name FROM texture t JOIN txd x ON x.id = t.txd_id WHERE x.name = ? AND "
                              f"t.alpha = 1 AND t.d3dfmt IN ({','.join('?' * len(fmts))})", [k, *fmts])
            self._alpha[k] = None if rows is None else frozenset(str(r[0]).lower() for r in rows)
        return self._alpha[k]

    def txd_users(self, name: str, limit: int = 50) -> list[ModelDef]:
        """Definitions of the index models whose TXD is ``name`` (for the class of a TXD)."""
        if self.db is None:
            return []
        rows = self._rows("SELECT m.id, m.name, m.sec, m.draw, m.flags, s.relpath FROM model m JOIN ide i ON "
                          "i.id = m.ide_id JOIN source s ON s.id = i.source_id WHERE m.txd = ? AND m.active = 1 "
                          "ORDER BY m.id LIMIT ?", [name.lower(), limit], limit=limit) or []
        return [ModelDef(int(mid), str(nm), name, str(sec), draw, "index", flags=flags, ide=str(ide or "").lower())
                for mid, nm, sec, draw, flags, ide in rows]

    def col_prims(self, name: str) -> int | None:
        """Spheres + boxes + faces of the active collision model ``name`` (``None`` if absent)."""
        if self.db is None:
            return None
        k = name.lower()
        if k not in self._prims:
            rows = self._rows("SELECT spheres + boxes + faces FROM col WHERE name = ? AND active = 1", [k])
            self._prims[k] = int(rows[0][0] or 0) if rows else None
        return self._prims[k]


@dataclass
class Catalog:
    """Everything the link checks know about the linted set."""

    defs: list[ModelDef] = field(default_factory=list)
    #: Index definitions of DFFs in the set that no IDE of the set defines (a mod replacing infernus.dff):
    #: link-checked like ``defs`` but never part of the IDE-set checks (no false ID conflicts).
    index_defs: list[ModelDef] = field(default_factory=list)
    txdp: dict[str, str] = field(default_factory=dict)            # child -> parent (lower)
    dffs: dict[str, DffFacts] = field(default_factory=dict)       # stem -> first DFF
    txds: dict[str, TxdFacts] = field(default_factory=dict)       # stem -> first TXD
    cols: dict[str, ColFacts] = field(default_factory=dict)       # model name -> first COL model
    col_list: list[ColFacts] = field(default_factory=list)        # every non-embedded COL model
    dff_list: list[DffFacts] = field(default_factory=list)
    txd_list: list[TxdFacts] = field(default_factory=list)

    def add_dff(self, f: DffFacts) -> None:
        self.dff_list.append(f)
        self.dffs.setdefault(f.stem, f)

    def add_txd(self, f: TxdFacts) -> None:
        self.txd_list.append(f)
        self.txds.setdefault(f.stem, f)

    def add_col(self, c: Collector, f: ColFacts) -> None:
        if f.embedded or not f.name:
            return
        self.col_list.append(f)
        k = f.name.lower()
        prev = self.cols.get(k)
        if prev is None:
            self.cols[k] = f
        else:
            c.add("col.dup_name", f.label, name=f.name, other=prev.label)


def _placeholder(name: str, pats: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in pats)


def _gap(a, b) -> float:
    """Distance between two AABBs (0 when they touch or overlap)."""
    d2 = 0.0
    for i in range(3):
        d = max(a[i] - b[3 + i], b[i] - a[3 + i], 0.0)
        d2 += d * d
    return d2 ** 0.5


def _chain(cat: Catalog, ix: IndexView, d: ModelDef) -> list[str]:
    if d.chain is not None:
        return list(d.chain)
    out: list[str] = []
    t = (d.txd or "").lower()
    while t and t != "null" and t not in out:
        out.append(t)
        t = cat.txdp.get(t) or (ix.txd_parent(t) if t not in cat.txds else None) or ""
    if d.sec == "cars" and "vehicle" not in out:
        out.append("vehicle")
    return out


def _textures_of(cat: Catalog, ix: IndexView, txd: str) -> frozenset | None:
    f = cat.txds.get(txd)
    if f is not None:
        return f.textures
    return ix.txd_textures(txd)


def _bigbuilding(c: Collector, d: ModelDef, col, ix: IndexView, where: str, lod_name: str) -> None:
    """``ide.draw_bigbuilding``: a non-LOD model with collision whose draw distance makes it a big building
    (``LODDistMultiplier (1.0) x draw > 300``: the game turns its collision off, FileLoader.cpp:1992)."""
    if lod_name and lod_name in d.name.lower():
        return
    prims = col.prims if col is not None else ix.col_prims(d.name)
    if not prims:
        return
    c.seen("ide.draw_bigbuilding")
    lim = float(c.rules.param("ide.draw_bigbuilding", "max_draw", 300.0))
    if d.draw is not None and d.draw > lim:
        line = d.origin.rpartition(":")[2]
        c.add("ide.draw_bigbuilding", where, line=line, id=d.id, name=d.name, draw=d.draw)


def _alpha_order(c: Collector, d: ModelDef, dff: DffFacts) -> None:
    """``mat.alpha_draw_last``: a map model with semi-transparent material colours (alpha < 255: always
    blended, unlike texture alpha that is mostly alpha-tested cut-outs) and neither the draw_last (4) nor the
    additive (8) IDE flag."""
    if d.flags is None or dff.cls not in MAP_LIKE or not dff.mat_alpha:
        return
    c.seen("mat.alpha_draw_last")
    if not d.flags & (4 | 8):
        c.add("mat.alpha_draw_last", dff.label, id=d.id, name=d.name, flags=d.flags)


def check_links(c: Collector, cat: Catalog, ix: IndexView, *, skip_loose_orphans: bool = False) -> None:
    """Run the ``link.*`` rules over the catalog (and the index for names outside the set).

    Definitions are not linked when nothing can answer for their files: no DFF/TXD/COL in the target and no
    index (a lone IDE file linted with ``--no-index``).
    """
    if not (ix.available or cat.dff_list or cat.txd_list or cat.col_list):
        return
    min_gap = float(c.rules.param("link.col_offset", "min_gap", 5.0))
    lod_name = str(c.rules.classes.get("lod_name", "lod")).lower()
    dff_ph = [str(x).lower() for x in c.rules.param("link.dff_missing", "placeholders", []) or []]
    txd_ph = [str(x).lower() for x in c.rules.param("link.txd_missing", "placeholders", []) or []]
    builtin = {str(x).lower() for x in c.rules.param("link.txd_missing", "builtin", []) or []}
    known: set[str] = set()
    used_txd: set[str] = set(cat.txdp.values())
    for d in [*cat.defs, *cat.index_defs]:
        name = d.name.lower()
        known.add(name)
        where = d.origin.rpartition(":")[0] if d.origin != "index" else f"model:{d.id}"
        dff = cat.dffs.get(name)
        if dff is None and not _placeholder(name, dff_ph) and not ix.has_blob("dff", name):
            c.add("link.dff_missing", where, id=d.id, name=d.name)
        txd = (d.txd or "").lower()
        if txd and txd != "null":
            used_txd.add(txd)
            if txd not in cat.txds and txd not in builtin and not _placeholder(name, txd_ph) \
                    and not ix.has_blob("txd", txd):
                c.add("link.txd_missing", where, id=d.id, name=d.name, txd=d.txd)
        if dff is not None and dff.textures:
            chain = _chain(cat, ix, d)
            used_txd.update(chain)
            sets = [_textures_of(cat, ix, t) for t in chain]
            if chain and all(s is not None for s in sets):
                have = frozenset().union(*sets)
                miss = sorted({t for t in dff.textures if t not in have})
                if miss:
                    c.add("link.texture_missing", dff.label, id=d.id, name=d.name, n=len(miss), tex=miss[:8],
                          chain="+".join(chain))
        col = cat.cols.get(name)
        cbox = (col.bbox,) if col is not None else ix.col_bbox(name)
        cls = model_class(c.rules.classes, d, None, archive=dff.archive if dff is not None else None)
        if d.sec in ("objs", "tobj") and cls in MAP_LIKE and cbox is None:
            c.add("link.col_missing", where, id=d.id, name=d.name, sec=d.sec)
        if d.sec in ("objs", "tobj") and d.origin != "index":
            _bigbuilding(c, d, col, ix, where, lod_name)
            if dff is not None:
                _alpha_order(c, d, dff)
        if dff is not None and dff.bbox is not None and cbox:
            g = _gap(dff.bbox, cbox[0])
            if g > min_gap:
                c.add("link.col_offset", dff.label, id=d.id, name=d.name, d=round(g, 1))
    have_models = bool(cat.defs) or ix.available
    if not have_models:
        return
    for f in cat.col_list:
        k = f.name.lower()
        if k not in known and ix.model(k) is None:
            c.add("link.col_orphan", f.label, name=f.name)
    ign_dff = {s.lower() for s in c.rules.param("link.dff_orphan", "ignore_archives", []) or []}
    for f in cat.dff_list:
        if (f.archive or "") in ign_dff or (skip_loose_orphans and f.loose):
            continue
        if f.stem not in known and ix.model(f.stem) is None:
            c.add("link.dff_orphan", f.label)
    ign_txd = {s.lower() for s in c.rules.param("link.txd_orphan", "ignore_archives", []) or []}
    for f in cat.txd_list:
        if (f.archive or "") in ign_txd or (skip_loose_orphans and f.loose):
            continue
        if f.stem not in used_txd and not ix.txd_used(f.stem):
            c.add("link.txd_orphan", f.label)
