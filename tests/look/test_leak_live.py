"""The light-leak check and region previews with the real Blender 5.1 (markers blender, slow; vanilla also game).

Synthetic scenes are built in a studio session ``leaklive`` that runs in a temp work directory
(``SATK_PATHS_WORK``): a closed box is clean, a box with aligned side holes shows a gap, a hole into a closed shell
is see-through for a culled kind while a single one-sided plane seen from behind is information, a slit between two
solid parts is a gap, a room shell with a crack leaks to the void. The vanilla calibration
(landstal, premier: no false gaps) reads the game only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from satk.core import config
from satk.core.errors import SatkError
from satk.core.registry import get_op

pytestmark = [pytest.mark.blender, pytest.mark.slow]

NAME = "leaklive"


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
    work = tmp_path_factory.mktemp("leak") / "work"
    work.mkdir()
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    try:
        runner.ensure_dragonff()
    except SatkError as e:
        os.environ.pop("SATK_PATHS_WORK", None) if old is None else os.environ.__setitem__("SATK_PATHS_WORK", old)
        config.reset()
        pytest.skip(str(e))
    L.start(NAME, owner_pid=os.getpid(), idle=600)
    try:
        yield {"work": work}
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


def _call(method: str, params: dict | None = None) -> dict:
    from satk.studio import api

    return api.call(method, params, session=NAME, stats="none", timeout=120).get("result") or {}


def _py(code: str, args: dict | None = None) -> dict:
    return (_call("python", {"code": code, "args": args or {}}).get("value") or {})


#: A box shell (outward faces) 2 x 4 x 1.4 m standing on z = 0; ``holes`` cuts the middle of both long sides
#: (aligned: you see through the body), ``inward`` turns every face inside (a room), ``crack`` leaves a thin slit
#: in the +Y wall of a room, ``plane`` makes one single-sided quad facing +Y.
_BUILD = """
import bmesh
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
bm = bmesh.new()
W, L, H = 1.0, 2.0, 1.4
def q(a, b, c, d):
    vs = [bm.verts.new(p) for p in (a, b, c, d)]
    f = bm.faces.new(vs)
    return f
if args.get('plane'):
    q((-1, 0, 0), (1, 0, 0), (1, 0, 2), (-1, 0, 2))      # faces -Y ... set below
else:
    q((-W, -L, 0), (-W, L, 0), (W, L, 0), (W, -L, 0))        # bottom
    q((-W, -L, H), (W, -L, H), (W, L, H), (-W, L, H))        # top
    if args.get('crack'):                                     # front +Y with a 4 cm slit
        q((-W, L, 0), (-W, L, H), (-0.02, L, H), (-0.02, L, 0))
        q((0.02, L, 0), (0.02, L, H), (W, L, H), (W, L, 0))
    else:
        q((-W, L, 0), (-W, L, H), (W, L, H), (W, L, 0))      # front +Y
    q((-W, -L, 0), (W, -L, 0), (W, -L, H), (-W, -L, H))      # rear -Y
    for x in (-W, W):
        pts = [(x, -L, 0), (x, L, 0), (x, L, H), (x, -L, H)]
        if args.get('holes'):
            a, b, z0, z1 = -0.5, 0.5, 0.4, 1.0
            frame = [((x, -L, 0), (x, L, 0), (x, L, z0), (x, -L, z0)), ((x, -L, z1), (x, L, z1), (x, L, H), (x, -L, H)),
                     ((x, -L, z0), (x, a, z0), (x, a, z1), (x, -L, z1)), ((x, b, z0), (x, L, z0), (x, L, z1), (x, b, z1))]
            for fr in frame:
                q(*fr)
        else:
            q(*pts)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
if args.get('inward'):
    bmesh.ops.reverse_faces(bm, faces=bm.faces)
if args.get('plane'):
    for f in bm.faces:
        f.normal_update()
        if f.normal.y < 0:
            bmesh.ops.reverse_faces(bm, faces=[f])
me = bpy.data.meshes.new('shell')
bm.to_mesh(me)
bm.free()
for p in me.polygons:
    p.use_smooth = False
o = bpy.data.objects.new('shell', me)
bpy.context.scene.collection.objects.link(o)
if args.get('dummy'):
    e = bpy.data.objects.new('wheel_lf_dummy', None)
    bpy.context.scene.collection.objects.link(e)
    e.location = (-1.0, 0.0, 0.7)
