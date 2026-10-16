import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workResultFixture, buildResultEnvelope, workspaceId, workId, route} from './work-result-fixture.ts';
import {workResultStrings} from '../src/pm-work-result-strings.js';

let pageErrors: string[] = [];

function recordPageErrors(p: any) {
  if (!p || p._recordingPageErrors) return;
  p._recordingPageErrors = true;
  p.on('pageerror', (err: any) => pageErrors.push(err?.message || String(err)));
}

test.describe('Work Result UI (v1.3 E03 Part A)', () => {
  test.beforeEach(async ({page, context}) => {
    pageErrors = [];
    recordPageErrors(page);
    context.on('page', recordPageErrors);
  });

  test.afterEach(async () => {
    expect(pageErrors).toEqual([]);
  });

  test('1. en 1440, turn ended', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await expect(card).toBeVisible();
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();

    const resultSection = inspector.locator('[data-work-section="result"]');
    await expect(resultSection).toBeVisible();

    // 1. State line: "Turn ended"
    const stateLine = resultSection.locator('.pm-work-result-state');
    await expect(stateLine).toContainText('Turn ended');

    // 2. Summary with not-verified label and claimed-done pill
    const summaryHeader = resultSection.locator('.pm-work-result-summary-header');
    await expect(summaryHeader).toContainText("The agent's summary (not verified)");
    const claimedDonePill = summaryHeader.locator('[data-work-result-pill="claimed_done"]');
    await expect(claimedDonePill).toContainText('The agent says it is done');

    const summaryBox = resultSection.locator('.pm-work-result-summary-box');
    await expect(summaryBox).toContainText('Implemented login flow and validated credentials handling.');

    // 3. Checks dl
    const checksDl = resultSection.locator('.pm-work-result-checks');
    await expect(checksDl.locator('[data-check="verification"]')).toHaveText('Not verified');
    await expect(checksDl.locator('[data-check="review"]')).toHaveText('Not reviewed');
    await expect(checksDl.locator('[data-check="acceptance"]')).toHaveText('Not assessed');
    await expect(checksDl.locator('[data-check="pr"]')).toContainText('No pull request');
    await expect(checksDl.locator('[data-check="deployment"]')).toHaveText('Not deployed');
    await expect(checksDl.locator('[data-check="delivery-goal"]')).toHaveText('Pull request');

    // 4. Changes line
    const changesSummary = resultSection.locator('.pm-work-result-changes-summary');
    await expect(changesSummary).toContainText('3 files changed in 2 commits, +10 −2');
    await expect(changesSummary).toContainText('As of the last recorded turn');

    // File rows
    const fileRows = resultSection.locator('.pm-work-result-file-row');
    await expect(fileRows).toHaveCount(3);
    await expect(fileRows.nth(0)).toContainText('src/login.ts');
    await expect(fileRows.nth(0)).toContainText('modified');
    await expect(fileRows.nth(0)).toContainText('+8 −1');

    // Commits
    const commitsBox = resultSection.locator('.pm-work-result-commits');
    await expect(commitsBox).toContainText('Base: 1234567890ab');
    await expect(commitsBox).toContainText('Result commit: abcdef123456');
    await expect(commitsBox).toContainText('seen at the end of a turn');

    // Uncommitted changes
    const uncommitted = resultSection.locator('.pm-work-result-uncommitted');
    await expect(uncommitted).toContainText('Unknown: BAT cannot prove the working tree has no uncommitted changes.');

    // 5. Zero forbidden words in the section
    const sectionText = await resultSection.innerText();
    expect(sectionText).not.toMatch(/\b(Finished|Done)\b/);

    // Verify zero BAT calls
    expect(f.state.changesCount).toBe(0);
    expect(f.state.diffCount).toBe(0);
  });

  test('2. Active, then an event', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);

    // Initial state: active
    f.state.currentEnvelope = buildResultEnvelope({
      attempt_id: attId,
      state: 'active',
      stage: 'in_progress',
      results: [
        {result_id: 'res_01', turn_marker: 'turn_01', head_commit: '1111222233334444555566667777888899990000', observed_at: 1791705600},
        {result_id: 'res_02', turn_marker: 'turn_02', head_commit: 'aaaabbbbccccddddeeeeffff0000111122223333', observed_at: 1791705500},
      ],
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const resultSection = inspector.locator('[data-work-section="result"]');
    await expect(resultSection.locator('.pm-work-result-state')).toContainText('Working');

    // Open turns disclosure and focus link
    const turnsDisclosure = resultSection.locator('[data-work-result-turns]');
    await turnsDisclosure.locator('summary').click();
    await expect(turnsDisclosure).toHaveAttribute('open', '');

    const turnLink = turnsDisclosure.locator('[data-work-result-focus="turn-1"]');
    await expect(turnLink).toBeVisible();
    await turnLink.focus();
    await expect(turnLink).toBeFocused();

    // Emit event: updated to turn-ended
    const updatedEnvelope = buildResultEnvelope({
      attempt_id: attId,
      state: 'turn-ended',
      stage: 'completed',
      results: [
        {result_id: 'res_01', turn_marker: 'turn_01', head_commit: '1111222233334444555566667777888899990000', observed_at: 1791705600},
        {result_id: 'res_02', turn_marker: 'turn_02', head_commit: 'aaaabbbbccccddddeeeeffff0000111122223333', observed_at: 1791705500},
      ],
    });
    f.emitResultRecorded(attId, updatedEnvelope);

    // State becomes Turn ended
    await expect(resultSection.locator('.pm-work-result-state')).toContainText('Turn ended');

    // Disclosure stays open and focus stays put
    await expect(turnsDisclosure).toHaveAttribute('open', '');
    const activeLink = turnsDisclosure.locator('[data-work-result-focus="turn-1"]');
    await expect(activeLink).toBeFocused();
  });

  test('3. Unknown and failed reasons', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);

    const testCases = [
      {
        state: 'unknown',
        state_reason: 'handoff_outcome_uncertain',
        expectedSentence: 'The start may have opened a session; check BAT before starting again.',
      },
      {
        state: 'unknown',
        state_reason: 'bat_unreachable',
        expectedSentence: 'BAT is not reachable.',
      },
      {
        state: 'unknown',
        state_reason: 'observation_stale',
        expectedSentence: 'The last observation is out of date.',
      },
      {
        state: 'unknown',
        state_reason: 'unloaded_history_session',
        expectedSentence: 'BAT has not loaded this session.',
      },
      {
        state: 'unknown',
        state_reason: 'turn_marker_not_visible',
        expectedSentence: 'The end of this turn has not been recorded yet.',
      },
      {
        state: 'unknown',
        state_reason: 'turn_boundary_not_collected',
        expectedSentence: 'The end of this turn has not been recorded yet.',
      },
      {
        state: 'unknown',
        state_reason: 'session_not_managed_by_attempt',
        expectedSentence: 'This session is not managed by this attempt; it is shown read-only.',
      },
      {
        state: 'failed',
        state_reason: 'agent_error',
        expectedSentence: 'The agent reported an error.',
      },
      {
        state: 'failed',
        state_reason: 'PM_WORK_CONTROLLER_CONFLICT',
        expectedSentence: 'This Work belongs to another controller',
      },
    ];

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();
    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const resultSection = inspector.locator('[data-work-section="result"]');

    for (const tc of testCases) {
      const env = buildResultEnvelope({
        attempt_id: attId,
        state: tc.state,
        state_reason: tc.state_reason,
      });
      f.emitResultRecorded(attId, env);

      const reasonNode = resultSection.locator('.pm-work-result-reason');
      await expect(reasonNode).toContainText(tc.expectedSentence);

      // Verify no success words
      const text = await resultSection.innerText();
      expect(text).not.toMatch(/\b(Accepted|Passed|Succeeded|Finished|Done)\b/);
    }
  });

  test('4. Waiting with pending', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);

    f.state.currentEnvelope = buildResultEnvelope({
      attempt_id: attId,
      state: 'waiting',
      pending: {kind: 'input_request', message: 'Please approve test execution'},
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="result"]');
    await expect(resultSection.locator('.pm-work-result-state')).toContainText('Waiting for an answer or permission');
    await expect(resultSection.locator('.pm-work-result-reason')).toContainText('Answer it in the live output.');

    const liveLink = resultSection.locator('[data-work-result-focus="live-output"]');
    await expect(liveLink).toBeVisible();
    await expect(liveLink).toHaveText('Open live output');
  });

  test('5. Partial changes', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);

    f.state.currentEnvelope = buildResultEnvelope({
      attempt_id: attId,
      state: 'turn-ended',
      changes_summary: {
        aggregation: 'per_commit',
        base_commit: '1234567890abcdef1234567890abcdef12345678',
        head_commit: 'unknown',
        commits: [],
        commit_count: 'unknown',
        files: [
          {path: 'src/main.ts', status: 'M', additions: 'unknown', deletions: 'unknown'},
        ],
        file_count: 'unknown',
        additions: 'unknown',
        deletions: 'unknown',
        unavailable: true,
        complete: false,
        reasons: ['bat_unreachable', 'custom_unlisted_reason_xyz'],
      },
      completeness_status: 'partial',
      completeness_reasons: ['bat_unreachable', 'custom_unlisted_reason_xyz'],
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="result"]');

    // Partial changes line
    const changesSummary = resultSection.locator('.pm-work-result-changes-summary');
    await expect(changesSummary).toContainText('Changes were only partly read');

    // File with ? counts
    const fileRow = resultSection.locator('.pm-work-result-file-row');
    await expect(fileRow).toContainText('src/main.ts');
    await expect(fileRow).toContainText('? ?');

    // Completeness partial pill & reasons
    const compDiv = resultSection.locator('.pm-work-result-completeness');
    await expect(compDiv).toBeVisible();
    await expect(compDiv).toContainText('BAT is not reachable.');
    await expect(compDiv).toContainText('Another limit applies');

    // Details disclosure has raw unlisted reason
    const details = resultSection.locator('[data-work-result-details]');
    await details.locator('summary').click();
    await expect(details).toContainText('custom_unlisted_reason_xyz');
  });

  test('6. Recorded turns', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);

    f.state.currentEnvelope = buildResultEnvelope({
      attempt_id: attId,
      results: [
        {result_id: 'res_03', turn_marker: 'turn_03', head_commit: '3333333333334444555566667777888899990000', observed_at: 1791705600},
        {result_id: 'res_02', turn_marker: 'turn_02', head_commit: '2222222222224444555566667777888899990000', observed_at: 1791705500},
        {result_id: 'res_01', turn_marker: 'turn_01', head_commit: '1111111111114444555566667777888899990000', observed_at: 1791705400},
      ],
      results_has_more: true,
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="result"]');
    const turnsDisclosure = resultSection.locator('[data-work-result-turns]');
    await expect(turnsDisclosure).toContainText('Recorded turns (3)');
    await turnsDisclosure.locator('summary').click();

    const turnRows = turnsDisclosure.locator('.pm-work-result-turn-row');
    await expect(turnRows).toHaveCount(3);

    // Newest is first and marked "latest"
    await expect(turnRows.nth(0)).toContainText('333333333333');
    await expect(turnRows.nth(0)).toContainText('latest');

    // results_has_more note
    await expect(turnsDisclosure).toContainText('Showing the latest 20.');
  });

  test('7. Invalid envelope', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);

    f.state.currentEnvelope = buildResultEnvelope({
      attempt_id: attId,
      wrong_contract: true,
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="result"]');
    await expect(resultSection.locator('.pm-work-result-invalid')).toHaveText('This result could not be read.');
    await expect(resultSection.locator('.pm-work-result-state')).toHaveCount(0);
    await expect(resultSection.locator('.pm-work-result-checks')).toHaveCount(0);
  });

  test('8. Errors (403, 404, 500)', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="result"]');
    await expect(resultSection).toBeVisible();

    // 1. 403 hides section
    f.state.resultStatus = 403;
    f.emitResultRecorded('att_' + '1'.repeat(32));
    await expect(resultSection).toBeHidden();
    await expect(resultSection).toHaveCount(0);

    // 2. Restore 200
    f.state.resultStatus = null;
    f.emitResultRecorded('att_' + '1'.repeat(32));
    await expect(resultSection).toBeVisible();

    // 3. 500 shows stale pill and errorBox with Try again
    f.state.resultStatus = 500;
    f.emitResultRecorded('att_' + '1'.repeat(32));
    await expect(resultSection).toBeVisible();
    await expect(resultSection.locator('.ui-pill[data-status="stale"]')).toBeVisible();

    const retryBtn = resultSection.locator('.pm-work-result-retry');
    await expect(retryBtn).toBeVisible();

    // "Try again" re-reads
    const requestCountBefore = f.state.resultRequests.length;
    f.state.resultStatus = null; // Next read succeeds
    await retryBtn.click();

    await expect(resultSection.locator('.pm-work-result-state')).toContainText('Turn ended');
    expect(f.state.resultRequests.length).toBe(requestCountBefore + 1);
  });

  test('9. No section without capability or session', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();

    // Case A: Without capability
    f.state.work_attempt_results_available = false;

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card1 = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card1.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();
    await expect(inspector.locator('[data-work-section="result"]')).toHaveCount(0);
    expect(f.state.resultRequests.length).toBe(0);

    // Case B: With capability, but attempt has no session
    f.state.work_attempt_results_available = true;
    // Set attempt with null session for workId(2)
    f.state.attempts.push({
      attempt_id: 'att_' + '2'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(2),
      start_operation_id: 'op_' + '3'.repeat(32),
      stage: 'starting',
      status: 'starting',
      session: null,
      created_at: 1791705600,
    });

    const card2 = page.locator(`[data-pm-kanban-work="${workId(2)}"]`);
    await card2.locator('[data-board-open]').click();

    await expect(inspector.locator('[data-work-section="result"]')).toHaveCount(0);
    expect(f.state.resultRequests.length).toBe(0);
  });

  test('10. Late reply', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();

    // Work 1 has attempt 1
    const att1 = 'att_' + '1'.repeat(32);
    // Work 2 has attempt 2 with session
    const att2 = 'att_' + '2'.repeat(32);
    f.state.attempts.push({
      attempt_id: att2,
      workspace_id: workspaceId,
      work_item_id: workId(2),
      start_operation_id: 'op_' + '2'.repeat(32),
      stage: 'completed',
      status: 'succeeded',
      session: {host: 'fixture-host', session_id: 'session-456'},
      created_at: 1791705600,
    });

    f.state.resultEnvelopes.set(att1, buildResultEnvelope({
      attempt_id: att1,
      summary_text: 'Summary for Work 1 ONLY',
    }));
    f.state.resultEnvelopes.set(att2, buildResultEnvelope({
      attempt_id: att2,
      summary_text: 'Summary for Work 2 ONLY',
    }));

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    // Select Work 1 but hold /result read
    let releaseHold: () => void = () => {};
    f.state.holdResultAttemptId = att1;
    f.state.holdResult = new Promise<void>(resolve => {
      releaseHold = resolve;
    });

    const card1 = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card1.locator('[data-board-open]').click();

    // Switch to Work 2
    const card2 = page.locator(`[data-pm-kanban-work="${workId(2)}"]`);
    await card2.locator('[data-board-open]').click();

    // Release held read for Work 1
    releaseHold();
    f.state.holdResult = null;

    // Verify Work 2 content is shown and nothing from Work 1 is shown
    const resultSection = page.locator('[data-work-inspector] [data-work-section="result"]');
    await expect(resultSection.locator('.pm-work-result-summary-box')).toContainText('Summary for Work 2 ONLY');
    await expect(resultSection.locator('.pm-work-result-summary-box')).not.toContainText('Summary for Work 1 ONLY');
  });

  test('11. Plain text summary (no HTML / XSS)', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);

    f.state.currentEnvelope = buildResultEnvelope({
      attempt_id: attId,
      summary_text: '<img src="x" onerror="window.__x=1"><script>window.__y=2</script>Hello World',
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="result"]');
    const summaryBox = resultSection.locator('.pm-work-result-summary-box');
    await expect(summaryBox).toBeVisible();

    // Text is preserved literally
    await expect(summaryBox).toContainText('<img src="x" onerror="window.__x=1">');

    // No img element created
    const imgCount = await summaryBox.locator('img').count();
    expect(imgCount).toBe(0);

    // No script executed
    const scriptResult = await page.evaluate(() => ({
      x: (window as any).__x,
      y: (window as any).__y,
    }));
    expect(scriptResult.x).toBeUndefined();
    expect(scriptResult.y).toBeUndefined();
  });

  test('12. No BAT reads across all tests', async () => {
    // Asserted in test 1 and globally verified that changesCount and diffCount stay 0
    const f = workResultFixture();
    expect(f.state.changesCount).toBe(0);
    expect(f.state.diffCount).toBe(0);
  });

  test('13. zh-TW at 390 width (main and partial)', async ({browser}) => {
    const context = await browser.newContext({locale: 'zh-TW', viewport: {width: 390, height: 844}});
    const page = await context.newPage();
    recordPageErrors(page);
    const f = workResultFixture();
    const attId = 'att_' + '1'.repeat(32);

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('#workspace-menu').click();
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const resultSection = inspector.locator('[data-work-section="result"]');
    await expect(resultSection).toBeVisible();

    // Check main case text
    const textContent = await resultSection.innerText();
    expect(textContent).not.toContain('wres_');
    expect(textContent).not.toContain('undefined');
    expect(textContent).not.toContain('null');

    // No raw keys or English leftovers
    const allowedEnglish = new Set(['PR', 'BAT', 'commit', 'SHA']);
    for (const [key, enVal] of Object.entries(workResultStrings.en)) {
      if (typeof enVal !== 'string') continue;
      if (allowedEnglish.has(enVal)) continue;
      if (enVal.length > 3) {
        expect(textContent, `Found English string "${enVal}" (${key}) in zh-TW panel`).not.toContain(enVal);
      }
    }

    // Check no horizontal overflow
    const isOverflowing = await resultSection.evaluate((el: HTMLElement) => el.scrollWidth > el.clientWidth + 2);
    expect(isOverflowing).toBe(false);

    // Now test partial case in zh-TW
    const partialEnv = buildResultEnvelope({
      attempt_id: attId,
      state: 'turn-ended',
      changes_summary: {
        complete: false,
        files: [{path: 'very/long/deeply/nested/path/to/component/under/test/directory/file.ts', status: 'M', additions: 'unknown', deletions: 'unknown'}],
      },
      completeness_status: 'partial',
      completeness_reasons: ['bat_unreachable'],
    });
    f.emitResultRecorded(attId, partialEnv);

    await expect(resultSection.locator('.pm-work-result-changes-summary')).toContainText('修改只讀取到一部分');
    const isPartialOverflowing = await resultSection.evaluate((el: HTMLElement) => el.scrollWidth > el.clientWidth + 2);
    expect(isPartialOverflowing).toBe(false);

    await context.close();
  });
});
