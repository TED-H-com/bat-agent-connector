import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {orchestrationFixture, openOrchestration, chooseTarget, fillOrchestration, operationId, childId} from './orchestration-fixture';
const primary = (form: any) => form.locator('button.primary');
for (const locale of ['en-US', 'zh-TW']) for (const width of [1440, 390]) {
  test(`Connector target and policy wording ${locale} ${width}`, async ({browser}) => {
    const context = await browser.newContext({locale, colorScheme: 'light', viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage(), state = await orchestrationFixture(page, false);
    const form = await openOrchestration(page, 'relay'), zh = locale === 'zh-TW';
    await form.getByRole('combobox', {name: zh ? '主機' : 'Host', exact: true}).selectOption('demo');
    const target = form.getByRole('combobox', {name: zh ? '固定的受管理工作階段' : 'Exact managed session', exact: true});
    await expect(target).toBeEnabled();
    await target.selectOption('managed-exact');
    await expect(form).toContainText(zh
      ? '只可選用最新觀察的 Connector 受管工作階段，或 Connector 傳回的固定工作區 ID。Connector 會在產生影響前重新檢查政策。'
      : 'Only fresh Connector-managed sessions or exact Connector workspace IDs are selectable. Connector rechecks policy before any effect.');
    await expect(target).toHaveValue('managed-exact');
    expect(await form.innerText()).not.toMatch(/\bcentral\b|中央/i);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const folder = process.env.POLISH_COPY_SCREENSHOTS || `${process.env.TMPDIR || '/tmp'}/polish-copy-shots`;
    await mkdir(folder, {recursive: true});
    await page.screenshot({path: `${folder}/orchestration-${locale}-${width}.png`, fullPage: true});
    expect(state.posts).toEqual([]); expect(state.errors).toEqual([]);
    await context.close();
  });
}
for (const locale of ['en-US', 'zh-TW']) test(`orchestration receipt statuses use shared labels ${locale}`, async ({browser}) => {
  const context = await browser.newContext({locale}), page = await context.newPage();
  const state = await orchestrationFixture(page, false, {status:'needs_attention'});
  const form = await openOrchestration(page, 'items');
  await form.locator('select').first().selectOption('demo');
  await expect(form.locator('select').nth(1)).toBeEnabled();
  await form.locator('select').nth(1).selectOption('demo-ws');
  await form.locator('.orch-item input').fill('Review login');
  await form.locator('textarea').fill('Retain the original requirements.');
  await form.locator('.orch-check input').last().check();
  await primary(form).click();
  await expect(form.locator(`a[href="#/op/${childId}"]`)).toContainText(locale === 'en-US' ? 'outcome unknown, reading back' : '結果不明，回查中');
  const steps = form.locator('[data-orchestration-steps]');
  await steps.locator('summary').click();
  await expect(steps.locator('li')).toHaveText(locale === 'en-US'
    ? ['fanout.start.prepare · done', 'child · done'] : ['fanout.start.prepare · 完成', 'child · 完成']);
  expect(state.posts).toHaveLength(1); expect(state.errors).toEqual([]);
  await context.close();
});
for (const native of [false, true]) for (const mode of ['relay', 'planner', 'items', 'failover']) {
  test(`${native ? 'native' : 'browser'} ${mode}: original envelope survives lost reply and changed selection policy`, async ({page}) => {
    const state = await orchestrationFixture(page, native, {lost: true}); const form = await openOrchestration(page, mode);
    await chooseTarget(form, mode); await fillOrchestration(form, mode); await primary(form).click();
    await expect(form).toContainText('Lost orchestration reply'); const first = state.posts[0];
    expect(first.body.preconditions).toEqual({}); expect(first.body.target.host).toBe('demo');
    if (mode === 'relay') expect(first.body.params).toEqual({message: '  Keep my original words.\nDo not replace them.  ', queue: false, start_if_missing: false});
    if (mode === 'items') expect(first.body.params.plan.map((v: any) => v.index)).toEqual([1, 2]);
    if (mode === 'failover') expect(first.body.params).toEqual({tail_messages: 12, instructions: '  Retain original evidence.  '});
    await expect(form.getByRole('combobox').first()).toBeDisabled();
    state.writes = false; state.orchestrate = false; state.manual = true; await page.reload(); expect(state.posts).toHaveLength(1);
    await form.getByRole('button', {name: 'Retry original request'}).click();
    await expect(form.locator(`a[href="#/op/${operationId}"]`)).toBeVisible(); expect(state.posts[1]).toEqual(first);
    await expect(form.locator(`a[href="#/op/${childId}"]`)).toBeVisible();
    const reads = state.reads.length; await page.reload(); await expect(form.locator(`a[href="#/op/${operationId}"]`)).toBeVisible();
    expect(state.posts).toHaveLength(2); expect(state.reads.slice(reads).some((p: string) => p.startsWith('/workspaces') || p.startsWith('/sessions'))).toBe(false);
    expect(state.errors).toEqual([]);
  });
}
for (const native of [false, true]) {
  test(`${native ? 'native' : 'browser'}: partial child receipts block new intent and failed reads block event cursor`, async ({page}) => {
    const state = await orchestrationFixture(page, native, {status: 'needs_attention'}); const form = await openOrchestration(page, 'items');
    await chooseTarget(form, 'items'); await fillOrchestration(form, 'items'); await primary(form).click();
    await expect(form).toContainText('Completion is not proven'); await expect(form).toContainText('0 of 2');
    await expect(form.getByRole('button', {name: 'Prepare another request'})).toBeHidden();
    state.failRead = true; state.events.push({seq: 1, resource_type: 'operation', resource_id: operationId, type: 'operation.updated'});
    await expect(form).toContainText('Receipt read failed', {timeout: 10000}); expect(state.after).toBe(0);
    state.failRead = false; await expect.poll(() => state.after, {timeout: 10000}).toBe(1);
    await page.reload(); await expect(form).toContainText('Completion is not proven'); expect(state.posts).toHaveLength(1);
  });
  test(`${native ? 'native' : 'browser'}: positive scopes and managed identity are required`, async ({page}) => {
    const state = await orchestrationFixture(page, native, {scopes: ['observe', 'start']}); const form = await openOrchestration(page, 'failover');
    await chooseTarget(form, 'failover'); await fillOrchestration(form, 'failover'); await expect(primary(form)).toBeDisabled();
    const values = await form.getByRole('combobox', {name: 'Exact managed session'}).locator('option').evaluateAll(xs => xs.map(x => (x as HTMLOptionElement).value));
    expect(values).toEqual(['', 'managed-exact', 'managed-second']);
    state.scopes.push('operate'); state.taskOwned = true; await page.reload(); await form.locator('.orch-check input').last().check(); await expect(primary(form)).toBeDisabled();
    state.taskOwned = false; state.manual = true; await page.reload(); await form.locator('.orch-check input').last().check(); await expect(primary(form)).toBeDisabled();
    state.manual = false; state.allowed = false; await page.reload(); await form.locator('.orch-check input').last().check(); await expect(primary(form)).toBeDisabled(); expect(state.posts).toEqual([]);
  });
}
for (const bad of ['key', 'actor', 'target', 'items', 'preconditions']) test(`reject mismatched ${bad} receipt without rotating original`, async ({page}) => {
  const state = await orchestrationFixture(page, false, {bad}); const form = await openOrchestration(page, 'items'); await chooseTarget(form, 'items'); await fillOrchestration(form, 'items');
  await primary(form).click(); await expect(form).toContainText('does not match the original'); await expect(form.getByRole('button', {name: 'Prepare another request'})).toBeHidden();
  state.bad = null; await form.getByRole('button', {name: 'Retry original request'}).click(); await expect(form.locator(`a[href="#/op/${operationId}"]`)).toBeVisible();
  expect(state.posts[1]).toEqual(state.posts[0]);
});
test('delayed host and session reads cannot replace the newer choice; editing clears review', async ({page}) => {
  const state = await orchestrationFixture(page, false, {delayHost: 'demo'}); const form = await openOrchestration(page, 'planner');
  await form.getByRole('combobox', {name: 'Host', exact: true}).selectOption('demo'); await expect.poll(() => Boolean(state.holdDiscovery)).toBe(true);
  await form.getByRole('combobox', {name: 'Host', exact: true}).selectOption('other'); await expect(form.getByRole('combobox', {name: 'Workspace', exact: true})).toBeEnabled();
  await form.getByRole('combobox', {name: 'Workspace', exact: true}).selectOption('other-ws'); state.holdDiscovery();
  await fillOrchestration(form, 'planner'); await expect(primary(form)).toBeEnabled(); await form.getByRole('textbox').fill('Changed instruction'); await expect(primary(form)).toBeDisabled();
  await form.locator('.orch-check input').last().check(); await primary(form).click(); await expect.poll(() => state.posts.length).toBe(1); expect(state.posts[0].body.target).toEqual({host: 'other', workspace: 'other-ws'});
});
test('storage failure prevents first mutation and new principal cannot recover another credential draft', async ({page}) => {
  const state = await orchestrationFixture(page, false); const form = await openOrchestration(page, 'relay'); await chooseTarget(form, 'relay'); await fillOrchestration(form, 'relay');
  await page.evaluate(() => {const original = Storage.prototype.setItem; Storage.prototype.setItem = function(k, v) {if (k.startsWith('batc.orchestrate.')) throw new Error('Storage unavailable'); return original.call(this, k, v);};});
  await primary(form).click(); await expect(form).toContainText('Storage unavailable'); expect(state.posts).toEqual([]);
  state.principal = 'different-credential'; await page.reload(); await expect(form.getByRole('textbox')).toHaveValue(''); expect(state.posts).toEqual([]);
});
for (const locale of ['en-US', 'zh-TW']) for (const width of [390, 768, 1440]) for (const mode of ['relay', 'planner', 'items', 'failover']) test(`orchestration ${mode} ${locale} ${width}`, async ({browser}) => {
  const context = await browser.newContext({locale, viewport: {width, height: width === 390 ? 844 : 900}}), page = await context.newPage();
  const state = await orchestrationFixture(page, false); const form = await openOrchestration(page, mode);
  await form.locator('select').first().selectOption('demo'); await expect(form.locator('select').nth(1)).toBeEnabled();
  await form.locator('select').nth(1).selectOption(['relay', 'failover'].includes(mode) ? 'managed-exact' : 'demo-ws');
  if (mode === 'items') {await form.locator('.orch-item input').fill('登入流程 · Login'); await form.locator('textarea').fill('保留原始需求，新增驗證案例。\nPreserve the original requirements and add a focused regression.');}
  else await form.locator('textarea').fill('保留原始需求與已完成的工作。\nKeep the original requirements and completed work.');
  await form.locator('textarea').blur(); await page.evaluate(() => window.scrollTo(0, 0));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await mkdir('/tmp/bac-orchestration-after', {recursive: true});
  await page.screenshot({path: `/tmp/bac-orchestration-after/${mode}-${locale}-${width}.png`, fullPage: true});
  expect(state.errors).toEqual([]); expect(state.posts).toEqual([]); await context.close();
});
test('cancelled parent cannot conceal an unknown child by preparing a replacement', async ({page}) => {
  const state = await orchestrationFixture(page, false, {status: 'cancelled'}); const form = await openOrchestration(page, 'items');
  await chooseTarget(form, 'items'); await fillOrchestration(form, 'items'); await primary(form).click();
  await expect(form).toContainText('Completion is not proven'); await expect(form.getByRole('button', {name: 'Prepare another request'})).toBeHidden();
  await expect(form.locator(`a[href="#/op/${childId}"]`)).toBeVisible(); expect(state.posts).toHaveLength(1);
});
test('late first-session identity cannot enable a changed unconfirmed target', async ({page}) => {
  const state = await orchestrationFixture(page, false, {delaySession: 'managed-exact'}); const form = await openOrchestration(page, 'relay');
  await chooseTarget(form, 'relay'); await expect.poll(() => Boolean(state.holdSelection)).toBe(true);
  await form.getByRole('combobox', {name: 'Exact managed session'}).evaluate((el: HTMLSelectElement) => {el.value = 'managed-second'; el.dispatchEvent(new Event('change', {bubbles: true}));});
  await fillOrchestration(form, 'relay'); state.holdSelection(); await primary(form).click(); await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0].body.target.session_id).toBe('managed-second');
});
test('generic admission errors retain the original key; proven tier refusal alone permits a new intent', async ({page}) => {
  const state = await orchestrationFixture(page, false, {refuse: 'CONFLICT'}); const form = await openOrchestration(page, 'planner');
  await chooseTarget(form, 'planner'); await fillOrchestration(form, 'planner'); await primary(form).click(); await expect(form).toContainText('Fixture refusal');
  await expect(form.getByRole('button', {name: 'Prepare another request'})).toBeHidden();
  state.refuse = 'TIER_DISABLED'; await form.getByRole('button', {name: 'Retry original request'}).click(); await expect.poll(() => state.posts.length).toBe(2);
  expect(state.posts[1]).toEqual(state.posts[0]); await expect(form.getByRole('button', {name: 'Prepare another request'})).toBeVisible();
  state.refuse = null; await form.getByRole('button', {name: 'Prepare another request'}).click(); await fillOrchestration(form, 'planner'); await primary(form).click(); await expect.poll(() => state.posts.length).toBe(3);
  expect(state.posts[2].idempotency_key).not.toBe(state.posts[0].idempotency_key);
});
test('combined UTF-8 request limit refuses oversized reviewed items before persisting an intent', async ({page}) => {
  const state = await orchestrationFixture(page, false); const form = await openOrchestration(page, 'items'); await chooseTarget(form, 'items');
  await page.evaluate(() => {const [key, value] = Object.entries(localStorage).find(([k]) => k.startsWith('batc.orchestrate.'))!;
    const saved = JSON.parse(value); saved.items = Array.from({length: 4}, (_, i) => ({title: 'Item '+i, prompt: '字'.repeat(19000)})); localStorage.setItem(key, JSON.stringify(saved));});
  await page.reload(); await form.locator('.orch-check input').last().check(); await expect(form).toContainText('200,000-byte limit');
  await expect(primary(form)).toBeDisabled(); expect(state.posts).toEqual([]);
  expect(await page.evaluate(() => JSON.parse(Object.entries(localStorage).find(([k]) => k.startsWith('batc.orchestrate.'))![1]).intent)).toBeUndefined();
});
