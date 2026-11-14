# Windows hidden process inventory

This inventory covers repository-owned Rust constructors and flag overrides, Node
child_process calls (including tests/build tooling), PowerShell process APIs and
scheduled tasks, plus Connector Python launch sites. Citations name searchable source symbols.
Third-party `desktop/vendor/`, installed dependencies and generated bundles are not
separate maintained launch implementations. Shell commands in CI/build shell scripts
run inside the invoking runner's existing console; these do not create desktop
console windows via a product process API. Remote POSIX programs embedded in Python
strings execute on the selected SSH host; their local SSH carrier uses the hidden
Python policy. No remote execution, authorization or lifecycle policy changed.

The application has no Tauri shell/sidecar plugin launch path. Its three system-browser
open requests use tauri-plugin-opener 2.7.0 / locked open 5.4.4, whose Windows
PowerShell, explorer and cmd fallback constructors already set CREATE_NO_WINDOW;
GUI browser behavior is preserved. Desktop updater checks/downloads use HTTP;
installation delegates to the signed installer GUI through the locked updater
plugin (ShellExecuteW), preserving its intended installer UI. Native Fleet SSH
tunnels, BAT profile launches, managed Connector bootstrap, PowerShell adapter,
Tailscale CLI/tray and route probes use the shared Rust constructors in
`desktop/fleet-core/src/hidden_process.rs`. Tokio uses the same standard constructor
through `Command::from`. Every explicit creation_flags update uses hidden_flags,
which ORs CREATE_NO_WINDOW (0x08000000) with the existing flags, including breakaway,
Unicode environment and any suspended-process flags. This suppresses console
allocation; it does not suppress BAT, Dashboard or Tailscale GUI windows.

PowerShell executable invocations use -NoProfile -NonInteractive -WindowStyle Hidden,
retaining ExecutionPolicy and other arguments. PowerShell script/scriptblock calls
inside an existing process do not launch another PowerShell executable.

Task Scheduler Hidden=true hides task presentation, not console allocation. The
Fleet monitor installer normally launches the release Dashboard GUI-subsystem exe
directly. If supplied a known console executable (powershell/pwsh/cmd/node), it uses
`%SystemRoot%/System32/conhost.exe --headless` with the same action arguments. This
uses the supported Windows 10/11 system console host without generating a VBS file,
changing user/logon/run-level settings, or introducing another persisted script.
The Dashboard release entry point already specifies the Windows GUI subsystem.

The guard runs in both CI workflows. Its allowlist binds an exact path, regex and
justification. It rejects direct Rust constructors/unsafe flag overrides and raw
Node child_process imports outside the shared helper, PowerShell visible starts,
unhidden task settings/XML and console XML task actions. Python local constructors
also require the shared policy. The allowlist preserves self-contained remote POSIX
scripts and distinguishes in-process PowerShell scriptblocks from external starts.
Tests exercise violations, accepted patterns, allowlist matching, flag preservation,
portable constructors and (on Windows CI) the absence of a console in a real child.
Linux checks cannot establish physical Windows visual acceptance.

