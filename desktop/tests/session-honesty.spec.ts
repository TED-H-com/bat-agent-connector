// Honest session states: withdrawal, read failures, raw codes, delivery receipts, unknown fields,
// read-only manual sessions and checkpoint flows. Mock responses only.
import {expect, test, type Page} from '@playwright/test';
import {checkpointHost, consoleNoise, forbidden, fromRequirements, go, notFound, openSession, type Override} from './session-fixture.ts';

const visibleText = (page: Page) => page.locator('main').evaluate(el => (el as HTMLElement).innerText);

test('a withdrawn session clears everything together, stops polling and recovers on a later event', async ({browser}) => {
  for (const locale of ['en', 'zh-TW'] as const) {
    const zh = locale === 'zh-TW';
    const s = await openSession(browser, {locale});
    await fromRequirements(s.page, s.ids, 'implement');
    const {page, ids} = s;
    const path = `/sessions/${ids.host}/${ids.sessions.implement}`;
    await expect(page.locator('[data-workspace-result]')).toContainText('PR #87');
    const box = page.locator('#workspace-message-input textarea');
    await box.fill('Keep this draft while the session is gone.');
    let mode: any = notFound;
    s.overrides.push((input, url) => (input.method || 'GET') === 'GET' && mode && (url.pathname === path || url.pathname === path + '/messages') ? mode : undefined);
    s.sessionEvent('implement');
    const state = page.locator('[data-session-withdrawn]');
    await expect(state).toHaveText(new RegExp(zh ? '這個工作階段已無法使用' : 'This session is no longer available'));
    await expect(state.getByRole('link', {name: zh ? '返回需求' : 'Back to Requirements'})).toBeVisible();
    // Rows, pill, delivery, checkpoints, inspector and composer leave in the same step.
    await expect(page.locator('.conversation-scroll .msg')).toHaveCount(0);
    await expect(page.locator('.session-pill [data-status]')).toHaveCount(0);
    await expect(page.locator('.session-delivery-row')).toHaveCount(0);
    await expect(page.locator('.ui-inspector')).toHaveCount(0);
    await expect(page.locator('#workspace-message-input')).toHaveCount(0);
    await expect(page.locator('.workspace-session-heading h1')).not.toContainText(s.copy.sessionImplement);
    const text = await visibleText(page);
    expect(text).not.toContain('PR #87');
    expect(text).not.toContain('NOT_FOUND');
    expect(await page.evaluate(() => Object.entries(localStorage).find(([key]) => key.startsWith('batc.draft.') && key.includes('send.'))?.[1]))
      .toBe('Keep this draft while the session is gone.');
    // No fixed 1-second loop after withdrawal.
    const since = Date.now();
    await page.waitForTimeout(3500);
    expect(s.reads(path, since)).toBe(0);
    mode = forbidden;
    s.sessionEvent('implement');
    await expect(state).toHaveText(new RegExp(zh ? '已移除存取權限' : 'Access removed'));
    expect(await visibleText(page)).not.toContain('FORBIDDEN');
    mode = null;
    s.sessionEvent('implement');
    await expect(page.locator('.conversation-scroll .msg').first()).toBeVisible();
    await expect(state).toHaveCount(0);
    await expect(page.locator('#workspace-message-input textarea')).toHaveValue('Keep this draft while the session is gone.');
    expect(s.errors.filter(line => !consoleNoise(line))).toEqual([]);
    await s.context.close();
  }
});

test('withdrawal closes the open drawer and leaves nothing inert', async ({browser}) => {
  const s = await openSession(browser, {width: 1000, height: 900});
  await fromRequirements(s.page, s.ids, 'implement');
  const {page, ids} = s;
  await page.getByRole('button', {name: 'Results', exact: true}).click();
  await expect(page.locator('.ui-inspector')).toBeVisible();
  s.overrides.push((input, url) => url.pathname.startsWith(`/sessions/${ids.host}/${ids.sessions.implement}`) ? notFound : undefined);
  s.sessionEvent('implement');
  await expect(page.locator('[data-session-withdrawn]')).toBeVisible();
  await expect(page.locator('.ui-inspector')).toHaveCount(0);
  await expect(page.locator('.ui-inspector-scrim')).toHaveCount(0);
  expect(await page.locator('[inert]').count()).toBe(0);
  await s.context.close();
});

