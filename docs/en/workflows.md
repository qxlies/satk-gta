# Typical tasks

[Русская версия](../ru/workflows.md)

<!-- The human version of the agent scenarios S1-S16 (the agent version with token costs is
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
