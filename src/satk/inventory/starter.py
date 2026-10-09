"""Starter inventories per kind and detail level (``data/kit/inventory/<kind>.json``). Stdlib only.

Regions are areas of the model; each names its ``views``: the close-up regions of ``blender preview --regions``
(``data/kit/regions/<kind>.json``) that show it (``overall`` without views = the standard preview sheet).

A starter lists the parts and details a model of the kind has, each with the lowest detail level that needs it
(``simple`` = what every vanilla model of the kind carries, ``standard`` = a full vanilla-class model, ``hero`` =
richer than vanilla: engine bays, full interiors, underbodies, roof clutter, separate sights). Items carry a stable
``key``; :func:`instantiate` turns the items of one level into an inventory with ids ``I01``.. (``attaches_to``
keys become ids). The design stage edits that inventory: it renames items to the real design, adds what the
design has (with keys of its own) and waives a starter item only with a reason.

Starter file::

    {"format": "satk.inventory-starter/1", "kind": "automobile", "summary": "...",
     "regions": {"overall": {"summary": "the whole model", "views": []},
                 "sides": {"summary": "...", "views": ["left", "right"]}, ...},
     "items": [{"key", "name", "region", "category", "construction", "attaches_to": key | [keys] | null,
                "stage", "detail": "simple|standard|hero", "required"?: false, "frame"?, "count"?, "notes"?}]}
"""

from __future__ import annotations

from typing import Any

from ..core.errors import SatkError
from .schema import DETAILS, detail_rank

__all__ = ["FORMAT", "kinds", "load", "instantiate", "summary", "views"]

FORMAT = "satk.inventory-starter/1"
_DIR = "kit/inventory"


def kinds() -> list[str]:
    """Kinds that have a starter list."""
    from ..core import resources

    try:
        names = resources.list_files(_DIR)
    except SatkError:
        return []
    out = []
    for n in names:
        base = str(n).replace("\\", "/").rsplit("/", 1)[-1]
        if base.endswith(".json") and base != "schema.json":
            out.append(base[:-5])
    return sorted(out)


def load(kind: str) -> dict:
    """The starter of ``kind`` (``NOT_FOUND`` with the kinds that have one)."""
    from ..core import resources

    k = str(kind or "").strip().lower()
    if not k or "/" in k or "\\" in k or k == "schema":
        raise SatkError("BAD_PARAMS", f"bad kind {kind!r}")
    try:
        data = resources.read_json(_DIR, f"{k}.json")
    except SatkError as e:
        if e.code != "NOT_FOUND":
            raise
        import difflib

        ks = kinds()
        raise SatkError("NOT_FOUND", f"no starter inventory for kind {kind!r}", data={"kinds": ks},
                        did_you_mean=difflib.get_close_matches(k, ks, n=3, cutoff=0.5),
                        hint="satk kit kinds lists the asset kinds") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise SatkError("INTERNAL", f"data/{_DIR}/{k}.json is not a {FORMAT} file")
    return data


def _ids(n: int) -> str:
    return f"I{n:02d}" if n < 100 else f"I{n}"


def instantiate(kind: str, detail: str = "hero") -> dict:
    """An inventory ``{kind, detail, items}`` of the starter items up to ``detail`` (ids in starter order)."""
    if detail not in DETAILS:
        raise SatkError("BAD_PARAMS", f"detail must be one of {', '.join(DETAILS)}, got {detail!r}")
    st = load(kind)
    rank = detail_rank(detail)
    chosen = [s for s in st.get("items") or [] if detail_rank(s.get("detail")) <= rank]
    ids = {s["key"]: _ids(i + 1) for i, s in enumerate(chosen)}
    items: list[dict[str, Any]] = []
    for s in chosen:
        a = s.get("attaches_to")
        if isinstance(a, list):
            att: Any = [ids[k] for k in a if k in ids] or None
            if isinstance(att, list) and len(att) == 1:
                att = att[0]
        elif isinstance(a, str):
            att = ids.get(a)
            if att is None:   # the parent is above this level: attach to the first root item
                att = next((ids[x["key"]] for x in chosen if not x.get("attaches_to")), None)
        else:
            att = None
        it: dict[str, Any] = {"id": ids[s["key"]], "key": s["key"], "name": s["name"], "region": s["region"],
                              "category": s["category"], "construction": s["construction"], "attaches_to": att,
                              "stage": s["stage"], "required": s.get("required", True) is not False,
                              "detail": s.get("detail", "simple")}
        for opt in ("frame", "count", "max_gap_mm", "notes", "verify"):
            if s.get(opt) is not None:
                it[opt] = s[opt]
        items.append(it)
    return {"format": "satk.inventory/1", "kind": st["kind"], "detail": detail, "source": f"starter {st['kind']}",
            "items": items}


def summary(kind: str) -> dict:
    """Item counts per detail level and the regions of a kind."""
    st = load(kind)
    counts = {d: sum(1 for s in st.get("items") or [] if detail_rank(s.get("detail")) <= detail_rank(d))
              for d in DETAILS}
    return {"kind": st["kind"], "summary": st.get("summary", ""), "regions": list(st.get("regions") or {}),
            "items": counts}


def views(kind: str) -> dict[str, list[str]]:
    """``{region: [close-up regions that show it]}`` of a kind (``{}`` without a starter)."""
    try:
        st = load(kind)
    except SatkError:
        return {}
    out: dict[str, list[str]] = {}
    for name, r in (st.get("regions") or {}).items():
        out[name] = list(r.get("views") or []) if isinstance(r, dict) else []
    return out
