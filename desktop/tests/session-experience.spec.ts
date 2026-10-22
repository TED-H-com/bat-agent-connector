import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';
import {fromRequirements, openSession, type Override} from './session-fixture.ts';

async function shot(page: Page, name: string) {
  const testInfo = test.info();

  await page.screenshot({path: testInfo.outputPath(`${name}.png`), animations: 'disabled'});
}

// Hash assignment stays in the same document, so the session can remember the PM view.
async function go(page: Page, hash: string) {
  await page.evaluate(next => {location.hash = next;}, hash);
}

function noOverflow() {
  return document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1;
}

for (const locale of ['en', 'zh-TW'] as const) for (const timezoneId of ['UTC', 'America/New_York', 'Asia/Taipei']) {
  test(`session observation time is local ${locale} ${timezoneId}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, timezoneId});
    const page = await context.newPage(), fixture = demoFixture({locale}), writes: unknown[] = [];
    await page.clock.install({time: new Date('2026-10-14T09:00:00Z')});
    await mountConversation(page, false, async input => {
      if (input.method !== 'GET') writes.push(input);
      return fixture.dispatch(input);
    });
    await page.goto(`/dashboard/#/session/${fixture.ids.host}/${fixture.ids.sessions.implement}`);
    const clocks = locale === 'en'
      ? {'UTC': '9:00 AM', 'America/New_York': '5:00 AM', 'Asia/Taipei': '5:00 PM'}
      : {'UTC': '上午9:00', 'America/New_York': '上午5:00', 'Asia/Taipei': '下午5:00'};
    await expect(page.locator('.workspace-session-meta time')).toHaveText(
      (locale === 'en' ? 'Oct 12, ' : '10月12日 ') + clocks[timezoneId]);
    expect(writes).toHaveLength(0);
    await context.close();
  });
}

