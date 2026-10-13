# Desktop foundation (R02)

This first slice packages the existing Dashboard in Tauri 2. It reads and operates the existing central Connector; it does not start a Python daemon, own tunnels, or maintain a task journal. The original colors, CSS, DOM helpers, forms, checkpoint flow, delivery flow and operation envelopes are reused from the Dashboard at `224bc7e`.

## One frontend

Edit `desktop/src`, never the generated Python static assets. The existing JavaScript UI remains JavaScript; its transport and event helpers use TypeScript. React/Vue are not introduced.

```sh
cd desktop
npm ci
npm run build:all
npm test
npm run test:ui
cargo test --locked --manifest-path src-tauri/Cargo.toml
# After `uv sync --locked --extra dev` at repository root:
npm run test:central
```

`npm run build` generates packaged assets in `desktop/dist`. `npm run build:browser` generates the original four permitted `/dashboard/` files, including a compatibility `i18n.js` stub; translations are bundled from `desktop/src/i18n.js`. The Python server keeps its same-origin authorization, file allowlist and CSP. CI rebuilds browser assets and rejects drift. `package-lock.json` and `src-tauri/Cargo.lock` pin dependency resolution; the initial build uses Node 24.21.0 and Rust 1.98.1, Tauri 2.12.1, Vite 8.3.4, TypeScript 7.0.2.

## Configure the native client

The client loads `central.json` from the platform app configuration directory:

| Platform | Configuration file |
| --- | --- |
| Windows | `%APPDATA%\io.betteragent.dashboard\central.json` |
| Linux | `$XDG_CONFIG_HOME/io.betteragent.dashboard/central.json`, normally `~/.config/io.betteragent.dashboard/central.json` |
| macOS | `~/Library/Application Support/io.betteragent.dashboard/central.json` |

Current bundled installations prepare a private local central, data and personal identity on an ordinary first launch. They do not require the user to enter a central address, actor or API token. Existing configuration remains selected on upgrade; a failed external connection does not create a replacement local database. See [managed installation](managed-installation.md) and [first use](../getting-started.md).

To connect a managed installation to another central, explicitly choose **Advanced: join an existing central**, review its trusted address and expected actor, then enroll that central's separate credential. The original managed service and work remain intact; its personal credential is not forwarded to the external address.

For a legacy client-only installation without a configuration file, Connection accepts the central address and expected API actor. **Review connection settings** opens a native confirmation showing the normalized endpoint and actor. Only that explicit confirmation creates the fixed `central.json`; no network request or credential lookup for the new endpoint occurs before saving. The supported contract is fixed by the client. Cancel and refusal retain the non-secret input draft in memory. A file created while confirmation is open, an existing invalid file or symlink, and a changed native connection generation all refuse replacement. Publication uses a synced temporary file and an atomic same-directory hard link with no overwrite; unsupported filesystems return an error and keep existing data.

Operators provisioning an external-central client before its first launch can copy `desktop/central.example.json` there and set the actual expected API actor. Existing managed installations use the Advanced flow above. The Connection screen displays the fixed path; **Reload configuration** rereads only that file and disconnects before a new identity can be used. The initial-configuration IPC requires native confirmation; request IPC cannot supply a URL or replace an existing endpoint. This configuration is client-local and is not another fleet host inventory. The endpoint must be an HTTPS origin or a literal loopback HTTP origin for an already authenticated SSH tunnel. The app neither creates that tunnel nor authenticates its ownership. Preserve the existing tunnel's host verification; an arbitrary loopback listener is not proof of central identity.

When connecting to an external central on Windows or macOS, choose **Add credential** and enter the Connector API token in the native secure dialog. The expected actor is fixed by configuration. Rust verifies the candidate before saving it in Windows Credential Manager or the local macOS Keychain for this OS user. **Replace credential** explicitly establishes a replacement identity; cancellation, verification failure or save failure retains the previous stored record and active connection. **Forget saved credential** removes only this configuration's local record and disconnects; it does not revoke the central token or erase drafts/operation IDs. Enrollment and storage never use a WebView password field, browser storage, clipboard-reading API, shell or subprocess.

