import {openPmSection} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {reviewFixture,mount,boot,wid} from './pm-maintenance-review-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture,pmRoute} from './pm-fixture.ts';

function fixture(){
  const f=reviewFixture(),event:any=f.addEvent(f.state.reviews[0],'candidate.record',{
    title:'Inspect recovery coverage / 檢視復原涵蓋範圍',problem:'The original fixed report leaves a coverage gap.',
    goal_relation:'Preserve the selected membership release',expected_effect:'Explain the missing evidence',
    options:['Investigate','Defer'],hypotheses:['One narrow check may help'],source_refs:[],
    rationale:'Keep this attributed proposal separate from accepted scope',disposition:'proposal',scope_adopted:false});
  event.document_digest='d'.repeat(64);event.planning_source={kind:'maintenance_review_candidate',review_id:event.review_id,
    event_id:event.event_id,document_digest:event.document_digest,operation_id:event.operation_id};
  return {f,event};
}
const importBox=(page:any)=>page.locator('[data-review-candidate-import]');
async function select(page:any,f:any,locale='en'){
  await importBox(page).locator('summary').first().click();
  await importBox(page).getByLabel(locale==='en'?'Choose an existing Idea discussion':'選擇既有 Idea 討論',{exact:true}).selectOption(f.state.threads[0].thread_id);
}

test('candidate selection freezes an unconfirmed source and original thread version',async({page})=>{
  const {f,event}=fixture();await mount(page,f);expect(f.state.writes).toHaveLength(0);await select(page,f);
  await importBox(page).getByLabel('Topic for this candidate',{exact:true}).selectOption('quality');
  await importBox(page).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body).toEqual({action:'pm.input.record',target:{workspace_id:wid,thread_id:f.state.threads[0].thread_id},
    params:{slot:'quality',value:event.document.title,status:'unresolved',source_ref:event.planning_source},preconditions:{expected_version:2}});
  await expect(importBox(page)).toContainText('No plan was adopted');
  await expect(importBox(page).getByRole('link',{name:'Open selected discussion'}).first()).toHaveAttribute('href',`#/pm/${wid}/${f.state.threads[0].thread_id}`);
});

test('lost receipt retains the original candidate and key after destination enters Planning',async({page})=>{
  const {f}=fixture();await mount(page,f);await select(page,f);f.state.lost=true;
  await importBox(page).locator('button[type=submit]').click();await expect(importBox(page)).toContainText('Reply unavailable');
  const original=structuredClone(f.state.writes[0]);f.state.threads[0].mode='planning';f.state.threads[0].version++;
  await page.reload();await boot(page);await importBox(page).locator('summary').first().click();
  await importBox(page).getByRole('button',{name:'Check original request',exact:true}).click();
  expect(f.state.writes.at(-1)).toEqual(original);expect(f.state.ops.size).toBe(1);
});

