# `satk mod add` и `satk data`: новые модели для Mod Loader, данные handling и оружия

[English version](../en/addon.md)

Пакет: `satk.addon`. Сам Mod Loader (что меняет мод, конфликты, итоговые строки) описан на
[странице modinspect](modinspect.md), свободные id — на [странице idmgr](idmgr.md).

## Что это

Добавить в San Andreas машину, скин, оружие или объект — в основном учёт: свободный id модели, строка
vehicles.ide, handling, цвета, тюнинг, имя в GXT, имена, которые ни с чем не пересекаются, и всё это в том виде,
в каком его читает Mod Loader. `satk mod add` делает этот учёт по модели-донору: копирует строки данных донора
токен в токен (меняются только id и имена), проверяет id и имена по профилю и пишет готовую папку Mod Loader в
`<work>/out/addon/<name>/`. `satk data` показывает любую запись `handling.cfg`, `weapon.dat`, `carcols.dat` или
`peds.ide`/`pedstats.dat` с единицами и пояснениями и меняет поля handling и оружия: папкой Mod Loader, фрагментом
Lua для MTA или полной копией файла. Папка игры только читается.

## Быстрый пример

```powershell
satk data get handling infernus --field fMass fTractionMultiplier fMaxVelocity
satk data explain handling fTractionBias
satk data patch handling infernus fMass=1500 fTractionMultiplier=0.8 --profile vanilla
satk data get weapon PISTOL --field damage
satk mod add vehicle --dff dff:infernus --txd txd:infernus --like 411 --name infernus2 --game-name "Infernus II" --profile vanilla --force
```

Что возвращается (сокращённо):

```json
{"ok":true,"cols":["field","value","unit","meaning"],"rows":[["fMass",1400.0,"kg","Vehicle mass. …"],["fTractionMultiplier",0.7,"x","Overall tyre grip. …"],["fMaxVelocity",240.0,"km/h","Top speed …"]],"id":"handling:INFERNUS","file":"data/handling.cfg","lines":[101],"models":["model:411 infernus"]}
{"ok":true,"id":"handling:fTractionBias","kind":"car","key":"traction_bias","name":"fTractionBias","col":"L","type":"float","unit":"front share","range":[0,1],"meaning":"Split of grip between the axles: 1.0 all at the front, 0.0 all at the rear, 0.5 even. …","mta":"tractionBias"}
{"ok":true,"cols":["field","old","new"],"rows":[["fMass","1400.0","1500.0"],["fTractionMultiplier","0.70","0.80"]],"id":"handling:INFERNUS","target":"modloader","out":"<work>/out/addon/infernus-handling-modloader","files":["infernus-handling-modloader.txt"],"new":["INFERNUS     1500.0    2725.3 …"]}
{"ok":true,"cols":["field","poor","std","pro","cop","unit","meaning"],"rows":[["damage",25,25,25,25,"health","Damage per hit."]],"id":"weapon:PISTOL","weapon_id":22}
{"ok":true,"cols":["file","line"],"rows":[["vehicles.ide","612, \tinfernus2, \tinfernus2, \tcar, \t\tINFERNUS2, \tINFERN1, …"],["handling.cfg","INFERNUS2     1400.0 …"],["carcols.dat","infernus2, 12,1, 64,1, …"],["carmods.dat","infernus2, nto_b_s, nto_b_l, nto_b_tw"],["infernus2.fxt","INFERN1 Infernus II"]],"warn":["ADDON_VEHICLE: vehicle id 612 is outside 400-611: …","NEW_HANDLING_ID: …","GXT_KEY: GXT key INFERNU is used by the game; the vehicle uses INFERN1"],"id":"model:612","donor":"model:411 infernus","out":"<work>/out/addon/infernus2","files":["infernus2.dff","infernus2.fxt","infernus2.txd","infernus2.txt"]}
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk mod add vehicle\|ped\|weapon\|object --dff F --txd F [--col F] --like DONOR --name NAME [--id auto\|N] [--lod-dff F [--lod-txd F] [--lod-name N]] [--place X,Y,Z]` | — (через `satk_op`) | папка Mod Loader, добавляющая новую модель со строками данных донора; таблица `file/line` |
| `satk data get handling\|carcols\|peds\|weapon KEY [--field F ...]` | — | одна запись: все поля, значения и единицы (с `--field` — ещё и пояснения) |
| `satk data patch handling\|weapon KEY field=value ... [--target modloader\|mta\|full]` | — | новые значения одной записи; таблица `field/old/new` и папка результата |
| `satk data explain handling\|weapon\|peds [FIELD]` | — | поле (или бит флага): единицы, диапазон, тип, буква столбца, имена редакторов и MTA; без поля — все поля |

Параметры `mod add`:

- `--dff`, `--txd`, `--col` — файлы; `dff:<name>` и `txd:<name>` берут модель самой игры из IMG-архивов профиля
  (быстрый способ сделать вариант). Объекты могут обойтись без `--txd` и оставить TXD донора; `--col` с одной
  моделью переименовывается в новое имя; из COL со многими моделями берётся модель с новым именем или именем
  донора;
- `--like` — донор: `model:411`, `411` или `infernus`; он должен быть того же вида, что и добавляемая модель;
- `--name` — имя модели, TXD и файлов (буквы, цифры, `_`, не больше 19 символов, хранится строчными);
- `--id auto` (по умолчанию) берёт первый свободный id нужного вида так же, как `satk id free` (все слои индекса
  профиля, включая моды и readme в modloader, id движка и SA-MP); `--id N` должен быть свободен;
