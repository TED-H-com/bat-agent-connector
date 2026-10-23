# Managed desktop installation and background service

Implementation: **included in this source version, introduced in [PR #81](https://github.com/TED-H-com/bat-agent-connector/pull/81)** (2026-10-10). Match package evidence to the exact source commit and consult the linked checks for validation records. Older published release assets are historical client packages and do not automatically contain this runtime.

This contract covers the ordinary local installation and its advanced external-central alternative. Python Connector / Task Service remains the sole business authority. Windows Fleet manages local connection and startup choices; BAT executes work on the selected hosts. Rust owns packaging, native credentials, windows and restricted lifecycle controls, not another task journal or scheduler.

## Implemented first-use journey

1. Install a matching Windows, Mac or Linux candidate package. It includes the matching Python runtime; the user does not first install Python, uv or developer tools.
2. Launch the application. It verifies the bundled artifact, prepares private local data, one owned Python central and a personal identity, then connects the shared Dashboard. No endpoint, actor or copied API token is required for this local path.
3. The first screen uses the OS/browser language: `zh-TW`, `zh-Hant`, `zh-HK` and `zh-MO` (including subtags) select Traditional Chinese; other locales select English. **English / 繁體中文** is visible before entering anything and in Settings. The explicit choice persists in `batc.lang`, overrides later OS changes, and controls the setup-guide language.
4. Choose **Review connection / 預覽連線**, inspect the source, changes and effect boundary, then choose **Apply connection / 套用連線**. Review creates no operation and does not probe or save the connection; pasted credentials may already have been privately staged, which the review shows separately. Cancel leaves the connection unapplied. One usable saved BAT remote connection is reused through the existing central profile import. Several usable connections require a plain-language choice. When none is available, paste a masked connection link copied from BAT's trusted Settings screen. Setup never guesses credentials, discovers trust pins or reads the clipboard automatically. Encrypted/damaged credentials remain unavailable. Central still probes the pinned host before publishing setup. The simple review flow uses connection-only defaults: writes, orchestration and shared clones stay off, even if an older advanced draft enabled them. No SSH, directories, model choices or paid API keys are required.
5. Expand **Advanced / 進階設定** only when needed for managed permissions, dedicated roots, SSH, exact repository bindings or verification commands. Existing validation and opt-in permissions apply. Repository setup does not push or create/merge PRs; saving verification commands does not execute them.
6. Open Web through **Open Dashboard in browser** or the tray/menu-bar entry. The private one-use handoff signs into the same identity; the bearer never enters command arguments, URLs, browser storage or logs.
7. Choose **Start when I sign in** for installation-owned autostart. Closing the browser or Dashboard does not stop central work. Fleet retains its own connection/startup ownership rules.

Existing external-central configuration stays selected. **Advanced: join an existing central** requires explicit native confirmation and separate credential enrollment. It preserves the existing owned background service and never forwards its credential to the new address. Failure to reach an external service cannot provision a second database.

## Setup connection readout

`GET /managed/setup` adds `connection_observation` to each configured host. GET
never probes BAT, refreshes Inventory or sends a POST. Inventory remains the source
and validity authority: Setup and Work use `Inventory.current_source_evidence`,
`_observation_reason` and Inventory's `_valid_until`. No second binding hash or
freshness window is computed.

The bounded envelope is `{version: 1, facets: {transport, service, trust_sign_in,
connection}}`. Every facet has `value` (`ready`, `unavailable`, `missing`, `unknown`),
`observed_at` (epoch seconds or null), `valid_until` (producer ISO deadline or null),
`reason` (bounded code or null for successful observation), and `source`:
`{host, profile_id, binding_version, kind, ref}`. The accessor supplies the current
binding even before an observation exists. Inventory facts reference
`hosts_observed:<host>`; writer transport references `fleet_client:<host>`.
Changed bindings withdraw earlier evidence. The overall connection uses the same
value, attempt time, success-based deadline and reason as Work's Inventory-derived
`can_observe`, independently of Work's configuration/action blockers.

Inventory records `failure_reason` by exception type at failure production:
`ConnectionLost` → `transport`, `FingerprintMismatch` → `tls_trust`, `AuthError` →
`auth_or_protocol`, `TokenUnavailable` → `credential_missing`, `InvokeError` →
`service_refused`, `InvokeTimeout` → `service_timeout`; all other types → `unknown`.
`AuthError` covers both sign-in and protocol negotiation; no finer claim is made.
Error text is never parsed or included in Setup. Idempotent nullable-column DDL
runs when Inventory opens; old rows retain unknown classification, with no data
step or `user_version` allocation. Success clears the metadata. Overall currentness
retains Inventory/Work's `never_observed`, `host_unreachable`, `host_not_refreshed`
and `DISCOVERY_SCOPE_CHANGED` reasons; typed failures explain the affected facet.

A live writer's reader starts after authentication and negotiation (`client.py`,
BAT `bat-remote/v2`). Observing it supplies only transport evidence with an explicit
read clock, Inventory-produced deadline and `authenticated_socket` reason. It
cannot establish workspace, Work capabilities or `setup_ready`.

Compatibility is additive. Older desktops receive byte-identical `connected`,
`connection_status` and `connection_checked_at` behavior, including the historical
inclusive recency window and live-client null clock. New desktops consume only
supported version-1 envelopes. Older Connectors, absent envelopes and future
versions show unknown and cannot produce `setup_ready`. The `connected === true`
fallback and Setup's local three-state formatter/labels are retired. No legacy
producer rule is removed: it is retained solely for older desktop compatibility.

