"""Inventory verdicts from facts (no Blender): every status, tolerances, pieces, the DFF sidecar, the operations."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.inventory import api as A
from satk.inventory import schema as SC
from satk.inventory import sidecar as SD

from .conftest import build_dff

INV = {
    "kind": "prop", "detail": "simple", "waived": [{"key": "secondary_parts", "why": "covered by I04 to I07"}],
    "items": [
        {"id": "I01", "key": "main_body", "name": "Body", "region": "body", "category": "shell", "stage": "G1",
         "construction": "lathe body, chamfered rim", "attaches_to": None, "frame": "bin"},
        {"id": "I02", "key": "base", "name": "Feet", "region": "base", "category": "structure", "stage": "G1",
         "construction": "four rounded_box feet", "attaches_to": "I01", "count": 4},
        {"id": "I03", "key": "top", "name": "Lid", "region": "top", "category": "shell", "stage": "G2",
         "construction": "lofted lid with a lip", "attaches_to": "I01"},
        {"id": "I04", "name": "Hinge", "region": "details", "category": "panel", "stage": "G2",
         "construction": "two knuckles on a pin", "attaches_to": ["I03", "I01"]},
        {"id": "I05", "name": "Label", "region": "details", "category": "sign", "stage": "G3",
         "construction": "a card on the body front", "attaches_to": "I01", "required": False},
        {"id": "I06", "name": "Pedal", "region": "base", "category": "detail", "stage": "G3",
         "construction": "a lever from the foot to the lid", "attaches_to": "I08"},
        {"id": "I07", "name": "Badge", "region": "details", "category": "trim", "stage": "G3",
         "construction": "a raised badge card on the lid", "attaches_to": "I03"},
        {"id": "I08", "name": "Pedal bracket", "region": "base", "category": "structure", "stage": "G3",
         "construction": "a bent plate on the feet", "attaches_to": "I02"},
        {"id": "I09", "name": "Handle", "region": "top", "category": "trim", "stage": "G3",
         "construction": "a swept bar on the lid", "attaches_to": "I03"},
        {"id": "I10", "name": "Rim", "region": "top", "category": "trim", "stage": "G3",
         "construction": "a swept rim band", "attaches_to": "I01",
         "rejected": {"reason": "the rim is a flat box band, make it round", "by": "critic"}},
    ],
}

FACTS = {
    "format": A.FACTS_FORMAT, "scope": "scene", "search_mm": 300,
    "items": {
        "I01": {"objects": ["bin"], "tris": 400, "size": [0.6, 0.6, 1.0], "pieces": 1, "frames": ["bin"]},
        "I02": {"objects": ["bin"], "tris": 48, "size": [0.6, 0.6, 0.05], "pieces": 3},
        "I03": {"objects": ["lid"], "tris": 120, "size": [0.62, 0.62, 0.08], "pieces": 1},
        "I04": {"objects": ["hinge"], "tris": 24, "size": [0.1, 0.02, 0.02], "pieces": 2},
        "I07": {"objects": ["badge"], "tris": 2, "size": [0.003, 0.002, 0.0], "pieces": 1},
        "I09": {"objects": ["handle"], "tris": 40, "size": [0.2, 0.03, 0.04], "pieces": 1},
        "I10": {"objects": ["rim"], "tris": 64, "size": [0.6, 0.6, 0.03], "pieces": 1},
    },
    "gaps": {"I02": {"I01": 0.0}, "I03": {"I01": 44.0}, "I04": {"I03": 28.0, "I01": 41.0}, "I09": {"I03": None},
             "I10": {"I01": 0.0}},
    "ambiguous": {"lever": ["I06", "I08"]},
    "hidden": {"I05": ["label_ghost"]},
    "untagged": [{"object": "junk", "tris": 12}],
    "tagged": ["I01", "I02", "I03", "I04", "I05", "I06", "I07", "I08", "I09", "I10", "I77"],
}


def _rows(inv=INV, facts=FACTS) -> dict:
    return {r["id"]: r for r in A.verdicts(inv, facts)}


def test_every_status_with_its_reason():
    r = _rows()
    assert r["I01"]["status"] == "built" and r["I01"]["objects"] == ["bin"]
    assert r["I02"]["status"] == "missing" and r["I02"]["reason"] == "3 of 4 pieces built"
    assert r["I03"]["status"] == "unattached" and r["I03"]["gap_mm"] == 44.0
    assert "44.0 mm from I01 (tolerance 10 mm)" in r["I03"]["reason"]
    assert r["I04"]["status"] == "built" and r["I04"]["gap_mm"] == 28.0          # a panel may stand off 30 mm
    assert r["I05"]["status"] == "rejected" and "hidden" in r["I05"]["reason"] and r["I05"]["optional"]
    assert r["I06"]["status"] == "rejected" and "lever" in r["I06"]["reason"] and "face tags" in r["I06"]["reason"]
    assert r["I07"]["status"] == "rejected" and r["I07"]["reason"].startswith("placeholder: 3.0 mm")
    assert r["I08"]["status"] == "rejected"                                         # the same ambiguous object
    assert r["I09"]["status"] == "unattached" and "more than 300 mm" in r["I09"]["reason"]
    assert r["I10"]["status"] == "rejected" and r["I10"]["reason"] == \
        "reviewer: the rim is a flat box band, make it round (critic)"


def test_parent_not_built_is_a_note_and_frames_are_checked_in_kit_scope():
    inv = copy.deepcopy(INV)
    facts = copy.deepcopy(FACTS)
    facts["items"]["I08"] = {"objects": ["bracket"], "tris": 10, "size": [0.1, 0.1, 0.01], "pieces": 1}
    del facts["ambiguous"]
    facts["items"].pop("I02")
    r = _rows(inv, facts)
    assert r["I08"]["status"] == "built" and r["I08"]["note"] == "attaches to I02: not built yet"
    facts["scope"] = "kit"
    facts["items"]["I01"]["frames"] = ["chassis"]
    r = _rows(inv, facts)
    assert r["I01"]["note"] == "planned in bin; found in chassis"


def test_report_counts_complete_and_unknown_tags(satk_home):
    with pytest.raises(SatkError) as e:
        A.report(None, facts=FACTS)                           # no project and no dff: nothing to read
    assert e.value.code == "BAD_PARAMS"
    from satk.studio import project as P

    P.init("bin1", kind="prop", detail="none")
    SC.save(P.project_dir("bin1"), INV)
    rep = A.report("bin1", facts=FACTS)
    c = rep["counts"]
    assert (c["built"], c["missing"], c["unattached"], c["rejected"], c["total"]) == (2, 1, 2, 5, 10)
    assert c["required_open"] == 7 and c["optional_open"] == 1 and rep["complete"] is False
    assert rep["gates"] == {"G1": "1/2", "G2": "1/2", "G3": "0/5"} and rep["done_through"] is None
    assert any(w.startswith("UNKNOWN: tags name no inventory item: I77") for w in rep["warn"])
    assert rep["untagged"] == {"objects": 1, "tris": 12, "names": ["junk"]}
    good = {"kind": "prop", "detail": "simple", "items": [INV["items"][0]]}
    SC.save(P.project_dir("bin1"), good)
    rep = A.report("bin1", facts=FACTS)
    assert rep["complete"] is True and rep["counts"]["built"] == 1 and rep["done_through"] == "G4"
    assert A.has_inventory("bin1") and not A.has_inventory(None)


def test_dff_sidecar_round_trip(satk_home, tmp_path):
    from satk.core import paths

    work = Path(paths.cfg().paths.work)
    pkg = work / "out" / "kit" / "bin1" / "files" / "bin1"
    pkg.mkdir(parents=True)
    dff = pkg / "bin1.dff"
    dff.write_bytes(build_dff("bin", "lid"))
    with pytest.raises(SatkError) as e:
        A.report(None, dff=dff)
    assert e.value.code == "NOT_FOUND" and "sidecar" in e.value.msg
    facts = copy.deepcopy(FACTS)
    facts["scope"] = "kit"
    facts["frames"] = {"bin": 2, "lid": 2, "handle": 2}
    facts["items"]["I03"]["frames"] = ["lid"]
    facts["items"]["I09"]["frames"] = ["handle"]
    files = SD.write([pkg, work / "out" / "kit" / "bin1"], "bin1", inventory=INV, facts=facts, model="bin1")
    assert len(files) == 2 and A.locate(dff) == pkg / "bin1.inventory.json"
    side = json.loads((pkg / "bin1.inventory.json").read_text(encoding="utf-8"))
    assert side["format"] == SD.FORMAT and side["frames"] == {"bin": 2, "lid": 2, "handle": 2}
    rep = A.report(None, dff=dff)
    r = {x["id"]: x for x in rep["items"]}
    assert rep["source"].startswith("dff:") and r["I03"]["status"] == "unattached"
    assert r["I09"]["status"] == "missing"                     # its frame 'handle' is not in the DFF
    assert any(w.startswith("MISSING: frame(s) handle") for w in rep["warn"])
    assert not [w for w in rep["warn"] if w.startswith("STALE")]            # 2 triangles per frame, as written
    dff.write_bytes(build_dff("bin"))                          # 'lid' gone too, and a stale count elsewhere
    facts["frames"]["bin"] = 4
    SD.write([pkg], "bin1", inventory=INV, facts=facts)
    rep = A.report(None, dff=dff)
    assert any(w.startswith("STALE: bin") for w in rep["warn"])
    assert {x["id"]: x for x in rep["items"]}["I03"]["status"] == "missing"


def test_mark_reject_and_accept(satk_home):
    from satk.studio import project as P

    P.init("bin2", kind="prop", detail="simple")
    p, inv = SC.load("bin2")
    assert [i["id"] for i in inv["items"]] == ["I01", "I02", "I03", "I04"]
    out = A.mark("bin2", "I03", reject="the cap floats 4 cm above the body", by="critic")
    assert out["rejected"] == {"reason": "the cap floats 4 cm above the body", "by": "critic"}
    assert SC.load("bin2")[1]["items"][2]["rejected"]["by"] == "critic"
    for bad in ({"reject": "short"}, {}, {"reject": "a valid reason here", "accept": True}):
        with pytest.raises(SatkError):
            A.mark("bin2", "I03", **bad)
    with pytest.raises(SatkError) as e:
        A.mark("bin2", "I3")
    assert e.value.code == "NOT_FOUND"
    A.mark("bin2", "I03", accept=True)
    assert "rejected" not in SC.load("bin2")[1]["items"][2]


def test_export_sidecar_never_fails_the_export(satk_home, monkeypatch):
    warn: list[str] = []

    def boom(*a, **k):
        raise SatkError("NOT_READY", "no session")

    from satk.core import paths

    pkg = Path(paths.cfg().paths.work) / "out" / "kit" / "stem"
    pkg.mkdir(parents=True)
    (pkg / "stem.inventory.json").write_text("{}", encoding="utf-8")       # the sidecar of an older export
    monkeypatch.setattr(SD, "facts_from_session", boom)
    assert SD.export_sidecar(pkg, "stem", session="nope", warn=warn) is None
    assert warn[0].startswith("ERROR: inventory sidecar not written (NOT_READY: no session)")
    assert not (pkg / "stem.inventory.json").exists()                     # never a stale sidecar
    monkeypatch.setattr(SD, "facts_from_session", lambda *a, **k: {"items": {}, "tagged": []})
    assert SD.export_sidecar(pkg, "stem", warn=warn, always=False) is None   # no inventory and no tags
    side = SD.export_sidecar(pkg, "stem", warn=warn, lod="lodstem")          # a kit export: always (authored)
    doc = json.loads(Path(side).read_text(encoding="utf-8"))
    assert doc["inventory"] is None and doc["authored"] is True and doc["lod"] == "lodstem"


# --------------------------------------------------------------------------- operations


def test_ops_starter_validate_plan_and_report(satk_home, run_cli, monkeypatch):
    r = run_cli(["inventory", "starter", "all"])
    assert r.code == 0 and {row[0] for row in r.json["rows"]} >= {"automobile", "building", "ped", "weapon"}
    r = run_cli(["inventory", "starter", "bike", "--detail", "simple"])
    assert r.json["kind"] == "bike" and r.json["rows"][0][:2] == ["I01", "frame"] and "cockpit" in r.json["regions"]
    r = run_cli(["asset", "init", "moto", "--kind", "bike", "--detail", "none"])
    assert r.code == 0 and "inventory" not in r.json
    r = run_cli(["inventory", "starter", "automobile", "--project", "moto"])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "is a bike" in r.json["error"]["msg"]
    r = run_cli(["inventory", "starter", "bike", "--project", "moto", "--detail", "standard"])
    assert r.code == 0 and r.json["items"] == 23
    r = run_cli(["inventory", "starter", "bike", "--project", "moto"])
    assert r.json["error"]["code"] == "EXISTS"
    r = run_cli(["inventory", "validate", "moto"])
    assert r.code == 0 and r.json["valid"] is True and r.json["items"] == 23
    p, inv = SC.load("moto")
    inv["items"] = [i for i in inv["items"] if i["key"] != "mirrors"]
    inv["items"][3]["attaches_to"] = "I99"
    SC.save(p.parent.parent, inv)
    r = run_cli(["inventory", "validate", "moto", "--strict"])
    msgs = [row[1] for row in r.json["rows"]]
    assert r.json["valid"] is False and r.json["dropped"] == ["mirrors"]
    assert any("attaches_to: no item 'I99'" in m for m in msgs) and any("mirrors" in m for m in msgs)
    r = run_cli(["asset", "inventory", "moto", "--plan", "--stage", "G1", "--md"])
    assert r.code == 0 and r.json["text"].startswith("# Inventory moto (bike, standard): G1 items")
    assert "- [ ] I01 Frame (sides, structure)" in r.json["text"]
    r = run_cli(["asset", "inventory", "moto", "--plan"])
    assert r.json["total"] == 22 and r.json["problems"]

    monkeypatch.setattr(A, "facts_from_session", lambda inv, **kw: {"scope": "scene", "items": {
        "I01": {"objects": ["frame"], "tris": 300, "size": [0.3, 1.2, 0.5], "pieces": 1}}, "gaps": {}})
    monkeypatch.setattr(A, "_project_session", lambda pdir: ("moto", None))
    r = run_cli(["asset", "inventory", "moto"])
    j = r.json
    assert r.code == 0 and j["complete"] is False and j["counts"]["built"] == 1 and j["source"] == "session:moto"
    assert j["cols"] == ["id", "status", "name", "stage", "region", "note"]
    assert all(row[1] != "built" for row in j["rows"]) and "scene.tag" in j["hint"]
    r = run_cli(["asset", "inventory", "moto", "--status", "built"])
    assert [row[0] for row in r.json["rows"]] == ["I01"] and "in frame" in r.json["rows"][0][5]
    r = run_cli(["asset", "inventory", "moto", "--md"])
    assert "1/22 built" in r.json["text"] and "**MISSING**" in r.json["text"]
    r = run_cli(["inventory", "mark", "moto", "I01", "--reject", "the frame is a box, sweep round tubes"])
    assert r.code == 0
    r = run_cli(["asset", "inventory", "moto", "--status", "rejected"])
    assert [row[0] for row in r.json["rows"]] == ["I01"]


def test_asset_init_seeds_the_inventory_and_status_shows_it(satk_home, run_cli):
    r = run_cli(["asset", "init", "car9", "--kind", "automobile"])
    assert r.code == 0 and r.json["inventory"]["detail"] == "hero" and r.json["inventory"]["items"] == 58
    data = json.loads((satk_home / "work" / "assets" / "car9" / "asset.json").read_text(encoding="utf-8"))
    assert data["detail"] == "hero"
    r = run_cli(["asset", "status", "car9"])
    assert r.json["inventory"].startswith("hero, 58 items (")
    r = run_cli(["asset", "init", "car9", "--kind", "automobile", "--detail", "simple", "--force"])
    assert r.json["inventory"]["kept"] is True and r.json["inventory"]["items"] == 58


def test_placeholders_surface_items_and_pieces_of_their_own():
    """A face patch of another item is no part (rejected), a count counts pieces with geometry of their own, a
    decal is built by its texture and a paint surface by the paint key (facts of scene.items version 2)."""
    inv = {"kind": "prop", "detail": "simple", "items": [
        {"id": "I01", "name": "Body", "region": "body", "category": "shell", "stage": "G1",
         "construction": "lathe body", "attaches_to": None},
        {"id": "I02", "name": "Cap", "region": "top", "category": "trim", "stage": "G2",
         "construction": "a cap on the body", "attaches_to": "I01"},
        {"id": "I03", "name": "Bolts", "region": "top", "category": "trim", "stage": "G3",
         "construction": "four bolts on the cap", "attaches_to": "I01", "count": 4},
        {"id": "I04", "name": "Texture", "region": "body", "category": "decal", "stage": "G4",
         "construction": "texture on the body", "attaches_to": "I01"},
        {"id": "I05", "name": "Paint", "region": "body", "category": "decal", "stage": "G4", "verify": "paint",
         "construction": "paint key faces", "attaches_to": "I01"},
        {"id": "I06", "name": "Pane", "region": "body", "category": "glass", "stage": "G2",
         "construction": "glass pane in the opening", "attaches_to": "I01"},
    ]}
    facts = {"format": A.FACTS_FORMAT, "version": 2, "scope": "scene", "items": {
        "I01": {"objects": ["body"], "tris": 136, "size": [0.6, 0.6, 1.1], "pieces": 1, "own_verts": 60,
                "tex_share": 0.0, "paint_share": 0.0},
        "I02": {"objects": ["body"], "tris": 12, "size": [0.6, 0.6, 0.0], "pieces": 0, "patch_pieces": 1,
                "own_verts": 0, "shares_with": ["I01"], "tex_share": 0.0},
        "I03": {"objects": ["body"], "tris": 10, "size": [0.6, 0.5, 0.5], "pieces": 1, "patch_pieces": 3,
                "own_verts": 4, "shares_with": ["I01"]},
        "I04": {"objects": ["body"], "tris": 1, "size": [0.1, 0.1, 0.0], "pieces": 0, "own_verts": 0,
                "tex_share": 0.0},
        "I05": {"objects": ["body"], "tris": 8, "size": [0.6, 0.6, 0.2], "pieces": 0, "own_verts": 0,
                "paint_share": 1.0},
        "I06": {"objects": ["body"], "tris": 2, "size": [0.4, 0.0, 0.3], "pieces": 0, "own_verts": 0},
    }, "gaps": {k: {"I01": 0.0} for k in ("I03", "I04", "I05", "I06")}}
    rows = {r["id"]: r for r in A.verdicts(inv, facts)}
    assert rows["I02"]["status"] == "rejected" and "no geometry of its own" in rows["I02"]["reason"]
    assert "I01's surface" in rows["I02"]["reason"]
    assert rows["I03"]["status"] == "missing" and rows["I03"]["reason"].startswith("1 of 4 pieces built (3 more")
    assert rows["I04"]["status"] == "missing" and "carry an image texture" in rows["I04"]["reason"]
    assert rows["I05"]["status"] == "built" and rows["I06"]["status"] == "built"     # paint key; a welded pane
    facts["items"]["I04"]["tex_share"] = 1.0
    facts["items"]["I05"]["paint_share"] = 0.1
    rows = {r["id"]: r for r in A.verdicts(inv, facts)}
    assert rows["I04"]["status"] == "built" and rows["I05"]["status"] == "missing"


def test_an_edited_or_broken_sidecar_is_a_problem_not_a_crash(satk_home):
    from satk.core import paths

    pkg = Path(paths.cfg().paths.work) / "out" / "kit" / "bin9" / "files" / "bin9"
    pkg.mkdir(parents=True)
    dff = pkg / "bin9.dff"
    dff.write_bytes(build_dff("bin", "lid", "hinge", "badge", "handle", "rim"))
    side = pkg / "bin9.inventory.json"
    side.write_text('{"format": "satk.inventory-sidecar/1", "facts": {', encoding="utf-8")
    with pytest.raises(SatkError) as e:
        A.report(None, dff=str(dff))
    assert e.value.code == "BAD_PARAMS" and "does not read" in e.value.msg
    side.write_text("[]", encoding="utf-8")
    with pytest.raises(SatkError) as e:
        A.report(None, dff=str(dff))
    assert e.value.code == "BAD_PARAMS"
    facts = copy.deepcopy(FACTS)
    facts["items"]["I01"]["frame_tris"] = {"bin": 400}             # more tagged triangles than the frame has
    SD.write([pkg], "bin9", inventory=INV, facts=facts)
    with pytest.raises(SatkError) as e:
        A.report(None, dff=str(dff))
    assert "claims more tagged triangles in bin" in e.value.msg
    facts["items"]["I01"].pop("frame_tris")
    SD.write([pkg], "bin9", inventory=INV, facts=facts, dff_sha256="0" * 64)
    rep = A.report(None, dff=str(dff))                              # another export's sidecar
    assert rep["complete"] is False and any(p.startswith("sidecar: STALE: bin9.dff is not the export")
                                            for p in rep["problems"])
