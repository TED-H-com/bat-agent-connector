import {test, expect, type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import {parse} from 'acorn';
import {unavailableBriefTransport} from './pm-source-transport-fixture.ts';
import {STRINGS} from '../src/i18n.js';

test.describe.configure({mode:'parallel'});

// Complete production session handlers and renderers, with controlled API outcomes.
async function mount(page: Page, locale: string, family: string, sourceRead: boolean) {
  const app=await readFile(new URL('../src/app.js',import.meta.url),'utf8');
  const ast:any=parse(app,{ecmaVersion:'latest',sourceType:'module'});
  const session=ast.body.find((n:any)=>n.type==='FunctionDeclaration'&&n.id.name==='viewSession');
  let send:any,refresh:any;
  function walk(n:any){if(!n||typeof n!=='object')return;
    if(n.type==='VariableDeclarator'&&n.id.name==='send'&&app.slice(n.start,n.end).includes('submit("session.send"'))
      send=n.init.arguments[1].properties.find((p:any)=>p.key.name==='onclick').value;
    if(n.type==='VariableDeclarator'&&n.id.name==='refresh')refresh=n.init;
    for(const v of Object.values(n)){if(Array.isArray(v))v.forEach(walk);else if(v&&typeof v==='object')walk(v);}
  }walk(session);expect(send).toBeTruthy();expect(refresh).toBeTruthy();
  const renderer=app.slice(app.indexOf('function errorBox('),app.indexOf('async function submit('));
  const sessionRenderer=app.slice(app.indexOf('function sessionErrorDetails('),app.indexOf('// Pull-request links'));
  await page.route('**/__operation_src/**',async route=>{
    const file=new URL(route.request().url()).pathname.slice('/__operation_src/'.length);
    await route.fulfill({contentType:'text/javascript',body:await readFile(new URL('../src/'+file,import.meta.url),'utf8')});
  });
  await unavailableBriefTransport(page,'__operation_src');
  await page.route('**/__operation-handlers.js',route=>route.fulfill({contentType:'text/javascript',body:`
    import {h} from '/__operation_src/ui/dom.js';
    import {t,setLocale} from '/__operation_src/i18n.js';
    import {button,disclosure} from '/__operation_src/ui/components.js';
    import {icon} from '/__operation_src/ui/icons.js';
    import {errorPresentation,sessionErrorLine} from '/__operation_src/error-presentation.js';
    import {settleRefreshes} from '/__operation_src/state/events.ts';
    export const state={refreshCycle:{error:null}};
    function openDiagnostics() {}
    ${renderer}
    ${sessionRenderer}
    export {errorBox};
    export function sessionHandlers(locale,api) {
      setLocale(locale);
      const status=h('div',{role:'status'}),box=h('textarea'),send=h('button',{type:'button'},t('send'));
      box.value='Keep this original draft';
      const context={status,box,send,queue:{checked:false,onchange(){}},host:'demo',sid:'session-1',scope:'send.demo.session-1',draftKey:'operation-readiness',
        connection:{},assertView(){},allowed:()=>true,paintSend(){},sendRecovery:()=>null,mobileLayout:null,
        conversation:{box:h('div'),follow(){},fail({error}){status.replaceChildren(error);}},
        permissions:null,perspectives:null,reinstate(){},withdraw(){},updateControls(){},scheduleRetry(){},
        loadObservation:()=>api('GET','/sessions/demo/session-1'),loadMessages:()=>Promise.resolve(),
        submit:(action,target,params,preconditions,key)=>api('POST','/operations?wait=3',{action,target,params,preconditions},key),
        workSendOwned:false,sending:false,disposed:false,readReady:true,refreshInFlight:null,readError:null,
        retryTimer:null,retryAt:0,retryDelay:1000,withdrawn:'',messageFailure:null,settleRefreshes,sessionErrorBox,t};
      let {queue,host,sid,scope,draftKey,connection,assertView,allowed,paintSend,sendRecovery,mobileLayout,conversation,permissions,perspectives,reinstate,withdraw,updateControls,scheduleRetry,loadObservation,loadMessages,submit,workSendOwned,sending,disposed,readReady,refreshInFlight,readError,retryTimer,retryAt,retryDelay,withdrawn,messageFailure}=context;
      const refresh=${app.slice(refresh.start,refresh.end)};
      send.onclick=${app.slice(send.start,send.end)};
      return {box:h('section',{},box,send,status),refresh};
    }` }));
  await page.goto('/ui-components.html');
  await page.evaluate(async({locale,family,sourceRead})=>{
    const {h}=await import('/__operation_src/ui/dom.js'),{t,setLocale}=await import('/__operation_src/i18n.js');setLocale(locale);
    const {ApiError}=await import('/__operation_src/error-presentation.js');
    const {state,errorBox,sessionHandlers}=await import('/__operation-handlers.js');
    const w=window as any;w.operationCycle=state.refreshCycle;w.operationCalls=[];
    const api=async(method:string,path:string,body:any)=>{w.operationCalls.push({method,path,body});throw new ApiError(sourceRead?503:422,sourceRead?'UNAVAILABLE':'INVALID_PARAMS','Fixture request refused');};
    const common={h,t,api,errorBox,guard:()=>{},alive:()=>{},caps:()=>({actor:'reader',scopes:['observe','operate'],hosts:[{host:'demo',writes:true}],actions:[{action:'session.approve_pending',allowed:true}]}),ready:()=>true,storageKey:'operation-readiness',opStatus:()=>h('span')};
    let ui:any;
    if(family==='session')ui=sessionHandlers(locale,api);
    else if(family==='pm') {
      if(sourceRead){
        const {pmContextBundle}=await import('/__operation_src/pm-context-bundle.js');
        ui=pmContextBundle({...common,caps:()=>({actor:'reader',features:{pm_workspaces:{accepted_context_bundle:{schema:'pm_context_bundle_v1'}}}}),target:{workspace_id:'ws_'+'1'.repeat(32),thread_id:'pmt_'+'2'.repeat(32)},thread:()=>({thread:{version:1}})});ui.update();ui.box.open=true;
      }else{
        const {pmWorktreeChoice}=await import('/__operation_src/pm-worktree-choice.js');
        ui=pmWorktreeChoice({...common,input:(key:string,opts:any)=>h('input',{value:opts.value}),labelled:(key:string,node:any)=>h('label',{},t(key),node),scopes:()=>['start'],editable:()=>true,
          capability:()=>({worktrees:[{available:true,repository:'example/project',host:'demo',workspace_id:'workspace-a'}]})});
        ui.attach({locked:()=>false,set(){}});ui.box.open=true;ui.fields.repository_mode.selectedIndex=1;ui.fields.repository_mode.dispatchEvent(new Event('change'));
      }
    }else{
      if(sourceRead)localStorage.setItem(common.storageKey,JSON.stringify({host:'demo',intent:{key:'approval-original',operation_id:'op_'+'8'.repeat(32),request:{action:'session.approve_pending',target:{host:'demo'},params:{preview_token:'bap1.fixture.signature',selection:[{item_id:'bapi_'+'1'.repeat(24),mode:null}]},preconditions:{expected_fingerprint:'a'.repeat(64)}}}})); // gitleaks:allow (unsigned fixture)
      const {approvalsPanel}=await import('/__operation_src/approvals.js');ui=approvalsPanel(common);
      if(!sourceRead){const select=ui.box.querySelector('select');select.value='demo';select.dispatchEvent(new Event('change'));}
    }
    document.getElementById('fixture')!.replaceChildren(ui.box);w.operationUi=ui;
  },{locale,family,sourceRead});
}
for(const locale of ['en','zh-TW'])for(const family of ['session','pm','approval']){
  test(`${family} operation outcome leaves an overlapping source refresh healthy ${locale}`,async({page})=>{
    await mount(page,locale,family,false);
    const key=family==='session'?'send':family==='pm'?'pm_preview_worktree':'bulk_preview';
    await page.getByRole('button',{name:STRINGS[locale][key],exact:true}).click();
    await expect.poll(()=>page.evaluate(()=>(window as any).operationCalls.length)).toBe(1);
    await expect(page.locator(family==='session'?'.session-error-line':'[data-error-summary]')).toHaveText(STRINGS[locale][family==='session'?'session_err_unknown':'error_code_INVALID_PARAMS']);
    expect(await page.evaluate(()=>(window as any).operationCycle.error)).toBeNull();
    expect(await page.evaluate(()=>(window as any).operationCalls[0].method)).toBe('POST');
    if(family==='session')await expect(page.locator('#fixture textarea')).toHaveValue('Keep this original draft');
  });
  test(`${family} source read keeps the strict refresh default ${locale}`,async({page})=>{
    await mount(page,locale,family,true);
    if(family==='pm')await page.getByRole('button',{name:STRINGS[locale].pm_cb_preview,exact:true}).click();
    else await page.evaluate(()=>(window as any).operationUi.refresh().catch(()=>{}));
    await expect.poll(()=>page.evaluate(()=>(window as any).operationCycle.error?.code)).toBe('UNAVAILABLE');
    expect(await page.evaluate(()=>(window as any).operationCalls[0].method)).toBe('GET');
  });
}

// These adapters also present errors before durableForm's catch sees them. Its
// existing collectReceipt callback identifies an original-ID read; ordinary GETs
// through the same adapter must keep the strict source default.
for(const locale of ['en','zh-TW'])for(const module of ['pm-ahp-observer','pm-quality-reviews','pm-maintenance-repairs'])
for(const kind of ['write','receipt','source'])test(`${module} preserves ${kind} error context ${locale}`,async({page})=>{
  const source=await readFile(new URL('../src/'+module+'.js',import.meta.url),'utf8');
  const all:any[]=[];
  const walk=(n:any)=>{if(!n||typeof n!=='object')return;if(n.type==='VariableDeclarator'&&n.id.name==='request'&&n.init?.params[0]?.type==='RestElement')all.push(n);
    for(const v of Object.values(n)){if(Array.isArray(v))v.forEach(walk);else if(v&&typeof v==='object')walk(v);}};
  walk(parse(source,{ecmaVersion:'latest',sourceType:'module'}));expect(all).toHaveLength(1);
  const adapter=all[0].init;
  await mount(page,locale,'session',false);
  await page.route('**/__operation-adapter.js',route=>route.fulfill({contentType:'text/javascript',body:`
    import {errorBox} from '/__operation-handlers.js';
    export function requestAdapter(api) {
      let epoch=0,serial=0,firstStart=false;
      const valid=()=>true,START='pm.maintenance.repair.start';
      const failure=(error,options)=>document.getElementById('fixture').replaceChildren(errorBox(error,options));
      return ${source.slice(adapter.start,adapter.end)};
    }`}));
  await page.evaluate(async kind=>{
    const {ApiError}=await import('/__operation_src/error-presentation.js');
    const {requestAdapter}=await import('/__operation-adapter.js');
    const request=requestAdapter(async()=>{throw new ApiError(403,'FORBIDDEN','Fixture refused this principal');});
    await request(kind==='write'?'POST':'GET',kind==='source'?'/source':'/operations/op_'+'8'.repeat(32),undefined,undefined,kind==='receipt'?()=>{}:undefined).catch(()=>{});
  },kind);
  await expect(page.locator('[data-error-summary]')).toHaveText(STRINGS[locale].error_code_FORBIDDEN);
  const code=await page.evaluate(()=>(window as any).operationCycle.error?.code??null);
  expect(code).toBe(kind==='source'?'FORBIDDEN':null);
});
