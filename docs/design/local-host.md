# Local BAT without SSH

Status: **proposal, no product changes**. Issue #141; source baseline
`f5dfa7ad4e52c7d578a48ea45a9f94a7e06c6eae` (2026-10-14 investigation).
File/line references below describe that baseline, not an installed BAT version.
Python Connector / Task Service remains the only central authority.

## Problem and recommendation

A person using BAT on the same computer currently has to supply an endpoint,
trusted certificate fingerprint and token. Local BAT profiles do not supply an
importable connection. Checkpoint continuation and other managed Git operations
require an SSH alias even when the central and BAT share a filesystem.

Add an explicit per-host Git transport, initially for a verified POSIX execution
environment, and a separate local connection quick start. Never select local Git
because an endpoint is loopback: a tunnel, container or VM can present the same
address. The user must assert that BAT runs on the central's computer and uses
the same filesystem namespace. The desktop quick start can combine that assertion
with its single certificate trust decision.

The connection flow is one screen, with one certificate decision and, when
necessary, one **native** token paste field. Automatic server-token discovery is
conditional on a documented BAT contract. Do not remove the local-profile filter
and assume remote fields or remote-client credentials describe a local server.
Windows local Git remains explicitly unavailable until there is a separate
reviewed implementation. Connecting and observing BAT need no Git runner.

This proposal preserves the [shared product decisions](../product/realignment-v2.md),
[PM plan](../product/pm-v1.2-plan.md), [PM status](../product/pm-v1.2-status.md),
[implementation status](../product/implementation-status.md),
[managed installation](managed-installation.md), [checkpoints](checkpoints.md),
[resource policy](resource-policy.md) and [confinement](confinement.md).
It does not reduce the PM lifecycle backlog or add another task engine.

## Verified connection facts

Source paths in this section are relative to the repository. BAT source is not
included here and was not independently inspected in this investigation.
Protocol notes are versioned secondary evidence; Connector code and synthetic
tests establish what this client accepts, not BAT's current persistence format.

| Question | Evidence and limit |
| --- | --- |
| Server mode | `docs/PROTOCOL.md:3–9,16` describes BAT v3.2.12's Rust remote server and standalone headless `bat-server` bundle. The Connector uses that remote WebSocket protocol even for a same-computer connection; it has no in-process/local IPC BAT adapter. A local desktop profile alone does not establish that a remote listener is enabled. The current desktop setting/CLI flag to enable it needs BAT confirmation. |
| Port and URL | Protocol notes name default port **9876**. `config.py:606–613` requires a configured `wss://` URL; guided setup additionally requires an explicit port and a bare origin (`managed_setup.py:104–114`). There is no client-side assumption that every running BAT listens on 9876. SSH port forwarding can use another loopback port. |
| Certificate | Protocol notes describe a self-signed certificate. `client.py:176–181,212–218` disables CA/hostname validation, reads the peer DER certificate and compares its SHA-256 fingerprint before sending authentication. `config.py:87–94` normalizes 64 hex digits, optional colons and `SHA256:`. No certificate file path or private-key format is established by this code. |
| Token | `client.py:190–235` resolves a token in central memory and sends it in the first `auth` frame, advertising only `bat-remote/v2` and no compression. It does not use an HTTP Authorization header for BAT. `docs/PROTOCOL.md:37` describes comparison with the server's current token. BAT's server-token generation, storage and rotation are not established here. |
| Profile identity | A connection's `profile_id` names the workspace profile reported by that server. It differs from the client profile ID in `bat-profile:ID` (`managed_setup.py:154–161,269–274`). Headless `default` is documented in `docs/PROTOCOL.md:71–72`; guided setup verifies the actual profile list instead of assuming it (`managed_setup.py:388–401`). |
| Existing manual route | `docs/getting-started.md:26–32` and its zh-TW counterpart require trusted endpoint/fingerprint/token, managed permissions/roots and an existing SSH alias where needed. Setup probes with writes/orchestration disabled. Pin/profile failure prevents publication (`tests/test_managed_setup.py:91–104`). |

`tests/test_client.py:67,159,167,273` covers v2 authentication, rejected token
redaction, pin mismatch before auth and token absence from subsequent frames.
`tests/test_managed_setup.py:97` explicitly asserts zero auth frames on a bad pin.
These are mock tests, not desktop/server-mode or OS credential acceptance.

### Local files this repository actually understands

| Location or reference | Current interpretation |
| --- | --- |
| Linux profiles | `config.py:62` defaults to `~/.local/share/org.tonyq.better-agent-terminal/profiles`. It is a literal default, not an XDG-aware BAT discovery algorithm. Guided setup uses it on Linux (`managed_setup.py:93–101`). |
| macOS profiles | Guided setup tries `~/Library/Application Support/org.tonyq.better-agent-terminal/profiles` (`managed_setup.py:99–100`). This is a Connector lookup convention, not verified current BAT storage evidence. |
| Windows profiles | Guided setup tries `%APPDATA%\org.tonyq.better-agent-terminal\profiles` (`managed_setup.py:97–98`). Missing APPDATA must not turn a future discovery attempt into a relative-directory search. This is also a lookup convention, not proof of BAT's installed layout. |
| Overrides | `[bat] profiles_dir` overrides guided discovery; a host's `bat_profiles_dir` can override token resolution (`managed_setup.py:93–96`; `config.py:598–599,683`). Outside guided setup, the general config/importer default remains the Linux-style path on every OS. |
| `index.json` | Guided import accepts only `type: remote` with `remoteHost`, `remotePort`, `remoteFingerprint`, and optional `remoteProfileId` (`managed_setup.py:123–165`). CLI import has the same filter (`importer.py:32–55`). It knows a remote endpoint from these fields. There is no supported mapping from a local profile to a local server endpoint. |
| `token_ref = "env:NAME"` | Resolve the selected environment value in central, strip it and reject empty values (`config.py:141–150`). This existing operator option is not a desktop discovery source. |
| `token_ref = "file:PATH"` | Read a private UTF-8 token file, at most 64 KiB, strip surrounding whitespace and reject empty values (`config.py:151–159`). This is a Connector reference, not a claim that BAT writes such a file. |
| `token_ref = "bat-profile:ID"` | Read `remote-tokens.enc.json` under the selected profiles directory, at most 1 MiB (`config.py:165–192`). The supported unencrypted shape is a JSON object whose `data` is an object or JSON string containing a `tokens` object, selecting exactly that client profile ID. Encrypted data is refused. This does not establish where the local BAT **server** token lives. |
| Private-file protection | `_private_files.py:8–51` rejects symlinks, nonregular files, excessive sizes, owner mismatch where `getuid` exists, and group/other access on non-Windows; checks opened-file identity too. Unix mode checks are skipped on Windows. Managed storage instead uses `platform_files` and Windows ACL/handle checks; do not call the older helper equivalent to Windows OS protection. `docs/getting-started.md:230–234` documents these distinctions. |

