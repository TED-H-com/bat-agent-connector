import {iconButton, emptyState, skeleton} from './ui/components.js';
// Publication uses the existing central operation; only explicitly entered text is public.
import {firstLabel, joinLabel, labelSegment} from './label-text.js';
export function workspaceIssueMappings({h,t,api,alive,workspaceId,permitted,form,input,labelled,errorBox}) {
  const endpoint=`/pm/workspaces/${encodeURIComponent(workspaceId)}/issue-mappings`;
  const id=/^pmim_[0-9a-f]{32}$/,revision=/^pmir_[0-9a-f]{32}$/,digest=/^[0-9a-f]{64}$/;
  let serial=0,loaded=false,rows=[],cursor=null,pages=1,filtered=false,packageRow=null,selectedId=null,current=null;
  const publishers=new Map(),cancellers=new Map(),status=h('div',{role:'status'}),catalog=h('div'),selected=h('div'),publication=h('div'),detail=h('div');
  const refreshButton=iconButton({icon:'refresh-cw', label:t('pmi_refresh'), ...{type:'button',class:'secondary',onclick:()=>void refresh()}});
  const more=h('button',{type:'button',class:'secondary',hidden:true,onclick:()=>{pages++;void refresh();}},t('pmi_more'));
  const element=h('section',{'data-pm-issue-mappings':'',class:'pm-issues'},
    h('div',{class:'actions'},h('h3',{},t('pmi_title')),refreshButton),h('p',{class:'muted'},t('pmi_intro')),status,catalog,more,selected,publication,detail);
  const valid=ticket=>{alive();return element.isConnected&&ticket===serial;};
  const hidden=()=>h('input',{type:'hidden'}),fold=(label,...children)=>h('details',{},h('summary',{},t(label)),...children);
  // relation.repository is the stored owner/name. A published issue copies the same name onto issue.repository.
  const repositoryName=doc=>firstLabel(doc?.relation?.repository,doc?.issue?.repository);
  const issueNumber=doc=>Number.isSafeInteger(doc?.issue?.number)?`#${doc.issue.number}`:'';
  const stateLabel=state=>{const key='pmi_'+state,text=t(key);return text===key?'':text;};
  const trackedIssue=(doc,state)=>joinLabel([repositoryName(doc),issueNumber(doc),state]);
  const checkScope=value=>{if(value?.version!==1||value.workspace_id!==workspaceId)throw Error(t('pmi_invalid'));return value;};
  const checkRow=row=>{
    if(!id.test(row?.mapping_id)||!revision.test(row.revision_id)||!digest.test(row.document_digest)||row.workspace_id!==workspaceId
      ||row.document?.mapping_id!==row.mapping_id||row.document?.work_item_id!==row.work_item_id||row.document?.revision_id!==row.revision_id
      ||!['tracking','cancelled'].includes(row.document?.state)||row.document.execution_authorized!==false||row.document.counts_as_done!==false)throw Error(t('pmi_invalid'));
    return row;
  };
  function update(){refreshButton.disabled=!loaded;more.disabled=!loaded;more.hidden=cursor===null;
    for(const editor of [...publishers.values(),...cancellers.values()])editor.update();}
  const matching=()=>packageRow?rows.filter(row=>row.work_item_id===packageRow.work_item_id
    &&row.document.relation.repository_id===packageRow.document.code_source.repository_id):[];
  function publicationReady(){
    if(!loaded||!packageRow||!permitted(false))return false;
    const found=matching();if(found.length>1)return false;
    if(found.length)return current?.mapping_id===found[0].mapping_id&&current.revision_id===found[0].revision_id&&current.document.state==='tracking';
    return cursor===null&&!filtered;
  }
  function publishEditor(){
    const workId=packageRow.work_item_id;
    if(publishers.has(workId))return publishers.get(workId);
    const title=input('pmi_public_title',{max:200}),body=input('pmi_public_body',{max:32000,multiline:true}),packageId=hidden(),packageDigest=hidden(),mappingRevision=hidden();
    let editor=null,syncing=false;
    editor=form({action:'pm.issue.publish',target:{workspace_id:workspaceId,work_item_id:workId},
      fields:{title,body,package:packageId,digest:packageDigest,revision:mappingRevision},
      params:values=>({package_id:values.package,public_title:values.title,public_body:values.body}),
      preconditions:()=>({expected_package_digest:packageDigest.value,expected_mapping_revision:mappingRevision.value||null}),
      permitted:()=>publicationReady()&&packageRow.work_item_id===workId&&packageId.value===packageRow.package_id
        &&packageDigest.value===packageRow.document_digest&&mappingRevision.value===(matching()[0]?.revision_id||''),
      recoverPermitted:()=>loaded&&permitted(false),label:'pmi_publish',onSaved:()=>refresh(),
      onStateChange:()=>{if(editor&&!syncing)editor.update();},
      extra:h('div',{},h('p',{class:'muted'},t('pmi_public_help')),labelled('pmi_public_title',title),labelled('pmi_public_body',body),packageId,packageDigest,mappingRevision)});
    const originalUpdate=editor.update,stale=h('p',{class:'muted',hidden:true},t('pmi_stale'));
    const select=h('button',{type:'button',class:'secondary',onclick:()=>{
      if(editor.locked()||!publicationReady()||packageRow.work_item_id!==workId)return;
      editor.set('package',packageRow.package_id);editor.set('digest',packageRow.document_digest);editor.set('revision',matching()[0]?.revision_id||'');editor.update();
    }},t('pmi_review_source'));
    editor.box.append(stale,select);
    editor.update=()=>{
      if(syncing)return;syncing=true;
      try{
        const selectedWork=packageRow?.work_item_id===workId;
        if(selectedWork&&publicationReady()&&!editor.locked()&&(!packageId.value||!title.value&&!body.value)){
          editor.set('package',packageRow.package_id);editor.set('digest',packageRow.document_digest);editor.set('revision',matching()[0]?.revision_id||'');
        }
        originalUpdate();
        const changed=selectedWork&&packageId.value&&(packageId.value!==packageRow.package_id||packageDigest.value!==packageRow.document_digest||mappingRevision.value!==(matching()[0]?.revision_id||''));
        stale.hidden=!changed;select.hidden=!changed;select.disabled=editor.locked()||!publicationReady();
      }finally{syncing=false;}
    };
    publishers.set(workId,editor);return editor;
  }
  function cancelEditor(row){
    const mid=row.mapping_id;if(cancellers.has(mid))return cancellers.get(mid);
    const reason=input('pmi_private_reason',{max:4000,multiline:true}),publicReason=input('pmi_public_reason',{max:8000,multiline:true,required:false}),version=hidden();
    let editor=null,syncing=false;
    editor=form({action:'pm.issue.cancel',target:{workspace_id:workspaceId,mapping_id:mid},fields:{reason,publicReason,version},
      params:values=>({rationale:values.reason,...(values.publicReason?{public_reason:values.publicReason}:{})}),
      preconditions:()=>({expected_mapping_revision:version.value}),permitted:()=>loaded&&permitted(false)&&current?.mapping_id===mid&&current.document.state==='tracking'&&version.value===current.revision_id,
      recoverPermitted:()=>loaded&&permitted(false),label:'pmi_stop',onSaved:()=>refresh(),
      onStateChange:()=>{if(editor&&!syncing)editor.update();},
      extra:h('div',{},h('p',{class:'muted'},t('pmi_stop_help')),labelled('pmi_private_reason',reason),labelled('pmi_public_reason',publicReason),version)});
    const updateOriginal=editor.update,warning=h('p',{class:'muted',hidden:true},t('pmi_stale'));
    const review=h('button',{type:'button',class:'secondary',onclick:()=>{
      if(editor.locked()||!loaded||!permitted(false)||current?.mapping_id!==mid)return;editor.set('version',current.revision_id);editor.update();
    }},t('pmi_review_source'));
    const removePublic=h('button',{type:'button',class:'secondary',onclick:()=>{
      if(editor.locked()||!loaded||!permitted(false))return;editor.set('publicReason','');editor.update();
    }},t('pmi_remove_public'));
    editor.box.append(warning,review,removePublic);
    editor.update=()=>{
      if(syncing)return;syncing=true;
      try{
        if(current?.mapping_id===mid&&!editor.locked()&&(!version.value||!reason.value&&!publicReason.value))editor.set('version',current.revision_id);
        updateOriginal();const changed=current?.mapping_id===mid&&version.value&&version.value!==current.revision_id;
        warning.hidden=!changed;review.hidden=!changed;review.disabled=editor.locked()||!loaded||!permitted(false);
        removePublic.hidden=!publicReason.value;removePublic.disabled=editor.locked()||!loaded||!permitted(false);
      }finally{syncing=false;}
    };
    // Changing the optional public text changes the lifecycle action variant.
    publicReason.addEventListener('input',()=>editor.update());
    cancellers.set(mid,editor);return editor;
  }
  function renderPublication(){
    selected.replaceChildren();publication.replaceChildren();
    if(!packageRow){selected.append(h('p',{class:'muted'},t('pmi_select_package')));return;}
    selected.append(h('p',{},t('pmi_fixed_package'),' ',packageRow.sequence,' · ',packageRow.document.title));
    const found=matching();
    if(found[0]?.document.state==='cancelled'){
      publication.append(h('p',{class:'muted'},t('pmi_cancelled_help')));
      // Stopping tracking forbids a new publication, but does not erase an
      // earlier ambiguous request or prevent its original receipt being read.
      const editor=publishEditor();if(editor.locked())publication.append(fold('pmi_prepare',editor.box));
    }
    else if(!found.length&&(cursor!==null||filtered))publication.append(h('p',{class:'muted'},t('pmi_incomplete')));
    else publication.append(fold('pmi_prepare',publishEditor().box));
    update();
  }
  function renderDetail(record){
    const row=record.current,doc=row.document;
    detail.replaceChildren(h('h4',{},trackedIssue(doc)),h('p',{},stateLabel(doc.state)||t('pm_label_unknown')),
      h('p',{class:'muted'},t('pmi_not_done')),
      fold('pmi_history',...record.revisions.map(item=>{
        const d=item.document,selected=d.selected;
        return h('details',{},h('summary',{},joinLabel([`${t('pmi_revision')} ${labelSegment(item.sequence)}`, item.actor])),
          selected.public_title?h('h4',{},selected.public_title):'',selected.public_body?h('pre',{class:'pm-note-text'},selected.public_body):'',
          selected.public_reason?h('pre',{class:'pm-note-text'},selected.public_reason):'',
          selected.rationale?h('p',{},t('pmi_private_reason'),': ',selected.rationale):'',
          h('p',{class:'pm-source'},item.revision_id,' · ',d.package_ref.package_id),
          h('a',{href:`#/op/${item.operation_id}`},t('pm_operation')));
      })),fold('pmi_stop',cancelEditor(row).box));
  }
  async function readSelected(ticket){
    if(!selectedId)return;
    const data=checkScope(await api('GET',`${endpoint}/${selectedId}`));if(!valid(ticket))return;
    if(data.mapping_id!==selectedId||data.execution_authorized!==false||!Array.isArray(data.revisions)||!data.revisions.length)throw Error(t('pmi_invalid'));
    data.revisions.forEach(checkRow);current=checkRow(data.current);
    if(current.mapping_id!==selectedId||data.revisions.at(-1).revision_id!==current.revision_id||data.revisions.some(row=>row.mapping_id!==selectedId))throw Error(t('pmi_invalid'));
    const catalogRow=rows.find(row=>row.mapping_id===selectedId);
    if(catalogRow&&catalogRow.revision_id!==current.revision_id)throw Error(t('pmi_changed'));
    renderDetail(data);
  }
  function renderCatalog(){catalog.replaceChildren(...rows.map(row=>h('button',{type:'button',class:'secondary','aria-pressed':row.mapping_id===selectedId,
    onclick:()=>void selectMapping(row.mapping_id)},trackedIssue(row.document,stateLabel(row.document.state)))));
    if(!rows.length)catalog.append(emptyState({title:t('pmi_empty')}));
    if(filtered)catalog.append(h('p',{class:'muted'},t('pmi_filtered')));
  }
  function withdraw(){serial++;loaded=false;rows=[];cursor=null;filtered=false;current=null;catalog.replaceChildren();detail.replaceChildren();publication.replaceChildren();selected.replaceChildren();update();}
  function failure(error,ticket){try{if(!valid(ticket))return;}catch{return;}packageRow=null;withdraw();status.replaceChildren(errorBox(error,{summary:t('pmi_unavailable')}));refreshButton.disabled=false;}
  async function selectMapping(mid){
    if(!loaded)return;selectedId=mid;const ticket=++serial;loaded=false;current=null;detail.replaceChildren();publication.replaceChildren();update();
    try{await readSelected(ticket);if(!valid(ticket))return;loaded=true;renderCatalog();renderPublication();update();}catch(error){failure(error,ticket);}
  }
  async function refresh(){
    withdraw();const ticket=serial;status.replaceChildren(skeleton(2));
    try{
      let after=0;
      for(let page=0;page<pages;page++){
        const data=checkScope(await api('GET',`${endpoint}?after=${after}&limit=20`));if(!valid(ticket))return;
        if(!Array.isArray(data.mappings)||data.next_cursor!==null&&(!Number.isSafeInteger(data.next_cursor)||data.next_cursor<=after))throw Error(t('pmi_invalid'));
        const known=new Set(rows.map(row=>row.mapping_id));for(const row of data.mappings){checkRow(row);if(known.has(row.mapping_id))throw Error(t('pmi_invalid'));known.add(row.mapping_id);rows.push(row);}
        filtered||=Boolean(data.coverage?.visibility_filtered);cursor=data.next_cursor;if(cursor===null)break;after=cursor;
      }
      if(packageRow){
        const expected=packageRow,data=checkScope(await api('GET',`/pm/workspaces/${workspaceId}/work-packages/${packageRow.package_id}`));
        if(!valid(ticket))return;
        if(data.package?.package_id!==expected.package_id||data.package?.work_item_id!==expected.work_item_id||data.package?.document_digest!==expected.document_digest)throw Error(t('pmi_invalid'));
        packageRow=data.package;
      }
      const found=matching();if(found.length===1)selectedId=found[0].mapping_id;
      if(selectedId&&!rows.some(row=>row.mapping_id===selectedId))selectedId=null;
      await readSelected(ticket);if(!valid(ticket))return;loaded=true;status.replaceChildren();renderCatalog();renderPublication();update();
    }catch(error){failure(error,ticket);}
  }
  async function setPackage(row){packageRow=row;if(!row){withdraw();status.replaceChildren();refreshButton.disabled=false;return;}await refresh();}
  return {element,refresh,withdraw,setPackage};
}
