# `satk engine`: форк MTA — настройка, зависимости, сборка

[English version](../en/engine.md)

Пакет: `satk.engine`. Правила для агента внутри форка — `engine\mtasa\CLAUDE.md`.

## Что это

`engine\mtasa` — наш форк mtasa-blue (GPL-3.0), созданный **без сети** из локального клона `src\mtasa-neon`.
`satk engine` настраивает его, держит зависимости сборки под sha256-пинами и собирает клиент (Win32) и сервер (x64)
напрямую через MSBuild; ошибки сборки возвращаются строками `file, line, code, msg`. Пишет только в `engine\` и
`work\engine\` рабочего пространства; `src\` и игру не трогает.

## Быстрый пример

```powershell
satk engine doctor --table
satk engine status
satk engine rc-test --table
```

Что вернёт `doctor` (сокращённо, в JSON):

```json
{"ok":true,"cols":["check","status","msg","fix"],"rows":[["fork","ok","branch main @ e563d3478, base 715ec056d, 2 commit(s) ahead",null]],"status":"ok","summary":"15/15 engine checks ok"}
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk engine doctor [--deep]` | — | 15 проверок: checkout, remotes, донор чист, шимы, MSBuild/toolset/rc/premake, DXFiles, пины, установленные зависимости, решение, ключевые артефакты, место на диске |
| `satk engine status [--deep]` | `engine` (`cmd=status`) | ветка и ревизия, последние сборки, зафиксированные зависимости, сколько ключевых артефактов есть |
| `satk engine setup [--deps] [--offline] [--update-pins] [--reuse-from DIR]` | — | офлайн-клон и remotes, шимы и обёртка premake; `--deps` — ещё и зависимости клиента (загрузки, только с вашего согласия) |
| `satk engine rc-test [--control]` | — | 4 клиентских `.rc` через `rc.exe` с шимом `afxres.h`; `--control` доказывает ошибку без шима |
| `satk engine gen` | — | `premake5 vs2026` с `DXSDK_DIR` → `Build\MTASA.sln` (около 5 с) |
| `satk engine build [--project P] [--platform Win32\|x64] [--config Release\|Debug\|Nightly] [--target T] [--jobs N] [--toolset v143]` | `engine` (`cmd=build`) | сборка; `P` = `server`, `client`, `all`, `changed` или имя проекта (`"Game SA"`) |
| `satk engine server-smoke` | — | сервер x64 на `127.0.0.1:22103/22105`, ждёт готовности, `shutdown` |
| `satk engine sites-check [--fork PATH] [--manifest PATH]` | — | все проверки манифеста сайтов патчей sa-engine по стоковому exe и данным исследования (см. «Сайты патчей») |
| `satk engine sites-gen [--fork PATH] [--manifest PATH]` | — | пишет `Shared/sdk/satk/generated/SaeSites.gen.h` форка из `docs/sae/patch-sites.toml` |
| `satk engine sites-scan --base B --size S --stride T [--only CLASS] [--flag FLAG]` | — | кандидаты-операнды статического массива с классификацией (`variable-after-array` не патчить) |
| `satk engine test [--no-build] [--filter F] [--config Release\|Debug] [--suite auto\|client\|satk\|all] [--platform Win32\|x64\|both]` | — | собирает и запускает `Tests_Client` (Win32) и, если в форке есть `Tests/satk`, `Tests_Satk` для x86 и x64; падения возвращаются строками `suite, test, file, line, message` |
| `satk engine worktree create\|list\|remove [--path P] [--branch B] [--base REV] [--profile server\|full]` | — | лёгкие вторые чекауты форка в `work\wt`; см. «Второй чекаут» |
| `satk engine run CMD --args JSON` | `engine` | единый MCP-инструмент: `status`, `doctor`, `build` |

Каждая строка, кроме `setup`, `rc-test` и `run`, принимает `--fork PATH` — другой чекаут форка (worktree), см. «Второй чекаут» ниже. По умолчанию — настроенный форк.

Ещё параметры `build`: `--no-deps` (с именем проекта — не собирать проекты, на которые он ссылается),
`--regen auto|always|never` (перегенерировать проекты через premake), `--max-errors` (сколько ошибок вернуть; в
логе — все).

## Как это устроено

**Раскладка.** `engine\mtasa` — репозиторий форка (ветка `main` от upstream `715ec056d`; `lab/neon` — Neon только
для чтения). Вне репозитория лежат `Directory.Build.targets` и `shims\afxres.h` (MFC не нужен: шим подключается к
`ResourceCompile` только у проектов под `engine\mtasa\`), `satk-premake.lua`, `bootstrap.ps1`, `deps\` и
`deps-lock.json`. Эти файлы генерирует `satk engine setup` из `src/satk/engine/templates`.

**Git.** `neon` указывает на локальный клон `src\mtasa-neon` (только fetch); `upstream` — на GitHub и без вашего
согласия не fetch'ится (полная история — около 1 ГБ; `skipFetchAll`). У обоих push = `DISABLED`. `origin` пока нет:
он появится, когда форк опубликуют в приватный репозиторий.

**Зависимости.** DXFiles, CEF, discord-rpc, rapidjson, Unifont и закрытые
`net.dll`/`net_64.dll`/`net_arm64.dll`/`netc.dll` хранятся один раз в `engine\deps` (`cache\`, `net\<sha256>\`).
Хэши — в `deps-lock.json`: для CEF, discord-rpc, rapidjson и Unifont это пины upstream из `utils\buildactions`, для
DXFiles — наш, для net-модулей — «доверие при первой загрузке». Источники по порядку: хранилище, проверенные копии
(сам форк), загрузка. Действия premake `install_cef/install_discord/install_unifont/install_data` запускаются через
`satk-premake.lua` с `SATK_OFFLINE=1`: premake получает файлы из `deps\` и в сеть не ходит. Если CDN отдаст другой
`net.dll`, `setup --deps` откажет с `REVISION`; принять новый хэш — только явно через `--update-pins`.

**Сборка.** MSBuild вызывается напрямую (`-nodeReuse:false`, TEMP/TMP — в `<workspace>\work\tmp\engine-build`).
Перед сборкой проверяется отпечаток дерева (список файлов, `premake5.lua`, установленные зависимости): если он
изменился, проекты перегенерируются — premake раскрывает glob'ы только при генерации. `--project changed` собирает то, что изменилось с последней успешной
сборки платформы: правка `.cpp` → только его проект (DLL/EXE), заголовок или статическая библиотека → решение
платформы целиком, документация — ничего. Логи: `work\engine\build\logs`. Параллельная вторая сборка получает
`BUSY`.

Замеры на эталонной машине (Release): сервер x64 с нуля 3:00, клиент Win32 3:14, правка одного `.cpp` в
`Client/game_sa` → `"Game SA"` 2 с, no-op x64 1,5 с. `engine\` занимает около 6,8 ГБ (две конфигурации).

**Neon.** `engine\mtasa\docs\porting\neon-ledger.toml` — реестр переносов (волны 0–3, диапазоны PR, предсказанные
конфликты); `engine\mtasa\docs\limits.toml` — лимиты движка: ваниль / наш trunk / Neon.

## Второй чекаут: `--fork` и `engine worktree`

Настроенный форк (`engine\mtasa`) — одно рабочее дерево, поэтому собирать в нём одновременно может только одна
линия работ. **Worktree** — второй чекаут того же репозитория со своей веткой, своими `Build\` и `Bin\`. Каждая
операция, которая работает с форком, принимает `--fork PATH`: `doctor`, `status`, `gen`, `build`, `test`,
`server-smoke`, `sites-check`, `sites-gen` и `sites-scan` (последняя читает только стоковый exe и экспорт Ghidra;
опция принимается для единообразия). Без `--fork` всё работает ровно как раньше; `--fork` с настроенным путём
равносилен отсутствию опции.

```powershell
satk engine worktree create --path <workspace>\work\wt\sae2-srv --branch feat/sae2-srv-base
satk engine build --fork <workspace>\work\wt\sae2-srv --project server --platform x64
satk engine build --fork <workspace>\work\wt\sae2-srv --project Tests_Client --platform Win32
satk engine test  --fork <workspace>\work\wt\sae2-srv --no-build
satk engine worktree list --sizes
satk engine worktree refresh <workspace>\work\wt\sae2-srv
satk engine worktree remove --path <workspace>\work\wt\sae2-srv --force
```

**`worktree create`** выполняет `git worktree add --no-checkout -b <branch> <path> <base>` (`--base` по умолчанию
`main`; ветка не должна существовать), затем sparse-checkout профиля, затем `git read-tree -m -u HEAD`. Каждая
команда git идёт с `GIT_NO_LAZY_FETCH=1`, ничего не скачивается. Путь должен быть `<workspace>\work\wt\<name>` (один
уровень, буквы, цифры и `._-`); всё остальное, а также junction, ведущий за пределы `work\wt`, отклоняется. Неудачный
create убирает то, что создал (worktree и его ветку). Первый sparse-checkout в репозитории заставляет git включить
`extensions.worktreeConfig` в конфиге репозитория (чтобы флаг sparse принадлежал одному worktree); в ответе тогда
есть предупреждение `REPO_CONFIG`.

**Профили.** `--profile server` (по умолчанию) извлекает то, что нужно серверу x64 и подключённым тестовым проектам;
список папок вычисляется из premake-файлов базовой ревизии, а не пишется вручную:

| Правило | Пример результата |
|---|---|
| строки `include` корневого `premake5.lua` вне блока `os.target() == "windows"`, целиком | `Server\*`, `Shared`, `Shared\XML`, серверные библиотеки `vendor\*` |
| каждый подключённый тестовый проект, включая проекты внутри блока Windows, и его транзитивные зависимости `include`/`links` | `Tests\client`, `Tests\satk`, `Tests\opennet`, `Shared\opennet`, `vendor\googletest` |
| папки, названные относительными путями в premake-файлах этих папок (`"../../vendor/sparsehash/src"` означает `vendor\sparsehash`; сам корень включений `vendor` пропускается) | `Client\sdk`, `vendor\sparsehash`, `vendor\mysql`, `vendor\bochs` |
| клиентские проекты и их библиотеки оставляют свои premake-скрипты и рекурсивно подключённые скрипты, чтобы premake смог записать решение | `Client\core\premake5.lua`, `Client\core\satk\premake5.lua`, `vendor\cegui-0.4.0-custom\premake5.lua` |
| действия и бинарник premake, и `docs\` (там лежит манифест сайтов патчей) | `utils\buildactions`, `utils\premake5.exe`, `docs` |

`--profile full` — обычный чекаут. Профиль записывается в `work\engine\forks\<fork-id>.json` вместе со списком
проектов, чьи исходники на месте; `doctor`, `status` и `build` его читают. `worktree create` также копирует
зафиксированный `net.dll` x64 из `engine\deps` в `Bin\server\x64` (сверка с `deps-lock.json`, без загрузок; нет пина —
шаг пропускается с пометкой).

**`worktree refresh PATH`** пересчитывает сохранённый профиль по текущему `HEAD` рабочего дерева и применяет его.
Используйте команду после обновления старого разреженного дерева: новые тестовые проекты и вложенные включения
premake появятся без ручной правки `info/sparse-checkout`. Ветка, локальные изменения и неотслеживаемые файлы
сохраняются; ошибки git выводятся без принудительного обновления дерева. Идущая сборка блокирует обновление.
Реестр проектов обновляется, следующий `engine build` заново генерирует решение. Команда читает premake-файлы
из коммита; новые включения нужно закоммитить до обновления профиля. Принимаются существующие рабочие деревья,
созданные satk в `work\wt`; их сохранённый профиль `server` или `full` остаётся прежним.

**Что меняет `--fork`.** Форк делит с настроенным `engine\deps`, `deps-lock.json`, обёртку premake и шимы. Своё:

| Что | Где |
|---|---|
| решение и результаты | `<fork>\Build`, `<fork>\Bin` |
| premake | `premake5 --file=engine\satk-premake.lua vs2026` с `SATK_FORK=<fork>` и `SATK_OFFLINE=1`: действия установки читают `engine\deps` и скачивать не могут (настроенный форк по-прежнему запускает собственный `premake5.lua` напрямую) |
| MSBuild | тот же вызов; `Directory.Build.targets` подключается через `-p:DirectoryBuildTargetsPath` и `-p:SatkForkRoot=<fork>\`, так что шим `afxres.h` действует и в worktree |
| логи | `work\engine\build\logs\<fork-id>\`; отчёты тестов — `work\engine\test\<fork-id>\` |
| отслеживание изменений | `work\engine\build\state.<fork-id>.json`: `--project changed` сравнивает с последней сборкой этого форка |
| блокировка и temp | `build-<fork-id>` (два форка собираются рядом; второй процесс в том же форке получит `BUSY`) и `work\tmp\engine-build-<fork-id>` |

`<fork-id>` — имя папки для чекаута прямо под `work\wt`, иначе имя плюс 6 символов хэша пути. В форке с профилем
`server` решение по-прежнему перечисляет клиентские библиотеки (исходников у них нет), поэтому `--project server` и
`--project all` собирают исполняемые файлы и DLL тех проектов, что на месте (статические библиотеки MSBuild
собирает по ссылкам между проектами); `--project client` отклоняется. Имя проекта работает как обычно. `doctor` и
`status` ждут только результаты сервера и `Tests_Client`.

Замеры на эталонной машине (Release, параллельно шли другие сборки): `worktree create` 3 с (6000 файлов, 263 МБ), сервер x64 с нуля 2:45-3:40, `Tests_Client` 1:25 (313 тестов зелёные), `server-smoke` готов за 1,7 с, no-op пересборка x64 18 с (по одному вызову MSBuild на исполняемый файл или DLL); готовое дерево занимает около 2,0 ГБ.

**`worktree list`** показывает все worktree репозитория форка (`--sizes` добавляет число файлов и мегабайты);
**`worktree remove --path P`** выполняет `git worktree remove` (`--force` отбрасывает и незакоммиченное), отказывает
для настроенного форка, путей вне `work\wt` и форка с идущей сборкой, и оставляет ветку, если нет
`--delete-branch` (`git branch -d`, только для ветки, созданной `create`). Для агентов операция только в CLI
(`satk_op` отвечает `CONSENT_REQUIRED`): она меняет git-метаданные репозитория форка.

## Сайты патчей (sa-engine)

Патчер sa-engine в форке пишет байты exe только в **сайтах** из `docs/sae/patch-sites.toml` (схема 1: группы с
идентификатором отчёта и фазой, сайты с адресом, длиной, видом и стоковыми байтами, которые должны быть на месте до
записи). `satk engine sites-*` следит, чтобы этот файл не врал. Инструменты читают стоковый exe (`[paths] game`),
`work/re/ghidra/` (`symbols/hoodlum_map.json`, `export/functions.jsonl`, `calls.jsonl`, `xrefs_data.jsonl`) и базу
символов (`work/re/symdb.sqlite`, патчи trunk); пишут только сгенерированный заголовок и
`work/re/securom_keys.json`.

```powershell
satk engine sites-check                       # манифест по умолчанию: <fork>\docs\sae\patch-sites.toml
satk engine sites-gen                         # перезаписать SaeSites.gen.h (в том же коммите, что и правка манифеста)
satk engine sites-scan --base 0xC3E058 --size 0xF00 --stride 0x3C --limit 60
satk engine test
```

**`sites-check`** при успехе возвращает таблицу групп; иначе `CHECK_FAILED` с находками строками
`check, where, va, msg` (до 60; полное число — в `error.data.total`). Коды находок:

| Код | Смысл |
|---|---|
| `TOML_PARSE`, `SCHEMA` | не TOML, неизвестный ключ, неверный тип, не тот `schema`, неверные `phase` или `kind` |
| `DUP_GROUP_ID`, `DUP_SITE_ID`, `DUP_REPORT_ID`, `BAD_ID`, `REPORT_ID_RANGE` | идентификаторы уникальны и из `[a-z0-9_.]` (и остаются допустимыми именами C++ после замены точек на подчёркивания); id отчётов лежат в 91000..91999 |
| `KIND_LEN` | `imm8` — 1 байт; `imm32`, `f32`, `absref`, `data32` — 4; `hook` — не меньше 5; любой сайт 1..16 |
| `GOLDEN_WINDOW`, `ALT_BAD`, `ARRAY_BAD`, `ACCEPT_BAD` | окно golden (до 24 байт) покрывает записываемый диапазон; в `alt` столько же токенов, сколько в `golden`; `array` только у `absref`; `accept_*` только у `data32` |
| `EXE_SHA`, `GOLDEN_MISMATCH`, `GOLDEN_UNBACKED` | хэш в манифесте — это проверяемый exe; golden-байты равны байтам exe; окно есть в данных файла |
| `ARRAY_RANGE` | стоковый операнд `absref` лежит в `[base, base + size + stride]` |
| `OVERLAP_KEY` | записываемый диапазон задевает одно из 256 окон ключей SecuROM (по 4 байта) |
| `OVERLAP_STOLEN` | задевает один из 745 сайтов «украденных» инструкций HOODLUM (`jmp stub` плюс заполнение) |
| `SITE_IN_RELOCATED_FUNCTION` | лежит в `.text`-слоте одной из 500 функций, перенесённых в `.HOODLUM` |
| `OVERLAP_TRUNK` | пересекается с патчем trunk форка (таблица `patch` в symdb, origin `trunk`) |
| `OVERLAP_SITE` | два сайта пишут один и тот же байт |
| `HEADER_MISSING`, `HEADER_STALE` | сгенерированного заголовка нет или он отличается от того, что записал бы `sites-gen` |

Предупреждения (`warn`): `ALLOW_UNUSED` (запись `allow_overlap` ни с чем не совпала). В `allow_overlap` допустимы
`trunk:0xADDR` (адрес патча trunk), `key:0xADDR`, `stolen:0xADDR`, `reloc:0xADDR` (вход перенесённой функции) и
`site:<группа>/<сайт>`. Сайты группы `id.anchors` и сайты с `readonly = true` только читаются: к ним применяются
структурные проверки и проверка golden, но не проверки пересечений (якоря идентификации exe включают и сам входной
переходник перенесённой функции).

*Ключи SecuROM.* Защищённый exe прячет константы за вычислениями `op reg, [KEY]` рядом с чтением таблицы в
`.HOODLUM` (0x1557000..0x1562000). `satk.engine.securom` находит ключи встроенным декодером длин инструкций x86:
проходит `.text` и тела в `.HOODLUM`; чтение таблицы берёт первый другой абсолютный операнд в следующих 4
инструкциях (останавливаясь на `popfd`, `xchg`, `ret`, `jmp`, `call`), иначе в 2 предыдущих. Стоковый exe обязан
дать ровно **256** различных адресов (302 чтения таблицы, у 2 ключа нет); результат кэшируется в
`work/re/securom_keys.json` по хэшу exe.

*Перенесённые функции.* Их вход — 5-байтовый `jmp` в `.HOODLUM`; байты за ним до начала следующей настоящей функции —
мёртвый остаток (SecuROM хранит там заглушки украденных инструкций), поэтому golden-байты ещё совпадают, а патч
не действует. Слот — `[вход, начало следующей функции)` по `functions.jsonl`; началом считается перенесённый вход,
функция с именем из gta-reversed или plugin-sdk либо функция с ребром `call` в `calls.jsonl`.

**`sites-gen`** пишет таблицы C++ детерминированно и побайтно стабильно (переводы строк LF). Раскладка, всё в
`namespace sae`:

```cpp
// generated by satk engine sites-gen from docs/sae/patch-sites.toml sha256=<hex>; do not edit
#pragma once