There is a small existing reader discrepancy: token resolution accepts absent
`enc` as unencrypted (`config.py:173`), while guided discovery requires exactly
`enc: false` (`managed_setup.py:139–140`). A new native importer should require an
explicit supported unencrypted marker; do not silently expand formats. Any change
to the existing reader's compatibility is a separately reviewed change.

`tests/test_mcp_and_config.py:71–101` supplies both local and remote profile
fixtures, imports only the remote one, keeps the token out of TOML and resolves
its reference. Lines 104–114 exercise damaged stores.
`tests/test_managed_setup.py:328–355,388` covers encryption, no credentials in
setup state, damaged stores and private permissions. None verifies an actual
BAT local server token or all three OS installation layouts.

Native capabilities already exist, but do not imply BAT decryption support:

- `desktop/src-tauri/src/credentials.rs:98–120` defines native vault reads,
  writes and secure prompts. Windows Credential Manager/macOS Keychain store
  **Connector** credentials; Linux's vault implementation reports unsupported.
- `desktop/fleet-core/src/credential.rs:90–113` reads reviewed unencrypted BAT
  remote-profile credentials. [Fleet credentials](fleet-credentials.md) also
  supports the Kit's Windows DPAPI export format. That is not a general reader
  for encrypted BAT token stores or BAT server secrets.
- Today's BAT setup password input lives in page memory until staging
  (`desktop/src/managed-setup.js:81,202–206`). `managed_setup.py:200–252` stages
  it privately and returns an opaque `secret_ref`. The proposed local flow must
  add native enrollment rather than claiming this existing DOM field satisfies
  “secrets never reach the page.”

### Questions for BAT's maintainers

Implementation must pin a BAT source revision or obtain a documented maintainer
contract for these facts. Do not guess filenames, encryption envelopes, keychain
service/account names, token encodings or local-profile fields.

1. How does each supported desktop/headless version enable its remote listener?
   What are the default port, bind addresses, disabled state and supported
   read-only way to discover the actual endpoint? Is there a local API or
   metadata file with an explicit version/schema?
2. Does any local profile describe that listener? How are multiple windows,
   profiles and simultaneous headless/desktop servers distinguished?
3. Where are the actual **server** token and certificate persisted on Linux,
   macOS and Windows, including portable/custom-data installations? Are they
   different from `remote-tokens.enc.json`'s client credentials?
4. Is a supported token export/read API available? What OS principal, application
   entitlement or user interaction does protected-token access require? Can
   another application legitimately read it without weakening BAT's protection?
5. What happens when a token or certificate rotates, and when BAT restarts?
   How can the user compare a server fingerprint in BAT's own trusted interface?
6. Where exactly can the user copy the remote-server token in each supported
   version? Supply the English/zh-TW screen names or headless operator procedure.
7. Can authenticated read-only capability/profile/workspace checks identify the
   intended local server and filesystem namespace? Are there trustworthy
   incarnation/installation identifiers? A cwd string alone cannot prove this.
8. Does the protocol permit a bounded TLS-only inspection without auth, and what
   operational limits should discovery observe? The client can read peer DER
   during TLS, but that does not prove the listener is BAT.

## Discovery and one-screen quick start

### Authority and bounds

“This computer” means **the Python central's computer**, not the browser's.
Enable this quick start only for a verified owned managed-local installation.
An external-central client must not probe its own loopback and then configure
the remote central to execute Git against that address. A standalone operator
may explicitly configure local transport on the central machine.

Central performs discovery and validates candidates. Native code handles
credential access/enrollment and transports only to the already verified owned
central; it performs no Git, session scheduling or operation reconciliation.
The page receives candidate IDs and nonsecret status, never BAT credential bytes.
No general filesystem, URL-fetch or shell IPC is introduced.

At present a local endpoint is **not known from a supported file**. Start with
the documented default as a bounded candidate: TLS-only probes of literal
`127.0.0.1:9876` and `[::1]:9876`, no DNS, proxy, redirects, port-range scan,
LAN discovery or bearer. Proposed budgets: at most two simultaneous sockets,
three seconds each, ten seconds for the complete discovery attempt, bounded
certificate/response sizes and cancellation cleanup. Do not call an arbitrary
TLS listener “BAT found”; say a local encrypted service was found until pinned
authentication and read-only BAT verification succeed.

A later BAT-documented metadata adapter can supply other ports. Validate a
versioned, bounded, no-follow file from a selected supported data directory,
and resolve only literal loopback addresses for this quick start. Metadata is
a hint, not execution ownership or automatic trust. Do not parse opaque server
state or reuse remote-profile fields for `type: local`.

A TLS handshake exposes the peer certificate in DER; central computes its
SHA-256 without sending a token, using the same digest as `client.py:72`.
First acceptance is **trust on first use (TOFU)**: the observed certificate is
not authenticated by that observation. A hostile local listener/tunnel can
win discovery. Show the fingerprint and one brief trust statement, with how to
compare it in BAT when BAT supplies that procedure. Persist the exact accepted
pin; reconnect performs the existing strict comparison. No silent repinning.