result['faces'] = len(me.polygons)
"""


def _leak(**p) -> dict:
    return _call("look.leak", p)


def test_closed_box_is_clean(live):
    _py(_BUILD, {})
    r = _leak(kind="automobile", views=["side", "3q", "front", "top"], size=160)
    assert r["clean"] is True and r["blocking"] == 0 and not r["rows"], r


def test_holes_through_the_body_are_located_gaps(live):
    _py(_BUILD, {"holes": True})
    r = _leak(kind="automobile", views=["side", "3q"], size=200)
    assert r["clean"] is False and r["blocking"] >= 1, r
    g = r["rows"][0]
    assert g[1] == "gap" and g[2] in (None, "") and g[3] == "side" and g[4] > 500   # 1.0 x 0.6 m hole: ~6000 cm2
    x, y, z = g[5]
    assert abs(abs(x) - 1.0) < 0.15 and abs(y) < 0.6 and 0.3 < z < 1.1             # on the near side, in the hole
    # the same holes seen as a prop (culled): light through the hollow shell, and the far wall's inside is culled
    r = _leak(kind="prop", views=["side"], size=160)
    assert r["clean"] is False and r["rows"][0][1] in ("gap", "see_through"), r


def test_inside_of_the_body_through_an_arch_hole(live):
    """A vehicle shows the inside of a shell through an opening (double-sided): it blocks in the wheel close-up."""
    _py(_BUILD, {"holes": True, "dummy": True})
    r = _leak(kind="automobile", regions=["wheels"], size=200)
    kinds = {row[1] for row in r["rows"]}
    assert r["clean"] is False and kinds & {"gap", "inside"}, r


def test_single_plane_from_behind(live):
    _py(_BUILD, {"plane": True})
    front = _leak(kind="prop", views=["front"], size=128)      # the camera at +Y sees the plane's front
    assert front["clean"] is True, front
    # from -Y the game culls it: a one-sided card (a fence, a facade card) seen from behind is information
    back = _leak(kind="prop", views=["rear"], size=128)
    assert back["clean"] is True and back.get("info", 0) >= 1, back
    vehicle = _leak(kind="automobile", views=["rear"], size=128)  # a vehicle draws both sides
    assert vehicle["clean"] is True, vehicle


def test_a_slit_between_solid_parts_is_a_gap(live):
    """Two closed boxes 2 cm apart under one plate and on one base: light through the slit (no hollow shell)."""
    _call("scene.clear")
    for name, size, loc in (("left", [0.29, 0.6, 1.0], [-0.155, 0, 0.6]), ("right", [0.29, 0.6, 1.0], [0.155, 0, 0.6]),
                            ("base", [0.7, 0.7, 0.1], [0, 0, 0.05]), ("top", [0.7, 0.7, 0.05], [0, 0, 1.125])):
        _call("mesh.primitive", {"kind": "cube", "name": name, "size": size, "location": loc})
    r = _leak(kind="prop", views=["front"], size=256)
    assert r["clean"] is False and r["rows"][0][1] == "gap", r
    pt = r["rows"][0][5]
    assert pt is None or abs(pt[0]) < 0.05, r                                 # on the slit (x = 0)
    _call("scene.transform", {"object": "right", "location": [-0.02, 0, 0], "delta": True})   # closed
    r = _leak(kind="prop", views=["front"], size=256)
    assert r["clean"] is True, r


def test_room_shell_crack_leaks_to_the_void(live, tmp_path):
    _py(_BUILD, {"inward": True})
    r = _leak(kind="interior_shell", size=160)
    assert r["clean"] is True, r
    _py(_BUILD, {"inward": True, "crack": True})
    pkg = live["work"] / "out" / "pkg"
    pkg.mkdir(parents=True)
    r = _leak(kind="interior_shell", size=200, package=str(pkg))
    assert r["clean"] is False and r["rows"][0][1] == "escape", r
    doc = json.loads((pkg / "checks" / "session.leak.json").read_text(encoding="utf-8"))
    assert doc["clean"] is False and doc["kind"] == "interior_shell" and doc["gaps"][0]["width_m"] <= 0.12


def test_region_preview_of_a_session(live):
    _py(_BUILD, {"holes": True, "dummy": True})
    r = get_op("blender.preview").call({"subject": f"session:{NAME}", "regions": ["left", "wheels", "roof"],
                                         "passes": ["game", "leak"], "kind": "automobile", "size": 160})
    rows = r["regions"]["rows"]
    assert [row[0] for row in rows] == ["left", "wheels", "roof"]
    n = Path(rows[0][1]).name.split("-")[1]
    assert all(Path(row[1]).is_file() and Path(row[1]).name == f"preview-{n}-{row[0]}.jpg" for row in rows)
    assert Path(r["files"]["index"]).is_file() and r["kind"] == "automobile"
    assert r["leak"]["clean"] is False and r["leak"]["gaps"] >= 1


@pytest.fixture(scope="module")
def vanilla_work(tmp_path_factory):
    work = tmp_path_factory.mktemp("leakvan")
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    from satk.blender import gamedata, runner

    gamedata.clear_cache()
    try:
        runner.blender_exe()
        runner.ensure_dragonff()
    except SatkError as e:
        pytest.skip(str(e))
    yield work
    if old is None:
        os.environ.pop("SATK_PATHS_WORK", None)
    else:
        os.environ["SATK_PATHS_WORK"] = old
    config.reset()


@pytest.mark.game
@pytest.mark.parametrize("sid", ["model:400", "model:426", "model:462", "model:487", "model:452"])
def test_vanilla_has_no_false_gaps(vanilla_work, sid):
    """Calibration: landstal, premier, faggio, maverick and speeder leak nothing from every review camera."""
    r = get_op("look.leak").call({"subject": sid, "size": 256})
    assert r["clean"] is True, r["rows"]
    assert r["views"] >= 8
