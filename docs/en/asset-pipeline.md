# How to build an asset with AI agents with satk

[Русская версия](../ru/asset-pipeline.md)

## What it is

Use one capable builder, a cheaper independent critic and a final polisher to make an asset through six gates.
You can coordinate them by hand in your own AI clients: satk supplies the live Blender session, inventories,
previews and checks. The maintainers' agent harness is not distributed. Human modellers can use the same method.

Describe the San Andreas style in words, backed by vanilla examples: soft, rounded, simplified forms, joined
parts, small photo-like textures and the lighting of the asset class. Read [sa-style.md](sa-style.md) and the
[construction guide](../agent/style/construction.md). Counts describe a mesh; they do not decide its style.
Models tend to stop at the first plausible result, so give every stage an explicit task list and a second pass.
The builder hands work in for review and never declares itself done.

## Quick example

These commands list a prop's starter tasks and the guidance used by the three roles; they need no game data.

```powershell
satk inventory starter prop --detail standard
satk help style_construction
satk help done
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk asset init <name> --kind prop --detail standard --like model:1300` | `satk_op` → `asset.init` | create the project and its starter inventory |
| `satk inventory validate <project> --strict` | `satk_op` → `inventory.validate` | check the design list and its waivers |
| `satk blender call scene.tag --session <name> --params '{"object":"body","item":"I01"}'` | `satk_op` → `blender.call` | associate geometry with a task |
| `satk asset inventory <project> --session <name> --stage G2` | `satk_op` → `asset.inventory` | report the stage's built, missing, unattached and rejected items |
| `satk blender preview session:<name> --regions all --lineup class` | `satk_op` → `blender.preview` | region sheets and a lineup beside vanilla peers |
| `satk look leak <model.dff> --package <package>` | `satk_op` → `look.leak` | record a full leak check for the exported bytes |
| `satk asset check <package> --strict` | `satk_op` → `asset.check` | return `done`, `blocking` and `advice` |

## The six gates

The coordinator opens a gate with its tasks, sends the resulting evidence to the critic and returns fixes to
the **same builder session**. Allow at most two fix rounds per gate; if it still fails, pause that gate and
report its open findings to the user. A limit on rounds bounds cost; it does not turn a failure into a pass.

| Gate | Produce and show | The critic checks |
|---|---|---|
| G0 design | a written design, reference features, named vanilla peers, scale anchor, region list and `<project>/design/inventory.json`; a reference board when licensed references are available | identity, plausible proportions, construction of each part, every required feature and justified design waivers |
| G1 form | the main shell and ground contact, neutral clay views from front/back/sides/top, a silhouette sheet and a peer lineup at one scale | volume, crown, section transitions, stance and character before decorative detail |
| G2 compose | openings cut into the shell, caps, liners and secondary volumes; full sheets, all region close-ups, inventory and a leak pass | contact lines, thickness, cavities, ground contact, hidden sides and whether pieces belong to one object |
| G3 detail | every detail item and its attachments; updated region sheets, wire/clay views and peer lineup | readable housings, seams, mechanisms and small forms; no placeholders or omitted back/underside |
| G4 surface | UVs, painted atlas or class paint keys, shading and prelight; game-look sheet, native-size texture sheet, region close-ups and peer lineup | softness, material identity, UV seams, painted light, appropriate wear and consistency with the peers |
| G5 finish | exported DFF/TXD/COL, LOD or damage states where applicable, package and inventory sidecar; collision/LOD views, final regions, lineup, recorded leak checks and strict report | exported result, complete inventory, no strict blockers, all regions passed, package contents and remaining limitations |

## Inventory and the finish line

At G0, start with the commissioned kind and detail level. Extend `<project>/design/inventory.json` with each design
feature, construction method, parent (`attaches_to`), region and stage. Keep starter keys; a feature the design
really lacks needs a written waiver. After G0 the builder must not drop items, reduce counts, move tasks later
or loosen attachment tolerances to pass. See [inventory.md](inventory.md) for the schema and evidence rules.

Tag objects or selected faces with `scene.tag` as they are built. `asset inventory` measures their geometry and
attachments; a tag on a hidden ghost or a recoloured face cannot stand in for a modelled part. Reviewers can
reject an item with `inventory mark --reject` and must explicitly accept the repaired result.

Done means all four conditions in the [definition of done](../agent/style/done.md) hold: every required item
is built, `asset check --strict` returns `done: true`, every required region passed, and the independent reviewer
passed the final sheets. Strict mode reads engine/form/fit/symmetry/coverage/reference/mesh/texture evidence;
reference counts remain information. `blocking` identifies unfinished work; `advice` keeps non-blocking notes.

