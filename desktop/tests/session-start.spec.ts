import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {startFixture, openStart, chooseWorkspace, startOperationId} from './session-start-fixture';
const start = (form: any) => form.getByRole('button', {name: 'Start session', exact: true});
const prompt = (form: any) => form.getByRole('textbox', {name: 'Original instructions (optional)', exact: true});
for (const native of [false, true]) {
  const transport = native ? 'native' : 'browser';
  test(`${transport}: exact intent survives lost reply, changed policy and accepted reload`, async ({page}) => {
    const state = await startFixture(page, native, {lost: true}); const form = await openStart(page);
    await expect(start(form)).toBeDisabled(); await chooseWorkspace(page);
    await form.getByRole('combobox', {name: 'Agent', exact: true}).selectOption('codex');
    await prompt(form).fill('  original\nunchanged.  ');
    await form.getByRole('textbox', {name: 'Model (optional)', exact: true}).fill('chosen-model');
    await start(form).click(); await expect(form).toContainText('Lost start reply');
    const posted = state.posts[0];
    expect(posted.body).toEqual({action: 'session.start', target: {host: 'demo', workspace: 'demo-ws'},
      params: {agent: 'codex', use_worktree: true, model: 'chosen-model', prompt: '  original\nunchanged.  '}, preconditions: {}});
    const stored = await page.evaluate(() => JSON.parse(Object.entries(localStorage).find(([key]) => key.startsWith('batc.start.'))![1]).intent);
    expect(stored.key).toBe(posted.idempotency_key); expect(stored.request).toEqual(posted.body);
    state.writes = false; state.orchestrate = false; await page.reload();
    await expect(form.getByRole('combobox', {name: 'Agent', exact: true})).toBeDisabled(); expect(state.posts).toHaveLength(1);
    await form.getByRole('button', {name: 'Retry original request'}).click(); await expect(form).toContainText('Initial instructions were accepted');
    expect(state.posts[1]).toEqual(posted);
    const discoveries = state.reads.filter(r => r.startsWith('/workspaces')).length;
    await page.reload(); await expect(form).toContainText('Start confirmed'); expect(state.posts).toHaveLength(2);
    expect(state.reads.filter(r => r.startsWith('/workspaces'))).toHaveLength(discoveries);
    await form.getByRole('button', {name: 'Prepare another session'}).click(); await expect(prompt(form)).toHaveValue('');
    expect(state.posts).toHaveLength(2); expect(state.errors).toEqual([]);
  });
  test(`${transport}: unproven initial prompt remains partial after reload`, async ({page}) => {
    const state = await startFixture(page, native, {status: 'uncertain'}); const form = await openStart(page); await chooseWorkspace(page);
    await prompt(form).fill('original'); await start(form).click(); await expect(form).toContainText('Started, but acceptance of the initial instructions is unconfirmed');
    await expect(form.getByRole('button', {name: 'Prepare another session'})).toBeHidden();
    await page.reload(); await expect(form).toContainText('Started, but acceptance');
    await form.getByRole('button', {name: 'Check original operation'}).click(); expect(state.posts).toHaveLength(1);
  });
  for (const bad of ['key', 'actor', 'host', 'prompt', 'preconditions', 'result']) test(`${transport}: mismatched ${bad} receipt is not adopted`, async ({page}) => {
    const state = await startFixture(page, native, {bad}); const form = await openStart(page); await chooseWorkspace(page);
    await start(form).click(); await expect(form).toContainText('does not match');
    await expect(form.getByRole('link', {name: 'View operation and step receipts'})).toHaveCount(0);
    await expect(form.getByRole('button', {name: 'Prepare another session'})).toBeHidden();
    state.bad = null; await form.getByRole('button', {name: 'Retry original request'}).click();
    await expect(form).toContainText('No initial instructions were requested'); expect(state.posts[1]).toEqual(state.posts[0]);
  });
  test(`${transport}: proven tier refusal permits explicit replacement, auth and conflict do not`, async ({page}) => {
    const state = await startFixture(page, native, {refuse: 'TIER_DISABLED'}); const form = await openStart(page); await chooseWorkspace(page);
    await start(form).click(); await expect(form).toContainText('TIER_DISABLED'); await page.reload();
    await expect(form.getByRole('button', {name: 'Prepare another session'})).toBeEnabled(); expect(state.posts).toHaveLength(1);
    await form.getByRole('button', {name: 'Prepare another session'}).click();
    await form.getByRole('combobox', {name: 'Workspace', exact: true}).selectOption('demo-ws'); state.refuse = null; state.lost = true;
    await start(form).click(); await expect(form).toContainText('Lost start reply'); expect(state.posts[1].idempotency_key).not.toBe(state.posts[0].idempotency_key);
    for (const code of ['FORBIDDEN', 'IDEMPOTENCY_CONFLICT', 'UNKNOWN_ACTION']) {
      state.refuse = code; await form.getByRole('button', {name: 'Retry original request'}).click();
      await expect(form.locator('.error')).toBeVisible(); await expect(form.getByRole('button', {name: 'Prepare another session'})).toBeHidden();
      expect(state.posts.at(-1)).toEqual(state.posts[1]);
    }
  });
  test(`${transport}: event joins held submission and failed read cannot advance cursor`, async ({page}) => {
    const state = await startFixture(page, native, {delayPost: true, status: 'running'}); const form = await openStart(page); await chooseWorkspace(page);
    await start(form).click(); await expect.poll(() => Boolean(state.holdPost)).toBe(true);
    state.failRead = true; state.status = 'uncertain'; state.events.push({seq: 1, kind: 'operation.uncertain', resource_type: 'operation', resource_id: startOperationId});
    await expect.poll(() => state.delivered).toBe(true); expect(state.after).toBe(0);
    state.holdPost(); await expect(form).toContainText('Start read failed'); expect(state.after).toBe(0);
    state.failRead = false; await expect.poll(() => state.after).toBe(1); expect(state.posts).toHaveLength(1);
  });
  test(`${transport}: late discovery cannot select the previous host`, async ({page}) => {
    const state = await startFixture(page, native, {delayHost: 'demo'}); const form = await openStart(page);
    await form.getByRole('combobox', {name: 'Host', exact: true}).selectOption('demo'); await expect.poll(() => Boolean(state.holdDiscovery)).toBe(true);
    await chooseWorkspace(page, 'other'); state.holdDiscovery(); await expect(form.getByRole('combobox', {name: 'Workspace', exact: true})).toHaveValue('other-ws');
    await start(form).click(); await expect(form).toContainText('Start confirmed'); expect(state.posts[0].body.target).toEqual({host: 'other', workspace: 'other-ws'});
  });
  test(`${transport}: backend and principal changes isolate frozen drafts`, async ({page}) => {
    const state = await startFixture(page, native, {lost: true}); const form = await openStart(page); await chooseWorkspace(page);
    await prompt(form).fill('old private draft'); await start(form).click(); await expect(form).toContainText('Lost start reply');
    state.principal = 'another-principal'; await page.reload(); await expect(prompt(form)).toHaveValue(''); expect(state.posts).toHaveLength(1);
    state.principal = 'start-principal'; state.server = 'another-server'; await page.reload(); await expect(prompt(form)).toHaveValue('');
    state.server = 'start-server'; await page.reload(); await expect(prompt(form)).toHaveValue('old private draft'); expect(state.posts).toHaveLength(1);
  });
  test(`${transport}: missing scope or unproven discovery cannot start`, async ({page}) => {
    const state = await startFixture(page, native, {scopes: ['observe']}); const form = await openStart(page); await chooseWorkspace(page);
    await expect(start(form)).toBeDisabled(); state.scopes = ['observe', 'start']; state.orchestrate = false; await page.reload(); await expect(start(form)).toBeDisabled();
    state.orchestrate = true; state.discoveryError = true; await page.reload(); await expect(form).toContainText('Workspace discovery is unconfirmed');
    await expect(start(form)).toBeDisabled(); expect(state.posts).toEqual([]);
  });
}
test('storage failure prevents first start request', async ({page}) => {
  const state = await startFixture(page, false); const form = await openStart(page); await chooseWorkspace(page);
  await page.evaluate(() => {const original = Storage.prototype.setItem; Storage.prototype.setItem = function(k, v) {
    if (k.startsWith('batc.start.')) throw new Error('Storage unavailable'); return original.call(this, k, v);
  };});
  await start(form).click(); await expect(form).toContainText('Storage unavailable'); expect(state.posts).toEqual([]);
});
for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) test(`start form fits ${locale} ${width}`, async ({browser}) => {
  const context = await browser.newContext({locale, viewport: {width, height: 900}}), page = await context.newPage();
  const state = await startFixture(page, false); await openStart(page);
  await page.locator('[data-session-start] select').nth(0).selectOption('demo'); await expect(page.locator('[data-session-start] select').nth(1)).toBeEnabled();
  await page.locator('[data-session-start] select').nth(1).selectOption('demo-ws');
  await page.locator('textarea').fill(locale === 'zh-TW' ? '請保留我的原始指示與工作區。' : 'Keep my original instructions and workspace.');
  await page.locator('textarea').blur();
  await page.evaluate(async () => {window.scrollTo(0, 0); await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await mkdir('/tmp/bac-start-ui-after', {recursive: true});
  await page.screenshot({path: `/tmp/bac-start-ui-after/start-${locale}-${width}.png`, fullPage: true});
  expect(state.errors).toEqual([]); expect(state.posts).toEqual([]); await context.close();
});

test('host route hints preserve a frozen start and its original key', async ({page}) => {
  const state = await startFixture(page, false, {lost: true});
  const form = await openStart(page); await chooseWorkspace(page);
  await start(form).click(); await expect(form).toContainText('Lost start reply');
  const original = structuredClone(state.posts[0]);
  await page.goto('/dashboard/#/start?host=other');
  await expect(form.getByRole('combobox', {name: 'Host', exact: true})).toHaveValue('demo');
  await form.getByRole('button', {name: 'Retry original request'}).click();
  await expect(form).toContainText('Start confirmed');
  expect(state.posts).toEqual([original, original]);
});
