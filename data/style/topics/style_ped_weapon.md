# SA style: peds and weapons

Both are dynamically lit: normals, no prelight, no night colours. Numbers are vanilla reference (p10 / p50 /
p90), never targets; more detail in the same style is fine.

## Peds (265)
- `geo.tris[ped]` 944 / 1,079 / 1,274 (max 2,048); sa_plus may add smooth detail with the SAME skeleton.
- Skinned: 33 frames = root + 32 named bones, identical names and order on every ped; at most 4 weights per
  vertex; one material. Breaking bone count, ids or order crashes the game (CrashInfo #130, lint `ped.skin`).
- One texture (242 of 265 peds), 128x256, UNCOMPRESSED X8R8G8B8 (253 of 289 ped textures), no mips, TXD 128 KB.
  Atlas: torso, sleeves, legs, shoes, hands; the face in the lower right at the highest texel density. Texel
  density 115 / 135 / 175 px/m. sa_plus 256x512 (512 KB TXD, 4x vanilla: check the streaming budget).
- Fully smooth shading: `shade.normal_bend` 26 / 29 / 35 deg, flat share 0.00 / 0.00 / 0.005.
- Look: folds, seams and shadows painted in, photographic face; texture luminance 46 / 80 / 133 of 255,
  saturation 0.08 / 0.25 / 0.45.
- Height 1.84 m in the bind pose (Y up): the scale anchor of every non-vehicle asset.
- Authoring today: re-skin (new atlas on the vanilla mesh) or vertex edits that keep the skin;
  `kit.template --kind ped` gives bone names and ids only. New skeletons and posing are out of scope.
- A replaced ped inherits `pedgrp.dat`, `ped.dat` and its voice; only 2 ped slots are free in a stock game.

## Weapons (50)
- `geo.tris[weapon]` 60 / 193 / 578 (minigun 1,254); materials 1 / 1 / 2. Build from the side profile (sweep or
  extruded outline, rounded grip), never from a stretched box.
- Length (`dims.size`) 0.20 / 0.72 / 1.73 m: model at real length against the 1.84 m ped.
- 1-3 atomics: the muzzle flash is a separate `gunflash` atomic at the muzzle tip (lint `weap.flash`). Keep
  the origin and axis of the vanilla weapon of the slot (barrel along +X; the hand holds it where the vanilla
  one is held); magazine, sights, scope and mounts push into the body, nothing floats.
- Textures: one photo of the real weapon (64x64, sa_plus 128) unwrapped onto both sides, a 64x64 DXT3 HUD icon
  (white silhouette, black outline), a 32x32 DXT3 muzzle flash on crossed planes; DXT1 51 / DXT3 16 textures, no
  mips; TXD 4 / 6 / 8 KB. Texel density 106 / 227 / 398 px/m (sa_plus 1.5-2x p50).
- Shading: firearms harder-edged (flat share 0.39-0.69 on m4, micro_uzi, sniper, colt45); melee and round items
  smooth (bend 24-49 deg). `shade.normal_bend[weapon]` 7.2 / 23 / 34.
- Draw 50 (30 for handguns and SMGs); collision on 10 % only (one sphere).
- A replaced weapon inherits its `weapon.dat` line; only one weapon slot is free in a stock game.

## Checks
`asset.check <dff> --like model:<id> --strict` (structure, skin, defects; `done`); `blender.preview --like
model:<id>` puts the asset next to the vanilla one with the same camera, `--regions` gives the close-ups
(weapon: both sides, top, muzzle, in the hand; ped: face, torso, back, legs, hands, the UV-border seams);
`style.texture <png> --role ped|weapon`. Items and done: `style_kinds`, `done`.

Full guide: docs/agent/style/peds-weapons.md