Run `look leak` on **every exported DFF, including its LOD**, at the default full coverage, with `--package`.
The reports in `<package>/checks/<stem>.leak.json` are tied to the DFF hash and satk's own run record: export changes require
new checks. A region-only preview is useful during modelling but does not replace the recorded full run.
Designed openings need explanation and proper walls or liners; never edit the leak result to make it pass.

`blender preview --regions all` exposes the top, base, backs and recesses that a flattering overview hides.
Open every returned sheet; account for any skipped region instead of counting it as passed. Use `--lineup class`
with the project's `--like` model, or name suitable peers explicitly. Compare at the same scale, camera and
lighting, including game-distance views. A check can detect defects; the critic still has to look.

## Prompts for the three roles

Supply the commission, project/session names, current gate, task list, relevant style pages, peer names and
evidence paths. The following templates are portable; the English [agent brief](../agent/briefs/asset-roles.md)
provides the same role contracts for model-facing use.

**Builder**

> Build the commissioned asset in the existing satk Blender session. Read the construction, reference and
> per-kind guides. For this gate, list its inventory items, build and tag each, inspect the sheets, then do
> a second pass. Describe rounded sections, crowns and how parts meet. Preserve the approved design list.
> Return the gate, inventory counts and open items, checks, region evidence and each answered finding.
> Hand the gate to the critic; completion is the coordinator's decision from checks and independent review.

**Critic**

> Review the supplied gate independently. Open the full sheet, every required region and the vanilla lineup.
> Score form, composition, detail and surface separately from 1 to 10, only where observable at this gate.
> Anchor 5 to a recognizable but visibly weaker result, 7 to broadly peer-like work with visible weaknesses,
> and 9 to matching the named peers at the same scale and light; 10 needs an explained improvement in the
> same style. Mark unobservable criteria pending. Cite a peer and sheet for every score. List deviations even
> on a pass. Give pass/fail, blocking items and up to eight actionable visual fixes, most visible first.
> Gate G5 passes only with complete inventory, strict `done: true` and every required region passed.
> Return fixes to the same builder session; after two failed fix rounds escalate the open gate.

**Polisher**

> Continue the accepted builder scene and preserve its identity, inventory and working parts. Walk every
> region and resolve the critic's remaining findings and strict blockers. Improve weak joins, UV transitions,
> shading and surfaces in the peer's style. Re-export, rerun full leak checks for all changed DFFs, strict
> checks and affected sheets. Return a finding-by-finding repair report and final evidence for independent
> approval; unresolved issues remain explicit.

Scores explain a judgement; a high average cannot cancel an absent item or a strict blocker. Early gates pass
their own deliverables, while unfinished later-stage criteria remain pending. The coordinator records approval
only after reviewing the evidence; satk does not run critics or assign these visual scores.

## Failure modes and guards

| Failure seen in practice | Guard |
|---|---|
| measuring pixels in perspective photos | describe features in words; derive ratios only from true elevations, anchor scale to a peer or ped |
| a collection of boxy primitives | build changing rounded sections and joined volumes; judge G1 in clay before surfacing |
| floating caps, handles or trim | require `attaches_to`, inspect contact close-ups and use `mesh.attach` where appropriate |
| dirt becomes the whole material | review a clean material first; compare wear to the peer, and keep vehicle paint keys compatible with engine dirt |
| chasing triangle counts | add geometry where the outline turns or a part needs it; investigate dense flat areas without treating counts as a style grade |
| the builder declares itself done | explicit stage lists, mandatory second pass, independent gate decisions and four finish conditions |
| a check is gamed with tags, waivers or edited reports | freeze the G0 list, reject placeholders, require geometry and recorded hash-bound leak evidence, inspect every region |

## Worked example: a small street litter bin

This is a textual construction plan, not a supplied finished asset. Use `pipeline-bin`, kind `prop`, detail
`standard`, donor `model:1300` (`bin1`), peers `CJ_BIN1` and `CJ_WASTEBIN`, and target dimensions 0.63 × 0.63 ×
1.09 m. The numbers describe this design. All generated files stay under your own `work` folder.

**G0.** Create the project, then edit its generated inventory to name the shell, ground-contact base, rounded
top, recessed mouth with inner walls, pressed bands, fixings, seams, painted surface and any label. Keep the
starter keys and assign actual ids to the added items. Build fixings and seams where the design has them;
otherwise justify their omission at G0. Store a features list and approve it before modelling.

<!-- docs-smoke: skip the worked example needs an approved design, Blender 5.1 and a vanilla index -->
```powershell
satk asset init pipeline-bin --kind prop --detail standard --like model:1300
satk inventory validate pipeline-bin --strict
satk blender session start --project pipeline-bin --name pipeline-bin --no-resume
satk kit template --like model:1300 --name sa_bin1 --dims 0.63,0.63,1.09 --session pipeline-bin
```

