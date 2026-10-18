import {mkdirSync} from 'node:fs';
import {test, expect} from '@playwright/test';
import {reviewFixture} from './requirements-review-fixture.ts';

for (const locale of ['en', 'zh-TW']) for (const width of [1440, 390]) {
  test(`Overview assessment preserves factual reading ${locale} ${width}`, async ({browser, baseURL}) => {
    const context = await browser.newContext({baseURL, locale, viewport: {width, height: width === 390 ? 844 : 900}});
    const page = await context.newPage(), f = reviewFixture(locale);
    try {
      await f.mount(page, 'overview');
      await expect(page.locator('[data-overview-content] [data-charter-section="definition"]')).toContainText(f.copy.goal);
      expect(f.writes).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      if (process.env.BATC_ASSESSMENT_BASELINE_SHOTS) {
        const dir = process.env.BATC_ASSESSMENT_BASELINE_SHOTS;
        mkdirSync(dir, {recursive: true});
        await page.screenshot({path: `${dir}/before-${locale}-${width}.png`, animations: 'disabled'});
      }
    } finally {await context.close();}
  });
}

import {mountConversation} from './conversation-fixture.ts';
const assessAction='pm.overview.assess', assessmentId='pmoa_'+'7'.repeat(32), opId='op_'+'8'.repeat(32);
function assessmentFixture(locale='en') {
  const f=reviewFixture(locale), wid=f.ids.workspaceId, tid=f.ids.threadIdea, root=`/pm/workspaces/${wid}`;
  const limits={allowed:true,retry_at:null,turns:1};
  const data:any={version:1,workspace_id:wid,freshness:'current',context:{schema:'pm_overview_bundle_v1',workspace_id:wid,thread_id:tid,
    projection_digest:'a'.repeat(64),source_references:{projection:{category:'projection',label:'Overview'},decision:{category:'decision',label:'Keep account recovery in scope',
      ref:{kind:'plan_revision',id:f.ids.planV3}}}},assessment:{assessment_id:assessmentId,workspace_id:wid,thread_id:tid,output_version:1,
    operation_id:opId,as_of:1792320000,created_at:1792320000,projection_digest:'a'.repeat(64),validation:{status:'format_valid'},
    document:{version:1,judgment:{key:'j',template:'decision_first',subject_ref:'decision',basis_refs:['decision']},recommendations:[],
      needs_decision:[{key:'d.a',template:'confirm_direction',subject_ref:'decision',basis_refs:['decision']}],can_be_handled:[],watch:[],limitations:[]}}};
  const state={withdrawn:false,lost:false,empty:false,partial:false,readOnly:false,version:1,newer:false,needsReview:false,noAgent:false,unsupported:false,
    holdDetail:null as string|null,releaseDetail:null as null|(()=>void),writes:[] as any[],previews:0,operation:null as any,create:null as any};
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),path=url.pathname;
    if(input.method==='POST'&&[assessAction,'pm.thread.create'].includes(input.body?.action)) {
      state.writes.push(structuredClone(input));
      if(input.body.action==='pm.thread.create') {
        state.create??={...input.body,operation_id:'op_'+'9'.repeat(32),actor:f.ids.actor,idempotency_key:input.idempotency_key,status:'succeeded',result:{thread_id:tid}};
        return {status:200,data:{operation:state.create}};
      }
      state.operation??={...input.body,operation_id:opId,actor:f.ids.actor,idempotency_key:input.idempotency_key,status:'succeeded',result:{outcome:{assessment_id:assessmentId}}};
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      state.empty=false;return {status:200,data:{operation:state.operation}};
    }
    if(path==='/operations/'+opId&&state.operation)return {status:200,data:{operation:state.operation}};
    if(path==='/operations/op_'+'9'.repeat(32)&&state.create)return {status:200,data:{operation:state.create}};
    if(path===root+'/overview-assessments') {
      const filtered=state.partial&&!url.searchParams.has('before');
      const rows=state.empty||filtered?[]:[{...data.assessment,freshness:data.freshness}];
      if(state.newer)rows.unshift({...rows[0],assessment_id:'pmoa_'+'6'.repeat(32),thread_id:f.ids.threadPlanning});
      return {status:200,data:{version:1,workspace_id:wid,order:'newest_first',assessments:rows,partial:filtered,next_cursor:filtered?'original_cursor':null,source_filtered:filtered}};
    }
    if(path.startsWith(root+'/overview-assessments/')) {
      if(state.holdDetail===path.split('/').at(-1))await new Promise<void>(resolve=>{state.releaseDetail=resolve;});
      if(state.withdrawn)return {status:404,data:{error:{code:'PM_OVERVIEW_SOURCE_UNAVAILABLE',message:'Sources withdrawn'}}};
      const copy=structuredClone(data);copy.assessment.assessment_id=path.split('/').at(-1);
      if(state.needsReview){copy.assessment.document=null;copy.assessment.validation={status:'needs_review'};copy.assessment.raw_reply='<script>untrusted reply</script>';}
      return {status:200,data:copy};
    }
    if(path.endsWith('/context-bundle')) {
      state.previews++;
      return {status:200,data:{schema:'pm_overview_bundle_v1',workspace_id:wid,thread_id:tid,context:{thread_version:state.version},
        selector:{schema:'pm_overview_bundle_v1',projection_digest:data.assessment.projection_digest},limits,calls_provider:false}};
    }
    const result=await f.dispatch(input);
    if(path==='/capabilities'||path==='/bootstrap') {
      const caps=path==='/bootstrap'?result.data.capabilities:result.data;
      caps.features.pm_workspaces.overview_assessment=true;
      caps.scopes=state.readOnly?['observe']:['observe','manage','start'];caps.actions.push({action:assessAction,allowed:!state.readOnly});
    }
    if(path===root+'/capabilities') {
      result.data.overview_assessment=state.unsupported?undefined:{version:1,authorized:!state.readOnly,eligible_for_preflight:!state.noAgent,limits};
      result.data.structured_analysis={version:1,actions:{[assessAction]:{adapters:[
        {adapter:'codex-native-linux',action_registered:true,authorized:!state.readOnly,eligible_for_preflight:true},
        {adapter:'bat-pm-linux-v1',action_registered:true,authorized:!state.readOnly,eligible_for_preflight:true}]}}};
    }
    if(path===root+'/threads/'+tid){result.data.thread.version=state.version;result.data.capabilities.write=!state.readOnly;}
    return result;
  };
  return {f,data,state,limits,dispatch,async mount(page:any,native=false){await page.addInitScript(value=>Object.defineProperty(navigator,'language',{value}),locale);
    await mountConversation(page,native,dispatch);await page.goto(`/dashboard/#/pm/${wid}/overview`);}};
}

