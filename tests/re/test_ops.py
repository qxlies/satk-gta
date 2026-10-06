"""satk re ... through the CLI and the registry (SPEC §4.6, §4.7; WP-09)."""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.core import registry as R
from satk.core.ids import provider_for

SAMPLE = Path(__file__).with_name("data") / "crash_sample.txt"


def test_registration():
    R.discover()
    assert "satk.re.ops" not in R.import_errors()
    names = {o.name: o for o in R.all_ops() if o.name.startswith("re.")}
    assert set(names) == {"re.build", "re.addr", "re.find", "re.src", "re.patches", "re.limits", "re.nodes", "re.export"}
    assert {o.mcp_name for o in names.values() if o.mcp_name} == {"re_addr", "re_find", "re_src", "re_patches"}
    assert all(o.mcp_group == "re" for o in names.values())
    assert names["re.build"].long_running
    schema = names["re.addr"].input_schema()
    assert schema["properties"]["text"]["type"] == "array" and "required" not in schema
    assert type(provider_for("fn")).__name__ == "ReProvider" and provider_for("patch") is provider_for("g")


def test_cli_addr_find_src(built, run_cli):
    r = run_cli(["re", "addr", "0x401008", "--json"])
    assert r.code == 0 and r.json["fn"] == "CFoo::Bar" and r.json["off"] == "0x8"
    r = run_cli(["re", "addr", "gta_sa.exe+0x5014", "0x401008", "--json"])
    assert r.code == 0 and r.json["cols"][0] == "addr" and r.json["n"] == 2
    r = run_cli(["re", "addr", "--text-file", str(SAMPLE), "--json"])
    assert r.code == 0 and r.json["summary"] == {"gta_sa": 5, "other_modules": 4}
    r = run_cli(["re", "find", "CFoo::Bar", "--json"])
    assert r.json["rows"][0][2] == "CFoo::Bar"
    r = run_cli(["re", "find", "gstate", "--kind", "global", "--json"])
    assert r.json["rows"][0][0] == "g:0x404010"
    r = run_cli(["re", "src", "CFoo::Bar", "--context", "5", "--json"])
    assert r.code == 0 and r.json["file"] == "game_sa/Foo.cpp"
    r = run_cli(["re", "addr", "0x401008"], tty=True)
    assert r.code == 0 and "fn: CFoo::Bar" in r.out


def test_cli_patches_limits_export(built, run_cli, satk_home):
    r = run_cli(["re", "patches", "--fn", "CFoo::Bar", "--origin", "neon", "--json"])
    assert r.code == 0 and r.json["total"] == 6
    r = run_cli(["re", "patches", "--range", "0x401000-0x40100f", "--kind", "hookpos", "--json"])
    assert {row[3] for row in r.json["rows"]} == {"neon", "trunk"}
    assert r.json["total"] == 2 and r.json["raw_total"] == 3
    raw = run_cli(["re", "patches", "--range", "0x401000-0x40100f", "--kind", "hookpos", "--origin", "upstream", "--json"])
    assert {row[3] for row in raw.json["rows"]} == {"upstream"}
    assert raw.json["total"] == raw.json["raw_total"] == 1
    r = run_cli(["re", "limits", "--json"])
    assert r.code == 0 and r.json["arrays_with_len"] == 2
    r = run_cli(["re", "limits", "--kind", "pool", "--match", "Thing", "--json"])
    assert r.json["rows"][0][6:8] == [160, 320]
    r = run_cli(["re", "export", "json", "--json"])
    assert r.code == 0 and r.json["files"][0].endswith("/work/re/export/symbols.json")
    r = run_cli(["re", "patches", "--fn", "CFoo::Bar", "--range", "0x1-0x2", "--json"])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS"


def test_cli_errors(satk_home, run_cli):
    from satk.re import db as dbm

    dbm.reset_cache()
    r = run_cli(["re", "addr", "0x401000", "--json"])
    assert r.code == 3 and r.json["error"]["code"] == "NOT_READY"
    r = run_cli(["re", "addr", "--json"])
    assert r.code == 2
    r = run_cli(["re", "build", "--json"])                 # no game copy in the isolated workspace
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"


def test_limits_world_filter(built, run_cli):
    result = run_cli(["re", "limits", "--kind", "world", "--json"])
    assert result.code == 0 and result.json["total"] == 1
    assert result.json["rows"][0][:3] == ["world.bounds", "world", 3000]


@pytest.mark.parametrize("separator", [" = ", ": "])
def test_re_addr_crash_lines_as_separate_items(built, separator):
    result = R.invoke(R.op_by_mcp("re_addr"), {"text": [
        "Module" + separator + r"C:\Games\gta_sa.exe", "Offset" + separator + "0x00001008",
    ]})
    assert result["ok"] and result["module"] == "gta_sa.exe"
    assert result["fn"] == "CFoo::Bar" and result["off"] == "0x8"


@pytest.mark.parametrize("module", ["gta_sa.exe", r"C:\Games\gta_sa.exe", "C:/Games/gta_sa.exe"])
def test_re_addr_split_module_offset_keeps_spaces(built, module):
    result = R.invoke(R.op_by_mcp("re_addr"), {"text": [module, "+", "0x00001008"]})
    assert result["ok"] and result["module"] == "gta_sa.exe"
    assert result["fn"] == "CFoo::Bar" and result["off"] == "0x8"


def test_build_json_includes_scan_counts(world, satk_home, monkeypatch, run_cli):
    from re_synth import make_sources
    from satk.re import build

    monkeypatch.setattr(build, "default_sources", lambda **kw: make_sources(world))
    result = run_cli(["re", "build", "--json"])
    assert result.code == 0
    stats = result.json["stats"]
    assert stats["neon_memput_calls_nonfast"] == 2  # one fixed and one dynamic address
    assert stats["patch_scan"]["neon"]["calls"]["MemPut"] == [1, 1]
    assert stats["patch_scan"]["neon"]["unresolved"]


def test_status_and_doctor(built, satk_home):
    status = R.status_providers()["re"](False)
    assert status["built"] is True and status["funcs"] == 6
    assert R.doctor_checks()["re_symdb"]()["status"] == "ok"


def test_status_without_db(satk_home):
    from satk.re import db as dbm

    dbm.reset_cache()
    assert R.status_providers()["re"](False)["built"] is False
    assert R.doctor_checks()["re_symdb"]()["status"] == "warn"


def test_re_addr_warns_on_other_exe_layouts(satk_home):
    """PORTABILITY §3: re addr says UNSUPPORTED_EXE when the addresses cannot be 1.0 US ones."""
    from satk.re import ops as O

    class Db:
        meta = {"exe_sha256": "a559aa772fd136379155efa71f00c47aad34bbfeae6196b0fe1047d0645cbd26",
                "exe_path": str(satk_home / "gta-sa-clean" / "gta_sa.exe")}

    assert O._exe_layout_warnings(Db()) == []  # stock 1.0 US HOODLUM, no user game
    game = satk_home / "GTA San Andreas"  # paths.game_root (= installed by default)
    game.mkdir()
    (game / "gta-sa.exe").write_bytes(b"MZ steam-like")
    w = O._exe_layout_warnings(Db())
    assert len(w) == 1 and w[0].startswith("UNSUPPORTED_EXE:") and "gta-sa.exe" in w[0]
    Db.meta = {"exe_sha256": "0" * 64, "exe_path": str(game / "gta-sa.exe")}  # DB built from that exe
    w = O._exe_layout_warnings(Db())
    assert len(w) == 1 and "symbol DB was built from" in w[0]
