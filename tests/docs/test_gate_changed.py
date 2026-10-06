"""satk dev gate --changed: which tests a change selects (synthetic checkout, git, the real checkout)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from satk.docs import changed as C
from satk.docs import gate as G

FILES = {
    "src/satk/__init__.py": "",
    "src/satk/core/__init__.py": "",
    "src/satk/core/paths.py": "",
    "src/satk/alpha/__init__.py": "",
    "src/satk/alpha/lib.py": "X = 1\n",
    "src/satk/alpha/ops.py": "from . import lib\nfrom ..core import resources\ndef f():\n    return resources.read_json('alpha', 'x.json')\n",
    "src/satk/beta/__init__.py": "",
    "src/satk/beta/ops.py": "def g():\n    from ..alpha import lib\n    return lib.X\n",
    "src/satk/gamma/__init__.py": "from .impl import *\n",
    "src/satk/gamma/impl.py": "Y = 2\n",
    "src/satk/delta/__init__.py": "",
    "src/satk/delta/ops.py": "from ..gamma import Y\n",
    "src/satk/index/__init__.py": "",
    "src/satk/index/api.py": "",
    "src/satk/index/schema.sql": "",
    "tests/conftest.py": "from satk.index import api\n",
    "tests/core/test_c.py": "",
    "tests/docs/test_d.py": "",
    "tests/mcp/test_m.py": "",
    "tests/alpha/test_a.py": "from satk.alpha import ops\n",
    "tests/alpha/conftest.py": "",
    "tests/beta/test_b.py": "",
    "tests/delta/test_e.py": "",
    "tests/other/test_o.py": "from satk.alpha import lib\n",
    "tests/other/helpers.py": "from satk.alpha import lib\n",
    # two literals: a backslash-n escape right before the decorator reads as an address to the public export audit
    "tests/gamma/test_g_game.py": "import pytest\n" "@pytest.mark.game\ndef test_x():\n    pass\n",
    "tests/golden/index_vanilla.json": "{}",
    "tests/e2e/test_docs_layout.py": "",
    "tests/e2e/test_repo_hygiene.py": "",
    "tests/release/test_r.py": "",
    "data/alpha/x.json": "{}",
    "data/orphan.json": "{}",
    "docs/en/alpha.md": "",
    "docs/agent/SKILL.md": "",
    "mta-resources/res/main.lua": "",
    "CLAUDE.md": "",
}
ALWAYS = ["tests/core", "tests/docs", "tests/mcp"]


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("repo")
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def sel(repo, *changed):
    return C.select(repo, list(changed), base="main", merge_base="abc")


def test_nothing_changed_runs_only_the_always_set(repo):
    s = sel(repo)
    assert s.paths == ALWAYS and not s.all_tests and not s.game


def test_a_source_change_selects_its_package_and_importers(repo):
    s = sel(repo, "src/satk/alpha/ops.py")
    assert sorted(s.paths) == sorted(ALWAYS + ["tests/alpha"])
    assert not s.game and not s.all_tests
    lib = sel(repo, "src/satk/alpha/lib.py")  # imported by beta.ops (lazily), alpha.ops and a test of another package
    # tests/other has a test and a helper that import it: the helper selects the folder, which swallows the test
    assert set(lib.paths) == set(ALWAYS) | {"tests/alpha", "tests/beta", "tests/other"}
    assert "imports satk.alpha.lib" in " ".join(lib.reasons["tests/beta"])


def test_reexport_through_the_package_init_counts(repo):
    s = sel(repo, "src/satk/gamma/impl.py")  # gamma/__init__ re-exports it, delta imports from gamma
    assert "tests/delta" in s.paths and "tests/gamma" in s.paths
    assert "tests/delta" in sel(repo, "src/satk/gamma/__init__.py").paths


def test_core_and_shared_test_setup_select_everything(repo):
    for f in ("src/satk/core/paths.py", "tests/conftest.py", "src/satk/__init__.py", "weird.bin", "tests/odd.py"):
        s = sel(repo, f)
        assert s.all_tests and s.game and s.all_reason, f


def test_game_steps_only_for_index_formats_game_code(repo):
    assert not sel(repo, "src/satk/beta/ops.py").game
    s = sel(repo, "src/satk/index/api.py")
    assert s.game and "src/satk/index/api.py" in s.game_reason and s.game_paths == []
    assert sel(repo, "src/satk/index/schema.sql").game
    g = sel(repo, "tests/golden/index_vanilla.json")
    assert g.game and "tests/golden/index_vanilla.json" in g.game_reason


def test_a_changed_test_selects_only_itself_unless_it_is_a_helper(repo):
    s = sel(repo, "tests/beta/test_b.py")
    assert set(s.paths) == set(ALWAYS) | {"tests/beta/test_b.py"} and not s.game
    s = sel(repo, "tests/alpha/conftest.py")
    assert "tests/alpha" in s.paths and "tests/alpha/test_a.py" not in s.paths
    s = sel(repo, "tests/alpha/test_deleted.py")  # a deleted test file selects nothing
    assert set(s.paths) == set(ALWAYS)


def test_a_game_marked_test_asks_for_the_game_steps_for_itself(repo):
    s = sel(repo, "tests/gamma/test_g_game.py")
    assert s.game and s.game_paths == ["tests/gamma/test_g_game.py"]


def test_folder_swallows_files_inside(repo):
    s = sel(repo, "src/satk/alpha/lib.py", "tests/alpha/test_a.py")
    assert "tests/alpha" in s.paths and "tests/alpha/test_a.py" not in s.paths
    assert any("test_a.py" in r for r in s.reasons["tests/alpha"])


def test_data_follows_the_code_that_reads_it(repo):
    s = sel(repo, "data/alpha/x.json")
    assert "tests/alpha" in s.paths and not s.all_tests
    assert any("reads data/alpha/x.json" in r for r in s.reasons["tests/alpha"])
    orphan = sel(repo, "data/orphan.json")  # nothing names it: do not guess
    assert orphan.all_tests and "not read by name" in orphan.all_reason


def test_docs_packaging_and_resources(repo):
    s = sel(repo, "docs/en/alpha.md")
    assert set(s.paths) == set(ALWAYS) | set(C._DOC_TESTS) and not s.game
    assert "tests/mcp" in sel(repo, "docs/agent/SKILL.md").paths
    c = sel(repo, "CLAUDE.md")
    assert "tests/release" in c.paths and not c.all_tests


def test_lines_and_summary_are_printable(repo):
    s = sel(repo, "src/satk/alpha/ops.py", "docs/en/alpha.md")
    text = "\n".join(s.lines())
    assert "tests/alpha <- src/satk/alpha/ops.py" in text and "game steps: no" in text
    d = s.summary()
    assert d["changed"] == 2 and d["game"] is False and "tests/alpha" in d["tests"]
    everything = sel(repo, "weird.bin").summary()
    assert everything["tests"] == "all" and "weird.bin" in everything["why_all"]


# --------------------------------------------------------------------------- git


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True,
                          capture_output=True, text=True).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git not found")
def test_changed_files_since_the_merge_base(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    (tmp_path / "b.txt").write_text("1", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "lane")
    (tmp_path / "a.txt").write_text("2", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "committed change")
    _git(tmp_path, "checkout", "-q", "main")
    (tmp_path / "m.txt").write_text("1", encoding="utf-8")  # main moved on after the branch point
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "main moves")
    _git(tmp_path, "checkout", "-q", "lane")
    (tmp_path / "b.txt").write_text("2", encoding="utf-8")  # unstaged
    (tmp_path / "new dir").mkdir()
    (tmp_path / "new dir" / "c.txt").write_text("1", encoding="utf-8")  # untracked
    mb, files = C.changed_files(tmp_path, "main")
    assert files == ["a.txt", "b.txt", "new dir/c.txt"] and len(mb) == 40  # nothing from main's own commits
    with pytest.raises(RuntimeError):
        C.changed_files(tmp_path, "no-such-branch")
    s = C.select_from_git(tmp_path, "no-such-branch")  # cannot tell: be safe, run everything
    assert s.all_tests and "cannot tell what changed" in s.all_reason


# --------------------------------------------------------------------------- the real checkout


def test_the_real_checkout_maps_a_package_change_to_its_own_tests(repo_root):
    s = C.select(repo_root, ["src/satk/mta/ops.py"])
    assert "tests/mta" in s.paths and not s.all_tests and not s.game
    s = C.select(repo_root, ["src/satk/index/api.py"])
    assert s.game and "tests/index" in s.paths


# --------------------------------------------------------------------------- the plan


def test_plan_with_a_selection_narrows_the_test_steps(tmp_path):
    s = C.Selection(paths=["tests/core", "tests/mta"], game=False)
    steps = {st.name: st for st in G.plan(quick=False, private_work=tmp_path, selection=s)}
    assert list(steps) == ["assetguard", "docs-generated", "agent-docs-sync", "tests", "mcp-selftest"]
    assert steps["tests"].argv[-3:] == ["not game", "tests/core", "tests/mta"] and steps["tests"].ok_codes == (0, 5)
    assert steps["tests"].describe() == 'pytest -m "not game" tests/core tests/mta'
    assert steps["mcp-selftest"].describe() == "satk mcp selftest --json"


def test_plan_with_game_selection_and_with_game(tmp_path):
    s = C.Selection(paths=["tests/core"], game=True, game_reason="x", game_paths=["tests/gamma/test_g_game.py"])
    names = [st.name for st in G.plan(quick=False, private_work=tmp_path, selection=s)]
    assert names[-3:] == ["index-golden", "index-verify", "tests-game"]
    last = G.plan(quick=False, private_work=tmp_path, selection=s)[-1]
    assert last.argv[-2:] == ["game", "tests/gamma/test_g_game.py"]
    quiet = C.Selection(paths=["tests/core"])
    assert "tests-game" not in [st.name for st in G.plan(quick=False, private_work=tmp_path, selection=quiet)]
    forced = G.plan(quick=False, private_work=tmp_path, selection=quiet, with_game=True)
    assert forced[-1].name == "tests-game" and forced[-1].argv[-1] == "game"  # every game test
    assert "tests-game" not in [st.name for st in G.plan(quick=True, private_work=tmp_path, selection=s, with_game=True)]


def test_plan_with_an_everything_selection_is_the_full_test_run(tmp_path):
    s = C.Selection(all_tests=True, all_reason="core", game=True)
    steps = {st.name: st for st in G.plan(quick=False, private_work=tmp_path, selection=s)}
    assert steps["tests"].argv[-2:] == ["-m", "not game"] and "tests-game" in steps


def test_full_plan_is_unchanged_without_a_selection(tmp_path):
    names = [st.name for st in G.plan(quick=False, private_work=tmp_path)]
    assert names == ["assetguard", "docs-generated", "agent-docs-sync", "tests", "mcp-selftest", "index-golden",
                     "index-verify", "tests-game"]
    assert all(st.ok_codes == (0,) for st in G.plan(quick=False, private_work=tmp_path))
