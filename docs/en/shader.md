# `satk shader textures|new|check`: MTA shaders for world textures

[Русская версия](../ru/shader.md)

Package: `satk.shader`. It reads texture names from the profile index, writes MTA:SA resources from its own
templates and compiles effects with `fxc.exe` of a local Windows SDK when one is installed.

## What it is

An MTA shader reaches the game's textures by name: `engineApplyShaderToWorldTexture(shader, "*road*")`. Finding the
right names by hand is slow and error-prone: `*road*` also hits road signs, `broadway92body256a` (a car) and the
LOD textures. `textures` lists the names of a pattern, a texture kind (`road`, `glass`, `water`, `tree`,
`vehicle`, `ped` ...), a model or an area of the map, with counts, and computes a short pattern list that hits exactly
those names. `new` writes a ready resource (`meta.xml`, the client Lua glue with those patterns, the `.fx`) from a
template: car paint reflection, world texture replacement, wet roads, water, sky/fog tint, ped shading, a
post-process. `check` compiles an effect and checks what MTA does differently from plain Direct3D, plus the
resource's client Lua. Everything is written under `<workspace>\work\out\shader\`; satk never touches the game or an
MTA server.

## Quick example

```powershell
satk shader textures --kind road --limit 3
satk shader textures --model model:411 --limit 3
satk shader new wet_roads --name wetroads
satk shader check wetroads
```

What comes back (shortened):

```json
{"ok":true,"cols":["name","txd","txds","models","inst"],"rows":[["crossing_law","barrio1_lae +36",37,285,285],["sf_road5","airportroads_sfse +24",25,239,239],["…"]],"total":270,"plan":{"exact":true,"patterns":27,"apply":["tar_*","*hiway*","*junc*","*road*","…"],"remove":["*sign*","*broadway*","*wheel*","…"]},"plan_cols":["op","pattern","covers","extra","sample"],"plan_rows":[["apply","*road*",132,16,"broadway92body256a, …"],["…"]],"select":["kind road: 270","profile vanilla: 13756 texture names"]}
{"ok":true,"rows":[["vehiclegeneric256","csbravura +9",10,352,0],["…"]],"total":13,"plan":{"exact":true,"patterns":10,"apply":["infernus*","…"],"remove":["vehiclespecdot64"]},"spill":{"names":10,"other_models":1638,"note":"other models use the same names; pass targetElement …"}}
{"ok":true,"dir":"<work>/out/shader/wetroads","files":["meta.xml","client.lua","wet_roads.fx"],"textures":{"apply":17,"remove":10,"names":270,"exact":true},"install":"copy the folder wetroads into <MTA server>/mods/deathmatch/resources/ and run 'start wetroads' …","check":{"status":"ok","compiler":"fxc","errors":0,"warnings":0}}
{"ok":true,"rows":[],"status":"ok","compiler":{"tool":"fxc","sdk":"Windows SDK 10.0.26100.0","profile":"fx_2_0"},"compiled":true,"techniques":["wet_roads: vs_2_0/ps_2_0","fallback: fixed-function"],"lua":{"linked":"shader (file name)","textures":270,"…":"…"}}
```

