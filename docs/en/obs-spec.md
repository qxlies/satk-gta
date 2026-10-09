# Observability formats: timeline key, JSONL records, bench JSON, containers

[Русская версия](../ru/obs-spec.md)

Specification version: **1.0** (stream `sae-obs/1`, bench `sae-bench/1`, container `1.0`). Reader, writer and validator:
[obs.md](obs.md). Machine-readable parts: `data/obs/*.schema.json` (JSON Schema 2020-12) and
`data/obs/sae-container-layout.json` (byte layout); `satk obs schema` prints them.

## Scope and versions

The sa-engine client, the dedicated server and the test bots all report what they do over time. Every report uses the
same vocabulary, so a client trace, a server trace and a bot trace can be joined, compared and benchmarked with one set
of tools. The family has four parts:

| Part | Id | What it is |
|---|---|---|
| Timeline key | - | four members every record carries: `t_us`, `tick`, `sub`, `frame` |
| JSONL stream | `sae-obs/1` | a header, then one JSON object per line: frame, tick, stats, event, network, input, seed, snap records and a footer |
| Bench JSON | `sae-bench/1` | one result document per scene run, shared by the in-game bench and the trace and server benches |
| Container | `.saenet` / `.saerec` v1.0 | a header, append-only chunks and a chunk table around the same records; optional zlib |

The data is engine-agnostic: nothing in a file names a game asset, and the formats carry no game content.

**Versions.** The id carries the major version (`sae-obs/1`); breaking changes get a new major version and a new schema
file. Compatible changes (a new optional member, a new record kind, a new enum value of an open field, a new chunk type
that is not critical) raise the minor version, which a stream writes as `minor` in its header and a container in its
header. A reader of major version 1 accepts any minor version and ignores members it does not know (strict validation,
`--strict`, reports them instead). A reader refuses another major version. Members are never renamed or re-typed within
a major version.

## The timeline key

| Member | Type | Meaning |
|---|---|---|
| `t_us` | integer 0..2^63-1 | session time in microseconds, in the server clock domain (the local clock when not connected) |
| `tick` | u32 | server tick `T = t_us // dt_s_us`; 4294967295 = no tick grid |
| `sub` | u32 | client simulation tick `k = t_us * sub_n // dt_s_us` of the fixed mode; 4294967295 in classic mode and on records of a process that has none |
| `frame` | u32 | client frame counter; 4294967295 for a process without frames (server, bot) |
| `tsrc` | optional enum | where `t_us` comes from: `server` (a server-side record), `est` (client estimate, clock locked), `est_unlocked`, `local` (not connected) |

`dt_s_us` is the server tick period in whole microseconds (33333 for 30 Hz) and `sub_n` the number of client ticks per
server tick (`dt_c = dt_s / n`); both are in the header of the stream. Keys are totally ordered by
`(t_us, tick, sub, frame)`. Joining streams of several nodes is a merge by this order (`satk obs merge`), which is only as
accurate as the clock alignment: records with `tsrc` `local` are not server-aligned.

Rules the validator enforces: `tick` is `t_us // dt_s_us` or 4294967295 (always the latter when `dt_s_us` is 0); a
`tick` record has `sub = t_us * sub_n // dt_s_us` (its `t_us` is the first microsecond of the tick:
`ceil(k * dt_s_us / sub_n)`); any other record has a `sub` at most that value (the last tick completed); classic mode
(`sim: classic` in the header) has `sub` 4294967295 everywhere and no `tick` records.

## The JSONL stream `sae-obs/1`

A UTF-8 file, one JSON object per line, `\n` line ends (a BOM and `\r\n` are tolerated when reading). The first record is
the header; the last is the footer. Records are ordered by `t_us` per node. Files of any size are read line by line.

| `rec` | Written | Required members besides the key | Optional |
|---|---|---|---|
| `hdr` | once, first (no key) | `schema`, `role`, `node`, `clock` | `minor`, `session`, `sim`, `pulse`, `fences`, `engine`, `producer`, `started`, `merged` |
| `frame` | at frame end (F7) | `frame_ms`, `ticks` | `work_ms`, `dropped_ms`, `fence_ms`, `gpu_ms`, `va_owner_mib`, `stream`, `ctr`, `gauge`, `sim`, `pulse`, `flags` |
| `tick` | after each simulation tick | `tick_ms`, `dt_us` | `fence_ms`, `ctr`, `gauge` |
| `stats` | about once a second | `frames` | `ticks`, `fences`, `game`, `mem`, `stream`, `va_owner_mib`, `gpu_ms`, `ctr`, `gauge` |
| `event` | when something happens | `name` | `level`, `data` |
| `net` | per transport row | `dir`, `element_id`, `packet_id`, `bytes` | `peer`, `subtype`, `lane`, `seq` |
| `input` | per recorded input | `src`, `name` | `value`, `data` |
| `seed` | per seed or deterministic parameter | `name`, `value` | - |
| `snap` | per correction of an entity | `entity`, `kind` | `err_m`, `data` |
| `end` | once, last (carries the key of the last record) | `counts` | `dropped_records`, `clean` |

