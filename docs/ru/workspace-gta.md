# Полное рабочее пространство разработчика — частный случай общей установки

[English version](../en/workspace-gta.md)

<!-- Как satk находит эту раскладку без единого пути в коде. Сверено 2026-10-05. -->

## Что это

Разработчики satk работают в одной папке — *рабочем пространстве*, где клон satk лежит рядом с копиями игры,
клонами-донорами исходников и необязательными форками. Некоторые страницы показывают ответы из такого рабочего
пространства и пишут его пути как `<workspace>\…`. Эта раскладка не требование satk, а один конкретный случай;
общая установка описана в [install.md](install.md).

| Папка | Ключ конфигурации | Профиль |
|---|---|---|
| `<workspace>` | `paths.workspace` | — |
| `<workspace>\tools` | клон репозитория (`satk.cmd`, `.venv`) | — |
| `<workspace>\gta-sa-clean` | `paths.game` (чистая копия 1.0 US) | `vanilla` |
| `<workspace>\GTA San Andreas` | `paths.installed` = `paths.game_root` (установленная игра со своими модами) | `installed`, `samp`, `game` |
| `<workspace>\work` | `paths.work` | — |
| `<workspace>\src` | `paths.src` (клоны-доноры, только чтение) | — |
| `<workspace>\viewer\ariane` | форк Ariane (необязателен, собирается локально) | — |
| `<workspace>\engine\mtasa` | форк MTA (необязателен) | — |

## Быстрый пример

```powershell
satk config show --section paths
satk doctor --only paths
```

`satk config show` печатает пути и `"sources":["defaults"]`: ни `satk.toml`, ни переменные `SATK_*` не
участвуют. `satk doctor --only paths` пишет `workspace <workspace> (checkout)` при запуске из `<workspace>\tools`
и `(main-checkout)` из worktree, затем рабочую папку, игру и число защищённых корней. Откуда взялись Blender и
MSBuild, показывают проверки `blender` (`detect:program_files`) и `msbuild` (`detect:vswhere`):
`satk doctor --only paths,blender,msbuild`.

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `satk config show` | — | пути этой раскладки без всякого `satk.toml` |
| `satk init --dry-run` | — | кандидаты `GTA San Andreas` (hoodlum-nointro) и `gta-sa-clean` (hoodlum-stock) |

## Как это устроено

- Такому рабочему пространству `satk.toml` не нужен. Рабочее пространство выводится из расположения кода: клон
  называется `tools`, рядом с ним есть `work\` — значит, рабочее пространство — родительская папка `tools`.
  Worktree (`<workspace>\work\wt\<имя>`) находит основной клон через `.git` → `gitdir` → `commondir` и берёт то же
  рабочее пространство и тот же `tools\.venv`.
- Blender 5.1 и MSBuild из VS2026 в коде не записаны: их находят в `Program Files` и через `vswhere -prerelease`.
- `paths.game_root` по умолчанию равен `paths.installed`; `[index] default_profile` — `vanilla`.
- Жёсткий нижний предел защиты записи (`GTA San Andreas`, `src`, `gta-sa-clean` этого рабочего пространства) тоже
  выводится из расположения клона и конфигурацией не снимается.
