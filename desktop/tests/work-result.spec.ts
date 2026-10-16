import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workResultFixture, buildResultEnvelope, workspaceId, workId, route} from './work-result-fixture.ts';
import {workResultStrings} from '../src/pm-work-result-strings.js';

let pageErrors: string[] = [];
let activeFixture: any = null;

function recordPageErrors(p: any) {
  if (!p || p._recordingPageErrors) return;
  p._recordingPageErrors = true;
  p.on('pageerror', (err: any) => pageErrors.push(err?.message || String(err)));
}

test.describe('Work Result UI (v1.3 E03 Part A)', () => {
  test.beforeEach(async ({page, context}) => {
    pageErrors = [];
    activeFixture = null;
    recordPageErrors(page);
    context.on('page', recordPageErrors);
  });

  test.afterEach(async () => {
    expect(pageErrors).toEqual([]);
    if (activeFixture) {
      expect(activeFixture.state.changesCount).toBe(0);
      expect(activeFixture.state.diffCount).toBe(0);
    }
  });

  test('1. en 1440, turn ended & section placement', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await expect(card).toBeVisible();
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();

    const resultSection = inspector.locator('[data-work-section="attempt-result"]');
    await expect(resultSection).toBeVisible();

    // Verify section order in the inspector DOM
    const sectionKeys = await inspector.locator('.ui-inspector-body > [data-work-section]').evaluateAll(nodes =>
      nodes.map(n => n.getAttribute('data-work-section')).filter(Boolean)
    );
    expect(sectionKeys).toEqual([
      'requirements',
      'status',
      'start',
      'attempt-result',
      'result',
      'blocker',
      'delivery',
      'details',
    ]);

    // Built-in static "Latest result" is hidden while attempt-result is visible
    const staticResultSection = inspector.locator('[data-work-section="result"]');
    await expect(staticResultSection).toBeHidden();

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
    const commitsBox = resultSection.locator('.pm-work-result-changes');
    await expect(commitsBox).toContainText('Base: 1234567890ab');
    await expect(commitsBox).toContainText('Result commit: abcdef123456');
    await expect(commitsBox).toContainText('seen at the end of a turn');

    // Uncommitted changes
    const uncommitted = resultSection.locator('.pm-work-result-uncommitted');
    await expect(uncommitted).toContainText('Unknown: BAT cannot prove the working tree has no uncommitted changes.');

    // 5. Zero forbidden words in the section
    const sectionText = await resultSection.innerText();
    expect(sectionText).not.toMatch(/\b(Finished|Done)\b/);
  });

  test('2. Active, then an event preserves focus and open disclosures', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
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
    const resultSection = inspector.locator('[data-work-section="attempt-result"]');
    await expect(resultSection.locator('.pm-work-result-state')).toContainText('Working');

    // Open turns disclosure and focus its summary element
    const turnsDisclosure = resultSection.locator('[data-work-result-turns]');
    const turnsSummary = turnsDisclosure.locator('summary');
    await turnsSummary.click();
    await expect(turnsDisclosure).toHaveAttribute('open', '');
    await turnsSummary.focus();
    await expect(turnsSummary).toBeFocused();

    // Check section order before event
    const sectionKeysBefore = await inspector.locator('.ui-inspector-body > [data-work-section]').evaluateAll(nodes =>
      nodes.map(n => n.getAttribute('data-work-section')).filter(Boolean)
    );

    // Emit event: updated to turn-ended
    const updatedEnvelope = buildResultEnvelope({
      attempt_id: attId,
      state: 'turn-ended',
      stage: 'started',
      results: [
        {result_id: 'res_01', turn_marker: 'turn_01', head_commit: '1111222233334444555566667777888899990000', observed_at: 1791705600},
        {result_id: 'res_02', turn_marker: 'turn_02', head_commit: 'aaaabbbbccccddddeeeeffff0000111122223333', observed_at: 1791705500},
      ],
    });
    f.emitResultRecorded(attId, updatedEnvelope);

    // State becomes Turn ended
    await expect(resultSection.locator('.pm-work-result-state')).toContainText('Turn ended');

    // Disclosure stays open and focus is preserved
    await expect(turnsDisclosure).toHaveAttribute('open', '');
    await expect(turnsSummary).toBeFocused();

    // Static result is still hidden
    await expect(inspector.locator('[data-work-section="result"]')).toBeHidden();

    // Result content is rendered correctly
    await expect(resultSection.locator('.pm-work-result-summary-box')).toContainText('Implemented login flow and validated credentials handling.');
    await expect(resultSection.locator('.pm-work-result-checks [data-check="verification"]')).toHaveText('Not verified');
    const turnRows = resultSection.locator('.pm-work-result-turn-row');
    await expect(turnRows).toHaveCount(2);
    await expect(turnRows.nth(0)).toContainText('111122223333');
    await expect(turnRows.nth(0)).toContainText('latest');
    await expect(turnRows.nth(1)).toContainText('aaaabbbbcccc');

    // Section order remains unchanged after event
    const sectionKeysAfter = await inspector.locator('.ui-inspector-body > [data-work-section]').evaluateAll(nodes =>
      nodes.map(n => n.getAttribute('data-work-section')).filter(Boolean)
    );
    expect(sectionKeysAfter).toEqual(sectionKeysBefore);

    // Event refresh: emit event again with same attempt/result
    f.emitResultRecorded(attId, updatedEnvelope);

    // After event refresh: section order remains identical
    const sectionKeysAfterRefresh = await inspector.locator('.ui-inspector-body > [data-work-section]').evaluateAll(nodes =>
      nodes.map(n => n.getAttribute('data-work-section')).filter(Boolean)
    );
    expect(sectionKeysAfterRefresh).toEqual(sectionKeysBefore);

    // Static result remains hidden
    await expect(inspector.locator('[data-work-section="result"]')).toBeHidden();

    // Result content remains identical after event refresh
    await expect(resultSection.locator('.pm-work-result-state')).toContainText('Turn ended');
    await expect(resultSection.locator('.pm-work-result-summary-box')).toContainText('Implemented login flow and validated credentials handling.');
    await expect(resultSection.locator('.pm-work-result-checks [data-check="verification"]')).toHaveText('Not verified');
    await expect(turnRows).toHaveCount(2);
    await expect(turnsDisclosure).toHaveAttribute('open', '');
  });

  test('3. Unknown and failed reasons', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
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
    const resultSection = inspector.locator('[data-work-section="attempt-result"]');

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

  test('4. Waiting with pending & needs attention', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
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

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    await expect(resultSection.locator('.pm-work-result-state')).toContainText('Waiting for an answer or permission');
    await expect(resultSection.locator('.pm-work-result-reason')).toContainText('Answer it in the live output.');

    const liveLink = resultSection.locator('[data-work-result-focus="live-output"]');
    await expect(liveLink).toBeVisible();
    await expect(liveLink).toHaveText('Open live output');

    // Test attention state
    const attentionEnv = buildResultEnvelope({
      attempt_id: attId,
      state: 'turn-ended',
      attention: {code: 'OP_PAUSED', message: 'Paused by user', resumable: true},
    });
    f.emitResultRecorded(attId, attentionEnv);

    await expect(resultSection.locator('.pm-work-result-attention')).toContainText('Needs attention: open the operation to resume or cancel it.');
    const opLink = resultSection.locator('[data-work-result-focus="operation"]');
    await expect(opLink).toBeVisible();
    await expect(opLink).toHaveText('View operation');
  });

  test('5. Partial changes & uncommitted warning', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
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

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');

    // Partial changes line
    const changesSummary = resultSection.locator('.pm-work-result-changes-summary');
    await expect(changesSummary).toContainText('Changes were only partly read');

    // Partial changes reasons list
    const changesReasons = resultSection.locator('.pm-work-result-changes-reasons');
    await expect(changesReasons).toBeVisible();
    await expect(changesReasons).toContainText('BAT is not reachable.');
    await expect(changesReasons).toContainText('Another limit applies');

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

  test('6. Singular forms and recorded turns', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);

    // Envelope with exactly 1 file, 1 commit, and 1 recorded turn
    f.state.currentEnvelope = buildResultEnvelope({
      attempt_id: attId,
      changes_summary: {
        aggregation: 'per_commit',
        base_commit: '1234567890abcdef1234567890abcdef12345678',
        head_commit: 'abcdef1234567890abcdef1234567890abcdef12',
        commits: [{commit_sha: 'abcdef1234567890abcdef1234567890abcdef12'}],
        commit_count: 1,
        files: [{path: 'src/single.ts', status: 'M', additions: 5, deletions: 1}],
        file_count: 1,
        additions: 5,
        deletions: 1,
        complete: true,
      },
      results: [
        {result_id: 'res_01', turn_marker: 'turn_01', head_commit: 'abcdef1234567890abcdef1234567890abcdef12', observed_at: 1791705600},
      ],
      results_has_more: false,
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');

    // Singular changes summary: "1 file changed in 1 commit, +5 −1"
    const changesSummary = resultSection.locator('.pm-work-result-changes-summary');
    await expect(changesSummary).toContainText('1 file changed in 1 commit, +5 −1');

    // Singular recorded turn: "Recorded turn (1)"
    const turnsDisclosure = resultSection.locator('[data-work-result-turns]');
    await expect(turnsDisclosure).toContainText('Recorded turn (1)');

    // Now emit 3 turns with results_has_more: true
    const multiEnv = buildResultEnvelope({
      attempt_id: attId,
      results: [
        {result_id: 'res_03', turn_marker: 'turn_03', head_commit: '3333333333334444555566667777888899990000', observed_at: 1791705600},
        {result_id: 'res_02', turn_marker: 'turn_02', head_commit: '2222222222224444555566667777888899990000', observed_at: 1791705500},
        {result_id: 'res_01', turn_marker: 'turn_01', head_commit: '1111111111114444555566667777888899990000', observed_at: 1791705400},
      ],
      results_has_more: true,
    });
    f.emitResultRecorded(attId, multiEnv);

    // Plural turns count: "Recorded turns (3)"
    await expect(turnsDisclosure).toContainText('Recorded turns (3)');
    await turnsDisclosure.locator('summary').click();

    const turnRows = turnsDisclosure.locator('.pm-work-result-turn-row');
    await expect(turnRows).toHaveCount(3);
    await expect(turnRows.nth(0)).toContainText('333333333333');
    await expect(turnRows.nth(0)).toContainText('latest');

    // Note for results_has_more
    await expect(turnsDisclosure).toContainText('Showing the latest 20.');
  });

  test('7. Invalid envelope', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
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

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    await expect(resultSection.locator('.pm-work-result-invalid')).toHaveText('This result could not be read.');
    await expect(resultSection.locator('.pm-work-result-state')).toHaveCount(0);
    await expect(resultSection.locator('.pm-work-result-checks')).toHaveCount(0);
  });

  test('8. Errors (403, 404, 500)', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const resultSection = inspector.locator('[data-work-section="attempt-result"]');
    const staticResultSection = inspector.locator('[data-work-section="result"]');

    await expect(resultSection).toBeVisible();
    await expect(staticResultSection).toBeHidden();

    // 1. 403 withdraws content and hides section, showing static result
    f.state.resultStatus = 403;
    f.emitResultRecorded('att_' + '1'.repeat(32));
    await expect(resultSection).toBeHidden();
    await expect(staticResultSection).toBeVisible();

    // 2. Restore 200: attempt-result shows again, static result hides
    f.state.resultStatus = null;
    f.emitResultRecorded('att_' + '1'.repeat(32));
    await expect(resultSection).toBeVisible();
    await expect(staticResultSection).toBeHidden();

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
    activeFixture = f;

    // Case A: Without capability
    f.state.work_attempt_results_available = false;

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card1 = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card1.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();
    // Static result is shown, attempt-result is not shown
    await expect(inspector.locator('[data-work-section="attempt-result"]')).toBeHidden();
    await expect(inspector.locator('[data-work-section="result"]')).toBeVisible();
    expect(f.state.resultRequests.length).toBe(0);

    // Case B: With capability, but attempt has no session
    f.state.work_attempt_results_available = true;
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

    await expect(inspector.locator('[data-work-section="attempt-result"]')).toBeHidden();
    await expect(inspector.locator('[data-work-section="result"]')).toBeVisible();
    expect(f.state.resultRequests.length).toBe(0);
  });

  test('10. Work switching & stale attempt failure must not withdraw current result', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;

    // Work 1 has attempt 1
    const att1 = 'att_' + '1'.repeat(32);
    // Work 2 has attempt 2 with session
    const att2 = 'att_' + '2'.repeat(32);
    f.state.attempts.push({
      attempt_id: att2,
      workspace_id: workspaceId,
      work_item_id: workId(2),
      start_operation_id: 'op_' + '2'.repeat(32),
      stage: 'started',
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

    const card1 = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    const card2 = page.locator(`[data-pm-kanban-work="${workId(2)}"]`);
    const inspector = page.locator('[data-work-inspector]');
    const attemptResult = inspector.locator('[data-work-section="attempt-result"]');
    const staticResult = inspector.locator('[data-work-section="result"]');

    // 1. Open Work A with its /attempts read held
    let releaseA: () => void = () => {};
    f.state.holdAttemptsWorkId = workId(1);
    f.state.holdAttempts = new Promise<void>(resolve => {
      releaseA = resolve;
    });
    await card1.locator('[data-board-open]').click();

    // 2. Switch to Work B and wait for B's Result
    await card2.locator('[data-board-open]').click();
    await expect(attemptResult).toBeVisible();
    await expect(attemptResult.locator('.pm-work-result-summary-box')).toContainText('Summary for Work 2 ONLY');
    await expect(staticResult).toBeHidden();

    // 3. Fail A's held read
    f.state.attemptsFailWorkId = workId(1);
    releaseA();
    f.state.holdAttempts = null;

    // 4. B's Result stays visible, with its content, and B's panel shows no error from A
    await expect(attemptResult).toBeVisible();
    await expect(attemptResult.locator('.pm-work-result-summary-box')).toContainText('Summary for Work 2 ONLY');
    await expect(inspector.locator('[data-work-section="start"] .error, [data-work-section="start"] [data-alert-kind="error"]')).toHaveCount(0);

    // Re-check "no new /result for A"
    const requestsForA = f.state.resultRequests.filter(r => r.attempt_id === att1).length;
    expect(requestsForA).toBe(0);
  });

  test('11. Plain text summary (no HTML / XSS)', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
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

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
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

  test('12. Missing and unlisted values show Unknown and route to Details', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);

    // Envelope with missing and unlisted facet values
    f.state.currentEnvelope = buildResultEnvelope({
      attempt_id: attId,
      state: 'turn-ended',
      execution: {
        state: 'turn-ended',
        stage: 'completed',
        operation_status: 'succeeded',
        state_facets: {
          self_reported: 'claimed_done',
          verification: 'unverified',
          // review_decision with custom unlisted kind
          review_decision: {kind: 'custom_reviewer_kind'},
          // acceptance with unlisted status
          acceptance: {status: 'in_limbo'},
          // merge with unlisted status
          merge: 'unheard_of_merge_status',
          // deployment with unlisted state
          deployment: {state: 'orbiting_mars', verified: false, is_current: null},
        },
      },
      delivery: {
        delivery_goal: 'custom_target_goal',
        pull_requests: [],
      },
      changes: {
        base_commit: '1234567890abcdef1234567890abcdef12345678',
        fixed_result_commit: null,
        uncommitted: {count: 'unknown', reason: 'bat_empty_status_not_clean_proof'},
        summary: {
          complete: true,
          commit_count: 1,
          file_count: 1,
          additions: 1,
          deletions: 0,
          files: [
            {path: 'src/odd.ts', status: 'Z', additions: 1, deletions: 0},
          ],
        },
      },
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    await expect(resultSection).toBeVisible();

    // Checks dl: unlisted or missing values show "Unknown"
    const checksDl = resultSection.locator('.pm-work-result-checks');
    await expect(checksDl.locator('[data-check="review"]')).toHaveText('Unknown');
    await expect(checksDl.locator('[data-check="acceptance"]')).toHaveText('Unknown');
    await expect(checksDl.locator('[data-check="pr"]')).toContainText('Unknown');
    await expect(checksDl.locator('[data-check="deployment"]')).toHaveText('Unknown');
    await expect(checksDl.locator('[data-check="delivery-goal"]')).toHaveText('Unknown');

    // File with unlisted status letter 'Z' shows "Unknown"
    const fileRow = resultSection.locator('.pm-work-result-file-row');
    await expect(fileRow).toContainText('src/odd.ts');
    await expect(fileRow).toContainText('Unknown');

    // Details disclosure routes raw values into unlisted facets & statuses rows
    const details = resultSection.locator('[data-work-result-details]');
    await details.locator('summary').click();
    await expect(details).toContainText('custom_reviewer_kind');
    await expect(details).toContainText('in_limbo');
    await expect(details).toContainText('unheard_of_merge_status');
    await expect(details).toContainText('orbiting_mars');
    await expect(details).toContainText('custom_target_goal');
    await expect(details).toContainText('src/odd.ts: Z');
  });

  test('13. zh-TW at 390 width (main and partial)', async ({browser}) => {
    const context = await browser.newContext({locale: 'zh-TW', viewport: {width: 390, height: 844}});
    const page = await context.newPage();
    recordPageErrors(page);
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);
    f.state.currentEnvelope = buildResultEnvelope({
      attempt_id: attId,
      summary_text: '已實作登入流程並驗證憑證處理。',
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('#workspace-menu').click();
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const resultSection = inspector.locator('[data-work-section="attempt-result"]');
    await expect(resultSection).toBeVisible();

    // Check main case text
    const textContent = await resultSection.innerText();
    expect(textContent).not.toContain('wres_');
    expect(textContent).not.toContain('undefined');
    expect(textContent).not.toContain('null');

    // Assert no Latin words outside allow-list (PR, BAT, commit, SHA), ignoring code, .mono, and summary box
    const getDisallowedLatinWords = async (locator: any) => {
      return await locator.evaluate((el: HTMLElement) => {
        const allowed = new Set(['PR', 'BAT', 'COMMIT', 'SHA']);
        const clone = el.cloneNode(true) as HTMLElement;
        for (const node of clone.querySelectorAll('code, .mono, .pm-work-result-summary-box')) {
          node.remove();
        }
        const text = clone.innerText || '';
        const words = text.match(/[A-Za-z]+/g) || [];
        return words.filter(w => !allowed.has(w.toUpperCase()));
      });
    };

    const disallowedWords = await getDisallowedLatinWords(resultSection);
    expect(disallowedWords, `Found unexpected Latin words in zh-TW panel: ${disallowedWords.join(', ')}`).toEqual([]);

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

    const partialDisallowedWords = await getDisallowedLatinWords(resultSection);
    expect(partialDisallowedWords, `Found unexpected Latin words in partial zh-TW panel: ${partialDisallowedWords.join(', ')}`).toEqual([]);

    await context.close();
  });
});
