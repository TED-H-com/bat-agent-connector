import {pmFixture,workspaceId} from './pm-fixture.ts';
export {workspaceId};
export const releasePath=`/pm/workspaces/${workspaceId}`,releaseRoute=`/dashboard/#/pm/${workspaceId}`;
export const bindingId='pmpb_'+'4'.repeat(32),repositoryName='example/library',commit='a'.repeat(40),deploymentId='dep_'+'d'.repeat(32);
const stamp=Date.parse('2026-10-11T12:00:00Z')/1000;
export function releaseFixture(){
  const base=pmFixture();base.state.packages=false;
  const state={enabled:true,discovery:true,rejectWrite:false,reads:[] as string[],writes:[] as any[],ops:new Map<string,any>(),baselines:[] as any[],version:0,hidden:false,
    fail:null as null|number,lost:false,deferPreview:null as null|Promise<void>,deferCurrent:null as null|Promise<void>,deferWrite:null as null|Promise<void>,
    emptyBindingPage:false,emptySourcePage:false,emptyHistoryPage:false,previewMismatch:false};
  const proof=(kind='github_release',branch:string|null=null)=>({source:kind==='github_release'?{kind,release_id:41,tag:'v1.4.0',published_at:stamp-3600,
    publication_status:'published',resolved_commit_sha:commit,draft:false,prerelease:false}:{kind,deployment_id:deploymentId,operation_id:'op_'+'d'.repeat(32),
      checked_at:stamp,commit_sha:commit,channel:'staging',version_checked:true,current_environment_status:'not_rechecked',publication_status:'deployed'},
    maintenance_branch:branch?{status:'verified_at_observation',ref:'refs/heads/'+branch,head_sha:'b'.repeat(40)}:{status:'unconfigured'},
    observed_at:stamp,intent_digest:'e'.repeat(64),observation_scope:'fixed_original_source_at_observation',
    selection:{repository_id:7,commit_sha:commit,channel:kind==='github_release'?'stable':'staging'}});
  const registration=(kind='github_release',branch:string|null=null)=>({action:'pm.release_baseline.register',target:{workspace_id:workspaceId,repository:repositoryName},
    params:{project_binding_id:bindingId,source:kind==='github_release'?{kind,release_id:41}:{kind,deployment_id:deploymentId},channel:kind==='github_release'?'stable':'staging',
      maintenance_branch:branch,known_limitations:[],rationale:''},preconditions:{expected_baseline_version:state.version,
      expected_previous_baseline_id:state.baselines.at(-1)?.baseline_id||null,expected_repository_id:7,expected_commit_sha:commit}});
  function addBaseline(kind='github_release',request:any=registration(kind)){
    const version=++state.version,baseline_id='pmrb_'+String(version).padStart(32,'0'),operation_id='op_'+String(version).padStart(32,'0');
    const row={sequence:version,baseline_id,workspace_id:workspaceId,version,previous_baseline_id:state.baselines.at(-1)?.baseline_id||null,
      document_digest:'c'.repeat(64),actor:base.state.actor,created_at:stamp,operation_id,
      document:{workspace_id:workspaceId,baseline_id,version,repository:repositoryName,repository_id:7,channel:request.params.channel,commit_sha:commit,
        release_version:{kind:kind==='github_release'?'tag':'commit',value:kind==='github_release'?'v1.4.0':commit},proof:proof(kind,request.params.maintenance_branch),
        intent:{rationale:request.params.rationale},delivery_status:{github_release:kind==='github_release'?'published':'not_assessed',package_registry:'not_assessed',deployment:kind==='deployment'?'verified':'not_assessed'},
        quality:{status:'unassessed',known_limitations:request.params.known_limitations.map(text=>({text,status:'owner_claim'}))},maintenance_authorized:false}};
    state.baselines.push(row);return row;
  }
  const dispatch=async(input:any)=>{
    const url=new URL(input.path,'http://fixture'),path=url.pathname;
    if(path==='/capabilities'||path==='/bootstrap'){
      const response=await base.dispatch(input),caps=path==='/bootstrap'?response.data.capabilities:response.data;
      if(state.enabled)caps.features.pm_workspaces.release_baselines={version:1,source_choices:state.discovery,source_preview:state.discovery};
      caps.actions.push({action:'pm.release_baseline.register',allowed:base.state.manage});return response;
    }
    if(path.startsWith(releasePath+'/release-')){
      state.reads.push(input.path);
      if(state.fail)return {status:state.fail,data:{error:{code:'FORBIDDEN',message:'Release source unavailable'}}};
      if(path.endsWith('/release-baselines/current')){
        const data={workspace_id:workspaceId,head:{version:state.version,baseline_id:state.hidden?null:state.baselines.at(-1)?.baseline_id||null},
          baseline:state.hidden?null:state.baselines.at(-1)||null,visibility_filtered:state.hidden,maintenance_available:false};
        const result=structuredClone(data);if(state.deferCurrent)await state.deferCurrent;return {status:200,data:result};
      }
      if(path.endsWith('/release-baselines')){
        const first=state.emptyHistoryPage&&url.searchParams.get('after')==='0';
        return {status:200,data:{workspace_id:workspaceId,baselines:first?[]:structuredClone(state.baselines),next_cursor:first?10:null,visibility_filtered:first}};
      }
      if(path.endsWith('/release-baseline-sources')){
        const first=state.emptyBindingPage&&url.searchParams.get('after')==='0';return {status:200,data:{workspace_id:workspaceId,
          bindings:first?[]:[{sequence:11,project_binding_id:bindingId,project_id:'project-1',project_name:'Library',repositories:[repositoryName]}],next_cursor:first?10:null}};
      }
      if(path.includes('/release-baseline-sources/')){
        const kind=path.endsWith('github-releases')?'github_release':'deployment',first=state.emptySourcePage&&(url.searchParams.get('page')==='1'||url.searchParams.get('after')==='0');
        return {status:200,data:{workspace_id:workspaceId,project_binding_id:bindingId,repository:repositoryName,repository_id:7,
          ...(kind==='github_release'?{releases:first?[]:[{release_id:41,tag:'v1.4.0',channel:'stable',published_at:stamp-3600}],next_page:first?2:null,
            coverage:{publication:'github_release_only',package_registry:'not_assessed'}}:{deployments:first?[]:[{sequence:9,deployment_id:deploymentId,operation_id:'op_'+'d'.repeat(32),repository_id:7,
              channel:'staging',commit_sha:commit,version:{kind:'commit',value:commit},checked_at:stamp}],next_cursor:first?8:null,visibility_filtered:first})}};
      }
      if(path.endsWith('/release-baseline-preview')){
        const kind=url.searchParams.get('kind')!,branch=url.searchParams.get('maintenance_branch');
        const data={workspace_id:workspaceId,proof:proof(kind,branch),registration:registration(kind,branch),requires_input:['rationale'],maintenance_authorized:false};
        if(state.previewMismatch)data.registration.preconditions.expected_commit_sha='f'.repeat(40);
        if(state.deferPreview)await state.deferPreview;return {status:200,data};
      }
      throw Error('Unexpected release path '+path);
    }
    if(path==='/operations'&&input.method==='POST'&&input.body.action==='pm.release_baseline.register'){
      state.writes.push(structuredClone(input));
      if(state.rejectWrite)return {status:403,data:{error:{code:'FORBIDDEN',message:'Source withdrawn',admission_refused:true}}};
      let op=state.ops.get(input.idempotency_key);
      if(!op){
        if(input.body.preconditions.expected_baseline_version!==state.version)return {status:409,data:{error:{code:'VERSION_CONFLICT',message:'Baseline changed',admission_refused:true}}};
        const row=addBaseline(input.body.params.source.kind,input.body);
        op={...input.body,actor:base.state.actor,idempotency_key:input.idempotency_key,operation_id:row.operation_id,status:'succeeded',
          result:{workspace_id:workspaceId,baseline_id:row.baseline_id,version:row.version,maintenance_authorized:false}};state.ops.set(input.idempotency_key,op);
      }
      if(state.deferWrite)await state.deferWrite;
      if(state.lost){state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status:200,data:{operation:op}};
    }
    if(path.startsWith('/operations/')&&state.ops.size){const op=[...state.ops.values()].find(op=>path.endsWith(op.operation_id));if(op)return {status:200,data:{operation:op}};}
    return base.dispatch(input);
  };
  return {state,base,dispatch,addBaseline,registration};
}
