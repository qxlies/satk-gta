# World: props, buildings, LODs, terrain, interiors, vegetation, pickups

<!-- Model-facing, English only. Conventions: README.md "How to read the numbers". Measured on the 14,620 active
     non-vehicle models of the clean 1.0 US copy (profile vanilla), 14,578 DFFs and their collision. Classes are
     heuristic: IDE section, flags, size and interior/exterior. Peds and weapons: peds-weapons.md. -->

A vanilla map model has **about 100-500 triangles whatever its size**: size changes the density, not the count.
Detail comes from **tiling 128 and 256 px DXT1 photo textures** and from **baked vertex colours**: a dark, nearly
grey day prelight and a darker, warmer night set with lit windows and lamp pools. Map geometry carries **no
normals**. Collision is far simpler than the mesh. Every HD model with collision is drawn to **at most 299 m**;
its LOD has about a fifth of the triangles and 32-64 px textures and is drawn to about 800 m.

## Budgets by size bucket

Size = the largest bounding box side (`dims.size`). HD map models only (no overlays, no LODs). Cells are p50 unless
the header says `p10 / p50 / p90`.

| Size bucket | n | `geo.tris` p10 / p50 / p90 | `geo.tris_per_m2` | `geo.median_edge_m` | `uv.texel_px_m` p10 / p50 / p90 | `tex.side_px` | `mat.count` | `ide.draw` | `col.mesh_faces` |
|---|---|---|---|---|---|---|---|---|---|
| 0-0.5 m | 234 | 12 / 80 / 272 | 773 | 0.05 | 98 / 359 / 978 | 64 | 1 | 100 | 0 |
| 0.5-1 m | 389 | 26 / 128 / 560 | 101 | 0.15 | 93 / 228 / 716 | 128 | 2 | 35 | 0 |
| 1-2 m | 685 | 12 / 128 / 352 | 26.3 | 0.28 | 64 / 138 / 278 | 128 | 2 | 30 | 0 |
| 2-4 m | 673 | 12 / 133 / 432 | 11.6 | 0.41 | 44 / 94 / 248 | 128 | 2 | 50 | 0 |
| 4-8 m | 464 | 12 / 137 / 817 | 3.04 | 0.88 | 28 / 72 / 171 | 128 | 2 | 80 | 0 |
| 8-16 m | 666 | 24 / 212 / 1,150 | 1.25 | 1.27 | 24 / 64 / 153 | 128 | 3 | 100 | 2 |
| 16-32 m | 860 | 46 / 342 / 1,399 | 0.53 | 1.79 | 15 / 53 / 126 | 128 | 5 | 100 | 25 |
| 32-64 m | 1,089 | 53 / 440 / 1,257 | 0.21 | 2.73 | 13 / 35 / 86 | 128 | 6 | 120 | 69 |
| 64-128 m | 1,554 | 72 / 353 / 1,411 | 0.09 | 5.00 | 14 / 37 / 80 | 256 | 5 | 150 | 122 |
| 128-256 m | 1,697 | 71 / 280 / 1,065 | 0.04 | 9.33 | 10 / 33 / 77 | 256 | 4 | 200 | 172 |
| over 256 m | 397 | 99 / 373 / 1,092 | 0.01 | 15.0 | 7 / 16 / 62 | 256 | 6 | 299 | 291 |

`sa_plus` (proposal): triangles up to 2x the bucket p90; texel density 1.5-2x the bucket p50; texture side 256
typical, 512 only for terrain or a landmark. LODs, collision and draw distances stay vanilla.

Rules:
- Stay inside p10..p90 of your size bucket; above the class p90 needs a reason (a landmark); above 2x p90 is not SA
  style. Vanilla never goes above about 3,000 triangles in a map model (interior shells reach 2,918 at p90).
- Spend triangles on the silhouette only. The median edge is about a tenth of the object size. No modelled window
  frames, bolts or panel gaps on map models: they are in the texture (prop bevels: see README "Reconciled rules").
- Materials: 1-2 on props, 4-7 on buildings. One texture per material; the material colour is white (RGBA 255 on
  93-99.8 % of map materials); untextured materials are rare.
- One geometry and one atomic per map model; several atomics only for breakables and damage states.
- Vanilla map geometry is tristripped; triangle lists load fine (a size convention, not a look).

## Budgets and look by class

