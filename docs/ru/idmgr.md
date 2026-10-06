# `satk id`: свободные id моделей, конфликты модов, перенос мода на другие id

[English version](../en/idmgr.md)

Пакет: `satk.idmgr`. Бинарные IPL и чистка карты — вторая половина этого набора — описаны на
[странице mapconv](mapconv.md).

## Что это

Машине, скину, оружию или объекту, которые добавляет мод, нужен id модели, который больше никто не занял, а два
мода с одинаковым id молча ломают друг друга. `satk id` отвечает по индексу ассетов на три вопроса: какие id
свободны во всех профилях, в которых вы играете (ваниль, установленная игра с папками modloader, SA-MP), какие id
и имена моделей определены дважды и как перенести id мода в свободный диапазон. В папке игры ничего не меняется:
мод с перенесёнными id — это копия под `<work>/out/idmgr/`.

## Быстрый пример

```powershell
satk id free --kind vehicle --count 3
satk id free --kind object --count 20 --contiguous
satk id conflicts --profile installed
satk id free --kind ped --target samp-dl --count 2
```

Что вернётся (сокращённо):

```json
{"ok":true,"kind":"vehicle","target":"sp","ids":[612,613,614],"blocks":["612-614"],"count":3,"range":"400-19999","free_in_range":3689,"taken_in_range":15911,"profiles":["installed","samp","vanilla"],"max_id":19999,"max_from":"engine","capacity":{"store":212,"used":212},"warn":["STORE_FULL: the stock engine has 212 vehicle model slots and a profile already defines 212: …","ADDON_VEHICLE: vehicle ids outside 400-611 need fastman92 LA (…)"]}
{"ok":true,"kind":"object","ids":[3136,3137,…,3155],"blocks":["3136-3155"],"count":20,…}
{"ok":true,"cols":["sev","code","id","name","where","other"],"rows":[],"n":0,"total":0,"profile":"installed","definitions":16286,"errors":0,"warnings":0}
{"ok":true,"kind":"ped","target":"samp-dl","ids":[20001,20002],"blocks":["20001-20002"],"range":"20001-30000","free_in_range":10000,"warn":["NO_ARTCONFIG: …"]}
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk id free --kind vehicle\|ped\|weapon\|object [--count N] [--range A-B] [--contiguous]` | — (через `satk_op`) | первые свободные id диапазона во всех собранных профилях; `capacity` — размер стандартного хранилища против наибольшего числа id в одном профиле |
| `satk id conflicts [MOD ...] [--profile installed] [--all]` | — | таблица `sev/code/id/name/where/other` id и имён, определённых дважды |
| `satk id remap MOD --to A-B [--map old=new] [--ids A-B]` | — | копия мода с перенесёнными id; `map` (`old`, `new`, `name`), `changes` (`file`, `kind`, `ids`) и `<work>/out/idmgr/<mod>.remap.json` |

Параметры `id free`:

- `--profile vanilla installed` — только эти профили (по умолчанию — все профили с собранным индексом);
- `--range 612-799,15000..15099` — где искать, в этом порядке; `a..b` может идти вниз (`-1000..-1100`);
  по умолчанию — от 400 (машины) или от 1 до предела id;
- `--max-id N` — последний допустимый id модели. По умолчанию 19999 (`NUM_MODEL_INFOS = 20000` стандартного
  движка) или `dff - 1` из ini fastman92 LA (`dff = N` / `FILE_TYPE_DFF = N`), если он есть во всех проверяемых
  профилях;
- `--mods ПАПКА ...` — ещё не установленные моды, чьи id считаются занятыми;
- `--no-samp` — не учитывать id SA-MP (клиентские скины 1–6, 8, 42, 65, 74, 86, 119, 149, 208, 265–273, 289,
  скины 300–311, объекты 11682–11753 и 18631–19999);
- `--target samp-dl` — вместо этого свои id SA-MP 0.3.DL / open.mp: объекты -1000..-30000 (`AddSimpleModel`),
  скины 20001..30000 (`AddCharModel`); `--artconfig ФАЙЛ` отмечает id, которые уже заняты в `artconfig.txt`.

