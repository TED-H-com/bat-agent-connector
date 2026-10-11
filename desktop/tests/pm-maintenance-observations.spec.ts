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
    const f=fixture();f.add();f.add('workload');await mountConversation(page,native,f.dispatch);await page.goto(policyRoute);
    await expect(section(page)).toContainText('Incomplete coverage');let release!:()=>void;f.state.defer=new Promise(resolve=>{release=resolve;});
    const before=f.state.reads.length;await section(page).getByRole('button',{name:'Refresh saved observations',exact:true}).click();
    await expect.poll(()=>f.state.reads.length).toBeGreaterThan(before);
    f.parent.state.fail=403;await page.getByRole('button',{name:'Refresh maintenance policy',exact:true}).click();
    await expect(page.locator('[data-pm-maintenance-policy]')).toContainText('Policy sources could not be verified');release();
    await expect(section(page).locator('[data-observation-kind]')).toHaveCount(0);
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
for(const language of ['en','zh-TW'])for(const width of [390,768,1440])test(`maintenance evidence ${language} ${width}`,async({browser,baseURL},testInfo)=>{
  const context=await browser.newContext({baseURL,locale:language,viewport:{width,height:1000}}),page=await context.newPage(),f=fixture();f.add();f.add('workload');
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));await mountConversation(page,false,f.dispatch);await page.goto(policyRoute);
  await expect(section(page)).toContainText(language==='en'?'These counts are partial':'這些數量只有部分涵蓋');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);
  const folder=process.env.PM_SCREENSHOTS||testInfo.outputPath('screenshots');mkdirSync(folder,{recursive:true});
  // Remove only the sticky masthead for this section crop; keep the app's CSP.
  await page.locator('.top').evaluate(element=>element.remove());
  await section(page).screenshot({path:`${folder}/observations-${language}-${width}.png`});await context.close();
});
