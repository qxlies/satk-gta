"""Inventory tags and facts in the real Blender 5.1 (markers blender, slow): scene.tag on objects and faces, the
item/linked selectors, tags surviving joins and MIRROR, every verdict from a live session, the report from a
checkpoint, and (with a vanilla index for the kit template) the kit scope, a kit export and its DFF sidecar.
Synthetic geometry only; the session is named after the lane and runs in its own work directory."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from satk.core import config
from satk.core.errors import SatkError
from satk.inventory import api as A
from satk.inventory import schema as SC
from satk.studio import api
from satk.studio import launcher as L
from satk.studio import project as P

pytestmark = [pytest.mark.blender, pytest.mark.slow]

NAME = "a3-inventory"
PROJECT = "invlive"


def _item(i, name, cat, att, stage="G2", **kw):
    return {"id": i, "name": name, "region": "body", "category": cat, "stage": stage, "attaches_to": att,
            "construction": f"{name.lower()} built for the live test", **kw}


INV = {"kind": "prop", "detail": "simple", "items": [
    _item("I01", "Body", "shell", None, "G1"),
    _item("I02", "Base", "structure", "I01", "G1"),
    _item("I03", "Lid", "shell", "I01"),
    _item("I04", "Handle", "trim", "I03", "G3"),
    _item("I05", "Feet", "structure", "I02", count=2),
    _item("I06", "Label", "sign", "I01", "G3"),
    _item("I07", "Lever", "detail", "I01", "G3"),
    _item("I08", "Pedal", "detail", "I01", "G3"),
    _item("I09", "Wheels", "detail", "I01", "G3"),
], "waived": [{"key": k, "why": "the live test has its own items"} for k in
              ("main_body", "base", "top", "secondary_parts")]}


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from satk.blender import runner
    from satk.docs.cleanup import remove_tree

    try:
        runner.blender_exe()
    except SatkError as e:
        pytest.skip(str(e))
    plan = None
    try:                                    # the kit template plan reads the configured vanilla index
        from satk.index import api as index_api
        from satk.kit.plan import template_plan

        plan = template_plan(kind="prop", name="invkit")
        index_api.clear_cache()
    except SatkError:
        plan = None
    work = tmp_path_factory.mktemp("inv") / "work"
    work.mkdir()
    old = {k: os.environ.get(k) for k in ("SATK_PATHS_WORK", "SATK_DRAGONFF")}
    dff = runner.dragonff_root()
    if (dff / "dragonff").is_dir():
        os.environ["SATK_DRAGONFF"] = str(dff)
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    P.init(PROJECT, kind="prop", detail="none")
    SC.save(P.project_dir(PROJECT), INV)
    if plan is not None:
        (work / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    L.start(NAME, project=PROJECT, owner_pid=os.getpid(), idle=600)
    try:
        yield {"work": work, "plan": work / "plan.json" if plan is not None else None}
    finally:
        api.close_all()
        try:
            L.stop(NAME)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            config.reset()
            remove_tree(work.parent)


def _c(method: str, params: dict | None = None, **kw) -> dict:
    return api.call(method, params, session=NAME, stats="none", checkpoint=False, **kw)


def _py(code: str) -> dict:
    return _c("python", {"code": code})["result"].get("value") or {}


def _rows() -> dict:
    rep = A.report(PROJECT, session=NAME)
    return {r["id"]: r for r in rep["items"]}, rep


def test_tags_and_verdicts(live):
    _c("scene.clear")
    _c("mesh.primitive", {"kind": "rounded_box", "name": "body", "size": [0.6, 0.6, 0.8], "radius": 0.05,
                          "segments": 2, "location": [0, 0, 0.5]})
    _c("mesh.primitive", {"kind": "cylinder", "name": "base", "radius": 0.32, "depth": 0.1, "segments": 16,
                          "location": [0, 0, 0.05]})
    _c("mesh.primitive", {"kind": "rounded_box", "name": "lid", "size": [0.62, 0.62, 0.06], "radius": 0.02,
                          "segments": 2, "location": [0, 0, 0.95]})                    # 20 mm above the body
    _c("mesh.primitive", {"kind": "rounded_box", "name": "foot", "size": [0.08, 0.08, 0.05], "radius": 0.01,
                          "segments": 1, "location": [0, 0, -0.025]})
    _c("mesh.transform", {"object": "foot", "translate": [0.25, 0, 0]})              # off the origin: MIRROR = 2
    _c("modifier.add", {"object": "foot", "type": "MIRROR"})
    _c("mesh.primitive", {"kind": "cube", "name": "label", "size": [0.2, 0.01, 0.1], "location": [0, -0.31, 0.5]})
    _c("mesh.primitive", {"kind": "cube", "name": "lever", "size": [0.05, 0.3, 0.03], "location": [0, -0.4, 0.1]})
    r = _c("scene.tag", {"objects": ["body"], "item": "I01"})["result"]
    assert r["item"] == "I01" and r["name"] == "Body" and r["faces"] > 0           # named from the project
    for obj, iid in (("base", "I02"), ("lid", "I03"), ("foot", "I05"), ("label", "I06")):
        _c("scene.tag", {"object": obj, "item": iid})
    _c("mesh.primitive", {"kind": "rounded_box", "name": "handle", "size": [0.2, 0.04, 0.03], "radius": 0.01,
                          "segments": 1, "location": [0, 0, 0.995]})                  # standing on the lid
    _c("scene.tag", {"object": "handle", "item": "I04"})
    # a face patch of the lid (its back side) tagged as another item is no part: a placeholder
    r = _c("scene.tag", {"object": "lid", "item": "I09", "select": {"side": "+y", "within": 5}})["result"]
    assert r["faces"] >= 1
    _c("scene.visible", {"object": "label", "hide": True})
    _c("scene.props", {"object": "lever", "set": {"satk_item": "I07,I08"}})
    w = api.call("scene.tag", {"object": "body", "item": "I42", "select": {"side": "-z", "within": 5}},
                 session=NAME, stats="none", checkpoint=False)
    assert any(x.startswith("UNKNOWN: I42") for x in w.get("warn") or [])
    _c("scene.tag", {"object": "body", "clear": True, "select": {"item": "I42"}})
    with pytest.raises(SatkError):
        _c("scene.tag", {"object": "body", "item": "5"})
    rows, rep = _rows()
    st = {k: v["status"] for k, v in rows.items()}
    assert st == {"I01": "built", "I02": "built", "I03": "unattached", "I04": "built", "I05": "built",
                  "I06": "rejected", "I07": "rejected", "I08": "rejected", "I09": "rejected"}, rows
    assert 15 <= rows["I03"]["gap_mm"] <= 25 and rows["I05"]["pieces"] == 2 and rows["I04"].get("gap_mm") == 0.0
    assert "hidden" in rows["I06"]["reason"] and "lever" in rows["I07"]["reason"]
    assert "no geometry of its own" in rows["I09"]["reason"] and "I03" in rows["I09"]["reason"], rows["I09"]
    assert rep["complete"] is False and rep["source"] == f"session:{NAME}"
    assert not [x for x in rep.get("warn") or [] if x.startswith("UNKNOWN")]


def test_moves_joins_and_selectors_keep_the_tags(live):
    _c("scene.transform", {"object": "lid", "location": [0, 0, -0.02], "delta": True})       # onto the body
    _c("scene.transform", {"object": "handle", "location": [0, 0, -0.02], "delta": True})    # with the lid
    rows, _ = _rows()
    assert rows["I03"]["status"] == "built" and rows["I03"]["gap_mm"] <= 1.0
    _c("scene.join", {"objects": ["body", "base", "lid"]})                                  # tags travel with faces
    rows, _ = _rows()
    for i in ("I01", "I02", "I03"):
        assert rows[i]["status"] == "built" and rows[i]["objects"] == ["body"], (i, rows[i])
    assert rows["I04"]["status"] == "built" and rows["I04"]["objects"] == ["handle"]
    v = _py("o = bpy.data.objects['body']\nresult['prop'] = o.get('satk_item')\n"
            "result['ids'] = list(o.get('satk_item_ids') or [])\n"
            "result['reg'] = list(bpy.context.scene['satk_item_ids'])")
    assert v["ids"][:2] == ["I01", "I02"] and v["reg"][:5] == ["I01", "I02", "I03", "I05", "I06"]
    assert v["prop"] == "I01"            # the join target's summary until the next scene.tag refreshes it
    before = _py("import bpy\nresult['z'] = max((bpy.data.objects['body'].matrix_world @ v.co).z "
                 "for v in bpy.data.objects['body'].data.vertices)")["z"]
    _c("mesh.transform", {"object": "body", "translate": [0, 0, 0.1], "select": {"item": "I03"}})
    after = _py("import bpy\nresult['z'] = max((bpy.data.objects['body'].matrix_world @ v.co).z "
                "for v in bpy.data.objects['body'].data.vertices)")["z"]
    assert after == pytest.approx(before + 0.1, abs=1e-4)
    _c("mesh.transform", {"object": "body", "translate": [0, 0, -0.1], "select": {"item": "I03"}})
    _c("scene.tag", {"object": "body", "item": "I09", "select": {"item": "I02", "linked": True}})
    v = _py("result['prop'] = bpy.data.objects['body'].get('satk_item')")
    assert set(v["prop"].split(",")) == {"I01", "I03", "I09"}, v
    rows, _ = _rows()
    assert rows["I09"]["status"] == "built" and rows["I02"]["status"] == "missing"       # the base became I09
    _c("scene.tag", {"object": "body", "item": "I02", "select": {"item": "I09"}})
    _c("scene.delete", {"object": "lever"})
    _c("scene.visible", {"object": "label", "hide": False})
    _c("mesh.primitive", {"kind": "cube", "name": "lever", "size": [0.05, 0.05, 0.03], "location": [0, -0.33, 0.3]})
    _c("scene.tag", {"object": "lever", "item": "I07"})
    _c("scene.duplicate", {"object": "lever", "name": "pedal", "offset": [0.1, 0, 0]})
    _c("scene.tag", {"object": "pedal", "item": "I08"})
    _c("mesh.primitive", {"kind": "cylinder", "name": "wheel", "radius": 0.05, "depth": 0.03, "segments": 12,
                          "location": [0.32, 0, 0.1]})                                 # into the body's side
    _c("scene.tag", {"object": "wheel", "item": "I09", "select": {"near": {"point": [0, 0, 0], "radius": 0.2}}})
    rows, rep = _rows()
    open_ = {k: v for k, v in rows.items() if v["status"] != "built"}
    assert not open_ and rep["complete"] is True, open_


def test_report_from_the_newest_checkpoint_when_the_session_is_down(live):
    res = api.call("session.checkpoint", {"tag": "G3"}, session=NAME, stats="none")
    assert res["result"]["checkpoint"]
    rep = A.report(PROJECT, blend=res["result"]["checkpoint"])
    assert rep["complete"] is True and rep["source"].startswith("blend:")


def test_kit_scope_export_and_dff_sidecar(live):
    if live["plan"] is None:
        pytest.skip("no vanilla index: the kit template plan needs it")
    from satk.inventory.sidecar import export_sidecar
    from satk.kit.export import run as kit_export

    _c("kit.template", {"plan": str(live["plan"]), "replace": True})
    slots = _py("import bpy\nc = [c for c in bpy.data.collections if c.get('satk_kit')][0]\n"
                "result['s'] = [o.name for o in c.objects if o.get('satk_role') in ('root', 'part')]")["s"]
    slot = slots[0]
    _c("scene.duplicate", {"object": "body", "name": "kitbody"})
    _c("kit.fill", {"slot": slot, "objects": ["kitbody"]})                                 # joins the tagged faces
    facts = A.facts_from_session(INV, session=NAME, scope="kit", model="invkit")
    assert facts["scope"] == "kit" and facts["frames"] and "I01" in facts["items"]
    assert facts["items"]["I01"]["frames"] == [slot]
    res = kit_export(session=NAME, name="invkit", lint=False, check=False, col="none")
    pkg = Path(res["files"]["dff"]).parent
    side = export_sidecar([pkg], "invkit", session=NAME, model="invkit", project=P.project_dir(PROJECT))
    assert side and Path(side).name == "invkit.inventory.json"
    rep = A.report(None, dff=res["files"]["dff"])
    rows = {r["id"]: r["status"] for r in rep["items"]}
    assert rows["I01"] == rows["I02"] == rows["I03"] == "built" and rows["I07"] == "missing"      # lever not filled
    assert not [w for w in rep.get("warn") or [] if w.startswith(("STALE", "MISSING"))], rep.get("warn")


def test_the_live_parameter_tables_know_branches(live):
    rows = dict(api.methods("mesh.primitive", session=NAME)["params"])
    assert rows["segments"].startswith("required if kind") and rows["rings"] == "required if kind=uv_sphere|capsule"
    assert rows["size"].startswith("1.0 if kind=")
    rows = dict(api.methods("scene.tag", session=NAME)["params"])
    assert rows["item"] == "required if without clear" and rows["object|objects"] == "required"
    rows = dict(api.methods("ref.plane", session=NAME)["params"])
    assert rows["distance"] == "required if with points" and rows["points"].startswith("required if without")
