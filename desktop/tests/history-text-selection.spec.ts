import {test, expect, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {fixture, open, reopen, select, relations, detail} from './external-history-links-fixture.ts';
import {workId} from './pm-workbench-fixture.ts';

const preview = (page:Page) => page.locator('[data-history-preview]');
const picker = (page:Page) => page.locator('[data-history-text-picker]');
const submit = (page:Page) => relations(page).getByRole('button',{name:'Link excerpt to Work',exact:true});
async function range(page:Page, start:number, end:number, part=0) {
  if (await picker(page).getAttribute('open') === null) await picker(page).locator('summary').click();
  const field = page.locator(`[data-history-text-part="${part}"]`);
  await field.evaluate((node:HTMLTextAreaElement, [start,end])=>{node.focus(); node.setSelectionRange(start,end);}, [start,end]);
  await field.locator('..').locator('..').getByRole('button').click();
}
async function prepareAgain(page:Page) {
  await page.reload(); await reopen(page);
  await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click();
}
for (const native of [false,true]) {
  test(`fixed emoji range, success and another selection ${native ? 'IPC' : 'HTTP'}`, async({page})=>{
    const f=fixture(); await open(page,native,f); await select(page);
    await range(page,7,9); await expect(preview(page).locator('.exh-text')).toHaveText('🦇');
    expect(f.state.previews[1].body.selection).toEqual({kind:'text',part_index:0,start_codepoint:7,end_codepoint:8});
    expect(f.state.previews[1].idempotency_key).toBeFalsy(); expect(f.state.writes).toEqual([]);
    await expect(preview(page)).toContainText('2 parts omitted'); await expect(preview(page)).toContainText('shortened');
    await submit(page).click(); await expect(relations(page)).toContainText('Excerpt linked to Work.');
    expect(f.state.writes[0].body.params.fixed_ref.selection).toEqual(f.state.previews[1].body.selection);
    expect(f.state.writes[0].body.params.fixed_ref.excerpt_digest).toBe('f'.repeat(64));
    await expect(submit(page)).toBeEnabled();
    await relations(page).getByRole('button',{name:'Use entire entry'}).click();
    await expect(preview(page)).toContainText('Entire entry'); await submit(page).click();
    await expect.poll(()=>f.state.writes.length).toBe(2);
    expect(f.state.writes[1].body.params.fixed_ref).toEqual(f.ref);
    expect(f.state.writes[1].idempotency_key).not.toBe(f.state.writes[0].idempotency_key);
    expect(f.state.writes.map(row=>row.body.action)).toEqual(['pm.history.link','pm.history.link']);
  });
  test(`lost range request restores exact excerpt, key and body ${native}`, async({page})=>{
    const f=fixture(); await open(page,native,f); await select(page); await range(page,7,9);
    await expect(preview(page).locator('.exh-text')).toHaveText('🦇'); f.state.lost=true; await submit(page).click();
    await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();
    const original=structuredClone(f.state.writes[0]); await prepareAgain(page);
    await expect(preview(page).locator('.exh-text')).toHaveText('🦇');
    await picker(page).locator('summary').click();
    await expect(picker(page).locator('textarea')).toBeDisabled();
    await expect(relations(page).getByRole('button',{name:'Use entire entry'})).toBeDisabled();
    await expect(relations(page).getByRole('combobox')).toBeDisabled();
    expect(f.state.writes).toHaveLength(1);
    await relations(page).getByRole('button',{name:'Check original request'}).click();
    await expect(relations(page)).toContainText('Excerpt linked to Work.');
    expect(f.state.writes[1]).toEqual(original); expect(f.state.ops.size).toBe(1);
  });
}
test('draft range survives Work refresh and reload without storing plaintext',async({page})=>{
  const f=fixture(); await open(page,false,f); await select(page); await range(page,7,9);
  await expect(preview(page).locator('.exh-text')).toHaveText('🦇');
  const saved=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('batc.pm.')).map(([,value])=>value).join('\n'));
  expect(saved).toContain('start_codepoint'); expect(saved).not.toContain('🦇'); expect(saved).not.toContain('keyboard fix');
  await relations(page).getByRole('button',{name:'Refresh selected Work'}).click();
  await expect(submit(page)).toBeEnabled(); await expect(preview(page).locator('.exh-text')).toHaveText('🦇');
  await prepareAgain(page); await expect(preview(page).locator('.exh-text')).toHaveText('🦇');
  await expect(submit(page)).toBeEnabled(); expect(f.state.writes).toEqual([]);
});
test('a completed receipt during range restoration cannot leave an empty disabled form',async({page})=>{
  const f=fixture(), dispatch=f.dispatch;
  let pending=true, restoring=false, textStarted=false, heldLinks=0, releaseText!:()=>void, releaseLinks!:()=>void;
  const textWait=new Promise<void>(resolve=>releaseText=resolve), linksWait=new Promise<void>(resolve=>releaseLinks=resolve);
  f.dispatch=async input=>{
    const result=await dispatch(input);
    if(input.path.startsWith('/operations') && result.data.operation) result.data.operation.status=pending?'running':'succeeded';
    if(restoring && input.path.endsWith('/history-excerpt-preview') && input.body.selection.kind==='text') {textStarted=true;await textWait;}
    if(textStarted && input.path.includes('/associations?')) {heldLinks++;await linksWait;}
    return result;
  };
  await open(page,false,f); await select(page); await range(page,7,9); await expect(submit(page)).toBeEnabled(); await submit(page).click();
  await expect.poll(()=>f.state.ops.size).toBe(1); pending=false; restoring=true;
  await prepareAgain(page); await expect.poll(()=>heldLinks).toBe(1);
  releaseText(); await expect(preview(page).locator('.exh-text')).toHaveText('🦇'); releaseLinks();
  await expect(relations(page)).toContainText('Excerpt linked to Work.'); await expect(submit(page)).toBeEnabled();
  expect(f.state.writes).toHaveLength(1);
});
test('selection before choosing Work is retained by that Work draft',async({page})=>{
  const f=fixture(); await open(page,false,f);
  await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click(); await range(page,7,9);
  await expect(preview(page).locator('.exh-text')).toHaveText('🦇'); await relations(page).getByRole('combobox').selectOption(workId(1));
  await expect(submit(page)).toBeEnabled(); await prepareAgain(page);
  await expect(preview(page).locator('.exh-text')).toHaveText('🦇'); expect(f.state.writes).toEqual([]);
});
test('keyboard selection uses readonly text and does not send an operation',async({page})=>{
  const f=fixture(); f.entry.parts[0].text='🦇🧑‍💻中文'; await open(page,false,f); await select(page);
  await picker(page).locator('summary').click(); const field=picker(page).locator('textarea');
  await field.focus(); await page.keyboard.press('Control+Home'); await page.keyboard.press('Shift+ArrowRight');
  await expect(field).toBeFocused();
  expect(await field.evaluate((node:HTMLTextAreaElement)=>[node.selectionStart,node.selectionEnd])).toEqual([0,2]);
  await page.keyboard.press('Tab'); await expect(picker(page).getByRole('button')).toBeFocused(); await page.keyboard.press('Enter');
  await expect(preview(page).locator('.exh-text')).toHaveText('🦇');
  await expect(picker(page).getByRole('button')).toBeFocused();
  await expect(field).toHaveAttribute('readonly',''); expect(f.state.writes).toEqual([]);
});
test('backward keyboard selection keeps joined emoji intact and rejects typing',async({page})=>{
  const f=fixture(); f.entry.parts[0].text='A\n🧑‍💻中'; await open(page,false,f); await select(page);
  await picker(page).locator('summary').click(); const field=picker(page).locator('textarea'); await field.focus();
  await page.keyboard.press('Control+End'); await page.keyboard.press('ArrowLeft'); await page.keyboard.press('Shift+ArrowLeft');
  expect(await field.evaluate((node:HTMLTextAreaElement)=>[node.selectionStart,node.selectionEnd,node.selectionDirection])).toEqual([2,7,'backward']);
  await page.keyboard.type('cannot change this'); await expect(field).toHaveValue('A\n🧑‍💻中');
  await page.keyboard.press('Tab'); await page.keyboard.press('Enter'); await expect(preview(page).locator('.exh-text')).toHaveText('🧑‍💻');
  expect(f.state.previews[1].body.selection).toEqual({kind:'text',part_index:0,start_codepoint:2,end_codepoint:5});
});
test('CRLF and other text parts retain original offsets, tools cannot be selected',async({page})=>{
  const f=fixture(); f.entry.parts=[{kind:'tool_request',name:'read'} as any,{kind:'text',text:'A\r\n🦇B\rC'}];
  await open(page,false,f); await select(page); await range(page,1,4,1);
  await expect(preview(page).locator('.exh-text')).toHaveText('\r\n🦇');
  expect(f.state.previews[1].body.selection).toEqual({kind:'text',part_index:1,start_codepoint:1,end_codepoint:4});
  await expect(picker(page).locator('textarea')).toHaveCount(1); await expect(picker(page).locator('[data-history-text-part="0"]')).toHaveCount(0);
  await expect(preview(page)).not.toContainText('Tool requested'); expect(f.state.writes).toEqual([]);
});
for (const [start,end] of [[0,0],[7,8],[8,9]]) test(`empty or partial surrogate range ${start}:${end} makes no request`,async({page})=>{
  const f=fixture(); await open(page,false,f); await select(page); await range(page,start,end);
  await expect(relations(page)).toContainText('Select a nonempty range'); expect(f.state.previews).toHaveLength(1);
  await expect(preview(page)).toContainText('Entire entry'); expect(f.state.writes).toEqual([]);
});
test('pending preview hides former excerpt and disables sending and retargeting',async({page})=>{
  const f=fixture(); await open(page,false,f); await select(page); let release!:()=>void;
  f.state.holdPreview=new Promise(resolve=>release=resolve); await range(page,7,9);
  await expect.poll(()=>f.state.previews.length).toBe(2); await expect(preview(page).locator('.exh-text')).toHaveCount(0);
  await expect(submit(page)).toBeDisabled(); await expect(relations(page).getByRole('combobox')).toBeDisabled();
  await expect(picker(page).locator('textarea')).toBeDisabled(); release();
  await expect(preview(page).locator('.exh-text')).toHaveText('🦇'); await expect(submit(page)).toBeEnabled(); expect(f.state.writes).toEqual([]);
});
for (const retire of ['close','revoke']) test(`late text preview is fenced after ${retire}`,async({page})=>{
  const f=fixture(); await page.clock.install(); await open(page,false,f); await select(page); let release!:()=>void;
  f.state.holdPreview=new Promise(resolve=>release=resolve); await range(page,7,9); await expect.poll(()=>f.state.previews.length).toBe(2);
  if (retire==='close') await detail(page).getByRole('button',{name:'Close',exact:true}).click();
  else {f.state.deny=true; await page.clock.fastForward(61000);}
  release(); await expect(detail(page)).toBeHidden(); await expect(page.locator('[data-history-preview]')).toHaveCount(0); expect(f.state.writes).toEqual([]);
});
for (const bad of ['range','content','coverage','digest','extra']) test(`refuse mismatched range preview ${bad}`,async({page})=>{
  const f=fixture(); await open(page,false,f); await select(page);
  f.state.previewTransform=data=>{
    if(bad==='range') data.ref.selection.end_codepoint++;
    if(bad==='content') data.content.parts[0].text='different';
    if(bad==='coverage') data.history_complete=true;
    if(bad==='digest') data.ref.content_digest='f'.repeat(64);
    if(bad==='extra') data.ref.extra='unrecognized';
  };
  await range(page,7,9); await expect(detail(page).locator('[data-history-entry]')).toHaveCount(0);
  await expect(detail(page)).toContainText('does not belong'); expect(f.state.writes).toEqual([]);
});
test('changed saved range proof hides plaintext and never resends the stored intent',async({page})=>{
  const f=fixture(); await open(page,false,f); await select(page); await range(page,7,9); await expect(submit(page)).toBeEnabled();
  f.state.lost=true; await submit(page).click(); await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();
  f.state.previewTransform=data=>{if(data.ref.selection.kind==='text') data.ref.excerpt_digest='9'.repeat(64);};
  await prepareAgain(page); await expect(detail(page).locator('[data-history-entry]')).toHaveCount(0); expect(f.state.writes).toHaveLength(1);
  const intents=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('.pm.history.link.')).map(([,value])=>JSON.parse(value).intent));
  expect(intents[0].key).toBe(f.state.writes[0].idempotency_key);
});
test('legacy whole-entry intent without the new metadata field remains recoverable',async({page})=>{
  const f=fixture(); await open(page,false,f); await select(page); f.state.lost=true; await submit(page).click();
  await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();
  await page.evaluate(()=>{for(const [key,value] of Object.entries(localStorage)) if(key.includes('.pm.history.link.')) {const saved=JSON.parse(value); saved.values={}; localStorage.setItem(key,JSON.stringify(saved));}});
  await prepareAgain(page); await expect(preview(page)).toContainText('Entire entry');
  await relations(page).getByRole('button',{name:'Check original request'}).click(); await expect(relations(page)).toContainText('Excerpt linked to Work.');
  expect(f.state.writes[1]).toEqual(f.state.writes[0]);
});
test('a new range cannot relabel an older whole-entry request when selecting its Work',async({page})=>{
  const f=fixture(); await open(page,false,f); await select(page); f.state.lost=true; await submit(page).click();
  await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();
  await page.evaluate(()=>{for(const [key,value] of Object.entries(localStorage)) {
    if(key.includes('.pm.history.link.')) {const saved=JSON.parse(value);saved.values={};localStorage.setItem(key,JSON.stringify(saved));}
    if(key.endsWith('.work-selection')) localStorage.removeItem(key);
  }});
  await prepareAgain(page); await range(page,7,9); await expect(preview(page).locator('.exh-text')).toHaveText('🦇');
  await relations(page).getByRole('combobox').selectOption(workId(1));
  await expect(preview(page)).toContainText('Entire entry'); await expect(preview(page)).toContainText('keyboard fix');
  await relations(page).getByRole('button',{name:'Check original request'}).click(); await expect(relations(page)).toContainText('Excerpt linked to Work.');
  expect(f.state.writes[1]).toEqual(f.state.writes[0]);
});
test('Work-specific range drafts are restored when switching targets',async({page})=>{
  const f=fixture(); await open(page,false,f); await select(page); await range(page,7,9); await expect(submit(page)).toBeEnabled();
  await relations(page).getByRole('combobox').selectOption(workId(2)); await expect(submit(page)).toBeEnabled();
  await range(page,0,6); await expect(preview(page).locator('.exh-text')).toHaveText('Review');
  await relations(page).getByRole('combobox').selectOption(workId(1)); await expect(preview(page).locator('.exh-text')).toHaveText('🦇');
  await submit(page).click(); await expect(relations(page)).toContainText('Excerpt linked to Work.');
  expect(f.state.writes[0].body.target.work_item_id).toBe(workId(1)); expect(f.state.writes[0].body.params.fixed_ref.selection.start_codepoint).toBe(7);
});
test('tool-only records keep whole-entry linking without text selection controls',async({page})=>{
  const f=fixture(); f.entry.parts=[{kind:'tool_result',name:'read'} as any]; await open(page,false,f); await select(page);
  await expect(picker(page)).toBeHidden(); await expect(preview(page)).toContainText('Tool result recorded'); await expect(submit(page)).toBeEnabled();
  expect(f.state.writes).toEqual([]);
});
test('failed original range requires an explicit new request before changing the excerpt',async({page})=>{
  const f=fixture(); await open(page,false,f); await select(page); await range(page,7,9); await expect(submit(page)).toBeEnabled();
  f.state.stale=true; await submit(page).click(); await expect(relations(page).getByRole('button',{name:'New request',exact:true})).toBeVisible();
  await expect(relations(page).getByRole('button',{name:'Use entire entry'})).toBeDisabled();
  await relations(page).getByRole('button',{name:'New request',exact:true}).click(); f.state.stale=false;
  await relations(page).getByRole('button',{name:'Use entire entry'}).click(); await expect(submit(page)).toBeEnabled(); await submit(page).click();
  await expect(relations(page)).toContainText('Excerpt linked to Work.'); expect(f.state.writes[1].body.params.fixed_ref.selection).toEqual({kind:'entry'});
  expect(f.state.writes[1].idempotency_key).not.toBe(f.state.writes[0].idempotency_key);
});
for (const locale of ['en','zh-TW']) for(const width of [390,1440]) test(`text selector layout ${locale} ${width}`,async({browser,baseURL})=>{
  const context=await browser.newContext({baseURL,locale,viewport:{width,height:900}}),page=await context.newPage(),f=fixture();
  const errors:string[]=[]; page.on('pageerror',e=>errors.push(e.message)); await open(page,false,f);
  await relations(page).getByRole('button',{name:locale==='en'?'Choose a Work':'選擇工作',exact:true}).click();
  await relations(page).getByRole('combobox').selectOption(workId(1)); await range(page,7,9);
  await expect(preview(page).locator('.exh-text')).toHaveText('🦇'); await expect(relations(page)).not.toContainText(/exh_|undefined|null/);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  if(width===390) {const box=await picker(page).getByRole('button').boundingBox(); expect(box!.height).toBeGreaterThanOrEqual(44);}
  const dir=process.env.BATC_SCREENSHOT_DIR; if(dir){await mkdir(dir,{recursive:true});await page.screenshot({path:`${dir}/text-selection-${locale}-${width}.png`,fullPage:true});}
  expect(errors).toEqual([]); await context.close();
});
