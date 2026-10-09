# Agent evals (fresh-session check)

<!-- English only. The fresh-session runs need a person to start a new AI client session; the scripted baseline
     below is what the doc author measured. -->

Goal: a fresh AI client session that has only the documents (the workspace `CLAUDE.md` / `AGENTS.md`, skill
`satk`) and the MCP server `satk` solves each scenario in **<= 8 tool calls**, without grepping game files and
without reading design notes or the satk source code. Any client registered with `satk mcp config --client <client>` and
`satk agent install-skill` qualifies (Claude Code, Codex, Cursor, ...); record the client in the results.

## Protocol

1. Preconditions (check with `satk status`): index `vanilla` built, symdb built (`re.built`), viewer built and
   not running, bookmark `bm:grove_center` exists (`satk view bookmark list`; to recreate:
   `satk view goto --pos 2490,-1655,16 --look 2500,-1700,16` with the viewer running, then
   `satk view bookmark save grove_center`). Record `satk version` (branch, commit).
2. Start a NEW session of the client in the workspace folder (so its instructions file, the MCP registration and
   the skill are loaded; approve the MCP server `satk` when asked). Give the prompt below verbatim (in the user's
   language if the user works in another one). No hints.
3. Count every tool call (MCP tools, Read of images, Bash). Note the tokens if the client shows them.
4. Score: PASS = correct answer (see "expected") in ≤ 8 calls; SOFT = correct in 9–12 calls;
   FAIL = wrong answer, > 12 calls, or a forbidden action (writing to protected roots, grepping IMG files,
   starting the MTA client).
5. Any FAIL or SOFT → fix the docs/tools (skill wording, hints, defaults) and re-run that scenario.
6. Write the row into "Results" (date, commit, calls, tokens, result, notes).

## Scenarios

### E1 (= S1) texture

- Prompt: "Where is the texture ws_rooftarmac1 used and what does it look like? Show me".
- Expected: 257 TXDs contain a texture with that name; 470 models use it (top: `portakabin`,
  `box_hse_10_SFXRF`); only 3 distinct images (`pix:45f2…` in 243 TXDs); ONE contact sheet shown, no per-texture
  image spam. Calls: 5 in the scripted chain.

### E2 (= S2) viewer

- Prompt: "Fly to Grove Street and tell me what the building on the left is".
- Expected: starts the viewer if needed (preferably `window="960x540"`), one capture with marks from
  `bm:grove_center` (or an equivalent street-level pose), answer with an `inst:` SID, its model name and IPL.
  For `bm:grove_center`: CJ's house `inst:lae2_stream0#8` (`carlshou1_lae2`) ahead, the building on the left is
  `inst:lae2_stream0#39` (`model:3646` `ganghous05_LAx`, `ipl:lae2_stream0`; mark 5 of the 2026-10-05 capture).
  Another pose is fine if the answer matches its own frame. Calls: 6 in the scripted chain (status, start,
  capture, Read, pick, asset_get).

### E3 (= S3) crash address

- Prompt: "MTA crashed: Offset = 0x0013BF09 in gta_sa.exe. Which function is it and which MTA patches are near?"
- Expected: `CGame::Process+0x29` (`game_sa/Game.cpp:78`), confidence `high`, patch at the address
  `HOOKPOS_CStreaming_Update_Caller` (trunk `Client/multiplayer_sa/CMultiplayerSA.cpp:34/638`); 22 patch sites in
  the function. Calls: 3 in the scripted chain.
- Trap to watch: the prompt gives a bare module offset. `re_addr(text=["0x0013BF09"])` answers
  `confidence:"none"`, "address outside the gta_sa.exe image" (costs a call); the skill (S3, section 9) and the
  workspace `CLAUDE.md` say to pass `gta_sa.exe+0x0013BF09`. Note in the results whether the agent fell into it.

### E4 (= S9 + S10) crash dump without a tool of its own

- Prompt: "Here is an MTA crash dump: <path to a .dmp>. Why did the game crash?" (for a dry run use the sample from
  `satk crash sample`).
- Expected: `satk_ops("crash")` or straight `satk_op("crash.analyze", {"path": ...})`; answer with the crashing
  function and source line (`CStreaming::Update+0x33`, `Streaming.cpp:111` for the sample), whether MTA patches it,
  and the full pool (`ped 140/140`). Calls: 2-3.

### Creation scenarios E5-E7 (= S25, S26)

