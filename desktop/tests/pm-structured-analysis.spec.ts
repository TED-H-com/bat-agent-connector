import {test,expect} from '@playwright/test';
import {proposalFixture,mountProposal,bootProposal,editor,PROPOSE} from './pm-plan-proposals-fixture.ts';
import {structuredCapability,NATIVE,BAT} from './pm-structured-analysis-fixture.ts';
const runtime=(page:any)=>editor(page,PROPOSE).locator('[data-pm-structured-analysis]');
const select=(page:any)=>runtime(page).locator('select');
const submit=(page:any)=>editor(page,PROPOSE).getByRole('button',{name:'Prepare plan update',exact:true});

test('legacy proposal keeps native envelope without a selector or adapter parameter',async({page})=>{
  const f=proposalFixture();await mountProposal(page,f);await expect(select(page)).toBeHidden();
  await editor(page,PROPOSE).locator('textarea').fill('A legacy native request');await submit(page).click();
  await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.params).toEqual({purpose:'A legacy native request'});
});
for(const locale of ['en','zh-TW'])test(`explicit BAT selection remains a bounded preflight, ${locale}`,async({page},info)=>{
  const f=proposalFixture();f.state.eligible=false;f.state.structured_analysis=structuredCapability();await mountProposal(page,f,locale);
  await expect(select(page)).toHaveValue(NATIVE);await expect(select(page)).toBeEnabled();
  await expect(editor(page,PROPOSE).locator('button[type=submit]')).toBeDisabled();await select(page).selectOption(BAT);
  await expect(editor(page,PROPOSE).locator('textarea')).toBeEnabled();await editor(page,PROPOSE).locator('textarea').fill('Compare the exact accepted scope and unresolved recovery question.');
  await expect(runtime(page)).toContainText('fixture-bat / fixture-project / example/member-access');
  for(const width of [390,768,1440]){await page.setViewportSize({width,height:900});await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:info.outputPath(`structured-${locale}-${width}.png`),fullPage:true});}
  await editor(page,PROPOSE).locator('button[type=submit]').click();await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params.adapter).toBe(BAT);
});
test('BAT draft survives reload and remains blocked when the capability disappears',async({page})=>{
  const f=proposalFixture();f.state.structured_analysis=structuredCapability();await mountProposal(page,f);await select(page).selectOption(BAT);
  await editor(page,PROPOSE).locator('textarea').fill('Keep the BAT draft');await page.reload();await bootProposal(page);
  await expect(select(page)).toHaveValue(BAT);await expect(editor(page,PROPOSE).locator('textarea')).toHaveValue('Keep the BAT draft');
  f.state.structured_analysis=null;await page.evaluate(()=>(window as any).proposalRefresh());
  await expect(select(page)).toHaveValue(BAT);await expect(submit(page)).toBeDisabled();expect(f.state.writes).toHaveLength(0);
  await select(page).selectOption(NATIVE);await expect(select(page)).toBeHidden();await expect(submit(page)).toBeEnabled();
});
test('missing descriptor, unknown capability version and unauthorized adapter never imply BAT availability',async({page})=>{
  const f=proposalFixture();f.state.structured_analysis=structuredCapability();f.state.structured_analysis.actions['pm.conversation.propose'].adapters[1].authorized=false;
  await mountProposal(page,f);await select(page).selectOption(BAT);await expect(submit(page)).toBeDisabled();
  f.state.structured_analysis.actions={};await page.evaluate(()=>(window as any).proposalRefresh());await expect(submit(page)).toBeDisabled();
  f.state.structured_analysis.version=2;await page.evaluate(()=>(window as any).proposalRefresh());await expect(select(page)).toHaveValue(BAT);await expect(submit(page)).toBeDisabled();expect(f.state.writes).toHaveLength(0);
});
test('unknown BAT admission retains its original adapter and key across reload and capability withdrawal',async({page})=>{
  const f=proposalFixture();f.state.structured_analysis=structuredCapability();f.state.lost=true;await mountProposal(page,f);await select(page).selectOption(BAT);
  await editor(page,PROPOSE).locator('textarea').fill('One original BAT request');await submit(page).click();
  await expect(editor(page,PROPOSE)).toContainText('Original reply unavailable');await expect(select(page)).toBeDisabled();
  const original=structuredClone(f.state.writes[0]);f.state.structured_analysis=null;await page.reload();await bootProposal(page);
  await expect(select(page)).toHaveValue(BAT);await expect(select(page)).toBeDisabled();
  await editor(page,PROPOSE).getByRole('button',{name:'Check original request',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(2);
  expect(f.state.writes[1]).toEqual(original);expect(f.state.ops.size).toBe(1);
});
test('legacy pending intent still omits adapter after a server adds structured capability',async({page})=>{
  const f=proposalFixture();f.state.lost=true;await mountProposal(page,f);await editor(page,PROPOSE).locator('textarea').fill('Original legacy request');await submit(page).click();
  await expect(editor(page,PROPOSE)).toContainText('Original reply unavailable');const original=structuredClone(f.state.writes[0]);f.state.structured_analysis=structuredCapability({native:true});
  await page.reload();await bootProposal(page);await expect(select(page)).toHaveValue(NATIVE);await expect(select(page)).toBeDisabled();
  await editor(page,PROPOSE).getByRole('button',{name:'Check original request',exact:true}).click();await expect.poll(()=>f.state.writes.length).toBe(2);
  expect(f.state.writes[1]).toEqual(original);expect(original.body.params.adapter).toBeUndefined();
});
test('new native selection is explicit in the version1 envelope',async({page})=>{
  const f=proposalFixture();f.state.structured_analysis=structuredCapability({native:true});await mountProposal(page,f);
  await expect(select(page)).toHaveValue(NATIVE);await editor(page,PROPOSE).locator('textarea').fill('Use the central native runtime');await submit(page).click();
  await expect.poll(()=>f.state.writes.length).toBe(1);expect(f.state.writes[0].body.params.adapter).toBe(NATIVE);
});
test('known pending BAT operation locks selection and polls only the original receipt',async({page})=>{
  const f=proposalFixture();f.state.structured_analysis=structuredCapability();const dispatch=f.dispatch;
  let release:()=>void=()=>{};const gate=new Promise<void>(resolve=>{release=resolve;});let reads=0;
  f.dispatch=async input=>{const result=await dispatch(input);
    if(input.method==='POST'){result.data.operation.status='running';for(const op of f.state.ops.values())op.status='running';}
    if(input.path.startsWith('/operations/')){reads++;await gate;result.data.operation.status='succeeded';}
    return result;
  };
  try{
    await mountProposal(page,f);await select(page).selectOption(BAT);await editor(page,PROPOSE).locator('textarea').fill('Wait for this original runtime');await submit(page).click();
    await expect.poll(()=>reads).toBe(1);await expect(select(page)).toBeDisabled();
    f.state.structured_analysis=null;await page.evaluate(()=>(window as any).proposalRefresh());await expect(select(page)).toHaveValue(BAT);await expect(select(page)).toBeDisabled();
    release();await expect.poll(()=>f.state.writes.length).toBe(1);await expect(editor(page,PROPOSE).getByRole('button',{name:'Check original request',exact:true})).toHaveCount(0);
    expect(f.state.ops.size).toBe(1);expect(reads).toBe(1);
  }finally{release();}
});