#include "../SaePatchPlan.h"

#include <cstddef>
#include <cstdint>

namespace sae::sites
{
    inline constexpr const char* kManifestSha256 = "<hex>";
    inline constexpr const char* kExeSha256 = "<sha256 exe>";

    // group cap.pools: report 91101, phase ctor, lane E2
    enum class cap_pools : std::uint16_t
    {
        pool_ped,
        COUNT
    };

    inline constexpr sae::SiteDef kSites_cap_pools[] = {
        {"pool.ped", 0x550FF2, 4, sae::SiteKind::Imm32, 0x550FF1, 8, {0x68, 0x8C, 0x00, 0x00, 0x00, 0x8B, 0xC8, 0xE8}, 0, {}, {}, 0x0, 0x0, 0x0, 0x00000000, 0x00000000},
    };

    inline constexpr sae::GroupDef kGroups[] = {
        {"cap.pools", 91101, sae::Phase::Ctor, kSites_cap_pools, 1},
    };
    inline constexpr std::size_t kGroupCount = sizeof(kGroups) / sizeof(kGroups[0]);

    static_assert(std::size_t(cap_pools::COUNT) == sizeof(kSites_cap_pools) / sizeof(kSites_cap_pools[0]));
}
```

Поля сайта по порядку: id, va, len, kind, golden va, длина golden, байты golden, длина alt, байты alt, маска alt,
база, размер и шаг массива, маска и значение accept. Группы и сайты идут в порядке манифеста (порядок enum — это
порядок сайтов). Хэш в первой строке — SHA-256 TOML с переводами строк, приведёнными к LF, поэтому checkout с CRLF
даёт тот же хэш; `sites-check` сравнивает файл целиком.

**`sites-scan`** перечисляет 32-битные операнды инструкций `.text` и `.HOODLUM`, содержащие адрес из
`[base, base + size + stride]` и имеющие xref данных Ghidra за 1-7 байт до себя, и классифицирует их по
инструкции:

| Класс | Смысл |
|---|---|
| `operand-in-array` | ниже `base + size`: адрес элемента, его переписывают при переносе или росте массива |
| `true-end` | ровно `base + size` в сравнении (`81 /7`, `3D`) или `lea`: граница цикла |
| `end-plus-field` | в `(base + size, base + size + stride]` в сравнении или `lea`: граница со смещением поля |
| `variable-after-array` | на конце или дальше в любой другой форме (обращение к памяти, `mov reg, imm`, `push imm`): следующий объект, **не патчить никогда** |

Флаги: `static-init` (у функции нет вызывающего в коде: статические конструкторы и деструкторы CRT, колбэки),
`in-hoodlum`, `in-relocated-function`, `no-array-ref-in-function` (граница в функции, которая сам массив не
трогает: вероятно, граница соседнего объекта) и `unclassified` (форма инструкции не распознана). В ответе всегда
есть `counts` по классам; `--only CLASS`, `--flag FLAG`, `--limit` и `--offset` листают строки.

**`satk engine test`** собирает `Tests_Client` (Win32) через `engine build`, запускает
`Bin\tests\Tests_Client.exe --gtest_output=json:work\engine\test\<метка времени>.json` и возвращает успех как `ok` со
счётчиками, любое падение — как `CHECK_FAILED` со строками `suite, test, file, line, message` (падение процесса,
таймаут или отсутствующий отчёт — одна строка `(process)`). `--no-build` запускает готовый бинарник, `--filter` —
фильтр googletest.

`--suite` выбирает проекты: `client` — только `Tests_Client` (Win32), прежнее поведение; `satk` — `Tests_Satk`
(`Tests/satk` форка: по одной единице трансляции на каждый заголовок sa-engine, тесты `Satk_*` и правило
POD-интерфейсов, см. `docs/sae/core-x64.md` форка) для `--platform Win32`, `x64` или `both` (по умолчанию); `all` —
оба проекта; `auto` (по умолчанию) — `client` плюс `satk`, если в чекауте есть `Tests/satk/premake5.lua`, поэтому
форк без этого проекта ведёт себя как раньше. При нескольких запусках в ответе есть список `runs` (проект,
платформа, счётчики, путь к отчёту), а строки падений получают префикс `Tests_Satk x64: <suite>`. Тест идентичности
exe из набора `Satk_*` читает `gta-sa-clean\gta_sa.exe` (только чтение) через `SAE_STOCK_EXE`, команда задаёт её,
если файл существует.

## Ограничения и известные проблемы

- `satk engine` не запускает клиент MTA. Для первого запуска клиента нужна однократная настройка от администратора —
  см. [mta-agent.md](mta-agent.md). `server-smoke` — только loopback, без ресурсов, `ase 0`, без рассылки по LAN.
- Имена и бренд пока от upstream (форк ещё не переименован); свой `BRANCH_ID` появится вместе с переименованием.
- Новый MSVC (сейчас 14.51, v145) может сломать сборку раньше, чем это заметит CI upstream: запасной вариант —
  `--toolset v143` (MSVC 14.44).
- Каждая сборка Win32 перелинковывает `core.dll` (upstream `gen_language_list` всегда переписывает заголовок).
- Мало места на диске — `satk engine doctor` предупреждает, если на диске форка свободно < 12 ГБ; `Build\obj`
  (около 4,7 ГБ) можно удалить, он пересобирается.
- Worktree с профилем `server` содержит сервер x64 и подключённые тестовые проекты (`Tests_Client`, `Tests_Satk`, `Tests_OpenNet`, если они есть). Клиентские библиотеки вне их зависимостей представлены только скриптами генерации. Данные `install_data` (`net_arm64.dll`, `netc.dll`) не устанавливаются.
- `engine worktree create` нужны blob'ы базовой ревизии в репозитории форка (форк — частичный клон, и ничего не скачивается); отсутствующий blob git сообщает, а не докачивает.
- `sites-scan` и проверка перенесённых функций опираются на выгрузку Ghidra в `work/re/ghidra/`: ссылку, которую
  Ghidra не нашла, в списке нет, а слот перенесённой функции — приближение (см. выше).
- Другие решения — в [troubleshooting.md](troubleshooting.md).

## Python-API

```python
from satk.engine.build import build, plan, parse_msbuild_log   # сборка и разбор логов MSBuild
from satk.engine.doctor import run_checks, status               # проверки и состояние
from satk.engine.setup import fork_info, manifest, load_lock    # форк и зависимости
from satk.engine.worktree import compute_profile, create, refresh  # разреженный профиль, рабочие деревья
from satk.engine.common import layout                          # layout(fork) = пути второго чекаута
from satk.engine.sites_check import check_manifest              # проверки сайтов патчей
from satk.engine.sites_scan import scan_array                   # поиск операндов массива
from satk.engine.securom import load_keys                       # набор ключей SecuROM
```
