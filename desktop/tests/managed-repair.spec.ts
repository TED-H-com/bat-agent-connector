import {test, expect} from '@playwright/test';
import {publishedFixture, selected, dispatchProject, publishedId, publishedSha} from './repository-start-fixture';
import {attentionFixture} from './attention-fixture';
const wid = 'wi_'+'7'.repeat(20), repairId = 'op_'+'8'.repeat(32), failedId = 'op_'+'9'.repeat(32);
const fingerprint = '6'.repeat(64), evidenceDigest = '5'.repeat(64);
const fixedPrompt = 'Repair only this recorded failure.\nKeep its exact central evidence.';
const form = (page: any) => page.locator('[data-published-start]');
const prompt = (page: any) => form(page).getByRole('textbox', {name:'Original instructions', exact:true});
const repair = (page: any) => page.locator('[data-repair-panel]');
const source = {kind:'discovery', host:'demo', profile_id:'installed-default'};
async function fixture(page: any, native: boolean, options: any = {}) {
  const item = {...attentionFixture().data.items[1], work_item_id:wid, project_id:dispatchProject,operation_id:repairId};
  return publishedFixture(page, native, {dispatch:true, bindings:[selected], scopes:['observe','start','manage'],
    repairPosts:[], repairReads:[], recordReads:0, repairOperation:{operation_id:repairId,action:'repair.create',target:{project_id:dispatchProject}}, badEvidence:false, missingRepair:false, failRepair:false,
    record:{version:1, project_id:dispatchProject, work_item_id:wid, evidence_digest:evidenceDigest,
      expected_work_item_fingerprint:fingerprint, request:fixedPrompt, dispatchable:true, dispatch_operation_id:null,
      evidence:{status:'failed', detail:'Evidence only'}},
    extendCaps: (caps: any) => ({...caps, hosts:caps.hosts.map((x: any) => ({...x,profile_id:source.profile_id})),
      actions:[...caps.actions,{action:'repair.create',allowed:true}], features:{...caps.features,managed_repairs:{version:1}}}),
    extraDispatch: async (input: any, state: any) => {
      const url = new URL(input.path,'http://fixture'), path=url.pathname;
      if (path === '/hosts/demo/discovery') return {status:200,data:{scopes:[]}};
      if (path === '/operations/'+failedId) return {status:200,data:{operation:{operation_id:failedId,actor:state.actor,
        action:'task.verify',status:'failed',target:{},params:{},preconditions:{},steps:[],events:[],external_refs:{},result:null}}};
      if (path === `/projects/${dispatchProject}/repair-evidence`) {
        state.repairReads.push(input.path);
        if (state.delayEvidence) await new Promise(resolve => {state.holdEvidence = resolve;});
        return {status:200,data:{version:1,project_id:dispatchProject,source:state.badEvidence?{...source,host:'wrong'}:Object.fromEntries(url.searchParams),
          expected_project_version:1,evidence_digest:evidenceDigest,evidence:{status:'failed'},existing:null}};
      }
      if (path === `/work-items/${wid}/repair`) {
        state.recordReads++;
        if(state.delayRepair) await new Promise(resolve=>{state.holdRepair=resolve;});
        if(state.failRepair) return {status:503,data:{error:{code:'READ_FAILED',message:'Repair read failed'}}};
        if(state.missingRepair) return {status:404,data:{error:{code:'REPAIR_NOT_FOUND',message:'No fixed repair evidence'}}};
        return {status:200,data:structuredClone(state.record)};
      }
      if (path === `/work-items/${wid}`) return {status:200,data:{work_item:item,project:state.project,path:[],links:[],children:[],derived:[],events:[]}};
      if (input.method==='POST' && input.body?.action==='repair.create') {
        state.repairPosts.push(structuredClone(input));
        if (state.delayRepairPost) await new Promise(resolve => {state.holdRepairPost = resolve;});
        if(!state.repairOperation.idempotency_key) state.repairOperation={operation_id:repairId,actor:state.actor,idempotency_key:input.idempotency_key,
          ...structuredClone(input.body),status:'succeeded',result:{project_id:dispatchProject,work_item_id:wid,evidence_digest:evidenceDigest}};
        if(state.lostRepair) {state.lostRepair=false;return {status:503,data:{error:{code:'LOST',message:'Lost repair reply'}}};}
        return {status:200,data:{operation:state.repairOperation}};
      }
      if(path === '/operations/'+repairId) return {status:200,data:{operation:state.repairOperation}};
    },...options});
}
async function openEvidence(page: any, operation = false) {
  await page.goto('/dashboard/#/'+(operation?'op/'+failedId:'host/demo'));
  await repair(page).getByText('Create repair work',{exact:true}).click();
  await repair(page).getByRole('combobox',{name:'Repair project'}).selectOption(dispatchProject);
  await expect(repair(page)).toContainText(evidenceDigest);
}
async function preview(page: any) {
  await form(page).getByRole('textbox',{name:'Published branch ref'}).fill('refs/heads/main');
  await form(page).getByRole('button',{name:'Preview published version',exact:true}).click();
  await expect(form(page).locator('[data-published-preview]')).toContainText(publishedSha);
}
for(const native of [false,true]) {
  const mode=native?'IPC':'HTTP';
  test(`${mode}: fixed discovery evidence creates work without launch and recovers the same key after reload`,async({page})=>{
    const state=await fixture(page,native,{lostRepair:true}); await openEvidence(page);
    expect(new URL(state.repairReads[0],'http://fixture').searchParams.get('profile_id')).toBe(source.profile_id);
    await repair(page).getByRole('button',{name:'Create or recover repair work',exact:true}).click();
    await expect(repair(page)).toContainText('Lost repair reply'); expect(state.posts).toEqual([]);
    const original=state.repairPosts[0]; expect(original.body).toEqual({action:'repair.create',target:{project_id:dispatchProject},
      params:{source},preconditions:{expected_project_version:1,expected_evidence_digest:evidenceDigest}});
    await page.reload(); await repair(page).getByText('Create repair work',{exact:true}).click();
    await repair(page).getByRole('button',{name:'Recover original repair request',exact:true}).click();
    await expect(repair(page).getByRole('link',{name:'Open repair work'})).toHaveAttribute('href','#/item/'+wid);
    expect(state.repairPosts).toHaveLength(2); expect(state.repairPosts[1]).toEqual(original); expect(state.posts).toEqual([]);
    await page.reload(); await repair(page).getByText('Create repair work',{exact:true}).click();
    await expect(repair(page).getByRole('link',{name:'Review and dispatch'})).toHaveAttribute('href',`#/dispatch/${dispatchProject}/${wid}`);
    expect(state.repairPosts).toHaveLength(2); expect(state.errors).toEqual([]);
  });
  test(`${mode}: failed-operation entry pins its exact source and rejects mismatched evidence`,async({page})=>{
    const state=await fixture(page,native); await openEvidence(page,true);
    const query=new URL(state.repairReads[0],'http://fixture').searchParams;
    expect(Object.fromEntries(query)).toEqual({kind:'operation',operation_id:failedId});
    state.badEvidence=true;
    await repair(page).getByRole('button',{name:'Read repair evidence',exact:true}).click();
    await expect(repair(page)).toContainText('This repair cannot be dispatched yet');
    await expect(repair(page).getByRole('button',{name:'Create or recover repair work',exact:true})).toBeDisabled();
    expect(state.repairPosts).toEqual([]); expect(state.posts).toEqual([]); expect(state.errors).toEqual([]);
  });
  test(`${mode}: repair dispatch preserves server words and fingerprint, isolates normal drafts and recovers original launch`,async({page})=>{
    const state=await fixture(page,native,{lost:true});
    await page.goto('/dashboard/#/dispatch/'+dispatchProject); await prompt(page).fill('Ordinary project draft');
    await page.goto(`/dashboard/#/dispatch/${dispatchProject}/${wid}`);
    await expect(prompt(page)).toHaveValue(fixedPrompt); await expect(prompt(page)).toHaveJSProperty('readOnly',true);
    expect(state.posts).toEqual([]); await preview(page);
    await form(page).getByRole('button',{name:'Start from this version',exact:true}).click();
    await expect(form(page)).toContainText('Lost published start reply');
    const original=state.posts[0]; expect(original.body.params).toMatchObject({project_id:dispatchProject,work_item_id:wid,prompt:fixedPrompt});
    expect(original.body.preconditions.expected_work_item_fingerprint).toBe(fingerprint);
    await page.reload(); await expect(prompt(page)).toHaveValue(fixedPrompt);
    await form(page).getByRole('button',{name:'Retry original request',exact:true}).click();
    await expect(form(page)).toContainText('Start confirmed'); expect(state.posts[1]).toEqual(original);
    await page.goto('/dashboard/#/dispatch/'+dispatchProject); await expect(prompt(page)).toHaveValue('Ordinary project draft');
    state.record.dispatch_operation_id=publishedId; state.record.dispatchable=false;
    await page.goto(`/dashboard/#/dispatch/${dispatchProject}/${wid}`);
    await expect(page.getByRole('link',{name:'View original dispatch',exact:true})).toHaveAttribute('href','#/op/'+publishedId);
    await expect(form(page)).toHaveCount(0); expect(state.posts).toHaveLength(2); expect(state.errors).toEqual([]);
  });
  test(`${mode}: reopened repair work keeps dispatch discoverable and refreshes receipts without replacing editor draft`,async({page})=>{
    const state=await fixture(page,native); await page.goto('/dashboard/#/item/'+wid);
    const slot=page.locator('[data-repair-work-item]');
    await expect(slot.getByRole('link',{name:'Review and dispatch'})).toHaveAttribute('href',`#/dispatch/${dispatchProject}/${wid}`);
    const draft=page.getByPlaceholder('New step'); await draft.fill('Keep this unfinished step'); await draft.focus();
    state.record.dispatch_operation_id=publishedId;state.record.dispatchable=false;
    state.events.push({seq:1,resource_type:'operation',resource_id:publishedId,kind:'operation.updated'});
    await expect(slot.getByRole('link',{name:'View original dispatch'})).toHaveAttribute('href','#/op/'+publishedId);
    await expect(draft).toHaveValue('Keep this unfinished step');await expect(draft).toBeFocused();
    await expect.poll(()=>state.after).toBe(1); expect(state.posts).toEqual([]);expect(state.errors).toEqual([]);
  });
  test(`${mode}: linked launch recovers a lost reply without creating a second dispatch`,async({page})=>{
    const state=await fixture(page,native,{lost:true});await page.goto(`/dashboard/#/dispatch/${dispatchProject}/${wid}`);
    await preview(page);await form(page).getByRole('button',{name:'Start from this version',exact:true}).click();
    await expect(form(page)).toContainText('Lost published start reply');
    state.record.dispatch_operation_id=publishedId;state.record.dispatchable=false;
    await page.reload();await expect(page.getByRole('link',{name:'View original dispatch',exact:true})).toHaveAttribute('href','#/op/'+publishedId);
    expect(state.posts).toHaveLength(1);expect(state.errors).toEqual([]);
  });
  test(`${mode}: observe-only repair reads cannot create or dispatch work`,async({page})=>{
    const state=await fixture(page,native,{scopes:['observe','start']});await openEvidence(page);
    await expect(repair(page).getByRole('button',{name:'Create or recover repair work',exact:true})).toBeDisabled();
    await page.goto(`/dashboard/#/dispatch/${dispatchProject}/${wid}`);await preview(page);
    await expect(form(page).getByRole('button',{name:'Start from this version',exact:true})).toBeDisabled();
    expect(state.posts).toEqual([]);expect(state.repairPosts).toEqual([]);expect(state.errors).toEqual([]);
  });
  test(`${mode}: leaving a pending repair route retires its response quietly`,async({page})=>{
    const state=await fixture(page,native,{delayRepair:true});await page.goto(`/dashboard/#/dispatch/${dispatchProject}/${wid}`);
    await expect.poll(()=>Boolean(state.holdRepair)).toBe(true);
    await page.evaluate(()=>{location.hash='#/projects';});await expect(page.getByRole('heading',{name:'Projects',exact:true})).toBeVisible();
    state.delayRepair=false;state.holdRepair();
    await expect(form(page)).toHaveCount(0);expect(state.posts).toEqual([]);expect(state.errors).toEqual([]);
  });
  test(`${mode}: ordinary work silently lacks repair evidence; unavailable or foreign repair routes fail visibly without uncaught errors`,async({page})=>{
    const state=await fixture(page,native,{repairOperation:{operation_id:repairId,action:'work_item.create',target:{project_id:dispatchProject}}}); await page.goto('/dashboard/#/item/'+wid);
    await expect(page.getByRole('heading',{name:'Draft follow-up'})).toBeVisible();
    await expect(page.locator('[data-repair-work-item]')).toBeHidden(); expect(state.recordReads).toBe(0);
    state.missingRepair=false;state.record.project_id='prj_'+'4'.repeat(20);
    await page.goto(`/dashboard/#/dispatch/${dispatchProject}/${wid}`);
    await expect(page.locator('main')).toContainText('Invalid repair work identity');await expect(form(page)).toHaveCount(0);
    state.record.project_id=dispatchProject;state.record.dispatchable=false;
    await page.reload();await expect(page.locator('main')).toContainText('Repair dispatch requires current server evidence');
    expect(state.posts).toEqual([]);expect(state.errors).toEqual([]);
  });
}
const repairStatus = (page: any) => repair(page).locator('.actions + [role="status"]');
test('busy focus: Create keeps focus while the request is held and then shows recover', async ({page}) => {
  const state = await fixture(page, false, {delayRepairPost: true});
  await openEvidence(page);
  const create = repair(page).getByRole('button', {name: 'Create or recover repair work', exact: true});
  await create.focus();
  await create.press('Enter');
  await expect.poll(() => Boolean(state.holdRepairPost)).toBe(true);
  await expect(create).toBeFocused();
  await expect(create).toHaveAttribute('aria-disabled', 'true');
  await expect(repairStatus(page)).toHaveAttribute('aria-busy', 'true');
  await create.press('Enter', {force: true});
  await create.click({force: true});
  expect(state.repairPosts).toHaveLength(1);
  state.delayRepairPost = false; state.holdRepairPost();
  const recover = repair(page).getByRole('button', {name: 'Recover original repair request', exact: true});
  await expect(recover).toBeEnabled();
  await expect(recover).toBeFocused();
  expect(state.repairPosts).toHaveLength(1);
});
test('busy focus: Read repair evidence keeps focus while its request is held', async ({page}) => {
  const state = await fixture(page, false);
  await openEvidence(page);
  state.delayEvidence = true;
  const read = repair(page).getByRole('button', {name: 'Read repair evidence', exact: true});
  const before = state.repairReads.length;
  await read.focus();
  await read.press('Enter');
  await expect.poll(() => Boolean(state.holdEvidence)).toBe(true);
  await expect(read).toBeFocused();
  await expect(read).toHaveAttribute('aria-disabled', 'true');
  await expect(repairStatus(page)).toHaveAttribute('aria-busy', 'true');
  await read.press('Enter', {force: true});
  expect(state.repairReads).toHaveLength(before + 1);
  state.delayEvidence = false; state.holdEvidence();
  await expect(read).not.toHaveAttribute('aria-disabled');
  await expect(read).toBeFocused();
  expect(state.repairReads).toHaveLength(before + 1);
});
test('busy focus: Review new evidence moves focus to Read repair evidence', async ({page}) => {
  await fixture(page, false);
  await openEvidence(page);
  await repair(page).getByRole('button', {name: 'Create or recover repair work', exact: true}).click();
  await expect(repair(page).getByRole('link', {name: 'Open repair work'})).toBeVisible();
  const next = repair(page).getByRole('button', {name: 'Review new evidence', exact: true});
  await next.focus();
  await next.press('Enter');
  await expect(repair(page).getByRole('button', {name: 'Read repair evidence', exact: true})).toBeFocused();
});
test('busy focus: static block keeps Create disabled during an evidence read', async ({page}) => {
  const state = await fixture(page, false);
  const extend = state.extendCaps;
  state.extendCaps = (caps: any) => {
    const next = extend(caps);
    return {...next, actions: next.actions.map((action: any) => action.action === 'repair.create' ? {...action, allowed: false} : action)};
  };
  await openEvidence(page);
  const create = repair(page).getByRole('button', {name: 'Create or recover repair work', exact: true});
  await expect(create).toHaveJSProperty('disabled', true);
  await expect(create).not.toHaveAttribute('aria-disabled');
  state.delayEvidence = true;
  await repair(page).getByRole('button', {name: 'Read repair evidence', exact: true}).click();
  await expect.poll(() => Boolean(state.holdEvidence)).toBe(true);
  await expect(create).toHaveJSProperty('disabled', true);
  await expect(create).not.toHaveAttribute('aria-disabled');
  state.delayEvidence = false; state.holdEvidence();
});
