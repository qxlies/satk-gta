"""``satk agent install-skill`` and the Claude Code plugin in ``.claude-plugin/`` (M3 A4).

The skill copies run in an isolated home (``USERPROFILE``/``HOME``/``CLAUDE_CONFIG_DIR`` under tmp).
"""

from __future__ import annotations

import json
import re

import pytest

from satk.core.config import REPO_ROOT
from satk.core.paths import jpath
from satk.mcp import skill as S

SKILL = REPO_ROOT / "docs" / "agent" / "SKILL.md"
PLUGIN = REPO_ROOT / ".claude-plugin"


@pytest.fixture
def home(satk_home, tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return h


def test_install_user_scope_both_clients(home, run_cli):
    env = run_cli(["agent", "install-skill"]).json
    assert env["ok"] is True and env["source"] == jpath(SKILL)
    rows = {r[0]: r for r in env["rows"]}
    assert rows["claude"][1] == jpath(home / ".claude" / "skills" / "satk" / "SKILL.md")
    assert rows["codex"][1] == jpath(home / ".agents" / "skills" / "satk" / "SKILL.md")
    assert {r[3] for r in env["rows"]} == {"installed"}
    for r in env["rows"]:
        assert open(r[1], encoding="utf-8").read() == SKILL.read_text(encoding="utf-8")
    again = run_cli(["agent", "install-skill", "--check"]).json
    assert again["ok"] is True and {r[2] for r in again["rows"]} == {"ok"} and {r[3] for r in again["rows"]} == {"none"}


def test_changed_copy_is_backed_up_and_check_fails(home, run_cli):
    dst = home / ".claude" / "skills" / "satk" / "SKILL.md"
    dst.parent.mkdir(parents=True)
    dst.write_text("old skill\n", encoding="utf-8")
    r = run_cli(["agent", "install-skill", "--client", "claude", "--check"])
    assert r.code == 1 and r.json["error"]["code"] == "REVISION"
    assert dst.read_text(encoding="utf-8") == "old skill\n"  # --check never writes
    env = run_cli(["agent", "install-skill", "--client", "claude"]).json
    assert env["rows"][0][3] == "updated"
    assert (dst.parent / "SKILL.md.satk-backup").read_text(encoding="utf-8") == "old skill\n"
    r = run_cli(["agent", "install-skill", "--client", "codex", "--check"])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"


def test_project_scope_and_dest(home, satk_home, tmp_path, run_cli, monkeypatch):
    mod = tmp_path / "mymod"
    mod.mkdir()
    env = run_cli(["agent", "install-skill", "--client", "codex", "--scope", "project", "--project", str(mod)]).json
    assert env["rows"][0][1] == jpath(mod / ".agents" / "skills" / "satk" / "SKILL.md")
    env = run_cli(["agent", "install-skill", "--client", "claude", "--scope", "project"]).json
    assert env["rows"][0][1] == jpath(satk_home / ".claude" / "skills" / "satk" / "SKILL.md")
    env = run_cli(["agent", "install-skill", "--dest", str(tmp_path / "other")]).json
    assert env["rows"] == [["dest", jpath(tmp_path / "other" / "satk" / "SKILL.md"), "ok", "installed"]]
    (tmp_path / "other" / "satk" / "SKILL.md").write_text("stale\n", encoding="utf-8")
    env = run_cli(["agent", "install-skill", "--dest", str(tmp_path / "other")]).json
    assert env["rows"][0][3] == "updated"
    assert not (tmp_path / "other" / "satk" / "SKILL.md.satk-backup").exists()  # --dest: the caller's folder
    assert run_cli(["agent", "install-skill", "--project", str(mod)]).json["error"]["code"] == "BAD_PARAMS"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    env = run_cli(["agent", "install-skill", "--client", "claude"]).json
    assert env["rows"][0][1] == jpath(tmp_path / "cc" / "skills" / "satk" / "SKILL.md")


# --------------------------------------------------------------------------- the skill itself


def test_skill_is_general_english_and_portable():
    text = SKILL.read_text(encoding="utf-8")
    assert not re.search(r"[А-Яа-яЁё]", text), "model-facing docs are English-only"
    machine = [f"D:{sep}{name}" for sep in ("\\", "/") for name in ("Games", "files")]  # the maintainers' layout
    for m in (*machine, "C:\\Users", "qxlies"):
        assert m not in text, m
    assert "satk_status" in text and "satk_ops" in text and "satk_op" in text


# --------------------------------------------------------------------------- Claude Code plugin


def test_marketplace_manifest():
    m = json.loads((PLUGIN / "marketplace.json").read_text(encoding="utf-8"))
    assert m["name"] == "satk" and m["owner"]["name"]
    (entry,) = m["plugins"]
    assert entry["name"] == "satk" and entry["source"] == "./.claude-plugin/satk"
    assert (REPO_ROOT / entry["source"] / ".claude-plugin" / "plugin.json").is_file()


def test_plugin_manifest_and_mcp_server():
    root = PLUGIN / "satk"
    p = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert p["name"] == "satk" and p["license"] == "MIT" and len(p["description"]) <= 300
    mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp == {"mcpServers": {"satk": {"command": "satk", "args": ["mcp"],
                                           "env": {"PYTHONUTF8": "1", "OTEL_SDK_DISABLED": "true"}}}}
    raw = (root / ".mcp.json").read_bytes() + (root / ".claude-plugin" / "plugin.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"D:" not in raw


def test_plugin_skill_is_the_published_skill():
    copy = PLUGIN / "satk" / "skills" / "satk" / "SKILL.md"
    assert copy.read_bytes() == SKILL.read_bytes(), \
        "refresh it: satk agent install-skill --dest .claude-plugin/satk/skills"
