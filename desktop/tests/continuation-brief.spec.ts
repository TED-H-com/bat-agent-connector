import {test, expect} from '@playwright/test';
import {mkdirSync, readFileSync} from 'node:fs';
import {demoFixture} from './demo/index.ts';
import {mountConversation} from './conversation-fixture.ts';

const action='pm.continuation.prepare', run='pmr_'+'7'.repeat(32), operation='op_'+'8'.repeat(32);
const archiveFirst='c'.repeat(64), archiveSecond='d'.repeat(64);
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
  const state={choice:null as string|null,writes:[] as any[],withdrawn:false,unavailable:false,lost:false,operation:null as any,
    archiveEnabled:false,archiveLatest:archiveFirst,archiveRefused:0,archiveReads:[] as URL[],
    archiveBinding:'e'.repeat(64),archiveHold:false,archiveRelease:null as null|(()=>void),
    archivePaged:false,archiveInventoryReads:[] as URL[],threadRefused:0};
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),path=url.pathname;
    if(state.archiveEnabled && path==='/sessions'){
      state.archiveInventoryReads.push(url);
      return {status:200,data:state.archivePaged&&!url.searchParams.has('cursor')?{
        sessions:[{host:'demo',session_id:'first-session',title:'First page conversation'}],next_cursor:'saved cursor/one'}:{sessions:[
        {host:'demo',session_id:'session-1',title:'Saved manual conversation',provenance:'manual',gone_at:1792320000}],next_cursor:null}};
    }
    if(state.threadRefused && path===root+'/threads/'+f.ids.threadIdea)
      return {status:state.threadRefused,data:{error:{code:'FORBIDDEN',message:'Discussion source unavailable'}}};
    if(state.archiveEnabled && path==='/sessions/demo/session-1/messages' && url.searchParams.get('source')==='retained'){
      state.archiveReads.push(url);
      const snapshot=url.searchParams.get('snapshot_id')||state.archiveLatest;
      const value={status:state.archiveRefused||200,data:state.archiveRefused?{error:{code:'NOT_FOUND',message:'Private archive diagnostic'}}:
        {host:'demo',session_id:'session-1',source:'retained',snapshot_id:snapshot,saved_at:1792320000,
          retained_session_ref:{version:1,server_id:'fixture server/opaque',host:'demo',session_id:'session-1',binding:state.archiveBinding,snapshot_id:snapshot},
          coverage:{history_complete:false,clipped_messages:1},source_status:'source_unavailable',offset:0,next_offset:null,
          brief:{kind:'extractive',items:[]},messages:[{id:'saved-one',role:'user',text:'Private retained source body',revision:archiveFirst,clipped:false}],
          snapshots:[{snapshot_id:archiveFirst,saved_at:1792320000},{snapshot_id:archiveSecond,saved_at:1792320060}]}};
      if(state.archiveHold){state.archiveHold=false;await new Promise<void>(resolve=>{state.archiveRelease=resolve;});}
      return value;
    }
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
      if(state.archiveEnabled)caps.features.session_archive={version:1,pm_continuation:true};
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

async function openRetainedPicker(page:any,x:Awaited<ReturnType<typeof fixture>>,native=false,locale='en'){
  x.state.archiveEnabled=true;
  await page.setViewportSize({width:locale==='en'?1440:390,height:900});
  await page.addInitScript((value:string)=>Object.defineProperty(navigator,'language',{get:()=>value}),locale);
  await mountConversation(page,native,x.dispatch);
  await page.goto(`/dashboard/#/pm/${x.f.ids.workspaceId}/discussions/${x.f.ids.threadIdea}`);
  await page.locator('[data-discussion-panel-toggle]').click();
  const tools=page.locator('[data-continuation-tools]'),picker=tools.locator('[data-pm-retained-session]');
  await picker.locator('summary').click();
  await picker.getByRole('button',{name:locale==='en'?'Browse conversations':'瀏覽對話',exact:true}).click();
  await picker.getByRole('combobox',{name:locale==='en'?'Choose a conversation':'選擇對話',exact:true}).selectOption(JSON.stringify(['demo','session-1']));
  await expect(picker.getByRole('button',{name:locale==='en'?'Include this saved version':'納入此保存版本',exact:true})).toBeVisible();
  return {tools,picker};
}

