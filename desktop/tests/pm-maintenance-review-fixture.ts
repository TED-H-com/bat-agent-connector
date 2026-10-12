import {expect,type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {unavailableBriefTransport} from './pm-source-transport-fixture.ts';

export const wid='ws_'+'1'.repeat(32),pid='pmmp_'+'2'.repeat(32),bid='pmrb_'+'3'.repeat(32),sid='pmsnap_'+'4'.repeat(32);
const actions=[...['prepare','note','candidate.record','complete','follow_up.record'].map(key=>'pm.maintenance_review.'+key),'pm.input.record'];
const now=1791716400;
export const action=(page:Page,name:string)=>page.locator(`[data-pm-action="pm.maintenance_review.${name}"]`);
export function reviewFixture(){
  const state:any={actor:'owner',role:'owner',manage:true,writes:[],reads:[],ops:new Map(),reviews:[],snapshots:[],lost:false,
    hidden:false,failRead:false,wrongScope:false,paged:false,badCursor:false,eventPages:false,zeroCursor:false,delayed:false,lifecycle:2,epoch:0,
    threads:[{workspace_id:wid,thread_id:'pmt_'+'6'.repeat(32),title:'Possible recovery improvements',mode:'idea',version:2}]};
  const policy={workspace_id:wid,policy_id:pid,revision:3,document:{workspace_id:wid,policy_id:pid,revision:3,baseline_id:bid}},baseline={workspace_id:wid,baseline_id:bid,version:1};
  const addReview=(title='Membership service maintenance')=>{
    const n=state.reviews.length+1,rid='pmrevw_'+n.toString(16).padStart(32,'0'),snapshotId='pmsnap_'+n.toString(16).padStart(32,'0');
    const report={schema_version:1,period:{start:now-7*86400,end:now},as_of:now,release:{repository:'example/product',channel:'stable',release_version:{kind:'tag',value:'v1.4.0'},baseline_id:bid,commit_sha:'a'.repeat(40)},
      policy:{version:3},coverage:{github:{status:'observed',samples:1},workload:{status:'incomplete',samples:0},checks:{status:'unchecked',samples:0},issues:'not_observed',dependencies:'not_observed',research:'not_performed',quality:'unassessed',model_analysis:'not_requested'},
      changes:{status:'no_observed_changes',added:[],changed:[],no_longer_observed:[],scope_changed:false},current_blockers:[],effort:{completed_checks:0,pending_checks:0},
      observations:[{source_id:'pmrs_'+'2'.repeat(24),observed_at:now-60,observed_open_pulls:0,observed_nonterminal_actions:0}],workloads:[],checks:[],
      previous_review:null,next_observation_focus:['review_source_coverage','verify_reported_results'],sources:[{source_id:'pmrs_'+'1'.repeat(24),kind:'policy',id:pid,digest:'d'.repeat(64)},
        {source_id:'pmrs_'+'2'.repeat(24),kind:'github',id:'pmgh_'+'5'.repeat(32),digest:'e'.repeat(64)}],meeting_completed:false,scope_adopted:false,automatic_maintenance_authorized:false};
    const snapshot={workspace_id:wid,snapshot_id:snapshotId,title,snapshot_digest:'a'.repeat(64),view_digest:'b'.repeat(64),as_of:now,journal_cursor:40,
      maintenance:{schema_version:1,review_id:rid,report},visibility:{filtered:false}};
    const review={workspace_id:wid,review_id:rid,sequence:n,revision:n,version:1,status:'prepared',title,snapshot_id:snapshotId,created_at:now,operation_id:'op_'+n.toString(16).padStart(32,'0'),completion_id:null};
    const detail={workspace_id:wid,review,snapshot,completion:null,events:[],next_cursor:null,visibility_filtered:false};
    state.reviews.push(detail);state.snapshots.push(snapshot);return detail;
  };
  const addEvent=(detail:any,kind:string,document:any,actor='owner')=>{
    const n=detail.events.length+1,event={workspace_id:wid,review_id:detail.review.review_id,sequence:n,event_id:'pmrve_'+n.toString(16).padStart(32,'0'),kind,
      document:{...document,rationale:document.rationale||'Keep an attributed record'},actor,created_at:now,operation_id:'op_'+(n+20).toString(16).padStart(32,'0')};
    detail.events.push(event);return event;
  };
  const initial=addReview();addEvent(initial,'note',{content:'We discussed the incomplete workload evidence.',source_refs:[]},'developer');
  const config=()=>({caps:{actor:state.actor,scopes:['observe',...(state.manage?['manage']:[])],actions:actions.map(action=>({action,allowed:state.manage})),
      features:{pm_workspaces:{maintenance_review:{version:1,planning_candidate_source:{version:1}},maintenance_checks:{version:1,weekly_review_execution:true},brief_export:{formats:['markdown','html','svg'],locales:['en','zh-TW']}}}},
    workspace:{workspace_id:wid,version:3,membership:{actor:state.actor,role:state.role},members:[{actor:'owner',role:state.actor==='owner'?state.role:'owner'},
      {actor:'developer',role:state.actor==='developer'?state.role:'contributor'},{actor:'reader',role:state.actor==='reader'?state.role:'reader'}]}});
  const response=(data:any,status=200)=>({data,status});
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),pathname=url.pathname,base='/pm/workspaces/'+wid;
    if(input.method==='GET'){
      state.reads.push(input.path);
      if(pathname.startsWith('/operations/'))return response({operation:[...state.ops.values()].find((op:any)=>pathname.endsWith(op.operation_id))});
      if(state.failRead)return response({error:{code:'FORBIDDEN',message:'Original sources unavailable'}},403);
      if(pathname===base+'/threads')return response({workspace_id:wid,threads:state.threads,next_cursor:null});
      if(pathname.startsWith(base+'/threads/')){const thread=state.threads.find((row:any)=>pathname.endsWith(row.thread_id));
        return thread?response({thread,capabilities:{write:state.manage&&['owner','contributor'].includes(state.role)}}):response({error:{code:'NOT_FOUND',message:'Idea unavailable'}},404);}
      if(pathname===base+'/maintenance-policies/current')return response({workspace_id:wid,policy,head:{policy_id:pid,revision:3},visibility_filtered:false});
      if(pathname===base+'/release-baselines/current')return response({workspace_id:wid,baseline,head:{baseline_id:bid,version:1},visibility_filtered:false});
      if(pathname===base+'/lifecycle')return response({workspace_id:wid,lifecycle:{revision:state.lifecycle,execution_epoch:state.epoch,stage:state.stage||'development'}});
      if(pathname===base+'/plans/head')return response({version:1,head:{version:4}});
      if(pathname===base+'/maintenance-schedule')return response({workspace_id:wid,visibility_filtered:false,schedule:{weekly_review:{execution_available:true,next_due:now+86400,
        blocked_reason:state.scheduleBlocked?'PM_MAINTENANCE_BUDGET_EXHAUSTED':null,pending_report:state.reviews.at(-1)?.review.review_id}}});
      if(pathname===base+'/snapshots'){
        if(state.delayed&&(state.delayedAfter===undefined||state.delayedAfter===Number(url.searchParams.get('after'))))await new Promise<void>(resolve=>{state.releaseRead=resolve;});
        if(state.paged&&!url.searchParams.get('after')?.match(/^[1-9]/))return response({workspace_id:wid,snapshots:[],next_cursor:10,visibility_filtered:true});
        return response({workspace_id:wid,snapshots:state.snapshots,next_cursor:state.badCursor?10:null,visibility_filtered:false});
      }
      if(pathname===base+'/maintenance-reviews')return response({workspace_id:wid,reviews:state.hidden?[]:state.reviews.map((detail:any)=>detail.review),next_cursor:null,visibility_filtered:state.hidden});
      if(pathname.endsWith('/events')){
        const detail=state.reviews.find((row:any)=>pathname.includes(row.review.review_id)),after=Number(url.searchParams.get('after'));
        if(state.eventPages&&after<10)return response({workspace_id:wid,review_id:detail.review.review_id,events:[],next_cursor:10,visibility_filtered:true});
        const extra={workspace_id:wid,review_id:detail.review.review_id,event_id:'pmrve_'+'a'.repeat(32),sequence:11,kind:'note',actor:'owner',created_at:now,
          document:{content:'We also reviewed the next observation focus.',rationale:'Second discussion'},operation_id:'op_'+'b'.repeat(32)};
        return response({workspace_id:wid,review_id:detail.review.review_id,events:[extra],next_cursor:state.badCursor?after:null,visibility_filtered:false});
      }
      if(pathname.startsWith(base+'/maintenance-reviews/')){
        const current=pathname.endsWith('/current'),detail=structuredClone(current?state.reviews.at(-1):state.reviews.find((row:any)=>pathname.endsWith(row.review.review_id)));
        if(current&&state.hidden)return response({workspace_id:wid,review:null,revision:state.reviews.length,visibility_filtered:true});
        if(!detail)return response({error:{code:'NOT_FOUND',message:'Review unavailable'}},404);
        if(current)detail.revision=state.reviews.length;
        if(state.wrongScope)detail.workspace_id='ws_'+'0'.repeat(32);
        if(state.eventPages){detail.next_cursor=state.zeroCursor?0:1;if(state.zeroCursor)detail.events=[];}
        return response(detail);
      }
      throw Error('Unexpected read '+input.path);
    }
    if(input.method!=='POST'||pathname!=='/operations')throw Error('Unexpected write '+input.path);
    state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
    if(!op){
      const {action,target,params}=input.body;let detail=state.reviews.find((row:any)=>row.review.review_id===target.review_id),result:any={workspace_id:wid,execution_authorized:false};
      if(action==='pm.input.record'){
        const thread=state.threads.find((row:any)=>row.thread_id===target.thread_id);thread.version++;
        result={...result,thread_id:thread.thread_id,input_id:'pmi_'+'7'.repeat(32),version:thread.version,mode:thread.mode};
      }else if(action==='pm.maintenance_review.prepare'){
        detail=addReview(params.title);detail.snapshot.maintenance.report.period={start:params.period_start,end:params.period_end};
        result={...result,review_id:detail.review.review_id,revision:detail.review.revision,version:1,snapshot_id:detail.snapshot.snapshot_id,meeting_completed:false};
      }else{
        detail.review.version++;const kind=action.replace('pm.maintenance_review.',''),event=addEvent(detail,kind,params,state.actor);
        if(kind==='complete'){detail.review.status='completed';detail.review.completion_id=event.event_id;detail.completion=event;}
        result={...result,review_id:detail.review.review_id,version:detail.review.version,event_id:event.event_id,meeting_completed:detail.review.status==='completed'};
      }
      op={...input.body,actor:state.actor,idempotency_key:input.idempotency_key,operation_id:'op_'+(state.ops.size+100).toString(16).padStart(32,'0'),status:'succeeded',result};
      state.ops.set(input.idempotency_key,op);
    }
    if(state.lost){state.lost=false;return response({error:{code:'LOST_REPLY',message:'Reply unavailable'}},503);}
    if(state.mismatch)return response({operation:{...op,actor:'wrong-actor'}});
    return response({operation:op});
  };
  return {state,config,dispatch,addReview,addEvent};
}
export async function mount(page:Page,f:ReturnType<typeof reviewFixture>,locale='en'){
  await page.exposeFunction('reviewFixtureRequest',f.dispatch);await page.exposeFunction('reviewFixtureConfig',f.config);
  await page.route('**/__review_src/*.js',async route=>{
    const file=path.basename(new URL(route.request().url()).pathname);let source=await readFile(new URL('../src/'+file,import.meta.url),'utf8');
    if(file==='pm-workspace.js')source=source.replace('function durableForm(', 'export function durableForm(');
    await route.fulfill({contentType:'text/javascript',body:source});
  });
  await unavailableBriefTransport(page,'__review_src');
  await page.route('**/__review_style.css',async route=>route.fulfill({contentType:'text/css',body:await readFile(new URL('../src/pm-maintenance-review.css',import.meta.url),'utf8')}));
  await page.route('**/__review_harness',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/dashboard/app.css"><link rel="stylesheet" href="/__review_style.css"><div class="pm-shell"><aside><h2>PM workspace</h2><p>Membership service</p></aside><main class="pm-main" id="mount"></main></div>'}));
  await page.goto('/__review_harness');await boot(page,locale);
}
export async function boot(page:Page,locale='en'){
  await page.evaluate(async locale=>{
    const {workspaceMaintenanceReview}=await import('/__review_src/pm-maintenance-review.js'),{durableForm}=await import('/__review_src/pm-workspace.js');
    const {maintenanceReviewStrings}=await import('/__review_src/pm-maintenance-review-strings.js'),{pmStrings}=await import('/__review_src/pm-strings.js');
    const {pmMaintenancePolicyStrings}=await import('/__review_src/pm-maintenance-policy-strings.js');
    const {maintenanceFindingStrings}=await import('/__review_src/pm-maintenance-findings-strings.js');
    const strings={...pmStrings[locale],...pmMaintenancePolicyStrings[locale],...maintenanceFindingStrings[locale],...maintenanceReviewStrings[locale]},t=(key:string,args:any={})=>Object.entries(args).reduce((text,[name,value])=>text.replaceAll('{'+name+'}',String(value)),strings[key]||key);
    const h=(tag:string,attrs:any={},...children:any[])=>{const el=document.createElement(tag);for(const[key,value]of Object.entries(attrs||{})){
      if(value===null||value===undefined||value===false)continue;if(key.startsWith('on'))el.addEventListener(key.slice(2),value as any);else el.setAttribute(key,value===true?'':String(value));
    }for(const child of children.flat()){if(child===null||child===undefined||child===false)continue;el.append(child instanceof Node?child:document.createTextNode(String(child)));}return el;};
    const win=window as any;let config=await win.reviewFixtureConfig();
    const api=async(method:string,path:string,body:any,key:any)=>{const result=await win.reviewFixtureRequest({method,path,body,idempotency_key:key});
      if(result.status>=400)throw Object.assign(Error(result.data.error.message),{code:result.data.error.code,status:result.status});return result.data;};
    const errorBox=(error:any)=>h('p',{class:'error'},error.message),input=(label:string,{multiline=false,max=4000,required=true,value=''}:any={})=>{
      const field=h(multiline?'textarea':'input',{'aria-label':t(label),maxlength:max,required}) as HTMLInputElement;field.value=value;return field;
    },labelled=(key:string,field:any)=>h('label',{},t(key),field),caps=()=>config.caps,alive=()=>{};
    const form=(options:any)=>durableForm({h,t,api,caps,guard:alive,namespace:'review-ui.'+config.caps.actor,errorBox,...options});
    const ui=workspaceMaintenanceReview({h,t,api,alive,workspaceId:config.workspace.workspace_id,workspace:()=>config.workspace,caps,
      permitted:(decide:boolean)=>config.caps.scopes.includes('manage')&&(decide?config.workspace.membership.role==='owner':['owner','contributor'].includes(config.workspace.membership.role)),form,input,labelled,errorBox,namespace:'review-ui.'+config.caps.actor});
    document.getElementById('mount')!.append(ui.element);win.refreshReviews=async()=>{config=await win.reviewFixtureConfig();await ui.refresh();};win.withdrawReviews=ui.withdraw;await ui.refresh();
  },locale);
  await expect(page.locator('[data-pm-maintenance-review]')).toBeVisible();
}
export async function openAction(page:Page,name:string){await action(page,name).evaluate(el=>{let node=el.parentElement;while(node){if(node.tagName==='DETAILS')(node as HTMLDetailsElement).open=true;node=node.parentElement;}});}
export async function reason(page:Page,name:string,value='Keep the fixed discussion outcome'){await openAction(page,name);await action(page,name).getByLabel('Reason for this record',{exact:true}).fill(value);}
