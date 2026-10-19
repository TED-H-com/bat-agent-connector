import {test, expect} from '@playwright/test';
import {managedSetupFixture} from './managed-setup-fixture.ts';
for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) test.describe(`${locale} ${native ? 'native' : 'web'} simple setup`, () => {
  test.use({locale});
  const automatic = locale === 'en' ? 'Set up automatically' : '自動設定';
  test('one click uses a saved connection with safe defaults and no technical fields', async ({page}) => {
    const state = await managedSetupFixture(page, native, locale); await page.goto('/dashboard/');
    await expect(page.locator('[data-language-switch]')).toBeVisible();
    await expect(page.locator('[data-setup-advanced]')).not.toHaveAttribute('open', '');
    // A previous advanced draft must not silently grant permissions on the simple path.
    await page.locator('[data-setup-host] input[type=checkbox]').evaluateAll((nodes: HTMLInputElement[]) => {for (const node of nodes) node.checked = true;});
    for (const field of ['host', 'url', 'fingerprint', 'bat_secret', 'ssh_alias', 'managed_roots', 'connection_url'])
      await expect(page.locator(`[data-setup-field=${field}]`)).toBeHidden();
    await page.getByRole('button', {name: automatic, exact: true}).click();
    await expect(page.locator('[data-setup-sessions]')).toBeVisible();
    await expect(page.locator('[data-setup-progress]')).toHaveText(locale === 'en' ? 'Setup is complete. You’re ready to go.' : '設定完成，可以開始使用了。');
    expect(state.posts).toHaveLength(1); expect(state.staged).toEqual([]);
    expect(state.posts[0].body).toMatchObject({action: 'setup.host', target: {host: 'bat'}, params: {
      import_profile_id: 'profile-demo', writes: false, orchestrate: false, shared_clone_worktrees: false, managed_roots: []}});
    await page.reload(); await expect(page.locator('[data-setup-simple]')).toBeHidden();
    expect(state.posts).toHaveLength(1); // Existing installs never re-run setup.
  });
  test('missing profiles use only a masked BAT link, clear invalid input, and keep secrets out of storage', async ({page}) => {
    const state = await managedSetupFixture(page, native, locale); state.hasProfiles = false;
    await page.goto('/dashboard/'); await page.getByRole('button', {name: automatic, exact: true}).click();
    const copy = page.locator('[data-setup-copy]'), input = copy.locator('input');
    await expect(input).toHaveAttribute('type', 'password');
    await input.fill('bad-link-with-synthetic-secret'); await copy.locator('button').click();
    await expect(input).toHaveValue(''); await expect(copy.locator('[role=alert]')).toHaveText(locale === 'en' ? 'This is not a BAT connection URL' : '這不是 BAT 連線網址');
    expect(state.posts).toEqual([]); expect(state.staged).toEqual([]);
    await input.fill(`ws://demo.example:9876?token=synthetic-bat-secret&fp=${'ab'.repeat(32)}`);
    await expect(input).toHaveValue('');
    await expect(copy.locator('[role=status]')).toBeVisible();
    expect(state.staged).toEqual([]);
    await copy.locator('button').click(); await expect(page.locator('[data-setup-sessions]')).toBeVisible();
    expect(state.posts).toHaveLength(1); expect(state.staged).toHaveLength(1);
    expect(state.posts[0].body.params.url).toBe('wss://demo.example:9876');
    expect(await page.evaluate(() => JSON.stringify({...localStorage, ...sessionStorage}))).not.toMatch(/synthetic.*secret|token=/);
    expect(JSON.stringify(state.posts)).not.toMatch(/synthetic.*secret|token=/);
    await expect(page.locator('[data-setup-advanced]')).not.toHaveAttribute('open', '');
  });
  test('ambiguous saved connections require a plain-language choice before any write', async ({page}) => {
    const state = await managedSetupFixture(page, native, locale);
    state.profiles = ['one', 'two'].map(id => ({id, name: id, url: `wss://${id}.example:9876`, fingerprint: 'ab'.repeat(32), profile_id: 'default', token_available: true}));
    await page.goto('/dashboard/'); await page.getByRole('button', {name: automatic, exact: true}).click();
    expect(state.posts).toEqual([]);
    await page.locator('[data-setup-simple] select').selectOption('two');
    await page.getByRole('button', {name: automatic, exact: true}).click();
    await expect(page.locator('[data-setup-sessions]')).toBeVisible();
    expect(state.posts[0].body.params.import_profile_id).toBe('two');
  });
  test('automatic setup recovers an uncertain result using the exact original request', async ({page}) => {
    const state = await managedSetupFixture(page, native, locale); state.lost = true;
    await page.goto('/dashboard/'); await page.getByRole('button', {name: automatic, exact: true}).click();
    await expect(page.locator('[data-error-summary]')).toHaveText(locale === 'en' ? 'This request could not be completed. Check Details.' : '無法完成此要求，請查看詳細資料。');
    const original = structuredClone(state.posts[0]);
    await page.reload(); await expect(page.getByRole('button', {name: automatic, exact: true})).toBeDisabled();
    await page.getByRole('button', {name: locale === 'en' ? 'Check request' : '查回請求', exact: true}).click();
    await expect(page.locator('[data-setup-sessions]')).toBeVisible(); expect(state.posts[1]).toEqual(original);
  });
});
for (const language of ['zh-TW', 'zh-Hant', 'zh-HK', 'zh-MO', 'zh-CN', 'en-US']) test(`OS language ${language}, explicit switch and persisted choice`, async ({browser}) => {
  const context = await browser.newContext({locale: language}), page = await context.newPage();
  await managedSetupFixture(page, false, null); await page.goto('/dashboard/');
  const zh = ['zh-TW', 'zh-Hant', 'zh-HK', 'zh-MO'].includes(language);
  const switcher = page.locator('[data-language-switch]');
  await expect(page.getByRole('button', {name: zh ? '自動設定' : 'Set up automatically', exact: true})).toBeVisible();
  await switcher.getByRole('button', {name: zh ? 'English' : '繁體中文', exact: true}).click();
  await expect(page.getByRole('button', {name: zh ? 'Set up automatically' : '自動設定', exact: true})).toBeVisible();
  await page.reload();
  await expect(switcher.getByRole('button', {name: zh ? 'English' : '繁體中文', exact: true})).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('html')).toHaveAttribute('lang', zh ? 'en' : 'zh-TW');
  await context.close();
});

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) test(`${locale} ${native ? 'native' : 'web'} Setup reports observation evidence rather than an idle write socket`, async ({page}) => {
  const state = await managedSetupFixture(page, native, locale); state.saved = true; state.connected = false;
  state.connectionStatus = 'connected'; // Writer pool empty, current session inventory succeeds.
  await page.goto('/dashboard/#/settings');
  const status = page.locator('[data-setup-host-status]');
  await expect(status).toHaveText(locale === 'en' ? 'demo · Connected' : 'demo · 已連線');
  const refresh = page.locator('#main > .ui-page-header button');
  state.connectionStatus = 'checking'; await refresh.click();
  await expect(status).toContainText(locale === 'en' ? 'Checking…' : '確認連線中…');
  await expect(status).not.toContainText(locale === 'en' ? 'Can’t reach' : '無法連線');
  state.connectionStatus = 'unreachable'; await refresh.click();
  await expect(status).toContainText(locale === 'en' ? 'Can’t reach this connection' : '目前無法連線');
  await expect(status).toContainText(locale === 'en' ? 'Make sure BAT is open, then select Refresh.' : '請確認 BAT 已開啟，再按「重新整理」。');
  state.connectionStatus = 'connected'; await refresh.click();
  await expect(status.locator('.muted')).toHaveCount(0);
  expect(state.posts).toEqual([]); expect(state.staged).toEqual([]);
});