test('transient read failures back off instead of retrying every second', async ({browser}) => {
  const s = await openSession(browser);
  await fromRequirements(s.page, s.ids, 'implement');
  const {page, ids} = s;
  const path = `/sessions/${ids.host}/${ids.sessions.implement}`;
  s.overrides.push((input, url) => url.pathname === path ? {status: 500, data: {error: {code: 'INTERNAL', message: 'Session read failed'}}} : undefined);
  s.sessionEvent('implement');
  await expect(page.locator('.session-status')).toContainText('Server error');
  const since = Date.now();
  await page.waitForTimeout(7000);
  // 1 s, 2 s, 4 s: at most four reads in seven seconds, not seven.
  expect(s.reads(path, since)).toBeLessThanOrEqual(4);
  expect(await visibleText(page)).not.toContain('INTERNAL');
  await expect(page.getByRole('button', {name: 'Send', exact: true})).toBeDisabled();
  await s.context.close();
});

for (const [name, response] of [
  ['500', {status: 500, data: {error: {code: 'INTERNAL', message: 'Messages unavailable'}}}],
  ['413', {status: 413, data: {error: {code: 'PAYLOAD_TOO_LARGE', message: 'Messages exceed the response limit'}}}],
] as const) {
  test(`a failed message read draws an inline state (${name})`, async ({browser}) => {
    let failing = true;
    const s = await openSession(browser, {overrides: [(input, url) => failing && url.pathname === '/sessions/demo-host/signin-implement/messages' ? response : undefined]});
    await fromRequirements(s.page, s.ids, 'implement');
    const {page} = s;
    const state = page.locator('.conversation [data-conversation-state=error]');
    await expect(state).toContainText(name === '413' ? 'Too many messages to load' : 'Messages unavailable');
    await expect(page.locator('.conversation-window')).toHaveText('Messages unavailable');
    expect(await visibleText(page)).not.toMatch(/INTERNAL|PAYLOAD_TOO_LARGE/);
    if (name === '413') expect(s.log.some(row => row.path.includes('/messages?') && row.path.includes('last_n=10'))).toBe(true);
    failing = false;
    await state.getByRole('button', {name: 'Retry', exact: true}).click();
    await expect(page.locator('.conversation-scroll .msg').first()).toBeVisible();
    await expect(state).toHaveCount(0);
    await s.context.close();
  });
}

test('send and answer conflicts say what happened and offer the recovery', async ({browser}) => {
  for (const locale of ['en', 'zh-TW'] as const) {
    const zh = locale === 'zh-TW';
    let busy = true;
    const s = await openSession(browser, {locale, overrides: [(input, url) => {
      const action = (input.body as any)?.action;
      if (input.method === 'POST' && url.pathname === '/operations' && action === 'session.send' && busy && !(input.body as any)?.params?.queue)
        return {status: 409, data: {error: {code: 'SESSION_BUSY', message: 'The session is busy with another turn'}}};
      if (input.method === 'POST' && url.pathname === '/operations' && action === 'session.answer')
        return {status: 409, data: {error: {code: 'PENDING_CHANGED', message: 'The question changed.'}}};
      return undefined;
    }]});
    await fromRequirements(s.page, s.ids, 'implement');
    const {page} = s;
    const box = page.locator('#workspace-message-input textarea');
    await box.evaluate(el => {(el.closest('#workspace-message-input')!.querySelector('input[type=checkbox]') as HTMLInputElement).checked = false;});
    await box.fill('Please keep the five-attempt lockout.');
    await page.getByRole('button', {name: zh ? '送出' : 'Send', exact: true}).click();
    const error = page.locator('[data-session-error]');
    await expect(error).toContainText(zh ? '正在處理另一輪' : 'Busy with another turn');
    expect(await visibleText(page)).not.toContain('SESSION_BUSY');
    await expect(error.locator('details')).toContainText('SESSION_BUSY');
    await error.getByRole('button', {name: zh ? '排在後面' : 'Queue it', exact: true}).click();
    await expect.poll(() => s.writes.filter(row => row.body?.action === 'session.send').length).toBe(2);
    expect(s.writes.filter(row => row.body?.action === 'session.send')[1].body.params).toMatchObject({text: 'Please keep the five-attempt lockout.', queue: true});
    await go(page, `#/session/${s.ids.host}/${s.ids.sessions.review}`);
    await page.getByRole('button', {name: zh ? '允許' : 'Allow', exact: true}).click();
    await expect(page.locator('[data-session-error]')).toContainText(zh ? '問題已更新' : 'The question changed');
    await expect(page.locator('[data-session-error]').getByRole('button', {name: zh ? '重新查看' : 'Review it', exact: true})).toBeVisible();
    expect(await visibleText(page)).not.toContain('PENDING_CHANGED');
    await s.context.close();
  }
});

