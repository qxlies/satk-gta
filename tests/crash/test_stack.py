"""Heuristic stack walk and the CALL-before-return check (M2-07)."""

from __future__ import annotations

import struct

import pytest
from crash_helpers import CORE, GTA_SIZE, GTA_TS, STACK, gta_dump

from satk.crash.images import Images, PeFile, call_before
from satk.crash.minidump import Minidump
from satk.crash.stack import walk


@pytest.mark.parametrize("code, length", [
    (b"\x90\x90" + b"\xE8\x10\x00\x00\x00", 5),        # call rel32
    (b"\x90" + b"\xFF\x15\x00\x10\x40\x00", 6),         # call [disp32]
    (b"\x90" * 5 + b"\xFF\xD0", 2),                     # call eax
    (b"\x90" * 5 + b"\xFF\x16", 2),                     # call [esi]
    (b"\x90" * 4 + b"\xFF\x50\x08", 3),                 # call [eax+8]
    (b"\x90" * 4 + b"\xFF\x14\x85", 3),                 # call [sib]
    (b"\x90" * 3 + b"\xFF\x54\x24\x04", 4),             # call [esp+4]
    (b"\x90" + b"\xFF\x90\x00\x01\x00\x00", 6),         # call [eax+100h]
    (b"\xFF\x94\x24\x00\x01\x00\x00", 7),               # call [esp+100h]
])
def test_call_forms(code, length):
    assert call_before(code)[0] == length


@pytest.mark.parametrize("code", [b"\x90" * 7, b"\x90" * 5 + b"\xFF\xE0", b"\x90" * 5 + b"\xFF\x15", b"\xC3" * 7])
def test_not_calls(code):
    assert call_before(code) is None                    # nops, jmp eax, truncated call [disp32], rets


def test_x64_rex_call():
    assert call_before(b"\x90" * 4 + b"\x41\xFF\xD3", 8)[0] == 3
    assert call_before(b"\x90" * 4 + b"\x41\xFF\xD3", 4)[0] == 2      # x86: inc ecx; call ebx


def test_direct_target():
    ln, rel = call_before(b"\x90\x90\xE8\xF0\xFF\xFF\xFF")
    assert (ln, rel) == (5, -16)


def _walk(data: bytes, exe=None, **kw):
    d = Minidump(data)
    images = Images(d, game_exes=[exe] if exe else [])
    exc = d.exception
    return walk(d, exc.context, d.thread(exc.tid), images, **kw)


def test_scan_keeps_calls_drops_data_and_function_pointers(synth):
    data = gta_dump(synth.exe, words={0x0: 0x401100,          # function start (nops before it): not a return
                                      0x4: 0x404010,          # .data global
                                      0x8: 0x401008,          # after "call F_CALLEE" in CFoo::Bar (exe bytes)
                                      0xC: CORE + 0x1234,     # core.dll, captured "call [disp32]" before it
                                      0x10: CORE + 0x3000,    # core.dll, code unknown -> kept unverified
                                      0x14: 0x401008})        # duplicate: keeps the first slot
    data2 = gta_dump(synth.exe, words={0x8: 0x401008, 0xC: CORE + 0x1234, 0x10: CORE + 0x3000},
                     code={CORE + 0x1234 - 6: b"\xFF\x15\x00\x10\x00\x10"})
    frames, st = _walk(data2, synth.exe)
    got = [(hex(f.va), f.how, f.call) for f in frames]
    assert got == [("0x401301", "ip", None), ("0x401008", "scan", True), (hex(CORE + 0x1234), "scan", True),
                   (hex(CORE + 0x3000), "scan", None)]
    assert frames[1].slot == STACK + 0x108 and frames[1].target == 0x401100
    frames, _ = _walk(data, synth.exe)
    vas = [f.va for f in frames]
    assert 0x401100 not in vas and 0x404010 not in vas and vas.count(0x401008) == 1
    assert st["scanned"] > 0 and st["stack_bytes"] == 0x700


def test_without_exe_data_pointers_survive_unverified(synth):
    frames, _ = _walk(gta_dump(None, words={0x4: 0x404010}), None)
    assert [(hex(f.va), f.call) for f in frames] == [("0x401301", None), ("0x404010", None)]
    # with the symbol DB's section map (is_code) the .data pointer is dropped again
    frames, _ = _walk(gta_dump(None, words={0x4: 0x404010}), None, is_code=lambda va: va < 0x403000)
    assert [f.va for f in frames] == [0x401301]


def test_ebp_chain(synth):
    esp = STACK + 0x100
    ebp = esp + 0x40
    words = {0x40: ebp + 0x20, 0x44: 0x401008,                 # frame 1 via ebp
             0x60: ebp + 0x20, 0x64: 0x401325}                 # self-loop: stops
    frames, _ = _walk(gta_dump(synth.exe, words=words, ebp=ebp), synth.exe)
    assert [(hex(f.va), f.how) for f in frames] == [("0x401301", "ip"), ("0x401008", "ebp"), ("0x401325", "ebp")]


def test_scan_window(synth):
    frames, _ = _walk(gta_dump(synth.exe, words={0x600: 0x401008}), synth.exe, scan_bytes=0x100)
    assert [f.va for f in frames] == [0x401301]


def test_pe_file_identity_and_reads(synth):
    pe = PeFile.load(synth.exe)
    assert (pe.timestamp, pe.size_of_image, pe.image_base, pe.is64) == (GTA_TS, GTA_SIZE, 0x400000, False)
    assert pe.executable(0x1000) and not pe.executable(0x4010)
    assert pe.read(0x1003, 5)[0] == 0xE8
    assert pe.read(0x1FFF0, 4) is None
    assert PeFile.load(synth.root / "missing.dll") is None
    junk = synth.root / "junk.dll"
    junk.write_bytes(b"MZ" + b"\0" * 100)
    assert PeFile.load(junk) is None
    assert struct.calcsize("<I") == 4
