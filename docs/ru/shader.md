# `satk shader textures|new|check`: шейдеры MTA для текстур мира

[English version](../en/shader.md)

Пакет: `satk.shader`. Берёт имена текстур из индекса профиля, пишет ресурсы MTA:SA по своим шаблонам и компилирует
эффекты программой `fxc.exe` из локального Windows SDK, если он установлен.

## Что это

Шейдер MTA находит текстуры игры по имени: `engineApplyShaderToWorldTexture(shader, "*road*")`. Подбирать имена
вручную долго и легко ошибиться: `*road*` цепляет ещё дорожные знаки, `broadway92body256a` (машину) и LOD-текстуры.
`textures` показывает имена по маске, по виду текстур (`road`, `glass`, `water`, `tree`, `vehicle`, `ped` …), по
модели или по участку карты, со счётчиками, и считает короткий список масок, который попадает ровно в эти имена.
`new` пишет готовый ресурс (`meta.xml`, клиентскую Lua-обвязку с этими масками и `.fx`) по шаблону: отражения на
краске машин, замена текстуры мира, мокрые дороги, вода, оттенок неба и тумана, тон кожи и одежды педов,
постобработка. `check` компилирует эффект и проверяет то, что в MTA работает иначе, чем в обычном Direct3D, плюс
клиентский Lua ресурса. Всё пишется в `<workspace>\work\out\shader\`; satk не трогает ни игру, ни сервер MTA.

## Быстрый пример

```powershell
satk shader textures --kind road --limit 3
satk shader textures --model model:411 --limit 3
satk shader new wet_roads --name wetroads
satk shader check wetroads
```

Что приходит в ответ (сокращённо):

```json
{"ok":true,"cols":["name","txd","txds","models","inst"],"rows":[["crossing_law","barrio1_lae +36",37,285,285],["sf_road5","airportroads_sfse +24",25,239,239],["…"]],"total":270,"plan":{"exact":true,"patterns":27,"apply":["tar_*","*hiway*","*junc*","*road*","…"],"remove":["*sign*","*broadway*","*wheel*","…"]},"plan_cols":["op","pattern","covers","extra","sample"],"plan_rows":[["apply","*road*",132,16,"broadway92body256a, …"],["…"]],"select":["kind road: 270","profile vanilla: 13756 texture names"]}
{"ok":true,"rows":[["vehiclegeneric256","csbravura +9",10,352,0],["…"]],"total":13,"plan":{"exact":true,"patterns":10,"apply":["infernus*","…"],"remove":["vehiclespecdot64"]},"spill":{"names":10,"other_models":1638,"note":"other models use the same names; pass targetElement …"}}
{"ok":true,"dir":"<work>/out/shader/wetroads","files":["meta.xml","client.lua","wet_roads.fx"],"textures":{"apply":17,"remove":10,"names":270,"exact":true},"install":"copy the folder wetroads into <MTA server>/mods/deathmatch/resources/ and run 'start wetroads' …","check":{"status":"ok","compiler":"fxc","errors":0,"warnings":0}}
{"ok":true,"rows":[],"status":"ok","compiler":{"tool":"fxc","sdk":"Windows SDK 10.0.26100.0","profile":"fx_2_0"},"compiled":true,"techniques":["wet_roads: vs_2_0/ps_2_0","fallback: fixed-function"],"lua":{"linked":"shader (file name)","textures":270,"…":"…"}}
```

`lua.textures` — сколько имён в итоге покрывают маски ресурса по правилам MTA (побеждает последнее совпадение):
ровно 270 имён вида `road`.

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk shader textures [PATTERN...] [--kind K] [--model SID] [--area X Y R] [--exclude P...] [--max-extra N]` | — (через `satk_op`) | имена (`name, txd, txds, models, inst`, для участка ещё `inside`), план `plan` (маски apply/remove) и `plan_rows` (`op, pattern, covers, extra, sample`), «утечка» `spill` |
| `satk shader new TEMPLATE [--name X] [--textures P...] [--kind K] [--model SID] [--area X Y R] [--exclude P...] [--image PNG]` | — | ресурс в `<work>/out/shader/<name>/`, проверенный (`check`), с инструкцией по установке |
| `satk shader check FILE.fx\|FOLDER\|NAME [--lua F...] [--define N=V...] [--fxc PATH\|none] [--strict]` | — | находки (`sev, code, where, msg`), `status`, компилятор, техники, кто задаёт каждый параметр, сверка с Lua |

