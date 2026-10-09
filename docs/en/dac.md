# The DAC file: the derived-asset cache, byte layout, format 1.0

[Русская версия](../ru/dac.md)

Package: `satk.pack` (`satk pack dac`, `verify`, `inspect`; see [pack.md](pack.md)). The tables on this page are printed
from the code (`satk.pack.layout`) and a test compares them.

## What it is

A modified renderer needs data the stock files do not carry or carry badly. Most map geometry is prelit and has no
vertex normals; the night colours live in an extension chunk the GPU cannot read as a vertex stream; tangents, the
light list of a model and texture mip chains are computed again and again. A **DAC file** stores such derived data for
one source asset, computed once.

- **Content addressed.** The file is named by its **cache key**: `SHA-256("DACKEY1" 0x0A, source SHA-256, u32 derive_id,
  canonical parameter JSON)`. A changed source, another recipe version or other options give another key, so a stale
  file is never found. The canonical JSON has sorted keys, no spaces and ASCII only.
- **Local only.** A DAC is derived from the user's own files. It is generated on their machine under
  `<workspace>/work/cache/dac/<hh>/<key>.dac` and is never redistributed or committed; a pack that carries DAC data
  sets the `local_only` flag.
- **Per-vertex data is indexed by the vertex index of the geometry in the source file.** A stream's `count` equals the
  geometry's vertex count, so a shader can bind it next to the geometry's own vertex buffer.

All integers are little-endian.

## File layout

```
0                         header (128 bytes)
128                       section table (64 bytes per section)
...                       section payloads, each 16-byte aligned, zero padded
```

The meta section (the derivation parameters as JSON) is always the last section.

### Header

| Offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | u8[8] | `magic` | ASCII SAEDACFL |
| 8 | u16 | `major` | format major version (1) |
| 10 | u16 | `minor` | format minor version (0) |
| 12 | u32 | `header_size` | 128 |
| 16 | u32 | `flags` | 0 |
| 20 | u32 | `section_count` | records in the section table |
| 24 | u32 | `derive_id` | derivation recipe (1 = DFF streams, version 1) |
| 28 | u32 | `source_kind` | 1 dff, 2 txd, 0 other |
| 32 | u64 | `section_table_off` | absolute offset of the section table (128) |
| 40 | u64 | `file_size` | size of the whole file |
| 48 | u8[32] | `source_sha256` | SHA-256 of the source asset bytes this cache was derived from |
| 80 | u8[32] | `cache_key` | SHA-256 of DACKEY1, source_sha256, derive_id and the canonical parameter JSON |
| 112 | u32 | `crc32` | CRC-32 (zlib) of header bytes 0..111 |
| 116 | u8[12] | `reserved` | zero |

A reader checks the magic, the header CRC, the major version, that `file_size` equals the real size, that the section
table fits, and, after reading the meta section, that `cache_key` equals the key recomputed from `source_sha256`,
`derive_id` and the parameters.

### Section table

| Offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | u16 | `kind` | 1 geom_stream, 2 light_list, 3 mip_chain, 4 meta |
| 2 | u16 | `stream` | geometry stream type: 1 normal, 2 night_color, 3 tangent (0 for other kinds) |
| 4 | u32 | `index` | geometry index in the source (stream order over clumps), or texture index |
| 8 | u32 | `count` | elements: vertices, lights or mip levels (bytes for meta) |
| 12 | u16 | `format` | element format, see the format table |
| 14 | u16 | `stride` | bytes per element (0 for bytes and json) |
| 16 | u64 | `offset` | absolute payload offset (16-byte aligned) |
| 24 | u64 | `size` | payload size in bytes (count * stride for fixed strides) |
| 32 | u32 | `crc32` | CRC-32 (zlib) of the payload |
| 36 | u32 | `flags` | bit 0: computed by satk (not copied from the source file) |
| 40 | u32 | `aux0` | mip_chain: D3D9 format (FOURCC or D3DFMT number); else 0 |
| 44 | u32 | `aux1` | mip_chain: width of the first stored level; else 0 |
| 48 | u32 | `aux2` | mip_chain: height of the first stored level; else 0 |
| 52 | u32 | `aux3` | mip_chain: index of the first stored level (0 = the full chain); else 0 |
| 56 | u64 | `reserved` | zero |

Sections are sorted by `(kind, index, stream)`; that triple is unique. Every payload is covered by its own CRC-32.

### Section kinds

