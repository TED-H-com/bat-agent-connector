# Real NSIS install/WebView fixture, restricted to a disposable hosted runner.
param([Parameter(Mandatory)][string]$Endpoint, [Parameter(Mandatory)][string]$Evidence)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'windows-installed-support.ps1')
if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true' -or $env:RUNNER_ENVIRONMENT -cne 'github-hosted') {
    throw 'Only disposable GitHub-hosted Windows runners may run this fixture'
}
if ($Endpoint -notmatch '^http://127\.0\.0\.1:[0-9]+$') { throw 'Loopback fixture required' }
$config = Get-Content src-tauri/tauri.conf.json -Raw | ConvertFrom-Json
$configRoot = Join-Path ([Environment]::GetFolderPath('ApplicationData')) $config.identifier
$dataRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) $config.identifier
$registryRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
function Get-Registration {
    Get-FixtureRegistration $registryRoot $config.productName
}
foreach ($path in @($configRoot, $dataRoot)) {
    if (Test-Path -LiteralPath $path) { throw "Refusing existing app state: $path" }
}
if (@(Get-Registration).Count -ne 0) { throw 'Refusing an existing app installation' }
if (Get-Process -Name better-agent-dashboard -ErrorAction SilentlyContinue) { throw 'Refusing an existing Dashboard process' }
$installers = @(Get-ChildItem src-tauri/target/release/bundle/nsis/*.exe)
if ($installers.Count -ne 1) { throw 'Exactly one NSIS package required' }
$installer = $installers[0].FullName
$built = (Resolve-Path src-tauri/target/release/better-agent-dashboard.exe).Path
$installRoot = Join-Path $env:RUNNER_TEMP ('batc-installed-' + [guid]::NewGuid().ToString('N'))
$installDir = Join-Path $installRoot 'Dashboard install'
$binary = Join-Path $installDir 'better-agent-dashboard.exe'
New-Item -ItemType Directory -Path $installRoot | Out-Null
New-Item -ItemType Directory -Path $Evidence -Force | Out-Null
$receipt = [ordered]@{
    status = 'running'; evidence_level = 'native-installed-fixture'; live_accepted = $false
    source_sha = (git rev-parse HEAD); source_tree = (git rev-parse 'HEAD^{tree}')
    runner_os = [Environment]::OSVersion.VersionString; version = $config.version
    installer_sha256 = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    steps = @(); primary_failure = $null; cleanup_errors = @()
    polling = [ordered]@{attempts = 0; transient_failures = 0; last_transport_error_type = $null}
}
function Record-Step([string]$Name) {
    $receipt.steps += $Name
    $receipt | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $Evidence 'result.json') -Encoding utf8
    Write-Host "Passed: $Name"
}
function Wait-Until([scriptblock]$Condition, [string]$Failure, [int]$Seconds = 30) {
    Wait-FixtureUntil $Condition $Failure $Seconds
}
function Run-Installer([string]$Path, [string]$Arguments) {
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -PassThru
    if (-not $process.WaitForExit(60000)) {
        $process.Kill()
        throw 'Owned installer exceeded its deadline'
    }
    if ($process.ExitCode -ne 0) { throw "Installer exit code: $($process.ExitCode)" }
}
function Start-Dashboard([bool]$Managed = $false) {
    $info = [Diagnostics.ProcessStartInfo]::new($binary)
    $info.UseShellExecute = $false
    $info.WorkingDirectory = $installDir
    if ($Managed) { $null = $info.Environment.Remove('BATC_DESKTOP_TOKEN') }
    else { $info.Environment['BATC_DESKTOP_TOKEN'] = 'fixture-native-token' }
    [Diagnostics.Process]::Start($info)
}
function Stop-Owned([Diagnostics.Process]$Process) {
    if ($null -ne $Process -and -not $Process.HasExited) {
        $Process.Kill()
        if (-not $Process.WaitForExit(10000)) { throw 'Owned app did not exit' }
    }
}
function Poll-Count([DateTime]$Deadline) {
    Get-FixturePollCount $Endpoint $Deadline $receipt.polling
}
Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class DashboardWindows {
    private delegate bool EnumProc(IntPtr window, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc callback, IntPtr data);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetWindowText(IntPtr window, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr window, uint message, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll", EntryPoint="SendMessageTimeoutW", SetLastError=true)]
    private static extern IntPtr SendMessageTimeout(IntPtr window, uint message, UIntPtr w, IntPtr l, uint flags, uint timeout, out UIntPtr result);
    [DllImport("kernel32.dll")] private static extern void SetLastError(uint error);
    public sealed class ResponseSample {
        public string State;
        public long ElapsedMilliseconds;
        public int ErrorCode;
    }
    public static ResponseSample SampleResponse(IntPtr window, uint expected, uint timeout) {
        if (timeout == 0 || timeout > 200) throw new ArgumentOutOfRangeException();
        var clock = System.Diagnostics.Stopwatch.StartNew();
        uint owner;
        var sample = new ResponseSample();
        if (GetWindowThreadProcessId(window, out owner) == 0 || owner != expected) sample.State = "owner_changed";
        else {
            UIntPtr result;
            SetLastError(0);
            // WM_NULL, SMTO_BLOCK | SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT.
            // No UIA call, HWND_BROADCAST or timeout extension for a live thread.
            IntPtr sent = SendMessageTimeout(window, 0, UIntPtr.Zero, IntPtr.Zero, 0x23, timeout, out result);
            sample.ErrorCode = sent == IntPtr.Zero ? Marshal.GetLastWin32Error() : 0;
            sample.State = sent != IntPtr.Zero ? "responsive" : sample.ErrorCode == 1460 ? "hung" : "failed";
        }
        sample.ElapsedMilliseconds = clock.ElapsedMilliseconds;
        return sample;
    }
    [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
    public static IntPtr[] Find(uint process) {
        var found = new List<IntPtr>();
        EnumWindows((window, data) => {
            uint owner; GetWindowThreadProcessId(window, out owner);
            var title = new StringBuilder(256); GetWindowText(window, title, title.Capacity);
            if (owner == process && title.ToString() == "Better Agent Dashboard") found.Add(window);
            return true;
        }, IntPtr.Zero);
        return found.ToArray();
    }
}
'@
Add-Type -AssemblyName System.Drawing
function Save-Window([IntPtr]$Window, [string]$Name) {
    $rect = [DashboardWindows+Rect]::new()
    if (-not [DashboardWindows]::GetWindowRect($Window, [ref]$rect)) { throw 'Window bounds unavailable' }
    $bitmap = [Drawing.Bitmap]::new($rect.Right - $rect.Left, $rect.Bottom - $rect.Top)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
        $bitmap.Save((Join-Path $Evidence $Name), [Drawing.Imaging.ImageFormat]::Png)
    } finally { $graphics.Dispose(); $bitmap.Dispose() }
}
function Build-ManagedViewProbe {
    $framework = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319'
    $info = [Diagnostics.ProcessStartInfo]::new((Join-Path $framework 'csc.exe'))
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($arg in @('/nologo', '/target:exe', '/platform:x64', '/codepage:65001', "/out:$managedViewProbe",
        "/reference:$(Join-Path $framework 'WPF/UIAutomationClient.dll')",
        "/reference:$(Join-Path $framework 'WPF/UIAutomationTypes.dll')",
        "/reference:$(Join-Path $framework 'WPF/WindowsBase.dll')",
        (Join-Path $PWD 'tests/windows-managed-view.cs'))) { $info.ArgumentList.Add($arg) }
    $compiler = [Diagnostics.Process]::Start($info)
    try {
        $output = $compiler.StandardOutput.ReadToEndAsync()
        $errors = $compiler.StandardError.ReadToEndAsync()
        if (-not $compiler.WaitForExit(60000)) {
            $compiler.Kill()
            if (-not $compiler.WaitForExit(5000)) { throw 'Owned UIA compiler did not exit after its deadline' }
            throw 'UIA fixture compilation exceeded its deadline'
        }
        if ($compiler.ExitCode -ne 0) {
            throw "UIA fixture compilation failed: $($output.GetAwaiter().GetResult())$($errors.GetAwaiter().GetResult())"
        }
    } finally { $compiler.Dispose() }
}
function Wait-ManagedView([Diagnostics.Process]$Process, [IntPtr]$Window, [string]$Name, [switch]$RoleChoice) {
    $observed = @{state = @{}}
    $diagnostics = [ordered]@{attempt_count = 0; attempts = [Collections.Generic.List[object]]::new()}
    try {
        Wait-Until {
            param($Deadline)
            if ($Process.HasExited) { throw 'Managed app exited before WebView readiness' }
            if ((Get-FixtureViewTimeout $Deadline) -eq 0) { return $false }
            $attempt = New-FixtureViewAttempt $diagnostics
            $observed.state = @{}
            $attempt.window_response = Get-FixtureWindowResponse $Window $Process.Id $Deadline
            if ((Get-FixtureViewTimeout $Deadline) -eq 0) { return $false }
            # The already compiled MTA client avoids PowerShell/STA startup and
            # retains process isolation if an accessibility provider stops responding.
            $info = [Diagnostics.ProcessStartInfo]::new($managedViewProbe)
            $info.UseShellExecute = $false
            $info.CreateNoWindow = $true
            $info.RedirectStandardOutput = $true
            $info.RedirectStandardError = $true
            foreach ($arg in @($Window.ToInt64().ToString(), $Process.Id.ToString())) { $info.ArgumentList.Add($arg) }
            $info.ArgumentList.Add('--elapsed-origin')
            $info.ArgumentList.Add([Diagnostics.Stopwatch]::GetTimestamp().ToString([Globalization.CultureInfo]::InvariantCulture))
            $processClock = [Diagnostics.Stopwatch]::StartNew()
            $probe = [Diagnostics.Process]::Start($info)
            $attempt.phases.spawned = $true
            try {
                $output = $probe.StandardOutput.ReadToEndAsync()
                $errors = $probe.StandardError.ReadToEndAsync()
                if (-not $probe.WaitForExit((Get-FixtureViewTimeout $Deadline))) {
                    $attempt.phases.timed_out = $true
                    $probe.Kill()
                    if (-not $probe.WaitForExit(5000)) { throw 'Owned UIA probe did not exit after its deadline' }
                }
                $attempt.exit_code = $probe.ExitCode
                Set-FixtureViewDiagnostics $attempt ($errors.GetAwaiter().GetResult())
                # Exit 3 is a failed read, not readiness evidence. The existing
                # Wait-Until loop retries it with the very same 30 s deadline.
                $ready = Test-FixtureViewResult $attempt ($output.GetAwaiter().GetResult()) $observed
                if ($attempt.exit_code -eq 3) { return $false }
                if ($RoleChoice) {
                    return $observed.state.bounded -eq $true -and $observed.state.roleChoice -eq $true
                }
                if ($ready -and $observed.state.roleChoice -ne $false) { throw 'Role chooser reappeared after selection' }
                return $ready
            } finally {
                $attempt.process_elapsed_ms = [Math]::Min(60000, $processClock.ElapsedMilliseconds)
                $probe.Dispose()
            }
        } 'Managed WebView did not render authenticated first-run configuration'
    } catch {
        # Preserve the real native surface before app cleanup, even when UIA stalls.
        try { Save-Window $Window "$Name-failed.png" } catch { Write-Warning 'Readiness failure screenshot was unavailable' }
        throw
    } finally {
        $observed.state | ConvertTo-Json | Set-Content (Join-Path $Evidence "$Name-readiness.json") -Encoding utf8
        $diagnostics | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $Evidence "$Name-probe-phases.json") -Encoding utf8
    }
}
function Save-ManagedStartupView([Diagnostics.Process]$Process, [IntPtr]$Window) {
    # One read only, outside the success/retry path. Never repeat the role action.
    $state = @{available = $false}
    $info = [Diagnostics.ProcessStartInfo]::new($managedViewProbe)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($arg in @($Window.ToInt64().ToString(), $Process.Id.ToString())) { $info.ArgumentList.Add($arg) }
    $probe = $null
    try {
        $probe = [Diagnostics.Process]::Start($info)
        $output = $probe.StandardOutput.ReadToEndAsync()
        $errors = $probe.StandardError.ReadToEndAsync()
        if (-not $probe.WaitForExit(8000)) {
            $probe.Kill()
            if (-not $probe.WaitForExit(5000)) { throw 'Owned diagnostic UIA probe did not exit' }
        }
        if ($probe.ExitCode -eq 0) { $state = $output.GetAwaiter().GetResult() | ConvertFrom-Json }
    } catch { Write-Warning 'Managed startup UIA diagnostic was unavailable' }
    finally {
        if ($null -ne $probe) { $probe.Dispose() }
        $state | ConvertTo-Json | Set-Content (Join-Path $Evidence 'managed-startup-failed-readiness.json') -Encoding utf8
    }
}
function Select-ProviderRole([Diagnostics.Process]$Process, [IntPtr]$Window) {
    # Invoke once, outside the readiness retry loop. An uncertain action is fatal.
    $info = [Diagnostics.ProcessStartInfo]::new($managedViewProbe)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($arg in @($Window.ToInt64().ToString(), $Process.Id.ToString(), 'select-provider')) { $info.ArgumentList.Add($arg) }
    $probe = [Diagnostics.Process]::Start($info)
    try {
        $output = $probe.StandardOutput.ReadToEndAsync()
        $errors = $probe.StandardError.ReadToEndAsync()
        if (-not $probe.WaitForExit(8000)) {
            $probe.Kill()
            if (-not $probe.WaitForExit(5000)) { throw 'Owned provider selection probe did not exit' }
            throw 'Provider selection exceeded its deadline'
        }
        $errors.GetAwaiter().GetResult() | Set-Content (Join-Path $Evidence 'provider-selection-phases.log') -Encoding utf8
        if ($probe.ExitCode -ne 0) { throw 'Owned provider role action failed' }
        $selected = $output.GetAwaiter().GetResult() | ConvertFrom-Json
        if ($selected.role -cne 'provider' -or $selected.pressed -ne $true -or $selected.matches -ne 1) {
            throw 'Provider role action did not confirm exactly one button press'
        }
        Record-Step 'Clean managed first-run selected provider (Provide a local workspace / 提供本機工作區) through the owned UI Automation button'
    } finally { $probe.Dispose() }
}
$app = $null
$second = $null
$reopened = $null
$primaryFailure = $null
$cleanupFailures = [Collections.Generic.List[object]]::new()
$managedViewProbe = Join-Path $installRoot 'managed-view-probe.exe'
try {
    Build-ManagedViewProbe
    # NSIS /D must be last and unquoted, even when the path contains spaces.
    Run-Installer $installer "/S /D=$installDir"
    if (-not (Test-Path -LiteralPath $binary)) { throw 'Installed executable missing' }
    $proof = Get-Content (Join-Path $Evidence 'bundle-proof.json') -Raw | ConvertFrom-Json
    $builtHash = (Get-FileHash $built -Algorithm SHA256).Hash.ToLowerInvariant()
    $installedHash = (Get-FileHash $binary -Algorithm SHA256).Hash.ToLowerInvariant()
    $receipt.built_binary_sha256 = $builtHash
    $receipt.installed_binary_sha256 = $installedHash
    $receipt.bundle_proof = $proof
    if ($builtHash -cne $proof.built_sha256 -or $installedHash -cne $proof.expected_installed_sha256) {
        throw 'Installed bytes differ from the reviewed build with its exact NSIS bundle marker'
    }
    $version = (Get-Item -LiteralPath $binary).VersionInfo.ProductVersion
    if ($version -cne $config.version) { throw "Installed PE version mismatch: $version" }
    $registered = @(Get-Registration)
    if ($registered.Count -ne 1 -or $registered[0].DisplayVersion -cne $config.version) {
        throw 'Installed version registration mismatch'
    }
    foreach ($name in @('LICENSE', 'COPYRIGHT')) {
        $source = (Get-FileHash "vendor/glib-0.18.5/$name").Hash
        if ((Get-FileHash (Join-Path $installDir "third-party/glib/$name")).Hash -cne $source) {
            throw 'Packaged third-party notice differs'
        }
    }
    Record-Step 'NSIS install, executable digest, PE/registered version and license resources'
    New-Item -ItemType Directory -Path $configRoot | Out-Null
    $central = Join-Path $configRoot 'central.json'
    @{ endpoint = "$Endpoint/"; expected_actor = 'fixture-operator'; contract_version = '2026-10-08' } |
        ConvertTo-Json | Set-Content $central -Encoding utf8
    $centralHash = (Get-FileHash $central).Hash
    $app = Start-Dashboard
    Wait-Until { param($Deadline) if ($app.HasExited) { throw 'Installed app exited before bootstrap' }; (Poll-Count $Deadline) -gt 0 } 'Installed WebView did not poll'
    $windows = @([DashboardWindows]::Find($app.Id))
    if ($windows.Count -ne 1 -or -not [DashboardWindows]::IsWindowVisible($windows[0])) {
        throw 'Expected exactly one visible Dashboard window owned by the installed app'
    }
    $window = $windows[0]
    Start-Sleep -Milliseconds 700
    Save-Window $window 'installed.png'
    Record-Step 'Installed WebView authenticated, bootstrapped and polled the loopback fixture'
    if (-not [DashboardWindows]::PostMessage($window, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)) { throw 'WM_CLOSE failed' }
    Wait-Until { -not [DashboardWindows]::IsWindowVisible($window) } 'Close did not hide window'
    if ($app.HasExited) { throw 'Closing the window terminated the app' }
    Record-Step 'WM_CLOSE hid the window and retained the original process'
    $second = Start-Dashboard
    if (-not $second.WaitForExit(10000) -or $second.ExitCode -ne 0) { throw 'Second launch did not hand off' }
    Wait-Until { [DashboardWindows]::IsWindowVisible($window) } 'Second launch did not restore original window'
    $windows = @([DashboardWindows]::Find($app.Id))
    if ($windows.Count -ne 1 -or $windows[0] -ne $window -or $app.HasExited) { throw 'Original window/process changed' }
    Record-Step 'Second invocation exited successfully and restored the same window/process'
    # This is process restart recovery, not a physical tray Quit interaction.
    Stop-Owned $app
    $restart = @{before = $null}
    Wait-Until { param($Deadline) $count = Poll-Count $Deadline; if ($null -eq $count) { return $false }; $restart.before = $count; return $true } 'Fixture status unavailable before relaunch'
    $reopened = Start-Dashboard
    Wait-Until { param($Deadline) if ($reopened.HasExited) { throw 'Reopened app exited' }; (Poll-Count $Deadline) -gt $restart.before } 'Reopened WebView did not resume polling'
    $windows = @([DashboardWindows]::Find($reopened.Id))
    if ($windows.Count -ne 1 -or -not [DashboardWindows]::IsWindowVisible($windows[0])) { throw 'Reopened window missing' }
    if ((Get-FileHash $central).Hash -cne $centralHash) { throw 'Reopen changed central configuration' }
    Save-Window $windows[0] 'reopened.png'
    Record-Step 'Owned process termination/relaunch resumed WebView polling and preserved configuration'
    Stop-Owned $reopened
    # Both state directories were absent before this fixture. The external-client
    # phase persisted a role, so reset the entire owned profile for a clean launch.
    $profileDeadline = [DateTime]::UtcNow.AddSeconds(15)
    $receipt.clean_profile_removal = [ordered]@{attempts=0; retries=0}
    foreach ($path in @($configRoot, $dataRoot)) { Remove-FixturePath $path $profileDeadline $receipt.clean_profile_removal }
    $app = Start-Dashboard $true
    Wait-Until { @([DashboardWindows]::Find($app.Id)).Count -eq 1 } 'Managed first-run window missing'
    $window = @([DashboardWindows]::Find($app.Id))[0]
    Wait-ManagedView $app $window 'managed-role-choice' -RoleChoice
    Select-ProviderRole $app $window
    $managedIdentity = node tests/managed-installed-probe.mjs wait $dataRoot (Join-Path $Evidence 'managed-startup-failure.json')
    if ($LASTEXITCODE -ne 0) {
        # Capture the owned native error surface before cleanup, even when the
        # central never reaches bootstrap and the UIA readiness probe cannot run.
        try {
            $failedWindows = @([DashboardWindows]::Find($app.Id))
            if ($failedWindows.Count -eq 1) {
                Save-ManagedStartupView $app $failedWindows[0]
                Save-Window $failedWindows[0] 'managed-startup-failed.png'
            }
        } catch { Write-Warning 'Managed startup failure screenshot was unavailable' }
        throw 'Managed clean-profile first-run failed'
    }
    $servicePointer = Join-Path $dataRoot 'managed-central/state/task-service.json'
    $serviceBefore = (Get-FileHash -LiteralPath $servicePointer).Hash
    Wait-ManagedView $app $window 'managed-first-run'
    Save-Window $window 'managed-first-run.png'
    if (-not [DashboardWindows]::PostMessage($window, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)) { throw 'Managed WM_CLOSE failed' }
    Wait-Until { -not [DashboardWindows]::IsWindowVisible($window) } 'Managed close did not hide window'
    $closedIdentity = node tests/managed-installed-probe.mjs probe $dataRoot
    if ($LASTEXITCODE -ne 0 -or $closedIdentity -cne $managedIdentity) { throw 'Managed identity changed after close' }
    Stop-Owned $app
    $backgroundIdentity = node tests/managed-installed-probe.mjs probe $dataRoot
    if ($LASTEXITCODE -ne 0 -or $backgroundIdentity -cne $managedIdentity) { throw 'Central did not survive UI process termination' }
    $reopened = Start-Dashboard $true
    $reopenedIdentity = node tests/managed-installed-probe.mjs wait $dataRoot
    if ($LASTEXITCODE -ne 0 -or $reopenedIdentity -cne $managedIdentity) { throw 'Managed relaunch changed installation identity' }
    Wait-Until { @([DashboardWindows]::Find($reopened.Id)).Count -eq 1 } 'Managed reopened window missing'
    $reopenedWindow = @([DashboardWindows]::Find($reopened.Id))[0]
    Wait-ManagedView $reopened $reopenedWindow 'managed-reopened'
    if ((Get-FileHash -LiteralPath $servicePointer).Hash -cne $serviceBefore) { throw 'Relaunch replaced the running managed service' }
    Save-Window $reopenedWindow 'managed-reopened.png'
    Stop-Owned $reopened
    node tests/managed-installed-probe.mjs stop $dataRoot
    if ($LASTEXITCODE -ne 0) { throw 'Managed fixture service cleanup failed' }
    Record-Step 'Provider first-run and relaunch rendered authenticated, enabled setup via UI Automation; chooser stayed absent; same identity and service across close and relaunch; central survived UI termination'
    $uninstaller = Join-Path $installDir 'uninstall.exe'
    Run-Installer $uninstaller '/S'
    Wait-Until { -not (Test-Path -LiteralPath $binary) -and @(Get-Registration).Count -eq 0 } 'Uninstall left binary or registration'
    Record-Step 'NSIS uninstall removed the installed executable and version registration'
    $receipt.status = 'passed'
} catch {
    $primaryFailure = $_
    $receipt.status = 'failed'
    $receipt.error = $_.Exception.Message
    $receipt.primary_failure = [ordered]@{error = $_.Exception.Message; type = $_.Exception.GetType().FullName}
    # Preserve the original failure even if process cleanup is later interrupted.
    try { $receipt | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $Evidence 'result.json') -Encoding utf8 }
    catch { Write-Warning 'Primary fixture failure receipt could not be saved' }
} finally {
    $appsStopped = $true
    foreach ($owned in @(@{name='second'; process=$second}, @{name='reopened'; process=$reopened}, @{name='app'; process=$app})) {
        if (-not (Invoke-FixtureCleanupStep "stop_$($owned.name)" { Stop-Owned $owned.process } $cleanupFailures)) { $appsStopped = $false }
    }
    $serviceStopped = Invoke-FixtureCleanupStep 'stop_managed_service' {
        node tests/managed-installed-probe.mjs stop $dataRoot
        if ($LASTEXITCODE -ne 0) { throw 'Managed fixture service cleanup failed' }
    } $cleanupFailures
    $removal = [ordered]@{attempts=0; retries=0}
    $receipt.cleanup = [ordered]@{apps_stopped=$appsStopped; service_stopped=$serviceStopped; data_removed=$false; removal=$removal}
    # Exact app handles only: never kill a WebView/app tree (the central must survive UI termination).
    # WebView releases its files naturally. A sharing lock gets one bounded cleanup window.
    if ($appsStopped -and $serviceStopped) {
        $cleanupDeadline = [DateTime]::UtcNow.AddSeconds(15)
        $removed = $true
        foreach ($ownedPath in @(@{name='config'; path=$configRoot}, @{name='data'; path=$dataRoot}, @{name='probe'; path=$managedViewProbe})) {
            if (-not (Invoke-FixtureCleanupStep "remove_$($ownedPath.name)" {
                Remove-FixturePath $ownedPath.path $cleanupDeadline $removal
            } $cleanupFailures)) { $removed = $false }
        }
        $receipt.cleanup.data_removed = $removed
    } else {
        $null = $cleanupFailures.Add([ordered]@{step='remove_private_data'; error='Cleanup blocked: owned app/service stop is unproven; data retained'; type='CleanupBlocked'})
    }
    $receipt.cleanup_errors = @($cleanupFailures.ToArray())
    if ($cleanupFailures.Count) {
        $receipt.status = 'failed'
        if ($null -eq $primaryFailure) { $receipt.error = 'Fixture cleanup failed' }
    }
    try { $receipt | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $Evidence 'result.json') -Encoding utf8 }
    catch {
        $null = $cleanupFailures.Add([ordered]@{step='save_receipt'; error=$_.Exception.Message; type=$_.Exception.GetType().FullName})
        Write-Warning 'Final fixture receipt could not be saved; earlier failure remains primary'
    }
}
if ($null -ne $primaryFailure) { throw $primaryFailure }
if ($cleanupFailures.Count) { throw 'Fixture cleanup failed; see result.json cleanup_errors' }
