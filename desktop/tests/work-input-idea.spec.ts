import {test, expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {followUpFixture, openWork, WS, WI, ATTEMPT} from './work-next-turn-fixture.ts';

const SOURCE = 'op_' + 'c'.repeat(32), PARENT = 'op_' + 'd'.repeat(32), THREAD = 'pmt_' + 'e'.repeat(32);
const ACTION = 'pm.work_input.defer';
const copy = {
  en: {open: 'Keep for later', reason: 'Reason for keeping it for later', save: 'Cancel queue and save Idea',
    draft: 'Open Idea draft', check: 'Check original request'},
  'zh-TW': {open: '留到稍後', reason: '留到稍後的原因', save: '取消排隊並保存想法',
    draft: '開啟想法草稿', check: '查回原請求'},
};

function fixture(lang = 'en', native = false) {
  const f = followUpFixture(lang, {native});
  const target = {workspace_id: WS, work_item_id: WI, source_operation_id: SOURCE};
  const row = {operation_id: SOURCE, workspace_id: WS, work_item_id: WI, attempt_id: ATTEMPT,
    sequence: 1, state: 'queued', actor: 'owner', cancellable: true,
    text_excerpt: lang === 'en' ? 'Review the remaining acceptance evidence tomorrow.' : '明天再整理剩餘的驗收證據。',
    labels: {state: {en: 'Queued', 'zh-TW': '排隊中'}, detail: {en: 'Waiting for the current turn.', 'zh-TW': '等待目前回合完成。'}},
    next_allowed_actions: [{kind: 'action', action: ACTION, target, preconditions: {expected_state: 'queued'}}]};
  f.follow.items = [row];
  const state = {posts: [] as any[], saved: null as any, loseReply: false, allowed: true,
    holdReply: null as Promise<void> | null};
  const dispatch = f.dispatch;
  f.dispatch = async input => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (input.method === 'POST' && path === '/operations' && input.body?.action === ACTION) {
      state.posts.push(input);
      if (!state.saved) state.saved = {operation_id: PARENT, actor: 'owner', idempotency_key: input.idempotency_key,
        ...input.body, status: 'succeeded', result: {...target, thread_id: THREAD, execution_authorized: false}};
      f.follow.items = [{...row, state: 'cancelled', cancellable: false, next_allowed_actions: [],
        deferred_idea: {operation_id: PARENT, status: 'succeeded', thread_id: THREAD}}];
      if (state.holdReply) await state.holdReply;
      if (state.loseReply) {state.loseReply = false; return {status: 503, data: {error: {code: 'NETWORK_ERROR', message: 'reply lost'}}};}
      return {status: 200, data: {operation: state.saved}};
    }
    if (input.method === 'GET' && path === `/operations/${PARENT}`) return {status: 200, data: {operation: state.saved}};
    const result = await dispatch(input);
    if (path === '/capabilities' || path === '/bootstrap') {
      const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.actions.push({action: ACTION, allowed: state.allowed});
      caps.features.pm_workspaces.work_input_idea = {version: 1, available: true};
    }
    return result;
  };
  return {f, state, row};
}

for (const lang of ['en', 'zh-TW'] as const) for (const native of [false, true]) {
  test(`${lang} ${native ? 'IPC' : 'HTTP'}: retain original input through one durable operation`, async ({page}) => {
    const {f, state} = fixture(lang, native), text = copy[lang];
    await page.setViewportSize({width: lang === 'en' ? 1440 : 390, height: 900});
    await openWork(page, f, lang);
    const shot = process.env.IDEA_SCREENSHOT_DIR;
    if (shot && !native) {mkdirSync(shot, {recursive: true}); await page.screenshot({path: `${shot}/${lang}-before.png`, fullPage: true});}
    await page.getByRole('button', {name: text.open, exact: true}).click();
    const reason = page.getByLabel(text.reason, {exact: true});
    await reason.fill('保留原方向，明天再討論。');
    await reason.evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(3, 6));
    const before = f.follow.reads.length;
    f.pushEvent();
    await expect.poll(() => f.follow.reads.length).toBeGreaterThan(before);
    await expect(reason).toBeFocused();
    await expect(reason).toHaveValue('保留原方向，明天再討論。');
    expect(await reason.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([3, 6]);
    if (shot && !native) {
      await page.getByRole('button', {name: text.save, exact: true}).scrollIntoViewIfNeeded();
      await page.screenshot({path: `${shot}/${lang}-draft.png`});
    }
    // No new fixture event can start after this page has been acknowledged.
    await expect.poll(() => f.follow.reads.some(path => /^GET \/events\?.*after=1(?:&|$)/.test(path))).toBe(true);
    await page.getByRole('button', {name: text.save, exact: true}).click();
    await expect(page.locator('[data-work-idea-result]').getByRole('link', {name: text.draft})).toHaveAttribute('href', `#/pm/${WS}/discussions/${THREAD}`);
    expect(state.posts).toHaveLength(1);
    expect(state.posts[0].body).toEqual({action: ACTION,
      target: {workspace_id: WS, work_item_id: WI, source_operation_id: SOURCE},
      params: {reason: '保留原方向，明天再討論。'}, preconditions: {expected_state: 'queued'}});
    expect(f.follow.posts).toEqual([]);
    expect(f.follow.errors).toEqual([]);
  });
}

