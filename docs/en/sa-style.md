# San Andreas style: what makes a new asset look like it shipped with the game

[Русская версия](../ru/sa-style.md)

<!-- User page (people). The numbers come from measurements of the stock 1.0 US game; the full tables with peer
     sets and percentiles are the agent guides in docs/agent/style (English). -->

This page explains, in plain words and with measured numbers, why some mods look like part of GTA San Andreas and
others look foreign: too sharp, too clean, too modern or like a toy. Everything here was measured on the stock
PC 1.0 game with satk; an AI assistant that uses satk follows the same rules, and the full tables (with the set of
models each number comes from) are in [the style guides for agents](../agent/style/README.md).

## In short

- **Small, photo-like, dirty textures**: 64-256 pixels, compressed (DXT1), dusty desaturated colours, shading,
  folds and grime painted in. Never clean vector art, never one flat colour per object.
- **Detail lives in the texture, not in the geometry**: windows, brickwork, grilles, bolts and tyre tread are
  pictures on a few large polygons.
- **Few polygons where the outline turns, soft shading in between**: a stock car body is about 1,400
  triangles and looks curved because its normals are smooth.
- **Shared textures and colour keys**: cars use the game's common `vehicle.txd`; the paint is a key colour the game
  recolours and dirties.
- **Baked light on map models**: buildings and props carry dark grey daytime vertex colours and darker, warm night
  colours with lit windows; they have no normals at all.
- **The right size**: San Andreas cars are about 1.1-1.2 times longer than the real car they imitate; wheels stay
  real size.

## How the stock game textures its assets

- **Sizes and formats.** Of the 32,878 textures of the game, 89 % are DXT1 and 6.9 % DXT3 (for transparency);
  none is DXT5. The common sizes are 128x128 (33 %), 256x256 (24 %) and 64x64 (16 %); only 2.9 % are 512 or
  larger (terrain, big buildings). Vehicle, ped and weapon textures have exactly one mip level.
- **Buildings and terrain tile.** A facade texture is one window bay or one storey, repeated three to six times
  across the wall. A whole city block can be 300 triangles with one 256-pixel facade repeated over it. The
  textures are mid-grey (median brightness about 120 of 255) and desaturated; the baked vertex light darkens them.
- **Map models are pre-lit.** Every building and terrain piece carries two vertex-colour sets: a dark, nearly grey
  day light with soft occlusion (dark under eaves and at wall bases) and a darker, warm night light with pools of
  lamp light and lit windows. A white or blue night light glows unnaturally next to stock models.
- **Cars reuse shared textures.** The body is a flat paint colour (green 60,255,0 means "primary paint") on the
  shared texture `vehiclegrunge256`; the game swaps in one of 16 dirt levels, so the dirt is in the UV layout:
  sides map to the grimy band, up-facing panels to the clean part. Trim, chrome, black plastic, grille and dark
  glass are areas of `vehiclegeneric256`; lamps are areas of `vehiclelights128` with special colour codes that
  the game lights up at night; glass is 50 % transparent. A glossy sheen (`xvehicleenv128`) reads a second set of
  texture coordinates. A car's own textures are usually only a dark interior (128 px) and a wheel rim (64 px).
- **Peds** are one 128x256 photo atlas with clothing folds and shadows painted in and a photographic face.
  **Weapons** are one 64-pixel photo of the real weapon on both sides, plus an icon and a muzzle flash.
- **Painted details.** Shading, ambient occlusion, edge wear, stains and grime are part of the texture. Car
  interiors are very dark (brightness about 30 of 255) yet have hundreds of colours of fine grain.

## Shape, density and shading

- Triangles go where the silhouette turns: wheel arches, bumper corners, lamp surrounds, roof and pillar edges,
  cornices. Flat areas are a few long triangles. A car spends about 41 % of its body triangles in the front third
  and 36 % in the rear third.
- Map models have about 100-500 triangles whatever their size; a bigger building has larger triangles, not more.
- Shading is soft: on stock cars the vertex normals bend about 13 degrees from the faces on average. Normals are
  split only where the material changes (glass and body, lamp and body, trim) and at a few designed creases
  (the beltline, shut lines). Fully faceted bodies look like a 1998 game; fully smooth ones look modern.
- Every car has damaged versions of its panels with as many triangles as the intact ones, a simple low-detail
  version, collision spheres and a shadow mesh.

## Size and proportions

The wheel is the measuring stick of a vehicle: the wheel model is exactly as wide as the size written in
`vehicles.ide` (0.70 m on most cars), and the body follows the class proportions (a stock sedan is 8.0-9.2 wheel
diameters long, 5.6-6.3 m). Props, buildings and interiors are measured against the 1.84 m pedestrian.

## Two detail tiers

| Tier | What it means | When to use it |
|---|---|---|
| `sa_plus` (default for new assets) | the same look, with more polygons only on the silhouette and curves (rounder wheels and arches, a fuller interior) and textures one size step larger | new models that should look a little better than stock without leaving the style |
| `vanilla` | every number inside the range of stock models of the class | replacements that must not stand out in traffic or in a street |

The `sa_plus` numbers are a proposal until they are checked in game and with you; the stock numbers are measured.

## Mistakes that make an asset look foreign

- **Voxel or box look**: the model is assembled from loose slabs and floating details instead of one welded shell.
- **One flat colour per mesh**: no real texture coordinates, so every surface is a single texel.
- **Vector-art textures**: clean fills with a few dozen colours (stock textures have hundreds), sharp modern fonts.
- **Hard faceted shading**: every polygon visible; or hard edges inside a smooth panel.
- **Hand-typed geometry**: a model generated from typed coordinates instead of modelled and looked at in Blender.
- **Real-world scale**: a car built at 1:1 is shorter than every stock sedan.
- **A bright interior** behind the semi-transparent glass, or a body painted on a texture that never gets dirty.

## Checking an asset

| What | Command |
|---|---|
| the numbers of a class (ranges, examples) | `satk style profile --like model:426` |
| a new model against the stock one it replaces | `satk asset check <your .dff> --like model:426` |
| a picture next to two stock models of the class | `satk blender preview <your .dff> --lineup class` |
| how a texture compares with stock textures of its kind | `satk style texture <your .png>` |
| lint with the presets of the tiers | `satk asset lint <folder> --preset sa_plus` |

These commands come with the asset-creation tools; [authoring.md](authoring.md) describes the whole workflow.

## Quick example

```powershell
satk asset get model:426
satk texture image model:426 --mode sheet
satk model image model:426 --views 4
```

The first command shows the Premier's game data (wheel size, colours), the second one sheet with every texture
the car uses (its own interior and wheel, the shared paint, lamp and plate textures), the third a preview of the
model from four sides.
