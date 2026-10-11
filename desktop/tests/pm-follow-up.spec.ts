import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {fixture,mount,intakeId,snapshotId,threadId,governanceId,meetingId,completionId,resultId} from './pm-follow-up-fixture.ts';

const section=(page:any)=>page.locator('[data-pm-follow-up]');
const edit=(page:any)=>page.locator('[data-pm-fu-editor]');
const refresh=(page:any)=>page.evaluate(()=>(window as any).refreshFollowUp());
async function openEditor(page:any){await edit(page).locator(':scope > summary').click();await expect(page.getByLabel('Evidence snapshot',{exact:true})).toBeEnabled();}
async function pickSnapshot(page:any){await page.getByLabel('Evidence snapshot',{exact:true}).selectOption(snapshotId);await expect(edit(page)).toContainText('Selections stay fixed.');}

test('eight domains and a bounded agenda stay on one page, preparation is not runtime completion',async({page})=>{
  const f=fixture();await mount(page,f);
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(8);
  await expect(page.locator('[data-pm-fu-agenda] ol > li')).toHaveCount(1);
  await expect(section(page)).toContainText('No AI Follow-up, scope change or work execution occurred');
  await expect(page.locator('[data-pm-fu-domain=finance]')).toContainText('Unresolved');
  await expect(page.locator('[data-pm-fu-domain=finance]')).toContainText('User confirmed');
  await expect(page.locator('[data-pm-fu-domain=finance]')).toContainText('The accepted plan remains unchanged');
  expect(await edit(page).getAttribute('open')).toBeNull();await expect(page.getByRole('tab')).toHaveCount(0);
  await page.getByText('Comparison with previous preparation',{exact:true}).click();
  await expect(section(page)).toContainText('No longer selected — resolution not assessed');
  expect(f.state.writes).toHaveLength(0);
});

test('empty agenda does not invent questions for the unknown domains',async({page})=>{
  const f=fixture();f.state.rows.at(-1).document.agenda=[];await mount(page,f);
  await expect(section(page)).toContainText('Unknown fields do not create questions');
  await expect(page.locator('[data-pm-fu-domain=schedule]')).toContainText('remains unknown');
});

test('history pages contain only metadata and selecting an older intake performs the exact get',async({page})=>{
  const f=fixture();await mount(page,f);await page.getByText('Preparation history',{exact:true}).click();
  await page.getByRole('button',{name:'Load more preparation history',exact:true}).click();
  await page.getByLabel('Saved preparation',{exact:true}).selectOption(intakeId(3));
  await expect.poll(()=>f.state.reads.some((p:string)=>p.endsWith('/follow-ups/'+intakeId(3)))).toBe(true);
  await expect(page.getByLabel('Saved preparation',{exact:true})).toHaveValue(intakeId(3));
  await page.getByLabel('Saved preparation',{exact:true}).selectOption(intakeId(1));
  await expect(section(page).getByText('Preparation 1',{exact:true})).toBeVisible();
  await page.getByLabel('Saved preparation',{exact:true}).selectOption('');
  await expect(section(page).getByText('Preparation 3',{exact:true})).toBeVisible();
});

test('selects fixed governance, thread and original meeting result without exposing JSON forms',async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);await pickSnapshot(page);
  await page.getByText('Additional sources (optional)',{exact:true}).click();
  await page.getByLabel('Discussion inputs (optional)',{exact:true}).selectOption(threadId);
  await page.getByLabel('Compare with (optional)',{exact:true}).selectOption(intakeId(1));
  await page.getByRole('checkbox',{name:/Recovery reliability assumption/}).check();
  await page.getByLabel('Choose a completed meeting',{exact:true}).selectOption(meetingId);
  await page.getByRole('button',{name:'Load more result history',exact:true}).click();
  await page.getByLabel('Fixed follow-up result',{exact:true}).selectOption(resultId);
  await page.getByRole('button',{name:'Select this follow-up',exact:true}).click();
  await page.getByRole('button',{name:'Save discussion preparation',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(1);
  const request=f.state.writes[0].body;
  expect(request.params).toEqual({snapshot_id:snapshotId,thread_id:threadId,previous_intake_id:intakeId(1),governance_revision_ids:[governanceId],
    meeting_follow_up_refs:[{completion_id:completionId,follow_up_id:'recovery',result_event_id:resultId}]});
  expect(request.preconditions).toEqual({expected_version:3,expected_plan_version:2,expected_thread_version:5});
  await expect(section(page)).toContainText('Preparation 4');
  await expect(page.locator('textarea')).toHaveCount(0);
});

