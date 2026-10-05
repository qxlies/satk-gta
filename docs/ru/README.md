# satk — руководство пользователя

[English version](../en/README.md)

<!-- Оглавление всех страниц docs/ru; разделы те же, что в docs/en/README.md.
     Проверяют `satk dev linkcheck`, `satk dev docs-smoke` и `satk dev docs-parity`. -->

satk (San Andreas ToolKit) — набор инструментов для моддинга, реверс-инжиниринга и отладки GTA San Andreas (PC).
Человек и ИИ-ассистент пользуются одним и тем же: команда `satk` в терминале и MCP-сервер `satk` в ИИ-клиенте
выполняют одни и те же операции и дают одинаковые ответы. `satk help --ru` показывает основные задачи,
`satk help --all` — все команды.

## Безопасность и приватность

- **Игра только читается.** Папка игры, чистая копия и клоны исходников защищены от записи; satk пишет только
  в `<workspace>\work\` (индексы, кэши, картинки, экспорт, логи). Любая запись проходит через одну проверку,
  которая отказывает в защищённых путях с кодом `PROTECTED_PATH`.
- **Ни сети, ни телеметрии.** Сам satk в интернет не ходит. Загрузки бывают только в командах, которые вы
  запускаете намеренно (`bootstrap.ps1 -Deps`, `satk engine setup`, `satk dev release`); `satk doctor` показывает
  это проверкой `network`. Сокеты — только на 127.0.0.1 (вьювер, игра, SAAP).
- **ИИ не обязателен.** Всё работает из командной строки. С облачным ИИ-ассистентом ответы satk уходят его
  провайдеру как часть разговора; полностью локальный вариант (LM Studio) не отправляет ничего — см.
  [ai.md](ai.md).
- **Отчёт об ошибке остаётся у вас:** `satk bug-report` пишет обезличенный локальный файл и ничего не отправляет
  ([bugreport.md](bugreport.md)).

## С чего начать

1. [Установка на любой машине](install.md): `satk init` сам находит игру; переносимому zip не нужен Python.
2. [Быстрый старт](quickstart.md): от нуля до первого скриншота за десять команд.
3. [Идентификаторы (SID)](ids.md): как адресуются модели, текстуры, расстановки и функции.
4. [Типовые задачи](workflows.md): готовые цепочки команд.
5. [Если что-то не работает](troubleshooting.md) и [словарь](glossary.md).

Пример конкретной раскладки рабочего пространства (копия игры, клоны исходников, вьювер и форк MTA рядом):
[workspace-gta.md](workspace-gta.md).

## Я хочу…

| Я хочу… | Команда | Страница |
|---|---|---|
| настроить satk на свою игру | `satk init`, затем `satk doctor` | [install.md](install.md) |
| проиндексировать игру (один раз, после изменений) | `satk index build` | [index.md](index.md) |
| найти модель, текстуру или файл | `satk asset find infernus` | [index.md](index.md), [ids.md](ids.md) |
| увидеть текстуры на одном листе | `satk texture image model:411 --mode sheet` | [media.md](media.md) |
| увидеть модель или кусок карты | `satk model image model:411`, `satk map image --center 2495,-1687` | [models.md](models.md), [media.md](media.md) |
| листать всё на веб-странице без сети | `satk catalog build` | [catalog.md](catalog.md) |
| выгрузить модель в glTF или OBJ | `satk asset export model:411 --format glb` | [models.md](models.md) |
| заменить текстуры в TXD | `satk texture replace txd:bistro Plate=bistro/Marble.png` | [texmod.md](texmod.md) |
| править DFF, COL или IMG без Blender | `satk rw patch`, `satk col write`, `satk img build` | [rw.md](rw.md) |
| проверить мод на ошибки | `satk asset lint`, `satk mod check` | [lint.md](lint.md), [modinspect.md](modinspect.md) |
| узнать, что меняет мод и какие моды конфликтуют | `satk mod inspect mymod.zip`, `satk mod conflicts` | [modinspect.md](modinspect.md) |
| найти свободные id моделей или перенести мод | `satk id free --kind vehicle` | [idmgr.md](idmgr.md) |
| сконвертировать карту (SA-MP, MTA, IPL) | `satk map convert my_map.pwn --to mta` | [mapconv.md](mapconv.md) |
| сравнить свою игру со стоковой | `satk index diff vanilla installed` | [index.md](index.md) |
| понять, почему игра упала | `satk crash analyze --last` | [crash.md](crash.md) |
| превратить адрес `gta_sa.exe` в функцию | `satk re addr 0x53BF09` | [re.md](re.md), [kb.md](kb.md) |
| полетать камерой и спросить «что это за объект» | `satk view goto`, `satk view capture --marks 6` | [viewer.md](viewer.md) |
| открыть район в Blender и выгрузить его в MTA | `satk blender import-area --center 2495,-1687 --r 60` | [blender.md](blender.md) |
| подключить ИИ-ассистента | `satk mcp config --client cursor` | [ai.md](ai.md), [mcp.md](mcp.md) |
| сообщить о проблеме с satk | `satk bug-report` | [bugreport.md](bugreport.md) |

## Все страницы

| Страница | О чём | Команды |
|---|---|---|
| [install.md](install.md) | установка на любой машине: zip, git-клон, Python-пакет; поиск игры | `satk init`, `doctor` |
| [quickstart.md](quickstart.md) | от нуля до первого скриншота | десять команд |
| [ids.md](ids.md) | стабильные идентификаторы (SID) всего, что адресует satk | `satk asset get` |
| [workflows.md](workflows.md) | типовые задачи цепочками команд | все |
| [troubleshooting.md](troubleshooting.md) | известные проблемы и их решения | `satk doctor` |
| [glossary.md](glossary.md) | форматы игры, понятия satk, смежные проекты | — |
| [workspace-gta.md](workspace-gta.md) | пример конкретной раскладки рабочего пространства | `satk config show` |
| [game.md](game.md) | чистая копия игры: проверка, защита, воспроизведение | `satk game` |
| [formats.md](formats.md) | парсеры форматов GTA:SA (IMG, DFF, TXD, COL, IDE, IPL, IFP, ZON, DAT) | `satk formats` |
| [index.md](index.md) | SQLite-индекс ассетов: профили, поиск, связи, SQL | `satk index`, `asset`, `world` |
| [media.md](media.md) | текстуры, контакт-листы, карта сверху | `satk texture`, `map image` |
| [texmod.md](texmod.md) | моддинг текстур: DXT-энкодер, сборка и правка TXD, папки для Mod Loader | `satk texture pack`, `replace`, `extract` |
| [rw.md](rw.md) | запись RenderWare без Blender: правка DFF, COL из JSON, архивы IMG | `satk rw`, `col`, `img` |
| [models.md](models.md) | превью моделей без GPU, экспорт в glTF, OBJ и raw | `satk model`, `asset export` |
| [catalog.md](catalog.md) | офлайн-каталог моделей, текстур и зон в HTML с миниатюрами | `satk catalog build` |
| [lint.md](lint.md) | линтер ассетов: DFF, TXD, COL, IDE и связи между ними | `satk asset lint`, `asset lint-rules` |
| [modinspect.md](modinspect.md) | что меняет мод и как Mod Loader совмещает моды | `satk mod inspect`, `conflicts`, `effective`, `check` |
| [idmgr.md](idmgr.md) | свободные id моделей, конфликты id между модами, перенос мода | `satk id` |
| [mapconv.md](mapconv.md) | конвертеры карт (Pawn SA-MP, `.map` MTA, IPL, JSON), бинарный IPL, чистка района | `satk map convert`, `map validate`, `ipl`, `map clean` |
| [paths.md](paths.md) | пути машин, лодок и пешеходов (NODES*.DAT) | `satk paths` |
| [crash.md](crash.md) | дампы и логи крэшей: отчёт, известное решение, виновник | `satk crash` |
| [re.md](re.md) | символьная БД `gta_sa.exe`: адрес → функция, исходник, патчи MTA | `satk re` |
| [kb.md](kb.md) | база знаний о движке: исходники, раскладки структур, опкоды, факты | `satk kb` |
| [describe.md](describe.md) | описания моделей (gta-scout) в заметках | `satk describe` |
| [viewer.md](viewer.md) | вьювер: камера, кадры с метками, «что это за объект» | `satk view` |
| [saap.md](saap.md) | SAAP/1 — протокол «глаз агента» (обзор) | `satk saap`, `view conformance` |
| [mta-agent.md](mta-agent.md) | «глаза» в настоящей игре через ресурс MTA (цель `game`) | `satk view … --target game` |
| [blender.md](blender.md) | Blender без окна: импорт моделей и районов, рендер, экспорт в MTA; аддон | `satk blender` |
| [engine.md](engine.md) | форк MTA: настройка, зависимости, сборка | `satk engine` |
| [mcp.md](mcp.md) | MCP-сервер, обзор, диагностика, справка и заметки | `satk status`, `doctor`, `help`, `note`, `mcp` |
| [ai.md](ai.md) | подключение ИИ-клиентов (Claude Code, Codex, Cursor, VS Code, LM Studio и другие), skill | `satk mcp config --client`, `agent install-skill` |
| [bugreport.md](bugreport.md) | приватный локальный отчёт о проблемах satk | `satk bug-report` |
| [release.md](release.md) | переносимый релиз для Windows | `satk dev release` |
| [credits.md](credits.md) | благодарности и все источники satk и его исследований по темам | — |

Новая страница начинается с шаблона [`_template.md`](_template.md).

## Как читать эти страницы

- Команды даны для PowerShell или cmd. В git-клоне `satk` — это `<workspace>\tools\satk.cmd`
  (в Git Bash — `satk.sh`); переносимый zip открывает консоль, где `satk` работает как есть.
- В терминале ответы печатаются таблицами, в пайпе и у ИИ-ассистента — компактным JSON. `--table` и `--json`
  переключают формат.
- Позиционные аргументы пишите первыми: `satk asset get model:411 --fields name,sec`
  (см. [troubleshooting](troubleshooting.md#cli-the-following-arguments-are-required)).
- Раздел **«Быстрый пример»** на каждой странице — настоящие команды: `satk dev docs-smoke` выполняет их все и
  падает, если команда сломалась или не существует. Примеры с вьювером используют тестовую цель `--target mock`.
- У каждой страницы есть английская версия, ссылка на неё — в самом начале.

## Быстрый пример

```powershell
satk version
satk help
satk config show --section paths
```

`satk help` печатает меню «Я хочу…» с точными командами (по-русски — `satk help --ru`); `satk config show`
показывает, где satk ищет игру, рабочую папку и инструменты.

## Для разработчиков

- Как разрабатывать satk: [`CLAUDE.md`](../../CLAUDE.md) в корне репозитория (на английском).
- Документы для ИИ-агентов (только на английском): [`SKILL.md`](../agent/SKILL.md),
  [`workflows.md`](../agent/workflows.md), [`errors.md`](../agent/errors.md), [`evals.md`](../agent/evals.md)
  и генерируемые справочники [`tools.md`](../agent/tools.md) и [`schema.md`](../agent/schema.md).
- Проверки этих страниц: `satk dev docs-smoke` (быстрые примеры), `satk dev linkcheck docs README.md README.ru.md`
  (пути и ссылки) и `satk dev docs-parity` (у каждой английской страницы есть русское зеркало).
