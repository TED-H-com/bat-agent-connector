import {openPmSection} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {mkdir} from 'node:fs/promises';
import {batchFixture,mountBatch,bootBatch,panel,start,proof,handoff,workspaceId,START,RECORD} from './pm-work-batches-fixture.ts';
const review=async(page:any)=>{await handoff(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();await expect(handoff(page).getByRole('heading',{name:'Reviewed implementation base',exact:true})).toBeVisible();
  await handoff(page).getByLabel('Why use this implementation base?',{exact:true}).fill('Implement this original reviewed base');await handoff(page).getByLabel('Instructions for this implementation',{exact:true}).fill('Preserve source identity and verify fixed result.');};
const add=async(page:any)=>{await review(page);await panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true}).click();await expect(start(page).locator('.pm-batch-entry')).toHaveCount(1);};
const fill=async(page:any)=>{await start(page).getByLabel('Reason for this request',{exact:true}).fill('Run the selected bounded work');const value=new Date(Date.now()+3600000);value.setMinutes(value.getMinutes()-value.getTimezoneOffset());await start(page).getByLabel('Deadline (local time)',{exact:true}).fill(value.toISOString().slice(0,16));};
const evidence=async(page:any,f:any)=>{await proof(page).getByLabel('Original batch entry',{exact:true}).selectOption('first');await proof(page).getByLabel('Delivered integration receipt',{exact:true}).selectOption(f.state.deliveries[0].operation_id+':1');await proof(page).getByLabel('Saved GitHub observation',{exact:true}).selectOption(f.state.observations[0].observation_id);await proof(page).getByLabel('Reason for this request',{exact:true}).fill('Match the original delivered revision');};

for(const native of [false,true])test(`mounted ${native?'native':'browser'} workspace selects and starts one fixed batch`,async({page})=>{
  const f=batchFixture();await mountConversation(page,native,f.dispatch);await page.goto('/dashboard/#/pm/'+workspaceId);await openPmSection(page,'requirements','ui_packages');
  await page.locator('[data-pm-work-packages]').getByRole('button',{name:'Restore member access · Preparation 1',exact:true}).click();
  await openPmSection(page,'requirements','ui_handoff');await handoff(page).locator('details>summary').first().click();await review(page);await openPmSection(page,'board','ui_batches');await panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true}).click();await expect(start(page).locator('.pm-batch-entry')).toHaveCount(1);await fill(page);await start(page).locator('button[type=submit]').click();
  await expect(panel(page).getByRole('heading',{name:'Original batch and result evidence',exact:true})).toBeVisible();
  expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].body.action).toBe(START);expect(f.base.state.writes).toHaveLength(0);
  f.state.historyDeny=true;f.base.fixture.base.state.cursor++;
  await expect(panel(page).getByRole('heading',{name:'Original batch and result evidence',exact:true})).toHaveCount(0);
});

