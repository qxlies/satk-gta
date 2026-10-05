"""``satk asset lint`` / ``asset lint-rules``: envelope, filters, paging, fail-on, save, targets, SIDs, index."""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.index.api import FakeIndexDB, override_index
from satk.lint.rules import Collector, Rules, rules_path
from satk.lint.runner import lint

from lint_synth import B, Mod

LINT_SRC = Path(__file__).resolve().parents[2] / "src" / "satk" / "lint"


def _bad_mod(m: Mod) -> Path:
    """The clean mod plus one error (missing TXD), one warn (dup name) and one info (empty TXD)."""
    m.ide += "objs\n18001, gm_box2, gm_none, 100, 0\nend\n"
    m.files["gm_box2.dff"] = B.dff(night=True)
    m.files["gm_box2.col"] = B.col3("gm_box2", boxes=B.BOX)
    m.files["dup.col"] = B.col3("gm_box", boxes=B.BOX)
    m.files["empty.txd"] = B.txd([])
    m.ide += "objs\n18002, empty, empty, 100, 0\nend\n"
    m.files["empty.dff"] = B.dff(night=True, mats=[B.material()])
    m.files["empty.col"] = B.col3("empty", boxes=B.BOX)
    return m.write()


# --------------------------------------------------------------------------- rules data


def test_rules_file_and_code_agree():
    data = json.loads(rules_path().read_text(encoding="utf-8"))
    ids = set(data["rules"])
    src = "\n".join(p.read_text(encoding="utf-8") for p in LINT_SRC.glob("*.py"))
    used = set(re.findall(r'\b(?:add|on|param)\(\s*(?:\w+,\s*)?"((?:img|file|dff|txd|col|ide|link)\.[a-z_0-9]+)"', src))
    used |= {f"{k}.parse" for k in ("dff", "txd", "col", "ide")}   # c.add(f"{it.kind}.parse", ...)
    assert used - ids == set(), "rules used by the code but missing in data/lint_rules.json"
    assert ids - used == set(), "rules in data/lint_rules.json that no check implements"
    for rid, r in data["rules"].items():
        assert r["sev"] in ("info", "warn", "error", "fatal"), rid
        assert r["what"] and r["what_ru"] and r["ref"] and r["msg"], rid
    for name, preset in data["presets"].items():
        assert set(preset) - {"_comment"} <= ids, name


def test_rules_load_presets_overrides_and_filters(config_file):
    game, strict = Rules.load(), Rules.load(preset="strict")
    assert game["txd.size_max"].params["max"] == 2048 and strict["txd.size_max"].params["max"] == 512
    assert strict["dff.tris_budget"].params["budget"]["map"] == 1040
    assert game["txd.pow2"].sev == "warn" and strict["txd.pow2"].sev == "error"
    r = Rules.load(config=config_file({"txd.pow2": {"sev": "fatal"},
                                       "dff.tris_budget": {"params": {"budget": {"map": 5}}},
                                       "col.empty": {"enabled": False}}))
    assert r["txd.pow2"].sev == "fatal" and not r.on("col.empty")
    assert r["dff.tris_budget"].params["budget"] == {**game["dff.tris_budget"].params["budget"], "map": 5}
    only = Rules.load(only=["col", "link.texture_missing", "txd.mip*"])
    on = {k for k in only.rules if only.on(k)}
    assert "col.box_inverted" in on and "link.texture_missing" in on and "txd.mip_size" in on
    assert "link.col_missing" not in on and "dff.parse" not in on
    with pytest.raises(SatkError) as e:
        Rules.load(config=config_file({"txd.pwo2": {"sev": "warn"}}))
    assert e.value.code == "BAD_PARAMS" and "txd.pow2" in e.value.did_you_mean
    with pytest.raises(SatkError) as e:
        Rules.load(config=config_file({"txd.pow2": {"severity": "warn"}}))
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as e:
        Rules.load(only=["nothing.like.this"])
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as e:
        Rules.load(preset="strcit")
    assert e.value.code == "BAD_PARAMS" and "strict" in e.value.did_you_mean


def test_collector_formats_params_and_drops_disabled(config_file):
    c = Collector(Rules.load(config=config_file({"txd.pow2": {"enabled": False}})))
    c.add("txd.size_max", "x.txd", tex="a", w=4096, h=4096)
    c.add("txd.pow2", "x.txd", tex="a", w=3, h=3)
    assert [f.row() for f in c.findings] == [["txd.size_max", "warn", "x.txd", "texture 'a': 4096x4096 > 2048"]]
    with pytest.raises(KeyError):
        c.add("no.such_rule", "x")


# --------------------------------------------------------------------------- CLI


