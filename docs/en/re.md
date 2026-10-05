# sa-re: the `gta_sa.exe` symbol DB (address → function, source, MTA patches)

[Русская версия](../ru/re.md)

Package: `satk.re`.

## What it is

A light symbol database for the stock `gta_sa.exe` 1.0 US HOODLUM of the clean copy, built without Ghidra and
without network access. It answers "what is this address from a crash log", "where is this function in
gta-reversed" and "which MTA patches (upstream, our trunk, Neon) sit inside it". It writes only
`work\re\symdb.sqlite` (rebuilt in 10-20 s, safe to delete) and exports to `work\re\export\`.

## Quick example

```powershell
satk re build
satk re addr 0x53BF09
satk re find CPed::Update --limit 3
```

What `re addr` returns (abridged):

```json
{"ok":true,"id":"fn:0x53bee0","addr":"0x53bf09","fn":"CGame::Process","start":"0x53bee0","off":"0x29",
 "confidence":"high","bounds":"next_start","src":"game_sa/Game.cpp:78","reversed":true,
 "patches":{"cols":["addr","kind","symbol","origin","src","hit"],
            "rows":[["0x53bf09","hookinstall","HOOKPOS_CStreaming_Update_Caller","trunk","Client/multiplayer_sa/CMultiplayerSA.cpp:638",true]],
            "total":22,"raw_total":33}}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk re build [--exe PATH] [--no-trunk] [--ghidra-functions FILE]` | — | rebuild the DB from all sources |
| `satk re addr 0x53BF09 gta_sa.exe+0x13E4FA` · `--text-file crash.txt` · `-` (stdin) | `re_addr` | address or crash log → function+offset, `file:line`, thunk, patches; one address gives an object, several a table |
| `satk re find NAME [--kind func\|global\|vtable\|struct]` | `re_find` | exact name, then `::member`, prefix, substring |
| `satk re src FN [--context 30]` | `re_src` | gta-reversed lines around the hook install and the definition (show only, never copy into files) |
| `satk re patches [--fn FN \| --range 0xA-0xB] [--origin upstream\|trunk\|neon\|satk\|all] [--kind K]` | `re_patches` | MTA patches with `file:line` |
| `satk re limits [--kind pool\|array\|store\|id_range\|streaming\|world] [--match S]` | — | engine limits: vanilla / trunk / Neon |
| `satk re export ghidra\|x32dbg\|json [--out DIR]` | — | `ApplySaSymbols.py` + `symbols.json`, `gta_sa.exe.dd32`, `symbols.json` |

The SIDs `fn:`, `g:`, `vt:`, `patch:` also work through the common `asset_get` / `asset_find` / `asset_refs`:
`g:0xc8d4c0` → `gGameState` (`int32`); `asset_refs("fn:0x53bf09", rel="patches")`; `rel` for `fn:` is `patches`,
`callers`, `callees`, `vtables`.

## How it works

**Sources** (all clones under `src\` are read with `git cat-file` at a revision, without checkout,
`GIT_NO_LAZY_FETCH=1`; `git status` there stays clean):

| Source | What it gives |
|---|---|
| `src\gta-reversed\docs\hooks.json` (revision `origin/master`) | 8,025 functions: name, `file:line`, reversed/locked |
| gta-reversed `source/**` | 2,345 globals `StaticRef<T>(0x…)` with types and array lengths, 385 vtables, 769 struct sizes, pools and stores |
| plugin-sdk `plugin_sa/game_sa` (`HEAD`) | 1,631 more function names (the whole RenderWare API), globals, 5,972 `RefList` call sites |
| `gta_sa.exe` | sections, thunks (`.HOODLUM` 500, `push -1; jmp` 87, `jmp` 195), `call` targets, function pointers, vtable slots |
| MTA: `src\mtasa-neon` (`upstream/master` and Neon `HEAD`), `engine\mtasa` (trunk) | `HOOKPOS_*`, `HookInstall*`, `MemPut/MemSet/MemCpy`, Neon relocation manifests; limits from `engine\mtasa\docs\limits.toml` |
| optional: a Ghidra export `functions.jsonl` (`--ghidra-functions`) | exact function bounds, including split bodies |

**Address → function.** `gta_sa.exe+0x…` is turned into a VA (base 0x400000). In an MTA crash dump the parser takes
`Offset =` (together with `Module =`), `(IDA: 0x…)` and `EIP=`; values of other registers are ignored. An address in
`.HOODLUM` belongs to the function whose thunk jumps to the nearest body below the address (`in_hoodlum: true`); an
address in a body moved by `push -1; jmp` belongs to the owner of the thunk (`via_thunk`). Otherwise the nearest
known start below the address is taken (about 20,800 starts: named ones plus vtable slots, `call` targets, pointers
from `.rdata/.data`, addresses after `ret` and padding).

**`confidence`:** `high` = a named start with no unnamed start between it and the address; `medium` = there is an
unnamed start in between (then `fn` = `sub_<address>` and the named one goes to `alt`) or the body was moved; `low` =
no named starts; `exact` = bounds from Ghidra (`"bounds":"ghidra"`, the `ranges` field lists the pieces of the body).
An honest `medium` example: `0x41B1D0` → `sub_41b1d0`, `alt: CCollision::CameraConeCastVsWorldCollision+0x1d0`.

**Ghidra bounds.** `satk re build --ghidra-functions work\re\ghidra\export\functions.jsonl` takes exact bounds
from a Ghidra analysis export (Ghidra itself is not needed at build time; an adjacent `summary.json`, when present,
must name the same exe hash). Names still come from the source scanners. With the export, `0x53BF09` is
`CGame::Process` with `confidence: exact` and two ranges, and `0x41B1D0` becomes an exact `sub_41b1d0`.

**Patches in the answer:** first those covering the address (`hit: true`), then by address; upstream rows that trunk
repeats are hidden (trunk = upstream + our commits). The full list: `satk re patches`. The origin `satk` is
reserved for our own patches.

## Limitations and known issues

- Without a Ghidra export, function bounds are heuristic ("until the next start").
- MTA patches with a computed address (`MemPut(pAddr + i, …)`) do not get into the DB; their number is in
  `satk re build --json` (`stats` from the DB `meta`, key `patch_scan.*.unresolved`).
- Addresses in other modules (`core.dll+0x…`) are returned as is.
- If `satk re addr` answers `NOT_READY`, the DB is not built: `satk re build`.
- The DB needs the 1.0 US address layout; with another exe `satk re addr` adds the warning `UNSUPPORTED_EXE`
  (`satk game info` shows the layout).
- Reference numbers and control addresses: `tests/golden/re.json`; check with `pytest tests/re -m game` (builds into
  a temporary folder, about 10 s). Other answers are in [troubleshooting.md](troubleshooting.md).

## Python API (for other packages)

```python
from satk.re.db import open_db
from satk.re import api

db = open_db()                                   # NOT_READY when there is no DB
d = api.addr_detail(db, 0x53BF09)                # dict: fn, off, src, confidence, patches...
loc = db.amap.locate(0x1566830)                  # Location(start, off, in_hoodlum, via_thunk, ...)
```
