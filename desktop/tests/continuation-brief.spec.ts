import {test, expect} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';

const action='pm.continuation.prepare', run='pmr_'+'7'.repeat(32), operation='op_'+'8'.repeat(32);
async function fixture(locale='en') {
  const f=demoFixture({locale,discussions:true}), root=`/pm/workspaces/${f.ids.workspaceId}`;
  const detail=f.discussion.details.get(f.ids.threadIdea);
  // Production freezes this metadata in the run context. Reading the current
  // plan later must not mutate the same saved continuation's source identity.
  const initialPlans=await f.dispatch({method:'GET',path:root+'/plans?limit=20'});
  const saved:any={available:true,run_id:run,operation_id:operation,document_digest:'a'.repeat(64),
    validation:{status:'format_valid',conclusions_verified:false},as_of:1792320000,thread_version:detail.thread.version,
    plan_head:structuredClone(initialPlans.data.head),coverage:{partial:false,through_message:8},source_references:{'message.one':{operation_id:operation,kind:'message',id:'pmm_'+'6'.repeat(32)}},
    document:{version:1,goal:{text:'Keep the original delivery goal.',source_refs:['message.one']},status:'next_steps',
      next_steps:[{text:'Decide whether the mobile evidence meets this release.',source_refs:['message.one']}],
      adopted_decisions:[],rejected_directions:[{text:'Do not add referrals to this release.',source_refs:['message.one']}],
      candidates:[],completed_evidence:[],open_questions:[],remaining_work:[]}};
  detail.continuation_brief=saved;
  const state={choice:null as string|null,writes:[] as any[],withdrawn:false,unavailable:false,lost:false,operation:null as any};
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(input.method==='POST' && input.body?.action===action){
      state.writes.push(structuredClone(input));
      state.operation??={...input.body,operation_id:operation,actor:f.ids.actor,status:'succeeded',result:{run_id:run},idempotency_key:input.idempotency_key};
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status:200,data:{operation:state.operation}};
    }
    if(path==='/operations/'+operation && state.operation)return {status:200,data:{operation:state.operation}};
    const value=await f.dispatch(input);
    if(path==='/capabilities' || path==='/bootstrap'){
      const caps=path==='/bootstrap'?value.data.capabilities:value.data;
      caps.actions.push({action,allowed:true});
    }
    if(path===root+'/capabilities')value.data.structured_analysis={version:1,actions:{[action]:{adapters:[
      {adapter:'codex-native-linux',action_registered:true,authorized:true,eligible_for_preflight:!state.unavailable},
      {adapter:'bat-pm-linux-v1',action_registered:true,authorized:true,eligible_for_preflight:true}]}}};
    if(path===root+'/capabilities'&&state.choice)value.data.workspace_adapter={version:1,choice:{adapter:state.choice}};
    if(path===root+'/threads/'+f.ids.threadIdea){
      value.data.continuation_brief=state.withdrawn?{available:false}:structuredClone(saved);
    }
    return value;
  };
  return {f,state,saved,detail,dispatch};
}

