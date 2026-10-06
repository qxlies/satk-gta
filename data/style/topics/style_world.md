# SA style: map objects (props, buildings, LODs, terrain, interiors, vegetation, pickups)

About 100-500 triangles WHATEVER the size; detail from tiling 128/256 px DXT1 photo textures and baked vertex
colours; NO normals; collision far simpler than the mesh; HD with collision drawn to <= 299 m; LOD ~0.2 of the
tris, 32-64 px textures, drawn to ~800 m. Bands p10 / p50 / p90 over models of the class; sa_plus = PROPOSAL.

## Size buckets (largest bbox side; tris p10 / p50 / p90, texel px/m p50, median edge m)
- 0.5-1 m (389): 26 / 128 / 560, 228 px/m, 0.15 m, texture 128, 2 mats, draw 35.
- 1-2 m (685): 12 / 128 / 352, 138 px/m, 0.28 m. 2-4 m (673): 12 / 133 / 432, 94 px/m, 0.41 m.
- 4-8 m (464): 12 / 137 / 817, 72 px/m. 8-16 m (666): 24 / 212 / 1,150, 64 px/m, 3 mats.
- 16-32 m (860): 46 / 342 / 1,399, 53 px/m, 5 mats, col faces 25. 32-64 m (1,089): 53 / 440 / 1,257, 35 px/m.
- 64-128 m (1,554): 72 / 353 / 1,411, 37 px/m, texture 256. 128-256 m (1,697): 71 / 280 / 1,065, 33 px/m.
- sa_plus: tris up to 2x the bucket p90; texel 1.5-2x the bucket p50; texture 256 typical, 512 only for terrain
  or a landmark. A uniform 32 px/m is right for buildings/terrain, 3-7x too low for props.

## Classes (tris p10 / p50 / p90, materials p50)
street_prop (699) 10 / 84 / 324, 1; building_small (734) 21 / 210 / 752, 4; building_medium (1,117) 72 / 484 /
1,163, 7; building_large (1,371) 78 / 504 / 1,643, 6; terrain_road (2,179) 66 / 236 / 745, 4; vegetation (314)
40 / 148 / 336; interior_prop (1,413) 26 / 144 / 570; interior_shell (672) 59 / 647 / 2,918; lod (4,349) 12 / 60
/ 262; pickup (28) 40 / 159 / 224; vehicle_upgrade (194) 31 / 161 / 575 (normals, no prelight).
Never above ~3,000 tris in a map model. Silhouette only: median edge ~1/10 of the size; windows, frames, bolts,
panel gaps, brickwork are texture. One geometry, one atomic; material colour white; 1 texture per material.

## Textures
DXT1 128 (props, small buildings) or 256 (large buildings, terrain), 64 for details and pickups; DXT3 for real
alpha, DXT1 1-bit for cut-outs, never DXT5. Buildings and terrain TILE: `uv.span` p50 3.0 (medium), 4.5 (large),
6.3 (terrain); props use 0..1 UVs. Texture luminance p50 110-126 of 255, saturation p50 0.10-0.14 (mid-grey,
desaturated; vegetation 0.33, pickups 0.41). Window/facade textures are modular (one bay, one storey). TXDs:
street props 1 model / 2 textures / 16 KB; buildings 2-3 models / 7-13 textures; LOD TXDs shared by ~15 models.

## Prelight and night (per-model median day luminance p10 / p50 / p90, of 255)
- Buildings 13-23 / 47-60 / 104-132; terrain 34 / 100 / 143; street props 7 / 62 / 152; interior props 35 / 91 /
  145. Nearly grey (mean day RGB 79, 78, 75). Near-white vertices: 0 % in the median building. Bake occlusion:
  dark eaves, wall bases, recesses; 8-15 % of a median building's vertices near black.
- Night colours DARKER and WARM: night/day ratio p50 0.36-0.43 (buildings), 0.30 (terrain); R-B per model
  -4.9 / 5.1 / 29.7 (50 % warm, 9.7 % cool); unlit night ~(30, 27, 23). Bake light pools and lit windows into
  the night set (15 % of map vertices are brighter at night; 71-82 % of buildings).
- `blender.game_ready --asset-class <class>` applies warm nights, texel by size, draw clamp.

## Flags, draw, LOD
- Alpha parts -> draw_last (4) in a separate `*_alpha` model without collision; foliage 2097284 (+ tree 8192 or
  palm 16384); roads is_road (1); night windows `tobj` flags 140, hours 20-6.
- Big-building rule: LOD children or `1.0 x draw > 300` -> collision OFF and never streamed out. HD with collision:
  draw <= 299 (1,090 vanilla models sit at 299). Draw p50: props 100 (interior 30), buildings 100-150, terrain 200.
- LOD for exterior models from ~30 m (72-74 % of medium/large buildings, 95 % of terrain): `lod.ratio` 0.05 /
  0.21 / 0.70, 32-64 px DXT1 in a shared LOD TXD, no collision, prelight + night, name `lod` + HD name[3:],
  draw 450 / 800 / 1,500 (`kit.lod`).

## Collision
Street props: primitives (67 %); buildings: mesh or mixed, faces/tris p50 0.23-0.39; terrain ~1:1 (0.89).
Surfaces: buildings DEFAULT; terrain by ground (TARMAC, PAVEMENT, DIRT...); props by material. Face light: low
nibble day, high nibble night; dominant 15 (day 15 / night 0); 0 on 5.4 % only (0 = ambient only and car
headlights on). Make it with `col.gen` (face light from prelight), check with `col.check`; one `.col` per pack.

## 2DFX, interiors, pickups
Lights in the DFF 2DFX (corona 1.0, range 18, far clip 100 on street props), never IDE `2dfx` lines; `fx2d.check`.
Interior shells prelit, props 42 % with normals, no shadow meshes, FLOORBOARD/CARPET floors; 24 free entry
markers. Pickups 0.2-1 m, 32-64 px saturated, prelit, one sphere, draw 40-100.

Full guide: docs/agent/style/world.md
