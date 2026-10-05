#!/bin/sh
# satk shim for sh/bash (Git Bash on Windows, Linux, macOS): python -X utf8 -m satk with
# PYTHONPATH=<this checkout>/src (docs/en/install.md). Interpreter: $SATK_PYTHON if set, else
# <checkout>/.venv, else the main checkout's .venv (a git worktree finds it via .git -> gitdir ->
# commondir, no git needed), else $SATK_HOME/tools/.venv, else py -3.12 / python3.12 / python3.
here=$(cd "$(dirname "$0")" && pwd)
abs() {  # absolute POSIX spelling of a path from a .git file (Windows "D:/x" too)
  p=$1
  if command -v cygpath >/dev/null 2>&1; then p=$(cygpath -u "$p"); fi
  case "$p" in /*|[A-Za-z]:*) ;; *) p="$2/$p" ;; esac
  printf '%s\n' "$p"
}
main=
if [ -f "$here/.git" ]; then
  gitdir=$(sed -n 's/^gitdir:[[:space:]]*//p' "$here/.git" | head -n 1 | tr -d '\r')
  if [ -n "$gitdir" ]; then
    gitdir=$(abs "$gitdir" "$here")
    if [ -f "$gitdir/commondir" ]; then
      common=$(abs "$(head -n 1 "$gitdir/commondir" | tr -d '\r')" "$gitdir")
      main=$(cd "$common/.." 2>/dev/null && pwd)
    fi
  fi
fi
py=${SATK_PYTHON:-}
pick() {
  for c in "$@"; do
    if [ -f "$c" ]; then py=$c; return 0; fi
  done
  return 1
}
if [ -z "$py" ]; then
  pick "$here/.venv/Scripts/python.exe" "$here/.venv/bin/python" ||
    { [ -n "$main" ] && pick "$main/.venv/Scripts/python.exe" "$main/.venv/bin/python"; } ||
    { [ -n "${SATK_HOME:-}" ] && pick "$SATK_HOME/tools/.venv/Scripts/python.exe" "$SATK_HOME/tools/.venv/bin/python"; } ||
    true
fi
src="$here/src"
if command -v cygpath >/dev/null 2>&1; then src=$(cygpath -w "$src"); fi
PYTHONUTF8=1
PYTHONPATH=$src
export PYTHONUTF8 PYTHONPATH
if [ -n "$py" ]; then exec "$py" -X utf8 -m satk "$@"; fi
if command -v py >/dev/null 2>&1; then exec py -3.12 -X utf8 -m satk "$@"; fi
if command -v python3.12 >/dev/null 2>&1; then exec python3.12 -X utf8 -m satk "$@"; fi
exec python3 -X utf8 -m satk "$@"
