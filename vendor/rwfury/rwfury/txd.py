"""TXD (Texture Dictionary) parser and DDS exporter for GTA RenderWare."""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .rwbinary import (
    RwBinaryReader,
    RW_STRUCT,
    RW_EXTENSION,
    RW_TEXTURE_NATIVE,
    RW_TEXTURE_DICTIONARY,
)

# D3D format constants
D3DFMT_A8R8G8B8 = 21
D3DFMT_X8R8G8B8 = 22
D3DFMT_R5G6B5 = 23
D3DFMT_X1R5G5B5 = 24
D3DFMT_A1R5G5B5 = 25
D3DFMT_A4R4G4B4 = 26
D3DFMT_P8 = 41
D3DFMT_DXT1 = 0x31545844  # 'DXT1' as LE u32
D3DFMT_DXT2 = 0x32545844
D3DFMT_DXT3 = 0x33545844
D3DFMT_DXT4 = 0x34545844
D3DFMT_DXT5 = 0x35545844

# Raster format flags (GTA SA)
RASTER_DEFAULT = 0x0000
RASTER_1555 = 0x0100
RASTER_565 = 0x0200
RASTER_4444 = 0x0300
RASTER_LUM8 = 0x0400
RASTER_8888 = 0x0500
RASTER_888 = 0x0600
RASTER_16 = 0x0700
RASTER_24 = 0x0800
RASTER_32 = 0x0900
RASTER_555 = 0x0A00

RASTER_FORMAT_MASK = 0x0F00
RASTER_HAS_ALPHA = 0x8000  # SA-specific flag not always reliable
RASTER_AUTOMIPMAP = 0x1000
RASTER_PAL8 = 0x2000
RASTER_PAL4 = 0x4000
RASTER_MIPMAP = 0x8000

# DDS header constants
DDS_MAGIC = b"DDS "
DDSD_CAPS = 0x01
DDSD_HEIGHT = 0x02
DDSD_WIDTH = 0x04
DDSD_PITCH = 0x08
DDSD_PIXELFORMAT = 0x1000
DDSD_MIPMAPCOUNT = 0x20000
DDSD_LINEARSIZE = 0x80000

DDPF_ALPHAPIXELS = 0x01
DDPF_FOURCC = 0x04
DDPF_RGB = 0x40

DDSCAPS_COMPLEX = 0x08
DDSCAPS_TEXTURE = 0x1000
DDSCAPS_MIPMAP = 0x400000


@dataclass
class TxdTexture:
    name: str = ""
    alpha_name: str = ""
    width: int = 0
    height: int = 0
    depth: int = 0  # bits per pixel
    raster_format: int = 0
    d3d_format: int = 0
    has_alpha: bool = False
    mipmap_count: int = 1
    mipmaps: list[bytes] = field(default_factory=list)
    palette: bytes = b""  # 256 * 4 bytes for PAL8

    @property
    def is_compressed(self) -> bool:
        return self.d3d_format in (
            D3DFMT_DXT1, D3DFMT_DXT2, D3DFMT_DXT3, D3DFMT_DXT4, D3DFMT_DXT5
        )

    @property
    def compression_name(self) -> str:
        mapping = {
            D3DFMT_DXT1: "DXT1",
            D3DFMT_DXT2: "DXT2",
            D3DFMT_DXT3: "DXT3",
            D3DFMT_DXT4: "DXT4",
            D3DFMT_DXT5: "DXT5",
        }
        return mapping.get(self.d3d_format, "none")

    def to_rgba(self) -> tuple[list[bytes], bool]:
        """Decode this texture to raw RGBA bytes (R,G,B,A byte order).

        Handles palettized, uncompressed, and DXT-compressed textures.

        Returns:
            (mipmaps, has_alpha) where mipmaps is a list of bytes objects
            (one per mip level, each width*height*4 bytes in RGBA order),
            and has_alpha indicates if any pixel has alpha < 255.
        """
        return _tex_to_rgba(self)


