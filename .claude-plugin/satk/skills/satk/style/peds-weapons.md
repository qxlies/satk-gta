# Peds and weapons

<!-- Model-facing, English only. Conventions: README.md "How to read the numbers". Measured on the 265 ped and
     50 weapon models of the clean 1.0 US copy (profile vanilla). Shading bands: shading.md. -->

Both are dynamically lit: normals, no prelight, no night colours. Peds are one smooth skinned mesh with one
photo atlas; weapons are small, hard-edged (firearms) or smooth (melee), with a 64 px photo texture.

## Peds

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `geo.tris[ped]` | peds (265) | 944 | 1,079 | 1,274 | 1,600-2,500 | max 2,048 |
| `mat.count[ped]` | peds (265) | 1 | 1 | 1 | same | one material |
| `uv.texel_px_m[ped]` | peds (265) | 115 | 135 | 175 | same band in `asset.check`; a 256x512 atlas gives about 2x | per-model p50 |
| `tex.txd_kb[ped TXD]` | ped TXDs (265) | 128 | 128 | 128 | 512 | uncompressed atlas |

| Fact | Value | Source |
|---|---|---|
| skeleton | skinned; 33 frames = root + 32 named bones, the same names and order on every ped | vanilla peds (265) |
| texture | one texture (242 of 265 peds), 128x256 (258 of 289 ped textures), uncompressed X8R8G8B8 (253 of 289), no mips | vanilla index |
| atlas layout | torso, sleeves, legs, shoes, hands; the face in the lower right at the highest texel density | vanilla textures |
| height | 1.84 m in the bind pose (Y up); this is the scale anchor of every non-vehicle asset | vanilla peds |
| shading | fully smooth: see `shade.normal_bend[ped]` and `shade.flat_share[ped]` in `shading.md` | vanilla peds |

- Clothing folds, seams and shadows are painted into the atlas; faces are photographic. Skin and fabric have
  moderate saturation and a mid-dark value (`tex.sat_mean[ped]`, `tex.lum_mean[ped]` in `textures.md`).
- `sa_plus` keeps the skeleton, the single material and the uncompressed format; it doubles the atlas side
  (256x512) and raises the triangles. Check the streaming cost: a 256x512 uncompressed atlas is 512 KB, 4x
  vanilla (`limits.md`).
- Construction rules: at most 4 bone weights per vertex, bone count, ids and order exactly as vanilla, one
  material. An edited skin that breaks these crashes the game (CrashInfo #130; lint rule `ped.skin`).
- Authoring path today: re-skin (a new atlas on the vanilla mesh) or edit vertices while keeping the skin.
  `kit.template --kind ped` gives the bone names and ids only (no vanilla vertices); posing and IFP work come later.
- A replaced ped inherits its `pedgrp.dat` groups, `ped.dat` entry and voice: list them in the readme.

## Weapons

| Metric | Peer set (n) | p10 | p50 | p90 | sa_plus (proposal) | Note |
|---|---|---|---|---|---|---|
| `geo.tris[weapon]` | weapons (50) | 60 | 193 | 578 | 400-1,200 | max 1,254 (minigun) |
| `mat.count[weapon]` | weapons (50) | 1 | 1 | 2 | same | |
| `dims.size[weapon]` | weapons (50) | 0.20 | 0.72 | 1.73 | same | longest side in metres, against the 1.84 m ped |
| `uv.texel_px_m[weapon]` | weapons (50) | 106 | 227 | 398 | 340-453 (1.5-2x p50: a 128 px texture) | per-model |
| `tex.txd_kb[weapon TXD]` | weapon TXDs (50) | 4 | 6 | 8 | about 4x | |

| Fact | Value | Source |
|---|---|---|
| atomics | 1 (34 weapons), 2 (13) or 3 (3): the muzzle flash is a separate atomic (`gunflash`) | vanilla index |
| textures | 64x64 (37) or 32x32 (19) DXT1 (51) or DXT3 (16, alpha for muzzle flashes); no mips | vanilla index |
| texture set | a photo of the real weapon unwrapped onto both sides, a 64x64 DXT3 HUD icon (white silhouette, black outline), a 32x32 DXT3 muzzle-flash texture on crossed planes | vanilla textures |
| draw distance | 50 (30 for handguns and SMGs) | vanilla IDE |
| collision | only 10 % of weapons, one sphere | vanilla index |
| shading | firearms harder-edged, melee and round items smooth: `shade.flat_share[firearm]`, `shade.normal_bend[weapon]` in `shading.md` | vanilla weapons |

- Hold scale: model the weapon at its real length relative to the 1.84 m ped; check it in the hand with a
  lineup next to the vanilla weapon of the same slot (`blender.preview --like model:<id>`).
- A replaced weapon inherits its `weapon.dat` line (damage, range, anims): keep the frame layout of the original
  and list the binding in the readme. Lint rule `weap.flash` checks the muzzle flash.
- Only one weapon model slot is free in a stock game (`limits.md`): new weapons replace.
