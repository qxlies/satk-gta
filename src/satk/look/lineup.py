"""Class peers of a model for a same-scale lineup (``satk blender preview ... --lineup class``).

A lineup puts the subject next to vanilla models of its class under identical cameras and light, so size,
proportions, density and the look can be compared at a glance. Peers come from the **style peer set** of the
reference (:func:`style_peers`: the same set ``style.profile`` and ``asset.check`` use, such as ``car.sedan``
or ``prop@1-2m``):

* models whose name shares a word with the reference come first (``bin1`` -> ``CJ_BIN1``, ``CJ_WASTEBIN``),
  then the nearest in shape (height and footprint; vehicles: length) and triangles;
* without a style cache (no index for it) the IDE fallback :func:`peers` is used: vehicles of the same type
  and ``vehicles.ide`` class, peds and weapons of the same section, objects of the same section with a
  bounding radius within a factor of 1.5 (else 3).

The reference model itself is left out (a file or session subject with a like model gets it first). The
choice is deterministic (ties by model id). Stdlib only (index SQL, the style cache JSON).
"""

from __future__ import annotations

import json
import math
from typing import Any

from ..core.errors import SatkError

__all__ = ["peers", "style_peers", "class_peers", "reference", "model_row", "name_words"]

_COLS = ("m.id, m.name, m.sec, m.extra, d.tris, d.bmin_x, d.bmin_y, d.bmin_z, d.bmax_x, d.bmax_y, d.bmax_z, d.bs_r")
_FROM = ("FROM model m JOIN model_link l ON l.id = m.id JOIN dff d ON d.id = l.dff_id WHERE m.active = 1")


def _db(profile: str):
    from ..index.api import open_index

    return open_index(profile)


def _row(r: list) -> dict:
    mid, name, sec, extra, tris, x0, y0, z0, x1, y1, z1, bsr = r
    try:
        ex = json.loads(extra or "{}")
    except ValueError:
        ex = {}
    return {"id": int(mid), "name": str(name), "sec": str(sec), "type": ex.get("type"), "class": ex.get("class"),
            "tris": int(tris or 0), "dims": [float((x1 or 0) - (x0 or 0)), float((y1 or 0) - (y0 or 0)),
                                             float((z1 or 0) - (z0 or 0))], "r": float(bsr or 0.0)}


def model_row(sid: str, profile: str = "vanilla") -> dict | None:
    """Index facts of a game model SID/name: ``{id, name, sec, type, class, tris, dims, r}``."""
    s = str(sid).strip()
    if s.lower().startswith("model:"):
        s = s[6:]
    db = _db(profile)
    if s.isdigit():
        env = db.query(f"SELECT {_COLS} {_FROM} AND m.id = ?", [int(s)], limit=1)
    else:
        env = db.query(f"SELECT {_COLS} {_FROM} AND m.name = ? COLLATE NOCASE ORDER BY m.id", [s], limit=1)
    rows = env.get("rows") or []
    return _row(rows[0]) if rows else None


def reference(*, sid: str | None = None, sec: str | None = None, dims: list[float] | None = None,
              tris: int | None = None, profile: str = "vanilla") -> dict:
    """The facts peers are matched against: a game model (``sid``) or a file's section, size and triangles."""
    if sid:
        r = model_row(sid, profile)
        if r is not None:
            return r
    if not sec:
        raise SatkError("BAD_PARAMS", "a lineup needs the subject's class", hint="give --like model:<id>")
    d = [float(x) for x in (dims or [1.0, 1.0, 1.0])]
    return {"id": None, "name": None, "sec": sec, "type": "car" if sec == "cars" else None, "class": None,
            "tris": int(tris or 0), "dims": d, "r": math.sqrt(sum(x * x for x in d)) / 2}


def _rows_by_id(ids: list[int], profile: str) -> list[dict]:
    out: list[dict] = []
    db = _db(profile)
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        marks = ",".join("?" * len(chunk))
        env = db.query(f"SELECT {_COLS} {_FROM} AND m.id IN ({marks}) ORDER BY m.id", chunk, limit=len(chunk))
        out += [_row(r) for r in env.get("rows") or []]
    return out


_NOISE = frozenset({"cj", "dyn", "kb", "lae", "lan", "law", "sf", "lv", "ls", "vg", "des", "sw", "ce", "gen", "mp",
                    "nt", "tmp", "lod", "the", "and", "new", "old", "big", "sml", "small"})


def name_words(name: str | None) -> set[str]:
    """Words of a model name for peer matching (``CJ_WASTEBIN`` -> {wastebin}; digits and prefixes dropped)."""
    import re

    words = re.split(r"[^a-z]+", str(name or "").lower())
    return {w for w in words if len(w) >= 3 and w not in _NOISE}


def _name_hit(words: set[str], ref: set[str]) -> bool:
    return any(w == r or w.endswith(r) or w == r + "s" or r.endswith(w) and len(w) >= 4 for w in words for r in ref)


