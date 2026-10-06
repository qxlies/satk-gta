# satk error codes and warnings

<!-- English only. Source of truth: src/satk/core/errors.py (ERROR_CODES, a frozen contract). Examples are real
     envelopes; re-checked 2026-10-06 (0.2.0 preview; the operations added after 0.2.1 too). -->

Every failure is the envelope

```json
{"ok":false,"error":{"code":"NOT_FOUND","msg":"no model 'infernos'","hint":"satk asset find infernos --kind model","did_you_mean":["model:infernus","model:info"]}}
```

`msg` is a short English sentence, `hint` is a command or action that likely fixes it (not every error has
one), `did_you_mean` lists close SIDs/names, `data` (optional) holds small machine-readable details: the valid
SID kinds, the real parameter names, a table of violations (`cols`/`rows`). The same codes are used by the CLI,
the MCP server (as a tool result with `isError`), the generic tool `satk_op` and SAAP/1 endpoints.

## CLI exit codes

| exit | meaning |
|---|---|
| 0 | success |
| 1 | error (any code not listed below) |
| 2 | bad arguments (`BAD_PARAMS`) |
| 3 | not ready (`NOT_READY`, `INDEX_MISSING`, `DEPENDENCY`) |
| 130 | interrupted (Ctrl+C) |

## Codes, with real examples

| code | exit | when | what to do |
|---|---|---|---|
| `BAD_ID` | 1 | not `kind:key`, unknown kind, malformed key | fix the SID; `data.valid_kinds` lists the kinds |
| `BAD_PARAMS` | 2 | unknown/missing parameter, bad enum, SQL error, unknown profile, `out` outside `work` | `data.params` / `data.profiles` show valid values; `satk <cmd> -h` |
| `NOT_FOUND` | 1 | no object with that SID/name, no file, no project | `did_you_mean`; search with `asset_find` / `re_find` |
| `AMBIGUOUS` | 1 | a name matches several objects | use a full SID |
| `NOT_READY` | 3 | viewer not running or minimized, symdb or knowledge base not built | run the `hint` command |
| `INDEX_MISSING` | 3 | no index file for the profile | `satk index build --profile <profile>` (~8 s) |
| `READ_ONLY` | 1 | a write through `index_query` | only SELECT/WITH |
| `PROTECTED_PATH` | 1 | a write into the original install, the donor sources or (outside `game.*`) the clean copy | write under the work directory; never bypass |
| `EXISTS` | 1 | destination exists (`satk game clone --dst` not empty, a workspace copy edited) | choose another destination / merge the edit |
| `TIMEOUT` | 1 | an operation or endpoint call exceeded its time; a studio step ran past its budget (`blender.call` 90 s, `blender.methods` 60 s, preview 60 s, cold preview 300 s) and the session is still usable | smaller request; split the step (`timeout` raises the budget); retry; check the viewer window |
| `UNSUPPORTED` | 1 | the target lacks a capability, satk cannot start it, or `satk_op` refuses a CLI-only operation | check `caps` from `view_control(action="status")`; CLI-only: ask the user to run it |
| `DEPENDENCY` | 3 | an optional pip package is missing (Pillow, numpy, mcp) | ask the user: installing it needs the network |
| `EXTERNAL_TOOL` | 1 | Blender, MSBuild, premake, an AI client's CLI (`claude mcp add`), a subprocess or a quick-example command failed; a Blender session that died (the message has the path of Blender's crash report and the command that resumes it) | open the log path from `msg`/`hint`/`data` |
| `CONSENT_REQUIRED` | 1 | the action needs explicit user consent (downloads, first MTA client run, publishing; through `satk_op`: setup, game copy writers, deleting notes, installing skills) | ask the user in chat; never add `--i-have-consent` on your own |
| `AUTH` | 1 | SAAP: wrong/missing token in `hello` | restart the target through satk (fresh session file) |
| `PROTOCOL` | 1 | SAAP: malformed frame, size limit exceeded | bug in client or endpoint; check `work/logs` |
| `UNKNOWN_METHOD` | 1 | SAAP method or MCP tool name unknown | check `caps`; `did_you_mean` |
| `BUSY` | 1 | the endpoint / a build is busy with another request; a Blender session still runs a step that cannot be interrupted (`blender.session status` shows it under `busy`) | retry after the current call; a stuck session: `blender.session stop`, then start it again (the newest checkpoint reopens) |
| `REVISION` | 1 | stale `expect_rev` (SAAP); `game verify` found differences; generated/published docs or an AI client config out of date (`--check`); a pinned download changed | re-read the state; regenerate/sync; never `--update-pins` without the user |
| `ASSET_GUARD` | 1 | `satk dev assetguard`: a file looks like a game asset or forbidden code | unstage it; synthetic fixtures go to `.assetguard-allow` with sha256 |
| `CHECK_FAILED` | 1 | a check found failures: a `batch` input, a `recipe.run` step, a check run with `fail_on` (`asset.lint`, `col.check`), the dev gate | read the rows (they name the failed inputs/steps); `batch.report` pages a batch's failures |
| `INTERNAL` | 1 | unexpected exception (a bug); also `satk mcp selftest` failures | the hint points to the traceback in `work/logs/errors.log`; report it |

