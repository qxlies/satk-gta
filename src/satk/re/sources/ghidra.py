"""Optional read-only import of exported Ghidra function bounds; no Ghidra runtime required.

functions.jsonl uses inclusive endpoints, including each pair in ``ranges``. We preserve
discontiguous bodies as separate half-open intervals, and verify moved-body owners against
the executable. An adjacent summary.json, when present, must name the same executable hash.
Names still come from the source scanners; imported default labels never replace them.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ...core.errors import SatkError
from ..pe import PeImage
from .exe import classify_thunk


@dataclass
class GhidraBounds:
    ends: dict[int, int]
    # (range start, exclusive end, canonical function entry, physical body entry)
    ranges: list[tuple[int, int, int, int]]
    sha256: str
    _starts: list[int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._starts = [r[0] for r in self.ranges]

    def containing(self, address: int) -> tuple[int, int, int, int] | None:
        i = bisect.bisect_right(self._starts, address) - 1
        return self.ranges[i] if i >= 0 and address < self.ranges[i][1] else None


def _addr(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("address must be an integer or hex string")
    result = int(value, 16) if isinstance(value, str) else value
    if not 0 <= result < (1 << 32):
        raise ValueError("address is outside the 32-bit image")
    return result


def load_functions(path: Path, image: PeImage) -> GhidraBounds:
    """Validate the complete export before the builder writes its temporary database."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SatkError("NOT_FOUND", f"cannot read Ghidra functions: {path}: {exc}") from None
    summary_path = path.with_name("summary.json")
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
            digest = summary.get("executable_sha256")
        except (ValueError, AttributeError, OSError) as exc:
            raise SatkError("BAD_PARAMS", f"invalid Ghidra summary: {summary_path}: {exc}") from None
        if not isinstance(digest, str) or digest.lower() != image.sha256:
            raise SatkError("BAD_PARAMS", "Ghidra executable SHA-256 does not match the build's executable")
    ends: dict[int, int] = {}
    ranges = []
    line = 0
    try:
        for line, text in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
            if not text.strip():
                continue
            row = json.loads(text)
            entry = _addr(row["entry"])
            canonical = row.get("canonical_entry")
            owner = entry if canonical is None else _addr(canonical)
            if entry in ends:
                raise ValueError(f"duplicate entry {entry:#x}")
            if owner != entry:
                thunk = classify_thunk(image, owner)
                if thunk is None or thunk[1] != entry:
                    raise ValueError(f"canonical entry {owner:#x} does not jump to body {entry:#x}")
            pairs = row.get("ranges")
            if pairs is None:
                pairs = [[row["entry"], row["end"]]]
            if not isinstance(pairs, list) or not pairs:
                raise ValueError("ranges must be a nonempty list")
            body_ranges = []
            for pair in pairs:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("each range must be a two-item list")
                lo, hi = (_addr(value) for value in pair)
                section = image.section_at(lo)
                if hi < lo or section is None or not section.executable or image.section_at(hi) is not section:
                    raise ValueError(f"invalid executable range {lo:#x}..{hi:#x}")
                body_ranges.append((lo, hi + 1, owner, entry))
            if not any(lo <= entry < hi for lo, hi, _o, _b in body_ranges):
                raise ValueError(f"entry {entry:#x} is not in its ranges")
            ends[entry] = max(hi for _lo, hi, _o, _b in body_ranges)
            ranges.extend(body_ranges)
        if not ends:
            raise ValueError("no functions")
        ranges.sort()
        for left, right in zip(ranges, ranges[1:]):
            if right[0] < left[1]:
                raise ValueError(f"overlapping function ranges at {right[0]:#x}")
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise SatkError("BAD_PARAMS", f"invalid Ghidra functions {path}:{line}: {exc}") from None
    return GhidraBounds(ends, ranges, hashlib.sha256(raw).hexdigest())