test('lost reply retains the same original request and key through refresh and recovery',async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);await pickSnapshot(page);f.state.lost=true;
  await page.getByRole('button',{name:'Save discussion preparation',exact:true}).click();
  await expect(edit(page)).toContainText('The original reply was lost');
  await expect(page.getByLabel('Evidence snapshot',{exact:true})).toBeDisabled();
  const original=structuredClone(f.state.writes[0]);f.state.plan=3;
  await refresh(page);await page.getByRole('button',{name:'Check original request',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(2);
  expect(f.state.writes[1].key).toBe(original.key);expect(f.state.writes[1].body).toEqual(original.body);
  expect(f.state.ops.size).toBe(1);await expect(section(page)).toContainText('Preparation 4');
});

test('original ambiguous request survives remount without a replacement key',async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);await pickSnapshot(page);f.state.lost=true;
  await page.getByRole('button',{name:'Save discussion preparation',exact:true}).click();await expect(edit(page)).toContainText('The original reply was lost');
  const original=structuredClone(f.state.writes[0]);await mount(page,f);await edit(page).locator(':scope > summary').click();
  await page.getByRole('button',{name:'Check original request',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(2);expect(f.state.writes[1].key).toBe(original.key);expect(f.state.writes[1].body).toEqual(original.body);
  expect(f.state.ops.size).toBe(1);
});

test('a refreshed head preserves draft selections and needs explicit version rebase',async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);await pickSnapshot(page);f.state.head=4;f.state.plan=3;
  await refresh(page);await expect(page.getByLabel('Evidence snapshot',{exact:true})).toHaveValue(snapshotId);
  await expect(edit(page)).toContainText('Versions changed');
  await expect(page.getByRole('button',{name:'Save discussion preparation',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Use current versions with these selections',exact:true}).click();
  await page.getByRole('button',{name:'Save discussion preparation',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.preconditions).toEqual({expected_version:4,expected_plan_version:3});
});

for(const failure of [403,503])test(`read failure ${failure} clears all facts and source names while keeping the draft`,async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);await pickSnapshot(page);f.state.failRead=failure;
  await refresh(page);await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);
  await expect(section(page)).not.toContainText('Members can recover access');
  await expect(section(page)).not.toContainText('Recovery reliability assumption');
  expect(await page.evaluate(()=>Object.values(localStorage).some(value=>value.includes('pmsnap_')))).toBe(true);
  f.state.failRead=null;await page.getByRole('button',{name:'Retry Follow-up',exact:true}).click();
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(8);
  await expect(page.getByLabel('Evidence snapshot',{exact:true})).toHaveValue(snapshotId);
});

test('withdrawn selected source clears the entire saved intake view, not just the chooser',async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);f.state.failSource='/snapshots/'+snapshotId;
  await page.getByLabel('Evidence snapshot',{exact:true}).selectOption(snapshotId);
  await expect(section(page)).toContainText('Selected source access withdrawn');
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);
  await expect(section(page)).not.toContainText('The first paid tier price');
});

test('reader role disables writing and actor change removes the former actor’s view and editor',async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);await pickSnapshot(page);f.state.role='reader';
  await refresh(page);await expect(page.getByRole('button',{name:'Save discussion preparation',exact:true})).toBeDisabled();
  await expect(edit(page)).toContainText('requires current workspace write access');
  f.state.actor='other-person';f.state.role='owner';await refresh(page);
  await expect(section(page)).toContainText('signed-in actor changed');await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);
  await expect(page.locator('[data-pm-action="pm.follow_up.prepare"]')).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
});

