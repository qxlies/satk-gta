"""dev.release registration, the portable/app_control doctor checks and the sandbox config (M3 A1)."""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from satk.core import config as C
from satk.core import registry as R
from satk.release import ops as O
from satk.release import sandbox as S


def test_release_op_is_cli_only():
    spec = R.get_op("dev.release")
    assert spec.mcp is False and spec.group == "dev" and spec.long_running
    params = {p.name: p for p in spec.params}
    assert set(params) == {"ref", "out", "smoke", "game", "offline", "relock", "sandbox", "public", "repo"}
    assert params["smoke"].choices == ("none", "quick", "game") and params["smoke"].default == "quick"
    assert params["ref"].default == "HEAD" and all(p.has_default for p in params.values())
    from satk.mcp.generic import denial

    code, reason = denial(spec)
    assert code == "UNSUPPORTED" and "download" in reason  # agents cannot start a release through satk_op


def test_doctor_checks_registered():
    checks = R.doctor_checks()
    assert {"portable", "app_control"} <= set(checks)


def test_portable_check_outside_a_portable_install(monkeypatch):
    monkeypatch.setattr(C, "PORTABLE_ROOT", None)
    assert O._check_portable()["status"] == "ok"


def _portable(tmp_path: Path, monkeypatch, version: str | None = None) -> Path:
    from satk import __version__

    root = tmp_path / "portable"
    (root / "python").mkdir(parents=True, exist_ok=True)
    (root / "satk.cmd").write_text("@echo off\r\n", encoding="ascii")
    (root / "python" / "python.exe").write_bytes(b"MZ")
    (root / "RELEASE.json").write_text(json.dumps({"version": version or __version__, "commit": "abc"}),
                                       encoding="utf-8")
    monkeypatch.setattr(C, "PORTABLE_ROOT", root)
    return root


def test_portable_check_ok_and_version_mismatch(tmp_path, monkeypatch):
    root = _portable(tmp_path, monkeypatch)
    res = O._check_portable()
    assert res["status"] == "ok" and "abc" in res["msg"]
    assert (root / "work").is_dir() and not (root / "work" / ".write-test").exists()
    _portable(tmp_path, monkeypatch, version="0.0.1")
    res = O._check_portable()
    assert res["status"] == "warn" and "0.0.1" in res["msg"]


@pytest.mark.skipif(os.name != "nt", reason="NTFS alternate data streams")
def test_portable_check_sees_the_mark_of_the_web(tmp_path, monkeypatch):
    root = _portable(tmp_path, monkeypatch)
    try:
        with open(str(root / "satk.cmd") + ":Zone.Identifier", "w", encoding="ascii") as f:
            f.write("[ZoneTransfer]\r\nZoneId=3\r\n")
    except OSError:
        pytest.skip("the temp drive has no alternate data streams")
    res = O._check_portable()
    assert res["status"] == "warn" and "Unblock-File" in res["fix"]


def test_app_control_check(monkeypatch):
    monkeypatch.setattr(O, "smart_app_control", lambda: "off")
    assert O._check_app_control()["status"] == "ok"
    monkeypatch.setattr(O, "smart_app_control", lambda: "on")
    monkeypatch.setattr(O, "blocked_extensions", lambda: [])
    assert O._check_app_control()["status"] == "ok"
    monkeypatch.setattr(O, "blocked_extensions", lambda: ["numpy", "pydantic-core (MCP server)"])
    res = O._check_app_control()
    assert res["status"] == "warn" and "numpy" in res["msg"] and "never changes it" in res["fix"]


def test_blocked_extensions_only_reports_policy_blocks(monkeypatch):
    import importlib

    def fake(name):
        if name.startswith("numpy"):
            raise ImportError("DLL load failed while importing _multiarray_umath: An Application Control policy "
                              "has blocked this file.")
        raise ImportError("No module named x")

    monkeypatch.setattr(importlib, "import_module", fake)
    assert O.blocked_extensions() == ["numpy"]


def test_wsb_config(tmp_path):
    rel, game, res = tmp_path / "rel & co", tmp_path / "game", tmp_path / "res"
    root = ET.fromstring(S.wsb_config(rel, game, res))
    assert root.findtext("Networking") == "Disable"
    maps = [(m.findtext("HostFolder"), m.findtext("SandboxFolder"), m.findtext("ReadOnly"))
            for m in root.iter("MappedFolder")]
    assert maps == [(str(rel), S.RELEASE_DIR, "true"), (str(game), S.GAME_DIR, "true"),
                    (str(res), S.RESULTS_DIR, "false")]
    assert "check.ps1" in root.findtext("LogonCommand/Command")
    assert ET.fromstring(S.wsb_config(rel, game, res, logon=False)).find("LogonCommand") is None


def test_sandbox_script_is_ascii(repo_root):
    raw = (repo_root / S.SCRIPT).read_bytes()
    raw.decode("ascii")  # Windows PowerShell 5.1 reads BOM-less files in the ANSI code page
    text = raw.decode("ascii")
    for word in ("result.json", "mcp_ok", "smart_app_control", "index', 'build", "ExtractToDirectory"):
        assert word in text, word


def test_prepare_writes_the_check_files(tmp_path):
    zip_path = tmp_path / "satk-9.9.9-win64.zip"
    zip_path.write_bytes(b"PK")
    out = S.prepare_and_run(zip_path, tmp_path / "game", tmp_path / "sandbox", b"# script\r\n", run=False)
    assert (tmp_path / "sandbox" / "check.ps1").read_bytes() == b"# script\r\n"
    assert (tmp_path / "sandbox" / "results").is_dir()
    wsb = (tmp_path / "sandbox" / "satk-check.wsb").read_text(encoding="utf-8")
    assert ET.fromstring(wsb).findtext("Networking") == "Disable"
    assert out["wsb"].endswith("satk-check.wsb") and "double-click" in out["next"]
