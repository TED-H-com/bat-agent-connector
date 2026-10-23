# Deterministic fixture regressions; no installation, app, credentials or live service.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'windows-installed-support.ps1')
function Assert-Fixture([bool]$Value, [string]$Message) { if (-not $Value) { throw $Message } }
function New-Diagnostics { return [ordered]@{attempts=0; transient_failures=0; last_transport_error_type=$null} }

$registered = @(Get-FixtureRegistration 'fixture-root' 'Fixture Dashboard' {
    @([pscustomobject]@{PSPath='removed'}, [pscustomobject]@{PSPath='unrelated'}, [pscustomobject]@{PSPath='present'})
} {
    param($Path)
    if ($Path -eq 'removed') { throw [System.Management.Automation.ItemNotFoundException]::new('Removed after enumeration') }
    [pscustomobject]@{DisplayName=$(if ($Path -eq 'present') {'Fixture Dashboard'} else {'Another app'}); Marker=$Path}
})
Assert-Fixture ($registered.Count -eq 1 -and $registered[0].Marker -ceq 'present') 'A vanished key hid a still-installed product'
$absent = @(Get-FixtureRegistration 'fixture-root' 'Fixture Dashboard' {
    [pscustomobject]@{PSPath='removed'}
} { throw [System.Management.Automation.ItemNotFoundException]::new('Removed after enumeration') })
Assert-Fixture ($absent.Count -eq 0) 'A removed key prevented uninstall observation'
$absentRoot = @(Get-FixtureRegistration 'fixture-root' 'Fixture Dashboard' {
    throw [System.Management.Automation.ItemNotFoundException]::new('Absent root')
} { throw 'An absent root must not be read' })
Assert-Fixture ($absentRoot.Count -eq 0) 'Absent registration root was not empty'
foreach ($phase in @('enumerate', 'read')) {
    $propagated = $false
    try {
        Get-FixtureRegistration 'fixture-root' 'Fixture Dashboard' {
            if ($phase -eq 'enumerate') { throw [UnauthorizedAccessException]::new('Synthetic denied enumeration') }
            [pscustomobject]@{PSPath='denied'}
        } { throw [UnauthorizedAccessException]::new('Synthetic denied properties') } | Out-Null
    } catch [UnauthorizedAccessException] { $propagated = $true }
    Assert-Fixture $propagated "Registration $phase access failure was swallowed"
}
Write-Host 'Passed helper: concurrent registration removal is absent; remaining products and access failures stay visible'

