"""Real Blender/Cycles conversion of synthetic inputs; game reads supply class measurements and peer previews."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from satk.convert.pipeline import run
from satk.core import paths
from satk.core.errors import SatkError
from satk.core.registry import get_op

pytestmark = [pytest.mark.blender, pytest.mark.game, pytest.mark.slow]


SOURCE = """
import math
import numpy as np
from pathlib import Path
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
if args['kind'] == 'prop':
    bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=48, radius=1)
    obj = bpy.context.object
    obj.scale = (0.6, 0.6, 0.9)
    obj.name = 'Synthetic high resolution prop'
else:
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.name = 'Synthetic car body'
    obj.scale = (2.2, 5.8, 1.15)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bevel = obj.modifiers.new('Rounded body panels', 'BEVEL')
    bevel.width = 0.38
    bevel.segments = 1
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    pieces = [obj]
    bpy.ops.mesh.primitive_cube_add(size=1)
    cabin = bpy.context.object
    cabin.name = 'Cabin panels'
    cabin.scale = (1.8, 2.5, 0.7)
    cabin.location = (0, -0.3, 0.85)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bevel = cabin.modifiers.new('Cabin chamfer', 'BEVEL')
    bevel.width = 0.3
    bevel.segments = 1
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    pieces.append(cabin)
    # Author coarse panels and concentrate source detail at the lamps and grille.
    # These are real surfaces, not subdivision added merely to inflate a fixture's triangle count.
    for end in [-1, 1]:
        for x in [-0.78, -0.5, 0.5, 0.78]:
            bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1)
            lamp = bpy.context.object
            lamp.scale = (0.12, 0.08, 0.12)
            lamp.location = (x, end * 2.92, 0.16)
            pieces.append(lamp)
        for i in range(24):
            bpy.ops.mesh.primitive_cube_add(size=1, location=(0, end * 2.94, -0.18 + i * 0.015))
            bar = bpy.context.object
            bar.scale = (0.65, 0.045, 0.009)
            pieces.append(bar)
    for item in bpy.context.selected_objects:
        item.select_set(False)
    for item in pieces:
        item.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.join()
size = 256
y, x = np.mgrid[:size, :size]
rng = np.random.default_rng(163)
grain = rng.normal(0, 0.012, (size, size))
rgb = np.zeros((size, size, 4), dtype=np.float32)
base = 0.26 + grain + 0.08 * np.sin(x / 17) * np.cos(y / 29)
rgb[..., 0] = base * 1.12
rgb[..., 1] = base * 0.94
rgb[..., 2] = base * 0.83
rgb[..., 3] = 1
img = bpy.data.images.new('synthetic_source', size, size, alpha=False)
img.pixels.foreach_set(rgb.ravel())
img.pack()
mat = bpy.data.materials.new('prop_paint' if args['kind'] == 'prop' else 'body_paint')
mat.use_nodes = True
node = mat.node_tree.nodes.new('ShaderNodeTexImage')
node.image = img
bsdf = mat.node_tree.nodes.get('Principled BSDF')
mat.node_tree.links.new(node.outputs['Color'], bsdf.inputs['Base Color'])
obj.data.materials.append(mat)
for p in obj.data.polygons:
    p.material_index = 0
    p.use_smooth = True
