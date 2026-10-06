"""Real stdio sessions against ``python -m satk.mcp`` (WP-06 acceptance 1, 7, 8). Marked ``slow``.

* ``satk mcp selftest``: raw JSON-RPC (initialize -> tools/list -> satk_help -> satk_status), tool set
  = registry, tools/list <= 16 KB, every stdout line is a JSON-RPC frame;
* the same with ``SATK_LOG=DEBUG`` (lots of logging): logs go to stderr and work/logs/mcp.log only;
* the official SDK stdio client talks to the server too.
"""

from __future__ import annotations

import satk
import json
import os
import subprocess
import sys
from pathlib import Path

import anyio
import pytest

from satk.core.config import SRC_ROOT
from satk.mcp import adapter as A

sys.path.append(str(Path(__file__).resolve().parents[1]))  # tests/: sandbox helper; appended, so it never shadows a conftest
import sandbox_compat  # noqa: E402

pytestmark = pytest.mark.slow
pytest.importorskip("mcp")


def test_selftest_all_tools(satk_home, run_cli):
    r = run_cli(["mcp", "selftest", "--json"])
    assert r.code == 0, r.out
    env = r.json
    assert env["ok"] is True and env["server"] == f"satk {satk.__version__}"
    assert env["tools"] == len(A.mcp_ops(None))
    assert env["list_bytes"] <= 16384 and env["stdout_frames"] >= 5
    assert env["groups"] == A.group_bytes(None)  # bytes per group as served
    assert sum(env["groups"].values()) < env["list_bytes"] and "warn" not in env


@pytest.mark.parametrize("groups", ["cor", "core,inex"])
def test_selftest_unknown_group_fails_loudly(satk_home, run_cli, groups):
    """SATK_MCP_GROUPS=cor used to pass with tools:0 and a warning."""
    r = run_cli(["mcp", "selftest", "--groups", groups, "--json"])
    assert r.code == 2, r.out
    err = r.json["error"]
    assert err["code"] == "BAD_PARAMS" and "SATK_MCP_GROUPS" in err["msg"] and err["did_you_mean"]


def test_server_refuses_unknown_group_without_touching_stdout(satk_home):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(SRC_ROOT), SATK_MCP_GROUPS="cor")
    p = subprocess.run([sys.executable, "-X", "utf8", "-m", "satk.mcp"], capture_output=True, env=env, timeout=120,
                       input=b"")
    assert p.returncode == 2
    assert p.stdout == b""  # stdout is the JSON-RPC stream: nothing else may appear there
    assert b"BAD_PARAMS" in p.stderr and b"SATK_MCP_GROUPS" in p.stderr
    log = (satk_home / "work" / "logs" / "mcp.log").read_text(encoding="utf-8")
    assert "satk mcp not started" in log


def test_selftest_core_group_and_debug_logging(satk_home, run_cli, monkeypatch):
    monkeypatch.setenv("SATK_LOG", "DEBUG")
    r = run_cli(["mcp", "selftest", "--groups", "core", "--json"])
    assert r.code == 0, r.out
    assert r.json["tools"] == 5  # satk_status, satk_help, satk_ops, satk_op, note
    log = (satk_home / "work" / "logs" / "mcp.log").read_text(encoding="utf-8")
    assert "call satk_help ok" in log and "5 tools (groups=core)" in log
    assert "call satk_op -> version ok" in log and "call satk_op -> dev.gate UNSUPPORTED" in log


def test_selftest_group_without_help_tools(satk_home, run_cli):
    r = run_cli(["mcp", "selftest", "--groups", "index", "--timeout", "60", "--json"])
    # satk_help/satk_status are not in 'index': those calls are skipped, the tool set must still match
    assert r.code == 0 and r.json["tools"] == len(A.mcp_ops({"index"}))


def test_selftest_detects_stdout_pollution(satk_home, run_cli, monkeypatch, tmp_path):
    """A module printing to stdout at interpreter start (before the transport claims fd 1) is caught."""
    (tmp_path / "sitecustomize.py").write_text("print('POLLUTION before MCP')\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    r = run_cli(["mcp", "selftest", "--groups", "core", "--json"])
    assert r.code == 1
    err = r.json["error"]
    failed = [c["name"] for c in err["data"]["checks"] if not c["ok"]]
    assert failed == ["stdout_only_jsonrpc"]
    assert "POLLUTION" in json.dumps(err["data"]["checks"], ensure_ascii=False)


@pytest.mark.parametrize("protocol", ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"])
def test_handshake_versions(satk_home, protocol):
    from satk.mcp.selftest import run

    res = run(groups="core", timeout=90, protocol=protocol)
    assert res["protocol"] == protocol and res["tools"] == 5


def test_official_sdk_stdio_client(satk_home):
    sandbox_compat.skip_unless_async_subprocess()
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    env = {"PYTHONUTF8": "1", "PYTHONPATH": str(SRC_ROOT), "SATK_HOME": os.environ["SATK_HOME"],
           "SATK_CONFIG": "none"}
    params = StdioServerParameters(command=sys.executable, args=["-X", "utf8", "-m", "satk.mcp"], env=env)

    async def main():
        async with Client(params, mode="legacy") as c:
            names = [t.name for t in (await c.list_tools()).tools]
            res = await c.call_tool("note", {"action": "add", "id": "model:411", "text": "Красная спортивная машина"})
            return names, res

    names, res = anyio.run(main)
    assert names == [A.tool_name(o) for o in A.mcp_ops(None)]
    assert not res.is_error and '"sid":"model:411"' in res.content[0].text


def test_python_m_satk_mcp_with_args_is_the_cli(satk_home):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(SRC_ROOT))
    p = subprocess.run([sys.executable, "-X", "utf8", "-m", "satk.mcp", "config", "--json"], capture_output=True,
                       env=env, timeout=120, stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stderr
    env_out = json.loads(p.stdout)
    assert env_out["mcpServers"]["satk"]["args"] == ["-X", "utf8", "-m", "satk.mcp"]
