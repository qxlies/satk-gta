"""``satk mcp config --client`` (M3 A4): reference fragments for every client, safe writes, scopes, groups.

Every test runs in an isolated workspace with an isolated home (``USERPROFILE``/``HOME``/``APPDATA``/
``CODEX_HOME``/``CLAUDE_CONFIG_DIR`` under tmp): nothing touches the real AI client configs.
Reference fragments: ``tests/mcp/golden/clients/<client>.<ext>`` and ``commands.txt`` (regenerate with
``UPDATE_GOLDEN_CLIENTS=1``; ``<ROOT>`` stands for the temp checkout/workspace root).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tomllib
import typing
import urllib.parse
from pathlib import Path

import pytest

from satk.core import config as _config
from satk.core.paths import jpath
from satk.mcp import clients as C
from satk.mcp import ops as mcp_ops

GOLDEN = Path(__file__).parent / "golden" / "clients"
_EXT = {"json": ".json", "toml": ".toml", "yaml": ".yaml"}


@pytest.fixture
def home(satk_home, tmp_path, monkeypatch):
    """Isolated home and app-data dirs; Blender/viewer/engine absent unless a test creates them."""
    h = tmp_path / "home"
    (h / "AppData" / "Roaming").mkdir(parents=True)
    (h / "AppData" / "Local").mkdir(parents=True)
    for var, val in (("USERPROFILE", h), ("HOME", h), ("APPDATA", h / "AppData" / "Roaming"),
                     ("LOCALAPPDATA", h / "AppData" / "Local")):
        monkeypatch.setenv(var, str(val))
    for var in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "SATK_MCP_GROUPS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SATK_PATHS_BLENDER", str(tmp_path / "no-blender.exe"))
    _config.reset()
    return h


def _fixed_entry(tmp_path: Path) -> tuple[dict, str]:
    """An entry with deterministic paths: <ROOT>/co/.venv python, <ROOT>/ws workspace."""
    root = tmp_path / "fixed"
    co, ws = root / "co", root / "ws"
    py = co / ".venv" / "Scripts" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    (co / "src" / "satk").mkdir(parents=True)
    return C.server_entry("core,index,media", workspace=ws, checkout=co), jpath(root)


def _render_all(tmp_path: Path) -> dict[str, str]:
    """{file name: text} of every reference file."""
    entry, root = _fixed_entry(tmp_path)
    files: dict[str, str] = {}
    cmds = []
    for name, cl in C.CLIENTS.items():
        files[name + _EXT[cl.fmt]] = C.fragment_text(cl, entry)
        for sc in cl.scopes or ("-",):
            argv = C.command_line(cl, entry, sc if sc != "-" else None)
            cmds.append(f"{name} {sc}: {C.quoted(argv) if argv else '(none)'}")
    files["commands.txt"] = "\n".join(cmds) + "\n"
    return {k: v.replace(root, "<ROOT>") for k, v in files.items()}


def test_fragments_match_reference(home, tmp_path):
    got = _render_all(tmp_path)
    if os.environ.get("UPDATE_GOLDEN_CLIENTS") == "1":
        GOLDEN.mkdir(parents=True, exist_ok=True)
        for f, text in got.items():
            (GOLDEN / f).write_text(text, encoding="utf-8", newline="\n")
    want = {p.name: p.read_text(encoding="utf-8") for p in sorted(GOLDEN.iterdir())}
    assert sorted(got) == sorted(want)
    for f in got:
        assert got[f] == want[f], f


def test_reference_files_parse(home):
    names = {p.stem for p in GOLDEN.iterdir() if p.name != "commands.txt"}
    assert names == set(C.CLIENT_NAMES) and len(names - {"generic"}) >= 7
    for name, cl in C.CLIENTS.items():
        text = (GOLDEN / (name + _EXT[cl.fmt])).read_text(encoding="utf-8")
        if cl.fmt == "toml":
            assert set(tomllib.loads(text)["mcp_servers"]["satk"]) == {"command", "args", "env"}
        elif cl.fmt == "json":
            data = json.loads(text)
            key = "servers" if name == "vscode" else "mcpServers"
            assert list(data) == [key]
            entry = data[key]["satk"]
            assert entry["args"] == ["-X", "utf8", "-m", "satk.mcp"]
            assert entry["env"]["OTEL_SDK_DISABLED"] == "true" and entry["env"]["SATK_MCP_GROUPS"] == "core,index,media"
            assert (entry.get("type") == "stdio") == (name == "vscode")
        else:
            assert text.startswith('name: "satk"\nversion: 0.0.1\nschema: v1\nmcpServers:\n  - name: "satk"\n')


def test_client_names_match_the_op_signature():
    assert typing.get_args(mcp_ops.ClientName) == C.CLIENT_NAMES


def test_locations(home, satk_home, monkeypatch):
    def p(client, scope, project=None):
        return jpath(C.config_path(C.CLIENTS[client], scope, project))

    h = jpath(home)
    assert p("cursor", "user") == f"{h}/.cursor/mcp.json"
    assert p("cursor", "project") == jpath(satk_home / ".cursor" / "mcp.json")
    assert p("windsurf", "user") == f"{h}/.codeium/windsurf/mcp_config.json"
    assert p("vscode", "user") == f"{h}/AppData/Roaming/Code/User/mcp.json"
    assert p("vscode", "project", satk_home / "mod") == jpath(satk_home / "mod" / ".vscode" / "mcp.json")
    assert p("claude-desktop", "user") == f"{h}/AppData/Roaming/Claude/claude_desktop_config.json"
    assert p("gemini", "user") == f"{h}/.gemini/settings.json"
    assert p("lmstudio", "user") == f"{h}/.lmstudio/mcp.json"
    assert p("continue", "project") == jpath(satk_home / ".continue" / "mcpServers" / "satk.yaml")
    assert p("codex", "user") == f"{h}/.codex/config.toml"
    assert p("claude-code", "user") == f"{h}/.claude.json"
    assert p("claude-code", "project") == jpath(satk_home / ".mcp.json")
    assert C.config_path(C.CLIENTS["generic"], None) is None
    monkeypatch.setenv("CODEX_HOME", str(home / "cx"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "cc"))
    assert p("codex", "user") == f"{h}/cx/config.toml"
    assert p("claude-code", "user") == f"{h}/cc/.claude.json"
    # the Microsoft Store build of Claude Desktop keeps its config in a package folder
    store = home / "AppData" / "Local" / "Packages" / "Claude_abc123" / "LocalCache" / "Roaming" / "Claude"
    store.mkdir(parents=True)
    assert p("claude-desktop", "user") == jpath(store / "claude_desktop_config.json")


def test_auto_groups(home, satk_home, tmp_path, monkeypatch):
    assert C.auto_groups() == ["core", "index", "media"]
    viewer = satk_home / "viewer" / "ariane" / "bin" / "ariane.exe"
    viewer.parent.mkdir(parents=True)
    viewer.write_bytes(b"")
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"")
    monkeypatch.setenv("SATK_PATHS_BLENDER", str(blender))
    _config.reset()
    assert C.auto_groups() == ["core", "index", "media", "view", "blender"]
    symdb = satk_home / "work" / "re" / "symdb.sqlite"
    symdb.parent.mkdir(parents=True)
    symdb.write_bytes(b"")
    (satk_home / "engine" / "mtasa" / ".git").mkdir(parents=True)
    assert C.auto_groups() == list(C.adapter.GROUP_ORDER)
    assert "SATK_MCP_GROUPS" not in C.server_entry()["env"]  # every group = no restriction


def test_groups_values(home):
    assert C.server_entry()["env"]["SATK_MCP_GROUPS"] == "core,index,media"
    assert C.server_entry("auto")["env"]["SATK_MCP_GROUPS"] == "core,index,media"
    assert "SATK_MCP_GROUPS" not in C.server_entry("all")["env"]
    assert C.server_entry("view, core")["env"]["SATK_MCP_GROUPS"] == "core,view"
    assert C.server_entry("core")["env"]["OTEL_SDK_DISABLED"] == "true"
    with pytest.raises(Exception) as e:
        C.server_entry("cor")
    assert getattr(e.value, "code", "") == "BAD_PARAMS"


def test_links_carry_the_entry(home, tmp_path):
    entry, _ = _fixed_entry(tmp_path)
    link = C.install_link(C.CLIENTS["cursor"], entry)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
    assert q["name"] == ["satk"] and json.loads(base64.b64decode(q["config"][0])) == entry
    v = C.install_link(C.CLIENTS["vscode"], entry)
    assert v.startswith("vscode:mcp/install?")
    obj = json.loads(urllib.parse.unquote(v.split("?", 1)[1]))
    assert obj == {"name": "satk", "type": "stdio", **entry}
    assert C.install_link(C.CLIENTS["codex"], entry) is None


# --------------------------------------------------------------------------- writes through the CLI


def _cfg(run_cli, *args):
    return run_cli(["mcp", "config", *args])


def test_json_write_keeps_other_servers_backup_and_no_secret_in_diff(home, run_cli):
    f = home / ".cursor" / "mcp.json"
    f.parent.mkdir()
    other = {"command": "npx", "env": {"API_KEY": "secret-value-123"}}
    f.write_bytes(b"\xef\xbb\xbf" + json.dumps({"mcpServers": {"other": other}, "extra": 1}).encode())
    before = f.read_bytes()
    r = _cfg(run_cli, "--client", "cursor", "--check")
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"
    w = _cfg(run_cli, "--client", "cursor", "--write").json
    assert w["written"] is True and w["status"] == "ok" and w["path"] == jpath(f)
    assert "secret-value-123" not in json.dumps(w)  # the diff shows the satk entry only
    raw = f.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    data = json.loads(raw)
    assert data["mcpServers"]["other"] == other and data["extra"] == 1
    assert data["mcpServers"]["satk"] == C.server_entry()
    assert Path(w["backup"]).read_bytes() == before
    assert _cfg(run_cli, "--client", "cursor", "--check").code == 0
    again = _cfg(run_cli, "--client", "cursor", "--write").json
    assert again["written"] is False and again["status"] == "ok"


def test_unparsable_json_is_never_overwritten(home, run_cli):
    f = home / "AppData" / "Roaming" / "Code" / "User" / "mcp.json"
    f.parent.mkdir(parents=True)
    f.write_text('{\n  // my servers\n  "servers": {}\n}\n', encoding="utf-8")  # JSONC: comments
    before = f.read_bytes()
    r = _cfg(run_cli, "--client", "vscode", "--write")
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS" and "not valid JSON" in r.json["error"]["msg"]
    assert '"servers"' in r.json["error"]["data"]["fragment"]
    assert f.read_bytes() == before
    assert not f.with_name(f.name + C.BACKUP_SUFFIX).exists()


def test_vscode_project_uses_servers_key(home, satk_home, run_cli):
    mod = satk_home / "mymod"
    mod.mkdir()
    w = _cfg(run_cli, "--client", "vscode", "--scope", "project", "--project", str(mod), "--write").json
    data = json.loads((mod / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    assert list(data) == ["servers"] and data["servers"]["satk"]["type"] == "stdio"
    assert w["link"].startswith("vscode:mcp/install?") and "command" not in w


def test_codex_toml_edit_keeps_everything_else(home, run_cli):
    f = home / ".codex" / "config.toml"
    f.parent.mkdir()
    f.write_text('model = "gpt-5"  # keep me\n\n[mcp_servers.other]\ncommand = "x"\n\n'
                 '[mcp_servers.satk]\ncommand = "old"\nargs = []\n\n[mcp_servers.satk.env]\nA = "1"\n\n'
                 '[profiles.fast]\nmodel = "mini"\n', encoding="utf-8")
    before = tomllib.loads(f.read_text(encoding="utf-8"))
    assert _cfg(run_cli, "--client", "codex").json["status"] == "differs"
    w = _cfg(run_cli, "--client", "codex", "--write").json
    assert w["written"] is True and any(ln.startswith("-command") for ln in w["diff"])
    text = f.read_text(encoding="utf-8")
    after = tomllib.loads(text)
    assert after["mcp_servers"]["satk"] == C.server_entry()
    assert after["mcp_servers"]["other"] == before["mcp_servers"]["other"]
    assert after["profiles"] == before["profiles"] and after["model"] == "gpt-5"
    assert "# keep me" in text and text.count("[mcp_servers.satk]") == 1
    assert _cfg(run_cli, "--client", "codex", "--check").code == 0
    assert w["command"].startswith("codex mcp add satk --env PYTHONUTF8=1 ")


def test_codex_inline_definition_is_refused(home, run_cli):
    f = home / ".codex" / "config.toml"
    f.parent.mkdir()
    f.write_text('mcp_servers = { satk = { command = "old" } }\n', encoding="utf-8")
    before = f.read_bytes()
    r = _cfg(run_cli, "--client", "codex", "--write")
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS" and "cannot edit safely" in r.json["error"]["msg"]
    assert f.read_bytes() == before


def test_continue_block_file(home, satk_home, run_cli):
    w = _cfg(run_cli, "--client", "continue", "--write").json
    f = satk_home / ".continue" / "mcpServers" / "satk.yaml"
    assert w["scope"] == "project" and w["path"] == jpath(f) and f.read_text(encoding="utf-8") == w["fragment"]
    assert _cfg(run_cli, "--client", "continue", "--check").code == 0
    f.write_text("name: other\n", encoding="utf-8")
    assert _cfg(run_cli, "--client", "continue").json["status"] == "differs"


def test_claude_code_user_scope_runs_the_official_cli(home, run_cli, monkeypatch):
    calls = []
    f = home / ".claude.json"
    f.write_text(json.dumps({"numStartups": 3, "mcpServers": {"satk": {"type": "stdio", "command": "old"}}}),
                 encoding="utf-8")

    def fake(argv):
        calls.append(argv)
        data = json.loads(f.read_text(encoding="utf-8"))
        if argv[2] == "remove":
            data["mcpServers"].pop(argv[3])
        else:
            i = argv.index("--")
            env = dict(argv[k + 1].split("=", 1) for k in range(i) if argv[k] == "-e")
            data["mcpServers"][argv[3]] = {"type": "stdio", "command": argv[i + 1], "args": argv[i + 2:], "env": env}
        f.write_text(json.dumps(data), encoding="utf-8")
        return 0, "ok"

    monkeypatch.setattr(C, "run_client_cli", fake)
    w = _cfg(run_cli, "--client", "claude-code", "--write").json
    assert w["written"] is True and w["via"] == "claude mcp add" and "backup" not in w
    assert [c[:4] for c in calls] == [["claude", "mcp", "remove", "satk"], ["claude", "mcp", "add", "satk"]]
    assert json.loads(f.read_text(encoding="utf-8"))["numStartups"] == 3
    assert _cfg(run_cli, "--client", "claude-code", "--check").code == 0  # "type": "stdio" is the default
    assert not any('"type"' in ln for ln in w["diff"])


def test_scope_and_parameter_errors(home, run_cli):
    r = _cfg(run_cli, "--client", "windsurf", "--scope", "project")
    assert r.json["error"]["code"] == "BAD_PARAMS" and r.json["error"]["data"]["scopes"] == ["user"]
    r = _cfg(run_cli, "--client", "cursor", "--project", ".")
    assert r.json["error"]["code"] == "BAD_PARAMS" and "--scope project" in r.json["error"]["msg"]
    assert _cfg(run_cli, "--client", "generic", "--write").json["error"]["code"] == "BAD_PARAMS"
    assert _cfg(run_cli, "--client", "nosuch").json["error"]["code"] == "BAD_PARAMS"
    assert _cfg(run_cli, "--client", "cursor", "--name", "bad name").json["error"]["code"] == "BAD_PARAMS"
    assert _cfg(run_cli, "--groups", "core").json["error"]["code"] == "BAD_PARAMS"  # needs --client
    assert _cfg(run_cli, "--client", "cursor", "--groups", "cor").json["error"]["code"] == "BAD_PARAMS"


def test_generic_with_path(home, run_cli, tmp_path):
    f = tmp_path / "any-client.json"
    w = _cfg(run_cli, "--client", "generic", "--path", str(f), "--write").json
    assert "scope" not in w and w["written"] is True
    assert json.loads(f.read_text(encoding="utf-8")) == {"mcpServers": {"satk": C.server_entry()}}
    shown = _cfg(run_cli, "--client", "generic").json
    assert "path" not in shown and shown["fragment"] == {"mcpServers": {"satk": C.server_entry()}}


def _raw(home: Path, *args: str) -> subprocess.CompletedProcess:
    from satk.core.config import SRC_ROOT

    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(SRC_ROOT))
    return subprocess.run([sys.executable, "-X", "utf8", "-m", "satk", "mcp", "config", *args], capture_output=True,
                          env=env, timeout=120, stdin=subprocess.DEVNULL)


def test_print_command_and_raw_print_text_only(home):
    p = _raw(home, "--client", "claude-code", "--groups", "core", "--print-command")
    assert p.returncode == 0, p.stderr
    line = p.stdout.decode("utf-8").strip()
    assert line.startswith("claude mcp add satk --scope user -e PYTHONUTF8=1 ") and line.endswith(" -X utf8 -m satk.mcp")
    assert "-e SATK_MCP_GROUPS=core -e OTEL_SDK_DISABLED=true -- " in line
    p = _raw(home, "--client", "codex", "--groups", "core", "--raw")
    assert p.returncode == 0 and tomllib.loads(p.stdout.decode("utf-8"))["mcp_servers"]["satk"]["env"]["SATK_MCP_GROUPS"] == "core"
    p = _raw(home, "--client", "cursor", "--print-command")
    assert p.returncode != 0 and b"UNSUPPORTED" in p.stdout + p.stderr


def test_agents_cannot_run_config_ops():
    from satk.core.registry import get_op
    from satk.mcp.generic import denial

    assert denial(get_op("mcp.config"))[0] == "UNSUPPORTED"
    assert denial(get_op("agent.install_skill"))[0] == "CONSENT_REQUIRED"
