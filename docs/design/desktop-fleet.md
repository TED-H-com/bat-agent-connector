# Desktop Fleet adapter (R03, PowerShell stage)

This document describes the optional legacy PowerShell adapter. For a new standalone
Rust installation, use the [public configuration guide](fleet-configuration.md);
its runtime and templates are shipped in this repository.

The Rust client calls the installed Fleet Kit's fixed `client/fleet-desktop.ps1`
entry point. That Kit remains the only owner of monitors and tunnels. This slice
adds native transport and connection settings in the shared frontend. Rust ownership
parity and actual Windows acceptance remain separate work.

Place `fleet.json` beside `central.json` in the app configuration directory, using
[`desktop/fleet.example.json`](../../desktop/fleet.example.json). `kit_root` is the
absolute local root of a reviewed Kit installation containing the desktop facade.
It is startup configuration, never a WebView parameter. The adapter reuses that
Kit's validated default inventory/profile pair and preference store; it creates
no duplicate host inventory. Missing configuration leaves central Dashboard use
available. Fleet control is Windows-only.

`fleet_availability` returns configuration/platform availability without paths.
`fleet_request` accepts only contract, status, configuration validation, selection,
ensure-monitor and quit-owned actions. Selection supplies a complete logical-ID
list, the observed selection revision and monitor epoch. All mutations include the
reviewed configuration fingerprint; the Kit checks it again from disk before acting.
Quit supplies the observed epoch; it accepts no PID. The Kit performs ownership and CAS checks.
Ensuring a monitor does not open BAT, launch a browser or run Kit self-update.

Each call first verifies the fixed `desktop-facade-v1` contract. Rust chooses
Windows PowerShell using the OS system-directory API, invokes the fixed facade
with the same fixed process execution-policy option used by the Kit launcher,
and sends one UTF-8 JSON request over stdin. Actions use `-File`; the read-only
contract uses the embedded native bootstrap via UTF-16LE `-EncodedCommand`, with
the trusted facade path passed as environment data, never interpolated source.
UNC/device paths and mapped network drives
are refused before and after canonicalization. Validated local paths are converted
to ordinary drive spelling for PowerShell 5.1; overlong installation paths are refused.
No arbitrary executable,
script, arguments, filesystem path, URL, headers or credentials cross IPC.
The child inherits only selected Windows identity paths. `PSModulePath` is explicitly the
OS-selected PowerShell's `Modules` directory; ambient user or PowerShell 7 module paths
are not inherited. Central tokens and proxy
credentials are not inherited. Only the packaged main window has command access,
with the same native origin checks as the central transport.

Requests are at most 64 KiB; stdout is at most 256 KiB; stderr is discarded.
Contract checks allow up to 60 seconds for cold PowerShell startup in the same
adapter process. The bootstrap imports Utility and Management by absolute manifest
paths under `$PSHOME/Modules`, exercises JSON conversion and a bounded read of the
facade, then flushes a fixed readiness marker. Rust accepts exactly that marker
before sending the single contract request; its ten-second deadline starts then
and covers stdin, the bounded reply and process exit. Missing/malformed readiness
fails closed. The facade's JSON protocol and request identity are unchanged.
Actions keep their total 35-second deadline, including process launch; they get no
startup extension. Installation and lifecycle checks still run between contract
verification and action dispatch.
Only one call runs at a time. On timeout Rust terminates its own facade process,
never a monitor/tunnel or an arbitrary PID. An interrupted action may already have
changed preferences or started a monitor: the client must read status and retain
its reviewed intent before retrying. The bridge does not automatically retry.
Closing or quitting the Dashboard sends no Fleet quit request.

This separates runtime readiness from the request budget, rather than just raising
the ten-second limit. Windows CI for `2bf152d` and PR #116 reached `input-closed`
before that limit expired; identical later launches finished in 1.11/1.74 seconds.
On `3bb4fff` the original timed out before the first fixture marker, and the next
launch also timed out (last marker `output-written`). These observations establish
a cold-process deadline problem in the product transport, not an stdin EOF bug;
they do not identify the precise OS bottleneck. Microsoft documents
[first-use module imports](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_modules?view=powershell-5.1)
and [startup module-analysis cache reads](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_windows_powershell_5.1?view=powershell-5.1#module-analysis-cache).
Module work, assembly/JIT and antivirus scanning remain candidate contributors.
The 60-second startup ceiling is bounded headroom, not a measured startup guarantee;
Windows CI must confirm the first call with this bootstrap. No test-only warm-up,
automatic retry or effect resend is used.

Responses must match the request ID/schema and process outcome. Rust constructs
the returned fields explicitly. Configuration errors expose a count rather than
raw diagnostic details; readiness exposes logical names, labels, bounded status,
observation time and recognized layers. Process identities, raw errors, tokens
and paths are excluded. Freshness and selection application remain separate:
`selection.revision` is desired state, `applied_revision` is the monitor's observed
state, and missing/stale readiness is not readiness success.

The connection page lists the complete configured logical selection even when the
monitor is stopped. It polls local status independently of central authentication.
Read failures pause local controls; other-login owners remain read-only. A reviewed
selection draft retains its original selection revision and epoch in a configuration
namespace. If either changes, it stays visible but cannot apply until the user reviews
the current selection. An uncertain write never resends automatically; a matching
status read settles it. Closing the page stops its poll, with no monitor lifecycle effect.

Synthetic JSON fixtures from the Fleet Kit compatibility implementation are retained
for the native contract tests. They contain no live configuration. Unit tests cover
forbidden IPC inputs, contract/reply mismatch, installation containment, output
filtering, subprocess EOF/bounds, uncertain exits and cancellation. Linux process
fixtures also cover slow readiness, no input before readiness, malformed/missing
markers, independent startup/request deadlines, and owned-child-only termination
without retry. They do not prove Windows ownership or
PowerShell startup behavior. Windows CI calls a self-contained synthetic contract
script through the actual system PowerShell, including installation paths with spaces,
quotes and Unicode. Startup elapsed time and finer decode/load/encode phase markers
are logged; diagnostic launches never replace the original failing result.
It requires no cross-repository credential. The Kit's own Windows CI exercises its real
entry point separately; this is not a combined installed-Kit acceptance test.
Browser fixtures cover both languages at 390/768/1440, read-back after lost
reply, stale draft refusal and other-login controls. The Kit Windows matrix and actual same/cross-login
session acceptance are required before claiming the R03/T04/T05 gates complete.
