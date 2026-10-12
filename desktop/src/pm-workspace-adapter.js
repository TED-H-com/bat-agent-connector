const ACTION='pm.workspace.adapter.select';
export const workspaceAdapter = capability => capability?.workspace_adapter?.version===1 ? capability.workspace_adapter : null;
export const workspaceAdapterAllows = (capability, adapter) => {
  if(capability?.workspace_adapter && capability.workspace_adapter.version!==1)return false;
  const saved=workspaceAdapter(capability);
  return !saved || saved.reason!=='original_sources_unavailable' && (!saved.choice || saved.choice.adapter===adapter);
};

// This is a saved workspace decision, not a runtime launch or another scheduler.
export function workspaceAdapterChoice({h,t,form,input,labelled,caps,capability,permitted,target,refresh,onStateChange}) {
  const saved=()=>workspaceAdapter(capability()),advertised=()=>caps()?.features?.pm_workspaces?.workspace_adapter?.version===1;
  const adapter=h('select',{'aria-label':t('pm_choice_adapter'),required:true},
    h('option',{value:''},t('pm_choice_select')),h('option',{value:'codex-native-linux'},t('pm_analysis_local')),
    h('option',{value:'bat-pm-linux-v1'},t('pm_analysis_bat')));
  const rationale=input('pm_choice_reason',{multiline:true,max:4000});
  const summary=h('p',{class:'pm-source','data-pm-choice-current':''}),reason=h('p',{class:'muted'});
  const request=form({action:ACTION,target:{workspace_id:target.workspace_id},fields:{adapter,rationale},
    params:values=>({adapter:values.adapter,rationale:values.rationale}),preconditions:()=>saved().preconditions,
    permitted:()=>advertised()&&permitted(true)&&saved()?.available===true&&Boolean(saved()?.preconditions),
    recoverPermitted:()=>permitted(true),label:'pm_choice_save',onSaved:refresh,onStateChange,
    extra:h('div',{class:'pm-fields'},labelled('pm_choice_adapter',adapter),labelled('pm_choice_reason',rationale))});
  const box=h('details',{'data-pm-workspace-adapter':''},h('summary',{},t('pm_choice_title')),summary,
    h('p',{class:'muted'},t('pm_choice_help')),reason,request.box);
  function update(){
    const current=saved();
    box.hidden=(!advertised()&&!request.locked())||!current||current.reason==='original_sources_unavailable';
    summary.replaceChildren();
    if(current?.choice){const choice=current.choice;
      summary.append(t(choice.adapter==='bat-pm-linux-v1'?'pm_analysis_bat':'pm_analysis_local'),' · ',
        t('pm_choice_revision')+' '+choice.version,' · ',h('a',{href:`#/op/${encodeURIComponent(choice.operation_id)}`},t('pm_operation')));
    }else summary.textContent=t('pm_choice_unset');
    reason.textContent=current?.reason==='original_writer_unresolved'?t('pm_choice_busy')
      :current?.available===false?t('pm_choice_owner'):'';
    reason.hidden=!reason.textContent;
    request.update();
  }
  return {box,update,locked:request.locked};
}
