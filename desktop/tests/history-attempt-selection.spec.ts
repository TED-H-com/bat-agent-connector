import {test, expect, type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {fixture, open, reopen, select, relations, detail} from './external-history-links-fixture.ts';
import {workspaceId, workId} from './pm-workbench-fixture.ts';

const att = (n:number) => 'att_' + n.toString(16).padStart(32,'0');
const picker = (page:Page) => page.locator('[data-history-attempt-picker]');
const attemptSelect = (page:Page) => picker(page).getByRole('combobox');
const selectedDetail = (page:Page) => picker(page).locator('[data-history-attempt-detail]');
const submit = (page:Page) => relations(page).getByRole('button',{name:'Link excerpt to Work',exact:true});
function attemptsFixture() {
  const f=fixture(), dispatch=f.dispatch;
  const state={supported:true, pageSize:20, hiddenFirst:false, denied:false, detailDenied:false,
    rows:[1,2].map(n=>({attempt_id:att(n),workspace_id:workspaceId,work_item_id:workId(1),sequence:n,stage:'started',created_at:1791705600+n})),
    listTransform:null as ((data:any)=>void)|null, detailTransform:null as ((data:any)=>void)|null,
    holdList:null as Promise<void>|null,holdDetail:null as Promise<void>|null, reads:[] as string[]};
  f.dispatch=async input=>{
    const url=new URL(input.path,'http://fixture'), path=url.pathname, endpoint=`/pm/workspaces/${workspaceId}`;
    if (path.startsWith(endpoint+'/work/') && path.endsWith('/attempts')) {
      state.reads.push(input.path);
      const wi=path.split('/').at(-2), after=Number(url.searchParams.get('after') || 0);
      const rows=state.rows.filter(row=>row.work_item_id===wi && row.sequence>after);
      const page=rows.slice(0,state.pageSize), next=rows.length>state.pageSize ? page.at(-1)!.sequence : null;
      const data={workspace_id:workspaceId,work_item_id:wi,attempts:structuredClone(page),next_cursor:next,visibility_filtered:false};
      if(state.hiddenFirst && after===0) {data.attempts=[];data.next_cursor=1;data.visibility_filtered=true;}
      state.listTransform?.(data); if(state.holdList) await state.holdList;
      return state.denied ? {status:404,data:{error:{code:'NOT_FOUND',message:'Attempts unavailable'}}} : {status:200,data};
    }
    if(path.startsWith(endpoint+'/work-attempts/')) {
      state.reads.push(input.path);
      const data={workspace_id:workspaceId,attempt:structuredClone(state.rows.find(row=>path.endsWith(row.attempt_id)))};
      state.detailTransform?.(data); if(state.holdDetail) await state.holdDetail;
      return state.detailDenied || !data.attempt ? {status:404,data:{error:{code:'NOT_FOUND',message:'Attempt unavailable'}}} : {status:200,data};
    }
    const result=await dispatch(input);
    if(path==='/bootstrap' || path==='/capabilities') {
      const caps=path==='/bootstrap' ? result.data.capabilities : result.data;
      if(state.supported) caps.features.pm_workspaces.work_start={version:1,available:true};
    }
    return result;
  };
  return Object.assign(f,{attempts:state});
}
async function choose(page:Page,id=att(1)) {
  if(await picker(page).getAttribute('open')===null) await picker(page).locator('summary').click();
  await expect(attemptSelect(page).locator(`option[value="${id}"]`)).toHaveCount(1);
  await attemptSelect(page).selectOption(id);
  await expect(selectedDetail(page)).toContainText(id); await expect(submit(page)).toBeEnabled();
}
async function reloadDraft(page:Page) {
  await page.reload(); await reopen(page); await relations(page).getByRole('button',{name:'Choose a Work',exact:true}).click();
}
for(const native of [false,true]) {
  test(`optional attempt is lazy and sends only the selected existing ID ${native}`,async({page})=>{
    const f=attemptsFixture(); await open(page,native,f); await select(page);
    expect(f.attempts.reads).toEqual([]); await submit(page).click();
    await expect(relations(page)).toContainText('Excerpt linked to Work.');
    expect(f.state.writes[0].body.params).toEqual({fixed_ref:f.ref});
    await choose(page,att(1)); expect(f.attempts.reads.some(path=>path.endsWith('/'+att(1)))).toBe(true);
    expect(f.state.writes).toHaveLength(1); await submit(page).click();
    await expect.poll(()=>f.state.writes.length).toBe(2);
    expect(f.state.writes[1].body.params).toEqual({fixed_ref:f.ref,attempt_id:att(1)});
    expect(f.state.links[0].attempt_id).toBe(att(1)); await expect(relations(page)).toContainText('Linked to a specific attempt');
    await expect(attemptSelect(page)).toHaveValue(att(1)); await expect(submit(page)).toBeEnabled();
    await attemptSelect(page).selectOption(''); await submit(page).click();
    await expect.poll(()=>f.state.writes.length).toBe(3); expect(f.state.writes[2].body.params).toEqual({fixed_ref:f.ref});
    expect(f.state.writes.every(row=>row.body.action==='pm.history.link')).toBe(true);
  });
  test(`lost response restores the original attempt even with changed draft metadata ${native}`,async({page})=>{
    const f=attemptsFixture(); await open(page,native,f); await select(page); await choose(page); f.state.lost=true;
    await submit(page).click(); await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();
    const original=structuredClone(f.state.writes[0]);
    await page.evaluate(id=>{for(const [key,raw] of Object.entries(localStorage)) if(key.includes('.pm.history.link.')) {const value=JSON.parse(raw);value.values.attempt_id=id;localStorage.setItem(key,JSON.stringify(value));}},att(2));
    await reloadDraft(page); await expect(selectedDetail(page)).toContainText(att(1));
    await expect(attemptSelect(page)).toBeDisabled(); await expect(picker(page).getByRole('button',{name:'Refresh attempts'})).toBeDisabled();
    expect(f.state.writes).toHaveLength(1); await relations(page).getByRole('button',{name:'Check original request'}).click();
    await expect(submit(page)).toBeEnabled(); expect(f.state.writes[1]).toEqual(original); expect(f.state.ops.size).toBe(1);
  });
}
test('draft selection survives reload and Work switching, with no attempt metadata saved',async({page})=>{
  const f=attemptsFixture(); await open(page,false,f); await select(page); await choose(page);
  const stored=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('.pm.history.link.')).map(([,value])=>JSON.parse(value)));
  expect(stored[0].values.attempt_id).toBe(att(1)); expect(Object.keys(stored[0].values).sort()).toEqual(['attempt_id','fixed_ref','replacement']);
  await relations(page).getByRole('combobox',{name:'Existing Work',exact:true}).selectOption(workId(2));
  await expect(submit(page)).toBeEnabled(); expect(await picker(page).getAttribute('open')).toBeNull();
  await relations(page).getByRole('combobox',{name:'Existing Work',exact:true}).selectOption(workId(1));
  await expect(selectedDetail(page)).toContainText(att(1)); await reloadDraft(page);
  await expect(selectedDetail(page)).toContainText(att(1)); await expect(submit(page)).toBeEnabled(); expect(f.state.writes).toEqual([]);
});
test('hidden pages advance without selecting the latest attempt',async({page})=>{
  const f=attemptsFixture(); f.attempts.hiddenFirst=true; await open(page,false,f); await select(page);
  await picker(page).locator('summary').click(); await expect(picker(page)).toContainText('No visible attempts');
  await picker(page).getByRole('button',{name:'Read more attempts'}).click();
  await expect(attemptSelect(page).locator(`option[value="${att(2)}"]`)).toHaveCount(1);
  await expect(attemptSelect(page)).toHaveValue('');
  expect(f.attempts.reads.filter(path=>path.includes('after=0'))).toHaveLength(2);
  expect(f.attempts.reads.some(path=>path.includes('after=1'))).toBe(true); expect(f.state.writes).toEqual([]);
});
test('more pages revalidate earlier choices and retain only current visible records',async({page})=>{
  const f=attemptsFixture(); f.attempts.pageSize=1; await open(page,false,f); await select(page);
  await picker(page).locator('summary').click(); await expect(attemptSelect(page).locator('option')).toHaveCount(2);
  f.attempts.hiddenFirst=true; await picker(page).getByRole('button',{name:'Read more attempts'}).click();
  await expect(attemptSelect(page).locator(`option[value="${att(1)}"]`)).toHaveCount(0);
  await expect(attemptSelect(page).locator(`option[value="${att(2)}"]`)).toHaveCount(1); expect(f.state.writes).toEqual([]);
});
for(const invalid of ['workspace','work','id','cursor','row-order','oversized','filtered']) {
  test(`refuses malformed attempt page: ${invalid}`,async({page})=>{
    const f=attemptsFixture(); f.attempts.listTransform=data=>{
      if(invalid==='workspace') data.workspace_id='ws_'+'9'.repeat(32);
      if(invalid==='work') data.attempts[0].work_item_id=workId(2);
      if(invalid==='id') data.attempts[0].attempt_id='invalid';
      if(invalid==='cursor') data.next_cursor=0;
      if(invalid==='row-order') data.attempts.reverse();
      if(invalid==='oversized') data.attempts=Array(21).fill(data.attempts[0]);
      if(invalid==='filtered') delete data.visibility_filtered;
    };
    await open(page,false,f); await select(page); await picker(page).locator('summary').click();
    await expect(picker(page).locator('[data-error-summary]')).toBeVisible(); await expect(attemptSelect(page).locator('option')).toHaveCount(1);
    await expect(submit(page)).toBeEnabled(); expect(f.state.writes).toEqual([]);
  });
}
for(const invalid of ['workspace','work','id','date','denied']) {
  test(`selection requires a matching readable attempt detail: ${invalid}`,async({page})=>{
    const f=attemptsFixture(); await open(page,false,f); await select(page);
    await picker(page).locator('summary').click(); await expect(attemptSelect(page).locator('option')).toHaveCount(3);
    f.attempts.detailDenied=invalid==='denied'; f.attempts.detailTransform=data=>{
      if(invalid==='workspace') data.workspace_id='ws_'+'9'.repeat(32);
      if(invalid==='work') data.attempt.work_item_id=workId(2);
      if(invalid==='id') data.attempt.attempt_id=att(2);
      if(invalid==='date') data.attempt.created_at=1e300;
    };
    await attemptSelect(page).selectOption(att(1)); await expect(picker(page).locator('[data-error-summary]')).toBeVisible();
    await expect(selectedDetail(page)).toBeEmpty(); await expect(submit(page)).toBeDisabled();
    await attemptSelect(page).selectOption(''); await expect(submit(page)).toBeEnabled(); expect(f.state.writes).toEqual([]);
  });
}
test('refresh immediately clears old detail; revoked choice cannot become a Work-only submission',async({page})=>{
  const f=attemptsFixture(); await open(page,false,f); await select(page); await choose(page);
  let release!:()=>void; f.attempts.holdList=new Promise(resolve=>release=resolve); f.attempts.detailDenied=true;
  await picker(page).getByRole('button',{name:'Refresh attempts'}).click(); await expect(selectedDetail(page)).toBeEmpty();
  await expect(submit(page)).toBeDisabled(); release(); await expect(picker(page).locator('[data-error-summary]')).toBeVisible();
  await expect(submit(page)).toBeDisabled(); await expect(attemptSelect(page)).toHaveValue(att(1)); expect(f.state.writes).toEqual([]);
});
test('late attempt detail cannot overwrite another Work',async({page})=>{
  const f=attemptsFixture(); await open(page,false,f); await select(page);
  await picker(page).locator('summary').click(); await expect(attemptSelect(page).locator('option')).toHaveCount(3);
  let release!:()=>void; f.attempts.holdDetail=new Promise(resolve=>release=resolve);
  await attemptSelect(page).selectOption(att(1)); await expect(submit(page)).toBeDisabled();
  await relations(page).getByRole('combobox',{name:'Existing Work',exact:true}).selectOption(workId(2));
  await expect(submit(page)).toBeEnabled(); release(); await expect(selectedDetail(page)).toBeEmpty();
  await submit(page).click(); await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.target.work_item_id).toBe(workId(2)); expect(f.state.writes[0].body.params.attempt_id).toBeUndefined();
});
test('closing the source disposes pending attempt reads and controls',async({page})=>{
  const f=attemptsFixture(); await open(page,false,f); await select(page);
  let release!:()=>void; f.attempts.holdList=new Promise(resolve=>release=resolve);
  await picker(page).locator('summary').click(); await expect.poll(()=>f.attempts.reads.length).toBe(1);
  await detail(page).getByRole('button',{name:'Close',exact:true}).click(); release();
  await expect(picker(page)).toHaveCount(0); expect(f.state.writes).toEqual([]);
});
test('older capabilities retain Work-only links and original request recovery',async({page})=>{
  const f=attemptsFixture(); await open(page,false,f); await select(page); await choose(page); f.state.lost=true;
  await submit(page).click(); await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();
  f.attempts.supported=false; await reloadDraft(page); await expect(picker(page).locator('[data-error-summary]')).toBeVisible();
  await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeEnabled();
  await relations(page).getByRole('button',{name:'Check original request'}).click();
  await expect(relations(page)).toContainText('Excerpt linked to Work.'); await expect(submit(page)).toBeDisabled();
  await attemptSelect(page).selectOption(''); await expect(submit(page)).toBeEnabled();
  expect(f.state.writes[1]).toEqual(f.state.writes[0]);
});
test('old original Work-only requests ignore a newer attempt draft field',async({page})=>{
  const f=attemptsFixture(); await open(page,false,f); await select(page); f.state.lost=true; await submit(page).click();
  await expect(relations(page).getByRole('button',{name:'Check original request'})).toBeVisible();
  await page.evaluate(id=>{for(const [key,raw] of Object.entries(localStorage)) if(key.includes('.pm.history.link.')) {const value=JSON.parse(raw);value.values.attempt_id=id;localStorage.setItem(key,JSON.stringify(value));}},att(1));
  await reloadDraft(page); expect(f.attempts.reads).toEqual([]);
  await relations(page).getByRole('button',{name:'Check original request'}).click(); await expect(submit(page)).toBeEnabled();
  expect(f.state.writes[1]).toEqual(f.state.writes[0]);
});
test('acknowledged success during restored attempt loading retains a usable verified choice',async({page})=>{
  const f=attemptsFixture(), dispatch=f.dispatch;
  let pending=true, restoring=false, detailStarted=false, heldLinks=0, releaseDetail!:()=>void, releaseLinks!:()=>void;
  const detailWait=new Promise<void>(resolve=>releaseDetail=resolve), linksWait=new Promise<void>(resolve=>releaseLinks=resolve);
  f.dispatch=async input=>{
    const result=await dispatch(input);
    if(input.path.startsWith('/operations') && result.data.operation) result.data.operation.status=pending?'running':'succeeded';
    if(restoring && input.path.includes('/work-attempts/')) {detailStarted=true;pending=false;await detailWait;}
    if(detailStarted && input.path.includes('/associations?')) {heldLinks++;await linksWait;}
    return result;
  };
  await open(page,false,f); await select(page); await choose(page); await submit(page).click();
  // Reload only after the browser has persisted the acknowledged operation ID.
  // Completion starts inside restoration, not in the interval before reload.
  await expect(relations(page).locator('a[href^="#/op/"]')).toHaveCount(1); restoring=true;
  await reloadDraft(page); await expect.poll(()=>heldLinks).toBe(1);
  releaseDetail(); await expect(selectedDetail(page)).toContainText(att(1)); releaseLinks();
  await expect(submit(page)).toBeEnabled(); await expect(attemptSelect(page)).toHaveValue(att(1));
  expect(f.state.writes).toHaveLength(1);
});
test('a selected text range and attempt stay in the same fixed request',async({page})=>{
  const f=attemptsFixture(); await open(page,false,f); await select(page); await choose(page,att(2));
  const ranges=page.locator('[data-history-text-picker]'); await ranges.locator('summary').click();
  await ranges.locator('textarea').evaluate((node:HTMLTextAreaElement)=>node.setSelectionRange(7,9));
  await ranges.getByRole('button',{name:'Preview selected text'}).click();
  await expect(page.locator('[data-history-preview] .exh-text')).toHaveText('🦇'); await submit(page).click();
  await expect.poll(()=>f.state.writes.length).toBe(1);
  expect(f.state.writes[0].body.params.attempt_id).toBe(att(2));
  expect(f.state.writes[0].body.params.fixed_ref.selection).toEqual({kind:'text',part_index:0,start_codepoint:7,end_codepoint:8});
});
test('new Work-only drafts on older servers do not expose an attempt picker',async({page})=>{
  const f=attemptsFixture(); f.attempts.supported=false; await open(page,false,f); await select(page);
  await expect(picker(page)).toBeHidden(); expect(f.attempts.reads).toEqual([]); await expect(submit(page)).toBeEnabled();
});
test('restored choice beyond the first page is read by exact ID and labelled as verified',async({page})=>{
  const f=attemptsFixture(); f.attempts.pageSize=1; await open(page,false,f); await select(page);
  await picker(page).locator('summary').click(); await expect(attemptSelect(page).locator('option')).toHaveCount(2);
  await picker(page).getByRole('button',{name:'Read more attempts'}).click(); await choose(page,att(2));
  await reloadDraft(page); await expect(selectedDetail(page)).toContainText(att(2));
  await expect(attemptSelect(page).locator('option:checked')).toContainText('Attempt 2'); await expect(submit(page)).toBeEnabled();
  expect(f.state.writes).toEqual([]);
});
test('bounded pages leave a partial-list notice',async({page})=>{
  const f=attemptsFixture(); f.attempts.pageSize=1;
  f.attempts.rows=Array.from({length:26},(_,i)=>({...f.attempts.rows[0],attempt_id:att(i+1),sequence:i+1}));
  await open(page,false,f); await select(page); await picker(page).locator('summary').click();
  for(let count=2;count<=25;count++) {
    await picker(page).getByRole('button',{name:'Read more attempts'}).click();
    await expect(attemptSelect(page).locator('option')).toHaveCount(count+1);
  }
  await expect(picker(page)).toContainText('This view has read 25 pages');
  await expect(picker(page).getByRole('button',{name:'Read more attempts'})).toBeHidden();
  await expect(attemptSelect(page)).toHaveValue(''); expect(f.state.writes).toEqual([]);
});
test('failed original attempt requires explicit reset before a different selection',async({page})=>{
  const f=attemptsFixture(); await open(page,false,f); await select(page); await choose(page); f.state.stale=true;
  await submit(page).click(); await expect(relations(page).getByRole('button',{name:'New request',exact:true})).toBeVisible();
  await expect(attemptSelect(page)).toBeDisabled(); await relations(page).getByRole('button',{name:'New request',exact:true}).click();
  f.state.stale=false; await choose(page,att(2)); await submit(page).click();
  await expect.poll(()=>f.state.writes.length).toBe(2); expect(f.state.writes[1].body.params.attempt_id).toBe(att(2));
  expect(f.state.writes[1].idempotency_key).not.toBe(f.state.writes[0].idempotency_key);
});
for(const locale of ['en','zh-TW']) for(const width of [390,1440]) {
  test(`attempt picker keyboard and layout ${locale} ${width}`,async({page})=>{
    const f=attemptsFixture(); await page.setViewportSize({width,height:900});
    await page.addInitScript(locale=>localStorage.setItem('batc.lang',locale),locale);
    await open(page,false,f);
    // The fixture entry helpers have English action names; use stable view nodes here.
    await relations(page).getByRole('button',{name:locale==='en'?'Choose a Work':'選擇工作',exact:true}).click();
    await relations(page).getByRole('combobox').selectOption(workId(1));
    await picker(page).locator('summary').focus(); await page.keyboard.press('Enter');
    await expect(attemptSelect(page).locator('option')).toHaveCount(3); await attemptSelect(page).selectOption(att(1));
    await expect(selectedDetail(page)).toContainText(att(1));
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await selectedDetail(page).scrollIntoViewIfNeeded();
    if(width===390) {
      expect((await attemptSelect(page).boundingBox())!.height).toBeGreaterThanOrEqual(44);
      expect((await picker(page).getByRole('button',{name:locale==='en'?'Refresh attempts':'重新讀取執行輪次'}).boundingBox())!.height).toBeGreaterThanOrEqual(44);
    }
    if(process.env.BATC_SCREENSHOT_DIR) {await mkdir(process.env.BATC_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:`${process.env.BATC_SCREENSHOT_DIR}/attempt-selection-${locale}-${width}.png`,fullPage:true});}
    expect(f.state.writes).toEqual([]);
  });
}
