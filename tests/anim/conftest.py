"""Synthetic fixtures for the ``satk.anim`` tests (no game files).

* :func:`anp3_bytes` packs an ANP3 file by hand from ``(name, [(bone, tag, ftype, keys)])`` tuples (independent of
  the writer under test); :func:`anpk_bytes` does the same for ANPK sections;
* :func:`walk_ifp` builds a small ped-like package with the model API: a root with translation, a pelvis, a
  spine and an arm, compressed;
* :func:`skinned_dff` / :func:`plain_dff` build minimal DFF clumps (FrameList with FrameName and HAnim plugins).
"""

from __future__ import annotations

import struct

import pytest

from satk.anim.ifp import Anim, Ifp, Seq
from satk.rw.chunk import CLUMP, EXTENSION, FRAMELIST, FRAMENAME, HANIM, STRUCT, Chunk, RwStream, pack_libid

_KF = {1: "<5f", 2: "<8f", 3: "<5h", 4: "<8h"}


def _name(s: str | bytes, n: int) -> bytes:
    b = s if isinstance(s, bytes) else s.encode("latin-1")
    return b[:n].ljust(n, b"\0")


def anp3_bytes(pack: str, anims: list, *, flags: int | None = None, anp2: bool = False) -> bytes:
    """``anims`` = ``[(name, [(bone, tag, ftype, [key tuple, ...]), ...]), ...]``; keys in file order."""
    body = b""
    for aname, seqs in anims:
        data = b""
        fds = 0
        comp = 0
        for bone, tag, ft, keys in seqs:
            kd = b"".join(struct.pack(_KF[ft], *k) for k in keys)
            fds += len(kd)
            comp |= ft in (3, 4)
            data += _name(bone, 24) + struct.pack("<IIi", ft, len(keys), tag) + kd
        body += _name(aname, 24) + struct.pack("<I", len(seqs))
        if not anp2:
            body += struct.pack("<II", fds, comp if flags is None else flags)
        body += data
    body = _name(pack, 24) + struct.pack("<I", len(anims)) + body
    return (b"ANP2" if anp2 else b"ANP3") + struct.pack("<I", len(body)) + body


def _sec(fc: bytes, payload: bytes, pad: bytes | None = None) -> bytes:
    n = -len(payload) % 4
    return fc + struct.pack("<I", len(payload)) + payload + (pad if pad is not None else b"\0" * n)


def anpk_bytes(pack: str, anims: list) -> bytes:
    """ANPK: ``anims`` = ``[(name, [(bone, tag, fourcc, [float tuples as stored: q, (tr, (scale)), t])])]``."""
    out = _sec(b"INFO", struct.pack("<I", len(anims)) + pack.encode() + b"\0")
    for aname, seqs in anims:
        out += _sec(b"NAME", aname.encode() + b"\0")
        dg = _sec(b"INFO", struct.pack("<I", len(seqs)) + b"\0\0\0\0")
        for bone, tag, fc, keys in seqs:
            cp = _sec(b"ANIM", _name(bone, 28) + struct.pack("<IIIi", len(keys), 0, 0, tag))
            if keys:
                w = len(keys[0])
                cp += _sec(fc, b"".join(struct.pack(f"<{w}f", *k) for k in keys))
            dg += _sec(b"CPAN", cp)
        out += _sec(b"DGAN", dg)
    return b"ANPK" + struct.pack("<I", len(out)) + out


def walk_ifp(pack: str = "test", n: int = 5, travel: float = 1.5) -> Ifp:
    """A compressed ped-like package: ``walk`` (root moves ``travel`` m along +Y) and ``idle`` (no travel)."""
    def rot(k: int) -> tuple:
        return (0, 0, int(2896 + k), 2896)

    anims = []
    for aname, tr in (("walk", travel), ("idle", 0.0)):
        seqs = [
            Seq("Root", 0, True, True, [rot(0) + (2 * k, 0, int(round(tr * 1024 * k / (n - 1))), -70)
                                        for k in range(n)]),
            Seq(" Pelvis", 1, False, True, [(-2048, -2048, -2048, 2048, 2 * k) for k in range(n)]),
            Seq(" Spine", 2, False, True, [(0, 0, 10 * k, 4096, 2 * k) for k in range(n)]),
            Seq(" R UpperArm", 22, False, True, [(100 * k, 0, 0, 4095, 2 * k) for k in range(n)]),
        ]
        anims.append(Anim(aname, seqs, flags=1))
    return Ifp("ANP3", pack, anims)


