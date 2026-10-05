@echo off
rem satk (portable): runs the bundled Python on src\satk. Usage: satk <command> [options], satk help.
rem -s: ignore the per-user site-packages; -X utf8: UTF-8 everywhere (paths with any letters work).
"%~dp0python\python.exe" -s -X utf8 -m satk %*
exit /b %ERRORLEVEL%