for (const locale of ['en', 'zh-TW'] as const) {
  test(`session workspace ${locale}`, async ({browser, baseURL}) => {
    test.setTimeout(90_000);
    const context = await browser.newContext({
      locale: locale === 'zh-TW' ? 'zh-TW' : 'en-US', baseURL, viewport: {width: 1440, height: 900},
      timezoneId: 'America/New_York',
    });
    const page = await context.newPage();
    const fixture = demoFixture({locale});
    const {copy, ids} = fixture;
    const writes: Array<{body?: {action?: string, params?: Record<string, unknown>}}> = [];
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
    await mountConversation(page, false, async input => {
      if (input.method === 'POST') writes.push(input);
      return fixture.dispatch(input);
    });
    const zh = locale === 'zh-TW';
    const send = zh ? '送出' : 'Send';
    const allow = zh ? '允許' : 'Allow';
    const newest = zh ? '新訊息' : 'New messages';
    const more = zh ? '更多操作' : 'More actions';
    const discuss = zh ? '討論' : 'Discuss';
    const results = zh ? '成果' : 'Results';
    const back = zh ? '返回需求' : 'Back to Requirements';
    await page.setViewportSize({width: 1440, height: 900});
    await page.goto(`/dashboard/#/pm/${ids.workspaceId}/requirements`);
    await expect(page.locator('[data-pm-view=requirements]')).toHaveAttribute('aria-current', 'page');
    await go(page, `#/session/${ids.host}/${ids.sessions.implement}`);
    await expect(page.locator('h1')).toContainText(copy.sessionImplement);
    const meta = page.locator('.workspace-session-meta');
    await expect(meta).toHaveText(zh
      ? '會員登入與忘記密碼 · demo-host · 10月12日 上午5:00'
      : 'Member sign-in & password reset · demo-host · Oct 12, 5:00 AM');
    expect(await meta.innerText()).not.toMatch(/:\s*demo-host/);
    expect(await meta.innerText()).not.toMatch(/\d:\d{2}:\d{2}/);
    await expect(page.locator('.session-checkpoint-state')).toHaveText(zh ? '此主機尚未設定受管工作' : 'This host is not set up for managed work');
    await expect(page.getByRole('button', {name: zh ? '記下這個檢查點' : 'Record this version'})).toHaveCount(0);
    await expect(page.locator('[data-session-sources]')).toHaveText(zh ? '尚無成果來源' : 'No result sources');
    await expect(page.locator('.ui-inspector-body')).not.toContainText(zh ? '請在 BAT 中搜尋' : 'Search in BAT');
    await expect(page.locator('.workspace-composer')).not.toContainText(zh ? '選字時不送出' : 'composition never sends');
    await expect(page.locator('[data-session-back=requirements]')).toHaveText(back);
    await expect(page.locator('.shell-crumb-view')).toHaveText(zh ? ['工作', '工作階段'] : ['Work', 'Session']);
    await expect(page.locator('.session-pill')).toContainText(zh ? '執行中' : 'Running');
    await page.locator('[data-session-details] > summary').click();
    const detailsText = await page.locator('[data-session-details]').innerText();
    expect(detailsText).toContain('Codex · example-codex');
    expect(detailsText).not.toMatch(/(^|\n)codex ·/);
    const details = page.locator('[data-session-details]');
    await expect(details.locator('dt').filter({hasText: zh ? /^工作階段 ID$/ : /^Session ID$/})).toHaveCount(1);
    await expect(details.locator('dt').filter({hasText: zh ? /^工作區$/ : /^Workspace$/})).toHaveCount(1);
    await expect(details.locator('dt').filter({hasText: zh ? /^標題$/ : /^Title$/})).toHaveCount(0);
    await expect(page.locator('[data-session-details] time + code')).toHaveCount(0);
    await page.locator('[data-session-details] > summary').click();
    await expect(page.locator('.session-work-line')).toHaveText(zh ? '工作 · 電子郵件登入' : 'Work · Email sign-in');
    await expect(page.locator('.session-label-state')).toHaveText(zh ? '無法讀取標籤' : 'Labels unavailable');
    await expect(page.locator('[data-session-labels] summary')).not.toContainText(zh ? '編輯標籤' : 'Edit labels');
    await expect(page.locator('[data-workspace-result]')).toContainText('PR #87');
    await expect(page.locator('[data-workspace-result]')).toContainText(zh ? '已合併' : 'Merged');
    await expect(page.locator('[data-workspace-result]')).toContainText('PR #91');
    await expect(page.locator('[data-workspace-result]')).toContainText(zh ? '已部署' : 'Deployed');
    await expect(page.locator('[data-workspace-result]')).not.toContainText('CI');
    await page.getByRole('button', {name: more, exact: true}).click();
    const href = await page.getByRole('menuitem', {name: discuss, exact: true}).getAttribute('href');
    const parsed = parseDiscussRefs(String(href).slice(String(href).indexOf('?') + 1));
    expect(parsed.refs).toEqual([{kind: 'work_item', id: ids.work.email}]);
    expect(parsed.title).toBe(copy.sessionImplement);
    expect(parsed.from).toBe(`#/session/${ids.host}/${ids.sessions.implement}?from=requirements`);
    await page.keyboard.press('Escape');
    const scroller = page.locator('.conversation-scroll');
    await expect(scroller.locator('.msg').first()).toBeVisible();
    await scroller.evaluate(el => {el.scrollTop = 0;});
    const before = await scroller.evaluate(el => el.scrollTop);
    fixture.appendSession(ids.sessions.implement, 'A later note arrived while you were reading the earlier notes.');
    await expect(scroller).toContainText('A later note arrived while you were reading the earlier notes.', {timeout: 10000});
    await expect(page.getByRole('button', {name: newest, exact: true})).toBeVisible();
    expect(await scroller.evaluate(el => el.scrollTop)).toBe(before);
    await page.getByRole('button', {name: newest, exact: true}).click();
    await expect.poll(() => scroller.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThanOrEqual(1);
    const box = page.locator('#workspace-message-input textarea');
    await expect(page.getByRole('button', {name: send, exact: true})).toBeEnabled();
    await box.fill('Ship the sign-in handler.');
    await box.focus();
    await page.keyboard.press('Control+Enter');
    await expect.poll(() => writes.some(item => item.body?.action === 'session.send')).toBe(true);
    const sent = writes.find(item => item.body?.action === 'session.send');
    expect(sent?.body?.params).toMatchObject({text: 'Ship the sign-in handler.'});
    await expect(page.locator('.conversation-scroll')).toContainText('Ship the sign-in handler.');
    // Missing central completion time stays unstamped; dated receipts are covered below.
    await expect(page.locator('.session-send-line')).toHaveText(zh ? '已送出' : 'Sent');
    await expect(page.locator('.session-send-line')).not.toContainText(/op_/);
    await expect(page.locator('.session-send-record')).not.toHaveAttribute('open');
    await shot(page, `session-${locale}-1440`);
    await page.setViewportSize({width: 768, height: 900});
    await expect(page.locator('.ui-inspector')).toBeHidden();
    await scroller.evaluate(el => {el.scrollTop = el.scrollHeight;});
    expect(await page.evaluate(noOverflow)).toBe(true);
    await shot(page, `session-${locale}-768`);
    await page.setViewportSize({width: 390, height: 844});
    const caption = await page.locator('.workspace-session-heading .ui-title-row').boundingBox();
    let iconWidth = 0;
    for (const action of await page.locator('.workspace-session-heading .ui-header-actions .ui-icon-button:visible').all()) {
      const bounds = (await action.boundingBox())!; iconWidth += bounds.width;
      expect(bounds.y).toBeLessThan(caption!.y + caption!.height);
    }
    expect(caption!.width + iconWidth).toBeGreaterThan(330);
    expect(await page.locator('h1').evaluate(node => node.getBoundingClientRect().height <= 2 * parseFloat(getComputedStyle(node).lineHeight))).toBe(true);
    await scroller.evaluate(el => {el.scrollTop = el.scrollHeight;});
    expect(await page.evaluate(noOverflow)).toBe(true);
    const leadingDot = await meta.evaluate(el => {
      const bad: string[] = [];
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      let node = walker.nextNode();
      while (node) {
        const text = node.textContent || '';
        for (let i = 0; i < text.length; i++) {
          if (text[i] !== '·') continue;
          const range = document.createRange();
          range.setStart(node, i); range.setEnd(node, i + 1);
          const rect = range.getBoundingClientRect();
          if (rect.width && rect.left - el.getBoundingClientRect().left < 8) bad.push(text.slice(Math.max(0, i - 6), i + 4));
        }
        node = walker.nextNode();
      }
      return bad;
    });
    expect(leadingDot).toEqual([]);
    await expect(page.locator('.workspace-info-toggle')).not.toContainText(/Collapse|收合|Expand|展開/);
    await expect(page.locator('.workspace-composer-toggle')).not.toContainText(/Collapse|收合|Write|撰寫/);
    await shot(page, `session-${locale}-390`);
    await page.setViewportSize({width: 1440, height: 900});
    await go(page, `#/session/${ids.host}/${ids.sessions.review}`);
    await expect(page.locator('.session-pill')).toContainText(zh ? '等待處理' : 'Waiting for you');
    await expect(page.locator('.session-asking')).toContainText(copy.approvalAsk);
    const allowButton = page.getByRole('button', {name: allow, exact: true});
    await expect(allowButton).toBeEnabled();
    await allowButton.focus();
    await page.keyboard.press('Enter');
    await expect.poll(() => writes.some(item => item.body?.action === 'session.answer')).toBe(true);
    expect(writes.find(item => item.body?.action === 'session.answer')?.body?.params).toMatchObject({permission: 'allow', tool_use_id: 'toolu_demo_review'});
    await page.getByRole('button', {name: more, exact: true}).click();
    const reviewHref = await page.getByRole('menuitem', {name: discuss, exact: true}).getAttribute('href');
    expect(parseDiscussRefs(String(reviewHref).slice(String(reviewHref).indexOf('?') + 1)).refs).toEqual([]);
    await page.keyboard.press('Escape');
    await go(page, `#/session/${ids.host}/${ids.sessions.manual}`);
    await expect(page.locator('.session-pill .ui-pill')).toHaveCount(0);
    await expect(page.locator('#workspace-message-input')).toBeHidden();
    await expect(page.locator('[data-session-readonly]')).toContainText(zh ? '僅供檢視' : 'View only');
    await expect(page.getByRole('button', {name: send, exact: true})).toHaveCount(0);
    await expect(page.getByRole('button', {name: zh ? '中斷這一輪' : 'Interrupt turn', exact: true})).toHaveCount(0);
    const text = await page.locator('#main, .workspace-nav').evaluateAll(nodes => nodes.map(node => (node as HTMLElement).innerText).join('\n'));
    expect(text).not.toMatch(/\b(?:undefined|null|NaN)\b/);
    expect(await page.evaluate(noOverflow)).toBe(true);
    await page.setViewportSize({width: 768, height: 900});
    await expect(page.locator('.ui-inspector')).toBeHidden();
    expect(await page.evaluate(noOverflow)).toBe(true);
    await page.setViewportSize({width: 900, height: 900});
    await page.getByRole('button', {name: results, exact: true}).click();
    await expect(page.locator('.ui-inspector')).toHaveAttribute('role', 'dialog');
    await page.keyboard.press('Escape');
    await expect(page.locator('.ui-inspector')).toBeHidden();
    await expect(page.getByRole('button', {name: results, exact: true})).toBeFocused();
    await page.setViewportSize({width: 390, height: 844});
    await page.getByRole('button', {name: results, exact: true}).click();
    await expect(page.getByRole('button', {name: zh ? '返回' : 'Back', exact: true})).toBeVisible();
    expect(await page.evaluate(noOverflow)).toBe(true);
    await shot(page, `session-${locale}-390-sheet`);
    expect(errors.filter(line => !line.includes('browser-session') && !line.startsWith('Failed to load resource:'))).toEqual([]);
    await context.close();
  });
}

test('session workspace dark at 1440', async ({page}) => {
  const fixture = demoFixture({locale: 'en'});
  await page.emulateMedia({colorScheme: 'dark'});
  await mountConversation(page, false, fixture.dispatch);
  await page.setViewportSize({width: 1440, height: 900});
  await page.goto(`/dashboard/#/pm/${fixture.ids.workspaceId}/overview`);
  await expect(page.locator('[data-pm-view=overview]')).toHaveAttribute('aria-current', 'page');
  await go(page, `#/session/${fixture.ids.host}/${fixture.ids.sessions.implement}`);
  await expect(page.locator('h1')).toContainText(fixture.copy.sessionImplement);
  await shot(page, 'session-en-1440-dark');
});

for (const locale of ['en','zh-TW'] as const) test(`successful send ages through the shared ticker ${locale}`, async ({browser,baseURL}) => {
  const context=await browser.newContext({baseURL,locale}), page=await context.newPage(), f=demoFixture({locale});
  const now=new Date('2026-10-14T09:00:00Z'); await page.clock.install({time:now});
  await mountConversation(page,false, async input => {
    const result=await f.dispatch(input);
    if (input.method==='POST' && input.body?.action==='session.send' && result.status===200)
      (result.data as any).operation.updated_at=now.valueOf()/1000;
    return result;
  });
  await page.goto(`/dashboard/#/session/${f.ids.host}/${f.ids.sessions.implement}`);
  const box=page.locator('#workspace-message-input textarea'); await box.fill('Please continue.');
  await page.getByRole('button',{name:locale==='en'?'Send':'送出',exact:true}).click();
  const stamp=page.locator('.session-send-line time'); await expect(stamp).toHaveText(locale==='en'?'just now':'剛剛');
  await expect(stamp).toHaveAttribute('datetime',now.toISOString()); await expect(stamp).toHaveAttribute('title',/.+/);
  await stamp.evaluate(node=>{(window as any).sentStamp=node;}); await box.fill('Preserved next draft'); await box.focus();
  await page.clock.fastForward(120_000); await expect(stamp).toHaveText(locale==='en'?'2 min ago':'2 分鐘前');
  expect(await stamp.evaluate(node=>node===(window as any).sentStamp)).toBe(true);
  await expect(box).toHaveValue('Preserved next draft'); await expect(box).toBeFocused(); await context.close();
});

const askUser = {kind: 'ask_user', toolUseId: 'toolu_layout_q', questions: [
  {header: 'Threshold', question: 'How many failed attempts before lockout?', options: ['3', '5', '10']},
  {header: 'Copy', question: 'Which lockout message should members see?', options: ['Short', 'Detailed']},
  {header: 'Unlock', question: 'Should support be able to unlock accounts?', options: ['Yes', 'No']}]};
const permission = {kind: 'permission', toolUseId: 'toolu_layout_p', toolName: 'Bash',
  input_preview: 'Run the rate-limit tests with npm run test -- --grep "lockout"'};
const withPending = (s: Awaited<ReturnType<typeof openSession>>, pending: unknown): Override => async (input, url) => {
  if (url.pathname !== `/sessions/${s.ids.host}/${s.ids.sessions.review}`) return undefined;
  const base = await s.fixture.dispatch({method: 'GET', path: url.pathname});
  base.data.session.pending = pending; return base;
};

for (const [width, height] of [[1440, 900], [390, 844]] as const) {
  test(`approval and question prompts stay reachable at ${width}`, async ({browser}) => {
    for (const [name, pending] of [['ask_user', askUser], ['permission', permission]] as const) {
      const s = await openSession(browser, {width, height});
      s.overrides.push(withPending(s, pending));
      await fromRequirements(s.page, s.ids, 'review');
      const {page} = s;
      await expect(page.locator('.session-asking')).toBeVisible();
      const send = page.getByRole('button', {name: 'Send', exact: true});
      // No programmatic focus: a pointer user must see every answer control and Send at rest.
      const controls = name === 'ask_user' ? [page.getByRole('button', {name: 'Answer', exact: true})]
        : [page.getByRole('button', {name: 'Allow', exact: true}), page.getByRole('button', {name: 'Deny', exact: true})];
      for (const control of [...controls, send]) await expect(control).toBeInViewport({ratio: 1});
      const geometry = await page.evaluate(() => {
        const scroller = document.querySelector('.conversation-scroll')!.getBoundingClientRect();
        const composer = document.querySelector('.workspace-composer')!.getBoundingClientRect();
        const preview = document.querySelector('.session-asking-preview') as HTMLElement | null;
        return {gap: composer.top - scroller.bottom, scrollerH: scroller.height, mainOverflow: getComputedStyle(document.querySelector('main')!).overflowY,
          preview: preview ? {display: getComputedStyle(preview).display, width: preview.getBoundingClientRect().width} : null,
          overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth};
      });
      expect(geometry.gap).toBeGreaterThanOrEqual(-1);
      expect(geometry.scrollerH).toBeGreaterThanOrEqual(100);
      expect(geometry.mainOverflow).toBe('auto');
      expect(geometry.overflow).toBeLessThanOrEqual(1);
      if (name === 'permission') {
        expect(geometry.preview?.display).toBe('block');
        expect(geometry.preview!.width).toBeGreaterThan(200);
        await expect(page.locator('.session-asking-preview')).toHaveText(permission.input_preview);
      } else {
        // A pointer can scroll to the last question and use the whole answer field.
        await page.locator('.session-asking-content').hover();
        await page.mouse.wheel(0, 1200);
        await expect(page.locator('.session-question input').last()).toBeInViewport({ratio: 1});
        await expect(send).toBeInViewport({ratio: 1});
      }
      await s.context.close();
    }
  });
}

test('Results drawer is 400px at 1000 and the sheet fills the window at 390', async ({browser}) => {
  for (const locale of ['en', 'zh-TW'] as const) {
    const s = await openSession(browser, {locale, width: 1000, height: 900});
    await fromRequirements(s.page, s.ids, 'implement');
    const {page} = s;
    const results = page.getByRole('button', {name: locale === 'zh-TW' ? '成果' : 'Results', exact: true});
    await results.click();
    await expect(page.locator('.ui-inspector')).toBeVisible();
    expect(Math.round((await page.locator('.ui-inspector').boundingBox())!.width)).toBe(400);
    await page.keyboard.press('Escape');
    await page.setViewportSize({width: 390, height: 844});
    await results.click();
    expect(Math.round((await page.locator('.ui-inspector').boundingBox())!.width)).toBe(390);
    await s.context.close();
  }
});

test('the reader stays pinned after their own send and after resizing', async ({browser}) => {
  const s = await openSession(browser);
  await fromRequirements(s.page, s.ids, 'implement');
  const {page} = s;
  const scroller = page.locator('.conversation-scroll');
  const distance = () => scroller.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight);
  await expect(scroller.locator('.msg').first()).toBeVisible();
  await expect.poll(distance).toBeLessThanOrEqual(1);
  await expect(page.getByRole('button', {name: 'Send', exact: true})).toBeInViewport({ratio: 1});
  await page.locator('#workspace-message-input textarea').fill('Please also cover the lockout copy.');
  await page.getByRole('button', {name: 'Send', exact: true}).click();
  await expect(scroller).toContainText('Please also cover the lockout copy.');
  await expect(page.locator('.session-send-line')).toBeVisible();
  await expect.poll(distance).toBeLessThanOrEqual(1);
  await expect(page.locator('.conversation-jump')).toBeHidden();
  for (const [width, height] of [[768, 900], [390, 844]]) {
    await page.setViewportSize({width, height});
    await expect.poll(distance).toBeLessThanOrEqual(1);
    await expect(page.getByRole('button', {name: 'Send', exact: true})).toBeInViewport({ratio: 1});
  }
  await s.context.close();
});

