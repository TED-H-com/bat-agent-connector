import {test,expect} from '@playwright/test';
import {proposalFixture,mountProposal,bootProposal,selectProposal,editor,workspaceId,threadId,proposalId} from './pm-plan-proposals-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture,pmRoute,pmPath} from './pm-fixture.ts';
import {openThreadTab} from './pm-navigation.ts';
const ACTION='work.adopt_and_start',project='prj_'+'a'.repeat(20),work='wi_'+'b'.repeat(20),second='wi_'+'c'.repeat(20);
function fixture(){
  const f=proposalFixture();f.save();const dispatch=f.dispatch;
  const state:any={previews:[],lost:false,blocked:false,hold:null,malformed:false,denied:false,opStatus:'accepted'};
  f.dispatch=async input=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path==='/fixture-state'){
      const value=await dispatch(input);value.data.caps.features.pm_workspaces.work_adopt_and_start={version:1,available:true,max_selected_works:10};
      value.data.caps.actions.push({action:ACTION,allowed:!state.denied});return value;
    }
    if(path.endsWith('/projects'))return {status:200,data:{workspace_id:workspaceId,bindings:[{workspace_id:workspaceId,project_id:project}],next_cursor:null}};
    if(path==='/work-items')return {status:200,data:{work_items:[work,second].map((id,index)=>({work_item_id:id,project_id:project,project_name:'Member access',title:index?'Verify retry':'Fix duplicate login',archived_at:null})),next_cursor:null}};
    if(path.endsWith('/adopt-and-start-preview')){
      state.previews.push(structuredClone(input));if(state.hold)await state.hold;
      const pre={expected_version:4,expected_plan_version:0,expected_accepted_revision_id:null,expected_proposal_digest:'a'.repeat(64),works:input.body.works.map((row:any)=>({work_item_id:row.work_item_id,expected_work_item_fingerprint:'1'.repeat(64),expected_work_version:1,expected_project_version:1,expected_binding_id:null,expected_previous_package_id:null,expected_latest_attempt_id:null,repository_id:41,binding_digest:'2'.repeat(64),expected_source_sha:'3'.repeat(40)}))};
      const works=input.body.works.map((row:any,index:number)=>({...row,instruction:row.instruction||'Fix duplicate submission and add tests',agent:'codex',model:null,delivery:'pull_request',code_target:{repository:'example/member-access',host:'host-a',workspace_id:'workspace-a',source_ref:'refs/heads/main'},readiness:{ready:!(state.blocked&&index===1),blocking_code:state.blocked&&index===1?'PM_WORK_START_PREREQUISITES':null}}));
      if(state.malformed)delete works[0].code_target.source_ref;
      return {status:200,data:{...input.body,workspace_id:workspaceId,thread_id:threadId,adoptable:true,works,preconditions:pre}};
    }
    if(path==='/operations'&&input.body?.action===ACTION){
      f.state.writes.push(structuredClone(input));let op=f.state.ops.get(input.idempotency_key);
      if(!op){op={...structuredClone(input.body),idempotency_key:input.idempotency_key,actor:'owner',operation_id:'op_'+'8'.repeat(32),status:state.opStatus,result:null};f.state.ops.set(input.idempotency_key,op);}
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Response unavailable'}}};}
      return {status:202,data:{operation:structuredClone(op)}};
    }
    return dispatch(input);
  };
  return {f,state};
}
async function prepare(page:any,f:any){await mountProposal(page,f);await selectProposal(page);await editor(page).getByLabel('Reason for adopting this update').fill('Adopt the reviewed login repair');}
async function choose(page:any,id=work){const row=page.locator(`[data-adopt-work="${id}"]`);await row.locator('.pm-adopt-choice > input').first().check();await row.getByLabel('Paid members can log in',{exact:true}).check();}
test('unchanged background proposal refresh preserves checked adoption readiness',async({page})=>{
  const {f,state}=fixture();await prepare(page,f);await choose(page);
  const start=page.getByRole('button',{name:'Adopt and start',exact:true});
  await page.getByRole('button',{name:'Check readiness',exact:true}).click();await expect(start).toBeEnabled();
  let release:any;f.state.gate=new Promise(resolve=>release=resolve);const reads=f.state.reads.length;
  await page.evaluate(()=>{(window as any).pendingRefresh=(window as any).proposalRefresh({background:true});});
  await expect.poll(()=>f.state.reads.length).toBeGreaterThan(reads+1);await expect(start).toBeDisabled();
  release();await page.evaluate(()=>(window as any).pendingRefresh);
  await expect(start).toBeEnabled();expect(state.previews).toHaveLength(1);
  await start.click();await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.preconditions.expected_proposal_digest).toBe('a'.repeat(64));
});
test('background baseline change and manual refresh still require checked readiness',async({page})=>{
  const {f}=fixture();await prepare(page,f);await choose(page);
  const start=page.getByRole('button',{name:'Adopt and start',exact:true});
  await page.getByRole('button',{name:'Check readiness',exact:true}).click();await expect(start).toBeEnabled();
  await page.evaluate(()=>(window as any).proposalRefresh());await expect(start).toBeDisabled();
  await page.getByRole('button',{name:'Check readiness',exact:true}).click();await expect(start).toBeEnabled();
  f.state.head.version++;await page.evaluate(()=>(window as any).proposalRefresh({background:true}));
  await expect(start).toBeDisabled();expect(f.state.writes).toEqual([]);
});
test('background authority loss cannot restore the old checked readiness',async({page})=>{
  const {f,state}=fixture();await prepare(page,f);await choose(page);
  const start=page.getByRole('button',{name:'Adopt and start',exact:true});
  await page.getByRole('button',{name:'Check readiness',exact:true}).click();await expect(start).toBeEnabled();
  state.denied=true;await page.evaluate(()=>(window as any).proposalRefresh({background:true}));await expect(start).toBeHidden();
  state.denied=false;await page.evaluate(()=>(window as any).proposalRefresh({background:true}));await expect(start).toBeDisabled();
  expect(f.state.writes).toEqual([]);
});
test('one preview supplies the original compound request and lands on its first Work',async({page})=>{
  const {f,state}=fixture();await prepare(page,f);await choose(page);
  await page.getByRole('button',{name:'Check readiness',exact:true}).click();await expect(page.getByRole('button',{name:'Adopt and start',exact:true})).toBeEnabled();
  await expect(editor(page).getByRole('button',{name:'Adopt, do later',exact:true})).toBeVisible();
  expect(state.previews[0].body.works).toEqual([{work_item_id:work,requirement_ids:['member_login']}]);
  await page.getByRole('button',{name:'Adopt and start',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
  const sent=f.state.writes[0];expect(sent.body.action).toBe(ACTION);expect(sent.body.params.works[0].agent).toBe('codex');
  expect(sent.body.params.works[0].model).toBeUndefined();expect(sent.body.preconditions.works[0].expected_scope_revision).toBeUndefined();
  await expect(page).toHaveURL(new RegExp('board\\?select='+work));
  const bookmarks=await page.evaluate(()=>Object.keys(localStorage).filter(key=>key.includes('adopt-start-landing')).map(key=>JSON.parse(localStorage.getItem(key)!)));
  expect(bookmarks).toEqual([{version:1,operation_id:'op_'+'8'.repeat(32),thread_id:threadId,work_item_ids:[work]}]);
  expect(await page.evaluate(workspaceId=>sessionStorage.getItem(`batc.pm.proposal-ui.owner.adopt-start-live.${workspaceId}.op_${'8'.repeat(32)}`),workspaceId)).toBe(work);
});
test('changed rationale invalidates readiness and a malformed ready row cannot submit',async({page})=>{
  const {f,state}=fixture();await prepare(page,f);await choose(page);const start=page.getByRole('button',{name:'Adopt and start',exact:true});
  await page.getByRole('button',{name:'Check readiness',exact:true}).click();await expect(start).toBeEnabled();
  await editor(page).getByLabel('Reason for adopting this update').fill('A different reviewed intent');await expect(start).toBeDisabled();
  state.malformed=true;await page.getByRole('button',{name:'Check readiness',exact:true}).click();await expect(page.locator('[data-adopt-start]')).toContainText('readiness response does not match');
  await expect(start).toBeDisabled();expect(f.state.writes).toEqual([]);
});
test('partially ready selection sends only the ready Work and corresponding frozen conditions',async({page})=>{
  const {f,state}=fixture();state.blocked=true;await prepare(page,f);await choose(page);await choose(page,second);
  await page.getByRole('button',{name:'Check readiness',exact:true}).click();await expect(page.locator(`[data-adopt-work="${second}"]`)).toContainText('Not ready');
  await page.getByRole('button',{name:'Adopt and start',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params.works.map((row:any)=>row.work_item_id)).toEqual([work]);expect(f.state.writes[0].body.preconditions.works.map((row:any)=>row.work_item_id)).toEqual([work]);
});
test('unknown admission retains the same key and reviewed request across refresh',async({page})=>{
  const {f,state}=fixture();state.lost=true;await prepare(page,f);await choose(page);await page.getByRole('button',{name:'Check readiness',exact:true}).click();
  const compound=page.locator(`[data-pm-action="${ACTION}"]`);await compound.getByRole('button',{name:'Adopt and start',exact:true}).click();
  await expect(compound.getByRole('button',{name:'Check original request',exact:true})).toBeVisible();
  await expect(editor(page).locator('button[type=submit]')).toBeDisabled();await expect(compound.getByRole('button',{name:'New request',exact:true})).toBeHidden();
  await page.evaluate(()=>(window as any).proposalRefresh());await compound.getByRole('button',{name:'Check original request',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(2);expect(f.state.writes[1]).toEqual(f.state.writes[0]);
});
test('withdrawal discards a late preview and removes every selected source',async({page})=>{
  const {f,state}=fixture();let release:any;state.hold=new Promise(resolve=>release=resolve);await prepare(page,f);await choose(page);
  await page.getByRole('button',{name:'Check readiness',exact:true}).click();await expect.poll(()=>state.previews.length).toBe(1);
  await page.evaluate(()=>(window as any).proposalWithdraw());release();await expect(page.locator('[data-adopt-work]')).toHaveCount(0);expect(f.state.writes).toEqual([]);
});
test('an edited instruction discards an older preview reply',async({page})=>{
  const {f,state}=fixture();let release:any;state.hold=new Promise(resolve=>release=resolve);await prepare(page,f);await choose(page);
  await page.getByRole('button',{name:'Check readiness',exact:true}).click();await expect.poll(()=>state.previews.length).toBe(1);
  const input=page.locator(`[data-adopt-work="${work}"]`).getByLabel('Instruction',{exact:true});await input.fill('A newer instruction');release();
  await expect(input).toHaveValue('A newer instruction');await expect(page.getByRole('button',{name:'Adopt and start',exact:true})).toBeDisabled();expect(f.state.writes).toEqual([]);
});
test('action withdrawal closes admission while preserving the instruction draft',async({page})=>{
  const {f,state}=fixture();await prepare(page,f);await choose(page);await page.getByRole('button',{name:'Check readiness',exact:true}).click();
  await expect(page.getByRole('button',{name:'Adopt and start',exact:true})).toBeEnabled();state.denied=true;await page.evaluate(()=>(window as any).proposalRefresh());
  await expect(page.getByRole('button',{name:'Adopt and start',exact:true})).toBeHidden();expect(f.state.writes).toEqual([]);
  state.denied=false;await page.evaluate(()=>(window as any).proposalRefresh());await expect(page.locator(`[data-adopt-work="${work}"]`).getByLabel('Instruction',{exact:true})).toHaveValue('Fix duplicate submission and add tests');
  await expect(page.getByRole('button',{name:'Adopt and start',exact:true})).toBeDisabled();
});
test('selection and instructions survive remount but readiness must be checked again',async({page})=>{
  const {f}=fixture();await prepare(page,f);await choose(page);await page.locator(`[data-adopt-work="${work}"]`).getByLabel('Instruction',{exact:true}).fill('Keep this draft instruction');
  await page.reload();await bootProposal(page);await expect(page.locator(`[data-adopt-work="${work}"]`).getByLabel('Instruction',{exact:true})).toHaveValue('Keep this draft instruction');
  await expect(page.getByRole('button',{name:'Adopt and start',exact:true})).toBeDisabled();expect(f.state.writes).toEqual([]);
});

test('ready preview must carry the numeric repository identity returned by the server',async({page})=>{
  const {f}=fixture(),dispatch=f.dispatch;f.dispatch=async input=>{const result=await dispatch(input);
    if(input.path.endsWith('/adopt-and-start-preview'))result.data.preconditions.works[0].repository_id='41';return result;};
  await prepare(page,f);await choose(page);await page.getByRole('button',{name:'Check readiness',exact:true}).click();
  await expect(page.locator('[data-adopt-start]')).toContainText('readiness response does not match');
  await expect(page.getByRole('button',{name:'Adopt and start',exact:true})).toBeDisabled();expect(f.state.writes).toEqual([]);
});

async function landing(page:any,{locale='en',denied=false,unknown=false,status='failed',hold=null}:any={}){
  const {f}=fixture(),dispatch=f.dispatch,id='op_'+'8'.repeat(32),child='op_'+'9'.repeat(32),handoff='op_'+'a'.repeat(32);
  const state:any={denied,hold,reads:[],writes:[],
    parent:{operation_id:id,actor:'owner',action:ACTION,target:{workspace_id:workspaceId,thread_id:threadId},status,params:{works:[{work_item_id:work},{work_item_id:second}]},
      result:{workspace_id:workspaceId,thread_id:threadId,adopted:true,stage:unknown?'start_unknown':'partially_started',works:[
        {work_item_id:work,stage:unknown?'start_unknown':'started',start_operation_id:child,handoff_operation_id:handoff,attempt_id:'att_'+'9'.repeat(32)},
        {work_item_id:second,stage:'not_started',start_operation_id:null,error_code:'WORK_ITEM_CHANGED'}]}},
    child:{operation_id:child,actor:'owner',action:'work.start',target:{workspace_id:workspaceId,work_item_id:work},status:unknown?'needs_attention':'succeeded',
      result:{stage:unknown?'start_unknown':'started',session:unknown?null:{host:'host-a',session_id:'session-a'},attempt_id:'att_'+'9'.repeat(32)}}};
  f.dispatch=async input=>{
    if(input.path===`/operations/${id}`){state.reads.push(input.path);if(state.hold)await state.hold;if(state.denied)return {status:404,data:{error:{code:'NOT_FOUND',message:'Source withdrawn'}}};return {status:200,data:{operation:structuredClone(state.parent)}};}
    if(input.path===`/operations/${child}`){state.reads.push(input.path);return {status:200,data:{operation:structuredClone(state.child)}};}
    if(input.path.startsWith('/work-items/'))return {status:200,data:{work_item:{work_item_id:input.path.split('/').at(-1),title:input.path.endsWith(work)?'Fix duplicate login':'Verify retry'}}};
    if(input.method==='POST'&&input.path.startsWith('/operations/')){state.writes.push(structuredClone(input));if(state.writeHold)await state.writeHold;return {status:200,data:{operation:structuredClone(state.parent)}};}
    return dispatch(input);
  };
  await mountProposal(page,f,locale);
  await page.evaluate(async({workspaceId,threadId,work,second,id,locale})=>{
    const win=window as any;win.proposalDispose();const {adoptStartLanding}=await import('/__proposal_src/pm-adopt-start-landing.js'),{STRINGS}=await import('/__proposal_src/i18n.js');
    const t=(key:string,vars:any={})=>(STRINGS[locale][key]||key).replace(/\{(\w+)\}/g,(_:string,name:string)=>vars[name]??'{'+name+'}');
    const api=async(method:string,path:string,body:any)=>{const result=await win.proposalFixtureRequest({method,path,body});if(result.status>=400)throw Object.assign(Error(result.data.error.message),{status:result.status,code:result.data.error.code});return result.data;};
    const caps=(await api('GET','/fixture-state')).caps;
    localStorage.setItem(`batc.pm.landing.owner.adopt-start-landing.${workspaceId}.${id}`,JSON.stringify({version:1,operation_id:id,thread_id:threadId,work_item_ids:[work,second]}));
    location.hash=`#/pm/${workspaceId}/board?select=${work}`;
    const mount=()=>{
      const element=adoptStartLanding({t,api,alive:()=>{},namespace:'landing.owner',workspaceId,caps:()=>caps,errorBox:(error:any)=>{const node=document.createElement('p');node.textContent=error.message;return node;}});
      document.getElementById('mount')!.replaceChildren(element.element);win.landingRefresh=element.refresh;win.landingWithdraw=element.withdraw;win.landing=element;
    };
    mount();win.remountLanding=()=>{const old=win.landing;mount();win.disposeEarlierLanding=()=>old.dispose();};
  },{workspaceId,threadId,work,second,id,locale});
  return {state,id,child,handoff};
}

async function followLanding(page:any,state:any,id:string,{ready=true,namespace='landing.owner'}={}) {
  if(ready){state.parent.status='succeeded';state.parent.result.stage='started';}
  state.parent.params.works=state.parent.params.works.slice(0,1);
  state.parent.result.works=state.parent.result.works.slice(0,1);
  await page.evaluate(({workspaceId,work,id,namespace})=>{
    const key=`batc.pm.landing.owner.adopt-start-landing.${workspaceId}.${id}`;
    const hint=JSON.parse(localStorage.getItem(key)!);hint.work_item_ids=[work];localStorage.setItem(key,JSON.stringify(hint));
    sessionStorage.setItem(`batc.pm.${namespace}.adopt-start-live.${workspaceId}.${id}`,work);
  },{workspaceId,work,id,namespace});
}

test('a fresh successful adoption opens its exact live Attempt once without another write',async({page})=>{
  const {state,id}=await landing(page);await followLanding(page,state,id);
  await page.evaluate(()=>(window as any).landingRefresh());
  await expect(page).toHaveURL(new RegExp(`/session/host-a/session-a\\?from=board&workspace=${workspaceId}&work=${work}&attempt=att_9{32}`));
  expect(state.writes).toEqual([]);
  expect(await page.evaluate(()=>Object.keys(sessionStorage).filter(key=>key.includes('adopt-start-live')))).toEqual([]);
  await page.evaluate(({workspaceId,work})=>{location.hash=`#/pm/${workspaceId}/board?select=${work}`;return (window as any).landingRefresh();},{workspaceId,work});
  await expect(page).toHaveURL(new RegExp('board\\?select='+work));
});

// The router can mount a replacement before an earlier asynchronous view load
// returns its disposer. Both instances read the same original receipt and tab hint.
for(const locale of ['en','zh-TW'])for(const destination of ['same-work','other-work','other-page'])
test(`Resume keeps the original navigation across an earlier view disposal: ${destination} ${locale}`,async({page},testInfo)=>{
  const {state,id}=await landing(page,{locale,status:'needs_attention'});
  await followLanding(page,state,id,{ready:false});
  // The child has reconciled its lost reply; the paused parent still records
  // start_unknown until its original Resume completes the readback.
  state.parent.result.stage='start_unknown';state.parent.result.works[0].stage='start_unknown';
  await page.evaluate(()=>(window as any).landingRefresh());
  await page.evaluate(()=>{(window as any).remountLanding();return (window as any).landingRefresh();});
  let release!:()=>void;state.writeHold=new Promise<void>(resolve=>{release=resolve;});
  await page.evaluate(()=>{
    const win=window as any;win.landingHashes=[];
    window.addEventListener('hashchange',()=>win.landingHashes.push(location.hash));
  });
  await page.locator('[data-adopt-start-landing]').getByRole('button',{name:locale==='en'?'Resume':'恢復',exact:true}).click();
  await expect.poll(()=>state.writes.length).toBe(1);
  if(destination!=='same-work')await page.evaluate(({workspaceId,second,destination})=>{
    location.hash=destination==='other-work'?`#/pm/${workspaceId}/board?select=${second}`:'#/settings';
  },{workspaceId,second,destination});
  await page.evaluate(()=>(window as any).disposeEarlierLanding());
  state.parent.status='succeeded';state.parent.result.stage='started';state.parent.result.works[0].stage='started';release();
  await expect(page.locator('[data-adopt-live]')).toHaveAttribute('href',new RegExp(`work=${work}&attempt=att_9{32}$`));
  await testInfo.attach('original-navigation', {contentType:'application/json',body:JSON.stringify({
    destination,locale,writes:state.writes,reads:state.reads,
    tab:await page.evaluate(()=>({hash:location.hash,hints:Object.entries(sessionStorage).filter(([key])=>key.includes('adopt-start-live'))})),
  })});
  if(destination==='same-work'){
    await expect(page).toHaveURL(new RegExp(`/session/host-a/session-a\\?from=board&workspace=${workspaceId}&work=${work}&attempt=att_9{32}$`));
    expect(await page.evaluate(()=>(window as any).landingHashes)).toEqual([
      `#/session/host-a/session-a?from=board&workspace=${workspaceId}&work=${work}&attempt=att_${'9'.repeat(32)}`]);
  }else await expect(page).toHaveURL(destination==='other-work'?new RegExp('board\\?select='+second):/#\/settings$/);
  expect(state.writes.map((row:any)=>({method:row.method,path:row.path,body:row.body})))
    .toEqual([{method:'POST',path:`/operations/${id}/resume`,body:{}}]);
  expect(await page.evaluate(()=>Object.keys(sessionStorage).filter(key=>key.includes('adopt-start-live')))).toEqual([]);
  await page.evaluate(({workspaceId,work})=>{location.hash=`#/pm/${workspaceId}/board?select=${work}`;return (window as any).landingRefresh();},{workspaceId,work});
  await expect(page).toHaveURL(new RegExp('board\\?select='+work));
});

test('another tab or connection navigation hint cannot open a completed adoption automatically',async({page})=>{
  const {state,id}=await landing(page);await followLanding(page,state,id,{namespace:'other.owner'});
  await page.evaluate(()=>(window as any).landingRefresh());
  await expect(page).toHaveURL(new RegExp('board\\?select='+work));expect(state.writes).toEqual([]);
});

for(const locale of ['en','zh-TW'])for(const destination of ['same-work','other-work','other-page'])
test(`Resume follows its own recovered Work after the original tab intent was withdrawn: ${destination} ${locale}`,async({page})=>{
  const {state,id}=await landing(page,{locale,status:'needs_attention'});
  await followLanding(page,state,id,{ready:false});
  state.parent.result.stage='start_unknown';state.parent.result.works[0].stage='start_unknown';
  await page.evaluate(()=>(window as any).landingRefresh());
  // The real board withdraws its landing on a view switch. The original
  // submission must not follow in the background or on a later passive visit.
  await page.evaluate(()=>{location.hash='#/settings';(window as any).landingWithdraw();});
  expect(await page.evaluate(()=>Object.keys(sessionStorage).filter(key=>key.includes('adopt-start-live')))).toEqual([]);
  await page.evaluate(({workspaceId,work})=>{location.hash=`#/pm/${workspaceId}/board?select=${work}`;return (window as any).landingRefresh();},{workspaceId,work});
  let release!:()=>void;state.writeHold=new Promise<void>(resolve=>{release=resolve;});
  await page.locator('[data-adopt-start-landing]').getByRole('button',{name:locale==='en'?'Resume':'恢復',exact:true}).click();
  // The POST is held, so this count is stable until the fixture releases it.
  await expect.poll(()=>state.writes.length).toBe(1);
  if(destination!=='same-work')await page.evaluate(({workspaceId,second,destination})=>{
    location.hash=destination==='other-work'?`#/pm/${workspaceId}/board?select=${second}`:'#/settings';
  },{workspaceId,second,destination});
  state.parent.status='succeeded';state.parent.result.stage='started';state.parent.result.works[0].stage='started';release();
  await expect(page.locator('[data-adopt-live]')).toHaveAttribute('href',new RegExp(`work=${work}&attempt=att_9{32}$`));
  await expect(page).toHaveURL(destination==='same-work'?new RegExp(`/session/host-a/session-a\\?from=board&workspace=${workspaceId}&work=${work}&attempt=att_9{32}$`)
    :destination==='other-work'?new RegExp('board\\?select='+second):/#\/settings$/);
  expect(state.writes.map((row:any)=>({method:row.method,path:row.path,body:row.body})))
    .toEqual([{method:'POST',path:`/operations/${id}/resume`,body:{}}]);
  expect(await page.evaluate(()=>Object.keys(sessionStorage).filter(key=>key.includes('adopt-start-live')))).toEqual([]);
});

for(const stop of ['partial','unknown','withdrawn','foreign','child-mismatch'])test(`automatic live navigation retains the ${stop} recovery view`,async({page})=>{
  const {state,id}=await landing(page,{unknown:stop==='unknown',denied:stop==='withdrawn',status:stop==='unknown'?'needs_attention':'failed'});
  await followLanding(page,state,id,{ready:!['partial','unknown'].includes(stop)});
  if(stop==='foreign')state.parent.actor='someone-else';
  if(stop==='child-mismatch')state.child.target.work_item_id=second;
  await page.evaluate(()=>(window as any).landingRefresh());
  await expect(page).toHaveURL(new RegExp('board\\?select='+work));expect(state.writes).toEqual([]);
});

test('leaving the selected Work during a delayed receipt cannot redirect the user back',async({page})=>{
  const {state,id}=await landing(page);await followLanding(page,state,id);
  let release!:()=>void;state.hold=new Promise<void>(resolve=>{release=resolve;});
  await page.evaluate(()=>{void (window as any).landingRefresh();});await expect.poll(()=>state.reads.length).toBe(1);
  await page.evaluate(({workspaceId,second})=>{location.hash=`#/pm/${workspaceId}/board?select=${second}`;},{workspaceId,second});
  release();await expect.poll(()=>state.reads.length).toBe(2);
  await expect(page).toHaveURL(new RegExp('board\\?select='+second));expect(state.writes).toEqual([]);
  expect(await page.evaluate(()=>Object.keys(sessionStorage).filter(key=>key.includes('adopt-start-live')))).toEqual([]);
});

test('landing preserves a partial plan and follows the fresh child receipt to the exact Attempt',async({page})=>{
  await landing(page);await page.evaluate(()=>(window as any).landingRefresh());const view=page.locator('[data-adopt-start-landing]');
  await expect(view).toContainText('Plan adopted; work partially started');await expect(view.getByRole('link',{name:'Start this work',exact:true})).toHaveCount(1);
  await expect(view.locator('[data-adopt-live]')).toHaveAttribute('href',new RegExp('workspace='+workspaceId+'&work='+work+'&attempt=att_9{32}'));
  expect(await page.evaluate(()=>Object.keys(localStorage).filter(key=>key.includes('landing.owner.adopt-start-landing')))).toEqual([]);
});
test('unknown landing offers original reconciliation and never a replacement start',async({page})=>{
  const {state,handoff}=await landing(page,{unknown:true,status:'needs_attention'});await page.evaluate(()=>(window as any).landingRefresh());
  const view=page.locator('[data-adopt-start-landing]');await expect(view.getByRole('link',{name:'Resume reconciliation',exact:true})).toHaveAttribute('href','#/op/'+handoff);
  await expect(view.getByRole('link',{name:'Start this work',exact:true})).toHaveCount(0);await expect(view.locator('[data-adopt-live]')).toHaveCount(0);
  await view.getByRole('button',{name:'Cancel remaining work; this does not settle an unknown start.',exact:true}).click();
  expect(state.writes.map((row:any)=>row.path)).toEqual(['/operations/op_'+'8'.repeat(32)+'/cancel']);
});
test('a withdrawn parent cannot expose sources',async({page})=>{
  const {state}=await landing(page,{denied:true});await page.evaluate(()=>(window as any).landingRefresh());
  await expect(page.locator('[data-adopt-start-landing]')).toBeHidden();expect(state.reads).toHaveLength(1);
  expect(await page.evaluate(()=>Object.keys(localStorage).filter(key=>key.includes('landing.owner.adopt-start-landing')))).toEqual([]);
});
test('a mismatched central parent cannot authorize a local landing hint',async({page})=>{
  const {state}=await landing(page);state.parent.actor='someone-else';await page.evaluate(()=>(window as any).landingRefresh());
  const view=page.locator('[data-adopt-start-landing]');await expect(view).not.toContainText('Fix duplicate login');
  await expect(view.locator('[data-adopt-live]')).toHaveCount(0);expect(state.reads).toHaveLength(1);expect(state.writes).toEqual([]);
});
test('a landing hint from another connection namespace is never read',async({page})=>{
  const {state}=await landing(page);await page.evaluate(()=>{
    const key=Object.keys(localStorage).find(key=>key.includes('landing.owner.adopt-start-landing'))!;
    localStorage.setItem(key.replace('landing.owner','other.owner'),localStorage.getItem(key)!);localStorage.removeItem(key);
  });await page.evaluate(()=>(window as any).landingRefresh());await expect(page.locator('[data-adopt-start-landing]')).toBeHidden();expect(state.reads).toEqual([]);
});

for(const locale of ['en','zh-TW'])test(`official adopt-and-start review keeps one discussion and readable controls ${locale}`,async({browser,baseURL},testInfo)=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage();try{
    const base=pmFixture(),{f,state}=fixture();Object.assign(base.state,{actor:'owner',plans:true,start:true,bound:true});base.state.thread.version=4;
    const dispatch=async(input:any)=>{
      const path=new URL(input.path,'http://fixture').pathname;
      if(path===`/pm/workspaces/${workspaceId}/kanban`)return {status:200,data:{version:1,workspace_id:workspaceId,project_id:null,observed_at:1791705600,journal_cursor:1,cards:[],next_cursor:null,wip:{scope:'returned_page',count:0,unknown:0,cards:0,archived_excluded:0},coverage:{},capabilities:{read_only:true}}};
      if(path.includes('/conversation-proposals')||path.endsWith('/projects')||path==='/work-items'||path.endsWith('/adopt-and-start-preview')||path.startsWith('/operations/')||input.body?.action===ACTION)return f.dispatch(input);
      const value=await base.dispatch(input);
      if(path==='/bootstrap'||path==='/capabilities'){
        const caps=path==='/bootstrap'?value.data.capabilities:value.data;Object.assign(caps.features.pm_workspaces,{kanban:true,conversation_proposals:{version:1},work_adopt_and_start:{version:1,available:true,max_selected_works:10}});
        caps.actions.push(...['pm.conversation.propose','pm.conversation.adopt',ACTION].map(action=>({action,allowed:true})));
      }
      if(path===`/pm/workspaces/${workspaceId}/capabilities`)value.data.native_analysis={eligible_for_preflight:true,authorized:true};
      if(path===pmPath&&value.status===200)value.data.next_message_cursor=1;
      return value;
    };
    await mountConversation(page,false,dispatch);await page.goto(pmRoute);await openThreadTab(page,'plan');
    await page.locator('[data-proposal-review]').click();await page.locator('[data-proposal-adopt-panel] textarea').last().fill(locale==='en'?'Adopt the reviewed repair':'採用已審閱的修正範圍');
    const row=page.locator(`[data-adopt-work="${work}"]`);await row.locator('.pm-adopt-choice > input').first().check();await row.getByLabel('Paid members can log in',{exact:true}).check();
    await page.getByRole('button',{name:locale==='en'?'Check readiness':'檢查就緒',exact:true}).click();
    await expect(page.getByRole('button',{name:locale==='en'?'Adopt and start':'採用並開始',exact:true})).toBeEnabled();
    for(const width of [1440,768,390]){
      await page.setViewportSize({width,height:900});
      const close=page.locator('.pd-panel-overlay .ui-inspector-back');if(width<800&&await close.isVisible())await close.click();
      await page.locator('[data-adopt-start]').scrollIntoViewIfNeeded();await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await page.screenshot({path:testInfo.outputPath(`adopt-start-${locale}-${width}.png`),fullPage:true});
    }
    expect(f.state.writes).toEqual([]);
    if(locale==='en'){
      state.lost=true;await page.getByRole('button',{name:'Adopt and start',exact:true}).click();
      const recover=page.locator(`[data-pm-action="${ACTION}"]`).getByRole('button',{name:'Check original request',exact:true});
      await expect(recover).toBeVisible();await page.reload();
      await expect(recover).toBeVisible();expect(f.state.writes).toHaveLength(1);
      await recover.click();await expect.poll(()=>f.state.writes.length).toBe(2);expect(f.state.writes[1]).toEqual(f.state.writes[0]);
      await expect(page).toHaveURL(new RegExp('board\\?select='+work));
      await expect(page.locator('[data-adopt-start-landing]')).toContainText('Queued to adopt and start');
      await expect(page).toHaveURL(new RegExp('board\\?select='+work));
    }
  }finally{await context.close();}
});