def test_cli_table_summary_and_filters(run_cli, mod: Mod):
    d = _bad_mod(mod)
    r = run_cli(["asset", "lint", str(d), "--no-index", "--json"])
    env = r.json
    assert r.code == 0 and env["ok"] is True
    assert env["cols"] == ["rule", "sev", "file", "msg"]
    assert env["summary"] == {"fatal": 0, "error": 1, "warn": 1, "info": 1}
    assert [row[:3] for row in env["rows"]] == [["link.txd_missing", "error", "gm/gm.ide"],
                                                 ["col.dup_name", "warn", "gm/gm_box.col"]]
    assert env["files"] == 10 and env["by_rule"]["link.txd_missing"] == 1
    assert env["root"].endswith(d.parent.name)
    info = run_cli(["asset", "lint", str(d), "--no-index", "--sev", "info", "--json"]).json
    assert info["total"] == 3 and info["rows"][-1][:2] == ["txd.empty", "info"]
    only = run_cli(["asset", "lint", str(d), "--no-index", "--rule", "col", "--json"]).json
    assert only["summary"] == {"fatal": 0, "error": 0, "warn": 1, "info": 0}
    p1 = run_cli(["asset", "lint", str(d), "--no-index", "--sev", "info", "--limit", "2", "--json"]).json
    assert p1["n"] == 2 and p1["next"] == "2"
    p2 = run_cli(["asset", "lint", str(d), "--no-index", "--sev", "info", "--limit", "2", "--cursor", "2",
                  "--json"]).json
    assert p2["n"] == 1 and p2["next"] is None and p2["rows"][0] == info["rows"][2]


def test_cli_fail_on_and_table_output(run_cli, mod: Mod):
    d = _bad_mod(mod)
    r = run_cli(["asset", "lint", str(d), "--no-index", "--fail-on", "error", "--json"])
    assert r.code == 1
    err = r.json["error"]
    assert err["code"] == "CHECK_FAILED" and err["data"]["rows"][0][0] == "link.txd_missing"
    assert run_cli(["asset", "lint", str(d), "--no-index", "--fail-on", "fatal"]).code == 0
    t = run_cli(["asset", "lint", str(d), "--no-index"], tty=True).out
    assert "rule" in t and "link.txd_missing" in t and "summary.error: 1" in t


def test_cli_save_writes_every_finding(run_cli, satk_home: Path, mod: Mod):
    d = _bad_mod(mod)
    env = run_cli(["asset", "lint", str(d), "--no-index", "--save", "--json"]).json
    p = Path(env["saved"])
    assert p.is_file() and p.parent == satk_home / "work" / "out" / "lint"
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert len(doc["rows"]) == 3 and doc["summary"] == env["summary"]
    again = run_cli(["asset", "lint", str(d), "--no-index", "--save", "--json"]).json
    assert again["saved"] == env["saved"]                     # same input -> same file


def test_lint_rules_listing(run_cli):
    env = run_cli(["asset", "lint-rules", "--json"]).json
    n = len(json.loads(rules_path().read_text(encoding="utf-8"))["rules"])
    assert env["total"] == n and env["cols"] == ["rule", "sev", "params", "what"]
    col = run_cli(["asset", "lint-rules", "--rule", "col", "--ru", "--ref", "--json"]).json
    assert col["cols"][-1] == "ref" and all(r[0].startswith("col.") for r in col["rows"])
    assert any(re.search("[а-я]", r[3]) for r in col["rows"])
    strict = {r[0]: r for r in run_cli(["asset", "lint-rules", "--preset", "strict", "--json"]).json["rows"]}
    assert strict["txd.pow2"][1] == "error" and '"max":512' in strict["txd.size_max"][2]


# --------------------------------------------------------------------------- targets


def test_targets_file_img_entry_and_errors(tmp_path: Path, mod: Mod):
    d = mod.write()
    one = lint(str(d / "gm_box.dff"), use_index=False)
    assert one.files == 1 and one.findings == []
    lone = lint(str(d / "gm_box.dff"), use_index=False, only=["dff"])
    assert lone.summary["warn"] == 0
    img = tmp_path / "pack.img"
    img.write_bytes(B.ver2([("gm_box.dff", mod.files["gm_box.dff"]), ("readme.txt", b"hi"),
                            ("broken.txd", struct.pack("<III", 0x16, 99999, 0x1803FFFF))]))
    rep = lint(str(img), use_index=False)
    assert rep.files == 2 and rep.skipped == 1
    assert [f.rule for f in rep.findings if f.sev == "fatal"] == ["txd.parse"]
    assert rep.at_least("fatal")[0].file == "pack.img/broken.txd"
    ent = lint(f"{img}/GM_BOX.DFF", use_index=False)
    assert ent.files == 1 and ent.findings == []
    for bad, code in ((str(tmp_path / "nope.dff"), "NOT_FOUND"), (f"{img}/nope.dff", "NOT_FOUND"),
                      (f"{img}/readme.txt", "UNSUPPORTED"), (str(tmp_path / "pack.img2"), "NOT_FOUND")):
        with pytest.raises(SatkError) as e:
            lint(bad, use_index=False)
        assert e.value.code == code, bad
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SatkError) as e:
        lint(str(tmp_path / "notes.txt"), use_index=False)
    assert e.value.code == "UNSUPPORTED"


