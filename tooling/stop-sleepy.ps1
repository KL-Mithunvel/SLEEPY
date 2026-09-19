# Stops everything started by the "Sleepy" shortcut (Flask/main.py, the Vite
# dev server, and the worker). Safe to run even if nothing is running.
#
# Asks politely first, kills only what refuses to go.
#
# This used to be a straight `Stop-Process -Force` over every process whose
# command line mentioned this repo. That is a hard TerminateProcess: no
# teardown runs, so a kill landing mid-commit left .git/index.lock behind in
# the corpus repo - and nothing ever clears that, so every later MD write
# failed with "Unable to create index.lock" until it was deleted by hand.
#
# Instead we drop a stop sentinel file, which main.py and worker.py both watch
# for, and let them shut down through their normal teardown path. Force is the
# fallback for anything still alive after the grace period.
#
# ASCII only, deliberately: Windows PowerShell 5.1 decodes a BOM-less file as
# ANSI, so a stray non-ASCII character here becomes a parser error at runtime.

$repoRoot = Split-Path -Parent $PSScriptRoot
$sentinel = Join-Path $repoRoot ".sleepy-stop"
$graceSeconds = 20

function Get-SleepyProcesses {
    $pattern = [regex]::Escape($repoRoot)
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe' OR Name='cmd.exe' OR Name='uv.exe'" |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match $pattern) }
}

$running = @(Get-SleepyProcesses)
if ($running.Count -eq 0) {
    Write-Host "Sleepy is not running."
    exit 0
}

Write-Host "Asking Sleepy to stop ($($running.Count) process(es))..."
Set-Content -Path $sentinel -Value "stop" -Encoding utf8

$elapsed = 0
while ($elapsed -lt $graceSeconds) {
    Start-Sleep -Seconds 1
    $elapsed += 1
    if (@(Get-SleepyProcesses).Count -eq 0) {
        Write-Host "Sleepy stopped cleanly after $elapsed second(s)."
        Remove-Item $sentinel -ErrorAction SilentlyContinue
        exit 0
    }
}

# Anything still here has ignored the sentinel - take it down the hard way.
$stubborn = @(Get-SleepyProcesses)
Write-Host "$($stubborn.Count) process(es) did not stop within $graceSeconds second(s) - forcing."
foreach ($p in $stubborn) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {}
}

Remove-Item $sentinel -ErrorAction SilentlyContinue

# A forced kill can have landed mid-commit. Clear the lock it may have left
# so the next start is not wedged before it begins (main.py sweeps this on
# startup too, but only for locks already older than its staleness window).
$indexLock = Join-Path $repoRoot "data\klm\.git\index.lock"
if (Test-Path $indexLock) {
    Write-Host "Removing git index.lock left by the forced kill."
    Remove-Item $indexLock -ErrorAction SilentlyContinue
}

Write-Host "Sleepy stopped."
