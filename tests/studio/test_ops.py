"""The studio operations: registration (CLI only, no MCP tool) and the answers without a running Blender."""

from __future__ import annotations

from satk.core.registry import all_ops, get_op


def test_ops_are_cli_only_in_the_blender_group():
    names = {o.name: o for o in all_ops()}
    for n in ("blender.session", "blender.call", "blender.methods"):
        o = names[n]
        assert o.mcp is False and o.mcp_name is None and o.group == "blender", n
        assert o.module == "satk.studio.ops"
    import inspect

    assert tuple(names["blender.session"].cli_path) == ("blender", "session")
    first = get_op("blender.call").params[0]
    assert first.name == "method" and first.default is inspect.Parameter.empty  # a CLI positional


def test_session_status_and_list_without_blender(satk_home, run_cli):
    r = run_cli(["blender", "session", "status"])
    assert r.code == 0 and r.json == {"ok": True, "name": "default", "up": False,
                                      "hint": "satk blender session start --name default"}
    r = run_cli(["blender", "session", "list"])
    assert r.code == 0 and r.json["rows"] == []
    r = run_cli(["blender", "session", "stop", "--name", "x"])
    assert r.json["note"] == "not running"
    r = run_cli(["blender", "session", "status", "--name", "Bad Name"])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS"


def test_call_on_a_missing_named_session(satk_home, run_cli):
    r = run_cli(["blender", "call", "scene.info", "--session", "car"])
    assert r.code == 3 and r.json["error"]["code"] == "NOT_READY"
    assert r.json["error"]["hint"] == "satk blender session start --name car"
    r = run_cli(["blender", "methods", "--session", "car"])
    assert r.json["error"]["code"] == "NOT_READY"
