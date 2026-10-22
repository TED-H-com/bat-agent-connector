// Never change an operator's Keychain. Restore the disposable runner's configuration even on failure.
import {testScratchRoot} from './test-scratch.mjs';
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

assert.equal(process.platform, "darwin");
assert.equal(process.env.GITHUB_ACTIONS, "true");
assert.equal(process.env.RUNNER_ENVIRONMENT, "github-hosted");
assert(process.env.RUNNER_TEMP);
const security = args => execFileSync("/usr/bin/security", args, { encoding: "utf8", timeout: 15_000 }).trim();
const unquote = value => value.trim().replace(/^"|"$/g, "");
const originalDefault = unquote(security(["default-keychain", "-d", "user"]));
const originalList = security(["list-keychains", "-d", "user"]).split("\n").filter(Boolean).map(unquote);
assert(originalDefault && originalList.length);
const root = await mkdtemp(join(testScratchRoot(), "dashboard-keychain-"));
const keychain = join(root, "fixture.keychain-db");
const password = randomUUID(); // Disposable synthetic keychain, no real credentials.
let created = false;
try {
  security(["create-keychain", "-p", password, keychain]);
  created = true;
  security(["unlock-keychain", "-p", password, keychain]);
  security(["list-keychains", "-d", "user", "-s", keychain]);
  security(["default-keychain", "-d", "user", "-s", keychain]);
  execFileSync("cargo", ["test", "--locked", "--manifest-path", "src-tauri/Cargo.toml", "--bin", "better-agent-dashboard",
    "credentials::macos::tests::isolated_keychain_roundtrip", "--", "--ignored", "--exact", "--nocapture"],
  { env: { ...process.env, BATC_ISOLATED_KEYCHAIN: "true" }, stdio: "inherit", timeout: 120_000 });
} finally {
  // Restore search list before default; keep cleanup attempts independent.
  const errors = [];
  for (const args of [["list-keychains", "-d", "user", "-s", ...originalList],
    ["default-keychain", "-d", "user", "-s", originalDefault],
    ...(created ? [["delete-keychain", keychain]] : [])]) {
    try { security(args); } catch (error) { errors.push(error); }
  }
  await rm(root, { recursive: true, force: true });
  if (errors.length) throw new AggregateError(errors, "Fixture Keychain cleanup failed");
}
assert.equal(unquote(security(["default-keychain", "-d", "user"])), originalDefault);
assert.deepEqual(security(["list-keychains", "-d", "user"]).split("\n").filter(Boolean).map(unquote), originalList);
const evidence = resolve("test-results/macos-installed");
await mkdir(evidence, { recursive: true });
await writeFile(join(evidence, "keychain.json"), JSON.stringify({ status: "passed", isolated_keychain: true,
  cases: ["create", "separate-process readback", "replace", "invalid replacement preserves original", "delete", "missing delete"],
  temporary_data_removed: true, original_configuration_restored: true }, null, 2));
