"""IFP animations through the real Blender 5.1 + DragonFF on a vanilla ped (markers blender, game, slow).

The work directory is redirected to a temp dir (``SATK_PATHS_WORK``), like ``tests/blender/test_live_blender.py``:
DragonFF is extracted there, the Blender profile and the jobs live there. The game is only read.

* four ped.ifp animations on male01 (model:7): every sequence binds except the prop frame of CAR_LjackedLHS,
  the actions exported back in the same session give the source int16 key frames exactly, the render shows the ped;
* ``from-blender`` on the saved .blend writes the same bytes as extracting those animations into an IFP;
* ``--fresh`` (no stored order) gives WALK_civi's key frames exactly too.
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

ANIMS = ["walk_civi", "idle_stance", "run_civi", "CAR_LjackedLHS"]


@pytest.fixture(scope="module")
def live_work(tmp_path_factory):
    work = tmp_path_factory.mktemp("work")
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    yield work
    if old is None:
        os.environ.pop("SATK_PATHS_WORK", None)
    else:
        os.environ["SATK_PATHS_WORK"] = old
    config.reset()


@pytest.fixture(scope="module")
def applied(live_work):
    from satk.core.paths import cfg

    if not (cfg().paths.game / "anim" / "ped.ifp").is_file():
        pytest.skip("no vanilla copy")
    return get_op("anim.to_blender").call({"target": "anim/ped.ifp", "names": ANIMS, "verify": True,
                                           "render": True, "size": 160})


def test_apply_verify_render(applied):
    r = applied
    assert r["verified"] is True and r["exact"] == "4/4" and r["skin"] == "model:7" and r["fps"] == 30
    rows = {row[0]: row for row in r["rows"]}
    assert rows["WALK_civi"][2:5] == [32, 32, 0] and rows["CAR_LjackedLHS"][4] == 1
    assert all(row[-1] is True for row in r["rows"])
    png = Path(r["png"])
    assert png.is_file() and png.stat().st_size > 5000 and Path(r["blend"]).is_file()


def test_export_saved_blend_bit_exact(applied):
    from satk.anim.ifp import Ifp, read_ifp, write_ifp
    from satk.anim.source import load_ifp

    src = read_ifp(load_ifp("anim/ped.ifp").data)
    want = [a for a in src.anims if a.name.lower() in {n.lower() for n in ANIMS}]
    r = get_op("anim.from_blender").call({"blend": applied["job"], "out": "from_blend.ifp"})
    assert r["verified"] is True and r["anims"] == 4
    assert Path(r["path"]).read_bytes() == write_ifp(Ifp("ANP3", src.pack, want, src.raw_pack))


def test_export_fresh_action(applied):
    from satk.anim.blender import compare
    from satk.anim.ifp import read_ifp
    from satk.anim.source import load_ifp

    r = get_op("anim.from_blender").call({"blend": applied["blend"], "action": ["WALK_civi"], "fresh": True,
                                         "out": "fresh.ifp"})
    out = read_ifp(Path(r["path"]).read_bytes()).anims[0]
    src = read_ifp(load_ifp("anim/ped.ifp").data).find("walk_civi")
    c = compare(src, out)
    assert c["exact"] is True and c["keys"] == sum(len(s.keys) for s in src.seqs)


def test_loose_dff_skin(applied, tmp_path):
    """A custom ped given as a .dff file (+ the .txd next to it): here male01 copied out of gta3.img."""
    from satk.core.paths import cfg
    from satk.formats.img import ImgArchive

    with ImgArchive.open(cfg().paths.game / "models" / "gta3.img") as img:
        for name in ("male01.dff", "male01.txd"):
            (tmp_path / f"my{name}").write_bytes(img.read(img.find(name)))
    r = get_op("anim.to_blender").call({"target": "anim/ped.ifp", "names": ["walk_civi"],
                                        "skin": str(tmp_path / "mymale01.dff"), "verify": True})
    assert r["verified"] is True and r["exact"] == "1/1" and r["rows"][0][3] == 32
    assert r["skin"].endswith("/mymale01.dff") and not any(w.startswith("MISSING_TEX") for w in r.get("warn", []))


def test_repeated_key_times(applied):
    """gym_bp_up_A has keys that share a time (an f-curve cannot): they are moved by 1/50 frame and come back."""
    r = get_op("anim.to_blender").call({"target": "anim/anim.img/benchpress.ifp", "names": ["gym_bp_up_A"],
                                        "verify": True})
    assert r["verified"] is True and r["exact"] == "1/1"
    assert any(w.startswith("SAME_TIME: gym_bp_up_A") for w in r["warn"])


def test_evaluated_bone_poses_match_the_source(applied, live_work):
    """Check evaluated pose matrices, independently of the exporter's inverse conversion."""
    from satk.blender import runner

    script = live_work / "evaluated_pose.py"
    result = live_work / "evaluated_pose.json"
    source = Path(applied["blend"]).parent / "source.ifp"
    script.write_text(
        "import sys\nsys.dont_write_bytecode = True\n"
        f"sys.path.insert(0, {str(runner.satk_src())!r})\n"
        f"source_path = {str(source)!r}\nresult_path = {str(result)!r}\n"
        + textwrap.dedent('''
            import json, math
            from pathlib import Path
            import bpy
            from satk.anim.ifp import read_ifp
            animation = read_ifp(Path(source_path).read_bytes()).find('walk_civi')
            arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
            arm.animation_data.action = bpy.data.actions['WALK_civi']
            names = {int(b.get('bone_id', -1)): b.name for b in arm.data.bones}
            errors = {'rot_deg': 0.0, 'trans_m': 0.0, 'keys': 0}
            for seq in animation.seqs:
                for t, q, tr in seq.frames():
                    frame = t * 30
                    bpy.context.scene.frame_set(math.floor(frame), subframe=frame - math.floor(frame))
                    evaluated = arm.evaluated_get(bpy.context.evaluated_depsgraph_get())
                    pb = evaluated.pose.bones[names[seq.tag]]
                    local = pb.parent.matrix.inverted() @ pb.matrix if pb.parent else pb.matrix.copy()
                    actual = local.to_quaternion()
                    got = (actual.x, actual.y, actual.z, actual.w)
                    norm = math.sqrt(sum(x*x for x in q) * sum(x*x for x in got))
                    dot = min(1.0, abs(sum(x*y for x, y in zip(q, got))) / norm)
                    errors['rot_deg'] = max(errors['rot_deg'], math.degrees(2 * math.acos(dot)))
                    if tr is None:
                        bone = arm.data.bones[pb.name]
                        rest = bone.parent.matrix_local.inverted() @ bone.matrix_local if bone.parent else bone.matrix_local
                        tr = rest.to_translation()
                    errors['trans_m'] = max(errors['trans_m'], max(abs(x-y) for x, y in zip(local.to_translation(), tr)))
                    errors['keys'] += 1
            Path(result_path).write_text(json.dumps(errors), encoding='utf-8')
        '''), encoding="utf-8")
    log = live_work / "evaluated_pose.log"
    code, _ = runner.run_blender(
        ["-b", applied["blend"], "--factory-startup", "--python-exit-code", "1", "--python", str(script)],
        log=log, timeout=90, env=runner.blender_env())
    assert code == 0, log.read_text(encoding="utf-8", errors="replace")[-3000:]
    measured = json.loads(result.read_text(encoding="utf-8"))
    assert measured["keys"] > 32 and measured["rot_deg"] < 0.05 and measured["trans_m"] < 0.001, measured
