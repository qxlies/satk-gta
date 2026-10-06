"""MTA world-texture name matching and compact pattern lists. Stdlib only.

How MTA matches (``CRenderWareSA::AppendAdditiveMatch`` and ``SWildcardMatchChain`` in the MTA client): the texture
name and the pattern are lower-cased, then compared with ``*`` (any run, also empty) and ``?`` (exactly one
character); every other character is literal. A shader keeps an ordered list of *apply* patterns
(``engineApplyShaderToWorldTexture``) and *remove* patterns (``engineRemoveShaderFromWorldTexture``); for each
texture the LAST matching pattern decides, and re-adding a pattern moves it to the end. The pattern ``cj`` alone is
rewritten to ``cj_ped_*``.

:func:`cover` turns a wanted set of names into a short list of patterns: apply patterns that hit only wanted names
(or a few extra names that remove patterns cancel), checked with :func:`resolve` against the whole universe.
"""

from __future__ import annotations

import bisect
import heapq
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

__all__ = ["mta_pattern", "matcher", "matches", "match_names", "resolve", "Pick", "Plan", "cover", "NameSet"]

_TOKEN = re.compile(r"[a-z]+|[0-9]+")


def mta_pattern(pattern: str) -> str:
    """The pattern as MTA uses it: lower case, ``cj`` -> ``cj_ped_*``."""
    p = str(pattern).lower()
    return "cj_ped_*" if p == "cj" else p


@lru_cache(maxsize=4096)
def matcher(pattern: str):
    """A compiled ``fullmatch`` for one MTA pattern (``*`` and ``?`` only; the rest is literal)."""
    p = mta_pattern(pattern)
    rx = "".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in p)
    return re.compile(rx, re.DOTALL).fullmatch


def matches(pattern: str, name: str) -> bool:
    """True when MTA would match texture ``name`` with ``pattern``."""
    return matcher(pattern)(str(name).lower()) is not None


def match_names(pattern: str, names: Iterable[str]) -> list[str]:
    """Names (already lower case) the pattern matches, in input order."""
    m = matcher(pattern)
    return [n for n in names if m(n) is not None]


def resolve(ops: Iterable[tuple[str, str]], names: Iterable[str]) -> set[str]:
    """Names a shader ends up on after ``ops`` = ``[("apply"|"remove", pattern), ...]`` in call order.

    Re-applying a pattern moves it to the end (MTA erases the earlier identical entry first).
    """
    chain: list[tuple[str, bool]] = []
    for op, pat in ops:
        p = mta_pattern(pat)
        chain = [c for c in chain if c[0] != p]
        chain.append((p, op == "apply"))
    compiled = [(matcher(p), add) for p, add in chain]
    out: set[str] = set()
    for n in names:
        hit = False
        for m, add in compiled:
            if m(n) is not None:
                hit = add
        if hit:
            out.add(n)
    return out


# --------------------------------------------------------------------------- name sets


class NameSet:
    """A sorted set of names with fast prefix / suffix / infix lookups (indices into :attr:`names`)."""

    def __init__(self, names: Iterable[str]):
        self.names: list[str] = sorted(set(names))
        self.index = {n: i for i, n in enumerate(self.names)}
        rev = sorted((n[::-1], i) for i, n in enumerate(self.names))
        self._rev = [r for r, _ in rev]
        self._rev_idx = [i for _, i in rev]
        self._joined = "\n".join(self.names) + "\n"
        self._offs: list[int] = []
        o = 0
        for n in self.names:
            self._offs.append(o)
            o += len(n) + 1

    def __len__(self) -> int:
        return len(self.names)

    def prefix(self, text: str) -> range:
        lo = bisect.bisect_left(self.names, text)
        hi = bisect.bisect_left(self.names, text + "\U0010ffff")
        return range(lo, hi)

    def suffix(self, text: str) -> list[int]:
        r = text[::-1]
        lo = bisect.bisect_left(self._rev, r)
        hi = bisect.bisect_left(self._rev, r + "\U0010ffff")
        return sorted(self._rev_idx[lo:hi])

    def infix(self, text: str) -> list[int]:
        out: list[int] = []
        j = self._joined
        start = 0
        last = -1
        while True:
            k = j.find(text, start)
            if k < 0:
                break
            i = bisect.bisect_right(self._offs, k) - 1
            if i != last:
                out.append(i)
                last = i
            start = self._offs[i] + len(self.names[i]) + 1 if i + 1 < len(self._offs) else len(j)
        return out

    def lookup(self, kind: str, text: str) -> list[int]:
        """Indices matched by a candidate: ``exact``, ``prefix`` (text*), ``suffix`` (*text), ``infix`` (*text*)."""
        if kind == "exact":
            i = self.index.get(text)
            return [] if i is None else [i]
        if kind == "prefix":
            return list(self.prefix(text))
        if kind == "suffix":
            return self.suffix(text)
        return self.infix(text)

    def count(self, kind: str, text: str) -> int:
        if kind == "prefix":
            return len(self.prefix(text))
        return len(self.lookup(kind, text))


