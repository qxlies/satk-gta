"""Heuristic stack walk for dumps without unwind data (``gta_sa.exe`` has no PDB and no frame pointers).

Frames, innermost first:

1. ``ip`` — the instruction pointer of the context;
2. ``ebp`` — the frame-pointer chain (x86 only; ``[ebp]`` = caller's ebp, ``[ebp+4]`` = return address),
   followed while it stays inside the captured stack and grows upwards;
3. ``scan`` — every pointer-sized stack slot from ``esp`` upwards whose value points into a module's
   executable code and is preceded by a CALL instruction (:func:`satk.crash.images.call_before`).
   When the code bytes are unknown (no captured memory, no matching module file) the value is kept
   with ``call: None`` if the module section is executable or unknown.

Stale return addresses of finished calls also pass the CALL test: this is a heuristic, read the
frames as "probably on the stack", like ``dps esp`` in cdb. Duplicates keep their first slot.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable

from .images import Images, call_before
from .minidump import Context, Minidump, Thread

__all__ = ["Frame", "walk"]


@dataclass(slots=True)
class Frame:
    va: int
    how: str                    # ip | ebp | scan
    slot: int | None = None     # stack address of the return address
    call: bool | None = None    # a CALL precedes it (None: code bytes unknown)
    target: int | None = None   # direct call target (E8), if any


def _plausible(images: Images, va: int, is_code: Callable[[int], bool | None] | None, ptr: int) -> tuple[bool, bool | None, int | None]:
    """``(keep, call, target)`` for a candidate return address."""
    mod = images.module_at(va)
    if mod is None or va - mod.base < 0x1000:
        return False, None, None
    ex = images.executable(va)
    if ex is None and is_code is not None:
        ex = is_code(va)
    if ex is False:
        return False, None, None
    code = None
    for n in (7, 6, 5, 3, 2):            # captured ranges may start just before the call
        code = images.code(va - n, n)
        if code is not None:
            break
    if code is None:
        return True, None, None
    cb = call_before(code, ptr)
    if cb is None:
        return False, False, None
    length, rel = cb
    return True, True, (va + rel) & ((1 << (8 * ptr)) - 1) if rel is not None else None


def walk(dump: Minidump, ctx: Context, thread: Thread | None, images: Images, *, scan_bytes: int = 0x10000,
         max_frames: int = 256, is_code: Callable[[int], bool | None] | None = None) -> tuple[list[Frame], dict]:
    """Frames of one thread and walk statistics ``{stack_bytes, scanned, candidates}``."""
    ptr = 8 if ctx.arch == "amd64" else 4
    fmt = "<Q" if ptr == 8 else "<I"
    frames = [Frame(ctx.ip, "ip")]
    stats = {"stack_bytes": 0, "scanned": 0, "candidates": 0}
    if thread is None:
        return frames, stats
    lo, data = dump.stack_bytes(thread, ctx.sp)
    stats["stack_bytes"] = len(data)
    if not data:
        return frames, stats
    skew = (-lo) % ptr
    data = data[:skew + scan_bytes]
    hi = lo + len(data)

    def word(va: int) -> int:
        return struct.unpack_from(fmt, data, va - lo)[0]

    found: dict[int, Frame] = {}
    if ptr == 4:
        fp, seen = ctx.fp, set()
        for _ in range(512):
            if not (lo <= fp and fp + 2 * ptr <= hi) or fp in seen or fp % ptr:
                break
            seen.add(fp)
            nxt, ret = word(fp), word(fp + ptr)
            keep, call, target = _plausible(images, ret, is_code, ptr)
            if keep:
                found[fp + ptr] = Frame(ret, "ebp", fp + ptr, call, target)
            if nxt <= fp:
                break
            fp = nxt
    n = 0
    for off in range(skew, len(data) - ptr + 1, ptr):
        slot = lo + off
        n += 1
        if slot in found:
            continue
        v = struct.unpack_from(fmt, data, off)[0]
        if v < 0x10000:
            continue
        keep, call, target = _plausible(images, v, is_code, ptr)
        if keep:
            stats["candidates"] += 1
            found[slot] = Frame(v, "scan", slot, call, target)
    stats["scanned"] = n
    seen_va = {ctx.ip}
    for slot in sorted(found):
        f = found[slot]
        if f.va in seen_va:
            continue
        seen_va.add(f.va)
        frames.append(f)
        if len(frames) >= max_frames:
            break
    return frames, stats
