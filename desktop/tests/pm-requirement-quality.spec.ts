import {test,expect,type Page} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {mkdir} from 'node:fs/promises';
import {batchFixture,mountBatch,panel,start,proof,handoff,workspaceId} from './pm-work-batches-fixture.ts';

const PROFILE='accepted_requirements_v1';
function fixture(){
  const f=batchFixture(),pkg=f.base.row,plan={revision_id:'pmrev_'+'1'.repeat(32),document_digest:'d'.repeat(64),scope_revision:'pmscope_'+'2'.repeat(32)};
  pkg.document.context.plan_ref=plan;
  pkg.document.requirements=[{id:'recovery',title:'Original recovery',depends_on:[],acceptance:[{source_input_id:'in_b',text:'Preserve owner'},{source_input_id:'in_a',text:'Restore original receipt'}]}];
  const condition={kind:'requirement_acceptance',profile:PROFILE,package_ref:{workspace_id:workspaceId,package_id:pkg.package_id,document_digest:pkg.document_digest},plan_ref:plan,requirements:[{requirement_id:'recovery',acceptance_input_ids:['in_a','in_b']}]};
  const review:any={workspace_id:workspaceId,review_id:'pmqr_'+'3'.repeat(32),qualified:true,applicable:true,context:{package_ref:condition.package_ref,plan_ref:plan,execution_operation_id:'op_'+'c'.repeat(32),native_result:{candidate_commit:'a'.repeat(40)},reviewer_actor:'responsible-reviewer'},assessment:{operation_id:'op_'+'4'.repeat(32),version:2,document_digest:'e'.repeat(64),document:{qualified:true,criteria:pkg.document.requirements[0].acceptance.map(item=>({criterion:{kind:'acceptance',requirement_id:'recovery',input_id:item.source_input_id},status:'accepted'}))}}};
  const reviews=[review],state={deny:false,partial:false,badCursor:false,capability:true};
  const ref=()=>({review_id:review.review_id,operation_id:review.assessment.operation_id,version:review.assessment.version,document_digest:review.assessment.document_digest,candidate_commit:review.context.native_result.candidate_commit});
  const dispatch=f.dispatch;f.dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture');
    if(url.pathname.endsWith('/quality-reviews')){f.state.reads.push(input.path);if(state.deny)return {status:404,data:{error:{code:'NOT_FOUND',message:'Original quality sources withdrawn'}}};
      const partial=state.partial&&url.searchParams.get('after')==='0';return {status:200,data:{workspace_id:workspaceId,reviews:partial?[]:structuredClone(reviews),visibility_filtered:partial,next_cursor:state.badCursor?0:partial?5:null}};}
    if(url.pathname.endsWith('/readiness')){
      const out=await dispatch(input),row=f.base.fixture.state.packages.find(row=>url.pathname.includes(row.package_id));
      if(row.document.requirements.some(req=>req.depends_on.length)){
        const refs=JSON.parse(url.searchParams.get('quality_refs')||'{}');out.data.checks=out.data.checks.filter(check=>!check.check.startsWith('dependency:'));
        out.data.checks.push({check:'dependency:downstream',requirement_ids:['recovery'],status:JSON.stringify(refs.recovery?.reference)===JSON.stringify(ref())?'passed':'blocked'});
      }return out;
    }
    const result=await dispatch(input);
    if(['/capabilities','/bootstrap'].includes(url.pathname)){const caps=url.pathname==='/bootstrap'?result.data.capabilities:result.data;caps.features.pm_workspaces.work_batches.profiles.push(PROFILE);caps.features.pm_workspaces.quality_reviews={version:1};if(state.capability)caps.features.pm_workspaces.package_handoff.requirement_acceptance_profiles=[PROFILE];}
    return result;
  };
  function saved(){const batch=f.evidence();batch.entries[0].result_condition=structuredClone(condition);batch.requirement_acceptance='see_explicit_original_evidence';return batch;}
  function downstream(){const row=structuredClone(pkg);row.package_id=row.document.package_id='pmwp_'+'9'.repeat(32);row.document_digest='f'.repeat(64);row.document.title='Downstream implementation';row.sequence=2;
    row.work_item_id=row.document.work_item_id=row.document.context.work_ref.work_item_id='wi_'+'9'.repeat(20);row.document.requirements=[{id:'downstream',title:'Downstream',depends_on:['recovery'],acceptance:[{source_input_id:'in_c',text:'Use accepted recovery'}]}];f.base.fixture.state.packages.push(row);return row;}
  return {...f,review,reviews,condition,ref,quality:state,saved,downstream};
}
async function reviewed(page:Page){await handoff(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();await expect(handoff(page).getByRole('heading',{name:'Reviewed implementation base'})).toBeVisible();const rebase=handoff(page).getByRole('button',{name:'Use this reviewed project version',exact:true});if(await rebase.isVisible())await rebase.click();await handoff(page).getByLabel('Why use this implementation base?',{exact:true}).fill('Reviewed original base');await handoff(page).getByLabel('Instructions for this implementation',{exact:true}).fill('Implement exact selected original requirements.');}
async function selectProof(page:Page,f:ReturnType<typeof fixture>){await proof(page).getByLabel('Original batch entry',{exact:true}).selectOption('first');await proof(page).getByLabel('Qualified original assessment',{exact:true}).selectOption(JSON.stringify(f.ref()));await proof(page).getByLabel('Reason for this request',{exact:true}).fill('Select the original complete reasoned assessment.');}

test('explicit result profile freezes every original acceptance identity and never treats dependency checkbox as proof',async({page})=>{
  const f=fixture();await mountBatch(page,f);await reviewed(page);await panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true}).click();
  await start(page).getByLabel('Result condition',{exact:true}).selectOption(PROFILE);
  const stored=await page.evaluate(()=>Object.values(localStorage).map(value=>{try{return JSON.parse(value);}catch{return null;}}).find(value=>value?.values?.entries));
  expect(JSON.parse(stored.values.entries)[0].result_condition).toEqual(f.condition);expect(f.state.writes).toHaveLength(0);
});

