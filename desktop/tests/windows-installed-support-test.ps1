# Deterministic fixture regressions; no installation, app, credentials or live service.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'windows-installed-support.ps1')
function Assert-Fixture([bool]$Value, [string]$Message) { if (-not $Value) { throw $Message } }
function New-Diagnostics { return [ordered]@{attempts=0; transient_failures=0; last_transport_error_type=$null} }
function New-FakeClock([DateTime]$StartTime = [DateTime]::UtcNow) {
    $state = [ordered]@{ current = $StartTime }
    return [pscustomobject]@{
        Now = { $state.current }.GetNewClosure()
        Sleep = { param([int]$Milliseconds) $state.current = $state.current.AddMilliseconds($Milliseconds) }.GetNewClosure()
        Advance = { param([int]$Milliseconds) $state.current = $state.current.AddMilliseconds($Milliseconds) }.GetNewClosure()
    }
}
$clock = New-FakeClock
$pollProbe = @{calls=0; deadline=$null}
$diagnostics = New-Diagnostics
Wait-FixtureUntil {
    param($Deadline)
    if ($null -eq $pollProbe.deadline) {
        $pollProbe.deadline = $Deadline
        # Advance by 1 ms on the first probe pass to model the sub-millisecond execution time
        # between setting the deadline and the first poll, ensuring the initial request budget
        # reflects floor(deadline - now) <= 2 s.
        & $clock.Advance 1
    }
    Assert-Fixture ($pollProbe.deadline -eq $Deadline) 'Read retries replaced the original deadline'
    $count = Get-FixturePollCount 'http://127.0.0.1:1' $Deadline $diagnostics {
        param($Url, $Timeout)
        $pollProbe.calls++
        Assert-Fixture ($Timeout -gt 0 -and $Timeout -le 2) 'Request exceeded original readiness budget'
        if ($pollProbe.calls -eq 1) { throw [Threading.Tasks.TaskCanceledException]::new('synthetic timeout') }
        if ($pollProbe.calls -eq 2) { throw [TimeoutException]::new('synthetic wrapped timeout') }
        return [pscustomobject]@{requests=@([pscustomobject]@{method='GET'; path='/api/v1/events'}); violations=@()}
    } -Now $clock.Now
    return $count -gt 0
} 'Transient fixture read did not recover' 3 -Now $clock.Now -Sleep $clock.Sleep
Assert-Fixture ($pollProbe.calls -eq 3 -and $diagnostics.transient_failures -eq 2) 'Typed timeouts were not retried'
Write-Host 'Passed helper: typed timeout retries within one original deadline'

# Prove runner independence under synthetic latency.
# Note: 900 ms per read is impossible under Get-FixturePollCount's floor rule:
# 2 reads (1800 ms) + 2 backoff sleeps (300 ms) = 2100 ms elapsed. At 2100 ms,
# remaining budget is 900 ms (< 1 s). Floor(0.9 s) = 0, so Get-FixturePollCount
# returns $null without reading once floor(deadline - now) < 1 s.
# The maximum per-read cost permitting 3 reads within 3 s is (3000 - 1000 - 300) / 2 = 850 ms.
$slowClock = New-FakeClock
$slowProbe = @{calls=0; deadline=$null}
$slowDiagnostics = New-Diagnostics
Wait-FixtureUntil {
    param($Deadline)
    if ($null -eq $slowProbe.deadline) { $slowProbe.deadline = $Deadline }
    Assert-Fixture ($slowProbe.deadline -eq $Deadline) 'Slow read retries replaced original deadline'
    $count = Get-FixturePollCount 'http://127.0.0.1:1' $Deadline $slowDiagnostics {
        param($Url, $Timeout)
        $slowProbe.calls++
        & $slowClock.Advance 850
        Assert-Fixture ($Timeout -gt 0 -and $Timeout -le 3) 'Request exceeded original readiness budget'
        if ($slowProbe.calls -eq 1) { throw [Threading.Tasks.TaskCanceledException]::new('synthetic timeout') }
        if ($slowProbe.calls -eq 2) { throw [TimeoutException]::new('synthetic wrapped timeout') }
        return [pscustomobject]@{requests=@([pscustomobject]@{method='GET'; path='/api/v1/events'}); violations=@()}
    } -Now $slowClock.Now
    return $count -gt 0
} 'Slow runner probe did not recover within budget' 3 -Now $slowClock.Now -Sleep $slowClock.Sleep
Assert-Fixture ($slowProbe.calls -eq 3 -and $slowDiagnostics.transient_failures -eq 2) 'Slow runner did not complete 3 reads'
Write-Host 'Passed helper: slow runner recovers within 3 s budget under floor rule (850 ms/read)'

