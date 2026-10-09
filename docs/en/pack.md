# `satk pack`: content containers, derived-asset cache files and the texture dedup census

[Русская версия](../ru/pack.md)

Package: `satk.pack`. Byte layouts: [saepak.md](saepak.md) (the `.saepak` container) and [dac.md](dac.md) (DAC files).

## What it is

Three tools for content that a modified engine loads and shares:

- **`.saepak`** is one file that holds many DFF, TXD, COL, IFP, IPL and DAT files. It is addressed by content
  (SHA-256, identical files are stored once), carries a hash for every chunk of every file so that a downloader can
  fetch and check pieces, and is also a valid IMG VER2 archive, so the engine's streamer and any IMG tool can read it.
  `build`, `verify` and `inspect` make, check and show packs of files you own.
- **DAC** files (derived-asset cache) hold per-vertex and per-model data a renderer needs and the stock files lack:
  normals for prelit geometry, the night-colour stream, tangents, the light list. `satk pack dac` derives them from
  DFFs into `<workspace>/work/cache/dac/`. They are local only and never redistributed.
- **`census`** measures how much texel data the texture sharing key would save: per TXD and over a whole index
  profile. Its files are game-derived numbers and names, so they go to `<workspace>/work/out/pack/census/` and
  never into git.

Everything reads its inputs only; outputs go under `<workspace>/work/` (or the `--out` you give).

## Quick example

```powershell
satk pack census --limit 5
satk pack census --by txd --limit 5
```

What comes back (shortened; needs the vanilla index, `satk index build`):

```json
{"ok":true,"cols":["rank","name","fmt","w","h","levels","copies","txds","chain_bytes","saved_chain_bytes","in"],"rows":[[1,"ws_rooftarmac1","DXT1",256,256,9,181,181,43704,7866720,"2notherbuildsfe, 711_sfw, a51_ext"],["…"]],"total":5042,"summary":{"textures":32874,"mip0_mib":624.8,"keys":{"texel":{"distinct":14559,"saved_pct_mip0":54.8},"full":{"distinct":15199,"saved_pct_mip0":52.3},"…":"…"},"…":"…"},"files":{"census.json":"<work>/out/pack/census/vanilla/census.json","…":"…"}}
```

Making a pack from your own files (a folder or a list of files; nothing here is a game file):

<!-- docs-smoke: skip needs a mod folder of your own -->
```powershell
satk pack build mymod --namespace mymod --priority 10
satk pack verify mymod
satk pack inspect mymod --show chunks --name models/car.dff
satk pack dac mymod/car.dff
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk pack build INPUT... [--out FILE] [--name N] [--namespace NS] [--mount overlay\|addon\|cache] [--priority N] [--chunk-kib 64] [--fast none\|xxh3] [--flat] [--local-only] [--no-manifest] [--force]` | — (through `satk_op`) | folders and/or `.dff .txd .col .ifp .ipl .dat` files into `<work>/out/pack/<name>.saepak` plus `<name>.saepak.manifest`; reports sizes and the bytes saved by content addressing |
| `satk pack verify TARGET [--no-deep] [--sev info\|warn\|error]` | — | footer and manifest hashes, table bounds, the IMG directory against the manifest, alignment, overlaps, the pack id; with `--deep` (default) SHA-256 of every entry and chunk. `CHECK_FAILED` lists the errors. Also checks `.manifest` and `.dac` files |
| `satk pack inspect TARGET [--show names\|entries\|chunks] [--name N]` | — | version, pack id, namespace, mount mode and priority, counts, sizes, dedup saving; a table of names (logical name, IMG stream name, kind, size, hash), of entries, or the chunk plan of one name (offset, length, SHA-256 per chunk). `.dac` files list their sections |
| `satk pack dac TARGET... [--normals missing\|all\|none] [--no-tangents] [--no-night] [--no-lights] [--angle 45] [--out DIR] [--profile P]` | — | DFF files, folders, `dff:<name>` or `model:<id>` into content-addressed `.dac` files (`<work>/cache/dac/<hh>/<key>.dac`, or `<name>.dac` in `--out`); a repeat with unchanged inputs writes nothing |
| `satk pack census [--profile vanilla] [--by groups\|txd] [--exact]` | — | the dedup census of an index profile (below) |

`TARGET` is a path, or the name of a file in `<work>/out/pack/`.

## How it works

**Build.** Inputs are sorted by logical name (a lower-case relative path such as `models/car.dff`), hashed once <!-- linkcheck: ignore -->
(SHA-256 of each file and of each 64 KiB chunk), identical files are stored once, every payload starts on a 2048-byte
sector, and the IMG directory, the manifest and the footer are written around them. The same inputs and options give
the same bytes (no time, no machine data). A header check warns about files that do not look like what their extension
says (`BAD_HEADER`) but does not block: the container does not judge its content. One entry is at most 65535 sectors
(just under 128 MiB), the IMG limit. IMG stream names are the file's base name when it fits the 23-character field,
otherwise `~<18 hex digits of the SHA-256>.<ext>`; a name that another content already uses gets the same treatment.

