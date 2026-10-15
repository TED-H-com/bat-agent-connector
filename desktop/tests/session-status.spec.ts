import {mkdirSync} from 'node:fs';
import {test, expect, type Page} from '@playwright/test';
import {conversationFixture, mountConversation} from './conversation-fixture.ts';

function fixture(zh: boolean) {
  const base = conversationFixture();
  const data = Object.assign(base.data, {reasons: ['message_ids'] as string[] | undefined,
    complete: false, unread: 34 as number | null});
  const rows = [
    {session_id: 'session-1', title: zh ? '先前的架構筆記' : 'Past architecture notes', loaded: false, streaming: null},
    {session_id: 'session-2', title: zh ? '檢查發行內容' : 'Review release contents', loaded: true, streaming: null},
    {session_id: 'session-3', title: zh ? '等待權限核准' : 'Await permission', loaded: true, streaming: false,
      pending: {kind: 'permission', toolUseId: 'tool-fixture', toolName: 'Bash', input_preview: 'npm test'}},
    {session_id: 'session-4', title: zh ? '執行測試' : 'Run tests', loaded: true, streaming: true},
    {session_id: 'session-5', title: zh ? '舊分支筆記' : 'Old branch notes', loaded: false, streaming: null,
      stale: true, stale_reason: 'host_unreachable'},
  ].map(row => ({host: 'demo', workspace: zh ? '設計筆記' : 'Design notes', workspace_id: 'workspace-notes',
    agent_kind: 'claude', provenance: 'manual', api_access: 'read_only',
    state: {lifecycle: 'unknown', enumeration: 'present'}, observed_at: '2026-10-12T09:00:00Z', ...row}));
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (input.method !== 'GET') return base.dispatch(input);
    if (path === '/hosts') return {status: 200, data: {hosts: [{host: 'demo'}]}};
    // A known, empty project catalog: the rail opens its session tree (#156 keeps it closed when the catalog is unknown).
    if (path === '/projects') return {status: 200, data: {projects: []}};
    if (path === '/sessions') return {status: 200, data: {sessions: rows, next_cursor: null}};
    const session = rows.find(row => path === `/sessions/demo/${row.session_id}`);
    if (session) return {status: 200, data: {session}};
    if (path.endsWith('/messages')) return {status: 200, data: {
      messages: [{id: 'note-1', role: 'assistant', ts: '2026-10-12T08:50:00Z',
        text: zh ? '工作階段只在需要處理或執行中時顯示狀態。' : 'Sessions show a status when they need attention or are running.'}],
      next_offset: null, reading: {unread_count: data.unread, complete: data.complete,
        ...(data.reasons !== undefined ? {incomplete_reasons: data.reasons} : {}),
        ...(data.reasons?.includes('message_ids') ? {unindexed_count: 2} : {})},
    }};
    const result = await base.dispatch(input);
    const features = {session_reading: {version: 1}};
    if (path === '/capabilities') result.data = {...result.data, features};
    if (path === '/bootstrap') result.data.capabilities = {...result.data.capabilities, features};
    return result;
  };
  return {data, dispatch, rows};
}

async function capture(page: Page, enabled: boolean, name: string) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  if (!enabled) return;
  const shots = `${process.env.TMPDIR || '/tmp'}/shots`;
  mkdirSync(shots, {recursive: true});
  await page.screenshot({path: `${shots}/fix1-after-${name}.png`, animations: 'disabled'});
}

