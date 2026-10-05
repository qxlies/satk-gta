@echo off
rem satk MCP server (stdio) for AI clients. "satk mcp config" prints ready-made client settings;
rem they call python\python.exe directly, this file is for clients that prefer a single command.
"%~dp0python\python.exe" -s -X utf8 -m satk.mcp %*
exit /b %ERRORLEVEL%
