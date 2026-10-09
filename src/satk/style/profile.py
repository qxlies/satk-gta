"""Profiles (contract K2): the vanilla reference numbers of each peer set, never targets.

* :func:`profile` - ``profile(cls_or_sid, tier) -> {metric: {p10, p50, p90, n, peer_set, lo, hi, status}}``;
* :func:`band` - ``band(metric, cls, tier) -> (lo, hi)`` or ``None``;
* :func:`describe` - the full answer of ``satk style profile`` (peer set, exemplars, anchors, rows);
* :func:`judge` - the verdict of one value against its band and fence (``ok``/``edge``/``low``/``high``).

``lo``/``hi`` are p10..p90 of the peer set; a tier changes only texture density (``data/style/tiers.json``
``sa_plus`` rules). No tier has a triangle band: counts are reference. :func:`judge` and :func:`fence` stay for
tools that describe a spread (``style build --validate``); ``asset.check`` never judges a reference number.

Example::

    from satk.style.api import profile, band
    p = profile("car.sedan", "vanilla")
    p["veh.hd_tris"]          # {'p10': 2010, 'p50': 2166, 'p90': 2401, 'n': 24, 'peer_set': 'car.sedan', ...}
    band("shade.normal_bend", "car")   # (7.28, 15.45): reference, not a target
"""

from __future__ import annotations

import fnmatch
import functools

from ..core import resources
from ..core.errors import SatkError
from . import cache as K
from . import classes as C
from .registry import lookup

__all__ = ["TIERS", "PED_HEIGHT_M", "tiers", "profile", "band", "describe", "judge", "resolve_target",
           "metric_list", "tier_band", "fence"]

TIERS = ("vanilla", "sa_plus")
#: Intrinsic scale anchor of map models: the height of a standing ped (metres).
PED_HEIGHT_M = 1.84


@functools.lru_cache(maxsize=1)
def tiers() -> dict:
    return resources.read_json("style", "tiers.json")


def _check_tier(tier: str | None) -> str:
    t = (tier or tiers()["default_tier"]).strip().lower().replace("-", "_").replace("+", "_plus")
    if t == "saplus":
        t = "sa_plus"
    if t not in TIERS:
        raise SatkError("BAD_PARAMS", f"unknown tier {tier!r}", hint="--tier vanilla | sa_plus")
    return t


def metric_list(cls: str, which: str = "profile") -> list[str]:
    """The ``profile``/``check``/``anchors`` metric list of a class's family (``tiers.json``)."""
    fam = tiers()["metrics"].get(C.family(cls), {})
    return list(fam.get(which, []))


def fence(stats: list, parent: list | None = None) -> tuple[float, float]:
    """``(lo, hi)`` fence of ``[p10, p50, p90, n]``.

    lo = p10 - max(k*(p50 - p10), r*|p50|), hi = p90 + max(k*(p90 - p50), r*|p50|) with ``k`` and
    ``r`` = ``min_rel`` of ``tiers.json`` (``r`` keeps a tight peer set, such as vans sharing one wheel, from
    flagging every small difference). A peer set smaller than ``borrow_below`` models also takes at least
    ``borrow`` x the margins of its ``parent`` stats (car.van borrows from car): few models give unstable
    percentiles.
    """
    f = tiers()["fence"]
    k, r = float(f["k"]), float(f.get("min_rel", 0.0))
    p10, p50, p90 = float(stats[0]), float(stats[1]), float(stats[2])
    floor = r * abs(p50)
    mlo, mhi = max(k * (p50 - p10), floor), max(k * (p90 - p50), floor)
    if parent is not None and len(stats) > 3 and int(stats[3]) < int(f.get("borrow_below", 0)):
        b = float(f.get("borrow", 0.0))
        q10, q50, q90 = float(parent[0]), float(parent[1]), float(parent[2])
        mlo, mhi = max(mlo, b * k * (q50 - q10)), max(mhi, b * k * (q90 - q50))
    return p10 - mlo, p90 + mhi


def _rule(metric: str, cls: str) -> dict | None:
    rules = tiers()["sa_plus"]
    base = C.split_peer(cls)[0]
    for key in [*C.fallback_chain(base), C.family(base)]:
        r = rules.get(key, {}).get(metric)
        if r is not None:
            return r
    return None


