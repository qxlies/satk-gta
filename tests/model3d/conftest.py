"""Synthetic DFF/TXD builders and fixtures for satk.model3d tests (no game files, SPEC §5.1 rule 5)."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from satk.model3d.gltf import parse_glb
from satk.model3d.png import png_size

SA_LIBID = 0x1803FFFF  # RW 3.6.0.3 (SA PC)
IDENT9 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


class M:
    """Byte builders: RW chunks, materials, geometries, clumps (DFF) and A8R8G8B8 TXDs."""

    @staticmethod
    def chunk(t: int, payload: bytes) -> bytes:
        return struct.pack("<III", t, len(payload), SA_LIBID) + payload

    @staticmethod
    def string(s: str) -> bytes:
        raw = s.encode("latin-1") + b"\0"
        return M.chunk(0x02, raw + b"\0" * (-len(raw) % 4))

    @staticmethod
    def material(rgba=(255, 255, 255, 255), tex: str | None = None) -> bytes:
        st = struct.pack("<I4BII3f", 0, *rgba, 0, 1 if tex is not None else 0, 1.0, 1.0, 1.0)
        body = M.chunk(0x01, st)
        if tex is not None:
            body += M.chunk(0x06, M.chunk(0x01, struct.pack("<HH", 0x1106, 0)) + M.string(tex) + M.string("")
                            + M.chunk(0x03, b""))
        return M.chunk(0x07, body + M.chunk(0x03, b""))

    @staticmethod
    def geometry(pos, tris, *, uv=None, prelit=None, normals=None, mats=None, strip: list | None = None,
                 skin: bool = False, night=None) -> bytes:
        """``tris``: ``(a, b, c, mat)``; ``uv``: per-vertex (u, v); ``prelit``: per-vertex RGBA;
        ``strip``: optional BinMesh ``[(mat, [indices])]`` in tristrip mode (then used for decoding)."""
        nv = len(pos)
        flags = 0x02 | 0x20 | (0x08 if prelit else 0) | (0x10 if normals else 0) | (0x04 if uv else 0)
        if strip is not None:
            flags |= 0x01
        st = struct.pack("<IiiI", flags | ((1 if uv else 0) << 16), len(tris), nv, 1)
        if prelit:
            st += b"".join(bytes(c) for c in prelit)
        if uv:
            st += b"".join(struct.pack("<2f", *t) for t in uv)
        st += b"".join(struct.pack("<4H", b, a, m, c) for a, b, c, m in tris)
        xs = [p[0] for p in pos]
        ys = [p[1] for p in pos]
        zs = [p[2] for p in pos]
        cx, cy, cz = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2
        r = max(((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2) ** 0.5 for x, y, z in pos)
        st += struct.pack("<4fII", cx, cy, cz, r, 1, 1 if normals else 0)
        st += b"".join(struct.pack("<3f", *p) for p in pos)
        if normals:
            st += b"".join(struct.pack("<3f", *n) for n in normals)
        mats = mats if mats is not None else [M.material()]
        ml = M.chunk(0x01, struct.pack(f"<I{len(mats)}i", len(mats), *([-1] * len(mats)))) + b"".join(mats)
        ext = b""
        if strip is not None:
            body = b"".join(struct.pack("<II", len(ix), m) + struct.pack(f"<{len(ix)}I", *ix) for m, ix in strip)
            ext += M.chunk(0x50E, struct.pack("<III", 1, len(strip), sum(len(ix) for _m, ix in strip)) + body)
        if night:
            ext += M.chunk(0x253F2F9, struct.pack("<I", 1) + b"".join(bytes(c) for c in night))
        if skin:
            ext += M.chunk(0x116, b"\0" * 4)
        return M.chunk(0x0F, M.chunk(0x01, st) + M.chunk(0x08, ml) + M.chunk(0x03, ext))

    @staticmethod
    def clump(geoms: list[bytes], frames, atomics) -> bytes:
        """``frames``: ``(parent, name, pos[, rot9])``; ``atomics``: ``(frame, geom)``."""
        st = M.chunk(0x01, struct.pack("<III", len(atomics), 0, 0))
        fl = struct.pack("<I", len(frames))
        for fr in frames:
            parent, _name, p = fr[0], fr[1], fr[2]
            rot = fr[3] if len(fr) > 3 else IDENT9
            fl += struct.pack("<12fiI", *rot, *p, parent, 0)
        fext = b"".join(M.chunk(0x03, M.chunk(0x253F2FE, name.encode()) if name else b"") for _p, name, *_ in frames)
        body = st + M.chunk(0x0E, M.chunk(0x01, fl) + fext)
        body += M.chunk(0x1A, M.chunk(0x01, struct.pack("<I", len(geoms))) + b"".join(geoms))
        for fi, gi in atomics:
            body += M.chunk(0x14, M.chunk(0x01, struct.pack("<4I", fi, gi, 5, 0)) + M.chunk(0x03, b""))
        body += M.chunk(0x03, b"")
        return M.chunk(0x10, body)

    @staticmethod
    def native(name: str, w: int, h: int, pixels, *, alpha: bool = False, addr: int = 0x11) -> bytes:
        """A D3D9 ``A8R8G8B8`` (alpha) / ``X8R8G8B8`` texture; ``pixels`` = RGBA tuples, row 0 = top."""
        raster = 0x500 if alpha else 0x600
        fmt = 21 if alpha else 22
        data = b"".join(bytes((b, g, r, a)) for r, g, b, a in pixels)
        st = struct.pack("<IBBH32s32sII", 9, 1, addr, 0, name.encode(), b"", raster, fmt)
        st += struct.pack("<HHBBBB", w, h, 32, 1, 4, 1 if alpha else 0)
        st += struct.pack("<I", len(data)) + data
        return M.chunk(0x15, M.chunk(0x01, st) + M.chunk(0x03, b""))

    @staticmethod
    def txd(natives: list[bytes]) -> bytes:
        body = M.chunk(0x01, struct.pack("<HH", len(natives), 2)) + b"".join(natives) + M.chunk(0x03, b"")
        return M.chunk(0x16, body)

    # ------------------------------------------------------------------ ready-made models
    @staticmethod
    def box(size=(2.0, 4.0, 1.0), center=(0.0, 0.0, 0.5), *, tex: str | None = None, prelit: bool = False,
            rgba=(255, 255, 255, 255), normals: bool = False):
        """``(pos, tris, uv)`` of a closed box: 8 corners per face (24 verts, 12 triangles)."""
        sx, sy, sz = (s / 2 for s in size)
        cx, cy, cz = center
        faces = [((1, 0, 0), [(1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1)]),
                 ((-1, 0, 0), [(-1, 1, -1), (-1, -1, -1), (-1, -1, 1), (-1, 1, 1)]),
                 ((0, 1, 0), [(1, 1, -1), (-1, 1, -1), (-1, 1, 1), (1, 1, 1)]),
                 ((0, -1, 0), [(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)]),
                 ((0, 0, 1), [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]),
                 ((0, 0, -1), [(-1, 1, -1), (1, 1, -1), (1, -1, -1), (-1, -1, -1)])]
        pos, nrm, uv, tris = [], [], [], []
        for n, quad in faces:
            b = len(pos)
            for (x, y, z), t in zip(quad, [(0, 1), (1, 1), (1, 0), (0, 0)]):
                pos.append((cx + x * sx, cy + y * sy, cz + z * sz))
                nrm.append(n)
                uv.append(t)
            tris += [(b, b + 1, b + 2, 0), (b, b + 2, b + 3, 0)]
        return pos, tris, uv, nrm

    @staticmethod
    def box_dff(*, tex: str | None = "boxtex", prelit=None, rgba=(255, 255, 255, 255), size=(2.0, 4.0, 1.0),
                normals: bool = True, name: str = "box") -> bytes:
        pos, tris, uv, nrm = M.box(size)
        pl = [prelit] * len(pos) if prelit else None
        g = M.geometry(pos, tris, uv=uv, prelit=pl, normals=nrm if normals else None,
                       mats=[M.material(rgba, tex)])
        return M.clump([g], [(-1, name, (0.0, 0.0, 0.0))], [(0, 0)])

    @staticmethod
    def car_dff() -> bytes:
        """A tiny vehicle: chassis (paint key slot 1 + glass), bonnet ok/dam, chassis_vlo, wheel at
        wheel_rf_dummy and three empty wheel dummies."""
        pos, tris, uv, nrm = M.box((2.0, 4.0, 1.0), (0.0, 0.0, 0.6))
        chassis = M.geometry(pos, [(a, b, c, (i // 2) % 2) for i, (a, b, c, _m) in enumerate(tris)], uv=uv,
                             normals=nrm, mats=[M.material((60, 255, 0, 255), "paint"), M.material((255, 255, 255, 120), None)])
        p2, t2, u2, n2 = M.box((1.8, 1.2, 0.2), (0.0, 1.2, 1.15))
        bonnet_ok = M.geometry(p2, t2, uv=u2, normals=n2, mats=[M.material((60, 255, 0, 255), "paint")])
        bonnet_dam = M.geometry(p2, t2, uv=u2, normals=n2, mats=[M.material((255, 0, 0, 255), None)])
        p3, t3, u3, n3 = M.box((2.2, 4.2, 1.2), (0.0, 0.0, 0.6))
        vlo = M.geometry(p3, t3, uv=u3, normals=n3, mats=[M.material((0, 0, 255, 255), None)])
        p4, t4, u4, n4 = M.box((0.3, 0.7, 0.7), (0.0, 0.0, 0.0))
        wheel = M.geometry(p4, t4, uv=u4, normals=n4, mats=[M.material((20, 20, 20, 255), "tyre")])
        frames = [(-1, "car", (0.0, 0.0, 0.0)), (0, "chassis_dummy", (0.0, 0.0, 0.0)), (1, "chassis", (0.0, 0.0, 0.0)),
                  (1, "chassis_vlo", (0.0, 0.0, 0.0)), (1, "bonnet_dummy", (0.0, 0.0, 0.0)),
                  (4, "bonnet_ok", (0.0, 0.0, 0.0)), (4, "bonnet_dam", (0.0, 0.0, 0.0)),
                  (0, "wheel_rf_dummy", (1.1, 1.3, 0.35)), (7, "wheel", (0.0, 0.0, 0.0)),
                  (0, "wheel_lf_dummy", (-1.1, 1.3, 0.35)), (0, "wheel_rb_dummy", (1.1, -1.3, 0.35)),
                  (0, "wheel_lb_dummy", (-1.1, -1.3, 0.35))]
        atomics = [(2, 0), (3, 3), (5, 1), (6, 2), (8, 4)]
        return M.clump([chassis, bonnet_ok, bonnet_dam, vlo, wheel], frames, atomics)

    @staticmethod
    def solid_txd(colours: dict[str, tuple], *, size: int = 4, alpha: dict[str, list] | None = None) -> bytes:
        """TXD with one solid ``size``² texture per name (``alpha``: name -> per-pixel alpha list)."""
        natives = []
        for name, rgba in colours.items():
            px = [tuple(rgba)] * (size * size)
            if alpha and name in alpha:
                px = [(rgba[0], rgba[1], rgba[2], a) for a in alpha[name]]
            natives.append(M.native(name, size, size, px, alpha=bool(alpha and name in alpha)))
        return M.txd(natives)


_SIZES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
_CBYTES = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}


def check_glb(data: bytes) -> dict:
    """Structural glTF 2.0 checks (subset of the Khronos validator) -> the JSON document."""
    doc, binary = parse_glb(data)
    assert doc["asset"]["version"] == "2.0"
    assert len(data) % 4 == 0
    buffers = doc.get("buffers", [])
    if buffers:
        assert buffers[0]["byteLength"] == len(binary) and "uri" not in buffers[0]
    for v in doc.get("bufferViews", []):
        assert v["byteOffset"] % 4 == 0 and v["byteOffset"] + v["byteLength"] <= len(binary)
    for a in doc.get("accessors", []):
        v = doc["bufferViews"][a["bufferView"]]
        need = a["count"] * _SIZES[a["type"]] * _CBYTES[a["componentType"]]
        assert need <= v["byteLength"], a
    for mesh in doc.get("meshes", []):
        for p in mesh["primitives"]:
            counts = {doc["accessors"][i]["count"] for i in p["attributes"].values()}
            assert len(counts) == 1, "attribute counts differ"
            nv = counts.pop()
            pa = doc["accessors"][p["attributes"]["POSITION"]]
            assert "min" in pa and "max" in pa and pa["type"] == "VEC3"
            ia = doc["accessors"][p["indices"]]
            assert ia["count"] % 3 == 0 and ia["componentType"] in (5123, 5125)
            v = doc["bufferViews"][ia["bufferView"]]
            fmt = "<%d%s" % (ia["count"], "H" if ia["componentType"] == 5123 else "I")
            idx = struct.unpack_from(fmt, binary, v["byteOffset"] + ia.get("byteOffset", 0))
            assert max(idx) < nv
            if "material" in p:
                assert p["material"] < len(doc["materials"])
    nodes = doc["nodes"]
    seen = set()
    stack = list(doc["scenes"][0]["nodes"])
    while stack:
        n = stack.pop()
        assert n not in seen, "node graph is not a tree"
        seen.add(n)
        stack.extend(nodes[n].get("children", []))
    assert seen == set(range(len(nodes)))
    for t in doc.get("textures", []):
        img = doc["images"][t["source"]]
        v = doc["bufferViews"][img["bufferView"]]
        png = binary[v["byteOffset"]:v["byteOffset"] + v["byteLength"]]
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and min(png_size(png)) >= 1
    return doc


@pytest.fixture
def validate_glb():
    """Structural glTF 2.0 checker: ``validate_glb(bytes) -> json``."""
    return check_glb


@pytest.fixture
def m() -> type[M]:
    return M


@pytest.fixture
def fake_db(satk_home: Path, tmp_path: Path):
    """``FakeIndexDB`` (+ ``open_index`` override) with synthetic payloads for model:411 (car) and
    model:17613 (prelit box); TXDs infernus/vehicle/lae2roadshub hold solid textures."""
    from satk.index.api import override_index
    from satk.index.fake import FakeIndexDB

    root = tmp_path / "fakegame"
    payloads = {
        "dff:infernus": M.car_dff(),
        "txd:infernus": M.solid_txd({"tyre": (30, 30, 30, 255)}),
        "txd:vehicle": M.solid_txd({"paint": (200, 200, 200, 255)}),
        "dff:lae2_roads89": M.box_dff(tex="plaintarmac1", prelit=(200, 180, 160, 255), size=(40.0, 30.0, 1.0)),
        "txd:lae2roadshub": M.solid_txd({"plaintarmac1": (90, 90, 90, 255)}),
    }
    db = FakeIndexDB(root=root, payloads=payloads)
    with override_index(db):
        yield db
