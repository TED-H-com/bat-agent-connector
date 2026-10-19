import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {fleetDiagnosticsTests} from './fleet-diagnostics';
const fixture=JSON.parse(readFileSync(new URL('./fixtures/fleet-status.json',import.meta.url),'utf8')).result;
fleetDiagnosticsTests(setup, true);
async function setup(page:any,options:any={}) {
 if(options.clock)await page.clock.install();
 await page.addInitScript(({doc,options}:any)=>{
 Object.assign(doc,{control_version:1,backend:'powershell',login:{show_picker:true,revision:'f'.repeat(64)},pending_migration:null,profiles:[{id:'default',label:'Local BAT',connection:null},{id:'profile-1',label:'Fixture 1',connection:'node-1'},{id:'profile-2',label:'Fixture 2',connection:'node-2'}]});Object.assign(doc.selection,{profiles:['profile-1'],dashboard:true});
 const env:any={doc,calls:[],receipt:null,migration:null,lose:null,wrongReceipt:false,hold:null,...options};
 if(options.damaged)sessionStorage.setItem('batc.desktop.fleet.controls.'+doc.configuration.binding,JSON.stringify({launch:{preview_id:'1'.repeat(32),attempted:true}}));
 Object.assign(window,{__fleet:env,isTauri:true,__TAURI_INTERNALS__:{invoke:async(command:string,args:any)=>{
  if(command==='native_status')return {endpoint:null,error:'Fixture central offline',credential_available:false};
  if(command==='connector_connect')throw new Error('Fixture central offline');
  if(command==='fleet_availability')return {configured:true,platform_supported:true,native_controls:true};
  if(!['fleet_control','fleet_request'].includes(command))throw new Error('Unexpected '+command);
  const input=structuredClone(args.input);env.calls.push(input);
  if(env.hold===input.action)await new Promise<void>(resolve=>{env.release=resolve;});
  let result:any={};
  switch(input.action){
   case 'overview':if(env.failReads)throw new Error('Fixture status unavailable');return structuredClone(env.doc);
   case 'preview_choices':env.choices=input.choices;result={preview_id:'2'.repeat(32),summary:{choices:input.choices,added_connections:input.choices.profiles.includes('profile-2')?['node-2']:[]}};break;
   case 'apply_choices':Object.assign(env.doc.selection,env.choices,{revision:'e'.repeat(64)});result=env.doc;break;
   case 'save_login':env.doc.login={show_picker:input.show_picker,revision:'a'.repeat(64)};result=env.doc.login;break;
   case 'preview_launch':result={preview_id:'3'.repeat(32),summary:{launch_id:'3'.repeat(32),profiles:[...env.doc.selection.profiles],dashboard:env.doc.selection.dashboard,opens_bat:env.doc.selection.profiles.length>0,already_running:false,bat_may_open_local_window:true}};env.launch=result;break;
   case 'launch':env.receipt={launch_id:input.preview_id,profiles:[...env.launch.summary.profiles],dashboard:env.launch.summary.dashboard,state:'started'};if(!env.launch.summary.opens_bat)env.receipt={state:'no_bat',summary:env.launch.summary};result=env.receipt;break;
   case 'launch_status':result=env.receipt?.state==='no_bat'?null:env.receipt;if(env.wrongReceipt&&result)result={...result,profiles:['different-profile']};break;
   case 'preview_migration':result={preview_id:'4'.repeat(32),fingerprint:'a'.repeat(64),from:env.doc.backend,to:input.backend,autostart_before:false,autostart:input.autostart};break;
   case 'apply_migration':env.migration={id:input.preview_id,phase:'prepared'};env.doc.pending_migration=input.preview_id;result=env.migration;break;
   case 'migration_status':if(!env.migration)throw new Error('missing receipt');result=env.migration;break;
   case 'advance_migration':env.migration.phase='complete';env.doc.pending_migration=null;result=env.migration;break;
   case 'restore_migration':env.migration={id:input.restore_id,phase:'prepared'};env.doc.pending_migration=input.restore_id;result=env.migration;break;
   case 'ensure_monitor':env.doc.monitor={state:'running',epoch:'a'.repeat(32),controllable:true};break;
   case 'quit_owned':env.doc.monitor={state:'stopped',epoch:null,controllable:false};break;
   case 'discard':break;
   default:throw new Error('unexpected action '+input.action);
  }
  if(env.lose===input.action){env.lose=null;throw new Error('Fixture lost reply');}return structuredClone(result);
 }}});
 },{doc:fixture,options});
 await page.goto('/dashboard/#/settings');await page.getByRole('link',{name:/^(Fleet|機群)$/}).click();if(!options.failReads)await expect(page.locator('[data-fleet-choice="dashboard:dashboard"]')).toBeVisible();
}
const calls=(page:any,action:string)=>page.evaluate((a:string)=>(window as any).__fleet.calls.filter((c:any)=>c.action===a),action);
const refresh=(page:any)=>page.getByRole('button',{name:'Read status',exact:true}).first().click();
for(const locale of ['en-US','zh-TW'])for(const width of [390,768,1440])test(`native Fleet organization ${locale} ${width}`,async({browser},testInfo)=>{
 const context=await browser.newContext({locale,viewport:{width,height:900}}),page=await context.newPage();const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await setup(page);await expect(page.locator('[data-fleet-login="picker"]')).toBeChecked();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);await page.screenshot({path:testInfo.outputPath(`fleet-${locale}-${width}.png`),fullPage:true});await context.close();
});
test('independent choices require prerequisite review; cancel writes nothing',async({page})=>{
 await setup(page);await page.locator('[data-fleet-choice="connections:node-2"]').uncheck();await page.locator('[data-fleet-choice="profiles:profile-2"]').check();await page.locator('[data-fleet-choice="dashboard:dashboard"]').uncheck();await page.getByRole('button',{name:'Review choices',exact:true}).click();await expect.poll(()=>calls(page,'preview_choices')).toHaveLength(1);expect((await calls(page,'preview_choices'))[0].choices).toMatchObject({profiles:['profile-1','profile-2'],dashboard:false});expect(await calls(page,'apply_choices')).toHaveLength(0);await page.getByRole('button',{name:'Use current selection',exact:true}).click();expect(await calls(page,'apply_choices')).toHaveLength(0);await expect(page.locator('[data-fleet-choice="dashboard:dashboard"]')).toBeChecked();
});
test('lost choice reply reads exact values without another apply or launch',async({page})=>{
 await setup(page,{lose:'apply_choices'});await page.locator('[data-fleet-choice="dashboard:dashboard"]').uncheck();await page.getByRole('button',{name:'Review choices',exact:true}).click();await page.getByRole('button',{name:'Save reviewed choices',exact:true}).click();await refresh(page);expect(await calls(page,'apply_choices')).toHaveLength(1);expect(await calls(page,'launch')).toHaveLength(0);await expect(page.locator('[data-fleet-choice="dashboard:dashboard"]')).not.toBeChecked();
});
test('login picker save never changes Fleet selection or launches',async({page})=>{
 await setup(page);await page.locator('[data-fleet-login="picker"]').uncheck();await expect.poll(()=>calls(page,'save_login')).toEqual([{action:'save_login',expected_revision:'f'.repeat(64),show_picker:false}]);expect(await calls(page,'apply_choices')).toHaveLength(0);expect(await calls(page,'launch')).toHaveLength(0);
});
async function launch(page:any){await page.getByRole('button',{name:'Review launch',exact:true}).click();await page.getByRole('button',{name:'Open saved selection',exact:true}).click();}
test('lost BAT reply retains original ID; reload never launches automatically',async({page})=>{
 await setup(page,{lose:'launch'});await launch(page);await refresh(page);expect(await calls(page,'launch')).toEqual([{action:'launch',preview_id:'3'.repeat(32)}]);await expect(page.getByRole('button',{name:'New launch review'})).toBeVisible();await page.reload();await expect(page.getByRole('button',{name:'Read status',exact:true}).first()).toBeVisible();expect(await calls(page,'launch')).toHaveLength(0);expect(await calls(page,'preview_launch')).toHaveLength(0);
});
test('wrong-profile receipt cannot release an uncertain launch',async({page})=>{
 await setup(page,{lose:'launch'});await launch(page);await page.evaluate(()=>{(window as any).__fleet.wrongReceipt=true});await refresh(page);await expect(page.getByRole('button',{name:'New launch review'})).toHaveCount(0);expect(await calls(page,'launch')).toHaveLength(1);
});
test('migration draft survives refresh and recovery uses original receipt',async({page})=>{
 await setup(page,{lose:'apply_migration'});await page.locator('[data-fleet-advanced="settings"]').click();await page.getByRole('combobox',{name:'Connection monitor and startup'}).selectOption('rust');await page.locator('[data-fleet-autostart="enabled"]').check();await refresh(page);await page.getByRole('button',{name:'Review monitor and startup',exact:true}).click();await expect.poll(()=>calls(page,'preview_migration')).toEqual([{action:'preview_migration',backend:'rust',autostart:true}]);await page.getByRole('button',{name:'Apply reviewed transition',exact:true}).click();await refresh(page);expect(await calls(page,'apply_migration')).toHaveLength(1);expect(await calls(page,'advance_migration')).toHaveLength(0);await page.getByRole('button',{name:'Continue original transition',exact:true}).click();await expect.poll(()=>calls(page,'advance_migration')).toEqual([{action:'advance_migration',migration_id:'4'.repeat(32)}]);await expect(page.getByRole('button',{name:'Restore original settings',exact:true})).toBeVisible();
});
test('advanced connection settings stay folded until needed and keep their state across reads', async ({page}) => {
 await setup(page, {clock: true});
 const backend = page.locator('[data-fleet-backend="choice"]');
 const summary = page.locator('[data-fleet-advanced="settings"]');
 await expect(backend).not.toBeVisible();
 await summary.click();
 await expect(backend).toBeVisible();
 await page.clock.fastForward(5000);
 await expect(summary).toBeFocused();
 await expect(backend).toBeVisible();
 await summary.click();
 await page.clock.fastForward(5000);
 await expect(backend).not.toBeVisible();
 expect(await calls(page, 'apply_migration')).toHaveLength(0);
});
test('changed owner blocks stale draft; new configuration has separate draft',async({page})=>{
 await setup(page);await page.locator('[data-fleet-choice="dashboard:dashboard"]').uncheck();await page.evaluate(()=>{(window as any).__fleet.doc.monitor.epoch='b'.repeat(32)});await refresh(page);await expect(page.getByRole('button',{name:'Review choices',exact:true})).toBeDisabled();await page.evaluate(()=>{(window as any).__fleet.doc.configuration.binding='e'.repeat(64)});await refresh(page);await expect(page.locator('[data-fleet-choice="dashboard:dashboard"]')).toBeChecked();expect(await calls(page,'apply_choices')).toHaveLength(0);
});
test('damaged saved summary retains recoverable ID without crash or launch',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await setup(page,{damaged:true});await refresh(page);expect(await calls(page,'launch')).toHaveLength(0);expect(await calls(page,'launch_status')).toContainEqual({action:'launch_status',launch_id:'1'.repeat(32)});expect(errors).toEqual([]);
});
test('Dashboard-only launch survives reload without BAT receipt or another launch',async({page})=>{
 await setup(page);await page.evaluate(()=>{(window as any).__fleet.doc.selection.profiles=[]});await refresh(page);await launch(page);await expect(page.getByRole('button',{name:'New launch review'})).toBeVisible();await page.reload();await expect(page.getByRole('button',{name:'New launch review'})).toBeVisible();expect(await calls(page,'launch')).toHaveLength(0);
});
const overviews=(page:any)=>page.evaluate(()=>(window as any).__fleet.calls.filter((call:{action:string})=>call.action==='overview').length);
const releaseHold=(page:any)=>page.evaluate(()=>{const env=(window as any).__fleet;env.hold=null;env.release?.();});
const controlsKey='batc.desktop.fleet.controls.'+'c'.repeat(64);

