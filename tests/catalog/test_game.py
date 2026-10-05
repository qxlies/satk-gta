"""satk catalog against the real vanilla index and gta-sa-clean (read-only; M2-09 acceptance).

Index: the configured ``<work>/index/vanilla.sqlite`` when it has the current schema (``satk dev gate`` builds a
private one before the game tests); otherwise (missing or an older schema) a private vanilla index is built once
per module into a pytest temp dir (subprocess ``satk index build`` with ``SATK_PATHS_WORK`` pointing there,
about 10 s with a warm OS cache), so the shared index is never touched. Catalog output goes to the isolated ``satk_home`` workspace: the
full catalog data is built without thumbnails (seconds), thumbnails only for a small ``limit`` sample.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from satk.core import config as _config
from satk.core.config import REPO_ROOT

pytestmark = pytest.mark.game


def _usable(path: Path) -> bool:
    """``path`` exists and has the schema version this checkout needs."""
    from satk.core.errors import SatkError
    from satk.index.api import IndexDB

    try:
        IndexDB("vanilla", path=path).close()
    except SatkError:
        return False
    return True


def _build_private_index(work: Path) -> Path:
    """Build a vanilla index of this checkout's schema into ``work`` (a private ``SATK_PATHS_WORK``)."""
    env = dict(os.environ, SATK_PATHS_WORK=str(work), PYTHONUTF8="1", PYTHONPATH=str(REPO_ROOT / "src"))
    p = subprocess.run([sys.executable, "-X", "utf8", "-m", "satk", "index", "build", "--profile", "vanilla",
                        "--json"], cwd=REPO_ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800)
    lines = p.stdout.strip().splitlines()
    try:
        res = json.loads(lines[-1]) if lines else {}
    except ValueError:
        res = {}
    out = work / "index" / "vanilla.sqlite"
    if p.returncode != 0 or not res.get("ok") or not _usable(out):
        pytest.fail(f"private vanilla index build failed (code {p.returncode}): {(p.stdout + p.stderr)[-2000:]}",
                    pytrace=False)
    return out


@pytest.fixture(scope="module")
def real_index_path(tmp_path_factory) -> Path:
    p = Path(_config.build().paths.work) / "index" / "vanilla.sqlite"
    if _usable(p):
        return p
    return _build_private_index(tmp_path_factory.mktemp("catalog-work"))


@pytest.fixture
def real_db(real_index_path, satk_home):
    from satk.index.api import IndexDB, override_index

    db = IndexDB("vanilla", path=real_index_path)
    with override_index(db):
        yield db
    db.close()


def _q(db, sql: str) -> int:
    return int(db.query(sql)["rows"][0][0])


def test_full_catalog_data(real_db, ch):
    from satk.catalog.build import build

    env = build(thumbs=False, jobs=1)
    assert env["ok"], env
    root = Path(env["dir"])
    c = env["counts"]
    assert c["models"] == _q(real_db, "SELECT count(*) FROM model WHERE active = 1")
    assert c["textures"] == _q(real_db, "SELECT count(*) FROM texture x JOIN txd t ON t.id = x.txd_id "
                                        "JOIN blob b ON b.id = t.blob_id WHERE b.active = 1")
    assert c["zones"] == _q(real_db, "SELECT count(*) FROM zone") and c["placements"] == _q(real_db, "SELECT count(*) FROM inst")
    assert env["map"] is True and (root / "map.webp").is_file()

    _, tex = ch.chunk(root / "data" / "textures.js")
    _, txds = ch.chunk(root / "data" / "txds.js")
    keys = [(txds[r[0]][0] + "/" + r[1]).lower() for r in tex["rows"]]
    grove = [k for k in keys if "grove" in k]
    # acceptance: searching "grove" finds textures (gang tags, the CJ tattoo, the cutscene sign)
    assert "tags_lafront/grove" in grove and "csgrove1/grove1" in grove and len(grove) >= 10

    _, zones = ch.chunk(root / "data" / "zones.js")
    _, z = ch.chunk(root / "data" / "z.js")
    gan = next(i for i, r in enumerate(zones) if r[0] == "GAN1")
    assert zones[gan][2] == "Ganton" and zones[gan][11] > 100 and len(z[str(gan)]) > 10

    _, meta = ch.chunk(root / "data" / "meta.js")
    _, page = ch.chunk(root / "data" / "m" / f"{17613 >> 7}.js")
    p = page["17613"]
    assert [meta["ipls"][e[0]] + "#" + str(e[1]) for e in p["p"]] == ["lae2_stream0#4"]
    assert {e[0].lower() for e in p["tx"]} >= {"plaintarmac1", "sidewgrass1"}


def test_sample_with_thumbnails(real_db, ch):
    pytest.importorskip("numpy")
    from satk.catalog.build import build

    env = build(limit=8, jobs=1)
    assert env["ok"] and not any(w.startswith("THUMB_FAILED") for w in env.get("warn", [])), env
    root = Path(env["dir"])
    _, models = ch.chunk(root / "data" / "models.js")
    with_dff = [r for r in models if r[8] is not None]
    assert with_dff and all(r[4] == 1 for r in with_dff)
    for r in with_dff:
        assert (root / "t" / "m" / str(r[0] >> 7) / f"{r[0]}.{env['format']}").is_file()
    _, tex = ch.chunk(root / "data" / "textures.js")
    assert tex["rows"] and tex["nothumb"] == []
    assert env["thumbs"]["textures"]["written"] == len(tex["img"])
    again = build(limit=8, jobs=1)
    assert again["changed"] == 0 and again["thumbs"]["models"]["kept"] == len(with_dff)
