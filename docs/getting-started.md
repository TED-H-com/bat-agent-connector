# Installation and first use

[Product overview](../README.md) · **English** · [繁體中文](getting-started.zh-TW.md)

## Choose the matching package

As checked on October 22, 2026, the latest [stable download](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.12) remains Connector **0.2.12** / desktop **0.1.8**, from `14c62830240148b14a4d28a9c428f7f8b5ba8bf3`. The [published prerelease](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.13-preview.20261022) is Connector **0.2.13** / desktop **0.1.9**, from `437ee6a7143a82e32eae1d6a008203b2df072aaf`. Download the selected version's platform asset with its `release-manifest.json` and `SHA256SUMS`. Each release has eight assets: wheel, sdist, deb, two DMGs, Windows installer, manifest and checksums. CI and installation fixtures are separate from native or live acceptance.

The stable package predates the route wake-up, managed-service recovery and first-run/status changes now included in the preview. #399–#403 merged after the preview source and are not in that package. See [delivery status](product/implementation-status.md) for merged, published, installed and live-tested evidence. The first-launch steps below describe stable 0.2.12; downloading a newer package does not establish installation or live acceptance.

| Platform | Stable 0.1.8 asset | Preview 0.1.9 asset | Package |
| --- | --- | --- | --- |
| Windows x64 | `better-agent-dashboard_0.1.8_windows_x64.exe` | `better-agent-dashboard_0.1.9_windows_x64.exe` | NSIS `.exe` |
| Mac Apple Silicon | `better-agent-dashboard_0.1.8_macos_arm64.dmg` | `better-agent-dashboard_0.1.9_macos_arm64.dmg` | `.dmg` |
| Mac Intel | `better-agent-dashboard_0.1.8_macos_x64.dmg` | `better-agent-dashboard_0.1.9_macos_x64.dmg` | `.dmg` |
| Linux | `better-agent-dashboard_0.1.8_linux_amd64.deb` | `better-agent-dashboard_0.1.9_linux_amd64.deb` | `.deb` |

Use the platform asset on the release page. Separate GitHub Actions candidates may require sign-in and expire. Windows packages are unsigned; Mac bundles use ad-hoc signing. Formal signing, Mac notarization, production update channels and a Linux persistent native credential vault are outside this delivery. GitHub's release label does not establish real-device stability.

## First launch: your local Connector

Install and open the matching package. It contains the Python runtime and prepares one private local central, its data and your personal identity. You do not install Python / uv, enter an actor, edit credential files or copy an API token for this path. With no BAT host configured, the initial Dashboard opens **Settings → Connection**, with **Get ready to work → 1. Connect BAT** expanded.

1. Paste the connection URL copied from BAT into the masked field. It fills the connection details locally.
2. Check the **Host ID**, then choose **Verify and save host**.
3. After success, choose **View sessions**. Human sessions remain read-only.

**Bind a GitHub repository** and **Configure verification commands** start collapsed. They are optional for reading sessions. Set them up when dispatch, Git work or Task Service verification needs them. Managed-work permissions also start collapsed; connecting does not grant them.

**Windows Fleet manages local connections and startup; Python Connector / Task Service authorizes and dispatches work; BAT executes it on the selected host.** A remote BAT host does not require BAT to be installed on the Dashboard computer. Windows Fleet settings remain separate from central onboarding.

### 1. Connect BAT

In **Settings → Connection → Connect BAT**, use **Paste BAT connection URL**. In BAT v3.2.15, copy it from **Settings → Remote → Remote Access → Connection URL → Copy**. With headless BAT, copy the URL from the startup banner's `connect:` line. The password field clears as soon as the address, credential and certificate fingerprint have been filled locally; a check-mark line confirms the address and abbreviated fingerprint without showing the token. Nothing is sent until **Verify and save host**. Editing the endpoint or fingerprint by hand clears that confirmation.

Enter a **Host ID** starting with a letter or number, using only letters, numbers, `-`, `_` or `.` (for example `local`). On first use, a loopback address suggests `local`; this is a name suggestion and does not establish where BAT runs or grant local execution permissions.

