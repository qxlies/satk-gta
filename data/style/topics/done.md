# Done: the inventory, close-up regions, the strict check and the review (every kind)

Models fail by stopping early: the task is an item list; checks and a reviewer decide "done", never
the builder. An asset is done only when ALL hold:
1. `asset.inventory <project>` answers `complete: true` (every required item `built`; a reviewer's
   rejection stays open until rebuilt and accepted);
2. `asset.check <package> --strict` answers `done: true` (`blocking` empty: no engine error or warning, no
   form/fit/mesh defect, no warning beyond the vanilla range (`advice` lists the others: polish them), no
   checklist item missing, the inventory complete, a full recorded `look.leak` of EVERY DFF (the model and
   its LOD: `checks/<stem>.leak.json`) without gaps);
3. every close-up region sheet was reviewed and passed;
4. the reviewer (user or critic) passed the final sheets.

## Builder rules
- Never write "complete/finished/ready" while `done` is false or an item is open.
- G0, before the first mesh: `design/inventory.json` (`asset.init --kind --detail` writes the kind's
  starter list; `inventory.validate <dir> --strict`). Its kind and detail are the commission's
  (`asset.json`). Add every feature of `refs/features.md` and of the design; split "interior" into seats,
  dash, wheel, door cards; left and right apart (`count: 2`).
- Tag in the batch that builds: `scene.tag` {`objects` | `object` + `select`, `item`}. Untagged = missing.
- Built = final SA form, joined to its `attaches_to`, with its material. A tagged face of another part (no
  vertices of its own), a placeholder or the template ghost is NOT built: extrude, inset+extrude or model
  the part. Texture items (`category: decal`) are built by an image texture on their faces.
- Drop a starter item only at G0, as a waiver with a design fact (`"waived": [{"key", "why"}]`, a lower
  count `{"key", "count", "why"}`): the design lacks it, the engine cannot show it, the user dropped it.
  Effort, time, budget, complexity, distance or visibility are refused. After G0 never remove, make optional,
  defer or loosen an item (count, attaches_to, tolerance). Rejections are the reviewer's
  (`inventory.mark --reject`); never clear one.
- A gate handed in is `review` (`asset status --record`); `done` needs its items built; `skipped` a why.
- After G2, G3, G4 and at the end: `blender.preview <subject> --regions all`; look at EVERY sheet, hidden
  ones too (underside, interior, engine_bay, roof, lod).
- `look.leak <dff> --package <dff folder>` after G2 and at G5, model and LOD: close each gap (`gap`,
  `see_through`, `inside`, `escape`; 3D point, nearest part).
- Polish loop after G5: export, leak, `asset.check --strict`, fix the first `blocking` rows, re-render the
  touched regions, repeat until `done: true`; a tool gap is reported with evidence, never hidden.
- Never delete, simplify or reject a built item to pass a check, or move geometry for a number.
- Answer every critique point on its own line: fixed how, or why not.

## Inventory item fields
`id` (I01), `key` (starter item), `name`, `region` (its `views` name the close-ups), `category` (shell,
panel, glass, lamp, trim, wheel, ..., other: the guide), `construction` (12+ chars), `attaches_to` (null
only for shell, wheel, structure, body_part; else an id or a list), `stage` (G1-G4), `required`, `count`,
`frame`, `max_gap_mm` (<= 150), `verify` (geometry, texture, paint). Several items in one object: tag faces. Statuses: `built`; `missing`;
`unattached` (a piece beyond the tolerance); `rejected` (reviewer, placeholder, face patch, hidden tags).
`kit.export` writes `<stem>.inventory.json` beside the DFF.

## Defect classes (fix every one)
Floating part (`form.floating`, `unattached`) -> `mesh.attach snap`. Gap/see-through (`form.see_through`,
leak) -> liner, weld, bridge, `mesh.flare`. Crack (`mesh.crack`) -> weld. Buried part (`form.intersect`).
Hard folds (`form.hard_corners`) -> `kit.shade`. Wasted density (`form.dense_flat`; map: defect) -> fewer
sections. CG-clean texture (`tex.look`, advice) -> `texture.finish --photo`. Z-fighting (`mesh.zfight`) -> offset or delete. Flipped
(`mesh.flipped|inside_out|normals`) -> normals out. Stray (`mesh.degenerate|unused|stray|far`) -> delete.
UV (`mesh.uv_smear|texel`) -> re-unwrap. Collision (`col.outside|uncovered`) -> `kit.col`. Function
(`fit.*`) -> refit frames. LOD outline (`lod.silhouette`) -> `kit.lod` or blocks. Asymmetry (`sym.*`;
blocks for items counted in pairs). Missing detail (`cov.*`, inventory). Placeholder (reviewer).

## Review (critic), in order
1. Inventory: EVERY missing/unattached item; refuse weak waivers (G0 too); reject a fake `built` item:
   `inventory.mark <dir> <id> --reject "<reason>" --by critic` (`--accept` once fixed).
2. Strict: every `blocking` row is a finding unless it is a reported tool gap with evidence.
3. Regions: on each sheet find the region's items (verify at least 3 `built` items are real, finished,
   attached); look for gaps, floats, stripes, dark faces, stretch, seams, boxy forms. Pass or fix per region.
4. Identity vs `refs/features.md`; 5. style vs two vanilla peers (wire view too); 6. hero definition.
Scores anchor to vanilla at the same size and render: 9-10 = indistinguishable in the lineup; 7-8 = SA with
a difference seen side by side; 5-6 = clearly not vanilla. Caps: `blocking` -> technical <= 6;
`form.dense_flat` -> sa_style <= 7 (map <= 5); look advice on a main texture -> sa_style <= 7; 9+ names the
peers compared (the first atelier bin got 9s: dense, CG-clean).
Pass only with inventory complete, `done: true`, every region passed. Findings: part, problem, evidence,
instruction; <= 8 fixes per round besides the items. Never pass "for now".

## Final report
Stage; strict `done`, first `blocking` rows; inventory counts, every open item and waiver; region table
(region, sheet, verdict); sheets, package; open issues with evidence. Per kind:
`satk_help("style_kinds")`. Full guide: docs/agent/style/done.md
