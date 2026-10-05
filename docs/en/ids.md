# Identifiers (SIDs)

[Русская версия](../ru/ids.md)

<!-- Source of truth: src/satk/core/ids.py (a frozen contract).
     The example answers are real (`satk asset get`, profile vanilla, 2026-10-05), shortened. -->

Everything satk knows is addressed by a short string, a **SID** (stable ID): `model:411`, `tex:bistro/vent_64`,
`inst:lae2_stream0#4`, `fn:0x53bf09`. The index, the viewer, Blender, the symbol database and the game return the
same SIDs, so the answers of different tools fit together.

## Grammar

```
sid := kind ":" key [ "@" layer ]
```

- The canonical form is lower case, addresses are `0x…` in lower case. Input is case-insensitive:
  `Model:Infernus` = `model:infernus`.
- A key holds no absolute paths and no database row numbers, so SIDs do not change when the index is rebuilt.
- `@layer` is written only for a version that did **not** win in the profile: in the `samp` profile model 300 is
  `lapdna` (`model:300`), and the vanilla definition is `model:300@vanilla` (`cutobj01`).
- `satk asset get` accepts only SIDs: `infernus` without a kind gives `BAD_ID`, write `model:infernus`.
  Model commands (`satk model image`, `satk asset export`) also accept a bare name.

## Kinds

| kind | key | example | what it is |
|---|---|---|---|
| `model` | model number (a name is accepted on input) | `model:411`, `model:infernus` | the active IDE definition; the answer always uses the number, the name is in `name` |
| `dff`, `txd` | file name without extension | `dff:infernus`, `txd:vehicle` | the active file: "the first registered archive wins" |
| `tex` | `txd/texture` | `tex:bistro/vent_64` | a texture inside a TXD; a texture name alone is not a key (711 names repeat) |
| `pix` | 24 hex characters | `pix:45f2dcad0a0e790249e06eb9` | unique picture content (the same pixels in different TXDs are one `pix`) |
| `file` | path[/entry] | `file:models/gta3.img/infernus.dff` | a concrete file, also one that is shadowed |
| `col` | COL model name | `col:infernus_col` | the active collision (embedded in the DFF for cars) |
| `ide` | path | `ide:data/vehicles.ide` | a definitions file |
| `ipl` | name | `ipl:lae2_stream0` | a text or binary placement file |
| `inst` | `ipl#number` | `inst:lae2_stream0#4` | one placement (the number is the index in the `inst` section of that IPL) |
| `item` | `ipl#section#number` | `item:lae2#enex#0` | other IPL sections (entrances, cars, …) |
| `zone` | code | `zone:gan1` | a zone from `map.zon`/`info.zon`; the key is the code (`GAN1`, `VE`, `LA`), the in-game name is in `title` ("Ganton") |
| `ifp`, `anim` | name / `ifp/anim` | `ifp:ped`, `anim:ped/walk_civi` | animations |
| `fn`, `g`, `vt`, `patch` | address or name | `fn:0x53bf09`, `fn:cped::update`, `g:0xc8d4c0`, `vt:0x86c538` | functions, globals, vtables and MTA patch points in `gta_sa.exe` |
| `el` | `type/id` | `el:vehicle/7` | a world element at run time (the `game` target through the MTA agent, and `mock`) |
| `bm`, `cap`, `note` | name / id | `bm:grove_center`, `cap:20261004-163136-1a23`, `note:1` | camera bookmarks, frame captures, notes |

The index also has SIDs of the data files (schema v3): `water:<n>`, `tcyc:<weather>/<hour>`, `handling:<id>`.
`asset get/refs/find` understand them; they are not in the list of `satk.core.ids` yet, so other packages do not
accept them (see [index.md](index.md)).

## What the answers look like

| SID | what `satk asset get` returns (shortened) |
|---|---|
| `model:411` | `"name":"infernus","sec":"cars","txd":"infernus","tex":{"total":13,"missing":0},"geo":{"verts":3573,"tris":3072},"links":{"dff":"dff:infernus","txd_chain":["txd:infernus","txd:vehicle"],"col":"col:infernus_col"}` |
| `inst:lae2_stream0#4` | `"model":"model:17613","name":"Lae2_roads89","pos":[2489.3,-1668.5,12.3],"rz":0.0,"lod":"inst:lae2#198","ipl":"ipl:lae2_stream0","aabb":[…]` |
| `tex:bistro/vent_64` | `"txd":"txd:bistro","w":64,"h":64,"d3dfmt":"X8R8G8B8","pix":"pix:6e63ecc6dc8ee4ad8bfd1139"` |
| `pix:45f2dcad0a0e790249e06eb9` | `"w":256,"h":256,"d3dfmt":"DXT1","textures":["tex:a51_ext/ws_rooftarmac1",… 243 TXDs]` |
| `txd:vehicle` | `"file":"file:models/generic/vehicle.txd","ns":"loose","tex_count":19` |
| `col:infernus_col` | `"version":3,"via":"embedded","spheres":20,"faces":14,"models":["model:411"]` |
| `ipl:lae2_stream0` | `"kind":"binary","parent":"ipl:lae2","n_inst":377,"items":{"cars":5}` |
| `zone:gan1` | `"name":"GAN1","title":"Ganton","min":[2222.56,-1722.33,-89.08],"max":[2632.83,-1628.53,110.92],"file":"file:data/info.zon"` |
| `fn:0x53bf09` | `"id":"fn:0x53bee0","fn":"CGame::Process","off":"0x29","src":"game_sa/Game.cpp:78","confidence":"high","patches":{…}`: an address inside a function gives the SID of its start |
| `g:0xc8d4c0` | `"name":"gGameState","type":"int32","src":"app/app.h:19","patches":{…}` |
| `bm:grove_center` | `"pose":{"pos":[2490.0,-1655.0,16.0],"look":[2500.0,-1700.0,16.0],"fov_h_deg":70.0},"env":{"time":"12:00",…}` (a bookmark saved in this workspace) |

## How to get and use a SID

- Find: `satk asset find <text> [--kind K]`, exact names first; a substring with the pattern `"*text*"` (it also
  works in Russian for notes: `--kind note`).
- Expand: `satk asset get <sid>`: the object and the related SIDs.
- Follow relations: `satk asset refs <sid>`: without `--rel` it returns how many relations of each kind there are;
  with `--rel inst|tex|models|lod|patches|…` the relations themselves.
- Near a point: `satk world near X Y [--r R]`: `inst:` placements by distance.
- From a viewer frame: `satk view capture --marks N` (legend "number → SID") and `satk view pick PX PY`.

## Rotations

An IPL stores the quaternion "as in the file", and the world rotation is its **conjugate** (the engine negates
the imaginary part). satk always returns world values: `rz` in degrees when the rotation is only around
Z, otherwise `q:[x,y,z,w]`.

## Quick example

```powershell
satk asset get model:411
satk asset refs model:411
satk asset get inst:lae2_stream0#4
satk asset get fn:0x53bf09 --fields fn,off,src,confidence
satk asset get model:300@vanilla --profile samp
```
