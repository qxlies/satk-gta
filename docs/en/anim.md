# `satk anim`: IFP animations (list, JSON, write, merge, check, Blender)

[Русская версия](../ru/anim.md)

Package: `satk.anim` (Blender side: `blender/satk_blender/anim`, GPL).

## What it is

Animations of GTA:SA live in IFP packages: `anim/ped.ifp` (walking, fighting, cars), the blocks of `anim/anim.img`
(bars, gyms, dances), the object animations in `models/gta3.img` and the cutscenes of `anim/cuts.img`.
`satk anim` reads every key frame of these files, shows what is inside, turns animations into editable JSON and
back, merges new animations into a package such as `ped.ifp`, checks an animation against the skeleton it will play
on, and moves animations to and from Blender: an IFP animation lands on a vanilla or custom ped imported by DragonFF
as an action, and an action becomes IFP key frames again; for MTA:SA it writes a client resource that loads an IFP
and replaces game animations. Every vanilla IFP (436 files, 13.7 million key frames)
comes back byte for byte. The game is only read; output goes to `<workspace>\work\out\anim\`.

## Quick example

```powershell
satk anim list ifp:ped --name walk
satk anim extract anim:ped/walk_civi --out walk.json
satk anim write walk.json --out mywalk.ifp --pack mywalk
satk anim check mywalk.ifp --sev info
satk anim merge ifp:ped mywalk.ifp --replace --out ped_mywalk.ifp
```

What comes back (shortened):

```json
{"ok":true,"cols":["anim","bones","keys","dur","root"],"rows":[["WALK_civi",32,35,1.133,"move 1.72m"],["WALK_drunk",26,38,3.933,"move 3.53m"]],"total":27,"file":"file:anim/ped.ifp","format":"ANP3","pack":"ped","anims":294,"compressed":"yes"}
{"ok":true,"rows":[["WALK_civi",32,35,1.133,"<workspace>/work/out/anim/walk.json"]],"path":"<workspace>/work/out/anim/walk.json"}
{"ok":true,"id":"<workspace>/work/out/anim/mywalk.ifp","format":"ANP3","pack":"mywalk","anims":1,"size":10594,"verified":true}
{"ok":true,"cols":["anim","sev","check","msg"],"rows":[["WALK_civi","info","root_motion","the root travels 1.72 m (+0.00, +1.72, +0.00): anims played with the movement flag move the ped by it"]],"skeleton":"sa-ped","summary":{"info":1}}
{"ok":true,"cols":["anim","action","from"],"rows":[["WALK_civi","replaced","<workspace>/work/out/anim/mywalk.ifp"]],"anims":294,"verified":true,"replaced":1}
```

## Commands

| Command | MCP | What it does |
|---|---|---|
| `satk anim list <ifp\|SID\|img\|folder> [--anim NAME] [--name TEXT]` | — (through `satk_op`) | animations (bones, keys, duration, root motion), the bones of one animation, or the IFPs of an IMG/folder |
| `satk anim extract <ifp\|SID> [NAME ...] [--all] [--out dir\|x.json\|x.ifp]` | — | animations to JSON (one file each, or one `.json`) or into a new IFP; names are case-insensitive, `*` globs |
| `satk anim write <json\|folder\|ifp> [--out x.ifp] [--format ANP3\|ANP2\|ANPK] [--compress keep\|yes\|no] [--pack NAME]` | — | an IFP from JSON (or re-encode/convert an IFP), re-read to verify |
| `satk anim merge <base> <add ...> [--replace] [--anim NAME ...]` | — | add animations to a package such as `ped.ifp`; same names are skipped or replaced in place |
| `satk anim check <ifp\|SID> [--anim NAME ...] [--skin model:7\|x.dff] [--loader game\|mta] [--sev info] [--fail-on error]` | — | bone tags/names the engine (or MTA's `engineLoadIFP`) cannot bind, root motion, key order, names, compression |
| `satk anim mta <ifp\|SID> [--replace ped/WALK_civi=mywalk ...] [--block NAME] [--name RES]` | — | an MTA client resource (`meta.xml`, `client.lua`, the IFP) that loads the IFP and replaces game animations on players and peds |
| `satk anim skeleton [model:7\|x.dff]` | — | the bones of the SA ped skeleton or a model, with the engine bone name |
| `satk anim roundtrip [folder\|img\|ifp] [--via-json]` | — | decode and encode every IFP of a target (default: the game folder) and compare bytes |
| `satk anim to-blender <ifp\|SID> [NAME ...] [--skin model:7\|x.dff] [--verify] [--render]` | — | animations as actions on a ped in Blender: `.blend`, contact sheet, round-trip check |
| `satk anim from-blender <file.blend\|job id> [--action NAME ...] [--out x.ifp] [--fresh] [--bake]` | — | Blender actions back to an IFP |

Targets: a `.ifp` path (absolute, current folder, game folder or `<work>/out/anim`), `<img>/<entry>.ifp`
(`anim/anim.img/bar.ifp`), `ifp:<pack>` and `anim:<pack>/<name>` from the index, or a bare pack name (`ped`). <!-- linkcheck: ignore -->

`anim check` codes: errors `name_length` (ANP2/ANP3 names over 23 bytes, ANPK bone names over 27), `compression`
(mixed key types or a mismatched ANP3 compression flag), `non_finite` (NaN or infinity); warnings `unknown_bone`,
`unbound_name` (with a suggested name), `tag_on_plain`, `duplicate_bone`, `tag_name_conflict`,
`root_motion_on_pelvis`, `duplicate_anim`, `empty`, `time_order`, `quat_norm`, `trans_limit`; info `root_motion`,
`no_keys`. A partial animation need not key every bone of its target: warnings identify source tracks that the
target cannot bind. Numeric checks also cover unbound tracks. Against `model:7`, vanilla `ped.ifp` has no errors,
134 root-motion findings and two warnings on the same unused prop sequence, `CAR_LjackedLHS/fam3`: `unbound_name`
and `quat_norm` (quaternion length off by 29.3%).

## The JSON form

```json
{"satk": "anim/1", "format": "ANP3", "pack": "ped", "anims": [
{"name": "WALK_civi", "index": 262, "compressed": true, "duration": 1.1333, "bones": [
 {"name": "Root", "tag": 0, "trans": true, "keys": [
   [0.0, 0.044922, -0.018066, 0.685791, 0.72583, -0.018555, 0.0, -0.068359],
   [0.033333, 0.040283, -0.020508, 0.686523, 0.725342, -0.018555, 0.050781, -0.056641]]},
 {"name": " Pelvis", "tag": 1, "keys": [
   [0.0, -0.498291, -0.498291, -0.501221, 0.501221]]}]}]}
