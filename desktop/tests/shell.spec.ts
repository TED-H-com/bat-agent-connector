import {test, expect} from '@playwright/test';
import {mountConversation} from './conversation-fixture.ts';
import {workbenchFixture, route, workspaceId, workId} from './pm-workbench-fixture.ts';
import {workspaceFixture, workspaceProject, workspaceRoute} from './workspace-fixture.ts';
import {threadId} from './pm-fixture.ts';
import {openPmSection, revealPmView} from './pm-navigation.ts';
function fixture() {
  const pm = workbenchFixture(), work = workspaceFixture();
  const state = {denyBindings: false, offline: false, bindingReads: 0, eventType: 'pm_thread'};
  const publish = (type: string) => {state.eventType = type; pm.base.state.cursor++;};
  const dispatch = async (input: any) => {
    const path = input.path.split('?')[0];
    if (path === '/events' && state.offline) return {status: 503, data: {error: {code: 'OFFLINE', message: 'Fixture offline'}}};
    if (path === '/bootstrap' || path === '/capabilities') {
      const result = await pm.dispatch(input), caps = path === '/bootstrap' ? result.data.capabilities : result.data;
      caps.features.pm_workspaces.work_evidence = true;
      caps.features.pm_resources = {version: 1};
      return result;
    }
    if (path === `/pm/workspaces/${workspaceId}/projects`) {state.bindingReads++; return state.denyBindings
      ? {status: 403, data: {error: {code: 'FORBIDDEN', message: 'Binding access removed'}}}
      : {status: 200, data: {workspace_id: workspaceId, bindings: [{workspace_id: workspaceId, project_id: workspaceProject}], next_cursor: null}};}
    if (path === `/pm/workspaces/${workspaceId}/work`) return {status: 200, data: {workspace_id: workspaceId, bindings: [], next_cursor: null}};
    if (path === `/pm/workspaces/${workspaceId}/resources`) return {status: 200, data: {workspace_id: workspaceId, rows: [], quota_pools: [], interval_s: 60, as_of: new Date().toISOString()}};
    if (/^\/(projects|work-items|sessions|worktrees|checkpoints)(\/|$)/.test(path)) return work.dispatch(input);
    const result = await pm.dispatch(input);
    if (path === '/events') for (const event of result.data.events) event.resource_type = state.eventType;
    return result;
  };
  return {pm, work, state, dispatch, publish};
}
for (const width of [390,1440]) test(`workspace switcher keeps its chevron after the label at ${width}`, async ({page}) => {
  await page.setViewportSize({width,height:900});
  const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(route);
  await expect(page.locator('.shell-workspace-block')).toHaveAttribute('data-workspace-id',workspaceId);
  if(width<1000)await page.locator('#workspace-menu').click();
  const trigger=page.locator('.shell-workspace-switch');
  await expect(trigger).toBeVisible();
  const label=await trigger.locator('.shell-workspace-name').boundingBox(),chevron=await trigger.locator('.ui-icon').boundingBox();
  expect(label).not.toBeNull();expect(chevron).not.toBeNull();
  expect(chevron!.x).toBeGreaterThanOrEqual(label!.x+label!.width);
  expect(chevron!.x+chevron!.width).toBeLessThanOrEqual(width);
  expect(f.pm.base.state.writes).toHaveLength(0);
});
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
  await page.evaluate(id => {location.hash = `#/pm/${id}`;}, workspaceId);
  await expect(page.locator('[data-pm-region=discussions]')).toBeVisible();
  await openPmSection(page, 'requirements'); await page.locator('.workspace-nav-footer a[href="#/settings"]').click();
  await (await revealPmView(page, 'requirements')).click(); await expect(context).toHaveAttribute('data-workspace-id', workspaceId);
});
test('filters, expanded rows and selection survive other views and Settings without caching facts', async ({page}) => {
  const f = fixture(); await mountConversation(page, false, f.dispatch); await page.goto(route); await openPmSection(page,'requirements');
  const filter = page.locator('[data-pm-rtm] input[type=search]'); await filter.fill('login');
  await page.locator('[data-rtm-toggle]').first().click(); await page.locator(`[data-rtm-select="${workId(1)}"]`).first().click();
  await openPmSection(page,'overview'); await openPmSection(page,'requirements');
  await expect(filter).toHaveValue('login'); await expect(page.locator('[data-work-inspector]')).toContainText('PR #10');
  await page.locator('.workspace-nav-footer a[href="#/settings"]').click(); await (await revealPmView(page, 'requirements')).click();
  await expect(filter).toHaveValue('login'); await expect(page.locator('[data-work-inspector]')).toContainText('PR #10');
  f.pm.state.denyIssues = true; await page.locator('[data-pm-refresh]').click();
  await expect(page.locator('[data-work-inspector]')).toContainText('PR #10');
  await expect(page.locator('[data-pm-rtm]')).not.toContainText('Issue #41');
  f.pm.state.denyWork = true; await page.locator('[data-pm-refresh]').click();
  await expect(page.locator('[data-work-inspector]')).toBeHidden(); await expect(page.locator('[data-pm-rtm]')).not.toContainText('PR #10');
});
for (const kind of ['pm_evidence', 'project']) test(`binding withdrawal removes Work rows live on ${kind}; workspace names never imply bindings`, async ({page}) => {
  const f = fixture(); await mountConversation(page, false, f.dispatch); await page.goto(route);
  await expect(page.locator('.workspace-tree').first()).toContainText('Documentation search');
  await expect(page.locator('.workspace-nav')).toHaveClass(/has-pm-context/);
  await expect(page.locator('.workspace-tree').first()).toHaveAttribute('data-binding-state','available');
  expect(f.state.bindingReads).toBeGreaterThan(0);
  f.state.denyBindings = true; f.publish(kind);
  await expect(page.locator('.workspace-tree').first()).not.toContainText('Documentation search');
  await expect(page.locator('.workspace-tree-status')).toContainText('scope');
  await expect(page.locator('.workspace-tree-status')).not.toContainText('Last observed work is retained');
  const failure = page.locator('[data-work-read-error]');
  await expect(failure.locator(':scope > details > summary')).toHaveText('Work links unavailable');
  await expect(failure.locator(':scope > details')).not.toHaveAttribute('open', '');
  await expect(failure.getByRole('button', {name: 'Retry', exact: true})).toBeVisible();
  f.state.denyBindings = false;
  await failure.getByRole('button', {name: 'Retry', exact: true}).click();
  await expect(page.locator('.workspace-tree').first()).toContainText('Documentation search');
  await expect(failure).toHaveCount(0);
  expect(f.pm.base.state.writes).toHaveLength(0);
});
test('mobile drawer traps focus, closes on Escape and returns to its opener', async ({page}) => {
  await page.setViewportSize({width:390,height:844});const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(route);
  await expect(page.locator('#live')).toHaveAttribute('data-connection','connected');
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
  f.state.offline=true; await expect(page.locator('#live')).toHaveAttribute('data-connection','reconnecting');
  f.state.offline=false; await expect(page.locator('#live')).toHaveAttribute('data-connection','connected',{timeout:10000});
  await page.locator('#live').click(); await expect(page).toHaveURL(/#\/settings$/);
  await page.getByRole('button',{name:'Disconnect',exact:true}).click();
  await expect(page.locator('#live')).toHaveAttribute('data-connection','offline');
  await expect(page.locator('#live')).toHaveText('Not connected');
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
test('authenticated bootstrap stays Connected while the first view read is held',async({page})=>{
  const f=fixture();let release=()=>{};const gate=new Promise<void>(resolve=>{release=resolve;});
  await mountConversation(page,false,async input=>{
    if(input.path===`/pm/workspaces/${workspaceId}`) await gate;
    return f.dispatch(input);
  });
  await page.goto(route);await expect(page.locator('#live')).toHaveAttribute('data-connection','connected');
  await expect(page.locator('#live')).toHaveText('Connected');release();
  await expect(page.locator('#live')).toHaveAttribute('data-connection','connected');
});
test('Overview rereads the selected fixed report after leaving for Settings',async({page})=>{
  const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(route);
  await page.locator('[data-pm-report-menu]').click();await page.locator('[data-menu-key=versions]').click();
  const picker=page.getByRole('combobox',{name:'Report version',exact:true});
  await picker.selectOption({index:0});const selected=await picker.inputValue();await expect(picker).toBeEnabled();
  await page.keyboard.press('Escape');await page.locator('.workspace-nav-footer a[href="#/settings"]').click();
  await page.locator('[data-pm-view=overview]').click();
  await page.locator('[data-pm-report-menu]').click();await page.locator('[data-menu-key=versions]').click();
  await expect(picker).toHaveValue(selected);expect(f.pm.base.state.writes).toHaveLength(0);
});
for (const name of ['A workspace with a long descriptive project name that still fits the central name limit', '跨團隊產品改善與長期維護計畫的工作空間名稱，需要在導覽列中完整保留並維持清楚的版面']) test(`long workspace label stays within the mobile topbar: ${name}`,async({page})=>{
  await page.setViewportSize({width:390,height:844});const f=fixture();
  await mountConversation(page,false,async input=>{
    const result=await f.dispatch(input);
    if(result.data?.workspace?.workspace_id===workspaceId) result.data.workspace.name=name;
    for(const row of result.data?.workspaces||[]) if(row.workspace_id===workspaceId) row.name=name;
    return result;
  });
  await page.goto(route);const crumb=page.locator('.shell-mobile-name');await expect(crumb).toHaveAttribute('title',name);
  await expect(crumb).toHaveAccessibleName(name);
  const top=await page.locator('.top').boundingBox(), bounds=await crumb.boundingBox();
  expect(top!.height).toBe(48);expect(bounds!.y).toBeGreaterThanOrEqual(top!.y);expect(bounds!.y+bounds!.height).toBeLessThanOrEqual(top!.y+48);
  await page.locator('#workspace-menu').click();await expect(page.locator('.shell-workspace-block')).toContainText(name);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
test('missing binding capability is unknown; an accessible empty binding page is known empty',async({page})=>{
  const f=fixture();let supported=false, hiddenProject=false;
  await mountConversation(page,false,async input=>{
    const result=await f.dispatch(input),path=input.path.split('?')[0];
    if(path==='/bootstrap'||path==='/capabilities') {
      const caps=path==='/bootstrap'?result.data.capabilities:result.data;caps.features.pm_workspaces.work_evidence=supported;
    }
    if(path===`/pm/workspaces/${workspaceId}/projects`&&!hiddenProject) result.data.bindings=[];
    if(path==='/projects'&&hiddenProject) result.data.projects=[];
    return result;
  });
  await page.goto(route);const tree=page.locator('.workspace-tree').first();
  const search=page.getByRole('searchbox',{name:'Search loaded projects and work'});
  await expect(tree).toHaveAttribute('data-binding-state','unknown');
  await expect(tree).toHaveText('');await expect(page.locator('[data-work-read-error]')).toHaveCount(0);
  await search.fill('missing');await expect(tree).toContainText('Work links unavailable');
  await expect(tree).not.toContainText('No linked projects');
  supported=true;await page.reload();await expect(tree).toHaveAttribute('data-binding-state','empty');
  await expect(tree).toHaveText('');await expect(page.locator('[data-work-read-error]')).toHaveCount(0);
  await search.fill('missing');await expect(tree).toContainText('No matching');
  await expect(tree).not.toContainText('unavailable');
  hiddenProject=true;await page.reload();await expect(tree).toHaveAttribute('data-binding-state','unknown');
  await expect(tree).toHaveText('');await search.fill('missing');await expect(tree).toContainText('Work links unavailable');
  await expect(tree).not.toContainText('No linked projects');
  expect(f.pm.base.state.writes).toHaveLength(0);
});

for (const width of [390, 768, 1440]) test(`sidebar rows share icon and text columns at ${width}`, async ({page}) => {
  await page.setViewportSize({width, height: 900});
  const f = fixture(); await mountConversation(page, false, f.dispatch); await page.goto(route);
  if (width < 1000) await page.locator('#workspace-menu').click();
  const textStart = async (selector: string) => {
    const row = page.locator(selector).first(); await expect(row).toBeVisible(); return (await row.boundingBox())!.x;
  };
  const start = await textStart('[data-pm-view=overview] > span');
  if (width < 1000) expect((await page.locator('[data-pm-view=overview]').boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await expect(page.locator('.shell-work-header .shell-all-projects')).toBeVisible();
  await expect(page.locator('.shell-project-nav .shell-all-projects')).toHaveCount(0);
  expect((await page.locator('.shell-all-projects').boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await page.locator('.shell-all-projects').click();
  if (width < 1000) await page.locator('#workspace-menu').click();
  expect(await textStart('.workspace-session-tree > summary > span')).toBe(start);
  await expect(page.locator('.workspace-project-row .workspace-tree-label').first()).toBeVisible();
  expect(await textStart('.workspace-project-row .workspace-tree-label')).toBe(start);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(f.pm.base.state.writes).toHaveLength(0);
});

test('successful poll stays Connected while a PM child read is held', async ({page}) => {
  const f=fixture(); let hold=false, held=false, release=()=>{};
  const gate=new Promise<void>(resolve=>{release=resolve;});
  await mountConversation(page,false,async input=>{
    if(hold && input.path.split('?')[0]===`/pm/workspaces/${workspaceId}/threads`) {held=true;await gate;}
    return f.dispatch(input);
  });
  await page.goto(route + '/discussions'); await expect(page.locator('#live')).toHaveAttribute('data-connection','connected');
  hold=true;f.publish('pm_workspace');await expect.poll(()=>held).toBe(true);
  await expect(page.locator('#live')).toHaveText('Connected');
  await expect(page.locator('#live')).toHaveAttribute('title',/Waiting to refresh/);
  release();await expect(page.locator('#live')).toHaveAttribute('title',/updates every second/);
  expect(f.pm.base.state.writes).toHaveLength(0);
});
for(const locale of ['en','zh-TW']) for(const width of [390,768,1440]) test(`destinations appear once and Work owns All projects ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=fixture();
  await mountConversation(page,false,f.dispatch);await page.goto(route);
  const pending=locale==='en'?'Pending':'待處理',settings=locale==='en'?'Settings':'設定';
  await expect(page.locator('.shell-workspace-block')).toHaveAttribute('data-workspace-id',workspaceId);
  await expect(page.getByRole('link',{name:pending,exact:true})).toHaveCount(1);
  if(width>=1000) {
    await expect(page.locator('.shell-top-actions a[href="#/home"]:visible,.shell-top-actions a.ui-icon-button[href="#/settings"]:visible')).toHaveCount(0);
    await expect(page.getByRole('link',{name:settings,exact:true})).toHaveCount(1);
    await expect(page.locator('.shell-crumb-workspace')).toHaveAttribute('href',`#/pm/${workspaceId}/overview`);
    await expect(page.locator('.shell-crumb-workspace')).not.toHaveAttribute('aria-haspopup');
  } else {
    await expect(page.locator('.shell-pending')).toHaveAccessibleName(pending);await expect(page.locator('.shell-pending')).toHaveAttribute('title',pending);
    await page.locator('.shell-mobile-name').click();await expect(page.locator('.workspace-nav')).toHaveAttribute('aria-modal','true');
    await expect(page.getByRole('link',{name:pending,exact:true})).toHaveCount(1);await expect(page.getByRole('link',{name:settings,exact:true})).toHaveCount(1);
  }
  await expect(page.locator('.shell-workspace-switch')).toHaveCount(1);
  await expect(page.locator('.shell-work-header > .shell-all-projects')).toBeVisible();
  await expect(page.locator('.shell-project-nav a[href="#/projects"]')).toHaveCount(0);
  if(width<1000){await page.keyboard.press('Escape');await expect(page.locator('.shell-mobile-name')).toBeFocused();}
  // A fresh global page has one view segment rather than a second navigation row.
  await page.goto('/dashboard/#/home');await page.reload();
  await expect(page.locator('.shell-workspace-block')).not.toHaveAttribute('data-workspace-id');
  await expect(page.locator('.shell-crumb-view')).toHaveText(pending);
  await expect(page.getByRole('link',{name:pending,exact:true})).toHaveCount(1);
  if(width<1000)await page.locator('.shell-mobile-name').click();
  await expect(page.getByRole('link',{name:settings,exact:true})).toHaveCount(1);
  await expect(page.getByRole('link',{name:pending,exact:true})).toHaveCount(1);
  await context.close();
});
for(const width of [390,768,1100,1440]) test(`long tree names are single-line with accessible full names at ${width}`,async({page})=>{
  await page.setViewportSize({width,height:900});const f=fixture();
  const project='Documentation search with a deliberately long project name',session='持續改善文件搜尋結果與空白狀態的完整工作階段名稱';
  f.work.data.roots[0].name=project;f.work.data.session.title=session;f.work.data.work.title=session;
  await mountConversation(page,false,f.dispatch);await page.goto(route);
  if(width<1000)await page.locator('#workspace-menu').click();
  const toggle=page.getByRole('button',{name:'Expand '+project,exact:true});if(await toggle.count())await toggle.click();
  for(const [selector,name] of [[`.workspace-project-row .workspace-tree-link`,project],[`.workspace-tree a[href="${workspaceRoute}"]`,session]]) {
    const row=page.locator(selector).first(),label=row.locator('.workspace-tree-label');
    await expect(row).toHaveAttribute('title',new RegExp(name));await expect(row).toHaveAccessibleName(new RegExp(name));
    await expect(label).toHaveCSS('white-space','nowrap');await expect(label).toHaveCSS('text-overflow','ellipsis');
    // A live refresh may replace the row between locator resolution and evaluation.
    // Retry the same layout assertions on a connected row, including visible width.
    await expect(async()=>{
      const size=await row.evaluate(node=>{
        const label=node.querySelector('.workspace-tree-label')!;
        return {connected:node.isConnected,scroll:label.scrollWidth,width:label.clientWidth,
          height:label.getBoundingClientRect().height,line:parseFloat(getComputedStyle(label).lineHeight),rowHeight:node.getBoundingClientRect().height};
      });
      expect(size.connected).toBe(true);expect(size.width).toBeGreaterThan(0);
      expect(size.scroll).toBeGreaterThan(size.width);expect(size.height).toBeLessThanOrEqual(size.line+1);
      expect(size.rowHeight).toBe(width<1000?44:36);
    }).toPass({timeout:5000});
    await expect(row.locator('.workspace-tree-state .ui-icon, .workspace-tree-state .workspace-dot')).toBeVisible();
    if(width===1100)await expect(row.locator('.workspace-tree-state-label')).toBeHidden();
  }
  expect(f.pm.base.state.writes).toHaveLength(0);
});
test('partial linked projects are disclosed without pretending the set is complete',async({page})=>{
  const f=fixture();await mountConversation(page,false,async input=>{
    const result=await f.dispatch(input);
    if(input.path.split('?')[0]===`/pm/workspaces/${workspaceId}/projects`)result.data.bindings.push({workspace_id:workspaceId,project_id:'prj_'+'f'.repeat(20)});
    return result;
  });await page.goto(route);
  await expect(page.locator('.workspace-nav')).toHaveClass(/has-pm-context/);
  await expect(page.locator('[data-bound-partial]')).toHaveText('Some linked projects are unavailable');
  await expect(page.locator('[data-bound-partial]')).toBeVisible();
  await expect(page.locator('.workspace-tree').first()).toContainText('Documentation search');
  expect(f.pm.base.state.writes).toHaveLength(0);
});
for(const requested of ['dependencies','unrecognized']) test(`unavailable PM view ${requested} canonicalizes its URL on mount and subsequent navigation`,async({page})=>{
  const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(route+'/'+requested);
  await expect(page).toHaveURL(new RegExp('/overview$'));await expect(page.locator('[data-pm-view=overview]')).toHaveAttribute('aria-current','page');
  await openPmSection(page,'requirements');await page.evaluate(({id,view})=>{location.hash=`#/pm/${id}/${view}`;},{id:workspaceId,view:requested});
  await expect(page).toHaveURL(new RegExp('/requirements$'));await expect(page.locator('[data-pm-view=requirements]')).toHaveAttribute('aria-current','page');
});
for(const status of [403,404])test(`withdrawn remembered workspace is not reopened after ${status}`,async({page})=>{
  const f=fixture();let denied=false;await mountConversation(page,false,async input=>denied&&input.path===`/pm/workspaces/${workspaceId}`
    ?{status,data:{error:{code:status===403?'FORBIDDEN':'NOT_FOUND',message:'Workspace removed'}}}:f.dispatch(input));
  await page.goto(route);await expect(page.locator('.shell-workspace-block')).toHaveAttribute('data-workspace-id',workspaceId);
  denied=true;f.publish('pm_workspace');await expect(page.locator('.workspace-nav')).not.toHaveClass(/has-pm-context/);
  await page.evaluate(()=>{location.hash='#/pm';});await expect(page).toHaveURL(/#\/pm$/);
  await expect(page.locator('.workspace-nav')).not.toHaveClass(/has-pm-context/);expect(f.pm.base.state.writes).toHaveLength(0);
});
test('global breadcrumb has exactly the current view and no fallback Sessions label',async({page})=>{
  const f=fixture();await mountConversation(page,false,async input=>input.path.startsWith('/work-items/example')?{status:404,data:{error:{code:'NOT_FOUND',message:'Fixture missing'}}}:f.dispatch(input));await page.goto('/dashboard/#/home');
  await expect(page.locator('#nav .shell-crumb-view')).toHaveText('Pending');
  await expect(page.locator('#nav').getByRole('link')).toHaveCount(0);
  for(const [path,label] of [['approvals','Batch approvals'],['start','Start a new session'],['published','Start from a published version'],['orchestrate','Orchestrate work'],['op/example','Operation'],['host/demo','Host'],['task/demo/example','Execution'],['worktree/demo/example','Worktree'],['dispatch','Quick project dispatch'],['item/example','Work item'],['work/example','Work and results']]){
    await page.evaluate(path=>{location.hash='#/'+path;},path);
    await expect(page.locator('#nav .shell-crumb-view')).toHaveCount(1);
    await expect(page.locator('#nav .shell-crumb-view')).toHaveText(label);
    expect(await page.locator('#nav .shell-crumb-view').innerText()).not.toMatch(/^(ui_|obs_|nav_|task_|dispatch_)/);
  }
  await page.evaluate(()=>{location.hash='#/unknown';});await expect(page.locator('#nav .shell-crumb-view')).toHaveCount(0);
});

for(const theme of ['light','dark'])test(`computed button, pill and muted text contrast meets 4.5 ${theme}`,async({page},info)=>{
  await page.emulateMedia({colorScheme:theme as 'light'|'dark'});await page.goto('/ui-components.html');
  const ratios=await page.evaluate(async()=>{
    const {button,statusPill}=await import('/src/ui/components.js');
    const root=document.getElementById('fixture')!;
    root.append(button({variant:'primary',label:'Send'}),button({variant:'danger',label:'Remove'}));
    for(const key of ['accepted','done','waiting','blocked','in_progress','in_review','unknown'])root.append(statusPill(key));
    const probe=document.createElement('span');probe.textContent='Muted';probe.style.color='var(--text-3)';root.append(probe);
    const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d')!;
    const rgb=(color:string)=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].slice(0,3);};
    const lum=(channels:number[])=>channels.map(x=>{const v=x/255;return v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4;}).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
    const ratio=(a:string,b:string)=>{const x=lum(rgb(a)),y=lum(rgb(b));return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);};
    const result:Record<string,number>={};
    for(const node of root.children){const style=getComputedStyle(node);let parent:Element|null=node,bg='rgba(0, 0, 0, 0)';while(parent&&bg==='rgba(0, 0, 0, 0)'){bg=getComputedStyle(parent).backgroundColor;parent=parent.parentElement;}result[node.textContent!]=ratio(style.color,bg);}
    for(const surface of ['--bg','--surface','--surface-2']){probe.style.background=`var(${surface})`;const style=getComputedStyle(probe);result['Muted '+surface]=ratio(style.color,style.backgroundColor);}
    return result;
  });
  await info.attach('computed-contrast',{body:JSON.stringify(ratios,null,2),contentType:'application/json'});
  console.log(theme+' contrast '+JSON.stringify(ratios));for(const [label,ratio]of Object.entries(ratios))expect(ratio,label).toBeGreaterThanOrEqual(4.5);
  const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto('/dashboard/'+workspaceRoute);
  const send=page.getByRole('button',{name:'Send',exact:true});await expect(send).toBeVisible();
  await expect(send).toHaveCSS('color',theme==='dark'?'rgb(11, 14, 20)':'rgb(255, 255, 255)');
});

test('global Projects starts at the rail padding and New workspace remains discoverable',async({page})=>{
  const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto('/dashboard/#/home');
  const rail=page.locator('.workspace-nav'),heading=rail.locator('.workspace-nav-heading');await expect(heading).toBeVisible();
  const bounds=await rail.boundingBox(),first=await heading.boundingBox();
  expect(first!.y-bounds!.y).toBe(16);
  await rail.getByRole('button',{name:'Tools',exact:true}).click();
  await expect(rail.getByRole('menuitem',{name:'New workspace',exact:true})).toBeVisible();
});

for (const composing of [{isComposing:true}, {keyCode:229}]) test(`IME Escape keeps the mobile drawer open ${JSON.stringify(composing)}`, async ({page}) => {
  await page.setViewportSize({width:390,height:844});const f=fixture();await mountConversation(page,false,f.dispatch);await page.goto(route);
  const opener=page.locator('#workspace-menu');await opener.click();const drawer=page.locator('.workspace-nav');
  await drawer.dispatchEvent('keydown',{key:'Escape',bubbles:true,...composing});await expect(drawer).toBeVisible();await expect(drawer).toHaveAttribute('aria-modal','true');
  await page.keyboard.press('Escape');await expect(drawer).toBeHidden();await expect(opener).toBeFocused();expect(f.pm.base.state.writes).toHaveLength(0);
});