test('source-gated qualified original assessment is explicitly recorded with fixed five-field reference',async({page})=>{
  const f=fixture();f.saved();f.quality.partial=true;await mountBatch(page,f);await expect(proof(page).locator('button[type=submit]')).toBeDisabled();await selectProof(page,f);
  await expect(proof(page).getByLabel('Delivered integration receipt',{exact:true})).toBeHidden();await proof(page).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params.evidence).toEqual([{entry_key:'first',condition:f.condition,reference:f.ref()}]);expect(f.state.reads.some(path=>path.includes('quality-reviews?after=5'))).toBe(true);
  await expect(panel(page)).toContainText('Original requirements accepted with complete qualified review');
});

for(const change of ['unqualified','other-child','other-plan','incomplete'])test(`${change} assessment cannot become semantic batch evidence`,async({page})=>{
  const f=fixture();f.saved();if(change==='unqualified')f.review.qualified=false;else if(change==='other-child')f.review.context.execution_operation_id='op_'+'7'.repeat(32);else if(change==='other-plan')f.review.context.plan_ref={...f.review.context.plan_ref,scope_revision:'pmscope_'+'8'.repeat(32)};else f.review.assessment.document.criteria.pop();
  await mountBatch(page,f);await proof(page).getByLabel('Original batch entry',{exact:true}).selectOption('first');await expect(proof(page).getByLabel('Qualified original assessment',{exact:true}).locator('option')).toHaveCount(1);await expect(proof(page).locator('button[type=submit]')).toBeDisabled();expect(f.state.writes).toHaveLength(0);
});

test('refresh never replaces selected original with newer assessment and withdrawal removes private selectors',async({page})=>{
  const f=fixture();f.saved();await mountBatch(page,f);await selectProof(page,f);const original=JSON.stringify(f.ref());f.review.assessment.version=3;f.review.assessment.operation_id='op_'+'5'.repeat(32);
  await page.evaluate(()=>(window as any).batchOnlyRefresh());await expect(proof(page).getByLabel('Qualified original assessment',{exact:true})).toHaveValue(original);await expect(proof(page).locator('button[type=submit]')).toBeDisabled();
  f.quality.deny=true;await page.evaluate(()=>(window as any).batchOnlyRefresh());await expect(proof(page)).toBeHidden();await expect(proof(page).getByLabel('Qualified original assessment',{exact:true}).locator('option')).toHaveCount(0);await expect(panel(page)).not.toContainText('responsible-reviewer');
});

