import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workResultFixture, buildResultEnvelope, workspaceId, workId, route} from './work-result-fixture.ts';

const CANDIDATE = 'abcdef1234567890abcdef1234567890abcdef12';
const CANDIDATE_NEXT = 'fedcba0987654321fedcba0987654321fedcba09';
const BASE = '1234567890abcdef1234567890abcdef12345678';
const EXEC = 'op_' + '2'.repeat(32);
const ATT = 'att_' + '1'.repeat(32);
const PUBLISH_OP = 'op_' + '3'.repeat(32);

let pageErrors: string[] = [];

function recordPageErrors(p: any) {
  if (!p || p._recordingPageErrors) return;
  p._recordingPageErrors = true;
  p.on('pageerror', (err: any) => pageErrors.push(err?.message || String(err)));
}

function publishTarget() {
  return {
    workspace_id: workspaceId,
    repository: 'acme/widget',
    host: 'fixture-host',
    source_operation_id: EXEC,
  };
}

function publishEntry(candidate: string, {base = true, preconditions = true} = {}) {
  const prefilled: Record<string, string> = {};
  if (candidate) prefilled.candidate_sha = candidate;
  if (base) prefilled.base_ref = 'main';
  return {
    kind: 'action',
    action: 'github.pr.publish',
    target: publishTarget(),
    required_scopes: ['integrate', 'manage', 'observe'],
    required_params: ['candidate_sha', 'base_ref', 'public_title', 'public_body', 'draft'],
    optional_params: ['quality_ref'],
    prefilled_params: prefilled,
    ...(preconditions ? {preconditions: {repository_id: 7, expected_base_sha: BASE}} : {}),
  };
}

function resultEnvelope(actions: any[], extra: Record<string, unknown> = {}) {
  return buildResultEnvelope({attempt_id: ATT, next_allowed_actions: actions, ...extra});
}

function publishOperation(actor: string, input: any, status = 'succeeded') {
  return {
    operation_id: PUBLISH_OP,
    action: input.body.action,
    target: input.body.target,
    params: input.body.params,
    preconditions: input.body.preconditions,
    actor,
    idempotency_key: input.idempotency_key,
    status,
    result: status === 'succeeded' ? {published: true, merged: false, deployed: false} : null,
    error_code: status === 'failed' ? 'PR_RECEIPT_EXISTS' : null,
    status_reason: status === 'failed' ? 'receipt exists' : null,
  };
}

function armPublish(f: any) {
  f.state.publishWrites = [];
  f.state.publishOps = new Map<string, any>();
  if (!Object.hasOwn(f.state, 'holdPublish')) f.state.holdPublish = null;
  if (!f.state.publishMode) f.state.publishMode = 'success';
  const actor = f.base?.state?.actor || 'reader';
  const inner = f.dispatch.bind(f);
  f.dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    if (input.method === 'POST' && path === '/operations' && input.body?.action === 'github.pr.publish') {
      f.state.publishWrites.push(structuredClone(input));
      const key = input.idempotency_key;
      if (f.state.publishOps.has(key)) {
        return {status: 200, data: {operation: structuredClone(f.state.publishOps.get(key))}};
      }
      const gate = f.state.holdPublish;
      if (gate) await gate;
      if (f.state.publishMode === 'lost') {
        f.state.publishMode = 'replay';
        const op = publishOperation(actor, input);
        f.state.publishOps.set(key, op);
        return {status: 503, data: {error: {code: 'LOST_REPLY', message: 'Reply unavailable'}}};
      }
      const op = publishOperation(actor, input, f.state.publishMode === 'failed' ? 'failed' : 'succeeded');
      if (op.status === 'failed' && f.state.publishError) op.error_code = f.state.publishError;
      f.state.publishOps.set(key, op);
      return {status: 200, data: {operation: op}};
    }
    return inner(input);
  };
}

function allowPublish(f: any) {
  if (!f.state.scopes.includes('integrate')) f.state.scopes.push('integrate');
  if (!f.state.actions.some((row: any) => row.action === 'github.pr.publish')) {
    f.state.actions.push({action: 'github.pr.publish', allowed: true});
  }
}