Candidate metadata is short-lived and bound to the installation, principal,
configuration revision, literal endpoint and fingerprint. Changing identity,
endpoint, discovery generation or trust invalidates an unsubmitted candidate.
After acceptance, freeze these values in the operation envelope. If the
certificate changes before authentication, fail before sending the token.

### Screen and fallback

Use one native enrollment sheet, opened from the shared Local BAT entry. It
contains the nonsecret connection/trust details and the secure token field in
the same surface; do not stack an HTML connection form and a separate credential
wizard. The page receives only nonsecret sheet state and the completed enrollment
reference. Reuse the shared localized copy/design vocabulary in this narrow
credential surface; native owns no independent host configuration or task UI.
Use one primary action. The normal path has no host-ID, URL, fingerprint-edit,
profile-ID or SSH form.
The displayed fingerprint is observed data, not user input. Technical evidence
and capabilities belong in Details, with the trust notice visible beside the
action. The stable logical host ID is allocated once by central with collision
checking; a localized display name does not become its identifier.

1. Open **Local BAT / 本機 BAT**. The screen states **BAT and this service run
   on this computer / BAT 與中央服務都在這台電腦執行**. It discovers a candidate
   without sending any credential. Opening the screen does not save local mode.
2. With one unambiguous candidate, show the endpoint and fingerprint plus
   **Trust this certificate? / 信任這張憑證？**. **Trust and connect / 信任並連線**
   confirms the exact pin and the visible same-computer assertion in one
   submission. For an already explicitly configured matching pin, use
   **Connect / 連線**, with no repeat trust decision.
3. Native enrollment obtains the appropriate token only through a supported
   BAT contract. If unavailable, use one secure **native** paste field:
   **BAT connection token / BAT 連線金鑰**. It is the credential-entry surface
   in the same enrollment sheet, not an additional dialog or an HTML password
   field. The page sees only **Token added / 已加入金鑰** or unavailable.
   The dialog's submit is data entry, not another permission/trust prompt.
4. Central runs the existing read-only host probe, verifies the exact server
   profile/workspaces, atomically publishes setup and returns its durable result.
   Show **Connected / 已連線** only with that central evidence. This does not
   enable writes/orchestration, create a workspace or prove managed Git ready.

The fallback instruction is **Copy the remote connection token from BAT's
remote-server settings / 從 BAT 的遠端伺服器設定複製連線金鑰**. The exact navigation
must be supplied and tested against the supported BAT release before shipping;
this investigation cannot establish it. For headless BAT, the operator must
use BAT's documented token output/configuration procedure. Never suggest
decrypting an unknown file, extracting a private key or creating a new token.

If discovering one endpoint, identifying its server profile or obtaining a token
would require another ambiguous choice, stop the quick start honestly:
**Local BAT needs setup / 本機 BAT 需要設定**, with a link to the existing advanced
connection flow. No running listener gets **BAT not found / 未找到 BAT** plus the
verified enable-listener instructions. Nondefault ports without metadata use
advanced setup. This keeps the simple path to at most one trust decision rather
than auto-selecting among unrelated servers.

Native protected-store access that would trigger a separate OS authorization
prompt is an advanced path; the minimal path falls back to paste. Linux needs
a reviewed native secure field/enrollment implementation: its current
unsupported vault must not trigger a DOM-secret fallback. Browser-only setup
can consume the configured host, or direct the user to desktop enrollment;
it cannot read another process's certificate or BAT's OS credentials itself.
There must be no misleading “automatic token import” on an unsupported platform.

Imported or pasted tokens stay in native memory, are registered for redaction,
and are submitted through a narrow fixed staging request to the verified local
central, using its existing private-file/opaque-reference mechanism. The central
must resolve the token for BAT auth, so this is a deliberate credential handoff,
not a second native BAT connection. No token enters page IPC, JS memory,
localStorage, URLs, command arguments, logs, events or operation bodies. Central
private files retain their existing platform protections; do not imply every
persisted BAT token is in an OS vault. No new Linux vault persistence is assumed.
Bind the reference to kind/principal/installation/candidate; retain linked
references through accepted-operation recovery and never reread another token
after an uncertain outcome.

All proposed labels and errors need English and natural Taiwan Traditional
Chinese in the existing `*-strings.js` pattern. Preserve nonsecret drafts,
principal/server namespaces and original operation keys. A withdrawn setup source
(403/404/scope loss) clears its candidates, counts and details together; a
generation guard rejects late discovery/auth responses. Trust is not implied by
a green count or a partial list. DOM construction stays CSP-safe through `h()`
or `createElementNS`; candidate/provider text is always text.

## Git runner inventory

All module references in this section are under `src/bat_agent_connector/`.
`SshGitRunner` is in **`checkpoints.py:61–79`**, not `git_worktree.py`.
Its interface is `available(host: str) -> bool`, currently `bool(alias)`;
`run(host, script, timeout_s)` executes an entire script, not a Git argv/cwd API.
The following table accounts for every direct runner invocation and its callers.

