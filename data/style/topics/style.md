# SA style: the look, the hard rules, and vanilla as reference

The San Andreas look is DESCRIBED here; numbers from the stock game are reference, never targets, gates or
warnings. Read this topic, `style_construction` (how vanilla builds a model) and ONE class topic
(`style_vehicle`, `style_world`, `style_ped_weapon`); `style_references` when the asset depicts a real object;
`style_shading` / `style_texture` when you reach that step.

## The look
- Shape: soft, rounded, "pillowy", simplified. Every surface a little crowned, corners turned in two or three
  smooth steps, glasshouses leaning in, lower bodies bulging like a barrel. Never a hard box, never a sharp
  modern crease except where a material or a panel changes.
- One coherent whole: a welded shell carries the body, panels are cut from it, arches have liners, bumpers wrap
  into the arches, details sit in recesses or touch the skin, lines run across the cuts.
- Proportions: chunky and planted; cars about 1.1-1.2x the real car and relatively wider; the donor's lines
  kept, its millimetres not. Non-vehicles measured against the 1.84 m ped.
- Shading: soft normals, hard only on seams (material/UV borders) and named creases. Map models: no normals, a
  dark baked prelight with occlusion.
- Textures: small (about 128 px), soft and slightly blurry, photo-like, desaturated, light painted in. Car paint
  is a clean key colour; the engine adds the dirt. Wear only where use leaves it.
- Detail: free. Richer than vanilla (real interior, engine bay, underbody, deeper lamps) is welcome when it is
  built in the same language. Model what reads at game distance, paint what is flat or fine.

## Hard rules (the engine needs them)
Frame names/parents/order, every `ug_*` frame of a replaced car, dummy sides; paint keys (60,255,0 /
255,0,175) on `vehiclegrunge256`, lamp keys only on `vehiclelights128`, glass alpha 128, UV2 where
`xvehicleenv128` is used; wheel mesh diameter = IDE `wheel_scale`; normals on dynamically lit models, prelight
on map models; valid COL3 + shadow for vehicles; names (model/TXD/frame <= 23, 21 with COL, texture <= 31);
TXD power of two, DXT1/DXT3, never DXT5, one mip level for vehicles/peds/weapons; <= 65,535 vertices per
geometry; map HD with collision draw <= 299; ped skin (32 bones, <= 4 weights); ids and capacity of the target.

## Tiers
- `sa_plus` (DEFAULT for new assets): the same visual language at a free detail level; own textures one size
  step larger. No triangle numbers.
- `vanilla`: about the detail of the stock peers, for a replacement that must blend into traffic or a street.
- Record the tier and why in `asset.json` (`asset.init ... --tier`).

## Ten rules
1. Describe, then build: design description, features per photo, spec ratios (`style_references`).
2. Form first: rounded sections and sweeps in the live session (`mesh.loft` shapes, `mesh.sweep`,
   `mesh.lathe`, soft `falloff` moves). Never a chamfered cube as a body part, never vertex-by-vertex meshes;
   the blank is an optional quick start.
3. Compose one whole: welded shell, panels cut, details touching, arches lined; floating, gapped and buried
   pieces are defects (`asset.check` form rows).
4. Scale from anchors: wheel mesh = `wheel_scale`; others the 1.84 m ped.
5. Detail pass: every inventory item (the kind's starter list + every feature), in the same soft language.
6. Shade per line (seams hard, corners soft); map models: prelight with occlusion, warm darker nights.
7. Shared textures and colour keys.
8. Small, soft, clean textures; car paint never painted.
9. Fit the function: refit dummies; bike steering axis and rider contacts; ship damage, LOD, COL, data lines.
10. Judge by eye: lineup with two vanilla peers + the reference board + clay views at every gate; `asset.check`
    without engine errors and open form/fit defects; its numbers are information. Never add or remove geometry
    to move a count. Done is not yours to declare: inventory complete, `asset.check --strict` `done: true`,
    every region sheet reviewed (`done`); every kind's items and regions: `style_kinds`.

## Anti-patterns (seen in agent builds)
Bodies of chamfered cubes; flares, bumpers, rails, pillars standing off the body; template dummies never moved;
too few details because a count looked "full"; stopping at the first acceptable lineup and declaring the
model done with items missing and regions never looked at; one smoothing angle for everything; grime painted everywhere;
neon key colour judged as paint; photos measured with grids and solved cameras; blank profile kept as the car;
geometry dissolved to move a metric; vector-art textures; bright interiors; real-world scale.

## Which operations
`asset.anatomy`, `style.profile` (vanilla reference), `style.brief_check <brief.md>`, `ref.import`,
`ref.board`, `asset.init`, `asset.status`, `blender.session`, `blender.methods`, `blender.call`,
`kit.template`, `kit.export`, `blender.preview --lineup class`, `style.texture`, `asset.check`, `asset.lint
--preset sa_plus|vanilla --baseline vanilla`, `texture.finish`, `texture.pack --asset-class`. All run through
`satk_op`; find them with `satk_ops("<words>")`. Workflow: `satk_help("authoring")`.

Full guide: docs/agent/style/README.md
