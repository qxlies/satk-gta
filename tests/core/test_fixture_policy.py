"""Acceptance-mode handling of missing real-game fixtures, using a temporary workspace."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


@pytest.mark.parametrize("fixture_name", ["clean_root", "installed_root"])
@pytest.mark.parametrize("no_skip", ["", "0", "1"])
def test_missing_root_honors_no_skip(satk_home, monkeypatch, request, fixture_name, no_skip):
    monkeypatch.setenv("SATK_TEST_NO_SKIP", no_skip)
    with pytest.raises((pytest.skip.Exception, pytest.fail.Exception)) as exc:
        request.getfixturevalue(fixture_name)
    expected = pytest.fail.Exception if no_skip == "1" else pytest.skip.Exception
    assert isinstance(exc.value, expected)
    assert "root not found" in str(exc.value)


@pytest.mark.parametrize("fixture_name,dirname", [
    ("clean_root", "gta-sa-clean"), ("installed_root", "GTA San Andreas"),
])
def test_available_root_in_no_skip_mode(satk_home, monkeypatch, request, fixture_name, dirname):
    root = satk_home / dirname
    root.mkdir()
    (root / "gta_sa.exe").write_bytes(b"synthetic fixture marker")
    monkeypatch.setenv("SATK_TEST_NO_SKIP", "1")
    assert request.getfixturevalue(fixture_name) == root


def test_no_test_imports_the_bare_name_conftest():
    """A bare ``conftest`` import binds to whichever folder's conftest pytest imported last, so such a test passes or
    fails with the order of the test folders. Helpers live in a uniquely named module (``tests/re/re_synth.py``,
    ``tests/batch/synth.py``) that the folder's conftest puts on ``sys.path``."""
    pat = re.compile(r"^[ 	]*(?:from[ 	]+conftest[ 	]+import|import[ 	]+conftest)", re.M)
    bad = [f.relative_to(Path(__file__).parents[1]).as_posix() for f in Path(__file__).parents[1].rglob("*.py")
           if pat.search(f.read_text(encoding="utf-8", errors="replace"))]
    assert bad == []