| Call site | What executes | Writes and paths touched |
| --- | --- | --- |
| `checkpoints.py:206` (`source_state`) | `source_state_script`, lines 191–197: no-optional-locks HEAD and porcelain status; used by preview/create/live source reads. | Source checkout is read-only, including its index. Failure is unknown dirt, never zero. Repository-configured programs need the local safety audit below. |
| `checkpoints.py:700` (`_run_continue`) | `prepare_script`, lines 415–459: clone/fetch exact commit, create branch/worktree, report HEAD/dirt. | Reads the source repository; writes only a proven managed clone, operation temp clone, worktree, refs/config and sibling `.batc-lock` beneath the managed root. Removes only its own temp during safe preparation. Source origin becomes a sanitized network URL or is removed. |
| `integration.py:309,311` (`Area.run`) | Preparation (352,462), analysis (381), composition (479), checks (525), push (548), readback (570), repair (588), inspection (610), resolution (622). Callers at 821,856,1084,1108,1142,1165,1225,1258,1261,1346,1617. | Area is the policy-derived `<managed root>/.batc-integration/<binding>/repo.git`; repair worktrees are inside its owned area. Preview is not filesystem-read-only: it can init/fetch/pin refs, write simulated objects and dry-run push. Apply writes composed objects/refs/temp checks and can push the fixed bound remote branch. Readback can fetch remote objects. Source checkouts are only read. Preserve final lineage checks and sent-push reconciliation. |
| `cleanup.py:730` (`_host_call`) | Injected `cleanup_host.py` with bounded request: observation/retained proof, lock.session, preserve, discard, remove.worktree, remove.branch, remove.temporary. | Observation reads proven resources; mutating phases write retained objects/refs, discard reviewed content and remove exact owned files/worktrees/branches/temp resources. Paths/manifest/identity and consumer checks, inherited inode flock and locked proceed exchange precede mutation (`cleanup_host.py:346–456`). It is not general `rm -rf` permission. |
| `pm_worktree.py:138` (`_host_call`) | Combines repository-sync, PM carrier and revision helpers (109–115). Fetch/prepare/read carrier, immutable snapshot/read snapshot, PM revision/read revision. | Writes only PM-owned clone/worktree/metadata; drafts under `docs/pm/drafts/pmr_…`, plan revisions under `docs/pm/revisions/pmrev_…`, and supported immutable spec paths from `pm_revision_paths.py`. Own Git object/ref/index updates for deterministic PM-only commits. Reads original pin/manifest for recovery. `pm_revision_git.py:68–91,115` reaches this same runner indirectly; there is no extra transport. |
| `repository_sync.py:277` (`host_call`) | Injected `repository_sync_host.py`: fetch, prepare, read.fetch, read.carrier. | Creates operation-owned clone/temp/ref/worktree beneath the bound managed root; fetches an explicitly published SHA from the configured remote. Reads back original marker/pin/carrier. Never names a manual source checkout; no push or cross-host unpublished Git transport (`repository_sync_host.py:101–177`). |
| `pr_publication_host.py:105` (`run`) | prepare/push/readback at 24,52,89; invoked by `pr_publication.py:439,467,475`. Reuses integration area and mutex. | Prepare writes fetched objects and candidate/base pins in the owned area. Push creates the generated remote branch only with the exact absent-ref lease and stores an exclusive `repo.git/batc-pr-create-<operation>` receipt. Readback reads that receipt and remote ref. A matching remote SHA without the own receipt cannot prove this operation created it. |
| `pm_dependency_sources.py:81` (`collect`) | Fixed Python helper combining repository identity, pinned-object reader and dependency paths; 60-second limit. | Reads manifest/lockfile blobs at the original bound commit in the PM clone, with directory locks and bounded tree/blob reads. No fetch, index/worktree write, package installation or provider code. `pm_dependencies_host.py`/`pm_spec_sources_host.py` supply this reader. |
| `pm_spec_sources.py:151` (`read_sources`) | Fixed pinned OpenSpec tree/blob read; 60-second limit. | Reads only the original bound PM clone/commit's supported `openspec/` objects. No fetch, checkout, CLI or index write (`pm_spec_sources_host.py:22–33,64`). Post-read membership/binding guard discards withdrawn bytes. |
| `worktree_merge_operations.py:168` (`proof`) | Fixed `worktree_merge_host.py`; 25-second limit. | Reads source/destination canonical roots, common Git dir, refs, registrations, clean state and ancestry under managed roots. Rejects config programs/submodules; disables optional locks. **Actual merge is a separate BAT effect**, with its existing final-frame/ownership/reconciliation guards. Reader identity is bound at lines 53–56. |
| `confinement.py:299` (`check_account`) | `run_account_check` with the explicit trusted auditor SSH alias; fixed Linux metadata/ACL/channel closure script at 426–441. | Reads protected-root metadata, process/account/channel and trusted executable evidence; not checkout contents or mutation. Central persists check evidence. It must not become a same-user local self-attestation. |

Availability consumers are `checkpoints.py:203,382`, `integration.py:86,1508`,
`cleanup.py:710`, `repository_sync.py:86`, `worktree_merge_operations.py:50` and
`api_v1.py:1270–1273`. PM reads reach the same prerequisites through carrier
binding. Installation wiring is `task_daemon.py:245–258`, setup activation is
`managed_setup.py:464–501`, and the confinement fallback constructor is
`confinement.py:294–301`. Change all three consistently, including both fleets.
Update transport-specific reasons and `requires_verifier_ssh` in the merge
capability (`worktree_merge_operations.py:59–63`) when local proof is supported.

### Adjacent transports are separate

`ArtifactHost` is SSH-only (`artifact_host.py:16–32,88–90`) and has its own
binary/helper protocol. Local Git does not enable attachment materialization or
managed artifact capture. Preserve ordinary upload/download; report host transfer
unavailable before dispatch with attachments. A later local byte adapter must
reuse its manifests, input verification, confinement and durable receipts.

`ObservedVerifier` already runs argv locally when it has no SSH alias
(`task_verifier.py:169–179`); other verifier paths also construct SSH commands.
It does **not** use `git_runner`. Audit its host/binding choice as a separate
follow-up before claiming complete local Task Service verification. Do not make
Git mode select an arbitrary local verification cwd, silently remove an alias,
or imply checkpoints alone complete all local managed-work workflows.

## Explicit host setting and composite dispatch

Add `HostConfig.git`, with the strict enum `"ssh" | "local"`. Omission means
`"ssh"`, preserving every existing host, even loopback hosts without an alias.
An operator's explicit TOML declaration is their same-computer assertion:

```toml
[hosts.local-bat]
# Existing URL, accepted fingerprint and token_ref fields still required.
git = "local"
```

Do not introduce a second alias authority. Today guided `ssh_alias` is saved in
`[managed_setup] ssh_hosts`, overlaid on verifier `ssh_hosts`
(`managed_setup.py:296–300,435–442,469–475`). Keep that representation. With
`git = "ssh"`, use the same mapping; with `git = "local"`, retain any existing
mapping but do not use it for Git or delete it to force other services local.
The trusted confinement auditor mapping remains independent.

