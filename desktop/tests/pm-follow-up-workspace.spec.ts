import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {pmFixture,workspaceId} from './pm-fixture.ts';
import {fixture as followUpFixture,snapshotId} from './pm-follow-up-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';

function fixture(){
  const base=pmFixture(),followUp=followUpFixture(),prefix=`/pm/workspaces/${workspaceId}`;
  followUp.state.actor=base.state.actor;
  const dispatch=async(input:any)=>{
    const p=new URL(input.path,'http://fixture').pathname;
    if(p==='/bootstrap'||p==='/capabilities'){
      const result=await base.dispatch(input),caps=p==='/bootstrap'?result.data.capabilities:result.data;
      caps.features.pm_workspaces.follow_up_prepare=true;caps.actions.push(...followUp.caps().actions);return result;
    }
    if(p==='/events'){
      const result=await base.dispatch(input);for(const event of result.data.events)event.resource_type='pm_follow_up';return result;
    }
    if(p.startsWith(prefix+'/follow-ups')||p.startsWith(prefix+'/snapshots')||p===prefix+'/plans/head'
      ||p.startsWith(prefix+'/threads')||p.startsWith(prefix+'/governance')||p.startsWith(prefix+'/meetings')
      ||p.startsWith('/operations/')||input.method==='POST'&&input.body?.action==='pm.follow_up.prepare')
      return followUp.dispatch({...input,key:input.idempotency_key});
    return base.dispatch(input);
  };
  return{base,followUp,dispatch};
}
const route=`/dashboard/#/pm/${workspaceId}`,section=(page:any)=>page.locator('[data-pm-follow-up]'),editor=(page:any)=>page.locator('[data-pm-fu-editor]');
async function open(page:any){
  if(!await editor(page).evaluate((node:HTMLDetailsElement)=>node.open))await editor(page).locator(':scope > summary').click();
  await expect(page.getByLabel('Evidence snapshot',{exact:true})).toBeEnabled();
}
async function select(page:any){await open(page);await page.getByLabel('Evidence snapshot',{exact:true}).selectOption(snapshotId);await expect(editor(page)).toContainText('Selections stay fixed.');}
for(const native of [false,true])test.describe(native?'native workspace Follow-up':'web workspace Follow-up',()=>{
  test('fixed management knowledge and later answers retain their distinct provenance',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);
    await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(8);
    await expect(page.locator('[data-pm-fu-agenda] ol > li')).toHaveCount(1);
    await expect(page.locator('[data-pm-fu-domain=finance]')).toContainText('The first paid tier price is undecided.');
    await expect(page.locator('[data-pm-fu-domain=finance]')).toContainText('The accepted plan remains unchanged');
    await expect(section(page)).toContainText('No AI Follow-up, scope change or work execution occurred');
    expect(f.followUp.state.writes).toHaveLength(0);await expect(page.getByRole('tab')).toHaveCount(0);
    f.followUp.state.rows.at(-1).document.agenda[0].text='A sourced agenda update from another client';f.base.state.cursor++;
    await expect(section(page)).toContainText('A sourced agenda update from another client',{timeout:10000});
    expect(f.followUp.state.writes).toHaveLength(0);
  });
  test('explicit preparation recovers the original request after a missing reply and reload',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await select(page);f.followUp.state.lost=true;
    await page.getByRole('button',{name:'Save discussion preparation',exact:true}).click();await expect(editor(page)).toContainText('The original reply was lost');
    const original=structuredClone(f.followUp.state.writes[0]);await page.reload();
    if(!await editor(page).evaluate((node:HTMLDetailsElement)=>node.open))await editor(page).locator(':scope > summary').click();
    await page.getByRole('button',{name:'Check original request',exact:true}).click();
    await expect(section(page)).toContainText('Preparation 4');
    expect(f.followUp.state.ops.size).toBe(1);expect(f.followUp.state.writes.at(-1).body).toEqual(original.body);
    expect(f.followUp.state.writes.at(-1).key).toBe(original.key);expect(original.body.preconditions).toEqual({expected_version:3,expected_plan_version:2});
    expect(original.body.params.snapshot_id).toBe(snapshotId);expect(f.base.state.writes).toHaveLength(0);
  });
  test('failed parent read hides knowledge and preserves unsent fixed selections',async({page})=>{
    const f=fixture();await mountConversation(page,native,f.dispatch);await page.goto(route);await select(page);
    f.base.state.failRead=true;f.base.state.cursor++;
    await expect(section(page)).toBeHidden({timeout:10000});
    await expect(page.locator('.pm-heading h1')).not.toContainText('Library refresh');
    f.base.state.failRead=false;f.base.state.cursor++;
    await expect(section(page)).toBeVisible({timeout:10000});await expect(page.getByLabel('Evidence snapshot',{exact:true})).toHaveValue(snapshotId);
    expect(f.followUp.state.writes).toHaveLength(0);
  });
});
for(const locale of ['en-US','zh-TW'])for(const width of [390,768,1440])test(`workspace Follow-up layout ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=fixture();
  await mountConversation(page,false,f.dispatch);await page.goto(route);await expect(page.locator('[data-pm-fu-domain]')).toHaveCount(8);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const out=process.env.PM_FOLLOW_UP_WORKSPACE_SCREENSHOTS;
  if(out){await mkdir(out,{recursive:true});await page.screenshot({path:`${out}/${locale}-${width}.png`,fullPage:true});}
  await context.close();
});
