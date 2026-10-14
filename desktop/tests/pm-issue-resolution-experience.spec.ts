import {test, expect} from '@playwright/test';
import {demoFixture} from './demo/index.ts';
import {demoProject} from './demo/project.ts';
import {overviewGovernance} from './demo/overview.ts';
import {mountConversation} from './conversation-fixture.ts';

const colorScheme = process.env.BATC_UI_TEST_SCHEME === 'dark' ? 'dark' : 'light';
test.use({colorScheme});
function fixture({locale = 'en', role = 'owner', assigned = false, manage = true, registered = true} = {}) {
  const f = demoFixture({locale, discussions: true}), root = `/pm/workspaces/${f.ids.workspaceId}`;
  const finding: any = overviewGovernance(demoProject(locale))[0]; finding.record.kind = 'issue';
  const id = finding.record.record_id;
  Object.assign(finding.revision.document, {owner_actor: assigned ? f.ids.actor : 'assigned-writer',
    sources: [{kind: 'requirement', id: f.ids.requirements.audit, identity: 'fixed-original', source: {text: 'Original snapshot statement'}}],
    trigger: 'Expiry unresolved', response: 'Discuss expiry', review_at: '2026-10-20T12:00:00Z',
    alternatives: ['Keep the original expiry'], disambiguation: {record_ids: ['pmgov_' + 'a'.repeat(32)], rationale: 'Distinct cause'}});
  const data = f.discussion.details.get(f.ids.threadFollow);
  data.thread.context = [{kind: 'governance', id, available: true, position: 0, label: finding.revision.document.title}];
  const state = {writes: [] as any[], operations: new Map<string, any>(), effects: 0, denied: 0, lose: false, held: null as Promise<void> | null, reads: [] as string[]};
  const dispatch = async (input: any): Promise<any> => {
    state.reads.push(input.path);
    const path = new URL(input.path, 'http://fixture').pathname;
    if (input.method === 'POST' && input.body?.action === 'pm.governance.revise') {
      state.writes.push(structuredClone(input));
      let operation = state.operations.get(input.idempotency_key);
      if (!operation) {
        if (input.body.preconditions.expected_version !== finding.record.version) return {status: 409,
          data: {error: {code: 'VERSION_CONFLICT', message: 'Read current finding', admission_refused: true}}};
        state.effects++; finding.record.version++; finding.revision.version++;
        finding.record.revision_id = finding.revision.revision_id = 'pmgrev_' + 'f'.repeat(32);
        finding.revision.document.status = 'reported_resolved'; finding.revision.document.rationale = input.body.params.rationale;
        operation = {...structuredClone(input.body), actor: f.ids.actor, idempotency_key: input.idempotency_key,
          operation_id: 'op_' + 'f'.repeat(32), status: 'succeeded', result: {workspace_id: f.ids.workspaceId, record_id: id}};
        state.operations.set(input.idempotency_key, operation);
      }
      if (state.lose) {state.lose = false; return {status: 503, data: {error: {code: 'UNAVAILABLE', message: 'Acknowledgement lost'}}};}
      return {status: 200, data: {operation}};
    }
    if (path.startsWith('/operations/')) {
      const operation = [...state.operations.values()].find(row => path.endsWith(row.operation_id));
      if (operation) return {status: 200, data: {operation}};
    }
    if (path === root + '/governance/' + id) {
      const held = state.held; state.held = null;
      const reply = state.denied ? {status: state.denied, data: {error: {code: 'NOT_FOUND', message: 'Source unavailable'}}}
        : {status: 200, data: {version: 1, workspace_id: f.ids.workspaceId, ...structuredClone(finding)}};
      if (held) await held;
      return reply;
    }
    const result = await f.dispatch(input);
    const caps = path === '/bootstrap' ? result.data.capabilities : path === '/capabilities' ? result.data : null;
    if (caps) {
      if (!manage) caps.scopes = caps.scopes.filter((scope: string) => scope !== 'manage');
      if (registered) caps.actions.push({action: 'pm.governance.revise', allowed: true});
    }
    if (path === root) Object.assign(result.data.workspace, {membership: {actor: f.ids.actor, role},
      members: [{actor: f.ids.actor, role}, {actor: 'assigned-writer', role: 'contributor'}]});
    return result;
  };
  return {...f, finding, state, dispatch, route: `/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadFollow}`};
}

