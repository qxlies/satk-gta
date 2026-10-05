"""``satk model image`` / ``satk asset export`` end to end over ``FakeIndexDB`` with synthetic payloads."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from satk.core import paths
from satk.core.registry import all_ops, discover
from satk.model3d.gltf import parse_glb
from satk.model3d.png import png_size

pytest.importorskip("numpy")


def _work() -> Path:
    return Path(os.path.abspath(paths.cfg().paths.work))


def _under_work(p: str) -> bool:
    return os.path.normcase(os.path.abspath(p)).startswith(os.path.normcase(str(_work())))


def test_ops_registered_with_mcp_names():
    discover()
    ops = {o.name: o for o in all_ops()}
    img, exp = ops["model.image"], ops["asset.export"]
    assert img.mcp_name == "model_image" and exp.mcp_name == "asset_export"
    assert img.mcp_group == "media" and exp.mcp_group == "media"
    assert len(img.summary) <= 300 and len(exp.summary) <= 300
    schema = img.input_schema()
    assert "id" not in schema.get("required", []) and schema["properties"]["backend"]["enum"] == [
        "auto", "soft", "ariane", "blender"]
    assert exp.input_schema()["required"] == ["id"]


def test_model_image_cli_cache_and_determinism(fake_db, run_cli):
    r = run_cli(["model", "image", "model:411", "--json"])
    assert r.code == 0, r.out
    j = r.json
    assert j["backend"] == "soft" and j["cached"] is False and j["name"] == "infernus"
    f = Path(j["file"])
    assert f.is_file() and _under_work(j["file"]) and f.parent == _work() / "cache" / "model"
    assert f.name.endswith("-soft-4-384.png")
    data = f.read_bytes()
    assert png_size(data) == (768, 768) == (j["w"], j["h"])
    assert j["legend"] == [[1, 45.0, 25.0], [2, 135.0, 25.0], [3, 225.0, 25.0], [4, 315.0, 25.0]]
    assert j["stats"]["tris"] == 60 and j["stats"]["tex_missing"] == 0
    assert all(c >= 0.05 for c in j["stats"]["cover"])
    side = json.loads(f.with_suffix(".json").read_text(encoding="utf-8"))
    assert side["id"] == "model:411" and side["stats"] == j["stats"]
    r2 = run_cli(["model", "image", "model:411", "--json"]).json
    assert r2["cached"] is True and r2["file"] == j["file"]
    r3 = run_cli(["model", "image", "model:411", "--force", "--json"]).json
    assert r3["cached"] is False and f.read_bytes() == data          # re-render gives the same bytes


def test_model_image_sizes_and_ids(fake_db, run_cli):
    j = run_cli(["model", "image", "lae2_roads89", "--views", "1", "--size", "64", "--json"]).json
    assert j["id"] == "model:17613" and png_size(Path(j["file"]).read_bytes()) == (64, 64)
    j2 = run_cli(["model", "image", "inst:lae2_stream0#4", "--views", "1", "--size", "64", "--json"]).json
    assert j2["id"] == "model:17613" and j2["file"] == j["file"]
    j3 = run_cli(["model", "image", "dff:infernus", "--views", "2", "--size", "32", "--json"]).json
    assert j3["id"] == "model:411" and png_size(Path(j3["file"]).read_bytes()) == (64, 32)


def test_model_image_hier_without_dff_is_placeholder(fake_db, run_cli):
    r = run_cli(["model", "image", "model:300", "--json"])
    assert r.code == 0
    j = r.json
    assert j["backend"] == "none" and j["stats"]["tris"] == 0 and j["warn"][0].startswith("NO_DFF")
    assert png_size(Path(j["file"]).read_bytes()) == (768, 768)


@pytest.mark.parametrize("backend", ["ariane", "blender"])
def test_backend_fallback_to_soft(fake_db, run_cli, backend, monkeypatch):
    import sys
    import types

    import satk.blender as SB

    empty = types.ModuleType("satk.blender.runner")              # never start a real Blender in unit tests
    monkeypatch.setitem(sys.modules, "satk.blender.runner", empty)
    monkeypatch.setattr(SB, "runner", empty, raising=False)
    j = run_cli(["model", "image", "model:17613", "--backend", backend, "--views", "1", "--size", "48", "--json"]).json
    assert j["ok"] and j["backend"] == "soft"
    assert any(w.startswith(f"FALLBACK: backend {backend}") for w in j["warn"])


def test_errors(fake_db, run_cli):
    r = run_cli(["model", "image", "model:999999", "--json"])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"
    r = run_cli(["model", "image", "--json"])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["model", "image", "model:411", "--views", "0", "--json"])
    assert r.code == 2
    r = run_cli(["model", "image", "tex:bistro/vent_64", "--json"])
    assert r.json["error"]["code"] == "BAD_ID"
    r = run_cli(["asset", "export", "model:411", "--format", "glb", "--out", str(_work().parent.parent), "--json"])
    assert r.code == 2 and "inside the work directory" in r.json["error"]["msg"]
    r = run_cli(["asset", "export", "model:300", "--format", "glb", "--json"])
    assert r.json["error"]["code"] == "NOT_FOUND"


def test_export_glb_obj_png(fake_db, run_cli):
    j = run_cli(["asset", "export", "model:411", "--format", "glb", "--json"]).json
    glb = Path(j["files"][0])
    assert glb == _work() / "out" / "models" / "infernus" / "infernus.glb"
    doc, _bin = parse_glb(glb.read_bytes())
    assert doc["asset"]["version"] == "2.0" and j["stats"]["tris"] == 60
    j = run_cli(["asset", "export", "model:17613", "--format", "glb", "--json"]).json
    doc, _bin = parse_glb(Path(j["files"][0]).read_bytes())
    assert "COLOR_0" in doc["meshes"][0]["primitives"][0]["attributes"] and j["stats"]["tris"] == 12
    j = run_cli(["asset", "export", "model:411", "--format", "obj", "--json"]).json
    files = [Path(f) for f in j["files"]]
    assert [f.suffix for f in files[:2]] == [".obj", ".mtl"]
    mtl = files[1].read_text(encoding="utf-8")
    for ln in mtl.splitlines():
        if ln.startswith(("map_Kd", "map_d")):
            assert (files[1].parent / ln.split(" ", 1)[1]).is_file()
    assert sum(1 for ln in files[0].read_text(encoding="utf-8").splitlines() if ln.startswith("f ")) == 60
    j = run_cli(["asset", "export", "model:411", "--format", "png", "--json"]).json
    assert sorted(Path(f).name for f in j["files"]) == ["paint.png", "tyre.png"]
    assert all(_under_work(f) for f in j["files"])


def test_export_raw_and_repeat(fake_db, run_cli, m):
    j = run_cli(["asset", "export", "model:411", "--format", "raw", "--json"]).json
    names = [Path(f).name for f in j["files"]]
    assert names == ["infernus.dff", "infernus.txd", "vehicle.txd"] and j["col"] == "embedded in infernus.dff"
    assert Path(j["files"][0]).parent == _work() / "cache" / "raw" / "vanilla"
    assert Path(j["files"][0]).read_bytes() == m.car_dff()             # RW payload, no sector padding
    mt = [Path(f).stat().st_mtime_ns for f in j["files"]]
    j2 = run_cli(["asset", "export", "model:411", "--format", "raw", "--json"]).json
    assert [Path(f).stat().st_mtime_ns for f in j2["files"]] == mt     # repeat rewrites nothing
    j3 = run_cli(["asset", "export", "model:17613", "--format", "raw", "--json"]).json
    assert [Path(f).name for f in j3["files"]] == ["lae2_roads89.dff", "lae2roadshub.txd", "lae2_4.col"]
    assert j3["col"] == "lae2_4.col#37"


def test_export_out_dir_inside_work(fake_db, run_cli):
    out = _work() / "out" / "custom"
    j = run_cli(["asset", "export", "model:17613", "--format", "obj", "--out", str(out), "--json"]).json
    assert j["dir"] == paths.jpath(out) and all(Path(f).parent == out for f in j["files"])


def test_thumbnails_all_in_process(fake_db, run_cli):
    r = run_cli(["model", "image", "--all", "--size", "32", "--views", "1", "--jobs", "1", "--json"])
    assert r.code == 0, r.out
    j = r.json
    assert j["models"] == 3 and j["rendered"] == 3                        # infernus, lae2_roads89, its LOD
    assert j["written"] == 2 and j["failed"] == 1                          # the LOD has no payload in the fake
    assert j["errors"][0][0] == "model:17858"
    man = json.loads(Path(j["manifest"]).read_text(encoding="utf-8"))
    assert set(man["thumbs"]) == {"model:411", "model:17613"} and man["size"] == 32
    j2 = run_cli(["model", "image", "--all", "--size", "32", "--views", "1", "--jobs", "1", "--json"]).json
    assert j2["rendered"] == 3 and j2["skipped"] == 2 and j2["failed"] == 1 and j2["written"] == 0
    from satk.model3d.batch import thumbnails

    j3 = thumbnails(32, 1, 1, limit=2)
    assert j3["rendered"] == 2 and j3["models"] == 3
    r = run_cli(["model", "image", "model:411", "--all", "--json"])
    assert r.code == 2


def test_nothing_written_outside_work(fake_db, run_cli, satk_home, tmp_path):
    game = tmp_path / "fakegame"
    before = {p: p.stat().st_mtime_ns for p in game.rglob("*") if p.is_file()}
    for argv in (["model", "image", "model:411", "--views", "1", "--size", "32"],
                 ["asset", "export", "model:411", "--format", "obj"],
                 ["asset", "export", "model:411", "--format", "raw"]):
        assert run_cli(argv + ["--json"]).code == 0
    after = {p: p.stat().st_mtime_ns for p in game.rglob("*") if p.is_file()}
    assert before == after
    outside = [p for p in tmp_path.rglob("*") if p.is_file() and not _under_work(str(p))
               and not str(p).startswith(str(game))]
    assert outside == []


class _FakeAriane:
    """Native SAAP asset.render writes RGBA PNGs at ``<prefix>_<az>.png``."""

    proto = "saap/1"

    def __init__(self, size_seen: list):
        self.warnings = ["ariane asset_preview: elevation and background are fixed"]
        self.size_seen = size_seen

    def require(self, cap, what=""):
        assert cap == "asset.render"

    def call(self, method, params):
        from satk.model3d.png import encode_png

        assert method == "asset.render" and Path(params["path_prefix"]).is_absolute() is True
        self.size_seen.append((params["model"], params["views"], params["size"], params["el"]))
        files = []
        n = params["size"]
        for az in params["views"]:
            px = b"".join(bytes((255, 0, 0, 255 if x < n // 2 else 0)) for _y in range(n) for x in range(n))
            path = f"{params['path_prefix']}_{int(az)}.png"
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(encode_png(n, n, px, 4))
            files.append(path)
        return {"files": files}

    def close(self):
        pass


def test_ariane_backend_adapter(fake_db, run_cli, monkeypatch):
    import satk.viewer.backends as VB

    seen: list = []
    monkeypatch.setattr(VB, "get_backend", lambda target, **kw: _FakeAriane(seen))
    j = run_cli(["model", "image", "model:17613", "--backend", "ariane", "--size", "16", "--json"]).json
    assert j["backend"] == "ariane" and j["cached"] is False and seen == [(17613, [45.0, 135.0, 225.0, 315.0], 16, 25.0)]
    assert j["file"].endswith("-ariane-4-16.png") and "ariane asset_preview" in " ".join(j["warn"])
    from PIL import Image

    with Image.open(j["file"]) as im:
        assert im.size == (32, 32)
        assert im.getpixel((2, 2)) == (255, 0, 0)                       # opaque half of view 1
        assert im.getpixel((12, 2)) == (178, 186, 196)                  # transparent half -> background
    assert run_cli(["model", "image", "model:17613", "--backend", "ariane", "--size", "16", "--json"]).json["cached"]


def test_blender_backend_adapter(fake_db, run_cli, monkeypatch, tmp_path):
    import sys
    import types

    import satk.blender as SB
    from satk.model3d.png import encode_png

    calls: list = []

    def run_job(cmd, args, *, profile="vanilla", **kw):
        calls.append((cmd, dict(args), profile))
        p = tmp_path / "job" / "model.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        n = 2 * args["size"] if args["views"] == 4 else args["size"]
        p.write_bytes(encode_png(n, n, bytes((0, 0, 255)) * (n * n), 3))
        return {"ok": True, "files": {"png": [str(p)]}, "stats": {"objects": 3}, "warnings": []}

    fake = types.ModuleType("satk.blender.runner")
    fake.run_job = run_job
    monkeypatch.setitem(sys.modules, "satk.blender.runner", fake)
    monkeypatch.setattr(SB, "runner", fake, raising=False)
    j = run_cli(["model", "image", "model:411", "--backend", "blender", "--size", "20", "--json"]).json
    assert j["backend"] == "blender" and calls[0][0] == "import_model"
    assert calls[0][1] == {"id": "model:411", "render": True, "views": 4, "size": 20, "save": False}
    assert png_size(Path(j["file"]).read_bytes()) == (40, 40) and j["stats"] == {"objects": 3}
    j = run_cli(["model", "image", "model:411", "--backend", "blender", "--views", "2", "--size", "20", "--json"]).json
    assert j["backend"] == "soft" and "UNSUPPORTED" in j["warn"][-1]
