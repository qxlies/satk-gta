"""Session reliability with the real Blender 5.1 (markers blender, slow): references to depsgraph copies are
repaired after every method (the preview crash after ``kit.lod``), the per-call watchdog (a Python loop is
stopped; a call stuck in native code is answered TIMEOUT, then BUSY, and the session recovers), game-look step
snapshots, the parameter table of one method and the default checkpoint of a batch.

The work directory is a temp dir (``SATK_PATHS_WORK``); the session's owner is this process. Reads nothing of
the game.
"""

from __future__ import annotations

import os
import time

import pytest

from satk.core import config
from satk.core.errors import SatkError

pytestmark = [pytest.mark.blender, pytest.mark.slow]

NAME = "reliable"

#: A mesh built like kit.lod built its LOD: from ``evaluated.to_mesh()`` with that mesh's (evaluated)
#: materials appended; the object sorts first so the game look reaches its material copy first.
BAD_LOD = """
import bmesh
m = bpy.data.materials.new('hd_mat')
bpy.ops.mesh.primitive_cylinder_add(vertices=16)
src = bpy.context.active_object
src.name = 'hd'
src.data.materials.append(m)
dg = bpy.context.evaluated_depsgraph_get()
ev = src.evaluated_get(dg)
me = ev.to_mesh()
bm = bmesh.new()
bm.from_mesh(me)
out = bpy.data.meshes.new('a_lod_mesh')
bm.to_mesh(out)
bm.free()
for mm in me.materials:
    out.materials.append(mm)
ev.to_mesh_clear()
o = bpy.data.objects.new('a_lod', out)
bpy.context.scene.collection.objects.link(o)
result = {'evaluated': [x.is_evaluated for x in out.materials]}
"""

CHECK_REFS = """
result = {'evaluated': sorted(f'{me.name}:{m.name}' for me in bpy.data.meshes for m in me.materials
                              if m is not None and m.is_evaluated)}
"""


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from satk.blender import runner
    from satk.docs.cleanup import remove_tree
    from satk.studio import api
    from satk.studio import launcher as L

    try:
        runner.blender_exe()
    except SatkError as e:
        pytest.skip(str(e))
    work = tmp_path_factory.mktemp("reliable") / "work"
    work.mkdir()
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    L.start(NAME, owner_pid=os.getpid(), idle=600)
    try:
        yield work
    finally:
        api.close_all()
        try:
            L.stop(NAME)
        finally:
            if old is None:
                os.environ.pop("SATK_PATHS_WORK", None)
            else:
                os.environ["SATK_PATHS_WORK"] = old
            config.reset()
            remove_tree(work.parent)


def _call(method: str, params: dict | None = None, **kw) -> dict:
    from satk.studio import api

    return api.call(method, params, session=NAME, **kw)


def _py(code: str, **kw) -> dict:
    return (_call("python", {"code": code}, stats="none", **kw).get("result") or {}).get("value") or {}


def _idle(seconds: float = 60.0) -> None:
    from satk.studio import launcher as L

    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        if not L.status(NAME).get("busy"):
            return
        time.sleep(0.3)
    raise AssertionError("the session stayed busy")


def test_depsgraph_copies_are_repaired_after_every_method(live):
    _call("scene.clear")
    assert _py(BAD_LOD)["evaluated"] == [True]       # what the step itself built
    assert _py(CHECK_REFS)["evaluated"] == []          # repaired before the next step
    r = _call("look.render", {"views": ["3q"], "size": 96})
    assert r["result"]["views"] == 1
    assert _call("scene.info")["result"]["total"] == 2  # the session is alive
    log = (live / "studio" / NAME / "blender.log").read_text(encoding="utf-8", errors="replace")
    assert "python: repaired 1 reference(s) to depsgraph copies" in log


def test_watchdog_stops_a_python_loop(live):
    t0 = time.monotonic()
    with pytest.raises(SatkError) as ei:
        _py("while True:\n    pass", timeout=2)
    assert ei.value.code == "TIMEOUT" and time.monotonic() - t0 < 6
    assert _call("scene.info")["result"]["total"] >= 0  # usable at once


def test_a_call_stuck_in_native_code_answers_then_recovers(live):
    from satk.studio import launcher as L

    t0 = time.monotonic()
    with pytest.raises(SatkError) as ei:
        _py("import time\ntime.sleep(8)\nresult = 1", timeout=1)
    assert ei.value.code == "TIMEOUT" and "could not be stopped" in ei.value.msg
    assert time.monotonic() - t0 < 7
    st = L.status(NAME)                               # answered while the main thread is stuck
    assert st["up"] and st["busy"]["over"] is True and st["busy"]["method"] == "author.call python"
    with pytest.raises(SatkError) as ei:
        _call("scene.info")
    assert ei.value.code == "BUSY"
    _idle()
    assert _call("scene.info")["method"] == "scene.info"


def test_game_look_snapshot(live):
    _call("scene.clear")
    _call("mesh.primitive", {"kind": "cylinder", "segments": 12, "name": "post", "depth": 1.2, "radius": 0.2})
    r = _call("scene.info", {"limit": 0}, snapshot={"view": "3q", "look": "game"}, size=128)
    assert r["snapshot"].endswith(".jpg") and os.path.isfile(r["snapshot"])
    assert not any(w.startswith("UNSUPPORTED") for w in r.get("warn") or [])
    r = _call("scene.info", {"limit": 0}, snapshot={"views": ["3q", "top"], "look": "game"}, size=256)
    assert os.path.isfile(r["snapshot"]) and not r.get("warn")


def test_one_method_by_name_lists_its_parameters(live):
    from satk.studio import api

    res = api.methods("mesh.lathe", session=NAME)
    rows = dict((k, v) for k, v in res["params"])
    assert rows["segments"] == "required" and rows["profile"] == "required" and rows["angle"] == "360.0"


def test_a_batch_writes_one_checkpoint_by_default(live):
    r = _call("batch", {"steps": [{"method": "mesh.primitive", "params": {"kind": "cube", "name": "b1"}},
                                  {"method": "mesh.primitive", "params": {"kind": "cube", "name": "b2"}}]})
    assert r["checkpoint"].endswith(f"{r['n']:04d}.blend")
    assert [s["result"]["object"] for s in r["steps"]] == ["b1", "b2"]
