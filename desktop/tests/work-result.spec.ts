import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workResultFixture, buildResultEnvelope, buildChangesResponse, buildDiffResponse, workspaceId, workId, route} from './work-result-fixture.ts';
import {workResultStrings} from '../src/pm-work-result-strings.js';
import {installBodyCompletedCounter, countCompletedBodies, syncCompletedBodies, syncHeldBodyCompletion} from './page-sync.ts';

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
      expect(activeFixture.state.changesCount).toBe(activeFixture.state.expectedChanges || 0);
      expect(activeFixture.state.diffCount).toBe(activeFixture.state.expectedDiff || 0);
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

    await installBodyCompletedCounter(page);
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

    // Event refresh: emit event again with same attempt/result, synced on completed body
    const beforeResultBodies = await countCompletedBodies(page, '/result');
    f.emitResultRecorded(attId, updatedEnvelope);
    await syncCompletedBodies(page, '/result', beforeResultBodies + 1);

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

    // Completeness partial pill & reasons (deduplicated against changes reasons: list left empty is not rendered, pill stays)
    const compDiv = resultSection.locator('.pm-work-result-completeness');
    await expect(compDiv).toBeVisible();
    await expect(compDiv.locator('.pm-work-result-completeness-reasons')).toHaveCount(0);

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

    await installBodyCompletedCounter(page);
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
    const beforeAttempts = await countCompletedBodies(page, '/attempts');
    f.state.attemptsFailWorkId = workId(1);
    releaseA();
    f.state.holdAttempts = null;
    await syncHeldBodyCompletion(page, '/attempts', beforeAttempts);

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

  test('14. Part B: 1. No call without a click', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId1 = 'att_' + '1'.repeat(32);
    const attId2 = 'att_' + '2'.repeat(32);

    f.state.attempts.push({
      attempt_id: attId2,
      workspace_id: workspaceId,
      work_item_id: workId(2),
      start_operation_id: 'op_' + '2'.repeat(32),
      stage: 'started',
      status: 'succeeded',
      session: {host: 'fixture-host', session_id: 'session-456'},
      created_at: 1791705600,
    });

    f.state.resultEnvelopes.set(attId1, buildResultEnvelope({attempt_id: attId1, summary_text: 'Summary Work 1'}));
    f.state.resultEnvelopes.set(attId2, buildResultEnvelope({attempt_id: attId2, summary_text: 'Summary Work 2'}));

    await installBodyCompletedCounter(page);
    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    // 1. Open Work 1, wait for Result
    const card1 = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    const card2 = page.locator(`[data-pm-kanban-work="${workId(2)}"]`);
    const inspector = page.locator('[data-work-inspector]');
    const resultSection = inspector.locator('[data-work-section="attempt-result"]');

    await card1.locator('[data-board-open]').click();
    await expect(resultSection).toBeVisible();
    await expect(resultSection.locator('.pm-work-result-summary-box')).toContainText('Summary Work 1');
    await expect(resultSection.locator('.pm-work-result-read-changes')).toBeVisible();

    // 2. Run an event refresh with sync point
    const beforeRefreshBodies = await countCompletedBodies(page, '/result');
    f.emitResultRecorded(attId1, buildResultEnvelope({attempt_id: attId1, summary_text: 'Summary Work 1'}));
    await syncCompletedBodies(page, '/result', beforeRefreshBodies + 1);

    // 3. Switch Works to Work 2 and come back to Work 1
    await card2.locator('[data-board-open]').click();
    await expect(resultSection.locator('.pm-work-result-summary-box')).toContainText('Summary Work 2');
    await card1.locator('[data-board-open]').click();
    await expect(resultSection.locator('.pm-work-result-summary-box')).toContainText('Summary Work 1');

    // Page-side sync before asserting: sync on latest completed /result body
    const finalResultBodies = await countCompletedBodies(page, '/result');
    expect(finalResultBodies).toBeGreaterThanOrEqual(2);

    // /changes and /diff stay at 0
    expect(f.state.changesCount).toBe(0);
    expect(f.state.diffCount).toBe(0);
  });

  test('15. Part B: 2. Read, complete', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);
    const headCommit = 'abcdef1234567890abcdef1234567890abcdef12';

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: headCommit,
      head_freshness: 'live',
      uncommitted: {
        count: 2,
        files: [
          {path: 'uncommitted/file1.ts', status: 'M'},
          {path: 'uncommitted/file2.ts', status: 'A'},
        ],
        evidence: 'bat_git_status',
        reason: null,
      },
      summary: {
        aggregation: 'per_commit',
        commits: [headCommit],
        commit_count: 1,
        files: [
          {path: 'src/app.ts', status: 'M', additions: 15, deletions: 3, commits_touched: [headCommit]},
        ],
        file_count: 1,
        additions: 15,
        deletions: 3,
        unavailable: [],
        complete: true,
        reason: null,
        reasons: [],
      },
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    await expect(resultSection).toBeVisible();

    // Check Part A summary text before read
    const partASummaryBefore = await resultSection.locator('.pm-work-result-changes > .pm-work-result-changes-summary').textContent();
    expect(partASummaryBefore).toContain('As of the last recorded turn');

    // Click "Read current changes"
    f.state.expectedChanges = 1;
    const readChangesBtn = resultSection.locator('.pm-work-result-read-changes');
    await readChangesBtn.click();

    // Check head (first 12 chars: abcdef123456)
    const headP = resultSection.locator('.pm-work-result-head');
    await expect(headP).toBeVisible();
    await expect(headP).toContainText('abcdef123456');

    // Check freshness: "Read from BAT just now"
    const freshnessP = resultSection.locator('.pm-work-result-freshness');
    await expect(freshnessP).toContainText('Read from BAT just now');

    // Check summary line (singular: 1 file changed in 1 commit, +15 −3)
    const currentChangesBlock = resultSection.locator('.pm-work-result-current-changes');
    const currentSummary = currentChangesBlock.locator('.pm-work-result-changes-summary');
    await expect(currentSummary).toContainText('1 file changed in 1 commit, +15 −3');
    expect(await currentSummary.textContent()).not.toContain('As of the last recorded turn');

    // Check rows in current changes
    const fileRow = currentChangesBlock.locator('.pm-work-result-file-row').first();
    await expect(fileRow).toContainText('src/app.ts');
    await expect(fileRow).toContainText('modified');
    await expect(fileRow).toContainText('+15 −3');

    // Check uncommitted paths and notice
    const uncommittedBox = currentChangesBlock.locator('.pm-work-result-uncommitted-box');
    await expect(uncommittedBox).toContainText('Uncommitted changes: 2');
    await expect(uncommittedBox).toContainText('uncommitted/file1.ts');
    await expect(uncommittedBox).toContainText('uncommitted/file2.ts');
    await expect(uncommittedBox).toContainText('BAT does not show diffs of uncommitted files.');

    // The Part A summary text does not change
    const partASummaryAfter = await resultSection.locator('.pm-work-result-changes > .pm-work-result-changes-summary').textContent();
    expect(partASummaryAfter).toBe(partASummaryBefore);
  });

  test('16. Part B: 3. Read, partial and unavailable', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);

    // 1. cached_receipt with partial reasons
    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: '1111222233334444555566667777888899990000',
      head_freshness: 'cached_receipt',
      completeness_status: 'partial',
      completeness_reasons: ['worktree_unreachable'],
      summary: {
        aggregation: 'per_commit',
        commits: ['1111222233334444555566667777888899990000'],
        commit_count: 1,
        files: [],
        file_count: 0,
        additions: 'unknown',
        deletions: 'unknown',
        unavailable: [],
        complete: false,
        reasons: [],
      },
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    const currentBlock = resultSection.locator('.pm-work-result-current-changes');
    // Check freshness: "BAT could not be read. Showing the last recorded turn."
    await expect(currentBlock.locator('.pm-work-result-freshness')).toContainText('BAT could not be read. Showing the last recorded turn.');
    // Check completeness pill & partial reason
    await expect(currentBlock.locator('.pm-work-result-completeness')).toContainText('The worktree is not reachable.');

    // 2. unavailable with live_head_commit: "unknown" and unknown uncommitted count
    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: 'unknown',
      head_freshness: 'unavailable',
      uncommitted: {
        count: 'unknown',
        files: 'unknown',
        evidence: 'unproven',
        reason: 'worktree_unreachable',
      },
      completeness_status: 'unavailable',
      completeness_reasons: ['worktree_unreachable'],
      summary: {
        aggregation: 'per_commit',
        commits: [],
        commit_count: 0,
        files: [],
        file_count: 0,
        additions: 'unknown',
        deletions: 'unknown',
        unavailable: [],
        complete: false,
        reasons: ['worktree_unreachable'],
      },
    });

    f.state.expectedChanges = 2;
    await currentBlock.locator('.pm-work-result-read-changes').click();

    // Head shows Unknown
    await expect(currentBlock.locator('.pm-work-result-head')).toContainText('Unknown');
    // Freshness: Not available
    await expect(currentBlock.locator('.pm-work-result-freshness')).toContainText('Not available');
    // Completeness: unavailable
    await expect(currentBlock.locator('.pm-work-result-completeness [data-status="unavailable"]')).toBeVisible();
    // Unknown uncommitted sentence & BAT diffs disclaimer
    const uncommittedBox = currentBlock.locator('.pm-work-result-uncommitted-box');
    await expect(uncommittedBox).toContainText('Unknown: BAT cannot prove the working tree has no uncommitted changes.');
    await expect(uncommittedBox).toContainText('BAT does not show diffs of uncommitted files.');
  });

  test('17. Part B: 4. Busy', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);

    f.state.changesResponse = buildChangesResponse({attempt_id: attId});

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    const readBtn = resultSection.locator('.pm-work-result-read-changes');

    // Hold /changes
    let releaseChanges: () => void = () => {};
    f.state.holdChanges = new Promise<void>(resolve => {
      releaseChanges = resolve;
    });

    // Click button to start read
    f.state.expectedChanges = 1;
    await readBtn.click();

    // While held: button keeps focus and has aria-disabled="true", block has aria-busy="true"
    await expect(readBtn).toHaveAttribute('aria-disabled', 'true');
    await expect(readBtn).toBeFocused();
    const currentBlock = resultSection.locator('.pm-work-result-current-changes');
    await expect(currentBlock).toHaveAttribute('aria-busy', 'true');

    // Clicking twice and pressing Enter make zero extra requests
    await readBtn.click({force: true});
    await readBtn.click({force: true});
    await readBtn.press('Enter');
    expect(f.state.changesCount).toBe(1);

    // Release /changes
    releaseChanges();
    f.state.holdChanges = null;

    // After release: aria-disabled is gone, aria-busy is gone, focus is still on the button
    await expect(readBtn).not.toHaveAttribute('aria-disabled', 'true');
    await expect(currentBlock).not.toHaveAttribute('aria-busy', 'true');
    await expect(readBtn).toBeFocused();
    expect(f.state.changesCount).toBe(1);
  });

  test('18. Part B: 5. Diff', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);
    const commitSha = 'abcdef1234567890abcdef1234567890abcdef12';
    const filePath = 'src/xss-test.ts';
    const filePath2 = 'src/second-file.ts';
    const payload = 'const x = "<img src=x onerror=alert(1)>";';

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commitSha,
      summary: {
        aggregation: 'per_commit',
        commits: [commitSha],
        commit_count: 1,
        files: [
          {path: filePath, status: 'M', additions: 1, deletions: 0, commits_touched: [commitSha]},
          {path: filePath2, status: 'A', additions: 5, deletions: 0, commits_touched: [commitSha]},
        ],
        file_count: 2,
        additions: 6,
        deletions: 0,
        unavailable: [],
        complete: true,
      },
    });

    f.state.diffResponses.set(`${commitSha}:${filePath}`, buildDiffResponse({
      attempt_id: attId,
      commit: commitSha,
      path: filePath,
      diff: `--- a/${filePath}\n+++ b/${filePath}\n@@ -1 +1 @@\n+${payload}\n`,
      stats: {additions: 1, deletions: 0},
    }));

    f.state.diffResponses.set(`${commitSha}:${filePath2}`, buildDiffResponse({
      attempt_id: attId,
      commit: commitSha,
      path: filePath2,
      diff: `--- /dev/null\n+++ b/${filePath2}\n@@ -0,0 +1,5 @@\n+// second file\n`,
      stats: {additions: 5, deletions: 0},
    }));

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    // Find the Show diff disclosure
    const diffDetails = resultSection.locator('.pm-work-result-diff-details').first();
    const diffSummary = diffDetails.locator('summary');
    await expect(diffSummary).toContainText('Show diff');
    await expect(diffSummary).toContainText('abcdef123456');

    // First open: makes exactly one request with exact commit and path
    f.state.expectedDiff = 1;
    await diffSummary.click();
    await expect(diffDetails).toHaveAttribute('open', '');

    // Diff shows textContent, never innerHTML; no <img> element exists
    const diffPre = diffDetails.locator('.pm-work-result-diff-pre');
    await expect(diffPre).toBeVisible();
    await expect(diffPre).toContainText(payload);
    const imgCount = await page.locator('.pm-work-result-diff-pre img').count();
    expect(imgCount).toBe(0);

    expect(f.state.diffRequests).toHaveLength(1);
    expect(f.state.diffRequests[0].commit).toBe(commitSha);
    expect(f.state.diffRequests[0].filePath).toBe(filePath);

    // Stats line: +A −D
    await expect(diffDetails.locator('.pm-work-result-diff-stats')).toContainText('+1 −0');

    // Region role & aria-label
    await expect(diffPre).toHaveAttribute('role', 'region');
    await expect(diffPre).toHaveAttribute('aria-label', `Diff for ${filePath} at abcdef123456`);

    // Second open: close and reopen makes no new request
    await diffSummary.click(); // close
    await expect(diffDetails).not.toHaveAttribute('open', '');
    await diffSummary.click(); // reopen
    await expect(diffDetails).toHaveAttribute('open', '');
    expect(f.state.diffCount).toBe(1);

    // Second file in same commit: clicking it makes one request for that file, previous diff stays open
    f.state.expectedDiff = 2;
    const diffDetails2 = resultSection.locator('.pm-work-result-diff-details').nth(1);
    await diffDetails2.locator('summary').click();
    await expect(diffDetails2).toHaveAttribute('open', '');
    await expect(diffDetails2.locator('.pm-work-result-diff-pre')).toBeVisible();
    await expect(diffDetails).toHaveAttribute('open', '');
    expect(f.state.diffCount).toBe(2);
  });

  test('19. Part B: 6. Diff notices', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);
    const commitSha = 'abcdef1234567890abcdef1234567890abcdef12';

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commitSha,
      summary: {
        aggregation: 'per_commit',
        commits: [commitSha],
        commit_count: 1,
        files: [
          {path: 'file1-truncated.ts', status: 'M', additions: 100, deletions: 50, commits_touched: [commitSha]},
          {path: 'file2-pipe-limit.ts', status: 'M', additions: 'unknown', deletions: 'unknown', commits_touched: [commitSha]},
          {path: 'file3-not-found.ts', status: 'M', additions: 1, deletions: 1, commits_touched: [commitSha]},
          {path: 'file4-retry.ts', status: 'M', additions: 2, deletions: 2, commits_touched: [commitSha]},
        ],
        file_count: 4,
        additions: 103,
        deletions: 53,
        unavailable: [],
        complete: true,
      },
    });

    // 1. Truncated
    f.state.diffResponses.set(`${commitSha}:file1-truncated.ts`, buildDiffResponse({
      commit: commitSha,
      path: 'file1-truncated.ts',
      truncated: true,
      diff: 'truncated diff content...',
    }));

    // 2. available: false
    f.state.diffResponses.set(`${commitSha}:file2-pipe-limit.ts`, buildDiffResponse({
      commit: commitSha,
      path: 'file2-pipe-limit.ts',
      available: false,
      reason: 'diff_exceeded_pipe_limit',
    }));

    // 3. 404 PM_DIFF_NOT_FOUND
    f.state.diffStatusByFile.set(`${commitSha}:file3-not-found.ts`, {
      status: 404,
      data: {error: {code: 'PM_DIFF_NOT_FOUND', message: 'Not found'}},
    });

    // 4. 502 PM_DIFF_UNAVAILABLE
    f.state.diffStatusByFile.set(`${commitSha}:file4-retry.ts`, {
      status: 502,
      data: {error: {code: 'PM_DIFF_UNAVAILABLE', message: 'Unavailable'}},
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    const disclosures = resultSection.locator('.pm-work-result-diff-details');

    // Check 1: Truncated
    f.state.expectedDiff = 1;
    await disclosures.nth(0).locator('summary').click();
    await expect(disclosures.nth(0).locator('.pm-work-result-diff-notice')).toContainText('Showing the first 256 KiB of this diff.');

    // Check 2: available: false with reason
    f.state.expectedDiff = 2;
    await disclosures.nth(1).locator('summary').click();
    await expect(disclosures.nth(1).locator('.pm-work-result-diff-notice')).toContainText('The diff exceeded the pipe buffer limit.');

    // Check 3: 404 PM_DIFF_NOT_FOUND - message shown, NO retry button
    f.state.expectedDiff = 3;
    await disclosures.nth(2).locator('summary').click();
    await expect(disclosures.nth(2).locator('.pm-work-result-diff-notice')).toContainText("This commit or file is no longer in this attempt's range. Read current changes again.");
    await expect(disclosures.nth(2).locator('.pm-work-result-retry')).toHaveCount(0);

    // Check 4: 502 then "Try again" (two requests in total)
    f.state.expectedDiff = 4;
    await disclosures.nth(3).locator('summary').click();
    await expect(disclosures.nth(3).locator('.pm-work-result-error-box')).toBeVisible();
    const retryBtn = disclosures.nth(3).locator('.pm-work-result-retry');
    await expect(retryBtn).toBeVisible();

    // Now clear the error so retry succeeds
    f.state.diffStatusByFile.delete(`${commitSha}:file4-retry.ts`);
    f.state.diffResponses.set(`${commitSha}:file4-retry.ts`, buildDiffResponse({
      commit: commitSha,
      path: 'file4-retry.ts',
      diff: 'success on retry',
    }));

    f.state.expectedDiff = 5;
    await retryBtn.click();
    await expect(disclosures.nth(3).locator('.pm-work-result-diff-pre')).toContainText('success on retry');
  });

  test('20. Part B: 7. Out of date', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);
    const commit1 = 'abcdef1234567890abcdef1234567890abcdef12';

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commit1,
      summary: {
        aggregation: 'per_commit',
        commits: [commit1],
        commit_count: 1,
        files: [{path: 'src/file.ts', status: 'M', additions: 1, deletions: 1, commits_touched: [commit1]}],
        file_count: 1,
        additions: 1,
        deletions: 1,
        complete: true,
      },
    });

    f.state.diffResponses.set(`${commit1}:src/file.ts`, buildDiffResponse({
      commit: commit1,
      path: 'src/file.ts',
      diff: 'loaded diff content',
    }));

    await installBodyCompletedCounter(page);
    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    // Read changes
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    // Open diff
    f.state.expectedDiff = 1;
    const diffDetails = resultSection.locator('.pm-work-result-diff-details');
    await diffDetails.locator('summary').click();
    await expect(diffDetails.locator('.pm-work-result-diff-pre')).toContainText('loaded diff content');

    // Event refresh with a changed envelope (e.g. state becomes process-ended)
    const changedEnv = buildResultEnvelope({
      attempt_id: attId,
      state: 'process-ended',
      stage: 'completed',
    });
    const beforeResultBodies = await countCompletedBodies(page, '/result');
    f.emitResultRecorded(attId, changedEnv);
    await syncCompletedBodies(page, '/result', beforeResultBodies + 1);

    // Shows "May be out of date" pill and "Read again" button
    const currentBlock = resultSection.locator('.pm-work-result-current-changes');
    const outOfDatePill = currentBlock.locator('[data-status="out-of-date"]');
    await expect(outOfDatePill).toContainText('May be out of date');
    const readAgainBtn = currentBlock.locator('.pm-work-result-read-again');
    await expect(readAgainBtn).toContainText('Read again');

    // Content and open diff are kept, changesCount is still 1
    await expect(diffDetails).toHaveAttribute('open', '');
    await expect(diffDetails.locator('.pm-work-result-diff-pre')).toContainText('loaded diff content');
    expect(f.state.changesCount).toBe(1);

    // Click "Read again": makes one request and clears loaded diffs
    f.state.expectedChanges = 2;
    await readAgainBtn.click();

    // Pill and "Read again" button disappear, "Read current changes" button returns
    await expect(currentBlock.locator('[data-status="out-of-date"]')).toHaveCount(0);
    await expect(currentBlock.locator('.pm-work-result-read-changes')).toBeVisible();

    // Diff disclosure was cleared/closed, opening it again fetches diff afresh
    f.state.expectedDiff = 2;
    const newDiffDetails = currentBlock.locator('.pm-work-result-diff-details');
    await expect(newDiffDetails).not.toHaveAttribute('open', '');
    await newDiffDetails.locator('summary').click();
    await expect(newDiffDetails).toHaveAttribute('open', '');
    await expect(newDiffDetails.locator('.pm-work-result-diff-pre')).toContainText('loaded diff content');
    expect(f.state.diffCount).toBe(2);
  });

  test('21. Part B: 8. Switch', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const att1 = 'att_' + '1'.repeat(32);
    const att2 = 'att_' + '2'.repeat(32);
    const commit1 = 'abcdef1234567890abcdef1234567890abcdef12';

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

    f.state.resultEnvelopes.set(att1, buildResultEnvelope({attempt_id: att1, summary_text: 'Summary Work 1'}));
    f.state.resultEnvelopes.set(att2, buildResultEnvelope({attempt_id: att2, summary_text: 'Summary Work 2'}));

    f.state.changesResponse = buildChangesResponse({
      attempt_id: att1,
      live_head_commit: commit1,
      summary: {
        commits: [commit1],
        files: [{path: 'work1-exclusive.ts', status: 'M', additions: 10, deletions: 2, commits_touched: [commit1]}],
      },
    });

    await installBodyCompletedCounter(page);
    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card1 = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    const card2 = page.locator(`[data-pm-kanban-work="${workId(2)}"]`);
    const inspector = page.locator('[data-work-inspector]');
    const attemptResult = inspector.locator('[data-work-section="attempt-result"]');

    // 1. Hold Work A's /changes
    let releaseChangesA: () => void = () => {};
    f.state.holdChanges = new Promise<void>(resolve => {
      releaseChangesA = resolve;
    });

    await card1.locator('[data-board-open]').click();
    await expect(attemptResult.locator('.pm-work-result-summary-box')).toContainText('Summary Work 1');

    // Click Read current changes on Work A
    f.state.expectedChanges = 1;
    await attemptResult.locator('.pm-work-result-read-changes').click();

    // 2. Switch to Work B
    await card2.locator('[data-board-open]').click();
    await expect(attemptResult.locator('.pm-work-result-summary-box')).toContainText('Summary Work 2');

    // 3. Release Work A's held /changes, synced via page-side counter
    const beforeChanges = await countCompletedBodies(page, '/changes');
    releaseChangesA();
    f.state.holdChanges = null;
    await syncHeldBodyCompletion(page, '/changes', beforeChanges);

    // Nothing from A renders in B, and B shows no "Current changes" (no head, no files from A)
    await expect(attemptResult.locator('.pm-work-result-head')).toHaveCount(0);
    await expect(attemptResult).not.toContainText('work1-exclusive.ts');

    // 4. Now test held /diff switch
    await card1.locator('[data-board-open]').click();
    await expect(attemptResult.locator('.pm-work-result-summary-box')).toContainText('Summary Work 1');

    // Read changes for A
    f.state.expectedChanges = 2;
    await attemptResult.locator('.pm-work-result-read-changes').click();
    await expect(attemptResult.locator('.pm-work-result-head')).toContainText('abcdef123456');

    // Hold /diff
    let releaseDiffA: () => void = () => {};
    f.state.holdDiff = new Promise<void>(resolve => {
      releaseDiffA = resolve;
    });

    f.state.diffResponses.set(`${commit1}:work1-exclusive.ts`, buildDiffResponse({
      commit: commit1,
      path: 'work1-exclusive.ts',
      diff: 'SECRET_DIFF_WORK_1',
    }));

    f.state.expectedDiff = 1;
    const diffSummary = attemptResult.locator('.pm-work-result-diff-details summary');
    await diffSummary.click();

    // Switch to Work B while A's /diff is in flight
    await card2.locator('[data-board-open]').click();
    await expect(attemptResult.locator('.pm-work-result-summary-box')).toContainText('Summary Work 2');

    // Release A's held diff, synced via page-side counter
    const beforeDiff = await countCompletedBodies(page, '/diff');
    releaseDiffA();
    f.state.holdDiff = null;
    await syncHeldBodyCompletion(page, '/diff', beforeDiff);

    // B has no diff from A
    await expect(attemptResult).not.toContainText('SECRET_DIFF_WORK_1');
  });

  test('22. Part B: 9. Keyboard', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);
    const commitSha = 'abcdef1234567890abcdef1234567890abcdef12';
    const filePath = 'src/app.ts';

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commitSha,
      summary: {
        commits: [commitSha],
        files: [{path: filePath, status: 'M', additions: 5, deletions: 2, commits_touched: [commitSha]}],
      },
    });

    f.state.diffResponses.set(`${commitSha}:${filePath}`, buildDiffResponse({
      commit: commitSha,
      path: filePath,
      diff: 'keyboard diff content',
    }));

    await installBodyCompletedCounter(page);
    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    const readBtn = resultSection.locator('.pm-work-result-read-changes');

    // Focus read button and press Enter
    await readBtn.focus();
    await expect(readBtn).toBeFocused();

    f.state.expectedChanges = 1;
    await page.keyboard.press('Enter');

    // Wait for changes to be read and rendered
    const diffSummary = resultSection.locator('.pm-work-result-diff-details summary');
    await expect(diffSummary).toBeVisible();
    await expect(readBtn).toBeFocused();

    // Tab reaches "Show diff" summary
    await page.keyboard.press('Tab');
    await expect(diffSummary).toBeFocused();

    // Space opens diff
    f.state.expectedDiff = 1;
    await page.keyboard.press('Space');
    const diffDetails = resultSection.locator('.pm-work-result-diff-details');
    await expect(diffDetails).toHaveAttribute('open', '');

    // Focus stays on summary after diff loads
    await expect(diffDetails.locator('.pm-work-result-diff-pre')).toBeVisible();
    await expect(diffSummary).toBeFocused();

    // Tab reaches diff region
    await page.keyboard.press('Tab');
    const diffRegion = resultSection.locator('.pm-work-result-diff-pre');
    await expect(diffRegion).toBeFocused();

    // Event re-render preserves focus on summary if focused
    await diffSummary.focus();
    await expect(diffSummary).toBeFocused();

    const beforeBodies = await countCompletedBodies(page, '/result');
    f.emitResultRecorded(attId, buildResultEnvelope({attempt_id: attId, state: 'turn-ended'}));
    await syncCompletedBodies(page, '/result', beforeBodies + 1);

    await expect(diffSummary).toBeFocused();
  });

  test('23. Part B: 10. zh-TW', async ({browser}) => {
    const context = await browser.newContext({locale: 'zh-TW', viewport: {width: 390, height: 844}});
    const page = await context.newPage();
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);
    const commitSha = 'abcdef1234567890abcdef1234567890abcdef12';
    const filePath = 'src/app.ts';

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commitSha,
      summary: {
        commits: [commitSha],
        files: [{path: filePath, status: 'M', additions: 5, deletions: 2, commits_touched: [commitSha]}],
      },
      uncommitted: {
        count: 1,
        files: [{path: 'uncommitted.ts', status: 'M'}],
      },
    });

    f.state.diffResponses.set(`${commitSha}:${filePath}`, buildDiffResponse({
      commit: commitSha,
      path: filePath,
      diff: 'chinese diff content',
      truncated: true,
    }));

    recordPageErrors(page);
    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('#workspace-menu').click();
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    f.state.expectedDiff = 1;
    const diffDetails = resultSection.locator('.pm-work-result-diff-details');
    await diffDetails.locator('summary').click();
    await expect(diffDetails.locator('.pm-work-result-diff-pre')).toBeVisible();

    const currentBlock = resultSection.locator('.pm-work-result-current-changes');

    // Check no stray English words outside allow-list
    const disallowed = await currentBlock.evaluate((el: HTMLElement) => {
      const allowed = new Set(['PR', 'BAT', 'COMMIT', 'SHA']);
      const clone = el.cloneNode(true) as HTMLElement;
      for (const node of clone.querySelectorAll('code, .mono, pre')) {
        node.remove();
      }
      const text = clone.innerText || '';
      const words = text.match(/[A-Za-z]+/g) || [];
      return words.filter(w => !allowed.has(w.toUpperCase()));
    });
    expect(disallowed, `Found unexpected Latin words in Current changes zh-TW: ${disallowed.join(', ')}`).toEqual([]);

    await context.close();
  });

  test('24. Part B: 11. Part A follow-ups', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);

    // State reason, changes reasons, and completeness reasons all give bat_unreachable
    f.state.currentEnvelope = buildResultEnvelope({
      attempt_id: attId,
      state: 'unknown',
      state_reason: 'bat_unreachable',
      changes_summary: {
        complete: false,
        files: [],
        reasons: ['bat_unreachable'],
      },
      completeness_status: 'unavailable',
      completeness_reasons: ['bat_unreachable'],
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');

    // Separator between Result links
    const linksDiv = resultSection.locator('.pm-work-result-links');
    await expect(linksDiv).toBeVisible();
    const linksText = await linksDiv.textContent();
    expect(linksText).toContain('Open live output · View operation');

    // Reason deduplication: bat_unreachable shows exactly once in the entire panel
    // State line displays it
    await expect(resultSection.locator('.pm-work-result-state ~ .pm-work-result-reason')).toContainText('BAT is not reachable.');

    // Changes reasons list does not render
    await expect(resultSection.locator('.pm-work-result-changes .pm-work-result-changes-reasons')).toHaveCount(0);

    // Completeness reasons list does not render, but the pill stays
    const compDiv = resultSection.locator('.pm-work-result-completeness');
    await expect(compDiv).toBeVisible();
    await expect(compDiv.locator('[data-status="unavailable"]')).toBeVisible();
    await expect(compDiv.locator('.pm-work-result-completeness-reasons')).toHaveCount(0);

    // In the entire resultSection, "BAT is not reachable." appears exactly once
    const allMatches = await resultSection.getByText('BAT is not reachable.').count();
    expect(allMatches).toBe(1);
  });

  test('25. Part B: 12. Commits ordered oldest first', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);
    const c1 = '1111111111111111111111111111111111111111';
    const c2 = '2222222222222222222222222222222222222222';
    const filePath = 'src/multi-commit.ts';

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: c2,
      summary: {
        aggregation: 'per_commit',
        commits: [c1, c2], // c1 is older, c2 is newer
        commit_count: 2,
        files: [
          {path: filePath, status: 'M', additions: 3, deletions: 1, commits_touched: [c1, c2]},
        ],
        file_count: 1,
        additions: 3,
        deletions: 1,
        complete: true,
      },
    });

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    const summaries = resultSection.locator('.pm-work-result-diff-details summary');
    await expect(summaries).toHaveCount(2);
    // Oldest commit c1 must appear before newer commit c2
    await expect(summaries.first()).toContainText('111111111111');
    await expect(summaries.nth(1)).toContainText('222222222222');
  });

  test('26. Part B: 13. Diff loading indicator in en and zh-TW', async ({browser}) => {
    // 1. English
    const contextEn = await browser.newContext({locale: 'en-US', viewport: {width: 1440, height: 900}});
    const pageEn = await contextEn.newPage();
    recordPageErrors(pageEn);
    const fEn = workResultFixture();
    activeFixture = fEn;
    const attId = 'att_' + '1'.repeat(32);
    const commitSha = 'abcdef1234567890abcdef1234567890abcdef12';
    const filePath = 'src/app.ts';

    fEn.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commitSha,
      summary: {
        commits: [commitSha],
        files: [{path: filePath, status: 'M', additions: 1, deletions: 1, commits_touched: [commitSha]}],
      },
    });

    let releaseDiffEn: () => void = () => {};
    fEn.state.holdDiff = new Promise<void>(resolve => { releaseDiffEn = resolve; });

    await mountConversation(pageEn, false, fEn.dispatch);
    await pageEn.goto(route);
    await pageEn.locator('[data-pm-view="board"]').click();

    const cardEn = pageEn.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await cardEn.locator('[data-board-open]').click();

    const resultSectionEn = pageEn.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    fEn.state.expectedChanges = 1;
    await resultSectionEn.locator('.pm-work-result-read-changes').click();

    fEn.state.expectedDiff = 1;
    const diffDetailsEn = resultSectionEn.locator('.pm-work-result-diff-details');
    await diffDetailsEn.locator('summary').click();

    const loadingEn = diffDetailsEn.locator('.pm-work-result-diff-loading');
    await expect(loadingEn).toBeVisible();
    await expect(loadingEn).toContainText('Loading diff…');
    expect(await loadingEn.textContent()).not.toContain('ui_loading');

    releaseDiffEn();
    fEn.state.holdDiff = null;
    await expect(diffDetailsEn.locator('.pm-work-result-diff-pre')).toBeVisible();
    await contextEn.close();

    // 2. zh-TW
    const contextZh = await browser.newContext({locale: 'zh-TW', viewport: {width: 1440, height: 900}});
    const pageZh = await contextZh.newPage();
    recordPageErrors(pageZh);
    const fZh = workResultFixture();
    activeFixture = fZh;

    fZh.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commitSha,
      summary: {
        commits: [commitSha],
        files: [{path: filePath, status: 'M', additions: 1, deletions: 1, commits_touched: [commitSha]}],
      },
    });

    let releaseDiffZh: () => void = () => {};
    fZh.state.holdDiff = new Promise<void>(resolve => { releaseDiffZh = resolve; });

    await mountConversation(pageZh, false, fZh.dispatch);
    await pageZh.goto(route);
    await pageZh.locator('[data-pm-view="board"]').click();

    const cardZh = pageZh.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await cardZh.locator('[data-board-open]').click();

    const resultSectionZh = pageZh.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    fZh.state.expectedChanges = 1;
    await resultSectionZh.locator('.pm-work-result-read-changes').click();

    fZh.state.expectedDiff = 1;
    const diffDetailsZh = resultSectionZh.locator('.pm-work-result-diff-details');
    await diffDetailsZh.locator('summary').click();

    const loadingZh = diffDetailsZh.locator('.pm-work-result-diff-loading');
    await expect(loadingZh).toBeVisible();
    await expect(loadingZh).toContainText('載入差異中…');
    expect(await loadingZh.textContent()).not.toContain('ui_loading');

    releaseDiffZh();
    fZh.state.holdDiff = null;
    await expect(diffDetailsZh.locator('.pm-work-result-diff-pre')).toBeVisible();
    await contextZh.close();
  });

  test('27. Part B: 14. Multiple open diffs (release order B then A, and A then B)', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);
    const commit = 'abcdef1234567890abcdef1234567890abcdef12';
    const fileA = 'src/fileA.ts';
    const fileB = 'src/fileB.ts';
    const fileC = 'src/fileC.ts';
    const fileD = 'src/fileD.ts';
    const keyA = `${commit}:${fileA}`;
    const keyB = `${commit}:${fileB}`;
    const keyC = `${commit}:${fileC}`;
    const keyD = `${commit}:${fileD}`;

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commit,
      summary: {
        commits: [commit],
        files: [
          {path: fileA, status: 'M', additions: 1, deletions: 0, commits_touched: [commit]},
          {path: fileB, status: 'M', additions: 2, deletions: 0, commits_touched: [commit]},
          {path: fileC, status: 'M', additions: 3, deletions: 0, commits_touched: [commit]},
          {path: fileD, status: 'M', additions: 4, deletions: 0, commits_touched: [commit]},
        ],
      },
    });

    f.state.diffResponses.set(keyA, buildDiffResponse({commit, path: fileA, diff: 'DIFF_CONTENT_A'}));
    f.state.diffResponses.set(keyB, buildDiffResponse({commit, path: fileB, diff: 'DIFF_CONTENT_B'}));
    f.state.diffResponses.set(keyC, buildDiffResponse({commit, path: fileC, diff: 'DIFF_CONTENT_C'}));
    f.state.diffResponses.set(keyD, buildDiffResponse({commit, path: fileD, diff: 'DIFF_CONTENT_D'}));

    // 1. First order: release B then A
    let releaseA: () => void = () => {};
    let releaseB: () => void = () => {};
    f.state.holdDiffByKey.set(keyA, new Promise<void>(resolve => { releaseA = resolve; }));
    f.state.holdDiffByKey.set(keyB, new Promise<void>(resolve => { releaseB = resolve; }));

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    const diffDetailsA = resultSection.locator('.pm-work-result-diff-details').first();
    const diffDetailsB = resultSection.locator('.pm-work-result-diff-details').nth(1);
    const diffDetailsC = resultSection.locator('.pm-work-result-diff-details').nth(2);
    const diffDetailsD = resultSection.locator('.pm-work-result-diff-details').nth(3);

    f.state.expectedDiff = 2;
    // Open A, then open B
    await diffDetailsA.locator('summary').click();
    await diffDetailsB.locator('summary').click();

    // Release B first
    releaseB();
    f.state.holdDiffByKey.delete(keyB);
    await expect(diffDetailsB.locator('.pm-work-result-diff-pre')).toContainText('DIFF_CONTENT_B');

    // Release A second (must not be stranded by B's ticket)
    releaseA();
    f.state.holdDiffByKey.delete(keyA);
    await expect(diffDetailsA.locator('.pm-work-result-diff-pre')).toContainText('DIFF_CONTENT_A');

    expect(f.state.diffCallsByFile.get(keyA)).toBe(1);
    expect(f.state.diffCallsByFile.get(keyB)).toBe(1);

    // 2. Second order: release C then D (forward order)
    let releaseC: () => void = () => {};
    let releaseD: () => void = () => {};
    f.state.holdDiffByKey.set(keyC, new Promise<void>(resolve => { releaseC = resolve; }));
    f.state.holdDiffByKey.set(keyD, new Promise<void>(resolve => { releaseD = resolve; }));

    f.state.expectedDiff = 4;
    // Open C, then open D
    await diffDetailsC.locator('summary').click();
    await diffDetailsD.locator('summary').click();

    // Release C first
    releaseC();
    f.state.holdDiffByKey.delete(keyC);
    await expect(diffDetailsC.locator('.pm-work-result-diff-pre')).toContainText('DIFF_CONTENT_C');

    // Release D second
    releaseD();
    f.state.holdDiffByKey.delete(keyD);
    await expect(diffDetailsD.locator('.pm-work-result-diff-pre')).toContainText('DIFF_CONTENT_D');

    expect(f.state.diffCallsByFile.get(keyC)).toBe(1);
    expect(f.state.diffCallsByFile.get(keyD)).toBe(1);
  });

  test('28. Part B: 15. Read again drops in-flight diff and leaves cache clean', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);
    const commit = 'abcdef1234567890abcdef1234567890abcdef12';
    const fileA = 'src/fileA.ts';
    const keyA = `${commit}:${fileA}`;

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commit,
      summary: {
        commits: [commit],
        files: [
          {path: fileA, status: 'M', additions: 1, deletions: 0, commits_touched: [commit]},
        ],
      },
    });

    f.state.diffResponses.set(keyA, buildDiffResponse({commit, path: fileA, diff: 'DIFF_CONTENT_A'}));

    await installBodyCompletedCounter(page);
    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    // Trigger out of date banner with changed envelope
    const changedEnv = buildResultEnvelope({
      attempt_id: attId,
      state: 'process-ended',
      stage: 'completed',
    });
    const beforeResultBodies = await countCompletedBodies(page, '/result');
    f.emitResultRecorded(attId, changedEnv);
    await syncCompletedBodies(page, '/result', beforeResultBodies + 1);

    const readAgainBtn = resultSection.locator('.pm-work-result-read-again');
    await expect(readAgainBtn).toBeVisible();

    // Hold diff read for fileA
    let releaseA: () => void = () => {};
    f.state.holdDiffByKey.set(keyA, new Promise<void>(resolve => { releaseA = resolve; }));

    // Open diff for fileA
    f.state.expectedDiff = 1;
    const diffDetails = resultSection.locator('.pm-work-result-diff-details').first();
    await diffDetails.locator('summary').click();

    // Click "Read again" while diff is in-flight
    f.state.expectedChanges = 2;
    await readAgainBtn.click();

    // Release the in-flight diff read; its reply should be dropped because diffGeneration changed
    releaseA();
    f.state.holdDiffByKey.delete(keyA);

    // After "Read again" completes, the "Read current changes" button returns
    await expect(resultSection.locator('.pm-work-result-read-changes')).toBeVisible();

    // Open the diff disclosure again: it must send a new /diff request instead of using stale cache
    f.state.expectedDiff = 2;
    const newDiffDetails = resultSection.locator('.pm-work-result-diff-details').first();
    await newDiffDetails.locator('summary').click();
    await expect(newDiffDetails.locator('.pm-work-result-diff-pre')).toContainText('DIFF_CONTENT_A');

    // Both the first and second open sent a /diff call
    expect(f.state.diffCallsByFile.get(keyA)).toBe(2);
  });

  test('29. Part B: 16. Stale /changes reply must not reset changesBusy for a newer read', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const att1 = 'att_' + '1'.repeat(32);
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

    f.state.resultEnvelopes.set(att2, buildResultEnvelope({
      attempt_id: att2,
      summary_text: 'Summary Work 2',
    }));

    let releaseChangesA: () => void = () => {};
    let releaseChangesB: () => void = () => {};
    f.state.holdChangesByAttemptId.set(att1, new Promise<void>(resolve => { releaseChangesA = resolve; }));
    f.state.holdChangesByAttemptId.set(att2, new Promise<void>(resolve => { releaseChangesB = resolve; }));

    await installBodyCompletedCounter(page);
    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card1 = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    const card2 = page.locator(`[data-pm-kanban-work="${workId(2)}"]`);
    const attemptResult = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');

    // 1. Open Work A, click Read current changes (held)
    await card1.locator('[data-board-open]').click();
    f.state.expectedChanges = 1;
    await attemptResult.locator('.pm-work-result-read-changes').click();

    // 2. Switch to Work B, click Read current changes (held)
    await card2.locator('[data-board-open]').click();
    await expect(attemptResult.locator('.pm-work-result-summary-box')).toContainText('Summary Work 2');
    f.state.expectedChanges = 2;
    const readBtnB = attemptResult.locator('.pm-work-result-read-changes');
    await readBtnB.click();
    await expect(readBtnB).toHaveAttribute('aria-disabled', 'true');

    // 3. Release A's read
    const beforeChanges = await countCompletedBodies(page, '/changes');
    releaseChangesA();
    f.state.holdChangesByAttemptId.delete(att1);
    await syncHeldBodyCompletion(page, '/changes', beforeChanges);

    // 4. B's button must still be busy: aria-disabled is true, clicks are ignored
    await expect(readBtnB).toHaveAttribute('aria-disabled', 'true');
    await readBtnB.click({force: true});
    await readBtnB.press('Enter');
    expect(f.state.changesRequests.filter(r => r.attempt_id === att2).length).toBe(1);

    // 5. Release B's read; finishes normally with one request
    releaseChangesB();
    f.state.holdChangesByAttemptId.delete(att2);
    await expect(attemptResult.locator('.pm-work-result-head')).toBeVisible();
    expect(f.state.changesRequests.filter(r => r.attempt_id === att2).length).toBe(1);
    expect(f.state.changesCount).toBe(2);
  });

  test('30. Part B: 17. Focus moves to successor when replacement button replaces its trigger', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);
    const commit = 'abcdef1234567890abcdef1234567890abcdef12';
    const fileA = 'src/fileA.ts';
    const keyA = `${commit}:${fileA}`;

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commit,
      summary: {
        commits: [commit],
        files: [
          {path: fileA, status: 'M', additions: 1, deletions: 0, commits_touched: [commit]},
        ],
      },
    });

    await installBodyCompletedCounter(page);
    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');

    // --- Control 1: Read again button ---
    // First read changes successfully
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    // Trigger out-of-date banner
    const changedEnv = buildResultEnvelope({
      attempt_id: attId,
      state: 'process-ended',
      stage: 'completed',
    });
    const beforeResultBodies = await countCompletedBodies(page, '/result');
    f.emitResultRecorded(attId, changedEnv);
    await syncCompletedBodies(page, '/result', beforeResultBodies + 1);

    const readAgainBtn = resultSection.locator('.pm-work-result-read-again');
    await expect(readAgainBtn).toBeVisible();

    // Focus Read again button and activate
    await readAgainBtn.focus();
    expect(await page.evaluate(() => document.activeElement?.classList.contains('pm-work-result-read-again'))).toBe(true);

    f.state.expectedChanges = 2;
    await readAgainBtn.click();

    // Wait for fresh read to complete and assert focus moved to successor ("Read current changes" button), not body
    const freshReadBtn = resultSection.locator('.pm-work-result-read-changes');
    await expect(freshReadBtn).toBeVisible();
    const active1 = await page.evaluate(() => ({
      tagName: document.activeElement?.tagName,
      className: document.activeElement?.className,
    }));
    expect(active1.tagName).toBe('BUTTON');
    expect(active1.className).toContain('pm-work-result-read-changes');

    // --- Control 2: Try again on failed /changes ---
    f.state.changesStatus = 500;
    f.state.expectedChanges = 3;
    await freshReadBtn.click();

    const retryChangesBtn = resultSection.locator('[data-work-result-retry="changes"]');
    await expect(retryChangesBtn).toBeVisible();

    // Case 2b: Try again fails again -> successor is the new "Try again" button
    await retryChangesBtn.focus();
    expect(await page.evaluate(() => document.activeElement?.getAttribute('data-work-result-retry'))).toBe('changes');

    f.state.expectedChanges = 4;
    await retryChangesBtn.click();
    await expect(resultSection.locator('[data-work-result-retry="changes"]')).toBeVisible();
    const active2b = await page.evaluate(() => ({
      tagName: document.activeElement?.tagName,
      retryAttr: document.activeElement?.getAttribute('data-work-result-retry'),
    }));
    expect(active2b.tagName).toBe('BUTTON');
    expect(active2b.retryAttr).toBe('changes');

    // Case 2a: Try again succeeds -> successor is "Read current changes"
    f.state.changesStatus = null;
    f.state.expectedChanges = 5;
    await resultSection.locator('[data-work-result-retry="changes"]').click();
    await expect(resultSection.locator('.pm-work-result-read-changes')).toBeVisible();
    const active2a = await page.evaluate(() => ({
      tagName: document.activeElement?.tagName,
      className: document.activeElement?.className,
    }));
    expect(active2a.tagName).toBe('BUTTON');
    expect(active2a.className).toContain('pm-work-result-read-changes');

    // --- Control 3: Try again on failed diff ---
    f.state.diffStatusByFile.set(keyA, {status: 500});
    f.state.expectedDiff = 1;
    const diffDetails = resultSection.locator('.pm-work-result-diff-details').first();
    const diffSummary = diffDetails.locator('summary');
    await diffSummary.click();

    const retryDiffBtn = diffDetails.locator(`[data-work-result-retry-diff="${keyA}"]`);
    await expect(retryDiffBtn).toBeVisible();

    // Focus diff retry button
    await retryDiffBtn.focus();
    expect(await page.evaluate(() => document.activeElement?.getAttribute('data-work-result-retry-diff'))).toBe(keyA);

    // Make diff succeed and click retry
    f.state.diffStatusByFile.delete(keyA);
    f.state.diffResponses.set(keyA, buildDiffResponse({commit, path: fileA, diff: 'DIFF_OK'}));
    f.state.expectedDiff = 2;
    await retryDiffBtn.click();

    // Wait for diff pre to appear and assert focus moved to diff summary, not body
    await expect(diffDetails.locator('.pm-work-result-diff-pre')).toContainText('DIFF_OK');
    const active3 = await page.evaluate(() => ({
      tagName: document.activeElement?.tagName,
      isSummary: document.activeElement?.tagName === 'SUMMARY',
    }));
    expect(active3.isSummary).toBe(true);
  });

  test('31. Part B: 18. Failed diff does not ask BAT again on re-render or re-open without Try again', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workResultFixture();
    activeFixture = f;
    const attId = 'att_' + '1'.repeat(32);
    const commit = 'abcdef1234567890abcdef1234567890abcdef12';
    const file404 = 'src/file404.ts';
    const file502 = 'src/file502.ts';
    const key404 = `${commit}:${file404}`;
    const key502 = `${commit}:${file502}`;

    f.state.changesResponse = buildChangesResponse({
      attempt_id: attId,
      live_head_commit: commit,
      summary: {
        commits: [commit],
        files: [
          {path: file404, status: 'M', additions: 1, deletions: 0, commits_touched: [commit]},
          {path: file502, status: 'M', additions: 2, deletions: 0, commits_touched: [commit]},
        ],
      },
    });

    f.state.diffStatusByFile.set(key404, {status: 404});
    f.state.diffStatusByFile.set(key502, {status: 502});

    await installBodyCompletedCounter(page);
    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const resultSection = page.locator('[data-work-inspector] [data-work-section="attempt-result"]');
    f.state.expectedChanges = 1;
    await resultSection.locator('.pm-work-result-read-changes').click();

    const diff404 = resultSection.locator('.pm-work-result-diff-details').first();
    const diff502 = resultSection.locator('.pm-work-result-diff-details').nth(1);

    // 1. Open 404 diff and 502 diff
    f.state.expectedDiff = 2;
    await diff404.locator('summary').click();
    await diff502.locator('summary').click();

    await expect(diff404.locator('.pm-work-result-diff-notice')).toBeVisible();
    await expect(diff502.locator('.pm-work-result-retry')).toBeVisible();
    expect(f.state.diffCallsByFile.get(key404)).toBe(1);
    expect(f.state.diffCallsByFile.get(key502)).toBe(1);

    // 2. Emit event refresh to re-render the panel
    const changedEnv = buildResultEnvelope({
      attempt_id: attId,
      state: 'process-ended',
      stage: 'completed',
    });
    const beforeResultBodies = await countCompletedBodies(page, '/result');
    f.emitResultRecorded(attId, changedEnv);
    await syncCompletedBodies(page, '/result', beforeResultBodies + 1);

    // Diff call count must NOT change after re-render
    expect(f.state.diffCallsByFile.get(key404)).toBe(1);
    expect(f.state.diffCallsByFile.get(key502)).toBe(1);

    // 3. Re-opening a failed diff without clicking Try again must NOT ask BAT again
    await diff502.locator(':scope > summary').click(); // close
    await diff502.locator(':scope > summary').click(); // re-open
    expect(f.state.diffCallsByFile.get(key502)).toBe(1);

    // 4. Click "Try again" on the 502 diff; only then does /diff get called again
    f.state.diffStatusByFile.delete(key502);
    f.state.diffResponses.set(key502, buildDiffResponse({commit, path: file502, diff: 'DIFF_RESOLVED'}));
    f.state.expectedDiff = 3;
    await diff502.locator('.pm-work-result-retry').click();
    await expect(diff502.locator('.pm-work-result-diff-pre')).toContainText('DIFF_RESOLVED');
    expect(f.state.diffCallsByFile.get(key502)).toBe(2);
  });
});