test('explicit selection freezes the reviewed existing handoff and bounded request',async({page})=>{
  const f=batchFixture();await mountBatch(page,f);await expect(panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true})).toBeDisabled();expect(f.state.writes).toHaveLength(0);
  await add(page);const selected=await page.evaluate(()=>(window as any).batchSelection());await fill(page);await start(page).locator('button[type=submit]').click();
  await expect(panel(page).getByRole('heading',{name:'Original batch and result evidence',exact:true})).toBeVisible();expect(f.state.writes).toHaveLength(1);const body=f.state.writes[0].body;
  expect(body.action).toBe(START);expect(body.target).toEqual({workspace_id:workspaceId});expect(body.preconditions).toEqual({expected_workspace_version:2,expected_lifecycle_revision:0});
  expect(body.params.entries[0]).toMatchObject({handoff:selected.handoff,depends_on:[],result_condition:{kind:'fixed_revision_verification',profile:'github_reported_checks_v1'}});expect(body.params.entries[0]).not.toHaveProperty('title');
  expect(body.params.entries[0].key).toMatch(/^entry_[0-9a-f]{32}$/);expect(body.params.limits).toMatchObject({max_starts:1,max_parallel:1,max_retries:0});expect(f.base.state.writes).toHaveLength(0);
});
test('handoff edits do not replace selected envelope and duplicate WorkItems are refused',async({page})=>{
  const f=batchFixture();await mountBatch(page,f);await add(page);const original=await page.evaluate(()=>(window as any).batchSelection());await handoff(page).getByLabel('Instructions for this implementation',{exact:true}).fill('Different later instructions');await panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true}).click();await expect(panel(page)).toContainText('This WorkItem is already selected');await fill(page);await start(page).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.params.entries[0].handoff).toEqual(original.handoff);
});
test('draft stable entry and exact source survive reload',async({page})=>{
  const f=batchFixture();await mountBatch(page,f);await add(page);await fill(page);const before=await page.evaluate(()=>Object.values(localStorage).map(value=>{try{return JSON.parse(value);}catch{return null;}}).find(value=>value?.values?.entries)?.values.entries);
  await page.reload();await bootBatch(page);await expect(start(page).locator('.pm-batch-entry')).toHaveCount(1);await start(page).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);expect(JSON.parse(before)[0].key).toBe(f.state.writes[0].body.params.entries[0].key);
});
test('new-start preflight refuses changed CAS without sending or rewriting the frozen request',async({page})=>{
  const f=batchFixture();await mountBatch(page,f);await add(page);await fill(page);f.state.version=3;f.state.revision=2;await start(page).locator('button[type=submit]').click();await expect(start(page)).toContainText('Workspace or lifecycle version changed');expect(f.state.writes).toHaveLength(0);
  const stored=await page.evaluate(()=>Object.values(localStorage).map(value=>{try{return JSON.parse(value);}catch{return null;}}).find(value=>value?.intent?.request.action==='pm.work_batch.start'));expect(stored.intent.request.preconditions).toEqual({expected_workspace_version:2,expected_lifecycle_revision:0});
  await start(page).getByRole('button',{name:'New request',exact:true}).click();await panel(page).getByRole('button',{name:'Use current workspace versions',exact:true}).click();await start(page).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.preconditions).toEqual({expected_workspace_version:3,expected_lifecycle_revision:2});
});
test('lost start reply recovers original key after pause and lifecycle epoch change',async({page})=>{
  const f=batchFixture();await mountBatch(page,f);await add(page);await fill(page);f.state.lost=true;await start(page).locator('button[type=submit]').click();await expect(start(page)).toContainText('Reply unavailable');const original=structuredClone(f.state.writes[0]);f.state.version=4;f.state.revision=7;f.base.fixture.state.stage='paused';await page.reload();await bootBatch(page);await start(page).getByRole('button',{name:'Check original request',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(2);expect(f.state.writes[1]).toEqual(original);expect(f.state.ops.size).toBe(1);
});
test('source withdrawal clears saved entry facts and refuses new starts',async({page})=>{
  const f=batchFixture();await mountBatch(page,f);await add(page);await fill(page);f.base.state.deny=true;await page.evaluate(()=>(window as any).batchOnlyRefresh());await expect(panel(page)).toContainText('Original batch sources are unavailable');await expect(start(page)).toBeHidden();await expect(panel(page).locator('.pm-batch-entry')).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
});
test('awaits empty filtered pages and preserves explicit original selection',async({page})=>{
  const f=batchFixture();f.save();f.state.partial=true;await mountBatch(page,f);expect(f.state.reads.some((path:string)=>path.includes('work-batches?after=10'))).toBe(true);await expect(panel(page).getByLabel('Saved batch',{exact:true})).toHaveValue('');await expect(panel(page)).toContainText('hidden by source access');await panel(page).getByLabel('Saved batch',{exact:true}).selectOption(f.state.rows[0].batch_id);await expect(panel(page).getByRole('heading',{name:'Original batch and result evidence',exact:true})).toBeVisible();f.state.badCursor=true;await page.evaluate(()=>(window as any).batchOnlyRefresh());await expect(panel(page).getByRole('heading',{name:'Original batch and result evidence',exact:true})).toHaveCount(0);
});
test('late source response cannot repopulate withdrawn facts',async({page})=>{
  const f=batchFixture();f.save();await mountBatch(page,f);let release!:()=>void;f.state.gate=new Promise<void>(resolve=>release=resolve);const pending=page.evaluate(()=>(window as any).batchOnlyRefresh());await expect(panel(page)).toContainText('Reading original batch sources');await page.evaluate(()=>(window as any).batchWithdraw());release();await pending;await expect(panel(page).getByRole('heading',{name:'Original batch and result evidence',exact:true})).toHaveCount(0);await expect(panel(page).getByLabel('Saved batch',{exact:true}).locator('option')).toHaveCount(0);
});
test('evidence selection fixes original integration source and observation digest',async({page})=>{
  const f=batchFixture();f.evidence();await mountBatch(page,f);await evidence(page,f);await proof(page).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body).toEqual({action:RECORD,target:{workspace_id:workspaceId,batch_id:f.state.rows[0].batch_id},params:{evidence:[{entry_key:'first',condition:{kind:'fixed_revision_verification',profile:'github_reported_checks_v1'},reference:{integration_operation_id:f.state.deliveries[0].operation_id,source_seq:1,candidate_commit:'b'.repeat(40),observation_id:f.state.observations[0].observation_id,document_digest:'f'.repeat(64)}}],rationale:'Match the original delivered revision'},preconditions:{expected_version:0}});await expect(panel(page)).toContainText('requirement acceptance remains unassessed');await panel(page).getByText('Recorded fixed revision checks passed; requirement acceptance remains unassessed.',{exact:true}).click();await expect(panel(page)).toContainText('Original observation digest: '+'f'.repeat(64));await expect(panel(page)).toContainText('Verified candidate revision: '+'b'.repeat(40));
});
test('unrelated integration receipts cannot become evidence choices',async({page})=>{
  const f=batchFixture();f.evidence();f.state.deliveries[0].integration.sources[0].source_id='op_'+'0'.repeat(32);await mountBatch(page,f);await proof(page).getByLabel('Original batch entry',{exact:true}).selectOption('first');await expect(proof(page).getByLabel('Delivered integration receipt',{exact:true}).locator('option')).toHaveCount(1);await expect(proof(page).locator('button[type=submit]')).toBeDisabled();
});
test('lost evidence request retains original revision and key after pause',async({page})=>{
  const f=batchFixture();f.evidence();await mountBatch(page,f);await evidence(page,f);f.state.lost=true;await proof(page).locator('button[type=submit]').click();await expect(proof(page)).toContainText('Reply unavailable');const original=structuredClone(f.state.writes[0]);f.base.fixture.state.stage='paused';f.state.revision=8;await page.reload();await bootBatch(page);await proof(page).getByRole('button',{name:'Check original request',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(2);expect(f.state.writes.at(-1)).toEqual(original);expect(f.state.ops.size).toBe(1);
});
test('multiple entries require original upstream evidence and reject cycles or removed prerequisites',async({page})=>{
  const f=batchFixture();await mountBatch(page,f);await add(page);const second=f.base.fixture.addPackage();second.document.title='Second bounded work';second.work_item_id=second.document.work_item_id=second.document.context.work_ref.work_item_id='wi_'+'8'.repeat(20);
  await page.evaluate(id=>(window as any).batchSelect(id),second.package_id);await review(page);await panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true}).click();await expect(start(page).locator('.pm-batch-entry')).toHaveCount(2);
  const rows=start(page).locator('.pm-batch-entry');await rows.nth(0).getByText('Prerequisites in this selection',{exact:true}).click();await rows.nth(1).getByText('Prerequisites in this selection',{exact:true}).click();
  await rows.nth(1).getByRole('checkbox').check();await rows.nth(0).getByRole('checkbox').check();await expect(start(page).locator('button[type=submit]')).toBeDisabled();
  await rows.nth(0).getByRole('checkbox').uncheck();await fill(page);await expect(start(page).locator('button[type=submit]')).toBeEnabled();
  const stored=await page.evaluate(()=>Object.values(localStorage).map(value=>{try{return JSON.parse(value);}catch{return null;}}).find(value=>value?.values?.entries));const entries=JSON.parse(stored.values.entries);
  expect(entries[1].depends_on).toEqual([{entry_key:entries[0].key,condition:entries[0].result_condition}]);
  await rows.nth(0).getByRole('button',{name:'Remove this entry',exact:true}).click();await expect(start(page).locator('button[type=submit]')).toBeDisabled();await rows.nth(0).getByText('Prerequisites in this selection',{exact:true}).click();await rows.nth(0).getByRole('button',{name:'Clear missing prerequisite',exact:true}).click();await expect(start(page).locator('button[type=submit]')).toBeEnabled();
});

test('proof source pagination is awaited and refresh retains exact selectors without stale source labels',async({page})=>{
  const f=batchFixture();f.evidence();f.state.observationPartial=true;f.state.deliveryPartial=true;await mountBatch(page,f);await evidence(page,f);
  expect(f.state.reads.some((path:string)=>path.includes('github-observations?after=3'))).toBe(true);expect(f.state.reads.some((path:string)=>path.includes('deliveries?after=4'))).toBe(true);
  await page.evaluate(()=>(window as any).batchOnlyRefresh());await expect(proof(page).getByLabel('Original batch entry',{exact:true})).toHaveValue('first');await expect(proof(page).getByLabel('Delivered integration receipt',{exact:true})).toHaveValue(f.state.deliveries[0].operation_id+':1');
  f.state.deny=true;await page.evaluate(()=>(window as any).batchOnlyRefresh());await expect(panel(page)).toContainText('Original batch sources are unavailable');await expect(proof(page).getByLabel('Delivered integration receipt',{exact:true}).locator('option')).toHaveCount(0);await expect(proof(page)).toBeHidden();f.state.deny=false;await page.evaluate(()=>(window as any).batchOnlyRefresh());await expect(proof(page).getByLabel('Original batch entry',{exact:true})).toHaveValue('first');await expect(proof(page).getByLabel('Delivered integration receipt',{exact:true})).toHaveValue(f.state.deliveries[0].operation_id+':1');
});

test('source withdrawal during fresh-start preflight sends no operation',async({page})=>{
  const f=batchFixture();await mountBatch(page,f);await add(page);await fill(page);f.base.state.deny=true;await start(page).locator('button[type=submit]').click();await expect(panel(page)).toContainText('Original batch sources are unavailable');expect(f.state.writes).toHaveLength(0);
});

test('re-reviewed handoff becomes explicitly selectable without replacing previous entry',async({page})=>{
  const f=batchFixture();await mountBatch(page,f);await add(page);f.base.state.version=4;await review(page);await expect(panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true})).toBeDisabled();
  await handoff(page).getByRole('button',{name:'Use this reviewed project version',exact:true}).click();await expect(panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true})).toBeEnabled();await fill(page);await start(page).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.params.entries[0].handoff.preconditions.expected_project_version).toBe(3);
});

