# Observability: validate, read, summarize and convert engine traces and bench results

[Русская версия](../ru/obs.md)

Package: `satk.obs`. Format specification: [obs-spec.md](obs-spec.md).

## What it is

The sa-engine client, server and bots write their measurements in one family of formats: JSONL streams of frame, tick,
statistics and network records, bench JSON documents, and the `.saenet` / `.saerec` containers. `satk obs` is the toolbox
for them: it checks a file against the specification, lists and filters records, summarizes a trace in a few lines,
converts the result files of `satk ingame bench` and traces into the common bench schema, merges the traces of several
nodes by their shared timeline key and packs streams into containers. It reads any file you give it and writes only under
`<workspace>\work\out\obs\`. It needs no game and no running engine.

## Quick example

```powershell
satk obs sample
satk obs validate sample/client-fixed.jsonl
satk obs summarize sample/client-fixed.jsonl
satk obs read sample/client-fixed.jsonl --rec frame --fields frame_ms ticks fence_ms.F5 --limit 5
satk obs convert sample/session.saerec --scene S0 --label rec
satk obs merge sample/client-fixed.jsonl sample/server.jsonl --out merged-demo.jsonl
satk obs validate sample/session.saerec
```

What `validate` answers for a good file (shortened):

```json
{"ok":true,"cols":["where","severity","code","message"],"rows":[],"n":0,"total":0,"verdict":"pass","errors":0,"warnings":0,"kind":"jsonl","schema":"sae-obs/1","records":368}
```

A relative name that is not a file is looked up under `work\out\obs`, where `obs sample` writes its examples. A failing
file is still a successful answer: `verdict` is `FAIL` and the rows say where and why.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk obs validate <path> [--kind auto] [--strict] [--deep false]` | `satk_op` → `obs.validate` | check a stream, a bench JSON (`sae-bench/1`, or the older `satk-bench/1`) or a container; table of problems and a verdict |
| `satk obs read <path> [--rec ...] [--t-from US --t-to US] [--tick-from T --tick-to T] [--node N] [--fields ...] [--chunks] [--chunk N] [--out FILE]` | same | records of a stream or container, stages of a bench, or the chunk table; paged; `--out` writes the matches as JSONL |
| `satk obs summarize <path> [--node N]` | same | frame-time statistics, ticks, dropped time, per-fence durations, counters, events and the last engine snapshot per node; a stage table for a bench |
| `satk obs convert <src> [--out F] [--scene S] [--label L] [--skip-s N] [--until-s N]` | same | write `sae-bench/1` from a `satk-bench/1` result (lossless) or from the frame records of a stream or container |
| `satk obs merge <paths...> [--out F]` | same | merge the streams of several nodes into one JSONL ordered by the timeline key |
| `satk obs pack <src> <out>` / `satk obs unpack <src>` | same | JSONL stream to `.saenet` / `.saerec` (kind from the extension, zlib by default) and back |
| `satk obs schema [name] [--definition D]` | same | list the JSON Schemas (`common`, `record`, `bench`, `layout`), or print one or a single definition |
| `satk obs sample [--dir sample]` | same | write the deterministic example files |

All of them are reached by agents through `satk_ops` and `satk_op`.

## How it works

- **Schemas.** `data/obs` holds the JSON Schemas of the records and of the bench document and the byte layout of the
  container. satk validates with a small built-in validator for the subset of JSON Schema they use (any external
  validator can read the same files); tests check the two agree.
- **Stream rules** that a schema cannot state are checked in one pass over the file: key order, the tick grid of the
  header, simulation-mode consistency, cumulative counters and the footer counts.
- **Containers** are validated at three depths: header and chunk table (`--deep false`), every chunk (CRC, size, codec)
  and the records inside. A container whose writer died is scanned from the start and reported with a warning.
- **Bench numbers.** The frame-statistics arithmetic is the one of `satk ingame bench`; a converted result gives the same
  `bench-compare` verdicts as the original.
- **Determinism.** `obs sample`, `pack` and `merge` write the same bytes for the same input (compressed chunks depend on the
  zlib build).

## Limitations and known issues

- `satk ingame bench` still writes `satk-bench/1`; run `satk obs convert` on its files to get `sae-bench/1`.
- The payload layouts of `net`, `input` and `snap` records are minimal: the network trace and replay packages extend them
  with new optional members.
- Codec 2 (zstd) is reserved in the container; chunks that use it are listed but not checked.
- Reading a very large trace is a single pass in Python: about a million records per few seconds. `summarize` and `convert`
  keep the frame records in memory (about 1 KB per frame), `validate` streams.

## Python API (if other packages use it)

```python
from satk.obs import schemas, stream, bench, container, key, sample
report = stream.check_file("trace.jsonl")          # Report: problems, by_code, kinds, nodes
doc = bench.convert_satk_bench(old_result)         # satk-bench/1 dict -> sae-bench/1 dict
with container.Writer("t.saerec", "SREC", dt_s_us=33333) as w:
    w.meta(header_record)
    w.add(record)
```
