# Windows Fleet process adapter

This implements the OS ownership boundary of [the Fleet port contract](fleet-rust.md), not the
supervisor, migration UI or installed parity. The public source and synthetic tests define the ownership contract. No private inventory, credential, host or service
configuration is copied. The module is not registered as a Tauri command.

## Interfaces and authority

`desktop/fleet-core/src/process_adapter.rs` supplies `ProcessSnapshot`, `TunnelRecord`,
`MonitorPointer`, `StopMode` and a held-process decision algorithm. `windows.rs` supplies:

| Native-only interface | Behavior |
| --- | --- |
| `current_login()` | SID and login session from the current process token |
| `WindowsProcess::observe(pid)` | Read a retained process handle; return complete evidence, proven absence, or an error |
| `WindowsProcess::from_child(&Child)` | Duplicate the retained launch handle, including for rollback if initial record publication fails |
| `process_state(pid)` | Birth/liveness only; inaccessible or incomplete evidence is `Unknown` |
| `MonitorMutex::try_acquire()` | Same `Global\BatFleetMonitor_<SID>` as PowerShell; occupied returns `None`, failures refuse |
| `MonitorMutex::stop_tunnel(record, mode)` | Current-login check, exact stored identity and final recheck on one held handle; no PID-based kill fallback |
| `PreferenceLock::try_acquire(path)` / `acquire(path)` | Same `fleet-client.json.lock`, OpenOrCreate/ReadWrite/FileShare.None; nonblocking attempt or fixed two-second wait |

The mutex is thread-affine and neither `Send` nor `Sync`. A dedicated supervisor thread retains it;
async work must communicate with that thread, not move the guard. Reentrant acquisition on the same
thread is refused. An abandoned mutex only acquires exclusion: it does not prove an old monitor,
child or record safe to replace. The runtime must still check both BAT directories, old monitor
records, configuration binding and existing listeners before any spawn or publication.

The runtime chooses fixed SSH executable/argv, creates a fresh unpredictable monitor epoch, starts
with a retained child handle and persists both child and parent identity before publishing. On record
write failure it may stop only that retained newly launched child. Normal later stops require
`Owned { current_epoch }`; orphan cleanup requires the recorded parent's incarnation to be proven
ended. Missing, partial, inaccessible or live parent evidence refuses orphan termination. Another
login's owner is readonly even when the account SID matches. No code kills a listener, port match,
substring match or an unrelated process tree.

The final child snapshot includes PID, exact native creation time, executable, parsed argv, epoch,
SID and login session. The first and final snapshots must be equal and match the persisted record.
`TerminateProcess` and the bounded two-second exit wait use that same retained process handle. An
unconfirmed termination returns `STOP_UNCONFIRMED`; callers preserve the record and reconcile,
never declare success or issue a blind repeat. A process can alter its own command line after a read;
this is identity evidence for a trusted fixed child, not an atomic attestation of a hostile process.

The preference lock remains held through caller read/CAS/configuration and owner checks/atomic
replacement. The lock file is never removed or replaced. Its parent directory must already exist
and the path comes from trusted native configuration. Uncooperative editors do not honor this lock;
callers still compare original bytes before replacement and retain the existing documented edit race.

## PowerShell compatibility and evidence sources

Existing flat SSH owner JSON keeps `pid`, `created`, `executable`, `arguments`, `monitor_instance`,
`owner_sid`, `session_id` and optional paired `monitor_pid` / `monitor_created`. Older records with
neither parent field may use an already-read `fleet-monitor.json` pointer only when the exact epoch
matches; a partially present parent never falls back. Decoded duplicate keys, unknown owner fields,
invalid values and inconsistent native/legacy time are rejected. Native writes add a decimal string
`created_filetime`; existing PowerShell's named-field checks ignore this additive field. Native
readers compare it exactly, including the final 100ns digit.

[GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes)
reports FILETIME ticks since 1601. Adding `504911232000000000` yields .NET UTC DateTime ticks since
0001, bounded by `3155378975999999999`. The prior PowerShell source uses Win32_Process.CreationDate:
[CIM datetime](https://learn.microsoft.com/en-us/dotnet/api/system.management.cimtype)
has six fractional digits. The legacy `created` field therefore truncates the final 100ns digit
(`ticks / 10 * 10`), while the native record retains the full kernel timestamp. No local timezone
conversion occurs. A Windows fixture compares this conversion with actual CIM output for its own
temporary child; installed compatibility remains a gate until that fixture and packaging run there.

Executable comes from `QueryFullProcessImageNameW`; SID and session from `OpenProcessToken` /
`GetTokenInformation`, all against the held handle. Command line uses dynamically resolved
`NtQueryInformationProcess`, class 60 (`ProcessCommandLineInformation`), at most 64 KiB plus the
native header. Buffer length, alignment, returned pointer range and UTF-16 are validated before
`CommandLineToArgvW` parses it; executable argv[0] is omitted to match PowerShell. Exact argument
order/case is retained. There is no PEB-memory, substring, shell or WMI subprocess fallback.
Microsoft notes that [this NT API may change](https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntqueryinformationprocess),
and class 60 is not a public stability guarantee. Missing API, unsupported class, changed shape or
unreadable arguments return unknown. Production activation must pass the Windows owned-child tests.

An invalid-parameter OpenProcess failure is only absence when a complete bounded Toolhelp process
snapshot also excludes the PID; other failures remain unknown. A different observed creation time
proves the old incarnation ended, never permission to terminate the process now using that PID.
Legacy monitor script/inventory/index parsing, exact path binding and normal quit requests remain
in the supervisor/migration layer; this module exposes argv without inferring its original CWD.

## Validation limits

### PowerShell fixture startup

All four fleet-core PowerShell fixture launch sites share the test-only runner in
`tests/support/powershell.rs`: the process/CIM and DPAPI unit helpers, configuration
binding oracle, and WScript shortcut oracle. Each script first writes and flushes
`BATC_POWERSHELL_FIXTURE_READY_V1` plus LF. The runner consumes exactly that marker
within 60 seconds, then starts the original work deadline (15 seconds for unit
helpers; the integration oracles retain their existing 30 seconds). Startup expiry
reports `PowerShell fixture did not start within 60 s`; a work timeout is distinct.
Output remains bounded, exit success is required, and every path kills and reaps
only the retained fixture child, then joins its stdout reader. A process-wide mutex
serializes fixture launches across unit modules; each integration test executable
uses the same helper. No separate warm-up launch or retry is involved.

The CIM test acquires that mutex before creating the child it observes. That child
has a bounded 90-second lifetime to cover 60-second startup plus 15-second work;
the existing child guard still kills/reaps it immediately when the test ends.

In PR #127's failing Windows endpoint-oracle run, unit execution began at
07:59:24.036 UTC. CIM failed at 07:59:39.789 and DPAPI at 07:59:39.816 (15.753 and
15.780 seconds later, 27 ms apart); the binary ended with 34 passed, 2 failed and
3 ignored in 22.04 seconds. The later binding oracle passed in a 1.88-second binary,
and the shortcut oracle's binary passed in 7.10 seconds. Combined with #124's
same-process contract-startup diagnosis, this is consistent with parallel cold
PowerShell launches consuming the fixtures' work budgets, rather than a stable
CIM/DPAPI format failure. The log has no pre-script timing trace and does not
measure the contribution of each startup component.

Windows PowerShell 5.1 runs on .NET Framework and reads a
[module analysis cache during command discovery](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_windows_powershell_5.1?view=powershell-5.1#module-analysis-cache).
Cold assembly loading and JIT can add first-use costs; the PowerShell team documents
[assembly precompilation's startup effect](https://devblogs.microsoft.com/powershell/speeding-up-powershell-startup/).
PowerShell 5.1 also submits script blocks to
[AMSI/Defender scanning](https://learn.microsoft.com/en-us/powershell/scripting/security/security-features?view=powershell-7.5#amsi-support).
First scans and concurrent cold loads are plausible contributors, not individually
proven causes in this run. `-NoProfile` does not remove these costs. The readiness
boundary separates interpreter startup from actual fixture work; lazy imports
after the marker still count against the work deadline. The 60-second cap is not
a measured startup guarantee or an increase to the actual work allowance.

### Product PowerShell audit

`discovery.rs::powershell()` is only the Serde default returning
`Backend::Powershell`. Discovery and `windows.rs::powershell_pids()` use native
process evidence; neither starts a shell or has a PowerShell startup deadline.
The sole product PowerShell spawn inside fleet-core is
`windows_migration/native.rs::launch_powershell`; its outstanding cold-start
limitation and required legacy handshake are documented in
[the migration contract](fleet-migration.md#legacy-powershell-cold-start-limitation).
Other product spawns execute fixed SSH, Tailscale, BAT or Dashboard binaries.
Outside fleet-core, #124 already gives the Tauri read-only contract a 60-second
same-process bootstrap followed by its unchanged 10-second request budget;
effectful facade calls retain their total 35-second deadline and unknown-result
semantics.

The Windows tunnel adapter proves local endpoint absence with a positive TCP
`ConnectionRefused` followed by a successful bind. Its connection deadline is three
seconds; timeouts, other errors and an accepted connection still refuse launch.
The actual Windows ephemeral-port oracle (CI run `37912084308`, job
`113759388907`) returned refusal after about two seconds for both never-connected
and previously occupied ports, while the former 100 ms budget timed out despite a
successful bind. This is a production deadline correction, not a relaxed absence
test. The two synchronous checks before tunnel spawn add at most six seconds of
connection waiting per launch on the supervisor thread. Route/readiness probe
deadlines remain independent; no SSH child is spawned by this oracle.

Supervisor ownership scans use the same lexical absolute-path and Windows 8.3
expansion as selection and launch. A short `%TEMP%`/roaming spelling must identify
the same monitor and child receipts as its expanded spelling. Previously, raw scan
paths made an owned child look foreign and lost the expected monitor bytes during
restart (Windows run `37912661759`). Shared normalization preserves the existing
reparse-point checks; it does not canonicalize through links. A temporary fixture
checks equivalent paths through readiness, orphan recovery on restart and owned
shutdown. The early Windows job also runs the complete supervisor fixture target.

Linux executes pure record/decision tests, including denied access, other login, PID reuse during
parent checks, changed final evidence, unknown exit and legacy record recovery. Windows compile
checks include the OS adapter and test source. Windows runtime fixtures use only owned test children,
a synthetic SID mutex and a temporary preference file. They verify retained-handle termination,
CIM birth compatibility, exact argv, PowerShell mutex exclusion and .NET file-lock exclusion. The
fixture-only system PowerShell calls have fixed scripts, a restricted system module path and a
15-second deadline; production does not invoke them.

No live SSH tunnels, real Fleet mutex, real preferences, host topology or remote operations are
changed by tests. Windows runtime/packaged cross-login acceptance, startup migration, DPAPI credential
integration, discovery and supervisor/probe integration are separate gates. Compilation and Linux
mocks do not establish those results.
