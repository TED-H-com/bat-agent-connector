import {test, expect} from '@playwright/test';
import {readFileSync,mkdirSync} from 'node:fs';
const fixture=JSON.parse(readFileSync(new URL('./fixtures/fleet-status.json',import.meta.url),'utf8')).result;
async function setup(page:any,options:any={}) {
 await page.addInitScript(({doc,options}:any)=>{
 Object.assign(doc,{control_version:1,backend:'powershell',login:{show_picker:true,revision:'f'.repeat(64)},pending_migration:null,profiles:[{id:'default',label:'Local BAT',connection:null},{id:'profile-1',label:'Fixture 1',connection:'node-1'},{id:'profile-2',label:'Fixture 2',connection:'node-2'}]});Object.assign(doc.selection,{profiles:['profile-1'],dashboard:true});
 const env:any={doc,calls:[],receipt:null,migration:null,lose:null,wrongReceipt:false,...options};
 if(options.damaged)sessionStorage.setItem('batc.desktop.fleet.controls.'+doc.configuration.binding,JSON.stringify({launch:{preview_id:'1'.repeat(32),attempted:true}}));
 Object.assign(window,{__fleet:env,isTauri:true,__TAURI_INTERNALS__:{invoke:async(command:string,args:any)=>{
  if(command==='native_status')return {endpoint:null,error:'Fixture central offline',credential_available:false};
  if(command==='connector_connect')throw new Error('Fixture central offline');
  if(command==='fleet_availability')return {configured:true,platform_supported:true,native_controls:true};
  if(!['fleet_control','fleet_request'].includes(command))throw new Error('Unexpected '+command);
  const input=structuredClone(args.input);env.calls.push(input);let result:any={};
  switch(input.action){
   case 'overview':return structuredClone(env.doc);
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
 await page.goto('/dashboard/#/settings');await page.getByRole('link',{name:/^(Fleet|機群)$/}).click();await expect(page.locator('[data-fleet-choice="dashboard:dashboard"]')).toBeVisible();
}
const calls=(page:any,action:string)=>page.evaluate((a:string)=>(window as any).__fleet.calls.filter((c:any)=>c.action===a),action);
const refresh=(page:any)=>page.getByRole('button',{name:'Read status',exact:true}).first().click();
for(const locale of ['en-US','zh-TW'])for(const width of [390,768,1440])test(`native Fleet organization ${locale} ${width}`,async({browser})=>{
 const context=await browser.newContext({locale,viewport:{width,height:900}}),page=await context.newPage();const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await setup(page);await expect(page.locator('[data-fleet-login="picker"]')).toBeChecked();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);mkdirSync('/tmp/bac-fleet-native-after',{recursive:true});await page.screenshot({path:`/tmp/bac-fleet-native-after/fleet-${locale}-${width}.png`,fullPage:true});await context.close();
});
test('independent choices require prerequisite review; cancel writes nothing',async({page})=>{
 await setup(page);await page.locator('[data-fleet-choice="connections:node-2"]').uncheck();await page.locator('[data-fleet-choice="profiles:profile-2"]').check();await page.locator('[data-fleet-choice="dashboard:dashboard"]').uncheck();await page.getByRole('button',{name:'Review choices',exact:true}).click();expect((await calls(page,'preview_choices'))[0].choices).toMatchObject({profiles:['profile-1','profile-2'],dashboard:false});expect(await calls(page,'apply_choices')).toHaveLength(0);await page.getByRole('button',{name:'Use current selection',exact:true}).click();expect(await calls(page,'apply_choices')).toHaveLength(0);await expect(page.locator('[data-fleet-choice="dashboard:dashboard"]')).toBeChecked();
});
test('lost choice reply reads exact values without another apply or launch',async({page})=>{
 await setup(page,{lose:'apply_choices'});await page.locator('[data-fleet-choice="dashboard:dashboard"]').uncheck();await page.getByRole('button',{name:'Review choices',exact:true}).click();await page.getByRole('button',{name:'Save reviewed choices',exact:true}).click();await refresh(page);expect(await calls(page,'apply_choices')).toHaveLength(1);expect(await calls(page,'launch')).toHaveLength(0);await expect(page.locator('[data-fleet-choice="dashboard:dashboard"]')).not.toBeChecked();
});
test('login picker save never changes Fleet selection or launches',async({page})=>{
 await setup(page);await page.locator('[data-fleet-login="picker"]').uncheck();expect(await calls(page,'save_login')).toEqual([{action:'save_login',expected_revision:'f'.repeat(64),show_picker:false}]);expect(await calls(page,'apply_choices')).toHaveLength(0);expect(await calls(page,'launch')).toHaveLength(0);
});
async function launch(page:any){await page.getByRole('button',{name:'Review launch',exact:true}).click();await page.getByRole('button',{name:'Open saved selection',exact:true}).click();}
test('lost BAT reply retains original ID; reload never launches automatically',async({page})=>{
 await setup(page,{lose:'launch'});await launch(page);await refresh(page);expect(await calls(page,'launch')).toEqual([{action:'launch',preview_id:'3'.repeat(32)}]);await expect(page.getByRole('button',{name:'New launch review'})).toBeVisible();await page.reload();await expect(page.getByRole('button',{name:'Read status',exact:true}).first()).toBeVisible();expect(await calls(page,'launch')).toHaveLength(0);expect(await calls(page,'preview_launch')).toHaveLength(0);
});
test('wrong-profile receipt cannot release an uncertain launch',async({page})=>{
 await setup(page,{lose:'launch'});await launch(page);await page.evaluate(()=>{(window as any).__fleet.wrongReceipt=true});await refresh(page);await expect(page.getByRole('button',{name:'New launch review'})).toHaveCount(0);expect(await calls(page,'launch')).toHaveLength(1);
});
test('migration draft survives refresh and recovery uses original receipt',async({page})=>{
 await setup(page,{lose:'apply_migration'});await page.getByRole('combobox',{name:'Connection monitor and startup'}).selectOption('rust');await page.locator('[data-fleet-autostart="enabled"]').check();await refresh(page);await page.getByRole('button',{name:'Review monitor and startup',exact:true}).click();expect(await calls(page,'preview_migration')).toEqual([{action:'preview_migration',backend:'rust',autostart:true}]);await page.getByRole('button',{name:'Apply reviewed transition',exact:true}).click();await refresh(page);expect(await calls(page,'apply_migration')).toHaveLength(1);expect(await calls(page,'advance_migration')).toHaveLength(0);await page.getByRole('button',{name:'Continue original transition',exact:true}).click();expect(await calls(page,'advance_migration')).toEqual([{action:'advance_migration',migration_id:'4'.repeat(32)}]);await expect(page.getByRole('button',{name:'Restore original settings',exact:true})).toBeVisible();
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
test('automatic login readback retains fixed original preview and does not auto-retry from UI',async({page})=>{
 await setup(page);await page.evaluate(()=>{const e=(window as any).__fleet;e.doc.login_launch={configuration_binding:e.doc.configuration.binding,preview_id:'5'.repeat(32),summary:{launch_id:'5'.repeat(32),profiles:['profile-1'],dashboard:true,opens_bat:true},state:'attention',code:'BAT_READINESS_PENDING'}});await refresh(page);await expect(page.getByRole('button',{name:'Retry original launch'})).toBeVisible();expect(await calls(page,'preview_launch')).toHaveLength(0);expect(await calls(page,'launch')).toHaveLength(0);expect(await calls(page,'launch_status')).toContainEqual({action:'launch_status',launch_id:'5'.repeat(32)});
});