**Enter manually** contains the individual **BAT endpoint**, **Certificate fingerprint (SHA-256)**, **BAT workspace profile ID** and **BAT connection token** fields, plus importable **BAT connection profiles**. If profiles cannot be read, a note states that beside the profile control. Use the host and port from BAT's Connection URL, its **Certificate fingerprint (SHA-256)** and **Connection Token** in Remote Access. The endpoint must be a `wss://` origin with an explicit port, without credentials or query parameters. A copied `ws://` URL becomes `wss://`; the port defaults to 9876. Confirm manually entered fingerprints with the host owner. Encrypted or damaged profile credentials require a fresh credential.

Common input errors appear beside the field before credential staging; the token stays in the password field. A proven admission refusal unlocks the form automatically and keeps non-secret drafts. Staged credentials clear from the page; their opaque reference can be reused in the same page for up to one hour. After reloading an editable draft, paste the credential again. A lost or uncertain reply retains the original request and key: use **Check request**, including after reloading. A configuration error alone does not prove that nothing was admitted.

After connecting, **1. Connect BAT** collapses and **View sessions** is the next action. Expand the connection step or choose **Change settings** to edit it again. Later visits start with it collapsed. The setup operation link and status are in **Details**. The page-header **Refresh** reloads setup status while keeping the current draft and credential field. GitHub binding and verification remain optional collapsed steps.

Expand **Allow managed work** when enabling dispatch. Choose the intended conversation / start permissions and dedicated **Remote managed directories**. For Git synchronization, checkpoint and artifact operations, provide the existing trusted **SSH alias configured on this computer**. The SSH account, keys, known host and remote permissions must already be usable; the app does not invent them.

The SSH alias is resolved by the central service account on the central computer. For the bundled local central this is the desktop computer; when joining a remote central, prepare SSH access on that central host. The browser or Tauri client does not forward its local SSH keys to central.

**Allow Git worktrees sharing a clone** is an explicit opt-in when a direct BAT start needs shared Git metadata. Leave it off when that is not intended. It does not grant control over manual sessions or worktrees. Click **Verify and save host**; the connection/profile checks are read-only and saving does not dispatch work.

### 2. Bind a GitHub repository

Under **Bind a GitHub repository**, enter `owner/repository` and the configured host. Use **Read this host's workspaces**, then choose the actual **BAT workspace ID**. Supply the **Git remote URL** and GitHub token when required. Enable integration, PR merge or metadata updates only for the intended workflow, then click **Verify and bind repository**.

The repository-to-host/workspace association is explicit. A similar project name, folder or remote URL is not a substitute for that binding. Verification reads BAT/GitHub; it does not push, create a PR or merge. Published-source dispatch additionally needs managed start permissions, a dedicated remote root and the trusted SSH alias. Deployment recipes remain separately configured.

### 3. Configure verification commands when needed

**Configure verification commands** applies to Task Service projects. Enter the exact **Task Service project name**, **Executable**, **Arguments (one per line)** and **Timeout in seconds (1–3600)**. Each nonempty argument line becomes one literal argument; spaces within it are retained. Shell quoting, pipes and expansion are not interpreted. Do not enter credentials.

**Save verification command** records the command without executing it. Other project commands are preserved; the timeout applies to all configured projects. Use the task's actual project key; it is not automatically mapped from a Dashboard project ID or title. If central configuration changed while editing, reload the saved verification settings, review the preserved draft and save again. Verification runs later only through the existing task, ownership and execution gates.

### 4. Start and follow work

Open **Projects**, associate the project with the configured repository, choose the bound host/workspace and review a fixed source commit before dispatching instructions, a model and optional attachment revisions. Same-host continuation can use a fixed local checkpoint; another host uses an explicitly published commit through the bound repository. Manual source work stays read-only.

The adjustable left tree keeps the selected conversation, reply controls and result links together. A send receipt records that request's outcome; a historical queued receipt is not the current BAT queue position. Model preferences affect the offered selection, not provider authorization. Saved skill selections pin reviewed catalog sources and are not automatic activation in a running agent. **Create repair work** records fixed failure evidence; review its destination and source before starting the new work.

### Background, browser and recovery

Use **Open Dashboard in browser** or the tray / menu-bar entry. The app hands off a one-use sign-in ticket; the long-lived API token stays out of URLs and browser storage. Web and Tauri use the same central identity and journal. After browser logout, reopen from the desktop entry.