test('a status poll keeps button focus',async({page})=>{
 await setup(page,{clock:true});
 const region=page.getByRole('region',{name:'Local Fleet and windows'});
 const refresh=region.getByRole('button',{name:'Read status',exact:true});
 const quit=region.getByRole('button',{name:'Stop local connection monitor',exact:true});
 await expect(quit).toBeEnabled();
 await refresh.focus();
 const before=await overviews(page);
 await page.clock.fastForward(5000);
 await expect.poll(()=>overviews(page)).toBe(before+1);
 await expect(refresh).toBeFocused();
 await quit.focus();
 await page.clock.fastForward(5000);
 await expect.poll(()=>overviews(page)).toBe(before+2);
 await expect(quit).toBeFocused();
});

test('a status poll keeps connection checkbox focus',async({page})=>{
 await setup(page,{clock:true});
 const box=page.locator('[data-fleet-choice="connections:node-1"]');
 await box.focus();
 await expect(box).toBeChecked();
 const stored=()=>page.evaluate((key:string)=>sessionStorage.getItem(key),controlsKey);
 const beforeStored=await stored();
 await page.evaluate(()=>{(window as any).__fleet.hold='overview';});
 const before=await overviews(page);
 await page.clock.fastForward(5000);
 await expect.poll(()=>overviews(page)).toBe(before+1);
 await expect(box).toBeFocused();
 await expect(box).toHaveAttribute('aria-disabled','true');
 expect(await box.evaluate(node=>(node as HTMLInputElement).disabled)).toBe(false);
 await page.keyboard.press('Space');
 await expect(box).toBeChecked();
 expect(await stored()).toBe(beforeStored);
 await releaseHold(page);
 await expect(box).not.toHaveAttribute('aria-disabled');
 await expect(box).toBeFocused();
 await expect(box).toBeChecked();
 expect(await stored()).toBe(beforeStored);
});

