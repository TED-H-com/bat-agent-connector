import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm, symlink, chmod } from "node:fs/promises";
import {testScratchRoot} from './test-scratch.mjs';
import { join } from "node:path";
import test from "node:test";
import { bundleManifest } from "./macos-bundle.mjs";

test("app proof detects changed resources and executable permissions", async () => {
  const root = await mkdtemp(join(testScratchRoot(), "dashboard-bundle-"));
  try {
    await mkdir(join(root, "Contents"));
    const file = join(root, "Contents", "app");
    await writeFile(file, "original", { mode: 0o755 });
    const original = await bundleManifest(root);
    await writeFile(file, "tampered");
    assert.notDeepEqual(await bundleManifest(root), original);
    await writeFile(file, "original");
    if (process.platform !== "win32") {
      await chmod(file, 0o644);
      assert.notDeepEqual(await bundleManifest(root), original);
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("app proof refuses escaping links and retains internal link identity", { skip: process.platform === "win32" }, async () => {
  const root = await mkdtemp(join(testScratchRoot(), "dashboard-bundle-"));
  try {
    await writeFile(join(root, "resource"), "fixture");
    await symlink("resource", join(root, "internal"));
    assert((await bundleManifest(root)).some(entry => entry.link === "resource"));
    await symlink("../foreign", join(root, "external"));
    await assert.rejects(bundleManifest(root), /escapes app/);
  } finally { await rm(root, { recursive: true, force: true }); }
});
