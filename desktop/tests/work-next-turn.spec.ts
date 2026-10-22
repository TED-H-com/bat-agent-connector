import {test, expect, type Page} from '@playwright/test';
import {followUpFixture, openWork, WS, WI, ATTEMPT} from './work-next-turn-fixture.ts';

const ui = {
  en: {
    send: 'Send next', check: 'Check original request', fresh: 'New request',
    queued: 'Queued for the next turn', accepted: 'Accepted. Queue status is unconfirmed.',
    state: 'Queued', detail: 'Waiting for the current turn to end.',
    empty: 'No next step yet.', more: 'More steps', hidden: 'Some steps are hidden because their source is not visible.',
    withdrawn: 'This work is no longer here.', unknown: 'Unknown',
    codex: 'A next step is not available for this Codex session.',
    controller: "You are not this work's controller.",
    notActor: 'Only the person who sent this instruction can cancel or reconcile it.',
    cancel: 'Cancel queued instruction', edit: 'Edit and send as new instruction',
    resume: 'Reconcile with original ID', blocked: 'Open the unconfirmed instruction',
    waiting: 'It does not interrupt.', excerpt: 'Only the opening of this instruction is shown.',
    readonly: 'Read-only', lineage: 'Lineage', live: 'Live', work: 'Work', legacy: 'Send',
  },
  'zh-TW': {
    send: '送出下一步', check: '查回原請求', fresh: '新請求',
    queued: '已排入下一輪', accepted: '已接受。排隊狀態尚未確認。',
    state: '排隊中', detail: '正在等待目前回合結束。',
    empty: '還沒有下一步。', more: '還有步驟', hidden: '有些步驟因來源不可見而沒有列出。',
    withdrawn: '這項工作已不在這裡。', unknown: '未知',
    codex: '這個 Codex 工作階段無法排入下一步。',
    controller: '你不是這項工作的控制者。',
    notActor: '只有送出這則指令的人可以取消或重新確認。',
    cancel: '取消排隊中的指令', edit: '編輯並以新指令送出',
    resume: '以原 ID 重新確認', blocked: '開啟未確認的指令',
    waiting: '不會中斷本輪。', excerpt: '只顯示這則指令的開頭。',
    readonly: '唯讀', lineage: '關聯', live: '現場', work: '工作', legacy: '送出',
  },
} as const;

type Lang = keyof typeof ui;
const section = (page: Page) => page.locator('[data-work-section="next-turn"]');
const field = (page: Page) => page.locator('[data-follow-up-input]');
const send = (page: Page, lang: Lang) => section(page).getByRole('button', {name: ui[lang].send, exact: true});
const receipts = (page: Page) => page.locator('[data-follow-up-receipt]');

for (const width of [390, 768, 1440]) {
  test(`reading controls stay above the next step at ${width}px`, async ({page}) => {
    await page.setViewportSize({width, height: width === 390 ? 844 : 900});
    const f = followUpFixture('zh-TW'), dispatch = f.dispatch;
    f.dispatch = async input => {
      const result = await dispatch(input);
      const path = new URL(input.path, 'http://fixture').pathname;
      if (path === '/capabilities' || path === '/bootstrap') {
        const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
        caps.features.session_reading = {version: 1};
      }
      return result;
    };
    await openWork(page, f, 'zh-TW');
    await field(page).fill('再補一個失敗後重新送出的案例。');
    await send(page, 'zh-TW').click();
    await expect(receipts(page)).toHaveCount(1);
    const reading = page.locator('.conversation > .actions');
    await expect(reading).toBeVisible();
    const layout = await page.locator('.workspace-conversation').evaluate(lane => ({
      readingBottom: Math.max(...[...lane.querySelectorAll('.conversation > .actions > *')]
        .map(node => node.getBoundingClientRect().bottom)),
      nextTop: lane.querySelector('[data-work-section="next-turn"]')!.getBoundingClientRect().top,
    }));
    expect(layout.readingBottom).toBeLessThanOrEqual(layout.nextTop);
  });
}

function queuedReceipt(id = 'op_' + 'c'.repeat(32), extra: Record<string, unknown> = {}) {
  return {
    operation_id: id, workspace_id: WS, work_item_id: WI, attempt_id: ATTEMPT,
    sequence: 1, state: 'queued', actor: 'owner', cancellable: true,
    text_excerpt: 'queued instruction', text_truncated: false, full_text: 'queued instruction',
    host: 'do-not-render-host', message_id: 'msg-secret', session_id: 'search-results',
    labels: {
      state: {en: 'Queued', 'zh-TW': '排隊中'},
      detail: {en: 'Waiting for the current turn to end.', 'zh-TW': '正在等待目前回合結束。'},
    },
    next_allowed_actions: [{kind: 'operation_endpoint', endpoint: 'cancel', operation_id: id, label: 'Cancel queued instruction', label_zh_TW: '取消排隊中的指令'}],
    ...extra,
  };
}

function assertWorkPost(post: any, text: string) {
  expect(post.body.action).toBe('session.send');
  expect(post.body.target).toEqual({workspace_id: WS, work_item_id: WI});
  expect(Object.keys(post.body.target).sort()).toEqual(['work_item_id', 'workspace_id']);
  expect(post.body.params).toEqual({text, schedule: 'after_current'});
  expect(post.body.preconditions).toEqual({expected_attempt_id: ATTEMPT});
  for (const key of ['host', 'session_id', 'task_id', 'attempt_id', 'message_id', 'queue', 'now']) {
    expect(post.body.target).not.toHaveProperty(key);
    expect(post.body.params).not.toHaveProperty(key);
    expect(post.body).not.toHaveProperty(key);
  }
}