`lua.textures` is what the resource's patterns cover after MTA's rules (last match wins): exactly the 270 names of
the kind.

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk shader textures [PATTERN...] [--kind K] [--model SID] [--area X Y R] [--exclude P...] [--max-extra N]` | — (through `satk_op`) | names (`name, txd, txds, models, inst`, plus `inside` for an area), the `plan` (apply/remove patterns) and `plan_rows` (`op, pattern, covers, extra, sample`), the `spill` |
| `satk shader new TEMPLATE [--name X] [--textures P...] [--kind K] [--model SID] [--area X Y R] [--exclude P...] [--image PNG]` | — | a resource in `<work>/out/shader/<name>/`, checked (`check`), with install instructions |
| `satk shader check FILE.fx\|FOLDER\|NAME [--lua F...] [--define N=V...] [--fxc PATH\|none] [--strict]` | — | findings (`sev, code, where, msg`), `status`, the compiler, techniques, who sets each parameter, the Lua cross-check |

Selectors of `textures` and `new` combine with AND: `--kind road --area 2495 -1666 150` = road textures of Grove
Street. Patterns use MTA's syntax: lower case, `*` and `?`, everything else literal (`cj` alone means `cj_ped_*`).

Kinds (rules in `data/shader/kinds.json`; heuristics, check the list): `road`, `pavement`, `grass`, `dirt`, `sand`,
`rock` (name rules plus the collision surface of each texture), `glass`, `water`, `tree` (names, IDE tree/glass
flags), `lod` (textures only LOD models use), `vehicle`, `vehicle_paint` (recolourable paint materials), `ped`
(ped TXDs and `player.img`). For `vehicle` and `ped` a shader with `elementTypes` `"vehicle"` / `"ped"` applied to
`*` is simpler; the answer says so in `hint`.

The plan: with `--max-extra 0` (default) it is exact, the shorter of apply-only patterns and broad apply patterns
plus remove patterns (`*road*` minus `*sign*`); every plan is verified against all texture names of the profile.
`--max-extra N` allows each apply pattern N unintended names instead (shorter lists, no removes). The `spill` of a
`--model` or `--area` selection counts uses of the same names elsewhere: MTA matches by name, so a world shader
also changes them there (only script-created elements can be targeted with `targetElement`).

Templates of `new`:

| Template | Default textures | `elementTypes` | What it does |
|---|---|---|---|
| `car_paint` | kind `vehicle_paint` | vehicle | sky reflection with Fresnel and a sun highlight; sky colours follow `getSkyGradient` |
| `texture_replace` | none: give `--textures`, `--kind`, `--model` or `--area` | world, vehicle, object, other | draws `--image` (default: a checker placeholder) instead |
| `wet_roads` | kind `road` | world, object | darker asphalt, sky reflection and sun highlight scaled by `getRainLevel` |
| `water` | `waterclear256` | world, object, other | scrolling ripples, sky reflection, tint |
| `sky_fog` | `*` | world, object | world tint, distance haze and a matching `setSkyGradient` (reset on stop) |
| `ped_shading` | `*` | ped | saturation, contrast, brightness, tint; pixel shader only, skinning untouched |
| `post_process` | the screen | — | colour grading and vignette before the HUD (`dxCreateScreenSource`, `onClientHUDRender`) |

Each resource toggles in game with `/<name>`; the values the glue sets with `dxSetShaderValue` sit at the top of
`client.lua`. Without an index `new` uses the kind's fixed fallback patterns and says so (`warn: INDEX_MISSING`).

`check` findings (MTA ignores most of these silently, the parameter just stays zero):

- `STATE_GROUP`, `STATE_NAME`, `STATE_STAGE`, `STATE_TYPE`: annotations such as `string renderState = "FOGCOLOR"`
  with a wrong group case, an unknown register (did you mean), a bad `stage,` prefix or a type MTA does not map
  (a `bool` for `LIGHTING`, a column-major matrix);
- `PROFILE`, `D3D10`, `UNKNOWN_FUNCTION`, `NO_TECHNIQUE`, `NO_PASS`, `TEXTURE_UNDECLARED`, `SEMANTIC`,
  `DEPTHBUFFER`, `SM3_FOG` (`ps_3_0` gets no fixed-function fog), `NO_FALLBACK`, `CUSTOMFLAGS`;
- `INCLUDE_ILLEGAL` (`..` in a path), `INCLUDE_MISSING`, `INCLUDE_LOOP`: MTA resolves `#include` relative to the
  first `.fx`, or to the resource root when the path starts with `/`;
- Lua: `LUA_UNKNOWN_PARAM` (`dxSetShaderValue` name that does not exist; a parameter with a semantic is found
  only by its semantic, an annotated one is not settable), `LUA_OVERRIDES_MTA`, `LUA_TEXTURE_NOT_SET`,
  `LUA_ELEMENT_TYPES`, `NO_MATCH` (a world-texture pattern that matches nothing: usually a typo);
- `X####`: errors and warnings of `fxc` with the original file and line.

## How it works

- Names: every texture of every active TXD of the profile index (lower case). Kinds use the index (model
  sections, IDE flags, `model_tex`, LOD placements, `dff_mat` colour slots) and the texture-to-surface table of
  `satk col` (`data/colgen/tex_surface.json`). The data is loaded once per index file (about 0.6 s).
- Pattern cover: candidates are prefixes, suffixes and word parts of the wanted names; a greedy set cover picks them,
  remove patterns are costed by the patterns they need; ties prefer patterns ending on word boundaries. Same input,
  same plan.
- `check` reads the effect like MTA (includes, macros), compiles it with `fxc /T fx_2_0` (`IS_DEPTHBUFFER_RAWZ`
  defined as MTA does, both values when the file uses it) and compares annotations and semantics with the facts of
  the MTA client source in `data/shader/mta.json`. `fxc` is searched in `--fxc`, `SATK_FXC`, `DXSDK_DIR`, the
  Windows Kits (registry and `Program Files`), the Visual Studio / Build Tools folder satk detected, then `PATH`.
- Lua glue: the client scripts of `meta.xml` (else the `.lua` files next to the `.fx`), read statically: literal
  calls, `local` string constants and `for _, p in ipairs(LIST)` loops over string tables.

## Limitations and known issues

- Kinds are heuristics from names, collision surfaces and IDE flags: a few false hits or misses are normal; use
  `--exclude` or explicit patterns for the final list.
- `fxc` uses `D3DCompiler_47`; MTA compiles with D3DX9 at run time. Both accept `fx_2_0` effects, rare
  differences are possible. Without the Windows SDK, `check` runs only the static syntax and MTA checks and says so.
- The templates are tested by compiling and by these checks, not inside a running MTA client: look at them in game
  before a release.
- Untextured materials (some vehicle paint parts) cannot be reached by a world-texture shader.

## Python API (if other packages use it)

```python
from satk.shader.wild import cover, resolve, matches     # MTA name matching and the exact pattern cover
from satk.shader.fx import load                          # effect reader with MTA include rules
from satk.shader.check import find_fxc, compile_fx, mta_checks
```
