"""Round-trip measurements of the satk writers on a game installation (``satk rw roundtrip``).

DFF levels (each compared with the original RW bytes):

* ``tree`` - lossless chunk tree written back with recomputed sizes;
* ``typed`` - every struct the patches touch decoded and re-encoded from its fields
  (:func:`satk.rw.dff.typed_roundtrip`);
* ``restamp`` - SA -> Vice City -> SA (librw struct conversions both ways).

COL levels (per record): ``typed`` (:func:`satk.rw.col.decode_model` -> ``encode_model``), ``json``
(``col export`` -> ``col write``, including the ``raw`` garbage bytes) and ``canonical`` (JSON without
``raw``: what an agent-authored file looks like).

``engine="rwfury"`` measures the vendored rwfury 0.6.1 instead (its model-rebuild writers).
"""

from __future__ import annotations

import io
import json
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable, Iterator

from ..core.paths import open_ro
from ..formats.img import ImgArchive
from ..formats.rw import rw_payload_size
from . import codecs as C
from . import col as COL
from .chunk import GAME_VERSIONS, parse
from .dff import DffDoc, PatchError, first_difference, typed_roundtrip

__all__ = ["iter_game", "dff_check", "col_check", "run", "measure", "measure_rwfury", "rwfury_dff", "rwfury_col"]


def iter_game(root: Path, kind: str) -> Iterator[tuple[str, bytes]]:
    """``(label, bytes)`` of every ``kind`` (dff/col) file: IMG entries of ``models/*.img``, then loose files
    under ``models/`` (sorted, labels relative to ``root``)."""
    models = root / "models"
    imgs = sorted((p for p in models.glob("*") if p.suffix.lower() == ".img" and p.is_file()), key=lambda p: p.name.lower())
    for img in imgs:
        with ImgArchive.open(img) as a:
            for e in a.entries:
                if e.ext == kind:
                    yield f"models/{img.name}/{e.name}", a.read(e)
    loose = sorted((p for p in models.rglob("*") if p.suffix.lower() == "." + kind and p.is_file()),
                   key=lambda p: str(p).lower())
    for p in loose:
        with open_ro(p) as f:
            yield p.relative_to(root).as_posix(), f.read()


def dff_check(data: bytes, *, restamp: bool = True) -> dict:
    """Round-trip one DFF; ``{"tree": bool, "typed": bool, "restamp": bool | None, "classes": [...]}``."""
    n = rw_payload_size(data) or 0
    src = bytes(data[:n])
    out: dict = {"tree": False, "typed": False, "restamp": None, "classes": [], "opaque": []}
    try:
        doc = DffDoc.parse(data)
    except PatchError as e:
        out["classes"].append(f"parse:{str(e)[:40]}")
        return out
    out["opaque"] = sorted({nd.name for nd, _p in doc.stream.walk() if nd.opaque})
    tree = doc.to_bytes()
    out["tree"] = tree == src
    if not out["tree"]:
        out["classes"].append("tree:" + (first_difference(src, tree) or "?"))
    try:
        rebuilt, problems = typed_roundtrip(DffDoc.parse(data))
        typed = rebuilt.to_bytes()
        out["typed"] = typed == src and not problems
        if not out["typed"]:
            out["classes"] += sorted(set(problems)) or ["typed:" + (first_difference(src, typed) or "?")]
    except PatchError as e:
        out["classes"].append(f"typed-error:{str(e)[:40]}")
    if restamp:
        try:
            d2 = DffDoc.parse(data)
            d2.restamp(GAME_VERSIONS["vc"])
            d3 = DffDoc.parse(d2.to_bytes())
            d3.restamp(GAME_VERSIONS["sa"])
            back = d3.to_bytes()
            out["restamp"] = back == src
            if not out["restamp"]:
                out["classes"].append("restamp:" + _restamp_class(src, back))
        except (PatchError, ValueError) as e:
            out["restamp"] = False
            out["classes"].append(f"restamp-error:{str(e)[:40]}")
    return out


