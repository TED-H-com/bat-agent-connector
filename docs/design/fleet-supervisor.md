# Native Fleet supervisor lifecycle

This extends [the Fleet contract](fleet-rust.md), [discovery](fleet-discovery.md),
[tunnel effects](fleet-tunnel-effects.md) and the reviewed Kit monitor.
It is a native library runtime; the app's fixed CLI dispatch, migration, autostart,
bootstrap and window facade are separate integration work. Central operations and
remote BAT sessions remain outside its authority.

The supervisor runs on the thread that holds `Global\\BatFleetMonitor_<SID>`.
It first proves monitor absence across both BAT data directories, records its exact
native process/argv/configuration pair, and creates an unpredictable epoch. Process
effects never run in a WebView or readiness worker. A positively proven second login, malformed monitor record or conflicting owner
prevents startup. Inaccessible process candidates are skipped with a diagnostic;
unknown receipt evidence still cannot authorize replacement or process effects. Normal quit
and positive exit proof are required to replace a legacy owner; there is no forced
monitor termination.

Previous monitor records remain available while old tunnel records are evaluated.
Both directories are inspected in place. Only a matching process and positively ended
origin can be reclaimed as an orphan. A reused PID proves the old incarnation ended,
but never authorizes stopping the replacement. Unknown tunnel records and unmatched
launch intents remain per-connection fences. They cannot trigger a repeated launch
or prevent an independently proven connection's readiness worker from running.

Every launch uses the reviewed opaque tunnel plan and retained process handle.
An unknown launch or stop keeps its original receipt/intent. Config, selection and
credential changes invalidate pending results before publication. The original
private selection snapshot, rather than its public primary-file digest alone,
binds each generation. Credentials are reread from their configured Fleet sources;
a private in-memory fingerprint detects replacement. It is never logged, serialized
or replaced with the Dashboard's mutation credential.

The native entrypoint additionally receives the captured installation snapshot's
`verify_current` callback. It checks before mutex acquisition, self publication,
each tick/host and the tunnel launch boundary. Installation/backend drift latches
normal shutdown, even if the edited file is restored. This check is separate from
original process ownership: drift cannot revoke the evidence required to finish
stopping an already owned child. An unconfirmed stop retains the runtime and its
receipts without another termination request.

The installed BAT profile document is application metadata, not the strict configured
profile/index pair. Each BAT probe validates its selected remote endpoint/profile/pin
against that pair and binds all live index bytes; unrelated local-profile metadata
is preserved. The Connector's independent probe does not depend on that BAT index.
Missing probe credentials prevent readiness but do not revoke a selected, configured
SSH connection. Neither case triggers profile repair or a fallback credential.

At most three BAT readiness workers and three BAT route-selection workers run
concurrently, with one independent Connector worker. Pending readiness cannot
occupy another host's route slot. Route selection and readiness are bounded and
cancellable. Route eligibility and retry/demotion policy come from the shared route
module; TCP alone never means ready.
Completed results retain their original epoch/configuration/selection/generation and
observation time. Old, cancelled, future or more-than-60-second-old observations
cannot become fresh by republishing the status file. Unrelated preference changes
invalidate old workers without resetting another host's recovery backoff.

`fleet-desktop-status.json` preserves the reviewed schema, applied selection revision,
configuration binding, per-entry observation time and safe readiness layers. Private
paths, arguments, credentials and remote diagnostic bodies never enter status. The
current desired revision remains distinct from the last applied revision. An invalid
configuration leaves existing ownership evidence intact and cannot relabel old
readiness with a new binding.
The status reader also accepts the Kit's sparse `layers` objects, including an empty
object for an off connection. Omitted booleans remain false; unknown fields, duplicate
keys, wrong types and mismatched schema/configuration/epoch still refuse.

The fixed shared quit file is consumed only for the exact current PID and epoch.
Unrelated or malformed bytes remain untouched. Quit cancels workers and stops only
proven owned tunnels. Unconfirmed child exit preserves monitor/tunnel evidence and a
recoverable status; it does not claim clean shutdown. Successful cleanup removes only
the exact files still owned by this incarnation. Atomic replacement uses staged,
flushed files and an expected-original check under the shared owner mutex; it is not
an atomic transaction against an uncooperative editor replacing ancestor paths.

