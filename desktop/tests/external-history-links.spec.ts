import {test, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mountConversation} from './conversation-fixture.ts';
import {route, workId} from './pm-workbench-fixture.ts';
import {revealPmView} from './pm-navigation.ts';
import {fixture, entryId, linkId, text, detail, relations, reopen, open, select} from './external-history-links-fixture.ts';
import {workspaceId} from './pm-workbench-fixture.ts';

for (const native of [false,true]) {
  test(`explicit fixed entry link and withdrawal over ${native ? 'IPC' : 'HTTP'}`, async({page})=>{
    const f = fixture(); await open(page,native,f);
    expect(f.state.writes).toEqual([]); expect(f.state.previews).toEqual([]);
    await select(page);
    await expect(relations(page)).toContainText(text);
    await expect(relations(page)).toContainText('2 parts omitted'); await expect(relations(page).locator('img')).toHaveCount(0);
    const preview = f.state.previews[0]; expect(preview.body).toEqual({source_id:'example',entry_id:entryId,expected_content_digest:f.entry.content_digest,selection:{kind:'entry'}});
    expect(preview.idempotency_key).toBeFalsy(); expect(f.state.writes).toEqual([]);
    await relations(page).getByRole('button',{name:'Link excerpt to Work',exact:true}).click();
    const linked = relations(page).locator('[data-history-link]'); await expect(linked.getByRole('link')).toHaveText('Member login');
    expect(f.state.writes[0].body).toEqual({action:'pm.history.link',target:{workspace_id:workspaceId,work_item_id:workId(1)},
      params:{fixed_ref:f.ref},preconditions:{expected_work_version:2,expected_binding_id:'pmwb_'+'1'.repeat(32)}});
    await linked.getByRole('button',{name:'Withdraw relationship'}).click(); await expect(linked).toHaveCount(0);
    expect(f.state.writes[1].body).toEqual({action:'pm.history.unlink',target:{workspace_id:workspaceId,work_item_id:workId(1),link_id:linkId},params:{},preconditions:{expected_link_version:1}});
    expect(f.state.writes.map(row=>row.body.action)).toEqual(['pm.history.link','pm.history.unlink']);
    expect(f.base.board.state.cards[0].work.state).toBe('doing');
    expect(await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('batc.pm.')).map(([,value])=>value).join('\n'))).not.toContain(text);
  });
  test(`lost reply restores the chosen Work and original request ${native}`, async({page})=>{
    const f = fixture(); await open(page,native,f); await select(page); f.state.lost = true;
    await relations(page).getByRole('button',{name:'Link excerpt to Work',exact:true}).click();
    await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();
    const original = structuredClone(f.state.writes[0]);
    await page.reload(); await reopen(page);
    await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click();
    await expect(relations(page).getByRole('combobox')).toHaveValue(workId(1));
    await expect(relations(page).getByRole('combobox')).toBeDisabled();
    f.base.board.state.cards[0].work.version = 3;
    await relations(page).getByRole('button',{name:'Check original request'}).click();
    await expect(relations(page)).toContainText('Excerpt linked to Work.');
    expect(f.state.writes).toHaveLength(2); expect(f.state.writes[1]).toEqual(original); expect(f.state.ops.size).toBe(1);
  });
  test(`lost withdrawal reply remains recoverable after the active link disappears ${native}`, async({page})=>{
    const f = fixture(); f.state.links = [f.link()]; await open(page,native,f); f.state.lost = true;
    await relations(page).getByRole('button',{name:'Withdraw relationship'}).click();
    await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();
    const original = structuredClone(f.state.writes[0]); expect(f.state.links).toEqual([]);
    await page.reload(); await reopen(page);
    await expect(relations(page).locator('[data-history-withdrawal]')).toBeVisible();
    await relations(page).getByRole('button',{name:'Check original request'}).click();
    await expect(relations(page).locator('[data-history-withdrawal]')).toHaveCount(0);
    expect(f.state.writes).toHaveLength(2); expect(f.state.writes[1]).toEqual(original); expect(f.state.ops.size).toBe(1);
    expect(await page.evaluate(()=>Object.keys(localStorage).filter(key=>key.endsWith('.withdrawals')))).toEqual([]);
  });
}
test('reader and observe-only access do not gain link controls through admin scope', async({page})=>{
  const f = fixture(); f.base.base.state.role = 'reader'; f.state.admin = true; f.state.links = [{...f.link(), created_by:'another-reader'}];
  await open(page,false,f); await expect(relations(page).getByRole('link')).toHaveText('Member login');
  await expect(relations(page).getByRole('button',{name:'Choose a Work',exact:true})).toBeHidden();
  await expect(relations(page).getByRole('button',{name:'Withdraw relationship'})).toHaveCount(0); expect(f.state.writes).toEqual([]);
});
test('two entries linked to one Work have independent original intents', async({page})=>{
  const f = fixture(), otherId = 'shi_'+'f'.repeat(32);
  f.entries.push({...f.entry,entry_id:otherId,sequence:2,content_digest:'e'.repeat(64),parts:[{kind:'text',text:'Second independent entry'}]});
  await mountConversation(page,false,f.dispatch); await page.goto(route); await (await revealPmView(page,'agents')).click();
  await page.locator('[data-history-source]').click();
  const first = page.locator(`[data-history-links="${entryId}"]`), second = page.locator(`[data-history-links="${otherId}"]`);
  await first.locator('summary').click(); await first.getByRole('button',{name:'Choose a Work',exact:true}).click();
  await first.getByRole('combobox').selectOption(workId(1)); f.state.lost = true;
  await first.getByRole('button',{name:'Link excerpt to Work',exact:true}).click();
  await expect(first.getByRole('button',{name:'Check original request'})).toBeVisible();
  await second.locator('summary').click(); await second.getByRole('button',{name:'Choose a Work',exact:true}).click();
  await expect(second.getByRole('combobox')).toHaveValue(''); await second.getByRole('combobox').selectOption(workId(1));
  await second.getByRole('button',{name:'Link excerpt to Work',exact:true}).click();
  await expect(second).toContainText('Excerpt linked to Work.');
  expect(f.state.writes).toHaveLength(2); expect(f.state.writes[1].idempotency_key).not.toBe(f.state.writes[0].idempotency_key);
  expect(f.state.writes[1].body.params.fixed_ref.entry_id).toBe(otherId);
  await expect(first.getByRole('button',{name:'Check original request'})).toBeVisible();
});
test('observe-only owner can read without sending', async({page})=>{
  const f = fixture(); f.base.base.state.manage = false; f.state.links = [f.link()]; await open(page,false,f);
  await expect(relations(page).getByRole('link')).toHaveText('Member login');
  await expect(relations(page).getByRole('button',{name:'Choose a Work',exact:true})).toBeHidden();
  await expect(relations(page).locator('form')).toHaveCount(0); expect(f.state.writes).toEqual([]);
});
test('membership withdrawal hides restored link controls despite admin scope', async({page})=>{
  const f = fixture(); await open(page,false,f); await select(page);
  f.base.base.state.role = 'reader'; f.state.admin = true;
  // Close the modal before the shell refresh, then reopen from current authority.
  await detail(page).getByRole('button',{name:'Close',exact:true}).click();
  await page.getByRole('button',{name:'Refresh',exact:true}).click(); await reopen(page);
  await expect(relations(page).getByRole('button',{name:'Choose a Work',exact:true})).toBeHidden();
  expect(f.state.writes).toEqual([]);
});
test('Work choices follow central pagination and read the selected binding', async({page})=>{
  const f = fixture(); f.state.workPageSize = 1; await open(page,false,f);
  await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click();
  await expect(relations(page).getByRole('combobox').locator('option')).toHaveCount(2);
  await relations(page).getByRole('button',{name:'Read more Work',exact:true}).click();
  await expect(relations(page).getByRole('combobox').locator('option')).toHaveCount(3);
  await relations(page).getByRole('combobox').selectOption(workId(2));
  await expect(relations(page)).toContainText('Password recovery · version 2');
  expect(f.state.reads.some(path=>path.endsWith('/kanban?after=1&limit=20'))).toBe(true);
  expect(f.state.reads.some(path=>path.endsWith('/kanban/'+workId(2)))).toBe(true); expect(f.state.writes).toEqual([]);
});
test('a failed selected Work read can be retried without changing the chosen ID', async({page})=>{
  const f = fixture(); f.state.failWork = true; await open(page,false,f);
  await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click();
  await relations(page).getByRole('combobox').selectOption(workId(1));
  await expect(relations(page)).toContainText('Work read unavailable'); await expect(relations(page).locator('form')).toHaveCount(0);
  f.state.failWork = false; await relations(page).getByRole('button',{name:'Refresh selected Work'}).click();
  await expect(relations(page).getByRole('button',{name:'Link excerpt to Work',exact:true})).toBeEnabled();
  await expect(relations(page).getByRole('combobox')).toHaveValue(workId(1)); expect(f.state.writes).toEqual([]);
});
test('reader creator can withdraw their existing relationship', async({page})=>{
  const f = fixture(); f.base.base.state.role = 'reader'; f.state.links = [f.link()]; await open(page,false,f);
  await relations(page).getByRole('button',{name:'Withdraw relationship'}).click();
  await expect(relations(page).locator('[data-history-link]')).toHaveCount(0); expect(f.state.writes[0].body.action).toBe('pm.history.unlink');
});
test('an older history capability keeps the reader read-only', async({page})=>{
  const f = fixture(); f.state.supported = false; await mountConversation(page,false,f.dispatch); await page.goto(route);
  await (await revealPmView(page,'agents')).click(); await page.locator('[data-history-source]').click();
  await expect(detail(page).locator('[data-history-entry]')).toHaveCount(1); await expect(relations(page)).toHaveCount(0);
  expect(f.state.reads.some(path=>path.includes('/associations'))).toBe(false); expect(f.state.previews).toEqual([]);
});
test('hidden association pages advance without implying an empty complete list', async({page})=>{
  const f = fixture(); f.state.hiddenPage = true; f.state.links = [{...f.link(),fixed_ref:{...f.ref,selection:{kind:'text',part_index:0,start_codepoint:0,end_codepoint:4}}}];
  await open(page,false,f); await expect(relations(page)).toContainText('More may be available');
  await relations(page).getByRole('button',{name:'Read more relationships'}).click();
  await expect(relations(page)).toContainText('Selected text'); await expect(relations(page).getByRole('link')).toHaveText('Member login');
  expect(f.state.reads.some(path=>path.includes(`entry_id=${entryId}&after=5`))).toBe(true);
});
test('source withdrawal fences a held preview and hides original plaintext', async({page})=>{
  const f = fixture(); await page.clock.install(); await open(page,false,f);
  let release!:()=>void; f.state.holdPreview = new Promise(resolve=>release=resolve);
  await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click(); await expect.poll(()=>f.state.previews.length).toBe(1);
  f.state.deny = true; await page.clock.fastForward(61000); release();
  await expect(detail(page)).toBeHidden(); await expect(page.locator('body')).not.toContainText(text); expect(f.state.writes).toEqual([]);
});
test('closing the reader fences a held preview without losing focus', async({page})=>{
  const f = fixture(); await open(page,false,f); let release!:()=>void; f.state.holdPreview = new Promise(resolve=>release=resolve);
  await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click();
  await detail(page).getByRole('button',{name:'Close',exact:true}).click(); release();
  await expect(detail(page)).toBeHidden(); await expect(page.locator('[data-history-source]')).toBeFocused(); expect(f.state.writes).toEqual([]);
});
test('late Work detail cannot replace a newer selection', async({page})=>{
  const f = fixture(); await open(page,false,f); let release!:()=>void; f.state.holdWork = new Promise(resolve=>release=resolve);
  await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click();
  await relations(page).getByRole('combobox').selectOption(workId(1));
  await relations(page).getByRole('combobox').selectOption(workId(2));
  await expect(relations(page)).toContainText('Password recovery · version 2'); release();
  await relations(page).getByRole('button',{name:'Link excerpt to Work',exact:true}).click();
  expect(f.state.writes[0].body.target.work_item_id).toBe(workId(2));
});
test('mismatched preview hides retained source text and never creates an intent', async({page})=>{
  const f = fixture(); f.state.previewMismatch = true; await open(page,false,f);
  await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click();
  await expect(detail(page)).toContainText('does not belong'); await expect(detail(page).locator('[data-history-entry]')).toHaveCount(0);
  expect(f.state.writes).toEqual([]);
});
test('mismatched Work binding refuses the form', async({page})=>{
  const f = fixture(); f.state.workMismatch = true; await open(page,false,f);
  await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click(); await relations(page).getByRole('combobox').selectOption(workId(1));
  await expect(relations(page)).toContainText('does not belong'); await expect(relations(page).locator('[data-pm-action="pm.history.link"]')).toHaveCount(0);
  expect(f.state.writes).toEqual([]);
});
test('stale Work refusal stays a failed original operation until explicit editing', async({page})=>{
  const f = fixture(); f.state.stale = true; await open(page,false,f); await select(page);
  await relations(page).getByRole('button',{name:'Link excerpt to Work',exact:true}).click();
  await expect(relations(page).getByRole('button',{name:'New request',exact:true})).toBeVisible();
  await expect(relations(page).getByRole('combobox')).toBeDisabled(); expect(f.state.writes).toHaveLength(1);
  await relations(page).getByRole('button',{name:'Refresh relationships'}).click(); expect(f.state.writes).toHaveLength(1);
  await expect(relations(page).getByRole('button',{name:'Refresh selected Work'})).toBeDisabled();
  await relations(page).getByRole('button',{name:'New request',exact:true}).click();
  f.state.stale = false; f.base.board.state.cards[0].work.version = 3;
  await relations(page).getByRole('button',{name:'Refresh selected Work'}).click();
  await expect(relations(page)).toContainText('Member login · version 3');
  await relations(page).getByRole('button',{name:'Link excerpt to Work',exact:true}).click();
  await expect(relations(page)).toContainText('Excerpt linked to Work.');
  expect(f.state.writes[1].body.preconditions.expected_work_version).toBe(3);
  expect(f.state.writes[1].idempotency_key).not.toBe(f.state.writes[0].idempotency_key);
});
for (const locale of ['en','zh-TW']) for (const width of [390,1440]) test(`relationship layout ${locale} ${width}`, async({browser,baseURL})=>{
  const context = await browser.newContext({baseURL,locale,viewport:{width,height:900}}), page = await context.newPage(), f = fixture();
  const errors:string[] = []; page.on('pageerror',error=>errors.push(error.message)); await open(page,false,f);
  await relations(page).getByRole('button',{name:locale === 'en' ? 'Choose a Work' : '選擇工作',exact:true}).click();
  await relations(page).getByRole('combobox').selectOption(workId(1)); await expect(relations(page).locator('form')).toBeVisible();
  await expect(relations(page)).not.toContainText(/exh_|undefined|null/); expect(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const dir = process.env.BATC_HISTORY_SCREENSHOTS; if (dir) {await mkdir(dir,{recursive:true}); await page.screenshot({path:`${dir}/relationships-${locale}-${width}.png`,fullPage:true});}
  expect(errors).toEqual([]); await context.close();
});