for (const lang of ['en', 'zh-TW'] as const) {
  test(`${lang}: accepted second input keeps the receipt and sends the same words again as a new operation`, async ({page}) => {
    const f = followUpFixture(lang);
    await openWork(page, f, lang);
    const box = section(page);
    const input = field(page);
    const button = send(page, lang);
    await expect(button).toBeDisabled();
    await input.fill('   ');
    await expect(button).toBeDisabled();
    expect(f.follow.posts).toEqual([]);
    await input.fill('  Same text  ');
    await expect(button).toBeEnabled();
    await button.click();
    await expect(receipts(page)).toHaveCount(1);
    await expect(input).toHaveValue('');
    await expect(input).toBeFocused();
    assertWorkPost(f.follow.posts[0], 'Same text');
    await expect(box).toContainText(ui[lang].state);
    await expect(box).toContainText(ui[lang].detail);
    await expect(box.locator('[data-pm-acknowledged]')).toContainText(ui[lang].queued);
    await expect(box.locator('[data-pm-acknowledged]')).toContainText(ui[lang].state);
    await expect(box).not.toContainText('DECOY');
    await expect(box).not.toContainText('誘餌');
    await expect(box).not.toContainText('do-not-render-host');
    await expect(box).not.toContainText('msg-secret');
    await expect(box).not.toContainText('已送出');
    await expect(box.locator('[data-follow-up-receipt] a').first()).toHaveText(ui[lang].state);
    const first = f.follow.posts[0].idempotency_key;
    await input.fill('Same text');
    await button.click();
    await expect(receipts(page)).toHaveCount(2);
    expect(f.follow.posts).toHaveLength(2);
    expect(f.follow.posts[1].idempotency_key).not.toBe(first);
    assertWorkPost(f.follow.posts[1], 'Same text');
    expect(f.follow.ops.size).toBe(2);
    await expect(page.locator('#workspace-message-input')).toBeHidden();
    await expect(page.locator('.workspace-conversation .workspace-composer')).toBeHidden();
    await expect(page.locator('.workspace-conversation textarea:visible')).toHaveCount(1);
    expect(f.follow.errors).toEqual([]);
  });

  test(`${lang}: a lost acknowledgement retries the original key`, async ({page}) => {
    const f = followUpFixture(lang);
    f.follow.failPost = {status: 503, code: 'UNAVAILABLE'};
    await openWork(page, f, lang);
    await field(page).fill('retry me');
    await send(page, lang).click();
    const check = section(page).getByRole('button', {name: ui[lang].check, exact: true});
    await expect(check).toBeVisible();
    expect(f.follow.posts).toHaveLength(1);
    const key = f.follow.posts[0].idempotency_key;
    expect(f.follow.reads.filter(row => row.startsWith('GET /operations/'))).toEqual([]);
    f.follow.failPost = null;
    await check.click();
    await expect(receipts(page)).toHaveCount(1);
    expect(f.follow.posts).toHaveLength(2);
    expect(f.follow.posts[1].idempotency_key).toBe(key);
    expect(f.follow.ops.size).toBe(1);
  });
}

for (const lang of ['en', 'zh-TW'] as const) {
test(`${lang}: an acknowledgement during an admission reread frees the draft without another POST`, async ({page}) => {
  const f = followUpFixture(lang);
  let release = () => {};
  f.follow.holdPost = new Promise<void>(resolve => {release = resolve;});
  await openWork(page, f, lang);
  await field(page).fill('Accepted while admission is being checked');
  await send(page, lang).click();
  await expect.poll(() => f.follow.postStarted).toBe(true);
  f.follow.hold.result = true;
  f.pushEvent();
  await expect.poll(() => f.follow.held.length).toBeGreaterThan(0);
  release();
  await expect(receipts(page)).toHaveCount(1);
  await expect(field(page)).toHaveValue('');
  await expect(field(page)).toBeFocused();
  await field(page).fill('  Keep this next draft 原文\n  ');
  await expect(send(page, lang)).toBeDisabled();
  expect(f.follow.posts).toHaveLength(1);
  f.follow.hold.result = false;
  f.follow.releaseHeld();
  await expect(send(page, lang)).toBeEnabled();
  await expect(field(page)).toHaveValue('  Keep this next draft 原文\n  ');
  expect(f.follow.posts).toHaveLength(1);
});
}

test('a held accept is not a local queue, and the field is free once the id exists', async ({page}) => {
  const f = followUpFixture('en');
  let release = () => {};
  f.follow.holdPost = new Promise<void>(resolve => { release = resolve; });
  await openWork(page, f, 'en');
  await field(page).fill('After this turn');
  await send(page, 'en').click();
  await expect.poll(() => f.follow.postStarted).toBe(true);
  await expect(page.locator('[data-follow-up-list]')).not.toContainText('After this turn');
  await expect(send(page, 'en')).toHaveAttribute('aria-disabled', 'true');
  expect(f.follow.posts).toHaveLength(1);
  release();
  await expect(receipts(page)).toHaveCount(1);
  await expect(field(page)).toHaveValue('');
  await expect(field(page)).toBeFocused();
  assertWorkPost(f.follow.posts[0], 'After this turn');
});