test('lost reply retains the same key and reload links the existing Idea without a new request', async ({page}) => {
  const {f, state} = fixture(); state.loseReply = true;
  await openWork(page, f);
  await page.getByRole('button', {name: copy.en.open, exact: true}).click();
  await page.getByLabel(copy.en.reason).fill('Keep for tomorrow');
  await page.getByRole('button', {name: copy.en.save}).click();
  await page.getByRole('button', {name: copy.en.check, exact: true}).click();
  await expect(page.locator('[data-work-idea-result] a')).toHaveAttribute('href', `#/pm/${WS}/discussions/${THREAD}`);
  expect(state.posts).toHaveLength(2);
  expect(state.posts[1].idempotency_key).toBe(state.posts[0].idempotency_key);
  expect(state.posts[1].body).toEqual(state.posts[0].body);
  await page.reload();
  await expect(page.locator(`[data-follow-up-receipt="${SOURCE}"]`).getByRole('link', {name: copy.en.draft})).toHaveAttribute('href', `#/pm/${WS}/discussions/${THREAD}`);
  expect(state.posts).toHaveLength(2);
});

test('unknown delivery, foreign actor and mismatched source never offer deferral', async ({page}) => {
  const {f, state, row} = fixture();
  f.follow.items = [{...row, state: 'unknown', cancellable: false},
    {...row, operation_id: 'op_' + 'a'.repeat(32), actor: 'another-user'},
    {...row, operation_id: 'op_' + 'b'.repeat(32)}];
  await openWork(page, f);
  await expect(page.locator('[data-follow-up-receipt]')).toHaveCount(3);
  await expect(page.getByRole('button', {name: copy.en.open, exact: true})).toHaveCount(0);
  expect(state.posts).toEqual([]);
});

test('withdrawal removes original text and form, and a late save cannot restore its links', async ({page}) => {
  const {f, state} = fixture('zh-TW');
  await openWork(page, f, 'zh-TW');
  await page.getByRole('button', {name: copy['zh-TW'].open, exact: true}).click();
  await page.getByLabel(copy['zh-TW'].reason).fill('尚未送出的草稿');
  let release!: () => void;
  state.holdReply = new Promise(resolve => {release = resolve;});
  await page.getByRole('button', {name: copy['zh-TW'].save, exact: true}).click();
  await expect.poll(() => state.posts.length).toBe(1);
  f.follow.inputsStatus = 404; f.pushEvent();
  await expect(page.locator('[data-follow-up-withdrawn]')).toBeVisible();
  await expect(page.getByText('明天再整理剩餘的驗收證據。', {exact: true})).toHaveCount(0);
  await expect(page.getByLabel(copy['zh-TW'].reason)).toHaveCount(0);
  release();
  await expect(page.locator('[data-work-idea-result]')).toHaveCount(0);
  await expect(page.getByRole('link', {name: copy['zh-TW'].draft})).toHaveCount(0);
  expect(state.posts).toHaveLength(1);
});

test('one retained reason stays readable at 1440, 768 and 390 pixels', async ({page}) => {
  const {f} = fixture('zh-TW');
  await openWork(page, f, 'zh-TW');
  await page.getByRole('button', {name: copy['zh-TW'].open, exact: true}).click();
  const reason = page.getByLabel(copy['zh-TW'].reason), save = page.getByRole('button', {name: copy['zh-TW'].save, exact: true});
  await reason.fill('這份想法先留到明天。');
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({width, height: width === 390 ? 844 : 900});
    await save.scrollIntoViewIfNeeded();
    await expect(reason).toHaveValue('這份想法先留到明天。');
    await expect(save).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    if (width === 390) expect((await save.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    const shot = process.env.IDEA_SCREENSHOT_DIR;
    if (shot) {mkdirSync(shot, {recursive: true}); await page.screenshot({path: `${shot}/zh-TW-${width}.png`});}
  }
});
