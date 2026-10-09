# The `.saepak` container: byte layout, format 1.0

[Русская версия](../ru/saepak.md)

Package: `satk.pack` (`satk pack build`, `verify`, `inspect`; see [pack.md](pack.md)). The tables on this page are
printed from the code (`satk.pack.layout`) and a test compares them, so they cannot drift.

## What it is

A `.saepak` holds game files (DFF, TXD, COL, IFP, IPL, DAT) so that they can be mounted, fetched in pieces and
checked. The design goals are:

- **content addressed**: every distinct content is stored once and named by its SHA-256; a name is a separate,
  small record that points at the content;
- **IMG compatible**: the file begins with a standard IMG VER2 directory and every payload starts on a 2048-byte
  sector, so the engine's streamer and IMG tools read it unchanged; everything extra sits after the payload;
- **chunked**: every content has a SHA-256 per chunk (default 64 KiB), so a downloader can fetch byte ranges and verify
  each piece, and a damaged download is repaired chunk by chunk;
- **versioned and extensible**: a major and a minor version, header and table sizes stored in the file, reserved
  fields that must be zero, and fast-hash fields (XXH3-128) reserved in every record;
- **deterministic**: the same inputs and options give the same bytes.

All integers are little-endian. "Offset" columns below are byte offsets inside the record.

## File layout

```
offset 0                  IMG VER2 directory: "VER2", u32 count, count x 32-byte entries, zero padded to a sector
data_offset               payload: each distinct content at a sector boundary, zero padded to whole sectors
manifest_offset           manifest block (sector aligned): header, entry table, name table, chunk table, strings
pack_size - 128           footer (128 bytes)
```

A reader starts at the end: the last 128 bytes are the footer, which locates the manifest block (an HTTP suffix range
of 128 bytes, then one range for the manifest), so the payload is never touched until a content is needed.
`<pack>.manifest` is the manifest-only copy: the manifest block followed by the footer (`manifest_size + 128` bytes).
Its offsets are the offsets in the full pack; a reader tells the two apart by the file size (`pack_size` or
`manifest_size + 128`).

### IMG directory

The first sectors. The count is the number of distinct stream names (`dir_count`), each entry is:

| Offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | u32 | `offset_sectors` | payload offset in 2048-byte sectors |
| 4 | u16 | `stream_sectors` | payload size in sectors (the engine streaming size) |
| 6 | u16 | `archive_sectors` | always 0; the engine then uses stream_sectors |
| 8 | u8[24] | `name` | stream name, NUL padded, at most 23 characters |

Two names with the same content point at one payload (two directory slots with one offset), or share one slot when
their stream names are equal. Entries are sorted by stream name. `archive_sectors` is 0 (the engine takes
`stream_sectors`). One content is at most 65535 sectors (134,215,680 bytes), the limit of the 16-bit size field. A whole pack is mounted as
one IMG and stays under 4 GiB (`build` refuses a larger one: split the inputs).

### Payload

Contents in the order of their lowest logical name; each starts at a multiple of 2048 and is followed by zero bytes up
to the next sector. `data_size` is the length of the region; `data_offset + data_size == manifest_offset`.

### Footer

| Offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | u8[8] | `magic` | ASCII SAEPAKFT |
| 8 | u16 | `major` | format major version (1); a reader refuses a larger one |
| 10 | u16 | `minor` | format minor version (0); additions only |
| 12 | u32 | `flags` | 0 |
| 16 | u64 | `manifest_offset` | absolute offset of the manifest block in the full pack (sector aligned) |
| 24 | u64 | `manifest_size` | size of the manifest block in bytes (a multiple of 8) |
| 32 | u64 | `pack_size` | size of the full pack file; a manifest-only file is manifest_size + 128 bytes |
| 40 | u8[32] | `manifest_sha256` | SHA-256 of the manifest block |
| 72 | u8[16] | `manifest_fast` | fast hash of the manifest block (header fast_algo); zero when fast_algo is 0 |
| 88 | u32 | `crc32` | CRC-32 (zlib) of footer bytes 0..87 |
| 92 | u8[36] | `reserved` | zero |

