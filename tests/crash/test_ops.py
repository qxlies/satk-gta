"""CLI operations of satk.crash (M2-07): sample, list, --last, info parts; registry shape."""

from __future__ import annotations

import os
import time

import pytest
from crash_helpers import gta_dump, write

from satk.crash.synth import sample_dump, sample_log


def test_ops_registered_cli_only():
    from satk.core.registry import all_ops

    ops = {o.name: o for o in all_ops() if o.name.startswith("crash.")}
    assert set(ops) == {"crash.analyze", "crash.info", "crash.list", "crash.sample", "crash.known", "crash.logs",
                        "crash.bisect"}
    for o in ops.values():
        assert o.mcp_name is None and o.group == "re" and len(o.summary) <= 300
    assert ops["crash.analyze"].param("path").positional


def test_sample_then_last(satk_home, run_cli):
    r = run_cli(["crash", "sample"])
    assert r.code == 0, r.out
    dmp, log = r.json["dump"], r.json["log"]
    assert dmp.endswith("work/out/crash/sample/client_sample_gtasa_0000e6a3_5.dmp") and log.endswith("core.log")
    first = open(dmp, "rb").read()
    assert first == sample_dump()                                   # deterministic
    assert run_cli(["crash", "sample"]).code == 0 and open(dmp, "rb").read() == first
    r = run_cli(["crash", "list"])
    assert r.code == 0 and r.json["total"] == 2
    kinds = {row[1]: row for row in r.json["rows"]}
    assert kinds["dump"][4] == "sample" and kinds["dump"][5] == "gtasa" and kinds["dump"][6] == "0xe6a3"
    r = run_cli(["crash", "analyze", "--last"])                     # the dump, not its core.log
    assert r.code == 0 and r.json["file"] == dmp and r.json["crash"].startswith("0xC0000005 ACCESS_VIOLATION")
    r = run_cli(["crash", "info", "--last", "--part", "modules"])
    assert r.code == 0 and [row[2] for row in r.json["rows"]] == ["gta_sa.exe", "core.dll", "ntdll.dll"]


def test_last_prefers_real_dumps_and_newest(satk_home, tmp_path, run_cli):
    assert run_cli(["crash", "sample"]).code == 0
    d = tmp_path / "dumps"
    d.mkdir()
    old = write(d, "client_a_gtasa_00001008_5_X.dmp", gta_dump(None))
    new = write(d, "client_b_core_00001234_5_X.dmp", gta_dump(None))
    os.utime(old, (time.time() - 100, time.time() - 100))
    write(d, "core.log", sample_log())                             # newest file, but a dump says more
    work_dumps = satk_home / "work" / "dumps"
    work_dumps.mkdir()
    r = run_cli(["crash", "analyze", "--last", "--dir", str(d)])
    assert r.code == 0 and r.json["file"].endswith("client_b_core_00001234_5_X.dmp")
    r = run_cli(["crash", "list", "--dir", str(d), "--limit", "2"])
    assert r.json["total"] == 3 and r.json["next"] == "o:2"
    r = run_cli(["crash", "list", "--dir", str(tmp_path / "nothing")])
    assert r.json["total"] == 0 and "hint" in r.json
    r = run_cli(["crash", "analyze", "--last", "--dir", str(tmp_path / "nothing")])
    assert r.code != 0 and r.json["error"]["code"] == "NOT_FOUND"
    assert new.exists()


