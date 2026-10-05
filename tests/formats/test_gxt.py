"""satk.formats.gxt: SA text files (synthetic GXT + read-only checks of the real american.gxt)."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from satk.formats.gxt import key_hash, load_gxt, parse_gxt
from satk.formats.rw import FormatError



def build_gxt(tables: dict[str, dict[str, str]], bits: int = 8) -> bytes:
    """A minimal SA (v4) GXT: TABL, then per table [name] TKEY + TDAT."""
    enc = (lambda s: s.encode("latin-1") + b"\0") if bits == 8 else (lambda s: s.encode("utf-16-le") + b"\0\0")
    names = list(tables)
    head = struct.pack("<HH", 4, bits)
    tabl_size = 12 * len(names)
    pos = 4 + 8 + tabl_size
    tabl, body = b"", b""
    for name in names:
        tabl += struct.pack("<8sI", name.encode(), pos + len(body))
        block = b"" if name == "MAIN" else name.encode().ljust(8, b"\0")
        tdat, keys = b"", b""
        for k, v in tables[name].items():
            keys += struct.pack("<II", len(tdat), key_hash(k))
            tdat += enc(v)
        block += b"TKEY" + struct.pack("<I", len(keys)) + keys + b"TDAT" + struct.pack("<I", len(tdat)) + tdat
        body += block
    return head + b"TABL" + struct.pack("<I", tabl_size) + tabl + body


def test_key_hash_is_jamcrc_of_upper_key():
    assert key_hash("gan") == key_hash("GAN")
    assert key_hash("GAN") == 0xFFFFFFFF ^ __import__("zlib").crc32(b"GAN")


@pytest.mark.parametrize("bits", [8, 16])
def test_parse_tables_and_lookup(bits):
    g = parse_gxt(build_gxt({"MAIN": {"GAN": "Ganton", "IWD": "Idlewood"}, "EXTRA": {"X1": "Hello"}}, bits))
    assert g.bits == bits and set(g.tables) == {"MAIN", "EXTRA"}
    assert g.text("gan") == "Ganton" and g.text("IWD") == "Idlewood"
    assert g.text("X1", table="EXTRA") == "Hello" and g.text("missing") is None


@pytest.mark.parametrize("mutate", [
    lambda d: d[:6],                                   # truncated header
    lambda d: struct.pack("<HH", 3, 8) + d[4:],        # not SA
    lambda d: d.replace(b"TKEY", b"TKEZ", 1),          # broken block magic
    lambda d: d[:-3],                                  # truncated TDAT
])
def test_malformed_raises_format_error(mutate):
    data = build_gxt({"MAIN": {"GAN": "Ganton"}})
    with pytest.raises(FormatError):
        parse_gxt(mutate(data))


def test_text_offset_outside_tdat():
    data = bytearray(build_gxt({"MAIN": {"GAN": "Ganton"}}))
    i = data.index(b"TKEY") + 8
    struct.pack_into("<I", data, i, 10_000)
    with pytest.raises(FormatError, match="outside TDAT"):
        parse_gxt(bytes(data))


@pytest.mark.game
def test_real_american_gxt_zone_names(clean_root):
    g = load_gxt(clean_root / "text" / "american.gxt")
    assert len(g.tables) == 127 and len(g.tables["MAIN"]) == 5428
    assert (g.text("GAN"), g.text("IWD"), g.text("LA"), g.text("STAR")) == (
        "Ganton", "Idlewood", "Los Santos", "Starfish Casino")
