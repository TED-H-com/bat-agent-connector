import {mkdirSync} from 'node:fs';
import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {RUNNING_OPERATION, UNCERTAIN_OPERATION} from './demo/global.ts';

const text = {
  en: {
    pending: 'Pending', questions: 'Questions', reviews: 'Reviews', failed: 'Needs attention',
    answer: 'Answer', open: 'Open', review: 'Review', readOnly: 'Read-only', details: 'Details', unknown: 'Unknown', action: 'Continue from checkpoint',
    running: 'Running', needs: 'Needs you', failedRail: 'Failed', operations: 'Operations', settings: 'Settings',
    connection: 'Connection', about: 'About', disconnect: 'Disconnect', connected: 'Connected', access: 'Access',
    noEvent: 'No event yet', guide: 'Setup guide', connect: 'Connect', openOperation: 'Open operation', attempts: 'Attempts',
    sentence: 'Connect this dashboard to your Connector.', product: 'BAT Dashboard',
    note: 'Questions and permission requests reported by Connector. Reading does not resolve them.',
    noMatch: 'No matches on this page', inReview: 'In review', sections: 'Settings sections',
    filter: 'Needs you',
  },
  'zh-TW': {
    pending: '待處理', questions: '提問', reviews: '待你確認', failed: '需要處理',
    answer: '回答', open: '開啟', review: '查看', readOnly: '唯讀', details: '詳細資料', unknown: '未知', action: '從檢查點繼續',
    running: '執行中', needs: '需要你處理', failedRail: '失敗', operations: '操作紀錄', settings: '設定',
    connection: '連線', about: '關於', disconnect: '中斷', connected: '已連線', access: '存取',
    noEvent: '尚無事件', guide: '設定說明', connect: '連線', openOperation: '開啟操作', attempts: '嘗試次數',
    sentence: '將這個儀表板連到你的 Connector。', product: 'BAT Dashboard',
    note: '列出 Connector 回報的問題與權限請求；閱讀不會解除請求。',
    noMatch: '這一頁沒有相符項目', inReview: '審閱中', sections: '設定分類',
    filter: '需要你處理',
  },
} as const;

type Locale = keyof typeof text;

function shotDir() {
  return process.env.BATC_GLOBAL_SHOTS_DIR || `${process.env.TMPDIR || '/tmp'}/shots`;
}

async function shot(page: Page, name: string) {
  if (process.env.BATC_GLOBAL_SHOTS !== '1') return;
  await page.evaluate(() => {if (document.activeElement instanceof HTMLElement) document.activeElement.blur();});
  mkdirSync(shotDir(), {recursive: true});
  await page.screenshot({path: `${shotDir()}/${name}.png`, fullPage: true});
}

async function expectNoOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

async function expectNoJunk(page: Page) {
  const body = await page.locator('#main').innerText();
  expect(body).not.toMatch(/\b(?:undefined|null|NaN)\b/);
}

async function revealTree(page: Page, width: number) {
  if (width >= 1000) return;
  if (await page.locator('.workspace-nav').getAttribute('aria-modal') === 'true') return;
  await page.locator('#workspace-menu').click();
  await expect(page.locator('.workspace-nav')).toHaveAttribute('aria-modal', 'true');
}

async function closeTree(page: Page, width: number) {
  if (width >= 1000) return;
  if (await page.locator('.workspace-nav').getAttribute('aria-modal') !== 'true') return;
  await page.keyboard.press('Escape');
  await expect(page.locator('.workspace-nav')).not.toHaveAttribute('aria-modal', 'true');
}

const layouts: {locale: Locale, width: number, dark?: boolean}[] = [
  {locale: 'en', width: 1440},
  {locale: 'en', width: 768},
  {locale: 'en', width: 390},
  {locale: 'zh-TW', width: 1440},
  {locale: 'zh-TW', width: 768},
  {locale: 'zh-TW', width: 390},
  {locale: 'en', width: 1440, dark: true},
];

