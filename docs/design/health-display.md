# Health display

Health is a projection of existing observations, scoped to a verified installation,
principal and explicit workspace binding. It is not an operation gate or another
probe. Python Connector / Task Service remains the business authority; native code
owns only its local service, windows, credentials and Fleet transport evidence.

## Freshness and identity

A producer's `valid_until` wins. Without it, a live fact is current for 60 seconds
after `observed_at` (two 30-second producer polls). At exactly 60 seconds it becomes
stale. Keep the last value, its age and **Checking / 檢查中**. A successful HTTP read
of cached inventory does not reset its observation time. An explicit producer stale
flag also withdraws currentness. Missing observation time means unknown.

Stale, unknown, refused, unconfigured and unsupported never become offline or down.
An explicit failed observation keeps its own reason. The legacy `data-connection`
attribute remains a transport compatibility signal; its `offline` value only means
no client credential, displayed as **Not connected / 未連線**, never machine failure.

Versions, capabilities and installation identity are static: no 60-second expiry.
The next bootstrap invalidates the previous snapshot; a changed version, build or
contract invalidates static facts associated with the old version. Newly verified
bootstrap facts replace them. Connection/principal changes discard the projection;
withdrawn source access discards cached inventory. Keep drafts and operation keys in
their existing namespaces. No display state authorizes work or changes final gates.

## One producer per displayed fact

| Fact | Producer and identity | Freshness source |
| --- | --- | --- |
| Connector connection and authenticated request result | Existing client `/events` reader (`pollHealth`, `lastPoll`), bound to the activated namespace. Before its first result, successful verified `/bootstrap`. Transport errors, refused requests, service errors and cursor resets remain distinct. | Client receipt `at`; bootstrap receipt time only before the first poll. An optional producer deadline takes precedence. View refresh failures do not withdraw a successful event response. |
| Actor, scopes, capabilities, Connector version/build, managed installation ID | `ApiV1` capabilities in the verified `/bootstrap`; subsequent capability reads detect version changes. | Bootstrap generation and Connector/build/contract identity; no time expiry. Scopes describe permission, never readiness. |
| Managed recovery | `native_status.managed_recovery`, from the existing native refresh for this client binding. | Receipt time of that native read. Socket/PID/port facts cannot replace it. |
| BAT host connection/error and other central machines requiring attention | `Inventory.hosts`, returned by existing `/hosts` or `/sessions` reads, within the current Connector principal. | `last_attempt_at` for the result; `last_success_at` only describes the last success. Producer stale/error fields survive. Never use HTTP receipt time as a fresh BAT check. |
| Output activity | `Inventory._state` session `state.activity` and its `state.evidence.activity`; legacy session streaming only if that axis is absent. | Axis `observed_at`, optional `valid_until`, and producer stale flags; legacy session `observed_at` only when axis evidence is absent. |
| Needs attention | Session inventory's pending interaction or explicitly managed runtime status, never idle/streaming inference. | The same session observation; a stale recorded request remains visible as a last-known request, separately from staleness. |
| Workspace membership used for the selected summary | Explicit session `host` + `workspace_id`; without a workspace ID only the explicitly selected session can be summarized. PM workspace `server_id` belongs to the activated Connector installation, not a Fleet alias. | Current scoped source read; discard on a new bootstrap or access withdrawal. No path/name matching. |
| Native Fleet machine attention | Existing native `Entry::observed` readiness, through `fleet::sanitize_result` / `readiness_row`, consumed by the existing Fleet status/overview read. | Row `observed_at` and readiness stale state. Native aliases are a separate identity domain; never join them to central host names. |
| Route kind, observation time, optional measured latency | Native route-selection observation, once the sanitized contract below exists. | Producer observation time/deadline and route generation. No new probes. |
| Desktop, runtime and MCP versions | Existing native `app_build`, managed status and adapter/version producers; each remains its own version fact. | Bootstrap/version/build invalidation. Installed and running versions are separate; installing does not prove a long-running process loaded the new code. |

## Summary and compact presentation

