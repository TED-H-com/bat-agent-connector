# Native Fleet desktop integration

This composes the reviewed [ownership/supervisor](fleet-rust.md),
[migration](fleet-migration.md) and [profile launch](fleet-profiles.md) contracts.
Python remains the authority for remote work; this module manages only local client
connections, windows and the fixed current-user Startup slot.

## Startup and independent choices

The executable parses `installation::Entry` before Tauri's single-instance plugin.
`--fleet-supervisor --fleet-config <trusted absolute path>` runs the independent
native monitor without a WebView. `--fleet-login --fleet-config <trusted absolute
path>` validates the installation and reads the user's login-picker preference.
Neither path can be supplied by WebView IPC. A second login invocation must match
the already running Dashboard's canonical installation path.

The fixed roaming `bat-fleet-desktop.json` stores version 1 and `show_picker` only.
The default is true. Its revision binds original bytes; saving it holds the shared
Launcher guard and checks configuration and original bytes again before replacement.
It does not alter Startup, selected connections or windows. File replacement does
not claim atomic compare-and-swap against an uncooperative external editor.

At sign-in the picker opens Connection settings without starting anything. When
explicitly disabled, Dashboard visibility follows its saved choice and a separate
worker processes the original saved launch preview. It waits at most 30 seconds for
selected remote BAT profiles, without blocking Dashboard or Connector's independent
readiness workers. Retries are limited to positively unsent readiness/start-publication
waits and keep the same native preview. Other failures retain their original receipt
or uncertainty. Repeated login delivery to the same app never creates a second
preview automatically. A quit/update request invalidates pending login work.

The shared compact UI retains independent connection, BAT profile and Dashboard
choices, and previews added prerequisite connections before saving. Cancel changes
nothing. Labels and rows reuse existing type, colors, controls and 44 px touch targets;
desktop choices use two columns and narrow layouts use one. Browser fallback retains
its existing central functionality and never offers native controls.

## Shared machine checks

The [health display contract](health-display.md) defines selected-workspace scope,
freshness, attention/activity precedence, route-quality follow-up and lifecycle ownership.

Both Fleet IPC surfaces use `desktop/src/fleet-status.js` to present each configured
machine with a plain-language status, one next step and optional network, identity,
service and workspace evidence. The Connector route has no BAT workspace check.
The view uses only the existing snapshot and fixed codes; it does not infer a route
from `ROUTE_UNAVAILABLE`, create credentials, change trust, start remote work or
introduce an additional check API. BAT connection readiness does not establish an
execution profile or permission to create work.

A failed status read withdraws cached readiness, including the aggregate freshness
label and individual successful layers. Stopped monitoring, stale snapshots, stale
rows and unapplied selection revisions also cannot present a previous success as
current. A downstream layer is unconfirmed after a blocking failure; an untested
layer is not itself labelled failed. Unsaved connection checkboxes are preserved
and are never used as observed readiness. Initial read failures retain an explicit
read-only retry, even when no snapshot has been loaded.

Technical codes remain in Details. Open disclosures and keyboard focus survive
polls and failed reads; disclosure state is captured synchronously before DOM
replacement rather than depending on a queued `toggle` event. Native backend and
Startup migration controls are folded into Advanced by default. Pending migration
receipts remain recoverable there, with a visible summary and the original handles.
No launch, migration, credential or central authorization gate is changed.

Browser automation covers both native IPC contracts, English/zh-TW, narrow/wide
layouts, failed first reads, stale success, drafts, polling focus and zero mutation
from checking status. These are mock-native tests, not Windows/Fleet acceptance.

## Fixed IPC and receipts

`fleet_control` is distinct from the existing six `fleet_request` actions. It accepts
only typed logical IDs, booleans, known backend enums, observed hashes/epochs and
opaque 32-hex handles. No caller-supplied path, PID, executable, argv, token or URL is
accepted. At most 32 original choice/launch/migration previews are retained in Rust. Both IPC paths capture a monotonic lifecycle ticket before scheduling. The same ticket is rechecked after acquiring Launcher and at final profile, monitor, migration, preference and PowerShell effects; resetting the stopping flag never revives a queued pre-stop request.

