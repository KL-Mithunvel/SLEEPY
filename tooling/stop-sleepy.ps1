# Stops everything started by the "Sleepy" shortcut (Flask/main.py, the Vite
# dev server, and the worker) by killing any process whose command line
# references this repo. Safe to run even if nothing is running.

$repoRoot = Split-Path -Parent $PSScriptRoot
$pattern = [regex]::Escape($repoRoot)

Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe' OR Name='cmd.exe' OR Name='uv.exe'" |
    Where-Object { $_.CommandLine -and ($_.CommandLine -match $pattern) } |
    ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}
    }
