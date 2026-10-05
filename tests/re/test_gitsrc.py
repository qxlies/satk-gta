"""Read-only source trees: git refs without checkout, plain directories (R16; WP-09)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.re.gitsrc import DirTree, GitTree, decode, git_env

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "source").mkdir()
    (r / "source" / "a.cpp").write_text("int a = 1;\n", encoding="utf-8")
    (r / "source" / "b.h").write_bytes("﻿// BOM header é\n".encode("utf-8"))
    (r / "docs").mkdir()
    (r / "docs" / "x.json").write_text("[]", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "one")
    _git(r, "tag", "v1")
    (r / "source" / "a.cpp").write_text("int a = 2;\n", encoding="utf-8")
    _git(r, "commit", "-q", "-am", "two")
    return r


def test_git_tree_reads_refs_without_checkout(repo: Path):
    head = GitTree(repo, "HEAD")
    old = GitTree(repo, "v1")
    assert head.rev == _git(repo, "rev-parse", "HEAD") and old.rev != head.rev and len(head.rev) == 40
    assert head.read("source/a.cpp") == "int a = 2;\n"
    assert old.read("source/a.cpp") == "int a = 1;\n"
    assert head.read("source/b.h").startswith("// BOM header")
    assert head.read("nope.txt") is None
    assert head.list(("source",), (".cpp",)) == ["source/a.cpp"]
    assert [p for p, _t in head.files(("source", "docs"))] == ["docs/x.json", "source/a.cpp", "source/b.h"]
    # partially consumed reader must not hang or leak
    it = head.files(("source",))
    next(it)
    it.close()
    assert _git(repo, "status", "--porcelain") == ""
    assert not (repo / ".git" / "index.lock").exists()


def test_git_tree_errors(repo: Path, tmp_path: Path):
    with pytest.raises(SatkError) as e:
        GitTree(repo, "no-such-ref")
    assert e.value.code in ("EXTERNAL_TOOL", "NOT_FOUND")
    with pytest.raises(SatkError):
        GitTree(tmp_path / "not-a-repo", "HEAD")


def test_git_env_disables_lazy_fetch():
    env = git_env()
    assert env["GIT_NO_LAZY_FETCH"] == "1" and env["GIT_OPTIONAL_LOCKS"] == "0" and env["GIT_TERMINAL_PROMPT"] == "0"


def test_dir_tree(tmp_path: Path):
    root = tmp_path / "d"
    (root / "s").mkdir(parents=True)
    (root / "s" / "x.h").write_text("x", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("ignored", encoding="utf-8")
    t = DirTree(root)
    assert t.list() == ["s/x.h"] and t.read("s/x.h") == "x" and t.rev.startswith("dir:")
    rev = t.rev
    (root / "s" / "y.h").write_text("y", encoding="utf-8")
    assert DirTree(root).rev != rev
    with pytest.raises(SatkError):
        DirTree(tmp_path / "missing")


def test_decode_variants():
    assert decode(b"\xef\xbb\xbfabc") == "abc"
    assert decode("hé".encode("utf-16")) == "hé"
    assert decode(b"bad \xff byte") == "bad � byte"
