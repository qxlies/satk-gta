"""Authoring and baking on a synthetic armature in real Blender, through satk's public runner."""

from __future__ import annotations

import textwrap

import pytest

pytestmark = pytest.mark.blender


@pytest.fixture
def blender_script(tmp_path, monkeypatch):
    from satk.blender import runner
    from satk.core import config

    monkeypatch.setenv("SATK_PATHS_WORK", str(tmp_path))
    config.reset()

    def run(body: str):
        script = tmp_path / "check_action.py"
        script.write_text(
            "import sys\nsys.dont_write_bytecode = True\n"
            f"sys.path[:0] = [{str(runner.satk_src())!r}, {str(runner.addon_dir().parent)!r}]\n"
            + textwrap.dedent('''
                import bpy
                from mathutils import Quaternion, Vector
                from satk.anim.ifp import Anim, Seq
                from satk.anim.jsonio import json_to_ifp
                from satk_blender.anim.apply import apply_anim, export_action

                bpy.ops.object.select_all(action='SELECT')
                bpy.ops.object.delete(use_global=False)
                data = bpy.data.armatures.new("TestRig")
                arm = bpy.data.objects.new("TestRig", data)
                bpy.context.collection.objects.link(arm)
                bpy.context.view_layer.objects.active = arm
                arm.select_set(True)
                bpy.ops.object.mode_set(mode='EDIT')
                root = data.edit_bones.new("Root")
                root.head, root.tail = (0, 0, 0), (0, 1, 0)
                child = data.edit_bones.new("Child")
                child.head, child.tail, child.parent = (0, 1, 0), (0, 2, 0), root
                bpy.ops.object.mode_set(mode='OBJECT')
                data.bones['Root']['bone_id'] = 0
                data.bones['Child']['bone_id'] = 1
                bpy.context.scene.render.fps = 30
            ''') + "\n" + textwrap.dedent(body), encoding="utf-8")
        log = tmp_path / "blender.log"
        code, _ = runner.run_blender(
            ["-b", "--factory-startup", "--python-exit-code", "1", "--python", str(script)],
            log=log, timeout=90, env=runner.blender_env())
        assert code == 0, log.read_text(encoding="utf-8", errors="replace")[-5000:]

    yield run
    config.reset()


def test_bake_evaluates_constraints_and_unkeyed_bones(blender_script):
    blender_script('''
        root = arm.pose.bones['Root']
        root.rotation_mode = 'QUATERNION'
        for f in (0, 30):
            root.keyframe_insert('rotation_quaternion', frame=f)
        action = arm.animation_data.action
        controller = bpy.data.objects.new('Controller', None)
        bpy.context.collection.objects.link(controller)
        for f, x in ((0, 1), (30, 3)):
            controller.location = (x, 2, 0)
            controller.keyframe_insert('location', frame=f)
        constraint = root.constraints.new('COPY_LOCATION')
        constraint.target = controller
        child_constraint = arm.pose.bones['Child'].constraints.new('COPY_LOCATION')
        child_constraint.target = controller
        bpy.context.scene.frame_set(7, subframe=0.25)
        doc, warnings = export_action(arm, action, bake=True, compressed=False, use_meta=False)
        bones = {b['tag']: b for b in doc['bones']}
        assert set(bones) == {0, 1}, bones.keys()
        assert len(bones[0]['keys']) == len(bones[1]['keys']) == 31
        assert bones[0]['keys'][0][5:] == [1.0, 2.0, 0.0], bones[0]['keys'][0]
        assert bones[0]['keys'][-1][5:] == [3.0, 2.0, 0.0], bones[0]['keys'][-1]
        # The child's world location equals the root's, so its parent-relative offset is zero.
        assert bones[1]['keys'][-1][5:] == [0.0, 0.0, 0.0], bones[1]['keys'][-1]
        assert bpy.context.scene.frame_current == 7 and bpy.context.scene.frame_subframe == 0.25
    ''')


