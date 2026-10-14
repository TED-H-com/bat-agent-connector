import {openPmSection} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workspaceId} from './pm-fixture.ts';
import {fixture,rid,bid,path} from './pm-roadmap-fixture.ts';

const wid='wi_'+'1'.repeat(20),pid='proj_'+'1'.repeat(20),oldBinding='pmwb_'+'1'.repeat(32),newBinding='pmwb_'+'2'.repeat(32);
const endpoint=`/pm/workspaces/${workspaceId}`;
const stamp=Date.parse('2026-10-11T12:00:00Z')/1000;
const binding=(id=oldBinding)=>({binding_id:id,work_item_id:wid,project_id:pid,plan_revision_id:rid,requirement_ids:['login']});
const baselineForm=(page:any)=>page.locator('[data-pm-action="pm.roadmap.baseline.set"]');
async function openEditor(page:any){await page.locator('[data-pm-roadmap-editor] > summary').click();return baselineForm(page);}
async function refresh(page:any){await page.getByRole('button',{name:'Refresh',exact:true}).click();}

function historyFixture(){
  const f=fixture();
  const rows:any[]=[{revision_id:bid,workspace_id:workspaceId,kind:'baseline',sequence:1,created_at:stamp,
    baseline_revision_id:bid,document:structuredClone(f.state.baseline.document)}];
  const state={rows,details:[] as string[],pages:[] as number[],hideSource:false,failMore:false};
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),route=url.pathname;
    if(route===endpoint+'/roadmap/revisions'){
      const after=Number(url.searchParams.get('after')||0),limit=Number(url.searchParams.get('limit')||20);state.pages.push(after);
      if(after&&state.failMore){state.failMore=false;return {status:503,data:{error:{code:'UNAVAILABLE',message:'History page unavailable'}}};}
      const eligible=rows.filter(row=>row.sequence>after),selected=eligible.slice(0,limit);
      return {status:200,data:{workspace_id:workspaceId,revisions:selected.map(({document,...row})=>row),
        next_cursor:eligible.length>limit?selected.at(-1).sequence:null}};
    }
    if(route.startsWith(endpoint+'/roadmap/revisions/')){
      const id=route.split('/').at(-1)!;state.details.push(id);const revision=structuredClone(rows.find(row=>row.revision_id===id));
      if(state.hideSource&&revision.kind==='observation')revision.document={...revision.document,milestones:[],coverage:{inaccessible_sources:true}};
      return {status:200,data:{workspace_id:workspaceId,revision}};
    }
    const oldCount=f.state.ops.size,response=await f.dispatch(input);
    if(input.method==='POST'&&route==='/operations'&&input.body.action==='pm.roadmap.forecast.set'&&f.state.ops.size>oldCount){
      const revision_id='pmrf_'+String(rows.length+1).padStart(32,'0');
      rows.push({revision_id,workspace_id:workspaceId,kind:'forecast',sequence:rows.length+1,created_at:stamp+rows.length,
        baseline_revision_id:bid,document:{...structuredClone(f.state.forecast.document),rationale:input.body.params.rationale}});
      f.state.forecast.revision_id=revision_id;
    }
    return response;
  };
  return {...f,history:state,dispatch};
}