Real envelopes (MCP tool calls; the paths in them are refused on purpose):

<!-- linkcheck: off -->
```text
asset_get(id="infernus")
  {"ok":false,"error":{"code":"BAD_ID","msg":"not a SID (expected kind:key): 'infernus'","hint":"satk help ids","data":{"valid_kinds":["model","dff","txd",…],"input":"infernus"}}}
asset_get(id="tex:ws_rooftarmac1")
  {"ok":false,"error":{"code":"BAD_ID","msg":"key must look like txd/texture, got 'ws_rooftarmac1'","hint":"satk help ids",…}}
asset_find(q="grove")
  {"ok":false,"error":{"code":"BAD_PARAMS","msg":"asset.find: unknown parameter 'q'","hint":"parameters: satk_help(\"asset.find\") or satk_ops(\"asset.find\")","data":{"params":["query","kind","limit","cursor","profile"]}}}
asset_find(query="grove", profile="nosuch")
  {"ok":false,"error":{"code":"BAD_PARAMS","msg":"unknown profile 'nosuch'","data":{"profiles":["installed","samp","vanilla"]}}}
index_query(sql="DELETE FROM meta")
  {"ok":false,"error":{"code":"READ_ONLY","msg":"the index is read-only: only SELECT is allowed (got DELETE)","hint":"SELECT ... FROM v_model / v_inst / v_tex / v_file"}}
index_query(sql="SELECT * FROM nosuch")
  {"ok":false,"error":{"code":"BAD_PARAMS","msg":"SQL error: no such table: nosuch","hint":"satk index query \"SELECT name, sql FROM sqlite_master\""}}
view_capture(target="ariane", marks=4)            # viewer not started
  {"ok":false,"error":{"code":"NOT_READY","msg":"target 'ariane' is not running","hint":"satk view start --target ariane","data":{"target":"ariane"}}}
view_control(action="start", target="game")      # the user starts the game target
  {"ok":false,"error":{"code":"UNSUPPORTED","msg":"satk cannot start target 'game' (start it yourself)"}}
satk_op(op="dev.gate")
  {"ok":false,"error":{"code":"UNSUPPORTED","msg":"dev.gate is not callable through satk_op: the acceptance gate runs the whole test-suite for minutes","hint":"CLI only: run `satk dev gate` in a terminal","data":{"op":"dev.gate"}}}
asset_find(query="infernus", kind="handling")
  {"ok":false,"error":{"code":"BAD_PARAMS","msg":"kind: 'handling' is not one of model, dff, txd, tex, col, ipl, zone, ifp, anim, file, note, fn, g",...}}
re_src(fn="CNoSuch::Fn")
  {"ok":false,"error":{"code":"NOT_FOUND","msg":"no function 'CNoSuch::Fn'","hint":"satk re find CNoSuch::Fn"}}
blender_job(cmd="render", args={})
  {"ok":false,"error":{"code":"BAD_PARAMS","msg":"--blend is required","hint":"satk blender import-area ... returns files.blend"}}
engine(cmd="build", args={"project":"nosuch"})
  {"ok":false,"error":{"code":"NOT_FOUND","msg":"no project 'nosuch' in MTASA.sln","hint":"aliases: server, client, all, changed","did_you_mean":["Launcher"]}}
satk_op(op="script.asm", args={"file":"mycar/mycar.txt"})   # a typo on line 8
  {"ok":false,"error":{"code":"BAD_PARAMS","msg":"1 error(s) in mycar.txt; line 8: opcode 0001 is wait, not 'wiat'","hint":"fix the lines listed in error.data.errors (satk script check shows them with warnings)","did_you_mean":["wait","switch"],"data":{"errors":["line 8: opcode 0001 is wait, not 'wiat'"],"total":1}}}
satk_op(op="kb.mta", args={"query":"engineRequestModel"})   # kb built before the scripting API tables
  {"ok":false,"error":{"code":"NOT_READY","msg":"the knowledge base has no scripting API tables (built by an older satk)","hint":"satk kb build"}}
asset_export(id="model:411", out="<workspace>/gta-sa-clean/x")
  {"ok":false,"error":{"code":"BAD_PARAMS","msg":"--out must be inside the work directory <workspace>/work","hint":"omit --out (default <workspace>/work/out/models/infernus)"}}
```
<!-- linkcheck: on -->

