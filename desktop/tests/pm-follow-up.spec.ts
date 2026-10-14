async function openRead(node:any) {await node.first().evaluate((el:Element)=>{for(let p=(el.tagName==='SUMMARY'?el.parentElement?.parentElement:el.parentElement);p;p=p.parentElement)if(p instanceof HTMLDetailsElement)p.open=true;});await node.first().click();}
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {fixture,mount,intake,intakeId,workspaceId,snapshotId,threadId,governanceId,meetingId,completionId,resultId} from './pm-follow-up-fixture.ts';

const section=(page:any)=>page.locator('[data-pm-follow-up]');
const edit=(page:any)=>page.locator('[data-pm-fu-editor]');
const refresh=(page:any)=>page.evaluate(()=>(window as any).refreshFollowUp());
async function openEditor(page:any){if (!await edit(page).isVisible()) await page.locator('[data-pm-follow-up-prepare]').click();await expect(page.getByLabel('Evidence snapshot',{exact:true})).toBeEnabled();}
async function pickSnapshot(page:any){await page.getByLabel('Evidence snapshot',{exact:true}).selectOption(snapshotId);await expect(edit(page)).toContainText('Selections stay fixed.');}

test('recorded domains and a bounded agenda stay on one page, preparation is not runtime completion',async({page})=>{
  const f=fixture();await mount(page,f);
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(3);
  await expect(page.locator('[data-pm-fu-agenda] ol > li')).toHaveCount(1);
  await expect(section(page)).toContainText('Preparing saves the selected sources. It does not adopt scope changes or dispatch work.');
  await expect(page.locator('[data-pm-fu-domain=finance]')).toContainText('Unresolved');
  await expect(page.locator('[data-pm-fu-domain=finance]')).toContainText('User confirmed');
  await expect(page.locator('[data-pm-fu-domain=finance]')).toContainText('The accepted plan remains unchanged');
  expect(await edit(page).getAttribute('open')).toBeNull();await expect(page.getByRole('tab')).toHaveCount(0);
  await openRead(page.getByText('Changes',{exact:true}));
  await expect(section(page)).toContainText('Omitted sources');
  await expect(section(page)).toContainText('A source no longer selected in this preparation has not been declared resolved.');
  expect(f.state.writes).toHaveLength(0);
});

test('empty agenda does not invent questions for the unknown domains',async({page})=>{
  const f=fixture();f.state.rows.at(-1).document.agenda=[];await mount(page,f);
  await expect(section(page)).toContainText('Unknown fields do not create questions');
  await expect(page.locator('[data-pm-fu-domain=schedule]')).toHaveCount(0); await expect(page.locator('[data-pm-fu-missing]')).toHaveText('No baseline input: Governance, Schedule, Stakeholders, Resources, Quality');
});

test('withdrawn latest retains only newly authorized readable history and permits a new preparation',async({page},info)=>{
  const f=fixture();await mount(page,f);f.state.hiddenIntakes.add(intakeId(3));await refresh(page);
  await expect(section(page)).toContainText('The latest preparation’s original sources are unavailable');
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);
  await expect(section(page)).not.toContainText('The first paid tier price');
  const output=process.env.BATC_PM_FOLLOW_UP_SCREENSHOTS||info.outputPath('screenshots');await mkdir(output,{recursive:true});
  for(const width of [390,768,1440]){await page.setViewportSize({width,height:900});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`${output}/follow-up-unavailable-${width}.png`,fullPage:true});}
  const authority=f.state.reads.lastIndexOf(`/pm/workspaces/${workspaceId}`);
  expect(authority).toBeGreaterThan(-1);expect(f.state.reads.slice(authority+1)).toContain(`/pm/workspaces/${workspaceId}/follow-ups?after=0&limit=2`);
  await openRead(page.getByText('Preparation history',{exact:true}));
  await expect(page.getByLabel('Saved preparation',{exact:true})).toHaveValue('');
  await expect(page.getByLabel('Saved preparation',{exact:true}).locator('option')).toHaveCount(3);
  await page.getByLabel('Saved preparation',{exact:true}).selectOption(intakeId(1));
  await expect(section(page).getByText('Preparation 1',{exact:true})).toBeVisible();
  await page.getByLabel('Saved preparation',{exact:true}).selectOption('');
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);
  await openEditor(page);await pickSnapshot(page);await page.getByRole('button',{name:'Save discussion preparation',exact:true}).click();
  await expect(section(page)).toContainText('Preparation 4');expect(f.state.writes[0].body.preconditions.expected_version).toBe(3);
});