for (const lang of ['en', 'zh-TW'] as const) {
  test(`${lang}: an attempt error without admission refusal keeps the original request`, async ({page}) => {
    const f = followUpFixture(lang);
    const next = 'att_' + 'c'.repeat(32);
    let release = () => {};
    f.follow.holdPost = new Promise<void>(resolve => { release = resolve; });
    f.follow.failPost = {status: 409, code: 'INVALID_PRECONDITIONS'};
    await openWork(page, f, lang);
    await field(page).fill('Keep this wording');
    await send(page, lang).click();
    await expect.poll(() => f.follow.postStarted).toBe(true);
    f.follow.latest = next;
    release();
    const check = section(page).getByRole('button', {name: ui[lang].check, exact: true});
    await expect(check).toBeVisible();
    await expect(section(page).getByRole('button', {name: ui[lang].fresh, exact: true})).toHaveCount(0);
    await expect(field(page)).toHaveValue('Keep this wording');
    await expect(field(page)).toBeDisabled();
    await expect(section(page)).toContainText(lang === 'zh-TW' ? '前置條件已變更。原請求維持不變。' : 'The preconditions changed. The original request stays in place.');
    expect(f.follow.posts).toHaveLength(1);
    const body = f.follow.posts[0].body;
    const key = f.follow.posts[0].idempotency_key;
    expect(body.preconditions).toEqual({expected_attempt_id: ATTEMPT});
    await expect(check).toBeEnabled();
    await check.click();
    await expect.poll(() => f.follow.posts.length).toBe(2);
    expect(f.follow.posts[1].idempotency_key).toBe(key);
    expect(f.follow.posts[1].body).toEqual(body);
    await expect(section(page).getByRole('button', {name: ui[lang].fresh, exact: true})).toHaveCount(0);
    expect(f.follow.posts).toHaveLength(2);
    f.follow.failPost = null;
    await expect(check).toBeEnabled();
    await check.click();
    await expect(receipts(page)).toHaveCount(1);
    expect(f.follow.posts).toHaveLength(3);
    expect(f.follow.posts[2].idempotency_key).toBe(key);
    expect(f.follow.posts[2].body).toEqual(body);
    expect(f.follow.ops.size).toBe(1);
  });
}

test('a proven admission refusal keeps the explicitly selected attempt for a new request', async ({page}) => {
  const f = followUpFixture('en');
  const next = 'att_' + 'c'.repeat(32);
  f.follow.failPost = {status: 409, code: 'PM_WORK_ATTEMPT_CHANGED', admission_refused: true};
  await openWork(page, f, 'en');
  await field(page).fill('Write it again');
  const reads = f.follow.reads.length;
  f.follow.latest = next;
  await send(page, 'en').click();
  const fresh = section(page).getByRole('button', {name: 'New request', exact: true});
  await expect(fresh).toBeVisible();
  await expect(section(page)).toContainText('This work has a newer attempt. The original request stays in place.');
  expect(f.follow.posts).toHaveLength(1);
  expect(f.follow.posts[0].body.preconditions).toEqual({expected_attempt_id: ATTEMPT});
  await expect.poll(() => f.follow.reads.length).toBeGreaterThan(reads);
  expect(f.follow.reads.some(row => row.includes('/work-attempts/') && row.includes(next))).toBe(false);
  await fresh.click();
  f.follow.failPost = null;
  await expect(send(page, 'en')).toBeEnabled();
  await send(page, 'en').click();
  await expect(receipts(page)).toHaveCount(1);
  expect(f.follow.posts).toHaveLength(2);
  expect(f.follow.posts[1].idempotency_key).not.toBe(f.follow.posts[0].idempotency_key);
  expect(f.follow.posts[1].body.preconditions).toEqual({expected_attempt_id: ATTEMPT});
  expect(f.follow.posts[1].body.params).toEqual({text: 'Write it again', schedule: 'after_current'});
});

test('an idempotency conflict retries the original key', async ({page}) => {
  const f = followUpFixture('en');
  f.follow.failPost = {status: 409, code: 'IDEMPOTENCY_CONFLICT'};
  await openWork(page, f, 'en');
  await field(page).fill('Conflict stays');
  await send(page, 'en').click();
  const check = section(page).getByRole('button', {name: 'Check original request', exact: true});
  await expect(check).toBeVisible();
  const conflict = f.follow.posts.at(-1).idempotency_key;
  const body = f.follow.posts.at(-1).body;
  f.follow.failPost = null;
  await check.click();
  expect(f.follow.posts.at(-1).idempotency_key).toBe(conflict);
  expect(f.follow.posts.at(-1).body).toEqual(body);
});

test('an admission refusal uses New request instead of the original key', async ({page}) => {
  const f = followUpFixture('en');
  f.follow.failPost = {status: 422, code: 'PM_WORK_SESSION_NOT_MANAGED', admission_refused: true};
  await openWork(page, f, 'en');
  await field(page).fill('Not managed yet');
  await send(page, 'en').click();
  const fresh = section(page).getByRole('button', {name: 'New request', exact: true});
  await expect(fresh).toBeVisible();
  await expect(section(page)).toContainText('not a managed session');
  expect(f.follow.posts).toHaveLength(1);
  const refused = f.follow.posts[0].idempotency_key;
  await fresh.click();
  f.follow.failPost = null;
  await expect(send(page, 'en')).toBeEnabled();
  await send(page, 'en').click();
  await expect(receipts(page)).toHaveCount(1);
  expect(f.follow.posts).toHaveLength(2);
  expect(f.follow.posts[1].idempotency_key).not.toBe(refused);
});

test('remount and reload show the server receipt without another send', async ({page}) => {
  const f = followUpFixture('en');
  await openWork(page, f, 'en');
  await field(page).fill('Remember this receipt');
  await send(page, 'en').click();
  await expect(receipts(page)).toHaveCount(1);
  const posts = f.follow.posts.length;
  const operationReads = () => f.follow.reads.filter(row => row.startsWith('GET /operations/')).length;
  const before = operationReads();
  await page.evaluate(id => { location.hash = '#/pm/' + id + '/discussions'; }, WS);
  await expect(page.locator('[data-workspace-id]')).toHaveAttribute('data-workspace-id', WS);
  await page.evaluate(({work, workspace, attempt}) => { location.hash = '#/item/' + work + '?' + new URLSearchParams({workspace, work, attempt}); }, {work: WI, workspace: WS, attempt: ATTEMPT});
  await expect(receipts(page)).toHaveCount(1);
  await expect(section(page)).toContainText('Remember this receipt');
  expect(f.follow.posts).toHaveLength(posts);
  expect(operationReads()).toBe(before);
  await page.goto(`/dashboard/?workbench=1#/pm/${WS}/discussions`);
  await expect(page.locator('[data-workspace-id]')).toHaveAttribute('data-workspace-id', WS);
  await page.evaluate(({work, workspace, attempt}) => { location.hash = '#/item/' + work + '?' + new URLSearchParams({workspace, work, attempt}); }, {work: WI, workspace: WS, attempt: ATTEMPT});
  await expect(receipts(page)).toHaveCount(1);
  expect(f.follow.posts).toHaveLength(posts);
  expect(operationReads()).toBe(before);
});

