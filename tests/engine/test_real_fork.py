"""Checks against the real ``<workspace>/engine/mtasa`` (marker ``engine``; skipped when the fork is absent).

Read-only except ``rc_test`` (writes .res files under work/engine/build/rc-test) and the
``slow`` no-op build of one project.
"""

from __future__ import annotations

import os

import pytest

from satk.core import config as _config

pytestmark = pytest.mark.engine


@pytest.fixture(autouse=True)
def _real_config():
    _config.reset()
    yield
    _config.reset()


def test_fork_git_setup():
    from satk.engine.common import EXPECTED_BASE, LOCAL_UPSTREAM_REF, file_url, git, layout
    from satk.engine.setup import fork_info

    L = layout()
    info = fork_info(L)
    assert info["exists"] and info["branch"] == "main"
    assert EXPECTED_BASE.startswith(info["base"])
    assert info["remotes"]["neon"] == {"fetch": file_url(L.donor), "push": "DISABLED"}
    assert info["remotes"]["upstream"]["push"] == "DISABLED"
    assert info["remotes"]["upstream"]["fetch"] == "https://github.com/multitheftauto/mtasa-blue.git"
    assert info["upstream_fetched"] is False  # consent D4 not given
    assert info["rerere"] is True
    assert "origin" not in info["remotes"]  # D5 not given
    assert git("config", "--get", "remote.upstream.skipFetchAll", cwd=L.fork) == "true"
    git("merge-base", "--is-ancestor", LOCAL_UPSTREAM_REF, "main", cwd=L.fork)  # raises if main is not on top


def test_donor_untouched():
    from satk.engine.common import donor_git

    assert donor_git("status", "--porcelain") == ""


def test_no_directory_build_files_in_fork_and_shims_outside():
    from satk.engine.common import layout
    from satk.engine.doctor import CHECKS
    from satk.engine.setup import template_status

    L = layout()
    assert CHECKS["no_dbuild_in_fork"](L, False)["status"] == "ok"
    assert L.targets.is_file() and L.afxres.is_file()
    assert {t["status"] for t in template_status(L)} == {"ok"}


def test_rc_test_with_shim():
    from satk.engine.build import RC_FILES, rc_test

    out = rc_test(control=True)
    rows = {(r[0], r[1]): r for r in out["rows"]}
    for rc in RC_FILES:
        assert rows[(rc, "shim")][2] is True, rows[(rc, "shim")]
    ctl = rows[(RC_FILES[0], "control(no shim)")]
    assert ctl[2] is False and "afxres.h" in ctl[5]


def test_deps_pinned_and_installed():
    from satk.engine.common import layout
    from satk.engine.doctor import CHECKS

    L = layout()
    assert CHECKS["deps_lock"](L, False)["status"] == "ok"
    assert CHECKS["deps_installed"](L, False)["status"] == "ok"
    assert CHECKS["dxfiles"](L, False)["status"] == "ok"


def test_solution_layout():
    from satk.engine.build import parse_sln, plan
    from satk.engine.common import layout

    L = layout()
    if not L.sln.is_file():
        pytest.skip("Build/MTASA.sln not generated")
    sln = parse_sln(L.sln)
    assert sln["Game SA"]["builds"] >= {"Release|Win32"} and "Release|x64" not in sln["Game SA"]["builds"]
    assert "Release|x64" in sln["Core"]["builds"]
    steps, _ = plan("Game SA", None, "Release", L)
    assert [(s.label, s.platform) for s in steps] == [("Game SA", "Win32")]
    steps, _ = plan("Deathmatch", None, "Release", L)
    assert [s.platform for s in steps] == ["x64"]


@pytest.mark.slow
def test_noop_build_of_one_project():
    from satk.engine.build import build
    from satk.engine.common import layout

    L = layout()
    if not (L.bin / "mta" / "game_sa.dll").is_file():
        pytest.skip("client not built yet")
    out = build(project="Game SA", platform="Win32")
    assert out["ok"] and out["rows"][0][3] == 0 and out["errors_total"] == 0


def test_engine_dir_size_budget():
    from satk.engine.common import layout

    total = 0
    for dirpath, _, files in os.walk(layout().root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    assert total < 16e9, f"engine is {total / 1e9:.1f} GB (budget 16 GB)"
