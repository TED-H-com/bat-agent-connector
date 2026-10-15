import {button, iconButton, emptyState} from './ui/components.js';
// A saved candidate is a fixed source. Only the destination and topic are chosen here.
export function reviewCandidateToIdea({h,t,api,alive,workspaceId,caps,can,form,labelled,event,errorBox,namespace}) {
  let dead=false,ticket=0,loaded=false,reading=false,thread=null,request=null,rows=[],next=null;
  const ref=event.planning_source,actor=caps()?.actor;
  const valid=ref?.kind==='maintenance_review_candidate'&&ref.review_id===event.review_id&&ref.event_id===event.event_id
    &&ref.document_digest===event.document_digest&&ref.operation_id===event.operation_id
    &&Object.keys(ref).sort().join(',')==='document_digest,event_id,kind,operation_id,review_id';
  const supported=()=>valid&&caps()?.features?.pm_workspaces?.maintenance_review?.planning_candidate_source?.version===1;
  const guard=()=>{alive();if(dead||caps()?.actor!==actor)throw Error(t('pmrv_candidate_unavailable'));};
  const current=n=>{guard();return n===ticket;};
  const key=`${namespace}.candidate-destination.${event.event_id}`;
  let selected='';try{selected=localStorage.getItem(key)||'';}catch{}
  const status=h('div',{role:'status'}),body=h('div'),chooser=h('select',{'aria-label':t('pmrv_idea_choose')}),pages=h('div');
  const refresh=iconButton({icon:'refresh-cw', label:t('pmrv_idea_refresh'), ...{type:'button',class:'secondary',onclick:()=>void load()}});
  const element=h('details',{'data-review-candidate-import':event.event_id},h('summary',{class:'ui-button secondary ui-sm'},t('pmrv_add_to_idea')),
    h('p',{class:'muted'},t('pmrv_idea_help')),labelled('pmrv_idea_choose',chooser),h('div',{class:'actions'},refresh,pages),status,body);
  function controls(){chooser.disabled=reading||Boolean(request?.locked())||!can();refresh.disabled=reading||Boolean(request?.locked());}
  function update(){controls();request?.update();}
  function navigation(){
    chooser.replaceChildren(h('option',{value:''},t('pmrv_idea_choose')),...rows.filter(row=>row.mode==='idea').map(row=>h('option',{value:row.thread_id},row.title)));
    if(selected&&![...chooser.options].some(row=>row.value===selected))chooser.append(h('option',{value:selected},t('pmrv_idea_saved')));
    chooser.value=selected;pages.replaceChildren();
    if(next)pages.append(button({variant:'secondary',size:'sm',label:t('pmrv_idea_more'),onClick:()=>void load(true)}));
    controls();
  }
  async function choose(){
    const n=++ticket;request?.dispose();request=null;thread=null;body.replaceChildren();status.replaceChildren();
    if(!selected)return;reading=true;update();
    try{
      const data=await api('GET',`/pm/workspaces/${workspaceId}/threads/${encodeURIComponent(selected)}`);if(!current(n))return;
      if(data.thread?.workspace_id!==workspaceId||data.thread.thread_id!==selected
        ||!Number.isSafeInteger(data.thread.version))throw Error(t('pmrv_candidate_unavailable'));
      thread=data;
      const slot=h('select',{'aria-label':t('pmrv_idea_topic')},...['governance','scope','schedule','finance','stakeholders','resources','risk','quality'].map(value=>h('option',{value},t('pm_slot_'+value))));
      slot.value='scope';slot.dataset.pmDefault='scope';
      const version=h('input',{type:'hidden',value:String(data.thread.version)});version.dataset.pmDefault=version.value;
      const stale=()=>Number(version.value)!==thread?.thread.version;
      const warning=h('p',{class:'muted'},t('pmrv_idea_stale'));
      const rebase=button({variant:'secondary',label:t('pmrv_idea_use_version'),onClick:()=>{guard();if(request&&!request.locked()&&can()){request.set('version',String(thread.thread.version));sync();request.update();}}});
      const sync=()=>{warning.hidden=rebase.hidden=!stale();rebase.disabled=!can()||Boolean(request?.locked());navigation();};
      request=form({action:'pm.input.record',target:{workspace_id:workspaceId,thread_id:selected},guard,
        namespace:`${namespace}.review-candidate.${event.event_id}`,fields:{slot,version},
        params:values=>({slot:values.slot,value:event.document.title,status:'unresolved',source_ref:ref}),
        preconditions:()=>({expected_version:Number(version.value)}),
        permitted:()=>supported()&&can()&&thread?.capabilities?.write===true&&thread.thread.mode==='idea'&&!stale(),
        recoverPermitted:()=>can()&&thread?.capabilities?.write===true,label:'pmrv_add_to_idea',onStateChange:()=>{if(request)sync();},
        onSaved:()=>{guard();status.replaceChildren(h('p',{},t('pmrv_idea_saved_input')),h('a',{href:`#/pm/${workspaceId}/${selected}`},t('pmrv_idea_open')));},
        extra:h('div',{class:'pm-fields'},labelled('pmrv_idea_topic',slot),version)});
      body.append(thread.thread.mode!=='idea'?h('p',{class:'muted'},t('pmrv_idea_only')):'',request.box,warning,rebase,
        h('a',{href:`#/pm/${workspaceId}/${selected}`},t('pmrv_idea_open')));sync();
    }catch(error){if(n===ticket&&!dead){thread=null;body.replaceChildren();status.replaceChildren(errorBox(error, {within:status}));}}
    finally{reading=false;update();}
  }
  async function load(more=false){
    if(reading||request?.locked()||!supported())return;const n=++ticket;reading=true;update();
    try{
      const after=more?next:null,data=await api('GET',`/pm/workspaces/${workspaceId}/threads?limit=20${after?'&after='+encodeURIComponent(after):''}`);
      if(!current(n))return;
      if(data.workspace_id!==workspaceId||!Array.isArray(data.threads)||data.threads.some(row=>row.workspace_id!==workspaceId)
        ||data.next_cursor!=null&&(!/^pmt_[0-9a-f]{32}$/.test(data.next_cursor)||data.next_cursor===after))throw Error(t('pmrv_candidate_unavailable'));
      rows=more?[...rows,...data.threads.filter(row=>!rows.some(old=>old.thread_id===row.thread_id))]:data.threads;next=data.next_cursor;loaded=true;navigation();
      if(!rows.some(row=>row.mode==='idea')&&!selected)status.replaceChildren(emptyState({title:t('pmrv_idea_empty')}));
      if(selected&&!more)await choose();
    }catch(error){if(n===ticket&&!dead){thread=null;request?.update();status.replaceChildren(errorBox(error, {within:status}));}}
    finally{reading=false;update();}
  }
  chooser.addEventListener('change',()=>{guard();if(request?.locked())return;selected=chooser.value;try{localStorage.setItem(key,selected);}catch{}void choose();});
  element.addEventListener('toggle',()=>{if(element.open&&!loaded)void load();});
  element.hidden=!supported();
  return {element,update,dispose:()=>{dead=true;ticket++;request?.dispose();}};
}

export function reviewCandidateProof({h,t,source}) {
  const d=source.document;
  return h('details',{'data-review-candidate-proof':''},h('summary',{},t('pmrv_idea_original')),
    h('p',{class:'muted'},t('pmrv_idea_original_help')),
    ...['title','problem','goal_relation','expected_effect','rationale'].map(key=>h('p',{class:'pm-note-text'},h('strong',{},t('pmrv_'+({title:'candidate_title',rationale:'reason'}[key]||key))),': ',d[key])),
    h('p',{class:'pm-source'},`${source.kind} · ${d.disposition} · ${source.event_id}`),
    h('details',{},h('summary',{},t('pmrv_proof')),h('pre',{class:'pm-brief-proof'},JSON.stringify(source,null,2))));
}
