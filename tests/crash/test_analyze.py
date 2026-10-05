"""``satk crash analyze`` (M2-07 acceptance): synthetic x86 minidump and MTA text log symbolized via sa-re,
report within 30 lines, damaged inputs give errors (never tracebacks)."""

from __future__ import annotations

import random
import struct

import pytest
from crash_helpers import CORE, STACK, gta_dump, make_dll, write

from satk.core.errors import SatkError
from satk.crash.analyze import analyze_file
from satk.crash.synth import DumpBuilder, sample_dump, sample_log

CALL_CORE = {CORE + 0x1234 - 6: b"\xFF\x15\x00\x10\x00\x10"}


def _frames(env):
    return [dict(zip(env["cols"], r)) for r in env["rows"]]


def test_synthetic_minidump_symbolized(synth, tmp_path):
    """Crash on an MTA hook site in CFoo::Bar, called from CFoo::Qux and from core.dll."""
    data = gta_dump(synth.exe, ip=0x401008, words={0x4: 0x404010, 0x8: 0x401325, 0xC: CORE + 0x1234},
                    code=CALL_CORE, sections=True)
    env = analyze_file(str(write(tmp_path, "client.dmp", data)))
    assert env["ok"] is True
    f = _frames(env)
    assert [r["at"] for r in f] == ["gta_sa.exe+0x1008", "gta_sa.exe+0x1325", "core.dll+0x1234"]
    assert f[0]["fn"] == "CFoo::Bar+0x8" and f[0]["src"] == "game_sa/Foo.cpp:7" and f[0]["via"] == "ip"
    assert f[1]["fn"] == "sub_401320+0x5" and f[1]["via"] == "scan"
    assert f[2]["fn"] is None and f[2]["via"] == "scan"
    assert env["crash"] == "0xC0000005 ACCESS_VIOLATION reading 0x00000010 at gta_sa.exe+0x1008 (thread 77)"
    assert env["fn"] == "CFoo::Bar+0x8" and env["src"] == "game_sa/Foo.cpp:7"
    assert any(p.startswith("HOOKPOS_FooEntry (trunk") for p in env["patched"])
    assert f[0]["mta"] == "HOOKPOS_FooEntry"
    assert env["pools_full"] == ["ped 140/140"]
    assert "sections POL REP" in env["mta"]
    assert env["regs"].startswith("eax=00000000 ebx=00000000 ecx=00001234")
    assert env["dump"].startswith("minidump x86, Windows 10.0.26100, 2 modules, 2 threads")
    assert "satk re src CFoo::Bar" in env["hint"]
    assert "warn" not in env


def test_hooked_call_site_marked(synth, tmp_path):
    env = analyze_file(str(write(tmp_path, "a.dmp", gta_dump(synth.exe, words={0x8: 0x401008}))))
    f = _frames(env)
    assert f[1]["fn"] == "CFoo::Bar+0x8" and f[1]["mta"] == "CALL_BarCallee"      # MTA hooked that call
    assert f[0]["fn"] == "CFoo::Qux+0x1"


def test_mta_text_log_symbolized(synth, tmp_path):
    log = ("** -- Unhandled exception -- **\n\nVersion = 1.6-custom.1\nTime = Sun Oct  4 14:30:00 2026\n"
           "Module = C:\\MTA\\GTA San Andreas\\gta_sa.exe\nCode = 0xC0000005\nOffset = 0x00001008\n\n"
           "EAX=00000000  EBX=0000000A  ECX=00B6F5F0  EDX=0177F3A8  ESI=00C8D4C0\n"
           "EDI=00000000  EBP=0177F9C8  ESP=0177F980  EIP=00401008  FLG=00210246\n\n"
           "ThreadId = 4120\n\nException: Access violation reading 0x00000010\nStack trace:\n"
           "  gta_sa.exe+0x1008 [0x401008]\n  gta_sa.exe+0x1325 [0x401325]\n  core.dll+0x4A1B2 [0x6C04A1B2]\n"
           "  CCore::DoPulse (C:\\mta\\Client\\core\\CCore.cpp:1234) [0x6C0012A0]\n")
    env = analyze_file(str(write(tmp_path, "core.log", sample_log().split("** -- Unhandled", 1)[0] + log)))
    f = _frames(env)
    assert [r["fn"] for r in f] == ["CFoo::Bar+0x8", "sub_401320+0x5", None, "CCore::DoPulse"]
    assert [r["at"] for r in f][:3] == ["gta_sa.exe+0x1008", "gta_sa.exe+0x1325", "core.dll+0x4a1b2"]
    assert f[3]["addr"] == "0x6c0012a0" and {r["via"] for r in f} == {"log"}
    assert env["crash"] == "0xC0000005 ACCESS_VIOLATION at gta_sa.exe+0x1008 (thread 4120)"
    assert env["what"] == "Access violation reading 0x00000010"
    assert env["log"].startswith("block 1 of 1, MTA 1.6-custom.1")
    assert env["fn"] == "CFoo::Bar+0x8" and env["patched"][0].startswith("HOOKPOS_FooEntry")


