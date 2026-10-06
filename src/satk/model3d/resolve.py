"""Which files make up a model: SID -> :class:`ModelSource` through ``satk.index.api`` (SPEC §4.3.4).

Accepted ids: ``model:411``, ``model:infernus``, ``411``, ``infernus``, ``dff:infernus`` (the model whose
name is that DFF, else the bare DFF), ``inst:lae2_stream0#4`` (the placed model), or the path of a ``.dff``
file of one's own (:func:`resolve_file`: the ``.txd`` of the same name next to it; a vehicle also gets the
game's ``vehicle.txd`` and default paint).

The index API is used first (``IndexDB.model_files`` ...). An ``IndexDB`` of stage Q1 raises
``IndexNotImplemented`` there; then a small read-only SQL fallback over the frozen schema v1 (``model``,
``model_link``, ``dff``, ``txd``, ``col``, ``blob``, ``source``, ``layer``) gives the same answer through the
public ``IndexDB.query`` (checked equal on 2 114 vanilla models). :func:`list_models` is plain SQL.
Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import SatkError
from ..core.ids import Sid
from ..index.api import BlobRef, ColRef, IndexDB, ModelFiles, open_index

__all__ = ["ModelSource", "resolve", "resolve_file", "is_dff_path", "model_files", "list_models", "read_blob",
           "blob_ref", "open_db"]

_QMAX = 500


@dataclass
class ModelSource:
    """A model to export or render (``dff`` ``None`` = the game has no DFF for it)."""

    sid: str
    model_id: int | None
    name: str
    sec: str | None
    dff: BlobRef | None
    txd_chain: list[BlobRef] = field(default_factory=list)
    col: ColRef | None = None
    profile: str = "vanilla"
    notes: list[str] = field(default_factory=list)


def open_db(profile: str = "vanilla") -> IndexDB:
    return open_index(profile)


# ----------------------------------------------------------------------------- SQL fallback (index stage Q1)
def _rows(db: IndexDB, sql: str, params: list = ()) -> list[dict]:
    env = db.query(sql, list(params), limit=_QMAX)
    cols = env["cols"]
    return [dict(zip(cols, r)) for r in env["rows"]]


_BLOB_COLS = ("b.id AS bid, b.name AS bname, b.stem AS bstem, b.ext AS bext, b.abs_off AS off, b.size AS size, "
              "b.active AS active, s.relpath AS relpath, s.kind AS skind, la.root AS root")
_BLOB_JOIN = "JOIN source s ON s.id = b.source_id JOIN layer la ON la.id = s.layer_id"


def _ref(r: dict, sid: str | None = None) -> BlobRef:
    path = Path(r["root"]) / Path(*str(r["relpath"]).split("/"))
    if sid is None:
        if r["active"] and r["bext"] in ("dff", "txd"):
            sid = f"{r['bext']}:{str(r['bstem']).lower()}"
        else:
            rel = str(r["relpath"]).lower()
            sid = f"file:{rel}/{str(r['bname']).lower()}" if r["skind"] == "img" else f"file:{rel}"
    return BlobRef(sid, path, int(r["off"]), int(r["size"]), str(r["bname"]))


def _sql_model(db: IndexDB, model: int | str) -> dict:
    if isinstance(model, int) or str(model).isdigit():
        rows = _rows(db, "SELECT id, name, sec, txd FROM model WHERE active = 1 AND id = ?", [int(model)])
    else:
        rows = _rows(db, "SELECT id, name, sec, txd FROM model WHERE active = 1 AND name = ? COLLATE NOCASE "
                         "ORDER BY id LIMIT 1", [str(model)])
    if not rows:
        like = _rows(db, "SELECT id, name FROM model WHERE active = 1 AND name LIKE ? ORDER BY id LIMIT 5",
                     [f"%{str(model)[:6]}%"]) if not str(model).isdigit() else []
        raise SatkError("NOT_FOUND", f"no model {model!r} in this profile",
                        hint=f"satk asset find {model} --kind model",
                        did_you_mean=[f"model:{r['id']}" for r in like])
    return rows[0]


def _sql_model_files(db: IndexDB, model: int | str) -> ModelFiles:
    m = _sql_model(db, model)
    mid = int(m["id"])
    lk = _rows(db, "SELECT dff_id, txd_id, col_id, col_via FROM model_link WHERE id = ?", [mid])
    lk = lk[0] if lk else {"dff_id": None, "txd_id": None, "col_id": None, "col_via": None}
    dff = None
    if lk["dff_id"] is not None:
        r = _rows(db, f"SELECT {_BLOB_COLS} FROM dff d JOIN blob b ON b.id = d.blob_id {_BLOB_JOIN} WHERE d.id = ?",
                  [lk["dff_id"]])
        dff = _ref(r[0]) if r else None
    chain: list[BlobRef] = []
    seen: set[int] = set()
    cur = None
    if lk["txd_id"] is not None:
        r = _rows(db, f"SELECT t.parent AS parent, {_BLOB_COLS} FROM txd t JOIN blob b ON b.id = t.blob_id "
                      f"{_BLOB_JOIN} WHERE t.id = ?", [lk["txd_id"]])
        cur = r[0] if r else None
    while cur is not None and cur["bid"] not in seen and len(chain) < 16:
        seen.add(cur["bid"])
        chain.append(_ref(cur))
        parent = cur.get("parent")
        cur = None
        if parent:
            r = _rows(db, f"SELECT t.parent AS parent, {_BLOB_COLS} FROM txd t JOIN blob b ON b.id = t.blob_id "
                          f"{_BLOB_JOIN} WHERE t.name = ? COLLATE NOCASE AND b.active = 1 "
                          "ORDER BY (b.ns = 'main') DESC, b.id LIMIT 1", [parent])
            cur = r[0] if r else None
    if m["sec"] == "cars" and not any(b.name.lower() == "vehicle.txd" for b in chain):
        r = _rows(db, f"SELECT {_BLOB_COLS} FROM txd t JOIN blob b ON b.id = t.blob_id {_BLOB_JOIN} "
                      "WHERE t.name = 'vehicle' AND b.active = 1 ORDER BY b.id LIMIT 1")
        if r and r[0]["bid"] not in seen:
            chain.append(_ref(r[0]))
    col = None
    if lk["col_id"] is not None:
        r = _rows(db, f"SELECT c.idx AS cidx, c.name AS cname, {_BLOB_COLS} FROM col c JOIN blob b ON b.id = c.blob_id "
                      f"{_BLOB_JOIN} WHERE c.id = ?", [lk["col_id"]])
        if r:
            br = _ref(r[0], sid=None)
            if not br.sid.startswith("file:"):
                rel = str(r[0]["relpath"]).lower()
                br = BlobRef(f"file:{rel}/{br.name.lower()}" if r[0]["skind"] == "img" else f"file:{rel}",
                             br.path, br.offset, br.size, br.name)
            col = ColRef(f"col:{str(r[0]['cname']).lower()}", br, int(r[0]["cidx"]), str(r[0]["cname"]),
                         str(lk["col_via"] or "colfile"))
    return ModelFiles(mid, str(m["name"]), str(m["sec"]), dff, chain, col)


def model_files(db: IndexDB, model: int | str) -> ModelFiles:
    """``db.model_files`` or, at index stage Q1, the SQL fallback."""
    try:
        return db.model_files(model)
    except NotImplementedError:
        return _sql_model_files(db, model)


def blob_ref(db: IndexDB, sid: str) -> BlobRef:
    """``db.blob_ref`` (``dff:``/``txd:``) or the SQL fallback for active blobs."""
    try:
        return db.blob_ref(sid)
    except NotImplementedError:
        s = Sid.parse(sid)
        if s.kind not in ("dff", "txd") or s.layer:
            raise SatkError("NOT_READY", f"{sid}: needs the full index API (stage Q1 only serves dff:/txd:)") from None
        r = _rows(db, f"SELECT {_BLOB_COLS} FROM blob b {_BLOB_JOIN} WHERE b.stem = ? COLLATE NOCASE AND b.ext = ? "
                      "AND b.active = 1 ORDER BY (b.ns = 'main') DESC, b.id LIMIT 1", [s.key, s.kind])
        if not r:
            raise SatkError("NOT_FOUND", f"no {sid}", hint=f"satk asset find {s.key} --kind {s.kind}") from None
        return _ref(r[0])


def read_blob(db: IndexDB, ref: BlobRef) -> bytes:
    return db.read_blob(ref)


def list_models(db: IndexDB) -> list[tuple[int, str, str, str]]:
    """``[(id, name, sec, txd)]`` of every active model with a DFF, sorted by TXD then id
    (models sharing a TXD land in the same batch chunk)."""
    out: list[tuple[int, str, str, str]] = []
    off = 0
    while True:
        rows = _rows(db, "SELECT l.id AS id, m.name AS name, m.sec AS sec, COALESCE(m.txd, '') AS txd "
                         "FROM model_link l JOIN model m ON m.id = l.id AND m.active = 1 "
                         "WHERE l.dff_id IS NOT NULL ORDER BY lower(COALESCE(m.txd, '')), l.id LIMIT ? OFFSET ?",
                     [_QMAX, off])
        out.extend((int(r["id"]), str(r["name"]), str(r["sec"]), str(r["txd"])) for r in rows)
        if len(rows) < _QMAX:
            break
        off += _QMAX
    return out


# ----------------------------------------------------------------------------- SID -> source
def _model_by_dff(db: IndexDB, stem: str) -> int | None:
    r = _rows(db, "SELECT m.id AS id FROM model m WHERE m.active = 1 AND m.name = ? COLLATE NOCASE "
                  "ORDER BY m.id LIMIT 1", [stem])
    return int(r[0]["id"]) if r else None


def _model_of_inst(db: IndexDB, s: Sid) -> int:
    ipl, _, idx = s.key.partition("#")
    if not idx.isdigit():
        raise SatkError("BAD_ID", f"bad inst SID {s}", hint="inst:<ipl>#<index>, e.g. inst:lae2_stream0#4")
    r = _rows(db, "SELECT i.model_id AS mid FROM inst i JOIN ipl p ON p.id = i.ipl_id "
                  "WHERE p.name = ? COLLATE NOCASE AND i.idx = ? LIMIT 1", [ipl, int(idx)])
    if not r:
        raise SatkError("NOT_FOUND", f"no placement {s}", hint=f"satk asset find {ipl} --kind ipl")
    return int(r[0]["mid"])


def _parse(ident: str) -> Sid:
    ident = str(ident).strip()
    if not ident:
        raise SatkError("BAD_PARAMS", "empty id", hint="satk model image model:411")
    if ":" not in ident:
        return Sid("model", ident.lower())
    s = Sid.parse(ident)
    if s.kind not in ("model", "dff", "inst"):
        raise SatkError("BAD_ID", f"{ident}: expected a model:, dff: or inst: SID",
                        hint=f"satk texture image {s}" if s.kind in ("txd", "tex", "pix") else
                        "satk asset find <name> --kind model")
    return s


def is_dff_path(ident) -> bool:
    """True for the path of a ``.dff`` file (instead of a SID or model name)."""
    s = str(ident).strip().strip('"')
    return s.lower().endswith(".dff") and ("/" in s or "\\" in s or Path(s).is_file())


def _loose(p: Path) -> BlobRef:
    return BlobRef(f"file:{p.name.lower()}", p, 0, p.stat().st_size, p.name)


def resolve_file(path, profile: str = "vanilla", db: IndexDB | None = None) -> ModelSource:
    """A DFF file of one's own: the TXD of the same name next to it; vehicles (chassis/wheel dummies) also get
    the game's ``vehicle.txd`` (from the index of ``profile``) and are previewed as ``cars``."""
    from ..core.paths import open_ro, jpath
    from ..formats.rw import FormatError
    from .mesh import build_scene, is_vehicle

    p = Path(str(path).strip().strip('"')).expanduser()
    if not p.is_file():
        raise SatkError("NOT_FOUND", f"no DFF file at {jpath(p)}", hint="a .dff file, or model:<id>")
    notes: list[str] = []
    want = p.stem.lower() + ".txd"
    txd = next((q for q in p.parent.iterdir() if q.name.lower() == want and q.is_file()), None)
    chain = [_loose(txd)] if txd is not None else []
    if txd is None:
        notes.append(f"NO_TXD: no {want} next to {p.name}: rendered without its own textures")
    with open_ro(p) as f:
        data = f.read()
    try:
        scene = build_scene(data, name=p.stem.lower())
    except FormatError as e:
        raise SatkError("UNSUPPORTED", f"{p.name}: cannot decode the DFF: {e}") from None
    sec = "cars" if is_vehicle(None, scene.frames) else None
    if sec == "cars":
        try:
            db = db or open_db(profile)
            chain.append(blob_ref(db, "txd:vehicle"))
        except SatkError as e:
            notes.append(f"NO_VEHICLE_TXD: {e.code}: vehicle.txd textures render missing")
    return ModelSource(f"file:{p.name.lower()}", None, p.stem.lower(), sec, _loose(p), chain, None, profile, notes)


def resolve(ident: str, profile: str = "vanilla", db: IndexDB | None = None) -> ModelSource:
    """Model files for any accepted id (see module docstring)."""
    if is_dff_path(ident):
        return resolve_file(ident, profile, db)
    db = db or open_db(profile)
    s = _parse(ident)
    notes: list[str] = []
    if s.kind == "inst":
        mid: int | str = _model_of_inst(db, s)
        notes.append(f"{s} places model:{mid}")
    elif s.kind == "dff":
        found = _model_by_dff(db, s.key)
        if found is None:
            ref = blob_ref(db, f"dff:{s.key}")
            return ModelSource(str(s), None, s.key, None, ref, [], None, profile,
                               ["no model uses this DFF by name: rendered without textures"])
        mid = found
    else:
        mid = int(s.key) if s.key.isdigit() else s.key
    if s.layer:
        raise SatkError("UNSUPPORTED", f"{s}: inactive (@layer) definitions cannot be exported yet",
                        hint=f"satk asset get {s}")
    mf = model_files(db, mid)
    return ModelSource(f"model:{mf.model_id}", mf.model_id, mf.name, mf.sec, mf.dff, list(mf.txd_chain), mf.col,
                       profile, notes)
