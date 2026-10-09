"""SecuROM key set of the stock gta_sa.exe (1.0 US, HOODLUM-protected).

SecuROM hides constants behind an obfuscated computation: the code reads a word from a table in
``.HOODLUM`` (0x1557000..0x1562000) and combines it with a 4-byte *key* read from another place of the
image (``op reg, [KEY]``). Overwriting a key with a patch silently changes the constant that code
computes. ``engine sites-check`` therefore refuses sites that overlap a key window (4 bytes).

The search is the logic of our research script ``keyrisk2.py``, but on the exe bytes with our own
length decoder (:mod:`.x86`) instead of a ``dumpbin`` listing:

* sweep ``.text`` and the relocated bodies of ``.HOODLUM`` linearly;
* an instruction with an absolute ``ds:[addr]`` operand inside the table is a *table read*;
* the key is the first other absolute operand within the next 4 instructions (stopping at
  ``popfd``, ``xchg``, ``ret``, ``jmp``, ``call``), else within the 2 instructions before it
  (stopping additionally at ``pushfd``).

The stock exe must give exactly 256 distinct key addresses. The result is cached in
``work/re/securom_keys.json`` (content-addressed by the exe sha256 and the algorithm version).
Stdlib only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import atomic_write, jpath
from ..re.pe import PeImage
from .x86 import abs_operand, decode, is_stop

__all__ = [
    "STOCK_EXE_SHA256",
    "EXPECTED_KEY_COUNT",
    "KEY_WINDOW",
    "TABLE_RANGE",
    "HOODLUM_BODY_START",
    "ALGORITHM",
    "KeyScan",
    "find_keys",
    "load_keys",
    "cache_path",
]

STOCK_EXE_SHA256 = "a559aa772fd136379155efa71f00c47aad34bbfeae6196b0fe1047d0645cbd26"
EXPECTED_KEY_COUNT = 256
KEY_WINDOW = 4
#: Table the obfuscated computations read (inside ``.HOODLUM``).
TABLE_RANGE = (0x1557000, 0x1562000)
#: First relocated function body in ``.HOODLUM``; below it are strings and the tables themselves.
HOODLUM_BODY_START = 0x1560930
#: Bumped when the search logic or the decoder changes (invalidates the cache).
ALGORITHM = 1
_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class KeyScan:
    keys: tuple[int, ...]  # sorted distinct key addresses
    reads: int  # table reads found
    unresolved: int  # table reads with no key in reach

    def windows(self) -> list[tuple[int, int]]:
        return [(k, k + KEY_WINDOW) for k in self.keys]


def _decode_section(image: PeImage, lo: int, hi: int):
    """``(buf, [(va, pos, insn)])`` of a linear sweep of ``[lo, hi)`` inside one section."""
    sec = image.section_at(lo)
    if sec is None:
        return b"", []
    buf = image.raw(sec)
    pos = lo - sec.va
    stop = min(hi - sec.va, len(buf))
    out = []
    while pos < stop:
        ins = decode(buf, pos)
        out.append((sec.va + pos, pos, ins))
        pos += ins.length
    return buf, out


def _key_reads(buf: bytes, ins: list, table: tuple[int, int], first_va: int) -> list[tuple[int | None, int]]:
    res: list[tuple[int | None, int]] = []
    tlo, thi = table
    n = len(ins)
    for j, (va, pos, i) in enumerate(ins):
        if va < first_va:
            continue
        v = abs_operand(buf, pos, i)
        if v is None or not tlo <= v < thi:
            continue
        found = None
        for jj in range(j + 1, min(j + 5, n)):
            _, p2, i2 = ins[jj]
            if is_stop(buf, p2, i2):
                break
            v2 = abs_operand(buf, p2, i2)
            if v2 is not None and not tlo <= v2 < thi:
                found = v2
                break
        if found is None:
            for jj in range(j - 1, max(j - 3, -1), -1):
                _, p2, i2 = ins[jj]
                if is_stop(buf, p2, i2) or i2.opcode == 0x9C:  # pushfd
                    break
                v2 = abs_operand(buf, p2, i2)
                if v2 is not None and not tlo <= v2 < thi:
                    found = v2
                    break
        res.append((found, va))
    return res


def find_keys(image: PeImage, *, text: tuple[int, int] | None = None, hood: tuple[int, int] | None = None,
              table: tuple[int, int] = TABLE_RANGE, hood_body_start: int = HOODLUM_BODY_START) -> KeyScan:
    """Search the key addresses in ``image`` (defaults: the sections of the stock exe)."""
    if text is None:
        s = image.section(".text", 0)
        text = (s.va, s.va + min(s.vsize, s.raw_size)) if s else (0, 0)
    if hood is None:
        s = image.section(".HOODLUM")
        hood = (s.va, s.va + min(s.vsize, s.raw_size)) if s else (0, 0)
    reads: list[tuple[int | None, int]] = []
    for (lo, hi), first in ((text, 0), (hood, hood_body_start)):
        if hi <= lo:
            continue
        buf, ins = _decode_section(image, lo, hi)
        reads.extend(_key_reads(buf, ins, table, first))
    keys = sorted({k for k, _ in reads if k is not None})
    return KeyScan(tuple(keys), len(reads), sum(1 for k, _ in reads if k is None))


def cache_path() -> Path:
    from ..core.paths import cfg

    return Path(cfg().paths.work) / "re" / "securom_keys.json"


def load_keys(image: PeImage, cache: Path | None = None, *, refresh: bool = False) -> KeyScan:
    """The key set for ``image``: from the cache when it matches the exe hash, else computed and cached.

    For the stock exe the count is asserted (256); another image is accepted with a smaller or larger
    set (fixtures, patched copies) and never cached under the stock name.
    """
    sha = image.sha256
    stock = sha == STOCK_EXE_SHA256
    path = cache if cache is not None else (cache_path() if stock else None)
    if path is not None and not refresh:
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8"))
            if d.get("schema") == _SCHEMA and d.get("algorithm") == ALGORITHM and d.get("exe_sha256") == sha:
                keys = tuple(int(x, 16) for x in d["keys"])
                scan = KeyScan(keys, int(d.get("reads", 0)), int(d.get("unresolved", 0)))
                _assert_count(scan, stock)
                return scan
        except (OSError, ValueError, KeyError, TypeError):
            pass
    scan = find_keys(image)
    _assert_count(scan, stock)
    if path is not None:
        doc = {"schema": _SCHEMA, "algorithm": ALGORITHM, "exe_sha256": sha, "key_window": KEY_WINDOW,
               "reads": scan.reads, "unresolved": scan.unresolved, "count": len(scan.keys),
               "keys": [f"0x{k:X}" for k in scan.keys]}
        atomic_write(Path(path), json.dumps(doc, indent=1) + "\n")
    return scan


def _assert_count(scan: KeyScan, stock: bool) -> None:
    if stock and len(scan.keys) != EXPECTED_KEY_COUNT:
        raise SatkError("CHECK_FAILED", f"SecuROM key search found {len(scan.keys)} keys, expected "
                        f"{EXPECTED_KEY_COUNT}", hint="delete work/re/securom_keys.json and rerun; "
                        "if it persists the decoder (satk/engine/x86.py) regressed",
                        data={"cache": jpath(cache_path())})
