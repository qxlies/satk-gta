"""The timeline key ``(t_us, tick, sub, frame)`` and the grid arithmetic behind it.

``t_us`` is session time in microseconds in the server clock domain (the local clock when not connected). On the tick
grid ``dt_s_us`` (an integer number of microseconds, from the stream header) the server tick is ``T = t_us // dt_s_us``
and the client simulation tick (``sub``, the ``k`` of the architecture) is ``k = t_us * n // dt_s_us`` where ``n`` is
the number of client sub-ticks per server tick. ``NONE`` (0xFFFFFFFF) stands for 'not applicable' in ``tick``, ``sub``
and ``frame``.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = ["NONE", "FIELDS", "tick_of", "sub_of", "tick_time_us", "make", "of", "sort_key", "fmt", "is_none"]

NONE = 0xFFFFFFFF
FIELDS = ("t_us", "tick", "sub", "frame")


def tick_of(t_us: int, dt_s_us: int) -> int:
    """Server tick ``T`` of session time ``t_us`` (``NONE`` without a grid)."""
    return t_us // dt_s_us if dt_s_us > 0 else NONE


def sub_of(t_us: int, dt_s_us: int, sub_n: int) -> int:
    """Client simulation tick ``k`` at ``t_us`` (``NONE`` without a grid)."""
    return (t_us * sub_n) // dt_s_us if dt_s_us > 0 and sub_n > 0 else NONE


def tick_time_us(k: int, dt_s_us: int, sub_n: int) -> int:
    """First microsecond of client tick ``k``: the smallest ``t`` with ``sub_of(t) == k`` (a tick record's ``t_us``)."""
    return -((-k * dt_s_us) // sub_n)


def make(t_us: int, *, dt_s_us: int = 0, sub_n: int = 0, frame: int = NONE, fixed: bool = False,
         tsrc: str | None = None) -> dict[str, Any]:
    """A key for ``t_us`` on the grid ``dt_s_us`` (``sub`` only in fixed mode)."""
    key: dict[str, Any] = {"t_us": int(t_us), "tick": tick_of(int(t_us), dt_s_us),
                           "sub": sub_of(int(t_us), dt_s_us, sub_n) if fixed else NONE, "frame": int(frame)}
    if tsrc:
        key["tsrc"] = tsrc
    return key


def of(rec: Mapping[str, Any]) -> dict[str, Any]:
    """The key members of a record."""
    return {k: rec[k] for k in (*FIELDS, "tsrc") if k in rec}


def sort_key(rec: Mapping[str, Any]) -> tuple[int, int, int, int]:
    """Total order of keys for merging: time first, then tick, sub, frame (``NONE`` last)."""
    return (int(rec.get("t_us", 0)), int(rec.get("tick", NONE)), int(rec.get("sub", NONE)), int(rec.get("frame", NONE)))


def is_none(v: int) -> bool:
    return v == NONE


def fmt(rec: Mapping[str, Any]) -> str:
    """``t=1.234567s T=37 k=- f=120`` (``-`` for NONE)."""
    def part(v: Any) -> str:
        return "-" if v is None or v == NONE else str(v)
    t = rec.get("t_us")
    ts = f"{t / 1e6:.6f}s" if isinstance(t, (int, float)) else "-"
    return f"t={ts} T={part(rec.get('tick'))} k={part(rec.get('sub'))} f={part(rec.get('frame'))}"
