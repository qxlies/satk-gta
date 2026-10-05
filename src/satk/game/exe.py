"""``gta_sa.exe`` variants: identify, restore stock, build the MTA-canonical image (SPEC §4.1).

Lineage (research report 13 §2.3, all verified by hash)::

    1.0 US HOODLUM (stock)     a559aa77...  14 383 616 B, header CheckSum 0xDC5BEA (stale; real 0xDBD689)
    +- local install copy      1d7c531f...  = stock + 12-byte "skip splashes" patch at file 0x347EA8 (VA 0x748AA8)
    +- MTA canonical           f63fa623...  = stock with the PE CheckSum field corrected to 0xDBD689
    |  +- MTA ProgramData runtime (LAA, NX, WINMM -> mtasa.dll, aero/optimus variants)
    +- 1.0 US Compact          72ae59e4...  5 189 632 B (gta-reversed); not derivable here

Everything happens in memory; the input is read with ``open_ro`` and the result is written
only by the caller (``satk game exe --out`` never writes into a game folder).
Standard library only.
"""

from __future__ import annotations

import hashlib
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from satk.core.errors import SatkError
from satk.core.paths import open_ro

__all__ = [
    "STOCK_SHA256",
    "STOCK_MD5",
    "LOCAL_SHA256",
    "MTA_SHA256",
    "VORBISFILE_STOCK_SHA256",
    "VORBISFILE_ASI_SHA256",
    "KNOWN_EXE",
    "KNOWN_VORBISFILE",
    "VARIANT_SHA256",
    "ExeVariant",
    "sha256",
    "pe_info",
    "pe_checksum",
    "identify",
    "identify_vorbisfile",
    "to_stock",
    "build",
    "read_exe",
]

STOCK_SHA256 = "a559aa772fd136379155efa71f00c47aad34bbfeae6196b0fe1047d0645cbd26"
STOCK_MD5 = "170b3a9108687b26da2d8901c6948a18"
STOCK_SIZE = 14_383_616
LOCAL_SHA256 = "1d7c531fdc5128977aef31cdab163bc4e64aa128a19b479f3fc71c9ea4148ac8"
MTA_SHA256 = "f63fa623d14e170d0ae9e7032186669cf3a177793be21c4824043f0279bd506a"
COMPACT_SHA256 = "72ae59e44c761389e354a50dc6215e964fe771121e2f4b1877273a493ceecc9b"

#: Original ``vorbisFile.dll`` (= MTA's reference) and Silent's ASI Loader that replaced it.
VORBISFILE_STOCK_SHA256 = "a08923479000cec366967fb8259e0920b7aa18859722c7dda1415726bed4774f"
VORBISFILE_ASI_SHA256 = "6ccf42587caeee3a09f6372a4e73cc657ea67c7fdc0234017a39adaac6d7bc80"

#: File offset of the no-intro patch (VA 0x748AA8 in WinMain).
PATCH_OFFSET = 0x347EA8
#: ``mov dword [gGameState=0xC8D4C0], 5 ; jmp short -0x21`` (skip the splash screens).
NOINTRO_PATCH = bytes.fromhex("c705c0d4c80005000000ebdf")
#: ``push ebx; push edi; call CLoadingScreen::LoadSplashes; push edi; push edi; call ...``
NOINTRO_ORIGINAL = bytes.fromhex("5357e80176e4ff5757e8fa77")
#: CheckSum field of the stock HOODLUM header (stale: the real checksum is 0xDBD689).
STOCK_HDR_CHECKSUM = 0x00DC5BEA
MTA_HDR_CHECKSUM = 0x00DBD689
MAX_EXE_SIZE = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ExeVariant:
    name: str
    desc: str
    derivable: bool = False  # satk can produce the stock image from it


