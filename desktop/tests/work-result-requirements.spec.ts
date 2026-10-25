import {test, expect, type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workResultFixture, buildResultEnvelope, workspaceId, workId, route} from './work-result-fixture.ts';
import {workResultStrings} from '../src/pm-work-result-strings.js';
import {installBodyCompletedCounter, countCompletedBodies, syncHeldBodyCompletion} from './page-sync.ts';

const attemptId = 'att_' + '1'.repeat(32);
const op = (n: number) => 'op_' + String(n).repeat(32);
function requirements(state = 'assessments_recorded') {
  const reference = {review_id: 'pmqr_' + '1'.repeat(32), operation_id: op(3), version: 2,
    document_digest: 'c'.repeat(64), candidate_commit: 'a'.repeat(40)};
  const assessment = {reference, applicable: false, qualified: false, conclusion: 'accepted', state: 'no_longer_applies',
    applicability_reasons: ['PM_QUALITY_VERSION_CONFLICT'], assessed_by: 'reviewer',
    original: {dod: 'accepted', rationale: 'Original fixed evidence', criteria: [
      {criterion: {kind: 'acceptance', requirement_id: 'member.login', input_id: 'input.login'},
        status: 'accepted', rationale: '<img src=x onerror=alert(1)> is literal evidence', verification: {status: 'passed'}, reference: {operation_id: op(5)}},
      {criterion: {kind: 'acceptance', requirement_id: 'member.recovery', input_id: 'input.recovery'}, status: 'accepted', rationale: 'Another requirement'},
    ]}};
  return {relation: 'explicit', package_ref: {workspace_id: workspaceId, package_id: 'pmwp_' + '1'.repeat(32), document_digest: 'd'.repeat(64)},
    plan_ref: {revision_id: 'pmrev_' + '1'.repeat(32), scope_revision: 'pmscope_' + '1'.repeat(32)},
    candidate_commit: 'a'.repeat(40), verification: state === 'no_conclusion' ? 'unverified' : 'verified', verification_evidence: [{operation_id: op(5)}],
    selection_rule: 'explicit_original_review_selection_required', coverage: {reviews_partial: false, packages_partial: false}, related: [],
    items: [{requirement_id: 'member.login', text: 'Members can sign in', source_input_id: 'input.requirement',
      acceptance_input_ids: ['input.login'], state, selection: 'undecided',
      link: {read: 'pm_plan_get', params: {workspace_id: workspaceId, revision_id: 'pmrev_' + '1'.repeat(32)}},
      assessments: state === 'assessments_recorded' ? [assessment,
        {...structuredClone(assessment), reference: {...reference, operation_id: op(4), version: 3},
          applicable: true, applicability_reasons: [], conclusion: 'rejected', state: 'rejected',
          original: {dod: 'rejected', rationale: 'Regression still present', criteria: []}}] : []}],
    dependent_work: [{work_item_id: workId(2), title: 'Password recovery', package_id: 'pmwp_' + '2'.repeat(32),
      document_digest: 'e'.repeat(64), prerequisite_ids: ['member.login'], readiness: {status: 'blocked'},
      selections_complete: true, selections: [{execution_operation_id: op(6),
        quality_refs: {'member.login': {reference}}, readiness: {status: 'ready'}}]}]};
}

async function open(page: Page, f: ReturnType<typeof workResultFixture>, native = false) {
  await mountConversation(page, native, f.dispatch);
  await page.goto(route);
  const menu = page.locator('#workspace-menu');
  if ((page.viewportSize()?.width || 1440) < 768) await menu.click();
  await page.locator('[data-pm-view="board"]').click();
  await page.locator(`[data-pm-kanban-work="${workId(1)}"] [data-board-open]`).click();
  await expect(page.locator('[data-work-result]')).toBeVisible();
}

