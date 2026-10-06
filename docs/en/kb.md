# kb: knowledge base of GTA:SA internals - functions, structures, opcodes, facts

[Русская версия](../ru/kb.md)

Package: `satk.kb`.

## What it is

A local SQLite database with full-text search (FTS5) over sources that are already in `<workspace>\src`:
gta-reversed, plugin-sdk, MTA (`Client/game_sa`, `multiplayer_sa`, `sdk/game` from upstream, and separately the
Neon files that differ from it), the cleo-ai opcode reference, optional Markdown notes in
`<workspace>\docs\research` (skipped when the folder does not exist), 40 checked facts about the engine and 16
authoring facts (`asset.*`: what a new model must follow). It
answers questions like "where is this function defined and what is its signature", "how big is this class and what
is at this offset", "what does this opcode do", "how many slots does this pool have". It writes only `work\kb\kb.sqlite` (~83 MB, rebuilt in 30-60 s, safe to delete). Source code in
answers is only lines for display; it never goes into the repository or into files.

## Quick example

```powershell
satk kb build
satk kb search "CStreaming RequestModel" --limit 3
satk kb struct CPed --at 0x540
satk kb opcode 0A8C
satk kb fact streaming.memory
satk kb fact asset
satk kb sym eCarPiece
```

What comes back (shortened):

```json
{"ok":true,"cols":["kind","name","loc","src","info"],
 "rows":[["func","CStreaming::RequestModel","source/game_sa/Streaming.cpp:1377","gta-reversed",
          "0x4087E0 void CStreaming::RequestModel(int32 modelId, int32 streamingFlags) // Request a given model to be loaded. ..."]],
 "n":3,"total":16,"next":"o:3"}
{"ok":true,"name":"CPed","kind":"class","src":"gta-reversed","loc":"source/game_sa/Entity/Ped/Ped.h:111","size":"0x79C","calc":"0x79C",
 "layout":"ok","bases":"CPhysical","sizes":{"plugin-sdk":"0x79C","mta-upstream":"0x79C (CPedSAInterface)"},
 "fields":{"cols":["off","name","type","size","via"],"rows":[["0x540","m_fHealth","float",4,"plugin-sdk"]],"total":1}}
{"ok":true,"op":"0A8C","name":"WRITE_MEMORY","ext":"CLEO","descr":"Writes the value at the memory address",
 "input":["address: int","size: int","value: any","vp: bool"],"handler":"WriteMemory (source/game_sa/Scripts/Commands/CLEO/CLEOMemoryCommands.cpp:11)"}
```

`satk kb build` itself answers with counts (`sym` 68 513, `struct` 3 354, `opcode` 3 739, `fact` 40, ...), the fact
checks (`verified` 40) and the layout summary per source.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk kb build [--no-research]` | - | rebuild the database from all sources (read-only git, no network) |
| `satk kb search "words" [--source S] [--kind sym\|code\|opcode\|fact]` | - | search: symbols with `file:line`, facts, opcodes, source lines; an address such as `0x5B8E64` finds every symbol and line that mentions it |
| `satk kb sym NAME [--kind K] [--source S]` | - | a symbol by name (exact, `::member`, prefix, substring) or address; kinds `func global struct vtable limit const enum define hookpos`; an enum type name (`eCarPiece`) lists its members in order |
| `satk kb struct NAME [--at 0x540] [--match S] [--inherited] [--bits] [--source S]` | - | size (declared and computed), bases, fields with offsets; `--at` finds the field at an offset, going into bases and nested structures |
| `satk kb opcode 0A8C` · `WRITE_MEMORY` · `"car coordinates"` `[--ext E]` | - | an opcode: parameters, description, the handler in gta-reversed |
| `satk kb fact [KEY\|topic\|words] [--status mismatch]` | - | 40 engine facts (pools, streaming, world, scripts, sizes) with a confidence level and checks; `asset` lists the 16 authoring facts (works without a built database) |

Sources (`--source`): `gta-reversed`, `plugin-sdk`, `mta-upstream`, `mta-neon`, `cleo-ai`, `research`, `facts`.
MCP clients reach these operations through the generic tools `satk_ops`/`satk_op` (they are `mcp=False`).

## How it works

- **Reading the sources.** The clones are read with `git cat-file` at a revision (`gta-reversed` at
  `origin/master`, MTA at `upstream/master` and Neon at `HEAD`), without checkout and without fetching
  (`GIT_NO_LAZY_FETCH=1`): `git status` in `src\` does not change. The revisions go into the `source` table;
  `satk status --deep` (section `kb`) shows sources that moved ahead after the build.
- **Functions.** `<workspace>\src\gta-reversed\docs\hooks.json` is joined with the definitions in `.cpp` files (the
  `// 0xADDR` comment above a function, the signature, the first comment line) and the declarations in classes.
  Other definitions, with or without an address, are symbols too. Plus plugin-sdk names, MTA
  `#define FUNC_/VAR_/HOOKPOS_...`, `StaticRef` globals, vtables, limits, constants and enum values (part of them
  comes from the `satk re` scanners).
