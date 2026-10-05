"""Step 4 of ``index build``: winners (SPEC §4.3.1 "Правила победителя"). Owner: WP-03.

* blobs: "first registered archive wins" per ``(ns, stem, ext)`` (``Streaming.cpp:1188``); loose
  files share the ``main`` key but come after every IMG; ``player``/``anim``/``cuts`` are separate;
* IDE: for one model ID the later load wins: larger ``layer.priority``, then later IDE file, then
  later line;
* COL: the first occurrence of a name wins (DAT ``COLFILE`` order, then exe-loaded loose files,
  then IMG archives in registration order, then everything else);
* TXD parents: ``txdp`` of the loaded IDE files (the last pair for a child wins), plus the
  implicit ``vehicle`` parent of every TXD used by a ``cars`` model (``FileLoader.cpp:1834``).

Pure functions over plain lists so they are easy to test. Stdlib only.
"""

from __future__ import annotations

from typing import Iterable, Sequence

__all__ = ["ns_key", "blob_winners", "model_winners", "col_winners", "txd_parents"]


def ns_key(ns: str) -> str:
    """Namespace used for the winner key: loose files compete inside ``main``."""
    return "main" if ns in ("main", "loose") else ns


def blob_winners(blobs: Sequence[tuple[int, str, str, str, tuple]]) -> dict[int, int | None]:
    """``[(id, ns, stem, ext, order_key)] -> {id: shadowed_by_id or None}``.

    ``order_key`` sorts registration order (smaller = registered earlier = wins).
    """
    out: dict[int, int | None] = {}
    winners: dict[tuple[str, str, str], int] = {}
    for bid, ns, stem, ext, _k in sorted(blobs, key=lambda b: (b[4], b[0])):
        key = (ns_key(ns), stem.lower(), ext.lower())
        w = winners.get(key)
        if w is None:
            winners[key] = bid
            out[bid] = None
        else:
            out[bid] = w
    return out


def model_winners(models: Iterable[tuple[int, int, int, int, int]]) -> set[int]:
    """``[(rid, id, layer_priority, ide_order, line)] -> {rid of the active definition per id}``."""
    best: dict[int, tuple] = {}
    for rid, mid, prio, ide_order, line in models:
        k = (prio, ide_order, line, rid)
        cur = best.get(mid)
        if cur is None or k > cur[0]:
            best[mid] = (k, rid)
    return {v[1] for v in best.values()}


def col_winners(cols: Iterable[tuple[int, str, tuple]]) -> set[int]:
    """``[(col_id, name, order_key)] -> {col_id of the first occurrence per lower-case name}``."""
    seen: dict[str, int] = {}
    for cid, name, _k in sorted(cols, key=lambda c: (c[2], c[0])):
        seen.setdefault(name.lower(), cid)
    return set(seen.values())


def txd_parents(txdp: Iterable[tuple[str, str]], car_txds: Iterable[str]) -> dict[str, tuple[str, str]]:
    """``{child txd (lower): (parent (lower), via 'txdp'|'vehicle')}``.

    ``txdp`` pairs in load order (the last pair for a child wins); every TXD of a ``cars`` model
    without a ``txdp`` parent gets the implicit ``vehicle`` parent (not ``vehicle`` itself).
    """
    out: dict[str, tuple[str, str]] = {}
    for child, parent in txdp:
        c, p = child.strip().lower(), parent.strip().lower()
        if c and p and c != p:
            out[c] = (p, "txdp")
    for t in car_txds:
        c = (t or "").strip().lower()
        if c and c != "vehicle" and c not in out:
            out[c] = ("vehicle", "vehicle")
    return out
