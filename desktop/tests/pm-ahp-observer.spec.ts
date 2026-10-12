import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {pmFixture,workspaceId} from './pm-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
const route=`/dashboard/#/pm/${workspaceId}`,base=`/pm/workspaces/${workspaceId}`;
const contract={contract:'ahp_observer_v1',protocol_version:'1.0.0',implemented_subset:['initialize','listSessions','subscribe','reconnect'],observation:'supported',create_own:'unsupported',send:'unsupported',cancel:'unsupported',close:'unsupported',pm_writer:'unsupported',adapter_switch:'unsupported',manual_foreign_access:'read_only',tested_host_versions:[],live_host_verified:false,changesets:'static_advertised_snapshots_only',transcript:'unsupported',limits:{seconds:30,selected_sessions:8,changesets:16}};
function fixture(){
  const pm=pmFixture();pm.state.manage=false;pm.state.role='reader';
  const state={adapters:[{adapter_id:'editor',binding_digest:'a'.repeat(64),...contract},{adapter_id:'review',binding_digest:'b'.repeat(64),...contract}],
    rows:[] as any[],writes:[] as any[],ops:new Map<string,any>(),lost:false,pending:false,denied:false,bad:false,filteredPage:false,reads:[] as string[],defer:null as null|Promise<void>};
  const make=(adapterId='editor',count=10)=>{const index=state.rows.length+1,id='pmah_'+index.toString(16).padStart(32,'0'),binding=state.adapters.find(a=>a.adapter_id===adapterId)!.binding_digest;
    const r={sequence:index,observation_id:id,workspace_id:workspaceId,adapter_id:adapterId,document_digest:index.toString(16).repeat(64).slice(0,64),created_at:1791720000,actor:pm.state.actor,
      operation_id:'op_'+index.toString(16).padStart(32,'0'),coverage_status:'observed',document:{schema:'ahp_observation_v1',observation_id:id,workspace_id:workspaceId,adapter_id:adapterId,binding_digest:binding,started_at:1791720000,finished_at:1791720001,
      original_sources:[],evidence:{protocol_version:'1.0.0',server_info:{name:'Fake editor',version:'fixture'},status:'observed',reason:null,catalog_complete:true,
        catalog:Array.from({length:count},(_,i)=>({resource:`ahp-session://${adapterId}/${i+1}`,title:`${adapterId} session ${i+1}`,provider:'example-agent',source_identity:'c'.repeat(64),createdAt:'2026-10-11',modifiedAt:'2026-10-11',status:0,ownership:'foreign_or_manual',access:'read_only',completion:'not_inferred'})),
        snapshots:[{resource:'ahp-root://',from_seq:1,later_events_observed:false,state:{kind:'root'}}],events:[],gaps:[],missing:[],reconnect:'not_needed',completion:'not_inferred',cost:'not_inferred'}}};state.rows.push(r);return r;};
  const dispatch=async(input:any)=>{const url=new URL(input.path,'http://fixture'),path=url.pathname;state.reads.push(input.path);
    if(path==='/bootstrap'||path==='/capabilities'){const r=await pm.dispatch(input),c=path==='/bootstrap'?r.data.capabilities:r.data;
      c.features.pm_workspaces.ahp_observation=contract;c.actions.push({action:'pm.ahp.observe',allowed:true});return r;}
    if(path==='/events'&&pm.state.cursor)return {status:200,data:{events:Number(url.searchParams.get('after'))<pm.state.cursor?[{seq:pm.state.cursor,resource_type:'pm_ahp_observation',resource_id:'fixture',kind:'pm.ahp.observed'}]:[],next_cursor:pm.state.cursor,head_cursor:pm.state.cursor,has_more:false,sync:{checkpoint:{cursor:pm.state.cursor,token:'pm-'+pm.state.cursor}}}};
    if(path.startsWith(base+'/ahp-')){
      if(state.denied)return {status:404,data:{error:{code:'NOT_FOUND',message:'Source unavailable'}}};
      if(path.endsWith('/ahp-adapters'))return {status:200,data:{workspace_id:workspaceId,adapters:state.adapters}};
      if(path.endsWith('/current'))return {status:200,data:{workspace_id:workspaceId,observation:state.rows.at(-1)||null,visibility_filtered:false}};
      if(path===base+'/ahp-observations')return {status:200,data:{workspace_id:workspaceId,observations:state.filteredPage&&!url.searchParams.get('after')||state.filteredPage&&url.searchParams.get('after')==='0'?[]:state.rows,
        visibility_filtered:state.filteredPage,next_cursor:state.filteredPage&&url.searchParams.get('after')==='0'?20:null}};
      const r=state.rows.find(r=>path.endsWith(r.observation_id));if(!r)return {status:404,data:{error:{code:'NOT_FOUND',message:'Original source unavailable'}}};
      return {status:200,data:{workspace_id:workspaceId,observation:state.bad?{...r,document:{...r.document,workspace_id:'foreign'}}:r}};
    }
    if(input.method==='POST'&&path==='/operations'&&input.body.action==='pm.ahp.observe'){
      state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
      if(!op){const row=make(input.body.target.adapter_id);op={...input.body,actor:pm.state.actor,idempotency_key:input.idempotency_key,operation_id:row.operation_id,status:state.pending?'running':'succeeded',
        result:{workspace_id:workspaceId,adapter_id:row.adapter_id,observation_id:row.observation_id,document_digest:row.document_digest,coverage_status:row.coverage_status}};state.ops.set(input.idempotency_key,op);}
      if(state.defer)await state.defer;
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}return {status:200,data:{operation:op}};
    }
    if(path.startsWith('/operations/')){const op=[...state.ops.values()].find(op=>path.endsWith(op.operation_id));if(op)return {status:200,data:{operation:op}};}
    return pm.dispatch(input);
  };return {state,pm,make,dispatch};
}
async function open(page:any,native:boolean,f:ReturnType<typeof fixture>){await mountConversation(page,native,f.dispatch);await page.goto(route);await page.locator('[data-pm-ahp] > summary').click();await expect(page.getByRole('button',{name:'Capture host catalog',exact:true})).toBeEnabled();}
for(const native of [false,true])test.describe(native?'AHP Tauri bridge':'AHP Web',()=>{
  test('reader captures catalog explicitly, max eight original sessions and optional static changesets',async({page})=>{
    const f=fixture();await open(page,native,f);expect(f.state.writes).toHaveLength(0);await expect(page.getByRole('button',{name:'Capture selected snapshots'})).toBeDisabled();
    await page.getByRole('button',{name:'Capture host catalog',exact:true}).click();await expect(page.locator('[data-ahp-catalog] input[type=checkbox]')).toHaveCount(10);
    expect(f.state.writes[0].body).toEqual({action:'pm.ahp.observe',target:{workspace_id:workspaceId,adapter_id:'editor'},params:{session_uris:[],include_changesets:false},preconditions:{}});
    for(let i=0;i<8;i++)await page.locator('[data-ahp-catalog] input[type=checkbox]').nth(i).check();await expect(page.locator('[data-ahp-catalog] input[type=checkbox]').nth(8)).toBeDisabled();
    await page.getByLabel('Snapshot contents').selectOption('true');await page.getByRole('button',{name:'Capture selected snapshots'}).click();await expect.poll(()=>f.state.writes.length).toBe(2);
    expect(f.state.writes[1].body.params).toEqual({session_uris:Array.from({length:8},(_,i)=>`ahp-session://editor/${i+1}`),include_changesets:true});
    await expect(page.locator('[data-ahp-detail]')).toContainText('No transcript, file contents or completion proof');
    await expect(page.locator('[data-pm-ahp] button')).not.toContainText(['Create session','Send message','Stop session']);
  });
  test('selection draft survives reload and is separated by adapter and binding',async({page})=>{
    const f=fixture();f.make();await open(page,native,f);await page.getByLabel('Select editor session 1',{exact:true}).check();await page.getByLabel('Snapshot contents').selectOption('true');
    await page.reload();await page.locator('[data-pm-ahp] > summary').click();await expect(page.getByLabel('Select editor session 1',{exact:true})).toBeChecked();
    await expect(page.getByLabel('Snapshot contents')).toHaveValue('true');await page.getByLabel('Configured adapter').selectOption({label:'review'});
    await expect(page.locator('[data-ahp-catalog] input[type=checkbox]')).toHaveCount(0);await page.getByRole('button',{name:'Capture host catalog',exact:true}).click();await expect(page.getByLabel('Select review session 1',{exact:true})).not.toBeChecked();
    await page.getByLabel('Configured adapter').selectOption({label:'editor'});await expect(page.getByLabel('Select editor session 1',{exact:true})).toBeChecked();
    f.state.adapters[0].binding_digest='d'.repeat(64);await page.getByRole('button',{name:'Reload saved observations'}).click();await expect(page.locator('[data-ahp-catalog] input[type=checkbox]')).toHaveCount(0);
    expect(f.state.writes).toHaveLength(1);
  });
  test('unknown original request stays with adapter and exact key across reload',async({page})=>{
    const f=fixture();f.make();f.state.lost=true;await open(page,native,f);await page.getByLabel('Select editor session 1',{exact:true}).check();await page.getByRole('button',{name:'Capture selected snapshots'}).click();
    await expect(page.locator('[data-pm-ahp]')).toContainText('Reply unavailable');const original=structuredClone(f.state.writes[0]);
    await page.getByLabel('Configured adapter').selectOption({label:'review'});await expect(page.getByRole('button',{name:'Check original request'})).toHaveCount(0);
    await page.getByLabel('Configured adapter').selectOption({label:'editor'});await expect(page.getByLabel('Select editor session 1',{exact:true})).toBeDisabled();
    await page.reload();await page.locator('[data-pm-ahp] > summary').click();await page.getByRole('button',{name:'Check original request'}).click();await expect.poll(()=>f.state.writes.length).toBe(2);
    expect(f.state.writes[1].idempotency_key).toBe(original.idempotency_key);expect(f.state.writes[1].body).toEqual(original.body);
  });
  test('known running receipt recovery uses GET and no duplicate admission',async({page})=>{
    const f=fixture();f.state.pending=true;await open(page,native,f);await page.getByRole('button',{name:'Capture host catalog',exact:true}).click();await expect(page.locator('[data-pm-ahp]')).toContainText('running');
    await page.reload();await page.locator('[data-pm-ahp] > summary').click();await expect.poll(()=>f.state.reads.filter(s=>s.startsWith('/operations/')).length).toBeGreaterThan(0);expect(f.state.writes).toHaveLength(1);
  });
  test('empty filtered history preserves next cursor and saved details use original source',async({page})=>{
    const f=fixture();f.make();f.state.filteredPage=true;await open(page,native,f);await page.locator('[data-ahp-history] > summary').click();await expect(page.locator('[data-ahp-history]')).toContainText('current source access');
    await page.getByRole('button',{name:'Next saved observations'}).click();await expect.poll(()=>f.state.reads.some(s=>s.includes('after=20'))).toBe(true);
    await page.locator('.pm-ahp-history-row button').first().click();await expect(page.locator('[data-ahp-detail]')).toBeVisible();
    await page.getByText('Original source document and receipt',{exact:true}).click();await expect(page.locator('[data-ahp-detail] pre')).toContainText('foreign_or_manual');
    expect(f.state.writes).toHaveLength(0);
  });
  test('source withdrawal on event removes original titles and details without a write',async({page})=>{
    const f=fixture();f.make();await open(page,native,f);await expect(page.getByLabel('Select editor session 1',{exact:true})).toBeVisible();f.state.denied=true;f.pm.state.cursor++;
    await expect(page.locator('[data-pm-ahp]')).toContainText('Previous source content has been hidden');await expect(page.locator('[data-ahp-detail]')).toHaveCount(0);await expect(page.locator('[data-pm-ahp]')).not.toContainText('editor session 1');expect(f.state.writes).toHaveLength(0);
  });
  test('catalog source 404 preserves ability to capture a replacement without preserving its titles',async({page})=>{
    const f=fixture();f.make();await open(page,native,f);await page.getByLabel('Select editor session 1',{exact:true}).check();f.state.rows=[];await page.getByRole('button',{name:'Reload saved observations'}).click();
    await expect(page.locator('[data-ahp-catalog]')).toContainText('original catalog selection is unavailable');await expect(page.getByRole('button',{name:'Capture selected snapshots'})).toBeDisabled();
    await expect(page.getByRole('button',{name:'Capture host catalog',exact:true})).toBeEnabled();expect(f.state.writes).toHaveLength(0);
  });
  test('late catalog response cannot render under another adapter',async({page})=>{
    const f=fixture();let release!:()=>void;f.state.defer=new Promise<void>(resolve=>release=resolve);await open(page,native,f);
    await page.getByRole('button',{name:'Capture host catalog',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(1);await page.getByLabel('Configured adapter').selectOption({label:'review'});release();
    await expect(page.getByLabel('Configured adapter')).toHaveValue(/review/);await expect(page.locator('[data-ahp-catalog] input[type=checkbox]')).toHaveCount(0);await expect(page.locator('[data-ahp-detail]')).toHaveCount(0);
    await page.getByLabel('Configured adapter').selectOption({label:'editor'});await expect.poll(()=>f.state.reads.filter(s=>s.startsWith('/operations/')).length).toBeGreaterThan(0);expect(f.state.writes).toHaveLength(1);
  });
  test('incomplete observation preserves reason and original gaps, never completion',async({page})=>{
    const f=fixture(),r=f.make();r.coverage_status='incomplete';r.document.evidence.status='incomplete';r.document.evidence.catalog_complete=false;r.document.evidence.reason='catalog_page_limit';r.document.evidence.gaps=[{reason:'snapshot_fallback'}];
    await open(page,native,f);await expect(page.locator('[data-ahp-detail]')).toContainText('Incomplete observation');await expect(page.locator('[data-ahp-detail]')).toContainText('catalog_page_limit');await expect(page.locator('[data-ahp-detail]')).toContainText('Recorded replay gaps: 1');
  });
  test('snapshot metadata displays its negotiated protocol and later-event caveat',async({page})=>{
    const f=fixture(),r=f.make();Object.assign(f.state.adapters[0],{protocol_version:'0.10.0',supported_protocol_versions:['0.10.0','1.0.0']});
    Object.assign(r.document.evidence,{protocol_version:'0.10.0',negotiated_version:'0.10.0',snapshots:[{resource:'ahp-session://editor/1',from_seq:12,later_events_observed:true,state:{title:'Original snapshot title',provider:'example-agent',lifecycle:'ready'}},{resource:'ahp-changeset://editor/fixed',from_seq:13,later_events_observed:false,state:{files:[{id:'fixed-source-file',edit:{after:{uri:'file:///fixture/source.js'}}}]}}]});
    await open(page,native,f);await expect(page.locator('[data-ahp-detail]')).toContainText('Protocol: 0.10.0');await page.getByText('Snapshot details',{exact:true}).click();await page.getByText('Original snapshot title',{exact:true}).click();
    await expect(page.locator('[data-ahp-detail]')).toContainText('Later events recorded: Yes');await expect(page.locator('[data-ahp-detail]')).toContainText('Reported lifecycle (not a completion proof): ready');
  });
  test('catalog pagination does not drop selections from previous page',async({page})=>{
    const f=fixture();f.make('editor',22);await open(page,native,f);await page.getByLabel('Select editor session 1',{exact:true}).check();await page.locator('[data-ahp-catalog]').getByRole('button',{name:'Next',exact:true}).click();
    await page.getByLabel('Select editor session 21',{exact:true}).check();await page.getByRole('button',{name:'Capture selected snapshots'}).click();await expect.poll(()=>f.state.writes.length).toBe(1);
    expect(f.state.writes[0].body.params.session_uris).toEqual(['ahp-session://editor/1','ahp-session://editor/21']);
  });
  test('another central identity cannot inherit the original selection draft',async({page})=>{
    const f=fixture();f.make();await open(page,native,f);await page.getByLabel('Select editor session 1',{exact:true}).check();
    f.pm.state.principal='person-2';await page.reload();await page.locator('[data-pm-ahp] > summary').click();await expect(page.getByLabel('Select editor session 1',{exact:true})).not.toBeChecked();
    f.pm.state.principal='person-1';await page.reload();await page.locator('[data-pm-ahp] > summary').click();await expect(page.getByLabel('Select editor session 1',{exact:true})).toBeChecked();expect(f.state.writes).toHaveLength(0);
  });
  test('a mismatched saved document clears the whole source display',async({page})=>{
    const f=fixture();f.make();await open(page,native,f);await page.locator('[data-ahp-history] > summary').click();f.state.bad=true;await page.locator('.pm-ahp-history-row button').click();
    await expect(page.locator('[data-pm-ahp]')).toContainText('does not match its original workspace');await expect(page.locator('[data-ahp-detail]')).toHaveCount(0);await expect(page.locator('[data-ahp-catalog] input')).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
  });
});
test('AHP no configured adapters explains central prerequisite without an endpoint field',async({page})=>{
  const f=fixture();f.state.adapters=[];await mountConversation(page,false,f.dispatch);await page.goto(route);await page.locator('[data-pm-ahp] > summary').click();await expect(page.locator('[data-pm-ahp]')).toContainText('No AHP adapters are configured');await expect(page.locator('[data-pm-ahp] form')).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
});
test('AHP shared responsive observations in English and Traditional Chinese',async({browser,baseURL},testInfo)=>{
  const folder=process.env.PM_AHP_SCREENSHOTS||testInfo.outputPath('screenshots');mkdirSync(folder,{recursive:true});
  for(const locale of ['en','zh-TW']){
    for(const [width,height]of [[1440,900],[768,900],[390,844]]){const context=await browser.newContext({locale,baseURL,viewport:{width,height}}),page=await context.newPage(),f=fixture();f.make('editor',3);await mountConversation(page,false,f.dispatch);
      await page.goto(route);await page.locator('[data-pm-ahp] > summary').click();await expect(page.locator('[data-ahp-catalog] input[type=checkbox]').first()).toBeVisible();await expect(page.locator('[data-ahp-catalog] input[type=checkbox]')).toHaveCount(3);
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      const check=await page.locator('.pm-ahp-session input').first().boundingBox(),title=await page.locator('.pm-ahp-session strong').first().boundingBox();expect(check!.x+check!.width).toBeLessThan(title!.x);expect(Math.abs(check!.y-title!.y)).toBeLessThan(12);
      await page.screenshot({path:`${folder}/ahp-${locale}-${width}.png`,fullPage:true});await context.close();}}
});
