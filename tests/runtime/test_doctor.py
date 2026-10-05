"""``satk doctor`` (SPEC §4.7, WP-06 acceptance 4)."""

from __future__ import annotations

import os
import re
import sys

import pytest

from satk.core import registry as R
from satk.runtime import doctor as D
from satk.runtime.sysinfo import drive_of, file_version, free_gb, run_version

REQUIRED = {"python", "venv", "deps", "utf8", "paths", "disk", "network", "blender", "msbuild", "premake", "git",
            "ops_import"}


def test_doctor_json_has_required_checks(satk_home, run_cli):
    r = run_cli(["doctor", "--json"])
    assert r.code == 0
    env = r.json
    names = [c["name"] for c in env["checks"]]
    assert REQUIRED <= set(names)
    assert names[: len(D.ORDER) - 1] == [n for n in D.ORDER if n in names][: len(D.ORDER) - 1]
    for c in env["checks"]:
        assert c["status"] in D.STATUSES and isinstance(c["msg"], str)
    assert env["counts"] == {s: sum(c["status"] == s for c in env["checks"]) for s in D.STATUSES}
    py = next(c for c in env["checks"] if c["name"] == "python")
    assert py["status"] == "ok" and py["version"].rsplit(".", 1)[0] in ("3.12", "3.13", "3.14")


def test_no_index_means_warn_with_fix(satk_home, run_cli):
    env = run_cli(["doctor", "--json"]).json
    idx = next(c for c in env["checks"] if c["name"] in ("index", "index_fresh"))
    assert idx["status"] == "warn" and idx["fix"] == "satk index build"
    assert env["status"] in ("warn", "fail")


def test_index_present_is_ok(satk_home):
    f = satk_home / "work" / "index" / "vanilla.sqlite"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 1024)
    res = D.check_index()
    assert res["status"] == "ok" and "vanilla index" in res["msg"]


def test_index_metadata_uses_decimal_mb_and_explicit_utc(satk_home):
    f = satk_home / "work" / "index" / "vanilla.sqlite"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 1_234_567)
    os.utime(f, (1_700_000_000, 1_700_000_000))
    res = D.check_index()
    assert res["status"] == "ok"
    assert res["msg"] == "vanilla index 1.2 MB, modified 2023-11-14T22:13:20Z"


def test_only_filter_and_unknown(run_cli):
    env = run_cli(["doctor", "--only", "python,utf8"]).json
    assert [c["name"] for c in env["checks"]] == ["python", "utf8"]
    r = run_cli(["doctor", "--only", "pyhton"])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS" and "python" in r.json["error"]["did_you_mean"]


@pytest.mark.parametrize("free,expect", [
    ({"D:": 39.0, "C:": 14.0}, "ok"),
    ({"D:": 9.9, "C:": 14.0}, "warn"),
    ({"D:": 39.0, "C:": 2.9}, "warn"),
    ({"D:": None, "C:": 14.0}, "ok"),
])
def test_disk_verdict(free, expect):
    status, msg = D.disk_verdict(free, "D:", "C:")
    assert status == expect
    if expect == "warn":
        assert "<" in msg


def test_disk_verdict_same_drive():
    assert D.disk_verdict({"C:": 5.0}, "C:", "C:")[0] == "warn"
    assert D.disk_verdict({"C:": 11.0}, "C:", "C:")[0] == "ok"


def test_deps_verdict():
    pins = {"pillow": "12.3.0", "numpy": "2.5.3", "mcp": "2.3.0", "pytest": "8.4.2"}
    assert D.deps_verdict(dict(pins), pins)[0] == "ok"
    st, msg = D.deps_verdict({**pins, "mcp": None}, pins)
    assert st == "warn" and "mcp missing" in msg
    st, msg = D.deps_verdict({**pins, "numpy": "2.0.0"}, pins)
    assert st == "warn" and "lock 2.5.3" in msg


def test_deps_verdict_pytest_is_development_only():
    pins = {"pillow": "12.3.0", "numpy": "2.5.3", "mcp": "2.3.0", "pytest": "8.4.2"}
    st, msg = D.deps_verdict({**pins, "pytest": None}, pins)
    assert st == "ok" and "pytest not installed (development only" in msg


@pytest.mark.parametrize("ver,status", [((3, 11), "fail"), ((3, 12), "ok"), ((3, 13), "ok"), ((3, 14), "ok"),
                                        ((3, 15), "warn")])
def test_python_verdict(ver, status):
    st, note = D.python_verdict(*ver)
    assert st == status and "3.12-3.14" in note


def test_network_line(satk_home, run_cli):
    env = run_cli(["doctor", "--only", "network", "--json"]).json
    (c,) = env["checks"]
    assert c["status"] == "ok" and c["msg"].startswith("none by default")
    tty = run_cli(["doctor", "--only", "network"], tty=True).out
    assert re.search(r"^\s+ok\s+network: none by default", tty, re.M), tty


def test_network_deep_scan_is_clean(satk_home, run_cli):
    env = run_cli(["doctor", "--only", "network", "--deep", "--json"]).json
    (c,) = env["checks"]
    assert c["status"] == "ok" and "all allowed" in c["msg"], c


