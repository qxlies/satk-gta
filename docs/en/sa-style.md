# San Andreas style: what makes a new asset look like it shipped with the game

[Русская версия](../ru/sa-style.md)

<!-- User page (people). The look described in plain words; the numbers come from measurements of the stock 1.0 US
     game and are reference only. The full guides for agents are in docs/agent/style (English). -->

This page explains, in plain words, why some mods look like part of GTA San Andreas and others look foreign: too
boxy, too sharp, too clean, too dirty, too modern or like a toy. The San Andreas style is a LOOK, not a polygon
count: a model may have much more detail than the stock ones as long as it is built in the same visual language.
Measurements of the stock PC 1.0 game made with satk show what that language is; they are reference, not
targets. An AI assistant that uses satk follows the same rules; the detailed guides are in
[the style guides for agents](../agent/style/README.md).

## In short

- **Soft, rounded, simplified forms**: every surface a little curved, corners turned in two or three smooth
  steps, windows that lean in, bodies that bulge slightly like a barrel. Never a pile of hard boxes.
- **One coherent whole**: the body is one joined shell, the doors, bonnet and bumpers are cut out of it, wheel
  arches are closed inside, small parts sit on the surface they belong to. Nothing floats.
- **Crisp lines only where something changes**: where glass meets paint, where a panel is cut, on a few designed
  creases. Everywhere else the shading is soft.
- **Small, soft, photo-like textures**: about 128 pixels, slightly blurry, desaturated, with light and folds
  painted in. Car paint is a clean colour; the game adds its own dirt.
- **Chunky proportions**: San Andreas cars are about 1.1-1.2 times larger and relatively wider than the real car
  they imitate; the characteristic lines of the original are kept, its exact millimetres are not.
- **Detail is free**: a richer interior, an engine bay or deeper lamps are welcome, built in the same soft way.

## Shape: soft, rounded, one whole

- **Nothing is flat.** Stock roofs are gentle arcs (a few centimetres of crown across the car), bonnets are
  domed and often carry two soft creases, door skins bulge between the sill and the window line.
- **Corners are rounded.** A roof edge, a fender top or a bumper end turns in two or three small steps that are
  shaded smoothly, so it reads round. A model with single hard 90-degree corners and flat chamfers reads like
  voxel art.
- **Parts are joined.** On stock cars almost the whole body is one welded surface; the opening panels are cut out
  of it, so their edges sit exactly on the body. SUV wheel-arch flares grow out of the fender instead of being
  separate horseshoes. Bumpers wrap from one wheel arch to the other. Pillars and window frames are dark parts
  of the body and the doors, not bars stuck on.
- **Details sit in place.** Headlamps sit in recesses a few centimetres deep, mirrors stand on short stalks that
  touch the door, handles and roof rails touch the body. Wheel arches have an inner liner: you never see through
  the car.
- **The inside is closed.** A cabin with seats, a dashboard and a steering wheel, an engine block under the
  bonnet, a floor underneath.
- **Props and buildings** are made of closed pieces that touch or push into each other (a hydrant is about 18
  pieces with rounded caps); a house is one main shell with a roof slab and small volumes on it, windows and
  bricks are in tiling textures.

## Shading

Shading is soft: the normals bend smoothly over rounded corners. Normals are split only where the material
changes (glass and body, lamp and body, trim) and on a few designed creases (the beltline, shut lines, bonnet
creases). Fully faceted bodies look like a 1998 game; smoothing by one angle (everything sharper than a
threshold becomes a hard edge) turns a rounded body into a bevelled box; fully smooth bodies with no splits look
melted. Map models (buildings, props) have no normals at all: a dark baked vertex light with soft occlusion
gives them their look.

## How the stock game textures its assets

- **Sizes and formats.** Of the 32,878 textures of the game, 89 % are DXT1 and 6.9 % DXT3 (for transparency);
  none is DXT5. The common sizes are 128x128 (33 %), 256x256 (24 %) and 64x64 (16 %); only 2.9 % are 512 or
  larger (terrain, big buildings). Vehicle, ped and weapon textures have exactly one mip level.
- **Soft, not crisp.** A car door is only about 50 texture pixels wide: textures are photo-like but blurry by
  design, with low contrast and dusty, desaturated colours. Painting a texture at four times its size and
  shrinking it with a soft filter gives this look; a sharp pixel grain or hard painted lines do not.
