"""No game assets in the repository (SPEC §5.3 WP-12 acceptance 6, R14). Fast; needs git."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from satk.core.config import REPO_ROOT
from satk.core.registry import invoke

ASSET_EXT = (".png", ".dff", ".txd", ".img", ".col", ".ifp")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not found")
def test_no_asset_extensions_tracked():
    out = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files"], capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    bad = [f for f in out.splitlines() if f.lower().endswith(ASSET_EXT) and not f.startswith("docs/img/")]
    assert bad == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git not found")
def test_assetguard_all_clean():
    env = invoke("dev.assetguard", {"files": None, "all": True})
    assert env.get("ok") is True and env.get("violations") == 0, env
