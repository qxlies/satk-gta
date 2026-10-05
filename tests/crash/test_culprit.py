"""Crash with solution and culprit (M3 C1 acceptance): known CrashInfo entry, suspects, culprit, logs.

Acceptance: a synthetic dump at a CrashList offset (0x00456809) with EAX = a model ID that a mod defines
gives a report of at most 30 lines with the solution and the culprit; log fixtures are read; an unknown
offset says honestly that it is not in the list.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from crash_helpers import write
from sp_helpers import DEFAULT_INI, ide, make_game, modloader_log, sp_dump

from satk.crash import crashlist
from satk.crash.analyze import MAX_LINES, analyze_file
from satk.crash.synth import sample_dump

PICKUP = 0x00456809
UNKNOWN = 0x00401008          # not in the list (checked below)
MOD_IDE = {"data/coolpickups.ide": ide({20101: "cp_trophy", 20102: "cp_badge"})}


def _game(tmp: Path, *, disabled: bool = False, extra: dict | None = None, logs: dict | None = None) -> Path:
    ini = DEFAULT_INI.replace("_ignore\n", "_ignore\nCoolPickups\n") if disabled else DEFAULT_INI
    mods = {"CoolPickups": MOD_IDE, "RoadSigns": {"roadsigns.txt": "x"}}
    mods.update(extra or {})
    return make_game(tmp / "GTA SA", mods, ini=ini, logs=logs)


def _lines(run_cli, *argv) -> list[str]:
    r = run_cli(["crash", "analyze", *argv], tty=True)
    assert r.code == 0, r.err
    return r.out.rstrip("\n").splitlines()


def test_unknown_offset_is_really_unknown():
    assert crashlist.lookup(f"0x{UNKNOWN:08X}") == [] and crashlist.lookup(f"0x{PICKUP:08X}")


def test_acceptance_dump_with_mod_model_in_eax(satk_home, tmp_path, run_cli):
    g = _game(tmp_path)
    dmp = write(g, "crash.dmp", sp_dump(str(g / "gta_sa.exe"), regs={"eax": 20101}))
    env = analyze_file(str(dmp))                         # the game folder comes from the dump's location
    assert env["crash"].startswith("0xC0000005 ACCESS_VIOLATION reading 0x0000002c at gta_sa.exe+0x56809")
    assert env["known"][0].startswith("#2 0x00456809 (ip): Creation of a pickup using a model that no longer")
    assert "EAX" in env["solution"] and env["solution"].endswith("satk crash known 0x00456809]")
    assert "CrashInfo list 2026-10-02" in env["solution"]
    assert env["culprit"] == "CoolPickups (modloader mod defining model 20101, EAX=0x4e85)"
    assert env["suspects"] == ["EAX=0x4e85 -> model 20101 'cp_trophy': defined by mod CoolPickups "
                               "(CoolPickups/data/coolpickups.ide:2)"]
    lines = _lines(run_cli, str(dmp))
    assert len(lines) <= MAX_LINES, "\n".join(lines)
    assert any(ln.startswith("solution: ") for ln in lines) and any(ln.startswith("culprit: CoolPickups") for ln in lines)
    # the same dump elsewhere: --game points at the folder; the exe path of the dump also finds it
    other = write(tmp_path, "elsewhere.dmp", sp_dump(str(tmp_path / "nowhere" / "gta_sa.exe"), regs={"eax": 20101}))
    assert "culprit" not in analyze_file(str(other))
    assert analyze_file(str(other), game=str(g))["culprit"].startswith("CoolPickups")
    via_exe = write(tmp_path, "exe.dmp", sp_dump(str(g / "gta_sa.exe"), regs={"eax": 20101}))
    assert analyze_file(str(via_exe))["culprit"].startswith("CoolPickups")


def test_disabled_mod_and_id_conflict(satk_home, tmp_path):
    g = _game(tmp_path / "a", disabled=True)
    env = analyze_file(str(write(g, "c.dmp", sp_dump("C:/x/gta_sa.exe", regs={"eax": 20101}))))
    assert env["culprit"] == "model 20101 is missing: only the disabled mod CoolPickups defines it"
    g2 = _game(tmp_path / "b", extra={"MorePickups": {"more.ide": ide({20101: "mp_star"})}})
    env = analyze_file(str(write(g2, "c.dmp", sp_dump("C:/x/gta_sa.exe", regs={"eax": 20101}))))
    assert env["culprit"] == "ID conflict on model 20101: mods CoolPickups, MorePickups define it"
    g3 = _game(tmp_path / "c")
    env = analyze_file(str(write(g3, "c.dmp", sp_dump("C:/x/gta_sa.exe", regs={"eax": 20999}))))
    assert env["suspects"][0].startswith("EAX=0x5207 -> model 20999: not defined by any modloader mod")
    assert "culprit" not in env


def test_unknown_offset_and_stray_registers(satk_home, tmp_path):
    g = _game(tmp_path)
    env = analyze_file(str(write(g, "u.dmp", sp_dump("C:/x/gta_sa.exe", ip=UNKNOWN, regs={"ecx": 20101}))))
    assert env["known"] == "none: 0x00401008 is not in the CrashInfo list 2026-10-02"
    assert "solution" not in env
    assert env["suspects"] == ["ECX=0x4e85 -> model 20101 'cp_trophy': defined by mod CoolPickups "
                               "(CoolPickups/data/coolpickups.ide:2)"]
    assert "culprit" not in env                          # a stray register value is only a suspect
    env = analyze_file(str(write(g, "v.dmp", sp_dump("C:/x/gta_sa.exe", ip=UNKNOWN, regs={"ecx": 411}))))
    assert "suspects" not in env and "culprit" not in env


def test_vanilla_model_replaced_by_a_mod_via_the_index(satk_home, tmp_path):
    from satk.index.api import FakeIndexDB, override_index

    g = _game(tmp_path, extra={"Cars": {"infernus.dff": b"\0" * 16}})
    with override_index(FakeIndexDB()):
        env = analyze_file(str(write(g, "c.dmp", sp_dump("C:/x/gta_sa.exe", regs={"eax": 411}))))
    assert env["suspects"] == ["EAX=0x19b -> model 411 'infernus': vanilla model replaced by mod Cars "
                               "(Cars/infernus.dff)"]
    assert env["culprit"] == "Cars (replaces model 411 'infernus')"


def test_mta_dumps_do_not_read_a_modloader_folder(satk_home, tmp_path):
    _game(tmp_path)
    env = analyze_file(str(write(tmp_path / "GTA SA", "client.dmp", sample_dump())))
    assert "suspects" not in env and "logs" not in env
    assert env["known"].startswith("none: 0x0040E6A3 is not in the CrashInfo list")


def test_modloader_log_report_with_logs(satk_home, tmp_path, run_cli):
    scr = "script cpick\n[0247] REQUEST_MODEL 20101\n[038B] LOAD_ALL_MODELS_NOW\n"
    g = _game(tmp_path, logs={"scrlog.log": scr, "cleo.log": "01/02/2026 10:00:00.000 Loading custom script "
                                                             "cpick.cs...\n"})
    log = g / "modloader" / "modloader.log"
    log.write_text(modloader_log(reports=2), encoding="utf-8", newline="\n")
    env = analyze_file(str(log))
    assert env["log"] == "crash report 1 of 2 (exception-address)" and env["rows"][0][2] == "gta_sa.exe+0x56809"
    assert [r[2] for r in env["rows"]] == ["gta_sa.exe+0x56809", "gta_sa.exe+0x58e69", "gta_sa.exe+0x13c0b2"]
    assert env["known"][0].startswith("#2 0x00456809 (ip)") and env["culprit"].startswith("CoolPickups")
    assert env["logs"] == ("modloader 0.3.7: 2 mods in the log, errors 1; scrlog: script cpick, last [038B] "
                           "LOAD_ALL_MODELS_NOW; cleo: 1 scripts")
    assert any(w.startswith("MORE: 2 crash reports") for w in env["warn"])
    second = analyze_file(str(log), block=2)
    assert second["known"] == ["0x00456819 itself is not in the list", "#315 [038B] (command): Loading a model"]
    # the SCRLog model command is a suspect of its own when the registers say nothing
    second_suspects = second["suspects"]
    assert second_suspects[0].startswith("EAX=0x4e85") or second_suspects[0].startswith("scrlog REQUEST_MODEL 20101")
    lines = _lines(run_cli, str(log))
    assert len(lines) <= MAX_LINES, "\n".join(lines)


def test_scrlog_script_and_command_entries(satk_home, tmp_path):
    g = _game(tmp_path, logs={"scrlog.log": "script nebo\n[0001] WAIT 0\n[04EE] REQUEST_ANIMATION SEX\n"})
    text = f"Exception At Address: 0x{UNKNOWN:08X} Base: 0x00400000\nEAX: 0x00000000\n"
    env = analyze_file(str(write(g, "crash.txt", text)))
    assert env["known"][:2] == ["0x00401008 itself is not in the list", "#319 [04EE] (command): Not found .ifp file."]
    assert env["culprit"] == "script nebo: Skybox [CrashInfo list 2026-10-02]"
    assert env["solution"].endswith("satk crash known 04EE]")


@pytest.mark.parametrize("ip", [PICKUP, UNKNOWN])
def test_reports_stay_short_with_every_key(satk_home, tmp_path, run_cli, ip):
    scr = "script cpick\n" + "".join(f"[0247] REQUEST_MODEL {20101 + i}\n" for i in range(5))
    g = _game(tmp_path, logs={"scrlog.log": scr})
    (g / "modloader" / "modloader.log").write_text(modloader_log(), encoding="utf-8")
    words = {0x10 + 4 * i: 0x458E69 for i in range(40)}
    dmp = write(g, "big.dmp", sp_dump("C:/x/gta_sa.exe", ip=ip, regs={"eax": 20101, "ebx": 20102, "edx": 20101},
                                      stack_words=words))
    lines = _lines(run_cli, str(dmp))
    assert len(lines) <= MAX_LINES, "\n".join(lines)
    assert len(_lines(run_cli, str(dmp), "--limit", "15")) <= MAX_LINES + 5


def test_the_games_own_index_proves_a_model_is_missing(satk_home, tmp_path):
    from satk.index.api import FakeIndexDB, override_index

    g = make_game(satk_home / "GTA San Andreas", {"Cars": {"infernus.dff": b"\0" * 16}})   # = profile 'installed'
    with override_index(FakeIndexDB()):
        env = analyze_file(str(write(g, "c.dmp", sp_dump("C:/x/gta_sa.exe", regs={"eax": 20999}))))
        assert env["suspects"] == ["EAX=0x5207 -> model 20999: not defined by the game or any modloader mod "
                                   "(a removed mod?)"]
        assert env["culprit"].startswith("model 20999 does not exist")
        env = analyze_file(str(write(g, "d.dmp", sp_dump("C:/x/gta_sa.exe", regs={"eax": 411}))))
        assert env["culprit"] == "Cars (replaces model 411 'infernus')"