| Action | Native state and effect |
| --- | --- |
| `overview` | Validated configuration, ownership, readiness, saved choices, picker preference and pending receipt IDs; no raw filesystem/process/credential data |
| `preview_choices` / `apply_choices` | Original private selection snapshot and owner/configuration CAS; explicit prerequisite summary |
| `preview_launch` / `launch` | Original live index/executable/selection preview; shared Launcher guard, original receipt readback before any launch |
| `launch_status` | Read the original durable launch ID; no resend |
| `preview_migration` / `apply_migration` | Exact old installation/Startup preview and fingerprint; freeze a durable transition with its original ID |
| `advance_migration` / `migration_status` | Continue or read that journal; normal quit and exit proof only, no force or implicit new launch |
| `restore_migration` | Explicit inverse transition from saved original bytes, with a fixed new restore ID retained across a lost reply |
| `save_login` / `discard` | Independent byte-CAS picker preference / drop an unused preview |

Remote profile launch requires the selected host's fresh, authenticated TLS/BAT/
workspace evidence, the current monitor incarnation and applied selection revision.
The original private selection snapshot (including legacy bytes) is checked before ensuring any monitor or connection. Readiness and selection are checked again immediately before profile effects. A TCP listener alone,
stale fields, future timestamps or another generation never suffice. Existing BAT
processes are preserved; Dashboard-only choices do not resolve a BAT executable.
A `started` receipt proves process creation, not rendered windows or live connection.
The frontend keeps original handles through refresh/reload and partitions drafts by
configuration binding. It checks receipt ID and original profile/Dashboard choices;
unknown outcomes cannot be cleared by silently creating another launch.

## Close, Quit and update exclusion

Window close hides to tray. Explicit Quit first blocks new local mutations and
invalidates automatic login retries, then runs the blocking
`fleet_native::with_stopped_fleet(path, effect)` fence. The same hook is available to
a caller-owned updater closure; update integration must also set the Control stopping
flag before entry. On refusal the caller can release that UI flag; cancelled login
workers stay invalidated.

The hook holds `Global\\BatFleetLauncher_<SID>`, refuses pending migration, validates
current installation/configuration and discovers both BAT data directories. It sends
normal quit only to an exact recorded epoch-proven current-login PowerShell or Rust
monitor. It waits at most 12 seconds for proven absence, refuses unknown launch
intents, then acquires the shared Monitor mutex and rechecks absence. Both guards
remain held through the caller effect. Unknown, legacy, other-login or replacement
owners prevent the effect. No force kill, unrelated quit-file overwrite, manual BAT
window close or central shutdown is available. Unconfirmed shutdown keeps Dashboard
open with an actionable error. A previously observed installation that is deleted
cannot be mistaken for a never-configured Dashboard.

Missing `fleet.json` also requires independent absence proof after a Dashboard
restart. `with_unconfigured_fleet_absent(path, effect)` holds the same Launcher
and Monitor mutexes, checks the fixed account launch journal and both BAT data
directories, and refuses unknown process enumeration or unrecorded legacy Fleet
monitor candidates. A completed launch journal is allowed only when its exact child
is proven absent. Any remaining monitor/tunnel ownership or intent file requires
restoring the trusted configuration for ordinary recovery; this path never deletes
evidence or stops a process. It rechecks that configuration is absent immediately
before the effect and keeps both mutexes held through the effect. The fresh
Dashboard-only case does not require a BAT executable or create Fleet state.

## Verification limits

Tests use temporary preferences/configuration, injected ownership/receipts, synthetic
PowerShell fixtures, and browser mocks of fixed native IPC. Real Windows native
modules are also typechecked in an isolated MSVC-target harness; its TLS crypto
constructor alone is stubbed because this Linux host lacks a Windows C linker.
That check does not execute Windows code or validate installed UI/Startup/process
behavior. Packaged Windows tray, sign-in, retained process handles, real profile
restoration and migration still require Windows acceptance. No live SSH, Tailscale,
BAT, Startup or production credential action is part of these checks.
