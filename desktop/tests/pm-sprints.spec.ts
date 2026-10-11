import {test, expect, type Page} from '@playwright/test';
import {readFile, mkdir} from 'node:fs/promises';
import path from 'node:path';

import {fixture,workspaceId,planId} from './pm-sprint-fixture.ts';
const action=(page:Page,name:string)=>page.locator(`[data-pm-action="${name}"]`);
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

// Model the real bounded backend: the terminal event may not fit in the first
// detail response at all (cursor 0), or may follow fifty ordinary decisions.
function closureFixture({initial=50,total=51,cancelled=false}: {initial?:number,total?:number,cancelled?:boolean}={}) {
  const f=fixture(), sprint=f.state.sprints[0], requests:string[]=[];
  sprint.status=cancelled?'cancelled':'closed';
  const events=Array.from({length:total},(_,index)=>({workspace_id:workspaceId,sprint_id:sprint.sprint_id,sequence:index+1,
    actor:'owner',action:index===total-1?(cancelled?'pm.sprint.cancel':'pm.sprint.close'):'pm.sprint.select',
    document:{rationale:index===total-1?'Keep the unverified recovery work visible <script>literal</script>':`Selection decision ${index+1}`,
      items:index===total-1?[{...sprint.selected[0],quality:'unassessed',counts_as_done:false,
        disposition:'requires_product_backlog_consideration',reason:'Recovery behavior still needs acceptance evidence'}]:[]}}));
  let invalidCursor:any=undefined, wrongRecord=false, hold:Promise<void>|null=null;
  const original=f.dispatch;
  f.dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture');
    if(input.method==='GET'&&url.pathname.endsWith('/history/events')) {
      requests.push(input.path);if(hold)await hold;
      if(f.state.failRead)return original(input);
      const after=Number(url.searchParams.get('after')), rows=events.filter(row=>row.sequence>after).slice(0,50);
      return {status:200,data:{version:1,workspace_id:workspaceId,sprint_id:sprint.sprint_id,kind:'events',
        records:wrongRecord?rows.map(row=>({...row,sprint_id:'pms_'+'9'.repeat(32)})):rows,
        next_cursor:invalidCursor===undefined?(events.some(row=>row.sequence>(rows.at(-1)?.sequence||after))?rows.at(-1)!.sequence:null):invalidCursor}};
    }
    const result=await original(input);
    if(input.method==='GET'&&url.pathname.endsWith('/sprints/'+sprint.sprint_id)&&result.status===200) {
      result.data={...result.data,history:events.slice(0,initial),history_next_cursors:{events:initial<events.length?initial:null},
        coverage:{histories:{events:{returned:initial,has_more:initial<events.length,byte_limited:initial===0}}}};
    }
    return result;
  };
  return {f,requests,events,setInvalidCursor:(value:any)=>{invalidCursor=value;},setWrongRecord:()=>{wrongRecord=true;},setHold:(value:Promise<void>)=>{hold=value;}};
}

test('terminal event after first fifty decisions updates the main closure and full history work disposition',async({page})=>{
  const {f,requests}=closureFixture();await mount(page,f);
  const closure=page.locator('[data-pm-sprint-closure]');
  await expect(closure).toContainText('has not been loaded yet');expect(requests).toHaveLength(0);
  await closure.getByRole('button',{name:'Load next decision history page',exact:true}).click();
  await expect(closure).toContainText('Recovery behavior still needs acceptance evidence');
  await expect(closure).toContainText('Unassessed');await expect(closure).toContainText('Requires Product Backlog consideration');
  await expect(closure.getByRole('button')).toHaveCount(0);expect(requests).toHaveLength(1);expect(requests[0]).toContain('after=50&limit=50');
  await page.getByText('Goal, selection and decision history',{exact:true}).click();
  const history=page.locator('[data-pm-sprint-history="events"]');await history.getByText('#51 · Close elapsed Sprint',{exact:true}).click();
  await expect(history.locator('[data-pm-sprint-ended-work]')).toContainText('Recovery behavior still needs acceptance evidence');
  await expect(history).toContainText('Review and Retrospective are not recorded by closing');
  await expect(page.locator('[data-pm-sprints] script')).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
});

