import {button, disclosure, iconButton, skeleton} from './ui/components.js';
import {timeText} from './ui/stamps.js';
// The saved project verifier is selected here. No client command or Task is created.
const ACTION='pm.execution.verify',sha=/^[0-9a-f]{40}$/,digest=/^[0-9a-f]{64}$/,operation=/^op_[0-9a-f]{32}$/;
export function workspaceExecutionVerification({h,t,api,alive,workspaceId,workspace,caps,form,labelled,errorBox,namespace,onSaved,onConfiguration=()=>{},onUnavailable=()=>{}}){
  let sources=[],selected='',generation=0,ready=false,configuration=null,editor=null;
  const navigation=`batc.pm.verifier.${namespace}.${workspaceId}`;try{const value=localStorage.getItem(navigation);if(operation.test(value||''))selected=value;}catch{}
  const picker=h('select',{'aria-label':t('pmq_verify_execution')}),status=h('div',{role:'status'}),factBody=h('div'),holder=h('div'),notes=h('div');
  const facts=h('details',{},h('summary',{},t('pmq_details')),factBody);
  disclosure({element:facts});
  const element=h('details',{'data-pm-execution-verification':''},h('summary',{},t('pmq_verify_heading')),
    h('p',{class:'muted'},t('pmq_verify_help')),labelled('pmq_verify_execution',picker),
    h('div',{class:'actions'},iconButton({icon:'refresh-cw', label:t('pmq_verify_refresh'), ...{type:'button',onclick:()=>void load()}})),status,notes,facts,holder);
  const stampOf=row=>['created_at','updated_at','finished_at'].map(key=>row?.[key]).find(value=>Number.isFinite(value));
  const optionLabel=row=>{const at=stampOf(row);return at===undefined?String(row.actor||''):`${row.actor} · ${timeText(at)}`;};
  const valid=ticket=>{try{alive();return ticket===generation;}catch{return false;}};
  const writer=()=>ready&&caps()?.scopes?.includes('observe')&&caps()?.scopes?.includes('operate')
    &&['owner','contributor'].includes(workspace()?.members?.find(row=>row.actor===caps()?.actor)?.role||workspace()?.membership?.role);
  const enabled=()=>caps()?.features?.pm_workspaces?.execution_verification?.version===1;
  const line=(key,value)=>h('p',{class:'pm-source'},h('strong',{},t(key)+': '),value);
  function clear(){ready=false;configuration=null;editor?.dispose();editor=null;factBody.replaceChildren();notes.replaceChildren();holder.replaceChildren();onConfiguration(null);}
  function withdraw(){generation++;clear();sources=[];picker.replaceChildren();status.replaceChildren();}
  async function load(){
    const ticket=++generation;clear();if(!selected||!sources.some(row=>row.operation_id===selected))return;
    status.replaceChildren(skeleton(2));
    try{
      const value=await api('GET',`/pm/workspaces/${encodeURIComponent(workspaceId)}/executions/${selected}/verification-config`);if(!valid(ticket))return;
      if(value.workspace_id!==workspaceId||value.execution_operation_id!==selected||value.profile!=='observed_runner_v1'||!digest.test(value.handoff_contract_digest)||typeof value.available!=='boolean'
        ||value.available&&!digest.test(value.command_digest)||value.candidate_commit!==null&&!sha.test(value.candidate_commit)||value.tree_hash!==null&&!sha.test(value.tree_hash))throw Error(t('pmq_invalid'));
      configuration=value;ready=true;status.replaceChildren();onConfiguration(value);
      factBody.append(line('pmq_execution',selected));if(value.command_digest)factBody.append(line('pmq_verify_command',value.command_digest));
      if(value.candidate_commit&&value.tree_hash)factBody.append(line('pmq_candidate',value.candidate_commit),line('pmq_verify_tree',value.tree_hash));
      else notes.append(h('p',{class:'muted'},t('pmq_verify_no_candidate')));
      if(!value.available)notes.append(h('p',{class:'muted'},t('pmq_verify_unconfigured')));
      const fields=Object.fromEntries(['candidate_commit','tree_hash','command_digest','handoff_contract_digest'].map(key=>[key,h('input',{type:'hidden',value:value[key]||''})]));
      const matches=()=>configuration&&Object.entries(fields).every(([key,field])=>field.value===configuration[key]);
      const admissible=()=>writer()&&configuration?.available&&sha.test(configuration.candidate_commit)&&sha.test(configuration.tree_hash)&&matches();
      const rebase=button({variant:'secondary',label:t('pmq_verify_use_config'),onClick:()=>{if(editor&&!editor.locked()&&configuration){for(const key of Object.keys(fields))editor.set(key,configuration[key]||'');editor.update();}}});
      editor=form({namespace:`${namespace}.verification`,api,requiredScope:'operate',action:ACTION,target:{workspace_id:workspaceId,execution_operation_id:selected},fields,
        params:values=>{if(!admissible())throw Error(t('pmq_verify_changed'));return {candidate_commit:values.candidate_commit,tree_hash:values.tree_hash,command_digest:values.command_digest};},
        preconditions:()=>({handoff_contract_digest:fields.handoff_contract_digest.value}),permitted:admissible,recoverPermitted:writer,label:'pmq_verify_run',onSaved,
        onStateChange:()=>{rebase.hidden=matches();rebase.disabled=!writer()||!!editor?.locked();},extra:h('div',{},h('p',{class:'muted'},t('pmq_verify_effect')),h('div',{class:'actions'},rebase))});
      holder.replaceChildren(editor.box);editor.update();
    }catch(err){if(valid(ticket)){clear();status.replaceChildren(errorBox(err, {within:status}));if([401,403,404].includes(err.status))onUnavailable(err);}}
  }
  async function setSources(rows){
    generation++;clear();sources=rows;picker.replaceChildren(h('option',{value:''},t('pmq_choose')),...rows.map(row=>h('option',{value:row.operation_id},optionLabel(row))));
    if(selected&&!sources.some(row=>row.operation_id===selected))selected='';picker.value=selected;element.hidden=!enabled();if(enabled()&&selected)await load();
  }
  picker.addEventListener('change',()=>{selected=picker.value;try{localStorage.setItem(navigation,selected);}catch{}void load();});
  return {element,setSources,withdraw};
}
