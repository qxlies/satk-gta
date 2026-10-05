"""MTA text crash logs, frame lines and dump file names (M2-07)."""

from __future__ import annotations

import pytest

from satk.crash.mta import exception_name, parse_dump_name, parse_frame_line, parse_log
from satk.crash.synth import SAMPLE, sample_log


def test_core_log_blocks_fields_regs_frames():
    blocks = parse_log(sample_log())
    assert len(blocks) == 2
    old, new = blocks
    assert old.module == "core.dll" and old.offset == 0x1234 and old.frames == []
    assert new.module == "gta_sa.exe" and new.code == 0xC0000005 and new.offset == SAMPLE["crash_ip"] - 0x400000
    assert new.fields["Version"] == "1.6-custom.00000.0.000"
    assert new.fields["Exception"] == "Access violation reading 0x00000010"
    assert new.regs["eip"] == SAMPLE["crash_ip"] and new.regs["esp"] == 0x0177F980 and new.regs["eflags"] == 0x210246
    assert [(f.module, f.off, f.va) for f in new.frames] == [
        ("gta_sa.exe", 0xE6A3, 0x40E6A3), ("gta_sa.exe", 0x13BF10, 0x53BF10), ("gta_sa.exe", 0x13E986, 0x53E986),
        ("core.dll", 0x4A1B2, 0x6C04A1B2), ("KERNEL32.DLL", 0x1FA29, 0x7655FA29)]
    assert new.fields["Crash dump file"].endswith("_20261004_1430.dmp")


@pytest.mark.parametrize("line, module, off, va, symbol", [
    ("  gta_sa.exe+0x13BF09 [0x53BF09]", "gta_sa.exe", 0x13BF09, 0x53BF09, None),
    ("  #3 core.dll+0x0004A1B2", "core.dll", 0x4A1B2, None, None),
    ("#00 CClientGame::DoPulse [0x6A3B12F0] (client.dll+0x12F0)", "client.dll", 0x12F0, 0x6A3B12F0, "CClientGame::DoPulse"),
    ("#01 0x53BF09 [0x53BF09] (gta_sa.exe+0x13BF09)", "gta_sa.exe", 0x13BF09, 0x53BF09, None),
    ("CCore::DoPulse (C:\\mta\\Client\\core\\CCore.cpp:1234) [0x6C0012A0]", None, None, 0x6C0012A0, "CCore::DoPulse"),
    ("0177f9c8 0053bf09 00000000 00000000 gta_sa+0x13bf09", "gta_sa.exe", 0x13BF09, 0x53BF09, None),
    ("0177fa00 6c04a1b2 core!CCore::DoPulse+0x12", "core", None, None, "CCore::DoPulse+0x12"),
    ("--> gta_sa.exe+0x7260  <-- FREEZE POINT", "gta_sa.exe", 0x7260, 0x407260, None),
    ("  0x0053BF09", "gta_sa.exe", 0x13BF09, 0x53BF09, None),
    ("gta_sa.exe+0x00007260 (IDA: 0x00407260)", "gta_sa.exe", 0x7260, 0x407260, None),
])
def test_frame_lines(line, module, off, va, symbol):
    f = parse_frame_line(line)
    assert (f.module, f.off, f.va) == (module, off, va)
    if symbol is not None:
        assert f.symbol == symbol


def test_non_frames():
    assert parse_frame_line("") is None
    assert parse_frame_line("EIP=0x00000000 (null instruction pointer - cannot walk stack)") is None
    assert parse_frame_line("just words here") is None


def test_server_log_amd64_registers():
    text = ("** -- Unhandled exception -- **\n\nVersion = 1.6.0-9.22741.0\nModule = C:\\srv\\deathmatch.dll\n"
            "Code = 0xC0000005\nOffset = 0x00012340\n\n"
            "RAX=0000000000000000  RBX=0000000000000001  RCX=00000000DEADBEEF\n"
            "R8 =0000000000000008  R9 =0000000000000009  R10=000000000000000A\n"
            "RBP=0000000000001000  RSP=0000000000002000  RIP=0000000140012340\n"
            "** -- End of unhandled exception -- **\n")
    (b,) = parse_log(text)
    assert b.module == "deathmatch.dll" and b.offset == 0x12340
    assert b.regs["rip"] == 0x140012340 and b.regs["r8"] == 8 and b.regs["rcx"] == 0xDEADBEEF


def test_dump_names():
    n = parse_dump_name("client_1.6.0-9.22741.0.000_gtasa_0013bf09_5_CPFMSA16_7F000001_5590_00A_ABCDE_20261004_1430.dmp")
    assert n == {"side": "client", "version": "1.6.0-9.22741.0.000", "module": "gtasa", "offset": "0x13bf09",
                 "code": "0x0005", "date": "2026-10-04 14:30"}
    s = parse_dump_name("server_1.6.0-9.22741.0_deathmatch64_00012340_5_20261004_0102.dmp")
    assert s["side"] == "server" and s["module"] == "deathmatch64" and s["offset"] == "0x12340"
    assert parse_dump_name("client_sample_gtasa_0000e6a3_5.dmp")["module"] == "gtasa"
    assert parse_dump_name("something.dmp") == {}


def test_exception_names():
    assert exception_name(0xC0000005) == "ACCESS_VIOLATION"
    assert exception_name(0xE06D7363) == "CPP_EXCEPTION"
    assert exception_name(0xE0000001) == "MTA_WATCHDOG_TIMEOUT"
    assert exception_name(0x12345678) is None and exception_name(None) is None
