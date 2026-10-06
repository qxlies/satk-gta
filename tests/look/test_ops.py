"""The operation blender.preview with the Blender job replaced by synthetic cells."""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.core.registry import get_op


@pytest.fixture
def fake_job(monkeypatch, tmp_path, png):
    """Replace ``runner.run_job``: one PNG cell per (row, view) of the spec; records the spec."""
    from satk.blender import runner

    seen: dict = {}

    def run_job(cmd, args, **kw):
        assert cmd == "preview"
        spec = args["spec"]
        seen["spec"] = spec
        w, h = spec["size"]
        rows = [f"{s}/{p}" for s in spec["states"] for p in spec["passes"]]
        cells = []
        for r in range(len(rows)):
            for c in range(len(spec["views"])):
                cells.append([r, c, str(png(tmp_path / f"cell{r}{c}.png", w, h, (90, 100, 110)))])
        return {"ok": True, "job": "fake", "warnings": ["NOT_FOUND: x has no dam parts"],
                "preview": {"cells": cells, "rows": rows, "cols": spec["views"], "stats": {},
                            "env": [{"time": "12:00"}], "seconds": {"total": 0.1}}}

    monkeypatch.setattr(runner, "run_job", run_job)
    return seen


def test_op_is_cli_only_and_documented():
    op = get_op("blender.preview")
    assert op.mcp is False or op.mcp in (None, False)
    assert len(op.summary) <= 300 and "session" in op.summary


def test_preview_of_a_file(satk_home, car_files, fake_job):
    r = get_op("blender.preview").call({"subject": str(car_files / "mycar.dff"), "states": ["ok", "dam"],
                                        "passes": ["game", "wire"]})
    assert r["ok"]
    sheet = Path(r["files"]["sheet"])
    assert sheet.is_file() and sheet.suffix == ".jpg" and r["sheet"]["bytes"] <= 300_000
    assert max(r["sheet"]["w"], r["sheet"]["h"]) <= 1024
    assert r["legend"]["entries"] == [["e0", "mycar.dff", "file"]]
    assert r["legend"]["rows"] == ["ok/game", "ok/wire", "dam/game", "dam/wire"]
    st = r["stats"]["e0"]
    assert st["geo.tris"] > 0 and "shade.normal_bend" in st and len(st["dims"]) == 3
    spec = fake_job["spec"]
    assert spec["views"] == ["3q", "rear3q", "side", "top"] and spec["dirt"] == 2.0 and spec["lights"] == "off"
    assert spec["envs"][0]["time"] == "12:00" and spec["entries"][0]["plan"]["sec"] == "cars"
    assert any(w.startswith("NOT_FOUND") for w in r["warn"])


def test_preview_tex_pass_only_needs_no_blender(satk_home, car_files, monkeypatch):
    from satk.blender import runner

    def boom(*a, **k):
        raise AssertionError("no Blender for a texture pass")

    monkeypatch.setattr(runner, "run_job", boom)
    r = get_op("blender.preview").call({"subject": str(car_files / "mycar.dff"), "passes": ["tex"]})
    assert Path(r["files"]["textures"]).suffix == ".png" and "sheet" not in r["files"]
    assert {row[1] for row in r["legend"]["textures"]} == {"mycar/paint", "mycar/tyre"}


def test_lineup_of_explicit_files_times_and_layout(satk_home, car_files, fake_job, m):
    other = car_files.parent / "other.dff"
    other.write_bytes(m.box_dff(tex=None, size=(1.0, 1.0, 1.0)))
    r = get_op("blender.preview").call({"subject": str(car_files / "mycar.dff"), "lineup": str(other),
                                        "time": ["12:00", "22:00"]})
    spec = fake_job["spec"]
    assert [e["label"] for e in spec["entries"]] == ["mycar.dff", "other.dff"]
    assert spec["views"] == ["side", "3q"] and spec["size"][0] > spec["size"][1]   # wide lineup cells
    assert [e["balance"] for e in spec["envs"]] == [0.0, 1.0]
    assert r["legend"]["entries"][1] == ["e1", "other.dff", "file"]


@pytest.mark.parametrize("args,code", [
    ({"passes": ["shiny"]}, "BAD_PARAMS"), ({"states": ["broken"]}, "BAD_PARAMS"), ({"dirt": 17}, "BAD_PARAMS"),
    ({"size": 20}, "BAD_PARAMS"), ({"time": ["25:00"]}, "BAD_PARAMS"), ({"context": 40}, "BAD_PARAMS"),
])
def test_bad_parameters(satk_home, car_files, fake_job, args, code):
    with pytest.raises(SatkError) as ei:
        get_op("blender.preview").call({"subject": str(car_files / "mycar.dff"), **args})
    assert ei.value.code == code


def test_same_call_same_folder(satk_home, car_files, fake_job):
    a = get_op("blender.preview").call({"subject": str(car_files / "mycar.dff"), "views": ["3q"]})
    b = get_op("blender.preview").call({"subject": str(car_files / "mycar.dff"), "views": ["3q"]})
    assert a["files"]["sheet"] == b["files"]["sheet"]
    c = get_op("blender.preview").call({"subject": str(car_files / "mycar.dff"), "views": ["side"]})
    assert c["files"]["sheet"] != a["files"]["sheet"]
