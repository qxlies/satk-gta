"""A scene from a .blend shows the right parts when DragonFF is not registered (Blender, no game files)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.blender]


def test_collision_and_shadow_meshes_are_hidden_without_dragonff(tmp_path):
    from satk.blender import runner

    try:
        exe = runner.blender_exe()  # noqa: F841 - skips without Blender
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Blender: {e}")
    req = {"satk_src": str(runner.satk_src()), "addon_parent": str(runner.addon_dir().parent),
           "out": str(tmp_path / "ok.json")}
    rp = tmp_path / "request.json"
    rp.write_text(json.dumps(req), encoding="utf-8")
    script = Path(__file__).with_name("probe_scene_states.py")
    code, _s = runner.run_blender(["-b", "--factory-startup", "--python-exit-code", "1", "--python", str(script),
                                   "--", str(rp)], log=tmp_path / "blender.log", timeout=300,
                                  env=runner.blender_env(tmp_path))
    assert code == 0, runner.log_tail(tmp_path / "blender.log", 20)
    assert json.loads((tmp_path / "ok.json").read_text(encoding="utf-8")) == {"ok": True}
