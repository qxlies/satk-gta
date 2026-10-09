"""Asset projects (K7), reference photos, journal replay planning, launcher and client helpers - no Blender."""

from __future__ import annotations

import json
import os
import time

import pytest

from satk.core.errors import SatkError
from satk.core.registry import get_op
from satk.saap import client as C
from satk.studio import api
from satk.studio import launcher as L
from satk.studio import project as P
from satk.studio.replay import plan_actions

# --------------------------------------------------------------------------- asset.init / asset.status


def test_init_record_status(satk_home, run_cli):
    r = run_cli(["asset", "init", "bench1", "--kind", "prop", "--dims", "1.8,0.6,0.9"])
    assert r.code == 0, r.out
    j = r.json
    assert j["name"] == "bench1" and j["tier"] == "sa_plus" and j["dims"] == [1.8, 0.6, 0.9]
    pdir = satk_home / "work" / "assets" / "bench1"
    data = json.loads((pdir / "asset.json").read_text(encoding="utf-8"))
    assert data["asset"] == 1 and data["kind"] == "prop" and data["intent"] == "add" and data["target"] == "sp"
    assert data["gates"] == {g: "open" for g in P.GATES} and data["dims"]["source"] == "given"
    assert all((pdir / d).is_dir() for d in ("refs", "snaps", "checkpoints"))
    again = run_cli(["asset", "init", "bench1", "--kind", "prop"])
    assert again.json["error"]["code"] == "EXISTS"
    P.record("bench1", {"decision": "slats are an ARRAY of one bevelled box", "step": 7})
    P.record("bench1", {"gate": "G0", "state": "done"})
    P.record("bench1", {"issue": "the legs read too thin at 30 m"})
    P.record("bench1", {"check": {"rows": 3, "out_of_band": ["shade.flat_share"]}})
    card = P.status("bench1")
    assert card["gates"] == "G0+ G1- G2- G3- G4- G5-" and card["next_gate"].startswith("G1: blockout")
    assert card["decisions"] == ["slats are an ARRAY of one bevelled box"]
    assert card["issues"] == ["#1 the legs read too thin at 30 m"] and "last_check" in card
    assert card["resume"] == "satk blender session start --project bench1"
    P.record("bench1", {"close": 1})
    assert "issues" not in P.status("bench1")
    for bad in ({}, {"gate": "G9"}, {"gate": "G1", "state": "maybe"}, {"close": 5}, {"what": 1},
                {"dims": [1, 2]}, {"check": "text"}):
        with pytest.raises(SatkError):
            P.record("bench1", bad)
    many = {"issue": "x" * 300}
    for _ in range(12):
        P.record("bench1", many)
        P.record("bench1", {"decision": "y" * 300})
    big = P.status("bench1")
    assert len(json.dumps(big, ensure_ascii=False, separators=(",", ":")).encode()) <= P.MAX_CARD


def test_init_validation_and_folders(satk_home, run_cli):
    r = run_cli(["asset", "init", "x1", "--kind", "automobil"])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "automobile" in r.json["error"]["did_you_mean"]
    r = run_cli(["asset", "init", "x1", "--kind", "automobile", "--intent", "replace"])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "--like" in r.json["error"]["msg"]
    with pytest.raises(SatkError) as ei:
        P.project_dir(str(satk_home.parent / "elsewhere"))
    assert ei.value.code == "PROTECTED_PATH"
    inside = satk_home / "work" / "my" / "car"
    assert P.project_dir(str(inside)) == inside
    assert P.project_dir("Car_2") == satk_home / "work" / "assets" / "car_2"
    with pytest.raises(SatkError):
        P.project_dir("bad name!")
    with pytest.raises(SatkError) as ei:
        P.load("nothing")
    assert ei.value.code == "NOT_FOUND" and "asset init" in ei.value.hint
    assert "automobile" in P.kinds() and "ped" in P.kinds() and "building" in P.kinds()
    r = run_cli(["asset", "status", "nothing"])
    assert r.json["error"]["code"] == "NOT_FOUND"


def test_status_op_and_session_config(satk_home):
    P.init("car9", kind="automobile", dims=[2.3, 5.5, 1.5], tier="vanilla")
    card = get_op("asset.status").call({"dir": "car9"})
    assert card["session"] == {"name": "car9", "up": False} and card["tier"] == "vanilla"
    pdir, data = P.load("car9")
    assert P.session_config(pdir, data) == {"target_dims": [2.3, 5.5, 1.5]}  # no style bands installed yet


# --------------------------------------------------------------------------- ref.import


