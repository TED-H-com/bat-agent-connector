import {button,sectionCard,disclosure,discussLink,inspector,skeleton} from './ui/components.js';
import {icon} from './ui/icons.js';
import {proof} from './pm-brief.js';
import {timeLabel,timeStamp} from './ui/stamps.js';
import {reasonLabel} from './ui/ui-strings.js';
import {retiredView,viewLeft} from './pm-retired.js';
import {overviewAssessmentRequest} from './pm-overview-assessment-request.js';

const validId=id=>typeof id==='string'&&/^pmoa_[0-9a-f]{32}$/.test(id);
const templates=new Set(['goal_blocker','decision_first','watch_exposure','insufficient_basis','no_priority_identified',
  'review_evidence','clarify_dependency','prepare_options','follow_recorded_action','confirm_direction','choose_response',
  'review_proposal','watch_recorded_risk','watch_recorded_forecast','await_recorded_evidence']);

export function overviewAssessment(options) {
  const {h,t,api,alive,workspaceId,errorBox,permitted}=options, root=`/pm/workspaces/${encodeURIComponent(workspaceId)}/overview-assessments`;
  let serial=0, epoch=0, current=null, latest=null, capability=null, signature='', reader=null, readerId=null, active=false, busy=false, readerSignature='';
  const content=h('div',{'data-overview-assessment-content':''},skeleton(2,{label:t('poa_loading')})), state=h('span',{class:'ui-pill tone-neutral','data-overview-assessment-freshness':'',hidden:true});
  const attribution=h('span',{class:'ui-meta'}),availability=h('div',{'data-overview-assessment-availability':''});
  const status=h('div',{role:'status'}), more=button({size:'sm',label:t('poa_latest'),'data-overview-assessment-latest':'',onClick:()=>void refresh(true)});
  more.hidden=true;
  const history=button({variant:'ghost',size:'sm',label:t('poa_history'),onClick:()=>void openHistory()});
  const assess=button({variant:'secondary',size:'sm',label:t('poa_assess'),'data-overview-assessment-prepare':'',onClick:()=>{controls.hidden=false;void request.prepare();}});
  const controls=h('div',{hidden:true,'data-overview-assessment-controls':''});
  const request=overviewAssessmentRequest({...options,readLatest:latestCarrier,onSaved:async()=>{
    const ownsFocus=controls.contains(document.activeElement);
    await refresh(true);controls.hidden=true;request.clear();
    if(ownsFocus&&active&&content.isConnected){content.tabIndex=-1;content.focus({preventScroll:true});content.scrollIntoView({block:'nearest'});}
  }});
  controls.append(request.element);
  const box=sectionCard({title:t('poa_title'),actions:[history,assess],
    body:h('div',{},h('div',{class:'pm-assessment-meta'},state,h('span',{class:'ui-meta'},t('poa_unadopted')),attribution),status,content,more,availability,controls)});
  box.dataset.overviewAssessment='';
  const valid=ticket=>{alive();return ticket===serial;};
  const mismatch=()=>Object.assign(Error(t('poa_mismatch')),{code:'PM_SCOPE_MISMATCH'});
  const checkList=data=>{
    if(data?.version!==1 || data.workspace_id!==workspaceId || data.order!=='newest_first' || !Array.isArray(data.assessments)
      || data.assessments.some(row=>!validId(row.assessment_id)||!/^pmt_[0-9a-f]{32}$/.test(row.thread_id))
      || typeof data.partial!=='boolean' || typeof data.source_filtered!=='boolean'
      || (data.next_cursor!==null && (typeof data.next_cursor!=='string'||!/^[A-Za-z0-9_-]{1,256}$/.test(data.next_cursor)))
      || data.partial!==(data.next_cursor!==null))throw mismatch();
    return data;
  };
  const list=async before=>checkList(await api('GET',`${root}?limit=10${before?`&before=${encodeURIComponent(before)}`:''}`));
  async function latestCarrier() {
    // A factual refresh can supersede another refresh, but not a person's
    // explicit preparation. Withdrawal/disposal still invalidates this read.
    let before=null;const seen=new Set(),ticket=epoch;
    for(let count=0;count<32;count++) {
      const data=await list(before);alive();if(ticket!==epoch)throw Object.assign(Error(),{code:'PM_DISPOSED'});
      if(data.assessments.length)return data.assessments[0];
      if(!data.partial)return null;
      if(seen.has(data.next_cursor))throw mismatch();seen.add(data.next_cursor);before=data.next_cursor;
    }
    throw Error(t('poa_partial_history'));
  }
  async function detail(id) {
    const value=await api('GET',`${root}/${encodeURIComponent(id)}`),row=value?.assessment;
    if(value?.version!==1 || value.workspace_id!==workspaceId || !validId(id) || row?.assessment_id!==id || row.workspace_id!==workspaceId
      || !['current','out_of_date','unknown'].includes(value.freshness) || value.context?.workspace_id!==workspaceId
      || value.context?.thread_id!==row.thread_id || value.context?.projection_digest!==row.projection_digest
      || value.context?.schema!=='pm_overview_bundle_v1' || row.output_version!==1)throw mismatch();
    return value;
  }
  function evidence(value,claim) {
    const refs=value.context.source_references;
    return disclosure({label:t('poa_basis'),body:h('ul',{},...claim.basis_refs.map(token=>{
      const ref=refs[token];return h('li',{},ref.ref?h('button',{type:'button',class:'pm-overview-link',onclick:()=>options.onOpen(ref.ref,{title:ref.label,ref:ref.ref})},ref.label):ref.label||reasonLabel(t,ref.reason||'unknown'));
    }))});
  }
  function claimNode(value,claim,heading=false) {
    const refs=value.context.source_references;
    if(!templates.has(claim?.template)||!refs?.[claim.subject_ref]||!Array.isArray(claim.basis_refs)||claim.basis_refs.some(token=>!refs[token]))throw mismatch();
    const subject=refs[claim.subject_ref],text=t('poa_'+claim.template,{subject:subject.label||t('poa_basis')});
    return h('div',{class:'pm-assessment-claim','data-overview-assessment-claim':claim.key},h(heading?'p':'div',{class:heading?'pm-assessment-judgment':''},text),
      h('div',{class:'pm-assessment-evidence'},evidence(value,claim),subject.ref?discussLink({workspaceId,refs:[subject.ref],title:subject.label,from:'overview'}):null));
  }
  function renderDocument(value) {
    const row=value.assessment,doc=row.document,refs=value.context.source_references;
    const result=h('div',{'data-overview-assessment-id':row.assessment_id},h('p',{class:'ui-meta'},timeLabel(t,'poa_as_of',{date:timeStamp(row.as_of)})));
    if(row.validation?.status==='format_valid' && doc?.version===1){
      result.append(claimNode(value,doc.judgment,true));
      for(const group of ['recommendations','needs_decision','can_be_handled','watch']) {
        if(!Array.isArray(doc[group]))throw mismatch();
        if(doc[group].length)result.append(h('section',{class:'pm-assessment-group'},h('h3',{},t('poa_'+group)),...doc[group].map(claim=>claimNode(value,claim))));
      }
      if(!Array.isArray(doc.limitations)||doc.limitations.some(token=>refs?.[token]?.category!=='coverage'))throw mismatch();
      if(doc.limitations.length)result.append(disclosure({label:t('poa_limits'),body:h('ul',{},...doc.limitations.map(token=>h('li',{},reasonLabel(t,refs[token].reason))))}));
    }else{
      result.append(h('p',{},t('poa_needs_review')));
      if(typeof row.raw_reply==='string')result.append(disclosure({label:t('poa_raw'),body:h('pre',{class:'pm-assessment-raw'},row.raw_reply)}));
    }
    result.append(disclosure({label:t('poa_record'),body:h('div',{},
      h('p',{class:'ui-meta'},timeLabel(t,'poa_generated',{date:timeStamp(row.created_at)})),
      row.provenance?.actual_model?h('p',{},t('pm_native_model')+': '+row.provenance.actual_model):null,
      /^op_[0-9a-f]{32}$/.test(row.operation_id)?h('a',{href:`#/op/${row.operation_id}`},t('pm_operation')):null,
      proof({h,t,name:t('poa_record'),value:{assessment_id:row.assessment_id,operation_id:row.operation_id,run_id:row.run_id,
        generated_at:row.created_at,as_of:row.as_of,journal_cursor:value.context.journal_cursor,profile:value.context.profile,
        output_version:row.output_version,context_digest:row.context_digest,document_digest:row.document_digest,
        coverage:value.context.coverage,validation:row.validation,provenance:row.provenance}}))}));
    result.append(h('a',{class:'ui-meta',href:`#/pm/${encodeURIComponent(workspaceId)}/discussions/${encodeURIComponent(row.thread_id)}`},t('poa_thread')));
    return result;
  }
  function renderCard(value) {
    const row=value.assessment,doc=row.document,refs=value.context.source_references;
    const details=button({variant:'ghost',size:'sm',label:t('poa_details'),'data-overview-assessment-details':'',onClick:()=>void openHistory(row.assessment_id)});
    if(row.validation?.status!=='format_valid'||doc?.version!==1)return h('div',{'data-overview-assessment-id':row.assessment_id},h('p',{},t('poa_needs_review')),details);
    const group=(label,body)=>h('div',{class:'pm-assessment-summary-row'},h('dt',{},t(label)),h('dd',{},body));
    const shown=[doc.judgment,...doc.recommendations.slice(0,1),...doc.needs_decision];
    const text=claim=>{if(!templates.has(claim?.template)||!refs?.[claim.subject_ref])throw mismatch();return t('poa_'+claim.template,{subject:refs[claim.subject_ref].label||t('poa_basis')});};
    const subjectLink=claim=>{const subject=refs[claim.subject_ref];return subject.ref?h('button',{type:'button',class:'pm-overview-link',onclick:()=>options.onOpen(subject.ref,{title:subject.label,ref:subject.ref})},subject.label):null;};
    const result=h('dl',{class:'pm-assessment-summary','data-overview-assessment-id':row.assessment_id},
      group('poa_current_read',h('span',{},text(doc.judgment))));
    if(doc.recommendations.length)result.append(group('poa_this_week',h('div',{},timeLabel(t,'poa_'+doc.recommendations[0].template,{subject:subjectLink(doc.recommendations[0])||refs[doc.recommendations[0].subject_ref].label}))));
    if(doc.needs_decision.length)result.append(group('poa_needs_decision',h('div',{},...doc.needs_decision.map(claim=>{
      const subject=refs[claim.subject_ref];return h('div',{class:'pm-assessment-decision'},h('span',{},timeLabel(t,'poa_'+claim.template,{subject:subjectLink(claim)||subject.label})),
        subject.ref?discussLink({workspaceId,refs:[subject.ref],title:subject.label,from:'overview'}):null);
    }))));
    const tokens=[...new Set(shown.map(claim=>claim.subject_ref))];
    result.append(group('poa_based_on',h('div',{class:'pm-assessment-evidence'},...tokens.map(token=>{
      const ref=refs[token];return ref.ref?h('button',{type:'button',class:'ui-button ghost ui-sm',onclick:()=>options.onOpen(ref.ref,{title:ref.label,ref:ref.ref})},ref.label)
        :h('span',{class:'ui-meta'},ref.category==='projection'?t('poa_overview'):reasonLabel(t,ref.reason||'unknown'));
    }),details)));
    return result;
  }
  function historyDocument(value) {return h('div',{},h('p',{class:'ui-meta','data-overview-history-freshness':''},t('poa_'+value.freshness)),renderDocument(value));}
  function sync() {
    const label=t(current?'poa_refresh':'poa_assess');assess.replaceChildren(current?icon('refresh-cw'):label);
    assess.className=current?'ui-button ghost ui-icon-button':'ui-button secondary ui-sm';assess.setAttribute('aria-label',label);assess.title=label;
    const cap=capability?.overview_assessment,recover=request.hasPending();
    // Opening a draft performs fresh capability/source reads. A factual refresh
    // must not disable a held pointer press and silently discard its click.
    assess.disabled=!permitted(false)||cap?.authorized!==true||(!recover&&(cap?.eligible_for_preflight!==true||cap?.limits?.allowed!==true));
    history.disabled=busy||!capability;
    more.hidden=!latest||!current||latest.assessment_id===current.assessment.assessment_id;
    state.hidden=!current;
    if(current){
      const row=current.assessment,source=row.provenance;
      state.textContent=t('poa_'+(current.freshness==='current'&&row.document?.limitations?.length?'partial':current.freshness));
      attribution.textContent=[source?.adapter,source?.actual_model||t('poa_model_unknown')].filter(Boolean).join(' · ');
      attribution.title=t('poa_as_of',{date:timeStamp(row.as_of).textContent});
    }else attribution.textContent='';
    availability.replaceChildren();
    if(cap&&!cap.authorized)availability.append(h('p',{class:'ui-meta'},t('poa_permission')));
    else if(cap&&cap.eligible_for_preflight!==true)availability.append(h('p',{class:'ui-meta'},t('poa_agent_missing'),' ',
      h('a',{href:`#/settings?section=agents&from=${encodeURIComponent(`#/pm/${workspaceId}/overview`)}`},t('poa_setup'))));
    else if(cap?.limits?.allowed===false)availability.append(h('p',{class:'ui-meta'},timeLabel(t,'poa_rate',{date:timeStamp(cap.limits.retry_at)})));
  }
  function closeHistory(){reader?.close();reader=null;readerId=null;readerSignature='';}
  function withdraw() {serial++;epoch++;current=null;latest=null;capability=null;signature='';busy=false;closeHistory();request.clear();controls.hidden=true;content.replaceChildren();status.replaceChildren();sync();}
  function unavailable() {withdraw();content.replaceChildren(h('p',{class:'ui-meta'},t('poa_unavailable')));}
  async function refresh(selectLatest=false) {
    const ticket=++serial;busy=true;sync();
    try{
      const cap=await api('GET',`/pm/workspaces/${encodeURIComponent(workspaceId)}/capabilities`);if(!valid(ticket))return;
      if(cap?.workspace_id!==workspaceId)throw mismatch();
      if(cap.overview_assessment?.version!==1){withdraw();box.hidden=true;return;}
      box.hidden=false;
      const page=await list();if(!valid(ticket))return;
      capability=cap;latest=page.assessments[0]||null;
      const id=selectLatest?latest?.assessment_id:current?.assessment.assessment_id||latest?.assessment_id;
      const value=id?await detail(id):null;if(!valid(ticket))return;
      if(readerId) {const historical=await detail(readerId);if(!valid(ticket))return;const stamp=JSON.stringify(historical.assessment);if(stamp!==readerSignature){reader.body.querySelector('[data-overview-history-detail]')?.replaceChildren(historyDocument(historical));readerSignature=stamp;}
        const freshness=reader.body.querySelector('[data-overview-history-freshness]');if(freshness)freshness.textContent=t('poa_'+historical.freshness);}
      const next=JSON.stringify(value?.assessment||null);
      if(signature!==next){const node=value?renderCard(value):h('p',{class:'ui-meta'},t(page.partial?'poa_partial_history':'poa_empty'));content.replaceChildren(node);signature=next;}
      current=value;status.replaceChildren();
    }catch(error){
      if(retiredView(error)||viewLeft(alive)||!valid(ticket))return;
      if([403,404].includes(error.status)||error.code==='PM_SCOPE_MISMATCH'){unavailable();return;}
      else if(current)current={...current,freshness:'unknown'};
      status.replaceChildren(errorBox(error,{within:status}),button({size:'sm',label:t('poa_retry'),onClick:()=>void refresh()}));
    }finally{if(ticket===serial){busy=false;sync();}}
  }
  async function openHistory(initialId=null) {
    closeHistory();const ticket=epoch;
    reader=inspector({title:t(initialId?'poa_title':'poa_history'),returnFocus:initialId?()=>content.querySelector('[data-overview-assessment-details]'):history,onClose:()=>{reader=null;readerId=null;readerSignature='';}});
    reader.dataset.overviewAssessmentHistory='';const opened=reader;
    const rows=h('div'),selected=h('div',{'data-overview-history-detail':''}),notice=h('div',{role:'status'});
    const next=button({size:'sm',label:t('poa_more'),onClick:()=>void load(before)});let before=null,selection=0;const cursors=new Set();
    opened.body.append(rows,next,notice,selected);box.append(opened);
    async function select(id){
      const turn=++selection;readerId=null;selected.replaceChildren(skeleton(2,{label:t('poa_loading')}));
      try{const value=await detail(id);if((!valid(serial)||ticket!==epoch)||reader!==opened||turn!==selection)return;readerId=id;readerSignature=JSON.stringify(value.assessment);selected.replaceChildren(historyDocument(value));notice.replaceChildren();}
      catch(error){if(retiredView(error)||viewLeft(alive)||reader!==opened||(!valid(serial)||ticket!==epoch)||turn!==selection)return;
        if([403,404].includes(error.status)){unavailable();return;}
        selected.replaceChildren();readerId=null;notice.replaceChildren(errorBox(error,{within:notice}));}
    }
    async function load(cursor){
      next.disabled=true;
      try{const page=await list(cursor);if((!valid(serial)||ticket!==epoch)||reader!==opened)return;
        if(page.next_cursor&&cursors.has(page.next_cursor))throw mismatch();if(page.next_cursor)cursors.add(page.next_cursor);
        for(const row of page.assessments)rows.append(button({variant:'ghost',size:'sm',label:h('span',{},timeStamp(row.created_at),' · ',t('poa_'+row.freshness)),
          'data-overview-history-id':row.assessment_id,onClick:()=>void select(row.assessment_id)}));
        before=page.next_cursor;next.hidden=!before;notice.replaceChildren(...(page.partial||page.source_filtered?[h('p',{class:'ui-meta'},t('poa_partial_history'))]:[]));
      }catch(error){if(retiredView(error)||viewLeft(alive)||reader!==opened||(!valid(serial)||ticket!==epoch))return;
        if([403,404].includes(error.status)){unavailable();return;}
        notice.replaceChildren(errorBox(error,{within:notice}));}
      finally{if(reader===opened)next.disabled=false;}
    }
    if(initialId){rows.hidden=true;next.hidden=true;await select(initialId);}else await load(null);
  }
  return {box,refresh,withdraw,setActive(value){active=value;if(!active)closeHistory();},dispose:withdraw};
}
