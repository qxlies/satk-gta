# Creation quickstart: the shortest correct path for any asset

<!-- Model-facing, English only. Also the help topic `creation` (data/style/topics/creation.md, kept equal by a test).
     Topics: `style`, `style_construction`, a class topic, `style_kinds`, `done`. -->

SA style is a LOOK (soft, rounded, joined forms; small soft textures), not a polygon count: detail is free.
Build in the live Blender session from rounded sections, never vertex by vertex. `satk <words>` =
`satk_op(op, args)`; read `error.hint`. Show a sheet at every gate; wait for the user at G1 and G3.
You never declare DONE (`satk help done`); until then report what is open.

## Every asset: the gates and the definition of done (any kind; the kind blocks below add their steps)

```
satk asset init myasset --kind prop --like model:1337 --detail hero
satk asset inventory myasset --plan --stage G1
satk blender session start --project myasset
satk blender call scene.tag --session myasset --params '{"objects":["myasset_body"],"item":"I01"}'
satk asset inventory myasset --stage G1
satk blender preview session:myasset --regions all
satk asset status myasset --record '{"gate":"G2","state":"review"}'
satk kit export --add --session myasset
satk look leak <files.dff> --package <files.dir>
satk asset check <files.dir> --strict
```

- G0: the inventory = the kind's starter list + every feature, one item per part; drop one only with a
  `waived` design fact (never effort or distance). G1 form, G2 compose, G3 detail, G4 surface, G5 finish.
- Every stage: `scene.tag` new pieces in the same batch; `asset inventory --stage G<n>` shows nothing open;
  from G2 read every region sheet. A tagged face of another part is no part: build it as its own piece.
- DONE = `asset check <package> --strict` `done: true`: every item built, a full `look leak` of every DFF
  (the model AND its LOD), no blocking row. A gate is `done` only when its items are built.

## Car (any vehicle: its template, same steps; `style_kinds`)

```
satk asset init mycar --kind automobile --intent replace --like model:426 --detail hero
satk kit template --like model:426 --name mycar --ghost --session mycar
satk blender call batch --session mycar --params-file body.json --snapshot 3q
satk blender call mesh.transform --session mycar --params '{"object":"mycar_body","select":{"near":{"point":[0.9,1.9,0.15],"radius":0.3}},"translate":[0,0,0.02],"falloff":{"radius":0.6,"curve":"smooth"}}'
satk blender call kit.wheel --session mycar --params '{}'
satk blender preview session:mycar --lineup class --passes game clay
satk blender call batch --session mycar --params-file compose.json --snapshot cam_low
satk blender call kit.blank_split --session mycar --params '{"object":"mycar_body","fill":true}'
satk blender call kit.fill --session mycar --params '{"slot":"bump_front_ok","objects":["mycar_bump_f"]}'
satk blender call kit.info --session mycar --params '{"frames":true}'
satk blender call kit.uv_region --session mycar --params '{"object":"chassis","region":"grunge.paint","faces":"role:paint1","project":"keep"}'
satk blender call kit.shade --session mycar --params '{}'
satk blender call kit.damage --session mycar --params '{}'
satk blender call kit.vlo --session mycar --params '{}'
satk blender call kit.col --session mycar --params '{}'
satk kit export --replace model:426 --session mycar
```

`body.json`: one `mesh.loft` (`half`, 8-10 `shape` sections, `parts`); `compose.json`: `mesh.flare` per
arch, `mesh.sweep` bumpers, mirrors (`rounded_box` + `mesh.attach` snap). Tested batches:
`style/modelling.md` "A car in batches". Refit what the `fit` rows name; bikes: steering axis in the headset.

## Prop (map object)

```
satk kit template --kind prop --name mybin --lod --session myasset
satk kit blank --kind prop_cyl --name mybin --dims 0.6,0.6,1.1 --split --fill --session myasset
satk blender call kit.lod --session myasset --params '{}'
satk kit export --add --session myasset --place 2495,-1687,13
```

Closed pieces that touch (chamfered caps, rounded boxes), base below ground, back finished; own texture:
`texture new`, `kit.bake`, `texture finish --supersample 4`. Free ids: never 321-373 (weapons).

## Building with LOD

```
satk asset init mybld --kind building --like model:3639 --detail hero
satk kit template --kind building --name mybld --session mybld
satk kit blank --kind building_box --name mybld --dims 14,10,12 --split --fill --session mybld
satk blender call kit.lod --session mybld --params '{}'
satk kit export --add --session mybld --place 2495,-1700,13
```

One shell, corners closed, roof slab on the walls, ledges, entrance; `kit.lod` keeps the outline (check its
coverage); leak-check `lod<name>.dff` too. Interiors: `style_kinds` (cameras in every room).

## Weapon

```
satk asset init mygun --kind weapon --like model:346 --detail hero
satk kit template --like model:346 --name mygun --session mygun
satk kit blank --kind prop_box --like model:346 --name mygun --split --fill --session mygun
satk kit export --replace model:346 --session mygun
```

Gun: side profile, rounded grip, flash at the muzzle. Knife, bat, katana, grenade: kind `weapon_melee`
(along +Z: blade, guard, grip, pommel). One small photo-like texture.

## Ped re-skin (kit export refuses peds: it cannot write a skin)

```
satk texture extract txd:male01
satk texture replace txd:male01 male01=<your edited png> --name myskin
satk mod check <work>/out/mods/myskin
```

Paint soft and photo-like (`texture finish <png> --preset photo_like`), no seams at the UV borders.

## Look in context, then in the game (after any export)

```
satk view vehicle --dff <files.dff> --txd <files.txd> --pos 2495,-1675,13.4 --watch
satk ingame start --mod <package.out>
satk ingame check --suite vehicle
```

The user starts the game client, never you (S29). Gates: `docs/agent/workflows.md` S25.
