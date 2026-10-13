import {openPmSection} from './pm-navigation.ts';
import {mountConversation} from './conversation-fixture.ts';
import {pmFixture} from './pm-fixture.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {action,boot,mount,openAction,reason,reviewFixture,wid,pid,bid} from './pm-maintenance-review-fixture.ts';

test.use({timezoneId:'America/New_York'});

async function saveNote(page:any){await reason(page,'note');await action(page,'note').getByLabel('Discussion note',{exact:true}).fill('Keep this actual discussion');await action(page,'note').getByRole('button',{name:'Save discussion note',exact:true}).click();}

for(const locale of ['en','zh-TW'])test(`saved repair distinguishes publication from held original capacity ${locale}`,async({page})=>{
  const f=reviewFixture(),report=f.state.reviews[0].snapshot.maintenance.report;
  const repairId='pmrp_'+'a'.repeat(32),operationId='op_'+'a'.repeat(32),sourceId='pmrs_'+'a'.repeat(24);
  report.sources.push({source_id:sourceId,kind:'maintenance_repair',id:repairId,digest:'a'.repeat(64),basis:'admission_context'});
  report.coverage.repairs={status:'observed',samples:1};
  report.repairs=[{repair_id:repairId,operation_id:operationId,source_id:sourceId,outcome:'published_for_review',capacity_held:true,
    runtime_completion:null,started_at:report.period.start,observation_basis:'saved_repair_point',later_changes_included:false,children:[]}];
  await mount(page,f,locale);await page.getByText(locale==='en'?'Original saved samples':'原始保存取樣',{exact:true}).click();
  const sample=page.locator('[data-review-repair]');await expect(sample).toContainText(locale==='en'?'Published for review':'已發布供審查');
  await expect(sample).toContainText(locale==='en'?'capacity remains reserved':'仍占用容量');
  await expect(sample).toContainText(locale==='en'?'completion unproven':'完成尚未證實');
  await sample.locator('summary').click();await expect(sample.locator('a')).toHaveAttribute('href','#/op/'+operationId);
  expect(f.state.writes).toHaveLength(0);f.state.failRead=true;await page.evaluate(()=>(window as any).refreshReviews());await expect(sample).toHaveCount(0);
});

for(const locale of ['en','zh-TW'])test(`fixed configured findings remain in the report and withdraw with its source ${locale}`,async({page})=>{
  const f=reviewFixture(),report=f.state.reviews[0].snapshot.maintenance.report;
  const kinds=['issues','dependabot_pull_requests','dependabot_alerts'],findingId='pmfn_'+'a'.repeat(32);
  const sources=Object.fromEntries(kinds.map(kind=>[kind,{status:kind==='issues'?'complete':'disabled',findings:kind==='issues'?[{
    finding_id:findingId,native:{kind,id:501,number:50,repository_id:7,provider_digest:'c'.repeat(64)},facts:{labels:['bug']},existing_work:[],existing_pull_request:null}]:[]}]));
  Object.assign(report.observations[0],{observation_id:'pmgh_'+'5'.repeat(32),document_digest:'e'.repeat(64),maintenance_sources:sources});
  Object.assign(report,{finding_changes:Object.fromEntries(kinds.map(kind=>[kind,{status:'comparison_unavailable',added:[],changed:[],no_longer_observed:[]}]))});
  report.coverage.issues={status:'observed',samples:1};
  await mount(page,f,locale);await page.getByText(locale==='en'?'Original saved samples':'原始保存取樣',{exact:true}).click();
  const view=page.locator('[data-review-report] [data-maintenance-findings]');await expect(view).toBeVisible();
  await view.getByText('Issue #50',{exact:true}).click();await expect(view).toContainText(findingId);
  await expect(view).toContainText(locale==='en'?'released version remains unproven':'發布版本仍');
  expect(f.state.reads.some((path:string)=>path.includes('/maintenance-findings'))).toBe(false);
  f.state.failRead=true;await page.evaluate(()=>(window as any).refreshReviews());await expect(view).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
});

test('fixed report and weekly status do not mark discussion complete or create work',async({page})=>{
  const f=reviewFixture();await mount(page,f);
  await expect(page.getByRole('heading',{name:'Membership service maintenance',exact:true})).toBeVisible();
  await expect(page.locator('[data-review-discussion]')).toContainText('No completed discussion');
  await page.getByText('Weekly report schedule',{exact:true}).click();await expect(page.locator('[data-pm-maintenance-review]')).toContainText('waiting for discussion');
  expect(f.state.writes).toHaveLength(0);
  await saveNote(page);expect(f.state.writes[0].body).toEqual({action:'pm.maintenance_review.note',target:{workspace_id:wid,review_id:f.state.reviews[0].review.review_id},
    params:{content:'Keep this actual discussion',rationale:'Keep the fixed discussion outcome',source_refs:[]},preconditions:{expected_version:1}});
  expect(f.state.reviews[0].review.status).toBe('prepared');
});

