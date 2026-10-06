# SA style: vehicles (all types)

A vanilla car: ~2,200 tris (wheel once) in ~22 geometries, normals and no prelight, flat paint KEY colours that
the engine recolours over the shared `vehicle.txd` (`vehiclegeneric256` + `vehiclegrunge256` cover ~70 % of the
surface), 2-3 small own textures (interior 128, wheel 64). Bands p10 / p50 / p90; road cars = 129 curated
automobiles; sa_plus = PROPOSAL.

## Budgets
- `veh.hd_tris` (wheel once) cars (144): 1,676 / 2,168 / 2,473; sa_plus 3,000-4,500 (cap 5,000).
- `veh.hi_tris` (wheel x4): sedan (24) 2,439 / 2,650 / 2,861; sports (19) 2,476 / 2,847 / 3,196; suv_pickup
  (13) 2,394 / 2,769 / 2,860; van (13) 2,152 / 2,506 / 3,045; truck_bus (15) 2,088 / 3,083 / 3,604; bikes (11)
  1,597 / 2,378 / 2,941; boats (10) 1,210 / 1,921 / 2,506; planes (12) 1,617 / 2,451 / 3,385; helis (9) 2,276 /
  2,398 / 2,783.
- `part.tris` cars (models with the part): chassis 1,152 / 1,398 / 1,819 (sa_plus 2,000-3,000); interior inside
  the chassis ~130 (sa_plus 250-450); wheel 134 / 158 / 236 (sa_plus 240-400); front door 57 / 104 / 148
  (120-220); bumpers 57 / 100 / 175 (150-300); bonnet 24 / 48 / 88 (72-120); boot 22 / 42 / 129 (63-105);
  windscreen 5 / 12 / 20 (12-30); `chassis_vlo` 60 / 80 / 140 (sa_plus 55-130).
- Materials: whole car 20 / 23 / 27; chassis 13 / 16 / 19; door 5.6 / 7 / 9; wheel 2 / 3 / 4.
- Where: chassis tris front / middle / rear thirds 41 / 23 / 36 %; trim on `vehiclegeneric256` ~42 %, paint 31 %,
  interior 10 %; `geo.median_dihedral[chassis]` 7.8 / 10.7 / 19 deg (2 deg = wasted flat skin);
  `geo.largest_piece_share[chassis]` 0.36 / 0.74 / 0.82 (one welded shell).

## Scale (anchor = wheel)
- Wheel mesh diameter = IDE `wheel_scale` (ratio 0.99 / 1.00 / 1.03), 0.70 m on most cars, 12 sides (p90 16;
  sa_plus 16-24), width 0.30-0.49 of the diameter.
- Sedan (24): L 5.50 / 5.82 / 6.15, W 2.11 / 2.27 / 2.44 (chassis, no mirrors), H 1.48 / 1.53 / 1.70, wheelbase
  3.16 / 3.55 / 3.79, track 1.79 / 1.87 / 2.01 m. Relative to the wheel: L 7.68 / 8.19 / 9.04, H 2.04 / 2.19 /
  2.44, wheelbase 4.43 / 4.88 / 5.43. Sports L_rel 6.98 / 7.32 / 7.84; suv_pickup 5.59 / 6.30 / 7.55.
- ~1.1-1.2x the real car (5 known counterparts: 1.01 / mean 1.12 / 1.20), wheels real size. Origin = body
  centre, 0.60 / 0.80 / 1.15 m above the ground.

## Frames
Root -> `chassis_dummy` -> `chassis`, `chassis_vlo`; `<part>_dummy` -> `<part>_ok` + `<part>_dam` (bonnet, boot,
bump_front/rear, door_lf/rf/lr/rr, windscreen); `wheel_lf/rf/lb/rb_dummy` (+ `wheel` under rf); `headlights`,
`taillights`, `ped_frontseat` on the RIGHT only (mirrored by the engine); `ped_arm` on the LEFT (driver window);
bumper dummies at the corners; hinges = dummy origins (doors front edge, bonnet rear edge, boot front edge);
keep every `ug_*` frame of the replaced model (tuning crash otherwise). Never copy vanilla typos (`bonnet_ok `).
Engine names per type: `re.nodes --type <type>`; template: `kit.template --like <SID>`.

## Materials and keys (RGB, alpha ignored)
- Paint 1 (60,255,0) on `vehiclegrunge256` (116 of 145 car DFFs); also on the white of `vehiclegeneric256` for
  jambs and inner panels (no dirt). Paint 2 (255,0,175) two-tone and trim.
- Lamps FL (255,175,0), FR (0,255,200), RL (185,255,0), RR (255,60,0): ONLY on `vehiclelights128` (the engine
  turns them white and swaps `vehiclelightson128` when lit).
- Glass: `vehiclegeneric256` dark-glass area, alpha 128 (606 of 759 alpha slots), single-sided. 242 is NOT glass:
  it is the `vehiclescratch64` / `vehicleshatter128` damage overlays.
- Sheen trio on paint, chrome, glass, lamps: env `xvehicleenv128` (reads UV2: give those geometries 2 UV sets) +
  reflection 0.0 / 0.08 / 0.10 (paint) + specular `vehiclespecdot64` 0.1 / 0.2 / 0.3. None on interior, tyres.

## Dirt
Every car spawns with dirt 0-14: level i (0..15) = c x i/16 + 255 x (16-i)/16 per texel of `vehiclegrunge256`
(raw texture = level 16, dirtier than the game shows). Dirt lives in the UV layout: up-facing panels map to the
clean upper area, sides to the band where grime rises from the bottom, V follows panel height.

## Damage, LOD, collision
- `dam.ok_ratio` 0.8-1.1 (vanilla doors 0.66 / 0.97 / 1.11, bonnet 0.82 / 1.11 / 1.54): `_dam` is NOT lighter.
  Displacement p90 per part 7.8 / 12.8 / 23.8 cm (make it >= 12 cm); add scratch (alpha 242) and shatter faces.
- `chassis_vlo`: box body + cabin trapezoid + black glass top, 2 lamp quads, 4 diamond wheel quads, 2-6 mats.
- COL3: spheres road cars 18 / 22 / 36 (surface CAR 63, piece byte = damage part), 0 boxes, mesh 8 / 12 / 14
  faces, shadow 216 / 264 / 506 faces (0.07 / 0.10 / 0.16 of `veh.hi_tris`). COL named `<model>_col`.
- Own TXD: 1-4 DXT1/DXT3 textures, ONE mip level, `<model>92interior128`, `<model>92wheel64`; never pack
  `vehicle.txd` copies. Texel px/m: grunge 22 / 49 / 88, generic 9 / 52 / 89.

Full guide: docs/agent/style/vehicles.md
