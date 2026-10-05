# Идентификаторы (SID)

[English version](../en/ids.md)

<!-- Источник правды — src/satk/core/ids.py (замороженный контракт).
     Примеры ответов настоящие (`satk asset get`, профиль vanilla, 2026-10-05), сокращены. -->

Всё, что знает satk, адресуется короткой строкой — **SID** (стабильный идентификатор): `model:411`,
`tex:bistro/vent_64`, `inst:lae2_stream0#4`, `fn:0x53bf09`. Одни и те же SID возвращают индекс, вьювер, Blender,
символьная база и игра, поэтому ответы разных инструментов складываются друг с другом.

## Грамматика

```
sid := kind ":" key [ "@" layer ]
```

- Каноническая форма — в нижнем регистре, адреса — `0x…` строчными. На входе регистр не важен:
  `Model:Infernus` = `model:infernus`.
- В ключе нет абсолютных путей и номеров строк БД, поэтому SID не меняются после пересборки индекса.
- `@layer` пишется только у версии, которая **не** победила в профиле: в профиле `samp` модель 300 — это
  `lapdna` (`model:300`), а ванильное определение — `model:300@vanilla` (`cutobj01`).
- `satk asset get` принимает только SID: `infernus` без вида даёт `BAD_ID`, нужно `model:infernus`.
  Команды моделей (`satk model image`, `satk asset export`) понимают и голое имя.

## Виды

| kind | ключ | пример | что это |
|---|---|---|---|
| `model` | номер модели (на входе можно имя) | `model:411`, `model:infernus` | активное определение из IDE; в ответе всегда номер, имя — в поле `name` |
| `dff`, `txd` | имя файла без расширения | `dff:infernus`, `txd:vehicle` | активный файл: «первый зарегистрированный архив побеждает» |
| `tex` | `txd/текстура` | `tex:bistro/vent_64` | текстура внутри TXD; одно имя текстуры ключом не служит (711 имён повторяются) |
| `pix` | 24 шестнадцатеричных символа | `pix:45f2dcad0a0e790249e06eb9` | уникальное содержимое картинки (одинаковые пиксели в разных TXD — один `pix`) |
| `file` | путь[/запись] | `file:models/gta3.img/infernus.dff` | конкретный файл, в том числе затенённый |
| `col` | имя COL-модели | `col:infernus_col` | активная коллизия (у машин — встроенная в DFF) |
| `ide` | путь | `ide:data/vehicles.ide` | файл определений |
| `ipl` | имя | `ipl:lae2_stream0` | текстовый или бинарный файл расстановки |
| `inst` | `ipl#номер` | `inst:lae2_stream0#4` | одна расстановка (номер — индекс в секции `inst` этого IPL) |
| `item` | `ipl#секция#номер` | `item:lae2#enex#0` | прочие секции IPL (входы, машины, …) |
| `zone` | код | `zone:gan1` | зона из `map.zon`/`info.zon`; ключ — код (`GAN1`, `VE`, `LA`), игровое название — в поле `title` («Ganton») |
| `ifp`, `anim` | имя / `ifp/anim` | `ifp:ped`, `anim:ped/walk_civi` | анимации |
| `fn`, `g`, `vt`, `patch` | адрес или имя | `fn:0x53bf09`, `fn:cped::update`, `g:0xc8d4c0`, `vt:0x86c538` | функции, глобальные переменные, vtable и точки патчей MTA в `gta_sa.exe` |
| `el` | `тип/id` | `el:vehicle/7` | элемент мира во время работы (цель `game` через MTA-агент, а также `mock`) |
| `bm`, `cap`, `note` | имя / id | `bm:grove_center`, `cap:20261004-163136-1a23`, `note:1` | закладки камеры, снятые кадры, заметки |

У индекса есть и SID файлов данных (схема v3): `water:<n>`, `tcyc:<погода>/<час>`, `handling:<id>`. Их понимают
`asset get/refs/find`; в списке `satk.core.ids` их пока нет, поэтому другие пакеты их не принимают (см.
[index.md](index.md)).

## Как выглядят ответы

| SID | что вернёт `satk asset get` (сокращённо) |
|---|---|
| `model:411` | `"name":"infernus","sec":"cars","txd":"infernus","tex":{"total":13,"missing":0},"geo":{"verts":3573,"tris":3072},"links":{"dff":"dff:infernus","txd_chain":["txd:infernus","txd:vehicle"],"col":"col:infernus_col"}` |
| `inst:lae2_stream0#4` | `"model":"model:17613","name":"Lae2_roads89","pos":[2489.3,-1668.5,12.3],"rz":0.0,"lod":"inst:lae2#198","ipl":"ipl:lae2_stream0","aabb":[…]` |
| `tex:bistro/vent_64` | `"txd":"txd:bistro","w":64,"h":64,"d3dfmt":"X8R8G8B8","pix":"pix:6e63ecc6dc8ee4ad8bfd1139"` |
| `pix:45f2dcad0a0e790249e06eb9` | `"w":256,"h":256,"d3dfmt":"DXT1","textures":["tex:a51_ext/ws_rooftarmac1",… 243 TXD]` |
| `txd:vehicle` | `"file":"file:models/generic/vehicle.txd","ns":"loose","tex_count":19` |
| `col:infernus_col` | `"version":3,"via":"embedded","spheres":20,"faces":14,"models":["model:411"]` |
| `ipl:lae2_stream0` | `"kind":"binary","parent":"ipl:lae2","n_inst":377,"items":{"cars":5}` |
| `zone:gan1` | `"name":"GAN1","title":"Ganton","min":[2222.56,-1722.33,-89.08],"max":[2632.83,-1628.53,110.92],"file":"file:data/info.zon"` |
| `fn:0x53bf09` | `"id":"fn:0x53bee0","fn":"CGame::Process","off":"0x29","src":"game_sa/Game.cpp:78","confidence":"high","patches":{…}` — адрес внутри функции даёт SID её начала |
| `g:0xc8d4c0` | `"name":"gGameState","type":"int32","src":"app/app.h:19","patches":{…}` |
| `bm:grove_center` | `"pose":{"pos":[2490.0,-1655.0,16.0],"look":[2500.0,-1700.0,16.0],"fov_h_deg":70.0},"env":{"time":"12:00",…}` (закладка, сохранённая в этом рабочем пространстве) |

## Как получить и использовать SID

- Найти: `satk asset find <текст> [--kind K]` — сначала точные имена; подстрока — шаблоном `"*текст*"`
  (для заметок работает и по-русски: `--kind note`).
- Раскрыть: `satk asset get <sid>` — объект и связанные с ним SID.
- Пройти по связям: `satk asset refs <sid>` — без `--rel` вернёт, сколько связей каждого вида; с
  `--rel inst|tex|models|lod|patches|…` — сами связи.
- Рядом с точкой: `satk world near X Y [--r R]` — расстановки `inst:` по расстоянию.
- С кадра вьювера: `satk view capture --marks N` (легенда «номер → SID») и `satk view pick PX PY`.

## Повороты

В IPL кватернион хранится «как в файле», а мировой поворот — **сопряжённый** (движок меняет знак мнимой части). satk везде отдаёт мировые значения: `rz` в градусах, если поворот только вокруг Z, иначе
`q:[x,y,z,w]`.

## Быстрый пример

```powershell
satk asset get model:411
satk asset refs model:411
satk asset get inst:lae2_stream0#4
satk asset get fn:0x53bf09 --fields fn,off,src,confidence
satk asset get model:300@vanilla --profile samp
```
