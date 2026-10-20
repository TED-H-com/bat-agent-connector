# Two independent services in one client (M04)

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

`npm run test:managed-recovery-reset` extends this same fixture and forwarding
harness with `--managed`: four passes, two reset reasons × en-US/zh-TW, at 1280px.
The runtime is started by `managed_runtime.ensure`, including its actual spawn,
manifest, lease, proof and identity verification. A test child entrypoint calls
product `managed_runtime.serve`; it supplies MockBat configuration and pauses only
`session.send` scheduling, keeping real accepted operations/receipts pending.
Shared registry seeding and membership actions use `settle_operations`. The
stdin control mailbox is fixture-only and exposes no new Connector API.

Only the harness's captured Popen is killed. A listener occupies its old port
while a second `ensure` recovers the same installation, identity and credential
on a different port. Native status/connect/recovery IPC remains a stub; it reads
identity from real bootstrap and forwards Connector requests unchanged to the
current endpoint. This is real Python runtime/HTTP recovery with a browser UI,
not execution of the Rust bridge, native vault or installed desktop.

The first forwarded `/events` after rebind returns a real 409 computed by
`dashboard_sync.check`:

- `cursor_expired`, `src/bat_agent_connector/dashboard_sync.py:141`: while the
  child is stopped, append a real event beyond the saved cursor and prune through
  that event. The existing deletion trigger advances `retained_after` beyond the
  client cursor. This models retention rather than fabricating an invalid token.
- `access_changed`, `src/bat_agent_connector/dashboard_sync.py:136`: after
  recovery, hold event forwarding while `pm.workspace.member.set` demotes the
  authenticated owner to contributor (a second owner already exists). Wait for
  the actual registry operation with `settle_operations`, then release the poll.

Hold the reset's bootstrap request to observe access-change teardown before any
facts can remount; the non-access case retains the exact composer DOM node.
Compare every entry in both storage areas, including other namespaces, legacy
and empty-scope sentinels, with one shared `storage-snapshot.ts` helper. Only the
current `batc.sync.<namespace>` may change, to the refreshed real bootstrap
checkpoint. The external reset spec uses the same full comparison. Pending
receipt/key bytes, unsent Unicode draft and namespace survive reset and reload.
No POST crosses the forwarding boundary during crash/recovery/reset or reload.
The pending receipt has no resend button; after asserting zero writes, the test
also explicitly submits the original composer request with Send, proving the
original key returns the original operation at the new port. No BAT send occurs.
Reload keeps the page origin and both storage areas; only static dashboard asset
requests are forwarded from the occupied old port to the recovered runtime.

The unchanged-main run exposed a non-access reset bug: `activate` cleared
`viewReady` and advanced the epoch, then the focused-form branch deferred routing.
Polling stopped with the old durable checkpoint and existing view guards became
obsolete. Same-identity resnapshots now retain an editing view's epoch/readiness;
changed identities and access boundaries still invalidate/remount. The regression
fails on the stale stored checkpoint before this fix. This contributes evidence
to E03/E04/E11 and M07 b/c/M08 b; it grants no R0–R5 or A01–A40 acceptance.

The first complete local managed run measured **80.65 seconds**, over this task's
60-second CI-admission threshold. The workflow is therefore unchanged. Proposed
wiring, after budget review: add `test:managed-recovery-reset` immediately after
`test:two-service-isolation` in `central-ui`, using the existing
`bash scripts/ci-central-ui.sh test:managed-recovery-reset` shared 600-second
budget. Do not increase that budget.

Rust bridge identity fixtures now include the producer golden at
`tests/fixtures/managed-bootstrap.json`. Managed recovery uses the full document;
external fixtures remove only `desktop_identity` and `managed_installation`, as
the Python golden test does. Changed identities update `sync`, `identity` and
`desktop_identity` together before deriving either HTTP response. `/capabilities`
is exactly `bootstrap.capabilities`: `ApiV1.bootstrap` calls `capabilities`, and
the Python golden test checks equality for both modes. The redacted golden proof
is a shape fixture; the Rust identity verifier does not verify checkpoint HMACs.

Malformed cases retained deliberately:

- `bootstrap_principal_mismatch_and_contract_missing_cannot_enroll`: retain its
  minimal `{"capabilities":{},"sync":{"version":1}}` branch because missing
  contract/identity fields are its subject; the actor-mismatch branch now uses
  the complete golden with coherent identities and a correct Content-Length.
- `contract_mismatch_cannot_unlock_operations`: retain the unsupported contract
  mutation, now applied to full producer capabilities.
- `wrong_identity_cannot_unlock_operations` and
  `cancelled_invalid_or_unsavable_replacement_keeps_old_record_and_connection`:
  retain wrong-actor negatives, now derived from full producer documents.

Unrelated malformed route/body, redirect and failed-transport tests retain their
inputs: they do not purport to be valid bootstrap documents. Native compilation
requires pkg-config and WebKitGTK, absent on this host. `bridge.rs` is outside
`scripts/ci_scope.py`'s native allowlist; its PR selects all four native build
entries, whose existing Cargo test steps execute these tests in CI.