Connection and enrollment read `/capabilities` and `/bootstrap`: the configured actor, API version `1`, contract version `2026-10-08`, `observe` scope and valid server/principal identity are required. A saved record pins the bootstrap identity. A changed backend/principal refuses reconnection until explicit replacement; every mutation, including binary upload, repeats identity verification before its effect request. The frontend also checks its bootstrap against the identity native just verified. These journal identities isolate credentials/drafts; they are not cryptographic transport identity and cannot prevent a trusted server or tunnel from being replaced between requests. HTTPS certificates or the verified tunnel provide that trust. Native requires bootstrap; the browser's older-server fallback remains unchanged.

`BATC_DESKTOP_TOKEN` remains a deliberate memory-only compatibility source on all platforms. Rust reads and removes it before constructing the runtime. When explicitly supplied with valid startup configuration it takes priority over the saved record, without automatic fallback on authentication failure. It is never automatically persisted. Changing endpoint/actor/contract through reload discards it; invalid/missing startup configuration also discards it rather than forwarding it to a later endpoint. After its first verification, its backend/principal remains pinned for that launch, including disconnect/reconnect. Successful enrollment or forgetting clears this launch source. Do not put a token in JSON, a URL, command arguments or frontend storage. Linux protected storage/enrollment remains unavailable; Linux retains the memory-only adapter.

### Credential boundary and lifecycle

The Windows adapter uses generic `CredUIPromptForCredentialsW` with `KEEP_USERNAME`, `ALWAYS_SHOW_UI` and `DO_NOT_PERSIST`; Windows must not save an unverified token. `CredWriteW` stores a generic credential with `CRED_PERSIST_LOCAL_MACHINE`, scoped to the current Windows user and computer, not enterprise roaming. Its fixed reference is `BetterAgentDashboard/central/v1/<SHA-256>` over length-framed normalized endpoint, expected actor and contract. The bounded record contains schema version, that binding, bootstrap server/principal and token. Reads check the binding/schema/size before use; unreadable records are refused instead of silently replaced. See the [Windows dialog contract](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-creduipromptforcredentialsw) and [credential persistence contract](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw).

Tokens are bounded to 512 ASCII bearer characters and records to 2560 bytes. Application-owned token strings, JSON byte buffers, native UTF-16 input buffers and the `CredReadW` secret allocation are zeroized when released. HTTP/OS library internals are not a claim of whole-process memory erasure. Credential Manager protects storage across users; software already running as the same Windows user can access that user's generic credentials. Fleet's observe-only credential/reference stays separate and is never reused as this Dashboard identity.

| Native command | WebView input | Result |
| --- | --- | --- |
| `connector_enroll` | `locale`: `en-US` or `zh-TW` only | Verified non-secret capabilities/identity, or null on cancel |
| `connector_reload_configuration` | None | Non-secret status after invalidation and fixed-file reread |
| `connector_setup_configuration` | Bounded endpoint, expected actor, supported contract, locale | Native confirmation, initial fixed-file creation only; non-secret status or cancellation |
| `connector_forget_credential` | None | Local removal result; no central operation |
| `connector_connect` / `connector_disconnect` | None | Verify configured credential / invalidate active generation |

All commands retain the main-window/local-origin checks. Only one connection/prompt may run at a time. Disconnect and configuration reload invalidate outstanding verification before another credential can activate or save. Responses from an older native generation cannot be adopted by the current frontend. While credential selection is pending, the shared UI pauses event processing and mutations and invalidates old asynchronous forms. Cancellation restores the original namespace; success mounts the newly verified namespace without replaying prior operations. An already sent operation cannot be unsent by disconnect; its original key/receipt remains the recovery mechanism.

Verification covers loopback HTTP and injected native vault/prompt lifecycle scenarios, shared browser/native IPC fixtures, and responsive English/zh-TW screens. Windows-only dialog adapter tests inject the native dialog return, without opening a dialog or using a real credential store. Windows native compilation/packaging belongs to exact-head CI; interactive dialog, real Credential Manager persistence across Windows logins and installed acceptance are separate evidence, not inferred from these mocks. The macOS Keychain adapter has separate real-store fixtures; Linux protected storage is not implemented.

The app works without BAT installed. Configured Windows installations use the [native Fleet controls](desktop-fleet-native.md) for independent connection/window choices, reviewed BAT launch, and explicit monitor/Startup migration. Native attachments use the bounded file adapter described below. Explicit signed updates use the [native update contract](desktop-updates.md); default validation packages keep signing disabled. Missing configuration and credentials remain visible in the app.

## Native boundary

Only the bundled `main` window receives generated application command permissions. Rust checks its label and local origin again. Navigation cannot replace it with an external site. The development origin is accepted only in debug builds. Native CSP allows IPC, without giving frontend `fetch` access to the central network.

