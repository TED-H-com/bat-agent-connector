import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workResultFixture, buildResultEnvelope, buildChangesResponse, buildDiffResponse, workspaceId, workId, route} from './work-result-fixture.ts';
import {installBodyCompletedCounter, countCompletedBodies, syncCompletedBodies} from './page-sync.ts';
import * as path from 'path';
import * as fs from 'fs';

const SHOTS_DIR = process.env.BATC_WORK_RESULT_SCREENSHOTS;

test.describe('Work Result Screenshots', () => {
  test.beforeEach(() => {
    test.skip(!SHOTS_DIR, 'Screenshots are opt-in via BATC_WORK_RESULT_SCREENSHOTS');
    fs.mkdirSync(SHOTS_DIR!, {recursive: true});
  });

  // 1. en 1440 with "Current changes" read and one diff open
  test('en-1440-current-changes-diff', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 1600});
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);
    const commitSha = 'abcdef1234567890abcdef1234567890abcdef12';

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commitSha,
      head_freshness: 'live',
      summary: {
        aggregation: 'per_commit',
        commits: [commitSha],
        commit_count: 1,
        files: [
          {path: 'src/login.ts', status: 'M', additions: 8, deletions: 1, commits_touched: [commitSha]},
          {path: 'src/auth.ts', status: 'A', additions: 14, deletions: 0, commits_touched: [commitSha]},
        ],
        file_count: 2,
        additions: 22,
        deletions: 1,
        complete: true,
      },
    });

    f.state.diffResponses.set(`${commitSha}:src/login.ts`, buildDiffResponse({
      attempt_id: attId,
      commit: commitSha,
      path: 'src/login.ts',
      diff: `--- a/src/login.ts\n+++ b/src/login.ts\n@@ -10,3 +10,10 @@\n+export function validateLogin(username: string): boolean {\n+  return username.length > 0;\n+}\n`,
      stats: {additions: 8, deletions: 1},
    }));

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();

    const resultSection = inspector.locator('[data-work-section="attempt-result"]');
    await expect(resultSection).toBeVisible();

    // Read current changes
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    // Open first diff
    f.state.expectedDiff = 1;
    const diffDetails = resultSection.locator('.pm-work-result-diff-details').first();
    await diffDetails.locator('summary').click();
    await expect(diffDetails.locator('.pm-work-result-diff-pre')).toBeVisible();

    await resultSection.scrollIntoViewIfNeeded();
    await inspector.screenshot({path: path.join(SHOTS_DIR!, 'en-1440-current-changes-diff.png')});
  });

  // 2. zh-TW 390 with the same ("Current changes" read and one diff open)
  test('zh-TW-390-current-changes-diff', async ({browser}) => {
    const context = await browser.newContext({locale: 'zh-TW', viewport: {width: 390, height: 844}});
    const page = await context.newPage();
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);
    const commitSha = 'abcdef1234567890abcdef1234567890abcdef12';

    f.state.currentEnvelope = buildResultEnvelope({
      attempt_id: attId,
      summary_text: '已實作登入流程並驗證憑證處理。',
    });

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commitSha,
      head_freshness: 'live',
      summary: {
        aggregation: 'per_commit',
        commits: [commitSha],
        commit_count: 1,
        files: [
          {path: 'src/login.ts', status: 'M', additions: 8, deletions: 1, commits_touched: [commitSha]},
          {path: 'src/auth.ts', status: 'A', additions: 14, deletions: 0, commits_touched: [commitSha]},
        ],
        file_count: 2,
        additions: 22,
        deletions: 1,
        complete: true,
      },
    });

    f.state.diffResponses.set(`${commitSha}:src/login.ts`, buildDiffResponse({
      attempt_id: attId,
      commit: commitSha,
      path: 'src/login.ts',
      diff: `--- a/src/login.ts\n+++ b/src/login.ts\n@@ -10,3 +10,10 @@\n+export function validateLogin(username: string): boolean {\n+  return username.length > 0;\n+}\n`,
      stats: {additions: 8, deletions: 1},
    }));

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('#workspace-menu').click();
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();

    const resultSection = inspector.locator('[data-work-section="attempt-result"]');
    await expect(resultSection).toBeVisible();

    // Read current changes
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    // Open first diff
    f.state.expectedDiff = 1;
    const diffDetails = resultSection.locator('.pm-work-result-diff-details').first();
    await diffDetails.locator('summary').click();
    await expect(diffDetails.locator('.pm-work-result-diff-pre')).toBeVisible();

    await resultSection.scrollIntoViewIfNeeded();
    await inspector.screenshot({path: path.join(SHOTS_DIR!, 'zh-TW-390-current-changes-diff.png')});
    await context.close();
  });

  // 3. en 1440 partial (cached_receipt)
  test('en-1440-partial-cached', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 1600});
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);
    const commitSha = 'abcdef1234567890abcdef1234567890abcdef12';

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commitSha,
      head_freshness: 'cached_receipt',
      completeness_status: 'partial',
      completeness_reasons: ['worktree_unreachable'],
      summary: {
        aggregation: 'per_commit',
        commits: [commitSha],
        commit_count: 1,
        files: [
          {path: 'src/handler.py', status: 'M', additions: 15, deletions: 3, commits_touched: [commitSha]},
        ],
        file_count: 1,
        additions: 15,
        deletions: 3,
        unavailable: ['src/unreachable.py'],
        complete: false,
        reasons: ['bat_unreachable'],
      },
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();

    const resultSection = inspector.locator('[data-work-section="attempt-result"]');
    await expect(resultSection).toBeVisible();

    // Read current changes
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    const currentBlock = resultSection.locator('.pm-work-result-current-changes');
    await expect(currentBlock.locator('.pm-work-result-freshness')).toContainText('BAT could not be read. Showing the last recorded turn.');
    await expect(currentBlock.locator('.pm-work-result-completeness')).toBeVisible();

    await resultSection.scrollIntoViewIfNeeded();
    await inspector.screenshot({path: path.join(SHOTS_DIR!, 'en-1440-partial-cached.png')});
  });

  // 4. en 1440 out of date
  test('en-1440-out-of-date', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 1600});
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);
    const commitSha = 'abcdef1234567890abcdef1234567890abcdef12';

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commitSha,
      summary: {
        aggregation: 'per_commit',
        commits: [commitSha],
        commit_count: 1,
        files: [{path: 'src/file.ts', status: 'M', additions: 1, deletions: 1, commits_touched: [commitSha]}],
        file_count: 1,
        additions: 1,
        deletions: 1,
        complete: true,
      },
    });

    f.state.diffResponses.set(`${commitSha}:src/file.ts`, buildDiffResponse({
      commit: commitSha,
      path: 'src/file.ts',
      diff: 'loaded diff content for review',
    }));

    await installBodyCompletedCounter(page);
    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();

    const resultSection = inspector.locator('[data-work-section="attempt-result"]');

    // Read changes & open diff
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    f.state.expectedDiff = 1;
    const diffDetails = resultSection.locator('.pm-work-result-diff-details');
    await diffDetails.locator('summary').click();
    await expect(diffDetails.locator('.pm-work-result-diff-pre')).toBeVisible();

    // Event refresh with a changed envelope
    const changedEnv = buildResultEnvelope({
      attempt_id: attId,
      state: 'process-ended',
      stage: 'completed',
    });
    const beforeResultBodies = await countCompletedBodies(page, '/result');
    f.emitResultRecorded(attId, changedEnv);
    await syncCompletedBodies(page, '/result', beforeResultBodies + 1);

    const currentBlock = resultSection.locator('.pm-work-result-current-changes');
    await expect(currentBlock.locator('[data-status="out-of-date"]')).toContainText('May be out of date');
    await expect(currentBlock.locator('.pm-work-result-read-again')).toBeVisible();

    await resultSection.scrollIntoViewIfNeeded();
    await inspector.screenshot({path: path.join(SHOTS_DIR!, 'en-1440-out-of-date.png')});
  });
});
