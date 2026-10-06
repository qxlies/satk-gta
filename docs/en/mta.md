# `satk mta`: MTA:SA developer tools (lint, starter resources, model packs, logs)

[Русская версия](../ru/mta.md)

Package: `satk.mta`.

## What it is

Tools for people who write MTA:SA resources. `satk mta lint` reads a resource folder (its `meta.xml` and every
script) and finds what would break on a server before you upload it: Lua syntax errors with the same message the
MTA server prints, MTA functions that do not exist or run only on the other side, deprecated functions, wrong
arguments, events nobody adds, render-loop and OOP mistakes, missing files and the size players have to
download. `satk mta resource new` writes a starter resource that already follows the usual conventions;
`satk mta pack` turns a folder of DFF/TXD/COL files into a resource that loads them (stock id replacement or
new ids); `satk mta logs` turns `server.log`, `clientscript.log` or `/debugscript` text into rows with a hint;
`satk mta server-check` lets the real MTA server load a resource (on 127.0.0.1, no game client).

Generated resources default to `<workspace>\work\out\mta\<name>\`; satk never writes into a server or the game.
Copy a resource into `<server>\mods\deathmatch\resources\` yourself.

## Quick example

```powershell
satk mta resource new my-panel --force
satk mta lint my-panel
satk mta resource new my-cars --kind vehicle-pack --force
satk mta lint my-cars --severity warn
```

What comes back (shortened):

```json
{"ok":true,"dir":"<workspace>/work/out/mta/my-panel","kind":"script","files":["client.lua","meta.xml","server.lua","shared.lua"],"lint":{"clean":true},"install":"copy the folder my-panel into <server>/mods/deathmatch/resources/ and 'start my-panel' in the server console (satk never writes into a server or the game)","next":"satk mta lint my-panel"}
{"ok":true,"cols":["sev","at","code","msg"],"rows":[],"n":0,"total":0,"next":null,"resource":"my-panel","scripts":3,"sides":["client","server"],"clean":true,"download_kb":1,"ref":{"source":"kb","functions":1861,"events":247,"mta":"1.7.0"}}
```

A resource with problems gives rows like these (`satk mta lint broken --limit 4`):

```json
{"ok":true,"cols":["sev","at","code","msg"],"rows":[
 ["error","meta.xml:6","FILE_MISSING","<script src=\"missing.lua\">: file not found (MTA: Couldn't find file(s) missing.lua)"],
 ["error","client.lua:6:17","EVENT_WRONG_SIDE","'onPlayerJoin' is a server event: it never fires in a client script"],
 ["error","client.lua:15:1","UNKNOWN_FUNCTION","outputChatBoxx is not an MTA:SA client function and is not defined in this resource: did you mean outputChatBox?"],
 ["warn","client.lua:3:65","RENDER_HEAVY","dxCreateFont in an onClientRender handler: creates a font every frame (memory grows until the client runs out of video memory)"]],
 "n":4,"total":35,"next":"4","counts":{"error":15,"warn":20,"info":4},"clean":false}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk mta lint PATH [--side S] [--severity warn] [--codes A,B] [--budget-mb 20] [--ref R]` | — (`satk_op`) | checks a resource folder, a resource `.zip`, its `meta.xml` or one `.lua`; table `sev/at/code/msg`, `counts`, `clean`, `download_kb` |
| `satk mta resource new NAME [--kind K] [--oop] [--model M --pos X Y Z] [--texture T] [--force]` | — | a starter resource: `script`, `map`, `shader`, `vehicle-pack`, `skin-pack`, `object-pack` |
| `satk mta pack SRC [--kind vehicle\|skin\|object] [--new-id] [--replace ID ...] [--parent ID] [--lod M] [--name N]` | — | a resource that loads custom models; download report with per-file sizes and advice |
| `satk mta logs FILE [--level error\|warning\|info\|all] [--resource R] [--grep TEXT] [--no-group]` | — | log lines as `line/level/res/at/msg/n/hint`, repeated lines merged, `by_resource` counts |
| `satk mta server-check PATH [--wait 3]` | — | the built MTA server loads and starts the resource on 127.0.0.1; its messages about it |
| `satk mta ref [--out FILE]` | — | the MTA reference the checks use; `--out` writes it as JSON for `--ref FILE` |

`--ref` picks the MTA reference: `auto` (the knowledge base from `satk kb build`, else the MTA source tree), `kb`,
`source`, `none` (only the Lua checks) or a JSON file from `satk mta ref --out`. Relative names (`my-panel`) are
also looked up in `work\out\mta\`.

## What `satk mta lint` checks

| Code | Severity | What it means |
|---|---|---|
| `SYNTAX` | error | the script does not compile; the message is the one the MTA server prints (`'end' expected (to close 'function' at line 3) near '<eof>'`), with a hint for Lua 5.2+ syntax (`goto`, `//`, `!=`, `continue`) |
| `UNKNOWN_FUNCTION` | error | a called global is not an MTA function and the resource never defines it; `did you mean` |
| `WRONG_SIDE` | error | a client-only function (or `localPlayer`) in a server script, or the other way round; in a `shared` script inside a function it is `info` |
| `DEPRECATED` / `REMOVED` / `MIN_VERSION` | warn / error / warn | the MTA server's own upgrade list: the replacement, a removed function, behaviour that depends on `<min_mta_version>` |
| `NEON_ONLY` | warn | the function exists only in the MTA Neon fork |
| `ARG_COUNT` / `ARG_TYPE` / `ENUM_VALUE` | warn | too few or too many arguments, a literal of the wrong type, a string the function does not accept (`engineRequestModel('vehical')`) |
| `EVENT_WRONG_SIDE` / `EVENT_UNKNOWN` / `EVENT_NOT_REMOTE` | error / warn / warn | a client event in a server script, an event nobody adds, `triggerServerEvent` without `addEvent(name, true)` on the server |
| `OOP_DISABLED` | error | `localPlayer.position`, `Vehicle(...)` without `<oop>true</oop>` (vectors and matrices always work) |
| `RENDER_HEAVY` | warn | `dxCreate*`, `engineLoad*`, `getElementsByType`, chat output ... every frame in an `onClientRender` handler |
| `HANDLER_CALLED` | warn | `setTimer(f(), ...)`, `addEventHandler(..., f())`: the result is passed, not the function |
| `DRAW_OUTSIDE_RENDER` / `RESOURCE_START_ROOT` | warn / info | `dxDraw*` at the top level; `onClientResourceStart` on `root` (fires for every resource) |
| `UNDEFINED_GLOBAL` / `IMPLICIT_GLOBAL` / `DUPLICATE_FUNCTION` | warn / info / warn | a global never assigned on that side, a global that should be `local`, one global function defined twice |
| `LUA_FIELD` / `MISSING_LIBRARY` / `DISABLED_FUNCTION` | error | `math.round`, `table.unpack` (Lua 5.2), `io.*`, `require`, `dofile`, `os.execute` |
| `ELEMENT_TYPE` / `NOT_EQ` / `ESCAPE_52` / `ENCODING` | warn | `getElementType(x) == "Player"`, `not a == b`, `"\x41"` in Lua 5.1, a script that is not UTF-8 |
| `META_*`, `FILE_MISSING`, `BAD_PATH`, `SCRIPT_TYPE`, `DUPLICATE_FILE`, `EXPORT_MISSING`, `FILE_INVALID` | error / warn | `meta.xml`: XML errors, missing files, paths MTA rejects, unknown script types, exports nobody defines, broken PNG/TXD/DFF/COL files |
| `DOWNLOAD_SIZE` / `FILE_SIZE` / `NOT_LISTED` / `UNKNOWN_TAG` | warn / info | what players download against the budget, big files, `.lua` files not in `meta.xml`, tags MTA ignores |

