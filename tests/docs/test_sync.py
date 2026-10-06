"""``satk dev sync-agent-docs`` (WP-12): publishes docs/agent sources into an isolated workspace."""

from __future__ import annotations

import os
import time
from pathlib import Path

from satk.core.config import REPO_ROOT
from satk.docs.sync import PAIRS


def _dsts(ws: Path) -> list[Path]:
    return [ws / d for _, d in PAIRS]


def test_check_reports_missing_then_sync_then_check_ok(satk_home: Path, run_cli):
    r = run_cli(["dev", "sync-agent-docs", "--check"])
    assert r.code == 1 and r.json["error"]["code"] == "REVISION"
    assert {row[2] for row in r.json["error"]["data"]["rows"]} == {"missing"}

    r = run_cli(["dev", "sync-agent-docs"])
    assert r.code == 0, r.out
    assert len(r.json["written"]) == len(PAIRS)
    for (s, _), dst in zip(PAIRS, _dsts(satk_home)):
        assert dst.read_bytes() == (REPO_ROOT / s).read_bytes()

    r = run_cli(["dev", "sync-agent-docs", "--check"])
    assert r.code == 0 and {row[2] for row in r.json["rows"]} == {"same"}
    r = run_cli(["dev", "sync-agent-docs"])
    assert r.code == 0 and r.json["written"] == []


def test_edited_copy_is_not_overwritten_without_force(satk_home: Path, run_cli):
    assert run_cli(["dev", "sync-agent-docs"]).code == 0
    dst = _dsts(satk_home)[0]
    dst.write_text("edited in place\n", encoding="utf-8")
    future = time.time() + 60
    os.utime(dst, (future, future))
    r = run_cli(["dev", "sync-agent-docs"])
    assert r.code == 1 and r.json["error"]["code"] == "EXISTS"
    assert dst.read_text(encoding="utf-8") == "edited in place\n"
    r = run_cli(["dev", "sync-agent-docs", "--force"])
    assert r.code == 0
    assert dst.read_bytes() == (REPO_ROOT / PAIRS[0][0]).read_bytes()


def test_older_differing_copy_is_updated(satk_home: Path, run_cli):
    assert run_cli(["dev", "sync-agent-docs"]).code == 0
    dst = _dsts(satk_home)[-1]
    dst.write_text("stale\n", encoding="utf-8")
    past = time.time() - 3600 * 24 * 365 * 5
    os.utime(dst, (past, past))
    r = run_cli(["dev", "sync-agent-docs"])
    assert r.code == 0 and len(r.json["written"]) == 1


def test_skill_companion_folders_are_published_with_the_skill():
    srcs = [s for s, _ in PAIRS]
    assert srcs[0] == "docs/agent/SKILL.md"
    style = [(s, d) for s, d in PAIRS if s.startswith("docs/agent/style/")]
    assert ("docs/agent/style/README.md", ".claude/skills/satk/style/README.md") in style
    assert ("docs/agent/briefs/asset-brief.md", ".claude/skills/satk/briefs/asset-brief.md") in PAIRS
    for s, d in PAIRS:
        assert (REPO_ROOT / s).is_file(), s


def _fake_worktree(root: Path) -> tuple[Path, Path, Path]:
    """<root>/ws/tools (main checkout) and its linked worktree <root>/ws/work/wt/x."""
    ws = root / "ws"
    main = ws / "tools"
    wtgit = main / ".git" / "worktrees" / "x"
    wtgit.mkdir(parents=True)
    (wtgit / "commondir").write_text("../..\n", encoding="utf-8")
    wt = ws / "work" / "wt" / "x"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {wtgit}\n", encoding="utf-8")
    return ws, main, wt


def test_a_task_worktree_publishes_nothing(tmp_path: Path):
    from satk.docs.sync import plan, task_worktree

    ws, main, wt = _fake_worktree(tmp_path)
    (wt / "docs" / "agent").mkdir(parents=True)
    (wt / "docs" / "agent" / "SKILL.md").write_text("unmerged\n", encoding="utf-8")
    assert task_worktree(wt, ws) is True
    assert plan(wt, ws) == []
    assert task_worktree(main, ws) is False  # the main checkout publishes
    other = tmp_path / "other-ws"
    other.mkdir()
    assert task_worktree(wt, other) is False  # a worktree of a checkout outside that workspace
    assert [p.state for p in plan(wt, other)] == ["missing"]