def _pattern(kind: str, text: str) -> str:
    return {"exact": text, "prefix": text + "*", "suffix": "*" + text, "infix": "*" + text + "*"}[kind]


def _wild_free(s: str) -> bool:
    return "*" not in s and "?" not in s


_SEPS = "_ -."


def _prefix_natural(n: str, k: int) -> bool:
    """``n[:k]*`` ends on a separator (``tar_*``) or a letter/digit change (``road5*`` before ``_``)."""
    a = n[k - 1]
    if a in _SEPS:
        return True
    return k < len(n) and n[k] not in _SEPS and a.isdigit() != n[k].isdigit()


def _suffix_natural(n: str, k: int) -> bool:
    """``*n[k:]`` starts on a separator (``*_lod``) or a letter/digit change."""
    b = n[k]
    if b in _SEPS:
        return True
    return k > 0 and n[k - 1] not in _SEPS and n[k - 1].isdigit() != b.isdigit()


def _candidates(names: list[str], *, infix: bool = True) -> dict[tuple[str, str], bool]:
    """``(kind, text) -> natural`` candidates made from ``names``: prefixes and suffixes of 3+ characters,
    letter/digit tokens and ``_``-separated parts (infix) and 4-8 character pieces of long words. *Natural*
    candidates end on token boundaries; they win ties, so ``*water*`` beats ``*wate*``."""
    out: dict[tuple[str, str], bool] = {}

    def add(key: tuple[str, str], nat: bool) -> None:
        out[key] = out.get(key, False) or nat

    for n in names:
        if not _wild_free(n) or n == "cj":
            continue
        L = len(n)
        for k in range(3, L):
            add(("prefix", n[:k]), _prefix_natural(n, k))
            add(("suffix", n[L - k:]), _suffix_natural(n, L - k))
        if not infix:
            continue
        parts = set(_TOKEN.findall(n)) | {p for p in re.split(r"[_\s\-.]+", n) if p}
        for t in parts:
            if len(t) >= 3 and t != n:
                add(("infix", t), True)
            if t.isalpha() and len(t) >= 6:
                for a in range(4, min(8, len(t)) + 1):
                    for st in range(0, len(t) - a + 1):
                        add(("infix", t[st:st + a]), False)
    return out


# --------------------------------------------------------------------------- cover


@dataclass
class Pick:
    """One pattern of a plan: ``covers`` wanted names (apply) or cancelled extra names (remove); ``extra`` =
    names outside the wanted set an apply pattern also matches (later cancelled by remove patterns when the
    plan is exact); ``sample`` = up to three of them."""

    op: str
    pattern: str
    covers: int
    extra: int = 0
    sample: list[str] = field(default_factory=list)

    def row(self) -> list:
        return [self.op, self.pattern, self.covers, self.extra, ", ".join(self.sample)]


@dataclass
class Plan:
    picks: list[Pick]
    exact: bool
    extra: int           # names outside the wanted set that the final plan still hits

    @property
    def apply(self) -> list[str]:
        return [p.pattern for p in self.picks if p.op == "apply"]

    @property
    def remove(self) -> list[str]:
        return [p.pattern for p in self.picks if p.op == "remove"]

    def ops(self) -> list[tuple[str, str]]:
        return [(p.op, p.pattern) for p in self.picks]


