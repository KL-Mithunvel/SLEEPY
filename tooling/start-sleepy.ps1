# Launched by the "Sleepy" desktop shortcut.
# Starts backend+frontend+worker hidden (via run-backend.bat), waits for the
# Vite dev server to come up, then opens it in the default browser. If Sleepy
# is already running, just opens the browser — no second instance is started.

$repoRoot = Split-Path -Parent $PSScriptRoot
$batPath = Join-Path $repoRoot "tooling\run-backend.bat"
$url = "http://localhost:5173"
$logPath = Join-Path $PSScriptRoot "sleepy.log"

function Test-Url([string]$u) {
    try {
        $resp = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 2
        return $resp.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (-not (Test-Url $url)) {
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$batPath`"" -WindowStyle Hidden -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $logPath -RedirectStandardError "$logPath.err"

    $maxWaitSeconds = 120
    $elapsed = 0
    while ($elapsed -lt $maxWaitSeconds -and -not (Test-Url $url)) {
        Start-Sleep -Seconds 2
        $elapsed += 2
    }
}

Start-Process $url
