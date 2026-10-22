import {test, expect, type Page} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {attentionFixture} from './attention-fixture.ts';

async function fixture(page: Page, locale: string, capability = true) {
  const base = attentionFixture();
  const state = {reads: [] as any[], previews: [] as any[], posts: [] as any[]};
  const caps = () => ({...base.caps(), scopes: ['observe', 'manage'], hosts: [],
    managed_installation: {runtime_version: '0.2.4', background: true},
    actions: ['host', 'repository', 'verification'].map(kind => ({action: `setup.${kind}`, allowed: true}))});
  await page.addInitScript(locale => {localStorage.setItem('batc.lang', locale); sessionStorage.setItem('batc.dashboard.token', 'fixture-token');}, locale);
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname.slice(7);
    let result: any;
    if (path === '/capabilities') result = {status: 200, data: caps()};
    else if (path === '/bootstrap') result = {status: 200, data: {capabilities: caps(), sync: {version: 1,
      server_id: 'server-a', principal_id: 'person-a', checkpoint: {cursor: 0, token: 'checkpoint-a'}}}};
    else if (path === '/managed/setup') result = {status: 200, data: {revision: 'a'.repeat(64), preview_contract_version: 1,
      ...(capability ? {discovery_contract_version: 1} : {}), hosts: [], repositories: [], profiles: [],
      profiles_error: null, verification: {commands: {}, timeout_s: 600}, busy: false}};
    else if (path === '/managed/setup/secrets') result = {status: 200, data: {kind: 'bat', secret_ref: 'setup_' + 'b'.repeat(32)}};
    else if (path === '/managed/setup/discover') {
      state.reads.push(req.postDataJSON());
      const golden = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-setup-discovery.json', import.meta.url), 'utf8'));
      result = {status: 200, data: golden[req.postDataJSON().profile_id || 'initial'].response};
    } else if (path === '/managed/setup/preview') {
      state.previews.push(req.postDataJSON());
      const golden = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-setup-discovery.json', import.meta.url), 'utf8'));
      result = {status: 200, data: golden.work.review};
    } else if (path === '/operations' && req.method() === 'POST') {state.posts.push(req.postDataJSON()); throw Error('No Apply in discovery fixture');}
    else result = await base.dispatch({method: req.method(), path, body: req.postData() ? req.postDataJSON() : null});
    await route.fulfill({status: result.status, json: result.data});
  });
  return state;
}

for (const locale of ['en', 'zh-TW']) test.describe(`${locale} source discovery`, () => {
  const find = locale === 'en' ? 'Find profiles and workspaces' : '尋找設定檔與工作區';
  test('pick a server profile, review it, and clear choices on URL edit', async ({page}) => {
    const state = await fixture(page, locale);
    await page.goto('/dashboard/');
    await page.locator('[data-setup-advanced] > summary').click();
    await page.locator('[data-setup-field=connection_url]').fill(`wss://host-a.example.invalid:9876?token=synthetic-bat-secret&fp=${'A'.repeat(64)}`);
    expect(state.reads).toEqual([]);
    await expect(page.locator('[data-setup-discover]')).toHaveText(find);
    await page.locator('[data-setup-discover]').click();
    const picker = page.locator('[data-setup-discovered-profile]');
    await expect(picker).toBeVisible();
    await expect(page.locator('[data-setup-field=profile_id]')).toBeHidden();
    await picker.selectOption('work');
    await expect(page.locator('[data-setup-discovered-workspaces]')).toContainText('demo-project');
    await page.locator('[data-setup-host] button[type=submit]').click();
    await expect(page.locator('[data-setup-review]')).toContainText('work');
    await expect(page.locator('[data-setup-review] h3')).toHaveText(locale === 'en' ? 'Review connection' : '預覽連線');
    expect(state.previews[0].params.profile_id).toBe('work');
    expect(state.posts).toEqual([]);
    await page.locator('[data-setup-manual] > summary').click();
    await page.locator('[data-setup-field=url]').fill('wss://host-b.example.invalid:9876');
    await expect(picker).toBeHidden();
    await expect(page.locator('[data-setup-review]')).toBeHidden();
    await expect(page.locator('[data-setup-progress]')).toHaveText(locale === 'en' ? 'Settings changed; review again' : '設定已變更，請重新預覽');
    const draft = await page.evaluate(() => JSON.stringify({...localStorage, ...sessionStorage}));
    expect(draft).not.toContain('synthetic-bat-secret'); expect(draft).not.toContain('source_digest');
  });
  test('a Connector without discovery retains the typed profile fallback', async ({page}) => {
    await fixture(page, locale, false); await page.goto('/dashboard/');
    await page.locator('[data-setup-advanced] > summary').click();
    await page.locator('[data-setup-manual] > summary').click();
    await expect(page.locator('[data-setup-field=profile_id]')).toBeVisible();
    await expect(page.getByRole('button', {name: find, exact: true})).toBeHidden();
  });
  test('the simple entry can refresh ambiguous choices before Review', async ({page}) => {
    const state = await fixture(page, locale); await page.goto('/dashboard/');
    await page.locator('.setup-automatic').click();
    const copy = page.locator('[data-setup-copy]');
    await copy.locator('input').fill(`wss://host-a.example.invalid:9876?token=synthetic-bat-secret&fp=${'A'.repeat(64)}`);
    await expect(copy.locator('button')).toHaveText(find);
    await copy.locator('button').click();
    await expect(page.locator('[data-setup-discovered-profile]')).toBeVisible();
    await expect(page.locator('.setup-automatic')).toHaveText(find);
    await page.locator('.setup-automatic').click();
    await expect.poll(() => state.reads.length).toBe(2);
    await page.locator('[data-setup-discovered-profile]').selectOption('work');
    await expect(page.locator('.setup-automatic')).toHaveText(locale === 'en' ? 'Review connection' : '預覽連線');
    await page.locator('.setup-automatic').click();
    await expect(page.locator('[data-setup-review]')).toContainText('work');
    expect(state.posts).toEqual([]);
  });
  test('staged credential expiry clears discovery and review', async ({page}) => {
    await page.clock.install();
    await fixture(page, locale); await page.goto('/dashboard/');
    await page.locator('[data-setup-advanced] > summary').click();
    await page.locator('[data-setup-field=connection_url]').fill(`wss://host-a.example.invalid:9876?token=synthetic-bat-secret&fp=${'A'.repeat(64)}`);
    await page.locator('[data-setup-discover]').click();
    await page.locator('[data-setup-discovered-profile]').selectOption('work');
    await page.locator('[data-setup-host] button[type=submit]').click();
    await expect(page.locator('[data-setup-review]')).toBeVisible();
    await page.clock.fastForward(3600001);
    await expect(page.locator('[data-setup-discovery]')).toBeHidden();
    await expect(page.locator('[data-setup-review]')).toBeHidden();
  });
});
