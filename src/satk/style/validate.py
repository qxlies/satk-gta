"""Leave-one-out spread of the vanilla reference numbers (``style build --validate``).

Every vanilla model is compared with its own peer set with itself left out, on the ``reference`` metrics
of its family (``data/style/tiers.json``). It measures how tight a peer set is (the share of vanilla models
with 0-1 numbers beyond the fence); nothing judges an asset by it: the numbers are reference only.

Example::

    from satk.style import cache, validate
    r = validate.loo(cache.load("vanilla"))
    r["families"]["vehicle"]      # {'models': 212, 'pass_share': 0.93, 'rows_p50': 0, ...}
"""

from __future__ import annotations

from . import classes as C
from .profile import fence, metric_list

__all__ = ["peer_of", "loo"]


def peer_of(rec: dict, peers: dict, min_n: int) -> str | None:
    """The peer set a model is compared with (class@bucket, falling back like ``style.profile``)."""
    chain = C.fallback_chain(C.peer_key(rec["cls"], rec.get("bucket")))
    for k in chain:
        p = peers.get(k)
        if p and p["n"] >= min_n:
            return k
    return next((k for k in chain if k in peers), None)


def loo(cache, *, families: tuple[str, ...] | None = None, max_rows: int = 1) -> dict:
    """Leave-one-out pass rates per family and per peer set.

    Returns ``{"families": {fam: {models, pass_share, rows_mean, worst_metrics}}, "peer_sets": {key:
    {models, pass_share}}, "max_rows": max_rows}``; a model passes with at most ``max_rows`` out-of-band rows.
    """
    import numpy as np

    peers = cache.peers
    min_n = int(C.taxonomy()["min_peers"])
    recs = cache.records()
    groups: dict[str, list[dict]] = {}
    for r in recs:
        if families and C.family(r["cls"]) not in families:
            continue
        k = peer_of(r, peers, min_n)
        if k is not None:
            groups.setdefault(k, []).append(r)
    # every model of the peer set (the group above is only the models compared against it)
    members: dict[str, list[dict]] = {}
    for r in recs:
        for k in C.fallback_chain(C.peer_key(r["cls"], r.get("bucket"))):
            if k in groups:
                members.setdefault(k, []).append(r)
    fam_stats: dict[str, dict] = {}
    peer_stats: dict[str, dict] = {}
    for key, rs in sorted(groups.items()):
        mem = members[key]
        idx = {id(r): i for i, r in enumerate(mem)}
        checks = metric_list(key, "reference")
        cols = {}
        for m in checks:
            v = np.array([r["m"].get(m, np.nan) for r in mem], dtype=np.float64)
            cols[m] = v
        chain = C.fallback_chain(key)
        parent = peers.get(chain[1])["metrics"] if len(chain) > 1 and chain[1] in peers else {}
        npass = 0
        for r in rs:
            i = idx[id(r)]
            bad = []
            for m, v in cols.items():
                x = v[i]
                if not np.isfinite(x):
                    continue
                rest = np.delete(v, i)
                rest = rest[np.isfinite(rest)]
                if len(rest) < 3:
                    continue
                p10, p50, p90 = np.percentile(rest, [10, 50, 90])
                lo, hi = fence([p10, p50, p90, len(rest)], parent.get(m))
                if x < lo or x > hi:
                    bad.append(m)
            fam = C.family(key)
            fs = fam_stats.setdefault(fam, {"models": 0, "pass": 0, "rows": 0, "worst": {}})
            fs["models"] += 1
            fs["rows"] += len(bad)
            for m in bad:
                fs["worst"][m] = fs["worst"].get(m, 0) + 1
            if len(bad) <= max_rows:
                fs["pass"] += 1
                npass += 1
        peer_stats[key] = {"models": len(rs), "pass_share": round(npass / len(rs), 3)}
    out_f = {}
    for fam, fs in sorted(fam_stats.items()):
        worst = sorted(fs["worst"].items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        out_f[fam] = {"models": fs["models"], "pass_share": round(fs["pass"] / fs["models"], 3),
                      "rows_mean": round(fs["rows"] / fs["models"], 3),
                      "worst_metrics": [f"{m} {n}" for m, n in worst]}
    return {"families": out_f, "peer_sets": peer_stats, "max_rows": max_rows}
