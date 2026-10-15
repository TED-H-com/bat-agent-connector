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

$view = @{state=@{}}
$history = [ordered]@{attempt_count=0; attempts=[Collections.Generic.List[object]]::new()}
$viewPoll = @{deadline=$null}
$ready = '{"webContent":true,"authenticated":true,"setup":true,"configurationLoaded":true,"saveEnabled":true,"bounded":true}'
Wait-FixtureUntil {
    param($Deadline)
    if ($null -eq $viewPoll.deadline) { $viewPoll.deadline = $Deadline }
    Assert-Fixture ($viewPoll.deadline -eq $Deadline) 'UIA retry replaced original deadline'
    Assert-Fixture ((Get-FixtureViewTimeout $Deadline) -le 3000) 'UIA attempt escaped remaining budget'
    $attempt = New-FixtureViewAttempt $history
    if ($history.attempt_count -le 2) {
        $attempt.exit_code = 3
        Set-FixtureViewDiagnostics $attempt "started`nquery_completed`nfailed`nerror_type:System.Runtime.InteropServices.COMException`nerror_hresult:0x80010108`nprivate UI text`n"
        $result = Test-FixtureViewResult $attempt 'not JSON: a failed read is never parsed' $view
        Assert-Fixture (-not $result -and $view.state.Count -eq 0) 'Transient failure became readiness'
        return $result
    }
    $attempt.exit_code = 0
    Set-FixtureViewDiagnostics $attempt "started`nquery_completed`ncompleted`n"
    return Test-FixtureViewResult $attempt $ready $view
} 'Transient UIA read did not recover' 3
Assert-Fixture ($history.attempt_count -eq 3 -and $history.attempts.Count -eq 3) 'UIA attempts were lost'
Assert-Fixture ($history.attempts[0].phases.failed -and $history.attempts[0].exit_code -eq 3 -and
    $history.attempts[0].error_type -ceq 'System.Runtime.InteropServices.COMException' -and
    $history.attempts[0].error_hresult -ceq '0x80010108' -and $history.attempts[2].phases.completed) 'UIA attempt diagnostics changed'
Assert-Fixture ((Get-FixtureViewTimeout ([DateTime]::UtcNow.AddSeconds(30))) -eq 8000) 'UIA per-probe deadline changed'
Assert-Fixture ((Get-FixtureViewTimeout ([DateTime]::UtcNow.AddSeconds(-1))) -eq 0) 'UIA expired deadline still has budget'
Write-Host 'Passed helper: transient UIA reads recover within the same deadline with per-attempt evidence'

$exhausted = $false
$retryBudget = @{deadline=$null; calls=0}
try {
    Wait-FixtureUntil {
        param($Deadline)
        $retryBudget.calls++
        if ($null -eq $retryBudget.deadline) { $retryBudget.deadline = $Deadline }
        Assert-Fixture ($retryBudget.deadline -eq $Deadline) 'Repeated UIA failures renewed the deadline'
        $attempt = New-FixtureViewAttempt $history
        $attempt.exit_code = 3
        Test-FixtureViewResult $attempt $ready $view
    } 'Synthetic UIA budget exhausted' 1
} catch { $exhausted = $_.Exception.Message -ceq 'Synthetic UIA budget exhausted' }
Assert-Fixture ($exhausted -and $retryBudget.calls -ge 1 -and [DateTime]::UtcNow -ge $retryBudget.deadline) 'Repeated UIA failures escaped the original wait budget'
Write-Host 'Passed helper: repeated transient UIA reads exhaust the original wait'

