"""kit ops through the CLI (catalog, scene contract) and the registry rules (mcp=False, summaries)."""

from __future__ import annotations

import json
from pathlib import Path

from satk.core.registry import get_op

KIT_OPS = ("kit.kinds", "kit.template", "kit.export", "kit.scene_spec")


def _run(run_cli, *argv) -> tuple[int, dict]:
    r = run_cli(list(argv))
    return r.code, json.loads(r.out) if r.out.strip() else {}


def test_kit_ops_are_cli_only_with_english_summaries():
    for name in KIT_OPS:
        spec = get_op(name)
        assert spec.mcp is False, name
        assert len(spec.summary) <= 300 and spec.summary.isascii(), name
    choices = get_op("blender.game_ready").param("asset_class").choices
    assert choices[:2] == ("prop", "building") and "overlay" in choices


def test_kinds_cli(run_cli):
    code, env = _run(run_cli, "kit", "kinds")
    assert code == 0, env
    assert env["total"] == 21 and env["cols"][0] == "kind"
    kinds = {row[0]: row for row in env["rows"]}
    assert kinds["automobile"][2] == "model:426" and kinds["weapon"][3] == "colt45"


def test_kinds_details_and_tables(run_cli):
    code, env = _run(run_cli, "kit", "kinds", "--kind", "heli")
    assert code == 0 and env["kind"] == "heli" and "moving_rotor" in env["frames"]["required"]
    _code, env = _run(run_cli, "kit", "kinds", "--materials", "--limit", "100")
    roles = {row[0] for row in env["rows"]}
    assert {"paint1", "glass", "lamp_fl", "map", "ped", "weapon", "gunflash"} <= roles
    _code, env = _run(run_cli, "kit", "kinds", "--atlas", "--limit", "100")
    assert env["total"] == len(env["rows"]) >= 20
    _code, env = _run(run_cli, "kit", "kinds", "--classes")
    assert {row[0] for row in env["rows"]} == {"prop", "building", "terrain", "vegetation", "interior_prop",
                                               "interior_shell", "pickup", "overlay"}
    code, env = _run(run_cli, "kit", "kinds", "--kind", "helii")
    assert code != 0 and env["error"]["code"] == "NOT_FOUND" and "heli" in env["error"].get("did_you_mean", [])


def test_scene_spec_cli(run_cli):
    code, env = _run(run_cli, "kit", "scene-spec", "--kind", "weapon")
    assert code == 0 and env["kind"] == "weapon" and "dragonff" in env["contract"]


def test_export_without_session_or_blend_says_how(run_cli, satk_home):
    code, env = _run(run_cli, "kit", "export", "--add")
    assert code != 0 and env["error"]["code"] == "BAD_PARAMS" and "--blend" in env["error"]["hint"]
    code, env = _run(run_cli, "kit", "export", "--add", "--replace", "model:426", "--session", "x")
    assert code != 0 and env["error"]["code"] == "BAD_PARAMS"


def test_modadd_kind():
    from satk.kit.export import modadd_kind

    assert [modadd_kind(k) for k in ("automobile", "bike", "ped", "weapon", "prop", "vehicle_upgrade", "pickup")] \
        == ["vehicle", "vehicle", "ped", "weapon", "object", "object", "object"]


def test_export_check_summary_reads_the_asset_check_envelope(monkeypatch):
    """kit.export keeps asset.check's verdict, counts and its blocking/out-of-band rows (not edge/ok/info)."""
    from satk.kit import export as E

    rows = [["veh.frames", "", 1, None, None, None, "error", "h", "r"],
            ["shade.normal_bend", "", 0.8, 6, 10, 15, "low", "h", "r"],
            ["geo.tris", "", 2000, 1500, 2100, 2600, "edge", "h", "r"],
            ["dims.L", "", 5.1, 4.8, 5.2, 5.6, "ok", "h", "r"],
            ["col.check", "", 1, None, None, None, "info", "h", "r"]]

    class Spec:
        def param(self, name):
            if name in ("like", "tier"):
                return object()
            raise KeyError(name)

        def call(self, args):
            return {"ok": True, "rows": rows, "verdict": "fail", "out_of_band": 1,
                    "counts": {"error": 1, "low": 1, "edge": 1, "ok": 1, "info": 1}}

    monkeypatch.setattr(E, "_op", lambda name: Spec())
    warn: list[str] = []
    got = E._check(Path("x.dff"), "model:426", "sa_plus", warn)
    assert got["verdict"] == "fail" and got["counts"]["error"] == 1 and got["out_of_band"] == 1
    assert [r[0] for r in got["rows"]] == ["veh.frames", "shade.normal_bend"] and got["total"] == 5
    assert not warn
