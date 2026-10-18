# Fixture-only helpers. Dot-sourcing does not start or stop a process.
function Wait-FixtureUntil(
    [scriptblock]$Condition,
    [string]$Failure,
    [int]$Seconds = 30,
    [scriptblock]$Now = { [DateTime]::UtcNow },
    [scriptblock]$Sleep = { param($Milliseconds) Start-Sleep -Milliseconds $Milliseconds }
) {
    $deadline = (& $Now).AddSeconds($Seconds)
    while ((& $Now) -lt $deadline) {
        if (& $Condition $deadline) { return }
        $remaining = ($deadline - (& $Now)).TotalMilliseconds
        if ($remaining -gt 0) { & $Sleep ([int][Math]::Min(150, $remaining)) }
    }
    throw $Failure
}
function Get-FixtureViewTimeout(
    [DateTime]$Deadline,
    [scriptblock]$Now = { [DateTime]::UtcNow }
) {
    # Keep the 8 s process cap inside the caller's original readiness deadline.
    return [int][Math]::Max(0, [Math]::Floor([Math]::Min(8000, ($Deadline - (& $Now)).TotalMilliseconds)))
}
function New-FixtureViewAttempt([System.Collections.IDictionary]$Diagnostics) {
    $Diagnostics.attempt_count++
    $attempt = [ordered]@{
        attempt = $Diagnostics.attempt_count; exit_code = $null; error_type = $null; error_hresult = $null; unavailable_nodes = 0
        phases = [ordered]@{spawned=$false; started=$false; owner_verified=$false; assemblies_loaded=$false;
            window_resolved=$false; query_started=$false; query_completed=$false; completed=$false; failed=$false; timed_out=$false}
    }
    $Diagnostics.attempts.Add($attempt)
    if ($Diagnostics.attempts.Count -gt 20) { $Diagnostics.attempts.RemoveAt(0) }
    return $attempt
}
function Set-FixtureViewDiagnostics([System.Collections.IDictionary]$Attempt, [string]$Stderr) {
    foreach ($line in $Stderr.Split([char]10)) {
        $line = $line.Trim()
        if (@('started', 'owner_verified', 'assemblies_loaded', 'window_resolved',
            'query_started', 'query_completed', 'completed', 'failed') -ccontains $line) { $Attempt.phases[$line] = $true }
        elseif ($line -ceq 'node_unavailable') { $Attempt.unavailable_nodes++ }
        elseif ($line -cmatch '^error_type:([A-Za-z_][A-Za-z0-9_.+]{0,199})$') {
            $Attempt.error_type = $Matches[1]
            $Attempt.error_hresult = $null
        }
        elseif ($line -cmatch '^error_hresult:(0x[0-9A-F]{8})$') { $Attempt.error_hresult = $Matches[1] }
        # Never retain unrecognized stderr, provider messages, stacks or UI text.
    }
}
function Test-FixtureViewResult {
    param([System.Collections.IDictionary]$Attempt, [string]$Output, [System.Collections.IDictionary]$Observed)
    # A failed read cannot leave the preceding attempt's readiness evidence behind.
    $Observed.state = @{}
    if ($Attempt.phases.timed_out) { throw 'Native UI Automation probe exceeded its deadline' }
    if ($Attempt.exit_code -eq 3) { return $false }
    if ($Attempt.exit_code -ne 0) { throw 'Native UI Automation readiness probe failed' }
    $Observed.state = $Output | ConvertFrom-Json
    return @('webContent', 'authenticated', 'setup', 'configurationLoaded', 'saveEnabled', 'bounded').Where({
        $Observed.state.$_ -isnot [bool] -or $Observed.state.$_ -ne $true
    }).Count -eq 0
}
function Test-FixtureTransportFailure([Exception]$Exception) {
    # HTTP status and invalid JSON/schema are contract failures, not readiness.
    for ($cause = $Exception; $null -ne $cause; $cause = $cause.InnerException) {
        if ($cause.GetType().FullName -eq 'Microsoft.PowerShell.Commands.HttpResponseException' -or
            ($cause.PSObject.Properties['StatusCode'] -and $null -ne $cause.StatusCode)) { return $false }
    }
    for ($cause = $Exception; $null -ne $cause; $cause = $cause.InnerException) {
        if ($cause -is [OperationCanceledException] -or $cause -is [TimeoutException] -or $cause -is [Net.Http.HttpRequestException] -or
            $cause -is [Net.Sockets.SocketException]) { return $true }
    }
    return $false
}
function Get-FixturePollCount {
    param([string]$Endpoint, [DateTime]$Deadline, [System.Collections.IDictionary]$Diagnostics,
        [scriptblock]$Read = { param($Url, $Timeout) Invoke-RestMethod $Url -TimeoutSec $Timeout -NoProxy },
        [scriptblock]$Now = { [DateTime]::UtcNow })
    # A request's timeout fits the caller's original readiness budget. No nested deadline.
    $seconds = [int][Math]::Floor(($Deadline - (& $Now)).TotalSeconds)
    if ($seconds -lt 1) { return $null }
    $Diagnostics.attempts++
    try { $state = & $Read "$Endpoint/_fixture/status" ([Math]::Min(3, $seconds)) }
    catch {
        if (-not (Test-FixtureTransportFailure $_.Exception)) { throw }
        $Diagnostics.transient_failures++
        $Diagnostics.last_transport_error_type = $_.Exception.GetType().FullName
        return $null
    }
    if ($null -eq $state -or -not $state.PSObject.Properties['requests'] -or
        -not $state.PSObject.Properties['violations'] -or $state.requests -isnot [array] -or
        $state.violations -isnot [array]) { throw 'Invalid fixture status response' }
    if (@($state.violations).Count) { throw 'Fixture received a mutation or invalid credential' }
    foreach ($request in $state.requests) {
        if ($null -eq $request -or -not $request.PSObject.Properties['path'] -or
            -not $request.PSObject.Properties['method'] -or $request.path -isnot [string] -or
            $request.method -cne 'GET') { throw 'Invalid fixture request record' }
    }
    return @($state.requests | Where-Object { $_.path -eq '/api/v1/events' }).Count
}
function Invoke-FixtureCleanupStep([string]$Name, [scriptblock]$Action, [System.Collections.IList]$Failures) {
    try { & $Action | Out-Null; return $true }
    catch {
        $null = $Failures.Add([ordered]@{ step = $Name; error = $_.Exception.Message; type = $_.Exception.GetType().FullName })
        return $false
    }
}
function Remove-FixturePath {
    param([string]$Path, [DateTime]$Deadline, [System.Collections.IDictionary]$Diagnostics,
        [scriptblock]$Exists = { param($Value) Test-Path -LiteralPath $Value },
        [scriptblock]$Remove = { param($Value) Remove-Item -LiteralPath $Value -Recurse -Force },
        [scriptblock]$Now = { [DateTime]::UtcNow },
        [scriptblock]$Sleep = { param($Milliseconds) Start-Sleep -Milliseconds $Milliseconds })
    while (& $Exists $Path) {
        $Diagnostics.attempts++
        try { & $Remove $Path; return }
        catch {
            $retry = $false
            for ($cause = $_.Exception; $null -ne $cause; $cause = $cause.InnerException) {
                if ($cause -is [IO.IOException] -or $cause -is [UnauthorizedAccessException]) { $retry = $true }
            }
            if (-not $retry -or (& $Now) -ge $Deadline) { throw }
            $Diagnostics.retries++
            $remaining = ($Deadline - (& $Now)).TotalMilliseconds
            if ($remaining -gt 0) { & $Sleep ([int][Math]::Min(150, $remaining)) }
        }
    }
}
function Get-FixtureRegistration {
    param([string]$RegistryRoot, [string]$ProductName,
        [scriptblock]$Enumerate = { param($Root) Get-ChildItem -LiteralPath $Root -ErrorAction Stop },
        [scriptblock]$Read = { param($Path) Get-ItemProperty -LiteralPath $Path -ErrorAction Stop })
    # NSIS removes its registration asynchronously. Enumeration is a snapshot:
    # a key can disappear before its properties are read. Only absence is normal;
    # access denial and every other provider failure must still fail the fixture.
    try { $keys = @(& $Enumerate $RegistryRoot) }
    catch [System.Management.Automation.ItemNotFoundException] { return }
    foreach ($key in $keys) {
        try { $properties = & $Read $key.PSPath }
        catch [System.Management.Automation.ItemNotFoundException] { continue }
        if ($properties.PSObject.Properties['DisplayName'] -and $properties.DisplayName -eq $ProductName) {
            $properties
        }
    }
}
