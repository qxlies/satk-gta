# `satk fx2d`: записи 2dEffect (2DFX) моделей в виде JSON

[English version](../en/fx2d.md)

Пакет: `satk.fx2d`.

## Что это

В DFF модели у каждой геометрии может быть блок 2dEffect: короны уличных фонарей и тени от их света, источники
частиц (дым, фонтаны, насекомые), аттракторы пешеходов (скамейки, банкоматы, витрины), маркеры входа и выхода,
текст дорожных знаков, точки игровых автоматов, точки укрытия и эскалаторы. Раньше для их правки нужен был
плагин 3D-редактора. `satk fx2d` превращает все записи DFF в один читаемый JSON-документ, записывает изменённый
документ обратно в копию DFF, копирует записи между моделями (поставить фонарь от `lamppost1` на свою лампу) и
проверяет их по ресурсам самой игры (`effects.fxp`, `particle.txd`).

Круговая запись точная: `dump`, затем `apply` неизменённого JSON дают тот же самый DFF для всех 1 851 моделей
оригинальной игры, у которых есть 2dEffect (18 795 записей). Файлы игры только читаются; всё пишется в
`<workspace>\work\out\fx2d\`.

## Быстрый пример

```powershell
satk fx2d dump model:lamppost1
satk fx2d check model:lamppost1
satk fx2d apply model:lamppost1 lamppost1.json --out docs-demo-lamp
satk fx2d copy model:lamppost1 models/gta3.img/vgseesc01.dff --filter light --offset 0,0,1 --out docs-demo-escalator
satk fx2d dump docs-demo-escalator.dff
satk fx2d roundtrip models/gta_int.img
```

Что возвращается (сокращённо: первая, третья, четвёртая и последняя команды):

```json
{"ok":true,"cols":["fx","type","pos","info"],
 "rows":[["g0#0","light",[-0.44,0.05,3.2],"coronastar rgba 249,145,34,200 size 2.5 far 100 range 12 shadow 8 at_night"]],
 "file":"<workspace>/work/out/fx2d/lamppost1.json","source":"model:lamppost1 (models/gta3.img/lamppost1.dff)",
 "geometries":1,"geometry_count":1,"entries":1,"counts":{"light":1}}
{"ok":true,"file":"<workspace>/work/out/fx2d/docs-demo-lamp.dff","mode":"replace","geometries":[0],"entries":1,
 "identical":true,"bytes":6666,"check":{"errors":0,"warnings":0}}
{"ok":true,"file":"<workspace>/work/out/fx2d/docs-demo-escalator.dff","from":"model:lamppost1 (models/gta3.img/lamppost1.dff)",
 "mode":"add","copied":1,"types":{"light":1},"geometries":[0],"entries":2,"identical":false}
{"ok":true,"target":"models/gta_int.img","files":1901,"with_2dfx":170,"exact":170,"differ":0,
 "entries":{"attractor":123,"cover_point":99,"light":1165,"particle":10,"trigger_point":3},"with_keep":1175}
```

Файл `lamppost1.json` (по записи на строку):

```json
{"format": "satk.fx2d/1", "source": "model:lamppost1 (models/gta3.img/lamppost1.dff)", "geometry_count": 1,
 "geometries": [
  {"geometry": 0, "frame": "lamppost1_L0", "effects": [
   {"type": "light", "pos": [-0.43525124, 0.048412323, 3.202], "color": [249, 145, 34, 200], "corona": "coronastar",
    "shadow": "shad_exp", "corona_size": 2.5, "far_clip": 100.0, "range": 12.000001, "shadow_size": 8.0,
    "shadow_mult": 40, "shadow_z": 0, "flash": "default", "reflection": true, "flare": 0,
    "flags": ["fog1", "at_night", "update_height_above_ground"], "look_dir": [0, 0, 100],
    "keep": {"corona": "0001000100400000003100000094", "shadow": "00000021000000436f6c205370686572"}}
  ]}
 ]}
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk fx2d dump SRC [--out NAME] [--filter TYPE…] [--no-keep] [--full]` | — (`satk_op`) | все записи DFF → `work\out\fx2d\<имя>.json`; таблица `fx/type/pos/info` (`g0#3` = геометрия 0, запись 3), `counts` по типам; `--full` ещё и возвращает записи как JSON |
| `satk fx2d apply DFF JSON [--mode replace\|add] [--geometry N] [--out NAME] [--no-check]` | — | записывает записи документа (файл или текст JSON) в копию DFF; `identical` говорит, совпал ли результат со входом; записанные геометрии проверяются |
| `satk fx2d copy SRC DST [--filter TYPE…] [--from-geometry N] [--to-geometry N] [--mode add\|replace] [--offset X,Y,Z] [--out NAME]` | — | копирует записи из одной модели в копию другой, при желании со сдвигом |
| `satk fx2d check SRC [--fxp FILE] [--txd FILE] [--strict]` | — | DFF или JSON-документ: таблица `where/sev/code/msg`, `errors`, `warnings`; `--strict` завершается с `CHECK_FAILED` при ошибке |
| `satk fx2d roundtrip [TARGET] [--no-keep]` | — | DFF → текст JSON → DFF для всей игры, IMG, папки или файла: `exact`, `differ`, записи по типам |