test('withdrawn historical selection is not replaced by the current intake',async({page})=>{
  const f=fixture();await mount(page,f);await openRead(page.getByText('Preparation history',{exact:true}));
  await page.getByLabel('Saved preparation',{exact:true}).selectOption(intakeId(1));
  await expect(section(page).getByText('Preparation 1',{exact:true})).toBeVisible();f.state.hiddenIntakes.add(intakeId(1));await refresh(page);
  await expect(section(page)).toContainText('This preparation’s original sources are unavailable');
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);
  await expect(page.getByLabel('Saved preparation',{exact:true})).toHaveValue(intakeId(1));
  await expect(section(page).getByText('Preparation 3',{exact:true})).toHaveCount(0);
  await page.getByLabel('Saved preparation',{exact:true}).selectOption(intakeId(2));
  await expect(section(page).getByText('Preparation 2',{exact:true})).toBeVisible();
});

test('history can scan past a wholly hidden page without treating it as empty',async({page})=>{
  const f=fixture();f.state.rows.push(intake(4));f.state.head=4;[1,2,4].forEach(n=>f.state.hiddenIntakes.add(intakeId(n)));
  await mount(page,f);await openRead(page.getByText('Preparation history',{exact:true}));
  await expect(page.getByLabel('Saved preparation',{exact:true}).locator('option')).toHaveCount(1);
  await page.getByRole('button',{name:'Load more',exact:true}).click();
  await page.getByLabel('Saved preparation',{exact:true}).selectOption(intakeId(3));
  await expect(section(page).getByText('Preparation 3',{exact:true})).toBeVisible();expect(f.state.writes).toHaveLength(0);
});

for(const reason of ['membership','actor','transient'])test(`unavailable intake cannot restore metadata after ${reason} authority failure`,async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);await pickSnapshot(page);f.state.hiddenIntakes.add(intakeId(3));
  if(reason==='actor')f.state.wrongWorkspaceActor=true;else f.state.failWorkspace=reason==='membership'?404:503;
  await refresh(page);await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);
  await expect(page.getByLabel('Saved preparation',{exact:true}).locator('option')).toHaveCount(0);await expect(edit(page)).toBeHidden();
  expect(await page.evaluate(()=>Object.values(localStorage).some(value=>value.includes('pmsnap_')))).toBe(true);
  expect(f.state.writes).toHaveLength(0);
});

for(const failure of [403,404,503])test(`generic preparation failure ${failure} does not take source-only recovery`,async({page})=>{
  const f=fixture();await mount(page,f);f.state.failIntake=failure;await refresh(page);
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);await expect(edit(page)).toBeHidden();
  await expect(page.getByLabel('Saved preparation',{exact:true}).locator('option')).toHaveCount(0);
  expect(f.state.reads).not.toContain(`/pm/workspaces/${workspaceId}`);
});

test('source recovery uses the newly checked reader role instead of stale parent write permission',async({page})=>{
  const f=fixture();f.state.hiddenIntakes.add(intakeId(3));f.state.workspaceRole='reader';await mount(page,f);
  await expect(section(page).locator('[data-pm-panel-readonly]')).toHaveText('Read only: needs operate permission');
  await expect(edit(page).locator('button[type=submit]')).toBeDisabled();
  f.state.workspaceRole='owner';await refresh(page);await openEditor(page);await expect(page.getByLabel('Evidence snapshot',{exact:true})).toBeEnabled();
  expect(f.state.writes).toHaveLength(0);
});

