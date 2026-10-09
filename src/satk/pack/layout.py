"""Byte layout of the ``.saepak`` container and of the DAC file: the single source of every record table.

The tables here are the documented layout. ``docs/en/saepak.md`` and ``docs/en/dac.md`` print the same tables and
``tests/pack/test_docs.py`` compares them with this module, so a layout change cannot leave the docs behind.

All integers are little-endian. A :class:`Record` is a fixed-size struct made from a field table; field names that
start with ``reserved`` must be written as zero and are checked by the verifier (a reader of the same major version
ignores them, a later minor version may give them a meaning).

Stdlib only.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

__all__ = [
    "SECTOR", "MAX_ENTRY_SECTORS", "MAX_ENTRY_BYTES", "MAX_PACK_BYTES", "FORMAT_MAJOR", "FORMAT_MINOR", "DEFAULT_CHUNK", "MIN_CHUNK",
    "MAX_CHUNK", "FAST_NONE", "FAST_XXH3_128", "MOUNT_MODES", "KINDS", "KIND_BY_EXT", "STREAM_EXTS", "RES_DEDUP_OK",
    "RES_LOCAL_ONLY", "NAME_DERIVED", "Field", "Record", "HEADER", "FOOTER", "ENTRY", "NAME", "CHUNK", "FOOTER_MAGIC",
    "MANIFEST_MAGIC", "IMG_MAGIC", "IMG_ENTRY", "STREAM_NAME_MAX", "FOOTER_CRC_SPAN",
]

SECTOR = 2048
#: IMG VER2 stores the entry size in a 16-bit sector count: 65535 sectors, just under 128 MiB.
MAX_ENTRY_SECTORS = 0xFFFF
MAX_ENTRY_BYTES = MAX_ENTRY_SECTORS * SECTOR
#: A pack is mounted as one IMG: the engine reads 32-bit byte positions, so a whole pack stays under 4 GiB.
MAX_PACK_BYTES = 4 * 1024 * 1024 * 1024 - SECTOR
FORMAT_MAJOR = 1
FORMAT_MINOR = 0
#: Chunk size of the per-chunk hash table (bytes). A multiple of the sector size and a power of two.
DEFAULT_CHUNK = 64 * 1024
MIN_CHUNK = SECTOR
MAX_CHUNK = 64 * 1024 * 1024

FAST_NONE = 0
FAST_XXH3_128 = 1

FOOTER_MAGIC = b"SAEPAKFT"
MANIFEST_MAGIC = b"SAEMANIF"
IMG_MAGIC = b"VER2"
#: Longest IMG directory name (the field has 24 bytes; the engine copies it as a C string).
STREAM_NAME_MAX = 23

#: Mount mode of the pack (header field ``mount_mode``).
MOUNT_MODES = {"overlay": 0, "addon": 1, "cache": 2}
#: Entry kinds (entry field ``kind``).
KINDS = {0: "other", 1: "dff", 2: "txd", 3: "col", 4: "ifp", 5: "ipl", 6: "dat"}
KIND_BY_EXT = {v: k for k, v in KINDS.items() if k}
#: Extensions the streamer reads from an IMG.
STREAM_EXTS = tuple(sorted(KIND_BY_EXT))

#: Header ``residency_flags`` bits.
RES_DEDUP_OK = 1       # the residency layer may share identical textures of this pack's TXDs
RES_LOCAL_ONLY = 2     # derived or private content: never redistribute the pack
#: Name record ``flags`` bit: the IMG stream name was derived from the content hash (the logical name does not fit).
NAME_DERIVED = 1


@dataclass(frozen=True)
class Field:
    name: str
    code: str          # struct code, e.g. "I", "Q", "32s"
    desc: str
    offset: int = 0

    @property
    def size(self) -> int:
        return struct.calcsize("<" + self.code)

    @property
    def reserved(self) -> bool:
        return self.name.startswith("reserved")

    @property
    def type_label(self) -> str:
        c = self.code
        names = {"B": "u8", "H": "u16", "I": "u32", "Q": "u64", "b": "i8", "h": "i16", "i": "i32", "q": "i64",
                 "f": "f32", "d": "f64"}
        if c in names:
            return names[c]
        if c.endswith("s"):
            return f"u8[{c[:-1]}]"
        raise ValueError(c)


class Record:
    """A fixed-size little-endian record made from a field table (offsets are computed and checked)."""

    def __init__(self, name: str, size: int, fields: list[tuple[str, str, str]]):
        self.name = name
        self.size = size
        total = sum(1 for n, _c, _d in fields if n == "reserved")
        seen = 0
        off = 0
        out: list[Field] = []
        for n, code, desc in fields:
            if n == "reserved" and total > 1:
                seen += 1
                n = f"reserved{seen}"
            f = Field(n, code, desc, off)
            out.append(f)
            off += f.size
        if off != size:
            raise ValueError(f"record {name}: fields add up to {off} bytes, expected {size}")
        if len({f.name for f in out}) != len(out):
            raise ValueError(f"record {name}: duplicate field names")
        self.fields = tuple(out)
        self.struct = struct.Struct("<" + "".join(f.code for f in out))
        self.by_name = {f.name: f for f in out}

    def pack(self, **values) -> bytes:
        args = []
        for f in self.fields:
            v = values.pop(f.name, b"" if f.code.endswith("s") else 0)
            if f.code.endswith("s"):
                v = bytes(v).ljust(f.size, b"\0")
            args.append(v)
        if values:
            raise TypeError(f"{self.name}: unknown field(s) {sorted(values)}")
        return self.struct.pack(*args)

    def unpack(self, buf, offset: int = 0) -> dict:
        vals = self.struct.unpack_from(buf, offset)
        return {f.name: v for f, v in zip(self.fields, vals)}

    def reserved_nonzero(self, rec: dict) -> list[str]:
        """Names of reserved fields of an unpacked record that are not zero."""
        bad = []
        for f in self.fields:
            if f.reserved:
                v = rec[f.name]
                if (any(v) if isinstance(v, (bytes, bytearray)) else v != 0):
                    bad.append(f.name)
        return bad

    def rows(self) -> list[tuple[int, str, str, str]]:
        """``(offset, type, name, description)`` of every field, in order."""
        return [(f.offset, f.type_label, f.name, f.desc) for f in self.fields]


# --------------------------------------------------------------------------- IMG VER2 directory (file start)

IMG_ENTRY = Record("img_entry", 32, [
    ("offset_sectors", "I", "payload offset in 2048-byte sectors"),
    ("stream_sectors", "H", "payload size in sectors (the engine streaming size)"),
    ("archive_sectors", "H", "always 0; the engine then uses stream_sectors"),
    ("name", "24s", "stream name, NUL padded, at most 23 characters"),
])

# --------------------------------------------------------------------------- footer (last 128 bytes of the file)

FOOTER = Record("footer", 128, [
    ("magic", "8s", "ASCII SAEPAKFT"),
    ("major", "H", "format major version (1); a reader refuses a larger one"),
    ("minor", "H", "format minor version (0); additions only"),
    ("flags", "I", "0"),
    ("manifest_offset", "Q", "absolute offset of the manifest block in the full pack (sector aligned)"),
    ("manifest_size", "Q", "size of the manifest block in bytes (a multiple of 8)"),
    ("pack_size", "Q", "size of the full pack file; a manifest-only file is manifest_size + 128 bytes"),
    ("manifest_sha256", "32s", "SHA-256 of the manifest block"),
    ("manifest_fast", "16s", "fast hash of the manifest block (header fast_algo); zero when fast_algo is 0"),
    ("crc32", "I", "CRC-32 (zlib) of footer bytes 0..87"),
    ("reserved", "36s", "zero"),
])
FOOTER_CRC_SPAN = 88

# --------------------------------------------------------------------------- manifest block

HEADER = Record("header", 192, [
    ("magic", "8s", "ASCII SAEMANIF"),
    ("major", "H", "format major version (1)"),
    ("minor", "H", "format minor version (0)"),
    ("header_size", "I", "192; a later minor version may enlarge the header, tables start at the offsets below"),
    ("flags", "I", "bit 0: the file starts with an IMG VER2 directory (always set in 1.0)"),
    ("sector_size", "I", "2048"),
    ("chunk_size", "I", "bytes per hash chunk: a power of two from 2048 to 64 MiB (default 65536)"),
    ("fast_algo", "I", "0 = fast hash fields are zero, 1 = XXH3-128 canonical (big-endian) digest"),
    ("entry_count", "I", "records in the entry table (distinct contents)"),
    ("name_count", "I", "records in the name table (logical names)"),
    ("dir_count", "I", "records in the IMG directory at the file start (distinct stream names)"),
    ("chunk_count", "I", "records in the chunk table"),
    ("entries_off", "Q", "offset of the entry table, relative to the manifest start"),
    ("names_off", "Q", "offset of the name table, relative to the manifest start"),
    ("chunks_off", "Q", "offset of the chunk table, relative to the manifest start"),
    ("strings_off", "Q", "offset of the string area, relative to the manifest start"),
    ("strings_size", "Q", "size of the string area in bytes"),
    ("data_offset", "Q", "absolute offset of the first payload byte (end of the IMG directory sectors)"),
    ("data_size", "Q", "bytes of the payload region (a multiple of 2048)"),
    ("pack_id", "32s", "SHA-256 identity of namespace and logical-name -> content mapping (see the spec)"),
    ("priority", "i", "mount priority: larger wins over smaller within the same mount mode"),
    ("mount_mode", "H", "0 overlay (shadows same-named entries of lower layers), 1 addon (new names only), "
                        "2 cache (content-addressed cache image)"),
    ("residency_flags", "H", "bit 0: texture dedup allowed; bit 1: local only, never redistribute"),
    ("namespace_off", "I", "string offset of the namespace of the logical names"),
    ("namespace_len", "I", "string length in bytes"),
    ("title_off", "I", "string offset of a human title (may be empty)"),
    ("title_len", "I", "string length in bytes"),
    ("mount_name_off", "I", "string offset of the suggested IMG file name when mounted (may be empty)"),
    ("mount_name_len", "I", "string length in bytes"),
    ("reserved", "Q", "zero"),
    ("reserved", "Q", "zero"),
    ("reserved", "Q", "zero"),
])

ENTRY = Record("entry", 96, [
    ("sha256", "32s", "SHA-256 of the content (the content address)"),
    ("fast", "16s", "fast hash of the content (header fast_algo); zero when fast_algo is 0"),
    ("offset", "Q", "absolute payload offset (sector aligned)"),
    ("size", "Q", "content size in bytes (1 .. 65535 sectors)"),
    ("first_chunk", "I", "index of the first record in the chunk table"),
    ("chunk_count", "I", "number of chunk records: ceil(size / chunk_size)"),
    ("kind", "B", "0 other, 1 dff, 2 txd, 3 col, 4 ifp, 5 ipl, 6 dat"),
    ("flags", "B", "0"),
    ("reserved", "H", "zero"),
    ("stream_sectors", "I", "ceil(size / 2048)"),
    ("rw_version", "I", "RenderWare version of the first chunk (e.g. 0x36003), 0 when the content is not RW"),
    ("reserved", "I", "zero"),
    ("reserved", "Q", "zero"),
])

NAME = Record("name", 40, [
    ("entry_index", "I", "index in the entry table"),
    ("dir_index", "I", "index of the IMG directory slot that streams this name"),
    ("logical_off", "I", "string offset of the logical name"),
    ("logical_len", "I", "string length in bytes"),
    ("asset_id", "Q", "reserved for the registry's 64-bit AssetId; 0 in 1.0"),
    ("flags", "I", "bit 0: the stream name was derived from the content hash"),
    ("reserved", "I", "zero"),
    ("reserved", "Q", "zero"),
])

CHUNK = Record("chunk", 48, [
    ("sha256", "32s", "SHA-256 of the chunk bytes (the last chunk of an entry is shorter)"),
    ("fast", "16s", "fast hash of the chunk (header fast_algo); zero when fast_algo is 0"),
])

# --------------------------------------------------------------------------- DAC (derived-asset cache) file

DAC_MAGIC = b"SAEDACFL"
DAC_MAJOR = 1
DAC_MINOR = 0
DAC_ALIGN = 16

#: Section kinds.
DAC_GEOM_STREAM = 1      # per-vertex data of one geometry of a model (the render track's vertex streams)
DAC_LIGHT_LIST = 2       # the lights of one geometry (2dEffect type 0), a flat array of LIGHT_V1 records
DAC_MIP_CHAIN = 3        # derived mip levels of one texture
DAC_META = 4             # UTF-8 JSON of the derivation parameters (informational; the cache key covers them)
DAC_KINDS = {DAC_GEOM_STREAM: "geom_stream", DAC_LIGHT_LIST: "light_list", DAC_MIP_CHAIN: "mip_chain",
             DAC_META: "meta"}

#: Geometry stream types (section field ``stream`` when kind is 1).
STREAM_NORMAL = 1
STREAM_NIGHT_COLOR = 2
STREAM_TANGENT = 3
STREAMS = {STREAM_NORMAL: "normal", STREAM_NIGHT_COLOR: "night_color", STREAM_TANGENT: "tangent"}

#: Element formats (section field ``format``).
FMT_F32X3 = 1       # 3 x f32, stride 12
FMT_F32X4 = 2       # 4 x f32, stride 16
FMT_OCT16X2 = 3     # unit vector, octahedral map, 2 x snorm16 (D3DDECLTYPE_SHORT2N), stride 4
FMT_RGBA8 = 4       # r, g, b, a bytes in the order the RW vertex colour stream stores them, stride 4
FMT_SNORM8X4 = 5    # x, y, z in snorm8 and w = +-127 (tangent: w is the bitangent sign), stride 4
FMT_LIGHT_V1 = 6    # 32-byte light record, see LIGHT_V1_FIELDS
FMT_BYTES = 7       # opaque bytes (mip levels), stride 0
FMT_JSON = 8        # UTF-8 JSON, stride 0
FORMATS = {FMT_F32X3: ("f32x3", 12), FMT_F32X4: ("f32x4", 16), FMT_OCT16X2: ("oct16x2", 4), FMT_RGBA8: ("rgba8", 4),
           FMT_SNORM8X4: ("snorm8x4", 4), FMT_LIGHT_V1: ("light_v1", 32), FMT_BYTES: ("bytes", 0),
           FMT_JSON: ("json", 0)}

#: Section ``flags`` for geometry streams.
SF_COMPUTED = 1     # derived by satk from the geometry (not copied from a stream the source file carries)

#: Derivation recipe ids (header ``derive_id``); a recipe change gets a new id, which changes every cache key.
DERIVE_DFF_V1 = 1
#: Source kinds (header ``source_kind``).
SRC_DFF = 1
SRC_TXD = 2
SRC_OTHER = 0

DAC_HEADER = Record("dac_header", 128, [
    ("magic", "8s", "ASCII SAEDACFL"),
    ("major", "H", "format major version (1)"),
    ("minor", "H", "format minor version (0)"),
    ("header_size", "I", "128"),
    ("flags", "I", "0"),
    ("section_count", "I", "records in the section table"),
    ("derive_id", "I", "derivation recipe (1 = DFF streams, version 1)"),
    ("source_kind", "I", "1 dff, 2 txd, 0 other"),
    ("section_table_off", "Q", "absolute offset of the section table (128)"),
    ("file_size", "Q", "size of the whole file"),
    ("source_sha256", "32s", "SHA-256 of the source asset bytes this cache was derived from"),
    ("cache_key", "32s", "SHA-256 of DACKEY1, source_sha256, derive_id and the canonical parameter JSON"),
    ("crc32", "I", "CRC-32 (zlib) of header bytes 0..111"),
    ("reserved", "12s", "zero"),
])
DAC_HEADER_CRC_SPAN = 112

DAC_SECTION = Record("dac_section", 64, [
    ("kind", "H", "1 geom_stream, 2 light_list, 3 mip_chain, 4 meta"),
    ("stream", "H", "geometry stream type: 1 normal, 2 night_color, 3 tangent (0 for other kinds)"),
    ("index", "I", "geometry index in the source (stream order over clumps), or texture index"),
    ("count", "I", "elements: vertices, lights or mip levels (bytes for meta)"),
    ("format", "H", "element format, see the format table"),
    ("stride", "H", "bytes per element (0 for bytes and json)"),
    ("offset", "Q", "absolute payload offset (16-byte aligned)"),
    ("size", "Q", "payload size in bytes (count * stride for fixed strides)"),
    ("crc32", "I", "CRC-32 (zlib) of the payload"),
    ("flags", "I", "bit 0: computed by satk (not copied from the source file)"),
    ("aux0", "I", "mip_chain: D3D9 format (FOURCC or D3DFMT number); else 0"),
    ("aux1", "I", "mip_chain: width of the first stored level; else 0"),
    ("aux2", "I", "mip_chain: height of the first stored level; else 0"),
    ("aux3", "I", "mip_chain: index of the first stored level (0 = the full chain); else 0"),
    ("reserved", "Q", "zero"),
])

#: Fields of a LIGHT_V1 record (32 bytes, little-endian): ``<3f4B3fI``.
LIGHT_V1_FIELDS = (
    ("x, y, z", "3 x f32", "position in the geometry's frame, as in the 2dEffect entry"),
    ("r, g, b, a", "4 x u8", "light colour"),
    ("corona_far_clip", "f32", "distance beyond which the corona is not drawn"),
    ("point_range", "f32", "radius of the point light"),
    ("corona_size", "f32", "corona size"),
    ("flags", "u32", "the 2dEffect light flags: first flag byte | second flag byte << 8"),
)
