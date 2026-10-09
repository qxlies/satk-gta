# Creating assets in Blender with satk

[Русская версия](../ru/authoring.md)

<!-- User page (people). The agent-facing version of this workflow is S25 in docs/agent/workflows.md. -->

satk lets an AI assistant (or you) build a new GTA San Andreas asset of any kind - a car, a motorbike, a
bicycle, a boat, a helicopter, a plane, a trailer, a train, a street prop, a building with its low-detail
version, an interior, a weapon, a ped skin, a pickup or a vehicle upgrade - in a live Blender session, step by
step, and compare it with the stock game at every step. The result is a ready Mod Loader folder, complete from
every angle. The style it follows is described in [sa-style.md](sa-style.md): a look (soft, rounded, joined
forms, small soft textures), not a polygon count.

## How it works

- **A live Blender session.** satk starts its own Blender with a separate profile (your Blender settings and
  add-ons are never touched). The assistant sends one modelling step at a time and gets back what changed, plain
  counts, the problems it should fix (a part floating next to the body, a gap, an opening you can see through)
  and, when asked, a small picture.
- **Form first.** Bodies are built from rounded cross-sections and profiles (like a modeller drawing sections
  through the car), then the parts are joined into one whole, then the details are added, then the surface. A
  ready-made generic body exists as a quick start, but it never decides how your car looks.
- **You can watch.** With `--gui` the same session opens a Blender window; what you change there is recorded too.
- **Nothing is lost.** Every step is written to a journal and the scene is saved as checkpoints, so a build can
  be replayed or rolled back, and a new assistant session can continue where the last one stopped.
- **Templates for the structure.** A template copies the frame names, dummies and material settings of a similar
  stock model (names and numbers only, never its geometry); the dummies are then moved onto your model's own
  lamps, exhaust and hinges.

## Before you start

- Blender 5.1 is installed and `satk doctor` shows the `blender` check as ok.
- The stock game index is built (`satk index build`); the clean game copy is the reference.
- Decide: replace an existing model or add a new one. A stock game has no free vehicle slots, so new cars replace
  a stock car (or need a limit adjuster).
- Describe what you want: the real object, what makes it recognisable, what should be modelled inside (interior,
  engine bay). Write a short brief from the template [`asset-brief.md`](../agent/briefs/asset-brief.md);
  `satk style brief-check <brief.md>` points out wording that leads to typical mistakes (for example asking for
  a "boxy" car, which an assistant builds literally).

## The six gates

| Gate | What happens | What you see |
|---|---|---|
| G0 design | the project folder, the stock model to compare with, the real object's measurements, a list of its recognisable features, one sheet with all reference photos | the reference sheet and the description: say now if a feature is wrong or missing |
| G1 form | the main volumes, rounded, at the right size, with the wheels | a picture next to two stock models of the same body type: the assistant waits for your answer |
| G2 compose | one joined body: panels cut from it, closed wheel arches, bumpers, pillars, mirrors attached; dummies moved onto the model | a picture from low angles where gaps would show |
| G3 detail | every part of the parts list: lamps in their recesses, grille, mirrors, handles, interior, engine bay (or the cockpit, the rotor head, the hull fittings, the facades) | close-ups of every area of the model: the assistant waits for your answer |
| G4 surface | texture coordinates on the shared game textures, small soft textures of its own, clean paint, soft shading with crisp seams | the game-look picture and the textures at their real size |
| G5 finish | damaged versions, low-detail version, collision, export, then polishing until the strict check passes, the game | the report (parts list, open problems), the final picture and the model in the game |

There are no time limits: the assistant stops at G1 and G3 because form and detail are cheap to change early and
expensive later.

## Nothing left out: the parts list and the finish line

Assistants tend to stop as soon as a model looks acceptable. satk turns the job into an explicit list and makes
the finish line a check, not the assistant's opinion.

