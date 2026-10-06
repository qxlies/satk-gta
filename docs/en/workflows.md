# Typical tasks

[Русская версия](../ru/workflows.md)

<!-- The human version of the agent scenarios S1-S29 (the agent version with token costs is
     docs/agent/workflows.md). Chains re-run on 2026-10-05 with schema-v3 indexes; the numbers in the comments are
     real answers. -->

Commands are for PowerShell/cmd: `satk` is the shim `tools\satk.cmd` of the workspace (or the `satk` command of an
installed package). An AI assistant runs the same chains through MCP tools with the same names
(`satk asset find` = `asset_find`; operations without a tool through `satk_op`), see
[docs/agent/workflows.md](../agent/workflows.md).

## Quick example

```powershell
satk asset refs tex:711_sfw/ws_rooftarmac1
satk world near 2495 -1687 --r 30 --limit 10
satk re addr gta_sa.exe+0x13BF09
satk index diff vanilla installed --kind txd
satk view capture --target mock --grid
satk view pick --cells D3 --target mock
```

One command from several scenarios below; the viewer commands use the built-in mock (`--target mock`), which needs
no window.

## Find a texture and where it is used (S1)

```powershell
satk asset find ws_rooftarmac1 --kind tex --limit 5          # total 257: a texture with this name in 257 TXDs
satk index query "SELECT v.sid, v.name, v.n_inst FROM model_tex t JOIN v_model v ON v.id = t.model_id WHERE t.texture = ? ORDER BY v.n_inst DESC" --params ws_rooftarmac1 --limit 5
satk index query "SELECT v.pix, count(*) AS txds FROM texture t JOIN v_tex v ON v.rid = t.id WHERE t.name = ? GROUP BY v.pix ORDER BY txds DESC" --params ws_rooftarmac1
satk texture image pix:45f2dcad0a0e790249e06eb9 pix:404679390e4ba32005cc74c5 pix:a9402c6bc5d7f741d16bf5a9 --mode sheet
```

The first query: how many TXDs contain the texture; the second: which models use it (470, the most placements
belong to `portakabin`); the third: how many different pictures really hide behind the name (3); the fourth draws
them on one sheet. For one particular texture: `satk asset refs tex:711_sfw/ws_rooftarmac1` (how many models, TXDs
and identical pictures: 1, 1, 242) and `satk asset refs tex:711_sfw/ws_rooftarmac1 --rel models`.

## Look at an area from above

```powershell
satk world near 2495 -1687 --r 30 --limit 10                 # what stands around the point, by distance (41 in all)
satk map image --center 2495,-1687 --span 300 --labels 20    # a 768x768 map with numbers and a legend
```

## Look at a place in the viewer and find out what the building is (S2)

```powershell
satk view start --window 960x540
satk view capture --bm grove_center --marks 6                # bookmark: Grove Street, looking at CJ's house
satk view pick 154 270                                       # what is under the pixel (by collision; --capture cap:... for another frame)
satk asset get inst:lae2_stream0#39                          # the house on the left: ganghous05_LAx, model:3646
satk view stop
```

Bookmarks: `satk view bookmark list`; your own bookmark: `satk view goto --pos X,Y,Z --look X,Y,Z`, then
`satk view bookmark save <name>`. A grid instead of pixels: `satk view capture --grid` and `satk view pick --cells D3`.
Ariane is the "quick eyes": no shadows, people, cars or particles, approximate LOD. The real game as a target
(`--target game`, [mta-agent.md](mta-agent.md)) exists, but starting the MTA client is not reliable yet.

## Analyze an MTA crash (S3, S10)

```powershell
satk re addr gta_sa.exe+0x13BF09             # or the dump text: satk re addr --text-file crash.txt
satk re src CGame::Process --context 12      # gta-reversed code around the function (display only)
satk re patches --fn CGame::Process          # 22 places where MTA patches this function, with file:line
```

`re addr` understands addresses (`0x53BF09`, `gta_sa.exe+0x13BF09`) and the text of an MTA crash dump when the lines
`Module = ...` and `Offset = ...` come on separate lines, as in the dump itself. `re patches` folds upstream sites
that trunk repeats (`raw_total` 33).

