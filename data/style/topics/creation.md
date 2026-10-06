# Creation quickstart: the shortest correct path for any asset

<!-- Model-facing, English only. Also the help topic `creation` (data/style/topics/creation.md, kept equal by a test).
     Numbers: `satk help style` + a class topic. -->

Model IN the live Blender session with methods, never with hand-typed vertices or a private mesh library. New
assets are tier `sa_plus` (`vanilla` only for a replacement that must blend into traffic). Show the user the lineup
sheet at G1 (shape) and G2 (shading). `satk <words>` = `satk_op(op, args)`; read `error.hint`.

## The loop (every kind)

1. `satk asset init <dir> --kind <kind> --intent add|replace --like <SID> --tier sa_plus`
2. `satk blender session start --name <a>`, then `satk kit template ... --session <a>` (frames, slots, materials).
3. `satk kit blank --kind <blank> --name <the template's name> --session <a>`: a clean low-poly quad base from class
   dimensions (`--like <SID>` supplies them, `--dims L,W,H` overrides), loops where parts are cut, UV seams, tier
   density. `--split --fill` cuts the parts and fills the kit slots.
4. Shape by steps, several per call with method `batch` (one checkpoint): `satk blender call <method> --session <a>
   --params '<json>' --snapshot 3q`. `mesh.transform` moves loops (select faces by `where`, `side`, `box`,
   `material`; widen with `"scale"` and `"pivot":"origin"`, never move vertices on x = 0 of a half shell), then
   `mesh.loopcut|inset|extrude|bevel`, `modifier.*`. `satk blender methods --query <name>`: one method's parameters
   (list: `studio-methods.md`).
5. Look once per cycle: `satk blender preview session:<a> --lineup class --passes game clay` (a project session
   takes the like model from `asset.json`). `TIMEOUT`: split the step; `BUSY`: wait.
6. Check: `satk asset check <package> --like <SID> --tier sa_plus --md`: 0 errors, every out-of-band row fixed or
   explained, then `asset lint --preset sa_plus` and `mod check`.

## Car (bike, boat, heli, plane: their blank, the same steps)

```
satk kit template --like model:426 --name mycar --tier sa_plus --ghost --session car1
satk kit blank --kind automobile --like model:426 --name mycar --session car1
satk blender call mesh.transform --session car1 --params '{"object":"mycar_body","select":{"where":["z>0.45"]},"translate":[0,0,0.03]}' --snapshot 3q
satk blender call kit.blank_split --session car1 --params '{"model":"mycar","fill":true}'
satk blender call kit.wheel --session car1 --params '{}'
satk blender call kit.shade --session car1 --params '{}'
satk blender call kit.vlo --session car1 --params '{}'
satk blender call kit.damage --session car1 --params '{}'
satk kit export --replace model:426 --session car1
```

`mycar_body` is the half shell with a MIRROR modifier; its part boundaries (chassis, bonnet, boot, doors, bumpers,
windscreen) are a face attribute that `kit.blank_split` cuts along. The blank, a few shaping steps, split, wheel
and shade already put `shade.*`, `geo.*`, `veh.hd_tris` and `dims.*` inside the class band.
`--body sedan|coupe|sports|suv|van` picks the proportions; `--tier vanilla` the lower density.

## Prop (map object)

```
satk kit template --kind prop --name mybin --lod --session p1
satk kit blank --kind prop_cyl --name mybin --dims 0.6,0.6,1.1 --split --fill --session p1
satk blender call kit.lod --session p1 --params '{}'
satk kit export --add --session p1 --place 2495,-1687,13
```

`--kind prop_box` makes boxes; `--like model:<id>` takes a vanilla prop's size. `kit export` writes prelight (map
models carry no normals), a primitive COL by the class rule (`--col`), the LOD DFF (`lod` + name from its
4th letter), the TXD and the IDE and IPL lines (check the free id is not in the weapon range 321-373). Own texture:
`texture new`, `kit.bake`, `texture finish` (`docs/en/kit.md`).

## Building with LOD

```
satk kit template --kind building --name mybld --session b1
satk kit blank --kind building_box --name mybld --dims 14,10,12 --split --fill --session b1
satk blender call kit.lod --session b1 --params '{}'
satk kit export --add --session b1 --place 2495,-1700,13
```

Windows and doors: `mesh.inset` and `mesh.extrude` on faces chosen by `side` and `where`; tiling UVs stay (metres
per repeat). The export writes HD, LOD, mesh COL and TXD; keep draw at most 299.

## Weapon

```
satk kit template --like model:346 --name mygun --session w1
satk kit blank --kind prop_box --like model:346 --name mygun --split --fill --session w1
satk kit export --replace model:346 --session w1
```

Shape the box or `prop_cyl` with `mesh.*` against the 1.84 m ped; one 64 px texture (`style texture --role weapon`).

## Ped re-skin

```
satk texture extract txd:male01
satk texture replace txd:male01 male01=<your edited png> --name myskin
satk mod check <work>/out/mods/myskin
```

Paint the extracted PNG (`texture finish <png> --preset photo_like` for a flat fill); `style texture <png> --role ped`.

## Look in context, then in the game (after any export)

```
satk view vehicle --dff <files.dff> --txd <files.txd> --pos 2495,-1675,13.4 --watch
satk view capture --pos 2503,-1666,17 --look 2495,-1674,13.4 --marks 6
satk ingame start --mod <package.out>
satk ingame check --suite vehicle
satk ingame reload
```

`files.*` and `package.out` come from the `kit export` answer (`view place`, `view ped` for props and peds). The
viewer is a fast look, only in the satk viewer build (else `UNSUPPORTED`); `--watch` shows a re-export at once.
Behaviour is the real game: `ingame start` runs the test server and writes the administrator setup the user runs
once; the user starts the client, never you. Read `report.json` and the `*-pair.png` frames in
`work/out/ingame/<model>/<suite>/`; fix, re-export, `ingame reload` (S29).

## Quick example

```powershell
satk kit blank
satk kit blank --kind automobile --like model:426 --name mycar --plan-only
satk kit blank --kind prop_cyl --dims 0.6,0.6,1.1 --name mybin --plan-only
```

Gates and costs: `docs/agent/workflows.md` S25.