test('preparation uses current source CAS and an explicit past reporting period',async({page})=>{
  const f=reviewFixture();await mount(page,f);await openAction(page,'prepare');const editor=action(page,'prepare');
  await editor.getByLabel('Report title',{exact:true}).fill('Next fixed report');await editor.getByLabel('Period start',{exact:true}).fill('2026-10-01T09:00');await editor.getByLabel('Period end',{exact:true}).fill('2026-10-08T09:00');
  await editor.getByRole('button',{name:'Prepare report',exact:true}).click();
  expect(f.state.writes[0].body.preconditions).toEqual({expected_plan_version:4,expected_review_revision:1,expected_policy_id:pid,expected_policy_version:3,expected_baseline_id:bid,expected_lifecycle_revision:2});
  expect(f.state.writes[0].body.params).toMatchObject({title:'Next fixed report'});expect(f.state.writes[0].body.params).not.toHaveProperty('previous_review_id');
  await expect(page.getByRole('heading',{name:'Next fixed report',exact:true})).toBeVisible();expect(f.state.reviews.at(-1).review.status).toBe('prepared');
});

test('candidate requires original evidence and keeps ordinary fields and proposal status',async({page})=>{
  const f=reviewFixture();await mount(page,f);await reason(page,'candidate.record');const editor=action(page,'candidate.record');
  for(const[label,value]of [['Candidate title','Cover a gap'],['Observed problem','Dependency evidence is missing'],['Relationship to the goal','Maintain predictable releases'],['Expected effect','Better evidence']])await editor.getByLabel(label,{exact:true}).fill(value);
  await expect(editor.locator('button[type=submit]')).toBeDisabled();
  await editor.getByText('Original report sources',{exact:true}).click();await editor.getByLabel('Adopted maintenance policy · '+pid,{exact:true}).check();
  await editor.locator('button[type=submit]').click();
  expect(f.state.writes[0].body.params.source_refs).toEqual(['pmrs_'+'1'.repeat(24)]);expect(f.state.writes[0].body.params.options).toEqual([]);
  await expect(page.locator('[data-review-discussion]')).toContainText('has not changed the accepted scope');expect(f.state.reviews[0].review.status).toBe('prepared');
});

test('only owner with a selected saved note and acknowledgment can complete then report followup',async({page})=>{
  const f=reviewFixture();await mount(page,f);await reason(page,'complete');const editor=action(page,'complete');
  await expect(editor.locator('button[type=submit]')).toBeDisabled();await editor.getByText('Saved notes discussed',{exact:true}).click();
  await editor.getByLabel('developer · We discussed the incomplete workload evidence.',{exact:true}).check();
  await editor.getByLabel('I acknowledge this saved discussion took place.',{exact:true}).check();
  await editor.getByLabel('Discussion summary',{exact:true}).fill('We discussed missing evidence and agreed to investigate.');
  await editor.getByLabel('Reason for this direction',{exact:true}).fill('Observe before repair');
  await editor.getByText('Follow-up assignments',{exact:true}).click();await editor.getByRole('button',{name:'Add follow-up',exact:true}).click();
  await editor.getByLabel('Follow-up',{exact:true}).fill('Inspect the missing evidence');await editor.getByLabel('Responsible person',{exact:true}).selectOption('owner');
  await editor.getByLabel('Expected effect',{exact:true}).fill('Make uncertainty visible');await editor.getByLabel('Verification method',{exact:true}).fill('Attach a fixed source');
  await editor.locator('button[type=submit]').click();expect(f.state.writes[0].body.params.acknowledged_note_ids).toEqual([f.state.reviews[0].events[0].event_id]);
  expect(f.state.writes[0].body.params.discussion_acknowledged).toBe(true);expect(f.state.reviews[0].review.status).toBe('completed');
  await expect(page.locator('[data-review-discussion]').getByRole('heading',{name:'Discussion outcome',exact:true})).toBeVisible();
  await reason(page,'follow_up.record');const follow=action(page,'follow_up.record');await follow.getByLabel('Reported status',{exact:true}).selectOption('reported_done');
  await follow.getByLabel('Reported result',{exact:true}).fill('Evidence collected, quality not yet assessed');await follow.locator('button[type=submit]').click();
  expect(f.state.writes[1].body.params.status).toBe('reported_done');await expect(page.locator('[data-review-discussion]')).toContainText('Reported progress is not verified quality');
});