Enable **Start when I sign in** for this installation's login startup. Closing Web or the Dashboard window leaves central running. Explicit desktop Quit retains central work while following the separate Fleet shutdown guards. Reopening recovers the same installation; a newer package upgrades only after the owned service accepts a safe stop. Busy or uncertain work prevents that transition, and older packages do not downgrade the journal.

Configuration changes wait while operations/tasks are active. If a reply is lost, use **Check request** and the **Setup operation** link in **Details**; do not submit a new request to guess the outcome. Expired credentials, invalid ownership or failed startup are recovery states, not reasons to create another database.

<a id="pm-agent-setup"></a>

## PM agent setup

Open **Settings → Agents** to check the Connector host's PM installation. **Check again**
only reads eligibility; it does not launch a model or read sign-in contents. **Ready**
means eligible for per-run checks, not a verified model execution. Coming from a discussion
also checks that workspace's existing BAT analysis configuration and permissions. Without
workspace context, only the local installation can be checked. Follow the runtime
prerequisites below to resolve an unavailable result. Manual sessions stay read-only.

## Try one PM workflow

Use a central and client built from the same reviewed PM candidate. Availability
comes from that central's capabilities; an older installation may expose fewer
steps. See the [current PM implementation status](product/pm-v1.2-status.md) for
merged, candidate and remaining work.

**PM runtime prerequisite:** discussion analysis, plan proposals and Follow-up
analysis can explicitly select the configured Linux central runtime or the
selected Linux BAT host. Each action has its own choice; Windows/macOS clients
can use either route when its capability is available. The central runtime's
service account needs Codex CLI **0.160.0**, bubblewrap, and a readable regular
native sign-in file in its configured `CODEX_HOME` (normally `~/.codex/auth.json`).
The current adapter does not import custom provider configuration or API-key
environment variables, and does not support administrator-managed Codex
configuration. Do not remove managed policy to enable it. See the
[native runtime boundary](design/pm-native-runtime.md).
BAT analysis requires the reviewed
dedicated PM runtime on the selected Linux BAT host; ordinary read-only BAT
settings are insufficient. See [BAT analysis prerequisites](design/pm-bat-analysis.md).
Capabilities show installation eligibility; each run still checks the actual
runtime, authentication and original completion evidence. A saved request keeps
its original adapter and key, including after a lost reply. An unavailable
adapter does not silently fall back to another. Older servers retain their
original native proposal and Follow-up behavior.

1. Use the workspace switcher → **New workspace**. In **Discussions**, start a
   **New discussion** in Idea mode. Save the goal and constraints in ordinary notes—
   for example, member login and password reset, with referral codes outside the first release.
   Choose **Send** to create the discussion and save the note before opening **Discussion info**.
   The project sidebar keeps Overview, Discussions, Requirements, Schedule, Dependencies,
   Issues & changes, Agents and Board in the same workspace; **Work** opens bound Sessions.
2. In **Discussions**, open **Discussion info → Context → Prepare discussion context**
   → **Save in a PM worktree**. Choose the
   intended existing repository/host/workspace connection, enter the published
   branch and choose **Preview published revision**. Check the fixed commit,
   describe the purpose and **Save context package**. This needs the repository
   binding and managed Git/start prerequisites described above. **Discussion
   record only** does not prepare the Git worktree needed for adoption or BAT
   analysis. Later requests can select **Use the existing PM worktree**.
3. In **Discussion info → Context → Analysis options**, choose an available analysis
   adapter, then use **Ask PM**. A record's **Discuss with PM** entry opens Discussions
   with its source chip and automatic title. Review the **Proposed update** card. Review its source quotes, retained
   requirements, exclusions, proposed removals and open questions. Edit the
   wording as needed, then **Review & adopt** the whole update once. Adoption needs workspace
   owner authority and the prepared PM-owned Git worktree.
4. In **Requirements**, inspect the adopted requirements and their bound work.
   The header's **More actions → Work packages** opens the existing package catalogue.
   Review each package’s adopted requirements and explicit repository/commit destination.
   This UI lists existing packages; package creation uses the registered
   `pm.work_package.create` API action. An accepted plan
   records direction; starting a package is a separate product action.