`HostGitRunner` composes the existing SSH runner and a new POSIX local runner.
There is no URL-based fallback, no retry on another transport, and no caller
selection of executable, environment or shell command. Dispatch follows the
validated configured host identity and its fixed mode on every internal call.

| Method | Proposed behavior |
| --- | --- |
| `available(host)` | Preserve bool signature and accept the logical host name. Unknown/unconfigured host is false. SSH preserves its existing alias check. Local is true only after explicit configuration and bounded prerequisite checks for the platform, shell/Git/Python helpers and required lock semantics. Read a configuration-bound readiness result here; do not launch synchronous probes on each capability read. Startup/setup performs bounded preflight off the event loop, and `run` revalidates before launch. Availability is transport readiness, not ownership, repo access or sandbox proof. Expose fixed reasons in a separate status projection; never reinterpret missing alias as local. |
| `run(host, script, timeout_s=None)` | SSH delegates unchanged. Local executes only central-generated scripts with `asyncio.create_subprocess_exec` and absolute validated executables, no interpolation of a caller command. Preserve return-string, async cancellation and exception contracts, concurrent bounded pipe draining and `_LOCKED_CHECK` exchange. The runner is internal transport; action/resource/script guards authorize actual paths and writes. |
| `run_account_check(host, command, timeout_s=None, *, ssh_alias)` | Always use the explicitly configured, validated trusted auditor SSH channel, even for local Git. Never dispatch this command to the current user's shell. No alias/trusted channel means unavailable/unknown account evidence. Local BAT without an auditor keeps its existing prompt-gated/options-confirmed limitations; it is not upgraded to `host_account` verified. A non-SSH auditor needs its own future design. |

Freeze execution location in admission/step bindings before any new effect:
transport kind, configured host/profile identity, installation/server identity
and relevant config digest. Recheck it at final frames and locked proceed gates.
No mode change can redirect recovery of an old intent to a different machine.
Setup's busy gate blocks active changes; standalone config reload must also fail
closed for a changed binding. Completed receipts remain readable without rerunning
effects. Old unresolved SSH operations stay on SSH; no automatic local migration.

`worktree_merge_operations._reader_binding` currently hashes runner class and
alias. A composite would change that digest for every SSH host. Introduce an
explicit per-host reader binding which reproduces the old SSH binding exactly;
new local proofs include transport/installation identity. Preserve old durable
readers rather than accidentally invalidating or rebinding every merge intent.
Do not fake a local SSH alias or rely on the composite class name as identity.

### POSIX compatibility is not confinement

The existing test runner executes scripts locally via `sh -c`
(`tests/test_checkpoints.py:42–53`). That proves script feasibility on its test
OS, not production isolation. `sh -lc` can run the syntax, but the login shell
can execute startup files and inherit dangerous environment/configuration.
Prefer a non-login `sh -c` with a controlled executable search path/environment
for local execution; do not promise unchanged `sh -lc` has the same guarantees.
No repository-supplied script or user-profile startup file should execute merely
because a checkpoint is read.

Retain the existing canonical-path, no-follow, marker/creation-intent checks,
inode locks, managed-root checks and controller/writer/consumer guards. A path
under a managed root is a necessary destination check, never ownership proof.
The manual source remains a separately permitted **read**; a blanket managed-root
cwd check would incorrectly reject safe checkpoint capture. Do not parse shell
strings heuristically to decide whether a command writes.

Before enabling local mutation, audit shared checkpoint scripts for symlink/temp
replacement, config hooks/filters/fsmonitor, Git environment overrides, lazy
fetch and source mutation. `source_state_script` currently supplies optional-lock
suppression but no full config-program isolation. `prepare_script` uses inherited
Git config and makes `flock` optional (`checkpoints.py:429,445`). Local support
must harden or refuse unsafe cases, including an existing unproven temp/clone.
No source hook/filter/helper may execute, and no lockless fallback is accepted.
Use a durable cross-process inode lock so restart/readback cannot race a surviving
child; an in-process mutex alone is insufficient. Review these prerequisites
before advertising local checkpoints, even though this design commit changes no
script.

Linux is the first acceptance target. macOS has POSIX shell/Python `fcntl`, but
external `flock`, Git features, directory locking and utility flags cannot be
assumed. The current integration helper needs Git's merge-tree features; shell
scripts use `sed`, `awk`, `grep`, `find`, `wc`, `tr` and filesystem utilities.
Check the actual helper closure on each OS; do not install tools automatically.
The managed package's bundled runtime is not evidence that a `python3 -c`
executable exists on PATH. Initially report the exact missing helper; packaged
support can later provide a pinned private helper entry point without requiring
the page to choose Python or exposing arbitrary native execution.

Windows has incompatible POSIX quoting/path semantics, no native `sh` contract,
no Python `fcntl.flock`, different directory/ACL/reparse behavior, and no POSIX
`/dev/null` or standard utility closure. Git Bash/WSL availability is not proof
BAT shares its filesystem. Do not translate paths, auto-run WSL or substitute
PowerShell. Return `GIT_RUNNER_UNAVAILABLE` with reason
`local_git_platform_unsupported`, rendered as **Local Git unavailable on
Windows / Windows 尚不支援本機 Git 執行**. Observation stays available; managed
continuation/integration controls show the limitation. POSIX missing helpers
similarly show **Local Git tools missing / 缺少本機 Git 工具** and exact details.

### Deadlines, ambiguous effects and output

Preserve caller deadlines (25/60 seconds, repository fetch limits, cleanup budgets)
and the existing 600-second default (`checkpoints.py:50,82–146`). Zero, negative,
nonfinite or unbounded proposed local timeouts are refused before launch; never
expand a caller budget silently. Capture is bounded to 1 MiB per pipe with
concurrent draining (`_bounded.py:7–35`). Oversized/truncated output is not success.

Missing prerequisites/spawn failure before launch are definitive refusals. After
launch, timeout, lost pipes, cancellation during a possible effect or unreadable
evidence must not prove “nothing happened.” Preserve `AmbiguousOutcome` and each
domain's `Uncertain`/partial-cleanup behavior. A nonzero exit can follow partial
writes; preserve structured effect evidence and reconcile the original step.
No generic transport retry, start resend, branch reset or global lock clearing.

