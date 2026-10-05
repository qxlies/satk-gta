"""``satk mcp config`` (WP-06 acceptance 6): prints the .mcp.json entry; --write merges, --check compares."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from satk.mcp import adapter as A


def test_print_matches_adapter(satk_home, run_cli):
    env = run_cli(["mcp", "config"]).json
    assert env["status"] == "missing" and env["written"] is False
    assert env["mcpServers"] == A.mcp_json()["mcpServers"]
    entry = env["mcpServers"]["satk"]
    assert entry["env"]["SATK_HOME"] == str(satk_home).replace("\\", "/")
    # the checkout's .venv when it has one (any folder name), else the running interpreter
    co = A._checkout()
    venv_py = co / ".venv" / "Scripts" / "python.exe" if co is not None else None
    expected = venv_py if venv_py is not None and venv_py.is_file() else Path(sys.executable)
    assert entry["command"] == A.jpath(expected)
    assert not (satk_home / ".mcp.json").exists()  # printing never writes


def test_write_keeps_other_servers_and_check(satk_home, run_cli):
    f = satk_home / ".mcp.json"
    f.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "extra": 1}), encoding="utf-8")
    r = run_cli(["mcp", "config", "--check"])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"
    w = run_cli(["mcp", "config", "--write"]).json
    assert w["written"] is True and w["status"] == "ok"
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["mcpServers"]["other"] == {"command": "x"} and data["extra"] == 1
    assert data["mcpServers"]["satk"] == A.mcp_json()["mcpServers"]["satk"]
    assert run_cli(["mcp", "config", "--check"]).code == 0
    again = run_cli(["mcp", "config", "--write"]).json
    assert again["written"] is False  # already up to date
    data["mcpServers"]["satk"]["args"] = ["-m", "satk.mcp"]
    f.write_text(json.dumps(data), encoding="utf-8")
    r = run_cli(["mcp", "config", "--check"])
    assert r.code == 1 and r.json["error"]["code"] == "REVISION"


def test_broken_file_is_never_overwritten(satk_home, run_cli):
    """--write used to replace an unparsable file with only the satk entry (other servers lost)."""
    f = satk_home / ".mcp.json"
    f.write_text('{"mcpServers": {"other": {"command": "x"}},', encoding="utf-8")
    before = f.read_bytes()
    assert run_cli(["mcp", "config"]).json["error"]["code"] == "BAD_PARAMS"
    w = run_cli(["mcp", "config", "--write"])
    assert w.code == 2 and w.json["error"]["code"] == "BAD_PARAMS" and "not valid JSON" in w.json["error"]["msg"]
    assert f.read_bytes() == before


def test_bom_file_keeps_other_servers(satk_home, run_cli):
    """PowerShell 5.1 `Out-File -Encoding utf8` writes a BOM; Claude Code cannot parse such a file."""
    f = satk_home / ".mcp.json"
    f.write_bytes(b'\xef\xbb\xbf{"mcpServers":{"other":{"command":"x"}}}')
    r = run_cli(["mcp", "config", "--check"])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"  # parsed despite the BOM: entry missing
    w = run_cli(["mcp", "config", "--write"]).json
    assert w["written"] is True and w["status"] == "ok"
    raw = f.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    data = json.loads(raw.decode("utf-8"))
    assert data["mcpServers"]["other"] == {"command": "x"} and data["mcpServers"]["satk"]
    # the right entry, but a BOM again: status 'bom', --check fails, --write repairs it
    f.write_bytes(b"\xef\xbb\xbf" + raw)
    assert run_cli(["mcp", "config"]).json["status"] == "bom"
    assert run_cli(["mcp", "config", "--check"]).json["error"]["code"] == "REVISION"
    assert run_cli(["mcp", "config", "--write"]).json["written"] is True
    assert f.read_bytes() == raw


def test_raw_prints_the_pasteable_fragment(satk_home):
    import os
    import subprocess
    import sys

    from satk.core.config import SRC_ROOT

    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(SRC_ROOT))
    p = subprocess.run([sys.executable, "-X", "utf8", "-m", "satk", "mcp", "config", "--raw"], capture_output=True,
                       env=env, timeout=120, stdin=subprocess.DEVNULL)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == A.mcp_json()  # exactly {"mcpServers": {...}}, no envelope
    assert p.stdout.decode("utf-8").replace("\r\n", "\n").startswith('{\n  "mcpServers"')  # pretty, pasteable
