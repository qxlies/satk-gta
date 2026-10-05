"""M2-13 acceptance on real data (read-only; marker ``game``): the gta-scout pack bound to the vanilla index.

Needs ``<paths.src>/gta-scout`` (the pack) and ``<work>/index/vanilla.sqlite`` (``satk dev gate`` builds a
private one before the game tests); skipped otherwise. Notes go to the isolated ``satk_home`` workspace.
Report 09 (section 3.6): the publisher's DFFs match ours for every model entry; on vanilla 1.0 2 131 of the
2 291 entries find their DFF among the models the game loads (the rest are SA-MP or shadowed copies).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.core import config as _config

pytestmark = pytest.mark.game

_REAL = _config.build()  # captured before satk_home isolates the environment
PACK_DIR = Path(_REAL.paths.src) / "gta-scout" / "data" / "annotations"
INDEX = Path(_REAL.paths.work) / "index" / "vanilla.sqlite"


@pytest.fixture
def real(satk_home):
    from satk.core.errors import SatkError
    from satk.index.api import IndexDB, override_index

    if not any(PACK_DIR.glob("sa-*.json")):
        pytest.skip(f"no gta-scout pack in {PACK_DIR}")
    if not INDEX.is_file():
        pytest.skip(f"no vanilla index: {INDEX} (satk index build)")
    try:
        db = IndexDB("vanilla", path=INDEX)
    except SatkError as e:
        pytest.skip(f"vanilla index unusable: {e.msg}")
    with override_index(db):
        yield db
    db.close()


def test_vanilla_binds_at_least_2000_and_reimport_is_idempotent(real, run_cli):
    r = run_cli(["describe", "import", "--pack", str(PACK_DIR)])
    assert r.code == 0, r.out
    env = r.json
    assert env["pack"]["digest"] == "ok" and env["pack"]["models_with_dff"] >= 2291
    assert env["bound"] >= 2000 and env["entries_bound"] >= 2000, env
    assert env["added"] == env["notes"] == env["bound"]  # one model per entry on vanilla
    assert env["hash_only"] == 0 and not any(w.startswith("BLOB_UNREADABLE") for w in env.get("warn", []))

    again = run_cli(["describe", "import", "--pack", str(PACK_DIR)]).json
    assert (again["added"], again["replaced"], again["unchanged"]) == (0, 0, env["notes"])

    found = run_cli(["asset", "find", "bench", "--kind", "note"]).json
    assert found["total"] >= 5 and all(row[2].startswith("model:") for row in found["rows"])
    status = run_cli(["describe", "status", "--pack", str(PACK_DIR)]).json
    assert status["imported"] == env["notes"]
