# Vehicles: budgets, scale, frames, materials, dirt, damage, LOD and collision

<!-- Model-facing, English only. Conventions: README.md "How to read the numbers". Measured on all 212 vehicles of
     the clean 1.0 US copy (profile vanilla). Shading of vehicles: shading.md. -->

A vanilla car is a shell of about 2,200 triangles (wheel counted once) split into about 22 geometries, lit
dynamically (normals, no prelight), painted with flat key colours that the engine recolours, over the large shared
textures of `vehicle.txd` (`vehiclegeneric256` and `vehiclegrunge256` cover about 70 % of the visible surface),
with only 2-3 small own textures (interior 128 px, wheel 64 px). The look comes from soft normals, the paint and
dirt system, the environment sheen and the shared photo textures, not from geometric detail.

## Peer sets

Body classes are curated (the IDE `class` column is a spawn class such as `richfamily`, not a body type).
Road cars = the 129 automobiles of sedan, wagon, coupe_muscle, sports, suv_pickup, van, truck_bus and emergency.
"Cars" = the 144 `vehicles.ide` entries of type `car`. The sedan class (24): admiral, cabbie, elegant, emperor,
glendale, glenshit, greenwoo, intruder, merit, nebula, oceanic, premier, primo, romero, sentinel, stafford,
stretch, sultan, sunrise, tahoma, taxi, vincent, washing, willard. `style.profile --like model:<id>` picks the
class of a model.

## Budgets

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `veh.hd_tris` | cars (144) | 1,676 | 2,168 | 2,473 | 3,000-4,500, cap 5,000 | wheel once |
| `veh.hi_tris[road car]` | road cars (129) | 2,380 | 2,785 | 3,275 | - | max 3,912 |
| `veh.hi_tris[sedan]` | sedan (24) | 2,439 | 2,650 | 2,861 | 3,710-5,565 | |
| `veh.hi_tris[wagon]` | wagon (4) | 2,580 | 2,806 | 2,960 | - | |
| `veh.hi_tris[coupe_muscle]` | coupe_muscle (28) | 2,387 | 2,712 | 3,015 | 3,797-5,695 | |
| `veh.hi_tris[sports]` | sports (19) | 2,476 | 2,847 | 3,196 | 3,986-5,979 | |
| `veh.hi_tris[suv_pickup]` | suv_pickup (13) | 2,394 | 2,769 | 2,860 | 3,877-5,815 | |
| `veh.hi_tris[van]` | van (13) | 2,152 | 2,506 | 3,045 | 3,508-5,263 | |
| `veh.hi_tris[truck_bus]` | truck_bus (15) | 2,088 | 3,083 | 3,604 | 4,316-6,474 | |
| `veh.hi_tris[emergency]` | emergency (13) | 2,277 | 2,796 | 3,186 | 3,914-5,872 | |
| `mat.count[road car]` | road cars (129) | 20 | 23 | 27 | same | unique materials |
| `tex.own_count[road car]` | road cars (129) | 2 | 2 | 4 | at most 4: interior 256, wheel 128, detail 128 | own TXD textures |
| `tex.txd_kb[road car]` | road cars (129) | 8 | 10 | 27 | about 4x, from the texture sizes below | own TXD |

Per part (`_ok` = undamaged part; `_dam` parts: see Damage):

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `part.tris[chassis]` | cars (144) | 1,152 | 1,398 | 1,819 | 2,000-3,000 | roof, sides, interior, glass, lamps |
| `part.tris[interior]` | premier, sentinel | 131 | - | 183 | 250-450 | inside the chassis; min/max |
| `part.tris[wheel]` | cars (141) | 134 | 158 | 236 | 240-400 | one mesh, cloned 4x |
| `part.tris[door_f_ok]` | cars (130) | 57 | 104 | 148 | 120-220 | |
| `part.tris[door_r_ok]` | cars (47) | 55 | 84 | 102 | - | |
| `part.tris[bonnet_ok]` | cars (121) | 24 | 48 | 88 | 72-120 | |
| `part.tris[boot_ok]` | cars (93) | 22 | 42 | 129 | 63-105 | |
| `part.tris[bump_front_ok]` | cars (128) | 57 | 100 | 175 | 150-300 | rounded profiles are the densest parts |
| `part.tris[bump_rear_ok]` | cars (114) | 55 | 101 | 169 | 150-300 | |
| `part.tris[windscreen_ok]` | cars (127) | 5 | 12 | 20 | 12-30 | curvature only |
| `part.tris[exhaust_ok]` | cars (97) | 16 | 16 | 48 | same | |
| `part.tris[extra]` | road cars (129) | 8 | 68 | 177 | - | |
| `part.tris[chassis_vlo]` | cars (141) | 60 | 80 | 140 | 55-130 (`kit.vlo` aims at 130 or less) | the vehicle LOD |