test('a status poll does not write the status message',async({page})=>{
 await setup(page,{clock:true});
 await page.evaluate(()=>{
  const message=document.querySelector('.fleet-desktop [role="status"]') as HTMLElement;
  const seen:string[]=[];
  (window as any).__fleetMessages=seen;
  let last=message.textContent;
  new MutationObserver(()=>{const text=message.textContent??'';if(text!==last){seen.push(text);last=text;}}).observe(message,{childList:true,characterData:true,subtree:true});
 });
 const before=await overviews(page);
 await page.clock.fastForward(5000);
 await expect.poll(()=>overviews(page)).toBe(before+1);
 expect(await page.evaluate(()=>(window as any).__fleetMessages)).toEqual([]);
});

test('refresh keeps focus and ignores a second press',async({page})=>{
 await setup(page,{clock:true});
 const region=page.getByRole('region',{name:'Local Fleet and windows'});
 const refresh=region.getByRole('button',{name:'Read status',exact:true});
 const status=region.getByRole('status');
 await page.evaluate(()=>{(window as any).__fleet.hold='overview';});
 const before=await overviews(page);
 await refresh.focus();
 await page.keyboard.press('Enter');
 await expect(refresh).toHaveAttribute('aria-disabled','true');
 await expect(refresh).toBeFocused();
 await expect(status).toHaveAttribute('aria-busy','true');
 expect(await refresh.evaluate(node=>(node as HTMLButtonElement).disabled)).toBe(false);
 await page.keyboard.press('Enter');
 await refresh.evaluate(node=>(node as HTMLButtonElement).click());
 await releaseHold(page);
 await expect(refresh).not.toHaveAttribute('aria-disabled');
 await expect(status).not.toHaveAttribute('aria-busy');
 expect(await overviews(page)).toBe(before+1);
 await expect(refresh).toBeFocused();
});

