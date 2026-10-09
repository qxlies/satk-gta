"""Synthetic PE32 images and site-tool fixtures for the engine tests (no game data)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

from satk.engine.securom import find_keys
from satk.engine.sites_data import RelocFunc, SiteInputs, Stolen, TrunkPatch
from satk.re.pe import PeImage

IMAGE_BASE = 0x400000


def build_pe(sections: list[tuple[str, int, bytes, int | None]]) -> bytes:
    """A PE32 file; ``sections`` = ``(name, absolute va, raw bytes, vsize or None)``."""
    nsec = len(sections)
    opt_size = 0xE0
    hdr_end = 0x80 + 24 + opt_size + 40 * nsec
    first_raw = (hdr_end + 0x1FF) & ~0x1FF
    dos = bytearray(0x80)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x80)
    coff = struct.pack("<HHIIIHH", 0x14C, nsec, 0, 0, 0, opt_size, 0x102)
    opt = bytearray(opt_size)
    struct.pack_into("<H", opt, 0, 0x10B)
    struct.pack_into("<I", opt, 16, 0x1000)  # entry rva
    struct.pack_into("<I", opt, 28, IMAGE_BASE)
    struct.pack_into("<I", opt, 32, 0x1000)  # section alignment
    struct.pack_into("<I", opt, 36, 0x200)  # file alignment
    struct.pack_into("<I", opt, 56, 0x2000000)  # size of image (not checked)
    headers = bytearray(dos) + b"PE\0\0" + coff + bytes(opt)
    raw_blob = bytearray()
    table = bytearray()
    raw_off = first_raw
    for name, va, data, vsize in sections:
        rsize = (len(data) + 0x1FF) & ~0x1FF
        table += struct.pack("<8sIIIIIIHHI", name.encode("ascii"), vsize or len(data), va - IMAGE_BASE, rsize,
                             raw_off, 0, 0, 0, 0, 0x60000020)
        raw_blob += data + bytes(rsize - len(data))
        raw_off += rsize
    out = bytearray(headers + table)
    out += bytes(first_raw - len(out))
    out += raw_blob
    return bytes(out)


def i32(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


def rel32(src_va: int, dst_va: int) -> bytes:
    """Operand of a 5-byte ``jmp``/``call`` at ``src_va`` targeting ``dst_va``."""
    return struct.pack("<i", dst_va - (src_va + 5))


# Layout of the fixture exe (.text at 0x401000, .HOODLUM at 0x1556000).
TEXT_VA = 0x401000
HOOD_VA = 0x1556000
RELOC_ENTRY = 0x401000  # jmp into .HOODLUM, dead leftover up to NEXT_FUNC
NEXT_FUNC = 0x401040
POOLS_VA = 0x401040  # push 140 / push 500 sequences
KEY_STUB_VA = 0x401100
KEY_VA = 0x4010F0
TABLE_READ = 0x1557010
STOLEN_VA = 0x401200
STUB_VA = 0x401020  # a stub inside the reloc slot (stolen-instruction stub pool)
ARRAY_BASE = 0x700000
ARRAY_SIZE = 0x100
ARRAY_STRIDE = 0x10
REFS_VA = 0x401300


def fixture_text() -> bytearray:
    t = bytearray(b"\xcc" * 0x1000)

    def put(va: int, data: bytes) -> None:
        t[va - TEXT_VA:va - TEXT_VA + len(data)] = data

    put(RELOC_ENTRY, b"\xe9" + rel32(RELOC_ENTRY, HOOD_VA + 0x100))
    put(0x401005, bytes(range(0x30, 0x30 + 27)))  # dead leftover
    put(STUB_VA, bytes([0x90] * 8))
    # pools: push 140 ; mov ecx,eax ; call -> bytes the manifests verify
    put(POOLS_VA, bytes.fromhex("688C0000008BC8E800000000"))  # operand at 0x401041
    put(POOLS_VA + 0x10, bytes.fromhex("6A408BC8E800000000"))  # imm8 at 0x401051
    put(POOLS_VA + 0x20, bytes.fromhex("56578B7C240C8BC769C0180A000050"))  # 15 bytes, 0x401060
    put(KEY_STUB_VA, bytes.fromhex("9C") + b"\xa1" + i32(TABLE_READ) + b"\x33\x05" + i32(KEY_VA) + b"\x9d" + b"\xc3")
    put(KEY_VA, i32(0x11223344))
    put(STOLEN_VA, b"\xe9" + rel32(STOLEN_VA, STUB_VA) + b"\x90\x90")
    # array operands (instructions the scan classifies)
    ops = [
        (REFS_VA, bytes.fromhex("8B0485") + i32(ARRAY_BASE)),  # mov eax,[eax*4+base]            operand-in-array
        (REFS_VA + 0x10, bytes.fromhex("81FE") + i32(ARRAY_BASE + ARRAY_SIZE)),  # cmp esi,end      true-end
        (REFS_VA + 0x20, bytes.fromhex("3D") + i32(ARRAY_BASE + ARRAY_SIZE + 8)),  # cmp eax,end+8  end-plus-field
        (REFS_VA + 0x30, bytes.fromhex("A1") + i32(ARRAY_BASE + ARRAY_SIZE)),  # mov eax,[end]       variable-after-array
        (REFS_VA + 0x40, bytes.fromhex("BE") + i32(ARRAY_BASE + ARRAY_SIZE)),  # mov esi,offset end  variable-after-array
        (REFS_VA + 0x50, bytes.fromhex("8D81") + i32(ARRAY_BASE + 4)),  # lea eax,[ecx+base+4]       operand-in-array
        (REFS_VA + 0x60, bytes.fromhex("A1") + i32(ARRAY_BASE + ARRAY_SIZE + ARRAY_STRIDE)),  # last legal operand
        (REFS_VA + 0x70, bytes.fromhex("A1") + i32(ARRAY_BASE + ARRAY_SIZE + ARRAY_STRIDE + 1)),  # one byte too far
    ]
    for va, data in ops:
        put(va, data)
    return t


def fixture_exe_bytes() -> bytes:
    hood = bytearray(b"\x00" * 0x400)
    return build_pe([(".text", TEXT_VA, bytes(fixture_text()), 0x1000), (".HOODLUM", HOOD_VA, bytes(hood), 0x2000)])


def fixture_image() -> PeImage:
    return PeImage(fixture_exe_bytes())


def fixture_inputs(image: PeImage | None = None, trunk: list[TrunkPatch] | None = None) -> SiteInputs:
    img = image or fixture_image()
    scan = find_keys(img)
    return SiteInputs(
        img, scan,
        stolen=[Stolen(STOLEN_VA, 7, STUB_VA, "moved-plain")],
        relocs=[RelocFunc(RELOC_ENTRY, NEXT_FUNC, HOOD_VA + 0x100, "FakeRelocated")],
        trunk=list(trunk or []),
    )


def write_xrefs(path: Path, refs: list[tuple[int, int, str | None]]) -> None:
    """A minimal ``xrefs_data.jsonl``: ``refs`` = ``(target, from, function name)``."""
    by_t: dict[int, list] = {}
    for tgt, frm, fn in refs:
        by_t.setdefault(tgt, []).append({"from": f"0x{frm:X}", "func": "0x401300" if fn else None, "func_name": fn,
                                         "type": "READ", "op": 0, "from_kind": "code", "from_region": "text"})
    with open(path, "w", encoding="utf-8") as f:
        for tgt in sorted(by_t):
            f.write(json.dumps({"addr": f"0x{tgt:X}", "refs": by_t[tgt]}) + "\n")


def manifest_text(exe_sha: str, extra_sites: str = "", extra_groups: str = "") -> str:
    """A valid manifest for the fixture exe (one group of two sites + one anchors group)."""
    return f'''schema = 1
exe_sha256 = "{exe_sha}"

[[group]]
id = "id.anchors"
report_id = 91001
phase = "ctor"
lane = "E1"
note = "read only"

  [[group.site]]
  id = "entry"
  va = 0x401000
  len = 5
  kind = "code"
  golden_va = 0x401000
  golden = "E9 {' '.join(f'{b:02X}' for b in (b'' + rel32(0x401000, HOOD_VA + 0x100)))}"

[[group]]
id = "cap.pools"
report_id = 91101
phase = "ctor"
lane = "E2"
note = "fixture pools"

  [[group.site]]
  id = "pool.ped"
  va = 0x401041
  len = 4
  kind = "imm32"
  golden_va = 0x401040
  golden = "68 8C 00 00 00 8B C8 E8"
  alt = "68 xx xx xx xx 8B C8 E8"
  note = "push 140"

  [[group.site]]
  id = "pool.route"
  va = 0x401051
  len = 1
  kind = "imm8"
  golden_va = 0x401050
  golden = "6A 40 8B C8 E8"
{extra_sites}{extra_groups}'''
