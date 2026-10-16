import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workStartFixture, workspaceId, workId, route, revisionId} from './work-start-fixture.ts';
import {workStartStrings} from '../src/pm-work-start-strings.js';

const REFUSAL_CODES = [
  'PM_WORK_ATTEMPT_CHANGED',
  'PM_WORK_START_IN_PROGRESS',
  'PM_WORK_START_OUTCOME_UNPROVEN',
  'PM_WORK_START_NOT_READY',
  'PM_WORK_START_PREREQUISITES',
  'PM_WORK_CONTROLLER_CONFLICT',
  'WORK_ITEM_CHANGED',
  'PROJECT_CHANGED',
  'PM_PACKAGE_SCOPE_CHANGED',
  'PM_PACKAGE_VERSION_CONFLICT',
  'PM_WORK_BINDING_CHANGED',
  'PROJECT_REPOSITORY_CHANGED',
  'REPOSITORY_REF_CHANGED',
  'PM_ADOPTED_PLAN_REQUIRED',
  'PM_LIFECYCLE_STOPPED',
  'PM_HANDOFF_TOO_LARGE',
];

test.describe('Work Start UI (v1.3 P3)', () => {
  test('1. Happy path, en 1440', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await expect(card).toBeVisible();
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await expect(inspector).toBeVisible();

    // Verify header button is visible
    const headerBtn = inspector.locator('[data-work-start-open]');
    await expect(headerBtn).toBeVisible();
    await expect(headerBtn).toHaveText('Start work…');

    // Default instruction prefilled
    const instruction = inspector.locator('[data-work-start-instruction]');
    await expect(instruction).toBeVisible();
    await expect(instruction).toHaveValue('Implement login and authentication flow');

    // Clicking header button focuses instruction
    await headerBtn.click();
    await expect(instruction).toBeFocused();

    // Check readiness shows facts
    const checkBtn = inspector.locator('[data-work-start-check]');
    await expect(checkBtn).toBeVisible();
    await checkBtn.click();

    const facts = inspector.locator('[data-work-start-facts]');
    await expect(facts).toBeVisible();
    await expect(facts).toContainText('example/library · main · aaaaaaaaaaaa · fixture-host · fixture-workspace');
    await expect(facts).toContainText('Prepares a new work package');

    // Start operation
    const submitBtn = inspector.locator('[data-work-section="start"] [data-pm-action="work.start"] button[type="submit"]');
    await expect(submitBtn).toBeEnabled();
    await expect(submitBtn).toHaveText('Start work');
    await submitBtn.click();

    // Verify exact POST
    await expect.poll(() => f.state.writes.length).toBe(1);
    const write = f.state.writes[0];
    expect(write.method).toBe('POST');
    expect(write.path).toBe('/operations?wait=3');
    expect(write.body.action).toBe('work.start');
    expect(write.body.target).toEqual({workspace_id: workspaceId, work_item_id: workId(1)});
    expect(write.body.params).toEqual({
      instruction: 'Implement login and authentication flow',
      agent: 'claude',
      delivery: 'pull_request',
      code_target: {repository: 'example/library', host: 'fixture-host', workspace_id: 'fixture-workspace'},
    });
    expect(write.body.preconditions).toEqual({
      expected_work_item_fingerprint: 'c'.repeat(64),
      expected_work_version: 2,
      expected_binding_id: 'pmwb_' + '1'.repeat(32),
      expected_scope_revision: 'pmscope_' + '5'.repeat(32),
      expected_previous_package_id: null,
      expected_project_version: 1,
      repository_id: 7,
      binding_digest: 'b'.repeat(64),
      expected_source_sha: 'a'.repeat(40),
      expected_latest_attempt_id: null,
    });
    expect(typeof write.idempotency_key).toBe('string');
    expect(write.idempotency_key.length).toBeGreaterThan(0);

    // Progress words update through events to Started
    f.emitAttempt({
      attempt_id: 'att_' + '1'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      start_operation_id: 'op_' + '1'.repeat(32),
      stage: 'preparing',
      status: 'running',
      created_at: 1791705600,
    });

    const statusWord = inspector.locator('.pm-work-start-attempt-status strong');
    await expect(statusWord).toContainText('Preparing the work basis');

    // Advance to started with a session
    f.emitAttempt({
      attempt_id: 'att_' + '1'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      start_operation_id: 'op_' + '1'.repeat(32),
      stage: 'started',
      status: 'succeeded',
      session: {host: 'fixture-host', session_id: 'session-123'},
      created_at: 1791705600,
    });

    await expect(statusWord).toContainText('Started');

    // Live output and Open work links appear
    const liveLink = inspector.locator('[data-work-start-session]');
    const workLink = inspector.locator('[data-work-start-item]');
    await expect(liveLink).toBeVisible();
    await expect(liveLink).toHaveText('Open live output');
    await expect(workLink).toBeVisible();
    await expect(workLink).toHaveText('Open work');

    // Form and header button are gone after started
    const editor = inspector.locator('[data-work-start-editor]');
    await expect(editor).toBeHidden();
    await expect(inspector.locator('[data-work-start-open]')).toHaveCount(0);
  });

  test('2. Lost reply sends same key and identical body', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    f.state.lostReply = true;

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await inspector.locator('[data-work-start-check]').click();

    const submitBtn = inspector.locator('[data-work-section="start"] [data-pm-action="work.start"] button[type="submit"]');
    await expect(submitBtn).toBeEnabled();
    await submitBtn.click();

    // After 503, button changes to "Check original request"
    await expect(submitBtn).toHaveText('Check original request');
    await expect.poll(() => f.state.writes.length).toBe(1);
    expect(f.state.writes).toHaveLength(1);
    const originalKey = f.state.writes[0].idempotency_key;
    const originalBody = structuredClone(f.state.writes[0].body);

    // Click "Check original request"
    await submitBtn.click();
    await expect.poll(() => f.state.writes.length).toBe(2);

    expect(f.state.writes[1].idempotency_key).toBe(originalKey);
    expect(f.state.writes[1].body).toEqual(originalBody);
    expect(f.state.writes).toHaveLength(2);
    await expect(inspector.locator('[data-pm-saved]')).toBeVisible();
  });

  test('3. Stale preview disables Start until checked again', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const checkBtn = inspector.locator('[data-work-start-check]');
    const submitBtn = inspector.locator('[data-work-section="start"] [data-pm-action="work.start"] button[type="submit"]');
    const instruction = inspector.locator('[data-work-start-instruction]');
    const staleNotice = inspector.locator('[data-work-start-stale]');

    await checkBtn.click();
    await expect(submitBtn).toBeEnabled();
    await expect(staleNotice).toBeHidden();

    // Edit instruction -> becomes stale
    await instruction.fill('Modified instruction text');
    await expect(staleNotice).toBeVisible();
    await expect(submitBtn).toBeDisabled();

    // Check readiness again -> becomes enabled
    await checkBtn.click();
    await expect(staleNotice).toBeHidden();
    await expect(submitBtn).toBeEnabled();
  });

  test('4. Blocked readiness disables Start and displays check help', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    f.state.readinessStatus = 'blocked';

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await inspector.locator('[data-work-start-check]').click();

    const submitBtn = inspector.locator('[data-work-section="start"] [data-pm-action="work.start"] button[type="submit"]');
    await expect(submitBtn).toBeDisabled();

    const blockedCheck = inspector.locator('[data-readiness-check="acceptance:membership.login"]');
    await expect(blockedCheck).toBeVisible();
    await expect(blockedCheck).toContainText('Members can sign in');
    await expect(blockedCheck).toContainText('Each original acceptance condition needs a proposed method');
    await expect(blockedCheck).not.toContainText('Acceptance criteria not met');
  });

  test('5. Candidates dropdown allows selection and 0 candidates shows explanation', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    f.state.candidatesCount = 2;

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await inspector.locator('[data-work-start-check]').click();

    // Candidate selector appears
    const candidateSelect = inspector.locator('[data-work-start-candidate]');
    await expect(candidateSelect).toBeVisible();
    await expect(candidateSelect.locator('option')).toHaveCount(3); // placeholder + 2

    // Select second candidate
    await candidateSelect.selectOption('1');
    const facts = inspector.locator('[data-work-start-facts]');
    await expect(facts).toBeVisible();
    await expect(facts).toContainText('example/other');

    // Test 0 candidates
    f.state.candidatesCount = 0;
    await inspector.locator('[data-work-start-check]').click();
    const noCandidates = inspector.locator('[data-work-start-no-candidates]');
    await expect(noCandidates).toBeVisible();
    await expect(noCandidates).toHaveText('No configured repository matches this project.');
  });

  test('6. Bind needed (card 2) requires owner to bind and submits requirement_ids', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    f.state.role = 'contributor'; // Contributor first

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    // Card 2 is workId(3) which has plan_revision_id from older plan
    const card = page.locator(`[data-pm-kanban-work="${workId(3)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const submitBtn = inspector.locator('[data-work-section="start"] [data-pm-action="work.start"] button[type="submit"]');

    // Requirements checkboxes are shown
    const reqCheckboxes = inspector.locator('.pm-work-start-reqs input[type="checkbox"]');
    await expect(reqCheckboxes).toHaveCount(2);

    // Contributor sees owner required notice
    const disabledNotice = inspector.locator('[data-work-start-disabled]');
    await expect(disabledNotice).toBeVisible();
    await expect(disabledNotice).toContainText('Only a workspace owner can bind this Work to the current plan.');
    await expect(submitBtn).toBeDisabled();

    // Switch to owner
    f.state.role = 'owner';
    await page.reload();
    await page.locator('[data-pm-view="board"]').click();
    await page.locator(`[data-pm-kanban-work="${workId(3)}"] [data-board-open]`).click();

    const inspectorOwner = page.locator('[data-work-inspector]');
    await inspectorOwner.locator('[data-work-start-check]').click();

    const submitBtnOwner = inspectorOwner.locator('[data-work-section="start"] [data-pm-action="work.start"] button[type="submit"]');
    await expect(submitBtnOwner).toBeEnabled();
    await submitBtnOwner.click();

    await expect.poll(() => f.state.writes.length).toBe(1);
    expect(f.state.writes[0].body.params.requirement_ids).toEqual(['membership.login']);
    expect(f.state.writes).toHaveLength(1);
  });

  for (const refusalCode of REFUSAL_CODES) {
    test(`7. Admission refusal: ${refusalCode}`, async ({page}) => {
      await page.setViewportSize({width: 1440, height: 900});
      const f = workStartFixture();
      f.state.refusalCode = refusalCode;

      await mountConversation(page, false, f.dispatch);
      await page.goto(route);
      await page.locator('[data-pm-view="board"]').click();

      const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
      await card.locator('[data-board-open]').click();

      const inspector = page.locator('[data-work-inspector]');
      await inspector.locator('[data-work-start-check]').click();

      const submitBtn = inspector.locator('[data-work-section="start"] [data-pm-action="work.start"] button[type="submit"]');
      await submitBtn.click();

      // Localized error message shown
      const errorMsg = inspector.locator('[data-work-section="start"] form .error');
      await expect(errorMsg).toBeVisible();

      // Exactly one write
      await expect.poll(() => f.state.writes.length).toBe(1);
      expect(f.state.writes).toHaveLength(1);

      // Assert exact localized summary for each code: panel key for shared, global key for start-only
      const isShared = [
        'WORK_ITEM_CHANGED',
        'PROJECT_CHANGED',
        'PM_PACKAGE_SCOPE_CHANGED',
        'PM_PACKAGE_VERSION_CONFLICT',
        'PM_WORK_BINDING_CHANGED',
        'PROJECT_REPOSITORY_CHANGED',
        'REPOSITORY_REF_CHANGED',
        'PM_ADOPTED_PLAN_REQUIRED',
        'PM_LIFECYCLE_STOPPED',
        'PM_HANDOFF_TOO_LARGE',
      ].includes(refusalCode);
      const expectedSummary = isShared
        ? workStartStrings.en[`wst_error_${refusalCode}`]
        : workStartStrings.en[`error_code_${refusalCode}`];
      expect(expectedSummary).toBeTruthy();
      await expect(errorMsg).toContainText(expectedSummary);
      await expect(errorMsg).not.toContainText(`Operation refused with ${refusalCode}`);

      // "New request" button appears
      const resetBtn = inspector.locator('[data-work-section="start"] form').getByRole('button', {name: 'New request'});
      await expect(resetBtn).toBeVisible();
      await expect(resetBtn).toHaveText('New request');

      // Click "New request"
      await resetBtn.click();

      // Start remains disabled until readiness is checked again
      await expect(submitBtn).toBeDisabled();
      expect(f.state.writes).toHaveLength(1);
    });
  }

  test('8. Attempts from another controller', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    f.state.attempts = [{
      attempt_id: 'att_' + '2'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      start_operation_id: 'op_' + '2'.repeat(32),
      stage: 'starting',
      status: 'running',
      created_at: 1791705600,
    }];

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');

    // In progress: no Start form, operation link shown
    await expect(inspector.locator('[data-work-start-editor]')).toBeHidden();
    const opLink = inspector.locator('a[href="#/op/op_' + '2'.repeat(32) + '"]');
    await expect(opLink).toBeVisible();
    await expect(opLink).toHaveText('View operation');

    // Move to needs_attention
    f.emitAttempt({
      attempt_id: 'att_' + '2'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      start_operation_id: 'op_' + '2'.repeat(32),
      stage: 'starting',
      status: 'needs_attention',
      created_at: 1791705600,
    });
    await expect(inspector.locator('.pm-work-start-attempt-status')).toContainText('Needs attention: open the operation to resume or cancel it.');

    // Move to start_unknown: assert no editor and no header button
    f.emitAttempt({
      attempt_id: 'att_' + '2'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      start_operation_id: 'op_' + '2'.repeat(32),
      stage: 'start_unknown',
      status: 'needs_attention',
      created_at: 1791705600,
    });
    await expect(inspector.locator('.pm-work-start-attempt')).toContainText("Check this Work's sessions on BAT before starting again.");
    await expect(inspector.locator('[data-work-start-editor]')).toBeHidden();
    await expect(inspector.locator('[data-work-start-open]')).toHaveCount(0);

    const attemptRow = inspector.locator('.pm-work-start-attempt');

    async function assertExclusivelyInDetails(reasonText: string) {
      const details = attemptRow.locator('details');
      await expect(details).toBeVisible();
      if (!(await details.evaluate((el: HTMLDetailsElement) => el.open))) {
        await details.locator('summary').click();
      }
      await expect(details).toContainText(reasonText);

      const outsideDetails = await attemptRow.evaluate((el: HTMLElement) => {
        const clone = el.cloneNode(true) as HTMLElement;
        clone.querySelector('details')?.remove();
        return clone.textContent || '';
      });
      expect(outsideDetails).not.toContain(reasonText);
    }

    // Realistic start_failed: null error_code shows generic start-failed sentence
    const nullReason = 'Internal failure without code';
    f.emitAttempt({
      attempt_id: 'att_' + '3'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      start_operation_id: 'op_' + '3'.repeat(32),
      stage: 'start_failed',
      status: 'failed',
      error_code: null,
      status_reason: nullReason,
      created_at: 1791705600,
    });
    await expect(attemptRow).toContainText(workStartStrings.en.wst_start_failed_generic);
    await assertExclusivelyInDetails(nullReason);

    // Realistic start_failed: unknown error_code shows generic start-failed sentence
    const unknownCodeReason = 'Internal failure with unknown code';
    f.emitAttempt({
      attempt_id: 'att_' + '4'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      start_operation_id: 'op_' + '4'.repeat(32),
      stage: 'start_failed',
      status: 'failed',
      error_code: 'UNKNOWN_FUTURE_CODE',
      status_reason: unknownCodeReason,
      created_at: 1791705600,
    });
    await expect(attemptRow).toContainText(workStartStrings.en.wst_start_failed_generic);
    await assertExclusivelyInDetails(unknownCodeReason);

    // Realistic start_failed: PM_HANDOFF_FAILED (global message)
    const handoffFailedReason = 'Agent connection dropped during handoff';
    f.emitAttempt({
      attempt_id: 'att_' + '5'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      start_operation_id: 'op_' + '5'.repeat(32),
      stage: 'start_failed',
      status: 'failed',
      error_code: 'PM_HANDOFF_FAILED',
      status_reason: handoffFailedReason,
      created_at: 1791705600,
    });
    await expect(attemptRow).toContainText(workStartStrings.en.error_code_PM_HANDOFF_FAILED);
    await assertExclusivelyInDetails(handoffFailedReason);

    // Realistic start_failed: PM_HANDOFF_TOO_LARGE (panel-local message)
    const tooLargeReason = 'Context size exceeded prompt limit';
    f.emitAttempt({
      attempt_id: 'att_' + '6'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      start_operation_id: 'op_' + '6'.repeat(32),
      stage: 'start_failed',
      status: 'failed',
      error_code: 'PM_HANDOFF_TOO_LARGE',
      status_reason: tooLargeReason,
      created_at: 1791705600,
    });
    await expect(attemptRow).toContainText(workStartStrings.en.wst_error_PM_HANDOFF_TOO_LARGE);
    await assertExclusivelyInDetails(tooLargeReason);

    // Unknown stage separately: stage: 'future_stage', status: 'running'
    f.emitAttempt({
      attempt_id: 'att_' + '7'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      start_operation_id: 'op_' + '7'.repeat(32),
      stage: 'future_stage',
      status: 'running',
      created_at: 1791705600,
    });
    const futureStatus = attemptRow.locator('.pm-work-start-attempt-status');
    await expect(futureStatus).toContainText('future_stage');
    await expect(futureStatus).toContainText('running');
    await expect(futureStatus).not.toContainText(workStartStrings.en.wst_start_failed_generic);
  });

  test('9. Observer and capability visibility', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    f.state.scopes = ['observe']; // Observer without start/manage

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    // Section exists with status, but editor form is hidden
    await expect(inspector.locator('[data-work-section="start"]')).toBeVisible();
    await expect(inspector.locator('[data-work-start-editor]')).toBeHidden();
    await expect(inspector.locator('[data-work-start-open]')).toHaveCount(0);

    // Now test without capability
    f.state.work_start_available = false;
    await page.reload();
    await page.locator('[data-pm-view="board"]').click();
    await page.locator(`[data-pm-kanban-work="${workId(1)}"] [data-board-open]`).click();

    const inspectorNoCap = page.locator('[data-work-inspector]');
    await expect(inspectorNoCap.locator('[data-work-section="start"]')).toHaveCount(0);
    await expect(inspectorNoCap.locator('[data-work-start-open]')).toHaveCount(0);
  });

  test('New test: failed operation', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    f.state.failHandoff = true;

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    await inspector.locator('[data-work-start-check]').click();

    const submitBtn = inspector.locator('[data-work-section="start"] [data-pm-action="work.start"] button[type="submit"]');
    await expect(submitBtn).toBeEnabled();
    await submitBtn.click();

    // Localized text displayed
    const errorMsg = inspector.locator('[data-work-section="start"] form .error');
    await expect(errorMsg).toBeVisible();
    await expect(errorMsg).toContainText(workStartStrings.en.error_code_PM_HANDOFF_FAILED);

    // Exactly one write
    await expect.poll(() => f.state.writes.length).toBe(1);
    expect(f.state.writes).toHaveLength(1);

    // "New request" button appears
    const resetBtn = inspector.locator('[data-work-section="start"] form').getByRole('button', {name: 'New request'});
    await expect(resetBtn).toBeVisible();
    await resetBtn.click();

    // Start disabled until readiness is checked again
    await expect(submitBtn).toBeDisabled();

    // No second write
    expect(f.state.writes).toHaveLength(1);
  });

  test('Start button is disabled before first readiness check at 1440 en and 390 zh-TW', async ({browser}) => {
    // 1440 en
    {
      const context = await browser.newContext({viewport: {width: 1440, height: 900}});
      const page = await context.newPage();
      const f = workStartFixture();
      await mountConversation(page, false, f.dispatch);
      await page.goto(route);
      await page.locator('[data-pm-view="board"]').click();
      await page.locator(`[data-pm-kanban-work="${workId(1)}"] [data-board-open]`).click();

      const inspector = page.locator('[data-work-inspector]');
      const startSection = inspector.locator('[data-work-section="start"]');
      await expect(startSection).toBeVisible();

      // Submit button is disabled before check
      const submitBtn = startSection.locator('[data-pm-action="work.start"] button[type="submit"]');
      await expect(submitBtn).toBeVisible();
      await expect(submitBtn).toBeDisabled();

      // Check button is ready and enabled
      const checkBtn = startSection.locator('[data-work-start-check]');
      await expect(checkBtn).toBeEnabled();

      await context.close();
    }

    // 390 zh-TW
    {
      const context = await browser.newContext({locale: 'zh-TW', viewport: {width: 390, height: 844}});
      const page = await context.newPage();
      const f = workStartFixture();
      await mountConversation(page, false, f.dispatch);
      await page.goto(route);
      await page.locator('#workspace-menu').click();
      await page.locator('[data-pm-view="board"]').click();
      await page.locator(`[data-pm-kanban-work="${workId(1)}"] [data-board-open]`).click();

      const inspector = page.locator('[data-work-inspector]');
      const startSection = inspector.locator('[data-work-section="start"]');
      await expect(startSection).toBeVisible();

      const submitBtn = startSection.locator('[data-pm-action="work.start"] button[type="submit"]');
      await expect(submitBtn).toBeVisible();
      await expect(submitBtn).toBeDisabled();

      const checkBtn = startSection.locator('[data-work-start-check]');
      await expect(checkBtn).toBeEnabled();

      await context.close();
    }
  });

  test('Requirement checkboxes are positioned to the left of text and vertically aligned', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    // workId(3) has multiple requirement checkboxes
    const card = page.locator(`[data-pm-kanban-work="${workId(3)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const reqLabels = inspector.locator('.pm-work-start-reqs label.pm-work-start-req');
    await expect(reqLabels).toHaveCount(2);

    for (let i = 0; i < 2; i++) {
      const label = reqLabels.nth(i);
      const cb = label.locator('input[type="checkbox"]');
      const textSpan = label.locator('.pm-work-start-req-text');

      const cbBox = await cb.boundingBox();
      const textBox = await textSpan.boundingBox();
      expect(cbBox).not.toBeNull();
      expect(textBox).not.toBeNull();

      if (cbBox && textBox) {
        // Checkbox is to the left of text
        expect(cbBox.x + cbBox.width).toBeLessThanOrEqual(textBox.x);

        // Checkbox vertically overlaps text: centers aligned within 4px
        const cbCenterY = cbBox.y + cbBox.height / 2;
        const textCenterY = textBox.y + textBox.height / 2;
        expect(Math.abs(cbCenterY - textCenterY)).toBeLessThanOrEqual(4);
      }
    }
  });

  test('New test: whitespace-only instruction', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const instruction = inspector.locator('[data-work-start-instruction]');
    await instruction.fill('   \n  \t  ');

    const checkBtn = inspector.locator('[data-work-start-check]');
    await checkBtn.click();

    // wst_instruction_required message shown
    const errorNotice = inspector.locator('[data-work-start-error]');
    await expect(errorNotice).toBeVisible();
    await expect(errorNotice).toHaveText(workStartStrings.en.wst_instruction_required);

    // No preview request sent
    expect(f.state.previews).toHaveLength(0);
  });

  test('10. Focus, caret and text survive event refresh with held read', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const instruction = inspector.locator('[data-work-start-instruction]');
    await instruction.focus();
    await instruction.fill('');
    await page.keyboard.type('First part');

    // Make the held read return an observable attempt
    f.state.attempts = [{
      attempt_id: 'att_' + 'c'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      stage: 'cancelled',
      status: 'cancelled',
      created_at: 1791705600,
    }];

    // Hold the read
    let releaseAttempts!: () => void;
    f.state.holdAttempts = new Promise<void>(resolve => {
      releaseAttempts = resolve;
    });

    const before = f.state.attemptsReads;
    const beforeServed = f.state.attemptsServed;
    // Bump cursor to trigger event refresh
    f.base.state.cursor++;
    await expect.poll(() => f.state.attemptsReads).toBeGreaterThan(before);

    // Type more characters while the read is held
    await page.keyboard.type(' and second part');

    // Assert textarea is not disabled
    await expect(instruction).not.toBeDisabled();

    // Release the read
    releaseAttempts();
    f.state.holdAttempts = null;

    // Assert that the read was actually served after releasing the hold
    await expect.poll(() => f.state.attemptsServed).toBeGreaterThan(beforeServed);

    // Wait for the attempt row to show that attempt
    const attemptRow = inspector.locator('.pm-work-start-attempt');
    await expect(attemptRow).toBeVisible();
    await expect(attemptRow).toContainText('Cancelled');

    // Assert afterwards: every typed character is present, caret is where typing left it, and focus is unchanged
    await expect(instruction).toHaveValue('First part and second part');
    const caretInfo = await instruction.evaluate((node: HTMLTextAreaElement) => ({
      start: node.selectionStart,
      end: node.selectionEnd,
      isFocused: document.activeElement === node,
    }));
    expect(caretInfo.isFocused).toBe(true);
    expect(caretInfo.start).toBe('First part and second part'.length);
    expect(caretInfo.end).toBe('First part and second part'.length);
  });

  test('10. Bind-needed: checkboxes and draft survive refresh and reload', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    // workId(3) has bind_needed because its binding.plan_revision_id is from an older plan
    const card = page.locator(`[data-pm-kanban-work="${workId(3)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const checkboxes = inspector.locator('.pm-work-start-reqs input[type="checkbox"]');
    await expect(checkboxes).toHaveCount(2);

    const loginCb = inspector.locator('input[type="checkbox"][value="membership.login"]');
    const recoveryCb = inspector.locator('input[type="checkbox"][value="membership.recovery"]');
    await expect(loginCb).toBeChecked();
    await expect(recoveryCb).not.toBeChecked();

    // Change checked requirements: check recovery
    await recoveryCb.check();
    await expect(recoveryCb).toBeChecked();

    // Trigger event refresh
    const beforeReads = f.state.attemptsReads;
    f.base.state.cursor++;
    await expect.poll(() => f.state.attemptsReads).toBeGreaterThan(beforeReads);

    // Assert checkboxes survive refresh
    await expect(loginCb).toBeChecked();
    await expect(recoveryCb).toBeChecked();

    // Reload page
    await page.reload();
    await page.locator('[data-pm-view="board"]').click();
    await page.locator(`[data-pm-kanban-work="${workId(3)}"] [data-board-open]`).click();

    // Assert checkboxes match the restored draft
    const reloadedInspector = page.locator('[data-work-inspector]');
    const reloadedLogin = reloadedInspector.locator('input[type="checkbox"][value="membership.login"]');
    const reloadedRecovery = reloadedInspector.locator('input[type="checkbox"][value="membership.recovery"]');
    await expect(reloadedLogin).toBeChecked();
    await expect(reloadedRecovery).toBeChecked();
  });

  test('10. Attempt details disclosure open state survives event refresh', async ({page}) => {
    await page.setViewportSize({width: 1440, height: 900});
    const f = workStartFixture();
    f.state.attempts = [{
      attempt_id: 'att_' + '7'.repeat(32),
      workspace_id: workspaceId,
      work_item_id: workId(1),
      start_operation_id: 'op_' + '7'.repeat(32),
      stage: 'starting',
      status: 'running',
      created_at: 1791705600,
    }];

    await mountConversation(page, false, f.dispatch);
    await page.goto(route);
    await page.locator('[data-pm-view="board"]').click();

    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await card.locator('[data-board-open]').click();

    const inspector = page.locator('[data-work-inspector]');
    const details = inspector.locator('.pm-work-start-attempt details');
    await expect(details).toBeVisible();
    await expect(details).not.toHaveAttribute('open', '');

    // Open Details disclosure
    await details.locator('summary').click();
    await expect(details).toHaveAttribute('open', '');

    // Trigger event refresh
    const beforeReads = f.state.attemptsReads;
    f.base.state.cursor++;
    await expect.poll(() => f.state.attemptsReads).toBeGreaterThan(beforeReads);

    // Assert details disclosure is still open
    await expect(details).toHaveAttribute('open', '');
  });

  test('11. zh-TW at 390 width: no raw keys, no English leftovers, no overflow', async ({browser}) => {
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

    // Check heading
    const heading = inspector.locator('[data-work-section="start"] h3');
    await expect(heading).toHaveText('開始工作');

    // Check readiness button
    const checkBtn = inspector.locator('[data-work-start-check]');
    await expect(checkBtn).toHaveText('檢查就緒狀態');
    await checkBtn.click();

    // Facts in Traditional Chinese
    const facts = inspector.locator('[data-work-start-facts]');
    await expect(facts).toBeVisible();
    await expect(facts).toContainText('準備新的工作包');

    // Submit button in Traditional Chinese
    const submitBtn = inspector.locator('[data-work-section="start"] [data-pm-action="work.start"] button[type="submit"]');
    await expect(submitBtn).toHaveText('開始工作');

    // Open options disclosure so all options are in the DOM
    const optionsDisclosure = inspector.locator('.pm-work-start-panel details');
    if (await optionsDisclosure.count() > 0) {
      await optionsDisclosure.first().locator('summary').click();
    }

    // Check for no raw keys or undefined/null
    const startSection = inspector.locator('[data-work-section="start"]');
    const textContent = await startSection.innerText();
    expect(textContent).not.toContain('undefined');
    expect(textContent).not.toContain('null');
    expect(textContent).not.toMatch(/wst_[a-z_]+/);
    expect(textContent).not.toMatch(/error_code_[A-Z_]+/);

    // Collect all panel text and all select option texts
    const selectOptions = await startSection.locator('select option').allInnerTexts();
    const fullRenderedText = [textContent, ...selectOptions].join('\n');

    // Assert that no string in workStartStrings.en appears anywhere in the rendered panel,
    // with the ONLY allowed exceptions being provider names (Claude, Codex) and the accepted acronym PR.
    const allowedEnglish = new Set(['Claude', 'Codex', 'PR']);
    for (const [key, enVal] of Object.entries(workStartStrings.en)) {
      if (allowedEnglish.has(enVal)) continue;
      expect(fullRenderedText, `Found English string "${enVal}" (${key}) in zh-TW panel`).not.toContain(enVal);
    }

    // Verify no horizontal overflow in start section
    const isOverflowing = await startSection.evaluate((el: HTMLElement) => el.scrollWidth > el.clientWidth + 2);
    expect(isOverflowing).toBe(false);

    await context.close();
  });
});
