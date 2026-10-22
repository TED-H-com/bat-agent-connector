# Two independent services in one client (M04)

The [health display contract](health-display.md) keeps observations and summaries
within these verified source boundaries and defines when displayed facts expire.

The client must select one verified source at a time. Identical host/session IDs,
names and actor labels cannot establish identity. `activate`
(`desktop/src/app.js:119`) verifies `/bootstrap`, then assigns the namespace before
mounting a view. Browser/external scopes include endpoint, server, principal and
actor (`desktop/src/state/events.ts:45`); native managed scopes use the verified
server, principal and actor without the ephemeral port
(`desktop/src/state/storage-identity.ts:7`). `mountWorkspace`
(`desktop/src/app.js:4768`) disposes the old shell/navigation when this scope changes.

## Actual connection paths

| Path | Product entrypoint and boundary | Integration proof |
| --- | --- | --- |
| Browser, same origin reused | `transport/index.ts:110` sends same-origin HTTP; `app.js:3338` signs in and `app.js:185` clears the origin credential on disconnect. A listener can stop and a different journal can bind the same loopback port. Only the verified namespace separates their client data. | Explicitly disconnect A, close A's real listener/connections, bind B at exactly the same port, sign in to B, then reverse. Same BrowserContext/page and origin storage throughout. |
| Browser, different origins | Browser origin storage and same-origin fetch (`transport/index.ts:123`) already isolate them. | A second page at B's other port sees neither A's source storage nor its token (only neutral language initialization is allowed). |
| Native, external A → B → A | `nativeTransition` (`app.js:3450`) disconnects/invalidates before `connector_reload_configuration` (`transport/index.ts:75`). Native `bridge.rs:853` reads the fixed external configuration and clears a changed binding's launch credential/identity. Reconnect verifies bootstrap (`bridge.rs:1097`). | Stub the OS configuration/credential boundary, use the actual Reload configuration/Connect UI, forward every Connector request unchanged from Node to the selected real HTTP service. No API data or namespace is synthesized. |

`connector_setup_configuration` (`transport/index.ts:76`, `bridge.rs:878`) creates
the initial external configuration with native confirmation; it cannot overwrite
an existing configuration. External A/B switching uses an external edit followed
by reload, not repeated setup calls. A managed client can explicitly join an
external source through setup when the fixed external file does not exist
(`bridge.rs:953`). Reload rejects a currently managed bridge (`bridge.rs:854`), so
managed/external A/B/A is not a reload toggle. Startup selects external mode if the
fixed configuration exists (`managed.rs:117`, `main.rs:736`); selecting managed
again requires a subsequent startup without that external selection. This test
does not simulate that lifecycle or accept it as native installation evidence.

## Every key outside the Connector namespace

Audit `localStorage`, `sessionStorage` and storage-helper call sites in
`desktop/src`. The following is the complete set outside `state.namespace`;
Fleet/Tailscale have their own native scopes. `batc.diagnostics.v1` is a schema
label, not a storage key. No global last-workspace key exists.