async function openResult(page: any, f: any) {
  allowPublish(f);
  armPublish(f);
  await mountConversation(page, false, input => f.dispatch(input));
  await page.goto(route);
  await page.locator('[data-pm-view="board"]').click();
  await page.locator(`[data-pm-kanban-work="${workId(1)}"] [data-board-open]`).click();
  await expect(page.locator('[data-work-result]')).toBeVisible();
}

async function fillPublish(page: any) {
  await page.locator('[data-work-publish-title]').fill('Login fixes');
  await page.locator('[data-work-publish-body]').fill('Describe the change.');
  await page.locator('[data-work-publish-draft="true"]').check();
}

test.describe('Work Result pull request publication', () => {
  test.beforeEach(async ({page}) => {
    pageErrors = [];
    recordPageErrors(page);
  });

  test.afterEach(async () => {
    expect(pageErrors).toEqual([]);
  });

  test('a canonical PR_RECEIPT_EXISTS receipt never offers a fresh request', async ({page}) => {
    const f = workResultFixture();
    f.state.publishMode = 'failed';
    f.state.currentEnvelope = resultEnvelope([publishEntry(CANDIDATE)]);
    await openResult(page, f);
    await fillPublish(page);
    await page.locator('[data-work-publish-submit]').click();
    await expect(page.locator('[data-work-publish]')).toContainText('already');
    await expect(page.locator('[data-work-publish-new-request]')).toBeHidden();
    expect(f.state.publishWrites).toHaveLength(1);
  });

  test('a foreign controller removes a pending publication form', async ({page}) => {
    const f = workResultFixture();
    f.state.publishMode = 'lost';
    f.state.currentEnvelope = resultEnvelope([publishEntry(CANDIDATE)]);
    await openResult(page, f);
    await fillPublish(page);
    await page.locator('[data-work-publish-submit]').click();
    await expect(page.getByRole('button', {name: 'Check original request'})).toBeVisible();
    f.emitResultRecorded(ATT, resultEnvelope([], {blocked_actions: [{action: 'github.pr.publish', reason: 'not_controller'}]}));
    await expect(page.locator('[data-work-publish-blocked="not_controller"]')).toBeVisible();
    await expect(page.locator('[data-work-publish] input, [data-work-publish] textarea, [data-work-publish] button')).toHaveCount(0);
    expect(f.state.publishWrites).toHaveLength(1);
  });

  test('an absent publication entry draws nothing', async ({page}) => {
    const f = workResultFixture();
    f.state.currentEnvelope = resultEnvelope([]);
    await openResult(page, f);
    await expect(page.locator('[data-work-publish]')).toHaveCount(0);
    expect(f.state.publishWrites).toEqual([]);
    expect(f.state.changesCount).toBe(0);
  });

  test('a complete entry submits the offered target and the typed fields', async ({page}) => {
    const f = workResultFixture();
    f.state.currentEnvelope = resultEnvelope([publishEntry(CANDIDATE)]);
    await openResult(page, f);
    const submit = page.locator('[data-work-publish-submit]');
    await expect(submit).toBeVisible();
    await expect(submit).toBeEnabled();
    await expect(page.locator('[data-work-publish-base]')).toHaveText('main');
    await expect(page.locator('[data-work-publish-commit]')).toHaveAttribute('title', CANDIDATE);
    await expect(page.locator('[data-work-publish-base-sha]')).toHaveAttribute('title', BASE);
    await fillPublish(page);
    await submit.click();
    await expect.poll(() => f.state.publishWrites.length).toBe(1);
    const write = f.state.publishWrites[0];
    expect(write.body).toEqual({
      action: 'github.pr.publish',
      target: publishTarget(),
      params: {
        candidate_sha: CANDIDATE,
        base_ref: 'main',
        public_title: 'Login fixes',
        public_body: 'Describe the change.',
        draft: true,
      },
      preconditions: {repository_id: 7, expected_base_sha: BASE},
    });
    expect(write.body.params.quality_ref).toBeUndefined();
    expect(write.idempotency_key).toMatch(/^[0-9a-f-]{36}$/i);
    await expect(page.locator('[data-pm-saved]')).toHaveText('Pull request published. Merge, deploy, and acceptance are separate steps.');
    expect(f.state.changesCount).toBe(0);
  });

  test('an incomplete entry keeps the draft and does not submit', async ({page}) => {
    const f = workResultFixture();
    f.state.currentEnvelope = resultEnvelope([publishEntry(CANDIDATE, {base: false, preconditions: false})]);
    await openResult(page, f);
    await expect(page.locator('[data-work-publish-gap="preconditions"]')).toBeVisible();
    await expect(page.locator('[data-work-publish-submit]')).toBeHidden();
    await page.locator('[data-work-publish-title]').fill('Kept draft');
    await page.locator('[data-work-publish-title]').press('Enter');
    expect(f.state.publishWrites).toEqual([]);
    const drafts = await page.evaluate(() => Object.entries(localStorage)
      .filter(([key]) => key.includes('github.pr.publish'))
      .map(([, value]) => value));
    expect(drafts.some(value => value.includes('Kept draft'))).toBe(true);
    expect(f.state.changesCount).toBe(0);
  });

  test('a lost acknowledgement retries the original key', async ({page}) => {
    const f = workResultFixture();
    f.state.publishMode = 'lost';
    f.state.currentEnvelope = resultEnvelope([publishEntry(CANDIDATE)]);
    await openResult(page, f);
    await fillPublish(page);
    await page.locator('[data-work-publish-submit]').click();
    await expect.poll(() => f.state.publishWrites.length).toBe(1);
    const key = f.state.publishWrites[0].idempotency_key;
    const original = f.state.publishWrites[0].body;
    await expect(page.getByRole('button', {name: 'Check original request'})).toBeVisible();
    await page.getByRole('button', {name: 'Check original request'}).click();
    await expect.poll(() => f.state.publishWrites.length).toBe(2);
    expect(f.state.publishWrites[1].idempotency_key).toBe(key);
    expect(f.state.publishWrites[1].body).toEqual(original);
    expect(f.state.publishWrites[1].body.params.candidate_sha).toBe(CANDIDATE);
    expect(f.state.changesCount).toBe(0);
  });

  test('a newer candidate does not retarget the original request', async ({page}) => {
    const f = workResultFixture();
    let release = () => {};
    f.state.holdPublish = new Promise<void>(resolve => { release = resolve; });
    f.state.currentEnvelope = resultEnvelope([publishEntry(CANDIDATE)]);
    await openResult(page, f);
    await fillPublish(page);
    await page.locator('[data-work-publish-submit]').click();
    await expect.poll(() => f.state.publishWrites.length).toBe(1);
    const next = resultEnvelope([publishEntry(CANDIDATE_NEXT)], {
      fixed_commit: {
        commit_sha: CANDIDATE_NEXT,
        source_kind: 'turn_end_observation',
        source_id: 'src_02',
        recorded_by: 'system',
        recorded_at: 1791705600,
        uncommitted: null,
      },
    });
    f.emitResultRecorded(ATT, next);
    await expect(page.locator('[data-work-publish-held]')).toBeVisible();
    await expect(page.locator('[data-work-publish-commit]')).toHaveAttribute('title', CANDIDATE);
    const stored = await page.evaluate(original => {
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (!key || !key.includes('github.pr.publish')) continue;
        const parsed = JSON.parse(localStorage.getItem(key) || '{}');
        if (parsed?.intent?.request?.params?.candidate_sha === original) return parsed.intent.request;
      }
      return null;
    }, CANDIDATE);
    expect(stored.params.candidate_sha).toBe(CANDIDATE);
    expect(stored.target).toEqual(publishTarget());
    expect(f.state.publishWrites).toHaveLength(1);
    expect(f.state.publishWrites[0].body.params.candidate_sha).toBe(CANDIDATE);
    release();
    await expect(page.locator('[data-pm-saved]')).toBeVisible();
    expect(f.state.publishWrites).toHaveLength(1);
    expect(f.state.changesCount).toBe(0);
  });
});
