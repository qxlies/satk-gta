"""Left/right symmetry of a model: mirrored parts and dummies that do not mirror each other.

* ``sym.part[<left part>]`` - a ``*_l*`` atomic (``door_lf_ok``) against its ``*_r*`` twin mirrored in X: the 90th
  percentile of the nearest-vertex distances both ways;
* ``sym.dummy[<left frame>]`` - a ``*_l*`` frame against its ``*_r*`` twin mirrored in X;
* ``sym.body[<part>]`` (information) - share of the body's vertices whose mirror image is farther than
  ``body_mm`` from its surface (vanilla bodies carry a few asymmetric details: steering column, exhaust, filler).

numpy inside the functions; limits in ``data/style/form.json`` (``symmetry``).
"""

from __future__ import annotations

import re

__all__ = ["SYM_DEFAULTS", "symmetry_check", "twin"]

SYM_DEFAULTS: dict = {
    "part_mm": 25.0,       # p90 distance between a part and its mirrored twin
    "dummy_mm": 25.0,      # a frame and its mirrored twin
    "body_mm": 20.0,       # body vertex to the mirrored body surface
    "max_verts": 4000,     # parts with more vertices are sampled
}
_SIDE = re.compile(r"_([lr])(f|b|r|m)?(?=_|$|\d)")
_LIM: list = []


def _limits() -> dict:
    if not _LIM:
        lim = dict(SYM_DEFAULTS)
        try:
            from ..core import resources

            lim.update(resources.read_json("style", "form.json").get("symmetry", {}))
        except Exception:  # noqa: BLE001
            pass
        _LIM.append(lim)
    return dict(_LIM[0])


def twin(name: str) -> str | None:
    """The mirrored name of a left name (``door_lf_ok`` -> ``door_rf_ok``), or None for other names."""
    n = name.lower()
    m = _SIDE.search(n)
    if not m or m.group(1) != "l":
        return None
    return n[:m.start(1)] + "r" + n[m.end(1):]


def _nn(np, A, B, chunk: int = 2_000_000):
    """Distance from each point of ``A`` to the nearest point of ``B``."""
    out = np.empty(len(A))
    step = max(1, chunk // max(len(B), 1))
    for s in range(0, len(A), step):
        d = np.linalg.norm(A[s:s + step, None, :] - B[None, :, :], axis=2)
        out[s:s + step] = d.min(axis=1)
    return out


def _sample(np, P, n: int):
    if len(P) <= n:
        return P
    idx = np.linspace(0, len(P) - 1, n).astype(np.int64)
    return P[idx]


def symmetry_check(parts: list[dict], frames: dict, *, body: str = "chassis", limits_override: dict | None = None
                   ) -> list[dict]:
    """``[{check, part, value, verdict ("defect" | "info"), hint}]`` (model-space parts, frame table)."""
    import numpy as np

    lim = _limits()
    lim.update(limits_override or {})
    rows: list[dict] = []
    by = {p["name"].lower(): p for p in parts if p.get("role") != "wheel"}
    for n, p in sorted(by.items()):
        tw = twin(n)
        if tw is None or tw not in by:
            continue
        L = _sample(np, np.unique(np.round(p["pos"], 4), axis=0), lim["max_verts"]) * [-1.0, 1.0, 1.0]
        R = _sample(np, np.unique(np.round(by[tw]["pos"], 4), axis=0), lim["max_verts"])
        if not len(L) or not len(R):
            continue
        d = np.concatenate([_nn(np, L, R), _nn(np, R, L)])
        p90 = float(np.percentile(d, 90)) * 1000.0
        if p90 > lim["part_mm"]:
            rows.append({"check": f"sym.part[{n}]", "part": f"{n}/{tw}", "value": round(p90, 1), "verdict": "defect",
                         "hint": f"{n} mirrored in X misses {tw} by {p90:.0f} mm (90 % of the vertices): the two sides "
                                 "differ; model one side and mirror it"})
    for n, f in sorted(frames.items()):
        tw = twin(n)
        if tw is None or tw not in frames:
            continue
        a = np.asarray(f["pos"], dtype=np.float64) * [-1.0, 1.0, 1.0]
        b = np.asarray(frames[tw]["pos"], dtype=np.float64)
        mm = float(np.linalg.norm(a - b)) * 1000.0
        if mm > lim["dummy_mm"]:
            rows.append({"check": f"sym.dummy[{n}]", "part": f"{n}/{tw}", "value": round(mm, 1), "verdict": "defect",
                         "hint": f"{n} mirrored in X is {mm:.0f} mm from {tw}: wheels, doors or lamps sit unevenly; "
                                 "mirror the frame positions"})
    bp = by.get(body.lower())
    if bp is not None and len(bp["tris"]):
        from .form import _Grid, _closest

        X = bp["pos"][bp["tris"]]
        A, B, C = X[:, 0], X[:, 1], X[:, 2]
        tol = lim["body_mm"] / 1000.0
        V = _sample(np, np.unique(np.round(bp["pos"], 4), axis=0), lim["max_verts"]) * [-1.0, 1.0, 1.0]
        tlo = np.minimum(np.minimum(A, B), C)
        thi = np.maximum(np.maximum(A, B), C)
        g = _Grid(np, tlo, thi, tol)
        qi, ti = g.pairs(V)
        near = np.zeros(len(V), dtype=bool)
        if len(qi):
            d, _ = _closest(np, V[qi], A[ti], B[ti], C[ti])
            ok = d <= tol
            near[qi[ok]] = True
        share = 1.0 - float(near.mean()) if len(V) else 0.0
        rows.append({"check": f"sym.body[{bp['name']}]", "part": bp["name"], "value": round(share, 3), "verdict": "info",
                     "hint": f"{share:.0%} of the {bp['name']} vertices have no mirror image within "
                             f"{lim['body_mm']:.0f} mm (one-sided details; vanilla cars about 5-25 %)"})
    return rows
