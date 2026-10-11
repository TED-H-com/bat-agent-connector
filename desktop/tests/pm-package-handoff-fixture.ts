import {issueFixture,packageId,route} from './pm-issue-mappings-fixture.ts';
import {workspaceId} from './pm-fixture.ts';
export {packageId,route,workspaceId};
export const projectId='prj_'+'8'.repeat(20),workId='wi_'+'7'.repeat(20),codeSha='c'.repeat(40),specSha='d'.repeat(40);
export const target={repository:'example/library',host:'fixture-host',workspace_id:'fixture-bat-workspace'};
export const metadata={action:'repository.continue',contract:'pm-engineering-handoff/v1',code_basis_required:true,source_materialization:'bounded_full_prompt'};
export function handoffFixture(){
  const fixture=issueFixture(),row=fixture.state.packages[0],doc=row.document;
  fixture.base.state.start=true;fixture.base.state.packages=false;
  row.work_item_id=doc.work_item_id=workId;doc.context.work_ref={...doc.context.work_ref,work_item_id:workId,project_id:projectId,work_fingerprint:'f'.repeat(64)};
  doc.code_source={...target,source_ref:'refs/heads/main',source_sha:codeSha,repository_id:7,binding_digest:'b'.repeat(64)};
  doc.unresolved=[];doc.sources={documents:[{path:'openspec/specs/recovery/spec.md',source_id:'pmssrc_'+'1'.repeat(64),content:'Private fixed specification',commit_sha:specSha,sha256:'2'.repeat(64)}]};
  const state={enabled:true,version:3,blocked:null as string|null,codeChanged:false,hostEnabled:true,deny:false,reject:false,reads:[] as any[],writes:[] as any[],ops:new Map<string,any>(),
    lost:false,previewGate:null as Promise<void>|null,writeGate:null as Promise<void>|null,lifecycleFailed:false};
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),path=url.pathname;
    if(path==='/capabilities'||path==='/bootstrap'){
      const response=await fixture.dispatch(input),caps=path==='/bootstrap'?response.data.capabilities:response.data;
      caps.features.pm_workspaces.issue_mapping=false;caps.features.pm_workspaces.package_handoff={...metadata,available:state.enabled};
      caps.features.repository_sync=[{...target,exact_ref_head_only:true}];caps.hosts=[{host:target.host,writes:state.hostEnabled,orchestrate:state.hostEnabled,observe:true}];
      caps.actions.push({action:'repository.continue',allowed:fixture.base.state.start});return response;
    }
    if(path===`/pm/workspaces/${workspaceId}/lifecycle`&&state.lifecycleFailed)return {status:503,data:{error:{code:'UNAVAILABLE',message:'Lifecycle unavailable'}}};
    if(path.startsWith(`/pm/workspaces/${workspaceId}/work-packages`)||path.startsWith('/projects/')||path==='/repository-previews'){
      state.reads.push(structuredClone(input));if(state.deny)return {status:404,data:{error:{code:'PM_PACKAGE_SOURCE_UNAVAILABLE',message:'Original package unavailable'}}};
      const selected=fixture.state.packages.find(row=>path.split('/').includes(row.package_id));
      if(path.endsWith('/readiness'))return {status:200,data:{version:1,workspace_id:workspaceId,package_id:selected.package_id,document_digest:selected.document_digest,
        ready_for:'engineering_handoff_review',execution_available:false,execution_authorized:false,dispatch_readiness:'separate_fixed_handoff_admission',status:'blocked',
        handoff:{...metadata,compatibility:'unverified'},checks:[{check:'scope',status:state.blocked==='scope'?'blocked':'passed'},
          {check:'work',status:state.blocked==='work'?'blocked':'passed'},{check:'package_revision',status:selected.package_id===fixture.state.packages.at(-1).package_id?'passed':'blocked'},
          {check:'spec_code_alignment',status:'blocked',assessment:'unknown'},...(state.blocked&& !['scope','work'].includes(state.blocked)?[{check:state.blocked,status:'blocked'}]:[])]}};
      if(path===`/projects/${projectId}`)return {status:200,data:{project:{project_id:projectId,name:'Library recovery',version:state.version,repositories:[target.repository],archived:false}}};
      if(path==='/repository-previews'){
        const data={preview:{target,source_ref:'refs/heads/main',source_sha:state.codeChanged?'e'.repeat(40):codeSha,repository_id:7,binding_digest:'b'.repeat(64),exact_ref_head_only:true,
          preconditions:{repository_id:7,binding_digest:'b'.repeat(64)},workspace:{workspace_id:target.workspace_id,name:'Recovery workspace',folder:'/mock/workspace'}}};
        if(state.previewGate)await state.previewGate;return {status:200,data};
      }
    }
    if(path.startsWith('/operations/')&&input.method==='GET'){
      const op=[...state.ops.values()].find(op=>path.endsWith(op.operation_id));if(op)return state.deny?{status:404,data:{error:{code:'NOT_FOUND',message:'Original operation unavailable'}}}:{status:200,data:{operation:op}};
    }
    if(path==='/operations'&&input.method==='POST'&&input.body.action==='repository.continue'){
      state.writes.push(structuredClone(input));
      if(state.reject)return {status:403,data:{error:{code:'FORBIDDEN',message:'Original source withdrawn',admission_refused:true}}};
      let op=state.ops.get(input.idempotency_key);
      if(!op){
        if(input.body.preconditions.expected_project_version!==state.version)return {status:409,data:{error:{code:'VERSION_CONFLICT',message:'Project version changed',admission_refused:true}}};
        if(fixture.state.stage!=='development')return {status:409,data:{error:{code:'PM_LIFECYCLE_STOPPED',message:'No new work',admission_refused:true}}};
        op={...input.body,actor:fixture.base.state.actor,idempotency_key:input.idempotency_key,operation_id:'op_'+String(state.ops.size+1).padStart(32,'0'),status:'succeeded',
          result:{...target,source_ref:'refs/heads/main',source_sha:codeSha,repository_id:7,binding_digest:'b'.repeat(64),session_id:'managed-session'}};state.ops.set(input.idempotency_key,op);
      }
      if(state.writeGate)await state.writeGate;
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status:200,data:{operation:op}};
    }
    return fixture.dispatch(input);
  };
  return {state,fixture,dispatch,row};
}
