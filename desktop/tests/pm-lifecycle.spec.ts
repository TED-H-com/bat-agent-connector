import {openPmPanel} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {pmFixture,workspaceId,pmRoute} from './pm-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {expectShellEmpty, shellShot} from './shell-empty-state.ts';
const endpoint=`/pm/workspaces/${workspaceId}/lifecycle`,transitionId='pmlt_'+'a'.repeat(32),operationId='op_'+'b'.repeat(32);
const gated=['pm.run.prepare','pm.thread.plan','pm.input.confirm','pm.plan.draft','pm.plan.adopt'];
function fixture(){
  const base=pmFixture();
  const state={stage:'idea' as string|null,status:'active',revision:0,epoch:0,transition:null as any,filtered:false,
    maintenance:null as any,failed:false,admin:false,pageFiltered:false,historyDenied:false,historyGate:null as Promise<void>|null,readGate:null as Promise<void>|null,
    lost:false,pending:false,writes:[] as any[],reads:[] as any[],ops:new Map<string,any>(),inflight:[] as any[]};
  const stopped=()=>['paused','closed'].includes(state.stage||'');
  function stop(){state.stage='paused';state.status='pausing';state.revision++;state.epoch++;state.transition={transition_id:transitionId,reason:'Keep the released version while reviewing unresolved work.',actor:'reader'};
    state.inflight=[{sequence:1,operation_id:operationId,action:'pm.run.start',actor:'reader',operation_status:'unknown',captured_at:1,disposition:'unresolved',steps:[{name:'pm.native.execute',status:'sent'}],writer:{run_id:'pmr_'+'c'.repeat(32),generation:1,status:'unknown'},retention:null}];}
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),path=url.pathname;
    if(path==='/bootstrap'||path==='/capabilities'){
      const result=await base.dispatch(input),caps=path==='/bootstrap'?result.data.capabilities:result.data;
      caps.features.pm_workspaces.lifecycle={version:1,gated_actions:gated,record_actions:['pm.message.append','pm.input.record']};
      if(state.admin)caps.scopes.push('admin');
      caps.actions.push(...['set_stage','pause','close','reopen','retain_inflight'].map(kind=>({action:'pm.lifecycle.'+kind,allowed:base.state.manage})));
      return result;
    }
    if(path.startsWith(endpoint)){
      state.reads.push(structuredClone(input));
      if(state.readGate)await state.readGate;
      if(state.failed)return {status:403,data:{error:{code:'FORBIDDEN',message:'Workspace source access changed'}}};
      if(path===endpoint)return {status:200,data:{workspace_id:workspaceId,
        lifecycle:{stage:state.stage,status:state.status,revision:state.revision,execution_epoch:state.epoch,transition_id:state.transition?.transition_id||null,coverage:state.stage===null?'legacy_unclassified':'recorded'},
        transition:state.filtered?null:structuredClone(state.transition),transition_visibility_filtered:state.filtered,
        blocked_actions:stopped()?gated:[],record_actions:['pm.message.append','pm.input.record'],available_stages:['idea','planning','development',...(state.maintenance?['maintain']:[])],
        maintenance:state.maintenance?{available:true,reason:null,reference:structuredClone(state.maintenance)}:{available:false,reason:'release_baseline_and_policy_required'},inflight_summary:{visible_unresolved:state.inflight.filter(row=>row.disposition==='unresolved').length,visible_retained:0,visibility_filtered:state.filtered}}};
      if(path.endsWith('/inflight'))return {status:200,data:{workspace_id:workspaceId,transition_id:state.transition?.transition_id,inflight:structuredClone(state.inflight),next_cursor:null,visibility_filtered:state.filtered||state.pageFiltered}};
      if(path.endsWith('/transitions'))return {status:200,data:{workspace_id:workspaceId,transitions:state.filtered?[]:state.transition?[structuredClone(state.transition)]:[],next_cursor:null,visibility_filtered:state.filtered}};
      if(state.historyGate)await state.historyGate;
      if(state.historyDenied)return {status:404,data:{error:{code:'NOT_FOUND',message:'Original transition source unavailable'}}};
      return {status:200,data:{workspace_id:workspaceId,transition:structuredClone(state.transition),inflight_summary:{}}};
    }
    if(path.startsWith('/operations/')&&input.method==='GET'){
      const op=[...state.ops.values()].find(op=>path.endsWith(op.operation_id));if(op)return {status:200,data:{operation:op}};
    }
    if(path==='/operations'&&input.method==='POST'&&input.body.action.startsWith('pm.lifecycle.')){
      state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
      if(!op){
        const {action,params}=input.body;
        if(action==='pm.lifecycle.pause')stop();
        else if(action==='pm.lifecycle.close'){stop();state.stage='closed';state.status='closing';}
        else if(action==='pm.lifecycle.retain_inflight'){
          for(const row of state.inflight)if(params.operation_ids.includes(row.operation_id)){row.disposition='retained';row.retention={reason:params.reason,actor:'reader',transition_id:transitionId,created_at:2};}
          state.revision++;state.status=state.stage==='closed'?'closed':'paused';
        }else{state.stage=params.stage;state.status='active';state.revision++;}
        op={...input.body,actor:base.state.actor,idempotency_key:input.idempotency_key,operation_id:'op_'+String(state.ops.size+1).padStart(32,'0'),status:state.pending?'running':'succeeded',result:{workspace_id:workspaceId}};
        state.ops.set(input.idempotency_key,op);
      }
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status:200,data:{operation:structuredClone(op)}};
    }
    return base.dispatch(input);
  };
  return {base,state,dispatch,stop};
}
const activity=(page:any)=>page.locator('[data-pm-lifecycle]');
const action=(page:any,kind:string)=>activity(page).locator(`[data-pm-action="pm.lifecycle.${kind}"]`);
async function edit(page:any,kind:string){
  await activity(page).locator('[data-pm-lifecycle-controls]>summary').click();
  await activity(page).locator(`[data-pm-lifecycle-intent="${kind}"]>summary`).click();
  return action(page,kind);
}
for(const locale of ['en','zh-TW'])for(const width of [1440,390])for(const font of ['', 'DejaVu Sans', 'Liberation Sans'])test(`phase empty and unavailable states explain their short titles ${locale} ${width} ${font || 'system'}`,async({browser,baseURL})=>{
  const context=await browser.newContext({locale,baseURL,viewport:{width,height:900}}),page=await context.newPage();
  if(font)await page.route('**/app.css',async route=>{const response=await route.fetch();await route.fulfill({response,body:(await response.text())+`\n*{font-family:"${font}"!important}`});});
  const f=fixture();f.state.stage=null;f.state.status='unclassified';
  const copy=locale==='en'?{phase:'Phase not yet selected',legacy:'Existing work keeps its classification.',
    pending:'No admitted work',scope:'Only readable records are shown.',
    unavailable:'Activity unavailable',reason:'Workspace state is unreadable.'}
    :{phase:'尚未指定管理階段',legacy:'既有工作保留原分類。',pending:'尚無已受理工作',scope:'僅顯示可讀取的管理紀錄。',
      unavailable:'無法取得狀態',reason:'無法讀取工作區狀態。'};
  await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openPmPanel(page,'ui_change_phase');
  const empty=(title:string)=>activity(page).locator('.ui-empty').filter({has:page.getByRole('heading',{name:title,exact:true})});
  const checkMargin=async(title:string)=>expect(await empty(title).evaluate(node=>{
    const css=getComputedStyle(node),range=document.createRange();range.selectNodeContents(node.querySelector(':scope > p')!);
    return node.clientWidth-parseFloat(css.paddingLeft)-parseFloat(css.paddingRight)-range.getBoundingClientRect().width;
  })).toBeGreaterThanOrEqual(16);
  const capture=async(state:string)=>{
    await shellShot(page,state,locale,width);
    if(!process.env.BATC_FIX3_SHOTS)return;
    await mkdir(process.env.BATC_FIX3_SHOTS,{recursive:true});
    await page.screenshot({path:`${process.env.BATC_FIX3_SHOTS}/fix3-${state}-${font.replaceAll(' ','-')||'system'}-${locale}-${width}.png`,animations:'disabled'});
  };
  await expectShellEmpty(empty(copy.phase),copy.phase,copy.legacy);
  await checkMargin(copy.phase);await capture('phase-unselected');
  await expectShellEmpty(empty(copy.pending),copy.pending,copy.scope);
  await empty(copy.pending).scrollIntoViewIfNeeded();await checkMargin(copy.pending);await capture('phase-no-pending');
  f.state.failed=true;f.base.state.cursor++;
  await expectShellEmpty(empty(copy.unavailable),copy.unavailable,copy.reason);
  await expect(activity(page).getByRole('heading',{name:copy.pending,exact:true})).toHaveCount(0);
  await expect(activity(page).locator('.pm-lifecycle-stage')).toHaveCount(0);
  await checkMargin(copy.unavailable);await capture('phase-unavailable');
  expect(f.state.writes).toHaveLength(0);expect(f.base.state.writes).toHaveLength(0);
  await context.close();
});
for(const native of [false,true])test.describe(native?'native lifecycle':'web lifecycle',()=>{
  test('initial thread mount retains lifecycle forms until the complete read can enable them',async({page})=>{
    const f=fixture();let release:()=>void=()=>{};f.state.readGate=new Promise<void>(resolve=>release=resolve);
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
    const editor=await edit(page,'pause'),reason=editor.getByLabel('Reason and retained context');
    try{await expect(reason).toBeDisabled();expect(f.state.writes).toHaveLength(0);}finally{release();}
    await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
    await expect(reason).toBeEnabled();await reason.fill('Pause once after the original workspace read.');
    await editor.getByRole('button',{name:'Pause new work',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(1);
    expect(f.state.writes[0].body.preconditions).toEqual({expected_lifecycle_revision:0});
  });
  test('a Development workspace does not submit Idea from a pristine phase form',async({page})=>{
    const f=fixture();f.state.stage='development';await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page,'ui_change_phase');
    await expect(activity(page).locator('.pm-lifecycle-stage')).toHaveText('Development');
    const editor=await edit(page,'set_stage'),stage=editor.getByLabel('Management phase');
    await expect(stage).toHaveValue('');
    await expect(stage.locator('option:checked')).toHaveText('Choose phase');
    await editor.getByLabel('Reason and retained context').fill('Keep the current delivery.');
    await expect(editor.getByRole('button',{name:'Record phase',exact:true})).toBeDisabled();
    await editor.evaluate((form:HTMLFormElement)=>form.requestSubmit());
    expect(f.state.writes).toHaveLength(0);
    await expect(stage).toBeEnabled();
    await expect(editor.getByLabel('Reason and retained context')).toBeEnabled();
    await stage.selectOption('planning');
    await expect(editor.getByRole('button',{name:'Record phase',exact:true})).toBeEnabled();
    const reads=f.state.reads.length;f.base.state.cursor++;
    await expect.poll(()=>f.state.reads.length).toBeGreaterThan(reads);
    await expect(stage).toHaveValue('planning');
    await expect(editor.getByLabel('Reason and retained context')).toHaveValue('Keep the current delivery.');
    f.state.lost=true;await editor.getByRole('button',{name:'Record phase',exact:true}).click();
    await expect(editor).toContainText('Reply unavailable');
    const original=structuredClone(f.state.writes[0]);
    await page.reload();await openPmPanel(page,'ui_change_phase');await edit(page,'set_stage');
    await action(page,'set_stage').getByRole('button',{name:'Check original request',exact:true}).click();
    expect(f.state.writes).toHaveLength(2);expect(f.state.writes[1]).toEqual(original);
    expect(original.body.params.stage).toBe('planning');
  });
  test('Idea remains a readable stage without dispatching or changing its discussion',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
    await expect(activity(page)).toContainText('Idea');await expect(activity(page)).toContainText('Discussions, Sprint commitments');
    const editor=await edit(page,'set_stage');await editor.getByLabel('Management phase').selectOption('planning');
    await editor.getByLabel('Reason and retained context').fill('Organize the selected direction; keep other ideas open.');
    await editor.getByRole('button',{name:'Record phase',exact:true}).click();
    await expect(activity(page).locator('.pm-lifecycle-stage')).toHaveText('Planning');
    expect(f.state.writes).toHaveLength(1);expect(f.state.writes[0].body.preconditions).toEqual({expected_lifecycle_revision:0});
    expect(f.base.state.thread.mode).toBe('idea');expect(f.base.state.writes).toHaveLength(0);await expect(activity(page).getByRole('tab')).toHaveCount(0);await expect(page.locator('[data-discussion-panel]')).toBeHidden();await expect(page.locator('[data-discussion-panel]').getByRole('tab',{includeHidden:true})).toHaveCount(3);
  });
  test('pause distinguishes unknown in-flight results and explicit retention does not restart them',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
    const editor=await edit(page,'pause');await editor.getByLabel('Reason and retained context').fill('Pause new investment.');
    await editor.getByRole('button',{name:'Pause new work',exact:true}).click();
    await expect(activity(page).locator('.pm-lifecycle-stage')).toHaveText('Paused for new work · existing work needs reconciliation');
    const row=activity(page).locator('[data-pm-lifecycle-operation]');await row.locator('summary').click();await expect(row).toContainText('Unknown');
    await activity(page).locator('[data-pm-lifecycle-retain]>summary').click();
    await activity(page).getByRole('checkbox',{name:'Retain this operation '+operationId,exact:true}).check();
    await action(page,'retain_inflight').getByLabel('Reason and retained context').fill('Leave the unknown result for its original controller to reconcile.');
    await action(page,'retain_inflight').getByRole('button',{name:'Record retained work',exact:true}).click();
    await expect(activity(page).locator('.pm-lifecycle-stage')).toHaveText('Paused');
    expect(f.state.inflight[0].operation_status).toBe('unknown');expect(f.state.writes[1].body.params.operation_ids).toEqual([operationId]);
    expect(f.base.state.writes).toHaveLength(0);
  });
  test('lost pause acknowledgement recovers the same key and original revision',async({page})=>{
    const f=fixture();f.state.lost=true;await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
    const editor=await edit(page,'pause');await editor.getByLabel('Reason and retained context').fill('Keep this exact stop intent.');
    await editor.getByRole('button',{name:'Pause new work',exact:true}).click();await expect(editor).toContainText('Reply unavailable');
    f.base.state.cursor++;await expect(activity(page).locator('.pm-lifecycle-stage')).toContainText('Paused for new work',{timeout:10000});
    await editor.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(2);expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
    expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);expect(f.state.ops.size).toBe(1);
  });
  test('a changed revision preserves the draft until the owner explicitly reviews the new state',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
    const editor=await edit(page,'close');await editor.getByLabel('Reason and retained context').fill('Preserve this closure rationale.');
    f.state.revision++;f.base.state.cursor++;await expect(editor).toContainText('Your draft still uses the earlier version',{timeout:10000});
    await expect(editor.getByRole('button',{name:'Close this management workspace',exact:true})).toBeDisabled();
    await expect(editor.getByLabel('Reason and retained context')).toHaveValue('Preserve this closure rationale.');
    await editor.getByRole('button',{name:'Review current activity state'}).click();await editor.getByRole('button',{name:'Close this management workspace',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(1);
    expect(f.state.writes[0].body.preconditions.expected_lifecycle_revision).toBe(1);
    expect(f.state.writes.some(call=>call.body.action.includes('archive'))).toBe(false);
  });
  test('reopening explicitly selects a stage without replaying stopped requests',async({page})=>{
    const f=fixture();f.stop();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
    const editor=await edit(page,'reopen');await editor.getByLabel('Management phase').selectOption('development');
    await editor.getByLabel('Reason and retained context').fill('Resume selected development; review old work separately.');
    await editor.getByRole('button',{name:'Reopen workspace',exact:true}).click();await expect(activity(page).locator('.pm-lifecycle-stage')).toHaveText('Development');
    expect(f.state.writes.map(call=>call.body.action)).toEqual(['pm.lifecycle.reopen']);expect(f.base.state.writes).toHaveLength(0);
    expect(f.state.inflight[0].operation_status).toBe('unknown');
  });
  test('legacy and filtered states do not infer missing decisions or hide an effective stop',async({page})=>{
    const f=fixture();f.state.stage=null;f.state.status='unclassified';await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
    await expect(activity(page)).toContainText('Existing work keeps its classification.');
    f.stop();f.state.filtered=true;f.base.state.cursor++;
    await expect(activity(page).locator('.pm-lifecycle-stage')).toContainText('Paused for new work',{timeout:10000});
    await expect(activity(page)).toContainText('Some records are outside your current source access');
    await expect(activity(page)).not.toContainText('Keep the released version while reviewing');
    expect(f.state.writes).toHaveLength(0);
  });
  test('source read failure clears prior facts and blocks new investment',async({page})=>{
    const f=fixture();f.stop();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
    await expect(activity(page)).toContainText('Keep the released version');f.state.failed=true;f.base.state.cursor++;
    await expect(activity(page).getByRole('heading',{name:'Activity unavailable',exact:true})).toBeVisible({timeout:10000});
    await expect(activity(page)).not.toContainText('Keep the released version');await expect(activity(page).locator('[data-pm-lifecycle-operation]')).toHaveCount(0);
    const editor=await edit(page,'reopen');await expect(editor.getByRole('button',{name:'Reopen workspace',exact:true})).toBeDisabled();
  });
  test('a withdrawn historical source removes its metadata and any late private detail',async({page})=>{
    const f=fixture();f.stop();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
    await activity(page).locator('[data-pm-lifecycle-history]>summary').click();
    f.state.historyDenied=true;await activity(page).locator('[data-pm-lifecycle-history] details>summary').click();
    await expect(activity(page)).toContainText('Original transition source unavailable');
    await expect(activity(page)).not.toContainText('Keep the released version');
  });
  test('a non-owner observes the effective stage without management authority',async({page})=>{
    const f=fixture();f.base.state.role='contributor';f.state.admin=true;await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
    await expect(activity(page)).toContainText('Only the current workspace owner');
    const editor=await edit(page,'pause');await expect(editor.getByRole('button',{name:'Pause new work',exact:true})).toBeDisabled();expect(f.state.writes).toHaveLength(0);
  });
  test('new execution preparation stays gated while manual notes remain available',async({page})=>{
    const f=fixture();f.stop();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
    await expect(activity(page).locator('.pm-lifecycle-stage')).toContainText('Paused for new work');
    await expect(page.locator('[data-pm-action="pm.run.prepare"] button[type=submit]')).toBeDisabled();
    await expect(page.locator('[data-pm-action="pm.message.append"] button[type=submit]')).toBeEnabled();
    expect(f.base.state.writes).toHaveLength(0);
  });
  test('filtered inflight pages and withdrawn retention reasons remain visibly incomplete',async({page})=>{
    const f=fixture();f.stop();f.state.pageFiltered=true;f.state.inflight[0].disposition='retained';f.state.inflight[0].retention_visibility_filtered=true;
    await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
    const row=activity(page).locator('[data-pm-lifecycle-operation]');await row.locator('summary').click();
    await expect(row).toContainText('Some records are outside your current source access');
    await expect(activity(page).getByText('Some records are outside your current source access. Visible rows are not a complete project total.',{exact:true})).toHaveCount(2);
  });
});
for(const locale of ['en-US','zh-TW'])for(const width of [390,768,1440])test(`lifecycle layout ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=fixture();f.stop();
  await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');await expect(activity(page).locator('.pm-lifecycle-stage')).toBeVisible();
  await activity(page).locator('[data-pm-lifecycle-controls]>summary').click();await activity(page).locator('[data-pm-lifecycle-intent="reopen"]>summary').click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.evaluate(()=>scrollTo(0,0));
  const out=process.env.PM_LIFECYCLE_SCREENSHOTS;if(out){await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/${locale}-${width}.png`,fullPage:true});}await context.close();
});

