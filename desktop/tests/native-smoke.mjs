// Linux native smoke, against an in-process HTTP fixture only. No BAT or real central credentials.
// Run a built app under xvfb-run + dbus-run-session. Pass the release binary for packaging CI.
import { createServer } from "node:http";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import {testScratchRoot} from './test-scratch.mjs';
import { join, resolve } from "node:path";
import { spawn, execFileSync } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import assert from "node:assert/strict";

const root = await mkdtemp(join(testScratchRoot(), "batc-native-smoke-"));
const evidence = process.env.BATC_NATIVE_SMOKE_ARTIFACT_DIR
  ? resolve(process.env.BATC_NATIVE_SMOKE_ARTIFACT_DIR) : resolve("test-results/native-smoke");
let app;
let server;
try {
await mkdir(evidence, {recursive: true});
const caps = {actor: "fixture-operator", scopes: ["observe"], api_version: 1, contract_version: "2026-10-08",
  features: {}, actions: [], hosts: []};
const checkpoint = {cursor: 0, token: "fixture-checkpoint"};
const paths = [];
server = createServer((req, res) => {
  assert.equal(req.method, "GET");
  assert.equal(req.headers.authorization, "Bearer fixture-native-token");
  const path = new URL(req.url, "http://127.0.0.1").pathname;
  paths.push(path);
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(path.endsWith("/capabilities") ? caps : path.endsWith("/bootstrap")
    ? {sync: {version: 1, server_id: "fixture-server", principal_id: "fixture-principal", checkpoint}, capabilities: caps}
    : path.endsWith("/events") ? {events: [], next_cursor: 0, head_cursor: 0, has_more: false, sync: {checkpoint}}
    : {sessions: [], work_items: [], operations: [], hosts: []}));
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const configDir = join(root, "config", "io.betteragent.dashboard");
await mkdir(configDir, {recursive: true});
await writeFile(join(configDir, "central.json"), JSON.stringify({endpoint: `http://127.0.0.1:${server.address().port}/`,
  expected_actor: "fixture-operator", contract_version: "2026-10-08"}));
const env = {...process.env, TMPDIR: root, TEMP: root, TMP: root, XDG_CONFIG_HOME: join(root, "config"), XDG_DATA_HOME: join(root, "data"),
  BATC_DESKTOP_TOKEN: "fixture-native-token", WEBKIT_DISABLE_DMABUF_RENDERER: "1"};
const binary = resolve(process.argv[2] || "src-tauri/target/debug/better-agent-dashboard");
app = spawn(binary, [], {env, stdio: ["ignore", "pipe", "pipe"]});
let diagnostic = "";
app.stderr.on("data", data => { diagnostic += data; });
  for (let tries = 0; tries < 100 && !paths.includes("/api/v1/events"); tries++) {
    if (app.exitCode !== null) throw new Error(`Native app exited: ${diagnostic}`);
    await delay(100);
  }
  assert(paths.includes("/api/v1/bootstrap"), `Native WebView did not bootstrap: ${diagnostic}`);
  assert(paths.includes("/api/v1/events"), `Native WebView did not render and poll: ${diagnostic}`);
  const window = execFileSync("xdotool", ["search", "--onlyvisible", "--name", "^Better Agent Dashboard$"]).toString().trim();
  assert(window && !window.includes("\n"), "expected one visible Dashboard window");
  await delay(500);
  execFileSync("python3", ["-c", "from PIL import ImageGrab; import sys; ImageGrab.grab().save(sys.argv[1])", join(evidence, "native.png")], {env});
  // Send the same WM_DELETE_WINDOW message as a window manager's Close action.
  execFileSync("python3", ["-c", `
import ctypes as c, sys
x = c.CDLL('libX11.so.6')
x.XOpenDisplay.restype = c.c_void_p
x.XInternAtom.argtypes = [c.c_void_p, c.c_char_p, c.c_int]
x.XInternAtom.restype = c.c_ulong
x.XSendEvent.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_long, c.c_void_p]
x.XFlush.argtypes = [c.c_void_p]
class Event(c.Structure):
    _fields_ = [('type', c.c_int), ('serial', c.c_ulong), ('send_event', c.c_int), ('display', c.c_void_p),
      ('window', c.c_ulong), ('message_type', c.c_ulong), ('format', c.c_int), ('data', c.c_long * 5)]
d = x.XOpenDisplay(None)
e = Event(33, 0, 1, d, int(sys.argv[1]), x.XInternAtom(d, b'WM_PROTOCOLS', 0), 32)
e.data[0] = x.XInternAtom(d, b'WM_DELETE_WINDOW', 0)
x.XSendEvent(d, e.window, 0, 0, c.byref(e))
x.XFlush(d)
`, window], {env});
  await delay(300);
  assert.equal(app.exitCode, null, "closing the window must keep the native app running");
  assert.match(execFileSync("xwininfo", ["-id", window]).toString(), /Map State: IsUnMapped/);
  const second = spawn(binary, [], {env, stdio: "ignore"});
  const exit = await Promise.race([new Promise(resolve => second.on("exit", resolve)), delay(5000).then(() => "timeout")]);
  if (exit === "timeout") second.kill();
  assert.equal(exit, 0, "second app must hand off to the existing instance and exit");
  const windows = execFileSync("xdotool", ["search", "--onlyvisible", "--name", "^Better Agent Dashboard$"]).toString().trim();
  assert.equal(windows, window);
  const result = {binary, artifact: join(evidence, "native.png"), close_to_tray: "window hidden, process retained",
    single_instance: "passed, hidden window restored", requests: [...new Set(paths)]};
  await writeFile(join(evidence, "result.json"), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} finally {
  if (app && app.exitCode === null && app.signalCode === null) {
    app.kill();
    for (let count = 0; count < 50 && app.exitCode === null && app.signalCode === null; count++) await delay(100);
    if (app.exitCode === null && app.signalCode === null) {
      app.kill("SIGKILL");
      await new Promise(resolve => app.once("exit", resolve));
    }
  }
  server?.closeAllConnections();
  server?.close();
  await rm(root, {recursive: true, force: true});
}