# RegistryKey.Win32Error passes the raw Win32 code to IOException(message, hresult),
# not HRESULT_FROM_WIN32. RegistryProvider writes that exception in an ErrorRecord;
# -ErrorAction Stop exposes it to catch without an inner exception.
function Write-FixtureRegistryError([Exception]$Exception, [string]$Path) {
    $category = if ($Exception -is [UnauthorizedAccessException] -or $Exception -is [Security.SecurityException]) {
        [System.Management.Automation.ErrorCategory]::PermissionDenied
    } else { [System.Management.Automation.ErrorCategory]::ReadError }
    $record = [System.Management.Automation.ErrorRecord]::new($Exception, $Exception.GetType().FullName, $category, $Path)
    Write-Error -ErrorRecord $record -ErrorAction Stop
}
$deletedKey = [IO.IOException]::new('Synthetic key marked for deletion', 1018)
Assert-Fixture ($deletedKey.HResult -eq 1018 -and $null -eq $deletedKey.InnerException) 'Deleted-key fixture has the wrong .NET exception shape'
$deletedRoot = @(Get-FixtureRegistration 'fixture-root' 'Fixture Dashboard' {
    param($Root)
    Write-FixtureRegistryError $deletedKey $Root
} { throw 'A deleted root must not be read' })
Assert-Fixture ($deletedRoot.Count -eq 0) 'A root deleted during name enumeration was not absent'
$deletedOnly = @(Get-FixtureRegistration 'fixture-root' 'Fixture Dashboard' {
    [pscustomobject]@{PSPath='deleted'}
} { param($Path) Write-FixtureRegistryError $deletedKey $Path })
Assert-Fixture ($deletedOnly.Count -eq 0) 'A key deleted during its property read was not absent'
foreach ($product in @('Fixture Dashboard', 'Another app')) {
    $reads = [Collections.Generic.List[string]]::new()
    $remaining = @(Get-FixtureRegistration 'fixture-root' $product {
        @('before', 'deleted', 'unrelated', 'after') | ForEach-Object { [pscustomobject]@{PSPath=$_} }
    } {
        param($Path)
        $reads.Add($Path)
        if ($Path -eq 'deleted') { Write-FixtureRegistryError $deletedKey $Path }
        [pscustomobject]@{DisplayName=$(if ($Path -eq 'unrelated') {'Another app'} else {'Fixture Dashboard'}); Marker=$Path}
    })
    $expected = if ($product -eq 'Fixture Dashboard') { 'before,after' } else { 'unrelated' }
    Assert-Fixture (($remaining.Marker -join ',') -ceq $expected) 'A deleted key hid another registration or leaked partial properties'
    Assert-Fixture (($reads -join ',') -ceq 'before,deleted,unrelated,after') 'A deleted key stopped subsequent registration reads'
}
foreach ($failure in @(
    [UnauthorizedAccessException]::new('Synthetic denied registration'),
    [Security.SecurityException]::new('Synthetic registry security failure'),
    [IO.IOException]::new('Synthetic registry I/O failure', 1016),
    [IO.IOException]::new('Synthetic other I/O failure'),
    [InvalidOperationException]::new('Synthetic unrecognized provider failure')
)) {
    foreach ($phase in @('enumerate', 'read')) {
        $propagated = $false
        try {
            Get-FixtureRegistration 'fixture-root' 'Fixture Dashboard' {
                param($Root)
                [pscustomobject]@{PSPath='partial'}
                if ($phase -eq 'enumerate') { Write-FixtureRegistryError $failure $Root }
            } { param($Path) Write-FixtureRegistryError $failure $Path } | Out-Null
        } catch { $propagated = [object]::ReferenceEquals($_.Exception, $failure) }
        Assert-Fixture $propagated "Registration $phase failure was swallowed or replaced: $($failure.GetType().FullName)"
    }
}
Write-Host 'Passed helper: ERROR_KEY_DELETED is absent; all remaining registrations and other provider failures stay visible'

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
        # A real first poll always starts after the deadline is computed, so less than 3 s
        # remains and the first timeout is at most 2 s.
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
$slowTimeouts = [Collections.Generic.List[int]]::new()
Wait-FixtureUntil {
    param($Deadline)
    if ($null -eq $slowProbe.deadline) { $slowProbe.deadline = $Deadline }
    Assert-Fixture ($slowProbe.deadline -eq $Deadline) 'Slow read retries replaced original deadline'
    $count = Get-FixturePollCount 'http://127.0.0.1:1' $Deadline $slowDiagnostics {
        param($Url, $Timeout)
        $slowProbe.calls++
        Assert-Fixture ($Timeout -gt 0) 'Request timeout was not positive'
        $slowTimeouts.Add($Timeout)
        & $slowClock.Advance 850
        if ($slowProbe.calls -eq 1) { throw [Threading.Tasks.TaskCanceledException]::new('synthetic timeout') }
        if ($slowProbe.calls -eq 2) { throw [TimeoutException]::new('synthetic wrapped timeout') }
        return [pscustomobject]@{requests=@([pscustomobject]@{method='GET'; path='/api/v1/events'}); violations=@()}
    } -Now $slowClock.Now
    return $count -gt 0
} 'Slow runner probe did not recover within budget' 3 -Now $slowClock.Now -Sleep $slowClock.Sleep
Assert-Fixture ($slowProbe.calls -eq 3 -and $slowDiagnostics.transient_failures -eq 2) 'Slow runner did not complete 3 reads'
Assert-Fixture (($slowTimeouts -join ',') -eq '3,2,1') "Expected timeout sequence 3,2,1 but observed $($slowTimeouts -join ',')"
Write-Host 'Passed helper: slow runner recovers within 3 s budget under floor rule (850 ms/read)'

