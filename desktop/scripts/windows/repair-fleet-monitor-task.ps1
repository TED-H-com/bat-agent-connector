[CmdletBinding()]
param([Parameter(Mandatory = $true)][string] $ExePath)
$ErrorActionPreference = 'Stop'
# Upgrade only the current user's task for this installation. Preserve its action,
# triggers, interactive logon, restart policy and all other settings.
try {
    $task = Get-ScheduledTask -TaskPath '\' -TaskName 'BAT-Connector Fleet Monitor' -ErrorAction Stop
} catch {
    if ($_.CategoryInfo.Category -eq 'ObjectNotFound') { return }
    throw
}
if (!$task) { return }
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if ($task.Principal.UserId -match '^S-1-') { $ownerSid = $task.Principal.UserId }
else {
    $owner = [System.Security.Principal.NTAccount]::new($task.Principal.UserId)
    $ownerSid = $owner.Translate([System.Security.Principal.SecurityIdentifier]).Value
}
if ($ownerSid -ne $sid) { return }
if ($task.Actions.Count -ne 1 -or $task.Actions[0].Execute -ine $ExePath) { return }
if ($task.Actions[0].Arguments -notmatch '^--fleet-supervisor(?:\s|$)') { return }
if (!$task.Settings.Hidden) {
    $task.Settings.Hidden = $true
    Set-ScheduledTask -InputObject $task | Out-Null
}
