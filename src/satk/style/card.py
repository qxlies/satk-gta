"""``style.card``: generated style cards - Markdown tables for the guides and JSON lint presets.

* :func:`card_md` - one class (or peer set) and tier as a Markdown table: every number sits in a row named
  by its canonical metric, with the unit, p10/p50/p90 of the peer set, the tier band and the definition;
* :func:`lint_preset` - a ``satk asset lint --config`` file (``{"rules": {...}}``) whose budgets and shading
  thresholds come from the profiles of the tier (lint classes map, lod, cars, peds, weap, upgrade, pickup,
  interior_prop, interior_shell).

Both read only the style cache and ``data/style``; regenerate them after the cache changes.
"""

from __future__ import annotations

from . import cache as K
from . import classes as C
from .profile import _check_tier, describe, fence, metric_list, tier_band, tiers
from .registry import lookup

__all__ = ["card_md", "lint_preset", "LINT_CLASSES"]

#: Lint class -> style peer sets (the budget is the largest of them).
LINT_CLASSES = {
    "cars": ["car"], "peds": ["ped"], "weap": ["weapon"], "lod": ["lod"], "upgrade": ["upgrade"],
    "pickup": ["pickup"], "interior_prop": ["interior_prop"], "interior_shell": ["interior_shell"],
    "map": ["prop", "building", "terrain", "vegetation", "overlay", "time_object"],
}
#: Lint ``veh.hd_tris`` budget keys -> vehicle classes.
_VEH_TYPES = {"default": "car", "bike": "bike", "mtruck": "mtruck", "plane": "plane", "train": "train",
              "heli": "heli", "boat": "boat", "bmx": "bmx", "quad": "quad", "trailer": "trailer"}
#: Lint ``veh.part_tris`` part keys -> frames.
_PARTS = {"chassis": "chassis", "chassis_vlo": "chassis_vlo", "wheel": "wheel", "door": "door_lf_ok",
          "bump": "bump_front_ok", "bonnet": "bonnet_ok", "boot": "boot_ok", "windscreen": "windscreen_ok"}


def _fmt(x) -> str:
    if isinstance(x, float):
        if x == int(x) and abs(x) >= 10:
            return f"{int(x):,}"
        return f"{x:.3g}" if abs(x) < 10 else f"{x:,.1f}"
    if isinstance(x, int):
        return f"{x:,}"
    return str(x)


def card_md(cls: str, tier: str | None = None, *, profile_name: str = "vanilla", metrics: list[str] | None = None) -> str:
    """Markdown card of one class/peer set and tier."""
    t = _check_tier(tier)
    d = describe(cls, t, metrics=metrics, profile_name=profile_name)
    tg = d["target"]
    key = tg["peer_set"]
    status = d["tier_status"]
    L = [f"### {key} ({d['n']} vanilla models), tier {t} ({status})", "",
         f"Peer set `{key}`: {C.taxonomy()['classes'][C.split_peer(key)[0]]['what']}. Percentiles p10/p50/p90 "
         "over one value per model (numpy linear). Band = the tier band"
         + (" (proposal until validated in game)." if status == "proposal" else " (p10..p90)."), "",
         "| metric | unit | p10 | p50 | p90 | band | definition |", "|---|---|---|---|---|---|---|"]
    for m, p10, p50, p90, _n, lo, hi in d["rows"]:
        reg = lookup(m)
        unit = reg.unit if reg else ""
        defin = (reg.definition.split(". ")[0].rstrip(".") if reg else "")[:110].replace("|", "\\|")
        L.append(f"| `{m}` | {unit} | {_fmt(p10)} | {_fmt(p50)} | {_fmt(p90)} | {_fmt(lo)}-{_fmt(hi)} | {defin} |")
    if d.get("anchors"):
        a = dict(d["anchors"])
        what = a.pop("what", "")
        L += ["", f"Anchors ({what}):", ""]
        for m, v in a.items():
            L.append(f"- `{m}`: p10 {_fmt(v[0])}, p50 {_fmt(v[1])}, p90 {_fmt(v[2])}")
    if d.get("exemplars"):
        L += ["", "Exemplars (closest to the p50 of the main metric): " + ", ".join(f"`{e}`" for e in d["exemplars"])]
    if d.get("tier_note"):
        L += ["", f"Tier note: {d['tier_note']}"]
    if tg.get("fallback"):
        L += ["", "Fallback: " + "; ".join(tg["fallback"])]
    return "\n".join(L) + "\n"


