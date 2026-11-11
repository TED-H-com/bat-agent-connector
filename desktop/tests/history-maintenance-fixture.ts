import {fixture} from './external-history-links-fixture.ts';
import {workspaceId,workId} from './pm-workbench-fixture.ts';

export const attemptId='att_'+'7'.repeat(32);
export function maintenanceFixture() {
  const f=fixture(), dispatch=f.dispatch;
  f.base.base.state.role='contributor';
  const state={hiddenSource:false,linkDenied:false,holdLink:null as Promise<void>|null,linkTransform:null as ((data:any)=>void)|null,
    attempts:false,retired:new Map<string,any>(),events:[] as string[],pending:false};
  f.state.links=[f.link()];
  f.dispatch=async input=>{
    const url=new URL(input.path,'http://fixture'),path=url.pathname,endpoint=`/pm/workspaces/${workspaceId}`;
    if(path===endpoint+'/history' && state.hiddenSource) return {status:200,data:{workspace_id:workspaceId,rows:[]}};
    if(path.startsWith(endpoint+'/external-links/')) {
      f.state.reads.push(input.path);
      const id=path.split('/').at(-1)!,link=f.state.links.find(row=>row.link_id===id)||state.retired.get(id);
      if(state.linkDenied || f.state.deny || !link) return {status:404,data:{error:{code:'PM_HISTORY_SOURCE_UNAVAILABLE',message:'Relationship unavailable'}}};
      const data={link:structuredClone(link)}; state.linkTransform?.(data); if(state.holdLink) await state.holdLink;
      return {status:200,data};
    }
    if(path.endsWith('/attempts') || path.includes('/work-attempts/')) {
      const row={attempt_id:attemptId,workspace_id:workspaceId,work_item_id:workId(2),sequence:1,created_at:1791705600,stage:'started'};
      return {status:200,data:path.endsWith('/attempts')?{workspace_id:workspaceId,work_item_id:workId(2),attempts:[row],next_cursor:null,visibility_filtered:false}:{workspace_id:workspaceId,attempt:row}};
    }
    if(path==='/operations' && input.method==='POST' && input.body.action.startsWith('pm.history.')) {
      f.state.writes.push(structuredClone(input)); let op=f.state.ops.get(input.idempotency_key);
      if(!op) {
        const {action,target,params,preconditions}=input.body,unlink=action==='pm.history.unlink';
        const oldId=unlink?target.link_id:params.replaces_link_id,old=f.state.links.find(row=>row.link_id===oldId);
        const failed=f.state.stale || Boolean(oldId && (!old || old.version!==(unlink?preconditions.expected_link_version:params.expected_old_link_version)));
        let result=null;
        const opId='op_'+String(f.state.ops.size+1).padStart(32,'0');
        if(!failed) {
          const found=!unlink && f.state.links.find(row=>row.work_item_id===target.work_item_id && row.attempt_id===(params.attempt_id??null) && JSON.stringify(row.fixed_ref)===JSON.stringify(params.fixed_ref));
          const next=found||{...f.link(),link_id:'hxl_'+String(f.state.ops.size+1).padStart(32,'0'),work_item_id:target.work_item_id,attempt_id:params.attempt_id??null,fixed_ref:params.fixed_ref,created_by:f.base.base.state.actor};
          if(old && (unlink || next.link_id!==old.link_id)) {f.state.links=f.state.links.filter(row=>row.link_id!==oldId);state.retired.set(oldId,{...old,state:'withdrawn',version:old.version+1});state.events.push(unlink?'withdrawn':'replaced');}
          if(!unlink && !found) {f.state.links.push(structuredClone(next));state.events.push('created');}
          result=unlink?{link_id:oldId,state:'withdrawn',version:old.version+1}:{link_id:next.link_id,state:'active',version:next.version};
        }
        op={...structuredClone(input.body),actor:f.base.base.state.actor,idempotency_key:input.idempotency_key,operation_id:opId,
          status:failed?'failed':'succeeded',result,...(failed?{error:{code:'PM_HISTORY_LINK_CHANGED',message:'Relationship changed'}}:{})};
        f.state.ops.set(input.idempotency_key,op);
      }
      if(f.state.lost) {f.state.lost=false;return {status:503,data:{error:{code:'LOST_REPLY',message:'Reply unavailable'}}};}
      return {status:200,data:{operation:{...op,status:state.pending?'running':op.status}}};
    }
    if(path.startsWith('/operations/')) {
      const op=[...f.state.ops.values()].find(row=>path.endsWith(row.operation_id));
      if(op) return {status:200,data:{operation:{...op,status:state.pending?'running':op.status}}};
    }
    const result=await dispatch(input);
    if(state.attempts && (path==='/bootstrap'||path==='/capabilities')) {
      const caps=path==='/bootstrap'?result.data.capabilities:result.data;caps.features.pm_workspaces.work_start={version:1};
    }
    return result;
  };
  return Object.assign(f,{maintenance:state});
}
