"""The SP bridge address table: internal consistency, C++ header parity, KB and exe cross-checks."""

from __future__ import annotations

import struct

import pytest

from satk.core.config import REPO_ROOT
from satk.core.registry import get_op, invoke
from satk.spbridge import addresses as A


def test_table_consistency():
    syms = A.all_symbols()
    names = [s.name for s in syms]
    assert len(names) == len(set(names)), "duplicate address names"
    for s in syms:
        assert s.kind in ("func", "global", "hook")
        # gta_sa.exe 1.0 US is a ~14 MB image based at 0x400000.
        assert 0x400000 <= s.addr <= 0x1600000, f"{s.name} 0x{s.addr:X} outside the image"
        assert s.symbol
    # hook site carries a plausible call target
    for h in A.HOOKS:
        assert 0x400000 <= h.target <= 0x1600000
    # every pool has a positive stride and a known kind
    from satk.saap.protocol import ENTITY_KINDS

    for p in A.POOLS:
        assert p.elem_size > 0
        assert p.kind in ENTITY_KINDS


def test_entity_types_and_flags():
    assert A.ENTITY_TYPES[1] == "building" and A.ENTITY_TYPES[2] == "vehicle"
    assert A.STREAMING_FLAGS["mission_required"] == 0x04
    assert A.ENTITY_OFFSETS["CCam.size"] * 3 == 0x6A8  # m_aCams is std::array<CCam, 3>


def test_cpp_header_matches_table():
    """native/sp_bridge/sp_addresses.hpp must be exactly what the table emits (no drift)."""
    hpp = REPO_ROOT / "native" / "sp_bridge" / "sp_addresses.hpp"
    assert hpp.is_file(), "generate it with: satk sp addresses --emit-header"
    on_disk = hpp.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert on_disk == A.cpp_header(), "sp_addresses.hpp is stale; run `satk sp addresses --emit-header`"


def _kb_addrs(symbol: str) -> set[int] | None:
    try:
        get_op("kb.sym")
    except Exception:
        return None
    r = invoke("kb.sym", {"name": symbol, "limit": 6})
    if not r.get("ok"):
        return None
    out: set[int] = set()
    for row in r.get("rows", []):
        d = dict(zip(r["cols"], row))
        av = d.get("addr")
        if isinstance(av, str) and av.startswith("0x"):
            out.add(int(av, 16))
    return out


def test_addresses_match_knowledge_base():
    """Every func/global in the table appears at that address in the knowledge base."""
    probe = _kb_addrs("CGame::Process")
    if not probe:
        pytest.skip("knowledge base not built (satk kb build)")
    mismatches = []
    checked = 0
    for s in A.FUNCS + A.GLOBALS:
        addrs = _kb_addrs(s.symbol)
        if not addrs:
            continue
        checked += 1
        if s.addr not in addrs:
            mismatches.append(f"{s.name} ({s.symbol}): 0x{s.addr:X} not in {sorted(hex(a) for a in addrs)}")
    assert checked >= 20, f"too few symbols cross-checked ({checked})"
    assert not mismatches, "address table disagrees with the KB:\n" + "\n".join(mismatches)


@pytest.mark.game
def test_hook_site_and_prologues(clean_root):
    """The hook `call` really targets CGame::Process and functions have real code (clean exe)."""
    from satk.re.pe import PeImage

    pe = PeImage.open(clean_root / "gta_sa.exe")
    assert pe.image_base == 0x400000
    h = A.HOOKS[0]
    site = pe.read(h.addr, 5)
    assert len(site) == 5 and site[0] == 0xE8, "hook site is not a `call rel32`"
    rel = struct.unpack("<i", site[1:5])[0]
    target = (h.addr + 5 + rel) & 0xFFFFFFFF
    assert target == h.target, f"call targets 0x{target:X}, table says 0x{h.target:X}"
    # a handful of functions must be backed by non-empty code in the image
    for s in A.FUNCS[:6]:
        assert len(pe.read(s.addr, 8)) == 8, f"{s.name} has no code at 0x{s.addr:X}"