Cands = dict[str, tuple[list[int], list[int], int]]   # pattern -> (wanted hits, extra hits, rank)
_KIND_RANK = {"exact": 0, "prefix": 0, "suffix": 1, "infix": 2}


def _greedy(want: set[int], cands: Cands, *, max_extra: int) -> list[tuple[str, list[int], list[int]]]:
    """Lazy greedy set cover of ``want``: most new wanted hits first, then fewer extra hits, natural patterns,
    shorter patterns; a candidate may hit at most ``max_extra`` unwanted names."""
    left = set(want)
    heap: list[tuple] = []
    for pat, (w, x, rank) in cands.items():
        if len(x) <= max_extra and w:
            heap.append((-len(w), len(x), rank, len(pat), pat))
    heapq.heapify(heap)
    out: list[tuple[str, list[int], list[int]]] = []
    while left and heap:
        neg, nx, rank, ln, pat = heapq.heappop(heap)
        w, x, _rank = cands[pat]
        g = sum(1 for i in w if i in left)
        if g < 1:
            continue
        if -g != neg:
            heapq.heappush(heap, (-g, nx, rank, ln, pat))
            continue
        out.append((pat, w, x))
        left.difference_update(w)
    return out


def _gather(cand_keys: dict[tuple[str, str], bool], want: NameSet, universe: NameSet, *, min_hits: int,
            extra_cap: int) -> Cands:
    """``pattern -> (indices into want, indices into universe outside want, rank)`` for useful candidates."""
    out: Cands = {}
    wanted_names = set(want.names)
    for (kind, text), nat in sorted(cand_keys.items()):
        w = want.lookup(kind, text)
        if len(w) < min_hits:
            continue
        if kind == "prefix":
            r = universe.prefix(text)
            if len(r) - len(w) > extra_cap:
                continue
            u: list[int] | range = r
        else:
            u = universe.lookup(kind, text)
            if len(u) - len(w) > extra_cap:
                continue
        x = [i for i in u if universe.names[i] not in wanted_names] if len(u) > len(w) else []
        out[_pattern(kind, text)] = (w, x, _KIND_RANK[kind] + (0 if nat else 3))
    return out


def _sample(idx: list[int], ns: NameSet) -> list[str]:
    return [ns.names[i] for i in idx[:3]]


def cover(wanted: Iterable[str], universe: Iterable[str], *, max_extra: int = 0, infix: bool | None = None) -> Plan:
    """A short pattern list for ``wanted`` (lower-case names) within ``universe``.

    ``max_extra`` = 0: an exact plan, the best of (a) apply patterns that match nothing outside the wanted set and
    (b) broader apply patterns plus remove patterns that cancel their extra names; fewer patterns win, ties
    prefer (a). ``max_extra`` > 0: apply patterns only, each may match up to that many unwanted names.
    Every plan is verified with :func:`resolve`; a failure falls back to exact names.
    """
    uni = NameSet(set(universe) | set(wanted))
    want = NameSet(wanted)
    if not want.names:
        return Plan([], True, 0)
    use_infix = len(want) <= 3000 if infix is None else infix
    keys = _candidates(want.names, infix=use_infix)
    all_w = set(range(len(want)))

    def finish(chosen: list[tuple[str, list[int], list[int]]]) -> list[Pick]:
        picks = [Pick("apply", pat, len(w), len(x), _sample(x, uni)) for pat, w, x in chosen]
        done = set().union(*(set(w) for _p, w, _x in chosen)) if chosen else set()
        for i in sorted(all_w - done):
            picks.append(Pick("apply", want.names[i], 1))
        return picks

    if max_extra > 0:
        cands = _gather(keys, want, uni, min_hits=2, extra_cap=max_extra)
        return _verified(finish(_greedy(all_w, cands, max_extra=max_extra)), want, uni, exact=False)

    every = _gather(keys, want, uni, min_hits=2, extra_cap=200)
    plan_a = finish(_greedy(all_w, every, max_extra=0))
    best = plan_a
    chosen = _greedy_subtractive(all_w, every, want, uni)
    if any(x for _p, _w, x in chosen):
        applies = finish(chosen)
        extra_idx = sorted(set().union(*(set(x) for _p, _w, x in chosen)))
        removes = _removes(NameSet(uni.names[i] for i in extra_idx), want)
        if removes is not None and len(applies) + len(removes) < len(plan_a):
            best = applies + removes
    return _verified(best, want, uni, exact=True, fallback=plan_a)


