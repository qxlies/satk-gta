"""Contract K1 inside Blender 5.1 (marker ``blender``): metrics.py runs in Blender's bundled Python 3.13.

Blender runs headless with satk's isolated profile in a private ``SATK_PATHS_WORK``; ``metrics.py`` is
loaded by file path (no satk import) and fed the evaluated mesh the way the docstring shows.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from satk.core import config
from satk.style import metrics as SM

pytestmark = [pytest.mark.blender, pytest.mark.slow]

_SCRIPT = r'''
import sys, json
sys.dont_write_bytecode = True
import importlib.util
import bpy
import numpy as np

spec = importlib.util.spec_from_file_location("satk_k1", METRICS)
K1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(K1)


def arrays(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    me.calc_loop_triangles()
    lt = me.loop_triangles
    pos = np.empty(len(me.vertices) * 3, np.float32); me.vertices.foreach_get("co", pos)
    tris = np.empty(len(lt) * 3, np.int32); lt.foreach_get("vertices", tris)
    loops = np.empty(len(lt) * 3, np.int32); lt.foreach_get("loops", loops)
    cn = np.empty(len(me.loops) * 3, np.float32); me.corner_normals.foreach_get("vector", cn)
    uvl = me.uv_layers.active
    uv = None
    if uvl is not None:
        u = np.empty(len(me.loops) * 2, np.float32); uvl.data.foreach_get("uv", u)
        uv = u.reshape(-1, 2)[loops]
    mat = np.empty(len(lt), np.int32); lt.foreach_get("material_index", mat)
    out = dict(pos=pos, tris=tris, corner_normals=cn.reshape(-1, 3)[loops], uv=uv, mat=mat)
    ev.to_mesh_clear()
    return out


bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=1.0)
obj = bpy.context.active_object
res = {"py": list(sys.version_info[:2]), "satk": any(k == "satk" or k.startswith("satk.") for k in sys.modules)}
obj.data.shade_flat()
res["flat"] = K1.mesh_metrics(**arrays(obj))
obj.data.shade_smooth()
res["smooth"] = K1.mesh_metrics(**arrays(obj))
mod = obj.modifiers.new("sub", "SUBSURF")
mod.levels = 1
res["subsurf"] = K1.mesh_metrics(**arrays(obj))
json.dump(res, open(OUT, "w", encoding="utf-8"))
'''


@pytest.fixture(scope="module")
def blender_k1(tmp_path_factory):
    work = tmp_path_factory.mktemp("work")
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    try:
        from satk.blender import runner

        script, out, log = work / "k1.py", work / "k1.json", work / "k1.log"
        script.write_text(f"METRICS = {str(Path(SM.__file__))!r}\nOUT = {str(out)!r}\n" + _SCRIPT, encoding="utf-8")
        code, _sec = runner.run_blender(["-b", "--factory-startup", "--python-exit-code", "1", "--python", str(script)],
                                        log=log, timeout=180, env=runner.blender_env())
        assert code == 0, log.read_text(encoding="utf-8", errors="replace")[-2000:]
        yield json.loads(out.read_text(encoding="utf-8"))
    finally:
        if old is None:
            os.environ.pop("SATK_PATHS_WORK", None)
        else:
            os.environ["SATK_PATHS_WORK"] = old
        config.reset()


def test_k1_runs_in_blender_python(blender_k1):
    assert blender_k1["py"][0] == 3 and blender_k1["py"][1] >= 13
    assert blender_k1["satk"] is False                       # loaded standalone, nothing else of satk


def test_k1_flat_vs_smooth_in_blender(blender_k1):
    flat, smooth, sub = blender_k1["flat"], blender_k1["smooth"], blender_k1["subsurf"]
    assert flat["geo.tris"] == smooth["geo.tris"] == 16 * 2 + 16 * 2 * 6   # quads triangulated, fans at the poles
    assert flat["shade.normal_bend"] < 0.01 and flat["shade.flat_share"] == 1.0 and flat["shade.hard_edge_share"] > 0.5
    assert smooth["shade.normal_bend"] > 5 and smooth["shade.flat_share"] == 0 and smooth["shade.hard_edge_share"] == 0
    for m in (flat, smooth, sub):
        assert m["geo.pieces"] == 1 and m["geo.open_edges"] == 0 and m["geo.nonmanifold_edges"] == 0
        assert m["uv.zero_area_share"] == 0
    # the evaluated stack (modifiers live) is what is measured
    assert sub["geo.tris"] > 3 * smooth["geo.tris"] and sub["shade.normal_bend"] < smooth["shade.normal_bend"]
    assert smooth["dff.verts_per_tri"] < flat["dff.verts_per_tri"]