def _restamp_class(src: bytes, back: bytes) -> str:
    """Why SA -> VC -> SA did not return the same bytes."""
    if {nd.libid for nd, _p in parse(src).walk()} != {0x1803FFFF}:
        return "source-not-3.6.0.3"
    where = first_difference(src, back) or "?"
    if "SkinPLG" not in where:
        return where
    a, b = DffDoc.parse(src), DffDoc.parse(back)
    kinds = set()
    for ga, gb in zip(a.geometries(), b.geometries()):
        sa, sb = _skin(ga), _skin(gb)
        if sa is None or sb is None or sa == sb:
            continue
        da = C.decode_skin(sa, ga.data().num_verts)
        db = C.decode_skin(sb, gb.data().num_verts)
        if set(da.used) == set(db.used) and da.used != db.used:
            kinds.add("skin-used-bone-order")
        elif set(da.used) != set(db.used):
            kinds.add("skin-used-bone-set")
        else:
            kinds.add("skin-other")
    return "+".join(sorted(kinds)) or where


def _skin(g) -> bytes | None:
    ext = g.ext()
    for k in (ext.kids or ()) if ext is not None else ():
        if k.type == 0x116:
            return k.data
    return None


def col_check(data: bytes) -> dict:
    """Round-trip the records of one COL blob (file, IMG entry or DFF CollisionModel payload)."""
    out = {"models": 0, "typed": 0, "json": 0, "canonical": 0, "flags_rule": 0, "groups_rule": 0, "rule_models": 0,
           "name_garbage": 0, "pad_garbage": 0, "file": False, "classes": Counter()}
    try:
        recs, tail = COL.split_models(data)
    except COL.ColError as e:
        out["classes"][f"split:{str(e)[:40]}"] += 1
        return out
    rebuilt = []
    for rec in recs:
        out["models"] += 1
        try:
            m = COL.decode_model(rec)
        except COL.ColError as e:
            out["classes"][f"decode:{str(e)[:50]}"] += 1
            rebuilt.append(rec)
            continue
        enc = COL.encode_model(m)
        rebuilt.append(enc)
        if enc == rec:
            out["typed"] += 1
        else:
            out["classes"]["typed:" + _col_diff(rec, enc)] += 1
        js = json.loads(json.dumps(COL.model_to_json(m)))
        try:
            jm = COL.encode_model(COL.model_from_json(js))
        except COL.ColError as e:
            out["classes"][f"json-error:{str(e)[:50]}"] += 1
            jm = b""
        if jm == rec:
            out["json"] += 1
        elif jm:
            out["classes"]["json:" + _col_diff(rec, jm)] += 1
        canon = json.loads(json.dumps(COL.model_to_json(m, raw=False)))
        try:
            cm = COL.encode_model(COL.model_from_json(canon))
        except COL.ColError:
            cm = b""
        if cm == COL.encode_model(_without_garbage(m)):
            out["canonical"] += 1
        else:
            out["classes"]["canonical:" + (_col_diff(rec, cm) if cm else "error")] += 1
        if m.version >= 2:
            out["flags_rule"] += COL.canonical_flags(m) == m.flags
            out["groups_rule"] += bool(m.groups) == (len(m.faces) >= COL.FG_MIN_FACES)
            out["rule_models"] += 1
        if m.name_raw is not None:
            out["name_garbage"] += 1
        if any(v.strip(b"\0") for v in m.pad.values()):
            out["pad_garbage"] += 1
    out["file"] = b"".join(rebuilt) + tail == bytes(data)
    return out


def _without_garbage(m: COL.ColModel) -> COL.ColModel:
    """The record with zeroed name garbage and padding (what a JSON without ``raw`` reproduces)."""
    import copy

    z = copy.copy(m)
    z.name_raw = None
    z.pad = {k: b"\0" * len(v) for k, v in m.pad.items()}
    return z


def _col_diff(a: bytes, b: bytes) -> str:
    if len(a) != len(b):
        return "size"
    i = next(i for i in range(len(a)) if a[i] != b[i])
    if i < 8:
        return "header"
    if i < 30:
        return "name"
    if i < 32:
        return "model_id"
    return "body"


def _embedded_cols(data: bytes) -> list[bytes]:
    from ..formats.dff import find_embedded_col

    n = rw_payload_size(data) or 0
    try:
        r = find_embedded_col(data[:n])
    except ValueError:
        return []
    return [bytes(data[r[0]:r[0] + r[1]])] if r else []


Source = Callable[[str], Iterable[tuple[str, bytes]]]


