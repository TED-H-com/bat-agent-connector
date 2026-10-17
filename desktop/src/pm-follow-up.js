import {iconButton, button, sectionCard, statusPill, disclosure, emptyState, inlineError} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
import {panelActorLabel} from './pm-governance-strings.js';
// Saved discussion preparation. Model execution and adopted scope remain separate.
import {followUpAssessments} from './pm-follow-up-analysis.js';
import {followUpAnalysisRequest} from './pm-follow-up-request.js';
import {retiredView, viewLeft} from './pm-retired.js';
export function workspaceFollowUp({h,t,api,alive,workspaceId,permitted,form,input,labelled,errorBox,caps,namespace,pageSize=20}) {
  const endpoint=`/pm/workspaces/${encodeURIComponent(workspaceId)}`, actor=caps()?.actor;
  const domains=['governance','scope','schedule','finance','stakeholders','resources','risk','quality'];
  const number=value=>Number.isSafeInteger(value)&&value>=0;
  if(!number(pageSize)||pageSize<1||pageSize>100)throw Error('Invalid Follow-up page size');
  let serial=0, selected='', loaded=false, head=null, plan=null, metadata=[], next=null, sourceLoaded=false;
  let sourcePages={}, snapshot=null, thread=null, editor=null, reading=false, sourceSerial=0, retired=false, verifiedRole=null;
  let prepareOpen=false, launch=null;
  const prepareId='pm-follow-up-create-'+crypto.randomUUID();
  const actorLabel=value=>panelActorLabel(t,value);
  const status=h('div',{role:'status'}),view=h('div'),sourceStatus=h('div',{role:'status'}),choices=h('div'),meetingChoices=h('div');
  const note=key=>h('p',{class:'muted pm-note-text'},t(key));
  const code=value=>h('code',{class:'mono pm-source'},value||t('pm_fu_unknown'));
  const state=value=>{const key='pm_fu_state_'+value;return t(key)===key?String(value||t('pm_fu_unknown')).replaceAll('_',' '):t(key);};
  const date=value=>timeStamp(value, {empty:t('pm_fu_unknown')});
  const fold=(key,...nodes)=>disclosure({label:t(key),body:h('div',{},...nodes)});
  const check=data=>{if(data?.workspace_id!==workspaceId)throw Error(t('pm_fu_scope_error'));return data;};
  const current=ticket=>{alive();if(actor!==caps()?.actor){retired=true;throw Error(t('pm_fu_actor_changed'));}return ticket===serial&&!retired;};
  const select=label=>h('select',{'aria-label':t(label)});
  const choose=select('pm_fu_history_choose'),snapshotChoice=select('pm_fu_snapshot'),threadChoice=select('pm_fu_thread'),previousChoice=select('pm_fu_compare');
  snapshotChoice.required=true;
  const hidden=value=>h('input',{type:'hidden',value});
  const governance=hidden('[]'),meetings=hidden('[]'),baseline=hidden('');
  const fields={snapshot:snapshotChoice,thread:threadChoice,previous:previousChoice,governance,meetings,_baseline:baseline};
  const onAccessFailure=error=>{if([401,403].includes(error.status)||['PM_FOLLOW_UP_SOURCE_UNAVAILABLE','FORBIDDEN','NOT_FOUND'].includes(error.code))unavailable(error);};
  const assessments=caps()?.features?.pm_workspaces?.follow_up_analysis
    ?followUpAssessments({h,t,api,alive,workspaceId,caps,errorBox,onAccessFailure,pageSize}):null;
  const analysis=assessments?followUpAnalysisRequest({h,t,api,alive,workspaceId,caps,namespace,form,input,labelled,
    permitted:()=>can(),errorBox,onAccessFailure,onSaved:result=>assessments.showResult(result)}):null;
  const json=field=>JSON.parse(field.value||'[]');
  function sourceRefs(refs=[]) {
    return h('ul',{class:'pm-fu-refs'},...refs.map(ref=>h('li',{},...Object.entries(ref).filter(([key,value])=>(key==='id'||key.endsWith('_id')||key.endsWith('_digest'))&&typeof value==='string')
      .map(([key,value])=>{const label='pm_fu_ref_'+key;return h('span',{},t(label)===label?key.replaceAll('_',' '):t(label),t('pm_panel_separator'),code(value));}))));
  }
  function validIntake(row,id=null) {
    if(!row||row.workspace_id!==workspaceId||!/^pmfu_[0-9a-f]{32}$/.test(row.intake_id||'')||id&&row.intake_id!==id
      ||!number(row.sequence)||!number(row.version)||!row.document||!domains.every(key=>row.document.knowledge?.[key])
      ||!Array.isArray(row.document.issues)||!Array.isArray(row.document.agenda)||row.document.agenda.length>3
      ||row.document.agenda.some(item=>!row.document.issues.some(issue=>issue.issue_id===item.issue_id)))throw Error(t('pm_fu_scope_error'));
    return row;
  }
  function updateOptions(node, entries, empty) {
    const saved=node.value;node.replaceChildren(h('option',{value:''},t(empty)),...entries.map(([id,text])=>h('option',{value:id},text)));
    if(saved&&!entries.some(([id])=>id===saved))node.append(h('option',{value:saved},t('pm_fu_saved_selection')+' · '+saved));node.value=saved;
  }
  function renderNavigation() {
    const entries=metadata.map(row=>[row.intake_id,`#${row.version} · ${actorLabel(row.created_by)}`]);
    updateOptions(previousChoice,entries,'pm_fu_no_comparison');
    if(selected&&!entries.some(([id])=>id===selected))entries.push([selected,t('pm_fu_saved_selection')+' · '+selected]);
    updateOptions(choose,entries,'pm_fu_latest');choose.value=selected;
    more.hidden=next==null;more.disabled=reading;
  }
  function clearSources() {
    sourceLoaded=false;sourcePages={};snapshot=null;thread=null;choices.replaceChildren();meetingChoices.replaceChildren();sourceStatus.replaceChildren();
    for(const node of [snapshotChoice,threadChoice,previousChoice])updateOptions(node,[],node===snapshotChoice?'pm_fu_select_snapshot':'pm_fu_none');
  }
  function unavailable(error) {
    assessments?.clear();
    analysis?.clear();
    ++serial;++sourceSerial;loaded=false;reading=false;head=null;plan=null;metadata=[];next=null;
    view.replaceChildren();choose.replaceChildren();more.hidden=true;clearSources();editor?.update();editorFold.hidden=true;
    if(retired){editor?.box.remove();}
    status.replaceChildren(errorBox(error, {within:status}),h('button',{type:'button',class:'ui-button secondary ui-sm',onclick:()=>void refresh()},t('pm_fu_retry')));
  }
  function render(row) {
    view.replaceChildren();if(!row){view.append(emptyState({title:t('pm_fu_empty')}));return;}
    const doc=row.document;
    const summary=h('div',{class:'pm-fu-meta'},h('strong',{},t('pm_fu_version',{version:row.version})),h('span',{},t('pm_fu_prepared_at'),t('pm_panel_separator'),date(row.created_at)),
      h('span',{},t('pm_fu_snapshot_at'),t('pm_panel_separator'),date(doc.snapshot_ref.as_of)));
    const gaps=['projects_truncated','work_truncated','evidence_truncated','bytes_truncated']
      .filter(key=>doc.coverage?.snapshot?.[key]===true).map(key=>'pm_fu_'+key);
    if(doc.coverage?.thread?.truncated)gaps.push('pm_fu_truncated');
    const coverageWarning=gaps.length?h('aside',{'data-pm-fu-partial':''},statusPill('partial'),
      note('pm_fu_partial_help'),h('ul',{},...gaps.map(key=>h('li',{},t(key))))):'';
    const agenda=h('section',{'data-pm-fu-agenda':''},h('h3',{},t('pm_fu_agenda')));
    if(!doc.agenda.length)agenda.append(note('pm_fu_no_agenda'));
    else agenda.append(h('ol',{},...doc.agenda.map(item=>h('li',{class:'pm-fact'},h('strong',{class:'pm-note-text'},item.text),
      h('p',{class:'muted'},t('pm_fu_basis_'+item.ordering_basis)),''))));
    const knowledge=h('section',{'data-pm-fu-knowledge':''},h('h3',{},t('pm_fu_knowledge')));
    const missingBaseline = [];
    const knowledgeSources = h('div');
    for(const domain of domains){
      const entry=doc.knowledge[domain],inputs=entry.baseline_inputs||[],later=entry.supplemental_inputs||[],links=entry.answer_links||[];
      if (!inputs.length) missingBaseline.push(t('pm_fu_domain_'+domain));
      if (!inputs.length && !later.length && !links.length) continue;
      knowledgeSources.append(h('h4',{},t('pm_fu_domain_'+domain)),...inputs.map(value=>h('p',{},code(value.input_id))), ...later.map(value=>h('p',{},code(value.input_id))),sourceRefs(links));
      const item=h('div',{class:'pm-fu-domain','data-pm-fu-domain':domain},h('h4',{},t('pm_fu_domain_'+domain)),h('div',{},
        ...inputs.map(value=>h('p',{class:'pm-note-text'},value.value,' · ',h('span',{class:'muted'},state(value.status)))),
        !inputs.length?statusPill('unknown'):'',
        ...later.map(value=>h('p',{class:'pm-note-text'},h('span',{class:'muted'},t('pm_fu_supplement'),t('pm_panel_separator')),value.value,' · ',state(value.status))),
        links.length?note('pm_fu_answer_recorded'):'',
        ''));knowledge.append(item);
    }
    if (missingBaseline.length) knowledge.append(h('p',{class:'ui-meta','data-pm-fu-missing':''},t('pm_fu_missing_areas',{areas:missingBaseline.join(t('pm_panel_list_separator'))})));
    const issues=fold('pm_fu_all_issues',...(doc.issues||[]).map(issue=>h('article',{class:'pm-fact'},h('h4',{},issue.text),
      h('p',{},state(issue.status)),issue.details?.answer_link?note(issue.kind==='input'?'pm_fu_answer_recorded':'pm_fu_direction_only'):'',
      issue.details?.owner_actor?h('p',{},t('pm_fu_owner'),t('pm_panel_separator'),actorLabel(issue.details.owner_actor)):'',
      issue.details?.adoption?h('p',{},t('pm_fu_prior_adoption'),h('a',{href:`#/op/${encodeURIComponent(issue.details.adoption.operation_id)}`},code(issue.details.adoption.operation_id))):'',
      issue.details?.result?h('p',{class:'pm-note-text'},issue.details.result):'',sourceRefs(issue.source_refs),code(issue.issue_id))));
    const changes=fold('pm_fu_comparison',note('pm_fu_comparison_help'),
      ...['added','changed','unchanged','no_longer_selected'].map(kind=>h('div',{},h('h4',{},t('pm_fu_change_'+kind)),
        ...(doc.changes?.[kind]||[]).map(identifier=>h('p',{},code(identifier))),!doc.changes?.[kind]?.length?note('pm_fu_none'):'')),
      doc.previous_intake_id?code(doc.previous_intake_id):note('pm_fu_no_comparison'));
    const sources=h('div',{},knowledgeSources,...doc.agenda.map(item=>sourceRefs(item.source_refs)),h('p',{},t('pm_fu_snapshot'),code(doc.snapshot_ref.snapshot_id)),
      h('p',{},t('pm_fu_plan'),code(doc.plan_ref.document_revision)),h('p',{},t('pm_fu_scope_revision'),code(doc.plan_ref.scope_revision)),
      h('p',{},t('pm_fu_digest'),code(row.document_digest)),doc.thread_ref?h('p',{},t('pm_fu_thread'),code(doc.thread_ref.thread_id),' · ',doc.thread_ref.version):'',
      ...(doc.selected_governance_refs||[]).map(value=>h('p',{},value.document.title,code(value.revision_id))),
      ...(doc.selected_meeting_follow_up_refs||[]).map(value=>h('p',{},value.follow_up.text,code(value.completion_id),code(value.follow_up_id),
        value.result_event_id?code(value.result_event_id):'',h('span',{class:'muted'},t('pm_fu_original_snapshot')),code(value.original_snapshot_ref.snapshot_id),
        value.result?h('span',{},t('pm_fu_result_snapshot'),code(value.result.document.snapshot_ref.snapshot_id)):'')),
      note('pm_fu_coverage'));
    view.append(summary,statusPill('not_assessed'),...(gaps.length?[statusPill('partial')]:[]),agenda,knowledge,fold('pm_fu_details',note('pm_fu_preparation_only'),coverageWarning,issues,changes,sources));
    if(assessments){view.insertBefore(assessments.element,knowledge);view.insertBefore(analysis.element,knowledge);void assessments.load(row);void analysis.load(row);}
  }
  function stale() {
    if(!baseline.value||head==null||plan==null)return false;
    const value=JSON.parse(baseline.value);
    return value.expected_version!==head||value.expected_plan_version!==plan||value.thread_id!==threadChoice.value
      ||thread&&value.expected_thread_version!==thread.version;
  }
  const can=()=>loaded&&!retired&&actor===caps()?.actor&&permitted(false)
    &&(verifiedRole===null||['owner','contributor'].includes(verifiedRole));
  const ready=()=>can()&&sourceLoaded&&snapshot?.snapshot_id===snapshotChoice.value&&snapshot.baseline?.document
    &&!snapshot.visibility?.filtered&&(!threadChoice.value||thread?.thread_id===threadChoice.value)&&baseline.value&&!stale();
  function bindVersions() {
    if(!editor||editor.locked()||!can()||head==null||plan==null||threadChoice.value&&!thread)return;
    editor.set('_baseline',JSON.stringify({expected_version:head,expected_plan_version:plan,thread_id:threadChoice.value,
      ...(threadChoice.value?{expected_thread_version:thread.version}:{})}));
  }
  function sync() {
    if(!editor)return;
    if(!baseline.value&&can())bindVersions();
    const locked=editor.locked()||!can()||!sourceLoaded||stale();
    choices.querySelectorAll('input,select,button').forEach(node=>{node.disabled=locked;});
    meetingChoices.querySelectorAll('input,select,button').forEach(node=>{node.disabled=locked;});
    rebase.hidden=!baseline.value||!stale();rebase.disabled=editor.locked()||!can();
    versionWarning.hidden=!stale();writeWarning.hidden=!loaded||can();editorFold.hidden=!loaded||!prepareOpen||!can();
    if(analysis)analysis.element.hidden=!can();
    if(launch){launch.hidden=!can();launch.setAttribute('aria-expanded',String(loaded&&prepareOpen));}
  }
  const rebase=h('button',{type:'button',class:'ui-button secondary ui-sm',onclick:()=>{bindVersions();editor.update();}},t('pm_fu_use_versions'));
  const versionWarning=note('pm_fu_stale'),writeWarning=note('pm_panel_read_only');writeWarning.dataset.pmPanelReadonly='';
  const optional=fold('pm_fu_optional',labelled('pm_fu_thread',threadChoice),labelled('pm_fu_compare',previousChoice),choices,meetingChoices);
  const editorFold=h('div',{id:prepareId,hidden:true},sourceStatus,versionWarning,rebase); editorFold.dataset.pmFuEditor='';
  editor=form({action:'pm.follow_up.prepare',target:{workspace_id:workspaceId},fields,
    params:values=>{if(!ready())throw Error(t('pm_fu_source_wait'));return {snapshot_id:values.snapshot,...(values.thread?{thread_id:values.thread}:{}),
      ...(values.previous?{previous_intake_id:values.previous}:{}),governance_revision_ids:JSON.parse(values.governance||'[]'),meeting_follow_up_refs:JSON.parse(values.meetings||'[]')};},
    preconditions:()=>{const {thread_id,...pre}=JSON.parse(baseline.value);return pre;},
    permitted:()=>can()&&sourceLoaded&&!stale(),recoverPermitted:can,label:'pm_fu_save',onStateChange:sync,
    onSaved:async result=>{selected=result.intake_id;await refresh();},
    extra:h('div',{class:'pm-fields'},note('pm_fu_prepare_help'),labelled('pm_fu_snapshot',snapshotChoice),optional,baseline,governance,meetings)});
  editorFold.append(editor.box);
  // Automatic baseline binding alone is not an authored draft.
  prepareOpen=editor.locked()||Boolean(snapshotChoice.value||threadChoice.value||previousChoice.value||json(governance).length||json(meetings).length);
  editor.box.querySelector('.actions').append(button({variant:'secondary',label:t('pm_panel_cancel'),onClick:()=>{prepareOpen=false;sync();if(!launch.hidden)launch.focus();}}));
  const more=h('button',{type:'button',class:'ui-button secondary ui-sm',hidden:true,onclick:()=>void loadMetadata(next)},t('pm_fu_more'));
  launch=button({variant:'primary',label:t('pm_fu_prepare'),'data-pm-follow-up-prepare':'','aria-controls':prepareId,'aria-expanded':'false',onClick:()=>void showPrepare()});
  const element=sectionCard({body:h('div',{},h('div',{class:'pm-heading'},launch,writeWarning,
    iconButton({icon:'refresh-cw', label:t('pm_fu_refresh'),onClick:()=>void refresh()})),
    status,view,fold('pm_fu_history',labelled('pm_fu_history_choose',choose),more),editorFold)});
  element.classList.add('pm-follow-up');element.dataset.pmFollowUp='';
  function metadataPage(data,after) {
    check(data);if(!number(data.head_version)||!Array.isArray(data.intakes)||data.next_cursor!=null&&(!number(data.next_cursor)||data.next_cursor<=after))throw Error(t('pm_fu_scope_error'));
    let cursor=after;for(const row of data.intakes){if(row.workspace_id!==workspaceId||!/^pmfu_[0-9a-f]{32}$/.test(row.intake_id||'')
      ||!number(row.sequence)||row.sequence<=cursor||!number(row.version))throw Error(t('pm_fu_scope_error'));cursor=row.sequence;}
    return data;
  }
  async function loadMetadata(after) {
    if(reading||after==null)return;const ticket=serial;reading=true;more.disabled=true;
    try{const data=metadataPage(await api('GET',`${endpoint}/follow-ups?after=${after}&limit=${pageSize}`),after);if(!current(ticket))return;
      for(const row of data.intakes)if(!metadata.some(value=>value.intake_id===row.intake_id))metadata.push(row);next=data.next_cursor;head=data.head_version;renderNavigation();editor.update();
    }catch(error){if(retiredView(error)||viewLeft(alive))return;if(ticket===serial)unavailable(error);}finally{if(ticket===serial){reading=false;more.disabled=false;}}
  }
  choose.addEventListener('change',()=>{selected=choose.value;void refresh();});
  async function readSelections() {
    const ticket=serial, sourceTicket=++sourceSerial;snapshot=null;thread=null;editor.update();
    try{
      const [snap,discussion]=await Promise.all([snapshotChoice.value?api('GET',`${endpoint}/snapshots/${encodeURIComponent(snapshotChoice.value)}`):null,
        threadChoice.value?api('GET',`${endpoint}/threads/${encodeURIComponent(threadChoice.value)}`):null]);
      if(!current(ticket)||sourceTicket!==sourceSerial)return;
      if(snap){check(snap);if(snap.snapshot?.snapshot_id!==snapshotChoice.value||snap.snapshot.workspace_id!==workspaceId||snap.snapshot.visibility?.filtered)throw Error(t('pm_fu_scope_error'));snapshot=snap.snapshot;}
      if(discussion){if(discussion.thread?.thread_id!==threadChoice.value||discussion.thread.workspace_id!==workspaceId)throw Error(t('pm_fu_scope_error'));thread=discussion.thread;}
      // Explicitly selecting a different thread fixes its current version. Head
      // changes during later refreshes still require the visible rebase action.
      if(baseline.value&&!editor.locked()){
        const saved=JSON.parse(baseline.value);
        if(saved.thread_id!==threadChoice.value){saved.thread_id=threadChoice.value;delete saved.expected_thread_version;if(thread)saved.expected_thread_version=thread.version;editor.set('_baseline',JSON.stringify(saved));}
      }
      sourceStatus.replaceChildren(snapshot&&!snapshot.baseline?.document?note('pm_fu_accepted_required'):note('pm_fu_source_help'));editor.update();
    }catch(error){if(retiredView(error)||viewLeft(alive))return;if(ticket===serial&&sourceTicket===sourceSerial)unavailable(error);}
  }
  snapshotChoice.addEventListener('change',()=>void readSelections());threadChoice.addEventListener('change',()=>void readSelections());
  function selectedGovernance() {
    const section=h('div',{},h('h4',{},t('pm_fu_governance'))), rows=sourcePages.governance?.records||[];
    const saved=json(governance);
    for(const row of rows){
      const box=h('input',{type:'checkbox',checked:saved.includes(row.revision.revision_id)});
      box.addEventListener('change',()=>{if(editor.locked()||!can())return;
        const values=json(governance).filter(id=>id!==row.revision.revision_id);if(box.checked)values.push(row.revision.revision_id);
        if(values.length>20){box.checked=false;return;}editor.set('governance',JSON.stringify(values));});
      section.append(h('label',{class:'pm-fact-heading'},box,h('span',{},row.revision.document.title,' · ',t('pm_fu_version',{version:row.revision.version}))));
    }
    for(const id of saved.filter(id=>!rows.some(row=>row.revision.revision_id===id))){
      const remove=h('button',{type:'button',class:'ui-button secondary ui-sm',onclick:()=>{editor.set('governance',JSON.stringify(json(governance).filter(value=>value!==id)));renderSourceChoices();}},t('pm_fu_remove'));
      section.append(h('p',{},t('pm_fu_saved_selection'),fold('pm_fu_details',code(id)),remove));
    }
    if(!rows.length)section.append(note('pm_fu_none'));return section;
  }
  function renderSourceChoices() {
    updateOptions(snapshotChoice,(sourcePages.snapshots?.records||[]).map(row=>[row.snapshot_id,row.title]),'pm_fu_select_snapshot');
    updateOptions(threadChoice,(sourcePages.threads?.records||[]).map(row=>[row.thread_id,row.title]),'pm_fu_none');
    choices.replaceChildren(selectedGovernance());
    for(const kind of ['snapshots','threads','governance','meetings']){
      const page=sourcePages[kind];if(page?.next!=null)choices.append(h('button',{type:'button',class:'ui-button secondary ui-sm',onclick:()=>void loadSourcePage(kind,page.next)},t('pm_fu_more_'+kind)));
    }
    renderMeetingChoices();editor.update();
  }
  function renderMeetingChoices() {
    meetingChoices.replaceChildren(h('h4',{},t('pm_fu_meetings')));
    const chooser=select('pm_fu_meeting_choose');updateOptions(chooser,(sourcePages.meetings?.records||[]).filter(row=>row.completion_id).map(row=>[row.meeting_id,row.title]),'pm_fu_none');
    const content=h('div');meetingChoices.append(labelled('pm_fu_meeting_choose',chooser),content);
    for(const ref of json(meetings))meetingChoices.append(h('div',{class:'pm-fact'},t('pm_fu_selected_follow_up'),fold('pm_fu_details',sourceRefs([ref])),
      h('button',{type:'button',class:'ui-button secondary ui-sm',onclick:()=>{if(editor.locked()||!can())return;editor.set('meetings',JSON.stringify(json(meetings).filter(item=>item.completion_id!==ref.completion_id||item.follow_up_id!==ref.follow_up_id)));renderMeetingChoices();sync();}},t('pm_fu_remove'))));
    let choiceSerial=0;
    chooser.addEventListener('change',()=>{content.replaceChildren();content.dataset.selection=String(++choiceSerial);if(chooser.value)void loadMeeting(chooser.value,content,content.dataset.selection);});
  }
  async function loadMeeting(id,content,selection) {
    const ticket=serial;content.replaceChildren(note('pm_fu_loading'));
    try{const data=check(await api('GET',`${endpoint}/meetings/${encodeURIComponent(id)}`));if(!current(ticket)||!content.isConnected||content.dataset.selection!==selection)return;
      if(data.meeting?.meeting_id!==id||data.meeting.workspace_id!==workspaceId||data.completion?.meeting_id!==id)throw Error(t('pm_fu_scope_error'));
      if(!Array.isArray(data.follow_up_events)||data.follow_up_events.some(row=>row.meeting_id!==id)||!Array.isArray(data.completion.document?.follow_ups))throw Error(t('pm_fu_scope_error'));
      let events=[...(data.follow_up_events||[])], cursor=data.history_next_cursors?.follow_ups??null;
      const render=()=>{
        content.replaceChildren();
        for(const follow of data.completion.document.follow_ups){
          const result=select('pm_fu_result');updateOptions(result,events.filter(row=>row.follow_up_id===follow.id).map(row=>[row.event_id,state(row.document.status)+' · '+row.document.result]),'pm_fu_original_assignment');
          const add=h('button',{type:'button',class:'ui-button secondary ui-sm',onclick:()=>{if(editor.locked()||!can())return;const values=json(meetings).filter(row=>row.completion_id!==data.completion.completion_id||row.follow_up_id!==follow.id);
            if(values.length>=20)return;values.push({completion_id:data.completion.completion_id,follow_up_id:follow.id,...(result.value?{result_event_id:result.value}:{})});editor.set('meetings',JSON.stringify(values));renderMeetingChoices();sync();}},t('pm_fu_select_follow_up'));
          content.append(h('div',{class:'pm-fact'},h('p',{class:'pm-note-text'},follow.text),labelled('pm_fu_result',result),add));
        }
        if(cursor!=null)content.append(h('button',{type:'button',class:'ui-button secondary ui-sm',onclick:async event=>{
          event.currentTarget.disabled=true;try{const page=check(await api('GET',`${endpoint}/meetings/${id}/history/follow_ups?after=${cursor}&limit=${pageSize}`));
            if(!current(ticket)||!content.isConnected||content.dataset.selection!==selection)return;if(page.meeting_id!==id||!Array.isArray(page.records)||page.next_cursor!=null&&(!number(page.next_cursor)||page.next_cursor<=cursor))throw Error(t('pm_fu_scope_error'));
            if(page.records.some(row=>row.meeting_id!==id))throw Error(t('pm_fu_scope_error'));events.push(...page.records);cursor=page.next_cursor;render();
          }catch(error){if(ticket===serial&&content.isConnected&&content.dataset.selection===selection)unavailable(error);}
        }},t('pm_fu_more_results')));sync();
      };render();
    }catch(error){if(ticket===serial&&content.isConnected&&content.dataset.selection===selection)unavailable(error);}
  }
  async function loadSourcePage(kind,after=null) {
    const ticket=serial;
    try{const data=check(await api('GET',`${endpoint}/${kind}?${new URLSearchParams({limit:String(pageSize),...(after!=null?{after:String(after)}:{})})}`));
      if(!current(ticket))return;const records=data[kind==='governance'?'records':kind];
      if(!Array.isArray(records)||data.next_cursor!=null&&(kind==='threads'?typeof data.next_cursor!=='string'||after!=null&&data.next_cursor<=after:!number(data.next_cursor)||data.next_cursor<=(after??-1)))throw Error(t('pm_fu_scope_error'));
      if(records.some(row=>(kind==='governance'?row.record?.workspace_id:row.workspace_id)!==workspaceId))throw Error(t('pm_fu_scope_error'));
      sourcePages[kind]={records:[...(after!=null?sourcePages[kind]?.records||[]:[]),...records],next:data.next_cursor};renderSourceChoices();return true;
    }catch(error){if(retiredView(error)||viewLeft(alive))return false;if(ticket===serial)unavailable(error);return false;}
  }
  async function loadSources() {
    const ticket=serial;sourceStatus.replaceChildren(note('pm_fu_loading'));
    try{const complete=await Promise.all(['snapshots','threads','governance','meetings'].map(kind=>loadSourcePage(kind)));
      if(!current(ticket)||!complete.every(Boolean))return;sourceLoaded=true;await readSelections();
    }catch{/* Each original source failure clears the complete stale view. */}
  }
  async function showPrepare() {
    if(!loaded)return;
    prepareOpen=true;sync();
    if(!sourceLoaded)await loadSources();
    if(loaded){snapshotChoice.focus();editor.box.scrollIntoView({block:'nearest'});}
  }
  async function verifyWorkspace(ticket) {
    const data=await api('GET',endpoint);if(!current(ticket))return false;
    const member=data.workspace?.membership;
    if(data.workspace?.workspace_id!==workspaceId||member?.actor!==actor||!['owner','contributor','reader'].includes(member.role))throw Error(t('pm_fu_scope_error'));
    verifiedRole=member.role;return true;
  }
  async function refresh() {
    assessments?.clear();
    analysis?.clear();
    const ticket=++serial;++sourceSerial;loaded=false;reading=false;view.replaceChildren();metadata=[];next=null;choose.replaceChildren();more.hidden=true;clearSources();editor.update();
    status.replaceChildren(note('pm_fu_loading'));
    try{
      if(retired||actor!==caps()?.actor){retired=true;throw Error(t('pm_fu_actor_changed'));}
      const listPath=`${endpoint}/follow-ups?after=0&limit=${pageSize}`, planPath=`${endpoint}/plans/head`;
      const detailPath=selected?`${endpoint}/follow-ups/${encodeURIComponent(selected)}`:`${endpoint}/follow-ups/latest`;
      let [page,planData,result]=await Promise.all([api('GET',listPath),api('GET',planPath),
        api('GET',detailPath).then(value=>({value}),error=>({error}))]);
      if(!current(ticket))return;
      const sourceUnavailable=result.error?.code==='PM_FOLLOW_UP_SOURCE_UNAVAILABLE';
      if(result.error&&!sourceUnavailable)throw result.error;
      // A missing source is different from missing workspace authority. Only
      // after rechecking membership may fresh readable metadata restore the
      // navigation and preparation form. Never substitute an older intake.
      if(sourceUnavailable||verifiedRole!==null){
        if(!await verifyWorkspace(ticket))return;
        [page,planData]=await Promise.all([api('GET',listPath),api('GET',planPath)]);
        if(!current(ticket))return;
      }
      metadataPage(page,0);
      const planHead=planData.head;if(planHead?.workspace_id!==workspaceId||!number(planHead.version))throw Error(t('pm_fu_scope_error'));
      head=page.head_version;plan=planHead.version;metadata=page.intakes;next=page.next_cursor;
      const detail=result.value;
      if(detail){check(detail);if(!number(detail.head_version))throw Error(t('pm_fu_scope_error'));
        // Concurrent newer heads may make this exact request stale; the server
        // CAS remains authoritative and the user explicitly rebases a draft.
        head=Math.max(head,detail.head_version);
        if(detail.intake)validIntake(detail.intake,selected||null);else if(selected)throw Error(t('pm_fu_scope_error'));}
      loaded=true;status.replaceChildren();
      if(sourceUnavailable)view.replaceChildren(inlineError({error:t(selected?'pm_fu_selected_unavailable':'pm_fu_latest_unavailable')}),fold('pm_fu_details',note('pm_fu_readable_history')));
      else render(detail?.intake);
      renderNavigation();editor.update();if(prepareOpen)await loadSources();
    }catch(error){if(retiredView(error)||viewLeft(alive))return;if(ticket===serial)unavailable(error);}
  }
  return {element,box:element,refresh,openRef:async target=>{
    const id=target?.intake_id || target?.follow_up_id;
    if(!/^pmfu_[0-9a-f]{32}$/.test(id||''))return;
    selected=id;await refresh();if(!loaded)return;
    await showPrepare();
  }};
}
