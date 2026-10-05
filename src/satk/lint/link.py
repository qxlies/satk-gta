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
from .dff import DffFacts, ModelDef, model_class
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
        try:
            while True:
                env = self.db.query("SELECT name, id, sec, txd, draw FROM v_model ORDER BY id LIMIT ? OFFSET ?",
                                    [page, off], limit=page)
                rows = env.get("rows") or []
                for name, mid, sec, txd, draw in rows:
                    if name:
                        models[str(name).lower()] = {"id": f"model:{mid}", "name": name, "sec": sec, "txd": txd,
                                                     "draw": draw}
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


def check_links(c: Collector, cat: Catalog, ix: IndexView, *, skip_loose_orphans: bool = False) -> None:
    """Run the ``link.*`` rules over the catalog (and the index for names outside the set).

    Definitions are not linked when nothing can answer for their files: no DFF/TXD/COL in the target and no
    index (a lone IDE file linted with ``--no-index``).
    """
    if not (ix.available or cat.dff_list or cat.txd_list or cat.col_list):
        return
    min_gap = float(c.rules.param("link.col_offset", "min_gap", 5.0))
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
        if d.sec in ("objs", "tobj") and model_class(c.rules.classes, d, None) == "map" and cbox is None:
            c.add("link.col_missing", where, id=d.id, name=d.name, sec=d.sec)
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
