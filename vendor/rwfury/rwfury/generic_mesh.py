"""Format-agnostic mesh representation for easy porting to any 3D format."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field


@dataclass
class GenericMesh:
    """Self-contained mesh with flat arrays and byte-packing helpers.

    All vertex data is stored as flat lists:
      positions: [x,y,z, x,y,z, ...]        (3 floats per vertex)
      normals:   [nx,ny,nz, ...]             (3 floats per vertex, or empty)
      texcoords: [[u,v, u,v, ...], ...]      (2 floats per vertex, per UV set)
      colors:    [r,g,b,a, r,g,b,a, ...]     (4 ints 0-255 per vertex, or empty)
      indices:   [i0,i1,i2, ...]             (3 per triangle)

    Use the *_as_bytes() helpers to get packed binary data for GPU upload
    or writing to any binary format (glTF, FBX, custom engines, etc.).
    """

    name: str = ""
    material_index: int = 0

    # Vertex data (flat)
    positions: list[float] = field(default_factory=list)
    normals: list[float] = field(default_factory=list)
    texcoords: list[list[float]] = field(default_factory=list)
    colors: list[int] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)

    # Material
    texture_name: str = ""
    mask_name: str = ""
    diffuse_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    ambient: float = 1.0
    diffuse: float = 1.0
    specular: float = 0.0

    # Transform (4x4 row-major, 16 floats)
    transform: list[float] = field(default_factory=list)

    # Skinning (flat, 4 per vertex)
    bone_indices: list[int] = field(default_factory=list)
    bone_weights: list[float] = field(default_factory=list)

    @property
    def vertex_count(self) -> int:
        return len(self.positions) // 3

    @property
    def triangle_count(self) -> int:
        return len(self.indices) // 3

    @property
    def has_normals(self) -> bool:
        return len(self.normals) > 0

    @property
    def has_colors(self) -> bool:
        return len(self.colors) > 0

    @property
    def has_skinning(self) -> bool:
        return len(self.bone_weights) > 0

    @property
    def uv_set_count(self) -> int:
        return len(self.texcoords)

    # ------------------------------------------------------------------
    # Byte packing (little-endian)
    # ------------------------------------------------------------------

    def positions_as_bytes(self) -> bytes:
        """Pack positions as little-endian float32."""
        return struct.pack(f"<{len(self.positions)}f", *self.positions)

    def normals_as_bytes(self) -> bytes:
        """Pack normals as little-endian float32."""
        if not self.normals:
            return b""
        return struct.pack(f"<{len(self.normals)}f", *self.normals)

    def texcoords_as_bytes(self, uv_set: int = 0) -> bytes:
        """Pack a UV set as little-endian float32."""
        if uv_set >= len(self.texcoords):
            return b""
        uvs = self.texcoords[uv_set]
        return struct.pack(f"<{len(uvs)}f", *uvs)

    def colors_as_bytes(self) -> bytes:
        """Pack vertex colors as uint8 RGBA."""
        if not self.colors:
            return b""
        return struct.pack(f"<{len(self.colors)}B", *self.colors)

    def indices_as_bytes(self, fmt: str = "u16") -> bytes:
        """Pack indices as uint16 ('u16') or uint32 ('u32')."""
        if fmt == "u16":
            return struct.pack(f"<{len(self.indices)}H", *self.indices)
        return struct.pack(f"<{len(self.indices)}I", *self.indices)

    def bone_indices_as_bytes(self) -> bytes:
        """Pack bone indices as uint8 (4 per vertex)."""
        if not self.bone_indices:
            return b""
        return struct.pack(f"<{len(self.bone_indices)}B", *self.bone_indices)

    def bone_weights_as_bytes(self) -> bytes:
        """Pack bone weights as little-endian float32 (4 per vertex)."""
        if not self.bone_weights:
            return b""
        return struct.pack(f"<{len(self.bone_weights)}f", *self.bone_weights)
