import {test,expect} from '@playwright/test';
import {STRINGS} from '../src/i18n.js';
import {discoveryFixture,open,openWorkspace,choose,cleanup,discovery,workspaceId,workId} from './history-discovery-fixture.ts';
import {linkId} from './external-history-links-fixture.ts';
import {mountConversation} from './conversation-fixture.ts';

const rows=(page:any)=>cleanup(page).locator('[data-history-cleanup-link]');
const read=(page:any,locale:'en'|'zh-TW'='en')=>discovery(page).getByRole('button',{name:STRINGS[locale].exh_cleanup_read_ids,exact:true});
const again=(page:any)=>discovery(page).getByRole('button',{name:STRINGS.en.exh_cleanup_read_again,exact:true});
const more=(page:any)=>discovery(page).getByRole('button',{name:STRINGS.en.exh_more_links,exact:true});
const recover=(page:any)=>cleanup(page).getByRole('button',{name:STRINGS.en.pm_check_original,exact:true});
const withdraw=(page:any)=>cleanup(page).getByRole('button',{name:STRINGS.en.exh_unlink,exact:true});
const readonly=(f:ReturnType<typeof discoveryFixture>)=>{
  expect(f.discovery.requests.filter(request=>request.method!=='GET')).toEqual([]);
  expect(f.discovery.requests.filter(request=>/\/(?:completions|responses|invoke|generate|probe)(?:\?|$)/.test(request.path))).toEqual([]);
  expect(f.state.ops.size).toBe(0);expect(f.state.writes).toEqual([]);
};
for(const native of [false,true]) for(const locale of ['en','zh-TW'] as const) for(const width of [390,1440])
test(`explicit cleanup discovery ${native?'IPC':'HTTP'} ${locale} ${width}`,async({page})=>{
  await page.setViewportSize({width,height:900});const f=discoveryFixture();await open(page,native,f,locale);
  expect(f.discovery.reads).toEqual([]);readonly(f);
  await cleanup(page).locator('summary').click();await cleanup(page).locator('summary').click();
  expect(f.discovery.reads).toEqual([]);readonly(f);
  await discovery(page).getByRole('button',{name:STRINGS[locale].exh_cleanup_discover,exact:true}).focus();await page.keyboard.press('Enter');
  const select=discovery(page).getByRole('combobox');await expect(select).toBeFocused();
  await expect(select.locator(`option[value="${workId(1)}"]`)).toHaveCount(1);
  const requests=f.discovery.requests.length;await select.selectOption(workId(1));
  expect(f.discovery.requests).toHaveLength(requests);expect(f.discovery.reads).toEqual([]);readonly(f);
  await expect(discovery(page)).toContainText(locale==='en'?'including ones whose source you can still read':'包括來源仍可讀取的關聯');
  await read(page,locale).focus();await page.keyboard.press('Enter');await expect(rows(page)).toHaveCount(1);
  expect(f.discovery.reads).toHaveLength(1);readonly(f);
  await expect(rows(page)).toContainText(linkId);await expect(rows(page)).not.toContainText('reader');
  await expect(rows(page)).not.toContainText('Member login');await expect(rows(page).locator('a')).toHaveAttribute('href',`#/item/${workId(1)}`);
  const saved=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('.history-')||key.includes('.history.')));
  expect(saved).toEqual([]);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  if(width===390)expect((await rows(page).getByRole('button',{name:STRINGS[locale].exh_unlink,exact:true}).boundingBox())!.height).toBeGreaterThanOrEqual(44);
});
test('owner rows have no creator identity and pagination walks 120 IDs once',async({page})=>{
  const f=discoveryFixture();f.base.base.state.role='owner';
  f.state.links=Array.from({length:120},(_,n)=>({...f.link(),link_id:'hxl_'+n.toString(16).padStart(32,'0'),created_by:'former-member'}));
  await open(page,false,f);await choose(page);await read(page).click();await expect(rows(page)).toHaveCount(20);
  for(let n=2;n<=6;n++){await more(page).click();await expect(rows(page)).toHaveCount(n*20);}
  await expect(more(page)).toBeHidden();expect(f.discovery.reads).toHaveLength(6);
  const ids=await rows(page).evaluateAll(nodes=>nodes.map(node=>node.getAttribute('data-history-cleanup-link')));
  expect(new Set(ids).size).toBe(120);await expect(cleanup(page)).not.toContainText('former-member');readonly(f);
});
test('empty pages keep More and the page cap never claims completion',async({page})=>{
  const f=discoveryFixture();f.discovery.pages=Array.from({length:25},(_,n)=>({links:[],next_after:'hxl_'+n.toString(16).padStart(32,'0')}));
  await open(page,false,f);await choose(page);await read(page).click();await expect(more(page)).toBeVisible();
  await expect(discovery(page)).toContainText('No relationships on this page. More may remain.');
  for(let n=2;n<=25;n++){await more(page).click();await expect.poll(()=>f.discovery.reads.length).toBe(n);await expect(again(page)).toBeEnabled();}
  await expect(more(page)).toBeHidden();await expect(discovery(page)).toContainText('The page limit was reached. More relationships may remain.');
  readonly(f);
});
for(const bad of ['extra','Work','duplicate','cursor-loop','404']) test(`invalid discovery page clears central rows: ${bad}`,async({page})=>{
  const f=discoveryFixture(),cursor='hxl_'+'c'.repeat(32);
  f.discovery.pages=[{links:[{link_id:linkId,work_item_id:workId(1),version:1}],next_after:cursor}];
  await open(page,false,f);await choose(page);await read(page).click();await expect(rows(page)).toHaveCount(1);
  const row:any={link_id:'hxl_'+'d'.repeat(32),work_item_id:workId(1),version:1};
  if(bad==='extra')row.source_id='forbidden-source-detail';if(bad==='Work')row.work_item_id=workId(2);if(bad==='duplicate')row.link_id=linkId;
  f.discovery.pages=[{links:[row],next_after:bad==='cursor-loop'?cursor:null}];if(bad==='404')f.discovery.deny=404;
  await more(page).click();await expect(rows(page)).toHaveCount(0);await expect(discovery(page)).toContainText('Cleanup IDs could not be read safely');
  await expect(discovery(page)).not.toContainText('forbidden-source-detail');readonly(f);
});
test('late Work page cannot replace a newer Work selection',async({page})=>{
  const f=discoveryFixture(),other='hxl_'+'e'.repeat(32);f.state.links.push({...f.link(),link_id:other,work_item_id:workId(2)});
  await open(page,false,f);await choose(page);let release!:()=>void;f.discovery.hold=new Promise(resolve=>release=resolve);
  await read(page).click();await expect.poll(()=>f.discovery.reads.length).toBe(1);
  await discovery(page).getByRole('combobox').selectOption(workId(2));f.discovery.hold=null;await read(page).click();
  await expect(rows(page)).toHaveCount(1);await expect(rows(page)).toContainText(other);release();
  await expect(rows(page)).not.toContainText(linkId);readonly(f);
});
test('closing the section retires a pending page without another read on reopening',async({page})=>{
  const f=discoveryFixture();await open(page,false,f);await choose(page);let release!:()=>void;
  f.discovery.hold=new Promise(resolve=>release=resolve);await read(page).click();await expect.poll(()=>f.discovery.reads.length).toBe(1);
  await cleanup(page).locator('summary').click();release();await cleanup(page).locator('summary').click();
  await expect(rows(page)).toHaveCount(0);await expect(discovery(page).getByRole('combobox')).toBeHidden();expect(f.discovery.reads).toHaveLength(1);readonly(f);
});
for(const native of [false,true]) test(`discovered withdrawal persists before send and recovers after lost reply ${native}`,async({page})=>{
  const f=discoveryFixture(),dispatch=f.dispatch,persisted:any[]=[];let server='pm-fixture';
  f.dispatch=async input=>{
    if(input.method==='POST'&&input.body?.action==='pm.history.unlink') persisted.push(await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('.history-cleanup.'))));
    const result=await dispatch(input);if(input.path==='/bootstrap')result.data.sync.server_id=server;return result;
  };
  await open(page,native,f);await choose(page);await read(page).click();await expect(rows(page)).toHaveCount(1);
  f.state.lost=true;await withdraw(page).click();await expect(recover(page)).toBeEnabled();
  const original=structuredClone(f.state.writes[0]);
  const saved=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('.history-cleanup.')));
  expect(saved).toHaveLength(1);expect(JSON.parse(saved[0][1])).toEqual({version:1,values:{},intent:{key:original.idempotency_key,request:original.body}});
  expect(persisted[0]).toEqual(saved);
  expect(saved[0][1]).not.toMatch(/source_id|entry_id|created_by|title|digest|fixed_ref/);expect(f.state.links).toHaveLength(0);
  const actor=f.base.base.state.actor,principal=f.base.base.state.principal;
  if(native){server='other-server';f.base.base.state.workspaces[0].server_id=server;}
  else {f.base.base.state.actor='another-owner';f.base.base.state.principal='person-2';}
  await page.reload();await cleanup(page).locator('summary').click();await expect(rows(page)).toHaveCount(0);
  expect(await page.evaluate(key=>localStorage.getItem(key),saved[0][0])).toBe(saved[0][1]);
  server='pm-fixture';f.base.base.state.workspaces[0].server_id=server;f.base.base.state.actor=actor;f.base.base.state.principal=principal;
  await page.reload();await cleanup(page).locator('summary').click();await expect(recover(page)).toBeEnabled();expect(f.discovery.reads).toHaveLength(1);
  await recover(page).click();await expect(cleanup(page)).toContainText('Relationship withdrawn.');
  expect(f.state.writes[1]).toEqual(original);expect(f.state.ops.size).toBe(1);
  expect(await page.evaluate(key=>localStorage.getItem(key),saved[0][0])).toBeNull();
});
test('withdrawal during More invalidates its late response',async({page})=>{
  const f=discoveryFixture();f.discovery.pages=[{links:[{link_id:linkId,work_item_id:workId(1),version:1}],next_after:'hxl_'+'c'.repeat(32)}];
  await open(page,false,f);await choose(page);await read(page).click();await expect(rows(page)).toHaveCount(1);
  let release!:()=>void;f.discovery.hold=new Promise(resolve=>release=resolve);
  f.discovery.pages=[{links:[{link_id:linkId,work_item_id:workId(1),version:1}],next_after:null}];
  await more(page).click();await expect.poll(()=>f.discovery.reads.length).toBe(2);await withdraw(page).click();
  await expect(cleanup(page)).toContainText('Relationship withdrawn.');release();
  await expect(withdraw(page)).toBeHidden();await cleanup(page).getByRole('button',{name:STRINGS.en.exh_cleanup_refresh,exact:true}).click();
  await expect(rows(page)).toHaveCount(0);expect(f.state.writes).toHaveLength(1);
});
test('conflict needs original reset and explicit reread before a new version and key',async({page})=>{
  const f=discoveryFixture();await open(page,false,f);await choose(page);await read(page).click();await expect(rows(page)).toHaveCount(1);
  f.state.links[0].version=2;await withdraw(page).click();await expect(cleanup(page)).toContainText('The relationship changed.');
  await cleanup(page).getByRole('button',{name:'New request',exact:true}).click();await expect(withdraw(page)).toBeDisabled();
  expect(f.state.writes).toHaveLength(1);expect(f.discovery.reads).toHaveLength(1);
  await again(page).click();await expect(rows(page)).toContainText('Last read version 2');await expect(withdraw(page)).toBeEnabled();
  await withdraw(page).click();await expect(cleanup(page)).toContainText('Relationship withdrawn.');
  expect(f.state.writes[1].body.preconditions.expected_link_version).toBe(2);
  expect(f.state.writes[1].idempotency_key).not.toBe(f.state.writes[0].idempotency_key);
});
test('older server retains saved recovery but offers no discovery action',async({page})=>{
  const f=discoveryFixture();f.discovery.capability=false;await open(page,false,f);
  await expect(discovery(page)).toBeHidden();expect(f.discovery.reads).toEqual([]);readonly(f);
});
for(const missing of ['observe','manage','membership','unlink','version']) test(`discovery is absent without ${missing}`,async({page})=>{
  const f=discoveryFixture(),dispatch=f.dispatch;
  f.dispatch=async input=>{
    const result=await dispatch(input);
    if(input.path==='/bootstrap'||input.path==='/capabilities') {
      const caps=input.path==='/bootstrap'?result.data.capabilities:result.data;
      if(['observe','manage'].includes(missing))caps.scopes=caps.scopes.filter(scope=>scope!==missing);
      if(missing==='unlink')caps.actions=caps.actions.filter(row=>row.action!=='pm.history.unlink');
      if(missing==='version')caps.features.external_history.relationships.cleanup_discovery.version=2;
    }
    if(missing==='membership'&&input.path===`/pm/workspaces/${workspaceId}`)result.data.workspace.membership=null;
    return result;
  };
  if(missing==='observe') {
    await mountConversation(page,false,f.dispatch);await page.goto(`/dashboard/#/pm/${workspaceId}`);
    await expect(page.locator('main')).toContainText('PM workspaces need View access.');
  } else await openWorkspace(page,false,f);
  await expect(discovery(page)).toBeHidden();expect(f.discovery.reads).toEqual([]);readonly(f);
});
for(const boundary of ['account','connection','workspace']) test(`late page cannot cross ${boundary}`,async({page})=>{
  const f=discoveryFixture(),dispatch=f.dispatch;let server='pm-fixture';
  f.dispatch=async input=>{const result=await dispatch(input);if(input.path==='/bootstrap')result.data.sync.server_id=server;return result;};
  await open(page,false,f);await choose(page);let release!:()=>void;f.discovery.hold=new Promise(resolve=>release=resolve);
  await read(page).click();await expect.poll(()=>f.discovery.reads.length).toBe(1);
  if(boundary==='account'){f.base.base.state.actor='another-owner';f.base.base.state.principal='person-2';await page.reload();}
  else if(boundary==='connection'){server='other-server';f.base.base.state.workspaces[0].server_id=server;await page.reload();}
  else await page.goto(`/dashboard/#/pm/ws_${'9'.repeat(32)}`);
  release();await expect(rows(page)).toHaveCount(0);expect(f.discovery.reads).toHaveLength(1);readonly(f);
});
test('failed first storage write blocks POST and retry keeps the original discovery intent',async({page})=>{
  const f=discoveryFixture();await open(page,false,f);await choose(page);await read(page).click();await expect(rows(page)).toHaveCount(1);
  await page.evaluate(()=>{
    const set=Storage.prototype.setItem;(window as any).failedCleanup=[];
    Storage.prototype.setItem=function(key,value){if(key.includes('.history-cleanup.')){(window as any).failedCleanup.push(JSON.parse(value));throw new DOMException('Storage full','QuotaExceededError');}return set.call(this,key,value);};
    (window as any).restoreCleanupStorage=()=>{Storage.prototype.setItem=set;};
  });
  await withdraw(page).click();await expect(cleanup(page).locator('[data-api-error]')).toBeVisible();expect(f.state.writes).toHaveLength(0);
  const original=await page.evaluate(()=>{(window as any).restoreCleanupStorage();return (window as any).failedCleanup[0].intent;});
  await recover(page).click();await expect(cleanup(page)).toContainText('Relationship withdrawn.');
  expect(f.state.writes[0].idempotency_key).toBe(original.key);expect(f.state.writes[0].body).toEqual(original.request);
});
test('500 pending requests block a new discovery intent without eviction and allow original recovery',async({page})=>{
  const f=discoveryFixture();f.discovery.pages=[{links:[{link_id:linkId,work_item_id:workId(1),version:1}],next_after:linkId}];
  await open(page,false,f);await choose(page);await read(page).click();await expect(rows(page)).toHaveCount(1);
  f.state.lost=true;await withdraw(page).click();await expect(recover(page)).toBeEnabled();
  const original=structuredClone(f.state.writes[0]),fresh='hxl_'+'d'.repeat(32);
  const before=await page.evaluate(()=>{
    const [key,raw]=Object.entries(localStorage).find(([key])=>key.includes('.history-cleanup.'))!;
    const original=JSON.parse(raw);
    for(let n=0;n<499;n++) {
      const saved=structuredClone(original),id='hxl_'+n.toString(16).padStart(32,'0');
      saved.intent.key='synthetic-pending-'+n;saved.intent.request.target.link_id=id;
      localStorage.setItem(key.replace(original.intent.request.target.link_id,id),JSON.stringify(saved));
    }
    return Object.entries(localStorage).filter(([key])=>key.includes('.history-cleanup.'));
  });
  f.state.links=[{...f.link(),link_id:fresh}];await more(page).click();
  const freshRow=cleanup(page).locator(`[data-history-cleanup-link="${fresh}"]`);
  await expect(freshRow.getByRole('button',{name:STRINGS.en.exh_unlink,exact:true})).toBeDisabled();
  await expect(freshRow).toContainText('500 withdrawal requests are saved.');
  expect(await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('.history-cleanup.')))).toEqual(before);
  await cleanup(page).locator(`[data-history-cleanup-link="${linkId}"]`).getByRole('button',{name:STRINGS.en.pm_check_original,exact:true}).click();
  await expect(cleanup(page)).toContainText('Relationship withdrawn.');expect(f.state.writes[1]).toEqual(original);
  await cleanup(page).getByRole('button',{name:STRINGS.en.exh_cleanup_refresh,exact:true}).click();
  await expect(freshRow.getByRole('button',{name:STRINGS.en.exh_unlink,exact:true})).toBeEnabled();
});
test('discovery dedupes saved metadata and a pending intent without replacing its version or key',async({page})=>{
  const f=discoveryFixture();await open(page,false,f);await choose(page);await read(page).click();await expect(rows(page)).toHaveCount(1);
  f.state.lost=true;await withdraw(page).click();await expect(recover(page)).toBeEnabled();const original=structuredClone(f.state.writes[0]);
  const saved=await page.evaluate(({workspaceId,work,link,source,entry})=>{
    const [key,raw]=Object.entries(localStorage).find(([key])=>key.includes('.history-cleanup.'))!;
    const prefix=key.slice(0,key.indexOf('.history-cleanup.'));
    localStorage.setItem(`${prefix}.history-known.${workspaceId}`,JSON.stringify({version:1,records:[{workspace_id:workspaceId,work_item_id:work,link_id:link,version:5,created_by:'reader',source_id:source,entry_id:entry}]}));
    return {key,raw};
  },{workspaceId,work:workId(1),link:linkId,source:f.ref.source_id,entry:f.ref.entry_id});
  f.discovery.pages=[{links:[{link_id:linkId,work_item_id:workId(1),version:9}],next_after:null}];
  await again(page).click();await expect(rows(page)).toHaveCount(1);await expect(rows(page)).toContainText('Last read version 1');
  expect(await page.evaluate(key=>localStorage.getItem(key),saved.key)).toBe(saved.raw);
  await recover(page).click();await expect(cleanup(page)).toContainText('Relationship withdrawn.');expect(f.state.writes[1]).toEqual(original);
});
test('a later page cannot repeat an ID already withdrawn in this scan',async({page})=>{
  const f=discoveryFixture(),row={link_id:linkId,work_item_id:workId(1),version:1};
  f.discovery.pages=[{links:[row],next_after:linkId},{links:[row],next_after:null}];
  await open(page,false,f);await choose(page);await read(page).click();await expect(rows(page)).toHaveCount(1);
  await withdraw(page).click();await expect(cleanup(page)).toContainText('Relationship withdrawn.');
  await more(page).click();await expect(discovery(page)).toContainText('Cleanup IDs could not be read safely');
  await expect(rows(page)).toHaveCount(0);expect(f.state.writes).toHaveLength(1);
});
test('failed removal keeps the original receipt recoverable without a remount loop or another POST',async({page})=>{
  const f=discoveryFixture();await open(page,false,f);await choose(page);await read(page).click();await expect(rows(page)).toHaveCount(1);
  await page.evaluate(()=>{
    const remove=Storage.prototype.removeItem;
    Storage.prototype.removeItem=function(key){if(key.includes('.history-cleanup.'))throw new DOMException('Storage unavailable','SecurityError');return remove.call(this,key);};
    (window as any).restoreCleanupRemoval=()=>{Storage.prototype.removeItem=remove;};
  });
  await withdraw(page).click();await expect(cleanup(page).locator('[data-api-error]')).toBeVisible();
  const original=structuredClone(f.state.writes[0]);
  expect(await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('.history-cleanup.')))).toHaveLength(1);
  expect(f.discovery.requests.filter(request=>request.path.startsWith('/operations/'))).toHaveLength(0);
  await page.evaluate(()=>(window as any).restoreCleanupRemoval());
  await cleanup(page).getByRole('button',{name:STRINGS.en.exh_cleanup_refresh,exact:true}).click();
  await expect(cleanup(page)).toContainText('Relationship withdrawn.');expect(f.state.writes).toEqual([original]);
  expect(await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.includes('.history-cleanup.')))).toEqual([]);
});