def test_ref_import_strips_exif_and_shrinks(satk_home):
    from PIL import Image

    src = satk_home / "photo.jpg"
    im = Image.new("RGB", (3200, 1800), (120, 130, 140))
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90 deg
    exif[0x010F] = "SomeCamera"
    im.save(src, "JPEG", exif=exif.tobytes())
    P.init("car1", kind="automobile")
    t = time.perf_counter()
    r = get_op("ref.import").call({"photo": str(src), "project": "car1"})
    assert time.perf_counter() - t < 5
    out = Image.open(r["image"])
    assert max(out.size) == 1600 and out.size == (900, 1600)  # EXIF rotation applied, then shrunk
    assert not out.info.get("exif") and r["exif_removed"] is True and r["source_size"] == [3200, 1800]
    assert r["image"].startswith(str(satk_home / "work" / "assets" / "car1" / "refs").replace("\\", "/"))
    assert "grid" not in r and "features.md" in r["next"]  # the pixel grid is opt-in (--grid)
    again = get_op("ref.import").call({"photo": str(src), "project": "car1"})
    assert again["reused"] is True and again["image"] == r["image"]
    plain = get_op("ref.import").call({"photo": str(src)})
    assert "/work/refs/" in plain["image"]
    with pytest.raises(SatkError):
        get_op("ref.import").call({"photo": str(satk_home / "nope.jpg")})


# --------------------------------------------------------------------------- replay planning


def _e(n, method, **kw):
    return {"n": n, "method": method, "ok": True, **kw}


def test_plan_actions():
    j = [_e(1, "session.start", name="a"),
         _e(2, "mesh.primitive", params={"kind": "cube"}),
         _e(3, "scene.info", ro=True, params={}),
         {"n": 4, "method": "mesh.bevel", "ok": False, "params": {}},
         _e(5, "session.checkpoint", ro=True, checkpoint="/c/0005-G1.blend"),
         _e(6, "python", params={"code": "x = 1"}, checkpoint="/c/0006.blend"),
         _e(7, "session.restore", restore=5, restore_path="/c/0005-G1.blend"),
         _e(8, "external", checkpoint="/c/0008-ext.blend"),
         _e(8, "session.end", checkpoint="/c/0008.blend"),
         _e(9, "session.start", open="/c/0008.blend", resume_n=8),
         _e(10, "modifier.add", params={"object": "a", "type": "MIRROR"}),
         _e(11, "session.start"),
         _e(12, "mesh.primitive", params={"kind": "plane"}, open="/x/file.blend"),
         _e(13, "scene.info", ro=True, params={}, open="/x/other.blend")]
    acts = plan_actions(j)
    assert [(a["op"], a["n"]) for a in acts] == [
        ("call", 2), ("call", 6), ("open", 7), ("open", 8), ("call", 10), ("clear", 11), ("open", 12), ("call", 12),
        ("open", 13)]
    assert acts[0]["method"] == "mesh.primitive" and acts[1]["params"] == {"code": "x = 1"}
    later = plan_actions(j, after=8)
    assert [(a["op"], a["n"]) for a in later] == [("call", 10), ("clear", 11), ("open", 12), ("call", 12),
                                                  ("open", 13)]
    resumed = plan_actions(j[:2] + j[9:11])
    assert [(a["op"], a["n"]) for a in resumed] == [("call", 2), ("open", 9), ("call", 10)]  # step 8 never replayed
    assert [a["n"] for a in plan_actions(j, upto=6)] == [2, 6]
    with pytest.raises(SatkError):
        plan_actions([_e(1, "session.restore")])


# --------------------------------------------------------------------------- launcher and client helpers


def test_argv_threads_and_request(satk_home, monkeypatch):
    a = L.argv(satk_home / "r.json", threads=4)
    assert a[:3] == ["-b", "-t", "4"] and a[-1].endswith("r.json")
    assert "-t" not in L.argv(satk_home / "r.json")
    monkeypatch.setenv("SATK_BLENDER_THREADS", "6")
    assert L._threads(None) == 6 and L._threads(2) == 2
    with pytest.raises(SatkError):
        L._threads(999)
    pdir = satk_home / "work" / "assets" / "p1"
    req = L.request("serve", satk_home / "work" / "studio" / "p1", name="p1", project=pdir,
                    project_cfg={"target_dims": [1, 2, 3]}, resume_n=12)
    assert req["journal"].endswith(os.path.join("assets", "p1", "journal.jsonl"))
    assert req["checkpoints"].endswith(os.path.join("p1", "checkpoints")) and req["resume_n"] == 12
    assert req["project"] == {"target_dims": [1, 2, 3]} and req["checkpoint_every"] == L.CHECKPOINT_EVERY


