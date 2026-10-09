# Kit: templates, shading, generators and export for any asset kind

[Русская версия](../ru/kit.md)

Package: `satk.kit` (MIT) and `blender/satk_blender/kit` (GPL-3.0-or-later).

## What it is

The kit is how a person or an AI assistant makes a new GTA:SA asset of any kind in Blender - a car, bike, boat,
plane, helicopter, trailer, train, street prop, building with its LOD, interior, weapon, ped skeleton, pickup
or tuning part - with the structure, materials and shading of the vanilla game. It starts from a vanilla model
(`--like`) or a kind (`--kind`) and copies only names and numbers: the frame tree in the vanilla order, dummies,
empty part slots, material presets with measured values, a collision skeleton and the LOD pair. Generators
build the wheel, the low-detail and damaged parts, the map LOD and the collision; `kit.export` makes a
game-ready package (DFF, own-texture TXD, COL, Mod Loader folder or add-on); a map model leaves with its baked
prelight, a primitive collision and its LOD. Everything is written under `<workspace>\work\`; the game is only read.

`kit blank` is an optional quick start, never the required path: a soft-shaded quad base mesh of a car (bodies
`sedan coupe sports suv van hatchback wagon suv_boxy pickup`), a bike (`sport`, `scooter`), boat, helicopter, plane,
prop or building, built from dimensions and the frames of a `--like` model and never from vanilla vertices. A car
blank is a half body with a MIRROR modifier: smooth faces with hard edges only on material borders, round wheel
arches (an arc of 6-8 segments around the wheel) with a tub, a liner plate and return walls (never see-through),
recessed glass, keyed lamps in a bezel, door jambs, a mirror on a stalk that starts inside the door, part regions
for the split, and one continuous paint UV island in the clean strip of `vehiclegrunge256`. A scooter is built
around its frames: the steering axis runs inside the leg shield, the seat top is 10 cm under the rider dummy, the
muffler ends at the exhaust dummy. When the blank does not fit the design, build the body from your own lofted
sections instead.

## Quick example

```powershell
satk kit kinds
satk kit kinds --kind bike
satk kit scene-spec --kind boat
satk kit kinds --atlas --limit 5
satk kit blank
satk kit blank --kind prop_cyl --dims 0.6,0.6,1.1 --name mybin --plan-only
satk kit blank --kind automobile --body hatchback --dims 4.6,2.1,1.5 --wheel-d 0.66 --name myhatch --plan-only
satk kit blank --kind bike --body scooter --name myscoot --plan-only
```

What comes back from the first command (shortened):

```json
{"ok":true,"cols":["kind","group","like","like_name","peers","what"],
 "rows":[["automobile","vehicle","model:426","premier",144,"4-wheel car, van, truck or bus (vehicles.ide type car)"],
         ["bike","vehicle","model:461","pcj600",10,"motorbike (2 wheels, forks, rider leans)"], ...],"total":20}