def _hi(peer: dict, metric: str, key: str, tier: str) -> float | None:
    s = peer.get(metric)
    if not s:
        return None
    if tier == "vanilla":
        return fence(s)[1]
    tb = tier_band(metric, s, key, tier)
    if tb["status"] == "proposal":
        return tb.get("cap", tb["hi"])
    return fence(s)[1]


def lint_preset(tier: str | None = None, *, profile_name: str = "vanilla") -> dict:
    """A lint config (``{"_comment", "rules"}``) with budgets and shading limits of the tier."""
    t = _check_tier(tier)
    c = K.load(profile_name)
    peers = c.peers
    rules: dict = {}
    budget = {}
    for lc, keys in LINT_CLASSES.items():
        vals = [_hi(peers[k]["metrics"], "geo.tris", k, t) for k in keys if k in peers]
        vals = [v for v in vals if v is not None]
        if lc == "cars":
            vals = [_hi(peers["car"]["metrics"], "file.tris", "car", t)] if "car" in peers else vals
        if vals:
            budget[lc] = int(round(max(vals), -1))
    if budget:
        rules["dff.tris_budget"] = {"params": {"budget": budget}}
    hb = {}
    for k, cls in _VEH_TYPES.items():
        if cls in peers:
            v = _hi(peers[cls]["metrics"], "veh.hd_tris", cls, t) or _hi(peers[cls]["metrics"], "geo.tris", cls, t)
            if v:
                hb[k] = int(round(v, -1))
    if hb:
        rules["veh.hd_tris"] = {"params": {"budget": hb}}
    if "car" in peers:
        pb = {}
        for k, frame in _PARTS.items():
            v = _hi(peers["car"]["metrics"], f"part.tris[{frame}]", "car", t)
            if v:
                pb[k] = int(round(v, -1)) if v >= 100 else int(round(v))
        if pb:
            rules["veh.part_tris"] = {"params": {"budget": pb}}
    bend, p10, vpt = {}, {}, {}
    for lc, key in (("cars", "car"), ("peds", "ped"), ("weap", "weapon")):
        s = peers.get(key, {}).get("metrics", {})
        if "shade.normal_bend" in s:
            bend[lc] = round(max(fence(s["shade.normal_bend"])[0], 0.0), 2)
            p10[lc] = s["shade.normal_bend"][0]
        if "dff.verts_per_tri" in s:
            vpt[lc] = round(fence(s["dff.verts_per_tri"])[1], 3)
    if bend:
        rules["dff.flat_shading"] = {"params": {"min_bend": bend, "vanilla_p10": p10}}
    if vpt:
        rules["dff.vert_sharing"] = {"params": {"max": vpt}}
    k = tiers()["fence"]
    return {"_comment": f"Generated by 'satk style card --lint-preset --tier {t}' from the style cache of profile "
                        f"{c.profile!r} ({c.data.get('index_hash', '')[:12]}): limits are the upper fence "
                        f"(p90 + max({k['k']} x (p90 - p50), {k.get('min_rel', 0)} x p50)) of the vanilla peer set"
                        + ("" if t == "vanilla" else "; sa_plus budgets are the PROPOSAL caps/bands of tiers.json")
                        + ". Use: satk asset lint <target> --config <this file>.",
            "rules": rules}


def card_classes() -> list[str]:
    """Classes with a card by default (the main class of each family and the car bodies)."""
    out = ["car", "car.sedan", "car.coupe_muscle", "car.sports", "car.suv_pickup", "car.van", "car.truck_bus",
           "bike", "boat", "heli", "plane", "prop", "building", "terrain", "vegetation", "interior_prop",
           "interior_shell", "lod", "ped", "weapon", "pickup", "upgrade"]
    return out


def metrics_of(cls: str) -> list[str]:
    return metric_list(cls, "profile")
