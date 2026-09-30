# Pulls the private sleepy-corpus GitHub repo into a local backup the EC2 box
# can't reach. Stand-in for GitHub branch protection, which needs a paid plan
# for private repos: the box holds a WRITE deploy key for sleepy-corpus, so a
# compromised box could force-push or delete the offsite backup. This copy
# lives on the dev PC, out of the box's reach.
#
# Every run fetches into refs/remotes/origin/* and then pins what it saw with
# a dated tag. Tags are local and never updated by fetch, so a later
# force-push that rewrites or empties the GitHub copy can't touch earlier
# snapshots. If the new master is not a descendant of the last snapshot
# (history was rewritten), it logs a loud ALERT and still keeps everything.
#
# Run by hand:     powershell -NoProfile -ExecutionPolicy Bypass -File tooling\pull-corpus-backup.ps1
# Scheduled daily: see docs/RECOVERY.md ("Laptop copy of the offsite backup").
# Restore:         git clone <BackupDir> restored-corpus ; git -C restored-corpus checkout <snapshot tag>

param(
    [string]$RepoUrl   = "https://github.com/KL-Mithunvel/sleepy-corpus.git",
    [string]$Branch    = "master",
    [string]$BackupDir = "$env:USERPROFILE\Backups\sleepy-corpus.git"
)

$ErrorActionPreference = "Stop"
$logFile = Join-Path (Split-Path $BackupDir -Parent) "sleepy-corpus-backup.log"

function Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Output $line
    Add-Content -Path $logFile -Value $line -Encoding utf8
}

function Invoke-BackupGit([string[]]$gitArgs) {
    $out = & git.exe --git-dir="$BackupDir" @gitArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git $($gitArgs -join ' ') failed: $out" }
    return $out
}

New-Item -ItemType Directory -Force -Path (Split-Path $BackupDir -Parent) | Out-Null

try {
    if (-not (Test-Path $BackupDir)) {
        & git.exe init --bare --quiet "$BackupDir"
        if ($LASTEXITCODE -ne 0) { throw "git init failed" }
        Invoke-BackupGit @("remote", "add", "origin", $RepoUrl) | Out-Null
        # Deliberately NOT a --mirror clone: a mirror maps refs/* to refs/* and
        # would copy a remote wipe straight over the local refs.
        Invoke-BackupGit @("config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*") | Out-Null
        Log "created backup repo at $BackupDir"
    }

    Invoke-BackupGit @("fetch", "--quiet", "--no-tags", "origin") | Out-Null
    $new = (Invoke-BackupGit @("rev-parse", "refs/remotes/origin/$Branch")).Trim()

    $last = (& git.exe --git-dir="$BackupDir" for-each-ref --sort=-refname --count=1 --format="%(objectname)" "refs/tags/snapshot-*" 2>$null)
    if ($last) {
        $last = ($last | Out-String).Trim()
        & git.exe --git-dir="$BackupDir" merge-base --is-ancestor $last $new
        if ($LASTEXITCODE -ne 0) {
            $alert = "GitHub's $Branch ($new) no longer contains the last snapshot ($last) - its history was REWRITTEN or WIPED. Local snapshots are intact; do not trust GitHub until you check. See docs/RECOVERY.md."
            Log "ALERT: $alert"
            # The log alone is easy to never read; a Desktop file is not.
            $desk = Join-Path ([Environment]::GetFolderPath("Desktop")) "SLEEPY-BACKUP-ALERT.txt"
            Add-Content -Path $desk -Value ("{0}  {1}`r`nBackup: {2}`r`n" -f (Get-Date -Format "yyyy-MM-dd HH:mm"), $alert, $BackupDir) -Encoding utf8
        }
    }

    if ($new -eq $last) {
        Log "no change since last snapshot ($($new.Substring(0,8)))"
    } else {
        $tag = "snapshot-" + (Get-Date -Format "yyyy-MM-dd-HHmmss")
        Invoke-BackupGit @("tag", $tag, $new) | Out-Null
        $count = (Invoke-BackupGit @("rev-list", "--count", $new)).Trim()
        Log "saved $tag -> $($new.Substring(0,8)) ($count commits of history)"
    }
    exit 0
}
catch {
    Log "FAILED: $($_.Exception.Message)"
    exit 1
}