for(const mismatch of ['wrongScope','wrongDetail','nonAdvancing'])test(`scope check rejects ${mismatch} and clears stale facts`,async({page})=>{
  const f=fixture();await mount(page,f);f.state[mismatch]=true;await refresh(page);
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);await expect(section(page)).toContainText('does not match this workspace');
});

test('history failure clears facts and does not skip its failed page on retry',async({page})=>{
  const f=fixture();await mount(page,f);await page.getByText('Preparation history',{exact:true}).click();f.state.failMore=503;
  await page.getByRole('button',{name:'Load more preparation history',exact:true}).click();
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);f.state.failMore=null;
  await page.getByRole('button',{name:'Retry Follow-up',exact:true}).click();
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(8);expect(f.state.reads.some((p:string)=>p.includes('follow-ups?after=0'))).toBe(true);
});

test('a late source read cannot restore facts after a newer permission failure',async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);f.state.holdPath='/snapshots/'+snapshotId;
  await page.getByLabel('Evidence snapshot',{exact:true}).selectOption(snapshotId);await expect.poll(()=>f.state.held.length).toBe(1);
  f.state.failRead=403;await refresh(page);f.state.held.shift()();
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);await expect(section(page)).toContainText('Workspace sources unavailable');
});

test('deselecting a meeting ignores its late original completion read',async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);await page.getByText('Additional sources (optional)',{exact:true}).click();
  f.state.holdPath='/meetings/'+meetingId;
  await page.getByLabel('Choose a completed meeting',{exact:true}).selectOption(meetingId);await expect.poll(()=>f.state.held.length).toBe(1);
  await page.getByLabel('Choose a completed meeting',{exact:true}).selectOption('');f.state.held.shift()();
  await expect(page.getByLabel('Fixed follow-up result',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Select this follow-up',exact:true})).toHaveCount(0);
});

test('empty workspace stays usable and literal input never becomes markup',async({page})=>{
  const f=fixture();f.state.rows.at(-1).document.knowledge.scope.baseline_inputs[0].value='<img src=x onerror=alert(1)> literal';
  await mount(page,f);await expect(section(page)).toContainText('<img src=x onerror=alert(1)> literal');await expect(section(page).locator('img')).toHaveCount(0);
  f.state.empty=true;await refresh(page);await expect(section(page)).toContainText('No Follow-up preparation has been saved');await openEditor(page);
});

for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`Follow-up layout ${locale} ${width}`,async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));await page.setViewportSize({width,height:900});
  const f=fixture();await mount(page,f,{locale});
  await expect(section(page).locator('h2')).toHaveText(locale==='en'?'Follow-up preparation':'Follow-up 討論準備');
  const output=process.env.BATC_PM_FOLLOW_UP_SCREENSHOTS||info.outputPath('screenshots');await mkdir(output,{recursive:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:`${output}/follow-up-${locale}-${width}.png`,fullPage:true});
  await edit(page).locator(':scope > summary').click();
  await expect(page.getByLabel(locale==='en'?'Evidence snapshot':'證據快照',{exact:true})).toBeEnabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(await section(page).innerText()).not.toContain('pm_fu_');expect(errors).toEqual([]);
  if(width===390)expect(await edit(page).locator(':scope > summary').evaluate((node:any)=>node.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await page.screenshot({path:`${output}/follow-up-editor-${locale}-${width}.png`,fullPage:true});
});

test('dark theme retains tokens and keyboard access',async({page},info)=>{
  await page.emulateMedia({colorScheme:'dark'});const f=fixture();await mount(page,f);
  await edit(page).locator(':scope > summary').focus();await page.keyboard.press('Enter');await expect(page.getByLabel('Evidence snapshot',{exact:true})).toBeEnabled();
  const output=process.env.BATC_PM_FOLLOW_UP_SCREENSHOTS||info.outputPath('screenshots');await mkdir(output,{recursive:true});await page.screenshot({path:`${output}/follow-up-dark.png`,fullPage:true});
});