The header describes the selected workspace's installation only. Connector connection
is explicitly labelled **Connector**; it does not mean BAT, authentication scopes,
workspace access or all machines are ready. Where an explicit selected session supplies
workspace membership, the adjacent header summary uses only observed sessions with
that exact host/workspace binding. With no workspace binding, label it selected work
and use only that session. Missing/partial evidence is not proof that a workspace is
idle. PM views keep the verified Connector summary; they do not invent a BAT binding
from a repository or any available adapter.

Other installations never change the selected summary. Show their separate count as
**2 other machines need attention / 另有 2 台機器需要處理**, linked to Fleet. It counts
observed problems, not a complete fleet total or a new scan. Prefer scoped central
inventory when available; otherwise use the already-read native Fleet host rows for
the Connector view. If a selected central host cannot be bound to native aliases,
omit that native count instead of possibly counting the selected machine as another.
An empty or unread inventory is not proof of zero problems.

Needs attention, output activity and stale are independent facets. A compact pill uses
**needs attention > stale/unknown > working > idle**. Idle only describes output;
it never means no person is needed. Stale activity retains its last value and age,
but cannot claim output is currently arriving. Both locales preserve these meanings.

The first consumer is `healthDisplay` in `desktop/src/health-display.js`, shared by
the header and Connection settings. It reuses existing reads and `sessionFacets`;
the local display clock also expires observations while a request is pending.
It replaces those consumers' direct transport-label projection. Action admission
continues using `clientActionGate`; no TTL relaxes or adds a mutation requirement.
Fleet rows, Work, Workbench, Diagnostics and Result sources still need adoption of
this freshness/compact contract. Existing source-specific facets are not replaced
by a global boolean. Native and backend lifecycle behavior is unchanged here.

## Route quality follow-up

The frontend currently receives readiness layers, not a sanitized route observation.
`desktop/fleet-core/src/route.rs` has `RouteChoice.kind`, `generation`,
`observed_ms`, and `TailscalePeer::{Direct, Idle, Unknown}`. These are insufficient
to label idle as relay. Carry a latest-only route observation through the supervisor
status producer to `desktop/src-tauri/src/fleet.rs::sanitize_result` and
`readiness_row`. The latter currently allowlists readiness `layers` and drops any
route evidence. Extend its shared sanitizer golden before using the fields in
`desktop/src/fleet-status.js::fleetStatus` and the shared display projection.

The sanitized observation needs `kind` (direct, relay, SSH fallback, idle, unknown),
`observed_at`, generation and optional already-measured latency. Translate monotonic
`observed_ms` at the producer; never treat it as a wall-clock timestamp. Include
latency only when an existing probe measured it; do not add a probe or packet-loss
metric. Omit aliases, endpoints, addresses, raw peer payload and process output.
Keep only the latest observation for the active route generation in memory; clear
it on replacement and persist no history. Route quality never establishes protocol,
authentication or workspace readiness. Those checks remain separate. Native
sanitizer CI and physical route acceptance are required for that follow-up.

## Lifecycle ownership

On every platform Desktop ensures its managed Connector. Closing a window hides
the client. Quit exits the client and never stops the Connector. The Connector stops
only through explicit managed stop, which refuses while busy. Existing bounded
managed upgrade/recovery operations retain their own ownership and busy checks.

Windows Fleet monitor and tunnels belong to the native application. Quit stops only
its own resources, preserving uncertain ownership refusals. An external/plain
Connector belongs to its service manager; the application never stops it.

Long-running MCP processes are never restarted automatically. The required display
when a newer version is installed is **Restart to load {version} / 重新啟動以載入
{version}**. **This notice is not built.** There is no producer reporting the loaded
version/build of each running MCP process, and no corresponding UI copy/consumer.
The follow-up needs a process-owned MCP runtime identity observation (loaded
version/build and process generation), separately compared with the updater's
installed-for-next-launch version. Installing must not change the loaded identity.
Diagnostics' existing `diag_runtime_differs` compares bundled and launch-observed
managed runtime versions; it cannot prove a running MCP process's loaded version.
This display slice makes no lifecycle changes or claim of native/live acceptance.
