"""Functional fit of a vehicle: do the parts sit where the engine moves, lights and seats them?

The engine never looks at the mesh when it turns a wheel, opens a door, steers a bike, puts a rider on it or
lights a lamp: it uses frames and dummies. A body built without them in mind looks wrong in motion. Checks:

* ``fit.wheel_arch[<wheel dummy>]`` (cars) - the side outline of the arch around each wheel is centred on the
  wheel (vanilla within 15 mm; the ratio to the wheel radius is reported);
* ``fit.dummy[<frame>]`` - lamp dummies on a lamp face of their key colour seen head-on (the glow and the
  corona are drawn there), the exhaust dummy and the petrol cap at some geometry;
* ``fit.hinge[<part>]`` - doors, bonnet and boot turn about their dummy: the hinge sits on the part's edge;
* ``fit.steer[<part>]`` (bikes) - steering parts sit on the steering axis (``forks_front`` local Z through its
  origin) as closely as on the like model;
* ``fit.rider[seat|foot|grip]`` (bikes) - the rider is a fixed animation at ``ped_frontseat``: the seat top,
  the foot rest and the grip ends stay where the like model (same animation group) has them.

Thresholds: ``data/style/form.json`` ``fit``. numpy inside the functions.
"""

from __future__ import annotations

from .form import arch_outline

__all__ = ["FIT_DEFAULTS", "fit_limits", "fit_check", "arch_outline", "rider_points", "steer_offsets"]

FIT_DEFAULTS: dict = {
    "arch_offset": 0.25,                  # |arch centre - wheel centre| / radius above this = not centred
    "lamp_mm": 100.0,                     # lamp dummy beside its lamp (seen head-on; vanilla 0, one 57 mm)
    "exhaust_mm": 250.0,                  # exhaust dummy to any geometry (vanilla up to 146 mm)
    "petrolcap_mm": 200.0,                # petrol cap dummy to the body (vanilla up to 136 mm)
    "hinge_mm": 150.0,                    # hinge axis to the nearest vertex of the part (vanilla up to 85 mm)
    "hinge_edge": 0.3,                    # hinge position along the part (0 / 1 = an edge; vanilla up to 0.28)
    "steer_mm": 40.0,                     # steering part centre farther off the axis than on the like model
    "steer_abs_mm": 400.0,                # ... or this far without a like model (vanilla up to 363 mm)
    "rider_mm": 60.0,                     # contact point off the like model's (one animation group: up to 78 mm)
}

_LAMP_KEYS = {"headlights": ("255,175,0", "0,255,200"), "headlights2": ("255,175,0", "0,255,200"),
              "taillights": ("185,255,0", "255,60,0"), "taillights2": ("185,255,0", "255,60,0")}
_HINGES = {"door_lf": "z", "door_rf": "z", "door_lr": "z", "door_rr": "z", "bonnet": "x", "boot": "x"}
_LIM: list = []


def fit_limits(override: dict | None = None) -> dict:
    if not _LIM:
        lim = dict(FIT_DEFAULTS)
        try:
            from ..core import resources

            lim.update(resources.read_json("style", "form.json").get("fit", {}))
        except Exception:  # noqa: BLE001 - the defaults equal the data file
            pass
        _LIM.append(lim)
    out = dict(_LIM[0])
    out.update(override or {})
    return out


def _r(x, nd: int = 2) -> float:
    return float(round(float(x), nd)) + 0.0


def _tris(np, parts: list[dict], pred=None):
    """Triangles ``(A, B, C, part names, material keys)`` of the non-wheel parts (``pred(part, tri mask)``)."""
    A, names, keys = [], [], []
    for p in parts:
        if p.get("role") == "wheel":
            continue
        X = p["pos"][p["tris"]]
        mk = p.get("keys") or []
        M = np.asarray(p["mat"]) if p.get("mat") is not None else np.zeros(len(X), np.int64)
        k = np.array([mk[m] if 0 <= m < len(mk) else "" for m in M], dtype=object) if len(mk) else \
            np.full(len(X), "", dtype=object)
        A.append(X)
        names += [p["name"]] * len(X)
        keys.append(k)
    if not A:
        z = np.zeros((0, 3))
        return z, z, z, np.zeros(0, dtype=object), np.zeros(0, dtype=object)
    X = np.concatenate(A)
    return X[:, 0], X[:, 1], X[:, 2], np.array(names, dtype=object), np.concatenate(keys)