- `--profile` — игра, из которой берутся донор, id и имена (по умолчанию `installed`, ваша игра);
- машины: `--game-name` (имя в игре; пишется в FXT-файл под свободным ключом GXT не длиннее 7 символов),
  `--handling own|donor` (копия строк handling донора под id `NAME` или общий с донором), `--cargrp
  donor|<номер>|<метка>` (добавить машину в группы cargrp.dat);
- оружие: `--weapon-type` — тип донора (по умолчанию: его строки weapon.dat получают новую модель), `none` или
  имя нового типа;
- объекты с LOD: `--lod-dff` копирует модель LOD (имя задаёт `--lod-name`, по умолчанию имя файла, например
  `lodmybin`) и даёт ей отдельную строку `objs` в том же файле IDE: следующий свободный id, TXD объекта (или
  `--lod-txd`, который копируется и называется как LOD), дальность прорисовки 800 (медиана LOD игры), флаги 0; имя
  LOD проверяется как любое имя модели. `--place X,Y,Z` (объекты) пишет ещё и `data/maps/<name>.ipl` со строкой
  `inst` объекта (позиция в мире, единичный поворот) и, при LOD, строкой `inst` LOD сразу за ней; строка HD
  заканчивается индексом строки LOD (`1`), строка LOD — `-1`; в readme попадает строка `IPL` для gta.dat. Без
  `--lod-dff` у расстановки нет связи с LOD (`-1`);
- `--out NAME` (папка в `<work>/out/addon/`), `--force` (заменить её), `--dry-run` (проверить и показать, ничего
  не писать).

Ключи `data`: handling принимает машину (`model:411`, `411`, `infernus`) или id handling (`INFERNUS`,
`handling:INFERNUS`); weapon — тип (`PISTOL`, `weapon:22`, `22`), модель оружия (`colt45`, `model:346`) или
`aim:<группа анимаций>`; carcols и peds — модель. Имена полей — как в редакторах handling (`fMass`,
`vecCentreOfMass.z`), ключи satk (`mass`), имена MTA (`tractionMultiplier`) или `bike.`/`boat.`/`flying.` + имя
для дополнительных строк мотоциклов, лодок и самолётов. `data patch` пишет в
`<work>/out/addon/<key>-<file>-<target>/` (`--name` меняет имя): `modloader` — readme только с изменёнными
строками, `mta` — `handling.lua`/`weapon.lua` с вызовами `setModelHandling`/`setWeaponProperty`, `full` —
`data/handling.cfg` или `data/weapon.dat`, где изменены только эти строки. Патч оружия меняет строки всех уровней
навыка, если не указан `--skill poor|std|pro|cop`.

## Как это устроено

- **Что куда.** Mod Loader читает строки данных прямо из `.txt` в моде (функция readme) для vehicles.ide и
  peds.ide (`cars`/`peds`), handling.cfg, carcols.dat, carmods.dat, gta.dat и weapon.dat (строки огнестрельного и
  холодного оружия). `mod add` пишет эти строки в `<name>.txt` и сверяет каждую с шаблонами readme Mod Loader
  0.3.7. Оружие и объекты получают свой `data/maps/<name>.ide` и строку gta.dat в readme (Mod Loader загружает
  IDE, только если его называет gta.dat). Файлы без чтения из readme (cargrp.dat, object.dat) пишутся как файл
  игры плюс изменение: из неполного файла данных Mod Loader выкинул бы отсутствующие записи.
- **Строки байт в байт.** Строка делится на токены и разделители между ними; переписываются только изменённые
  токены, в стиле прежнего значения (`1400.0` -> `1500.0`, `0.70` -> `0.80`, регистр и ширина hex сохраняются).
  Каждая строка ванильных `handling.cfg` и `weapon.dat` записывается обратно байт в байт (игровые тесты).
- **Проверки.** Донор существует и нужного вида; id свободен; имя модели и имена файлов DFF, TXD и COL не заняты
  архивами игры, её IDE и (при собранном индексе) модами modloader; ключ GXT машины свободен в vehicles.ide и
  `text/american.gxt` (к занятому добавляется цифра); новый id handling не должен существовать и укладывается в 13
  символов. DFF и TXD должны разбираться; машина без встроенной коллизии, пед без скина и текстуры, которых нет в
  TXD, дают предупреждения.
- **Чтение игры.** Записи берутся из собственных файлов данных профиля (того, что загружает `gta.dat`), а не из
  результата других модов Mod Loader (его показывает `satk mod effective`).

## Ограничения и известные проблемы

- У стандартного движка жёсткие лимиты: id машин вне 400-611, новые id handling и новые типы оружия требуют
  лимит-аджастер (fastman92 LA); об этом говорят предупреждения `ADDON_VEHICLE`, `NEW_HANDLING_ID` и
  `NEW_WEAPON_TYPE`. Аудионастройки машин для новых id не пишутся.
- `satk mod inspect` показывает части папки с vehicles.ide, handling, gta.dat и файлами IDE; он не читает строки
  readme для carcols.dat модели, которую определяет тот же readme, и строки carmods.dat и weapon.dat (сам Mod Loader
  их читает).
- Движок читает не больше 23 моделей в группе машин; `--cargrp` дописывает имя в конец строки группы, поэтому
  для уже полной группы выдаётся предупреждение `CARGRP_FULL` (в ванильной группе 6 их 26).
- Десять строк педов `SPECIAL01-10` не подходят под шаблон readme Mod Loader для педов: такой донор получает файл
  IDE.
- MTA не умеет задавать стоимость и фары в handling, поля мотоциклов, лодок и самолётов, смещения прицеливания и
  навык пистолета `cop`; это становится предупреждениями. Кадры анимаций оружия переводятся в секунды (кадры / 30).

## Python-API (если другие пакеты его используют)

```python
from satk.addon.game import GameData
from satk.addon import handling, weapon, fields
from satk.addon.add import add
from satk.addon.data import get, patch, explain
```
