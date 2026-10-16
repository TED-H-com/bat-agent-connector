import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workStartFixture, workspaceId, workId, route, revisionId} from './work-start-fixture.ts';

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
    expect(f.state.writes).toHaveLength(1);
    const originalKey = f.state.writes[0].idempotency_key;
    const originalBody = structuredClone(f.state.writes[0].body);

    // Click "Check original request"
    await submitBtn.click();
    await expect.poll(() => f.state.writes.length).toBe(2);

    expect(f.state.writes[1].idempotency_key).toBe(originalKey);
    expect(f.state.writes[1].body).toEqual(originalBody);
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
    await expect(blockedCheck).toContainText('Acceptance criteria not met');
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
    await expect(disabledNotice).toContainText('Workspace owner required to bind this Work to the current plan.');
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

      // Exactly one write
      expect(f.state.writes).toHaveLength(1);

      // Localized error message shown
      const errorMsg = inspector.locator('[data-work-section="start"] form .error');
      await expect(errorMsg).toBeVisible();

      // "New request" button appears
      const resetBtn = inspector.locator('[data-work-section="start"] form').getByRole('button', {name: 'New request'});
      await expect(resetBtn).toBeVisible();
      await expect(resetBtn).toHaveText('New request');

      // Click "New request"
      await resetBtn.click();

      // Start remains disabled until readiness is checked again
      await expect(submitBtn).toBeDisabled();
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

    // Move to start_unknown
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

  test('10. Focus, caret and text survive event refresh', async ({page}) => {
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
    await instruction.fill('Typing custom instructions here...');
    await instruction.evaluate((node: HTMLTextAreaElement) => {
      node.setSelectionRange(7, 13);
    });

    const initialSelection = await instruction.evaluate((node: HTMLTextAreaElement) => ({
      start: node.selectionStart,
      end: node.selectionEnd,
      value: node.value,
      isFocused: document.activeElement === node,
    }));

    // Trigger an event refresh
    f.base.state.cursor++;
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('batc-test-refresh')));
    await page.waitForTimeout(500);

    const postSelection = await instruction.evaluate((node: HTMLTextAreaElement) => ({
      start: node.selectionStart,
      end: node.selectionEnd,
      value: node.value,
      isFocused: document.activeElement === node,
    }));

    expect(postSelection.value).toBe(initialSelection.value);
    expect(postSelection.start).toBe(initialSelection.start);
    expect(postSelection.end).toBe(initialSelection.end);
    expect(postSelection.isFocused).toBe(true);
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
    await expect(facts).toContainText('準備新的工作依據套件');

    // Submit button in Traditional Chinese
    const submitBtn = inspector.locator('[data-work-section="start"] [data-pm-action="work.start"] button[type="submit"]');
    await expect(submitBtn).toHaveText('開始工作');

    // Check for no raw keys or undefined/null
    const startSection = inspector.locator('[data-work-section="start"]');
    const textContent = await startSection.innerText();
    expect(textContent).not.toContain('undefined');
    expect(textContent).not.toContain('null');
    expect(textContent).not.toMatch(/wst_[a-z_]+/);
    expect(textContent).not.toMatch(/error_code_[A-Z_]+/);

    // Verify no horizontal overflow in start section
    const isOverflowing = await startSection.evaluate((el: HTMLElement) => el.scrollWidth > el.clientWidth + 2);
    expect(isOverflowing).toBe(false);

    await context.close();
  });
});
