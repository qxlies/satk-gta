"""``satk id`` against the real profile indexes (read-only; B4 acceptance: no free id is taken in any profile).

Uses every profile index built in the real work directory (vanilla, installed, samp; the gate builds vanilla);
skipped when none is built.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from satk.core import config as _config
from satk.core.errors import SatkError

pytestmark = pytest.mark.game

_REAL = _config.build()  # captured before satk_home isolates the environment
PROFILES = ("vanilla", "installed", "samp")


@pytest.fixture
def real_profiles(satk_home):
    from satk.index.api import IndexDB, override_index

    dbs = {}
    for p in PROFILES:
        path = Path(_REAL.paths.work) / "index" / f"{p}.sqlite"
        if not path.is_file():
            continue
        try:
            db = IndexDB(p, path=path)
        except SatkError:
            continue
        prof = _REAL.profiles.get(p)
        if prof is not None:
            db.root = prof.root
        dbs[p] = db
    if not dbs:
        pytest.skip("no profile index built (satk index build --all)")

    def factory(profile: str):
        if profile in dbs:
            return dbs[profile]
        raise SatkError("INDEX_MISSING", f"no index for {profile}")

    with override_index(factory):
        yield dbs
    for db in dbs.values():
        db.close()


def _defined(dbs) -> set[int]:
    out: set[int] = set()
    for db in dbs.values():
        with sqlite3.connect("file:" + db.path.as_posix() + "?mode=ro", uri=True) as c:
            out |= {r[0] for r in c.execute("SELECT id FROM model")}
    return out


@pytest.mark.parametrize("kind", ["vehicle", "ped", "weapon", "object"])
def test_free_ids_are_free_in_every_profile(real_profiles, run_cli, kind):
    defined = _defined(real_profiles)
    env = run_cli(["id", "free", "--kind", kind, "--count", "300"]).json
    assert env["ok"] and sorted(env["profiles"]) == sorted(real_profiles)
    ids = env["ids"]
    assert len(ids) == 300 and len(set(ids)) == 300
    assert not set(ids) & defined
    assert all(0 < i <= 19999 for i in ids)
    for p in real_profiles:  # each profile alone gives ids that are free there too
        one = run_cli(["id", "free", "--kind", kind, "--count", "50", "--profile", p]).json
        assert not set(one["ids"]) & _defined({p: real_profiles[p]})


def test_vanilla_facts(real_profiles, run_cli):
    env = run_cli(["id", "free", "--kind", "vehicle", "--count", "3"]).json
    assert env["ids"][0] >= 612  # 400-611 are the stock vehicles
    assert env["capacity"]["used"] == 212 and any(w.startswith("STORE_FULL") for w in env["warn"])
    blk = run_cli(["id", "free", "--kind", "object", "--count", "100", "--contiguous"]).json
    assert blk["ids"] == list(range(blk["ids"][0], blk["ids"][0] + 100))
    c = run_cli(["id", "conflicts", "--profile", next(iter(real_profiles))]).json
    assert c["ok"] and c["errors"] == 0


def test_vanilla_store_low_warnings(real_profiles, run_cli):
    if "vanilla" not in real_profiles:
        pytest.skip("no vanilla index")
    ped = run_cli(["id", "free", "--kind", "ped", "--profile", "vanilla"]).json
    assert ped["capacity"] == {"store": 278, "used": 276}
    assert any(w.startswith("STORE_LOW: only 2 of 278") for w in ped["warn"])
    weap = run_cli(["id", "free", "--kind", "weapon", "--profile", "vanilla"]).json
    assert any(w.startswith("STORE_LOW: only 1 of 51") for w in weap["warn"])
    obj = run_cli(["id", "free", "--kind", "object", "--profile", "vanilla"]).json
    assert obj["capacity"]["used"] == 14045 and not any(w.startswith("STORE_") for w in obj.get("warn", []))