for(const locale of ['en','zh-TW']) for(const native of [false,true])test(`saved-session continuation pins one selected source without running on browse ${locale} ${native?'IPC':'HTTP'}`,async({page})=>{
  const x=await fixture(locale);x.state.archiveLatest=archiveSecond;
  const {tools,picker}=await openRetainedPicker(page,x,native,locale);
  await picker.getByRole('combobox',{name:locale==='en'?'Saved version':'保存版本',exact:true}).selectOption(archiveFirst);
  const search=picker.getByRole('searchbox');await search.fill('manual');await search.press('Enter');
  expect(x.state.writes).toEqual([]);
  await picker.getByRole('button',{name:locale==='en'?'Include this saved version':'納入此保存版本',exact:true}).click();
  const exact={version:1,server_id:'fixture server/opaque',host:'demo',session_id:'session-1',binding:x.state.archiveBinding,snapshot_id:archiveFirst};
  const persisted=await page.evaluate(()=>Object.entries(localStorage).find(([key])=>key.includes('.pm.continuation.prepare.'))?.[1]);
  expect(JSON.parse(JSON.parse(persisted!).values.retained_session)).toEqual(exact);
  expect(persisted).not.toContain('Private retained source body');
  x.state.archiveLatest=archiveSecond;
  await tools.locator('button[type=submit]').click();
  await expect.poll(()=>x.state.writes.length).toBe(1);
  expect(x.state.writes[0].body.params.retained_session_ref).toEqual(exact);
  expect(x.f.discussion.writes).toEqual([]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

for(const native of [false,true])test(`saved-session continuation recovers the same source and key after lost acknowledgement ${native?'IPC':'HTTP'}`,async({page})=>{
  const x=await fixture(),{tools,picker}=await openRetainedPicker(page,x,native);
  await picker.getByRole('button',{name:'Include this saved version',exact:true}).click();
  x.state.lost=true;
  await tools.locator('button[type=submit]').click();await expect(tools).toContainText('Reply unavailable');
  const original=structuredClone(x.state.writes[0]),reads=x.state.archiveReads.length;
  x.state.archiveLatest=archiveSecond;x.state.archiveRefused=403;
  await page.reload();await page.locator('[data-discussion-panel-toggle]').click();
  await tools.locator('button[type=submit]').click();
  await expect.poll(()=>x.state.writes.length).toBe(2);
  expect(x.state.writes[1]).toEqual(original);
  expect(x.state.archiveReads).toHaveLength(reads);
});

test('saved-session draft rechecks the original binding and refuses replacement after reload',async({page})=>{
  const x=await fixture(),{tools,picker}=await openRetainedPicker(page,x);
  await picker.getByRole('button',{name:'Include this saved version',exact:true}).click();
  x.state.archiveBinding='f'.repeat(64);x.state.archiveLatest=archiveSecond;
  await page.reload();await page.locator('[data-discussion-panel-toggle]').click();
  await picker.locator('summary').click();
  await expect(picker).toContainText('selected saved source is unavailable');
  expect(x.state.archiveReads.at(-1)!.searchParams.get('snapshot_id')).toBe(archiveFirst);
  await expect(tools.locator('button[type=submit]')).toBeDisabled();
  expect(x.state.writes).toEqual([]);
  await picker.getByRole('button',{name:'Use discussion only',exact:true}).click();
  await expect(tools.locator('button[type=submit]')).toBeEnabled();
});

test('saved-session citations open their fixed archive version',async({page})=>{
  const x=await fixture();x.state.archiveEnabled=true;x.state.archiveLatest=archiveSecond;
  x.saved.source_references['message.one']={kind:'retained_session_message',host:'demo',session_id:'session-1',snapshot_id:archiveFirst,
    binding:x.state.archiveBinding,id:'saved-one',revision:archiveFirst};
  await mountConversation(page,false,x.dispatch);
  await page.goto(`/dashboard/#/pm/${x.f.ids.workspaceId}/discussions/${x.f.ids.threadIdea}`);
  const reader=page.locator('[data-continuation-brief]');await reader.locator(':scope > details > summary').click();
  await reader.getByRole('link',{name:'Saved source 1',exact:true}).first().click();
  await expect(page.locator('[data-session-archive]')).toHaveAttribute('open','');
  await expect(page.locator('[data-archive-message="saved-one"]')).toContainText('Private retained source body');
  expect(x.state.archiveReads.at(-1)!.searchParams.get('snapshot_id')).toBe(archiveFirst);
  expect(x.state.writes).toEqual([]);
});

test('saved-session picker loads gone conversations through explicit bounded pages',async({page})=>{
  const x=await fixture();x.state.archiveEnabled=true;x.state.archivePaged=true;
  await mountConversation(page,false,x.dispatch);
  await page.goto(`/dashboard/#/pm/${x.f.ids.workspaceId}/discussions/${x.f.ids.threadIdea}`);
  await page.locator('[data-discussion-panel-toggle]').click();
  const picker=page.locator('[data-pm-retained-session]');await picker.locator('summary').click();
  await picker.getByRole('button',{name:'Browse conversations',exact:true}).click();
  const more=picker.getByRole('button',{name:'Load more conversations',exact:true});await expect(more).toBeVisible();
  expect(x.state.archiveInventoryReads).toHaveLength(1);
  expect(x.state.archiveInventoryReads[0].searchParams.get('limit')).toBe('50');
  expect(x.state.archiveInventoryReads[0].searchParams.get('include_gone')).toBe('true');
  await picker.getByRole('searchbox').fill('manual');
  await expect(picker.getByRole('combobox',{name:'Choose a conversation',exact:true}).locator('option')).toHaveCount(1);
  expect(x.state.archiveReads).toHaveLength(0);
  await more.click();
  const sessions=picker.getByRole('combobox',{name:'Choose a conversation',exact:true});
  await expect(sessions.locator('option')).toHaveCount(2);
  expect(x.state.archiveInventoryReads).toHaveLength(2);
  expect(x.state.archiveInventoryReads[1].searchParams.get('cursor')).toBe('saved cursor/one');
  expect(x.state.archiveInventoryReads[1].searchParams.get('include_gone')).toBe('true');
  await expect(more).toBeHidden();
  await sessions.selectOption(JSON.stringify(['demo','session-1']));
  await expect(picker.getByRole('button',{name:'Include this saved version',exact:true})).toBeVisible();
  expect(x.state.writes).toEqual([]);
});

for(const native of [false,true])test(`saved-session late reads cannot restore a withdrawn discussion ${native?'IPC':'HTTP'}`,async({page})=>{
  const x=await fixture(),{tools,picker}=await openRetainedPicker(page,x,native);
  if(native)await page.evaluate(()=>{
    const boundary=(window as any).__TAURI_INTERNALS__,invoke=boundary.invoke;
    boundary.invoke=async(command:string,args:any)=>{
      const reply=await invoke(command,args);
      if(command==='connector_request'&&reply.data?.source==='retained')
        (window as any).__fixtureSavedReply=reply.data.snapshot_id;
      return reply;
    };
  });
  x.state.archiveHold=true;
  await picker.getByRole('combobox',{name:'Saved version',exact:true}).selectOption(archiveSecond);
  await expect.poll(()=>Boolean(x.state.archiveRelease)).toBe(true);
  x.state.threadRefused=403;
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled();
  await expect(page.locator('[data-discussion-composer]')).toBeDisabled();
  const response=native?null:page.waitForResponse(value=>value.url().includes('snapshot_id='+archiveSecond));
  x.state.archiveRelease!();
  if(native)await expect.poll(()=>page.evaluate(()=>(window as any).__fixtureSavedReply)).toBe(archiveSecond);
  else await(await response!).finished();
  await expect(picker.getByRole('link',{name:'Open this saved version',exact:true})).toHaveCount(0);
  await expect(tools.locator('button[type=submit]')).toBeDisabled();
  expect(x.state.writes).toEqual([]);
  expect(await page.evaluate(()=>Object.entries(localStorage).find(([key])=>key.includes('.pm.continuation.prepare.'))?.[1]||'')).not.toContain('Private retained source body');
});

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
