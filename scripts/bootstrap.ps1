<#
.SYNOPSIS
  Set up the satk Python environment. Windows PowerShell 5.1+. Python 3.12, 3.13 or 3.14.

.DESCRIPTION
  1. Creates the venv if it is missing: <checkout>\.venv, or -Venv <dir> (local, no network).
     Python: -PythonVersion 3.12|3.13|3.14, else the first of 3.12, 3.13, 3.14 that the "py" launcher
     (or "python" on PATH) has. In a git worktree no venv is created: the shims and this script use the
     main checkout's .venv, found through .git -> gitdir -> commondir (works from any folder).
  2. -Deps: pip-installs what satk uses at run time (pyproject.toml extras img, num, mcp: Pillow, numpy,
     mcp), without pytest (network; the user's consent).
     -Dev: the same plus the development tools (extra dev: pytest), the git pre-commit hook and, in the
     main checkout's 3.12 .venv, a rewritten requirements.lock.
     -FromLock: exact versions from requirements.lock (-Dev: the whole lock; -Deps: the run-time
     packages constrained by the lock).
     PIP_CACHE_DIR=<workspace>\work\cache\pip, TEMP/TMP=<workspace>\work\tmp\bootstrap
     (or -TempDir <dir>). <workspace> = %SATK_HOME%, else the parent of a "tools" checkout that
     has work\ or satk.toml next to it, else %LOCALAPPDATA%\satk (docs/en/install.md).
  3. -Dev: git config core.hooksPath scripts/hooks (pre-commit -> satk dev assetguard --staged);
     skipped with -NoHooks or when the folder is not a git checkout.
  4. Prints "satk version". With -Venv it also prints the SATK_PYTHON line that makes satk.cmd use it
     (for example to run "satk dev gate" on Python 3.13).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File <checkout>\scripts\bootstrap.ps1 -Deps
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File <checkout>\scripts\bootstrap.ps1 -Dev
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File <checkout>\scripts\bootstrap.ps1 -Dev -FromLock -PythonVersion 3.13 -Venv <workspace>\work\venv313
#>
[CmdletBinding()]
param(
    [switch]$Deps,
    [switch]$Dev,
    [switch]$FromLock,
    [switch]$NoHooks,
    [string]$PythonVersion = "",
    [string]$Venv = "",
    [string]$TempDir = ""
)

$ErrorActionPreference = "Stop"
$supported = @("3.12", "3.13", "3.14")
$repo = Split-Path -Parent $PSScriptRoot
$isWorktree = Test-Path -PathType Leaf (Join-Path $repo ".git")
if ($PythonVersion -and ($supported -notcontains $PythonVersion)) {
    throw "Python $PythonVersion is not supported; use one of $($supported -join ', ')"
}

function Get-MainCheckout([string]$checkout) {
    # .git file "gitdir: <main>/.git/worktrees/<name>"; <gitdir>/commondir -> <main>/.git (no git needed)
    $g = Join-Path $checkout ".git"
    if (Test-Path -PathType Container $g) { return $checkout }
    if (-not (Test-Path -PathType Leaf $g)) { return $null }
    $line = Get-Content -LiteralPath $g -TotalCount 1
    if ($line -notmatch '^gitdir:\s*(.+)$') { return $null }
    $gitdir = $Matches[1].Trim()
    if (-not [System.IO.Path]::IsPathRooted($gitdir)) { $gitdir = Join-Path $checkout $gitdir }
    $cd = Join-Path $gitdir "commondir"
    if (-not (Test-Path -PathType Leaf $cd)) { return $null }
    $common = (Get-Content -LiteralPath $cd -TotalCount 1).Trim()
    if (-not [System.IO.Path]::IsPathRooted($common)) { $common = Join-Path $gitdir $common }
    return [System.IO.Path]::GetFullPath((Join-Path $common ".."))
}

function Find-BasePython([string]$want) {
    # -> @(exe, args) of a Python 3.12-3.14: the "py" launcher first, then "python" on PATH
    $versions = if ($want) { @($want) } else { $supported }
    $ErrorActionPreference = "Continue"  # a missing version writes to stderr; that is not an error here
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($v in $versions) {
            & py "-$v" -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) { return @("py", "-$v") }
        }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $v = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and ($versions -contains "$v")) { return @("python") }
    }
    throw "no Python $($versions -join ' / ') found (py launcher or python on PATH); install it from python.org"
}

