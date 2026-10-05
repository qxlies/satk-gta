"""satk.media against the real vanilla index and gta-sa-clean (read-only; WP-04 acceptance 1, 3, 5).

Skipped when the game copy or ``work/index/vanilla.sqlite`` is missing, or when the index API is still
the Q1 stub (``NOT_READY``). Output goes to the isolated ``satk_home`` workspace, never the real ``work``.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from satk.core import config as _config
from satk.core.errors import SatkError

pytestmark = pytest.mark.game


@pytest.fixture(scope="module")
def real_index_path() -> Path:
    p = Path(_config.build().paths.work) / "index" / "vanilla.sqlite"
    if not p.is_file():
        pytest.skip(f"no vanilla index: {p} (satk index build)")
    return p


@pytest.fixture
def real_db(real_index_path, satk_home):
    from satk.index.api import IndexDB, override_index

    db = IndexDB("vanilla", path=real_index_path)
    try:
        db.texture_ref("tex:bistro/vent_64")
    except SatkError as e:
        db.close()
        if e.code == "NOT_READY":
            pytest.skip("satk.index API is still the Q1 stub (WP-03 not merged)")
        raise
    with override_index(db):
        yield db
    db.close()


def test_vent_64_png(real_db, mh):
    from satk.media import texture_png

    p = texture_png("tex:bistro/vent_64")
    assert mh.png_pixels(p)[:2] == (64, 64)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    assert texture_png("tex:bistro/vent_64") == p and hashlib.sha256(p.read_bytes()).hexdigest() == sha


def test_lawest1_sheet_has_15(real_db, mh):
    from satk.core.registry import invoke

    env = invoke("texture.image", {"ids": ["txd:lawest1"], "mode": "sheet"})
    assert env["ok"], env
    assert len(env["files"]) == 1 and len(env["legend"]) == 15  # gta3 lawest1 wins over gta_int's (1 texture)
    assert all(r[1].startswith("tex:lawest1/") for r in env["legend"])
    assert mh.png_pixels(env["files"][0])[:2] == (532, 532)


def test_grove_map(real_db, mh):
    from satk.core.registry import invoke

    t = time.perf_counter()
    env = invoke("map.image", {"center": [2495.0, -1687.0], "span": 300.0})
    dt = time.perf_counter() - t
    assert env["ok"], env
    assert mh.png_pixels(env["file"])[:2] == (768, 768)
    assert 0 < len(env["legend"]) <= 20 and env["m_per_px"] == pytest.approx(0.39, abs=0.005)
    assert all(r[1].startswith("inst:") for r in env["legend"])
    assert dt <= 1.5, dt


def test_every_format_decodes(real_db):
    from satk.media.texture import decode_ref

    rows = real_db.query("SELECT 'tex:' || lower(t.name) || '/' || lower(x.name), x.d3dfmt FROM texture x "
                         "JOIN txd t ON t.id = x.txd_id JOIN blob b ON b.id = t.blob_id WHERE b.active = 1 "
                         "GROUP BY x.d3dfmt", limit=50)["rows"]
    fmts = {f for _, f in rows}
    assert {"DXT1", "DXT3", "A8R8G8B8", "X8R8G8B8"} <= fmts
    for sid, fmt in rows:
        ref = real_db.texture_ref(sid)
        w, h, rgba = decode_ref(ref)
        assert len(rgba) == w * h * 4 and w == ref.w, (sid, fmt)
