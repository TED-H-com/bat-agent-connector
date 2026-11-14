// @ts-nocheck
import {defineConfig} from '@playwright/test';

const port = Number(process.env.BATC_UI_TEST_PORT || process.env.BATC_TEST_PORT || 1421);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid fixture port');
export default defineConfig({
  testDir: 'tests', testMatch: '**/*.spec.ts',
  use: {baseURL: `http://127.0.0.1:${port}`, locale: 'en-US', headless: true},
  webServer: {command: 'node tests/suite-server.mjs --harness', url: `http://127.0.0.1:${port}/index.html`, reuseExistingServer: false},
});
