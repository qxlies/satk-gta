"""``style.brief_check``: wording in a task brief that leads to known mistakes of agent-built SA assets.

Terms come from ``data/style/brief_terms.json``: box words ('boxy', 'slab', 'squared-off', 'flat sides',
'voxel'), "style = numbers" phrases ('measured bands, not adjectives', 'inside the class band'), any triangle
number or budget, 'low-poly', pixel measuring of photos, painted grime, 'detail is texture, not geometry', plus
the older texture and shape terms ('crisp', 'clean shapes', 'flat colour', 'never photographs', 'close to real
scale', 'deliberate hard edges', '_dam never heavier', 'rebuild from nothing'). A brief describes the design in
words and cites the style help topics; it has no budget section.
"""

from __future__ import annotations

import bisect
import functools
import re

from ..core import resources

__all__ = ["terms", "check_text"]


@functools.lru_cache(maxsize=1)
def terms() -> dict:
    return resources.read_json("style", "brief_terms.json")


def _flat(text: str) -> tuple[str, list[int]]:
    """Text with line breaks as spaces (a phrase may wrap) and the offsets where lines start."""
    starts = [0]
    for m in re.finditer("\n", text):
        starts.append(m.end())
    return text.replace("\r", " ").replace("\n", " "), starts


def _line(starts: list[int], off: int) -> int:
    return bisect.bisect_right(starts, off)


def _excerpt(flat: str, a: int, b: int) -> str:
    s = max(0, a - 30)
    e = min(len(flat), b + 30)
    return re.sub(r"\s+", " ", flat[s:e]).strip()[:90]


def check_text(text: str, *, label: str = "brief", p50: dict | None = None) -> list[list]:
    """Rows ``[term, where, excerpt, why, fix, ref]`` for one brief text (``p50`` is accepted for older callers
    and ignored: any triangle number is flagged, whatever its size)."""
    flat, starts = _flat(text)
    rows = []
    for name, d in terms()["terms"].items():
        for m in re.finditer(d["pattern"], flat, flags=re.I):
            rows.append([name, f"{label}:{_line(starts, m.start())}", _excerpt(flat, m.start(), m.end()), d["why"],
                         d["fix"], d["ref"]])
    rows.sort(key=lambda r: (int(r[1].rsplit(":", 1)[1]), r[0]))
    return rows
