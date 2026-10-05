"""``satk dev gen-docs`` (SPEC §4.13): deterministic generated agent docs and ``--check``."""

from __future__ import annotations

from satk.core import registry as R
from satk.runtime import gendocs as G


def test_generate_check_roundtrip(tmp_path, run_cli):
    r = run_cli(["dev", "gen-docs", "--root", str(tmp_path)])
    assert r.code == 0 and sorted(r.json["written"]) == sorted(G.DOCS)
    tools = (tmp_path / "docs/agent/tools.md").read_text(encoding="utf-8")
    assert "### `satk_status`" in tools and "### `note`" in tools and "satk mcp selftest" in tools
    assert "> satk = GTA:SA toolkit" in tools
    schema = (tmp_path / "docs/agent/schema.md").read_text(encoding="utf-8")
    assert "## notes: `work/notes.sqlite`" in schema and "| `note_fts` | fts5 |" in schema
    again = run_cli(["dev", "gen-docs", "--root", str(tmp_path)])
    assert "written" not in again.json and len(again.json["unchanged"]) == 2
    assert run_cli(["dev", "gen-docs", "--check", "--root", str(tmp_path)]).code == 0
    (tmp_path / "docs/agent/tools.md").write_text(tools + "\nedited\n", encoding="utf-8")
    bad = run_cli(["dev", "gen-docs", "--check", "--root", str(tmp_path)])
    assert bad.code == 1 and bad.json["error"]["code"] == "REVISION"
    assert bad.json["error"]["data"]["stale"] == ["docs/agent/tools.md"]


def test_render_is_deterministic():
    assert G.render_tools() == G.render_tools()
    assert G.render_schema() == G.render_schema()


def test_every_operation_is_documented():
    text = G.render_tools()
    for o in R.all_ops():
        assert (f"### `{o.mcp_name}`" if o.mcp_name else f"`satk {o.cli}`") in text, o.name
