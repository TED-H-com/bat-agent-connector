import {test, expect} from '@playwright/test';
import {readFileSync, mkdirSync} from 'node:fs';
const fixture=JSON.parse(readFileSync(new URL('./fixtures/fleet-status.json',import.meta.url),'utf8')).result;
const ID='1'.repeat(32), BINDING='a'.repeat(64);
async function setup(page:any, options:any={}) {
 await page.addInitScript(({doc,options}:any)=>{
  Object.assign(doc,{control_version:1,backend:'rust',login:{show_picker:true,revision:'f'.repeat(64)},pending_migration:null,profiles:[]});
  Object.assign(doc.selection,{profiles:[],dashboard:true});
  const storage='fixture.bootstrap.native-journal';
  const saved=JSON.parse(sessionStorage.getItem(storage)||'null');
  const env:any={calls:[],latest:saved,configured:true,eligible:true,auto:false,lose:null,wrong:null,...options};
  const persist=()=>sessionStorage.setItem(storage,JSON.stringify(env.latest));
  const original=()=>({recipe_binding:'a'.repeat(64),status:{request_id:'1'.repeat(32),phase:'querying',queries:0,ensure_requested:false,ensure_accepted:false,last_state:null}});
  if(options.nativeOriginal&&!env.latest){env.latest=original();persist();}
  if(options.exhausted){env.latest=original();Object.assign(env.latest.status,{queries:4,ensure_requested:true,phase:'needs_attention',last_state:'unknown'});persist();}
  if(options.storageFailure){const set=Storage.prototype.setItem;Storage.prototype.setItem=function(k:string,v:string){if(k==='batc.desktop.fleet.bootstrap.original')throw new Error('synthetic storage failure');return set.call(this,k,v)}}
  Object.assign(window,{__bootstrap:env,isTauri:true,__TAURI_INTERNALS__:{invoke:async(command:string,args:any)=>{
  if(command==='native_status')return {endpoint:'http://127.0.0.1:19796',error:null,credential_available:true};
   if(command==='connector_connect'){env.connects=(env.connects||0)+1;throw new Error('Fixture central offline');}
   if(command==='fleet_availability')return {configured:true,platform_supported:true,native_controls:true,bootstrap_controls:true};
   if(command==='fleet_control'||command==='fleet_request')return structuredClone(doc);
   if(command!=='fleet_bootstrap')throw new Error('Unexpected '+command);
   const input=structuredClone(args.input);env.calls.push(input);
   if(input.action==='overview')return {version:1,configured:env.configured,eligible:env.eligible,auto_ensure:env.auto,recipe_binding:env.configured?(env.binding||'a'.repeat(64)):null,code:env.eligible?null:'BOOTSTRAP_NOT_NEEDED',latest:structuredClone(env.latest)};
   if(input.action==='prepare'){if(!env.latest)env.latest=original();persist();}
   if(input.action==='advance'){
    if(input.request_id!==env.latest?.status.request_id||input.recipe_binding!==env.latest.recipe_binding)throw new Error('Wrong original');
    Object.assign(env.latest.status,{queries:1,ensure_requested:true,phase:'ensure_requested',last_state:'unknown'});persist();
    if(env.hold)await new Promise<void>(resolve=>env.release=resolve);
   }
   if(env.lose===input.action){env.lose=null;throw new Error('Synthetic reply lost');}
   const value=structuredClone(env.latest);
   if(env.wrong==='id'&&value)value.status.request_id='2'.repeat(32);
   if(env.wrong==='binding'&&value)value.recipe_binding='b'.repeat(64);
   return value;
  }}});
 },{doc:fixture,options});
 await page.goto('/dashboard/#/settings');
 await page.getByRole('tab',{name:/^(Fleet|機群)$/}).click();
 await expect(page.locator('.fleet-bootstrap')).toBeVisible();
 await expect(page.locator('.fleet-bootstrap [role=status]')).toHaveText('');
}
const panel=(page:any)=>page.locator('.fleet-bootstrap');
const calls=(page:any,action:string)=>page.evaluate((a:string)=>(window as any).__bootstrap.calls.filter((c:any)=>c.action===a),action);
const button=(page:any,name:string)=>panel(page).getByRole('button',{name,exact:true});
const prepare=(page:any)=>button(page,'Prepare fixed service request').click();
test('missing recipe and healthy central never offer remote repair',async({page})=>{
 await setup(page,{configured:false,eligible:false});
 await expect(button(page,'Prepare fixed service request')).toBeDisabled();
 await expect(panel(page)).toContainText('No startup recipe configured');
 expect(await calls(page,'prepare')).toHaveLength(0);expect(await calls(page,'advance')).toHaveLength(0);
 await page.evaluate(()=>Object.assign((window as any).__bootstrap,{configured:true,eligible:false}));
 await button(page,'Refresh startup status').click();await expect(panel(page)).toContainText('no startup is needed');
 await expect(button(page,'Prepare fixed service request')).toBeDisabled();
});
test('native request is persisted before ensure and original envelope is exact',async({page})=>{
 await setup(page);await prepare(page);
 await expect(panel(page)).toContainText('No remote query has been sent');
 expect(await calls(page,'advance')).toHaveLength(0);
 expect(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('batc.desktop.fleet.bootstrap.original')!))).toEqual({request_id:ID,recipe_binding:BINDING});
 await button(page,'Check and ensure existing service').click();
 expect(await calls(page,'advance')).toEqual([{action:'advance',request_id:ID,recipe_binding:BINDING}]);
 await expect(panel(page)).toContainText('only reconcile the original result');
});
test('lost ensure response and reload only read durable original without another ensure',async({page})=>{
 await setup(page,{lose:'advance'});await prepare(page);await button(page,'Check and ensure existing service').click();
 await expect(panel(page)).toContainText('Outcome not confirmed');
 await expect(button(page,'Prepare fixed service request')).toBeDisabled();
 await page.reload();await expect(panel(page)).toContainText(ID);
 await expect(button(page,'Reconcile original result')).toBeVisible();
 expect(await calls(page,'advance')).toHaveLength(0);expect(await calls(page,'prepare')).toHaveLength(0);
 expect(await calls(page,'receipt')).toContainEqual({action:'receipt',request_id:ID});
});
for(const wrong of ['id','binding'])test(`wrong ${wrong} receipt preserves frozen original`,async({page})=>{
 await setup(page,{lose:'advance'});await prepare(page);await button(page,'Check and ensure existing service').click();
 await page.evaluate((w:string)=>{(window as any).__bootstrap.wrong=w},wrong);
 await button(page,'Read original request').click();
 await expect(panel(page)).toContainText('Outcome not confirmed');
 await expect(button(page,'Check and ensure existing service')).toBeDisabled();
 expect(await page.evaluate(()=>JSON.parse(sessionStorage.getItem('batc.desktop.fleet.bootstrap.original')!).request_id)).toBe(ID);
 expect(await calls(page,'advance')).toHaveLength(1);
});
test('changed recipe retains read-only original instead of rebinding it',async({page})=>{
 await setup(page);await prepare(page);
 await page.evaluate(()=>{(window as any).__bootstrap.binding='b'.repeat(64)});
 await button(page,'Refresh startup status').click();await expect(panel(page)).toContainText('earlier fixed recipe');
 await expect(button(page,'Check and ensure existing service')).toBeDisabled();
 await button(page,'Read original request').click();expect(await calls(page,'advance')).toHaveLength(0);
});
test('exhausted original cannot be replaced by a new key or another ensure',async({page})=>{
 await setup(page,{exhausted:true});await expect(panel(page)).toContainText('4/4 queries used');
 await expect(button(page,'Prepare fixed service request')).toBeDisabled();await expect(button(page,'Reconcile original result')).toBeDisabled();
 await page.reload();await expect(panel(page)).toContainText(ID);expect(await calls(page,'advance')).toHaveLength(0);
});
test('opt-in automatic native receipt is adopted with UI reads only',async({page})=>{
 await setup(page,{nativeOriginal:true,auto:true});await expect(panel(page)).toContainText('Automatic check and ensure enabled');
 await expect(panel(page)).toContainText(ID);expect(await calls(page,'prepare')).toHaveLength(0);expect(await calls(page,'advance')).toHaveLength(0);
});
test('unavailable browser storage recovers the native original after reload',async({page})=>{
 await setup(page,{storageFailure:true});await prepare(page);await page.reload();
 await expect(panel(page)).toContainText(ID);expect(await calls(page,'prepare')).toHaveLength(0);expect(await calls(page,'advance')).toHaveLength(0);
});
test('remounting settings while remote result is pending preserves original readback',async({page})=>{
 await setup(page,{hold:true});await prepare(page);await button(page,'Check and ensure existing service').click();
 const old=await panel(page).elementHandle();
 await expect(page.locator('.native-connection').getByRole('button',{name:'Connect',exact:true})).toBeEnabled();
 await page.evaluate(()=>{location.hash='#/sessions'});await expect.poll(()=>old!.evaluate((element:Element)=>element.isConnected)).toBe(false);
 await page.evaluate(()=>{(window as any).__bootstrap.release()});
 await page.evaluate(()=>{location.hash='#/settings'});await expect(panel(page)).toContainText(ID);
 expect(await calls(page,'advance')).toHaveLength(1);expect(await calls(page,'prepare')).toHaveLength(1);
});
test('invalid refreshed receipt freezes an earlier prepared draft',async({page})=>{
 await setup(page);await prepare(page);await page.evaluate(()=>{(window as any).__bootstrap.wrong='binding'});
 await button(page,'Read original request').click();
 await expect(button(page,'Check and ensure existing service')).toBeDisabled();
 await expect(button(page,'Prepare fixed service request')).toBeDisabled();
 expect(await calls(page,'advance')).toHaveLength(0);
});
for(const locale of ['en-US','zh-TW'])for(const width of [390,768,1440])test(`bootstrap layout ${locale} ${width}`,async({browser})=>{
 const context=await browser.newContext({locale,viewport:{width,height:900}}),page=await context.newPage();
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await setup(page,{nativeOriginal:true,auto:true});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);
 mkdirSync('/tmp/bac-bootstrap-desktop-after',{recursive:true});await page.screenshot({path:`/tmp/bac-bootstrap-desktop-after/bootstrap-${locale}-${width}.png`,fullPage:true});await context.close();
});