const resolution = (page: any) => page.locator('[data-proposal-resolution]');

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) test(`explicit resolution preserves the finding ${locale} ${native ? 'native' : 'web'}`, async ({browser, baseURL}) => {
  const context = await browser.newContext({baseURL, locale, colorScheme, viewport: {width: locale === 'en' ? 1440 : 390, height: 900}});
  const page = await context.newPage(), f = fixture({locale}), original = structuredClone(f.finding.revision.document);
  await mountConversation(page, native, f.dispatch); await page.goto(f.route);
  const chip = page.locator('[data-discussion-context="governance"]:visible').first();
  await expect(chip).toContainText(locale === 'en' ? 'Issue ·' : '問題 ·');
  await expect(chip).not.toContainText(locale === 'en' ? 'Decision ·' : '決策 ·');
  await expect(chip).not.toContainText(/pmgov_|pmrev_/);
  await resolution(page).locator('[data-issue-resolve-open]').click();
  const reason = resolution(page).getByRole('textbox');
  await expect(reason).toHaveValue(new RegExp(locale === 'en' ? 'revision #' : '修訂 #'));
  await expect(reason).not.toHaveValue(/pmgov_|pmrev_/);
  await reason.fill(locale === 'en' ? 'The reviewed decision removes the blocker.' : '已審閱的決策解除了阻礙。');
  await page.reload(); await expect(reason).toHaveValue(locale === 'en' ? 'The reviewed decision removes the blocker.' : '已審閱的決策解除了阻礙。');
  expect(f.state.writes).toHaveLength(0);
  await resolution(page).getByRole('button', {name: locale === 'en' ? 'Resolve issue' : '回報解決', exact: true}).click();
  await expect(resolution(page).locator('[data-issue-resolved]')).toContainText(locale === 'en' ? 'Not assessed' : '未評估');
  expect(f.state.effects).toBe(1); expect(f.state.writes).toHaveLength(1);
  expect(f.state.writes[0].body.params).toEqual({title: original.title, description: original.description, owner_actor: original.owner_actor,
    affected_requirement_ids: original.affected_requirement_ids, trigger: original.trigger, response: original.response,
    review_at: original.review_at, dimension: original.dimension, alternatives: original.alternatives, disambiguation: original.disambiguation,
    source_refs: original.sources.map(({kind, id}: any) => ({kind, id})), proposed_revision_id: null,
    status: 'reported_resolved', rationale: locale === 'en' ? 'The reviewed decision removes the blocker.' : '已審閱的決策解除了阻礙。'});
  expect(f.state.writes[0].body.preconditions).toEqual({expected_version: 1});
  expect(f.state.reads.some(path => path.endsWith('/overview'))).toBe(true);
  await expect(resolution(page)).not.toContainText(/Accepted|Verified|已驗收|已驗證/);
  await context.close();
});

for (const native of [false, true]) for (const [role, assigned, manage, registered, allowed] of [
  ['contributor', true, true, true, true], ['contributor', false, true, true, false], ['reader', true, true, true, false],
  ['owner', false, false, true, false], ['owner', false, true, false, false],
] as const) test(`resolution gate ${role} assigned=${assigned} manage=${manage} registered=${registered} ${native ? 'native' : 'web'}`, async ({page}) => {
  const f = fixture({role, assigned, manage, registered}); await mountConversation(page, native, f.dispatch); await page.goto(f.route);
  await expect(page.locator('[data-proposal-adopted]')).toBeVisible();
  await expect(page.getByRole('button', {name: 'Refresh', exact: true})).toBeEnabled();
  await expect(resolution(page).locator('[data-issue-resolve-open]')).toHaveCount(allowed ? 1 : 0);
  expect(f.state.writes).toHaveLength(0);
});

