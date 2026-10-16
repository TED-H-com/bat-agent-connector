import {test, expect, type Page} from '@playwright/test';
import {mkdirSync} from 'node:fs';
const sid = 'session-full-0123456789abcdef-0123456789abcdef';
const title = 'Review complete project delivery and preserve original source instructions';
const caps = {actor:'observer', scopes:['observe'],api_version:1,contract_version:'2026-10-08',hosts:[],features:{},actions:[]};
async function setup(page:Page, native=true, options:any={}) {
 await page.addInitScript(({native, options, caps, sid, title})=>{
  const env:any = {calls:[], copies:[], receipt:null, profile:'', ...options};
  (window as any).__sessionBat=env;
  Object.defineProperty(navigator, 'clipboard', {value:{writeText:async(text:string)=>env.copies.push(text)}});
  const read=(path:string)=> {
   const p=new URL(path,'http://fixture').pathname;
   if(p==='/events'){
    const after=Number(new URL(path,'http://fixture').searchParams.get('after')||0);
    const event=env.event;
    if(event&&event.seq>after)return {events:[event],head_cursor:event.seq,next_cursor:event.seq,has_more:false,sync:{checkpoint:{cursor:event.seq,token:'proof'}}};
    return {events:[],head_cursor:after,next_cursor:after,has_more:false,sync:{checkpoint:{cursor:after,token:'proof'}}};
   }
   return p==='/capabilities'?caps:p==='/bootstrap'?{capabilities:caps,sync:{version:1,server_id:'fixture-server',principal_id:sessionStorage.getItem('fixture-principal')||env.principal||'fixture-principal',checkpoint:{cursor:0,token:'proof'}}}
    :p===`/sessions/demo/${sid}`?{session:{host:'demo',session_id:sid,title:env.sessionTitle||title,provenance:'manual',api_access:'read_only',workspace:'/not/a/profile/mapping',agent_kind:'claude'}}
    :{messages:[],checkpoints:[],operations:[],hosts:[],sessions:[],work_items:[],links:[],history:[]};
  };
  env.read=read;
  sessionStorage.setItem('batc.dashboard.token','fixture-token');
  if(native) Object.assign(window,{isTauri:true,__TAURI_INTERNALS__:{invoke:async(command:string,args:any)=>{
   if(command==='native_status')return {endpoint:'https://central.example/',credential_available:true};
   if(command==='connector_connect')return caps;
   if(command==='connector_disconnect')return null;
   if(command==='connector_request'){if(args.input.method!=='GET')throw new Error('Unexpected central mutation');return{status:200,data:read(args.input.path)};}
   if(command==='fleet_availability')return {configured:!env.missing,platform_supported:true,native_controls:true};
   if(command!=='fleet_control')throw new Error('Unexpected '+command);
   const input=structuredClone(args.input);env.calls.push(input);
   if(input.action==='overview'){if(env.holdOverview){env.heldOverview=(env.heldOverview||0)+1;if(env.heldOverview===1)await new Promise(resolve=>{env.releaseOverview=resolve;});}return {control_version:1,configuration:{binding:'a'.repeat(64),valid:true},profiles:[{id:'profile-1',label:'Explicit remote choice',connection:'node-1'},{id:'default',label:'Local BAT',connection:null}],selection:{connections:['node-1'],profiles:[],dashboard:false},pending_migration:null,monitor:{state:'running',controllable:true}};}
   if(input.action==='preview_profile'){
    if(env.previewError)throw new Error(env.previewError);
    env.profile=input.profile_id;env.preview_id=sessionStorage.getItem('fixture-preview-id')||'1'.repeat(32);
    return {preview_id:env.preview_id,configuration_binding:'a'.repeat(64),summary:{launch_id:env.preview_id,profiles:[env.wrongPreview?'wrong':input.profile_id],dashboard:false,opens_bat:true,already_running:!!env.running,bat_may_open_local_window:input.profile_id!=='default'}};
   }
   if(input.action==='launch'){
    if(env.expired && input.preview_id!==env.preview_id)throw new Error('PREVIEW_EXPIRED');
    if(env.hold)await new Promise(resolve=>env.release=resolve);
    env.receipt={launch_id:input.preview_id,profiles:[env.profile],dashboard:false,state:env.uncertain?'uncertain':'started'};
    localStorage.setItem('fixture-bat-receipt',JSON.stringify(env.receipt));
    if(env.lose)throw new Error('Lost reply');
    return env.wrongReceipt?{...env.receipt,profiles:['wrong']}:env.receipt;
   }
   if(input.action==='launch_status'){
    const receipt=env.receipt||JSON.parse(localStorage.getItem('fixture-bat-receipt')||'null');
    return receipt&&env.wrongReceipt?{...receipt,profiles:['wrong']}:receipt;
   }
   if(input.action==='discard')return {discarded:true};
   throw new Error('Unexpected action '+input.action);
  }}});
 },{native,options,caps,sid,title});
 await page.route('**/api/v1/**',async route=>{
  if(native)throw new Error('Native cannot use browser HTTP');
  expect(route.request().method()).toBe('GET');
  const data=await page.evaluate(path=>(window as any).__sessionBat.read(path),new URL(route.request().url()).pathname.slice(7));
  await route.fulfill({json:data});
 });
 await page.goto(`/dashboard/#/session/demo/${sid}`);
 await expect(page.getByRole('heading',{name:title})).toBeVisible();
}
const calls=(page:Page, action:string)=>page.evaluate(a=>(window as any).__sessionBat.calls.filter((c:any)=>c.action===a),action);

