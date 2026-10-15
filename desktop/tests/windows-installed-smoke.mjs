// Installs only on a disposable GitHub-hosted Windows runner. No live BAT/central.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { nsisBundleProof } from "./windows-bundle.mjs";

assert.equal(process.platform, "win32", "Windows runner required");
assert.equal(process.env.GITHUB_ACTIONS, "true", "GitHub Actions required");
assert.equal(process.env.RUNNER_ENVIRONMENT, "github-hosted", "Disposable hosted runner required");
const evidence = resolve("test-results/windows-installed");
await mkdir(evidence, { recursive: true });
const proof = nsisBundleProof(await readFile("src-tauri/target/release/better-agent-dashboard.exe"));
await writeFile(resolve(evidence, "bundle-proof.json"), JSON.stringify(proof, null, 2));
const caps = { actor: "fixture-operator", scopes: ["observe"], api_version: 1,
  contract_version: "2026-10-08", features: {}, actions: [], hosts: [] };
const checkpoint = { cursor: 0, token: "fixture-checkpoint" };
const requests = [];
const violations = [];
let statusRequests = 0;
const server = createServer((req, res) => {
  const path = new URL(req.url, "http://127.0.0.1").pathname;
  res.setHeader("Content-Type", "application/json");
  if (path === "/_fixture/status" && req.method === "GET") {
    statusRequests++;
    res.end(JSON.stringify({ requests, violations }));
    return;
  }
  requests.push({ method: req.method, path });
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
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
let diagnostic = "";
const harness = { status: "running", processes: [], error: null, evidence_errors: [] };
async function runPowerShell(name, script, args, timeoutMs) {
  const child = spawn("pwsh", ["-NoLogo", "-NoProfile", "-NonInteractive", "-File",
    resolve(script), ...args], { stdio: ["ignore", "pipe", "pipe"] });
  const outcome = { name, exit_code: null, signal: null, timed_out: false, spawn_error: null,
    exit_unconfirmed: false, output_drain_incomplete: false };
  harness.processes.push(outcome);
  for (const stream of [child.stdout, child.stderr]) stream.on("data", data => {
    // Disposable fixture output only; retain a bounded diagnostic even on repeated failures.
    diagnostic = (diagnostic + data).slice(-1_048_576);
    process.stdout.write(data);
  });
  await new Promise(resolve => {
    let drain, exited = false, finished = false;
    const closePipes = () => { child.stdout.destroy(); child.stderr.destroy(); };
    const finish = () => { if (finished) return; finished = true; clearTimeout(timer); clearTimeout(drain); resolve(); };
    const timer = setTimeout(() => {
      outcome.timed_out = true;
      try { child.kill(); } catch (error) { outcome.spawn_error = error.message; }
      drain = setTimeout(() => {
        outcome.exit_unconfirmed = !exited; outcome.output_drain_incomplete = true;
        closePipes(); child.unref(); finish();
      }, 5000);
    }, timeoutMs);
    child.once("error", error => { outcome.spawn_error = error.message; closePipes(); finish(); });
    child.once("exit", (code, signal) => {
      exited = true; outcome.exit_code = code; outcome.signal = signal; clearTimeout(timer); clearTimeout(drain);
      // A surviving descendant may inherit a pipe. Do not wait indefinitely or kill its tree.
      drain = setTimeout(() => { outcome.output_drain_incomplete = true; closePipes(); finish(); }, 1000);
    });
    child.once("close", finish);
  });
  return outcome;
}
let primaryFailure;
try {
  // The helper checks take seconds; compiling and running the UIA probe tests with csc.exe
  // on a cold runner can take longer, so the budget covers both while staying bounded.
  const support = await runPowerShell("fixture_helpers", "tests/windows-installed-support-test.ps1", [], 60_000);
  assert(!support.timed_out && !support.spawn_error && !support.output_drain_incomplete && support.exit_code === 0,
    `Windows fixture helper regressions failed: ${JSON.stringify(support)}`);
  const installed = await runPowerShell("installed_app", "tests/windows-installed-smoke.ps1", [
    "-Endpoint", `http://127.0.0.1:${server.address().port}`, "-Evidence", evidence], 240_000);
  let receipt;
  try { receipt = JSON.parse(await readFile(resolve(evidence, "result.json"), "utf8")); }
  catch (error) { harness.evidence_errors.push({ file: "result.json", error: error.message }); }
  assert(!installed.timed_out && !installed.spawn_error && !installed.output_drain_incomplete && installed.exit_code === 0,
    receipt?.primary_failure?.error || receipt?.error || `Installed Windows smoke failed (exit ${installed.exit_code}, timeout ${installed.timed_out})`);
  assert.deepEqual(violations, [], "Fixture received an unauthorized or mutating request");
  for (const path of ["/api/v1/capabilities", "/api/v1/bootstrap", "/api/v1/events"]) {
    assert(requests.some(request => request.path === path), `Missing native request: ${path}`);
  }
  assert.equal(receipt?.status, "passed");
  assert.deepEqual(receipt.cleanup_errors, [], "Installed fixture cleanup was incomplete");
  harness.status = "passed";
  console.log(JSON.stringify(receipt));
} catch (error) {
  primaryFailure = error;
  harness.status = "failed";
  harness.error = error.message;
} finally {
  for (const [file, content] of [["fixture.json", JSON.stringify({ requests, violations, status_requests: statusRequests }, null, 2)], ["process.log", diagnostic]]) {
    try { await writeFile(resolve(evidence, file), content); }
    catch (error) { harness.evidence_errors.push({ file, error: error.message }); }
  }
  if (harness.evidence_errors.length) harness.status = "failed";
  try { await writeFile(resolve(evidence, "harness.json"), JSON.stringify(harness, null, 2)); }
  catch (error) { harness.evidence_errors.push({ file: "harness.json", error: error.message }); }
  server.closeAllConnections();
  server.close();
}
if (primaryFailure) throw primaryFailure;
assert.deepEqual(harness.evidence_errors, [], "Installed fixture evidence could not be saved");