| Key | Area; product reference | Carrying across A/B |
| --- | --- | --- |
| `batc.dashboard.token` | Session; optional remembered local; `app.js:72`, `app.js:174`–`186` | Credential for the current browser origin, not an identity fact. Explicit disconnect removes both copies before B signs in. Native credentials stay behind IPC. An unannounced server replacement while a remembered token remains is outside this explicit-switch proof: authentication precedes discovery of the new identity. |
| `batc.lang` | Local; `i18n.js:2174`, `i18n.js:2183` | Correct: a client language preference. |
| `batc.sidebar.width` | Local; `workspace-layout.js:12` | Correct: client layout, no source facts. |
| `batc.mobile.info_collapsed` | Local; `workspace-layout.js:13` | Correct: client layout. |
| `batc.mobile.composer_collapsed` | Local; `workspace-layout.js:14` | Correct: client layout; never the composer contents. |
| `batc.tab` | Session, legacy read fallback only; `attention.js:9` | Correct: only a neutral tab choice. New choices write `batc.attention.<namespace>`. |
| `batc.repo`, `batc.pr` | Session, retired; formerly `viewDelivery` | Incorrect: a repository/PR selection can reveal private source facts or trigger a read at the next source. Retained without reads, writes, deletion or guessed migration. New selections use `batc.delivery.<namespace>.repo/pr` (`app.js:2480`, `app.js:2521`). |
| `batc.desktop.fleet.selection.<binding>` | Session; `fleet.js:16`, `fleet.js:113` | Correct: belongs to the independent native Fleet configuration, not a Connector journal. Changes of native binding select a different key. |
| `batc.desktop.fleet.controls.<binding>` | Session; `fleet-desktop.js:14`, `fleet-desktop.js:43` | Correct: independently bound native choices and original request hints; current native receipts/gates remain authoritative. |
| `batc.desktop.fleet.bootstrap.original` | Session; `fleet-bootstrap.js:12`–`21` | Correct: original native request ID plus recipe binding, checked again by native. No central intent, credential or source data is copied. |
| `batc.desktop.tailscale.<login-scope>` | Local; `tailscale.js:20`, `tailscale.js:93`; `src-tauri/src/tailscale_control.rs:84` | Correct: request ID bound to the Windows login owner/session, independently of either Connector. |

Last PM workspace and selected view live in bounded Maps keyed by the namespace
(`pm-workbench.js:20`–`24`), and view memory includes namespace/workspace/view
(`ui/view-state.js`). The integration pins the native same-page selection and
namespace-scoped session filters, without claiming browser reload persists RAM.

## Harness and assertions

Run `cd desktop && npm run test:two-service-isolation`. Default execution includes
four complete passes: browser and native forwarding, each in en-US and zh-TW.
`BATC_ISOLATION_LOCALE` optionally selects one locale for diagnosis. CI runs the
default next to `test:central` through `scripts/ci-central-ui.sh`, within the
existing shared ten-minute budget. No budget or product timeout is increased.

Two Python fixture **processes** each own a temporary on-disk journal, TaskDaemon,
real HTTP handler, token and MockBat. Both expose host `h1`, session
`sess-claude-0001`, the same actor and display names. Each has a different journal
`server_id`, `principal_id`, project/Work/workspace IDs and a private marker in its
message and Work goal. Principal IDs hash actor/scopes/admin rather than the
journal; B therefore has one additional valid `approve` scope, keeping the actor
and displayed resource names identical without inventing a principal ID.

Registry actions seed the resources using `settle_operations`. Worker scheduling
then stays paused: the UI POST creates a real `accepted` operation and `pending`
instruction receipt, retaining its original Idempotency key. No operation, receipt,
bootstrap, response or checkpoint is fabricated; no BAT send frame is emitted.
`tests/test_two_service_fixture.py` independently verifies the producer, identity,
pending receipt, listener reuse, original-key replay and cleanup.

Each pass types A's multiline Unicode draft, obtains its pending receipt/key,
selects a delivery repository/PR and session host, switches to B, checks an empty
composer and delivery fields, checks B's own checkpoint and source lists, types
B's draft, returns to A and checks original bytes/receipt/key. An explicit allowed
resend uses A's endpoint and original request/key. B's bytes stay unchanged. Host,
session, Work and PM workspace lists are checked despite identical labels.

Private test evidence defaults to ignored `desktop/test-results/two-service-isolation/`;
`BATC_ISOLATION_EVIDENCE` selects another directory. `requests.json` captures every
browser request and native forwarded request: method, URL, target endpoint,
Idempotency-Key, Authorization presence/fixture owner and body. Bearer values are
compared in memory and omitted from evidence. `storage.json` snapshots both areas
grouped by verified namespace, with neutral/legacy globals separate and credentials
redacted. `results.json` records pass counts and elapsed seconds. Test scratch,
browsers, Python processes and both listeners are removed after success or failure.

## Confirmed defect and limits