test('contributor may record notes but cannot complete the owner discussion',async({page})=>{
  const f=reviewFixture();f.state.actor='developer';f.state.role='contributor';await mount(page,f);await openAction(page,'complete');
  await expect(action(page,'complete').locator('button[type=submit]')).toBeDisabled();await saveNote(page);expect(f.state.writes).toHaveLength(1);
});

test('lost original note receipt survives reload and lifecycle change without new intent',async({page})=>{
  const f=reviewFixture();await mount(page,f);f.state.lost=true;await saveNote(page);await expect(action(page,'note')).toContainText('Reply unavailable');
  const original=structuredClone(f.state.writes[0]);f.state.lifecycle++;f.state.epoch++;f.state.stage='paused';await page.reload();await boot(page);await openAction(page,'note');
  await action(page,'note').getByRole('button',{name:'Check original request',exact:true}).click();expect(f.state.writes.at(-1)).toEqual(original);expect(f.state.ops.size).toBe(1);
});

test('source withdrawal clears report and selector facts while preserving disabled original recovery',async({page})=>{
  const f=reviewFixture();await mount(page,f);f.state.lost=true;await saveNote(page);const original=structuredClone(f.state.writes[0]);
  f.state.failRead=true;await page.evaluate(()=>(window as any).refreshReviews());await expect(page.locator('[data-review-report]')).toHaveCount(0);
  await expect(action(page,'note').locator('button[type=submit]')).toBeDisabled();
  f.state.failRead=false;await page.evaluate(()=>(window as any).refreshReviews());await openAction(page,'note');await action(page,'note').getByRole('button',{name:'Check original request',exact:true}).click();
  expect(f.state.writes.at(-1)).toEqual(original);
});

test('a dirty draft keeps focus and text on refresh and explicitly rebases only its CAS',async({page})=>{
  const f=reviewFixture();await mount(page,f);await reason(page,'note','Preserve this draft');const content=action(page,'note').getByLabel('Discussion note',{exact:true});await content.fill('An unfinished thought');
  f.state.reviews[0].review.version=7;await page.evaluate(()=>(window as any).refreshReviews());await expect(content).toHaveValue('An unfinished thought');
  await expect(action(page,'note').locator('button[type=submit]')).toBeDisabled();await page.getByRole('button',{name:'Use the displayed versions for this draft',exact:true}).click();
  await openAction(page,'note');await expect(action(page,'note').getByLabel('Discussion note',{exact:true})).toHaveValue('An unfinished thought');await action(page,'note').locator('button[type=submit]').click();
  expect(f.state.writes[0].body.preconditions).toEqual({expected_version:7});
});

test('empty filtered pages are awaited and malformed pagination cannot authorize writes',async({page})=>{
  const f=reviewFixture();f.state.paged=true;await mount(page,f);expect(f.state.reads).toContain('/pm/workspaces/'+wid+'/snapshots?after=10&limit=50');
  await expect(page.locator('[data-pm-maintenance-review]')).toContainText('hidden by current source access');
  f.state.badCursor=true;await page.evaluate(()=>(window as any).refreshReviews());await expect(page.locator('[data-review-report]')).toHaveCount(0);await expect(action(page,'note').locator('button[type=submit]')).toBeDisabled();expect(f.state.writes).toHaveLength(0);
});

test('zero event cursor and empty filtered history continue to the next complete note',async({page})=>{
  const f=reviewFixture();f.state.eventPages=true;f.state.zeroCursor=true;await mount(page,f);
  await page.getByRole('button',{name:'More discussion history',exact:true}).click();expect(f.state.reads.some((path:string)=>path.endsWith('/events?after=0&limit=20'))).toBe(true);
  await page.getByRole('button',{name:'More discussion history',exact:true}).click();await expect(page.locator('[data-review-discussion]')).toContainText('We also reviewed the next observation focus.');
  await openAction(page,'complete');await action(page,'complete').getByText('Saved notes discussed',{exact:true}).click();await expect(action(page,'complete').getByLabel('owner · We also reviewed the next observation focus.',{exact:true})).toBeVisible();
});

test('hidden latest report never falls back to a visible older report',async({page})=>{
  const f=reviewFixture();f.addReview('Another fixed report');f.state.hidden=true;await mount(page,f);await expect(page.locator('[data-review-report]')).toHaveCount(0);
  await expect(page.locator('[data-pm-maintenance-review]')).toContainText('latest report is hidden');expect(f.state.writes).toHaveLength(0);
});

