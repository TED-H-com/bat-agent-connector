import {button, disclosure, emptyState, iconButton, sectionCard, skeleton, statusPill} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
import {joinLabel} from './label-text.js';
// The central service owns preparation and source policy. This view only reads.
export function workspaceWorkPackages({h,t,api,alive,workspaceId,errorBox,pageSize=20,onSelection=()=>{}}) {
  const endpoint=`/pm/workspaces/${encodeURIComponent(workspaceId)}/work-packages`;
  const idPattern=/^pmwp_[0-9a-f]{32}$/, digestPattern=/^[0-9a-f]{64}$/;
  let sequence=0,rows=[],cursor=null,pages=1,selected=null,busy=false;
  const status=h('div',{role:'status'}),list=h('div'),facts=h('div',{'data-pm-wp-detail':''});
  const refreshButton=iconButton({icon:'refresh-cw', label:t('wp_refresh'), onClick:()=>void refresh()});
  const more=button({variant:'secondary', label:t('wp_more'), hidden:true, onClick:()=>{pages++;void refresh();}});
  const element=h('section',{class:'pm-packages','data-pm-work-packages':''},sectionCard({body:h('div',{},
    h('div',{class:'pm-tool-head'},refreshButton),status,list,more,facts,
    disclosure({label:t('wp_details'),body:h('p',{class:'muted'},t('wp_intro'))}))}));
  const valid=ticket=>{alive();return element.isConnected&&ticket===sequence;};
  const detail=(title,...children)=>disclosure({element:h('details',{},h('summary',{},title),...children)});
  const paragraph=value=>h('p',{class:'pm-note-text'},value);
  const fact=(key,value)=>h('p',{class:'pm-source'},t(key),': ',value??t('wp_unknown'));
  const refs=value=>value?.length?fact('wp_source_refs',value.join(', ')):null;
  const labelled=(key,child)=>h('section',{},h('h4',{},t(key)),child);
  const checked=(data)=>{
    if(data?.version!==1||data.workspace_id!==workspaceId)throw Error(t('wp_invalid'));
    return data;
  };
  function packageCheck(row) {
    if(!idPattern.test(row?.package_id)||row.workspace_id!==workspaceId||!digestPattern.test(row.document_digest)
      ||row.document?.package_id!==row.package_id||row.document?.workspace_id!==workspaceId
      ||row.document?.work_item_id!==row.work_item_id||row.document?.execution_authorized!==false
      ||row.document?.quality_assessment!=='unassessed'||typeof row.document?.title!=='string')throw Error(t('wp_invalid'));
    return row;
  }
  function controls(){refreshButton.disabled=busy;more.disabled=busy;more.hidden=cursor===null;}
  function renderList(){
    list.replaceChildren(...(cursor===null?[]:[h('div',{class:'pm-tool-head'},statusPill('partial'),h('p',{class:'muted'},t('wp_partial')))]),
      ...rows.map(row=>h('div',{class:'pm-workspace-row','data-pm-wp-row':row.package_id},
        button({variant:'secondary',disabled:busy,'aria-pressed':selected===row.package_id?'true':'false',
          label:joinLabel([row.document.title, joinLabel([t('wp_preparation'), row.sequence], ' ')]),
          onClick:()=>void select(row.package_id)}))));
    if(!rows.length)list.append(emptyState({title:t('wp_empty'),heading:'h3'}));
  }
  function render(row,readiness){
    const doc=row.document,context=doc.context;
    const checks=h('ul',{'data-pm-wp-checks':''},...readiness.checks.map(check=>{
      const [kind,...suffix]=check.check.split(':'),key='wp_check_'+kind,label=t(key),help=t(key+'_help');
      return h('li',{},h('strong',{},label===key?check.check:label+(suffix.length?' · '+suffix.join(':'):'')),
        ' — ',t(check.status==='passed'?'wp_passed':'wp_attention'),check.assessment==='unknown'?' · '+t('wp_unknown'):'',
        check.status!=='passed'&&help!==key+'_help'?paragraph(help):check.status!=='passed'&&check.detail?paragraph(check.detail):null,
        check.requirement_ids?.length?fact('wp_dependencies',check.requirement_ids.join(', ')):null);
    }));
    const requirements=h('div',{},...(doc.requirements||[]).map(req=>detail(req.title||req.text||req.id,
      paragraph(req.text||req.description||req.title||req.id),fact('wp_requirement_state',t('wp_source_status_'+req.source_status)),
      h('ul',{},...(req.acceptance||[]).map(item=>h('li',{},paragraph(item.text),
        h('small',{class:'pm-source'},item.source_input_id,' · ',t('wp_source_status_'+item.source_status))))))));
    const tasks=h('ol',{},...(doc.tasks||[]).map(task=>h('li',{},paragraph(task.description),
      h('small',{class:'pm-source'},task.key,' · ',task.requirement_ids.join(', ')),
      task.depends_on.length?fact('wp_dependencies',task.depends_on.join(', ')):null,refs(task.source_ids))));
    const verification=h('div',{},h('p',{class:'muted'},t('wp_methods')),...(doc.verification||[]).map(item=>detail(item.key+' · '+item.requirement_id,
      paragraph(item.method),fact('wp_expected',item.expected_evidence),fact('wp_acceptance',item.acceptance_input_ids.join(', ')),refs(item.source_ids))));
    const delivery=doc.delivery,deliveryNode=h('div',{},paragraph(t('wp_'+delivery.goal)+' · '+delivery.repository+' · '+delivery.base_ref
      +(delivery.pull_number?' · #'+delivery.pull_number:'')),...['migration','rollout','rollback','observation'].map(key=>
      detail(t('wp_'+key)+' · '+t(delivery[key].status==='unresolved'?'wp_unresolved_status':'wp_'+delivery[key].status),paragraph(delivery[key].text))));
    const sources=detail(t('wp_sources'),fact('wp_package_id',row.package_id),fact('wp_digest',row.document_digest),
      fact('wp_created',timeStamp(row.created_at, {empty:null})),fact('wp_author',row.actor),
      fact('wp_plan',context.plan_ref.revision_id),
      fact('wp_scope',context.plan_ref.scope_revision),fact('wp_code',doc.code_source.repository+'@'+doc.code_source.source_sha),
      fact('wp_work',row.work_item_id),fact('wp_binding',context.work_ref?.binding_id),fact('wp_work_version',context.work_ref?.work_version),
      context.dod_ref?fact('wp_dod_revision',context.dod_ref.revision_id):null,context.dod_ref?fact('wp_dod_digest',context.dod_ref.document_digest):null,
      row.previous_package_id?fact('wp_previous',row.previous_package_id):null,
      h('a',{href:`#/op/${encodeURIComponent(row.operation_id)}`},t('wp_operation')),
      doc.evidence_refs?.length?detail(t('wp_evidence'),...doc.evidence_refs.map(ref=>h('p',{class:'pm-source'},ref.evidence_id,' · ',ref.fact_digest))):null,
      detail(t('wp_original'),...(doc.sources?.documents||[]).map(source=>detail(source.path,
        paragraph(source.content),h('p',{class:'pm-source'},source.source_id,' · ',source.commit_sha,' · ',source.sha256)))));
    const ready=readiness.status==='ready';
    const pill=ready?h('span',{class:'ui-pill tone-ok','data-status':'ready'},t('wp_ready_short')):statusPill('blocked');
    facts.replaceChildren(h('div',{class:'pm-tool-head'},h('h3',{},doc.title),h('p',{'data-pm-wp-readiness':''},pill)),
      labelled('wp_checks',checks),
      disclosure({label:t('wp_details'),body:h('div',{},paragraph(t(ready?'wp_ready':'wp_blocked')),paragraph(t('wp_boundary')),paragraph(t('wp_coverage')))}),
      labelled('wp_requirements',requirements),detail(t('wp_design'),paragraph(doc.design.text),fact('wp_reason',doc.rationale),refs(doc.design.source_ids)),
      detail(t('wp_tasks'),tasks),detail(t('wp_verification'),verification),detail(t('wp_delivery'),deliveryNode),
      detail(t('wp_dod'),h('p',{class:'muted'},t(doc.dod?'wp_dod_note':'wp_no_dod')),
        doc.dod?h('ul',{},...(doc.dod.criteria||[]).map(item=>h('li',{},item.text))):null),
      ...(doc.unresolved.length?[labelled('wp_unresolved',h('ul',{},...doc.unresolved.map(value=>h('li',{},value))))]:[]),sources);
  }
  async function readSelected(ticket){
    const id=selected;
    if(!id){facts.replaceChildren(h('p',{class:'muted'},t('wp_select')));return;}
    const [data,readiness]=await Promise.all([api('GET',`${endpoint}/${encodeURIComponent(id)}`),api('GET',`${endpoint}/${encodeURIComponent(id)}/readiness`)]);
    if(!valid(ticket)||id!==selected)return;
    const row=packageCheck(checked(data).package);checked(readiness);
    if(row.package_id!==id||readiness.package_id!==id||readiness.document_digest!==row.document_digest
      ||readiness.ready_for!=='engineering_handoff_review'||readiness.execution_authorized!==false||readiness.execution_available!==false
      ||!(readiness.dispatch_readiness==='not_implemented'||readiness.dispatch_readiness==='separate_fixed_handoff_admission'
        &&readiness.handoff?.action==='repository.continue'&&readiness.handoff.contract==='pm-engineering-handoff/v1'
        &&readiness.handoff.code_basis_required===true&&readiness.handoff.compatibility==='unverified'&&['bounded_full_prompt','bounded_full_prompt_or_fixed_spec_artifact'].includes(readiness.handoff.source_materialization))
      ||!['ready','blocked'].includes(readiness.status)||!Array.isArray(readiness.checks))throw Error(t('wp_invalid'));
    render(row,readiness);onSelection(row);
  }
  function failed(error,ticket){
    try{if(!valid(ticket))return;}catch{return;}
    // A later source/permission failure must not leave an earlier quoted title or document visible.
    rows=[];cursor=null;list.replaceChildren();facts.replaceChildren();onSelection(null);status.replaceChildren(errorBox(error),paragraph(t('wp_unavailable')));
  }
  async function select(id){
    if(busy||!idPattern.test(id))return;
    selected=id;const ticket=++sequence;busy=true;facts.replaceChildren();onSelection(null);renderList();controls();status.replaceChildren(skeleton(2));
    try{await readSelected(ticket);if(valid(ticket))status.replaceChildren();}catch(error){failed(error,ticket);}
    finally{try{if(valid(ticket)){busy=false;renderList();controls();}}catch{}}
  }
  async function refresh(){
    const ticket=++sequence;busy=true;rows=[];cursor=null;list.replaceChildren();facts.replaceChildren();onSelection(null);controls();
    status.replaceChildren(skeleton(2));
    try{
      let after=0,next=null;const loaded=[];
      for(let page=0;page<pages;page++){
        const data=checked(await api('GET',`${endpoint}?after=${after}&limit=${pageSize}`));
        if(!valid(ticket))return;
        if(!Array.isArray(data.packages)||data.next_cursor!==null&&(!Number.isSafeInteger(data.next_cursor)||data.next_cursor<=after))throw Error(t('wp_invalid'));
        loaded.push(...data.packages.map(packageCheck));next=data.next_cursor;
        if(next===null)break;after=next;
      }
      rows=loaded;cursor=next;renderList();
      // A hidden previous selection does not suppress a freshly authorized catalog.
      if(selected&&!rows.some(row=>row.package_id===selected))selected=null;
      await readSelected(ticket);if(valid(ticket))status.replaceChildren();
    }catch(error){failed(error,ticket);}
    finally{try{if(valid(ticket)){busy=false;renderList();controls();}}catch{}}
  }
  function withdraw(){sequence++;busy=false;rows=[];cursor=null;list.replaceChildren();facts.replaceChildren();onSelection(null);status.replaceChildren();controls();}
  return{element,refresh,withdraw};
}
