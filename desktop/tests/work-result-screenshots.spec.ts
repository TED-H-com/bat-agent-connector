import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workResultFixture, buildResultEnvelope, workspaceId, workId, route} from './work-result-fixture.ts';
import * as path from 'path';
import * as fs from 'fs';

const SHOTS_DIR = process.env.BATC_WORK_RESULT_SCREENSHOTS || path.join(process.env.TMPDIR || '/tmp', 'shots');

test.describe('Work Result Screenshots', () => {
  test.beforeEach(() => {
    test.skip(!process.env.BATC_WORK_RESULT_SCREENSHOTS, 'Screenshots are opt-in via BATC_WORK_RESULT_SCREENSHOTS');
    fs.mkdirSync(SHOTS_DIR, {recursive: true});
  });

  // 1. en-1440-turn-ended.png: viewport 1440x2400 to show full inspector (Start above Result, static Result hidden)
  test('en-1440-turn-ended', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 2400});
    const f = workResultFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();

    const resultSection = inspector.locator('[data-work-section="attempt-result"]');
    await expect(resultSection).toBeVisible();
    await expect(resultSection.locator('.pm-work-result-state')).toContainText('Turn ended');

    // Built-in static Result is hidden
    await expect(inspector.locator('[data-work-section="result"]')).toBeHidden();

    await inspector.screenshot({path: path.join(SHOTS_DIR, 'en-1440-turn-ended.png')});
  });

  // 2. en-1440-partial.png: viewport 1440x900, en-US, partial changes envelope showing uncommitted warning and files list
  test('en-1440-partial', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);

    f.state.currentEnvelope = buildResultEnvelope({
      attempt_id: attId,
      state: 'turn-ended',
      uncommitted: {
        count: 'unknown',
        files: 'unknown',
        evidence: 'unproven',
        reason: 'bat_empty_status_not_clean_proof',
      },
      changes_summary: {
        aggregation: 'per_commit',
        base_commit: '1234567890abcdef1234567890abcdef12345678',
        head_commit: '2345678901abcdef2345678901abcdef23456789',
        commits: [
          {commit_sha: '2345678901abcdef2345678901abcdef23456789', message: 'feat: add session handler'},
        ],
        commit_count: 1,
        files: [
          {path: 'src/handler.py', status: 'M', additions: 15, deletions: 3, commits_touched: 1},
          {path: 'tests/test_handler.py', status: 'A', additions: 42, deletions: 0, commits_touched: 1},
        ],
        file_count: 2,
        additions: 57,
        deletions: 3,
        unavailable: false,
        complete: true,
      },
      completeness_status: 'partial',
      completeness_reasons: ['bat_empty_status_not_clean_proof'],
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
    await expect(resultSection.locator('.pm-work-result-state')).toContainText('Turn ended');

    const filesToggle = resultSection.locator('.pm-work-result-files summary');
    if (await filesToggle.isVisible()) {
      await filesToggle.click();
    }

    await resultSection.scrollIntoViewIfNeeded();
    await inspector.screenshot({path: path.join(SHOTS_DIR, 'en-1440-partial.png')});
  });

  // 3. zh-TW-390-turn-ended.png: viewport 390x844, zh-TW, turn-ended envelope
  test('zh-TW-390-turn-ended', async ({browser}) => {
    const context = await browser.newContext({locale: 'zh-TW', viewport: {width: 390, height: 844}});
    const page = await context.newPage();
    const f = workResultFixture();

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
    await expect(resultSection.locator('.pm-work-result-state')).toContainText('本輪已結束');

    await resultSection.scrollIntoViewIfNeeded();
    await inspector.screenshot({path: path.join(SHOTS_DIR, 'zh-TW-390-turn-ended.png')});
    await context.close();
  });

  // 4. zh-TW-390-unknown.png: viewport 390x844, zh-TW, realistic unknown/reason envelope when no turn end is recorded
  test('zh-TW-390-unknown', async ({browser}) => {
    const context = await browser.newContext({locale: 'zh-TW', viewport: {width: 390, height: 844}});
    const page = await context.newPage();
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);

    f.state.currentEnvelope = buildResultEnvelope({
      attempt_id: attId,
      state: 'unknown',
      state_reason: 'turn_boundary_not_collected',
      summary_text: null,
      results: [],
      self_reported: 'none',
      fixed_commit: null,
      head_commit: 'unknown',
      uncommitted: {
        count: 'unknown',
        files: 'unknown',
        evidence: 'unproven',
        reason: 'turn_boundary_not_collected',
      },
      changes_summary: {
        aggregation: 'per_commit',
        commits: [],
        files: [],
        additions: 'unknown',
        deletions: 'unknown',
        unavailable: [],
        complete: false,
        reason: 'turn_boundary_not_collected',
        cached_from_result_id: null,
      },
      completeness_status: 'partial',
      completeness_reasons: ['turn_boundary_not_collected'],
    });

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
    await expect(resultSection.locator('.pm-work-result-state')).toContainText('未知');

    await resultSection.scrollIntoViewIfNeeded();
    await inspector.screenshot({path: path.join(SHOTS_DIR, 'zh-TW-390-unknown.png')});
    await context.close();
  });
});
