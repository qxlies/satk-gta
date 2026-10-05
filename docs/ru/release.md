# `satk dev release`: переносимый релиз для Windows

[English version](../en/release.md)

Пакет: `satk.release`. Входные файлы упаковки: [`packaging/`](../../packaging/README.md).

## Что это

Одна команда превращает коммит satk в файлы, которые можно отдать моддеру без Python, без git и без прав
администратора:

- `satk-<version>-win64.zip` (≈48 МБ, в распакованном виде 124 МБ): официальный embeddable CPython 3.12 с
  python.org, колёса времени выполнения (Pillow, numpy, mcp и то, что им нужно в Windows; без pip и pytest),
  сам satk и ярлыки для запуска двойным щелчком;
- `satk_gta-<version>-py3-none-any.whl` и `satk_gta-<version>.tar.gz`: пакет для `pip`/`pipx` (имя на PyPI —
  `satk-gta`, команда остаётся `satk`; на PyPI пока не опубликован);
- `SHA256SUMS.txt` по трём файлам.

Релиз собирается из коммита, а не из рабочего дерева: незакоммиченные изменения дают только предупреждение
`DIRTY`. Один и тот же коммит и один и тот же lock дают побайтно одинаковые файлы. После сборки zip
распаковывается в папку, в имени которой есть кириллица и пробел, и запускается так, как его запустит новый
пользователь.

## Быстрый пример

<!-- docs-smoke: skip три сборки релиза идут около минуты; первая скачивает около 46 МБ -->
```powershell
satk dev release
satk dev release --smoke game
satk dev release --offline --smoke none --sandbox prepare
```

Первая сборка один раз скачивает закреплённые файлы в `<work>/cache/release` (≈46 МБ); дальше сборки работают
без сети. Что вернёт `--smoke game` (сокращённо):

```json
{"ok":true,"version":"0.1.0","commit":"16ae5c567","out":".../work/out/release/satk-0.1.0",
 "files":[{"name":"satk-0.1.0-win64.zip","mb":47.8,"sha256":"7483a107..."},
          {"name":"satk_gta-0.1.0-py3-none-any.whl","mb":1.4,"sha256":"3a781264..."},
          {"name":"satk_gta-0.1.0.tar.gz","mb":1.2,"sha256":"0134e486..."}],
 "zip":{"entries":3139,"unpacked_mb":124.3,"python":"3.12.10","wheels":31},
 "smoke":{"cols":["step","ok","seconds","summary"],"rows":[["version",true,1.2,"satk 0.1.0, Python 3.12.10 (bundled)"],
   ["index-build",true,8.1,"..."],["first-result",true,0.3,"3 rows, first tex:tags2_lalae/grove; first result 9 s after init"]]},
 "seconds":21.7}
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk dev release [--ref REF] [--out DIR] [--smoke none\|quick\|game] [--game DIR] [--offline] [--relock] [--sandbox none\|prepare\|run]` | — | собрать zip, колесо, sdist и `SHA256SUMS.txt` в `<work>/out/release/satk-<version>/` (или `--out`), затем проверить zip |

- `--smoke quick` (по умолчанию): `satk version`, `config show`, `doctor` и `mcp selftest` из распакованного zip
  в чистом окружении (без переменных `SATK_*`, `PYTHON*` и venv). `--smoke game` дополнительно выполняет
  `init --game`, `index build` и первый `asset find` на папке игры (`--game`, по умолчанию — чистая копия
  `paths.game`). Копия лежит в `<work>/tmp/release/smoke/`.
- `--relock` пересобирает `packaging/release-lock.json` из venv разработки (см. ниже) и пишет его в checkout;
  его нужно закоммитить.
- `--sandbox prepare` пишет рядом с zip `<out>/sandbox/satk-check.wsb`: двойной щелчок — и Windows Sandbox
  распакует zip на чистой Windows, соберёт индекс на копии игры только для чтения и запишет
  `<out>/sandbox/results/result.json`. `--sandbox run` делает то же через командную строку `wsb` в
  Windows 11 24H2+ и ждёт результата.

## Что внутри zip

```
satk-<version>-win64\
  Start satk.cmd               двойной щелчок: консоль, в которой работает "satk"; при первом запуске — "satk init"
  Запустить satk.cmd           тот же ярлык под русским именем
  satk.cmd                     python\python.exe -s -X utf8 -m satk %*
  satk-mcp.cmd                 python\python.exe -s -X utf8 -m satk.mcp (MCP-сервер по stdio)
  README-FIRST.txt, README-FIRST.ru.txt
  portable.txt                 переносимый режим: эта папка — рабочее пространство
  LICENSE, NOTICE.md, CHANGELOG.md, README.md, satk.toml.example
  RELEASE.json                 версия, коммит, Python, версии колёс
  python\                      embeddable CPython; python312._pth добавляет ..\src и "import site"
  python\Lib\site-packages\    колёса времени выполнения (распакованы, dist-info сохранены)
  src\satk\ data\ vendor\ proto\ mta-resources\ blender\ docs\
```