test('a held read gives Use current, a profile and the backend no new effect',async({page})=>{
 await setup(page,{clock:true});
 await page.locator('[data-fleet-advanced="settings"]').click();
 const region=page.getByRole('region',{name:'Local Fleet and windows'});
 await page.locator('[data-fleet-choice="profiles:profile-2"]').check();
 const stored=()=>page.evaluate((key:string)=>sessionStorage.getItem(key),controlsKey);
 const beforeStored=await stored();
 const calls=()=>page.evaluate(()=>(window as any).__fleet.calls.length as number);
 const beforeCalls=await calls();
 await page.evaluate(()=>{(window as any).__fleet.hold='overview';});
 const refresh=region.getByRole('button',{name:'Read status',exact:true});
 await refresh.focus();
 await page.keyboard.press('Enter');
 await expect(refresh).toHaveAttribute('aria-disabled','true');
 const useCurrent=region.getByRole('button',{name:'Use current selection',exact:true});
 const profile=page.locator('[data-fleet-choice="profiles:profile-1"]');
 const added=page.locator('[data-fleet-choice="profiles:profile-2"]');
 const backend=region.getByRole('combobox',{name:'Connection monitor and startup'});
 await expect(useCurrent).toHaveAttribute('aria-disabled','true');
 await expect(profile).toHaveAttribute('aria-disabled','true');
 await expect(backend).toHaveAttribute('aria-disabled','true');
 expect(await useCurrent.evaluate(node=>(node as HTMLButtonElement).disabled)).toBe(false);
 expect(await profile.evaluate(node=>(node as HTMLInputElement).disabled)).toBe(false);
 expect(await backend.evaluate(node=>(node as HTMLSelectElement).disabled)).toBe(false);
 await useCurrent.evaluate(node=>(node as HTMLButtonElement).click());
 await profile.evaluate(node=>(node as HTMLInputElement).click());
 await backend.evaluate(node=>{const select=node as HTMLSelectElement;select.value='rust';select.dispatchEvent(new Event('change',{bubbles:true}));});
 await expect(added).toBeChecked();
 await expect(profile).toBeChecked();
 await expect(backend).toHaveValue('powershell');
 expect(await stored()).toBe(beforeStored);
 expect(await calls()).toBe(beforeCalls+1);
 await releaseHold(page);
 await expect(refresh).not.toHaveAttribute('aria-disabled');
 expect(await calls()).toBe(beforeCalls+1);
 expect(await stored()).toBe(beforeStored);
 await expect(backend).toHaveValue('powershell');
 await expect(profile).toBeChecked();
 await expect(added).toBeChecked();
});

