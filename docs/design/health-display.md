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
| Route kind and observation time | Native accepted `RouteChoice`, through `Entry::observed`, `Context::status` and the IPC allowlist. | Choice observation time, the shared 60-second deadline and active route generation. No new probes. |
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

The header and Connection settings use `healthDisplay` in
`desktop/src/health-display.js`. It reuses existing reads and `sessionFacets`;
the local display clock also expires observations while a request is pending.
It replaces those consumers' direct transport-label projection. Action admission
continues using `clientActionGate`; no TTL relaxes or adds a mutation requirement.
Fleet rows also use its `displayFact` and `healthLabel` projection/formatter,
now in the shared `desktop/src/health-fact.js` module and
re-exported from `health-display.js`. Fleet retains `fleetDiagnosis` as the single
native reason/step interpretation. `fleetHealth` applies the same local display
clock at render time: an expired connected observation keeps Connected, its age
and Checking; missing observations use Unknown and Checking. Expired layers cannot
claim current success. Unconfirmed stop and unsupported-version reasons remain
visible, and each row expires independently. Native Fleet identities are never
joined to central hosts by name, port, address or server string.

Checking and unknown diagnoses establish no value even with a recent probe
timestamp, so they always use Unknown and Checking; stale diagnoses retain the
observation age.

The 60-second display rule uses the same `observed_at` and observation window as
the native producer: `desktop/fleet-core/src/supervisor_status.rs` marks a selected ready
row `PROBE_STALE` after 60 seconds. Both Fleet views (`fleet.js` and
`fleet-desktop.js`) schedule their existing status/overview re-read after 5 seconds.
Checking describes those ongoing reads and the supervisor's probes; this adoption
adds neither a probe nor a polling loop.

Work retains six independent producer values, reason codes and past verification,
separate from PM analysis and from the saved profile. The profile response's
`observed_at` dates the read, not a BAT observation. Its states currently lack
`valid_until` and Inventory's `stale_after_s`; current evidence sometimes includes
only `last_success_at`. Work has not adopted the shared freshness display: its
existing value text, producer reasons (including `host_not_refreshed` and
`host_unreachable`) and timestamps remain unchanged. Nothing renews these values
automatically, so missing validity must not produce a permanent Checking label.
Adoption requires a producer validity envelope per capability: `observed_at` and
`valid_until` from the underlying Inventory host observation, or an explicitly
`static` fact with bootstrap/version invalidation. The existing readiness re-read
must then serve as renewal. Past verification never becomes current evidence,
and a reachable Connector never supplies a missing capability. No readiness
display changes admission, starts a check or adds a cache policy.

Fleet's existing status/overview renders consume the projection; updating mounted
rows while a native read is pending still requires wiring the existing `app.js`
display clock to the Fleet painter. `fleetHealth(row, {snapshot, readable, kind,
now})` is the tested pure entry point; the remaining call site is `app.js`'s
`tickHealth`. This change does not add another timer or alter those mounts.
Work, Workbench, Diagnostics and Result sources still need adoption of this
freshness/compact contract. Existing source-specific facets are not replaced by
a global boolean. Native and backend lifecycle behavior is unchanged here.

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
last-known until renewed. This background verification does not dismiss a retained
native credential-replacement refusal; explicit connection actions own that outcome.
Unsafe or failed verification shows its reason and a
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

## Chosen route observation

The native supervisor retains one accepted route choice per selected host, including
Connector. `desktop/fleet-core/src/route.rs::Routes::from_inventory` and
`SelectionRequest::run` produce exactly these `RouteKind` values:

| Kind | Meaning from the producer | English / zh-TW row text |
| --- | --- | --- |
| `direct` | Tailscale genuine-direct peer proof plus the existing successful TCP probe | Route: Tailscale direct / 路徑：Tailscale 直連 |
| `lan` | Configured LAN route with the existing successful TCP probe | Route: LAN / 路徑：區域網路 |
| `cf` | Configured Cloudflare fallback selected by the existing policy | Route: Cloudflare / 路徑：Cloudflare |
| `configured` | Connector's single configured SSH alias; this path has no route probe | Route: Configured SSH / 路徑：已設定的 SSH |

`route.rs::tailscale_peer` distinguishes `TailscalePeer::{Direct, Idle, Unknown}`.
Idle and unknown do not supply direct proof and never mean relay. They are peer
states, not chosen route kinds. A later choice may be LAN or Cloudflare; without a
choice there is no route line. No producer path emits relay or SSH fallback, so the
IPC allowlist and display do not invent those values. See [route policy](fleet-routes.md).

`supervisor.rs::tick_host` retains the safe choice only after revalidating it and
binding a launched process to the policy run. `Host::invalidate` clears it on
selection/generation/configuration changes, deselection, lost ownership or exit;
a replacement choice replaces it. `supervisor_status.rs::Entry::observed` emits
`route: {kind, observed_at, generation}` independently of readiness layers.
`observation_time` translates the observation age against the producer's Unix clock
anchor. The native caller `windows_supervisor.rs::now_ms` currently supplies Unix
milliseconds for both clock coordinates; a monotonic observation must instead use
its corresponding Unix anchor. A read or readiness probe never renews a route's time.
There is one latest status snapshot, no route history or database persistence.

`fleet.rs::readiness_route` rebuilds exactly `kind`, `observed_at`, `generation`
and optional `latency_ms`. It accepts only the four kinds above, a required timestamp
validated by the same `timestamp` sanitizer as readiness, and integer generation
1–9007199254740991. Optional latency must be an integer 0–60000 ms. Current
`RouteProbe` methods return boolean/peer eligibility, not measured latency, so the
producer emits no latency and the row shows kind only. Extra keys are dropped;
invalid declared fields refuse the response. Aliases, endpoints, addresses,
hostnames, raw peer payload and process output never enter the typed observation or
cross the IPC allowlist. No new probe, metric, timer, retry or fallback is added.

`fleet-status.js::fleetStatus` uses #396's unchanged `displayFact` / `healthLabel`
projection. At the inclusive 60-second deadline the last route kind remains with its
age and **Checking / 檢查中**; the existing Fleet status/overview read supplies future
observations. Missing route evidence adds no line. Failed/unavailable reads,
unapplied selection, stopped monitoring, deselected/off rows and **down** rows hide
it. In particular a last direct choice cannot reassure beside a down tunnel.
Route age is independent of readiness age: even a current readiness observation
cannot refresh the route. Route kind never changes readiness level, blocking layer,
reason, protocol, authentication or workspace readiness, nor any action gate.
Connector remains the name for the Connector service; a configured SSH route is not
proof of Connector or BAT readiness. Native sanitizer fixtures do not establish
physical route acceptance.

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