Раскладка повторяет checkout, поэтому всё, что ищет свои файлы относительно кода (файлы данных, вендоренные
gta-flow и rwfury, файлы conformance SAAP, аддон Blender), работает без изменений. `-s` не пускает
пользовательские `site-packages`; файл `._pth` и так игнорирует `PYTHONPATH`, а `-X utf8` делает безопасными
пути с любыми буквами.

## Переносимый режим

`portable.txt` рядом с `src\` делает эту папку рабочим пространством (`satk.core.config.portable_root`):
`satk init` пишет туда `satk.toml`, всё сгенерированное идёт в `work\`, а пользовательская конфигурация
(`%APPDATA%\satk\satk.toml`) не читается. Строка `workspace` в `satk.toml` папки, которая называет другую папку
(папку перенесли или `satk.toml` остался от старой версии), игнорируется с предупреждением
`PORTABLE_WORKSPACE`. `SATK_HOME` и `SATK_CONFIG` по-прежнему главнее. Удаление папки полностью удаляет satk.
`satk doctor` добавляет две проверки:

- `portable`: в папку можно писать, на её файлах нет метки «загружено из интернета» (Mark of the Web: Windows
  ставит её на файлы, распакованные из скачанного zip; исправление — `Unblock-File`), и `RELEASE.json`
  соответствует коду;
- `app_control`: блокирует ли Smart App Control модули расширений (см. ниже).

## Файл lock и проверка входных файлов

`packaging/release-lock.json` закрепляет всё, чего нет в git:

- **Python:** `python-3.12.10-embed-amd64.zip`. Ветка 3.12 получает исправления безопасности только в
  исходниках, поэтому 3.12.10 — её последний выпуск с бинарниками для Windows. `--relock` сверяет размер и MD5
  с API выпусков python.org и подпись Authenticode каждого `.exe`, `.dll` и `.pyd` (Python Software Foundation;
  `vcruntime140*.dll` — Microsoft), затем закрепляет SHA-256. Подпись OpenPGP ключа выпусков для Windows
  проверена вручную; команды — в [`packaging/README.md`](../../packaging/README.md).
- **Колёса:** замыкание extras из `pyproject.toml`, кроме `dev` (`img`, `num`, `mcp`), вычисленное для
  CPython 3.12 на 64-битной Windows, по одному колесу на пакет, с SHA-256, который публикует PyPI. Версии
  должны совпадать с `requirements.lock`; `excluded` перечисляет то, что не входит (pytest и его зависимости).

Сборка сверяет каждый файл кэша с lock; несовпадающий файл никогда не используется. Скачивание идёт только с
python.org и PyPI по HTTPS.

## Результаты приёмочного прогона

| Проверка | Результат |
|---|---|
| zip, колесо, sdist и `SHA256SUMS.txt` из коммита | 20 с с кэшем; две сборки одного коммита одинаковы |
| распаковка в `...\<кириллица> satk\` с чистым окружением | `version`, `config show`, `doctor` (ни одна проверка времени выполнения не падает), `mcp selftest` проходят |
| `init` + `index build` + `asset find` на чистой копии игры | 8 с от `init` до первого результата |
| распаковка Проводником (Shell) и `Expand-Archive` | по 3014 файлов, русское имя ярлыка цело |
| Windows Sandbox (без Python, сеть выключена, игра только для чтения) | первый результат через 10 с после `init`, 29 с вместе с запуском песочницы |

## Ограничения и известные проблемы

- **Smart App Control.** Модули расширений в колёсах PyPI (numpy, Pillow, pydantic-core, cryptography) не
  подписаны. Там, где Smart App Control включён (в Windows Sandbox он включён, как и в некоторых новых
  установках Windows 11), Windows отказывается их загружать: индекс, поиск, экспорт и пути на stdlib работают;
  превью, быстрые пути PNG/DXT и MCP-сервер — нет. `satk doctor` (`app_control`) об этом сообщает. Менять
  Smart App Control — решение пользователя; satk его никогда не трогает. Подпись (SignPath) — следующий шаг.
- Только 64-битные Windows 10/11. `satk doctor` в zip по-прежнему показывает компоненты разработки (форк MTA,
  вьювер, `pytest`) как предупреждения или ошибки; они необязательны.
- Обновление — распаковать новый zip рядом со старым и перенести `work\` и `satk.toml`.
- Колесо для PyPI несёт `satk/_data` и `satk/_vendor`; вендоренный код находится из checkout или zip, но пока
  не из установки через pip.

## Для разработчиков

- Код: `src/satk/release/` — `lock.py` (lock, `--relock`), `fetch.py` (проверенные загрузки), `tree.py`
  (`git archive` нужного ref), `wheels.py` (содержимое колёс без pip), `dist.py` (колесо и sdist средствами
  stdlib, в согласии с `pyproject.toml`, `setup.py` и `MANIFEST.in`), `build.py` (zip, контрольные суммы,
  проверка), `sandbox.py` (Windows Sandbox), `ops.py` (операция и проверки doctor).
- Входные файлы: `packaging/portable/` (файлы для корня zip), `packaging/CHANGELOG.md`,
  `packaging/sandbox/check.ps1`, `packaging/release-lock.json`.
- Тесты: `tests/release/` (синтетические деревья и колёса, настоящий HEAD, без сети).

## Публичный экспорт

Репозиторий разработки закрыт. Публичный репозиторий (`qxlies/satk-gta`) вместо его истории получает один
коммит-снимок на каждый публичный релиз; снимок пишет `satk dev export-public` из коммита репозитория разработки.

| Команда | MCP | Что делает |
|---|---|---|
| `satk dev export-public [--ref REF] [--out DIR] [--base REPO] [--check-only] [--no-tests] [--limit N]` | — | отфильтровать и проверить коммит, записать его git-репозиторием с одним коммитом и тегом `v<version>`, прогнать его тесты |
| `satk dev release --public [--repo DIR]` | — | релиз, файлы которого прошли ту же проверку; `--repo` собирает из другого checkout, например из снимка |

1. **Файлы.** `git archive` коммита (никогда не рабочее дерево) без файлов, подходящих под
   `data/public-exclude.txt` этого коммита (синтаксис gitignore; строки `@allow <rule> <path> [<regex>]`
   принимают известную находку). В списке — аудит установки игры самого сопровождающего, CLAUDE.md рабочего
   пространства разработки и внутренние заметки ревью.
2. **Проверка** каждого оставшегося файла. Любая находка останавливает экспорт с таблицей (строка на файл и
   правило, первые `--limit` строк); все строки — в `<work>/out/public/satk-gta-<version>.audit.json`.
   `--check-only` на этом заканчивает.

   | Правило | Что запрещено |
   |---|---|
   | `machine-path` | в любом текстовом файле: папки машины разработки (папки `Games` и `files` её диска с данными, в том числе в записи Git Bash) и папки учётных записей Windows с конкретным именем (`Public`, `Default` и заглушки вроде `<you>` разрешены) |
   | `plan-ref` | в файлах для людей (README, CLAUDE.md, NOTICE.md, `docs/`, `packaging/`, `proto/`, `.claude-plugin/`): ссылки на внутреннюю спецификацию и планы, номера рабочих пакетов и дорожек этапов |
   | `email` | адреса, кроме адреса коммитов сопровождающего, адресов no-reply и зарезервированных доменов-примеров; файлы лицензий и уведомлений могут называть своих авторов |
   | `secret` | ключи API и токены (ключи `sk-`, токены GitHub `ghp_`/`gho_`/`github_pat_`, ключи AWS, Slack и Google), блоки закрытых ключей, имя ключа маршрутизатора моделей |
   | `private-repo` | ссылки на другие репозитории сопровождающего (они закрыты) |
   | `asset` | то, что запрещает `satk dev assetguard` (сигнатуры ассетов игры, чанки RenderWare, картинки вне docs/img), с учётом `.assetguard-allow` |
   | `large-file` | файлы больше 5 МиБ |
   | `dangling-ref` | пользовательские документы, которые называют исключённый файл |

3. **Снимок** в `<work>/out/public/satk-gta-<version>/` (или `--out`) — git-репозиторий. Без `--base` это новый
   репозиторий с одним корневым коммитом в ветке `main`; с `--base` (клон или URL публичного репозитория) — его
   история плюс один коммит, который заменяет всё дерево. Коммит называется `satk <version> public preview`,
   автор — `user.name`/`user.email` репозитория разработки, дата — дата выгружаемого коммита, плюс аннотированный
   тег `v<version>`. Затем blob-ы и режимы сравниваются с выгружаемым коммитом: публичное дерево побайтно равно
   коммиту разработки без исключённых файлов. Уже существующий тег с другими файлами — отказ (поднимите версию);
   повторный экспорт тех же файлов ничего не меняет. Папку, в которой лежит что-то кроме прежнего снимка, команда
   не заменяет.
4. **Тесты** самого снимка: `pytest -m "not game"` с `PYTHONPATH=<snapshot>/src`, пустой папкой `SATK_CONFIG`,
   пустым `SATK_HOME`, выключенным поиском инструментов и без других переменных `SATK_*`, так что рабочее
   пространство разработки не видно. Как у свежего клона после настройки, у снимка появляется `.venv` (после
   прогона удаляется), который видит пакеты venv разработки. Вывод — в
   `<work>/tmp/export-public/tests/pytest.log`.

```powershell
satk dev export-public --check-only
satk dev export-public --base https://github.com/qxlies/satk-gta.git
satk dev release --public --repo <work>/out/public/satk-gta-<version>
```

Ничего не отправляется и не публикуется: после зелёного экспорта отправьте ветку и тег снимка сами и приложите
файлы `satk dev release --public` к релизу на GitHub.

Каждый `satk dev release` применяет тот же список исключений (исключённый файл никогда не попадает в zip, колесо
или sdist) и перечитывает собранные архивы, чтобы это проверить. С `--public` он сначала проверяет коммит и
дополнительно отказывает при любом пути машины разработки внутри собранных файлов (собственные файлы satk — все
правила `machine-path`; сторонние файлы в комплекте — только папки и учётная запись самой машины разработки).
`satk dev sync-agent-docs` пропускает CLAUDE.md рабочего пространства, если в checkout его нет, как в публичном
снимке.
