"""M2-01 acceptance over a real stdio session (``python -m satk.mcp``, official SDK client). Marked ``slow``.

``satk_ops`` finds operations without a tool of their own, ``satk_op("index status", {})`` and
``satk_op("formats dump", {...})`` work, a real long-running operation goes through the worker
subprocess, refused operations are refused, and tools/list stays within the 16 KB budget.
"""

from __future__ import annotations

import json
import os
import sys

import anyio
import pytest

from satk.core.config import REPO_ROOT, SRC_ROOT
from satk.mcp import adapter as A

pytestmark = pytest.mark.slow
pytest.importorskip("mcp")


def _env(result) -> dict:
    return json.loads(result.content[0].text)


def test_generic_tools_over_stdio(satk_home, tmp_path):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    ide = tmp_path / "zz.ide"
    ide.write_text("objs\n1700, zz_box, zz_tex, 100, 0\nend\n", encoding="utf-8")
    env = {"PYTHONUTF8": "1", "PYTHONPATH": str(SRC_ROOT), "SATK_HOME": os.environ["SATK_HOME"],
           "SATK_CONFIG": "none"}
    params = StdioServerParameters(command=sys.executable, args=["-X", "utf8", "-m", "satk.mcp"], env=env)
    progress: list[tuple] = []

    async def on_progress(p, total, message):
        progress.append((p, total, message))

    async def main():
        async with Client(params, mode="legacy") as c:
            out = {"tools": (await c.list_tools()).tools}
            out["gxt"] = await c.call_tool("satk_ops", {"query": "gxt"})
            out["dump?"] = await c.call_tool("satk_ops", {"query": "dump"})
            out["status"] = await c.call_tool("satk_op", {"op": "index status", "args": {}})
            out["dump"] = await c.call_tool("satk_op", {"op": "formats dump",
                                                        "args": {"target": str(ide), "level": "full"}})
            out["long"] = await c.call_tool(
                "satk_op", {"op": "dev.docs_smoke", "args": {"pages": [str(REPO_ROOT / "docs/ru/mcp.md")],
                                                            "dry_run": True}},
                progress_callback=on_progress)
            out["refused"] = await c.call_tool("satk_op", {"op": "dev gate"})
            return out

    out = anyio.run(main)
    names = [t.name for t in out["tools"]]
    assert names == [A.tool_name(o) for o in A.mcp_ops(None)] and {"satk_ops", "satk_op"} <= set(names)
    wire = [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in out["tools"]]
    assert A.list_bytes(wire) <= A.LIST_BUDGET

    gxt = _env(out["gxt"])
    assert not out["gxt"].is_error and gxt["ok"] is True  # a valid answer even when nothing mentions GXT
    dump_ops = _env(out["dump?"])
    assert dump_ops["rows"][0][0] == "formats.dump" and "target:str" in dump_ops["rows"][0][1]

    st = _env(out["status"])
    assert not out["status"].is_error and st["cols"][0] == "profile" and st["total"] >= 1
    dump = _env(out["dump"])
    assert not out["dump"].is_error and dump["kind"] == "ide" and dump["defs"] == 1
    assert dump["rows"][0][3] == "zz_box"

    long = _env(out["long"])
    assert not out["long"].is_error, long
    assert long["n"] >= 1 and {r[3] for r in long["rows"]} <= {"would-run", "skip"}

    refused = _env(out["refused"])
    assert out["refused"].is_error and refused["error"]["code"] == "UNSUPPORTED"
    log = (satk_home / "work" / "logs" / "mcp.log").read_text(encoding="utf-8")
    assert "call satk_op -> index.status ok" in log and "call satk_op -> formats.dump ok" in log
    assert "worker dev.docs_smoke exit 0" in log  # the long-running op ran in the worker subprocess