Every record may carry `node` (required in a merged stream). Unknown members are allowed (see Versions).

**Header.** `schema` is `sae-obs/1`; `role` is `client`, `server`, `bot` or `tool`; `node` is an id such as `client-1`;
`clock.dt_s_us` is the tick period (0 = no grid), `clock.sub_n` the client ticks per server tick, `clock.domain`
`server` or `local`; `sim` is `classic` or `fixed`; `pulse` is `standard` or `alt` (the network pulse order); `fences`
lists the fences the producer reports; `session` is a hex id shared by the files of one session.

**`frame`.** `frame_ms` is the interval between this frame's F0 and the previous frame's F0 (what `onClientPreRender`'s
`timeSlice` measures); `work_ms` is the CPU time from F0 to F7 without the limiter wait; `ticks` is the number of
simulation ticks run in the frame (classic: 1 for a simulated frame, 0 otherwise); `dropped_ms` is simulated time dropped
by the spiral-of-death guard in this frame; `fence_ms` maps a fence name (`F0`..`F7`, with an optional sub-fence letter:
`F3b`, `F6a`) to the milliseconds spent in its callbacks; `gpu_ms` maps a render pass to milliseconds (resolved two
frames late); `va_owner_mib` maps a budget owner to the address space it holds; `stream` is `{used_mib, budget_mib}`;
`ctr` holds counters, cumulative since the start of the stream and never decreasing (`sim.dropped_ms`, `timer.reentry`);
`gauge` holds levels at this frame (`visible_entities`); `flags` lists `minimized`, `skipped_render`, `loading`,
`front_end`, `cutscene`, `rebase`. The `t_us` of a frame record is the time sampled at F0.

**`tick`.** `tick_ms` is the CPU time of the tick part, `dt_us` the simulated step. Server streams use `tick` records for
the server tick loop (`sub` and `frame` 4294967295).