test('Work, Lineage and Live keep the field, the selection and the result', async ({page}) => {
  const f = followUpFixture('en');
  await openWork(page, f, 'en');
  const input = field(page);
  await input.fill('abcdef');
  await input.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(2, 5));
  await page.evaluate(() => {
    const bag = window as unknown as {nodes: Record<string, Node>};
    bag.nodes = {
      field: document.querySelector('[data-follow-up-input]')!,
      result: document.querySelector('[data-workspace-result]')!,
      legacy: document.querySelector('#workspace-message-input textarea')!,
    };
  });
  const tabs = page.locator('[data-workbench-views]');
  await tabs.getByRole('tab', {name: 'Lineage', exact: true}).click();
  await tabs.getByRole('tab', {name: 'Live', exact: true}).click();
  await tabs.getByRole('tab', {name: 'Work', exact: true}).click();
  const kept = await page.evaluate(() => {
    const bag = (window as unknown as {nodes: Record<string, Node>}).nodes;
    const node = document.querySelector('[data-follow-up-input]') as HTMLTextAreaElement;
    return {
      field: node === bag.field, result: document.querySelector('[data-workspace-result]') === bag.result,
      legacy: document.querySelector('#workspace-message-input textarea') === bag.legacy,
      value: node.value, start: node.selectionStart, end: node.selectionEnd,
    };
  });
  expect(kept).toEqual({field: true, result: true, legacy: true, value: 'abcdef', start: 2, end: 5});
  expect(f.follow.posts).toEqual([]);
});

test('zh-TW Work, Lineage and Live keep an unsent draft', async ({page}) => {
  const f = followUpFixture('zh-TW');
  await openWork(page, f, 'zh-TW');
  await field(page).fill('先留著這段');
  const tabs = page.locator('[data-workbench-views]');
  await tabs.getByRole('tab', {name: '關聯', exact: true}).click();
  await tabs.getByRole('tab', {name: '現場', exact: true}).click();
  await tabs.getByRole('tab', {name: '工作', exact: true}).click();
  await expect(field(page)).toHaveValue('先留著這段');
  const keys = await page.evaluate(() => Object.keys(localStorage).filter(key => key.includes('session.send')));
  expect(keys.some(key => key.includes(WS) && key.includes(WI))).toBe(true);
  expect(keys.some(key => key.includes('search-results'))).toBe(false);
});

test('composition and IME Enter do not send', async ({page}) => {
  const f = followUpFixture('zh-TW');
  await openWork(page, f, 'zh-TW');
  await field(page).evaluate((node: HTMLTextAreaElement) => {
    node.focus();
    node.value = '組成中';
    node.dispatchEvent(new InputEvent('input', {bubbles: true}));
    node.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true}));
    node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 229, isComposing: true, bubbles: true, cancelable: true}));
    node.form?.requestSubmit();
  });
  expect(f.follow.posts).toEqual([]);
  await field(page).evaluate((node: HTMLTextAreaElement) => {
    node.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 229, isComposing: true, bubbles: true, cancelable: true}));
    node.form?.requestSubmit();
    node.dispatchEvent(new CompositionEvent('compositionend', {bubbles: true, data: '組成中'}));
    node.form?.requestSubmit();
  });
  expect(f.follow.posts).toEqual([]);
  await expect.poll(() => field(page).evaluate((node: HTMLTextAreaElement) => !node.form?.querySelector('button[type=submit]')?.hasAttribute('disabled')
    && node.form?.querySelector('button[type=submit]')?.getAttribute('aria-disabled') !== 'true')).toBe(true);
  await page.waitForTimeout(80);
  await field(page).press('Control+Enter');
  await expect.poll(() => f.follow.posts.length).toBe(1);
  expect(f.follow.posts[0].body.params.text).toBe('組成中');
});

test('cancel focuses edit, resume reconciles, and a blocked row does not resume itself', async ({page}) => {
  const f = followUpFixture('en');
  const id = 'op_' + 'c'.repeat(32);
  const later = 'op_' + 'd'.repeat(32);
  f.follow.items = [
    queuedReceipt(id, {full_text: 'the full instruction', text_excerpt: 'the opening'}),
    queuedReceipt(later, {
      sequence: 2, state: 'queued', cancellable: false, blocked_by_operation_id: id, text_excerpt: 'waiting behind',
      next_allowed_actions: [{kind: 'operation_endpoint', endpoint: 'resume', operation_id: id, label: 'Reconcile with original ID', label_zh_TW: '以原 ID 重新確認'}],
    }),
  ];
  await openWork(page, f, 'en');
  const laterRow = page.locator(`[data-follow-up-receipt="${later}"]`);
  await expect(laterRow.getByRole('link', {name: 'Open the unconfirmed instruction'})).toHaveAttribute('href', `#/op/${id}`);
  await expect(laterRow.getByRole('button')).toHaveCount(0);
  await page.locator(`[data-follow-up-receipt="${id}"]`).getByRole('button', {name: 'Cancel queued instruction'}).click();
  await expect(page.locator(`[data-follow-up-receipt="${id}"]`).getByRole('button', {name: 'Edit and send as new instruction'})).toBeFocused();
  const cancel = f.follow.posts.find(row => String(row.path).endsWith('/cancel'));
  expect(cancel?.body).toEqual({});
  expect(f.follow.posts.filter(row => row.body?.action === 'session.send')).toEqual([]);
  await page.locator(`[data-follow-up-receipt="${id}"]`).getByRole('button', {name: 'Edit and send as new instruction'}).click();
  await expect(field(page)).toHaveValue('the full instruction');
  expect(f.follow.posts.filter(row => row.body?.action === 'session.send')).toEqual([]);
  expect(f.follow.reads.filter(row => row.startsWith('GET /operations/'))).toEqual([]);
});