test('public source collection draft survives reload and records dated attributed claims',async({page})=>{
  const f=reviewFixture();await mount(page,f);await reason(page,'candidate.record');let editor=action(page,'candidate.record');
  for(const[label,value]of [['Candidate title','Check upstream compatibility'],['Observed problem','Dependency evidence is absent'],['Relationship to the goal','Maintain compatibility'],['Expected effect','Fill the evidence gap']])await editor.getByLabel(label,{exact:true}).fill(value);
  await editor.getByText('Original report sources',{exact:true}).click();await editor.getByLabel('Adopted maintenance policy · '+pid,{exact:true}).check();
  await editor.getByText('Attributed public sources (optional)',{exact:true}).click();await editor.getByRole('button',{name:'Add public source',exact:true}).click();
  await editor.getByLabel('Public source URL',{exact:true}).fill('https://example.org/release');await editor.getByLabel('Source title',{exact:true}).fill('Upstream release notes');
  await editor.getByLabel('Source checked at',{exact:true}).fill('2026-10-08T09:00');await editor.getByLabel('Attributed source claim',{exact:true}).fill('The author reports changed compatibility.');
  await page.reload();await boot(page);await openAction(page,'candidate.record');editor=action(page,'candidate.record');
  await editor.getByText('Attributed public sources (optional)',{exact:true}).click();await expect(editor.getByLabel('Public source URL',{exact:true})).toHaveValue('https://example.org/release');
  await editor.locator('button[type=submit]').click();expect(f.state.writes[0].body.params.public_sources[0]).toEqual({url:'https://example.org/release',title:'Upstream release notes',checked_at:1791464400,claim:'The author reports changed compatibility.'});
  await expect(page.locator('[data-review-discussion]')).toContainText('The author reports changed compatibility.');
});

test('awaited empty filtered second page keeps stale facts and forms hidden until complete',async({page})=>{
  const f=reviewFixture();await mount(page,f);await reason(page,'note');await action(page,'note').getByLabel('Discussion note',{exact:true}).fill('Keep my pending local draft');
  f.state.paged=true;f.state.delayed=true;f.state.delayedAfter=10;const pending=page.evaluate(()=>(window as any).refreshReviews());
  await expect.poll(()=>typeof f.state.releaseRead).toBe('function');await expect(page.locator('[data-review-report]')).toHaveCount(0);
  await expect(action(page,'note').locator('button[type=submit]')).toBeDisabled();f.state.delayed=false;f.state.releaseRead();await pending;
  await expect(action(page,'note').getByLabel('Discussion note',{exact:true})).toHaveValue('Keep my pending local draft');
  await expect(action(page,'note').locator('button[type=submit]')).toBeEnabled();expect(f.state.writes).toHaveLength(0);
});

test('unchanged refresh preserves the focused draft and selected report survives reload',async({page})=>{
  const f=reviewFixture();const first=f.state.reviews[0];f.addReview('More recent report');await mount(page,f);
  await page.getByLabel('Select a report',{exact:true}).selectOption(first.review.review_id);await expect(page.getByRole('heading',{name:first.review.title,exact:true})).toBeVisible();
  await reason(page,'note');const content=action(page,'note').getByLabel('Discussion note',{exact:true});await content.fill('Continue this thought');await content.evaluate((node:HTMLTextAreaElement)=>node.setSelectionRange(4,8));
  await page.evaluate(()=>(window as any).refreshReviews());await expect(content).toBeFocused();expect(await content.evaluate((node:HTMLTextAreaElement)=>[node.selectionStart,node.selectionEnd])).toEqual([4,8]);
  await page.reload();await boot(page);await openAction(page,'note');await expect(page.getByRole('heading',{name:first.review.title,exact:true})).toBeVisible();
  await expect(action(page,'note').getByLabel('Discussion note',{exact:true})).toHaveValue('Continue this thought');
});

test('wrong-scope data clears facts and mismatched receipt cannot replace original intent',async({page})=>{
  const f=reviewFixture();await mount(page,f);f.state.mismatch=true;await saveNote(page);await expect(action(page,'note')).toContainText('receipt');const original=structuredClone(f.state.writes[0]);
  f.state.wrongScope=true;await page.evaluate(()=>(window as any).refreshReviews());await expect(page.locator('[data-review-report]')).toHaveCount(0);
  f.state.wrongScope=false;f.state.mismatch=false;await page.evaluate(()=>(window as any).refreshReviews());await openAction(page,'note');
  await action(page,'note').getByRole('button',{name:'Check original request',exact:true}).click();expect(f.state.writes.at(-1)).toEqual(original);expect(f.state.ops.size).toBe(1);
});

