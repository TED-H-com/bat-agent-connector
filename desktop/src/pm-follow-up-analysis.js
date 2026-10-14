import {button, disclosure, statusPill, inlineError} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
// Assessments quote one immutable intake. A valid response format is not verified advice.
export function followUpAssessments({h,t,api,alive,workspaceId,caps,errorBox,onAccessFailure=()=>{},pageSize=20}) {
  const actor=caps()?.actor, endpoint=`/pm/workspaces/${encodeURIComponent(workspaceId)}/follow-up-assessments`;
  const number=value=>Number.isSafeInteger(value)&&value>=0;
  const identifier=value=>/^pmfa_[0-9a-f]{32}$/.test(value||'');
  if(!number(pageSize)||pageSize<1||pageSize>100)throw Error('Invalid assessment page size');
  let serial=0,intake=null,rows=[],next=null,reading=false,selected='',retired=false;
  const selections=new Map(),status=h('div',{role:'status'}),list=h('div'),detail=h('div');
  const note=key=>h('p',{class:'muted'},t(key));
  const code=value=>h('code',{class:'mono pm-source'},String(value??'—'));
  const fold=(key,...children)=>disclosure({label:t(key),body:h('div',{},...children)});
  const current=ticket=>{alive();if(actor!==caps()?.actor){retired=true;throw Error(t('pm_fa_actor_changed'));}return !retired&&ticket===serial;};
  const check=data=>{if(data?.workspace_id!==workspaceId)throw Error(t('pm_fa_mismatch'));return data;};
  const date=value=>timeStamp(value);
  const more=h('button',{type:'button',class:'ui-button secondary ui-sm',hidden:true,onclick:()=>void loadMore()},t('pm_fa_more'));
  const element=h('section',{'data-pm-follow-up-assessments':'',class:'pm-fu-assessments'},
    h('h3',{},t('pm_fa_title')),status,list,more,detail);
  function clear() {
    ++serial;intake=null;rows=[];next=null;reading=false;selected='';status.replaceChildren();list.replaceChildren();detail.replaceChildren();more.hidden=true;
  }
  function fail(error) {
    ++serial;reading=false;rows=[];next=null;list.replaceChildren();detail.replaceChildren();more.hidden=true;
    status.replaceChildren([t('pm_fa_mismatch'),t('pm_fa_read_failed')].includes(error.message)?inlineError({error:error.message}):errorBox(error));
    onAccessFailure(error);
  }
  function valid(row) {
    if(!row||row.workspace_id!==workspaceId||!identifier(row.assessment_id)||!number(row.sequence)
      ||row.intake_id!==intake?.intake_id||row.intake_digest!==intake?.document_digest
      ||!['format_valid','needs_review'].includes(row.validation?.status)||!Array.isArray(row.validation.errors)
      ||row.validation.conclusions_verified!==false||row.conclusions_verified!==false
      ||row.accepted_plan_changed!==false||row.execution_authorized!==false
      ||row.assessment_basis!=='unadopted_agent_analysis')throw Error(t('pm_fa_mismatch'));
    return row;
  }
  function page(data,after) {
    check(data);if(!Array.isArray(data.assessments)||data.next_cursor!=null&&(!number(data.next_cursor)||data.next_cursor<=after))throw Error(t('pm_fa_mismatch'));
    let cursor=after;for(const row of data.assessments){valid(row);if(row.sequence<=cursor)throw Error(t('pm_fa_mismatch'));cursor=row.sequence;}
    return data;
  }
  const path=after=>`${endpoint}?intake_id=${encodeURIComponent(intake.intake_id)}&after=${after}&limit=${pageSize}`;
  function navigation() {
    list.replaceChildren(...rows.map(row=>h('p',{},h('button',{type:'button',class:'ui-button secondary ui-sm','aria-pressed':selected===row.assessment_id,
      onclick:()=>{selected=row.assessment_id;selections.set(intake.intake_id,selected);navigation();void read(selected);}},
      t('pm_fa_record',{sequence:row.sequence}), ' · ', date(row.created_at)),h('span',{class:'muted'},' · ',t('pm_fa_'+row.validation.status)))));
    more.hidden=next==null;more.disabled=reading;
    if(!rows.length)list.append(note(next==null?'pm_fa_empty':'pm_fa_filtered_page'));
  }
  function sourceValue(pointer) {
    if(typeof pointer!=='string'||!pointer.startsWith('/'))throw Error(t('pm_fa_mismatch'));
    let value=intake.document;
    for(const encoded of pointer.slice(1).split('/')){
      if(/~(?![01])/u.test(encoded))throw Error(t('pm_fa_mismatch'));
      const key=encoded.replaceAll('~1','/').replaceAll('~0','~');
      if(value===null||typeof value!=='object'||!Object.hasOwn(value,key))throw Error(t('pm_fa_mismatch'));
      value=value[key];
    }
    return value;
  }
  function render(row) {
    const refs=row.provenance?.source_references;
    if(typeof row.raw_reply!=='string'||!refs||typeof refs!=='object'||Array.isArray(refs)
      ||row.provenance.intake_id!==intake.intake_id||row.provenance.intake_digest!==intake.document_digest
      ||row.provenance.run_id!==row.run_id||row.provenance.context_digest!==row.context_digest)throw Error(t('pm_fa_mismatch'));
    const sources=keys=>{
      if(!Array.isArray(keys)||keys.some(key=>typeof key!=='string'||!Object.hasOwn(refs,key)))throw Error(t('pm_fa_mismatch'));
      return fold('pm_fa_details',...keys.map(key=>{
        const ref=refs[key];if(!/^[0-9a-f]{64}$/.test(ref?.digest||''))throw Error(t('pm_fa_mismatch'));
        const value=sourceValue(ref.pointer);
        return h('div',{},code(key),code(ref.pointer),h('pre',{class:'pm-note-text'},typeof value==='string'?value:JSON.stringify(value,null,2)),code(ref.digest));
      }));
    };
    const content=h('div',{'data-pm-assessment-record':row.assessment_id},
      h('h4',{},t('pm_fa_record',{sequence:row.sequence})),h('p',{class:'muted'},date(row.created_at),' · ',row.created_by),
      h('p',{'data-pm-assessment-validation':''},t('pm_fa_'+row.validation.status)),statusPill('not_assessed'));
    if(row.validation.status==='format_valid'){
      const doc=row.document;
      if(doc?.version!==1||typeof doc.summary?.text!=='string'||!['findings','tradeoffs','questions','recommendations'].every(key=>Array.isArray(doc[key]))
        ||doc.questions.length>3)throw Error(t('pm_fa_mismatch'));
      content.append(h('p',{class:'pm-note-text'},doc.summary.text),sources(doc.summary.source_refs));
      const group=(key,items,renderItem)=>h('section',{},h('h4',{},t(key)),items.length?h('ol',{},...items.map(item=>h('li',{},...renderItem(item),sources(item.source_refs)))):note('pm_fa_none'));
      const options=items=>{
        if(!Array.isArray(items)||items.length<2||items.length>4||items.some(item=>typeof item.label!=='string'||typeof item.consequence!=='string'))throw Error(t('pm_fa_mismatch'));
        return h('dl',{},...items.flatMap(item=>[h('dt',{},item.label),h('dd',{class:'pm-note-text'},item.consequence)]));
      };
      content.append(group('pm_fa_findings',doc.findings,item=>{
        if(!['problem','deviation','assumption','unknown','decision_effect'].includes(item.kind)||typeof item.text!=='string'||typeof item.impact!=='string')throw Error(t('pm_fa_mismatch'));
        return [h('strong',{},t('pm_fa_kind_'+item.kind)),h('p',{class:'pm-note-text'},item.text),h('p',{class:'pm-note-text'},t('pm_fa_impact'),': ',item.impact)];
      }),group('pm_fa_tradeoffs',doc.tradeoffs,item=>{
        if(typeof item.decision!=='string')throw Error(t('pm_fa_mismatch'));return [h('strong',{},item.decision),options(item.options)];
      }),group('pm_fa_questions',doc.questions,item=>{
        if(typeof item.question!=='string'||typeof item.why_now!=='string')throw Error(t('pm_fa_mismatch'));
        return [h('strong',{},item.question),h('p',{class:'pm-note-text'},t('pm_fa_why'),': ',item.why_now),options(item.options)];
      }),group('pm_fa_recommendations',doc.recommendations,item=>{
        if(typeof item.text!=='string'||item.proposed_owner!=null&&typeof item.proposed_owner!=='string')throw Error(t('pm_fa_mismatch'));
        return [h('p',{class:'pm-note-text'},item.text),item.proposed_owner?h('p',{},t('pm_fa_proposed_owner'),': ',item.proposed_owner):''];
      }));
    }else content.append(note('pm_fa_raw_review'),h('ul',{},...row.validation.errors.map(error=>h('li',{},String(error)))));
    content.append(fold('pm_fa_details',note('pm_fa_unverified'),fold('pm_fa_raw',h('pre',{class:'pm-note-text'},row.raw_reply)),
      fold('pm_fa_provenance',h('p',{},t('pm_fa_intake'),code(row.intake_id),code(row.intake_digest)),
        h('p',{},t('pm_native_origin'),' ',row.provenance.adapter||t('pm_native_unknown_source')),
        h('p',{},t('pm_fa_model'),': ',row.provenance.actual_model||'—'),h('p',{},t('pm_fa_run'),code(row.run_id)),
        h('p',{},t('pm_fa_context'),code(row.context_digest)),h('p',{},t('pm_fa_reply_digest'),code(row.raw_reply_digest)),
        h('p',{},t('pm_fa_document_digest'),code(row.document_digest)),
        h('a',{href:`#/op/${encodeURIComponent(row.operation_id)}`},t('pm_operation')))));
    detail.replaceChildren(content);
  }
  async function read(id) {
    const ticket=++serial;reading=false;more.disabled=false;detail.replaceChildren();status.replaceChildren(note('pm_fa_loading'));
    try{const data=check(await api('GET',`${endpoint}/${encodeURIComponent(id)}`));if(!current(ticket))return;
      const row=valid(data.assessment);if(row.assessment_id!==id)throw Error(t('pm_fa_mismatch'));
      render(row);status.replaceChildren();
    }catch(error){if(ticket===serial)fail(error);}
  }
  async function loadMore() {
    if(reading||next==null||!intake)return;const ticket=serial,after=next;reading=true;more.disabled=true;
    try{const data=page(await api('GET',path(after)),after);if(!current(ticket))return;
      rows.push(...data.assessments.filter(row=>!rows.some(old=>old.assessment_id===row.assessment_id)));next=data.next_cursor;navigation();
    }catch(error){if(ticket===serial)fail(error.message===t('pm_fa_mismatch')?Error(t('pm_fa_read_failed')):error);}finally{if(ticket===serial){reading=false;more.disabled=false;}}
  }
  async function load(row) {
    clear();intake=row;selected=selections.get(row.intake_id)||'';const ticket=serial;status.replaceChildren(note('pm_fa_loading'));
    try{const data=page(await api('GET',path(0)),0);if(!current(ticket))return;
      rows=data.assessments;next=data.next_cursor;navigation();status.replaceChildren();
      if(selected)await read(selected);else if(rows.length)detail.append(note('pm_fa_select'));
    }catch(error){if(ticket===serial)fail(error.message===t('pm_fa_mismatch')?Error(t('pm_fa_read_failed')):error);}
  }
  async function showResult(result){
    const id=result?.outcome?.assessment_id;
    if(intake&&identifier(id)&&result.outcome.intake_id===intake.intake_id){selections.set(intake.intake_id,id);await load(intake);}
  }
  return {element,load,clear,showResult};
}