All four facets use shared `displayFact` / `healthLabel`. Expiry is inclusive
(`now >= valid_until`), with no implicit local validity fallback. The existing
shell `health-display-tick` repaints ages and expiry; no second timer is added.
Settings and Work share `claimObservationRenewal`: record a deadline before
awaiting one GET; failures and unchanged deadlines never retry. A normal read with
a new deadline permits renewal. One setup GET covers every configured host, so all
due source/deadline pairs are claimed together before that read. Pending reads keep
Checking; only completed failures or unchanged responses show an unrenewed result.
Full reads take priority: a deadline is still spent when its observation-only
read is skipped behind an in-flight full read, and a later full read supersedes
any pending observation reply. No discarded or skipped read retries that deadline.
For an unchanged configuration revision, host-name set and source bindings,
renewal patches only observations, keeping mounted draft controls, focus, review
and receipt. A changed revision or source identity applies the reply through the
normal refresh path, including review invalidation. It needs no extra GET.
Normal updates and clock paints share one automatic-status computation: review
support, reviewing and changed-review messages, then applying an existing intent,
then the observation/receipt status. Read failures retain aged facts and show the
failure through the shared formatter. These display clocks admit no actions.

A succeeded `setup.host` receipt records verification and saved configuration at
its `updated_at` completion time. `setup_verified_saved` always uses that time,
never the observation clock. Current readiness requires the matching target host's
fresh overall Inventory fact; a live writer or unrelated host cannot override it.
IDs, history, drafts and original operation keys remain intact. This readout makes
no additional claim about starting Work and never resubmits setup.

## 與「加入機器」共用的契約（已定，尚未實作）

