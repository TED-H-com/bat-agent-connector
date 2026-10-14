async function openRead(node:any) {await node.first().evaluate((el:Element)=>{for(let p=(el.tagName==='SUMMARY'?el.parentElement?.parentElement:el.parentElement);p;p=p.parentElement)if(p instanceof HTMLDetailsElement)p.open=true;});await node.first().click();}
import {test, expect, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {action, governanceFixture, mount, boot, openAction, reason, snapshotId, proposedId, assumptionId, adoptionId} from './pm-governance-fixture';

const view = (page: Page) => page.locator('[data-pm-governance]');
async function selectChange(page: Page, f: ReturnType<typeof governanceFixture>) {
  await page.locator(`[data-pm-governance-row="${f.state.records[1].record.record_id}"]`).getByRole('button').click();
  await expect(action(page, 'decide')).toHaveCount(1);
}
async function newFinding(page: Page) {
  await openAction(page, 'record'); const editor = action(page, 'record');
  await editor.getByLabel('Explicit root cause key', {exact: true}).fill('mail-delivery-uncertainty');
  await editor.getByLabel('Finding title', {exact: true}).fill('Verify the recovery mail route');
  await editor.getByLabel('What was observed and why it matters', {exact: true}).fill('A successful password reset needs a verified mail route.');
  await editor.getByRole('checkbox').first().check(); await reason(page, 'record');
  return editor;
}

test('attention list keeps unknown verification and source status without tabs or open blank forms', async ({page}) => {
  const f = governanceFixture(); await mount(page, f);
  await expect(view(page).getByRole('tab')).toHaveCount(0);
  await expect(view(page)).toContainText('A reported resolution does not verify delivery');
  await expect(action(page, 'record')).toBeHidden(); await expect(action(page, 'revise')).toBeHidden();
  await openRead(page.locator('[data-pm-governance-detail]').getByText('Fixed sources', {exact: true}));
  await expect(page.locator('[data-pm-governance-detail]')).toContainText('Agent assumption');
  await expect(view(page)).toContainText('Not scheduled'); expect(f.state.writes).toEqual([]);
});
test('record pins snapshot and current plan version with no invented fields or dispatch', async ({page}) => {
  const f = governanceFixture(); await mount(page, f); const editor = await newFinding(page);
  await editor.getByRole('button', {name: 'Save finding', exact: true}).click();
  await expect(page.locator('[data-pm-governance-detail] h3').first()).toHaveText('Verify the recovery mail route');
  const request = f.state.writes[0].body;
  expect(request.params.source_refs).toEqual([{kind: 'plan_input', id: assumptionId}]);
  expect(request.params.snapshot_id).toBe(snapshotId); expect(request.preconditions).toEqual({expected_plan_version: 2});
  expect(request.params.review_at).toBeNull(); expect(request.params.response).toBeNull(); expect(request.params).not.toHaveProperty('_baseline');
  expect(f.state.writes.map((row: any) => row.body.action)).toEqual(['pm.governance.record']);
});
test('revision preserves original sources and reports resolution without asserting verification', async ({page}) => {
  const f = governanceFixture(); await mount(page, f); await openAction(page, 'revise'); const editor = action(page, 'revise');
  await expect(editor.getByRole('checkbox').first()).toBeChecked(); await expect(editor.getByRole('checkbox').first()).toBeDisabled();
  await editor.getByLabel('Management status', {exact: true}).selectOption('reported_resolved'); await reason(page, 'revise');
  await editor.getByRole('button', {name: 'Save finding revision', exact: true}).click();
  await expect(view(page)).toContainText('Reported resolved'); await expect(view(page)).toContainText('A reported resolution does not verify delivery');
  expect(f.state.writes[0].body.params.source_refs).toEqual([{kind: 'plan_input', id: assumptionId}]);
  expect(f.state.writes[0].body.params).not.toHaveProperty('snapshot_id'); expect(f.state.writes[0].body.preconditions).toEqual({expected_version: 1});
});
test('direction decision keeps exact proposal and scope adoption separate', async ({page}) => {
  const f = governanceFixture(); await mount(page, f); await selectChange(page, f); await reason(page, 'decide');
  await action(page, 'decide').getByRole('button', {name: 'Save direction decision', exact: true}).click();
  await expect(page.locator('[data-pm-governance-detail]')).toContainText('Direction adopted');
  await expect(page.locator('[data-pm-governance-detail]')).toContainText('does not adopt a plan or authorize execution');
  expect(f.state.records[1].revision.document.scope_applied).toBe(false);
  expect(f.state.writes.map((row: any) => row.body.action)).toEqual(['pm.governance.decide']);
});
test('actual adoption requires reading the matching original operation', async ({page}) => {
  const f = governanceFixture(); f.state.records[1].revision.document.decision = {disposition: 'adopted_direction', rationale: 'Use existing provider', recorded_by: 'owner'};
  await mount(page, f); await selectChange(page, f); await reason(page, 'adoption.link'); const editor = action(page, 'adoption.link');
  await editor.getByLabel('Adoption operation', {exact: true}).fill(adoptionId);
  await expect(editor.getByRole('button', {name: 'Link verified adoption', exact: true})).toBeDisabled();
  await editor.getByRole('button', {name: 'Read original adoption', exact: true}).click();
  await editor.getByRole('button', {name: 'Link verified adoption', exact: true}).click();
  await expect(page.locator('[data-pm-governance-detail]')).toContainText('Historical');
  expect(f.state.writes[0].body.params.adoption_operation_id).toBe(adoptionId);
  expect(f.state.writes.map((row: any) => row.body.action)).toEqual(['pm.governance.adoption.link']);
});
test('mismatched adoption does not enable link', async ({page}) => {
  const f = governanceFixture(); f.state.records[1].revision.document.decision = {disposition: 'adopted_direction'}; f.state.adoptionMismatch = true;
  await mount(page, f); await selectChange(page, f); await reason(page, 'adoption.link'); const editor = action(page, 'adoption.link');
  await editor.getByLabel('Adoption operation', {exact: true}).fill(adoptionId); await editor.getByRole('button', {name: 'Read original adoption', exact: true}).click();
  await expect(editor).toContainText('does not prove adoption'); await expect(editor.getByRole('button', {name: 'Link verified adoption', exact: true})).toBeDisabled(); expect(f.state.writes).toEqual([]);
});
test('lost decision reply preserves key and original version across changed state and reload', async ({page}) => {
  const f = governanceFixture(); await mount(page, f); await selectChange(page, f); await reason(page, 'decide'); f.state.lost = true;
  await action(page, 'decide').getByRole('button', {name: 'Save direction decision', exact: true}).click(); await expect(action(page, 'decide')).toContainText('Reply unavailable');
  const request = structuredClone(f.state.writes[0]); await page.reload(); await boot(page);
  await action(page, 'decide').getByRole('button', {name: 'Check original request', exact: true}).click();
  expect(f.state.writes[1]).toEqual(request); expect(f.state.ops.size).toBe(1);
});
test('changed plan head freezes unsent creation and preserves draft until explicit baseline review', async ({page}) => {
  const f = governanceFixture(); await mount(page, f); await newFinding(page); f.state.planVersion = 9;
  await page.evaluate(() => (window as any).refreshGovernance()); await expect(action(page, 'record').getByRole('button', {name: 'Save finding', exact: true})).toBeDisabled();
  await page.reload(); await boot(page); await openAction(page, 'record');
  await expect(action(page, 'record').getByLabel('Finding title', {exact: true})).toHaveValue('Verify the recovery mail route');
  await expect(action(page, 'record').getByRole('button', {name: 'Save finding', exact: true})).toBeDisabled();
  await page.getByRole('button', {name: 'Review updated baseline', exact: true}).click(); await openAction(page, 'record');
  await action(page, 'record').getByRole('button', {name: 'Save finding', exact: true}).click(); expect(f.state.writes[0].body.preconditions).toEqual({expected_plan_version: 9});
});
test('read failure hides source facts while keeping local draft for later authorized recovery', async ({page}) => {
  const f = governanceFixture(); await mount(page, f); await reason(page, 'revise', 'Keep this reasoning'); f.state.failRead = true;
  await page.evaluate(() => (window as any).refreshGovernance()); await expect(view(page)).toContainText('Workspace access removed');
  await expect(page.locator('[data-pm-governance-detail]')).toHaveCount(0); await expect(action(page, 'revise')).toBeHidden();
  f.state.failRead = false; await page.evaluate(() => (window as any).refreshGovernance()); await openAction(page, 'revise');
  await expect(action(page, 'revise').getByLabel('Reason for this record', {exact: true})).toHaveValue('Keep this reasoning'); expect(f.state.writes).toEqual([]);
});
for (const failed of [true, 'large']) test(`source failure ${failed} does not leave old fixed content visible`, async ({page}) => {
  const f = governanceFixture(); await mount(page, f); f.state.snapshotFailed = failed;
  await page.evaluate(() => (window as any).refreshGovernance()); await expect(view(page)).toContainText(failed === 'large' ? 'exceeds the read limit' : 'Original source access removed');
  await expect(page.locator('[data-pm-governance-detail]')).toHaveCount(0); expect(f.state.writes).toEqual([]);
});
test('current writer and actual owner roles gate edits and decisions without admin impersonation', async ({page}) => {
  const f = governanceFixture(); f.state.actor = 'developer'; f.state.role = 'contributor'; f.state.admin = true;
  await mount(page, f); await selectChange(page, f); await openAction(page, 'decide'); await openAction(page, 'revise');
  await expect(action(page, 'decide').locator('button[type=submit]')).toBeDisabled(); await expect(action(page, 'revise').locator('button[type=submit]')).toBeDisabled();
  f.state.records[1].revision.document.owner_actor = 'developer'; f.state.records[1].record.version = 2; f.state.records[1].revision.version = 2;
  await page.evaluate(() => (window as any).refreshGovernance()); await reason(page, 'revise');
  await expect(action(page, 'revise').locator('button[type=submit]')).toBeEnabled();
  f.state.manage = false; await page.evaluate(() => (window as any).refreshGovernance()); await expect(action(page, 'revise').locator('button[type=submit]')).toBeDisabled();
});
test('administrator without explicit membership cannot impersonate a direction owner', async ({page}) => {
  const f = governanceFixture(); f.state.actor = 'administrator'; f.state.role = 'owner'; f.state.admin = true;
  await mount(page, f); await selectChange(page, f); await openAction(page, 'decide');
  await expect(action(page, 'decide').locator('button[type=submit]')).toBeDisabled(); expect(f.state.writes).toEqual([]);
});
test('whole-record pages consume invisible sequences and do not treat incomplete list as total', async ({page}) => {
  const f = governanceFixture(); f.state.hiddenFirst = true; f.state.pageSize = 1;
  await mount(page, f); await expect(view(page)).toContainText('More records remain'); await page.getByRole('button', {name: 'More findings', exact: true}).click();
  await expect(page.locator('[data-pm-governance-row]')).toHaveCount(1); await expect(view(page)).toContainText('Use the existing mail provider');
  expect(f.state.reads).toContain('/pm/workspaces/' + f.config().workspace.workspace_id + '/governance?after=1&limit=20'); expect(f.state.writes).toEqual([]);
});
test('history pages preserve old text and stop on source access loss', async ({page}) => {
  const f = governanceFixture(), rid = f.state.records[0].record.record_id; f.state.historyPageSize = 1;
  f.state.history[rid].push({...structuredClone(f.state.history[rid][0]), version: 2, sequence: 2, document: {...f.state.history[rid][0].document, rationale: 'Earlier explicit decision'}});
  await mount(page, f); await openRead(page.getByText('History', {exact: true}));
  await page.getByRole('button', {name: 'More history', exact: true}).click(); await openRead(page.getByText('#2 · owner · Finding recorded', {exact: true}));
  await expect(view(page)).toContainText('Earlier explicit decision'); expect(f.state.writes).toEqual([]);
});
test('lazy history authorization failure removes previously shown facts', async ({page}) => {
  const f = governanceFixture(); await mount(page, f); f.state.failRead = true;
  await openRead(page.getByText('History', {exact: true})); await expect(view(page)).toContainText('Workspace access removed');
  await expect(page.locator('[data-pm-governance-detail]')).toHaveCount(0); expect(f.state.writes).toEqual([]);
});
test('older delayed success cannot repopulate a newer failed view', async ({page}) => {
  const f = governanceFixture(); await mount(page, f); let release!: () => void;
  f.state.defer = new Promise<void>(resolve => {release = resolve;});
  await page.evaluate(() => {(window as any).olderRead = (window as any).refreshGovernance();});
  await expect.poll(() => f.state.defer === null).toBe(true); f.state.failRead = true;
  await page.evaluate(() => (window as any).refreshGovernance()); await expect(view(page)).toContainText('Workspace access removed');
  f.state.failRead = false; release(); await page.evaluate(() => (window as any).olderRead);
  await expect(page.locator('[data-pm-governance-detail]')).toHaveCount(0); await expect(view(page)).toContainText('Workspace access removed');
});
test('nonadvancing page or wrong workspace fails closed', async ({page}) => {
  const f = governanceFixture(); await mount(page, f); f.state.badCursor = true;
  await page.evaluate(() => (window as any).refreshGovernance()); await expect(page.locator('[data-pm-governance-row]')).toHaveCount(0);
  await expect(action(page, 'record')).toBeHidden(); expect(f.state.writes).toEqual([]);
});
test('snapshot metadata follows advancing pages before offering fixed sources', async ({page}) => {
  const f = governanceFixture(); f.state.snapshotsPaged = true; await mount(page, f); await newFinding(page);
  expect(f.state.reads.some((value: string) => value.endsWith('/snapshots?after=10&limit=50'))).toBe(true);
  await expect(action(page, 'record').getByRole('button', {name: 'Save finding', exact: true})).toBeEnabled();
});
test('snapshot without adopted baseline keeps creation editable without inventing a baseline', async ({page}) => {
  const f = governanceFixture(); f.state.records = []; f.state.snapshots[0].baseline.document = null;
  await mount(page, f); await openAction(page, 'record');
  await expect(action(page, 'record')).toContainText('containing an adopted plan');
  await expect(action(page, 'record').getByLabel('Source snapshot', {exact: true})).toBeEnabled();
  await expect(action(page, 'record').getByRole('button', {name: 'Save finding', exact: true})).toBeDisabled(); expect(f.state.writes).toEqual([]);
});
test('new proposed plan must be inspected before recording and does not silently become adopted', async ({page}) => {
  const f = governanceFixture(); await mount(page, f); const editor = await newFinding(page);
  await editor.getByLabel('Kind', {exact: true}).selectOption('change'); await editor.getByText('Alternatives & related', {exact: true}).click();
  await editor.getByLabel('Proposed plan version', {exact: true}).fill(proposedId);
  await expect(editor.getByRole('button', {name: 'Save finding', exact: true})).toBeDisabled(); await editor.getByRole('button', {name: 'Read proposed revision', exact: true}).click();
  await editor.getByRole('button', {name: 'Save finding', exact: true}).click(); expect(f.state.writes[0].body.params.proposed_revision_id).toBe(proposedId);
  expect(f.state.writes[0].body.params).not.toHaveProperty('scope_applied'); expect(f.state.writes[0].body.params).not.toHaveProperty('decision');
});
test('reported drift requires both observed evidence and fixed plan source plus dimension', async ({page}) => {
  const f = governanceFixture(); await mount(page, f); const editor = await newFinding(page);
  await editor.getByLabel('Kind', {exact: true}).selectOption('drift'); await expect(editor).toContainText('an observed evidence fact');
  await expect(editor.getByRole('button', {name: 'Save finding', exact: true})).toBeDisabled();
  await editor.getByText('Response & impact', {exact: true}).click(); await editor.getByLabel('Affected dimension', {exact: true}).selectOption('quality');
  await editor.getByRole('checkbox', {name: /^Evidence ·/}).check(); await editor.getByRole('button', {name: 'Save finding', exact: true}).click();
  expect(f.state.writes[0].body.params.dimension).toBe('quality'); expect(f.state.writes[0].body.params.source_refs.map((row: any) => row.kind)).toEqual(['plan_input', 'evidence']);
});
test('same root cause shows existing fact without claiming the new wording was applied', async ({page}) => {
  const f = governanceFixture(); await mount(page, f); const editor = await newFinding(page);
  await editor.getByLabel('Explicit root cause key', {exact: true}).fill('cause-1'); await editor.getByRole('button', {name: 'Save finding', exact: true}).click();
  await expect(view(page)).toContainText('The original finding is shown'); await expect(page.locator('[data-pm-governance-detail]')).toContainText('Mail delivery remains unverified');
  expect(f.state.records).toHaveLength(2);
});
test('literal untrusted text stays text and fixed IDs wrap', async ({page}) => {
  const f = governanceFixture(); f.state.records[0].revision.document.description = '<script>window.bad=true</script> ' + '長文字'.repeat(80);
  await mount(page, f); await expect(page.locator('[data-pm-governance-detail]')).toContainText('<script>');
  expect(await page.evaluate(() => (window as any).bad)).toBeUndefined(); await expect(view(page).locator('script')).toHaveCount(0);
});
test('focused unsent draft survives an unchanged refresh', async ({page}) => {
  const f = governanceFixture(); await mount(page, f); await reason(page, 'revise', 'Keep focus and content');
  const field = action(page, 'revise').getByLabel('Reason for this record', {exact: true}); await page.evaluate(() => (window as any).refreshGovernance());
  await expect(field).toBeFocused(); await expect(field).toHaveValue('Keep focus and content');
});
for (const locale of ['en', 'zh-TW']) for (const width of [390, 768, 1440]) test(`governance layout ${locale} ${width}`, async ({page}, info) => {
  const f = governanceFixture(); await page.setViewportSize({width, height: 900}); await mount(page, f, locale);
  await expect(view(page).locator('[data-pm-governance-record]')).toHaveText(locale === 'en' ? 'Record finding' : '記錄新事項');await expect(view(page).getByRole('heading', {level: 2})).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const out = process.env.BATC_PM_GOVERNANCE_SCREENSHOTS || info.outputPath('screenshots'); await mkdir(out, {recursive: true});
  await page.screenshot({path: `${out}/governance-${locale}-${width}.png`, fullPage: true});
});
for (const locale of ['en', 'zh-TW']) test(`expanded governance form at mobile width ${locale}`, async ({page}, info) => {
  const f = governanceFixture(); await page.setViewportSize({width: 390, height: 844}); await mount(page, f, locale); await openAction(page, 'record');
  await action(page, 'record').locator('details').evaluateAll(nodes => nodes.forEach(node => (node as HTMLDetailsElement).open = true));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const out = process.env.BATC_PM_GOVERNANCE_SCREENSHOTS || info.outputPath('screenshots'); await mkdir(out, {recursive: true});
  await action(page, 'record').screenshot({path: `${out}/governance-form-${locale}-390.png`});
});

 test('record action focuses the finding form, chooses requirement names and formats review time', async ({page}) => {
  const f=governanceFixture(); await mount(page,f);
  await page.locator('[data-pm-governance-record]').click();
  const editor=action(page,'record'); await expect(editor).toBeVisible(); await expect(editor.getByLabel('Kind',{exact:true})).toBeFocused();
  await newFinding(page); const response=editor.locator('details').filter({has:page.getByText('Response & impact',{exact:true})}); await response.locator('summary').click();
  await editor.getByRole('group',{name:'Affected requirements',exact:true}).getByRole('checkbox').check();
  const when=editor.getByLabel('Review time',{exact:true}); await expect(when).toHaveAttribute('type','datetime-local'); await when.fill('2026-11-01T14:30');
  await expect(editor.locator('time')).toBeVisible(); expect(f.state.writes).toEqual([]);
  await editor.getByRole('button',{name:'Save finding',exact:true}).click();
  expect(f.state.writes[0].body.params.affected_requirement_ids).toEqual(['account.recovery']);
  expect(f.state.writes[0].body.params.review_at).toMatch(/^2026-11-01T.*Z$/);
 });

test('fixed work source choices use titles while retaining their binding IDs',async({page})=>{
  const f=governanceFixture(),snapshot=f.state.snapshots[0];
  snapshot.work=[{work_item_id:'wi_'+'1'.repeat(20),title:'Verify account recovery',binding:{binding_id:'pmwb_'+'1'.repeat(32),work_item_id:'wi_'+'1'.repeat(20)}}];
  await mount(page,f);await openAction(page,'record');
  const editor=action(page,'record'),choice=editor.getByRole('checkbox',{name:/Verify account recovery/});
  await expect(choice).toBeVisible();await choice.check();
  expect(await page.evaluate(id=>Object.values(localStorage).some(text=>{try{return JSON.parse(text).values?.source_refs===JSON.stringify([{kind:'work_binding',id}]);}catch{return false;}}),'pmwb_'+'1'.repeat(32))).toBe(true);
  expect(f.state.writes).toEqual([]);
});
