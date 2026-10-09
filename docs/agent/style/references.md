# References: describe the design, measure only true views

<!-- Model-facing, English only. How to use photos and real-car data when an asset depicts a real object. Written
     after builds where pixel grids, solved cameras and back-projected photo points produced the wrong car while
     a plain description produced the right one. -->

A reference tells you WHAT the object is: its proportions in words, its signature features, how its parts meet.
It does not give you coordinates. San Andreas simplifies and exaggerates (`construction.md`, rule 16): a model
that copies a photo millimetre by millimetre is not more like the car, it is less like San Andreas. Recognition
comes from the features being there and in the right relation to each other.

## 1. Overall size: the spec sheet

Global proportions come from the real object's data, never from photos:

- Cars: length, width (without mirrors), height, wheelbase, track, tyre size (for example 195/55 R15 = a tyre
  diameter of 0.596 m), front and rear overhang. Other objects: the main dimensions from a catalogue or a
  sensible estimate against the 1.84 m ped.
- Write them as a ratio card: wheelbase / length, overhangs / length, height / length, width / length, tyre
  diameter / height. The ratios are what you keep.
- One scale factor k for the whole object. Vehicles: the SA wheel is the class `wheel_scale` (0.70 m on most
  cars), so k = wheel_scale / real tyre diameter is a good start; vanilla cars come out about 1.1-1.2 times the
  real car and a little wider than k alone gives (`construction.md`, rule 16). Check k in the lineup with two
  vanilla peers of the same body type.
- The numbers set the box and the wheel positions. Everything inside the box comes from the description.

## 2. Features: one list per photo

Look at every photo once and write what makes the object recognisable into `<project>/refs/features.md` in the project
folder: 10-20 lines, each with the feature, where it is, its size relative to something else, and a priority
(identity-critical or secondary). Write it in words a modeller can build from. Example (a compact five-door
hatchback):

- Teardrop headlamps sweeping up and back into the wings, reaching the line of the A-pillar (identity).
- Slot grille with one chrome bar and a centred badge, set in a V formed by the bonnet edge (identity).
- Soft domed bonnet with two shallow ridges running back to the screen.
- Deep rounded bumper with a wide black intake bar and round fog-lamp pods at its ends.
- Strongly raked windscreen; the roof is an arch, highest over the B-pillar, falling to a small roof spoiler.
- Soft convex sides with one moulding that rises slightly towards the rear.
- Round arches, tight to the tyre, a little flared; 12-slot steel wheel covers.
- Glass about a quarter of the side height; black B-pillar; the rear door glass sweeps up at its rear corner;
  a small quarter window in a thick C-pillar (identity).
- Low, horizontal wedge-shaped tail lamps wrapping onto the rear quarters (identity).

Name what the photos do NOT show (the rear, the roof, the interior) and how you fill it: another photo, the
spec sheet, a common design of the era. Say so in the gate report; never invent precise numbers for it.

## 3. Which photos may be measured

Only a true elevation: a side, front, rear or top view taken from far away with a long lens (both wheels of a
side view are round to within a few percent, the ground line is level, the far side is invisible), or a
manufacturer blueprint. From such a view:

- Put it on a reference plane scaled by the object's length (`ref.plane` {`view`, `image`, `length` = the SA
  length}: the object is found against the photo's border colour, or give its `span` [x0, x1] in pixels; or
  `points` = the two hub centres in pixels and `distance` = their SA distance).
- Read about ten key points once, as ratios to the wheelbase or the height: belt height, roof peak and its
  station, windscreen base and top, C-pillar or hatch top, nose and tail heights, sill height, glass height.
  Write them on the ratio card with a tolerance of a few percent and do not iterate on them later.
- `blender.preview session:<name> --ref <photo> --ref-view side` shows the photo at low opacity behind the
  side view of your model (`--ref-flip` for a photo facing right, `--ref-box` when the object is not found);
  `look.silhouette` {`ref`, `view`, `flip`, `box`, `stations`} reports the outline overlap and the top line at
  stations along the length as information for the review. Both are for true views only.

A three-quarter, close-up, wide-angle or detail photo is never measured. Use it to describe character and to
judge the model by eye from a similar viewpoint.

## 4. Never

- Pixel grids on photos, reading pixel coordinates, labelled grid crops.
- Solving the photo's camera, back-projecting pixels onto planes, matching your render to a photo camera.
- Overlay chasing: moving vertices until an outline matches a photo "within a few pixels".
- Writing your own measuring code for any of the above.
- Rebuilding the body again and again from edited numbers; after the form gate, edit the mesh.

"Never guess pixels" in the skill is about picking objects in viewer frames (`view_capture` marks and grid
cells), not about reference photos.

## 5. The reference board

One labelled sheet (at most 1 MP) with every reference: side, front three-quarter, rear three-quarter, front,
rear, interior, details. `ref.import <photo> --project <asset> --view side|front|rear|top|3q|detail` prepares
each photo (at most 1,600 px; no grid unless you ask for one with `--grid`); `ref.board --project <asset>`
composes the sheet. The board, `<project>/refs/features.md` and the ratio card travel together: to a subagent,
to a critic, into every gate report. A builder that only gets text descriptions loses the parts the text forgets.

Missing views (often the rear or a true side view): ask the user for photos before G1, or for consent to
download a few; otherwise work from the description and mark the uncertain features.

## 6. How a critic compares

At every gate a reviewer (you, the user or a critic agent) reads the gate sheet next to the board and the
features list. The inventory report, the strict check and the close-up region sheets come first (`done.md`,
section 8); the likeness review follows, in this order:

1. **Identity:** does it read as the object from about 10 m in the game camera? Go through the features list:
   present, missing or wrong, identity items first.
2. **Proportions:** the ratio card; on a true side view the overlay as information. Local proportions (glass
   height, bonnet length, overhangs) matter more than total size.
3. **SA style:** next to two vanilla peers: soft, rounded, simplified, chunky; textures soft and small
   (`README.md`).
4. **Composition:** one joined whole; the `form` and `fit` rows of `asset.check` (`construction.md`).
5. **Detail:** the inventory items, the class checklist (the `coverage` rows) and the richness of interior,
   lamps, grille, bay (or the kind's own hero items, `done.md`).
6. **Technical:** engine rules (`limits.md`).

A finding names the part, the problem, the evidence (which view, which feature or row) and an instruction a
modeller can carry out ("raise the belt line behind the B-pillar so the rear door glass sweeps up"), at most
eight per round, most visible first. Never "N pixels off".

Back to the guides: `README.md`; construction rules: `construction.md`.
