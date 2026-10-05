"""SQL implementation of the :class:`satk.index.api.IndexDB` query methods. Owner: WP-03.

Output shapes are identical to :class:`satk.index.fake.FakeIndexDB` (the Q1 reference) - the
parity is tested on the fake's own SQLite dump. SID rules:

* ``dff:``/``txd:`` name the version a lookup resolves to (the active blob, ``main``/loose before
  ``player``/``anim``/``cuts``); a version shadowed by another layer is ``<kind>:<stem>@<layer>``; a
  version shadowed inside its own layer is only addressable as ``file:<relpath>/<entry>``;
* ``tex:<txd>/<name>`` is the texture in the active TXD; ``@<layer>`` selects a non-winning one;
  textures that cannot be identified that way use their ``pix:`` content SID;
* ``model:<id>`` the active definition, ``model:<id>@<layer>`` an overridden one.

Stdlib only.
"""

from __future__ import annotations

import difflib
import json
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Sequence

from ..core import envelope as env
from ..core.errors import SatkError
from ..core.ids import Sid
from ..core.paths import jpath
from .api import (
    DATA_KINDS, FIND_COLS, GENERIC_COLS, INST_COLS, LOD_MODES, MATCH_MODES, MODEL_TEX_COLS, NEAR_COLS, NEAR_KINDS,
    REFS_RELS, SEARCH_KINDS, BlobRef, ColRef, InstRow, ModelFiles, TexRef, as_sid, check_choice, cursor_offset,
    parse_sid, world_quat, world_rz,
)
from .identity import TextureSids, blob_id
from .search import KIND_ORDER, MIN_TRIGRAM, match_expr

__all__ = ["Q"]

_BLOB_SQL = ("SELECT b.id, b.source_id, b.idx, b.name, b.stem, b.ext, b.abs_off, b.size, b.rw_size, b.ns, b.active, "
             "b.shadowed_by, s.relpath, s.kind AS skind, l.name AS layer "
             "FROM blob b JOIN source s ON s.id = b.source_id JOIN layer l ON l.id = s.layer_id ")


def _row(cur: sqlite3.Cursor, r) -> dict:
    return None if r is None else {d[0]: v for d, v in zip(cur.description, r)}


