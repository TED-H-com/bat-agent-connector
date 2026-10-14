import {button, disclosure, iconButton, inlineError, menu, statusPill} from './ui/components.js';
import {timeStamp, timeText} from './ui/stamps.js';
import {maintenanceReviewView} from './pm-maintenance-review-view.js';
import {reviewCandidateToIdea} from './pm-review-candidate.js';

// The central retains report facts and decides authority. Forms keep exact original requests.
export function workspaceMaintenanceReview({h,t,api,alive,workspaceId,workspace,caps,permitted,form,input,labelled,errorBox,namespace}) {
  const base=`/pm/workspaces/${encodeURIComponent(workspaceId)}`,prefix='pm.maintenance_review.';
  const navigationKey=`batc.pm.review-navigation.${namespace}.${workspaceId}.${caps()?.actor}`;
  let selected='';try{const value=JSON.parse(localStorage.getItem(navigationKey)||'{}');if(typeof value.review_id==='string')selected=value.review_id;}catch{}
  let model=null,ready=false,ticket=0,forms=[],widgets=[],signature='',dirty=false,editorId=null,output=null,imports=[];
  const status=h('div',{role:'status'}),scheduleView=h('div'),view=h('div'),editors=h('div'),warning=h('p',{class:'muted',hidden:true},t('pmrv_stale'));
  const text=key=>h('p',{class:'muted pm-note-text'},t(key));
  const fold=(key,contents,open=false)=>h('details',{'data-review-fold':key,open},h('summary',{},t(key)),contents);
  const check=value=>{if(value?.workspace_id!==workspaceId)throw Error(t('pmrv_invalid'));return value;};
  const valid=epoch=>{try{alive();return epoch===ticket;}catch{return false;}};
  const readable=()=>ready&&caps()?.scopes?.includes('observe');
  const can=()=>Boolean(readable()&&permitted(false)&&caps()?.scopes?.includes('manage'));
  const role=actor=>workspace()?.members?.find(member=>member.actor===actor)?.role;
  const owner=()=>can()&&role(caps()?.actor)==='owner';
  const date=value=>timeStamp(value, {empty:t('pmrv_unknown')});
  const remember=()=>{try{localStorage.setItem(navigationKey,JSON.stringify({review_id:selected}));}catch{}};
  const choose=h('select',{'aria-label':t('pmrv_choose'),onchange:()=>{selected=choose.value;remember();void refresh(true);}});
  const rebase=button({variant:'secondary',label:t('pmrv_use_versions'),onClick:()=>{if(ready)renderEditors(true);}});
  rebase.hidden=true;
  const about=h('details',{},h('summary',{},t('pmrv_details')),text('pmrv_intro'));
  disclosure({element:about});
  const headingState=h('div',{class:'pm-answer'},h('h3',{},t('pmrv_title')));
  const chooseLabel=labelled('pmrv_choose',choose);chooseLabel.hidden=true;
  const actionHost=h('div',{class:'pm-review-actions'}),formHost=h('div',{class:'pm-review-forms'});
  const actionBoxes=new Map();let shown=null,actionSig='';
  const actionLabel=key=>t({note:'pmrv_note','candidate.record':'pmrv_candidate',complete:'pmrv_complete','follow_up.record':'pmrv_save_followup',prepare:'pmrv_prepare'}[key]);
  function registerForm(key,editor){const node=h('div',{'data-review-form':key},editor.box);node.hidden=true;actionBoxes.set(key,{editor,node});formHost.append(node);if(editor.locked())shown=key;}
  function visibleKeys(){const reviewStatus=model?.detail?.review.status,locked=key=>Boolean(actionBoxes.get(key)?.editor.locked()),keys=[];
    if(reviewStatus==='prepared'||locked('note'))keys.push('note');
    if(reviewStatus==='prepared'||locked('candidate.record'))keys.push('candidate.record');
    if(reviewStatus==='prepared'||locked('complete'))keys.push('complete');
    if((reviewStatus==='completed'&&model?.detail?.completion?.document.follow_ups?.length)||locked('follow_up.record'))keys.push('follow_up.record');
    if(actionBoxes.has('prepare'))keys.push('prepare');return keys;}
  function primaryOf(keys){const reviewStatus=model?.detail?.review.status;
    if(reviewStatus==='prepared'&&owner()&&keys.includes('complete'))return 'complete';
    if(reviewStatus==='prepared'&&keys.includes('note'))return 'note';
    if(reviewStatus==='completed'&&keys.includes('follow_up.record'))return 'follow_up.record';
    return keys.includes('prepare')?'prepare':keys[0]||null;}
  function paintActions(){const lockedKey=[...actionBoxes].find(([,box])=>box.editor.locked())?.[0]||null;if(lockedKey)shown=lockedKey;
    const keys=ready?visibleKeys():[];
    if(ready&&shown&&!keys.includes(shown))shown=null;
    if(shown&&!actionBoxes.has(shown))shown=null;
    const primary=keys.length?primaryOf(keys):null,sig=JSON.stringify([primary,keys,shown,lockedKey,ready]);
    if(sig!==actionSig){actionSig=sig;actionHost.replaceChildren();
      if(primary){const open=key=>{if(lockedKey&&key!==lockedKey)return;shown=key;paintActions();};
        const nodes=[button({variant:'primary',label:actionLabel(primary),'data-review-open':primary,onClick:()=>open(primary)})];
        const rest=keys.filter(key=>key!==primary);
        if(rest.length)nodes.push(menu({button:button({variant:'secondary',label:t('pmrv_more'),'data-review-more':'',icon:'chevron-down',iconPosition:'end'}),items:rest.map(key=>({key,label:actionLabel(key),onSelect:()=>open(key)}))}));
        actionHost.replaceChildren(...nodes);}}
    actionHost.hidden=!primary;
    for(const [key,box] of actionBoxes)box.node.hidden=key!==shown||(!ready&&!box.editor.locked());
    if(lockedKey)actionBoxes.get(lockedKey).node.hidden=false;
    if(!ready)editors.hidden=!lockedKey;}
  function paintHeading(){const review=model?.detail?.review;headingState.replaceChildren();
    if(review)headingState.append(h('h3',{},review.title),statusPill(review.status==='completed'?'done':'in_review'));
    else headingState.append(h('h3',{},t(ready&&model?(model.current.visibility_filtered?'pmrv_hidden':'pmrv_empty'):'pmrv_title')));}
  editors.append(actionHost,formHost);
  const element=h('section',{class:'panel pm-evidence','data-pm-maintenance-review':''},
    h('header',{class:'pm-heading'},headingState,iconButton({icon:'refresh-cw', label:t('pmrv_refresh'), ...{type:'button',onclick:()=>void refresh()}})),
    chooseLabel,status,warning,rebase,editors,scheduleView,view,about);
  const hidden=value=>{const field=h('input',{type:'hidden',value:JSON.stringify(value)});field.dataset.pmDefault=field.value;return field;};
  const lines=value=>value.split('\n').map(value=>value.trim()).filter(Boolean);
  const select=(key,entries,value='')=>{const node=h('select',{'aria-label':t(key)},...entries.map(([id,label])=>h('option',{value:id},label)));node.value=value;node.dataset.pmDefault=value;return node;};
  const reason=()=>input('pmrv_reason',{multiline:true,max:4000});
  const target=()=>({workspace_id:workspaceId,review_id:model.detail.review.review_id});
  const pre=()=>({expected_version:model.detail.review.version});
  const sourceRows=()=>model?.detail?.snapshot.maintenance.report.sources||[];
  const sourceLabel=row=>row.title||t('pmrv_ref_'+row.kind);
  const known=(field,choices)=>{const selected=JSON.parse(field.value||'[]');return selected.every(id=>choices.some(row=>row.id===id));};
  const request=async(...args)=>{const current=ticket;try{return await api(...args);}catch(error){
    if(valid(current)&&([401,403,404].includes(error.status)||['FORBIDDEN','NOT_FOUND'].includes(error.code)))failure(error);throw error;
  }};
  function update(){
    let stale=false;for(const entry of forms){stale ||= !entry.editor.locked()&&entry.stale();entry.editor.update();}
    widgets.forEach(widget=>widget.sync());warning.hidden=rebase.hidden=!stale;rebase.disabled=!ready;choose.disabled=!ready;chooseLabel.hidden=!ready;
    output?.update();
    imports.forEach(item=>item.update());
    paintActions();
  }
  function withdraw(){ticket++;ready=false;model=null;imports.forEach(item=>item.dispose());imports=[];view.replaceChildren();scheduleView.replaceChildren();choose.replaceChildren();editors.hidden=true;status.replaceChildren();output=null;update();}
  function failure(error){withdraw();status.replaceChildren(inlineError({error:t('pmrv_unavailable')}),errorBox(error));paintHeading();}
  function localForm(config){
    const expected=()=>JSON.stringify({preconditions:config.preconditions(),context:config.context?.()??null});
    const baseline=h('input',{type:'hidden',value:expected()});baseline.dataset.pmDefault=baseline.value;
    const stale=()=>ready&&baseline.value!==expected();
    let editor;
    editor=form({...config,api:request,fields:{...config.fields,_baseline:baseline},
      params:values=>{if(config.valid&&!config.valid())throw Error(t('pmrv_selection_required'));return config.params(Object.fromEntries(Object.entries(values).filter(([name])=>name!=='_baseline')));},
      onStateChange:()=>{if(ready&&editor&&!editor.locked()&&config.valid&&!config.valid())editor.box.querySelector('button[type=submit]').disabled=true;widgets.forEach(widget=>widget.sync());},
      preconditions:()=>JSON.parse(baseline.value).preconditions,permitted:()=>!stale()&&config.permitted(),
      recoverPermitted:can,extra:h('div',{},config.extra,baseline),onSaved:async result=>{
        if(config.action===prefix+'prepare'&&result?.review_id){selected=result.review_id;remember();}await refresh(true);
      }});
    editor.box.addEventListener('input',()=>{dirty=true;update();});editor.box.addEventListener('change',()=>{dirty=true;update();});
    forms.push({editor,stale,rebase:()=>{if(!editor.locked())editor.set('_baseline',expected());}});
    return editor;
  }
  function choices(field,getEditor,getChoices,key){
    const holder=h('div',{class:'pm-review-checks'});let previous='',controls=[];
    const sync=()=>{
      const available=getChoices(),chosen=JSON.parse(field.value||'[]'),rows=[...available];
      for(const id of chosen)if(!rows.some(row=>row.id===id))rows.push({id,label:t('pmrv_source_missing'),unavailable:true});
      const signature=JSON.stringify(rows);
      if(signature!==previous){
        previous=signature;controls=[];holder.replaceChildren();
        for(const row of rows){const node=h('input',{type:'checkbox','aria-label':row.label});
          node.addEventListener('input',()=>{const editor=getEditor();if(!editor||editor.locked()||!can()||node.disabled)return;
            const values=JSON.parse(field.value||'[]').filter(value=>value!==row.id);if(node.checked)values.push(row.id);
            field.value=JSON.stringify(values);editor.changed();dirty=true;update();});
          const line=h('div',{class:'pm-review-source'},h('label',{},node,h('span',{},row.label)));
          const shown=row.detail||row.id;
          if(shown){const detail=h('details',{},h('summary',{},t('pmrv_details')),h('p',{class:'mono'},shown));disclosure({element:detail});line.append(detail);}
          holder.append(line);controls.push({node,row});
        }
        if(!rows.length)holder.append(text('pmrv_none'));
      }
      for(const{node,row}of controls){node.checked=chosen.includes(row.id);node.disabled=!can()||Boolean(getEditor()?.locked());}
    };
    const widget={element:fold(key,h('div',{},text('pmrv_source_help'),holder)),sync};widgets.push(widget);return widget;
  }
  function sourceSelect(key,entries,initial=''){
    const node=select(key,entries(),initial);let signature='';
    const sync=()=>{const choices=ready?entries():[],value=node.value,next=JSON.stringify(choices);
      if(signature!==next){signature=next;node.replaceChildren(...choices.map(([id,label])=>h('option',{value:id},label)));
        if(value&&!choices.some(([id])=>id===value))node.append(h('option',{value},t('pmrv_source_missing')));node.value=value;}
    };
    widgets.push({sync});return node;
  }
  function sourceChoices(field,getEditor){return choices(field,getEditor,()=>sourceRows().map(row=>({id:row.source_id,label:sourceLabel(row),detail:row.id})),'pmrv_source_refs');}
  function collection(field,getEditor,definitions,key,limit=10){
    const holder=h('div'),rows=h('div'),add=button({variant:'secondary',label:t('pmrv_'+key)});let controls=[];
    const change=values=>{if(!getEditor()||getEditor().locked()||!can())return;field.value=JSON.stringify(values);getEditor().changed();dirty=true;update();};
    const render=()=>{
      rows.replaceChildren();controls=[];
      for(const item of JSON.parse(field.value||'[]')){
        const fields=definitions.map(def=>{
          const choices=def.choices?.(),value=item[def.name]??'';
          const node=choices?select(def.label,choices,value):input(def.label,{multiline:!def.type,max:def.max||4000,value});
          if(def.type)node.type=def.type;
          if(choices&&value&&!choices.some(([id])=>id===value))node.append(h('option',{value},t('pmrv_source_missing')));
          node.value=value;
          node.addEventListener('input',()=>{const values=JSON.parse(field.value||'[]'),current=values.find(row=>row.id===item.id);if(current){current[def.name]=node.value;change(values);}});
          controls.push(node);return def.type==='datetime-local'?timeChoice(def.label,node):labelled(def.label,node);
        });
        const remove=button({variant:'secondary',label:t('pmrv_remove'),onClick:()=>{change(JSON.parse(field.value||'[]').filter(row=>row.id!==item.id));render();sync();}});
        controls.push(remove);rows.append(h('div',{class:'pm-review-assignment pm-fields'},...fields,remove));
      }
    };
    const sync=()=>{const disabled=!can()||Boolean(getEditor()?.locked());controls.forEach(node=>{node.disabled=disabled;});add.disabled=disabled||JSON.parse(field.value||'[]').length>=limit;};
    add.addEventListener('click',()=>{if(add.disabled)return;const values=JSON.parse(field.value||'[]');change([...values,{id:crypto.randomUUID().replaceAll('-',''),
      ...Object.fromEntries(definitions.map(def=>[def.name,def.choices?.()[0]?.[0]||'']))}]);render();sync();});
    holder.append(rows,add);const widget={element:holder,sync,mount:render};widgets.push(widget);return widget;
  }
  function mutationEditors(){
    const detail=model.detail;
    const context=()=>model?.detail?.snapshot.snapshot_id??null;
    const prepared=()=>can()&&model?.detail?.review.status==='prepared';
    const content=input('pmrv_note_content',{multiline:true,max:8000}),noteReason=reason(),noteRefs=hidden([]);let noteEditor;
    const noteSources=sourceChoices(noteRefs,()=>noteEditor);
    noteEditor=localForm({action:prefix+'note',target:target(),fields:{content,rationale:noteReason,source_refs:noteRefs},preconditions:pre,context,
      permitted:prepared,valid:()=>known(noteRefs,sourceRows().map(row=>({id:row.source_id}))),label:'pmrv_note',
      params:values=>({...values,source_refs:JSON.parse(values.source_refs)}),extra:h('div',{class:'pm-fields'},
        labelled('pmrv_note_content',content),labelled('pmrv_reason',noteReason),noteSources.element)});
    registerForm('note',noteEditor);
    const candidateFields={};for(const[key,label,max]of [['title','candidate_title',200],['problem','problem',4000],['goal_relation','goal_relation',4000],
      ['expected_effect','expected_effect',4000],['options','options',20000],['hypotheses','hypotheses',20000]])candidateFields[key]=input('pmrv_'+label,{multiline:key!=='title',max,required:!['options','hypotheses'].includes(key)});
    candidateFields.rationale=reason();candidateFields.source_refs=hidden([]);candidateFields.public_sources=hidden([]);let candidateEditor;
    const candidateSources=sourceChoices(candidateFields.source_refs,()=>candidateEditor);
    const publicSources=collection(candidateFields.public_sources,()=>candidateEditor,[{name:'url',label:'pmrv_public_url',type:'url',max:2000},
      {name:'title',label:'pmrv_public_title',type:'text',max:200},{name:'checked_at',label:'pmrv_public_date',type:'datetime-local'},
      {name:'claim',label:'pmrv_public_claim',max:4000}],'add_public_source',5);
    candidateEditor=localForm({action:prefix+'candidate.record',target:target(),fields:candidateFields,preconditions:pre,context,
      permitted:prepared,valid:()=>JSON.parse(candidateFields.source_refs.value).length>0&&known(candidateFields.source_refs,sourceRows().map(row=>({id:row.source_id}))),
      label:'pmrv_candidate',params:values=>({...values,options:lines(values.options),hypotheses:lines(values.hypotheses),source_refs:JSON.parse(values.source_refs),
        public_sources:JSON.parse(values.public_sources).map(({id,...source})=>({...source,checked_at:new Date(source.checked_at).getTime()/1000}))}),
      extra:h('div',{class:'pm-fields'},text('pmrv_candidate_help'),...Object.entries(candidateFields).filter(([,node])=>node.type!=='hidden').map(([key,node])=>labelled('pmrv_'+({title:'candidate_title',rationale:'reason'}[key]||key),node)),
        candidateSources.element,fold('pmrv_public_sources',h('div',{},text('pmrv_attributed_source'),publicSources.element)))});
    publicSources.mount();registerForm('candidate.record',candidateEditor);
    const notes=hidden([]),candidates=hidden([]),ack=hidden(false),summary=input('pmrv_summary',{multiline:true,max:8000}),direction=select('pmrv_direction',
      ['continue_maintenance','bounded_repair_proposal','explore_idea','request_planning','revise_policy','pause_proposal','close_proposal','defer'].map(value=>[value,t('pmrv_direction_'+value)]),'defer'),
      directionReason=input('pmrv_direction_reason',{multiline:true,max:4000}),followups=hidden([]),focus=input('pmrv_next_focus',{multiline:true,max:40000,required:false}),completeReason=reason();let completeEditor;
    const events=kind=>(model?.detail?.events||[]).filter(row=>row.kind===kind).map(row=>({id:row.event_id,label:`${row.actor} · ${row.document.content||row.document.title}`}));
    const noteChoices=choices(notes,()=>completeEditor,()=>events('note'),'pmrv_acknowledged_notes'),candidateChoices=choices(candidates,()=>completeEditor,()=>events('candidate.record'),'pmrv_candidates');
    const checkAck=h('input',{type:'checkbox','aria-label':t('pmrv_acknowledge'),oninput:()=>{if(!completeEditor.locked()&&owner()){ack.value=JSON.stringify(checkAck.checked);completeEditor.changed();dirty=true;update();}}});
    const assignments=collection(followups,()=>completeEditor,[{name:'text',label:'pmrv_followup_text'},
      {name:'owner_actor',label:'pmrv_owner',choices:()=>workspace().members.filter(row=>['owner','contributor'].includes(row.role)).map(row=>[row.actor,row.actor])},
      {name:'expected_effect',label:'pmrv_expected_effect'},{name:'verification',label:'pmrv_verification'}],'add_followup');
    widgets.push({sync:()=>{checkAck.checked=JSON.parse(ack.value);checkAck.disabled=!owner()||Boolean(completeEditor?.locked());}});
    completeEditor=localForm({action:prefix+'complete',target:target(),fields:{acknowledged_note_ids:notes,candidate_ids:candidates,discussion_acknowledged:ack,
      discussion_summary:summary,direction,direction_rationale:directionReason,follow_ups:followups,next_observation_focus:focus,rationale:completeReason},preconditions:pre,context,
      permitted:()=>prepared()&&owner(),valid:()=>JSON.parse(ack.value)===true&&JSON.parse(notes.value).length>0&&known(notes,events('note'))&&known(candidates,events('candidate.record'))
        &&JSON.parse(followups.value).every(row=>['owner','contributor'].includes(role(row.owner_actor))),label:'pmrv_complete',
      params:values=>({...values,acknowledged_note_ids:JSON.parse(values.acknowledged_note_ids),candidate_ids:JSON.parse(values.candidate_ids),
        discussion_acknowledged:JSON.parse(values.discussion_acknowledged),follow_ups:JSON.parse(values.follow_ups).map(item=>({...item,source_refs:[]})),next_observation_focus:lines(values.next_observation_focus)}),
      extra:h('div',{class:'pm-fields'},text('pmrv_completion_help'),!owner()?text('pmrv_complete_owner'):'',noteChoices.element,
        h('label',{class:'pm-review-ack'},checkAck,t('pmrv_acknowledge')),labelled('pmrv_summary',summary),labelled('pmrv_direction',direction),
        labelled('pmrv_direction_reason',directionReason),candidateChoices.element,fold('pmrv_followups',assignments.element),
        labelled('pmrv_next_focus',focus),labelled('pmrv_reason',completeReason))});
    assignments.mount();registerForm('complete',completeEditor);
    // Always mount original action targets for lost completion/result receipt recovery.
    const allowedAssignments=()=>model?.detail?.completion?.document.follow_ups?.filter(item=>owner()||item.owner_actor===caps()?.actor)||[];
    const assigned=sourceSelect('pmrv_select_followup',()=>allowedAssignments().map(item=>[item.id,`${item.text} · ${item.owner_actor}`]),allowedAssignments()[0]?.id||''),
      reported=select('pmrv_followup_status',['open','blocked','reported_done'].map(value=>[value,t('pmrv_followup_'+value)]),'open'),result=input('pmrv_result',{multiline:true,max:8000}),followReason=reason(),
      snapshot=sourceSelect('pmrv_result_snapshot',()=>[['',t('pmrv_same_snapshot')],...(model?.snapshots||[]).map(row=>[row.snapshot_id,`${row.title} · ${timeText(row.as_of)}`])]);
    const followEditor=localForm({action:prefix+'follow_up.record',target:target(),fields:{follow_up_id:assigned,status:reported,result,rationale:followReason,snapshot_id:snapshot},preconditions:pre,
      context:()=>model?.detail?.review.completion_id??null,permitted:()=>can()&&model?.detail?.review.status==='completed'&&allowedAssignments().some(item=>item.id===assigned.value)
        ,valid:()=>!snapshot.value||model.snapshots.some(row=>row.snapshot_id===snapshot.value),label:'pmrv_save_followup',
      params:({snapshot_id,...values})=>({...values,...(snapshot_id?{snapshot_id}:{})}),extra:h('div',{class:'pm-fields'},text('pmrv_unassessed'),
        labelled('pmrv_select_followup',assigned),labelled('pmrv_followup_status',reported),labelled('pmrv_result',result),
        labelled('pmrv_result_snapshot',snapshot),labelled('pmrv_reason',followReason))});
    registerForm('follow_up.record',followEditor);
  }
  const localTime=value=>{const date=new Date(value);return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);};
  function timeChoice(key,field){const preview=h('span');const paint=()=>{const ms=new Date(field.value).getTime();preview.replaceChildren(Number.isFinite(ms)?timeStamp(ms/1000):t('pmrv_unknown'));};
    field.addEventListener('input',paint);field.addEventListener('change',paint);paint();
    return h('div',{class:'pm-time-field'},labelled(key,field),h('p',{class:'muted pm-time-shown'},t('pmrv_shown'),' ',preview));}
  function prepareEditor(){
    const title=input('pmrv_report_title',{max:200}),start=input('pmrv_start',{value:localTime(Date.now()-7*86400000)}),end=input('pmrv_end',{value:localTime(Date.now())});
    start.type=end.type='datetime-local';start.dataset.pmDefault=start.value;end.dataset.pmDefault=end.value;
    const previous=sourceSelect('pmrv_previous',()=>[['',t('pmrv_no_previous')],...(model?.reviews||[]).map(row=>[row.review_id,`${row.title} · ${timeText(row.created_at)}`])]);
    const p=()=>({expected_plan_version:model.plan.head.version,expected_review_revision:model.current.revision,
      expected_policy_id:model.policy?.policy_id??null,expected_policy_version:model.policy?.revision??null,
      expected_baseline_id:model.baseline?.baseline_id??null,expected_lifecycle_revision:model.lifecycle.revision});
    const periodValid=()=>{const a=new Date(start.value).getTime(),b=new Date(end.value).getTime();return Number.isFinite(a)&&Number.isFinite(b)&&b>a&&b<=Date.now()&&b-a<=31*86400000;};
    const editor=localForm({action:prefix+'prepare',target:{workspace_id:workspaceId},fields:{title,period_start:start,period_end:end,previous_review_id:previous},
      preconditions:p,permitted:()=>can()&&Boolean(model.policy&&model.baseline),valid:()=>periodValid()&&(!previous.value||model.reviews.some(row=>row.review_id===previous.value)),
      label:'pmrv_prepare',params:values=>({title:values.title,period_start:new Date(values.period_start).getTime()/1000,period_end:new Date(values.period_end).getTime()/1000,
        ...(values.previous_review_id?{previous_review_id:values.previous_review_id}:{})}),
      extra:h('div',{class:'pm-fields'},text('pmrv_prepare_help'),!model.policy||!model.baseline?text('pmrv_context_missing'):'',
        labelled('pmrv_report_title',title),h('div',{class:'capture-fields'},timeChoice('pmrv_start',start),timeChoice('pmrv_end',end)),text('pmrv_period_help'),
        labelled('pmrv_previous',previous))});
    registerForm('prepare',editor);
  }
  function renderEditors(rebase=false){
    const id=model.detail?.review.review_id??null;
    forms.forEach(entry=>entry.editor.dispose?.());forms=[];widgets=[];dirty=false;
    actionBoxes.clear();actionSig='';shown=null;formHost.replaceChildren();editors.replaceChildren(actionHost,formHost);
    if(model.detail)mutationEditors();prepareEditor();
    if(rebase)forms.forEach(entry=>entry.rebase());
    editorId=id;update();
  }
  function eventPage(data,after){
    check(data);if(data.review_id!==model.detail.review.review_id||!Array.isArray(data.events)||typeof data.visibility_filtered!=='boolean'
      ||data.next_cursor!==null&&(!Number.isSafeInteger(data.next_cursor)||data.next_cursor<=after))throw Error(t('pmrv_invalid'));
    const seen=new Set(model.detail.events.map(row=>row.event_id));
    for(const row of data.events){check(row);if(row.review_id!==data.review_id||seen.has(row.event_id))throw Error(t('pmrv_invalid'));seen.add(row.event_id);}
    return data;
  }
  function syncAbout(){const summary=about.querySelector(':scope > summary');about.replaceChildren(summary,text('pmrv_intro'));
    if(!model?.detail)return;
    about.append(text('pmrv_period_help'),text('pmrv_fixed_help'),text('pmrv_samples'),text('pmrv_effort_unknown'));
    if(!model.detail.completion)about.append(text('pmrv_not_completed'));
    if(model.detail.review?.status)about.append(text(model.detail.review.status==='completed'?'pmrv_completed':'pmrv_prepared'));}
  function renderView(){
    imports.forEach(item=>item.dispose());imports=[];view.replaceChildren();output=null;
    syncAbout();paintHeading();
    if(!model.detail)return;
    const candidateInput=event=>{const entry=reviewCandidateToIdea({h,t,api,alive,workspaceId,caps,can,form,labelled,event,errorBox,namespace});imports.push(entry);return entry.element;};
    output=maintenanceReviewView({h,t,api,alive,workspaceId,caps,readable,errorBox,onUnavailable:failure,detail:model.detail,candidateInput});view.append(output.element);syncAbout();
    const detail=model.detail;
    if(detail.visibility_filtered)view.append(text('pmrv_history_filtered'));
    if(detail.next_cursor!==null){
      view.append(text('pmrv_history_partial'));
      const after=detail.next_cursor,epoch=ticket,rid=detail.review.review_id;
      const more=button({variant:'secondary',label:t('pmrv_more_events'),onClick:async()=>{
        more.disabled=true;try{
          const page=await api('GET',`${base}/maintenance-reviews/${rid}/events?after=${after}&limit=20`);if(!valid(epoch)||!more.isConnected)return;
          eventPage(page,after);model.detail.events.push(...page.events);model.detail.next_cursor=page.next_cursor;model.detail.visibility_filtered ||= page.visibility_filtered;
          renderView();update();
        }catch(error){if(valid(epoch))failure(error);}finally{more.disabled=false;}
      }});view.append(more);
    }
  }
  function renderSchedule(data){
    scheduleView.replaceChildren();
    if(data===null){scheduleView.append(text('pmrv_weekly_unavailable'));return;}
    check(data);if(typeof data.visibility_filtered!=='boolean'||data.visibility_filtered&&data.schedule!==null)throw Error(t('pmrv_invalid'));
    const weekly=data.schedule?.weekly_review;
    if(!weekly){scheduleView.append(text(data.visibility_filtered?'pmrv_hidden':'pmrv_weekly_unavailable'));return;}
    if(typeof weekly.execution_available!=='boolean'||weekly.next_due!==null&&!Number.isFinite(weekly.next_due))throw Error(t('pmrv_invalid'));
    const reasons={PM_MAINTENANCE_BUDGET_EXHAUSTED:'capacity',PM_MAINTENANCE_STAGE_STOPPED:'stage',PM_MAINTENANCE_DISABLED:'disabled',
      PM_MAINTENANCE_AUTHORITY_UNAVAILABLE:'authority',PM_MAINTENANCE_CONTROLLER_CHANGED:'authority',
      PM_MAINTENANCE_SOURCES_UNAVAILABLE:'sources',PM_MAINTENANCE_REVIEW_SOURCE_UNAVAILABLE:'sources'};
    scheduleView.append(fold('pmrv_weekly',h('div',{},h('p',{},t('pmrv_weekly_next'),': ',date(weekly.next_due)),
      text(!weekly.execution_available?'pmrv_weekly_unavailable':weekly.blocked_reason?'pmrv_weekly_'+(reasons[weekly.blocked_reason]||'blocked'):'pmrv_weekly_ready'),
      weekly.pending_report?text('pmrv_weekly_pending'):'')));
  }
  async function pages(suffix,property,epoch){
    let after=0,records=[],filtered=false;const ids=new Set();
    for(let count=0;count<20;count++){
      const page=check(await api('GET',`${base}/${suffix}?after=${after}&limit=50`));if(!valid(epoch))return null;
      if(!Array.isArray(page[property])||typeof page.visibility_filtered!=='boolean'||page.next_cursor!==null&&(!Number.isSafeInteger(page.next_cursor)||page.next_cursor<=after))throw Error(t('pmrv_invalid'));
      for(const row of page[property]){check(row);const id=property==='reviews'?row.review_id:row.snapshot_id;
        if(!(property==='reviews'?/^pmrevw_[0-9a-f]{32}$/:/^pmsnap_[0-9a-f]{32}$/).test(id)||ids.has(id))throw Error(t('pmrv_invalid'));ids.add(id);records.push(row);}
      filtered ||= page.visibility_filtered===true;if(page.next_cursor===null)return {records,filtered};after=page.next_cursor;
    }throw Error(t('pmrv_history_bound'));
  }
  async function refresh(force=false){
    const focus=editors.contains(document.activeElement)?document.activeElement:null,selection=focus&&typeof focus.selectionStart==='number'?[focus.selectionStart,focus.selectionEnd]:null;
    const epoch=++ticket;ready=false;editors.hidden=true;view.replaceChildren();scheduleView.replaceChildren();choose.replaceChildren();status.replaceChildren();output=null;update();
    try{
      const [reviews,snapshots,current,policy,baseline,lifecycle,plan,schedule]=await Promise.all([pages('maintenance-reviews','reviews',epoch),pages('snapshots','snapshots',epoch),
        ...['maintenance-reviews/current?history_limit=20','maintenance-policies/current','release-baselines/current','lifecycle','plans/head'].map(path=>api('GET',`${base}/${path}`)),
        caps()?.features?.pm_workspaces?.maintenance_checks?.weekly_review_execution===true?api('GET',`${base}/maintenance-schedule`):Promise.resolve(null)]);
      if(!valid(epoch))return;
      for(const row of [current,policy,baseline,lifecycle])check(row);
      if(!Number.isSafeInteger(current.revision)||typeof current.visibility_filtered!=='boolean'
        ||!Number.isSafeInteger(lifecycle.lifecycle?.revision)||!Number.isSafeInteger(plan.head?.version)
        ||typeof policy.visibility_filtered!=='boolean'||typeof baseline.visibility_filtered!=='boolean'
        ||policy.policy&&(check(policy.policy).policy_id!==policy.head?.policy_id||policy.policy.revision!==policy.head.revision)
        ||baseline.baseline&&check(baseline.baseline).baseline_id!==baseline.head?.baseline_id)throw Error(t('pmrv_invalid'));
      const detail=selected?check(await api('GET',`${base}/maintenance-reviews/${encodeURIComponent(selected)}?history_limit=20`)):current.review?current:null;
      if(!valid(epoch))return;
      if(detail&&(check(detail.review).review_id!==(selected||current.review.review_id)||!Number.isSafeInteger(detail.review.version)
        ||!Array.isArray(detail.events)||detail.next_cursor!==null&&(!Number.isSafeInteger(detail.next_cursor)||detail.next_cursor<0)))throw Error(t('pmrv_invalid'));
      const next=JSON.stringify([detail?.review.review_id,detail?.review.version,current.revision,policy.head,baseline.head,lifecycle.lifecycle.revision,plan.head.version,workspace()?.version]);
      const changed=signature&&next!==signature;
      model={reviews:reviews.records,snapshots:snapshots.records,current,detail,policy:policy.visibility_filtered?null:policy.policy,
        baseline:baseline.visibility_filtered?null:baseline.baseline,lifecycle:lifecycle.lifecycle,plan};ready=true;editors.hidden=false;
      choose.replaceChildren(h('option',{value:''},t('pmrv_current')),...model.reviews.map(row=>h('option',{value:row.review_id},`${row.title} · ${timeText(row.created_at)}`)));
      if(selected&&!model.reviews.some(row=>row.review_id===selected))choose.append(h('option',{value:selected},detail.review.title));choose.value=selected;
      renderView();renderSchedule(schedule);
      if(force||!signature||editorId!==(detail?.review.review_id??null)||changed&&!dirty&&!forms.some(entry=>entry.editor.locked()))renderEditors();
      signature=next;update();
      if(reviews.filtered||snapshots.filtered)status.append(text('pmrv_history_filtered'));
      if(!can())status.append(text('pmrv_write_scope'));
      if(focus?.isConnected&&!focus.disabled&&document.activeElement===document.body){focus.focus({preventScroll:true});if(selection)focus.setSelectionRange(...selection);}
    }catch(error){if(valid(epoch))failure(error);}
  }
  return {element,refresh,withdraw};
}