def test_info_parts(satk_home, tmp_path, run_cli):
    p = str(write(tmp_path, "s.dmp", sample_dump()))
    r = run_cli(["crash", "info", p])
    j = r.json
    assert j["kind"] == "minidump" and j["exception"]["name"] == "ACCESS_VIOLATION" and j["threads"] == 2
    assert [s.split(":")[0] for s in j["mta"]["sections"]] == ["POL", "LOG", "MSC", "LOG", "REP"]
    r = run_cli(["crash", "info", p, "--part", "streams"])
    assert {row[1] for row in r.json["rows"]} >= {"ModuleList", "ThreadList", "Exception", "summary", "stack"}
    r = run_cli(["crash", "info", p, "--part", "section", "--section", "LOG"])
    assert r.json["cols"] == ["age_ms", "type", "context", "body"] and r.json["total"] == 2
    r = run_cli(["crash", "info", p, "--part", "section", "--section", "LOG:2"])
    assert r.json["kind"] == "text" and r.json["lines"] == ["[14:29:59] sample logfile.txt line"]
    r = run_cli(["crash", "info", p, "--part", "section", "--section", "XYZ"])
    assert r.json["error"]["code"] == "NOT_FOUND" and "POL" in r.json["error"]["did_you_mean"]
    r = run_cli(["crash", "info", p, "--part", "section"])
    assert r.json["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["crash", "info", p, "--part", "text"])
    assert r.json["summary"][0] == "Exception Code: 0xC0000005" and r.json["comment"][0].startswith("Version =")
    r = run_cli(["crash", "info", p, "--part", "memory"])
    assert r.json["total"] == 6
    r = run_cli(["crash", "info", p, "--part", "stack", "--thread", "4120"])
    assert r.json["thread"] == 4120 and r.json["rows"][0][6] == "ip"
    r = run_cli(["crash", "info", p, "--part", "threads"])
    assert r.json["rows"][0][1] == "MainThread" and r.json["rows"][0][8] is True
    r = run_cli(["crash", "info", p, "--part", "blocks"])
    assert r.json["error"]["code"] == "BAD_PARAMS"


def test_info_on_logs(satk_home, tmp_path, run_cli):
    p = str(write(tmp_path, "core.log", sample_log()))
    r = run_cli(["crash", "info", p, "--part", "blocks"])
    assert r.json["total"] == 2 and r.json["rows"][1][2] == "gta_sa.exe" and r.json["rows"][1][5] == 5
    r = run_cli(["crash", "info", p])
    assert r.json["kind"] == "log" and r.json["blocks"] == 2 and r.json["regs"]["eip"] == "0x40e6a3"
    r = run_cli(["crash", "info", p, "--part", "modules"])
    assert r.json["error"]["code"] == "BAD_PARAMS"


@pytest.mark.parametrize("argv", [["crash", "analyze", "--help"], ["crash", "info", "-h"], ["crash", "list", "-h"]])
def test_help(run_cli, argv):
    r = run_cli(argv)
    assert r.code == 0


# --------------------------------------------------------------------------- M3 C1: known, logs, single-player sample


def test_sp_sample_then_last_gives_solution_and_culprit(satk_home, run_cli):
    from satk.crash.synth import sample_sp_files

    assert run_cli(["crash", "sample"]).code == 0
    r = run_cli(["crash", "sample", "--kind", "sp"])
    assert r.code == 0 and r.json["files"] == len(sample_sp_files())
    game = r.json["game"]
    assert game.endswith("work/out/crash/sample-sp") and r.json["dump"].endswith("sample-sp/gta_sa_sample.dmp")
    assert open(r.json["dump"], "rb").read() == sample_sp_files()["gta_sa_sample.dmp"]      # deterministic
    r = run_cli(["crash", "analyze", "--last"])                     # the newest sample dump: the SP one
    j = r.json
    assert r.code == 0 and j["file"].endswith("gta_sa_sample.dmp")
    assert j["known"][0].startswith("#2 0x00456809 (ip)") and "EAX" in j["solution"]
    assert j["culprit"] == "model 20101 is missing: only the disabled mod CoolPickups defines it"
    assert j["logs"].startswith("modloader 0.3.7: 4 mods in the log") and "last [0213] CREATE_PICKUP" in j["logs"]
    r = run_cli(["crash", "analyze", f"{game}/modloader/modloader.log"])
    assert r.json["culprit"] == j["culprit"] and r.json["log"] == "crash report 1 of 1 (exception-address)"
    r = run_cli(["crash", "logs", "--game", game])
    assert r.code == 0 and r.json["modloader"]["crash"] == "gta_sa.exe+0x56809"
    assert r.json["scrlog"]["models"][0] == {"id": 20101, "command": "CREATE_PICKUP", "script": "cpick"}
    assert r.json["cleo"]["error_opcodes"] == ["0213"]
    r = run_cli(["crash", "logs", f"{game}/scrlog.log", "--kind", "scrlog"])
    assert r.code == 0 and r.json["scrlog"]["last_script"] == "cpick"
    r = run_cli(["crash", "bisect", f"{game}/modloader"])
    assert r.code == 0 and r.json["candidates"] == 4                # CoolPickups is already ignored
    r = run_cli(["crash", "logs"])                                  # no configured game folder here
    assert r.json["error"]["code"] == "NOT_FOUND"


def test_known_op(satk_home, run_cli):
    r = run_cli(["crash", "known", "0x00456809"])
    assert r.code == 0 and r.json["entry"] == 2 and r.json["problem"].startswith("Creation of a pickup")
    assert r.json["list"] == "CrashInfo list 2026-10-02"
    r = run_cli(["crash", "known", "pickup"])
    assert r.json["total"] >= 2 and r.json["cols"] == ["n", "head", "section", "match", "title"]
    r = run_cli(["crash", "known", "0x00401008"])
    assert r.json["total"] == 0 and r.json["result"] == "not in the list: '0x00401008'"
    r = run_cli(["crash", "known"])
    assert r.json["entries"] == 358 and r.json["license"] == "MIT" and r.json["notice"] == "data/notices/crashinfo.txt"
    r = run_cli(["crash", "known", "nebo", "--kind", "script"])
    assert r.json["mod"] == "Skybox"


def test_list_finds_a_game_crash_in_modloader_log(satk_home, run_cli):
    ml = satk_home / "GTA San Andreas" / "modloader"
    ml.mkdir(parents=True)
    log = ml / "modloader.log"
    log.write_text("========================== Mod Loader 0.3.7 ==========================\n", encoding="utf-8")
    r = run_cli(["crash", "list"])
    assert r.json["total"] == 0                                     # no crash report in it: not listed
    log.write_text("Mod Loader has started up!\nException Address: 0x00456809\nEAX: 0x00004E85\n", encoding="utf-8")
    r = run_cli(["crash", "list"])
    assert r.json["total"] == 1 and r.json["rows"][0][4] == "game" and r.json["rows"][0][1] == "log"
    r = run_cli(["crash", "analyze", "--last"])
    assert r.code == 0 and r.json["known"][0].startswith("#2 0x00456809")