A whole minidump or `core.log` ([crash.md](crash.md)):

```powershell
satk crash sample                            # a generated sample dump to try it on
satk crash list --limit 5                    # dumps and crash logs, newest first
satk crash analyze --last --limit 5          # the stack with functions: CStreaming::Update+0x33, game_sa/Streaming.cpp:111
```

## What does my install change (S4, S8)

```powershell
satk index diff vanilla installed --kind txd                 # +3 TXDs of console buttons in the original install
satk texture image txd:ps3btns --mode sheet --profile installed
satk index diff vanilla samp --kind model                    # SA-MP: +1 435 models, 12 overridden
satk asset get model:300 --profile samp                      # lapdna (samp) instead of cutobj01
```

The profiles `installed` and `samp` are built separately: `satk index build --profile installed` and `--profile samp`.

## Check a mod (S12)

```powershell
satk mod check mymod.zip                       # problems, table rule | sev | file | msg | sid
satk mod inspect mymod.zip                     # what the mod replaces, adds and changes
satk mod conflicts --profile game              # mods in the game's modloader folder that fight over a file or record
```

`mod check` reports ID collisions, files Mod Loader skips, game records a partial data file drops, IDs over the
limit and asset lint findings ([lint.md](lint.md)); `mod inspect` lists what the mod replaces, adds and changes
([modinspect.md](modinspect.md)). A folder, a `.zip`, an `.img` or a single file works.

## Replace textures (S13)

```powershell
satk texture extract txd:bistro                              # every texture as PNG + texmod.json: work\out\texmod\bistro\
satk texture replace txd:bistro vent_64=my_vent.png          # PSNR per texture, a Mod Loader folder
satk mod check <workspace>\work\out\mods\bistro              # 26 info (mipmaps), nothing worse
```

The result is `work\out\mods\bistro\bistro.txd` + `README.txt`, a Mod Loader folder; the answer has format, size,
mips and PSNR per texture. A new TXD from a folder of PNGs: `satk texture pack` ([texmod.md](texmod.md)).

## Model: preview and export

```powershell
satk model image model:411                     # 4 views on one 768x768 sheet (software renderer)
satk asset export model:411 --format glb       # glTF for Blender and viewers: work\out\models\infernus\
satk asset export model:411 --format raw       # DFF + TXD "as in the game" (the collision is inside the DFF)
```

## Find a model by how it looks (S14)

```powershell
satk describe import                           # once: 2 131 gta-scout descriptions into the notes
satk asset find bench --kind note              # 15 models whose description mentions a bench
satk model image model:4085 --views 1 --size 256
```

The descriptions are bound to models by the DFF hash ([describe.md](describe.md)).

## Vehicle data (S15)

```powershell
satk asset get model:411 --fields name,handling,colors,links  # mass 1400, max_vel 240, 8 colour pairs
satk asset get handling:infernus                              # every handling.cfg field and the flags
```

## An area in Blender and back to MTA (S5)

```powershell
satk blender doctor --quick
satk blender import-area --center 2495,-1687 --box 60 --lod hd
satk blender render --blend <workspace>\work\blender\jobs\<job>\scene.blend --pos 2495,-1757,45 --look 2495,-1687,13 --objindex
satk blender export --blend <workspace>\work\blender\jobs\<job>\scene.blend --objects carlshou1_lae2 --target mta-resource
```

