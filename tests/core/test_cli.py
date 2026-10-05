"""The generated CLI: output modes, exit codes, core operations (SPEC §3.3, §4.6, WP-00 acceptance)."""

from __future__ import annotations

import satk
import io
import json
import platform

import pytest

from satk.core import registry as R
from satk.core.cli import main
from satk.core.errors import SatkError


def test_version_json(run_cli, repo_root):
    from satk.core.paths import jpath

    r = run_cli(["version"])
    assert r.code == 0 and r.err == ""
    d = r.json
    assert d["ok"] is True and d["satk"] == satk.__version__ and d["python"] == platform.python_version()
    assert d["repo"] == jpath(repo_root) and d["src"] == jpath(repo_root / "src")
    assert r.out.startswith('{"ok":true,') and r.out.count("\n") == 1  # compact, one line


def test_version_table_and_tty_default(run_cli):
    t = run_cli(["version", "--table"])
    assert t.code == 0 and f"satk: {satk.__version__}" in t.out
    tty = run_cli(["version"], tty=True)
    assert f"satk: {satk.__version__}" in tty.out  # TTY -> table by default
    forced = run_cli(["version", "--json"], tty=True)
    assert json.loads(forced.out)["ok"] is True


def test_root_listing_contains_core_ops(run_cli):
    r = run_cli([])
    cmds = [row[0] for row in r.json["rows"]]
    for c in ("satk version", "satk config show", "satk dev guard-test", "satk dev assetguard"):
        assert c in cmds
    sub = run_cli(["dev"])
    assert {row[0] for row in sub.json["rows"]} >= {"satk dev guard-test", "satk dev assetguard"}


def test_unknown_command_suggests(run_cli):
    r = run_cli(["versoin"])
    assert r.code == 2
    err = r.json["error"]
    assert err["code"] == "BAD_PARAMS" and "satk version" in err["did_you_mean"]
    assert run_cli(["dev", "guard-tset"]).code == 2


@pytest.mark.parametrize("root", ["GTA San Andreas", "src", "gta-sa-clean"])
def test_guard_test_protected(run_cli, satk_home, root):
    import os

    path = str(satk_home / root / "x.tmp")
    r = run_cli(["dev", "guard-test", path])
    assert r.code == 1 and r.json["error"]["code"] == "PROTECTED_PATH"
    assert not os.path.exists(path)


def test_guard_test_allowed(run_cli, satk_home):
    target = satk_home / "work" / "x.tmp"
    r = run_cli(["dev", "guard-test", str(target)])
    assert r.code == 0 and r.json["writable"] is True and not target.exists()


def test_guard_test_remove_checks_protected_descendants(run_cli, satk_home):
    r = run_cli(["dev", "guard-test", str(satk_home), "--remove"])
    assert r.code == 1 and r.json["error"]["code"] == "PROTECTED_PATH"
    target = satk_home / "work" / "discard"
    r = run_cli(["dev", "guard-test", str(target), "--remove"])
    assert r.code == 0 and r.json["removable"] is True
    assert not target.exists()


def test_errors_go_to_stderr_in_table_mode(run_cli, satk_home):
    r = run_cli(["dev", "guard-test", str(satk_home / "src" / "x.tmp"), "--table"])
    assert r.code == 1 and r.out == "" and "error PROTECTED_PATH" in r.err and "hint:" in r.err


def test_quiet_timing_help(run_cli):
    q = run_cli(["version", "-q"])
    assert q.code == 0 and q.out == ""
    t = run_cli(["version", "--timing"])
    assert "[timing]" in t.err and t.json["ok"]
    h = run_cli(["dev", "guard-test", "-h"])
    assert h.code == 0 and "usage: satk dev guard-test" in h.out


def test_generic_fields_flag(run_cli):
    r = run_cli(["version", "--fields", "satk,python"])
    assert set(r.json) == {"ok", "satk", "python"}
    r2 = run_cli(["version", "--fields=python"])
    assert set(r2.json) == {"ok", "python"}
    assert run_cli(["version", "--fields", "zzz"]).code == 2


def test_config_show(run_cli, satk_home):
    r = run_cli(["config", "show"])
    d = r.json
    assert r.code == 0 and d["paths"]["game"] == satk_home.as_posix() + "/gta-sa-clean"
    assert d["sources"][0] == "defaults"
    s = run_cli(["config", "show", "--section", "profiles"])
    assert s.json["section"] == "profiles" and "samp" in s.json["profiles"]
    assert run_cli(["config", "show", "--section", "nope"]).code == 2


def test_cyrillic_paths_on_cp1251_streams(satk_home, monkeypatch):
    monkeypatch.setenv("SATK_PATHS_BLENDER", r"D:\Тест\blender ø.exe")
    from satk.core import config

    config.reset()
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp1251", errors="strict", newline="\n")
    err = io.StringIO()
    code = main(["config", "show", "--table"], stdout=out, stderr=err)
    out.flush()
    text = raw.getvalue().decode("cp1251")
    assert code == 0 and "D:/Тест/blender ?.exe" in text  # ø is not in cp1251 -> replaced
    raw2 = io.BytesIO()
    out2 = io.TextIOWrapper(raw2, encoding="cp1251", errors="strict")
    assert main(["config", "show", "--json"], stdout=out2, stderr=err) == 0
    out2.flush()
    d = json.loads(raw2.getvalue().decode("ascii"))  # JSON on non-UTF streams is ASCII-escaped
    assert d["paths"]["blender"] == "D:/Тест/blender ø.exe"


def test_exit_code_3_for_not_ready(isolated_ops, run_cli):
    @R.op("t.idx", summary="needs an index", summary_ru="нужен индекс")
    def idx(profile: str = "vanilla") -> dict:
        raise SatkError("INDEX_MISSING", f"no index for {profile}", hint="satk index build")

    @R.op("t.sum", summary="adds", summary_ru="сложить")
    def add(a: int, b: int = 0, neg: list[float] | None = None) -> dict:
        return {"sum": a + b, "neg": neg}

    r = run_cli(["t", "idx", "--profile", "samp"])
    assert r.code == 3 and r.json["error"]["msg"] == "no index for samp"
    assert run_cli(["t", "sum", "2", "--b", "-5"]).json["sum"] == -3
    assert run_cli(["t", "sum", "1", "--neg", "-1,-2.5"]).json["neg"] == [-1.0, -2.5]
    assert run_cli(["t", "sum"]).code == 2
    assert run_cli(["t", "sum", "x"]).code == 2


def test_internal_error_envelope(isolated_ops, run_cli, satk_home):
    @R.op("t.crash", summary="crash", summary_ru="падение")
    def crash() -> dict:
        raise ZeroDivisionError("nope")

    r = run_cli(["t", "crash"])
    assert r.code == 1 and r.json["error"]["code"] == "INTERNAL" and "errors.log" in r.json["error"]["hint"]


@pytest.mark.parametrize("data", [b"\xff", b'{"key":"\xc3"}'])
def test_invalid_utf8_json_argument_file_is_bad_params(run_cli, satk_home, tmp_path, data):
    args = tmp_path / "invalid-utf8.json"
    args.write_bytes(data)
    r = run_cli(["engine", "run", "status", "--args", "@" + str(args)])
    assert r.code == 2
    assert r.json["ok"] is False and r.json["error"]["code"] == "BAD_PARAMS"
    assert args.name in r.json["error"]["msg"]
    assert r.out.count("\n") == 1