def _nearest(np, q, A, B, C) -> float:
    from .form import _closest

    if not len(A):
        return float("inf")
    d, _ = _closest(np, np.repeat(np.asarray(q, dtype=np.float64)[None, :], len(A), axis=0), A, B, C)
    return float(d.min())


def _beside(np, p, A, B, C) -> float:
    """Distance from ``p`` to triangles ``A, B, C`` seen along Y (head-on: lamps face forward or back)."""
    from .form import _closest

    if not len(A):
        return float("inf")
    A2, B2, C2 = A.copy(), B.copy(), C.copy()
    for X in (A2, B2, C2):
        X[:, 1] = p[1]
    d, _ = _closest(np, np.repeat(np.asarray(p, dtype=np.float64)[None, :], len(A), axis=0), A2, B2, C2)
    return float(d.min())


def _row(check, part, value, verdict, hint) -> dict:
    return {"check": check, "part": part, "value": value, "verdict": verdict, "hint": hint}


def _lamps_exhaust(np, parts, frames, lim) -> list[dict]:
    A, B, C, names, keys = _tris(np, parts)
    rows = []
    for fr, ks in _LAMP_KEYS.items():
        if fr not in frames:
            continue
        m = np.isin(keys, ks)
        if not m.any():
            continue
        p = np.asarray(frames[fr]["pos"], dtype=np.float64)
        mm = min(_beside(np, p, A[m], B[m], C[m]), _beside(np, p * [-1, 1, 1], A[m], B[m], C[m])) * 1000.0
        if mm > lim["lamp_mm"]:
            rows.append(_row(f"fit.dummy[{fr}]", fr, _r(mm, 1), "defect",
                             f"seen head-on, the {fr} dummy is {mm:.0f} mm beside the nearest lamp face of its key "
                             "colour: the game draws the glow and the corona at the dummy; move it onto the lamp"))
    if "exhaust" in frames and len(A):
        mm = _nearest(np, np.asarray(frames["exhaust"]["pos"], dtype=np.float64), A, B, C) * 1000.0
        if mm > lim["exhaust_mm"]:
            rows.append(_row("fit.dummy[exhaust]", "exhaust", _r(mm, 1), "defect",
                             f"the exhaust dummy is {mm:.0f} mm from any geometry: smoke comes out of thin air; put it "
                             "at the end of the pipe"))
    if "petrolcap" in frames and len(A):
        p = np.asarray(frames["petrolcap"]["pos"], dtype=np.float64)
        mm = _nearest(np, p, A, B, C) * 1000.0
        if mm > lim["petrolcap_mm"]:
            rows.append(_row("fit.dummy[petrolcap]", "petrolcap", _r(mm, 1), "defect",
                             f"the petrol cap dummy is {mm:.0f} mm off the body: put it on the filler flap"))
    return rows


def _hinges(np, parts, frames, lim) -> list[dict]:
    rows = []
    by = {p["name"].lower(): p for p in parts}
    for part, axis in _HINGES.items():
        p = by.get(f"{part}_ok")
        d = frames.get(f"{part}_dummy")
        if p is None or d is None or not len(p["pos"]):
            continue
        P = p["pos"]
        o = np.asarray(d["pos"], dtype=np.float64)
        # doors swing about a vertical axis, bonnet and boot about a transverse one
        ax = np.array([0.0, 0.0, 1.0]) if axis == "z" else np.array([1.0, 0.0, 0.0])
        v = P - o
        dist = np.linalg.norm(v - np.outer(v @ ax, ax), axis=1)     # the hinge axis to the panel's vertices
        dmm = float(dist.min()) * 1000.0
        # how far in from the panel's edge: along its span across the hinge axis (a side door spans Y, a van's
        # rear door spans X, a tailgate spans Z), from the principal direction of the part seen along the axis
        w = v - np.outer(v @ ax, ax)
        wc = w - w.mean(axis=0)
        try:
            d = np.linalg.svd(wc, full_matrices=False)[2][0]
        except np.linalg.LinAlgError:
            d = np.array([0.0, 1.0, 0.0])
        pr = w @ d
        span = float(pr.max() - pr.min())
        t = (0.0 - pr.min()) / span if span > 1e-6 else 0.5
        edge = min(t, 1 - t)
        if dmm > lim["hinge_mm"] or edge > lim["hinge_edge"]:
            where = f"{edge:.0%} of the part's span in from its nearest edge" if edge > lim["hinge_edge"] else \
                f"{dmm:.0f} mm off the part"
            rows.append(_row(f"fit.hinge[{part}]", f"{part}_ok", _r(edge, 2), "defect",
                             f"{part} turns about {part}_dummy, which sits {where}: it would swing through the body; "
                             f"move the dummy to the hinge edge"))
    return rows


