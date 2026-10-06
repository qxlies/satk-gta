# Сцена: свои модели, машины и педы во вьювере

[English version](../en/viewscene.md)

Пакет: `satk.viewscene`; методы SAAP `scene.*` вьювера (текст протокола: `proto/SAAP-v1.md`, раздел 11).

## Что это

`satk view place|vehicle|ped` ставят вашу работу в запущенный вьювер прямо на карту: модель из только что
выгруженных файлов (DFF + TXD), машину с цветом, грязью, фарами и битыми деталями, педа в позе из анимации. Каждая
сущность получает handle; кадры с метками, `view pick` и слой ID показывают её как `el:scene/<handle>`. С `--watch`
вьювер перечитывает файлы при изменении, и повторный экспорт из Blender или `satk rw patch` появляется примерно
через полсекунды. Это быстрая визуальная проверка; поведение (физика, столкновения, повреждения, стриминг)
проверяется в настоящей игре через MTA ([mta-agent.md](mta-agent.md)). Ничего не записывается: вьювер только
читает переданные файлы.

## Быстрый пример

```powershell
satk view vehicle 411 --pos 2495,-1675,13.4 --heading 90 --dirt 2 --colors 3 1 --target mock
satk view ped 105 --pos 2497.5,-1673,13.4 --heading 180 --target mock
satk view place --model 1280 --pos 2493,-1671,13.4 --heading 30 --target mock
satk view list --target mock
```

Что отвечает вьювер (сокращённо; mock отвечает теми же полями):

```json
{"ok":true,"id":"s1","sid":"el:scene/s1","ref":"@s1","kind":"vehicle","model":"infernus","model_id":411,"pos":[2495.0,-1675.0,12.75],"heading":90.0,"grounded":true,"vehicle":{"wheels":{"from":"own","scale":[0.7,0.7],"count":4},"colors":[{"slot":1,"index":3,"rgb":"#840410"},{"slot":2,"index":1,"rgb":"#f5f5f5"}],"dirt":2,"lights":false,"parts":{"door_lf":"ok","bonnet":"ok"}},"load_ms":19.8,"rtt_ms":23.2,"target":"ariane"}
{"ok":true,"id":"s1","sid":"el:scene/s1","kind":"ped","model":"fam1","pos":[2497.5,-1673.0,13.3],"heading":180.0,"ped":{"anim":{"ifp":"ped","name":"idle_stance","time":0.0,"duration":1.5,"bones":32,"nodes":32}}}
{"ok":true,"cols":["handle","sid","kind","model","x","y","z","heading","watch","reloads","load_ms","file","error"],"rows":[["bench","el:scene/bench","object","bench",2493.0,-1671.0,12.75,30.0,true,2,5.2,"<workspace>/work/tmp/bench/bench.dff",null]]}
```

Mock рисует поставленные сущности коробками и держит их только в пределах одной команды; настоящий вьювер хранит
их до `view remove` или до остановки. С вьювером (видимое окно):

<!-- docs-smoke: skip запускает Ariane и пишет файлы в work -->
```powershell
satk view start --target ariane --window 960x540
satk asset export model:1280 --format raw
New-Item -ItemType Directory -Force work\tmp\bench
Copy-Item work\cache\raw\vanilla\parkbench1.dff work\tmp\bench\bench.dff
Copy-Item work\cache\raw\vanilla\benches_cj.txd work\tmp\bench\bench.txd
satk view place work\tmp\bench\bench.dff --txd work\tmp\bench\bench.txd --pos 2493,-1671,13.4 --ground --watch --id bench
satk view vehicle premier --pos 2495,-1675,13.4 --heading 90 --dirt 2 --id car
satk view ped 105 --pos 2497.5,-1673,13.4 --heading 180 --id fam
satk view capture --pos 2503,-1666,17 --look 2495,-1674,13.4 --marks 6
satk rw patch work\tmp\bench\bench.dff --material-color 0=255,0,0 --out bench_red
Copy-Item work\out\rw\bench_red\bench.dff work\tmp\bench\bench.dff
satk view capture --pos 2503,-1666,17 --look 2495,-1674,13.4 --marks 6
satk view list
satk view stop
```

