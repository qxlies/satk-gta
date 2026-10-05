"""Acceptance-mode handling of missing real-game fixtures, using a temporary workspace."""

from __future__ import annotations

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