Коды `id conflicts`: `CLASH` (ошибка: два мода, один id, разные модели), `REPLACES` (мод ставит другое имя модели
на id базовой игры), `DUPLICATE` (два мода определяют один и тот же id с тем же именем), `DUP_IN_MOD`,
`NAME_TWICE` (одно имя модели на двух id: их файлы DFF/TXD сталкиваются в modloader); с `--all` ещё `REDEFINES`
(мод переписывает строку базовой игры с тем же именем) и `BASE` (SA-MP повторно использует стандартные id).
`--no-use-profile` сравнивает только указанные папки модов.

`id remap` переносит все id, которые добавляет мод: старые id по возрастанию — на следующие свободные id из `--to`;
строка, которая переопределяет модель базовой игры под тем же именем, — это замена, и она остаётся (`kept`). Пары
`--map 15000=16000` важнее, `--ids` ограничивает, какие старые id переносятся, `--name` задаёт папку результата
под `<work>/out/idmgr/`, `--dry-run` ничего не пишет, `--changed-only` пишет только изменённые файлы, `--force`
заменяет папку прошлого запуска.

## Как это устроено

- **Кто занимает id.** Для каждого профиля: строки IDE, которые загружает игра (таблица `model` индекса, все
  слои), файлы IDE, которые лежат в игре, но не подключены файлами DAT (папки modloader, `SAMP\samp.ide` в
  одиночной установке, случайные копии), и похожие на IDE строки readme в папках modloader (modloader читает
  строки `vehicles.ide`/`peds.ide` из readme-файлов). Плюс id, которые движок использует без строки IDE: 2–3
  (значения загрузчика катсцен), 290–319 (особые персонажи, объекты катсцен), 374–399 (временная коллизия,
  модели одежды и рук). Источники — заголовки ModelInfo.h и eModelID.h из gta-reversed.
- **Пределы хранилищ.** В стандартном движке 212 слотов моделей машин, 278 педов, 51 оружия и 14 000 + 70
  объектов; ванильная игра уже занимает 212, 276, 50 и 14 045 из них. `STORE_FULL` предупреждает, когда
  запрошенные id не помещаются, `STORE_LOW` — когда свободно меньше 10 слотов (в ванили: 2 слота педов и 1 слот
  оружия); тогда нужен расширитель лимитов (Open Limit Adjuster, fastman92 LA). Собственные
  определения SA-MP не считаются (SA-MP поднимает свои лимиты сам).
- **Перенос сохраняет байты.** Меняются только токены id: первое поле строк `objs tobj anim cars peds weap hier`
  и `2dfx`, модель строк `inst` и `cars` текстовых IPL, поля модели в записях бинарных IPL (правятся на месте) и
  строки readme, у которых первое поле — старый id, а второе — имя этой модели. Пробелы, комментарии, концы строк
  и все остальные файлы остаются как были.

## Ограничения и известные проблемы

- IMG-архивы внутри мода копируются без изменений (`NOT_REWRITTEN`): id бинарных IPL внутри них остаются
  прежними. Скрипты (`.cs`, `.cm`, `.lua`, `.pwn`) могут использовать id моделей, которых satk не видит
  (`SCRIPTS`).
- Файлы данных, которые ссылаются на модели по имени (`handling.cfg`, `carcols.dat`, `carmods.dat`, настройки
  звука), менять не нужно; файлы, где числовые id стоят вне строк IDE/IPL/readme, не переписываются.
- Список клиентских скинов SA-MP и диапазоны DL взяты из документации SA-MP/open.mp, а не из кода.
- MTA выделяет id во время работы (`engineRequestModel`), поэтому свободный id ей не нужен.

## Python-API (если другие пакеты его используют)

```python
from satk.idmgr.free import open_profiles, taken_ids, free_ids
from satk.idmgr.scan import profile_defs, scan_mod, scan_mods
from satk.idmgr.conflicts import conflicts
from satk.idmgr.remap import plan, rewrite, rewrite_bytes
```
