# Authoring: create an asset of any kind (workflow S25, gates G0-G5)

Form first, then composition, then detail, then surface. Build in the live Blender session from rounded
sections and sweeps; never vertex by vertex. Tier `sa_plus` (default) or `vanilla` (blends in). The look:
`style`, `style_construction` + one class topic; other kinds: `style_kinds`. Ops run through `satk_op`.
The task is the INVENTORY and you never declare done: done = `asset.inventory` complete + `asset.check
--strict` `done: true` + every region sheet reviewed + the reviewer's pass (`done`). No time targets, no
number gates. Show the sheet at every gate; wait for the user at G1 and G3.

## Every stage
- Tag each new piece with its item id in the same batch (`scene.tag`); untagged = `missing`.
- End: `asset.inventory <project>`: no `missing`/`unattached` item of the stage; a second pass over the
  inventory, `refs/features.md` and the sheets for what is still missing (add items, never ignore).
- From G2: `blender.preview <subject> --regions` and read EVERY region sheet (underside, interior, inside
  doors and hulls too); one line per region in `notes.md`.

## G0 Design
- `asset.init <dir> --kind <kind> --intent replace|add --like <SID> --tier sa_plus --target sp|mta|samp`;
  `asset.anatomy <SID> --md` (frames, parts, materials of the model you replace).
- Real object: spec sheet -> ratio card and one scale from the wheel (`style_references`). Photos:
  `ref.import <photo> --view side|front|rear|top|3q|detail`, `refs/features.md` (10-20 per photo, identity
  first), `ref.board`. Missing views: ask the user now. Peers of the same body type; `style.brief_check`.
- `design/inventory.json`: kind, detail (`hero` default), the kind's starter list plus one item per feature
  and design part (id, name, region, category, construction, attaches_to, stage, required); left and right
  apart; "interior" split into its parts; a dropped starter item needs a waiver (`waived`: key, why).
- Adding (not replacing): `id.free --kind <kind>` first. Show: board + description + inventory.

## G1 Form (HUMAN CHECKPOINT)
- `blender.session start --name <asset>`; `blender.methods` once; `kit.template --like <SID> --ghost`.
- `mesh.loft` section shapes (crown, exp, tumble; `interp` smooth; `half`; `parts`), `mesh.sweep`,
  `mesh.lathe`, `mesh.primitive rounded_box|capsule`; soft moves (`falloff`, `near`/`loop`/`grow`),
  `mesh.relax`, `mesh.deform`. `kit.blank` is an optional quick start. Wheels or the ped at scale.
- Sheet: `blender.preview session:<name> --lineup class --passes game,clay`; identity, proportions.

## G2 Compose
- One welded shell, panels cut (`kit.blank_split`), lined arches or welded flares (`mesh.flare`), bumpers
  wrapped, details touching (`mesh.attach`). Refit every frame (`kit.info` `frames: true`): lamps, exhaust,
  hinges, wheels in arches, seat and steering; moving parts (rotors, props, control surfaces, forks, pedals,
  bogies) pivot at their frame origin, clear through the motion.
- Gate: `form`/`fit`/`symmetry` rows clean, LOW three-quarter clay view, `look.leak` gaps closed or named
  as designed openings, region sheets read.

## G3 Detail (HUMAN CHECKPOINT)
- Every G3 item of the inventory in the same soft language (lamps in buckets, grille, mirrors, handles,
  interior, bay, underbody; the kind's hero items). A placeholder primitive is not built.
- Sheet: the region sheets next to a vanilla peer; no G3 item missing.

## G4 Surface
- `kit.material_preset` (paint on `vehiclegrunge256` + UV2 sheen, lamp keys, glass alpha 128),
  `kit.uv_region`, tiling UVs for map models; own textures soft, small, clean (paint at 4x,
  `texture.finish` grime 0 `supersample`, `kit.bake`); `kit.shade` (seams hard, corners soft).
- Sheet: game look at dirt 2 (and 0); region sheets for seams, stretch, stripes, dark faces.

## G5 Finish and polish
- `kit.damage`, `kit.vlo` or `kit.lod`, `kit.col` (or `col.gen` + `col.check`); `fx2d.copy` + `fx2d.check`.
- `kit.export --replace <SID>` or `--add`; `asset.lint <out> --preset <tier> --baseline vanilla`;
  `mod.check`; `texture.audit`.
- Polish loop: `look.leak` (package) -> `asset.check <out> --like <SID> --strict` -> fix the first
  `blocking` rows -> export -> regions you touched -> again until `done: true`. Never delete or reject a
  built item to pass; a tool gap is an open issue with evidence.
- S29: `view.vehicle|place|ped` with the export, then `ingame.start` (the user starts the client),
  `ingame.check`; fix, `kit.export`, `ingame.reload`. Report: stage, `done`/`blocking`, inventory counts
  and rejections, region table, open issues.

## Session rules
- 2+ mutating steps = one checkpoint; 90 s per step: `TIMEOUT` = split, `BUSY` = `blender.session stop`
  and start again. `--params-file` for long parameters; `blender.session restore --ref <step|tag|last>`.
- After G1 edit the mesh; never rebuild a blank from edited numbers. `python` only as a last resort.
- Never measuring code for photos, private metric scripts, or geometry changes that only move a number.
- Blender cannot start (`DEPENDENCY`): `blender_job`, `blender.game_ready`; say so.

Full workflow: docs/agent/workflows.md