for(const native of [false,true])test(`fixed Maintain evidence survives unknown reply ${native?'IPC':'browser'}`,async({page})=>{
  const f=fixture();f.state.stage='development';f.state.maintenance={policy_id:'pmmp_'+'1'.repeat(32),policy_version:1,baseline_id:'pmrb_'+'2'.repeat(32),snapshot_id:'pmwl_'+'3'.repeat(32),snapshot_digest:'4'.repeat(64)};
  await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
  const editor=await edit(page,'set_stage');await editor.getByLabel('Management phase').selectOption('maintain');
  await editor.getByLabel('Reason and retained context').fill('Use the original complete workload');
  f.state.lost=true;await editor.getByRole('button',{name:'Record phase',exact:true}).click();
  await expect(editor).toContainText('Reply unavailable');const original=structuredClone(f.state.writes[0]);
  expect(original.body.params.maintenance_ref).toEqual(f.state.maintenance);
  f.state.maintenance.snapshot_digest='5'.repeat(64);f.state.stage='paused';f.state.revision++;
  await page.reload();await openPmPanel(page, 'ui_change_phase');await edit(page,'set_stage');await action(page,'set_stage').getByRole('button',{name:'Check original request',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(2);expect(f.state.writes[1]).toEqual(original);
});
test('a newer Maintain observation requires explicit review before a fresh decision',async({page})=>{
  const f=fixture();f.state.stage='development';f.state.maintenance={policy_id:'pmmp_'+'1'.repeat(32),policy_version:1,baseline_id:'pmrb_'+'2'.repeat(32),snapshot_id:'pmwl_'+'3'.repeat(32),snapshot_digest:'4'.repeat(64)};
  await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openPmPanel(page, 'ui_change_phase');
  const editor=await edit(page,'set_stage');await editor.getByLabel('Management phase').selectOption('maintain');await editor.getByLabel('Reason and retained context').fill('Review this observed idle state');
  f.state.maintenance.snapshot_digest='6'.repeat(64);f.base.state.cursor++;
  await expect(editor.getByRole('button',{name:'Record phase',exact:true})).toBeDisabled({timeout:10000});
  await editor.getByRole('button',{name:'Review current activity state',exact:true}).click();await editor.getByRole('button',{name:'Record phase',exact:true}).click();
  expect(f.state.writes[0].body.params.maintenance_ref).toEqual(f.state.maintenance);
});


for(const locale of ['en','zh-TW']) test(`lifecycle uses shared action and operation labels ${locale}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage(),f=fixture();f.stop();
  Object.assign(f.state.inflight[0],{action:'session.start',operation_status:'uncertain',steps:[{name:'continue',status:'uncertain'}]});
  await mountConversation(page,false,f.dispatch);await page.goto(pmRoute);await openPmPanel(page,'ui_change_phase');
  const row=activity(page).locator('[data-pm-lifecycle-operation]');
  await expect(row.locator('summary')).toContainText(locale==='en'?'Start session':'開始工作階段');
  await expect(row.locator('summary')).not.toContainText('session.start');
  await row.locator('summary').click();
  await expect(row).toContainText(locale==='en'?'continue · outcome unknown, reading back':'continue · 結果不明，回查中');
  await expect(row).not.toContainText('uncertain');
  await expect(row).not.toContainText('writer: unknown');expect(f.state.writes).toHaveLength(0);
  await context.close();
});
