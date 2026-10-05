"""RenderWare binary chunk reader/writer."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO

# RenderWare chunk IDs
RW_STRUCT = 0x0001
RW_STRING = 0x0002
RW_EXTENSION = 0x0003
RW_TEXTURE = 0x0006
RW_MATERIAL = 0x0007
RW_MATERIAL_LIST = 0x0008
RW_FRAME_LIST = 0x000E
RW_GEOMETRY = 0x000F
RW_CLUMP = 0x0010
RW_LIGHT = 0x0012
RW_ATOMIC = 0x0014
RW_TEXTURE_NATIVE = 0x0015
RW_TEXTURE_DICTIONARY = 0x0016
RW_GEOMETRY_LIST = 0x001A
RW_ANIM_ANIMATION = 0x001B
RW_UV_ANIM_DICT = 0x002B

# Extension plugin IDs
RW_FRAME_NAME = 0x0253F2FE  # Frame name (right to render)
RW_HANIM = 0x011E
RW_UV_ANIM_PLG = 0x0135
RW_BIN_MESH = 0x050E
RW_SKIN = 0x0116
RW_MAT_EFFECTS = 0x0120
RW_2DFX = 0x0253F2F8
RW_NIGHT_COLORS = 0x0253F2F9
RW_COLLISION = 0x0253F2FA
RW_SPECULAR_MAT = 0x0253F2F6
RW_REFLECTION_MAT = 0x0253F2FC
RW_PIPELINE_SET = 0x0253F2F3

# Geometry flags
GEO_TRISTRIP = 0x01
GEO_POSITIONS = 0x02
GEO_TEXTURED = 0x04
GEO_PRELIT = 0x08  # vertex colors
GEO_NORMALS = 0x10
GEO_LIGHT = 0x20
GEO_MODULATE_COLOR = 0x40
GEO_TEXTURED2 = 0x80  # multi-texcoord
GEO_NATIVE = 0x01000000


@dataclass
class ChunkHeader:
    id: int
    size: int
    version: int


class RwBinaryReader:
    """Reads RenderWare binary stream chunk-by-chunk."""

    def __init__(self, stream: BinaryIO):
        self._stream = stream
        self._stream.seek(0, 2)
        self._file_size = self._stream.tell()
        self._stream.seek(0)

    @classmethod
    def from_file(cls, path: str) -> RwBinaryReader:
        return cls(open(path, "rb"))

    def close(self):
        self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def tell(self) -> int:
        return self._stream.tell()

    def seek(self, pos: int):
        self._stream.seek(pos)

    def skip(self, n: int):
        self._stream.seek(n, 1)

    @property
    def file_size(self) -> int:
        return self._file_size

    def read_bytes(self, n: int) -> bytes:
        data = self._stream.read(n)
        if len(data) < n:
            raise EOFError(f"Expected {n} bytes, got {len(data)}")
        return data

    def read_u8(self) -> int:
        return struct.unpack("<B", self.read_bytes(1))[0]

    def read_u16(self) -> int:
        return struct.unpack("<H", self.read_bytes(2))[0]

    def read_u32(self) -> int:
        return struct.unpack("<I", self.read_bytes(4))[0]

    def read_i16(self) -> int:
        return struct.unpack("<h", self.read_bytes(2))[0]

    def read_i32(self) -> int:
        return struct.unpack("<i", self.read_bytes(4))[0]

    def read_f32(self) -> float:
        return struct.unpack("<f", self.read_bytes(4))[0]

    def read_chunk_header(self) -> ChunkHeader:
        chunk_id = self.read_u32()
        size = self.read_u32()
        version = self.read_u32()
        return ChunkHeader(id=chunk_id, size=size, version=version)

    def read_string(self, size: int) -> str:
        """Read a fixed-size string, stripping null bytes."""
        data = self.read_bytes(size)
        null_idx = data.find(b"\x00")
        if null_idx >= 0:
            data = data[:null_idx]
        return data.decode("ascii", errors="replace")


class RwBinaryWriter:
    """Writes RenderWare binary stream."""

    def __init__(self, stream: BinaryIO):
        self._stream = stream

    @classmethod
    def to_file(cls, path: str) -> RwBinaryWriter:
        return cls(open(path, "wb"))

    def close(self):
        self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def tell(self) -> int:
        return self._stream.tell()

    def seek(self, pos: int):
        self._stream.seek(pos)

    def write_bytes(self, data: bytes):
        self._stream.write(data)

    def write_u8(self, value: int):
        self._stream.write(struct.pack("<B", value))

    def write_u16(self, value: int):
        self._stream.write(struct.pack("<H", value))

    def write_u32(self, value: int):
        self._stream.write(struct.pack("<I", value))

    def write_i32(self, value: int):
        self._stream.write(struct.pack("<i", value))

    def write_f32(self, value: float):
        self._stream.write(struct.pack("<f", value))

    def write_chunk_header(self, chunk_id: int, size: int, version: int):
        self.write_u32(chunk_id)
        self.write_u32(size)
        self.write_u32(version)

    def write_string(self, s: str, size: int):
        """Write a fixed-size null-padded string."""
        data = s.encode("ascii", errors="replace")[:size]
        data = data + b"\x00" * (size - len(data))
        self.write_bytes(data)

    def write_null(self, count: int = 1):
        self._stream.write(b"\x00" * count)