| File and symbol | What it launches / launch expression | How it was fixed |
| --- | --- | --- |
| `desktop/fleet-core/src/connection_participant/protected/authority_tests.rs` — `hidden_command` | `Standard child: let output = crate::hidden_process::hidden_command("cmd.exe")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/connection_participant/protected/publish_tests.rs` — `hidden_command` | `Standard child: let mut child = crate::hidden_process::hidden_command(std::env::current_exe().unwrap())` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/hidden_process.rs` — `hidden_command` | `Standard child: let mut command = Command::new(program);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/hidden_process.rs` — `hidden_flags` | `Existing creation flags: command.creation_flags(hidden_flags(0));` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/hidden_process.rs` — `hidden_command` | `Tokio child: tokio::process::Command::from(hidden_command(program))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/hidden_process.rs` — `hidden_command` | `Standard child: let mut command = hidden_command("example");` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/hidden_process.rs` — `hidden_tokio_command` | `Tokio child: hidden_tokio_command("example").as_std().get_program(),` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/hidden_process.rs` — `hidden_command` | `Standard child: let result = hidden_command("powershell.exe")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/route_probe.rs` — `hidden_tokio_command` | `Tailscale route probe; Tokio child: let mut command = crate::hidden_process::hidden_tokio_command(path);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/route_probe.rs` — `hidden_flags` | `Tailscale route probe; Existing creation flags: command.creation_flags(crate::hidden_process::hidden_flags(0x0800_0000)); // CREATE_NO_WINDOW` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/tailscale.rs` — `hidden_command` | `Standard child: assert!(crate::hidden_process::hidden_command("rustc")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/tailscale.rs` — `hidden_tokio_command` | `Tokio child: let mut command = crate::hidden_process::hidden_tokio_command(&exe);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/tailscale.rs` — `hidden_tokio_command` | `Tokio child: let mut command = crate::hidden_process::hidden_tokio_command(&exe);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/tailscale.rs` — `hidden_tokio_command` | `Tokio child: query_status(crate::hidden_process::hidden_tokio_command(` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows/tests.rs` — `hidden_command` | `Standard child: crate::hidden_process::hidden_command(std::env::current_exe().unwrap())` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows/tests.rs` — `hidden_command` | `Standard child: crate::hidden_process::hidden_command(std::env::current_exe().unwrap())` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_bootstrap.rs` — `hidden_tokio_command` | `SSH bootstrap transport; Tokio child: let mut child = crate::hidden_process::hidden_tokio_command(executable)` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_bootstrap.rs` — `hidden_flags` | `SSH bootstrap transport; Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(0x0800_0000))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_creation/windows_tests.rs` — `hidden_command` | `Standard child: crate::hidden_process::hidden_command("cmd.exe")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_creation/writer_windows_tests.rs` — `hidden_command` | `Standard child: crate::hidden_process::hidden_command("icacls.exe")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_creation/writer_windows_tests.rs` — `hidden_command` | `Standard child: crate::hidden_process::hidden_command("cmd.exe")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_detach.rs` — `hidden_flags` | `Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(flags))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_migration/native.rs` — `hidden_command` | `legacy PowerShell Fleet migration monitor; Standard child: let mut command = crate::hidden_process::hidden_command(&legacy.powershell);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_migration/native.rs` — `hidden_flags` | `legacy PowerShell Fleet migration monitor; Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(CREATE_NO_WINDOW))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_monitor_launch.rs` — `hidden_command` | `Fleet monitor Dashboard supervisor; Standard child: let mut command = crate::hidden_process::hidden_command(self.native.executable());` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_monitor_launch.rs` — `hidden_flags` | `Fleet monitor Dashboard supervisor; Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(CREATE_NO_WINDOW))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_profiles.rs` — `hidden_command` | `Better Agent Terminal profile; Standard child: let mut command = crate::hidden_process::hidden_command(executable);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_profiles.rs` — `hidden_flags` | `Better Agent Terminal profile; Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(CREATE_NO_WINDOW))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_tailscale.rs` — `hidden_tokio_command` | `Tailscale CLI status / vendor tray application; Tokio child: crate::hidden_process::hidden_tokio_command(self.directory.join("tailscale.exe"));` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_tailscale.rs` — `hidden_flags` | `Tailscale CLI status / vendor tray application; Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(0x0800_0000));` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_tailscale.rs` — `hidden_command` | `Tailscale CLI status / vendor tray application; Standard child: let child = crate::hidden_process::hidden_command(self.directory.join("tailscale-ipn.exe"))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_tailscale.rs` — `hidden_flags` | `Tailscale CLI status / vendor tray application; Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(0x0000_0400))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_tunnel.rs` — `tunnel_command` | `Fleet Monitor SSH tunnel: tunnel_command uses the shared hidden constructor` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_tunnel.rs` — `tunnel_command` | `Fleet Monitor SSH tunnel: tunnel_command uses the shared hidden constructor` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/src/windows_tunnel.rs` — `spawn_boundary_retains_handle_and_exact_arguments_for_rollback` | `Tunnel launch fixture: spawn_boundary_retains_handle_and_exact_arguments_for_rollback` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/tests/publication_state.rs` — `hidden_command` | `Standard child: let output = bat_fleet_core::hidden_process::hidden_command("cmd.exe")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/tests/route_native.rs` — `hidden_command` | `Standard child: let child = bat_fleet_core::hidden_process::hidden_command(env!("CARGO"))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/tests/support/powershell.rs` — `hidden_command` | `Standard child: let mut command = bat_fleet_core::hidden_process::hidden_command(executable);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/tests/support/powershell_tests.rs` — `hidden_command` | `Standard child: let mut command = bat_fleet_core::hidden_process::hidden_command("python3");` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/tests/support/powershell_tests.rs` — `hidden_command` | `Standard child: child: bat_fleet_core::hidden_process::hidden_command("python3")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/tests/support/powershell_tests.rs` — `hidden_command` | `Standard child: &mut bat_fleet_core::hidden_process::hidden_command(temp.0.join("missing")),` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/tests/support/powershell_tests.rs` — `hidden_command` | `Standard child: child: bat_fleet_core::hidden_process::hidden_command("python3")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/fleet-core/tests/support/powershell_tests.rs` — `hidden_command` | `Standard child: &mut bat_fleet_core::hidden_process::hidden_command(next.0.join("missing")),` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/scripts/check-browser-assets.mjs` — `execFileSync` | `execFileSync("git", ["diff", "--exit-code", "HEAD", "--", relative], {cwd: root, stdio: "inherit"});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/scripts/check-browser-assets.mjs` — `execFileSync` | `const untracked = execFileSync("git", ["ls-files", "--others", "--exclude-standard", "--", relative], {cwd: root, encoding: "utf8"});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/scripts/windows/install-fleet-monitor-task.ps1` — `New-ScheduledTaskAction` | `$action = New-ScheduledTaskAction -Execute $exe -Argument $arguments` | Hidden task setting; GUI Dashboard directly, known console actions via trusted system conhost.exe --headless. |
| `desktop/scripts/windows/install-fleet-monitor-task.ps1` — `New-ScheduledTaskSettingsSet` | `$settings = New-ScheduledTaskSettingsSet -Hidden -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -R` | Hidden task setting; GUI Dashboard directly, known console actions via trusted system conhost.exe --headless. |
| `desktop/scripts/windows/install-fleet-monitor-task.ps1` — `Register-ScheduledTask` | `Register-ScheduledTask -TaskName $name -InputObject $definition -Force` | Hidden task setting; GUI Dashboard directly, known console actions via trusted system conhost.exe --headless. |
| `desktop/src-tauri/src/bridge.rs` — `hidden_command` | `Standard child: bat_fleet_core::hidden_process::hidden_command(python)` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/src-tauri/src/credentials_macos.rs` — `hidden_command` | `Standard child: bat_fleet_core::hidden_process::hidden_command(std::env::current_exe().unwrap())` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/src-tauri/src/files.rs` — `hidden_command` | `Standard child: bat_fleet_core::hidden_process::hidden_command(python)` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/src-tauri/src/fleet-startup.ps1` (invoked by `adapter_command` in `desktop/src-tauri/src/fleet.rs`) | `& $env:BATC_FLEET_ADAPTER` | In-process script/scriptblock invocation; reviewed allowlist, no child creation. |
| `desktop/src-tauri/src/fleet.rs` — `hidden_tokio_command` | `Fleet PowerShell adapter / fixture Python; Tokio child: let mut command = bat_fleet_core::hidden_process::hidden_tokio_command(executable);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/src-tauri/src/fleet.rs` — `hidden_flags` | `Fleet PowerShell adapter / fixture Python; Existing creation flags: command.creation_flags(bat_fleet_core::hidden_process::hidden_flags(0x0800_0000)); // CREATE_NO_WINDOW; monitor bootstrap ` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/src-tauri/src/fleet.rs` — `hidden_flags` | `Fleet PowerShell adapter / fixture Python; Existing creation flags: command.creation_flags(bat_fleet_core::hidden_process::hidden_flags(0));` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/src-tauri/src/fleet.rs` — `hidden_tokio_command` | `Fleet PowerShell adapter / fixture Python; Tokio child: let mut c = bat_fleet_core::hidden_process::hidden_tokio_command("python3");` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/src-tauri/src/managed.rs` — `hidden_command` | `bundled Python Connector runtime; Standard child: let mut command = bat_fleet_core::hidden_process::hidden_command(&executable);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/src-tauri/src/managed.rs` — `hidden_flags` | `bundled Python Connector runtime; Existing creation flags: command.creation_flags(bat_fleet_core::hidden_process::hidden_flags(0x08000000));` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/src-tauri/src/managed.rs` — `hidden_command` | `bundled Python Connector runtime; Standard child: let mut command = bat_fleet_core::hidden_process::hidden_command(executable);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/src-tauri/tests/source_admission_unicode.rs` — `hidden_command` | `Standard child: let compiler = bat_fleet_core::hidden_process::hidden_command("rustc")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/src-tauri/tests/source_admission_unicode.rs` — `hidden_command` | `Standard child: let changed = bat_fleet_core::hidden_process::hidden_command("chown")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/src-tauri/tests/source_admission_unicode.rs` — `hidden_command` | `Standard child: let result = bat_fleet_core::hidden_process::hidden_command(python).args(["-c", "from pathlib import Path; import sys; from bat_agent_connector.connection_sources import read` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| `desktop/tests/action-labels.test.ts` — `execFileSync` | `const output = execFileSync('python3', [resolve(here, 'scan-actions.py'), resolve(root, 'src/bat_agent_connector')], {encoding: 'utf8'});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/approvals-integration.mjs` — `spawn` | `const child = spawn(python, [resolve("tests/approvals-fixture.py")], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/artifact-integration.mjs` — `spawn` | `const child = spawn(python, [resolve("tests/artifact-fixture.py")], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/artifact-review-integration.mjs` — `spawn` | `const child=spawn(python,[resolve('tests/artifact-review-fixture.py'),...(process.argv.includes('--start')?['--start']:[])],{cwd:backend,env:{...process.env,PYTHONPATH:[resolve(backend,'src'` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/attention-integration.mjs` — `spawn` | `const child = spawn(python, [resolve('tests/attention-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/browser-auth-service.ts` — `spawn` | `const child = spawn(process.env.BATC_AUTH_PYTHON, ['tests/browser-auth-fixture.py'], {stdio: ['pipe', 'pipe', 'inherit']});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/capture-integration.mjs` — `spawn` | `const child = spawn(python, [resolve("tests/capture-fixture.py")], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/central-integration.mjs` — `spawn` | `const child = spawn(python, [resolve("tests/central-fixture.py")], {cwd: resolve(".."),` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/cleanup-integration.mjs` — `spawn` | `const child = spawn(python, [resolve("tests/cleanup-fixture.py")], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/delivery-integration.mjs` — `spawn` | `child = spawn(python, [path.join(backend, 'tests/manual/delivery_dashboard_fixture.py')], {` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/hidden-child-process.test.ts` — `execFileSync` | `assert.equal(execFileSync(process.execPath, args, {...options, stdio: ['ignore', 'pipe', 'ignore']}), args[2]);` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/hidden-child-process.test.ts` — `spawnSync` | `const result = spawnSync(process.execPath, args, options);` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/hidden-child-process.test.ts` — `execFile` | `const pending = promisify(execFile)(process.execPath, args, options);` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/hidden-child-process.test.ts` — `execFile` | `await new Promise((resolve, reject) => execFile(process.execPath, args, (error, stdout, stderr) => {` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/hidden-child-process.test.ts` — `spawn` | `const child = spawn(process.execPath, ['-e', 'process.exit(0)']);` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/loaded-ui-identity.spec.ts` — `execFile` | `const run = promisify(execFile);` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/loaded-ui-identity.spec.ts` — `run` | `await run(process.execPath, [join(desktop, 'node_modules/vite/bin/vite.js'), 'build', '--mode', 'browser', '--outDir', output],` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/macos-installed-smoke.mjs` — `execFileSync` | `return execFileSync(command, args, { encoding: "utf8", timeout: 60_000 });` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/macos-installed-smoke.mjs` — `spawn` | `const child = spawn(binary, [], { env: { ...process.env, BATC_DESKTOP_TOKEN: "fixture-native-token" },` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/macos-keychain.mjs` — `execFileSync` | `const security = args => execFileSync("/usr/bin/security", args, { encoding: "utf8", timeout: 15_000 }).trim();` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/macos-keychain.mjs` — `execFileSync` | `execFileSync("cargo", ["test", "--locked", "--manifest-path", "src-tauri/Cargo.toml", "--bin", "better-agent-dashboard",` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/managed-browser-integration.mjs` — `spawnSync` | `const result = spawnSync(python, ["-m", "bat_agent_connector.managed_runtime", command, "--data-dir", directory],` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/managed-installed-probe.mjs` — `execFileSync` | `const reply = execFileSync(executable, ['stop', '--data-dir', data], {` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/managed-setup-browser-integration.mjs` — `spawnSync` | `const result = spawnSync(executable, [command, "--data-dir", directory], {encoding: "utf8", timeout: 60000,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/managed-setup-browser-integration.mjs` — `spawn` | `mock = spawn(python, [resolve("tests/managed-setup-mock.py")], {cwd: resolve(".."),` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/native-files-integration.mjs` — `spawn` | `const child = spawn("cargo", ["test", "--manifest-path", "src-tauri/Cargo.toml", "native_transfer_real_central", "--", "--ignored", "--nocapture"],` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/native-smoke.mjs` — `spawn` | `app = spawn(binary, [], {env, stdio: ["ignore", "pipe", "pipe"]});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/native-smoke.mjs` — `execFileSync` | `const window = execFileSync("xdotool", ["search", "--onlyvisible", "--name", "^Better Agent Dashboard$"]).toString().trim();` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/native-smoke.mjs` — `execFileSync` | `execFileSync("python3", ["-c", "from PIL import ImageGrab; import sys; ImageGrab.grab().save(sys.argv[1])", join(evidence, "native.png")], {env});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/native-smoke.mjs` — `execFileSync` | `execFileSync("python3", ["-c", ` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/native-smoke.mjs` — `execFileSync` | `assert.match(execFileSync("xwininfo", ["-id", window]).toString(), /Map State: IsUnMapped/);` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/native-smoke.mjs` — `spawn` | `const second = spawn(binary, [], {env, stdio: "ignore"});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/native-smoke.mjs` — `execFileSync` | `const windows = execFileSync("xdotool", ["search", "--onlyvisible", "--name", "^Better Agent Dashboard$"]).toString().trim();` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/observation-integration.mjs` — `spawn` | `const child = spawn(python, [resolve("tests/observation-fixture.py")], {cwd: root,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/orchestration-integration.mjs` — `spawn` | `const child = spawn(python, [resolve('tests/orchestration-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/permissions-integration.mjs` — `spawn` | `const child = spawn(python, [resolve("tests/permissions-fixture.py")], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/pm-central-integration.mjs` — `spawn` | `child = spawn(python, [resolve('tests/pm-central-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/pm-walkthrough.mjs` — `spawn` | `child = spawn(python, [resolve('tests/pm-walkthrough-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/project-dispatch-integration.mjs` — `spawn` | `const child = spawn(python, [resolve('tests/repository-start-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/prototype-v1-3.spec.ts` — `execFile` | `await promisify(execFile)('npm', ['run', 'build:prototype'], {cwd: new URL('..', import.meta.url)});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/prototype-v1-3.spec.ts` — `run` | `await run({page, lang, width, all, capture});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/public-demo.spec.ts` — `execFile` | `await promisify(execFile)('npm', ['run', 'build:demo'], {cwd: new URL('..', import.meta.url)});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/repository-start-integration.mjs` — `spawn` | `const child = spawn(python, [resolve('tests/repository-start-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/session-labels-integration.mjs` — `spawn` | `const child=spawn(python,[resolve('tests/session-labels-fixture.py')],{cwd:backend,env:{...process.env,PYTHONPATH:[resolve(backend,'src'),backend].join(delimiter)},stdio:['pipe','pipe','inhe` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/session-start-integration.mjs` — `spawn` | `const child = spawn(python, [resolve('tests/session-start-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/task-cleanup-integration.mjs` — `spawn` | `const child = spawn(python, [resolve("tests/cleanup-fixture.py"), "--task"], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/task-controls-integration.mjs` — `spawn` | `const child=spawn(python,[resolve('tests/task-controls-fixture.py')],{cwd:backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/two-service-isolation.mjs` — `spawn` | `const child = spawn(python, [resolve('tests/two-service-fixture.py'), label], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/windows-installed-smoke.mjs` — `spawn` | `const child = spawn("pwsh", ["-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-File",` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `desktop/tests/windows-installed-smoke.ps1` — `Start-Process` | `$process = Start-Process -WindowStyle Hidden -FilePath $Path -ArgumentList $Arguments -PassThru` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-smoke.ps1` — `ProcessStartInfo` | `$info = [Diagnostics.ProcessStartInfo]::new($binary)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-smoke.ps1` — `Process` | `[Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-smoke.ps1` — `ProcessStartInfo` | `$info = [Diagnostics.ProcessStartInfo]::new((Join-Path $framework 'csc.exe'))` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-smoke.ps1` — `Process` | `$compiler = [Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-smoke.ps1` — `ProcessStartInfo` | `$info = [Diagnostics.ProcessStartInfo]::new($managedViewProbe)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-smoke.ps1` — `Process` | `$probe = [Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-smoke.ps1` — `ProcessStartInfo` | `$info = [Diagnostics.ProcessStartInfo]::new($managedViewProbe)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-smoke.ps1` — `Process` | `$probe = [Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-smoke.ps1` — `ProcessStartInfo` | `$info = [Diagnostics.ProcessStartInfo]::new($managedViewProbe)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-smoke.ps1` — `Process` | `$probe = [Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-support-test.ps1` — `New-FakeClock` | `& $clock.Advance 1` | In-process script/scriptblock invocation; reviewed allowlist, no child creation. |
| `desktop/tests/windows-installed-support-test.ps1` — `New-FakeClock` | `& $slowClock.Advance 850` | In-process script/scriptblock invocation; reviewed allowlist, no child creation. |
| `desktop/tests/windows-installed-support-test.ps1` — `New-FakeClock` | `& $floorClock.Advance 900` | In-process script/scriptblock invocation; reviewed allowlist, no child creation. |
| `desktop/tests/windows-installed-support-test.ps1` — `ProcessStartInfo` | `$info = [Diagnostics.ProcessStartInfo]::new((Join-Path $framework 'csc.exe'))` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-support-test.ps1` — `Process` | `$compiler = [Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-support-test.ps1` — `ProcessStartInfo` | `$info = [Diagnostics.ProcessStartInfo]::new($testProbe)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/windows-installed-support-test.ps1` — `Process` | `$probe = [Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| `desktop/tests/workbench-mode.test.ts` — `execFileSync` | `const result = JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e', ` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `scripts/capture_site.mjs` — `execFileSync` | `const git = (...args) => execFileSync('git', args, {cwd: root, encoding: 'utf8'}).trim();` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| `scripts/hidden-child-process.mjs` — `execFile` | `childProcess[name](...args): spawn, exec, execFile, fork and synchronous variants` | Single Node dispatch sets windowsHide=true after caller options. |
| `src/bat_agent_connector/artifact_capture_helper.py` — `head` | `subprocess.run(  # noqa: S603 - fixed read-only verbs, centrally observed root` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| `src/bat_agent_connector/artifact_host.py` — `call` | `hidden_process.create_subprocess_exec(*self.argv(host), stdin=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/artifact_host.py` — `capture` | `hidden_process.create_subprocess_exec(*self.capture_argv(host), stdin=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/artifact_host_helper.py` — `git` | `subprocess.run(  # noqa: S603,S607 - fixed git verbs, validated managed cwd` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| `src/bat_agent_connector/artifact_host_helper.py` — `execute` | `subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=20, check=True)` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| `src/bat_agent_connector/board_github_provider.py` — `access_token` | `hidden_process.run([executable, 'auth', 'token', '--hostname', 'github.com'],  # noqa: S603 - resolved gh, fixed argv` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/checkpoints.py` — `_run` | `hidden_process.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/checkpoints.py` — `_run_locked` | `hidden_process.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/cleanup_host.py` — `git` | `subprocess.run(  # noqa: S603, S607 - fixed commands, argv only` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| `src/bat_agent_connector/detached_process.py` — `spawn` | `hidden_process.popen(command, **options)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/detached_process.py` — `spawn` | `hidden_process.popen(command, **options)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/fleet_bootstrap.py` — `_call` | `hidden_process.run([SYSTEMCTL, "--user", "--no-pager", *arguments],  # noqa: S603` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/goose_acp.py` — `check_pinned_version` | `hidden_process.create_subprocess_exec(self.config.command[0], "--version",` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/goose_acp.py` — `_run_process` | `hidden_process.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/hidden_process.py` — `run` | `subprocess.run(*args, **hidden_options(kwargs))` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/hidden_process.py` — `popen` | `subprocess.Popen(*args, **hidden_options(kwargs))` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/hidden_process.py` — `create_subprocess_exec` | `asyncio.create_subprocess_exec(*args, **hidden_options(kwargs))` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/hidden_process.py` — `create_subprocess_shell` | `asyncio.create_subprocess_shell(*args, **hidden_options(kwargs))` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/managed_runtime.py` — `_spawn` | `detached_process.spawn(_service_command(root), windows=os.name == "nt", **options)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/managed_runtime.py` — `_ensure_locked` | `_spawn(root)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/managed_runtime.py` — `_ensure_locked` | `_spawn(root)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/managed_setup.py` — `_probe_ssh` | `hidden_process.create_subprocess_exec("ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/native_isolation.py` — `probe` | `_spawn(*command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **SPAWN)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/native_isolation.py` — `run` | `_spawn(*plan.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/native_process.py` — `_spawn` | `hidden_process.create_subprocess_exec(*args, **kwargs)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/pm_native_runtime.py` — `_runtime_version` | `_spawn(executable, "--version", stdout=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/pm_native_runtime.py` — `_launch` | `_spawn(*command, cwd=self.workspace, env=environment,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/pm_revision_host.py` — `rev_git` | `subprocess.run(["git", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",  # noqa: S603,S607` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| `src/bat_agent_connector/pm_spec_sources_host.py` — `spec_git` | `subprocess.run(["git", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",  # noqa: S603,S607` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| `src/bat_agent_connector/project_skills.py` — `scan` | `hidden_process.create_subprocess_exec(*self.argv(host), stdin=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/repository_sync_host.py` — `git` | `subprocess.run(["git", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",  # noqa: S603,S607` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| `src/bat_agent_connector/task_bat.py` — `_ssh_script` | `hidden_process.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/task_bat.py` — `_verify_original_archive` | `result = await asyncio.to_thread(hidden_process.run, command, capture_output=True,` | Shared Python hidden_process.run; no Linux option changes. |
| `src/bat_agent_connector/task_verifier.py` — `_run` | `hidden_process.create_subprocess_exec(*cmd, cwd=working_dir,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/task_verifier.py` — `run` | `hidden_process.create_subprocess_exec(*cmd, cwd=working_dir, **platform_args,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/task_verifier.py` — `_kill_tree` | `hidden_process.create_subprocess_exec(*_remote_kill_command(alias, marker),` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/update_install.py` — `run` | `hidden_process.popen(args, cwd=cwd, env=env, stdout=output, stderr=output,  # noqa: S603` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| `src/bat_agent_connector/worktree_merge_host.py` — `git` | `subprocess.Popen(  # noqa: S603 - fixed argv, no shell` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| `desktop/src-tauri/src/main.rs` — `managed_control` | System browser / managed browser HTML through Tauri opener | Already windowless in locked open 5.4.4 Windows constructors; preserve GUI browser launch. |
| `desktop/src-tauri/src/main.rs` — `open_managed_browser` | System browser / managed browser HTML through Tauri opener | Already windowless in locked open 5.4.4 Windows constructors; preserve GUI browser launch. |
| `desktop/src-tauri/src/main.rs` — `open_external` | System browser / managed browser HTML through Tauri opener | Already windowless in locked open 5.4.4 Windows constructors; preserve GUI browser launch. |
| `desktop/src-tauri/src/updates.rs` — `check` | Desktop updater check | HTTP only; no subprocess or console. |
| `desktop/src-tauri/src/updates.rs` — `install` | Desktop update installer | Locked tauri-plugin-updater launches the verified GUI installer using ShellExecuteW; preserve installer UI. |
| `scripts/check-hidden-spawns.py` — `main` | Git source-file listing for the CI guard | CREATE_NO_WINDOW on Windows; NUL-separated UTF-8 paths preserve Unicode and spaces. |
| `desktop/fleet-core/src/windows_startup.rs` — `render` | Fleet login shortcut (Dashboard supervisor, or legacy wscript/Open BAT.vbs) | Release Dashboard is GUI-subsystem; legacy login already uses wscript/VBS instead of a console host. Preserve ownership and shortcut matching. |
| `desktop/src-tauri/src/managed_login.rs` — `expected` | Managed Connector per-user Run login entry | Targets the release Dashboard GUI executable with --managed-login; its child runtime uses the shared hidden constructor. |

## Windows field follow-up

The sole maintained registration path is
`desktop/scripts/windows/install-fleet-monitor-task.ps1`. It builds a task definition
with `Settings.Hidden=true` and force-registers that definition, including replacement
of an existing task. `-DefinitionOnly` returns the same definition without registering
it, for the Windows definition test. There are no Rust/TypeScript task XML generators
or `schtasks` registration calls in this repository. Interactive logon remains intentional:
Hidden is independent of the principal's logon type.

Previously, upgrading the desktop did not repair a task installed before the hidden
setting was added. The NSIS postinstall hook now runs
`repair-fleet-monitor-task.ps1` during both ordinary installs and updater installs.
It changes only Hidden on an existing root Fleet Monitor task whose principal is the
current user and whose single action targets this installation's Dashboard executable
with `--fleet-supervisor`. It preserves arguments, triggers and other settings, creates
no task when absent and leaves foreign tasks alone. PowerShell runs through NSIS's
windowless nsExec plugin with `-NoProfile -NonInteractive -WindowStyle Hidden`.
The hook logs a warning through `DetailPrint` for a nonzero repair result, including
the exit code and captured output. Repair failure never aborts the install or sets
an installer error level; ordinary installs and updater installs continue.

Initial SSH tunnels and reconnect/respawn attempts already shared
`WindowsEffects::launch -> tunnel::launch -> WindowsPlatform::spawn`. Their command
construction is now factored into `tunnel_command`, retaining the shared
`hidden_process::hidden_command` and its CREATE_NO_WINDOW flag for every attempt.
A Windows-only test executes the production command builder with a child that checks
GetConsoleWindow; the existing supervisor recovery fixture exercises capped retries.
There is no separate unhidden reconnect constructor to repair.

**Sidecar lifetime remains outside this repository.** There is no
`BAT_SIDECAR_ENTRY_PATH` launcher or Node sidecar runtime here. That variable belongs
to Better Agent Terminal's `src-tauri/crates/bat-agent-bridge/src/lib.rs`.
The inspected local BAT source uses `bat_host_support::subprocess::hide_console_window`
for Node, but its `SidecarHandle` has no Drop implementation despite a comment claiming
that retaining `Child` kills the process on drop. Rust's standard Child does not do that.
The requested Windows Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE and explicit
normal-shutdown kill must be implemented and tested in BAT; this Connector commit
does not claim to fix the orphan. It must leave non-Windows behavior unchanged.

Follow-up tests cover the real generated task settings without live registration,
upgrade repair through mocked task reads/writes, installer hook packaging and the
shared tunnel command. Task Scheduler and child-console tests require Windows;
Linux results do not establish native or physical-host acceptance.

Follow-up Linux validation: Fleet-core `cargo test` passed, including the supervisor
recovery fixture; focused Python checks passed 17 tests with two Windows-only tests
skipped; the desktop fixture-input coverage check also passed. Formatting, focused
Ruff and `scripts/check-hidden-spawns.py` passed.
No full Python suite was run for this follow-up, as requested. The NSIS hook and
Windows-only tests require a Windows machine. No person has live-tested this follow-up on
Windows; this record does not establish installation.

## Validation and delivery status

This is E03/E11 launch-reliability maintenance. It does not close the remaining
E00–E11 or v1.2 work, or claim R0–R5 / A01–A40 acceptance. The changes were merged
into main by `dfefdbfc` (PR #490); the first release tag containing that merge is `v0.2.14`.
Merge and release history do not establish installation or a person's live test on Windows.

Linux validation: Ruff passed; Node 573 passed; both frontend builds/typecheck and
browser asset consistency passed. Fleet-core `cargo check`, formatting and Clippy
with warnings denied passed; its full test run passed 616 tests with 4 ignored.
The guard's 16 focused tests and a CLI smoke check for Unicode/space filenames and
allowlisting passed. The repository-pinned, checksum-verified Gitleaks 8.30.1 scan
of the complete change found no leaks; public-content checking passed.

Python 3.13 executed all 9,835 collected tests across five disjoint CI shards:
9,744 passed, 82 skipped, 9 failed. The failures were obsolete detachment/archive
verifier mock references and shifted setup source citations. The mocks now use the
shared wrapper, and consolidating the setup import preserves the original citations.
All nine affected cases pass in focused reruns totaling 90 passing tests. The original
full run remains recorded as failed; it is not represented as a clean full rerun.

Python 3.10 completed the full final suite: **9,753 passed, 82 skipped, 0 failed**.
The completeness verifier confirmed all 9,835 tests in 424 files across five
disjoint shards, without missing or duplicated tests.

The first Rust test attempts used default RAM-backed `/tmp` and failed metadata
access-time comparisons. The complete passing rerun used task-owned disk scratch,
which was removed afterward. Python runs use unique disk basetemps and clean them on
exit. Full Python versions run sequentially, using the existing five-shard plugin
within each version.

Tauri `cargo check` stopped in `glib-sys` because this host lacks `pkg-config`.
The attempted Windows cross-check also lacks `x86_64-w64-mingw32-gcc` for `ring`.
The Windows-only Rust child-console test is added for Windows CI; Linux fixtures do
not establish physical Windows visual acceptance. Intended GUI windows and installer
UI remain visible.

The legacy kit scripts referenced by configured paths (including Open BAT.vbs and
fleet-desktop.ps1) are external files, not repository source. Their bodies are not
modified; this change hides the repository-owned PowerShell launchers.