| Class | n | `dims.size` | `geo.tris` p10 / p50 / p90 | `mat.count` | `tex.side_px` | `uv.texel_px_m` p25 / p50 / p75 | `uv.span` p10 / p50 / p90 | `ide.draw` p10 / p50 / p90 | `light.night_models_share` | `light.normal_models_share` |
|---|---|---|---|---|---|---|---|---|---|---|
| street_prop | 699 | 2.5 | 10 / 84 / 324 | 1 | 128 | 49 / 83 / 166 | 0.94 / 1.0 / 4.0 | 35 / 100 / 100 | 0.60 | 0.08 |
| small_static | 154 | 3.2 | 10 / 80 / 501 | 2 | 128 | 59 / 83 / 166 | - | - | 0.84 | 0.04 |
| building_small | 734 | 15.2 | 21 / 210 / 752 | 4 | 128 | 29 / 59 / 99 | 1.0 / 2.2 / 7.8 | 50 / 100 / 250 | 0.98 | 0.01 |
| building_medium | 1,117 | 45.5 | 72 / 484 / 1,163 | 7 | 128 | 21 / 35 / 59 | 1.0 / 3.0 / 8.0 | 80 / 120 / 220 | 0.99 | 0.01 |
| building_large | 1,371 | 117 | 78 / 504 / 1,643 | 6 | 256 | 17 / 29 / 59 | 1.0 / 4.5 / 9.0 | 100 / 150 / 290 | 0.99 | 0.01 |
| terrain_road | 2,179 | 162 | 66 / 236 / 745 | 4 | 256 | 17 / 42 / 70 | 1.0 / 6.3 / 8.0 | 150 / 200 / 299 | 1.00 | 0.00 |
| vegetation | 314 | 12.4 | 40 / 148 / 336 | 2 | 128 | 21 / 49 / 99 | 0.98 / 1.0 / 6.9 | 45 / 99 / 299 | 0.98 | 0.01 |
| overlay_alpha | 631 | 129 | 10 / 72 / 320 | 1 | 128 | 10 / 17 / 42 | 1.0 / 1.0 / 8.0 | - | 0.96 | 0.00 |
| time_object | 136 | 114 | 48 / 148 / 514 | 1 | 128 | 9 / 15 / 25 | - | - | 0.90 | 0.01 |
| interior_prop | 1,413 | 1.5 | 26 / 144 / 570 | 2 | 128 | 99 / 166 / 279 | 0.94 / 1.0 / 3.0 | 30 / 30 / 100 | 0.63 | 0.42 |
| interior_shell | 672 | 22.3 | 59 / 647 / 2,918 | 5 | 128 | 35 / 70 / 117 | 1.0 / 2.0 / 7.9 | 30 / 100 / 299 | 0.75 | 0.02 |
| lod | 4,349 | 120 | 12 / 60 / 262 | 2 | 64 | 1.3 / 2.2 / 3.7 | 1.0 / 3.7 / 8.0 | 450 / 800 / 1,500 | 1.00 | 0.00 |
| pickup | 28 | 0.5 | 40 / 159 / 224 | 1 | 64 | 83 / 140 / 166 | 0.61 / 1.0 / 1.02 | 40 / 100 / 100 | 0.07 | 0.14 |
| vehicle_upgrade | 194 | 2.0 | 31 / 161 / 575 | 2 | 128 | 25 / 42 / 83 | 0.11 / 0.45 / 0.99 | - | 0.00 | 1.00 |

Texel density here is pooled per class (area-weighted, p25 / p50 / p75 over models); use the size bucket table
for a single model. Props, interior props, pickups, weapons and peds use 0..1 unique UVs (`uv.span` about 1);
buildings and terrain tile: 65-87 % of their materials repeat more than 1.5x.

## Textures

- Default to DXT1 128x128 (props, small buildings) or 256x256 (large buildings, terrain); 64x64 for small details
  and pickups; 512 only for terrain or a landmark. Non-square 2:1 is fine. Alpha: DXT3 for real alpha
  (vegetation, fences, signs, overlays), DXT1 with 1-bit alpha for cut-outs; never DXT5 (`textures.md`).
- Window and facade textures are modular units (one bay, one storey) tiled by UVs. Districts differ: Los Santos
  stucco, peeling paint, shingles, chain-link, graffiti; San Fierro Victorian and brick facades, bay windows,
  cornices; Las Venturas concrete, desert scrub, sand, car-park asphalt, neon accents.
- Grime is in the texture: stains below windows, water marks, dirty bases, cracks. Let the prelight do the
  darkening; textures stay mid-grey (`tex.lum_mean` in `textures.md`).

TXD organisation (every map TXD carries its own copies; there are no parent TXDs):

| Class | n | `txd.models` p50 | `txd.textures` p50 | `tex.txd_kb` p10 / p50 / p90 |
|---|---|---|---|---|
| street_prop TXDs | 308 | 1 | 2 | 3 / 16 / 80 |
| building_small TXDs | 223 | 2 | 7 | 20 / 94 / 423 |
| building_medium TXDs | 355 | 2 | 12 | 47 / 226 / 540 |
| building_large TXDs | 364 | 3 | 13 | 50 / 252 / 634 |
| terrain_road TXDs | 273 | 4 | 12 | 70 / 344 / 1,030 |
| interior_prop TXDs | 264 | 3 | 5 | 8 / 68 / 230 |
| lod TXDs | 133 | 15 | 21 | 2 / 40 / 168 |