A comment `-- satk:ignore` (or `-- satk:ignore CODE1 CODE2`) silences the findings of its line;
`-- satk:ignore-file CODE` silences a code in the whole file.

`RENDER_HEAVY` follows directly named helpers, including helpers in other scripts on the same side. A call
inside an `if` or a short-circuit expression can still run every frame. Persistent lazy caches such as
`font = font or dxCreateFont(...)` and `if not font then font = dxCreateFont(...) end` are recognised;
a `local font` created inside the handler is a fresh variable each frame and does not count as a cache.

## Model packs

<!-- docs-smoke: skip needs your model files -->
```powershell
satk mta pack mods/cars --kind vehicle
satk mta pack mods/cars --kind vehicle --new-id --name my-cars
satk mta pack mods/house.dff --kind object --new-id --lod 300
satk mta pack models.json --kind skin --replace 7 --replace 9
```

Input is a directory (searched recursively), one `.dff`, or a JSON list (also accepted as `{"models": [...]}`).
Paths in JSON are relative to the list file; `name`, `txd`, `col`, `replace`, `parent` and `lod` are optional:

```json
[{"name":"crate","dff":"models/crate.dff","txd":"models/shared.txd","col":"models/crate.col","parent":1337,"lod":300}]
```

Directory discovery prefers matching TXD/COL files beside their DFF, then unambiguous matches elsewhere in
the input. Without a matching TXD, a single TXD beside the DFF (or in the whole input) can be shared. Shared
source files are copied once, and distinct files with colliding names receive distinct, portable output
names. Missing files, duplicate model names and invalid values are rejected before the resource is written.
Resource names use 1–64 ASCII letters, digits, `_` or `-`, excluding Windows device names such as `CON`.

- **Replace** (default): every model replaces a stock id. The id comes from `--replace` (one per model, in name
  order, including JSON input: `411`, `model:411` or `model:infernus`), from the JSON list, or from the file name
  when it is a stock model name (`infernus.dff` replaces 411; needs `satk index build`). Replacement ids must be
  distinct and compatible with the selected kind.
