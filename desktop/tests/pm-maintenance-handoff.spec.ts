import {expect, test} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {openPmSection} from './pm-navigation.ts';
import {demoFixture, demoRoute} from './demo/index.ts';
import {demoProject, hexId} from './demo/project.ts';
import {registerIds} from './demo/register.ts';

const project = demoProject();
const ids = registerIds(project);
const repairId = hexId('pmrp_', 1, 32);

function ownerDispatch() {
  const fixture = demoFixture();
  const writes: {body: any, idempotency_key: string}[] = [];
  const receipts = new Map<string, any>();
  let lose = false;
  const dispatch = async (input: any) => {
    const path = new URL(input.path, 'http://fixture').pathname;
    const workspace = `/pm/workspaces/${fixture.ids.workspaceId}`;
    if (path === '/capabilities' || path === '/bootstrap') {
      const result = await fixture.dispatch(input);
      const caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.scopes = [...new Set([...(caps.scopes || []), 'manage', 'start'])];
      for (const action of ['pm.maintenance.repair', 'pm.maintenance.repair.evidence.record']) {
        if (!caps.actions.some((row: {action: string}) => row.action === action)) caps.actions.push({action, allowed: true});
      }
      return result;
    }
    if (path === '/pm/workspaces' || path === workspace) {
      const result = await fixture.dispatch(input);
      const rows = path === '/pm/workspaces' ? result.data?.workspaces || [] : [result.data?.workspace].filter(Boolean);
      for (const row of rows) if (row.membership) row.membership = {...row.membership, role: 'owner'};
      return result;
    }
    if ((input.method || 'GET') === 'POST' && path === '/operations') {
      writes.push(structuredClone({body: input.body, idempotency_key: input.idempotency_key}));
      const key = String(input.idempotency_key || '');
      if (lose && !receipts.has(key)) return {status: 503, data: {error: {code: 'LOST_REPLY', message: 'Reply unavailable'}}};
      let operation = receipts.get(key);
      if (!operation) {
        operation = {operation_id: hexId('op_', 40 + receipts.size, 32), actor: fixture.ids.actor, idempotency_key: key,
          action: input.body?.action, target: input.body?.target, params: input.body?.params, preconditions: input.body?.preconditions,
          status: 'succeeded', result: input.body?.action === 'pm.maintenance.repair' ? {repair_id: repairId} : {recorded: true}};
        receipts.set(key, operation);
      }
      return {status: 200, data: {operation}};
    }
    if ((input.method || 'GET') === 'GET' && /^\/operations\/op_[0-9a-f]{32}$/.test(path)) {
      const operation = [...receipts.values()].find(row => row.operation_id === path.slice('/operations/'.length));
      if (operation) return {status: 200, data: {operation}};
    }
    return fixture.dispatch(input);
  };
  return {dispatch, writes, lose: (value: boolean) => {lose = value;}};
}

async function openFinding(page: import('@playwright/test').Page) {
  await page.goto(demoRoute());
  await openPmSection(page, 'issues', 'ui_governance');
  const row = page.locator(`[data-row-key="${ids.finding}"]`);
  await expect(row).toBeVisible();
  await row.focus();
  await row.press('Enter');
  await expect(page.locator('[data-reg-inspector]')).toBeVisible();
}

test('Turn into work binds the finding and recovers the original key', async ({page}) => {
  const gate = ownerDispatch();
  await mountConversation(page, false, gate.dispatch);
  await openFinding(page);
  await expect(page.locator('[data-pm-section="ui_governance"] > details')).toHaveAttribute('open', '');
  await page.locator('[data-reg-action="pm.maintenance.repair"]').click();
  await expect(page.locator('[data-pm-section="ui_governance"] > details')).not.toHaveAttribute('open', '');
  await expect(page.locator('[data-pm-section="ui_repairs"] > details')).toHaveAttribute('open', '');
  const repairs = page.locator('[data-pm-maintenance-repairs]');
  await expect(repairs.locator('[data-pm-repair-finding]')).toHaveValue(ids.findingSource);
  await expect(repairs.getByLabel('Adopted repair rule', {exact: true})).toHaveValue('dependency');
  const start = repairs.locator('[data-pm-action="pm.maintenance.repair"]');
  await expect(start.locator('button[type=submit]')).toBeEnabled();
  await expect(repairs).toContainText('30 min');
  expect(gate.writes).toHaveLength(0);
  gate.lose(true);
  await start.locator('button[type=submit]').click();
  await expect(start).toContainText('Reply unavailable');
  expect(gate.writes).toHaveLength(1);
  await page.reload();
  await openPmSection(page, 'issues', 'ui_repairs');
  const again = page.locator('[data-pm-action="pm.maintenance.repair"]');
  const fold = page.locator('[data-repair-start]');
  if (!await fold.evaluate(node => (node as HTMLDetailsElement).open)) await fold.locator(':scope > summary').click();
  await again.getByRole('button', {name: 'Check original request', exact: true}).click();
  await expect.poll(() => gate.writes.length).toBe(2);
  expect(gate.writes[1].idempotency_key).toBe(gate.writes[0].idempotency_key);
  expect(gate.writes[1].body).toEqual(gate.writes[0].body);
});

test('Record evidence selects the saved repair without writing', async ({page}) => {
  const gate = ownerDispatch();
  await mountConversation(page, false, gate.dispatch);
  await openFinding(page);
  await page.locator('[data-reg-action="pm.maintenance.repair.evidence.record"]').click();
  const repairs = page.locator('[data-pm-maintenance-repairs]');
  await expect(repairs.getByLabel('Saved repair', {exact: true})).toHaveValue(repairId);
  await expect(repairs.getByLabel('Original result evidence', {exact: true})).toBeFocused();
  expect(gate.writes).toHaveLength(0);
});
