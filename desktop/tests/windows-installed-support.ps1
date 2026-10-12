# Fixture-only helpers. Dot-sourcing does not start or stop a process.
function Wait-FixtureUntil([scriptblock]$Condition, [string]$Failure, [int]$Seconds = 30) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (& $Condition $deadline) { return }
        $remaining = ($deadline - [DateTime]::UtcNow).TotalMilliseconds
        if ($remaining -gt 0) { Start-Sleep -Milliseconds ([int][Math]::Min(150, $remaining)) }
    }
    throw $Failure
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
        [scriptblock]$Read = { param($Url, $Timeout) Invoke-RestMethod $Url -TimeoutSec $Timeout -NoProxy })
    # A request's timeout fits the caller's original readiness budget. No nested deadline.
    $seconds = [int][Math]::Floor(($Deadline - [DateTime]::UtcNow).TotalSeconds)
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
        [scriptblock]$Remove = { param($Value) Remove-Item -LiteralPath $Value -Recurse -Force })
    while (& $Exists $Path) {
        $Diagnostics.attempts++
        try { & $Remove $Path; return }
        catch {
            $retry = $false
            for ($cause = $_.Exception; $null -ne $cause; $cause = $cause.InnerException) {
                if ($cause -is [IO.IOException] -or $cause -is [UnauthorizedAccessException]) { $retry = $true }
            }
            if (-not $retry -or [DateTime]::UtcNow -ge $Deadline) { throw }
            $Diagnostics.retries++
            $remaining = ($Deadline - [DateTime]::UtcNow).TotalMilliseconds
            if ($remaining -gt 0) { Start-Sleep -Milliseconds ([int][Math]::Min(150, $remaining)) }
        }
    }
}
