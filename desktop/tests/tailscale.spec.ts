import {test,expect} from '@playwright/test';
import {mkdirSync,readFileSync} from 'node:fs';
const fleet=JSON.parse(readFileSync(new URL('./fixtures/fleet-status.json',import.meta.url),'utf8')).result;
async function setup(page:any,options:any={}) {
 await page.addInitScript(({options,fleet}:any)=>{
  const scope='a'.repeat(64), prefix='batc.desktop.tailscale.';
  const saved=localStorage.getItem(prefix+scope);const prior=saved?JSON.parse(saved):null;
  const env:any={calls:[],status:{version:1,supported:true,scope,installation:'available',login:'needs_login',can_open:true,latest:null},receipt:null,...options};
  if(options.recover&&prior)env.receipt={request_id:prior.request_id,phase:options.recover};
  if(options.malformed)localStorage.setItem(prefix+scope,'{"request_id":true}');
  if(options.storageFailure){const set=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k.startsWith(prefix))throw Error('fixture storage unavailable');return set.call(this,k,v);};}
  if(options.state)Object.assign(env.status,options.state);
  Object.assign(fleet,{control_version:1,backend:'rust',login:{show_picker:true,revision:'f'.repeat(64)},pending_migration:null,profiles:[{id:'default',label:'Local BAT',connection:null},{id:'profile-1',label:'Fixture 1',connection:'node-1'}]});Object.assign(fleet.selection,{profiles:[],dashboard:true});
  Object.assign(window,{__tailscale:env,...(options.browser?{}:{isTauri:true,__TAURI_INTERNALS__:{invoke:async(command:string,args:any)=>{
   if(command==='native_status')return {endpoint:null,error:'Fixture central offline',credential_available:false};
   if(command==='connector_connect')throw Error('Fixture central offline');
   if(command==='fleet_availability')return {configured:true,platform_supported:true,native_controls:true};
   if(command==='fleet_control'&&args.input.action==='overview')return structuredClone(fleet);
   if(command!=='tailscale_control')throw Error('unexpected '+command);
   const input=structuredClone(args.input);env.calls.push(input);
   if(input.action==='status'){if(env.failRead)throw Error('read unavailable');if(env.holdStatus){env.heldStatus=(env.heldStatus||0)+1;if(env.heldStatus===1)await new Promise<void>(resolve=>{env.releaseStatus=resolve;});}return structuredClone(env.status);}
   if(input.action==='receipt')return {version:1,scope:env.status.scope,receipt:env.wrongReceipt?{request_id:'c'.repeat(32),phase:'started'}:env.receipt};
   if(input.action==='open'){
    if(env.hold)await new Promise<void>(resolve=>env.release=resolve);
    if(!env.receipt)env.receipt={request_id:input.request_id,phase:env.phase||'started'};
    env.status.latest=env.receipt;
    if(env.lose){env.lose=false;throw Error('lost reply');}
    return {version:1,scope:env.wrongScope?'c'.repeat(64):env.status.scope,receipt:env.wrongReceipt?{request_id:'c'.repeat(32),phase:'started'}:env.receipt};
   }
   throw Error('unexpected action');
  }}})});
 },{options,fleet});
 await page.goto('/dashboard/#/settings');if(!options.browser){await page.getByRole('link',{name:/^(Fleet|機群)$/}).click();await expect(page.getByRole('region',{name:'Tailscale',exact:true})).toBeVisible();}
}
const panel=(page:any)=>page.getByRole('region',{name:'Tailscale',exact:true});
const calls=(page:any,action:string)=>page.evaluate((a:string)=>(window as any).__tailscale.calls.filter((c:any)=>c.action===a),action);
const open=(page:any)=>panel(page).getByRole('button',{name:'Open Tailscale',exact:true}).click();
const refresh=(page:any)=>panel(page).getByRole('button',{name:'Refresh Tailscale status',exact:true}).click();
for(const [login,text] of [['needs_login','Tailscale needs sign-in.'],['needs_approval','This device still needs administrator approval.'],['stopped','Tailscale is disconnected.'],['starting','Tailscale is connecting.'],['running','Tailscale is running. See Fleet for each host’s connection status.'],['unknown','Tailscale status could not be confirmed.']])test(`safe login state ${login}`,async({page})=>{
 await setup(page,{state:{login}});await expect(panel(page).getByText(text,{exact:true})).toBeVisible();expect(await calls(page,'open')).toHaveLength(0);
});
test('explicit opening preserves ID, process receipt and focus return only rereads',async({page})=>{
 await setup(page);await open(page);await expect(panel(page).getByText('The Tailscale process started. Check the notification area; sign-in is not yet confirmed.')).toBeVisible();
 const original=(await calls(page,'open'))[0];expect(original).toEqual({action:'open',request_id:expect.stringMatching(/^[0-9a-f]{32}$/)});
 await page.evaluate(()=>{(window as any).__tailscale.status.login='running';window.dispatchEvent(new Event('focus'));});
 await expect(panel(page).getByText('Tailscale is running. See Fleet for each host’s connection status.')).toBeVisible();expect(await calls(page,'open')).toHaveLength(1);
});
test('lost reply reload reads original receipt without another opening',async({page})=>{
 await setup(page,{lose:true,recover:'started'});await open(page);await expect.poll(()=>calls(page,'open')).toHaveLength(1);const original=(await calls(page,'open'))[0];await page.reload();
 await expect(panel(page).getByRole('button',{name:'Prepare another opening'})).toBeVisible();expect(await calls(page,'open')).toHaveLength(0);expect(await calls(page,'receipt')).toContainEqual({action:'receipt',request_id:original.request_id});
});
test('unknown original receipt never offers replacement opening',async({page})=>{
 await setup(page,{phase:'uncertain'});await open(page);await refresh(page);
 await expect(panel(page).getByRole('button',{name:'Open Tailscale',exact:true})).toBeDisabled();await expect(panel(page).getByRole('button',{name:'Prepare another opening'})).toHaveCount(0);expect(await calls(page,'open')).toHaveLength(1);
});
test('missing receipt retries only original key after explicit click',async({page})=>{
 await setup(page,{lose:true});await open(page);await expect.poll(()=>calls(page,'open')).toHaveLength(1);const original=(await calls(page,'open'))[0];
 await page.evaluate(()=>{const e=(window as any).__tailscale;e.receipt=null;e.status.latest=null;});await refresh(page);
 await panel(page).getByRole('button',{name:'Check or retry original request'}).click();await expect.poll(()=>calls(page,'open')).toHaveLength(2);expect(await calls(page,'open')).toEqual([original,original]);
});
test('definite failure requires explicit new choice and never automatically opens',async({page})=>{
 await setup(page,{phase:'not_started'});await open(page);await panel(page).getByRole('button',{name:'Prepare another opening'}).click();expect(await calls(page,'open')).toHaveLength(1);await open(page);await expect.poll(()=>calls(page,'open')).toHaveLength(2);const requests=await calls(page,'open');expect(requests[0].request_id).not.toEqual(requests[1].request_id);
});
for(const option of ['wrongReceipt','wrongScope','storageFailure'])test(`refuse ${option} without accepting another result`,async({page})=>{
 await setup(page,{[option]:true});await open(page);await expect(panel(page).getByText('The latest status or opening result could not be confirmed. Refresh to check; the original request is kept.')).toBeVisible();
 await expect(panel(page).getByRole('button',{name:'Prepare another opening'})).toHaveCount(0);if(option==='storageFailure'){expect(await calls(page,'open')).toHaveLength(0);await refresh(page);await panel(page).getByRole('button',{name:'Check or retry original request'}).click();expect(await calls(page,'open')).toHaveLength(0);}
});
test('malformed draft cannot authorize another launch',async({page})=>{
 await setup(page,{malformed:true});await expect(panel(page).getByRole('button',{name:'Open Tailscale',exact:true})).toBeDisabled();expect(await calls(page,'open')).toHaveLength(0);
});
for(const state of [{supported:false,scope:null,can_open:false},{installation:'missing',can_open:false},{installation:'incomplete',can_open:false}])test(`unsupported or missing ${JSON.stringify(state)}`,async({page})=>{
 await setup(page,{state});await expect(panel(page).getByRole('button',{name:'Open Tailscale',exact:true})).toBeDisabled();expect(await calls(page,'open')).toHaveLength(0);
});
test('local browser setup has no unrelated Tailscale note or IPC',async({page})=>{
 await setup(page,{browser:true});await expect(panel(page)).toHaveCount(0);expect(await calls(page,'status')).toHaveLength(0);expect(await calls(page,'open')).toHaveLength(0);
});
test('failed read blocks fresh opening and returning to page rereads',async({page})=>{
 await setup(page);await page.evaluate(()=>{(window as any).__tailscale.failRead=true});await refresh(page);await expect(panel(page).getByRole('button',{name:'Open Tailscale',exact:true})).toBeDisabled();await page.evaluate(()=>{(window as any).__tailscale.failRead=false;window.dispatchEvent(new Event('focus'));});await expect(panel(page).getByRole('button',{name:'Open Tailscale',exact:true})).toBeEnabled();
});
test('focus during opening waits and never creates a second action',async({page})=>{
 await setup(page,{hold:true});await open(page);await page.evaluate(()=>window.dispatchEvent(new Event('focus')));expect(await calls(page,'open')).toHaveLength(1);await page.evaluate(()=>(window as any).__tailscale.release());await expect(panel(page).getByRole('button',{name:'Prepare another opening'})).toBeVisible();expect(await calls(page,'open')).toHaveLength(1);
});
for(const locale of ['en-US','zh-TW'])for(const width of [390,768,1440])test(`layout ${locale} ${width}`,async({browser})=>{
 const context=await browser.newContext({locale,viewport:{width,height:900}}),page=await context.newPage();const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await setup(page);await expect(panel(page).getByText(locale==='zh-TW'?'Tailscale 需要登入。':'Tailscale needs sign-in.',{exact:true})).toBeVisible();await panel(page).scrollIntoViewIfNeeded();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);mkdirSync('/tmp/bac-tailscale-after',{recursive:true});await page.screenshot({path:`/tmp/bac-tailscale-after/${locale}-${width}.png`,fullPage:true});await context.close();
});