const receipt = (pull: number, index: number, origin = 'https://git.example.test', deployed = false) => ({
  operation_id: 'op_' + index.toString(16).padStart(32, '0'), action: 'github.pr.merge', status: 'succeeded',
  requirement_acceptance: 'not_evaluated_by_delivery',
  target: {provider_origin: origin, repository: 'example/signin', repository_id: 7, pull_number: pull},
  merge: {merged: true, verified: true, merged_sha: 'a'.repeat(40)},
  ...(deployed ? {deployment: {environment: 'production', verified: true, is_current: true, source_sha: 'a'.repeat(40)}} : {}),
});
const page200 = (ids: any, deliveries: unknown[], next: number | null = null) => ({status: 200, data: {workspace_id: ids.workspaceId,
  work_item_id: ids.work.email, contract: 'pm-engineering-delivery/v1', original_packages: [], requirement_acceptance: 'not_evaluated_by_delivery',
  source_visibility_filtered: false, deliveries, next_after: next}});
const deliveriesPath = /\/work\/[^/]+\/deliveries$/;

test('delivery shows unknown, partial, grouped and validated receipts honestly', async ({browser}) => {
  let respond: (ids: any) => any = () => ({status: 500, data: {error: {code: 'INTERNAL', message: 'Delivery read failed'}}});
  const s = await openSession(browser, {overrides: [(input, url) => deliveriesPath.test(url.pathname) ? respond(s.ids) : undefined]});
  await fromRequirements(s.page, s.ids, 'implement');
  const {page} = s;
  const delivery = page.locator('[data-workspace-result]');
  // Unknown is not "none".
  await expect(delivery.locator('[data-session-delivery-state=unknown]')).toContainText('Unknown');
  await expect(delivery.getByRole('button', {name: 'Retry', exact: true})).toBeVisible();
  // Two receipts for one PR read as one row; links use the provider origin.
  respond = ids => page200(ids, [receipt(87, 1), receipt(87, 2, 'https://git.example.test', true)]);
  await delivery.getByRole('button', {name: 'Retry', exact: true}).click();
  await expect(delivery.locator('[data-session-pr]')).toHaveCount(1);
  await expect(delivery.locator('[data-session-pr]')).toContainText('Deployed');
  await expect(delivery.locator('[data-session-pr] a')).toHaveAttribute('href', 'https://git.example.test/example/signin/pull/87');
  // Refreshes swap rows only after a validated read: no empty frame, no flicker.
  await page.evaluate(() => {
    const node = document.querySelector('.session-delivery-pills')!;
    (window as any).__emptyFrames = 0;
    new MutationObserver(() => {if (!node.querySelector('[data-session-pr]')) (window as any).__emptyFrames++;}).observe(node, {childList: true, subtree: true});
  });
  const reads = s.log.filter(row => deliveriesPath.test(row.path.split('?')[0])).length;
  s.sessionEvent('implement');
  await expect.poll(() => s.log.filter(row => deliveriesPath.test(row.path.split('?')[0])).length).toBeGreaterThan(reads);
  await page.waitForTimeout(300);
  expect(await page.evaluate(() => (window as any).__emptyFrames)).toBe(0);
  // A later failure keeps the last rows, marked as not updated.
  respond = () => ({status: 500, data: {error: {code: 'INTERNAL', message: 'Delivery read failed'}}});
  s.sessionEvent('implement');
  await expect(delivery.locator('[data-session-delivery-state=stale]')).toContainText('Not updated');
  await expect(delivery.locator('[data-session-pr]')).toHaveCount(1);
  // A response for another scope never renders.
  respond = ids => ({...page200(ids, [receipt(666, 9, 'https://git.example.test', true)]), data: {...page200(ids, []).data, workspace_id: 'ws_' + 'f'.repeat(32), deliveries: [receipt(666, 9)]}});
  s.sessionEvent('implement');
  await expect(delivery.locator('[data-session-delivery-state=stale]')).toBeVisible();
  await page.waitForTimeout(500);
  expect(await delivery.innerText()).not.toContain('666');
  // A partial page says so.
  respond = ids => page200(ids, Array.from({length: 20}, (_, i) => receipt(100 + i, i + 1)), 20);
  s.sessionEvent('implement');
  await expect(delivery.locator('[data-session-delivery-state=partial]')).toHaveText('20 shown · more');
  expect(s.errors.filter(line => !consoleNoise(line))).toEqual([]);
  await s.context.close();
});