test('invalid report period remains editable and weekly capacity is explained',async({page})=>{
  const f=reviewFixture();f.state.scheduleBlocked=true;await mount(page,f);await page.getByText('Weekly report schedule',{exact:true}).click();
  await expect(page.locator('[data-pm-maintenance-review]')).toContainText('concurrent or period run allowance is in use');await openAction(page,'prepare');const editor=action(page,'prepare');
  await editor.getByLabel('Report title',{exact:true}).fill('Bounded period');await editor.getByLabel('Period start',{exact:true}).fill('2026-10-08T09:00');await editor.getByLabel('Period end',{exact:true}).fill('2026-10-01T09:00');
  await expect(editor.locator('button[type=submit]')).toBeDisabled();await expect(editor.getByLabel('Period end',{exact:true})).toBeEnabled();
  await editor.getByLabel('Period end',{exact:true}).fill('2026-10-09T09:00');await expect(editor.locator('button[type=submit]')).toBeEnabled();
});

test('original sample provenance and bounded previous discussion remain separately readable',async({page})=>{
  const f=reviewFixture(),detail=f.state.reviews[0],previousId='pmrevw_'+'b'.repeat(32),priorNote={...structuredClone(detail.events[0]),review_id:previousId,event_id:'pmrve_'+'c'.repeat(32)};
  detail.snapshot.maintenance.report.previous_review={review_id:previousId,status:'prepared',snapshot_ref:{snapshot_id:'pmsnap_'+'d'.repeat(32)},history_truncated:true,completion:null,notes:[priorNote],candidates:[],follow_up_results:[]};
  await mount(page,f);await page.getByText('Original saved samples',{exact:true}).click();await expect(page.locator('[data-review-report]')).toContainText('Observed open PRs: 0');
  await page.getByText('Previous discussion context',{exact:true}).click();await expect(page.locator('[data-review-report]')).toContainText('full history remains in that report');
  await expect(page.locator('[data-review-report]')).toContainText('We discussed the incomplete workload evidence.');await expect(page.locator('[data-review-discussion]')).toContainText('No completed discussion');
});

for(const locale of ['en','zh-TW'])for(const width of [1440,768,390])test(`maintenance Review layout ${locale} ${width}`,async({page},info)=>{
  const f=reviewFixture();await page.setViewportSize({width,height:900});await mount(page,f,locale);await openAction(page,'note');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(await page.locator('[data-pm-maintenance-review]').innerText()).not.toMatch(/pmrv_[a-z_]+/);
  const output=process.env.BATC_PM_REVIEW_SCREENSHOTS||info.outputPath('screenshots');await mkdir(output,{recursive:true});await page.screenshot({path:`${output}/review-${locale}-${width}.png`,fullPage:true});
});

for(const native of [false,true])test(native?'native shared workspace maintenance Review':'web shared workspace maintenance Review',async({page})=>{
  const f=reviewFixture(),base=pmFixture();f.state.actor='reader';
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path.startsWith('/operations/')||input.body?.action?.startsWith('pm.maintenance_review.')
      ||path.startsWith('/pm/workspaces/'+wid+'/maintenance-reviews')
      ||['/maintenance-policies/current','/release-baselines/current','/maintenance-schedule','/lifecycle','/plans/head','/snapshots'].some(suffix=>path==='/pm/workspaces/'+wid+suffix))return f.dispatch(input);
    const response=await base.dispatch(input),config=f.config();
    if(path==='/bootstrap'||path==='/capabilities'){
      const caps=path==='/bootstrap'?response.data.capabilities:response.data;caps.actions.push(...config.caps.actions);
      caps.features.pm_workspaces.maintenance_review={version:1};caps.features.pm_workspaces.maintenance_checks={version:1,weekly_review_execution:true};
    }
    if(path==='/pm/workspaces/'+wid)Object.assign(response.data.workspace,config.workspace);
    if(path==='/events')for(const event of response.data.events)event.resource_type='pm_maintenance_review';
    return response;
  };
  await mountConversation(page,native,dispatch);await page.goto('/dashboard/#/pm/'+wid);await openPmSection(page,'issues','ui_maintenance_review');
  const box=page.locator('[data-pm-maintenance-review]');await expect(box).toContainText('Membership service maintenance');
  await saveNote(page);await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.action).toBe('pm.maintenance_review.note');await expect(box).toContainText('Keep this actual discussion');
  f.state.failRead=true;base.state.cursor++;
  await expect(box).toContainText('This Review or one of its original sources is unavailable.');await expect(box.locator('[data-review-report]')).toHaveCount(0);
  await expect(box).not.toContainText('Membership service maintenance');
});