def test_prune_stale_sessions_and_checkpoints(satk_home):
    C.write_endpoint("blender-ghost1", {"role": "blender", "name": "ghost1", "pid": 999_999, "port": 1})
    C.write_session("blender-ghost1", {"role": "blender", "name": "ghost1", "pid": 999_999, "token": "t" * 64})
    P.init("car2", kind="automobile")
    ck = satk_home / "work" / "assets" / "car2" / "checkpoints"
    for n in range(1, 15):
        (ck / f"{n:04d}.blend").write_bytes(b"x")
    (ck / "0003-G1.blend").write_bytes(b"x")
    out = L.prune("car2", keep=4)
    assert out["stale_removed"] == ["ghost1"] and C.read_endpoint("blender-ghost1") is None
    assert out["checkpoints_removed"] == 10 and out["checkpoints_kept"] == 5
    r = get_op("blender.session").call({"action": "prune"})
    assert not r.get("stale_removed") and "checkpoints_removed" not in r


def test_prepare_paths_sids_and_snapshot_specs(satk_home, monkeypatch):
    monkeypatch.chdir(satk_home)
    p = api.prepare("ref.plane", {"image": "refs/a.jpg", "view": "left"})
    assert os.path.isabs(p["image"]) and p["image"].endswith(os.path.join("refs", "a.jpg"))
    b = api.prepare("batch", {"steps": [{"method": "material.create", "params": {"texture": "t.png"}},
                                        {"method": "scene.info"}]})
    assert os.path.isabs(b["steps"][0]["params"]["texture"]) and b["steps"][1] == {"method": "scene.info"}
    assert api.prepare("io.ghost", {"dff": "x.dff"})["dff"] == str(satk_home / "x.dff")
    assert api.snapshot_spec("sheet", 512) == {"views": ["3q", "front", "left", "top"], "size": 512}
    assert api.snapshot_spec('{"view": "left", "ghost": "lineup"}', None) == {"view": "left", "ghost": "lineup"}
    assert api.snapshot_spec("cam_front", None) == {"view": "cam_front"}
    with pytest.raises(SatkError):
        api.snapshot_spec("{bad", None)
    with pytest.raises(SatkError) as ei:
        api.prepare("io.ghost", {"sid": "model:426"})  # no index in an empty workspace
    assert ei.value.code in ("INDEX_MISSING", "NOT_FOUND", "NOT_READY")


def test_new_ops_are_cli_only(satk_home):
    for name in ("asset.init", "asset.status", "ref.import", "blender.session", "blender.call", "blender.methods"):
        o = get_op(name)
        assert o.mcp is False and o.module == "satk.studio.ops" and len(o.summary) <= 300, name


def test_export_and_check_results_are_recorded_in_the_project(satk_home):
    P.init("car3", kind="automobile")
    pdir = satk_home / "work" / "assets" / "car3"
    C.write_session("blender-car3", {"role": "blender", "name": "car3", "pid": os.getpid(), "token": "t" * 64,
                                     "project": str(pdir)})
    api._record("car3", "kit.export", {"n": 41, "result": {"dff": "x/car3.dff", "tris": 2100, "files": ["a", "b"]}})
    api._record("car3", "mesh.extrude", {"n": 42, "result": {"faces": 3}})  # not an export or check: ignored
    api._record("car3", "asset.check", {"n": 43, "result": {"rows": 2, "blocking": 0}})
    data = json.loads((pdir / "asset.json").read_text(encoding="utf-8"))
    assert data["last_export"] == {"dff": "x/car3.dff", "tris": 2100, "n": 41}
    assert data["last_check"] == {"rows": 2, "blocking": 0, "n": 43}
    assert "last_export" in P.status("car3")
    C.remove_discovery("blender-car3")


def test_gate_states_review_skip_reasons_and_done_needs_the_items(satk_home):
    P.init("gates1", kind="prop", detail="simple")
    P.record("gates1", {"gate": "G1", "state": "review"})
    assert P.status("gates1")["gates"].startswith("G0- G1?")
    with pytest.raises(SatkError) as e:                       # no session, no checkpoint: nothing proves the items
        P.record("gates1", {"gate": "G1", "state": "done"})
    assert e.value.code == "BAD_PARAMS" and "G1 is not done" in e.value.msg
    with pytest.raises(SatkError) as e:
        P.record("gates1", {"gate": "G3", "state": "skipped"})
    assert "needs a reason" in e.value.msg
    P.record("gates1", {"gate": "G3", "state": "skipped", "why": "the bin has no detail stage of its own"})
    card = P.status("gates1")
    assert "G3~" in card["gates"] and card["skipped"] == {"G3": "the bin has no detail stage of its own"}
    P.init("gates2", kind="prop", detail="none")              # no inventory: nothing to verify, strict blocks later
    data = json.loads((P.project_dir("gates2") / "asset.json").read_text(encoding="utf-8"))
    assert data["detail"] == "none"
    P.record("gates2", {"gate": "G1", "state": "done"})
