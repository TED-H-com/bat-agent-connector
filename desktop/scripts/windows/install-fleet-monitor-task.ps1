[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $ExePath,
    [Parameter(Mandatory = $true)][string] $ConfigPath
)
$ErrorActionPreference = 'Stop'
$exe = (Resolve-Path -LiteralPath $ExePath).ProviderPath
$config = (Resolve-Path -LiteralPath $ConfigPath).ProviderPath
if ($exe.Contains('"') -or $config.Contains('"')) { throw 'Invalid path quoting' }
$name = 'BAT-Connector Fleet Monitor'
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing) {
    $backup = Join-Path (Split-Path -Parent $config) ('fleet-monitor-task-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff') + '.xml')
    Export-ScheduledTask -TaskName $name | Set-Content -LiteralPath $backup -Encoding Unicode
}
$arguments = '--fleet-supervisor --fleet-config "' + $config + '"'
# Release Dashboard is a GUI-subsystem exe. Console executables need a headless host:
# Task Scheduler's Hidden setting alone does not suppress a console window.
if ([IO.Path]::GetFileName($exe) -match '^(powershell|pwsh|cmd|node)(\.exe)?$') {
    if ([IO.Path]::GetFileName($exe) -match '^(powershell|pwsh)(\.exe)?$') {
        $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden ' + $arguments
    }
    $arguments = '--headless "' + $exe + '" ' + $arguments
    $exe = Join-Path $env:SystemRoot 'System32/conhost.exe'
}
$action = New-ScheduledTaskAction -Execute $exe -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT45S'
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -Hidden -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force
