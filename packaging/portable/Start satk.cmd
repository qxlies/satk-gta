@echo off
rem Double-click: opens a console in this folder where the "satk" command works.
rem The first time (no satk.toml yet) it runs "satk init" to find your game.
title satk
cd /d "%~dp0"
set "PATH=%~dp0;%PATH%"
call "%~dp0satk.cmd" version --table
if not exist "%~dp0satk.toml" (
  echo.
  echo First start: looking for your GTA San Andreas folder ^(satk init^)...
  call "%~dp0satk.cmd" init
)
echo.
echo Next steps:
echo   satk index build          index your game ^(about a minute^)
echo   satk asset find grove     search models and textures
echo   satk help                 all commands
echo   satk doctor               check the setup
echo Read README-FIRST.txt for more.
echo.
cmd /k