Local processes need an owned process group/session, bounded termination and
joining on cancellation/timeout/output overflow. Terminate only that helper's
owned descendants, never a BAT process or a PID from configuration. Killing the
shell alone (`_bounded.capture` currently kills its direct process) can leave
children holding locks. If termination cannot be proved, keep the operation and
resource claims uncertain and read back under the same lock. Termination cannot
roll back a push, commit or prior file write.

Use `redact` and `redact_secrets` on every diagnostic/error/receipt field that
could contain output, with bounded fixed codes and no raw command/environment.
The existing SSH runner redacts failure tails, but returns successful stdout
internally unchanged (`checkpoints.py:99–101,145–146`). Preserve the machine
protocol: indiscriminate redaction before parsing would corrupt base64 blobs,
digests and Git evidence. Validate successful output as data, retain exact bytes
only where the domain contract requires them, and sanitize the outward/logging
projection. Never log raw success stdout. Pinned document contents still use
their existing authorized, withdrawn-source-aware read contract, not a new
general terminal transcript.

## Setup operation, configuration and schema

Extend the existing registered `setup.host` action, scope `manage`; use the
installation's original principal, `config_revision` and existing-key replay
before admission. Do not add a chat confirmation or separate start action.
Current authority/admission/publication/recovery are
`managed_setup.py:47–57,356–385,504–567`.

Proposed fields for the local route:

- `params.git`: strict enum, optional for existing clients/hosts.
- `params.local_confirmation`: exactly `true` for a new local declaration,
  representing the visible BAT/central/same-filesystem assertion. It is not
  inferred from discovery. Bind it to the original installation in admission;
  persist the transport and confirmation provenance with the setup receipt.
- `params.discovery_id`: optional opaque candidate ID, validated against the
  installation/principal/config revision and exact supplied `url`, `fingerprint`
  and verified `profile_id`. An expired candidate blocks a new admission, not
  readback/replay of an already accepted operation.
- Existing `secret_ref` carries only the staged native credential reference.
  Manual advanced setup can still provide the reviewed nonsecret fields without
  discovery. Do not offer `import_profile_id` for unsupported local metadata.

Reject unknown params, local mode without confirmation, external-central native
enrollment, malformed pins/URLs, nonboolean flags and invalid reference bindings.
For local mode, do not require or probe a Git SSH alias. Enforce real managed
roots and existing writes/orchestrate tiers only when explicitly configuring
managed work; the quick start defaults both tiers and shared-clone mode to false.
A central-owned dedicated root may be offered later but is never taken from
BAT's cwd or silently substituted for a person's checkout.

Changing an existing host from SSH to local (or back) changes execution identity;
use a new stable host ID for that change rather than rebinding existing resources.
Omitted mode preserves an existing host's setting; omitted mode for a new host is
SSH. Keep endpoint/profile/pin immutability, the busy gate and concurrent-config
CAS. Validate prerequisites and BAT read-only proof before atomic publication;
activate one composite consistently across operation context and both fleets.

The original envelope includes transport/confirmation, exact endpoint/pin/profile,
candidate values, secret reference and before/after config revisions. A failed
reply is recovered with the same operation ID/key and original reference. When
the after digest is already published, activation/receipt recovery performs no
new probe or credential enrollment. Before digest permits only the original
reviewed setup transaction; another digest requires attention. A credential
expiry or discovery refresh cannot cause a new key, new host, new token or reset
of active work. Keep the existing post-publication receipt-loss tests.

The transport belongs in TOML/`HostConfig`, not a new resource owner or scheduler.
Existing durable operation JSON/steps can hold the binding and confirmation;
no new journal table, `user_version`, or data migration is needed for this
proposal. Candidate discovery can be ephemeral; durable accepted values are
self-contained. If implementation proves a separate table necessary, its DDL
must be idempotent (`CREATE TABLE IF NOT EXISTS`) on every open, preserve all
IDs/history and allocate no `user_version`. Coordinate any data step with the
integrator; existing steps 1–3 are not reused. No existing host is migrated to
local, and old operation payloads/keys are not rewritten.

## Safety review

| Repository rule | How this proposal preserves it |
| --- | --- |
| Manual sessions/worktrees read-only | Continue using inventory/read-only BAT access for capture. Source Git uses no optional locks and refuses executable config paths; capture makes no source index/HEAD/refs/file changes. No send/resume/merge/cleanup capability is added to a manual session. |
| Unknown resources need creation evidence | Local transport supplies no ownership evidence. Existing creation intent, receipt, controller/incarnation, marker and exact binding must agree before reuse/recovery. Matching path/name/pin or an existing local folder is insufficient. |
| Fixed checkpoint, new resources | Keep original committed SHA and stable operation-derived clone/worktree/branch/session IDs, verify the new HEAD before the first BAT frame, then use existing confined start/send. Dirty manual bytes are excluded and unknown dirt stays unknown. |
| Never infer ownership from a path | `git = local` is execution location only. Canonical managed-root checks limit destinations; registry/creation/marker policy separately establishes ownership. Loopback, `.bat-worktrees`, folder names and repo URLs confer none. |
| Sent-effect reconciliation/final checks | Preserve original keys, start/send frame receipts, resource claims, operation steps, final-frame/lineage/consumer gates and locked proceed exchanges. Read completed evidence first. Timeout/cancel does not release uncertain writers or cause another start/push. |
| Central/native split | Python owns configuration, authorization, Git and durable business outcomes. Native owns only bounded credential access, enrollment and existing transport/windows/Fleet. No raw shell or BAT mutation IPC. |
| Actions/scopes/coordinator | Extend registered setup and reuse existing checkpoint/integration/repository/PM/cleanup actions and scopes. Local mode neither enables tiers nor waives rate/capacity/claim gates. No redundant chat approval. |
| Shared frontend and localization | Future UI changes occur only in `desktop/src`, use both string modules, preserve drafts/stable IDs/keys and regenerate browser assets. Browser and Tauri read the same central result; unsupported native features remain explicit. |
| Own history; no Hub importer | Only Connector configuration/receipts are extended. BAT connection metadata is not Hub snapshot import/source mapping. Existing PM/backlog/history remain intact; schema adds no version allocation. |
| Worker/host/secret discipline | This task edits only this design document in its isolated branch. Implementation uses one integrator for shared core files; public evidence uses synthetic IDs/paths and mock hosts. No live writes or private deployment inventory. |
| Checks/temp data/evidence | Use disk-backed pytest wrapper, clean task-owned fixtures/containers, Python 3.10-compatible code, no global stdlib monkeypatches and `settle_operations`. Separate source, merged, packaged/native and live evidence; CI never substitutes for live acceptance. |