```

- A key is `[t, qx, qy, qz, qw]` or `[t, qx, qy, qz, qw, tx, ty, tz]`: time in seconds from the start, the
  rotation quaternion of the bone relative to its parent, the translation in metres (bones with `"trans": true`).
- `tag` is the bone id (`satk anim skeleton`); `-1` binds by the engine bone name instead (`Pelvis`, `R Forearm`).
- `"compressed": true` (the default for new JSON) stores rotation x4096, time x60 and translation x1024 as signed
  16-bit integers: translation must stay within +-32 m, positive times within 546 s. Vanilla `ped.ifp` and
  `anim.img` use this form; ANPK cutscenes use float32. `false` writes float keys.
- ANPK also supports `[t, qx, qy, qz, qw, tx, ty, tz, sx, sy, sz]` with `"scale": true` and
  `"compressed": false`. The codec preserves those scale values even though the game ignores them.
- `index` restores source animation order when writing several extracted files; remove or change it to reorder
  animations. `duration` and `source` are informational. `write` retains the declared format; JSON without a
  format produces ANP3.
- `*_raw`, `extra`, `kf_tail`, `name_pad` keep source byte details for a bit-exact full extract (an editor may
  drop them). Editing `pack` or `name` overrides its raw name bytes. New names must fit the format and use Latin-1
  without embedded NULs. `"f32:7fc00000"` represents exact float32 bits for non-finite cutscene values; these
  survive JSON even though `check` reports them as errors.

## MTA:SA

<!-- docs-smoke: skip writes a resource folder; the commands are shown with the Quick example's files -->
```powershell
satk anim check mywalk.ifp --loader mta
satk anim mta mywalk.ifp --replace ped/WALK_civi=WALK_civi ped/WALK_player=WALK_civi
```

Built-in packages (`ped.ifp`, `anim.img`) are loaded by the game itself, in MTA too, so `--loader game` (the
default) applies to them. An IFP a resource loads with `engineLoadIFP` goes through MTA's own loader, which first
turns untagged bone names into ids with its own table (lower case, spaces removed: `" Pelvis"` works there but
not in the game) and keeps only its 64 bone ids: check such files with `--loader mta`. `satk anim mta` writes
`<work>/out/anim/mta/satk_anim_<block>/` with `meta.xml`, `client.lua` and the IFP: the script loads the block once,
calls `engineReplaceAnimation(ped, "ped", "WALK_civi", block, "WALK_civi")` for every streamed-in player and ped
(retrying while a game block is still loading) and `engineRestoreAnimation` when the resource stops; any animation
of the block also plays directly with `setPedAnimation(ped, block, name)`. Copy the folder into the server's
`mods/deathmatch/resources` and `start` it. The generated Lua compiles with the Lua 5.1 of MTA (tested).

## Blender

<!-- docs-smoke: skip needs Blender 5.1 and runs a headless job for every command -->
```powershell
satk anim to-blender ifp:ped walk_civi run_civi --verify --render
satk anim from-blender <job-id> --out mywalk.ifp
satk anim to-blender mymod/dance.ifp dance1 --skin mymod/myped.dff
```

`to-blender` imports the skin with DragonFF (default `model:7`, male01; any ped model or a `.dff` with its `.txd`),
creates one action per animation on its armature (scene at 30 fps, or 60 when the keys need it), saves
`scene.blend` in the job folder, and prints the job id. Replace `<job-id>` above with that value. `--render` writes
a 4-frame contact sheet (`png`). `--verify` exports the actions again in the same session and compares every
animation, sequence and key with the source. Passing requires matching structure, finite values, nonzero
quaternions and errors no greater than 0.05 degrees, 0.001 m per translation component and 0.001 s; the decision
uses unrounded errors. `exact` additionally checks the stored key values after quantization for compressed keys.

Open the `.blend`, edit an action, then `from-blender <job id or .blend>` writes the IFP. Imported actions keep
their original animation and sequence order, names, tags and unbound sequences; renaming an action changes its
exported name. New bone channels and location keys are included. A new action (or `--fresh`) gets one sequence
per animated bone in armature order, with the bone's name and id; the root bone and bones with location keys get
translation. Quaternion, Euler and axis-angle rotation modes are supported. `--compress yes|no` overrides
compression for every sequence, including stored unbound ones.

`--bake` samples the evaluated pose at every integer frame of the action's range, including constraints, IK,
drivers and unkeyed skin bones. It exports parent-relative rotation and translation for each bone. Use it when
motion depends on evaluation between the authored keys. Without baking, export uses the bone's key times.

## Verification

Measured on 2026-10-06 against the read-only GTA:SA 1.0 US copy, using Blender 5.1.1 and DragonFF `b3bd7aa`.
All Blender imports and exports used satk's public job runner and the vanilla male01 skin (`model:7`).

| Check | Files | Animations | Key frames | Result |
|---|---:|---:|---:|---|
| Entire game, binary and JSON round trips | 436 (287 ANP3, 149 ANPK) | — | 13,693,940 | 436/436 byte-identical in both forms |
| `ped.ifp`, Blender import/export | 1 | 294 | 112,150 | 294/294 exact |
| `anim.img`, Blender import/export | 133 | 1,577 | 890,817 | 1,577/1,577 exact |
| Saved `.blend` reopened, all `ped.ifp` actions exported | 1 | 294 | 112,150 | Entire 1,433,248-byte IFP identical |

The Blender sweeps had zero rotation, translation and time error after int16 quantization. An independent check
of evaluated male01 bone matrices at 916 `WALK_civi` source keys measured maximum errors of 0.000021 degrees and
0.00000030 m (limits: 0.05 degrees and 0.001 m). Synthetic Blender tests cover constraints, unkeyed bones,
Euler/axis-angle actions, added channels and
repeated or decreasing times. A loose custom `.dff` skin import is covered using a copy of male01.

Unbound sequences and earlier duplicate bindings survive in action metadata; they do not pose a bone. Exact
keyframe preservation therefore does not mean every source track binds to male01. Codec round trips cover the
cutscenes too; the Blender sweep above covers `ped.ifp` and `anim.img`.

## How it works

- **Codec.** ANP3 (SA), ANP2 and ANPK (the III/VC layout of the cutscenes) are read with every key frame kept as
  stored: int16 for compressed key frames, float32 otherwise. Byte details that do not follow from the values
  (bytes after a name's NUL, ANPK link words, non-zero padding, NaN payloads) are kept, sizes are recomputed, so
  all 436 vanilla IFPs encode back byte for byte, also through JSON (`satk anim roundtrip --via-json`). The IMG
  sector padding after a file is not part of it. `satk.formats.ifp` checks the structure first.
- **Quaternions.** The rotation is the engine's `CQuaternion` `(x, y, z, w)`; ANPK files store its conjugate (the
  loader negates x, y, z), so satk converts on read and write and the JSON is the same for every format.
- **Binding** (`CAnimBlendAssociation::Init`): a sequence with a bone tag goes to the HAnim bone with that id; an
  untagged one goes, on a skinned model, to the bone whose engine bone name (a string table of `gta_sa.exe`, not the
  DFF frame name) matches case-insensitively, and on a plain model to the frame with that name; a tagged sequence
  never binds on a plain model; two sequences on one bone: the later wins. `data/anim/ped_skeleton.json` holds the
  32 bones all 265 skinned vanilla peds share.
- **Root motion.** The root bone (tag 0, or the untagged engine name `Root`) carries the travel of walk-type
  animations in its translation (+Y is forward); the game turns it into ped movement. The last sequence bound
  to the root wins. A pelvis track is never inferred to be a root track. Travel left on the pelvis makes the ped
  snap back when the animation loops (`root_motion_on_pelvis`).
- **Blender.** DragonFF's bone rest matrices are the skin's bind matrices; with `R` = rest relative to the parent
  bone, a pose bone's basis is `R^-1 * (T(t) R(q))` and export inverts it. Quaternion signs are kept (a few vanilla
  animations flip sign between keys; new and baked actions get sign continuity). Repeated, decreasing or very
  close key times are separated by at least 1/50 frame so Blender does not merge them. Metadata maps the adjusted
  positions to source times; unchanged positions recover those times on export, including float keys. Moving an
  adjusted key uses its edited time; `--fresh` and `--bake` discard this timing restoration. The jobs run headless
  with satk's isolated Blender profile.

## Limitations and known issues

- Blender: skinned peds only (an object animation of a plain DFF is listed, checked and converted, but not applied
  in Blender). Float (uncompressed) key frames come back within float32 precision, not bit-exact.
- ANPK scale keys survive codec/JSON round trips but are not applied to bones or exported from mapped Blender
  tracks. Animated Blender scale is ignored with a warning. Non-finite transforms cannot be applied to a bone.
- `merge`, `write` and `mta` produce new files; installing them into a game copy or a server is up to you.
- Which animations the game plays with the movement flag is decided by the executable (animation group
  definitions), not by the IFP; `root_motion` only reports the travel.

## Python API

```python
from satk.anim.ifp import read_ifp, write_ifp
from satk.anim.jsonio import ifp_to_json, json_to_ifp

ifp = read_ifp(blob)
walk = ifp.find("walk_civi")
doc = ifp_to_json(ifp, [walk])
assert write_ifp(json_to_ifp(doc)) == write_ifp(type(ifp)(ifp.format, ifp.pack, [walk], ifp.raw_pack))
```