test('automatic login readback retains fixed original preview and does not auto-retry from UI',async({page})=>{
 await setup(page);await page.evaluate(()=>{const e=(window as any).__fleet;e.doc.login_launch={configuration_binding:e.doc.configuration.binding,preview_id:'5'.repeat(32),summary:{launch_id:'5'.repeat(32),profiles:['profile-1'],dashboard:true,opens_bat:true},state:'attention',code:'BAT_READINESS_PENDING'}});await refresh(page);await expect(page.getByRole('button',{name:'Retry original launch'})).toBeVisible();expect(await calls(page,'preview_launch')).toHaveLength(0);expect(await calls(page,'launch')).toHaveLength(0);expect(await calls(page,'launch_status')).toContainEqual({action:'launch_status',launch_id:'5'.repeat(32)});
});

for (const locale of ['en-US','zh-TW']) test(`restricted launching job has a localized Fleet warning (${locale})`, async ({browser}) => {
 const context=await browser.newContext({locale}),page=await context.newPage();
 await setup(page);
 await page.evaluate(()=>{(window as any).__fleet.doc.launch_warning='FLEET_SESSION_LIFETIME';});
 await page.locator('[data-fleet-action="refresh"]').click();
 const warning=page.locator('[data-fleet-lifetime]');
 await expect(warning.locator('p')).toHaveText(locale==='zh-TW'
   ?'啟動環境不允許背景連線監控獨立執行。關閉啟動工作階段時，本機連線可能中斷。'
   :'The launching environment prevents the connection monitor from running independently. Local connections may stop when the launching session ends.');
 await warning.locator('summary').click();
 await expect(warning.locator('code')).toHaveText('FLEET_SESSION_LIFETIME');
 expect(await calls(page,'ensure_monitor')).toHaveLength(0);
 await context.close();
});

for (const locale of ['en-US', 'zh-TW']) test(`Tailscale wake and automatic retry status keeps codes in details ${locale}`, async ({browser}) => {
 const context = await browser.newContext({locale, viewport: {width: 390, height: 900}});
 const page = await context.newPage();
 await setup(page);
 const region = page.locator('.fleet-desktop');
 const refreshStatus = () => region.getByRole('button', {name: locale === 'zh-TW' ? '重新讀取狀態' : 'Read status', exact: true}).click();
 await page.evaluate(() => {
  const row = (window as any).__fleet.doc.readiness.hosts[0];
  Object.assign(row, {code: 'TAILSCALE_WAKING', level: 'degraded', blocking: 'tunnel'});
 });
 await refreshStatus();
 await expect(region.getByText(locale === 'zh-TW' ? '正在喚醒 Tailscale 連線' : 'Waking the Tailscale connection', {exact: true})).toBeVisible();
 await expect(region.getByText('TAILSCALE_WAKING', {exact: true})).not.toBeVisible();
 await page.evaluate(() => { (window as any).__fleet.doc.readiness.hosts[0].code = 'ROUTE_UNAVAILABLE'; });
 await refreshStatus();
 await expect(region.locator('[data-fleet-diagnosis="node-1"]')).toContainText(locale === 'zh-TW' ? '已設定的網路連線可用' : 'configured network connection is available');
 await expect(region.getByText('ROUTE_UNAVAILABLE', {exact: true})).not.toBeVisible();
 await region.locator('[data-fleet-details="node-1"]').click();
 await expect(region.getByText('ROUTE_UNAVAILABLE', {exact: true})).toBeVisible();
 expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
 await context.close();
});

test('desktop Fleet preserves unmapped blocking reasons and native diagnostic codes', async ({page}) => {
 await setup(page);
 const region = page.locator('.fleet-desktop');
 for (const native of [false, true]) {
  await page.evaluate((native: boolean) => {
   const row = (window as any).__fleet.doc.readiness.hosts[0];
   Object.assign(row, {level: 'down', code: native ? 'OWNER_UNPROVEN' : null, blocking: native ? 'tls' : 'OWNER_UNPROVEN'});
  }, native);
  await region.getByRole('button', {name: 'Read status', exact: true}).click();
  const status = region.locator('[data-fleet-diagnosis="node-1"]');
  await expect(status.getByText('The background connection outcome is unconfirmed', {exact: true})).toBeVisible();
  if (!native) await status.locator('summary').click();
  await expect(status.locator('code').filter({hasText: 'OWNER_UNPROVEN'})).toBeVisible();
  if (native) await expect(status.locator('code').filter({hasText: 'tls'})).toBeVisible();
 }
});
