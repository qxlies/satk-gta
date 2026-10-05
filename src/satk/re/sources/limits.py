"""Engine limit table: gta-reversed limits + ``engine/mtasa/docs/limits.toml`` (SPEC §4.9.2; WP-09).

WP-11 keeps ``docs/limits.toml`` in our MTA fork (``[[limit]]`` with ``name``, ``kind``, ``vanilla``,
``trunk``, ``neon``, ``global``). It is read read-only through git at the fork's ``HEAD``:

* an entry whose ``global`` matches a gta-reversed limit fills that row's ``trunk``/``neon``;
* other entries become rows of their own (``source = engine/mtasa:docs/limits.toml``).

Kinds are normalized (``id-range`` -> ``id_range``); unknown kinds are skipped and counted.
"""

from __future__ import annotations

import tomllib

from ..gitsrc import SourceTree
from . import LimitDef

__all__ = ["LIMITS_PATH", "load_limits_toml", "merge_limits", "KINDS"]

LIMITS_PATH = "docs/limits.toml"
KINDS = ("pool", "array", "id_range", "streaming", "store", "world")


def _int(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v.strip(), 0)
        except ValueError:
            return None
    return None


def load_limits_toml(tree: SourceTree | None, path: str = LIMITS_PATH) -> list[dict] | None:
    """``[[limit]]`` entries or ``None`` when the file does not exist / is invalid."""
    if tree is None:
        return None
    text = tree.read(path)
    if text is None:
        return None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    items = data.get("limit")
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else None


def merge_limits(base: list[LimitDef], entries: list[dict] | None, source: str
                 ) -> tuple[list[tuple], dict]:
    """Rows ``(name, kind, vanilla, global_addr, trunk, neon, source)`` and merge stats."""
    rows: dict[str, list] = {lim.name: [lim.name, lim.kind, lim.vanilla, lim.global_addr, None, None, lim.source]
                             for lim in base}
    by_addr: dict[int, str] = {}
    for lim in base:
        if lim.global_addr is not None:
            by_addr.setdefault(lim.global_addr, lim.name)
    st = {"entries": 0, "matched": 0, "added": 0, "skipped": 0, "vanilla_mismatch": []}
    for e in entries or []:
        st["entries"] += 1
        kind = str(e.get("kind", "")).replace("-", "_").lower()
        vanilla = _int(e.get("vanilla"))
        trunk, neon = _int(e.get("trunk")), _int(e.get("neon"))
        g = _int(e.get("global"))
        name = str(e.get("name") or "").strip()
        if g is not None and g in by_addr:
            r = rows[by_addr[g]]
            r[4] = trunk if trunk is not None else r[4]
            r[5] = neon if neon is not None else r[5]
            if vanilla is not None and vanilla != r[2]:
                st["vanilla_mismatch"].append(f"{name}: toml {vanilla} vs gta-reversed {r[2]} ({r[0]})")
            st["matched"] += 1
            continue
        if kind not in KINDS or vanilla is None or not name:
            st["skipped"] += 1
            continue
        if name in rows:
            name = f"{name}@limits.toml"
        rows[name] = [name, kind, vanilla, g, trunk, neon, source]
        st["added"] += 1
    return [tuple(rows[k]) for k in sorted(rows)], st
