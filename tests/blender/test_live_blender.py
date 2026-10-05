"""End-to-end with the real Blender 5.1 and gta-sa-clean (markers blender, game, slow).

The work directory is redirected to a temp dir (``SATK_PATHS_WORK``): DragonFF is extracted there with
``git archive`` from the read-only clone, the Blender profile and jobs live there too. Only reads the game.
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest

from satk.core import config
from satk.core.registry import get_op

pytestmark = [pytest.mark.blender, pytest.mark.game, pytest.mark.slow]


@pytest.fixture(scope="module")
def live_work(tmp_path_factory):
    work = tmp_path_factory.mktemp("work")
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    from satk.blender import gamedata

    gamedata.clear_cache()
    yield work
    if old is None:
        os.environ.pop("SATK_PATHS_WORK", None)
    else:
        os.environ["SATK_PATHS_WORK"] = old
    config.reset()


def _call(name: str, **args) -> dict:
    return get_op(name).call(args)


def test_doctor(live_work):
    r = _call("blender.doctor")
    assert r["blender"].startswith("5.") and r["python"].startswith("3.13.")
    assert r["dragonff"]["commit"] == "b3bd7aa" and r["dragonff"]["registered"] is True
    assert r["dragonff"]["path"].lower() == (str(live_work).replace("\\", "/") + "/blender/dragonff").lower()
    assert r["profile"]["isolated"] is True
    assert r["profile"]["config"].lower().startswith(str(live_work).replace("\\", "/").lower())


def _inspect(live_work, blend: str, body: str) -> dict:
    """Run ``body`` (Python, sets ``d``) in a fresh Blender on ``blend``; returns ``d``."""
    from satk.blender import runner

    name = f"inspect_{abs(hash((blend, body))) % 10**8}"
    script, out = live_work / f"{name}.py", live_work / f"{name}.json"
    script.write_text("import sys, json\nsys.dont_write_bytecode = True\nimport bpy\nd = {}\n" + textwrap.dedent(body)
                      + f"\njson.dump(d, open({str(out)!r}, 'w'))\n", encoding="utf-8")
    log = live_work / f"{name}.log"
    code, _ = runner.run_blender(["-b", blend, "--factory-startup", "--python-exit-code", "1", "--python", str(script)],
                                 log=log, timeout=180, env=runner.blender_env())
    assert code == 0, log.read_text(encoding="utf-8", errors="replace")[-2000:]
    return json.loads(out.read_text())


def test_import_vehicle_render(live_work):
    r = _call("blender.import_model", id="model:411", render=True)
    st = r["stats"]
    # txd_images: infernus.txd (3) + vehicle.txd (19) loaded; images: the 13 the materials use = saved in the .blend
    assert st["meshes"] >= 17 and st["txd_images"] >= 22 and 0 < st["images"] < st["txd_images"]
    assert st["missing_tex"] == 0 and st["unused_images"] == st["txd_images"] - st["images"]
    assert st["hidden_dam"] is True and st["wheels"] == 4 and st["paint_slots"] > 0
    assert st["png_std"] > 10 and Path(r["files"]["png"][0]).is_file()
    assert st["frame_margin"] > 0  # every vertex projects inside the image (the bumper was cut off before)
    assert st["process_s"] <= 40
    d = _inspect(live_work, r["files"]["blend"],
                 "d['images'] = len([i for i in bpy.data.images if i.name not in ('Render Result', 'Viewer Node')])")
    assert d["images"] == st["images"]  # the statistics describe the saved file


def test_import_ped_bones(live_work):
    r = _call("blender.import_model", id="bfori")
    assert r["stats"]["bones"] == 32 and r["stats"]["armatures"] == 1


@pytest.fixture(scope="module")
def area(live_work):
    # includes lamppost3, fire_hydrant and DYN_* boxes, whose DFFs carry breakable meshes
    return _call("blender.import_area", center=[2489.3, -1668.5], r=30.0, col=True, lod="all", area="any")


def test_area_import(area, live_work):
    st = area["stats"]
    assert st["instances"] > 20 and st["meshes"] <= st["models"]
    assert st["packed"] == st["images"] and st["col_instances"] > 0 and st["breakables"] > 0
    assert Path(area["files"]["blend"]).is_file()
    d = _inspect(live_work, area["files"]["blend"], """
        imgs = [i for i in bpy.data.images if i.name not in ('Render Result', 'Viewer Node')]
        d['images'], d['packed'] = len(imgs), sum(1 for i in imgs if i.packed_file is not None)
        d['root'] = [c.name for c in bpy.context.scene.collection.children]
        brk = [o for o in bpy.data.objects if o.name.split('.')[0].endswith('_breakable')]  # no DragonFF here
        models = bpy.data.collections['SATK_models']
        d['brk'] = len(brk)
        d['brk_ok'] = all(o.hide_render and o.name in models.all_objects and o.get('satk_breakable') and o.get('satk_sid')
                          for o in brk)
        vis = [o for o in bpy.context.scene.objects if o.type == 'MESH' and o.visible_get() and not o.hide_render]
        d['vis_untagged'] = [o.name for o in vis if not o.get('satk_sid')]
        d['vis_near_origin'] = [o.name for o in vis if o.matrix_world.translation.length < 1000]
        road = bpy.data.objects['Lae2_roads89@lae2_stream0#4']
        d['road_iflags'] = road.get('satk_iflags')
        d['road_ide'] = dict(next(c for c in bpy.data.collections if c.get('satk_sid') == 'model:17613')['satk_ide'])
    """)
    assert d["images"] == st["images"] == d["packed"]  # statistics = saved file, every image packed
    assert "Breakable" not in d["root"] and d["brk"] == st["breakables"] and d["brk_ok"]
    assert d["vis_untagged"] == [] and d["vis_near_origin"] == []  # nothing stray at the world origin
    assert d["road_iflags"] == 0 and d["road_ide"] == {"sec": "objs", "draw": 150.0, "flags": 1}


def test_area_reopen_render_and_ids(area):
    blend = area["files"]["blend"]
    r = _call("blender.render", blend=blend, engine="workbench", size="480x270")
    assert r["stats"]["png_mean"] > 0.05  # report 11 regression: unpacked images render black after reopening
    r = _call("blender.render", blend=blend, pos=[2489.3, -1700.0, 30.0], look=[2489.3, -1668.5, 12.3],
              objindex=True, size="480x270", limit=500)
    rows = r["visible"]["rows"]
    assert rows and all(row[0].startswith("inst:") for row in rows)
    from satk.blender import gamedata

    g = gamedata.load("vanilla")
    known = {x.sid for x in g.insts(center=(2489.3, -1668.5), r=30.0, area=None, lod="all", match="aabb")}
    assert {row[0] for row in rows} <= known
    assert "inst:lae2_stream0#4" in {row[0] for row in rows}


def test_area_export(area, live_work):
    from satk.formats.col import iter_col
    from satk.formats.dff import scan_dff

    r = _call("blender.export", blend=area["files"]["blend"], objects=["lae2_roads89"], target="mta-resource")
    out = Path(r["out"])
    assert out.is_relative_to(live_work)
    for fn in ("lae2_roads89.dff", "lae2_roads89.txd", "lae2_roads89.col", "meta.xml", "client.lua",
               "lae2_roads89.ide", "lae2_roads89.ipl"):
        assert (out / fn).is_file(), fn
    info = scan_dff((out / "lae2_roads89.dff").read_bytes())
    assert info.rw_version == 0x36003 and info.tris > 200 and all(m.texture for m in info.materials)
    cols = list(iter_col((out / "lae2_roads89.col").read_bytes()))
    assert [c.name for c in cols] == ["lae2_roads89"] and cols[0].faces == 100  # the game COL, unchanged
    assert not any(w.startswith(("COL_FROM_MESH", "IDE_DEFAULTS")) for w in r.get("warn", []))
    # the original definition (lae2.ide: draw 150, flags 1), not 299/0
    assert "17613, lae2_roads89, lae2_roads89, 150, 1" in (out / "lae2_roads89.ide").read_text(encoding="utf-8")


def test_area_export_keeps_instance_flags(area, live_work):
    """telgrphpole02 placements have interior 512 (area 0, iflags 2) in lae2_stream2.ipl."""
    r = get_op("blender.export").call({"blend": area["files"]["blend"], "objects": ["telgrphpole02"], "name": "pole"})
    out = Path(r["out"])
    assert "1308, telgrphpole02, telgrphpole02, 100, 32896" in (out / "pole.ide").read_text(encoding="utf-8")
    rows = [ln.split(", ") for ln in (out / "pole.ipl").read_text(encoding="utf-8").splitlines() if ln.startswith("1308")]
    assert rows and all(f[2] == "512" for f in rows)


def test_export_nothing_found_leaves_no_folder(area, live_work):
    from satk.core.errors import SatkError

    with pytest.raises(SatkError) as e:
        _call("blender.export", blend=area["files"]["blend"], objects=["lae2_roadz89"])
    assert e.value.code == "NOT_FOUND" and "lae2_roads89" in e.value.data["did_you_mean"]
    assert not (live_work / "out" / "exports" / "lae2_roadz89").exists()


#: Blender-side part of the add-on test: operators of the N-panel in a factory-startup Blender.
_ADDON_SCRIPT = """
import os, sys, json
# Blender ignores PYTHONDONTWRITEBYTECODE (ignore_environment): honour it as Python would, before any import
sys.dont_write_bytecode = bool(os.environ.get("PYTHONDONTWRITEBYTECODE"))
import bpy
sys.path.insert(0, ADDON_PARENT)
sys.path.insert(0, SATK_SRC)
import satk_blender
from satk_blender import common
satk_blender.register()
d = {"panel": hasattr(bpy.types, "SATK_PT_panel")}
p = bpy.context.scene.satk
d["model"] = list(bpy.ops.satk.import_model(sid="model:411"))
d["objects"] = len([o for o in bpy.data.objects if o.get("satk_sid") == "model:411"])
# Import Area by zone: GAN1 (the test work dir has no index -> data/info.zon)
p.center_mode, p.zone, p.size, p.lod = "zone", "GAN1", 40.0, "hd"
d["zone"] = list(bpy.ops.satk.import_area())
insts = [o for o in bpy.data.objects if str(o.get("satk_sid", "")).startswith("inst:")]
d["zone_insts"] = len(insts)
xs, ys = sorted(o["satk_pos"][0] for o in insts), sorted(o["satk_pos"][1] for o in insts)
d["zone_median"] = [xs[len(xs) // 2], ys[len(ys) // 2]] if insts else None
vl = bpy.context.view_layer

def lcs(lc, acc):
    for ch in lc.children:
        acc[ch.name] = [ch.exclude, ch.hide_viewport]
        lcs(ch, acc)
    return acc

def snap():
    return {"lcs": lcs(vl.layer_collection, {}),
            "sel": sorted(o.name for o in vl.objects if o.select_get()),
            "active": vl.objects.active.name if vl.objects.active else None,
            "hidden": sorted(o.name for o in vl.objects if o.hide_get()),
            "hide_viewport": sorted(o.name for o in bpy.data.objects if o.hide_viewport),
            "surface": sorted([m.name, lk.from_node.name] for m in bpy.data.materials if m.node_tree
                              for n in m.node_tree.nodes if n.type == "OUTPUT_MATERIAL"
                              for lk in n.inputs["Surface"].links),
            "nodes": sum(len(m.node_tree.nodes) for m in bpy.data.materials if m.node_tree)}

# Export the first placement from this live scene: the scene must be the same afterwards
for o in vl.objects:
    o.select_set(False)
target = sorted(insts, key=lambda o: o.name)[0]
target.select_set(True)
vl.objects.active = target
before = snap()
d["models_excluded_before"] = before["lcs"]["SATK_models"][0]
p.export_name = "addon_zone"
d["export"] = list(bpy.ops.satk.export())
after = snap()
d["restored"] = before == after
d["diff"] = {k: [str(before[k])[:200], str(after[k])[:200]] for k in before if before[k] != after[k]}
json.dump(d, open(OUT, "w"))
"""


def test_addon_registers_and_imports(live_work):
    """The N-panel add-on: Import Model, Import Area by zone, Export from the live scene (which is put
    back as it was), and no bytecode written into the checkout or the DragonFF copy."""
    import time

    from satk.blender import runner

    runner.ensure_dragonff()
    script = live_work / "addon_smoke.py"
    out = live_work / "addon_smoke.json"
    head = [f"ADDON_PARENT = {str(runner.addon_dir().parent)!r}", f"SATK_SRC = {str(runner.satk_src())!r}",
            f"OUT = {str(out)!r}"]
    script.write_text("\n".join(head) + _ADDON_SCRIPT, encoding="utf-8")
    env = runner.blender_env()
    env["SATK_DRAGONFF"] = str(runner.dragonff_root())
    env["SATK_SRC"] = str(runner.satk_src())  # this checkout, not the main one
    log = live_work / "addon_smoke.log"
    t0 = time.time() - 1
    code, _ = runner.run_blender(["-b", "--factory-startup", "--python-exit-code", "1", "--python", str(script)],
                                 log=log, timeout=300, env=env)
    assert code == 0, log.read_text(encoding="utf-8", errors="replace")[-2000:]
    d = json.loads(out.read_text())
    assert d["model"] == ["FINISHED"] and d["objects"] > 20 and d["panel"] is True
    assert d["zone"] == ["FINISHED"] and d["zone_insts"] > 5  # was RuntimeError KeyError 'pos'
    assert d["zone_median"] == pytest.approx([2427.69, -1675.43], abs=25)  # around the centre of GAN1 (box 40)
    assert d["export"] == ["FINISHED"] and d["models_excluded_before"] is True
    assert d["restored"], d["diff"]  # exclusion, visibility, selection, shading links, nodes
    exp = live_work / "out" / "exports" / "addon_zone"
    assert {"export.json", "meta.xml", "client.lua", "addon_zone.ide", "addon_zone.ipl"} <= {p.name for p in exp.iterdir()}
    # Blender-side bytecode (cpython-313) is never written: not into this checkout, not into DragonFF
    fresh = [p for root in (runner.addon_dir(), runner.satk_src() / "satk", runner.dragonff_dir())
             for p in root.rglob("*.cpython-313.pyc") if p.stat().st_mtime >= t0]
    assert fresh == []