Stop intent records persist the original record digest before termination. On restart,
a matching intent permits only positive-death cleanup; an unknown still-live result
does not resend termination. Another login's files remain read-only even if their
recorded PID is now absent. Once exit is proven, matching launch and stop intents are
retired before the full owner receipt. Every intervening process-crash cut point thus
retains the child's original identity for recovery; failed intent retirement retains
that receipt as well. An unrelated intent is never cleared by filename alone.
Retained launch intents without a complete matching child receipt never authorize an
automatic retry. Legacy parent pointers are available from
the previous monitor bytes during this run; if old records lack durable parent evidence
after a crash, they remain unknown and require explicit recovery, never inferred ownership.

## Native interfaces

- `windows_supervisor::run(options, kit_client_directory, native_identity, tailscale,
  verify_installation)` owns the current-thread runtime and account mutex. The caller
  supplies the trusted **Kit client directory** (`kit_root/client`), fixed native
  executable/configuration identity and optional installed Tailscale path. No argument
  comes directly from IPC. `Options` contains the trusted Kit `Paths`, roaming directory
  and shared fixed quit-file path.
- `Supervisor::start` and `tick_guarded` expose the injectable core. `Effects` owns
  process effects; readonly `ProbeFactory` and `RouteProbe` inputs cannot launch or stop.
  Unguarded `tick` exists for callers with another equivalent installation lifetime
  fence; the Windows entrypoint always uses the guarded path.
- `supervisor_control::read_snapshot(configuration, discovery, observation, now_ms)`
  performs read-only discovery and revalidates epoch/configuration/age before returning
  safe status. It does not acquire the monitor mutex.
  `Snapshot::is_fresh(now_ms)` also checks the outer timestamp when every entry is off;
  age over 60 seconds is stale, while malformed/future timestamps refuse.
- `supervisor_control::request_quit(configuration, discovery, observation, quit_file,
  expected_owner)` writes only an exact current-login recorded owner request, including
  compatible recorded PowerShell owners. Callers obtain `expected_owner` through native
  discovery; a WebView-provided PID/epoch cannot establish ownership. Unrecorded legacy
  owners, another login and changed ownership refuse. Existing unrelated bytes survive.

The app's pre-Tauri dispatch, launcher intent, normal-quit wait, migration and bootstrap
compose these interfaces separately. This module does not implement those app flows.

## Verification scope

Tests use temporary configuration and records, injected process/route/readiness
effects and the existing bounded loopback fixtures. The full Linux core suite passes
138 cases, including 21 supervisor scenarios and four new file-boundary cases.
Formatting and all-target Clippy pass. An isolated MSVC check compiles the exact new
supervisor/Windows modules and their test bodies with the existing TLS provider constructor replaced only
in the temporary harness; it does not execute those modules or validate native TLS.
The unmodified all-target MSVC build is blocked locally by the missing `lib.exe` C
toolchain required by `ring`. Real Windows CI and packaged same/cross-login ownership,
migration and live topology acceptance remain separate gates.

## Resilient Windows monitor

Windows startup retries transient preparation failures under the same held account
mutex, restoring the exact previous monitor record if preparation fails after
publication and before any child launch. The Windows loop logs iteration failures and retains its mutex and children while
retrying uncertain evidence. Only successful quit/installation shutdown or a positive
installation/monitor conflict ends the loop. Code-only diagnostics append to
`fleet-monitor.log` in the roaming directory (configuration directory for failures
before the runtime loads), rotating at 1 MiB to one backup. Arguments, inventory,
credentials and process output are never written.

Each owned SSH process and its configured loopback listener are checked, with a
15-second listener grace period for a new SSH handshake. A positive
process exit or connection refusal retires only its proven receipt using the existing
stop-intent reconciliation, then retries after 5, 10, 20, 40 and at most 60 seconds.
The supervisor enables continuous recovery; other bounded recovery callers keep their
existing policy. Sixty seconds of positive owned liveness resets recovery.

After restart, a selected healthy orphan can retain its original receipt and epoch
when PID, FILETIME, executable, exact configured argv and login match, the original
monitor is positively dead, and no stop intent exists. Stale or deselected owned
orphans use the existing proven cleanup path. Unknown receipts and unsettled effects
remain fenced. Adoption does not create new ownership evidence.

`desktop/scripts/windows/install-fleet-monitor-task.ps1` backs up an existing task
before registration and configures delayed current-user interactive logon, unlimited
execution, IgnoreNew and 999 one-minute failure restarts. It is an operator installer,
not an automatic change to a person's scheduled tasks.
