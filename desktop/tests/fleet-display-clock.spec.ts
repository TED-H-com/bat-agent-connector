import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';

test.describe.configure({mode: 'parallel'});

const golden = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));

for (const locale of ['en-US', 'zh-TW']) for (const controls of [false, true]) {
  test(`mounted Fleet ${controls ? 'overview' : 'status'} diagnosis and route age in place on display ticks ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const at = Date.parse(golden.readiness.observed_at);
    await page.clock.install({time: at});
    await page.clock.pauseAt(at);
    await page.addInitScript(({doc, controls}) => {
      // The overview adds controls to the same sanitized readiness snapshot.
      Object.assign(doc, {control_version: 1, backend: 'rust', login: {show_picker: true, revision: 'f'.repeat(64)},
        pending_migration: null, profiles: []});
      Object.assign(doc.selection, {profiles: [], dashboard: true});
      const caps = {actor: 'fixture', scopes: ['observe'], api_version: 1, contract_version: '2026-10-08', features: {}, hosts: [], actions: []};
      const checkpoint = {cursor: 0, token: 'fixture'};
      const state = (window as any).__clockFleet = {reads: 0, writes: 0, hold: false, release: null as any, listeners: new Set()};
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
        if (command === 'native_status') return {endpoint: 'https://connector.example.invalid', credential_available: true};
        if (command === 'connector_connect') return caps;
        if (command === 'connector_request') return {status: 200, data: args.input.path === '/bootstrap'
          ? {capabilities: caps, sync: {version: 1, server_id: 'fixture', principal_id: 'fixture', checkpoint}}
          : args.input.path.startsWith('/events') ? {events: [], head_cursor: 0, next_cursor: 0, sync: {checkpoint}}
          : {sessions: [], hosts: [], work_items: [], operations: []}};
        if (command === 'fleet_availability') return {configured: true, platform_supported: true, native_controls: controls};
        if (['fleet_request', 'fleet_control'].includes(command)) {
          if (args.input.action !== (controls ? 'overview' : 'status')) {state.writes++; throw new Error('Unexpected write');}
          state.reads++;
          if (state.hold) await new Promise(resolve => {state.release = resolve;});
          return structuredClone(doc);
        }
        throw new Error('Unexpected command ' + command);
      }}});
    }, {doc: golden, controls});
    try {
      await page.goto('/dashboard/#/settings?section=fleet');
      const row = page.locator('.fleet-machine').first().locator('.fleet-diagnosis');
      const title = row.locator('.fleet-diagnosis-title');
      const route = row.locator('[data-fleet-route]');
      await expect(title).toHaveText(locale === 'en-US' ? 'Connected' : '已連線');
      await expect(route).toHaveText(locale === 'en-US' ? 'Route: Tailscale direct' : '路徑：Tailscale 直連');
      await expect(route).toHaveAttribute('data-freshness', 'current');
      const baseline = await page.evaluate(() => {const s = (window as any).__clockFleet; s.hold = true; return s.reads;});
      await page.getByRole('button', {name: locale === 'en-US' ? 'Read status' : '重新讀取狀態', exact: true}).first().click();
      await expect.poll(() => page.evaluate(() => (window as any).__clockFleet.reads)).toBe(baseline + 1);
      // Hold the existing action read so only display ticks can change this DOM.
      await page.evaluate(controls => {
        const checkbox = document.querySelector('.fleet-machine input') as HTMLInputElement;
        const backend = document.querySelector('[data-fleet-backend]') as HTMLSelectElement;
        const details = document.querySelector('.fleet-diagnosis details') as HTMLDetailsElement;
        const diagnosis = document.querySelector('.fleet-diagnosis');
        const route = diagnosis?.querySelector('[data-fleet-route]');
        const advanced = document.querySelector('.fleet-advanced') as HTMLDetailsElement;
        if (advanced) advanced.open = true;
        details.open = true;
        const nodes = [checkbox, backend, diagnosis, route, details, advanced, document.querySelector('.fleet-machine')?.closest('section')?.querySelector('[role="status"]')].filter(Boolean);
        for (const node of nodes) (node as any).clockMarker = 'retained';
        const focused = controls ? backend : checkbox;
        focused.focus();
        (window as any).__clockFleet.retained = {nodes, checkbox, backend, diagnosis, route, details, advanced, focused};
      }, controls);
      const retained = async () => expect(await page.evaluate(() => {
        const r = (window as any).__clockFleet.retained;
        return {nodes: r.nodes.every((node: any) => node.isConnected && node.clockMarker === 'retained'),
          checkbox: document.querySelector('.fleet-machine input') === r.checkbox,
          backend: document.querySelector('[data-fleet-backend]') === r.backend,
          diagnosis: document.querySelector('.fleet-diagnosis') === r.diagnosis,
          route: document.querySelector('[data-fleet-route]') === r.route,
          focused: document.activeElement === r.focused, open: r.details.open && (!r.advanced || r.advanced.open)};
      })).toEqual({nodes: true, checkbox: true, backend: true, diagnosis: true, route: true, focused: true, open: true});
      await retained();
      await page.clock.runFor(3_000);
      await retained();
      await page.clock.fastForward(57_000);
      await expect(title).toHaveText(locale === 'en-US' ? 'Connected · 1 min ago · Checking' : '已連線 · 1 分鐘前 · 檢查中');
      await expect.soft(route).toHaveText(locale === 'en-US'
        ? 'Route: Tailscale direct · 1 min ago · Checking' : '路徑：Tailscale 直連 · 1 分鐘前 · 檢查中');
      await expect.soft(route).toHaveAttribute('data-freshness', 'stale');
      await page.clock.fastForward(600_000);
      await expect(title).toHaveText(locale === 'en-US' ? 'Connected · 11 min ago · Checking' : '已連線 · 11 分鐘前 · 檢查中');
      await expect.soft(route).toHaveText(locale === 'en-US'
        ? 'Route: Tailscale direct · 11 min ago · Checking' : '路徑：Tailscale 直連 · 11 分鐘前 · 檢查中');
      await retained();
      await expect(row).toHaveAttribute('data-fleet-state', 'stale');
      await expect(row).toHaveAttribute('data-freshness', 'stale');
      await expect(row.locator('[data-check-state="passed"]')).toHaveCount(0);
      await expect(row.locator('[data-fleet-check]').first()).toHaveText(locale === 'en-US' ? 'Not yet confirmed' : '尚未確認');
      await expect(row.locator('.fleet-diagnosis-help')).toHaveText(locale === 'en-US'
        ? 'Choose Read status to get a current result before continuing.' : '按「重新讀取狀態」取得新結果，再繼續操作。');
      expect(await page.evaluate(() => (window as any).__clockFleet.reads)).toBe(baseline + 1);
      expect(await page.evaluate(() => (window as any).__clockFleet.writes)).toBe(0);
      expect(await page.evaluate(() => (window as any).__clockFleet.listeners.size)).toBe(1);
      await page.evaluate(() => {location.hash = '#/sessions';});
      await expect(page.locator('.fleet-machine')).toHaveCount(0);
      expect(await page.evaluate(() => (window as any).__clockFleet.listeners.size)).toBe(0);
      await page.evaluate(() => (window as any).__clockFleet.release());
      await page.clock.fastForward(600_000);
      expect(await page.evaluate(() => (window as any).__clockFleet.reads)).toBe(baseline + 1);
    } finally {await context.close();}
  });
}
