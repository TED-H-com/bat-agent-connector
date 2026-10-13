import {mkdirSync} from 'node:fs';
import {expect, test, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {demoFixture} from './demo/index.ts';
import {RUNNING_OPERATION, UNCERTAIN_OPERATION} from './demo/global.ts';

const text = {
  en: {
    pending: 'Pending', questions: 'Questions', reviews: 'Reviews', failed: 'Failed operations',
    answer: 'Answer', details: 'Details', unknown: 'Unknown', action: 'Continue from checkpoint',
    running: 'Running', needs: 'Needs you', operations: 'Operations', settings: 'Settings',
    connection: 'Connection', about: 'About', disconnect: 'Disconnect', connected: 'Connected',
    noEvent: 'No event yet', guide: 'Setup guide', connect: 'Connect',
    sentence: 'Connect this dashboard to your Connector.', product: 'BAT Dashboard',
    note: 'Questions and permission requests reported by central. Reading does not resolve them.',
    noMatch: 'No matches on this page', inReview: 'In review', sections: 'Settings sections',
    filter: 'Needs you',
  },
  'zh-TW': {
    pending: '待處理', questions: '提問', reviews: '待你確認', failed: '失敗的操作',
    answer: '回答', details: '詳細資料', unknown: '未知', action: '從檢查點繼續',
    running: '執行中', needs: '需要你', operations: '操作紀錄', settings: '設定',
    connection: '連線', about: '關於', disconnect: '中斷', connected: '已連線',
    noEvent: '尚無事件', guide: '設定說明', connect: '連線',
    sentence: '將這個儀表板連到你的 Connector。', product: 'BAT Dashboard',
    note: '依中央實際問題與權限請求列出；閱讀不會解除請求。',
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
    await expect(page.locator('[data-pending-count]:visible')).toHaveText('5');
    const questions = page.getByRole('region', {name: words.questions});
    await expect(questions).toContainText(fixture.copy.sessionReview);
    await expect(questions.getByRole('link', {name: words.answer, exact: true})).toHaveAttribute('href', `#/session/${fixture.ids.host}/${fixture.ids.sessions.review}`);
    const reviews = page.getByRole('region', {name: words.reviews});
    await expect(reviews).toContainText(fixture.copy.work.rate);
    await expect(reviews).toContainText(fixture.copy.work.lockoutMessage);
    await expect(reviews.getByText(words.inReview, {exact: true})).toHaveCount(2);
    const failed = page.getByRole('region', {name: words.failed});
    const bare = failed.locator(`[data-inbox-key="${UNCERTAIN_OPERATION}"]`);
    await expect(bare).toContainText(words.action);
    await expect(bare).toContainText(words.unknown);
    await expect(bare).not.toContainText('·');
    await expect(bare).not.toContainText('checkpoint.continue');
    await expect(failed).not.toContainText('loaded (not a total)');
    await expect(questions.getByRole('button', {name: locale === 'zh-TW' ? '載入更多' : 'Load more'})).toBeHidden();
    await expect(page.getByText(words.note)).toBeHidden();
    await expectNoJunk(page);
    await expectNoOverflow(page);
    await shot(page, `pending-${locale}-${width}${dark ? '-dark' : ''}`);
    // The shared disclosure control is a summary, not a button.
    await page.locator('#main > details.ui-disclosure > summary').click();
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
    await closeTree(page, width);

    await page.locator(`[aria-label="${words.details} for ${words.action}"], [aria-label="${words.action}的詳細資料"]`).click();
    const inbox = page.locator('.ui-inspector');
    await expect(inbox).toContainText(words.action);
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
    await expect(inspector.locator('details.ui-disclosure > summary')).toBeVisible();
    await inspector.locator('details.ui-disclosure > summary').click();
    await expect(inspector.locator('code').filter({hasText: 'checkpoint.continue'})).toBeVisible();
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