def _axis(frames: dict):
    import numpy as np

    f = frames.get("forks_front")
    if f is None:
        return None
    o = np.asarray(f["pos"], dtype=np.float64)
    a = np.asarray(f["at"], dtype=np.float64)
    n = np.linalg.norm(a)
    if n < 1e-9:
        return None
    return o, a / n


def steer_offsets(np, parts, frames) -> dict:
    """``{part: mm}``: area-weighted centre of ``handlebars``/``forks_front`` off the steering axis."""
    ax = _axis(frames)
    if ax is None:
        return {}
    o, a = ax
    out = {}
    for p in parts:
        n = p["name"].lower()
        if n not in ("handlebars", "forks_front") or not len(p["tris"]):
            continue
        X = p["pos"][p["tris"]]
        ar = np.linalg.norm(np.cross(X[:, 1] - X[:, 0], X[:, 2] - X[:, 0]), axis=1)
        if ar.sum() <= 0:
            continue
        c = ((X.mean(axis=1)) * ar[:, None]).sum(axis=0) / ar.sum()
        v = c - o
        out[n] = float(np.linalg.norm(v - (v @ a) * a)) * 1000.0
    return out


def _steer(np, parts, frames, lim, like_parts=None, like_frames=None, like_name="") -> list[dict]:
    mine = steer_offsets(np, parts, frames)
    ref = steer_offsets(np, like_parts, like_frames) if like_parts is not None and like_frames is not None else {}
    rows = []
    for n, off in sorted(mine.items()):
        base = ref.get(n)
        if base is not None:
            bad = off > base + lim["steer_mm"]
            why = f"{off:.0f} mm off it ({like_name}: {base:.0f} mm)"
        else:
            bad = off > lim["steer_abs_mm"]
            why = f"{off:.0f} mm off it"
        if bad:
            rows.append(_row(f"fit.steer[{n}]", n, _r(off, 1), "defect",
                             f"{n} turns about the steering axis of forks_front (its local Z), but its centre sits {why}: "
                             "it swings sideways when steering; build it around the axis"))
    return rows


def rider_points(np, parts, frames) -> dict:
    """Contact points of the rider relative to ``ped_frontseat``: ``seat`` (top surface z under the seat dummy),
    ``foot`` (highest surface in the foot zone), ``grip`` (outer end of the handlebars)."""
    if "ped_frontseat" not in frames:
        return {}
    s = np.asarray(frames["ped_frontseat"]["pos"], dtype=np.float64)
    out: dict = {}
    A, B, C, names, _k = _tris(np, parts)
    if len(A):
        from .form import _axis_hits

        z = _axis_hits(np, A, B, C, 2, s)
        z = z[(z <= s[2] + 0.3) & (z >= s[2] - 0.6)]
        if len(z):
            out["seat"] = np.array([0.0, 0.0, float(z.max()) - s[2]])
        cen = (A + B + C) / 3
        rel = cen - s
        m = (np.abs(rel[:, 0]) >= 0.08) & (np.abs(rel[:, 0]) <= 0.45) & (rel[:, 1] >= -0.1) & (rel[:, 1] <= 0.7) \
            & (rel[:, 2] >= -0.85) & (rel[:, 2] <= -0.2)
        up = np.cross(B - A, C - A)
        up = up[:, 2] / np.maximum(np.linalg.norm(up, axis=1), 1e-30)
        m &= up > 0.7
        if m.any():
            k = np.flatnonzero(m)[np.argmax(rel[m, 2])]
            out["foot"] = np.array([abs(rel[k, 0]), rel[k, 1], rel[k, 2]])
    hb = next((p for p in parts if p["name"].lower() == "handlebars" and len(p["pos"])), None)
    if hb is not None:
        P = hb["pos"]
        k = int(np.argmax(np.abs(P[:, 0])))
        out["grip"] = np.array([abs(P[k, 0]), P[k, 1] - s[1], P[k, 2] - s[2]])
    return out


