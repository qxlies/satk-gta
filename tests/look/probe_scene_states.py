"""Runs inside Blender (``tests/look/test_scene_states.py``): the states of a scene that came from a .blend,
with DragonFF not registered (a live session that has not imported a model yet).

``blender -b --factory-startup --python probe_scene_states.py -- <request.json>``; exit code 1 on failure.
"""

import json
import sys

sys.dont_write_bytecode = True
REQ = json.load(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8"))
for p in (REQ["satk_src"], REQ["addon_parent"]):
    if p not in sys.path:
        sys.path.insert(0, p)

import bpy  # noqa: E402

from satk_blender.look import api, preview  # noqa: E402


def mesh(name):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


names = ["chassis", "bonnet_dam", "bonnet_ok", "premier.dff.premier_col.ColMesh",
         "premier.dff.premier_col.ShadowMesh", "premier.dff.premier_col.ShadowMesh.001"]
objs = [mesh(n) for n in names]
assert not hasattr(objs[0], "dff"), "DragonFF must not be registered in this probe"
shown = preview.set_state(objs, "ok")["shown"]
vis = sorted(o.name for o in objs if not o.hide_render)
assert vis == ["bonnet_ok", "chassis"], vis
assert shown == 2, shown
col = sorted(o.name for o in objs if not o.hide_render) if preview.set_state(objs, "col") else []
assert col == sorted(n for n in names if "Col" in n or "Shadow" in n), col
assert isinstance(api.ensure_dragonff(), bool)
json.dump({"ok": True}, open(REQ["out"], "w"))
