"""What ``asset.check`` / ``asset.anatomy`` look at: a model with its TXD, collision and IDE facts.

A *subject* comes from the index (a SID such as ``model:426`` or ``premier``) or from files (a ``.dff``
path; a mod folder gives one subject per ``.dff``). Next to a DFF the loader picks up, read-only:

* ``<stem>.txd`` (texture names, sizes, formats, levels);
* the collision: embedded in the DFF (vehicles) or a ``.col`` in the folder holding a model named ``<stem>``;
* the IDE line of ``<stem>`` from any ``.ide`` in the folder (section, vehicle type, wheel_scale, draw, flags);
* ``asset.json`` (project manifest) in the folder or up to two parents: its ``tier`` and ``like``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..core import paths
from ..core.errors import SatkError

__all__ = ["Subject", "load", "load_sid", "load_file", "col_summary"]

_MAX_FILES = 200


@dataclass
class Subject:
    label: str                         # SID or path (forward slashes)
    name: str                          # model name (DFF stem)
    dff: bytes
    origin: str                        # "index" | "file"
    sec: str | None = None             # IDE section when known
    ide: dict = field(default_factory=dict)        # type, wheel_scale, draw, flags, id, txd
    tex: dict = field(default_factory=dict)        # name -> {"w", "h", "fmt", "levels", "alpha"}
    col: bytes | None = None           # one COL record (embedded or from a .col)
    col_via: str | None = None         # "embedded" | "file" | "index"
    manifest: dict = field(default_factory=dict)   # tier / like from asset.json
    model_id: int | None = None
    path: Path | None = None
    txd_file: Path | None = None       # the TXD read for ``tex`` (DFF files only)
    notes: list = field(default_factory=list)
    _scene: object = None

    @property
    def scene(self):
        if self._scene is None:
            from ..formats.rw import FormatError
            from ..model3d.mesh import build_scene

            try:
                self._scene = build_scene(self.dff, name=self.name, sec=self.sec)
            except FormatError as e:
                raise SatkError("UNSUPPORTED", f"{self.label}: cannot decode the DFF: {e}",
                                hint=f"satk formats dump {self.label}") from None
        return self._scene

    def tex_sizes(self) -> dict:
        return {k: (v["w"], v["h"]) for k, v in self.tex.items()}


def _txd_rows(buf: bytes) -> dict:
    from ..formats.txd import parse_txd

    t = parse_txd(buf)
    return {x.name.lower(): {"w": x.w, "h": x.h, "fmt": x.d3dfmt, "levels": x.levels, "alpha": bool(x.alpha)}
            for x in t.textures if x.name}


def _col_record(buf: bytes, name: str | None) -> bytes | None:
    from ..rw.col import split_models

    try:
        recs, _tail = split_models(buf)
    except Exception:  # noqa: BLE001 - not a COL file
        return None
    if name is None:
        return recs[0] if recs else None
    for r in recs:
        if r[8:30].split(b"\0", 1)[0].decode("latin-1").lower() == name.lower():
            return r
    return None


def col_summary(rec: bytes | None) -> dict | None:
    """Counts and face light of one COL record: ``{spheres, boxes, faces, shadow_faces, light0_share,
    light_dominant, version}``; ``None`` when there is none or it does not decode."""
    if not rec:
        return None
    from collections import Counter

    from ..rw.col import decode_model

    try:
        m = decode_model(rec)
    except Exception:  # noqa: BLE001
        return None
    out = {"version": m.version, "spheres": len(m.spheres), "boxes": len(m.boxes), "faces": len(m.faces),
           "shadow_faces": len(m.shadow_faces)}
    if m.version >= 2 and m.faces:
        lights = Counter(int(f[4]) for f in m.faces if len(f) >= 5)
        if lights:
            out["light_dominant"] = lights.most_common(1)[0][0]
            out["light0_share"] = round(lights.get(0, 0) / sum(lights.values()), 3)
    return out


def _manifest(folder: Path) -> dict:
    cur = folder
    for _ in range(3):
        p = cur / "asset.json"
        if p.is_file():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            return {k: d[k] for k in ("tier", "like", "kind") if isinstance(d.get(k), str)}
        if cur.parent == cur:
            break
        cur = cur.parent
    return {}


def _ide_line(folder: Path, name: str) -> tuple[dict, str | None]:
    from ..formats.ide import parse_ide

    for p in sorted(folder.glob("*.ide"))[:20]:
        try:
            text = p.read_text(encoding="latin-1")
        except OSError:
            continue
        defs, _txdp, _fx = parse_ide(text)
        for d in defs:
            if d.name.lower() == name.lower():
                ex = d.extra or {}
                return ({"id": d.id, "txd": d.txd, "draw": d.draw, "flags": d.flags, "type": ex.get("type"),
                         "wheel_scale": ex.get("wheel_scale_f")}, d.sec)
    return {}, None


def load_file(path: str | os.PathLike) -> Subject:
    p = Path(os.path.abspath(os.fspath(path)))
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"no file {paths.jpath(p)}", hint="give a .dff path, a mod folder or a SID")
    with paths.open_ro(p) as f:
        dff = f.read()
    name = p.stem
    s = Subject(paths.jpath(p), name, dff, "file", path=p)
    folder = p.parent
    txd = folder / f"{name}.txd"
    ide, sec = _ide_line(folder, name)
    if ide.get("txd") and not txd.is_file():
        txd = folder / f"{ide['txd']}.txd"
    if txd.is_file():
        try:
            with paths.open_ro(txd) as f:
                s.tex = _txd_rows(f.read())
            s.txd_file = txd
        except Exception as e:  # noqa: BLE001
            s.notes.append(f"{txd.name}: not read ({type(e).__name__})")
    s.ide, s.sec = ide, sec
    from ..formats.dff import find_embedded_col

    emb = find_embedded_col(dff)
    if emb is not None:
        rec = _col_record(dff[emb[0]:emb[0] + emb[1]], None)
        if rec:
            s.col, s.col_via = rec, "embedded"
    if s.col is None:
        for c in sorted(folder.glob("*.col"))[:20]:
            with paths.open_ro(c) as f:
                rec = _col_record(f.read(), name)
            if rec:
                s.col, s.col_via = rec, "file"
                break
    s.manifest = _manifest(folder)
    return s


def load_sid(ident: str, profile: str = "vanilla") -> Subject:
    from ..model3d.resolve import open_db, resolve

    db = open_db(profile)
    src = resolve(ident, profile, db)
    if src.dff is None:
        raise SatkError("NOT_FOUND", f"{src.sid} ({src.name}) has no DFF", hint=f"satk asset get {src.sid}")
    s = Subject(src.sid, src.name, db.read_blob(src.dff), "index", sec=src.sec, model_id=src.model_id)
    if src.model_id is not None:
        r = db.query("SELECT txd, draw, flags, extra FROM model WHERE active = 1 AND id = ?", [src.model_id], limit=1)
        if r["rows"]:
            txd, draw, flags, extra = r["rows"][0]
            try:
                ex = json.loads(extra) if extra else {}
            except ValueError:
                ex = {}
            s.ide = {"id": src.model_id, "txd": txd, "draw": draw, "flags": flags, "type": ex.get("type"),
                     "wheel_scale": ex.get("wheel_scale_f")}
        r = db.query("SELECT lower(mt.texture), x.w, x.h, x.d3dfmt, x.levels, x.alpha, mt.via FROM model_tex mt "
                     "JOIN texture x ON x.id = mt.texture_id WHERE mt.model_id = ?", [src.model_id], limit=500)
        s.tex = {n: {"w": w, "h": h, "fmt": f, "levels": lv, "alpha": bool(a), "via": via}
                 for n, w, h, f, lv, a, via in r["rows"]}
    from ..formats.dff import find_embedded_col

    emb = find_embedded_col(s.dff)
    if emb is not None:
        s.col = _col_record(s.dff[emb[0]:emb[0] + emb[1]], None)
        s.col_via = "embedded" if s.col else None
    if s.col is None and src.col is not None:
        try:
            buf = db.read_blob(src.col.blob)
            from ..rw.col import split_models

            recs, _ = split_models(buf)
            s.col = recs[src.col.idx] if 0 <= src.col.idx < len(recs) else _col_record(buf, src.col.name)
            s.col_via = src.col.via
        except Exception as e:  # noqa: BLE001
            s.notes.append(f"collision not read ({type(e).__name__})")
    return s


def load(target: str, profile: str = "vanilla") -> list[Subject]:
    """Subjects of a SID, a ``.dff`` path or a folder (every ``.dff`` in it, sorted, at most 200)."""
    t = str(target).strip()
    if not t:
        raise SatkError("BAD_PARAMS", "empty target", hint="satk asset check model:426")
    p = Path(t)
    if p.is_dir():
        files = sorted(x for x in p.rglob("*.dff") if x.is_file())
        if not files:
            raise SatkError("NOT_FOUND", f"no .dff in {paths.jpath(p)}")
        return [load_file(x) for x in files[:_MAX_FILES]]
    if p.suffix.lower() == ".dff" or p.is_file():
        return [load_file(p)]
    return [load_sid(t, profile)]