def run(root: Path, kind: str = "all", *, restamp: bool = True, examples: int = 5) -> dict:
    """Measure the satk writers on every DFF/COL of the game installation ``root``."""
    return measure(lambda k: iter_game(root, k), kind, restamp=restamp, examples=examples)


def measure(source: Source, kind: str = "all", *, restamp: bool = True, examples: int = 5) -> dict:
    """Measure the satk writers; ``source(ext)`` yields ``(label, bytes)`` of the dff/col files."""
    res: dict = {}
    if kind in ("dff", "all"):
        st: Counter = Counter()
        classes: Counter = Counter()
        opaque: Counter = Counter()
        ex: dict[str, list[str]] = {}
        for label, data in source("dff"):
            r = dff_check(data, restamp=restamp)
            st["files"] += 1
            st["tree_exact"] += r["tree"]
            st["typed_exact"] += r["typed"]
            if restamp:
                st["restamp_exact"] += bool(r["restamp"])
            for c in r["classes"]:
                classes[c] += 1
                if len(ex.setdefault(c, [])) < examples:
                    ex[c].append(label)
            for o in r["opaque"]:
                opaque[o] += 1
        res["dff"] = {**dict(st), "classes": dict(classes.most_common()), "opaque": dict(opaque.most_common()),
                      "examples": ex}
    if kind in ("col", "all"):
        st = Counter()
        classes = Counter()
        ex = {}

        def add(label: str, data: bytes, prefix: str = "") -> None:
            r = col_check(data)
            st[prefix + "files"] += 1
            st[prefix + "files_exact"] += r["file"]
            for k in ("models", "typed", "json", "canonical", "flags_rule", "groups_rule", "rule_models",
                      "name_garbage", "pad_garbage"):
                st[prefix + k] += r[k]
            for c, n in r["classes"].items():
                classes[prefix + c] += n
                if len(ex.setdefault(prefix + c, [])) < examples:
                    ex[prefix + c].append(label)

        for label, data in source("col"):
            add(label, data)
        for label, data in source("dff"):
            for blob in _embedded_cols(data):
                add(label, blob, "embedded_")
        res["col"] = {**dict(st), "classes": dict(classes.most_common()), "examples": ex}
    return res


# ============================================================================ rwfury baseline


def rwfury_dff(data: bytes, rwf) -> str | None:
    """``None`` when rwfury 0.6.1 rewrites the DFF bit for bit, else the first differing chunk path."""
    n = rw_payload_size(data) or 0
    src = bytes(data[:n])
    try:
        d = rwf.Dff.from_bytes(src)
        b = io.BytesIO()
        d._write(rwf.RwBinaryWriter(b))
        out = b.getvalue()
    except Exception as e:  # noqa: BLE001 - third-party reader, any failure is a measurement result
        return f"error:{type(e).__name__}"
    return first_difference(src, out)


def rwfury_col(data: bytes, rwf) -> tuple[int, int]:
    """``(records, records rewritten bit for bit)`` by rwfury 0.6.1."""
    from rwfury.col import _write_col1, _write_col23  # vendored; private writers of one record

    try:
        recs, _tail = COL.split_models(data)
    except COL.ColError:
        return 0, 0
    same = 0
    for rec in recs:
        try:
            m = rwf.Col.from_bytes(rec).models[0]
            w = _write_col1(m) if m.version == 1 else _write_col23(m)
        except Exception:  # noqa: BLE001
            continue
        same += w == rec
    return len(recs), same


def measure_rwfury(source: Source, kind: str = "all") -> dict:
    """The rwfury 0.6.1 baseline: its own model-rebuild writers on the same files."""
    from .vendor import load

    rwf = load()
    if rwf is None:
        raise RuntimeError("vendor/rwfury is not available")
    res: dict = {}
    if kind in ("dff", "all"):
        st: Counter = Counter()
        classes: Counter = Counter()
        for _label, data in source("dff"):
            r = rwfury_dff(data, rwf)
            st["files"] += 1
            st["exact"] += r is None
            if r is not None:
                classes[r] += 1
        res["dff"] = {**dict(st), "classes": dict(classes.most_common(10))}
    if kind in ("col", "all"):
        st = Counter()
        for _label, data in source("col"):
            n, same = rwfury_col(data, rwf)
            st["files"] += 1
            st["models"] += n
            st["models_exact"] += same
        res["col"] = dict(st)
    return res
