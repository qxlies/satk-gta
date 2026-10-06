# SA style: the rules for any new asset (vanilla or sa_plus)

Measured on the clean 1.0 US copy, not recalled. Bands are p10 / p50 / p90 over a named peer set. Read this
topic plus ONE class topic (`style_vehicle`, `style_world`, `style_ped_weapon`) before modelling; add
`style_shading` / `style_texture` when you reach that step. Exact numbers for your class: `style.profile <class>
--tier <tier>` (or `--like model:<id>`).

## Tiers
- `sa_plus` (DEFAULT for new assets): the same visual language, higher budgets only where silhouette and
  curvature live (chassis 2,000-3,000 tris, interior 250-450, wheels 16-24 sides, arches 12-16 segments, own
  textures one size step up). These numbers are a PROPOSAL until validated with the user.
- `vanilla`: every metric inside the measured class band; for replacements that must blend into traffic.
- Never changes between tiers: shared textures and colour keys, paint on the dirt texture, the shading rule,
  photo-like textures, formats (DXT1/DXT3, one mip level for vehicles/peds/weapons, never DXT5), scale, frames,
  LOD, collision and shadow budgets, draw distance, prelight rules, data lines.
- Record the tier and why in `asset.json` (`asset.init ... --tier`).

## Ten rules
1. Measure, do not recall: `style.profile`, `asset.anatomy <SID>` of the model you replace or resemble.
2. Model IN Blender through the live session (`blender.session start`, `blender.call`): primitives with explicit
   segment counts, modifiers, reference planes, stats + snapshot per step. Never type vertex lists or write a
   private mesh library.
3. Scale from anchors: vehicle wheel mesh diameter = IDE `wheel_scale` (0.70 m on most cars) and the class ratios
   follow from it; SA cars are about 1.1-1.2x the real car. Other assets: the ped is 1.84 m.
4. Silhouette first: triangles where the outline turns (arches, bumper corners, lamp surrounds, roof edges,
   cornices); flat areas are a few long triangles; windows, grilles, bolts, brickwork are texture.
5. Smooth shading: weld, smooth every face, split normals only at material/UV seams and designed creases (cars:
   smooth < 30 deg, decide 30-60, hard > 60). Peds fully smooth. Map objects have no normals.
6. Reuse shared textures and keys: paint = key colour (60,255,0) on `vehiclegrunge256` (dirt), lamps = key
   colours on `vehiclelights128`, glass = `vehiclegeneric256` alpha 128; map models tile district textures.
7. Textures small (64-256 px), photo-like, dirty, desaturated, shading and grime painted in; never vector fills,
   never one flat colour per mesh.
8. Light by class: vehicles/peds/weapons normals, no prelight; map objects dark grey day prelight + darker warm
   night colours with light pools; collision face light from the prelight (15 by day is the vanilla norm).
9. Ship every part: `_dam` at 0.8-1.1x the tris of `_ok`, LOD, COL (+ shadow for vehicles), frames in vanilla
   order and side, names within limits (model 23 chars, 21 with collision), data lines.
10. Judge against vanilla: `asset.check`, `blender.preview --lineup class` (game look next to two peers), lint
    preset of the tier; show the lineup sheet to the user at gates G1 and G2 (workflow S25).

## Metric names (one definition each)
- `veh.hd_tris` = chassis + non-`_dam`/`_vlo` parts + the wheel ONCE; `veh.hi_tris` = wheel once per dummy.
- `part.tris[<frame>]`, `dam.ok_ratio[<part>]`, `dam.disp_cm`, `dims.*` (`dims.L_rel` = length / wheel diameter).
- `shade.normal_bend` (area-weighted corner-normal bend, deg), `shade.flat_share`, `shade.hard_by_dihedral`,
  `shade.hard_at_seam`, `dff.verts_per_tri`.
- `geo.tris`, `geo.median_dihedral`, `geo.largest_piece_share`, `geo.thirds`, `geo.round_sides[<feature>]`.
- `uv.zero_area_share`, `uv.texel_px_m`, `uv.span`; `tex.colours`, `tex.lum_mean`, `tex.sat_mean`,
  `tex.val_mean`, `tex.hf_energy`; `light.prelit_lum_p50`, `light.night_ratio`, `light.night_tint`; `col.*`,
  `lod.ratio`, `ide.draw`.

## Anti-patterns (seen in agent-built assets)
- Faceted body: bend 0.81 deg, flat share 0.47 (cars p50: 10.6 deg, 0.144).
- One flat colour per mesh: 95 % zero-area UVs (cars p50 0.045).
- Voxel/slab look: 89 loose pieces, largest 6.5 % (chassis p50 0.74).
- Vector textures: 19-77 colours (car interiors p10 344).
- Bright interior: value 0.42 behind alpha-128 glass (interiors p50 0.14).
- Real-world scale: a 4.98 m sedan (sedans p10 5.50 m).
- Paint on `vehiclegeneric256` only (never gets dirty); `_dam` decimated to 0.45x; hand-typed geometry.

## Which operations
`style.profile`, `style.card --md`, `asset.anatomy`, `style.brief_check <brief.md>`, `asset.init`,
`asset.status`, `blender.session`, `blender.methods`, `blender.call`, `kit.template`, `kit.export`,
`blender.preview`, `style.texture`, `asset.check`, `asset.lint --preset sa_plus|vanilla --baseline vanilla`,
`rw.patch --smooth-normals --recalc-bsphere`, `texture.pack --asset-class`, `texture.finish`. All run through
`satk_op`; find them with `satk_ops("<words>")`. Workflow: `satk_help("authoring")`.

Full guide: docs/agent/style/README.md