def test_log_blocks_and_plain_text(synth, tmp_path):
    p = write(tmp_path, "core.log", sample_log())
    old = analyze_file(str(p), block=1)
    assert old["crash"].endswith("at core.dll+0x1234") and old["log"].startswith("block 1 of 2")
    with pytest.raises(SatkError) as e:
        analyze_file(str(p), block=3)
    assert e.value.code == "BAD_PARAMS"
    # any text with addresses (a chat paste, cdb output) falls back to address extraction
    env = analyze_file(str(write(tmp_path, "paste.txt", "it crashed at gta_sa.exe+0x1325 then core.dll+0x10\n")))
    assert [r[3] for r in env["rows"]] == ["sub_401320+0x5", None] and env["rows"][0][6] == "text"
    with pytest.raises(SatkError) as e:
        analyze_file(str(write(tmp_path, "none.txt", "nothing useful here\n")))
    assert e.value.code == "BAD_PARAMS"


def test_without_symbol_db_warns(satk_home, tmp_path):
    env = analyze_file(str(write(tmp_path, "s.dmp", sample_dump())))
    assert env["rows"][0][2] == "gta_sa.exe+0xe6a3" and env["rows"][0][3] is None
    assert any(w.startswith("NOT_READY: symbol DB not built") for w in env["warn"])


def test_exports_name_other_modules(satk_home, tmp_path):
    dll = tmp_path / "helper.dll"
    base = 0x20000000
    ts, size = make_dll(dll, base, {"DoThing": 0x1010, "Other": 0x1100},
                        {0x101B: b"\xE8\x00\x00\x00\x00"})          # call at DoThing+0xB -> ret DoThing+0x10
    b = DumpBuilder("x86")
    b.add_module(str(dll), base, size, timestamp=ts)
    stack = bytearray(0x100)
    struct.pack_into("<I", stack, 0x10, base + 0x1020)
    struct.pack_into("<I", stack, 0x14, base + 0x1030)                # no CALL before it: dropped
    b.add_thread(5, {"eip": base + 0x1104, "esp": STACK, "ebp": 0}, STACK, bytes(stack))
    b.set_exception(5, 0xC0000094, base + 0x1104)
    env = analyze_file(str(write(tmp_path, "x.dmp", b.build())))
    f = _frames(env)
    assert [(r["at"], r["fn"], r["src"]) for r in f] == [("helper.dll+0x1104", "Other+0x4", "export"),
                                                          ("helper.dll+0x1020", "DoThing+0x10", "export")]
    assert env["crash"].startswith("0xC0000094 INT_DIVIDE_BY_ZERO at helper.dll+0x1104")


def test_hang_dump_without_exception_and_thread_choice(synth, tmp_path):
    b = DumpBuilder("x86")
    b.add_module(str(synth.exe), 0x400000, 0x6000, timestamp=0x427101CA)
    stack = bytearray(0x100)
    struct.pack_into("<I", stack, 0x8, 0x401008)
    b.add_thread(1, {"eip": 0x401301, "esp": STACK, "ebp": 0}, STACK, bytes(stack))
    b.add_thread(2, {"eip": 0x401050, "esp": STACK + 0x1000, "ebp": 0}, STACK + 0x1000, bytes(0x40))
    p = write(tmp_path, "hang.dmp", b.build())
    env = analyze_file(str(p))
    assert env["rows"][0][3] == "CFoo::Qux+0x1" and env["rows"][1][3] == "CFoo::Bar+0x8"
    assert any(w.startswith("NO_EXCEPTION") for w in env["warn"])
    assert env["crash"].startswith("crash at gta_sa.exe+0x1301")
    env2 = analyze_file(str(p), thread=2)
    assert env2["rows"][0][3] == "CDoor::Open"
    with pytest.raises(SatkError) as e:
        analyze_file(str(p), thread=99)
    assert e.value.code == "NOT_FOUND" and "1" in e.value.did_you_mean


# --------------------------------------------------------------------------- CLI and report size


