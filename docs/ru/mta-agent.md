# MTA-агент: «глаза» в настоящей игре (ресурс `satk-agent`)

[English version](../en/mta-agent.md)

Пакет: `satk.viewer.backends.mta_lua` и Lua-ресурс `mta-resources/satk-agent`. Протокол: SAAP/1, цель `game`.

## Что это

Цель `game` для `satk view`: собранный форк MTA ([engine.md](engine.md)) с Lua-ресурсом `satk-agent` на приватном
сервере только на `127.0.0.1`. Сервер отдаёт HTTP-экспорт `rpc` с токеном; клиент MTA ставит камеру
(`setCameraMatrix`), снимает кадр (`dxCreateScreenSource` → `dxGetTexturePixels` → PNG), пикает мир
(`processLineOfSight` с информацией о здании → `model_pos` → SID индекса), меняет время и погоду, отдаёт лог и
исполняет Lua на обеих сторонах. Ariane — «быстрые глаза», `game` — истина. Пишет только в `work\mta\server\`
(копия сервера, логи) и `work\out\captures\` (кадры). Сценарные проверки мода в этой игре —
[ingame.md](ingame.md); проверки скриптов ресурса MTA — [mta.md](mta.md).

## Быстрый пример

```powershell
satk view status --target game
```

Что вернётся без запущенного сервера:

```json
{"ok":true,"target":"game","up":false}
```

С сервером и клиентом (на эталонной машине клиент пока не стартует — см. «Первый запуск клиента»):

<!-- docs-smoke: skip поднимает сервер MTA и клиент игры -->
```powershell
# в checkout satk (папка tools)
$env:PYTHONPATH = "$PWD\src"
.venv\Scripts\python.exe -X utf8 -m satk.viewer.backends.mta_lua up --client
satk view conformance --target game --files proto/conformance/core.jsonl proto/conformance/camera.jsonl proto/conformance/capture.jsonl --show-all
satk view capture --target game --pos 2495,-1720,60 --look 2495,-1670,15 --fov 70
satk view pick 640 360 --target game
satk view stop --target game
```

## Команды

| Команда | MCP | Что делает |
|---|---|---|
| `python -m satk.viewer.backends.mta_lua up [--client] [--timeout S] [--conf ключ=значение ...] [--cvar ключ=значение ...] [--windowed]` | — | приватный сервер в `work\mta\server` (порты свободные, только `127.0.0.1`), ждёт `ping` ресурса, пишет `work\run\endpoints\game.json` и `work\run\sessions\game.json`; `--client` — ещё и клиент; `--conf`, `--cvar`, `--windowed`: см. «Настройки теста» в [ingame.md](ingame.md) |
| `python -m satk.viewer.backends.mta_lua client [--force]` | — | клиент форка `mtasa://127.0.0.1:<port>`; без `--force` сначала `preflight`; при диалоге ошибки Windows процесс снимается, текст диалога попадает в ошибку |
| `python -m satk.viewer.backends.mta_lua preflight` | — | только чтение: ключ реестра `HKLM\…\Multi Theft Auto: San Andreas All`, `%ProgramData%\MTA San Andreas All`, не запущен ли уже `gta_sa.exe` |
| `python -m satk.viewer.backends.mta_lua status` / `down` | — | состояние / `quit` ресурсу, закрыть клиент, убрать файлы обнаружения |
| `satk view status\|goto\|capture\|pick\|set\|stop --target game` | `view_*` | как у Ariane; `view set` — только время и погода |
| `satk view conformance --target game [--files …]` | — | тесты SAAP/1; приёмка — файлы `core`, `camera`, `capture` |

`satk view start --target game` пока отвечает `UNSUPPORTED`: цель `game` запускается командой выше.

## Как это устроено

**Протокол `mta-lua/1`.** `POST http://127.0.0.1:<httpport>/satk-agent/call/rpc`, тело
`[{"token","id","method","params"}]` (MTA передаёт экспорту JSON-массив аргументов), ответ
`[{"ok":true,"result":…}]` или `[{"ok":false,"error":{code,message,data}}]` с кодами SAAP/1. Методы клиента (камера,
кадр, пик, `world.settle`, `lua.exec side=client`) сервер передаёт агенту событием и отвечает
`{"ok":true,"pending":"<rid>"}`; бэкенд опрашивает `_poll` каждые 10–50 мс. Методы сервера (`hello`, `ping`,
`status` без клиента, `env.*`, `log.poll`, `console.exec`, `lua.exec side=server`, `quit`) отвечают сразу.

**Безопасность.** Сервер слушает только `127.0.0.1` (`serverip`, `--ip`), без ASE и рассылки по LAN; Lua отказывает
не-loopback адресам и неверному токену (64 hex, `secrets.token_hex`). Токен лежит в
`work\mta\server\mods\deathmatch\settings.xml` (`@satk-agent.token`, приватная настройка ресурса) и в
`work\run\sessions\game.json`. Свой ACL (`satk-acl.xml`): гостю — только `resource.satk-agent.http`, ресурсу —
`function.shutdown`. `lua.exec` — исполнение кода по замыслу: только локальная разработка.