- **New ids** (`--new-id`, MTA 1.6): each client calls `engineRequestModel(type, parent)`. The server creates
  elements with the parent model and the element data `satk:<resource>:model` (`MODEL_DATA_KEY` in `models.lua`).
  Clients switch matching elements to the new id at startup, on stream-in and when that data changes. Skin
  packs handle both peds and players, including the local player. Each pack has its own data key, so identical
  model names in different packs do not collide. Other resources use
  `exports["my-cars"]:createPackVehicle(name, x, y, z)` (skins: `setPackSkin(ped, name)`, objects:
  `createPackObject(...)`). The install hint names the admin test command. `--parent` requires `--new-id`.
- Files load in the order COL -> TXD -> DFF; `--lod` sets `engineSetModelLODDistance` (objects with new ids get
  300 m). `download.bytes` is the exact total for assets and downloaded scripts (`models.lua`, `client.lua`),
  counting shared files once. The report shows the 20 largest files, compares the total against `--budget-mb`,
  and gives advice (`satk texture audit` / `satk texture optimize` for big textures). Budgets and LOD distances
  must be finite and positive. Files are copied as they are: satk never encrypts or obfuscates them.

## Logs and the server check

<!-- docs-smoke: skip needs a server log and a built MTA server -->
```powershell
satk mta logs server.log --level error
satk mta logs clientscript.log --resource my-panel
satk mta server-check my-panel
```

`satk mta logs` reads the last 32 MB of `server.log`, `clientscript.log`, a saved server console or text copied
from `/debugscript`. Rows are merged when the same message repeats (`[DUP x12]` counts too) and sorted errors
first. Hints: `Bad argument @ 'setElementPosition'` gets the function's arguments, `attempt to call global 'x'`
gets the side of `x` or a spelling suggestion, missing files and ACL errors get the fix.
When a large log is truncated, a partial first line is discarded and `line` is `0` because its absolute log
line number is unknown; the script location in `at` is preserved. If the reference cannot be loaded, a warning
explains that only built-in hints are available, while parsed messages are still returned.

`satk mta server-check` needs the MTA fork's server (`satk engine build --project server --platform x64`). It
copies the server and the resource into `work\mta\check\`, starts it headless on 127.0.0.1 with free ports and
no other resources, types `start <name>` on its console, waits `--wait` seconds and shuts it down. `started`
says whether the resource runs; the rows are the server's own messages (load failures, server script errors,
deprecation warnings). Client scripts do not run there (no game client): `satk mta lint` checks them.

## How it works

- **Lua 5.1, like MTA.** The parser is satk's own (no dependencies) and follows the Lua 5.1.5 compiler MTA ships:
  the same tokens, quirks (`[[` nesting, `\x` is not an escape, the "ambiguous syntax" rule) and limits (200
  local variables, 60 upvalues, 200 nesting levels). Tests compare valid and malformed scripts against MTA's
  own `lua5.1.dll`, including mutated scripts. The source-corpus check compiles Lua files without executing
  them. A non-ASCII character in `near '...'` is printed as the character, not as a raw byte.
- **Per side.** Scripts of one side share one Lua state, so a global defined in `a.lua` (client) is known in
  `b.lua` (client) but not on the server; `shared` scripts are checked for both sides. A standalone file whose
  side cannot be inferred accepts either side's signature and reports deprecations that apply on every
  applicable side; use `--side` to check a specific side.
- **The MTA reference** comes from `satk kb build` (functions per side with argument lists, events, OOP classes,
  enum strings; see [scriptapi.md](scriptapi.md)). The deprecated functions are the MTA server's own upgrade
  list, read from the MTA source tree. Without a knowledge base satk parses the tree directly (about 5 s, then
  cached in `work\cache\mta\`); without a tree only the Lua checks run.

## Limitations and known issues

- Calls through variables, `exports`, `call()` and globals made at run time (`loadstring`, `_G[name]`) are not
  followed; a resource that uses them gets softer `UNKNOWN_FUNCTION` and `UNDEFINED_GLOBAL` findings.
- Argument checks trust only exact signatures (functions with the new argument parser); functions read with
  branches are checked for the leading arguments only.
- Events added by another resource look unknown (`EVENT_UNKNOWN` says so); custom element types created by
  another resource are `info`.
- `min_mta_version` is checked for format and for the functions MTA lists with a version; there is no full
  "function added in version X" table.

## Python API

```python
from pathlib import Path
from satk.mta.api import load_reference
from satk.mta.lint import lint_path, lint_source
from satk.mta.luaparse import parse, LuaSyntaxError

ref, warnings = load_reference("auto")
result = lint_path(Path("my-panel"), ref)
for f in result.findings:
    print(f.sev, f.at, f.code, f.msg)
```
