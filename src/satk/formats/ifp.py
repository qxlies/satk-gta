"""Animation packages (``*.ifp``: ANP3/ANP2/ANPK), SPEC §4.2 ``satk.formats.ifp``. Stdlib only. F2.

* **ANP3** (SA): ``"ANP3", u32 size, char pack[24], u32 anims``; per animation ``char name[24], u32 bones,
  u32 frame_data_size, u32 flags``; per bone ``char name[24], u32 frame_type, u32 frames, i32 bone_tag``
  followed by ``frames`` key frames of 20/32/10/16 bytes (types 1..4: rot / rot+trans, float / int16).
* **ANP2**: the same without ``frame_data_size, flags`` (returned as format ``"ANP2"``).
* **ANPK** (III/VC style, the 148 files of ``cuts.img``): nested sections ``fourcc + u32 size`` padded
  to 4: ``ANPK > INFO(u32 n, pack name) > n x (NAME, DGAN > INFO(u32 bones) > bones x (CPAN > ANIM(name[28],
  u32 frames, ...), KR00|KRT0|KRTS key frames))`` — read sequentially like ``CAnimManager::LoadAnimFile``.

``frames`` of an animation = the largest key-frame count of its bones.

Example::

    fmt, pack, anims = parse_ifp(blob)       # ("ANP3", "ped", [("walk_civi", 32, 23), ...])
"""

from __future__ import annotations

import struct

from .rw import FormatError

__all__ = ["parse_ifp", "KEYFRAME_SIZES"]

#: ANP2/ANP3 frame type -> bytes per key frame.
KEYFRAME_SIZES = {1: 20, 2: 32, 3: 10, 4: 16}
#: ANPK key frame section -> bytes per key frame (quaternion + time [+ translation [+ scale]]).
_ANPK_KF = {b"KR00": 20, b"KRT0": 32, b"KRTS": 44}


def _need(cond: bool, off: int, msg: str) -> None:
    if not cond:
        raise FormatError("ifp", off, msg)


def _name(buf, a: int, n: int) -> str:
    return bytes(buf[a:a + n]).split(b"\0", 1)[0].decode("latin-1")


def _anp23(buf, anp3: bool) -> tuple[str, str, list[tuple[str, int, int]]]:
    n = len(buf)
    _need(n >= 36, 0, "truncated ANP header")
    pack = _name(buf, 8, 24)
    (count,) = struct.unpack_from("<I", buf, 32)
    p = 36
    out = []
    for i in range(count):
        hdr = 36 if anp3 else 28
        _need(p + hdr <= n, p, f"animation {i} header overruns the file")
        name = _name(buf, p, 24)
        (bones,) = struct.unpack_from("<I", buf, p + 24)
        p += hdr
        frames = 0
        for b in range(bones):
            _need(p + 36 <= n, p, f"animation {name!r} bone {b} header overruns the file")
            ftype, nfr = struct.unpack_from("<II", buf, p + 24)
            kf = KEYFRAME_SIZES.get(ftype)
            _need(kf is not None, p + 24, f"animation {name!r} bone {b}: unknown frame type {ftype}")
            p += 36 + kf * nfr
            _need(p <= n, p, f"animation {name!r} bone {b}: {nfr} key frames overrun the file")
            frames = max(frames, nfr)
        out.append((name, bones, frames))
    return ("ANP3" if anp3 else "ANP2"), pack, out


def _section(buf, p: int, want: tuple[bytes, ...] | None = None) -> tuple[bytes, int, int]:
    """``(fourcc, data offset, padded size)`` of the section at ``p``."""
    _need(p + 8 <= len(buf), p, "truncated ANPK section header")
    fc = bytes(buf[p:p + 4])
    (size,) = struct.unpack_from("<I", buf, p + 4)
    if want is not None:
        _need(fc in want, p, f"expected {b'|'.join(want).decode()} section, got {fc!r}")
    size += -size % 4
    _need(p + 8 + size <= len(buf), p, f"section {fc!r} of {size} bytes overruns the file")
    return fc, p + 8, size


def _anpk(buf) -> tuple[str, str, list[tuple[str, int, int]]]:
    _fc, p, _size = _section(buf, 0, (b"ANPK",))          # the outer size is not used (as in the engine)
    _fc, d, size = _section(buf, p, (b"INFO",))
    _need(size >= 4, d, "ANPK INFO shorter than 4 bytes")
    (count,) = struct.unpack_from("<I", buf, d)
    pack = _name(buf, d + 4, size - 4)
    p = d + size
    out = []
    for i in range(count):
        _fc, d, size = _section(buf, p, (b"NAME",))
        name = _name(buf, d, size)
        _fc, p, _size = _section(buf, d + size, (b"DGAN",))     # enter DGAN, read its children sequentially
        _fc, d, size = _section(buf, p, (b"INFO",))
        _need(size >= 4, d, f"animation {name!r}: INFO shorter than 4 bytes")
        (bones,) = struct.unpack_from("<I", buf, d)
        p = d + size
        frames = 0
        for b in range(bones):
            _fc, p, _size = _section(buf, p, (b"CPAN",))
            _fc, d, size = _section(buf, p, (b"ANIM",))
            _need(size >= 32, d, f"animation {name!r} bone {b}: ANIM shorter than 32 bytes")
            (nfr,) = struct.unpack_from("<I", buf, d + 28)
            p = d + size
            if nfr:
                fc, d, size = _section(buf, p, tuple(_ANPK_KF))
                _need(nfr * _ANPK_KF[fc] <= size, d, f"animation {name!r} bone {b}: {nfr} key frames overrun {fc!r}")
                p = d + size
            frames = max(frames, nfr)
        out.append((name, bones, frames))
    return "ANPK", pack, out


def parse_ifp(buf) -> tuple[str, str, list[tuple[str, int, int]]]:
    """``(format "ANP3"|"ANPK"|"ANP2", pack name, [(anim name, bones, frames)])``; ``FormatError`` if broken."""
    head = bytes(buf[:4])
    try:
        if head == b"ANP3":
            return _anp23(buf, True)
        if head == b"ANP2":
            return _anp23(buf, False)
        if head == b"ANPK":
            return _anpk(buf)
    except (struct.error, IndexError, OverflowError) as e:   # defensive net; bounds are checked above
        raise FormatError("ifp", 0, f"malformed IFP: {e}") from None
    raise FormatError("ifp", 0, f"not an IFP file (magic {head!r})")