A reader checks the magic, the CRC, the major version (a larger major is refused), then reads `manifest_size` bytes at
`manifest_offset` (or at `file size - 128 - manifest_size` in a manifest-only file) and checks their SHA-256.

### Manifest header

| Offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | u8[8] | `magic` | ASCII SAEMANIF |
| 8 | u16 | `major` | format major version (1) |
| 10 | u16 | `minor` | format minor version (0) |
| 12 | u32 | `header_size` | 192; a later minor version may enlarge the header, tables start at the offsets below |
| 16 | u32 | `flags` | bit 0: the file starts with an IMG VER2 directory (always set in 1.0) |
| 20 | u32 | `sector_size` | 2048 |
| 24 | u32 | `chunk_size` | bytes per hash chunk: a power of two from 2048 to 64 MiB (default 65536) |
| 28 | u32 | `fast_algo` | 0 = fast hash fields are zero, 1 = XXH3-128 canonical (big-endian) digest |
| 32 | u32 | `entry_count` | records in the entry table (distinct contents) |
| 36 | u32 | `name_count` | records in the name table (logical names) |
| 40 | u32 | `dir_count` | records in the IMG directory at the file start (distinct stream names) |
| 44 | u32 | `chunk_count` | records in the chunk table |
| 48 | u64 | `entries_off` | offset of the entry table, relative to the manifest start |
| 56 | u64 | `names_off` | offset of the name table, relative to the manifest start |
| 64 | u64 | `chunks_off` | offset of the chunk table, relative to the manifest start |
| 72 | u64 | `strings_off` | offset of the string area, relative to the manifest start |
| 80 | u64 | `strings_size` | size of the string area in bytes |
| 88 | u64 | `data_offset` | absolute offset of the first payload byte (end of the IMG directory sectors) |
| 96 | u64 | `data_size` | bytes of the payload region (a multiple of 2048) |
| 104 | u8[32] | `pack_id` | SHA-256 identity of namespace and logical-name -> content mapping (see the spec) |
| 136 | i32 | `priority` | mount priority: larger wins over smaller within the same mount mode |
| 140 | u16 | `mount_mode` | 0 overlay (shadows same-named entries of lower layers), 1 addon (new names only), 2 cache (content-addressed cache image) |
| 142 | u16 | `residency_flags` | bit 0: texture dedup allowed; bit 1: local only, never redistribute |
| 144 | u32 | `namespace_off` | string offset of the namespace of the logical names |
| 148 | u32 | `namespace_len` | string length in bytes |
| 152 | u32 | `title_off` | string offset of a human title (may be empty) |
| 156 | u32 | `title_len` | string length in bytes |
| 160 | u32 | `mount_name_off` | string offset of the suggested IMG file name when mounted (may be empty) |
| 164 | u32 | `mount_name_len` | string length in bytes |
| 168 | u64 | `reserved1` | zero |
| 176 | u64 | `reserved2` | zero |
| 184 | u64 | `reserved3` | zero |

Tables follow the header in this order, each 8-byte aligned and addressed by the offsets in the header: entries, names,
chunks, strings. The block is padded with zeros to a multiple of 8 bytes.

### Entry table (distinct contents)

| Offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | u8[32] | `sha256` | SHA-256 of the content (the content address) |
| 32 | u8[16] | `fast` | fast hash of the content (header fast_algo); zero when fast_algo is 0 |
| 48 | u64 | `offset` | absolute payload offset (sector aligned) |
| 56 | u64 | `size` | content size in bytes (1 .. 65535 sectors) |
| 64 | u32 | `first_chunk` | index of the first record in the chunk table |
| 68 | u32 | `chunk_count` | number of chunk records: ceil(size / chunk_size) |
| 72 | u8 | `kind` | 0 other, 1 dff, 2 txd, 3 col, 4 ifp, 5 ipl, 6 dat |
| 73 | u8 | `flags` | 0 |
| 74 | u16 | `reserved1` | zero |
| 76 | u32 | `stream_sectors` | ceil(size / 2048) |
| 80 | u32 | `rw_version` | RenderWare version of the first chunk (e.g. 0x36003), 0 when the content is not RW |
| 84 | u32 | `reserved2` | zero |
| 88 | u64 | `reserved3` | zero |

Entries are sorted by their lowest logical name and laid out consecutively in that order; a verifier reports a gap
between payloads (`GAP`) and an overlap (`OVERLAP`).
`first_chunk` of entry *i+1* equals `first_chunk + chunk_count` of entry *i*: the entries tile the chunk table in order.

### Name table (logical names)

| Offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | u32 | `entry_index` | index in the entry table |
| 4 | u32 | `dir_index` | index of the IMG directory slot that streams this name |
| 8 | u32 | `logical_off` | string offset of the logical name |
| 12 | u32 | `logical_len` | string length in bytes |
| 16 | u64 | `asset_id` | reserved for the registry's 64-bit AssetId; 0 in 1.0 |
| 24 | u32 | `flags` | bit 0: the stream name was derived from the content hash |
| 28 | u32 | `reserved1` | zero |
| 32 | u64 | `reserved2` | zero |

Names are sorted by the UTF-8 bytes of the logical name and unique. The logical name is a lower-case relative path with
forward slashes, up to 240 bytes, no empty, `.` or `..` components, no `:`, ending in one of `.dff .txd .col .ifp .ipl
.dat`. It is the key the registry and the mount table use together with the pack's namespace; the asset id is derived
from both (the `asset_id` field is reserved for it).

### Chunk table

| Offset | Type | Field | Meaning |
|---:|---|---|---|
| 0 | u8[32] | `sha256` | SHA-256 of the chunk bytes (the last chunk of an entry is shorter) |
| 32 | u8[16] | `fast` | fast hash of the chunk (header fast_algo); zero when fast_algo is 0 |

An entry of `size` bytes has `ceil(size / chunk_size)` chunk records; chunk *k* covers content bytes
`[k * chunk_size, min(size, (k + 1) * chunk_size))`, which is the absolute range `[offset + k * chunk_size, ...)`. A
content of up to one chunk has one record whose hash equals the entry hash. The chunk size is a power of two from 2048
to 64 MiB (default 65536), so a chunk always starts on a sector boundary.

### Strings

UTF-8, no separators and no terminators; every string is addressed by an `(offset, length)` pair of the header or of a
name record, relative to `strings_off`. The area holds the namespace, the title, the suggested mount name and all
logical names. Equal strings are stored once; the empty string is `(0, 0)`.

## Names, identity and mounting

**Stream names.** A logical name's *stream name* is what the IMG directory and the engine see: the base name of the
path when it is at most 23 characters and made of `a-z 0-9 _ - .` (not starting with `.` or `-`) with a three-letter extension; otherwise
`~<first 18 hex digits of the content SHA-256>.<ext>` (exactly 23 characters). When two different contents want the same
base name, the first in logical-name order keeps it and the others get the derived name; name record flag bit 0 marks a
derived stream name. A verifier checks that every slot name is explained by a logical name.

**Pack id.** `pack_id` is `SHA-256` over the bytes `SAEPACK1` + `0x0A`, the namespace and `0x0A`, then for every
name sorted by its UTF-8 bytes: the logical name, a zero byte, the 32-byte content SHA-256 and `0x0A`. It names *what
the pack maps*: it does not change with the chunk size, the mount metadata or the layout, and it changes with a name, a
content or the namespace.