```

A whole asset, step by step in a live Blender session ([studio.md](studio.md)):

<!-- docs-smoke: skip needs Blender 5.1, the vanilla index and a few minutes -->
```powershell
satk blender session start --name car
satk kit template --like model:426 --name mycar --dims 5.9,2.3,1.5 --session car
satk kit blank --kind automobile --like model:426 --name mycar --dims 5.9,2.3,1.5 --split --fill --session car
satk blender call kit.shade --session car
satk blender call kit.wheel --session car
satk blender call kit.damage --session car
satk blender call kit.vlo --session car
satk kit export --session car --add
```

A street prop with a LOD, from nothing but satk (the dogfood task of the A1 wave; `tests/kit/test_dogfood_prop.py`
runs these steps, 13 satk commands after the session start, no helper script):

<!-- docs-smoke: skip needs Blender 5.1, the vanilla index and a few minutes -->
```powershell
satk blender session start --name bin
satk kit template --like model:1300 --name sa_bin1 --dims 0.63,0.63,1.09 --lod --session bin
satk blender call mesh.lathe --session bin --params '{"name":"bin_body","segments":16,"axis":"z","profile":[[0,0.03],[0.26,0.03],[0.29,0],[0.29,0.09],[0.275,0.12],[0.275,0.86],[0.315,0.89],[0.315,0.96],[0.29,1.03],[0.17,1.09],[0.15,1.02],[0,1.02]]}'
satk blender call kit.fill --session bin --params '{"slot":"sa_bin1","objects":["bin_body"]}'
satk blender call uv.fit --session bin --params '{"object":"sa_bin1","select":{"where":"z<0.87"},"rect":[0,0,1,0.72]}'
satk texture new sa_bin1 --size 256 128 --color "#7a7c7c" --rect 0:0:1:0.6=#5c7a68
satk blender call kit.bake --session bin --params '{"object":"sa_bin1","size":[256,128]}'
satk texture finish new/sa_bin1.png --mask work/studio/bin/bake/sa_bin1_ao.png --edge work/studio/bin/bake/sa_bin1_edge.png --role prop
satk blender call kit.material_preset --session bin --params '{"object":"sa_bin1","role":"map","slot":0,"image":"work/out/texmod/finish/sa_bin1-photo_like.png"}'
satk blender call kit.lod --session bin
satk kit export --add --session bin --place 2495,-1687,13
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk kit kinds [--kind K] [--materials] [--atlas] [--classes]` | — (`satk_op`) | the 20 asset kinds, material presets, atlas regions, game-ready classes |
| `satk kit template (--like SID \| --kind K) [--name N] [--dims L,W,H] [--tier sa_plus\|vanilla] [--ghost] [--lod] [--session S] [--plan-only]` | — (`satk_op`) | the template plan and the Blender scaffold (session, or a one-shot `.blend`); `--lod` gives a prop a LOD slot like a building's |
| `satk kit export [--model N] (--replace SID \| --add [--place X,Y,Z]) [--session S \| --blend F] [--col auto\|kit\|none\|box\|boxes\|spheres\|hull\|mesh] [--prelight auto\|bake\|simple\|none]` | — (`satk_op`) | package a kit model: DFF, COL, own-texture TXD, the LOD, Mod Loader folder or `mod add`, checks; `<stem>.inventory.json` beside the DFF when the model carries inventory tags ([inventory.md](inventory.md)) |
| `satk kit blank [--kind automobile\|bike\|boat\|heli\|plane\|prop_box\|prop_cyl\|building_box] [--like SID] [--name N] [--dims L,W,H] [--wheel-d M] [--tier sa_plus\|vanilla] [--body sedan\|coupe\|sports\|suv\|van\|hatchback\|wagon\|suv_boxy\|pickup\|sport\|scooter] [--split] [--fill] [--session S] [--plan-only]` | — (`satk_op`) | an optional soft base mesh of a kind in the session (no kind: the catalog); `--dims` is length, width, height (the order of `kit template --dims`); `--split --fill` cuts the parts into the kit slots of the template of the same name |
| `satk kit scene-spec [--kind K]` | — (`satk_op`) | the Blender scene contract (collections, tags, DragonFF properties) |
| `satk blender game-ready <mesh> --asset-class prop\|building\|terrain\|vegetation\|interior_prop\|interior_shell\|pickup\|overlay` | — (`satk_op`) | any mesh made game-ready with the vanilla texel density, warm night prelight, face light, draw distance at most 300 with collision |

Kit methods in a session (`satk blender call <method>` or `satk blender methods --query kit`):

| Method | What it does |
|---|---|
| `kit.template` | build the scaffold from a plan file |
| `kit.blank` | build the blank of a plan file: half body + MIRROR modifier, smooth with hard material borders, part regions, clean paint UVs, `satk_sec` on vehicle pieces |
| `kit.blank_split` | apply the mirror and cut the blank into its parts (doors per side); `fill` moves them into the kit slots; the parts keep the shell's normals and the panel lines are matched (`seams`), so a cut bonnet or door shades as the body it came from |
| `kit.adopt` | make an imported model (`satk blender import-model`) a kit model, for edits and re-export |
| `kit.fill` | move modelling objects into a slot (joined, modifiers applied); `weld: true` (or a distance in m, default 0.002) merges the open-boundary vertices where the pieces meet, so they become one welded surface |
| `kit.material_preset` | assign a preset (`paint1`, `glass`, `lamp_fl`, `map`, ...); `image` sets an own texture |
| `kit.shade` | vanilla shading in the fixed order, checked on the exported DFF: hard edges on material borders, UV seams that fold more than `seam_angle` (20 deg), named creases (`mesh.mark` sharp, edge creases, attribute `satk_crease`) and fold-backs above `hard` (110 deg); corners stay soft; `satk_soft` edges stay smooth; `mode: angle` is the old dihedral rule; then the cuts between parts are matched (`match_seams`, `seam_deg` = the class's hard angle): the corners of two parts that meet take one normal, so panels never read as faceted lids |
| `kit.wheel` | the wheel: diameter = `wheel_scale`, 12 sides (vanilla) or 20 (sa_plus), tyre UVs on `vehicletyres128` mapped like vanilla wheels (tread column u 0-0.25 and sidewall column u 0.25-0.5 of one tyre quarter, each segment the full column width): a dark tyre with tread |
| `kit.vlo` | the low-detail body: `method: sections` (default) lofts 7 outline sections of the body with a dark window band (about 100 triangles, the silhouette kept); `decimate` is the old collapse |
| `kit.damage` | the `_dam` parts: dents (every vertex of a dent moves one way, so inner skins follow the outer one), hinge sag, shattered glass (alpha 242), same triangle count, duplicate material slots merged |
| `kit.lod` | a map model's LOD (about 0.21 of the triangles, never below 12 per closed piece; it keeps the HD outline: answers `coverage` and `how`, `boxes` when the decimation lost the walls); a prop scaffolded without a LOD slot gets one on the first call |
| `kit.col` | collision in Blender: a closed shadow mesh and contact faces under the top of the body for vehicles (8-14 triangles, surfaces CAR and GLASS, as vanilla cars carry; `contact: false` skips them), hull/box/mesh with face light for the map |
| `kit.uv_region` | project faces into a named region of the shared vehicle atlases |
| `kit.bake` | Cycles bakes of ambient occlusion and an edge mask for textures; `size` is a number, `[w, h]` or `"WxH"` (a 256x128 texture gets 256x128 masks) |
| `kit.info`, `kit.export` | the model's slots, triangles and LOD (`frames: true` writes every frame and dummy with its world position, parent and part box, and the ghost's part sizes, to `frames_file` for refitting); the Blender half of the export |

The mesh and UV methods the kit relies on (studio methods, [studio.md](studio.md)) changed in the A2 wave:

| Method | What changed |
|---|---|
| `mesh.lathe` | writes cylindrical UVs: `u` once around the axis (0..1; over the sweep for a partial lathe), `v` = arc length along the profile (flat tops and rims get their own height); the seam is on an edge, so no face spans the `u` range; caps get a planar disc |
| `uv.unwrap method=cylinder` | computed, not the Blender operator: `u` around `axis` (`x\|y\|z`), `v` = height at the mean radius, the seam at a vertex angle (after the biggest empty gap for an arc), axis vertices take the `u` of their face, caps are planar discs |
| `uv.fit` | `keep_aspect` defaults to `false`: the island fills the rectangle (the usual way to place a part in a texture region); `true` keeps the shape |
| `uv.texel` | `px` may be `[w, h]` or `"WxH"`; the density of a non-square texture counts `sqrt(w * h)` |
| `mesh.inset`, `mesh.extrude` | the new faces get real UVs (planar on their own plane at the density of their neighbours, joined to the neighbour they touch; `uv_filled` counts them); an inset that would collapse the faces and a zero-distance extrude are errors with a hint; a free sheet extruded is a closed slab (`slab: true`) |
| `mesh.solidify` | new: a whole shell or free sheet gets `thickness` (m) with `offset` -1 (inward) .. 1 (outward), rim faces on open borders and UVs; a region of a solid is an error (use inset + extrude) |

## How it works

- Data, measured on the vanilla game (names and numbers only): `data/kit/kinds.json` (kinds, frames with their
  share among peers, slots, material roles, collision/LOD/TXD recipes, data lines), `materials.json` (role
  presets: key colours, shared textures, env/specular/reflection values), `segments.json` (round-part side
  counts per tier, reference numbers), `classes.json` (texel density by
  size bucket, night prelight, face light) and `data/kit/atlas/vehicle.json` (regions of `vehicle.txd` with their
  measured support).
- Blanks: `data/kit/blanks/kinds.json` (the catalog: dimensions, parts, tier knobs, bike bodies) and
  `automobile.json` (body profiles and the tier defaults; a body's own numbers win over the tier's; `like_body`
  maps vanilla models to bodies, e.g. `landstal` to `suv_boxy`, `bobcat` to `pickup`, `blistac` to `hatchback`).
  `satk.kit.blanks` computes a plan and the mesh (vertices, faces, part ids, UVs, seams, hard edges) with the
  standard library only; the Blender side builds objects `<model>_<piece>` from it, a MIRROR modifier on the half
  bodies and a face attribute `satk_part` that `kit.blank_split` cuts along. The blank is a start for shaping,
  not a result: triangle counts are reported, never targets.
- Paint UVs: up-facing panels lie in the clean top band of `vehiclegrunge256` at u 0.03-0.23 (where vanilla puts
  them; the band right of it holds the dark drips), the sides lower in the grime band that the engine's dirt level
  fades in, the belly at the bottom. Glass, trim and lamps get planar UVs inside their shared atlas regions.
- Tiers: `sa_plus` is the default for new assets; `vanilla` is the lighter loop density.
- Shading order: weld, smooth every face, hard edges by the vanilla rule (an edge is hard where the look changes
  or the author drew a line: material borders, folded UV seams, named creases; corners stay soft, also at 60-90
  degrees), Weighted Normal last; the object is then exported alone and the re-read DFF is compared with the
  shaded mesh. A Weighted Normal on flat faces exports fully flat, so the order matters. Last, the seams between
  parts: a part's border vertex averages only its own faces, so a bonnet edge points up while the wing next to it
  points out - a hard crease along every panel line. Coinciding border corners of two parts take the mean normal
  of both sides when they fold less than the class's hard angle (110 degrees for vehicles) and are baked as custom
  normals (the `satk_wn` modifier of those parts is applied); measured on an exported split blank: the normal
  difference across the bonnet, door and boot cuts went from a median of 68 degrees to 0 (vanilla landstal: 38).
- Export: DragonFF writes the DFF from copies of the frame objects (the scene stays as it was); geometry
  bounding spheres are recomputed in frame space (DragonFF writes them in model space); the vehicle COL is
  embedded as `<model>_col` in exactly ONE clump Extension (DragonFF writes one per collision plus an empty one;
  the game reads only the first, and a second Extension left a vehicle invisible), `col gen` refits it when
  installed and the contact faces of `kit.col` are added to it; the TXD holds only the model's own textures
  (DXT1/DXT3, one level for vehicles, peds and weapons) - the `vehicle.txd` atlases are never copied.
- `--replace SID` names the files after the replaced model and writes a Mod Loader folder with a readme;
  `--add` makes a new model with `mod add` (free id, data lines like the template's model).
- **Map models** (props, buildings, interior props, pickups) are exported with their **prelight**: day and night
  vertex colours on the DFF (ray-cast ambient occlusion with a ground plane under the model, times sun and sky;
  a warm night; pickups get no night set). A mesh that already has colour attributes keeps them
  (`--prelight auto`); `bake` forces the bake, `simple` uses the normals only, `none` writes no colours. The
  face light of the COL comes from the prelight.
- **Collision of small props.** Vanilla map models up to 8 m carry no collision faces, so `--col auto` picks a
  primitive by the class rule in `data/kit/classes.json` (`col_rule`; the exporter measures the size and the closed
  volume of the model): a pickup or a ball-like solid gets one sphere, a solid block (volume above 55 % of its
  bounding box; the bin of the example: 0.60) one box, a thin or open shape up to 3-6 boxes by size. Bigger models
  and the other classes keep their mode (hull for props, mesh for buildings); an explicit `--col box|boxes|spheres|hull|mesh`
  wins. The answer says which rule fired (`col.why`).
- **LOD.** `--lod` on `kit template` (or the first `kit.lod` call) adds the slot `lod<name>` (unique like the
  name, at most 19 characters; a replacement keeps the vanilla LOD's name; `mod add` refuses a LOD name another
  add-on folder defines);
  the export writes the LOD DFF with its own prelight. With `--add`, `mod add` gives it its own IDE line (draw 800,
  the vanilla LOD median, next free id) next to the object's; with `--place X,Y,Z` it also writes the placement
  (`data/maps/<name>.ipl`) in which the HD `inst` holds the index of the LOD `inst`. With `--replace` the LOD file
  is written under its own name: it replaces a vanilla LOD only when it carries that LOD's name.
- `satk blender export` packs its TXD the same way (own textures only, DXT1/DXT3 by the class of the IDE section, no
  RGBA8888 any more).

## Limitations and known issues

- Peds are names-only templates (bones and HAnim ids, stood up on +Z in Blender); `kit export` refuses peds: it
  cannot write the Skin plugin, and an unskinned ped crashes the game. A new ped is a re-skin of a vanilla ped.
- Melee and thrown weapons have their own kind, `weapon_melee` (modelled along +Z like vanilla; exported as a
  weapon). Add-on ids skip the weapon block 321-373 for every kind but weapons.
- Animated map objects keep their frame names; IFP animation is not made.
- The smart UV unwrap of a selection (`uv.unwrap method=smart` with `select`) also unwraps unselected faces
  whose vertices are all selected (the studio's edit-mode selection runs in vertex mode); unwrap such parts as
  separate objects or with `kit.uv_region` until the studio fix lands.
- Atlas regions of `vehicle.txd` are measured rectangles; some labelled ones are rarely used by vanilla cars
  (see their `support`).
- The prelight of a model whose parts hang on moved frames is cast in world space; a part under an overhang of
  another part is darkened by it, which is right for one atomic and approximate for several.
- `mod add --place` writes one placement pair; the position is yours (map placement tools come later).

## Python API (if other packages use it)

```python
from satk.kit import kinds, plan, atlas
kinds.get("automobile")["like"]                       # 'model:426'
p = plan.template_plan(like="model:426", name="mycar")  # frames, presets, COL skeleton (no geometry)
atlas.region("generic.glass_core")["rect"]           # [u0, v0, u1, v1], v down
```
