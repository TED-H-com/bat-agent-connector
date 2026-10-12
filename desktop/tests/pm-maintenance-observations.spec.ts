import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {mountConversation} from './conversation-fixture.ts';
import {policyFixture,policyRoute,workspaceId,policyProfile} from './pm-maintenance-policy-fixture.ts';
const section=(page:any)=>page.locator('[data-pm-maintenance-observations]');
const request=(page:any,action:string)=>section(page).locator(`[data-pm-action="${action}"]`);
function fixture(){
  const parent=policyFixture(),p=parent.addPolicy(),base=`/pm/workspaces/${workspaceId}`;
  const state={reads:[] as string[],writes:[] as any[],ops:new Map<string,any>(),hidden:false,fail:false,lost:false,mismatch:false,
    defer:null as null|Promise<void>,snapshot:null as any,observation:null as any};
  function add(kind='github'){
    const now=Date.now()/1000-1,key=kind==='github'?'observation_id':'snapshot_id',id=(kind==='github'?'pmgh_':'pmwl_')+'b'.repeat(32);
    const row={workspace_id:workspaceId,[key]:id,policy_id:p.policy_id,baseline_id:parent.release.state.baselines[0].baseline_id,
      operation_id:'op_'+'b'.repeat(32),document_digest:'c'.repeat(64),document:{workspace_id:workspaceId,[key]:id,as_of:now,expires_at:now+1800,
        ...(kind==='github'?{profile:policyProfile,evidence:{status:'incomplete',observed_open_pulls:0,observed_nonterminal_actions:0}}:
          {classification:'unknown',source_coverage:[{kind:'github',status:'incomplete'}],blocking_reasons:[{code:'source_expired'}],stability:{satisfied:false}})}};
    if(kind==='github')state.observation=row;else state.snapshot=row;return row;
  }
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path==='/capabilities'||path==='/bootstrap'){
      const result=await parent.dispatch(input),caps=path==='/bootstrap'?result.data.capabilities:result.data;
      caps.features.pm_workspaces.workload={version:1};
      for(const action of ['pm.github.observe','pm.workload.capture'])caps.actions.push({action,allowed:parent.base.state.manage});return result;
    }
    if(path===base+'/workload-snapshots/current'||path===base+'/github-observations/current'){
      state.reads.push(path);if(state.fail)return {status:403,data:{error:{code:'FORBIDDEN',message:'Observation source withdrawn'}}};
      const kind=path.includes('github-')?'observation':'snapshot',data=structuredClone({workspace_id:state.mismatch?'ws_'+'f'.repeat(32):workspaceId,
        [kind]:state.hidden?null:state[kind],visibility_filtered:state.hidden});if(state.defer)await state.defer;return {status:200,data};
    }
    if(path==='/operations'&&['pm.github.observe','pm.workload.capture'].includes(input.body?.action)){
      state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
      if(!op){const row=add(input.body.action==='pm.github.observe'?'github':'workload');op={...input.body,operation_id:row.operation_id,
        actor:parent.base.state.actor,idempotency_key:input.idempotency_key,status:'succeeded',result:{workspace_id:workspaceId}};state.ops.set(input.idempotency_key,op);}
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Observation reply unavailable'}}};}
      return {status:200,data:{operation:op}};
    }
    if(path.startsWith('/operations/')){const op=[...state.ops.values()].find(op=>path.endsWith(op.operation_id));if(op)return {status:200,data:{operation:op}};}
    return parent.dispatch(input);
  };
  return {parent,state,add,dispatch};
}
const runtimeReceipt='op_'+'7'.repeat(32),completedExecution='op_'+'6'.repeat(32);
function explanations(f:ReturnType<typeof fixture>){
  const row=f.add('workload');Object.assign(row.document,{classification:'unknown',
    source_coverage:[{kind:'central_project',project_id:'fixture-project',status:'complete'},
      {kind:'bat_workspace',host:'fixture-bat',workspace_id:'fixture-workspace',profile_id:'fixture-profile',status:'complete'},
      {kind:'github',status:'incomplete'}],
    blocking_reasons:[{code:'session_end_unproven',severity:'unknown',source:{kind:'session',id:'fixture-bat/manual-retained'}},
      {code:'source_not_visible',severity:'unknown'}],
    exclusions:[{kind:'operation',id:completedExecution,reason:'original_maintenance_attempt_completed',runtime_observation_id:runtimeReceipt},
      {kind:'session',id:'fixture-bat/verified-retained',reason:'verified_ended_session_retained'},
      {kind:'work',id:'wi_'+'8'.repeat(20),reason:'checkpoint_is_not_activity'}]});return row;
}
for(const native of [false,true])test.describe(native?'Native maintenance evidence':'Web maintenance evidence',()=>{
  test('incomplete zero counts never claim idle and actions use exact current versions',async({page})=>{
    const f=fixture();f.add();f.add('workload');await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);
    await expect(section(page)).toContainText('These counts are partial; zero does not establish that no work exists.');
    await expect(section(page)).toContainText('Not enough evidence to determine workload');expect(f.state.writes).toHaveLength(0);
    await section(page).getByText('Review blockers and coverage gaps',{exact:true}).click();
    await expect(section(page)).toContainText('A source observation is expired or its time is unproven.');
    await section(page).getByRole('button',{name:'Read GitHub workload',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
    expect(f.state.writes[0].body).toEqual({action:'pm.github.observe',target:{workspace_id:workspaceId},params:{policy_id:f.parent.state.policies[0].policy_id},
      preconditions:{expected_policy_version:1,expected_baseline_id:f.parent.release.state.baselines[0].baseline_id,expected_lifecycle_revision:0}});
    await section(page).getByRole('button',{name:'Capture saved workload',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(2);
    expect(f.state.writes[1].body.action).toBe('pm.workload.capture');expect(f.parent.base.state.writes).toHaveLength(0);
  });
  test('lost response retains the original key across new policy versions',async({page})=>{
    const f=fixture();f.state.lost=true;await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);
    await section(page).getByRole('button',{name:'Read GitHub workload',exact:true}).click();await expect(section(page)).toContainText('Observation reply unavailable');
    f.parent.addPolicy();await page.reload();await request(page,'pm.github.observe').getByRole('button',{name:'Check original request',exact:true}).click();
    await expect.poll(()=>f.state.writes.length).toBe(2);expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);
    expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);expect(f.state.ops.size).toBe(1);
  });
  test('source revocation clears evidence and a late read cannot restore it',async({page})=>{
    const f=fixture();f.add();explanations(f);await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);
    await expect(section(page)).toContainText('Incomplete coverage');let release!:()=>void;f.state.defer=new Promise(resolve=>{release=resolve;});
    const before=f.state.reads.length;await section(page).getByRole('button',{name:'Refresh saved observations',exact:true}).click();
    await expect.poll(()=>f.state.reads.length).toBeGreaterThan(before);
    f.parent.state.fail=403;await page.getByRole('button',{name:'Refresh maintenance policy',exact:true}).click();
    await expect(page.locator('[data-pm-maintenance-policy]')).toContainText('Policy sources could not be verified');release();
    await expect(section(page).locator('[data-observation-kind]')).toHaveCount(0);
    await expect(section(page)).not.toContainText(completedExecution);await expect(section(page)).not.toContainText('fixture-bat/manual-retained');
    await expect(section(page).getByRole('button',{name:'Read GitHub workload',exact:true})).toBeDisabled();
  });
  test('hidden latest evidence differs from no observation and readers cannot create evidence',async({page})=>{
    const f=fixture();f.state.hidden=true;f.parent.base.state.role='reader';await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);
    await expect(section(page)).toContainText('latest evidence is outside your current source access');
    await expect(section(page)).not.toContainText('No saved observation');await expect(section(page).getByRole('button',{name:'Read GitHub workload',exact:true})).toBeDisabled();expect(f.state.writes).toHaveLength(0);
  });
  test('mismatched sources refuse capture and expired idle evidence is qualified',async({page})=>{
    const f=fixture(),row=f.add('workload');row.document.classification='idle_candidate';row.document.expires_at=1;
    await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);await expect(section(page)).toContainText('This observation is expired');
    f.state.mismatch=true;await section(page).getByRole('button',{name:'Refresh saved observations',exact:true}).click();
    await expect(section(page)).toContainText('Observation evidence does not match this workspace');
    await expect(section(page).locator('[data-observation-kind]')).toHaveCount(0);await expect(section(page).getByRole('button',{name:'Capture saved workload',exact:true})).toBeDisabled();
  });
});
for(const native of [false,true])for(const locale of ['en','zh-TW'])test(`workload explanations ${native?'Tauri':'Web'} ${locale} keep completion and manual blockers distinct`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage();try{
    const f=fixture();explanations(f);await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);
    const box=section(page),exclusions=box.locator('[data-workload-exclusions]');
    await expect(box).toContainText(locale==='en'?'This workload observation does not assess quality.':'這份工作量觀察不做品質評估。');
    await expect(box).toContainText(locale==='en'?'Not enough evidence to determine workload':'證據不足，無法判定工作量');
    await expect(box).toContainText(locale==='en'?'BAT workspace: Complete within the selected scope':'BAT 工作區: 在選定範圍內完整');
    await box.getByText(locale==='en'?'Review blockers and coverage gaps':'檢視阻止原因與涵蓋缺口',{exact:true}).click();
    await exclusions.locator('summary').click();await expect(exclusions).toContainText(completedExecution);
    await expect(exclusions).toContainText(locale==='en'?'A verified ended session is retained as history.':'已驗證結束的工作階段保留為歷史。');
    await expect(box).toContainText('fixture-bat/manual-retained');await expect(exclusions).not.toContainText('fixture-bat/manual-retained');
    await expect(exclusions.locator('a')).toHaveAttribute('href',`#/op/${runtimeReceipt}`);
    await expect(exclusions).toContainText(locale==='en'?'A retained worktree alone is not active work.':'保留 worktree 本身不算進行中的工作。');
    const hidden=box.locator('li').filter({hasText:'source_not_visible'});await expect(hidden).not.toContainText('fixture-bat');
    expect(f.state.writes).toEqual([]);expect(f.parent.base.state.writes).toEqual([]);
  }finally{await context.close();}
});
test('unknown public exclusion reasons remain literal and do not infer completed work',async({page})=>{
  const f=fixture(),row=explanations(f);row.document.exclusions=[{kind:'session',id:'fixture-bat/<img src=x onerror=alert(1)>',reason:'future_observation_reason'}];
  await mountConversation(page,false,f.dispatch);await page.goto(policyRoute);const box=section(page),excluded=box.locator('[data-workload-exclusions]');await excluded.locator('summary').click();
  await expect(excluded).toContainText('future_observation_reason');await expect(excluded).toContainText('fixture-bat/<img src=x onerror=alert(1)>');await expect(excluded.locator('img')).toHaveCount(0);
  await expect(excluded).not.toContainText('recorded completion evidence');await expect(box).toContainText('Not enough evidence to determine workload');expect(f.state.writes).toEqual([]);
});
for(const locale of ['en','zh-TW'])test(`normal execution and manual observation exclusions retain separate original receipts ${locale}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage();try{
    const f=fixture(),row=f.add('workload');row.document.exclusions=[
      {kind:'operation',id:completedExecution,reason:'original_batch_attempt_completed',runtime_observation_id:runtimeReceipt},
      {kind:'session',id:'fixture-bat/manual-single-input',reason:'manual_session_currently_quiescent',runtime_observation_id:'op_'+'8'.repeat(32)}];
    await mountConversation(page,false,f.dispatch);await page.goto(policyRoute);const exclusions=section(page).locator('[data-workload-exclusions]');await exclusions.locator('summary').click();
    await expect(exclusions).toContainText(locale==='en'?'original batch attempt has recorded completion evidence':'原始批次嘗試已有保存的完成證據');
    await expect(exclusions).toContainText(locale==='en'?'Ownership and work acceptance are unchanged':'控制權與工作驗收不變');
    expect(await exclusions.locator('a').evaluateAll((nodes:any[])=>nodes.map(n=>n.getAttribute('href')))).toEqual([`#/op/${runtimeReceipt}`,'#/op/op_'+'8'.repeat(32)]);
    expect(f.state.writes).toEqual([]);
  }finally{await context.close();}
});
test('malformed exclusion source clears the whole saved projection instead of hiding only that fact',async({page})=>{
  const f=fixture(),row=explanations(f);await mountConversation(page,false,f.dispatch);await page.goto(policyRoute);
  await expect(section(page).locator('[data-workload-exclusions]')).toHaveCount(1);row.document.exclusions[0].id={unknown:'not an original source identity'};
  await section(page).getByRole('button',{name:'Refresh saved observations',exact:true}).click();
  await expect(section(page)).toContainText('Observation evidence does not match this workspace');await expect(section(page).locator('[data-observation-kind]')).toHaveCount(0);
  await expect(section(page).getByRole('button',{name:'Capture saved workload',exact:true})).toBeDisabled();expect(f.state.writes).toEqual([]);
});
for(const language of ['en','zh-TW'])for(const width of [390,768,1440])test(`maintenance evidence ${language} ${width}`,async({browser,baseURL},testInfo)=>{
  const context=await browser.newContext({baseURL,locale:language,viewport:{width,height:1000}}),page=await context.newPage(),f=fixture();f.add();explanations(f);
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));await mountConversation(page,false,f.dispatch);await page.goto(policyRoute);
  await expect(section(page)).toContainText(language==='en'?'These counts are partial':'這些數量只有部分涵蓋');
  await section(page).locator('[data-workload-exclusions] > summary').click();
  await section(page).getByText(language==='en'?'Review blockers and coverage gaps':'檢視阻止原因與涵蓋缺口',{exact:true}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);
  const folder=process.env.PM_SCREENSHOTS||testInfo.outputPath('screenshots');mkdirSync(folder,{recursive:true});
  // Remove only the sticky masthead for this section crop; keep the app's CSP.
  await page.locator('.top').evaluate(element=>element.remove());
  await section(page).screenshot({path:`${folder}/observations-${language}-${width}.png`});await context.close();
});
