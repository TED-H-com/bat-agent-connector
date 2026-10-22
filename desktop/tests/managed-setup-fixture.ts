import {managedStatusFixture} from './managed-status-fixture.ts';
import type {Page} from '@playwright/test';
import {attentionFixture} from './attention-fixture.ts';
import {readFileSync} from 'node:fs';

const previewGolden = JSON.parse(readFileSync(new URL('../../tests/fixtures/managed-setup-preview.json', import.meta.url), 'utf8'));

export async function managedSetupFixture(page: Page, native: boolean, locale: string | null = 'en',
  evidence?: {state: any, operation: any}) {
  const base = attentionFixture();
  const state = {managed: managedStatusFixture(), managedCalls: [] as string[], stopBusy: false, stopPending: false, startStillStopping: false, posts: [] as any[], previews: [] as any[], staged: [] as any[], lost: false, refused: false, previewRefused: false,
    admissionRefused: true, refusalCode: 'CONFIGURATION_CHANGED', refusalMessage: 'Read the new configuration.', refusalStatus: 409, drop: false, setupStatus: 200, configuredHost: 'demo', configuredUrl: 'wss://demo.example:9876/', saved: false, connectionStatus: null as string | null, connected: true, extraHosts: [] as any[], repositories: [] as any[], reads: 0, revision: 'a'.repeat(64), scopes: ['observe', 'manage'], allowed: true, badActor: false, operation: null as any, hold: false, release: null as any, hasProfiles: true, profiles: null as any[] | null, profilesError: null as string | null, verification: {commands: {other: ['uv', 'run', 'pytest', '-q']}, timeout_s: 300}};
  if (evidence) {state.saved = true; state.operation = evidence.operation; state.configuredHost = evidence.operation.target.host;}
  const caps = () => ({...base.caps(), ...(evidence ? {actor: evidence.operation.actor} : {}), scopes: state.scopes, hosts: state.saved ? [{host:state.configuredHost}] : [],
    managed_installation: {runtime_version:'0.2.4', background:true},
    actions: [{action:'setup.host', allowed:state.allowed}, {action:'setup.repository', allowed:state.allowed}, {action:'setup.verification', allowed:state.allowed}]});
  const dispatch = async (input: any) => {
    const url = new URL(input.path, 'http://fixture'), path = url.pathname;
    if (path === '/capabilities') return {status:200, data:caps()};
    if (path === '/bootstrap') return {status:200, data:{capabilities:caps(), sync:{version:1,
      server_id:'setup-central', principal_id:'setup-person', checkpoint:{cursor:0,token:'setup-0'}}}};
    if (path === '/managed/setup') {
      state.reads++;
      if (evidence) return {status: 200, data: structuredClone(evidence.state)};
      const result = state.setupStatus !== 200 ? {status:state.setupStatus,data:{error:{code:state.setupStatus===403?'FORBIDDEN':'NOT_FOUND',message:'Setup source withdrawn'}}}
        : {status:200,data:{revision:state.revision, preview_contract_version:1, verification:structuredClone(state.verification),
          hosts:[...(state.saved ? [{name:state.configuredHost,url:state.configuredUrl,connected:state.connected, ...(state.connectionStatus ? {connection_status: state.connectionStatus} : {})}] : []), ...state.extraHosts],
          repositories:structuredClone(state.repositories), profiles:state.profiles ?? (state.hasProfiles ? [{id:'profile-demo',name:locale==='zh-TW'?'示範連線設定檔':'Demo profile',url:'wss://demo.example:9876/',
            fingerprint:'a'.repeat(64),profile_id:'default',token_available:true}] : []), profiles_error: state.profilesError, busy:false}};
      if (state.hold) await new Promise(resolve => {state.release = resolve;});
      return result;
    }
    if (path === '/managed/setup/secrets') {state.staged.push(input); return {status:200,data:{secret_ref:'setup_'+(state.staged.length===1?'b':'d').repeat(32),kind:input.body.kind}};}
    if (path === '/managed/setup/preview') {
      state.previews.push(input);
      if (state.previewRefused) return {status:state.refusalStatus,data:{error:{code:state.refusalCode,message:state.refusalMessage}}};
      const params = input.body.params;
      const preview = structuredClone(previewGolden[params.secret_ref ? 'staged' : 'profile'].preview);
      preview.target = input.body.target; preview.config_revision = input.body.preconditions.config_revision;
      for (const change of preview.changes) if (Object.hasOwn(params, change.field)) change.after = params[change.field];
      preview.source.reference = params.secret_ref || params.import_profile_id || input.body.target.host;
      return {status:200, data:{preview}};
    }
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
        idempotency_key:input.idempotency_key,status:'succeeded',result:{configured:true,revision:state.revision}};
      return {status:200,data:{operation:state.operation}};
    }
    return base.dispatch(input);
  };
  await page.addInitScript(locale => {if (locale && !localStorage.getItem('batc.lang')) localStorage.setItem('batc.lang',locale);}, locale);
  if (evidence) await page.addInitScript(operation => {
    const namespace = [location.origin, 'setup-central', 'setup-person', operation.actor].map(encodeURIComponent).join(':');
    const {action, target, params, preconditions, operation_id, idempotency_key: key} = operation;
    localStorage.setItem(`batc.managed.setup.${namespace}`, JSON.stringify({
      intent: {key, operation_id, request: {action, target, params, preconditions}}}));
  }, evidence.operation);
  if (native) {
    await page.exposeFunction('setupConnector', dispatch);
    await page.exposeFunction('setupManagedControl', (input: any) => {
      state.managedCalls.push(input.action);
      if (input.action === 'stop') {
        if (state.stopBusy) throw Error('SERVICE_BUSY: finish or reconcile current operations before stopping');
        state.managed.lifecycle = {state: state.stopPending ? 'stop_pending' : 'stopped', requested_at: 1800000000,
          stopped_at: state.stopPending ? null : 1800000001};
      }
      if (input.action === 'start' && state.startStillStopping) throw Error('SERVICE_STILL_STOPPING');
      if (input.action === 'start') state.managed.lifecycle = {state: 'running', requested_at: null, stopped_at: null};
      return state.managed;
    });
    await page.addInitScript(c => Object.assign(window,{isTauri:true,__TAURI_INTERNALS__:{
      invoke:async (command:string,args:any) => {
        if(command==='native_status') return {endpoint:'http://127.0.0.1:9870',credential_available:true};
        if(command==='connector_connect') return c;
        if(command==='connector_request') return (window as any).setupConnector(args.input);
        if(command==='managed_control') return (window as any).setupManagedControl(args.input);
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

// Review is a distinct user choice. Keep original receipt/refusal assertions after Apply.
export async function applySetupReview(page: Page) {
  await page.locator('[data-setup-review] button.primary').click();
}

// Existing manual/recovery regressions deliberately exercise the advanced path.
export async function openSetupAdvanced(page: Page) {
  const advanced = page.locator('[data-setup-advanced]');
  if (new URL(page.url()).hash.startsWith('#/sessions')) return;
  await advanced.waitFor({state: 'attached'});
  if (!await advanced.evaluate(node => node.hasAttribute('open')))
    await advanced.locator(':scope > summary').click();
}