5. In **Requirements → More actions → Quality reviews**, verify the fixed result
   using the configured command or original GitHub checks. Appoint a **Quality review** against the fixed criteria and Definition of Done.
   The responsible reviewer records conclusions, reasons and original evidence.
   Select that assessment for the work or its dependency condition; the shared
   views use the same original result. Passing tests alone leave acceptance open.
6. In **Dependencies**, open **Release baselines** and **Maintenance policy**.
   After publication, record the verified release baseline and review a maintenance
   policy's sources, repair bounds and delivery ceiling. **Issues & changes → More actions
   → Maintenance review** shows
   the original findings and repair receipts. A published PR, merged result,
   verified deployment and completed runtime remain distinct facts.
   Enable a reviewed policy with the explicit GitHub observation profile, sources,
   budget and repair rule before expecting scheduled checks or repairs. Maintain
   entry requires a fresh, complete eligible workload observation; an idle-looking
   session or a retained finished worktree does not decide the stage.
7. In **Overview → Report → Save report**, save a snapshot. Use **Report → Versions**
   to select it, then **Report → Export brief** for the download controls. Choose SVG, Markdown or HTML and the language.
   Every export uses that same fixed snapshot. In Review, select the intended
   snapshot, record discussion and feedback, then record the owner's outcome and
   owned next steps. A prepared report or downloaded SVG is not a completed Review.
   Select the previous Review when preparing the next one to retain its decisions
   and reported effects.
8. In **Issues & changes → More actions → Maintenance review**, open a saved candidate.
   First create or select an existing **Idea** in this workspace's **Discussions**. Open that candidate's **Add to Idea
   discussion**, choose the Idea and topic, and submit. The full original proposal,
   rationale and source identities remain attached; the new input starts
   unresolved. Open the selected discussion to confirm or select its direction,
   then explicitly prepare Planning or a plan proposal. Importing a candidate
   does not adopt it, create a discussion automatically or start work.
9. For another analysis, use **Discussions → Discussion info → Context → Analysis options**
   to choose the adapter, then **Ask PM**. Under
   **Accepted plan for this analysis (optional)**, preview the accepted revision,
   check complete/omitted file coverage, then select that exact version. Without
   this selection the analysis uses the bounded discussion context. On the BAT
   adapter, a new request uses a new owned session on the same binding after the
   original turn and process death are proven. Service restart preserves the
   discussion and fixed files; an unknown old writer blocks a successor.
10. In **Overview → More actions → Change phase**, open **Project activity** and
    record a reason to pause or close. New managed work
    stops; inspect original in-flight operations and readable results. Reopen is
    an explicit owner action with the current lifecycle version. It does not
    replay old commands or resume the old execution epoch. Retaining an unresolved
    operation is not proof that its runtime stopped or permission to resend it.

A missing runtime, Git worktree, verifier or original source is reported at its
step. Resolve that prerequisite before starting a new action. For a lost reply,
use **Check original request** with the saved draft; recovery retains its original
version and operation. Human checkouts and manual sessions remain read-only.
These steps describe available product actions, not a claim that full
lifecycle live acceptance has run. Record actual operation, source, runtime and
delivery receipts separately from offline tests and CI.

### Read an AHP host catalog

In the workspace's **Agents** view, expand **AHP observations**. An operator must first configure an
adapter for this workspace on central, including its endpoint, credential reference
and credential transport; see the [AHP observation contract](design/pm-ahp-observer.md).
The capture negotiates a supported protocol version, and its saved receipt identifies
that version and decoder profile. There is no protocol-profile configuration field.
The client does not accept host URLs or credentials here. This is separate from
selecting an analysis runtime or moving a writer to another adapter.

Choose the configured adapter and **Capture host catalog**. Select up to eight
sessions from that saved catalog, optionally include advertised static changesets,
then **Capture selected snapshots**. Only catalogued sessions and bounded metadata
are read; no session is created, messaged or stopped. **Reload saved observations**
and **Saved observation history** read central receipts without contacting the host.
Open **Original source document and receipt** for identities, event metadata, replay
gaps, missing sources and the original operation. An incomplete observation does not
prove absence, completion, cost or management authority.

