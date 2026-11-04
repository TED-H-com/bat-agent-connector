import { readdir } from "node:fs/promises";
import { execFileSync } from "../../scripts/hidden-child-process.mjs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const root = fileURLToPath(new URL("../../", import.meta.url));
const relative = "src/bat_agent_connector/dashboard";
assert.deepEqual((await readdir(new URL(`../../${relative}/`, import.meta.url))).sort(),
  ["app.css", "app.js", "i18n.js", "index.html"], "Browser build must match the Python static-file allowlist");
execFileSync("git", ["diff", "--exit-code", "HEAD", "--", relative], {cwd: root, stdio: "inherit"});
const untracked = execFileSync("git", ["ls-files", "--others", "--exclude-standard", "--", relative], {cwd: root, encoding: "utf8"});
assert.equal(untracked, "", "Generated browser files must be tracked");