Скопируйте исправленный DFF поверх отслеживаемого (экспортёр, пишущий на месте, делает то же самое), и следующий
кадр покажет его; `view list` считает перезагрузки.

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk view place [DFF] [--txd T…] [--model ID] --pos X,Y,Z [--heading H \| --rot RX,RY,RZ] [--scale S] [--ground] [--watch] [--id NAME]` | — | модель из файлов или объект игры; `--model` вместе с DFF даёт его TXD как запасной |
| `satk view vehicle MODEL\|--dff D --pos X,Y,Z [--heading H] [--colors C1 C2 …] [--dirt 0-15] [--lights] [--parts door_lf=dam bonnet=off …] [--wheel-model W] [--wheel-scale D] [--no-ground]` | — | машина со своими фреймами, колёсами из IDE, цветами carcols (индекс или `#rrggbb`), грязью, фарами и состоянием деталей |
| `satk view ped MODEL\|--dff D --pos X,Y,Z [--heading H] [--anim NAME] [--ifp FILE] [--anim-time S] [--no-ground]` | — | пед со скиннингом в позе из кадра IFP (по умолчанию `ped` / `idle_stance`) |
| `satk view reload [HANDLE]` | — | перечитывает файлы одной сущности или всех; неудачная перезагрузка оставляет старую модель |
| `satk view remove HANDLE… \| --all` | — | убирает сущности |
| `satk view list` | — | handle, SID, позиции, файлы, число перезагрузок, последняя ошибка перезагрузки |

Каждая команда принимает `--target ariane|mock` (по умолчанию `ariane`). ИИ-агенты вызывают их через
`satk_ops("view place")` → `satk_op("view.place", {...})`. Время и погода: `satk view set --time 21:30
--weather 8`.

## Как это работает

- Вьювер загружает каждую сущность в собственную копию (clump, текстуры), поэтому перезагрузка подменяет её, не
  трогая данные игры. TXD ищутся в заданном порядке, затем игровые (для машин `vehicle.txd`); текстуры, которых нет
  нигде, возвращаются предупреждением `NOT_FOUND` с именами.
- Машины: атомики `*_vlo` и битые детали скрыты, пока их не попросили; `--parts NAME=dam` показывает `NAME_dam`,
  `=off` убирает деталь. Колёса — копии колеса машины (левые развёрнуты), при колесе из другой модели они
  масштабируются по размеру колеса из IDE. Цвет заменяет цвета четырёх материалов покраски; уровень грязи `i`
  пересчитывает `vehiclegrunge256` в `c·i/16 + 255·(16−i)/16`; включённые фары берут `vehiclelightson128`. Стёкла
  (альфа материала меньше 255) рисуются после всего непрозрачного.
- Педы рисуются со скиннингом в позе одного кадра анимации, в том числе в слоях ID и глубины, поэтому pick
  попадает в руку там, где она нарисована.
- `--ground` (по умолчанию для машин и педов) пускает луч вниз на коллизию карты и ставит на неё нижнюю точку
  модели.
- Отслеживание опрашивает файлы каждые 200 мс и перезагружает, когда они не меняются 150 мс.

## Ограничения и известные проблемы

- Во вьювере нет бликов, теней и частиц; машины освещены объектным фоновым светом временного цикла и одним светом
  сверху. Настоящий вид и поведение проверяются в игре через MTA.
- CJ (пед 0) собирается из одежды и не поддерживается; берите скин педа, например 105 (`fam1`).
- Сущности живут, пока жив процесс вьювера; `view start` после `view stop` начинает с пустой сцены.
- Старая сборка вьювера отвечает `UNSUPPORTED` с подсказкой пересобрать его; у цели `game` нет `scene.*`.

## Python API (если пакет используют другие)

```python
from satk.viewscene import api
api.vehicle("ariane", model=426, pos=[2495, -1675, 13.4], dirt=2)
```