本節補充上方現行首次設定，未實作新的角色精靈。程式查核包含 main `511fc618`
與合併後的連線預覽實作；下列 `file:line` 已於合併 main `94dbddc8` 後重核位置。預覽 API 見 [setup API 契約](api-v1.md#managed-setup連線預覽與受理)。
責任分工只維護於 [Fleet 唯一責任表](fleet-configuration.md#責任表與退休條件)；
共用欄位、狀態、變更、lifecycle、健康投影、驗收與 D1–D8 的產品決定／技術預設
均引用 [Fleet 共用契約](fleet-configuration.md#加入機器共用契約)，本處不另訂一份。

| 首次設定特有行為（現況） | 可核對的輸入／輸出 |
| --- | --- |
| UI 選 BAT 來源 | 自動模式沿用可讀 BAT profile，多項時選擇，無可用項目時貼遮蔽連結；parser 拆出 endpoint／pin／token。[managed-setup.js:10](../../desktop/src/managed-setup.js#L10)、[managed-setup.js:360](../../desktop/src/managed-setup.js#L360) |
| 簡易連線預覽送出值 | writes／orchestrate／shared_clone_worktrees 為 false，managed_roots 為空；advanced 才送 SSH／管理選擇。[managed-setup.js:677](../../desktop/src/managed-setup.js#L677) |
| UI 恢復原請求及接受收據 | 已知 operation ID 時用 GET 查原操作，否則帶原 request／key 送出；接受收據前核對 operation ID、actor、action、target、params、preconditions 及 key。[managed-setup.js:618](../../desktop/src/managed-setup.js#L618)、[managed-setup.js:635](../../desktop/src/managed-setup.js#L635) |

2026-10-22 經 owner 授權由整合者決定 D1＝A、D6＝A、D8＝A，角色入口須移至本機服務準備前，
完整保留與驗收規則只維護於 [已定契約](fleet-configuration.md#已決定的產品契約2026-10-22經-owner-授權)。
這是上方 managed-first 旅程的後續改接，尚不代表現有首次設定可免建本機服務。
後端對帳與新 setup 阻擋規則只引用
[Fleet 的現況／規範狀態表](fleet-configuration.md#預覽--套用--讀回)。

## First-run visual evidence

Synthetic shared-browser fixtures, identical data and light theme, 1440×900 and 390×844. These are not installed Windows or live BAT acceptance. Before captures use the original `origin/main` UI; after captures use this implementation. Every PNG is under 90 KB.

| Locale / width | Before | After |
| --- | --- | --- |
| English / desktop | [Before](first-run-simple/before-en-1440.png) | [After](first-run-simple/after-en-1440.png) |
| English / 390px | [Before](first-run-simple/before-en-390.png) | [After](first-run-simple/after-en-390.png) |
| 繁體中文 / desktop | [Before](first-run-simple/before-zh-TW-1440.png) | [After](first-run-simple/after-zh-TW-1440.png) |
| 繁體中文 / 390px | [Before](first-run-simple/before-zh-TW-390.png) | [After](first-run-simple/after-zh-TW-390.png) |

Capture after rebuilding browser assets, from `desktop/`:

```sh
BATC_FIRST_RUN_SHOTS=after npx playwright test tests/first-run-evidence.spec.ts --workers=1
```

The translation regression checks explicit English/zh-TW entries and variables for setup, managed/native setup and errors, scans screen sources for translation calls and rejects literal DOM prose. Browser and IPC regressions cover locale detection/persistence, safe defaults, masked-link fallback, ambiguous connections, original-key recovery and the three connectivity states. Backend tests reproduce a successful read-only scan followed by closed sockets, missing observations, recent failures, stale evidence and changed bindings.

## Ownership, identity and storage

The managed launch reply and native status distinguish `bundled_runtime` from
`running_runtime`. Both carry `version`, `commit`, provenance `build_source`,
observation `source` and an epoch-seconds `observed_at`. The bundle fact is the
`build_identity()` snapshot this launcher loaded at startup (`this_process`), not
an installed receipt. The running fact comes from the proven owner's existing
authenticated capabilities read (`capabilities`), whose build is that service's loaded snapshot.
Missing identity fields remain null, including for an older service.

The shared version projection also distinguishes the answering Connector's loaded
build from its updater's `connector_installed` row, sourced only from
`capabilities.connector_update`. It describes the installation for the next launch,
with its own capabilities read time, channel and last result/time; it is visible in
both browser and desktop. `connector_installation_difference` compares commits as
`same`, `differs` or `unknown`. Missing or non-`reported` update evidence leaves this
row unknown with `not_reported` as its source, while the explicit reason remains
visible. This is separate from `running_runtime` and its launch observation; it
does not start, stop or replace a service. Updater ownership is established at
process startup as specified in the [update contract](connector-updates.md).

`runtime_difference` is `same`, `differs` or `unknown`; either missing commit
makes it unknown. `differs` means the running service is not that bundle and the
change waits for the service's next launch. These facts do not trigger replacement,
restart, stop or refusal. Same-version reuse and the existing version upgrade gates
are unchanged; readiness is not a build comparison. Native launch/status fields are
additive and optional, and recovery refreshes the recorded observations. Build
differences do not trigger a restart. Explicit owned-service stop/resume follows
the lifecycle contract below, with Python as its sole owner.

- The persistent installation manifest binds runtime version, data location, installation actor, server ID and principal ID. Reopening and concurrent clients recover that installation.
- Central uses kernel file leases, not an occupied port, stale PID or matching path, to enforce a single owner. The endpoint pointer is checked against the owned database, then a challenge verifies the live service before any bearer is sent.
- Concurrent first clients open an existing POSIX lease without `O_CREAT`, or
  create it with `O_CREAT | O_EXCL` after `ENOENT`. A losing creator reopens the
  existing inode; it never replaces or unlinks another client's lease. The shared
  private-file helper bounds this lookup/create reconciliation to eight pairs and
  reports `PrivateFileCreateRaceError` (`EAGAIN`) if the leaf keeps changing.
  Other create errors propagate. Every open keeps `O_NOFOLLOW`, ownership, private
  mode and single-link checks; truncation still waits for validation. This is
  file-creation reconciliation, not a retry of `ensure` or application effects.
- Windows uses private ACLs, verified handles, reparse rejection and native locking. POSIX uses private owned directories/files and process locks. These protections also cover journal, token and atomic configuration publication.
- The package pins its compressed runtime and executable digests. Installation does not download and execute an unversioned script. The frozen runtime starts without Python or uv on the user's PATH.
- Browser handoff is short-lived and single-use. The resulting session uses an HttpOnly SameSite cookie plus origin/CSRF checks; this is not a public unauthenticated hosting mode.
- Setup mutations use central durable actions, opaque staged credential references and the reviewed configuration revision. Publication is atomic. Recovery reads the original revision/receipt rather than inventing a new intent; live configuration changes wait for a safe point.

### Credentials and browser connections

API tokens are bearer credentials for HTTP and `/rpc`, with their existing actor
and scopes. A managed browser-session credential is accepted only through the
browser cookie path. It requires the existing exact-origin, fetch-metadata and
CSRF checks. Only the session restore GET omits the CSRF header; it still requires
the origin and fetch metadata. Browser-session credentials presented as bearers
receive exactly the same status and error as unknown credentials on that path.
Managed browser entry and service stop continue to require an API bearer.

Existing session records identify the cookie-only credentials, including sessions
created before an upgrade. Opening the journal creates the session table
idempotently without changing the schema data-step version or rewriting tokens.
Cookie sessions retain their lifetime, parent binding, actor and scopes. Long
reads recheck the same credential class, expiry and revocation. This corrects the
credential acceptance boundary; it adds no protocol channel or capability and
does not change API request/response shapes or the contract version.

When a remembered bearer receives HTTP 401, the Dashboard displays its localized
refusal state and pauses further automatic presentations. HTTP 403 pauses it only
on the reads required to run the Dashboard: capabilities, bootstrap and event
polling. Other 403 responses retain the existing action or resource error; the
connection and polling continue. One shared decision covers ordinary requests
and file transfers. Once refused, the pause covers startup, page restore,
polling, retries and file requests.

The token remains stored because it may still be valid for the original service.
A separate refusal record identifies its domain-separated digest, endpoint and refusal status.
It is kept in memory and in the same store as the token, preferring local storage
when the token is remembered there; session-only tokens leave no durable refusal
record. The pause survives reloads, and remembered tokens stay paused in new
tabs. The person can Connect with a new token or choose Retry remembered token.
Disconnect, successful replacement with a different token and explicit retry
remove the corresponding refusal record. Tabs at the same origin observe stored refusal changes, including an explicit retry in another tab. Credential checks before requests are synchronous; preparing the storage key does not delay Connect or Retry. Work drafts, pending intents and
operation keys stay in their original namespaces. If browser storage is
unavailable, the current page retains the pause in memory.

Cookies are scoped to a host, not a port: browsers can send them to other ports
of the same host. Origin checks and HttpOnly do not provide port isolation or
prove which installation is listening. Per-installation channel binding remains
future work. Pausing after a refusal also does not prevent the first credential
presentation or establish server identity.

On Windows, a private reader can temporarily block replacement of a manifest or
endpoint pointer. Atomic replacement retries only native rename errors 5 and 32,
with at most six attempts and a 500 ms retry deadline. Every attempt checks the
private parent and target again, retaining the same pinned directory and flushed
temporary file. Persistent contention preserves the original error and target;
cleanup cannot hide the original failure. ACL, reparse and hard-link checks,
handle sharing and immutable artifact publication keep their existing rules.

The inverse collision occurs after publication, while the writer retains DELETE
access through flush and close. Native read-only opens retry only sharing violation
32 for at most 500 ms, pausing up to 20 ms between attempts. Access denial (5) and
validation failures propagate immediately. The packaged upgrade observer uses this
same private reader and reports the final winerror without its own storage retry.

## Background recovery and upgrades

Network recovery retries connections and reads, not a possibly accepted start, prompt, integration, merge or deployment. Original operation IDs, request keys, reservations and receipts remain authoritative. Manual and unknown resources remain read-only unless existing creation evidence proves the managed identity.

A newer bundled runtime proves ownership and asks the current central to stop safely. Active/nonterminal work blocks this transition. The launcher holds its lock through shutdown and replacement, waits for the owned kernel lease, then starts and verifies the new runtime against the same journal and identity. It never kills a PID found in a pointer. Downgrades and unknown version ordering are refused. Interrupted upgrades recover the same saved installation.

Each distributed core change requires a distinct increasing three-part Python
package/runtime version (`pyproject.toml`, `__version__` and the lockfile). The
bundled manifest must match the actual executable's version readback. The desktop
package version also increases for a new distribution. Updating only the desktop
version is insufficient: `ensure` deliberately reuses a verified running core of
the same runtime version. Development commits do not each need a version bump;
release preparation fixes the next package identity before final validation.

The published update candidate pairs core `0.2.10` with desktop `0.1.6`. Its packaged
upgrade fixture builds the published core `0.2.6` from fixed source
`f31e7964c627e4b6d3af2e2370e7a38c64a20e25` and compares the two actual payloads.
Earlier `0.2.4` → `0.2.5` receipts remain historical evidence for their original
candidates. Version declarations alone are preparation, not proof of a passing
package build, published release or upgrade of an operator's installation.

Expired/revoked credentials, invalid manifests, changed identity or missing ready-state data are recovery failures. They do not cause automatic credential reissue, deletion of history or replacement ownership. Reinstallation reuses preserved application data; deliberately deleting that data is outside ordinary reopen/upgrade behavior.

Explicit desktop Quit follows the existing Fleet shutdown guards and leaves the independent central service running. Stopping the managed service is separate and requires its own verified owner and safe-stop checks.

## Recovery after the service exits

The managed Dashboard checks local endpoint reachability with a two-second read
bound. After at least ten seconds of failed reads, it reuses the existing serialized
`Managed::request("ensure")` path with a sixty-second launcher deadline. Failed
recovery backs off for 15, 30, then at most once per 60 seconds. Launcher and service
kernel leases still decide ownership; no PID from disk is killed.

Before rebinding, the Launch reply must pass the startup protocol, bundled runtime
version, literal loopback endpoint and credential checks. Its server, principal and
actor must match the original binding, and ordinary capabilities/bootstrap reads
must verify the same identity. A successful rebind advances the bridge generation,
invalidating in-flight responses and file scopes. A native recovery counter triggers
fresh navigation/shell reads even when restart produces no event; failed views
retry through the existing deferred-render guards. Failed initial managed reads
remain eligible for native recovery; an explicit Disconnect disables it. Client state
across recovery/reset follows the [shared managed resnapshot contract](two-service-isolation.md#managed-crashrebind-followed-by-a-producer-reset-m07m08).
Managed `/capabilities` and `/bootstrap` both include `desktop_identity`. An
external central may omit that field from bootstrap; the native bridge still
checks the bootstrap server/principal against its verified credential identity.
Missing the optional field alone is not evidence of an account change.
Disconnect, credential removal or switching configuration cancels a pending rebind.
External-central connections never use this recovery loop, provision a local
service or transfer a local credential to another endpoint. Recovery errors appear
as short English/zh-TW status labels and visible recovery explanations, with a
fixed diagnostic code in connection details;
a changed identity stops automatic recovery until the connection is reviewed.

### Verified storage identity and legacy port migration

The native managed route uses `managed:v1:<server_id>:<principal_id>:<actor>`
(each component URI encoded) for **all** shared-frontend storage. The native
configuration source must be `managed`, and bootstrap must pass the existing
identity checks before any view mounts or migration runs. The persistent journal
`server_id` already distinguishes installations; adding `installation_id` would
split the same journal unnecessarily. Principal identity includes effective actor,
scopes and admin status. Workspaces, Works and exact Attempts retain their existing
key suffixes and request bodies. A runtime port is only the current transport.

Previously, the namespace included the endpoint. The managed runtime selects a
random port on each start, so relaunching the native app after a service restart
could hide local drafts, operation keys and checkpoints under the old port. The
data was still stored. In-session recovery does not activate a new scope and did
not reproduce that relaunch defect. This explains a reproducible failure mode,
not a diagnosis of a particular installation without its local evidence.

External-central native connections keep the endpoint in their namespace, even
when that central advertises a managed installation: the explicit native route,
not a remote capability, determines the storage rule. Distinct external bindings
remain isolated. `bridge.rs` continues to reject a bootstrap whose verified server
or principal changes. Browser clients keep endpoint scopes too: a changed port is
a different origin with separate localStorage, which this migration cannot read.

Migration runs only into a destination with no scoped data in either localStorage
or sessionStorage. Exactly one legacy loopback-port scope must have an exact
`batc.sync.<scope>` key whose encoded server, principal and actor all match the
verified bootstrap. The checkpoint key delimits the old identity; an action suffix
on a draft alone is insufficient. Only that complete scope is copied, byte for
byte, including nested form namespaces. Nothing is submitted or automatically
retried as part of migration. A matching retained draft can therefore reappear
after an upgrade even if it looked lost on the previous port.

The originals remain unchanged for rollback. A local
`batc.storage-migration.<new-scope>` receipt records source, destination, version
and copy status. A completed receipt prevents deleted/consumed drafts and keys from
being imported again. A failed copy rolls back only its exact newly written values;
an interrupted copy or failed rollback retains its marker and refuses activation
instead of reading a draft separated from its original operation key. This case
needs explicit recovery; automatic repair is not supported.

Multiple matching old ports are **not** merged or ranked, even when their values
look identical or one port only has orphan drafts/tab preferences without a
checkpoint. Existing destination data, missing old checkpoint keys, legacy
keys without server/principal proof, unrecognized legacy key envelopes, ambiguous namespace boundaries, non-loopback
endpoints and different identities are left in place. Reverting to the old frontend can still read original old-port keys
when connected at that original endpoint, but later edits in the new scope are not
copied back automatically.

`batc.sync.<scope>` stores only `{cursor, token}`, not a last-used time or a
namespace generation. The cursor orders journal events, not local draft edits:
several restarts can save the same cursor with different drafts. A restored
journal also retains its `server_id` while its cursor regresses; the B02 backend
test covers both regression and divergent reuse of a sequence. Thus even a
unique highest saved cursor does not prove the latest namespace. This migration
deliberately leaves all such sources untouched. A user with several old scopes
gets an empty new scope. The text recovery panel below makes matching old drafts
readable without selecting or importing a namespace. Original operation keys and
pending requests stay in their original scope: copying draft text alone must not replay a pending request. Do not clear storage or guess a
source from its port or cursor.

#### Reading drafts from earlier local addresses

After native managed bootstrap verification, an optional deferred scan finds old
loopback HTTP port scopes with the exact verified server, principal and actor.
It uses the same canonical endpoint rules and exact checkpoint-key boundaries
as migration. Orphan scopes without a checkpoint and overlapping dotted-actor
boundaries are excluded. Browser clients and external native connections do not
scan. A completed migration receipt excludes its source, even if the migrated
draft has subsequently been consumed. No cursor ranks drafts. The panel does not
cover orphan scopes, ambiguous boundaries, different identities, non-loopback
addresses, unverified connections, generated Work checkpoint-continuation summaries
(`draft.continue.<checkpoint>.<work item>`) or unrecognized key families. Those sources
remain available for explicit manual review/export of the original storage,
including their operation evidence; the panel does not activate or repair them.

When recoverable text exists, **Settings → About → Drafts from earlier local
addresses** appears. A non-blocking notice links there the first time that
verified identity has recoverable text. The notice stays eligible until its link is
used or the panel is opened, lives inside the main layout, and follows language
changes. Each entry shows its localized form and field names,
original loopback address and read-only text. The audited writers do not store a
draft-edit timestamp; checkpoint cursors, operation receipt times and dates typed
inside a form are not presented as an edit time.

Entries with an `intent`, `submission`, artifact-review `accept`, or a
sibling `batc.key` record are **possibly sent requests**, even when their result
is unknown, unreadable or has no operation ID. Artifact capture alone does not
classify review text as possibly sent: `artifact.capture.managed` carries a preview
token, while `artifact.accept` carries the review receipt. Session writers may append a
request digest to the sibling key. Mirrored form/attachment text retains the
submission evidence from either writer. These entries are shown in a separate
read-only group labelled **May already have been sent — check it before sending
it again**, with links to their known `#/op/<id>` operations on the same Connector,
or to the original session/form when no operation ID is known. They have no
**Copy text** action and are never presented as ordinary drafts. Inspecting them
does not look up, replay or adopt an operation key.

For ordinary draft text, **Copy text** copies only that entry's text to the clipboard. If clipboard access
is unavailable, the text is selected for manual copying. **Hide** writes a local
marker scoped to the verified identity and a digest of the original key/field.
It survives relaunch and never deletes the original. The notice has its own local
marker. Nothing is sent, resubmitted, loaded into a current form, merged or
automatically deleted. Current drafts, operation keys and checkpoints are untouched.
Unreadable storage is skipped with a content-free console diagnostic, without an
error dialog or error summary. The scan yields between batches and never gates
view activation; connection changes invalidate its result and controls. Cancelling
a connection change retains the panel. The identity-preserving reset reasons
`cursor_expired`, `cursor_ahead`, `history_changed` and `checkpoint_cursor_mismatch`
preserve the panel instance, expansion, focus and reading position, but Copy/Hide
remain inert during bootstrap verification and until the current identity's rescan
completes. Every other reset reason, including `principal_changed`, `server_changed`,
`identity_unavailable`, `checkpoint_invalid` and `access_changed`, withdraws cached
text before re-verification; failed bootstrap cannot revive its controls. An
ordinary transport outage does not revoke the completed identity scan, so local
inspection, copy and hide remain usable without Connector writes.

The recovery reader uses explicit editable-text fields from each producer, rather
than walking arbitrary JSON. Some writers persist all fields when one changes,
including loaded values and defaults. Legacy storage has no per-field authorship
proof, so the sources marked below may contain server text; their writers are
unchanged. The inventory below is separate from the complete-scope migration
inventory: migration still preserves all original bytes together.

| Key family (without `batc.`) | Text recovery decision |
| --- | --- |
| `draft` | Raw session `send` text; `answer.answers`; Work edit `fields.title/goal/request/acceptance`; published/dispatch attachment `text`; session continuation `continue.<checkpoint>.text`. Only the Work continuation key `continue.<checkpoint>.<work item>` is excluded as a generated summary. Work edit fields may include text loaded from the server, not only what the person typed. Generated Work continuation text, queue flags, attachments and agent choices are excluded. Submission metadata is used only to classify possibly sent requests. Identical published/dispatch prompts mirrored in the corresponding form appear once; orphan or differing attachment text remains recoverable. |
| `create` | Project `values.name/description/task_project`; Work `values.title/goal/request/acceptance/steps`. Repository, ancestry and state selectors are excluded. |
| `pm.<scope>.<action>.<target>` (including nested form scopes) | Allowlisted editable fields for discussion messages/titles, Work instructions/follow-ups, plan text, analysis purposes, input values/references, rationale/reasons, governance prose, product-goal/definition-of-done rationale (not their server-prefilled text), sprint goals/capacity notes, meeting notes/output, maintenance review prose, PR title/body and handoff prompts. Per-criterion review reasons and authored text within meeting decisions/follow-ups/outcomes, maintenance follow-ups/public sources and roadmap milestones are extracted field by field. Hidden inputs (including the overview-assessment carrier title), structured source-reference selectors, model/adapter choices, IDs, policy configuration, selected receipts, structured plan snapshots and intent/request bodies are excluded from displayed text. The editable `pm.input.record.reference` is included; it is not a structured source selector. Intent presence and operation IDs only classify/link possibly sent requests. Malformed collections are skipped independently without discarding other fields. PM form values may include text loaded from the server, not only what the person typed, as well as form defaults. New action/field shapes require an explicit reader update. |
| `pm.<scope>.discussion-send` | Handoff `content` only; thread identity is excluded. |
| Other `pm` keys | Exclude discussion origins/indexes, follow-up/assessment pointers, adopt/start landing records, adapter/profile-return/release selectors and all nested selection/navigation/verifier keys. They contain provenance or navigation, not authored drafts. |
| `start`, `published`, `dispatch` | `title` and `prompt` only. Repair-dispatch keys (`<project>.<repair work item>`) and `.shortcut` keys are excluded. Targets, agent/model/source choices and previews are excluded; intents only classify/link possibly sent requests. |
| `orchestrate` | `message` and each item's `title/prompt` only; host, session/workspace, queue, agent and limits are excluded; intents only classify/link possibly sent requests. |
| `labels` | Editable `text` only; reviewing current labels may persist server values. These may include text loaded from the server, not only what the person typed. Base version is excluded; intents only classify/link possibly sent requests. |
| `artifact-review` | `reviewText` only; paths, selected evidence and previews are excluded; only `accept` classifies/links possibly sent review text; `capture` never carries it. |
| `managed.setup`, `repair`, `capture`, `task-control`, `session-bat`, `permissions`, `approvals` | Exclude configuration, credentials, paths/identifiers, resource choices, previews, control flags and durable request/recovery data. |
| `model-preferences`, `project-skills`, `cleanup.draft/pending/intent` | Exclude preferences, skill selections and cleanup selections/receipts. |
| `key`, `sync`, `tree`, `sessions`, `attention`, `composer-shortcut`, sessionStorage `delivery` | Do not display or adopt keys, checkpoint proof, navigation, filters or preferences. Sibling operation metadata only classifies/links possibly sent requests. |
| `earlier-drafts.<managed scope>.*` | Local hide and acknowledged-notice markers; never displayed as text or adopted from an old scope. |
| Globals, credentials, Fleet bindings and in-memory view state | Outside the verified legacy envelope; never read or copied by recovery. |

All panel text, form labels and clipboard fallback text are available in English
and Traditional Chinese. This is local text inspection only; it does not prove
native installation, real BAT/model behavior or physical-mobile acceptance.

The namespace audit covers these durable families uniformly (names below omit
`batc.`); memory-only consumers use the same verified namespace:

| Stored state | Storage family / scope |
| --- | --- |
| Drafts, original operation IDs/keys and sync proof | `draft`, `key`, `sync` |
| PM forms, intents, handoffs, exact Work/Attempt navigation | `pm.<scope>.*`, nested `pm.*-selection`, `pm.*-navigation`, `pm.governance.selection`, `pm.verifier` |
| Setup, create, repair, capture and work controls | `managed.setup`, `create`, `repair`, `capture`, `task-control`, `start`, `orchestrate`, `published`, `dispatch` |
| Resource forms, approvals and cleanup | `labels`, `session-bat`, `permissions`, `artifact-review`, `model-preferences`, `project-skills`, `approvals`, `cleanup.draft`, `cleanup.pending`, `cleanup.intent` |
| Text recovery hide/notice markers | `earlier-drafts.<managed scope>.*` |
| Tab preferences | `tree`, `sessions`, `attention`, `composer-shortcut` |
| Standalone delivery repository/PR selection | sessionStorage `delivery.<scope>.repo`, `delivery.<scope>.pr` |
| In-memory view selection and navigation folds | `viewState(namespace, workspaceId, view)` and `pm-workbench` maps, including `project-navigation`; no durable keys to copy |

`ui/view-state.js`, `pm-workbench.js`, project navigation and workbench perspectives use the same new namespace in their
bounded in-memory maps; they have no durable data to migrate. Delivery selections
use the same verified scope; the old global `batc.repo` / `batc.pr` hints remain
untouched and are never adopted because they cannot prove their source. For keys
outside this namespace and why they may cross sources, use the
[out-of-namespace key inventory](two-service-isolation.md#every-key-outside-the-connector-namespace).
Migration does not turn retained native file handles into
valid handles for a different credential; existing native checks still apply.

`tests/fixtures/managed-bootstrap.json` is produced and drift-checked by
`tests/test_api_v1_bootstrap_golden.py`, using the real Python bootstrap handler.
Both recovery and relaunch Playwright specs load this same de-identified response;
the external-central omission is checked against the real handler too. Regenerate
the golden with `BATC_UPDATE_BOOTSTRAP_GOLDEN=1 pytest-disktmp uv run pytest -q
tests/test_api_v1_bootstrap_golden.py`. These are automated contract/transport
fixtures, not installed Tauri, BAT/model or operating-system restart acceptance.

### Windows job objects

Console detachment (`DETACHED_PROCESS` / `CREATE_NEW_PROCESS_GROUP`, or the Fleet
supervisor's `CREATE_NO_WINDOW`) does not escape a Windows Job Object. A job with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` can terminate its inherited children when the
launching session closes. This explains a possible external termination without a
Python exception; it is a cause hypothesis, not a live-host diagnosis.

The Connector and native Fleet supervisor query the launching job and request
`CREATE_BREAKAWAY_FROM_JOB` (`0x01000000`) when `BREAKAWAY_OK` or
`SILENT_BREAKAWAY_OK` permits it (also harmless outside a job). Unknown/restrictive
policy keeps the existing flags; native access-denied refusal of the breakaway
creation retries once without that flag. Each affected launch emits only one fixed
lifetime diagnostic, with no paths, tokens or exception text. Connector warnings
are written to its private `service.log`; Fleet records the current launch warning
in native memory, exposes it in the localized Fleet view and also emits native stderr. A
restrictive enclosing job can still end the background child, so detachment cannot
promise survival against a policy that forbids it. POSIX retains `start_new_session`.

References: Microsoft [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
[Nested Jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs)
and [process creation flags](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags).
Nested parent jobs may still constrain breakaway; creation refusal is handled
without changing their policy, bypassing ownership checks or spawning duplicates.

## Evidence and delivery boundary

Candidate source and fixture locations:

| Boundary | Source / automated fixture |
| --- | --- |
| Runtime ownership, identity, restart, upgrade and refusal | `managed_runtime.py`; `tests/test_managed_runtime.py` |
| Private storage and Windows portability | `platform_files.py`, `windows_files.py`; `tests/test_platform_files.py`, `tests/test_windows_central.py` |
| Guided setup, read-only probes and revision recovery | `managed_setup.py`; `tests/test_managed_setup.py` |
| Browser handoff and shared setup | `browser_sessions.py`; `tests/test_browser_sessions.py`, `desktop/tests/managed-setup-browser-integration.mjs` |
| Bundled integrity, empty child PATH and concurrent clients | `desktop/scripts/build-managed-runtime.py`, `desktop/tests/managed-runtime-fixture.py` |
| Distinct old/new packaged cores, retained workspace/history and busy refusal | `desktop/tests/managed-upgrade-fixture.py --previous-resources <old-bundle-directory> --current-resources <new-bundle-directory>` |
| Native installation/login lifecycle | `desktop/src-tauri/src/managed.rs`, `managed_login.rs`; platform jobs in `.github/workflows/desktop.yml` |

Use the [current main checks](https://github.com/TED-H-com/bat-agent-connector/actions?query=branch%3Amain) and [desktop workflow](https://github.com/TED-H-com/bat-agent-connector/actions/workflows/desktop.yml?query=branch%3Amain) for validation by source commit. Match the package to a successful run from the current public history; historical PR artifacts are not the download source. Package fixtures use controlled temporary resources; a successful job is evidence for its source commit and platform, not every user environment.

The packaged upgrade fixture requires two original manifest/payload pairs with
different binary digests and increasing actual versions. It verifies concurrent
upgrade clients, original identity and PM workspace, original operation key and
history, restart, and downgrade refusal. A separate synthetic uncertain operation
must keep its old owner and original intent while blocking replacement. Its
explicitly launched fixture process tree is reaped for teardown; the product never
kills an owner to upgrade. Passing output includes both binary digests and cleanup.
This supplements clean-profile installation evidence; it does not establish an
in-place app installer upgrade or acceptance of an operator's existing data.

The publication extension uses the earlier checkout's own Python product source
with this checkout's supporting test fixtures to produce a real Work/Attempt/Result and a GitHub REST 201 receipt
in the earlier core's owned journal. Only the outer completion is interrupted;
receipt rows are not synthesized. That producer releases its journal while its
FakeGitHub/MockBat peers remain alive. The current core's ordinary `serve` child
then opens the same data, and `ensure` verifies its ownership and identity. The
original key resolves the same operation without a second creation call or push;
immutable Work/Attempt/Result and creation-receipt rows, original events, Result
API content and manual checkout bytes are checked.
Only fixture-owned process handles are terminated for fault-case teardown.

The receipt records the old producer version/source hashes and the added time,
which must be at most 90 seconds including cleanup. The source harness can test
two actual source revisions locally; distinct frozen binaries remain CI evidence
for their exact platform/head. Existing Linux and macOS receipts prove continuity
on their recorded heads; the revised wait needs new native CI evidence. Its
seed log, fixture setup stages and 40-second thread dump are preserved on failure.
A test-only audit plugin records monotonic child-start and fixture start/end
events without replacing subprocess or socket functions. Collection start/end
events separate test-module imports from fixture setup. Large host arguments
remain opaque (length and digest only); phase detection reads at most a 128-character
plain-text prefix. The fixture's own host-call await records duration, and fixture
setup samples at most twelve main-thread frame locations once per second. Import
origins and reverse-lookup call stacks identify which checkout owns a slow fake.
Environment values and frame locals are omitted; the same log redactor is used
before printing and uploading.

The seed wait fails after 20 seconds without a useful timeline event, or at an
unconditional 75-second readiness ceiling. Collection/fixture transitions, child
starts and host-call boundaries count as progress; periodic stack samples, socket diagnostics
and arbitrary output do not. There are no retries or Intel-specific skips.
An Intel macOS failure reached readiness at 47.127 seconds despite the former
45-second limit: the observer-to-first-fixture interval was 10.903 seconds, and
15 host calls took 26.475 seconds, with the longest call taking 3.855 seconds.
The 20-second inactivity bound rounds 1.5 times that largest observed progress
gap up to five seconds. Recent Intel whole-case receipts ranged from 23.602 to
50.545 seconds; adding one inactivity interval to the slowest receipt and rounding
up to five seconds gives the 75-second ceiling. The whole-case budget is 90 seconds,
retaining the former 60 - 45 = 15 seconds for recovery and cleanup. The new core's
recovery waits are unchanged. The 40-second faulthandler timeout and 60-second
provider window after readiness also remain unchanged. Numeric-only fake HTTP
binding and bounded observer argument handling remain in place.
Linux source measurements and virtual-clock deadline tests establish the rule's
behavior; the revised wait still needs Intel native CI evidence on its exact head.
Pytest's `--setup-show` prints fixture completion; the timeline locates slow work.
macOS retains the existing packaged workspace/identity/busy checks. Windows also
keeps those checks; its publication path and real OS-vault continuity remain open
acceptance items.
This is upgrade evidence: managed downgrade refusal remains unchanged, and the
separate plain-updater rollback cases cannot stand in for a managed rollback.

The owner explicitly excluded formal signing, Mac notarization, production update channels and Linux persistent native credential enrollment from this delivery. Full human execution of the 46-item real-environment acceptance matrix is user-owned and is not an outstanding agent task. Exclusion does not mean these capabilities or checks were completed. Unknown native BAT merge acknowledgements preserve original work under the [merge contract](worktree-merge.md); its separate human-adjudication API is not a delivery gate.

See [first use](../getting-started.md), [implementation status](../product/implementation-status.md), [acceptance reference](../product/acceptance-v2.md) and [shared frontend lifecycle](shared-frontend.md).

## Deliberately stopping the owned background service

Provider and Both installations offer **Stop background service** in native Settings.
The preview explains that the local Connector stops, unsettled work blocks shutdown,
external connections are unaffected, and app open, sign-in, recovery and **Open in
browser** will keep it stopped until **Start background service**. One confirmation
sends the request. Client installations own no service and offer neither control;
native Stop/Start also refuse them before calling the launcher.

Python remains the sole start/stop owner. Under `launcher.lock`, it writes a private,
versioned `state/lifecycle.json` stop hold (`version: 1`, `state`, `requested_at`,
`stopped_at`) before sending the proven installation owner's authenticated
`/api/v1/managed/stop` request. The existing operations, tasks and active-work busy
gates are unchanged for the first accepted stop. Any HTTP 4xx is a definitive
refusal before an effect and removes the hold: 409 returns `SERVICE_BUSY` with a
registered reason, or generic localized busy text for an unknown reason; other
4xx responses return `SERVICE_STOP_REFUSED`. The service remains running. An
acknowledgement followed by release of the service's kernel lease records `stopped`
and its time. No PID or port is used to stop a service.

An acknowledgement without lease release at the deadline, a 5xx response, a bad
acknowledgement or a transport failure retains `stop_pending`. This means
**stopping**, not running or failed. Both Stop and Start remain available. The
person can wait or choose Stop again, review the same preview and confirm once.
That new explicit action verifies the owner and sends one stop request if the
lease is still held; if it is free, it records `stopped` without sending. The
same refusal and uncertainty rules apply. The authenticated stop route is
idempotent once shutdown is accepted: it returns the same acknowledgement without
re-running busy admission or signaling a second effect. Ensure, resume, status
and recovery never resend a pending stop automatically.

Native Status returns the cached snapshot without the launcher or the operation
serial lock. Launcher results and refusals carrying lifecycle refresh that cache;
successful ensure/resume record running. An external CLI stop is observed by the
next recovery ensure, and an external resume by the person's Start action. CLI
status still reads the hold without writing, even if the service subsequently
exits. Only stop, ensure or resume, while holding `launcher.lock`, may reconcile
`stop_pending` to `stopped`.

Every ensure path honours the hold before starting: ordinary native startup, the
background recovery loop, `--managed-login`, browser handoff and CLI `ensure` or
`browser`. They receive `SERVICE_STOPPED` with the lifecycle and its times. Native
startup opens Settings normally with the stopped state; login entry stays quiet.
A cached hold skips the recovering announcement and launcher. Real recovery
attempts announce `MANAGED_RECOVERING`; a hold first discovered by that ensure
may briefly show recovering before settling and clearing the recovery error,
without retries or backoff growth. The internal CLI `serve` also refuses an
existing hold. Unreadable, symlinked,
foreign-owned or unknown-version holds return `SERVICE_LIFECYCLE_UNPROVEN`; none
falls back to provisioning.

To start a service while its stop hold remains, choose explicit **Start background
service** / CLI `resume`. Under `launcher.lock`, while the previous service retains
its lease, resume returns
`SERVICE_STILL_STOPPING` and keeps the hold. Once the lease is free, it removes and
syncs the hold, then runs ordinary ensure once under that same lock. Both-role
external connections retain their existing endpoint, credentials and selection.
This runtime-root file adds no SQL DDL or journal data step.

## 主動停止自有背景服務

Provider 與 Both 安裝在原生設定提供「停止背景服務」。預覽先說明本機 Connector
會停止、尚未完成或核對的工作會阻擋停止、外部連線不受影響，以及開啟程式、登入電腦、
自動恢復或「在瀏覽器開啟」都不會重新啟動，直到選擇「啟動背景服務」。確認一次後才送出。
Client 沒有自有服務，不顯示這兩個控制；原生 Stop／Start 也會在呼叫 launcher 前拒絕。

Python 是唯一啟停 owner。在 `launcher.lock` 內，先寫私有、具版本的
`state/lifecycle.json`（`version: 1`、`state`、`requested_at`、`stopped_at`），
再以已證明的安裝 owner 憑證呼叫 `/api/v1/managed/stop`。首次停止時，既有操作、任務
及執行中工作的 busy gate 完全保留。任何 HTTP 4xx 都是副作用前的明確拒絕，會移除 hold：
409 回傳 `SERVICE_BUSY` 與已登記的原有原因；未知原因只顯示通用的在地化忙碌訊息。
其他 4xx 回傳 `SERVICE_STOP_REFUSED`，服務維持運作。收到停止確認且 kernel lease
已釋放，才記錄 `stopped` 與時間。
不使用 PID 或 port 終止服務。

若已確認但 deadline 到時 lease 仍未釋放、收到 5xx、不正確的確認或 transport 失敗，
保留 `stop_pending`，顯示「停止中」，不宣稱運作中或失敗，並同時提供停止與啟動。
你可以等待，或再次選擇停止，查看相同預覽並確認一次。這個新的明確操作會先驗證 owner；
lease 仍在時只送一次停止請求，已釋放則直接記錄 `stopped`，不送請求，結果規則與首次相同。
停止路由已受理後具冪等性：同一個已驗證 owner 的重複請求回傳相同確認，不重跑 busy gate，
也不再次發出停止信號。ensure、resume、status 及自動恢復不會自動重送 pending 的停止。

原生 Status 直接回傳快取，不執行 launcher，也不取得操作的 serial lock。帶有 lifecycle
的 launcher 結果及拒絕會更新快取；成功的 ensure／resume 記錄運作中。外部 CLI 停止由
下一次 recovery ensure 觀察，外部 resume 則由使用者選擇啟動時觀察。CLI status 讀 hold
不寫檔，稍後程序退出也一樣。只有 stop、ensure 或 resume 在持有 `launcher.lock` 時，
才能將 `stop_pending` 對帳為 `stopped`。

所有 ensure 入口在啟動前遵守 hold：原生開啟程式、自動恢復、`--managed-login`、瀏覽器
handoff，以及 CLI `ensure`／`browser`，都回傳含 lifecycle 與時間的 `SERVICE_STOPPED`。
原生開啟正常進入設定並顯示已停止；登入啟動保持安靜、不顯示視窗。恢復流程將 hold 視為穩定
狀態。已快取的 hold 不公告恢復中，也不呼叫 launcher；真正恢復時公告
`MANAGED_RECOVERING`。若該次 ensure 才發現外部 CLI 建立的 hold，可短暫顯示恢復中，
隨後穩定為停止並清除恢復錯誤，不重試、不增加 backoff。內部 CLI `serve` 也拒絕既有 hold。
不可讀、symlink、非目前使用者所有或未知版本的 hold 回傳 `SERVICE_LIFECYCLE_UNPROVEN`，
不會退回建立服務。

若 stop hold 仍存在，要啟動服務必須明確選擇「啟動背景服務」或 CLI `resume`，
由 Python 在 `launcher.lock` 內處理。
舊服務仍持有 lease 時回傳 `SERVICE_STILL_STOPPING` 並保留 hold；lease 釋放後才刪除並
同步 hold，在同一把 lock 內執行一次普通 ensure。Both 角色的外部連線位址、憑證與選擇
維持原狀。此檔案只屬 runtime root，不新增 SQL DDL 或 journal data step。
