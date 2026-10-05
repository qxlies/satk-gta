"""Byte builders for satk.lint tests (DFF/TXD/COL/IMG) and a clean synthetic mod (importable by tests).

No game files: everything is generated here (the RW/TXD/COL layouts follow tests/formats/conftest.py).
"""

from __future__ import annotations

import struct
from pathlib import Path

SA_LIBID = 0x1803FFFF  # RW 3.6.0.3, build 0xFFFF
SECTOR = 2048


def libid(ver: int, build: int = 0xFFFF) -> int:
    """RW library id of version ``ver`` (``0x36003``)."""
    return (((ver - 0x30000) & 0x3FF00) << 14) | ((ver & 0x3F) << 16) | build


class B:
    """Byte builders."""

    @staticmethod
    def chunk(t: int, payload: bytes, lib: int = SA_LIBID) -> bytes:
        return struct.pack("<III", t, len(payload), lib) + payload

    # ------------------------------------------------------------------ TXD
    @staticmethod
    def native(name: str = "tex", *, platform: int = 9, fmt: int | str = "DXT1", raster: int = 0x200,
               w: int = 4, h: int = 4, levels: list[bytes] | None = None, flags: int | None = None,
               alpha: int = 0, depth: int = 16) -> bytes:
        if isinstance(fmt, str):
            fmt_val = struct.unpack("<I", fmt.encode("ascii").ljust(4, b"\0"))[0]
        else:
            fmt_val = fmt
        if levels is None:
            levels = [bytes(8)]
        if flags is None:
            flags = (8 if isinstance(fmt, str) and fmt.startswith("DXT") else 0) | (1 if alpha else 0)
        st = struct.pack("<IBBH32s32sII", platform, 6, 0x11, 0, name.encode(), b"", raster, fmt_val)
        st += struct.pack("<HHBBBB", w, h, depth, len(levels), 4, flags)
        for lv in levels:
            st += struct.pack("<I", len(lv)) + lv
        return B.chunk(0x15, B.chunk(0x01, st) + B.chunk(0x03, b""))

    @staticmethod
    def dxt1_chain(w: int, h: int) -> list[bytes]:
        out = []
        while True:
            out.append(bytes(max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 8))
            if w == 1 and h == 1:
                return out
            w, h = max(1, w // 2), max(1, h // 2)

    @staticmethod
    def txd(natives: list[bytes], count: int | None = None) -> bytes:
        body = B.chunk(0x01, struct.pack("<HH", len(natives) if count is None else count, 2))
        body += b"".join(natives) + B.chunk(0x03, b"")
        return B.chunk(0x16, body)

    # ------------------------------------------------------------------ IMG
    @staticmethod
    def ver2(files: list[tuple[str, bytes]]) -> bytes:
        def pad(d: bytes) -> bytes:
            return d + b"\0" * (-len(d) % SECTOR)
        n = len(files)
        dir_sectors = max(1, -(-(8 + n * 32) // SECTOR))
        head = b"VER2" + struct.pack("<I", n)
        body = b""
        off = dir_sectors
        for name, data in files:
            d = pad(data)
            secs = len(d) // SECTOR
            head += struct.pack("<IHH24s", off, secs, 0, name.encode())
            body += d
            off += secs
        return pad(head) + body

    # ------------------------------------------------------------------ DFF
    @staticmethod
    def string(s: str) -> bytes:
        raw = s.encode("latin-1") + b"\0"
        return B.chunk(0x02, raw + b"\0" * (-len(raw) % 4))

    @staticmethod
    def material(tex: str | None = None, rgba=(255, 255, 255, 255)) -> bytes:
        st = struct.pack("<I4BII3f", 0, *rgba, 0, 1 if tex is not None else 0, 1.0, 1.0, 1.0)
        body = B.chunk(0x01, st)
        if tex is not None:
            body += B.chunk(0x06, B.chunk(0x01, struct.pack("<HH", 0x1106, 0)) + B.string(tex) + B.string("")
                            + B.chunk(0x03, b""))
        return B.chunk(0x07, body + B.chunk(0x03, b""))

    @staticmethod
    def geometry(pos: list[tuple], tris: list[tuple], *, mats: list[bytes] | None = None, uv_sets: int = 1,
                 prelit: bool = True, normals: bool = False, night: bool = False, prelit_rgba=None, uvs=None,
                 extra_flags: int = 0, skin: bool = False, lib: int = SA_LIBID) -> bytes:
        """A Geometry with a struct triangle list (``tris``: (a, b, c, mat))."""
        nv = len(pos)
        flags = 0x02 | 0x20 | (0x08 if prelit else 0) | (0x10 if normals else 0) | extra_flags
        flags |= 0x80 if uv_sets >= 2 else (0x04 if uv_sets == 1 else 0)
        st = struct.pack("<IiiI", flags | (uv_sets << 16), len(tris), nv, 1)
        if prelit:
            rgba = prelit_rgba or (120, 110, 100, 255)
            st += bytes(rgba) * nv
        for s in range(uv_sets):
            if uvs is not None:
                st += b"".join(struct.pack("<2f", *uv) for uv in uvs)
            else:
                st += b"".join(struct.pack("<2f", (i % 2) * 1.0, (i // 2) * 1.0) for i in range(nv))
        st += b"".join(struct.pack("<4H", b_, a, m, c) for a, b_, c, m in tris)
        st += struct.pack("<4fII", 0.5, 0.5, 0.0, 1.0, 1, 1 if normals else 0)
        st += b"".join(struct.pack("<3f", *p) for p in pos)
        if normals:
            st += b"".join(struct.pack("<3f", 0.0, 0.0, 1.0) for _ in pos)
        mats = mats if mats is not None else [B.material("gm_wall")]
        ml = B.chunk(0x01, struct.pack(f"<I{len(mats)}i", len(mats), *([-1] * len(mats)))) + b"".join(mats)
        e = b""
        if night:
            e += B.chunk(0x253F2F9, struct.pack("<I", 1) + bytes((40, 40, 60, 255)) * nv)
        if skin:
            e += B.chunk(0x116, b"\0" * 4)
        return B.chunk(0x0F, B.chunk(0x01, st, lib) + B.chunk(0x08, ml) + B.chunk(0x03, e), lib)

    QUAD = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 1.0)]
    QUAD_TRIS = [(0, 1, 2, 0), (2, 1, 3, 0)]

    @staticmethod
    def clump(geoms: list[bytes], *, frames=((-1, "root"),), atomics=((0, 0),), col: bytes | None = None,
              lib: int = SA_LIBID) -> bytes:
        st = B.chunk(0x01, struct.pack("<III", len(atomics), 0, 0))
        fl = struct.pack("<I", len(frames))
        for parent, _name in frames:
            fl += struct.pack("<12fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, parent, 0)
        fext = b"".join(B.chunk(0x03, B.chunk(0x253F2FE, name.encode()) if name else b"") for _p, name in frames)
        body = st + B.chunk(0x0E, B.chunk(0x01, fl) + fext)
        body += B.chunk(0x1A, B.chunk(0x01, struct.pack("<I", len(geoms))) + b"".join(geoms))
        for fi, gi in atomics:
            body += B.chunk(0x14, B.chunk(0x01, struct.pack("<4I", fi, gi, 5, 0)) + B.chunk(0x03, b""))
        body += B.chunk(0x03, B.chunk(0x253F2FA, col) if col is not None else b"")
        return B.chunk(0x10, body, lib)

    @staticmethod
    def dff(**geo) -> bytes:
        """One clump, one frame, one atomic, one quad geometry (keyword args go to :meth:`geometry`)."""
        pos = geo.pop("pos", B.QUAD)
        tris = geo.pop("tris", B.QUAD_TRIS)
        clump_kw = {k: geo.pop(k) for k in ("frames", "atomics", "col", "lib") if k in geo}
        if "lib" in clump_kw:
            geo["lib"] = clump_kw["lib"]
        return B.clump([B.geometry(pos, tris, **geo)], **clump_kw)

    # ------------------------------------------------------------------ COL
    @staticmethod
    def col_v1(name: str, *, spheres=(), boxes=(), verts=(), faces=(), bounds=None) -> bytes:
        """``spheres``: (r, cx, cy, cz, mat); ``boxes``: (min3, max3, mat); ``faces``: (a, b, c, mat)."""
        r, c, mn, mx = bounds or (5.0, (0.0, 0.0, 0.0), (-2.0, -2.0, -2.0), (2.0, 2.0, 2.0))
        body = struct.pack("<10f", r, *c, *mn, *mx)
        body += struct.pack("<I", len(spheres)) + b"".join(struct.pack("<4f4B", *s[:4], s[4], 0, 0, 0) for s in spheres)
        body += struct.pack("<I", 0)
        body += struct.pack("<I", len(boxes)) + b"".join(struct.pack("<6f4B", *b_[0], *b_[1], b_[2], 0, 0, 0)
                                                         for b_ in boxes)
        body += struct.pack("<I", len(verts)) + b"".join(struct.pack("<3f", *v) for v in verts)
        body += struct.pack("<I", len(faces)) + b"".join(struct.pack("<3I4B", *f[:3], f[3], 0, 0, 0) for f in faces)
        return b"COLL" + struct.pack("<I22sH", 24 + len(body), name.encode(), 0) + body

    @staticmethod
    def col3(name: str, *, spheres=(), boxes=(), verts=(), faces=(), bounds=None, flags: int | None = None,
             version: int = 3) -> bytes:
        """COL2/3. ``spheres``: (cx, cy, cz, r, mat); ``boxes``: (min3, max3, mat); ``verts`` metres (x128 on
        disk); ``faces``: (a, b, c, mat); ``bounds``: (min3, max3, center3, radius)."""
        mn, mx, c, r = bounds or ((-2.0, -2.0, -2.0), (2.0, 2.0, 2.0), (0.0, 0.0, 0.0), 3.5)
        hdr = 76 + (12 if version >= 3 else 0)
        data = b""
        base = 32 - 4 + hdr

        def put(blob: bytes) -> int:
            nonlocal data
            if not blob:
                return 0
            off = base + len(data)
            data += blob
            return off

        o_sph = put(b"".join(struct.pack("<4f4B", *s[:4], s[4], 0, 0, 0) for s in spheres))
        o_box = put(b"".join(struct.pack("<6f4B", *b_[0], *b_[1], b_[2], 0, 0, 0) for b_ in boxes))
        vb = b"".join(struct.pack("<3h", *(round(x * 128) for x in v)) for v in verts)
        o_vrt = put(vb + b"\0" * (-len(vb) % 4))
        o_face = put(b"".join(struct.pack("<3H2B", *f[:3], f[3], 0) for f in faces))
        if flags is None:
            flags = 2 if (spheres or boxes or faces) else 0
        h = struct.pack("<10f3HBxI6I", *mn, *mx, *c, r, len(spheres), len(boxes), len(faces), 0, flags,
                        o_sph, o_box, 0, o_vrt, o_face, 0)
        if version >= 3:
            h += struct.pack("<3I", 0, 0, 0)
        fc = b"COL3" if version == 3 else b"COL2"
        return fc + struct.pack("<I22sH", 24 + len(h) + len(data), name.encode()[:22], 0) + h + data

    BOX = (((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0), 0),)
    TRI_VERTS = ((-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (0.0, 1.0, 0.0))


class Mod:
    """A clean synthetic mod folder ``gm/``: IDE + DFF + TXD + COL that lint without findings."""

    def __init__(self, root: Path):
        self.dir = root / "gm"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ide = "objs\n18000, gm_box, gm_txd, 100, 0\nend\n"
        self.files: dict[str, bytes] = {
            "gm_box.dff": B.dff(night=True),
            "gm_txd.txd": B.txd([B.native("gm_wall", w=64, h=64, levels=B.dxt1_chain(64, 64))]),
            "gm_box.col": B.col3("gm_box", boxes=B.BOX, verts=B.TRI_VERTS, faces=((0, 1, 2, 0),)),
        }

    def write(self) -> Path:
        (self.dir / "gm.ide").write_text(self.ide, encoding="latin-1")
        for name, data in self.files.items():
            (self.dir / name).write_bytes(data)
        return self.dir
