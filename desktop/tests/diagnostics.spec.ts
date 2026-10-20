import {test,expect} from '@playwright/test';
import {readFile,mkdir} from 'node:fs/promises';
import {readFileSync} from 'node:fs';
import {diagnosticsFixture} from './diagnostics-fixture.ts';
const bootstrap=JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-bootstrap.json',import.meta.url),'utf8'));
const about=(page,locale)=>page.locator('#settings-tab-about');
const open=async(page,locale)=>{await page.goto('/dashboard/#/settings');await about(page,locale).click();await page.locator('[data-export-diagnostics]').click();await expect(page.locator('[data-diagnostics-json]')).toContainText('batc.diagnostics.v1');};
for(const locale of ['en','zh-TW'])test.describe(locale,()=>{
 test.use({locale});
 test('export separates a refused source from missing manage scope',async({page})=>{
  const state=await diagnosticsFixture(page);state.producer.bootstrap=structuredClone(bootstrap);state.operationsStatus=403;
  const posts:string[]=[];page.on('request',request=>{if(request.method()==='POST')posts.push(request.url());});
  await open(page,locale);
  const bundle=JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);
  expect(bundle.source_reads?.operations?.status).toBe('failed');
  expect(bundle.source_reads.operations).toMatchObject({http_status:403,code:'FORBIDDEN'});
  expect(bundle.source_reads.managed_setup.status).toBe('not_permitted');
  expect(bundle.source_reads.capabilities.status).toBe('available');expect(bundle.source_reads.observation.status).toBe('not_offered');
  for(const source of Object.values(bundle.source_reads) as any[])expect(Number.isFinite(Date.parse(source.read_at))).toBe(true);
  const summary=page.locator('[data-diagnostics-summary]');
  await expect(summary.locator('[data-diagnostics-source=operations]')).toContainText(locale==='en'?'Read failed':'讀取失敗');
  await expect(summary.locator('[data-diagnostics-source=managed_setup]')).toContainText(locale==='en'?'Required scope missing':'缺少所需權限');
  await expect(summary.locator('[data-diagnostics-source=operations]')).toBeVisible();
  await expect(summary.locator('[data-diagnostics-source=managed_setup]')).toBeVisible();
  expect(bundle.sessions.sampled).toBeNull();expect(bundle.operations).toBeNull();
  expect(JSON.stringify(bundle)).not.toMatch(/private-box|batc_secret|private prompt|secret@example|192\.0\.2\.8/);
  const downloadPromise=page.waitForEvent('download');
  await page.getByRole('dialog').getByRole('button',{name:locale==='en'?'Save':'儲存',exact:true}).click();
  const download=await downloadPromise;expect(JSON.parse(await readFile((await download.path())!,'utf8'))).toEqual(bundle);
  expect(posts).toEqual([]);expect(state.reads.some(path=>path.startsWith('/managed/setup'))).toBe(false);
 });
 for(const native of [false,true])for(const known of [false,true])test(`${native?'desktop':'browser'} build identity ${known?'known':'unknown'}`,async({page})=>{
  await diagnosticsFixture(page,native,known);await page.goto('/dashboard/#/settings');await about(page,locale).click();
  const line=page.locator('[data-build-line]');await expect(line).toContainText('Connector 0.2.5');
  await expect(line).toContainText(known?'aaaaaaa':locale==='en'?'unknown':'未知');
  if(native){await expect(line).toContainText(locale==='en'?'Desktop 0.1.1':'桌面版 0.1.1');await expect(line).toContainText(known?'bbbbbbb':locale==='en'?'unknown':'未知');}
  else await expect(line).not.toContainText('0.1.1');
 });
 test('preview, download, copy, keyboard wrap and restored focus',async({page})=>{
  const state=await diagnosticsFixture(page);state.hostStale=true;await open(page,locale);const dialog=page.getByRole('dialog');
  await expect(dialog).toContainText(locale==='en'?'host-1 · Reachable':'host-1 · 可連線');
  await expect(page.locator('[data-diagnostics-summary]')).toContainText(locale==='en'?'Reachable · Stale':'可連線 · 資料已過期');
  await expect(page.locator('[data-diagnostics-summary]')).toContainText(locale==='en'?'1 failed or uncertain':'1 筆失敗');
  const text=await page.locator('[data-diagnostics-json]').textContent(),bundle=JSON.parse(text!);
  expect(bundle.sessions.loaded).toBeNull();expect(bundle.sources.observation).toBe('not_available');
  expect(text).not.toMatch(/private-box|batc_secret|private prompt|secret@example|192\.0\.2\.8/);
  const downloadPromise=page.waitForEvent('download');await dialog.getByRole('button',{name:locale==='en'?'Save':'儲存',exact:true}).click();
  const download=await downloadPromise;expect(download.suggestedFilename()).toMatch(/^batc-diagnostics-\d{8}-\d{4}\.json$/);
  expect(JSON.parse(await readFile((await download.path())!,'utf8'))).toEqual(bundle);
  await expect(dialog.getByRole('status')).toHaveText(locale==='en'?'Download started':'已開始下載');
  await dialog.getByRole('button',{name:locale==='en'?'Copy':'複製',exact:true}).click();
  await expect(dialog.getByRole('status')).toHaveText(locale==='en'?'Copied':'已複製');expect(await page.evaluate(()=>(window as any).diagnosticsClipboard)).toBe(text);
  await dialog.getByRole('button',{name:locale==='en'?'Close':'關閉',exact:true}).focus();await page.keyboard.press('Tab');await expect(page.locator('[data-diagnostics-json]')).toBeFocused();
  await page.keyboard.press('Shift+Tab');await expect(dialog.getByRole('button',{name:locale==='en'?'Close':'關閉',exact:true})).toBeFocused();
  await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);await expect(page.locator('[data-export-diagnostics]')).toBeFocused();
  expect(state.reads.some(path=>path.startsWith('/managed/setup'))).toBe(false);
 });
 test('native saving passes only text, generated filename and locale',async({page})=>{
  const state=await diagnosticsFixture(page,true);await open(page,locale);
  await page.getByRole('dialog').getByRole('button',{name:locale==='en'?'Save':'儲存',exact:true}).click();await expect.poll(()=>state.saved.length).toBe(1);
  await expect(page.getByRole('dialog').getByRole('status')).toHaveText(locale==='en'?'Saved':'已儲存');
  expect(Object.keys(state.saved[0]).sort()).toEqual(['content','filename','locale']);expect(JSON.parse(state.saved[0].content).client.app_commit).toBe('b'.repeat(40));
 });
 for(const native of [false,true])test(`failed read exports its template through error Details first (${native?'native':'browser'})`,async({page})=>{
  await diagnosticsFixture(page,native);await page.goto('/dashboard/#/session/private-box.internal/private-session');
  await expect(page.locator('[data-session-error]').first()).toBeVisible();await page.locator('[data-session-error]').first().locator('summary').click();
  await page.locator('[data-session-error]').first().locator('[data-export-diagnostics]').click();await expect(page.locator('[data-diagnostics-json]')).toContainText('batc.diagnostics.v1');
  const bundle=JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);
  expect(bundle.recent_errors.some(e=>e.path_template==='/v1/sessions/:host/:session'&&e.status===503&&e.code==='BAT_ERROR')).toBe(true);
  expect(JSON.stringify(bundle)).not.toMatch(/private-box|private-session/);
  if(native){expect(bundle.client.app_version).toBe('0.1.1');expect(bundle.client.app_commit).toBe('b'.repeat(40));
   await expect(page.locator('[data-diagnostics-summary]')).toContainText(locale==='en'?'Desktop 0.1.1 (bbbbbbb)':'桌面版 0.1.1（bbbbbbb）');}
 });
});
test('busy focus: Save keeps the keyboard while the save is held',async({page})=>{
 const state=await diagnosticsFixture(page,true);await open(page,'en');
 await page.evaluate(()=>{
  const env=(window as any).__diagHold={calls:0,release:null};
  const orig=(window as any).diagnosticsSave.bind(window);
  (window as any).diagnosticsSave=async(input:any)=>{env.calls+=1;if(env.calls===1)await new Promise<void>(resolve=>{env.release=resolve;});return orig(input);};
 });
 const dialog=page.getByRole('dialog');
 const save=dialog.getByRole('button',{name:'Save',exact:true});
 const copy=dialog.getByRole('button',{name:'Copy',exact:true});
 const status=dialog.getByRole('status');
 await save.focus();
 await page.keyboard.press('Enter');
 await page.waitForFunction(()=>typeof (window as any).__diagHold.release==='function');
 await expect(save).toBeFocused();
 await expect(save).toHaveAttribute('aria-disabled','true');
 await expect(status).toHaveAttribute('aria-busy','true');
 await page.keyboard.press('Tab');
 await expect(copy).toBeFocused();
 await page.keyboard.press('Shift+Tab');
 await expect(save).toBeFocused();
 await page.keyboard.press('Shift+Tab');
 await expect(page.locator('[data-diagnostics-json]')).toBeFocused();
 await page.keyboard.press('Tab');
 await expect(save).toBeFocused();
 await page.keyboard.press('Enter');
 await page.evaluate(()=>(window as any).__diagHold.release());
 await expect(status).toHaveText('Saved');
 await expect(save).toBeFocused();
 await expect(save).not.toHaveAttribute('aria-disabled');
 await expect(status).not.toHaveAttribute('aria-busy');
 expect(await page.evaluate(()=>(window as any).__diagHold.calls)).toBe(1);
 expect(state.saved).toHaveLength(1);
});
test('busy focus: withdrawal during Save moves to Close',async({page})=>{
 const state=await diagnosticsFixture(page,true);await open(page,'en');
 await page.evaluate(()=>{
  const env=(window as any).__diagHold={calls:0,release:null};
  const orig=(window as any).diagnosticsSave.bind(window);
  (window as any).diagnosticsSave=async(input:any)=>{env.calls+=1;if(env.calls===1)await new Promise<void>(resolve=>{env.release=resolve;});return orig(input);};
 });
 const dialog=page.getByRole('dialog');
 const save=dialog.getByRole('button',{name:'Save',exact:true});
 const close=dialog.getByRole('button',{name:'Close',exact:true});
 const status=dialog.getByRole('status');
 await save.focus();
 await page.keyboard.press('Enter');
 await page.waitForFunction(()=>typeof (window as any).__diagHold.release==='function');
 await expect(save).toBeFocused();
 state.eventStatus=403;
 await expect(status).toContainText('Source access changed',{timeout:15000});
 await expect(close).toBeFocused();
 await expect(save).toBeDisabled();
 await expect(save).not.toHaveAttribute('aria-disabled');
 await expect(status).not.toHaveAttribute('aria-busy');
 await page.evaluate(()=>(window as any).__diagHold.release());
 await expect.poll(()=>state.saved.length).toBe(1);
 await expect(close).toBeFocused();
 await expect(status).toContainText('Source access changed');
 expect(await page.evaluate(()=>(window as any).__diagHold.calls)).toBe(1);
});
test('session summary reflects client reads, then removes them after scope loss',async({page})=>{
 const state=await diagnosticsFixture(page);await page.goto('/dashboard/#/sessions');
 await expect(page.locator('.session-results')).toContainText('Private title');await open(page,'en');
 let bundle=JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);
 expect(bundle.sessions).toMatchObject({basis:'client_loaded_reads',sampled:2,loaded:1,streaming:1,unknown_activity:0,stale:1,pending:1,history_reasons:null});
 await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();state.scopes=[];
 const before=state.reads.length;await page.locator('[data-export-diagnostics]').click();await expect(page.locator('[data-diagnostics-json]')).toContainText('batc.diagnostics.v1');
 bundle=JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);
 expect(bundle.sessions.sampled).toBeNull();expect(bundle.hosts).toEqual([]);expect(bundle.operations).toBeNull();
 expect(state.reads.slice(before).filter(path=>path.startsWith('/hosts')||path.startsWith('/operations')||path.startsWith('/managed/setup'))).toEqual([]);
});
for(const native of [false,true])for(const omitReading of [false,true])test(`history counts use real message-response reading, survive inventory, and clear when ${omitReading?'reading':'reasons'} absent (${native?'native':'browser'})`,async({page})=>{
 const state=await diagnosticsFixture(page,native);state.sessionReads=true;
 await page.goto('/dashboard/#/session/private-box.internal/sample-1');await expect(page.getByText('Private history message',{exact:true})).toBeVisible();
 await page.goto('/dashboard/#/sessions');await expect(page.locator('.session-results')).toContainText('Private title');await open(page,'en');
 let bundle=JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);
 expect(bundle.sessions.history_reasons).toEqual({archive_unconfirmed:1,archive_budget:1,message_ids:1});
 expect(JSON.stringify(bundle)).not.toContain('Private history message');
 await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();state.historyReasons=undefined;state.omitReading=omitReading;
 await page.goto('/dashboard/#/session/private-box.internal/sample-1');await expect(page.getByText('Private history message',{exact:true})).toBeVisible();await open(page,'en');
 bundle=JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);expect(bundle.sessions.history_reasons).toBeNull();
});
test('withdrawal clears preview facts and actions',async({page})=>{
 const state=await diagnosticsFixture(page);await open(page,'en');state.eventStatus=403;
 await expect(page.getByRole('dialog').getByRole('status')).toContainText('Source access changed',{timeout:15000});
 await expect(page.locator('[data-diagnostics-json]')).toBeEmpty();await expect(page.locator('[data-diagnostics-summary]')).toBeEmpty();
 await expect(page.getByRole('dialog').getByRole('button',{name:'Save',exact:true})).toBeDisabled();await expect(page.getByRole('dialog').getByRole('button',{name:'Copy',exact:true})).toBeDisabled();
});
test('late read cannot revive a withdrawn source',async({page})=>{
 const state=await diagnosticsFixture(page);await page.goto('/dashboard/#/settings');await about(page,'en').click();state.hold=true;state.operationsStatus=403;
 await page.locator('[data-export-diagnostics]').click();await expect.poll(()=>state.release!==null).toBe(true);state.release();
 await expect(page.locator('[data-diagnostics-json]')).toContainText('batc.diagnostics.v1');
 const bundle=JSON.parse((await page.locator('[data-diagnostics-json]').textContent())!);expect(bundle.hosts).toEqual([]);expect(bundle.operations).toBeNull();expect(bundle.sessions.loaded).toBeNull();
});
for(const locale of ['en','zh-TW'])for(const width of [1440,768,390])test(`visual ${locale} ${width}`,async({page})=>{
 await page.setViewportSize({width,height:width===390?844:900});
 // Locale comes from browser context, so set it using a new context in the visual fixture.
 const context=await page.context().browser()!.newContext({locale,viewport:{width,height:width===390?844:900}}),shot=await context.newPage();
 await diagnosticsFixture(shot);await shot.goto((test.info().project.use.baseURL as string)+'/dashboard/#/settings');await about(shot,locale).click();await shot.locator('[data-export-diagnostics]').click();await expect(shot.locator('[data-diagnostics-json]')).toContainText('batc.diagnostics.v1');
 expect(await shot.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 if(process.env.BATC_DIAGNOSTICS_SHOTS){await mkdir(process.env.BATC_DIAGNOSTICS_SHOTS,{recursive:true});await shot.screenshot({path:process.env.BATC_DIAGNOSTICS_SHOTS+`/diagnostics-${locale}-${width}.png`});}
 await context.close();
});
for(const locale of ['en','zh-TW'])for(const width of [1440,390])test(`review layouts ${locale} ${width}`,async({browser,baseURL})=>{
 const context=await browser.newContext({locale,baseURL,viewport:{width,height:width===390?844:900}});
 const capture=async(page,name)=>{
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  if(process.env.BATC_DIAGNOSTICS_SHOTS){await mkdir(process.env.BATC_DIAGNOSTICS_SHOTS,{recursive:true});await page.screenshot({path:`${process.env.BATC_DIAGNOSTICS_SHOTS}/review-${name}-${locale}-${width}.png`});}
 };
 for(const native of [false,true]){
  const page=await context.newPage();await diagnosticsFixture(page,native);await page.goto('/dashboard/#/settings');await about(page,locale).click();
  await expect(page.locator('[data-build-line]')).toContainText('Connector 0.2.5');
  await page.locator('[data-export-diagnostics]').scrollIntoViewIfNeeded();
  await capture(page,`about-${native?'native':'browser'}`);
  if(native){
   await page.locator('[data-export-diagnostics]').click();await expect(page.locator('[data-diagnostics-json]')).toContainText('batc.diagnostics.v1');
   await page.getByRole('dialog').getByRole('button',{name:locale==='en'?'Save':'儲存',exact:true}).click();
   await expect(page.getByRole('dialog').getByRole('status')).toHaveText(locale==='en'?'Saved':'已儲存');
   await capture(page,'native-saved');
  }
  await page.close();
 }
 const page=await context.newPage();await diagnosticsFixture(page);await page.goto('/dashboard/#/session/private-box.internal/private-session');
 const error=page.locator('[data-session-error]').first();await error.locator('summary').click();
 await expect(error.locator('[data-export-diagnostics]')).toBeVisible();await error.locator('[data-export-diagnostics]').scrollIntoViewIfNeeded();
 expect(await error.locator('xpath=ancestor::*[contains(@class,"session-status")][1]').evaluate(node=>node.scrollHeight<=node.clientHeight)).toBe(true);
 await capture(page,'error-details');
 await context.close();
});
