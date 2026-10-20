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

## 與「加入機器」共用的契約（待審查）

本節補充上方現行首次設定，未實作新的角色精靈。程式查核基線為 `ebee8196`
（含 #323 的 `2df805ed`），下列 `file:line` 已按此基線重核。
責任分工只維護於 [Fleet 唯一責任表](fleet-configuration.md#責任表與退休條件)；
共用欄位、狀態、變更、lifecycle、健康投影、驗收與 D1–D8 的產品問題／技術預設
均引用 [Fleet 共用契約](fleet-configuration.md#加入機器共用契約待審查)，本處不另訂一份。

| 首次設定特有行為（現況） | 可核對的輸入／輸出 |
| --- | --- |
| UI 選 BAT 來源 | 自動模式沿用可讀 BAT profile，多項時選擇，無可用項目時貼遮蔽連結；parser 拆出 endpoint／pin／token。[managed-setup.js:9](../../desktop/src/managed-setup.js#L9)、[managed-setup.js:318](../../desktop/src/managed-setup.js#L318) |
| 自動設定送出值 | writes／orchestrate／shared_clone_worktrees 為 false，managed_roots 為空；advanced 才送 SSH／管理選擇。[managed-setup.js:465](../../desktop/src/managed-setup.js#L465) |
| UI 恢復原請求及接受收據 | 已知 operation ID 時用 GET 查原操作，否則帶原 request／key 送出；接受收據前核對 operation ID、actor、action、target、params、preconditions 及 key。[managed-setup.js:426](../../desktop/src/managed-setup.js#L426)、[managed-setup.js:441](../../desktop/src/managed-setup.js#L441) |

角色啟動時機依共用契約 D1 決定；這會修訂上方 managed-first 旅程的未來入口，
不表示現有首次設定已可免建本機服務。後端對帳與新 setup 阻擋規則只引用
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

- The persistent installation manifest binds runtime version, data location, installation actor, server ID and principal ID. Reopening and concurrent clients recover that installation.
- Central uses kernel file leases, not an occupied port, stale PID or matching path, to enforce a single owner. The endpoint pointer is checked against the owned database, then a challenge verifies the live service before any bearer is sent.
- Windows uses private ACLs, verified handles, reparse rejection and native locking. POSIX uses private owned directories/files and process locks. These protections also cover journal, token and atomic configuration publication.
- The package pins its compressed runtime and executable digests. Installation does not download and execute an unversioned script. The frozen runtime starts without Python or uv on the user's PATH.
- Browser handoff is short-lived and single-use. The resulting session uses an HttpOnly SameSite cookie plus origin/CSRF checks; this is not a public unauthenticated hosting mode.
- Setup mutations use central durable actions, opaque staged credential references and the reviewed configuration revision. Publication is atomic. Recovery reads the original revision/receipt rather than inventing a new intent; live configuration changes wait for a safe point.

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
remain eligible for native recovery; an explicit Disconnect disables it. It retains existing drafts,
event checkpoints and operation keys, and never resends an application mutation.
Disconnect, credential removal or switching configuration cancels a pending rebind.
External-central connections never use this recovery loop, provision a local
service or transfer a local credential to another endpoint. Recovery errors appear
as short English/zh-TW status labels and visible recovery explanations, with a
fixed diagnostic code in connection details;
a changed identity stops automatic recovery until the connection is reviewed.

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

The owner explicitly excluded formal signing, Mac notarization, production update channels and Linux persistent native credential enrollment from this delivery. Full human execution of the 46-item real-environment acceptance matrix is user-owned and is not an outstanding agent task. Exclusion does not mean these capabilities or checks were completed. Unknown native BAT merge acknowledgements preserve original work under the [merge contract](worktree-merge.md); its separate human-adjudication API is not a delivery gate.

See [first use](../getting-started.md), [implementation status](../product/implementation-status.md), [acceptance reference](../product/acceptance-v2.md) and [shared frontend lifecycle](shared-frontend.md).
