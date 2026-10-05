@echo off
rem satk shim: python -X utf8 -m satk with PYTHONPATH=<this checkout>\src (docs/en/install.md).
rem Interpreter: %SATK_PYTHON% if set, else <this checkout>\.venv, else the main checkout's .venv (a git
rem worktree finds it via .git -> gitdir -> commondir, no git needed), else %SATK_HOME%\tools\.venv,
rem else "py -3.12", else "python". No machine-specific default paths.
setlocal
set "SATK_HERE=%~dp0"
set "SATK_PY=%SATK_PYTHON%"
if defined SATK_PY goto run
set "SATK_PY=%SATK_HERE%.venv\Scripts\python.exe"
if exist "%SATK_PY%" goto run
call :mainvenv
if defined SATK_PY if exist "%SATK_PY%" goto run
set "SATK_PY="
if defined SATK_HOME set "SATK_PY=%SATK_HOME%\tools\.venv\Scripts\python.exe"
if defined SATK_PY if exist "%SATK_PY%" goto run
set "SATK_PY="
:run
set "PYTHONUTF8=1"
set "PYTHONPATH=%SATK_HERE%src"
if not defined SATK_PY goto nopy
"%SATK_PY%" -X utf8 -m satk %*
exit /b %ERRORLEVEL%
:nopy
where py >nul 2>nul
if errorlevel 1 goto nolauncher
py -3.12 -X utf8 -m satk %*
exit /b %ERRORLEVEL%
:nolauncher
python -X utf8 -m satk %*
exit /b %ERRORLEVEL%

:mainvenv
rem In a worktree <checkout>\.git is a file "gitdir: <main>\.git\worktrees\<name>" and
rem <gitdir>\commondir names the shared <main>\.git (usually relative: ..\..).
set "SATK_PY="
if not exist "%SATK_HERE%.git" exit /b 0
if exist "%SATK_HERE%.git\" exit /b 0
set "SATK_GITDIR="
for /f "usebackq tokens=1,*" %%a in ("%SATK_HERE%.git") do if /i "%%a"=="gitdir:" set "SATK_GITDIR=%%b"
if not defined SATK_GITDIR exit /b 0
set "SATK_GITDIR=%SATK_GITDIR:/=\%"
if not "%SATK_GITDIR:~1,1%"==":" if not "%SATK_GITDIR:~0,2%"=="\\" set "SATK_GITDIR=%SATK_HERE%%SATK_GITDIR%"
set "SATK_COMMON="
if exist "%SATK_GITDIR%\commondir" for /f "usebackq delims=" %%c in ("%SATK_GITDIR%\commondir") do set "SATK_COMMON=%%c"
if not defined SATK_COMMON exit /b 0
set "SATK_COMMON=%SATK_COMMON:/=\%"
if not "%SATK_COMMON:~1,1%"==":" if not "%SATK_COMMON:~0,2%"=="\\" set "SATK_COMMON=%SATK_GITDIR%\%SATK_COMMON%"
for %%m in ("%SATK_COMMON%\..") do set "SATK_PY=%%~fm\.venv\Scripts\python.exe"
exit /b 0
