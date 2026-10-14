import {openPmSection, expectPmWithdrawn} from './pm-navigation.ts';
import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {pmFixture,workspaceId} from './pm-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';
import {governanceFixture,action,openAction,reason} from './pm-governance-fixture.ts';

function fixture(){
  const base=pmFixture(),governance=governanceFixture(),prefix=`/pm/workspaces/${workspaceId}`;
  governance.state.actor='reader';
  const dispatch=async(input:any)=>{
    const path=new URL(input.path,'http://fixture').pathname;
    if(path==='/bootstrap'||path==='/capabilities'){
      const result=await base.dispatch(input),caps=path==='/bootstrap'?result.data.capabilities:result.data;
      caps.features.pm_workspaces.governance=true;caps.actions.push(...governance.config().caps.actions);return result;
    }
    if(path==='/events'){
      const result=await base.dispatch(input);for(const event of result.data.events)event.resource_type='pm_governance';return result;
    }
    if(path.startsWith(prefix+'/governance')||path.startsWith(prefix+'/snapshots')||path.startsWith(prefix+'/plans')
      ||path.startsWith('/operations/')||input.method==='POST'&&input.body?.action?.startsWith('pm.governance.'))return governance.dispatch(input);
    if(path===prefix&&!base.state.failRead){
      const result=await base.dispatch(input);result.data.workspace.members=[{actor:'reader',role:base.state.role},{actor:'developer',role:'contributor'}];return result;
    }
    return base.dispatch(input);
  };
  return{base,governance,dispatch,publish:()=>base.state.cursor++};
}
const route=`/dashboard/#/pm/${workspaceId}`,region='[data-pm-governance]';
for(const native of [false,true])test.describe(native?'native governance workspace':'web governance workspace',()=>{
  test('source-backed decisions stay in the workspace and refresh from another client',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'issues');
    await expect(page.locator(region)).toContainText('Mail delivery remains unverified');
    await expect(page.getByRole('tab')).toHaveCount(0);await expect(action(page,'record')).toBeHidden();
    const first=f.governance.state.records[0];first.revision.document.title='A reviewed source from another client';
    first.record.version++;first.revision.version++;f.publish();
    await expect(page.locator(region)).toContainText('A reviewed source from another client',{timeout:10000});
    expect(f.governance.state.writes).toHaveLength(0);
  });
  test('a lost finding response reloads its original request without creating a second finding',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'issues');await openAction(page,'record');
    const editor=action(page,'record');await editor.getByLabel('Explicit root cause key',{exact:true}).fill('first-release-recovery');
    await editor.getByLabel('Finding title',{exact:true}).fill('Verify recovery before release');
    await editor.getByLabel('What was observed and why it matters',{exact:true}).fill('The selected mail route remains unverified.');
    await editor.getByRole('checkbox').first().check();await reason(page,'record');f.governance.state.lost=true;
    await editor.getByRole('button',{name:'Save finding',exact:true}).click();await expect(editor).toContainText('Reply unavailable');
    const request=structuredClone(f.governance.state.writes[0]);await page.reload();await openPmSection(page, 'issues');
    await action(page,'record').getByRole('button',{name:'Check original request',exact:true}).click();
    expect(f.governance.state.writes.at(-1)).toEqual(request);expect(f.governance.state.ops.size).toBe(1);
    await expect(page.locator('[data-pm-governance-detail]')).toContainText('Verify recovery before release');
  });
  test('failed workspace reads hide source facts and restore unsent wording after recovery',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'issues');await openAction(page,'revise');
    const field=action(page,'revise').getByLabel('Finding title',{exact:true});await field.fill('Keep this unsubmitted finding');
    f.base.state.failRead=true;f.publish();await expect(page.locator(region)).toHaveJSProperty('hidden',true,{timeout:10000});await expectPmWithdrawn(page,'issues',region);
    await expect(page.locator('.shell-workspace-block')).not.toContainText('Library refresh');
    f.base.state.failRead=false;f.publish();await expect(page.locator(region)).toBeVisible({timeout:10000});
    await expect(field).toHaveValue('Keep this unsubmitted finding');expect(f.governance.state.writes).toHaveLength(0);
  });
  test('a contributor cannot decide directions through the mounted application',async({page})=>{
    const f=fixture();f.base.state.role='contributor';f.governance.state.role='contributor';await mountConversation(page,native,f.dispatch);await page.goto(route);await openPmSection(page, 'issues');
    await page.locator(`[data-pm-governance-row="${f.governance.state.records[1].record.record_id}"]`).getByRole('button').click();
    await expect(action(page,'decide')).toHaveCount(0);await expect(action(page,'adoption.link')).toHaveCount(0);
    expect(f.governance.state.writes).toHaveLength(0);
  });
});
for(const locale of ['en','zh-TW'])for(const width of [390,768,1440])test(`governance workspace ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:1000}}),page=await context.newPage(),f=fixture();
  await mountConversation(page,false,f.dispatch);await page.goto(route);await openPmSection(page, 'issues');await expect(page.locator(region)).toContainText('Mail delivery remains unverified');
  expect(await page.locator('.pm-main').innerText()).not.toMatch(/pm_gov_|\bundefined\b/);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const directory=process.env.BATC_PM_GOVERNANCE_SCREENSHOTS;
  if(directory){await mkdir(directory,{recursive:true});await page.screenshot({path:`${directory}/workspace-${locale}-${width}.png`,fullPage:true});}
  await context.close();
});