for (const locale of ['en', 'zh-TW'] as const) for (const native of [false, true]) {
  test(`original assessments and dependency selections stay separate: ${locale} ${native ? 'IPC' : 'HTTP'}`, async ({browser}, info) => {
    const context = await browser.newContext({locale, viewport: {width: locale === 'zh-TW' ? 390 : 1440, height: 900}});
    const page = await context.newPage(), f = workResultFixture(), strings = workResultStrings[locale];
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    try {
      f.state.currentEnvelope = buildResultEnvelope({verification_status: 'verified', requirements: requirements()});
      await open(page, f, native);
      const section = page.locator('[data-result-requirements]');
      await expect(section.getByRole('heading', {name: strings.wres_req_heading})).toBeVisible();
      const row = section.locator('[data-result-requirement="member.login"]');
      await expect(row).toContainText(strings.wres_req_assessments_recorded);
      const accepted = row.locator('summary').filter({hasText: strings.wres_req_accepted + ' · '});
      const rejected = row.locator('summary').filter({hasText: strings.wres_req_rejected + ' · '});
      await expect(accepted).toHaveText(`${strings.wres_req_accepted} · ${strings.wres_req_no_longer_applies}`);
      await expect(rejected).toHaveText(`${strings.wres_req_rejected} · ${strings.wres_req_applicable}`);
      await accepted.click();
      await row.locator('summary').filter({hasText: `${strings.wres_req_criterion}: input.login`}).click();
      await expect(row.getByText('<img src=x onerror=alert(1)> is literal evidence', {exact: true})).toBeVisible();
      await expect(row.locator('img')).toHaveCount(0);
      await expect(row.locator('summary').filter({hasText: 'input.recovery'})).toHaveCount(0);
      await expect(row.getByRole('link', {name: strings.wres_view_operation})).toHaveAttribute('href', `#/op/${op(3)}`);
      await section.locator('[data-result-requirement-details="dependencies"] > summary').click();
      const dependent = section.locator('[data-result-dependent]');
      await expect(dependent).toContainText(`${strings.wres_req_without_selection}: ${strings.wres_req_blocked}`);
      await expect(dependent.locator('summary').first()).toHaveText(`${strings.wres_req_original_selection}: ${strings.wres_req_ready}`);
      await expect(section.locator('button, input, select, textarea')).toHaveCount(0);
      expect(f.state.writes).toEqual([]);
      expect(f.state.changesCount + f.state.diffCount).toBe(0);
      expect(await section.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      await section.getByRole('heading', {name: strings.wres_req_heading}).scrollIntoViewIfNeeded();
      await page.screenshot({path: info.outputPath('requirements.png'), fullPage: true});
      expect(errors).toEqual([]);
    } finally {await context.close();}
  });
}

test('refresh preserves original assessment focus, then withdrawal removes its history', async ({page}) => {
  const f = workResultFixture(), data = requirements();
  f.state.currentEnvelope = buildResultEnvelope({requirements: data});
  await open(page, f);
  const row = page.locator('[data-result-requirement="member.login"]');
  const summary = row.locator('summary').filter({hasText: 'Original conclusion: accepted ·'});
  await summary.click();
  await summary.focus();
  const next = structuredClone(data);
  next.coverage = {reviews_partial: true, packages_partial: true};
  next.dependent_work[0].selections_complete = false;
  f.emitResultRecorded(attemptId, buildResultEnvelope({requirements: next}));
  await expect(page.getByText(workResultStrings.en.wres_req_reviews_partial, {exact: true})).toBeVisible();
  await expect(page.getByText(workResultStrings.en.wres_req_packages_partial, {exact: true})).toBeVisible();
  await expect(summary).toBeFocused();
  await expect(summary.locator('..')).toHaveAttribute('open', '');
  f.emitResultRecorded(attemptId, buildResultEnvelope({requirements: {relation: 'none', reason: 'no_visible_explicit_binding', items: [], dependent_work: []}}));
  await expect(page.locator('[data-result-requirements]')).toHaveText(workResultStrings.en.wres_req_heading + workResultStrings.en.wres_req_none);
  await expect(row).toHaveCount(0);
  await expect(page.locator(`[href="#/op/${op(3)}"]`)).toHaveCount(0);
  expect(f.state.writes).toEqual([]);
});

for (const state of ['no_conclusion','verified_no_conclusion','unavailable']) {
  test(`a requirement remains ${state} without a selected assessment`, async ({page}) => {
    const f = workResultFixture();
    f.state.currentEnvelope = buildResultEnvelope({requirements: requirements(state)});
    await open(page, f);
    await expect(page.locator('[data-result-requirement]')).toContainText(workResultStrings.en[`wres_req_${state}`]);
    await expect(page.locator('[data-check="acceptance"]')).toHaveText(workResultStrings.en.wres_acceptance_unassessed);
    expect(f.state.writes).toEqual([]);
  });
}

test('permission withdrawal removes relations and an older server adds no empty relation claim', async ({page}) => {
  const f = workResultFixture();
  f.state.currentEnvelope = buildResultEnvelope({requirements: requirements()});
  await open(page, f);
  f.state.resultStatus = 403;
  f.emitResultRecorded(attemptId);
  await expect(page.locator('[data-result-requirements]')).toHaveCount(0);
  f.state.resultStatus = null;
  f.emitResultRecorded(attemptId, buildResultEnvelope());
  await expect(page.locator('[data-work-result]')).toBeVisible();
  await expect(page.locator('[data-result-requirements]')).toHaveCount(0);
  expect(f.state.writes).toEqual([]);
});

test('a late result from the previous Work cannot restore its requirement evidence', async ({page}) => {
  const f = workResultFixture(), secondId = 'att_' + '2'.repeat(32);
  f.state.currentEnvelope = buildResultEnvelope({requirements: requirements()});
  const second = requirements('no_conclusion');
  second.items[0].requirement_id = 'member.recovery';
  second.items[0].text = 'Recover an account';
  second.dependent_work = [];
  f.state.attempts.push({...f.state.attempts[0], attempt_id: secondId, work_item_id: workId(2),
    session: {host: 'fixture-host', session_id: 'session-456'}});
  f.state.resultEnvelopes.set(secondId, buildResultEnvelope({attempt_id: secondId, requirements: second}));
  await installBodyCompletedCounter(page);
  await open(page, f);
  let release = () => {};
  f.state.holdResult = new Promise<void>(resolve => {release = resolve;});
  f.state.holdResultAttemptId = attemptId;
  const reads = f.state.resultRequests.length;
  try {
    f.emitResultRecorded(attemptId);
    await expect.poll(() => f.state.resultRequests.length).toBeGreaterThan(reads);
    await page.locator(`[data-pm-kanban-work="${workId(2)}"] [data-board-open]`).click();
    await expect(page.locator('[data-result-requirement="member.recovery"]')).toBeVisible();
    const before = await countCompletedBodies(page, `/work-attempts/${attemptId}/result`);
    release();
    f.state.holdResult = null;
    await syncHeldBodyCompletion(page, `/work-attempts/${attemptId}/result`, before);
    await expect(page.locator('[data-result-requirement="member.login"]')).toHaveCount(0);
    await expect(page.locator('[data-result-requirement="member.recovery"]')).toBeVisible();
    expect(f.state.writes).toEqual([]);
  } finally {release(); f.state.holdResult = null;}
});

test('one assessment covering two requirements retains focus on the selected requirement', async ({page}) => {
  const f = workResultFixture(), data = requirements();
  data.items.push({...structuredClone(data.items[0]), requirement_id: 'member.recovery', text: 'Recover an account',
    source_input_id: 'input.recovery.requirement', acceptance_input_ids: ['input.recovery']});
  f.state.currentEnvelope = buildResultEnvelope({requirements: data});
  await open(page, f);
  for (const id of ['member.login', 'member.recovery']) {
    await page.locator(`[data-result-requirement="${id}"] > details > summary`)
      .filter({hasText: 'Original conclusion: accepted ·'}).click();
  }
  const link = page.locator(`[data-result-requirement="member.recovery"] a[href="#/op/${op(3)}"]`);
  await link.focus();
  const next = structuredClone(data);
  next.coverage.reviews_partial = true;
  f.emitResultRecorded(attemptId, buildResultEnvelope({requirements: next}));
  await expect(page.getByText(workResultStrings.en.wres_req_reviews_partial, {exact: true})).toBeVisible();
  await expect(link).toBeFocused();
  expect(f.state.writes).toEqual([]);
});
