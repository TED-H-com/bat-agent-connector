import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';

test.describe.configure({mode: 'parallel'});

const golden = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));

for (const locale of ['en-US', 'zh-TW']) for (const controls of [false, true]) {
  test(`mounted Fleet ${controls ? 'overview' : 'status'} ages on display ticks ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const at = Date.parse(golden.readiness.observed_at);
    await page.clock.install({time: at});
    await page.clock.pauseAt(at);
    await page.addInitScript(({doc, controls}) => {
      // The overview adds controls to the same sanitized readiness snapshot.
      Object.assign(doc, {control_version: 1, backend: 'rust', login: {show_picker: true, revision: 'f'.repeat(64)},
        pending_migration: null, profiles: []});
      Object.assign(doc.selection, {profiles: [], dashboard: true});
      const state = (window as any).__clockFleet = {reads: 0, writes: 0, release: null as any, listeners: new Set()};
      const add = document.addEventListener.bind(document), remove = document.removeEventListener.bind(document);
      document.addEventListener = ((type, listener, options) => {
        if (type === 'health-display-tick') state.listeners.add(listener);
        add(type, listener, options);
      }) as typeof document.addEventListener;
      document.removeEventListener = ((type, listener, options) => {
        if (type === 'health-display-tick') state.listeners.delete(listener);
        remove(type, listener, options);
      }) as typeof document.removeEventListener;
      Object.assign(window, {isTauri: true, __TAURI_INTERNALS__: {invoke: async (command: string, args: any) => {
        if (command === 'native_status') return {endpoint: null, credential_available: false};
        if (command === 'connector_connect') throw new Error('Fixture has no Connector');
        if (command === 'fleet_availability') return {configured: true, platform_supported: true, native_controls: controls};
        if (['fleet_request', 'fleet_control'].includes(command)) {
          if (args.input.action !== (controls ? 'overview' : 'status')) {state.writes++; throw new Error('Unexpected write');}
          state.reads++;
          if (state.reads > 1) await new Promise(resolve => {state.release = resolve;});
          return structuredClone(doc);
        }
        throw new Error('Unexpected command ' + command);
      }}});
    }, {doc: golden, controls});
    try {
      await page.goto('/dashboard/#/settings?section=fleet');
      const row = page.locator('.fleet-machine').first().locator('.fleet-diagnosis');
      const title = row.locator('.fleet-diagnosis-title');
      await expect(title).toHaveText(locale === 'en-US' ? 'Connected' : '已連線');
      await page.getByRole('button', {name: locale === 'en-US' ? 'Read status' : '重新讀取狀態', exact: true}).first().click();
      await expect.poll(() => page.evaluate(() => (window as any).__clockFleet.reads)).toBe(2);
      await page.clock.fastForward(60_000);
      await expect(title).toHaveText(locale === 'en-US' ? 'Connected · 1 min ago · Checking' : '已連線 · 1 分鐘前 · 檢查中');
      await page.clock.fastForward(600_000);
      await expect(title).toHaveText(locale === 'en-US' ? 'Connected · 11 min ago · Checking' : '已連線 · 11 分鐘前 · 檢查中');
      expect(await page.evaluate(() => (window as any).__clockFleet.reads)).toBe(2);
      expect(await page.evaluate(() => (window as any).__clockFleet.writes)).toBe(0);
      expect(await page.evaluate(() => (window as any).__clockFleet.listeners.size)).toBe(1);
      await page.evaluate(() => {location.hash = '#/settings?section=connection';});
      await expect(page.locator('.fleet-machine')).toHaveCount(0);
      expect(await page.evaluate(() => (window as any).__clockFleet.listeners.size)).toBe(0);
      await page.evaluate(() => (window as any).__clockFleet.release());
      await page.clock.fastForward(600_000);
      expect(await page.evaluate(() => (window as any).__clockFleet.reads)).toBe(2);
    } finally {await context.close();}
  });
}