test('a late authority recovery cannot restore an earlier readable view after a new denial',async({page})=>{
  const f=fixture();await mount(page,f);f.state.hiddenIntakes.add(intakeId(3));
  // Hold the authority read, not the earlier parallel intake reads.
  let release:any;const request=f.dispatch;f.dispatch=async input=>input.path===`/pm/workspaces/${workspaceId}`
    ?new Promise(resolve=>{release=async()=>resolve(await request(input));}):request(input);
  const pending=refresh(page);await expect.poll(()=>Boolean(release)).toBe(true);f.state.failRead=403;await refresh(page);
  f.state.failRead=null;await release();await pending;
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);await expect(edit(page)).toBeHidden();
  await expect(page.getByLabel('Saved preparation',{exact:true}).locator('option')).toHaveCount(0);
});

test('a withdrawn newer intake does not replace an ambiguous original request or its CAS',async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);await pickSnapshot(page);f.state.lost=true;
  await page.getByRole('button',{name:'Save discussion preparation',exact:true}).click();await expect(edit(page)).toContainText('The original reply was lost');
  const original=structuredClone(f.state.writes[0]);f.state.rows.push(intake(5));f.state.head=5;f.state.hiddenIntakes.add(intakeId(5));await refresh(page);
  await expect(section(page)).toContainText('The latest preparation’s original sources are unavailable');
  await page.getByRole('button',{name:'Check original request',exact:true}).click();
  await expect(section(page)).toContainText('Preparation 4');expect(f.state.writes.at(-1).key).toBe(original.key);
  expect(f.state.writes.at(-1).body).toEqual(original.body);expect(f.state.ops.size).toBe(1);
});

test('unavailable latest preserves an unsent draft until explicit CAS rebase',async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);await pickSnapshot(page);
  f.state.rows.push(intake(4));f.state.head=4;f.state.plan=3;f.state.hiddenIntakes.add(intakeId(4));await refresh(page);
  await expect(page.getByLabel('Evidence snapshot',{exact:true})).toHaveValue(snapshotId);
  await expect(page.getByRole('button',{name:'Save discussion preparation',exact:true})).toBeDisabled();
  const saved=await page.evaluate(()=>JSON.parse(Object.values(localStorage).find(value=>value.includes('pmsnap_'))!));
  expect(JSON.parse(saved.values._baseline)).toMatchObject({expected_version:3,expected_plan_version:2});
  await page.getByRole('button',{name:'Use current versions',exact:true}).click();
  await page.getByRole('button',{name:'Save discussion preparation',exact:true}).click();
  await expect(section(page)).toContainText('Preparation 5');expect(f.state.writes[0].body.preconditions).toEqual({expected_version:4,expected_plan_version:3});
});

for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`snapshot omissions remain visible with an empty agenda ${locale} ${width}`,async({page},info)=>{
  const f=fixture();f.state.rows.at(-1).document.agenda=[];
  f.state.rows.at(-1).document.coverage.snapshot={projects_truncated:true,work_truncated:true,evidence_truncated:true,bytes_truncated:true};
  await page.setViewportSize({width,height:900});
  await mount(page,f,{locale});const warning=page.locator('[data-pm-fu-partial]');await expect(section(page).locator('[data-status=partial]').first()).toBeVisible();await warning.evaluate(el=>{for(let p=(el.tagName==='SUMMARY'?el.parentElement?.parentElement:el.parentElement);p;p=p.parentElement)if(p instanceof HTMLDetailsElement)p.open=true;});await expect(warning).toBeVisible();
  await expect(warning.locator('li')).toHaveCount(4);await expect(warning).toContainText(locale==='en'?'The agenda does not assess those omitted records':'議程並未評估那些省略的紀錄');
  const output=process.env.BATC_PM_FOLLOW_UP_SCREENSHOTS||info.outputPath('screenshots');await mkdir(output,{recursive:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:`${output}/follow-up-partial-${locale}-${width}.png`,fullPage:true});
  expect(f.state.writes).toHaveLength(0);
  await openRead(page.getByText(locale==='en'?'Preparation history':'討論準備歷史',{exact:true}));
  await page.getByLabel(locale==='en'?'Saved preparation':'已保存的討論準備',{exact:true}).selectOption(intakeId(1));
  await expect(warning).toHaveCount(0);
});

