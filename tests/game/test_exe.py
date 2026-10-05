"""gta_sa.exe variants (SPEC §4.1 game.exe, research report 13 §2.3)."""

from __future__ import annotations

import os
import struct

import pytest

from satk.core.errors import SatkError
from satk.game import exe as X


def _slow_checksum(data: bytes) -> int:
    """Reference CheckSumMappedFile: word-by-word with end-around carry."""
    lf = struct.unpack_from("<I", data, 0x3C)[0]
    off = lf + 24 + 64
    b = bytearray(data)
    b[off:off + 4] = bytes(4)
    if len(b) % 2:
        b.append(0)
    s = 0
    for (w,) in struct.iter_unpack("<H", b):
        s += w
        s = (s & 0xFFFF) + (s >> 16)
    s = (s & 0xFFFF) + (s >> 16)
    return (s & 0xFFFF) + len(data)


def _tiny_pe(n: int, seed: int) -> bytes:
    b = bytearray(os.urandom(n)) if seed < 0 else bytearray((i * 131 + seed) & 0xFF for i in range(n))
    b[0:2] = b"MZ"
    struct.pack_into("<I", b, 0x3C, 0x40)
    b[0x40:0x44] = b"PE\0\0"
    return bytes(b)


@pytest.mark.parametrize("n,seed", [(512, 1), (513, 7), (4096, 3), (70001, -1), (200000, -1)])
def test_pe_checksum_matches_reference(n, seed):
    d = _tiny_pe(n, seed)
    assert X.pe_checksum(d) == _slow_checksum(d)


def test_pe_checksum_ignores_stored_field():
    d = bytearray(_tiny_pe(1024, 5))
    a = X.pe_checksum(d)
    struct.pack_into("<I", d, 0x40 + 24 + 64, 0xDEADBEEF)
    assert X.pe_checksum(d) == a


def test_not_a_pe():
    with pytest.raises(SatkError) as ei:
        X.pe_info(b"hello world" * 10)
    assert ei.value.code == "BAD_PARAMS"
    assert X.identify(b"hello")["pe"] is None


def test_to_stock_reverts_nointro_patch(local_exe):
    local = local_exe
    stock = X.to_stock(local, strict=False)
    end = X.PATCH_OFFSET + 12
    assert stock[X.PATCH_OFFSET:end] == X.NOINTRO_ORIGINAL
    assert stock[:X.PATCH_OFFSET] == local[:X.PATCH_OFFSET] and stock[end:] == local[end:]
    assert X.identify(local).get("nointro_patch") is True


def test_build_mta_sets_real_checksum(local_exe):
    mta = X.build(local_exe, "mta", strict=False)
    lf = struct.unpack_from("<I", mta, 0x3C)[0]
    assert struct.unpack_from("<I", mta, lf + 24 + 64)[0] == X.pe_checksum(mta)
    stock = X.build(local_exe, "stock", strict=False)
    assert len(stock) == len(mta) and stock != mta


def test_strict_refuses_unknown_images(local_exe):
    with pytest.raises(SatkError) as ei:
        X.to_stock(local_exe)
    assert ei.value.code == "UNSUPPORTED"
    with pytest.raises(SatkError) as ei:
        X.build(local_exe, "compact")
    assert ei.value.code == "BAD_PARAMS"


def test_identify_tables(local_exe):
    assert X.KNOWN_EXE[X.STOCK_SHA256].name == "hoodlum-stock"
    assert X.KNOWN_EXE[X.LOCAL_SHA256].name == "hoodlum-nointro"
    assert X.KNOWN_EXE[X.MTA_SHA256].name == "mta-canonical"
    assert X.KNOWN_EXE[X.COMPACT_SHA256].name == "compact-1.0us"
    assert all(len(h) == 64 for h in X.KNOWN_EXE)
    assert X.identify_vorbisfile(X.VORBISFILE_STOCK_SHA256) == "stock"
    assert X.identify_vorbisfile(X.VORBISFILE_ASI_SHA256) == "asi-loader"
    assert X.identify_vorbisfile(None) == "missing" and X.identify_vorbisfile("0" * 64) == "unknown"
    assert X.identify(local_exe)["variant"] == "unknown"


# --------------------------------------------------------------------------- real executables


@pytest.mark.game
def test_real_clean_exe(clean_root):
    data = X.read_exe(clean_root / "gta_sa.exe")
    ident = X.identify(data)
    assert ident["variant"] == "hoodlum-stock" and ident["size"] == X.STOCK_SIZE
    assert ident["pe"]["sections"] == 11 and ident["pe"]["checksum"] == "0xdc5bea"
    assert X.pe_checksum(data) == X.MTA_HDR_CHECKSUM
    import hashlib

    assert hashlib.md5(data).hexdigest() == X.STOCK_MD5
    mta = X.build(data, "mta")
    assert X.sha256(mta) == X.MTA_SHA256
    assert X.sha256(X.build(mta, "stock")) == X.STOCK_SHA256  # and back


@pytest.mark.game
def test_real_install_exe(installed_root):
    data = X.read_exe(installed_root / "gta_sa.exe")
    assert X.identify(data)["variant"] == "hoodlum-nointro"
    assert X.sha256(X.build(data, "stock")) == X.STOCK_SHA256
    assert X.sha256(X.build(data, "mta")) == X.MTA_SHA256