for(const locale of ['en','zh-TW']) for(const native of [false,true])test(`continuation uses one explicit sourced request ${locale} ${native?'IPC':'HTTP'}`,async({page})=>{
  const x=await fixture(locale), {f,state}=x, width=locale==='en'?1440:390;
  await page.setViewportSize({width,height:900});
  await page.addInitScript(value=>Object.defineProperty(navigator,'language',{get:()=>value}),locale);
  await mountConversation(page,native,x.dispatch);
  if(process.env.BATC_CONTINUATION_BASELINE_ASSETS)for(const asset of ['app.js','app.css']){
    const body=readFileSync(`${process.env.BATC_CONTINUATION_BASELINE_ASSETS}/${asset}`);
    await page.route(`**/dashboard/${asset}`,route=>route.fulfill({status:200,body,contentType:asset.endsWith('.js')?'text/javascript':'text/css'}));
  }
  await page.goto(`/dashboard/#/pm/${f.ids.workspaceId}/discussions/${f.ids.threadIdea}`);
  const composer=page.locator('[data-discussion-composer]');await expect(composer).toBeEnabled();
  await composer.fill('Keep this unsent discussion draft.');
  if(process.env.BATC_CONTINUATION_BASELINE){
    if(!native){const dir=process.env.BATC_CONTINUATION_SHOTS!;mkdirSync(dir,{recursive:true});await page.screenshot({path:`${dir}/before-${locale}-${width}.png`});}
    return;
  }
  const reader=page.locator('[data-continuation-brief]');await expect(reader).toBeVisible();
  await reader.locator(':scope > details > summary').click();
  await expect(reader).toContainText('Decide whether the mobile evidence');
  if(process.env.BATC_CONTINUATION_SHOTS && !native){const dir=process.env.BATC_CONTINUATION_SHOTS;mkdirSync(dir,{recursive:true});await page.screenshot({path:`${dir}/after-${locale}-${width}.png`});}
  await page.getByRole('button',{name:locale==='en'?'Refresh':'重新整理',exact:true}).click();
  await expect(composer).toBeEnabled();expect(state.writes).toEqual([]);
  await page.locator('[data-discussion-panel-toggle]').click();
  const tools=page.locator('[data-continuation-tools]');await expect(tools).toBeVisible();
  await tools.getByLabel(locale==='en'?'Run analysis with':'分析執行方式',{exact:true}).selectOption('bat-pm-linux-v1');
  await tools.locator('button[type=submit]').click();
  await expect.poll(()=>state.writes.length).toBe(1);
  expect(state.writes[0].body.params.previous_brief_ref).toEqual({run_id:run,document_digest:'a'.repeat(64)});
  expect(state.writes[0].body.params.adapter).toBe('bat-pm-linux-v1');
  expect(state.writes[0].body.target).toEqual({workspace_id:f.ids.workspaceId,thread_id:f.ids.threadIdea});
  await expect(composer).toHaveValue('Keep this unsent discussion draft.');
  expect(f.discussion.writes).toEqual([]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

for(const native of [false,true])test(`withdrawn continuation disappears without falling back ${native?'IPC':'HTTP'}`,async({page})=>{
  const x=await fixture();await mountConversation(page,native,x.dispatch);
  await page.goto(`/dashboard/#/pm/${x.f.ids.workspaceId}/discussions/${x.f.ids.threadIdea}`);
  const reader=page.locator('[data-continuation-brief]');await expect(reader).toBeVisible();
  await reader.locator(':scope > details > summary').click();await expect(reader).toContainText('mobile evidence');
  x.state.withdrawn=true;
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(reader).toContainText('original continuation sources are unavailable');
  await expect(reader).not.toContainText('mobile evidence');
  await expect(reader.locator('a')).toHaveCount(0);expect(x.state.writes).toEqual([]);
});

for(const native of [false,true])test(`continuation recovery retains the exact prior version and adapter ${native?'IPC':'HTTP'}`,async({page})=>{
  const x=await fixture();x.state.lost=true;await mountConversation(page,native,x.dispatch);
  await page.goto(`/dashboard/#/pm/${x.f.ids.workspaceId}/discussions/${x.f.ids.threadIdea}`);
  await page.locator('[data-discussion-panel-toggle]').click();
  const tools=page.locator('[data-continuation-tools]');await expect(tools).toBeVisible();
  await tools.getByLabel('Run analysis with',{exact:true}).selectOption('bat-pm-linux-v1');
  await tools.locator('button[type=submit]').click();
  await expect(tools.locator('[data-error-details]')).toContainText('Reply unavailable');
  x.saved.document_digest='b'.repeat(64);
  await page.reload();await page.locator('[data-discussion-panel-toggle]').click();
  await tools.locator('button[type=submit]').click();
  await expect.poll(()=>x.state.writes.length).toBe(2);
  expect(x.state.writes[1]).toEqual(x.state.writes[0]);
  expect(x.state.writes[1].body.params.previous_brief_ref.document_digest).toBe('a'.repeat(64));
  expect(x.f.discussion.writes).toEqual([]);
});

test('discussion refresh preserves an open brief and does not run an unavailable adapter',async({page})=>{
  const x=await fixture();x.state.unavailable=true;await mountConversation(page,false,x.dispatch);
  await page.goto(`/dashboard/#/pm/${x.f.ids.workspaceId}/discussions/${x.f.ids.threadIdea}`);
  await expect(page.locator('[data-discussion-composer]')).toBeEnabled();
  const reader=page.locator('[data-continuation-brief]');
  await expect(reader).not.toContainText('New discussion or plan changes');
  await reader.locator(':scope > details > summary').click();
  const detail=reader.locator(':scope > details');const original=await detail.elementHandle();
  x.detail.thread.version++;
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  await expect(reader).toContainText('New discussion or plan changes');
  expect(await detail.evaluate((node,old)=>node===old,original)).toBe(true);
  await expect(detail).toHaveAttribute('open','');
  await page.locator('[data-discussion-panel-toggle]').click();
  const tools=page.locator('[data-continuation-tools]');await expect(tools.locator('button[type=submit]')).toBeDisabled();
  expect(x.state.writes).toEqual([]);
});

for(const native of [false,true])test(`workspace choice routes continuation while its original receipt survives a later choice ${native?'IPC':'HTTP'}`,async({page})=>{
  const x=await fixture();x.state.choice='bat-pm-linux-v1';x.state.lost=true;await mountConversation(page,native,x.dispatch);
  await page.goto(`/dashboard/#/pm/${x.f.ids.workspaceId}/discussions/${x.f.ids.threadIdea}`);
  await page.locator('[data-discussion-panel-toggle]').click();
  const tools=page.locator('[data-continuation-tools]'),adapter=tools.getByLabel('Run analysis with',{exact:true});
  await expect(adapter).toHaveValue('bat-pm-linux-v1');await expect(adapter).toBeDisabled();
  await tools.locator('button[type=submit]').click();await expect(tools).toContainText('Reply unavailable');
  const original=structuredClone(x.state.writes[0]);x.state.choice='codex-native-linux';
  await page.reload();await page.locator('[data-discussion-panel-toggle]').click();
  await expect(adapter).toHaveValue('bat-pm-linux-v1');await expect(adapter).toBeDisabled();
  await tools.locator('button[type=submit]').click();await expect.poll(()=>x.state.writes.length).toBe(2);
  expect(x.state.writes[1]).toEqual(original);expect(x.f.discussion.writes).toEqual([]);
});

for(const locale of ['en','zh-TW']) for(const native of [false,true]) for(const first of ['thread','plans'] as const)
test(`continuation refresh keeps the same frozen brief with ${first} first ${locale} ${native?'IPC':'HTTP'}`,async({page})=>{
  const x=await fixture(locale), root=`/pm/workspaces/${x.f.ids.workspaceId}`;
  const paths={thread:root+'/threads/'+x.f.ids.threadIdea,plans:root+'/plans'};
  let release!:()=>void;
  const firstRead=new Promise<void>(resolve=>{release=resolve;});
  const initial=new Set(Object.values(paths)), order:string[]=[];
  const briefs:any[]=[];
  await page.addInitScript(value=>Object.defineProperty(navigator,'language',{get:()=>value}),locale);
  await mountConversation(page,native,async input=>{
    const path=new URL(input.path,'http://fixture').pathname;
    const initialRead=initial.delete(path);
    // Hold the other initial response until the selected producer has returned.
    // No timer or browser scheduling can choose the snapshot's plan metadata.
    if(initialRead && path!==paths[first])await firstRead;
    const value=await x.dispatch(input);
    if(path===paths.thread)briefs.push(structuredClone(value.data.continuation_brief));
    if(initialRead){order.push(path);if(path===paths[first])release();}
    return value;
  });
  await page.goto(`/dashboard/#/pm/${x.f.ids.workspaceId}/discussions/${x.f.ids.threadIdea}`);
  const composer=page.locator('[data-discussion-composer]');await expect(composer).toBeEnabled();
  expect(order[0]).toBe(paths[first]);
  await composer.fill('Keep this discussion draft.');
  const reader=page.locator('[data-continuation-brief]');
  const stale=locale==='en'?'New discussion or plan changes':'尚未納入新增討論或計畫變更';
  await expect(reader).not.toContainText(stale);
  await reader.locator(':scope > details > summary').click();
  const detail=reader.locator(':scope > details'), original=await detail.elementHandle();
  x.detail.thread.version++;
  const refresh=page.getByRole('button',{name:locale==='en'?'Refresh':'重新整理',exact:true});
  await refresh.click();await expect(refresh).toBeEnabled();
  await expect(reader).toContainText(stale);
  expect(await detail.evaluate((node,old)=>node===old,original)).toBe(true);
  await expect(detail).toHaveAttribute('open','');
  expect(briefs).toHaveLength(2);expect(briefs[1]).toEqual(briefs[0]);
  await expect(composer).toHaveValue('Keep this discussion draft.');
  expect(x.state.writes).toEqual([]);expect(x.f.discussion.writes).toEqual([]);
});