test('history pages contain only metadata and selecting an older intake performs the exact get',async({page})=>{
  const f=fixture();await mount(page,f);await openRead(page.getByText('Preparation history',{exact:true}));
  await page.getByRole('button',{name:'Load more',exact:true}).click();
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
  await openRead(page.getByText('Additional sources (optional)',{exact:true}));
  await page.getByLabel('Discussion inputs (optional)',{exact:true}).selectOption(threadId);
  await page.getByLabel('Compare with (optional)',{exact:true}).selectOption(intakeId(1));
  await page.getByRole('checkbox',{name:/Recovery reliability assumption/}).check();
  await page.getByLabel('Completed meeting',{exact:true}).selectOption(meetingId);
  await page.getByRole('button',{name:'More results',exact:true}).click();
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
  const original=structuredClone(f.state.writes[0]);await mount(page,f);await expect(edit(page)).toBeVisible();
  await expect(page.locator('[data-pm-follow-up-prepare]')).toHaveAttribute('aria-expanded','true');
  await page.getByRole('button',{name:'Check original request',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(2);expect(f.state.writes[1].key).toBe(original.key);expect(f.state.writes[1].body).toEqual(original.body);
  expect(f.state.ops.size).toBe(1);
});

test('a refreshed head preserves draft selections and needs explicit version rebase',async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);await pickSnapshot(page);f.state.head=4;f.state.plan=3;
  await refresh(page);await expect(page.getByLabel('Evidence snapshot',{exact:true})).toHaveValue(snapshotId);
  await expect(edit(page)).toContainText('Versions changed');
  await expect(page.getByRole('button',{name:'Save discussion preparation',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Use current versions',exact:true}).click();
  await page.getByRole('button',{name:'Save discussion preparation',exact:true}).click();
  await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.preconditions).toEqual({expected_version:4,expected_plan_version:3});
});

for(const failure of [403,503])test(`read failure ${failure} clears all facts and source names while keeping the draft`,async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);await pickSnapshot(page);f.state.failRead=failure;
  await refresh(page);await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);
  await expect(section(page)).not.toContainText('Members can recover access');
  await expect(section(page)).not.toContainText('Recovery reliability assumption');
  expect(await page.evaluate(()=>Object.values(localStorage).some(value=>value.includes('pmsnap_')))).toBe(true);
  f.state.failRead=null;await page.getByRole('button',{name:'Retry preparation',exact:true}).click();
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(3);
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
  await refresh(page);await expect(edit(page)).toBeHidden();await expect(edit(page).locator('button[type=submit]')).toBeDisabled();
  await expect(section(page).locator('[data-pm-panel-readonly]')).toHaveText('Read only: needs operate permission');
  f.state.actor='other-person';f.state.role='owner';await refresh(page);
  await expect(section(page)).toContainText('signed-in actor changed');await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);
  await expect(page.locator('[data-pm-action="pm.follow_up.prepare"]')).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
});

for(const mismatch of ['wrongScope','wrongDetail','nonAdvancing'])test(`scope check rejects ${mismatch} and clears stale facts`,async({page})=>{
  const f=fixture();await mount(page,f);f.state[mismatch]=true;await refresh(page);
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);await expect(section(page)).toContainText('does not match this workspace');
});