def _shape(ref: dict, r: dict) -> float:
    """Shape distance: height and footprint for map models, length for vehicles, height for peds."""
    def lg(a: float, b: float) -> float:
        return abs(math.log(max(a, 1e-3) / max(b, 1e-3)))

    if ref.get("sec") in ("cars", "peds", "weap"):
        return lg(_length(r), _length(ref))
    rw, rl, rh = ref["dims"]
    w, l_, h = r["dims"]
    return lg(h, rh) + 0.5 * lg(max(w, l_), max(rw, rl)) + 0.25 * lg(min(w, l_), min(rw, rl))


def style_peers(ref: dict, n: int = 2, *, profile: str = "vanilla", exclude: tuple = (),
                cls: str | None = None) -> tuple[list[str], str] | None:
    """``(SIDs, peer set)``: ``n`` peers of ``ref`` from its style peer set (``cls`` names the class when
    ``ref`` is no game model); ``None`` when there is no style cache."""
    try:
        from ..style import cache as SK
        from ..style import classes as SC
        from ..style.profile import resolve_target

        c = SK.load(profile)
        if ref.get("id") is not None:
            tg = resolve_target(f"model:{ref['id']}", c)
        elif cls:
            tg = resolve_target(cls, c)
        else:
            return None
    except Exception:  # noqa: BLE001 - no index or no style package: the IDE fallback
        return None
    key = tg["peer_set"]
    ids = sorted(int(mid) for mid, row in c.data.get("models", {}).items()
                 if key in SC.fallback_chain(SC.peer_key(row[1], row[2])))
    skip = {int(x) for x in exclude if x is not None} | ({int(ref["id"])} if ref.get("id") is not None else set())
    rows = [r for r in _rows_by_id([i for i in ids if i not in skip], profile) if r["tris"] > 0]
    if ref.get("sec") == "cars" and ref.get("type"):
        rows = [r for r in rows if r["type"] == ref["type"]] or rows
    words = name_words(ref.get("name"))

    def score(r: dict) -> tuple:
        hit = 0 if words and _name_hit(name_words(r["name"]), words) else 1
        tri = abs(math.log(max(r["tris"], 1) / max(ref["tris"], 1))) if ref.get("tris") else 0.0
        return (hit, round(_shape(ref, r) + 0.25 * tri, 4), r["id"])

    rows.sort(key=score)
    return [f"model:{r['id']}" for r in rows[: max(0, int(n))]], key


def class_peers(ref: dict, n: int = 2, *, profile: str = "vanilla", exclude: tuple = (),
                cls: str | None = None) -> tuple[list[str], str]:
    """``(SIDs, how)``: :func:`style_peers`, else the IDE fallback :func:`peers`."""
    got = style_peers(ref, n, profile=profile, exclude=exclude, cls=cls)
    if got is not None:
        return got[0], f"style peer set {got[1]}"
    if not ref.get("sec"):
        raise SatkError("BAD_PARAMS", "a lineup needs the subject's class", hint="give --like model:<id>")
    return peers(ref, n, profile=profile, exclude=exclude), f"IDE section {ref['sec']}"


def _length(r: dict) -> float:
    return max(r["dims"][0], r["dims"][1]) if r["sec"] == "cars" else r["dims"][2] if r["sec"] == "peds" else r["r"]


def _score(ref: dict, r: dict) -> tuple:
    a, b = max(_length(ref), 1e-3), max(_length(r), 1e-3)
    size = abs(math.log(b / a))
    tri = abs(math.log(max(r["tris"], 1) / max(ref["tris"], 1))) if ref["tris"] else 0.0
    same_class = 0 if (ref.get("class") and r.get("class") == ref.get("class")) else 1
    return (same_class, round(size + 0.25 * tri, 4), r["id"])


def peers(ref: dict, n: int = 2, *, profile: str = "vanilla", exclude: tuple = ()) -> list[str]:
    """``n`` model SIDs of the class of ``ref`` (:func:`reference`), best first."""
    db = _db(profile)
    sec = ref["sec"]
    params: list[Any] = [sec]
    where = "AND m.sec = ? ORDER BY m.id"
    if sec in ("objs", "tobj", "anim"):
        lo, hi = ref["r"] / 3.0, ref["r"] * 3.0
        where = ("AND m.sec IN ('objs', 'tobj', 'anim') AND d.bs_r BETWEEN ? AND ? AND d.tris > 0 "
                 "ORDER BY abs(d.bs_r - ?), m.id")
        params = [lo, hi, ref["r"]]
    env = db.query(f"SELECT {_COLS} {_FROM} {where}", params, limit=500)
    rows = [_row(r) for r in env.get("rows") or []]
    skip = {int(x) for x in exclude if x is not None} | ({ref["id"]} if ref.get("id") is not None else set())
    rows = [r for r in rows if r["id"] not in skip and r["tris"] > 0]
    if sec == "cars" and ref.get("type"):
        same = [r for r in rows if r["type"] == ref["type"]]
        rows = same or rows
    if sec in ("objs", "tobj", "anim"):
        near = [r for r in rows if 1 / 1.5 <= r["r"] / max(ref["r"], 1e-3) <= 1.5]
        rows = near if len(near) >= n else rows
    rows.sort(key=lambda r: _score(ref, r))
    return [f"model:{r['id']}" for r in rows[: max(0, int(n))]]
