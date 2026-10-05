"""Address -> function attribution with optional exact ranges (SPEC §4.9.3; owner WP-09).

:class:`AddrMap` holds every known function start (named or not), the thunks and the sections,
and answers :meth:`AddrMap.locate`:

1. Imported exact ranges take precedence, preserving holes, detached blocks and thunk owners.
   A known exact body is never extended heuristically into its gaps or trailing padding.
2. ``.HOODLUM`` (and any other section reached only through thunks): the thunk with the nearest
   ``target <= A`` owns ``A`` (``in_hoodlum``);
3. main ``.text``: the nearest start ``s <= A``; if a ``jmp``/``push -1; jmp`` thunk target ``t``
   lies between ``s`` and ``A`` the address is inside a *moved body* and belongs to that thunk's
   owner (``via_thunk``);
4. confidence: ``exact`` (Ghidra range), ``high`` (curated named start), ``medium`` (source-only
   declaration, ambiguous unnamed start or moved body inside .text), ``low`` (no named predecessor).
   In thunk-only sections, a bounded body with a named, non-tentative owner is ``high``;
   tentative or unnamed owners are ``medium``. An unbounded last body region is always ``low``.

The same class is used at build time (patch -> function) and at query time (loaded from the DB).
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

__all__ = ["SectionInfo", "Location", "AddrMap", "CONF_ORDER"]

CONF_ORDER = {"exact": 4, "high": 3, "medium": 2, "low": 1, "none": 0}


@dataclass(frozen=True, slots=True)
class SectionInfo:
    name: str
    start: int
    end: int
    flags: int

    @property
    def executable(self) -> bool:
        return bool(self.flags & 0x20000020)


@dataclass(slots=True)
class Location:
    addr: int
    section: str | None
    kind: str                                  # code | data | outside
    start: int | None = None                   # function entry (thunk entry for moved bodies)
    off: int | None = None                     # offset from the body start (thunk target if via thunk)
    body: int | None = None                    # body start used for ``off``
    end: int | None = None                     # exclusive end of the body region
    confidence: str = "none"
    alt: list[tuple[int, int]] = field(default_factory=list)   # (start, off) other candidates
    via_thunk: tuple[int, int, str] | None = None              # (thunk entry, target, kind)
    in_hoodlum: bool = False


class AddrMap:
    """In-memory index of starts/thunks/sections (all sorted lists; ~40k ints)."""

    def __init__(self, sections: list[SectionInfo], starts: dict[int, bool], thunks: list[tuple[int, int, str]],
                 ghidra: set[int] | None = None, ends: dict[int, int] | None = None, *,
                 tentative: set[int] | None = None,
                 exact_ranges: list[tuple[int, int, int, int]] | None = None):
        self.sections = sorted(sections, key=lambda s: s.start)
        self._sec_starts = [s.start for s in self.sections]
        self.starts = sorted(starts)
        self.named = {a for a, n in starts.items() if n}
        self.named_sorted = sorted(self.named)
        self.thunks = {a: (t, k) for a, t, k in thunks}
        tt = sorted((t, a, k) for a, t, k in thunks)
        self._tt = [t for t, _a, _k in tt]
        self._tt_rec = tt
        self.ghidra = ghidra or set()
        self.ends = ends or {}
        self.tentative = tentative or set()
        # (lo, hi, canonical entry, body entry); an exported body may have holes or
        # detached blocks before its entry. A nearest-start lookup cannot represent it.
        self.exact_ranges = sorted(exact_ranges if exact_ranges is not None else
                                   [(a, self.ends[a], a, a) for a in self.ghidra if a in self.ends])
        self._range_starts = [r[0] for r in self.exact_ranges]
        self._exact_bodies = {r[3] for r in self.exact_ranges}
        self._function_ranges: dict[int, list[tuple[int, int]]] = {}
        for lo, hi, entry, _body in self.exact_ranges:
            self._function_ranges.setdefault(entry, []).append((lo, hi))
        texts = [s for s in self.sections if s.name == ".text"]
        self.main_text = texts[0] if texts else None

    # -- sections ---------------------------------------------------------------------

    def section_of(self, a: int) -> SectionInfo | None:
        i = bisect.bisect_right(self._sec_starts, a) - 1
        if i >= 0 and self.sections[i].start <= a < self.sections[i].end:
            return self.sections[i]
        return None

    def _in_main(self, a: int) -> bool:
        s = self.section_of(a)
        return s is not None and (s is self.main_text or (s.name == "_rwcseg"))

    # -- primitives -------------------------------------------------------------------

    def start_le(self, a: int) -> int | None:
        i = bisect.bisect_right(self.starts, a) - 1
        return self.starts[i] if i >= 0 else None

    def start_gt(self, a: int) -> int | None:
        i = bisect.bisect_right(self.starts, a)
        return self.starts[i] if i < len(self.starts) else None

    def named_le(self, a: int) -> int | None:
        i = bisect.bisect_right(self.named_sorted, a) - 1
        return self.named_sorted[i] if i >= 0 else None

    def target_le(self, a: int, lo: int) -> tuple[int, int, str] | None:
        """Nearest thunk ``(target, entry, kind)`` with ``lo <= target <= a``."""
        i = bisect.bisect_right(self._tt, a) - 1
        if i >= 0 and self._tt[i] >= lo:
            return self._tt_rec[i]
        return None

    def target_gt(self, a: int) -> int | None:
        i = bisect.bisect_right(self._tt, a)
        return self._tt[i] if i < len(self._tt) else None

    def end_of(self, start: int) -> int:
        """Exclusive body end, exact when supplied, otherwise the next start in its section."""
        if start in self.ends:
            return self.ends[start]
        sec = self.section_of(start)
        nxt = self.start_gt(start)
        lim = sec.end if sec else start + 1
        return nxt if nxt is not None and nxt <= lim else lim

    def _region_end(self, body: int, sec: SectionInfo) -> int:
        cands = [sec.end]
        n1 = self.start_gt(body)
        if n1 is not None and n1 < sec.end:
            cands.append(n1)
        n2 = self.target_gt(body)
        if n2 is not None and n2 < sec.end:
            cands.append(n2)
        return min(cands)

    def exact_function_ranges(self, entry: int) -> list[tuple[int, int]]:
        """Proven ranges of an owner, including its relocated body, or an empty list."""
        return list(self._function_ranges.get(entry, ()))

    # -- locate -----------------------------------------------------------------------

    def locate(self, a: int) -> Location:
        sec = self.section_of(a)
        if sec is None:
            return Location(a, None, "outside")
        if not sec.executable:
            return Location(a, sec.name, "data")
        loc = Location(a, sec.name, "code", in_hoodlum=sec.name == ".HOODLUM")
        i = bisect.bisect_right(self._range_starts, a) - 1
        if i >= 0 and a < self.exact_ranges[i][1]:
            _lo, hi, entry, body = self.exact_ranges[i]
            loc.start, loc.body, loc.off, loc.end = entry, body, a - body, hi
            loc.confidence = "exact"
            thunk = self.thunks.get(entry)
            if body != entry and thunk is not None and thunk[0] == body:
                loc.via_thunk = (entry, body, thunk[1])
            return loc
        if not self._in_main(a):
            rec = self.target_le(a, sec.start)
            if rec is None:
                return loc
            t, entry, kind = rec
            if t in self._exact_bodies:
                return loc  # gap or padding after an exact body; no heuristic extension
            loc.start, loc.body, loc.off = entry, t, a - t
            loc.end = self._region_end(t, sec)
            loc.via_thunk = (entry, t, kind)
            loc.in_hoodlum = sec.name == ".HOODLUM"
            # A following start/target bounds the body, as the next start does in .text.
            # The last region has no such bound and may include trailing section padding.
            if loc.end >= sec.end:
                loc.confidence = "low"
            elif entry in self.named and entry not in self.tentative:
                loc.confidence = "high"
            else:
                loc.confidence = "medium"
            return loc
        s = self.start_le(a)
        if s is not None and self.section_of(s) is not sec:
            s = None
        rec = self.target_le(a, s + 1 if s is not None else sec.start)
        if rec is not None and self.section_of(rec[0]) is sec:
            t, entry, kind = rec
            if t in self._exact_bodies:
                return loc
            loc.start, loc.body, loc.off = entry, t, a - t
            loc.end = self._region_end(t, sec)
            loc.via_thunk = (entry, t, kind)
            loc.confidence = "medium"
            if s is not None:
                loc.alt.append((s, a - s))
            return loc
        if s is None or s in self.ghidra or a >= self.end_of(s):
            return loc
        loc.start, loc.body, loc.off = s, s, a - s
        loc.end = self.end_of(s)
        if s in self.tentative:
            loc.confidence = "medium" if s in self.named else "low"
            # An Install declaration can describe an interior hook site. Keep the
            # prior named owner as an alternative until exact bounds disambiguate it.
            n = self.named_le(s - 1)
            if n is not None and self.section_of(n) is sec:
                loc.alt.append((n, a - n))
        elif s in self.named:
            loc.confidence = "high"
        else:
            n = self.named_le(a)
            if n is not None and self.section_of(n) is sec:
                loc.confidence = "medium"
                loc.alt.append((n, a - n))
            else:
                loc.confidence = "low"
        return loc
