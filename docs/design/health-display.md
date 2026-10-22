# Health display

Health is a projection of existing observations, scoped to a verified installation,
principal and explicit workspace binding. It is not an operation gate or another
probe. Python Connector / Task Service remains the business authority; native code
owns only its local service, windows, credentials and Fleet transport evidence.

## Freshness and identity

A producer's `valid_until` wins. Without it, a live fact is current for 60 seconds
after `observed_at`. This default applies to the event reader and native reads,
not to Inventory, which declares its own validity. At exactly the deadline it becomes
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
withdrawn source access discards the affected cached inventory, retaining selection. Keep drafts and operation keys in
their existing namespaces. No display state authorizes work or changes final gates.

## One producer per displayed fact

| Fact | Producer and identity | Freshness source |
| --- | --- | --- |
| Connector connection and authenticated request result | Existing client `/events` reader (`pollHealth`, `lastPoll`), bound to the activated namespace. Before its first result, successful verified `/bootstrap`. Transport errors, refused requests, service errors and cursor resets remain distinct. | Client receipt `at`; bootstrap receipt time only before the first poll. An optional producer deadline takes precedence. View refresh failures do not withdraw a successful event response. |
| Actor, scopes, capabilities, Connector version/build, managed installation ID | `ApiV1` capabilities in the verified `/bootstrap`; subsequent capability reads detect version changes. | Bootstrap generation and Connector/build/contract identity; no time expiry. Scopes describe permission, never readiness. |
| Managed recovery | `native_status.managed_recovery`, from the existing native refresh for this client binding. | Receipt time of that native read. Socket/PID/port facts cannot replace it. |
| BAT host connection/error and other central machines requiring attention | `Inventory.hosts`, returned by existing `/hosts` or `/sessions` reads, within the current Connector principal. | `last_attempt_at` dates the result; `valid_until` is the last successful observation plus Inventory `stale_after_s`. Producer stale/error fields survive, including `host_unreachable`. A failed attempt is labelled as such, not as an unexplained stale connection. Never use HTTP receipt time as a fresh BAT check. |
| Output activity | `Inventory._state` session `state.activity` and its `state.evidence.activity`; legacy session streaming only if that axis is absent. | Axis `observed_at`, producer `valid_until` (the last successful activity observation plus `stale_after_s`), and producer stale flags; legacy session `observed_at` only when axis evidence is absent. |
| Needs attention | Session inventory's pending interaction or explicitly managed runtime status, never idle/streaming inference. | The same session observation; a stale recorded request remains visible as a last-known request, separately from staleness. |
| Workspace membership used for the selected summary | Explicit session `host` + `workspace_id`; without a workspace ID only the explicitly selected session can be summarized. PM workspace `server_id` belongs to the activated Connector installation, not a Fleet alias. | Complete scoped page chain; invalidate currentness on bootstrap, discard withdrawn inventory, and preserve selection/last values only within the same verified namespace. No path/name matching. |
| Native Fleet machine attention | Existing native `Entry::observed` readiness, through `fleet::sanitize_result` / `readiness_row`, consumed by the existing Fleet status/overview read. | Row `observed_at`, readiness stale state and the shared `fleetDiagnosis` (including monitor/selection state). Native aliases are a separate identity domain; never join them to central host names. |
| Route kind, observation time, optional measured latency | Native route-selection observation, once the sanitized contract below exists. | Producer observation time/deadline and route generation. No new probes. |
| Desktop and managed runtime versions | Existing native `app_build` and managed status producers; each remains its own version fact. | Bootstrap/version/build invalidation. Installed and launch-observed versions are separate. |
| Loaded MCP process version (required, not built) | Missing process-owned MCP runtime identity producer; see the lifecycle follow-up below. Existing adapter/version facts cannot establish this identity. | The future observation must bind loaded version/build to process generation. Installing does not prove an existing process loaded new code. |

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
**2 other machines need attention / 另有 2 台機器需要處理** (with singular/plural copy). Native alias counts link to Fleet; central Inventory counts have no link because no existing view lists those host identities. It counts
observed problems, not a complete fleet total or a new scan. Prefer scoped central
inventory when available; otherwise use the already-read native Fleet host rows for
the Connector view. If a selected central host cannot be bound to native aliases,
omit that native count instead of possibly counting the selected machine as another.
An empty or unread inventory is not proof of zero problems.

Needs attention, output activity and stale are independent facets. A compact pill uses
**needs attention > stale/unknown > working > idle**. Idle only describes output;
it never means no person is needed. Stale activity retains its last value and age,
but cannot claim output is currently arriving. Gone rows are historical and supply
neither aggregate attention/activity nor last-value spans. Connection problems are
separate facts; only pending interactions or managed runtime status produce attention. Both locales preserve these meanings.

