# Windows hidden process inventory

This inventory covers repository-owned Rust constructors and flag overrides, Node
child_process calls (including tests/build tooling), PowerShell process APIs and
scheduled tasks, plus Connector Python launch sites. Lines refer to this change.
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

| File:line | What it launches / launch expression | How it was fixed |
| --- | --- | --- |
| desktop/fleet-core/src/connection_participant/protected/authority_tests.rs:499 | `Standard child: let output = crate::hidden_process::hidden_command("cmd.exe")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/connection_participant/protected/publish_tests.rs:298 | `Standard child: let mut child = crate::hidden_process::hidden_command(std::env::current_exe().unwrap())` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/hidden_process.rs:13 | `Standard child: let mut command = Command::new(program);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/hidden_process.rs:17 | `Existing creation flags: command.creation_flags(hidden_flags(0));` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/hidden_process.rs:23 | `Tokio child: tokio::process::Command::from(hidden_command(program))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/hidden_process.rs:36 | `Standard child: let mut command = hidden_command("example");` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/hidden_process.rs:41 | `Tokio child: hidden_tokio_command("example").as_std().get_program(),` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/hidden_process.rs:48 | `Standard child: let result = hidden_command("powershell.exe")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/route_probe.rs:88 | `Tailscale route probe; Tokio child: let mut command = crate::hidden_process::hidden_tokio_command(path);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/route_probe.rs:96 | `Tailscale route probe; Existing creation flags: command.creation_flags(crate::hidden_process::hidden_flags(0x0800_0000)); // CREATE_NO_WINDOW` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/tailscale.rs:262 | `Standard child: assert!(crate::hidden_process::hidden_command("rustc")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/tailscale.rs:276 | `Tokio child: let mut command = crate::hidden_process::hidden_tokio_command(&exe);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/tailscale.rs:293 | `Tokio child: let mut command = crate::hidden_process::hidden_tokio_command(&exe);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/tailscale.rs:297 | `Tokio child: query_status(crate::hidden_process::hidden_tokio_command(` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows/tests.rs:13 | `Standard child: crate::hidden_process::hidden_command(std::env::current_exe().unwrap())` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows/tests.rs:139 | `Standard child: crate::hidden_process::hidden_command(std::env::current_exe().unwrap())` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_bootstrap.rs:85 | `SSH bootstrap transport; Tokio child: let mut child = crate::hidden_process::hidden_tokio_command(executable)` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_bootstrap.rs:89 | `SSH bootstrap transport; Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(0x0800_0000))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_creation/windows_tests.rs:115 | `Standard child: crate::hidden_process::hidden_command("cmd.exe")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_creation/writer_windows_tests.rs:225 | `Standard child: crate::hidden_process::hidden_command("icacls.exe")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_creation/writer_windows_tests.rs:254 | `Standard child: crate::hidden_process::hidden_command("cmd.exe")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_detach.rs:73 | `Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(flags))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_migration/native.rs:222 | `legacy PowerShell Fleet migration monitor; Standard child: let mut command = crate::hidden_process::hidden_command(&legacy.powershell);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_migration/native.rs:233 | `legacy PowerShell Fleet migration monitor; Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(CREATE_NO_WINDOW))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_monitor_launch.rs:89 | `Fleet monitor Dashboard supervisor; Standard child: let mut command = crate::hidden_process::hidden_command(self.native.executable());` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_monitor_launch.rs:96 | `Fleet monitor Dashboard supervisor; Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(CREATE_NO_WINDOW))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_profiles.rs:150 | `Better Agent Terminal profile; Standard child: let mut command = crate::hidden_process::hidden_command(executable);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_profiles.rs:156 | `Better Agent Terminal profile; Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(CREATE_NO_WINDOW))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_tailscale.rs:108 | `Tailscale CLI status / vendor tray application; Tokio child: crate::hidden_process::hidden_tokio_command(self.directory.join("tailscale.exe"));` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_tailscale.rs:118 | `Tailscale CLI status / vendor tray application; Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(0x0800_0000));` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_tailscale.rs:127 | `Tailscale CLI status / vendor tray application; Standard child: let child = crate::hidden_process::hidden_command(self.directory.join("tailscale-ipn.exe"))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_tailscale.rs:134 | `Tailscale CLI status / vendor tray application; Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(0x0000_0400))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_tunnel.rs:140 | `Fleet Monitor SSH tunnel; Standard child: crate::hidden_process::hidden_command(&self.executable)` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_tunnel.rs:142 | `Fleet Monitor SSH tunnel; Existing creation flags: .creation_flags(crate::hidden_process::hidden_flags(CREATE_NO_WINDOW))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/src/windows_tunnel.rs:254 | `Fleet Monitor SSH tunnel; Standard child: crate::hidden_process::hidden_command(std::env::current_exe().unwrap())` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/tests/publication_state.rs:386 | `Standard child: let output = bat_fleet_core::hidden_process::hidden_command("cmd.exe")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/tests/route_native.rs:48 | `Standard child: let child = bat_fleet_core::hidden_process::hidden_command(env!("CARGO"))` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/tests/support/powershell.rs:53 | `Standard child: let mut command = bat_fleet_core::hidden_process::hidden_command(executable);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/tests/support/powershell_tests.rs:15 | `Standard child: let mut command = bat_fleet_core::hidden_process::hidden_command("python3");` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/tests/support/powershell_tests.rs:83 | `Standard child: child: bat_fleet_core::hidden_process::hidden_command("python3")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/tests/support/powershell_tests.rs:170 | `Standard child: &mut bat_fleet_core::hidden_process::hidden_command(temp.0.join("missing")),` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/tests/support/powershell_tests.rs:263 | `Standard child: child: bat_fleet_core::hidden_process::hidden_command("python3")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/fleet-core/tests/support/powershell_tests.rs:318 | `Standard child: &mut bat_fleet_core::hidden_process::hidden_command(next.0.join("missing")),` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/scripts/check-browser-assets.mjs:10 | `execFileSync("git", ["diff", "--exit-code", "HEAD", "--", relative], {cwd: root, stdio: "inherit"});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/scripts/check-browser-assets.mjs:11 | `const untracked = execFileSync("git", ["ls-files", "--others", "--exclude-standard", "--", relative], {cwd: root, encoding: "utf8"});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/scripts/windows/install-fleet-monitor-task.ps1:27 | `$action = New-ScheduledTaskAction -Execute $exe -Argument $arguments` | Hidden task setting; GUI Dashboard directly, known console actions via trusted system conhost.exe --headless. |
| desktop/scripts/windows/install-fleet-monitor-task.ps1:31 | `$settings = New-ScheduledTaskSettingsSet -Hidden -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -R` | Hidden task setting; GUI Dashboard directly, known console actions via trusted system conhost.exe --headless. |
| desktop/scripts/windows/install-fleet-monitor-task.ps1:32 | `Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force` | Hidden task setting; GUI Dashboard directly, known console actions via trusted system conhost.exe --headless. |
| desktop/src-tauri/src/bridge.rs:5918 | `Standard child: bat_fleet_core::hidden_process::hidden_command(python)` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/src-tauri/src/credentials_macos.rs:214 | `Standard child: bat_fleet_core::hidden_process::hidden_command(std::env::current_exe().unwrap())` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/src-tauri/src/files.rs:2020 | `Standard child: bat_fleet_core::hidden_process::hidden_command(python)` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/src-tauri/src/fleet-startup.ps1:16 | `& $env:BATC_FLEET_ADAPTER` | In-process script/scriptblock invocation; reviewed allowlist, no child creation. |
| desktop/src-tauri/src/fleet.rs:350 | `Fleet PowerShell adapter / fixture Python; Tokio child: let mut command = bat_fleet_core::hidden_process::hidden_tokio_command(executable);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/src-tauri/src/fleet.rs:421 | `Fleet PowerShell adapter / fixture Python; Existing creation flags: command.creation_flags(bat_fleet_core::hidden_process::hidden_flags(0x0800_0000)); // CREATE_NO_WINDOW; monitor bootstrap ` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/src-tauri/src/fleet.rs:1415 | `Fleet PowerShell adapter / fixture Python; Existing creation flags: command.creation_flags(bat_fleet_core::hidden_process::hidden_flags(0));` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/src-tauri/src/fleet.rs:1462 | `Fleet PowerShell adapter / fixture Python; Tokio child: let mut c = bat_fleet_core::hidden_process::hidden_tokio_command("python3");` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/src-tauri/src/managed.rs:550 | `bundled Python Connector runtime; Standard child: let mut command = bat_fleet_core::hidden_process::hidden_command(&executable);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/src-tauri/src/managed.rs:560 | `bundled Python Connector runtime; Existing creation flags: command.creation_flags(bat_fleet_core::hidden_process::hidden_flags(0x08000000));` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/src-tauri/src/managed.rs:1146 | `bundled Python Connector runtime; Standard child: let mut command = bat_fleet_core::hidden_process::hidden_command(executable);` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/src-tauri/tests/source_admission_unicode.rs:6 | `Standard child: let compiler = bat_fleet_core::hidden_process::hidden_command("rustc")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/src-tauri/tests/source_admission_unicode.rs:78 | `Standard child: let changed = bat_fleet_core::hidden_process::hidden_command("chown")` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/src-tauri/tests/source_admission_unicode.rs:113 | `Standard child: let result = bat_fleet_core::hidden_process::hidden_command(python).args(["-c", "from pathlib import Path; import sys; from bat_agent_connector.connection_sources import read` | Shared constructor applies CREATE_NO_WINDOW; flag updates OR existing bits through hidden_flags. |
| desktop/tests/action-labels.test.ts:13 | `const output = execFileSync('python3', [resolve(here, 'scan-actions.py'), resolve(root, 'src/bat_agent_connector')], {encoding: 'utf8'});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/approvals-integration.mjs:12 | `const child = spawn(python, [resolve("tests/approvals-fixture.py")], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/artifact-integration.mjs:13 | `const child = spawn(python, [resolve("tests/artifact-fixture.py")], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/artifact-review-integration.mjs:14 | `const child=spawn(python,[resolve('tests/artifact-review-fixture.py'),...(process.argv.includes('--start')?['--start']:[])],{cwd:backend,env:{...process.env,PYTHONPATH:[resolve(backend,'src'` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/attention-integration.mjs:12 | `const child = spawn(python, [resolve('tests/attention-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/browser-auth-service.ts:8 | `const child = spawn(process.env.BATC_AUTH_PYTHON, ['tests/browser-auth-fixture.py'], {stdio: ['pipe', 'pipe', 'inherit']});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/capture-integration.mjs:14 | `const child = spawn(python, [resolve("tests/capture-fixture.py")], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/central-integration.mjs:12 | `const child = spawn(python, [resolve("tests/central-fixture.py")], {cwd: resolve(".."),` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/cleanup-integration.mjs:14 | `const child = spawn(python, [resolve("tests/cleanup-fixture.py")], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/delivery-integration.mjs:24 | `child = spawn(python, [path.join(backend, 'tests/manual/delivery_dashboard_fixture.py')], {` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/hidden-child-process.test.ts:9 | `assert.equal(execFileSync(process.execPath, args, {...options, stdio: ['ignore', 'pipe', 'ignore']}), args[2]);` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/hidden-child-process.test.ts:10 | `const result = spawnSync(process.execPath, args, options);` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/hidden-child-process.test.ts:15 | `const pending = promisify(execFile)(process.execPath, args, options);` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/hidden-child-process.test.ts:18 | `await new Promise((resolve, reject) => execFile(process.execPath, args, (error, stdout, stderr) => {` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/hidden-child-process.test.ts:22 | `const child = spawn(process.execPath, ['-e', 'process.exit(0)']);` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/loaded-ui-identity.spec.ts:93 | `const run = promisify(execFile);` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/loaded-ui-identity.spec.ts:96 | `await run(process.execPath, [join(desktop, 'node_modules/vite/bin/vite.js'), 'build', '--mode', 'browser', '--outDir', output],` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/macos-installed-smoke.mjs:62 | `return execFileSync(command, args, { encoding: "utf8", timeout: 60_000 });` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/macos-installed-smoke.mjs:106 | `const child = spawn(binary, [], { env: { ...process.env, BATC_DESKTOP_TOKEN: "fixture-native-token" },` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/macos-keychain.mjs:13 | `const security = args => execFileSync("/usr/bin/security", args, { encoding: "utf8", timeout: 15_000 }).trim();` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/macos-keychain.mjs:28 | `execFileSync("cargo", ["test", "--locked", "--manifest-path", "src-tauri/Cargo.toml", "--bin", "better-agent-dashboard",` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/managed-browser-integration.mjs:25 | `const result = spawnSync(python, ["-m", "bat_agent_connector.managed_runtime", command, "--data-dir", directory],` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/managed-installed-probe.mjs:126 | `const reply = execFileSync(executable, ['stop', '--data-dir', data], {` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/managed-setup-browser-integration.mjs:303 | `const result = spawnSync(executable, [command, "--data-dir", directory], {encoding: "utf8", timeout: 60000,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/managed-setup-browser-integration.mjs:342 | `mock = spawn(python, [resolve("tests/managed-setup-mock.py")], {cwd: resolve(".."),` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/native-files-integration.mjs:5 | `const child = spawn("cargo", ["test", "--manifest-path", "src-tauri/Cargo.toml", "native_transfer_real_central", "--", "--ignored", "--nocapture"],` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/native-smoke.mjs:41 | `app = spawn(binary, [], {env, stdio: ["ignore", "pipe", "pipe"]});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/native-smoke.mjs:50 | `const window = execFileSync("xdotool", ["search", "--onlyvisible", "--name", "^Better Agent Dashboard$"]).toString().trim();` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/native-smoke.mjs:53 | `execFileSync("python3", ["-c", "from PIL import ImageGrab; import sys; ImageGrab.grab().save(sys.argv[1])", join(evidence, "native.png")], {env});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/native-smoke.mjs:55 | `execFileSync("python3", ["-c", ` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/native-smoke.mjs:74 | `assert.match(execFileSync("xwininfo", ["-id", window]).toString(), /Map State: IsUnMapped/);` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/native-smoke.mjs:75 | `const second = spawn(binary, [], {env, stdio: "ignore"});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/native-smoke.mjs:79 | `const windows = execFileSync("xdotool", ["search", "--onlyvisible", "--name", "^Better Agent Dashboard$"]).toString().trim();` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/observation-integration.mjs:11 | `const child = spawn(python, [resolve("tests/observation-fixture.py")], {cwd: root,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/orchestration-integration.mjs:12 | `const child = spawn(python, [resolve('tests/orchestration-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/permissions-integration.mjs:13 | `const child = spawn(python, [resolve("tests/permissions-fixture.py")], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/pm-central-integration.mjs:36 | `child = spawn(python, [resolve('tests/pm-central-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/pm-walkthrough.mjs:64 | `child = spawn(python, [resolve('tests/pm-walkthrough-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/project-dispatch-integration.mjs:12 | `const child = spawn(python, [resolve('tests/repository-start-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/prototype-v1-3.spec.ts:108 | `await promisify(execFile)('npm', ['run', 'build:prototype'], {cwd: new URL('..', import.meta.url)});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/prototype-v1-3.spec.ts:177 | `await run({page, lang, width, all, capture});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/public-demo.spec.ts:10 | `await promisify(execFile)('npm', ['run', 'build:demo'], {cwd: new URL('..', import.meta.url)});` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/repository-start-integration.mjs:12 | `const child = spawn(python, [resolve('tests/repository-start-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/session-labels-integration.mjs:10 | `const child=spawn(python,[resolve('tests/session-labels-fixture.py')],{cwd:backend,env:{...process.env,PYTHONPATH:[resolve(backend,'src'),backend].join(delimiter)},stdio:['pipe','pipe','inhe` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/session-start-integration.mjs:12 | `const child = spawn(python, [resolve('tests/session-start-fixture.py')], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/task-cleanup-integration.mjs:14 | `const child = spawn(python, [resolve("tests/cleanup-fixture.py"), "--task"], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/task-controls-integration.mjs:12 | `const child=spawn(python,[resolve('tests/task-controls-fixture.py')],{cwd:backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/two-service-isolation.mjs:31 | `const child = spawn(python, [resolve('tests/two-service-fixture.py'), label], {cwd: backend,` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/windows-installed-smoke.mjs:46 | `const child = spawn("pwsh", ["-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-File",` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| desktop/tests/windows-installed-smoke.ps1:48 | `$process = Start-Process -WindowStyle Hidden -FilePath $Path -ArgumentList $Arguments -PassThru` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-smoke.ps1:56 | `$info = [Diagnostics.ProcessStartInfo]::new($binary)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-smoke.ps1:62 | `[Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-smoke.ps1:138 | `$info = [Diagnostics.ProcessStartInfo]::new((Join-Path $framework 'csc.exe'))` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-smoke.ps1:148 | `$compiler = [Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-smoke.ps1:176 | `$info = [Diagnostics.ProcessStartInfo]::new($managedViewProbe)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-smoke.ps1:185 | `$probe = [Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-smoke.ps1:223 | `$info = [Diagnostics.ProcessStartInfo]::new($managedViewProbe)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-smoke.ps1:231 | `$probe = [Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-smoke.ps1:247 | `$info = [Diagnostics.ProcessStartInfo]::new($managedViewProbe)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-smoke.ps1:253 | `$probe = [Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-support-test.ps1:109 | `& $clock.Advance 1` | In-process script/scriptblock invocation; reviewed allowlist, no child creation. |
| desktop/tests/windows-installed-support-test.ps1:144 | `& $slowClock.Advance 850` | In-process script/scriptblock invocation; reviewed allowlist, no child creation. |
| desktop/tests/windows-installed-support-test.ps1:168 | `& $floorClock.Advance 900` | In-process script/scriptblock invocation; reviewed allowlist, no child creation. |
| desktop/tests/windows-installed-support-test.ps1:378 | `$info = [Diagnostics.ProcessStartInfo]::new((Join-Path $framework 'csc.exe'))` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-support-test.ps1:388 | `$compiler = [Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-support-test.ps1:392 | `$info = [Diagnostics.ProcessStartInfo]::new($testProbe)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/windows-installed-support-test.ps1:395 | `$probe = [Diagnostics.Process]::Start($info)` | CreateNoWindow=true with UseShellExecute=false (or Start-Process -WindowStyle Hidden). |
| desktop/tests/workbench-mode.test.ts:42 | `const result = JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e', ` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| scripts/capture_site.mjs:24 | `const git = (...args) => execFileSync('git', args, {cwd: root, encoding: 'utf8'}).trim();` | Shared Node wrapper enforces windowsHide: true; argv, options, callbacks and promisified output retained. |
| scripts/hidden-child-process.mjs:12 | `childProcess[name](...args): spawn, exec, execFile, fork and synchronous variants` | Single Node dispatch sets windowsHide=true after caller options. |
| src/bat_agent_connector/artifact_capture_helper.py:61 | `subprocess.run(  # noqa: S603 - fixed read-only verbs, centrally observed root` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| src/bat_agent_connector/artifact_host.py:34 | `hidden_process.create_subprocess_exec(*self.argv(host), stdin=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/artifact_host.py:92 | `hidden_process.create_subprocess_exec(*self.capture_argv(host), stdin=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/artifact_host_helper.py:94 | `subprocess.run(  # noqa: S603,S607 - fixed git verbs, validated managed cwd` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| src/bat_agent_connector/artifact_host_helper.py:106 | `subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=20, check=True)` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| src/bat_agent_connector/board_github_provider.py:55 | `hidden_process.run([executable, 'auth', 'token', '--hostname', 'github.com'],  # noqa: S603 - resolved gh, fixed argv` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/checkpoints.py:101 | `hidden_process.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/checkpoints.py:117 | `hidden_process.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/cleanup_host.py:53 | `subprocess.run(  # noqa: S603, S607 - fixed commands, argv only` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| src/bat_agent_connector/detached_process.py:52 | `hidden_process.popen(command, **options)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/detached_process.py:59 | `hidden_process.popen(command, **options)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/fleet_bootstrap.py:173 | `hidden_process.run([SYSTEMCTL, "--user", "--no-pager", *arguments],  # noqa: S603` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/goose_acp.py:74 | `hidden_process.create_subprocess_exec(self.config.command[0], "--version",` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/goose_acp.py:165 | `hidden_process.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/hidden_process.py:17 | `subprocess.run(*args, **hidden_options(kwargs))` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/hidden_process.py:21 | `subprocess.Popen(*args, **hidden_options(kwargs))` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/hidden_process.py:25 | `asyncio.create_subprocess_exec(*args, **hidden_options(kwargs))` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/hidden_process.py:29 | `asyncio.create_subprocess_shell(*args, **hidden_options(kwargs))` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/managed_runtime.py:190 | `detached_process.spawn(_service_command(root), windows=os.name == "nt", **options)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/managed_runtime.py:366 | `_spawn(root)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/managed_runtime.py:384 | `_spawn(root)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/managed_setup.py:564 | `hidden_process.create_subprocess_exec("ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/native_isolation.py:176 | `_spawn(*command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **SPAWN)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/native_isolation.py:206 | `_spawn(*plan.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/native_process.py:55 | `hidden_process.create_subprocess_exec(*args, **kwargs)` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/pm_native_runtime.py:59 | `_spawn(executable, "--version", stdout=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/pm_native_runtime.py:392 | `_spawn(*command, cwd=self.workspace, env=environment,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/pm_revision_host.py:53 | `subprocess.run(["git", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",  # noqa: S603,S607` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| src/bat_agent_connector/pm_spec_sources_host.py:27 | `subprocess.run(["git", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",  # noqa: S603,S607` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| src/bat_agent_connector/project_skills.py:40 | `hidden_process.create_subprocess_exec(*self.argv(host), stdin=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/repository_sync_host.py:41 | `subprocess.run(["git", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",  # noqa: S603,S607` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| src/bat_agent_connector/task_bat.py:96 | `hidden_process.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/task_bat.py:856 | `result = await asyncio.to_thread(hidden_process.run, command, capture_output=True,` | Shared Python hidden_process.run; no Linux option changes. |
| src/bat_agent_connector/task_verifier.py:179 | `hidden_process.create_subprocess_exec(*cmd, cwd=working_dir,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/task_verifier.py:231 | `hidden_process.create_subprocess_exec(*cmd, cwd=working_dir, **platform_args,` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/task_verifier.py:279 | `hidden_process.create_subprocess_exec(*_remote_kill_command(alias, marker),` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/update_install.py:60 | `hidden_process.popen(args, cwd=cwd, env=env, stdout=output, stderr=output,  # noqa: S603` | Python hidden_process policy ORs CREATE_NO_WINDOW on Windows; existing suspension, group, breakaway, IO and lifetime options preserved. |
| src/bat_agent_connector/worktree_merge_host.py:26 | `subprocess.Popen(  # noqa: S603 - fixed argv, no shell` | POSIX remote program sent over SSH; package-free implementation preserved; reviewed allowlist. |
| desktop/src-tauri/src/main.rs:105 | System browser / managed browser HTML through Tauri opener | Already windowless in locked open 5.4.4 Windows constructors; preserve GUI browser launch. |
| desktop/src-tauri/src/main.rs:128 | System browser / managed browser HTML through Tauri opener | Already windowless in locked open 5.4.4 Windows constructors; preserve GUI browser launch. |
| desktop/src-tauri/src/main.rs:293 | System browser / managed browser HTML through Tauri opener | Already windowless in locked open 5.4.4 Windows constructors; preserve GUI browser launch. |
| desktop/src-tauri/src/updates.rs:304 | Desktop updater check | HTTP only; no subprocess or console. |
| desktop/src-tauri/src/updates.rs:480 | Desktop update installer | Locked tauri-plugin-updater launches the verified GUI installer using ShellExecuteW; preserve installer UI. |
| scripts/check-hidden-spawns.py:83 | Git source-file listing for the CI guard | CREATE_NO_WINDOW on Windows; NUL-separated UTF-8 paths preserve Unicode and spaces. |
| desktop/fleet-core/src/windows_startup.rs:234 | Fleet login shortcut (Dashboard supervisor, or legacy wscript/Open BAT.vbs) | Release Dashboard is GUI-subsystem; legacy login already uses wscript/VBS instead of a console host. Preserve ownership and shortcut matching. |
| desktop/src-tauri/src/managed_login.rs:17 | Managed Connector per-user Run login entry | Targets the release Dashboard GUI executable with --managed-login; its child runtime uses the shared hidden constructor. |

## Validation and delivery status

This is E03/E11 launch-reliability maintenance. It does not close the remaining
E00–E11 or v1.2 work, or claim R0–R5 / A01–A40 acceptance. Delivery is on
`fix/hide-windows-console-windows` only: not merged, installed or Windows live-tested.

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
