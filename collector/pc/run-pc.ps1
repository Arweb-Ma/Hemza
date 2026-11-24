# Collects the stores that refuse servers (marked "pc": true in collector/stores.json:
# Decathlon, Zara, DeFacto, Lefties, Beauty Success) from this PC's home connection.
# Started by the "Reeklam - collecte PC" scheduled task (see install-schedule.ps1); can also be run by hand:
#   powershell -ExecutionPolicy Bypass -File collector\pc\run-pc.ps1
#
# Settings come from your Windows user environment variables (set once by install-schedule.ps1):
#   SUPABASE_SERVICE_KEY   required
#   HEMZA_CHROME           default "chrome" (your installed Google Chrome)
#   HEMZA_HEADED           default "1" (visible window; set it to 0 to try hidden)
#   HEMZA_PROFILE          default %LOCALAPPDATA%\Hemza\chrome-profile (keeps cookies between runs)
#   HEMZA_PC_MINUTES       default 150 (time budget of one run)

$ErrorActionPreference = 'Continue'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $repo

$logDir = Join-Path $repo 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ('pc-' + (Get-Date -Format 'yyyy-MM-dd_HHmm') + '.log')
# Keep the last 30 logs.
Get-ChildItem $logDir -Filter 'pc-*.log' | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 30 | Remove-Item -ErrorAction SilentlyContinue

function Write-Log([string]$text) {
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $text
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-Setting([string]$name, [string]$default) {
    $value = [Environment]::GetEnvironmentVariable($name, 'Process')
    if (-not $value) { $value = [Environment]::GetEnvironmentVariable($name, 'User') }
    if (-not $value) { $value = $default }
    return $value
}

$env:SUPABASE_URL         = Get-Setting 'SUPABASE_URL' 'https://tcsjfqkreqceektalogu.supabase.co'
$env:SUPABASE_SERVICE_KEY = Get-Setting 'SUPABASE_SERVICE_KEY' ''
$env:HEMZA_CHROME         = Get-Setting 'HEMZA_CHROME' 'chrome'
$env:HEMZA_HEADED         = Get-Setting 'HEMZA_HEADED' '1'
$env:HEMZA_PROFILE        = Get-Setting 'HEMZA_PROFILE' (Join-Path $env:LOCALAPPDATA 'Hemza\chrome-profile')
$minutes                  = Get-Setting 'HEMZA_PC_MINUTES' '150'
$env:PYTHONIOENCODING     = 'utf-8'
# Telegram posting stays on GitHub, so the 6-a-day limit is counted in one place.
Remove-Item Env:TELEGRAM_BOT_TOKEN, Env:TELEGRAM_CHANNEL -ErrorAction SilentlyContinue

if (-not $env:SUPABASE_SERVICE_KEY) {
    Write-Log 'SUPABASE_SERVICE_KEY is not set: run collector\pc\install-schedule.ps1 once. Nothing collected.'
    exit 1
}
New-Item -ItemType Directory -Force -Path $env:HEMZA_PROFILE | Out-Null

# Use the latest code from GitHub; if that fails (no network, local edits), run what is here.
if (Get-Command git -ErrorAction SilentlyContinue) {
    $pull = git pull --ff-only 2>&1 | Out-String
    Write-Log ('git pull: ' + $pull.Trim())
}

$py = 'python'
if (Get-Command py -ErrorAction SilentlyContinue) { $py = 'py' }

Write-Log "Collecting the PC stores (budget $minutes min)..."
# cmd /c keeps Python's stderr as plain text in the log (PowerShell 5 would wrap it as errors).
cmd /c "$py collector\run.py --where pc --max-minutes $minutes >> `"$log`" 2>&1"
$code = $LASTEXITCODE
Write-Log "Collection finished (exit code $code)."

# Rebuild the site now if the GitHub CLI is installed and signed in; otherwise the next
# GitHub brands run (every 2 hours) rebuilds it with these prices.
if (Get-Command gh -ErrorAction SilentlyContinue) {
    $out = gh workflow run deploy.yml --repo Arweb-Ma/Hemza --ref main 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) { Write-Log 'Site rebuild started on GitHub.' }
    else { Write-Log ('Could not start the rebuild (' + $out.Trim() + '); the next GitHub run will do it.') }
} else {
    Write-Log 'GitHub CLI (gh) not found: the site will show these prices after the next GitHub run.'
}
exit $code