KNOWN_EXE: dict[str, ExeVariant] = {
    STOCK_SHA256: ExeVariant("hoodlum-stock", "GTA SA 1.0 US HOODLUM, stock", True),
    LOCAL_SHA256: ExeVariant("hoodlum-nointro", "stock + 12-byte skip-splashes patch (audited local install)", True),
    MTA_SHA256: ExeVariant("mta-canonical", "stock with corrected PE checksum (MTA HASH_GTA_EXE)", True),
    "77485627b4ef17f92819318050d501e171c7ab84ceffe5091b973b9e29f9cc98":
        ExeVariant("mta-programdata", "MTA ProgramData runtime: Release, aero=1, optimus=0"),
    "f3d07cca3375af2fe4d19ff4035c444f2d4fd5a9c07fa78ed834bd6756d5b37c":
        ExeVariant("mta-programdata-noaero", "MTA ProgramData runtime: Release, aero=0, optimus=0"),
    "5b01125db4b0bf04592bd22ddc69245eb391e9b626a950525f95cfb08f676cce":
        ExeVariant("mta-programdata-noaero-optimus", "MTA ProgramData runtime: Release, aero=0, optimus=1"),
    "1c8e082037446cd239eed9c924afff8ced3891cd9a426fdd1c9317671cfcf4b9":
        ExeVariant("mta-programdata-optimus", "MTA ProgramData runtime: Release, aero=1, optimus=1"),
    "416cdc04f19b317393a6dfc404be28449e6dd38bfc63cc3cccc10e5fb51f3eae":
        ExeVariant("mta-programdata-debug", "MTA ProgramData runtime: Debug (mtasa_d.dll), aero=1"),
    COMPACT_SHA256: ExeVariant("compact-1.0us", "GTA SA 1.0 US Compact (gta-reversed reference)"),
}

KNOWN_VORBISFILE: dict[str, str] = {
    VORBISFILE_STOCK_SHA256: "stock",
    VORBISFILE_ASI_SHA256: "asi-loader",
}

#: ``satk game exe --variant`` -> expected SHA-256.
VARIANT_SHA256: dict[str, str] = {"stock": STOCK_SHA256, "mta": MTA_SHA256}


def sha256(data: bytes | bytearray | memoryview) -> str:
    return hashlib.sha256(data).hexdigest()


