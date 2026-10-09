"""satk.style core on synthetic data: measure, cache aggregation, profiles/bands (K2), asset.check, anatomy, cards.

A fake style cache is aggregated from synthetic model records (no game data): 20 'sedans' spread around the
metrics of the synthetic car of rwkit.car_dff, plus props in two size buckets.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from satk.model3d.mesh import build_scene
from satk.style import anatomy as A
from satk.style import cache as K
from satk.style import card as CD
from satk.style import check as CK
from satk.style import measure as ME
from satk.style import profile as P
from satk.style import subject as S
from satk.style import validate as V

from .rwkit import car_dff, prop_dff

np = pytest.importorskip("numpy")


def _car_metrics() -> tuple[dict, list]:
    sc = build_scene(car_dff(), name="car", sec="cars")
    return ME.measure(sc, wheel_scale=0.7), K._frame_rows(sc, ME.frame_names(sc))


def _records() -> list[dict]:
    base, frames = _car_metrics()
    recs = []
    for i in range(20):
        f = 0.9 + 0.2 * i / 19                       # 0.9 .. 1.1 around the synthetic car
        m = {k: (round(v * f, 4) if isinstance(v, float) else int(round(v * f))) for k, v in base.items()
             if isinstance(v, (int, float)) and not isinstance(v, bool)}
        cls = "car.sedan" if i < 14 else "car.van"
        recs.append({"id": 400 + i, "name": f"car{i:02d}", "cls": cls, "m": m, "frames": frames})
    for i in range(10):
        for size, bucket in ((1.5, "1-2m"), (3.0, "2-4m")):
            m = {"geo.tris": 40 + 10 * i, "geo.tris_per_m2": 5.0 + i, "mat.count": 1 + i % 3,
                 "uv.texel_px_m": 100.0 + 10 * i, "uv.zero_area_share": 0.01 * i, "light.prelit_lum_p50": 60.0 + i,
                 "dims.size": size, "dims.H": size}
            recs.append({"id": 1000 + i * 2 + (bucket == "2-4m"), "name": f"prop{bucket}{i}", "cls": "prop",
                         "bucket": bucket, "m": m})
    return recs


@pytest.fixture
def fake_cache(monkeypatch, tmp_path):
    recs = _records()
    data = {"profile": "vanilla", "index_hash": "synthetic", "peers": K.aggregate(recs), "frames": K._frames(recs),
            "models": {str(r["id"]): [r["name"], r["cls"], r.get("bucket")] for r in recs}}
    c = K.StyleCache(tmp_path / "c.json", tmp_path / "c.models.json", data)
    c._records = [{k: v for k, v in r.items() if k != "frames"} for r in recs]
    monkeypatch.setattr(K, "load", lambda profile="vanilla", **kw: c)
    return c


# ----------------------------------------------------------------------------- measure
def test_measure_vehicle_definitions():
    m, frames = _car_metrics()
    parts = {k: v for k, v in m.items() if k.startswith("part.tris[")}
    assert parts == {"part.tris[chassis]": 12, "part.tris[wheel]": 12, "part.tris[bump_front_ok]": 12,
                     "part.tris[door_lf_ok]": 12}
    assert m["veh.hd_tris"] == ME.hd_tris({"chassis": 12, "wheel": 12, "bump_front_ok": 12, "door_lf_ok": 12}) == 48
    assert m["veh.hi_tris"] == 48 + 3 * 12                    # the wheel once per wheel dummy
    assert m["dims.wheelbase"] == 3.2 and m["dims.track"] == 1.8 and m["dims.W"] == 2.2
    assert m["dims.L_over_wheel"] == round(m["dims.L"] / 0.7, 3)
    assert m["veh.wheel_mesh_d"] == 0.7 and m["dims.wheel_d"] == 0.7
    assert sum(m["geo.thirds"]) == pytest.approx(1.0, abs=0.01)
    assert frames[0][0] == "car" and frames[2][:2] == ["chassis", "chassis_dummy"]
    assert frames[1][1] == "<root>"


def test_hd_tris_excludes_optional_parts():
    parts = {"chassis": 1000, "wheel": 150, "door_lf_ok": 100, "door_lf_dam": 90, "exhaust_ok": 20, "extra1": 50,
             "misc_a": 30, "chassis_vlo": 60}
    assert ME.hd_tris(parts) == 1250


def test_measure_map_model_light_and_texel():
    sc = build_scene(prop_dff(2.0), name="box")
    m = ME.measure(sc, tex_sizes={"crate64": (64, 64)}, col={"spheres": 0, "boxes": 1, "faces": 0})
    assert m["dims.size"] == 2.0 and m["light.prelit_lum_p50"] == pytest.approx(81.8, abs=0.1)
    assert m["uv.texel_px_m"] > 0 and m["col.boxes"] == 1 and "veh.hd_tris" not in m


# ----------------------------------------------------------------------------- cache aggregation
def test_percentiles_and_aggregate_are_deterministic():
    assert K.percentiles([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == [1.9, 5.5, 9.1, 10]
    assert K.percentiles([100, 200, 300]) == [120, 200, 280, 3]
    recs = _records()
    a, b = K.aggregate(recs), K.aggregate(list(reversed(recs)))
    assert a == b
    assert a["car"]["n"] == 20 and a["car.sedan"]["n"] == 14 and a["prop@1-2m"]["n"] == 10 and a["prop"]["n"] == 20
    assert len(a["car.sedan"]["exemplars"]) == 3


def test_frame_table_shares(fake_cache):
    rows = {r[0]: r for r in fake_cache.data["frames"]["car"]}
    assert rows["wheel_lf_dummy"][1] == 1.0 and rows["chassis"][2] == "chassis_dummy" and rows["chassis"][6] == 1.0


# ----------------------------------------------------------------------------- K2
def test_profile_band_and_tiers(fake_cache):
    p = P.profile("car.sedan", "vanilla")
    hd = p["veh.hd_tris"]
    assert hd["peer_set"] == "car.sedan" and hd["n"] == 14 and (hd["lo"], hd["hi"]) == (hd["p10"], hd["p90"])
    # no tier has a triangle band: sa_plus shows the same vanilla reference numbers
    sp = P.profile("car.sedan", "sa_plus")["veh.hd_tris"]
    assert (sp["lo"], sp["hi"], sp["status"]) == (hd["p10"], hd["p90"], "measured") and "cap" not in sp
    assert P.band("shade.normal_bend", "car.sedan", "sa_plus") == P.band("shade.normal_bend", "car.sedan", "vanilla")
    assert P.band("veh.hd_tris", "car", "sa_plus") == P.band("veh.hd_tris", "car", "vanilla")
    assert P.band("geo.tris", "prop@1-2m", "sa_plus") == P.band("geo.tris", "prop@1-2m", "vanilla")
    assert P.band("uv.texel_px_m", "prop@1-2m", "sa_plus")[1] > P.band("uv.texel_px_m", "prop@1-2m", "vanilla")[1]
    # a model resolves to its peer set; small sets fall back to the parent
    d = P.describe("model:400", "vanilla")
    assert d["target"]["peer_set"] == "car.sedan" and d["target"]["like"]["name"] == "car00"
    van = P.describe("car.van", "vanilla")
    assert van["target"]["peer_set"] == "car" and van["target"]["fallback"]
    assert P.describe("prop@1-2m", "vanilla")["anchors"]["dims.H_in_peds"][1] == pytest.approx(1.5 / 1.84, abs=0.01)
    assert "dims.L_over_wheel" in P.describe("car.sedan", "vanilla")["anchors"]


def test_metric_selection(fake_cache):
    p = P.profile("car.sedan", "vanilla", metrics=["part.tris[*]"])
    assert set(p) == {"part.tris[chassis]", "part.tris[wheel]", "part.tris[bump_front_ok]", "part.tris[door_lf_ok]"}
    assert set(P.profile("car.sedan", "vanilla", metrics=["part.tris[chassis]", "dims.L"])) == {
        "part.tris[chassis]", "dims.L"}
    with pytest.raises(Exception):
        P.profile("car.sedan", "vanilla", metrics=["nope.metric"])


def test_fence_and_judge():
    s = [10.0, 20.0, 30.0, 50]
    lo, hi = P.fence(s)
    assert lo == pytest.approx(10 - 1.5 * 10) and hi == pytest.approx(30 + 1.5 * 10)
    tb = {"lo": 10.0, "hi": 30.0, "status": "measured"}
    assert [P.judge(x, s, tb) for x in (20, 9, -10, 44, 46)] == ["ok", "edge", "low", "edge", "high"]
    # a tight peer set keeps a relative floor; a small one borrows the parent's spread
    tight = [100.0, 100.0, 101.0, 40]
    assert P.fence(tight)[0] == pytest.approx(100 - 0.08 * 100)
    small, parent = [10.0, 11.0, 12.0, 6], [5.0, 11.0, 20.0, 200]
    assert P.fence(small, parent)[0] < P.fence(small)[0]


# ----------------------------------------------------------------------------- asset.check
def _check(tmp_path, data: bytes, name: str = "mycar", **kw) -> dict:
    p = tmp_path / f"{name}.dff"
    p.write_bytes(data)
    return CK.check_subject(S.load_file(p), K.load(), w2=False, **kw)


def _rows(r: dict, *verdicts: str) -> dict:
    return {(row[0], row[1]): row for row in r["rows"] if not verdicts or row[6] in verdicts}


def test_check_clean_synthetic_car_passes(fake_cache, tmp_path):
    r = _check(tmp_path, car_dff(), tier="vanilla")
    assert r["peer_set"] == "car" and "frames match vehicle type car" in r["how"]
    assert r["verdict"] == "pass", r["rows"]
    assert not _rows(r, "error", "warn", "defect")
    assert {row[9] for row in r["rows"]} <= set(CK.SECTIONS) and r["defects"] == 0
    assert {"engine", "fit", "coverage", "reference"} <= set(r["sections"])
    assert r["form"]["pieces"] >= 3 and "floating_pieces" in r["form"]


def test_check_semantic_errors(fake_cache, tmp_path):
    bad = car_dff(paint_tex="vehiclegeneric256", lamp_tex="vehiclegeneric256", arm_x=0.9, door_x=1.0, uv_sets=1)
    r = _check(tmp_path, bad, tier="vanilla")
    rows = _rows(r)
    assert r["verdict"] == "fail"
    assert rows[("veh.paint_dirt", "paint")][6] == "warn"
    assert rows[("veh.lamp_tex", "headlight left")][6] == "error"
    assert rows[("veh.dummy_side", "ped_arm")][6] == "error"
    assert rows[("veh.dummy_side", "door_lf_dummy")][6] == "error"
    assert rows[("veh.env_uv2", "chassis")][6] == "warn"
    assert all(len(row) == len(CK.COLS) for row in r["rows"])


def test_check_metrics_out_of_band_and_tier(fake_cache, tmp_path):
    from .rwkit import box_mesh, clump, geometry, material

    P_, T, _N = box_mesh(2.2, 4.0, 1.4)                     # flat-shaded (no normals), short car
    g = geometry(P_, [(a, b, c, 0) for a, b, c in T], [material((60, 255, 0, 255), "vehiclegrunge256")],
                 uv_sets=2, uv=[(0.5, 0.5)] * 8)
    frames = [(-1, "car", (0, 0, 0)), (0, "chassis_dummy", (0, 0, 0)), (1, "chassis", (0, 0, 0)),
              (0, "wheel_lf_dummy", (-0.9, 1.2, -0.3)), (0, "wheel_rf_dummy", (0.9, 1.2, -0.3)),
              (0, "wheel_lb_dummy", (-0.9, -1.2, -0.3)), (0, "wheel_rb_dummy", (0.9, -1.2, -0.3))]
    r = _check(tmp_path, clump([g], frames, [(2, 0)]), tier="vanilla")
    rows = _rows(r)
    assert rows[("geom.normals", "chassis")][6] == "error"
    assert rows[("veh.frames", "wheel")][6] == "warn"            # not a critical frame
    # numbers outside the vanilla range are reference rows: named, never a verdict
    uv, dl = rows[("uv.zero_area_share", "")], rows[("dims.L", "")]
    assert (uv[6], uv[9], dl[6]) == ("info", "reference", "info")
    assert uv[7].startswith("above vanilla p10..p90") and dl[7].startswith("below vanilla p10..p90")
    assert r["verdict"] == "fail"                                # geom.normals is an engine error
    sa = _check(tmp_path, clump([g], frames, [(2, 0)]), name="mycar2", tier="sa_plus")
    hd = _rows(sa)[("veh.hd_tris", "")]
    assert hd[6] == "info" and "proposal" not in hd[7] and "not a target" in hd[7]


def test_check_like_by_file_name_and_structure_diff(fake_cache, tmp_path, monkeypatch):
    like_scene = build_scene(car_dff(), name="car05", sec="cars")
    monkeypatch.setattr(S, "load_sid", lambda ident, profile="vanilla": type("X", (), {"scene": like_scene})())
    from .rwkit import box_mesh, clump, geometry, material

    P_, T, N = box_mesh(2.2, 5.6, 1.4)
    g = geometry(P_, [(a, b, c, 0) for a, b, c in T], [material(tex="vehiclegrunge256")], normals=N, uv_sets=2)
    frames = [(-1, "car05", (0, 0, 0)), (0, "chassis_dummy", (0, 0, 0)), (1, "chassis", (0, 0, 0)),
              (0, "wheel_lf_dummy", (-0.9, 1.6, -0.3)), (0, "wheel_rf_dummy", (0.9, 1.6, -0.3)),
              (0, "wheel_lb_dummy", (-0.9, -1.6, -0.3)), (0, "wheel_rb_dummy", (0.9, -1.6, -0.3)),
              (0, "my_extra", (0, 0, 0))]
    r = _check(tmp_path, clump([g], frames, [(2, 0)]), name="car05", tier="vanilla")
    assert r["like"] == "model:405" and r["peer_set"] == "car.sedan" and "replacement" in r["how"]
    rows = _rows(r)
    assert rows[("struct.frame_missing", rows and next(k[1] for k in rows if k[0] == "struct.frame_missing"))][6] \
        == "warn"
    assert ("struct.frame_extra", "my_extra") in rows
    assert ("struct.part_missing", "bump_front_ok,door_lf_ok,wheel") in rows


def test_check_map_model(fake_cache, tmp_path):
    r = _check(tmp_path, prop_dff(1.5, prelit=False), name="mybox", cls="prop")
    assert r["peer_set"] == "prop" or r["peer_set"].startswith("prop")
    assert _rows(r)[("geom.prelit", "box")][6] == "warn"
    auto = _check(tmp_path, prop_dff(1.5), name="mybox2")
    assert auto["peer_set"] == "prop@1-2m" and "by size" in auto["how"]


def test_col_face_light_and_draw(fake_cache, tmp_path):
    from .rwkit import col3

    (tmp_path / "mybox.col").write_bytes(col3("mybox", [(0, 0, 0, 1, 0)], face_light=0))
    (tmp_path / "x.ide").write_text("objs\n9000, mybox, mytxd, 400, 0\nend\n", encoding="latin-1")
    r = _check(tmp_path, prop_dff(1.5), name="mybox")
    rows = _rows(r)
    assert rows[("col.face_light", "")][6] == "warn"
    assert rows[("ide.draw_hd_col", "")][6] == "error" and r["verdict"] == "fail"


# ----------------------------------------------------------------------------- anatomy, cards, validation
def test_anatomy_json_and_markdown(tmp_path):
    p = tmp_path / "mycar.dff"
    p.write_bytes(car_dff())
    a = A.anatomy(S.load_file(p))
    assert a["frames_n"] == 13 and [f["name"] for f in a["frames"]][:3] == ["car", "chassis_dummy", "chassis"]
    roles = {m["role"] for m in a["materials"]}
    assert {"paint1", "lamp:headlight_left", "plain"} <= roles
    assert a["col"]["spheres"] == 2 and a["col"]["sphere_pieces"] == {"0": 1, "2": 1}
    md = A.to_markdown(a)
    assert md.count("\n|") >= 13 and "env xvehicleenv128 0.5" in md and len(md) < 4096


def test_card_markdown_and_lint_preset(fake_cache):
    md = CD.card_md("car.sedan", "sa_plus")
    assert "| `veh.hd_tris` | count |" in md and "never targets" in md and "proposal" not in md
    for line in md.splitlines():
        if line.startswith("| `"):
            assert len(re.findall(r"(?<!\\)\|", line)) == 7
    for tier in ("vanilla", "sa_plus"):
        lp = CD.lint_preset(tier)
        r = lp["rules"]
        assert {"dff.flat_shading", "dff.vert_sharing"} <= set(r)
        assert all(r[k] == {"sev": "info"} for k in ("dff.tris_budget", "veh.hd_tris", "veh.part_tris"))
        assert json.loads(json.dumps(lp)) == lp


def test_leave_one_out(fake_cache):
    r = V.loo(fake_cache)
    assert set(r["families"]) == {"vehicle", "map"}
    assert r["families"]["vehicle"]["pass_share"] >= 0.9 and r["families"]["map"]["pass_share"] >= 0.9
    assert "prop@1-2m" in r["peer_sets"]


def test_subject_folder_and_manifest(tmp_path):
    (tmp_path / "a.dff").write_bytes(prop_dff())
    (tmp_path / "b.dff").write_bytes(prop_dff(3))
    (tmp_path / "asset.json").write_text(json.dumps({"tier": "vanilla", "like": "model:1000"}), encoding="utf-8")
    subs = S.load(str(tmp_path))
    assert [Path(s.label).name for s in subs] == ["a.dff", "b.dff"]
    assert subs[0].manifest == {"tier": "vanilla", "like": "model:1000"}


def test_w2_rows_take_errors_from_col_check_and_fx2d_check(monkeypatch):
    """asset.check summary rows: col.check counts severities in 'summary', fx2d.check in 'errors'."""
    from types import SimpleNamespace

    from satk.core import registry
    from satk.style import check as CH

    replies = {"col.check": {"ok": True, "rows": [[1]], "total": 1, "summary": {"warn": 1}},
               "fx2d.check": {"ok": True, "rows": [[1], [2]], "total": 2, "errors": 2, "warnings": 0}}
    monkeypatch.setattr(registry, "get_op", lambda name: name)
    monkeypatch.setattr(registry, "invoke", lambda name, args: replies[name])
    subj = SimpleNamespace(label="x.dff", col=object(), scene=SimpleNamespace(info=SimpleNamespace(effects=[1])))
    rows = {r[0]: r for r in CH._w2_rows(subj, "vanilla")}
    assert rows["col.check"][6] == "info" and rows["col.check"][2] == 1
    assert rows["fx2d.check"][6] == "warn" and rows["fx2d.check"][2] == 2