class Txd:
    """TXD (Texture Dictionary) parser and DDS exporter."""

    def __init__(self):
        self.textures: list[TxdTexture] = []

    @classmethod
    def from_file(cls, path: str) -> Txd:
        txd = cls()
        with RwBinaryReader.from_file(path) as reader:
            txd._parse(reader)
        return txd

    @classmethod
    def from_bytes(cls, data: bytes) -> Txd:
        """Parse a TXD from raw bytes (e.g. read from an IMG archive)."""
        import io
        txd = cls()
        reader = RwBinaryReader(io.BytesIO(data))
        txd._parse(reader)
        return txd

    def _parse(self, reader: RwBinaryReader):
        header = reader.read_chunk_header()
        if header.id != RW_TEXTURE_DICTIONARY:
            raise ValueError(
                f"Not a TXD file: expected chunk 0x{RW_TEXTURE_DICTIONARY:04X}, "
                f"got 0x{header.id:04X}"
            )

        # Read struct with texture count
        struct_header = reader.read_chunk_header()
        assert struct_header.id == RW_STRUCT
        texture_count = reader.read_u16()
        _device_id = reader.read_u16()  # usually 1 (D3D8) or 2 (D3D9)

        for _ in range(texture_count):
            tex = self._parse_texture_native(reader)
            self.textures.append(tex)

    def _parse_texture_native(self, reader: RwBinaryReader) -> TxdTexture:
        tex_header = reader.read_chunk_header()
        if tex_header.id != RW_TEXTURE_NATIVE:
            raise ValueError(
                f"Expected Texture Native (0x{RW_TEXTURE_NATIVE:04X}), "
                f"got 0x{tex_header.id:04X}"
            )
        tex_end = reader.tell() + tex_header.size

        # Struct header
        struct_header = reader.read_chunk_header()
        assert struct_header.id == RW_STRUCT

        tex = TxdTexture()

        # Platform ID (9 = D3D8/D3D9)
        platform_id = reader.read_u32()
        if platform_id not in (8, 9):
            raise ValueError(f"Unsupported platform ID: {platform_id} (expected D3D8/9)")

        # Filter mode + addressing
        filter_flags = reader.read_u32()

        # Texture name (32 bytes)
        tex.name = reader.read_string(32)
        # Alpha/mask name (32 bytes)
        tex.alpha_name = reader.read_string(32)

        # Raster format, D3D format, dimensions
        tex.raster_format = reader.read_u32()
        tex.d3d_format = reader.read_u32()
        tex.width = reader.read_u16()
        tex.height = reader.read_u16()
        tex.depth = reader.read_u8()
        tex.mipmap_count = reader.read_u8()
        _raster_type = reader.read_u8()  # usually 4
        alpha_or_compression = reader.read_u8()

        tex.has_alpha = bool(alpha_or_compression & 0x01) if not tex.is_compressed else bool(alpha_or_compression)

        # Infer D3D format from raster format when not explicitly set (GTA III/VC)
        if tex.d3d_format == 0 or tex.d3d_format == 1:
            raster_fmt = tex.raster_format & RASTER_FORMAT_MASK
            if raster_fmt == RASTER_8888:
                tex.d3d_format = D3DFMT_A8R8G8B8
            elif raster_fmt == RASTER_888:
                tex.d3d_format = D3DFMT_X8R8G8B8
            elif raster_fmt == RASTER_565:
                tex.d3d_format = D3DFMT_R5G6B5
            elif raster_fmt == RASTER_1555:
                tex.d3d_format = D3DFMT_A1R5G5B5
            elif raster_fmt == RASTER_4444:
                tex.d3d_format = D3DFMT_A4R4G4B4
            elif raster_fmt == RASTER_555:
                tex.d3d_format = D3DFMT_X1R5G5B5
            elif tex.depth == 32:
                tex.d3d_format = D3DFMT_A8R8G8B8
            elif tex.depth == 16:
                tex.d3d_format = D3DFMT_A1R5G5B5 if tex.has_alpha else D3DFMT_R5G6B5
            elif tex.depth == 8:
                tex.d3d_format = D3DFMT_P8

        # Palette (for PAL8 raster format)
        is_pal8 = bool(tex.raster_format & RASTER_PAL8)
        is_pal4 = bool(tex.raster_format & RASTER_PAL4)

        if is_pal8:
            tex.palette = reader.read_bytes(256 * 4)
        elif is_pal4:
            tex.palette = reader.read_bytes(16 * 4)

        # Read mipmap data
        for i in range(tex.mipmap_count):
            data_size = reader.read_u32()
            mip_data = reader.read_bytes(data_size)
            tex.mipmaps.append(mip_data)

        # Skip to end of texture native chunk (extensions etc.)
        remaining = tex_end - reader.tell()
        if remaining > 0:
            reader.skip(remaining)

        return tex

    def export_to_dds(self, output_folder: str) -> list[str]:
        """Export each texture as a .dds file. Returns list of written file paths."""
        os.makedirs(output_folder, exist_ok=True)
        written = []

        for tex in self.textures:
            filename = tex.name + ".dds"
            filepath = os.path.join(output_folder, filename)

            dds_data = _build_dds(tex)
            with open(filepath, "wb") as f:
                f.write(dds_data)

            written.append(filepath)

        return written


