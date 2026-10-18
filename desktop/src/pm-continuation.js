import {disclosure, button} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
import {structuredAnalysis} from './pm-structured-analysis.js';

const ACTION = 'pm.continuation.prepare';
// The existing run owns the version, adapter and recovery. Refresh only reads.
export function continuationBrief({h,t,form,input,labelled,caps,capability,thread,permitted,target,refresh,onOpen}) {
  let head = null, ready = false, signature = '', request, status;
  const source = () => thread()?.continuation_brief;
  const usable = () => source()?.available === true && source()?.document && source()?.validation?.status === 'format_valid';
  const authorized = () => permitted(false) && thread()?.capabilities?.write === true && caps()?.scopes?.includes('start');
  const adapter = structuredAnalysis({h,t,labelled,action:ACTION,capability,legacyEligible:()=>false,authorized,readable:()=>Boolean(thread())});
  const purpose = input('pcb_purpose', {multiline:true,max:4000,value:t('pcb_default_purpose')});
  const previous = h('input', {type:'hidden',value:'', 'data-pm-default':''});
  request = form({action:ACTION,target,fields:{purpose,adapter:adapter.field,previous},
    params: values => ({purpose:values.purpose,...adapter.params(values),max_chars:64000,
      ...(values.previous ? {previous_brief_ref:JSON.parse(values.previous)} : {})}),
    preconditions:()=>({expected_version:thread().thread.version,expected_plan_version:head.version,expected_accepted_revision_id:head.accepted_revision_id}),
    permitted:()=>authorized() && adapter.eligible() && Boolean(head),recoverPermitted:authorized,
    label:'pcb_prepare',onSaved:refresh,onStateChange:()=>{adapter.update(request);if(ready) render();},
    extra:h('div',{class:'pm-fields'},labelled('pcb_purpose',purpose),adapter.box,previous)});
  const reader = h('section',{'data-continuation-brief':'',hidden:true});
  const tools = h('section',{'data-continuation-tools':'',hidden:true},h('h3',{},t('pcb_title')),
    h('p',{class:'muted'},t('pcb_help')),request.box);
  function links(row, saved) {
    return h('span',{class:'pm-source'},...(row.source_refs || []).flatMap((key,index)=>{
      const ref=saved.source_references?.[key];
      return ref?.operation_id ? [index ? ' · ' : ' ',h('a',{href:'#/op/'+encodeURIComponent(ref.operation_id)},t('pd_source_record')+' '+(index+1))] : [];
    }));
  }
  function render() {
    const saved=source();
    tools.hidden=!thread() || !capability()?.structured_analysis?.actions?.[ACTION] && !request.locked();
    reader.hidden=!thread() || !saved;
    const stale=saved?.thread_version!==thread()?.thread.version || head && (saved?.plan_head?.version!==head.version || saved?.plan_head?.accepted_revision_id!==head.accepted_revision_id);
    const statusText=()=>t(!usable()?'pcb_needs_review':stale?'pcb_stale':'pcb_status_'+saved.document.status);
    if(status)status.textContent=statusText();
    const next=JSON.stringify(saved);
    if(signature===next)return;
    const open=reader.querySelector(':scope > details')?.open;
    signature=next;reader.replaceChildren();status=null;
    if(!saved || !thread())return;
    if(saved.available!==true){reader.append(h('p',{class:'muted'},t('pcb_unavailable')));return;}
    const doc=usable()?saved.document:null;
    const details=h('div',{});
    const rows=(values)=>h('ul',{},...values.map(row=>h('li',{},row.text,links(row,saved))));
    const meta=h('p',{class:'pm-source'},timeStamp(saved.as_of),' · ',h('a',{href:'#/op/'+encodeURIComponent(saved.operation_id)},t('pm_operation')));
    if(doc){
      if(doc.goal)details.append(h('h4',{},t('pcb_goal')),h('p',{},doc.goal.text,links(doc.goal,saved)));
      for(const name of ['adopted_decisions','candidates','rejected_directions','completed_evidence','open_questions','remaining_work']){
        if(doc[name]?.length)details.append(h('h4',{},t('pcb_'+name)),rows(doc[name]));
      }
    }
    details.append(meta,h('p',{class:'muted'},t('pcb_interpretation')),
      h('p',{class:'muted'},t(saved.coverage?.partial?'pcb_partial':'pcb_coverage')));
    status=h('p',{class:'muted'},statusText());
    const body=h('div',{},status,
      doc?.next_steps?.length?rows(doc.next_steps):'',disclosure({label:t('pd_details'),body:details}),
      button({size:'sm',variant:'ghost',label:t('pcb_update'),onClick:onOpen}));
    const panel=disclosure({label:t('pcb_title'),body});panel.open=Boolean(open);reader.append(panel);
  }
  ready=true;
  return {reader,tools,update(nextHead){
    if(nextHead!==undefined)head=nextHead;
    if(!request.locked()){
      const value=usable()?JSON.stringify({run_id:source().run_id,document_digest:source().document_digest}):'';
      // Metadata follows the selected immutable brief, never a new operation key.
      if(request.values().previous!==value)request.set('previous',value);
    }
    adapter.update(request);render();request.update();
  },recheck(){request.recheck();}};
}