def _surface_below(np, parts, x: float, y: float, top: float, bottom: float) -> float | None:
    """Highest surface z under the point ``(x, y)`` between ``top`` and ``bottom`` (either side: |x|)."""
    from .form import _axis_hits

    A, B, C, _n, _k = _tris(np, parts)
    best = None
    for sx in (x, -x):
        z = _axis_hits(np, A, B, C, 2, (sx, y, 0.0))
        z = z[(z <= top) & (z >= bottom)]
        if len(z):
            best = float(z.max()) if best is None else max(best, float(z.max()))
    return best


def _rider(np, parts, frames, like_parts, like_frames, like_name, lim) -> list[dict]:
    mine = rider_points(np, parts, frames)
    ref = rider_points(np, like_parts, like_frames)
    rows = []
    s = np.asarray(frames.get("ped_frontseat", {}).get("pos", (0.0, 0.0, 0.0)), dtype=np.float64)
    if "foot" in ref and "ped_frontseat" in frames:
        fx, fy, fz = ref["foot"]
        z = _surface_below(np, parts, float(s[0] + fx), float(s[1] + fy), float(s[2] + fz + 0.25),
                           float(s[2] + fz - 0.25))
        mine["foot"] = None if z is None else np.array([fx, fy, z - s[2]])
    for k in ("seat", "foot", "grip"):
        if k not in ref:
            continue
        if mine.get(k) is None:
            rows.append(_row(f"fit.rider[{k}]", k, None, "defect",
                             f"no {k} surface where {like_name} has one: the rider animation (fixed at ped_frontseat) "
                             "needs it"))
            continue
        d = float(np.linalg.norm(mine[k] - ref[k]))
        if d * 1000.0 > lim["rider_mm"]:
            delta = mine[k] - ref[k]
            rows.append(_row(f"fit.rider[{k}]", k, _r(d * 1000.0, 1), "defect",
                             f"the rider's {k} contact is {d * 1000:.0f} mm off {like_name} (dx dy dz "
                             f"{_r(delta[0])} {_r(delta[1])} {_r(delta[2])} m relative to ped_frontseat): the animation "
                             "is fixed, so the rider floats, sinks or misses the bars; match the like model"))
    return rows


def fit_check(parts: list[dict], frames: dict, wheels: list[dict], *, vtype: str, like_parts: list[dict] | None = None,
              like_frames: dict | None = None, like_name: str = "the like model",
              limits_override: dict | None = None) -> list[dict]:
    """Fit rows ``[{check, part, value, verdict ("defect" | "info"), hint}]`` of one vehicle (model space parts,
    :func:`satk.style.sections.frame_table` frames, :func:`satk.style.sections.wheels_of` wheels)."""
    import numpy as np

    lim = fit_limits(limits_override)
    rows: list[dict] = []
    A, B, C, _n, _k = _tris(np, parts)
    if vtype not in ("bike", "bmx"):
        for w in wheels:
            if w.get("bike"):
                continue
            o = arch_outline(np, A, B, C, w)
            if o is None or o.get("open"):
                continue
            R = float(w["radius"])
            rows.append(_row(f"fit.wheel_arch[{w['name']}]", w["name"], _r(o["ratio"], 2), "info",
                             f"arch outline {o['ratio']:.2f} x the wheel radius, centre {o['offset_m'] * 1000:+.0f} mm "
                             "along Y (vanilla 1.07-1.55, within 15 mm)"))
            if abs(o["offset_m"]) > lim["arch_offset"] * R:
                rows.append(_row(f"fit.wheel_arch[{w['name']}]", w["name"], _r(o["offset_m"] * 1000.0, 1), "defect",
                                 f"the arch is centred {o['offset_m'] * 1000:+.0f} mm along Y from the wheel: the tyre "
                                 "sits off-centre in its arch; move the wheel dummy or reshape the arch"))
        rows += _hinges(np, parts, frames, lim)
    rows += _lamps_exhaust(np, parts, frames, lim)
    if vtype in ("bike", "bmx"):
        rows += _steer(np, parts, frames, lim, like_parts, like_frames, like_name)
        if like_parts is not None and like_frames is not None:
            rows += _rider(np, parts, frames, like_parts, like_frames, like_name, lim)
    return rows
