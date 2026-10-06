# Инспектор модов: что меняет мод и как Mod Loader совмещает моды

[English version](../en/modinspect.md)

Пакет: `satk.modinspect`. Правила перенесены из Mod Loader 0.3 (thelink2012/modloader, MIT) и его
datalib (BSL-1.0); уведомления — в `src/satk/modinspect/NOTICE-modloader.txt`.

## Что это

`satk mod` отвечает на вопрос моддера «что мой мод ломает и с чем конфликтует» так, как мод загрузил бы
[Mod Loader](https://github.com/thelink2012/modloader), без запуска игры:

- `mod inspect` читает один мод (папку, `.zip` без распаковки, `.img` или один файл) и перечисляет, что он
  меняет относительно файлов игры профиля: заменённые и новые стриминговые файлы, записи файлов данных
  (`поле старое->новое`), записи, которые файл данных выбрасывает, новые и перекрытые ID в IDE, строки readme,
  которые Mod Loader вливает;
- `mod conflicts` совмещает несколько модов (папку `modloader/` профиля или переданные папки и zip) и для
  каждого места, которое трогают два мода, называет победителя и правило, которое это решило;
- `mod effective` печатает строку данных, которую в итоге получит игра (например, строку `handling.cfg`
  модели 411), а без ключа может записать весь слитый файл;
- `mod check` превращает находки в проблемы и добавляет `asset lint`; с `--engine inu` вместо этого
  запускает INU Check, если вы установили его сами.

Всё только читается. Пишет лишь `mod effective --save` — в `work\out\mods\effective\<профиль>\`. <!-- linkcheck: ignore -->

## Быстрый пример

```powershell
satk mod conflicts --profile game
satk mod effective handling 411 --profile game
satk mod inspect data/handling.cfg --profile vanilla
```

Первая команда перечисляет конфликты модов, установленных в папку `modloader/` игры; вторая показывает строку
`handling.cfg`, которой после слияния пользуется модель 411; третья проверяет один файл (подходит и путь
относительно корня профиля) — это собственный файл чистой копии, поэтому различий нет.

Вывод для синтетического мода (`--table`; приёмочный тест `tests/modinspect/test_ops.py`): DFF, неполный
`handling.cfg` и readme со строкой `carcols.dat`:

```text
kind  target             change   by                   detail
----  -----------------  -------  -------------------  ------------------------------------------------
dff   dff:fastcar        replace  cars/fastcar.dff     model 401 fastcar in models/gta3.img
data  handling:FASTCAR   modify   data/handling.cfg:2  mass 1200->1250, max_vel 250->260
data  handling:TESTBOAT  modify   data/handling.cfg:3  boat.thrust_y 0.6->0.9
data  handling:NEWCAR    add      data/handling.cfg:5  NEWCAR 1600.0 3000.0 2.0 ...
data  handling:^0        remove   data/handling.cfg    the mod's file lacks it and replaces the game's; ...
data  carcols:testcar    modify   readme.txt:3         colours 1,2,2,1->3,3,0,0
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk mod inspect PATH [--profile vanilla] [--change ...] [--kind ...]` | — (через `satk_op`) | строки `kind, target, change, by, detail`; сводка `changes`, `unchanged` (файлы, совпадающие с файлами игры), `new_ids` |
| `satk mod conflicts [MODS...] [--profile installed] [--with-installed] [--ml-profile NAME] [--priority name=N ...]` | — | строки `target, kind, winner, losers, rule`; `mods` — в порядке установки; предупреждения `DROPS` |
| `satk mod effective FILE [KEY] [--profile installed] [--mod PATH ...] [--with-installed] [--save]` | — | `FILE`: `handling`, `carcols`, `ide`, `gta`, `default`, `object`, имя файла данных или IDE; `KEY`: id handling либо ID/имя модели, ID модели, индекс цвета, путь |
| `satk mod check PATH [--profile vanilla] [--engine satk\|inu] [--exe PATH] [--sev info] [--no-lint]` | — | строки `rule, sev, file, msg, sid`: коллизии ID, файлы, которые Mod Loader пропустит, выпавшие записи игры, ID за пределом, ёмкость движка (`mod.capacity`) и находки [`asset lint`](lint.md) |

Значения `change`: `replace` (файл игры подменён), `add` (новый файл или запись), `modify` (запись отличается),
`remove` (запись игры исчезает), `new-id`, `override-id` (ID в игре принадлежит другой модели), `load`
(ASI/CLEO), `ignored` (Mod Loader файл не возьмёт; причина — в `detail`).

## Как это устроено

- **Ёмкость (`mod.capacity`).** У стандартного одиночного движка хранилища фиксированы: 212 слотов моделей машин,
  278 педов, 51 оружия, 14 070 объектов, 169 объектов со временем и 92 клампов, 255 слотов файлов COL (слот 0
  занят самим движком), 5 000 слотов TXD, 100 записей IDE `2dfx` и 400 входов-выходов. Ванильная игра оставляет
  свободными немногие (2 педа, 1 оружие, 3 COL, 3 `2dfx`, 24 входа-выхода). Для каждого хранилища, которое
  пополняет мод, `mod check` добавляет строку: `error`, если итог больше стандартного размера, `warn`, если
  остаётся меньше 10 слотов, иначе `info`. Расширители лимитов увеличивают эти хранилища. Если доступна команда
  `satk texture budget`, мод с файлами TXD/DFF получает ещё подсказку `mod.streaming` о памяти стриминга 50 МиБ.

- **Файлы** (`classify.py`): файл обрабатывает первый принявший его плагин Mod Loader — имена FX (`hud.txd`,
  `particle.txd`, `vehicle.txd` …), ASI/CLEO, std.data (файлы данных — по имени, IDE/IPL — по пути, readme —
  `*.txt`), sprites (TXD в папке с именем txd), std.stream (DFF, TXD, COL, IFP, бинарные IPL, `carrec*.rrr`,
  `nodes*.dat` — только внутри папки `*.img`). Имена, начинающиеся с `.`, пропускаются. IDE/IPL мода
  загружается, только если его путь назван строкой `gta.dat` или его имя файла встречается ровно в одной строке.
- **Кто побеждает при замене файла** (`modloader.py`): моды ставятся по приоритету (1..100, по умолчанию 50,
  `modloader.ini` `[Profiles.<name>.Priority]`), затем по **длине** имени, затем по имени; побеждает последний.
  Поэтому при равном приоритете `longname` побеждает `zz`. Учитываются `IgnoreMods`, `IgnoreFiles`,
  `IncludeMods` с `ExcludeAllMods`, `ExclusiveMods`, `Parents` профиля и `.profiles/*.ini`.
- **Файлы данных** (`merge.py`, `traits.py`): если файл мода один и строк readme нет, файл мода заменяет файл
  игры. Иначе Mod Loader сливает по записям: запись игры, которой нет **хотя бы в одном** слитом файле мода,
  **удаляется** (неполный `handling.cfg` удаляет все машины, которых в нём нет); среди изменённых значений
  побеждает **самое редкое**, а при равенстве — мод, установленный **первым**; значение игры остаётся, только
  если его не меняет ни один мод. Записи: `handling.cfg` — по id (с учётом регистра; строки лодок, мотоциклов и
  самолётов идут вместе со своей основной строкой), `carcols.dat` — по индексу цвета и модели, IDE — по ID
  модели, `gta.dat` — по директиве и пути, `object.dat` — по модели. Числа сравниваются как 32-битные float,
  поэтому `1400` и `1400.0` равны.
- **Особенности чтения Mod Loader** (сохранены): `#` и `;` начинают комментарий в любом месте строки, поэтому
  `;the end` не останавливает Mod Loader (игра на этом останавливается); лишние токены в конце строки
  игнорируются; внутри слитого файла побеждает первая строка с данным id; readme больше 60000 байт не читаются.
- **Игра под модами** (`base.py`) читается из корня профиля: каталоги IMG, IDE, которые загружают файлы DAT,
  файлы данных. Сборка индекса не нужна. Файлы, побайтно совпадающие с копией игры, строк не получают и
  считаются в `unchanged`.

## Ограничения и известные проблемы

- Правила слияния перенесены для `handling.cfg`, `carcols.dat`, IDE, `gta.dat`/`default.dat` и `object.dat`;
  остальные сливаемые файлы (`weapon.dat`, `ped.dat`, `water.dat` …) сравниваются построчно, и конфликт на них
  сообщается по файлу целиком. Текстовые IPL заменяются, а не сливаются (как в Mod Loader).
- Порядок при равенстве в слитых файлах предполагает, что Mod Loader хранит свой список виртуальных файлов в
  порядке установки (его `unordered_multimap`; собственный код IPL в Mod Loader предполагает то же). Если
  равенство важно, проверьте его в игре.
- Строки readme: распознаются шаблоны handling, `carcols`, `vehicles.ide`/`peds.ide`/`veh_mods.ide` и
  `gta.dat`; строки readme для `weapon.dat` и `carmods.dat` — нет.
- `.img` внутри мода заменяет архив игры с тем же именем; остальным IMG нужна строка `IMG` в `gta.dat`. Zip
  читаются на месте; 7z/rar не поддерживаются (распакуйте их).
- INU Check необязателен и никогда не скачивается. Его командная строка (`SATK_INU_CHECK_ARGS`, по умолчанию
  `--json {path}`) и формат JSON не проверены на настоящей копии; адаптер (`inu-adapter/1`) принимает обычные
  имена полей, а если их нет — разбирает строки `SEVERITY: file: message` с предупреждением `INU_FORMAT`.

## Python-API (если другие пакеты его используют)

```python
from satk.modinspect.base import BaseGame
from satk.modinspect.modloader import read_folder
from satk.modinspect.world import World
from satk.modinspect.merge import merge

base = BaseGame("installed")
with World(base, read_folder(base.root).loaded) as w:
    trait, plan, _ = w.plan("handling.cfg")
    outcome = {o.key: o for o in merge(plan.stores, trait)}[("veh", "INFERNUS")]
```
