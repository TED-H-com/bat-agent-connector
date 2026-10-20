# Windows Fleet and desktop resources

**English** · [繁體中文](windows.zh-TW.md) · [Product overview](../README.md)

Windows Fleet is the local connection and startup layer inside Tauri. Python Connector / Task Service authorizes and dispatches work; BAT executes it on the selected host. The candidate desktop package includes a local Python central runtime. Joining an existing central, including one on Linux, remains an advanced option.

Open the [desktop workflow on `main`](https://github.com/TED-H-com/bat-agent-connector/actions/workflows/desktop.yml?query=branch%3Amain) and select a successful run from the current public history. Match the source commit and Windows installed-fixture records, then download `desktop-Windows-unsigned` from its successful Windows package job. Wait if that job is still building. Historical PR artifacts and earlier release assets are not the current download source.

## What runs where

| Layer | Responsibility |
| --- | --- |
| Windows Tauri / Fleet | Selected background connections, SSH tunnel ownership, route/readiness checks, BAT profile launch, login choices, credentials, files and tray. |
| Python Connector / Task Service | Agent identities, authorization, dispatch, task coordination, operations, receipts and history. |
| BAT hosts | Workspaces, human sessions and worktrees, plus separate agent-owned managed resources. Human BAT desktop / mobile access continues. |
| GitHub / configured deployment target | Published commits, integration PRs, merge and deployment evidence through central operations. |

Project organization is one Dashboard view over this system. Project Hub supplies
interaction references; it is not the runtime or data backend.

## First launch and BAT access

1. Install the matching NSIS `.exe` and launch Dashboard. It prepares its private local central and personal identity; no preinstalled Python / uv or copied central API token is needed.
2. Open **Connection / Fleet → Get ready to work**. In **Connect BAT**, choose an importable profile or enter the actual BAT endpoint, trusted fingerprint, workspace profile ID and BAT token. Choose the intended managed permissions and dedicated remote directories, then **Verify and save host**.
3. Git synchronization and artifact work use an existing trusted **SSH alias configured on this computer**. Choose **Allow Git worktrees sharing a clone** only when the intended direct BAT starts need shared clone metadata. Neither setting transfers ownership of human work.
4. Under **Bind a GitHub repository**, read the selected host's workspaces and bind the exact repository, Git remote and workspace ID. Grant only the intended integration / merge / metadata permissions. Saving verifies access without pushing or opening a PR.
5. **Configure verification commands** is for exact Task Service project keys: executable, one literal argument per line, and timeout. Saving does not run the command. See the [full setup guide](getting-started.md) for source selection, recovery and agent access.

The shared Dashboard provides the adjustable project tree, conversation and send receipts, model preferences, reviewed skill selections, results and repair-work entry. Skill selection does not automatically activate a skill in an agent.

## Fleet connections, profiles and sign-in

For a new Fleet installation, use the public [Rust configuration and setup guide](design/fleet-configuration.md) with [fleet.example.json](../desktop/fleet.example.json) and the [generic inventory, profile and SSH examples](../desktop/fleet.example/README.md). Explicit `"backend":"rust"` needs no separate repository or legacy PS/VBS scripts. Replace all example connection details with your own trusted configuration. Existing Fleet installations retain their inventory, profiles, SSH configuration and owner; use the native migration flow for a backend change. Central onboarding does not invent SSH topology or take over an existing Fleet owner.

In Connection settings, inspect the current backend and choose **connections**, **BAT profiles** and **Dashboard** independently. Preview prerequisites before saving or launching. Dashboard-only works without a local BAT executable. Tunnel, pinned TLS, BAT authentication/workspace and central observe access are distinct readiness checks.

For Tailscale sign-in, open the installed Tailscale app from its panel, sign in there, then refresh status. Keep the login picker or review the saved launch choices. An omitted `backend` retains **PowerShell** compatibility; changing an existing PowerShell installation to Rust requires the explicit migration flow that proves the previous owner stopped.

For a known idle Tailscale peer, the Rust Fleet monitor automatically wakes the
connection using the configured Tailscale IP, with bounded retries and a cooldown.
The panel shows wake progress and retries automatically if the host remains unreachable;
expand Details for the diagnostic code. Unknown or ambiguous peers are never pinged.

**Start when I sign in** starts the owned managed installation and the selected Windows Fleet login flow. **Open Dashboard in browser** uses authenticated local handoff. Closing the window leaves background central work running. Explicit **Quit Dashboard** follows the normal shutdown guards for the proven local Fleet monitor; it does not stop central tasks or manual BAT sessions.

**Advanced: join an existing central** changes the selected client binding after native confirmation and separate credential enrollment. Existing local background work remains. A configured remote bootstrap uses a fixed SSH recipe to query/start that central; it does not install an arbitrary service or create another database after failure.

## Resource index

| Topic | Maintained resource |
| --- | --- |
| Connections, windows and login choices | [Native Fleet integration](design/desktop-fleet-native.md) · [shared UI](../desktop/src/fleet-desktop.js) |
| Rust runtime and PowerShell compatibility | [Fleet core](../desktop/fleet-core/README.md) · [backend routing](../desktop/src-tauri/src/fleet.rs) |
| Tailscale, configured fallback routes and readiness | [Route policy](design/fleet-routes.md) · [Tailscale sign-in](design/tailscale-recovery.md) |
| Startup and owner migration | [Migration](design/fleet-migration.md) · [Windows ownership](design/fleet-windows.md) |
| Selected central service startup | [Fixed bootstrap recipe](design/fleet-bootstrap.md) |
| Credential Manager, tray and native files | [Desktop client](design/desktop.md) · [file operations](design/native-files.md) |
| Validation packages and installed fixtures | [Desktop CI](https://github.com/TED-H-com/bat-agent-connector/actions/workflows/desktop.yml?query=branch%3Amain) · [acceptance matrix](product/acceptance-v2.md) |

Windows package/WebView fixtures, Fleet source tests and a user's real SSH / Tailscale / BAT environment are distinct evidence. Match validation records to the package source commit. Formal signing and production update channels are excluded from this delivery; full real-environment acceptance belongs to the user. Neither exclusion is a completed feature or test. Mac Dashboard support does not imply a Windows Fleet port.
