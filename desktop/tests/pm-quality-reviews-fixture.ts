import {expect,type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {unavailableBriefTransport} from './pm-source-transport-fixture.ts';
export const workspaceId='ws_'+'1'.repeat(32),workId='wi_'+'7'.repeat(20),packageId='pmwp_'+'2'.repeat(32),childId='op_'+'3'.repeat(32),dodId='pmdod_'+'4'.repeat(32),candidate='a'.repeat(40);
export const CREATE='pm.quality.review.create',RECORD='pm.quality.review.record',REVOKE='pm.quality.review.revoke',VERIFY='pm.execution.verify';
export const panel=(page:Page)=>page.locator('[data-pm-quality-reviews]'),editor=(page:Page,action=CREATE)=>panel(page).locator(`[data-pm-action="${action}"]`);
const condition={kind:'fixed_revision_verification',profile:'observed_runner_v1',command_digest:'b'.repeat(64)};
export function qualityFixture(){
  const package_ref={workspace_id:workspaceId,package_id:packageId,document_digest:'c'.repeat(64)},plan_ref={revision_id:'pmrev_'+'9'.repeat(32),document_digest:'9'.repeat(64),scope_revision:'pmscope_'+'9'.repeat(32)},work_ref={work_item_id:workId,binding_id:'pmwb_'+'5'.repeat(32)};
  const criteria=[{criterion:{kind:'acceptance',requirement_id:'recovery',input_id:'in_'+'6'.repeat(32)},text:'Private recovery criterion: preserve original owner',original_confirmed:true,condition},
    {criterion:{kind:'dod',criterion_id:'tests'},text:'Private completion criterion: verify the fixed result',original_confirmed:true,condition}];
  const pkg={...package_ref,work_item_id:workId,document:{title:'Restore original ownership',work_item_id:workId,context:{plan_ref,work_ref,dod_ref:{revision_id:dodId}},requirements:[{id:'recovery',acceptance:[{text:criteria[0].text,source_input_id:criteria[0].criterion.input_id}]}]}};
  const state:any={actor:'reviewer',role:'owner',deny:false,gate:null,partial:false,badCursor:false,lost:false,writes:[],reads:[],ops:new Map(),reviews:[],selection:null,changes:[],
    packages:[pkg],dods:[{workspace_id:workspaceId,revision_id:dodId,document:{plan_ref,criteria:[{id:'tests',text:criteria[1].text}]}}],
    verifyEnabled:false,verifyRecords:[],scopes:null,config:{workspace_id:workspaceId,execution_operation_id:childId,handoff_contract_digest:'f'.repeat(64),profile:'observed_runner_v1',available:true,reason:null,command_digest:condition.command_digest,candidate_commit:candidate,tree_hash:'d'.repeat(40),candidate_reason:null},
    operations:[{operation_id:childId,action:'repository.continue',actor:'owner',status:'succeeded',params:{work_item_id:workId},external_refs:{admission_binding:{pm_handoff:{contract:{package_ref}}}}}],
    evidence:[{workspace_id:workspaceId,evidence_id:'pme_'+'8'.repeat(32),source_kind:'observed_verification',document:{facts:{verification_id:7,candidate_commit:candidate,tree_hash:'d'.repeat(40),command_digest:condition.command_digest,source:'observed_runner',verification:'passed'}}}],
    deliveries:[],observations:[]};
  const assess=(row:any,conclusions:any[]=criteria.map(item=>({criterion:item.criterion,verdict:'accepted',rationale:'Independent result and original behavior inspected',reference:{verification_id:7,candidate_commit:candidate,tree_hash:'d'.repeat(40)}})))=>{
    row.version++;const accepted=conclusions.every(item=>item.verdict==='accepted'&&item.reference),value=accepted?'accepted':'unassessed';
    row.assessment={operation_id:'op_'+String(40+row.version).padStart(32,'0'),version:row.version,document_digest:String(row.version).repeat(64).slice(0,64),actor:state.actor,kind:'assessment',
      document:{criteria:conclusions.map(item=>({...item,status:item.verdict==='accepted'&&item.reference?'accepted':item.verdict==='rejected'?'rejected':'unassessed',verification:item.reference?{status:'passed'}:null})),acceptance:value,dod:value,qualified:accepted}};
    row.history.push({...row.assessment,document:undefined});row.original_outcome={acceptance:value,dod:value,qualified:accepted};row.acceptance=row.dod=value;row.qualified=accepted;return row;
  };
  const save=(qualified=false,params:any={})=>{const row={workspace_id:workspaceId,work_item_id:workId,sequence:state.reviews.length+1,review_id:'pmqr_'+String(state.reviews.length+1).padStart(32,'0'),operation_id:'op_'+'6'.repeat(32),version:1,context_digest:'e'.repeat(64),
    context:{package_ref,plan_ref,work_ref,dod_ref:{revision_id:dodId},execution_operation_id:childId,native_result:{candidate_commit:params.candidate_commit||candidate},reviewer_actor:params.reviewer_actor||'reviewer',self_review:params.reviewer_actor===state.actor,policy_rationale:params.rationale||'Fixed complete acceptance and DoD policy',criteria:criteria.map(item=>({...item,condition:params.criteria?.find((value:any)=>JSON.stringify(value.criterion)===JSON.stringify(item.criterion))?.condition||item.condition}))},
    assessment:null,history:[],revocation:null,original_outcome:{acceptance:'unassessed',dod:'unassessed',qualified:false},applicable:true,applicability_reasons:[],acceptance:'unassessed',dod:'unassessed',qualified:false};state.reviews.push(row);return qualified?assess(row):row;};
  const caps=()=>({actor:state.actor,scopes:state.scopes||(state.role==='reader'?['observe']:['observe','manage','operate']),actions:[CREATE,RECORD,REVOKE,VERIFY].map(action=>({action,allowed:state.role!=='reader'})),features:{pm_workspaces:{quality_reviews:{version:1},execution_verification:state.verifyEnabled?{version:1}:null}}});
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),pathname=url.pathname;if(input.method==='GET')state.reads.push(input.path);
    if(pathname==='/capabilities')return {status:200,data:caps()};
    if(pathname===`/pm/workspaces/${workspaceId}`)return {status:200,data:{workspace:{membership:{role:state.role},members:[{actor:state.actor,role:state.role},{actor:'other-reviewer',role:'contributor'}]}}};
    if(state.deny)return {status:404,data:{error:{code:'NOT_FOUND',message:'Original sources withdrawn'}}};
    if(input.method==='GET'){
      if(pathname.startsWith('/operations/')){const op=[...state.ops.values()].find((op:any)=>pathname.endsWith(op.operation_id));return op?{status:200,data:{operation:op}}:{status:404,data:{error:{code:'NOT_FOUND',message:'Operation unavailable'}}};}
      if(pathname==='/operations')return {status:200,data:{operations:structuredClone(url.searchParams.get('action')===VERIFY?[...state.ops.values()].filter((op:any)=>op.action===VERIFY):state.operations),next_before:null}};
      if(pathname.endsWith('/verification-config'))return {status:200,data:structuredClone(state.config)};
      if(pathname.includes('/execution-verifications/'))return {status:200,data:{workspace_id:workspaceId,operation_status:'succeeded',verification:structuredClone(state.verifyRecords.find((row:any)=>pathname.endsWith(row.operation_id))||null)}};
      if(state.gate)await state.gate;
      if(pathname.includes('/evidence/'))return {status:200,data:{evidence:structuredClone(state.evidence.find((row:any)=>pathname.endsWith(row.evidence_id)))}};
      let key='',rows:any[]=[],cursor='next_cursor';
      if(pathname.endsWith('/work-packages')){key='packages';rows=state.packages;}
      else if(pathname.endsWith('/sprint-artifacts/dod')){key='revisions';rows=state.dods;}
      else if(pathname.endsWith('/quality')){key='reviews';rows=state.reviews;}
      else if(pathname.endsWith('/evidence')){key='evidence';rows=state.evidence;}
      else if(pathname.endsWith('/deliveries')){key='deliveries';rows=state.deliveries;cursor='next_after';}
      else if(pathname.endsWith('/github-observations')){key='observations';rows=state.observations;}
      else throw Error('Unhandled fixture read '+input.path);
      const partial=state.partial&&key==='reviews'&&Number(url.searchParams.get('after'))===0;
      return {status:200,data:{workspace_id:workspaceId,[key]:structuredClone(partial?[]:rows),[cursor]:state.badCursor&&key==='reviews'?0:partial?3:null,visibility_filtered:partial}};
    }
    if(pathname==='/operations'){
      state.writes.push(structuredClone(input));let op=state.ops.get(input.idempotency_key);
      if(!op&&input.body.action===VERIFY){const operation_id='op_'+String(200+state.ops.size).padStart(32,'0');const receipt={operation_id,workspace_id:workspaceId,execution_operation_id:input.body.target.execution_operation_id,reference:{verification_operation_id:operation_id,candidate_commit:input.body.params.candidate_commit,tree_hash:input.body.params.tree_hash},receipt_digest:'a'.repeat(64),profile:'observed_runner_v1',status:'passed',exit_code:0,semantic_acceptance:'not_evaluated'};state.verifyRecords.push(receipt);op={...input.body,actor:state.actor,idempotency_key:input.idempotency_key,operation_id,status:'succeeded',result:receipt};state.ops.set(input.idempotency_key,op);}
      if(!op){const {action,params,target,preconditions}=input.body;let row=state.reviews.find((row:any)=>row.review_id===target.review_id);
        if(action!==CREATE&&preconditions.expected_version!==row.version)return {status:409,data:{error:{code:'VERSION_CONFLICT',message:'Review version changed',admission_refused:true}}};
        if(action===CREATE)row=save(false,params);else if(action===RECORD)assess(row,params.conclusions);else {row.version++;row.revocation={operation_id:'op_'+'9'.repeat(32),actor:state.actor,version:row.version,document_digest:'f'.repeat(64),document:{reason:params.reason},kind:'revocation'};row.history.push(row.revocation);row.applicable=row.qualified=false;row.applicability_reasons=['review_revoked'];row.acceptance=row.dod='unassessed';}
        op={...input.body,actor:state.actor,idempotency_key:input.idempotency_key,operation_id:'op_'+String(100+state.ops.size).padStart(32,'0'),status:'succeeded',result:{review_id:row.review_id,version:row.version}};state.ops.set(input.idempotency_key,op);}
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}return {status:200,data:{operation:structuredClone(op)}};
    }throw Error('Unhandled fixture '+input.path);
  };return {state,save,assess,dispatch,criteria,package_ref};
}
export async function mountQuality(page:Page,f:ReturnType<typeof qualityFixture>,locale='en'){
  page.on('pageerror',error=>{throw error;});
  await page.exposeFunction('qualityFixtureRequest',f.dispatch);
  await page.route('**/__quality_src/*.js',async route=>{const file=path.basename(new URL(route.request().url()).pathname);let source=await readFile(new URL('../src/'+file,import.meta.url),'utf8');
    if(file==='pm-workspace.js')source=source.replace('function durableForm(', 'export function durableForm(');await route.fulfill({contentType:'text/javascript',body:source});});
  await unavailableBriefTransport(page,'__quality_src');
  await page.route('**/__quality_harness',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/dashboard/app.css"><main class="pm-main" id="mount"></main>'}));
  await page.goto('/__quality_harness');await bootQuality(page,locale);
}
export async function bootQuality(page:Page,locale='en'){
  await page.evaluate(async({locale,workspaceId,workId})=>{
    const {workspaceQualityReviews}=await import('/__quality_src/pm-quality-reviews.js'),{durableForm}=await import('/__quality_src/pm-workspace.js');
    const {pmQualityReviewsStrings}=await import('/__quality_src/pm-quality-reviews-strings.js'),{pmStrings}=await import('/__quality_src/pm-strings.js');
    const strings={...pmStrings[locale],...pmQualityReviewsStrings[locale]},t=(key:string)=>strings[key]||key;
    const h=(tag:string,attrs:any={},...children:any[])=>{const el=document.createElement(tag);for(const[key,value]of Object.entries(attrs||{})){if(value===null||value===undefined||value===false)continue;
      if(key.startsWith('on'))el.addEventListener(key.slice(2),value as any);else el.setAttribute(key,value===true?'':String(value));}for(const child of children.flat()){if(child!==null&&child!==undefined&&child!==false)el.append(child instanceof Node?child:document.createTextNode(String(child)));}return el;};
    const win=window as any,api=async(method:string,path:string,body:any,key:any)=>{const result=await win.qualityFixtureRequest({method,path,body,idempotency_key:key});
      if(result.status>=400)throw Object.assign(Error(result.data.error.message),{code:result.data.error.code,status:result.status,admissionRefused:result.data.error.admission_refused});return result.data;};
    const {createErrorBox}=await import('/__quality_src/test-error-box.js');
    const errorBox=createErrorBox(h,t,locale),input=(label:string,{multiline=false,max=4000,required=true,value=''}:any={})=>{
      const field=h(multiline?'textarea':'input',{'aria-label':t(label),maxlength:max,required}) as HTMLInputElement;field.value=value;return field;},labelled=(key:string,field:any)=>h('label',{},t(key),field),alive=()=>{};
    let caps=await api('GET','/capabilities'),workspace=(await api('GET','/pm/workspaces/'+workspaceId)).workspace;
    const form=(options:any)=>durableForm({h,t,api,caps:()=>caps,guard:alive,namespace:'quality-ui.'+caps.actor,errorBox,...options});
    const quality=workspaceQualityReviews({h,t,api,alive,workspaceId,workspace:()=>workspace,caps:()=>caps,permitted:()=>['owner','contributor'].includes(workspace.membership?.role),form,input,labelled,errorBox,namespace:'quality-ui.'+caps.actor,
      onSelectionChange:(work:string,ref:any)=>{win.qualitySelected=ref;win.qualityChanges=(win.qualityChanges||[]).concat([{work,ref}]);}});
    document.getElementById('mount')!.append(quality.element);win.qualityRefresh=async()=>{caps=await api('GET','/capabilities');workspace=(await api('GET','/pm/workspaces/'+workspaceId)).workspace;await quality.refresh();};
    win.qualityOnlyRefresh=()=>quality.refresh();win.qualityWithdraw=()=>quality.withdraw();win.qualitySelection=()=>quality.selection();quality.setWork(workId);
  },{locale,workspaceId,workId});await expect(panel(page)).toBeVisible();await expect(editor(page).locator('select').first()).toHaveCount(1);
}