test('a different local login scope never reuses the original opening ID',async({page})=>{
 await setup(page);await open(page);const original=(await calls(page,'open'))[0];
 await page.evaluate(()=>{const e=(window as any).__tailscale;e.status.scope='d'.repeat(64);e.status.latest=null;e.receipt=null;});await refresh(page);await open(page);
 const requests=await calls(page,'open');expect(requests[1].request_id).not.toEqual(original.request_id);
});
test('busy focus: Refresh keeps the keyboard while status is held',async({page})=>{
 await setup(page);
 const refreshButton=panel(page).getByRole('button',{name:'Refresh Tailscale status',exact:true});
 const message=panel(page).getByRole('status');
 await expect(refreshButton).toBeEnabled();
 const before=(await calls(page,'status')).length;
 await page.evaluate(()=>{(window as any).__tailscale.holdStatus=true;});
 await refreshButton.focus();
 await page.keyboard.press('Enter');
 await page.waitForFunction(()=>typeof (window as any).__tailscale.releaseStatus==='function');
 await expect(refreshButton).toBeFocused();
 await expect(refreshButton).toHaveAttribute('aria-disabled','true');
 await expect(message).toHaveAttribute('aria-busy','true');
 const held=(await calls(page,'status')).length;
 expect(held).toBe(before+1);
 await page.keyboard.press('Enter');
 await page.evaluate(()=>{const env=(window as any).__tailscale;env.holdStatus=false;env.releaseStatus();});
 await expect(refreshButton).toBeEnabled();
 await expect(refreshButton).toBeFocused();
 await expect(message).not.toHaveAttribute('aria-busy');
 expect(await calls(page,'status')).toHaveLength(held);
});
test('busy focus: Open moves to the next opening action',async({page})=>{
 await setup(page);
 const openButton=panel(page).getByRole('button',{name:'Open Tailscale',exact:true});
 await expect(openButton).toBeEnabled();
 await openButton.focus();
 await page.keyboard.press('Enter');
 const next=panel(page).getByRole('button',{name:'Prepare another opening',exact:true});
 await expect(next).toBeFocused();
 expect(await calls(page,'open')).toHaveLength(1);
});
test('busy focus: Open stays disabled when Tailscale cannot be opened',async({page})=>{
 await setup(page,{state:{can_open:false}});
 const openButton=panel(page).getByRole('button',{name:'Open Tailscale',exact:true});
 await expect(openButton).toBeDisabled();
 await page.evaluate(()=>{(window as any).__tailscale.holdStatus=true;});
 await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
 await page.waitForFunction(()=>typeof (window as any).__tailscale.releaseStatus==='function');
 await expect(openButton).toBeDisabled();
 await expect(openButton).not.toHaveAttribute('aria-disabled');
});
test('busy focus: window focus keeps Refresh',async({page})=>{
 await setup(page);
 const refreshButton=panel(page).getByRole('button',{name:'Refresh Tailscale status',exact:true});
 await expect(refreshButton).toBeEnabled();
 await refreshButton.focus();
 await page.evaluate(()=>{(window as any).__tailscale.holdStatus=true;});
 await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
 await page.waitForFunction(()=>typeof (window as any).__tailscale.releaseStatus==='function');
 await expect(refreshButton).toBeFocused();
 await expect(refreshButton).toHaveAttribute('aria-disabled','true');
 await page.evaluate(()=>{const env=(window as any).__tailscale;env.holdStatus=false;env.releaseStatus();});
 await expect(refreshButton).toBeEnabled();
 await expect(refreshButton).toBeFocused();
});
test('native uncertain latest receipt restores after frontend storage loss',async({page})=>{
 await setup(page,{state:{latest:{request_id:'b'.repeat(32),phase:'uncertain'}}});
 await expect(panel(page).getByRole('button',{name:'Open Tailscale',exact:true})).toBeDisabled();
 expect(await calls(page,'receipt')).toContainEqual({action:'receipt',request_id:'b'.repeat(32)});expect(await calls(page,'open')).toHaveLength(0);
});
