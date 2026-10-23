import {test, expect} from '@playwright/test';
import {setTimeout as delay} from 'node:timers/promises';
import {conversationFixture, mountConversation} from './conversation-fixture';
import {runtimeGolden} from './session-runtime-fixture';
import {hostGolden} from './header-hosts-fixture';

test.describe.configure({mode: 'parallel'});

for (const locale of ['en-US', 'zh-TW']) {
  test(`header same-scope reconnect preserves the deadline renewal ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const fixture = conversationFixture(), doc = structuredClone(hostGolden.host_not_refreshed);
    const host = doc.hosts[0], at = Date.parse(host.last_attempt_at), deadline = Date.parse(host.valid_until);
    const session = structuredClone(runtimeGolden.ended_runtime.listed);
    for (const axis of ['activity', 'connection']) Object.assign(session.state.evidence[axis], {
      observed_at: host.last_attempt_at, valid_until: host.valid_until});
    const path = '/sessions/fixture-host/fixture-session';
    let gets = 0, posts = 0, reset = false, issued = false, bootstraps = 0;
    await page.clock.install({time: at + 8_000}); await page.clock.pauseAt(at + 8_000);
    await mountConversation(page, false, async input => {
      if (input.method !== 'GET') posts++;
      if (input.path === path) {gets++; return {status: 200, data: {session}};}
      if (input.path.startsWith('/events') && reset && !issued) {
        issued = true;
        return {status: 409, data: {error: {code: 'EVENT_CURSOR_RESET', reason: 'cursor_expired', resnapshot: true, preserve_drafts: true}}};
      }
      if (input.path === '/bootstrap') bootstraps++;
      return fixture.dispatch(input);
    });
    try {
      await page.goto('/dashboard/#/session/fixture-host/fixture-session');
      const activity = page.locator('#installation-health .health-last-value');
      const connection = page.locator('#installation-health .health-installation-connection');
      await expect(connection).toHaveText(locale === 'en-US' ? 'Connected' : '已連線');
      await expect(activity).toHaveCount(0);
      // A live draft uses the existing same-scope bootstrap path without remounting the view.
      await page.locator('#main textarea').first().fill('Keep this draft through reconnect');
      expect(gets).toBe(1);
      reset = true;
      await Promise.all([page.waitForResponse('**/api/v1/bootstrap'), page.clock.fastForward(1_000)]);
      await expect.poll(() => bootstraps).toBe(2);
      await page.clock.fastForward(1_000);
      await expect.poll(() => gets).toBe(2);
      await expect.soft(activity).toHaveCount(0);
      await expect.soft(connection).toHaveText(locale === 'en-US' ? 'Connected' : '已連線');
      expect(await page.evaluate(() => Date.now())).toBe(deadline - 170_000);
      await page.clock.fastForward(170_000);
      await expect.poll(() => gets).toBe(3);
      await expect(activity).toHaveText(locale === 'en-US'
        ? 'Idle · 3 min ago · Could not confirm (OBSERVATION_NOT_RENEWED)'
        : '閒置 · 3 分鐘前 · 無法確認（OBSERVATION_NOT_RENEWED）');
      await expect(connection).toHaveText(locale === 'en-US'
        ? 'Connected · 3 min ago · Could not confirm (OBSERVATION_NOT_RENEWED)'
        : '已連線 · 3 分鐘前 · 無法確認（OBSERVATION_NOT_RENEWED）');
      await page.clock.fastForward(600_000);
      await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
      expect(gets).toBe(3); expect(posts).toBe(0);
      await expect(page.locator('#main textarea').first()).toHaveValue('Keep this draft through reconnect');
    } finally {await context.close();}
  });

  test(`header unchanged renewal retains unreachable host wording ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const fixture = conversationFixture(), now = 1790000900000;
    const previous = structuredClone(hostGolden.host_unreachable);
    previous.hosts[0].last_attempt_at = previous.hosts[0].last_success_at;
    let gets = 0, posts = 0, release: () => void = () => {};
    const held = new Promise<void>(resolve => {release = resolve;});
    await page.clock.install({time: now}); await page.clock.pauseAt(now);
    await mountConversation(page, false, async input => {
      if (input.method !== 'GET') posts++;
      if (input.path === '/hosts') {
        if (++gets === 2) await held;
        return {status: 200, data: gets === 1 ? previous : hostGolden.host_unreachable};
      }
      return fixture.dispatch(input);
    });
    try {
      await page.goto('/dashboard/#/sessions');
      const other = page.locator('#installation-health [data-health-other]');
      await expect(other).toHaveText(locale === 'en-US'
        ? '1 other machine needs attention · Last attempt: 6 min ago; host unreachable, last successful observation is stale'
        : '另有 1 台機器需要處理 · 上次嘗試：6 分鐘前；無法連至主機，上次成功觀察已過期');
      await page.clock.fastForward(1_000);
      await expect.poll(() => gets).toBe(2);
      release();
      await page.clock.fastForward(1_000);
      await expect(other).toHaveText(locale === 'en-US'
        ? '1 other machine needs attention · Last attempt: 1 min ago; host unreachable, last successful observation is stale'
        : '另有 1 台機器需要處理 · 上次嘗試：1 分鐘前；無法連至主機，上次成功觀察已過期');
      await page.clock.fastForward(600_000);
      await expect(other).toHaveText(locale === 'en-US'
        ? '1 other machine needs attention · Last attempt: 11 min ago; host unreachable, last successful observation is stale'
        : '另有 1 台機器需要處理 · 上次嘗試：11 分鐘前；無法連至主機，上次成功觀察已過期');
      await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
      expect(gets).toBe(2); expect(posts).toBe(0);
    } finally {release(); await context.close();}
  });

  test(`header unchanged session renewal records both evidence axes ${locale}`, async ({browser}) => {
    const context = await browser.newContext({locale}), page = await context.newPage();
    const fixture = conversationFixture(), doc = hostGolden.host_not_refreshed, host = doc.hosts[0];
    const session = structuredClone(runtimeGolden.ended_runtime.listed);
    for (const axis of ['activity', 'connection']) Object.assign(session.state.evidence[axis], {
      observed_at: host.last_attempt_at, valid_until: host.valid_until, stale: true});
    const path = '/sessions/fixture-host/fixture-session', counts = new Map<string, number>();
    let posts = 0, release: () => void = () => {};
    const held = new Promise<void>(resolve => {release = resolve;});
    await page.clock.pauseAt(1790000600000);
    await mountConversation(page, false, async input => {
      if (input.method !== 'GET') posts++;
      if (input.path === '/hosts' || input.path === path) {
        counts.set(input.path, (counts.get(input.path) || 0) + 1);
        if (counts.get(input.path) === 2) {await held; await delay(1_500);}
        return {status: 200, data: input.path === '/hosts' ? doc : {session}};
      }
      return fixture.dispatch(input);
    });
    try {
      await page.goto('/dashboard/#/sessions');
      await expect(page.locator('.filters select option[value="fixture-host"]')).toBeAttached();
      expect(counts.get('/hosts')).toBe(1);
      // Start hosts renewal before selecting the session. The shell serializes
      // renewals, so the later session result proves both reads have applied.
      await page.clock.fastForward(1_000);
      await page.evaluate(() => {location.hash = '#/session/fixture-host/fixture-session';});
      const activity = page.locator('#installation-health .health-last-value');
      const checking = page.locator('#installation-health [data-health-checking]');
      await expect(activity).toHaveText(locale === 'en-US' ? 'Idle · 10 min ago' : '閒置 · 10 分鐘前');
      await expect(checking).toHaveCount(1);
      await expect(checking).toHaveText(locale === 'en-US' ? 'Checking' : '檢查中');
      release();
      await expect.poll(async () => {
        await page.clock.fastForward(1_000);
        return activity.textContent();
      }).toBe(locale === 'en-US'
        ? 'Idle · 10 min ago · Could not confirm (OBSERVATION_NOT_RENEWED)'
        : '閒置 · 10 分鐘前 · 無法確認（OBSERVATION_NOT_RENEWED）');
      expect(counts.get(path)).toBe(2);
      await expect(activity).toHaveText(locale === 'en-US'
        ? 'Idle · 10 min ago · Could not confirm (OBSERVATION_NOT_RENEWED)'
        : '閒置 · 10 分鐘前 · 無法確認（OBSERVATION_NOT_RENEWED）');
      await expect(page.locator('#installation-health .health-installation-connection')).toHaveText(locale === 'en-US'
        ? 'Connected · 10 min ago · Could not confirm (OBSERVATION_NOT_RENEWED)'
        : '已連線 · 10 分鐘前 · 無法確認（OBSERVATION_NOT_RENEWED）');
      // An applied unchanged observation is still stale, so the strip retains
      // its single Checking statement even though the renewal has completed.
      await expect(checking).toHaveCount(1);
      await expect(checking).toHaveText(locale === 'en-US' ? 'Checking' : '檢查中');
      await page.clock.fastForward(1_000);
      expect(counts.get('/hosts')).toBe(2);
      await page.clock.fastForward(600_000);
      await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
      expect(Object.fromEntries(counts)).toEqual({'/hosts': 2, [path]: 2});
      expect(posts).toBe(0);
    } finally {release(); await context.close();}
  });
}