- **Buildings and terrain tile.** A facade texture is one window bay or one storey, repeated three to six times
  across the wall. The textures are mid-grey and desaturated; the baked vertex light darkens them.
- **Map models are pre-lit.** Every building and terrain piece carries a dark, nearly grey day light with soft
  occlusion and a darker, warm night light with pools of lamp light and lit windows.
- **Cars reuse shared textures.** The body is a clean paint colour (green 60,255,0 means "primary paint") on
  the shared texture `vehiclegrunge256`; the game picks one of 16 dirt levels and, because of the UV layout, the
  dirt only reaches the lower sides even on the dirtiest cars. Trim, chrome, black plastic, grille and dark glass
  are areas of `vehiclegeneric256`; lamps are areas of `vehiclelights128` with special colour codes the game
  lights up at night; glass is 50 % transparent. A car's own textures are usually only a dark interior (128 px)
  and a wheel rim (64 px). Painting dirt or grime into a car's textures makes it look filthy next to stock cars.
- **Peds** are one 128x256 photo atlas with clothing folds and shadows painted in and a photographic face.
  **Weapons** are one 64-pixel photo of the real weapon on both sides, plus an icon and a muzzle flash.

## Size and proportions

The wheel is the measuring stick of a vehicle: the wheel model is exactly as wide as the size written in
`vehicles.ide` (0.70 m on most cars). The body follows the real vehicle's proportions (from its spec sheet)
scaled to about 1.1-1.2 times, a little wider than that. Keep what makes the car recognisable (roof line, window
shape, overhangs, lamp and grille arrangement) rather than its exact measurements. Reference photos are
described (which features make the car what it is); only true side, front or rear views may be measured, and
never by reading pixels off a perspective photo. Props, buildings and interiors are measured against the 1.84 m
pedestrian.

## Detail and the two tiers

| Tier | What it means | When to use it |
|---|---|---|
| `sa_plus` (default for new assets) | the same look at a free detail level: rounder forms, a real interior, an engine bay, deeper lamps, textures one size step larger | new models that should look better than stock without leaving the style |
| `vanilla` | about the detail of the stock models of the class | replacements that must not stand out in traffic or in a street |

Neither tier has polygon limits. What stays fixed are the rules the game engine needs: frame names, colour
codes, texture formats, the wheel size, collision, name lengths.

## Mistakes that make an asset look foreign

- **Voxel or box look**: bodies and parts made of hard boxes with one chamfer, flat sides, a flat roof.
- **Parts not joined**: flares, bumpers, roof rails, pillars or window frames standing next to the body with gaps,
  floating details, see-through wheel arches.
- **Parts in the wrong place**: lights, exhaust or hinges left where a template put them; a motorbike whose
  steering pivot sits outside the body or whose seat and handlebars do not match the rider's pose.
- **Too few details**: a model stopped early because a polygon count looked "full".
- **Unfinished**: parts missing, gaps you can see through, an empty underside, interior or back side because
  nobody looked there; moving parts (rotors, propellers, pedals, flaps) that swing off the body.
- **Hard faceted shading**, or one smoothing angle for everything.
- **Dirt everywhere**: grime painted over a car, or a raw preview judged instead of the game look.
- **Vector-art or crisp textures**: clean fills with a few dozen colours, sharp modern fonts, sharp grain.
- **Pixel-measured photos**: proportions read from a perspective photo end up wrong in a way nobody recognises.
- **Real-world scale**: a car built at 1:1 is shorter than every stock sedan.
- **A bright interior** behind the semi-transparent glass, or a body painted on a texture that never gets dirty.

## Checking an asset

| What | Command |
|---|---|
| what the replaced stock model is made of | `satk asset anatomy model:426 --md` |
| a picture next to two stock models of the class | `satk blender preview <your .dff> --lineup class` |
| a new model against the stock one it replaces: game rules, joined parts, fit of lights and pivots | `satk asset check <your .dff> --like model:426` |
| how a texture compares with stock textures of its kind | `satk style texture <your .png>` |
| the stock numbers of a class (for reference) | `satk style profile --like model:426` |
| lint with the presets of the tiers | `satk asset lint <folder> --preset sa_plus` |
| the finish line: every part built, no gaps, no defects | `satk asset check <your .dff> --like model:426 --strict` |
| close-ups of every area of the model | `satk blender preview <your session> --regions all` |

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
