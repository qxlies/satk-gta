# SA style: references (describe the design, measure only true views)

A reference tells you WHAT the object is: proportions in words, signature features, how the parts meet. It does
not give you coordinates. SA simplifies and exaggerates; recognition comes from the features being there and in
the right relation.

## 1. Overall size: the spec sheet
- Cars: length, width (no mirrors), height, wheelbase, track, tyre size (195/55 R15 = 0.596 m), overhangs.
  Write a ratio card: wheelbase/L, overhangs/L, H/L, W/L, tyre/H.
- One scale k for the whole object: k = class `wheel_scale` (0.70 m on most cars) / real tyre diameter is a
  good start; vanilla cars end up about 1.1-1.2x real and a little wider. Check in the lineup with two peers of
  the same BODY TYPE.
- The numbers set the box and the wheels; everything inside comes from the description.

## 2. Features: one list per photo
`refs/features.md`: 10-20 lines, feature + where + size relative to something else + priority (identity or
secondary), in words a modeller can build from. Example: "teardrop headlamps sweeping up into the wings to the
A-pillar line (identity)", "roof arched, highest over the B-pillar, falling to a small spoiler", "glass about a
quarter of the side height; rear door glass sweeps up at its rear corner". Name what the photos do NOT show
(rear, roof, interior) and how you fill it; never invent precise numbers for it.

## 3. Which photos may be measured
Only true elevations: a far side/front/rear/top view with a long lens (wheels round, ground level, far side
invisible) or a manufacturer blueprint. Put it on `ref.plane` scaled by the SA length (or two hub centres); read
about ten key points ONCE as ratios (belt, roof peak and its station, screen base and top, C-pillar top, nose
and tail heights, sill, glass height); never iterate on them. `blender.preview --ref <photo> --ref-view side`
and `look.silhouette` are information for true views only.
Three-quarter, close-up, wide-angle and detail photos: describe and judge by eye, never measure.

## 4. Never
Pixel grids or reading pixel coordinates; solving the photo camera; back-projecting pixels; matching a render to
a photo camera; overlay chasing "within a few pixels"; writing measuring code; rebuilding the body again and
again from edited numbers. "Never guess pixels" in the skill is about viewer frames, not photos.

## 5. The reference board
`ref.import <photo> --view side|front|rear|top|3q|detail` (<= 1,600 px, no grid by default), `ref.board`: one
labelled sheet (<= 1 MP). Board + features list + ratio card travel together to subagents, critics and every
gate report. Missing views: ask the user for photos (or consent to download a few) before G1.

## 6. How a critic compares (every gate)
1. Identity: reads as the object from about 10 m in the game camera? Each features-list item present, missing
   or wrong, identity first.
2. Proportions: the ratio card; true-view overlay as information; local proportions (glass height, bonnet
   length, overhangs) first.
3. SA style next to two vanilla peers: soft, rounded, simplified, chunky; soft small textures.
4. Composition: one joined whole; `asset.check` `form` and `fit` rows.
5. Detail: `coverage` rows; interior, lamps, grille, bay.
6. Technical: engine rules.
At most 8 findings per round, most visible first; each names part, problem, evidence (view, feature or row) and
an instruction a modeller can carry out. Never "N pixels off".

Full guide: docs/agent/style/references.md
