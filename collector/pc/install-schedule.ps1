# Creates (or updates) the Windows scheduled task "Reeklam - collecte PC", which runs
# collector\pc\run-pc.ps1 every day. Run it once from the repo folder, in PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File collector\pc\install-schedule.ps1
#   powershell -ExecutionPolicy Bypass -File collector\pc\install-schedule.ps1 -At 08:00,20:00   # twice a day
#   powershell -ExecutionPolicy Bypass -File collector\pc\install-schedule.ps1 -RunNow           # and run once now
#   powershell -ExecutionPolicy Bypass -File collector\pc\install-schedule.ps1 -Remove           # delete the task
#
# The task runs while you are signed in to Windows (Chrome opens a window), and if the PC
# was off or asleep at the planned time, it runs as soon as possible afterwards.

param(
    [string[]]$At = @('08:00'),
    [switch]$RunNow,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$taskName = 'Reeklam - collecte PC'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Task '$taskName' removed."
    return
}

$runner = Join-Path $PSScriptRoot 'run-pc.ps1'
$collector = Split-Path $PSScriptRoot -Parent

# 1. The database key, kept in your Windows user settings (never in the repo).
$key = [Environment]::GetEnvironmentVariable('SUPABASE_SERVICE_KEY', 'User')
if (-not $key) {
    Write-Host 'Paste the Supabase service key (Supabase > Project settings > API keys > service_role / secret):'
    $secure = Read-Host 'Key' -AsSecureString
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if (-not $key) { throw 'No key given: nothing was scheduled.' }
    [Environment]::SetEnvironmentVariable('SUPABASE_SERVICE_KEY', $key, 'User')
    Write-Host 'Key saved in your Windows user settings.'
} else {
    Write-Host 'Supabase key already set in your Windows user settings.'
}

# 2. Python and the browser reader. (Windows PowerShell turns a program's error output into
#    errors, so "Stop" is relaxed while Python runs.)
$py = 'python'
if (Get-Command py -ErrorAction SilentlyContinue) { $py = 'py' }
$ErrorActionPreference = 'Continue'
& $py -c "import requests, playwright" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing the Python packages the collector needs...'
    & $py -m pip install -r (Join-Path $collector 'requirements.txt') -r (Join-Path $collector 'requirements-browser.txt')
}
$ErrorActionPreference = 'Stop'

# 3. The scheduled task.
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$runner`""
$triggers = @($At | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Settings $settings -Principal $principal `
    -Description 'Reeklam: collects Decathlon, Zara, DeFacto, Lefties and Beauty Success from this PC (collector\pc\run-pc.ps1).' `
    -Force | Out-Null

Write-Host ''
Write-Host "Task '$taskName' scheduled every day at $($At -join ', ')."
Write-Host "Logs: $(Join-Path (Split-Path $collector -Parent) 'logs')"
Write-Host 'Change it later in Task Scheduler (Planificateur de taches), or run this script again with -At.'

if ($RunNow) {
    Start-ScheduledTask -TaskName $taskName
    Write-Host 'Started a first run now.'
}