test('zh-TW hides a cancel action that has no Traditional Chinese label', async ({page}) => {
  const f = followUpFixture('zh-TW');
  const id = 'op_' + 'c'.repeat(32);
  f.follow.items = [queuedReceipt(id, {next_allowed_actions: [{kind: 'operation_endpoint', endpoint: 'cancel', operation_id: id, label: 'Cancel queued instruction'}]})];
  await openWork(page, f, 'zh-TW');
  await expect(page.locator('[data-follow-up-action="cancel"]')).toHaveCount(0);
  await expect(section(page)).not.toContainText('Cancel queued instruction');
});

test('cancel after send removes the button and does not restore the text', async ({page}) => {
  const f = followUpFixture('en');
  f.follow.cancelMode = 'denied';
  f.follow.items = [queuedReceipt()];
  await openWork(page, f, 'en');
  await page.getByRole('button', {name: 'Cancel queued instruction'}).click();
  await expect(section(page)).toContainText('can no longer be withdrawn');
  await expect(page.locator('[data-follow-up-action="cancel"]')).toHaveCount(0);
  await expect(field(page)).toHaveValue('');
  expect(f.follow.posts.filter(row => row.body?.action === 'session.send')).toEqual([]);
});

test('resume posts the original operation and does not send the text again', async ({page}) => {
  const f = followUpFixture('en');
  const id = 'op_' + 'c'.repeat(32);
  f.follow.items = [queuedReceipt(id, {
    state: 'unknown', cancellable: false,
    labels: {state: {en: 'Delivery unconfirmed'}, detail: {en: 'The service has not confirmed this yet.'}},
    next_allowed_actions: [{kind: 'operation_endpoint', endpoint: 'resume', operation_id: id, label: 'Reconcile with original ID', label_zh_TW: '以原 ID 重新確認'}],
  })];
  await openWork(page, f, 'en');
  await expect(page.locator('[data-follow-up-action="cancel"]')).toHaveCount(0);
  await page.getByRole('button', {name: 'Reconcile with original ID'}).click();
  await expect.poll(() => f.follow.posts.some(row => String(row.path).endsWith('/resume'))).toBe(true);
  expect(f.follow.posts.find(row => String(row.path).endsWith('/resume')).body).toEqual({});
  expect(f.follow.posts.filter(row => row.body?.action === 'session.send')).toEqual([]);
});

test('edit with no stored text leaves the field empty', async ({page}) => {
  const f = followUpFixture('en');
  const id = 'op_' + 'c'.repeat(32);
  f.follow.items = [queuedReceipt(id, {state: 'failed', text_excerpt: 'only the opening', editText: {schedule: 'after_current'}, cancellable: false, next_allowed_actions: []})];
  f.follow.items[0].next_allowed_actions = [{
    kind: 'action', action: 'session.send', target: {workspace_id: WS, work_item_id: WI},
    params: {schedule: 'after_current'}, label: 'Retry as new instruction', label_zh_TW: '以新指令重試',
  }];
  await openWork(page, f, 'en');
  await page.getByRole('button', {name: 'Retry as new instruction'}).click();
  await expect(field(page)).toHaveValue('');
  await expect(field(page)).toBeFocused();
  await expect(section(page)).toContainText('only the opening');
  expect(f.follow.posts).toEqual([]);
});

test('another writer can send and cannot cancel someone else\'s receipt', async ({page}) => {
  const f = followUpFixture('en');
  f.follow.items = [queuedReceipt('op_' + 'c'.repeat(32), {actor: 'other'})];
  await openWork(page, f, 'en');
  await expect(section(page)).toContainText('Only the person who sent this instruction can cancel or reconcile it.');
  await expect(page.locator('[data-follow-up-action="cancel"]')).toHaveCount(0);
  await field(page).fill('A different instruction');
  await send(page, 'en').click();
  await expect.poll(() => f.follow.posts.length).toBe(1);
  assertWorkPost(f.follow.posts[0], 'A different instruction');
});

for (const item of [
  {name: 'manual', options: {provenance: 'manual', api_access: 'read_only'}},
  {name: 'unknown', options: {provenance: 'unknown', api_access: 'read_only'}},
  {name: 'codex', options: {agent: 'codex', result: 'codex'}},
  {name: 'controller', options: {result: 'not_controller'}},
]) {
  test(`${item.name} work gets no follow-up mutations`, async ({page}) => {
    const f = followUpFixture('en', item.options);
    f.follow.items = [queuedReceipt()];
    await openWork(page, f, 'en');
    await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
    await expect(page.locator('[data-follow-up-action]')).toHaveCount(0);
    if (item.name === 'codex') await expect(section(page)).toContainText('not available for this Codex session');
    if (item.name === 'controller') await expect(section(page)).toContainText("not this work's controller");
    expect(f.follow.posts.filter(row => row.body?.action === 'session.send')).toEqual([]);
  });
}