`connector_request` accepts a method, a relative allowlisted API path, an optional JSON body and an idempotency key. It cannot accept an endpoint, filesystem path, shell command, arbitrary headers or credentials. Defined GET reads and central operation POSTs share the existing backend authorization/policy. Create operations require an idempotency key. Paths are checked before URL normalization; encoded path segments and traversal are refused. Malformed/control-character queries and unknown query keys are refused. Requests/responses and timeouts are bounded; redirects and environment HTTP proxies are disabled. Request errors never expose native credentials.

The initial allowlist accepts ASCII host/session identifiers; encoded/non-ASCII path identifiers are a remaining compatibility limit, not a reason to bypass validation. New central API routes require an explicit allowlist update and review.

Existing GitHub links call the narrow `open_external` action. Rust accepts credential-free `https://github.com` URLs without queries and sends them to the system browser. No raw opener, filesystem, shell or HTTP plugin permissions are granted to the WebView.

## Events, drafts and retries

Both builds use bounded one-second polling of the same central `/events` journal. Native streaming subscriptions are not implemented; the central SSE endpoint remains available to other clients.

When `/bootstrap` is available, the client takes its checkpoint before mounting views, isolates state by endpoint/server ID/principal ID/actor, then replays pages with the original checkpoint token and cursor. It persists the returned paired checkpoint only after every visible event's asynchronous view refresh has finished successfully, including legitimate `next_cursor` advancement over filtered migration facts. It never jumps to `head_cursor` on a bounded page and backs off if `has_more` makes no progress. Navigation pauses consumption until the new view is mounted.

`EVENT_CURSOR_RESET` reboots observation reads. Open edits defer the refresh until the user leaves/closes them; operations remain blocked during that interval. A changed backend/principal switches namespaces immediately so an old form cannot write drafts into the new identity. The initial bootstrap snapshot is not a separate authoritative UI cache: views read central data after the captured checkpoint. Older servers returning 404 for bootstrap use current views and polling without continuity guarantees; R04 must be deployed for reset/retention semantics.

Session text drafts, original operation keys/IDs and checkpoint state stay in browser storage under the identity namespace. Poll failures block operation POSTs while preserving the draft and retry key. A reconnect never starts another session or changes the operation envelope. A lost reply can be retried after reopening with the same key; a 409 retains that key. Existing unscoped browser storage is left untouched and is not automatically assigned to a newly identified backend. Migration of ambiguous pre-desktop drafts/unknown operations needs explicit reconciliation before replay.

Event acknowledgment now waits for asynchronous and debounced view refreshes, including all sibling reads on a failed batch. A locally displayed refresh error retains the original cursor/checkpoint pair and pauses mutations; polling retries the same page. Open edits defer renders and acknowledgment until they can refresh without discarding input. A pending refresh alone does not disable connected, version-checked form saves; an actual failed read or continuity reset does. Delayed refreshes are bound to the original account and mounted view. Mounted session events now refresh the observation header, pending questions/permissions and messages without replacing the composer or checkpoint forms. Pending answers are stored per identity and pending ID. Before answering, the client rereads the persisted pending observation and refuses a changed/missing ID; central still validates the live BAT frame because inventory reads are not write authority. Work-item pages refresh for linked operation/session/execution/checkpoint and parent-project events. Parent archive restrictions update immediately while an open edit remains intact; full rerender and acknowledgment wait until editing ends. Resource history/relations and discovery presentation are described below; complete M1 acceptance remains pending.

Initial session/message read failures retain the mounted event subscription. Session writes remain
disabled until those reads succeed; a one-second read retry can recover even before another journal
event arrives. Event-driven refreshes join any in-flight retry and then read fresh evidence before
acknowledgment. Navigation disposes the retry, and recovery preserves answer/composer drafts and
existing operation keys.

## Shared delivery controls

Delivery's environment cards, paginated history, rollback/retry confirmations and English/Traditional Chinese
labels now live in the same canonical `desktop/src` files. Browser assets are generated; the delivery backend
retains operation ownership. Deployment and deployment-environment journal events invalidate these cards through
the shared polling reader. Open confirmations keep their fixed identity and preview; a stale generation requires
a refreshed preview and another explicit click. Stable identity-scoped intent keys survive a lost reply and reload.
These cards have the same documented asynchronous view-refresh limitations as other views.

