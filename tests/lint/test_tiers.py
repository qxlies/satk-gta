"""Detail-tier presets, class-aware severities, the class taxonomy, semantic vehicle rules and the reference-aware
answers (``--baseline``, ``--like``) of ``satk asset lint`` (wave A1, lane rules). Synthetic files only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.lint import baseline as BL
from satk.lint.dff import ModelDef, model_class
from satk.lint.rules import Collector, Finding, Rules
from satk.lint.runner import lint

from lint_synth import B, Mod

CARS = "cars\n400, gm_car, gm_car, car, PREMIER, PREMIER, null, richfamily, 10, 0, 0, -1, 0.7, 0.7, -1\nend\n"
FLAT = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0)]


def _car_frames(ped_arm_x: float = -0.4) -> tuple:
    return ((-1, "gm_car"), (0, "chassis_dummy"), (1, "chassis"), (0, "headlights", (0.6, 2.0, 0.3)),
            (0, "ped_frontseat", (-0.4, 0.3, 0.2)), (0, "ped_arm", (ped_arm_x, 0.3, 0.4)),
            (0, "wheel_lf_dummy", (-0.8, 1.4, 0.0)), (0, "wheel_rf_dummy", (0.8, 1.4, 0.0)),
            (0, "wheel_lb_dummy", (-0.8, -1.4, 0.0)), (0, "wheel_rb_dummy", (0.8, -1.4, 0.0)))


def _car_col() -> bytes:
    return B.col3("gm_car", spheres=((0.0, 0.0, 0.0, 1.0, 0),), shadow_verts=B.TRI_VERTS,
                  shadow_faces=((0, 1, 2, 0),))


def _car(paint_tex: str = "vehiclegrunge256", ped_arm_x: float = -0.4, env_uv: int = 2) -> bytes:
    mats = [B.material(paint_tex, rgba=(60, 255, 0, 255), env="xvehicleenv128"),
            B.material("vehiclelights128", rgba=(255, 175, 0, 255))]
    tris = [(0, 1, 2, 0), (2, 1, 3, 1)]
    nrm = [(-0.3, -0.3, 0.9), (0.3, -0.3, 0.9), (-0.3, 0.3, 0.9), (0.3, 0.3, 0.9)]
    geo = B.geometry([(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (-1.0, 1.0, 0.0), (1.0, 1.0, 0.3)], tris, mats=mats,
                     uv_sets=env_uv, normals=True, nrm=nrm, prelit=False)
    return B.clump([geo], frames=_car_frames(ped_arm_x), atomics=((2, 0),), col=_car_col())


def _car_mod(m: Mod, **kw) -> Path:
    m.ide += CARS
    m.files["gm_car.dff"] = _car(**kw)
    m.files["gm_car.txd"] = B.txd([B.native("gm_carint", w=64, h=64, levels=[bytes(2048)])])
    return m.write()


def _rules(rep) -> set[str]:
    return {f.rule for f in rep.findings}


# --------------------------------------------------------------------------- presets and classes


def test_tier_presets_build_on_each_other():
    game, van, plus, strict = (Rules.load(preset=p) for p in ("game", "vanilla", "sa_plus", "strict"))
    assert van["txd.size_max"].params["max"] == plus["txd.size_max"].params["max"] == 512   # sa_plus _base vanilla
    # triangle counts are never graded by the tier presets: info in vanilla, off in sa_plus
    for rid in ("dff.tris_budget", "veh.hd_tris", "veh.part_tris"):
        assert van[rid].sev == "info" and not plus.on(rid), rid
        assert game[rid].sev == "warn"                    # the performance hint for third-party mods stays
    assert not plus.on("dff.materials_budget") and van["dff.materials_budget"].sev == "info"
    assert van["dff.tris_budget"].params["budget"]["interior_shell"] > van["dff.tris_budget"].params["budget"]["map"]
    for r in (game, van, plus, strict):                  # the engine limits stay in every preset
        assert r.on("dff.verts_max") and r.on("dff.clump_ext_dup") and r["dff.clump_ext_dup"].sev == "error"
    assert not game.on("mat.alpha_draw_last") and plus.on("mat.alpha_draw_last") and strict.on("mat.alpha_draw_last")
    for r in (game, van, plus, strict):                  # vehicles, peds, weapons: mipmaps are never more than info
        assert {r["txd.mips_missing"].sev_for(c) for c in ("cars", "peds", "weap", "upgrade")} == {"info"}
    assert strict["txd.mips_missing"].sev_for("map") == "warn"


def test_class_sev_overrides_and_off(config_file):
    rs = Rules.load(config=config_file({"txd.pow2": {"class_sev": {"cars": "off", "map": "error"}}}))
    c = Collector(rs)
    c.add("txd.pow2", "a.txd", tex="t", w=3, h=3, cls="cars")
    c.add("txd.pow2", "b.txd", tex="t", w=3, h=3, cls="map")
    c.add("txd.pow2", "c.txd", tex="t", w=3, h=3)
    assert [(f.file, f.sev) for f in c.findings] == [("b.txd", "error"), ("c.txd", "warn")]
    with pytest.raises(SatkError):
        Rules.load(config=config_file({"txd.pow2": {"class_sev": {"cars": "loud"}}}))


def test_vehicle_txd_mips_are_info_even_in_strict(mod: Mod):
    d = _car_mod(mod)
    rep = lint(str(d), use_index=False, preset="strict")
    mips = [f for f in rep.findings if f.rule == "txd.mips_missing"]
    assert mips and {f.sev for f in mips} == {"info"}               # the car's own TXD
    mod.ide = "objs\n18000, gm_box, gm_txd, 100, 0\nend\n"
    mod.files = {"gm_txd.txd": B.txd([B.native("gm_wall", w=64, h=64, levels=[bytes(2048)])])}
    rep = lint(str(mod.write() / "gm_txd.txd"), use_index=False, preset="strict")
    assert [f.sev for f in rep.findings if f.rule == "txd.mips_missing"] == ["warn"]   # a lone map-ish TXD


def test_model_class_taxonomy():
    cl = Rules.load().classes

    def d(name, sec="objs", ide="data/maps/la/lae.ide", draw=100.0):
        return ModelDef(1, name, None, sec, draw, f"{ide}:1", ide=ide)

    assert model_class(cl, d("bribe"), None) == "pickup"
    assert model_class(cl, d("spl_a_l_b", ide="data/maps/veh_mods/veh_mods.ide"), None) == "upgrade"
    assert model_class(cl, d("gen_chair", ide="data/maps/interior/gen_int1.ide"), None) == "interior_prop"

    class Info:
        bbox = (0.0, 0.0, 0.0, 20.0, 10.0, 4.0)
        flags = 0
    assert model_class(cl, d("gen_room", ide="data/maps/interior/gen_int1.ide"), Info()) == "interior_shell"
    assert model_class(cl, d("lodhouse"), None) == "lod" and model_class(cl, d("house", draw=400.0), None) == "lod"
    assert model_class(cl, d("coach", sec="cars"), None, archive="player.img") == "other"   # clothes, not the bus
    assert model_class(cl, d("house"), None) == "map"


# --------------------------------------------------------------------------- semantic vehicle rules


def test_a_vanilla_like_car_has_no_semantic_findings(mod: Mod):
    rep = lint(str(_car_mod(mod)), use_index=False)
    sem = {r for r in _rules(rep) if r.startswith(("veh.", "dff.flat", "dff.vert"))}
    assert sem == set(), [f.row() for f in rep.findings]
    assert rep.checked["veh.frames"] == rep.checked["veh.paint_dirt"] == rep.checked["veh.env_uv2"] == 1


def test_a_car_built_like_the_benchmark_triggers_the_semantic_rules(mod: Mod):
    rep = lint(str(_car_mod(mod, paint_tex="vehiclegeneric256", ped_arm_x=0.81, env_uv=1)), use_index=False)
    assert {"veh.paint_dirt", "veh.dummy_side", "veh.env_uv2"} <= _rules(rep)
    side = next(f for f in rep.findings if f.rule == "veh.dummy_side")
    assert "ped_arm x=+0.81" in side.msg


def test_damage_parts_like_vanilla_pass_and_budgets_count_the_hd_model(mod: Mod, config_file):
    """A _dam part about as heavy as its _ok twin (vanilla median 0.95) is fine; the budget counts veh.hd_tris
    (chassis + _ok parts + the wheel once), so heavy _dam parts never push a car over it."""
    mod.ide += CARS
    quad = [(0, 1, 2, 0), (2, 1, 3, 0)]
    geoms = [B.geometry(B.QUAD, quad * 13, normals=True), B.geometry(B.QUAD, quad * 12, normals=True),
             B.geometry(B.QUAD, quad * 20, normals=True), B.geometry(B.QUAD, quad * 19, normals=True)]
    mod.files["gm_car.dff"] = B.clump(geoms, frames=((-1, "gm_car"), (0, "door_lf_ok"), (0, "door_lf_dam"),
                                                     (0, "bump_front_ok"), (0, "bump_front_dam")),
                                      atomics=((1, 0), (2, 1), (3, 2), (4, 3)))
    cfg = config_file({"veh.hd_tris": {"params": {"budget": {"default": 70}}}})
    rep = lint(str(mod.write()), use_index=False, config=cfg)
    assert rep.checked["veh.dam_ratio"] == 1 and "veh.dam_ratio" not in _rules(rep)
    assert "veh.hd_tris" not in _rules(rep)               # hd = 26 + 40 = 66 <= 70; the file has 128
    rep = lint(str(mod.dir), use_index=False, config=config_file({"veh.hd_tris": {"params": {"budget": {"default": 65}}}}))
    assert "veh.hd_tris" in _rules(rep)


# --------------------------------------------------------------------------- --baseline / --like


def test_apply_baseline_drops_rules_vanilla_does_too():
    rates = {"rules": {"txd.mips_missing": {"rate": 0.8}, "veh.paint_dirt": {"rate": 0.007}}}
    fs = [Finding("txd.mips_missing", "info", "a.txd", "m"), Finding("veh.paint_dirt", "warn", "a.dff", "p"),
          Finding("txd.mips_missing", "info", "b.txd", "m")]
    kept, dropped = BL.apply_baseline(fs, rates, 0.05)
    assert [f.rule for f in kept] == ["veh.paint_dirt"] and dropped == {"txd.mips_missing": 2}
    assert BL.rate_of(rates, "veh.paint_dirt") == 0.007 and BL.rate_of(rates, "nothing") == 0.0


def _game(root: Path) -> Path:
    g = root / "game"
    (g / "data").mkdir(parents=True)
    (g / "models").mkdir()
    (g / "data" / "default.dat").write_text("IDE DATA\\DEFAULT.IDE\n", encoding="latin-1")
    (g / "data" / "gta.dat").write_text("", encoding="latin-1")
    (g / "data" / "default.ide").write_text("objs\n18000, gm_box, gm_txd, 100, 0\nend\n", encoding="latin-1")
    (g / "models" / "gta3.img").write_bytes(B.ver2([
        ("gm_box.dff", B.dff(night=True)),
        ("gm_txd.txd", B.txd([B.native("gm_wall", w=64, h=64, levels=[bytes(2048)])])),
        ("gm_box.col", B.col3("gm_box", boxes=B.BOX))]))
    return g


def test_rule_rates_are_cached_per_preset(satk_home: Path, monkeypatch):
    g = _game(satk_home)
    monkeypatch.setattr(BL, "profile_root", lambda profile: g)
    monkeypatch.setattr(BL, "_index_hash", lambda profile: "test")
    r1 = BL.rule_rates("vanilla", "game")
    assert r1["rules"]["txd.mips_missing"] == {"fired": 1, "checked": 1, "rate": 1.0}
    assert r1["rules"]["dff.tris_budget"]["checked"] == 1
    cache = list((satk_home / "work" / "cache" / "lint").glob("rates-vanilla-game-*.json"))
    assert len(cache) == 1

    def boom(*a, **k):
        raise AssertionError("the cache was not used")
    import satk.lint.runner as runner
    monkeypatch.setattr(runner, "lint", boom)
    assert BL.rule_rates("vanilla", "game") == r1
    with pytest.raises(AssertionError):
        BL.rule_rates("vanilla", "strict")                       # another preset is another cache entry


def test_cli_baseline_adds_the_rate_column_and_suppresses(run_cli, satk_home: Path, mod: Mod, monkeypatch):
    g = _game(satk_home)
    monkeypatch.setattr(BL, "profile_root", lambda profile: g)
    monkeypatch.setattr(BL, "_index_hash", lambda profile: "test")
    d = _car_mod(mod, paint_tex="vehiclegeneric256")
    env = run_cli(["asset", "lint", str(d), "--no-index", "--baseline", "vanilla", "--sev", "info", "--json"]).json
    assert env["ok"] and env["cols"][-1] == "vanilla_rate"
    assert env["suppressed"] == {"txd.mips_missing": 1}             # vanilla does it on 100 % of its textures
    rows = {r[0]: r for r in env["rows"]}
    assert rows["veh.paint_dirt"][-1] == 0.0
    assert "veh.paint_dirt" in env["hints"]
    plain = run_cli(["asset", "lint", str(d), "--no-index", "--sev", "info", "--json"]).json
    assert "txd.mips_missing" in {r[0] for r in plain["rows"]} and len(plain["cols"]) == 4
    bad = run_cli(["asset", "lint", str(d), "--no-index", "--baseline", "vanilla", "--baseline-rate", "2", "--json"])
    assert bad.json["error"]["code"] == "BAD_PARAMS"


def test_cli_like_drops_what_the_reference_triggers(run_cli, mod: Mod, monkeypatch):
    d = _car_mod(mod, paint_tex="vehiclegeneric256")
    monkeypatch.setattr(BL, "like_rules", lambda sid, **kw: {"veh.paint_dirt"})
    env = run_cli(["asset", "lint", str(d), "--no-index", "--like", "model:426", "--sev", "info", "--json"]).json
    assert env["like"] == "model:426" and env["suppressed"] == {"veh.paint_dirt": 1}
    assert "veh.paint_dirt" not in {r[0] for r in env["rows"]}


def test_lint_rules_lists_the_tier_presets(run_cli):
    env = run_cli(["asset", "lint-rules", "--preset", "vanilla", "--rule", "veh", "--ref", "--json"]).json
    rows = {r[0]: r for r in env["rows"]}
    assert rows["veh.hd_tris"][1] == "info" and json.loads(rows["veh.part_tris"][2])["budget"]["chassis"] == 2600
    assert rows["veh.frames"][-1].startswith("crash 0x004C7DAD")
    plus = run_cli(["asset", "lint-rules", "--preset", "sa_plus", "--rule", "veh", "--json"]).json
    assert "veh.hd_tris" not in {r[0] for r in plus["rows"]} or \
        any(r[0] == "veh.hd_tris" and r[1] in ("off", "info") for r in plus["rows"])