@pytest.fixture
def ifp_files(tmp_path):
    """Writes a few synthetic IFP files into ``tmp_path`` and returns their paths by key."""
    from satk.anim.ifp import write_ifp

    d = tmp_path / "ifp"
    d.mkdir()
    files = {"walk": d / "walk.ifp"}
    files["walk"].write_bytes(write_ifp(walk_ifp()))
    files["float"] = d / "float.ifp"
    files["float"].write_bytes(anp3_bytes("flt", [("spin", [("Root", 0, 2, [(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0),
                                                                           (0.0, 0.0, 0.70710677, 0.70710677, 0.5,
                                                                            0.0, 2.0, 0.0)])])]))
    files["anpk"] = d / "cut.ifp"
    files["anpk"].write_bytes(anpk_bytes("cut", [("door_open", [("DOOR", -1, b"KRT0",
                                                                 [(0.0, 0.0, 0.0, 1.0, 1.0, 2.0, 3.0, 0.0),
                                                                  (0.0, 0.0, 0.5, 0.8660254, 1.0, 2.0, 3.5, 1.0)])])]))
    return files


# --------------------------------------------------------------------------- DFF clumps

#: (bone id, name, frame parent, HAnim flags) of a small skinned hierarchy: Root > Pelvis > (Spine > Head,
#: L Thigh), as push/pop flags encode it.
SKIN_BONES = [(0, "Root", 0, 0), (1, " Pelvis", 1, 0), (2, " Spine", 2, 2), (5, " Head", 3, 1), (41, " L Thigh", 2, 0)]


def _frames_chunk(libid: int, frames: list[tuple[str, int, tuple | None]]) -> Chunk:
    raw = b"".join(struct.pack("<9f3fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, parent, 0) for _n, parent, _h in frames)
    exts = []
    for name, _p, han in frames:
        kids = [Chunk(FRAMENAME, libid, data=name.encode("latin-1"))]
        if han is not None:
            bid, nodes = han
            payload = struct.pack("<IiI", 0x100, bid, len(nodes))
            if nodes:
                payload += struct.pack("<II", 0, 36) + b"".join(struct.pack("<iii", *n) for n in nodes)
            kids.append(Chunk(HANIM, libid, data=payload))
        exts.append(Chunk(EXTENSION, libid, kids=kids))
    return Chunk(FRAMELIST, libid, kids=[Chunk(STRUCT, libid, data=struct.pack("<I", len(frames)) + raw), *exts])


def _clump(frames) -> bytes:
    libid = pack_libid(0x36003)
    clump = Chunk(CLUMP, libid, kids=[Chunk(STRUCT, libid, data=struct.pack("<III", 0, 0, 0)),
                                      _frames_chunk(libid, frames), Chunk(EXTENSION, libid, kids=[])])
    return RwStream([clump]).to_bytes()


def skinned_dff() -> bytes:
    """Frame 0 = clump root; frames 1.. = the bones of :data:`SKIN_BONES` (the first one holds the hierarchy)."""
    nodes = [(bid, i, flags) for i, (bid, _n, _p, flags) in enumerate(SKIN_BONES)]
    frames = [("ped", -1, None)]
    for i, (bid, name, parent, _f) in enumerate(SKIN_BONES):
        frames.append((name, parent, (bid, nodes if i == 0 else [])))
    return _clump(frames)


def plain_dff() -> bytes:
    """A door object: root > door > handle."""
    return _clump([("door_root", -1, None), ("DOOR", 0, None), ("handle", 1, None)])


def img_bytes(entries: dict[str, bytes]) -> bytes:
    """A VER2 IMG archive (directory, then every entry on its own 2048-byte sectors)."""
    names = list(entries)
    first = (8 + 32 * len(names) + 2047) // 2048
    head = b"VER2" + struct.pack("<I", len(names))
    data = b""
    sector = first
    for n in names:
        blob = entries[n]
        secs = (len(blob) + 2047) // 2048
        head += struct.pack("<IHH24s", sector, secs, 0, n.encode())
        data += blob + b"\0" * (secs * 2048 - len(blob))
        sector += secs
    return head + b"\0" * (first * 2048 - len(head)) + data
