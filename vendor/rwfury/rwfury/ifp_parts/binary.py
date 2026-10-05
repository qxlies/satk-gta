"""Shared binary primitives for IFP codecs."""

from __future__ import annotations

import io
import struct


IFP_ANP3_MAGIC = b"ANP3"
IFP_ANPK_MAGIC = b"ANPK"
IFP_V2_NAME_SIZE = 24
IFP_V2_QUAT_SCALE = 4096.0
IFP_V2_TRANS_SCALE = 1024.0
IFP_V2_TIME_SCALE = 60.0


def read_exact(stream: io.BytesIO, count: int) -> bytes:
    data = stream.read(count)
    if len(data) != count:
        raise EOFError(f"Expected {count} bytes, got {len(data)}")
    return data


def slice_ifp_data(data: bytes) -> bytes:
    if len(data) < 8:
        raise EOFError("Expected at least 8 bytes for IFP header")

    if data.startswith((IFP_ANP3_MAGIC, IFP_ANPK_MAGIC)):
        size = struct.unpack("<I", data[4:8])[0] + 8
        if size > len(data):
            raise EOFError(f"Expected {size} bytes, got {len(data)}")
        return data[:size]

    return data


def decode_string(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def encode_name(value: str) -> bytes:
    try:
        return value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"IFP names must be ASCII: {value!r}") from exc


def read_fixed_string_raw(
    stream: io.BytesIO,
    size: int,
) -> tuple[str, bytes]:
    raw = read_exact(stream, size)
    return decode_string(raw), raw


def pack_fixed_string(value: str, size: int) -> bytes:
    encoded = encode_name(value)
    if len(encoded) > size:
        raise ValueError(
            f"IFP name {value!r} exceeds the {size}-byte field"
        )
    return encoded.ljust(size, b"\x00")


def pack_preserved_fixed_string(
    value: str,
    size: int,
    raw: bytes | None,
) -> bytes:
    if raw is not None and len(raw) == size and decode_string(raw) == value:
        return raw
    return pack_fixed_string(value, size)


def pack_c_string(value: str) -> bytes:
    return encode_name(value) + b"\x00"


def pack_preserved_c_string(value: str, raw: bytes | None) -> bytes:
    if raw is not None and decode_string(raw) == value:
        return raw
    return pack_c_string(value)


def read_any_section_full(
    stream: io.BytesIO,
) -> tuple[bytes, bytes, bytes]:
    magic = read_exact(stream, 4)
    size = struct.unpack("<I", read_exact(stream, 4))[0]
    data = read_exact(stream, size)
    padding_size = align4(size) - size
    padding = read_exact(stream, padding_size) if padding_size else b""
    return magic, data, padding


def read_section_full(
    stream: io.BytesIO,
    expected: bytes,
) -> tuple[bytes, bytes]:
    magic, data, padding = read_any_section_full(stream)
    if magic != expected:
        raise ValueError(f"Expected ANPK section {expected!r}, got {magic!r}")
    return data, padding


def pack_section(
    magic: bytes,
    data: bytes,
    preserved_padding: bytes | None = None,
) -> bytes:
    if len(magic) != 4:
        raise ValueError(f"IFP section identifiers must be four bytes: {magic!r}")
    padding_size = align4(len(data)) - len(data)
    padding = (
        preserved_padding
        if preserved_padding is not None and len(preserved_padding) == padding_size
        else b"\x00" * padding_size
    )
    return magic + struct.pack("<I", len(data)) + data + padding


def align4(value: int) -> int:
    return (value + 3) & ~3


def to_i16(value: float) -> int:
    rounded = int(round(value))
    if not -32768 <= rounded <= 32767:
        raise ValueError(f"IFP value {value!r} does not fit in a signed 16-bit field")
    return rounded


def to_i32(value: int) -> int:
    value &= 0xFFFFFFFF
    if value >= 0x80000000:
        return value - 0x100000000
    return value


def to_u32(value: int) -> int:
    return value & 0xFFFFFFFF
