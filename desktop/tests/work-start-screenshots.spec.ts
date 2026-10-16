import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workStartFixture, workspaceId, workId, route} from './work-start-fixture.ts';
import * as path from 'path';
import * as fs from 'fs';

const SHOTS_DIR = process.env.BATC_WORK_START_SCREENSHOTS || path.join('test-results', 'work-start-shots');

test.beforeAll(() => {
  fs.mkdirSync(SHOTS_DIR, {recursive: true});
});

async function ensureScrolled(locator: any) {
  await locator.evaluate((el: HTMLElement) => {
    el.scrollIntoView({block: 'start', inline: 'nearest', behavior: 'instant'});
    const container = el.closest('.ui-inspector-body');
    if (container && container.scrollTop > 16) {
      container.scrollTop = Math.max(0, container.scrollTop - 16);
    }
  });
}

test.describe('Work Start Screenshots', () => {
  // 1. Initial form before checking
  test('en-1440-form', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();

    const startSection = inspector.locator('[data-work-section="start"]');
    await expect(startSection).toBeVisible();

    const checkBtn = startSection.locator('[data-work-start-check]');
    await expect(checkBtn).toBeVisible();
    await expect(checkBtn).toBeEnabled();

    const submitBtn = startSection.locator('[data-pm-action="work.start"] button[type="submit"]');
    await expect(submitBtn).toBeDisabled();

    await ensureScrolled(startSection);
    await startSection.screenshot({path: path.join(SHOTS_DIR, 'en-1440-form.png')});
  });

  test('zh-TW-390-form', async ({browser}) => {
    const context = await browser.newContext({locale: 'zh-TW', viewport: {width: 390, height: 844}});
    const page = await context.newPage();
    const f = workStartFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('#workspace-menu').click();
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();

    const startSection = inspector.locator('[data-work-section="start"]');
    await expect(startSection).toBeVisible();

    const checkBtn = startSection.locator('[data-work-start-check]');
    await expect(checkBtn).toBeVisible();
    await expect(checkBtn).toBeEnabled();

    const submitBtn = startSection.locator('[data-pm-action="work.start"] button[type="submit"]');
    await expect(submitBtn).toBeDisabled();

    await startSection.scrollIntoViewIfNeeded();
    await inspector.screenshot({path: path.join(SHOTS_DIR, 'zh-TW-390-form.png')});
    await context.close();
  });

  // 2. Ready (facts, checks passed, Start enabled)
  test('en-1440-ready', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const startSection = inspector.locator('[data-work-section="start"]');
    const checkBtn = startSection.locator('[data-work-start-check]');
    const submitBtn = startSection.locator('[data-pm-action="work.start"] button[type="submit"]');

    await checkBtn.click();
    await expect(submitBtn).toBeEnabled();
    await expect(startSection.locator('[data-work-start-facts]')).toBeVisible();

    await ensureScrolled(startSection);
    await startSection.screenshot({path: path.join(SHOTS_DIR, 'en-1440-ready.png')});
  });

  test('zh-TW-390-ready', async ({browser}) => {
    const context = await browser.newContext({locale: 'zh-TW', viewport: {width: 390, height: 844}});
    const page = await context.newPage();
    const f = workStartFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('#workspace-menu').click();
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const startSection = inspector.locator('[data-work-section="start"]');
    const checkBtn = startSection.locator('[data-work-start-check]');
    const submitBtn = startSection.locator('[data-pm-action="work.start"] button[type="submit"]');

    await checkBtn.click();
    await expect(submitBtn).toBeEnabled();
    await expect(startSection.locator('[data-work-start-facts]')).toBeVisible();

    // Scroll Start button into view inside the inspector body
    await submitBtn.scrollIntoViewIfNeeded();
    await expect(submitBtn).toBeVisible();
    await inspector.screenshot({path: path.join(SHOTS_DIR, 'zh-TW-390-ready.png')});
    await context.close();
  });

  // 3. Blocked (failed check with help text, Start disabled)
  test('en-1440-blocked', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    f.state.readinessStatus = 'blocked';

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const startSection = inspector.locator('[data-work-section="start"]');
    const checkBtn = startSection.locator('[data-work-start-check]');
    const submitBtn = startSection.locator('[data-pm-action="work.start"] button[type="submit"]');

    await checkBtn.click();
    await expect(submitBtn).toBeDisabled();
    await expect(startSection.locator('[data-pm-wp-checks]')).toBeVisible();

    await ensureScrolled(startSection);
    await startSection.screenshot({path: path.join(SHOTS_DIR, 'en-1440-blocked.png')});
  });

  test('zh-TW-390-blocked', async ({browser}) => {
    const context = await browser.newContext({locale: 'zh-TW', viewport: {width: 390, height: 844}});
    const page = await context.newPage();
    const f = workStartFixture();
    f.state.readinessStatus = 'blocked';

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('#workspace-menu').click();
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const startSection = inspector.locator('[data-work-section="start"]');
    const checkBtn = startSection.locator('[data-work-start-check]');
    const submitBtn = startSection.locator('[data-pm-action="work.start"] button[type="submit"]');

    await checkBtn.click();
    await expect(submitBtn).toBeDisabled();

    const checksList = startSection.locator('[data-pm-wp-checks]');
    await expect(checksList).toBeVisible();

    const blockedRow = checksList.locator('[data-readiness-check="acceptance:membership.login"]');
    await expect(blockedRow).toBeVisible();
    // Scroll the failing check row into view within the inspector body
    await blockedRow.scrollIntoViewIfNeeded();
    await page.evaluate(() => {
      const container = document.querySelector('.ui-inspector-body');
      if (container) container.scrollTop += 50;
    });

    await inspector.screenshot({path: path.join(SHOTS_DIR, 'zh-TW-390-blocked.png')});
    await context.close();
  });

  // 4. Stale (modified instruction after ready, warning visible, Start disabled)
  test('en-1440-stale', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const startSection = inspector.locator('[data-work-section="start"]');
    const checkBtn = startSection.locator('[data-work-start-check]');
    const submitBtn = startSection.locator('[data-pm-action="work.start"] button[type="submit"]');
    const instruction = startSection.locator('[data-work-start-instruction]');
    const staleNotice = startSection.locator('[data-work-start-stale]');

    await checkBtn.click();
    await expect(submitBtn).toBeEnabled();

    await instruction.fill('Modified instruction text');
    await expect(staleNotice).toBeVisible();
    await expect(submitBtn).toBeDisabled();

    await ensureScrolled(startSection);
    await startSection.screenshot({path: path.join(SHOTS_DIR, 'en-1440-stale.png')});
  });

  test('zh-TW-390-stale', async ({browser}) => {
    const context = await browser.newContext({locale: 'zh-TW', viewport: {width: 390, height: 844}});
    const page = await context.newPage();
    const f = workStartFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('#workspace-menu').click();
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const startSection = inspector.locator('[data-work-section="start"]');
    const checkBtn = startSection.locator('[data-work-start-check]');
    const submitBtn = startSection.locator('[data-pm-action="work.start"] button[type="submit"]');
    const instruction = startSection.locator('[data-work-start-instruction]');
    const staleNotice = startSection.locator('[data-work-start-stale]');

    await checkBtn.click();
    await expect(submitBtn).toBeEnabled();

    await instruction.fill('修改後的指示內容');
    await expect(staleNotice).toBeVisible();
    await expect(submitBtn).toBeDisabled();

    await staleNotice.scrollIntoViewIfNeeded();
    await inspector.screenshot({path: path.join(SHOTS_DIR, 'zh-TW-390-stale.png')});
    await context.close();
  });

  // 5. Bind-needed (notice, checkboxes with labels beside them, Start disabled)
  test('en-1440-bind-needed', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(3)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const startSection = inspector.locator('[data-work-section="start"]');
    const reqLabels = startSection.locator('.pm-work-start-reqs label.pm-work-start-req');
    await expect(reqLabels).toHaveCount(2);

    const submitBtn = startSection.locator('[data-pm-action="work.start"] button[type="submit"]');
    await expect(submitBtn).toBeDisabled();

    await ensureScrolled(startSection);
    await startSection.screenshot({path: path.join(SHOTS_DIR, 'en-1440-bind-needed.png')});
  });

  test('zh-TW-390-bind-needed', async ({browser}) => {
    const context = await browser.newContext({locale: 'zh-TW', viewport: {width: 390, height: 844}});
    const page = await context.newPage();
    const f = workStartFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('#workspace-menu').click();
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(3)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const startSection = inspector.locator('[data-work-section="start"]');
    const reqLabels = startSection.locator('.pm-work-start-reqs label.pm-work-start-req');
    await expect(reqLabels).toHaveCount(2);

    const submitBtn = startSection.locator('[data-pm-action="work.start"] button[type="submit"]');
    await expect(submitBtn).toBeDisabled();

    await startSection.locator('.pm-work-start-reqs').scrollIntoViewIfNeeded();
    await inspector.screenshot({path: path.join(SHOTS_DIR, 'zh-TW-390-bind-needed.png')});
    await context.close();
  });

  // 6. Latest attempt row with Details disclosure open (unknown stage)
  test('en-1440-attempt', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    f.state.attempts = [{
      attempt_id: 'att_' + '1'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      stage: 'future_stage',
      status: 'running',
      start_operation_id: 'op_' + '1'.repeat(32),
      created_at: 1791705600,
      updated_at: 1791705600,
    }];

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const startSection = inspector.locator('[data-work-section="start"]');
    const attempt = startSection.locator('.pm-work-start-attempt');
    await expect(attempt.locator('.pm-work-start-attempt-status strong')).toHaveText('Unknown');

    const details = attempt.locator('details');
    await expect(details).toBeVisible();
    await details.locator('summary').click();
    await expect(details).toHaveAttribute('open', '');
    await expect(details).toContainText('future_stage');

    await ensureScrolled(startSection);
    await startSection.screenshot({path: path.join(SHOTS_DIR, 'en-1440-attempt.png')});
  });

  test('zh-TW-390-attempt', async ({browser}) => {
    const context = await browser.newContext({locale: 'zh-TW', viewport: {width: 390, height: 844}});
    const page = await context.newPage();
    const f = workStartFixture();
    f.state.attempts = [{
      attempt_id: 'att_' + '1'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      stage: 'future_stage',
      status: 'running',
      start_operation_id: 'op_' + '1'.repeat(32),
      created_at: 1791705600,
      updated_at: 1791705600,
    }];

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('#workspace-menu').click();
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const startSection = inspector.locator('[data-work-section="start"]');
    const attempt = startSection.locator('.pm-work-start-attempt');
    await expect(attempt.locator('.pm-work-start-attempt-status strong')).toHaveText('未知');

    const details = attempt.locator('details');
    await expect(details).toBeVisible();
    await details.locator('summary').click();
    await expect(details).toHaveAttribute('open', '');
    await expect(details).toContainText('future_stage');

    await attempt.scrollIntoViewIfNeeded();
    await inspector.screenshot({path: path.join(SHOTS_DIR, 'zh-TW-390-attempt.png')});
    await context.close();
  });
});