test('the jump control names what it does and returns focus to the transcript', async ({browser}) => {
  const s = await openSession(browser);
  await fromRequirements(s.page, s.ids, 'implement');
  const {page} = s;
  const scroller = page.locator('.conversation-scroll');
  await expect(scroller.locator('.msg').first()).toBeVisible();
  await scroller.evaluate(el => {el.scrollTop = 0;});
  const jump = page.locator('.conversation-jump');
  await expect(jump).toHaveText('Back to latest');
  await jump.click();
  await expect(scroller).toBeFocused();
  await expect(jump).toBeHidden();
  await scroller.evaluate(el => {el.scrollTop = 0;});
  s.fixture.appendSession(s.ids.sessions.implement, 'A note that arrived while you were reading.');
  s.sessionEvent('implement');
  await expect(scroller).toContainText('A note that arrived while you were reading.');
  await expect(jump).toHaveText('New messages');
  await scroller.evaluate(el => {el.scrollTop = el.scrollHeight;});
  await expect(jump).toBeHidden();
  await s.context.close();
});

const at = (minutes: number) => new Date(Date.parse('2026-10-13T09:00:00.000Z') - minutes * 60_000).toISOString();
const crafted = [
  {id: 'm-user', role: 'user', ts: at(90), text: 'Please implement the sign-in handler.'},
  {id: 'm-codex', role: 'assistant', agent: 'codex', model: 'example-codex', ts: at(80), text: 'I will start with the handler.'},
  {id: 'm-tool-ok', role: 'tool', tool: 'Bash', status: 'completed', ts: at(60), text: 'npm test\n' + 'ok - case passes\n'.repeat(400)},
  {id: 'm-tool-fail', role: 'tool', tool: 'Bash', status: 'failed', ts: at(50), text: 'npm run lint\nError: 3 problems\n  src/login.ts:12:5  error  Unexpected any'},
  {id: 'm-tool-run', role: 'tool', tool: 'Bash', status: 'running', ts: at(40), text: 'npm run e2e -- --project=chromium'},
  {id: 'm-system', role: 'system', ts: at(20), text: 'Context compacted\nThe earlier conversation was summarised.'},
  {id: 'm-noagent', role: 'assistant', ts: at(10), text: 'An assistant row without an agent name.', duration_ms: 4200},
  {id: 'm-slow', role: 'assistant', agent: 'claude', model: 'example-claude', ts: at(2), text: 'Last message.\n'.repeat(20), duration_ms: 65_000},
];

