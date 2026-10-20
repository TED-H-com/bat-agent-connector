// One adapter field in the existing durable intent; never switch a saved request.
import {workspaceAdapter,workspaceAdapterAllows} from './pm-workspace-adapter.js';
import {effectivePmRoute,renderEffectivePmRoute} from './pm-effective-route.js';
const NATIVE='codex-native-linux',BAT='bat-pm-linux-v1';
export function structuredAnalysis({h,t,labelled,action,capability,legacyEligible,authorized,readable=()=>true}) {
  const field=h('select',{'aria-label':t('pm_analysis_adapter'),'data-pm-default':NATIVE},
    h('option',{value:NATIVE},t('pm_analysis_local')),h('option',{value:BAT},t('pm_analysis_bat')));
  field.value=NATIVE;
  const choice=labelled('pm_analysis_adapter',field),reason=h('div',{class:'muted','data-structured-analysis-reason':''}),binding=h('p',{class:'muted pm-note-text'});
  const box=h('div',{'data-pm-structured-analysis':''},choice,reason,binding);
  let activeRequest=null;
  field.addEventListener('change',()=>activeRequest?.update());
  const supported=()=>capability()?.structured_analysis?.version===1;
  const descriptor=()=>{const rows=supported()?capability().structured_analysis.actions?.[action]?.adapters:null;
    return Array.isArray(rows)?rows.find(row=>row?.adapter===field.value):null;};
  const eligible=()=>workspaceAdapterAllows(capability(),field.value)&&(supported()?Boolean([NATIVE,BAT].includes(field.value)&&descriptor()?.action_registered===true
    &&descriptor()?.authorized===true&&descriptor()?.eligible_for_preflight===true):field.value===NATIVE&&legacyEligible());
  function update(request){
    activeRequest=request;
    const fixed=workspaceAdapter(capability())?.choice?.adapter;
    if(supported()&&fixed&&!request?.locked()&&field.value!==fixed){request?.set('adapter',fixed);request?.update();}
    box.hidden=!readable();choice.hidden=!supported()&&field.value===NATIVE;
    // Unavailable native must still allow an explicit choice of an eligible BAT adapter.
    field.disabled=Boolean(request?.locked())||Boolean(fixed)||!authorized();
    const selected=descriptor(),legacy=action==='pm.conversation.propose'?capability()?.native_analysis:capability()?.follow_up_analysis;
    const code=selected?.reason||(!supported()&&field.value===NATIVE?legacy?.reason:null);
    const key=!authorized()||selected?.authorized===false?'pm_native_permissions':eligible()?(field.value===BAT?'pm_analysis_bat_preflight':'pm_native_preflight_pending')
      :field.value===BAT?({PM_WORKTREE_REQUIRED:'pm_analysis_bat_worktree',REPOSITORY_BINDING_CHANGED:'pm_analysis_bat_binding_changed',HOST_READ_ONLY:'pm_analysis_bat_readonly'}[code]||'pm_analysis_bat_unavailable')
      :({PM_NATIVE_PLATFORM_UNSUPPORTED:'pm_native_platform',PM_NATIVE_EXECUTABLE_UNAVAILABLE:'pm_native_install',PM_NATIVE_MANAGED_CONFIG_UNSUPPORTED:'pm_native_managed',PM_NATIVE_AUTH_UNAVAILABLE:'pm_native_auth',PM_NATIVE_HOME_UNAVAILABLE:'pm_native_auth'}[code]||'pm_native_unavailable');
    // The request gate above remains unchanged. A blocked workspace facet also
    // explains unreadable choices, which a selected descriptor cannot explain.
    if(effectivePmRoute(capability())?.ready===false)renderEffectivePmRoute(reason,{h,t,document:capability()});
    else reason.replaceChildren(t(key));
    const source=field.value===BAT&&selected?.selected_binding;
    binding.hidden=!source;binding.textContent=source?`${t('pm_analysis_selected_binding')} ${source.host} / ${source.workspace_id} / ${source.repository}`:'';
  }
  return {field,box,eligible,update,params:values=>supported()?{adapter:values.adapter}:{}};
}
