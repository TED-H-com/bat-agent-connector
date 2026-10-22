import { defineConfig } from "@playwright/test";
const port = Number(process.env.BATC_UI_TEST_PORT || process.env.BATC_TEST_PORT || 1421);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid fixture port");
export default defineConfig({
  testDir: "tests",
  testMatch: "**/*.spec.ts",
  // These journeys use the Python-driven auth harness in work-loop CI.
  testIgnore: ["**/remembered-credentials.spec.ts", "**/credential-permissions.spec.ts"],
  use: { baseURL: `http://127.0.0.1:${port}`, locale: "en-US", headless: true },
  webServer: { command: "node tests/static-server.mjs", url: `http://127.0.0.1:${port}/dashboard/`, reuseExistingServer: false }
});