$main = Get-MainCheckout $repo
$workspace = $env:SATK_HOME
if (-not $workspace) {
    foreach ($co in @($repo, $main)) {
        if ($co -and ((Split-Path -Leaf $co) -ieq "tools")) {
            $parent = Split-Path -Parent $co
            if ((Test-Path (Join-Path $parent "work")) -or (Test-Path (Join-Path $parent "satk.toml"))) {
                $workspace = $parent
                break
            }
        }
    }
}
if (-not $workspace) { $workspace = Join-Path $env:LOCALAPPDATA "satk" }
Write-Host "workspace $workspace"

$env:PYTHONUTF8 = "1"
$tmpDir = $TempDir
if (-not $tmpDir) { $tmpDir = Join-Path $workspace "work\tmp\bootstrap" }
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
$env:TEMP = $tmpDir
$env:TMP = $tmpDir
$env:PIP_CACHE_DIR = Join-Path $workspace "work\cache\pip"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"

function Invoke-Checked([string]$what, [scriptblock]$block) {
    & $block
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

# 1. venv
$ownVenv = [bool]$Venv
if ($ownVenv) {
    $venv = [System.IO.Path]::GetFullPath($Venv)
} else {
    $venv = Join-Path $repo ".venv"
    if ($isWorktree -and -not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
        if (-not $main) { throw "worktree: cannot find the main checkout through .git/commondir" }
        $venv = Join-Path $main ".venv"
        Write-Host "worktree: using the shared venv $venv"
    }
}
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    if ($isWorktree -and -not $ownVenv) {
        throw "shared venv not found: $py (run bootstrap.ps1 in the main checkout $main first, or pass -Venv <dir>)"
    }
    $base = Find-BasePython $PythonVersion
    $baseArgs = @()
    if ($base.Count -gt 1) { $baseArgs = $base[1..($base.Count - 1)] }
    Write-Host "creating venv $venv ($($base -join ' '))"
    Invoke-Checked "venv" { & $base[0] @baseArgs -m venv $venv }
}
$ver = & $py -c "import platform; print(platform.python_version())"
$minor = ($ver -split "\.")[0..1] -join "."
if ($supported -notcontains $minor) { throw "venv python is $ver; satk needs $($supported -join ', ')" }
if ($PythonVersion -and $minor -ne $PythonVersion) {
    throw "venv python is $ver, expected $PythonVersion.x (remove $venv or pass another -Venv)"
}
Write-Host "python $ver at $py"

# 2. dependencies (network)
$lock = Join-Path $repo "requirements.lock"
$pyproject = Join-Path $repo "pyproject.toml"
$extras = @("img", "num", "mcp")
if ($Dev) { $extras += "dev" }
if ($Deps -or $Dev) {
    $code = "import sys, tomllib; d = tomllib.load(open(sys.argv[1], 'rb'))['project']['optional-dependencies'];" +
            " print(' '.join(p for e in sys.argv[2:] for p in d.get(e, [])))"
    $pins = (& $py -c $code $pyproject @extras).Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries)
    if ($FromLock) {
        if (-not (Test-Path $lock)) { throw "requirements.lock not found" }
        if ($Dev) {
            Invoke-Checked "pip install -r requirements.lock" { & $py -m pip install -r $lock }
        } else {
            Write-Host ("pip install -c requirements.lock " + ($pins -join " "))
            Invoke-Checked "pip install" { & $py -m pip install -c $lock @pins }
        }
    } else {
        Write-Host ("pip install " + ($pins -join " "))
        Invoke-Checked "pip install" { & $py -m pip install @pins }
    }
    if (-not $Dev) {
        Write-Host "run-time dependencies only (no pytest); development tools: -Dev"
    } elseif ($isWorktree -or $ownVenv -or $minor -ne "3.12") {
        Write-Host "requirements.lock not rewritten (it records the main checkout's 3.12 .venv)"
    } else {
        Invoke-Checked "deps.py lock" { & $py (Join-Path $PSScriptRoot "deps.py") lock $lock }
    }
}

# 3. git hooks (development only; repository-local setting shared by all worktrees)
if ($Dev -and -not $NoHooks) {
    if (-not (Test-Path (Join-Path $repo ".git"))) {
        Write-Host "git hooks: skipped ($repo is not a git checkout)"
    } else {
        Invoke-Checked "git config core.hooksPath" { & git -C $repo config core.hooksPath scripts/hooks }
        Write-Host "git hooks: core.hooksPath = scripts/hooks"
    }
}

# 4. smoke test
$env:PYTHONPATH = Join-Path $repo "src"
Invoke-Checked "satk version" { & $py -X utf8 -m satk version --json }
if ($ownVenv) { Write-Host "to run satk with this venv: set SATK_PYTHON=$py" }
