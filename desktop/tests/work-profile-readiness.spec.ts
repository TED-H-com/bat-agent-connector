import {openPmSection} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workStartFixture,workspaceId,workId,route} from './work-start-fixture.ts';
import {managedSetupFixture} from './managed-setup-fixture.ts';
import {readFileSync} from 'node:fs';
import {workProfileStrings} from '../src/pm-work-profile-strings.js';
const readinessGolden=JSON.parse(readFileSync(new URL('../../tests/fixtures/work-profile-readiness.json',import.meta.url),'utf8'));

const first='wpf_'+'a'.repeat(32),second='wpf_'+'b'.repeat(32);
const names=['discovered','signed_in_or_available','can_observe','can_start_own','can_continue_own','last_verified_execution'];

// Exercise the picker itself: the parent Work form's separate start-preview POST
// remains covered by the existing integration cases below.
async function mountGoldenPicker(page:any,body:any,locale:string){
  const reads:string[]=[],posts:string[]=[];
  page.on('request',(request:any)=>{if(request.method()==='POST')posts.push(request.url());});
  await page.route('**/api/v1/**',async(route:any)=>{
    const request=route.request(),path=new URL(request.url()).pathname;reads.push(path);
    expect(request.method()).toBe('GET');
    const profile=readinessGolden.profile;
    const data=path.endsWith('/readiness')?body:path.endsWith('/work-profiles')
      ?{profiles:[profile],count:1,default_profile_id:null,next_cursor:null}:profile;
    await route.fulfill({status:200,json:data});
  });
  await page.goto('/ui-components.html');
  await page.evaluate(async({body,locale})=>{
    const {workProfilePicker}=await import('/src/pm-work-profile-picker.js');
    const {t,setLocale}=await import('/src/i18n.js');const {h}=await import('/src/ui/dom.js');
    setLocale(locale==='en-US'?'en':locale);
    const field=document.createElement('input');field.type='hidden';
    location.hash=`#/pm/${body.workspace_id}/board?select=wi_${'1'.repeat(20)}`;
    const picker=workProfilePicker({t,api:async(method,path)=>{
      if(method!=='GET')throw Error('Picker must only read');
      const response=await fetch('/api/v1'+path);return response.json();
    },alive:()=>{},namespace:'fixture',workspaceId:body.workspace_id,workId:'wi_'+'1'.repeat(20),
    caps:()=>({scopes:['observe'],features:{pm_workspaces:{work_profiles:{version:1,available:true}}}}),field,
    request:()=>({locked:()=>false,set:(_key,value)=>{field.value=value;},update:()=>{}}),active:()=>true,onChange:()=>{},
    errorBox:error=>h('p',{},String(error))});
    document.getElementById('fixture')!.append(picker.box,field);await picker.load();
  },{body,locale});
  await page.locator('[data-work-profile-picker] select').selectOption(body.profile_id);
  return {reads,posts,facts:page.locator('[data-work-profile-readiness]')};
}
const goldenCases=Object.entries(readinessGolden.cases);
const future=structuredClone(readinessGolden.cases.never_observed);future.states.can_observe.reason='FUTURE_PROFILE_REASON';
for(const locale of ['en-US','zh-TW'])test.describe(`${locale} producer profile evidence`,()=>{
  test.use({locale});
  for(const width of [390,1440])for(const [name,body] of [...goldenCases,['unknown_reason',future]])
    test(`golden ${name} ${width}`,async({page})=>{
      await page.setViewportSize({width,height:900});
      const lang=locale==='en-US'?'en':locale,copy=(key:string)=>workProfileStrings[lang][key];
      const {facts,reads,posts}=await mountGoldenPicker(page,body,locale);
      const cells=facts.locator('dl.pm-work-profile-states > dd');await expect(cells).toHaveCount(6);
      for(const key of ['can_observe',...names.filter(key=>key!=='can_observe')]){
        const state=body.states[key],cell=cells.nth(names.indexOf(key));
        // This fails against the old value-only cells before any new selectors.
        if(state.reason)await expect(cell).toContainText(state.reason);
        const reasonKey='wpf_reason_'+state.reason;
        await expect(cell).toContainText(copy(reasonKey)||copy('wpf_unverified'));
        await expect(cell.locator('[data-work-profile-value]')).toHaveText(copy('wpf_'+state.value));
        expect((await cell.innerText()).trim().startsWith(copy('wpf_'+state.value))).toBe(true);
        if(state.verified){
          const past=cell.locator('[data-work-profile-past]');await expect(past).toBeVisible();
          await expect(past).toContainText(locale==='en-US'?'Last verified':'上次驗證');
          await expect(past.locator('time')).toHaveAttribute('datetime',new Date(state.verified.observed_at*1000).toISOString());
          expect(state.verified.observed_at).not.toBe(body.observed_at);
          await expect(past).toContainText(copy('wpf_'+state.verified.value));
        }else await expect(cell.locator('[data-work-profile-past]')).toHaveCount(0);
        const op=state.evidence_ref?.operation_id||state.verified?.evidence_ref?.operation_id;
        if(op)await expect(cell.locator(`a[href="#/op/${op}"]`)).toHaveCount(1);
        const success=state.evidence_ref?.last_success_at??state.verified?.evidence_ref?.last_success_at;
        if(success)await expect(cell.locator('[data-work-profile-last-success] time')).toHaveAttribute('datetime',new Date(success*1000).toISOString());
      }
      const read=facts.locator('[data-work-profile-read-at]');await expect(read).toContainText(locale==='en-US'?'Read at':'讀取時間');
      await expect(read.locator('time')).toHaveAttribute('datetime',new Date(body.observed_at*1000).toISOString());
      await expect(read).not.toContainText(locale==='en-US'?'verified':'驗證');
      await expect(facts.locator('[data-work-profile-gap]')).toHaveCount(body.smallest_gap?1:0);
      if(name==='host_unreachable'||name==='host_not_refreshed'){
        const cell=cells.nth(names.indexOf('can_observe'));
        await expect(cell).toContainText(locale==='en-US'?'does not contact the host':'不會連絡主機');
        await expect(cell).toContainText(copy('wpf_reason_'+name));
        expect(copy('wpf_reason_host_unreachable')).not.toBe(copy('wpf_reason_host_not_refreshed'));
      }
      expect(await facts.innerText()).not.toMatch(/scan_fixture|fixture-bat|\/srv\/|\/tmp\//);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      expect(reads.filter(path=>path.endsWith('/readiness'))).toHaveLength(1);expect(posts).toEqual([]);
    });
});

function fixture(){
  const f=workStartFixture(),base=f.dispatch;
  const profiles=[first,second].map((id,index)=>({profile_id:id,revision:1,digest:String(index+1).repeat(64),is_default:!index,
    agent:index?'codex':'claude',model:index?null:'example-model',delivery_goal:index?null:'reviewable_changes',
    repository:'example/library',host:'fixture-host',workspace_id:'fixture-workspace'}));
  const state:any={available:true,withdrawn:false,gap:null,held:null,reads:[],profiles};
  f.dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path.includes('/work-profiles')){
      state.reads.push(input.path);
      if(path.endsWith('/work-profiles'))return {status:200,data:{profiles:state.withdrawn?[]:structuredClone(profiles),count:state.withdrawn?0:2,default_profile_id:first,next_cursor:null}};
      const id=path.split('/work-profiles/')[1].split('/')[0],row=profiles.find(row=>row.profile_id===id);
      if(state.withdrawn||!row)return {status:404,data:{error:{code:'PM_WORK_PROFILE_WITHDRAWN',message:'Profile withdrawn'}}};
      if(!path.endsWith('/readiness'))return {status:200,data:structuredClone(row)};
      const data={contract:'work-profile-readiness/v1',workspace_id:workspaceId,profile_id:id,revision:row.revision,digest:row.digest,calls_bat:false,
        states:Object.fromEntries(names.map(name=>[name,{value:name==='can_observe'?'ready':'unknown',evidence_ref:null,verified:null}])),
        pm_analysis:{pm_ready:true},smallest_gap:structuredClone(state.gap)};
      if(state.nextTurn)Object.assign(data,{work_follow_up: state.nextTurn(row)});
      if(state.held)await state.held;return {status:200,data};
    }
    const result=await base(input);
    if(path==='/bootstrap'||path==='/capabilities'){
      const caps=path==='/bootstrap'?result.data.capabilities:result.data;
      if(state.available)caps.features.pm_workspaces.work_profiles={version:1,available:true};
    }
    if(path.endsWith('/start-preview')&&input.body?.profile_id&&result.data.code_target){
      const row=profiles.find(row=>row.profile_id===input.body.profile_id)!;
      Object.assign(result.data.preconditions,{expected_profile_revision:row.revision,expected_profile_digest:row.digest});
      result.data.code_target.source_ref='refs/heads/main';
    }
    return result;
  };
  return {f,state};
}
async function mount(page:any,f:any,native=false){
  await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'board');
  await page.locator(`[data-pm-kanban-work="${workId(1)}"] [data-board-open]`).click();
  return page.locator('[data-work-inspector]');
}
for(const native of [false,true])test(`${native?'Native':'Web'} profile selection pins only reviewed execution values`,async({page})=>{
  const {f}=fixture(),panel=await mount(page,f,native),select=panel.getByLabel('Saved profile',{exact:true});
  await expect(select).toHaveValue('');expect(f.state.previews).toEqual([]);
  await select.selectOption(first);await expect(panel.getByRole('button',{name:'Start work',exact:true})).toBeEnabled();
  expect(f.state.previews[0].body).toMatchObject({profile_id:first,profile_revision:1,delivery:'reviewable_changes',code_target:{repository:'example/library',host:'fixture-host',workspace_id:'fixture-workspace'}});
  expect(f.state.previews[0].body.agent).toBeUndefined();expect(f.state.previews[0].body.model).toBeUndefined();
  await expect(panel.getByLabel('Agent',{exact:true})).toBeDisabled();await expect(panel.getByLabel('Delivery',{exact:true})).toBeDisabled();
  await expect(panel.locator('[data-work-profile-readiness]')).toContainText('A saved setting is not a ready check.');
  await expect(panel.locator('[data-work-profile-readiness]')).toContainText('PM preflight is available.');
  await panel.getByRole('button',{name:'Start work',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params).toMatchObject({profile_id:first,agent:'claude',model:'example-model',delivery:'reviewable_changes'});
  expect(Object.keys(f.state.writes[0].body.preconditions)).toHaveLength(12);
  expect(f.state.writes[0].body.preconditions.expected_profile_digest).toBe('1'.repeat(64));
});
test('changing profiles invalidates the old preview and a null model is omitted',async({page})=>{
  const {f}=fixture(),panel=await mount(page,f),select=panel.getByLabel('Saved profile',{exact:true});
  await select.selectOption(first);await expect(panel.getByRole('button',{name:'Start work',exact:true})).toBeEnabled();
  await select.selectOption(second);await expect.poll(()=>f.state.previews.length).toBe(2);await expect(panel.getByLabel('Delivery',{exact:true})).toBeEnabled();
  await panel.getByRole('button',{name:'Start work',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params.profile_id).toBe(second);expect(f.state.writes[0].body.params.agent).toBe('codex');expect(f.state.writes[0].body.params.model).toBeUndefined();
  expect(f.state.writes[0].body.preconditions.expected_profile_digest).toBe('2'.repeat(64));
});
test('a withdrawn profile releases the pin and keeps the instruction draft',async({page})=>{
  const {f,state}=fixture();let panel=await mount(page,f);await panel.getByLabel('Saved profile',{exact:true}).selectOption(first);
  await panel.locator('[data-work-start-instruction]').fill('Preserve this instruction');state.withdrawn=true;
  await page.reload();await page.locator(`[data-pm-kanban-work="${workId(1)}"] [data-board-open]`).click();panel=page.locator('[data-work-inspector]');
  await expect(panel.getByLabel('Saved profile',{exact:true})).toHaveValue('');await expect(panel.getByLabel('Agent',{exact:true})).toBeEnabled();
  await expect(panel.locator('[data-work-start-instruction]')).toHaveValue('Preserve this instruction');await expect(panel.locator('[data-work-profile-readiness]')).toBeEmpty();expect(f.state.writes).toEqual([]);
});
test('returning without a saved profile preserves the legacy ten conditions',async({page})=>{
  const {f}=fixture(),panel=await mount(page,f),select=panel.getByLabel('Saved profile',{exact:true});
  await select.selectOption(first);await expect(panel.getByRole('button',{name:'Start work',exact:true})).toBeEnabled();await select.selectOption('');
  await expect(panel.getByLabel('Agent',{exact:true})).toBeEnabled();await expect(panel.getByRole('button',{name:'Start work',exact:true})).toBeEnabled();
  await panel.getByRole('button',{name:'Start work',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.previews.at(-1).body.profile_id).toBeUndefined();expect(f.state.writes[0].body.params.profile_id).toBeUndefined();expect(Object.keys(f.state.writes[0].body.preconditions)).toHaveLength(10);
});
test('only the offered setup gap links back to the current Work',async({page})=>{
  const {f,state}=fixture();state.gap={reason:'REPOSITORY_NOT_BOUND',fix:{action:'setup.repository',route:'POST /api/v1/operations',ui_hash:'#/settings?section=connection'},fallback:null};
  const panel=await mount(page,f);await panel.getByLabel('Saved profile',{exact:true}).selectOption(first);
  await panel.locator('[data-work-start-instruction]').fill('Return to this draft');
  const link=panel.getByRole('link',{name:'Set up repository binding',exact:true});await expect(link).toHaveAttribute('href',new RegExp('from='+encodeURIComponent('#/pm/'+workspaceId+'/board?select='+workId(1))));
  expect(state.reads.filter((path:string)=>path.endsWith('/readiness'))).toHaveLength(1);expect(f.state.writes).toEqual([]);
  await link.click();await page.getByRole('link',{name:'Back to work',exact:true}).click();
  await expect(page.locator('[data-work-start-instruction]')).toHaveValue('Return to this draft');
  await expect(page.locator('[data-work-profile-readiness]')).toBeFocused();expect(f.state.writes).toEqual([]);
});

test('late readiness cannot restore the previously selected profile',async({page})=>{
  const {f,state}=fixture(),panel=await mount(page,f);let release:any;state.held=new Promise(resolve=>release=resolve);
  await panel.getByLabel('Saved profile',{exact:true}).selectOption(first);await expect.poll(()=>state.reads.filter((p:string)=>p.endsWith('/readiness')).length).toBe(1);
  state.held=null;await panel.getByLabel('Saved profile',{exact:true}).selectOption(second);await expect(panel.getByLabel('Agent',{exact:true})).toHaveValue('codex');release();
  await expect(panel.getByRole('button',{name:'Start work',exact:true})).toBeEnabled();await expect(panel.getByLabel('Saved profile',{exact:true})).toHaveValue(second);
  expect(f.state.writes).toEqual([]);
});

test('lost start reply retains the original profile, request and key across reload',async({page})=>{
  const {f,state}=fixture();f.state.lostReply=true;let panel=await mount(page,f);
  await panel.getByLabel('Saved profile',{exact:true}).selectOption(first);
  await panel.getByRole('button',{name:'Start work',exact:true}).click();
  await expect(panel.getByRole('button',{name:'Check original request',exact:true})).toBeVisible();
  const original=structuredClone(f.state.writes[0]);state.profiles[0].revision=2;state.profiles[0].digest='3'.repeat(64);
  await page.reload();await page.locator(`[data-pm-kanban-work="${workId(1)}"] [data-board-open]`).click();panel=page.locator('[data-work-inspector]');
  await expect(panel.getByLabel('Saved profile',{exact:true})).toBeDisabled();expect(f.state.writes).toHaveLength(1);
  await panel.getByRole('button',{name:'Check original request',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(2);
  expect(f.state.writes[1].idempotency_key).toBe(original.idempotency_key);expect(f.state.writes[1].body).toEqual(original.body);
  await expect(panel.locator('[data-pm-saved]')).toBeVisible();
});

test('a profile revision change preserves the draft and requires a fresh preview',async({page})=>{
  const {f,state}=fixture();let panel=await mount(page,f);await panel.getByLabel('Saved profile',{exact:true}).selectOption(first);
  await expect(panel.getByRole('button',{name:'Start work',exact:true})).toBeEnabled();
  await panel.locator('[data-work-start-instruction]').fill('Keep this revised instruction');state.profiles[0].revision=2;state.profiles[0].digest='3'.repeat(64);
  await page.reload();await page.locator(`[data-pm-kanban-work="${workId(1)}"] [data-board-open]`).click();panel=page.locator('[data-work-inspector]');
  await expect(panel.locator('[data-work-start-instruction]')).toHaveValue('Keep this revised instruction');
  await expect(panel.getByRole('button',{name:'Start work',exact:true})).toBeDisabled();
  await panel.locator('[data-work-start-check]').click();await panel.getByRole('button',{name:'Start work',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.preconditions.expected_profile_revision).toBe(2);
  expect(f.state.writes[0].body.preconditions.expected_profile_digest).toBe('3'.repeat(64));
});

test('an instruction edited during preview cannot use that late response',async({page})=>{
  const {f}=fixture(),base=f.dispatch;let release:any,held=new Promise<void>(resolve=>release=resolve);
  f.dispatch=async(input:any)=>{const result=await base(input);if(input.path.endsWith('/start-preview'))await held;return result;};
  const panel=await mount(page,f);await panel.getByLabel('Saved profile',{exact:true}).selectOption(first);
  await expect.poll(()=>f.state.previews.length).toBe(1);await panel.locator('[data-work-start-instruction]').fill('Changed while checking');release();
  await expect(panel.locator('[data-work-start-stale]')).toBeVisible();await expect(panel.getByRole('button',{name:'Start work',exact:true})).toBeDisabled();
  await panel.locator('[data-work-start-check]').click();await panel.getByRole('button',{name:'Start work',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.params.instruction).toBe('Changed while checking');
});

test('a single-profile response cannot replace the selected identity',async({page})=>{
  const {f,state}=fixture();let panel=await mount(page,f);await panel.getByLabel('Saved profile',{exact:true}).selectOption(first);
  await expect(panel.getByRole('button',{name:'Start work',exact:true})).toBeEnabled();
  const base=f.dispatch;f.dispatch=async(input:any)=>{
    const result=await base(input);
    if(input.path.includes('/work-profiles?'))result.data.profiles=[];
    if(input.path.endsWith('/work-profiles/'+first))result.data=structuredClone(state.profiles[1]);
    return result;
  };
  // The installed dispatcher retains the function object; remount the transport
  // to exercise a bad single-read response after the original row left the page.
  await mountConversation(page,false,f.dispatch);await page.reload();await page.locator(`[data-pm-kanban-work="${workId(1)}"] [data-board-open]`).click();panel=page.locator('[data-work-inspector]');
  await expect(panel.getByLabel('Saved profile',{exact:true})).toHaveValue(first);await expect(panel.locator('[data-work-profile-readiness]')).toBeEmpty();
  await expect(panel.getByRole('button',{name:'Start work',exact:true})).toBeDisabled();expect(f.state.writes).toEqual([]);
});

test('withdrawal during start preview releases only the profile and retains the Work draft',async({page})=>{
  const {f,state}=fixture(),base=f.dispatch;f.dispatch=async(input:any)=>{
    if(input.path.endsWith('/start-preview')&&input.body?.profile_id){state.withdrawn=true;return {status:404,data:{error:{code:'PM_WORK_PROFILE_WITHDRAWN',message:'Profile withdrawn'}}};}
    return base(input);
  };
  const panel=await mount(page,f);await panel.locator('[data-work-start-instruction]').fill('Retain the work draft');
  await panel.getByLabel('Saved profile',{exact:true}).selectOption(first);
  await expect(panel.getByLabel('Saved profile',{exact:true})).toHaveValue('');await expect(panel.getByLabel('Agent',{exact:true})).toBeEnabled();
  await expect(panel.locator('[data-work-start-instruction]')).toHaveValue('Retain the work draft');await expect(panel.locator('[data-work-profile-readiness]')).toBeEmpty();
  await panel.locator('[data-work-start-check]').click();await expect(panel.getByRole('button',{name:'Start work',exact:true})).toBeEnabled();expect(f.state.writes).toEqual([]);
});

test('a refused profile request changes profile only after New request and gets a new key',async({page})=>{
  const {f}=fixture();f.state.refusalCode='PM_WORK_PROFILE_CHANGED';const panel=await mount(page,f);
  await panel.getByLabel('Saved profile',{exact:true}).selectOption(first);await panel.getByRole('button',{name:'Start work',exact:true}).click();
  await expect(panel.getByRole('button',{name:'New request',exact:true})).toBeVisible();await expect(panel.getByLabel('Saved profile',{exact:true})).toBeDisabled();
  const key=f.state.writes[0].idempotency_key;f.state.refusalCode=null;await panel.getByRole('button',{name:'New request',exact:true}).click();
  await panel.getByLabel('Saved profile',{exact:true}).selectOption(second);await panel.getByRole('button',{name:'Start work',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(2);expect(f.state.writes[1].idempotency_key).not.toBe(key);expect(f.state.writes[1].body.params.profile_id).toBe(second);
});

test('profile pages retain the selected identity without adopting a default',async({page})=>{
  const {f,state}=fixture(),base=f.dispatch;f.dispatch=async(input:any)=>{
    if(input.path.includes('/work-profiles?'))return {status:200,data:{profiles:[structuredClone(state.profiles[input.path.includes('cursor=1')?1:0])],count:2,default_profile_id:second,next_cursor:input.path.includes('cursor=1')?null:1}};
    return base(input);
  };
  const panel=await mount(page,f),select=panel.getByLabel('Saved profile',{exact:true});await expect(select).toHaveValue('');
  await panel.getByRole('button',{name:'More profiles',exact:true}).click();await expect(select.locator('option')).toHaveCount(3);await expect(select).toHaveValue('');
  await select.selectOption(second);await expect(panel.getByRole('button',{name:'Start work',exact:true})).toBeEnabled();expect(f.state.writes).toEqual([]);
});

test('New request rereads a changed profile while keeping its fields pinned',async({page})=>{
  const {f,state}=fixture();f.state.refusalCode='PM_WORK_PROFILE_CHANGED';const panel=await mount(page,f);
  await panel.getByLabel('Saved profile',{exact:true}).selectOption(first);await expect(panel.getByRole('button',{name:'Start work',exact:true})).toBeEnabled();
  state.profiles[0].revision=2;state.profiles[0].digest='3'.repeat(64);state.profiles[0].agent='codex';
  await panel.getByRole('button',{name:'Start work',exact:true}).click();await expect(panel.getByRole('button',{name:'New request',exact:true})).toBeVisible();
  f.state.refusalCode=null;await panel.getByRole('button',{name:'New request',exact:true}).click();
  await expect(panel.getByLabel('Agent',{exact:true})).toHaveValue('codex');await expect(panel.getByLabel('Agent',{exact:true})).toBeDisabled();
  await panel.locator('[data-work-start-check]').click();await panel.getByRole('button',{name:'Start work',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(2);expect(f.state.writes[1].body.preconditions.expected_profile_revision).toBe(2);
});

for(const reason of ['git_execution_location_changed','local_git_not_enabled','host_unreachable'])test(`readiness gap ${reason} does not offer a substitute route or probe`,async({page})=>{
  const {f,state}=fixture();state.gap={reason,fix:null,fallback:null};const panel=await mount(page,f);await panel.getByLabel('Saved profile',{exact:true}).selectOption(first);
  await expect(panel.locator('[data-work-profile-readiness]')).toContainText('PM preflight');
  await expect(panel.locator('[data-work-profile-readiness] a')).toHaveCount(0);expect(state.reads.filter((p:string)=>p.endsWith('/readiness'))).toHaveLength(1);expect(f.state.writes).toEqual([]);
});

for(const native of [false,true])test(`${native?'Native':'Web'} connection return links accept only the original bounded destinations`,async({page})=>{
  const state=await managedSetupFixture(page,native),board=`#/pm/${workspaceId}/board?select=${workId(1)}`;
  for(const from of [board,'#/published','#/dispatch/project/work',`#/pm/${workspaceId}/discussions/thread?revision=original`]){
    await page.goto('/dashboard/#/settings?section=connection&from='+encodeURIComponent(from));await expect(page.locator('[data-connection-return]')).toHaveAttribute('href',from);
  }
  for(const from of [board+'&extra=1','#/published?extra=1','#/dispatch/a/b/c','#/dispatch/../b','#//example.test','#/session/%0a/id','https://example.test']){
    await page.goto('/dashboard/#/settings?section=connection&from='+encodeURIComponent(from));await expect(page.locator('[data-connection-return]')).toHaveCount(0);
  }
  expect(state.posts).toEqual([]);
});

for(const locale of ['en','zh-TW'])test(`saved profile review remains readable ${locale}`,async({browser,baseURL},testInfo)=>{
  const context=await browser.newContext({baseURL,locale}),page=await context.newPage();try{
    const {f}=fixture(),panel=await mount(page,f);await panel.getByLabel(locale==='en'?'Saved profile':'已存設定檔',{exact:true}).selectOption(first);
    await expect(panel.locator('[data-work-profile-readiness]')).toContainText(locale==='en'?'A saved setting is not a ready check.':'已設定不等於就緒。');
    for(const width of [1440,768,390]){await page.setViewportSize({width,height:900});await panel.locator('[data-work-profile-picker]').scrollIntoViewIfNeeded();
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:testInfo.outputPath(`profile-${locale}-${width}.png`),fullPage:true});}
  }finally{await context.close();}
});

for (const locale of ['en', 'zh-TW']) for (const native of [false, true]) test(`profile reports the Work next-turn limit before starting ${locale} ${native ? 'IPC' : 'HTTP'}`, async ({browser, baseURL}, testInfo) => {
  const context = await browser.newContext({baseURL, locale, viewport: {width: locale === 'en' ? 1440 : 390, height: 900}});
  try {
    const page = await context.newPage(), {f, state} = fixture();
    state.nextTurn = (row:any) => ({route: 'bat_work_start', agent: row.agent, admission_checked: false,
      state: row.agent === 'codex' ? 'unavailable' : 'requires_attempt', reason: row.agent === 'codex' ? 'codex_uncorrelated_turn_boundary' : 'owned_attempt_required'});
    const panel = await mount(page, f, native), select = panel.getByLabel(locale === 'en' ? 'Saved profile' : '已存設定檔', {exact: true});
    await select.selectOption(second);
    await expect(panel.getByLabel(locale === 'en' ? 'Agent' : '代理', {exact: true})).toHaveValue('codex');
    const note = panel.locator('[data-work-profile-next-turn]');
    await expect(note).toContainText(locale === 'en' ? 'This BAT Codex route does not currently support another turn.' : '這條 BAT Codex 路線目前不支援追加下一輪。');
    await note.scrollIntoViewIfNeeded();
    await page.screenshot({path: testInfo.outputPath(`next-turn-${locale}-${native}.png`), fullPage: true});
    await select.selectOption(first);
    await expect(note).toContainText(locale === 'en' ? 'Availability is checked against the owned attempt after starting.' : '開始後會再核對自己的工作嘗試');
    await expect(note).not.toContainText('Codex');
    state.nextTurn = () => undefined;
    await select.selectOption(second);
    await expect(note).toContainText(locale === 'en' ? 'This connection has not reported next-turn support' : '這次連線尚未回報');
    expect(f.state.writes).toEqual([]);
  } finally {await context.close();}
});