test('lost resolution acknowledgement and reload recover the original key without a second effect', async ({page}) => {
  const f = fixture(); await mountConversation(page, false, f.dispatch); await page.goto(f.route);
  await resolution(page).locator('[data-issue-resolve-open]').click();
  await resolution(page).getByRole('textbox').fill('My original resolution'); f.state.lose = true;
  await resolution(page).getByRole('button', {name: 'Resolve issue', exact: true}).click();
  await expect(resolution(page)).toContainText('Acknowledgement lost');
  await page.reload(); await resolution(page).getByRole('button', {name: 'Check original request', exact: true}).click();
  await expect(resolution(page).locator('[data-issue-resolved]')).toBeVisible();
  expect(f.state.writes).toHaveLength(2); expect(f.state.writes[1]).toEqual(f.state.writes[0]); expect(f.state.effects).toBe(1);
});

test('a changed finding requires review of its full new baseline and retains the resolution rationale', async ({page}) => {
  const f = fixture(); await mountConversation(page, false, f.dispatch); await page.goto(f.route);
  await resolution(page).locator('[data-issue-resolve-open]').click();
  await resolution(page).getByRole('textbox').fill('Keep my reviewed rationale');
  f.finding.record.version++; f.finding.revision.version++;
  f.finding.revision.document.response = 'Review the updated mitigation';
  await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  const submit = resolution(page).getByRole('button', {name: 'Resolve issue', exact: true});
  await expect(submit).toBeDisabled(); expect(f.state.writes).toHaveLength(0);
  await resolution(page).getByRole('button', {name: 'Review updated baseline', exact: true}).click();
  await expect(resolution(page).getByRole('textbox')).toHaveValue('Keep my reviewed rationale');
  await submit.click(); await expect(resolution(page).locator('[data-issue-resolved]')).toBeVisible();
  expect(f.state.effects).toBe(1); expect(f.state.writes).toHaveLength(1);
  expect(f.state.writes[0].body.preconditions).toEqual({expected_version: 2});
  expect(f.state.writes[0].body.params.response).toBe('Review the updated mitigation');
});

for (const status of [403, 404]) test(`resolution source withdrawal ${status} removes fields and action`, async ({page}) => {
  const f = fixture(); await mountConversation(page, false, f.dispatch); await page.goto(f.route);
  await resolution(page).locator('[data-issue-resolve-open]').click(); await resolution(page).getByRole('textbox').fill('Private resolution draft');
  f.state.denied = status; await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect(resolution(page).locator('[data-issue-resolution]')).toHaveCount(0); await expect(resolution(page)).not.toContainText('Private resolution draft');
  expect(f.state.writes).toHaveLength(0);
});

test('a late finding read cannot revive withdrawn resolution controls after remount', async ({page}) => {
  const f = fixture(); await mountConversation(page, false, f.dispatch); await page.goto(f.route);
  await expect(resolution(page).locator('[data-issue-resolve-open]')).toBeVisible();
  let release!: () => void; f.state.held = new Promise<void>(resolve => {release = resolve;});
  const before = f.state.reads.length;
  await page.getByRole('button', {name: 'Refresh', exact: true}).click();
  await expect.poll(() => f.state.reads.slice(before).some(path => path.includes('/governance/'))).toBe(true);
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/overview`);
  f.state.denied = 404; await page.goto(f.route);
  try {await expect(page.getByRole('button', {name: 'Refresh', exact: true})).toBeEnabled();} finally {release();}
  await expect(resolution(page).locator('[data-issue-resolve-open]')).toHaveCount(0); expect(f.state.writes).toHaveLength(0);
});