def _render_lines(run_cli, *argv) -> list[str]:
    r = run_cli(["crash", "analyze", *argv], tty=True)
    assert r.code == 0, r.err
    return r.out.rstrip("\n").splitlines()


def test_report_at_most_30_lines(synth, tmp_path, run_cli):
    many = {0x10 + 4 * i: 0x401008 if i % 2 else 0x401325 for i in range(40)}
    many.update({0x200 + 4 * i: CORE + 0x1234 + 0x10 * i for i in range(30)})          # unverified core frames
    p = write(tmp_path, "big.dmp", gta_dump(synth.exe, ip=0x401008, words=many, sections=True,
                                            user={0x10001: "Stack Trace (CrashHandler):\n  #0 gta_sa.exe+0x1"
                                                           "008 [0x401008]\n  #1 core.dll+0x99 [0x10000099]\n"}))
    lines = _render_lines(run_cli, str(p))
    assert len(lines) <= 30, "\n".join(lines)
    assert lines[0].split()[:3] == ["#", "addr", "at"] and any(ln.startswith("crash: ") for ln in lines)
    assert any(ln.startswith("mta_stack: ") for ln in lines)                # MTA's walk differs from ours
    sp = write(tmp_path, "sample.dmp", sample_dump())
    assert len(_render_lines(run_cli, str(sp))) <= 30
    assert len(_render_lines(run_cli, str(write(tmp_path, "core.log", sample_log())))) <= 30
    assert len(_render_lines(run_cli, str(p), "--limit", "15")) <= 30 + 5


def test_cli_json_and_errors(synth, tmp_path, run_cli):
    p = write(tmp_path, "a.dmp", gta_dump(synth.exe))
    r = run_cli(["crash", "analyze", str(p), "--limit", "1"])
    assert r.code == 0 and r.json["n"] == 1 and r.json["total"] >= 1
    r = run_cli(["crash", "analyze"])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["crash", "analyze", str(tmp_path / "missing.dmp")])
    assert r.json["error"]["code"] == "NOT_FOUND"
    r = run_cli(["crash", "analyze", str(tmp_path)])
    assert r.json["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["crash", "analyze", str(p), "--last"])
    assert r.json["error"]["code"] == "BAD_PARAMS"


# --------------------------------------------------------------------------- damaged input


@pytest.mark.parametrize("data, code", [
    (b"", "UNSUPPORTED"),
    (b"MDMP\x93\xa7", "UNSUPPORTED"),
    (b"MDMP" + struct.pack("<IIIIIQ", 0xA793, 5, 0x10000, 0, 0, 0), "UNSUPPORTED"),
    (bytes(range(256)) * 4, "UNSUPPORTED"),
    (b"MDMP" + struct.pack("<IIIIIQ", 0xA793, 0, 32, 0, 0, 0), "UNSUPPORTED"),        # no streams at all
])
def test_broken_files_are_errors(satk_home, tmp_path, data, code):
    p = write(tmp_path, "bad.dmp", data)
    with pytest.raises(SatkError) as e:
        analyze_file(str(p))
    assert e.value.code == code


def _must_not_crash(path: str) -> None:
    try:
        env = analyze_file(path)
    except SatkError:
        return
    assert env["ok"] is True and isinstance(env["rows"], list)
    from satk.crash.info import info_file

    for part in ("summary", "modules", "threads", "streams", "memory", "sections", "stack", "text"):
        try:
            info_file(path, part)
        except SatkError:
            pass


def test_truncated_dumps_never_crash(synth, tmp_path):
    data = sample_dump()
    p = tmp_path / "t.dmp"
    for n in list(range(0, 400, 7)) + list(range(400, len(data), 211)):
        p.write_bytes(data[:n])
        _must_not_crash(str(p))


def test_fuzzed_dumps_never_crash(synth, tmp_path):
    base = bytearray(gta_dump(synth.exe, ip=0x401008, words={0x8: 0x401325, 0xC: CORE + 0x1234}, code=CALL_CORE,
                              sections=True))
    rng = random.Random(1907)
    p = tmp_path / "f.dmp"
    n_dir, dir_rva = struct.unpack_from("<II", base, 8)
    hot = list(range(8, 32)) + list(range(dir_rva, dir_rva + 12 * n_dir))     # header and directory bytes
    for i in range(250):
        b = bytearray(base)
        for _ in range(rng.randint(1, 12)):
            pos = rng.choice(hot) if rng.random() < 0.4 else rng.randrange(4, len(b))
            b[pos] = rng.randrange(256)
        if i % 10 == 0:
            b = b[:rng.randrange(32, len(b))]
        p.write_bytes(bytes(b))
        _must_not_crash(str(p))