Отборы у `textures` и `new` складываются через И: `--kind road --area 2495 -1666 150` — дорожные текстуры
Грув-стрит. Маски пишутся по правилам MTA: нижний регистр, `*` и `?`, остальное буквально (`cj` само по себе
означает `cj_ped_*`).

Виды (правила в `data/shader/kinds.json`; это эвристика, список стоит просмотреть): `road`, `pavement`, `grass`,
`dirt`, `sand`, `rock` (правила имён плюс поверхность коллизии каждой текстуры), `glass`, `water`, `tree` (имена,
флаги IDE «дерево» и «стекло»), `lod` (текстуры только LOD-моделей), `vehicle`, `vehicle_paint` (перекрашиваемые
материалы краски), `ped` (TXD педов и `player.img`). Для `vehicle` и `ped` проще шейдер с `elementTypes`
`"vehicle"` / `"ped"`, наложенный на `*`; об этом говорит поле `hint`.

План: при `--max-extra 0` (по умолчанию) он точный — короче из двух: только маски apply или широкие маски apply
плюс маски remove (`*road*` минус `*sign*`); каждый план сверяется со всеми именами текстур профиля.
`--max-extra N` вместо этого разрешает каждой маске apply N лишних имён (список короче, без remove). `spill` для
отбора `--model` или `--area` считает, где ещё встречаются те же имена: MTA сравнивает по имени, поэтому шейдер мира
меняет их и там (адресовать можно только элементы, созданные скриптом, через `targetElement`).

Шаблоны `new`:

| Шаблон | Текстуры по умолчанию | `elementTypes` | Что делает |
|---|---|---|---|
| `car_paint` | вид `vehicle_paint` | vehicle | отражение неба с эффектом Френеля и блик солнца; цвета неба берутся из `getSkyGradient` |
| `texture_replace` | нет: укажите `--textures`, `--kind`, `--model` или `--area` | world, vehicle, object, other | рисует вместо них `--image` (по умолчанию шахматная заглушка) |
| `wet_roads` | вид `road` | world, object | асфальт темнее, отражение неба и блик солнца в меру `getRainLevel` |
| `water` | `waterclear256` | world, object, other | бегущая рябь, отражение неба, оттенок |
| `sky_fog` | `*` | world, object | оттенок мира, дымка по расстоянию и подходящий `setSkyGradient` (сбрасывается при остановке) |
| `ped_shading` | `*` | ped | насыщенность, контраст, яркость, оттенок; только пиксельный шейдер, скиннинг не трогается |
| `post_process` | экран | — | цветокоррекция и виньетка до отрисовки HUD (`dxCreateScreenSource`, `onClientHUDRender`) |

Каждый ресурс включается и выключается в игре командой `/<name>`; значения, которые обвязка задаёт через
`dxSetShaderValue`, собраны в начале `client.lua`. Без индекса `new` берёт фиксированные запасные маски вида и
сообщает об этом (`warn: INDEX_MISSING`).

Находки `check` (большинство из них MTA молча пропускает, параметр просто остаётся нулём):

- `STATE_GROUP`, `STATE_NAME`, `STATE_STAGE`, `STATE_TYPE`: аннотации вроде `string renderState = "FOGCOLOR"` с
  неверным регистром группы, неизвестным регистром (с подсказкой), плохим префиксом `stage,` или типом, который MTA
  не сопоставляет (`bool` для `LIGHTING`, матрица по столбцам);
