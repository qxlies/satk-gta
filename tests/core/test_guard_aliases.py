"""UNC identities are simulated against temporary trees; no SMB writes or real assets."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from satk.core import config
from satk.core import paths as P
from satk.core.errors import SatkError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows UNC paths")


@pytest.fixture(params=[
    ("localhost", "D$", False),
    ("127.0.0.1", "D$", False),
    ("localhost", "D$", True),
    ("127.0.0.1", "D$", True),
    ("satk-test", "fixture", False),
], ids=["localhost", "loopback-ip", "extended-localhost", "extended-ip", "custom-share"])
def unc_alias(satk_home, monkeypatch, request):
    """Simulate a share backed by satk_home, whose realpath keeps the UNC namespace.

    Windows returns different final path strings for local and SMB handles, even
    when their file identities match. All alias metadata reads below use the fixture.
    """
    host, share, extended = request.param
    prefix = f"\\\\{host}\\{share}"
    realpath, stat = os.path.realpath, os.stat

    def mapped(path):
        if not isinstance(path, (str, os.PathLike)):
            return None
        raw = os.fspath(path).replace("/", "\\")
        if raw.lower().startswith("\\\\?\\unc\\"):
            raw = "\\\\" + raw[8:]
        if raw.lower().rstrip("\\") == prefix.lower():
            return satk_home
        if raw.lower().startswith(prefix.lower() + "\\"):
            target = satk_home / raw[len(prefix) + 1:]
            assert target.is_relative_to(satk_home)
            return target
        return None

    def resolve(path, **kwargs):
        return os.fspath(path) if mapped(path) is not None else realpath(path, **kwargs)

    def stat_alias(path, *args, **kwargs):
        local = mapped(path)
        return stat(local if local is not None else path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", resolve)
    monkeypatch.setattr(os, "stat", stat_alias)

    def alias(path):
        rel = Path(path).relative_to(satk_home)
        raw = prefix if rel == Path(".") else prefix + "\\" + str(rel)
        return "\\\\?\\UNC\\" + raw[2:] if extended else raw

    return alias


@pytest.mark.parametrize("exists", [False, True], ids=["missing-root", "existing-root"])
def test_unc_alias_cannot_bypass_protection(satk_home, unc_alias, exists):
    root = satk_home / "src"
    if exists:
        root.mkdir()
    path = unc_alias(root / "missing" / "out.txt")
    assert P.is_protected(path) == P.jpath(root)
    with pytest.raises(SatkError) as exc:
        P.ensure_writable(path)
    assert exc.value.code == "PROTECTED_PATH"
    assert not (root / "missing").exists()


def test_unc_alias_respects_game_writer(satk_home, unc_alias):
    clean = satk_home / "gta-sa-clean" / "out.txt"
    with P.game_writer():
        assert P.is_protected(unc_alias(clean)) is None
        for name in ("src", "GTA San Andreas"):
            assert P.is_protected(unc_alias(satk_home / name / "out.txt")) is not None
    assert P.is_protected(unc_alias(clean)) is not None


def test_protected_unc_root_covers_local_spelling(satk_home, unc_alias, monkeypatch):
    root = satk_home / "precious"
    monkeypatch.setenv("SATK_SAFETY_PROTECTED_ROOTS", json.dumps([unc_alias(root)]))
    config.reset()
    assert P.is_protected(root / "out.txt") == P.jpath(unc_alias(root))


def test_game_writer_unlocks_a_configured_unc_alias(satk_home, unc_alias, monkeypatch):
    root = satk_home / "gta-sa-clean"
    monkeypatch.setenv("SATK_SAFETY_PROTECTED_ROOTS", json.dumps([unc_alias(root)]))
    config.reset()
    with P.game_writer():
        assert P.is_protected(unc_alias(root / "out.txt")) is None
    assert P.is_protected(unc_alias(root / "out.txt")) is not None


def test_unc_game_writer_root_unlocks_a_local_protected_path(satk_home, unc_alias, monkeypatch):
    root = satk_home / "precious"
    monkeypatch.setenv("SATK_PATHS_GAME", unc_alias(root))
    monkeypatch.setenv("SATK_SAFETY_PROTECTED_ROOTS", json.dumps([str(root)]))
    config.reset()
    with P.game_writer():
        assert P.is_protected(root / "out.txt") is None
    assert P.is_protected(root / "out.txt") is not None


@pytest.mark.parametrize("mapped", ["candidate", "root"])
def test_local_path_resolving_to_unc_still_checks_identity(satk_home, unc_alias, monkeypatch, mapped):
    root = satk_home / "precious"
    root.mkdir()
    alias = satk_home / "work" / "mapped"
    realpath = os.path.realpath

    def resolve(path, **kwargs):
        # A mapped drive (or a junction onto one) has a DOS spelling but an SMB final path.
        if os.path.normcase(os.fspath(path)) == os.path.normcase(str(alias)):
            return unc_alias(root)
        return realpath(path, **kwargs)

    monkeypatch.setattr(os.path, "realpath", resolve)
    protected = alias if mapped == "root" else root
    monkeypatch.setenv("SATK_SAFETY_PROTECTED_ROOTS", json.dumps([str(protected)]))
    config.reset()
    candidate = root / "out.txt" if mapped == "root" else alias
    assert P.is_protected(candidate) == P.jpath(protected)


@pytest.mark.parametrize("guard", ["ensure_writable", "ensure_removable"])
def test_unc_identity_errors_fail_closed(satk_home, unc_alias, monkeypatch, guard):
    stat = os.stat

    def denied(path, *args, **kwargs):
        if isinstance(path, (str, os.PathLike)) and os.fspath(path).replace("/", "\\").startswith("\\\\"):
            raise PermissionError("simulated unavailable UNC metadata")
        return stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", denied)
    with pytest.raises(SatkError, match="cannot inspect path identity safely") as exc:
        getattr(P, guard)(unc_alias(satk_home / "work" / "out.txt"))
    assert exc.value.code == "BAD_PARAMS"


def test_unc_ancestor_cannot_be_removed(satk_home, unc_alias):
    with pytest.raises(SatkError) as exc:
        P.ensure_removable(unc_alias(satk_home))
    assert exc.value.code == "PROTECTED_PATH"


def test_unprotected_unc_sibling_is_writable(satk_home, unc_alias):
    assert P.is_protected(unc_alias(satk_home / "src-backup" / "out.txt")) is None
