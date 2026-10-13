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
for(const native of [false,true])test.describe(native?'native management workspace':'web management workspace',()=>{
  test('access revalidation keeps an open discussion input usable without exposing old facts',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(pmRoute);
    const fold=page.locator('[data-pm-add-input]');await fold.locator('summary').click();
    const statement=fold.getByLabel('Statement or question',{exact:true});await statement.fill('Keep an unsubmitted research question');
    const before=f.bootstrapReads();f.changeAccess();
    await expect.poll(f.bootstrapReads,{timeout:10000}).toBeGreaterThan(before);
    await expect(statement).toBeVisible();await expect(statement).toBeEnabled();
    await expect(statement).toHaveValue('Keep an unsubmitted research question');expect(f.base.state.writes).toHaveLength(0);
  });
  test('work and Sprint remain on one page with no repeated delivery count',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);
    await expect(page.locator('[data-pm-kanban]')).toHaveCount(1);await expect(page.locator('[data-pm-sprints]')).toHaveCount(1);await expect(page.locator('[data-pm-meetings]')).toHaveCount(1);
    await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(3);
    const card = page.locator(`[data-pm-kanban-work="${workId(1)}"]`);
    await expect(card).not.toContainText('3 accepted sends');
    await card.getByText('Execution and sources',{exact:true}).click();
    await expect(card).toContainText('3 accepted sends');
    await expect(page.locator('[data-pm-sprints]')).toContainText('A returning member can sign in');
    await expect(page.getByRole('tab')).toHaveCount(0);expect(f.kanban.state.writes).toHaveLength(0);
  });
  test('another client’s work and Sprint changes are read from central events',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);
    const card=page.locator(`[data-pm-kanban-work="${workId(1)}"]`);await expect(card).toContainText('Member login');
    f.kanban.state.cards[0].work.title='Login after review';f.publish('work_item');
    await expect(card).toContainText('Login after review',{timeout:10000});
    f.sprint.state.sprints[0].goal.text='A revised direction from another client';f.sprint.state.sprints[0].version++;f.publish('pm_sprint');
    await expect(page.locator('[data-pm-sprints]')).toContainText('A revised direction from another client',{timeout:10000});
    f.meetings.state.meetings[0].meeting.title='Discussion updated in another client';f.meetings.state.meetings[0].meeting.version++;f.publish('pm_meeting');
    await expect(page.locator('[data-pm-meetings]')).toContainText('Discussion updated in another client',{timeout:10000});
  });
  test('failed workspace authorization hides previous facts and recovers the local draft',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);const region=page.locator('[data-pm-sprints]');
    await expect(region).toContainText('A returning member can sign in');
    await region.locator('summary').filter({hasText:/^Product commitments$/}).click();
    await region.locator('summary').filter({hasText:/^Revise Product Goal$/}).click();
    const draft=region.locator('[data-pm-action="pm.product_goal.revise"]').getByRole('textbox',{name:'Product Goal',exact:true});await draft.fill('Keep this unsent direction');
    f.base.state.failRead=true;f.publish('pm_workspace');
    await expect(region).toBeHidden({timeout:10000});await expect(page.locator('[data-pm-kanban]')).toBeHidden();await expect(page.locator('[data-pm-meetings]')).toBeHidden();
    await expect(page.locator('.shell-workspace-block')).not.toContainText('Library refresh');
    f.base.state.failRead=false;f.publish('pm_workspace');await expect(region).toBeVisible({timeout:10000});
    await expect(draft).toHaveValue('Keep this unsent direction');expect(f.sprint.state.writes).toHaveLength(0);
  });
  for(const bootstrapFails of [false,true])test(`access reset clears focused private facts and preserves drafts (bootstrap failure: ${bootstrapFails})`,async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);
    const region=page.locator('[data-pm-sprints]');await expect(region).toContainText('A returning member can sign in');
    await region.locator('summary').filter({hasText:/^Product commitments$/}).click();
    await region.locator('summary').filter({hasText:/^Revise Product Goal$/}).click();
    const draft=region.locator('[data-pm-action="pm.product_goal.revise"]').getByRole('textbox',{name:'Product Goal',exact:true});
    await draft.fill('Preserve an unsubmitted direction');await expect(draft).toBeFocused();
    f.base.state.failRead=true;f.bootstrapFailure(bootstrapFails);f.changeAccess();
    await expect(page.locator('#main')).not.toContainText('A returning member can sign in',{timeout:10000});
    await expect(page.locator('#main')).not.toContainText('Member login');
    await expect(region).toBeHidden();expect(f.sprint.state.writes).toHaveLength(0);
    f.bootstrapFailure(false);f.base.state.failRead=false;f.changeAccess();
    await expect(region).toBeVisible({timeout:15000});
    await region.locator('summary').filter({hasText:/^Product commitments$/}).click();
    await region.locator('summary').filter({hasText:/^Revise Product Goal$/}).click();
    await expect(draft).toHaveValue('Preserve an unsubmitted direction');expect(f.sprint.state.writes).toHaveLength(0);
  });
  test('discussion draft and explicit completion recover the original command after reload',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);
    await expect(page.locator('[data-pm-meetings]')).toContainText('A returning member can sign in');
    await openAction(page,'note');await action(page,'note').getByLabel('Discussion note',{exact:true}).fill('We reviewed the evidence and retained the current direction.');
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
    await page.reload();await action(page,'complete').getByRole('button',{name:'Check original request',exact:true}).click();
    expect(f.meetings.state.writes.at(-1)).toEqual(original);expect(f.meetings.state.ops.size).toBe(3);
    await expect(page.locator('[data-pm-meeting-completion]')).toBeVisible();
    expect(f.sprint.state.writes).toHaveLength(0);expect(f.kanban.state.writes).toHaveLength(0);
  });
  test('an older child response cannot reveal facts after a newer workspace read fails',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);
    const card=page.locator(`[data-pm-kanban-work="${workId(1)}"]`);await expect(card).toContainText('Member login');
    f.kanban.state.cards[0].work.title='Older response held after revoke';f.kanban.state.holdNext=true;
    await page.locator('.pm-main > .pm-heading').getByRole('button',{name:'Refresh',exact:true}).click();
    await expect.poll(()=>f.kanban.state.held.length).toBe(1);
    f.base.state.failRead=true;f.publish('pm_workspace');await expect(page.locator('[data-pm-kanban]')).toBeHidden({timeout:10000});
    f.kanban.state.held.shift()();await expect(card).toContainText('Older response held after revoke');
    await expect(page.locator('[data-pm-kanban]')).toBeHidden();await expect(page.locator('[data-pm-meetings]')).toBeHidden();
    expect(f.sprint.state.writes).toHaveLength(0);
  });
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`management workspace ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:1000}}),page=await context.newPage(),f=fixture();
  await mountConversation(page,false,f.dispatch);await page.goto(route);
  await expect(page.locator('[data-pm-sprints] h2')).toHaveText(locale==='en'?'Sprint and iteration':'Sprint 與迭代');
  await expect(page.locator('[data-pm-kanban-work]')).toHaveCount(3);
  await expect(page.locator('[data-pm-meetings]')).toContainText('A returning member can sign in');
  expect(await page.locator('.pm-main').innerText()).not.toMatch(/pm_kanban_|pm_sprint_|pm_meeting_|\bundefined\b/);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const directory=process.env.BATC_PM_MANAGEMENT_SCREENSHOTS;
  if(directory){await mkdir(directory,{recursive:true});await page.screenshot({path:`${directory}/workspace-${locale}-${width}.png`,fullPage:true});}
  await context.close();
});