**`stats`.** The windowed numbers of `getEngineStats()`: `frames` is a frame-statistics block (below) over the last window
(the engine keeps 1024 frames), `ticks` and `fences` are `{n, avg_ms, p50_ms, p95_ms, p99_ms, max_ms}` blocks, `game`
is `{time_ms, timer_mode, frame_counter, frame_counter_hz, clock_ratio}` (SA's own clock, not session time), `mem` is
`{va_used_mib, va_largest_free_mib, private_mib, working_set_mib, va_guard, cef_loaded}`.

**Frame-statistics block.** `n`, `avg_ms`, `p50_ms`, `p95_ms`, `p99_ms`, `p999_ms`, `max_ms`, `fps_avg`, `low1_fps` (FPS of
the mean of the slowest 1 % of frames), `integer_share`, `sum_ms`, `wall_ms`, `fps_wall` and `q`, the 0th to 100th
percentile (101 values). Percentiles are exact nearest-rank values of the sorted frames: `q[p] = sorted[floor(p/100 *
(n-1) + 0.5)]`. The same block appears in `stats` records and in bench stages.

**Mapping of the v1 engine statistics.** `getEngineStats()` of the client maps as follows.

| v1 value | Member |
|---|---|
| average FPS, p50 / p95 / p99 / maximum frame time, 1 % low | `frames.fps_avg`, `frames.p50_ms`, `frames.p95_ms`, `frames.p99_ms`, `frames.max_ms`, `frames.low1_fps` |
| `gameTimeMs`, `timerMode`, `frameCounter` and its rate | `game.time_ms`, `game.timer_mode`, `game.frame_counter`, `game.frame_counter_hz` |
| VA used, largest free block, private bytes, working set, guard state, CEF loaded | `mem.va_used_mib`, `mem.va_largest_free_mib`, `mem.private_mib`, `mem.working_set_mib`, `mem.va_guard`, `mem.cef_loaded` |
| streaming used and budget | `stream.used_mib`, `stream.budget_mib` |

**Footer.** `counts` has one number per record kind written (without `hdr` and `end`). A stream without a footer was cut
or is still being written.

### What the validator checks

`satk obs validate` reports a table of `where`, `severity`, `code`, `message`. Errors make the verdict FAIL; warnings
do not.

| Code | Severity | Meaning |
|---|---|---|
| `JSON`, `SCHEMA` | error | a line is not a JSON object; a record breaks its schema |
| `HDR_FIRST`, `HDR_LATE`, `HDR_DUP`, `NODE` | error | header placement; a record whose node has no header, or is not named in a stream with several headers |
| `VERSION` | error / warning | another major version; a newer minor version (warning) |
| `ORDER`, `FRAME_ORDER`, `TICK_ORDER` | error | `t_us` goes back; frame numbers or tick `sub` do not advance |
| `KEY_TICK`, `KEY_SUB`, `KEY_FRAME` | error | the key disagrees with the grid of the header; a frame record without a frame number |
| `MODE` | error | a record contradicts `sim` of the header (classic with `sub`, ticks above 1, a `tick` record) |
| `CTR_DECREASE` | error | a cumulative counter went down |
| `END_COUNT`, `AFTER_END`, `EMPTY` | error | footer counts differ; a record after the footer; no records |
| `NO_END`, `WORK`, `FENCE`, `STATS`, `BLANK` | warning | no footer; `work_ms` above the frame interval; a fence the header does not list; inconsistent statistics block; an empty line |

## The bench JSON `sae-bench/1`

One document per run: `{schema, run, scene, label, title, started, source, request, env, key, stages, summary,
planned_stages, incomplete, error, warn}`. `source.tool` names the producer (`ingame-bench`, `obs-convert`, later the
trace and server benches) and `source.converted_from` the schema of the file it was converted from. `env` says what was
measured: `role`, `sae` (true for an sa-engine build), `build`, `preset`, `profile`, `sim`, `pulse`, `features` (which
engine functions the runner found), `screen`, `settings`. `key.first` and `key.last` bound the run on the timeline.

A **stage** is one measured window: `id`, `index`, `title`, `warmup_s`, `sample_s`, `key` (`start` and `end`), `frames`
(a frame-statistics block, required), `ticks` (`n`, `per_frame_avg`, `max_per_frame`, `tick_ms` block, `dropped_ms`),
`fences` and `gpu` (maps to `{n, avg_ms, p50_ms, p95_ms, p99_ms, max_ms}` blocks), `series` (`cols` and `rows`:
about one row per second, `cols[0]` is `t_s`), `va` and `stream` (`start_mib`, `end_mib`, `peak_mib`, VA also
`growth_mib`), `clock` (`ratio`, `frame_counter`, `frame_counter_hz`, `game_ms`, `tick_count_ms`, `timer_mode`), `ctr`
and `gauge` (maps to `{last, max}`), `snap_start` and `snap_end` (engine snapshots as in a `stats` record), `counts`,
`probe`, `camera`, `fps_limit`, `console`, `raw` (producer tables kept verbatim), `ext` (members a converter did not
map) and `warn`.

The **summary** pools the stages: `stages`, `frames`, `fps_avg`, pooled `p50_ms` / `p95_ms` / `p99_ms` (each stage's 101
percentiles weighted by its frame count, so approximate when several stages are pooled), `max_ms`, the worst `low1_fps`,
`va_peak_mib`, `va_growth_mib`, `stream_peak_mib`, `clock_ratio_min` / `clock_ratio_max`, `shots_per_s`,
`ticks_per_frame`, `dropped_ms`, and `pooled` when there is more than one stage.

**From `satk-bench/1`.** `satk obs convert` maps the result files of `satk ingame bench` without loss.

| `satk-bench/1` | `sae-bench/1` |
|---|---|
| `client` (`version`, `features`, `screen`, `sae`, `preset`, `profile`, `settings`) | `env` with `role` `client` |
| stage `frames`, `va`, `stream`, `warn`, `counts`, `probe`, `camera`, `fps_limit`, `console`, `id`, `index`, `title`, `warmup_s`, `sample_s` | the same members |
| stage `clock.tick_ms` | `clock.tick_count_ms` |
| stage `series` (rows of `[t, frames, avg_ms, max_ms, va_mib, streaming_mib]`) | `series` with those `cols` (`t_s`, `frames`, `avg_ms`, `max_ms`, `va_mib`, `stream_mib`) |
| stage `limits_max` and `limits_last` (numeric leaves, dotted) | `gauge` entries `{last, max}` |
| stage `start` and `finish` (engine, mem, draw tables) | `snap_start` and `snap_end` (normalised) and the originals in `raw` |
| any other stage member | `ext` |
| `summary` | recomputed with the same arithmetic (equal on all recorded results) |

The numbers `bench-compare` reads (`frames.p50_ms`, `p99_ms`, `va.peak_mib`, `stream.peak_mib`, `clock.ratio`) are in the
same places, so the same comparison rules give the same verdicts on a converted file.

Validation adds to the schema: unique stage ids, consistent frame statistics (percentile order, `q` against the named
percentiles, FPS against the average), series rows as wide as `cols`, a summary that matches the stages, fewer than 30
frames reported as a warning, and `planned_stages` against the stages present.

## The container `.saenet` and `.saerec`

One container family: `.saenet` (kind `SNET`, a transport trace) and `.saerec` (kind `SREC`, a session record). The
payload of both is the same records as the JSONL stream; a container is the stream cut into chunks, optionally
compressed, with an index. All integers are little-endian.

**Header** (64 bytes, at offset 0).

| Offset | Type | Field |
|---|---|---|
| 0 | bytes[8] | magic `89 53 41 45 43 0D 0A 1A` |
| 8 | u16, u16 | version major, minor (1, 0) |
| 12 | fourcc | kind: `SNET` or `SREC` |
| 16 | u32 | `header_size` (64; a reader starts the first chunk there) |
| 20 | u32 | `flags`: bit 0 FINALIZED, bit 1 COMPRESSED, bit 2 SORTED, bit 3 MERGED; other bits 0 |
| 24 | u64 | `index_offset` of the chunk table (0 = not finalized) |
| 32 | u32 | `chunk_count` (valid when finalized) |
| 36 | u32 | `dt_s_us`, the server tick period (0 = unknown) |
| 40, 48 | i64, i64 | `t_first_us`, `t_last_us` of the records (-1 = unknown) |
| 56 | u32 | `session_id` (0 = unknown) |
| 60 | u32 | CRC-32 (zlib) of bytes 0..59 |

COMPRESSED is the compression flag: at least one chunk uses a codec other than 0. SORTED means the chunk time ranges are in
non-decreasing order. MERGED means several nodes (one META chunk each, `node` on every record).

**Chunk** (a 40-byte header, the payload, zero padding to a multiple of 8 bytes).

| Offset | Type | Field |
|---|---|---|
| 0 | fourcc | type |
| 4 | u8 | `codec`: 0 none, 1 zlib (RFC 1950), 2 zstd (reserved) |
| 5 | u8 | `cflags`: bit 0 CRITICAL |
| 6 | u16 | reserved, 0 |
| 8 | u32, u32 | `stored_size` (payload bytes as stored), `raw_size` (after decompression) |
| 16, 24 | i64, i64 | `t_first_us`, `t_last_us` of the records inside (-1 = none) |
| 32 | u32 | `rec_count` |
| 36 | u32 | CRC-32 of the stored payload |

Chunk types: `META` (critical, exactly one first, or one per node when MERGED): the `hdr` record as JSON. `OBSJ`
(critical): JSON Lines, the records after the header, in order. `BLOB` (ancillary): a u16 name length, the UTF-8 name,
then opaque bytes. A reader skips an unknown chunk unless its CRITICAL bit is set, in which case it stops with an error.
`NETB` is reserved for a binary packet table of the network trace.

**Chunk table** (at `index_offset`, last in the file): `SAEX`, `entry_count` u32, `entry_size` u32 (48), 0 u32; then
per chunk the u64 offset of its chunk header and a copy of the 40-byte chunk header; then the CRC-32 of everything from
`SAEX` to the last entry, and `XEAS`.

**Writing.** A writer emits the header (flags 0), the chunks as they fill (so it can stream and a crash loses at most the
open chunk), and on close the table, after which it rewrites the header with FINALIZED and the counts. **Reading.** A
reader uses the table when it is valid, and always can scan the chunks from `header_size` until `SAEX` or the end of the
file; a file the writer did not close (flags 0) is read that way, with a warning. A reader must bound its work by the
header values: it refuses a chunk whose `raw_size` exceeds its limit (default 256 MiB), never decompresses beyond
`raw_size` and checks the CRC before it trusts a payload.

`satk obs validate` checks the header CRC and version, flags against the chunks, the table against the scan, every chunk's
CRC, size and codec, the `rec_count` and time range of every OBSJ chunk, and then the records with the rules of the stream
(deep mode, the default).

## Conformance

`satk obs sample` writes a deterministic example of every file kind. `tests/obs/fixtures` of the repository holds a golden
container (`golden-plain.saerec`, uncompressed, reproduced byte for byte by the reference writer) and a compressed one.
A writer in another language is conformant when `satk obs validate --strict` passes on its output, `satk obs unpack`
gives back its records, and its reader reads both golden files.
