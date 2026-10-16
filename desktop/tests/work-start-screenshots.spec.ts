import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workStartFixture, workspaceId, workId, route} from './work-start-fixture.ts';
import * as path from 'path';

const SHOTS_DIR = path.resolve(process.env.HOME || '/home/ted-h', 'agent-work/tmp/work-start-ui/shots/r2');

async function ensureScrolled(locator: any) {
  await locator.evaluate((el: HTMLElement) => {
    el.scrollIntoView({block: 'start', inline: 'nearest', behavior: 'instant'});
    const container = el.closest('.ui-inspector-body');
    if (container && container.scrollTop > 16) {
      container.scrollTop = Math.max(0, container.scrollTop - 16);
    }
  });
}

test.describe('Work Start Screenshots (Round 2)', () => {
  test('en 1440 - in-progress', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    f.state.attempts = [{
      attempt_id: 'att_' + '1'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      status: 'running',
      stage: 'preparing',
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
    await expect(inspector).toBeVisible();

    const startSection = inspector.locator('[data-work-section="start"]');
    await expect(startSection).toBeVisible();

    const attempt = startSection.locator('.pm-work-start-attempt');
    await expect(attempt).toContainText('Preparing the work basis');
    const viewOp = attempt.locator('a:has-text("View operation")');
    await expect(viewOp).toBeVisible();

    await ensureScrolled(startSection);

    await page.screenshot({path: path.join(SHOTS_DIR, 'en-1440-in-progress-full.png'), fullPage: true});
    await startSection.screenshot({path: path.join(SHOTS_DIR, 'en-1440-in-progress.png')});
  });

  test('en 1440 - started', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    f.state.attempts = [{
      attempt_id: 'att_' + '2'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      status: 'succeeded',
      stage: 'started',
      session: {host: 'fixture-host', session_id: 'sess_' + '2'.repeat(32)},
      work_tree_id: 'wt_' + '2'.repeat(32),
      created_at: 1791705600,
      updated_at: 1791705600,
    }];

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();

    const startSection = inspector.locator('[data-work-section="start"]');
    await expect(startSection).toBeVisible();

    const attempt = startSection.locator('.pm-work-start-attempt');
    await expect(attempt).toContainText('Started');

    const liveOutput = attempt.locator('[data-work-start-session]');
    await expect(liveOutput).toHaveText('Open live output');
    const openWork = attempt.locator('a:has-text("Open work")');
    await expect(openWork).toBeVisible();

    // Verify form and header button hidden on started
    await expect(inspector.locator('[data-work-start-open]')).toHaveCount(0);
    await expect(startSection.locator('form')).toBeHidden();

    await ensureScrolled(startSection);

    await page.screenshot({path: path.join(SHOTS_DIR, 'en-1440-started-full.png'), fullPage: true});
    await startSection.screenshot({path: path.join(SHOTS_DIR, 'en-1440-started.png')});
  });

  test('en 1440 - admission-refusal', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 1080});
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

    // Check readiness to enable Start
    const checkBtn = startSection.locator('[data-work-start-check]');
    await checkBtn.click();
    await expect(startSection.locator('[data-work-start-facts]')).toBeVisible();

    // Configure refusal on submit
    f.state.refusalCode = 'PM_WORK_START_IN_PROGRESS';

    const submitBtn = startSection.locator('[data-pm-action="work.start"] button[type="submit"]');
    await expect(submitBtn).toBeEnabled();
    await submitBtn.click();

    // Operation fails with admission refusal
    const errorMsg = startSection.locator('form .error');
    await expect(errorMsg).toBeVisible();
    await expect(errorMsg).toContainText('This Work is already starting. Follow or cancel that operation first.');

    const newReqBtn = startSection.locator('form').getByRole('button', {name: 'New request'});
    await expect(newReqBtn).toBeVisible();

    await ensureScrolled(errorMsg);

    await page.screenshot({path: path.join(SHOTS_DIR, 'en-1440-admission-refusal-full.png'), fullPage: true});
    await startSection.screenshot({path: path.join(SHOTS_DIR, 'en-1440-admission-refusal.png')});
  });

  test('en 1440 - bind-needed contributor', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    f.state.role = 'contributor';

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    // Card 2 is workId(3) which has plan_revision_id from older plan (needs re-binding)
    const card = page.locator(`[data-pm-kanban-work="${workId(3)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();

    const startSection = inspector.locator('[data-work-section="start"]');
    await expect(startSection).toBeVisible();

    // Contributor explanation visible
    const disabledNotice = startSection.locator('[data-work-start-disabled]');
    await expect(disabledNotice).toBeVisible();
    await expect(disabledNotice).toContainText('Workspace owner required to bind this Work to the current plan.');

    await ensureScrolled(startSection);

    await page.screenshot({path: path.join(SHOTS_DIR, 'en-1440-bind-needed-full.png'), fullPage: true});
    await startSection.screenshot({path: path.join(SHOTS_DIR, 'en-1440-bind-needed.png')});
  });

  test('zh-TW 390 - form', async ({browser}) => {
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
    await expect(startSection.locator('h3')).toHaveText('開始工作');

    await ensureScrolled(startSection);

    await page.screenshot({path: path.join(SHOTS_DIR, 'zh-TW-390-form-full.png'), fullPage: true});
    await startSection.screenshot({path: path.join(SHOTS_DIR, 'zh-TW-390-form.png')});
  });

  test('zh-TW 390 - blocked-readiness', async ({browser}) => {
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
    await expect(inspector).toBeVisible();

    const startSection = inspector.locator('[data-work-section="start"]');
    await expect(startSection).toBeVisible();

    // Check readiness to reveal blocked checks
    const checkBtn = startSection.locator('[data-work-start-check]');
    await checkBtn.click();

    const checksList = startSection.locator('[data-pm-wp-checks]');
    await expect(checksList).toBeVisible();

    const blockedCheck = checksList.locator('[data-readiness-check="acceptance:membership.login"]');
    await expect(blockedCheck).toBeVisible();
    // Localized help is visible
    await expect(blockedCheck).toContainText('每個原始接受條件都需有預定驗證方式；這些方法尚未執行。');
    // English server detail is NOT shown
    await expect(blockedCheck).not.toContainText('Acceptance criteria not met');

    await blockedCheck.scrollIntoViewIfNeeded();

    await page.screenshot({path: path.join(SHOTS_DIR, 'zh-TW-390-blocked-readiness-full.png'), fullPage: true});
    await startSection.screenshot({path: path.join(SHOTS_DIR, 'zh-TW-390-blocked-readiness.png')});
  });
});
