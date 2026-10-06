"""Derive the texture -> surface table and the lighting fit from a game's own collisions (``satk.colgen.derive``).

For every model of an index profile that has both a DFF and a collision (``COLFILE``/IMG ``.col`` or a
vehicle's embedded COL), each collision face is matched to the nearest render triangle (model space);
the triple *(TXD of the model, texture of that triangle, surface of the face)* is one vote. Boxes and
spheres vote with the texture that covers most of the render area inside them. The prelit brightness of
the matched triangle against the face's lighting byte (day = low nibble, night = high nibble) gives a
linear fit.

The table keeps, per texture, the surface with the most votes, its share and the vote count; per TXD only
the textures whose majority differs from the texture's own. Only numbers and names are stored, never
geometry or pixels.

A hold-out split (models whose id ``% holdout == 3``) measures how well the table predicts surfaces of
models it has not seen: the table is built from the other models and evaluated face by face on the
held-out ones (the keyword fallbacks and ``DEFAULT`` cover unseen textures exactly like ``col gen``).
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable

from ._np import np

from ..core.errors import SatkError
from ..formats.dff import find_embedded_col
from ..formats.rw import FormatError
from ..rw import col as COL
from .geom import render_mesh
from .nearest import nearest_tri
from .surface import SurfaceTable

__all__ = ["DeriveResult", "ModelVotes", "derive", "model_votes", "build_table", "score", "MAX_MATCH"]

#: A collision face further than this from every render triangle votes for nothing (metres).
MAX_MATCH = 1.0
_PAGE = 500


@dataclass
class ModelVotes:
    """Votes of one model: ``faces``/``prims`` = ``[(texture, surface)]``; ``light`` = ``[(day, night, byte)]``."""

    model_id: int
    name: str
    txd: str = ""
    faces: list = field(default_factory=list)
    prims: list = field(default_factory=list)
    light: list = field(default_factory=list)
    unmatched: int = 0


@dataclass
class DeriveResult:
    textures: dict          # texture -> [surface, share %, votes]
    txd: dict               # txd -> {texture -> [surface, share %, votes]} (only where it differs)
    lighting: dict
    report: dict
    rows: list              # per-surface table rows for the envelope


def col_vertices(m: COL.ColModel, shadow: bool = False) -> np.ndarray:
    vs = m.shadow_vertices if shadow else m.vertices
    if not vs:
        return np.zeros((0, 3))
    v = np.asarray(vs, dtype=np.float64)
    return v if m.version == 1 else v / 128.0


def col_faces(m: COL.ColModel, shadow: bool = False) -> tuple[np.ndarray, list[int], list[int]]:
    """``(indices (n, 3), materials, lighting bytes)`` of the faces (or shadow faces)."""
    fs = m.shadow_faces if shadow else m.faces
    if not fs:
        return np.zeros((0, 3), dtype=np.int64), [], []
    if m.version == 1:
        return np.asarray([f[:3] for f in fs], dtype=np.int64), [f[3][0] for f in fs], [f[3][3] for f in fs]
    return np.asarray([f[:3] for f in fs], dtype=np.int64), [f[3] for f in fs], [f[4] for f in fs]


def model_votes(dff: bytes, cm: COL.ColModel, model_id: int, name: str, sec: str | None, txd: str = "") -> ModelVotes:
    """Votes of one model (see the module doc)."""
    out = ModelVotes(model_id, name, txd)
    rm = render_mesh(dff, name, sec)
    P, T = rm.P, rm.T
    V = col_vertices(cm)
    F, mats, lights = col_faces(cm)
    if len(F) and len(V):
        ok = (F < len(V)).all(axis=1)
        cen = V[F[ok]].mean(axis=1)
        idx, dist = nearest_tri(cen, P, T)
        for k, i in enumerate(np.nonzero(ok)[0]):
            if dist[k] > MAX_MATCH:
                out.unmatched += 1
                continue
            j = int(idx[k])
            out.faces.append((rm.texture_of(j), int(mats[i])))
            if cm.version >= 2 and rm.day[j] >= 0:
                out.light.append((float(rm.day[j]), float(rm.night[j]), int(lights[i])))
    if cm.boxes or cm.spheres:
        cen = P[T].mean(axis=1)
        area = 0.5 * np.linalg.norm(np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]), axis=1)
        prims = [("box", p) for p in cm.boxes] + [("sphere", p) for p in cm.spheres]
        for kind, prim in prims:
            if kind == "box":
                lo, hi, s = prim
                inside = ((cen >= np.asarray(lo) - 0.1) & (cen <= np.asarray(hi) + 0.1)).all(axis=1)
                mid = (np.asarray(lo) + np.asarray(hi)) / 2
            else:
                c, r, s = prim
                inside = ((cen - np.asarray(c)) ** 2).sum(axis=1) <= (r + 0.1) ** 2
                mid = np.asarray(c)
            if inside.any():
                w = np.bincount(rm.tex[inside] + 1, weights=area[inside])
                tj = int(w.argmax()) - 1
                t = rm.textures[tj] if tj >= 0 else None
            else:
                j, d = nearest_tri(mid[None, :], P, T)
                if d[0] > MAX_MATCH:
                    out.unmatched += 1
                    continue
                t = rm.texture_of(int(j[0]))
            out.prims.append((t, int(s[0])))
    return out


def _iter_models(db, limit: int | None) -> list[tuple]:
    """``(model id, name, sec, txd, dff ref, col ref)`` of models with a DFF and a collision, by COL blob."""
    from ..model3d.resolve import model_files

    ids: list[int] = []
    off = 0
    while True:
        env = db.query("SELECT l.id FROM model_link l JOIN model m ON m.id = l.id AND m.active = 1 "
                       "WHERE l.dff_id IS NOT NULL AND l.col_id IS NOT NULL ORDER BY l.col_id, l.id LIMIT ? OFFSET ?",
                       [_PAGE, off], limit=_PAGE)
        ids += [int(r[0]) for r in env["rows"]]
        if len(env["rows"]) < _PAGE:
            break
        off += _PAGE
    if limit:
        ids = ids[:limit]
    out = []
    for mid in ids:
        mf = model_files(db, mid)
        if mf.dff is not None and mf.col is not None:
            txd = mf.txd_chain[0].name.rsplit(".", 1)[0].lower() if mf.txd_chain else ""
            out.append((mid, mf.name, mf.sec, txd, mf.dff, mf.col))
    return out


def _fit(pairs: list[tuple[float, float]]) -> list[float]:
    """Least-squares ``byte = a * brightness + b`` (rounded to 4 digits)."""
    if len(pairs) < 10:
        return [0.0, 15.0]
    x = np.asarray([p[0] for p in pairs])
    y = np.asarray([p[1] for p in pairs])
    a, b = np.polyfit(x, y, 1)
    return [round(float(a), 4), round(float(b), 4)]


def _majority(c: Counter) -> list:
    n = sum(c.values())
    s, k = min(c.items(), key=lambda kv: (-kv[1], kv[0]))
    return [s, round(100.0 * k / n), n]


def build_table(votes: dict[tuple[str, str], Counter]) -> tuple[dict, dict]:
    """``{(txd, texture): Counter(surface)}`` -> ``(textures, txd overrides)``."""
    by_tex: dict[str, Counter] = defaultdict(Counter)
    for (_txd, tex), c in votes.items():
        by_tex[tex].update(c)
    textures = {t: _majority(by_tex[t]) for t in sorted(by_tex) if t}
    over: dict[str, dict] = {}
    for (txd, tex), c in sorted(votes.items()):
        if not tex or not txd:
            continue
        row = _majority(c)
        if row[0] != textures[tex][0]:
            over.setdefault(txd, {})[tex] = row
    return textures, over


def _read_col(cref, cache: dict) -> COL.ColModel:
    from ..index.api import read_blob_bytes

    key = (str(cref.blob.path), cref.blob.offset)
    recs = cache.get(key)
    if recs is None:
        blob = read_blob_bytes(cref.blob)
        if cref.via == "embedded":
            r = find_embedded_col(blob)
            if r is None:
                raise COL.ColError("embedded collision not found")
            blob = blob[r[0]:r[0] + r[1]]
        recs = COL.split_models(blob)[0]
        cache.clear()
        cache[key] = recs
    return COL.decode_model(recs[cref.idx])


def collect(models: list[tuple], progress: Callable[[int, int], None] | None = None) -> tuple[list[ModelVotes], Counter]:
    """Votes of every model (in order); ``Counter`` of error types for models that could not be read."""
    from ..index.api import read_blob_bytes

    out: list[ModelVotes] = []
    errors: Counter = Counter()
    cache: dict = {}
    for i, (mid, name, sec, txd, dref, cref) in enumerate(models):
        if progress is not None and i % 250 == 0:
            progress(i, len(models))
        try:
            cm = _read_col(cref, cache)
            out.append(model_votes(read_blob_bytes(dref), cm, mid, name, sec, txd))
        except (FormatError, COL.ColError, IndexError, ValueError) as e:
            errors[type(e).__name__] += 1
    return out, errors


def derive(profile: str = "vanilla", *, holdout: int = 10, limit: int | None = None,
           progress: Callable[[int, int], None] | None = None) -> DeriveResult:
    """Build the table from ``profile`` (see the module doc); ``holdout`` 0 = no evaluation."""
    from ..index.api import open_index

    db = open_index(profile)
    models = _iter_models(db, limit)
    if not models:
        raise SatkError("NOT_FOUND", f"no model of profile {profile!r} has both a DFF and a collision",
                        hint=f"satk index build --profile {profile}")
    t0 = time.monotonic()
    votes, errors = collect(models, progress)
    full: dict[tuple[str, str], Counter] = defaultdict(Counter)
    train: dict[tuple[str, str], Counter] = defaultdict(Counter)
    held: list[tuple] = []
    held_prims: list[tuple] = []
    light_day: list[tuple[float, float]] = []
    light_night: list[tuple[float, float]] = []
    n_faces = n_prims = unmatched = 0
    for mv in votes:
        unmatched += mv.unmatched
        is_held = bool(holdout) and mv.model_id % holdout == 3
        for t, s in mv.faces + mv.prims:
            full[(mv.txd, t or "")][s] += 1
            if not is_held:
                train[(mv.txd, t or "")][s] += 1
        n_faces += len(mv.faces)
        n_prims += len(mv.prims)
        if is_held:
            held += [(mv.txd, t, s) for t, s in mv.faces]
            held_prims += [(mv.txd, t, s) for t, s in mv.prims]
        for day, night, b in mv.light:
            light_day.append((day, b & 15))
            if night >= 0:
                light_night.append((night, b >> 4))
    textures, over = build_table(full)
    report: dict = {"profile": profile, "models": len(votes), "faces": n_faces, "prims": n_prims,
                    "unmatched": unmatched, "textures": len(textures), "txd_overrides": sum(len(v) for v in over.values()),
                    "seconds": round(time.monotonic() - t0, 1)}
    if errors:
        report["errors"] = dict(errors)
    st = SurfaceTable.load()
    if holdout and held:
        st_train = st.with_textures(*build_table(train))
        report["holdout"] = {"every": holdout, "faces": len(held), **score(st_train, held),
                             "prims": len(held_prims), "prims_acc": score(st_train, held_prims)["acc"],
                             "texture_only_acc": score(st_train, [(None, t, s) for _x, t, s in held])["acc"]}
        report["in_sample"] = score(st.with_textures(textures, over), held)
    lighting = {"day": _fit(light_day), "night": _fit(light_night), "pairs": [len(light_day), len(light_night)]}
    if light_day:
        a, b = lighting["day"]
        pred = np.clip(np.rint(a * np.asarray([p[0] for p in light_day]) + b), 0, 15)
        lighting["day_mae"] = round(float(np.abs(pred - np.asarray([p[1] for p in light_day])).mean()), 2)
    by_surface: Counter = Counter()
    for row in textures.values():
        by_surface[row[0]] += row[2]
    rows = [[sid, st.name(sid), n] for sid, n in by_surface.most_common()]
    return DeriveResult(textures, over, lighting, report, rows)


def score(st: SurfaceTable, obs: list[tuple]) -> dict:
    """Accuracy of ``st`` on ``(txd, texture, actual surface)`` observations, split by how it decided."""
    hit: Counter = Counter()
    tot: Counter = Counter()
    for txd, tex, actual in obs:
        sid, how = st.lookup(tex, txd)
        k = how.split(":", 1)[0]
        tot[k] += 1
        if sid == actual:
            hit[k] += 1
    n = sum(tot.values()) or 1
    return {"acc": round(100.0 * sum(hit.values()) / n, 1),
            "by": {k: [tot[k], round(100.0 * hit[k] / tot[k], 1)] for k in sorted(tot)}}


def table_text(res: DeriveResult) -> str:
    """The ``data/colgen/tex_surface.json`` text of a result: one texture per line, sorted (diff friendly)."""
    import json

    def row(k: str, v) -> str:
        return f"  {json.dumps(k)}: {json.dumps(v, separators=(',', ':'))}"

    rep = {k: res.report[k] for k in ("profile", "models", "faces", "prims", "textures", "txd_overrides")
           if k in res.report}
    if "holdout" in res.report:
        h = res.report["holdout"]
        rep["holdout_acc"] = h["acc"]
        rep["holdout_texture_only_acc"] = h["texture_only_acc"]
        rep["holdout_prims_acc"] = h["prims_acc"]
    lines = ["{", ' "format": "satk-colgen-tex-surface",', ' "version": 1,',
             ' "about": "texture -> [surface id, share %, votes] from collision faces matched to render triangles '
             '(satk col derive); txd holds (TXD, texture) pairs whose majority differs.",',
             f' "derived": {json.dumps(rep, separators=(",", ":"))},',
             f' "lighting": {json.dumps({k: res.lighting[k] for k in ("day", "night") if k in res.lighting})},',
             ' "textures": {']
    lines.append(",\n".join(row(k, v) for k, v in sorted(res.textures.items())))
    lines.append(" },")
    lines.append(' "txd": {')
    txd_rows = []
    for txd in sorted(res.txd):
        inner = ",".join(f"{json.dumps(t)}:{json.dumps(v, separators=(',', ':'))}" for t, v in sorted(res.txd[txd].items()))
        txd_rows.append(f"  {json.dumps(txd)}: {{{inner}}}")
    lines.append(",\n".join(txd_rows))
    lines.append(" }")
    lines.append("}")
    return "\n".join(lines) + "\n"
