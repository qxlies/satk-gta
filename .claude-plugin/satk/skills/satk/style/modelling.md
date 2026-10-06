# Modelling in the live Blender session

<!-- Model-facing, English only. Conventions: README.md "How to read the numbers". Session and kit method names
     follow the authoring packages; `blender.methods` lists the exact parameters of the running version. -->

Model IN Blender, step by step, and look after every step. Never generate geometry from hand-typed coordinates
or a private mesh library: agents who did that wrote 1,400-4,250 lines of geometry code, waited minutes per
write-run-look cycle and still produced blocky shapes. In the session one step takes well under a second and
returns the numbers that matter.

## The session loop

1. `blender.session start --name <asset>` (isolated Blender profile, headless; `--gui` opens a
   window with the same profile for the user to watch). `blender.session status|list|stop`.
2. `blender.methods` lists the methods (generic `scene.*`, `mesh.*`, `modifier.*`, `material.*`, `uv.*`,
   `camera.*`, `ref.*`, `io.*`, `shade.basic`, `python`, plus the kit methods `kit.*` and `look.preview`).
3. `blender.call <method> --params {...}` runs ONE step, or a list of steps (`batch`) with one snapshot and, for two
   or more mutating steps, one checkpoint (`--no-checkpoint` skips it). A step has a 90 s budget: `TIMEOUT` = split it,
   `BUSY` = Blender is stuck inside its own code (`blender.session stop`, then start again from the checkpoint).
   `blender.methods --query <name>` returns the parameter table of one method; `studio-methods.md` lists all.
   The reply is small: `{ok, method, changed, stats, snapshot?, checkpoint, ms}`. `stats` holds triangles per
   object against the tier band, `shade.normal_bend` and `shade.flat_share`, an estimate of `dff.verts_per_tri`,
   open and non-manifold edges, `uv.zero_area_share`, dimensions against the class band and warnings. The numbers
   are computed on the evaluated mesh with the same code as `asset.check`.
4. Snapshots are opt-in: a 512 px JPEG (Workbench clay + wire by default, `look=game` for the SA look, rendered
   by the look plug-in; no raw fallback) from named
   cameras (front, side, top, rear, 3q), optionally a 2x2 sheet, a reference overlay or the vanilla ghost.
5. Checkpoints are `.blend` saves (background Blender has no undo): `blender.session restore <n>`; gate tags are
   kept when old checkpoints are pruned. The journal (one JSON line per call) replays the build
   (`blender.session replay`).
6. `python` is the last resort: the code and its hash are journaled and a checkpoint follows. Prefer a method.

Request at most one snapshot per few steps and one sheet per review cycle (SKILL.md image rules).

## Segment counts by tier

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `geo.round_sides[wheel]` | road cars (129) | 12 | 12 | 16 | 16-24 | rim segments |
| `geo.round_sides[arch]` | 12 cars, counted on clay renders | 7 | - | 9 | 12-16 | segments over the half circle; min/max |
| `geo.round_sides[bumper profile]` | 12 cars, counted on clay renders | 2 | - | 4 | 4-6 | rounded nose and bumper profile; min/max |
| `geo.round_sides[round prop]` | hydrant, bin | 8 | 8 | 8 | 12-16 | cylinders with chamfered caps |
| `geo.round_sides[pole]` | street poles | 4 | - | 6 | 8 | min/max |

Vegetation: crossed alpha planes (a palm is 44 triangles) with 128-256 px DXT3 cut-out fronds, in both tiers.

## Rules for every kind

- **Start from the template:** `kit.template --like <SID> --tier <tier> --ghost` (or `--kind <kind>`) builds the
  collections, frame empties in vanilla order, dummies scaled to your dimensions (wheels keep `wheel_scale`),
  empty part slots, material presets with measured values, a collision skeleton and a vanilla ghost tagged
  `satk_ghost` that is never exported. It carries names and numbers only, never vanilla vertices.
- **Scale first:** set the anchor (wheel diameter for vehicles, the 1.84 m ped for everything else) and the class
  dimensions before any detail; check the lineup at gate G1.
- **Author at the target density.** Place the segments you need (table above) when you create a primitive or a
  loop. Do not subdivide and then decimate: decimation leaves slivers and uneven density. Allowed clean-up:
  limited (planar) dissolve on flat areas, merge by distance.
- **One welded shell per body,** parts cut along existing edges, openings closed with jamb strips; small details
  (lamps, mirrors, handles) may be separate flush pieces. Loose slabs read as a voxel model.
- **Density where the outline turns,** long triangles on flats; check `geo.median_dihedral` and `geo.thirds`
  (vehicles) in the step stats.
- **Mirror** symmetric models (MIRROR modifier on X, clipping on) and apply it before cutting left/right parts.
- **Shade with `kit.shade`** (`shading.md`) and keep modifiers live until export; the exporter writes the
  evaluated mesh.
- **UVs on purpose:** `kit.uv_region` into named atlas regions, `uv.unwrap` with method `planar` or `cube`
  (`size` = metres per repeat) for tiling surfaces at the class texel density, `uv.texel` reports px/m; `uv.fit`
  keeps no aspect by default (`keep_aspect` true to keep it); `mesh.lathe` and `uv.unwrap` `cylinder` give round parts
  cylindrical UVs; `mesh.inset` and `mesh.extrude` give new faces real UVs. Never collapse a face onto one texel.

## Modifiers