**Mount metadata** (header): `namespace`, `title`, `mount_name` (the IMG file name to use when mounting), `priority`,
`mount_mode` and `residency_flags`. `overlay` packs shadow same-named entries of lower layers (higher priority wins),
`addon` packs only add names (a name that already exists below is a conflict for the mounting layer), `cache` is a
content-addressed cache image. `residency_flags` bit 0 allows the texture dedup of the residency layer for the pack's
TXDs; bit 1 marks derived or private content that must not be redistributed (a DAC-bearing or cache pack).

## Hashes

SHA-256 is always present and is the authority. The `fast` fields (16 bytes in every entry, chunk and the footer) hold
the canonical (big-endian) XXH3-128 digest when `fast_algo` is 1 and are zero when it is 0; a reader that cannot compute
XXH3 skips them and says so (`FAST_SKIPPED`). The writer fills them only on request (`--fast xxh3`, needs the `xxhash`
package), so the default output does not depend on an optional package.

## Compatibility rules

- A reader accepts every file whose **major** version it knows and ignores unknown **minor** additions: new fields only
  appear in reserved space or after the tables, and `header_size` tells where a larger header ends.
- Reserved fields are written as zero; a verifier warns when they are not (`RESERVED`).
- A reader must treat every offset and count as untrusted: check them against the file and the manifest size before
  use (the reference reader does, and refuses a manifest whose tables do not fit it).
- Unknown `kind` values and unknown mount modes are not errors; they are shown as such.

## Verification

`satk pack verify` reports these finding codes (E = error, W = warning, I = info):

| Code | Level | Meaning |
|---|---|---|
| `UNREADABLE` | E | the file cannot be opened or read |
| `BAD_MAGIC` | E | no footer magic at the end of the file (not a pack, truncated, or extra bytes appended) |
| `FOOTER_CRC` | E | the footer CRC does not match |
| `BAD_VERSION` | E | a major version this reader does not know |
| `SIZE_MISMATCH` | E | the file size is neither `pack_size` nor `manifest_size + 128` |
| `MANIFEST_RANGE` | E | the manifest is misaligned, misplaced or its size is invalid; the payload region does not end at it |
| `MANIFEST_HASH` | E | the manifest SHA-256 differs from the footer |
| `MANIFEST_FORMAT` | E | a table, string or header field does not fit the manifest |
| `NAME_INVALID`, `NAME_DUP`, `NAME_ENTRY`, `NAME_DIR` | E | a bad, repeated or dangling logical name, entry index or directory slot |
| `NAME_ORDER`, `KIND_MISMATCH` | W | names not sorted; the extension and the entry kind disagree |
| `ENTRY_SIZE`, `ENTRY_RANGE`, `ALIGN`, `OVERLAP`, `ENTRY_DUP` | E | wrong size or sector count, payload outside the region, misaligned, overlapping or duplicated contents |
| `CHUNK_RANGE` | E | the chunk records do not tile the chunk table or do not match `ceil(size / chunk_size)` |
| `PACK_ID` | E | `pack_id` does not match the namespace and the name -> content map |
| `DIR_MISMATCH`, `STREAM_NAME`, `STREAM_NAME_DUP` | E | the IMG directory disagrees with the manifest, a slot name is not explained by its logical name, repeated slot names |
| `DIR_ORPHAN`, `GAP`, `PADDING`, `RESERVED`, `TOO_LARGE` | W | an unused slot, unused bytes between payloads, non-zero padding, non-zero reserved fields, a file of 4 GiB or more |
| `ENTRY_HASH`, `CHUNK_HASH`, `FAST_HASH` | E | (deep) a content, a chunk or a fast hash differs from the manifest |
| `FAST_SKIPPED`, `MANIFEST_ONLY` | I | XXH3 not checked (no package); a manifest-only file, payload not checked |

## Related

[pack.md](pack.md) (commands), [dac.md](dac.md) (the derived-asset cache format), [formats.md](formats.md) (IMG and the
RenderWare files).
