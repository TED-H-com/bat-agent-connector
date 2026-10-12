# Deterministic fixture regressions; no installation, app, credentials or live service.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'windows-installed-support.ps1')
function Assert-Fixture([bool]$Value, [string]$Message) { if (-not $Value) { throw $Message } }
function New-Diagnostics { return [ordered]@{attempts=0; transient_failures=0; last_transport_error_type=$null} }
$pollProbe = @{calls=0; deadline=$null}
$diagnostics = New-Diagnostics
Wait-FixtureUntil {
    param($Deadline)
    if ($null -eq $pollProbe.deadline) { $pollProbe.deadline = $Deadline }
    Assert-Fixture ($pollProbe.deadline -eq $Deadline) 'Read retries replaced the original deadline'
    $count = Get-FixturePollCount 'http://127.0.0.1:1' $Deadline $diagnostics {
        param($Url, $Timeout)
        $pollProbe.calls++
        Assert-Fixture ($Timeout -gt 0 -and $Timeout -le 2) 'Request exceeded original readiness budget'
        if ($pollProbe.calls -eq 1) { throw [Threading.Tasks.TaskCanceledException]::new('synthetic timeout') }
        if ($pollProbe.calls -eq 2) { throw [TimeoutException]::new('synthetic wrapped timeout') }
        return [pscustomobject]@{requests=@([pscustomobject]@{method='GET'; path='/api/v1/events'}); violations=@()}
    }
    return $count -gt 0
} 'Transient fixture read did not recover' 3
Assert-Fixture ($pollProbe.calls -eq 3 -and $diagnostics.transient_failures -eq 2) 'Typed timeouts were not retried'
Write-Host 'Passed helper: typed timeout retries within one original deadline'

$expired = Get-FixturePollCount 'http://127.0.0.1:1' ([DateTime]::UtcNow.AddSeconds(-1)) $diagnostics { throw 'Expired request was sent' }
Assert-Fixture ($null -eq $expired -and $diagnostics.attempts -eq 3) 'Expired readiness sent a request'
foreach ($kind in @('http', 'mutation', 'schema')) {
    $observed = $false
    try {
        Get-FixturePollCount 'http://127.0.0.1:1' ([DateTime]::UtcNow.AddSeconds(3)) $diagnostics {
            param($Url, $Timeout)
            if ($kind -eq 'http') { throw [Net.Http.HttpRequestException]::new('synthetic HTTP failure', [TimeoutException]::new('nested timeout'), [Net.HttpStatusCode]::Forbidden) }
            if ($kind -eq 'mutation') { return [pscustomobject]@{requests=@(); violations=@('unauthorized')} }
            return [pscustomobject]@{requests=@()}
        } | Out-Null
    } catch { $observed = $true }
    Assert-Fixture $observed "$kind contract failure was swallowed"
}
Assert-Fixture ($diagnostics.transient_failures -eq 2) 'Contract failure was counted as transient'
Write-Host 'Passed helper: expired budget sends nothing; HTTP, mutation and malformed status fail immediately'

$state = @{exists=$true; calls=0}
$removal = [ordered]@{attempts=0; retries=0}
Remove-FixturePath 'synthetic' ([DateTime]::UtcNow.AddSeconds(3)) $removal { $state.exists } {
    $state.calls++
    if ($state.calls -eq 1) { throw [IO.IOException]::new('synthetic sharing lock') }
    $state.exists = $false
}
Assert-Fixture (-not $state.exists -and $removal.retries -eq 1) 'Natural file unlock did not complete bounded cleanup'
$failures = [Collections.Generic.List[object]]::new()
$primary = [InvalidOperationException]::new('original readiness failure')
$recovered = @{ran=$false}
try { throw $primary } catch {
    $original = $_
    $clean = Invoke-FixtureCleanupStep 'locked_file' {
        Remove-FixturePath 'synthetic' ([DateTime]::UtcNow.AddSeconds(-1)) $removal { $true } { throw [IO.IOException]::new('retained sharing lock') }
    } $failures
    Assert-Fixture (-not $clean) 'Unresolved file lock was reported cleaned'
    $clean = Invoke-FixtureCleanupStep 'next_owned_step' { $recovered.ran=$true } $failures
    Assert-Fixture ($clean -and $recovered.ran) 'One cleanup error prevented the next cleanup step'
    Assert-Fixture ($original.Exception.Message -eq 'original readiness failure' -and $failures.Count -eq 1 -and $failures[0].step -eq 'locked_file') 'Cleanup hid the original failure'
}
Write-Host 'Passed helper: bounded file-lock cleanup and separate primary/cleanup failures'
