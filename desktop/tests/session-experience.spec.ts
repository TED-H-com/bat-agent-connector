import {mkdirSync} from 'node:fs';
import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {parseDiscussRefs} from '../src/pm-discuss-link.js';

const shotDir = `${process.env.TMPDIR || '/tmp'}/shots`;

async function shot(page: Page, name: string) {
  mkdirSync(shotDir, {recursive: true});
  await page.screenshot({path: `${shotDir}/${name}.png`});
}

// Hash assignment stays in the same document, so the session can remember the PM view.
async function go(page: Page, hash: string) {
  await page.evaluate(next => {location.hash = next;}, hash);
}

function noOverflow() {
  return document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1;
}

for (const locale of ['en', 'zh-TW'] as const) {
  test(`session workspace ${locale}`, async ({browser, baseURL}) => {
    test.setTimeout(90_000);
    const context = await browser.newContext({
      locale: locale === 'zh-TW' ? 'zh-TW' : 'en-US', baseURL, viewport: {width: 1440, height: 900},
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
    const discuss = zh ? '與專案討論' : 'Discuss with PM';
    const results = zh ? '成果' : 'Results';
    const back = zh ? '返回需求' : 'Back to Requirements';
    await page.setViewportSize({width: 1440, height: 900});
    await page.goto(`/dashboard/#/pm/${ids.workspaceId}/requirements`);
    await expect(page.locator('[data-pm-view=requirements]')).toHaveAttribute('aria-current', 'page');
    await go(page, `#/session/${ids.host}/${ids.sessions.implement}`);
    await expect(page.locator('h1')).toContainText(copy.sessionImplement);
    await expect(page.locator('[data-session-back=requirements]')).toHaveText(back);
    await expect(page.locator('.shell-crumb-view')).toHaveText(zh ? ['工作', '工作階段'] : ['Work', 'Session']);
    await expect(page.locator('.session-pill')).toContainText(zh ? '執行中' : 'Running');
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
    expect(parsed.from).toBe('requirements');
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
    await shot(page, `session-${locale}-1440`);
    await page.setViewportSize({width: 768, height: 900});
    await expect(page.locator('.ui-inspector')).toBeHidden();
    await scroller.evaluate(el => {el.scrollTop = el.scrollHeight;});
    expect(await page.evaluate(noOverflow)).toBe(true);
    await shot(page, `session-${locale}-768`);
    await page.setViewportSize({width: 390, height: 844});
    await scroller.evaluate(el => {el.scrollTop = el.scrollHeight;});
    expect(await page.evaluate(noOverflow)).toBe(true);
    await shot(page, `session-${locale}-390`);
    await page.setViewportSize({width: 1440, height: 900});
    await go(page, `#/session/${ids.host}/${ids.sessions.review}`);
    await expect(page.locator('.session-pill')).toContainText(zh ? '正在詢問' : 'Asking you');
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
    await expect(page.locator('.session-pill')).toContainText(zh ? '唯讀' : 'Read-only');
    await expect(page.locator('#workspace-message-input')).toBeHidden();
    await expect(page.locator('.workspace-composer')).toContainText(zh ? 'API 唯讀' : 'API never writes to it');
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
