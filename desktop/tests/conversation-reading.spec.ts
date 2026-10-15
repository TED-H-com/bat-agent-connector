import {createHash} from 'node:crypto';
import {test, expect} from '@playwright/test';
import {conversationFixture, mountConversation} from './conversation-fixture.ts';

function fixture() {
  const base = conversationFixture(), receipts = new Set<string>();
  const data = Object.assign(base.data, {
    failReceipt: false, position: null as any,
    complete: true,
    incomplete_reasons: undefined as string[] | undefined,
    unindexed_count: undefined as number | undefined,
  });
  data.messages = Array.from({length: 60}, (_, i) => ({id: `message-${i}`, role: 'assistant',
    ts: '2026-10-10T12:00:00Z', text: `Reading note ${i}\n${'Visible content is a personal reading choice. '.repeat(8)}`}));
  const revision = (message: any) => createHash('sha256').update(message.text).digest('hex');
  const key = (message: any) => message.id + ':' + revision(message);
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    if (input.method !== 'GET') {
      data.writes.push(input);
      if (data.failReceipt) return {status: 500, data: {error: {code: 'RECEIPT_FAILURE', message: 'Private receipt diagnostic'}}};
      if (input.body.action === 'session.read') {
        for (const message of input.body.params.messages) receipts.add(message.message_id + ':' + message.revision);
      } else if (input.body.action === 'session.position') {
        data.position = {...input.body.params, version: (data.position?.version || 0) + 1};
      } else throw Error('Unexpected write');
      data.cursor++;
      return {status: 200, data: {operation: {operation_id: 'op_' + String(data.writes.length).padStart(32, '0'), status: 'succeeded'}}};
    }
    if (path.endsWith('/messages')) {
      const offset = Number(url.searchParams.get('offset') || 0), end = data.messages.length - offset;
      return {status: 200, data: {messages: data.messages.slice(Math.max(0, end - 30), end)
        .map(message => ({...message, reading: {revision: revision(message), unread: !receipts.has(key(message)), can_mark: true}})),
        offset, next_offset: end > 30 ? offset + 30 : null,
        reading: {unread_count: data.messages.filter(message => !receipts.has(key(message))).length,
          known_count: data.messages.length, complete: data.complete, observed_at: 1,
          ...(data.incomplete_reasons !== undefined ? {incomplete_reasons: data.incomplete_reasons} : {}),
          ...(data.unindexed_count !== undefined ? {unindexed_count: data.unindexed_count} : {}),
          position: data.position ? {...data.position, page_offset: data.messages.length - 1 - data.messages.findIndex(message => message.id === data.position.message_id)} : null}}};
    }
    const result = await base.dispatch(input);
    if (path === '/capabilities') result.data = {...result.data, features: {session_reading: {version: 1}}};
    if (path === '/bootstrap') result.data.capabilities = {...result.data.capabilities, features: {session_reading: {version: 1}}};
    return result;
  };
  return {data, dispatch};
}

for (const native of [false, true]) {
  const platform = native ? 'IPC' : 'Web';
  test(`explicit visible receipts preserve skipped history and changed revisions (${platform})`, async ({page}) => {
    const {data, dispatch} = fixture();
    await mountConversation(page, native, dispatch); await page.goto('/dashboard/#/session/demo/session-1');
    await expect(page.locator('[data-conversation-reading]')).toHaveText('Last observed: 60 unread');
    expect(data.writes).toEqual([]);
    const mark = page.getByRole('button', {name: 'Mark read'});
    await mark.scrollIntoViewIfNeeded();
    await expect(mark).toBeEnabled(); await mark.click();
    await expect.poll(() => data.writes.filter(write => write.body.action === 'session.read').length).toBe(1);
    const marked = data.writes.find(write => write.body.action === 'session.read').body.params.messages;
    expect(marked.length).toBeGreaterThan(0); expect(marked.length).toBeLessThan(30);
    expect(marked.some((message: any) => message.message_id === 'message-0')).toBe(false);
    await expect(page.locator('[data-conversation-reading]')).toHaveText(`Last observed: ${60 - marked.length} unread`);
    await page.locator('.conversation-scroll').evaluate(el => el.scrollTop = 0);
    data.messages.at(-1)!.text += ' A new version.'; data.cursor++;
    await expect(page.locator('[data-conversation-reading]')).toHaveText(`Last observed: ${61 - marked.length} unread`);
    expect(data.writes).toHaveLength(1);
    const remaining = 61 - marked.length;
    data.complete = false; data.incomplete_reasons = ['message_ids']; data.unindexed_count = 2; data.cursor++;
    await expect(page.locator('[data-conversation-reading]')).toHaveText(`${remaining}+ unread`);
    await expect(page.locator('[data-session-reading-detail]')).toHaveText('Some messages have no ID and are not counted.');
    data.incomplete_reasons = ['archive_budget']; data.unindexed_count = undefined; data.cursor++;
    await expect(page.locator('[data-conversation-reading]')).toHaveText(`${remaining}+ unread · Showing the latest 3,000 messages`);
    data.incomplete_reasons = ['archive_unconfirmed']; data.cursor++;
    await expect(page.locator('[data-conversation-reading]')).toHaveText(`${remaining}+ unread · Earlier messages may not be loaded`);
    await expect(page.locator('[data-session-reading-detail]')).toHaveText('BAT did not confirm the end of the history.');
    data.incomplete_reasons = undefined; data.cursor++;
    await expect(page.locator('[data-conversation-reading]')).toHaveText(`${remaining}+ unread · earlier messages may not be loaded`);
    await expect(page.locator('[data-conversation-reading]')).not.toContainText('Partial history');
    expect(data.writes).toHaveLength(1);
  });

  test(`saved position restores across session entry and history pages without marking read (${platform})`, async ({page}) => {
    const {data, dispatch} = fixture();
    await mountConversation(page, native, dispatch); await page.goto('/dashboard/#/session/demo/session-1');
    await page.getByRole('button', {name: 'Load earlier'}).click();
    await expect(page.locator('[data-message-id="message-0"]')).toHaveCount(1);
    await page.locator('.conversation-scroll').evaluate(el => el.scrollTop = 400);
    const remember = page.getByRole('button', {name: 'Save position'});
    await remember.scrollIntoViewIfNeeded(); await expect(remember).toBeEnabled(); await remember.click();
    await expect.poll(() => data.position?.message_id).toBeTruthy();
    const saved = {...data.position};
    await expect(page.locator('[data-conversation-reading]')).toHaveText('Last observed: 60 unread');
    await page.goto('/dashboard/#/sessions'); await page.goto('/dashboard/#/session/demo/session-1');
    await expect(page.getByText('Restored the reading position saved by this identity.')).toBeVisible();
    await expect(page.locator(`[data-message-id="${saved.message_id}"]`)).toBeVisible();
    const offset = await page.locator(`[data-message-id="${saved.message_id}"]`).evaluate(el =>
      el.getBoundingClientRect().top - el.closest('.conversation-scroll')!.getBoundingClientRect().top);
    expect(Math.abs(offset - saved.offset)).toBeLessThan(2);
    await page.getByRole('button', {name: 'Back to latest', exact: true}).click();
    await expect(page.locator('[data-message-id="message-59"]')).toBeVisible();
    expect(data.writes.every(write => write.body.action === 'session.position')).toBe(true);
  });
}