class Q:
    """Query helper bound to one :class:`~satk.index.api.IndexDB` (uses its per-thread connection)."""

    def __init__(self, db):
        self.db = db
        self.c: sqlite3.Connection = db._conn()
        self._texture_sids = TextureSids(self.c)

    # ================================================================== small helpers

    @staticmethod
    def _p(params):
        return params if isinstance(params, dict) else list(params)

    def one(self, sql: str, params: Sequence | dict = ()) -> dict | None:
        cur = self.c.execute(sql, self._p(params))
        return _row(cur, cur.fetchone())

    def all(self, sql: str, params: Sequence | dict = ()) -> list[dict]:
        cur = self.c.execute(sql, self._p(params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def val(self, sql: str, params: Sequence | dict = ()):
        r = self.c.execute(sql, self._p(params)).fetchone()
        return r[0] if r else None

    @property
    def root(self) -> Path:
        return self.db.root

    def path_of(self, relpath: str) -> Path:
        return self.root.joinpath(*relpath.split("/"))

    def not_found(self, s: Sid, cands: Sequence[str], what: str | None = None) -> SatkError:
        close = difflib.get_close_matches(s.key, list(cands), n=3, cutoff=0.6)
        word = s.key.split("/")[-1].split("#")[0]
        return SatkError("NOT_FOUND", f"no {what or s.kind} {s.key!r}" + (f"@{s.layer}" if s.layer else ""),
                         hint=f"satk asset find {word} --kind {s.kind}"
                         if s.kind in SEARCH_KINDS and s.kind not in DATA_KINDS else f"satk asset find {word}",
                         did_you_mean=[f"{s.kind}:{c}" for c in close])

    def suggest(self, kind: str, key: str) -> list[str]:
        """Candidate keys for did-you-mean (trigram neighbours of ``key``)."""
        word = key.split("/")[-1].split("#")[0]
        out: list[str] = []
        probes = [word[i:i + 3] for i in range(0, max(1, len(word) - 2), 2)][:6] if len(word) >= 3 else [word]
        for p in probes:
            if len(p) < MIN_TRIGRAM:
                rows = self.c.execute("SELECT sid FROM fts_name WHERE kind=? AND name LIKE ? LIMIT 50",
                                      (kind, p + "%")).fetchall()
            else:
                rows = self.c.execute("SELECT sid FROM fts_name WHERE fts_name MATCH ? AND kind=? LIMIT 50",
                                      (match_expr(p), kind)).fetchall()
            for (sid,) in rows:
                k = sid.split(":", 1)[1].split("@")[0]
                if k not in out:
                    out.append(k)
        if kind == "model":  # model SIDs are numeric: suggest by name (model:<name> is a valid SID too)
            names: list[str] = []
            for p in probes:
                q = ("SELECT lower(name) FROM fts_name WHERE kind='model' AND name LIKE ? LIMIT 50" if len(p) < MIN_TRIGRAM
                     else "SELECT lower(name) FROM fts_name WHERE fts_name MATCH ? AND kind='model' LIMIT 50")
                for (n,) in self.c.execute(q, (p + "%",) if len(p) < MIN_TRIGRAM else (match_expr(p),)):
                    if n not in names:
                        names.append(n)
            return names
        return out

    # ================================================================== blobs

    def blob(self, bid: int) -> dict | None:
        return self.one(_BLOB_SQL + "WHERE b.id = ?", (bid,))

    @staticmethod
    def file_key(b: dict) -> str:
        return f"{b['relpath']}/{b['name'].lower()}" if b["skind"] == "img" else b["relpath"]

    def canon_blob_id(self, stem: str, ext: str) -> int | None:
        return blob_id(self.c, stem, ext)

    def variant(self, b: dict) -> str | None:
        """'' winner, '@layer' loser from another layer, None loser from the winner's layer."""
        if b["active"]:
            return ""
        w = self.blob(b["shadowed_by"]) if b["shadowed_by"] else None
        if w is not None and w["layer"] == b["layer"]:
            return None
        return f"@{b['layer']}"

    def blob_sid(self, b: dict) -> str:
        if b["ext"] in ("dff", "txd"):
            v = self.variant(b)
            if v == "":
                if self.canon_blob_id(b["stem"], b["ext"]) == b["id"]:
                    return f"{b['ext']}:{b['stem'].lower()}"
            elif v is not None:
                return f"{b['ext']}:{b['stem'].lower()}{v}"
        return f"file:{self.file_key(b)}"

    def blob_ref(self, b: dict, sid: str | None = None) -> BlobRef:
        return BlobRef(sid or self.blob_sid(b), self.path_of(b["relpath"]), int(b["abs_off"]), int(b["size"]), b["name"])

    def find_blob(self, s: Sid) -> dict:
        """Blob of ``dff:``/``txd:`` (``@layer``) or ``file:<relpath>[/<entry>]``."""
        if s.kind == "file":
            key = s.key
            src = self.one("SELECT id, kind FROM source WHERE relpath = ?", (key,))
            if src is not None and src["kind"] != "img":
                b = self.one(_BLOB_SQL + "WHERE b.source_id = ? ORDER BY b.idx LIMIT 1", (src["id"],))
                if b is not None:
                    return b
            rel, _, entry = key.rpartition("/")
            if rel:
                b = self.one(_BLOB_SQL + "WHERE s.relpath = ? AND b.name = ? ORDER BY b.idx LIMIT 1", (rel, entry))
                if b is not None:
                    return b
            names = [r[0] for r in self.c.execute(
                "SELECT s.relpath || '/' || lower(b.name) FROM blob b JOIN source s ON s.id=b.source_id "
                "WHERE b.name LIKE ? LIMIT 200", (entry[:3] + "%" if entry else key.rsplit("/", 1)[-1][:3] + "%",))]
            raise self.not_found(s, names, "file")
        ext = s.kind
        bid = blob_id(self.c, s.key, ext, s.layer)
        b = self.blob(bid) if bid is not None else None
        if b is None:
            raise self.not_found(s, self.suggest(ext, s.key))
        return b

    # ================================================================== models

    def model_sid(self, m: dict) -> str:
        return f"model:{m['id']}" + ("" if m["active"] else f"@{m['layer']}")

    def find_model(self, s: Sid) -> dict:
        base = ("SELECT m.*, l.name AS layer, s.relpath AS ide_rel FROM model m JOIN layer l ON l.id = m.layer_id "
                "JOIN ide i ON i.id = m.ide_id JOIN source s ON s.id = i.source_id ")
        if s.num is not None:
            where, p = "WHERE m.id = ?", [s.num]
        else:
            where, p = "WHERE m.name = ?", [s.key]
        if s.layer:
            where += " AND l.name = ? ORDER BY m.active, m.rid"
            p.append(s.layer)
        else:
            where += " AND m.active = 1 ORDER BY m.rid"
        m = self.one(base + where + " LIMIT 1", p)
        if m is None:
            cands = self.suggest("model", s.key)
            raise self.not_found(s, cands, "model")
        return m

    def model_from_arg(self, model) -> dict:
        if isinstance(model, bool):
            raise SatkError("BAD_PARAMS", "model must be an ID, a name or a model: SID")
        if isinstance(model, int):
            return self.find_model(Sid("model", str(model)))
        text = str(model)
        s = Sid.parse(text) if ":" in text else Sid("model", text)
        return self.find_model(as_sid(s, ("model",)))

    def active_txd_blob(self, name: str | None) -> dict | None:
        if not name:
            return None
        bid = self.canon_blob_id(name, "txd")
        return self.blob(bid) if bid is not None else None

    def txd_of_blob(self, bid: int) -> dict | None:
        return self.one("SELECT * FROM txd WHERE blob_id = ?", (bid,))

    def txd_chain(self, m: dict) -> list[tuple[dict, dict, str]]:
        """``[(blob, txd row, via)]``: own TXD, its parents, then ``vehicle`` for cars."""
        out: list[tuple[dict, dict, str]] = []
        seen: set[int] = set()
        b = self.active_txd_blob(m["txd"])
        how = "own"
        while b is not None and b["id"] not in seen:
            t = self.txd_of_blob(b["id"])
            if t is None:
                break
            seen.add(b["id"])
            out.append((b, t, how))
            how = t["parent_via"] or "txdp"
            b = self.active_txd_blob(t["parent"])
        if m["sec"] == "cars":
            v = self.active_txd_blob("vehicle")
            if v is not None and v["id"] not in seen:
                t = self.txd_of_blob(v["id"])
                if t is not None:
                    out.append((v, t, "vehicle"))
        return out

    def model_link(self, mid: int) -> dict | None:
        return self.one("SELECT * FROM model_link WHERE id = ?", (mid,))

    def dff_blob_of(self, link: dict | None) -> tuple[dict, dict] | None:
        if not link or link["dff_id"] is None:
            return None
        d = self.one("SELECT * FROM dff WHERE id = ?", (link["dff_id"],))
        return (d, self.blob(d["blob_id"])) if d else None

    def col_row(self, cid: int | None) -> dict | None:
        if cid is None:
            return None
        return self.one("SELECT c.*, b.ext AS bext FROM col c JOIN blob b ON b.id = c.blob_id WHERE c.id = ?", (cid,))

    # ================================================================== textures

    def tex_sid(self, x: dict) -> str:
        """Use the same identity policy as FTS generation (see :meth:`tex_rows`)."""
        return self._texture_sids.sid(x["id"], x["name"], x["hash"], x["blob_id"], x["txd_name"], x["layer"])

    _TEX_SQL = ("SELECT x.*, t.name AS txd_name, t.blob_id, b.active, b.shadowed_by, s.relpath, l.name AS layer "
                "FROM texture x JOIN txd t ON t.id = x.txd_id JOIN blob b ON b.id = t.blob_id "
                "JOIN source s ON s.id = b.source_id JOIN layer l ON l.id = s.layer_id ")

    def tex_rows(self, where: str, params: Sequence = ()) -> list[dict]:
        return self.all(self._TEX_SQL + where, params)

    def find_tex(self, s: Sid) -> dict:
        if s.kind == "pix":
            rows = self.tex_rows("WHERE x.hash = ? ORDER BY (b.active = 0), x.id LIMIT 1", (bytes.fromhex(s.key),))
            if not rows:
                raise self.not_found(s, [], "pixel hash")
            return rows[0]
        txd_name, _, tex_name = s.key.partition("/")
        try:
            b = self.find_blob(Sid("txd", txd_name, s.layer))
        except SatkError:
            raise self.not_found(s, self.suggest("tex", s.key), "texture") from None
        rows = self.tex_rows("WHERE t.blob_id = ? AND x.name = ? ORDER BY x.idx LIMIT 1", (b["id"], tex_name))
        if not rows:
            names = [f"{txd_name}/{r[0].lower()}" for r in self.c.execute(
                "SELECT x.name FROM texture x JOIN txd t ON t.id = x.txd_id WHERE t.blob_id = ?", (b["id"],))]
            raise self.not_found(s, names, "texture")
        return rows[0]

    def tex_ref(self, x: dict) -> TexRef:
        pal = x["pal_off"]
        return TexRef(self.tex_sid(x), "pix:" + bytes(x["hash"]).hex(), self.path_of(x["relpath"]), int(x["data_off"]),
                      int(x["data_size"]), None if pal is None else int(pal), x["d3dfmt"], int(x["raster_fmt"]),
                      int(x["platform"]), int(x["w"]), int(x["h"]), int(x["levels"]), bool(x["alpha"]))

    # ================================================================== placements

    _INST_SQL = ("SELECT i.*, p.name AS ipl_name, p.kind AS ipl_kind, l.name AS layer, "
                 "(SELECT name FROM model WHERE id = i.model_id AND active = 1) AS mname "
                 "FROM inst i JOIN ipl p ON p.id = i.ipl_id JOIN layer l ON l.id = p.layer_id ")

    @staticmethod
    def inst_sid(i: dict) -> str:
        return f"inst:{i['ipl_name'].lower()}#{i['idx']}"

    def lod_sid(self, lod_id: int | None) -> str | None:
        if lod_id is None:
            return None
        r = self.one("SELECT p.name AS ipl_name, i.idx FROM inst i JOIN ipl p ON p.id = i.ipl_id WHERE i.id = ?",
                     (lod_id,))
        return self.inst_sid(r) if r else None

    def inst_cells(self, i: dict) -> list:
        rz = world_rz((i["qx"], i["qy"], i["qz"], i["qw"]))
        return [self.inst_sid(i), f"model:{i['model_id']}", i["mname"], env.round_pos((i["x"], i["y"], i["z"])),
                None if rz is None else env.round_angle(rz), i["area"], bool(i["is_lod"])]

    def aabb(self, iid: int) -> tuple:
        r = self.c.execute("SELECT minx, miny, minz, maxx, maxy, maxz FROM inst_rtree WHERE id = ?", (iid,)).fetchone()
        return tuple(r) if r else (0.0,) * 6

    def find_inst(self, s: Sid) -> dict:
        ipl, _, idx = s.key.partition("#")
        i = self.one(self._INST_SQL + "WHERE p.name = ? AND i.idx = ? ORDER BY p.id LIMIT 1", (ipl, int(idx)))
        if i is None:
            # Read actual indices: an IPL can be empty or have gaps (e.g. a filtered fixture).
            rows = self.c.execute("SELECT DISTINCT i.idx FROM inst i JOIN ipl p ON p.id=i.ipl_id "
                                  "WHERE p.name=? ORDER BY abs(i.idx - ?), i.idx LIMIT 3", (ipl, int(idx)))
            cands = [f"inst:{ipl}#{r[0]}" for r in rows]
            if not cands:
                names = difflib.get_close_matches(ipl, self.suggest("ipl", ipl), n=3, cutoff=0.6)
                cands = [f"ipl:{name}" for name in names]
            raise SatkError("NOT_FOUND", f"no placement {s.key!r}",
                            hint=f"satk asset find {ipl} --kind ipl", did_you_mean=cands)
        return i

    def inst_row(self, i: dict) -> InstRow:
        return InstRow(self.inst_sid(i), int(i["model_id"]), i["mname"] or "", (i["x"], i["y"], i["z"]),
                       (i["qx"], i["qy"], i["qz"], i["qw"]), int(i["area"]), int(i["iflags"]), self.lod_sid(i["lod_id"]),
                       bool(i["is_lod"]), self.aabb(i["id"]))

    # ================================================================== get

    def get(self, sid: str, fields: list[str] | None) -> dict:
        s = as_sid(sid)
        fn = getattr(self, f"_get_{s.kind}")
        return env.select_fields(fn(s), fields)

    def _get_model(self, s: Sid) -> dict:
        m = self.find_model(s)
        links: dict[str, Any] = {"ide": f"ide:{m['ide_rel']}"}
        out: dict[str, Any] = {}
        if m["active"]:
            lk = self.model_link(m["id"]) or {}
            db = self.dff_blob_of(lk)
            col = self.col_row(lk.get("col_id"))
            others = self.all("SELECT m.id, m.active, l.name AS layer FROM model m JOIN layer l ON l.id = m.layer_id "
                              "WHERE m.id = ? AND m.rid != ? ORDER BY m.rid", (m["id"], m["rid"]))
            links.update(dff=self.blob_sid(db[1]) if db else None,
                         txd_chain=[self.txd_sid(b, t) for b, t, _ in self.txd_chain(m)],
                         col=f"col:{col['name'].lower()}" if col else None, col_via=lk.get("col_via"),
                         overrides=[self.model_sid(o) for o in others])
            out["n_inst"] = lk.get("n_inst", 0)
            out["tex"] = {"total": lk.get("tex_total", 0), "missing": lk.get("tex_missing", 0)}
            if db:
                d = db[0]
                out["geo"] = {"verts": d["verts"], "tris": d["tris"],
                              "bs": env.round_pos((d["bs_x"], d["bs_y"], d["bs_z"], d["bs_r"]))
                              if d["bs_r"] is not None else None}
        else:
            out["active"] = False
            links["overridden_by"] = f"model:{m['id']}"
        extra = json.loads(m["extra"]) if m["extra"] else None
        out.update(self.model_data(m, extra, links))
        return env.obj(self.model_sid(m), name=m["name"], sec=m["sec"], txd=m["txd"], layer=m["layer"],
                       draw=m["draw"], flags=m["flags"], extra=extra, line=m["line"], **out, links=links)

    def blob_obj(self, b: dict, sid: str, **extra) -> dict:
        w = self.blob(b["shadowed_by"]) if b["shadowed_by"] else None
        return env.obj(sid, name=b["name"], file=f"file:{self.file_key(b)}", layer=b["layer"], active=bool(b["active"]),
                       shadowed_by=f"file:{self.file_key(w)}" if w else None, ns=b["ns"], size=b["size"],
                       path=jpath(self.path_of(b["relpath"])), offset=b["abs_off"], **extra)

    def _get_dff(self, s: Sid) -> dict:
        b = self.find_blob(s)
        d = self.one("SELECT * FROM dff WHERE blob_id = ?", (b["id"],))
        if d is None:
            raise SatkError("NOT_FOUND", f"{s} could not be parsed as a DFF", hint="satk index build (see meta.errors)")
        users = [f"model:{r[0]}" for r in self.c.execute(
            "SELECT id FROM model_link WHERE dff_id = ? ORDER BY id", (d["id"],))]
        bbox = None if d["bmin_x"] is None else env.round_pos(
            (d["bmin_x"], d["bmin_y"], d["bmin_z"])) + env.round_pos((d["bmax_x"], d["bmax_y"], d["bmax_z"]))
        bs = None if d["bs_r"] is None else env.round_pos((d["bs_x"], d["bs_y"], d["bs_z"], d["bs_r"]))
        return self.blob_obj(b, self.blob_sid(b) if not s.layer else str(s), verts=d["verts"], tris=d["tris"],
                             geoms=d["geoms"], materials=d["materials"], flags=d["flags"], bbox=bbox, bsphere=bs,
                             models=users)

    def txd_sid(self, b: dict, t: dict | None = None) -> str:
        v = self.variant(b)
        if v == "" and self.canon_blob_id(b["stem"], "txd") != b["id"]:
            v = None
        return f"txd:{b['stem'].lower()}{v}" if v is not None else f"file:{self.file_key(b)}"

    def _get_txd(self, s: Sid) -> dict:
        b = self.find_blob(s)
        t = self.txd_of_blob(b["id"])
        if t is None:
            raise SatkError("NOT_FOUND", f"{s} is not a parsed TXD", hint="satk index build (see meta.errors)")
        return self.blob_obj(b, self.txd_sid(b, t), tex_count=t["tex_count"],
                             parent=f"txd:{t['parent'].lower()}" if t["parent"] else None, parent_via=t["parent_via"])

    def _get_tex(self, s: Sid) -> dict:
        x = self.find_tex(s)
        b = self.blob(x["blob_id"])
        return env.obj(self.tex_sid(x), name=x["name"], txd=self.txd_sid(b), w=x["w"], h=x["h"], d3dfmt=x["d3dfmt"],
                       raster_fmt=x["raster_fmt"], platform=x["platform"], levels=x["levels"], alpha=bool(x["alpha"]),
                       pix="pix:" + bytes(x["hash"]).hex(), active=bool(x["active"]))

    def _get_pix(self, s: Sid) -> dict:
        img = self.one("SELECT * FROM image WHERE hash = ?", (bytes.fromhex(s.key),))
        if img is None:
            raise self.not_found(s, [], "pixel hash")
        xs = self.tex_rows("WHERE x.hash = ? ORDER BY (b.active = 0), x.id", (bytes.fromhex(s.key),))
        return env.obj(str(s), w=img["w"], h=img["h"], d3dfmt=img["d3dfmt"], nbytes=img["nbytes"],
                       mean_rgba=img["mean_rgba"], textures=[self.tex_sid(x) for x in xs[:50]],
                       n_textures=len(xs) if len(xs) > 50 else None)

    def _get_file(self, s: Sid) -> dict:
        src = self.one("SELECT s.*, l.name AS layer FROM source s JOIN layer l ON l.id = s.layer_id WHERE s.relpath = ?",
                       (s.key,))
        if src is not None:
            n = self.val("SELECT count(*) FROM blob WHERE source_id = ?", (src["id"],))
            if not (src["kind"] != "img" and n == 1):
                return env.obj(f"file:{src['relpath']}", kind=src["kind"], layer=src["layer"], size=src["size"],
                               path=jpath(self.path_of(src["relpath"])), load_ref=src["load_ref"],
                               load_order=src["load_order"], entries=n if src["kind"] == "img" else None,
                               sha256=src["sha256"])
        b = self.find_blob(s)
        parsed = None
        if b["ext"] == "txd" and self.txd_of_blob(b["id"]):
            parsed = self.txd_sid(b)
        elif b["ext"] == "dff" and self.val("SELECT id FROM dff WHERE blob_id = ?", (b["id"],)):
            parsed = self.blob_sid(b) if b["active"] else None
        elif b["ext"] == "ipl":
            p = self.val("SELECT name FROM ipl WHERE blob_id = ?", (b["id"],))
            parsed = f"ipl:{p.lower()}" if p else None
        elif b["ext"] == "ifp":
            parsed = f"ifp:{b['stem'].lower()}" if b["active"] else None
        sid = f"file:{self.file_key(b)}"
        return self.blob_obj(b, sid, parsed=None if parsed == sid else parsed, rw_size=b["rw_size"])

    def find_col(self, s: Sid) -> dict:
        c = self.one("SELECT c.*, b.ext AS bext FROM col c JOIN blob b ON b.id = c.blob_id WHERE c.name = ? "
                     "ORDER BY (b.ext != 'col'), (c.active = 0), c.id LIMIT 1", (s.key,))
        if c is None or not c["active"]:
            if c is None:
                raise self.not_found(s, self.suggest("col", s.key), "collision model")
        return c

    def _get_col(self, s: Sid) -> dict:
        c = self.find_col(s)
        b = self.blob(c["blob_id"])
        via = "embedded" if c["bext"] == "dff" else "colfile"
        users = [f"model:{r[0]}" for r in self.c.execute("SELECT id FROM model_link WHERE col_id = ? ORDER BY id",
                                                           (c["id"],))]
        bbox = env.round_pos((c["bmin_x"], c["bmin_y"], c["bmin_z"])) + env.round_pos((c["bmax_x"], c["bmax_y"],
                                                                                        c["bmax_z"]))
        return env.obj(f"col:{c['name'].lower()}", name=c["name"], version=c["version"], via=via, idx=c["idx"],
                       file=f"file:{self.file_key(b)}", bbox=bbox, active=bool(c["active"]),
                       spheres=c["spheres"], boxes=c["boxes"], faces=c["faces"], models=users)

    def _get_ide(self, s: Sid) -> dict:
        r = self.one("SELECT i.id, s.load_ref, l.name AS layer FROM ide i JOIN source s ON s.id = i.source_id "
                     "JOIN layer l ON l.id = s.layer_id WHERE s.relpath = ?", (s.key,))
        if r is None:
            raise self.not_found(s, [x[0] for x in self.c.execute(
                "SELECT s.relpath FROM ide i JOIN source s ON s.id = i.source_id")], "loaded IDE")
        n = self.val("SELECT count(*) FROM model WHERE ide_id = ?", (r["id"],))
        return env.obj(f"ide:{s.key}", layer=r["layer"], load_ref=r["load_ref"], models=n)

    def find_ipl(self, s: Sid) -> dict:
        p = self.one("SELECT p.*, l.name AS layer FROM ipl p JOIN layer l ON l.id = p.layer_id WHERE p.name = ? "
                     "ORDER BY p.id LIMIT 1", (s.key,))
        if p is None:
            raise self.not_found(s, self.suggest("ipl", s.key), "IPL")
        return p

    def _get_ipl(self, s: Sid) -> dict:
        p = self.find_ipl(s)
        if p["source_id"] is not None:
            f = "file:" + self.val("SELECT relpath FROM source WHERE id = ?", (p["source_id"],))
        else:
            f = "file:" + self.file_key(self.blob(p["blob_id"]))
        parent = self.val("SELECT name FROM ipl WHERE id = ?", (p["parent_id"],)) if p["parent_id"] else None
        n = self.val("SELECT count(*) FROM inst WHERE ipl_id = ?", (p["id"],))
        items = {r[0]: r[1] for r in self.c.execute("SELECT sec, count(*) FROM ipl_item WHERE ipl_id = ? GROUP BY sec",
                                                    (p["id"],))}
        return env.obj(f"ipl:{p['name'].lower()}", kind=p["kind"], file=f,
                       parent=f"ipl:{parent.lower()}" if parent else None, loaded=bool(p["loaded"]), layer=p["layer"],
                       n_inst=n, items=items)

    def _get_inst(self, s: Sid) -> dict:
        i = self.find_inst(s)
        q = (i["qx"], i["qy"], i["qz"], i["qw"])
        rz = world_rz(q)
        rot: dict[str, Any] = {"rz": env.round_angle(rz)} if rz is not None else {"q": env.round_quat(world_quat(q))}
        return env.obj(self.inst_sid(i), model=f"model:{i['model_id']}", name=i["mname"],
                       pos=env.round_pos((i["x"], i["y"], i["z"])), **rot, area=i["area"], iflags=i["iflags"],
                       lod=self.lod_sid(i["lod_id"]), is_lod=bool(i["is_lod"]), ipl=f"ipl:{i['ipl_name'].lower()}",
                       layer=i["layer"], aabb=env.round_pos(self.aabb(i["id"])), bbox_src=i["bbox_src"])

    def find_item(self, s: Sid) -> dict:
        ipl, sec, idx = s.key.split("#")
        it = self.one("SELECT t.*, p.name AS ipl_name FROM ipl_item t JOIN ipl p ON p.id = t.ipl_id "
                      "WHERE p.name = ? AND t.sec = ? AND t.idx = ? LIMIT 1", (ipl, sec, int(idx)))
        if it is None:
            raise self.not_found(s, [], "IPL item")
        return it

    def _get_item(self, s: Sid) -> dict:
        it = self.find_item(s)
        pos = env.round_pos((it["x"], it["y"], it["z"])) if it["x"] is not None else None
        return env.obj(f"item:{it['ipl_name'].lower()}#{it['sec']}#{it['idx']}", sec=it["sec"], pos=pos,
                       ipl=f"ipl:{it['ipl_name'].lower()}", data=json.loads(it["data"]))

    def find_zone(self, s: Sid) -> dict:
        z = self.one("SELECT z.*, s.relpath FROM zone z JOIN source s ON s.id = z.source_id WHERE z.name = ? "
                     "ORDER BY z.id LIMIT 1", (s.key,))
        if z is None:
            raise self.not_found(s, self.suggest("zone", s.key), "zone")
        return z

    def _get_zone(self, s: Sid) -> dict:
        z = self.find_zone(s)
        n = self.val("SELECT count(*) FROM zone WHERE name = ?", (s.key,))
        return env.obj(f"zone:{z['name'].lower()}", name=z["name"], title=z["title"], label=z["label"],
                       type=z["type"], level=z["level"],
                       min=env.round_pos((z["minx"], z["miny"], z["minz"])),
                       max=env.round_pos((z["maxx"], z["maxy"], z["maxz"])), file=f"file:{z['relpath']}",
                       n_rows=n if n > 1 else None)

    def find_ifp(self, s: Sid) -> dict:
        f = self.one("SELECT f.*, b.ns, b.active FROM ifp f JOIN blob b ON b.id = f.blob_id WHERE f.name = ? "
                     "ORDER BY (b.active = 0), (b.ns NOT IN ('main','loose')), f.id LIMIT 1", (s.key,))
        if f is None:
            raise self.not_found(s, self.suggest("ifp", s.key), "animation package")
        return f

    def _get_ifp(self, s: Sid) -> dict:
        f = self.find_ifp(s)
        b = self.blob(f["blob_id"])
        return env.obj(f"ifp:{f['name'].lower()}", name=f["name"], format=f["format"], anims=f["anim_count"],
                       file=f"file:{self.file_key(b)}", ns=b["ns"], active=bool(b["active"]))

    def find_anim(self, s: Sid) -> dict:
        pack, _, name = s.key.partition("/")
        f = self.find_ifp(Sid("ifp", pack))
        a = self.one("SELECT * FROM anim WHERE ifp_id = ? AND name = ? ORDER BY idx LIMIT 1", (f["id"], name))
        if a is None:
            names = [f"{pack}/{r[0].lower()}" for r in self.c.execute("SELECT name FROM anim WHERE ifp_id = ?",
                                                                      (f["id"],))]
            raise self.not_found(s, names, "animation")
        a["ifp_name"] = f["name"]
        return a

    def _get_anim(self, s: Sid) -> dict:
        a = self.find_anim(s)
        return env.obj(f"anim:{a['ifp_name'].lower()}/{a['name'].lower()}", name=a["name"], idx=a["idx"],
                       bones=a["bones"], frames=a["frames"], ifp=f"ifp:{a['ifp_name'].lower()}")

    # ================================================================== data files (schema v3)

    def data_file(self, source_id: int | None) -> str | None:
        rel = self.val("SELECT relpath FROM source WHERE id = ?", (source_id,)) if source_id is not None else None
        return f"file:{rel}" if rel else None

    def model_data(self, m: dict, extra: dict | None, links: dict) -> dict:
        """v3 fields of a model: handling and colours (cars), acquaintances (peds), object.dat physics.

        Only present when the index has the rows (a v3 build of a game with the data files).
        """
        out: dict[str, Any] = {}
        if m["sec"] == "cars" and m["active"]:
            hid = str((extra or {}).get("handling") or "")
            h = self.one("SELECT * FROM handling WHERE name = ? AND kind = 'car'", (hid,)) if hid else None
            if h is not None:
                links["handling"] = f"handling:{h['name'].lower()}"
                out["handling"] = {k: h[k] for k in ("mass", "max_vel", "accel", "gears", "drive", "engine", "brake",
                                                     "steer_lock", "value")}
            sets = self.all("SELECT c1, c2, c3, c4 FROM car_color WHERE model = ? ORDER BY idx", (m["name"],))
            if sets:
                cols = [[v for v in (r["c1"], r["c2"], r["c3"], r["c4"]) if v is not None] for r in sets]
                used = sorted({v for c in cols for v in c})
                pal = {r[0]: r[1] for r in self.c.execute(
                    f"SELECT idx, rgb FROM carcol WHERE idx IN ({','.join('?' * len(used))})", used)}
                out["colors"] = cols
                out["color_rgb"] = {str(i): (f"#{pal[i]:06x}" if i in pal else None) for i in used}
        elif m["sec"] == "peds" and m["active"]:
            pt = str((extra or {}).get("pedtype") or "")
            rels: dict[str, list[str]] = {}
            for rel, other in self.c.execute("SELECT rel, other FROM ped_rel WHERE pedtype = ? ORDER BY line, other",
                                             (pt,)):
                rels.setdefault(rel, []).append(other)
            if rels:
                out["pedtype_rel"] = rels
        if m["active"]:
            o = self.one("SELECT * FROM object_data WHERE name = ? ORDER BY loaded DESC, line DESC LIMIT 1", (m["name"],))
            if o is not None:
                from ..formats.objectdat import COL_RESPONSES, DMG_EFFECTS

                out["physics"] = {
                    "mass": o["mass"], "turn_mass": o["turn_mass"], "elasticity": o["elasticity"],
                    "dmg_mult": o["dmg_mult"], "dmg_effect": DMG_EFFECTS.get(o["dmg_effect"], o["dmg_effect"]),
                    "col_response": COL_RESPONSES.get(o["col_response"], o["col_response"]),
                    "explodes": bool(o["explodes"]) or None, "fx": o["fx_name"] if o["fx_type"] else None,
                    "loaded": None if o["loaded"] else False, "line": o["line"]}
        return out

    @staticmethod
    def _hex(v: int | None, digits: int = 6) -> str | None:
        return None if v is None else f"#{v:0{digits}x}"

    # ---- handling:<id>

    def handling_rows(self, s) -> list[dict]:
        rows = self.all("SELECT * FROM handling WHERE name = ? ORDER BY CASE kind WHEN 'car' THEN 0 WHEN 'bike' THEN 1 "
                        "WHEN 'boat' THEN 2 ELSE 3 END", (s.key,))
        if not rows:
            raise self.not_found(s, self.suggest("handling", s.key), "handling id")
        return rows

    def _get_handling(self, s) -> dict:
        from ..formats.handling import HANDLING_FLAGS, MODEL_FLAGS, flag_names

        rows = self.handling_rows(s)
        out: dict[str, Any] = {}
        for r in rows:
            d = json.loads(r["data"])
            if r["kind"] == "car":
                out["model_flags"] = flag_names(int(d["model_flags"]), MODEL_FLAGS)
                out["handling_flags"] = flag_names(int(d["handling_flags"]), HANDLING_FLAGS)
                d["model_flags"] = f"0x{int(d['model_flags']):x}"
                d["handling_flags"] = f"0x{int(d['handling_flags']):x}"
            out[r["kind"]] = d
        users = self.handling_users(rows[0]["name"])
        return env.obj(f"handling:{rows[0]['name'].lower()}", name=rows[0]["name"], kinds=[r["kind"] for r in rows],
                       **out, models=[u[0] for u in users[:50]], n_models=len(users) if len(users) > 50 else None,
                       file=self.data_file(rows[0]["source_id"]), line=rows[0]["line"])

    def handling_users(self, name: str) -> list[tuple[str, str, str]]:
        """``(model sid, name, info)`` of active ``cars`` definitions whose IDE ``handling`` is ``name``."""
        return [(f"model:{i}", n, f"{json.loads(x).get('type', '')} {json.loads(x).get('class', '')}".strip())
                for i, n, x in self.c.execute(
                    "SELECT id, name, extra FROM model WHERE active = 1 AND sec = 'cars' "
                    "AND lower(json_extract(extra, '$.handling')) = lower(?) ORDER BY id", (name,))]

    # ---- tcyc:<weather>[/<hour>]

    def find_tcyc(self, s) -> tuple[dict, list[dict]]:
        """``(weather row, rows of the requested hour or of all hours)``."""
        w, _, h = s.key.partition("/")
        where, p = ("weather = ?", [int(w)]) if w.isdigit() else ("weather_name = ?", [w])
        rows = self.all(f"SELECT * FROM timecyc WHERE {where} ORDER BY hour", p)
        if not rows:
            names = [r[0].lower() for r in self.c.execute("SELECT DISTINCT weather_name FROM timecyc ORDER BY weather")]
            raise self.not_found(s, names, "weather")
        if h:
            sel = [r for r in rows if h.isdigit() and r["hour"] == int(h)]
            if not sel:
                raise SatkError("NOT_FOUND", f"no time cycle hour {h!r} for weather {rows[0]['weather_name']}",
                                hint=f"hours: {' '.join(str(r['hour']) for r in rows)}",
                                did_you_mean=[f"tcyc:{rows[0]['weather_name'].lower()}/{r['hour']}" for r in rows[:3]])
            return rows[0], sel
        return rows[0], rows

    @staticmethod
    def tcyc_sid(r: dict, hour: bool = True) -> str:
        return f"tcyc:{r['weather_name'].lower()}" + (f"/{r['hour']}" if hour else "")

    def _get_tcyc(self, s) -> dict:
        w, rows = self.find_tcyc(s)
        if "/" not in s.key:
            return env.obj(self.tcyc_sid(w, False), weather=w["weather"], weather_name=w["weather_name"],
                           hours=[self.tcyc_sid(r) for r in rows],
                           far_clip=[r["far_clip"] for r in rows], file=self.data_file(w["source_id"]))
        r = rows[0]
        d = json.loads(r["data"])
        rgb = {k: self._hex(r[k]) for k in ("amb", "amb_obj", "dir", "sky_top", "sky_bot", "sun_core", "sun_corona",
                                            "low_clouds", "bottom_clouds")}
        return env.obj(self.tcyc_sid(r), weather=r["weather"], weather_name=r["weather_name"], hour=r["hour"], **rgb,
                       water=self._hex(r["water"], 8), sun_size=r["sun_size"], sprite_size=r["sprite_size"],
                       sprite_bright=r["sprite_bright"], shadow=r["shadow"], light_shadow=r["light_shadow"],
                       pole_shadow=r["pole_shadow"], far_clip=r["far_clip"], fog_start=r["fog_start"],
                       light_on_ground=r["light_on_ground"], postfx1=d.get("postfx1"), postfx2=d.get("postfx2"),
                       cloud_alpha=d.get("cloud_alpha"), high_light_min=d.get("high_light_min"),
                       water_fog_alpha=d.get("water_fog_alpha"), dir_mult=d.get("dir_mult"),
                       nread=r["nread"] if r["nread"] < 51 else None, file=self.data_file(r["source_id"]),
                       line=r["line"])

    # ---- water:<idx> | water:water1/<idx>

    def find_water(self, s) -> dict:
        f, _, i = s.key.rpartition("/")
        config = {"": 0, "water": 0, "water1": 1}.get(f)
        if config is None or not i.isdigit():
            raise SatkError("BAD_ID", f"water SID key must be <idx> or water1/<idx>, got {s.key!r}",
                            hint="satk help ids", did_you_mean=["water:0", "water:water1/0"])
        w = self.one("SELECT * FROM water_quad WHERE config = ? AND idx = ?", (config, int(i)))
        if w is None:
            n = self.val("SELECT count(*) FROM water_quad WHERE config = ?", (config,))
            raise SatkError("NOT_FOUND", f"no water polygon {s.key!r} ({n} in {'water1' if config else 'water'}.dat)",
                            hint="satk index query \"SELECT idx, minx, miny, maxx, maxy FROM water_quad\"",
                            did_you_mean=[f"{s.kind}:{f + '/' if config else ''}{max(0, n - 1)}"] if n else [])
        return w

    @staticmethod
    def water_sid(w: dict) -> str:
        return f"water:water1/{w['idx']}" if w["config"] else f"water:{w['idx']}"

    def _get_water(self, s) -> dict:
        from ..formats.water import FLAG_SHALLOW, FLAG_VISIBLE

        w = self.find_water(s)
        vs = json.loads(w["verts"])
        fl = w["flags"]
        return env.obj(self.water_sid(w), kind="quad" if w["nverts"] == 4 else "tri", config=w["config"], flags=fl,
                       visible=None if fl is None else bool(fl & FLAG_VISIBLE),
                       shallow=None if fl is None else bool(fl & FLAG_SHALLOW) or None,
                       verts=[env.round_pos(v[:3]) for v in vs], waves=[v[3:] for v in vs],
                       bbox=env.round_pos((w["minx"], w["miny"], w["minz"])) + env.round_pos((w["maxx"], w["maxy"],
                                                                                              w["maxz"])),
                       file=self.data_file(w["source_id"]), line=w["line"])

    # ================================================================== find

    def info(self, kind: str, sid: str) -> str:
        try:
            s = parse_sid(sid)
            if kind == "handling":
                rows = self.handling_rows(s)
                car = next((r for r in rows if r["kind"] == "car"), None)
                return "+".join(r["kind"] for r in rows) + (f" {car['mass']:g} kg" if car and car["mass"] else "")
            if kind == "tcyc":
                w, rows = self.find_tcyc(s)
                return f"weather {w['weather']}, {len(rows)} hours"
            if kind == "model":
                m = self.find_model(s)
                return f"{m['sec']} {m['ide_rel'].rsplit('/', 1)[-1]}"
            if kind == "dff":
                b = self.find_blob(s)
                d = self.one("SELECT tris FROM dff WHERE blob_id = ?", (b["id"],))
                return f"{d['tris']} tris" if d else ""
            if kind == "txd":
                b = self.find_blob(s)
                t = self.txd_of_blob(b["id"])
                return f"{t['tex_count']} tex" if t else ""
            if kind == "tex":
                x = self.find_tex(s)
                return f"{x['d3dfmt']} {x['w']}x{x['h']}"
            if kind == "col":
                c = self.find_col(s)
                return f"COL{c['version']} {'embedded' if c['bext'] == 'dff' else 'colfile'}"
            if kind == "ipl":
                p = self.find_ipl(s)
                return p["kind"]
            if kind == "zone":
                z = self.find_zone(s)
                return z["title"] or z["label"] or ""
            if kind == "ifp":
                f = self.find_ifp(s)
                return f"{f['format']} {f['anim_count']} anims"
            if kind == "anim":
                a = self.find_anim(s)
                return f"{a['bones']} bones {a['frames']} frames"
        except SatkError:
            return ""
        return ""

    def find(self, q: str, kind: str | None, limit: int, cursor: str | None) -> dict:
        if kind is not None:
            check_choice("kind", kind, SEARCH_KINDS)
        lim = env.clamp_limit(limit)
        needle = str(q or "").strip()
        if not needle:
            raise SatkError("BAD_PARAMS", "empty search string")
        off = cursor_offset(cursor)
        if kind == "file":
            return self._find_files(needle.strip("*"), lim, off)
        order = "CASE kind " + " ".join(f"WHEN '{k}' THEN {i}" for i, k in enumerate(KIND_ORDER)) + " ELSE 99 END"
        kq = " AND kind = :kind" if kind is not None else ""
        warn: list[str] = []
        if "*" in needle or "?" in needle:
            # explicit pattern: '*rooftarmac*' substring, 'ws_roof*' prefix, '?' one character
            core = max((p for p in needle.replace("?", "*").split("*")), key=len)
            low = needle.lower()
            params: dict[str, Any] = {"g": low.replace("[", "[[]"), "kind": kind}
            where = "lower(name) GLOB :g"
            if len(core) >= MIN_TRIGRAM:
                where = "fts_name MATCH :m AND " + where
                params["m"] = match_expr(core)
            rank = "''"
        else:
            low = needle.lower()
            params = {"low": low, "pfx": _like_escape(low) + "%", "kind": kind}
            if len(needle) >= MIN_TRIGRAM:
                params["m"] = match_expr(needle)
                exact = self.val(f"SELECT count(*) FROM fts_name WHERE fts_name MATCH :m AND lower(name) = :low{kq}",
                                 params)
                where = "fts_name MATCH :m"
                if exact:
                    more = self.val(f"SELECT count(*) FROM fts_name WHERE fts_name MATCH :m{kq}", params) - exact
                    where += " AND lower(name) = :low"
                    if more:
                        warn.append(f"MORE: {more} other names contain {needle!r}: asset find '*{needle}*'")
            else:
                where = "name LIKE :pfx ESCAPE '\\'"
            rank = "CASE WHEN lower(name) = :low THEN 0 WHEN lower(name) LIKE :pfx ESCAPE '\\' THEN 1 ELSE 2 END"
        where += kq
        total = self.val(f"SELECT count(*) FROM fts_name WHERE {where}", params)
        rows = self.c.execute(f"SELECT sid, kind, name FROM fts_name WHERE {where} "
                              f"ORDER BY {rank}, {order}, lower(name), (sid LIKE 'file:%' OR instr(sid, '@') > 0), sid LIMIT :lim OFFSET :off",
                              {**params, "lim": lim, "off": off}).fetchall()
        out = [[sid, k, name, self.info(k, sid)] for sid, k, name in rows]
        nxt = f"o{off + lim}" if off + lim < total else None
        return env.table(FIND_COLS, out, total=total, next=nxt, warn=warn)

    def _find_files(self, needle: str, lim: int, off: int) -> dict:
        pat = "%" + _like_escape(needle.lower()) + "%"
        base = ("FROM blob b JOIN source s ON s.id = b.source_id JOIN layer l ON l.id = s.layer_id "
                "WHERE (CASE WHEN s.kind = 'img' THEN s.relpath || '/' || lower(b.name) ELSE s.relpath END) "
                "LIKE ? ESCAPE '\\'")
        total = self.val("SELECT count(*) " + base, (pat,))
        rows = self.all("SELECT b.id, b.name, b.size, b.active, s.relpath, s.kind AS skind, l.name AS layer " + base +
                        " ORDER BY (lower(b.name) != ?), s.relpath, b.idx LIMIT ? OFFSET ?",
                        (pat, needle.lower(), lim, off))
        out = [[f"file:{self.file_key(r)}", "file", r["name"],
                f"{r['size']} B {r['layer']}" + ("" if r["active"] else " shadowed")] for r in rows]
        nxt = f"o{off + lim}" if off + lim < total else None
        return env.table(FIND_COLS, out, total=total, next=nxt)

    # ================================================================== refs

    def refs(self, sid: str, rel: str | None, limit: int, cursor: str | None) -> dict:
        s = as_sid(sid)
        rels = REFS_RELS.get(s.kind, ())
        lim = env.clamp_limit(limit, default=50)
        if s.kind == "water":  # no relations: still NOT_FOUND for unknown polygons, canonical id
            s = as_sid(self.water_sid(self.find_water(s)))
        elif s.kind == "handling":
            s = as_sid(f"handling:{self.handling_rows(s)[0]['name'].lower()}")
        elif s.kind == "tcyc":
            w, rows = self.find_tcyc(s)
            s = as_sid(self.tcyc_sid(rows[0]) if "/" in s.key else self.tcyc_sid(w, False))
        if rel is None:
            counts = {r: self._refs(s, r, count_only=True)[1] for r in rels}  # NOT_FOUND for unknown objects
            ident = self.model_sid(self.find_model(s)) if s.kind == "model" else str(s)
            return env.obj(ident, rels=counts)
        if rel not in rels:
            raise SatkError("BAD_PARAMS", f"rel for {s.kind} must be one of {', '.join(rels)}, got {rel!r}",
                            data={"rels": list(rels)})
        if s.kind == "zone" and rel == "inst":
            off = cursor_offset(cursor)
            _cols, total = self._zone_insts(s, count_only=True)
            cols, chunk = self._zone_insts(s, limit=lim, offset=off)
            return env.table(cols, chunk, total=total, next=f"o{off + lim}" if off + lim < total else None)
        cols, rows = self._refs(s, rel)
        off = cursor_offset(cursor)
        chunk = rows[off:off + lim]
        nxt = f"o{off + lim}" if off + lim < len(rows) else None
        return env.table(cols, chunk, total=len(rows), next=nxt)

    def _insts(self, where: str, params: Sequence) -> list[list]:
        return [self.inst_cells(i) for i in self.all(self._INST_SQL + where, params)]

    def _zone_insts(self, sid: Sid, *, count_only: bool = False, limit: int | None = None, offset: int = 0):
        """Count and page zone placements in SQLite, without materializing the entire zone."""
        zone = self.find_zone(sid)
        x0, x1 = sorted((zone["minx"], zone["maxx"]))
        y0, y1 = sorted((zone["miny"], zone["maxy"]))
        where = "WHERE i.x BETWEEN ? AND ? AND i.y BETWEEN ? AND ?"
        params = [x0, x1, y0, y1]
        if zone["minz"] is not None:
            where += " AND i.z BETWEEN ? AND ?"
            params += [zone["minz"], zone["maxz"]]
        if count_only:
            return None, self.val("SELECT count(*) FROM inst i " + where, params)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        # Same distance-to-zone-centre and lexical SID order as near_insts.
        where += " ORDER BY (i.x-?)*(i.x-?)+(i.y-?)*(i.y-?), lower(p.name) || '#' || i.idx"
        params += [cx, cx, cy, cy]
        if limit is not None:
            where += " LIMIT ? OFFSET ?"
            params += [limit, offset]
        return INST_COLS, self._insts(where, params)

    def _refs(self, s: Sid, rel: str, count_only: bool = False):  # noqa: C901 - one branch per relation
        """``(cols, rows)``; with ``count_only`` -> ``(None, n)``."""
        k = s.kind

        def gen(items):
            if count_only:
                return None, len(items)
            return GENERIC_COLS, [list(x) for x in items]

        def insts(where: str, params: Sequence, order: str = " ORDER BY p.name, i.idx"):
            if count_only:
                return None, self.val("SELECT count(*) FROM inst i JOIN ipl p ON p.id = i.ipl_id " + where, params)
            return INST_COLS, self._insts(where + order, params)

        if k == "model":
            m = self.find_model(s)
            act = bool(m["active"])
            if rel == "inst":
                return insts("WHERE i.model_id = ?" + ("" if act else " AND 0"), (m["id"],))
            if rel == "tex":
                rows = self.all("SELECT mt.texture, mt.texture_id, mt.via, mt.uses FROM model_tex mt "
                                "WHERE mt.model_id = ? ORDER BY mt.texture", (m["id"],)) if act else []
                if count_only:
                    return None, len(rows)
                out = []
                for r in rows:
                    x = self.tex_rows("WHERE x.id = ?", (r["texture_id"],))[0] if r["texture_id"] else None
                    out.append([self.tex_sid(x) if x else None, r["texture"], r["via"], r["uses"],
                                "pix:" + bytes(x["hash"]).hex() if x else None])
                return MODEL_TEX_COLS, out
            lk = self.model_link(m["id"]) if act else None
            if rel == "dff":
                db = self.dff_blob_of(lk)
                return gen([(self.blob_sid(db[1]), db[1]["name"], f"{db[0]['tris']} tris")] if db else [])
            if rel == "txd":
                return gen([(self.txd_sid(b, t), b["stem"], how) for b, t, how in self.txd_chain(m)] if act else [])
            if rel == "col":
                c = self.col_row(lk["col_id"]) if lk else None
                return gen([(f"col:{c['name'].lower()}", c["name"], lk["col_via"])] if c else [])
            if rel == "ide":
                return gen([(f"ide:{m['ide_rel']}", m["ide_rel"], f"line {m['line']}")])
            if rel == "overrides":
                rows = self.all("SELECT m.id, m.active, m.name, l.name AS layer FROM model m JOIN layer l "
                                "ON l.id = m.layer_id WHERE m.id = ? AND m.rid != ? ORDER BY m.rid", (m["id"], m["rid"]))
                return gen([(self.model_sid(o), o["name"], o["layer"]) for o in rows])
        if k == "handling" and rel == "models":
            self.handling_rows(s)  # NOT_FOUND for unknown ids
            return gen(self.handling_users(s.key))
        if k == "tcyc" and rel == "hours":
            _w, rows = self.find_tcyc(as_sid(f"tcyc:{s.key.partition('/')[0]}"))
            return gen([(self.tcyc_sid(r), str(r["hour"]),
                         f"far_clip {r['far_clip']:g} fog_start {r['fog_start']:g}") for r in rows])
        if k == "tex":
            x = self.find_tex(s)
            if rel == "models":
                rows = self.all("SELECT m.id, m.name, m.sec FROM model_tex mt JOIN model m ON m.id = mt.model_id "
                                "AND m.active = 1 WHERE mt.texture_id = ? ORDER BY m.id", (x["id"],))
                return gen([(f"model:{r['id']}", r["name"], r["sec"]) for r in rows])
            if rel == "same_pixels":
                xs = self.tex_rows("WHERE x.hash = ? AND x.id != ? ORDER BY x.id", (x["hash"], x["id"]))
                if count_only:
                    return None, len(xs)
                return gen([(self.tex_sid(t), t["name"], self.txd_sid(self.blob(t["blob_id"]))) for t in xs])
            if rel == "txd":
                return gen([(self.txd_sid(self.blob(x["blob_id"])), x["txd_name"], "")])
        if k == "pix":
            xs = self.tex_rows("WHERE x.hash = ? ORDER BY (b.active = 0), x.id", (bytes.fromhex(s.key),))
            if not xs:
                raise self.not_found(s, [], "pixel hash")
            return gen([(self.tex_sid(t), t["name"], f"{t['w']}x{t['h']} {t['d3dfmt']}") for t in xs])
        if k in ("txd", "dff", "col") and rel == "file":
            b = self.blob(self.find_col(s)["blob_id"]) if k == "col" else self.find_blob(s)
            return gen([(f"file:{self.file_key(b)}", b["name"], b["layer"])])
        if k == "txd":
            b = self.find_blob(s)
            t = self.txd_of_blob(b["id"])
            if t is None:
                raise SatkError("NOT_FOUND", f"{s} is not a parsed TXD")
            if rel == "textures":
                xs = self.tex_rows("WHERE x.txd_id = ? ORDER BY x.idx", (t["id"],))
                return gen([(self.tex_sid(x), x["name"], f"{x['w']}x{x['h']} {x['d3dfmt']}") for x in xs])
            if rel == "models":
                rows = self.all("SELECT id, name, sec FROM model WHERE active = 1 AND txd = ? ORDER BY id", (b["stem"],))
                return gen([(f"model:{r['id']}", r["name"], r["sec"]) for r in rows])
            if rel == "parent":
                p = self.active_txd_blob(t["parent"])
                return gen([(self.txd_sid(p), p["stem"], t["parent_via"] or "")] if p else [])
            if rel == "children":
                rows = self.all("SELECT t.*, b.id AS bid FROM txd t JOIN blob b ON b.id = t.blob_id WHERE t.parent = ? "
                                "AND b.active = 1 ORDER BY t.name", (b["stem"],))
                return gen([(self.txd_sid(self.blob(r["bid"])), r["name"], r["parent_via"] or "") for r in rows])
        if k == "dff" and rel == "models":
            b = self.find_blob(s)
            rows = self.all("SELECT m.id, m.name, m.sec FROM model_link ml JOIN dff d ON d.id = ml.dff_id "
                            "JOIN model m ON m.id = ml.id AND m.active = 1 WHERE d.blob_id = ? ORDER BY m.id", (b["id"],))
            return gen([(f"model:{r['id']}", r["name"], r["sec"]) for r in rows])
        if k == "col" and rel == "models":
            c = self.find_col(s)
            rows = self.all("SELECT m.id, m.name, m.sec FROM model_link ml JOIN model m ON m.id = ml.id AND m.active = 1 "
                            "WHERE ml.col_id = ? ORDER BY m.id", (c["id"],))
            return gen([(f"model:{r['id']}", r["name"], r["sec"]) for r in rows])
        if k == "inst":
            i = self.find_inst(s)
            if rel == "model":
                m = self.one("SELECT name, sec FROM model WHERE id = ? AND active = 1", (i["model_id"],))
                return gen([(f"model:{i['model_id']}", m["name"] if m else "", m["sec"] if m else "")])
            if rel == "lod":
                return insts("WHERE i.id = ?", (i["lod_id"] if i["lod_id"] is not None else -1,))
            if rel == "hd_children":
                return insts("WHERE i.lod_id = ?", (i["id"],))
            if rel == "ipl":
                return gen([(f"ipl:{i['ipl_name'].lower()}", i["ipl_name"], i["ipl_kind"])])
            if rel == "near":
                a = self.aabb(i["id"])
                hits = self.near_insts((a[0] + a[3]) / 2, (a[1] + a[4]) / 2, None, 50.0, None, "aabb", None, "all")
                hits = [h for h in hits if h[1]["id"] != i["id"]]
                if count_only:
                    return None, len(hits)
                return INST_COLS, [self.inst_cells(j) for _, j in hits]
        if k == "file":
            if self.val("SELECT count(*) FROM source WHERE relpath = ? AND kind = 'img'", (s.key,)):
                return gen([])  # an archive: no parsed object, never shadowed as a whole
            b = self.find_blob(s)
            if rel == "parsed":
                obj = self._get_file(s)
                return gen([(obj["parsed"], b["stem"], b["ext"])] if obj.get("parsed") else [])
            if rel == "shadowed_by":
                w = self.blob(b["shadowed_by"]) if b["shadowed_by"] else None
                return gen([(f"file:{self.file_key(w)}", w["name"], w["layer"])] if w else [])
            if rel == "shadows":
                rows = self.all(_BLOB_SQL + "WHERE b.shadowed_by = ? ORDER BY b.id", (b["id"],))
                return gen([(f"file:{self.file_key(o)}", o["name"], o["layer"]) for o in rows])
        if k == "ipl" and rel == "inst":
            p = self.find_ipl(s)
            return insts("WHERE i.ipl_id = ?", (p["id"],), " ORDER BY i.idx")
        if k == "ide" and rel == "models":
            self._get_ide(s)
            rows = self.all("SELECT m.id, m.active, m.name, m.sec, l.name AS layer FROM model m JOIN ide i ON i.id = m.ide_id "
                            "JOIN source s ON s.id = i.source_id JOIN layer l ON l.id = m.layer_id WHERE s.relpath = ? "
                            "ORDER BY m.line", (s.key,))
            return gen([(self.model_sid(m), m["name"], m["sec"]) for m in rows])
        if k == "item" and rel == "ipl":
            it = self.find_item(s)
            return gen([(f"ipl:{it['ipl_name'].lower()}", it["ipl_name"], it["sec"])])
        if k == "zone" and rel == "inst":
            return self._zone_insts(s, count_only=count_only)
        if k == "ifp" and rel == "anims":
            f = self.find_ifp(s)
            rows = self.all("SELECT * FROM anim WHERE ifp_id = ? ORDER BY idx", (f["id"],))
            return gen([(f"anim:{f['name'].lower()}/{a['name'].lower()}", a["name"],
                         f"{a['bones']} bones {a['frames']} frames") for a in rows])
        if k == "anim" and rel == "ifp":
            a = self.find_anim(s)
            return gen([(f"ifp:{a['ifp_name'].lower()}", a["ifp_name"], "")])
        return gen([])  # pragma: no cover

    # ================================================================== spatial

    def near_insts(self, x, y, z, r, box, match: str, area, lod: str, *,
                   intersect_radius: bool = False) -> list[tuple[float, dict]]:
        conds, params = [], []
        if box is not None:
            bx0, by0, bx1, by1 = (float(v) for v in box)
            bx0, bx1 = min(bx0, bx1), max(bx0, bx1)
            by0, by1 = min(by0, by1), max(by0, by1)
            if x is None or y is None:
                x, y = (bx0 + bx1) / 2, (by0 + by1) / 2
            if match == "center":
                conds.append("i.x BETWEEN ? AND ? AND i.y BETWEEN ? AND ?")
                params += [bx0, bx1, by0, by1]
                src = ""
            else:
                src = "JOIN inst_rtree rt ON rt.id = i.id AND rt.minx <= ? AND rt.maxx >= ? AND rt.miny <= ? AND rt.maxy >= ? "
                params = [bx1, bx0, by1, by0] + params
        else:
            x, y, rr = float(x), float(y), float(r)
            if match == "center":
                conds.append("i.x BETWEEN ? AND ? AND i.y BETWEEN ? AND ?")
                params += [x - rr, x + rr, y - rr, y + rr]
                src = ""
            else:
                src = "JOIN inst_rtree rt ON rt.id = i.id AND rt.minx <= ? AND rt.maxx >= ? AND rt.miny <= ? AND rt.maxy >= ? "
                params = [x + rr, x - rr, y + rr, y - rr] + params
        if area is not None:
            conds.append("i.area = ?")
            params.append(int(area))
        if lod == "hd":
            conds.append("i.is_lod = 0")
        elif lod == "lod":
            conds.append("i.is_lod = 1")
        sql = self._INST_SQL.replace("FROM inst i ", "FROM inst i " + src)
        if src:
            sql = sql.replace("SELECT i.*, ", "SELECT i.*, rt.minx AS a0, rt.miny AS a1, rt.minz AS a2, "
                                               "rt.maxx AS a3, rt.maxy AS a4, rt.maxz AS a5, ", 1)
        sql += "WHERE " + " AND ".join(conds) if conds else ""
        out: list[tuple[float, dict]] = []
        for i in self.all(sql, params):
            px, py, pz = i["x"], i["y"], i["z"]
            if box is None or intersect_radius:
                if match == "center":
                    d = math.dist((px, py) if z is None else (px, py, pz), (x, y) if z is None else (x, y, float(z)))
                else:
                    a = (i["a0"], i["a1"], i["a2"], i["a3"], i["a4"], i["a5"])
                    dx = max(a[0] - x, 0.0, x - a[3])
                    dy = max(a[1] - y, 0.0, y - a[4])
                    dz = 0.0 if z is None else max(a[2] - float(z), 0.0, float(z) - a[5])
                    d = math.sqrt(dx * dx + dy * dy + dz * dz)
                if d > float(r):
                    continue
            dist = math.dist((px, py) if z is None else (px, py, pz), (x, y) if z is None else (x, y, float(z)))
            out.append((dist, i))
        out.sort(key=lambda t: (t[0], self.inst_sid(t[1])))
        return out

    def near(self, x, y, z, r, box, match, kinds, area, lod, limit) -> dict:
        check_choice("match", match, MATCH_MODES)
        check_choice("lod", lod, LOD_MODES)
        kinds = tuple(kinds or ("inst",))
        for k in kinds:
            check_choice("kinds", k, NEAR_KINDS)
        if box is not None and len(tuple(box)) != 4:
            raise SatkError("BAD_PARAMS", "box must be (minx, miny, maxx, maxy)")
        if box is None and (x is None or y is None):
            raise SatkError("BAD_PARAMS", "give x and y (and r) or a box", hint="satk world near 2495 -1687 --r 50")
        if box is None and (r is None or float(r) < 0):
            raise SatkError("BAD_PARAMS", "r must be >= 0")
        lim = env.clamp_limit(limit, default=50)
        rows: list[tuple[float, list]] = []
        omitted_insts = 0
        if "inst" in kinds:
            hits = self.near_insts(x, y, z, r, box, match, area, lod)
            omitted_insts = max(0, len(hits) - lim)
            # At most lim instances can survive the final merge with items/zones.
            for d, i in hits[:lim]:
                rows.append((d, self.inst_cells(i) + [env.round_pos(d)]))
        if "item" in kinds or "zone" in kinds or "water" in kinds:
            cx, cy, bx = x, y, None
            if box is not None:
                b = [float(v) for v in box]
                bx = (min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3]))
                if cx is None or cy is None:
                    cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
            else:
                rr = float(r)
                bx = (float(x) - rr, float(y) - rr, float(x) + rr, float(y) + rr)
            if "item" in kinds:
                for it in self.all("SELECT t.*, p.name AS ipl_name FROM ipl_item t JOIN item_rtree rt ON rt.id = t.id "
                                   "JOIN ipl p ON p.id = t.ipl_id WHERE rt.minx <= ? AND rt.maxx >= ? AND rt.miny <= ? "
                                   "AND rt.maxy >= ?", (bx[2], bx[0], bx[3], bx[1])):
                    d = math.dist((it["x"], it["y"]), (cx, cy))
                    if box is None and d > float(r):
                        continue
                    rows.append((d, [f"item:{it['ipl_name'].lower()}#{it['sec']}#{it['idx']}", None, it["sec"],
                                     env.round_pos((it["x"], it["y"], it["z"])), None, None, None, env.round_pos(d)]))
            if "zone" in kinds:
                for zr in self.all("SELECT * FROM zone WHERE minx <= ? AND maxx >= ? AND miny <= ? AND maxy >= ? "
                                   "ORDER BY id", (bx[2], bx[0], bx[3], bx[1])):
                    zx, zy = (zr["minx"] + zr["maxx"]) / 2, (zr["miny"] + zr["maxy"]) / 2
                    zz = (zr["minz"] + zr["maxz"]) / 2
                    if match == "center":
                        if box is not None and not (bx[0] <= zx <= bx[2] and bx[1] <= zy <= bx[3]):
                            continue
                        d = math.dist((zx, zy) if z is None else (zx, zy, zz),
                                      (cx, cy) if z is None else (cx, cy, float(z)))
                    else:
                        dx = max(zr["minx"] - cx, 0.0, cx - zr["maxx"])
                        dy = max(zr["miny"] - cy, 0.0, cy - zr["maxy"])
                        dz = 0.0 if z is None else max(zr["minz"] - float(z), 0.0, float(z) - zr["maxz"])
                        d = math.sqrt(dx * dx + dy * dy + dz * dz)
                    if box is None and d > float(r):
                        continue
                    rows.append((d, [f"zone:{zr['name'].lower()}", None, zr["title"] or zr["label"] or zr["name"],
                                     env.round_pos((zx, zy, zz)), None, None, None,
                                     env.round_pos(d)]))
            if "water" in kinds:  # schema v3: water.dat polygons (the default water configuration)
                for w in self.all("SELECT q.* FROM water_quad q JOIN water_rtree rt ON rt.id = q.id WHERE q.config = 0 "
                                  "AND rt.minx <= ? AND rt.maxx >= ? AND rt.miny <= ? AND rt.maxy >= ? ORDER BY q.id",
                                  (bx[2], bx[0], bx[3], bx[1])):
                    wx, wy, wz = (w["minx"] + w["maxx"]) / 2, (w["miny"] + w["maxy"]) / 2, w["maxz"]
                    if match == "center":
                        if box is not None and not (bx[0] <= wx <= bx[2] and bx[1] <= wy <= bx[3]):
                            continue
                        d = math.dist((wx, wy) if z is None else (wx, wy, wz),
                                      (cx, cy) if z is None else (cx, cy, float(z)))
                    else:
                        dx = max(w["minx"] - cx, 0.0, cx - w["maxx"])
                        dy = max(w["miny"] - cy, 0.0, cy - w["maxy"])
                        dz = 0.0 if z is None else max(w["minz"] - float(z), 0.0, float(z) - w["maxz"])
                        d = math.sqrt(dx * dx + dy * dy + dz * dz)
                    if box is None and d > float(r):
                        continue
                    rows.append((d, [self.water_sid(w), None, "quad" if w["nverts"] == 4 else "tri",
                                     env.round_pos((wx, wy, wz)), None, None, None, env.round_pos(d)]))
        rows.sort(key=lambda t: (t[0], t[1][0]))
        return env.table(NEAR_COLS, [r for _, r in rows[:lim]], total=len(rows) + omitted_insts)

    def insts(self, model, box, center, r, area, lod, match, limit) -> list[InstRow]:
        check_choice("match", match, MATCH_MODES)
        check_choice("lod", lod, LOD_MODES)
        if box is not None or center is not None:
            cx = cy = cz = None
            if center is not None:
                c = tuple(float(v) for v in center)
                cx, cy = c[0], c[1]
                cz = c[2] if len(c) > 2 else None
            hits = [i for _, i in self.near_insts(cx, cy, cz, 50.0 if r is None else r, box, match, area, lod,
                                                 intersect_radius=center is not None)]
            if model is not None:
                hits = [i for i in hits if i["model_id"] == int(model)]
        else:
            conds, params = [], []
            if model is not None:
                conds.append("i.model_id = ?")
                params.append(int(model))
            if area is not None:
                conds.append("i.area = ?")
                params.append(int(area))
            if lod == "hd":
                conds.append("i.is_lod = 0")
            elif lod == "lod":
                conds.append("i.is_lod = 1")
            hits = self.all(self._INST_SQL + ("WHERE " + " AND ".join(conds) if conds else "") +
                            " ORDER BY lower(p.name), i.idx", params)
        if limit is not None:
            hits = hits[: int(limit)]
        return [self.inst_row(i) for i in hits]

    def match_runtime(self, model_id: int, pos, tol: float) -> list[str]:
        p = tuple(float(v) for v in pos)
        if len(p) != 3:
            raise SatkError("BAD_PARAMS", "pos must be (x, y, z)")
        t = float(tol)
        rows = self.all(self._INST_SQL + "WHERE i.model_id = ? AND i.x BETWEEN ? AND ? AND i.y BETWEEN ? AND ? "
                        "AND i.z BETWEEN ? AND ?", (int(model_id), p[0] - t, p[0] + t, p[1] - t, p[1] + t, p[2] - t,
                                                    p[2] + t))
        hits = sorted((math.dist((i["x"], i["y"], i["z"]), p), self.inst_sid(i)) for i in rows
                      if math.dist((i["x"], i["y"], i["z"]), p) <= t)
        return [sid for _, sid in hits]

    # ================================================================== typed lookups

    def model_files(self, model) -> ModelFiles:
        m = self.model_from_arg(model)
        if not m["active"]:
            return ModelFiles(int(m["id"]), m["name"], m["sec"], None, [], None)
        lk = self.model_link(m["id"])
        db = self.dff_blob_of(lk)
        c = self.col_row(lk["col_id"]) if lk else None
        col = None
        if c is not None:
            cb = self.blob(c["blob_id"])
            col = ColRef(f"col:{c['name'].lower()}", self.blob_ref(cb, f"file:{self.file_key(cb)}"), int(c["idx"]),
                         c["name"], lk["col_via"])
        chain = [self.blob_ref(b, self.txd_sid(b)) for b, _t, _h in self.txd_chain(m)]
        return ModelFiles(int(m["id"]), m["name"], m["sec"], self.blob_ref(db[1]) if db else None, chain, col)

    def texture_ref(self, sid: str) -> TexRef:
        s = as_sid(sid, ("tex", "pix"))
        return self.tex_ref(self.find_tex(s))

    def textures_of(self, sid: str) -> list[TexRef]:
        s = as_sid(sid, ("txd", "model"))
        if s.kind == "txd":
            b = self.find_blob(s)
            return [self.tex_ref(x) for x in self.tex_rows("WHERE t.blob_id = ? ORDER BY x.idx", (b["id"],))]
        m = self.find_model(s)
        if not m["active"]:
            return []
        return [self.tex_ref(x) for x in self.tex_rows(
            "JOIN model_tex mt ON mt.texture_id = x.id WHERE mt.model_id = ? ORDER BY mt.texture", (m["id"],))]

    def blob_ref_of(self, sid: str) -> BlobRef:
        s = as_sid(sid, ("dff", "txd", "file"))
        b = self.find_blob(s)
        return self.blob_ref(b, f"file:{self.file_key(b)}" if s.kind == "file" else (str(s) if s.layer else None))


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --------------------------------------------------------------------------- freshness


class Freshness:
    """``INDEX_STALE`` warnings: compares ``meta.sources_sig`` with the files (cached ``ttl`` seconds)."""

    def __init__(self, root: Path, sig_json: str | None, ttl: float = 2.0):
        self.root = root
        try:
            self.sig = [tuple(x) for x in json.loads(sig_json or "[]")]
        except ValueError:
            self.sig = []
        self.ttl = ttl
        self._at = 0.0
        self._warn: list[str] = []

    def warnings(self) -> list[str]:
        now = time.monotonic()
        if now - self._at < self.ttl:
            return list(self._warn)
        out: list[str] = []
        for rel, size, mt in self.sig:
            p = self.root.joinpath(*rel.split("/"))
            try:
                st = os.stat(p)
            except OSError:
                out.append(f"INDEX_STALE: {rel} is gone")
                continue
            if st.st_size != size or st.st_mtime_ns != mt:
                out.append(f"INDEX_STALE: {rel} changed")
            if len(out) >= 3:
                break
        if out:
            out[-1] += " (satk index build)"
        self._warn, self._at = out, now
        return list(out)
