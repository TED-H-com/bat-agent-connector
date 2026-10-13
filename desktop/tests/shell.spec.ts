import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workbenchFixture, route, workspaceId, workId} from './pm-workbench-fixture.ts';
import {workspaceFixture, workspaceProject, workspaceRoute} from './workspace-fixture.ts';
import {threadId} from './pm-fixture.ts';
import {openPmSection} from './pm-navigation.ts';
function fixture() {
  const pm = workbenchFixture(), work = workspaceFixture();
  const state = {denyBindings: false, offline: false};
  const dispatch = async (input: any) => {
    const path = input.path.split('?')[0];
    if (path === '/events' && state.offline) return {status: 503, data: {error: {code: 'OFFLINE', message: 'Fixture offline'}}};
    if (path === '/bootstrap' || path === '/capabilities') {
      const result = await pm.dispatch(input), caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_workspaces.work_evidence = true;
      caps.features.pm_resources = {version: 1};
      return result;
    }
    if (path === `/pm/workspaces/${workspaceId}/projects`) return state.denyBindings
      ? {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Binding access removed'}}}
      : {status: 200, data: {workspace_id: workspaceId, bindings: [{workspace_id: workspaceId, project_id: workspaceProject}], next_cursor: null}};
    if (path === `/pm/workspaces/${workspaceId}/work`) return {status: 200, data: {workspace_id: workspaceId, bindings: [], next_cursor: null}};
    if (path === `/pm/workspaces/${workspaceId}/resources`) return {status: 200, data: {workspace_id: workspaceId, rows: [], quota_pools: [], interval_s: 60, as_of: new Date().toISOString()}};
    if (/^\/(projects|work-items|sessions|worktrees|checkpoints)(\/|$)/.test(path)) return work.dispatch(input);
    return pm.dispatch(input);
  };
  return {pm, work, state, dispatch};
}
test('workspace remains selected through views and bound work; history and legacy thread routes', async ({page}) => {
  const f = fixture(); await mountConversation(page, false, f.dispatch); await page.goto(route);
  const context = page.locator('.shell-workspace-block'); await expect(context).toHaveAttribute('data-workspace-id', workspaceId);
  for (const view of ['discussions','requirements','agents','board','overview']) {
    await openPmSection(page, view); await expect(context).toHaveAttribute('data-workspace-id', workspaceId);
    await expect(page).toHaveURL(new RegExp('/' + view + '$'));
  }
  await page.goBack(); await expect(page.locator('[data-pm-region=board]')).toBeVisible();
  await page.goForward(); await expect(page.locator('[data-pm-region=overview]')).toBeVisible();
  const project = page.locator('.workspace-tree').getByRole('button', {name: 'Expand Documentation search', exact: true});
  if (await project.count()) await project.click();
  await page.locator(`.workspace-tree a[href="${workspaceRoute}"]`).click();
  await expect(page.locator('.conversation .msg')).toHaveCount(2); await expect(context).toHaveAttribute('data-workspace-id', workspaceId);
  await expect(page.locator('#nav')).toContainText('Work'); expect(f.pm.base.state.writes).toHaveLength(0);
  await page.goto(`/dashboard/#/pm/${workspaceId}/${threadId}`);
  await expect(page).toHaveURL(new RegExp(`/discussions/${threadId}$`)); await expect(page.locator('.pm-notes')).toBeVisible();
  await openPmSection(page, 'requirements'); await page.locator('.shell-top-actions a.ui-icon-button[href="#/settings"]').click();
  await page.locator('[data-pm-view=requirements]').click(); await expect(context).toHaveAttribute('data-workspace-id', workspaceId);
});
test('filters, expanded rows and selection survive other views and Settings without caching facts', async ({page}) => {
  const f = fixture(); await mountConversation(page, false, f.dispatch); await page.goto(route); await openPmSection(page,'requirements');
  const filter = page.locator('[data-pm-rtm] input[type=search]'); await filter.fill('login');
  await page.locator('[data-rtm-toggle]').first().click(); await page.locator(`[data-rtm-select="${workId(1)}"]`).first().click();
  await openPmSection(page,'overview'); await openPmSection(page,'requirements');
  await expect(filter).toHaveValue('login'); await expect(page.locator('[data-rtm-detail]')).toContainText('PR #10');
  await page.locator('.shell-top-actions a.ui-icon-button[href="#/settings"]').click(); await page.locator('[data-pm-view=requirements]').click();
  await expect(filter).toHaveValue('login'); await expect(page.locator('[data-rtm-detail]')).toContainText('PR #10');
  f.pm.state.denyIssues = true; await page.locator('[data-pm-rtm]').getByRole('button',{name:'Refresh records',exact:true}).click();
  await expect(page.locator('[data-rtm-detail]')).toBeHidden(); await expect(page.locator('[data-pm-rtm]')).not.toContainText('PR #10');
});
test('binding withdrawal removes Work rows; workspace names never imply bindings', async ({page}) => {
  const f = fixture(); await mountConversation(page, false, f.dispatch); await page.goto(route);
  await expect(page.locator('.workspace-tree').first()).toContainText('Documentation search');
  f.state.denyBindings = true; f.pm.base.state.cursor++;
  // PM evidence events and project events both revalidate the explicit association.
  await page.locator('[data-pm-view=discussions]').click(); await page.locator('[data-pm-view=overview]').click();
  await page.reload(); await expect(page.locator('.workspace-tree').first()).not.toContainText('Documentation search');
  await expect(page.locator('.workspace-tree-status')).toContainText('scope');
});
test('mobile drawer traps focus, closes on Escape and returns to its opener', async ({page}) => {
  await page.setViewportSize({width:390,height:844});const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(route);
  const opener=page.locator('#workspace-menu'); await opener.click(); const drawer=page.locator('.workspace-nav');
  await expect(drawer).toHaveAttribute('aria-modal','true'); await expect(page.locator('#main')).toHaveAttribute('inert','');
  await drawer.locator('.workspace-nav-footer > a').last().focus(); await page.keyboard.press('Tab');
  await expect(drawer.locator('.shell-workspace-switch')).toBeFocused(); await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden(); await expect(opener).toBeFocused();
  await opener.click(); await page.locator('[data-pm-view=board]').click(); await expect(drawer).toBeHidden();
});
test('topbar uses connection states and opens Connection', async ({page}) => {
  const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(route);
  await expect(page.locator('#live')).toHaveAttribute('data-connection','connected'); await expect(page.locator('#live')).toHaveText('Connected');
  f.state.offline=true; await expect(page.locator('#live')).toHaveAttribute('data-connection','offline');
  f.state.offline=false; await expect(page.locator('#live')).toHaveAttribute('data-connection','connected',{timeout:10000});
  await page.locator('#live').click(); await expect(page).toHaveURL(/#\/settings$/);
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`shell layout ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:900},colorScheme:'light'}),page=await context.newPage(),f=fixture(),errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));await mountConversation(page,false,f.dispatch);await page.goto(route);
  for(const view of ['overview','discussions','requirements','agents','board']) {
    await openPmSection(page,view);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await expect(page.locator('.pm-main > .ui-page-header h1')).toHaveCount(1);
  }
  expect(await page.locator('#main').innerText()).not.toMatch(/\b(?:undefined|null|ui_\w+)\b/);expect(errors).toEqual([]);await context.close();
});
test('dark system tokens and explicit light override apply to the whole shell',async({page})=>{
  await page.emulateMedia({colorScheme:'dark'});const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(route);
  expect(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--surface').trim())).toBe('#161a21');
  await page.evaluate(()=>document.documentElement.dataset.theme='light');
  expect(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--surface').trim())).toBe('#ffffff');
  await page.evaluate(()=>document.documentElement.dataset.theme='dark');
  expect(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--text').trim())).toBe('#e7eaf0');
});
test('all eight source-capable views keep context even when a secondary source is unavailable',async({page})=>{
  const f=fixture();
  const dispatch=async(input:any)=>{
    const path=input.path.split('?')[0];
    if (/\/(roadmap|release-baselines|governance)(\/|$)/.test(path)) return {status:403,data:{error:{code:'FORBIDDEN',message:'Source access removed'}}};
    const result=await f.dispatch(input);
    if(path==='/bootstrap'||path==='/capabilities') {
      const caps=path==='/bootstrap'?result.data.capabilities:result.data;
      Object.assign(caps.features.pm_workspaces,{roadmap:true,release_baselines:true,governance:true});
    }
    return result;
  };
  await mountConversation(page,false,dispatch);await page.goto(route);
  for(const view of ['overview','discussions','requirements','schedule','dependencies','issues','agents','board']) {
    await openPmSection(page,view);await expect(page.locator(`[data-pm-region="${view}"]`)).toBeVisible();
    await expect(page.locator('.shell-workspace-block')).toHaveAttribute('data-workspace-id',workspaceId);
  }
  expect(f.pm.base.state.writes).toHaveLength(0);
});
test('absent capabilities hide views and preferences never cross namespace boundaries',async({page})=>{
  const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(route);await openPmSection(page,'overview');
  await expect(page.locator('[data-pm-view=schedule], [data-pm-view=dependencies], [data-pm-view=issues]')).toHaveCount(0);
  await page.goto('/ui-components.html');
  expect(await page.evaluate(async()=>{
    const {viewState}=await import('/src/ui/view-state.js');
    const {rememberWorkspace,lastWorkspace,rememberProjectView,lastProjectView}=await import('/src/pm-workbench.js');
    rememberWorkspace('connection-A','workspace');rememberProjectView('connection-A','workspace','requirements');
    viewState('connection-A','workspace','requirements').filter='private preference';
    return [lastWorkspace('connection-B')??null,lastProjectView('connection-B','workspace'),viewState('connection-B','workspace','requirements').filter??null];
  })).toEqual([null,'overview',null]);
});
test('workspace creation opens from the mobile switcher and from Settings with the durable draft intact',async({page})=>{
  await page.setViewportSize({width:390,height:844});const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(route);
  await page.locator('#workspace-menu').click(); await page.locator('.shell-workspace-switch').click();
  await page.getByRole('menuitem',{name:'New workspace',exact:true}).click();
  await expect(page.locator('.workspace-nav')).toBeHidden();
  const panel=page.getByRole('dialog',{name:'New workspace',exact:true}); await expect(panel).toBeVisible();
  await panel.getByRole('textbox',{name:'Workspace name',exact:true}).fill('Saved draft');
  await page.keyboard.press('Escape'); await expect(page.locator('#workspace-menu')).toBeFocused();
  await page.goto('/dashboard/#/settings'); await page.locator('#workspace-menu').click(); await page.locator('.shell-workspace-switch').click();
  await page.getByRole('menuitem',{name:'New workspace',exact:true}).click(); await expect(panel).toBeVisible();
  await expect(panel.getByRole('textbox',{name:'Workspace name',exact:true})).toHaveValue('Saved draft');
  expect(f.pm.base.state.writes).toHaveLength(0);
});
test('initial central loading reports Reconnecting until the view is ready',async({page})=>{
  const f=fixture();let release=()=>{};const gate=new Promise<void>(resolve=>{release=resolve;});
  await mountConversation(page,false,async input=>{
    if(input.path===`/pm/workspaces/${workspaceId}`) await gate;
    return f.dispatch(input);
  });
  await page.goto(route);await expect(page.locator('#live')).toHaveAttribute('data-connection','reconnecting');
  await expect(page.locator('#live')).toHaveText('Reconnecting');release();
  await expect(page.locator('#live')).toHaveAttribute('data-connection','connected');
});
