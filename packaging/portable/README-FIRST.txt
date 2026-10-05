satk - San Andreas ToolKit (portable build for Windows 10/11, 64-bit)
=====================================================================

satk indexes your GTA San Andreas (PC) and lets you, or an AI assistant, find and inspect
models, textures, map placements and crash addresses. Nothing to install, no admin rights,
no internet needed. The game is only read; satk writes only inside this folder.
Russian version of this file: README-FIRST.ru.txt

1. Unblock and unpack
   Files downloaded from the internet are marked by Windows, which then asks before every
   start. Before unpacking: right-click the zip > Properties > tick "Unblock" > OK.
   Unpack into your own folder, e.g. D:\satk (any letters and spaces are fine).
   Not into the game folder, not into Program Files.

2. Start
   Double-click "Start satk.cmd". The first time it runs "satk init", which looks for
   your game folder (registry, Steam, usual places). If it is not found:
       satk init --game "C:\Games\GTA San Andreas"

3. Index the game (about a minute) and search
       satk index build
       satk asset find grove
       satk help

4. Connect an AI assistant (MCP)
       satk mcp config
   prints the server settings; satk-mcp.cmd starts the server for clients that want one command.

5. Problems
       satk doctor
   checks the setup and says how to fix each problem.

Documentation: docs\en\ (English), docs\ru\ (Russian). History: CHANGELOG.md.
Home page, new versions and issues: https://github.com/qxlies/satk-gta
(satk bug-report writes a redacted local report you can attach to an issue).
Check the download: SHA256SUMS.txt next to the zip; in PowerShell: Get-FileHash <zip>.
Update: unpack the new version into a new folder and move work\ and satk.toml into it.
Remove: delete this folder.
Licenses: LICENSE (satk, MIT), NOTICE.md (third-party code), python\LICENSE.txt (Python),
blender\satk_blender\LICENSE (Blender add-on, GPL-3.0-or-later).
