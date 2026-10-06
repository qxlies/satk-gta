# Asset brief: <name>

<!-- Template for a brief that asks an agent to create a GTA San Andreas asset with satk. Copy it, fill in the
     angle brackets, delete what does not apply, then run `style.brief_check <brief.md>`: it flags wording that
     prescribes known anti-patterns. Model-facing, English only. The brief names WHAT is wanted and points to the
     style topics; it never restates style numbers (they live in satk and are measured). -->

## 1. The asset

- Kind: <automobile | bike | bmx | quad | boat | heli | plane | trailer | train | mtruck | prop | building |
  interior_shell | interior_prop | breakable | weapon | ped | pickup | vehicle_upgrade>
- Intent: <replace `model:<id>` | add a new model (the agent checks free ids with `id.free` first)>
- Closest vanilla model (the `--like` reference for templates, profiles and lineups): <`model:<id>`>
- Target platform: <sp (Mod Loader) | sp-la (limit adjuster) | mta | samp>
- Detail tier: <`sa_plus` (default for new assets) | `vanilla` (the replacement must blend into traffic or a
  district)>; the reason: <one line>
- Inspiration: <the real object or idea>; reference photos: <files> (the agent prepares them with `ref.import`)
- Must-have features: <for example sliding door, roof rack, open-top variant>

## 2. Style

"San Andreas style" means the measured bands of satk, not adjectives. The agent reads `satk_help("style")` and
the class topic (`satk_help("style_vehicle")`, `satk_help("style_world")` or `satk_help("style_ped_weapon")`)
before modelling, and `satk help style_shading` / `satk help style_texture` when it reaches those steps.

- Scale: derived from the anchor (vehicles: the wheel size of the class or of the replaced model; everything
  else: the ped height) and the class proportions. SA vehicles are larger than their real counterparts; the
  class bands decide, not the real dimensions of the inspiration.
- Shape: silhouette first; detail where the outline turns; panels stay soft; split normals at material and UV
  seams and at the designed creases of the class rule.
- Surfaces: the shared game textures and colour keys of the class; own textures small, photo-like, with shading,
  folds and grime painted in. Licensed photographs may be used; otherwise the photo-free recipe of
  `style_texture`.
- Parts: everything the class has in vanilla (for vehicles: damage parts of comparable density, LOD, collision
  and shadow, frames and dummies; for map models: LOD, collision, prelight and night colours).
- Budgets: the tier band of the class (`style.profile`). Do not set triangle floors or ceilings in the brief.

## 3. Process

- Workflow S25 (`satk_help("authoring")`): gates G0-G5, modelling in the live Blender session with stats after
  every step; no hand-typed geometry.
- Human checkpoints: the scale lineup sheet at G1 (target: within about 15 minutes) and the shape and shading
  sheet at G2. Wait for the answer before detailing further.
- Reproducibility: the project manifest `asset.json`, the session journal and the `.blend` checkpoints.
- Images: one review sheet per cycle (S26, `satk help visual_qa`), reference photos prepared by `ref.import`.

## 4. Deliverables

- A Mod Loader folder (or the target's package) from `kit.export`, with a readme that lists what the
  replacement inherits (data lines, upgrade lists, groups, sounds).
- The `asset.check` report and the lint result at the tier preset.
- The lineup sheets of G1, G2 and G5; `asset.json` with the decisions.

## 5. Acceptance

- `asset.check`: no structure or semantic errors; every out-of-band row has a fix or a written reason.
- `asset.lint --preset <tier> --baseline vanilla` and `mod.check`: no errors.
- The user approves the final lineup next to two vanilla peers of the class.

## 6. Notes

<anything else: deadline, things to avoid for this particular asset, links>
