import {eligibilityReasons} from './pm-availability.js';

export const hasEffectivePmRoute=document=>document!=null&&Object.hasOwn(document,'effective_pm_route');
export const effectivePmRoute=document=>document?.effective_pm_route?.version===1
  &&document.effective_pm_route.scope==='workspace_selected_route'?document.effective_pm_route:null;
export function workspacePmReady(document){
  if(hasEffectivePmRoute(document))return effectivePmRoute(document)?.ready===true;
  // Version-gated compatibility only: older servers have no effective-route facet.
  return Boolean(document?.structured_analysis?.version===1
    ?(document.structured_analysis.actions?.['pm.conversation.propose']?.adapters||[]).some(row=>['codex-native-linux','bat-pm-linux-v1'].includes(row.adapter)
      &&row.action_registered===true&&row.authorized===true&&row.eligible_for_preflight===true)
    :document?.native_analysis?.authorized!==false&&document?.native_analysis?.eligible_for_preflight===true
      ||document?.bat_analysis?.authorized!==false&&document?.bat_analysis?.eligible_for_preflight===true);
}
const routeReasons={scope_required:'agents_scope',authorization_required:'agents_authorization',original_sources_unavailable:'pm_effective_sources'};
export const pmRouteReasonKey=code=>Object.hasOwn(routeReasons,code)?routeReasons[code]
  :Object.hasOwn(eligibilityReasons,code)?eligibilityReasons[code]:'agents_unknown';
const safeCode=value=>typeof value==='string'&&/^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(value)?value:'';
const adapterKey=adapter=>adapter==='codex-native-linux'?'pm_analysis_local':adapter==='bat-pm-linux-v1'?'pm_analysis_bat':null;

// All readiness and reasons come from the versioned server facet. This renderer
// adds labels/next steps; it does not select a route or authorize a request.
export function renderEffectivePmRoute(node,{h,t,document,readyKey='pm_effective_ready'}){
  const facet=effectivePmRoute(document);
  node.dataset.pmEffectiveReady=String(facet?.ready===true);
  if(!facet){node.replaceChildren(h('p',{},t('pm_effective_unknown')));return node;}
  const label=adapter=>h('span',{},t(adapterKey(adapter)||'pm_effective_unknown'),' · ',h('code',{},safeCode(adapter)));
  const parts=[h('p',{},t(facet.choice_source==='saved'?'pm_effective_saved':facet.choice_source==='unset'?'pm_effective_unset':'pm_effective_unreadable'),
    ...(facet.adapter?[label(facet.adapter)]:[]))];
  const ref=facet.choice_ref;
  if(ref&&Number.isSafeInteger(ref.version)&&ref.version>0){
    const link=typeof ref.operation_id==='string'&&/^op_[0-9a-f]{32}$/.test(ref.operation_id)
      ?h('a',{href:`#/op/${ref.operation_id}`},t('pm_operation')):null;
    parts.push(h('p',{},t('pm_choice_revision')+' '+ref.version,' · ',link));
  }
  parts.push(h('p',{},t(facet.ready===true?readyKey:'pm_effective_blocked')));
  if(facet.choice_source==='unset'&&facet.ready===true)
    parts.push(h('p',{},t('pm_effective_available'),' ',...(facet.eligible_adapters||[]).map(label)));
  if(facet.ready!==true){
    const aggregate=facet.choice_source==='unset'&&facet.reason==='no_eligible_adapter';
    if(aggregate)parts.push(h('code',{},facet.reason));
    const reasons=aggregate?facet.adapters||[]:[{reason:facet.reason}];
    for(const row of reasons)parts.push(h('p',{},...(row.adapter?[label(row.adapter),' · ']:[]),
      t(pmRouteReasonKey(row.reason)),' · ',h('code',{},safeCode(row.reason))));
  }
  parts.push(h('p',{class:'muted'},t('pm_effective_boundary')));
  node.replaceChildren(...parts);return node;
}