## Prelight, night colours and normals

Map objects of 3 m and more are 99-100 % prelit with day and night colours and practically never carry normals;
the engine adds the time-cycle object ambient on top, so the prelight is the occlusion and sun term, not the full
colour. Small props are 61-77 % prelit; interior props carry normals in 42 % of models.

| Class | n | `light.prelit_lum_p50` p10 / p50 / p90 | `light.prelit_lum_std` p50 | `light.dark_vertex_share` p50 | `light.white_vertex_share` p90 | `light.night_ratio` p10 / p50 / p90 | `light.lit_vertex_share` p50 |
|---|---|---|---|---|---|---|---|
| street_prop | 699 | 7 / 62 / 152 | 38 | 0.12 | 0.17 | 0.18 / 0.46 / 1.0 | 0.0 |
| building_small | 734 | 13 / 47 / 104 | 55 | 0.15 | 0.04 | 0.11 / 0.43 / 1.35 | 0.14 |
| building_medium | 1,117 | 20 / 51 / 110 | 54 | 0.13 | 0.02 | 0.11 / 0.39 / 1.09 | 0.12 |
| building_large | 1,371 | 23 / 60 / 132 | 50 | 0.08 | 0.01 | 0.09 / 0.36 / 1.05 | 0.09 |
| terrain_road | 2,179 | 34 / 100 / 143 | 40 | 0.01 | 0.01 | 0.11 / 0.30 / 0.84 | 0.02 |
| vegetation | 314 | 1 / 56 / 126 | 45 | 0.13 | 0.0 | 0.04 / 0.29 / 1.0 | 0.04 |
| interior_prop | 1,413 | 35 / 91 / 145 | 36 | 0.05 | 0.02 | 0.23 / 0.65 / 1.0 | 0.0 |
| interior_shell | 672 | 15 / 61 / 153 | 48 | 0.12 | 0.22 | 0.36 / 0.87 / 1.07 | 0.09 |
| lod | 4,349 | 32 / 84 / 141 | 38 | 0.0 | 0.0 | 0.09 / 0.29 / 0.80 | 0.01 |

- **Day prelight is dark and nearly grey**: mean day RGB over the vertices of all night-coloured models
  (79, 78, 75). A white or un-baked prelight glows next to vanilla. Bake ambient occlusion: dark under eaves, at
  wall bases, inside recesses; upper walls lighter. 8-15 % of the vertices of the median building are near black.
- **Night colours are darker and warm, not blue**: unlit night vertices average (30, 27, 23).
- **Light pools and lit windows** go into the night set: 15 % of all map vertices are brighter at night than by
  day (night (97, 93, 76) against day (38, 37, 34) on those vertices); 71-82 % of buildings have them. Time
  objects (`tobj`, night window planes) are the additive version.
- Vertex alpha is almost unused (0.3-2 % of buildings, 22 % of alpha overlays, 42 % of time objects for fades).

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `light.night_tint` | night-coloured map models (12,877) | -4.9 | 5.1 | 29.7 | same | R - B; 50 % warm (above +5), 9.7 % cool (below -5) |

`blender.game_ready --asset-class <class>` sets these defaults (warm night prelight, texel density by size bucket, draw
clamped for HD models with collision); check the result with `asset.check`.

## IDE flags and transparency

| Fact | Value | Source |
|---|---|---|
| flag shares (14,259 objs/tobj/anim models) | dont_receive_shadows (128) 35.7 %, draw_last (4) 16.4 %, disable backface culling (2097152) 10.8 %, is_road (1) 5.4 %, no_zbuffer_write (64) 1.8 %, tree (8192) 0.7 %, additive (8) 0.6 % | vanilla index |
| alpha parts | draw_last on 56-63 % of buildings with alpha textures, 96 % of vegetation, 100 % of overlays; put alpha parts into a separate `*_alpha` model without collision | vanilla index |
| foliage | draw_last + dont_receive_shadows + no culling (2097284), plus tree (8192) or palm (16384) for wind sway | vanilla index |
| roads | is_road (1) on 30 % of terrain and road models (wet reflections) | vanilla index |
| night windows | `tobj` with 140 = draw_last + additive + dont_receive_shadows, hours 20-6 (78 of 160) | vanilla index |

## Draw distance and LOD