foreach ($kind in @('fatal', 'unknown_exit', 'timeout', 'invalid_json')) {
    $calls = @{count=0}
    $caught = $false
    try {
        Wait-FixtureUntil {
            $calls.count++
            $attempt = New-FixtureViewAttempt $history
            $attempt.exit_code = if ($kind -eq 'fatal') { 2 } elseif ($kind -eq 'unknown_exit') { 9 } elseif ($kind -eq 'timeout') { 3 } else { 0 }
            $attempt.phases.timed_out = $kind -eq 'timeout'
            Test-FixtureViewResult $attempt 'invalid JSON' $view
        } 'Unexpected wait exhaustion' 3
    } catch { $caught = $true }
    Assert-Fixture ($caught -and $calls.count -eq 1 -and $view.state.Count -eq 0) "$kind UIA failure was retried or retained stale evidence"
}
foreach ($field in @('webContent', 'authenticated', 'setup', 'configurationLoaded', 'saveEnabled', 'bounded')) {
    $attempt = New-FixtureViewAttempt $history
    $attempt.exit_code = 0
    $partial = $ready | ConvertFrom-Json
    $partial.$field = $false
    Assert-Fixture (-not (Test-FixtureViewResult $attempt ($partial | ConvertTo-Json) $view)) "UIA readiness ignored $field"
}
$attempt = New-FixtureViewAttempt $history
$attempt.exit_code = 3
Assert-Fixture (-not (Test-FixtureViewResult $attempt $ready $view) -and $view.state.Count -eq 0) 'Failed read reused prior readiness'
Write-Host 'Passed helper: fatal and timed-out UIA reads stop immediately; every positive readiness flag remains required'

1..25 | ForEach-Object { $null = New-FixtureViewAttempt $history }
Set-FixtureViewDiagnostics $history.attempts[19] "node_unavailable`nerror_type:System.Runtime.InteropServices.COMException`nerror_hresult:0x80040201`nerror_type:System.InvalidOperationException`nprivate UI text`nerror_type:private UI text`nerror_hresult:private UI text`n"
$json = $history | ConvertTo-Json -Depth 5
$saved = $json | ConvertFrom-Json
Assert-Fixture ($saved.attempts.Count -eq 20 -and $saved.attempts[0].attempt -eq ($saved.attempt_count - 19) -and
    $saved.attempts[19].attempt -eq $saved.attempt_count) 'UIA evidence did not retain the last 20 attempts'
Assert-Fixture ($saved.attempts[19].unavailable_nodes -eq 1 -and $saved.attempts[19].error_type -ceq 'System.InvalidOperationException' -and
    $null -eq $saved.attempts[19].error_hresult -and -not $json.Contains('private UI text')) 'UIA diagnostics leaked text or paired an old HRESULT'
Write-Host 'Passed helper: bounded UIA evidence round-trips phases, outcomes and sanitized diagnostics'

if ($IsWindows) {
    # Compile the actual probe plus a different test entry point against the same
    # .NET Framework UIA assemblies used by the installed fixture.
    $framework = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319'
    $testRoot = Join-Path ([IO.Path]::GetTempPath()) ('batc-uia-test-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $testRoot
    $testProbe = Join-Path $testRoot 'probe-test.exe'
    try {
        # Phase lines show which step used the budget if the self-test times out.
        Write-Host 'Compiling UIA probe tests'
        & (Join-Path $framework 'csc.exe') /nologo /target:exe /platform:x64 /codepage:65001 /main:ManagedViewProbeTests "/out:$testProbe" `
            "/reference:$(Join-Path $framework 'WPF/UIAutomationClient.dll')" `
            "/reference:$(Join-Path $framework 'WPF/UIAutomationTypes.dll')" `
            "/reference:$(Join-Path $framework 'WPF/WindowsBase.dll')" `
            (Join-Path $PSScriptRoot 'windows-managed-view.cs') (Join-Path $PSScriptRoot 'windows-managed-view-test.cs')
        if ($LASTEXITCODE -ne 0) { throw 'UIA probe regression compilation failed' }
        Write-Host 'Running UIA probe tests'
        & $testProbe
        if ($LASTEXITCODE -ne 0) { throw 'UIA probe regression failed' }
    } finally { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}