def test_relative_paths_resolve_under_the_profile_root(satk_home: Path, mod: Mod):
    root = satk_home / "gta-sa-clean"
    (root / "models").mkdir(parents=True)
    (root / "models" / "gta3.img").write_bytes(B.ver2([("gm_box.dff", mod.files["gm_box.dff"])]))
    rep = lint("models/GTA3.IMG/gm_box.dff", use_index=False)
    assert rep.files == 1 and rep.root == root.as_posix()
    assert lint("file:models/gta3.img", use_index=False).files == 1


def test_deterministic(mod: Mod):
    d = _bad_mod(mod)
    a, b_ = lint(str(d), use_index=False), lint(str(d), use_index=False)
    assert [f.row() for f in a.findings] == [f.row() for f in b_.findings] and a.by_rule == b_.by_rule


# --------------------------------------------------------------------------- SIDs and the index


def _road_dff(*textures: str) -> bytes:
    return B.dff(mats=[B.material(t) for t in textures], night=True,
                 pos=[(-30.0, -20.0, 0.0), (30.0, -20.0, 0.0), (-30.0, 20.0, 0.0), (30.0, 20.0, 0.3)])


def _road_txd() -> bytes:
    return B.txd([B.native(n, w=64, h=64, levels=B.dxt1_chain(64, 64)) for n in ("plaintarmac1", "dt_road")])


def _road_col() -> bytes:
    dummies = b"".join(B.col3(f"dummy{i}", boxes=B.BOX) for i in range(37))
    return dummies + B.col3("lae2_roads89", boxes=(((-30.0, -20.0, -0.5), (30.0, 20.0, 0.5), 0),),
                            bounds=((-30.0, -20.0, -0.5), (30.0, 20.0, 0.5), (0.0, 0.0, 0.0), 37.0))


@pytest.fixture
def fake(tmp_path: Path):
    db = FakeIndexDB(root=tmp_path / "game", payloads={
        "dff:lae2_roads89": _road_dff("plaintarmac1", "dt_road", "lost_tex"),
        "txd:lae2roadshub": _road_txd(),
        "file:models/gta3.img/lae2_4.col": _road_col(),
    })
    with override_index(db):
        yield db


def test_model_sid_lints_its_files_and_links(fake):
    rep = lint("model:17613")
    labels = {f.file for f in rep.findings}
    assert rep.files == 3 and rep.summary["fatal"] == rep.summary["error"] == 0
    tm = [f for f in rep.findings if f.rule == "link.texture_missing"]
    assert len(tm) == 1 and "lost_tex" in tm[0].msg and tm[0].file == "models/gta3.img/lae2_roads89.dff"
    assert not any(f.rule == "link.col_orphan" for f in rep.findings)   # only model 37 of lae2_4.col
    assert labels <= {"models/gta3.img/lae2_roads89.dff", "models/gta3.img/lae2roadshub.txd",
                      "models/gta3.img/lae2_4.col"}


def test_other_sids(fake):
    assert lint("dff:lae2_roads89").files == 1
    assert lint("txd:lae2roadshub").findings == []
    col = lint("col:lae2_roads89")
    assert col.files == 1 and col.findings == []
    with pytest.raises(SatkError) as e:
        lint("model:999999")
    assert e.value.code == "NOT_FOUND"


def test_sid_needs_an_index(satk_home: Path):
    with pytest.raises(SatkError) as e:
        lint("model:411")
    assert e.value.code == "INDEX_MISSING"


def test_index_answers_names_outside_the_target(fake, mod: Mod):
    mod.ide = "objs\n18000, gm_box, lae2roadshub, 100, 0\nend\n"
    mod.files["gm_box.dff"] = B.dff(mats=[B.material("plaintarmac1"), B.material("gm_nowhere")], night=True)
    del mod.files["gm_txd.txd"]
    d = mod.write()
    with_ix = lint(str(d))
    rules = [f.rule for f in with_ix.findings]
    assert "link.txd_missing" not in rules
    tm = [f for f in with_ix.findings if f.rule == "link.texture_missing"]
    assert len(tm) == 1 and "gm_nowhere" in tm[0].msg and "plaintarmac1" not in tm[0].msg
    without = lint(str(d), use_index=False)
    assert "link.txd_missing" in [f.rule for f in without.findings]


def test_index_missing_is_a_warning_for_paths(satk_home: Path, mod: Mod):
    rep = lint(str(mod.write()))
    assert rep.findings == [] and rep.warn and rep.warn[0].startswith("INDEX_MISSING")


def test_lone_ide_without_index_checks_lines_not_links(mod: Mod):
    d = mod.write()
    rep = lint(str(d / "gm.ide"), use_index=False)
    assert rep.findings == []
    mod.ide = "objs\n18000, gm_box, gm_txd, 2, 0\nend\n"
    rep = lint(str(mod.write() / "gm.ide"), use_index=False)
    assert [f.rule for f in rep.findings] == ["ide.draw_min"]