**G1.** Lathe a closed shell with a rounded base and crown. The profile follows the exterior up to the crown
and back along the axis. Its 16 radial sections are a construction choice, assessed on the silhouette beside
the peers. Tag the shell and base on their real faces; use the ids in the approved inventory.

```powershell
satk blender call mesh.lathe --session pipeline-bin --params '{"name":"bin_body","segments":16,"axis":"z","profile":[[0,0.03],[0.26,0.03],[0.29,0],[0.29,0.09],[0.275,0.12],[0.275,0.86],[0.315,0.89],[0.315,0.96],[0.29,1.03],[0.17,1.09],[0,1.09]]}'
satk blender call scene.tag --session pipeline-bin --params '{"object":"bin_body","item":"I01"}'
satk blender preview session:pipeline-bin --passes clay --lineup class
```

**G2.** Select the front shell faces at the mouth, inset the surround, then delete the inner faces to cut the
mouth from the shell. Give the remaining shell inward thickness: the rim and inner walls form a real cavity
with a bottom. Inspect the opening and its thickness, rather than hiding the shell behind a black plate.
The builder must inspect and refine the selected region before accepting the result.

```powershell
satk blender call mesh.inset --session pipeline-bin --params '{"object":"bin_body","select":{"where":["y<-0.2","z>0.85","z<0.97"]},"thickness":0.01,"save_group":"mouth"}'
satk blender call mesh.delete --session pipeline-bin --params '{"object":"bin_body","select":{"group":"mouth"}}'
satk blender call mesh.solidify --session pipeline-bin --params '{"object":"bin_body","thickness":0.008,"offset":-1}'
satk asset inventory pipeline-bin --session pipeline-bin --stage G2
satk blender preview session:pipeline-bin --regions all --passes clay,leak
```

**G3.** Cut loops at the band heights and move those rings outward a little: pressed bands grow from the shell.
Tag each approved detail on its own geometry and inspect the rear, mouth, top and base. The following ring is
one detail step; repeat only where the approved design calls for a band.

```powershell
satk blender call mesh.loopcut --session pipeline-bin --params '{"object":"bin_body","axis":"z","at":[0.25,0.28,0.70,0.73]}'
satk blender call mesh.transform --session pipeline-bin --params '{"object":"bin_body","select":{"loop":{"point":[0.275,0,0.28],"dir":"y"}},"scale":[1.025,1.025,1],"pivot":"origin"}'
satk blender preview session:pipeline-bin --regions all --passes clay,wire --lineup class
```

**G4.** Paint a soft 128 × 128 atlas: muted green metal, darker recess, modest wear where the peers show it.
The commands create a starting page; paint the approved atlas regions before finishing it. Lathe UVs already
run around the body; check their scale and the UVs of the new recess and band faces. Apply the finished page and
review it in the game look, including the native-size texture sheet.

```powershell
satk texture new sa_bin1 --size 128 128 --color "#5c7a68"
satk texture finish new/sa_bin1.png --role prop
satk blender call kit.material_preset --session pipeline-bin --params '{"object":"bin_body","role":"map","slot":0,"image":"work/out/texmod/finish/sa_bin1-photo_like.png"}'
satk blender preview session:pipeline-bin --regions all --passes game,tex --lineup class
```

**G5.** Fill the kit slot with the reviewed geometry, export collision and prelight with the Mod Loader package,
and inspect the **returned** package path. Preserve the inventory sidecar. Run full leak and strict checks on
that package, then show collision, every region and the final peer lineup. Re-export and repeat checks after
any repair. The last commands use placeholders because export returns the actual output paths.

```powershell
satk blender call kit.fill --session pipeline-bin --params '{"slot":"sa_bin1","objects":["bin_body"]}'
satk kit export --model sa_bin1 --session pipeline-bin --add --col auto --prelight bake
```

<!-- linkcheck: off -->
```powershell
satk look leak <package>/sa_bin1.dff --kind prop --package <package>
satk asset inventory pipeline-bin --dff <package>/sa_bin1.dff --strict
satk asset check <package> --strict
satk blender preview <package>/sa_bin1.dff --regions all --lineup class --like model:1300 --states ok,col
```
<!-- linkcheck: on -->

The hand-in lists the reached gate, inventory counts and waivers, `done` and all blockers, a verdict and sheet
for each region, package files and open issues. A rendered sheet alone is not a playable package.

## Limitations and known issues

Blender authoring needs Blender 5.1; vanilla comparisons need your own indexed game. Review scores and stage
decisions are external to satk, and automatic checks do not prove visual quality or live game behaviour.
The viewer and MTA fork are not distributed; use the Blender evidence without claiming a live game test.
`sa_plus` measurements are reference, not a promise of performance or a target score.