for(const locale of ['en','zh-TW'])for(const width of [1440,768,390])test(`explicit assessment preserves facts and fixed citations ${locale} ${width}`,async({page})=>{
  const x=assessmentFixture(locale);await page.setViewportSize({width,height:900});await x.mount(page,width===768);
  const card=page.locator('[data-overview-assessment]');await expect(card).toContainText('Keep account recovery in scope');
  await expect(page.locator('[data-overview-metric=goal]')).toContainText(x.f.copy.goal);
  expect(x.state.writes).toEqual([]);expect(x.state.previews).toBe(0);
  await page.getByRole('button',{name:locale==='en'?'Refresh':'重新整理',exact:true}).click();
  await expect(card).toContainText('Keep account recovery in scope');expect(x.state.writes).toEqual([]);
  await card.locator('[data-overview-assessment-prepare]').click();
  const form=card.locator('[data-pm-action="pm.overview.assess"]');await expect(form.locator('button[type=submit]')).toBeEnabled();
  await form.getByLabel(locale==='en'?'Run analysis with':'分析執行方式',{exact:true}).selectOption('bat-pm-linux-v1');
  if(process.env.BATC_ASSESSMENT_SHOTS){mkdirSync(process.env.BATC_ASSESSMENT_SHOTS,{recursive:true});await page.screenshot({path:`${process.env.BATC_ASSESSMENT_SHOTS}/after-${locale}-${width}.png`,animations:'disabled'});}
  await form.locator('button[type=submit]').click();await expect.poll(()=>x.state.writes.length).toBe(1);
  expect(x.state.writes[0].body).toEqual({action:assessAction,target:{workspace_id:x.f.ids.workspaceId,thread_id:x.f.ids.threadIdea},
    params:{adapter:'bat-pm-linux-v1',context_bundle_ref:{schema:'pm_overview_bundle_v1',projection_digest:'a'.repeat(64)}},preconditions:{expected_version:1}});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('lost acknowledgement retains original carrier selector adapter and key across reload',async({page})=>{
  const x=assessmentFixture();x.state.lost=true;await x.mount(page);
  await page.locator('[data-overview-assessment-prepare]').click();
  const form=page.locator('[data-pm-action="pm.overview.assess"]');
  await form.getByLabel('Run analysis with',{exact:true}).selectOption('bat-pm-linux-v1');await form.locator('button[type=submit]').click();
  await expect(form).toContainText('Reply unavailable');const original=structuredClone(x.state.writes[0]);
  x.data.assessment.projection_digest='b'.repeat(64);x.data.context.projection_digest='b'.repeat(64);x.state.version=2;x.state.newer=true;
  await page.reload();await page.locator('[data-overview-assessment-prepare]').click();
  await expect(form.getByLabel('Run analysis with',{exact:true})).toHaveValue('bat-pm-linux-v1');
  await form.locator('button[type=submit]').click();await expect.poll(()=>x.state.writes.length).toBe(2);
  expect(x.state.writes[1]).toEqual(original);expect(x.state.previews).toBe(1);
});

test('source withdrawal clears assessment and history without hiding factual overview',async({page})=>{
  const x=assessmentFixture();await x.mount(page);
  const card=page.locator('[data-overview-assessment]');await expect(card).toContainText('Keep account recovery in scope');
  await card.getByRole('button',{name:'Assessment history',exact:true}).click();
  await page.locator(`[data-overview-history-id="${assessmentId}"]`).click();await expect(page.locator('[data-overview-history-detail]')).toContainText('Keep account recovery in scope');
  x.data.freshness='out_of_date';x.f.emit();
  await expect(page.locator('[data-overview-history-freshness]')).toHaveText('Out of date');
  x.state.withdrawn=true;x.f.emit();
  await expect(card).not.toContainText('Keep account recovery in scope');await expect(page.locator('[data-overview-assessment-history]')).toHaveCount(0);
  await expect(page.locator('[data-overview-metric=goal]')).toContainText(x.f.copy.goal);expect(x.state.writes).toEqual([]);
});

test('partial empty history follows central cursor before reusing carrier',async({page})=>{
  const x=assessmentFixture();x.state.partial=true;await x.mount(page);
  await page.locator('[data-overview-assessment-prepare]').click();
  await expect(page.locator('[data-pm-action="pm.overview.assess"]')).toBeVisible();
  expect(x.f.reads.some(path=>path.includes('/threads/'+x.f.ids.threadIdea))).toBe(true);expect(x.state.writes).toEqual([]);
});

test('first assessment prepares exactly one contextual discussion before explicit model launch',async({page})=>{
  const x=assessmentFixture();x.state.empty=true;await x.mount(page);
  await page.locator('[data-overview-assessment-prepare]').click();
  const create=page.locator('[data-overview-assessment] [data-pm-action="pm.thread.create"]');
  await create.locator('button[type=submit]').click();await expect.poll(()=>x.state.writes.length).toBe(1);
  expect(x.state.writes[0].body.params.title).toBe('PM assessment');
  expect(x.state.writes[0].body.params.context).toEqual([{kind:'plan_revision',id:x.f.ids.planV3}]);
  const form=page.locator('[data-pm-action="pm.overview.assess"]');await expect(form).toBeVisible();expect(x.state.writes.length).toBe(1);
  await page.reload();await page.locator('[data-overview-assessment-prepare]').click();await expect(form).toBeVisible();expect(x.state.writes.length).toBe(1);
  await form.locator('button[type=submit]').click();await expect.poll(()=>x.state.writes.length).toBe(2);expect(x.state.writes[1].body.action).toBe(assessAction);
});

test('invalid result remains inspectable without rendering model prose as advice',async({page})=>{
  const x=assessmentFixture();x.state.needsReview=true;await x.mount(page);
  const card=page.locator('[data-overview-assessment]');await expect(card).toContainText('This reply needs review');
  expect(await card.locator('[data-overview-assessment-claim]').count()).toBe(0);
  await card.locator('[data-overview-assessment-details]').click();
  const history=page.locator('[data-overview-assessment-history]');
  await history.getByText('Original reply',{exact:true}).click();await expect(history.locator('pre')).toHaveText('<script>untrusted reply</script>');
  expect(await card.locator('script').count()).toBe(0);expect(x.state.writes).toEqual([]);
});

test('stale assessment preserves frozen text and read-only member cannot launch',async({page})=>{
  const x=assessmentFixture();x.data.freshness='out_of_date';x.state.readOnly=true;await x.mount(page);
  const card=page.locator('[data-overview-assessment]');await expect(card).toContainText('Out of date');
  await expect(card).toContainText('Keep account recovery in scope');await expect(card.locator('[data-overview-assessment-prepare]')).toBeDisabled();
  expect(x.state.writes).toEqual([]);
});

test('background evidence update preserves draft focus and requires explicit selection of newer assessment',async({page})=>{
  const x=assessmentFixture();await x.mount(page);
  await page.locator('[data-overview-assessment-prepare]').click();
  const form=page.locator('[data-pm-action="pm.overview.assess"]');await form.getByText('Model preference (optional)',{exact:true}).click();
  const model=form.getByLabel('Model (optional)',{exact:true});await model.fill('keep-my-profile');
  x.state.newer=true;x.f.emit();
  await expect(page.locator('[data-overview-assessment-latest]')).toBeVisible();
  await expect(model).toBeFocused();await expect(model).toHaveValue('keep-my-profile');
  await expect(page.locator('[data-overview-assessment-content] [data-overview-assessment-id]')).toHaveAttribute('data-overview-assessment-id',assessmentId);
  expect(x.state.writes).toEqual([]);expect(x.state.previews).toBe(1);
});

test('central admission limit disables fresh model work without removing the previous assessment',async({page})=>{
  const x=assessmentFixture();x.limits.allowed=false;x.limits.retry_at=1792320123 as any;await x.mount(page);
  await expect(page.locator('[data-overview-assessment-availability]')).toContainText('Assessment is available again after');
  await expect(page.locator('[data-overview-assessment-prepare]')).toBeDisabled();
  await expect(page.locator('[data-overview-assessment-content]')).toContainText('Keep account recovery in scope');expect(x.state.writes).toEqual([]);
});

for(const mode of ['unconfigured','unsupported'])test(`assessment handles ${mode} central without model work`,async({page})=>{
  const x=assessmentFixture();x.state.empty=true;x.state.noAgent=mode==='unconfigured';x.state.unsupported=mode==='unsupported';await x.mount(page);
  await expect(page.locator('[data-overview-metric=goal]')).toContainText(x.f.copy.goal);
  const card=page.locator('[data-overview-assessment]');
  if(mode==='unsupported')await expect(card).toBeHidden();
  else {await expect(card).toContainText('PM agent is not connected.');await expect(card.locator('[data-overview-assessment-prepare]')).toBeDisabled();
    await expect(card.getByRole('link',{name:'Set up PM'})).toHaveAttribute('href',`#/settings?section=agents&from=${encodeURIComponent(`#/pm/${x.f.ids.workspaceId}/overview`)}`);}
  expect(x.state.writes).toEqual([]);
});


test('late history selection cannot replace the subsequently selected original assessment',async({page})=>{
  const x=assessmentFixture();x.state.newer=true;await x.mount(page);const card=page.locator('[data-overview-assessment]');
  await expect(card).toContainText('Keep account recovery in scope');await card.getByRole('button',{name:'Assessment history',exact:true}).click();
  x.state.holdDetail=assessmentId;
  await page.locator(`[data-overview-history-id="${assessmentId}"]`).click();await expect.poll(()=>Boolean(x.state.releaseDetail)).toBe(true);
  const other='pmoa_'+'6'.repeat(32);await page.locator(`[data-overview-history-id="${other}"]`).click();
  await expect(page.locator('[data-overview-history-detail] [data-overview-assessment-id]')).toHaveAttribute('data-overview-assessment-id',other);
  x.state.holdDetail=null;x.state.releaseDetail!();
  await page.getByRole('complementary').last().getByText('Original evidence',{exact:true}).first().click();
  await expect(page.locator('[data-overview-history-detail] [data-overview-assessment-id]')).toHaveAttribute('data-overview-assessment-id',other);expect(x.state.writes).toEqual([]);
});

test('compact card keeps extra recommendations and watch items in exact Details',async({page})=>{
  const x=assessmentFixture(),doc=x.data.assessment.document;
  doc.recommendations=[{key:'r.a',template:'prepare_options',subject_ref:'decision',basis_refs:['decision']},
    {key:'r.b',template:'review_evidence',subject_ref:'decision',basis_refs:['decision']}];
  x.data.context.source_references.risk={category:'risk',label:'A recorded risk',ref:{kind:'plan_revision',id:x.f.ids.planV3}};
  doc.watch=[{key:'w.a',template:'watch_recorded_risk',subject_ref:'risk',basis_refs:['risk']}];
  await x.mount(page);const card=page.locator('[data-overview-assessment]');
  await expect(card.locator('.pm-assessment-summary-row')).toHaveCount(4);await expect(card).not.toContainText('A recorded risk');
  await card.locator('[data-overview-assessment-details]').click();const detail=page.locator('[data-overview-history-detail]');
  await expect(detail).toContainText('A recorded risk');await expect(detail.locator('[data-overview-assessment-claim="r.b"]')).toBeVisible();expect(x.state.writes).toEqual([]);
});