for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`batch view ${locale} ${width}`,async({page},info)=>{
  const f=batchFixture();f.evidence();await page.setViewportSize({width,height:900});await mountBatch(page,f,locale);expect(await page.evaluate(()=>[...document.querySelectorAll('main *')].filter(node=>node.getBoundingClientRect().right>innerWidth+1).map(node=>({tag:node.tagName,class:node.className,text:node.textContent?.slice(0,80)})))).toEqual([]);expect(await panel(page).innerText()).not.toMatch(/pmb_[a-z_]+/);const output=process.env.BATC_PM_BATCH_SCREENSHOTS||info.outputPath('screenshots');await mkdir(output,{recursive:true});await page.screenshot({path:`${output}/batch-${locale}-${width}.png`,fullPage:true});
});

test('reader and stopped lifecycle prevent new batch selection while original results remain readable',async({page})=>{
  const f=batchFixture();f.evidence();f.base.fixture.base.state.role='reader';await mountBatch(page,f);await expect(panel(page)).toContainText('Original batch and result evidence');await expect(panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true})).toBeDisabled();await expect(proof(page).locator('button[type=submit]')).toBeDisabled();expect(f.state.writes).toHaveLength(0);
});

test('observed runner result remains readable without invented verification ID inputs',async({page})=>{
  const f=batchFixture();const row=f.evidence();row.entries[0].result_condition={kind:'fixed_revision_verification',profile:'observed_runner_v1',command_digest:'a'.repeat(64)};await mountBatch(page,f);
  await expect(panel(page)).toContainText('Observed runner command');await expect(panel(page)).toContainText('registered source workflow');await expect(proof(page)).toHaveCount(0);expect(f.state.reads.some((path:string)=>path.includes('/github-observations'))).toBe(false);
});