- `PROFILE`, `D3D10`, `UNKNOWN_FUNCTION`, `NO_TECHNIQUE`, `NO_PASS`, `TEXTURE_UNDECLARED`, `SEMANTIC`,
  `DEPTHBUFFER`, `SM3_FOG` (`ps_3_0` не получает туман фиксированного конвейера), `NO_FALLBACK`, `CUSTOMFLAGS`;
- `INCLUDE_ILLEGAL` (`..` в пути), `INCLUDE_MISSING`, `INCLUDE_LOOP`: MTA ищет `#include` относительно первого
  `.fx`, а путь, начинающийся с `/`, — от корня ресурса;
- Lua: `LUA_UNKNOWN_PARAM` (имя в `dxSetShaderValue`, которого нет; параметр с семантикой находится только по
  семантике, параметр с аннотацией задать нельзя), `LUA_OVERRIDES_MTA`, `LUA_TEXTURE_NOT_SET`,
  `LUA_ELEMENT_TYPES`, `NO_MATCH` (маска текстур мира, которая ничего не находит: обычно опечатка);
- `X####`: ошибки и предупреждения `fxc` с исходным файлом и строкой.

## Как это устроено

- Имена: все текстуры всех активных TXD индекса профиля (в нижнем регистре). Виды опираются на индекс (секции
  моделей, флаги IDE, `model_tex`, LOD-размещения, слоты цвета в `dff_mat`) и на таблицу «текстура → поверхность» из
  `satk col` (`data/colgen/tex_surface.json`). Данные загружаются один раз на файл индекса (около 0,6 с).
- Покрытие масками: кандидаты — префиксы, суффиксы и части слов нужных имён; жадное покрытие множества выбирает их,
  маски remove оцениваются числом масок, которые им нужны; при равенстве выигрывают маски, кончающиеся на границе
  слова. Одинаковый вход — одинаковый план.
- `check` читает эффект так же, как MTA (include, макросы), компилирует его `fxc /T fx_2_0` (`IS_DEPTHBUFFER_RAWZ`
  задан, как в MTA, оба значения, если файл его использует) и сверяет аннотации и семантики с фактами из исходников
  клиента MTA в `data/shader/mta.json`. `fxc` ищется в `--fxc`, `SATK_FXC`, `DXSDK_DIR`, Windows Kits (реестр и
  `Program Files`), папке Visual Studio / Build Tools, найденной satk, затем в `PATH`.
- Lua-обвязка: клиентские скрипты из `meta.xml` (иначе файлы `.lua` рядом с `.fx`), читаются статически: вызовы с
  литералами, строковые константы `local` и циклы `for _, p in ipairs(LIST)` по таблицам строк.

## Ограничения и известные проблемы

- Виды — эвристика по именам, поверхностям коллизии и флагам IDE: несколько лишних или пропущенных имён — норма;
  для окончательного списка используйте `--exclude` или явные маски.
- `fxc` работает через `D3DCompiler_47`, а MTA компилирует через D3DX9 во время игры. Оба принимают эффекты
  `fx_2_0`, редкие различия возможны. Без Windows SDK `check` делает только статическую проверку синтаксиса и правил
  MTA и прямо об этом пишет.
- Шаблоны проверены компиляцией и этими проверками, но не в запущенном клиенте MTA: посмотрите их в игре перед
  выпуском.
- Материалы без текстуры (часть деталей краски машин) шейдером текстур мира не достать.

## Python-API (если другие пакеты его используют)

```python
from satk.shader.wild import cover, resolve, matches     # сравнение имён как в MTA, точный набор масок
from satk.shader.fx import load                          # чтение эффекта по правилам include MTA
from satk.shader.check import find_fxc, compile_fx, mta_checks
```