test('two candidate receipts and an ordinary input draft keep separate keys for the same Idea',async({page})=>{
  const {f,event}=fixture(),other:any=f.addEvent(f.state.reviews[0],'candidate.record',{...event.document,title:'A separate investigation'});
  other.document_digest='e'.repeat(64);other.planning_source={...event.planning_source,event_id:other.event_id,
    operation_id:other.operation_id,document_digest:other.document_digest};
  await mount(page,f);
  const target={workspace_id:wid,thread_id:f.state.threads[0].thread_id};
  const ordinaryKey=`batc.pm.review-ui.owner.pm.input.record.${JSON.stringify(target)}`;
  const ordinary={version:1,values:{value:'Unrelated ordinary draft',slot:'scope',status:'unresolved',source:'statement',reference:'Owner note'},
    intent:{key:'ordinary-original-key',request:{action:'pm.input.record',target,params:{slot:'scope',value:'Unrelated ordinary draft',
      status:'unresolved',source_ref:{kind:'statement',ref:'Owner note'}},preconditions:{expected_version:2}}}};
  await page.evaluate(({key,value})=>localStorage.setItem(key,JSON.stringify(value)),{key:ordinaryKey,value:ordinary});
  for(const candidate of [event,other]){
    const box=page.locator(`[data-review-candidate-import="${candidate.event_id}"]`);
    await box.locator('summary').first().click();
    await box.getByLabel('Choose an existing Idea discussion',{exact:true}).selectOption(target.thread_id);
    await expect(box.locator('button[type=submit]')).toHaveText('Add to Idea discussion');f.state.lost=true;
    await box.locator('button[type=submit]').click();await expect(box).toContainText('Reply unavailable');
    await expect(box.getByLabel('Choose an existing Idea discussion',{exact:true})).toBeDisabled();
  }
  const originals=structuredClone(f.state.writes);expect(new Set(originals.map((row:any)=>row.idempotency_key)).size).toBe(2);
  expect(originals.map((row:any)=>row.body.params.source_ref.event_id)).toEqual([event.event_id,other.event_id]);
  await page.reload();await boot(page);
  for(const [index,candidate] of [event,other].entries()){
    const box=page.locator(`[data-review-candidate-import="${candidate.event_id}"]`);
    await box.locator('summary').first().click();
    await expect(box.getByLabel('Choose an existing Idea discussion',{exact:true})).toBeDisabled();
    await box.getByRole('button',{name:'Check original request',exact:true}).click();
    expect(f.state.writes.at(-1)).toEqual(originals[index]);
  }
  expect(f.state.ops.size).toBe(2);
  expect(await page.evaluate(key=>JSON.parse(localStorage.getItem(key)!),ordinaryKey)).toEqual(ordinary);
});

test('a saved draft requires explicit current discussion version review',async({page})=>{
  const {f}=fixture();await mount(page,f);await select(page,f);await importBox(page).getByLabel('Topic for this candidate',{exact:true}).selectOption('risk');
  f.state.threads[0].version++;await importBox(page).getByRole('button',{name:'Refresh Idea discussions',exact:true}).click();
  await expect(importBox(page)).toContainText('The discussion changed');await expect(importBox(page).locator('button[type=submit]')).toBeDisabled();
  await importBox(page).getByRole('button',{name:'Use this discussion version',exact:true}).click();await expect(importBox(page).locator('button[type=submit]')).toBeEnabled();
  expect(f.state.writes).toHaveLength(0);
});

test('empty Idea choice and source withdrawal do not create or copy a discussion',async({page})=>{
  const {f}=fixture();f.state.threads=[];await mount(page,f);await importBox(page).locator('summary').first().click();
  await expect(importBox(page)).toContainText('Create an Idea discussion');expect(f.state.writes).toHaveLength(0);
  f.state.failRead=true;await page.evaluate(()=>(window as any).refreshReviews());await expect(importBox(page)).toHaveCount(0);
  await expect(page.locator('[data-review-discussion]')).toHaveCount(0);
});

for(const native of [false,true])test(`${native?'native':'web'} discussion keeps full original candidate before confirmation`,async({page})=>{
  const {event}=fixture(),f=pmFixture();f.state.inputs=[{...f.state.inputs[1],status:'unresolved',value:event.document.title,
    source_ref:event.planning_source,source_candidate:event}];await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);
  const proof=page.locator('[data-review-candidate-proof]');await proof.locator('summary').first().click();
  await expect(proof).toContainText(event.document.problem);await expect(proof).toContainText(event.document.rationale);
  await expect(proof).toContainText('candidate.record · proposal');expect(f.state.writes).toHaveLength(0);
  await expect(page.locator('.pm-facts')).toContainText('Unresolved');
});

for(const locale of ['en','zh-TW'])for(const width of [1440,768,390])test(`review candidate layout ${locale} ${width}`,async({page},info)=>{
  const {f}=fixture();await page.setViewportSize({width,height:900});await mount(page,f,locale);await select(page,f,locale);
  await expect(importBox(page).locator('button[type=submit]')).toBeEnabled();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const output=process.env.BATC_PM_REVIEW_CANDIDATE_SCREENSHOTS||info.outputPath('screenshots');await mkdir(output,{recursive:true});
  await importBox(page).scrollIntoViewIfNeeded();await page.screenshot({path:`${output}/candidate-${locale}-${width}.png`,fullPage:true});
});