The native bridge explicitly allows the delivery preview, record, environment and history GET routes and `recipe`
query parameter. Deployment writes still go through `/operations` with the same envelope and idempotency key.
The restricted native GitHub opener is unchanged; arbitrary provider/enterprise origins are not supported by it.

`npm run test:delivery` runs the real daemon's delivery fixture with fake GitHub/BAT/runtime providers and serves
this worktree's generated assets. It requires the delivery backend and its dev dependencies; while the changes
are on separate branches, set `BATC_DELIVERY_ROOT` to that backend worktree. It covers both languages at
390/768/1440px, cursor paging, readiness/scope refusals, fixed confirmations during polling, stale previews,
double clicks, deploy-only retries, provider URL filtering, and lost-reply keys across reopening/reloading.
Only a legacy backend's expected `/bootstrap` 404 is excluded from console-error checks; this does not claim
checkpoint support for that backend. The CI UI fixtures separately exercise paired checkpoint polling through
browser HTTP and mocked native IPC. Neither fixture is a live deployment or Windows WebView acceptance test.

## Packaging and lifecycle

```sh
# Windows, from a configured Visual Studio/Rust environment
npm run tauri -- build --bundles nsis

# Linux, with WebKitGTK 4.1 and AppIndicator development packages
npm run tauri -- build --bundles deb

# Native fixture smoke of that release package (Linux + Pillow/xdotool/Xvfb)
xvfb-run -a -s '-screen 0 1440x900x24' dbus-run-session -- node tests/native-smoke.mjs src-tauri/target/release/better-agent-dashboard
```

Linux builds select the exact upstream GLib backport described in
[`desktop/vendor/README.md`](../../desktop/vendor/README.md). Desktop CI verifies
all vendored bytes and the target dependency graph, exercises the affected
iterator with release optimization, and runs the packaged release WebView against
an isolated HTTP fixture. Windows remains on its GLib-free dependency graph.
These checks are native fixture evidence; installed/live acceptance remains separate.

The app bundles frontend assets and does not require Vite in production. Closing the main window hides it; the tray offers Open Dashboard and Quit Dashboard. Explicit Quit uses [normal exact-owner Fleet shutdown](desktop-fleet-native.md), refuses unconfirmed/foreign ownership, and leaves central work and manual BAT windows alone. The single-instance plugin focuses the current desktop window; Fleet separately enforces the shared cross-session ownership protocol. Fixed native supervisor/login CLI dispatch precedes the WebView and single-instance plugin. Native and PowerShell backends are mutually exclusive under the same ownership guards.

The desktop workflow packages unsigned Windows NSIS and Linux deb artifacts for validation. The separate signed-candidate workflow verifies updater signatures and writes a fixed feed without publishing it; signing identities, Authenticode and installed acceptance remain operator release setup. macOS builds provide separate Apple Silicon and Intel DMGs with ad-hoc signatures for validation; Developer ID signing and notarization are separate release gates.

After packaging, `node tests/windows-installed-smoke.mjs` installs the NSIS package on the
disposable GitHub-hosted Windows runner. It refuses workstations, existing app state,
registered installations and running Dashboard processes. It checks installed binary
hashes against the build with Tauri's exact NSIS bundle marker, PE/registered versions and
bundled license bytes; then connects
the real WebView to an observe-only loopback fixture using a synthetic memory-only token.
It checks WM_CLOSE hides the window without terminating its process, a second invocation
restores that same window, and terminating/relaunching the owned process resumes polling
without changing configuration. Finally it checks silent uninstall removes the binary
and version registration. JSON receipts, process/HTTP diagnostics and window screenshots
are uploaded as `desktop-windows-installed-fixture`, including diagnostics after failures.

