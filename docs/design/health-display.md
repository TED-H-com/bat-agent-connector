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
| Route kind and observation time | Native accepted `RouteChoice`, through `Entry::observed`, `Context::status` and the IPC allowlist. | Last supervisor-verified use, the shared 60-second deadline and active route generation. No new probes. |
| Desktop and managed runtime versions | Existing native `app_build` and managed status producers; each remains its own version fact. | Bootstrap/version/build invalidation. Installed and launch-observed versions are separate. |
| Loaded MCP process version | `batc://identity` reports only the calling MCP process’s loaded build and start time, separately from its central capabilities observation. | Its next-launch comparison reuses the updater’s process installation proof. Dashboard has no MCP observation transport and leaves this row unknown. |

## Summary and compact presentation

Relative ages share `desktop/src/relative-age.js`: English uses `min`, `hr`, `d`
with spaces; zh-TW uses `分鐘`, `小時`, `天`. Units roll over at 60 minutes and
24 hours. This formatter never chooses a clock or evaluates freshness. Health
retains its observation/deadline clock, Overview its projection clock, and shared
timestamps their display clock and existing switch to calendar dates after a day.

The compact installation strip (including its Settings copy) retains each state and last-known age, then says
Checking once after all facets, equally in visible and accessible text. Full
health rows, Fleet rows and their per-fact descriptions retain Checking per fact;
they do not repeat it inside a single fact. The priority and expiry rules below
are unchanged.

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

Mounted Fleet status and overview painters subscribe to the existing
`health-display-tick` event and remove that listener on unmount. They repaint
the last snapshot in place through `fleet-status.js::paintFleetHealth`, including
while a native read is pending. Only diagnosis and route text, their independent
freshness and layer states change; controls, disclosures, focus and the message
area retain their nodes.
Ticks add no read, IPC call, timer or retry.

The 60-second display rule uses the same `observed_at` and observation window as
the native producer: `desktop/fleet-core/src/supervisor_status.rs` marks a selected ready
row `PROBE_STALE` after 60 seconds. Both Fleet views (`fleet.js` and
`fleet-desktop.js`) schedule their existing status/overview re-read after 5 seconds.
Checking describes those ongoing reads and the supervisor's probes; this adoption
adds neither a probe nor a polling loop.

Work adopts the shared `displayFact` / `healthLabel` projection for the
observation-derived states in `work-profile-readiness/v1`. Each declares
`observed_at` (the host's `last_attempt_at`, epoch seconds, matching Inventory's
connection evidence and the header) and `valid_until` (the last successful
observation plus `stale_after_s`, Inventory's ISO deadline from the same
`_valid_until` helper). Inventory and Work also share `_observation_reason` for
missing, unreachable and expired host facts, including expiry at the deadline.
`discovered` declares them only for `ready`, since discovery is ready only while
the host observation is current. `can_observe` declares them for `ready` via
`hosts_observed` and for `never_observed`, `host_unreachable` and
`host_not_refreshed`. `can_start_own` declares them when its winning blocker is
`host_unreachable` or `host_not_refreshed`. With no attempt, `observed_at` is null;
with no successful observation, `valid_until` is null and establishes no deadline.
`host_unknown` is a configuration reason, not an observation. All other states omit validity: authentication,
`configured_unverified`, execution-based states and `action_unavailable` retain
their existing text and never acquire Checking. This adoption declares no static facts.

Work preserves all six independent values, producer reasons, separate PM analysis
and the saved-setting warning. Past `verified` evidence remains history; neither
it nor the response's top-level read timestamp establishes currentness. A current
declared state shows its value; expiry shows the last value, age and Checking.
`host_unreachable` uses the shared unreachable wording and retains its reason.
Its "Last attempt" age dates the failed attempt, while the success-based deadline
and past verification keep their older instants. Successful observations update
both attempt and success times together, so ready and unrefreshed ages are unchanged.

The picker renews through its existing `read()` and contract/workspace/profile/
revision/digest/ticket checks, following the delegated renewal rule below. One
scheduled wakeup at the earliest declared deadline re-reads that same readiness
path once per producer deadline. No renewal starts while hidden; returning to the
page checks that same deadline. Renewal uses the shared per-reason outcome below,
preserving each validated response and retiring each attempted deadline. The
existing shell display clock only repaints ages; it cannot start reads. Selection changes invalidate pending renewal tickets, disposal clears
the wakeup/listeners, and renewal patches mounted content without changing the
selection, focus, scroll or open disclosure. No new endpoint, probe, authorization,
DDL, data step or action admission rule is involved.

Workbench, Diagnostics and Result sources still need adoption of this
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
Inventory, never BAT, and adds no endpoint or probe.

