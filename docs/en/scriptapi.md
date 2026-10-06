# scriptapi: MTA Lua API and SA-MP/open.mp natives in the knowledge base

[Русская версия](../ru/scriptapi.md)

Package: `satk.kb` (modules `scriptapi*`).

## What it is

A scripting reference built from sources you already have, without the network: every MTA:SA Lua function
(signature, client/server/shared, OOP names, accepted enum strings, the C++ implementation `file:line`), the
built-in events with their parameters, OOP classes and enum string tables, plus SA-MP/open.mp Pawn natives,
callbacks and constants from local include files. `satk kb build` writes it into `<workspace>\work\kb\kb.sqlite`
next to the rest of the knowledge base; `satk kb mta` and `satk kb native` answer in one call instead of
grepping C++ or opening a wiki.

## Quick example

```powershell
satk kb build
satk kb mta engineRequestModel
satk kb mta "vehicle handling"
satk kb mta Vehicle:setHandling
satk kb mta --event onClientElementStreamIn
satk kb native SetObjectMaterial
satk kb mta
```

What comes back (shortened):

```json
{"ok":true,"name":"engineRequestModel","side":"client",
 "sig":"int|false engineRequestModel(string modelType, [int parentID])",
 "enums":{"client-model-type":"ped|object|object-damageable|vehicle|timed-object|clump"},
 "impl":"EngineRequestModel (CScriptArgReader)","loc":"Client/mods/deathmatch/logic/luadefs/CLuaEngineDefs.cpp:903",
 "src":"mta-lua","repo":"engine/mtasa"}
{"ok":true,"cols":["kind","name","side","sig"],"rows":[
 ["function","getVehicleHandling","shared","number|string|table|false getVehicleHandling(vehicle vehicle, [string property])"],
 ["function","setVehicleHandling","shared","number|bool setVehicleHandling(vehicle vehicle, [string property], [float|int|string|bool value])"]]}
{"ok":true,"name":"onClientElementStreamIn","kind":"event","side":"client","params":"(none)",
 "loc":"Client/mods/deathmatch/logic/CClientGame.cpp:2657"}
{"ok":true,"name":"SetObjectMaterial","kind":"native",
 "sig":"native SetObjectMaterial(objectid, materialindex, modelid, txdname[], texturename[], materialcolor = 0)",
 "params":6,"inc":"a_objects","loc":"src/samp-server/pawno/include/a_objects.inc:68","src":"pawn"}
{"ok":true,"functions":{"total":1527,"client":661,"server":261,"shared":605,"neon_only":339},"events":247,
 "classes":72,"enums":102,"natives":1747,"callbacks":564}
```

A typo answers `NOT_FOUND` with `did_you_mean` (`engineRequestModle` -> `engineRequestModel`). For a shared function
whose client and server versions differ, the second side comes as a nested object with only what differs.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk kb mta NAME` | - | a Lua function: `sig`, `side`, `doc` (the signature comment in the MTA source), `enums`, `oop`, `impl`, `loc`; also `Class:method`/`Class.var`, a class (`Vehicle`: members), an enum (`client-model-type`), an event |
| `satk kb mta "words" [--side client\|server\|shared]` | - | functions and events whose names contain all the words |
| `satk kb mta --event [NAME]` | - | an event with its handler parameters; without a name, the list of events |
| `satk kb mta` | - | counts: functions per side, Neon-only functions, events, classes, enums, natives |
| `satk kb native NAME [--kind native\|callback\|const] [--inc a_objects]` | - | a Pawn declaration with tags, references, arrays and defaults, its include and `file:line`; words give a table |
| `satk kb native` | - | include files with their numbers of natives, callbacks and constants |

MCP clients reach these operations through `satk_ops`/`satk_op` (they are `mcp=False`). `satk kb search` finds the
same functions, events and natives (symbol kinds `lua`, `lua-event`, `native`, `callback`).

## How it works

- **MTA source.** The tree is `paths.engine` at `HEAD` when it holds `Client/mods/deathmatch/logic/luadefs`, else
  `<workspace>\src\mtasa-neon` at `upstream/master`, else `<workspace>\src\mtasa-blue`. It is read with
  `git cat-file` (no checkout, no fetch). `<workspace>\src\mtasa-neon` at `HEAD` adds only the functions and events
  upstream lacks (`src` = `mta-lua-neon`, note "Neon fork only").
- **Functions.** Registrations are the `{"name", Func}` entries of `lua_CFunction` arrays and
  `CLuaCFunctions::AddFunction("name", Func)`; the side is the source root (`Client`, `Server`; `Shared` is both,
  narrowed by `#ifdef MTA_CLIENT`). `ArgumentParser<F>` functions get exact types from the C++ parameters of `F`
  (`std::optional` = optional, `std::variant` = `A|B`, element classes = their element type, C++ enums = `string`
  with the accepted values). Classic `CScriptArgReader` functions get their arguments from the `Read*` calls in
  source order, defaults included; reads under `if (argStream.NextIs...)` are optional, `if`/`else` alternatives
  merge into `A|B`, and the return comes from the `lua_push*` calls. Such answers carry the note "approximate
  arguments", and `doc` shows MTA's own signature comment when the function has one.
- **OOP, events, enums.** `lua_classfunction`/`lua_classvariable` between `lua_newclass` and `lua_registerclass`;
  `AddEvent("onX", "params")`; `IMPLEMENT_ENUM_BEGIN` ... `ADD_ENUM(value, "name")` tables.
- **Pawn includes.** Every `.inc` with `native`/`forward` declarations under the folders of `kb.pawn_include` in
  `satk.toml` (a path or a list), or of `SATK_KB_PAWN_INCLUDE` (paths separated by `;` on Windows; it overrides the file value), plus `pawno/include`,
  `qawno/include` or `include` of every folder under `<workspace>\src`:

  ```toml
  [kb]
  pawn_include = ["<workspace>/src/samp-server/pawno/include"]
  ```

  Without any include file the build lists only the map natives satk's map converter reads (`inc` = `satk-mapconv`)
  and warns `SKIPPED: pawn`.
- **Cost.** About 6 s of the KB build; the tables are `mta_func`, `mta_class`, `mta_oop`, `mta_event`, `mta_enum`,
  `pawn_sym`. Nothing is copied into the repository: names, types, parameter names and lines go into `work\kb`.

## Limitations and known issues

- No descriptions from the MTA wiki or SA-MP docs (no network, no third-party documentation text); `doc` is the
  source comment, when there is one.
- `CScriptArgReader` functions with type-dependent branches (`setVehicleHandling`, `setObjectProperty`) are
  approximate: read `doc` and open `loc`. Return types of classic functions list every pushed type.
- No "added in version" data. A KB built by an older satk answers `NOT_READY`: run `satk kb build`.

## Python API (if other packages use it)

```python
from satk.kb.scriptapi import parse_mta, parse_pawn       # pure parsers: {path: text} -> records
from satk.kb.scriptapi_query import mta, native, overview  # the answers of the commands
```