def tier_band(metric: str, stats: list, cls: str, tier: str) -> dict:
    """``{"lo", "hi", "status", "cap"?}`` of one metric for a tier (``stats`` = ``[p10, p50, p90, n]``)."""
    p10, p50, p90 = (float(x) for x in stats[:3])
    out = {"lo": p10, "hi": p90, "status": "measured"}
    if tier != "sa_plus":
        return out
    r = _rule(metric, cls)
    if not r or r.get("same"):
        return out
    if "band" in r:
        out["lo"], out["hi"] = float(r["band"][0]), float(r["band"][1])
    elif "mul" in r:
        out["lo"], out["hi"] = float(r["mul"][0]) * p50, float(r["mul"][1]) * p50
    elif "hi_mul" in r:
        out["lo"], out["hi"] = p10, float(r["hi_mul"]) * p90
    if "cap" in r:
        out["cap"] = float(r["cap"])
    out["status"] = "proposal"
    return out


def judge(value: float, stats: list, tband: dict, parent: list | None = None) -> str:
    """``ok`` inside the tier band, ``edge`` between the band and the fence, else ``low``/``high``.

    For the vanilla band the fence is :func:`fence`; a proposal band (sa_plus) is widened by the same
    relative margins of the vanilla peer set (so an sa_plus asset is not flagged for being 1 % over).
    """
    lo, hi = tband["lo"], tband["hi"]
    if lo <= value <= hi:
        return "ok"
    flo, fhi = fence(stats, parent)
    p10, p50, p90 = (float(x) for x in stats[:3])
    if tband.get("status") == "proposal":
        flo = lo - (p10 - flo) * (lo / p10 if p10 else 1.0)
        fhi = hi + (fhi - p90) * (hi / p90 if p90 else 1.0)
    if "cap" in tband:
        fhi = min(fhi, tband["cap"])
    if value < lo:
        return "edge" if value >= flo else "low"
    return "edge" if value <= fhi else "high"


# ----------------------------------------------------------------------------- targets
def _as_class(ident: str) -> str | None:
    try:
        return C.resolve(ident)
    except SatkError:
        return None


def resolve_target(ident: str, cache: K.StyleCache) -> dict:
    """``{"class", "peer_set", "chain", "like"?, "model"?}`` for a class name or a model (SID/name/id)."""
    s = (ident or "").strip()
    if not s:
        raise SatkError("BAD_PARAMS", "empty class or model", hint="satk style profile car.sedan")
    like = None
    cls = None if s.lower().startswith(("model:", "dff:")) or s.isdigit() else _as_class(s)
    if cls is None:
        key = s.split(":", 1)[1] if ":" in s else s
        mid = int(key) if key.isdigit() else cache.model_id(key)
        row = cache.model(mid) if mid is not None else None
        if row is None:
            raise SatkError("NOT_FOUND", f"{s!r} is neither a style class nor a model with a DFF in profile "
                                         f"{cache.profile!r}", hint="satk style profile --list",
                            did_you_mean=_suggest(s))
        like = {"sid": f"model:{mid}", "name": row[0]}
        cls = C.peer_key(row[1], row[2])
    chain = [k for k in C.fallback_chain(cls)]
    peers = cache.peers
    used = None
    notes = []
    min_n = int(C.taxonomy()["min_peers"])
    for k in chain:
        p = peers.get(k)
        if p and p["n"] >= min_n:
            used = k
            break
        notes.append(f"{k}: {p['n'] if p else 0} models < {min_n}, using the parent")
    if used is None:
        used = next((k for k in chain if k in peers), None)
    if used is None:
        raise SatkError("NOT_FOUND", f"no vanilla models in peer set {cls!r}", hint="satk style profile --list")
    out = {"class": cls, "peer_set": used, "chain": chain}
    if notes:
        out["fallback"] = notes
    if like:
        out["like"] = like
    return out


def _suggest(s: str) -> list[str]:
    import difflib

    names = list(C.taxonomy()["classes"]) + list(C.taxonomy()["aliases"])
    return difflib.get_close_matches(s.lower(), names, n=3, cutoff=0.6)