test('unknown fields stay unknown', async ({browser}) => {
  for (const locale of ['en', 'zh-TW'] as const) {
    const zh = locale === 'zh-TW';
    const s = await openSession(browser, {locale});
    const path = `/sessions/${s.ids.host}/${s.ids.sessions.implement}`;
    s.overrides.push((input, url) => url.pathname === path ? {status: 200, data: {session: {host: s.ids.host, session_id: s.ids.sessions.implement}}} : undefined);
    await fromRequirements(s.page, s.ids, 'implement');
    const {page} = s;
    await expect(page.locator('.workspace-session-heading h1')).toHaveText(zh ? '未命名工作階段' : 'Untitled session');
    await expect(page.locator('.session-mark .ui-agent-mark')).toHaveText('?');
    await page.getByRole('button', {name: zh ? '更多操作' : 'More actions', exact: true}).click();
    const href = await page.locator('[data-menu-key=discuss]').getAttribute('href');
    expect(new URLSearchParams(String(href).split('?')[1] || '').get('title')).toBeNull();
    await s.context.close();
  }
});

test('a manual session reads as one plain read-only line, and its turns belong to a person', async ({browser}) => {
  for (const locale of ['en', 'zh-TW'] as const) {
    const zh = locale === 'zh-TW';
    const s = await openSession(browser, {locale});
    await fromRequirements(s.page, s.ids, 'manual');
    const {page} = s;
    const line = page.locator('[data-session-readonly]');
    await expect(line).toHaveText(zh ? '這是有人在 BAT 開啟的工作階段，這裡只能檢視。' : "Started in BAT by a person, so it's read-only here.");
    expect(await line.evaluate(el => Boolean(el.closest('.panel')))).toBe(false);
    await expect(page.locator('.workspace-composer')).toBeHidden();
    for (const name of [zh ? '送出' : 'Send', zh ? '允許' : 'Allow', zh ? '中斷這一輪' : 'Interrupt turn'])
      await expect(page.getByRole('button', {name, exact: true})).toHaveCount(0);
    expect(await visibleText(page)).not.toMatch(/\bAPI\b/);
    await expect(page.locator('.conversation-scroll .msg .message-name').first()).toHaveText(zh ? '使用者' : 'Person');
    expect(s.writes).toEqual([]);
    await s.context.close();
  }
});

