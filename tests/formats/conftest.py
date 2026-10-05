"""Synthetic fixture builders for satk.formats tests (no game files in the repo, SPEC §5.1 rule 5)."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

SA_LIBID = 0x1803FFFF  # RW 3.6.0.3, build 0xFFFF (SA PC)
SECTOR = 2048


class B:
    """Byte builders for RW chunks, TXD, IMG v1/v2, bnry and a tiny game root."""

    SA_LIBID = SA_LIBID

    @staticmethod
    def chunk(t: int, payload: bytes, libid: int = SA_LIBID) -> bytes:
        return struct.pack("<III", t, len(payload), libid) + payload

    @staticmethod
    def native(name: str = "tex", *, platform: int = 9, fmt: int | str = "DXT1", raster: int = 0x200,
               w: int = 4, h: int = 4, levels: list[bytes] | None = None, flags: int | None = None,
               alpha: int = 0, palette: bytes | None = None, mask: str = "", depth: int = 16,
               filt: int = 6, addr: int = 0x11) -> bytes:
        """A TextureNative chunk (D3D9 by default)."""
        if isinstance(fmt, str):
            fmt_val = struct.unpack("<I", fmt.encode("ascii").ljust(4, b"\0"))[0]
        else:
            fmt_val = fmt
        if levels is None:
            levels = [bytes(range(8))]  # one 4x4 DXT1 block
        if flags is None:
            flags = (8 if isinstance(fmt, str) and fmt.startswith("DXT") else 0) | (1 if alpha else 0)
        if platform == 8:
            fmt_val = alpha
        st = struct.pack("<IBBH32s32sII", platform, filt, addr, 0, name.encode(), mask.encode(), raster, fmt_val)
        st += struct.pack("<HHBBBB", w, h, depth, len(levels), 4, flags)
        if palette is not None:
            st += palette
        for lv in levels:
            st += struct.pack("<I", len(lv)) + lv
        return B.chunk(0x15, B.chunk(0x01, st) + B.chunk(0x03, b""))

    @staticmethod
    def txd(natives: list[bytes], device: int = 2, count: int | None = None) -> bytes:
        body = B.chunk(0x01, struct.pack("<HH", len(natives) if count is None else count, device))
        body += b"".join(natives) + B.chunk(0x03, b"")
        return B.chunk(0x16, body)

    @staticmethod
    def pad(data: bytes) -> bytes:
        return data + b"\0" * (-len(data) % SECTOR)

    @staticmethod
    def ver2(files: list[tuple[str, bytes]], archive_sectors: dict[str, int] | None = None) -> bytes:
        """A VER2 archive; data starts at sector 1 (directory fits in one sector for tests)."""
        archive_sectors = archive_sectors or {}
        n = len(files)
        dir_sectors = max(1, -(-(8 + n * 32) // SECTOR))
        head = b"VER2" + struct.pack("<I", n)
        body = b""
        off = dir_sectors
        for name, data in files:
            d = B.pad(data)
            secs = len(d) // SECTOR
            head += struct.pack("<IHH24s", off, secs, archive_sectors.get(name, 0), name.encode())
            body += d
            off += secs
        return B.pad(head) + body

    @staticmethod
    def v1(files: list[tuple[str, bytes]]) -> tuple[bytes, bytes]:
        """(dir, img) of a v1 archive."""
        d = b""
        img = b""
        off = 0
        for name, data in files:
            p = B.pad(data)
            d += struct.pack("<II24s", off, len(p) // SECTOR, name.encode())
            img += p
            off += len(p) // SECTOR
        return d, img

    @staticmethod
    def bnry(insts: list[tuple], cars: list[tuple] = ()) -> bytes:
        """insts: (x, y, z, qx, qy, qz, qw, model, interior, lod); cars: 4 floats + 8 ints."""
        inst_off = 0x4C
        cars_off = inst_off + 40 * len(insts)
        hdr = b"bnry" + struct.pack("<6I", len(insts), 0, 0, 0, len(cars), 0)
        hdr += struct.pack("<12I", inst_off, 40 * len(insts), 0, 0, 0, 0, 0, 0, cars_off, 48 * len(cars), 0, 0)
        body = b"".join(struct.pack("<7f3i", *i) for i in insts)
        body += b"".join(struct.pack("<4f8i", *c) for c in cars)
        return hdr + body

    # ------------------------------------------------------------------ DFF
    @staticmethod
    def string(s: str) -> bytes:
        raw = s.encode("latin-1") + b"\0"
        return B.chunk(0x02, raw + b"\0" * (-len(raw) % 4))

    @staticmethod
    def material(rgba=(255, 255, 255, 255), tex: str | None = None, mask: str = "", ext: bytes = b"") -> bytes:
        st = struct.pack("<I4BII3f", 0, *rgba, 0, 1 if tex is not None else 0, 1.0, 1.0, 1.0)
        body = B.chunk(0x01, st)
        if tex is not None:
            body += B.chunk(0x06, B.chunk(0x01, struct.pack("<HH", 0x1106, 0)) + B.string(tex) + B.string(mask)
                            + B.chunk(0x03, b""))
        return B.chunk(0x07, body + B.chunk(0x03, ext))

    @staticmethod
    def geometry(pos: list[tuple], *, tris: list[tuple] = (), strips: list[tuple] | None = None,
                 lists: list[tuple] | None = None, mats: list[bytes] | None = None, uv_sets: int = 1,
                 prelit: bool = True, normals: bool = False, night: bool = False, fx2d: bytes | None = None,
                 libid: int = SA_LIBID, bsphere=(0.0, 0.0, 0.0, 1.0), ext: bytes = b"", mat_refs: list[int] | None = None,
                 skin: bool = False, breakable: int = 0) -> bytes:
        """A Geometry chunk. ``tris``: struct triangles (a, b, c, mat); ``strips``/``lists``: BinMesh meshes
        ``(mat, [indices])`` (strip or list mode); vertex attributes are derived from the position index."""
        nv = len(pos)
        flags = 0x02 | 0x20 | (0x08 if prelit else 0) | (0x10 if normals else 0)
        flags |= 0x80 if uv_sets >= 2 else (0x04 if uv_sets == 1 else 0)
        st = struct.pack("<IiiI", flags | (uv_sets << 16), len(tris), nv, 1)
        from satk.formats.rw import rw_version
        if rw_version(libid) < 0x34000:
            st += struct.pack("<3f", 1.0, 1.0, 1.0)
        if prelit:
            st += b"".join(bytes((i & 255, 10, 20, 255)) for i in range(nv))
        for s in range(uv_sets):
            st += b"".join(struct.pack("<2f", i * 0.5 + s, -i * 0.25) for i in range(nv))
        st += b"".join(struct.pack("<4H", b_, a, m, c) for a, b_, c, m in tris)
        st += struct.pack("<4fII", *bsphere, 1, 1 if normals else 0)
        st += b"".join(struct.pack("<3f", *p) for p in pos)
        if normals:
            st += b"".join(struct.pack("<3f", 0.0, 0.0, 1.0) for _ in pos)
        mats = mats if mats is not None else [B.material()]
        refs = mat_refs if mat_refs is not None else [-1] * len(mats)
        ml = B.chunk(0x01, struct.pack(f"<I{len(refs)}i", len(refs), *refs)) + b"".join(mats)
        e = b""
        meshes = strips if strips is not None else lists
        if meshes is not None:
            body = b"".join(struct.pack("<II", len(ix), m) + struct.pack(f"<{len(ix)}I", *ix) for m, ix in meshes)
            e += B.chunk(0x50E, struct.pack("<III", 1 if strips is not None else 0, len(meshes),
                                            sum(len(ix) for _m, ix in meshes)) + body)
        if night:
            e += B.chunk(0x253F2F9, struct.pack("<I", 1) + b"".join(bytes((1, 2, 3, i & 255)) for i in range(nv)))
        if fx2d is not None:
            e += B.chunk(0x253F2F8, fx2d)
        if skin:
            e += B.chunk(0x116, b"\0" * 4)
        e += B.chunk(0x253F2FD, struct.pack("<I", breakable))
        return B.chunk(0x0F, B.chunk(0x01, st, libid) + B.chunk(0x08, ml) + B.chunk(0x03, e + ext), libid)

    @staticmethod
    def fx_light(pos=(1.0, 2.0, 3.0), rgba=(255, 128, 0, 200), corona="coronastar", shadow="shad_exp") -> bytes:
        d = bytes(rgba) + struct.pack("<4f", 100.0, 18.0, 1.5, 2.0) + bytes((0, 1, 0, 40, 0x41))
        d += corona.encode().ljust(24, b"\0") + shadow.encode().ljust(24, b"\0") + bytes((7, 1)) + bytes(5)
        return struct.pack("<3fII", *pos, 0, len(d)) + d

    @staticmethod
    def fx_entries(*entries: bytes) -> bytes:
        return struct.pack("<I", len(entries)) + b"".join(entries)

    @staticmethod
    def clump(geoms: list[bytes], *, frames: list[tuple] = ((-1, "root", (0.0, 0.0, 0.0)),),
              atomics: list[tuple] = ((0, 0),), col: bytes | None = None, lights: list[int] = (),
              libid: int = SA_LIBID, hanim: bool = False, atomic_ext: bytes = b"") -> bytes:
        """A Clump. ``frames``: ``(parent, name, pos)`` with identity rotation; ``atomics``: ``(frame, geom)``;
        ``lights``: frame indices of Light chunks (their Struct must not count as atomics)."""
        st = B.chunk(0x01, struct.pack("<III", len(atomics), len(lights), 0))
        fl = struct.pack("<I", len(frames))
        for parent, _name, p in frames:
            fl += struct.pack("<12fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, *p, parent, 0)
        fext = b""
        for i, (_parent, name, _p) in enumerate(frames):
            inner = B.chunk(0x253F2FE, name.encode()) if name else b""
            if hanim and i == 0:
                inner += B.chunk(0x11E, b"\0" * 12)
            fext += B.chunk(0x03, inner)
        body = st + B.chunk(0x0E, B.chunk(0x01, fl) + fext)
        body += B.chunk(0x1A, B.chunk(0x01, struct.pack("<I", len(geoms))) + b"".join(geoms))
        for fi, gi in atomics:
            body += B.chunk(0x14, B.chunk(0x01, struct.pack("<4I", fi, gi, 5, 0)) + B.chunk(0x03, atomic_ext))
        for f in lights:
            body += B.chunk(0x01, struct.pack("<I", f)) + B.chunk(0x12, B.chunk(0x01, b"\0" * 24) + B.chunk(0x03, b""))
        body += B.chunk(0x03, B.chunk(0x253F2FA, col) if col is not None else b"")
        return B.chunk(0x10, body, libid)

    @staticmethod
    def quad_dff(**kw) -> bytes:
        """1 clump, 1 frame, 1 atomic, 1 geometry: 4 vertices, strip [0,1,2,3] -> 2 triangles."""
        pos = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 2.0)]
        g = B.geometry(pos, tris=[(0, 1, 2, 0), (2, 1, 3, 0)], strips=[(0, [0, 1, 2, 3])],
                       mats=[B.material((255, 0, 0, 255), "brick", "brickm")], **kw)
        return B.clump([g])

    # ------------------------------------------------------------------ COL
    @staticmethod
    def col_v1(name: str, *, spheres=(), boxes=(), verts=(), faces=(), bounds=None, mid: int = 0) -> bytes:
        """``spheres``: (r, cx, cy, cz, mat); ``boxes``: (min3, max3, mat); ``faces``: (a, b, c, mat)."""
        r, c, mn, mx = bounds or (5.0, (0.0, 0.0, 0.0), (-1.0, -2.0, -3.0), (1.0, 2.0, 3.0))
        body = struct.pack("<10f", r, *c, *mn, *mx)
        body += struct.pack("<I", len(spheres)) + b"".join(struct.pack("<4f4B", *s[:4], s[4], 0, 0, 0) for s in spheres)
        body += struct.pack("<I", 0)
        body += struct.pack("<I", len(boxes)) + b"".join(struct.pack("<6f4B", *b_[0], *b_[1], b_[2], 0, 0, 0)
                                                         for b_ in boxes)
        body += struct.pack("<I", len(verts)) + b"".join(struct.pack("<3f", *v) for v in verts)
        body += struct.pack("<I", len(faces)) + b"".join(struct.pack("<3I4B", *f[:3], f[3], 0, 0, 0) for f in faces)
        return b"COLL" + struct.pack("<I22sH", 24 + len(body), name.encode(), mid) + body

    @staticmethod
    def col_v23(name: str, version: int = 3, *, spheres=(), boxes=(), verts=(), faces=(), shadow=(),
                bounds=None, mid: int = 0) -> bytes:
        """COL2/COL3/COL4. ``verts`` int16 triples (x128 fixed point); ``faces`` (a, b, c, mat)."""
        mn, mx, c, r = bounds or ((-1.0, -2.0, -3.0), (1.0, 2.0, 3.0), (0.0, 0.0, 0.0), 5.0)
        hdr = 76 + (12 if version >= 3 else 0) + (4 if version >= 4 else 0)
        data = b""
        base = 32 - 4 + hdr                          # offsets are relative to fourcc + 4

        def put(blob: bytes) -> int:
            nonlocal data
            if not blob:
                return 0
            off = base + len(data)
            data += blob
            return off

        o_sph = put(b"".join(struct.pack("<4f4B", *s[:4], s[4], 0, 0, 0) for s in spheres))
        o_box = put(b"".join(struct.pack("<6f4B", *b_[0], *b_[1], b_[2], 0, 0, 0) for b_ in boxes))
        vb = b"".join(struct.pack("<3h", *v) for v in verts)
        o_vrt = put(vb + b"\0" * (-len(vb) % 4))
        o_face = put(b"".join(struct.pack("<3H2B", *f[:3], f[3], 0) for f in faces))
        flags = (2 if (spheres or boxes or faces) else 0) | (16 if shadow else 0)
        h = struct.pack("<10f3HBxI6I", *mn, *mx, *c, r, len(spheres), len(boxes), len(faces), 0, flags,
                        o_sph, o_box, 0, o_vrt, o_face, 0)
        if version >= 3:
            o_sv = put(vb + b"\0" * (-len(vb) % 4)) if shadow else 0
            o_sf = put(b"".join(struct.pack("<3H2B", *f[:3], f[3], 0) for f in shadow))
            h += struct.pack("<3I", len(shadow), o_sv, o_sf)
        if version >= 4:
            h += struct.pack("<I", 0)
        fc = {2: b"COL2", 3: b"COL3", 4: b"COL4"}[version]
        return fc + struct.pack("<I22sH", 24 + len(h) + len(data), name.encode(), mid) + h + data

    # ------------------------------------------------------------------ IFP
    @staticmethod
    def anp3(pack: str, anims: list[tuple], *, anp2: bool = False) -> bytes:
        """``anims``: (name, [(bone name, frame_type 1..4, frames)])."""
        kf = {1: 20, 2: 32, 3: 10, 4: 16}
        body = b""
        for name, bones in anims:
            data = b"".join(n.encode().ljust(24, b"\0") + struct.pack("<IIi", t, f, i) + b"\0" * (kf[t] * f)
                            for i, (n, t, f) in enumerate(bones))
            body += name.encode().ljust(24, b"\0") + struct.pack("<I", len(bones))
            if not anp2:
                body += struct.pack("<II", len(data), 1)
            body += data
        head = pack.encode().ljust(24, b"\0") + struct.pack("<I", len(anims))
        return (b"ANP2" if anp2 else b"ANP3") + struct.pack("<I", len(head) + len(body)) + head + body

    @staticmethod
    def anpk(pack: str, anims: list[tuple]) -> bytes:
        """``anims``: (name, [(bone name, frames, b"KR00"|b"KRT0"|b"KRTS")])."""
        kf = {b"KR00": 20, b"KRT0": 32, b"KRTS": 44}

        def sec(fc: bytes, data: bytes) -> bytes:
            return fc + struct.pack("<I", len(data)) + data + b"\0" * (-len(data) % 4)

        body = sec(b"INFO", struct.pack("<I", len(anims)) + pack.encode() + b"\0")
        for name, bones in anims:
            dg = sec(b"INFO", struct.pack("<II", len(bones), 0))
            for bn, fr, k in bones:
                cp = sec(b"ANIM", bn.encode().ljust(28, b"\0") + struct.pack("<4i", fr, 0, -1, -1))
                if fr:
                    cp += sec(k, b"\0" * (kf[k] * fr))
                dg += sec(b"CPAN", cp)
            body += sec(b"NAME", name.encode() + b"\0") + sec(b"DGAN", dg)
        return sec(b"ANPK", body)

    @staticmethod
    def game_root(root: Path, *, samp: bool = False) -> Path:
        """A tiny game root: DAT files, IDE, text IPL, gta3/gta_int with a TXD and a bnry."""
        (root / "data" / "maps").mkdir(parents=True)
        (root / "models" / "generic").mkdir(parents=True)
        (root / "anim").mkdir(parents=True)
        (root / "data" / "default.dat").write_text("# default\nIDE DATA\\DEFAULT.IDE\n", encoding="latin-1")
        (root / "data" / "gta.dat").write_text(
            "IMG DATA\\PATHS\\CARREC.IMG\nIMG MODELS\\GTA_INT.IMG\nIMG MODELS\\GTA3.IMG\n"
            "IDE DATA\\MAPS\\TEST.IDE\nIPL DATA\\MAPS\\TEST.IPL\nIPL DATA\\MAP.ZON\nSPLASH loadsc2\n",
            encoding="latin-1")
        (root / "data" / "Default.ide").write_text(
            "objs\n1, box, generic, 100, 0\nend\ncars\n400, landstal, landstal, car, LANDSTAL, LANDSTK, null,"
            " richfamily, 10, 7, 0, 0, 0.7, 0.7, -1\nend\n", encoding="latin-1")
        (root / "data" / "maps" / "Test.IDE").write_text(
            "objs\n2, lodbox, generic, 300, 0\nend\ntxdp\nchild, generic\nend\n", encoding="latin-1")
        (root / "data" / "maps" / "test.ipl").write_text(
            "inst\n2, lodbox, 0, 10, 20, 3, 0, 0, 0, 1, -1\n1, box, 0, 11, 21, 3, 0, 0, 0, 1, 0\nend\n",
            encoding="latin-1")
        tx = B.txd([B.native("a"), B.native("b", fmt=21, raster=0x500, w=1, h=1, levels=[b"\1\2\3\4"], flags=0)])
        bn = B.bnry([(12, 22, 3, 0, 0, 0, 1, 1, 0, 0)])
        (root / "models" / "gta3.img").write_bytes(B.ver2([("box.txd", tx), ("test_stream0.ipl", bn)]))
        (root / "models" / "gta_int.img").write_bytes(B.ver2([("box.txd", B.txd([]))]))
        (root / "models" / "player.img").write_bytes(B.ver2([]))
        (root / "anim" / "anim.img").write_bytes(B.ver2([]))
        (root / "anim" / "cuts.img").write_bytes(B.ver2([]))
        (root / "models" / "generic" / "vehicle.txd").write_bytes(B.txd([B.native("v")]))
        (root / "anim" / "ped.ifp").write_bytes(b"ANP3" + b"\0" * 32)
        if samp:
            (root / "samp").mkdir()
            (root / "samp" / "SAMP.IMG").write_bytes(B.ver2([]))
            (root / "data" / "default.two").write_text(
                "IMG SAMP\\SAMP.IMG\nIMG MODELS\\GTA3.IMG\nIMG MODELS\\GTA_INT.IMG\nIDE DATA\\DEFAULT.IDE\n",
                encoding="latin-1")
            (root / "data" / "gta.two").write_text("IMG MODELS\\CUTSCENE.IMG\n", encoding="latin-1")
        return root


@pytest.fixture(scope="session")
def b() -> type[B]:
    return B


@pytest.fixture(scope="session")
def vanilla_full() -> dict:
    """ONE full vanilla selftest shared by the game tests: every stage + full geometry decode + spike S2.

    Running it once (instead of a full run, a ``--quick --geometry`` run and a ``--quick --dxt`` run)
    keeps ``tests/formats`` inside its 20 s budget (SPEC 5.3 WP-02, acceptance 1). Read-only.
    """
    from satk.core import config
    from satk.formats import selftest

    root = config.build().paths.game
    if not (root / "gta_sa.exe").is_file():
        pytest.skip(f"game root not found: {root}")
    return selftest.run(root, "vanilla", bench=True, geometry=True, dxt=True, dxt_sample_mpix=2.0)


@pytest.fixture
def mini_root(tmp_path: Path) -> Path:
    return B.game_root(tmp_path / "game")


@pytest.fixture
def directory_link():
    """Temporary directory aliases, without a subprocess or Windows symlink privileges."""
    links: list[Path] = []

    def make(link: Path, target: Path) -> None:
        if os.name == "nt":
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        else:
            link.symlink_to(target, target_is_directory=True)
        links.append(link)

    yield make
    for link in reversed(links):
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()
