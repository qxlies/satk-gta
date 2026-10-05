"""The MCP server in-process through the SDK client (mcp==2.3.0, legacy initialize handshake).

Covers: tools/list = registry, calls, errors as ``isError`` envelopes, ``SATK_MCP_GROUPS``, inline
images (<= 1024 px), in-thread timeouts, long-running ops in a subprocess with progress.
"""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import signal
import time
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402

from satk.core.errors import SatkError  # noqa: E402
from satk.mcp import adapter as A  # noqa: E402
from satk.mcp.proctree import pid_alive  # noqa: E402
from satk.mcp.server import SatkMcp  # noqa: E402

FIXTURE = Path(__file__).with_name("fixture_ops.py")


def _load_fixture_ops():
    spec = importlib.util.spec_from_file_location("satk_test_fixture_ops", FIXTURE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _env(result) -> dict:
    return json.loads(result.content[0].text)


def _run(app: SatkMcp, fn):
    async def main():
        async with Client(app.server, mode="legacy") as client:
            return await fn(client)

    return anyio.run(main)


def test_list_and_call_real_tools(satk_home):
    app = SatkMcp(groups=None)

    async def body(c):
        tools = (await c.list_tools()).tools
        names = [t.name for t in tools]
        assert names == [A.tool_name(o) for o in A.mcp_ops(None)]
        assert all(t.input_schema == A.tool_def(app.ops[t.name])["inputSchema"] for t in tools)
        help_ = await c.call_tool("satk_help", {"topic": "ids"})
        assert not help_.is_error and _env(help_)["topic"] == "ids"
        status = await c.call_tool("satk_status", {})
        assert not status.is_error and "satk" in _env(status)
        added = await c.call_tool("note", {"action": "add", "id": "model:411", "text": "Красная спортивная машина",
                                           "tags": ["машина"]})
        assert not added.is_error and _env(added)["note"].startswith("note:")
        assert "машина" in added.content[0].text  # UTF-8, not \\u escapes
        listed = await c.call_tool("note", {"action": "list", "query": "машин"})
        assert _env(listed)["rows"][0][1] == "model:411"
        bad = await c.call_tool("note", {"action": "bogus"})
        assert bad.is_error and _env(bad)["error"]["code"] == "BAD_PARAMS"
        unknown = await c.call_tool("nope_tool", {})
        assert unknown.is_error and _env(unknown)["error"]["code"] == "UNKNOWN_METHOD"
        extra = await c.call_tool("satk_status", {"deep": False, "zzz": 1})
        assert extra.is_error and _env(extra)["error"]["code"] == "BAD_PARAMS"

    _run(app, body)


def test_initialize_reports_instructions():
    app = SatkMcp(groups=None)

    async def body(c):
        return c.initialize_result if hasattr(c, "initialize_result") else None

    res = _run(app, body)
    if res is not None:  # attribute name differs between SDK minors; the stdio selftest checks it too
        assert res.instructions == A.INSTRUCTIONS
    opts = app.server.create_initialization_options()
    assert opts.instructions == A.INSTRUCTIONS and opts.server_name == "satk"


def test_groups_core():
    app = SatkMcp(groups={"core"})
    assert [t.name for t in app.tools] == ["satk_status", "satk_help", "satk_ops", "satk_op", "note"]


def test_groups_from_env(monkeypatch):
    monkeypatch.setenv("SATK_MCP_GROUPS", "core")
    app = SatkMcp()
    assert app.groups == {"core"} and len(app.tools) == 5


@pytest.mark.parametrize("raw", ["cor", "core,unknownthing", "all,bogus"])
def test_unknown_group_is_an_error_not_an_empty_server(monkeypatch, raw):
    """A typo in SATK_MCP_GROUPS used to give a server with zero (or fewer) tools and only a log line."""
    monkeypatch.setenv("SATK_MCP_GROUPS", raw)
    with pytest.raises(SatkError) as e:
        SatkMcp()
    assert e.value.code == "BAD_PARAMS" and "SATK_MCP_GROUPS" in e.value.msg


def _big_png(path: Path, w: int, h: int) -> Path:
    from PIL import Image

    Image.new("RGB", (w, h), (200, 30, 30)).save(path)
    return path


def test_inline_images_only_on_request(isolated_ops, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    _load_fixture_ops()
    png = _big_png(tmp_path / "big.png", 2048, 512)
    app = SatkMcp(groups=None)

    async def body(c):
        plain = await c.call_tool("zz_picture", {"path": str(png)})
        assert len(plain.content) == 1
        rich = await c.call_tool("zz_picture", {"path": str(png), "inline": True})
        assert len(rich.content) == 2 and rich.content[1].type == "image"
        img = rich.content[1]
        assert img.mime_type == "image/png"
        with Image.open(io.BytesIO(base64.b64decode(img.data))) as im:
            assert im.size == (1024, 256)
        missing = await c.call_tool("zz_picture", {"path": str(tmp_path / "none.png"), "inline": True})
        assert len(missing.content) == 1  # nothing to embed, JSON still there

    _run(app, body)


def test_in_thread_timeout(isolated_ops):
    _load_fixture_ops()
    app = SatkMcp(groups=None, call_timeout=0.2)

    async def body(c):
        started = time.perf_counter()
        r = await c.call_tool("zz_sleep", {"seconds": 2})
        elapsed = time.perf_counter() - started
        assert r.is_error and _env(r)["error"]["code"] == "TIMEOUT"
        assert 0.15 <= elapsed < 1, f"timeout response took {elapsed:.3f} s"
        ok = await c.call_tool("zz_sleep", {"seconds": 0.01})
        assert not ok.is_error and _env(ok)["slept"] == 0.01

    _run(app, body)


def test_long_running_runs_in_subprocess_with_progress(isolated_ops, satk_home):
    _load_fixture_ops()
    app = SatkMcp(groups=None)
    seen: list[tuple] = []

    async def on_progress(progress, total, message):
        seen.append((progress, total, message))

    async def body(c):
        r = await c.call_tool("zz_slow_steps", {"n": 3}, progress_callback=on_progress)
        env = _env(r)
        assert not r.is_error and env["steps"] == 3 and env["pid"] != os.getpid()
        f = await c.call_tool("zz_slow_steps", {"n": 1, "fail": True})
        assert f.is_error and _env(f)["error"]["code"] == "INTERNAL" and "worker op failed" in _env(f)["error"]["msg"]
        bad = await c.call_tool("zz_slow_steps", {"n": "x"})
        assert bad.is_error and _env(bad)["error"]["code"] == "BAD_PARAMS"

    _run(app, body)
    assert [s[0] for s in seen] == [1, 2, 3] and seen[-1][2] == "step 3"


def test_long_running_timeout_kills_worker(isolated_ops, satk_home):
    _load_fixture_ops()
    app = SatkMcp(groups=None, long_timeout=0.05)

    async def body(c):
        return await c.call_tool("zz_slow_steps", {"n": 1})

    r = _run(app, body)
    assert r.is_error and _env(r)["error"]["code"] == "TIMEOUT"


def _child_pid(pid_file: Path, timeout: float = 30.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pid_file.is_file() and pid_file.read_text(encoding="utf-8").strip():
            return int(pid_file.read_text(encoding="utf-8"))
        time.sleep(0.05)
    raise AssertionError(f"the child never wrote {pid_file}")


def _gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return False


def _kill_quietly(pid: int) -> None:
    if pid_alive(pid):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)


def test_long_running_timeout_kills_the_whole_process_tree(isolated_ops, satk_home, tmp_path):
    """server -> venv launcher -> worker -> launcher -> sleeping child: a timeout leaves nothing running.

    This is the blender_job case (Blender is a grandchild of the worker): killing only the
    process the server started used to leave it running and writing.
    """
    _load_fixture_ops()
    pid_file = tmp_path / "child.pid"
    app = SatkMcp(groups=None, long_timeout=8.0)
    seen: list[tuple] = []

    async def on_progress(progress, total, message):
        seen.append((progress, total, message))

    async def body(c):
        return await c.call_tool("zz_spawn_and_wait", {"pid_file": str(pid_file), "seconds": 120},
                                 progress_callback=on_progress)

    r = _run(app, body)
    pid = _child_pid(pid_file, timeout=1)
    try:
        assert r.is_error and _env(r)["error"]["code"] == "TIMEOUT"
        assert seen and seen[0][2] == "child started"  # the child really ran before the timeout
        assert _gone(pid), f"grandchild {pid} still runs after the TIMEOUT"
    finally:
        _kill_quietly(pid)


def test_long_running_normal_end_leaves_helpers_alone(isolated_ops, satk_home, tmp_path):
    """Only a timeout/cancel kills the tree; a helper an operation leaves on purpose survives."""
    _load_fixture_ops()
    pid_file = tmp_path / "helper.pid"
    app = SatkMcp(groups=None)

    async def body(c):
        return await c.call_tool("zz_spawn_and_leave", {"pid_file": str(pid_file), "seconds": 30})

    r = _run(app, body)
    pid = _child_pid(pid_file, timeout=1)
    try:
        assert not r.is_error, r.content[0].text
        time.sleep(0.5)
        assert pid_alive(pid)
    finally:
        _kill_quietly(pid)
