# Authoring: create an asset of any kind (workflow S25, gates G0-G5)

Model IN Blender through the live session, step by step, with stats after every step; never hand-type geometry.
Default tier `sa_plus` for new assets, `vanilla` for a replacement that must blend in. Style numbers:
`satk_help("style")` + one class topic. All operations run through `satk_op` (CLI `satk <group> <command>`).

## G0 Setup (target: 5 min, about 6 calls)
- `asset.init <dir> --kind <kind> --intent replace|add --like <SID> --tier sa_plus --target sp|mta|samp`:
  writes `<dir>/asset.json` (kind, intent, tier, target, dims, decisions, gates, checkpoints).
- `style.profile --like <SID> --tier sa_plus`: the bands; `asset.anatomy <SID> --md`: frames, parts, materials.
- Photos: `ref.import <photo>` (resized to <= 1,600 px, grid sheet). Never open a full-size photo.
- A brief from the user: `style.brief_check <brief.md>`; ask about every flagged phrase.
- Adding (not replacing): `id.free --kind <kind>` first (a stock game has 0 vehicle, 2 ped, 1 weapon slots).

## G1 Blockout + scale lineup (target: by 15 min)
- `blender.session start --name <asset>` (`--gui` to let the user watch); `blender.methods` once.
- `blender.call kit.template` (`--like <SID> --tier --ghost`): frames, dummies at your dims, slots, materials,
  vanilla ghost (never exported). Then the blockout with `mesh.*` / `modifier.*` steps (batch several steps in
  one `blender.call`).
- `blender.preview session:<name> --lineup class --passes game,clay`: ONE sheet with two class peers at the same
  scale (a project session takes the like model from `asset.json`). HUMAN CHECKPOINT: show the sheet, ask about proportions and silhouette, record the answer
  (`asset.status --record`). Gate: `dims.*` in the class band.

## G2 Shape and shading
- Detail where the outline turns, at the tier's segment counts; long triangles on flats.
- `kit.shade` (weld, smooth, sharp at seams/creases, weighted normals). Gate: `shade.normal_bend`,
  `shade.flat_share`, `part.tris`, `geo.median_dihedral`, `geo.largest_piece_share` in band (step stats).
- Second sheet (game + clay + wire). HUMAN CHECKPOINT before parts and textures.

## G3 Parts: damage, LOD, collision
- Cut parts along edges into their slots; jambs; hinges at dummy origins.
- `kit.wheel`, `kit.damage` (`dam.ok_ratio` 0.8-1.1, dents >= 12 cm at p90), `kit.vlo` or `kit.lod`, `kit.col`
  (or `col.gen` + `col.check`; `col.surface <texture>` explains a surface). Preview `--states ok,dam,vlo,col`.
- Map models with lamps, smoke or an entry: `fx2d.copy` the 2dEffects of a vanilla class peer, then `fx2d.check`.

## G4 UVs and textures
- `kit.uv_region` into shared atlas regions, `kit.material_preset` (paint on `vehiclegrunge256` + UV2 sheen,
  lamp keys, glass alpha 128), tiling UVs at the class texel density for map models.
- Own textures: small, photo-like (`texture.new` flat base, `kit.bake` masks of any size, `texture.finish --edge
  --role`); `style.texture <png> --role auto` in band. `uv.fit` keeps no aspect by default; `uv.texel` gives px/m.
  Gate: `uv.zero_area_share` in band; texture crops checked at native size.

## G5 Export, check, package
- `kit.export --replace <SID>` (Mod Loader folder + readme) or `--add` (via `mod.add`: free id and
  data lines); it fixes bounding spheres, names the COL `<model>_col`, packs own textures only (DXT, one level),
  re-imports and diffs. Props and buildings also get prelight, a primitive COL by the class rule, the LOD DFF
  (`kit.template --lod`, `kit.lod`) and, with `--place X,Y,Z`, the IPL pair.
- `asset.check <out> --like <SID> --tier <tier> --md`: 0 structure/semantic errors, every out-of-band row
  explained. `asset.lint <out> --preset <tier> --baseline vanilla`; `mod.check <folder>`; `texture.audit <folder>`
  (`texture.optimize` only for what it flags).
- Final lineup sheet; `asset.status` (<= 1 KB resume card) for the report.
- Hybrid loop (S29): `view.vehicle|place|ped` with the exported files (`watch`) and `view_capture(marks=6)` for the
  context; then `ingame.start` (the user runs the one-time setup and starts the client) and `ingame.check`; fix,
  `kit.export`, `ingame.reload`. The viewer is a fast look, the game is the truth for behaviour.

## Per-kind notes
- Vehicles (automobile, bike, bmx, quad, boat, heli, plane, trailer, train, mtruck): anchor = `wheel_scale`;
  keep every `ug_*` frame; `_dam` parts; `chassis_vlo`; COL3 + shadow; handling/carcols lines via `mod.add`.
- Prop: size-bucket bands; prelight and primitive COL from `kit.export`; draw <= 100.
- Building + LOD: tiling textures; prelight + warm night; COL mesh ~quarter of faces; draw <= 299; `kit.lod`.
- Interior shell/prop, breakable, pickup, vehicle upgrade: their class rows in `style_world`.
- Weapon: real length vs the 1.84 m ped; `gunflash` atomic; `weapon.dat` binding.
- Ped: re-skin or vertex edits keeping the 32-bone skin; no new skeleton.

## Session rules
- Reply per step <= 1.5 KB (`stats`, optional `snapshot`, `checkpoint`); snapshots opt-in, 512 px JPEG (`look: game`
  for the SA look). A batch of 2+ mutating steps writes one checkpoint. A step has a 90 s budget: `TIMEOUT` = split
  it, `BUSY` = stuck (`blender.session stop`, start again). `blender.methods --query <name>` = its parameters.
- Checkpoints are `.blend` saves: `blender.session restore <n>`; never rely on undo. The journal replays the build.
- `python` method only as a last resort (journaled). Never write a mesh library or vertex lists.
- Keep research out of the context: write notes to `<dir>/notes.md`, keep a summary of <= 30 lines.
- Blender session cannot start (`DEPENDENCY`, `EXTERNAL_TOOL`): fall back to `blender_job` (import/render/export)
  and `blender.game_ready`, and say so in the report.

Full workflow: docs/agent/workflows.md