`<job>` is the job folder from the `import-area` answer (`files.blend`). A 60x60 m area: import about 7 s, render
about 3 s, export about 2 s; the export lands in `work\out\exports\`.

## Convert a map (S16)

```powershell
satk map validate ipl:lae2_stream0 --limit 3   # 377 objects, 0 errors, 2 info lines
satk map convert ipl:lae2_stream0 --to mta     # work\out\mapconv\lae2_stream0.map + a JSON report
```

`map convert` reads SA-MP Pawn, MTA `.map`, text IPL and JSON and writes any of them ([mapconv.md](mapconv.md)).

## One SQL query (S7)

```powershell
satk index query "SELECT i.is_lod, count(DISTINCT m.id) AS models FROM v_inst i JOIN v_model m ON m.id = i.model_id JOIN zone z ON z.name = 'VE' WHERE i.x BETWEEN z.minx AND z.maxx AND i.y BETWEEN z.miny AND z.maxy AND i.area = 0 AND m.col_via IS NULL GROUP BY i.is_lod"
```

The answer `[[0,1],[1,911]]`: in Las Venturas 911 LOD models have no collision (as intended) and there is one
non-LOD placement without it. The table schema: `satk help schema` or [docs/agent/schema.md](../agent/schema.md).

## Engine knowledge (S11)

```powershell
satk kb build                                  # once, about 30 s: reads the donor sources
satk kb struct CPed --at 0x540                 # m_fHealth, float; CPed is 0x79C bytes
satk kb opcode 0A8C                            # WRITE_MEMORY (CLEO) and its gta-reversed handler
```

More in [kb.md](kb.md).

## Add a new car as an add-on (S17)

```powershell
satk mod add vehicle --dff dff:infernus --txd txd:infernus --like 411 --name infernus2 --game-name "Infernus II" --dry-run
satk mod add vehicle --dff my_car.dff --txd my_car.txd --like 411 --name mycar --game-name "My Car"
satk mod check <workspace>\work\out\addon\mycar
```

`mod add` copies the donor's data lines (IDE, handling, carcols, carmods, the game name) with a free id and new names
into a Mod Loader folder `work\out\addon\<name>\`; copy that folder into the game's `modloader` folder. Read the
warnings: a vehicle id over 611 or a new handling name needs the fastman92 Limit Adjuster. Peds, weapons and objects
work the same way ([addon.md](addon.md)).

## Change handling or weapon data (S18)

```powershell
satk data get handling infernus --field fMass fTractionMultiplier   # value, unit and meaning of each field
satk data explain handling fTractionBias                           # unit, range, column, MTA name
satk data patch handling infernus fMass=1500 fTractionMultiplier=0.8
satk data patch handling infernus fMass=1500 --target mta          # a setModelHandling Lua snippet instead
```

The default target writes a Mod Loader folder whose readme holds only the changed line; `--target full` writes a
patched copy of the whole file. Weapons: `satk data get weapon PISTOL`, `satk data patch weapon ...`.

## Write or fix a CLEO script (S19)

```powershell
satk script new spawn_car --name mycar --model 522 --cheat BIKE   # a checked .txt and the assembled .cs
satk script check mycar/mycar.txt                                  # errors with line numbers
satk script asm mycar/mycar.txt
satk script disasm mymod.cs                                        # someone else's script as text
satk script asm mymod/mymod.txt --compare mymod.cs                 # what your edit changed
```

satk never writes into the game: copy the `.cs` into the game's `cleo` folder (CLEO 4 or 5). Opcode signatures:
`satk kb opcode <id or words>` ([script.md](script.md)).

## Look up an MTA Lua function or a SA-MP native (S20)

```powershell
satk kb mta engineRequestModel                 # signature, client/server, enums, the C++ source line
satk kb mta "vehicle handling"                 # every function whose name has both words
satk kb mta --event onClientElementStreamIn
satk kb native SetObjectMaterial
```

The answers come from the local sources through the knowledge base (`satk kb build` after an update). For the full
SA-MP/open.mp list set `kb.pawn_include` in `satk.toml` to a pawno or qawno include folder ([scriptapi.md](scriptapi.md)).

## Make a mod's textures smaller (S21)

```powershell
satk texture audit mymod                                        # issues by bytes saved, the fix for each
satk texture optimize mymod --max 512 --drop-unused --dedupe    # copies in work\out\txdopt\mymod\, PSNR per texture
satk texture budget --area 2495 -1666 150                       # streaming memory around a point vs the 50 MiB limit
```

Look at `psnr_min` and every `LOW_PSNR` warning before you copy the output over the mod ([txdopt.md](txdopt.md)).

## Check a mod before release; many files at once (S22)

```powershell
satk recipe list
satk recipe run check-mod-before-release --var mod=mymod --dry-run
satk recipe run check-mod-before-release --var mod=mymod
satk batch asset.lint --over "models/gta3.img/infernus.*" --arg fail_on=error --jobs 2
```

A recipe is a saved chain of operations (inspect, check, texture audit, id conflicts); a batch runs one operation over
a glob, IMG entries, a list file, a folder or an index query and writes one JSON line per input ([batch.md](batch.md)).

## Add a street light to a model (S23)

```powershell
satk fx2d dump model:lamppost1                                       # the lamp post's light as JSON
satk fx2d copy model:lamppost1 my_lamp.dff --filter light --offset 0,0,1
satk fx2d check my_lamp.dff
```

The result is a copy of the DFF in `work\out\fx2d\`; `fx2d apply` writes hand-edited JSON back ([fx2d.md](fx2d.md)).

## Collision for a new model (S24)

```powershell
satk col gen my_model.dff                       # a COL3 file in work\out\colgen\
satk col gen my_models --archive my_models.col  # one .col for a folder of models
satk col check my_models.col
```

The mode is chosen per model (a hull for small objects, spheres for cars, a mesh for big ones); surfaces come from
texture names, `satk col surface <texture>` explains the choice ([colgen.md](colgen.md)).

## Create a new asset in SA style (S25)

An AI assistant (or you) models the asset in a live Blender session, shows a picture next to two stock models
of the class after the rough shape and again after the final shape, then exports a Mod Loader folder and checks
it against the stock model. The whole workflow: [authoring.md](authoring.md); the style rules:
[sa-style.md](sa-style.md).

## Write and check an MTA resource (S27)

```powershell
satk mta resource new my-panel                  # a starter resource in work\out\mta\my-panel\
satk mta lint my-panel                          # what would break on a server, before you upload it
satk mta pack mods\cars --kind vehicle          # a resource that loads a folder of DFF/TXD/COL files
satk mta server-check my-panel                  # the built MTA server loads it (127.0.0.1, no game client)
```

`satk mta logs server.log` turns server and client logs into rows with a hint ([mta.md](mta.md)). Copy the
resource folder into `<server>\mods\deathmatch\resources` yourself; satk never writes into a server.

## Animations (S28)

```powershell
satk anim list ifp:ped --name walk              # animations of ped.ifp: bones, keys, duration, root motion
satk anim extract anim:ped/walk_civi --out walk.json
satk anim write walk.json --out mywalk.ifp --pack mywalk
satk anim check mywalk.ifp --loader mta
satk anim mta mywalk.ifp --replace ped/WALK_civi=WALK_civi
```

The last command writes a client resource that replaces the game animation. `anim merge` patches a package such
as `ped.ifp` instead; `anim to-blender` and `anim from-blender` edit animations in Blender ([anim.md](anim.md)).

## See a new model next to the map, then in the game (S29)

```powershell
satk view vehicle --dff work\out\kit\mycar\files\mycar\mycar.dff --txd work\out\kit\mycar\files\mycar\mycar.txd --pos 2495,-1675,13.4 --watch
satk view capture --pos 2503,-1666,17 --look 2495,-1674,13.4 --marks 6
satk ingame start --mod work\out\addon\mycar
satk ingame check --suite vehicle
satk ingame reload
```

The viewer shows the exported files next to the map in a fraction of a second after each re-export (`view place`
for a prop, `view ped` for a ped); it needs the viewer build with the scene methods ([viewscene.md](viewscene.md)).
Behaviour (driving, collisions, damage, lights, streaming) is checked in the real game: `ingame start` runs a
private test server and writes the one-time administrator setup, you start the client with `satk ingame play`, and
`ingame check` returns a verdict and a mod-versus-vanilla frame per check ([ingame.md](ingame.md)).

## Build the server of the MTA fork

```powershell
satk engine doctor
satk engine build --project server             # x64, about 3 min from scratch; seconds after editing one .cpp
satk engine server-smoke                       # start on 127.0.0.1 and a clean stop
```

## Notes

```powershell
satk note add model:411 "Infernus: sports car, base for the streaming test" --tags test
satk note list model:411
satk note list --query sports
```
