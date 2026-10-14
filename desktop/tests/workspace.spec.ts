import {test, expect} from '@playwright/test';
import {workspaceFixture, mountWorkspace, workspaceRoute} from './workspace-fixture';
for (const native of [false,true]) {
  test(`${native?'IPC':'HTTP'} workspace tree opens conversation and result together and preserves drafts`,async({page})=>{
    const f=workspaceFixture(); await mountWorkspace(page,native,f); await page.goto('/dashboard/#/home');
    const tree=page.locator('.workspace-nav'); await tree.getByRole('link',{name:/Improve empty search results/}).click();
    await expect(page).toHaveURL(new RegExp('/work/'));
    await expect(page.locator('.conversation')).toContainText('Tests are still running');
    await expect(page.locator('[data-workspace-result]')).toContainText('Review result and add to PR');
    const draft=page.getByPlaceholder('Message for this managed session…'); await draft.fill('Keep this draft');
    f.data.messages.push({id:'next',role:'assistant',ts:'2026-10-10T12:03:00Z',text:'Another observation'});
    f.data.session.streaming=false;f.data.events.push({seq:1,resource_type:'session',resource_id:'demo/search-results',kind:'session.updated'});
    await expect(page.locator('.conversation')).toContainText('Another observation');
    await expect(draft).toHaveValue('Keep this draft'); await expect(draft).toBeFocused();
    await expect(tree.getByRole('link',{name:/Improve empty search results/})).toHaveAttribute('aria-current','page');
    await expect(page.locator('.workspace-session-heading')).not.toContainText('Completed');
    await tree.getByRole('link',{name:/^Documentation search$/}).click();await page.goBack(); await expect(draft).toHaveValue('Keep this draft');
    await draft.fill('Continue with the tests'); await page.getByRole('button',{name:'Send',exact:true}).click();
    await expect.poll(()=>f.data.posts.length).toBe(1);
    expect(f.data.posts[0].body).toMatchObject({action:'session.send',target:{host:'demo',session_id:'search-results'},params:{text:'Continue with the tests'}});
    expect(f.data.posts[0].idempotency_key).toBeTruthy();
  });
  test(`${native?'IPC':'HTTP'} workspace refresh failure retains tree and blocks checkpoint`,async({page})=>{
    const f=workspaceFixture(); await mountWorkspace(page,native,f); await page.goto('/dashboard/'+workspaceRoute);
    await expect(page.locator('.conversation')).toContainText('Tests are still running');
    f.data.failTree=true;f.data.events.push({seq:1,resource_type:'project',resource_id:f.data.detail.project.project_id,kind:'project.updated'});
    await expect(page.locator('.workspace-tree-status')).toContainText('Tree read failed');
    await expect(page.locator('.workspace-tree-scroll > .workspace-tree')).toContainText('Improve empty search results'); expect(f.data.after).toBe(0);
    f.data.failTree=false; await expect.poll(()=>f.data.after,{timeout:10000}).toBe(1);expect(f.data.posts).toEqual([]);
  });
  test(`${native?'IPC':'HTTP'} linked work item opens conversation with its review controls`,async({page})=>{
    const f=workspaceFixture();f.data.linkedItem=true;f.data.detail.work_items=[f.fixture.data.items[0]];
    await mountWorkspace(page,native,f);await page.goto('/dashboard/#/home');
    await page.locator('.workspace-tree-scroll > .workspace-tree').getByRole('link',{name:/Review delivery/}).click();
    await expect(page.locator('.conversation')).toContainText('Tests are still running');
    await expect(page.locator('.workspace-work-item')).toContainText('Review delivery');
    await expect(page.locator('.workspace-work-item').getByRole('button',{name:'Accept as done',exact:true})).toBeDisabled();
    expect(f.data.posts).toEqual([]);
  });
  test(`${native?'IPC':'HTTP'} missing project work does not fall back to an unrelated session`,async({page})=>{
    const f=workspaceFixture(); f.data.detail.work=[];await mountWorkspace(page,native,f);await page.goto('/dashboard/'+workspaceRoute);
    await expect(page.locator('#main')).toContainText('This work is no longer');await expect(page.locator('.conversation')).toHaveCount(0);
    expect(f.data.posts).toEqual([]);
  });
  test(`${native?'IPC':'HTTP'} tree collapse persists and reopening reads current work`,async({page})=>{
    const f=workspaceFixture();await mountWorkspace(page,native,f);await page.goto('/dashboard/#/home');
    await page.getByRole('button',{name:'Collapse Documentation search',exact:true}).click();
    await page.reload();const expand=page.getByRole('button',{name:'Expand Documentation search',exact:true});
    await expect(expand).toHaveAttribute('aria-expanded','false');
    f.data.work.title='Renamed work';await expand.click();
    const link=page.locator('.workspace-nav').getByRole('link',{name:/Renamed work/});await expect(link).toBeVisible();
    await link.focus();f.data.events.push({seq:1,resource_type:'session',resource_id:'demo/search-results',kind:'session.updated'});
    await expect.poll(()=>f.data.after,{timeout:10000}).toBe(1);await expect(link).toBeFocused();
    expect(f.data.posts).toEqual([]);
  });
  test(`${native?'IPC':'HTTP'} collapsed parent stays closed across updates and reloads`,async({page})=>{
    const f=workspaceFixture();
    const parent={...f.data.roots[1],name:'Parent project',children:[f.data.roots[0]]};
    f.data.roots.splice(0,2,parent);
    await mountWorkspace(page,native,f);await page.goto('/dashboard/'+workspaceRoute);
    const collapse=page.getByRole('button',{name:'Collapse Parent project',exact:true});
    await expect(collapse).toBeVisible();
    await expect(page.locator('.workspace-nav').getByRole('link',{name:/Improve empty search results/})).toBeVisible();
    await collapse.click();
    const expand=page.getByRole('button',{name:'Expand Parent project',exact:true});
    f.data.events.push({seq:1,resource_type:'project',resource_id:parent.project_id,kind:'project.updated'});
    await expect.poll(()=>f.data.after,{timeout:10000}).toBe(1);
    await expect(expand).toHaveAttribute('aria-expanded','false');
    await page.goto('/dashboard/#/home');await page.reload();
    await expect(expand).toHaveAttribute('aria-expanded','false');
    await page.goto('/dashboard/'+workspaceRoute);
    await expect(collapse).toBeVisible();
    await expect(page.locator('.workspace-nav').getByRole('link',{name:/Improve empty search results/})).toBeVisible();
    expect(f.data.posts).toEqual([]);
  });
  test(`${native?'IPC':'HTTP'} unknown and manual work remain read only`,async({page})=>{
    const f=workspaceFixture(); f.data.session.api_access='read_only'; f.data.session.provenance='manual';
    await mountWorkspace(page,native,f); await page.goto('/dashboard/'+workspaceRoute);
    await expect(page.getByRole('button',{name:'Send',exact:true})).toBeHidden();
    await expect(page.locator('[data-session-readonly]')).toContainText("read-only here");
    await expect(page.locator('.workspace-composer')).toBeHidden();expect(f.data.posts).toEqual([]);
  });
}
for(const locale of ['en-US','zh-TW']) for(const width of [390,768,1440]) test(`workspace layout ${locale} ${width}`,async({page})=>{
  const f=workspaceFixture(locale);await mountWorkspace(page,false,f);await page.setViewportSize({width,height:900});
  await page.addInitScript(locale=>Object.defineProperty(navigator,'language',{value:locale}),locale);await page.emulateMedia({colorScheme:'dark'});
  await page.goto('/dashboard/'+workspaceRoute);await expect(page.locator('.conversation .msg')).toHaveCount(2);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:`test-results/workspace-${locale}-${width}.png`,fullPage:true});
  if(width<801){
    for (const button of await page.locator('.workspace-composer button').all()) expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await page.locator('#workspace-menu').click();await expect(page.locator('.workspace-nav')).toBeVisible();await page.locator('.workspace-tree a[href^="#/work/"]').first().click();await expect(page.locator('.workspace-nav')).toBeHidden();}
});
