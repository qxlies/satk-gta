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


# --------------------------------------------------------------------------- A1-L7: aliases, output options


def test_ops_alias_equals_mcp_ops(run_cli):
    a, b = run_cli(["ops", "dff"]), run_cli(["mcp", "ops", "dff"])
    assert a.code == b.code == 0 and a.json == b.json and a.json["total"] > 0
    words = run_cli(["ops", "make", "txd", "from", "png", "--limit", "5"])   # free words = one query
    assert words.code == 0 and words.json["rows"][0][0] == "texture.pack"
    one = run_cli(["op", "version"])
    assert one.code == 0 and one.json["satk"] == satk.__version__


def test_query_words_are_joined(isolated_ops, run_cli):
    @R.op("t.find", summary="find", summary_ru="поиск")
    def find(query: str, limit: int = 20) -> dict:
        return {"query": query, "limit": limit}

    r = run_cli(["t", "find", "red", "car", "--limit", "3", "fast"])
    assert r.json == {"ok": True, "query": "red car fast", "limit": 3}
    assert run_cli(["t", "find", "--", "-x"]).json["query"] == "-x"

    @R.op("t.tagged", summary="find with tags", summary_ru="поиск с метками")
    def tagged(query: str, tags: list[str] | None = None) -> dict:
        return {"query": query, "tags": tags}

    r = run_cli(["t", "tagged", "red", "car", "--tags", "a", "b"])     # a list option keeps its run of values
    assert r.json == {"ok": True, "query": "red car", "tags": ["a", "b"]}


def test_json_errors_echo_to_stderr(run_cli, satk_home):
    r = run_cli(["dev", "guard-test", str(satk_home / "src" / "x.tmp"), "--json"])
    assert r.code == 1 and r.json["error"]["code"] == "PROTECTED_PATH"
    assert r.err.startswith("error PROTECTED_PATH: ") and r.err.count("\n") == 1 and "(hint: " in r.err


def test_summary_flag(run_cli, satk_home):
    r = run_cli(["version", "--summary"])
    assert r.code == 0 and set(r.json) == {"ok", "summary"}
    assert r.json["summary"].startswith("ok: satk=") and "\n" not in r.json["summary"]
    t = run_cli(["mcp", "ops", "dff", "--limit", "3", "--summary"])
    assert t.json["summary"].startswith("ok: 3 of ") and "rows (cols op, args, summary)" in t.json["summary"]
    tab = run_cli(["version", "--summary", "--table"])
    assert tab.out.startswith("summary: ok: satk=") and tab.out.count("\n") == 1
    bad = run_cli(["dev", "guard-test", str(satk_home / "src" / "x.tmp"), "--summary"])
    assert bad.code == 1 and bad.json["error"]["code"] == "PROTECTED_PATH"   # errors stay whole


def test_out_flag_writes_the_envelope(run_cli, satk_home):
    from satk.core.paths import jpath

    dest = satk_home / "work" / "out" / "v.json"
    r = run_cli(["version", "--out", str(dest)])
    assert r.code == 0 and r.json["out"] == jpath(dest) and r.json["bytes"] == dest.stat().st_size
    full = json.loads(dest.read_text(encoding="utf-8"))
    assert full["ok"] and full["satk"] == satk.__version__ and r.json["summary"].startswith("ok: satk=")
    err_dest = satk_home / "work" / "out" / "e.json"
    e = run_cli(["dev", "guard-test", str(satk_home / "src" / "x.tmp"), "--out=" + str(err_dest)])
    assert e.code == 1 and e.json["error"]["code"] == "PROTECTED_PATH" and e.json["out"] == jpath(err_dest)
    assert json.loads(err_dest.read_text(encoding="utf-8"))["error"]["code"] == "PROTECTED_PATH"
    guarded = run_cli(["version", "--out", str(satk_home / "src" / "v.json")])
    assert guarded.code == 1 and guarded.json["error"]["code"] == "PROTECTED_PATH"
    assert run_cli(["version", "--out"]).code == 2


def test_out_flag_defers_to_an_own_out_parameter(isolated_ops, run_cli, satk_home):
    @R.op("t.write", summary="write", summary_ru="запись")
    def write(out: str | None = None, summary: bool = False) -> dict:
        return {"own_out": out, "own_summary": summary, "rows_like": [1, 2, 3]}

    r = run_cli(["t", "write", "--out", "mine.bin", "--summary"])
    assert r.json == {"ok": True, "own_out": "mine.bin", "own_summary": True, "rows_like": [1, 2, 3]}
    dest = satk_home / "work" / "w.json"
    r2 = run_cli(["t", "write", "--out", "mine.bin", "--json-out", str(dest)])
    assert r2.json["summary"] == "ok: own_out=mine.bin; own_summary=false; rows_like[3]"
    assert json.loads(dest.read_text(encoding="utf-8"))["own_out"] == "mine.bin"


def test_help_find(run_cli):
    r = run_cli(["help", "--find", "make", "txd", "from", "png"])
    assert r.code == 0 and r.json["cols"] == ["kind", "name", "run", "summary"]
    ops = [row[1] for row in r.json["rows"] if row[0] == "op"]
    assert ops[0] == "texture.pack" and r.json["rows"][0][1] == "texture.pack"     # the intent op before topics
    t = run_cli(["help", "--find", "crash", "address", "--limit", "4"])
    assert t.json["n"] <= 4 and any(row[:2] == ["topic", "re"] for row in t.json["rows"])
    assert run_cli(["help", "--find"]).code == 2
    assert run_cli(["help", "--find", "x", "--bogus"]).code == 2


def test_summary_of_paged_tables(isolated_ops, run_cli):
    from satk.core.envelope import table

    @R.op("t.page", summary="page", summary_ru="страница")
    def page() -> dict:
        return table(["a"], [[1], [2]], total=9, next="2", warn=["TRUNCATED: 2 of 9"])

    r = run_cli(["t", "page", "--summary"])
    assert r.json == {"ok": True, "summary": "ok: 2 of 9 rows (cols a); next 2; warn 1: TRUNCATED",
                      "warn": ["TRUNCATED: 2 of 9"]}
