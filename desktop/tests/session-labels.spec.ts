import {test,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {labelsFixture,openLabels,target,opId} from './session-labels-fixture';
for(const native of [false,true]){
 const name=native?'native':'browser';
 test(`${name}: manual and unknown use local labels with host writes disabled`,async({page})=>{
  const state=await labelsFixture(page,native,{provenance:'unknown'}),panel=await openLabels(page);
  await panel.getByRole('textbox').fill('待確認\nUI');await panel.getByRole('button',{name:'Save labels',exact:true}).click();
  await expect(panel).toContainText('This label change was saved');
  expect(state.posts[0].body).toEqual({action:'session.labels.set',target,params:{labels:['待確認','UI']},preconditions:{expected_version:0}});
  await expect(panel.getByRole('textbox')).toBeDisabled();
  await page.reload();await expect(page.locator('[data-session-labels] .chip')).toHaveText(['待確認','UI']);
  expect(state.posts).toHaveLength(1);expect(state.errors).toEqual([]);
 });
 test(`${name}: lost reply keeps exact labels version and key across reload and later edits`,async({page})=>{
  const state=await labelsFixture(page,native,{lost:true});let panel=await openLabels(page);
  await panel.getByRole('textbox').fill('Original');await panel.getByRole('button',{name:'Save labels',exact:true}).click();
  await expect(panel).toContainText('Lost label reply');state.metadata={version:8,labels:['Other person']};
  await page.reload();panel=page.locator('[data-session-labels]');await panel.locator('summary').click();
  await expect(panel.getByRole('textbox')).toHaveValue('Original');
  await panel.getByRole('button',{name:'Retry original request'}).click();await expect(panel).toContainText('This label change was saved');
  expect(state.posts[1]).toEqual(state.posts[0]);
  await expect(panel.locator('.chip')).toHaveText(['Other person']);
  await panel.getByRole('button',{name:'Review current version and prepare change'}).click();
  await expect(panel.getByRole('textbox')).toHaveValue('Other person');expect(state.posts).toHaveLength(2);
 });
 test(`${name}: version conflict keeps draft until explicit rebase without auto-save`,async({page})=>{
  const state=await labelsFixture(page,native,{refuse:'METADATA_VERSION_CONFLICT'});let panel=await openLabels(page);
  await panel.getByRole('textbox').fill('My draft');state.metadata={version:2,labels:['Other person']};
  await panel.getByRole('button',{name:'Save labels',exact:true}).click();await expect(panel).toContainText('METADATA_VERSION_CONFLICT');
  await page.reload();panel=page.locator('[data-session-labels]');await panel.locator('summary').click();
  await expect(panel.getByRole('textbox')).toHaveValue('My draft');
  await panel.getByRole('button',{name:'Review current version and prepare change'}).click();
  await expect(panel.getByRole('textbox')).toHaveValue('My draft');expect(state.posts).toHaveLength(1);
  state.refuse=null;await panel.getByRole('button',{name:'Save labels',exact:true}).click();
  await expect(panel).toContainText('This label change was saved');
  expect(state.posts[1].body.preconditions.expected_version).toBe(2);expect(state.posts[1].idempotency_key).not.toBe(state.posts[0].idempotency_key);
 });
 test(`${name}: wrong key and ambiguous auth never unlock a possibly accepted intent`,async({page})=>{
  const state=await labelsFixture(page,native,{badKey:true});const panel=await openLabels(page);
  await panel.getByRole('textbox').fill('Fixed');await panel.getByRole('button',{name:'Save labels',exact:true}).click();
  await expect(panel).toContainText('does not match');
  state.refuse='FORBIDDEN';await panel.getByRole('button',{name:'Retry original request'}).click();
  await expect(panel).toContainText('Your token lacks this scope');await expect(panel.getByRole('button',{name:'Review current version and prepare change'})).toBeHidden();
  await expect(panel.getByRole('textbox')).toBeDisabled();expect(state.posts[1]).toEqual(state.posts[0]);
  state.badKey=false;state.refuse=null;await panel.getByRole('button',{name:'Retry original request'}).click();
  await expect(panel).toContainText('This label change was saved');expect(state.posts[2]).toEqual(state.posts[0]);
 });
 test(`${name}: event before POST reply waits for original receipt and failed metadata read`,async({page})=>{
  const state=await labelsFixture(page,native,{delayPost:true,status:'running'});const panel=await openLabels(page);
  await panel.getByRole('textbox').fill('Fixed');await panel.getByRole('button',{name:'Save labels',exact:true}).click();
  await expect.poll(()=>Boolean(state.holdPost)).toBe(true);
  state.status='succeeded';state.metadata={version:1,labels:['Fixed']};state.failMetadata=true;
  state.events.push({seq:1,kind:'session.labels_updated',resource_type:'session',resource_id:'demo/manual-full-session'});
  await expect.poll(()=>state.delivered).toBe(true);expect(state.after).toBe(0);state.holdPost();
  await expect(panel).toContainText('Metadata read failed');expect(state.after).toBe(0);
  await expect(panel.getByRole('button',{name:'Review current version and prepare change'})).toBeDisabled();
  state.failMetadata=false;await expect.poll(()=>state.after).toBe(1);
  await expect(panel).toContainText('This label change was saved');expect(state.posts).toHaveLength(1);
 });
 test(`${name}: active draft survives label event and uses its originally observed version`,async({page})=>{
  const state=await labelsFixture(page,native);const panel=await openLabels(page);
  await panel.getByRole('textbox').fill('Unsent');state.metadata={version:3,labels:['Changed']};
  state.events.push({seq:1,kind:'session.labels_updated',resource_type:'session',resource_id:'demo/manual-full-session'});
  await expect.poll(()=>state.after).toBe(1);await expect(panel.getByRole('textbox')).toHaveValue('Unsent');
  await expect(panel).toContainText('newer version');state.refuse='METADATA_VERSION_CONFLICT';
  await panel.getByRole('button',{name:'Save labels',exact:true}).click();await expect.poll(()=>state.posts.length).toBe(1);
  expect(state.posts[0].body.preconditions.expected_version).toBe(0);
 });
 for(const options of [{scopes:['observe']},{allowed:null},{allowed:false},{metadata:null},{metadata:{version:1,labels:'wrong'}}])
 test(`${name}: positive capability and readable metadata gate ${JSON.stringify(options)}`,async({page})=>{
  const state=await labelsFixture(page,native,options);
  await page.goto('/dashboard/#/session/demo/manual-full-session');
  const panel=page.locator('[data-session-labels]');
  await expect(panel).toBeVisible();
  await expect(panel.locator('summary')).not.toContainText('Edit labels');
  await expect(panel.locator('.session-label-editor button')).toHaveCount(0);
  expect(state.posts).toHaveLength(0);
 });
 test(`${name}: unknown operation stays readback-only`,async({page})=>{
  const state=await labelsFixture(page,native,{status:'uncertain'}),panel=await openLabels(page);
  await panel.getByRole('textbox').fill('Fixed');await panel.getByRole('button',{name:'Save labels',exact:true}).click();
  await expect(panel.getByRole('link',{name:'View operation and step receipts'})).toHaveAttribute('href','#/op/'+opId);
  await panel.getByRole('button',{name:'Check original operation'}).click();
  await expect(panel.getByRole('button',{name:'Review current version and prepare change'})).toBeHidden();expect(state.posts).toHaveLength(1);
 });
 test(`${name}: draft namespace stays with original backend and principal`,async({page})=>{
  const state=await labelsFixture(page,native);let panel=await openLabels(page);
  await panel.getByRole('textbox').fill('Private original draft');
  state.principal='another-principal';state.actor='another';await page.reload();panel=page.locator('[data-session-labels]');await panel.locator('summary').click();
  await expect(panel.getByRole('textbox')).toHaveValue('');
  state.principal='labels-principal';state.actor='label-person';await page.reload();panel=page.locator('[data-session-labels]');await panel.locator('summary').click();
  await expect(panel.getByRole('textbox')).toHaveValue('Private original draft');expect(state.posts).toHaveLength(0);
 });
}
test('loaded session search matches labels while preserving title and grouping',async({page})=>{
 const state=await labelsFixture(page,false,{metadata:{version:1,labels:['Need-review','A','B']}});
 await page.goto('/dashboard/#/sessions');await expect(page.locator('.session-entry')).toHaveCount(1);
 await page.locator('#main input[type=search]').fill('need-review');await expect(page.locator('.session-entry')).toHaveCount(1);
 await expect(page.locator('.session-entry')).toContainText('Review the shared dashboard');await expect(page.locator('.session-entry')).toContainText('+1');
 await page.locator('#main input[type=search]').fill('not-loaded');await expect(page.locator('.session-entry')).toHaveCount(0);expect(state.posts).toHaveLength(0);
});
for(const locale of ['en-US','zh-TW'])for(const width of [390,768,1440])test(`labels layout ${locale} ${width}`,async({page})=>{
 await page.addInitScript(language=>Object.defineProperty(navigator,'language',{value:language}),locale);
 await labelsFixture(page,false,{metadata:{version:1,labels:['待確認','介面整理']}});await page.setViewportSize({width,height:900});
 const panel=await openLabels(page);await panel.getByRole('textbox').fill('待確認\n介面整理');
 await expect(panel).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await mkdir('/tmp/bac-labels-after',{recursive:true});await page.screenshot({path:`/tmp/bac-labels-after/${locale}-${width}.png`,fullPage:true});
});

for(const native of [false,true])test(`${native?'native':'browser'}: detached POST reply cannot read old session in a new view`,async({page})=>{
 const state=await labelsFixture(page,native,{delayPost:true});const panel=await openLabels(page);
 await panel.getByRole('textbox').fill('Fixed');await panel.getByRole('button',{name:'Save labels',exact:true}).click();
 await expect.poll(()=>Boolean(state.holdPost)).toBe(true);await page.goto('/dashboard/#/sessions');
 await expect(page.locator('.session-entry')).toHaveCount(1);
 const before=state.reads.filter((p:string)=>p==='/sessions/demo/manual-full-session').length;
 state.holdPost();await expect.poll(()=>state.posts.length).toBe(1);
 await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,50)));
 expect(state.reads.filter((p:string)=>p==='/sessions/demo/manual-full-session')).toHaveLength(before);
 expect(state.errors).toEqual([]);
});
test('maximum labels render as text and wrap on mobile; invalid draft cannot submit',async({page})=>{
 const values=['x'.repeat(40),...Array.from({length:7},(_,i)=>String(i)+'字'.repeat(39))];
 const state=await labelsFixture(page,false,{metadata:{version:1,labels:values}});await page.setViewportSize({width:390,height:844});
 const panel=await openLabels(page);await expect(panel.locator('.chip')).toHaveCount(8);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await panel.getByRole('textbox').fill('<img src=x onerror=alert(1)>');
 await panel.getByRole('button',{name:'Save labels',exact:true}).click();await expect(panel).toContainText('This label change was saved');
 await expect(panel.locator('img')).toHaveCount(0);expect(state.posts).toHaveLength(1);
 await panel.getByRole('button',{name:'Review current version and prepare change'}).click();
 await panel.getByRole('textbox').fill('x'.repeat(41));await expect(panel.getByRole('button',{name:'Save labels',exact:true})).toBeDisabled();
 await panel.getByRole('textbox').fill('same\nsame');await expect(panel.getByRole('button',{name:'Save labels',exact:true})).toBeDisabled();
});

for(const native of [false,true])for(const retained of [false,true])test(`${native?'native':'browser'}: exact metadata identity ${retained?'retained':'mismatched'}`,async({page})=>{
 const state=await labelsFixture(page,native,{retained,wrongIdentity:!retained}),panel=await openLabels(page);
 if(retained){
  await panel.getByRole('textbox').fill('History');await panel.getByRole('button',{name:'Save labels',exact:true}).click();
  await expect(panel).toContainText('This label change was saved');expect(state.posts[0].body.target).toEqual(target);
 }else{
  await expect(panel.locator('summary')).not.toContainText('Edit labels');
  await expect(panel.locator('.session-label-editor button')).toHaveCount(0);
  expect(state.posts).toHaveLength(0);
 }
 expect(state.errors).toEqual([]);
});