def _tex_to_rgba(tex: TxdTexture) -> tuple[list[bytes], bool]:
    """Decode any TxdTexture mipmap data to RGBA (R,G,B,A byte order).

    Returns (list_of_mipmap_rgba_bytes, has_meaningful_alpha).
    """
    is_pal8 = bool(tex.raster_format & RASTER_PAL8)
    is_pal4 = bool(tex.raster_format & RASTER_PAL4)

    if is_pal8 or is_pal4:
        pal_count = 256 if is_pal8 else 16
        palette = []
        for i in range(pal_count):
            palette.append((
                tex.palette[i * 4],
                tex.palette[i * 4 + 1],
                tex.palette[i * 4 + 2],
                tex.palette[i * 4 + 3],
            ))
        mipmaps = []
        w, h = tex.width, tex.height
        has_alpha = False
        for mip_data in tex.mipmaps:
            pixels = bytearray(w * h * 4)
            for j in range(min(len(mip_data), w * h)):
                idx = mip_data[j]
                r, g, b, a = palette[idx] if idx < pal_count else (0, 0, 0, 255)
                pixels[j * 4] = r
                pixels[j * 4 + 1] = g
                pixels[j * 4 + 2] = b
                pixels[j * 4 + 3] = a
                if a < 255:
                    has_alpha = True
            mipmaps.append(bytes(pixels))
            w = max(1, w // 2)
            h = max(1, h // 2)
        return mipmaps, has_alpha

    if tex.is_compressed:
        # Decompress DXT to RGBA
        mipmaps = []
        w, h = tex.width, tex.height
        has_alpha = tex.d3d_format != D3DFMT_DXT1
        for mip_data in tex.mipmaps:
            mipmaps.append(_decompress_dxt(mip_data, w, h, tex.d3d_format))
            w = max(1, w // 2)
            h = max(1, h // 2)
        return mipmaps, has_alpha

    # Uncompressed: decode based on d3d_format
    mipmaps = []
    w, h = tex.width, tex.height
    has_alpha = False
    for mip_data in tex.mipmaps:
        pixels = bytearray(w * h * 4)
        bpp = tex.depth
        for j in range(w * h):
            if bpp == 32:
                # BGRA in memory -> RGBA
                off = j * 4
                if off + 3 < len(mip_data):
                    pixels[j * 4] = mip_data[off + 2]      # R
                    pixels[j * 4 + 1] = mip_data[off + 1]  # G
                    pixels[j * 4 + 2] = mip_data[off]      # B
                    pixels[j * 4 + 3] = mip_data[off + 3]  # A
                    if mip_data[off + 3] < 255:
                        has_alpha = True
            elif bpp == 16:
                off = j * 2
                if off + 1 < len(mip_data):
                    val = mip_data[off] | (mip_data[off + 1] << 8)
                    r, g, b, a = _decode_16bit_pixel(val, tex.d3d_format)
                    pixels[j * 4] = r
                    pixels[j * 4 + 1] = g
                    pixels[j * 4 + 2] = b
                    pixels[j * 4 + 3] = a
                    if a < 255:
                        has_alpha = True
        mipmaps.append(bytes(pixels))
        w = max(1, w // 2)
        h = max(1, h // 2)
    return mipmaps, has_alpha


def _decode_16bit_pixel(val: int, d3d_format: int) -> tuple[int, int, int, int]:
    if d3d_format == D3DFMT_R5G6B5:
        r = ((val >> 11) & 0x1F) * 255 // 31
        g = ((val >> 5) & 0x3F) * 255 // 63
        b = (val & 0x1F) * 255 // 31
        return (r, g, b, 255)
    elif d3d_format == D3DFMT_A1R5G5B5:
        a = 255 if (val >> 15) else 0
        r = ((val >> 10) & 0x1F) * 255 // 31
        g = ((val >> 5) & 0x1F) * 255 // 31
        b = (val & 0x1F) * 255 // 31
        return (r, g, b, a)
    elif d3d_format == D3DFMT_A4R4G4B4:
        a = ((val >> 12) & 0xF) * 255 // 15
        r = ((val >> 8) & 0xF) * 255 // 15
        g = ((val >> 4) & 0xF) * 255 // 15
        b = (val & 0xF) * 255 // 15
        return (r, g, b, a)
    elif d3d_format == D3DFMT_X1R5G5B5:
        r = ((val >> 10) & 0x1F) * 255 // 31
        g = ((val >> 5) & 0x1F) * 255 // 31
        b = (val & 0x1F) * 255 // 31
        return (r, g, b, 255)
    return (0, 0, 0, 255)


def _decompress_dxt(data: bytes, w: int, h: int, fmt: int) -> bytes:
    """Decompress a DXT mipmap to RGBA bytes."""
    pixels = bytearray(w * h * 4)
    bw = max(1, (w + 3) // 4)
    bh = max(1, (h + 3) // 4)
    block_size = 8 if fmt == D3DFMT_DXT1 else 16
    offset = 0

    for by in range(bh):
        for bx in range(bw):
            if offset + block_size > len(data):
                break

            if fmt == D3DFMT_DXT1:
                _decode_dxt1_block(data, offset, pixels, bx, by, w, h)
            elif fmt in (D3DFMT_DXT3, D3DFMT_DXT2):
                _decode_dxt3_block(data, offset, pixels, bx, by, w, h)
            elif fmt in (D3DFMT_DXT5, D3DFMT_DXT4):
                _decode_dxt5_block(data, offset, pixels, bx, by, w, h)

            offset += block_size

    return bytes(pixels)


def _unpack_rgb565(c: int) -> tuple[int, int, int]:
    r = ((c >> 11) & 0x1F) * 255 // 31
    g = ((c >> 5) & 0x3F) * 255 // 63
    b = (c & 0x1F) * 255 // 31
    return (r, g, b)


def _decode_dxt1_block(data: bytes, off: int, pixels: bytearray,
                        bx: int, by: int, w: int, h: int):
    c0 = data[off] | (data[off + 1] << 8)
    c1 = data[off + 2] | (data[off + 3] << 8)
    r0, g0, b0 = _unpack_rgb565(c0)
    r1, g1, b1 = _unpack_rgb565(c1)

    colors = [(r0, g0, b0, 255), (r1, g1, b1, 255)]
    if c0 > c1:
        colors.append(((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255))
        colors.append(((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255))
    else:
        colors.append(((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255))
        colors.append((0, 0, 0, 0))

    bits = data[off + 4] | (data[off + 5] << 8) | (data[off + 6] << 16) | (data[off + 7] << 24)
    for py in range(4):
        for px in range(4):
            x, y = bx * 4 + px, by * 4 + py
            if x < w and y < h:
                idx = (bits >> ((py * 4 + px) * 2)) & 0x03
                r, g, b, a = colors[idx]
                p = (y * w + x) * 4
                pixels[p] = r
                pixels[p + 1] = g
                pixels[p + 2] = b
                pixels[p + 3] = a


def _decode_dxt3_block(data: bytes, off: int, pixels: bytearray,
                        bx: int, by: int, w: int, h: int):
    # 8 bytes of explicit alpha, then DXT1 color block
    alpha_bits = data[off:off + 8]
    _decode_dxt1_block(data, off + 8, pixels, bx, by, w, h)
    # Overwrite alpha
    for py in range(4):
        for px in range(4):
            x, y = bx * 4 + px, by * 4 + py
            if x < w and y < h:
                ai = py * 4 + px
                byte_idx = ai // 2
                if ai & 1:
                    a = (alpha_bits[byte_idx] >> 4) & 0xF
                else:
                    a = alpha_bits[byte_idx] & 0xF
                p = (y * w + x) * 4
                pixels[p + 3] = a * 255 // 15


def _decode_dxt5_block(data: bytes, off: int, pixels: bytearray,
                        bx: int, by: int, w: int, h: int):
    a0 = data[off]
    a1 = data[off + 1]
    # 6 bytes = 48 bits of 3-bit alpha indices
    abits = 0
    for i in range(6):
        abits |= data[off + 2 + i] << (i * 8)

    if a0 > a1:
        alphas = [a0, a1,
                  (6 * a0 + 1 * a1) // 7, (5 * a0 + 2 * a1) // 7,
                  (4 * a0 + 3 * a1) // 7, (3 * a0 + 4 * a1) // 7,
                  (2 * a0 + 5 * a1) // 7, (1 * a0 + 6 * a1) // 7]
    else:
        alphas = [a0, a1,
                  (4 * a0 + 1 * a1) // 5, (3 * a0 + 2 * a1) // 5,
                  (2 * a0 + 3 * a1) // 5, (1 * a0 + 4 * a1) // 5,
                  0, 255]

    _decode_dxt1_block(data, off + 8, pixels, bx, by, w, h)
    for py in range(4):
        for px in range(4):
            x, y = bx * 4 + px, by * 4 + py
            if x < w and y < h:
                ai = py * 4 + px
                a_idx = (abits >> (ai * 3)) & 0x07
                p = (y * w + x) * 4
                pixels[p + 3] = alphas[a_idx]


# ---------------------------------------------------------------------------
# DDS building
# ---------------------------------------------------------------------------

def _build_dds(tex: TxdTexture) -> bytes:
    """Build a complete DDS file from a TxdTexture."""
    is_pal8 = bool(tex.raster_format & RASTER_PAL8)
    is_pal4 = bool(tex.raster_format & RASTER_PAL4)

    if is_pal8 or is_pal4:
        return _build_dds_from_palettized(tex)

    header = _build_dds_header(tex)
    return b"".join([DDS_MAGIC, header] + list(tex.mipmaps))


def _build_dds_from_palettized(tex: TxdTexture) -> bytes:
    """Convert palettized texture to RGBA DDS."""
    palette_colors = []
    pal_count = 256 if (tex.raster_format & RASTER_PAL8) else 16
    for i in range(pal_count):
        r = tex.palette[i * 4]
        g = tex.palette[i * 4 + 1]
        b = tex.palette[i * 4 + 2]
        a = tex.palette[i * 4 + 3]
        palette_colors.append((r, g, b, a))

    converted_mipmaps = []
    w, h = tex.width, tex.height
    for mip_data in tex.mipmaps:
        pixels = bytearray(w * h * 4)
        for j in range(min(len(mip_data), w * h)):
            idx = mip_data[j]
            if idx < pal_count:
                r, g, b, a = palette_colors[idx]
            else:
                r, g, b, a = 0, 0, 0, 255
            # DDS expects BGRA for A8R8G8B8 (D3D ARGB is stored as BGRA in memory)
            pixels[j * 4] = b
            pixels[j * 4 + 1] = g
            pixels[j * 4 + 2] = r
            pixels[j * 4 + 3] = a
        converted_mipmaps.append(bytes(pixels))
        w = max(1, w // 2)
        h = max(1, h // 2)

    # Build DDS with uncompressed A8R8G8B8
    fake_tex = TxdTexture(
        name=tex.name,
        width=tex.width,
        height=tex.height,
        depth=32,
        d3d_format=D3DFMT_A8R8G8B8,
        has_alpha=True,
        mipmap_count=len(converted_mipmaps),
        mipmaps=converted_mipmaps,
    )
    header = _build_dds_header(fake_tex)
    parts = [DDS_MAGIC, header]
    for mip in converted_mipmaps:
        parts.append(mip)
    return b"".join(parts)


def _build_dds_header(tex: TxdTexture) -> bytes:
    """Build a 124-byte DDS header (without the magic)."""
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT
    caps = DDSCAPS_TEXTURE

    if tex.mipmap_count > 1:
        flags |= DDSD_MIPMAPCOUNT
        caps |= DDSCAPS_COMPLEX | DDSCAPS_MIPMAP

    # Pixel format
    if tex.is_compressed:
        flags |= DDSD_LINEARSIZE
        pf_flags = DDPF_FOURCC
        fourcc = tex.d3d_format
        pf_rgb_bits = 0
        pf_r_mask = 0
        pf_g_mask = 0
        pf_b_mask = 0
        pf_a_mask = 0

        # Compute linear size for first mip
        block_size = 8 if tex.d3d_format == D3DFMT_DXT1 else 16
        pitch_or_linear = max(1, (tex.width + 3) // 4) * max(1, (tex.height + 3) // 4) * block_size
    else:
        flags |= DDSD_PITCH
        pf_flags = DDPF_RGB
        fourcc = 0

        if tex.d3d_format in (D3DFMT_A8R8G8B8, D3DFMT_X8R8G8B8):
            pf_rgb_bits = 32
            pf_r_mask = 0x00FF0000
            pf_g_mask = 0x0000FF00
            pf_b_mask = 0x000000FF
            pf_a_mask = 0xFF000000 if tex.d3d_format == D3DFMT_A8R8G8B8 else 0
        elif tex.d3d_format == D3DFMT_R5G6B5:
            pf_rgb_bits = 16
            pf_r_mask = 0xF800
            pf_g_mask = 0x07E0
            pf_b_mask = 0x001F
            pf_a_mask = 0
        elif tex.d3d_format == D3DFMT_A1R5G5B5:
            pf_rgb_bits = 16
            pf_r_mask = 0x7C00
            pf_g_mask = 0x03E0
            pf_b_mask = 0x001F
            pf_a_mask = 0x8000
        elif tex.d3d_format == D3DFMT_A4R4G4B4:
            pf_rgb_bits = 16
            pf_r_mask = 0x0F00
            pf_g_mask = 0x00F0
            pf_b_mask = 0x000F
            pf_a_mask = 0xF000
        elif tex.d3d_format == D3DFMT_X1R5G5B5:
            pf_rgb_bits = 16
            pf_r_mask = 0x7C00
            pf_g_mask = 0x03E0
            pf_b_mask = 0x001F
            pf_a_mask = 0
        else:
            # Fallback to 32-bit ARGB
            pf_rgb_bits = 32
            pf_r_mask = 0x00FF0000
            pf_g_mask = 0x0000FF00
            pf_b_mask = 0x000000FF
            pf_a_mask = 0xFF000000

        if pf_a_mask:
            pf_flags |= DDPF_ALPHAPIXELS

        pitch_or_linear = (tex.width * pf_rgb_bits + 7) // 8

    # Pack header: 124 bytes
    # struct DDS_HEADER {
    #   DWORD size (124), flags, height, width, pitchOrLinearSize,
    #   depth (0), mipMapCount, reserved1[11],
    #   DDS_PIXELFORMAT { size (32), flags, fourCC, rgbBitCount, masks[4] },
    #   caps, caps2, caps3, caps4, reserved2
    # }
    header = struct.pack(
        "<7I 11I 8I 4I I",
        124,                   # size
        flags,                 # flags
        tex.height,            # height
        tex.width,             # width
        pitch_or_linear,       # pitchOrLinearSize
        0,                     # depth (volume textures)
        tex.mipmap_count,      # mipMapCount
        # reserved1[11]
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        # DDS_PIXELFORMAT
        32,                    # size
        pf_flags,              # flags
        fourcc,                # fourCC
        pf_rgb_bits,           # rgbBitCount
        pf_r_mask,             # rBitMask
        pf_g_mask,             # gBitMask
        pf_b_mask,             # bBitMask
        pf_a_mask,             # aBitMask
        # caps
        caps, 0, 0, 0,
        # reserved2
        0,
    )
    return header