def _pe_offsets(data: bytes | bytearray) -> tuple[int, int]:
    """(offset of ``PE\\0\\0``, offset of the optional header); ``BAD_PARAMS`` if not a PE."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise SatkError("BAD_PARAMS", "not a PE executable (no MZ header)")
    lf = struct.unpack_from("<I", data, 0x3C)[0]
    if lf + 24 + 96 > len(data) or data[lf:lf + 4] != b"PE\0\0":
        raise SatkError("BAD_PARAMS", "not a PE executable (no PE signature)")
    return lf, lf + 24


def pe_info(data: bytes | bytearray) -> dict:
    """Header fields that identify a GTA executable: machine, sections, timestamp, sizes, checksum."""
    lf, opt = _pe_offsets(data)
    machine, nsec, ts = struct.unpack_from("<HHI", data, lf + 4)
    magic = struct.unpack_from("<H", data, opt)[0]
    size_of_image = struct.unpack_from("<I", data, opt + 56)[0]
    checksum = struct.unpack_from("<I", data, opt + 64)[0]
    return {
        "machine": f"0x{machine:x}",
        "magic": f"0x{magic:x}",
        "sections": nsec,
        "timestamp": f"0x{ts:08x}",
        "image_size": f"0x{size_of_image:x}",
        "checksum": f"0x{checksum:06x}",
    }


def pe_checksum(data: bytes | bytearray) -> int:
    """PE image checksum (``CheckSumMappedFile`` algorithm), ignoring the stored field."""
    _, opt = _pe_offsets(data)
    off = opt + 64
    buf = bytearray(data)
    buf[off:off + 4] = b"\0\0\0\0"
    n = len(buf)
    if n % 2:
        buf.append(0)
    if sys.byteorder == "little":
        total = sum(memoryview(buf).cast("H"))  # native 16-bit words = little-endian
    else:  # pragma: no cover - big-endian hosts
        total = sum(struct.unpack(f"<{len(buf) // 2}H", buf))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return total + n


def _set_checksum(buf: bytearray, value: int) -> None:
    _, opt = _pe_offsets(buf)
    struct.pack_into("<I", buf, opt + 64, value)


def identify(data: bytes | bytearray) -> dict:
    """``{"variant", "sha256", "size", "pe": {...}}``; ``variant`` is ``"unknown"`` if not listed."""
    h = sha256(data)
    v = KNOWN_EXE.get(h)
    out: dict = {"variant": v.name if v else "unknown", "sha256": h, "size": len(data)}
    if v:
        out["desc"] = v.desc
    try:
        out["pe"] = pe_info(data)
    except SatkError:
        out["pe"] = None
    if v is None and len(data) > PATCH_OFFSET + len(NOINTRO_PATCH) \
            and data[PATCH_OFFSET:PATCH_OFFSET + len(NOINTRO_PATCH)] == NOINTRO_PATCH:
        out["nointro_patch"] = True
    return out


def identify_vorbisfile(sha: str | None) -> str:
    """``stock`` / ``asi-loader`` / ``unknown`` / ``missing`` for a ``vorbisFile.dll`` hash."""
    if sha is None:
        return "missing"
    return KNOWN_VORBISFILE.get(sha, "unknown")


def to_stock(data: bytes | bytearray, *, strict: bool = True) -> bytearray:
    """Stock HOODLUM image from the local (no-intro) or MTA-canonical variant.

    With ``strict`` the result must hash to :data:`STOCK_SHA256` (``UNSUPPORTED`` otherwise,
    e.g. for the compact or ProgramData images). Without it only the structural transforms
    are applied (used with synthetic test images; ``clone`` checks the manifest hash itself).
    """
    buf = bytearray(data)
    h = sha256(buf)
    if h == STOCK_SHA256:
        return buf
    end = PATCH_OFFSET + len(NOINTRO_PATCH)
    if len(buf) >= end and buf[PATCH_OFFSET:end] == NOINTRO_PATCH:
        buf[PATCH_OFFSET:end] = NOINTRO_ORIGINAL
    elif h == MTA_SHA256:
        _set_checksum(buf, STOCK_HDR_CHECKSUM)
    if strict and sha256(buf) != STOCK_SHA256:
        v = KNOWN_EXE.get(h)
        raise SatkError(
            "UNSUPPORTED",
            f"cannot derive the stock executable from variant {v.name if v else 'unknown'} ({h[:12]}...)",
            hint="use the audited install exe (1d7c531f...) or a stock/MTA-canonical copy as --src",
            data={"sha256": h, "variant": v.name if v else "unknown"},
        )
    return buf


def build(data: bytes | bytearray, variant: str, *, strict: bool = True) -> bytes:
    """Image of ``variant`` (``stock`` or ``mta``) from any derivable input image."""
    if variant not in VARIANT_SHA256:
        raise SatkError("BAD_PARAMS", f"unknown exe variant {variant!r}", did_you_mean=list(VARIANT_SHA256))
    buf = to_stock(data, strict=strict)
    if variant == "mta":
        _set_checksum(buf, pe_checksum(buf))
    if strict:
        got = sha256(buf)
        if got != VARIANT_SHA256[variant]:  # pragma: no cover - guarded by to_stock
            raise SatkError("INTERNAL", f"{variant} image hashes to {got}, expected {VARIANT_SHA256[variant]}")
    return bytes(buf)


def read_exe(path: str | os.PathLike) -> bytes:
    """Read an executable with ``open_ro`` (size-limited)."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except FileNotFoundError:
        raise SatkError("NOT_FOUND", f"no executable at {p}") from None
    if size > MAX_EXE_SIZE:
        raise SatkError("BAD_PARAMS", f"{p} is {size} bytes; not a GTA executable")
    with open_ro(p) as f:
        return f.read()