`SRC`/`DFF` — это `model:<id|имя>` или `dff:<имя>` (через индекс профиля `--profile`, по умолчанию `vanilla`),
`models/gta3.img/<имя>.dff` или путь: абсолютный, от корня игры, от текущей папки или от `work\out\fx2d\` (так что
цепочка `dump` → правка → `apply` → `dump` работает по имени файла). `--out` — имя внутри `work\out\fx2d\` или
абсолютный путь; по умолчанию имя входного файла. Агенты вызывают каждую команду через
`satk_op("fx2d.dump", {...})`.

## JSON-документ

Верхний уровень: `format` (`satk.fx2d/1`), `source`, `geometry_count` и `geometries`: по объекту на каждую
геометрию с блоком 2dEffect, `{"geometry": N, "frame": "<имя фрейма>", "effects": [...]}`. `apply` принимает и
`{"effects": [...], "geometry": N}`, и просто список записей (геометрия 0 или `--geometry`). В режиме
`--mode replace` (по умолчанию) перечисленные геометрии получают ровно эти записи (пустой список удаляет блок);
`--mode add` добавляет в конец. Неперечисленные геометрии не трогаются; новый блок встаёт в конец Extension
геометрии, как в файлах Rockstar.

У каждой записи есть `type` и `pos` (`[x, y, z]` относительно объекта; у дорожных знаков — мировые координаты).
Ключи по типам:

| `type` (номер) | Ключи | Значения по умолчанию для записи, написанной вручную |
|---|---|---|
| `light` (0) | `color` `[r,g,b,a]`, `corona`, `shadow` (имена текстур в `particle.txd`), `corona_size`, `far_clip`, `range` (точечный свет), `shadow_size`, `shadow_mult`, `shadow_z`, `flash`, `reflection`, `flare` (0 нет, 1 солнце, 2 фары), `flags`, `look_dir` `[x,y,z]` (×100) | уличный фонарь `lamppost1` |
| `particle` (1) | `name` (система эффекта из `effects.fxp`) | — |
| `attractor` (3) | `atype` (`atm seat stop pizza shelter trigger_script look_at scripted park step`), `queue_dir`, `use_dir`, `fwd_dir`, `script`, `probability`, `unk1`, `flags` | направления `[0,1,0]`, `script` `none`, `probability` 75 |
| `sun_glare` (4) | — (данных нет; его использует секция `2dfx` в IDE, загрузчик DFF игры — нет) | — |
| `enex` (6) | `name` (интерьер, не больше 7 символов), `enter_angle`, `radius` `[x,y]`, `exit` `[x,y,z]` (относительно `pos`), `exit_angle`, `interior`, `flags`, `sky`, `time_on`, `time_off` | `radius` `[2,2]`, `time_off` 24 |
| `roadsign` (7) | `text` (4 строки, `_` = пробел, хвостовые `_` опущены), `size` `[w,h]`, `rot` `[x,y,z]` (градусы), `lines` (1-4), `chars` (2, 4, 8, 16 в строке), `color` (0-3) | `rot` 0, 4 строки по 16, цвет 0 |
| `trigger_point` (8) | `id` (барабан игрового автомата) | — |
| `cover_point` (9) | `dir` `[x,y]`, `usage` (`low_cover wall_to_left wall_to_right`) | `usage` `low_cover` |
| `escalator` (10) | `bottom`, `top`, `end` (`[x,y,z]`), `up` | `up` true |

Флаги света `flags`: `check_obstacles fog1 fog2 without_corona only_long_distance at_day at_night blinking1
only_from_below blinking2 update_height_above_ground check_direction blinking3`. `flash`: `default random
random_when_wet anim_speed_4x anim_speed_2x anim_speed_1x unknown6 traffic_light train_crossing unused9
only_rain on5_off5 on6_off4 on4_off6`. Флаги входа-выхода `flags`: `unknown_interior unknown_pairing
create_linked_pair reward_interior used_reward_entrance cars_and_aircraft bikes_and_motorcycles disable_on_foot
accept_npc_group food_date_flag unknown_burglary disable_exit burglary_access entered_without_exit enable_access
delete_enex`. Перечисления принимают и числа, списки флагов — и целое число.

Точность: числа с плавающей точкой пишутся с минимумом цифр, дающим то же 32-битное значение (`12.000001`
действительно так хранится); NaN и бесконечность — сырыми битами (`"0x7FC00000"`). В `keep` лежат байты, которые
игра не читает (остатки после конца имени, выравнивание): они есть у 2 642 записей оригинальной игры. Удалять
`keep` (или делать `dump --no-keep`) всегда безопасно: тогда в этих местах будут нули. `bytes` появляется только
у света на 76 байт вместо 80 и у входа-выхода на 40 вместо 44. Типы 2 и 5, неизвестные номера и нестандартные
размеры хранятся сырым hex в `data`. Неизвестный ключ, флаг или значение — ошибка с JSON-путём и подсказкой
ближайшего имени (`colour` → `color`).

## Проверки

`check`, а также `apply`/`copy` для записанных геометрий сообщают:

| Код | Уровень | Значение |
|---|---|---|
| `TYPE_NOT_READ`, `BAD_SIZE` | error | загрузчик DFF игры не читает такой тип или размер |
| `NAME_TOO_LONG`, `NOT_FINITE`, `INVALID` | error | имя без места под завершающий ноль, NaN/бесконечность, запись JSON, которую нельзя закодировать |
| `TEX_MISSING`, `NO_TEXTURE` | warn | текстуры короны или тени нет в `particle.txd` (не рисуется) |
| `PARTICLE_MISSING`, `EMPTY_NAME` | warn | частица не является системой эффекта из `effects.fxp` (эффекта не будет) или имя пустое |
| `NEVER_SHOWN` | warn | свет без `at_day` и без `at_night` |
| `RANGE`, `BAD_ENUM`, `DIR_NOT_UNIT`, `LOOK_DIR_ZERO`, `SCRIPT_NONE` | warn | значения далеко за пределами оригинальных, неизвестные значения перечислений, ненормированные направления |
| `TEXT_TOO_LONG`, `TEXT_IGNORED` | warn | текст знака не помещается в `chars` символов строки или в `lines` строк |
| `FAR_FROM_MODEL`, `DUPLICATE` | warn | запись далеко за ограничивающей сферой модели; одна и та же запись дважды |

`--fxp`/`--txd` проверяют по собственным файлам мода; без них берутся `models\effects.fxp` и
`models\particle.txd` профиля (`RESOURCE_MISSING`, если их нет). Базовая линия оригинала: ни одной ошибки и 87
предупреждений на 1 851 модель (68 огней далеко от своей модели, 16 переполнений текста знаков, 3 прочих).

## Как это устроено

DFF разбирается в дерево чанков без потерь из `satk.rw`: меняются только чанки 2dEffect записанных геометрий и
размеры их родительских чанков, остальные байты копируются. Байты после последнего чанка RW (выравнивание
сектора IMG) в результат не пишутся. Вывод детерминирован. `roundtrip` по всей оригинальной игре (15 356 DFF)
занимает около 3 секунд.

Чтобы найти модели по их эффектам, спросите индекс: `satk index query "SELECT d.name, f.type_name FROM fx2d f
JOIN dff d ON d.id = f.dff_id WHERE f.origin = 'dff' AND f.type_name = 'enex'"`.

## Ограничения и известные проблемы

- Правится только блок 2dEffect в DFF; строки `2dfx` в IDE (блики солнца) — это текст, они остаются как есть.
- Позиции дорожных знаков — мировые координаты (игра не двигает их вместе с объектом), поэтому знак,
  скопированный на другую модель, остаётся там же в мире; используйте `--offset`.
- У типов 2 (не используется) и 5 (интерьер) нет разобранных полей: они хранятся сырыми в `data`.
- `apply` пишет один DFF за запуск; собрать результаты в IMG можно через `satk img build` ([rw.md](rw.md)).

## Python-API

```python
from satk.fx2d.doc import apply_doc, normalize, read_doc

doc = read_doc(dff_bytes)
doc["geometries"][0]["effects"][0]["color"] = [255, 0, 0, 200]
new_bytes, report = apply_doc(dff_bytes, normalize(doc))
```
