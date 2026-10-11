import {test, expect, type Page} from '@playwright/test';
import {readFile, mkdir} from 'node:fs/promises';
import path from 'node:path';

const workspaceId='ws_'+'1'.repeat(32), sprintId='pms_'+'2'.repeat(32), planId='pmrev_'+'3'.repeat(32), pid='prj_'+'4'.repeat(20);
const actions=['pm.product_goal.revise','pm.dod.revise','pm.backlog.revise','pm.sprint.create','pm.sprint.goal.revise','pm.sprint.select','pm.sprint.start','pm.sprint.close','pm.sprint.cancel'];
const action=(page:Page,name:string)=>page.locator(`[data-pm-action="${name}"]`);
function fixture() {
  const wid=(i:number)=>'wi_'+String(i).repeat(20), bid=(i:number)=>'pmwb_'+String(i).repeat(32);
  const bindings=[1,2].map(i=>({workspace_id:workspaceId,binding_id:bid(i),work_item_id:wid(i),project_id:pid,plan_revision_id:planId,requirement_ids:['membership.login'],execution_spec:'not_assigned'}));
  const artifact=(kind:string,prefix:string,document:any)=>({workspace_id:workspaceId,revision_id:prefix+'1'.repeat(32),kind,document,actor:'owner',sequence:1});
  const goal=artifact('product_goal','pmgoal_',{text:'Members can regain account access'}),dod=artifact('dod','pmdod_',{criteria:[{id:'tests',text:'Relevant tests verify the fixed change'}]}),
    backlog=artifact('backlog','pmbkl_',{product_goal_revision_id:goal.revision_id,items:bindings});
  const all=new Map([goal,dod,backlog].map(row=>[row.revision_id,row]));
  const state:any={role:'owner',actor:'owner',manage:true,elapsed:false,lost:false,failRead:false,wrongScope:false,planId,writes:[],ops:new Map(),artifacts:{workspace_id:workspaceId,product_goal:goal,dod,backlog},
    sprints:[{workspace_id:workspaceId,sprint_id:sprintId,version:2,status:'planned',sequence:1,definition:{title:'Recover account access',mode:'personal_iteration',owner_actor:'owner',developers:['owner','developer'],
      scrum_master:null,product_goal_revision_id:goal.revision_id,dod_revision_id:dod.revision_id,backlog_revision_id:backlog.revision_id,
      starts_at:new Date(Date.now()-3600000).toISOString(),ends_at:new Date(Date.now()+604800000).toISOString(),capacity_note:'One focused delivery, two developers'},
      goal:{sprint_id:sprintId,text:'A returning member can sign in',revision_id:'pmsg_'+'1'.repeat(32),sequence:1,actor:'owner',rationale:'Keep this round focused'},
      selected:[{...bindings[0],assignee:'owner',how:'Verify login in my own environment'}],history:[]}]};
  const config=()=>({caps:{actor:state.actor,scopes:['observe',...(state.manage?['manage']:[])],actions:actions.map(action=>({action,allowed:state.manage}))},
    workspace:{workspace_id:workspaceId,version:4,membership:{actor:state.actor,role:state.role},
      members:[{actor:'owner',role:state.actor==='owner'?state.role:'owner'},{actor:'developer',role:'contributor'},{actor:'reader',role:'reader'}]}});
  const detail=(s:any)=>({version:1,sprint:{...s,workspace_id:state.wrongScope?'ws_'+'9'.repeat(32):workspaceId},goal:s.goal,selected_items:s.selected,
    goal_revisions:[s.goal],selection_revisions:[],history:s.history,timebox_elapsed:state.elapsed,quality:'unassessed',history_next_cursors:state.more?{goals:1}: {}});
  const response=(data:any,status=200)=>({data,status});
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),pathname=url.pathname,base='/pm/workspaces/'+workspaceId;
    if(input.method==='GET'){
      if(pathname.startsWith('/operations/'))return response({operation:[...state.ops.values()].find((op:any)=>pathname.endsWith(op.operation_id))});
      if(state.failRead)return response({error:{code:state.failRead==='revoked'?'FORBIDDEN':'UNAVAILABLE',message:state.failRead==='revoked'?'Workspace access removed':'Sprint read unavailable'}},state.failRead==='revoked'?403:503);
      if(pathname===base+'/sprint-artifacts')return response(state.artifacts);
      if(pathname.startsWith(base+'/sprint-artifacts/')&&pathname.split('/').length===6)return response({revisions:[state.artifacts[pathname.split('/').at(-1)!]],next_cursor:null});
      if(pathname.startsWith(base+'/sprint-artifacts/'))return response({artifact:all.get(pathname.split('/').at(-1)!)});
      if(pathname===base+'/plans/accepted')return response({head:{workspace_id:workspaceId,accepted_revision_id:state.planId},plan:{workspace_id:workspaceId,revision_id:state.planId}});
      if(pathname===base+'/work')return response({workspace_id:workspaceId,bindings,next_cursor:null});
      if(pathname===base+'/sprints')return response({sprints:state.sprints,next_cursor:null});
      if(pathname.includes('/history/'))return response({workspace_id:workspaceId,sprint_id:sprintId,kind:'goals',records:[{...state.sprints[0].goal,sequence:2,text:'Preserved earlier direction'}],next_cursor:null});
      if(pathname.startsWith(base+'/sprints/'))return response(detail(state.sprints.find((s:any)=>pathname.endsWith(s.sprint_id))));
      if(pathname==='/projects/'+pid)return response({project:{project_id:pid},work_items:[{work_item_id:wid(1),title:'Member login',children:[]},{work_item_id:wid(2),title:'Password recovery',children:[]}]});
      throw Error('Unexpected read '+input.path);
    }
    if(input.method!=='POST'||pathname!=='/operations')throw Error('Unexpected write '+input.path);
    state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
    if(!op){const {action,target,params}=input.body;let result:any={workspace_id:workspaceId,execution_authorized:false};
      if(action==='pm.sprint.create'){
        const id='pms_'+String(state.sprints.length+2).repeat(32),s={workspace_id:workspaceId,sprint_id:id,version:1,status:'planned',sequence:state.sprints.length+1,
          definition:{...params,product_goal_revision_id:state.artifacts.product_goal.revision_id},selected:[],history:[],
          goal:{sprint_id:id,text:params.goal,revision_id:'pmsg_'+'2'.repeat(32),sequence:1,actor:state.actor,rationale:params.rationale}};
        state.sprints.push(s);result.sprint_id=id;
      }else if(action.startsWith('pm.sprint.')){
        const s=state.sprints.find((row:any)=>row.sprint_id===target.sprint_id);s.version++;
        if(action==='pm.sprint.select')s.selected=params.items.map((row:any)=>({...bindings.find(i=>i.binding_id===row.binding_id),...row}));
        if(action==='pm.sprint.start')s.status='active';if(action==='pm.sprint.close')s.status='closed';if(action==='pm.sprint.cancel')s.status='cancelled';
        if(action==='pm.sprint.goal.revise')s.goal={...s.goal,text:params.goal,sequence:2};
        s.history.push({sprint_id:s.sprint_id,workspace_id:workspaceId,sequence:s.history.length+1,actor:state.actor,action,document:{rationale:params.rationale,
          items:s.selected.map((row:any)=>({...row,quality:'unassessed',reason:'DoD has not been assessed',counts_as_done:false}))}});result.sprint_id=s.sprint_id;
      }else{
        const kind=action.split('.')[1],prior=state.artifacts[kind],revision_id=prior.revision_id.replace(/1/g,'2');
        const document=kind==='product_goal'?{text:params.text}:kind==='dod'?{criteria:params.criteria}:{product_goal_revision_id:params.product_goal_revision_id,items:params.binding_ids.map((id:string)=>bindings.find(row=>row.binding_id===id))};
        const next={...prior,revision_id,document};all.set(revision_id,next);state.artifacts[kind]=next;result.revision_id=revision_id;
      }
      op={...input.body,actor:state.actor,idempotency_key:input.idempotency_key,operation_id:'op_'+String(state.ops.size+1).padStart(32,'0'),status:'succeeded',result};state.ops.set(input.idempotency_key,op);
    }
    if(state.lost){state.lost=false;return response({error:{code:'LOST_REPLY',message:'Reply unavailable'}},503);}
    return response({operation:op});
  };
  return {state,config,dispatch,bindings};
}
async function mount(page:Page,f:ReturnType<typeof fixture>,locale='en') {
  await page.exposeFunction('sprintFixtureRequest',f.dispatch);await page.exposeFunction('sprintFixtureConfig',f.config);
  await page.route('**/__sprint_src/*.js',async route=>{
    const file=path.basename(new URL(route.request().url()).pathname);
    let source=await readFile(new URL('../src/'+file,import.meta.url),'utf8');
    // Exercise the actual shared durable form without exporting a test hook in production.
    if(file==='pm-workspace.js')source=source.replace('function durableForm(', 'export function durableForm(');
    await route.fulfill({contentType:'text/javascript',body:source});
  });
  await page.route('**/__sprint_harness',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/dashboard/app.css"><div class="pm-shell"><aside><h2>PM workspace</h2><p>Membership service</p></aside><main class="pm-main" id="mount"></main></div>'}));
  await page.goto('/__sprint_harness');
  await boot(page,locale);
}
async function boot(page:Page,locale='en') {
  await page.evaluate(async locale=>{
    const {workspaceSprints}=await import('/__sprint_src/pm-sprints.js'),{durableForm}=await import('/__sprint_src/pm-workspace.js');
    const {pmSprintStrings}=await import('/__sprint_src/pm-sprint-strings.js'),{pmStrings}=await import('/__sprint_src/pm-strings.js');
    const strings={...pmStrings[locale],...pmSprintStrings[locale]},t=(key:string,args:any={})=>Object.entries(args).reduce((text,[name,value])=>text.replaceAll('{'+name+'}',String(value)),strings[key]||key);
    const h=(tag:string,attrs:any={},...children:any[])=>{const el=document.createElement(tag);for(const [key,value]of Object.entries(attrs||{})){
      if(value===null||value===undefined||value===false)continue;if(key.startsWith('on'))el.addEventListener(key.slice(2),value as any);else el.setAttribute(key,value===true?'':String(value));
    }for(const child of children.flat()){if(child===null||child===undefined||child===false)continue;el.append(child instanceof Node?child:document.createTextNode(String(child)));}return el;};
    const win=window as any;let config=await win.sprintFixtureConfig();
    const api=async(method:string,path:string,body:any,key:any)=>{const result=await win.sprintFixtureRequest({method,path,body,idempotency_key:key});
      if(result.status>=400)throw Object.assign(Error(result.data.error.message),{code:result.data.error.code});return result.data;};
    const errorBox=(error:any)=>h('p',{class:'error'},error.message),input=(label:string,{multiline=false,max=4000,required=true,value=''}:any={})=>{
      const field=h(multiline?'textarea':'input',{'aria-label':t(label),maxlength:max,required}) as HTMLInputElement;field.value=value;return field;
    },labelled=(key:string,field:any)=>h('label',{},t(key),field),caps=()=>config.caps,alive=()=>{};
    const form=(options:any)=>durableForm({h,t,api,caps,guard:alive,namespace:'sprint-ui.'+config.caps.actor,errorBox,...options});
    const ui=workspaceSprints({h,t,api,alive,workspaceId:config.workspace.workspace_id,workspace:()=>config.workspace,caps,
      permitted:(decide:boolean)=>config.caps.scopes.includes('manage')&&(decide?config.workspace.membership.role==='owner':['owner','contributor'].includes(config.workspace.membership.role)),
      form,input,labelled,errorBox});document.getElementById('mount')!.append(ui.box);
    win.refreshSprint=async()=>{config=await win.sprintFixtureConfig();await ui.refresh();};await ui.refresh();
  },locale);
  await expect(page.locator('[data-pm-sprints]')).toBeVisible();
}
async function openAction(page:Page,name:string){await action(page,name).evaluate(el=>{let parent=el.parentElement;while(parent){if(parent.tagName==='DETAILS')(parent as HTMLDetailsElement).open=true;parent=parent.parentElement;}});}
const reason=async(page:Page,name:string,text='A deliberate management decision')=>{await openAction(page,name);await action(page,name).getByLabel('Reason for this decision',{exact:true}).fill(text);};

test('fixed Goal, period, work and DoD share one view without fabricated completion',async({page})=>{
  const f=fixture();await mount(page,f);const view=page.locator('[data-pm-sprints]');
  await expect(view).toContainText('A returning member can sign in');await expect(view).toContainText('Relevant tests verify the fixed change');
  await expect(view).toContainText('DoD is unassessed');await expect(view.getByRole('tab')).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
});
test('explicit creation pins references and responsibilities without starting work',async({page})=>{
  const f=fixture();await mount(page,f);const name='pm.sprint.create';await openAction(page,name);const editor=action(page,name);
  await editor.getByLabel('Sprint name',{exact:true}).fill('Account recovery');await editor.getByLabel('Sprint Goal',{exact:true}).fill('A member can reset a forgotten password');
  await editor.getByLabel('Start (UTC)',{exact:true}).fill('2026-10-12T09:00');await editor.getByLabel('End (UTC)',{exact:true}).fill('2026-10-19T09:00');
  await editor.getByLabel('owner',{exact:true}).check();await editor.getByLabel('Capacity and availability',{exact:true}).fill('One developer this week');
  await reason(page,name);await editor.getByRole('button',{name:'Save Sprint plan',exact:true}).click();
  await expect(page.locator('[data-pm-sprints]')).toContainText('A member can reset a forgotten password');
  expect(f.state.writes).toHaveLength(1);const request=f.state.writes[0].body;
  expect(request.params.starts_at).toBe('2026-10-12T09:00:00.000Z');expect(request.params.developers).toEqual(['owner']);
  expect(request.params.backlog_revision_id).toBe(f.state.artifacts.backlog.revision_id);expect(request.params.dod_revision_id).toBe(f.state.artifacts.dod.revision_id);
  expect(request.preconditions).toEqual({expected_workspace_version:4});expect(request.params).not.toHaveProperty('_baseline');
});
test('developer changes own How while preserving another developer and does not dispatch',async({page})=>{
  const f=fixture();f.state.actor='developer';f.state.role='contributor';await mount(page,f);const name='pm.sprint.select';await openAction(page,name);const editor=action(page,name);
  await expect(editor.getByLabel('Implementation approach for Member login',{exact:true})).toBeDisabled();
  await expect(editor.getByLabel('Member login',{exact:true})).toBeDisabled();await editor.getByLabel('Password recovery',{exact:true}).check();
  await editor.getByLabel('Implementation approach for Password recovery',{exact:true}).fill('Use my own CLI and check reset expiry');
  await reason(page,name);await editor.getByRole('button',{name:'Save work selection',exact:true}).click();
  expect(f.state.writes.map((row:any)=>row.body.action)).toEqual([name]);expect(f.state.writes[0].body.params.items).toEqual([
    {binding_id:f.bindings[0].binding_id,assignee:'owner',how:'Verify login in my own environment'},
    {binding_id:f.bindings[1].binding_id,assignee:'developer',how:'Use my own CLI and check reset expiry'}]);
});
test('owner cannot write another Developer How and readonly identity cannot cancel',async({page})=>{
  const f=fixture();f.state.sprints[0].selected[0].assignee='developer';await mount(page,f);await openAction(page,'pm.sprint.select');
  await expect(action(page,'pm.sprint.select').getByLabel('Implementation approach for Member login',{exact:true})).toBeDisabled();
  f.state.role='contributor';await page.evaluate(()=> (window as any).refreshSprint());await openAction(page,'pm.sprint.cancel');
  await expect(action(page,'pm.sprint.cancel').locator('button[type=submit]')).toBeDisabled();expect(f.state.writes).toHaveLength(0);
});
test('lost start acknowledgement remains recoverable after active state and reload with exact key',async({page})=>{
  const f=fixture();await mount(page,f);await reason(page,'pm.sprint.start');f.state.lost=true;
  await action(page,'pm.sprint.start').getByRole('button',{name:'Start Sprint',exact:true}).click();await expect(action(page,'pm.sprint.start')).toContainText('Reply unavailable');
  await page.reload();await boot(page);
  await action(page,'pm.sprint.start').getByRole('button',{name:'Check original request',exact:true}).click();
  expect(f.state.ops.size).toBe(1);expect(f.state.writes[1].body).toEqual(f.state.writes[0].body);expect(f.state.writes[1].idempotency_key).toBe(f.state.writes[0].idempotency_key);
});
test('unsent draft freezes on remote version change and keeps its baseline across remount',async({page})=>{
  const f=fixture();await mount(page,f);await reason(page,'pm.sprint.cancel','Retain this draft');
  f.state.sprints[0].version=9;await page.evaluate(()=> (window as any).refreshSprint());
  await expect(action(page,'pm.sprint.cancel').getByLabel('Reason for this decision',{exact:true})).toHaveValue('Retain this draft');
  await expect(action(page,'pm.sprint.cancel').getByRole('button',{name:'Cancel Sprint',exact:true})).toBeDisabled();
  await page.reload();await boot(page);await openAction(page,'pm.sprint.cancel');
  await expect(action(page,'pm.sprint.cancel').getByLabel('Reason for this decision',{exact:true})).toHaveValue('Retain this draft');
  await expect(action(page,'pm.sprint.cancel').getByRole('button',{name:'Cancel Sprint',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Review updated baseline',exact:true}).click();await openAction(page,'pm.sprint.cancel');
  await action(page,'pm.sprint.cancel').getByRole('button',{name:'Cancel Sprint',exact:true}).click();
  expect(f.state.writes[0].body.preconditions).toEqual({expected_version:9});
});
test('close only after elapsed period preserves unknown quality and does not claim Review',async({page})=>{
  const f=fixture();f.state.sprints[0].status='active';await mount(page,f);await openAction(page,'pm.sprint.close');
  await expect(action(page,'pm.sprint.close').getByRole('button',{name:'Close elapsed Sprint',exact:true})).toBeDisabled();
  f.state.elapsed=true;await page.evaluate(()=> (window as any).refreshSprint());await reason(page,'pm.sprint.close');
  await action(page,'pm.sprint.close').getByRole('button',{name:'Close elapsed Sprint',exact:true}).click();
  await expect(page.locator('[data-pm-sprint-closure]')).toContainText('DoD has not been assessed');
  await expect(page.locator('[data-pm-sprint-closure]')).toContainText('Review and Retrospective are not recorded by closing');
  expect(f.state.writes.map((row:any)=>row.body.action)).toEqual(['pm.sprint.close']);
});
test('failed or mismatched read freezes mutations while preserving form text',async({page})=>{
  const f=fixture();await mount(page,f);await reason(page,'pm.sprint.cancel','Keep my text');f.state.failRead=true;
  await page.evaluate(()=> (window as any).refreshSprint());await expect(action(page,'pm.sprint.cancel').locator('button[type=submit]')).toBeDisabled();
  await expect(action(page,'pm.sprint.cancel').getByLabel('Reason for this decision',{exact:true})).toHaveValue('Keep my text');
  f.state.failRead=false;f.state.wrongScope=true;await page.evaluate(()=> (window as any).refreshSprint());
  await expect(action(page,'pm.sprint.cancel').locator('button[type=submit]')).toBeDisabled();expect(f.state.writes).toHaveLength(0);
});
test('artifact revisions use adopted plan, ordered bindings and exact head without altering Sprint',async({page})=>{
  const f=fixture();await mount(page,f);const name='pm.product_goal.revise';await openAction(page,name);
  await action(page,name).getByLabel('Product Goal',{exact:true}).fill('A clear next direction <script>literal</script>');await reason(page,name);
  await action(page,name).getByRole('button',{name:'Save Product Goal revision',exact:true}).click();
  expect(f.state.writes[0].body.params.plan_revision_id).toBe(planId);expect(f.state.writes[0].body.preconditions.expected_revision_id).toMatch(/^pmgoal_1+$/);
  await expect(page.locator('[data-pm-sprints]')).toContainText('A returning member can sign in');
  await expect(page.locator('[data-pm-sprints] script')).toHaveCount(0);
});
test('history pagination keeps immutable original text without a write',async({page})=>{
  const f=fixture();f.state.more=true;await mount(page,f);await page.getByText('Goal, selection and decision history',{exact:true}).click();
  await page.getByRole('button',{name:'More Goal history',exact:true}).click();await page.getByText('#2 · Goal',{exact:true}).click();
  await expect(page.locator('[data-pm-sprints]')).toContainText('Preserved earlier direction');expect(f.state.writes).toHaveLength(0);
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`Sprint layout ${locale} ${width}`,async({page},info)=>{
  const f=fixture();f.state.sprints[0].status='active';await page.setViewportSize({width,height:900});await mount(page,f,locale);
  await expect(page.locator('[data-pm-sprints] h2')).toHaveText(locale==='en'?'Sprint and iteration':'Sprint 與迭代');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const out=process.env.BATC_PM_SPRINT_SCREENSHOTS||info.outputPath('screenshots');await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/sprint-${locale}-${width}.png`,fullPage:true});
});

test('Product Backlog ordering is explicit and preserves fixed Sprint work',async({page})=>{
  const f=fixture();await mount(page,f);const name='pm.backlog.revise';await openAction(page,name);
  const editor=action(page,name);await editor.getByRole('button',{name:'Earlier',exact:true}).nth(1).click();await reason(page,name);
  await editor.getByRole('button',{name:'Save Product Backlog revision',exact:true}).click();
  expect(f.state.writes[0].body.params.binding_ids).toEqual([f.bindings[1].binding_id,f.bindings[0].binding_id]);
  expect(f.state.sprints[0].selected[0].binding_id).toBe(f.bindings[0].binding_id);
});
test('DoD revisions keep unchanged criterion IDs and never assess quality',async({page})=>{
  const f=fixture();await mount(page,f);const name='pm.dod.revise';await openAction(page,name);
  await action(page,name).getByLabel('Shared quality criteria, one per line',{exact:true}).fill('Relevant tests verify the fixed change\nPassword reset expires as specified');
  await reason(page,name);await action(page,name).getByRole('button',{name:'Save DoD revision',exact:true}).click();
  const request=f.state.writes[0].body;expect(request.params.criteria[0].id).toBe('tests');expect(request.params.criteria[1].id).toMatch(/^criterion_[0-9a-f]{32}$/);
  expect(request.preconditions.expected_revision_id).toMatch(/^pmdod_1+$/);await expect(page.locator('[data-pm-sprints]')).toContainText('DoD is unassessed');
});
test('changed adopted plan cannot silently rebind an unsent Product Goal draft after reload',async({page})=>{
  const f=fixture();await mount(page,f);const name='pm.product_goal.revise';await reason(page,name,'Review the source plan first');
  await action(page,name).getByLabel('Product Goal',{exact:true}).fill('Retain this product direction');
  f.state.planId='pmrev_'+'8'.repeat(32);await page.reload();await boot(page);await openAction(page,name);
  await expect(action(page,name).getByRole('button',{name:'Save Product Goal revision',exact:true})).toBeDisabled();
  await expect(action(page,name).getByLabel('Product Goal',{exact:true})).toHaveValue('Retain this product direction');
  await page.getByRole('button',{name:'Review updated baseline',exact:true}).click();await openAction(page,name);
  await action(page,name).getByRole('button',{name:'Save Product Goal revision',exact:true}).click();
  expect(f.state.writes[0].body.params.plan_revision_id).toBe(f.state.planId);
});
test('product commitment history loads exact revisions without authorizing work',async({page})=>{
  const f=fixture();await mount(page,f);await page.getByText('Product commitments',{exact:true}).click();
  await page.getByText('Product commitment revisions',{exact:true}).click();
  await expect(page.getByText('#1 · owner',{exact:true})).toHaveCount(3);expect(f.state.writes).toHaveLength(0);
});
test('focused unsent selection survives a refresh without losing keyboard focus',async({page})=>{
  const f=fixture();await mount(page,f);await openAction(page,'pm.sprint.select');
  const field=action(page,'pm.sprint.select').getByLabel('Implementation approach for Member login',{exact:true});
  await field.fill('A careful implementation approach');await page.evaluate(()=> (window as any).refreshSprint());
  await expect(field).toHaveValue('A careful implementation approach');await expect(field).toBeFocused();expect(f.state.writes).toHaveLength(0);
});

test('revoked read clears facts and history while retaining the disabled draft for later recovery',async({page})=>{
  const f=fixture();await mount(page,f);await reason(page,'pm.sprint.cancel','Keep this local draft');
  f.state.failRead='revoked';await page.evaluate(()=> (window as any).refreshSprint());
  const text=await page.locator('[data-pm-sprints]').innerText();expect(text).toContain('Workspace access removed');expect(text).not.toContain('A returning member can sign in');
  expect(text).not.toContain('Members can regain account access');await expect(action(page,'pm.sprint.cancel').locator('button[type=submit]')).toBeDisabled();
  f.state.failRead=false;await page.evaluate(()=> (window as any).refreshSprint());await openAction(page,'pm.sprint.cancel');
  await expect(action(page,'pm.sprint.cancel').getByLabel('Reason for this decision',{exact:true})).toHaveValue('Keep this local draft');expect(f.state.writes).toHaveLength(0);
});
test('revocation during lazy history read clears previously visible Sprint facts',async({page})=>{
  const f=fixture();f.state.more=true;await mount(page,f);await page.getByText('Goal, selection and decision history',{exact:true}).click();
  f.state.failRead='revoked';await page.getByRole('button',{name:'More Goal history',exact:true}).click();
  await expect(page.locator('[data-pm-sprints]')).toContainText('Workspace access removed');
  expect(await page.locator('[data-pm-sprints]').innerText()).not.toContain('A returning member can sign in');expect(f.state.writes).toHaveLength(0);
});
for(const locale of ['en','zh-TW'])test(`selection editor layout ${locale}`,async({page},info)=>{
  const f=fixture();await page.setViewportSize({width:390,height:844});await mount(page,f,locale);await openAction(page,'pm.sprint.select');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const out=process.env.BATC_PM_SPRINT_SCREENSHOTS||info.outputPath('screenshots');await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/sprint-editor-${locale}-390.png`,fullPage:true});
});
