"""asset.check rows: optional parts, sections, reference rows that never judge, what the default output shows."""

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


def test_reference_rows_are_information_in_every_tier(fake_cache):
    """Vanilla numbers are reference: the row is 'info' in the reference section, whatever the value or tier."""
    for tier in ("vanilla", "sa_plus"):
        for value in (2500.0, 10.0, 48.0):
            row = _rows(CK.metric_rows({"veh.hd_tris": value}, fake_cache, "car", tier), "veh.hd_tris")[0]
            assert row[6] == "info" and row[9] == "reference" and "reference, not a target" in row[7], row
            assert "vanilla p10..p90" in row[7] and len(row) == len(CK.COLS)


def _fake_check(rows: list[list]):
    def fn(subj, cache, **kw):
        counts: dict[str, int] = {}
        sections: dict[str, dict] = {}
        for r in rows:
            counts[r[6]] = counts.get(r[6], 0) + 1
            sections.setdefault(r[9], {})[r[6]] = sections.get(r[9], {}).get(r[6], 0) + 1
        return {"target": "x.dff", "class": "prop", "peer_set": "prop", "how": "test", "tier": "sa_plus", "like": None,
                "rows": rows, "counts": counts, "sections": sections, "defects": counts.get("defect", 0),
                "verdict": "review", "metrics": {}, "notes": []}
    return fn


def test_default_output_shows_findings_and_hides_information(fake_cache, tmp_path, monkeypatch):
    rows = [["geom.prelit", "box", 1, None, None, None, "warn", "h", "r", "engine"],
            ["form.floating", "chassis", 12.0, None, None, None, "defect", "h", "r", "form"],
            ["cov.plates", "", "missing", None, None, None, "missing", "h", "r", "coverage"],
            ["cov.glass", "", "present", None, None, None, "present", "h", "r", "coverage"],
            ["geo.tris", "", 540, 10, 84, 324, "info", "above vanilla p10..p90", "r", "reference"],
            ["col.check", "", 1, None, None, None, "info", "h", "r", "engine"]]
    monkeypatch.setattr(CK, "check_subject", _fake_check(rows))
    p = tmp_path / "x.dff"
    p.write_bytes(prop_dff(1.0))
    spec = get_op("asset.check")
    default = spec.call({"target": str(p)})
    assert [r[0] for r in default["rows"]] == ["geom.prelit", "form.floating", "cov.plates"]
    assert default["cols"][-1] == "section" and default["defects"] == 1 and default["sections"]["form"] == {"defect": 1}
    full = spec.call({"target": str(p), "full": True})
    assert [r[0] for r in full["rows"]] == [r[0] for r in rows]
    md = spec.call({"target": str(p), "md": True})["text"]
    assert "## form" in md and "## coverage" in md and "never targets" in md
