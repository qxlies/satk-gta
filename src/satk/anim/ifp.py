"""IFP key frame codec: read and write ANP3 / ANP2 / ANPK animation packages bit-exact. Stdlib only.

Layouts (``satk.formats.ifp`` has the structural summary; this module adds the key frames):

* **ANP3** (SA): ``"ANP3", u32 size (= file end - 8), char pack[24], u32 anims``; per animation
  ``char name[24], u32 sequences, u32 frame_data_size, u32 flags`` (bit 0 = compressed); per sequence (one bone)
  ``char name[24], u32 frame_type, u32 keys, i32 bone_tag`` and the key frames:

  ====  =====================================  ========================================================
  type  bytes  layout                          meaning
  ====  =====================================  ========================================================
  1     20     ``f32 q[4], f32 t``             rotation, uncompressed
  2     32     ``f32 q[4], f32 t, f32 tr[3]``  rotation + translation, uncompressed
  3     10     ``i16 q[4], i16 t``             rotation, compressed: ``q / 4096``, ``t / 60`` s
  4     16     ``i16 q[4], i16 t, i16 tr[3]``  + translation ``tr / 1024`` m
  ====  =====================================  ========================================================

  ``q`` is ``(x, y, z, w)``, the engine's ``CQuaternion``; ``t`` is the absolute time of the key in seconds
  (``CAnimBlendHierarchy::CalcTotalTime`` turns it into deltas after loading).
* **ANP2**: ANP3 without ``frame_data_size``/``flags``.
* **ANPK** (III/VC style, the cutscene files of ``cuts.img``): ``ANPK > INFO(u32 n, pack) > n x (NAME,
  DGAN > INFO(u32 seqs, 4 bytes) > seqs x CPAN > ANIM(name[28], u32 keys, u32 next, u32 prev, i32 tag),
  KR00|KRT0|KRTS)``; sections are ``fourcc, u32 size`` padded to 4 bytes with zeros. Key frames are floats
  ``q[4], [tr[3], [scale[3]]], t``; the stored quaternion is the conjugate of the engine's (the loader negates
  ``x, y, z``), so this module keeps the engine's convention in memory for every format.

In memory a :class:`Seq` keeps its key frames **as stored** (``int`` for compressed, ``float`` otherwise) in
the order ``(qx, qy, qz, qw, t[, tx, ty, tz[, sx, sy, sz]])``; :meth:`Seq.frames` gives floats in seconds,
unit quaternions and metres. Byte details that do not follow from the values (garbage after a name's NUL, ANPK
link words, padding inside sections) are kept in ``raw_*``/``extra`` fields, so :func:`write_ifp` reproduces
every vanilla IFP byte for byte. Sizes (``size``, ``frame_data_size``, section sizes) are recomputed; trailing
bytes after the last animation (IMG sector padding) are not part of the IFP.

Example::

    ifp = read_ifp(blob)
    ifp.anims[0].name, [s.name for s in ifp.anims[0].seqs]
    assert write_ifp(ifp) == blob[:ifp.end]
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field, replace

from ..formats.ifp import parse_ifp
from ..formats.rw import FormatError

__all__ = [
    "Q_SCALE", "T_SCALE", "TIME_SCALE", "MAX_NAME", "FORMATS", "Seq", "Anim", "Ifp", "read_ifp", "write_ifp",
    "frame_type", "quantize_seq", "dequantize_seq", "convert_anim", "upper_key",
]

#: Compressed rotation: int16 / 4096.
Q_SCALE = 4096.0
#: Compressed translation: int16 / 1024 (metres).
T_SCALE = 1024.0
#: Compressed time: int16 / 60 (seconds).
TIME_SCALE = 60.0
#: Longest name an ANP2/ANP3 name field holds (24 bytes with the NUL); ANPK bone names hold 27.
MAX_NAME = 23
FORMATS = ("ANP3", "ANP2", "ANPK")

_KF = {1: struct.Struct("<5f"), 2: struct.Struct("<8f"), 3: struct.Struct("<5h"), 4: struct.Struct("<8h")}
_ANPK_KF = {b"KR00": 5, b"KRT0": 8, b"KRTS": 11}       # floats per key frame
_I16 = (-32768, 32767)


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1")


def _field(name: str, size: int) -> bytes:
    b = name.encode("latin-1", "replace")[:size - 1]
    return b + b"\0" * (size - len(b))


def upper_key(name: str) -> str:
    """The engine matches animation and bone names case-insensitively (``CKeyGen::GetUppercaseKey``)."""
    return name.upper()


# --------------------------------------------------------------------------- model


@dataclass(eq=False)
class Seq:
    """One sequence (``CAnimBlendSequence``): the key frames of one bone.

    Attributes:
        name: bone name (used for matching when ``tag`` is -1).
        tag: bone id (``eBoneTag``: 0 Root, 1 Pelvis, ...); -1 = match by name.
        trans: key frames carry a translation.
        compressed: key frames are int16 (ANP3 types 3/4).
        keys: ``(qx, qy, qz, qw, t[, tx, ty, tz[, sx, sy, sz]])`` per key, as stored.
        scale: ANPK ``KRTS`` (a scale vector the engine reads and ignores).
        raw_name: the whole name field when it is not ``name`` + NUL padding (ANPK NAME: the payload).
        extra: ANPK ``ANIM`` bytes after the key count (next, prev, tag) when not ``0, 0, tag``.
        kf_tail: ANPK key frame section bytes after the key frames.
    """

    name: str
    tag: int = -1
    trans: bool = False
    compressed: bool = False
    keys: list = field(default_factory=list)
    scale: bool = False
    raw_name: bytes | None = None
    extra: bytes | None = None
    kf_tail: bytes | None = None

    @property
    def width(self) -> int:
        return 11 if self.scale else (8 if self.trans else 5)

    def frames(self) -> list[tuple]:
        """Key frames as floats: ``(t, (qx, qy, qz, qw), (tx, ty, tz) | None)``."""
        out = []
        if self.compressed:
            for k in self.keys:
                q = (k[0] / Q_SCALE, k[1] / Q_SCALE, k[2] / Q_SCALE, k[3] / Q_SCALE)
                tr = (k[5] / T_SCALE, k[6] / T_SCALE, k[7] / T_SCALE) if self.trans else None
                out.append((k[4] / TIME_SCALE, q, tr))
        else:
            for k in self.keys:
                out.append((float(k[4]), (float(k[0]), float(k[1]), float(k[2]), float(k[3])),
                            (float(k[5]), float(k[6]), float(k[7])) if self.trans else None))
        return out

    def times(self) -> list[float]:
        d = TIME_SCALE if self.compressed else 1.0
        return [k[4] / d for k in self.keys]

    @property
    def end_time(self) -> float:
        if not self.keys:
            return 0.0
        return self.keys[-1][4] / (TIME_SCALE if self.compressed else 1.0)


@dataclass(eq=False)
class Anim:
    """One animation (``CAnimBlendHierarchy``).

    Attributes:
        name: animation name (the game looks it up case-insensitively).
        seqs: sequences in file order.
        flags: ANP3 flags word (bit 0 = compressed); 0 for ANP2/ANPK.
        raw_name: the whole name field when not ``name`` + NUL padding.
        info_extra: ANPK ``DGAN/INFO`` bytes after the sequence count when not 4 zero bytes.
        name_pad: ANPK ``NAME`` padding bytes when not zeros (one vanilla file has them).
    """

    name: str
    seqs: list[Seq] = field(default_factory=list)
    flags: int = 0
    raw_name: bytes | None = None
    info_extra: bytes | None = None
    name_pad: bytes | None = None

    @property
    def compressed(self) -> bool:
        return bool(self.seqs) and all(s.compressed for s in self.seqs if s.keys) and any(s.keys for s in self.seqs)

    @property
    def duration(self) -> float:
        """Length in seconds: the latest last key of any sequence (``CalcTotalTime``)."""
        return max((s.end_time for s in self.seqs), default=0.0)

    @property
    def keys(self) -> int:
        """Largest key count of a sequence (``formats.ifp``'s ``frames``)."""
        return max((len(s.keys) for s in self.seqs), default=0)


@dataclass(eq=False)
class Ifp:
    """An animation package. ``end`` = bytes the IFP occupies in its source (trailing padding excluded)."""

    format: str
    pack: str
    anims: list[Anim] = field(default_factory=list)
    raw_pack: bytes | None = None
    end: int = 0

    def find(self, name: str) -> Anim | None:
        """First animation with this name (case-insensitive, like the engine)."""
        key = upper_key(name)
        for a in self.anims:
            if upper_key(a.name) == key:
                return a
        return None


def frame_type(seq: Seq) -> int:
    """ANP2/ANP3 frame type of a sequence (1..4)."""
    return (3 if seq.compressed else 1) + (1 if seq.trans else 0)


# --------------------------------------------------------------------------- read


def _read_anp23(buf, anp3: bool) -> Ifp:
    raw_pack = bytes(buf[8:32])
    pack = _cstr(raw_pack)
    (count,) = struct.unpack_from("<I", buf, 32)
    p = 36
    anims = []
    for _ in range(count):
        raw = bytes(buf[p:p + 24])
        (nseq,) = struct.unpack_from("<I", buf, p + 24)
        flags = struct.unpack_from("<I", buf, p + 32)[0] if anp3 else 0
        p += 36 if anp3 else 28
        a = Anim(_cstr(raw), flags=flags)
        if raw != _field(a.name, 24):
            a.raw_name = raw
        for _ in range(nseq):
            sraw = bytes(buf[p:p + 24])
            ftype, nk, tag = struct.unpack_from("<IIi", buf, p + 24)
            p += 36
            st = _KF[ftype]
            s = Seq(_cstr(sraw), tag=tag, trans=ftype in (2, 4), compressed=ftype in (3, 4))
            if sraw != _field(s.name, 24):
                s.raw_name = sraw
            n = st.size * nk
            s.keys = list(st.iter_unpack(bytes(buf[p:p + n]))) if nk else []
            p += n
            a.seqs.append(s)
        anims.append(a)
    ifp = Ifp("ANP3" if anp3 else "ANP2", pack, anims, end=p)
    if raw_pack != _field(pack, 24):
        ifp.raw_pack = raw_pack
    return ifp


def _sec(buf, p: int) -> tuple[bytes, int, int, int]:
    """``(fourcc, payload offset, size as stored, padded size)``."""
    fc = bytes(buf[p:p + 4])
    (size,) = struct.unpack_from("<I", buf, p + 4)
    return fc, p + 8, size, size + (-size % 4)


def _read_anpk(buf) -> Ifp:
    _fc, p, _size, _pad = _sec(buf, 0)
    _fc, d, size, pad = _sec(buf, p)
    (count,) = struct.unpack_from("<I", buf, d)
    rawp = bytes(buf[d + 4:d + size])
    ifp = Ifp("ANPK", _cstr(rawp))
    if rawp != ifp.pack.encode("latin-1") + b"\0":
        ifp.raw_pack = rawp
    p = d + pad
    for _ in range(count):
        _fc, d, size, pad = _sec(buf, p)
        raw = bytes(buf[d:d + size])
        a = Anim(_cstr(raw))
        if raw != a.name.encode("latin-1") + b"\0":
            a.raw_name = raw
        padb = bytes(buf[d + size:d + pad])
        if padb.strip(b"\0"):
            a.name_pad = padb
        p = d + pad
        _fc, p, _size, _pad = _sec(buf, p)          # DGAN: enter
        _fc, d, size, pad = _sec(buf, p)            # INFO
        (nseq,) = struct.unpack_from("<I", buf, d)
        rest = bytes(buf[d + 4:d + size])
        if rest != b"\0\0\0\0":
            a.info_extra = rest
        p = d + pad
        for _ in range(nseq):
            _fc, p, _size, _pad = _sec(buf, p)      # CPAN: enter
            _fc, d, size, pad = _sec(buf, p)        # ANIM
            body = bytes(buf[d:d + size])
            (nk,) = struct.unpack_from("<I", body, 28)
            extra = body[32:]
            tag = struct.unpack_from("<i", extra, 8)[0] if len(extra) >= 12 else -1
            s = Seq(_cstr(body[:28]), tag=tag)
            if body[:28] != _field(s.name, 28):
                s.raw_name = body[:28]
            if extra != b"\0" * 8 + struct.pack("<i", tag):
                s.extra = extra
            p = d + pad
            if nk:
                fc, d, size, pad = _sec(buf, p)
                width = _ANPK_KF[fc]
                st = struct.Struct(f"<{width}f")
                n = st.size * nk
                s.trans = width >= 8
                s.scale = width == 11
                keys = []
                for v in st.iter_unpack(bytes(buf[d:d + n])):
                    q = (-v[0], -v[1], -v[2], v[3])            # the engine conjugates on load
                    keys.append(q + (v[-1],) + v[4:-1])        # (q, t, tr, scale)
                s.keys = keys
                if size > n:
                    s.kf_tail = bytes(buf[d + n:d + size])
                p = d + pad
            a.seqs.append(s)
        ifp.anims.append(a)
    ifp.end = p
    return ifp


def read_ifp(buf) -> Ifp:
    """Decode an IFP with all key frames; ``FormatError`` on broken data (checked by ``parse_ifp`` first)."""
    fmt, _pack, _anims = parse_ifp(buf)
    try:
        if fmt == "ANPK":
            return _read_anpk(buf)
        return _read_anp23(buf, fmt == "ANP3")
    except (struct.error, KeyError, IndexError) as e:   # parse_ifp checked the bounds; defensive net
        raise FormatError("ifp", 0, f"malformed IFP key frames: {e}") from None


# --------------------------------------------------------------------------- conversions


def _i16(v: float, scale: float, what: str) -> int:
    x = v * scale
    if x != x or not _I16[0] - 0.5 < x < _I16[1] + 0.5:
        raise ValueError(f"{what} {v:g} is outside the compressed range (+-{_I16[1] / scale:.4g})")
    return int(round(x))


def quantize_seq(seq: Seq) -> Seq:
    """A compressed copy of a float sequence (rotation x4096, time x60, translation x1024).

    Raises ``ValueError`` when a value does not fit int16 (translation beyond +-32 m, time beyond 546 s).
    """
    if seq.compressed:
        return seq
    keys = []
    for k in seq.keys:
        q = tuple(_i16(c, Q_SCALE, "rotation") for c in k[:4])
        t = (_i16(k[4], TIME_SCALE, "time"),)
        tr = tuple(_i16(c, T_SCALE, "translation") for c in k[5:8]) if seq.trans else ()
        keys.append(q + t + tr)
    return replace(seq, compressed=True, keys=keys, scale=False, kf_tail=None)


def dequantize_seq(seq: Seq) -> Seq:
    """A float copy of a compressed sequence (rotation and translation exact in float32; time t/60 rounds)."""
    if not seq.compressed:
        return seq
    keys = [tuple(q) + (t,) + (tuple(tr) if tr is not None else ()) for t, q, tr in seq.frames()]
    return replace(seq, compressed=False, keys=keys)


def _family(fmt: str) -> str:
    return "ANPK" if fmt == "ANPK" else "ANP"


def convert_anim(a: Anim, src_fmt: str, dst_fmt: str, compress: bool | None = None) -> tuple[Anim, list[str]]:
    """``a`` (read from a ``src_fmt`` file) ready to be written as ``dst_fmt``.

    ``compress`` True/False forces compressed/float key frames (ANPK is always float); ``None`` keeps them.
    Byte details of the other format family (raw names, ANPK link words) are dropped. Returns the animation
    (the same object when nothing changes) and notes about what changed.
    """
    notes: list[str] = []
    same = _family(src_fmt) == _family(dst_fmt)
    want = False if dst_fmt == "ANPK" else compress
    seqs = []
    for s in a.seqs:
        n = s
        if dst_fmt != "ANPK" and s.scale:
            n = replace(s, keys=[k[:8] for k in s.keys], scale=False, kf_tail=None)
            notes.append(f"{a.name}/{s.name}: ANPK scale keys dropped (the engine ignores them)")
        if want is True and not n.compressed:
            n = quantize_seq(n)
        elif want is False and n.compressed:
            n = dequantize_seq(n)
        if not same and (n.raw_name is not None or n.extra is not None or n.kf_tail is not None):
            n = replace(n, raw_name=None, extra=None, kf_tail=None)
        seqs.append(n)
    if dst_fmt == "ANP3":
        comp = any(s.compressed for s in seqs)
        flags = ((a.flags & ~1) if src_fmt == "ANP3" else 0) | (1 if comp else 0)
    else:
        flags = 0
    raw = a.raw_name if same else None
    info = a.info_extra if same else None
    npad = a.name_pad if same else None
    if all(n is s for n, s in zip(seqs, a.seqs)) and flags == a.flags and raw is a.raw_name \
            and info is a.info_extra and npad is a.name_pad:
        return a, notes
    return Anim(a.name, seqs, flags, raw, info, npad), notes


# --------------------------------------------------------------------------- write


def _need_len(raw: bytes, size: int, name: str) -> None:
    if len(raw) != size:
        raise ValueError(f"raw name field of {name!r} has {len(raw)} bytes, expected {size} (convert_anim first)")


def _check_name(name: str, size: int | None, what: str) -> None:
    if "\0" in name:
        raise ValueError(f"{what} must not contain a NUL character")
    try:
        encoded = name.encode("latin-1")
    except UnicodeEncodeError:
        raise ValueError(f"{what} {name!r} must use Latin-1 characters") from None
    if size is not None and len(encoded) > size - 1:
        raise ValueError(f"{what} {name!r} is longer than {size - 1} characters")


def _name_bytes(name: str, raw: bytes | None, size: int, what: str) -> bytes:
    """Preserve byte details only while the visible name is unchanged; edits take precedence."""
    if raw is not None and _cstr(raw) == name:
        _need_len(raw, size, name)
        return raw
    _check_name(name, size, what)
    return _field(name, size)


def _write_anp23(ifp: Ifp, anp3: bool) -> bytes:
    out = bytearray()
    for a in ifp.anims:
        out += _name_bytes(a.name, a.raw_name, 24, "animation name")
        data = bytearray()
        for s in a.seqs:
            if s.scale:
                raise ValueError(f"{a.name}/{s.name}: scale keys exist only in ANPK (convert_anim drops them)")
            data += _name_bytes(s.name, s.raw_name, 24, "bone name")
            ft = frame_type(s)
            data += struct.pack("<IIi", ft, len(s.keys), int(s.tag))
            st = _KF[ft]
            for k in s.keys:
                if len(k) != st.size // (2 if s.compressed else 4):
                    raise ValueError(f"{a.name}/{s.name}: key frame {k!r} does not match frame type {ft}")
                data += st.pack(*k)
        fds = len(data) - 36 * len(a.seqs)
        out += struct.pack("<I", len(a.seqs))
        if anp3:
            out += struct.pack("<II", fds, a.flags)
        out += data
    head = _name_bytes(ifp.pack, ifp.raw_pack, 24, "pack name")
    body = head + struct.pack("<I", len(ifp.anims)) + bytes(out)
    return (b"ANP3" if anp3 else b"ANP2") + struct.pack("<I", len(body)) + body


def _section(fc: bytes, payload: bytes, pad: bytes | None = None) -> bytes:
    n = -len(payload) % 4
    tail = pad if pad is not None and len(pad) == n else b"\0" * n
    return fc + struct.pack("<I", len(payload)) + payload + tail


def _write_anpk(ifp: Ifp) -> bytes:
    _check_name(ifp.pack, None, "pack name")
    rawp = ifp.raw_pack if ifp.raw_pack is not None and _cstr(ifp.raw_pack) == ifp.pack else None
    out = bytearray(_section(b"INFO", struct.pack("<I", len(ifp.anims))
                             + (rawp if rawp is not None else ifp.pack.encode("latin-1") + b"\0")))
    for a in ifp.anims:
        _check_name(a.name, None, "animation name")
        raw = a.raw_name if a.raw_name is not None and _cstr(a.raw_name) == a.name else None
        out += _section(b"NAME", raw if raw is not None else a.name.encode("latin-1") + b"\0", a.name_pad)
        dgan = bytearray(_section(b"INFO", struct.pack("<I", len(a.seqs))
                                  + (a.info_extra if a.info_extra is not None else b"\0\0\0\0")))
        for s in a.seqs:
            if s.compressed:
                raise ValueError(f"{a.name}/{s.name}: ANPK key frames are floats (convert_anim dequantizes)")
            name = _name_bytes(s.name, s.raw_name, 28, "bone name")
            extra = s.extra if s.extra is not None else b"\0" * 8 + struct.pack("<i", int(s.tag))
            if len(extra) >= 12 and s.extra is not None:
                extra = extra[:8] + struct.pack("<i", int(s.tag)) + extra[12:]
            cpan = bytearray(_section(b"ANIM", name + struct.pack("<I", len(s.keys)) + extra))
            if s.keys:
                fc = b"KRTS" if s.scale else (b"KRT0" if s.trans else b"KR00")
                st = struct.Struct(f"<{s.width}f")
                kd = bytearray()
                for k in s.keys:
                    if len(k) != s.width:
                        raise ValueError(f"{a.name}/{s.name}: key frame {k!r} has {len(k)} values, expected {s.width}")
                    kd += st.pack(-k[0], -k[1], -k[2], k[3], *k[5:], k[4])
                cpan += _section(fc, bytes(kd) + (s.kf_tail or b""))
            dgan += _section(b"CPAN", bytes(cpan))
        out += _section(b"DGAN", bytes(dgan))
    return b"ANPK" + struct.pack("<I", len(out)) + bytes(out)


def write_ifp(ifp: Ifp, fmt: str | None = None) -> bytes:
    """Encode ``ifp`` as ``fmt`` (default: its own format). Animations must already suit the format
    (:func:`convert_anim`); ``ValueError`` names the first problem (long name, wrong key width)."""
    fmt = (fmt or ifp.format).upper()
    if fmt not in FORMATS:
        raise ValueError(f"unknown IFP format {fmt!r} (ANP3, ANP2, ANPK)")
    try:
        if fmt == "ANPK":
            return _write_anpk(ifp)
        return _write_anp23(ifp, fmt == "ANP3")
    except (struct.error, OverflowError) as e:
        raise ValueError(f"a field is outside its IFP storage range: {e}") from None
