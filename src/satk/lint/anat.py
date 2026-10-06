"""Frames of a DFF with model-space positions (``satk.lint.anat``). Stdlib only.

The vehicle, ped and weapon rules need frame names, parents and positions (``ped_arm`` must sit on the left,
``wheel_*_dummy`` must exist, ``gunflash`` must be an atomic). :func:`read_frames` walks the clump headers
only (no geometry decode): ``Clump -> FrameList -> Struct (n x 56 bytes) + n Extension (FrameName)``.
Frame indices are global across clumps, like :class:`satk.formats.dff.Frame`.

Model space: a frame's matrix is relative to its parent (columns right/up/at, then the position), so the
model-space position is the parent's matrix applied to the local position (the rule of
``RwFrameGetLTM`` with an identity root).

Example::

    for f in read_frames(blob):
        print(f.idx, f.parent, f.name, f.pos)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

__all__ = ["FrameRec", "read_frames", "side_of"]

_HDR = struct.Struct("<III")
_FRAME = struct.Struct("<12fiI")
_CLUMP, _FRAMELIST, _STRUCT, _EXT, _FRAMENAME = 0x10, 0x0E, 0x01, 0x03, 0x253F2FE
_IDENT = ((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0))


@dataclass(frozen=True, slots=True)
class FrameRec:
    """One frame: global index, global parent (-1 = root), lower-case name, model-space position."""

    idx: int
    parent: int
    name: str
    pos: tuple[float, float, float]
    local: tuple[float, float, float]


def _kids(buf: bytes, start: int, end: int):
    off = start
    while off + 12 <= end:
        t, s, _v = _HDR.unpack_from(buf, off)
        e = off + 12 + s
        if e > end:
            return
        yield t, off + 12, e
        off = e


def _names(buf: bytes, start: int, end: int, n: int) -> list[str]:
    out: list[str] = []
    for t, a, e in _kids(buf, start, end):
        if t != _EXT:
            continue
        name = ""
        for t2, a2, e2 in _kids(buf, a, e):
            if t2 == _FRAMENAME:
                name = bytes(buf[a2:e2]).split(b"\0", 1)[0].decode("latin-1").strip().lower()
        out.append(name)
    return (out + [""] * n)[:n]


def _compose(parent, rot, pos):
    pr, pp = parent
    r = []
    for c in range(3):
        vx, vy, vz = rot[3 * c], rot[3 * c + 1], rot[3 * c + 2]
        r += [vx * pr[0] + vy * pr[3] + vz * pr[6], vx * pr[1] + vy * pr[4] + vz * pr[7],
              vx * pr[2] + vy * pr[5] + vz * pr[8]]
    x, y, z = pos
    t = (x * pr[0] + y * pr[3] + z * pr[6] + pp[0], x * pr[1] + y * pr[4] + z * pr[7] + pp[1],
         x * pr[2] + y * pr[5] + z * pr[8] + pp[2])
    return tuple(r), t


def read_frames(data: bytes) -> list[FrameRec]:
    """Frames of every clump of a DFF (empty list when there is no readable frame list)."""
    buf = bytes(data)
    out: list[FrameRec] = []
    try:
        for t, a, e in _kids(buf, 0, len(buf)):
            if t != _CLUMP:
                continue
            for t2, a2, e2 in _kids(buf, a, e):
                if t2 != _FRAMELIST:
                    continue
                kids = list(_kids(buf, a2, e2))
                if not kids or kids[0][0] != _STRUCT:
                    break
                _t, sa, se = kids[0]
                (n,) = struct.unpack_from("<i", buf, sa)
                if n < 0 or sa + 4 + 56 * n > se:
                    break
                names = _names(buf, se, e2, n)
                base = len(out)
                mats: list = []
                for i in range(n):
                    v = _FRAME.unpack_from(buf, sa + 4 + 56 * i)
                    rot, pos, parent = v[:9], v[9:12], v[12]
                    m = _compose(mats[parent], rot, pos) if 0 <= parent < i else (tuple(rot), tuple(pos))
                    mats.append(m)
                    out.append(FrameRec(base + i, base + parent if 0 <= parent < n else -1, names[i],
                                        tuple(round(x, 4) for x in m[1]), tuple(round(x, 4) for x in pos)))
                break
    except (struct.error, IndexError, ValueError):
        return out
    return out


def side_of(name: str) -> int:
    """Expected side of a frame by its name: -1 left (x < 0), +1 right (x > 0), 0 unknown/centre.

    ``wheel_lf_dummy``/``door_lr_dummy`` -> left, ``wheel_rb_dummy`` -> right; ``ped_arm`` is the driver's
    arm (left in every vanilla car)."""
    n = name.lower()
    if n == "ped_arm":
        return -1
    for tok in n.replace("-", "_").split("_"):
        if tok in ("lf", "lb", "lr", "lm", "l", "left"):
            return -1
        if tok in ("rf", "rb", "rr", "rm", "r", "right"):
            return 1
    return 0
