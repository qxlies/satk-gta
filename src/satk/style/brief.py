"""``style.brief_check``: wording in a task brief that prescribes a known anti-pattern.

Terms come from ``data/style/brief_terms.json`` ('crisp', 'clean shapes', 'flat colour', 'never photographs',
'close to real scale', 'deliberate hard edges', '_dam never heavier', 'rebuild from nothing') plus triangle
floors above the vanilla p50 of the brief's class. A brief should name the asset, the tier and the
``--like`` model and cite the style help topics, never restate numbers.
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


def _num(s: str) -> int:
    return int(s.replace(",", ""))


def check_text(text: str, *, label: str = "brief", p50: dict | None = None) -> list[list]:
    """Rows ``[term, where, excerpt, why, fix, ref]`` for one brief text.

    ``p50`` maps budget metrics (``veh.hd_tris``, ``part.tris[chassis]``, ...) to the vanilla p50 of the
    brief's class; triangle ranges whose floor is above it are flagged as ``tri_floor``.
    """
    flat, starts = _flat(text)
    t = terms()
    rows = []
    for name, d in t["terms"].items():
        for m in re.finditer(d["pattern"], flat, flags=re.I):
            rows.append([name, f"{label}:{_line(starts, m.start())}", _excerpt(flat, m.start(), m.end()), d["why"],
                         d["fix"], d["ref"]])
    if p50:
        b = t["budget"]
        lines = text.splitlines()
        for i, ln in enumerate(lines, 1):
            low = ln.lower()
            if not re.search(b["context"], low) and not any(k in low for k in b["parts"]):
                continue
            for m in re.finditer(b["pattern"], ln):
                floor = _num(m.group(1))
                head = low[:m.start()]
                metric = None
                best = -1
                for key, met in b["parts"].items():
                    pos = head.rfind(key)
                    if pos > best:
                        best, metric = pos, met
                if metric is None or metric not in p50:
                    metric = "geo.tris" if "geo.tris" in p50 and "veh.hd_tris" not in p50 else metric
                if metric is None or metric not in p50 or floor < 10:
                    continue
                if floor > float(p50[metric]):
                    rows.append(["tri_floor", f"{label}:{i}", re.sub(r"\s+", " ", ln.strip())[:90],
                                 f"{b['why']} ({metric} vanilla p50 {p50[metric]}, floor {floor})", b["fix"], b["ref"]])
    rows.sort(key=lambda r: (int(r[1].rsplit(":", 1)[1]), r[0]))
    return rows
