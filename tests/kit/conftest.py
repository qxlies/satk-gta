"""Synthetic RW builders for satk.kit tests (bytes are built here, never stored as files)."""

from __future__ import annotations

import struct
import types

import pytest

LIB = 0x1803FFFF  # RW 3.6.0.3 (SA PC)


def chunk(t: int, payload: bytes, lib: int = LIB) -> bytes:
    return struct.pack("<III", t, len(payload), lib) + payload


def geometry(pos: list[tuple], tris: list[tuple], bsphere=(0.0, 0.0, 0.0, 1.0)) -> bytes:
    """A Geometry: positions, one UV set, list triangles ``(a, b, c)`` on material 0, no normals."""
    nv = len(pos)
    flags = 0x02 | 0x04 | 0x20
    st = struct.pack("<IiiI", flags | (1 << 16), len(tris), nv, 1)
    st += b"".join(struct.pack("<2f", 0.1 * i, 0.2 * i) for i in range(nv))
    st += b"".join(struct.pack("<4H", b, a, 0, c) for a, b, c in tris)
    st += struct.pack("<4fII", *bsphere, 1, 0)
    st += b"".join(struct.pack("<3f", *p) for p in pos)
    mat = chunk(0x07, chunk(0x01, struct.pack("<I4BII3f", 0, 255, 255, 255, 255, 0, 0, 1.0, 1.0, 1.0))
                + chunk(0x03, b""))
    ml = chunk(0x01, struct.pack("<Ii", 1, -1)) + mat
    return chunk(0x0F, chunk(0x01, st) + chunk(0x08, ml) + chunk(0x03, b""))


def clump(geoms: list[bytes], frames: list[tuple], atomics: list[tuple], col: bytes | None = None,
          hanim: dict | None = None) -> bytes:
    """``frames``: ``(parent, name, (x, y, z))``; ``atomics``: ``(frame, geom)``; ``hanim``: frame -> node id."""
    st = chunk(0x01, struct.pack("<III", len(atomics), 0, 0))
    fl = struct.pack("<I", len(frames))
    for parent, _n, p in frames:
        fl += struct.pack("<12fiI", 1, 0, 0, 0, 1, 0, 0, 0, 1, *p, parent, 0)
    fext = b""
    for i, (_parent, name, _p) in enumerate(frames):
        inner = chunk(0x253F2FE, name.encode()) if name else b""
        if hanim and i in hanim:
            inner += chunk(0x11E, struct.pack("<IiI", 0x100, hanim[i], 0))
        fext += chunk(0x03, inner)
    body = st + chunk(0x0E, chunk(0x01, fl) + fext)
    body += chunk(0x1A, chunk(0x01, struct.pack("<I", len(geoms))) + b"".join(geoms))
    for fi, gi in atomics:
        body += chunk(0x14, chunk(0x01, struct.pack("<4I", fi, gi, 5, 0)) + chunk(0x03, b""))
    body += chunk(0x03, chunk(0x253F2FA, col) if col is not None else b"")
    return chunk(0x10, body)


def col3(name: str, spheres: list[tuple] = ()) -> bytes:
    """A COL3 model with spheres ``(x, y, z, r)`` and nothing else."""
    sph = b"".join(struct.pack("<4f4B", x, y, z, r, 63, 0, 0, 0) for x, y, z, r in spheres)
    hdr_len = 4 + 22 + 2 + 40 + 2 * 3 + 2 + 4 + 6 * 4 + 12
    off_sph = hdr_len if spheres else 0
    body = name.encode().ljust(22, b"\0") + struct.pack("<H", 0)
    body += struct.pack("<10f", -1, -1, -1, 1, 1, 1, 0, 0, 0, 2)
    body += struct.pack("<3HBxI", len(spheres), 0, 0, 0, 0)
    body += struct.pack("<6I", off_sph, 0, 0, 0, 0, 0) + struct.pack("<3I", 0, 0, 0)
    body += sph
    return b"COL3" + struct.pack("<I", len(body)) + body


def box_geometry(cx: float, cy: float, cz: float, half: float = 0.5, bsphere=(0.0, 0.0, 0.0, 0.1)) -> bytes:
    pos = [(cx + sx * half, cy + sy * half, cz + sz * half) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    tris = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6),
            (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    return geometry(pos, tris, bsphere)


@pytest.fixture
def rw():
    class R:
        pass

    R.chunk = staticmethod(chunk)
    R.geometry = staticmethod(geometry)
    R.clump = staticmethod(clump)
    R.col3 = staticmethod(col3)
    R.box = staticmethod(box_geometry)
    return R


# ----------------------------------------------------------------------------- a studio session for live tests


@pytest.fixture(scope="module")
def studio_session():
    """``start(name, plans=None)`` -> a context manager with a Blender studio session in its own work directory.

    The work directory is redirected (``SATK_PATHS_WORK``; DragonFF is extracted there from the read-only clone),
    so a live test never writes into the shared ``work``. Anything that needs the real configuration (the vanilla
    index for a template plan) is built before ``start``. Yields a namespace with ``work``, ``name``, ``call``,
    ``py``. Skips without Blender."""
    import contextlib
    import os

    from satk.blender import runner
    from satk.core import config
    from satk.core.errors import SatkError
    from satk.docs.cleanup import remove_tree
    from satk.studio import api
    from satk.studio import launcher as L

    @contextlib.contextmanager
    def start(name: str, tmp_root):
        try:
            runner.blender_exe()
        except SatkError as e:
            pytest.skip(str(e))
        from satk.blender import gamedata
        from satk.index import api as index_api

        index_api.clear_cache()
        work = tmp_root / "work"
        work.mkdir(parents=True, exist_ok=True)
        old = os.environ.get("SATK_PATHS_WORK")
        os.environ["SATK_PATHS_WORK"] = str(work)
        config.reset()
        try:
            runner.ensure_dragonff()
        except SatkError as e:
            os.environ.pop("SATK_PATHS_WORK", None) if old is None else os.environ.__setitem__("SATK_PATHS_WORK", old)
            config.reset()
            pytest.skip(str(e))
        L.start(name, owner_pid=os.getpid(), idle=900)

        def call(method: str, params: dict | None = None, **kw) -> dict:
            return api.call(method, params or {}, session=name, stats="none", **kw)

        def py(code: str, args: dict | None = None) -> dict:
            return (call("python", {"code": code, "args": args or {}}).get("result") or {}).get("value") or {}

        ns = types.SimpleNamespace(work=work, name=name, call=call, py=py)
        try:
            yield ns
        finally:
            api.close_all()
            try:
                L.stop(name)
            finally:
                if old is None:
                    os.environ.pop("SATK_PATHS_WORK", None)
                else:
                    os.environ["SATK_PATHS_WORK"] = old
                config.reset()
                index_api.clear_cache()
                gamedata.clear_cache()
                remove_tree(tmp_root)

    return start