test('a waiting turn still queues after the current turn and does not interrupt', async ({page}) => {
  const f = followUpFixture('en');
  f.follow.items = [queuedReceipt('op_' + 'c'.repeat(32), {status_reason: 'waiting_permission_or_question_gate'})];
  await openWork(page, f, 'en');
  await expect(section(page)).toContainText('does not interrupt');
  await expect(section(page).getByRole('button', {name: 'Interrupt', exact: true})).toHaveCount(0);
  await field(page).fill('Stay in the queue');
  await send(page, 'en').click();
  await expect.poll(() => f.follow.posts.length).toBe(1);
  assertWorkPost(f.follow.posts[0], 'Stay in the queue');
});

test('a missing operate scope keeps history without a composer', async ({page}) => {
  const scoped = followUpFixture('en', {operate: false});
  scoped.follow.items = [queuedReceipt()];
  await openWork(page, scoped, 'en');
  await expect(page.locator('[data-follow-up-pill]')).toHaveText('API read-only');
  await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
  await expect(receipts(page)).toHaveCount(1);
});

test('a forbidden result withdraws receipts and controls', async ({page}) => {
  const denied = followUpFixture('en', {resultStatus: 403});
  denied.follow.items = [queuedReceipt()];
  await openWork(page, denied, 'en');
  await expect(page.locator('[data-follow-up-withdrawn]')).toBeVisible();
  await expect(receipts(page)).toHaveCount(0);
  await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
  await expect(section(page)).not.toContainText('queued instruction');
});

test('a missing result withdraws the list', async ({page}) => {
  const f = followUpFixture('en', {resultStatus: 404});
  f.follow.items = [queuedReceipt()];
  await openWork(page, f, 'en');
  await expect(page.locator('[data-follow-up-withdrawn]')).toBeVisible();
  await expect(receipts(page)).toHaveCount(0);
  await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
});

test('withdrawal drops the list and a late 200 does not restore it', async ({page}) => {
  const f = followUpFixture('en');
  f.follow.holdFiltered = true;
  f.follow.items = [queuedReceipt()];
  await openWork(page, f, 'en');
  await expect.poll(() => f.follow.holdConsumed).toBe(true);
  f.follow.inputsStatus = 404;
  f.pushEvent('operation');
  await expect(page.locator('[data-follow-up-withdrawn]')).toBeVisible({timeout: 15000});
  const late = page.waitForResponse(response => response.url().includes('/inputs') && response.url().includes('attempt_id=') && response.status() === 200);
  f.follow.releaseHold();
  await late;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(undefined)))));
  await expect(page.locator('[data-follow-up-withdrawn]')).toBeVisible();
  await expect(receipts(page)).toHaveCount(0);
  await expect(section(page)).not.toContainText('queued instruction');
  await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
  expect(f.follow.lateReturned).toBe(true);
});

test('more steps, hidden rows, an unknown label and a bad page stay honest', async ({page}) => {
  const f = followUpFixture('zh-TW');
  const first = queuedReceipt('op_' + 'a'.repeat(32), {sequence: 2, text_excerpt: '第二則'});
  const second = queuedReceipt('op_' + 'b'.repeat(32), {sequence: 1, text_excerpt: '第一則', text_truncated: true});
  f.follow.items = [first];
  f.follow.hasMore = true;
  f.follow.cursor = 'page-2';
  f.follow.pages.set('page-2', {
    workspace_id: WS, work_item_id: WI, attempt_id: ATTEMPT, items: [second], next_cursor: null, has_more: false,
    latest_attempt_id: ATTEMPT, visibility_filtered: true,
  });
  await openWork(page, f, 'zh-TW');
  await expect(section(page).getByRole('button', {name: '還有步驟', exact: true})).toBeVisible();
  await expect(section(page)).not.toContainText('總共');
  await section(page).getByRole('button', {name: '還有步驟', exact: true}).click();
  await expect(section(page)).toContainText('第一則');
  await expect(section(page)).toContainText('第二則');
  await expect(section(page)).toContainText('只顯示這則指令的開頭。');
  await expect(page.locator('[data-follow-up-hidden]')).toContainText('有些步驟因來源不可見而沒有列出。');
  const order = await receipts(page).evaluateAll(nodes => nodes.map(node => node.getAttribute('data-follow-up-receipt')));
  expect(order).toEqual(['op_' + 'b'.repeat(32), 'op_' + 'a'.repeat(32)]);
  const more = f.follow.reads.filter(row => row.includes('after=page-2'));
  expect(more.length).toBeGreaterThan(0);
  f.follow.items = [{
    ...queuedReceipt('op_' + '9'.repeat(32)), labels: {state: {en: 'Queued'}, detail: {en: 'English detail'}},
    text_excerpt: '', next_allowed_actions: [],
  }];
  f.follow.hasMore = false;
  f.follow.cursor = null;
  f.follow.filtered = false;
  f.pushEvent('operation');
  await expect(section(page).locator('[data-follow-up-receipt]').first()).toContainText('未知', {timeout: 15000});
  await expect(section(page)).not.toContainText('English detail');
  await expect(section(page)).not.toContainText('Queued');
  f.follow.malformed = true;
  f.pushEvent('operation');
  await expect(section(page)).toContainText('這一步無法送出。', {timeout: 15000});
  await expect(receipts(page)).toHaveCount(1);
  await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
  await expect(page.locator('[data-follow-up-action]')).toHaveCount(0);
});

test('an empty list is not a zero count', async ({page}) => {
  const f = followUpFixture('en');
  f.follow.filtered = true;
  await openWork(page, f, 'en');
  await expect(section(page)).toContainText('No next step yet.');
  await expect(page.locator('[data-follow-up-hidden]')).toContainText('Some steps are hidden');
  await expect(section(page)).not.toContainText(/\b0\b/);
});

