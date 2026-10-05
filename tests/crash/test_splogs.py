"""Single-player logs (M3 C1): crash reports in text, modloader.log, scrlog.log, CLEO log, modloader folder."""

from __future__ import annotations

from pathlib import Path

import pytest
from sp_helpers import DEFAULT_INI, ide, make_game, modloader_log

from satk.core.errors import SatkError
from satk.crash import gamelogs as L
from satk.crash import modfolder as F
from satk.crash import sptext as S

# shapes of crash reports (invented values)
SAMP = """Exception At Address: 0x00745BDC Base: 0x00400000

Registers:
EAX: 0x00000000\tEBX: 0x00000001\tECX: 0x003EF48C\tEDX: 0x7C90E514
ESI: 0x00000000\tEDI: 0x00000000\tEBP: 0x0022FF78\tESP: 0x0022FEB4
EFLAGS: 0x00010246
"""
MODSA = """[19:47:24.196]  mod_sa has crashed.
[19:47:24.196]  Exception at address: 0x0061A5C5, Last function processed: proxy::Draw()
[19:47:24.196]  Cause: EXCEPTION_ACCESS_VIOLATION
[19:47:24.196]  Attempted to read from: 0x08c4e42d
[19:47:24.196]  EAX: 0x001160bd || ESI: 0x08b05ed0
[19:47:24.196]  EBX: 0xffffff01 || EDI: 0x10175804
"""
VS = ("Unhandled exception at 0x6C04A1B2 in SilentPatchSA.asi: 0xC0000005: Access violation writing location "
      "0x00000010.\n")


def test_crash_report_shapes():
    (c,) = S.parse(SAMP)
    assert (c.kind, c.addr, c.module, c.off) == ("exception-at-address", 0x745BDC, "gta_sa.exe", 0x345BDC)
    assert c.regs["ecx"] == 0x3EF48C and c.regs["eflags"] == 0x10246 and c.code is None
    (m,) = S.parse(MODSA)
    assert (m.addr, m.module, m.code, m.access) == (0x61A5C5, "gta_sa.exe", 0xC0000005, "reading 0x08c4e42d")
    assert m.regs["edi"] == 0x10175804
    (v,) = S.parse(VS)
    assert (v.kind, v.module, v.off, v.code, v.access) == ("unhandled-exception", "SilentPatchSA.asi", None,
                                                           0xC0000005, "writing 0x00000010")
    assert S.has_crash(SAMP) and not S.has_crash("Mod Loader has started up!\nException handling: none\n")


def test_modloader_style_report_with_backtrace_and_two_reports():
    crashes = S.parse(modloader_log(reports=2))
    assert len(crashes) == 2 and [c.addr for c in crashes] == [0x456809, 0x456819]
    c = crashes[0]
    assert c.regs["eax"] == 20101 and c.code == 0xC0000005 and c.access == "reading 0x0000002c"
    assert [(f.module, f.off) for f in c.frames] == [("gta_sa.exe", 0x56809), ("gta_sa.exe", 0x58E69),
                                                     ("gta_sa.exe", 0x13C0B2)]
    assert c.fields["Exception Code"] == "0xC0000005"


def test_parse_modloader_log():
    r = L.parse_modloader(modloader_log(mods=("CoolPickups", "RoadSigns"), extra_lines=(
        "Removing imported model file at index 20000",)))
    assert r["version"] == "0.3.7" and r["game"] == "GTA SA 1.0 US" and r["profile"] == "Default"
    assert r["mods"] == ["Broken", "CoolPickups", "RoadSigns"]
    assert r["n_errors"] == 1 and r["errors"][0].startswith('Failed to install file "modloader\\Broken')
    assert r["crashes"] == 1 and r["crash"] == "gta_sa.exe+0x56809"
    assert r["last"][-1] == "Mod Loader has started up!"            # the report banner is not "last activity"


def test_parse_scrlog_forms():
    text = "\n".join([
        "SCRLog started", "script cpick", "[0001] WAIT 250", "[0247] REQUEST_MODEL 20101",
        "carspwn: [009A] CREATE_CHAR 4 102 0.0 0.0 0.0 $PED",
        "[tuning] [00A5] CREATE_CAR 411 1.0 2.0 3.0 $CAR",
        "script main", "[0001] WAIT 0", "script cpick", "[0213] CREATE_PICKUP 20101 3 1.0 2.0 3.0 $P",
        "[038B] LOAD_ALL_MODELS_NOW"])
    r = L.parse_scrlog(text)
    assert r["commands"] == 7 and r["last_command"] == "[038B] LOAD_ALL_MODELS_NOW" and r["last_script"] == "cpick"
    assert r["last_opcodes"] == ["038B", "0213", "0001"]
    assert r["scripts"] == ["cpick", "main", "tuning", "carspwn"]
    assert [(m["id"], m["command"], m["script"]) for m in r["models"]] == [
        (20101, "CREATE_PICKUP", "cpick"), (411, "CREATE_CAR", "tuning"), (102, "CREATE_CHAR", "carspwn")]
    assert L.parse_scrlog("nothing here\n")["warn"].startswith("no script commands")