| Kind | Name | Payload |
|---:|---|---|
| 1 | `geom_stream` | one per-vertex stream of one geometry (`index` = geometry index, `stream` = stream type) |
| 2 | `light_list` | the lights of one geometry: `count` records of `LIGHT_V1` |
| 3 | `mip_chain` | derived mip levels of one texture: `count` levels, tightly packed from the first stored level down; `aux0` = D3D9 format (a FOURCC such as `DXT1` as a little-endian u32, or a `D3DFMT` number), `aux1` x `aux2` = size of the first stored level, `aux3` = its index (0 = the full chain) |
| 4 | `meta` | UTF-8 JSON of the derivation parameters |

### Geometry stream types

| Stream | Name | Format | Meaning |
|---:|---|---|---|
| 1 | `normal` | `oct16x2` (or `f32x3`) | unit vertex normal; flag bit 0 says satk computed it |
| 2 | `night_color` | `rgba8` | the night vertex colours as the file stores them, ready for the GPU day/night blend |
| 3 | `tangent` | `snorm8x4` | tangent x, y, z and the bitangent sign, derived from UV set 0 |

### Element formats

| Id | Name | Stride | Encoding |
|---:|---|---:|---|
| 1 | `f32x3` | 12 | three 32-bit floats |
| 2 | `f32x4` | 16 | four 32-bit floats |
| 3 | `oct16x2` | 4 | octahedral map of a unit vector, two snorm16 (`D3DDECLTYPE_SHORT2N`), below |
| 4 | `rgba8` | 4 | r, g, b, a bytes in the order of the RenderWare vertex-colour stream |
| 5 | `snorm8x4` | 4 | four signed bytes: `round(v * 127)` for x, y, z; w is +127 or -127 (the sign) |
| 6 | `light_v1` | 32 | the light record, below |
| 7 | `bytes` | 0 | opaque bytes (mip levels) |
| 8 | `json` | 0 | UTF-8 JSON |

**Octahedral normals.** Encode: `n = |x| + |y| + |z|`; `(px, py) = (x / n, y / n)`; if `z < 0`:
`(px, py) = ((1 - |py|) * sgn(px), (1 - |px|) * sgn(py))` with `sgn(0) = +1`; store `round(p * 32767)` as two
signed 16-bit integers (a zero vector stores `(0, 0)`). Decode: `p = stored / 32767`; `z = 1 - |px| - |py|`; if `z < 0`
apply the same fold to `(px, py)`; normalise `(px, py, z)`. The round trip error is below 0.01 degrees.

**`LIGHT_V1`** (32 bytes):

| Offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | 3 x f32 | `x, y, z` | position in the geometry's frame, as in the 2dEffect entry |
| 12 | 4 x u8 | `r, g, b, a` | light colour |
| 16 | f32 | `corona_far_clip` | distance beyond which the corona is not drawn |
| 20 | f32 | `point_range` | radius of the point light |
| 24 | f32 | `corona_size` | corona size |
| 28 | u32 | `flags` | the 2dEffect light flags: first flag byte, second flag byte shifted left by 8 |

## Recipe 1 (`derive_id` 1): DFF streams

`satk pack dac` fills these sections for every geometry of a DFF (geometries are numbered in stream order over all
clumps; platform-native geometries are skipped):

- `normal`: for geometries without normals (`--normals missing`, the default), or for all (`--normals all`; stored
  normals are copied, flag bit 0 stays clear). Computed normals are smooth, corner-angle weighted, with a hard edge
  where faces meet above `--angle` degrees (45 by default) or across a material seam; vertices at one position share
  their face normals, vertex records are never merged.
- `night_color`: the night-colour extension of the geometry, when it is present and holds colours.
- `tangent`: from UV set 0, orthogonalised against the normal; geometries without UVs or triangles get none.
- `light_list`: the 2dEffect entries of type 0 (lights) of the geometry.

The parameters stored in the meta section (and covered by the cache key) are `normals`, `tangents`, `night`, `lights`
and `angle`.

## Verification

`satk pack verify file.dac` reports the first structural problem as an error: `BAD_MAGIC`, `DAC_CRC` (header or a
payload), `BAD_VERSION`, `SIZE_MISMATCH`, `DAC_FORMAT` (table, kind or format does not fit), `DAC_RANGE` (a payload
outside the file or misaligned), `CACHE_KEY` (the key does not match the contents). Warnings: `RESERVED` (a reserved
field is not zero), `STREAM_UNKNOWN`; error `SECTION_DUP` (one `(kind, index, stream)` twice).

## Compatibility rules

The same rules as [saepak.md](saepak.md): a reader accepts its major version and ignores later minor additions
(`header_size` and the section table say where things are), reserved fields are zero, every offset and count is checked
against the file size before use. A recipe change gets a new `derive_id`, which changes every cache key.

## Related

[pack.md](pack.md) (commands), [saepak.md](saepak.md) (the content container).