For Work, the header and Fleet, `displayFact` / `healthLabel` in
`desktop/src/health-fact.js` preserve each value, observation age and producer
reason. Work and the header also call that module's `healthReadError` for
validated unchanged-deadline renewal outcomes: `host_unreachable` keeps its
unreachable wording and newest attempt age without a read error; every other
declared state shows `health_read_failed` with `OBSERVATION_NOT_RENEWED`.
For these Connector renewals, an actual failed read overrides every reason with
that read's code. The header
stores the successful response and a source-path deadline cutoff alongside
`readErrors`: each host or session activity/connection fact whose deadline is
still at or before that cutoff is not renewed. Session axes have no host-reason
exception. A normal read advancing a fact's deadline clears its outcome;
bootstrap, namespace changes and inventory withdrawal discard the outcomes.
Only a read started at or after a fact's deadline can classify it as not renewed.
`health-display.js::takeHealthRenewal` bounds the source cutoff by the read's
start time. A verified same-scope reconnect forces one re-read of each retained
source, but a read before its deadline neither records an outcome for current
facts nor spends that deadline's renewal. At the deadline the source gets one
renewal GET, unless a normal read already advanced its deadline. A reconnect
read started after expiry counts as that renewal. A response arriving after
expiry cannot turn a read started before expiry into a renewal.
Fleet continues to use its existing native status/overview reads and diagnosis;
it has no Connector deadline renewal or synthetic not-renewed outcome.
Failed or unchanged Connector renewals do not retry on ticks or visibility
changes. A later normal source read with a new deadline permits the next renewal.
While the page is hidden no renewal starts. Older producers without deadlines retain the 60-second
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
binding a launched process to the policy run. Its retained `confirmed_ms` dates
that acceptance, independently of the original choice's `observed_ms` (which still
bounds choice admission). `reconcile_records` renews confirmation only after the
matching owned record, positive process liveness, retained child identity, policy
run, launch intent and ownership receipt have been checked, for the same generation.
Unknown liveness does not renew it; a blocked or failed check never supplies a new
confirmation. `Host::invalidate` clears the route on selection/generation/configuration
changes, deselection, lost ownership or exit; a replacement choice replaces it.

Every normal `tick_guarded` publication follows `reconcile_records`. A route newly
launched later in that tick gets its initial confirmation from its bound run.
Early verification/input errors do not publish or renew a route. Shutdown can
publish without reconciliation, but it first invalidates all hosts and passes no
route to `Entry::observed`, including an unconfirmed stop.

`supervisor_status.rs::Entry::observed` emits
`route: {kind, observed_at, generation}` independently of readiness layers.
`observed_at` means the **last verified use**, not the original route selection.
`observation_time` translates confirmation age against the producer's Unix clock
anchor. The native caller `windows_supervisor.rs::now_ms` currently supplies Unix
milliseconds for both clock coordinates; a monotonic confirmation must instead use
its corresponding Unix anchor. A future or unrepresentable confirmation omits only
that row's route, preserving its readiness and the remaining publication. Retained
`Snapshot::parse` still refuses future route timestamps, as it does row timestamps.

A status-file read has no process ownership evidence. A readiness probe establishes
protocol/authentication/workspace facts, not which owned transport is still in use.
Neither can renew confirmation; neither can the UI. There is one latest status
snapshot, no route history or database persistence.

The configured native tick cadence is shared across these states (source:
`windows_supervisor.rs::run`, `supervisor.rs::tick_host`, `route.rs::start_wake` and
`ownership.rs::Recovery`). The scheduled wait begins after a tick's work:

| State | Next supervisor tick | Independent work/backoff |
| --- | --- | --- |
| Normal running, including a live route | 1-second wait, or earlier route progress notification | Healthy readiness probes are scheduled 30 seconds apart; failed probes 15 seconds apart. These do not delay ownership checks. |
| Route/probe/recovery backoff, including another host with a live route | The same 1-second wait, or earlier progress | Unavailable-route retry: 15 seconds. Recovery waits 5 seconds, then 15/45/45-second spacing; alias demotion lasts 10 minutes. These are per-host eligibility deadlines, not supervisor sleeps. |
| Tailscale waking or wake cooldown, including another host with a live route | The same wait, interrupted by wake reservation/completion progress | Wake runs in a background task with a 5-second budget; cooldown is 5/10/20/40/60 seconds. Neither suspends the supervisor loop. |

Code inspection establishes no state that intentionally schedules ownership checks
60 seconds apart while a live route is retained. The paused-clock wake fixture also
observes completion progress at 1.5 seconds. This is fixture/source evidence, not
physical-host cadence measurement or an execution-time bound: synchronous local
I/O, a stalled process or scheduling may delay a tick. In that case the unrenewed
confirmation honestly ages to **Checking / 檢查中** at the unchanged 60-second
deadline. No new timer or probe is added.

`fleet.rs::readiness_route` rebuilds exactly `kind`, `observed_at`, `generation`
and optional `latency_ms`. It accepts only the four kinds above, a required timestamp
validated by the same `timestamp` sanitizer as readiness, and integer generation
1–9007199254740991. Optional latency must be an integer 0–60000 ms. Current
`RouteProbe` methods return boolean/peer eligibility, not measured latency, so the
producer emits no latency and the row shows kind only. Extra keys are dropped;
invalid declared fields refuse the response. Aliases, endpoints, addresses,
hostnames, raw peer payload and process output never enter the typed observation or
cross the IPC allowlist. No new probe, metric, timer, retry or fallback is added.