test('checkpoints on a capable host: quiet at rest, honest notes, and a way into the new session', async ({browser}) => {
  for (const locale of ['en', 'zh-TW'] as const) {
    const zh = locale === 'zh-TW';
    const s = await openSession(browser, {locale});
    s.overrides.push(checkpointHost(s, s.ids.sessions.manual));
    await fromRequirements(s.page, s.ids, 'manual');
    const {page} = s;
    const section = page.locator('.session-section').filter({has: page.locator('.session-checkpoints')});
    await expect(section.locator('.session-checkpoint-row')).toHaveCount(1);
    // At rest: one quiet action, no picker.
    await expect(section.getByRole('combobox', {name: zh ? '提交' : 'Commit'})).toBeHidden();
    const record = section.getByRole('button', {name: zh ? '記下這個版本' : 'Record this version', exact: true});
    await expect(record).toBeVisible();
    // Notes on a capable host do not claim checkpoints are unavailable.
    const notes = section.locator('summary').filter({hasText: zh ? '檢查點說明' : 'Checkpoint notes'});
    await notes.click();
    const notesText = await notes.locator('..').innerText();
    expect(notesText).not.toMatch(zh ? /無法從檢查點/ : /cannot start from a checkpoint/);
    expect(notesText).not.toMatch(/\b(?:null|undefined)\b/);
    // Start from here comes before the form it opens; the caveat waits in Details.
    const start = section.getByRole('button', {name: zh ? '從這裡接續' : 'Start from here', exact: true});
    await start.click();
    const form = section.locator('[data-checkpoint-form]');
    await expect(form).toBeVisible();
    expect(await start.evaluate((button, node) => Boolean(button.compareDocumentPosition(node!) & Node.DOCUMENT_POSITION_FOLLOWING), await form.elementHandle())).toBe(true);
    await expect(form.locator('[data-confinement-note]')).toBeHidden();
    const formText = await form.innerText();
    if (zh) expect(formText).not.toMatch(/agent|default|acceptEdits|clone|branch/);
    await form.locator('textarea').fill(zh ? '依照筆記完成重設密碼流程。' : 'Finish the reset flow from these notes.');
    await form.getByRole('button', {name: zh ? '開始代理工作' : 'Start agent work', exact: true}).click();
    await expect(form.getByRole('link', {name: zh ? '開啟新工作階段' : 'Open the new session'})).toHaveAttribute('href', `#/session/${s.ids.host}/continued-session`);
    await expect(form.locator('.session-checkpoint-op [data-status=waiting]')).toHaveText(zh ? '等待中' : 'Waiting');
    await expect(form.locator('.session-checkpoint-op [data-status=done]')).toHaveCount(0);
    expect(await form.innerText()).not.toMatch(/op_[0-9a-f]/);
    const continued = s.writes.filter(row => row.body?.action === 'checkpoint.continue');
    expect(continued.map(row => row.body.target)).toEqual([{checkpoint_id: 'cp_' + '7'.repeat(32)}]);
    // Recording opens the picker, then records only the chosen commit of the manual session.
    await record.click();
    await expect(section.getByRole('combobox', {name: zh ? '提交' : 'Commit'})).toBeVisible();
    await section.getByRole('button', {name: zh ? '記下' : 'Record', exact: true}).click();
    await expect.poll(() => s.writes.filter(row => row.body?.action === 'checkpoint.create').length).toBe(1);
    expect(s.writes.find(row => row.body?.action === 'checkpoint.create').body.target).toEqual({host: s.ids.host, session_id: s.ids.sessions.manual});
    expect(s.writes.filter(row => String(row.body?.action).startsWith('session.'))).toEqual([]);
    expect(await visibleText(page)).not.toMatch(/\b(?:null|undefined)\b/);
    if (zh) {
      await page.getByRole('button', {name: '更多操作', exact: true}).click();
      await expect(page.getByRole('menuitem', {name: '討論', exact: true})).toBeVisible();
    }
    await s.context.close();
  }
});