| Modifier | Use it for | Watch out |
|---|---|---|
| MIRROR | symmetric bodies, props, weapons | apply before splitting left/right parts and before UV2 |
| SUBSURF | one level on curved patches only (roof, bonnet crown, fenders), then apply and dissolve flats | never on the whole body, never as density padding |
| BEVEL | the single chamfer of a prop cap or a bumper lip; vehicle silhouette edges | no bevels on flat walls or buildings |
| SOLIDIFY | thin parts that must read from both sides (spoilers, fins, fences); `mesh.solidify` closes a free sheet into a slab | glass stays single-sided |
| SHRINKWRAP | flush details on a curved body (lamps, badges, trim strips) | keep a small offset against z-fighting |
| WEIGHTED_NORMAL | the last shading step | only after smooth faces and sharp edges |
| Smooth by Angle | marking sharp edges by the dihedral rule | use the class angle of `shading.md`, then un-mark rounded corners |
| ARRAY, CURVE | repeated or bent elements (rails, fence posts, cables, exhaust pipes) | apply before export if counts must be exact |
| DECIMATE | planar mode only, to remove coplanar splits | never as the way to reach a budget |
| TRIANGULATE | checking final triangle counts | the exporter triangulates anyway |

## Reference photos and planes

- `ref.import <photo>` resizes to at most 1,600 px, strips metadata, saves a JPEG and a grid sheet; never open the
  original full-size photo.
- `ref.*` methods put a photo on an image plane scaled from the class dimensions or from two points, or behind a
  named camera; snapshots can overlay it with alpha.
- A photo shows the real car: scale the plane so its wheel matches `wheel_scale` and accept that the body comes
  out 1.1-1.2x the real length, as vanilla cars do.

## Recipes by kind

**Car (automobile; other vehicle types the same with their own template).**
1. `kit.template --like <replaced SID> --tier sa_plus --ghost`; dimensions from the anchored class ratios.
2. Reference planes from the side, front and top photos; cameras front, side, 3q.
3. Half body with MIRROR: start from `kit.blank --kind automobile --like <SID>` (a clean quad half shell with loops
   at arches, belt line, pillars and bumpers, part regions and UV seams; `kit.blank_split` cuts the parts into their
   slots) and move its loops with `mesh.transform`; the same blank exists for bike, boat, heli and plane. By hand: the
   side profile as a few long horizontal bands (sill, lower door, beltline, window frame), extruded along the length; wheel arches as arcs with the tier's segment count; nose and tail profiles
   with 2-4 (vanilla) or 4-6 (`sa_plus`) segments; cabin and glass faces on their own materials.
4. Snapshot at G1 with `blender.preview session:<name> --lineup class`; show the user.
5. Interior (seat boxes, dash slab, steering wheel), lamp recesses with flat lens faces, grille surround, mirrors.
6. Cut doors, bonnet, boot and bumpers into their slots along existing edges; jamb strips; hinges at the dummy
   origins.
7. `kit.shade`; `kit.wheel`; `kit.vlo`; `kit.damage`; `kit.col` (or `col.gen`); `kit.material_preset` for paint
   (grunge, UV2 sheen), lamps, glass, chrome, trim, interior, plate.
8. UVs: `kit.uv_region` for trim, glass, lamps, tyres; paint on `vehiclegrunge256` with V following the height;
   own interior and rim textures (`textures.md`).
9. `kit.export --replace <SID>` (or `--add`): DFF with frame-local bounding spheres, embedded COL3 + shadow, TXD of
   own textures only, Mod Loader folder, re-import diff, lint and `asset.check`.

**Street prop.** `kit.blank --kind prop_cyl|prop_box` or primitives with explicit segments (cylinders 8 vanilla, 12-16 `sa_plus`), at most one chamfer on
the silhouette caps, 1-2 materials, one 64-128 px texture, no normals in game: bake occlusion into the prelight
(`kit.bake` masks, `texture.finish --edge --role prop`, then `kit.export`, which writes prelight and a primitive COL
by the class rule; `blender.game_ready --asset-class prop` for a mesh made outside the kit), draw 100 or less.

**Building with LOD.** `kit.blank --kind building_box`, or boxes and setbacks with thin extruded ledges and cornices; windows, doors and brickwork
only in tiling textures (`uv.span` 3-6); 4-7 materials; dark grey day prelight with occlusion, warm night colours
with lit windows; collision mesh at about a quarter of the render faces (`col.gen`); draw at most 299; LOD with
`kit.lod` (about 0.2 of the triangles, 32-64 px textures in a shared LOD TXD, `lod` + name without its first 3
characters, draw about 800).

**Interior.** Shells face inwards and are prelit; interior props may carry normals; floors get FLOORBOARD or CARPET
surfaces; keep entry markers in mind (`limits.md`).

**Weapon.** Real length against the 1.84 m ped; firearms with hard edges on receivers and slides, melee smooth;
one 64 px (`sa_plus` 128 px) photo-like texture on both sides; the `gunflash` atomic from the template.

**Ped.** Re-skin the vanilla mesh or edit vertices while keeping the 32-bone skin, one material and at most 4
weights per vertex; a new skeleton is out of scope.

**Pickup.** Small (0.2-1 m), one 32-64 px saturated texture, prelit, one collision sphere, draw 40-100.

**Vehicle upgrade.** Dynamically lit like the car (normals, no prelight), 1-2 materials, frames named for the
`ug_*` anchors of the cars that take it.

## Anti-patterns

- Hand-typed vertex lists, private loft or spline libraries, geometry rebuilt from scratch by a script each run.
- Subdivide-then-decimate density; uniform grid tessellation; triangles in the middle of flat panels.
- Boolean soup and unwelded slabs; floating details; deep black cavities around small wheels.
- Detail in geometry that vanilla puts in textures (window frames, bolts, grille mesh, tread, panel gaps on map
  models).