test('text longer than 20000 characters is not sent', async ({page}) => {
  const f = followUpFixture('en');
  await openWork(page, f, 'en');
  await field(page).evaluate((node: HTMLTextAreaElement) => {
    node.value = 'x'.repeat(20001);
    node.dispatchEvent(new Event('input', {bubbles: true}));
  });
  await expect(send(page, 'en')).toBeDisabled();
  await field(page).fill('short enough');
  await expect(send(page, 'en')).toBeEnabled();
  expect(f.follow.posts).toEqual([]);
});

for (const lang of ['en', 'zh-TW'] as const) {
  test(`${lang}: the work route has one instruction composer and keeps the old draft`, async ({page}) => {
    const allow = lang === 'zh-TW' ? '允許' : 'Allow';
    const deny = lang === 'zh-TW' ? '拒絕' : 'Deny';
    const f = followUpFixture(lang, {pending: {
      kind: 'permission', toolUseId: 'toolu_next', toolName: 'Bash', input_preview: 'npm test',
    }});
    await openWork(page, f, lang);
    await expect(field(page)).toBeVisible();
    await expect(page.locator('#workspace-message-input')).toBeHidden();
    await expect(page.locator('.workspace-conversation textarea:visible')).toHaveCount(1);
    await expect(page.getByRole('button', {name: allow, exact: true})).toBeVisible();
    await expect(page.getByRole('button', {name: deny, exact: true})).toBeVisible();
    // Rendering precedes the completed snapshot's sync checkpoint. Let that
    // checkpoint commit before navigation invalidates the in-flight view.
    await expect.poll(() => page.evaluate(() =>
      Object.keys(localStorage).some(key => key.startsWith('batc.sync.')),
    )).toBe(true);
    await page.evaluate(id => { location.hash = '#/pm/' + id + '/discussions'; }, WS);
    await page.evaluate(() => {
      const key = Object.keys(localStorage).find(item => item.startsWith('batc.sync.'));
      if (!key) throw new Error('missing sync scope');
      const ns = key.slice('batc.sync.'.length);
      localStorage.setItem(`batc.draft.${ns}.send.demo.search-results`, 'keep the old draft');
    });
    await page.evaluate(({work, workspace, attempt}) => { location.hash = '#/item/' + work + '?' + new URLSearchParams({workspace, work, attempt}); }, {work: WI, workspace: WS, attempt: ATTEMPT});
    await expect(field(page)).toBeVisible();
    await expect(field(page)).toHaveValue('');
    await expect(page.locator('#workspace-message-input textarea')).toHaveValue('keep the old draft');
    await expect(page.locator('#workspace-message-input')).toBeHidden();
    expect(f.follow.posts).toEqual([]);
    expect(f.data.posts).toEqual([]);
    await field(page).fill('next instruction');
    await send(page, lang).click();
    await expect.poll(() => f.follow.posts.length).toBe(1);
    assertWorkPost(f.follow.posts[0], 'next instruction');
    expect(f.data.posts).toEqual([]);
    await expect(page.locator('#workspace-message-input textarea')).toHaveValue('keep the old draft');
  });

  test(`${lang}: a missing or non-queued receipt is not called queued`, async ({page}) => {
    const f = followUpFixture(lang);
    f.follow.receiptMode = 'missing';
    f.follow.opStatus = 'failed';
    await openWork(page, f, lang);
    await field(page).fill('no receipt');
    await send(page, lang).click();
    const ack = section(page).locator('[data-pm-acknowledged]');
    await expect(ack).toHaveAttribute('data-follow-up-ack', 'unknown');
    await expect(ack).not.toContainText(ui[lang].queued);
    await expect(ack.locator('a')).toHaveAttribute('href', /^#\/op\/op_[0-9a-f]{32}$/);
    f.follow.opStatus = 'succeeded';
    await field(page).fill('accepted without a receipt');
    await send(page, lang).click();
    await expect(ack).toHaveAttribute('data-follow-up-ack', 'accepted');
    await expect(ack).toContainText(ui[lang].accepted);
    await expect(ack).not.toContainText(ui[lang].queued);
    f.follow.receiptMode = 'mismatch';
    f.follow.opStatus = 'queued';
    await field(page).fill('wrong receipt');
    await send(page, lang).click();
    await expect(ack).toHaveAttribute('data-follow-up-ack', 'unknown');
    await expect(ack).not.toContainText(ui[lang].queued);
    f.follow.receiptMode = 'normal';
    f.follow.receiptState = 'failed';
    f.follow.opStatus = 'failed';
    await field(page).fill('failed receipt');
    await send(page, lang).click();
    await expect(ack).toHaveAttribute('data-follow-up-ack', 'failed');
    await expect(ack).not.toContainText(ui[lang].queued);
    expect(f.follow.posts).toHaveLength(4);
    expect(new Set(f.follow.posts.map(row => row.idempotency_key)).size).toBe(4);
  });
}

test('a held offer does not return after a denial or a malformed result', async ({page}) => {
  const f = followUpFixture('en');
  f.follow.items = [queuedReceipt()];
  await openWork(page, f, 'en');
  await expect(field(page)).toBeVisible();
  await expect(receipts(page)).toHaveCount(1);
  // The form refresh is not the event queue, so a later denial can finish while this result is still held.
  f.follow.hold.result = true;
  f.follow.failPost = {status: 409, code: 'INVALID_PRECONDITIONS'};
  await field(page).fill('held refresh');
  await send(page, 'en').click();
  await expect.poll(() => f.follow.held.length).toBeGreaterThan(0);
  await expect(section(page).getByRole('button', {name: ui.en.check, exact: true})).toBeDisabled();
  f.follow.resultStatus = 403;
  f.follow.hold.result = false;
  f.pushEvent('operation');
  await expect(page.locator('[data-follow-up-withdrawn]')).toBeVisible({timeout: 15000});
  f.follow.releaseHeld();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(undefined)))));
  await expect(page.locator('[data-follow-up-withdrawn]')).toBeVisible();
  await expect(receipts(page)).toHaveCount(0);
  await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
  await expect(section(page)).not.toContainText('queued instruction');
});