async function openMore(page:Page) {
 const trigger = page.getByRole('button',{name:'More actions',exact:true});
 if ((await trigger.getAttribute('aria-expanded')) !== 'true') await trigger.click();
}
async function review(page:Page, selected='profile-1') {
 await openMore(page);
 await page.getByRole('menuitem',{name:'Open in BAT',exact:true}).click();
 const picker=page.getByRole('combobox',{name:'BAT profile',exact:true});
 await expect(picker).toHaveValue(''); await picker.selectOption(selected);
 await page.getByRole('button',{name:'Review selected profile',exact:true}).click();
}
for (const native of [false,true]) test(`complete copy and no inferred mapping (${native?'native':'browser'})`,async({page})=>{
 await setup(page,native);
 await openMore(page); await page.getByRole('menuitem',{name:'Copy title',exact:true}).click();
 await openMore(page); await page.getByRole('menuitem',{name:'Copy ID',exact:true}).click();
 expect(await page.evaluate(()=>(window as any).__sessionBat.copies)).toEqual([title,sid]);
 expect(await calls(page,'preview_profile')).toHaveLength(0);expect(await calls(page,'launch')).toHaveLength(0);
 await openMore(page);
 if(native){await page.getByRole('menuitem',{name:'Open in BAT',exact:true}).click();await expect(page.getByRole('combobox',{name:'BAT profile'})).toHaveValue('');await expect(page.getByRole('button',{name:'Review selected profile',exact:true})).toBeDisabled();}
 else await expect(page.getByRole('menuitem',{name:'Open in BAT',exact:true})).toBeDisabled();
});
test('explicit profile review launches only the fixed native request and preserves manual read-only',async({page})=>{
 await setup(page);await review(page);await expect.poll(()=>calls(page,'preview_profile')).toEqual([{action:'preview_profile',profile_id:'profile-1'}]);
 expect(await calls(page,'launch')).toHaveLength(0);await expect(page.getByRole('combobox',{name:'BAT profile'})).toBeDisabled();
 await page.getByRole('button',{name:'Open selected profile',exact:true}).click();
 await expect.poll(()=>calls(page,'launch')).toEqual([{action:'launch',preview_id:'1'.repeat(32)}]);
 await expect(page.getByRole('button',{name:'Choose another profile',exact:true})).toBeVisible();
 expect(await calls(page,'apply_choices')).toHaveLength(0);expect(await calls(page,'save_login')).toHaveLength(0);
 await expect(page.getByRole('button',{name:'Send',exact:true})).toBeHidden();
});
test('lost reply reload reads original receipt without preview or launch',async({page})=>{
 await setup(page,true,{lose:true});await review(page);await page.getByRole('button',{name:'Open selected profile',exact:true}).click();
 await expect(page.getByText(/Lost reply/)).toBeVisible();expect(await calls(page,'launch')).toHaveLength(1);
 await page.reload();await expect(page.getByRole('button',{name:'Choose another profile',exact:true})).toBeVisible();
 expect(await calls(page,'launch')).toHaveLength(0);expect(await calls(page,'preview_profile')).toHaveLength(0);
 expect(await calls(page,'launch_status')).toEqual([{action:'launch_status',launch_id:'1'.repeat(32)}]);
});
test('wrong preview refuses launch; wrong and uncertain receipts retain frozen request',async({page})=>{
 await setup(page,true,{wrongPreview:true});await review(page);await expect(page.getByText(/response does not match/)).toBeVisible();
 expect(await calls(page,'launch')).toHaveLength(0);
 await page.evaluate(()=>{(window as any).__sessionBat.wrongPreview=false});
 await page.getByRole('button',{name:'Review selected profile',exact:true}).click();
 await page.evaluate(()=>{(window as any).__sessionBat.wrongReceipt=true});await page.getByRole('button',{name:'Open selected profile',exact:true}).click();
 await expect(page.getByRole('button',{name:'Choose another profile',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Read original launch',exact:true}).click();
 expect(await calls(page,'launch')).toHaveLength(1);
 await page.evaluate(()=>{const e=(window as any).__sessionBat;e.wrongReceipt=false;e.receipt.state='uncertain'});
 await page.getByRole('button',{name:'Read original launch',exact:true}).click();
 await expect(page.getByRole('button',{name:'Check and retry original launch',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Choose another profile',exact:true})).toHaveCount(0);
});
test('storage failure before effect prevents launch; retry retains same preview',async({page})=>{
 await setup(page);await review(page);
 await page.evaluate(()=>{const set=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k.startsWith('batc.session-bat.'))throw new Error('Fixture full storage');return set.call(this,k,v)};(window as any).__restoreStorage=()=>Storage.prototype.setItem=set});
 await page.getByRole('button',{name:'Open selected profile',exact:true}).click();await expect(page.getByText(/Fixture full storage/)).toBeVisible();expect(await calls(page,'launch')).toHaveLength(0);
 await page.evaluate(()=>(window as any).__restoreStorage());await page.getByRole('button',{name:'Open selected profile',exact:true}).click();
 await expect.poll(()=>calls(page,'launch')).toEqual([{action:'launch',preview_id:'1'.repeat(32)}]);
});
test('already running BAT and missing installation never launch',async({page})=>{
 await setup(page,true,{running:true});await review(page);
 await expect(page.getByRole('button',{name:'Open selected profile',exact:true})).toHaveCount(0);
 expect(await calls(page,'launch')).toHaveLength(0);
 await page.getByRole('button',{name:'Cancel',exact:true}).click();
 await page.evaluate(()=>{(window as any).__sessionBat.missing=true});
 await page.getByRole('button',{name:'Read original launch',exact:true}).click();
 await expect(page.getByRole('button',{name:'Review selected profile',exact:true})).toBeDisabled();
 await expect(page.getByText(/Local BAT launch support/)).toBeVisible();
});
test('profile refusal is readable and never guesses another profile',async({page})=>{
 await setup(page,true,{previewError:'PROFILE_CONNECTION_NOT_SELECTED'});await review(page);
 await expect(page.getByText(/PROFILE_CONNECTION_NOT_SELECTED/)).toBeVisible();expect(await calls(page,'launch')).toHaveLength(0);
 expect(await calls(page,'preview_profile')).toEqual([{action:'preview_profile',profile_id:'profile-1'}]);
});
test('navigation while launch reply held preserves original for later readback',async({page})=>{
 await setup(page,true,{hold:true});await review(page);await page.getByRole('button',{name:'Open selected profile',exact:true}).click();
 await expect.poll(()=>calls(page,'launch')).toHaveLength(1);
 await page.evaluate(()=>location.hash='#/sessions');await expect(page.locator('.session-bat')).toHaveCount(0);
 await page.evaluate(()=>(window as any).__sessionBat.release());
 await page.evaluate(id=>location.hash=`#/session/demo/${id}`,sid);
 await expect(page.getByRole('button',{name:'Choose another profile',exact:true})).toBeVisible();
 expect(await calls(page,'launch')).toHaveLength(1);expect(await calls(page,'preview_profile')).toHaveLength(1);
});
for(const locale of ['en-US','zh-TW']) for(const width of [390,768,1440]) test(`session handoff ${locale} ${width}`,async({browser})=>{
 const context=await browser.newContext({locale,viewport:{width,height:900}}),page=await context.newPage();await setup(page);
 await expect(page.locator('[data-session-view]')).toBeVisible();
 const more=locale==='zh-TW'?'更多操作':'More actions';
 const openBat=locale==='zh-TW'?'在 BAT 中開啟':'Open in BAT';
 await page.getByRole('button',{name:more,exact:true}).click();
 await page.getByRole('menuitem',{name:openBat,exact:true}).click();
 const opener=page.getByRole('button',{name:/^(Results|成果)$/,exact:true});
 if(await opener.isVisible())await opener.click();
 const picker=page.locator('.session-bat select');await expect(picker).toBeEnabled();await picker.selectOption('profile-1');
 await page.locator('.session-bat').getByRole('button',{name:locale==='zh-TW'?'預覽所選 profile':'Review selected profile',exact:true}).click();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 for(const button of await page.locator('.session-bat button').all())expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(44);
 mkdirSync('/tmp/bac-session-bat-after',{recursive:true});await page.screenshot({path:`/tmp/bac-session-bat-after/${locale}-${width}.png`,fullPage:true});await context.close();
});

const bat=(page:Page)=>page.locator('.session-bat');
test('busy focus: Read keeps the keyboard while the overview is held',async({page})=>{
 await setup(page);await openMore(page);await page.getByRole('menuitem',{name:'Open in BAT',exact:true}).click();
 const read=bat(page).getByRole('button',{name:'Read original launch',exact:true});
 await expect(read).toBeEnabled();
 const before=(await calls(page,'overview')).length;
 await page.evaluate(()=>{(window as any).__sessionBat.holdOverview=true;});
 await read.focus();
 await page.keyboard.press('Enter');
 await page.waitForFunction(()=>typeof (window as any).__sessionBat.releaseOverview==='function');
 await expect(read).toBeFocused();
 await expect(read).toHaveAttribute('aria-disabled','true');
 await expect(bat(page).getByRole('status')).toHaveAttribute('aria-busy','true');
 const held=(await calls(page,'overview')).length;
 expect(held).toBe(before+1);
 await page.keyboard.press('Enter');
 await page.evaluate(()=>{const env=(window as any).__sessionBat;env.holdOverview=false;env.releaseOverview();});
 await expect(read).toBeEnabled();
 await expect(read).toBeFocused();
 expect(await calls(page,'overview')).toHaveLength(held);
});
test('busy focus: Review stays disabled without a chosen profile',async({page})=>{
 await setup(page);await openMore(page);await page.getByRole('menuitem',{name:'Open in BAT',exact:true}).click();
 const review=bat(page).getByRole('button',{name:'Review selected profile',exact:true});
 const read=bat(page).getByRole('button',{name:'Read original launch',exact:true});
 await expect(review).toBeDisabled();
 await page.evaluate(()=>{(window as any).__sessionBat.holdOverview=true;});
 await read.focus();
 await page.keyboard.press('Enter');
 await page.waitForFunction(()=>typeof (window as any).__sessionBat.releaseOverview==='function');
 await expect(review).toBeDisabled();
 await expect(review).not.toHaveAttribute('aria-disabled');
});
test('busy focus: Review moves to Launch',async({page})=>{
 await setup(page);await openMore(page);await page.getByRole('menuitem',{name:'Open in BAT',exact:true}).click();
 const picker=page.getByRole('combobox',{name:'BAT profile',exact:true});
 await expect(picker).toBeEnabled();
 await picker.selectOption('profile-1');
 const review=bat(page).getByRole('button',{name:'Review selected profile',exact:true});
 await review.focus();
 await page.keyboard.press('Enter');
 const launch=bat(page).getByRole('button',{name:'Open selected profile',exact:true});
 await expect(launch).toBeFocused();
 expect(await calls(page,'preview_profile')).toEqual([{action:'preview_profile',profile_id:'profile-1'}]);
 expect(await calls(page,'launch')).toHaveLength(0);
});
test('busy focus: session re-render keeps Read',async({page})=>{
 await setup(page);await openMore(page);await page.getByRole('menuitem',{name:'Open in BAT',exact:true}).click();
 const read=bat(page).getByRole('button',{name:'Read original launch',exact:true});
 await expect(read).toBeEnabled();
 await read.focus();
 await expect(read).toBeFocused();
 await page.evaluate(id=>{(window as any).__sessionBat.sessionTitle='Session reread after event';(window as any).__sessionBat.event={seq:1,kind:'session.updated',resource_type:'session',resource_id:`demo/${id}`};},sid);
 await expect(page.getByRole('heading',{name:'Session reread after event'})).toBeVisible();
 await expect(read).toBeFocused();
});
test('a different verified central principal cannot load the previous session launch',async({page})=>{
 await setup(page,true,{lose:true});await review(page);await page.getByRole('button',{name:'Open selected profile',exact:true}).click();
 await expect(page.getByText(/Lost reply/)).toBeVisible();
 await page.evaluate(()=>sessionStorage.setItem('fixture-principal','other-principal'));await page.reload();
 await openMore(page);
 await expect(page.getByRole('menuitem',{name:'Open in BAT',exact:true})).toBeVisible();
 expect(await calls(page,'launch_status')).toHaveLength(0);expect(await calls(page,'launch')).toHaveLength(0);
 await expect(page.getByRole('combobox',{name:'BAT profile'})).toHaveCount(0);
 await page.evaluate(()=>sessionStorage.removeItem('fixture-principal'));await page.reload();
 await expect(page.getByRole('button',{name:'Choose another profile',exact:true})).toBeVisible();
 expect(await calls(page,'launch_status')).toHaveLength(1);expect(await calls(page,'launch')).toHaveLength(0);
});
test('clipboard denial retains selectable complete text without a launch',async({page})=>{
 await setup(page,false);await page.evaluate(()=>navigator.clipboard.writeText=async()=>{throw new Error('denied')});
 await openMore(page); await page.getByRole('menuitem',{name:'Copy ID',exact:true}).click();
 await expect(page.getByRole('textbox',{name:'Copy ID',exact:true})).toHaveValue(sid);
 expect(await calls(page,'launch')).toHaveLength(0);
});

test('restart never submits an unattempted lost native handle; explicit re-review uses new handle',async({page})=>{
 await setup(page,true,{expired:true});await review(page);
 await page.evaluate(()=>sessionStorage.setItem('fixture-preview-id','2'.repeat(32)));
 await page.reload();
 await expect(page.getByRole('button',{name:'Review original choice again',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Open selected profile',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Cancel',exact:true})).toBeVisible();
 expect(await calls(page,'launch')).toHaveLength(0);expect(await calls(page,'preview_profile')).toHaveLength(0);
 await page.getByRole('button',{name:'Review original choice again',exact:true}).click();
 await expect.poll(()=>calls(page,'preview_profile')).toEqual([{action:'preview_profile',profile_id:'profile-1'}]);
 await page.getByRole('button',{name:'Open selected profile',exact:true}).click();
 await expect.poll(()=>calls(page,'launch')).toEqual([{action:'launch',preview_id:'2'.repeat(32)}]);
 await expect(page.getByRole('button',{name:'Choose another profile',exact:true})).toBeVisible();
});
test('attempted lost reply with missing native receipt cannot become a new preview after restart',async({page})=>{
 await setup(page,true,{lose:true,expired:true});await review(page);
 await page.getByRole('button',{name:'Open selected profile',exact:true}).click();await expect(page.getByText(/Lost reply/)).toBeVisible();
 await page.evaluate(()=>localStorage.removeItem('fixture-bat-receipt'));await page.reload();
 await expect(page.getByText(/No original launch receipt was found/)).toBeVisible();
 expect(await calls(page,'launch')).toHaveLength(0);expect(await calls(page,'preview_profile')).toHaveLength(0);
 await expect(page.getByRole('button',{name:'Review original choice again',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Cancel',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Check and retry original launch',exact:true}).click();
 await expect(page.getByText(/PREVIEW_EXPIRED/)).toBeVisible();
 await expect(page.getByRole('button',{name:'Choose another profile',exact:true})).toHaveCount(0);
 expect(await calls(page,'launch')).toEqual([{action:'launch',preview_id:'1'.repeat(32)}]);
 expect(await page.evaluate(()=>JSON.parse(Object.entries(localStorage).find(([k])=>k.startsWith('batc.session-bat.'))![1]).preview_id)).toBe('1'.repeat(32));
});