- **The parts list.** At the design stage the assistant writes `<project>/design/inventory.json` in the project folder:
  every part and detail as an item (what it is, where it is, how it will be built, what it is attached to, at
  which stage). It starts from a list per asset kind that comes with satk and adds every recognisable feature
  of your references. How long the list is follows your ambition: simple, standard or hero (the default for
  new assets: interior, mechanics, underside and small details everywhere). Read it at G0: it is the cheapest
  moment to add what you want.
- **Every part is accounted for.** Each built piece is tagged with its item; `satk asset inventory <folder>`
  shows which items are built, missing, not attached to their parent, or rejected by the reviewer (with the
  reason). A part of the stock list that your design does not have is left out only with a written reason.
- **Close-ups of every area.** From the composition stage on, the preview draws one close-up picture per area of
  the kind (for a car the front, the rear, the sides, the wheels, the roof, the underside, the interior and the
  engine bay; for a building each facade, the roof, the entrance and the base). The assistant has to look at
  every one, including the ones nobody normally sees.
- **No gaps.** A light-leak picture shows the model black against a bright background: light inside the outline
  that is not a window or an opening is a gap to close.
- **The finish line.** `satk asset check <file> --strict` answers `done: true` only when the game rules hold, no
  defect is left (floating parts, gaps, cracks, flickering overlapping faces, flipped faces, stretched textures,
  collision that does not match the model) and every item of the list is built. Until then the assistant
  reports what is still open; it never calls the model finished on its own.

## Reference photos

Photos tell the assistant what the object looks like, not its coordinates. It writes down the recognisable
features of each photo (lamp shapes, grille, roof line, how the parts meet) and takes the overall size from the
real object's specifications. Only a true side, front or rear view taken from far away may be measured; a
three-quarter or close-up photo is described, never measured pixel by pixel. If your photos miss the rear or a
side view, add them: what no picture shows has to be guessed.

## Commands

| Command | What it does |
|---|---|
| `satk asset init <folder> --kind automobile --like model:426` | starts a project (`asset.json`: kind, tier, decisions, gates) |
| `satk asset status` | a short summary of the project to continue from |
| `satk blender session start --gui` | starts the live Blender session (with a window) |
| `satk blender call <method>` | runs one modelling step and returns what changed and what to fix |
| `satk blender preview <file or session> --lineup class` | one picture with stock models of the class at the same scale |
| `satk asset check <file> --like model:426` | game rules, joined parts, fit of lamps, hinges and pivots, missing details |
| `satk asset check <file> --like model:426 --strict` | the finish line: `done: true` only without defects, gaps and missing parts |
| `satk asset inventory <folder>` | the parts list: built, missing, not attached, rejected (with the reason) |
| `satk blender preview <session> --regions all` | one close-up picture per area of the model |
| `satk kit export --replace model:426` | DFF, TXD, collision and a Mod Loader folder |
| `satk col gen <dff>`, `satk fx2d copy` | collision for a finished model made outside the kit ([colgen.md](colgen.md)); street lights and other effects copied from a stock model ([fx2d.md](fx2d.md)) |

These commands arrive with the asset-creation packages of satk; `satk mcp ops create` lists the ones your version
has.

## After the export: look, then drive

The exported files can be placed next to the map in the viewer for a quick look in context (`satk view vehicle`,
`view place`, `view ped`; [viewscene.md](viewscene.md)) and tried in the real game on a private test server:
`satk ingame start`, then `satk ingame check` ([ingame.md](ingame.md)): lights, doors, damage, and for a
motorbike whether the rider sits on it and the steering stays inside the body. The game needs the one-time
administrator setup of the MTA client; satk never starts the game itself.

## Installing the result

- A replacement is a Mod Loader folder under `<workspace>\work\out\`: copy it into the `modloader` folder of your game
  (never into the clean copy). The readme in the folder lists what the replaced model keeps (handling, upgrade
  list, sounds).
- A new model (when a slot is free or with a limit adjuster) gets a free id and its data lines.
- Check the folder before publishing: `satk mod check <folder>`.

## Quick example

```powershell
satk mcp ops create
satk help blender
```

The first command searches every satk operation for "create"; the second prints the Blender notes, with the list
of Blender operations of your version.