for(const native of [false,true])test.describe(native?'native Roadmap recovery and history':'web Roadmap recovery and history',()=>{
  test('newly saved forecast appears in already opened history with its fixed source',async({page})=>{
    const f=historyFixture();await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');
    const history=page.locator('[data-pm-roadmap-history]');await history.locator(':scope > summary').click();
    await expect(history.locator('details')).toHaveCount(1);
    await page.getByText('Update a forecast',{exact:true}).click();const form=page.locator('[data-pm-action="pm.roadmap.forecast.set"]');
    await form.getByLabel('Milestone',{exact:true}).selectOption('first_release');await form.getByLabel('Forecast date',{exact:true}).fill('2026-12-01');
    await form.getByLabel('Reason for this change',{exact:true}).fill('Known dependency changed our forecast');
    await form.getByRole('button',{name:'Save forecast',exact:true}).click();await expect(history.locator('details')).toHaveCount(2);
    const saved=history.locator('details').filter({has:page.locator('summary',{hasText:'Forecast'})});await saved.locator('summary').click();
    await expect(saved).toContainText('Known dependency changed our forecast');await expect(saved).toContainText(bid);
    await expect(saved).toContainText('2026-12-01');expect(f.history.details).toEqual([f.history.rows[1].revision_id]);
  });

  test('explicit current-baseline reset adopts the current title and current CAS together',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');const form=await openEditor(page);
    await form.getByLabel('Roadmap name',{exact:true}).fill('Old local name');await form.getByLabel('Milestone',{exact:true}).fill('Old local work');
    f.state.version=4;f.state.baseline.document.title='A different owner renamed this';f.state.baseline.document.milestones[0].title='Current agreed work';
    await refresh(page);await expect(page.locator('[data-pm-milestone]')).toContainText('Current agreed work');
    // Background refresh alone must preserve this unfinished local draft.
    await expect(form.getByLabel('Roadmap name',{exact:true})).toHaveValue('Old local name');
    await page.locator('[data-pm-roadmap-editor]').getByRole('button',{name:'Use the current baseline',exact:true}).click();
    await expect(form.getByLabel('Roadmap name',{exact:true})).toHaveValue('A different owner renamed this');
    await expect(form.getByLabel('Milestone',{exact:true})).toHaveValue('Current agreed work');
    await form.getByLabel('Reason for this change',{exact:true}).fill('Use the currently agreed baseline');
    await form.getByRole('button',{name:'Adopt scheduling baseline',exact:true}).click();await expect(form).toContainText('Saved centrally.');
    expect(f.state.writes[0].body.params.title).toBe('A different owner renamed this');
    expect(f.state.writes[0].body.preconditions.expected_roadmap_version).toBe(4);
  });

  test('lost baseline response restores a visible read-only original draft and original key',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');let form=await openEditor(page);
    await form.getByLabel('Milestone',{exact:true}).fill('The submitted original work');
    await form.getByLabel('Reason for this change',{exact:true}).fill('Freeze this original request');f.state.lost=true;
    await form.getByRole('button',{name:'Adopt scheduling baseline',exact:true}).click();await expect(form).toContainText('Reply unavailable');
    const original=structuredClone(f.state.writes[0]);f.state.version=7;f.state.baseline.document.milestones[0].title='Later unrelated baseline';
    await page.reload();await openPmSection(page, 'schedule');form=await openEditor(page);
    await expect(form.getByLabel('Milestone',{exact:true})).toHaveValue('The submitted original work');
    await expect(form.getByLabel('Milestone',{exact:true})).toBeDisabled();
    await expect(page.locator('[data-pm-milestone]')).toContainText('Later unrelated baseline');
    await form.getByRole('button',{name:'Check original request',exact:true}).click();await expect(form).toContainText('Saved centrally.');
    expect(f.state.writes[1].body).toEqual(original.body);expect(f.state.writes[1].idempotency_key).toBe(original.idempotency_key);
    expect(f.state.ops.size).toBe(1);
  });

  test('incompatible scoped work stays disabled until matching requirements are explicitly chosen',async({page})=>{
    const f=fixture();f.state.bindings=[binding()];f.state.baseline.document.milestones[0].requirement_ids=[];
    await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');const form=await openEditor(page);
    await form.getByText('Associated work',{exact:true}).click();const work=form.locator(`[data-pm-work-binding="${oldBinding}"]`);
    await expect(work).toBeDisabled();await form.getByText('Linked requirements',{exact:true}).click();
    await form.getByRole('checkbox',{name:'Members can sign in',exact:true}).check();await expect(work).toBeEnabled();await work.check();
    await form.getByRole('checkbox',{name:'Members can sign in',exact:true}).uncheck();
    await expect(work).toBeEnabled();await work.uncheck();await expect(work).toBeDisabled();
    await form.getByRole('checkbox',{name:'Members can sign in',exact:true}).check();await work.check();
    await form.getByLabel('Reason for this change',{exact:true}).fill('Explicitly select matching work and requirements');
    await form.getByRole('button',{name:'Adopt scheduling baseline',exact:true}).click();await expect(form).toContainText('Saved centrally.');
    expect(f.state.writes[0].body.params.milestones[0]).toMatchObject({requirement_ids:['login'],work_binding_ids:[oldBinding]});
  });

  test('work rebinding during an unfinished draft preserves the rendered fixed association safely',async({page})=>{
    const f=fixture(),errors:string[]=[];f.state.bindings=[binding()];f.state.baseline.document.milestones[0].work_binding_ids=[oldBinding];
    page.on('pageerror',error=>errors.push(error.message));await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');const form=await openEditor(page);
    await form.getByText('Associated work',{exact:true}).click();await form.getByText('Linked requirements',{exact:true}).click();
    f.state.bindings=[binding(newBinding)];await refresh(page);
    const selected=form.getByRole('checkbox',{name:'Members can sign in',exact:true});await selected.uncheck();await selected.check();
    await expect(form.locator(`[data-pm-work-binding="${oldBinding}"]`)).toBeChecked();
    await form.getByLabel('Reason for this change',{exact:true}).fill('Retain the original work association');
    await form.getByRole('button',{name:'Adopt scheduling baseline',exact:true}).click();await expect(form).toContainText('Saved centrally.');
    expect(errors).toEqual([]);expect(f.state.writes[0].body.params.milestones[0].work_binding_ids).toEqual([oldBinding]);
  });

  test('historical work can be unchecked and rechecked without visually hiding a scope removal',async({page})=>{
    const f=fixture();f.state.baseline.document.milestones[0].work_binding_ids=[oldBinding];f.state.bindings=[binding(newBinding)];
    await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');const form=await openEditor(page);
    await form.getByText('Associated work',{exact:true}).click();const historical=form.getByRole('checkbox',{name:/Previously selected work association/});
    await historical.uncheck();await historical.check();
    await form.getByLabel('Reason for this change',{exact:true}).fill('Keep the exact earlier association');
    await form.getByRole('button',{name:'Adopt scheduling baseline',exact:true}).click();await expect(form).toContainText('Saved centrally.');
    expect(f.state.writes[0].body.params.milestones[0].work_binding_ids).toEqual([oldBinding]);
  });

  test('explicit refresh rechecks historical source visibility even when roadmap version is unchanged',async({page})=>{
    const f=historyFixture(),observation='pmro_'+'3'.repeat(32);f.history.rows.push({revision_id:observation,workspace_id:workspaceId,
      sequence:2,kind:'observation',created_at:stamp+1,baseline_revision_id:bid,
      document:{rationale:'Saved observation',milestones:[{milestone_id:'first_release',actual:{facts:[{source_id:'PREVIOUSLY_VISIBLE_SOURCE'}]}}]}});
    await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');const history=page.locator('[data-pm-roadmap-history]');
    await history.locator(':scope > summary').click();let detail=history.locator('details').filter({has:page.locator('summary',{hasText:'Saved result observation'})});
    await detail.locator('summary').click();await expect(detail).toContainText('PREVIOUSLY_VISIBLE_SOURCE');
    f.history.hideSource=true;await refresh(page);await expect(history).not.toContainText('PREVIOUSLY_VISIBLE_SOURCE');
    detail=history.locator('details').filter({has:page.locator('summary',{hasText:'Saved result observation'})});
    await detail.locator('summary').click();await expect(detail).toContainText('inaccessible_sources');await expect(detail).not.toContainText('PREVIOUSLY_VISIBLE_SOURCE');
    expect(f.history.details.filter(id=>id===observation)).toHaveLength(2);
  });

  test('failed history pagination retains its exact retry cursor',async({page})=>{
    const f=historyFixture();for(let index=2;index<=21;index++)f.history.rows.push({revision_id:'pmrf_'+String(index).padStart(32,'0'),
      sequence:index,workspace_id:workspaceId,kind:'forecast',created_at:stamp+index,baseline_revision_id:bid,document:{milestones:[]}});
    await mountConversation(page,native,f.dispatch);await page.goto(path);await openPmSection(page, 'schedule');const history=page.locator('[data-pm-roadmap-history]');
    await history.locator(':scope > summary').click();await expect(history.locator('details')).toHaveCount(20);
    const more=history.getByRole('button');f.history.failMore=true;await more.click();await expect(history).toContainText('History page unavailable');
    await expect(more).toBeEnabled();await more.click();await expect(history.locator('details')).toHaveCount(21);
    expect(f.history.pages).toEqual([0,20,20]);
  });
});
