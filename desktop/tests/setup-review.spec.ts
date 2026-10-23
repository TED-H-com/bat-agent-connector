import {test, expect, type Page} from '@playwright/test';
import {managedSetupFixture, reviewSetupConnection, expectLegacyProfileFallback, openSetupAdvanced, applySetupReview} from './managed-setup-fixture.ts';

const connectionUrl = `ws://127.0.0.1?token=synthetic-bat-secret&fp=${'ab'.repeat(32)}`;
const field = (page: Page, name: string) => page.locator(`[data-setup-field=${name}]`);
async function paste(page: Page, value: string) {
  await field(page, 'connection_url').evaluate((node, text) => {
    const event = new Event('paste', {bubbles: true, cancelable: true});
    Object.defineProperty(event, 'clipboardData', {value: {getData: () => text}});
    node.dispatchEvent(event);
  }, value);
}

for (const locale of ['en', 'zh-TW']) test.describe(`${locale} setup review`, () => {
  test.use({locale});
  for (const native of [false, true]) {
    test(`paste confirms safe values and clears on manual edits or another paste (${native ? 'native' : 'web'})`, async ({page}) => {
      const state = await managedSetupFixture(page, native, locale); await page.goto('/dashboard/'); await openSetupAdvanced(page);
      const feedback = page.locator('[data-setup-paste-read]');
      await expect(feedback).toBeHidden(); await paste(page, connectionUrl);
      await expect(field(page, 'connection_url')).toHaveValue('');
      await expect(feedback).toHaveText(locale === 'en'
        ? '✓ Read from BAT: 127.0.0.1:9876 · fingerprint ABAB…ABAB · token ready'
        : '✓ 已從 BAT 讀取：127.0.0.1:9876 · 指紋 ABAB…ABAB · 權杖已備妥');
      await expect(field(page, 'connection_url')).toHaveAttribute('aria-describedby', /setup-connection_url-read/);
      expect(await page.locator('[data-managed-setup]').innerText()).not.toMatch(/synthetic-bat-secret|token=|ABABABAB/);
      await page.locator('[data-setup-manual] > summary').click();
      await field(page, 'url').fill('wss://localhost:9876'); await expect(feedback).toBeHidden();
      await paste(page, connectionUrl); await expect(feedback).toBeVisible();
      await page.locator('[data-setup-manual] > summary').click();
      await field(page, 'fingerprint').fill('cd'.repeat(32)); await expect(feedback).toBeHidden();
      await paste(page, `localhost:9999?token=second-synthetic-secret&fp=${'cd'.repeat(32)}`);
      await expect(feedback).toHaveText(locale === 'en'
        ? '✓ Read from BAT: localhost:9999 · fingerprint CDCD…CDCD · token ready'
        : '✓ 已從 BAT 讀取：localhost:9999 · 指紋 CDCD…CDCD · 權杖已備妥');
      await paste(page, 'malformed BAT URL with synthetic-secret'); await expect(feedback).toBeHidden();
      await expect(page.locator('[data-setup-error=connection_url]')).toHaveText(locale === 'en'
        ? 'This is not a BAT connection URL' : '這不是 BAT 連線網址');
      expect(await page.locator('[data-managed-setup]').innerText()).not.toContain('malformed BAT URL');
      expect(await page.evaluate(() => JSON.stringify({...localStorage, ...sessionStorage}))).not.toMatch(/synthetic.*secret|token=/);
      expect(state.posts).toEqual([]); expect(state.staged).toEqual([]);
    });

    test(`one page Refresh reloads status while preserving the credential and draft (${native ? 'native' : 'web'})`, async ({page}) => {
      const state = await managedSetupFixture(page, native, locale); await page.goto('/dashboard/#/settings'); await openSetupAdvanced(page);
      await paste(page, connectionUrl); await field(page, 'host').fill('my-local');
      const refresh = page.locator('#main').getByRole('button', {name: locale === 'en' ? 'Refresh' : '重新整理', exact: true});
      await expect(refresh).toHaveCount(1);
      await expect(page.locator('#main > .ui-page-header').getByRole('button', {name: locale === 'en' ? 'Refresh' : '重新整理', exact: true})).toBeVisible();
      const reads = state.reads; state.saved = true; await refresh.click();
      await expect.poll(() => state.reads).toBe(reads + 1);
      await expect(page.locator('[data-managed-setup]')).toContainText(locale === 'en'
        ? 'Configured: 1 host, 0 repositories' : '已設定 1 台主機、0 個儲存庫');
      await expect(page.locator('[data-setup-sessions]')).toBeVisible();
      await expect(field(page, 'host')).toHaveValue('my-local');
      await expect(field(page, 'bat_secret')).toHaveValue('synthetic-bat-secret');
      await expect(field(page, 'url')).toHaveValue('wss://127.0.0.1:9876');
      expect(state.posts).toEqual([]); expect(state.staged).toEqual([]);
    });
  }

  for (const width of [1440, 1000, 390]) test(`connected actions, receipt and step spacing at ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 900});
    const state = await managedSetupFixture(page, false, locale); await page.goto('/dashboard/'); await openSetupAdvanced(page);
    const panel = page.locator('[data-managed-setup]');
    const firstGap = await panel.evaluate(node => {
      const step = node.querySelector('[data-setup-connect]')!;
      const submit = step.querySelector('button[type=submit]')!.getBoundingClientRect();
      return step.nextElementSibling!.getBoundingClientRect().top - submit.bottom;
    });
    expect(firstGap).toBe(16);
    await paste(page, connectionUrl); await reviewSetupConnection(page,locale); await applySetupReview(page);
    const sessions = panel.getByRole('link', {name: locale === 'en' ? 'View sessions' : '查看工作階段', exact: true});
    const change = panel.getByRole('button', {name: locale === 'en' ? 'Change settings' : '修改設定', exact: true});
    await expect(sessions).toBeVisible(); await expect(change).toBeVisible();
    const a = (await sessions.boundingBox())!, b = (await change.boundingBox())!;
    expect(Math.abs(a.y - b.y)).toBeLessThan(1); expect(b.x - a.x - a.width).toBe(8);
    const laterGaps = await panel.evaluate(node => {
      const step = node.querySelector('[data-setup-connect]')!;
      const steps = [step, step.nextElementSibling!, step.nextElementSibling!.nextElementSibling!];
      const centers = steps.map(detail => {
        const rect = detail.querySelector('summary')!.getBoundingClientRect();
        return (rect.top + rect.bottom) / 2;
      });
      return {betweenSteps: step.nextElementSibling!.getBoundingClientRect().top - step.getBoundingClientRect().bottom,
        afterActions: node.querySelector('[data-setup-advanced]')!.getBoundingClientRect().top - node.querySelector('[data-setup-actions]')!.getBoundingClientRect().bottom,
        centerGaps: [centers[1] - centers[0], centers[2] - centers[1]]};
    });
    expect(laterGaps.betweenSteps).toBe(0); expect(laterGaps.afterActions).toBe(16);
    expect(laterGaps.centerGaps[0]).toBe(laterGaps.centerGaps[1]);
    const receipt = panel.locator('[data-setup-operation]');
    await expect(receipt).toBeHidden(); expect(await panel.innerText()).not.toMatch(/\bdone\b|Setup operation|設定操作/);
    await panel.locator('[data-setup-details] > summary').click();
    await expect(receipt).toHaveText(locale === 'en' ? 'Setup operation · Done' : '設定操作 · 完成');
    await expect(receipt).toBeVisible(); await expect(receipt).toHaveAttribute('href', '#/op/' + 'op_' + 'c'.repeat(32));
    expect(state.posts).toHaveLength(1); expect(state.staged).toHaveLength(1);
  });

  for (const width of [1440, 1000, 390]) test(`pasted and refused setup keeps space only after the action at ${width}`, async ({page}) => {
    await page.setViewportSize({width, height: 900});
    const state = await managedSetupFixture(page, false, locale); await page.goto('/dashboard/'); await openSetupAdvanced(page);
    const panel = page.locator('[data-managed-setup]'); await paste(page, connectionUrl);
    const gaps = () => panel.evaluate(node => {
      const step = node.querySelector('[data-setup-connect]')!;
      const second = step.nextElementSibling!, third = second.nextElementSibling!;
      return {afterAction: second.getBoundingClientRect().top - step.querySelector('button[type=submit]')!.getBoundingClientRect().bottom,
        betweenSteps: third.getBoundingClientRect().top - second.getBoundingClientRect().bottom};
    });
    await expect(panel.locator('[data-setup-paste-read]')).toContainText(locale === 'en' ? 'Read from BAT' : '已從 BAT 讀取');
    await expect(panel.locator('[data-setup-actions]')).toBeHidden();
    expect(await gaps()).toEqual({afterAction: 16, betweenSteps: 0});
    state.refused = true; state.refusalCode = 'HOST_IDENTITY_CHANGED'; state.refusalStatus = 409;
    await reviewSetupConnection(page,locale); await applySetupReview(page);
    await expect(panel.locator('[data-setup-error=host]')).toHaveText(locale === 'en'
      ? 'Use a new host ID for changed connection or trust settings.' : '此主機的連線設定已變更，請使用新的主機代號。');
    await expect(field(page, 'host')).toBeEditable(); await expect(panel.locator('[data-setup-actions]')).toBeHidden();
    expect(await gaps()).toEqual({afterAction: 16, betweenSteps: 0});
    expect(state.posts).toHaveLength(1); expect(state.staged).toHaveLength(1);
  });

  test('configured counts distinguish singular and plural without changing zh-TW', async ({page}) => {
    const state = await managedSetupFixture(page, false, locale); await page.goto('/dashboard/'); await openSetupAdvanced(page);
    const panel = page.locator('[data-managed-setup]');
    await expect(panel).toContainText(locale === 'en' ? 'Configured: 0 hosts, 0 repositories' : '已設定 0 台主機、0 個儲存庫');
    state.saved = true; state.repositories = [{repository: 'fixture/one'}];
    const refresh = page.locator('#main > .ui-page-header').getByRole('button', {name: locale === 'en' ? 'Refresh' : '重新整理', exact: true});
    await refresh.click(); await expect(panel).toContainText(locale === 'en' ? 'Configured: 1 host, 1 repository' : '已設定 1 台主機、1 個儲存庫');
    state.extraHosts = [{name: 'second', url: 'wss://second.example:9876', connected: true}];
    state.repositories.push({repository: 'fixture/two'});
    await refresh.click(); await expect(panel).toContainText(locale === 'en' ? 'Configured: 2 hosts, 2 repositories' : '已設定 2 台主機、2 個儲存庫');
    expect(state.posts).toEqual([]); expect(state.staged).toEqual([]);
  });

  test('a proven identity refusal explains the valid Host ID and allows correcting it', async ({page}) => {
    const state = await managedSetupFixture(page, false, locale);
    state.refused = true; state.refusalCode = 'HOST_IDENTITY_CHANGED'; state.refusalStatus = 409;
    await page.goto('/dashboard/'); await openSetupAdvanced(page); await paste(page, connectionUrl);
    await reviewSetupConnection(page,locale); await applySetupReview(page);
    await expect(field(page, 'host')).toHaveValue('local'); await expect(field(page, 'host')).toBeEditable();
    await expect(page.locator('[data-setup-error=host]')).toHaveText(locale === 'en'
      ? 'Use a new host ID for changed connection or trust settings.'
      : '此主機的連線設定已變更，請使用新的主機代號。');
    await expect(page.getByRole('button', {name: locale === 'en' ? 'Check request' : '查回請求', exact: true})).toBeHidden();
    state.refused = false; await field(page, 'host').fill('local-new');
    await reviewSetupConnection(page,locale); await applySetupReview(page);
    // The saved operation does not supply a matching current observation.
    await expect(page.locator('[data-setup-host-state][data-host="local-new"] h3')).toHaveText('local-new');
    await expect(page.locator('[data-setup-facet="connection"] [data-setup-fact-value]')).toHaveText(locale === 'en'
      ? 'Not verified yet · Checking' : '尚未驗證 · 檢查中');
    expect(state.posts).toHaveLength(2); expect(state.staged).toHaveLength(1);
    expect(state.posts[1].idempotency_key).not.toBe(state.posts[0].idempotency_key);
  });
});

for (const locale of ['en', 'zh-TW']) test(`${locale} older Connector keeps the typed profile fallback`, async ({page}) => {
  await expectLegacyProfileFallback(page, locale);
});