Managed first-run and reopen readiness use `tests/windows-managed-view.cs`, a dedicated
MTA .NET Framework UI Automation process. Each read has an 8-second cap within the same
30-second readiness wait; a timeout remains fatal. Exit **0** means a completed read with
boolean JSON, not necessarily readiness: all existing English/zh-TW authentication,
setup, empty-configuration and enabled-save labels, document presence and bounds must
still be positive. Exit **3** retries a transient provider read in that original wait:
`ElementNotAvailableException`, `UIA_E_ELEMENTNOTAVAILABLE` (`0x80040201`),
`RPC_E_DISCONNECTED` (`0x80010108`), `RPC_E_CALL_REJECTED` (`0x80010001`) or
`RPC_E_SERVERCALL_RETRYLATER` (`0x8001010A`). The latter COM outcomes describe a
disconnected, rejecting or busy callee; a fresh read can recover. All other failures
exit **2** and stop immediately, including a missing cache property or an invalid cast.
See Microsoft's [UIA errors](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-error-codes)
and [COM errors](https://learn.microsoft.com/en-us/windows/win32/com/com-error-codes-3).

The query caches its three requested properties for every returned raw-view node,
including text outside the default control view. `AutomationElementMode.None` permits
cached reads; no current-property calls are needed. An unavailable node is skipped
without contributing partial readiness. Null/unsupported values supply no positive
evidence, especially no default enabled-button value. Cache-contract errors are not
swallowed. This follows the [.NET Framework caching contract](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/caching-in-ui-automation-clients)
and [`GetCachedPropertyValue` contract](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.automationelement.getcachedpropertyvalue).

`<name>-probe-phases.json` records `attempt_count` and the last 20 `attempts`, each with
its ordinal, phase flags, exit code (null if no exit was observed), unavailable-node
count and last `error_type`/`error_hresult`. Stderr diagnostics include only exception
type and an eight-digit hexadecimal HRESULT for `ExternalException` (including
`COMException`); no exception messages, stacks, names, values or entry contents are
retained. `<name>-readiness.json` contains only the latest completed read, or an empty
object after a failed read. Failure screenshots are still captured before cleanup.
`windows-installed-support-test.ps1`, run by the installed fixture, exercises retry,
deadline, fatal-error and evidence behavior and compiles deterministic C# probe tests
against the same Framework assemblies on Windows.

Installation uses the documented [NSIS command-line switches](https://nsis.sourceforge.io/Docs/Chapter3.html).
The [pinned Tauri bundler](https://github.com/tauri-apps/tauri/blob/tauri-cli-v2.12.1/crates/tauri-bundler/src/bundle.rs)
changes the first `__TAURI_BUNDLE_TYPE_VAR_UNK` marker to `__TAURI_BUNDLE_TYPE_VAR_NSS`
inside the package and restores the unpatched build afterward. The fixture permits exactly
that transform and rejects any other byte change. An Authenticode setup needs its own
reviewed post-signing payload proof; it is not silently exempted by this unsigned/updater-only check.
Linux CI extracts the built deb into a disposable directory and exercises its actual executable.
This is native installed fixture evidence, not a physical tray interaction, normal Quit,
real Credential Manager enrollment, sign-in persistence, signed updater execution or live
central/BAT acceptance. The script never runs on an operator's existing installation.

## Evidence and remaining acceptance

On the Linux development host: both frontend builds, Rust compilation/tests/clippy, existing Python static allowlist/CSP test, and Chromium browser/IPC-mock tests passed. The separate `test:central` integration uses the real Python HTTP API and journal with MockBat: signed checkpoint replay, actual retention reset, and draft recovery passed through the generated browser UI. Browser screenshots at 390/768/1440 preserve the original dashboard layout. A debug deb was built. The genuine packaged Linux WebKit view read a loopback fixture's capabilities/bootstrap/data/events without Vite; a window-manager close event hid it while retaining the process, and a second app invocation exited and restored the same window. This verifies close handling and instance handoff, but not a physical tray interaction. These are fixture results, not live BAT/central service acceptance.

Installed Windows tray/sign-in behavior, native profile restoration, cross-session ownership, production credentials, native files, real Startup migration and end-to-end work against real central/BAT hosts remain acceptance gates. Their fixture and compile evidence is distinct from installed validation; signed update release configuration remains a separate lane. The foundation and follow-up slices do not claim M1 or complete product acceptance.

Implementation references: [Tauri capabilities and application commands](https://v2.tauri.app/security/capabilities/), [tray](https://v2.tauri.app/learn/system-tray/), [single instance](https://v2.tauri.app/plugin/single-instance/), [system opener](https://v2.tauri.app/plugin/opener/), [Windows installers](https://v2.tauri.app/distribute/windows-installer/).


The shared confinement and cleanup slice displays persisted creation claims separately from current verification, including the unverified Codex/BAT gap. Checkpoint continuation and integration repair keep the chosen agent. It ports the existing central cleanup preview, explicit reviewed apply, retained-content list, tombstones, and operation item receipts; restoration remains unavailable.

Cleanup drafts and uncertain apply requests use the captured backend/account namespace. An uncertain reply keeps the exact signed preview, fingerprint, and operation key on reload; switching accounts cannot reuse them. A target/choice edit invalidates an in-flight preview, and cleanup events await history and retained-content refresh without rebuilding the reviewed form. Native cleanup preview IPC accepts only typed central IDs, booleans, and at most 500 resource IDs per choice; exact cleanup read routes have their own query allowlists. Apply still uses the existing central operation journal.

`npm run test:cleanup` runs generated shared assets against the merged cleanup HTTP endpoints, temporary real Git repositories and MockBat. It uses this checkout by default; set `BATC_CLEANUP_ROOT` to test a separate compatible backend checkout. This check covers signed preview, reviewed apply, actual temporary worktree reclamation and per-item receipts. Browser/native IPC fixtures also cover lost replies, identity separation, deferred events and both languages at 390/768/1440 widths. These tests do not prove live OS confinement or complete M1 acceptance.


The attachment slice reuses central immutable artifact revisions. Work-item input/result references and checkpoint-continuation inputs live in the same canonical form code. Browser File objects remain in WebView memory. Native selection and drag/drop use OS dialogs/events and a bounded Rust spool; the WebView receives opaque handles, names, sizes, digests and local receipts. The old raw-byte native upload IPC permission is removed. Only draft text, immutable references and exact operation intents are stored in identity-scoped browser storage. An uncertain continuation retains its original source precondition, manifest and key after reopening.

See [native file transfers](native-files.md) for the Rust-only credential binding, exact receipt verification, streaming, stop/retry/cancel distinctions, restart recovery, Save As collision policy and safe preview limits. Native requests use fixed content paths under the trusted configured origin; no server-supplied content URL is followed. Browser upload keeps the same-origin fixed route and refuses redirects. Existing artifact selection still pages by the central cursor. Manual remote capture remains a separate read-only source review.

`BATC_ARTIFACT_ROOT=/path/to/integrated/backend npm run test:artifacts` retains the existing real-central browser/materialization fixture. `npm run test:native-files` runs the Rust native adapter against temporary real central HTTP operations/content: exact-key replay, lost content reply recovery, verified binary Save As, literal preview and unchanged temporary manual Git source bytes/index/refs. Rust and browser fixtures mock OS interaction; they do not prove installed Windows picker/drop/save, credential prompts or live host behavior. The Linux GLib release gate and Windows installed acceptance remain distinct.



## Manual remote file capture

The shared capture form appears in manual session details and alongside attachment drafts. It requires an
explicit configured host, full manual session ID and one relative source path. A reviewed preview shows the
filename, byte count, SHA-256, source root/repository, HEAD and expiry before an explicit save. This is a remote
single-file read, with no directory listing or local OS picker. Central revalidates source identity, authority
and bytes; the frontend's manual observation and scope checks are only an early gate.

Capture uses the existing preview and `artifact.capture` operation. Identity-scoped storage retains the source
draft, reviewed evidence, exact request/key and accepted operation through reload or a lost reply. An accepted
intent reads back the original operation even after preview expiry. A result becomes selectable only after its
ready revision, reviewed digest and central capture operation proof match. Adding it to an attachment draft
requires a separate click; capture does not assign project ownership or save the surrounding work item.
Malformed stored preview data cannot crash the page or replace a recoverable accepted intent. Event callbacks
join pending reads and refresh evidence before acknowledgment; failed reads retain the event cursor.

Native IPC permits only the fixed `/artifact-capture-previews` POST with a typed host/session/relative-path
body. It refuses query parameters, unknown fields and unsafe paths, while accepting valid Unicode, spaces and
literal percent characters in relative paths. Credentials remain native and requests target the configured
central origin. No arbitrary filesystem or shell command is added.

`npm run test:capture` uses the real central HTTP API, temporary Git source, a fixed readonly local helper and
MockBat. It checks binary content, accepted readback after reload, unchanged source bytes/index/refs, and no
BAT writes or inferred project/work-item ownership. Browser/native IPC fixtures cover recovery, identity and
scope boundaries plus en/zh-TW layouts at 390/768/1440. Windows WebView and live remote host acceptance remain
pending. Managed-result capture/acceptance and explicitly bound published-version continuation now have
separate central actions and shared UI (see artifacts.md and repository-sync.md); full dirty directory
snapshots and direct unpublished Git transport are not exposed.

## Observation UI (R04)

Session pages expose central connection, loading, tab, activity, lifecycle, enumeration and freshness separately, with each field's observation time, source and stale flag. Unloaded/no-tab/idle never imply an ended lifecycle. Host discovery shows recorded profiles, last successful observation, latest failure, read authority, methods, coverage and explicitly unscanned scopes; the desktop starts no additional scanner.

Session, known worktree and execution pages consume the existing history and relations endpoints. Each independently paged section retains its own server `as_of` and filters. New journal facts display a notice without replacing loaded pages. Explicit refresh starts a new snapshot; unknown occurrence times stay unknown and are distinct from recording times. Relations show stable IDs, roles, half-open sequence intervals, commands and evidence; only proven worktree IDs become links. History/relations grant no write authority.

Connection-scoped resource records are indexed by stable ID with explicit parent/linked-resource dependencies. Inventory walks `order=id` with gone records included, preserves loaded page count and filter selection, and keeps the visible scroll anchor on refresh. This is a refreshed inventory walk, not snapshot isolation. History pages remain mounted during session updates. During an open work-item edit, safety reads continue once per second while an event acknowledgment waits, so a later parent archive still disables actions without destroying the draft. General detail renders wait until the editor closes.

`npm run test:observation` checks the generated shared UI against actual central HTTP, the journal and MockBat: a newly observed pending question, fixed history pagination, execution/worktree relations and discovery. `BATC_OBSERVATION_PYTHON` can select an already prepared Python environment; the fixture always imports this checkout. Chromium browser/native IPC fixtures cover failed reads, stale pending refusal, linked completion, later parent archive, stable-ID inventory pages, and English/zh-TW layouts at 390/768/1440. These are fixtures, not live host acceptance. Native route permissions already cover these read-only endpoints.

Remaining R04 limits: history exposes kind and relation execution/closed filters but no time-range picker, event-type catalog or saved multi-resource queries. Inventory refreshes the loaded window rather than maintaining a persistent offline resource database; selected history state is retained while mounted, not across route changes. General project tree refreshes still defer under open drawers; central version/policy guards remain authoritative. Principal/backend switches and continuity resets continue to preserve only drafts in their own namespace. Pending preflight reads the latest persisted observation; central must still reject a changed live BAT tool-use ID. Native event streaming and full M1/T07 live acceptance remain unclaimed.

## Managed session permission requests

The shared session detail has a compact permission form only for positively observed Connector-managed
sessions. Applying requires successful current session reads, `operate` scope, an explicit allowed
`session.permissions` capability, and `writes: true` for the selected host. Missing evidence disables
the action; manual and unknown sessions never mount it. Central resource policy, confinement and task
coordination remain authoritative. There is no force control or native policy bypass.

Normal permissions and Allow all are requested modes, not observations of the running agent. The form
explains that Allow all bypasses agent approval prompts for file writes and commands. A successful
operation means BAT accepted the requested configuration, not independent proof of live enforcement;
Codex settings take effect on its next turn. Streaming Claude is refused with `PERMISSIONS_STREAMING`;
after idle, the user must explicitly start a new change. The UI never queues a deferred permission change.

The existing typed `/operations` transport sends `session.permissions`, the exact host/full session ID,
and `{mode: "default" | "allow_all"}`. Current session observations expose no authoritative task
`control_version`, so the UI omits that optional precondition; central admission binds the incarnation.
It never substitutes an unrelated resource or event version. No new Rust command or URL permission is added.

The requested mode, full request, original key and accepted operation ID persist in the existing
endpoint/server/principal namespace. A lost reply locks the choice and allows only the same request/key;
an accepted operation uses GET readback on reload or refresh. Terminal results require an explicit
"Start another change" before a new request, resetting the selection to Normal permissions. The same
explicit reset is available for `TASK_PAUSED`, `CONTROL_VERSION_CONFLICT`, `PERMISSIONS_HOST_POLICY` and `CONFINEMENT_RAISE_REFUSED`
admission refusals: central checks them after existing-key replay and before inserting an operation.
Generic authorization errors, unknown actions, key conflicts and transport failures do not prove that
an earlier attempt was never accepted and keep the original key locked. `uncertain` and `needs_attention` retain the fixed intent
and link to the existing operation page's per-frame steps, refs and result. Response action, target,
requested mode, original key and accepted ID must match before the form stores success. Damaged saved requests
preserve a recoverable accepted ID instead of generating another operation. Event acknowledgment waits
for required operation reads, including a submission whose first reply has not arrived yet; failed reads
keep the cursor and disable another change.

Browser and mocked-native fixtures cover lost replies, mode edits, accepted/unknown recovery, capability
and identity boundaries, malformed storage, and en/zh-TW layouts at 390/768/1440. Actual-central permission
validation follows the backend integration; these synthetic fixtures do not establish Windows WebView
or live host acceptance.

## Tailscale sign-in recovery (T10)

Connection settings reuses the existing compact panel/actions for sanitized Tailscale
login state, explicit Windows tray-app opening and diagnosis after focus returns.
The fixed native command preserves original local request receipts and shares the
Launcher/monotonic Quit-update fence. It does not change network configuration,
Fleet selection or central credentials. Browser/unsupported/missing installations
remain explicit; installed Windows sign-in and tray behavior are not yet proven.
See [the exact T10 adapter contract](tailscale-recovery.md).

## macOS packages and native verification

The platform configuration produces an `.app` inside a `.dmg`, with the existing icon
converted to ICNS and a minimum system version of macOS 13. Native CI runs on macOS 15
for Apple Silicon and Intel separately; older supported OS versions are not yet exercised.
The ordinary workflow sets `APPLE_SIGNING_IDENTITY=-` for an ad-hoc signature. This
verifies bundle integrity but does not establish a Developer ID identity or notarization.
See the upstream [DMG](https://v2.tauri.app/distribute/dmg/) and
[macOS signing](https://v2.tauri.app/distribute/sign/macos/) contracts.

`node tests/macos-installed-smoke.mjs` refuses non-hosted runners, existing app state and
existing Dashboard processes. It mounts the actual DMG read-only, copies its app into a
unique disk directory, compares every bundled file and internal symlink against the built
app, verifies its signature, architecture, version and resources, and unmounts the image.
It launches that copied app through NSWorkspace/Launch Services against an authenticated
observe-only loopback fixture and captures the real WKWebView. The AppKit helper checks
the exact bundle URL, PID and launch date before controlling the owned application.
A native helper requests hide and the fixture proves the actual hidden/window state
(the hosted OS can return false even when asynchronous hide succeeds). Finder/Dock
activation restores the same window/process. The native Accessibility close-button action
then hides that window and Finder/Dock restores it again. A second executable hands off, and a normal system Quit
followed by relaunch resumes polling without changing configuration. Owned app/state and
temporary files are removed on exit. No Accessibility privacy setting is changed.
The close-button action requires existing Accessibility trust; physical pointer and
menu-bar interaction remain manual acceptance.
Normal Quit requires an accepted native termination request and observed process termination;
Launch Services does not expose the application's exit status to this fixture. Only the
second direct executable invocation has a child-process exit-code assertion.

The macOS event loop handles Dock/Finder Reopen and routes system Quit through the same
shutdown function as tray Quit. These changes do not make the Windows Fleet supervisor,
Startup migration or Windows updater available on Mac. Mac supports Keychain enrollment through a native secure system dialog. Signed in-app
updates and real central/BAT acceptance remain distinct implementation/acceptance work. Receipts identify
the exact candidate and platform; adding this fixture does not itself record a passing run.

### macOS Keychain

The native adapter uses Core Foundation's secure text-field notification (English/zh-TW),
with cancellation and a five-minute timeout. It runs on the enrollment worker; no token
passes through shell arguments, a subprocess, WebView, browser storage or clipboard API.
The existing central capability/bootstrap verification and generation fence run before
`SecItemAdd`/`SecItemUpdate`. The fixed service is `io.betteragent.dashboard.central.v1`;
the account is the same length-framed endpoint/actor/contract SHA-256 binding used on Windows.
The local Keychain record contains the bounded schema, identity and token, without enabling
iCloud synchronization. Missing items are distinct from locked/denied/unreadable failures;
replacement does not delete the old item first. Application-owned copies are zeroized;
Core Foundation and Security framework internal buffers are not a whole-process erasure claim.

The hosted-Mac Keychain fixture creates a disposable keychain, restores the prior search
list/default, and deletes the fixture even on failure. It verifies real create, read from
a separate process, replacement, malformed replacement refusal and deletion. Shared bridge
tests cover verification-before-save, cancellation and configuration changes. Interactive
secure-field entry and signed-app Keychain access across upgrades remain user acceptance.
