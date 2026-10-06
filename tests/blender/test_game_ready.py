"""``satk blender game-ready`` (M2-08): contract, the satk side, and live runs in Blender 5.1.

The live tests (markers ``blender``, ``slow``) build their sources in the test (a textured cube
``.obj``, a high-poly sphere ``.obj``, a ``.blend`` with a procedural material) and write into a private
``SATK_PATHS_WORK``; they never read the game.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from satk.blender import contract as C
from satk.blender import gameready as G
from satk.core import config
from satk.core.errors import SatkError
from satk.core.registry import get_op


# --------------------------------------------------------------------------- contract (no Blender)


def test_args_defaults_and_name():
    a = C.normalize_args("game_ready", {"src": "D:/x/My Crate-01.OBJ"})
    assert a["name"] == "my_crate_01" and a["budget"] == C.GR_BUDGET == 1040
    assert (a["origin"], a["uv"], a["bake"], a["prelight"], a["col"]) == ("base", "auto", "auto", "bake", "hull")
    assert a["tex_size"] == 256 and a["lod"] == 0.25 and a["surface"] == 0 and a["objects"] is None
    a = C.normalize_args("game_ready", {"src": "s.blend", "objects": "Statue, Plinth", "name": "Statue"})
    assert a["objects"] == ["Statue", "Plinth"] and a["name"] == "statue"
    assert C.normalize_args("game_ready", {"src": "s.blend", "objects": ["Statue,Plinth"]})["objects"] == ["Statue", "Plinth"]
    # the open scene of the add-on: no file, objects required, name from the first object
    a = C.normalize_args("game_ready", {"objects": ["Big Rock.001"]})
    assert a["src"] is None and a["name"] == "big_rock_001"


@pytest.mark.parametrize("args", [
    {},                                                    # neither src nor objects
    {"src": "x.txt"},                                      # unsupported type
    {"src": "x.obj", "name": "a" * 22},                    # COL name holds 21 characters
    {"src": "x.obj", "name": "bad name"},
    {"src": "x.obj", "budget": 5},
    {"src": "x.obj", "tex_size": 300},
    {"src": "x.obj", "surface": 179},
    {"src": "x.obj", "lod": 1.0},
    {"src": "x.obj", "col": "sphere"},
    {"src": "x.obj", "height": 0},
    {"src": "x.obj", "bogus": 1},
])
def test_args_bad(args):
    with pytest.raises(C.ContractError) as e:
        C.normalize_args("game_ready", args)
    assert e.value.code == "BAD_PARAMS"


def test_names():
    assert C.model_name("Ünïcode Model!!") == "n_code_model"
    assert C.model_name("___") == "model" and len(C.model_name("x" * 40)) == 21
    assert C.lod_name("a" * 21) == "lod" + "a" * 20 and len(C.lod_name("a" * 21)) == 23


# --------------------------------------------------------------------------- satk side (no Blender)


def test_out_dir(satk_home):
    d = G.out_dir("crate")
    assert os.path.realpath(d) == os.path.realpath(satk_home / "work" / "out" / "blender" / "crate")
    assert not d.exists()
    with pytest.raises(SatkError) as e:
        G.out_dir("crate", satk_home / "elsewhere")
    assert e.value.code == "PROTECTED_PATH"


def test_ide_text():
    t = G.ide_text("crate", "lodcrate", 150.0)
    assert "objs\n-1, crate, crate, 150, 0\n-1, lodcrate, crate, 800, 0\nend\n" in t
    assert "lodcrate" not in G.ide_text("crate", None, 99.5) and "-1, crate, crate, 99.5, 0" in G.ide_text("crate", None, 99.5)


def test_pack_txd_fallback(satk_home, tmp_path, monkeypatch):
    monkeypatch.setattr(G, "_texmod_packer", lambda: None)
    out = satk_home / "work" / "out" / "blender" / "crate"
    (out / "tex").mkdir(parents=True)
    png = out / "tex" / "crate.png"
    png.write_bytes(b"png")
    r = G.pack_txd(out, "crate", [{"name": "crate", "png": str(png), "w": 64, "h": 64, "alpha": False}])
    assert r["png"] == [str(png).replace("\\", "/")] and r["warn"][0].startswith("TXD_PENDING:")
    note = (out / G.TODO_TXD).read_text(encoding="utf-8")
    assert "crate.png  64x64" in note and "satk texture pack" in note
    assert G.pack_txd(out, "crate", []) == {}


def test_pack_txd_with_a_packer(satk_home, monkeypatch):
    """With a TXD writer (satk texmod, M2-03) the TXD replaces the TODO note of an earlier run."""
    calls = []

    def fake_packer():
        def pack(textures, txd_path):
            calls.append([t["name"] for t in textures])
            txd_path.write_bytes(b"TXD")
            return {"formats": {"crate": "DXT1 64x64 mips 7"}, "warn": []}
        return pack

    out = satk_home / "work" / "out" / "blender" / "crate"
    out.mkdir(parents=True)
    (out / G.TODO_TXD).write_text("old", encoding="utf-8")
    monkeypatch.setattr(G, "_texmod_packer", fake_packer)
    r = G.pack_txd(out, "crate", [{"name": "crate", "png": str(out / "crate.png"), "w": 64, "h": 64}])
    assert calls == [["crate"]] and r["txd"].endswith("/crate/crate.txd") and r["warn"] == []
    assert r["txd_formats"] == {"crate": "DXT1 64x64 mips 7"} and not (out / G.TODO_TXD).exists()


def test_verify_reports_missing_outputs():
    rows, st = G.verify({"name": "x", "lod": "lodx", "args": {"budget": 100, "col": "hull"}, "files": {},
                         "textures": [{"name": "t", "w": 96, "h": 64}]})
    status = {r[0]: r[1] for r in rows}
    assert status["dff"] == "fail" and status["lod"] == "fail" and status["col"] == "fail"
    assert status["textures"] == "fail" and status["names"] == "ok" and st == {}


def test_op_registered_and_cli(run_cli):
    spec = get_op("blender.game_ready")
    assert spec.mcp_name is None and spec.long_running and len(spec.summary) <= 300
    r = run_cli(["blender", "game-ready", "-h"])
    assert r.code == 0 and "--budget" in r.out and "--col" in r.out and "--prelight" in r.out


def test_cli_errors_before_blender(run_cli, satk_home, tmp_path):
    r = run_cli(["blender", "game-ready", str(tmp_path / "nope.obj")])
    assert r.json["error"]["code"] == "NOT_FOUND"
    bad = tmp_path / "x.txt"
    bad.write_text("x", encoding="utf-8")
    r = run_cli(["blender", "game-ready", str(bad)])
    assert r.json["error"]["code"] == "BAD_PARAMS"
    src = tmp_path / "x.obj"
    src.write_text("v 0 0 0\n", encoding="utf-8")
    r = run_cli(["blender", "game-ready", str(src), "--out", str(tmp_path / "outside")])
    assert r.json["error"]["code"] == "PROTECTED_PATH" and not (tmp_path / "outside").exists()


# --------------------------------------------------------------------------- live (Blender 5.1)


@pytest.fixture(scope="module")
def live_work(tmp_path_factory):
    work = tmp_path_factory.mktemp("grwork")
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    yield work
    if old is None:
        os.environ.pop("SATK_PATHS_WORK", None)
    else:
        os.environ["SATK_PATHS_WORK"] = old
    config.reset()


def _game_ready(**args) -> dict:
    return get_op("blender.game_ready").call(args)


def _checks(r: dict) -> dict[str, str]:
    return {row[0]: row[1] for row in r["checks"]["rows"]}


def _all_ok(rows: list[list], packed: bool) -> bool:
    """Every check ``ok``; without a TXD writer (M2-03 not merged) the ``txd`` check is a ``warn``."""
    return all(st == "ok" or (c == "txd" and st == "warn" and not packed) for c, st, _d in rows)


@pytest.mark.blender
@pytest.mark.slow
def test_textured_cube_to_dff_col(live_work, tmp_path, gr_sources, run_cli):
    """Acceptance: a textured cube -> DFF + COL in work/out/blender/<name>, both read by formats dump."""
    src = gr_sources["cube"](tmp_path, "crate")
    r = _game_ready(src=str(src), col="box")
    out = Path(r["out"])
    assert out == live_work / "out" / "blender" / "crate"
    assert _all_ok(r["checks"]["rows"], "txd" in r["files"]), r["checks"]
    dump = run_cli(["formats", "dump", str(out / "crate.dff"), "--level", "full"]).json
    assert dump["ok"] and dump["kind"] == "dff" and dump["tris"] == 12 and dump["rw_version"] == "0x36003"
    assert {"prelit", "night"} <= set(dump["flags"]) and dump["textures"] == ["crate"]
    assert dump["rows"][0][4] == "ffffffff"  # white material colour under the texture
    assert dump["bbox"] == [-1.0, -1.0, 0.0, 1.0, 1.0, 2.0]  # origin at the base centre
    col = run_cli(["formats", "dump", str(out / "crate.col"), "--level", "full"]).json
    assert col["ok"] and col["by_version"] == {"COL3": 1} and col["rows"][0][1] == "crate" and col["rows"][0][4] == 1
    png = out / "tex" / "crate.png"
    assert png.is_file() and r["files"]["png"] == [png.as_posix()]
    assert (png.read_bytes()[16:24] == (64).to_bytes(4, "big") * 2)  # 96 px source -> 64 (power of two)
    man = json.loads((out / "gameready.json").read_text(encoding="utf-8"))
    assert man["name"] == "crate" and man["checks"] == r["checks"]["rows"]
    assert "-1, crate, crate, 150, 0" in (out / "crate.ide").read_text(encoding="utf-8")
    if "txd" in r["files"]:  # satk texmod (M2-03) packed it
        assert Path(r["files"]["txd"]).is_file()
    else:
        assert (out / G.TODO_TXD).is_file() and any(w.startswith("TXD_PENDING") for w in r["warn"])
    assert any(w.startswith("LOD_SKIPPED") for w in r["warn"])  # 12 triangles need no LOD
    assert r["stats"]["prelight"] == "bake" and 0.1 < r["stats"]["day_mean"] < 0.6


@pytest.mark.blender
@pytest.mark.slow
def test_sphere_budget_lod_hull(live_work, tmp_path, gr_sources, run_cli):
    src = gr_sources["sphere"](tmp_path, "ball")  # 4 992 triangles, no UVs, a plain colour
    r = _game_ready(src=str(src), budget=500, prelight="simple", lod=0.3, surface=51, draw=200)
    st = r["stats"]
    assert st["src_tris"] == 4992 and 400 <= st["tris"] <= 500 and st["uv"] == "smart" and not st["baked"]
    assert r["lod"] == "lodball" and st["lod_tris"] <= 0.35 * st["tris"]
    assert st["col"] == "hull" and 4 <= st["col_faces"] <= 256
    assert _all_ok(r["checks"]["rows"], "txd" in r["files"]), r["checks"]
    out = Path(r["out"])
    lod = run_cli(["formats", "dump", str(out / "lodball.dff")]).json
    assert lod["ok"] and lod["tris"] == st["lod_tris"] and "prelit" in lod["flags"]
    dump = run_cli(["formats", "dump", str(out / "ball.dff"), "--level", "full"]).json
    assert not dump.get("textures") and dump["rows"][0][4] != "ffffffff"  # the blue material colour, no texture
    ide = (out / "ball.ide").read_text(encoding="utf-8")
    assert "-1, ball, ball, 200, 0" in ide and "-1, lodball, ball, 800, 0" in ide
    from satk.formats.col import iter_col

    c = next(iter_col((out / "ball.col").read_bytes()))
    assert c.name == "ball" and set(c.surfaces) == {51}


_MAKE_BLEND = """
import sys
sys.dont_write_bytecode = True
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_monkey_add(size=2, location=(5, 3, 1))
m = bpy.context.object
m.name = "Statue"
mod = m.modifiers.new("sub", "SUBSURF")
mod.levels = mod.render_levels = 3
mat = bpy.data.materials.new("noise_mat")
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
noise = nt.nodes.new("ShaderNodeTexNoise")
ramp = nt.nodes.new("ShaderNodeValToRGB")
ramp.color_ramp.elements[0].color = (0.25, 0.12, 0.05, 1)
ramp.color_ramp.elements[1].color = (0.8, 0.7, 0.5, 1)
nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
m.data.materials.append(mat)
bpy.ops.mesh.primitive_cylinder_add(radius=0.6, depth=1.0, location=(5, 3, -0.5))
bpy.context.object.name = "Plinth"
bpy.ops.mesh.primitive_plane_add(size=1, location=(20, 0, 0))
bpy.context.object.name = "Helper"
bpy.context.preferences.filepaths.file_preview_type = "NONE"  # no thumbnail in the user's .thumbnails
bpy.ops.wm.save_as_mainfile(filepath=OUT)
"""


@pytest.mark.blender
@pytest.mark.slow
def test_blend_bake_render(live_work, tmp_path):
    """A .blend with a subdivided procedural mesh: evaluated modifiers, objects, bake, preview; the
    source .blend is not changed."""
    from satk.blender import runner

    blend = tmp_path / "statue.blend"
    script = tmp_path / "make_blend.py"
    script.write_text(f"OUT = {str(blend)!r}\n" + _MAKE_BLEND, encoding="utf-8")
    code, _ = runner.run_blender(["-b", "--factory-startup", "--python-exit-code", "1", "--python", str(script)],
                                 log=tmp_path / "make.log", timeout=180, env=runner.blender_env())
    assert code == 0 and blend.is_file()
    before = hashlib.sha256(blend.read_bytes()).hexdigest()
    r = _game_ready(src=str(blend), objects=["Statue,Plinth"], name="statue", budget=600, height=3.0,
                    render=True)
    st = r["stats"]
    assert st["source_objects"] == 2 and st["src_tris"] > 30000 and st["tris"] <= 600
    assert st["baked"] and st["uv"] == "smart" and st["textures"] == 1 and st["size"][2] == pytest.approx(3.0)
    assert _all_ok(r["checks"]["rows"], "txd" in r["files"]), r["checks"]
    assert Path(r["files"]["preview"]).is_file() and Path(r["files"]["blend"]).is_file()
    tex = Path(r["out"]) / "tex" / "statue.png"
    assert tex.is_file() and tex.stat().st_size > 2000  # a real baked texture, not a flat colour
    assert hashlib.sha256(blend.read_bytes()).hexdigest() == before
    with pytest.raises(SatkError) as e:
        _game_ready(src=str(blend), objects=["Statu"])
    assert e.value.code == "NOT_FOUND" and "Statue" in e.value.data["did_you_mean"]


_ADDON = """
import os, sys, json
sys.dont_write_bytecode = True
import bpy
sys.path.insert(0, ADDON_PARENT)
import satk_blender
satk_blender.register()
from satk_blender import common
d = {}
# a textured cube in the live scene (the satk sources and DragonFF come from the add-on defaults)
bpy.ops.mesh.primitive_cube_add(size=2, location=(10, 0, 1))
cube = bpy.context.object
cube.name = "Crate In Scene"
img = bpy.data.images.new("crate_px", 32, 32, alpha=True)
img.pixels.foreach_set([0.6, 0.4, 0.2, 1.0] * 32 * 32)
mat = bpy.data.materials.new("crate_mat")
nt = mat.node_tree
tex = nt.nodes.new("ShaderNodeTexImage")
tex.image = img
nt.links.new(tex.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
cube.data.materials.append(mat)
verts0 = len(cube.data.vertices)
p = bpy.context.scene.satk
p.gr_name, p.gr_col, p.gr_prelight = "addon_gr", "box", "simple"
d["op"] = list(bpy.ops.satk.make_game_ready())
d["again"] = list(bpy.ops.satk.make_game_ready())  # a re-run replaces the first result
d["results"] = sorted(o.name for o in bpy.data.objects if o.get("satk_gr_name") == "addon_gr")
d["source_kept"] = (len(cube.data.vertices) == verts0 and cube.data.materials[0] == mat
                    and not cube.hide_render and len(mat.node_tree.nodes) == 3)
# DragonFF's TXD import decodes through satk.formats (DXT3 fix): a TXD written by DragonFF comes back
te = common.dff_module("ops.txd_exporter")
ti = common.dff_module("ops.txd_importer")
te.txd_exporter.version = 0x36003
txd = os.path.join(OUT_DIR, "check.txd")
te.txd_exporter.export_textures([cube], txd)
r = ti.import_txd({"file_name": txd, "skip_mipmaps": True, "pack": False})
d["patched"] = bool(getattr(ti.import_txd, "_satk", False))
d["txd_names"] = sorted(r.images)
im = r.images[sorted(r.images)[0]][0]
px = list(im.pixels[:4])
d["px"] = [round(v, 2) for v in px]
d["img_name"] = im.name
json.dump(d, open(os.path.join(OUT_DIR, "addon.json"), "w"))
"""


@pytest.mark.blender
@pytest.mark.slow
def test_addon_operator_and_txd_patch(live_work, tmp_path):
    """The N-panel operator without SATK_SRC/SATK_DRAGONFF (defaults found from the checkout and the
    satk work dir), the selection unchanged; DragonFF's TXD import routed through satk.formats."""
    from satk.blender import runner

    runner.ensure_dragonff()
    script = tmp_path / "addon_gr.py"
    script.write_text(f"ADDON_PARENT = {str(runner.addon_dir().parent)!r}\nOUT_DIR = {str(tmp_path)!r}\n" + _ADDON,
                      encoding="utf-8")
    env = runner.blender_env()
    env.pop("SATK_SRC", None)
    env.pop("SATK_DRAGONFF", None)
    log = tmp_path / "addon_gr.log"
    code, _ = runner.run_blender(["-b", "--factory-startup", "--python-exit-code", "1", "--python", str(script)],
                                 log=log, timeout=300, env=env)
    assert code == 0, log.read_text(encoding="utf-8", errors="replace")[-2500:]
    d = json.loads((tmp_path / "addon.json").read_text(encoding="utf-8"))
    assert d["op"] == ["FINISHED"] and d["again"] == ["FINISHED"]
    assert d["results"] == ["addon_gr", "addon_gr_colbox"] and d["source_kept"] is True
    out = live_work / "out" / "blender" / "addon_gr"
    assert {"addon_gr.dff", "addon_gr.col", "addon_gr.ide", "gameready.json"} <= {p.name for p in out.iterdir()}
    man = json.loads((out / "gameready.json").read_text(encoding="utf-8"))
    assert _all_ok(man["checks"], "txd" in man["files"]), man["checks"]
    assert d["patched"] and d["txd_names"] == ["crate_px"] and d["img_name"] == "check.txd/crate_px/0"
    assert d["px"] == pytest.approx([0.6, 0.4, 0.2, 1.0], abs=0.01)


def test_ide_text_flags_and_class_param():
    t = G.ide_text("bin", None, 100.0, flags=132)
    assert "-1, bin, bin, 100, 132" in t
    from satk.core.registry import get_op

    spec = get_op("blender.game_ready")
    assert spec.param("asset_class").choices == ("prop", "building", "terrain", "vegetation", "interior_prop",
                                                 "interior_shell", "pickup", "overlay")
    assert spec.param("draw").default is None and spec.param("col").default is None
    assert G.BIG_BUILDING_DRAW == 300