for (const status of [403, 404, 410]) {
  test(`delivery withdrawal removes cached receipts and their count (${status})`, async ({browser}) => {
    let unavailable = false;
    const s = await openSession(browser, {overrides: [(input, url) => unavailable && deliveriesPath.test(url.pathname)
      ? {status, data: {error: {code: status === 403 ? 'FORBIDDEN' : 'NOT_FOUND', message: 'Delivery unavailable'}}} : undefined]});
    await fromRequirements(s.page, s.ids, 'implement');
    const result = s.page.locator('[data-workspace-result]');
    await expect(result).toContainText('PR #87');
    unavailable = true;
    s.sessionEvent('implement');
    await expect(result.locator('[data-session-delivery-state=removed]')).toBeVisible();
    await expect(result.locator('[data-session-pr]')).toHaveCount(0);
    expect(await result.innerText()).not.toMatch(/PR #|Merged|Deployed|shown/);
    await s.context.close();
  });
}

test('withdrawal clears immediately while a message response is held; late text cannot return', async ({browser}) => {
  let hold = false, waiting = false, release!: () => void;
  const gate = new Promise<void>(resolve => {release = resolve;});
  const s = await openSession(browser, {overrides: [async (input, url) => {
    if (hold && url.pathname === '/sessions/demo-host/signin-implement/messages') {
      waiting = true; await gate;
      return {status: 200, data: {messages: [{id: 'late', role: 'assistant', text: 'Withdrawn secret text'}], next_offset: null}};
    }
    if (hold && url.pathname === '/sessions/demo-host/signin-implement') return {status: 410, data: {error: {code: 'NOT_FOUND', message: 'Gone'}}};
    return undefined;
  }]});
  await fromRequirements(s.page, s.ids, 'implement');
  await expect(s.page.locator('.conversation-scroll .msg')).toHaveCount(18);
  hold = true; s.sessionEvent('implement');
  await expect.poll(() => waiting).toBe(true);
  await expect(s.page.locator('[data-session-withdrawn]')).toBeVisible();
  await expect(s.page.locator('.conversation-scroll .msg, .ui-inspector, #workspace-message-input')).toHaveCount(0);
  release();
  await s.page.waitForTimeout(500);
  expect(await visibleText(s.page)).not.toContain('Withdrawn secret text');
  await s.context.close();
});

test('a late Delivery retry cannot revive a withdrawn session', async ({browser}) => {
  let failing = false, hold = false, waiting = false, gone = false, release!: () => void;
  const gate = new Promise<void>(resolve => {release = resolve;});
  const s = await openSession(browser, {overrides: [async (input, url) => {
    if (gone && url.pathname === '/sessions/demo-host/signin-implement') return notFound;
    if (deliveriesPath.test(url.pathname) && hold) {
      waiting = true; await gate; return page200(s.ids, [receipt(666, 9)]);
    }
    if (deliveriesPath.test(url.pathname) && failing) return {status: 500, data: {error: {code: 'INTERNAL', message: 'Delivery unavailable'}}};
    return undefined;
  }]});
  await fromRequirements(s.page, s.ids, 'implement');
  const result = s.page.locator('[data-workspace-result]');
  await expect(result).toContainText('PR #87');
  failing = true; s.sessionEvent('implement');
  await expect(result.locator('[data-session-delivery-state=stale]')).toBeVisible();
  hold = true; await result.getByRole('button', {name: 'Retry', exact: true}).click();
  await expect.poll(() => waiting).toBe(true);
  gone = true; s.sessionEvent('implement');
  await expect(s.page.locator('[data-session-withdrawn]')).toBeVisible();
  release(); await s.page.waitForTimeout(500);
  expect(await visibleText(s.page)).not.toMatch(/666|PR #87/);
  await expect(s.page.locator('.ui-inspector')).toHaveCount(0);
  await s.context.close();
});

test('send retries and question drafts survive reload with their original operation keys', async ({browser}) => {
  let refuse = true;
  const s = await openSession(browser, {overrides: [(input, url) => {
    const body = input.body as any;
    if (refuse && input.method === 'POST' && url.pathname === '/operations' && ['session.send', 'session.answer'].includes(body?.action))
      return {status: 409, data: {error: {code: body.action === 'session.send' ? 'SESSION_BUSY' : 'PENDING_CHANGED', message: 'Conflict'}}};
    return undefined;
  }]});
  const {page} = s;
  await fromRequirements(page, s.ids, 'implement');
  await page.locator('#workspace-message-input textarea').fill('Keep this exact intent.');
  await page.locator('.composer-queue input').uncheck();
  await page.getByRole('button', {name: 'Send', exact: true}).click();
  await expect(page.locator('[data-session-error=SESSION_BUSY]')).toBeVisible();
  const first = s.writes.find(row => row.body?.action === 'session.send');
  await page.reload();
  await expect(page.locator('#workspace-message-input textarea')).toHaveValue('Keep this exact intent.');
  refuse = false;
  await page.getByRole('button', {name: 'Send', exact: true}).click();
  await expect(page.locator('.session-send-line')).toContainText('Sent');
  const sends = s.writes.filter(row => row.body?.action === 'session.send');
  expect(sends).toHaveLength(2); expect(sends[1].idempotency_key).toBe(first.idempotency_key);
  expect(sends[1].body).toEqual(first.body);
  const question = {kind: 'ask_user', toolUseId: 'toolu_draft', questions: [{question: 'Which threshold?', options: ['3', '5']}]};
  s.overrides.push(async (input, url) => {
    if (url.pathname !== '/sessions/demo-host/ratelimit-review') return undefined;
    const base = await s.fixture.dispatch({method: 'GET', path: url.pathname}); base.data.session.pending = question; return base;
  });
  refuse = true;
  await go(page, `#/session/${s.ids.host}/${s.ids.sessions.review}`);
  await page.getByRole('textbox', {name: 'Which threshold?'}).fill('5');
  await page.getByRole('button', {name: 'Answer', exact: true}).click();
  await expect(page.locator('[data-session-error=PENDING_CHANGED]')).toBeVisible();
  const answer = s.writes.find(row => row.body?.action === 'session.answer');
  await page.reload();
  await expect(page.getByRole('textbox', {name: 'Which threshold?'})).toHaveValue('5');
  refuse = false;
  await page.getByRole('button', {name: 'Answer', exact: true}).click();
  await expect.poll(() => s.writes.filter(row => row.body?.action === 'session.answer').length).toBe(2);
  const answers = s.writes.filter(row => row.body?.action === 'session.answer');
  expect(answers[1].idempotency_key).toBe(answer.idempotency_key); expect(answers[1].body).toEqual(answer.body);
  await s.context.close();
});

test('checkpoint disclosure choices stay with their central principal', async ({browser}) => {
  let principal = 'principal-one';
  const s = await openSession(browser, {overrides: [async (input, url) => {
    if (url.pathname !== '/bootstrap') return undefined;
    const base = await s.fixture.dispatch(input);
    base.data.sync.principal_id = principal; return base;
  }]});
  const {page, ids} = s;
  const notes = () => page.locator('.session-checkpoints details').filter({has: page.getByText('Checkpoint notes', {exact: true})});
  await fromRequirements(page, ids, 'manual');
  await notes().locator('summary').click();
  await expect(notes()).toHaveAttribute('open');
  await go(page, '#/settings');
  await page.getByRole('button', {name: 'Disconnect', exact: true}).click();
  principal = 'principal-two';
  await page.locator('.connection-token input').fill('fixture-token-two');
  await page.getByRole('button', {name: 'Connect', exact: true}).click();
  await expect(page.locator('#live')).toHaveText('Connected');
  await go(page, `#/session/${ids.host}/${ids.sessions.manual}`);
  await expect(notes()).not.toHaveAttribute('open');
  expect(s.writes).toEqual([]);
  await s.context.close();
});

test('a delayed clipboard failure cannot restore withdrawn text when the session returns', async ({browser}) => {
  const s = await openSession(browser);
  const {page} = s;
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
      writeText: () => new Promise((_, reject) => {(window as any).rejectSessionCopy = () => reject(new Error('Clipboard refused'));}),
    }});
  });
  await fromRequirements(page, s.ids, 'implement');
  await expect(page.locator('.conversation-scroll .msg').last()).toBeVisible();
  await page.locator('.conversation-scroll .msg').last().getByRole('button', {name: 'Copy original', exact: true}).click();
  const unavailable: Override = (_, url) => url.pathname === `/sessions/${s.ids.host}/${s.ids.sessions.implement}` ? notFound : undefined;
  s.overrides.push(unavailable);
  s.sessionEvent('implement');
  await expect(page.locator('[data-session-withdrawn]')).toBeVisible();
  await page.evaluate(() => (window as any).rejectSessionCopy());
  s.overrides.splice(s.overrides.indexOf(unavailable), 1);
  s.sessionEvent('implement');
  await expect(page.locator('[data-session-view]')).toBeVisible();
  await expect(page.locator('.conversation-copy textarea')).toHaveCount(0);
  expect(s.writes).toEqual([]);
  await s.context.close();
});