$floorExhausted = $false
$floorClock = New-FakeClock
$floorProbe = @{calls=0}
$floorDiagnostics = New-Diagnostics
$floorTimeouts = [Collections.Generic.List[int]]::new()
try {
    Wait-FixtureUntil {
        param($Deadline)
        $count = Get-FixturePollCount 'http://127.0.0.1:1' $Deadline $floorDiagnostics {
            param($Url, $Timeout)
            $floorProbe.calls++
            Assert-Fixture ($Timeout -gt 0) 'Request timeout was not positive'
            $floorTimeouts.Add($Timeout)
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
Assert-Fixture (($floorTimeouts -join ',') -eq '3,1') "Expected timeout sequence 3,1 but observed $($floorTimeouts -join ',')"
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
$uiaClock = New-FakeClock
Wait-FixtureUntil {
    param($Deadline)
    if ($null -eq $viewPoll.deadline) { $viewPoll.deadline = $Deadline }
    Assert-Fixture ($viewPoll.deadline -eq $Deadline) 'UIA retry replaced original deadline'
    Assert-Fixture ((Get-FixtureViewTimeout $Deadline -Now $uiaClock.Now) -le 3000) 'UIA attempt escaped remaining budget'
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
} 'Transient UIA read did not recover' 3 -Now $uiaClock.Now -Sleep $uiaClock.Sleep
Assert-Fixture ($history.attempt_count -eq 3 -and $history.attempts.Count -eq 3) 'UIA attempts were lost'
Assert-Fixture ($history.attempts[0].phases.failed -and $history.attempts[0].exit_code -eq 3 -and
    $history.attempts[0].error_type -ceq 'System.Runtime.InteropServices.COMException' -and
    $history.attempts[0].error_hresult -ceq '0x80010108' -and $history.attempts[2].phases.completed) 'UIA attempt diagnostics changed'
Assert-Fixture ((Get-FixtureViewTimeout ((& $uiaClock.Now).AddSeconds(30)) -Now $uiaClock.Now) -eq 8000) 'UIA per-probe deadline changed'
Assert-Fixture ((Get-FixtureViewTimeout ((& $uiaClock.Now).AddSeconds(-1)) -Now $uiaClock.Now) -eq 0) 'UIA expired deadline still has budget'
Write-Host 'Passed helper: transient UIA reads recover within the same deadline with per-attempt evidence'

$exhausted = $false
$retryBudget = @{deadline=$null; calls=0}
$exhaustClock = New-FakeClock
try {
    Wait-FixtureUntil {
        param($Deadline)
        $retryBudget.calls++
        if ($null -eq $retryBudget.deadline) { $retryBudget.deadline = $Deadline }
        Assert-Fixture ($retryBudget.deadline -eq $Deadline) 'Repeated UIA failures renewed the deadline'
        $attempt = New-FixtureViewAttempt $history
        $attempt.exit_code = 3
        Test-FixtureViewResult $attempt $ready $view
    } 'Synthetic UIA budget exhausted' 1 -Now $exhaustClock.Now -Sleep $exhaustClock.Sleep
} catch { $exhausted = $_.Exception.Message -ceq 'Synthetic UIA budget exhausted' }
Assert-Fixture ($exhausted -and $retryBudget.calls -ge 1 -and (& $exhaustClock.Now) -ge $retryBudget.deadline) 'Repeated UIA failures escaped the original wait budget'
Write-Host 'Passed helper: repeated transient UIA reads exhaust the original wait'

foreach ($kind in @('fatal', 'unknown_exit', 'timeout', 'invalid_json')) {
    $calls = @{count=0}
    $caught = $false
    $fatalClock = New-FakeClock
    try {
        Wait-FixtureUntil {
            $calls.count++
            $attempt = New-FixtureViewAttempt $history
            $attempt.exit_code = if ($kind -eq 'fatal') { 2 } elseif ($kind -eq 'unknown_exit') { 9 } elseif ($kind -eq 'timeout') { 3 } else { 0 }
            $attempt.phases.timed_out = $kind -eq 'timeout'
            Test-FixtureViewResult $attempt 'invalid JSON' $view
        } 'Unexpected wait exhaustion' 3 -Now $fatalClock.Now -Sleep $fatalClock.Sleep
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

# Diagnostics only: neither a responsive HWND nor a slow-start phase permits a
# timed-out UIA query to be retried without evidence establishing its cause.
$timed = New-FixtureViewAttempt $history
Set-FixtureViewDiagnostics $timed "started`nphase_elapsed_ms:started:7300`nphase_elapsed_ms:owner_verified:7310`nphase_elapsed_ms:assemblies_loaded:7800`n"
Assert-Fixture ($timed.phase_elapsed_ms.started -eq 7300 -and $timed.phase_elapsed_ms.assemblies_loaded -eq 7800 -and
    $timed.phases.assemblies_loaded -and -not $timed.phases.window_resolved) 'Cold probe startup time was lost'
Set-FixtureViewDiagnostics $timed "phase_elapsed_ms:started:1`nphase_elapsed_ms:private UI text:2`nphase_elapsed_ms:failed:-1`nphase_elapsed_ms:failed:60001`nphase_elapsed_ms:failed:99999999999999`nphase_elapsed_ms:failed:1 secret`n"
Assert-Fixture ($timed.phase_elapsed_ms.started -eq 7300 -and $timed.phase_elapsed_ms.Count -eq 3) 'Malformed, duplicate or unbounded phase timing was retained'
$bounded = New-FixtureViewAttempt $history
Set-FixtureViewDiagnostics $bounded (("private UI text`n" * 256) + "phase_elapsed_ms:completed:1`n")
Assert-Fixture ($bounded.diagnostics_truncated -and $bounded.phase_elapsed_ms.Count -eq 0) 'Diagnostic line bound was ignored'
$bounded = New-FixtureViewAttempt $history
Set-FixtureViewDiagnostics $bounded (('x' * 16384) + "`nphase_elapsed_ms:completed:1`n")
Assert-Fixture ($bounded.diagnostics_truncated -and $bounded.phase_elapsed_ms.Count -eq 0) 'Diagnostic character bound was ignored'
Set-FixtureViewDiagnostics $timed "phase_elapsed_ms:failed:60000`n"
Assert-Fixture ($timed.phase_elapsed_ms.failed -eq 60000) 'Maximum fixed-format elapsed time was lost'
$json = $timed | ConvertTo-Json -Depth 5
Assert-Fixture (-not $json.Contains('private UI text') -and -not $json.Contains('secret')) 'Timing diagnostics retained provider text'
Write-Host 'Passed helper: phase elapsed times are fixed, first-only and bounded without provider text'

$sampleClock = New-FakeClock
foreach ($state in @('responsive', 'hung', 'failed', 'owner_changed')) {
    $response = Get-FixtureWindowResponse ([IntPtr]1) 42 ((& $sampleClock.Now).AddMilliseconds(75)) {
        param($Window, $Owner, $Timeout)
        Assert-Fixture ($Window -eq [IntPtr]1 -and $Owner -eq 42 -and $Timeout -eq 75) 'WM_NULL sample escaped its target or remaining deadline'
        [pscustomobject]@{State=$state; ElapsedMilliseconds=75L; ErrorCode=0}
    } -Now $sampleClock.Now
    Assert-Fixture ($response.state -ceq $state -and $response.elapsed_ms -eq 75 -and $response.timeout_ms -eq 75) 'Responsiveness sample changed'
}
$response = Get-FixtureWindowResponse ([IntPtr]1) 42 ((& $sampleClock.Now).AddSeconds(30)) {
    param($Window, $Owner, $Timeout)
    Assert-Fixture ($Timeout -eq 200) 'WM_NULL sample cap changed'
    [pscustomobject]@{State='responsive'; ElapsedMilliseconds=1L; ErrorCode=0}
} -Now $sampleClock.Now
$expired = Get-FixtureWindowResponse ([IntPtr]1) 42 ((& $sampleClock.Now).AddMilliseconds(-1)) {
    throw 'Expired responsiveness sample ran'
} -Now $sampleClock.Now
Assert-Fixture ($expired.state -ceq 'not_sampled' -and $expired.timeout_ms -eq 0) 'Expired window sampling used a new deadline'
foreach ($invalid in @(
    @{State='private UI text'; ElapsedMilliseconds=1L; ErrorCode=0},
    @{State='responsive'; ElapsedMilliseconds=-1L; ErrorCode=0},
    @{State='responsive'; ElapsedMilliseconds='secret'; ErrorCode=0}
)) {
    $refused = $false
    try { Get-FixtureWindowResponse ([IntPtr]1) 42 ((& $sampleClock.Now).AddSeconds(30)) { $invalid } -Now $sampleClock.Now }
    catch { $refused = $true }
    Assert-Fixture $refused 'Unknown responsiveness diagnostics escaped'
}
foreach ($stage in @('assemblies_loaded', 'query_started')) {
    foreach ($responseState in @('responsive', 'hung')) {
        $attempt = New-FixtureViewAttempt $history
        $attempt.phases[$stage] = $true
        $attempt.phases.timed_out = $true
        $attempt.window_response = @{state=$responseState; elapsed_ms=200; timeout_ms=200; win32_error=1460}
        $attempt.exit_code = 3
        $fatal = $false
        try { Test-FixtureViewResult $attempt $ready $view | Out-Null }
        catch { $fatal = $_.Exception.Message -ceq 'Native UI Automation probe exceeded its deadline' }
        Assert-Fixture ($fatal -and $view.state.Count -eq 0) 'Diagnostics weakened timeout/readiness classification'
    }
}
Write-Host 'Passed helper: bounded native window samples are diagnostic only; all UIA timeouts remain fatal'

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
