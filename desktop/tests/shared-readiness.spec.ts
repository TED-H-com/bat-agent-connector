import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {mountProfilePicker} from './work-profile-picker-fixture.ts';

const fleet = JSON.parse(readFileSync(new URL('./fixtures/fleet-status.golden.json', import.meta.url), 'utf8'));
const profiles = JSON.parse(readFileSync(new URL('../../tests/fixtures/work-profile-readiness.json', import.meta.url), 'utf8'));

for (const locale of ['en-US', 'zh-TW']) test.describe(`shared readiness ${locale}`, () => {
  test.use({locale});
  test('Fleet uses the header formatter at the same clock boundary', async ({page}) => {
    await page.goto('/ui-components.html');
    await page.evaluate(async ({fleet, locale}) => {
      const {fleetStatus} = await import('/src/fleet-status.js');
      const {h} = await import('/src/ui/dom.js');
      const {t, setLocale} = await import('/src/i18n.js');
      setLocale(locale === 'en-US' ? 'en' : locale);
      const now = Date.parse(fleet.readiness.observed_at) + 60_000;
      const node = fleetStatus(fleet.readiness.hosts[0], {h, t, now, snapshot: fleet,
        readable: true, entry: fleet.configuration.connections[0], detailsState: new Map()});
      document.getElementById('fixture')!.append(node);
    }, {fleet, locale});
    await expect(page.locator('.fleet-diagnosis-title')).toHaveText(locale === 'en-US'
      ? 'Connected · 1 min ago · Checking' : '已連線 · 1 分鐘前 · 檢查中');
    await expect(page.locator('[data-fleet-diagnosis]')).toHaveAttribute('data-freshness', 'stale');
    await page.locator('summary').click();
    await expect(page.locator('[data-check-state="passed"]')).toHaveCount(0);
  });

  test('Work unreachable shows the last attempt age while success is five minutes older', async ({page}) => {
    const body = profiles.cases.host_unreachable;
    await page.clock.install({time: new Date(1790000900000)});
    await page.clock.pauseAt(1790000900000);
    let release;
    const held = new Promise<void>(resolve => release = resolve);
    const {facts, reads, posts} = await mountProfilePicker(page, body, profiles.profile, locale, async (route, n) => {
      if (n > 1) await held;
      await route.fulfill({status: 200, json: body});
    });
    try {
      for (const name of ['can_observe', 'can_start_own']) {
        await expect(facts.locator(`[data-work-profile-state="${name}"] [data-work-profile-value]`)).toHaveText(locale === 'en-US'
          ? 'Unavailable · Last attempt: 1 min ago; host unreachable, last successful observation is stale'
          : '不可用 · 上次嘗試：1 分鐘前；無法連至主機，上次成功觀察已過期');
        expect(body.states[name].observed_at).toBe(1790000840);
      }
      expect(body.states.can_observe.verified.observed_at).toBe(1790000540);
      await expect.poll(() => reads.filter(path => path.endsWith('/readiness')).length).toBe(2);
      release();
      await expect(facts.locator('[data-work-profile-state="can_observe"] [data-work-profile-value]')).toHaveText(locale === 'en-US'
        ? 'Unavailable · 1 min ago · Could not confirm (OBSERVATION_NOT_RENEWED)'
        : '不可用 · 1 分鐘前 · 無法確認（OBSERVATION_NOT_RENEWED）');
      await page.clock.fastForward(600_000);
      expect(reads.filter(path => path.endsWith('/readiness'))).toHaveLength(2);
      expect(posts).toEqual([]);
    } finally {release();}
  });

  const body = profiles.cases.owned_execution;
  const deadline = 1790000720000;
  const copy = locale === 'en-US' ? {
    ready: 'Ready', checking: 'Ready · 3 min ago · Checking',
    failed: 'Ready · 3 min ago · Could not confirm (FIXTURE_READ_FAILED)',
    unchanged: 'Ready · 3 min ago · Could not confirm (OBSERVATION_NOT_RENEWED)',
    values: {ready: 'Ready', missing: 'Setup still missing', unknown: 'Not verified yet', unavailable: 'Unavailable'},
  } : {
    ready: '已就緒', checking: '已就緒 · 3 分鐘前 · 檢查中',
    failed: '已就緒 · 3 分鐘前 · 無法確認（FIXTURE_READ_FAILED）',
    unchanged: '已就緒 · 3 分鐘前 · 無法確認（OBSERVATION_NOT_RENEWED）',
    values: {ready: '已就緒', missing: '還缺設定', unknown: '尚未驗證', unavailable: '不可用'},
  };
  const value = facts => facts.locator('[data-work-profile-state="can_observe"] [data-work-profile-value]');
  const count = reads => reads.filter(path => path.endsWith('/readiness')).length;
  async function heldPicker(page) {
    await page.clock.install({time: new Date(1790000540000)});
    await page.clock.pauseAt(1790000540000);
    let release;
    const held = new Promise<void>(resolve => release = resolve);
    const response = {body: profiles.cases.renewed_observation || body, status: 200};
    const fixture = await mountProfilePicker(page, body, profiles.profile, locale, async (route, n) => {
      if (n > 1) await held;
      await route.fulfill({status: n === 1 ? 200 : response.status, json: n === 1 ? body : response.body});
    });
    await expect(value(fixture.facts)).toHaveText(copy.ready);
    return {...fixture, release, response};
  }
  test('Work renews once at the deadline and retains focus, scroll and disclosure', async ({page}) => {
    const f = await heldPicker(page);
    await page.evaluate(() => {
      const box = document.querySelector('[data-work-profile-picker]')!;
      const details = document.createElement('details'); details.open = true;
      const summary = document.createElement('summary'); summary.textContent = 'Profile';
      box.before(details); details.append(summary, box);
    });
    const link = f.facts.locator('[data-work-profile-state="last_verified_execution"] a');
    await link.focus();
    const scroll = await page.evaluate(() => scrollY);
    await page.clock.runFor(deadline - 1790000540000 - 1);
    expect(count(f.reads)).toBe(1);
    await page.clock.runFor(1);
    await expect(value(f.facts)).toHaveText(copy.checking);
    await expect.poll(() => count(f.reads)).toBe(2);
    await expect(link).toBeFocused();
    expect(await page.evaluate(() => scrollY)).toBe(scroll);
    f.release();
    await expect(value(f.facts)).toHaveText(copy.ready);
    await expect(link).toBeFocused();
    await expect(page.locator('details')).toHaveAttribute('open', '');
    expect(await page.evaluate(() => scrollY)).toBe(scroll);
    expect(count(f.reads)).toBe(2); expect(f.posts).toEqual([]);
    await expect(f.facts.locator('[data-work-profile-state]')).toHaveCount(6);
    await expect(f.facts).toContainText(locale === 'en-US' ? 'PM analysis' : 'PM 分析');
    await expect(f.facts).toContainText(locale === 'en-US' ? 'A saved setting is not a ready check.' : '已設定不等於就緒。');
    await expect(f.facts.locator('[data-work-profile-state="can_observe"] [data-work-profile-past]')).toContainText(locale === 'en-US' ? 'Last verified' : '上次驗證');
  });
  for (const failure of ['failed', 'unchanged']) test(`Work ${failure} renewal never retries on later clocks`, async ({page}) => {
    const f = await heldPicker(page);
    f.response.body = failure === 'failed' ? {error: {code: 'FIXTURE_READ_FAILED'}} : body;
    f.response.status = failure === 'failed' ? 503 : 200;
    await page.clock.runFor(deadline - 1790000540000);
    await expect(value(f.facts)).toHaveText(copy.checking);
    await expect.poll(() => count(f.reads)).toBe(2);
    f.release();
    await expect(value(f.facts)).toHaveText(copy[failure]);
    for (const elapsed of [60_000, 600_000, 3_600_000]) {
      await page.clock.fastForward(elapsed);
      expect(count(f.reads)).toBe(2);
      await expect(value(f.facts)).not.toContainText(locale === 'en-US' ? 'Checking' : '檢查中');
      if(elapsed===60_000)await expect(value(f.facts)).toHaveText(copy[failure].replace('3 min ago','4 min ago').replace('3 分鐘前','4 分鐘前'));
    }
    expect(f.posts).toEqual([]);
  });
  test('Work permits a new renewal only after a normal read supplies a new deadline', async ({page}) => {
    const f = await heldPicker(page);
    f.response.body = body;
    await page.clock.runFor(deadline - 1790000540000);
    await expect.poll(() => count(f.reads)).toBe(2);
    f.release();
    await expect(value(f.facts)).toHaveText(copy.unchanged);
    await page.evaluate(() => (window as any).__profilePicker.load());
    expect(count(f.reads)).toBe(3);
    await expect(value(f.facts)).toHaveText(copy.unchanged);
    f.response.body = profiles.cases.renewed_observation;
    await page.evaluate(() => (window as any).__profilePicker.load());
    expect(count(f.reads)).toBe(4);
    await expect(value(f.facts)).toHaveText(copy.ready);
    await page.clock.runFor(180_000);
    await expect.poll(() => count(f.reads)).toBe(5);
    await expect(value(f.facts)).toHaveText(copy.unchanged);
    await page.clock.fastForward(3_600_000);
    expect(count(f.reads)).toBe(5);
  });
  test('Work rejects a renewal for another workspace and removes the timer on disposal', async ({page}) => {
    const f = await heldPicker(page);
    f.response.body = {...profiles.cases.renewed_observation, workspace_id: 'ws_' + '2'.repeat(32)};
    await page.clock.runFor(deadline - 1790000540000);
    await expect.poll(() => count(f.reads)).toBe(2);
    f.release();
    await expect(value(f.facts)).toHaveText(locale === 'en-US'
      ? 'Ready · 3 min ago · Could not confirm (READ_FAILED)' : '已就緒 · 3 分鐘前 · 無法確認（READ_FAILED）');
    f.response.body = profiles.cases.renewed_observation;
    await page.evaluate(() => (window as any).__profilePicker.load());
    expect(count(f.reads)).toBe(3);
    await page.evaluate(() => (window as any).__profilePicker.dispose());
    await page.clock.fastForward(3_600_000);
    expect(count(f.reads)).toBe(3); await expect(f.facts).toBeEmpty();
  });
  test('Work waits while hidden and renews once on return', async ({page}) => {
    const f = await heldPicker(page);
    await page.evaluate(() => {Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange'));});
    await page.clock.runFor(600_000);
    expect(count(f.reads)).toBe(1);
    await page.evaluate(() => {Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange'));});
    await expect.poll(() => count(f.reads)).toBe(2);
    f.release();
  });
  test('Work ignores a held renewal after selection changes', async ({page}) => {
    const f = await heldPicker(page);
    await page.clock.runFor(deadline - 1790000540000);
    await expect.poll(() => count(f.reads)).toBe(2);
    const select = page.locator('[data-work-profile-picker] select');
    await select.selectOption('');
    const response = page.waitForResponse(response => response.url().endsWith('/readiness'));
    f.release();
    // Wait for the response to be processed before checking the stale ticket.
    await response;
    await page.clock.runFor(1);
    await expect(select).toHaveValue(''); await expect(f.facts).toBeEmpty();
    expect(count(f.reads)).toBe(2);
  });
  test('Work returning to a selection cannot leave a discarded renewal permanently Checking', async ({page}) => {
    const f = await heldPicker(page);
    f.response.body = body;
    await page.clock.runFor(deadline - 1790000540000);
    await expect.poll(() => count(f.reads)).toBe(2);
    const select = page.locator('[data-work-profile-picker] select');
    await select.selectOption('');
    f.release();
    await select.selectOption(body.profile_id);
    await expect(value(f.facts)).toHaveText(copy.unchanged);
    expect(count(f.reads)).toBe(3);
    await page.clock.fastForward(600_000);
    expect(count(f.reads)).toBe(3);
  });
  for (const [name, data] of Object.entries(profiles.cases) as [string, any][]) {
    test(`Work ${name}: undeclared states never show Checking`, async ({page}) => {
      await page.clock.install({time: new Date(1790000540000)});
      await page.clock.pauseAt(1790000540000);
      const {facts, posts} = await mountProfilePicker(page, data, profiles.profile, locale);
      for (const elapsed of [0, 60_000, 600_000, 3_600_000]) {
        await page.clock.fastForward(elapsed);
        for (const [key, state] of Object.entries(data.states) as [string, any][]) if (!state.valid_until) {
          await expect(facts.locator(`[data-work-profile-state="${key}"] [data-work-profile-value]`)).toHaveText(copy.values[state.value]);
        }
      }
      expect(posts).toEqual([]);
    });
  }
});
