import {openThreadTab} from './pm-navigation.ts';
import {openPmSection, expectPmWithdrawn} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {pmFixture,workspaceId,pmRoute} from './pm-fixture.ts';
import {fixture as sprintFixture,actions,pid} from './pm-sprint-fixture.ts';
import {fixture as kanbanFixture,workId} from './pm-kanban-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {meetingFixture,action,openAction,reason} from './pm-meetings-fixture.ts';

function fixture(){
  const base=pmFixture(),sprint=sprintFixture(),kanban=kanbanFixture(),meetings=meetingFixture(),prefix=`/pm/workspaces/${workspaceId}`;
  sprint.state.actor='reader';sprint.state.sprints[0].definition.owner_actor='reader';
  sprint.state.sprints[0].definition.developers=['reader','developer'];sprint.state.sprints[0].selected[0].assignee='reader';
  meetings.state.actor='reader';meetings.state.sprints=sprint.state.sprints;
  const context=meetings.state.meetings[0].context.document,current=sprint.state.sprints[0];
  Object.assign(context,{sprint_version:current.version,sprint_status:current.status,sprint_definition:structuredClone(current.definition),
    sprint_goal:structuredClone(current.goal),selected_items:structuredClone(current.selected),artifacts:structuredClone(sprint.state.artifacts)});
  delete context.artifacts.workspace_id;
  let eventType='pm_workspace',accessVersion=0,failBootstrap=false,bootstrapReads=0;
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),path=url.pathname;
    if(path==='/bootstrap')bootstrapReads++;
    if(path==='/bootstrap'&&failBootstrap)return {status:503,data:{error:{code:'UNAVAILABLE',message:'Bootstrap unavailable'}}};
    if(path==='/events'&&!url.searchParams.get('checkpoint')?.startsWith(`pm-access-${accessVersion}-`))return {status:409,data:{error:{code:'EVENT_CURSOR_RESET',reason:'access_changed',preserve_drafts:true}}};
    if(path==='/bootstrap'||path==='/capabilities'){
      const result=await base.dispatch(input),caps=path==='/bootstrap'?result.data.capabilities:result.data;
      Object.assign(caps.features.pm_workspaces,{kanban:true,sprints:true,meetings:true});caps.actions.push(...sprint.config().caps.actions,...meetings.config().caps.actions);
      if(path==='/bootstrap')result.data.sync.checkpoint.token=`pm-access-${accessVersion}-0`;return result;
    }
    if(path==='/events'){
      const result=await base.dispatch(input);for(const event of result.data.events)event.resource_type=eventType;result.data.sync.checkpoint.token=`pm-access-${accessVersion}-${result.data.next_cursor}`;return result;
    }
    if(path.startsWith(prefix+'/kanban'))return kanban.dispatch(input);
    if(path.startsWith(prefix+'/meetings')||path===prefix+'/snapshots'||input.method==='POST'&&input.body?.action?.startsWith('pm.meeting.'))return meetings.dispatch(input);
    if(path.startsWith('/operations/')&&[...meetings.state.ops.values()].some((op:any)=>path.endsWith(op.operation_id)))return meetings.dispatch(input);
    if(path.startsWith(prefix+'/sprint')||path===prefix+'/plans/accepted'||path===prefix+'/work'||path==='/projects/'+pid
      ||path.startsWith('/operations/')||input.method==='POST'&&actions.includes(input.body?.action))return sprint.dispatch(input);
    if(path===prefix&&!base.state.failRead){const result=await base.dispatch(input);result.data.workspace.members=[{actor:'reader',role:'owner'},{actor:'developer',role:'contributor'}];return result;}
    return base.dispatch(input);
  };
  return{base,sprint,kanban,meetings,dispatch,publish:(kind:string)=>{eventType=kind;base.state.cursor++;},changeAccess:()=>accessVersion++,bootstrapFailure:(value:boolean)=>{failBootstrap=value;},bootstrapReads:()=>bootstrapReads};
}
const route=`/dashboard/#/pm/${workspaceId}`;
async function withdrawnViews(page) {
  const facts = ['Member login', 'A returning member can sign in', 'Older response held after revoke'];
  await expectPmWithdrawn(page, 'board', '[data-pm-kanban]', undefined, facts);
  await expectPmWithdrawn(page, 'discussions', '[data-pm-meetings]', 'ui_meetings', facts);
  await expectPmWithdrawn(page, 'schedule', '[data-pm-sprints]', 'ui_sprints', facts);
}
for(const native of [false,true])test.describe(native?'native management workspace':'web management workspace',()=>{
  test('access revalidation keeps an open discussion input usable without exposing old facts',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);await openThreadTab(page,'inputs');
    const fold=page.locator('[data-pm-add-input]');await fold.locator('summary').click();
    const statement=fold.getByLabel('Statement or question',{exact:true});await statement.fill('Keep an unsubmitted research question');
    const before=f.bootstrapReads();f.changeAccess();
    await expect.poll(f.bootstrapReads,{timeout:10000}).toBeGreaterThan(before);
    await expect(statement).toBeVisible();await expect(statement).toBeEnabled();
    await expect(statement).toHaveValue('Keep an unsubmitted research question');expect(f.base.state.writes).toHaveLength(0);
  });
  test('work and Sprint keep their sources across views without repeated delivery counts',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'board');
    await expect(page.locator('[data-pm-kanban]')).toHaveCount(1);await expect(page.locator('[data-pm-sprints]')).toHaveCount(1);await expect(page.locator('[data-pm-meetings]')).toHaveCount(1);
    await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(3);
    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await expect(card).not.toContainText('3 accepted sends');
    await openPmSection(page,'board');await card.locator('[data-board-open]').click();
    await page.locator('[data-work-section=details] > summary').click();
    await expect(page.locator('[data-work-inspector]')).toContainText('3 accepted sends');
    await expect(page.locator('[data-pm-sprints]')).toContainText('A returning member can sign in');
    await expect(page.getByRole('tab')).toHaveCount(0);expect(f.kanban.state.writes).toHaveLength(0);
  });
  test('another client’s work and Sprint changes are read from central events',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'board');
    const card=page.locator(`[data-pm-kanban-work="${workId(1)}"]`);await expect(card).toContainText('Member login');
    f.kanban.state.cards[0].work.title='Login after review';f.publish('work_item');
    await expect(card).toContainText('Login after review',{timeout:10000});
    f.sprint.state.sprints[0].goal.text='A revised direction from another client';f.sprint.state.sprints[0].version++;f.publish('pm_sprint');
    await openPmSection(page,'schedule','ui_sprints');
    await expect(page.locator('[data-pm-sprints]')).toContainText('A revised direction from another client',{timeout:10000});
    f.meetings.state.meetings[0].meeting.title='Discussion updated in another client';f.meetings.state.meetings[0].meeting.version++;f.publish('pm_meeting');
    await openPmSection(page,'discussions','ui_meetings');
    await expect(page.locator('[data-pm-meetings]')).toContainText('Discussion updated in another client',{timeout:10000});
  });
  test('failed workspace authorization hides previous facts and recovers the local draft',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'board');const region=page.locator('[data-pm-sprints]');
    await expect(region).toContainText('A returning member can sign in');
    await openPmSection(page,'schedule','ui_sprints');await region.locator('summary').filter({hasText:/^Product commitments$/}).click();
    await region.locator('summary').filter({hasText:/^Revise Product Goal$/}).click();
    const draft=region.locator('[data-pm-action="pm.product_goal.revise"]').getByRole('textbox',{name:'Product Goal',exact:true});await draft.fill('Keep this unsent direction');
    f.base.state.failRead=true;f.publish('pm_workspace');
    await expect(region).toHaveJSProperty('hidden',true,{timeout:10000});await withdrawnViews(page);
    await expect(page.locator('.shell-workspace-block')).not.toContainText('Library refresh');
    f.base.state.failRead=false;f.publish('pm_workspace');await expect(region).toBeVisible({timeout:10000});
    await expect(draft).toHaveValue('Keep this unsent direction');expect(f.sprint.state.writes).toHaveLength(0);
  });
  for(const bootstrapFails of [false,true])test(`access reset clears focused private facts and preserves drafts (bootstrap failure: ${bootstrapFails})`,async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'board');
    const region=page.locator('[data-pm-sprints]');await expect(region).toContainText('A returning member can sign in');
    await openPmSection(page,'schedule','ui_sprints');await region.locator('summary').filter({hasText:/^Product commitments$/}).click();
    await region.locator('summary').filter({hasText:/^Revise Product Goal$/}).click();
    const draft=region.locator('[data-pm-action="pm.product_goal.revise"]').getByRole('textbox',{name:'Product Goal',exact:true});
    await draft.fill('Preserve an unsubmitted direction');await expect(draft).toBeFocused();
    f.base.state.failRead=true;f.bootstrapFailure(bootstrapFails);f.changeAccess();
    await expect(page.locator('#main')).not.toContainText('A returning member can sign in',{timeout:10000});
    await expect(page.locator('#main')).not.toContainText('Member login');
    await expect(region).toBeHidden();expect(f.sprint.state.writes).toHaveLength(0);
    f.bootstrapFailure(false);f.base.state.failRead=false;f.changeAccess();
    await expect(region).toBeVisible({timeout:15000});
    await openPmSection(page,'schedule','ui_sprints');await region.locator('summary').filter({hasText:/^Product commitments$/}).click();
    await region.locator('summary').filter({hasText:/^Revise Product Goal$/}).click();
    await expect(draft).toHaveValue('Preserve an unsubmitted direction');expect(f.sprint.state.writes).toHaveLength(0);
  });
  test('discussion draft and explicit completion recover the original command after reload',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'board');
    await expect(page.locator('[data-pm-meetings]')).toContainText('A returning member can sign in');
    await openPmSection(page,'discussions','ui_meetings');await openAction(page,'note');await action(page,'note').getByLabel('Discussion note',{exact:true}).fill('We reviewed the evidence and retained the current direction.');
    await reason(page,'note');await action(page,'note').getByRole('button',{name:'Save discussion note',exact:true}).click();
    await expect(page.locator('[data-pm-meetings]')).toContainText('We reviewed the evidence');
    await openAction(page,'draft');const editor=action(page,'draft');
    await editor.getByLabel('Why this Sprint matters',{exact:true}).fill('Members need reliable access');
    await editor.getByLabel('Delivery approach',{exact:true}).fill('Review the fixed selected work');
    await editor.getByLabel('Capacity and availability discussed',{exact:true}).fill('One focused improvement');
    await reason(page,'draft');await editor.getByRole('button',{name:'Save discussion draft',exact:true}).click();
    await expect(page.getByRole('heading',{name:'Saved discussion draft',exact:true})).toBeVisible();
    expect(f.meetings.state.meetings[0].meeting.status).toBe('draft');
    await openAction(page,'complete');await action(page,'complete').getByLabel('What was discussed and concluded',{exact:true}).fill('The recorded discussion keeps the direction and unresolved quality checks.');
    await reason(page,'complete');f.meetings.state.lost=true;
    await action(page,'complete').getByRole('button',{name:'Complete discussion',exact:true}).click();
    await expect(action(page,'complete')).toContainText('Reply unavailable');const original=structuredClone(f.meetings.state.writes.at(-1));
    await page.reload();await openPmSection(page,'discussions','ui_meetings');await action(page,'complete').getByRole('button',{name:'Check original request',exact:true}).click();
    expect(f.meetings.state.writes.at(-1)).toEqual(original);expect(f.meetings.state.ops.size).toBe(3);
    await expect(page.locator('[data-pm-meeting-completion]')).toBeVisible();
    expect(f.sprint.state.writes).toHaveLength(0);expect(f.kanban.state.writes).toHaveLength(0);
  });
  test('an older child response cannot reveal facts after a newer workspace read fails',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page,'board');
    const card=page.locator(`[data-pm-kanban-work="${workId(1)}"]`);await expect(card).toContainText('Member login');
    f.kanban.state.cards[0].work.title='Older response held after revoke';f.kanban.state.holdNext=true;
    await page.locator('.pm-main > .ui-page-header').getByRole('button',{name:'Refresh',exact:true}).click();
    await expect.poll(()=>f.kanban.state.held.length).toBe(1);
    f.base.state.failRead=true;f.publish('pm_workspace');await expect(page.locator('[data-pm-kanban]')).toHaveJSProperty('hidden',true,{timeout:10000});await withdrawnViews(page);
    f.kanban.state.held.shift()();await expect(card).toHaveCount(0);
    await withdrawnViews(page);
    expect(f.sprint.state.writes).toHaveLength(0);
  });
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`management workspace ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:1000}}),page=await context.newPage(),f=fixture();
  await mountConversation(page,false,f.dispatch);await page.goto(route);await openPmSection(page,'board');
  await expect(page.locator('[data-pm-sprints] [data-pm-sprint-status]')).toHaveText(locale==='en'?'Planned':'已規劃');
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(3);
  await expect(page.locator('[data-pm-meetings]')).toContainText('A returning member can sign in');
  for (const [view, section] of [['board', undefined], ['discussions', 'ui_meetings'], ['schedule', 'ui_sprints']]) {
    await openPmSection(page, view!, section);
    expect(await page.locator('.pm-main').innerText()).not.toMatch(/pm_kanban_|pm_sprint_|pm_meeting_|\bundefined\b/);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  }
  const directory=process.env.BATC_PM_MANAGEMENT_SCREENSHOTS;
  if(directory){await mkdir(directory,{recursive:true});await page.screenshot({path:`${directory}/workspace-${locale}-${width}.png`,fullPage:true});}
  await context.close();
});

test('thread meetings and follow-up reads wait for their visible disclosure', async ({page}) => {
  const f = fixture(), reads: string[] = [], prefix = `/pm/workspaces/${workspaceId}`;
  await mountConversation(page, false, async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    if (input.method === 'GET' && path !== '/events') reads.push(path);
    if (path === prefix + '/follow-ups') return {status: 200, data: {version: 1, workspace_id: workspaceId, head_version: 0, intakes: [], next_cursor: null, coverage: {visibility_filtered: false}}};
    if (path === prefix + '/follow-ups/latest') return {status: 200, data: {version: 1, workspace_id: workspaceId, head_version: 0, intake: null}};
    if (path === prefix + '/plans/head') return {status: 200, data: {version: 1, head: {workspace_id: workspaceId, version: 0}}};
    const result = await f.dispatch(input);
    if (path === '/bootstrap' || path === '/capabilities') (path === '/bootstrap' ? result.data.capabilities : result.data).features.pm_workspaces.follow_up_prepare = true;
    return result;
  });
  await page.goto(pmRoute); await expect(page.locator('[data-discussion-composer]')).toBeEnabled();
  const related = () => reads.filter(path => /\/(meetings|follow-ups)(\/|$)/.test(path));
  expect(related()).toHaveLength(0);
  const refresh = page.getByRole('button', {name: 'Refresh', exact: true});
  await refresh.click(); await expect(refresh).toBeEnabled(); expect(related()).toHaveLength(0);
  const meetings = page.locator('[data-pm-section=ui_meetings] > details'), follow = page.locator('[data-pm-section=ui_follow_up] > details');
  await meetings.locator(':scope > summary').click(); await expect.poll(() => related().filter(path => path.includes('/meetings')).length).toBe(2);
  await meetings.locator(':scope > summary').click();
  await follow.locator(':scope > summary').click(); await expect.poll(() => related().filter(path => path.includes('/follow-ups')).length).toBe(2);
  await follow.locator(':scope > summary').click(); reads.length = 0;
  f.base.state.thread.title = 'Thread event read'; f.publish('pm_thread'); await expect(page.locator('[data-discussion-title]')).toHaveText('Thread event read', {timeout: 10000});
  await expect(refresh).toBeEnabled(); expect(related()).toHaveLength(0);
  f.publish('pm_meeting'); await expect.poll(() => page.evaluate(token => Object.values(localStorage).some(value => value.includes(token)), 'pm-access-0-' + f.base.state.cursor)).toBe(true);
  expect(related()).toHaveLength(0); await meetings.locator(':scope > summary').click();
  await expect.poll(() => related().filter(path => path.includes('/meetings')).length, {timeout: 10000}).toBe(2);
  await meetings.locator(':scope > summary').click();
  expect(related().filter(path => path.includes('/follow-ups'))).toHaveLength(0); reads.length = 0;
  f.publish('pm_follow_up'); await expect.poll(() => page.evaluate(token => Object.values(localStorage).some(value => value.includes(token)), 'pm-access-0-' + f.base.state.cursor)).toBe(true);
  expect(related()).toHaveLength(0); await follow.locator(':scope > summary').click();
  await expect.poll(() => related().filter(path => path.includes('/follow-ups')).length, {timeout: 10000}).toBe(2);
  expect(related().filter(path => path.includes('/meetings'))).toHaveLength(0);
  expect(f.base.state.writes).toHaveLength(0); expect(f.meetings.state.writes).toHaveLength(0);
});
