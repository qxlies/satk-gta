"""Engine writing boundaries must reject protected temporary checkouts and outputs."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from satk.core.errors import SatkError
from satk.engine import build as B
from satk.engine import setup as S
from satk.engine.common import layout


@pytest.mark.parametrize("operation", ["setup", "gen", "build"])
@pytest.mark.parametrize("alias", [False, True])
def test_existing_protected_checkout_never_reaches_tools(satk_home, tmp_path, monkeypatch,
                                                       make_junction, operation, alias):
    L = layout()
    protected = satk_home / "src" / "checkout"
    (protected / ".git").mkdir(parents=True)
    (protected / "premake5.lua").write_text("-- synthetic\n", encoding="utf-8")
    (protected / "Build").mkdir()
    (protected / "Build" / "MTASA.sln").write_text("synthetic\n", encoding="utf-8")
    fork = make_junction(tmp_path / "alias", protected) if alias else protected
    L = replace(L, fork=fork, root=fork.parent)
    tools = Mock(side_effect=AssertionError("a writing tool was reached"))
    monkeypatch.setattr(S, "git", tools)
    monkeypatch.setattr(B, "git", tools)
    monkeypatch.setattr(B, "run", tools)
    monkeypatch.setattr(B, "layout", lambda: L)
    monkeypatch.setattr(B, "find_premake", lambda _: Path("premake.exe"))
    monkeypatch.setattr(B, "find_msbuild", lambda: Path("MSBuild.exe"))
    monkeypatch.setattr(B, "exclusive", lambda _: nullcontext())
    monkeypatch.setattr(B, "build_env", lambda: {})
    with pytest.raises(SatkError) as exc:
        if operation == "setup":
            S.ensure_fork(L)
        elif operation == "gen":
            B.gen(L)
        else:
            B.build(project="server", regen="never")
    assert exc.value.code == "PROTECTED_PATH"
    tools.assert_not_called()
    assert not L.build_dir.exists()


@pytest.mark.parametrize("operation", ["gen", "build"])
@pytest.mark.parametrize("output", ["Build", "Bin", "logs"])
def test_protected_output_junction_is_guarded_before_tools(satk_home, monkeypatch, make_junction,
                                                         operation, output):
    L = layout()
    L.fork.mkdir(parents=True)
    (L.fork / "premake5.lua").write_text("-- synthetic\n", encoding="utf-8")
    protected = satk_home / "src"
    protected.mkdir()
    link = L.logs if output == "logs" else L.fork / output
    link.parent.mkdir(parents=True, exist_ok=True)
    make_junction(link, protected)
    if output != "Build":
        L.sln.parent.mkdir()
    L.sln.write_text("synthetic\n", encoding="utf-8")
    tools = Mock(side_effect=AssertionError("a writing tool was reached"))
    monkeypatch.setattr(B, "run", tools)
    monkeypatch.setattr(B, "git", tools)
    monkeypatch.setattr(B, "layout", lambda: L)
    monkeypatch.setattr(B, "find_premake", lambda _: Path("premake.exe"))
    monkeypatch.setattr(B, "find_msbuild", lambda: Path("MSBuild.exe"))
    monkeypatch.setattr(B, "exclusive", lambda _: nullcontext())
    monkeypatch.setattr(B, "build_env", lambda: {})
    with pytest.raises(SatkError) as exc:
        if operation == "gen":
            B.gen(L)
        else:
            B.build(project="server", regen="never")
    assert exc.value.code == "PROTECTED_PATH"
    tools.assert_not_called()


def test_git_metadata_junction_is_guarded(satk_home, monkeypatch, make_junction):
    L = layout()
    L.fork.mkdir(parents=True)
    protected = satk_home / "src"
    protected.mkdir()
    make_junction(L.fork / ".git", protected)
    git = Mock(side_effect=AssertionError("git was reached"))
    monkeypatch.setattr(S, "git", git)
    with pytest.raises(SatkError) as exc:
        S.ensure_fork(L)
    assert exc.value.code == "PROTECTED_PATH"
    git.assert_not_called()


@pytest.mark.parametrize("pointer", ["gitdir", "commondir"])
def test_worktree_metadata_pointer_is_guarded(satk_home, monkeypatch, pointer):
    L = layout()
    L.fork.mkdir(parents=True)
    protected = satk_home / "src"
    protected.mkdir()
    if pointer == "gitdir":
        (L.fork / ".git").write_text("gitdir: " + str(protected), encoding="utf-8")
    else:
        metadata = L.fork / ".git"
        metadata.mkdir()
        (metadata / "commondir").write_text(str(protected), encoding="utf-8")
    git = Mock(side_effect=AssertionError("git was reached"))
    monkeypatch.setattr(S, "git", git)
    with pytest.raises(SatkError) as exc:
        S.ensure_fork(L)
    assert exc.value.code == "PROTECTED_PATH"
    git.assert_not_called()