test('zh-TW: a held offer does not return after a malformed result', async ({page}) => {
  const f = followUpFixture('zh-TW');
  f.follow.items = [queuedReceipt()];
  await openWork(page, f, 'zh-TW');
  await expect(field(page)).toBeVisible();
  f.follow.hold.result = true;
  f.follow.failPost = {status: 409, code: 'PM_WORK_ATTEMPT_CHANGED'};
  await field(page).fill('保留這次讀取');
  await send(page, 'zh-TW').click();
  await expect.poll(() => f.follow.held.length).toBeGreaterThan(0);
  f.follow.result = 'bad';
  f.follow.hold.result = false;
  f.pushEvent('operation');
  await expect(section(page)).toContainText('這一步無法送出。', {timeout: 15000});
  await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
  f.follow.releaseHeld();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(undefined)))));
  await expect(page.locator('[data-follow-up-input]')).toHaveCount(0);
  await expect(page.locator('[data-follow-up-action]')).toHaveCount(0);
  await expect(receipts(page)).toHaveCount(1);
  await expect(section(page)).toContainText('這一步無法送出。');
});

for (const lang of ['en', 'zh-TW'] as const) {
  test(`${lang}: an acknowledged row disappears when the list no longer shows it`, async ({page}) => {
    const f = followUpFixture(lang);
    await openWork(page, f, lang);
    await field(page).fill('visible then hidden');
    await send(page, lang).click();
    await expect(receipts(page)).toHaveCount(1);
    f.follow.items = [];
    f.follow.filtered = true;
    f.follow.hasMore = true;
    f.follow.cursor = 'later';
    f.pushEvent('operation');
    await expect(receipts(page)).toHaveCount(0, {timeout: 15000});
    await expect(section(page)).not.toContainText('visible then hidden');
    await expect(page.locator('[data-follow-up-hidden]')).toBeVisible();
    await expect(section(page).getByRole('button', {name: ui[lang].more, exact: true})).toBeVisible();
    await expect(section(page)).not.toContainText(lang === 'zh-TW' ? '總共' : 'total');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(undefined)))));
    await expect(receipts(page)).toHaveCount(0);
  });
}

for (const feature of [null, {version: 1, available: false}, {version: 2, available: true}] as const) {
  test(`follow-up ${JSON.stringify(feature)} is not advertised`, async ({page}) => {
    const f = followUpFixture('en', {feature});
    await openWork(page, f, 'en', {section: false});
    await expect(page.locator('[data-workspace-id]')).toHaveAttribute('data-workspace-id', WS);
    await expect(page.locator('[data-work-section="next-turn"]')).toHaveCount(0);
    await expect(page.locator('#main')).not.toContainText('Next step');
    await expect(page.locator('#main')).not.toContainText('Send next');
    expect(f.follow.reads.some(row => row.includes('/inputs') || row.includes('/work-attempts/'))).toBe(false);
    await expect(page.locator('#workspace-message-input')).toBeVisible();
  });
}

test('the native shell still requires a supported follow-up capability', async ({page}) => {
  const f = followUpFixture('en', {native: true, feature: {version: 1, available: false}});
  await openWork(page, f, 'en', {section: false});
  await expect(page.locator('[data-work-section="next-turn"]')).toHaveCount(0);
  expect(f.follow.reads.some(row => row.includes('/inputs') || row.includes('/work-attempts/'))).toBe(false);
});

test('zh-TW layouts at 1440, 768 and 390', async ({page}, testInfo) => {
  test.setTimeout(90000);
  const title = '改善文件搜尋在沒有結果時的提示，並保留讀者原本輸入的關鍵字、焦點與下一步指令，讓很長的中文標題在窄螢幕上仍然可以完整換行閱讀';
  const f = followUpFixture('zh-TW', {title});
  f.follow.items = [queuedReceipt('op_' + 'c'.repeat(32), {
    text_excerpt: '請在這一輪結束後，把空結果的提示改成保留搜尋字、輸入焦點與重新搜尋的測試，不要把尚未確認的步驟顯示成已經送出。',
  })];
  await openWork(page, f, 'zh-TW');
  await expect(page.locator('.workspace-session-heading h1')).toContainText('改善文件搜尋');
  for (const [width, height] of [[1440, 900], [768, 1024], [390, 844]] as const) {
    await page.setViewportSize({width, height});
    await section(page).scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    await expect(section(page)).not.toContainText(/undefined|NaN|\bnull\b/);
    await page.screenshot({path: testInfo.outputPath(`queued-${width}.png`), animations: 'disabled'});
  }
  f.follow.items = [];
  f.follow.filtered = false;
  f.pushEvent('operation');
  await expect(section(page)).toContainText('還沒有下一步。', {timeout: 15000});
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height: width === 390 ? 844 : 900});
    await section(page).scrollIntoViewIfNeeded();
    await page.screenshot({path: testInfo.outputPath(`empty-${width}.png`), animations: 'disabled'});
  }
  f.follow.items = [{
    ...queuedReceipt('op_' + '9'.repeat(32), {state: 'unknown', text_excerpt: '這次送出的結果尚未確認。', next_allowed_actions: []}),
    labels: {},
  }];
  f.pushEvent('operation');
  await expect(section(page)).toContainText('未知', {timeout: 15000});
  await expect(section(page)).toContainText('結果尚未確認');
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({width, height: width === 390 ? 844 : 900});
    await section(page).scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    await page.screenshot({path: testInfo.outputPath(`unknown-${width}.png`), animations: 'disabled'});
  }
});
