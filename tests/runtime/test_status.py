"""``satk status`` / MCP ``satk_status`` (SPEC §4.7 #2)."""

from __future__ import annotations

import satk
import time

from satk.core import registry as R
from satk.core.errors import SatkError
from satk.runtime import status as S


def test_status_basic_sections_without_providers(satk_home, run_cli):
    t0 = time.perf_counter()
    r = run_cli(["status", "--json"])
    assert r.code == 0
    env = r.json
    assert env["satk"]["version"] == satk.__version__ and env["satk"]["workspace"].endswith("/ws")
    for sec in ("game", "index", "viewer", "re", "blender", "engine", "notes"):
        assert sec in env, sec
    # game/index providers are merged (WP-01/WP-03) and report state only; without a copy/index the
    # top-level warn list must still say so (the §4.7 contract; the agent's first call)
    codes = [w.split(":")[0] for w in env["warn"]]
    assert "GAME_MISSING" in codes and "INDEX_MISSING" in codes, env["warn"]
    assert any("satk index build" in w for w in env["warn"]) and any("satk game clone" in w for w in env["warn"])
    assert env["notes"]["exists"] is False
    assert time.perf_counter() - t0 < 5


def test_derived_warnings_from_provider_state(isolated_ops):
    @R.status_provider("game")
    def _game(deep):
        return {"root": "D:/x/gta-sa-clean", "ok": False, "exe": "unknown", "files": 416}

    @R.status_provider("index")
    def _index(deep):
        return {"vanilla": {"built": True, "fresh": False, "stale": ["data/gta.dat changed"]}}

    env = S.collect()
    assert "GAME_BAD: clean copy at D:/x/gta-sa-clean has problems (exe unknown); fix: satk game verify --deep" \
        in env["warn"]
    assert "INDEX_STALE: profile vanilla (data/gta.dat changed); fix: satk index build" in env["warn"]


def test_provider_warning_replaces_the_derived_one(isolated_ops):
    @R.status_provider("index")
    def _index(deep):
        return {"vanilla": {"built": False}, "warn": ["INDEX_MISSING: build it"]}

    @R.status_provider("game")
    def _game(deep):
        return {"root": "D:/g", "ok": True, "exe": "hoodlum-stock"}

    env = S.collect()
    assert env["warn"][0] == "index: INDEX_MISSING: build it"
    assert not any(w.startswith(("GAME_", "INDEX_MISSING: profile")) for w in env["warn"])


def test_providers_win_and_failures_are_isolated(isolated_ops, satk_home):  # errors.log -> tmp work
    @R.status_provider("index")
    def _index(deep):
        return {"vanilla": {"built": True, "fresh": deep}, "warn": ["INDEX_STALE: data/gta.dat changed"]}

    @R.status_provider("viewer")
    def _viewer(deep):
        raise SatkError("NOT_READY", "viewer not running", hint="satk view start")

    @R.status_provider("re")
    def _re(deep):
        raise RuntimeError("boom")

    @R.status_provider("custom")
    def _custom(deep):
        return {"x": 1, "empty": [], "none": None}

    env = S.collect(deep=True)
    assert env["index"] == {"vanilla": {"built": True, "fresh": True}}
    assert env["viewer"]["error"]["code"] == "NOT_READY"
    assert env["re"]["error"]["code"] == "INTERNAL"
    assert env["custom"] == {"x": 1}
    assert env["game"]["basic"] is True  # no provider registered in this isolated registry
    assert "index: INDEX_STALE: data/gta.dat changed" in env["warn"]
    assert any(w.startswith("viewer: NOT_READY") for w in env["warn"])
    assert any(w.startswith("re: INTERNAL") for w in env["warn"])
    assert list(env)[:4] == ["satk", "game", "index", "viewer"]  # stable section order


def test_sections_filter(isolated_ops):
    env = S.collect(sections=["game"])
    assert set(env) - {"warn"} == {"satk", "game"}
    try:
        S.collect(sections=["nope"])
    except SatkError as e:
        assert e.code == "BAD_PARAMS"
    else:  # pragma: no cover
        raise AssertionError("expected BAD_PARAMS")


def test_basic_index_lists_built_profiles(satk_home):
    d = satk_home / "work" / "index"
    d.mkdir(parents=True)
    (d / "vanilla.sqlite").write_bytes(b"\0" * 2048)
    sec, warn = S.basic_section("index")
    assert sec["vanilla"]["built"] is True and sec["basic"] is True and warn == []


def test_mcp_tool_status(satk_home):
    env = R.invoke(R.op_by_mcp("satk_status"), {})
    assert env["ok"] is True and "satk" in env