for (const status of [403, 404, 410]) {
  test(`answer preflight withdraws the Session on a revoked read (${status})`, async ({browser}) => {
    const s = await openSession(browser);
    await fromRequirements(s.page, s.ids, 'review');
    s.overrides.push((_, url) => url.pathname === `/sessions/${s.ids.host}/${s.ids.sessions.review}`
      ? {status, data: {error: {code: status === 403 ? 'FORBIDDEN' : 'NOT_FOUND', message: 'Read withdrawn'}}} : undefined);
    await s.page.getByRole('button', {name: 'Allow', exact: true}).click();
    await expect(s.page.locator('[data-session-withdrawn]')).toBeVisible();
    await expect(s.page.locator('[data-session-view]')).toHaveCount(0);
    await expect(s.page.locator('.msg')).toHaveCount(0);
    const details = s.page.locator('[data-session-withdrawn] details');
    await expect(details.locator('summary')).toHaveText('Error details');
    await details.locator('summary').click();
    await expect(details).toContainText(status === 403 ? 'FORBIDDEN' : 'NOT_FOUND');
    expect(s.writes).toEqual([]);
    await s.context.close();
  });
}

test('a late answer preflight cannot restore withdrawn facts or submit after reinstatement', async ({browser}) => {
  const s = await openSession(browser);
  const {page} = s, path = `/sessions/${s.ids.host}/${s.ids.sessions.review}`;
  let mode = 'normal', release!: (data: any) => void;
  const held = new Promise(resolve => {release = resolve;});
  const old = await s.fixture.dispatch({method: 'GET', path});
  s.overrides.push(async (_, url) => {
    if (url.pathname !== path) return undefined;
    if (mode === 'hold') {mode = 'withdrawn'; return held;}
    if (mode === 'withdrawn') return notFound;
    if (mode === 'fresh') {
      const latest = await s.fixture.dispatch({method: 'GET', path});
      latest.data.session.title = 'Fresh central title'; return latest;
    }
    return undefined;
  });
  await fromRequirements(page, s.ids, 'review');
  mode = 'hold';
  await page.getByRole('button', {name: 'Allow', exact: true}).click();
  await expect.poll(() => mode).toBe('withdrawn');
  s.sessionEvent('review');
  await expect(page.locator('[data-session-withdrawn]')).toBeVisible();
  mode = 'fresh'; s.sessionEvent('review');
  await expect(page.locator('.workspace-session-heading h1')).toHaveText('Fresh central title');
  const completed = page.waitForResponse(response => response.url().endsWith('/api/v1' + path));
  release(old); await (await completed).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  expect(s.writes).toEqual([]);
  await expect(page.locator('.workspace-session-heading h1')).toHaveText('Fresh central title');
  await s.context.close();
});
