import {managedStatusFixture} from './managed-status-fixture.ts';
import {test, expect, type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {STRINGS} from '../src/i18n.js';
import {conversationFixture, mountConversation} from './conversation-fixture';
import {expectHeaderFits} from './header-layout';

test.describe.configure({mode: 'parallel'});

// Drift-checked against ApiV1.bootstrap by test_api_v1_bootstrap_golden.py.
const golden = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json', import.meta.url), 'utf8'));
// ApiV1's error envelope and DashboardSync.ResetRequired.document(); no new wire fields.
const failures = {
  unauthorized: {status: 401, data: {error: {code: 'UNAUTHORIZED', message: 'a valid bearer token is required'}}},
  forbidden: {status: 403, data: {error: {code: 'FORBIDDEN', message: "this route needs the 'observe' scope"}}},
  service: {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Events unavailable'}}},
  network: {status: 0, data: {}},
  // Deliberately malformed fault injection, not a claimed producer response.
  malformed: {status: 200, data: {}},
  reset: {status: 409, data: {error: {code: 'EVENT_CURSOR_RESET', reason: 'cursor_expired',
    message: 'Reload the bootstrap snapshot before replaying events; preserve local drafts.',
    resnapshot: true, preserve_drafts: true}}},
};
const states = {
  unauthorized: ['ui_connection_refused', 'refused'], forbidden: ['ui_connection_refused', 'refused'],
  service: ['ui_service_error', 'service_error'], network: ['ui_reconnecting', 'reconnecting'],
  malformed: ['ui_invalid_response', 'invalid_response'], reset: ['ui_connecting', 'connecting'],
} as const;

async function abortNetwork(page: Page, failed: () => boolean) {
  await page.route('**/api/v1/events?*', route => failed() ? route.abort('connectionfailed') : route.fallback());
}

for (const locale of ['en-US', 'zh-TW']) {
  const copy = STRINGS[locale === 'zh-TW' ? 'zh-TW' : 'en'];
  test(`Connect verifies the token before any event poll ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const fixture = conversationFixture();
    let polls = 0, release!: () => void;
    const pending = new Promise<void>(resolve => {release = resolve;});
    await mountConversation(page, false, async input => {
      if (input.path === '/capabilities') await pending;
      if (input.path.startsWith('/events?')) polls++;
      return fixture.dispatch(input);
    });
    await page.addInitScript(() => sessionStorage.clear());
    await page.clock.install();
    try {
      await page.goto('/dashboard/');
      await page.locator('input[type=password]').fill('fixture-token');
      await page.getByRole('button', {name: copy.connect, exact: true}).click();
      await page.evaluate(() => {location.hash = '#/settings';});
      await page.clock.runFor(3000);
      await expect(page.locator('#live')).toHaveText(copy.ui_connecting);
      expect(polls).toBe(0);
      release();
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      await expect.poll(() => polls).toBeGreaterThan(0);
    } finally {release(); await context.close();}
  });
  test(`inventory permission refusal stays visible while bearer polling continues ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const fixture = conversationFixture();
    let denied = false;
    await mountConversation(page, false, async input => {
      if (denied && input.path === '/sessions/demo/session-1') return failures.forbidden;
      return fixture.dispatch(input);
    });
    try {
      await page.goto('/dashboard/#/session/demo/session-1');
      await expect(page.locator('.msg')).toHaveCount(12);
      denied = true; fixture.data.cursor = 1;
      await expect(page.getByRole('heading', {name: copy.session_err_forbidden, exact: true})).toBeVisible();
      await expect(page.locator('.msg')).toHaveCount(0);
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      expect(await page.evaluate(() => [sessionStorage, localStorage].flatMap(store => Object.keys(store))
        .filter(key => key.startsWith('batc.credential-refused.')))).toEqual([]);
      const poll = await page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/events');
      expect(poll.status()).toBe(200);
      expect(poll.request().headers().authorization).toBe('Bearer fixture-token');
      await expect(page.getByRole('heading', {name: copy.session_err_forbidden, exact: true})).toBeVisible();
    } finally {await context.close();}
  });
  test(`successful poll time does not wait for view refresh success ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const fixture = conversationFixture();
    let fail = false, answeredAt = 0, refreshFailed = false, release!: () => void;
    const pending = new Promise<void>(resolve => {release = resolve;});
    await mountConversation(page, false, async input => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (path === '/events' && fail) {
        if (answeredAt) await pending;
        else answeredAt = Date.now();
      }
      if (path.endsWith('/messages') && fail) refreshFailed = true;
      return fixture.dispatch(input);
    });
    try {
      await page.goto('/dashboard/#/session/demo/session-1');
      await expect(page.locator('.msg')).toHaveCount(12);
      fail = fixture.data.fail = true; fixture.data.cursor = 1;
      await expect.poll(() => refreshFailed).toBe(true);
      const name = copy.ui_connector_status.replace('{status}', copy.ui_connected);
      await expect(page.locator('#live')).toHaveText(copy.ui_connected);
      await expect(page.locator('#live')).toHaveAttribute('title', `${name} — ${copy.sync_actions_paused}`);
      await page.evaluate(() => {location.hash = '#/settings';});
      const at = await page.locator('[data-last-successful-poll]').getAttribute('data-observed-at');
      expect(Date.parse(at!)).toBeGreaterThanOrEqual(answeredAt);
      await expect(page.locator('[data-last-event]')).toHaveAttribute('data-observed-at', '');
    } finally {release(); await context.close();}
  });

  for (const native of [false, true]) for (const width of [390, 1440]) for (const failure of ['forbidden', 'service', 'network'] as const) {
    test(`managed heading makes no static connection claim ${failure} ${locale} ${width} ${native ? 'IPC' : 'HTTP'}`, async ({browser}, info) => {
      const context = await browser.newContext({locale, viewport: {width, height: 1000}}), page = await context.newPage();
      const fixture = conversationFixture();
      let failed = false;
      await mountConversation(page, native, async input => {
        const path = new URL(input.path, 'http://fixture').pathname;
        if (path === '/bootstrap') return {status: 200, data: structuredClone(golden)};
        if (path === '/capabilities') return {status: 200, data: structuredClone(golden.capabilities)};
        if (path === '/managed/setup') return {status: 200, data: {revision: 'a'.repeat(64), hosts: [], repositories: [],
          profiles: [], profiles_error: null, verification: {commands: {}, timeout_s: 300}, busy: false}};
        if (path === '/events' && native && failed && failure === 'network') throw new Error('Fixture connection failed');
        if (path === '/events') return failed ? failures[failure] : {status: 200, data: {
          events: [], next_cursor: 0, head_cursor: 0, has_more: false, sync: golden.sync}};
        return fixture.dispatch(input);
      });
      if (native) await page.exposeFunction('reasonManagedStatus', () => managedStatusFixture());
      if (native) await page.addInitScript(() => {
        const original = (window as any).__TAURI_INTERNALS__.invoke;
        (window as any).__TAURI_INTERNALS__.invoke = async (command: string, args: any) => {
          if (command === 'managed_control') return (window as any).reasonManagedStatus();
          if (command === 'fleet_availability') return {configured: false, platform_supported: false};
          return original(command, args);
        };
      });
      await abortNetwork(page, () => failed && failure === 'network');
      try {
        await page.goto('/dashboard/#/settings');
        const advanced = page.locator('[data-setup-advanced]');
        await advanced.evaluate((el: HTMLDetailsElement) => {el.open = true;});
        await expect(page.locator('#live')).toHaveText(copy.ui_connected);
        failed = true;
        await expect(page.locator('#live')).toHaveAttribute('data-connection', 'reconnecting');
        await page.locator('[data-connection-card]').scrollIntoViewIfNeeded();
        await expect(page.locator('[data-connection-card] h2')).toBeInViewport();
        await expect(page.locator('.global-connection-facts .ui-pill')).toBeInViewport();
        await page.screenshot({path: info.outputPath(`managed-${failure}-${locale}-${width}.png`)});
        await expect.soft(page.locator('[data-connection-card] h2')).toHaveText(copy.ui_local_connector);
        const label = copy[states[failure][0]];
        await expect.soft(page.locator('#live')).toHaveText(label);
        await expect.soft(page.locator('.global-connection-facts .ui-pill')).toHaveText(label);
        await expect(page.locator('[data-connection-card]')).not.toContainText(
          locale === 'zh-TW' ? '已連線至本機 Connector' : 'Connected to your local Connector');
        await expectHeaderFits(page, info);
        if (!native && failure === 'forbidden') {
          failed = false;
          await page.getByRole('button', {name: copy.connection_retry_remembered, exact: true}).click();
          await expect(page.locator('#live')).toHaveText(copy.ui_connected);
          await expect(page.getByRole('button', {name: copy.connection_retry_remembered, exact: true})).toHaveCount(0);
        }
      } finally {await context.close();}
    });
  }

  for (const failure of Object.keys(failures) as (keyof typeof failures)[]) {
    test(`poll reason survives to header, Settings and POST then empty recovery: ${failure} ${locale}`, async ({browser}) => {
      const context = await browser.newContext({locale}), page = await context.newPage();
      const fixture = conversationFixture(), id = 'wi_' + 'd'.repeat(20);
      let failed = false, resetSeen = false, posts = 0, successfulPolls = 0, release!: () => void;
      const pending = new Promise<void>(resolve => {release = resolve;});
      const caps = {...golden.capabilities, scopes: ['observe', 'manage']};
      delete caps.managed_installation; delete caps.desktop_identity;
      await mountConversation(page, false, async input => {
        const path = new URL(input.path, 'http://fixture').pathname;
        if (input.method === 'POST') {posts++; throw new Error('Paused POST reached transport');}
        if (path === '/capabilities') return {status: 200, data: caps};
        if (path === '/bootstrap') {
          if (resetSeen) await pending;
          return {status: 200, data: {...structuredClone(golden), capabilities: caps}};
        }
        if (path === '/events') {
          if (failed) {if (failure === 'reset') resetSeen = true; return failures[failure];}
          successfulPolls++;
          return {status: 200, data: {events: [], next_cursor: 0, head_cursor: 0, has_more: false, sync: golden.sync}};
        }
        if (path === '/work-items/' + id) return {status: 200, data: {
          work_item: {work_item_id: id, title: 'Poll reason fixture', version: 1, state: 'todo', goal: '', request: '',
            acceptance: '', steps: [], attachments: [], completion: {display_state: 'todo', fingerprint: 'fixture-fingerprint'}},
          project: {project_id: 'prj_fixture', name: 'Fixture'}, links: [], path: [], children: [], derived: [], events: []}};
        if (path === '/artifacts') return {status: 200, data: {artifacts: [], next_cursor: null}};
        return fixture.dispatch(input);
      });
      await abortNetwork(page, () => failed && failure === 'network');
      try {
        await page.goto('/dashboard/#/item/' + id);
        await expect.poll(() => successfulPolls).toBeGreaterThan(0);
        await page.getByRole('button', {name: locale === 'zh-TW' ? '更多' : 'More', exact: true}).click();
        await page.locator('input[maxlength="120"]').fill('Preserved draft');
        failed = true;
        const [key, reason] = states[failure];
        const label = copy[key], name = copy.ui_connector_status.replace('{status}', label);
        const help = reason === 'reconnecting' ? copy.offline_actions_paused : reason === 'connecting'
          ? copy.ui_connecting_help : copy[`error_code_CLIENT_ACTION_${reason.toUpperCase()}`];
        await expect(page.locator('#live')).toHaveText(label);
        await expect(page.locator('#live')).toHaveAccessibleName(name);
        await expect(page.locator('#live')).toHaveAttribute('title', `${name} — ${help}`);
        if (failure !== 'network') await expect(page.locator('#live')).not.toHaveAttribute('title', new RegExp(copy.offline_actions_paused));
        if (failure !== 'reset') {
          await page.getByRole('button', {name: copy.save, exact: true}).click();
          await expect(page.getByText(copy[`error_code_CLIENT_ACTION_${reason.toUpperCase()}`], {exact: true})).toBeVisible();
          expect(posts).toBe(0);
          await expect(page.locator('input[maxlength="120"]')).toHaveValue('Preserved draft');
        } else {
          // activate retires the form epoch before awaiting the new snapshot, so
          // its final-frame check precedes POST. Check the shared gate separately;
          // changing that reset lifecycle belongs to the continuity increment.
          const gate = await page.evaluate(async failure => {
            const {pollFailure, clientActionGate} = await import('/src/central-service-status.js');
            return clientActionGate({token: 'credential', online: false, viewReady: false,
              pollHealth: pollFailure({status: failure.status, code: failure.data.error.code}, Date.now())}, {post: true});
          }, failures.reset);
          expect(gate).toEqual({allowed: false, reason});
        }
        await page.evaluate(() => {location.hash = '#/settings';});
        await expect(page.locator('.global-connection-facts .ui-pill')).toHaveText(label);
        const poll = page.locator('[data-last-successful-poll]'), event = page.locator('[data-last-event]');
        const lastSuccess = await poll.getAttribute('data-observed-at');
        const lastEvent = await event.getAttribute('data-observed-at');
        expect(lastSuccess).toBeTruthy(); expect(lastEvent).toBe('');
        failed = false; release();
        if (failure === 'unauthorized' || failure === 'forbidden') {
          await page.getByRole('button', {name: copy.connection_retry_remembered, exact: true}).click();
        }
        await expect(page.locator('#live')).toHaveText(copy.ui_connected);
        await expect(page.locator('#live')).toHaveAttribute('title', `${copy.ui_connector_status.replace('{status}', copy.ui_connected)} — ${copy.desktop_polling}`);
        await expect(page.locator('.global-connection-facts .ui-pill')).toHaveText(copy.ui_connected);
        await expect.poll(() => poll.getAttribute('data-observed-at')).not.toBe(lastSuccess);
        await expect(event).toHaveAttribute('data-observed-at', lastEvent!);
        expect(posts).toBe(0);
      } finally {release(); await context.close();}
    });
  }
}
