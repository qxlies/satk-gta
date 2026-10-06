# sa-re: символьная БД `gta_sa.exe` — адрес → функция, исходник, патчи MTA

[English version](../en/re.md)

Пакет: `satk.re`.

## Что это

Лёгкая символьная база для стокового `gta_sa.exe` 1.0 US HOODLUM из чистой копии, без Ghidra и без сети. Отвечает
на вопросы «что это за адрес из крэш-лога», «где эта функция в gta-reversed» и «какие патчи MTA (upstream, наш
trunk, Neon) стоят внутри неё». Пишет только `work\re\symdb.sqlite` (пересобирается за 10–20 с, можно удалять) и
экспорт в `work\re\export\`.

## Быстрый пример

```powershell
satk re build
satk re addr 0x53BF09
satk re find CPed::Update --limit 3
satk re nodes --type bike
```

Что вернёт `re addr` (сокращённо):

```json
{"ok":true,"id":"fn:0x53bee0","addr":"0x53bf09","fn":"CGame::Process","start":"0x53bee0","off":"0x29",
 "confidence":"high","bounds":"next_start","src":"game_sa/Game.cpp:78","reversed":true,
 "patches":{"cols":["addr","kind","symbol","origin","src","hit"],
            "rows":[["0x53bf09","hookinstall","HOOKPOS_CStreaming_Update_Caller","trunk","Client/multiplayer_sa/CMultiplayerSA.cpp:638",true]],
            "total":22,"raw_total":33}}
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk re build [--exe PATH] [--no-trunk] [--ghidra-functions FILE]` | — | пересобрать базу из всех источников |
| `satk re addr 0x53BF09 gta_sa.exe+0x13E4FA` · `--text-file crash.txt` · `-` (stdin) | `re_addr` | адрес или крэш-лог → функция+смещение, `file:line`, thunk, патчи; один адрес — объект, несколько — таблица |
| `satk re find NAME [--kind func\|global\|vtable\|struct]` | `re_find` | точное имя, затем `::член`, префикс, подстрока; если совпадений нет — функция, которую знает база знаний (без хука: `kb: not reversed at file:line`), или член базового класса (`inherited: ...`), иначе имена с теми же словами в `did_you_mean` |
| `satk re src FN [--context 30]` | `re_src` | строки gta-reversed вокруг установки хука и определения (только показать, в файлы не копировать); `--context` 0..400; функцию, для которой в базе нет строки, показывает база знаний (`via: kb`, `status: not reversed ...` с `file:line`; и до `satk re build`); `CVehicle::X`, объявленная в базовом классе, отвечает как `CPhysical::X` с полем `inherited`; для неизвестного имени `did_you_mean` предлагает похожие |
| `satk re patches [--fn FN \| --range 0xA-0xB] [--origin upstream\|trunk\|neon\|satk\|all] [--kind K]` | `re_patches` | патчи MTA с `file:line` |
| `satk re limits [--kind pool\|array\|store\|id_range\|streaming\|world] [--match S] [--summary]` | — | лимиты движка: ваниль / trunk / Neon; по 20 строк на страницу, `--summary` — только счётчики по видам |
| `satk re nodes [--type automobile\|mtruck\|quad\|heli\|plane\|boat\|train\|fheli\|fplane\|bike\|bmx\|trailer\|ped]` | — | имена фреймов, которые движок ищет у каждого типа транспорта и у педов, прочитанные из `gta_sa.exe`: имя, id, роль (деталь, дамми, экстра), флаги |
| `satk re export ghidra\|x32dbg\|json [--out DIR]` | — | `ApplySaSymbols.py` + `symbols.json`, `gta_sa.exe.dd32`, `symbols.json` |

SID'ы `fn:`, `g:`, `vt:`, `patch:` работают и через общие `asset_get` / `asset_find` / `asset_refs`:
`g:0xc8d4c0` → `gGameState` (`int32`); `asset_refs("fn:0x53bf09", rel="patches")`; `rel` для `fn:` — `patches`,
`callers`, `callees`, `vtables`.

## Как это устроено

**Источники** (все клоны в `src\` читаются через `git cat-file` по ревизии, без checkout, `GIT_NO_LAZY_FETCH=1`;
`git status` там остаётся чистым):

| Источник | Что даёт |
|---|---|
| `src\gta-reversed\docs\hooks.json` (ревизия `origin/master`) | 8 025 функций: имя, `file:line`, reversed/locked |
| gta-reversed `source/**` | 2 345 глобалов `StaticRef<T>(0x…)` с типами и длинами массивов, 385 vtable, 769 размеров структур, пулы и хранилища |
| plugin-sdk `plugin_sa/game_sa` (`HEAD`) | ещё 1 631 имя функции (весь RenderWare API), глобалы, 5 972 call-site'а `RefList` |
| `gta_sa.exe` | секции, thunk'и (`.HOODLUM` 500, `push -1; jmp` 87, `jmp` 195), цели `call`, указатели на функции, слоты vtable |
| MTA: `src\mtasa-neon` (`upstream/master` и Neon `HEAD`), `engine\mtasa` (trunk) | `HOOKPOS_*`, `HookInstall*`, `MemPut/MemSet/MemCpy`, манифесты релокаций Neon; лимиты из `engine\mtasa\docs\limits.toml` |
| по желанию: экспорт Ghidra `functions.jsonl` (`--ghidra-functions`) | точные границы функций, включая разорванные тела |

**Адрес → функция.** `gta_sa.exe+0x…` переводится в VA (база 0x400000). В крэш-дампе MTA берутся `Offset =`
(вместе с `Module =`), `(IDA: 0x…)` и `EIP=`; значения других регистров игнорируются. Адрес в `.HOODLUM` относится к
функции, чей thunk прыгает на ближайшее тело ниже адреса (`in_hoodlum: true`); адрес в теле, перенесённом
`push -1; jmp`, — к владельцу thunk'а (`via_thunk`). Иначе берётся ближайший известный старт ниже адреса (около
20 800 стартов: именованные плюс слоты vtable, цели `call`, указатели из `.rdata/.data`, адреса после `ret` и
выравнивания).

**`confidence`:** `high` — именованный старт без неименованных между ним и адресом; `medium` — между ними есть
неименованный старт (тогда `fn` = `sub_<адрес>`, а именованный — в `alt`) или тело перенесено; `low` — именованных
стартов нет; `exact` — границы из Ghidra (`"bounds":"ghidra"`, поле `ranges` перечисляет куски тела). Пример честного
`medium`: `0x41B1D0` → `sub_41b1d0`, `alt: CCollision::CameraConeCastVsWorldCollision+0x1d0`.

**Границы из Ghidra.** `satk re build --ghidra-functions work\re\ghidra\export\functions.jsonl` берёт точные
границы из экспорта анализа Ghidra (сама Ghidra при сборке не нужна; `summary.json` рядом, если он есть, должен
называть тот же хэш exe). Имена по-прежнему дают сканеры исходников. С экспортом `0x53BF09` — это `CGame::Process`
с `confidence: exact` и двумя диапазонами, а `0x41B1D0` становится точным `sub_41b1d0`.

**Таблицы фреймов.** `satk re nodes` читает `CVehicleModelInfo::ms_vehicleDescs` (12 указателей по адресу
0x8A7740, по одному на тип транспорта) и список педов по адресу 0x8A6268 прямо из чистого `gta_sa.exe` (база
символов не нужна). У строки детали `id` — номер узла, у дамми — номер позиции (`ped_frontseat` = 4), у экстр — 0;
`flag_words` расшифровывает биты флагов. С базой знаний номера получают имена из перечислений gta-reversed
(`CAR_WHEEL_LF`, `DUMMY_SEAT_FRONT`). Модель без нужного имени теряет эту деталь в игре.

**Патчи в ответе:** сначала накрывающие адрес (`hit: true`), затем по адресу; строки upstream, которые повторяет
trunk, скрыты (trunk = upstream + наши коммиты). Полный список — `satk re patches`. Происхождение `satk`
зарезервировано для наших собственных патчей.

## Ограничения и известные проблемы

- Без экспорта Ghidra границы функций эвристические («до следующего старта»).
- Патчи MTA с вычисляемым адресом (`MemPut(pAddr + i, …)`) в базу не попадают; их число — в
  `satk re build --json` (`stats` из `meta` базы, ключ `patch_scan.*.unresolved`).
- Адреса других модулей (`core.dll+0x…`) возвращаются как есть.
- Если `satk re addr` отвечает `NOT_READY`, база не собрана: `satk re build`.
- Базе нужна раскладка адресов 1.0 US; с другим exe `satk re addr` добавляет предупреждение `UNSUPPORTED_EXE`
  (раскладку показывает `satk game info`).
- Эталонные числа и контрольные адреса — `tests/golden/re.json`; проверка — `pytest tests/re -m game` (сборка во
  временный каталог, около 10 с). Другие решения — в [troubleshooting.md](troubleshooting.md).

## Python-API (если другие пакеты его используют)

```python
from satk.re.db import open_db
from satk.re import api

db = open_db()                                   # NOT_READY, если базы нет
d = api.addr_detail(db, 0x53BF09)                # dict: fn, off, src, confidence, patches...
loc = db.amap.locate(0x1566830)                  # Location(start, off, in_hoodlum, via_thunk, ...)
```