Changing adapters separates selections and request keys. If a response is lost,
return to the original adapter and use **Check original request**; a changed or
withdrawn source may prevent recovery until its original access is restored. Never
recreate an uncertain request on another adapter. Configured protocol support and
offline tests do not establish live host acceptance.

## Advanced: existing central and operator deployment

Existing desktop central configuration is retained. To join a different service explicitly, use **Advanced: join an existing central**, then enroll its separate credential. The remaining sections describe an operator-managed central; they are not prerequisites for the normal bundled installation.

### 1. Prepare a central host

The following operator example uses Linux and Python 3.10–3.13. This is separate from the bundled desktop path. Central needs access to BAT; managed Git work also needs configured SSH / Git access, a Connector-owned managed root and an explicit repository binding.

The two credentials serve different purposes:

| Credential | Used by | Purpose |
| --- | --- | --- |
| BAT remote token | Connector to a BAT host | Access BAT's remote protocol, with its certificate fingerprint pinned. |
| Connector API token | Dashboard or MCP client to central | Identify the caller and authorize central actions. Issue one per client / actor. |

Do not use a BAT remote token in the Dashboard API login field.

### 2. Install Connector

For the operator/CLI path on Linux or macOS, install `uv`, Git and Python 3.10+
first. The verified installer resolves an explicit release tag, checks its manifest
and SHA256SUMS, installs the manifest's fixed commit with locked dependencies,
and verifies source files, VCS identity, canonical skill and `batc --version`.
It creates a private version directory under
`~/.local/share/bat-agent-connector/releases/<commit>` and prints its
`rollout-receipt.json` for [update enrollment](connector-updates.md).