def test_paths_fail_when_the_workspace_is_inside_a_game(tmp_path, monkeypatch, run_cli):
    from satk.core import config as C

    game = tmp_path / "GTA San Andreas"
    (game / "models").mkdir(parents=True)
    (game / "data").mkdir()
    (game / "gta_sa.exe").write_bytes(b"MZ")
    (game / "models" / "gta3.img").write_bytes(b"VER2")
    (game / "data" / "gta.dat").write_text("x", encoding="utf-8")
    ws = game / "satk"
    (ws / "work").mkdir(parents=True)
    for k in list(os.environ):
        if k.startswith("SATK_") and k not in ("SATK_LOG", "SATK_TEST_NO_SKIP"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SATK_HOME", str(ws))
    monkeypatch.setenv("SATK_CONFIG", "none")
    C.reset()
    try:
        (c,) = run_cli(["doctor", "--only", "paths", "--json"]).json["checks"]
    finally:
        C.reset()
    assert c["status"] == "fail" and "inside the game folder" in c["msg"] and "satk init --workspace" in c["fix"]


def test_doctor_table_is_a_checklist(satk_home, run_cli):
    out = run_cli(["doctor", "--only", "python,index"], tty=True).out
    lines = out.splitlines()
    assert lines[0].startswith("status: ") and "checks:" in lines
    assert any(re.match(r"^\s+ok\s+python: CPython", ln) for ln in lines), out
    assert any(ln.strip() == "fix: satk index build" for ln in lines), out
    assert '{"name"' not in out  # no JSON blob for people


def test_crashing_and_malformed_checks_do_not_break_doctor(satk_home, isolated_ops):
    @R.doctor_check("boom")
    def _boom():
        raise RuntimeError("kaputt")

    @R.doctor_check("weird")
    def _weird():
        return "ok"

    @R.doctor_check("fine")
    def _fine():
        return {"status": "ok", "msg": "fine", "fix": None, "extra": 1}

    res = D.run()
    by = {c["name"]: c for c in res["checks"]}
    assert by["boom"]["status"] == "fail" and "kaputt" in by["boom"]["msg"]
    assert by["weird"]["status"] == "fail"
    assert by["fine"] == {"name": "fine", "status": "ok", "msg": "fine", "extra": 1}
    assert res["status"] == "fail"


def test_owner_check_wins_over_runtime_check(isolated_ops):
    """A package that registered a check name first keeps it (runtime yields, no import error)."""

    def owner():
        return {"status": "ok", "msg": "owner", "fix": None}

    R.doctor_check("blender")(owner)
    D._register("blender")(D.check_blender)  # what importing runtime.doctor does
    assert R.doctor_checks()["blender"] is owner


def test_mcp_config_check(satk_home, run_cli):
    import json

    assert D.check_mcp_config()["status"] == "warn"
    run_cli(["mcp", "config", "--write"])
    # the entry runs this checkout's interpreter (PORTABILITY §7), which exists
    assert D.check_mcp_config()["status"] == "ok"
    f = satk_home / ".mcp.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    data["mcpServers"]["satk"]["command"] = str(satk_home / "tools" / ".venv" / "Scripts" / "python.exe")
    f.write_text(json.dumps(data), encoding="utf-8")
    # a command path that does not exist -> still a warning, but specific
    res = D.check_mcp_config()
    assert res["status"] == "warn" and "does not exist" in res["msg"]


def test_mcp_config_check_bom_and_garbage(satk_home, run_cli):
    f = satk_home / ".mcp.json"
    run_cli(["mcp", "config", "--write"])
    f.write_bytes(b"\xef\xbb\xbf" + f.read_bytes())
    res = D.check_mcp_config()
    assert res["status"] == "warn" and "BOM" in res["msg"] and res["fix"] == "satk mcp config --write"
    f.write_text("{oops", encoding="utf-8")
    res = D.check_mcp_config()
    assert res["status"] == "warn" and "not valid JSON" in res["msg"] and "--write" in res["fix"]


@pytest.mark.parametrize("raw,status", [(None, "ok"), ("core,index", "ok"), ("all", "ok"), ("cor", "fail")])
def test_mcp_groups_check(monkeypatch, raw, status):
    if raw is None:
        monkeypatch.delenv("SATK_MCP_GROUPS", raising=False)
    else:
        monkeypatch.setenv("SATK_MCP_GROUPS", raw)
    res = D.check_mcp_groups()
    assert res["status"] == status
    if status == "fail":
        assert "cor" in res["msg"] and "core" in res["msg"] and "SATK_MCP_GROUPS" in res["fix"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows version resources")
def test_file_version_of_python():
    v = file_version(sys.executable)
    assert v is None or re.match(r"^\d+\.\d+", v)
    assert file_version(r"C:\definitely\missing.exe") is None


def test_sysinfo_helpers(tmp_path):
    assert drive_of(r"D:\ws\GTA").upper() == "D:"
    assert free_gb(tmp_path / "not" / "there") is not None
    assert run_version(["definitely-not-a-tool-xyz", "--version"]) is None
    assert run_version([sys.executable, "--version"]).startswith("Python 3.")
