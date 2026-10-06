"""``satk shader textures|new|check`` through the CLI (synthetic index, isolated workspace)."""

from __future__ import annotations

from pathlib import Path

import pytest


def _ok(r):
    assert r.code == 0, r.out + r.err
    return r.json


def test_textures_by_kind_with_plan(index, run_cli):
    env = _ok(run_cli(["shader", "textures", "--kind", "road", "--json"]))
    assert env["cols"] == ["name", "txd", "txds", "models", "inst"]
    assert {r[0] for r in env["rows"]} == {"dt_road_stoplinea", "plaintarmac1"}
    plan = env["plan"]
    assert plan["exact"] and plan["patterns"] == len(env["plan_rows"])
    from satk.shader.names import load_world
    from satk.shader.wild import resolve

    w = load_world(index)
    assert resolve([("apply", p) for p in plan["apply"]] + [("remove", p) for p in plan["remove"]], w.names) == \
        {"dt_road_stoplinea", "plaintarmac1"}
    assert env["select"][0] == "kind road: 2"


def test_textures_patterns_are_the_plan_and_paging(index, run_cli):
    env = _ok(run_cli(["shader", "textures", "vehicle*", "infernus*", "--limit", "3", "--json"]))
    assert env["n"] == 3 and env["total"] > 3 and env["next"] == "3"
    assert env["plan"]["apply"] == ["vehicle*", "infernus*"] and not env["plan"]["remove"]
    assert [r[1] for r in env["plan_rows"]] == ["vehicle*", "infernus*"]
    page2 = _ok(run_cli(["shader", "textures", "vehicle*", "infernus*", "--limit", "3", "--cursor", "3", "--json"]))
    assert not {r[0] for r in page2["rows"]} & {r[0] for r in env["rows"]}


def test_textures_model_spill_and_hint(index, run_cli):
    env = _ok(run_cli(["shader", "textures", "--model", "model:1233", "--json"]))
    assert {r[0] for r in env["rows"]} == {"roadsign01_128", "glass_64", "ab_window"}
    assert env["spill"]["names"] == 1 and env["spill"]["other_models"] == 2
    env = _ok(run_cli(["shader", "textures", "--kind", "vehicle", "--json"]))
    assert "vehicle" in env["hint"] and "*" in env["hint"]
    env = _ok(run_cli(["shader", "textures", "--area", "2489.3", "-1668.5", "30", "--json"]))
    assert env["cols"][-1] == "inside"


def test_textures_errors(index, run_cli):
    r = run_cli(["shader", "textures", "--json"])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["shader", "textures", "--kind", "roads", "--json"])
    assert r.code == 2
    env = _ok(run_cli(["shader", "textures", "zzz_nothing*", "--json"]))
    assert env["total"] == 0 and any(w.startswith("EMPTY") for w in env["warn"])
    r = run_cli(["shader", "textures", "--model", "model:99999", "--json"])
    assert r.code != 0 and r.json["error"]["code"] in ("NOT_FOUND", "BAD_ID")


def test_new_wet_roads_writes_a_checked_resource(index, run_cli, satk_home):
    env = _ok(run_cli(["shader", "new", "wet_roads", "--name", "wr", "--fxc", "none", "--json"]))
    out = Path(env["dir"])
    assert out == satk_home / "work" / "out" / "shader" / "wr"
    assert sorted(p.name for p in out.iterdir()) == ["client.lua", "meta.xml", "wet_roads.fx"]
    assert env["textures"]["names"] == 2 and env["textures"]["exact"]
    assert env["check"]["status"] == "ok" and env["check"]["compiler"] == "none"
    lua = (out / "client.lua").read_text(encoding="utf-8")
    assert "satk shader textures --kind road" in lua and 'local ELEMENT_TYPES = "world,object"' in lua
    chk = _ok(run_cli(["shader", "check", str(out), "--fxc", "none", "--json"]))
    assert chk["status"] == "ok" and chk["lua"]["patterns"]


def test_new_texture_replace_needs_a_selection_and_copies_the_image(index, run_cli, tmp_path):
    r = run_cli(["shader", "new", "texture_replace", "--json"])
    assert r.code == 2 and "--textures" in r.json["error"]["hint"]
    img = tmp_path / "mine.png"
    from satk.shader.templates import checker_png

    img.write_bytes(checker_png(8, 2))
    env = _ok(run_cli(["shader", "new", "texture_replace", "--name", "tr", "--textures", "vent_64", "--image",
                       str(img), "--fxc", "none", "--json"]))
    out = Path(env["dir"])
    assert (out / "texture.png").read_bytes() == img.read_bytes()
    assert env["textures"]["apply"] == 1 and "warn" not in env
    env = _ok(run_cli(["shader", "new", "texture_replace", "--name", "tr2", "--model", "txd:bistro", "--fxc", "none",
                       "--json"]))
    assert any(w.startswith("PLACEHOLDER") for w in env["warn"])
    assert (Path(env["dir"]) / "texture.png").read_bytes()[:4] == b"\x89PNG"


def test_new_without_index_uses_kind_fallback(satk_home, run_cli):
    env = _ok(run_cli(["shader", "new", "wet_roads", "--name", "fb", "--fxc", "none", "--json"]))
    assert any(w.startswith("INDEX_MISSING") for w in env["warn"]) and not env["textures"]["exact"]
    lua = (Path(env["dir"]) / "client.lua").read_text(encoding="utf-8")
    assert '"*road*"' in lua
    env = _ok(run_cli(["shader", "new", "post_process", "--name", "pp", "--textures", "x*", "--fxc", "none", "--json"]))
    assert any(w.startswith("UNUSED") for w in env["warn"])
    env = _ok(run_cli(["shader", "new", "ped_shading", "--fxc", "none", "--json"]))
    assert env["textures"]["apply"] == 1 and Path(env["dir"]).name == "ped_shading"


def test_check_strict_and_missing(run_cli, write, satk_home):
    bad = write("bad.fx", "texture t;\ntechnique10 x { pass P0 { } }\n")
    env = _ok(run_cli(["shader", "check", str(bad), "--fxc", "none", "--json"]))
    assert env["status"] == "fail" and env["rows"][0][0] == "error"
    r = run_cli(["shader", "check", str(bad), "--fxc", "none", "--strict", "--json"])
    assert r.code == 1 and r.json["error"]["code"] == "CHECK_FAILED"
    r = run_cli(["shader", "check", str(bad.parent / "nope.fx"), "--json"])
    assert r.json["error"]["code"] == "NOT_FOUND"


@pytest.mark.engine
def test_new_every_template_compiles(index, run_cli, fxc):
    for t in ("car_paint", "water", "sky_fog", "ped_shading", "post_process", "wet_roads"):
        env = _ok(run_cli(["shader", "new", t, "--fxc", str(fxc), "--json"]))
        assert env["check"]["status"] == "ok" and env["check"]["compiler"] == "fxc", (t, env)