The first real A/B pass found the old global delivery fields displaying A's
private repository on B. The fix is confined to `viewDelivery`'s storage scope;
the old pair of global lookups/writes is retired. Its new family fits the existing
identity migration envelope and is covered by the family-copy unit test. There is
no ownership proof for old global hints, so they remain unused in place.

The existing composer/key/checkpoint scoping was not found defective. This proof
pins activation, scope generation, draft reads/writes, original operation-key
lookup, saved checkpoints and native transition invalidation; it does not replace
real BAT/provider, Rust bridge, vault, managed/external restart, packaged WebView,
real installation or R0–R5/A01–A40 acceptance. It contributes to E03/E04/E11 without
closing unrelated v1.2 work. No source is globally merged or transported.

## Managed crash/rebind followed by a producer reset (M07/M08)

The managed proof (`npm run test:managed-recovery-reset`, the same harness with
`--managed`) covers recovery on a new port with the same identity, a reset on the
first event poll, and storage continuity through reload in en-US and zh-TW.
Real parts are product `managed_runtime.ensure`/`serve`, HTTP/SQLite, bootstrap,
checkpoints, registry actions with `settle_operations`, and the UI-created accepted
operation and pending receipt. Only the harness-owned child may be killed; the old
port is occupied before `ensure` recovers the installation.

Stubs are native status/connect/recovery and credential IPC, MockBat, and paused
`session.send` scheduling. Connector requests must pass unchanged to the current
real endpoint; the client learns identity from real bootstrap. This proof does
not establish installed native or real BAT/model acceptance.

The first forwarded `/events` after recovery must return a producer-computed
`EVENT_CURSOR_RESET` from `dashboard_sync.check`:

- `cursor_expired` (`src/bat_agent_connector/dashboard_sync.py:141`): append an
  event beyond the saved cursor while the child is stopped, then prune through
  it so the existing deletion trigger advances the retention floor.
- `access_changed` (`src/bat_agent_connector/dashboard_sync.py:136`): hold event
  forwarding until a real `pm.workspace.member.set` change has settled, then
  release the poll.

Across recovery, reset and reload, every localStorage/sessionStorage entry must
remain byte-identical except the current `batc.sync.<namespace>`, which must
become the refreshed bootstrap checkpoint. Drafts, pending receipts and original
operation keys stay in their original namespace. Recovery must not automatically
POST; an explicit retry must reuse the original key and return the original
operation.

For checkpointed event polling, only `cursor_expired`, `cursor_ahead`,
`history_changed` and `checkpoint_cursor_mismatch` are identity-proven reset
reasons: `dashboard_sync.check` has already established the server and principal.
The client keeps this set in `IDENTITY_PRESERVING_RESETS`. A resnapshot with the
same verified namespace keeps an editing view's connection epoch and readiness
so events continue to be read and the refreshed checkpoint is saved. When editing
ends, the view must remount from fresh reads.

Every other reset reason, including unknown reasons, hides old facts and pauses
actions immediately, just like `access_changed`; editing does not defer this
invalidation. This includes a changed principal or server, unavailable identity
metadata and an invalid checkpoint. See the [producer reset-reason
table](dashboard-sync.md#身份持久化與恢復) for their definitions. The client tears
down the old view, shell and workspace navigation and shows the localized
sync-waiting text in `#main` before attempting bootstrap. If managed bootstrap
rejects the new identity, the old facts stay hidden. A different verified
namespace also requires invalidation and remounting.

Withdrawing a view does not delete or move its namespace: typed drafts and
original operation keys remain there unchanged, and no request is automatically
sent. The [managed view-reset tests](../../desktop/tests/managed-reset-view.spec.ts)
verify immediate withdrawal before and after a failed identity comparison,
byte-identical original localStorage entries and zero POSTs in both languages.
They also verify that a same-identity editing view retains its editor, draft and
selection through a completed resnapshot and a subsequent checkpointed poll.

Rust identity tests must derive valid and identity-negative bootstrap/capabilities
documents from the [producer golden](../../tests/fixtures/managed-bootstrap.json)
with coherent identity fields; only malformed-input tests may keep hand-written
documents.
