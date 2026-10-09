# Asset brief: <name>

<!-- Template for a brief that asks an agent to create a GTA San Andreas asset with satk. Copy it, fill in the
     angle brackets, delete what does not apply, then run `style.brief_check <brief.md>`: it flags wording that
     leads to known mistakes. Model-facing, English only. The brief describes WHAT is wanted (the design, in
     words) and points to the style topics; it never sets polygon numbers. -->

## 1. The asset

- Kind: <automobile | bike | bmx | quad | boat | heli | plane | trailer | train | mtruck | prop | building |
  interior_shell | interior_prop | breakable | weapon | ped | pickup | vehicle_upgrade>
- Intent: <replace `model:<id>` | add a new model (the agent checks free ids with `id.free` first)>
- Closest vanilla model (the `--like` reference for frames, dummies and the lineup): <`model:<id>`>; lineup
  peers of the same body type: <for example hatchbacks `model:496`, `model:589`>
- Target platform: <sp (Mod Loader) | sp-la (limit adjuster) | mta | samp>
- Detail tier: <`sa_plus` (default for new assets: the SA language at a free detail level) | `vanilla` (the
  replacement must blend into traffic or a district)>; the reason: <one line>
- Detail level of the inventory: <`hero` (default for new assets: interior, mechanics, underbody, housings and
  secondary details on every region) | `standard` (everything the vanilla peers carry) | `simple` (silhouette
  and the parts the engine needs)>

## 2. Design description

What the object is, in words a modeller can build from. The agent turns it into the features list and the
reference board at gate G0 (`satk help style_references`).

- The real object: <make, model, body style, year or generation>; spec sheet: <length, width, height,
  wheelbase, track, tyre size> (the agent derives the ratios and one scale factor from them).
- Character in one or two sentences: <for example "a soft, rounded early-2000s compact hatchback, tall
  glasshouse, friendly face">.
- Proportions in words: <long or short bonnet, glass height against the body, roof line, overhangs, stance>.
- Signature features, most important first: <lamp shapes and where they reach, grille, bonnet creases,
  mouldings, arch shape, pillar treatment, tail lamps, wheels>.
- How the parts meet: <bumpers wrap into the arches, flares grow out of the fenders, black B-pillar, ...>.
- What must be modelled beyond the outside: <interior (seats, dash, door cards), engine bay (the bonnet opens),
  boot, underbody, rider contact points for a bike>.
- References: <files in `<project>/refs/`, with the view of each: side, front, rear, three-quarter, detail; whether a true
  side view exists; what the photos do not show (the rear, the roof, the interior)>.
- Must-have items beyond the kind's starter list: <for example "the roof rack with its feet", "a cabin interior
  with two bunks", "the rear service door with its lamp">; items the design does NOT have: <for example "no
  rear doors (coupe)">.

## 3. Style

The agent reads `satk_help("style")`, `satk_help("style_construction")` and the class topic
(`satk_help("style_vehicle")`, `satk_help("style_world")` or `satk_help("style_ped_weapon")`) before modelling,
and `satk help style_shading` / `satk help style_texture` when it reaches those steps.

- Look: the San Andreas language: soft, rounded, simplified forms composed into one joined whole; crowned
  surfaces, corners turned in smooth steps, a glasshouse that leans in; distinct lines only where a material or a
  panel changes.
- Scale: from the anchor (vehicles: the wheel of the class or of the replaced model; everything else: the ped
  height) and the design's ratios; SA vehicles are a little larger and wider than their real counterparts.
- Detail: free in that language: model what reads at game distance (lamps in recesses, grille surround, mirrors,
  handles, interior, engine bay), paint what is fine or flat.
- Surfaces: the shared game textures and colour keys of the class; own textures small (about 128 px), soft and
  photo-like, light and folds painted in; car paint stays a clean key colour (the game adds its own dirt).
  Licensed photographs may be used for textures; otherwise the photo-like recipe of `style_texture`.
- Parts: everything the class has in vanilla (for vehicles: damage parts, LOD, collision and shadow, frames and
  dummies fitted to the new geometry; for map models: LOD, collision, prelight and night colours).

## 4. Process

- Workflow S25 (`satk_help("authoring")`): G0 design, G1 form, G2 compose, G3 detail, G4 surface, G5 finish;
  modelling in the live Blender session with rounded sections and sweeps; no vertex-by-vertex geometry.
- The task list is the inventory `<project>/design/inventory.json` written at G0 (`satk_help("done")`): every
  part and detail as an item with its construction, its parent and its stage; every required item built (a
  starter item is dropped only at G0 with a waiver and a real reason). The agent never declares itself done: done = the inventory complete, `asset.check
  --strict` answering `done: true` and every close-up region sheet (`blender.preview --regions`) reviewed.
- The kind's frames, moving parts and regions: `satk_help("style_kinds")`.
- Human checkpoints: the form sheet at G1 and the detail sheet at G3 (wait for the answer); the other gates
  show their sheet as progress. No time targets.
- References are described; only true side, front or rear views may be measured, never with grids or solved
  cameras.
- Reproducibility: the project manifest `asset.json`, the session journal and the `.blend` checkpoints.
- Images: one review sheet per gate (S26, `satk help visual_qa`) next to the reference board.

## 5. Deliverables

- A Mod Loader folder (or the target's package) from `kit.export`, with a readme that lists what the
  replacement inherits (data lines, upgrade lists, groups, sounds).
- `<project>/refs/features.md`, the reference board and the gate sheets; the `asset.check` report and the lint result.
- `<project>/design/inventory.json` with the `asset.inventory` report (built, missing, unattached, rejected with reasons),
  the region sheets with one verdict line per region, the leak result and the `asset.check --strict` answer.
- `asset.json` with the decisions.

## 6. Acceptance

- It reads as the described object from about 10 m in the game camera; every identity feature is there.
- It sits next to two vanilla peers of the body type as if it shipped with the game.
- `asset.check --strict`: `done: true` (no engine errors, no form, fit or defect rows, no leak gaps, the
  inventory complete); `asset.lint --preset <tier> --baseline vanilla` and `mod.check`: no errors.
- Every region sheet reviewed and passed: no gap, no floating part, no stripe, no dark face, no placeholder.
- In the game (when the target allows): it loads, lights, doors and damage work; a bike's rider sits on it and the
  steering stays inside the body.
- The user approves the final sheet.

## 7. Notes

<anything else: deadline, things to avoid for this particular asset, links>
