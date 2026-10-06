"""asset.check row fixes of lane a2-kit: optional parts, edge rows by default, the band named in every message."""

from __future__ import annotations

import pytest

from satk.core.registry import get_op
from satk.model3d.mesh import build_scene
from satk.style import check as CK
from satk.style import subject as S

from .rwkit import car_dff, prop_dff
from .test_core import fake_cache  # noqa: F401  (the synthetic style cache fixture)

np = pytest.importorskip("numpy")


def _subject(tmp_path, data: bytes, name: str):
    p = tmp_path / f"{name}.dff"
    p.write_bytes(data)
    return S.load_file(p)


def _rows(rows: list[list], check: str) -> list[list]:
    return [r for r in rows if r[0] == check]


def test_parts_are_optional_for_map_models(fake_cache, tmp_path):
    """A prop replaces or imitates a model whose atomic has another name: no struct.part_missing, ever."""
    like = build_scene(car_dff(), name="car05", sec="cars")           # a like model with many atomics
    subj = _subject(tmp_path, prop_dff(1.0), "mybox")
    for replacing in (True, False):
        rows = CK.structure_rows(subj, fake_cache, "prop", like, replacing)
        assert not _rows(rows, "struct.part_missing"), replacing


def test_missing_parts_warn_for_a_replacement_and_only_inform_for_a_new_vehicle(fake_cache, tmp_path):
    like = build_scene(car_dff(), name="car05", sec="cars")
    subj = _subject(tmp_path, prop_dff(1.0), "mycar")
    warn = _rows(CK.structure_rows(subj, fake_cache, "car", like, True), "struct.part_missing")
    info = _rows(CK.structure_rows(subj, fake_cache, "car", like, False), "struct.part_missing")
    assert [r[6] for r in warn] == ["warn"] and [r[6] for r in info] == ["info"]
    assert "optional for a new model" in info[0][7] and "optional" not in warn[0][7]


def test_check_subject_tells_a_replacement_from_a_new_model(fake_cache, tmp_path, monkeypatch):
    like_scene = build_scene(car_dff(), name="car05", sec="cars")
    monkeypatch.setattr(S, "load_sid", lambda ident, profile="vanilla": type("X", (), {"scene": like_scene})())
    from .rwkit import box_mesh, clump, geometry, material

    P_, T, N = box_mesh(2.2, 5.6, 1.4)
    g = geometry(P_, [(a, b, c, 0) for a, b, c in T], [material(tex="vehiclegrunge256")], normals=N, uv_sets=2)
    frames = [(-1, "mycar", (0, 0, 0)), (0, "chassis_dummy", (0, 0, 0)), (1, "chassis", (0, 0, 0)),
              (0, "wheel_lf_dummy", (-0.9, 1.6, -0.3)), (0, "wheel_rf_dummy", (0.9, 1.6, -0.3)),
              (0, "wheel_lb_dummy", (-0.9, -1.6, -0.3)), (0, "wheel_rb_dummy", (0.9, -1.6, -0.3))]
    data = clump([g], frames, [(2, 0)])
    new = _subject(tmp_path, data, "mycar")
    r = CK.check_subject(new, fake_cache, like="model:405", tier="vanilla", w2=False)
    assert [x[6] for x in _rows(r["rows"], "struct.part_missing")] == ["info"]
    rep = _subject(tmp_path, data, "car05")
    r = CK.check_subject(rep, fake_cache, like="model:405", tier="vanilla", w2=False)
    assert [x[6] for x in _rows(r["rows"], "struct.part_missing")] == ["warn"]


def test_metric_hints_name_the_tier_band_for_every_verdict(fake_cache):
    m = {"veh.hd_tris": 2500.0}
    cols = {}
    for tier in ("vanilla", "sa_plus"):
        rows = CK.metric_rows(m, fake_cache, "car", tier, all_rows=True)
        cols[tier] = _rows(rows, "veh.hd_tris")[0]
    for tier, row in cols.items():
        assert f"the {tier} band" in row[7], row
    assert cols["sa_plus"][7].endswith("(proposal)") and cols["vanilla"][7].endswith("(vanilla)")
    if cols["vanilla"][6] == "ok":
        assert cols["vanilla"][7].startswith("inside ")


def _fake_check(rows: list[list]):
    def fn(subj, cache, **kw):
        counts: dict[str, int] = {}
        for r in rows:
            counts[r[6]] = counts.get(r[6], 0) + 1
        return {"target": "x.dff", "class": "prop", "peer_set": "prop", "how": "test", "tier": "sa_plus", "like": None,
                "rows": rows, "counts": counts, "out_of_band": 0, "verdict": "review", "metrics": {}, "notes": []}
    return fn


def test_default_output_shows_edge_rows_and_hides_ok_and_info(fake_cache, tmp_path, monkeypatch):
    rows = [["geom.prelit", "box", 1, None, None, None, "warn", "h", "r"],
            ["geo.tris", "", 540, 10, 84, 324, "edge", "near the edge", "r"],
            ["dims.size", "", 1.0, 0.5, 1.0, 2.0, "ok", "inside", "r"],
            ["col.check", "", 1, None, None, None, "info", "h", "r"]]
    monkeypatch.setattr(CK, "check_subject", _fake_check(rows))
    p = tmp_path / "x.dff"
    p.write_bytes(prop_dff(1.0))
    spec = get_op("asset.check")
    default = spec.call({"target": str(p)})
    assert [r[0] for r in default["rows"]] == ["geom.prelit", "geo.tris"]
    full = spec.call({"target": str(p), "full": True})
    assert [r[0] for r in full["rows"]] == ["geom.prelit", "geo.tris", "dims.size", "col.check"]
