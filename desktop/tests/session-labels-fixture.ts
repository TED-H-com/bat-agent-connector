import {expect,type Page} from '@playwright/test';
export const target={host:'demo',session_id:'manual-full-session'}, opId='op_'+'b'.repeat(32);
export async function labelsFixture(page:Page,native:boolean,options:any={}) {
 const state:any={actor:'label-person',principal:'labels-principal',server:'labels-server',scopes:['observe','manage'],allowed:true,
  metadata:{version:0,labels:[],updated_by:null,updated_at:null},posts:[],reads:[],events:[],after:0,errors:[],status:'succeeded',...options};
 const dispatch=async(input:any)=>{
  const url=new URL(input.path,'http://fixture'),path=url.pathname;
  const caps={actor:state.actor,scopes:state.scopes,api_version:1,contract_version:'2026-10-08',hosts:[{host:'demo',writes:false}],
   actions:state.allowed===null?[]:[{action:'session.labels.set',allowed:state.allowed}],features:{checkpoints:[]}};
  const row={...target,title:'Review the shared dashboard',workspace:'Connector',workspace_id:'workspace-1',agent_kind:'claude',
   provenance:state.provenance||'manual',api_access:'read_only',streaming:false,connector_metadata:state.metadata,
   ...(state.wrongIdentity?{session_id:'wrong-full-session'}:{})};
  if(input.method==='POST'){
   state.posts.push(input);
   if(state.refuse)return {status:state.refuse==='FORBIDDEN'?403:409,data:{error:{code:state.refuse,message:state.refuse}}};
   if(!state.operation){
    state.operation={...input.body,actor:state.actor,operation_id:opId,idempotency_key:input.idempotency_key,status:state.status,
     result:{...target,connector_metadata:{labels:input.body.params.labels,version:input.body.preconditions.expected_version+1}},steps:[]};
    if(state.status==='succeeded')state.metadata={...state.metadata,...state.operation.result.connector_metadata};
   }
   const operation=structuredClone(state.operation);
   if(state.delayPost)await new Promise(resolve=>state.holdPost=resolve);
   if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST',message:'Lost label reply'}}};}
   if(state.badKey)operation.idempotency_key='wrong-key';
   return {status:200,data:{operation}};
  }
  state.reads.push(input.path);
  if(path==='/operations/'+opId){
   if(state.failOp)return {status:503,data:{error:{code:'READ_FAILED',message:'Receipt read failed'}}};
   return {status:200,data:{operation:{...state.operation,status:state.status}}};
  }
  if(path==='/sessions/demo/manual-full-session'&&state.failMetadata)return {status:503,data:{error:{code:'READ_FAILED',message:'Metadata read failed'}}};
  if(path==='/events')state.after=Number(url.searchParams.get('after'));
  const events=state.events.filter((e:any)=>e.seq>state.after),cursor=events.at(-1)?.seq||state.after;
  if(path==='/events'&&events.length)state.delivered=true;
  return {status:200,data:path==='/capabilities'?caps:path==='/bootstrap'?{capabilities:caps,sync:{version:1,server_id:state.server,principal_id:state.principal,checkpoint:{cursor:0,token:'proof-0'}}}
   :path==='/events'?{events,head_cursor:cursor,next_cursor:cursor,sync:{checkpoint:{cursor,token:'proof-'+cursor}}}
   :path==='/sessions/demo/manual-full-session'?{session:state.retained?null:row,cleanup:state.retained?[{kind:'session',...target}]:[],connector_metadata:state.metadata}
   :path==='/sessions'?{sessions:[row],next_cursor:null}
   :{messages:[],sessions:[],operations:[],hosts:[{host:'demo'}],work_items:[],projects:[],checkpoints:[]}};
 };
 page.on('pageerror',e=>state.errors.push(e.message));
 if(native){
  await page.exposeFunction('labelsFixture',dispatch);
  await page.addInitScript(()=>Object.assign(window,{isTauri:true,__TAURI_INTERNALS__:{invoke:async(command:string,args:any)=>{
   if(command==='native_status')return {endpoint:'https://fixture.example',credential_available:true};
   if(command==='connector_connect')return (await (window as any).labelsFixture({method:'GET',path:'/capabilities'})).data;
   if(command==='connector_disconnect')return;
   if(command==='connector_request')return (window as any).labelsFixture(args.input);
   if(command==='fleet_availability')return {configured:false,platform_supported:false};
   throw Error(command);
  }}}));
 }else await page.addInitScript(()=>sessionStorage.setItem('batc.dashboard.token','fixture-token'));
 await page.route('**/api/v1/**',async route=>{
  if(native)throw Error('Native flow must not use browser HTTP');
  const req=route.request(),url=new URL(req.url());
  const res=await dispatch({method:req.method(),path:url.pathname.slice('/api/v1'.length)+url.search,body:req.postDataJSON(),idempotency_key:req.headers()['idempotency-key']});
  await route.fulfill({status:res.status,json:res.data});
 });return state;
}
export async function openLabels(page:Page){
 await page.goto('/dashboard/#/session/demo/manual-full-session');
 await expect(page.locator('[data-session-view]')).toBeVisible();
 const opener=page.getByRole('button',{name:/^(Results|成果)$/,exact:true});
 if(await opener.isVisible())await opener.click();
 const panel=page.locator('[data-session-labels]');await expect(panel).toBeVisible();await panel.locator('summary').click();return panel;
}