def test_parse_cleo_log():
    text = "\n".join([
        "05/10/2026 21:40:01.100 Log started.", "05/10/2026 21:40:01.101 Started on game of version: SA 1.0 us",
        "05/10/2026 21:40:01.102 Loading plugin cleo/IniFiles.cleo",
        "05/10/2026 21:40:01.103 Audio hardware acceleration disabled (no EAX)",
        "05/10/2026 21:40:04.501 Loading custom script cpick.cs...",
        "05/10/2026 21:40:04.502 Registering custom script named cpick",
        "05/10/2026 21:40:04.503 Loading custom script old.cs...",
        "05/10/2026 21:40:04.504 Registering custom script named old",
        "05/10/2026 21:40:05.000 Unregistering custom script named old",
        "05/10/2026 21:40:09.010 [ERROR] Script cpick.cs: opcode [0213] model 20101 is not available"])
    r = L.parse_cleo(text)
    assert r["game"] == "SA 1.0 us" and r["plugins"] == ["IniFiles.cleo"] and r["scripts"] == ["cpick.cs", "old.cs"]
    assert r["running"] == ["cpick"] and r["n_errors"] == 1 and r["error_opcodes"] == ["0213"]
    assert r["error_scripts"] == ["cpick.cs"] and "crashes" not in r


def test_detect_and_find_and_tail(tmp_path: Path):
    assert L.detect_kind("modloader.log", "") == "modloader"
    assert L.detect_kind("x.txt", modloader_log()) == "modloader"
    assert L.detect_kind("x.txt", "\n".join(f"[00{i:02d}] CMD" for i in range(20))) == "scrlog"
    assert L.detect_kind("x.txt", "01/02/2026 10:00:00.000 Loading custom script a.cs\n") == "cleo"
    assert L.detect_kind("x.txt", "hello\n") is None
    g = make_game(tmp_path / "g", {}, logs={"scrlog.log": "[0001] WAIT 0\n", "cleo/.cleo.log": "x\n"})
    (g / "modloader" / "modloader.log").write_text(modloader_log(), encoding="utf-8")
    found = L.find_logs(g)
    assert set(found) == {"modloader", "scrlog", "cleo"} and found["cleo"].name == ".cleo.log"
    big = tmp_path / "scrlog.log"
    big.write_text("[0001] WAIT 0\n" * 200_000 + "[038B] LOAD_ALL_MODELS_NOW\n", encoding="utf-8", newline="\n")
    assert big.stat().st_size > 2 << 20
    text = L.read_log(big, "scrlog")
    assert len(text) <= 2 << 20 and text.startswith("[0001]") and text.endswith("LOAD_ALL_MODELS_NOW\n")
    assert L.parse_any(big)["last_command"] == "[038B] LOAD_ALL_MODELS_NOW"
    with pytest.raises(SatkError) as e:
        L.parse_any(tmp_path / "missing.log")
    assert e.value.code == "NOT_FOUND"
    (tmp_path / "x.log").write_text("hello\n", encoding="utf-8")
    with pytest.raises(SatkError) as e:
        L.parse_any(tmp_path / "x.log")
    assert e.value.code == "UNSUPPORTED"


def test_modloader_folder_rules(tmp_path: Path):
    ini = DEFAULT_INI.replace("[Profiles.Default.Priority]\n", "[Profiles.Default.Priority]\nZeroed = 0\nHigh=90\n")
    ini = ini.replace("_ignore\n", "_ignore\nold*\n") + "\n[Profiles.Other.ExclusiveMods]\nOtherOnly\n"
    g = make_game(tmp_path / "g", {n: {"a.txt": "x"} for n in ("Alpha", "_ignore", "Old Cars", "Zeroed", "High",
                                                                   "OtherOnly")}, ini=ini)
    (g / "modloader" / "Alpha" / "modloader.ini").write_text("[Folder.Config]\n", encoding="utf-8")
    f = F.read_folder(F.find_folder(g))
    st = {m.name: (m.loaded, m.why) for m in f.mods}
    assert st == {"Alpha": (True, None), "_ignore": (False, "ignore-mods"), "Old Cars": (False, "ignore-mods"),
                  "Zeroed": (False, "priority-0"), "High": (True, None), "OtherOnly": (False, "exclusive")}
    assert [m.name for m in f.loaded] == ["Alpha", "High"] and next(m for m in f.mods if m.name == "High").priority == 90
    assert next(m for m in f.mods if m.name == "Alpha").nested and f.section_name("IgnoreMods") == \
        "Profiles.Default.IgnoreMods"
    exc = DEFAULT_INI.replace("ExcludeAllMods = false", "ExcludeAllMods = true") \
        .replace("[Profiles.Default.IncludeMods]\n", "[Profiles.Default.IncludeMods]\nhi*\n")
    (g / "modloader" / "modloader.ini").write_text(exc, encoding="utf-8")
    assert [m.name for m in F.read_folder(g / "modloader").loaded] == ["High"]
    assert F.find_folder(g / "modloader" / "modloader.ini") == g / "modloader"
    assert F.find_folder(tmp_path) is None


def test_scan_models(tmp_path: Path):
    g = make_game(tmp_path / "g", {
        "CoolPickups": {"data/coolpickups.ide": ide({20101: "cp_trophy", 20102: "cp_badge"}),
                        "models/cp_trophy.dff": b"\0" * 8},
        "Cars": {"infernus.dff": b"\0" * 8, "INFERNUS.TXD": b"\0" * 8, "cars.ide": ide({411: "infernus"})},
        "_ignore": {"old.ide": ide({20101: "old_trophy"})},
    })
    s = F.scan_models(F.read_folder(g / "modloader"))
    assert [(d.mod, d.relpath, d.line, d.name) for d in s.defined(20101)] == [
        ("_ignore", "_ignore/old.ide", 2, "old_trophy"), ("CoolPickups", "CoolPickups/data/coolpickups.ide", 2,
                                                          "cp_trophy")]
    assert s.loaded == {"Cars": True, "CoolPickups": True, "_ignore": False}
    assert s.replacing("Infernus") == [("Cars", "Cars/INFERNUS.TXD"), ("Cars", "Cars/infernus.dff")]
    assert s.defined(411)[0].sec == "objs" and s.replacing(None) == []