test('history failure clears facts and does not skip its failed page on retry',async({page})=>{
  const f=fixture();await mount(page,f);await openRead(page.getByText('Preparation history',{exact:true}));f.state.failMore=503;
  await page.getByRole('button',{name:'Load more',exact:true}).click();
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);f.state.failMore=null;
  await page.getByRole('button',{name:'Retry preparation',exact:true}).click();
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(3);expect(f.state.reads.some((p:string)=>p.includes('follow-ups?after=0'))).toBe(true);
});

test('a late source read cannot restore facts after a newer permission failure',async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);f.state.holdPath='/snapshots/'+snapshotId;
  await page.getByLabel('Evidence snapshot',{exact:true}).selectOption(snapshotId);await expect.poll(()=>f.state.held.length).toBe(1);
  f.state.failRead=403;await refresh(page);f.state.held.shift()();
  await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(0);await expect(section(page)).toContainText('Workspace sources unavailable');
});

test('deselecting a meeting ignores its late original completion read',async({page})=>{
  const f=fixture();await mount(page,f);await openEditor(page);await openRead(page.getByText('Additional sources (optional)',{exact:true}));
  f.state.holdPath='/meetings/'+meetingId;
  await page.getByLabel('Completed meeting',{exact:true}).selectOption(meetingId);await expect.poll(()=>f.state.held.length).toBe(1);
  await page.getByLabel('Completed meeting',{exact:true}).selectOption('');f.state.held.shift()();
  await expect(page.getByLabel('Fixed follow-up result',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Select this follow-up',exact:true})).toHaveCount(0);
});

test('empty workspace stays usable and literal input never becomes markup',async({page})=>{
  const f=fixture();f.state.rows.at(-1).document.knowledge.scope.baseline_inputs[0].value='<img src=x onerror=alert(1)> literal';
  await mount(page,f);await expect(section(page)).toContainText('<img src=x onerror=alert(1)> literal');await expect(section(page).locator('img')).toHaveCount(0);
  f.state.empty=true;await refresh(page);await expect(section(page)).toContainText('No saved preparation');await openEditor(page);
});

for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`Follow-up layout ${locale} ${width}`,async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));await page.setViewportSize({width,height:900});
  const f=fixture();await mount(page,f,{locale});
  await expect(section(page).locator('.pm-heading > button').first()).toHaveText(locale==='en'?'Prepare a discussion':'準備一次討論');await expect(section(page).locator('h2')).toHaveCount(0);
  const output=process.env.BATC_PM_FOLLOW_UP_SCREENSHOTS||info.outputPath('screenshots');await mkdir(output,{recursive:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:`${output}/follow-up-${locale}-${width}.png`,fullPage:true});
  if (!await edit(page).isVisible()) await page.locator('[data-pm-follow-up-prepare]').click();
  await expect(page.getByLabel(locale==='en'?'Evidence snapshot':'證據快照',{exact:true})).toBeEnabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(await section(page).innerText()).not.toContain('pm_fu_');expect(errors).toEqual([]);
  if(width===390)expect(await page.locator('[data-pm-follow-up-prepare]').evaluate((node:any)=>node.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await page.screenshot({path:`${output}/follow-up-editor-${locale}-${width}.png`,fullPage:true});
});

test('dark theme retains tokens and keyboard access',async({page},info)=>{
  await page.emulateMedia({colorScheme:'dark'});const f=fixture();await mount(page,f);
  await page.locator('[data-pm-follow-up-prepare]').focus();await page.keyboard.press('Enter');await expect(page.getByLabel('Evidence snapshot',{exact:true})).toBeEnabled();
  const output=process.env.BATC_PM_FOLLOW_UP_SCREENSHOTS||info.outputPath('screenshots');await mkdir(output,{recursive:true});await page.screenshot({path:`${output}/follow-up-dark.png`,fullPage:true});
});