- **Structures.** Class fields are parsed from the headers; offsets follow the 32-bit MSVC rules (alignment,
  `#pragma pack`, vptr, bases, bit-fields, unions, simple templates). Offsets from `VALIDATE_OFFSET`/`offsetof` of
  the same source are anchors (`via` = `assert`). For a gta-reversed class, plugin-sdk offsets with the same field
  name confirm the computation (`via` = `plugin-sdk`) or flag a difference; they replace the computation only when
  it does not match the declared size itself. `layout`: `ok` means the computed size equals the declared one
  (`VALIDATE_SIZE`/`static_assert`), then `mismatch`, `unverified` (no declared size), `partial` (a type of unknown
  size). Now 743 of the 761 gta-reversed classes with a declared size are `ok` (2 `mismatch`, 16 `partial`);
  plugin-sdk has 391 `ok` and not a single `mismatch`.
- **Opcodes** come from `<workspace>\src\cleo-ai\reference\opcode-index.md` and the detailed sections next to it
  (Sanny Builder Library data); the handler from `REGISTER_COMMAND_HANDLER` in
  `<workspace>\src\gta-reversed\source\game_sa\Scripts\Commands`.
- **Facts** (`satk.kb.facts`): 40 curated facts. Confidence levels: `code` (gta-reversed code), `2src`
  (a second independent source), `exe` (bytes of `gta_sa.exe`). Every build checks them again against the database
  and the bytes of the clean `gta_sa.exe` (read-only): `verified` / `mismatch` / `unchecked`.
- **Authoring facts** (`asset.*`, `ASSET_FACTS` of `satk.kb.facts`): lamp and paint colour keys, wheel size,
  COL sphere piece byte, no mipmaps on vehicle textures, `ug_*` tuning frames, door/bonnet/boot hinges, glass,
  normals, dirt levels, frame tables, the big-building rule (> 300), COL face light, IDE flag bits, the 50 MiB
  streaming budget and the stock store capacities. Each one names what checks it (`verify`); the test-suite
  checks all of them against the vanilla index, the clean game files and `gta_sa.exe`, so their status is
  `tested`. They are served from the package (not stored by `satk kb build`).
- **Search.** FTS5 `unicode61` with `_` inside words: every query word is a prefix, `camelCase` is split into words
  (`InfoForModel` finds `ms_aInfoForModel`). Order: exact name, facts, opcodes, symbols, code lines (at least a
  third of the page). Addresses in the text (`0x05B8E55` too) are kept in `addr_ref`.
- The database is written to a temporary file next to it and swapped in atomically; queries open it read-only and
  close it right away.

## Limitations and known issues

- `loc` is the path inside the source repository at the revision the database was built from. For `mta-upstream`
  the working copy `src\mtasa-neon` is Neon, so view the upstream line with
  `git -C <workspace>\src\mtasa-neon show upstream/master:<path>`.
- The layout is a heuristic without a compiler: complex templates, `#ifdef` and macros give `partial`/`mismatch`;
  check the offsets with `via` = `calc` of such classes against `VALIDATE_OFFSET` or a disassembler.
- Signatures and comments are one-line excerpts; the full gta-reversed code of a function is `satk re src`.
- A database built before the facts were translated still holds the old texts for `satk kb search`; `satk kb fact`
  already shows the current English text. `satk kb build` refreshes the search.
- gtamods and forums are not mirrored; SilentPatch, OLA and CrashInfo are not in the database yet.
- `NOT_READY`: the database is not built or was built by an older schema version: `satk kb build`.

## Python API (if other packages use it)

```python
from satk.kb import query

query.search("CStreaming RequestModel")          # a table, like the CLI
query.struct("CPed", at="0x540")                 # an object with fields
query.sym("0x8A5A80")                            # symbols at an address
query.enum_members("eCarPiece")                  # [(name, value), ...] in declaration order
query.func_location("CAutomobile::PreRender")    # file:line of a function in gta-reversed / plugin-sdk
```
