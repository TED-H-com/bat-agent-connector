import type {Page} from '@playwright/test';
import {attentionFixture} from './attention-fixture.ts';

export async function managedSetupFixture(page: Page, native: boolean, locale = 'en') {
  const base = attentionFixture();
  const state = {posts: [] as any[], staged: [] as any[], lost: false, refused: false,
    admissionRefused: true, refusalCode: 'CONFIGURATION_CHANGED', refusalMessage: 'Read the new configuration.', refusalStatus: 409, drop: false, setupStatus: 200, configuredHost: 'demo', configuredUrl: 'wss://demo.example:9876/', saved: false, reads: 0, revision: 'a'.repeat(64), scopes: ['observe', 'manage'], allowed: true, badActor: false, operation: null as any, hold: false, release: null as any, verification: {commands: {other: ['uv', 'run', 'pytest', '-q']}, timeout_s: 300}};
  const caps = () => ({...base.caps(), scopes: state.scopes, hosts: state.saved ? [{host:state.configuredHost}] : [],
    managed_installation: {runtime_version:'0.2.4', background:true},
    actions: [{action:'setup.host', allowed:state.allowed}, {action:'setup.repository', allowed:state.allowed}, {action:'setup.verification', allowed:state.allowed}]});
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    if (path === '/capabilities') return {status:200, data:caps()};
    if (path === '/bootstrap') return {status:200, data:{capabilities:caps(), sync:{version:1,
      server_id:'setup-central', principal_id:'setup-person', checkpoint:{cursor:0,token:'setup-0'}}}};
    if (path === '/managed/setup') {
      state.reads++;
      const result = state.setupStatus !== 200 ? {status:state.setupStatus,data:{error:{code:state.setupStatus===403?'FORBIDDEN':'NOT_FOUND',message:'Setup source withdrawn'}}}
        : {status:200,data:{revision:state.revision, verification:structuredClone(state.verification),
          hosts:state.saved ? [{name:state.configuredHost,url:state.configuredUrl,connected:true}] : [],
          repositories:[], profiles:[{id:'profile-demo',name:locale==='zh-TW'?'示範連線設定檔':'Demo profile',url:'wss://demo.example:9876/',
            fingerprint:'a'.repeat(64),profile_id:'default',token_available:true}],busy:false}};
      if (state.hold) await new Promise(resolve => {state.release = resolve;});
      return result;
    }
    if (path === '/managed/setup/secrets') {state.staged.push(input); return {status:200,data:{secret_ref:'setup_'+(state.staged.length===1?'b':'d').repeat(32),kind:input.body.kind}};}
    if (path.startsWith('/operations/') && state.operation) return {status:200,data:{operation:state.operation}};
    if (path === '/operations' && input.method === 'POST') {
      state.posts.push(input);
      if (state.refused) return {status:state.refusalStatus,data:{error:{code:state.refusalCode,message:state.refusalMessage, ...(state.admissionRefused ? {admission_refused:true} : {})}}};
      if (state.drop && state.posts.length === 1) return {status:0,data:{}};
      if (state.lost && state.posts.length === 1) return {status:503,data:{error:{code:'LOST_REPLY',message:'The reply was lost.'}}};
      state.saved = true;
      if (input.body.action === 'setup.host') {state.configuredHost = input.body.target.host;state.configuredUrl = input.body.params.url || 'wss://demo.example:9876/';}
      if (input.body.action === 'setup.verification') state.verification = input.body.params;
      state.operation = {...input.body,actor:state.badActor ? 'other' : caps().actor,operation_id:'op_'+'c'.repeat(32),
        idempotency_key:input.idempotency_key,status:'succeeded'};
      return {status:200,data:{operation:state.operation}};
    }
    return base.dispatch(input);
  };
  await page.addInitScript(locale => localStorage.setItem('batc.lang',locale), locale);
  if (native) {
    await page.exposeFunction('setupConnector', dispatch);
    await page.addInitScript(c => Object.assign(window,{isTauri:true,__TAURI_INTERNALS__:{
      invoke:async (command:string,args:any) => {
        if(command==='native_status') return {endpoint:'http://127.0.0.1:9870',credential_available:true};
        if(command==='connector_connect') return c;
        if(command==='connector_request') return (window as any).setupConnector(args.input);
        if(command==='managed_control') return {mode:'managed',ready:true,background:true,login_enabled:false};
        if(command==='connector_disconnect') return null;
        throw new Error('Fixture native feature unavailable');
      }}}),caps());
  } else await page.addInitScript(() => sessionStorage.setItem('batc.dashboard.token','fixture-token'));
  await page.route('**/api/v1/**',async route=>{
    if(native) throw Error('Native must use IPC');
    const req=route.request(),url=new URL(req.url());
    const result=await dispatch({method:req.method(),path:url.pathname.slice(7)+url.search,
      body:req.postData()?req.postDataJSON():null,idempotency_key:req.headers()['idempotency-key']});
    if (result.status === 0) await route.abort('connectionreset');
    else await route.fulfill({status:result.status,json:result.data});
  });
  return state;
}