for (const {locale, width, dark} of layouts) {
  test(`global screens in ${locale} at ${width}${dark ? ' dark' : ''}`, async ({browser, baseURL}) => {
    test.setTimeout(90_000);
    const words = text[locale];
    const fixture = demoFixture({locale});
    const context = await browser.newContext({locale, baseURL, viewport: {width, height: width === 390 ? 844 : 900}, colorScheme: dark ? 'dark' : 'light'});
    const page = await context.newPage();
    const errors: string[] = [];
    const httpErrors: string[] = [];
    page.on('pageerror', error => errors.push(error.stack || error.message));
    page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
    page.on('response', response => {
      if (response.status() < 400) return;
      httpErrors.push(`${response.status()} ${new URL(response.url()).pathname}`);
    });
    await mountConversation(page, false, fixture.dispatch);
    if (dark) await page.addInitScript(() => {
      const root = document.documentElement;
      if (root) root.dataset.theme = 'dark';
    });
    await page.goto('/dashboard/#/home');
    if (dark) await page.evaluate(() => {document.documentElement.dataset.theme = 'dark';});
    await expect(page.getByRole('heading', {level: 1, name: words.pending})).toBeVisible();
    await expect(page.getByRole('link', {name: words.pending, exact: true})).toHaveCount(1);
    await expect(page.locator('[data-pending-count]:visible')).toHaveText('6');
    const questions = page.getByRole('region', {name: words.questions});
    await expect(questions).toContainText(fixture.copy.sessionReview);
    await expect(questions.getByRole('link', {name: words.answer, exact: true})).toHaveAttribute('href', `#/session/${fixture.ids.host}/${fixture.ids.sessions.review}`);
    const manual = questions.locator('[data-inbox-key]').filter({hasText: fixture.copy.manualTitle});
    await expect(manual.getByRole('link', {name: words.open, exact: true})).toHaveAttribute('href', `#/session/${fixture.ids.host}/${fixture.ids.sessions.manual}`);
    await expect(manual.getByRole('link', {name: words.answer, exact: true})).toHaveCount(0);
    await expect(manual.getByRole('link', {name: words.review, exact: true})).toHaveCount(0);
    await expect(manual.getByText(words.readOnly, {exact: true})).toBeVisible();
    const askingRow = questions.locator('[data-inbox-key]').filter({hasText: fixture.copy.sessionReview});
    await askingRow.getByRole('button').click();
    const sessionInspector = page.locator('.ui-inspector');
    await expect(sessionInspector.getByRole('link', {name: words.answer, exact: true})).toHaveAttribute('href', `#/session/${fixture.ids.host}/${fixture.ids.sessions.review}`);
    await page.keyboard.press('Escape');
    await expect(sessionInspector).toHaveCount(0);
    const reviews = page.getByRole('region', {name: words.reviews});
    await expect(reviews).toContainText(fixture.copy.work.rate);
    await expect(reviews).toContainText(fixture.copy.work.lockoutMessage);
    await expect(reviews.getByText(words.inReview, {exact: true})).toHaveCount(2);
    const failed = page.getByRole('region', {name: words.failed});
    const bare = failed.locator(`[data-inbox-key="${UNCERTAIN_OPERATION}"]`);
    await expect(bare).toContainText(words.action);
    await expect(bare).toContainText(words.unknown);
    await expect(bare).not.toContainText('· ·');
    await expect(bare).not.toContainText('checkpoint.continue');
    await expect(failed).not.toContainText('loaded (not a total)');
    await expect(questions.getByRole('button', {name: locale === 'zh-TW' ? '載入更多' : 'Load more'})).toBeHidden();
    await expect(page.getByText(words.note)).toBeHidden();
    await expectNoJunk(page);
    await expectNoOverflow(page);
    await shot(page, `pending-${locale}-${width}${dark ? '-dark' : ''}`);
    // The shared disclosure control is a summary, not a button.
    await page.locator('#main .global-pending-main > details.ui-disclosure > summary').click();
    await expect(page.getByText(words.note)).toBeVisible();
    await expectNoOverflow(page);

    await revealTree(page, width);
    const project = page.locator(`[data-tree-key="#/project/${fixture.ids.projectId}"]`);
    await expect(project).toHaveAttribute('aria-label', new RegExp(`${fixture.copy.name} · ${words.needs}`));
    await expect(project.locator('.workspace-rail-alert')).toBeVisible();
    await expect(project.locator('.workspace-tree-state-label')).toHaveCount(0);
    const work = page.locator(`[data-tree-key="#/work/${encodeURIComponent(fixture.ids.projectId)}/session/${encodeURIComponent(fixture.ids.sessions.implement)}"]`);
    await expect(work).toHaveAttribute('aria-label', new RegExp(words.running));
    await expect(work.locator('.workspace-dot')).toBeVisible();
    await expect(work.locator('.workspace-tree-state-label')).toHaveCount(0);
    const failedWork = page.locator(`[data-tree-key="#/work/${encodeURIComponent(fixture.ids.projectId)}/execution/failed-delivery"]`);
    await expect(failedWork).toHaveAttribute('aria-label', new RegExp(words.failedRail));
    await expect(failedWork.locator('.workspace-rail-failed')).toBeVisible();
    const unreadWork = page.locator(`[data-tree-key="#/work/${encodeURIComponent(fixture.ids.projectId)}/session/${encodeURIComponent(fixture.ids.sessions.manual)}"]`);
    await expect(unreadWork.locator('.workspace-unread-count')).toHaveText('3');
    await expect(unreadWork.locator('.workspace-tree-state')).toHaveCount(0);
    await closeTree(page, width);

    await page.locator(`[aria-label="${words.details} for ${words.action}"], [aria-label="${words.action}的詳細資料"]`).click();
    const inbox = page.locator('.ui-inspector');
    await expect(inbox).toContainText(words.action);
    if (width >= 1200) {
      const placed = await page.evaluate(() => {
        const box = (node: Element | null) => {
          const rect = node?.getBoundingClientRect();
          return rect ? {x: rect.x, width: rect.width} : null;
        };
        return {
          group: box(document.querySelector('section[data-inbox-group="replies"]')),
          inspector: box(document.querySelector('#main .ui-inspector')),
          details: box(document.querySelector('#main .global-pending-main > details.ui-disclosure')),
        };
      });
      expect(placed.group && placed.inspector && placed.details).toBeTruthy();
      expect(placed.inspector!.x).toBeGreaterThan(placed.group!.x + placed.group!.width - 2);
      expect(Math.abs(placed.details!.x - placed.group!.x)).toBeLessThanOrEqual(8);
    }
    await inbox.locator('details.ui-disclosure > summary').click();
    await expect(inbox.locator('code', {hasText: 'checkpoint.continue'})).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(inbox).toHaveCount(0);

    await questions.getByRole('link', {name: words.answer, exact: true}).click();
    await expect(page.locator('h1')).toContainText(fixture.copy.sessionReview);
    await page.goto('/dashboard/#/operations');
    await expect(page.getByRole('heading', {level: 1, name: words.operations})).toBeVisible();
    const search = page.getByRole('searchbox', {name: locale === 'zh-TW' ? '搜尋操作' : 'Search operations'});
    await search.fill('checkpoint');
    await expect(page.locator(`[data-row-key="${UNCERTAIN_OPERATION}"]`)).toBeVisible();
    await expect(page.locator(`[data-row-key="${RUNNING_OPERATION}"]`)).toHaveCount(0);
    await page.evaluate(() => {location.hash = '#/settings';});
    await expect(page.getByRole('heading', {level: 1, name: words.settings})).toBeVisible();
    await page.evaluate(() => {location.hash = '#/operations';});
    await expect(search).toHaveValue('checkpoint');
    await search.fill('zzzz-no-match');
    await expect(page.locator('#main')).toContainText(words.noMatch);
    await search.fill('');
    const row = page.locator(`[data-row-key="${UNCERTAIN_OPERATION}"]`);
    await expect(row).toContainText(words.action);
    await expect(row).not.toContainText('checkpoint.continue');
    await expect(page.locator(`[data-row-key="${RUNNING_OPERATION}"]`)).toBeVisible();
    await expectNoOverflow(page);
    await shot(page, `operations-${locale}-${width}${dark ? '-dark' : ''}`);
    await row.click();
    const inspector = page.locator('.ui-inspector');
    await expect(inspector.getByRole('link', {name: words.openOperation, exact: true})).toHaveAttribute('href', `#/op/${UNCERTAIN_OPERATION}`);
    await expect(inspector).toContainText(words.attempts);
    await expect(inspector.locator('p.muted').first()).toHaveText(fixture.ids.repository);
    await expect(inspector).toContainText(locale === 'zh-TW' ? '結果不明，回查中' : 'outcome unknown, reading back');
    await expect(inspector.locator('.ui-inspector-body > p').filter({hasText: 'Checkpoint result is not confirmed'})).toHaveCount(0);
    await expect(inspector.locator('details.ui-disclosure > summary')).toBeVisible();
    await inspector.locator('details.ui-disclosure > summary').click();
    await expect(inspector.locator('code').filter({hasText: 'checkpoint.continue'})).toBeVisible();
    await expect(inspector.locator('details.ui-disclosure').getByText('Checkpoint result is not confirmed', {exact: true})).toBeVisible();
    await page.keyboard.press('Escape');
    await page.getByRole('radio', {name: words.filter, exact: true}).click();
    await expect(page).toHaveURL(/#\/operations\/attention$/);
    await expect(page.locator(`[data-row-key="${UNCERTAIN_OPERATION}"]`)).toBeVisible();
    await expect(page.locator(`[data-row-key="${RUNNING_OPERATION}"]`)).toHaveCount(0);
    await expectNoOverflow(page);

    await page.goto('/dashboard/#/settings');
    await expect(page.getByRole('heading', {level: 1, name: words.settings})).toBeVisible();
    if (width < 1000) {
      await page.locator('.shell-mobile-name').click();
      await expect(page.locator('.workspace-nav')).toHaveAttribute('aria-modal', 'true');
    }
    await expect(page.getByRole('link', {name: words.settings, exact: true})).toHaveCount(1);
    if (width < 1000) {
      await page.keyboard.press('Escape');
      await expect(page.locator('.workspace-nav')).not.toHaveAttribute('aria-modal', 'true');
    }
    const facts = page.locator('.global-connection-facts');
    await expect(facts).toContainText(words.connected);
    await expect(facts).toContainText(words.noEvent);
    await expect(page.locator('[data-connection-access]')).toContainText(locale === 'en' ? 'View' : '檢視');
    expect(await page.locator('[data-settings-panel=connection]').innerText()).not.toContain('mei.lin · observe');
    await page.locator('[data-settings-panel=connection] details > summary').click();
    await expect(page.locator('[data-settings-panel=connection] details')).toContainText('mei.lin');
    await expect(page.locator('[data-settings-panel=connection] details')).toContainText('observe');
    await expect(facts.locator('dd').first()).not.toHaveText('—');
    await expect(page.getByRole('button', {name: words.disconnect, exact: true})).toBeVisible();
    await expectNoJunk(page);
    await expectNoOverflow(page);
    await shot(page, `settings-${locale}-${width}${dark ? '-dark' : ''}`);
    const tabs = page.locator(`[aria-label="${words.sections}"] [role="tab"]:visible`);
    await tabs.first().focus();
    await page.keyboard.press('ArrowDown');
    await expect(page.locator('[role="tab"][aria-selected="true"]')).not.toHaveText(words.connection);
    await page.keyboard.press('End');
    await expect(page.locator('[role="tab"][aria-selected="true"]')).toHaveText(words.about);
    await expect(page.getByRole('link', {name: words.guide})).toBeVisible();
    await expectNoOverflow(page);
    expect(httpErrors).toEqual(['404 /api/v1/browser-session']);
    expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
    await context.close();
  });
}

for (const locale of ['en', 'zh-TW'] as const) for (const width of [1440, 390]) {
  test(`first run connects in ${locale} at ${width}`, async ({browser, baseURL}) => {
    const words = text[locale];
    const fixture = demoFixture({locale});
    const context = await browser.newContext({locale, baseURL, viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage();
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
    await page.route('**/api/v1/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const result = await fixture.dispatch({method: request.method(), path: url.pathname.slice(7) + url.search,
        body: request.postData() ? request.postDataJSON() : null, idempotency_key: request.headers()['idempotency-key']});
      await route.fulfill({status: result.status, json: result.data});
    });
    await page.goto('/dashboard/');
    const card = page.locator('.global-first-card');
    await expect(card.getByRole('heading', {level: 1, name: words.product})).toBeVisible();
    await expect(card).toContainText(words.sentence);
    await expect(card.locator('.brand-mark')).toBeVisible();
    await expect(card.getByRole('link', {name: words.guide})).toHaveAttribute('href', new RegExp(locale === 'zh-TW' ? 'getting-started\\.zh-TW\\.md' : 'getting-started\\.md'));
    await expect(card.locator('input[type="password"]')).toBeVisible();
    await expect(card.getByText(locale === 'zh-TW' ? '以 batc api-token issue 發行' : 'Issue one with batc api-token issue')).toBeHidden();
    await expectNoOverflow(page);
    await shot(page, `first-run-${locale}-${width}`);
    await card.locator('input[type="password"]').fill('fixture-token');
    await card.getByRole('button', {name: words.connect, exact: true}).click();
    await expect(page.getByRole('heading', {level: 1, name: words.pending})).toBeVisible();
    await expect(page.getByRole('region', {name: words.questions})).toContainText(fixture.copy.sessionReview);
    expect(errors.filter(line => !line.startsWith('Failed to load resource:'))).toEqual([]);
    await context.close();
  });
}