test('transcript rows: marks, tool labels, durations and collapsed rows that stay collapsed', async ({browser}) => {
  const s = await openSession(browser, {overrides: [(input, url) => url.pathname === '/sessions/demo-host/signin-implement/messages'
    ? {status: 200, data: {messages: structuredClone(crafted), next_offset: null}} : undefined]});
  await fromRequirements(s.page, s.ids, 'implement');
  const {page} = s;
  const row = (id: string) => page.locator(`[data-message-id="${id}"]`);
  await expect(row('m-slow')).toBeVisible();
  // One mark vocabulary: the header and the transcript agree; tool and system rows carry an icon.
  await expect(row('m-codex').locator(':scope > .ui-agent-mark')).toHaveText('CX');
  await expect(row('m-slow').locator(':scope > .ui-agent-mark')).toHaveText('CL');
  await expect(row('m-noagent').locator(':scope > .ui-agent-mark')).toHaveText('?');
  for (const id of ['m-tool-ok', 'm-system']) {
    await expect(row(id).locator(':scope > .ui-agent-mark svg')).toHaveCount(1);
    await expect(row(id).locator(':scope > .ui-agent-mark')).toHaveText('');
  }
  await expect(row('m-codex').locator('.message-chip')).toHaveText('example-codex');
  await expect(row('m-noagent')).toContainText('4.2 s');
  await expect(row('m-noagent').locator('.message-extra')).not.toContainText('ms');
  await expect(row('m-slow')).toContainText('1 min 5 s');
  // Tool labels: the first line of any command, and a short title.
  await expect(row('m-tool-run').locator('.message-tool-label')).toHaveText('npm run e2e -- --project=chromium');
  await expect(row('m-tool-ok').locator('.message-tool-label')).toHaveText('npm test');
  const title = await row('m-tool-ok').locator('summary').getAttribute('title');
  expect((title || '').length).toBeLessThanOrEqual(200);
  // Collapse the failed and running rows; a live event must not re-open them or move rows.
  await row('m-tool-fail').locator('summary').click();
  await row('m-tool-run').locator('summary').click();
  await expect(row('m-tool-fail').locator('details')).not.toHaveAttribute('open');
  const scroller = page.locator('.conversation-scroll');
  await scroller.evaluate(el => {const r = el.querySelector('[data-message-id="m-tool-fail"]')!;
    el.scrollTop += r.getBoundingClientRect().top - el.getBoundingClientRect().top - 10;});
  await expect.poll(() => scroller.evaluate(el => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeGreaterThan(48);
  const before = await row('m-system').evaluate(el => Math.round(el.getBoundingClientRect().top));
  const reads = s.reads('/sessions/demo-host/signin-implement/messages');
  s.sessionEvent('implement');
  await expect.poll(() => s.reads('/sessions/demo-host/signin-implement/messages')).toBeGreaterThan(reads);
  await page.waitForTimeout(300);
  await expect(row('m-tool-fail').locator('details')).not.toHaveAttribute('open');
  await expect(row('m-tool-run').locator('details')).not.toHaveAttribute('open');
  expect(await row('m-system').evaluate(el => Math.round(el.getBoundingClientRect().top))).toBe(before);
  await s.context.close();
});

test('headings, landmarks and disclosure names are distinct', async ({browser}) => {
  const s = await openSession(browser);
  await fromRequirements(s.page, s.ids, 'implement');
  const {page} = s;
  await expect(page.locator('.conversation-scroll .msg').first()).toBeVisible();
  await expect(page.locator('.ui-inspector-body .session-section-title:visible').first()).toBeVisible();
  expect(await page.locator('.ui-inspector-body h2').count()).toBe(0);
  expect(await page.locator('.ui-inspector-body h3.session-section-title').count()).toBeGreaterThanOrEqual(3);
  await expect(page.getByRole('region', {name: 'Messages', exact: true})).toHaveCount(1);
  const names = await page.locator('.ui-inspector-body summary').evaluateAll(nodes => nodes
    .filter(node => (node as HTMLElement).checkVisibility()).map(node => (node as HTMLElement).innerText.trim()));
  expect(names).not.toContain('Details');
  expect(new Set(names).size).toBe(names.length);
  expect(names).toEqual(expect.arrayContaining(['Checkpoint notes', 'Session details']));
  await s.context.close();
});

test('window caption counts what was received; an empty transcript uses the empty state', async ({browser}) => {
  for (const locale of ['en', 'zh-TW'] as const) {
    const zh = locale === 'zh-TW';
    const s = await openSession(browser, {locale, overrides: [(input, url) => url.pathname === '/sessions/demo-host/ratelimit-review/messages'
      ? {status: 200, data: {messages: [], next_offset: null}} : undefined]});
    await fromRequirements(s.page, s.ids, 'manual');
    const {page} = s;
    await expect(page.locator('.conversation-window')).toHaveText(zh ? '1 則訊息' : '1 message');
    await go(page, `#/session/${s.ids.host}/${s.ids.sessions.implement}`);
    await expect(page.locator('.conversation-window')).toHaveText(zh ? '18 則訊息' : '18 messages');
    await go(page, `#/session/${s.ids.host}/${s.ids.sessions.review}`);
    await expect(page.locator('.conversation-scroll .ui-empty')).toContainText(zh ? '還沒有訊息' : 'No messages yet');
    await expect(page.locator('.conversation-window')).toHaveText(zh ? '0 則訊息' : '0 messages');
    // The empty toolbar takes no room between the transcript and the composer.
    expect(await page.locator('.conversation-toolbar').evaluate(el => el.getBoundingClientRect().height)).toBe(0);
    await s.context.close();
  }
});

test('the way back survives a reload', async ({browser}) => {
  const s = await openSession(browser);
  await fromRequirements(s.page, s.ids, 'implement');
  const {page} = s;
  await expect(page.locator('[data-session-back=requirements]')).toBeVisible();
  await page.reload();
  await expect(page.locator('.workspace-session-heading h1')).toContainText(s.copy.sessionImplement);
  await expect(page.locator('[data-session-back=requirements]')).toHaveText('Back to Requirements');
  await s.context.close();
});

test('Requirements links keep their return query; Discuss returns to the exact Session after reload', async ({browser}) => {
  const s = await openSession(browser);
  const {page, ids} = s;
  await page.goto(`/dashboard/#/pm/${ids.workspaceId}/requirements`);
  await page.locator('[data-rtm-toggle]').first().click();
  await page.locator(`[data-rtm-select="${ids.work.email}"]`).first().click();
  const href = `#/session/${ids.host}/${ids.sessions.implement}?from=requirements`;
  const session = page.locator('[data-work-inspector]').getByRole('link').filter({hasText: s.copy.sessionImplement});
  await expect(session).toHaveAttribute('href', href);
  await session.click();
  await expect(page.locator('.workspace-session-heading h1')).toContainText(s.copy.sessionImplement);
  await page.getByRole('button', {name: 'More actions', exact: true}).click();
  await page.getByRole('menuitem', {name: 'Discuss', exact: true}).click();
  const back = page.locator('[data-discussion-back]');
  await expect(back).toHaveAttribute('href', href);
  await expect(back).toHaveText('Back to Session');
  await expect(page.locator('.pd-thread-header [data-discussion-context=work_item]')).toHaveText('Work · Email sign-in');
  await page.reload();
  await expect(back).toHaveAttribute('href', href);
  await page.setViewportSize({width: 390, height: 844});
  await back.click();
  await expect(page.locator('[data-session-back=requirements]')).toBeVisible();
  await s.context.close();
});
