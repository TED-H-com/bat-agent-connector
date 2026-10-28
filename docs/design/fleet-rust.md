# Native Fleet supervisor contract

This is the R03/T04/T05 implementation contract, not parity or installed acceptance evidence.
It extends [the desktop facade](desktop-fleet.md) and the [shared product decisions](../product/realignment-v2.md).
The central Python Connector remains authoritative for tasks and domain operations. Fleet owns only local
connections, readonly readiness, fixed configured bootstrap and BAT window launch.

## Sources and boundaries

The public [Fleet core source](../../desktop/fleet-core/) and its tests define the
native contract. Preserve the documented Fleet Kit compatibility formats when upgrading
an existing PowerShell installation. Only synthetic fixture inventory/index/SSH data may enter this public repository. Never copy the Kit's
tracked private inventory, profile index, token files, fingerprints, service paths or SSH configuration.

| Responsibility | Required native behavior and evidence |
| --- | --- |
| Inventory | Read the same versioned Kit inventory/index pair, strict duplicate/unknown field rejection, full host set retained; no second host table |
| Selection | Connections, BAT profiles and Dashboard choices remain independent; stable logical IDs, known-host migration, explicit added prerequisites, cancel keeps prior preferences |
| Readiness | Tunnel, TLS pin, BAT v2/auth/version/workspace and observe-only Connector capabilities; generation and age fences, bounded independent host probes |
| Ownership | Same `Global\\BatFleetMonitor_<SID>` exclusion across implementations and login sessions; PID plus creation time, SID/session, executable, exact argv, inventory/index and monitor epoch |
| Recovery | Only proven owned processes, bounded backoff (Windows supervisor: 5/10/20/40/60 seconds, continuously capped at 60; other callers retain the three-attempt policy), unknown listener or unknown owner blocks, no port/substring-based killing |
| Lifecycle | Existing app focus remains separate from supervisor ownership; close-to-tray, exit and client crash do not stop central tasks or remote BAT sessions |
| Migration | Explicit PS/Rust selection, exact old owner exit proof before acquiring ownership, reviewed autostart state and rollback receipt; neither installation nor a stale record proves transfer |
| Bootstrap | Fixed configured service/SSH recipe only, bounded query/ensure/requery, local receipt before central is online; no empty journal or substitute daemon |

## Inventory and local authority

The trusted native `fleet.json` identifies the reviewed Kit installation and selected backend. Existing
configuration defaults to the PowerShell adapter until native parity is enabled through the migration flow.
WebView requests continue to accept logical IDs, observed revision/epoch and fixed actions only; no path,
command, token, arbitrary URL, service name or PID may be supplied through IPC.

Read configuration bytes with bounds, reject duplicate JSON keys (including case/escape variants), and
validate inventory/profile/SSH pairing before either frontend projection or process creation. Retain the
Kit's content-based binding across inventory/index and effective SSH config inputs. Re-read it before each
effect and publication; do not label old readiness with a newly computed binding. External SSH Include
changes remain explicit limitations until observed by the port; do not claim a complete SSH transaction.

Windows configuration binding expands existing 8.3 path components like the reviewed Kit's
Windows PowerShell/.NET `GetFullPath`, including parents of an absent user SSH file. It uses
`GetLongPathNameW` without resolving reparse targets or adding a verbatim path prefix. The
Windows test runs the copied read-only Kit binding oracle against explicit short paths and
compares both exact synthetic preimages and digests; Linux checks alone do not prove this parity.

Profile records accept only the reviewed Kit/BAT keys: `id`, `name`, `type`, `createdAt`, `updatedAt`,
`remoteHost`, `remotePort`, `remoteProfileId`, `remoteProfileName` and `remoteFingerprint`.
Unlike the historical PowerShell object check, the Rust port rejects unknown per-profile fields,
including in alternate indexes. Canonical root extension fields do not extend this record schema;
a future BAT schema change requires an explicit compatibility update. Connection/pin values still
pass the separate inventory pairing checks.

Use the existing BAT data directory discovery (new and old names) and shared preference lock. Selection
CAS compares original bytes, current configuration and owner epoch under the lock, preserving unseen
edits. Removing a connection also removes dependent future window choices, so launch planning cannot
silently add it back. Dashboard-only works without a local BAT executable. The desktop token and Fleet's
observe-only credential remain separate; no credential contents, raw process arguments or private paths
are returned in status or logs.

## Windows ownership and process effects

Acquire the same account-scoped named mutex used by PowerShell, not a new Rust-only lock. Check existing
records in both BAT data directories and any supported legacy monitor identity before starting. A process
with inaccessible identity is unknown, never dead. Reused PID with a different creation timestamp proves
the old incarnation ended but does not authorize stopping the new process. A matching owner in another
login is visible/read-only and cannot be taken over or told to stop.

Spawn only the configured system SSH executable with fixed argv and an unpredictable monitor epoch.
Persist exact child identity and parent identity before publishing ownership. Failure to save a newly
spawned child's record may terminate only the launch's retained process handle. For later termination,
open and retain a process handle before the final identity check so PID reuse cannot change its target.
Orphan recovery additionally proves the originating monitor incarnation ended. Unknown listeners are
reported as occupied and never cleared. Configuration/selection changes invalidate pending probe results.

Native state must preserve the facade's distinction between desired selection revision, applied revision,
monitor epoch, configuration binding and timestamped readiness. No TCP-only ready inference. Long-running
local control is separate from the WebView window lifecycle; central offline never triggers replacement
business services. Each route/host has independent recovery backoff; one slow host does not block central
authentication or other readiness.

## Probe and migration acceptance

Port the independent synthetic wire fixtures, including pinned TLS, failed auth, wrong IDs/profile,
fragmented UTF-8, ping/pong between fragments, bounded message size and total deadlines. BAT probes use
only auth/capabilities and readonly workspace load. Connector readiness requires exactly observe scope,
API/contract/features and the configured endpoint. Secrets are resolved in Rust and dropped/zeroed where
owned; no fallback to the user's mutation credential.

The migration UI first displays observed backend, owner/login state and existing startup entries. Its
fixed intent retains prior settings and requested backend. Request normal old-owner exit using its exact
epoch; then prove exit and mutex availability before starting the replacement. An unknown exit remains
recoverable and never starts a second owner. Rollback follows the same protocol in reverse. Startup entry
changes use exact previously observed entry bytes/identity and preserve unrelated entries; a second
installed executable cannot independently enable another reconnect loop.

Required evidence combines pure contract tests, Windows real process/handle/mutex fixtures, mock TLS/
WebSocket/HTTP peers, synthetic migration/autostart tests and packaged same/cross-login acceptance.
Linux unit tests and successful Windows compilation cannot establish installed Windows ownership,
autostart or full R03/T04/T05 parity. No live topology or service effects occur during development tests.
