// @ts-nocheck
import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';

const manifest = JSON.parse(readFileSync(new URL('../../dist-suite/manifest.json', import.meta.url), 'utf8'));
const paths = new Set(manifest.files.map(file => '/' + file.path.split('/').map(encodeURIComponent).join('/')));

for (const language of ['en', 'zh-TW']) {
  test(`suite first view uses read-only native status in ${language}`, async ({page}) => {
    const requests = [], errors = [];
    page.on('request', request => requests.push(request.url()));
    page.on('pageerror', error => errors.push(error.message));
    await page.clock.install({time: new Date('2026-01-01T00:00:00Z')});
    await page.addInitScript(language => {
      localStorage.setItem('batc.lang', language);
      const calls = [], violations = [];
      window.addEventListener('securitypolicyviolation', event => violations.push({directive: event.violatedDirective, blocked: event.blockedURI}));
      Object.assign(window, {isTauri: true, __suiteCalls: calls, __suiteViolations: violations,
        __TAURI_INTERNALS__: {invoke: async command => {
          calls.push(command);
          if (command !== 'plugin:bac|native_status') throw new Error('Unexpected command');
          return {endpoint: null, error: null, credential_available: false, configuration_setup: false,
            dashboard_bundle: {embedded: false, pinned: false, commit: null, sha256: null}};
        }}});
    }, language);
    await page.goto('/index.html#/settings');
    await expect(page.getByText(language === 'en' ? 'No current observation' : '尚無可用觀測')).toHaveCount(1);
    await expect(page.locator('[data-api-error], .ui-inline-error')).toHaveCount(0);
    await expect(page.locator('[data-managed-service]')).toHaveCount(0);
    await expect(page.locator('section[aria-label="Tailscale"]')).toHaveCount(0);
    const assertCalls = async () => {
      const calls = await page.evaluate(() => window.__suiteCalls);
      expect(calls.length).toBeGreaterThan(0);
      expect(calls.every(command => command === 'plugin:bac|native_status')).toBe(true);
    };
    await assertCalls();
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await assertCalls();
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await assertCalls();
    await expect.poll(() => page.evaluate(() => window.__suiteCalls.length)).toBeGreaterThanOrEqual(3);
    const beforeRecovery = await page.evaluate(() => window.__suiteCalls.length);
    const now = await page.evaluate(() => Date.now());
    // Move wall time without firing the poll timer: visibility must wake recovery.
    await page.clock.setSystemTime(new Date(now + 3100));
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await expect.poll(() => page.evaluate(() => window.__suiteCalls.length)).toBeGreaterThan(beforeRecovery);
    await assertCalls();
    const origin = new URL(page.url()).origin;
    for (const request of requests) {
      const url = new URL(request);
      expect(url.origin).toBe(origin);
      expect(paths.has(url.pathname)).toBe(true);
      expect(url.search).toBe('');
    }
    expect(errors).toEqual([]);
    expect(await page.evaluate(() => window.__suiteViolations)).toEqual([]);
  });
}