The first consumer is `healthDisplay` in `desktop/src/health-display.js`, shared by
the header and Connection settings. It reuses existing reads and `sessionFacets`;
the local display clock also expires observations while a request is pending.
It replaces those consumers' direct transport-label projection. Action admission
continues using `clientActionGate`; no TTL relaxes or adds a mutation requirement.
Fleet rows, Work, Workbench, Diagnostics and Result sources still need adoption of
this freshness/compact contract. Existing source-specific facets are not replaced
by a global boolean. Native and backend lifecycle behavior is unchanged here.

## Delegated production display decisions

The orchestrator, under the owner's delegation of health display decisions, resolved
the following producer and consumer details. These amend the initial display rule;
they do not alter lifecycle ownership or action admission.

Inventory polls every `inventory_interval_s` (normally 60 seconds), backs failing
hosts off up to 600 seconds and uses `stale_after_s` (normally 180 seconds). Host
results and session activity evidence now carry additive `valid_until` fields beside
the existing stale flags; connection axis evidence carries the host deadline too.
Missing observations have no deadline. `not_enumerated`, `gone`, metadata failure and
`host_unreachable` still invalidate currentness immediately. The stale threshold is
inclusive, matching display expiry at the deadline.

Timestamp-only refreshes deliberately emit no event. Therefore the visible client
renews an expired observation with one GET of the same already-used Connector source
path (including that page's scope/cursor), once per producer deadline. The client retains the successful GET callback and
replays that exact request instead of synthesizing a route. It reads cached
Inventory, never BAT, and adds no endpoint or probe. A failed/unchanged renewal does
not retry on clock ticks; it retains the last value, age and its own failure reason.
A later normal source read with a new deadline permits the next renewal. While the
page is hidden no renewal starts. Older producers without deadlines retain the 60-second
display fallback. No additional validity delivery mechanism exists in steady state.

`order`, `include_gone`, `limit` and `cursor` do not filter. A chain beginning without
`cursor` and ending at `next_cursor === null` establishes completeness globally, or
for one host when `host=` is the only filter. Attention/orchestration/other filtered
reads update observations without establishing or withdrawing completeness. Caches
pin the selected row; bounded eviction withdraws completeness for affected hosts.

A 401 withdraws the principal's cached Inventory. A 403 on an Inventory read withdraws
that Inventory; other 403s do not. A 404 clears only the selected session's facts when
it comes from that session's own detail GET. Other 404s can represent normal absence.
Selection remains so unknown is visible. Credential pausing stays with the existing
client transport; the health projection does not duplicate that policy.

Event-stream resets use the view's shared `IDENTITY_PRESERVING_RESETS` contract:
`cursor_expired`, `cursor_ahead`, `history_changed` and `checkpoint_cursor_mismatch`
retain the last scoped display while bootstrap verifies continuity. A verified
same-namespace bootstrap retains selection and last observations for renewal;
inventory completeness must still be established by current reads.
Every other or unknown reset reason withdraws the entire health projection before
bootstrap, alongside the view: no old workspace label, activity or machine count
remains, and the Connector shows waiting/unknown instead of the old connected fact.
Failed verification leaves it withdrawn. Successful verification starts with empty
health facts and fills them only from the newly authorized reads. Drafts and
operation keys stay in their original namespace. This uses the existing
[identity reset boundary](two-service-isolation.md#managed-crashrebind-followed-by-a-producer-reset-m07m08),
not a separate health classification or bootstrap path.

Version/build/contract changes trigger one existing verified bootstrap when the
principal and namespace can be verified unchanged. It preserves drafts, operation
keys and the active view; static facts are replaced, cached Inventory becomes
last-known until renewed. Unsafe or failed verification shows its reason and a
localized Reload action. A bootstrap still pending after 60 seconds also offers
Reload with `VERIFICATION_PENDING`; it does not issue another request or discard a
later verified response. It never leaves an unbounded capabilities Checking state.

The display clock starts before the first view read. Ages use coarse minute buckets
outside the Connector live region. Header links remain attached; Settings reconciles
only changed content and preserves focus. One banner landmark remains. A measured
shell height includes a wrapped summary in session height and navigation offsets,
so the composer uses the available viewport space. Conversation captions, notices,
diagnostics and reading controls retain their content height. The transcript yields
space down to its reading minimum; if the controls still cannot fit, the session
view scrolls instead of shrinking rows into each other. The summary stays sticky.
Only a direct `.conversation-scroll` child of the product's `.conversation` column
starts at the 120 px reading minimum and fills the remaining space. Standalone
transcripts retain automatic content height: sharing `.session-workspace` message
styling does not opt them into the column's height constraint.

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
