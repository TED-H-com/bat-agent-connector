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
$action = New-ScheduledTaskAction -Execute $exe -Argument ('--fleet-supervisor --fleet-config "' + $config + '"')
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT45S'
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force