Not an error: `re_addr(text=["0x12345"])` → `{"ok":true,"addr":"0x12345","module":"gta_sa.exe","confidence":"none","note":"address outside the gta_sa.exe image"}`.
Usually this is a crash-dump `Offset` (module-relative): retry as `gta_sa.exe+0x12345` (= `0x412345`).
A crash dump squeezed into one line (`Module = … Offset = 0x…`) resolves to module `?`: keep its line breaks.

## Warnings (`"warn":[...]` of a successful answer)

Format `CODE: text`. The answer is still valid; the warning tells what was cut, guessed or stale.

| warning | from | meaning / action |
|---|---|---|
| `MORE` | `asset_find` | only exact names returned; N more names contain the query → `asset_find(query="*q*")` |
| `LIMIT` | `index_query` | more rows than `limit` → add LIMIT/OFFSET or raise `limit` |
| `TRUNCATED` | index, media, blender | a list/file set was cut (64 PNGs per call, placements per area) |
| `PAGES` | `texture_image` | more than 64 textures → several sheets (max 4) |
| `INDEX_STALE` | index queries | DAT/IDE/IPL/IMG changed after the build → `satk index build` |
| `INDEX_MISSING` | status, viewer, blender | no index: the viewer uses `FakeIndexDB`, Blender parses the game directly |
| `FALLBACK` | `model_image` | the requested backend (ariane/blender) failed; the soft renderer drew it |
| `NO_DFF` / `TEX_MISSING` | `model_image` | a model without DFF (grey placeholder) / textures not found |
| `NO_TXD` / `MODELS_SKIPPED` | Blender planning | models imported without textures / skipped |
| `EXTRA_FILES` / `MANIFEST_DIFFERS` / `NO_MANIFEST` | `game verify` | extra files in the copy / `MANIFEST.sha256` differs / missing |
| `DUP_ID` / `DUP_IPL` / `BAD_IMG` | `index build` | duplicate IDE IDs or IPL names, an unreadable IMG (modded installs) |
| `GAME_MISSING` / `OPS_IMPORT` | `satk_status` | no game copy / a package's operations failed to import (see `satk doctor`) |
| `NO_DXFILES` / `NO_CLIENT_DEPS` / `DONOR_CHANGED` / `NON_LOOPBACK` | `engine` | client build deps missing (consent D3) / donor clone moved / server bound outside 127.0.0.1 |
| `NO_FULL_MATCH` | `satk_ops` | no operation matches every query word; the closest ones are shown |
| `UNVERIFIED` / `DUMP_DAMAGED` | `crash.*` | stack frames marked `?` were not checked against a CALL / a damaged stream of the dump was skipped |
| `IMPORTED` | `paths.near`, `paths.node` | the path database was imported on first use |
| `TXD_PENDING` | `blender.game_ready` | textures left as PNG (no TXD writer available) |
| `UNSUPPORTED_EXE` | `game.*` | the exe is not 1.0 US: assets work, addresses (RE, crash) do not |
| `ADDON_VEHICLE` / `NEW_HANDLING_ID` / `NEW_WEAPON_TYPE` | `mod.add` | the add-on needs fastman92 LA (an id over 611, a handling or weapon type the stock engine does not know): tell the user |
| `WEAPON_TYPE` / `CARGRP_FULL` / `GXT_KEY` / `NO_COL` / `NO_INDEX` | `mod.add` | the donor's weapon type switches to the new model / the engine reads only 23 models per car group / another GXT key was chosen / no collision / free ids guessed from IDE files |
| `UNDECODED` / `KEPT` / `OPDB` / `SOURCE` | `script.*` | bytes that are not code (kept as `hex`) / an existing different text was kept (written as `<name>.disasm.txt`) / the opcode db fell back to the built-in core subset / an argument is a global, local or literal where the opcode db expects another kind (assembled anyway) |
| `LOW_PSNR` / `KEPT_WHOLE` / `SHAREABLE` / `OVER_LIMIT` | `texture.optimize`, `texture.budget` | a texture fell below 30 dB / a TXD was left whole (no model loads it) / identical textures could move into a shared TXD (`share`) / over the 50 MiB streaming memory |
| `OVER` / `JOBS` / `MAX_FAIL` | `batch` | how `over` was resolved (e.g. matched under the game root) / the op is not marked thread-safe, inputs ran one by one / stopped after `max_fail` failures |
| `SKIPPED` | `kb.build` | a part was not built (no SA-MP/open.mp include files: set `kb.pawn_include`) |
| `NOTE` | `col.gen` | a hint about the chosen mode (spheres cover thin bodies poorly: try hull or boxes) |
| `OVER_BUDGET` | `satk dev workflow-cost` | a documented workflow needs more calls than its budget |
| (free text) | viewer | e.g. `capture size is the window size 1280x720 (target has no capture.size)`, `mock endpoint not running: using the in-process mock` |