for (const native of [false, true]) for (const locale of ['en', 'zh-TW'] as const) for (const width of [1440, 390]) {
  test(`quiet sessions retain unread bounds and localized details (${native ? 'IPC' : 'Web'} ${locale} ${width})`, async ({browser, baseURL}) => {
    // This matrix includes nine screenshots and repeated event-driven refreshes.
    test.setTimeout(60000);
    const zh = locale === 'zh-TW', context = await browser.newContext({baseURL,
      locale: zh ? 'zh-TW' : 'en-US', timezoneId: 'UTC', viewport: {width, height: width === 390 ? 844 : 900}});
    try {
      const page = await context.newPage(), f = fixture(zh), errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      await mountConversation(page, native, f.dispatch);
      await page.goto('/dashboard/#/sessions');
      const row = (id: string) => page.locator(`[data-resource-id="demo/${id}"]`);
      await expect(page.locator('[data-resource-id]')).toHaveCount(5);
      for (const id of ['session-1', 'session-2']) await expect(row(id).locator('.session-entry-heading .chip')).toHaveCount(0);
      for (const [id, label] of [['session-3', zh ? '等待處理' : 'Waiting for you'],
        ['session-4', zh ? '執行中' : 'Running'], ['session-5', zh ? '未同步' : 'Out of sync']]) {
        await expect(row(id).locator('.session-entry-heading .chip')).toHaveText(label);
      }
      await row('session-1').locator('.session-row-details > summary').click();
      await expect(row('session-1')).toContainText(zh ? 'BAT 中未開啟' : 'Not open in BAT');
      await row('session-1').locator('.session-row-details > summary').click();
      await row('session-2').locator('.session-row-details > summary').click();
      await expect(row('session-2')).toContainText(zh ? 'BAT 未回報活動狀態' : 'BAT did not report activity');
      await row('session-2').locator('.session-row-details > summary').click();
      // Quiet status presentation must not change inventory order or factual search.
      expect(await page.locator('[data-resource-id]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-resource-id'))))
        .toEqual(f.rows.map(row => `demo/${row.session_id}`));
      const search = page.getByRole('searchbox', {name: zh ? '搜尋已載入的工作階段' : 'Search loaded sessions'});
      await search.fill('session-1'); await expect(page.locator('[data-resource-id]')).toHaveCount(1);
      await search.fill(''); await expect(page.locator('[data-resource-id]')).toHaveCount(5);
      for (const summary of await page.locator('.session-row-details[open] > summary').all()) await summary.click();
      await expect(page.locator('.session-row-details[open]')).toHaveCount(0);
      const suffix = `${zh ? 'zh' : 'en'}-${width}`;
      await capture(page, !native, `01-sessions-list-${suffix}`);
      const rail = page.locator('.workspace-session-tree');
      const unloadedLink = rail.locator('a[href="#/session/demo/session-1"]');
      await expect(unloadedLink).toHaveCount(1);
      await expect(unloadedLink.locator('.workspace-tree-state')).toHaveCount(0);
      await expect(rail.locator('a[href="#/session/demo/session-5"]')).toHaveAttribute('aria-label', /未同步|Out of sync/);
      for (const [id, label] of [['session-3', zh ? '等待處理' : 'Waiting for you'],
        ['session-4', zh ? '執行中' : 'Running'], ['session-5', zh ? '未同步' : 'Out of sync']]) {
        await page.goto(`/dashboard/#/session/demo/${id}`);
        await expect(page.locator('.session-pill .ui-pill')).toHaveText(label);
      }
      await page.goto('/dashboard/#/session/demo/session-1');
      await expect(page.locator('.session-pill .ui-pill')).toHaveCount(0);
      await expect(page.locator('[data-session-readonly]')).toBeVisible();
      await expect(page.getByRole('button', {name: zh ? '送出' : 'Send', exact: true})).toHaveCount(0);
      const progress = page.locator('[data-conversation-reading]'), details = page.locator('[data-session-details]');
      const idDetail = zh ? '部分訊息沒有 ID，未計入未讀數。' : 'Some messages have no ID and are not counted.';
      const archiveDetail = zh ? 'BAT 未確認歷史紀錄是否已到結尾。' : 'BAT did not confirm the end of the history.';
      const lower = zh ? '34+ 則未讀' : '34+ unread';
      for (const [name, reasons, line, detail] of [
        ['02-unloaded-session-message-ids', ['message_ids'], lower, idDetail],
        ['03-history-archive-budget', ['archive_budget'], `${lower} · ${zh ? '顯示最近 3,000 則訊息' : 'Showing the latest 3,000 messages'}`, ''],
        ['04-history-archive-unconfirmed', ['archive_unconfirmed'], `${lower} · ${zh ? '較早的訊息可能未載入' : 'Earlier messages may not be loaded'}`, archiveDetail],
        ['05-history-legacy', undefined, `${lower} · ${zh ? '較早的訊息可能未載入' : 'earlier messages may not be loaded'}`, ''],
      ] as const) {
        f.data.reasons = reasons ? [...reasons] : undefined; f.data.cursor++;
        await expect(progress).toHaveText(line);
        await expect(page.locator('[data-session-reading-detail]')).toHaveText(detail);
        if (!detail) await expect(page.locator('[data-session-reading-detail]')).toHaveAttribute('hidden', '');
        await progress.scrollIntoViewIfNeeded();
        await capture(page, !native, `${name}-conversation-${suffix}`);
        if (width === 390) await page.getByRole('button', {name: zh ? '成果' : 'Results', exact: true}).click();
        if (await details.getAttribute('open') !== '') await details.locator(':scope > summary').click();
        await expect(details).toContainText(zh ? 'BAT 中未開啟' : 'Not open in BAT');
        await expect(details.locator('dt').filter({hasText: zh ? /^工作階段 ID$/ : /^Session ID$/})).toHaveCount(1);
        await expect(details.locator('dt').filter({hasText: zh ? /^工作區$/ : /^Workspace$/})).toHaveCount(1);
        await expect(details.locator('dt').filter({hasText: zh ? /^標題$/ : /^Title$/})).toHaveCount(0);
        await expect(details.locator('time + code')).toHaveCount(0);
        if (detail) await expect(page.locator('[data-session-reading-detail]')).toHaveText(detail);
        else await expect(page.locator('[data-session-reading-detail]')).toBeHidden();
        await details.scrollIntoViewIfNeeded();
        await capture(page, !native, `${name}-${suffix}`);
        if (width === 390) await page.keyboard.press('Escape');
      }
      f.data.reasons = ['archive_unconfirmed', 'message_ids']; f.data.cursor++;
      await expect(page.locator('[data-session-reading-detail]')).toHaveText(`${archiveDetail} ${idDetail}`);
      f.data.complete = true; f.data.reasons = []; f.data.cursor++;
      await expect(progress).toHaveText(zh ? '上次觀察：34 則未讀' : 'Last observed: 34 unread');
      f.data.unread = null; f.data.cursor++;
      await expect(progress).toHaveText(zh ? '尚未讀取對話紀錄' : 'Conversation has not been observed');
      await expect(page.locator('#main')).not.toContainText(/活動狀態未確認|部分紀錄|undefined|null/);
      expect(f.data.writes).toEqual([]); expect(errors).toEqual([]);
    } finally {await context.close();}
  });
}
