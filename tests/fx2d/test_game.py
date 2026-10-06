"""satk.fx2d on the vanilla game (READ-ONLY; gta-sa-clean). Golden numbers: tests/fx2d/golden.json.

* every vanilla DFF with 2dEffect entries (1 851) goes DFF -> JSON text -> DFF bit for bit;
* the street light of lamppost1 copied onto a synthetic DFF re-dumps equal;
* the check baseline: no errors on vanilla data, every particle name and corona/shadow texture resolves.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from satk.core import config as _config
from satk.formats.img import ImgArchive
from satk.fx2d.check import check_items, load_resources
from satk.fx2d.doc import read_doc, spheres
from satk.rw.roundtrip import iter_game

from .conftest import build_dff

pytestmark = pytest.mark.game

GOLDEN = json.loads((Path(__file__).with_name("golden.json")).read_text(encoding="utf-8"))


@pytest.fixture
def game_work(clean_root, tmp_path, monkeypatch):
    """The real configuration (game roots) with a private work directory."""
    monkeypatch.setenv("SATK_PATHS_WORK", str(tmp_path / "work"))
    _config.reset()
    yield tmp_path / "work"
    _config.reset()


def test_every_vanilla_dff_round_trips(game_work, run_cli):
    env = run_cli(["fx2d", "roundtrip"]).json
    assert env["ok"], env
    got = {k: env.get(k, 0) for k in GOLDEN["roundtrip"]}
    assert got == GOLDEN["roundtrip"]
    env = run_cli(["fx2d", "roundtrip", "--no-keep"]).json
    assert env["exact"] == GOLDEN["roundtrip_no_keep_exact"]


def test_street_light_copied_onto_a_synthetic_dff(game_work, run_cli, clean_root, tmp_path):
    box = tmp_path / "box.dff"
    box.write_bytes(build_dff(None, names=("mylamp",)))
    env = run_cli(["fx2d", "copy", "models/gta3.img/lamppost1.dff", str(box), "--filter", "light"]).json
    assert env["ok"] and env["copied"] == 1 and env["created"] == [0], env
    assert env["check"] == {"errors": 0, "warnings": 0}
    out = Path(env["file"])
    assert out.is_relative_to(game_work)
    with ImgArchive.open(clean_root / "models" / "gta3.img") as a:
        src = read_doc(a.read(a.find("lamppost1.dff")))
    new = read_doc(out.read_bytes())
    assert new["geometries"][0]["effects"] == src["geometries"][0]["effects"]
    assert new["geometries"][0]["frame"] == "mylamp"
    d = run_cli(["fx2d", "dump", str(out)]).json
    assert d["rows"][0][1] == "light" and d["rows"][0][3].startswith("coronastar rgba 249,145,34,200")


def test_vanilla_check_baseline(clean_root):
    res = load_resources(clean_root / "models" / "effects.fxp", clean_root / "models" / "particle.txd",
                         lambda p: p.read_bytes())
    assert len(res.fxp) == 82 and {"coronastar", "shad_exp"} <= res.txd
    sev, codes = Counter(), Counter()
    for label, data in iter_game(clean_root, "dff"):
        if b"\xf8\xf2\x53\x02" not in data:
            continue
        doc = read_doc(data, keep=False)
        items = [(f"{label}#{j}", g["geometry"], e) for g in doc["geometries"] for j, e in enumerate(g["effects"])]
        for i in check_items(items, res, spheres(data)):
            sev[i.sev] += 1
            codes[i.code] += 1
    assert sev["error"] == GOLDEN["check"]["errors"]
    assert dict(codes) == GOLDEN["check"]["warnings"]


def test_check_by_model_sid(clean_root, run_cli):
    """``model:`` SIDs go through the vanilla index (built by the gate before the game tests)."""
    env = run_cli(["fx2d", "check", "model:1297"]).json
    if not env["ok"] and env["error"]["code"] in ("INDEX_MISSING", "NOT_READY"):
        pytest.skip("no vanilla index")
    assert env["ok"] and env["counts"] == {"light": 1} and env["errors"] == 0, env
    assert env["source"] == "model:1297 (models/gta3.img/lamppost1.dff)"