test('byte capped initial history cursor zero is pending and is read explicitly',async({page})=>{
  const {f,requests}=closureFixture({initial:0,total:1,cancelled:true});await mount(page,f);
  const closure=page.locator('[data-pm-sprint-closure]');await expect(closure).toContainText('has not been loaded yet');
  await closure.getByRole('button',{name:'Load next decision history page',exact:true}).click();
  await expect(closure).toContainText('Keep the unverified recovery work visible');expect(requests[0]).toContain('after=0&limit=50');
  await page.getByText('Goal, selection and decision history',{exact:true}).click();
  await page.getByText('#1 · Cancel Sprint',{exact:true}).click();
  await expect(page.locator('[data-pm-sprint-history="events"] [data-pm-sprint-ended-work]')).toContainText('Unassessed');
  expect(f.state.writes).toHaveLength(0);
});

test('history More and closure share each bounded continuation without duplicate records',async({page})=>{
  const {f,requests}=closureFixture({initial:0,total:101});await mount(page,f);
  await page.getByText('Goal, selection and decision history',{exact:true}).click();
  const history=page.locator('[data-pm-sprint-history="events"]'),closure=page.locator('[data-pm-sprint-closure]');
  await history.getByRole('button',{name:'More decision history',exact:true}).click();
  await expect(history.locator(':scope > details')).toHaveCount(50);await expect(closure).toContainText('has not been loaded yet');
  await closure.getByRole('button',{name:'Load next decision history page',exact:true}).click();
  await expect(history.locator(':scope > details')).toHaveCount(100);await expect(closure).toContainText('has not been loaded yet');
  await history.getByRole('button',{name:'More decision history',exact:true}).click();
  await expect(history.locator(':scope > details')).toHaveCount(101);await expect(closure).toContainText('Recovery behavior still needs acceptance evidence');
  expect(requests.map(value=>new URL(value,'http://fixture').searchParams.get('after'))).toEqual(['0','50','100']);expect(f.state.writes).toHaveLength(0);
});

test('exhausted retained history reports unavailable closure instead of inventing a reason',async({page})=>{
  const {f,events}=closureFixture({initial:0,total:1});events[0].action='pm.sprint.select';await mount(page,f);
  const closure=page.locator('[data-pm-sprint-closure]');await closure.getByRole('button',{name:'Load next decision history page',exact:true}).click();
  await expect(closure).toContainText('unavailable in the returned history');await expect(closure.getByRole('button')).toHaveCount(0);
  await expect(closure.locator('[data-pm-sprint-ended-work]')).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
});

for(const mode of ['revoked','wrong-record','noninteger-cursor','no-progress'])test(`closure history read fails closed: ${mode}`,async({page})=>{
  const data=closureFixture(),{f}=data;await mount(page,f);
  if(mode==='revoked')f.state.failRead='revoked';else if(mode==='wrong-record')data.setWrongRecord();
  else data.setInvalidCursor(mode==='no-progress'?50:'51');
  await page.locator('[data-pm-sprint-closure]').getByRole('button',{name:'Load next decision history page',exact:true}).click();
  await expect(page.locator('[data-pm-sprint-closure]')).toHaveCount(0);
  expect(await page.locator('[data-pm-sprints]').innerText()).not.toContain('A returning member can sign in');
  expect(f.state.writes).toHaveLength(0);
});

test('refresh supersedes an in-flight history read and ignores its stale terminal event',async({page})=>{
  const data=closureFixture(),{f,requests}=data;let release!:()=>void;data.setHold(new Promise<void>(resolve=>{release=resolve;}));await mount(page,f);
  const closure=page.locator('[data-pm-sprint-closure]');await closure.getByRole('button',{name:'Load next decision history page',exact:true}).click();
  await expect.poll(()=>requests.length).toBe(1);await expect(closure.getByRole('button',{name:'Loading decision history…',exact:true})).toBeDisabled();
  await page.evaluate(()=> (window as any).refreshSprint());release();
  await expect(closure).toContainText('has not been loaded yet');
  await expect(closure).not.toContainText('Recovery behavior still needs acceptance evidence');expect(f.state.writes).toHaveLength(0);
});

for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`closure history layout ${locale} ${width}`,async({page},info)=>{
  const {f}=closureFixture();await page.setViewportSize({width,height:900});await mount(page,f,locale);
  const closure=page.locator('[data-pm-sprint-closure]');await closure.getByRole('button').click();
  await expect(closure).toContainText(locale==='en'?'Unassessed':'尚未評估');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const out=process.env.BATC_PM_SPRINT_SCREENSHOTS||info.outputPath('screenshots');await mkdir(out,{recursive:true});
  await closure.scrollIntoViewIfNeeded();await page.screenshot({path:`${out}/sprint-closure-${locale}-${width}.png`,fullPage:true});
});