test('uncertain semantic evidence reply reconciles original key despite later review and pause',async({page})=>{
  const f=fixture();f.saved();await mountBatch(page,f);await selectProof(page,f);f.state.lost=true;await proof(page).locator('button[type=submit]').click();await expect(proof(page)).toContainText('Reply unavailable');const original=structuredClone(f.state.writes[0]);
  f.review.assessment.version=3;f.base.fixture.state.stage='paused';await page.evaluate(()=>(window as any).batchOnlyRefresh());await proof(page).getByRole('button',{name:'Check original request',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(2);
  expect(f.state.writes[1].body).toEqual(original.body);expect(f.state.writes[1].idempotency_key).toBe(original.idempotency_key);expect(f.state.ops.size).toBe(1);
});

test('handoff readiness carries only explicitly selected original prerequisite map',async({page})=>{
  const f=fixture(),downstream=f.downstream();await mountBatch(page,f);await page.evaluate(id=>(window as any).batchSelect(id),downstream.package_id);
  await handoff(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();const selection=handoff(page).getByLabel('Accepted prerequisite recovery',{exact:true});await expect(selection).toHaveValue('');await expect(handoff(page)).toContainText('can be selected for a bounded batch');await expect(handoff(page).getByRole('button',{name:'Start implementation',exact:true})).toBeDisabled();
  await selection.selectOption({index:1});await reviewed(page);await handoff(page).getByRole('button',{name:'Start implementation',exact:true}).click();await expect.poll(()=>f.base.state.writes.length).toBe(1);
  const expected={recovery:{condition:f.condition,reference:f.ref(),execution_operation_id:f.review.context.execution_operation_id}};
  expect(f.base.state.writes[0].body.params.pm_requirement_quality_refs).toEqual(expected);
  const selected=f.base.state.reads.filter(row=>row.path.includes('/readiness?quality_refs=')).at(-1);expect(JSON.parse(new URL(selected.path,'http://fixture').searchParams.get('quality_refs')!)).toEqual(expected);
});

for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`semantic selector ${locale} ${width}`,async({page},info)=>{
  const f=fixture();f.saved();const row=f.downstream();await page.setViewportSize({width,height:900});await mountBatch(page,f,locale);await page.evaluate(id=>(window as any).batchSelect(id),row.package_id);
  await handoff(page).getByRole('button',{name:locale==='en'?'Review fixed implementation base':'核對固定實作基底',exact:true}).click();
  await proof(page).getByLabel(locale==='en'?'Original batch entry':'原始批次項目',{exact:true}).selectOption('first');
  expect(await page.evaluate(()=>[...document.querySelectorAll('main *')].filter(node=>node.getBoundingClientRect().right>innerWidth+1).map(node=>node.tagName))).toEqual([]);expect(await page.locator('main').innerText()).not.toMatch(/(?:ph|pmb)_[a-z_]+/);
  const out=process.env.BATC_PM_REQUIREMENT_SCREENSHOTS||info.outputPath('screenshots');await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/semantic-${locale}-${width}.png`,fullPage:true});
});

test('older quality-enabled central receives no unsupported prerequisite selector reads or query',async({page})=>{
  const f=fixture(),row=f.downstream();f.quality.capability=false;await mountBatch(page,f);await page.evaluate(id=>(window as any).batchSelect(id),row.package_id);
  await handoff(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();await expect(handoff(page)).toContainText('unresolved checks beyond');
  await expect(handoff(page).getByLabel('Accepted prerequisite recovery',{exact:true})).toHaveCount(0);expect(f.state.reads.some(path=>path.includes('/quality-reviews'))).toBe(false);expect(f.base.state.reads.some(row=>row.path.includes('quality_refs'))).toBe(false);expect(f.base.state.writes).toHaveLength(0);
});

test('a changed selected assessment cannot silently rebase the handoff and original unknown request remains recoverable',async({page})=>{
  const f=fixture(),row=f.downstream();await mountBatch(page,f);await page.evaluate(id=>(window as any).batchSelect(id),row.package_id);
  await handoff(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();await handoff(page).getByLabel('Accepted prerequisite recovery',{exact:true}).selectOption({index:1});await reviewed(page);
  f.base.state.lost=true;await handoff(page).getByRole('button',{name:'Start implementation',exact:true}).click();await expect(handoff(page)).toContainText('Reply unavailable');const original=structuredClone(f.base.state.writes[0]);
  f.review.assessment.version++;f.base.fixture.state.stage='paused';await page.evaluate(()=>(window as any).batchRefresh());await handoff(page).getByRole('button',{name:'Check original request',exact:true}).click();await expect.poll(()=>f.base.state.writes.length).toBe(2);
  expect(f.base.state.writes[1].body).toEqual(original.body);expect(f.base.state.writes[1].idempotency_key).toBe(original.idempotency_key);expect(f.base.state.ops.size).toBe(1);
});

test('withdrawn prerequisite sources clear handoff selectors before any code preview or new operation',async({page})=>{
  const f=fixture(),row=f.downstream();await mountBatch(page,f);await page.evaluate(id=>(window as any).batchSelect(id),row.package_id);await handoff(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();
  await handoff(page).getByLabel('Accepted prerequisite recovery',{exact:true}).selectOption({index:1});const before=f.base.state.reads.filter(row=>row.path==='/repository-previews').length;f.quality.deny=true;await handoff(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();
  await expect(handoff(page).getByLabel('Accepted prerequisite recovery',{exact:true})).toHaveCount(0);await expect(handoff(page)).not.toContainText('responsible-reviewer');expect(f.base.state.writes).toHaveLength(0);expect(f.base.state.reads.filter(row=>row.path==='/repository-previews')).toHaveLength(before);
});

test('quality catalog cursor must advance before proof options become available',async({page})=>{
  const f=fixture();f.saved();f.quality.badCursor=true;await mountBatch(page,f);await expect(panel(page)).toContainText('Original batch sources are unavailable');await expect(proof(page)).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
});

test('whole UTF-8 prerequisite map bound counts JSON wire spacing',async({page})=>{
  const f=fixture();await mountBatch(page,f);
  expect(await page.evaluate(async()=>{const {boundedQualityMap}=await import('/__batch_src/pm-requirement-quality.js');const value={x:'字'.repeat(5458)};boundedQualityMap(value);try{boundedQualityMap({x:'字'.repeat(5460)});return false;}catch{return true;}})).toBe(true);
});

test('changed upstream condition requires explicit prerequisite reselection',async({page})=>{
  const f=fixture();await mountBatch(page,f);await reviewed(page);await panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true}).click();
  const row=f.downstream();row.document.requirements[0].depends_on=[];await page.evaluate(id=>(window as any).batchSelect(id),row.package_id);await reviewed(page);await panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true}).click();
  const rows=start(page).locator('.pm-batch-entry');await rows.nth(1).getByText('Prerequisites in this selection',{exact:true}).click();await rows.nth(1).getByRole('checkbox').check();
  await rows.nth(0).getByLabel('Result condition',{exact:true}).selectOption(PROFILE);await expect(rows.nth(1)).toContainText('An upstream result condition changed');await expect(start(page).locator('button[type=submit]')).toBeDisabled();
  await rows.nth(1).getByRole('checkbox').uncheck();await rows.nth(1).getByRole('checkbox').check();await expect(rows.nth(1).locator('[data-condition-changed]')).toBeHidden();
  const stored=await page.evaluate(()=>Object.values(localStorage).map(value=>{try{return JSON.parse(value);}catch{return null;}}).find(value=>value?.values?.entries));const selected=JSON.parse(stored.values.entries);expect(selected[1].depends_on[0].condition).toEqual(f.condition);
});

test('batch fresh-start preflight rechecks original prerequisite assessment sources',async({page})=>{
  const f=fixture(),row=f.downstream();await mountBatch(page,f);await page.evaluate(id=>(window as any).batchSelect(id),row.package_id);
  await handoff(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();await handoff(page).getByLabel('Accepted prerequisite recovery',{exact:true}).selectOption({index:1});await reviewed(page);await panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true}).click();
  await start(page).getByLabel('Reason for this request',{exact:true}).fill('Exact reviewed selection');const deadline=new Date(Date.now()+3600000);await start(page).getByLabel('Deadline (local time)',{exact:true}).fill(new Date(deadline.getTime()-deadline.getTimezoneOffset()*60000).toISOString().slice(0,16));
  f.quality.deny=true;await start(page).locator('button[type=submit]').click();await expect(panel(page)).toContainText('Original batch sources are unavailable');expect(f.state.writes).toHaveLength(0);
});

test('declared unmet prerequisites can wait in explicit batch but never enable single handoff start',async({page})=>{
  const f=fixture();await mountBatch(page,f);await reviewed(page);await panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true}).click();await start(page).getByLabel('Result condition',{exact:true}).selectOption(PROFILE);
  const row=f.downstream();await page.evaluate(id=>(window as any).batchSelect(id),row.package_id);await reviewed(page);await expect(handoff(page).getByRole('button',{name:'Start implementation',exact:true})).toBeDisabled();
  await expect(handoff(page)).toContainText('waits for the declared prerequisites');await panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true}).click();const rows=start(page).locator('.pm-batch-entry');await rows.nth(1).getByText('Prerequisites in this selection',{exact:true}).click();await rows.nth(1).getByRole('checkbox').check();
  await start(page).getByLabel('Reason for this request',{exact:true}).fill('Await original upstream acceptance before downstream starts');const deadline=new Date(Date.now()+3600000);await start(page).getByLabel('Deadline (local time)',{exact:true}).fill(new Date(deadline.getTime()-deadline.getTimezoneOffset()*60000).toISOString().slice(0,16));
  await start(page).getByLabel('Maximum starts',{exact:true}).fill('2');await start(page).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);
  const entries=f.state.writes[0].body.params.entries;expect(entries[1].depends_on).toEqual([{entry_key:entries[0].key,condition:f.condition}]);expect(entries[1].handoff.params.pm_requirement_quality_refs).toBeUndefined();expect(f.base.state.writes).toHaveLength(0);
});

test('batch preparation never bypasses unrelated original source readiness checks',async({page})=>{
  const f=fixture(),row=f.downstream();f.base.state.blocked='scope';await mountBatch(page,f);await page.evaluate(id=>(window as any).batchSelect(id),row.package_id);await handoff(page).getByRole('button',{name:'Review fixed implementation base',exact:true}).click();
  await expect(handoff(page)).toContainText('unresolved checks beyond');await expect(panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true})).toBeDisabled();await expect(handoff(page).getByRole('button',{name:'Start implementation',exact:true})).toBeDisabled();expect(f.base.state.reads.some(row=>row.path==='/repository-previews')).toBe(false);
});

for(const native of [false,true])test(`mounted ${native?'native':'browser'} batch preserves unmet declared prerequisites without single dispatch`,async({page})=>{
  const f=fixture();f.reviews.length=0;await mountConversation(page,native,f.dispatch);await page.goto('/dashboard/#/pm/'+workspaceId);
  await page.locator('[data-pm-work-packages]').getByRole('button',{name:'Restore member access · Preparation 1',exact:true}).click();await handoff(page).locator('details>summary').first().click();await reviewed(page);
  await panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true}).click();await start(page).getByLabel('Result condition',{exact:true}).selectOption(PROFILE);
  f.downstream();await page.getByRole('button',{name:'Refresh work packages',exact:true}).click();await page.locator('[data-pm-work-packages]').getByRole('button',{name:'Downstream implementation · Preparation 2',exact:true}).click();await reviewed(page);
  await expect(handoff(page).getByRole('button',{name:'Start implementation',exact:true})).toBeDisabled();await panel(page).getByRole('button',{name:'Add reviewed handoff',exact:true}).click();const entries=start(page).locator('.pm-batch-entry');await entries.nth(1).getByText('Prerequisites in this selection',{exact:true}).click();await entries.nth(1).getByRole('checkbox').check();
  await start(page).getByLabel('Maximum starts',{exact:true}).fill('2');await start(page).getByLabel('Reason for this request',{exact:true}).fill('Bounded dependency admission');const deadline=new Date(Date.now()+3600000);await start(page).getByLabel('Deadline (local time)',{exact:true}).fill(new Date(deadline.getTime()-deadline.getTimezoneOffset()*60000).toISOString().slice(0,16));
  await start(page).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.params.entries[1].depends_on[0].condition).toEqual(f.condition);expect(f.base.state.writes).toHaveLength(0);
});
