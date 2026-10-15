import {test, expect} from '@playwright/test';
import {conversationFixture, mountConversation} from './conversation-fixture.ts';
import {wt} from './observed-resource-fixture.ts';
import {expectNoNestedErrorDetails} from './api-error-assertions.ts';

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) {
  const zh = locale === 'zh-TW';
  test(`empty sessions distinguish a first run, filters and partial reads ${locale} ${native ? 'IPC' : 'Web'}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale}), page = await context.newPage(), f = conversationFixture();
    let more = false;
    await mountConversation(page, native, async input => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (path === '/hosts') return {status: 200, data: {hosts: [{host: 'demo'}]}};
      if (path === '/sessions') return {status: 200, data: {sessions: [], next_cursor: more ? 'more' : null}};
      return f.dispatch(input);
    });
    await page.goto('/dashboard/#/sessions');
    const first = page.getByRole('heading', {name: zh ? '尚無工作階段' : 'No sessions yet', exact: true});
    const noMatch = page.getByRole('heading', {name: zh ? '目前範圍沒有符合的工作階段' : 'No matching sessions in this view', exact: true});
    const count = page.locator('.session-results > p'), pageNote = page.locator('.session-pagination > span');
    await expect(first).toBeVisible(); await expect(count).toBeHidden(); await expect(pageNote).toBeHidden();
    await expect(page.locator('[data-session-paths]')).toBeVisible();
    const search = page.locator('#main').getByRole('searchbox');
    await search.fill('missing'); await expect(first).toHaveCount(0); await expect(noMatch).toBeVisible();
    await expect(page.locator('[data-session-paths]')).toBeHidden();
    await expect(page.locator('.session-inventory')).toContainText(zh ? '未出現在這裡不代表工作已結束' : 'Absence from this view does not mean work has ended');
    await search.fill(''); await expect(first).toBeVisible();
    await page.getByRole('combobox', {name: zh ? '主機' : 'Host', exact: true}).selectOption('demo');
    await expect(noMatch).toBeVisible(); await expect(first).toHaveCount(0);
    await page.getByRole('combobox', {name: zh ? '主機' : 'Host', exact: true}).selectOption('');
    await expect(first).toBeVisible(); more = true; await page.reload();
    await expect(first).toHaveCount(0); await expect(noMatch).toBeVisible();
    await expect(count).toBeVisible(); await expect(pageNote).toBeVisible();
    await expect(page.locator('#main').getByRole('button', {name: zh ? '載入更多' : 'Load more'})).toBeVisible();
    expect(f.data.writes).toEqual([]); await context.close();
  });

  test(`resource activity agrees with sessions and suppresses stale observations ${locale} ${native ? 'IPC' : 'Web'}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale}), page = await context.newPage(), f = conversationFixture();
    const rows = [
      {session_id: 'asking', pending: {kind: 'ask_user'}}, {session_id: 'permission', pending: {kind: 'permission'}},
      {session_id: 'running', streaming: true}, {session_id: 'failed', status: 'failed'},
      {session_id: 'quiet', streaming: false}, {session_id: 'unknown'},
      {session_id: 'stale', streaming: true, stale: true}, {session_id: 'gone', streaming: true, gone_at: 1},
      {session_id: 'ended', streaming: true, state: {lifecycle: 'ended'}},
      {session_id: 'stale-pending', pending: {kind: 'ask_user'}, fields_stale: true},
    ].map(row => ({host: 'demo', title: row.session_id, api_access: 'managed', provenance: 'connector_managed', ...row}));
    await mountConversation(page, native, async input => {
      const path = new URL(input.path, 'http://fixture').pathname;
      if (path === '/hosts') return {status: 200, data: {hosts: [{host: 'demo'}]}};
      if (path === '/projects') return {status: 200, data: {projects: []}};
      if (path === '/sessions') return {status: 200, data: {sessions: rows, next_cursor: null}};
      if (path === '/worktrees/' + wt) return {status: 200, data: {worktree: {resource_id: wt, host: 'demo', work: [],
        known_sessions: rows.map(row => ({host: row.host, session_id: row.session_id, observation: row}))}}};
      return f.dispatch(input);
    });
    await page.goto('/dashboard/#/sessions');
    const labels = [zh ? '等待處理' : 'Waiting for you', zh ? '等待處理' : 'Waiting for you', zh ? '執行中' : 'Running', zh ? '失敗' : 'failed'];
    for (const [i, label] of labels.entries()) await expect(page.locator(`[data-resource-id="demo/${rows[i].session_id}"] .session-entry-heading .chip`)).toHaveText(label);
    for (const id of ['quiet', 'unknown']) await expect(page.locator(`[data-resource-id="demo/${id}"] .session-entry-heading .chip`)).toHaveCount(0);
    await page.goto('/dashboard/#/worktree/' + wt);
    const sessions = page.locator('[data-observed-session]'); await expect(sessions).toHaveCount(rows.length);
    for (const [i, label] of labels.entries()) await expect(sessions.nth(i).locator('.ui-pill')).toHaveText(label);
    await expect(sessions.nth(0).locator('.ui-pill')).toHaveClass(/tone-warn/);
    await expect(sessions.nth(2).locator('.ui-pill')).toHaveClass(/tone-info/);
    await expect(sessions.nth(3).locator('.ui-pill')).toHaveClass(/tone-bad/);
    for (let i = 4; i < rows.length; i++) await expect(sessions.nth(i).locator('.ui-pill')).toHaveCount(0);
    expect(f.data.writes).toEqual([]); await context.close();
  });

  test(`message failure has one recovery and preserves diagnostics ${locale} ${native ? 'IPC' : 'Web'}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width: 390, height: 844}}), page = await context.newPage(), f = conversationFixture();
    f.data.fail = true;
    await mountConversation(page, native, f.dispatch); await page.goto('/dashboard/#/session/demo/session-1');
    const failure = page.locator('[data-session-error]'); await expect(failure).toHaveCount(1);
    await expect(failure.locator('.session-error-line')).toContainText(zh ? '無法載入訊息' : 'Messages unavailable');
    await expect(page.locator('#main').getByRole('button', {name: zh ? '重試' : 'Retry', exact: true})).toHaveCount(1);
    await expect(page.locator('.conversation-caption')).toHaveText('');
    await failure.locator('details > summary').click();
    await expect(failure).toContainText('UNAVAILABLE'); await expect(failure).toContainText('Messages unavailable');
    await expect(failure.locator('[data-export-diagnostics]')).toBeVisible();
    await expectNoNestedErrorDetails(failure);
    f.data.fail = false;
    await failure.getByRole('button', {name: zh ? '重試' : 'Retry', exact: true}).click();
    await expect(page.locator('.msg')).toHaveCount(12); await expect(failure).toHaveCount(0);
    expect(f.data.writes).toEqual([]); await context.close();
  });
}