Materials per geometry (one material = one draw call; keep the counts):

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `mat.per_geom[chassis]` | road cars (129) | 13 | 16 | 19 | same | |
| `mat.per_geom[door_lf_ok]` | road cars (129) | 5.6 | 7 | 9 | same | |
| `mat.per_geom[bonnet_ok]` | road cars (129) | 1 | 1 | 2 | same | |
| `mat.per_geom[boot_ok]` | road cars (129) | 1 | 2 | 7 | same | |
| `mat.per_geom[bump_front_ok]` | road cars (129) | 1 | 2 | 4 | same | |
| `mat.per_geom[windscreen_ok]` | road cars (129) | 1 | 1 | 1 | same | |
| `mat.per_geom[wheel]` | road cars (129) | 2 | 3 | 4 | same | tyre, rim, hub |
| `mat.per_geom[chassis_vlo]` | road cars (129) | 2 | 4 | 6 | same | |

## Where the triangles go

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `geo.thirds[chassis]` | road cars | - | 41 / 23 / 36 % | - | same split | front / middle / rear, pooled |
| `geo.median_dihedral[chassis]` | road cars (129) | 7.8 | 10.7 | 19 | same | below 2 deg = wasted on flat skin |
| `geo.median_dihedral[ok part]` | road cars (129) | 1.8 | 7.2 | 28 | same | |
| `geo.tris_per_m2[chassis]` | road cars (129) | 8.6 | 21 | 29 | - | |
| `geo.tris_per_m2[wheel]` | road cars (129) | 63 | 88 | 118 | - | agent wheels had 178-211 |
| `geo.median_edge_m[chassis]` | cars (144) | 0.15 | 0.23 | 0.29 | - | big quads on flat panels |
| `geo.pieces[chassis]` | cars (144) | 27 | 39 | 78 | same | lamps, mirrors, interior bits apart |
| `geo.largest_piece_share[chassis]` | cars (144) | 0.36 | 0.74 | 0.82 | same | one welded body shell |
| `geo.sliver_share[car]` | cars, HD parts (144) | 0.18 | 0.23 | 0.30 | same | min angle below 8 deg |
| `geo.open_edge_share[chassis]` | road cars (129) | 0.12 | 0.16 | 0.23 | same | several open shells, not watertight |

- By material, chassis triangles go to trim, chrome, black plastic and underbody on `vehiclegeneric256` (about
  42 %), body paint (31 %), interior (10 %), colour-coded generic (6 %), lamps (4 %), glass (1.5 %).
- Noses and tails (bumpers, lamps, grille, plate recess) carry twice the density of the cabin section. Sides are a
  few long horizontal bands running the full length: sill, lower door, beltline, window frame. A flat door skin is
  2-6 long triangles.

## Scale and proportions

