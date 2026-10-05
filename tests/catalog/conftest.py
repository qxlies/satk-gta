"""Fixtures of the satk.catalog tests (M2-09): a FakeIndexDB with real synthetic texture pixels and zones.

No game files are read: every texture of the fake data set gets a solid colour in its own format (written
by ``FakeIndexDB(root=..., payloads=...)`` into small synthetic archives under ``tmp_path``), three zones are
added to the fake's SQLite, and model previews come from a stub of ``satk.model3d.batch.render_chunk``
(solid 128 px PNGs), because the fake has no DFF bytes. Output goes to the isolated ``satk_home`` workspace.
"""

from __future__ import annotations

import struct
import types
import zlib
from pathlib import Path

import pytest

from satk.index.api import override_index
from satk.index.fake import FakeIndexDB

ZONES = [
    # id, name, label, title, type, level, box
    (1, "GAN1", "GAN", "Ganton", 0, 1, (2222.6, -1722.3, -89.1, 2632.8, -1628.5, 110.9)),
    (2, "LA01", "LA", "Los Santos", 0, 0, (44.6, -2892.9, -242.9, 2997.0, -768.0, 900.0)),
    (3, "SF01", "UNUSED", None, 3, 2, (-3000.0, -742.3, -500.0, -1270.5, 1530.2, 500.0)),
]


def _c565(r: int, g: int, b: int) -> int:
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def payload(fmt: str, w: int, h: int, rgba) -> bytes:
    """mip0 bytes of a solid-colour texture in ``fmt``."""
    r, g, b, a = rgba
    if fmt in ("A8R8G8B8", "X8R8G8B8"):
        return bytes((b, g, r, a)) * (w * h)
    nb = max(1, (w + 3) // 4) * max(1, (h + 3) // 4)
    c = _c565(r, g, b)
    if fmt == "DXT1":
        return struct.pack("<HHI", c, c, 0) * nb
    if fmt == "DXT3":
        alpha = bytes([((a >> 4) << 4) | (a >> 4)]) * 8
        return (alpha + struct.pack("<HHI", c, c, 0)) * nb
    raise ValueError(fmt)


_PALETTE = [(255, 0, 255), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 0)]


def make_fake(root: Path) -> FakeIndexDB:
    """A FakeIndexDB with pixels for every texture and the :data:`ZONES`."""
    base = FakeIndexDB()
    rows = base.query("SELECT t.name, x.name, x.d3dfmt, x.w, x.h FROM texture x JOIN txd t ON t.id = x.txd_id "
                      "ORDER BY t.name, x.idx", limit=500)["rows"]
    payloads = {}
    for i, (txd, name, fmt, w, h) in enumerate(rows):
        r, g, b = _PALETTE[i % len(_PALETTE)]
        payloads[f"tex:{txd}/{name}"] = payload(fmt, w, h, (r, g, b, 128 if fmt == "A8R8G8B8" else 255))
    db = FakeIndexDB(root=root, payloads=payloads)
    for zid, name, label, title, typ, level, box in ZONES:
        db._db.execute("INSERT INTO zone(id, name, label, title, type, level, minx, miny, minz, maxx, maxy, maxz, "
                       "source_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)", (zid, name, label, title, typ, level, *box))
    db._db.commit()
    return db


def solid_png(path: Path, rgb: tuple[int, int, int], size: int = 128) -> Path:
    """A solid RGB PNG (stdlib)."""
    raw = b"".join(b"\0" + bytes(rgb) * size for _ in range(size))

    def ch(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                     + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))
    return path


@pytest.fixture
def cat_db(satk_home, tmp_path, monkeypatch):
    """Fake index with pixels and zones (installed via ``override_index``) + stubbed model previews.

    ``cat_db.renders`` counts the model ids passed to the stub.
    """
    import satk.model3d.batch as batch

    db = make_fake(tmp_path / "game")
    renders: list[int] = []
    previews = tmp_path / "previews"

    def fake_render_chunk(ids, views, size, profile, force, el, bg):
        out = []
        for mid in ids:
            renders.append(mid)
            r, g, b = _PALETTE[mid % len(_PALETTE)]
            f = solid_png(previews / f"{mid:06d}aaaa-bbbbbbbb-soft-1-128.png", (r, g, b))
            out.append((mid, f"m{mid}", "cached", str(f), None, 1.0, []))
        return out

    monkeypatch.setattr(batch, "render_chunk", fake_render_chunk)
    db.renders = renders
    with override_index(db):
        yield db


def chunk(path: Path) -> tuple[str, object]:
    """Parse ``CAT.add("<name>",<json>);`` -> ``(name, payload)``."""
    import json

    text = path.read_text(encoding="utf-8").strip()
    assert text.startswith("CAT.add(") and text.endswith(");"), text[:80]
    name_json, _, body = text[len("CAT.add("):-2].partition(",")
    return json.loads(name_json), json.loads(body)


@pytest.fixture
def ch():
    """Helpers: ``ch.chunk(path)``, ``ch.ZONES``, ``ch.solid_png``."""
    return types.SimpleNamespace(chunk=chunk, ZONES=ZONES, solid_png=solid_png, payload=payload)
