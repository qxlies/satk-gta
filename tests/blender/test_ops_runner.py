"""satk.blender ops and runner without starting Blender (the subprocess is simulated)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from satk.blender import ops as O
from satk.blender import runner as RN
from satk.core.errors import SatkError
from satk.core.registry import all_ops, get_op, op_by_mcp


# --------------------------------------------------------------------------- registry


def test_ops_registered():
    names = {o.name for o in all_ops() if o.name.startswith("blender.")}
    assert names == {"blender.doctor", "blender.import_model", "blender.import_area", "blender.render",
                     "blender.export", "blender.addon_build", "blender.run", "blender.game_ready"}
    mcp = [o for o in all_ops() if o.name.startswith("blender.") and o.mcp_name]
    assert [o.mcp_name for o in mcp] == ["blender_job"]  # one MCP tool (SPEC §4.7 #24)
    spec = op_by_mcp("blender_job")
    assert spec.long_running and len(spec.summary) <= 300
    for o in all_ops():
        if o.name.startswith("blender."):
            assert len(o.summary) <= 300 and o.summary_ru


def test_cli_names(run_cli):
    r = run_cli(["blender", "import-area", "-h"])
    assert r.code == 0 and "--center" in r.out and "--box" in r.out


# --------------------------------------------------------------------------- argument checks (no Blender)


def test_export_out_protected(run_cli, satk_home, tmp_path):
    fake = tmp_path / "scene.blend"
    fake.write_bytes(b"BLENDER")
    r = run_cli(["blender", "export", "--blend", str(fake), "--objects", "x", "--out", str(satk_home / "gta-sa-clean" / "x")])
    assert r.code == 1 and r.json["error"]["code"] == "PROTECTED_PATH"
    r = run_cli(["blender", "export", "--blend", str(fake), "--objects", "x", "--out", str(tmp_path / "elsewhere")])
    assert r.json["error"]["code"] == "PROTECTED_PATH" and "only under" in r.json["error"]["msg"]
    assert not (tmp_path / "elsewhere").exists()


def test_bad_params(run_cli, satk_home):
    r = run_cli(["blender", "import-area", "--box", "100"])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["blender", "render"])
    assert r.json["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["blender", "render", "--blend", "D:/nope/x.blend"])
    assert r.json["error"]["code"] == "NOT_FOUND"
    r = run_cli(["blender", "import-area", "--center", "1,2", "--box", "1,2,3"])
    assert r.json["error"]["code"] == "BAD_PARAMS"


def test_pose_helper():
    assert O._pose([1, 2, 3], [4, 5, 6], None, None, 70) == ({"pos": [1, 2, 3], "look": [4, 5, 6]}, None)
    assert O._pose([1, 2, 3], None, None, None, 70)[0]["ypr"] == [0.0, -20.0, 0.0]
    assert O._pose(None, None, None, None, 70) == (None, None)
    with pytest.raises(SatkError):
        O._pose(None, [1, 2, 3], None, None, 70)
    with pytest.raises(SatkError):
        O._pose([1, 2, 3], [1, 1, 1], [0, 0, 0], None, 70)


# --------------------------------------------------------------------------- jobs (simulated Blender)


@pytest.fixture
def sim(satk_home, monkeypatch):
    """Replace the Blender subprocess: ``sim.reply`` decides what the fake Blender writes."""
    calls: list[dict] = []

    class Sim:
        reply = staticmethod(lambda req: {"ok": True, "cmd": req["cmd"], "files": {}, "stats": {"x": 1}, "warnings": []})

    def fake_run_blender(argv, *, log, timeout, env, cwd=None):
        req_path = Path(argv[argv.index("--") + 1])
        req = json.loads(req_path.read_text(encoding="utf-8"))
        calls.append({"argv": argv, "req": req, "env": env})
        log.write_text("fake blender log\nline 2\n", encoding="utf-8")
        resp = Sim.reply(req)
        if resp is not None:
            (req_path.parent / "response.json").write_text(json.dumps(resp), encoding="utf-8")
        return (0 if resp and resp.get("ok") else 1), 0.01

    monkeypatch.setattr(RN, "run_blender", fake_run_blender)
    monkeypatch.setattr(RN, "ensure_dragonff", lambda force=False: {"ok": True, "commit": RN.DRAGONFF_COMMIT, "path": "x"})
    Sim.calls = calls
    return Sim


def test_run_job_ok(sim):
    r = RN.run_job("render", {"blend": "a.blend", "pose": {"pos": [0, 0, 10], "look": [0, 10, 0]}}, blend="a.blend")
    c = sim.calls[0]
    assert c["argv"][:2] == ["-b", "a.blend"] and "--factory-startup" in c["argv"]
    assert c["argv"][c["argv"].index("--python") + 1].endswith("agent_cli.py")
    req = c["req"]
    assert req["contract"] == "satk-blender/1" and req["cmd"] == "render" and req["profile"] == "vanilla"
    assert req["args"]["size"] == [960, 540] and req["satk_src"].endswith("/src")
    assert r["ok"] and r["job"] == req["job"] and r["stats"]["x"] == 1 and "process_s" in r["stats"]
    env = c["env"]
    prof = str(RN.profile_dir())
    for k in ("BLENDER_USER_CONFIG", "BLENDER_USER_SCRIPTS", "BLENDER_USER_EXTENSIONS"):
        assert env[k].startswith(prof)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1" and "PYTHONPATH" not in env
    assert env["TEMP"].startswith(str(Path(req["out_dir"])))


def test_run_job_error_response(sim):
    sim.reply = staticmethod(lambda req: {"ok": False, "cmd": req["cmd"], "error": {"code": "NOT_FOUND", "msg": "nothing"},
                                          "warnings": []})
    with pytest.raises(SatkError) as e:
        RN.run_job("export", {"blend": "a.blend", "objects": ["x"]})
    assert e.value.code == "NOT_FOUND" and e.value.data["job"]


def test_run_job_no_response(sim):
    sim.reply = staticmethod(lambda req: None)
    with pytest.raises(SatkError) as e:
        RN.run_job("doctor", {})
    assert e.value.code == "EXTERNAL_TOOL" and "line 2" in e.value.data["tail"]


def test_run_job_bad_args(sim):
    with pytest.raises(SatkError) as e:
        RN.run_job("import_area", {"center": [0, 0]})
    assert e.value.code == "BAD_PARAMS" and not sim.calls


def test_blender_job_dispatch(sim, monkeypatch):
    from satk.blender import resolve

    seen = {}

    def fake_plan_area(**kw):
        seen.update(kw)
        return {"source": "direct", "query": {"center": kw["center"], "box": kw["box"], "r": None}, "insts": [{"sid": "inst:a#0"}],
                "models": [], "warnings": []}

    monkeypatch.setattr(resolve, "plan_area", fake_plan_area)
    r = get_op("blender.run").call({"cmd": "import_area", "args": {"center": [2495, -1687], "box": 100, "lod": "all",
                                                                   "area": "any", "match": "center"}})
    assert r["ok"] and r["cmd"] == "import_area" and r["source"] == "direct"
    assert seen["box"] == [2445.0, -1737.0, 2545.0, -1637.0] and seen["area"] is None and seen["lod"] == "all"
    req = sim.calls[-1]["req"]
    assert req["plan"]["insts"] == [{"sid": "inst:a#0"}]

    monkeypatch.setattr(resolve, "plan_area", lambda **kw: dict(fake_plan_area(**kw), insts=[]))
    with pytest.raises(SatkError) as e:
        get_op("blender.run").call({"cmd": "import_area", "args": {"center": [0, 0], "r": 5}})
    assert e.value.code == "NOT_FOUND"


def test_render_visible_is_cut(sim, tmp_path):
    rows = [[f"inst:x#{i}", "m", 0.01] for i in range(30)]
    sim.reply = staticmethod(lambda req: {"ok": True, "cmd": "render", "files": {}, "stats": {}, "warnings": [],
                                          "visible": {"cols": ["id", "name", "share"], "rows": rows}})
    b = tmp_path / "s.blend"
    b.write_bytes(b"x")
    r = get_op("blender.run").call({"cmd": "render", "args": {"blend": str(b), "pose": {"pos": [0, 0, 5], "look": [0, 1, 0]},
                                                              "size": [320, 200]}})
    assert r["visible"]["n"] == 20 and r["visible"]["total"] == 30
    req = sim.calls[-1]["req"]
    assert req["args"]["size"] == [320, 200] and req["args"]["pose"]["fov_h_deg"] == 70.0


# --------------------------------------------------------------------------- DragonFF extraction


def test_ensure_dragonff_needs_clone(satk_home):
    with pytest.raises(SatkError) as e:
        RN.ensure_dragonff()
    assert e.value.code == "NOT_READY"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_ensure_dragonff_git_archive(satk_home, monkeypatch):
    from satk.core.paths import cfg

    repo = Path(cfg().paths.src) / "DragonFF"
    (repo / "ops").mkdir(parents=True)
    (repo / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "ops" / "a.py").write_text("y = 2\n", encoding="utf-8")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "x"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True)
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    monkeypatch.setattr(RN, "DRAGONFF_SHA", sha)
    monkeypatch.setattr(RN, "DRAGONFF_COMMIT", sha[:7])
    before = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout
    info = RN.ensure_dragonff()
    assert info["ok"] and info["commit"] == sha[:7]
    d = RN.dragonff_dir()
    assert (d / "ops" / "a.py").read_text(encoding="utf-8") == "y = 2\n"
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout == before
    assert RN.ensure_dragonff() == info  # idempotent


# --------------------------------------------------------------------------- export (simulated Blender)


def _blend(tmp_path) -> Path:
    b = tmp_path / "scene.blend"
    b.write_bytes(b"BLENDER")
    return b


def test_export_bad_name_creates_nothing(sim, tmp_path):
    from satk.core.paths import cfg

    wk = Path(cfg().paths.work)
    for bad in ("../../gta-sa-clean/x", "a b"):
        with pytest.raises(SatkError) as e:
            get_op("blender.run").call({"cmd": "export", "args": {"blend": str(_blend(tmp_path)), "objects": ["x"],
                                                                  "name": bad}})
        assert e.value.code == "BAD_PARAMS"
    assert not sim.calls and not (wk / "gta-sa-clean").exists() and not (wk / "out" / "exports").exists()


def test_export_not_found_leaves_no_folder(sim, tmp_path):
    from satk.core.paths import cfg

    sim.reply = staticmethod(lambda req: {"ok": False, "cmd": "export", "warnings": [],
                                          "error": {"code": "NOT_FOUND", "msg": "nothing to export for 'lae2_roadz89'"}})
    with pytest.raises(SatkError) as e:
        O.blender_export(blend=str(_blend(tmp_path)), objects=["lae2_roadz89"])
    assert e.value.code == "NOT_FOUND" and sim.calls
    assert sim.calls[-1]["req"]["args"]["out"].endswith("/out/exports/lae2_roadz89")
    assert not (Path(cfg().paths.work) / "out" / "exports" / "lae2_roadz89").exists()


def test_export_completes_old_scenes_from_game_data(sim, tmp_path):
    """A .blend imported before the IDE data was kept: draw distance, flags and iflags from the index."""
    from satk.index.api import FakeIndexDB, override_index

    def reply(req):
        out = Path(req["args"]["out"])
        out.mkdir(parents=True, exist_ok=True)  # the Blender side creates it after resolving the objects
        model = {"name": "lae2_roads89", "sid": "model:17613", "id": 17613, "sec": "objs", "files": {},
                 "insts": [{"sid": "inst:lae2_stream0#4", "pos": [2489.3, -1668.5, 12.3], "q": [0, 0, 0, 1], "area": 0,
                            "rot_zxy_deg": [0, 0, 0]}]}
        (out / "export.json").write_text(json.dumps({"target": "mta-resource", "profile": "vanilla", "models": [model]}),
                                         encoding="utf-8")
        return {"ok": True, "cmd": "export", "files": {"dir": str(out)}, "stats": {"models": 1}, "warnings": []}

    sim.reply = staticmethod(reply)
    with override_index(FakeIndexDB(root=tmp_path / "game")):
        r = O.blender_export(blend=str(_blend(tmp_path)), objects=["lae2_roads89"])
    out = Path(r["out"])
    assert "17613, lae2_roads89, lae2_roads89, 150, 1" in (out / "lae2_roads89.ide").read_text(encoding="utf-8")
    m = json.loads((out / "export.json").read_text(encoding="utf-8"))["models"][0]
    assert m["ide"] == {"sec": "objs", "draw": 150.0, "flags": 1} and m["insts"][0]["iflags"] == 0
    assert not any(w.startswith("IDE_DEFAULTS") for w in r.get("warn", []))