def _greedy_subtractive(want: set[int], cands: Cands, wset: NameSet,
                        uni: NameSet) -> list[tuple[str, list[int], list[int]]]:
    """Greedy cover where a pattern costs 1 plus the remove patterns its new extra names need; ratio =
    new wanted hits per pattern spent, taken only while it beats exact names (ratio > 1).

    Lazy evaluation: entries are re-scored when popped and taken when their score is current. Remove counts are
    cached per set of new extra names.
    """
    left = set(want)
    hit_extra: set[int] = set()
    cost_cache: dict[frozenset, float] = {}

    def cost(x: list[int]) -> float:
        new = frozenset(i for i in x if i not in hit_extra)
        if not new:
            return 1.0
        c = cost_cache.get(new)
        if c is None:
            r = _removes(NameSet(uni.names[i] for i in new), wset)
            c = float("inf") if r is None else 1.0 + len(r)
            cost_cache[new] = c
        return c

    heap: list[tuple] = []
    for pat, (w, x, rank) in cands.items():
        if len(w) >= 2:
            heap.append((-(len(w) / (2.0 if x else 1.0)), rank, len(pat), pat, False))
    heapq.heapify(heap)
    out: list[tuple[str, list[int], list[int]]] = []
    while left and heap:
        neg, rank, ln, pat, current = heapq.heappop(heap)
        w, x, _rank = cands[pat]
        gain = sum(1 for i in w if i in left)
        if gain < 2:
            continue
        ratio = gain / cost(x)
        if current and -neg == ratio:
            out.append((pat, w, x))
            left.difference_update(w)
            hit_extra.update(x)
            continue
        if ratio > 1.0:   # an exact name covers one name per pattern
            heapq.heappush(heap, (-ratio, rank, ln, pat, True))
    return out


def _removes(extras: NameSet, want: NameSet) -> list[Pick] | None:
    """Remove patterns that cancel every name of ``extras`` and match no wanted name."""
    keys = _candidates(extras.names, infix=len(extras) <= 3000)
    cands: Cands = {}
    for (kind, text), nat in sorted(keys.items()):
        if want.count(kind, text):
            continue
        hits = extras.lookup(kind, text)
        if len(hits) >= 2:
            cands[_pattern(kind, text)] = (hits, [], _KIND_RANK[kind] + (0 if nat else 3))
    chosen = _greedy(set(range(len(extras))), cands, max_extra=0)
    picks = [Pick("remove", pat, len(w)) for pat, w, _x in chosen]
    done = set().union(*(set(w) for _p, w, _x in chosen)) if chosen else set()
    for i in range(len(extras)):
        if i not in done:
            if extras.names[i] in want.index:
                return None
            picks.append(Pick("remove", extras.names[i], 1))
    return picks


def _verified(picks: list[Pick], want: NameSet, uni: NameSet, *, exact: bool,
              fallback: list[Pick] | None = None) -> Plan:
    ops = [(p.op, p.pattern) for p in picks]
    got = resolve(ops, uni.names)
    target = set(want.names)
    ok = target <= got and (not exact or got == target)
    if not ok:
        fb_ok = fallback is not None and resolve([(p.op, p.pattern) for p in fallback], uni.names) == target
        picks = fallback if fb_ok and fallback is not None else [Pick("apply", n, 1) for n in want.names]
        got = resolve([(p.op, p.pattern) for p in picks], uni.names)
    return Plan(picks, exact=(got == target), extra=len(got - target))
