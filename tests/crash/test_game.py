"""The heuristic on the real gta_sa.exe (read-only, ``game`` marker): CALL checks read the clean exe."""

from __future__ import annotations

import struct

import pytest

from satk.crash.images import Images, PeFile
from satk.crash.minidump import Minidump
from satk.crash.stack import walk
from satk.crash.synth import DumpBuilder

pytestmark = pytest.mark.game


def test_return_addresses_verified_against_clean_exe(clean_root):
    exe = clean_root / "gta_sa.exe"
    pe = PeFile.load(exe)
    assert pe is not None and pe.image_base == 0x400000 and not pe.is64
    b = DumpBuilder("x86")
    b.add_module(r"C:\elsewhere\gta_sa.exe", 0x400000, pe.size_of_image, timestamp=pe.timestamp)
    stack = bytearray(0x100)
    # 1.0 US: CGame::Process calls CStreaming::Update at 0x53BF0B; Game::Idle calls CGame::Process at 0x53E981
    for off, v in ((0x4, 0x00B74490), (0x8, 0x53BF10), (0xC, 0x53BEE0), (0x10, 0x53E986)):
        struct.pack_into("<I", stack, off, v)
    b.add_thread(1, {"eip": 0x40E6A3, "esp": 0x1000, "ebp": 0}, 0x1000, bytes(stack))
    b.set_exception(1, 0xC0000005, 0x40E6A3, [0, 0x10])
    d = Minidump(b.build())
    images = Images(d, game_exes=[exe])
    frames, _ = walk(d, d.exception.context, d.thread(1), images)
    got = [(hex(f.va), f.call) for f in frames]
    # the pool global (.data) and the function entry (no CALL before it) are dropped
    assert got == [("0x40e6a3", None), ("0x53bf10", True), ("0x53e986", True)]
    assert frames[1].target == 0x40E670 and frames[2].target == 0x53BEE0
    assert images.used["gta_sa.exe"].lower().endswith("gta_sa.exe")