`fleet-status.js::routePresentation` owns route visibility, key, freshness and label
for both `fleetStatus` and the in-place `paintFleetHealth` clock paint, using the
shared `displayFact` / `healthLabel` projection. At the inclusive 60-second deadline the last route kind remains with its
age and **Checking / 檢查中**; the existing Fleet status/overview read supplies future
observations. Missing route evidence adds no line. Failed/unavailable reads,
unapplied selection, stopped monitoring, deselected/off rows and **down** rows hide
it. These visibility conditions depend only on the snapshot, so a display tick
updates the existing route node's text and `data-freshness` without adding,
removing or replacing it. In particular a last direct choice cannot reassure
beside a down tunnel.
Route age is independent of readiness age: even a current readiness observation
cannot refresh the route. Route kind never changes readiness level, blocking layer,
reason, protocol, authentication or workspace readiness, nor any action gate.
Connector remains the name for the Connector service; a configured SSH route is not
proof of Connector or BAT readiness. Native sanitizer fixtures do not establish
physical route acceptance.

The optional `route_requirement` explains the last failed route selection in the
current generation while no tunnel exists, including the wait between attempts.
It is a closed explanation from the existing selector: `DIRECT_PEER_UNKNOWN`,
`DIRECT_NOT_CONFIRMED`, `DIRECT_TCP_UNREACHABLE`
or `CONFIGURED_ROUTES_EXHAUSTED`. The first three describe a direct-only configuration
and its observed missing prerequisite; unknown peer evidence stays unknown. It is
independent of chosen route kind/time, adds no probe, fallback or retry, and carries
no peer data or latency. Native IPC allowlists the code; the shared row translates
it in English and Traditional Chinese. Success, invalidation (selection, credential
or generation change), an unrelated failure, or expiry withdraws the explanation
from the row. A row with current probe evidence cannot carry it. Unresolved native Fleet `STOP_UNCONFIRMED`
survives deselection, file expiry and re-reads until ownership reconciliation proves
the stop. Expiry still clears current readiness and successful layers; it cannot
erase the unresolved outcome or cause another stop. Managed Connector lifecycle
retains its separate owner.

`route_requirement` 在尚無 tunnel 時，以封閉代碼說明目前世代最後一次失敗的路徑選擇，
包括兩次嘗試之間的等待。僅設定直連時，
分別保留 peer 證據未知、直連未確認或 TCP 不通，其他情況說明所有設定路徑均不符合條件。
已選路徑的種類與確認時間仍獨立，不新增探測、替代路徑或重試，也不推測 relay 或延遲。
原生 IPC 僅接受列出的代碼，共用狀態列提供英語與繁中說明。路徑成功、失效（選取、憑證
或世代改變）、出現其他無關失敗或過期時，列上的說明會撤回；已有目前 probe 觀測的列也
不帶此說明。原生 Fleet 未確認的停止結果
會跨取消選取、過期與重讀保留，直到原擁有權核對證明已停止；過期仍撤回就緒與成功分層，
不抹去未決結果或再次送出停止。代管 Connector 的生命週期仍由其原擁有者處理。

## Lifecycle ownership

On every platform Desktop ensures its managed Connector. Closing a window hides
the client. Quit exits the client and never stops the Connector. The Connector stops
only through explicit managed stop, which refuses while busy. Existing bounded
managed upgrade/recovery operations retain their own ownership and busy checks.

Windows Fleet monitor and tunnels belong to the native application. Quit stops only
its own resources, preserving uncertain ownership refusals. An external/plain
Connector belongs to its service manager; the application never stops it.

Long-running MCP processes are never restarted automatically. `batc://identity`
reports the calling process's loaded build and start time, with an independent
central capabilities observation. Its `next_launch` observation reuses
`connector_update_status` and the updater's captured startup installation proof.
Only a snapshot belonging to that same updater installation can yield `same` or
`differs`. A difference supplies **Restart to load {version} / 重新啟動以載入
{version}** for this identified process only; it may reflect an upgrade or rollback.
Different installations, missing enrollment, an unproven loaded package or an
unreadable pointer yield `unknown` and no restart advice. Central unavailability
or authorization failure does not hide the loaded MCP build or local comparison.
Installing never changes the loaded identity. There is no MCP process scan,
registry or Dashboard transport; the Dashboard MCP row remains explicitly unknown.
Diagnostics' `diag_runtime_differs` compares bundled and launch-observed managed
runtime versions and cannot fill the MCP row.

The shared version projection also retains independent released rows for Desktop
and Connector. Unknown is the default: no loaded, installed or answering value can
fill a released slot. Desktop uses only the native updater's validated candidate
and its completed-check clock. Failed checks retain the prior candidate's original
clock; Status never renews it. Connector uses only its updater's persisted,
completed release check for the current channel, with separate completion and
publication times. Capabilities reads that private state without a lease, writes
or network access; old states without this observation stay unknown. Check policy
and activation are unchanged. About, Updates and diagnostics export share these
same facts; read time is never substituted for check completion or publication.
This display slice makes no lifecycle changes or claim of native/live acceptance.