for item in bpy.context.selected_objects:
    item.select_set(False)
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
result['tris'] = sum(len(p.vertices) - 2 for p in obj.data.polygons)
bpy.ops.export_scene.gltf(filepath=args['out'], export_format='GLB', use_selection=True)
"""


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    from satk.core import config
    from satk.docs.cleanup import remove_tree
    from satk.index import api as index_api
    from satk.studio import api

    work = tmp_path_factory.mktemp("convert-live")
    paths.ensure_writable(work)
    previous = paths.cfg().paths.work
    index = index_api.IndexDB(path=index_api.index_path())
    if (previous / "style").is_dir():
        (work / "style").mkdir()
        for cached in (previous / "style").glob("*.json"):
            shutil.copyfile(cached, work / "style" / cached.name)
    old_work = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    result = {}
    try:
        with index_api.override_index(index):
            for kind in ("prop", "vehicle"):
                path = work / f"source_{kind}.glb"
                api.cold("author.call", {"method": "python", "params": {"code": SOURCE,
                         "args": {"kind": kind, "out": str(path)}}, "stats": "none"}, timeout=120)
                assert path.is_file()
                result[kind] = path
            yield result
    finally:
        api.close_all()
        index.close()
        if old_work is None:
            os.environ.pop("SATK_PATHS_WORK", None)
        else:
            os.environ["SATK_PATHS_WORK"] = old_work
        config.reset()
        index_api.clear_cache()
        # This fixture owns only its private temporary work tree; durable acceptance files live elsewhere.
        test_work = work.resolve()
        assert test_work.is_relative_to(tmp_path_factory.getbasetemp().resolve())
        assert not remove_tree(test_work), f"conversion test work was not removed: {test_work}"


def test_convert_high_poly_gltf_prop(sources):
    source = sources["prop"]
    result = run(str(source), kind="prop", dims=[1.2, 1.2, 1.8], out=str(source.parent / "prop"), name="cvprop")
    stages = json.loads(Path(result["stages"]).read_text(encoding="utf-8"))
    assert stages["import"]["tris"] > 8000
    # no triangle target: the count is a plain number under the engine safety cap
    assert isinstance(result["tris"], int) and result["tris"] <= result["limit"]["max_tris"], result["limit"]
    assert "budget" not in result
    assert result["check"].get("counts", {}).get("error", 0) == 0, result["check"]
    assert all(t["verdict"] == "in" for t in result["textures"]), result["textures"]
    assert stages["bake"]["engine"] == "CYCLES"
    reduced = stages["reduce"]["roles"]["prop"]
    assert reduced["surface_error"] < 0.04, reduced
    assert reduced["preserved_samples"] > 8
    assert [result["metrics"][f"dims.{k}"] for k in "LWH"] == pytest.approx([1.2, 1.2, 1.8], abs=0.01)
    assert result["metrics"]["uv.zero_area_share"] < 0.02
    lighting = next(iter(stages["assemble"]["prelight"].values()))
    assert 0 < lighting["night_mean"] < lighting["day_mean"] < 0.8
    assert Path(result["package"]["files"]["col"]).is_file()
    assert Path(result["preview"]["files"]["sheet"]).stat().st_size > 1000
    assert Path(result["mta"]["meta"]).is_file()
    again = run(str(source), kind="prop", dims=[1.2, 1.2, 1.8], out=str(source.parent / "prop"), name="cvprop")
    assert again["cached"] is True


def test_convert_procedural_car_body(sources):
    source = sources["vehicle"]
    result = run(str(source), kind="vehicle", out=str(source.parent / "vehicle"), name="cvcar")
    stages = json.loads(Path(result["stages"]).read_text(encoding="utf-8"))
    assert stages["import"]["tris"] > 8000
    assert result["tris"] <= result["limit"]["max_tris"], result["limit"]
    assert result["check"].get("counts", {}).get("error", 0) == 0, result["check"]
    assert all(t["verdict"] == "in" for t in result["textures"]), result["textures"]
    # shading numbers are the class reference (info), never a warning
    assert set(result["shading"]) == {"shade.normal_bend", "shade.flat_share"}, result["shading"]
    assert not [w for w in result["warn"] if "shade." in w], result["warn"]
    plan = json.loads(Path(result["plan"]).read_text(encoding="utf-8"))
    assert "part.tris[chassis]" not in plan["bands"]  # no triangle band reaches the reduction
    assert stages["assemble"]["body_tris"] <= plan["limit"]["max_tris"]
    assert stages["reduce"]["roles"]["body"]["surface_error"] < 0.03
    assert stages["assemble"]["generators"]["vlo"]["tris"] <= 130
    assert Path(result["preview"]["files"]["sheet"]).is_file()
    import xml.etree.ElementTree as ET
    mta = Path(result["mta"]["out"])
    for element in ET.parse(result["mta"]["meta"]).getroot():
        if element.get("src"):
            assert (mta / element.get("src")).is_file()
    assert 'kind = "vehicle"' in Path(result["mta"]["client"]).read_text(encoding="utf-8")


@contextmanager
def studio():
    from satk.studio import api, launcher

    name = f"convert_test_{uuid.uuid4().hex[:8]}"
    launcher.start(name, owner_pid=os.getpid(), idle=600)
    try:
        yield name
    finally:
        try:
            launcher.stop(name)
        finally:
            api.close_all()


BUILDING = """
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
visible = bpy.data.collections.new('Visible building')
bpy.context.scene.collection.children.link(visible)
hidden = bpy.data.collections.new('Hidden render group')
bpy.context.scene.collection.children.link(hidden)
hidden.hide_render = True
child = bpy.data.collections.new('Visible child of hidden group')
hidden.children.link(child)

def move(obj, collection):
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    collection.objects.link(obj)

bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 1.5))
body = bpy.context.object
body.name = 'Concrete booth'
body.scale = (4, 6, 3)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
bevel = body.modifiers.new('Rounded concrete corners', 'BEVEL')
bevel.width = 0.12
bevel.segments = 8
bpy.ops.object.modifier_apply(modifier=bevel.name)
body.scale.x = -1
move(body, visible)
# A visible link wins over another link into a hidden collection.
hidden.objects.link(body)
wall = bpy.data.materials.new('concrete_wall')
wall.use_nodes = True
noise = wall.node_tree.nodes.new('ShaderNodeTexNoise')
noise.inputs['Scale'].default_value = 7
ramp = wall.node_tree.nodes.new('ShaderNodeValToRGB')
ramp.color_ramp.elements[0].color = (0.06, 0.045, 0.035, 1)
ramp.color_ramp.elements[1].color = (0.32, 0.28, 0.23, 1)
wall.node_tree.links.new(noise.outputs['Fac'], ramp.inputs[0])
wall.node_tree.links.new(ramp.outputs['Color'], wall.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])
body.data.materials.append(wall)

bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -3.025, 2.15))
glass = bpy.context.object
glass.scale = (2.4, 0.05, 0.7)
move(glass, visible)
mat = bpy.data.materials.new('glass_facade')
mat.use_nodes = True
bsdf = mat.node_tree.nodes.get('Principled BSDF')
bsdf.inputs['Base Color'].default_value = (0.19, 0.16, 0.12, 1)
# A linked scalar tests Cycles' alpha pass, including partially transparent pixels.
alpha = mat.node_tree.nodes.new('ShaderNodeValue')
alpha.outputs[0].default_value = 0.4
mat.node_tree.links.new(alpha.outputs[0], bsdf.inputs['Alpha'])
glass.data.materials.append(mat)

bpy.ops.mesh.primitive_cube_add(size=10, location=(200, 200, 200))
move(bpy.context.object, child)
bpy.context.view_layer.update()
result['visible_tris'] = sum(len(p.vertices) - 2 for o in (body, glass) for p in o.data.polygons)
bpy.ops.wm.save_as_mainfile(filepath=args['out'])
"""


def test_manual_building_alpha_lod_and_saved_blend(sources):
    import numpy as np
    import xml.etree.ElementTree as ET
    from PIL import Image

    from satk.convert import finish, package, plan
    from satk.studio import api
    from satk.style.texture import load_images

    work = sources["prop"].parent
    source = work / "source_building.blend"
    made = api.cold("author.call", {"method": "python", "params": {"code": BUILDING,
                    "args": {"out": str(source)}}, "stats": "none"}, timeout=120)["result"]["value"]
    folder = work / "building"
    blend = folder / "converted.blend"
    with studio() as session:
        api.call("python", {"code": "bpy.ops.mesh.primitive_cube_add(location=(42, 0, 0))\n"
                  "bpy.context.object.name = 'unrelated'\nbpy.context.object['keep'] = 73"}, session=session)
        prepared = get_op("convert.prepare").call({"model": str(source), "kind": "building", "tier": "vanilla",
                    "dims": [6.1, 4, 3], "name": "cvbuilding", "out": str(folder), "session": session})
        params = {"plan": prepared["plan"]}
        with pytest.raises(SatkError) as error:
            api.call("convert.normalize", params, session=session, stats="none")
        assert error.value.code == "NOT_READY"
        imported_call = api.call("convert.import", params, session=session, stats="none")
        imported = imported_call["result"]
        assert imported["objects"] == 2
        assert imported["tris"] == made["visible_tris"]
        assert imported["skipped_hidden_or_nonmesh"] >= 1
        volumes = api.call("python", {"code": "import bmesh\nresult['volumes'] = []\n"
                          "for name in args['objects']:\n"
                          "    bm = bmesh.new()\n    bm.from_mesh(bpy.data.objects[name].data)\n"
                          "    result['volumes'].append(bm.calc_volume(signed=True))\n    bm.free()",
                          "args": {"objects": imported_call["changed"]}}, session=session, stats="none")["result"]["value"]
        assert all(v > 0 for v in volumes["volumes"]), volumes
        for step in ("normalize", "clean", "reduce", "bake"):
            api.call(f"convert.{step}", params, session=session, stats="none", timeout=120)
        with pytest.raises(SatkError) as error:
            api.call("convert.assemble", params, session=session, stats="none")
        assert error.value.code == "NOT_READY"
        finished = get_op("convert.finish").call(params)
        api.call("convert.assemble", params, session=session, save=str(blend), stats="none", timeout=120)
        exported = get_op("kit.export").call({"model": "cvbuilding", "session": session,
                    "out": str(folder / "package"), "col": "auto", "timeout": 120})
        sentinel = api.call("python", {"code": "o = bpy.data.objects['unrelated']\n"
                            "result.update(keep=o['keep'], location=list(o.location), hidden=o.hide_render)"},
                            session=session, stats="none")["result"]["value"]
        assert sentinel == {"keep": 73, "location": [42.0, 0.0, 0.0], "hidden": False}
    p, _ = plan.load(prepared["plan"])
    assert Path(exported["files"]["lod_dff"]).is_file()
    assert Path(exported["files"]["col"]).is_file()
    check = get_op("asset.check").call({"target": exported["files"]["dff"], "cls": "building", "full": True})
    assert check.get("counts", {}).get("error", 0) == 0, check
    decoded = {name.rsplit("/", 1)[-1]: (w, h, rgba) for name, w, h, rgba in load_images(exported["files"]["txd"])}
    assert any(name.startswith("lod_") and (w, h) == (64, 64) for name, (w, h, _) in decoded.items()), decoded.keys()
    texture = finished["textures"][0]
    with Image.open(folder / "baked" / "cvbuilding_wall.png") as image:
        baked_alpha = np.array(image.convert("RGBA"))[..., 3]
    with Image.open(texture["file"]) as image:
        final_alpha = np.array(image.convert("RGBA"))[..., 3]
    assert np.array_equal(final_alpha, baked_alpha)
    assert ((baked_alpha > 90) & (baked_alpha < 115)).any(), np.unique(baked_alpha)
    assert (baked_alpha == 255).any()
    w, h, rgba = decoded[texture["name"]]
    actual_alpha = np.frombuffer(rgba, np.uint8).reshape(h, w, 4)[..., 3]
    assert np.abs(actual_alpha.astype(int) - final_alpha.astype(int)).max() <= 8
    assert all(t["verdict"] == "in" for t in finish.check_txd(exported["files"]["txd"], finished["textures"],
                                                             p["texture_distribution"]))
    mta = package.mta(p, exported, folder)
    files = [e.get("src") for e in ET.parse(mta["meta"]).getroot().findall("file")]
    assert len(files) == len(set(files))
    assert Path(exported["files"]["lod_dff"]).name in files
    lua = Path(mta["client"]).read_text(encoding="utf-8")
    assert lua.count('txd = "cvbuilding.txd"') == 2
    reloaded = api.cold("author.call", {"method": "python", "params": {"code": """