- **The big-building rule:** a building with LOD children, or whose `LODDistMultiplier (1.0) x draw` is above
  300, becomes a "big building": its collision is switched off and it is never streamed out. So an HD model with
  collision is drawn to at most 299 m: 1,090 vanilla HD models sit exactly at 299; only 128 of 9,909 HD models
  exceed 300, almost all without collision (night windows, overlays).
- A LOD for every exterior model of about 30 m and more: 34 % of small, 72 % of medium, 74 % of large buildings
  and 95 % of terrain have one; props, vegetation and interiors almost never.
- LOD textures: 32x32 or 64x64 DXT1, no mips, in shared LOD TXDs (`lod_countn2`, `laeast2_lod`, `vegaselod2`);
  LODs bake their own low-res textures (only 17 % of pairs share a texture name). LODs have no collision, prelight
  and night colours always, no normals. One LOD per HD model.
- Naming: 95 % start with `lod`; the usual form is `lod` + the HD name without its first 3 characters
  (`shabbyhouse03_lvs` -> `lodbbyhouse03_lvs`), within the 23-character limit.

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `lod.ratio` | HD/LOD pairs (4,311) | 0.05 | 0.21 | 0.70 | vanilla LOD budget | LOD tris / HD tris |
| `ide.draw[hd with lod]` | HD models with a LOD (4,300) | 100 | 160 | 299 | same | |
| `ide.draw[map without lod]` | map models without a LOD (2,431) | 45 | 100 | 290 | same | |

## Collision

- Kind by class (primitives only / mesh only / mixed / none): street props 67 / 10 / 6 / 17 %, buildings small 23 /
  29 / 44 / 5 %, medium 14 / 32 / 52 / 2 %, large 6 / 47 / 45 / 2 %, terrain 1 / 90 / 9 / 0 %, vegetation 55 / 10 / 3
  / 32 % (bushes and grass have none), interior props 57 / 4 / 1 / 38 %; overlays and LODs none; pickups and
  weapons one sphere.
- Mesh collision is much coarser than the render mesh on buildings; terrain is collided almost 1:1.
- Surfaces: buildings DEFAULT on 88-91 % of faces; terrain by ground type (DEFAULT 29 %, TARMAC 14 %, PAVEMENT
  11 %, ROCK_DRY 6 %, DIRT 6 %); props by material (METAL_DUMPSTER, WOOD_BENCH, LAMP_POST ...); interiors FLOORBOARD,
  CARPET.
- Shadow mesh (COL3): 8 % of large buildings, 19 % of vegetation, 0 on interiors.
- Make collision with `col.gen` (box, boxes, hull, mesh; surfaces from textures; face light from the prelight) and
  check it with `col.check`; one `.col` archive per pack (`limits.md`).

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `col.mesh_ratio[building_small]` | building_small (734) | 0.04 | 0.23 | 1.0 | same | faces / render tris |
| `col.mesh_ratio[building_large]` | building_large (1,371) | 0.07 | 0.39 | 1.0 | same | |
| `col.mesh_ratio[terrain_road]` | terrain_road (2,179) | 0.36 | 0.89 | 1.0 | same | |
| `col.mesh_ratio[vegetation]` | vegetation (314) | 0.18 | 0.40 | 0.73 | same | |

| Fact | Value | Source |
|---|---|---|
| face light byte | low nibble = day 0..15, high nibble = night 0..15 | engine (collision lighting) |
| dominant face light of mesh collisions | 15 (day 15, night 0) 744 models, 255 (15/15) 624, 111 (15/6) 509, 47 (15/2) 463, 31 (15/1) 420; 0 on 5.4 % only | vanilla index |
| what 0 means | peds, vehicles and objects on the face get only ambient light (about 3x darker by day) and cars switch on their headlights; it does not make them black | engine (renderer, vehicle) |
| primitives | spheres and boxes mostly keep 0 (70-74 % in every class) | vanilla index |

## 2D effects

Lights (corona `coronastar`): street props corona 1.0, range 18, far clip 100, shadow 8; buildings corona 1-1.5,
range 18, far clip 200; interiors corona 0.3, range 2-6. Ped attractors (benches, ATMs, phones), cover points on
25-41 % of buildings, particles on 2.4 % of street props. Put 2DFX into the DFF (never IDE `2dfx` lines:
`limits.md`), edit them as JSON with `fx2d.*` and validate with `fx2d.check`.

## Interiors and pickups

- Interiors: `interior_prop` and `interior_shell` rows above; interior props are lit dynamically more often (42 %
  carry normals), shells are prelit; no shadow meshes; floors use FLOORBOARD or CARPET surfaces; entry markers are
  scarce (`limits.md`).
- Pickups: 0.2-1 m, 32 or 64 px DXT1, saturated (`tex.sat_mean` in `textures.md`), prelit (no night colours in
  93 %), flags 128, draw 40 or 100, one collision sphere.
