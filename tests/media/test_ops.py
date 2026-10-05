"""CLI/MCP operations of satk.media: texture.image, texture.export_all, map.image (SPEC §4.6, §4.7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.core import registry as R


def test_ops_registered_with_mcp_names():
    R.discover()
    assert R.get_op("texture.image").mcp_name == "texture_image"
    assert R.get_op("map.image").mcp_name == "map_image"
    assert R.get_op("texture.export_all").mcp_name is None
    assert R.get_op("texture.export_all").cli == "texture export-all"
    for name in ("texture.image", "map.image"):
        spec = R.get_op(name)
        assert spec.mcp_group == "media" and any(p.name == "inline" for p in spec.params)


def test_mcp_schema_is_small():
    """tools/list budget (16 KB for 25 tools, SPEC §4.7): each media tool stays well under 1 KB."""
    from satk.mcp import adapter as A

    for name in ("texture_image", "map_image"):
        d = A.tool_def(R.op_by_mcp(name))
        size = len(json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode())
        assert size <= 850, (name, size)


def test_cli_texture_image_png(fake_db, run_cli, mh):
    """Acceptance 1 (fake data): files[0] = 64x64 PNG under work/cache/tex; repeat -> same sha256."""
    r = run_cli(["texture", "image", "tex:bistro/vent_64", "--json"])
    assert r.code == 0, r.out + r.err
    env = r.json
    f = Path(env["files"][0])
    assert "/work/cache/tex/" in env["files"][0] and mh.png_pixels(f)[:2] == (64, 64)
    assert env["legend"] == [[1, "tex:bistro/vent_64", "64x64 X8R8G8B8"]]
    sha = f.read_bytes()
    assert run_cli(["texture", "image", "tex:bistro/vent_64", "--json"]).json == env and f.read_bytes() == sha


def test_cli_texture_image_sheet_and_sizes(fake_db, run_cli, mh):
    r = run_cli(["texture", "image", "txd:bistro", "model:411", "--mode", "sheet", "--json"])
    assert r.code == 0, r.out + r.err
    env = r.json
    assert len(env["files"]) == 1 and len(env["legend"]) == 15
    w, h, _ = mh.png_pixels(env["files"][0])
    assert (w, h) == (532, 532)
    r = run_cli(["texture", "image", "tex:vehicle/vehiclegeneric256", "--size", "100", "--json"])
    assert mh.png_pixels(r.json["files"][0])[:2] == (100, 100)
    r = run_cli(["texture", "image", "txd:bistro", "--mode", "sheet", "--size", "64", "--json"])
    assert mh.png_pixels(r.json["files"][0])[:2] == (2 * 4 + 2 * 64 + 4, 4 * 2 + 64)


def test_texture_image_paging_and_truncation(fake_db, monkeypatch):
    from satk.media import ops as O
    from satk.media import sheet as S

    monkeypatch.setattr(S, "MAX_CELLS", 8)
    env = R.invoke("texture.image", {"ids": ["model:411", "txd:bistro"], "mode": "sheet"})
    assert env["ok"] and len(env["files"]) == 2 and [r[0] for r in env["legend"]] == list(range(1, 16))
    assert any(w.startswith("PAGES") for w in env["warn"])
    monkeypatch.setattr(O, "PNG_MAX", 3)
    env = R.invoke("texture.image", {"ids": ["model:411"]})
    assert len(env["files"]) == 3 and any(w.startswith("TRUNCATED") for w in env["warn"])


def test_texture_image_errors(fake_db, run_cli):
    env = R.invoke("texture.image", {"ids": ["model:300"]})
    assert env["error"]["code"] == "NOT_FOUND"
    env = R.invoke("texture.image", {"ids": ["inst:lae2_stream0#4"]})
    assert env["error"]["code"] == "BAD_ID"
    assert R.invoke("texture.image", {"ids": []})["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["texture", "image", "tex:bistro/vent_64", "--mode", "gif"])
    assert r.code == 2


def test_cli_map_image_center(fake_db, run_cli, mh):
    """Acceptance 5 shape (fake data): 768x768 PNG, legend <= 20, m_per_px ~ 0.39."""
    r = run_cli(["map", "image", "--center", "2489.3,-1668.5", "--span", "300", "--json"])
    assert r.code == 0, r.out + r.err
    env = r.json
    assert mh.png_pixels(env["file"])[:2] == (768, 768)
    assert len(env["legend"]) <= 20 and env["legend"][0][1] == "inst:lae2_stream0#4"
    assert env["m_per_px"] == pytest.approx(0.39, abs=0.005)
    mcp = R.invoke(R.op_by_mcp("map_image"), {"x": 2489.3, "y": -1668.5, "inline": True})
    assert mcp["file"] == env["file"] and mcp["legend"] == env["legend"]
    r = run_cli(["map", "image", "--center", "2489.3,-1668.5", "--layers", "inst,lod", "--labels", "0", "--json"])
    assert r.json["counts"] == {"inst": 1, "lod": 1, "zone": 0} and "legend" not in r.json


def test_map_image_param_errors(fake_db, run_cli):
    assert run_cli(["map", "image"]).code == 2
    assert run_cli(["map", "image", "--center", "1,2,3"]).code == 2
    assert R.invoke("map.image", {"x": 1.0})["error"]["code"] == "BAD_PARAMS"
    assert R.invoke("map.image", {"x": 1.0, "y": 2.0, "layers": ["water"]})["error"]["code"] == "BAD_PARAMS"


def test_cli_export_all(fake_db, run_cli):
    r = run_cli(["texture", "export-all", "--jobs", "1", "--json"])
    assert r.code == 0, r.out + r.err
    env = r.json
    assert env["written"] + env["skipped"] == env["images"] and env["written"] > 0
    assert run_cli(["texture", "export-all", "--jobs", "1", "--json"]).json["written"] == 0
    assert run_cli(["texture", "export-all", "--jobs", "-1"]).code == 2


def test_index_missing_is_not_ready(satk_home, run_cli):
    r = run_cli(["texture", "image", "tex:bistro/vent_64", "--json"])
    assert r.code == 3 and r.json["error"]["code"] in ("INDEX_MISSING", "NOT_READY")
    r = run_cli(["map", "image", "--center", "0,0", "--json"])
    assert r.code == 3


def test_ops_write_only_under_work(fake_db, satk_home, tmp_path, run_cli):
    """Acceptance 6: nothing outside work/ is created by any media operation."""
    before = set(tmp_path.rglob("*"))
    assert run_cli(["texture", "image", "model:411", "--json"]).code == 0
    assert run_cli(["texture", "image", "model:411", "--mode", "sheet", "--labels", "--json"]).code == 0
    assert run_cli(["map", "image", "--center", "2489.3,-1668.5", "--layers", "inst,lod,zone", "--json"]).code == 0
    assert run_cli(["texture", "export-all", "--jobs", "1", "--json"]).code == 0
    new = set(tmp_path.rglob("*")) - before
    work = satk_home / "work"
    outside = sorted(str(p) for p in new if p != work and work not in p.parents)
    assert new and outside == []


def test_status_and_doctor(satk_home):
    st = R.status_providers()["media"](True)
    assert st["png"] in ("pillow", "zlib") and st["cached"] == 0 and st["cache"].endswith("/work/cache/tex")
    d = R.doctor_checks()["media_pillow"]()
    assert d["status"] in ("ok", "warn") and d["msg"]
