"""Minidump reader on synthetic dumps (M2-07): every stream, damaged files, x86 and AMD64 contexts."""

from __future__ import annotations

import struct

import pytest

from satk.crash.minidump import DumpError, Minidump
from satk.crash.mta import decode_section, trailing_sections
from satk.crash.synth import SAMPLE, DumpBuilder, mta_section, sample_dump


def test_sample_dump_streams():
    d = Minidump(sample_dump())
    assert d.arch == "x86" and d.ptr_size == 4
    assert d.sysinfo.os == "10.0.26100" and d.sysinfo.cpus == 8
    assert d.pid == 4242
    assert [m.name for m in d.modules] == ["gta_sa.exe", "core.dll", "ntdll.dll"]      # sorted by base
    gta = d.module_named("gta_sa.exe")
    assert (gta.base, gta.size, gta.timestamp) == (0x400000, 0x1177000, 0x427101CA)
    assert gta.version == "1.6.0.1"
    core = d.module_named("core.dll")
    assert core.pdb == "core.pdb" and core.pdb_id == "123456789ABCDEF0" + "0001020304050607" + "1"
    assert d.module_at(0x53BF10) is gta and d.module_at(0x6C04A1B2) is core and d.module_at(0x300000) is None
    exc = d.exception
    assert (exc.tid, exc.code, exc.address, exc.params) == (SAMPLE["tid"], 0xC0000005, SAMPLE["crash_ip"], [0, 0x10])
    assert exc.context.ip == SAMPLE["crash_ip"] and exc.context.regs["ecx"] == 0xB6F5F0
    t = d.thread(SAMPLE["tid"])
    assert t.name == "MainThread" and t.stack_size == 0x1000 and t.context.sp == 0x0177F980
    assert len(d.threads) == 2
    assert d.read(0x53BF0B, 5)[0] == 0xE8
    assert d.read(0x53BF0B, 64) is None                      # not captured
    lo, data = d.stack_bytes(t, t.context.sp)
    assert lo == 0x0177F980 and len(data) == 0x1000 - 0x980
    assert set(d.user_streams) == {0x10000, 0x10001, 0x10002}
    assert d.comments and d.comments[0].startswith("Version = 1.6-custom")
    assert d.warnings == []


def test_trailing_sections_decode():
    data = sample_dump()
    secs, start = trailing_sections(data)
    assert [s.tag for s in secs] == ["POL", "LOG", "MSC", "LOG", "REP"]
    assert start == len(sample_dump(sections=False))
    pools = decode_section(secs[0])
    assert pools["kind"] == "pools" and ["ped", 140, 140] in pools["rows"]
    log = decode_section(secs[1])
    assert log["kind"] == "log" and log["rows"][1] == [20, "warn", "ped", "pool full"]
    assert decode_section(secs[2]) == {"kind": "misc", "version": 2, "bytes_748add": "8b 0d", "crash_zone": 0}
    assert decode_section(secs[3])["text"].startswith("[14:29:59]")
    assert "report.log" in decode_section(secs[4])["text"]
    assert trailing_sections(sample_dump(sections=False))[0] == []


def test_section_footer_mismatch_stops():
    good = mta_section("REP", b"abc")
    bad = good[:-8] + struct.pack("<I", struct.unpack(">I", b"XXXe")[0]) + b"\0\0\0\0"   # end magic != begin
    assert trailing_sections(b"\0" * 64 + bad)[0] == []
    assert [s.tag for s in trailing_sections(b"\0" * 64 + good)[0]] == ["REP"]


def test_amd64_context_and_memory64():
    b = DumpBuilder("amd64")
    b.add_module(r"C:\srv\MTA Server64.exe", 0x140000000, 0x200000, timestamp=7)
    regs = {"rip": 0x140001234, "rsp": 0x7FF000, "rbp": 0x7FF100, "r15": 0xDEADBEEF}
    b.add_thread(9, regs, 0x7FF000, bytes(0x100))
    b.set_exception(9, 0xC0000005, 0x140001234, [1, 0])
    d = Minidump(b.build())
    assert d.arch == "amd64" and d.ptr_size == 8
    c = d.exception.context
    assert (c.ip, c.sp, c.fp, c.regs["r15"]) == (0x140001234, 0x7FF000, 0x7FF100, 0xDEADBEEF)
    # a Memory64List stream appended by hand
    raw = bytearray(b.build())
    payload = b"\x11" * 32
    data_rva = len(raw)
    raw += payload
    m64 = struct.pack("<QQ", 1, data_rva) + struct.pack("<QQ", 0x500000, len(payload))
    m64_rva = len(raw)
    raw += m64
    n, dir_rva = struct.unpack_from("<II", raw, 8)
    new_dir = bytes(raw[dir_rva:dir_rva + 12 * n]) + struct.pack("<III", 9, len(m64), m64_rva)
    nd_rva = len(raw)
    raw += new_dir
    struct.pack_into("<II", raw, 8, n + 1, nd_rva)
    d2 = Minidump(bytes(raw))
    assert d2.read(0x500008, 4) == b"\x11" * 4


@pytest.mark.parametrize("data, msg", [
    (b"", "too small"),
    (b"MDMP" + b"\0" * 10, "too small"),
    (b"PAGEDU64" + b"\0" * 64, "kernel"),
    (b"MZ\x90\0" + b"\0" * 64, "not a minidump"),
    (b"MDMP" + struct.pack("<IIIIIQ", 0xA793, 3, 0x7FFFFFF0, 0, 0, 0), "outside the file"),
    (b"MDMP" + struct.pack("<IIIIIQ", 0xA793, 100000, 32, 0, 0, 0), "implausible stream count"),
])
def test_bad_headers_raise_dump_error(data, msg):
    with pytest.raises(DumpError, match=msg):
        Minidump(data)


def test_damaged_stream_is_skipped_with_warning():
    raw = bytearray(sample_dump(sections=False))
    n, dir_rva = struct.unpack_from("<II", raw, 8)
    for i in range(n):
        st, size, rva = struct.unpack_from("<III", raw, dir_rva + 12 * i)
        if st == 4:                                              # module list -> absurd count
            struct.pack_into("<I", raw, rva, 0x7FFFFFFF)
    d = Minidump(bytes(raw))
    assert d.modules == [] and any("ModuleList" in w for w in d.warnings)
    assert d.exception is not None and d.threads                 # the rest still parses


def test_open_from_file_uses_mmap(tmp_path):
    p = tmp_path / "x.dmp"
    p.write_bytes(sample_dump())
    with Minidump.open(p) as d:
        assert d.exception.code == 0xC0000005 and d.path == str(p)
    empty = tmp_path / "e.dmp"
    empty.write_bytes(b"")
    with pytest.raises(DumpError, match="empty"):
        Minidump.open(empty)
