# Creating assets in Blender with satk

[Русская версия](../ru/authoring.md)

<!-- User page (people). The agent-facing version of this workflow is S25 in docs/agent/workflows.md. -->

satk lets an AI assistant (or you) build a new GTA San Andreas asset of any kind - a car, a bike, a boat, a
street prop, a building with its low-detail version, an interior, a weapon, a pickup or a vehicle upgrade - in a
live Blender session, step by step, and compare it with the stock game at every step. The result is a ready Mod
Loader folder. The style rules it follows are described in [sa-style.md](sa-style.md).

## How it works

- **A live Blender session.** satk starts its own Blender with a separate profile (your Blender settings and
  add-ons are never touched). The assistant sends one modelling step at a time (add a primitive with a given
  number of segments, extrude, add a mirror or bevel modifier, place a reference photo...) and gets back the
  numbers that matter (triangles, shading, size against stock models) and, when asked, a small picture.
- **You can watch.** With `--gui` the same session opens a Blender window; what you change there is recorded too.
- **Nothing is lost.** Every step is written to a journal and the scene is saved as checkpoints, so a build can
  be replayed or rolled back, and a new assistant session can continue where the last one stopped.
- **Templates instead of guesses.** A template copies the frame names, dummies, material settings and
  proportions of a similar stock model (names and numbers only, never its geometry), and a semi-transparent stock
  model can be shown next to yours.

## Before you start

- Blender 5.1 is installed and `satk doctor` shows the `blender` check as ok.
- The stock game index is built (`satk index build`); the clean game copy is the reference.
- Decide: replace an existing model or add a new one. A stock game has no free vehicle slots, so new cars replace
  a stock car (or need a limit adjuster).
- Optional: reference photos of what you want. Write a short brief from the template
  [`asset-brief.md`](../agent/briefs/asset-brief.md); `satk style brief-check <brief.md>` points out wording that
  leads to typical mistakes.

## The six gates

| Gate | What happens | What you see |
|---|---|---|
| G0 setup | the project folder, the detail tier (`sa_plus` by default), the stock model to compare with, the photos | the class numbers |
| G1 blockout | the rough shape at the right size | a picture next to two stock models of the class, within about 15 minutes; say what is wrong now, before details |
| G2 shape and shading | the final silhouette, smooth shading with crisp seams | a second picture: approve it before parts and textures |
| G3 parts | doors, bonnet, damaged versions, low-detail version, collision | pictures of the damaged and low-detail states |
| G4 textures | texture coordinates on the shared game textures, own small photo-like textures | the game-look picture and the textures at their real size |
| G5 export | the DFF, TXD and collision, the check against stock, the Mod Loader folder | the report and the final picture |

At G1 and G2 the assistant stops and asks you: proportions and silhouette are cheap to fix early and expensive
later.

## Commands

| Command | What it does |
|---|---|
| `satk asset init <folder> --kind automobile --like model:426` | starts a project (`asset.json`: kind, tier, decisions, gates) |
| `satk asset status` | a short summary of the project to continue from |
| `satk blender session start --gui` | starts the live Blender session (with a window) |
| `satk blender call <method>` | runs one modelling step and returns its numbers |
| `satk blender preview <file or session> --lineup class` | one picture with stock models of the class at the same scale |
| `satk asset check <file> --like model:426` | structure, numbers and game rules against the stock model |
| `satk kit export --replace model:426` | DFF, TXD, collision and a Mod Loader folder |
| `satk col gen <dff>`, `satk fx2d copy` | collision for a finished model made outside the kit ([colgen.md](colgen.md)); street lights and other effects copied from a stock model ([fx2d.md](fx2d.md)) |

These commands arrive with the asset-creation packages of satk; `satk mcp ops create` lists the ones your version
has.

## After the export: look, then drive

The exported files can be placed next to the map in the viewer for a quick look in context (`satk view vehicle`,
`view place`, `view ped`; [viewscene.md](viewscene.md)) and tried in the real game on a private test server:
`satk ingame start`, then `satk ingame check` ([ingame.md](ingame.md)). The game needs the one-time administrator
setup of the MTA client; satk never starts the game itself.

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