### New risks and mitigations

| Risk introduced or exposed by local support | Mitigation / honest limit |
| --- | --- |
| A tunnel/container/VM looks local | Explicit same-computer/same-filesystem assertion, verified owned central, frozen transport binding and BAT root/SHA checks; mismatch refuses start. These checks do not prove OS namespace identity from a path string alone. |
| Same-user local scripts have access to personal files | All existing policy/gates remain; harden source and destination helpers and reject unproven paths/config programs. Local subprocess transport is not an OS sandbox. Preserve prompt-gated/options-confirmed limitations and do not advertise full agent isolation without independent acceptance. |
| Self-attested protected-root/account proof | `run_account_check` remains on the trusted auditor SSH channel. Unsupported local auditor means unknown; never synthesize verified evidence. |
| Malicious local listener wins TOFU | No token before explicit trust; identify the notice as TOFU, offer BAT-side comparison, verify authenticated protocol/profile, retain the exact pin and refuse certificate change. First-use impersonation cannot be eliminated by loopback alone. |
| Multiple/nondefault listeners or stale metadata | Bounded candidate set, no arbitrary probing and no auto-selection when ambiguous. Advanced setup is the honest fallback; discovery has generation/revision checks. |
| Unsupported/encrypted server-token format | Require BAT's documented API/export contract, honor OS denial, never decrypt guessed envelopes or weaken ACLs. One native paste fallback; unsupported native enrollment stays unavailable. |
| Token crosses page, wrong server or diagnostics | Narrow native staging to verified owned central; references bound to principal/kind/candidate and exact accepted pin. Redact diagnostics, never persist page token bytes, test zero auth before pin trust/mismatch. |
| Symlink/hard-link/temp replacement or inherited Git programs | No-follow/inode/creation checks, fixed operation paths, reject unrelated existing temp resources, isolate read config/environment, disable hooks/filters/fsmonitor/lazy fetch as applicable. Treat incomplete hardening as a release blocker for local mutation. |
| Login startup files/PATH resolve uncontrolled executables | Non-login shell, validated helper closure and controlled environment; no page-provided PATH/argv. Preserve reviewed Git credential handling only for explicitly authorized network operations. |
| Timeout leaves child/partial push alive | Owned process group cleanup plus bounded joins; maintain cross-process locks/claims and original uncertain receipts. Read back fixed effects, never resend or assume killing undid them. |
| Transport switch redirects old operation or changes reader digest | New host ID for switches; bind per-host mode/installation at admission, preserve exact legacy SSH reader identity and completed receipts. Old unresolved steps never silently migrate. |
| macOS/Windows assumed equivalent to Linux | OS-specific helper/lock fixtures. Missing prerequisites are unavailable; Windows local Git is unsupported even when Git Bash/WSL happens to exist. No path translation guess. |
| One runner enables more actions than intended | Update and test every availability consumer; readiness is transport only. Tiers, scopes, repository bindings, creation evidence and resource checks remain necessary per action. |
| Attachments or Task verification falsely advertised ready | Separate byte/verifier capabilities. Do not dispatch with unavailable materialization, or equate local Git with complete Task Service verification. Preserve ordinary artifact upload/download. |
| Withdrawn/late candidates revive UI state | Installation/principal/revision/generation guard; clear rows/counts/details together on withdrawal, preserve only namespaced nonsecret drafts and original accepted-operation recovery. |

## Test plan and evidence

Run backend changes on **Python 3.10, 3.11, 3.12 and 3.13**, using temporary Git
repositories and mock BAT/providers only. No real checkout, BAT data directory,
OS credential record or live writes. Use `pytest-disktmp uv run --python <version>
pytest -q <test files>` with disk basetemps and cleanup on exit. Existing tests
remain intact; add assertions rather than replacing safety checks.

