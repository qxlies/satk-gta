"""``satk index build``: the full pipeline of SPEC §4.3.2 for one load profile. Owner: WP-03.

1. **discover/attribute** (:mod:`.layers`): sources, IMG registration order, DAT directives,
   loose assets, layer of every file;
2. **scan** (:mod:`.scan`, worker processes via :func:`satk.core.procpool.pool`, in-process when processes
   cannot start): IMG directories -> blobs; TXD/DFF/COL/IFP/bnry payloads parsed in parallel, in chunks per archive;
3. **resolve** (:mod:`.resolve`): blob winners, active IDE definitions, active COL, TXD parents;
4. **link** (:mod:`.link`): ``model_link``/``model_tex``, IPL parents, LOD, world AABB + R-tree;
   **data files** (:mod:`.gamedata`, schema v3): ``water.dat``, ``timecyc.dat``, ``carcols.dat``,
   ``handling.cfg``, ``ped.dat``, ``object.dat``;
5. **search** (:mod:`.search`): ``fts_name``;
6. **finalize**: ``meta`` (incl. ``content_hash`` and the source signature for freshness checks),
   ``ANALYZE``, build into a temp file, then atomic replace. On Windows a database another
   process holds open cannot be replaced; then the new content is copied INTO the existing file
   with the SQLite backup API (readers see it on their next transaction, the mtime changes and
   :func:`satk.index.api.open_index` reopens).

Everything is inserted in a deterministic order, so two builds of the same files give the same
``content_hash`` (:func:`content_hash`, ``satk index hash``).

Game files are only read (``open_ro``). Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import __version__ as SATK_VERSION
from ..core.errors import SatkError
from ..core.paths import cfg, ensure_writable, jpath
from ..core.procpool import fallback_reason, pool
from ..core.registry import report_progress
from ..formats.dat import read_text
from ..formats.ide import parse_ide
from ..formats.img import ImgArchive
from ..formats.dff import EFFECT_TYPES
from ..formats.ipl import parse_ipl_text, stream_base
from ..formats.rw import FormatError
from . import gamedata as G
from . import link as L
from . import resolve as R
from .api import index_path
from .ddl import SCHEMA_VERSION, create_schema
from .identity import TextureSids
from .layers import Discovery, HashCache, SourceSpec, discover, now_iso
from .scan import BlobJob, BlobResult, default_jobs, impl_names, scan_job
from .search import fill_fts

__all__ = ["build", "content_hash", "HASH_TABLES", "SKIP_ITEM_SECTIONS", "replace_db"]

#: Tables (and their ORDER BY) that make up ``meta.content_hash``. ``source.mtime_ns`` is left out.
HASH_TABLES: dict[str, str] = {
    "layer": "id", "source": "id", "blob": "id", "txd": "id", "image": "hash", "texture": "id", "dff": "id",
    "dff_frame": "dff_id, idx", "dff_geom": "dff_id, idx", "dff_mat": "dff_id, geom, idx", "fx2d": "id",
    "col": "id", "ide": "id", "model": "rid", "model_link": "id", "model_tex": "model_id, texture",
    "ipl": "id", "inst": "id", "ipl_item": "id", "zone": "id", "ifp": "id", "anim": "ifp_id, idx",
    # v3: exe-loaded data files
    "water_quad": "id", "timecyc": "id", "handling": "id", "carcol": "idx", "car_color": "model, idx",
    "ped_rel": "pedtype, rel, other", "object_data": "id",
}
#: Legacy text IPL sections not stored in ``ipl_item`` (165 152 VC-era path lines, unused by SA).
SKIP_ITEM_SECTIONS = frozenset({"path"})
_CHUNK_BLOBS = 600
_CHUNK_BYTES = 48 << 20


# --------------------------------------------------------------------------- records


@dataclass
class _Blob:
    id: int
    src: SourceSpec
    source_id: int
    idx: int
    name: str
    stem: str
    ext: str
    abs_off: int
    size: int
    stream_sectors: int | None
    archive_sectors: int | None
    ns: str
    order: tuple
    res: BlobResult | None = None
    shadowed_by: int | None = None

    @property
    def active(self) -> bool:
        return self.shadowed_by is None

    @property
    def file_key(self) -> str:
        return f"{self.src.relpath}/{self.name.lower()}" if self.src.kind == "img" else self.src.relpath


@dataclass
class _Txd:
    id: int
    blob: _Blob
    tex: dict[str, int] = field(default_factory=dict)   # lower name -> texture id (first)
    parent: str | None = None
    via: str | None = None


@dataclass
class _Col:
    id: int
    blob: _Blob
    idx: int
    name: str
    bbox: tuple
    embedded: bool
    active: bool = False


class _Build:
    """State of one build (in memory until the final inserts)."""

    def __init__(self, disc: Discovery, conn: sqlite3.Connection, jobs: int):
        self.d = disc
        self.c = conn
        self.jobs = jobs
        self.t: dict[str, float] = {}
        self.warn: list[str] = list(disc.warnings)
        self.errors: list[str] = []
        self.notes: list[str] = []  # known quirks of the data (not problems of the build)
        self.stats: dict[str, int] = {}
        self.layer_id = {la.name: i + 1 for i, la in enumerate(disc.layers)}
        self.layer_prio = {la.name: la.priority for la in disc.layers}
        self.source_id = {s.relpath: i + 1 for i, s in enumerate(disc.sources)}
        self.blobs: list[_Blob] = []
        self.data_files: list[str] = []

    def zone_titles(self):
        """label -> in-game name from ``text/american.gxt`` of the profile root (None if absent)."""
        from ..formats.dat import resolve_ci
        from ..formats.gxt import load_gxt
        from ..formats.rw import FormatError

        p = resolve_ci(self.d.root, "text/american.gxt")
        if p is None:
            self.warn.append("zone titles: text/american.gxt not found; zones keep their GXT labels only")
            return lambda label: None
        try:
            gxt = load_gxt(p)
        except (OSError, FormatError) as e:
            self.warn.append(f"zone titles: cannot read {p.name}: {e}")
            return lambda label: None
        return lambda label: gxt.text(label) if label else None

    def lap(self, name: str, t0: float) -> None:
        self.t[name] = round(time.perf_counter() - t0, 3)

    # ------------------------------------------------------------------ sources / blobs

    def insert_sources(self) -> None:
        c = self.c
        c.executemany("INSERT INTO layer(id,name,kind,priority,root) VALUES (?,?,?,?,?)",
                      [(self.layer_id[la.name], la.name, la.kind, la.priority, jpath(self.d.root)) for la in self.d.layers])
        c.executemany(
            "INSERT INTO source(id,layer_id,relpath,kind,size,mtime_ns,sha256,load_ref,load_order) VALUES (?,?,?,?,?,?,?,?,?)",
            [(self.source_id[s.relpath], self.layer_id[s.layer], s.relpath, s.kind, s.size, s.mtime_ns, s.sha256,
              s.load_ref, s.load_order) for s in self.d.sources])

    def enumerate_blobs(self) -> None:
        bid = 0
        for s in self.d.archives:
            try:
                with ImgArchive.open(s.path) as a:
                    entries = list(a.entries)
            except FormatError as e:
                self.warn.append(f"BAD_IMG: {s.relpath}: {e}")
                continue
            for e in entries:
                bid += 1
                self.blobs.append(_Blob(bid, s, self.source_id[s.relpath], e.idx, e.name, e.stem, e.ext.lower(),
                                        e.abs_offset, e.size, e.stream_sectors, e.archive_sectors, s.ns or "main",
                                        (0, s.load_order, e.idx)))
        loose = sorted((s for s in self.d.sources if s.loose), key=lambda s: s.relpath)
        for n, s in enumerate(loose):
            bid += 1
            name = s.path.name
            stem, _, ext = name.rpartition(".")
            self.blobs.append(_Blob(bid, s, self.source_id[s.relpath], 0, name, stem, ext.lower(), 0, s.size, None, None,
                                    "loose", (1, n, 0)))
        self.by_id = {b.id: b for b in self.blobs}

    def scan(self) -> None:
        work: list[list[BlobJob]] = []
        groups: dict[int, list[_Blob]] = {}
        for b in self.blobs:
            groups.setdefault(b.source_id, []).append(b)
        for _sid, bl in groups.items():
            chunk: list[BlobJob] = []
            nbytes = 0
            for b in sorted(bl, key=lambda b: b.abs_off):
                chunk.append(BlobJob(b.id, str(b.src.path), b.ext, b.abs_off, b.size, b.ns))
                nbytes += b.size
                if len(chunk) >= _CHUNK_BLOBS or nbytes >= _CHUNK_BYTES:
                    work.append(chunk)
                    chunk, nbytes = [], 0
            if chunk:
                work.append(chunk)
        total = len(work)
        if self.jobs <= 1 or total <= 1:
            for i, ch in enumerate(work):
                for r in scan_job(ch):
                    self.by_id[r.key].res = r
                report_progress(i + 1, total, "scan")
        else:
            with pool(min(self.jobs, total)) as ex:
                for i, rs in enumerate(ex.map(scan_job, work)):
                    for r in rs:
                        self.by_id[r.key].res = r
                    report_progress(i + 1, total, "scan")
        for b in self.blobs:
            if b.res is not None and b.res.error:
                self.errors.append(f"{b.file_key}: {b.res.error}")
            if b.res is not None and b.res.warn:
                self.notes.append(f"{b.file_key}: {b.res.warn}")

    def resolve_blobs(self) -> None:
        win = R.blob_winners([(b.id, b.ns, b.stem, b.ext, b.order) for b in self.blobs])
        for b in self.blobs:
            b.shadowed_by = win[b.id]
        self.active_blob: dict[tuple[str, str], _Blob] = {}
        for b in sorted(self.blobs, key=lambda b: (R.ns_key(b.ns) != "main", b.order, b.id)):
            if b.active:
                self.active_blob.setdefault((b.stem.lower(), b.ext), b)

    def insert_blobs(self) -> None:
        rows = []
        for b in self.blobs:
            r = b.res
            rows.append((b.id, b.source_id, b.idx, b.name, b.stem, b.ext, b.abs_off, b.size,
                         r.rw_size if r else None, b.stream_sectors, b.archive_sectors, r.hash if r else None,
                         b.ns, int(b.active), b.shadowed_by))
        self.c.executemany("INSERT INTO blob VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    # ------------------------------------------------------------------ SID formatting (same rules as FakeIndexDB)

    def variant(self, b: _Blob) -> str | None:
        """'' winner, '@layer' loser from another layer, None loser from the winner's own layer."""
        if b.active:
            return ""
        w = self.by_id[b.shadowed_by]  # type: ignore[index]
        if w.src.layer == b.src.layer:
            return None
        return f"@{b.src.layer}"

    def blob_sid(self, b: _Blob) -> str:
        """``dff:``/``txd:`` for the version a SID of that kind resolves to, ``file:`` otherwise."""
        if b.ext in ("dff", "txd"):
            v = self.variant(b)
            if v == "":
                if self.active_blob.get((b.stem.lower(), b.ext)) is b:  # main/loose winner (not player/anim/cuts twin)
                    return f"{b.ext}:{b.stem.lower()}"
            elif v is not None:
                return f"{b.ext}:{b.stem.lower()}{v}"
        return f"file:{b.file_key}"

    # ------------------------------------------------------------------ definitions (IDE)

    def load_ide(self) -> None:
        self.models: list[dict] = []
        self.txdp: list[tuple[str, str]] = []
        self.ide_fx: list[tuple[int, int, dict]] = []  # (ide_id, idx, fx)
        self.ide_ids: dict[str, int] = {}
        ide_rows = []
        n_err = 0
        for order, s in enumerate(self.d.loaded("ide")):
            ide_id = len(ide_rows) + 1
            ide_rows.append((ide_id, self.source_id[s.relpath]))
            self.ide_ids[s.relpath] = ide_id
            errs: list = []
            defs, txdp, fx = parse_ide(read_text(s.path), errors=errs)
            n_err += len(errs)
            for ln, msg in errs[:3]:
                self.errors.append(f"{s.relpath}:{ln}: {msg}")
            seen: set[int] = set()
            for d in defs:
                if d.id in seen:
                    self.warn.append(f"DUP_ID: {s.relpath}:{d.line}: model {d.id} defined twice in one file")
                    continue
                seen.add(d.id)
                self.models.append({"rid": len(self.models) + 1, "id": d.id, "layer": s.layer, "name": d.name,
                                    "txd": d.txd, "sec": d.sec, "ide_id": ide_id, "ide_order": order, "line": d.line,
                                    "draw": d.draw, "flags": d.flags, "time_on": d.time_on, "time_off": d.time_off,
                                    "anim": d.anim, "extra": d.extra or None, "active": False})
            self.txdp += [(c, p) for c, p in txdp]
            for i, f in enumerate(fx):
                self.ide_fx.append((ide_id, i, f))
        self.stats["ide_errors"] = n_err
        self.stats["txdp"] = len(self.txdp)
        self.c.executemany("INSERT INTO ide VALUES (?,?)", ide_rows)
        act = R.model_winners((m["rid"], m["id"], self.layer_prio[m["layer"]], m["ide_order"], m["line"])
                              for m in self.models)
        self.model_by_id: dict[int, dict] = {}
        for m in self.models:
            m["active"] = m["rid"] in act
            if m["active"]:
                self.model_by_id[m["id"]] = m

    # ------------------------------------------------------------------ assets

    def insert_assets(self) -> None:
        c = self.c
        parents = R.txd_parents(self.txdp, [m["txd"] for m in self.model_by_id.values() if m["sec"] == "cars"])
        self.txds: dict[int, _Txd] = {}        # blob id -> txd
        txd_rows, tex_rows, img_rows = [], [], []
        seen_img: set[bytes] = set()
        tid = 0
        self.dffs: dict[int, tuple[int, Any]] = {}   # blob id -> (dff id, DffInfo)
        dff_rows, frame_rows, geom_rows, mat_rows, fx_rows = [], [], [], [], []
        self.cols: list[_Col] = []
        col_rows = []
        self.ifps: list[tuple[int, _Blob, str]] = []
        ifp_rows, anim_rows = [], []
        for b in self.blobs:
            r = b.res
            if r is None or r.error:
                continue
            if r.txd is not None:
                t = _Txd(len(self.txds) + 1, b)
                p = parents.get(b.stem.lower())
                if p:
                    t.parent, t.via = p
                self.txds[b.id] = t
                txd_rows.append((t.id, b.id, b.stem, r.txd[0], r.txd[1], len(r.textures), t.parent, t.via))
                for (idx, name, mask, plat, raster, fmt, w, h, depth, levels, alpha, filt, u, v, doff, dsize, poff, hsh,
                     nbytes, mean) in r.textures:
                    tid += 1
                    t.tex.setdefault(name.lower(), tid)
                    if hsh not in seen_img:
                        seen_img.add(hsh)
                        img_rows.append((hsh, w, h, fmt, nbytes, mean))
                    tex_rows.append((tid, t.id, idx, name, mask, plat, raster, fmt, w, h, depth, levels, alpha, filt, u, v,
                                     doff, dsize, poff, hsh))
            if r.dff is not None:
                info = r.dff
                did = len(self.dffs) + 1
                self.dffs[b.id] = (did, info)
                bb = info.bbox or (None,) * 6
                bs = info.bsphere or (None,) * 4
                plugins = ",".join(f"0x{p:x}" for p in sorted(info.plugins))
                dff_rows.append((did, b.id, b.stem, info.rw_version, info.clumps, info.atomics, len(info.frames),
                                 len(info.geoms), len(info.materials), info.verts, info.tris, *bb, *bs, info.flags,
                                 plugins or None))
                frame_rows += [(did, f.idx, f.parent, f.name, int(f.atomic)) for f in info.frames]
                geom_rows += [(did, g.idx, g.rw_flags, g.verts, g.tris, g.uv_sets, int(g.strip), g.frame, *g.bsphere,
                               g.geom_off) for g in info.geoms]
                mat_rows += [(did, m.geom, m.idx, m.rgba, m.texture, m.mask, m.fx, m.color_slot) for m in info.materials]
                if r.empty_tex:
                    self.stats.setdefault("dff_empty_texname", {})[str(did)] = r.empty_tex
                for e in info.effects:
                    fx_rows.append((did, None, "dff", e.idx, e.type, e.type_name, *e.pos,
                                    json.dumps(e.data, separators=(",", ":"), sort_keys=True, default=str)))
            for cm in r.cols:
                col = _Col(len(self.cols) + 1, b, cm.idx, cm.name, tuple(cm.bbox), b.ext == "dff")
                self.cols.append(col)
                bb = tuple(cm.bbox) + (None,) * (6 - len(cm.bbox))
                col_rows.append([col.id, b.id, cm.idx, cm.version, cm.name, cm.hdr_model_id, *bb[:6], *cm.bsphere,
                                 cm.spheres, cm.boxes, cm.verts, cm.faces, cm.shadow_faces,
                                 json.dumps({str(k): v for k, v in sorted(cm.surfaces.items())}, separators=(",", ":")),
                                 0])
            if r.ifp is not None:
                fmt, _pack, anims = r.ifp
                iid = len(self.ifps) + 1
                self.ifps.append((iid, b, b.stem))
                ifp_rows.append((iid, b.id, b.stem, fmt if fmt in ("ANP3", "ANPK") else "ANP3", len(anims)))
                anim_rows += [(iid, i, a[0], a[1], a[2]) for i, a in enumerate(anims)]
        # COL winners: DAT COLFILE, exe-loaded loose, IMG order, the rest
        def col_order(cl: _Col) -> tuple:
            s = cl.blob.src
            if s.dat_order is not None and s.kind == "col":
                return (0, s.dat_order, cl.idx)
            if cl.blob.ns == "loose":
                return (1 if s.load_ref else 3, s.relpath, cl.idx)
            return (2, cl.blob.order, cl.idx)
        # Streaming never loads a shadowed IMG entry. Explicit loose COLFILE directives
        # still participate even when an IMG entry wins the blob name.
        act = R.col_winners((cl.id, cl.name, col_order(cl)) for cl in self.cols
                           if not cl.embedded and (cl.blob.src.kind != "img" or cl.blob.active))
        for cl, row in zip(self.cols, col_rows):
            cl.active = (cl.blob.active if cl.embedded else cl.id in act)
            row[-1] = int(cl.active)
        self.col_by_name: dict[str, _Col] = {cl.name.lower(): cl for cl in self.cols if cl.active and not cl.embedded}
        self.col_embedded: dict[int, _Col] = {}
        for cl in self.cols:
            if cl.embedded:
                self.col_embedded.setdefault(cl.blob.id, cl)
        c.executemany("INSERT INTO txd VALUES (?,?,?,?,?,?,?,?)", txd_rows)
        c.executemany("INSERT INTO image VALUES (?,?,?,?,?,?)", img_rows)
        c.executemany("INSERT INTO texture(id,txd_id,idx,name,mask,platform,raster_fmt,d3dfmt,w,h,depth,levels,alpha,"
                      "filter,uaddr,vaddr,data_off,data_size,pal_off,hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      tex_rows)
        c.executemany("INSERT INTO dff VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", dff_rows)
        c.executemany("INSERT INTO dff_frame VALUES (?,?,?,?,?)", frame_rows)
        c.executemany("INSERT INTO dff_geom VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", geom_rows)
        c.executemany("INSERT INTO dff_mat VALUES (?,?,?,?,?,?,?,?)", mat_rows)
        c.executemany("INSERT INTO col(id,blob_id,idx,version,name,hdr_model_id,bmin_x,bmin_y,bmin_z,bmax_x,bmax_y,bmax_z,"
                      "bs_x,bs_y,bs_z,bs_r,spheres,boxes,verts,faces,shadow_faces,surfaces,active) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", col_rows)
        c.executemany("INSERT INTO ifp VALUES (?,?,?,?,?)", ifp_rows)
        c.executemany("INSERT INTO anim VALUES (?,?,?,?,?)", anim_rows)
        self._fx_rows = fx_rows
        self.stats.update(txd=len(txd_rows), texture=len(tex_rows), image=len(img_rows), dff=len(dff_rows),
                          col=len(col_rows), ifp=len(ifp_rows), anim=len(anim_rows))

    # ------------------------------------------------------------------ models + links

    def active_txd(self, name: str | None) -> _Txd | None:
        if not name:
            return None
        b = self.active_blob.get((name.lower(), "txd"))
        return self.txds.get(b.id) if b is not None else None

    def active_dff(self, name: str) -> tuple[int, Any, _Blob] | None:
        b = self.active_blob.get((name.lower(), "dff"))
        if b is None or b.id not in self.dffs:
            return None
        did, info = self.dffs[b.id]
        return did, info, b

    def model_col(self, m: dict, dff) -> _Col | None:
        if dff is not None:
            emb = self.col_embedded.get(dff[2].id)
            if emb is not None:
                return emb
        return self.col_by_name.get(m["name"].lower())

    def insert_models(self) -> None:
        c = self.c
        rows = []
        for m in self.models:
            rows.append((m["rid"], m["id"], self.layer_id[m["layer"]], m["name"], m["txd"], m["sec"], m["ide_id"],
                         m["line"], m["draw"], m["flags"], m["time_on"], m["time_off"], m["anim"],
                         json.dumps(m["extra"], separators=(",", ":"), sort_keys=True) if m["extra"] else None,
                         int(m["active"])))
        c.executemany("INSERT INTO model(rid,id,layer_id,name,txd,sec,ide_id,line,draw,flags,time_on,time_off,anim,"
                      "extra,active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        # IDE 2dfx -> fx2d (origin ide)
        for ide_id, i, f in self.ide_fx:
            m = self.model_by_id.get(f["id"])
            typ = -1
            fields = f.get("fields") or []
            if len(fields) > 4:
                try:
                    typ = int(float(fields[4]))
                except ValueError:
                    typ = -1
            self._fx_rows.append((None, m["rid"] if m else None, "ide", i, typ, EFFECT_TYPES.get(typ, f"type{typ}"),
                                  *f["pos"], json.dumps({"ide": ide_id, "line": f.get("line"), "fields": fields},
                                                        separators=(",", ":"))))
        c.executemany("INSERT INTO fx2d(dff_id,model_rid,origin,idx,type,type_name,x,y,z,data) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      self._fx_rows)

    def link_models(self) -> None:
        def parent_of(t: _Txd):
            return t.parent, t.via

        def tex_lookup(t: _Txd, name: str):
            return t.tex.get(name)

        mats_by_dff: dict[int, list[str]] = {}
        for _bid, (did, info) in self.dffs.items():
            mats_by_dff[did] = [m.texture for m in info.materials if m.texture]
        self.link: dict[int, dict] = {}
        tex_rows = []
        for mid in sorted(self.model_by_id):
            m = self.model_by_id[mid]
            dff = self.active_dff(m["name"])
            own = self.active_txd(m["txd"])
            col = self.model_col(m, dff)
            chain = L.txd_chain(m["txd"], m["sec"], self.active_txd, parent_of)
            rs = L.resolve_textures(mats_by_dff.get(dff[0], []) if dff else [], chain, tex_lookup)
            tex_rows += [(mid, name, tid, via, uses) for name, tid, via, uses in rs]
            self.link[mid] = {"dff": dff, "txd": own, "col": col, "tex_total": len(rs),
                              "tex_missing": sum(1 for r in rs if r[2] == "missing"), "n_inst": 0}
        self.c.executemany("INSERT INTO model_tex VALUES (?,?,?,?,?)", tex_rows)
        self.stats["model_tex"] = len(tex_rows)

    # ------------------------------------------------------------------ world

    def load_world(self) -> None:
        ipl_rows: list[tuple] = []
        ipl_info: dict[int, tuple[str, int | None]] = {}
        insts: list[list] = []      # [id, ipl_id, idx, model, area, iflags, x,y,z, qx,qy,qz,qw, lod_idx]
        items: list[tuple] = []     # (ipl_id, sec, idx, x, y, z, data)
        text_by_name: dict[str, int] = {}
        skipped: dict[str, int] = {}
        n_err = 0
        for s in self.d.loaded("ipl"):
            name = s.relpath.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if name in text_by_name:
                self.warn.append(f"DUP_IPL: {s.relpath}: an IPL named {name!r} was loaded before; skipped")
                continue
            pid = len(ipl_rows) + 1
            text_by_name[name] = pid
            ipl_rows.append((pid, name, "text", self.source_id[s.relpath], None, None, 1, self.layer_id[s.layer]))
            ipl_info[pid] = ("text", None)
            errs: list = []
            ins, its = parse_ipl_text(read_text(s.path), errors=errs)
            n_err += len(errs)
            for ln, msg in errs[:3]:
                self.errors.append(f"{s.relpath}:{ln}: {msg}")
            for i in ins:
                insts.append([0, pid, i.idx, i.model_id, i.interior & 0xFF, (i.interior >> 8) & 0xFFFFFF, *i.pos, *i.q, i.lod])
            for sec, lst in sorted(its.items()):
                if sec in SKIP_ITEM_SECTIONS:
                    skipped[sec] = skipped.get(sec, 0) + len(lst)
                    continue
                for it in lst:
                    pos = it.get("pos") or (None, None, None)
                    items.append((pid, sec, it["idx"], *pos,
                                  json.dumps({"line": it["line"], "fields": it["fields"]}, separators=(",", ":"))))
        bins = sorted((b for b in self.blobs if b.ext == "ipl" and b.active and R.ns_key(b.ns) == "main"
                       and b.res is not None and b.res.ipl is not None), key=lambda b: (b.order, b.id))
        orphans = 0
        for b in bins:
            name = b.stem.lower()
            parent = text_by_name.get(stream_base(name) or "")
            pid = len(ipl_rows) + 1
            ipl_rows.append((pid, name, "binary", None, b.id, parent, 1 if parent else 0, self.layer_id[b.src.layer]))
            ipl_info[pid] = ("binary", parent)
            if parent is None:
                orphans += 1
            ins, cars = b.res.ipl  # type: ignore[union-attr]
            for i in ins:
                insts.append([0, pid, i.idx, i.model_id, i.interior & 0xFF, (i.interior >> 8) & 0xFFFFFF, *i.pos, *i.q, i.lod])
            for k, car in enumerate(cars):
                items.append((pid, "cars", k, car.get("x"), car.get("y"), car.get("z"),
                              json.dumps(car, separators=(",", ":"), sort_keys=True)))
        for n, row in enumerate(insts, 1):
            row[0] = n
        lod, is_lod, links, unresolved = L.lod_links(ipl_info, [(r[0], r[1], r[2], r[13]) for r in insts])
        rt = []
        rows = []
        for r in insts:
            iid, pid, idx, mid = r[0], r[1], r[2], r[3]
            pos, q = (r[6], r[7], r[8]), (r[9], r[10], r[11], r[12])
            lk = self.link.get(mid)
            col_box = None
            sphere = None
            if lk is not None:
                lk["n_inst"] += 1
                if lk["col"] is not None and len(lk["col"].bbox) >= 6:
                    col_box = lk["col"].bbox
                elif lk["dff"] is not None and lk["dff"][1].bsphere:
                    sphere = lk["dff"][1].bsphere
            box, src = L.inst_aabb(pos, q, col_box, sphere)
            rows.append((iid, pid, idx, mid, r[4], r[5], *pos, *q, r[13], lod.get(iid), int(iid in is_lod), src))
            rt.append((iid, box[0], box[3], box[1], box[4], box[2], box[5]))
        c = self.c
        c.executemany("INSERT INTO ipl VALUES (?,?,?,?,?,?,?,?)", ipl_rows)
        c.executemany("INSERT INTO inst VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        c.executemany("INSERT INTO inst_rtree VALUES (?,?,?,?,?,?,?)", rt)
        c.executemany("INSERT INTO ipl_item(ipl_id,sec,idx,x,y,z,data) VALUES (?,?,?,?,?,?,?)", items)
        c.execute("INSERT INTO item_rtree SELECT id, x, x, y, y, z, z FROM ipl_item WHERE x IS NOT NULL AND y IS NOT NULL "
                  "AND z IS NOT NULL")
        # zones
        zrows = []
        from ..formats.zon import parse_zon as zon

        titles = self.zone_titles()

        for s in self.d.loaded("zon"):
            for z in zon(read_text(s.path)):
                mn, mx = z.get("min") or (None,) * 3, z.get("max") or (None,) * 3
                zrows.append((z.get("name"), z.get("label"), titles(z.get("label")), z.get("type"),
                              z.get("level"), *mn, *mx, self.source_id[s.relpath]))
        c.executemany("INSERT INTO zone(name,label,title,type,level,minx,miny,minz,maxx,maxy,maxz,source_id) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", zrows)
        self.stats["zone_titles"] = sum(1 for r in zrows if r[2])
        self.stats.update(ipl=len(ipl_rows), inst=len(rows), lod_links=links, lod_unresolved=unresolved,
                          bnry_orphans=orphans, items=len(items), zones=len(zrows), ipl_errors=n_err)
        if skipped:
            self.stats.update({f"skipped_{k}": v for k, v in skipped.items()})
        # model_link (n_inst now known)
        lrows = []
        for mid in sorted(self.link):
            lk = self.link[mid]
            col = lk["col"]
            lrows.append((mid, lk["dff"][0] if lk["dff"] else None, lk["txd"].id if lk["txd"] else None,
                          col.id if col else None, ("embedded" if col.embedded else "colfile") if col else None,
                          lk["n_inst"], lk["tex_total"], lk["tex_missing"]))
        c.executemany("INSERT INTO model_link VALUES (?,?,?,?,?,?,?,?)", lrows)

    # ------------------------------------------------------------------ data files (v3)

    def load_data(self) -> None:
        """``water_quad``, ``timecyc``, ``handling``, ``carcol``/``car_color``, ``ped_rel``, ``object_data``."""
        two = any(f.lower().endswith(".two") for f in self.d.dat_files)  # SA-MP: HANDLING.two, timecyc.two
        self.data_files = G.load(self.c, self.d.root, self.source_id, notes=self.notes, stats=self.stats, two=two)

    # ------------------------------------------------------------------ search

    def fts_rows(self) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        for m in self.models:
            sid = f"model:{m['id']}" if m["active"] else f"model:{m['id']}@{m['layer']}"
            out.append((sid, "model", m["name"]))
        for bid, (_did, _info) in self.dffs.items():
            b = self.by_id[bid]
            out.append((self.blob_sid(b), "dff", b.stem))
        for bid, t in self.txds.items():
            b = t.blob
            out.append((self.blob_sid(b), "txd", b.stem))
        rows = self.c.execute("SELECT id, txd_id, name, hash FROM texture ORDER BY id").fetchall()
        txd_by_id = {t.id: t for t in self.txds.values()}
        texture_sids = TextureSids(self.c)
        for tid, txd_id, name, digest in rows:
            t = txd_by_id[txd_id]
            sid = texture_sids.sid(tid, name, digest, t.blob.id, t.blob.stem, t.blob.src.layer)
            out.append((sid, "tex", name))
        named = set()
        for cl in self.cols:
            if cl.active and not cl.embedded:
                named.add(cl.name.lower())
                out.append((f"col:{cl.name.lower()}", "col", cl.name))
        for cl in self.cols:
            if cl.active and cl.embedded and cl.name.lower() not in named:
                named.add(cl.name.lower())
                out.append((f"col:{cl.name.lower()}", "col", cl.name))
        for (name,) in self.c.execute("SELECT name FROM ipl ORDER BY id"):
            out.append((f"ipl:{name.lower()}", "ipl", name))
        seen_zone: set[str] = set()
        for name, title in self.c.execute("SELECT name, title FROM zone ORDER BY id"):
            if name.lower() in seen_zone:
                continue
            seen_zone.add(name.lower())
            out.append((f"zone:{name.lower()}", "zone", f"{name} {title}" if title else name))
        seen_ifp: set[str] = set()
        anims = self.c.execute("SELECT ifp_id, name FROM anim ORDER BY ifp_id, idx").fetchall()
        by_ifp: dict[int, list[str]] = {}
        for iid, name in anims:
            by_ifp.setdefault(iid, []).append(name)
        for iid, b, stem in sorted(self.ifps, key=lambda x: (R.ns_key(x[1].ns) != "main", x[1].order, x[1].id)):
            if not b.active or stem.lower() in seen_ifp:
                continue
            seen_ifp.add(stem.lower())
            out.append((f"ifp:{stem.lower()}", "ifp", stem))
            seen_anim: set[str] = set()
            for a in by_ifp.get(iid, []):
                if a.lower() in seen_anim:
                    continue
                seen_anim.add(a.lower())
                out.append((f"anim:{stem.lower()}/{a.lower()}", "anim", a))
        out += G.fts_rows(self.c)
        return out

    # ------------------------------------------------------------------ meta

    def meta_rows(self, seconds: float, chash: str) -> list[tuple[str, str]]:
        d = self.d
        exe = next((s for s in d.sources if s.relpath == "gta_sa.exe"), None)
        data = set(self.data_files)
        sig = [[s.relpath, s.size, s.mtime_ns] for s in d.sources
               if (s.load_ref is not None and s.kind in ("dat", "ide", "ipl", "zon", "img")) or s.relpath in data]
        assumptions = list(d.assumptions)
        impl = impl_names()
        if self.stats.get("skipped_path"):
            assumptions.append(f"legacy text IPL 'path' sections not stored ({self.stats['skipped_path']} lines)")
        return [
            ("schema_version", str(SCHEMA_VERSION)),
            ("satk_version", SATK_VERSION),
            ("profile", d.profile),
            ("root", jpath(d.root)),
            ("dat_files", json.dumps(d.dat_files)),
            ("img_order", json.dumps([s.relpath for s in d.archives])),
            ("img_order_mode", d.img_order),
            ("assumptions", json.dumps(assumptions, ensure_ascii=False)),
            ("built_at", now_iso()),
            ("build_seconds", f"{seconds:.2f}"),
            ("content_hash", chash),
            ("game_exe_sha256", (exe.sha256 or "") if exe else ""),
            ("formats_impl", json.dumps(impl, sort_keys=True)),
            ("sources_sig", json.dumps(sig, separators=(",", ":"))),
            ("warnings", json.dumps(self.warn[:200], ensure_ascii=False)),
            ("notes", json.dumps(self.notes[:200], ensure_ascii=False)),
            ("errors", json.dumps(self.errors[:200], ensure_ascii=False)),
            ("stats", json.dumps(self.stats, sort_keys=True)),
        ]


# --------------------------------------------------------------------------- hash / replace


def content_hash(conn: sqlite3.Connection) -> str:
    """sha256 of a canonical dump of :data:`HASH_TABLES` (``source.mtime_ns`` excluded)."""
    h = hashlib.sha256()
    for table, order in HASH_TABLES.items():
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if table == "source":
            cols = [c for c in cols if c != "mtime_ns"]
        h.update(f"\x00{table}:{','.join(cols)}\x00".encode())
        for row in conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order}"):
            h.update(repr(tuple(bytes(v) if isinstance(v, memoryview) else v for v in row)).encode("utf-8"))
            h.update(b"\n")
    return h.hexdigest()


def replace_db(tmp: Path, dst: Path, *, retries: int = 5) -> str:
    """Move ``tmp`` over ``dst``; if ``dst`` is held open (Windows), copy into it with the backup API.

    Returns ``"replace"`` or ``"backup"``.
    """
    last: OSError | None = None
    for i in range(retries):
        try:
            os.replace(tmp, dst)
            return "replace"
        except PermissionError as e:
            last = e
            time.sleep(0.05 * (i + 1))
    src = sqlite3.connect(tmp)
    try:
        out = sqlite3.connect(dst, timeout=30)
        try:
            src.backup(out)
        except sqlite3.Error as e:
            raise SatkError("BUSY", f"cannot replace {jpath(dst)}: {last}; backup failed: {e}",
                            hint="close programs using the index (MCP server) and rebuild") from None
        finally:
            out.close()
    finally:
        src.close()
    try:
        os.remove(tmp)
    except OSError:
        pass
    st = os.stat(dst)
    os.utime(dst, ns=(st.st_atime_ns, max(st.st_mtime_ns, time.time_ns())))
    return "backup"


# --------------------------------------------------------------------------- entry point


def build(profile: str = "vanilla", *, jobs: int | None = None, out: Path | None = None, root: Path | None = None,
          dat_files: list[str] | None = None, img_order: str | None = None, vanilla_manifest: Path | None = None,
          hashcache: HashCache | None = None) -> dict:
    """Build ``work/index/<profile>.sqlite`` (or ``out``). Returns a JSON-able report.

    Args:
        profile: load profile name.
        jobs: worker processes for the scan (default: min(12, CPUs); 1 = in-process).
        out: target database path (default :func:`satk.index.api.index_path`).
        root, dat_files, img_order, vanilla_manifest, hashcache: overrides (tests).
    """
    t_all = time.perf_counter()
    if out is None and root is None:
        profile = cfg().canonical_profile(profile)  # vanilla -> game without a clean copy
    jobs = default_jobs() if not jobs or jobs < 1 else int(jobs)
    dst = Path(os.path.abspath(out)) if out is not None else index_path(profile)
    ensure_writable(dst)  # reject before discovery can create or update the hash cache
    t0 = time.perf_counter()
    disc = discover(profile, root=root, dat_files=dat_files, img_order=img_order,
                    vanilla_manifest=vanilla_manifest, hashcache=hashcache)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    ok = False
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA cache_size = -200000")
        create_schema(conn)
        b = _Build(disc, conn, jobs)
        b.lap("discover", t0)
        report_progress(0, None, "discover done")
        t0 = time.perf_counter()
        b.insert_sources()
        b.enumerate_blobs()
        b.scan()
        b.lap("scan", t0)
        t0 = time.perf_counter()
        b.resolve_blobs()
        b.insert_blobs()
        b.load_ide()
        b.insert_assets()
        b.insert_models()
        b.lap("resolve", t0)
        t0 = time.perf_counter()
        b.link_models()
        b.load_world()
        b.load_data()
        b.lap("link", t0)
        t0 = time.perf_counter()
        n_fts = fill_fts(conn, b.fts_rows())
        b.stats["fts"] = n_fts
        b.lap("search", t0)
        t0 = time.perf_counter()
        conn.commit()
        chash = content_hash(conn)
        seconds = time.perf_counter() - t_all
        conn.executemany("INSERT INTO meta VALUES (?,?)", b.meta_rows(seconds, chash))
        conn.commit()
        conn.execute("ANALYZE")
        conn.commit()
        conn.close()
        how = replace_db(tmp, dst)
        b.lap("finalize", t0)
        ok = True
    finally:
        if not ok:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            try:
                tmp.unlink()
            except OSError:
                pass
    seconds = round(time.perf_counter() - t_all, 2)
    size = dst.stat().st_size
    counts = {k: b.stats.get(k, 0) for k in ("txd", "texture", "image", "dff", "col", "ifp", "anim", "ipl", "inst",
                                             "model_tex", "fts", "water_quad", "timecyc", "handling", "car_color",
                                             "object_data")}
    counts.update(blobs=len(b.blobs), sources=len(disc.sources), models=len(b.models),
                  models_active=len(b.model_by_id), layers=len(disc.layers))
    out_env: dict[str, Any] = {
        "ok": True, "profile": profile, "path": jpath(dst), "build_seconds": seconds,
        "size_mb": round(size / 1e6, 1), "user_version": SCHEMA_VERSION, "content_hash": chash, "replaced_by": how,
        "jobs": jobs, "counts": counts, "timings": b.t,
        "lod": {"links": b.stats.get("lod_links"), "unresolved": b.stats.get("lod_unresolved")},
        "hash_cache": disc.hash_stats, "formats_impl": impl_names(),
    }
    if b.errors:
        out_env["errors"] = b.errors[:20]
        out_env["n_errors"] = len(b.errors)
    if b.notes:
        out_env["notes"] = len(b.notes)
    fb = fallback_reason()
    if fb:
        b.warn.append(f"UNSUPPORTED: worker processes are not available here ({fb}); the scan ran in-process "
                      "(same result, slower)")
    if b.warn:
        out_env["warn"] = b.warn[:20]
    return out_env