test('parallel capacity and absolute future deadline are validated before a request is saved',async({page})=>{
  const f=batchFixture();await mountBatch(page,f);await add(page);await fill(page);await start(page).getByLabel('Maximum parallel results',{exact:true}).fill('2');await start(page).locator('button[type=submit]').click();await expect(start(page)).toContainText('Parallel capacity must not exceed the start limit');expect(f.state.writes).toHaveLength(0);
  await start(page).getByLabel('Maximum parallel results',{exact:true}).fill('1');await start(page).getByLabel('Deadline (local time)',{exact:true}).fill('2020-01-01T12:00');await start(page).locator('button[type=submit]').click();await expect(start(page)).toContainText('Choose a future deadline within the next 24 hours');expect(f.state.writes).toHaveLength(0);
});

 test('new evidence version requires explicit rebase without replacing selected originals',async({page})=>{
  const f=batchFixture();f.evidence();await mountBatch(page,f);await evidence(page,f);await expect(proof(page).getByRole('button',{name:'Use current evidence revision',exact:true})).toBeHidden();f.state.rows[0].version=3;await page.evaluate(()=>(window as any).batchOnlyRefresh());
  await expect(proof(page).locator('button[type=submit]')).toBeDisabled();await proof(page).getByRole('button',{name:'Use current evidence revision',exact:true}).click();await proof(page).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.preconditions).toEqual({expected_version:3});expect(f.state.writes[0].body.params.evidence[0].reference.observation_id).toBe(f.state.observations[0].observation_id);
});