For an existing uv tool installation, see [adoption and restoration](connector-updates.md#adopting-an-existing-uv-tool-installation).

Both published versions include the installer. Pin the same tag for the bootstrap
and installation. For stable **v0.2.12**:

```bash
curl -fsSL https://raw.githubusercontent.com/TED-H-com/bat-agent-connector/v0.2.12/scripts/install.sh | sh -s -- --tag v0.2.12 --channel-check stable
```

For prerelease **v0.2.13-preview.20261022**:

```bash
curl -fsSL https://raw.githubusercontent.com/TED-H-com/bat-agent-connector/v0.2.13-preview.20261022/scripts/install.sh | sh -s -- --tag v0.2.13-preview.20261022 --channel-check preview
```

Add `~/.local/bin` to PATH. `--dry-run` verifies release metadata without writes;
`--verify-only` verifies an existing installation and its receipt without writes.
`--channel-check stable` refuses previews; the default `preview` accepts stable releases
and prereleases. The updater uses that channel to offer eligible newer versions.
`--root /absolute/private/version-directory` selects a new version directory.
Root requires `--allow-root`. Existing entrypoints/skills and update-managed
installations are refused; configuration, credentials and device identity are
never overwritten. Failed installs retain their private directory for inspection;
they do not issue a successful rollout receipt. Use a new empty root for a retry.

On Linux, `--enable-updates` enrolls the selected channel and schedules the user
systemd timer. Otherwise follow the printed enrollment and schedule commands
for that channel; installation alone does not enable updates. macOS supports installation and
verification; the current automatic updater requires Linux.
GitHub HTTPS/release authority is the trust boundary; checksums are not an
independent signature. The bootstrap optionally uses `GH_TOKEN` for the release
API only and never prints it.

Install with `uv` on the central host. For a development source installation, pin a reviewed commit instead of silently following changing `main`. Replace `REVIEWED_COMMIT` with the reviewed source commit matching your chosen desktop / service deployment:

```bash
uv tool install 'git+https://github.com/TED-H-com/bat-agent-connector@REVIEWED_COMMIT'
batc --help
```

A development checkout can instead use `uv sync --locked --extra dev`, with `uv run batc` for the commands below. Keep the desktop and central contract versions compatible; the desktop refuses incompatible identity / contracts.

### 3. Configure BAT access

Linux operators can use the optional [user SSH tunnels and token-file provisioning helpers](../contrib/linux/README.md) with existing trusted SSH aliases and Bitwarden or command sources.

If this host has the BAT desktop client's profiles:

```bash
batc import-bat
batc hosts
```

Import writes `~/.config/bat-agent-connector/hosts.toml` with writes disabled. It stores the endpoint, pinned TLS fingerprint and a **token reference**, not the token value. `batc hosts` probes configured hosts; a successful probe does not prove Git access or deployment readiness.

Keep shared defaults in a public `hosts.toml` and private host definitions in untracked `hosts.d/90-private.toml` beside the effective config file (selected by `BATC_CONFIG`, or `BATC_CONFIG_DIR` when unset); the base file may be absent if fragments exist. Non-hidden `*.toml` fragments load in lexical filename order: tables merge recursively, while later scalars and arrays replace earlier values; defining a host in multiple files is an error. Fragments must be private regular files, not symlinks, owned by the current user where supported, with no group/other permissions on POSIX (`chmod 600`); use only `token_ref`, never token values. `batc import-bat` continues to write only `hosts.toml`.

For a different profile location, use `batc import-bat --profiles-dir /path/to/profiles`. Preview generated configuration with `--output -`. Do not add `--force` merely to bypass an existing configuration.

Without local BAT profiles, configure [hosts.example.toml](../examples/hosts.example.toml) with the actual BAT endpoint, trusted fingerprint and an appropriate `token_ref`:

Token files (`file:`) and BAT token stores (`bat-profile:`) must be regular files, not symlinks, owned by the current user, with no group or other permissions (for example, `chmod 600`). Ownership is checked where supported; permission checks are skipped on Windows. Token files are limited to 64 KiB and BAT token stores to 1 MiB. Audit files use the same private-file rules: existing files are refused rather than having their permissions changed; new files use mode 0600 and newly created parent directories use mode 0700.

- `env:NAME`: resolve from the service's private environment.
- `file:/path`: resolve from a private token file.
- `bat-profile:ID`: resolve from the supported BAT client token store.

A default profile path is not a cross-platform discovery guarantee. Preserve the original BAT configuration and keep credentials out of source control.

### 4. Start central and open Web

Run the service in one terminal on the central host:

```bash
batc serve
```

The default is `127.0.0.1:18796`. The process must keep running; this command does not install an autostart service. An already owned service may report `OWNER_CONFLICT`: use that owner instead of creating another database.

In a second terminal on the same host, issue an observation identity:

```bash
batc api-token issue --actor personal-dashboard --scope observe
```

The token is printed once. Keep it private. Open `http://127.0.0.1:18796/dashboard/` on that host and use the Connection screen to sign in. Pending, Projects and Sessions should become available. This initial identity intentionally cannot dispatch or edit work.

If the central host is remote, use an existing verified tunnel. An example with a previously configured and trusted SSH alias is:

```bash
ssh -N -L 18796:127.0.0.1:18796 central-alias
```

Then open the same loopback Dashboard address on the client machine. Keep that tunnel running. Do not expose the service by changing its bind address or disabling Host / Origin checks. The service does not currently provide a general public-hosting ingress.

### 5. Join that external central from desktop

On a managed desktop, choose **Advanced: join an existing central** in Connection settings. Enter the trusted central address and expected actor, confirm in the native window, then enroll that central's API token through **Add credential**. Windows uses Credential Manager; Mac uses Keychain. The old managed service and its work remain intact; its personal credential is never forwarded to the external address.

An existing `central.json` remains selected on upgrade, including when it needs repair. A failed external connection never creates a local replacement database. For an older client-only package, this external-central form is its normal connection screen; that does not prove the package contains the new bundled runtime.

For an external central on Linux, native credential enrollment remains memory-only through `BATC_DESKTOP_TOKEN`; browser login is another supported entry. This limitation does not prevent the candidate's local managed installation from owning a private service credential. [Native configuration and credentials](design/desktop.md#configure-the-native-client).

**Forget saved credential** removes only the local saved credential; it does not revoke the central token. Windows Fleet has separate connection / login choices described in the [Windows guide](windows.md).

### 6. Enable useful work deliberately

Observation working is the first checkpoint. For actual dispatch, the operator configures:

1. The selected host's `writes` and `orchestrate` gates, owned managed root, Git / SSH runner and confinement policy.
2. An explicit repository-to-host/workspace binding under the existing `github.repos` configuration. A project name, workspace path or Git remote alone is not that binding.
3. The client's scopes for the intended action. A typical project-dispatch identity needs `observe`, `manage` and `start`; session interaction uses `operate`. Integration, acceptance, merge, deployment and cleanup have separate scopes.

Example binding (replace every demonstration value with the reviewed destination):

```toml
[[github.repos]]
repository = "example/project"
sync = { remote_url = "git@github.com:example/project.git", bindings = [{ host = "worker", workspace_id = "exact-workspace-id" }] }
```

This snippet is not the complete host / GitHub configuration and does not grant integration push or deployment permission. Follow [repository setup](design/repository-sync.md) and the [configuration example](../examples/hosts.example.toml).

In Dashboard, create a project and explicitly choose its configured repository. Open project dispatch, choose the destination and branch, review its fixed commit, add original instructions and optional attachments / model, then submit. Return to the project to follow the work and its delivery entry.

Read the integration preview before adding results to a PR. Merge and deployment require their own configuration and scopes; deployment additionally needs a recipe with actual source-version / health verification. [Delivery setup](design/delivery.md).

<a id="cli-and-agent-access"></a>

## CLI and agent access

**Settings → Agents** offers a copyable dispatch config for an operator installation
(`uv tool install`), with a placeholder instead of a real token. The bundled desktop app
ships only its managed service launcher; it does not put `bat-agent-connector-mcp` or
`batc` on PATH. For that installation, follow the operator setup above on the intended
Connector host before configuring an MCP client. Do not copy the desktop's personal token.

The operator issues a separate token from the same service's configured journal:

```bash
batc api-token issue --actor mcp-agent --scope observe --scope operate --scope start
```

Store the printed token privately as the MCP process's `BATC_API_TOKEN`. Grant `manage`
only when project or work-item changes are needed. Keep `--principal-only`; host gates,
scopes and managed-resource ownership still apply. Use `BATC_TASK_URL` when the intended
Task Service is not at the default endpoint.

Manual sessions offer three paths: reply in BAT; review and capture a fixed checkpoint,
then continue in a new managed session; or let a scoped MCP agent drive managed work.
The checkpoint path keeps the person's session and checkout read-only. The Start and
dispatch forms explain the worktree, BAT session and tracked evidence in **How dispatch works**.


The CLI offers the same observation and operation records:

```bash
batc hosts
batc inventory
batc history --help
batc relations --help
batc op --help
batc repository --help
batc delivery --help
batc resource-cleanup --help
```

Register `bat-agent-connector-mcp` in the MCP client's supported configuration. A common read-only JSON form is:

```json
{
  "mcpServers": {
    "bat": {
      "command": "bat-agent-connector-mcp",
      "args": ["--principal-only", "--read-only"]
    }
  }
}
```

Run the configured central service and give this MCP process its own scoped `BATC_API_TOKEN` through private environment configuration, including `observe` for reads. Ensure it can find the executable and intended configuration. `--principal-only` keeps the agent on its authenticated central identity; retain it when enabling writes. Remove only `--read-only` when the client is meant to act; host gates and scopes still apply. Set `BATC_TASK_URL` privately if central uses a different RPC endpoint.

The operator compatibility mode without `--principal-only` includes local-admin read fallback and direct Fleet tools. It is not a scope-isolated agent endpoint. A trusted local operator can use `bat-agent-connector-mcp --read-only` for direct BAT observation without the central workflow, but this is not the default for a shared agent integration.

The optional loopback MCP HTTP transport is a separate adapter:

```bash
bat-agent-connector-mcp --principal-only --read-only --http --port 8765
```

This serves MCP at `http://127.0.0.1:8765/mcp`; it is not the Web Dashboard address. Use the [canonical skill](../skills/bat-agent-connector/SKILL.md) and [matching agent adapters](agent-skills.md) for durable work and recovery. Task Service recipes have additional settings; a queued task with the planner disabled does not prove dispatch failure. [Task Service](design/task-service.md).

## Troubleshooting

### Connector installer: `entrypoint_directory_not_owned`

The installer reports the offending directory and a `chmod g-w,o-w <dir>` hint.
Directories such as `~/.local`, `~/.local/bin` or `~/.config` must be owned by you
and must not be group- or world-writable. Review the reported path, then run the
suggested command for that directory and retry installation or `--verify-only`.
The installer never changes permissions automatically. If the owner is wrong,
removing write permissions alone will not fix ownership; ask the administrator.


| What you see | What to check / do |
| --- | --- |
| Desktop asks for an external central / actor | Check whether this is an older client-only artifact or an existing external configuration. On the matching managed candidate, ordinary first launch prepares local central automatically. |
| Managed startup or upgrade needs attention | Preserve its installation/data. Reopen the matching app; unresolved work or credentials can prevent startup/upgrade. Do not delete the journal or create a second owner. |
| BAT profile cannot be imported | Check profile metadata, certificate trust and credential availability. Use manual host entry if its token store is encrypted or damaged. |
| Connected but no hosts / sessions | Review **Connect BAT** and actual host reachability. Offline or stale observations do not prove work stopped. |
| No destination in project dispatch | Save the exact repository/host/workspace binding and associate the project with that repository. |
| Setup is busy or a receipt is missing | Let active work settle; recover the original setup request and operation before changing it. |
| An action is disabled | Read its scopes, host permissions, ownership and source/binding requirements. |
| A send/start reply is lost | Keep its operation ID and request key; read back instead of dispatching again. |
| Native BAT merge acknowledgement stays unknown | Preserve its original operation and resources. The separate human-adjudication API is outside the current merge contract. |
| Cleanup is refused | Read the retention reasons; unresolved writers and unique content must remain. |
| Fleet does not start | For a new installation, use the [public Rust configuration](design/fleet-configuration.md), then check selected connections, prerequisites and ownership. An omitted backend retains PowerShell compatibility; existing owners require reviewed migration. |

Real Fleet/account/deployment acceptance is performed by the user. The [46-item acceptance reference](product/acceptance-v2.md) remains useful for that work; it is excluded from the agent's completion checklist, not reported as already passed.

## Diagnostics

Open **Settings → About → Export diagnostics**, or use the same action in an
error's **Details**. Review the summary and JSON, then Save or Copy. Nothing is
uploaded by the app. The file contains build versions/commits, anonymous host
states, capability/policy booleans, counts from client-read sessions, up to 50
failed API calls (route templates), and the loaded page of failed/uncertain
operations. Missing sources and versions remain unknown. History reason counts
appear only when the Connector supplies `reading.incomplete_reasons`.

The export never includes tokens, secret references, certificate fingerprints,
hostnames/endpoints/SSH aliases, user paths, session/thread titles or messages,
prompts/drafts, repository/remote/branch names, actor IDs or email addresses.
Only named fields are selected, followed by a serialized-text scrubber. Inspect
the preview before sharing the saved file yourself.

Desktop state normally lives under the following templates (custom locations
may differ):

| OS | State database |
| --- | --- |
| Linux | `~/.local/share/io.betteragent.dashboard/central/state/tasks.sqlite3` |
| macOS | `~/Library/Application Support/io.betteragent.dashboard/central/state/tasks.sqlite3` |
| Windows | `%LOCALAPPDATA%\io.betteragent.dashboard\central\state\tasks.sqlite3` |

`config/hosts.toml` and `installation.json` are inside the same `central` directory.
These are state/configuration files, not exported logs. The desktop does **not**
persist Connector stdout/stderr; startup error lines stay in memory. For a
standalone CLI service, stdout/stderr go to its launching terminal/service manager;
the configured journal location is not inferred by diagnostics. Default journal
templates follow `config.state_dir()`: `~/.local/state/bat-agent-connector/tasks.sqlite3`
on Linux/macOS and `~\.local\state\bat-agent-connector\tasks.sqlite3` on Windows,
unless `BATC_STATE_DIR` or `XDG_STATE_HOME` is configured.

About shows the Connector version and installed Git commit, or `unknown` for a
plain package/editable checkout without Git-install provenance. A desktop bundle
also shows its native app version/commit. Packaging records both commits; local
native builds without `BATC_BUILD_COMMIT` show `unknown`. Browser assets use the
serving Connector's identity and do not embed the checkout's HEAD.

## Linux Connector automatic updates

Connector 0.2.7 introduces an opt-in user timer for published Connector updates.
See [enrollment, status and rollback](connector-updates.md). This changes future
launches; the desktop bundled runtime retains its existing lifecycle.
