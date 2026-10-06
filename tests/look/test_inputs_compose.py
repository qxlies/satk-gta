"""Preview subjects (files, folders, sessions), lineup scoring, sheets and texture crops (no Blender)."""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.look import compose, inputs, lineup


def test_file_subject_finds_the_sibling_txd_and_is_a_vehicle(satk_home, car_files):
    es, warn = inputs.resolve_subject(str(car_files / "mycar.dff"))
    e = es[0]
    assert e.kind == "file" and e.label == "mycar.dff" and e.sec == "cars"
    plan = e.plan
    assert plan["sid"] == "file:mycar.dff" and plan["txd_names"][0] == "mycar"
    work = str(satk_home).replace("\\", "/").lower()
    assert plan["dff"].lower().startswith(work) and all(t.lower().startswith(work) for t in plan["txd"])  # cached copies
    assert plan["vehicle"]["colors"] and len(plan["vehicle"]["colors"]) == 4      # default paint
    assert e.dff_bytes() == (car_files / "mycar.dff").read_bytes()
    assert any(w.startswith("NO_VEHICLE_TXD") for w in warn)                      # no game data here
    spec = e.spec()
    assert spec == {"key": "e0", "label": "mycar.dff", "plan": plan, "source": "file"}


def test_file_without_txd_warns_and_explicit_txd(satk_home, car_files):
    (car_files / "lone.dff").write_bytes((car_files / "mycar.dff").read_bytes())
    es, warn = inputs.resolve_subject(str(car_files / "lone.dff"))
    assert not es[0].plan["txd"] and any(w.startswith("NO_TXD") for w in warn)
    es, warn = inputs.resolve_subject(str(car_files / "lone.dff"), txd=[str(car_files / "mycar.txd")])
    assert es[0].plan["txd_names"][0] == "mycar" and not any(w.startswith("NO_TXD") for w in warn)


def test_folder_subject_and_shared_txd(satk_home, car_files, m):
    (car_files / "crate.dff").write_bytes(m.box_dff(tex="boxtex", size=(1.0, 1.0, 1.0)))
    es, _w = inputs.resolve_subject(str(car_files))
    assert [e.label for e in es] == ["crate.dff", "mycar.dff"] and [e.key for e in es] == ["e0", "e1"]
    assert es[0].sec == "objs" and es[1].sec == "cars"
    d = car_files.parent / "one"
    d.mkdir()
    (d / "crate.dff").write_bytes(m.box_dff(tex="boxtex"))
    (d / "shared.txd").write_bytes(m.solid_txd({"boxtex": (1, 2, 3, 255)}))
    es, _w = inputs.resolve_subject(str(d))
    assert es[0].plan["txd_names"] == ["shared"]                                   # the folder's only TXD
    empty = car_files.parent / "empty"
    empty.mkdir()
    with pytest.raises(SatkError) as ei:
        inputs.resolve_subject(str(empty))
    assert ei.value.code == "NOT_FOUND"


def test_session_and_bad_subjects(satk_home, tmp_path):
    es, warn = inputs.resolve_subject("session:car")
    assert es[0].kind == "session" and es[0].spec() == {"key": "e0", "label": "session:car", "scene": True}
    assert inputs.session_name("session:x") == "x" and inputs.session_name("model:1") is None
    with pytest.raises(SatkError):
        inputs.resolve_subject("session:Bad Name")
    (tmp_path / "a.txd").write_bytes(b"x")
    with pytest.raises(SatkError) as ei:
        inputs.resolve_subject(str(tmp_path / "a.txd"))
    assert ei.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as ei:
        inputs.resolve_subject(str(tmp_path / "missing.dff"))
    assert ei.value.code == "NOT_FOUND"


def test_lineup_scoring_prefers_class_then_size():
    ref = {"id": 1, "sec": "cars", "type": "car", "class": "richfamily", "tris": 2000, "dims": [2.5, 5.5, 1.5], "r": 3}
    a = {"id": 2, "sec": "cars", "type": "car", "class": "richfamily", "tris": 2100, "dims": [2.4, 5.0, 1.4], "r": 3}
    b = {"id": 3, "sec": "cars", "type": "car", "class": "normal", "tris": 2000, "dims": [2.5, 5.5, 1.5], "r": 3}
    c = {"id": 4, "sec": "cars", "type": "car", "class": "richfamily", "tris": 2000, "dims": [2.5, 5.4, 1.5], "r": 3}
    assert sorted([a, b, c], key=lambda r: lineup._score(ref, r))[0]["id"] == 4
    assert lineup._score(ref, b)[0] == 1
    r = lineup.reference(sec="objs", dims=[1, 1, 1], tris=12)
    assert r["sec"] == "objs" and r["r"] == pytest.approx(0.866, abs=1e-3)
    with pytest.raises(SatkError):
        lineup.reference()


def test_layout_and_cell_size():
    assert compose.layout(1, 2, 384) == (384, 384, None)
    w, h, grid = compose.layout(8, 2, 384)               # many states x passes, 2 views: wrap into a grid
    assert grid and w > compose.cell_size(384, 2, 8)
    w, h, grid = compose.layout(1, 2, 384, aspect=3.0)   # a lineup: wide cells
    assert w == 1024 and h == 341
    for rows, cols in ((1, 4), (2, 4), (4, 4), (3, 1)):
        w, h, grid = compose.layout(rows, cols, 384)
        gc = grid or cols
        gr = -(-rows * cols // gc)
        assert gc * w <= 1024 and gr * h + 16 <= 1024


def test_sheet_is_a_small_jpeg_with_labels(satk_home, tmp_path, png):
    cells = []
    for r in range(2):
        for c in range(3):
            cells.append([r, c, str(png(tmp_path / f"c{r}{c}.png", 300, 300, (40 * c, 90, 30 * r)))])
    out = satk_home / "work" / "out" / "preview" / "t" / "preview.jpg"
    info = compose.sheet(cells, ["ok/game", "dam/game"], ["3q", "side", "top"], title="test", out=out)
    assert Path(info["path"]).is_file() and info["format"] == "jpg"
    assert info["w"] <= 1024 and info["h"] <= 1024 and info["bytes"] <= compose.MAX_BYTES
    assert Path(info["path"]).read_bytes()[:3] == b"\xff\xd8\xff"
    big = compose.sheet(cells, ["a", "b"], ["x", "y", "z"], title="t", out=out.with_name("g.jpg"), grid=2)
    assert big["h"] > big["w"]                            # 6 cells wrapped into 2 columns


def test_texture_sheet_native_scale(satk_home, tmp_path, m):
    txd = tmp_path / "own.txd"
    txd.write_bytes(m.solid_txd({"a": (255, 0, 0, 255), "b": (0, 255, 0, 255)}, size=8))
    info = compose.texture_sheet([str(txd)], satk_home / "work" / "out" / "preview" / "t" / "textures.png")
    assert info["format"] == "png" and [row[1] for row in info["legend"]] == ["own/a", "own/b"]
    assert info["legend"][0][2] == "8x8"
