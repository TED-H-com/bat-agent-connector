// Real DMG -> installed .app -> WKWebView. Only disposable GitHub-hosted Macs.
import {testScratchRoot} from './test-scratch.mjs';
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { access, lstat, mkdtemp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { bundleManifest, sha256 } from "./macos-bundle.mjs";
import { probeManaged, stopManaged } from "./managed-installed-probe.mjs";

assert.equal(process.platform, "darwin", "macOS runner required");
assert.equal(process.env.GITHUB_ACTIONS, "true", "GitHub Actions required");
assert.equal(process.env.RUNNER_ENVIRONMENT, "github-hosted", "Disposable hosted runner required");
assert(process.env.RUNNER_TEMP, "Runner disk temporary directory required");
const evidence = resolve("test-results/macos-installed");
await mkdir(evidence, { recursive: true });
const id = "io.betteragent.dashboard";
const statePaths = [join(homedir(), "Library/Application Support", id),
  join(homedir(), "Library/Caches", id), join(homedir(), "Library/WebKit", id),
  join(homedir(), "Library/Preferences", `${id}.plist`),
  join(homedir(), "Library/Saved Application State", `${id}.savedState`),
  // Pinned single-instance plugin uses this fixed disk-backed Unix socket on macOS.
  // Refuse an existing socket and remove only the one this isolated fixture creates.
  "/tmp/io_betteragent_dashboard_si.sock"]; // tmpfs-path-allow: pinned product socket, not test scratch.
for (const path of [...statePaths, `/Applications/Better Agent Dashboard.app`,
  join(homedir(), "Applications/Better Agent Dashboard.app")]) {
  await assert.rejects(lstat(path), { code: "ENOENT" }, `Refusing pre-existing app/state: ${path}`);
}
const root = await mkdtemp(join(testScratchRoot(), "dashboard-macos-"));
const helper = join(root, "window-fixture");
const mount = join(root, "mounted");
const installed = join(root, "Installed Apps", "Better Agent Dashboard.app");
const binary = join(installed, "Contents/MacOS/better-agent-dashboard");
let mounted = false;
let ownsState = false;
let app;
let diagnostic = "";
const requests = [];
const violations = [];
const steps = [];
const caps = { actor: "fixture-operator", scopes: ["observe"], api_version: 1,
  contract_version: "2026-10-08", features: {}, actions: [], hosts: [] };
const checkpoint = { cursor: 0, token: "fixture-checkpoint" };
const server = createServer((req, res) => {
  const path = new URL(req.url, "http://127.0.0.1").pathname;
  requests.push({ method: req.method, path });
  res.setHeader("Content-Type", "application/json");
  if (req.method !== "GET" || req.headers.authorization !== "Bearer fixture-native-token"
      || !path.startsWith("/api/v1/")) {
    violations.push({ method: req.method, path });
    res.writeHead(403).end("{}");
    return;
  }
  res.end(JSON.stringify(path.endsWith("/capabilities") ? caps : path.endsWith("/bootstrap")
    ? { sync: { version: 1, server_id: "fixture-server", principal_id: "fixture-principal", checkpoint }, capabilities: caps }
    : path.endsWith("/events") ? { events: [], next_cursor: 0, head_cursor: 0, has_more: false, sync: { checkpoint } }
    : { sessions: [], work_items: [], operations: [], hosts: [] }));
});
function run(command, args) {
  return execFileSync(command, args, { encoding: "utf8", timeout: 60_000 });
}
function native(action) { return run(helper, [action, String(app.pid), installed, String(app.launchDate)]); }
function launch() { return JSON.parse(run(helper, ["launch", installed])); }
async function stopApplication() {
  if (!app || JSON.parse(native("inspect")).terminated) return;
  try { native("quit"); } catch (error) { diagnostic += String(error); }
  for (let count = 0; count < 50; count++) {
    if (JSON.parse(native("inspect")).terminated) return;
    await delay(100);
  }
  native("cleanup-force");
  await until(() => JSON.parse(native("inspect")).terminated, "Owned application did not terminate");
}
async function until(check, message) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (await check()) return;
    await delay(100);
  }
  throw new Error(`${message}\n${diagnostic}`);
}
async function waitManagedView(name) {
  let state = {};
  try {
    await until(() => {
      state = JSON.parse(native("managed-view"));
      return ["webContent", "authenticated", "setup", "configurationLoaded", "saveEnabled", "bounded"]
        .every(key => state[key] === true);
    }, "Managed WKWebView did not render authenticated first-run configuration");
  } finally {
    await writeFile(join(evidence, `${name}-readiness.json`), JSON.stringify(state, null, 2));
  }
}
async function stopOwned(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill("SIGTERM");
  for (let count = 0; count < 50 && child.exitCode === null && child.signalCode === null; count++) await delay(100);
  if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
  await until(() => child.exitCode !== null || child.signalCode !== null, "Owned process did not exit");
}
function launchSecondExecutable() {
  const child = spawn(binary, [], { env: { ...process.env, BATC_DESKTOP_TOKEN: "fixture-native-token" },
    stdio: ["ignore", "pipe", "pipe"] });
  child.on("error", error => { diagnostic += String(error); });
  for (const stream of [child.stdout, child.stderr]) stream.on("data", chunk => {
    diagnostic = (diagnostic + chunk).slice(-1_000_000);
  });
  return child;
}
let receipt;
try {
  run("xcrun", ["swiftc", "tests/macos-window.swift", "-o", helper]);
  assert.equal(run(helper, ["existing"]).trim(), "0", "Refusing an existing Dashboard process");
  const folder = resolve("src-tauri/target/release/bundle/dmg");
  const dmgs = (await readdir(folder)).filter(file => file.endsWith(".dmg"));
  assert.equal(dmgs.length, 1, "Exactly one DMG required");
  const dmg = join(folder, dmgs[0]);
  const built = resolve("src-tauri/target/release/bundle/macos/Better Agent Dashboard.app");
  const expected = await bundleManifest(built);
  run("hdiutil", ["verify", dmg]);
  await mkdir(mount);
  run("hdiutil", ["attach", "-readonly", "-nobrowse", "-mountpoint", mount, dmg]);
  mounted = true;
  await mkdir(join(root, "Installed Apps"));
  run("ditto", [join(mount, "Better Agent Dashboard.app"), installed]);
  assert.deepEqual(await bundleManifest(installed), expected, "Installed app differs from packaged app");
  run("codesign", ["--verify", "--deep", "--strict", "--verbose=2", installed]);
  const plist = JSON.parse(run("plutil", ["-convert", "json", "-o", "-", join(installed, "Contents/Info.plist")]));
  const config = JSON.parse(await readFile("src-tauri/tauri.conf.json", "utf8"));
  assert.equal(plist.CFBundleIdentifier, id);
  assert.equal(plist.CFBundleShortVersionString, config.version);
  assert.equal(plist.CFBundleExecutable, "better-agent-dashboard");
  const architecture = process.arch === "arm64" ? "arm64" : "x86_64";
  assert.equal(run("lipo", ["-archs", binary]).trim(), architecture);
  for (const file of ["LICENSE", "COPYRIGHT"]) await access(join(installed, "Contents/Resources/third-party/glib", file));
  steps.push("DMG mount, app copy, complete bundle proof, code-signature integrity, architecture/version and licenses");
  run("hdiutil", ["detach", mount]);
  mounted = false;
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  await mkdir(statePaths[0]);
  ownsState = true;
  const configuration = JSON.stringify({ endpoint: `http://127.0.0.1:${server.address().port}/`,
    expected_actor: "fixture-operator", contract_version: "2026-10-08" });
  await writeFile(join(statePaths[0], "central.json"), configuration, { flag: "wx", mode: 0o600 });
  app = launch();
  await until(() => requests.some(item => item.path === "/api/v1/events"), "Installed WKWebView did not poll");
  await until(() => {
    const state = JSON.parse(native("inspect"));
    return state.finishedLaunching && state.windows.length === 1;
  }, "Expected a fully launched native window");
  const window = JSON.parse(native("inspect")).windows[0].id;
  run("screencapture", ["-x", "-l", String(window), join(evidence, "installed.png")]);
  steps.push("Installed WKWebView authenticated, bootstrapped and polled the observe-only fixture");
  const hideRequest = JSON.parse(native("hide"));
  await until(() => {
    const state = JSON.parse(native("inspect"));
    return state.hidden && state.windows.length === 0 && !state.terminated;
  }, "Native hide did not complete");
  run("open", ["-a", installed]);
  await until(() => !JSON.parse(native("inspect")).hidden && JSON.parse(native("inspect")).windows.length === 1,
    "Finder/Dock reopen did not restore the window");
  assert.equal(JSON.parse(native("inspect")).windows[0].id, window);
  assert.equal(JSON.parse(native("inspect")).terminated, false);
  steps.push("Native app hide and Finder/Dock reopen retained the original process and window");
  native("close");
  await until(() => {
    const state = JSON.parse(native("inspect"));
    return state.windows.length === 0 && !state.terminated;
  }, "Native close button did not hide the window and retain the app");
  run("open", ["-a", installed]);
  await until(() => JSON.parse(native("inspect")).windows.length === 1, "Reopen after native close did not restore the window");
  assert.equal(JSON.parse(native("inspect")).windows[0].id, window);
  steps.push("Native Accessibility close-button action hid the window; Finder/Dock restored the same process and window");
  const second = launchSecondExecutable();
  try {
    await until(() => second.exitCode !== null, "Second instance did not hand off");
    assert.equal(second.exitCode, 0);
    assert.equal(JSON.parse(native("inspect")).windows[0].id, window);
  } finally { await stopOwned(second); }
  steps.push("Second executable invocation exited successfully with the original window retained");
  native("quit");
  await until(() => JSON.parse(native("inspect")).terminated, "Normal macOS Quit did not terminate");
  const previousRequests = requests.length;
  app = launch();
  await until(() => requests.slice(previousRequests).some(item => item.path === "/api/v1/events"), "Relaunched WKWebView did not poll");
  assert.equal(await readFile(join(statePaths[0], "central.json"), "utf8"), configuration);
  await until(() => JSON.parse(native("inspect")).windows.length === 1, "Relaunched window missing");
  run("screencapture", ["-x", "-l", String(JSON.parse(native("inspect")).windows[0].id), join(evidence, "reopened.png")]);
  native("quit");
  await until(() => JSON.parse(native("inspect")).terminated, "Second normal Quit did not terminate");
  assert.deepEqual(violations, []);
  for (const path of ["/api/v1/capabilities", "/api/v1/bootstrap", "/api/v1/events"]) {
    assert(requests.some(item => item.path === path), `Missing ${path}`);
  }
  steps.push("Normal system Quit and relaunch preserved configuration and resumed WebView polling");
  await rm(join(statePaths[0], "central.json"));
  app = JSON.parse(run(helper, ["launch-managed", installed]));
  const managedIdentity = await probeManaged(statePaths[0], { wait: true });
  await until(() => JSON.parse(native("inspect")).windows.length === 1, "Managed first-run window missing");
  await waitManagedView("managed-first-run");
  run("screencapture", ["-x", "-l", String(JSON.parse(native("inspect")).windows[0].id), join(evidence, "managed-first-run.png")]);
  native("close");
  await until(() => JSON.parse(native("inspect")).windows.length === 0, "Managed close did not hide window");
  assert.deepEqual(await probeManaged(statePaths[0]), managedIdentity);
  native("quit");
  await until(() => JSON.parse(native("inspect")).terminated, "Managed Dashboard Quit did not terminate UI");
  assert.deepEqual(await probeManaged(statePaths[0]), managedIdentity, "Central must survive native UI Quit");
  app = JSON.parse(run(helper, ["launch-managed", installed]));
  assert.deepEqual(await probeManaged(statePaths[0], { wait: true }), managedIdentity);
  await until(() => JSON.parse(native("inspect")).windows.length === 1, "Reopened managed window missing");
  await waitManagedView("managed-reopened");
  run("screencapture", ["-x", "-l", String(JSON.parse(native("inspect")).windows[0].id), join(evidence, "managed-reopened.png")]);
  native("quit");
  await until(() => JSON.parse(native("inspect")).terminated, "Reopened managed UI did not quit");
  await stopManaged(statePaths[0]);
  steps.push("Clean managed first-run and relaunch rendered authenticated, enabled setup in WKWebView via Accessibility; same identity survived close and Quit; owned service stopped");
  receipt = { status: "passed", evidence_level: "native-installed-fixture", live_accepted: false,
    hide_request_reported_success: hideRequest.reportedSuccess,
    launch_method: "NSWorkspace Launch Services", quit_method: "NSRunningApplication normal terminate request and observed termination",
    source_sha: run("git", ["rev-parse", "HEAD"]).trim(), source_tree: run("git", ["rev-parse", "HEAD^{tree}"]).trim(),
    macos: run("sw_vers", ["-productVersion"]).trim(), architecture, version: config.version,
    dmg_sha256: sha256(await readFile(dmg)), binary_sha256: sha256(await readFile(binary)), steps };
  await writeFile(join(evidence, "bundle-proof.json"), JSON.stringify(expected, null, 2));
} finally {
  await stopApplication();
  if (ownsState) await stopManaged(statePaths[0]);
  server.closeAllConnections();
  server.close();
  if (mounted) run("hdiutil", ["detach", mount]);
  if (ownsState) for (const path of statePaths) await rm(path, { recursive: true, force: true });
  await rm(root, { recursive: true, force: true });
  await writeFile(join(evidence, "fixture.json"), JSON.stringify({ requests, violations, steps }, null, 2));
  await writeFile(join(evidence, "process.log"), diagnostic);
}
await assert.rejects(lstat(root), { code: "ENOENT" });
for (const path of statePaths) await assert.rejects(lstat(path), { code: "ENOENT" });
steps.push("Owned app and state removed, disk image detached and disk temporary directory removed");
receipt.temporary_data_removed = true;
await writeFile(join(evidence, "result.json"), JSON.stringify(receipt, null, 2));
console.log(JSON.stringify(receipt));