The wheel is the anchor: the wheel mesh diameter equals the IDE `wheel_scale` (the engine uses half of it as the
physics radius), 0.70 m on most cars; every other dimension follows from the class ratios below. SA cars are
oversized against their real counterparts, wheels stay real size.

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `dims.L[sedan]` | sedan (24) | 5.50 | 5.82 | 6.15 | same | metres |
| `dims.W[sedan]` | sedan (24) | 2.11 | 2.27 | 2.44 | same | chassis; mirrors on doors excluded |
| `dims.H[sedan]` | sedan (24) | 1.48 | 1.53 | 1.70 | same | |
| `dims.wheelbase[sedan]` | sedan (24) | 3.16 | 3.55 | 3.79 | same | |
| `dims.track[sedan]` | sedan (24) | 1.79 | 1.87 | 2.01 | same | |
| `dims.wheel_d[sedan]` | sedan (24) | 0.69 | 0.70 | 0.75 | same | IDE `wheel_scale` |
| `dims.wheel_ratio[road car]` | road cars (129) | 0.99 | 1.00 | 1.03 | same | wheel mesh diameter / `wheel_scale` |
| `dims.origin_z[road car]` | road cars (129) | 0.60 | 0.80 | 1.15 | same | origin = body centre |
| `dims.real_ratio[L]` | 5 sedans with known counterparts | 1.01 | 1.12 | 1.20 | same | min/mean/max |

Anchored proportions (dimension / wheel diameter): build these first, then scale the whole car so the wheel is
`wheel_scale`.

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `dims.L_rel[sedan]` | sedan (24) | 7.68 | 8.19 | 9.04 | same | |
| `dims.W_rel[sedan]` | sedan (24) | 3.00 | 3.17 | 3.46 | same | |
| `dims.H_rel[sedan]` | sedan (24) | 2.04 | 2.19 | 2.44 | same | |
| `dims.wheelbase_rel[sedan]` | sedan (24) | 4.43 | 4.88 | 5.43 | same | |
| `dims.track_rel[sedan]` | sedan (24) | 2.45 | 2.64 | 2.85 | same | |
| `dims.L_rel[coupe_muscle]` | coupe_muscle (28) | 6.61 | 7.77 | 8.97 | same | |
| `dims.H_rel[coupe_muscle]` | coupe_muscle (28) | 1.91 | 2.11 | 2.26 | same | |
| `dims.wheelbase_rel[coupe_muscle]` | coupe_muscle (28) | 4.16 | 4.45 | 5.09 | same | |
| `dims.L_rel[sports]` | sports (19) | 6.98 | 7.32 | 7.84 | same | |
| `dims.H_rel[sports]` | sports (19) | 1.72 | 1.92 | 2.05 | same | |
| `dims.wheelbase_rel[sports]` | sports (19) | 3.80 | 4.29 | 4.57 | same | |
| `dims.L_rel[suv_pickup]` | suv_pickup (13) | 5.59 | 6.30 | 7.55 | same | wheels 0.80 m |
| `dims.H_rel[suv_pickup]` | suv_pickup (13) | 2.35 | 2.39 | 2.54 | same | |
| `dims.wheelbase_rel[suv_pickup]` | suv_pickup (13) | 3.47 | 3.96 | 4.51 | same | |
| `dims.L_rel[van]` | van (13) | 6.91 | 7.87 | 8.99 | same | |
| `dims.H_rel[van]` | van (13) | 2.90 | 3.48 | 4.76 | same | |
| `dims.wheelbase_rel[van]` | van (13) | 4.43 | 5.10 | 5.65 | same | |
| `dims.L_rel[truck_bus]` | truck_bus (15) | 7.53 | 8.51 | 11.6 | same | wheels 1.0 m |
| `dims.H_rel[truck_bus]` | truck_bus (15) | 2.75 | 3.42 | 3.82 | same | |
| `dims.wheelbase_rel[truck_bus]` | truck_bus (15) | 4.65 | 6.33 | 7.87 | same | |

