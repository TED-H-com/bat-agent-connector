import type {Page} from '@playwright/test';
import {managedStatusFixture} from './managed-status-fixture.ts';
import {attentionFixture, mountAttention} from './attention-fixture.ts';
export async function diagnosticsFixture(page:Page, native=false, known=true, producerBootstrap:any=null) {
 const base=attentionFixture(),state={projectsStatus:200,hostsStatus:200,hostStale:false,operationsStatus:200,eventStatus:200,sessionReads:false,omitReading:false,historyReasons:['archive_unconfirmed','archive_budget','message_ids'] as string[]|undefined,scopes:['observe'],reads:[] as string[],hold:false,release:null as any,saved:[] as any[],sessions:[{host:'private-box.internal',session_id:'sample-1',title:'Private title',loaded:true,streaming:false,stale:false},{host:'private-box.internal',session_id:'sample-2',title:'Private title 2',loaded:false,streaming:true,stale:true,pending:{kind:'permission'}}] as any[]};
 const producer={bootstrap:producerBootstrap};
 const caps=()=>producer.bootstrap?.capabilities||({...base.caps(),connector:'0.2.5',build:{version:'0.2.5',commit:known?'a'.repeat(40):null,source:'vcs'},scopes:state.scopes,
  hosts:[{host:'private-box.internal',writes:true}],managed_installation:undefined});
 const dispatch=async(input:any)=>{
  state.reads.push(input.path);const path=new URL(input.path,'http://fixture').pathname;
  if(path==='/projects'&&state.projectsStatus!==200)return {status:state.projectsStatus,data:{error:{code:'BAT_ERROR',message:'Fixture project read failed'}}};
  if(path==='/capabilities')return {status:200,data:caps()};
  if(path==='/bootstrap'){if(producer.bootstrap)return {status:200,data:producer.bootstrap};const response=await base.dispatch(input);response.data.capabilities=caps();return response;}
  if(path==='/hosts'){
   const response={status:state.hostsStatus,data:state.hostsStatus===200?{hosts:[{host:'private-box.internal',reachable:true,stale:state.hostStale,server_version:'1.2.3',endpoint:'wss://192.0.2.8',error:'batc_secret /home/example/private'}]}:{error:{code:'FORBIDDEN',message:'private'}}};
   if(state.hold)await new Promise(resolve=>{state.release=resolve;});return response;
  }
  if(path==='/operations')return {status:state.operationsStatus,data:state.operationsStatus===200?{operations:[{operation_id:'op_'+'a'.repeat(32),action:'session.send',status:'failed',error_code:'BAT_ERROR',created_at:'2026-10-14T12:34:56Z',params:{prompt:'private prompt'},actor:'secret@example.invalid'}],next_before:123}:{error:{code:'FORBIDDEN'}}};
  if(path==='/sessions')return {status:200,data:{sessions:state.sessions,next_cursor:null}};
  if(state.sessionReads&&path==='/sessions/private-box.internal/sample-1')return {status:200,data:{session:state.sessions[0]}};
  if(state.sessionReads&&path==='/sessions/private-box.internal/sample-1/messages')return {status:200,data:{host:'private-box.internal',session_id:'sample-1',messages:[{id:'history-1',role:'assistant',text:'Private history message',ts:'2026-10-14T12:34:56Z'}],next_offset:null,...(state.omitReading?{}:{reading:{complete:false,unread_count:null,known_count:1,incomplete_reasons:state.historyReasons}})}};
  if(state.sessionReads&&path.startsWith('/sessions/private-box.internal/sample-1/'))return {status:200,data:{pending:null,history:[],relations:[],labels:[]}};
  if(path.startsWith('/sessions/'))return {status:503,data:{error:{code:'BAT_ERROR',message:'Private server error /home/example/private'}}};
  if(path==='/events'&&state.eventStatus!==200)return {status:state.eventStatus,data:{error:{code:'FORBIDDEN'}}};
  return base.dispatch(input);
 };
 if(native){
  await page.exposeFunction('diagnosticsRequest',dispatch);
  await page.exposeFunction('diagnosticsSave',(input:any)=>{state.saved.push(input);return {display_name:input.filename};});
  await page.addInitScript(({caps,known,managed})=>Object.assign(window,{isTauri:true,__TAURI_INTERNALS__:{invoke:async(command:string,args:any)=>{
   if(command==='native_status')return {endpoint:'https://central.example/',credential_available:true,diagnostics_save:true,app_build:{version:'0.1.1',commit:known?'b'.repeat(40):null}};
   if(command==='managed_control' && args.input.action==='status')return managed;
   if(command==='connector_connect')return caps;
   if(command==='connector_request')return (window as any).diagnosticsRequest(args.input);
   if(command==='native_diagnostics_save')return (window as any).diagnosticsSave(args.input);
   if(command==='connector_disconnect')return null;
   throw Error('Fixture feature unavailable');
  }}}),{caps:caps(),known,managed:managedStatusFixture(false)});
 }else await mountAttention(page,false,{...base,caps},dispatch);
 await page.addInitScript(()=>{Object.defineProperty(navigator,'clipboard',{value:{writeText:async(text:string)=>{(window as any).diagnosticsClipboard=text;}}});});
 return Object.assign(state,{producer});
}