**Сервер.** `MTA Server64.exe` и `x64\*.dll` копируются из `engine\mtasa\Bin\server` в `work\mta\server` (повторно —
только изменённые), конфиг `satk-agent.conf` строится из `mtaserver.conf` исходников форка (копия в `Bin/server` — только если исходного нет), ресурс копируется из
`mta-resources/satk-agent`. Старт с `--child-process` и событием готовности; готовность — ответ на `ping` (около
1,1 с на эталонной машине). Логи — `work\mta\server\mods\deathmatch\logs\satk-agent*.log`.

**Камера и FOV.** SAAP задаёт горизонтальный FOV кадра. Клиент ставит камеру, через 2 кадра меряет реальные
полутангенсы экрана (`getWorldFromScreenPosition` на краях) и, если нужно, переставляет `fov` MTA так, чтобы
горизонтальный FOV совпал (допуск 0,01°). `camera.get` возвращает измеренный FOV.

**Кадр.** Вид камеры → `enginePreloadWorldArea` → скрыть HUD и чат → ждать `min(max_frames, 30)` кадров и не меньше
300 мс → в `onClientRender` `dxUpdateScreenSource` → `dxGetTexturePixels` → `dxConvertPixels("png")` → латентное
событие (20 МБ/с) → файл в ресурсе → бэкенд переносит его в `path_prefix.png` и считает `sha256`. Размер, отличный
от окна (`capture.size`): центральная вырезка с пропорциями кадра, растянутая через render target; FOV камеры
пересчитывается так, чтобы у вырезки был запрошенный FOV. Камера, HUD, чат и время восстанавливаются после кадра
(кадр без побочных эффектов, как у mock).

**Пик.** Окно: луч через центр пикселя (`getWorldFromScreenPosition`) →
`processLineOfSight(…, includeWorldModelInformation=true)`, игрок агента игнорируется. Кадр (`space=capture`): лучи
считает бэкенд по той же модели камеры, клиент только трассирует (с `enginePreloadWorldArea` для коллизий у камеры).
Здание → `{"kind":"building","model_id","pos","src":{"kind":"model_pos"}}`, `satk view pick` превращает его в
SID `inst:` через индекс (допуск 0,05 м); элемент MTA → `el:<тип>/<id>`.

**Тесты.** `tests/mta`: скрипты компилируются и исполняются Lua 5.1 самого форка
(`engine\mtasa\Bin\server\x64\lua5.1.dll` через ctypes) в симуляции MTA (`tests/mta/sim.lua`: события, латентные
события, камера с другим масштабом FOV и пропорциями, мир с плоскостью и зданием). Полный путь бэкенд → HTTP →
`server.lua` → `client.lua` → PNG проходит группы conformance
`core`/`camera`/`capture`/`pick`/`raycast`/`env`/`world`/`log`/`console`/`lua` (26 PASS, 0 FAIL, 2 пропуска:
`capture.ids`, `capture.depth`); живой сервер — `SATK_TEST_LIVE=1`.

## Первый запуск клиента: что блокирует

Проверено 2026-10-04 на эталонной машине (стандартный пользователь, UAC включён, `ConsentPromptBehaviorAdmin=5`).
Загрузчик MTA (`Multi Theft Auto.exe`, манифест `asInvoker`, поэтому без виртуализации реестра) хранит всё в
`HKLM\SOFTWARE\WOW6432Node\Multi Theft Auto: San Andreas All`; ключ и ACL «Users: запись» создаёт NSIS-установщик
MTA, который здесь не запускался (официальную MTA рядом с форком ставить нельзя).

- `preflight` (только чтение) на машине, где MTA никогда не ставили, находит до 4 блокеров: нет
  `Common\GTA:SA Path`; нет ключа `HKLM\…\Multi Theft Auto: San Andreas All` (запись `Last Run Location` при каждом
  старте не пройдёт); нет `%ProgramData%\MTA San Andreas All\Common` и `\1.7`.
- Принудительный запуск (`client --force`, один раз): через 4 с окно **«Error [U01]: Multi Theft Auto has not been
  installed properly, please reinstall.»** (`GetMTASABaseDir`: `Last Run Location` не записался и не читается,
  `Shared/sdk/SharedUtil.Misc.hpp:550`). Процесс снят автоматически, других процессов не осталось. UAC не появлялся.
  Сервер и `satk view conformance` при этом работали: `core` 5/5 PASS, `camera.bad_params`,
  `capture.unsupported_layer`, `capture.bad_params` PASS, остальные 7 случаев — `NOT_READY: no game client has
  joined the agent server`, 2 пропуска (`capture.ids`, `capture.depth`).