**Verify and inspect** read the footer first (the last 128 bytes), then the manifest, so they work on a downloaded
`.manifest` without the payload; `--deep` reads each entry once.

**DAC.** `pack dac` computes, per geometry of a DFF: seam-aware smooth normals for geometries that have none (or for all
with `--normals all`), the night colours as a ready vertex stream, tangents from UV set 0, and the 2dEffect lights of
type 0. The file name is the cache key, `SHA-256(source bytes, recipe id, parameters)`, so a changed source or option
never finds a stale file. See [dac.md](dac.md).

**Census.** For every active TXD of an index profile it counts textures and bytes and groups them under three keys:
the base-level hash alone (`texel`), the hash and the name (`texel_name`), and the name-preserving key
`(hash, name, format, width, height, mip count)` (`full`): the key under which two textures may share one D3D texture
while name-based world-texture shaders keep working. Bytes are counted for the base level (`mip0`) and for all levels
(`chain`, computed from format, size and level count). The index hashes only the base level (plus a palette);
`--exact` re-reads the TXD files and hashes the whole mip chain. The table lists the groups with the most saved
bytes (`--by groups`) or the TXDs with the most bytes shared with other TXDs (`--by txd`). Files in
`<work>/out/pack/census/<profile>/`: `census.json` (the summary), `per_txd.csv`, `groups.csv`. They are deterministic
and rewritten only when they change.

### The vanilla numbers

The stock 1.0 US copy (profile `vanilla`):

| Key | Distinct | Unique base-level MiB | Saved | Unique MiB with all levels | Saved |
|---|---:|---:|---:|---:|---:|
| all textures | 32,874 | 624.8 | | 703.8 | |
| `texel`: base-level hash | 14,559 | 282.6 | 54.8 % | 303.6 | 56.9 % |
| `texel_name`: hash + name | 14,811 | 285.0 | 54.4 % | 306.1 | 56.5 % |
| `full`: hash, name, format, size, mips | 15,199 | 297.8 | 52.3 % | 320.1 | 54.5 % |
| `full` with `--exact` (whole mip chain hashed) | 15,729 | 315.2 | 49.5 % | 343.3 | 51.2 % |

- 32,874 textures in 3,979 TXDs hold 624.8 MiB of base-level data (703.8 MiB with all mip levels).
- Sharing by texel hash alone would keep 14,559 textures (282.6 MiB); keeping the name as well, 14,811 (285.0 MiB).
- The extra fields of the sharing key cost only the **mip count**: 388 groups with the same hash and name ship both with
  a full chain (9 levels) and with one level (12.8 MiB of base-level data, 19.6 MiB with chains). Nothing else splits a
  (hash, name) group: format and size never differ.
- Hashing the whole chain (`--exact`) finds 530 more groups, and in all of them the base levels are identical and
  one TXD stores the smallest two to four mip levels as empty data while another stores them (17.4 MiB of base-level
  data, 23.2 MiB with chains). Whether to treat those as equal is a matter for the residency layer, which can rebuild
  small levels; the census only counts them apart.
- A TXD cannot hold two textures of one name, so sharing **inside** one TXD saves nothing; every saved byte comes from
  textures that several TXDs carry (345 groups have more than ten copies, the largest 181).

## Limitations and known issues

- `pack build` packs what it is given; it does not parse the content, so it cannot tell a broken DFF from a good one
  (`BAD_HEADER` only checks the first chunk or magic).
- XXH3-128 fast hashes need the optional `xxhash` package (`--fast xxh3`); without it the fields stay zero and
  `verify` reports `FAST_SKIPPED` for a pack that has them. SHA-256 is always checked.
- A pack is a container, not an installer: mounting packs in the game and the client is the job of the engine.
- Census numbers describe the files on disk. What is saved at run time depends on which TXDs are loaded together.
- DAC derivation reads DFFs through the lossless RenderWare chunk tree; platform-native geometries are skipped with a
  `NATIVE` warning. Normals are computed per vertex record, so the vertex count never changes.

## Python API

```python
from satk.pack.sources import collect
from satk.pack.saepak import build_pack, verify_pack, Pack
from satk.pack.dac import derive_dff, encode_dac, decode_dac
from satk.pack.census import run_census

sources, warnings = collect(["mymod"])
report = build_pack(sources, "mymod.saepak", namespace="mymod")
with Pack.open("mymod.saepak") as pack:
    data = pack.read_name("models/car.dff")
    plan = pack.chunk_plan(pack.names[pack.name_index("models/car.dff")]["entry_index"])   # [(offset, length, sha256)]
```