def _select(all_metrics: dict, wanted: list[str] | None, cls: str) -> list[str]:
    if not wanted:
        names = metric_list(cls, "profile")
        return [m for m in names if m in all_metrics]
    out: list[str] = []
    for w in wanted:
        for part in str(w).split(","):
            part = part.strip()
            if not part:
                continue
            if part in all_metrics:
                if part not in out:
                    out.append(part)
            elif "*" in part or "?" in part:
                pat = part.replace("[", "[[]")                  # brackets are literal in metric names
                out += [m for m in sorted(all_metrics) if fnmatch.fnmatchcase(m, pat) and m not in out]
            elif lookup(part) is None:
                raise SatkError("BAD_PARAMS", f"unknown metric {part!r}", hint="satk style profile --metrics '*'")
    return out


# ----------------------------------------------------------------------------- K2
def profile(cls_or_sid: str, tier: str | None = None, *, metrics: list[str] | None = None,
            profile_name: str = "vanilla") -> dict:
    """``{metric: {p10, p50, p90, n, peer_set, lo, hi, status}}`` of a class or a model's peer set."""
    t = _check_tier(tier)
    c = K.load(profile_name)
    tg = resolve_target(cls_or_sid, c)
    stats = c.peers[tg["peer_set"]]["metrics"]
    out = {}
    for m in _select(stats, metrics, tg["peer_set"]):
        s = stats[m]
        b = tier_band(m, s, tg["peer_set"], t)
        out[m] = {"p10": s[0], "p50": s[1], "p90": s[2], "n": s[3], "peer_set": tg["peer_set"],
                  "lo": _num(b["lo"]), "hi": _num(b["hi"]), "status": b["status"]}
        if "cap" in b:
            out[m]["cap"] = _num(b["cap"])
    return out


def band(metric: str, cls: str, tier: str | None = None, *, profile_name: str = "vanilla") -> tuple | None:
    """``(lo, hi)`` of ``metric`` for a class (or model) and tier; ``None`` when the peer set lacks it."""
    p = profile(cls, tier, metrics=[metric], profile_name=profile_name) if lookup(metric) else {}
    r = p.get(metric)
    return (r["lo"], r["hi"]) if r else None


def _num(x: float):
    if isinstance(x, float) and x == int(x):
        return int(x)
    return round(x, 4) if isinstance(x, float) else x


def describe(ident: str, tier: str | None = None, *, metrics: list[str] | None = None,
             profile_name: str = "vanilla") -> dict:
    """Everything ``satk style profile`` prints: target, peer set, rows, exemplars, anchors, notes."""
    t = _check_tier(tier)
    c = K.load(profile_name)
    tg = resolve_target(ident, c)
    key = tg["peer_set"]
    peer = c.peers[key]
    stats = peer["metrics"]
    rows = []
    for m in _select(stats, metrics, key):
        s = stats[m]
        b = tier_band(m, s, key, t)
        row = [m, s[0], s[1], s[2], s[3], _num(b["lo"]), _num(b["hi"])]
        rows.append(row)
    out = {"target": tg, "tier": t, "tier_status": tiers()["tiers"][t]["status"], "n": peer["n"],
           "percentiles": "p10/p50/p90 over the models of the peer set (numpy linear); lo..hi = p10..p90 (sa_plus: "
                          "wider texel density); vanilla reference, never targets",
           "cols": ["metric", "p10", "p50", "p90", "n", "lo", "hi"], "rows": rows,
           "exemplars": [f"model:{i} {n}" for i, n in peer.get("exemplars", [])]}
    fam = C.family(key)
    if fam == "vehicle":
        anc = {m: stats[m][:3] for m in metric_list(key, "anchors") if m in stats}
        if anc:
            out["anchors"] = {"what": "sizes in wheel diameters (dims.wheel_d): scale from the wheel, not from "
                                      "real-car specs", **{m: v for m, v in anc.items()}}
    elif fam == "map" and "dims.H" in stats:
        h = stats["dims.H"]
        out["anchors"] = {"what": f"heights in standing peds ({PED_HEIGHT_M} m)",
                          "dims.H_in_peds": [round(float(x) / PED_HEIGHT_M, 2) for x in h[:3]]}
    note = tiers().get("notes", {}).get(fam)
    if t == "sa_plus" and note:
        out["tier_note"] = note
    return out