from satk_blender.kit import util as U
U.ensure_dff()
collections = [c for c in bpy.data.collections if c.get('satk_name') == 'cvbuilding'
               or c.get('satk_lod_of') == 'cvbuilding']
objects = [o for c in collections for o in c.objects if o.type == 'MESH' and len(o.data.polygons)]
assert objects
result['missing_materials'] = sum(s.material is None for o in objects for s in o.material_slots)
result['images'] = sorted({tuple(n.image.size) for o in objects for s in o.material_slots if s.material
                          for n in s.material.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image})
result['sentinel'] = bpy.data.objects['unrelated']['keep']
"""}, "stats": "none"}, blend=blend, timeout=120)["result"]["value"]
    assert reloaded["missing_materials"] == 0, reloaded
    assert reloaded["images"] == [[64, 64], [128, 128]], reloaded
    assert reloaded["sentinel"] == 73


def test_obj_import_refuses_source_revision(sources):
    from satk.convert import plan
    from satk.convert.pipeline import install
    from satk.studio import api

    work = sources["prop"].parent
    source = work / "tetrahedron.obj"
    text = "v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf 1 3 2\nf 1 2 4\nf 1 4 3\nf 2 3 4\n"
    source.write_text(text, encoding="utf-8")
    _, path = plan.prepare(str(source), out=str(work / "obj"), name="cvobj", dims=[1, 1, 1])
    with studio() as session:
        install(session)
        params = {"plan": str(path)}
        source.write_text(text + "# saved after prepare\n", encoding="utf-8")
        with pytest.raises(SatkError) as error:
            api.call("convert.import", params, session=session, stats="none")
        assert error.value.code == "REVISION"
        source.write_text(text, encoding="utf-8")
        imported = api.call("convert.import", params, session=session, stats="none")["result"]
        assert imported["objects"] == 1 and imported["tris"] == 4
        for step in ("normalize", "clean"):
            api.call(f"convert.{step}", params, session=session, stats="none")
        with pytest.raises(SatkError) as error:
            api.call("convert.import", params, session=session, stats="none")
        assert error.value.code == "EXISTS"