- A real car built at 1:1 ends up below every vanilla sedan (an agent's 4.98 m car was shorter than all 24).
- The ground is at `wheel dummy z - wheel_d / 2`, 0.0-0.14 m above the bounding box floor.

## Frames and dummies

Standard road-car tree (present in at least half of the sedans): root `<model>` -> `chassis_dummy` -> `chassis`,
`chassis_vlo`; `<part>_dummy` -> `<part>_ok` + `<part>_dam` for bonnet, boot, bump_front, bump_rear,
door_lf/rf/lr/rr, windscreen; `wheel_lf/rf/lb/rb_dummy` (the `wheel` mesh under `wheel_rf_dummy`), `headlights`,
`taillights`, `ped_frontseat`, `ped_backseat`, `ped_arm`, `engine`, `exhaust`, `exhaust_ok`, `petrolcap` and
the `ug_*` tuning anchors.

- Geometry is modelled in its dummy's space; the dummy origin is the hinge: doors at their front edge, bonnet at
  its rear edge, boot at its front edge.
- `headlights`, `taillights` and `ped_frontseat` sit on the RIGHT side only: the engine mirrors them. `ped_arm`
  is the driver window on the LEFT (no vanilla car puts it at x > 0). Bumper dummies sit at the corners (125 of 132
  cars), not on the centre line.
- Keep every frame the engine or `carmods.dat` can address for the model you replace, in vanilla order; a missing
  `ug_*` frame crashes tuning (`limits.md`). The engine's full list per vehicle type: `re.nodes --type automobile`.
- Never copy a vanilla typo (`bonnet_ok ` with a trailing space, `plate-rear_dam`, `pedarm`, `pad_arm`,
  `transmision_f`): use the clean standard names.
- Cars carry no DFF 2DFX: their lights come from the `headlights`/`taillights` dummies and the lamp keys. Aircraft
  and boats carry 1-3 light entries on `omni*` frames.

Typical positions, normalised to the bounding box (0 = min, 1 = max per axis):

| Metric | Peer set (n) | x p50 | y p50 | z p50 | Note |
|---|---|---|---|---|---|
| `frame.pos[wheel_rf_dummy]` | road cars (129) | 0.87 | 0.81 | 0.24 | rear axle y 0.23 |
| `frame.pos[headlights]` | road cars (129) | 0.80 | 0.955 | 0.47 | right side only |
| `frame.pos[taillights]` | road cars (129) | 0.84 | 0.04 | 0.48 | right side only |
| `frame.pos[ped_frontseat]` | road cars (129) | 0.69 | 0.53 | 0.41 | passenger seat; driver mirrored |
| `frame.pos[ped_arm]` | road cars (129) | 0.12 | 0.52 | 0.68 | driver window, left |
| `frame.pos[door_lf_dummy]` | road cars (129) | 0.07 | 0.68 | 0.49 | hinge; rear doors y 0.46 |
| `frame.pos[bonnet_dummy]` | road cars (129) | 0.50 | 0.69 | 0.67 | hinge at the rear edge |
| `frame.pos[boot_dummy]` | road cars (129) | 0.50 | 0.18 | 0.69 | |
| `frame.pos[windscreen_dummy]` | road cars (129) | 0.50 | 0.65 | 0.81 | |
| `frame.pos[engine]` | road cars (129) | 0.50 | 0.82 | 0.52 | |
| `frame.pos[exhaust]` | road cars (129) | 0.69 | 0.04 | 0.18 | usually right-rear, low |
| `frame.pos[petrolcap]` | road cars (129) | 0.08 | 0.17 | 0.52 | usually left-rear |

## Materials, sheen and colour keys

The engine finds paint and lamps by the material colour (RGB; alpha ignored):

| Fact | Value | Source |
|---|---|---|
| paint 1 (primary) key | 60,255,0: 116 of 145 car DFFs on `vehiclegrunge256` (1,288 slots), 103 also on `vehiclegeneric256` (jambs, inner panels: paint without dirt) | vanilla index, car DFFs |
| paint 2 (secondary) key | 255,0,175: two-tone bodies, trim, stripes | vanilla index |
| paint 3 / paint 4 keys | 0,255,255 / 255,0,255: special vehicles only | vanilla index |
| lamp keys | front-left 255,175,0; front-right 0,255,200; rear-left 185,255,0; rear-right 255,60,0: every vanilla use (130-137 car DFFs each) is on `vehiclelights128` | vanilla index |
| lamp behaviour | the engine resets a lamp material to white and swaps `vehiclelights128` for `vehiclelightson128` when the lamp is on; a lamp key on any other texture is an orange polygon that never lights | engine (vehicle model info) |
| glass | `vehiclegeneric256` with material alpha 128 on 606 of 759 alpha slots of that texture (146 vehicle DFFs); dark glass = untextured black alpha 128; never 242 | vanilla index |
| damage overlays | `vehiclescratch64` alpha 242 (321 slots), `vehicleshatter128` alpha 242-250 | vanilla index |
| blackout | untextured black (0,0,0): gaps, grille holes, underside | vanilla index |
| trim | neutral greys on `vehiclegeneric256` are trim and plastics; white on it is chrome or metal | vanilla index |
| surface properties | ambient 0.5 on most materials (0.3 or 0.2 on inner paint), diffuse 1, specular 1 | vanilla DFFs |

The sheen trio of paint, chrome, glass and lamps: MatFX environment map `xvehicleenv128` + a non-zero
reflection intensity + specular texture `vehiclespecdot64`. `xvehicleenv128` (a name starting with `x`) is the
"wave" sheen that reads the SECOND UV set: all 4,070 vanilla slots that use it sit on 2-UV geometries, and on a
1-UV geometry it renders nothing. So body `_ok` panels carry 2 UV sets (61 % of all vehicle geometries); wheels,
exhausts and most `windscreen_dam` carry one. `vehicleenvmap128` is a camera-space map that needs no UV2. The
MatFX coefficient is not used by the car pipeline.

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `mat.refl[paint]` | paint-1 slots of cars (2,115) | 0.0 | 0.08 | 0.10 | same | the weak car "shininess" |
| `mat.refl[glass and alpha]` | alpha slots of cars (1,596) | 0.0 | 0.09 | 0.15 | same | |
| `mat.refl[lamp]` | lamp slots of cars (514) | 0.0 | 0.09 | 0.13 | same | |
| `mat.spec[paint]` | paint-1 slots of cars (1,651) | 0.1 | 0.2 | 0.3 | same | `vehiclespecdot64` |
| `mat.spec[glass and alpha]` | alpha slots of cars (812) | 0.2 | 0.3 | 0.3 | same | |
| `mat.spec[lamp]` | lamp slots of cars (515) | 0.2 | 0.2 | 0.3 | same | |

Interior, tyres, plates and decals carry no sheen (reflection 0).

## Paint and dirt

- Paint is a flat key colour (the colour comes from `carcols.dat`) multiplied by `vehiclegrunge256`. The dirt is
  in the UV layout: up-facing panels map to the clean upper area of the texture, sides map to the band where grime
  rises from the bottom edge, so dirt collects on sills and lower doors. The UV V coordinate follows the panel
  height. A body painted on any other texture never gets dirty.
- Every car spawns with a random dirt level 0-14; the game builds 16 dirt textures from `vehiclegrunge256`: level
  i (0..15) has RGB = c x i / 16 + 255 x (16 - i) / 16 per texel colour c, alpha kept. Level 0 is clean white; the
  raw texture equals level 16, dirtier than anything the game shows. Judge paint at dirt 2 (the preview default).
- Paintjob-capable cars use `remap<model>body128/256` (white with grime at the top and bottom edges); paintjobs
  replace it.
- The paint itself has no painted shading or wear: wear lives in the interior and wheel textures.

## Lamps, glass, interior, wheels

- **Lamps:** flat or slightly curved lens faces set slightly into the body (a visible step), mapped onto a slice of
  `vehiclelights128` with the lamp keys; plain white `vehiclelights128` for reverse and indicator lenses. A slim
  modern lamp is a thin strip of a lens slice, not a new texture.
- **Glass:** single-sided faces on the dark glass area of `vehiclegeneric256` (it already has painted glints),
  alpha 128, sheen on; window frames are black trim strips. The interior must read as dark shapes through it.
- **Interior:** about 130 triangles in the chassis (seat boxes, dashboard slab, steering wheel on
  `vehiclesteering128`, gauges on `vehicledash32`), textured with the car's dark interior atlas (`textures.md`).
- **Wheels:** one mesh with tyre, rim and hub, width 0.30-0.39-0.49 of the diameter (p10-p50-p90, road cars),
  tyre on `vehicletyres128`, rim on a 64 px photo of a quarter rim (sedans) or modelled spokes (sports cars). The
  engine clones it to the four `wheel_*_dummy` frames (IDE `wheel_id -1`). Segment counts: `modelling.md`.

## Damage

`_dam` parts are the same panel re-cut and displaced, NOT lighter: most vanilla cars have at least one `_dam`
heavier than its `_ok`. Build `_dam` from `_ok` (`kit.damage`): dents, a sagging hinge, shattered glass, plus
overlay faces with `vehiclescratch64` (alpha 242) and `vehicleshatter128`. `windscreen_dam` is the same geometry
with the shatter texture.

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `dam.ok_ratio[door_lf]` | cars (125) | 0.66 | 0.97 | 1.11 | 0.8-1.1 | |
| `dam.ok_ratio[bonnet]` | cars (118) | 0.83 | 1.10 | 1.52 | 0.8-1.1 | |
| `dam.ok_ratio[boot]` | cars (89) | 0.79 | 1.08 | 1.32 | 0.8-1.1 | |
| `dam.ok_ratio[bump_front]` | cars (125) | 0.72 | 0.97 | 1.07 | 0.8-1.1 | |
| `dam.ok_ratio[bump_rear]` | cars (112) | 0.72 | 0.99 | 1.06 | 0.8-1.1 | |
| `dam.ok_ratio[windscreen]` | cars (125) | 1.0 | 1.0 | 1.0 | 1.0 | same mesh |
| `dam.disp_cm[p50]` | damage parts of 23 cars (185) | 2.9 | 4.9 | 9.2 | same | windscreens excluded |
| `dam.disp_cm[p90]` | damage parts of 23 cars (185) | 7.8 | 12.8 | 23.8 | at least 12 | the dent must be obvious |

Two agent cars missed this: one decimated its `_dam` parts to 0.43-0.45 of `_ok` to pass a whole-file budget,
the other displaced them by 2-4 cm (p90), which reads as undamaged.

## LOD (`chassis_vlo`)

The silhouette as a box body with a cabin trapezoid and a black glass top, two headlamp quads on
`vehiclelights128`, four diamond-shaped wheel quads; flat shading is fine here. Budget: `part.tris[chassis_vlo]`
above, 2-6 materials. The same in both tiers.

## Collision (embedded COL3) and shadow

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `col.spheres[road car]` | road cars (129) | 18 | 22 | 36 | unchanged | radius 0.15-0.28 x car width |
| `col.spheres[sedan]` | sedan (24) | 19 | 21 | 25 | unchanged | |
| `col.boxes[road car]` | road cars (129) | 0 | 0 | 0 | unchanged | boxy vans and trailers only |
| `col.mesh_faces[road car]` | road cars (129) | 8 | 12 | 14 | unchanged | roof and cabin cap |
| `col.shadow_faces[road car]` | road cars (129) | 216 | 264 | 506 | unchanged | |
| `col.shadow_faces[sedan]` | sedan (24) | 174 | 228 | 331 | unchanged | |
| `col.shadow_ratio[road car]` | road cars (129) | 0.07 | 0.10 | 0.16 | unchanged | shadow faces / `veh.hi_tris` |

- Sphere surface: material CAR (63), brightness byte 187; the "piece" byte is the damage routing (`eCarPiece`):
  per car (p50) about 9 default (0), 4 front bumper (3), 3 rear bumper (4), 2 per front door (5, 6), rear doors (7, 8),
  bonnet (1), boot (2). Mesh faces: CAR and GLASS (45).
- With the FORCE_GROUND_CLEARANCE handling flag the engine raises spheres whose bottom is less than 0.25 m above
  the road.
- Make COL with `col.gen` (vehicle spheres + shadow) or `kit.col`; check with `col.check`. Never copy the broken
  collision of `rccam`.

## Textures and texel density

Own TXD (named like the model): 1-4 textures of about 10 KB, DXT1 (DXT3 only for alpha), exactly one mip level
(every one of the 573 own textures and all `vehicle.txd` textures), named `<model>92<what><size>` (73 %):
`premier92interior128`, `copcarla92wheel64`; the size suffix equals the larger side. Never pack the shared
`vehicle.txd` textures into the own TXD: the game finds them by name.

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `uv.texel_px_m[vehiclegrunge256]` | road cars (129) | 22 | 49 | 88 | same | paint |
| `uv.texel_px_m[vehiclegeneric256]` | road cars (129) | 9 | 52 | 89 | same | trim, glass, chrome |
| `uv.texel_px_m[vehicletyres128]` | road cars (129) | 105 | 130 | 144 | same | |
| `uv.texel_px_m[vehiclelights128]` | road cars (129) | 70 | 162 | 220 | same | |
| `uv.area_share[vehiclegeneric256]` | road cars, undamaged parts | - | 0.49 | - | same | pooled |
| `uv.area_share[vehiclegrunge256]` | road cars, undamaged parts | - | 0.20 | - | same | pooled |
| `uv.area_share[own interior]` | road cars, undamaged parts | - | 0.19 | - | same | pooled |

A 1024 px body texture is foreign to the style; so is a body texture with painted panel lines.

## Data lines

- `vehicles.ide` road cars: `anims null`, `flags 0`, `freq 10`, `wheel_id -1`, front = rear `wheel_scale` (0.70
  typical), `wheel_upgrade 0`; bikes use `anims bikes/bikev/bikeh/biked`. `carcols.dat`: 8 colour pairs for
  civilian cars, 1 for emergency vehicles. Cars never use 1 gear; electric engines are rare.
- Write the lines with `mod.add` (a Mod Loader add-on with a free id) and read what a line means with
  `data.explain`. A value outside the class band needs a written reason.

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `handling.mass[sedan]` | sedan (24) | 1,495 | 1,775 | 2,200 | same | kg |
| `handling.mass[sports]` | sports (19) | 1,360 | 1,400 | 1,600 | same | kg |
| `handling.mass[suv_pickup]` | suv_pickup (13) | 1,620 | 1,850 | 2,500 | same | kg |
| `handling.mass[van]` | van (13) | 1,900 | 2,000 | 5,500 | same | kg |
| `handling.mass[truck_bus]` | truck_bus (15) | 3,500 | 5,000 | 8,300 | same | kg |
| `handling.max_vel[sedan]` | sedan (24) | 160 | 162 | 180 | same | |
| `handling.max_vel[sports]` | sports (19) | 200 | 200 | 232 | same | |
| `handling.max_vel[van]` | van (13) | 140 | 150 | 160 | same | |

Other fields and classes: `style.profile <class> --metrics handling.*`.

## Other vehicle types

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `veh.hi_tris[motorbike]` | motorbikes and quad (11) | 1,597 | 2,378 | 2,941 | - | wheels are 2 meshes |
| `veh.hi_tris[bicycle]` | bicycles (3) | 1,954 | 1,985 | 2,854 | - | |
| `veh.hi_tris[boat]` | boats (10) | 1,210 | 1,921 | 2,506 | - | `boat` + `boat_vlo` |
| `veh.hi_tris[plane]` | planes (12) | 1,617 | 2,451 | 3,385 | - | |
| `veh.hi_tris[heli]` | helicopters (9) | 2,276 | 2,398 | 2,783 | - | |
| `veh.hi_tris[monster_offroad]` | monster and offroad (5) | 3,661 | 4,413 | 5,359 | - | |
| `veh.hi_tris[trailer]` | trailers (9) | 860 | 1,461 | 1,926 | - | |
| `veh.hi_tris[train]` | trains (6) | 2,879 | 3,033 | 3,707 | - | |
| `part.tris[vlo, plane]` | planes (12) | 122 | 185 | 552 | unchanged | |
| `part.tris[vlo, heli]` | helicopters (9) | 160 | 264 | 525 | unchanged | |

- Bikes: `wheel_front` and `wheel_rear` are separate meshes of about 360 triangles each, handlebars about 190,
  forks about 100. Helicopters: rotors 56-92 triangles each. Planes: control surfaces 18-50 each.
- The frame names and order of each type come from the engine tables: `re.nodes --type bike|heli|plane|boat|...`
  (or `asset.anatomy` of a vanilla model of that type; `kit.template --like <SID>` copies them).