def test_export_euler_action_and_new_channels_on_imported_action(blender_script):
    blender_script('''
        import math
        root = arm.pose.bones['Root']
        root.rotation_mode = 'XYZ'
        for f, angle in ((0, 0), (30, math.pi / 2)):
            root.rotation_euler = (0, 0, angle)
            root.keyframe_insert('rotation_euler', frame=f)
        doc, warnings = export_action(arm, arm.animation_data.action, compressed=False, use_meta=False)
        assert len(doc['bones']) == 1, doc
        assert abs(doc['bones'][0]['keys'][-1][3] - math.sqrt(0.5)) < 1e-6, doc

        source = Anim('imported', [Seq('Root', 0, False, True, [(0, 0, 0, 4096, 0)])], 1)
        action = apply_anim(arm, source, fps=30, action_name=source.name)['action']
        root.location = (2, 0, 0)
        root.keyframe_insert('location', frame=0)
        child = arm.pose.bones['Child']
        child.rotation_mode = 'QUATERNION'
        child.keyframe_insert('rotation_quaternion', frame=0)
        doc, warnings = export_action(arm, action)
        assert [b['tag'] for b in doc['bones']] == [0, 1], doc
        assert doc['bones'][0]['trans'] and doc['bones'][0]['keys'][0][5:] == [2.0, 0.0, 0.0], doc
    ''')


def test_adjusted_times_and_compression_overrides(blender_script):
    blender_script('''
        # More duplicates than time rounding alone can recover, followed by a decreasing time.
        times = [1.0] * 20 + [0.75, 1.5]
        seq = Seq('Root', 0, False, False, [(0, 0, 0, 1, t) for t in times])
        source = Anim('repeated', [seq, Seq('prop', -1, False, False, [(0, 0, 0, 1, 0)])])
        action = apply_anim(arm, source, fps=1, action_name=source.name)['action']
        doc, warnings = export_action(arm, action)
        back = json_to_ifp(doc).anims[0]
        assert back.seqs[0].times() == times, back.seqs[0].times()
        doc, warnings = export_action(arm, action, compressed=True)
        back = json_to_ifp(doc).anims[0]
        assert all(s.compressed for s in back.seqs), doc
        assert back.seqs[0].times() == times
        doc, warnings = export_action(arm, action, compressed=False)
        assert not any(s.compressed for s in json_to_ifp(doc).anims[0].seqs)
        # Edits take precedence over timing and name metadata.
        from satk_blender.anim.apply import _fcurves
        for curve in _fcurves(action, arm).values():
            curve.keyframe_points[1].co.x = 3.0
            curve.update()
        action.name = 'renamed'
        doc, warnings = export_action(arm, action)
        assert doc['name'] == 'renamed' and max(k[0] for k in doc['bones'][0]['keys']) == 3.0, doc
    ''')


def test_axis_angle_and_imported_action_order(blender_script):
    blender_script('''
        import math
        from satk_blender.anim.job import _actions_for
        root = arm.pose.bones['Root']
        root.rotation_mode = 'AXIS_ANGLE'
        for f, angle in ((0, 0), (30, math.pi / 2)):
            root.rotation_axis_angle = (angle, 0, 0, 1)
            root.keyframe_insert('rotation_axis_angle', frame=f)
        doc, warnings = export_action(arm, arm.animation_data.action, compressed=False, use_meta=False)
        assert abs(doc['bones'][0]['keys'][-1][3] - math.sqrt(0.5)) < 1e-6, doc
        for i, name in enumerate(('zeta', 'alpha')):
            source = Anim(name, [Seq('Root', 0, False, True, [(0, 0, 0, 4096, 0)])], 1)
            apply_anim(arm, source, fps=30, action_name=name, source={'index': i})
        assert [a.name for a in _actions_for(arm, None)] == ['zeta', 'alpha']
    ''')
