# Managed desktop installation and background service

Implementation: **included in this source version, introduced in [PR #81](https://github.com/TED-H-com/bat-agent-connector/pull/81)** (2026-10-10). Match package evidence to the exact source commit and consult the linked checks for validation records. Older published release assets are historical client packages and do not automatically contain this runtime.

This contract covers the ordinary local installation and its advanced external-central alternative. Python Connector / Task Service remains the sole business authority. Windows Fleet manages local connection and startup choices; BAT executes work on the selected hosts. Rust owns packaging, native credentials, windows and restricted lifecycle controls, not another task journal or scheduler.

## Implemented first-use journey

1. Install a matching Windows, Mac or Linux candidate package. It includes the matching Python runtime; the user does not first install Python, uv or developer tools.
2. Launch the application. It verifies the bundled artifact, prepares private local data, one owned Python central and a personal identity, then connects the shared Dashboard. No endpoint, actor or copied API token is required for this local path.
3. The first screen uses the OS/browser language: `zh-TW`, `zh-Hant`, `zh-HK` and `zh-MO` (including subtags) select Traditional Chinese; other locales select English. **English / 繁體中文** is visible before entering anything and in Settings. The explicit choice persists in `batc.lang`, overrides later OS changes, and controls the setup-guide language.
4. Choose **Set up automatically / 自動設定**. One usable saved BAT remote connection is reused through the existing central profile import. Several usable connections require a plain-language choice. When none is available, paste a masked connection link copied from BAT's trusted Settings screen. Setup never guesses credentials, discovers trust pins or reads the clipboard automatically. Encrypted/damaged credentials remain unavailable. Central still probes the pinned host before publishing setup. Automatic setup uses connection-only defaults: writes, orchestration and shared clones stay off, even if an older advanced draft enabled them. No SSH, directories, model choices or paid API keys are required.
5. Expand **Advanced / 進階設定** only when needed for managed permissions, dedicated roots, SSH, exact repository bindings or verification commands. Existing validation and opt-in permissions apply. Repository setup does not push or create/merge PRs; saving verification commands does not execute them.
6. Open Web through **Open Dashboard in browser** or the tray/menu-bar entry. The private one-use handoff signs into the same identity; the bearer never enters command arguments, URLs, browser storage or logs.
7. Choose **Start when I sign in** for installation-owned autostart. Closing the browser or Dashboard does not stop central work. Fleet retains its own connection/startup ownership rules.

Existing external-central configuration stays selected. **Advanced: join an existing central** requires explicit native confirmation and separate credential enrollment. It preserves the existing owned background service and never forwards its credential to the new address. Failure to reach an external service cannot provision a second database.

## Setup connection readout

Setup previously read `daemon.fleet._clients[name].connected`, the write-capable pool's open-socket flag. Session observation uses a separate read-only inventory pool, whose scheduled scans deliberately close idle sockets. Setup therefore reported disconnected hosts even after successful session scans.

Setup now uses an authenticated active write connection or existing inventory evidence matching the current endpoint, certificate pin and profile binding within the inventory freshness window. Current success is **Connected / 已連線**. A recent failure is **Can’t reach this connection / 目前無法連線**, with one suggestion to check BAT and refresh. Missing, expired or mismatched evidence is **Checking… / 確認連線中…**, suggesting a refresh shortly. Legacy `connected: false` alone also means checking. Refresh reads observation evidence; it does not launch probes or change observer backoff.

`GET /managed/setup` adds per-host `connection_status` and `connection_checked_at`. `connected` is `null` for unknown reachability. No schema, migration, permission or token-handling changes are introduced. Status is a recent observation, not a continuous network guarantee. Configured installs retain IDs, history, non-secret drafts and original operation keys and never automatically repeat setup.

A succeeded `setup.host` receipt is evidence of that operation's verification and saved configuration at its `updated_at` completion time. When its target host's current row is checking, unreachable or absent, Setup shows that past evidence and time instead of the ready message. Each host row supplies its own current state, observation time when available, and next step. The existing ready copy remains when the receipt's target host is currently connected; without a host receipt, every displayed host must be connected to show it. An unrelated connected host cannot override the configured host's state. This readout makes no additional claim about starting Work and never resubmits setup.

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

`runtime_difference` is `same`, `differs` or `unknown`; either missing commit
makes it unknown. `differs` means the running service is not that bundle and the
change waits for the service's next launch. These facts do not trigger replacement,
restart, stop or refusal. Same-version reuse and the existing version upgrade gates
are unchanged; readiness is not a build comparison. Native launch/status fields are
additive and optional, and recovery refreshes the recorded observations. Same-version
restart/refusal policy and each platform's ensure/stop ownership are owner decisions
that have not been made.

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
A separate refusal record identifies its digest, endpoint and refusal status.
It is kept in memory and in the same store as the token, preferring local storage
when the token is remembered there; session-only tokens leave no durable refusal
record. The pause survives reloads, and remembered tokens stay paused in new
tabs. The person can Connect with a new token or choose Retry remembered token.
Disconnect, successful replacement with a different token and explicit retry
remove the corresponding refusal record. Work drafts, pending intents and
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
endpoints and different identities are left in place. These cases require explicit
review/export of the retained data; there is no automatic picker or recovery UI in
this change. Reverting to the old frontend can still read original old-port keys
when connected at that original endpoint, but later edits in the new scope are not
copied back automatically.

`batc.sync.<scope>` stores only `{cursor, token}`, not a last-used time or a
namespace generation. The cursor orders journal events, not local draft edits:
several restarts can save the same cursor with different drafts. A restored
journal also retains its `server_id` while its cursor regresses; the B02 backend
test covers both regression and divergent reuse of a sequence. Thus even a
unique highest saved cursor does not prove the latest namespace. This migration
deliberately leaves all such sources untouched. A user with several old scopes
gets an empty new scope; their old drafts remain available for explicit local
inspection/export. Recovery requires reviewing the matching identity and choosing
the intended complete source, including its original operation keys and pending
requests, after backing up both old and new data. This change supplies no tool
for that selection or import; copying draft text alone must not replay a pending
request. Do not clear storage or guess a source from its port or cursor.

The namespace audit covers these durable families uniformly (names below omit
`batc.`); memory-only consumers use the same verified namespace:

| Stored state | Storage family / scope |
| --- | --- |
| Drafts, original operation IDs/keys and sync proof | `draft`, `key`, `sync` |
| PM forms, intents, handoffs, exact Work/Attempt navigation | `pm.<scope>.*`, nested `pm.*-selection`, `pm.*-navigation`, `pm.governance.selection`, `pm.verifier` |
| Setup, create, repair, capture and work controls | `managed.setup`, `create`, `repair`, `capture`, `task-control`, `start`, `orchestrate`, `published`, `dispatch` |
| Resource forms, approvals and cleanup | `labels`, `session-bat`, `permissions`, `artifact-review`, `model-preferences`, `project-skills`, `approvals`, `cleanup.draft`, `cleanup.pending`, `cleanup.intent` |
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
