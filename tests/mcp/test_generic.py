"""Generic MCP access (M2-01): ``satk_ops`` search, ``satk_op`` resolution/policy/validation/dispatch.

Unit level (registry), the CLI twins ``satk mcp ops`` / ``satk mcp op`` and the in-process server
(SDK client): ordinary targets run in a thread, long-running ones in the worker subprocess with
progress, refused ones never run, ``SATK_MCP_GROUPS`` limits what ``satk_op`` reaches.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import anyio
import pytest

from satk.core import registry as R
from satk.core.errors import SatkError
from satk.mcp import adapter as A
from satk.mcp import generic as G

FIXTURE = Path(__file__).with_name("fixture_ops.py")
#: The generic operations of the real registry (re-registered inside isolated registries).
REAL_GENERIC = [R.get_op(G.FIND_OP), R.get_op(G.RUN_OP)]


def _load_fixture_ops():
    spec = importlib.util.spec_from_file_location("satk_test_fixture_ops", FIXTURE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _with_generic():
    for spec in REAL_GENERIC:
        R._register(spec)  # noqa: SLF001 - test registry setup


# --------------------------------------------------------------------------- policy


def test_every_denied_name_is_a_real_operation():
    """A renamed operation must not silently become callable: DENY names exist in the registry."""
    names = {o.name for o in R.all_ops()}
    assert set(G.DENY) <= names, set(G.DENY) - names


@pytest.mark.parametrize("name,code", [
    ("mcp", "UNSUPPORTED"), ("mcp.config", "UNSUPPORTED"), ("mcp.selftest", "UNSUPPORTED"),
    ("mcp.op", "UNSUPPORTED"), ("mcp.ops", "UNSUPPORTED"), ("dev.gate", "UNSUPPORTED"),
    ("game.clone", "CONSENT_REQUIRED"), ("game.protect", "CONSENT_REQUIRED"), ("init", "CONSENT_REQUIRED"),
    ("view.mock", "UNSUPPORTED"),
])
def test_denied_operations(name, code):
    assert G.denial(R.get_op(name))[0] == code
    with pytest.raises(SatkError) as e:
        G.prepare(name, {})
    assert e.value.code == code and "not callable through satk_op" in e.value.msg
    assert f"satk {R.get_op(name).cli}" in e.value.hint


@pytest.mark.parametrize("name", ["index.status", "formats.dump", "game.info", "game.verify", "version",
                                  "asset.find", "dev.linkcheck", "note.list", "re.build", "index.build"])
def test_allowed_operations(name):
    assert G.denial(R.get_op(name)) is None


def test_unknown_mcp_operations_are_server_management(isolated_ops):
    @R.op("mcp.zz_new", summary="x", summary_ru="x", mcp=False)
    def _x() -> dict:
        return {}

    assert G.denial(R.get_op("mcp.zz_new")) == ("UNSUPPORTED", "MCP server management")


def test_cli_only_decorator(isolated_ops):
    @G.cli_only("rewrites the user's thing", consent=True)
    @R.op("zz.reset", summary="Reset.", summary_ru="Сброс.", mcp=False)
    def _reset() -> dict:
        return {}

    @G.cli_only("blocks")
    @R.op("zz.serve", summary="Serve.", summary_ru="Сервер.", mcp=False)
    def _serve() -> dict:
        return {}

    assert G.denial(R.get_op("zz.reset")) == ("CONSENT_REQUIRED", "rewrites the user's thing")
    assert G.denial(R.get_op("zz.serve")) == ("UNSUPPORTED", "blocks")
    with pytest.raises(TypeError):
        G.cli_only("x")(lambda: {})  # not an @op
    assert G.callable_ops(None) == []


def test_groups_limit_satk_op():
    with pytest.raises(SatkError) as e:
        G.prepare("index.status", {}, groups={"core"})
    assert e.value.code == "UNSUPPORTED" and "SATK_MCP_GROUPS" in e.value.msg
    assert G.prepare("index.status", {}, groups={"core", "index"})[0].name == "index.status"
    assert all(o.mcp_group == "core" for o in G.callable_ops({"core"}))


def test_current_groups_env_and_serving(monkeypatch):
    monkeypatch.setenv("SATK_MCP_GROUPS", "core,index,bogus")  # lenient outside the server
    assert G.current_groups() == {"core", "index"}
    with G.serving({"view"}):
        assert G.current_groups() == {"view"}
        with G.serving(None):
            assert G.current_groups() is None
    monkeypatch.delenv("SATK_MCP_GROUPS")
    assert G.current_groups() is None


# --------------------------------------------------------------------------- resolve / validate


@pytest.mark.parametrize("text,name", [
    ("formats.dump", "formats.dump"), ("formats dump", "formats.dump"), ("satk formats dump", "formats.dump"),
    ("  Formats   Dump ", "formats.dump"), ("formats_dump", "formats.dump"), ("asset_find", "asset.find"),
    ("asset find", "asset.find"), ("dev guard-test", "dev.guard_test"), ("dev.guard-test", "dev.guard_test"),
    ("satk_status", "status"), ("note", "note"), ("engine", "engine.run"), ("satk_op", "mcp.op"),
])
def test_resolve_spellings(text, name):
    assert G.resolve(text).name == name


def test_resolve_unknown_suggests():
    with pytest.raises(SatkError) as e:
        G.resolve("formats dumb")
    assert e.value.code == "NOT_FOUND" and "formats.dump" in e.value.did_you_mean
    assert "satk_ops(" in e.value.hint
    with pytest.raises(SatkError) as e:
        G.resolve("  ")
    assert e.value.code == "BAD_PARAMS"


def test_prepare_validates_like_the_cli():
    spec, args = G.prepare("formats dump", {"target": "data/gta.dat", "level": "full"})
    assert spec.name == "formats.dump" and args == {"target": "data/gta.dat", "level": "full"}
    with pytest.raises(SatkError) as e:
        G.prepare("formats.dump", {"target": "x", "levl": "full"})
    assert e.value.code == "BAD_PARAMS" and e.value.did_you_mean == ["level"]
    with pytest.raises(SatkError) as e:
        G.prepare("formats.dump", {"level": "full"})
    assert e.value.code == "BAD_PARAMS" and "target" in e.value.msg
    with pytest.raises(SatkError) as e:
        G.prepare("formats.dump", {"target": "x", "level": "fulll"})
    assert e.value.code == "BAD_PARAMS" and "full" in e.value.did_you_mean
    with pytest.raises(SatkError) as e:
        G.prepare("version", ["not", "a", "dict"])  # type: ignore[arg-type]
    assert e.value.code == "BAD_PARAMS" and "JSON object" in e.value.msg
    assert G.prepare("version", None) == (R.get_op("version"), {})


# --------------------------------------------------------------------------- search


def test_args_signature():
    assert G.args_signature(R.get_op("formats.dump")) == (
        'target:str, level:stats|full|tree="stats", limit:int=20, profile:vanilla|installed|samp="vanilla"')
    assert G.args_signature(R.get_op("re.addr")) == "text?:[str], text_file?:str, limit:int=50"
    assert G.args_signature(R.get_op("version")) == ""
    for o in R.all_ops():  # every operation has a signature and a JSON-serializable schema
        G.args_signature(o)
        json.dumps(o.input_schema())


def test_search_exact_name_has_schema():
    env = G.search("index status")
    assert env["ok"] and env["cols"] == ["op", "args", "summary"]
    assert env["rows"][0][0] == "index.status" and env["rows"][0][1] == "deep:bool=false"
    assert env["schema"] == R.get_op("index.status").input_schema()
    assert env["examples"] == ["satk index status"]
    assert 'satk_op(op="index.status"' in env["hint"]


def test_search_words_and_ranking():
    env = G.search("dump")
    assert env["rows"][0][0] == "formats.dump"
    env = G.search("IMG entries list")
    assert "formats.ls" in [r[0] for r in env["rows"]]
    env = G.search("модели")  # Russian summaries are searched too
    assert env["total"] >= 1


def test_search_marks_tools_and_long_running():
    rows = {r[0]: r[2] for r in G.search(None, limit=500)["rows"]}
    assert rows["asset.find"].endswith("(tool asset_find)")
    assert rows["index.build"].endswith("(long-running)")
    assert "dev.gate" not in rows and "mcp.op" not in rows and "game.clone" not in rows


def test_search_without_query_lists_all_callable():
    env = G.search(None, limit=500)
    assert env["total"] == len(G.callable_ops(None)) == env["n"]
    assert [r[0] for r in env["rows"]] == sorted(r[0] for r in env["rows"])
    small = G.search(None, limit=3)
    assert small["n"] == 3 and small["total"] == env["total"]
    punct = G.search(" ?? ", limit=500)  # no words: same as no query, nothing listed as refused
    assert punct["total"] == env["total"] and "cli_only" not in punct
    with pytest.raises(SatkError):
        G.search(None, limit=0)


def test_search_partial_match_warns():
    env = G.search("index zzqqxx")
    assert env["rows"] and env["warn"][0].startswith("NO_FULL_MATCH")


def test_search_names_refused_matches():
    env = G.search("dev gate")
    assert all(r[0] != "dev.gate" for r in env["rows"])
    assert any(x.startswith("dev.gate: ") for x in env["cli_only"])
    env = G.search("clone")
    assert env["cli_only"] == ["game.clone: it writes the clean game copy (gigabytes)"]
    assert all(r[0] != "game.clone" for r in env["rows"])
    mock = ["view.mock: it serves until interrupted and would block the call"]
    env = G.search("synthetic")  # nothing callable matches: refused matches by any text
    assert not env["rows"] and env["cli_only"] == mock
    env = G.search("synthetic world")  # callable ones match only partly
    assert env["rows"] and env["warn"][0].startswith("NO_FULL_MATCH") and env["cli_only"] == mock
    nothing = G.search("zzqqxx")
    assert nothing["total"] == 0 and "cli_only" not in nothing and "satk_ops()" in nothing["hint"]


def test_search_respects_groups():
    ops = {r[0] for r in G.search(None, limit=500, groups={"core"})["rows"]}
    assert "version" in ops and "index.status" not in ops
    env = G.search("index status", groups={"core"})
    assert env["rows"][0][0] != "index.status"
    assert any(x.startswith("index.status: MCP group") for x in env.get("cli_only", []))


# --------------------------------------------------------------------------- CLI twins


def test_cli_mcp_ops_and_op(satk_home, run_cli):
    r = run_cli(["mcp", "ops", "index status", "--json"])
    assert r.code == 0 and r.json["rows"][0][0] == "index.status"
    r = run_cli(["mcp", "op", "version", "--json"])
    assert r.code == 0 and r.json["satk"]
    r = run_cli(["mcp", "op", "index status", "--json"])
    assert r.code == 0 and r.json["cols"][0] == "profile"
    r = run_cli(["mcp", "op", "dev.gate", "--json"])
    assert r.code == 1 and r.json["error"]["code"] == "UNSUPPORTED"
    r = run_cli(["mcp", "op", "formats.dump", "--args", '{"targt": "x"}', "--json"])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS" and r.json["error"]["did_you_mean"] == ["target"]


def test_cli_formats_dump_through_mcp_op(satk_home, run_cli, tmp_path):
    ide = tmp_path / "zz.ide"
    ide.write_text("objs\n1700, zz_box, zz_tex, 100, 0\nend\n", encoding="utf-8")
    r = run_cli(["mcp", "op", "formats dump", "--args", json.dumps({"target": str(ide), "level": "full"}), "--json"])
    assert r.code == 0, r.out
    assert r.json["kind"] == "ide" and r.json["defs"] == 1 and r.json["rows"][0][3] == "zz_box"


# --------------------------------------------------------------------------- in-process server


@pytest.fixture
def sdk():
    """The MCP SDK (the in-process server needs it)."""
    return pytest.importorskip("mcp")


def _env(result) -> dict:
    return json.loads(result.content[0].text)


def _run(app, fn):
    from mcp import Client

    async def main():
        async with Client(app.server, mode="legacy") as client:
            return await fn(client)

    return anyio.run(main)


def test_server_generic_tools(satk_home, tmp_path, sdk):
    from satk.mcp.server import SatkMcp

    ide = tmp_path / "zz.ide"
    ide.write_text("objs\n1700, zz_box, zz_tex, 100, 0\nend\n", encoding="utf-8")
    app = SatkMcp(groups=None)

    async def body(c):
        names = [t.name for t in (await c.list_tools()).tools]
        assert names[:4] == ["satk_status", "satk_help", "satk_ops", "satk_op"]
        found = await c.call_tool("satk_ops", {"query": "dump"})
        assert not found.is_error and _env(found)["rows"][0][0] == "formats.dump"
        st = await c.call_tool("satk_op", {"op": "index status", "args": {}})
        assert not st.is_error and _env(st)["cols"][0] == "profile"
        dump = await c.call_tool("satk_op", {"op": "formats.dump", "args": {"target": str(ide)}})
        assert not dump.is_error and _env(dump)["kind"] == "ide"
        no_args = await c.call_tool("satk_op", {"op": "version"})
        assert not no_args.is_error and _env(no_args)["satk"]
        refused = await c.call_tool("satk_op", {"op": "dev gate", "args": {"quick": True}})
        assert refused.is_error and _env(refused)["error"]["code"] == "UNSUPPORTED"
        consent = await c.call_tool("satk_op", {"op": "game.clone", "args": {}})
        assert consent.is_error and _env(consent)["error"]["code"] == "CONSENT_REQUIRED"
        bad = await c.call_tool("satk_op", {"op": "formats.dump", "args": {"target": "x", "lvl": 1}})
        assert bad.is_error and _env(bad)["error"]["code"] == "BAD_PARAMS"
        unknown = await c.call_tool("satk_op", {"op": "formats dumb"})
        assert unknown.is_error and _env(unknown)["error"]["code"] == "NOT_FOUND"
        own_schema = await c.call_tool("satk_op", {"opp": "version"})
        assert own_schema.is_error and _env(own_schema)["error"]["code"] == "BAD_PARAMS"
        via_tool = await c.call_tool("satk_op", {"op": "satk_help", "args": {"topic": "ops"}})
        assert not via_tool.is_error and _env(via_tool)["topic"] == "ops"

    _run(app, body)


def test_server_satk_ops_and_op_follow_server_groups(satk_home, sdk):
    from satk.mcp.server import SatkMcp

    app = SatkMcp(groups={"core"})

    async def body(c):
        found = await c.call_tool("satk_ops", {"limit": 500})
        ops = [r[0] for r in _env(found)["rows"]]
        assert "version" in ops and "index.status" not in ops and "formats.dump" not in ops
        r = await c.call_tool("satk_op", {"op": "index.status"})
        assert r.is_error and _env(r)["error"]["code"] == "UNSUPPORTED" and "SATK_MCP_GROUPS" in _env(r)["error"]["msg"]

    _run(app, body)


def test_server_dispatch_thread_and_worker(isolated_ops, satk_home, sdk):
    """Ordinary target: this process (thread). long_running target: the worker subprocess, with progress."""
    from satk.mcp.server import SatkMcp

    _load_fixture_ops()
    _with_generic()
    app = SatkMcp(groups=None)
    seen: list[tuple] = []

    async def on_progress(progress, total, message):
        seen.append((progress, total, message))

    async def body(c):
        names = [t.name for t in (await c.list_tools()).tools]
        assert "satk_op" in names and "zz_cli_steps" not in names  # CLI-only fixture ops have no own tool
        quick = await c.call_tool("satk_op", {"op": "zz cli-quick", "args": {"word": "hi", "times": 2}})
        assert not quick.is_error and _env(quick) == {"ok": True, "echo": "hi hi", "pid": os.getpid()}
        long = await c.call_tool("satk_op", {"op": "zz.cli_steps", "args": {"n": 3, "label": "машина"}},
                                 progress_callback=on_progress)
        env = _env(long)
        assert not long.is_error and env["steps"] == 3 and env["label"] == "машина"
        assert env["pid"] != os.getpid()  # ran in the worker subprocess
        bad = await c.call_tool("satk_op", {"op": "zz.cli_steps", "args": {"n": "x"}})
        assert bad.is_error and _env(bad)["error"]["code"] == "BAD_PARAMS"  # refused before any process starts
        ex = await c.call_tool("satk_op", {"op": "zz.cli_exit", "args": {"code": 4}})
        assert ex.is_error and _env(ex)["error"]["code"] == "INTERNAL" and "exit" in _env(ex)["error"]["msg"]
        alive = await c.call_tool("satk_op", {"op": "zz.cli_quick", "args": {"word": "still"}})
        assert not alive.is_error  # the SystemExit did not end the server
        ops = await c.call_tool("satk_ops", {"query": "cli-only"})
        assert {r[0] for r in _env(ops)["rows"]} >= {"zz.cli_steps", "zz.cli_quick"}

    _run(app, body)
    assert [s[0] for s in seen] == [1, 2, 3] and seen[-1][2] == "cli step 3"


def test_server_generic_long_running_timeout(isolated_ops, satk_home, sdk):
    from satk.mcp.server import SatkMcp

    _load_fixture_ops()
    _with_generic()
    app = SatkMcp(groups=None, long_timeout=0.05)

    async def body(c):
        return await c.call_tool("satk_op", {"op": "zz.cli_steps", "args": {"n": 1}})

    r = _run(app, body)
    err = _env(r)["error"]
    assert r.is_error and err["code"] == "TIMEOUT" and err["msg"].startswith("zz.cli_steps did not finish")


def test_server_generic_inline_images(isolated_ops, tmp_path, sdk):
    pytest.importorskip("PIL")
    from PIL import Image

    from satk.mcp.server import SatkMcp

    _load_fixture_ops()
    _with_generic()
    png = tmp_path / "p.png"
    Image.new("RGB", (64, 32), (1, 2, 3)).save(png)
    app = SatkMcp(groups=None)

    async def body(c):
        plain = await c.call_tool("satk_op", {"op": "zz.picture", "args": {"path": str(png)}})
        rich = await c.call_tool("satk_op", {"op": "zz.picture", "args": {"path": str(png), "inline": True}})
        return plain, rich

    plain, rich = _run(app, body)
    assert len(plain.content) == 1 and len(rich.content) == 2 and rich.content[1].type == "image"


def test_help_topics_for_generic_access():
    from satk.runtime import help as H

    text = H.render("ops")["text"]
    assert "satk_ops(query" in text and "satk_op(op, args)" in text and "- dev.gate: UNSUPPORTED" in text
    assert "formats dump" in text and "Reachable through satk_op now" in text
    assert "satk_ops(query)" in H.render("start")["text"]
    tools = H.render("tools")["text"]
    assert "## Via satk_op (" in tools and "## CLI only (" in tools
    assert H.render("satk_op")["op"] == "mcp.op" and H.render("satk_ops")["mcp"] == "satk_ops"
    assert 'MCP: satk_op(op="formats.dump"' in H.render("formats.dump")["text"]
    assert "CLI only (UNSUPPORTED" in H.render("dev gate")["text"]
    assert "Long-running" not in H.render("dev gate")["text"]