test('browser and desktop share receipts and position while retaining their own drafts', async ({page, browser}) => {
  const {data, dispatch} = fixture(), desktop = await browser.newPage();
  try {
    await mountConversation(page, false, dispatch); await mountConversation(desktop, true, dispatch);
    for (const client of [page, desktop]) {
      await client.goto('/dashboard/#/session/demo/session-1');
      await expect(client.locator('[data-conversation-reading]')).toHaveText('Last observed: 60 unread');
    }
    await page.locator('textarea').first().fill('Browser draft'); await desktop.locator('textarea').first().fill('Desktop draft');
    const mark = page.getByRole('button', {name: 'Mark read'});
    await mark.scrollIntoViewIfNeeded(); await mark.click();
    await expect.poll(() => data.writes.length).toBe(1);
    const count = 60 - data.writes[0].body.params.messages.length;
    for (const client of [page, desktop]) await expect(client.locator('[data-conversation-reading]')).toHaveText(`Last observed: ${count} unread`);
    await expect(page.locator('textarea').first()).toHaveValue('Browser draft');
    await expect(desktop.locator('textarea').first()).toHaveValue('Desktop draft');
  } finally {await desktop.close();}
});

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) {
  test(`reading controls explain inapplicability without moving and retain diagnostics ${locale} ${native ? 'IPC' : 'Web'}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width: 390, height: 844}}), page = await context.newPage();
    const {data, dispatch} = fixture(), zh = locale === 'zh-TW';
    await mountConversation(page, native, dispatch); await page.goto('/dashboard/#/session/demo/session-1');
    const mark = page.getByRole('button', {name: zh ? '標為已讀' : 'Mark read', exact: true});
    const remember = page.getByRole('button', {name: zh ? '儲存位置' : 'Save position', exact: true});
    await mark.scrollIntoViewIfNeeded(); await expect(mark).toBeEnabled();
    const before = await mark.boundingBox();
    await page.locator('.conversation-scroll').evaluate(node => node.scrollTop = 0);
    await expect.poll(async () => (await mark.boundingBox())!.y).toBe(before!.y);
    await page.locator('.conversation-scroll').scrollIntoViewIfNeeded();
    // Moving the page past the transcript makes neither action applicable.
    await page.evaluate(() => window.scrollTo(0, 0));
    data.messages = []; data.cursor++;
    await expect(mark).toBeDisabled();
    await expect(mark).toHaveAccessibleDescription(zh ? '畫面中沒有可標為已讀的訊息' : 'No unread messages in view');
    await expect(remember).toBeDisabled();
    await expect(remember).toHaveAccessibleDescription(zh ? '畫面中沒有可儲存位置的訊息' : 'No message position in view');
    expect((await mark.boundingBox())!.height).toBe(before!.height);
    expect(data.writes).toEqual([]);
    data.messages = [{id: 'last', role: 'assistant', ts: '2026-10-10T12:00:00Z', text: 'One readable message'}]; data.cursor++;
    await mark.scrollIntoViewIfNeeded(); await expect(mark).toBeEnabled();
    data.failReceipt = true; await mark.click();
    const failure = page.locator('.conversation-toolbar [data-api-error]');
    await expect(failure).toContainText(zh ? '無法儲存閱讀狀態' : 'Reading state unavailable');
    expect(await failure.innerText()).not.toContain('Private receipt diagnostic');
    await failure.locator('details > summary').click();
    await expect(failure).toContainText('Private receipt diagnostic');
    await expect(failure.locator('[data-error-details] code')).toHaveText('RECEIPT_FAILURE');
    expect(data.writes).toHaveLength(1); await context.close();
  });
}
