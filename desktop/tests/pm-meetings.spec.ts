async function openRead(node:any) {await node.first().evaluate((el:Element)=>{for(let p=(el.tagName==='SUMMARY'?el.parentElement?.parentElement:el.parentElement);p;p=p.parentElement)if(p instanceof HTMLDetailsElement)p.open=true;});await node.first().click();}
import {test,expect,type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {action,meetingFixture,mount,boot,openAction,reason,snapshotId} from './pm-meetings-fixture';

async function saveDraft(page:Page,kind='planning') {
  await openAction(page,'draft');const editor=action(page,'draft');
  if(kind==='planning'){
    await editor.getByLabel('Why this Sprint matters',{exact:true}).fill('Members need reliable access');
    await editor.getByLabel('Delivery approach',{exact:true}).fill('Inspect the fixed change and review its result');
    await editor.getByLabel('Capacity and availability discussed',{exact:true}).fill('One developer can review this week');
  }else if(kind==='review'){
    await editor.getByLabel('Goal impact',{exact:true}).fill('Tests support the change; actual usage remains unknown');
    await editor.getByLabel('Feedback (one point per line)',{exact:true}).fill('The reset instructions are easier to follow');
    await editor.getByLabel('Next direction (one point per line)',{exact:true}).fill('Maintain the current direction and observe');
  }else{
    await editor.getByLabel('Process and collaboration observations (one per line)',{exact:true}).fill('Written handoffs helped avoid duplicate work');
    await editor.getByLabel('Previous improvements',{exact:true}).fill('There is no measured result yet');
    await editor.getByLabel('If adopting no improvement, explain why',{exact:true}).fill('The current approach is useful; no new change is warranted');
  }
  await reason(page,'draft');await editor.getByRole('button',{name:'Save discussion draft',exact:true}).click();
  await expect(page.locator('summary').filter({hasText:'Saved discussion draft'})).toBeVisible();
}
async function saveNote(page:Page,waitSaved=true) {
  await openAction(page,'note');await action(page,'note').getByLabel('Discussion note',{exact:true}).fill('We discussed results, uncertainty and the next direction together.');
  await reason(page,'note');await action(page,'note').getByRole('button',{name:'Save discussion note',exact:true}).click();
  if(waitSaved)await expect(page.locator('[data-pm-meetings]')).toContainText('We discussed results, uncertainty and the next direction together.');
}
async function complete(page:Page) {
  await openAction(page,'complete');await action(page,'complete').getByLabel('What was discussed and concluded',{exact:true}).fill('We reviewed the evidence and agreed to observe the existing direction.');
  await reason(page,'complete');await action(page,'complete').getByRole('button',{name:'Complete discussion',exact:true}).click();
}

test('fixed source, Goal and quality stay in the meeting without tabs or writes',async({page})=>{
  const f=meetingFixture();await mount(page,f);
  await expect(page.locator('[data-pm-meetings]')).toContainText('Members regain access without losing their account');
  await openRead(page.getByText('Details',{exact:true}));
  await expect(page.locator('[data-pm-meetings]')).toContainText('Relevant tests and actual behavior are checked');
  await expect(page.locator('[data-pm-meetings]')).toContainText('DoD and improvement effects are unassessed');
  await expect(page.getByRole('tab')).toHaveCount(0);expect(f.state.writes).toEqual([]);
});
for(const kind of ['planning','review','retro'])test(`${kind} draft and explicit completion preserve distinct meanings`,async({page})=>{
  const f=meetingFixture(kind);await mount(page,f);await saveNote(page);await saveDraft(page,kind);
  expect(f.state.meetings[0].meeting.status).toBe('draft');await expect(page.locator('[data-pm-meeting-completion]')).toHaveCount(0);
  const output=f.state.writes.find((row:any)=>row.body.action==='pm.meeting.draft').body.params.output;
  if(kind==='planning'){expect(output).not.toHaveProperty('what');expect(output).not.toHaveProperty('how');}
  if(kind==='review')expect(output.next_steps).toEqual(['Maintain the current direction and observe']);
  if(kind==='retro'){expect(output.no_improvement_rationale).toContain('no new change');expect(f.state.meetings[0].draft.document.follow_ups).toEqual([]);}
  const revision=f.state.meetings[0].draft.revision_id;await complete(page);
  await expect(page.locator('[data-pm-meetings]')).toContainText('Attendance and quality are not independently verified');
  expect(f.state.writes.at(-1).body.params.draft_revision_id).toBe(revision);
  expect(f.state.writes.map((row:any)=>row.body.action)).toEqual(['pm.meeting.note','pm.meeting.draft','pm.meeting.complete']);
});
test('a lost completion reply recovers the exact request after completed state and reload',async({page})=>{
  const f=meetingFixture();await mount(page,f);await saveNote(page);await saveDraft(page);f.state.lost=true;await complete(page);
  await expect(action(page,'complete')).toContainText('Reply unavailable');expect(f.state.meetings[0].meeting.status).toBe('completed');
  const original=structuredClone(f.state.writes.at(-1));await page.reload();await boot(page);
  await action(page,'complete').getByRole('button',{name:'Check original request',exact:true}).click();
  await expect.poll(() => f.state.writes.length).toBe(4);
  expect(f.state.writes.at(-1)).toEqual(original);expect(f.state.ops.size).toBe(3);
  await expect(action(page,'complete').getByRole('button',{name:'Check original request',exact:true})).toHaveCount(0);
  await expect(page.getByLabel('Meeting',{exact:true})).toBeEnabled();
  await openRead(page.locator('[data-pm-meeting-completion]').locator('..').locator(':scope > summary'));
  await expect(page.locator('[data-pm-meeting-completion]')).toBeVisible();
});
test('saving a note preserves another open draft and requires an explicit version refresh',async({page})=>{
  const f=meetingFixture();await mount(page,f);await openAction(page,'draft');
  const editor=action(page,'draft'),field=editor.getByLabel('Why this Sprint matters',{exact:true});
  await field.fill('Keep this unfinished reasoning');await saveNote(page);
  await expect(field).toBeVisible();await expect(field).toHaveValue('Keep this unfinished reasoning');
  await expect(editor.locator('button[type=submit]')).toBeDisabled();
  await expect(page.locator('[data-pm-meetings]')).toContainText('Update baseline');
  expect(f.state.writes.map((row:any)=>row.body.action)).toEqual(['pm.meeting.note']);
});
test('current membership and designated owner govern completion even after reload',async({page})=>{
  const f=meetingFixture();await mount(page,f);await saveDraft(page);
  f.state.role='contributor';await page.evaluate(()=>(window as any).refreshMeetings());
  await expect(action(page,'complete')).toHaveCount(0);
  f.state.actor='developer';await page.reload();await boot(page);
  await expect(action(page,'complete')).toHaveCount(0);expect(f.state.writes).toHaveLength(1);
});
test('new source versions preserve typed text and fixed context until explicit update',async({page})=>{
  const f=meetingFixture();await mount(page,f);await openAction(page,'draft');const field=action(page,'draft').getByLabel('Why this Sprint matters',{exact:true});
  await field.fill('Preserve this reasoning');await page.evaluate(()=>(window as any).refreshMeetings());await expect(field).toBeFocused();
  f.state.sprints[0].version++;f.state.snapshots.push({...f.state.snapshots[0],snapshot_id:'pmsnap_'+'9'.repeat(32),title:'Later snapshot'});
  await page.evaluate(()=>(window as any).refreshMeetings());await expect(field).toHaveValue('Preserve this reasoning');
  await expect(action(page,'draft').locator('button[type=submit]')).toBeDisabled();expect(f.state.meetings[0].context.document.snapshot_ref.snapshot_id).toBe(snapshotId);
  await page.reload();await boot(page);await openAction(page,'draft');await expect(action(page,'draft').getByLabel('Why this Sprint matters',{exact:true})).toHaveValue('Preserve this reasoning');
  // Sprint changed, but the meeting's immutable context did not: page reload must not re-pin it.
  expect(f.state.meetings[0].context.document.sprint_version).toBe(4);expect(f.state.writes).toEqual([]);
  await openAction(page,'context.revise');await action(page,'context.revise').getByLabel('Saved project snapshot',{exact:true}).selectOption('pmsnap_'+'9'.repeat(32));
  await reason(page,'context.revise');
  if(await page.getByRole('button',{name:'Update baseline',exact:true}).isVisible())await page.getByRole('button',{name:'Update baseline',exact:true}).click();
  await openAction(page,'context.revise');await action(page,'context.revise').getByRole('button',{name:'Save context',exact:true}).click();
  await expect.poll(() => f.state.writes.length).toBe(1);
  expect(f.state.writes.at(-1).body.preconditions).toEqual({expected_version:1,expected_sprint_version:5});
  expect(f.state.meetings[0].context.document.snapshot_ref.snapshot_id).toBe('pmsnap_'+'9'.repeat(32));
});
test('normal controls retain stable decision and follow-up IDs across reload',async({page})=>{
  const f=meetingFixture('retro');await mount(page,f);await openAction(page,'draft');const draft=action(page,'draft');
  await draft.getByText('Improvements and follow-up',{exact:true}).click();await draft.getByRole('button',{name:'Add follow-up',exact:true}).click();
  await draft.getByLabel('Improvement or follow-up',{exact:true}).fill('Check handoff quality once next week');
  await draft.getByLabel('Responsible person',{exact:true}).selectOption('developer');
  await draft.getByLabel('Expected effect',{exact:true}).fill('Reduce repeated context questions');
  await draft.getByLabel('How the result will be checked',{exact:true}).fill('Review one real handoff');
  await draft.getByText('Decision proposals',{exact:true}).click();await draft.getByRole('button',{name:'Add decision proposal',exact:true}).click();
  await draft.getByLabel('Proposed decision',{exact:true}).fill('Keep current scope');await draft.getByLabel('Reason for the proposed decision',{exact:true}).fill('Current uncertainty does not justify expanding work');
  const before=await page.evaluate(()=>Object.values(localStorage).find((value:any)=>value.includes('Check handoff quality')));
  await page.reload();await boot(page);await openAction(page,'draft');await draft.getByText('Improvements and follow-up',{exact:true}).click();
  await expect(draft.getByLabel('Responsible person',{exact:true})).toHaveValue('developer');
  const after=await page.evaluate(()=>Object.values(localStorage).find((value:any)=>value.includes('Check handoff quality')));expect(after).toBe(before);
  await draft.getByLabel('Process and collaboration observations (one per line)',{exact:true}).fill('Context questions repeated');
  await draft.getByLabel('Previous improvements',{exact:true}).fill('No prior measured improvement');await reason(page,'draft');
  await draft.getByRole('button',{name:'Save discussion draft',exact:true}).click();
  const saved=f.state.meetings[0].draft.document;expect(saved.follow_ups[0]).toMatchObject({owner_actor:'developer',verification:'Review one real handoff'});
  expect(saved.follow_ups[0].id).toMatch(/^followup_[0-9a-f]{32}$/);expect(saved.decisions[0].id).toMatch(/^decision_[0-9a-f]{32}$/);
});
test('follow-up result is an unverified record on the original assignment',async({page})=>{
  const f=meetingFixture('retro'),detail=f.state.meetings[0];detail.meeting.status='completed';detail.meeting.completion_id='pmmcomplete_'+'1'.repeat(32);
  detail.completion={meeting_id:detail.meeting.meeting_id,completion_id:detail.meeting.completion_id,actor:'owner',document:{discussion_summary:'We chose one measurable improvement',decisions:[],follow_ups:[
    {id:'handoff',text:'Review one handoff',owner_actor:'developer',expected_effect:'Fewer context questions',verification:'Read one handoff'}]}};
  f.state.actor='developer';f.state.role='contributor';await mount(page,f);await openAction(page,'follow_up.record');
  await action(page,'follow_up.record').getByLabel('Reported status',{exact:true}).selectOption('reported_done');
  await action(page,'follow_up.record').getByLabel('Result and remaining uncertainty',{exact:true}).fill('The handoff was reviewed; impact remains unmeasured');
  await reason(page,'follow_up.record');await action(page,'follow_up.record').getByRole('button',{name:'Record follow-up result',exact:true}).click();
  await expect.poll(() => f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params).toMatchObject({follow_up_id:'handoff',status:'reported_done'});expect(f.state.writes[0].body.params).not.toHaveProperty('snapshot_id');
  await expect(action(page,'follow_up.record').getByLabel('Result and remaining uncertainty',{exact:true})).toHaveValue('');
  await expect(page.getByLabel('Meeting',{exact:true})).toBeEnabled();
  await openRead(page.getByText('History',{exact:true}));await openRead(page.getByText('Follow-up results',{exact:true}));await openRead(page.getByText('#1 · Developer',{exact:true}));
  await expect(page.locator('[data-pm-meetings]')).toContainText('Reported done — unverified');
  expect(detail.completion.document.follow_ups[0].owner_actor).toBe('developer');
});
test('history follows cursors and a revoked history read clears previously visible facts',async({page})=>{
  const f=meetingFixture();f.state.moreNotes=true;f.state.pagedSnapshots=true;await mount(page,f);
  expect(f.state.reads).toContain('/pm/workspaces/'+'ws_'+'1'.repeat(32)+'/snapshots?after=10&limit=50');
  await openRead(page.getByText('History',{exact:true}));await openRead(page.getByText('Discussion notes',{exact:true}));await page.getByRole('button',{name:'Load more',exact:true}).click();
  await openRead(page.getByText('#2 · Developer',{exact:true}));await expect(page.getByText('A second discussion note',{exact:true})).toBeVisible();
  f.state.failRead='revoked';await openRead(page.getByText('Context revisions',{exact:true}));await page.getByText('Context revisions',{exact:true}).locator('..').getByRole('button',{name:'Load more',exact:true}).click();
  await expect(page.locator('[data-pm-meetings]')).toContainText('Workspace access removed');
  expect(await page.locator('[data-pm-meetings]').innerText()).not.toContain('Members regain access');expect(f.state.writes).toEqual([]);
});
test('source choices keep exact IDs and render untrusted strings as text',async({page})=>{
  const f=meetingFixture();f.state.meetings[0].snapshot.evidence[0].summary='<img src=x onerror=alert(1)> literal evidence';await mount(page,f);
  await openAction(page,'note');await action(page,'note').getByText('Source references',{exact:true}).click();
  await action(page,'note').getByRole('checkbox',{name:/literal evidence/}).check();
  await action(page,'note').getByLabel('Discussion note',{exact:true}).fill('Evidence is incomplete');await reason(page,'note');
  await action(page,'note').getByRole('button',{name:'Save discussion note',exact:true}).click();
  await expect.poll(() => f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params.source_refs).toEqual([{kind:'evidence',id:'pme_'+'7'.repeat(32)}]);
  await expect(page.locator('[data-pm-meetings] img')).toHaveCount(0);
});
test('review outcomes outside a changed context remain visible until explicitly removed',async({page})=>{
  const f=meetingFixture('review');await mount(page,f);await openAction(page,'draft');
  await action(page,'draft').getByLabel('Observed result',{exact:true}).fill('Preserve the original result claim');
  const detail=f.state.meetings[0];detail.meeting.version++;detail.context.context_revision_id='pmmctx_'+'7'.repeat(32);detail.meeting.context_revision_id=detail.context.context_revision_id;
  detail.context.document.selected_items=[];await page.reload();await boot(page);await openAction(page,'draft');
  await expect(action(page,'draft').getByLabel('Observed result',{exact:true})).toHaveValue('Preserve the original result claim');
  await expect(action(page,'draft')).toContainText('Source outside the current context');await expect(action(page,'draft').locator('button[type=submit]')).toBeDisabled();
  await page.getByRole('button',{name:'Update baseline',exact:true}).click();await openAction(page,'draft');
  await expect(action(page,'draft').getByLabel('Observed result',{exact:true})).toHaveValue('Preserve the original result claim');
  await action(page,'draft').getByRole('button',{name:'Remove',exact:true}).click();await expect(action(page,'draft').getByLabel('Observed result',{exact:true})).toHaveCount(0);
  expect(f.state.writes).toHaveLength(0);
});
test('non-latest meeting selection survives reload with its original lost note request',async({page})=>{
  const f=meetingFixture(),first=f.state.meetings[0].meeting.meeting_id;f.addMeeting('retro','Later discussion');await mount(page,f);
  await page.getByLabel('Meeting',{exact:true}).selectOption(first);await expect(page.locator('[data-pm-meeting-selected]')).toHaveAttribute('data-pm-meeting-selected',first);
  await expect(page.getByLabel('Meeting',{exact:true})).toBeEnabled();f.state.lost=true;await saveNote(page,false);await expect(action(page,'note')).toContainText('Reply unavailable');
  const original=structuredClone(f.state.writes[0]);await page.reload();await boot(page);await expect(page.getByLabel('Meeting',{exact:true})).toHaveValue(first);
  await action(page,'note').getByRole('button',{name:'Check original request',exact:true}).click();
  await expect.poll(() => f.state.writes.length).toBe(2);
  expect(f.state.writes.at(-1)).toEqual(original);expect(f.state.ops.size).toBe(1);
});
test('zero cursor offers standalone history when detail budget includes no notes',async({page})=>{
  const f=meetingFixture();f.state.meetings[0].history_next_cursors.notes=0;await mount(page,f);
  await openRead(page.getByText('History',{exact:true}));await openRead(page.getByText('Discussion notes',{exact:true}));await page.getByRole('button',{name:'Load more',exact:true}).click();
  expect(f.state.reads.some((path:string)=>path.endsWith('/history/notes?after=0&limit=20'))).toBe(true);
  await openRead(page.getByText('#2 · Developer',{exact:true}));await expect(page.getByText('A second discussion note',{exact:true})).toBeVisible();
});
test('Review creation stays disabled before the Sprint has started',async({page})=>{
  const f=meetingFixture();f.state.sprints[0].started_at=null;f.state.sprints[0].status='planned';await mount(page,f);await openAction(page,'create');
  await action(page,'create').getByLabel('Discussion type',{exact:true}).selectOption('review');
  await expect(action(page,'create').locator('button[type=submit]')).toBeDisabled();expect(f.state.writes).toHaveLength(0);
});
test('a malformed page cursor or mismatched workspace prevents writes and clears facts',async({page})=>{
  const f=meetingFixture();await mount(page,f);await reason(page,'note','Keep this unsent reason');f.state.pagedSnapshots=true;f.state.badCursor=true;
  await page.evaluate(()=>(window as any).refreshMeetings());await expect(action(page,'note')).toHaveCount(0);
  expect(await page.locator('[data-pm-meetings]').innerText()).not.toContain('Members regain access');
  f.state.badCursor=false;f.state.wrongScope=true;await page.evaluate(()=>(window as any).refreshMeetings());
  await expect(action(page,'note')).toHaveCount(0);expect(f.state.writes).toHaveLength(0);
});
test('mismatched receipt and lost acknowledgement remain locked after scope revocation',async({page})=>{
  const f=meetingFixture();await mount(page,f);f.state.mismatch=true;await saveNote(page,false);
  await expect(action(page,'note')).toContainText('does not match');const original=structuredClone(f.state.writes[0]);
  const stored=()=>page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).filter(([key])=>key.includes('pm.meeting.note'))));const pending=await stored();
  f.state.manage=false;await page.evaluate(()=>(window as any).refreshMeetings());
  await expect(action(page,'note')).toHaveCount(0);expect(await stored()).toEqual(pending);expect(f.state.writes).toHaveLength(1);
  f.state.manage=true;f.state.mismatch=false;await page.evaluate(()=>(window as any).refreshMeetings());
  await action(page,'note').getByRole('button',{name:'Check original request',exact:true}).click();
  await expect.poll(() => f.state.writes.length).toBe(2);
  expect(f.state.writes.at(-1)).toEqual(original);
});
test('creating Review uses an existing started Sprint and a chosen saved snapshot',async({page})=>{
  const f=meetingFixture();await mount(page,f);await openAction(page,'create');const editor=action(page,'create');
  await editor.getByLabel('Meeting title',{exact:true}).fill('Review observed account recovery');await editor.getByLabel('Discussion type',{exact:true}).selectOption('review');await reason(page,'create');
  await editor.getByRole('button',{name:'Create meeting draft',exact:true}).click();
  await expect.poll(() => f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params).toMatchObject({kind:'review',snapshot_id:snapshotId});expect(f.state.writes[0].body.preconditions).toEqual({expected_sprint_version:4});
  expect(f.state.meetings.at(-1).meeting.status).toBe('draft');
});
for(const locale of ['en','zh-TW'])for(const width of [1440,768,390])test(`meeting layout ${locale} ${width}`,async({page},info)=>{
  const f=meetingFixture('review');await page.setViewportSize({width,height:900});await mount(page,f,locale);await openAction(page,'draft');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(await page.locator('[data-pm-meetings]').innerText()).not.toMatch(/pm_meeting_[a-z_]+/);
  const out=process.env.BATC_PM_MEETING_SCREENSHOTS||info.outputPath('screenshots');await mkdir(out,{recursive:true});
  await page.screenshot({path:`${out}/meeting-${locale}-${width}.png`,fullPage:true});
});

 test('saved decisions use their title and narrow context keeps identifiers in Details',async({page})=>{
  const f=meetingFixture('review'), detail=f.state.meetings[0]; detail.meeting.draft_revision_id='pmmrev_'+'a'.repeat(32);
  detail.draft={meeting_id:detail.meeting.meeting_id,revision_id:detail.meeting.draft_revision_id,document:{kind:'review',output:{goal_review:'Review recovery',outcomes:[],feedback:[],environment_changes:[],next_steps:[]},
    decisions:[{id:'evidence',title:'Check recovery evidence',rationale:'Verify the result',source_refs:[]}],follow_ups:[],source_refs:[],unresolved_questions:[]}};
  await page.setViewportSize({width:390,height:844}); await mount(page,f); await page.getByText('Saved discussion draft',{exact:true}).click();
  await expect(page.getByRole('heading',{name:'Check recovery evidence',exact:true})).toBeVisible();
  await expect(page.locator('[data-pm-meetings] h4').filter({hasText:/^$/})).toHaveCount(0);
  expect(await page.locator('[data-pm-meetings]').evaluate(el=>[...el.querySelectorAll('code')].every(node=>Boolean(node.closest('details'))))).toBe(true);
  expect(f.state.writes).toEqual([]);
 });

test('selected work uses the title from its exact saved snapshot binding',async({page})=>{
  const f=meetingFixture(),detail=f.state.meetings[0],item=detail.context.document.selected_items[0];
  delete item.title; detail.snapshot.work=[{work_item_id:item.work_item_id,title:'Verify the reset link',binding:{binding_id:item.binding_id}}];
  await mount(page,f);await expect(page.getByRole('link',{name:'Verify the reset link',exact:true})).toBeVisible();
  await openAction(page,'draft');expect(f.state.writes).toEqual([]);
});