| Area | Required regression/acceptance cases |
| --- | --- |
| Config/dispatch | Default SSH on old/missing settings; strict enum/boolean validation; loopback/tunnel without opt-in remains unavailable; unknown host; local never falls back to SSH; aliases preserved for other services; one composite across startup/reactivation/both fleets. Use `tests/test_mcp_and_config.py`, `tests/test_managed_setup.py` and new runner tests. |
| Discovery/trust | Loopback-only bound probes, disabled/no listener/nondefault port, IPv4/IPv6, malformed/non-BAT TLS, ambiguous candidates, timeout/size/cancellation, stale candidate/config/principal, no token read/send before acceptance, certificate change before auth and no silent repin. Extend `tests/test_client.py` and managed setup; add synthetic TLS discovery fixtures. |
| Credentials | Supported plaintext profile fixture remains reference-only; local client/server token distinction; encrypted/missing/damaged/oversized/linked files and OS denial; absent platform data root; native paste cancel/replacement; zero secret in IPC/state/log/journal/URLs/argv. Native tests use injected vaults and task-owned temporary files; supported OS prompt integration needs separate acceptance. |
| Setup/recovery | Manage/installation owner, config revision CAS and busy gate, new params, tier defaults, new host identity for mode changes; lost publication receipt/restart with original key/reference; after digest causes zero repeat probe/enrollment; accepted secret/candidate expiry does not rewrite intent; different payload with same key refuses. Keep `tests/test_managed_setup.py:193,216,240`. |
| Manual source | Snapshot files/index bytes+mtime/config/HEAD/refs/worktree registrations before/after dirty source reads and continuation. No BAT write frame on capture. Unsafe filters/fsmonitor/hooks/submodules/lazy-fetch/program environment refuse without execution; source remains unchanged. Keep `tests/test_checkpoints.py:67–73,186,307,317,538,587`. |
| Fixed continuation/recovery | Temporary clone/worktree at exact SHA, isolated names, unsupported path/marker/temp/links refuse, no index reset; lost start/send reply and failed later evidence read recover once with original key/IDs. Keep write counts in `tests/test_checkpoints.py:367,470,509,560,685` and confinement recovery suites. |
| Integration/publication | Exercise every script through production local dispatch against temporary repos and controlled mock remotes: preview filesystem effects, apply/push/readback, repair/resolution, source/pin moves, no manual writes, publication absent-ref lease and missing own receipt stays unresolved. Keep `tests/test_integration.py`, `tests/test_pr_publication.py`, `tests/test_worktree_merge_operations.py`. No public GitHub push. |
| PM/repository reads and writes | Production local runner for fetch/carrier/snapshot/revision and dependency/spec reads; exact original pin, append-only draft/spec bytes, pending deterministic commit recovery, membership/writer revocation at locked gate and after read, no fetch/index write in pinned readers. Keep `tests/test_repository_sync.py`, `tests/test_pm_worktree.py`, `tests/test_pm_spec_sources.py`, `tests/test_pm_dependencies.py`. |
| Cleanup/locks | Production local locked/proceed exchange; denial/EOF/cancel before proceed produces zero mutations; timeout after proceed retains partial/uncertain receipt; surviving child/restart/concurrent helpers cannot bypass lock; manual/unknown/writer/live-consumer/sole-content/retention gates and exact deletion targets. Keep cleanup uncertainty/effects/protocol/host tests and original write-count assertions. |
| Account proof/output | Local Git still calls only the explicit auditor SSH channel, missing channel stays unknown. Verify exact bytes for internal structured evidence, redacted outward errors/success projections, 1 MiB concurrent capture, spawn refusal versus post-launch ambiguity and helper-group cleanup. Retain existing confinement channel/closure/integrity tests. |
| Platform and packaging | Linux real temporary-repo helper/lock tests; macOS helper closure, utility differences, absent flock and locks; Windows refuses before subprocess while BAT observation works. Python-version CI is not OS or installed acceptance. Packaged runtime with a minimal/empty PATH must accurately expose helper readiness. |
| Shared UX | EN/zh-TW one screen, at most one trust decision, native token fallback/instructions and no DOM secret, accessible focus/error/unsupported states, stable drafts/keys and withdrawal/late response rejection. Use stable `data-*` hooks in new/extended managed-setup Playwright/Node tests. Screenshots at 1440×900, 390×844 and 768 for layout, inspected by the implementer. |

For future UI PRs run `npm test`, the affected Playwright specs using the inherited
`BATC_UI_TEST_PORT`, `npm run build:browser` and `npm run build` in `desktop`;
commit generated browser assets with source. For backend PRs run affected pytest
files and `uv run ruff check .`, then let the integrator run full sequential
3.10–3.13 validation. On the shared worker host, full suites need the `heavy`
lock; workers do not run a full Python suite without explicit authorization.
Do not put basetemps/scratch in tmpfs or leave task-owned containers running.

This design investigation ran the following baseline command on Python 3.14.5:

```sh
pytest-disktmp uv run pytest -q tests/test_client.py tests/test_mcp_and_config.py tests/test_managed_setup.py tests/test_checkpoints.py
```

Result: **121 passed, 0 failed**. This validates the existing client/config/setup/
checkpoint behavior, not the proposed runner/discovery/native enrollment or the
planned 3.10–3.13 matrix. Ruff and the tracked public-content check also passed.
No new implementation, UI screenshot, packaged/native or live acceptance is
claimed. Full suites were not run for this design-only task.

## Staged pull requests

1. **Transport/config contract and regression protection.** Add strict host mode,
   composite wiring, explicit per-host/legacy reader bindings and local
   prerequisite/error projection. Keep local mutations disabled until the next
   gates pass. Test default SSH and unchanged old-host/old-operation behavior.
2. **POSIX helpers and local Git.** Harden checkpoint source/temp/lock handling,
   add bounded owned-process execution and the locked exchange, then enable only
   verified Linux local dispatch. Exercise all inventory consumers with real
   temporary repositories, preserved write counts and lost-effect recovery.
   macOS enablement requires its own helper/lock evidence; Windows stays unavailable.
3. **Discovery and credential contract.** Obtain pinned BAT answers, implement
   bounded central TLS discovery and candidate validation, plus a narrow native
   BAT credential staging/paste adapter. Reuse existing credential protections
   and opaque refs. Auto-read only documented supported server-token sources;
   protected/unsupported stores use paste. No page secret or generic native APIs.
4. **Guided setup and shared screen.** Extend `setup.host` params/admission/digest
   recovery and expose the one-screen EN/zh-TW flow, local confirmation and
   unsupported states. Add experience/withdrawal/key tests, regenerate browser
   assets and inspect screenshots. Update `desktop/DESIGN.md`, applicable design
   contracts, Unreleased changelog and both getting-started/setup guides. If BAT
   cannot provide copy-token instructions, keep this shipping step pending.
5. **Separate completeness follow-ups.** Local artifact helper transport and
   explicit Task verifier bindings, each with original manifests/gates/recovery;
   macOS/package acceptance; Windows Git/helper/confinement design if requested.
   These are named gaps, not implicitly enabled by local Git or a successful
   connection. Keep published GitHub synchronization and same-host checkpoints;
   do not revive unpublished cross-host workspace transport.

One integrator owns shared config/daemon/operation wiring. Each PR reports branch,
merged, installed/native and live-tested status separately. This document is the
only deliverable of the present task; implementation and BAT maintainer decisions
remain future work.