Что записал этот запуск (снимки до и после): каталог `%ProgramData%\MTA San Andreas All\1.7\` с файлом `report.log`
(461 байт, 5 строк журнала загрузчика) и пустые каталоги `engine\mtasa\Bin\MTA\dumps\private`. В реестре <!-- linkcheck: ignore -->
(`HKLM` 32/64, `HKCU\Software\Multi Theft Auto…`, `HKCU\…\VirtualStore`) — ничего; чистая копия не менялась. <!-- linkcheck: ignore -->
Откат (PowerShell в папке рабочего пространства, без прав администратора):

<!-- docs-smoke: skip удаление файлов вне work -->
```powershell
Remove-Item -Recurse -Force "$env:ProgramData\MTA San Andreas All"
Remove-Item -Recurse -Force .\engine\mtasa\Bin\MTA\dumps
```

Чтобы клиент запускался, нужна однократная настройка **от администратора** — то же, что делает установщик MTA, плюс
значение `MaxLoaderThreads`, которое загрузчик иначе пишет сам и ради него просит UAC («Update compatibility
settings», `CInstallManager::_ProcessAppCompatChecks`). Выполняет пользователь сам в 64-битном PowerShell «от имени
администратора», в папке рабочего пространства (агент не повышает права и не меняет ACL):

<!-- docs-smoke: skip требует прав администратора -->
```powershell
$ws = (Get-Location).Path   # папка рабочего пространства
$users = New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-545'
foreach ($k in 'HKLM:\SOFTWARE\WOW6432Node\Multi Theft Auto: San Andreas All', 'HKLM:\SOFTWARE\Multi Theft Auto: San Andreas All') {
  New-Item -Path "$k\Common", "$k\1.7" -Force | Out-Null
  $acl = Get-Acl $k
  $acl.AddAccessRule((New-Object Security.AccessControl.RegistryAccessRule($users, 'FullControl', 'ContainerInherit', 'None', 'Allow')))
  Set-Acl $k $acl
}
Set-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Multi Theft Auto: San Andreas All\Common' 'GTA:SA Path' "$ws\gta-sa-clean"
$ifeo = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\gta_sa.exe'
New-Item -Path $ifeo -Force | Out-Null
Set-ItemProperty $ifeo MaxLoaderThreads 1 -Type DWord
New-Item -ItemType Directory -Force "$env:ProgramData\MTA San Andreas All\Common", "$env:ProgramData\MTA San Andreas All\1.7" | Out-Null
icacls "$env:ProgramData\MTA San Andreas All" /grant '*S-1-5-32-545:(OI)(CI)M'
```

Откат настройки (тоже от администратора; ключа `Image File Execution Options\gta_sa.exe` до неё не было):

<!-- docs-smoke: skip требует прав администратора -->
```powershell
Remove-Item -Recurse -Force 'HKLM:\SOFTWARE\WOW6432Node\Multi Theft Auto: San Andreas All', 'HKLM:\SOFTWARE\Multi Theft Auto: San Andreas All'
Remove-Item -Recurse -Force 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\gta_sa.exe'
Remove-Item -Recurse -Force "$env:ProgramData\MTA San Andreas All"
```

После неё клиент (по коду загрузчика; запуском не проверено) пишет при каждом старте: значения в этих ключах
(`Last Run Location`, `Settings\…`), `%ProgramData%\MTA San Andreas All\{Common,1.7}` (журналы, кэш), <!-- linkcheck: ignore -->
`engine\mtasa\Bin\MTA` (`config\coreconfig.xml`, `logs`, `dumps`, копия exe для запуска) и, если там уже есть <!-- linkcheck: ignore -->
устаревшие флаги, `AppCompatFlags\Layers` для `gta_sa.exe` (откат — удалить эти значения). Дальше возможны шаги, <!-- linkcheck: ignore -->
которые здесь не дошли до проверки: диалоги первого запуска MTA и выдача serial (`netc.dll`). Проверить
готовность: `python -m satk.viewer.backends.mta_lua preflight` (`"ready": true`). Альтернатива без администратора —
режим разработчика в форке (пути из окружения вместо `HKLM`); это запланированная задача движка.

## Ограничения и известные проблемы

- Один клиент-агент (первый, приславший `satk:hello`); игрок агента невидим, заморожен, без коллизий.
- `world.settle` — ожидание кадров после `enginePreloadWorldArea`: очередь стриминга из Lua не видна, `pending`
  всегда 0 (предупреждение в ответе). Для честного предиката нужен хук в форке (запланирован).
- Нет `capture.ids`/`capture.depth`, `entity.*`, `view.set`, `asset.render` (`UNSUPPORTED`).
- `capture.size` больше окна — растяжение (мыльно); ширина и высота кадра берутся из окна клиента.
- Свёрнутое окно MTA не рендерит: запросы клиента заканчиваются `TIMEOUT` с подсказкой.
- `dxGetTexturePixels` отдаёт заглушку 32×32, если в настройках MTA выключено «Allow screen upload» — `NOT_READY`.
- `lua.exec` ограничен таймаутом самого MTA («Aborting; infinite running script»); `resource` — только `satk-agent`.
- `console.exec` исполняет только командные обработчики скриптов сервера (`executeCommandHandler`).

## Python-API

```python
from satk.viewer.backends import get_backend           # цель "game" -> MtaLuaBackend
from satk.viewer.backends import mta_lua
mta_lua.start_server(); mta_lua.client_preflight(); mta_lua.start_client(); mta_lua.stop()
b = get_backend("game"); b.call("camera.set", {"pose": {"pos": [2495, -1720, 60], "look": [2495, -1670, 15]}})
```
