import {defineConfig} from '@playwright/test';

// Invoked by pytest in the Python-equipped work-loop job, never browser-ui.
export default defineConfig({
  testDir: 'tests',
  testMatch: ['remembered-credentials.spec.ts', 'credential-permissions.spec.ts'],
  outputDir: 'test-results/credential-service',
  use: {headless: true, trace: 'retain-on-failure'},
});