These take tens of calls, so the 8-call rule of the protocol does not apply. Score by the look and the defects,
never by counts: PASS = every gate reached with its sheet shown (human checkpoints at G1 and G3), the features of
the brief recognisable, the SA look next to vanilla peers (soft, rounded, joined, soft textures), no engine errors
and no open `form`/`fit` defects in `asset.check`; SOFT = reached with open form or fit defects, missing checklist
items or a missed checkpoint; FAIL = vertex-by-vertex geometry (a vertex list or a private mesh library instead of
session methods), measuring code for photos (grids, solved cameras, back-projection), geometry changed only to
move a number, a full-size image opened, or a forbidden action. Record calls, wall time to G1 and to the first
export and the context size at the first export as information.

#### E5 car to the composition gate (G2)

- Prompt: "Make an SA-style replacement for the Premier (model 426) inspired by <a real sedan>. Get it to the
  composition gate and show me."
- Expected: reads `style`, `style_construction`, `style_references` and `style_vehicle`; `asset.init` with tier
  `sa_plus`; a design description and `<project>/refs/features.md` from the sedan's spec sheet and photos (described, not
  measured); `kit.template --like model:426`; body from rounded section lofts (or a reshaped sedan blank) with
  smooth shading; G1 lineup sheet shown and the answer awaited; at G2 one welded shell with cut panels, lined
  arches, wrapped bumpers, mirrors on stalks, dummies refitted, and the `form`/`fit` rows of `asset.check` clean;
  the composition sheet shown.

#### E6 street prop with collision (and the LOD question)

- Prompt: "Make a new bus shelter for Grove Street with collision, in the stock style."
- Expected: kind `prop`; closed pieces that touch (posts, roof, bench, panels), no gaps; texel density like the
  size bucket (`uv.texel_px_m`, reference); tiling or 0..1 UVs as the class does;
  prelight with warm night colours (`blender.game_ready --asset-class prop` or the kit); collision from primitives
  (`col.gen`) with a non-zero face light; draw distance at most 100-299; NO LOD unless the model is about 30 m or
  more (props almost never have one) - the agent should say why.

#### E7 weapon

- Prompt: "Replace the baseball bat with a cricket bat in SA style."
- Expected: `kit.template --like model:336`; built from the side profile (blade and rounded handle, not a
  stretched box); real length against the 1.84 m ped; smooth shading (melee); one small soft photo-like texture
  (`style.texture` compared with the weapon role); `asset.check` without engine errors; the readme names the
  inherited `weapon.dat` line.

#### Discovery checks (no session needed)

One `satk_ops` call each (or `satk mcp ops <words>` on the CLI); expected among the first 3 rows:

| Query | Expected operation |
|---|---|
| create vehicle | `kit.template`, `blender.session` or `mod.add` |
| make txd from png | `texture.pack` |
| smooth faceted normals | `rw.patch` or `kit.shade` |
| check my model against vanilla | `asset.check` |
| preview my dff next to vanilla | `blender.preview` |
| style numbers for sedans | `style.profile` |

## Results

### Scripted baseline (not a fresh session)

`satk dev workflow-cost S1 S2 S3 --steps` (S2 with `--target ariane`) runs the documented chains through the MCP
dispatch path (`op_by_mcp` + `invoke`), counting a Read for every image the agent would open. It proves the
chains work and fit the budget; it does not prove an agent finds them.

| date | commit | scenario | calls | tokens (est.) | result | notes |
|---|---|---|---|---|---|---|
| 2026-10-04 | pre-0.1.0 (internal) | E1 | 5 | ~620 | PASS | answer 257 / 470 / 3 variants, sheet 268×268 |
| 2026-10-04 | pre-0.1.0 (internal) | E2 | 6 | ~1 280 | PASS | live Ariane, window 960×540, start 2.2 s, capture 2.6 s, left = `inst:lae2_stream0#39` |
| 2026-10-04 | pre-0.1.0 (internal) | E3 | 3 | ~1 320 | PASS | `CGame::Process+0x29`, `Game.cpp:78`, `high` |
| 2026-10-05 | 0.2.0 preview | E1 | 5 | ~620 | PASS | same answers |
| 2026-10-05 | 0.2.0 preview | E2 | 6 | ~1 310 | PASS | native SAAP/1 Ariane, exact marks (ID buffer), left = mark 5 `inst:lae2_stream0#39` |
| 2026-10-05 | 0.2.0 preview | E3 | 3 | ~1 340 | PASS | 22 patch sites in the function |
| 2026-10-05 | 0.2.0 preview | E4 | 2 | ~510 | PASS | `crash.list` + `crash.analyze --last` on the generated sample |

### Fresh-session runs

| date | commit | scenario | calls | tokens | result | notes |
|---|---|---|---|---|---|---|
| - | - | E1-E4 | - | - | not run yet | needs a person to start a new client session (protocol above) |
| - | - | E5-E7 | - | - | not run yet | need the authoring operations merged and a person to start the session |
