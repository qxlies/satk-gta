"""``mod check`` capacity rows (``mod.capacity``): what a mod adds to the stock engine stores.

Synthetic games and mods only (``conftest.py``); store sizes are shrunk with ``monkeypatch`` so a few lines
exercise the warn and error levels. The real numbers are checked against the clean copy at the end.
"""

from __future__ import annotations

import pytest

from satk.core import config as _config

from .conftest import make_mod, rw_stub

_REAL = _config.build()  # captured before satk_home isolates the environment

CAR = "450, newcar, newcar, car, TESTCAR, NEWCAR, null, normal, 10, 0, 0, -1, 0.7, 0.7, 0"


def _rows(env: dict, rule: str = "mod.capacity") -> list[list]:
    return [r for r in env["rows"] if r[0] == rule]


def test_capacity_rows_for_new_cols_ids_2dfx_and_enex(games, tmp_path, run_cli, monkeypatch):
    from satk.modinspect import check as C

    monkeypatch.setitem(C.STORES, "col", (4, "COL file slots"))
    monkeypatch.setitem(C.STORES, "vehicle", (12, "vehicle model slots"))
    files = {f"cols/new{i}.col": b"COLL" + bytes(60) for i in range(5)}
    files["data/vehicles.ide"] = ("cars\n400, testcar, testcar, car, TESTCAR, TESTCAR, null, normal, 10, 0, 0, -1, 0.7, "
                                  "0.7, 0\n" + CAR + "\nend\n")
    files["data/maps/test.ide"] = "objs\n1000, box1, boxes, 100, 0\nend\n2dfx\n1000, 0, 0, 1, 255, 255, 255, 255, 0\nend\n"
    files["data/maps/test.ipl"] = ("inst\n1000, box1, 0, 10.0, 20.0, 5.0, 0, 0, 0, 1, -1\nend\nenex\n"
                                   "1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, \"ex\", 0, 2, 24, 0\nend\n")
    mod = make_mod(tmp_path / "pack", files)
    env = run_cli(["mod", "check", str(mod), "--no-lint", "--json"]).json
    rows = {r[3].split(":", 1)[0]: r for r in _rows(env)}
    col = rows["COL file slots"]
    assert col[1] == "error" and col[2].startswith("cols/new")
    assert "the game uses 1, this mod adds 5 = 6 of 4" in col[3] and "(-2 left)" in col[3]
    car = rows["vehicle model slots"]
    assert car[1] == "warn" and "the game uses 3, this mod adds 1 = 4 of 12" in car[3] and "limit adjuster" in car[3]
    assert rows["IDE 2dfx entries"][1] == "info" and "adds 1 = 1 of 100" in rows["IDE 2dfx entries"][3]
    assert rows["entry-exits (IPL enex)"][1] == "info" and "adds 1 = 1 of 400" in rows["entry-exits (IPL enex)"][3]
    assert env["summary"]["error"] >= 1


def test_no_capacity_rows_for_replacements(games, tmp_path, run_cli):
    mod = make_mod(tmp_path / "rep", {"testcar.dff": rw_stub(tag=b"x"), "testcar.txd": rw_stub(0x16, tag=b"y")})
    env = run_cli(["mod", "check", str(mod), "--no-lint", "--json"]).json
    assert _rows(env) == []


def test_streaming_hint_needs_the_budget_operation(games, tmp_path, run_cli):
    from satk.core import registry as R

    mod = make_mod(tmp_path / "tex", {"brandnew.txd": rw_stub(0x16)})
    env = run_cli(["mod", "check", str(mod), "--no-lint", "--json"]).json
    has_budget = any(o.name == "texture.budget" for o in R.all_ops())
    assert bool(_rows(env, "mod.streaming")) == has_budget
    txd = _rows(env)
    assert txd and txd[0][1] == "info" and txd[0][3].startswith("TXD slots: the game uses 2, this mod adds 1")


@pytest.mark.game
def test_real_capacity_numbers():
    """The vanilla counts the capacity rows start from (clean copy, read-only)."""
    from satk.modinspect.base import BaseGame
    from satk.modinspect.check import _base_count

    root = _REAL.paths.game
    if not (root / "gta_sa.exe").is_file():
        pytest.skip(f"no clean copy at {root}")

    class _W:
        base = BaseGame("vanilla", root=root)

    counts = {k: _base_count(_W, k) for k in ("vehicle", "ped", "weapon", "object", "timed", "clump", "col", "2dfx",
                                              "enex")}
    assert counts == {"vehicle": 212, "ped": 276, "weapon": 50, "object": 14045, "timed": 160, "clump": 89,
                      "col": 252, "2dfx": 97, "enex": 376}
