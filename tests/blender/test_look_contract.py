"""Contract ``preview``, Blender threads, file plans and environment hygiene (no Blender started)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from satk.blender import contract as C
from satk.core.errors import SatkError

REPO = Path(__file__).resolve().parents[2]


def test_preview_command_contract():
    assert "preview" in C.CMDS
    a = C.normalize_args("preview", {"spec": {"entries": [{"key": "e0"}]}})
    assert a["save"] is False and a["spec"]["entries"]
    with pytest.raises(C.ContractError):
        C.normalize_args("preview", {"spec": {}})
    with pytest.raises(C.ContractError):
        C.normalize_args("preview", {"spec": {"entries": [1]}, "bogus": 1})


def test_thread_limit_from_env_and_config(satk_home, monkeypatch):
    from satk.blender import runner
    from satk.core import config

    monkeypatch.delenv("SATK_BLENDER_THREADS", raising=False)
    config.reset()
    assert runner.blender_threads() == 0 and runner.thread_args() == []
    monkeypatch.setenv("SATK_BLENDER_THREADS", "4")
    assert runner.thread_args() == ["-t", "4"]
    monkeypatch.setenv("SATK_BLENDER_THREADS", "four")
    with pytest.raises(SatkError):
        runner.blender_threads()
    monkeypatch.delenv("SATK_BLENDER_THREADS")
    toml = Path(satk_home) / "threads.toml"
    toml.write_text("[blender]\nthreads = 3\n", encoding="utf-8")
    monkeypatch.setenv("SATK_CONFIG", str(toml))
    config.reset()
    try:
        assert runner.blender_threads() == 3
    finally:
        config.reset()


def test_entry_scripts_disable_thumbnails_and_bytecode():
    for rel in ("blender/satk_blender/agent_cli.py",):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "sys.dont_write_bytecode = True" in text
        assert '"file_preview_type", "NONE"' in text


def test_look_package_is_gpl_and_methods_are_k3():
    gpl = REPO / "blender" / "satk_blender" / "look"
    for p in gpl.glob("*.py"):
        assert p.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: GPL-3.0-or-later"), p.name
    tree = ast.parse((gpl / "methods.py").read_text(encoding="utf-8"))
    names = [n.targets[0].id for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)]
    assert "METHODS" in names
    api = (gpl / "api.py").read_text(encoding="utf-8")
    for fn in ("def apply(", "def render_views(", "def sheet(", "def restore("):
        assert fn in api                                              # contract K5


def test_plan_file_and_is_file_spec(satk_home, tmp_path):
    from satk.blender import resolve

    import importlib.util

    spec = importlib.util.spec_from_file_location("_m3d", REPO / "tests" / "model3d" / "conftest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = tmp_path / "car"
    d.mkdir()
    (d / "Mycar.DFF").write_bytes(mod.M.car_dff())
    (d / "MYCAR.txd").write_bytes(mod.M.solid_txd({"paint": (1, 2, 3, 255)}))
    p = d / "Mycar.DFF"
    assert resolve.is_file_spec(str(p)) and not resolve.is_file_spec("model:426") and not resolve.is_file_spec("426")
    assert resolve.sibling_txd(p).name == "MYCAR.txd"
    r = resolve.plan_model(str(p))                       # plan_model accepts a DFF path
    m = r["model"]
    assert r["source"] == "file" and m["sec"] == "cars" and m["name"] == "mycar" and m["txd_names"] == ["mycar"]
    assert m["vehicle"]["colors"][0] == [42, 119, 161]
    assert Path(m["dff"]).read_bytes() == p.read_bytes()
    with pytest.raises(SatkError):
        resolve.plan_file(d / "nothing.dff")