$floorExhausted = $false
$floorClock = New-FakeClock
$floorProbe = @{calls=0}
$floorDiagnostics = New-Diagnostics
try {
    Wait-FixtureUntil {
        param($Deadline)
        $count = Get-FixturePollCount 'http://127.0.0.1:1' $Deadline $floorDiagnostics {
            param($Url, $Timeout)
            $floorProbe.calls++
            & $floorClock.Advance 900
            if ($floorProbe.calls -le 2) { throw [TimeoutException]::new('synthetic wrapped timeout') }
            return [pscustomobject]@{requests=@([pscustomobject]@{method='GET'; path='/api/v1/events'}); violations=@()}
        } -Now $floorClock.Now
        return $count -gt 0
    } 'Probe with 900 ms per read exhausted budget under floor rule' 3 -Now $floorClock.Now -Sleep $floorClock.Sleep
} catch {
    $floorExhausted = $_.Exception.Message -ceq 'Probe with 900 ms per read exhausted budget under floor rule'
}
Assert-Fixture ($floorExhausted -and $floorProbe.calls -eq 2 -and $floorDiagnostics.transient_failures -eq 2) 'Floor rule did not exhaust 900 ms reads after 2 attempts'
Write-Host 'Passed helper: 900 ms/read documented as impossible under floor rule (3rd read denied at <1 s budget)'

$expired = Get-FixturePollCount 'http://127.0.0.1:1' ((& $clock.Now).AddSeconds(-1)) $diagnostics { throw 'Expired request was sent' } -Now $clock.Now
Assert-Fixture ($null -eq $expired -and $diagnostics.attempts -eq 3) 'Expired readiness sent a request'
foreach ($kind in @('http', 'mutation', 'schema')) {
    $observed = $false
    try {
        Get-FixturePollCount 'http://127.0.0.1:1' ((& $clock.Now).AddSeconds(3)) $diagnostics {
            param($Url, $Timeout)
            if ($kind -eq 'http') { throw [Net.Http.HttpRequestException]::new('synthetic HTTP failure', [TimeoutException]::new('nested timeout'), [Net.HttpStatusCode]::Forbidden) }
            if ($kind -eq 'mutation') { return [pscustomobject]@{requests=@(); violations=@('unauthorized')} }
            return [pscustomobject]@{requests=@()}
        } -Now $clock.Now | Out-Null
    } catch { $observed = $true }
    Assert-Fixture $observed "$kind contract failure was swallowed"
}
Assert-Fixture ($diagnostics.transient_failures -eq 2) 'Contract failure was counted as transient'
Write-Host 'Passed helper: expired budget sends nothing; HTTP, mutation and malformed status fail immediately'

$state = @{exists=$true; calls=0}
$removal = [ordered]@{attempts=0; retries=0}
Remove-FixturePath 'synthetic' ((& $clock.Now).AddSeconds(3)) $removal { $state.exists } {
    $state.calls++
    if ($state.calls -eq 1) { throw [IO.IOException]::new('synthetic sharing lock') }
    $state.exists = $false
} -Now $clock.Now -Sleep $clock.Sleep
Assert-Fixture (-not $state.exists -and $removal.retries -eq 1) 'Natural file unlock did not complete bounded cleanup'
$failures = [Collections.Generic.List[object]]::new()
$primary = [InvalidOperationException]::new('original readiness failure')
$recovered = @{ran=$false}
try { throw $primary } catch {
    $original = $_
    $clean = Invoke-FixtureCleanupStep 'locked_file' {
        Remove-FixturePath 'synthetic' ((& $clock.Now).AddSeconds(-1)) $removal { $true } { throw [IO.IOException]::new('retained sharing lock') } -Now $clock.Now -Sleep $clock.Sleep
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
