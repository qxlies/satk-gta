# satk release check inside Windows Sandbox (a clean Windows without Python or satk).
# Unpacks the release zip into C:\Users\Public\<Cyrillic> satk (Cyrillic letters and a space), then runs the
# first steps of a new user on the read-only game copy and writes result.json into the results folder.
# Written for Windows PowerShell 5.1; ASCII only (the folder name is built from character codes).
param(
    [string]$Release = 'C:\satk-release',
    [string]$Game = 'C:\game',
    [string]$Results = 'C:\satk-results'
)
$ErrorActionPreference = 'Stop'
$started = Get-Date
$steps = New-Object System.Collections.ArrayList

function Invoke-Step([string]$Name, [scriptblock]$Body) {
    $t = Get-Date
    $ok = $true
    try { $msg = & $Body } catch { $ok = $false; $msg = $_.Exception.Message }
    $msg = ('' + $msg)
    if ($msg.Length -gt 300) { $msg = $msg.Substring(0, 300) }
    [void]$script:steps.Add([ordered]@{ step = $Name; ok = $ok; seconds = [math]::Round(((Get-Date) - $t).TotalSeconds, 1); summary = $msg })
    return $ok
}

function Invoke-Satk([string[]]$Arguments) {
    $out = & $script:satk @Arguments --json 2>$null
    $last = ($out | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1)
    if (-not $last) { throw "no JSON answer (exit $LASTEXITCODE)" }
    $res = $last | ConvertFrom-Json
    if (-not $res.ok) { throw ("" + $res.error.code + ": " + $res.error.msg) }
    return $res
}

$folder = (-join [char[]](0x0422, 0x0435, 0x0441, 0x0442)) + ' satk'
$dest = Join-Path $env:PUBLIC $folder
$zip = Get-ChildItem -LiteralPath $Release -Filter 'satk-*-win64.zip' | Select-Object -First 1
$script:satk = $null
$script:appControl = $null

$ok = Invoke-Step 'unpack' {
    if (-not $zip) { throw "no satk-*-win64.zip in $Release" }
    # .NET instead of Expand-Archive: the Archive module does not load in the SYSTEM context of wsb exec
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
    [IO.Compression.ZipFile]::ExtractToDirectory($zip.FullName, $dest)
    $root = Get-ChildItem -LiteralPath $dest -Directory | Select-Object -First 1
    $script:satk = Join-Path $root.FullName 'satk.cmd'
    $root.FullName
}
if ($ok) { $ok = Invoke-Step 'version' { $v = Invoke-Satk @('version'); "satk $($v.satk), Python $($v.python): $($v.exe)" } }
if ($ok) {
    $ok = Invoke-Step 'doctor' {
        $d = Invoke-Satk @('doctor')
        $bad = @($d.checks | Where-Object { $_.status -eq 'fail' } | ForEach-Object { $_.name })
        $script:appControl = ($d.checks | Where-Object { $_.name -eq 'app_control' } | Select-Object -First 1).msg
        "ok=$($d.counts.ok) warn=$($d.counts.warn) fail=$($d.counts.fail) $($bad -join ',')"
    }
}
$first = Get-Date
$script:firstDone = $null
if ($ok) { $ok = Invoke-Step 'init' { $i = Invoke-Satk @('init', '--game', $Game, '--yes'); "profile $($i.profile)" } }
if ($ok) { $ok = Invoke-Step 'index-build' { $b = Invoke-Satk @('index', 'build'); "dff=$($b.counts.dff) txd=$($b.counts.txd) inst=$($b.counts.inst)" } }
if ($ok) {
    $ok = Invoke-Step 'first-result' {
        $f = Invoke-Satk @('asset', 'find', 'grove', '--limit', '3')
        if (-not $f.rows) { throw 'no rows' }
        "$(@($f.rows).Count) rows, first $($f.rows[0][0])"
    }
    $script:firstDone = Get-Date
}
# The MCP server needs compiled extensions (pydantic-core); Smart App Control may block them (unsigned).
$mcpOk = $false
if ($ok) { $mcpOk = Invoke-Step 'mcp-selftest' { $m = Invoke-Satk @('mcp', 'selftest'); "tools=$($m.tools) list_bytes=$($m.list_bytes)" } }
$sac = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState

$result = [ordered]@{
    ok = $ok
    mcp_ok = $mcpOk
    smart_app_control = $sac
    app_control = $script:appControl
    total_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    first_result_seconds = $(if ($script:firstDone) { [math]::Round(($script:firstDone - $first).TotalSeconds, 1) } else { $null })
    windows = [Environment]::OSVersion.VersionString
    steps = $steps
}
New-Item -ItemType Directory -Force $Results | Out-Null
$json = $result | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText((Join-Path $Results 'result.json'), $json, (New-Object Text.UTF8Encoding $false))
