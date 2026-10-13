import {emptyState} from './ui/components.js';
import {timeStamp} from './ui/stamps.js';
// One scheduling baseline, explicit forecasts and fixed source observations.
export function workspaceRoadmap({h,t,api,alive,workspaceId,permitted,form,input,labelled,errorBox}) {
  const endpoint=`/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  let current=null, accepted=null, planHead=null, workBindings=[], workTitles={}, serial=0, signature='', baselineForm=null;
  const status=h('div',{role:'status'}), view=h('div'), rows=h('div',{class:'pm-roadmap-editor'});
  const hidden=()=>h('input',{type:'hidden'});
  const title=input('pm_roadmap_name',{max:200,required:false}), rationale=input('pm_roadmap_reason',{max:4000,multiline:true});
  const milestones=hidden(), version=hidden(), planVersion=hidden();
  const pick=row=>Object.fromEntries(['milestone_id','title','plan_revision_id','requirement_ids','work_binding_ids','baseline_date','depends_on'].map(key=>[key,row[key]]));
  const drafts=()=>JSON.parse(milestones.value || '[]');
  const persist=list=>{milestones.value=JSON.stringify(list);milestones.dispatchEvent(new Event('input'));};
  const textDate=value=>value ? timeStamp(value, {dateOnly:true}) : t('pm_roadmap_unknown_date');
  const can=()=>Boolean(current&&planHead)&&permitted(true);
  const lock=()=>{for(const field of rows.querySelectorAll('input,select,button'))field.disabled=milestones.disabled||field.dataset.pmIncompatible==='true';add.disabled=milestones.disabled;};
  const add=h('button',{type:'button',class:'secondary',onclick:()=>{
    if(milestones.disabled||!accepted)return;
    const list=drafts();if(list.length>=100)return;
    list.push({milestone_id:'ms_'+crypto.randomUUID().replaceAll('-','').slice(0,16),title:'',plan_revision_id:accepted.revision_id,
      requirement_ids:[],work_binding_ids:[],baseline_date:null,depends_on:[]});persist(list);renderEditor();
  }},t('pm_roadmap_add'));
  baselineForm=form({action:'pm.roadmap.baseline.set',target:{workspace_id:workspaceId},fields:{title,rationale,milestones,version,planVersion},
    params:values=>({...(values.title.trim()?{title:values.title.trim()}:{}),rationale:values.rationale,milestones:JSON.parse(values.milestones||'[]')}),
    preconditions:()=>({expected_roadmap_version:Number(version.value),expected_plan_version:Number(planVersion.value)}),
    permitted:()=>can()&&Boolean(accepted)&&Boolean(version.value),recoverPermitted:()=>permitted(true),label:'pm_roadmap_save_baseline',
    onStateChange:()=>{
      if(baselineForm&&!baselineForm.locked()&&!milestones.value&&current&&planHead){initializeEditor();baselineForm.update();return;}lock();
    },onSaved:async()=>{await refresh();initializeEditor();},
    extra:h('div',{},h('p',{class:'muted'},t('pm_roadmap_baseline_help')),labelled('pm_roadmap_name',title),rows,add,
      labelled('pm_roadmap_reason',rationale),milestones,version,planVersion)});
  const editor=h('details',{'data-pm-roadmap-editor':''},h('summary',{},t('pm_roadmap_edit')),baselineForm.box);
  function initializeEditor(){
    if(milestones.value){if(!rows.children.length&&drafts().length)renderEditor();return;}
    if(!current||!planHead||baselineForm.locked())return;
    baselineForm.set('milestones',JSON.stringify((current.baseline?.document.milestones||[]).map(pick)));
    baselineForm.set('version',String(current.head.version));baselineForm.set('planVersion',String(planHead.version));
    if(!title.value)baselineForm.set('title',current.baseline?.document.title||'');renderEditor();
  }
  function renderEditor(){
    rows.replaceChildren();let list;
    try{list=drafts();if(!Array.isArray(list))throw Error();}catch{rows.append(h('p',{class:'error'},t('pm_damaged')));return;}
    const renderedBindings=workBindings.slice();
    for(const row of list){
      const name=input('pm_roadmap_milestone',{max:200,value:row.title}),date=h('input',{type:'date','aria-label':t('pm_roadmap_baseline_date'),value:row.baseline_date||''});
      const set=(key,value)=>{const latest=drafts();const match=latest.find(item=>item.milestone_id===row.milestone_id);if(match){match[key]=value;persist(latest);}};
      name.addEventListener('input',()=>set('title',name.value));date.addEventListener('change',()=>set('baseline_date',date.value||null));
      const work=h('div',{class:'pm-roadmap-choices'}),requirements=h('div',{class:'pm-roadmap-choices'}),dependencies=h('div',{class:'pm-roadmap-choices'});
      if(row.plan_revision_id===accepted?.revision_id){
        for(const requirement of accepted.document.requirements||[]){
          const check=h('input',{type:'checkbox',checked:row.requirement_ids.includes(requirement.id)});
          check.addEventListener('change',()=>{set('requirement_ids',check.checked?[...new Set([...drafts().find(item=>item.milestone_id===row.milestone_id).requirement_ids,requirement.id])]
            :drafts().find(item=>item.milestone_id===row.milestone_id).requirement_ids.filter(id=>id!==requirement.id));refreshWorkEligibility();});
          requirements.append(h('label',{},check,' ',requirement.text||requirement.id));
        }
      }else requirements.append(h('p',{class:'muted'},t('pm_roadmap_historical_requirements')));
      for(const binding of workBindings.filter(value=>value.plan_revision_id===row.plan_revision_id)){
        const selected=row.work_binding_ids.includes(binding.binding_id),compatible=!(binding.requirement_ids||[]).length||(binding.requirement_ids||[]).some(id=>row.requirement_ids.includes(id));
        const check=h('input',{type:'checkbox',checked:selected,'data-pm-work-binding':binding.binding_id,'data-pm-incompatible':String(!compatible&&!selected)});
        check.addEventListener('change',()=>{set('work_binding_ids',check.checked?[...new Set([...drafts().find(item=>item.milestone_id===row.milestone_id).work_binding_ids,binding.binding_id])]
          :drafts().find(item=>item.milestone_id===row.milestone_id).work_binding_ids.filter(id=>id!==binding.binding_id));refreshWorkEligibility();});
        work.append(h('label',{},check,' ',workTitles[binding.work_item_id]||binding.work_item_id,compatible?'':h('small',{class:'muted'},t('pm_roadmap_work_scope'))));
      }
      for(const id of row.work_binding_ids.filter(id=>!workBindings.some(binding=>binding.binding_id===id))){
        const check=h('input',{type:'checkbox',checked:true});check.addEventListener('change',()=>{
          const selected=drafts().find(item=>item.milestone_id===row.milestone_id).work_binding_ids;
          set('work_binding_ids',check.checked?[...new Set([...selected,id])]:selected.filter(value=>value!==id));
        });work.append(h('label',{},check,' ',t('pm_roadmap_historical_work'),h('code',{},id)));
      }
      for(const id of row.depends_on.filter(id=>!list.some(item=>item.milestone_id===id))){
        const check=h('input',{type:'checkbox',checked:true});check.addEventListener('change',()=>{
          const selected=drafts().find(item=>item.milestone_id===row.milestone_id).depends_on;
          set('depends_on',check.checked?[...new Set([...selected,id])]:selected.filter(value=>value!==id));
        });dependencies.append(h('label',{},check,' ',t('pm_roadmap_missing_dependency'),h('code',{},id)));
      }
      function refreshWorkEligibility(){
        const selected=drafts().find(item=>item.milestone_id===row.milestone_id);
        for(const check of work.querySelectorAll('[data-pm-work-binding]')){
          const binding=renderedBindings.find(item=>item.binding_id===check.dataset.pmWorkBinding);
          const compatible=!(binding.requirement_ids||[]).length||binding.requirement_ids.some(id=>selected.requirement_ids.includes(id));
          check.dataset.pmIncompatible=String(!compatible&&!selected.work_binding_ids.includes(binding.binding_id));
        }lock();
      }
      for(const other of list.filter(item=>item.milestone_id!==row.milestone_id)){
        const check=h('input',{type:'checkbox',checked:row.depends_on.includes(other.milestone_id)});
        check.addEventListener('change',()=>set('depends_on',check.checked?[...new Set([...drafts().find(item=>item.milestone_id===row.milestone_id).depends_on,other.milestone_id])]
          :drafts().find(item=>item.milestone_id===row.milestone_id).depends_on.filter(id=>id!==other.milestone_id)));
        dependencies.append(h('label',{},check,' ',other.title||t('pm_roadmap_untitled')));
      }
      const remove=h('button',{type:'button',class:'secondary',onclick:()=>{
        if(milestones.disabled)return;persist(drafts().filter(item=>item.milestone_id!==row.milestone_id));renderEditor();
      }},t('pm_roadmap_remove'));
      rows.append(h('fieldset',{'data-pm-milestone-editor':row.milestone_id},labelled('pm_roadmap_milestone',name),
        labelled('pm_roadmap_baseline_date',date),h('details',{},h('summary',{},t('pm_roadmap_work')),work),h('details',{},h('summary',{},t('pm_roadmap_requirements')),requirements),
        h('details',{},h('summary',{},t('pm_roadmap_dependencies')),dependencies),remove));
    }lock();
  }
  const resetEditor=h('button',{type:'button',class:'secondary',onclick:()=>{
    if(baselineForm.locked()||!can())return;baselineForm.set('title',current.baseline?.document.title||'');baselineForm.set('milestones','');initializeEditor();
  }},t('pm_roadmap_use_current'));editor.append(resetEditor);
  // Forecasts amend one selected milestone; other forecasts retain their own explicit values.
  const choice=h('select',{'aria-label':t('pm_roadmap_milestone'),required:true}),forecastDate=h('input',{type:'date','aria-label':t('pm_roadmap_forecast_date')}),
    forecastReason=input('pm_roadmap_reason',{max:4000,multiline:true});
  const forecastBaseline=hidden(),forecastVersion=hidden();
  let forecast;forecast=form({action:'pm.roadmap.forecast.set',target:{workspace_id:workspaceId},fields:{milestone:choice,date:forecastDate,rationale:forecastReason,baseline:forecastBaseline,version:forecastVersion},
    params:values=>({baseline_revision_id:values.baseline,rationale:values.rationale,
      updates:[{milestone_id:values.milestone,forecast_date:values.date||null}]}),
    preconditions:()=>({expected_roadmap_version:Number(forecastVersion.value)}),permitted:()=>can()&&Boolean(current?.baseline),
    recoverPermitted:()=>permitted(true),label:'pm_roadmap_save_forecast',onStateChange:()=>{if(initializeAction(forecast,forecastBaseline))forecast.update();},onSaved:()=>refresh(),
    extra:h('div',{},h('p',{class:'muted'},t('pm_roadmap_forecast_help')),labelled('pm_roadmap_milestone',choice),
      labelled('pm_roadmap_forecast_date',forecastDate),labelled('pm_roadmap_reason',forecastReason))});
  forecast.box.append(h('button',{type:'button',class:'secondary',onclick:()=>{
    if(!current?.baseline||forecast.locked())return;forecast.set('baseline',current.head.baseline_revision_id);forecast.set('version',String(current.head.version));
  }},t('pm_roadmap_use_current')));
  const snapshot=h('select',{'aria-label':t('pm_roadmap_snapshot'),required:true});
  const observationBaseline=hidden(),observationVersion=hidden();
  let observe;observe=form({action:'pm.roadmap.observe',target:{workspace_id:workspaceId},fields:{snapshot,baseline:observationBaseline,version:observationVersion},
    params:values=>({baseline_revision_id:values.baseline,snapshot_id:values.snapshot}),
    preconditions:()=>({expected_roadmap_version:Number(observationVersion.value)}),permitted:()=>Boolean(current?.baseline)&&permitted(false),
    recoverPermitted:()=>permitted(false),label:'pm_roadmap_save_observation',onStateChange:()=>{if(initializeAction(observe,observationBaseline))observe.update();},onSaved:()=>refresh(),
    extra:h('div',{},h('p',{class:'muted'},t('pm_roadmap_observation_help')),labelled('pm_roadmap_snapshot',snapshot))});
  snapshot.addEventListener('change',()=>observe.update());
  observe.box.append(h('button',{type:'button',class:'secondary',onclick:()=>{
    if(!current?.baseline||observe.locked())return;observe.set('baseline',current.head.baseline_revision_id);observe.set('version',String(current.head.version));
  }},t('pm_roadmap_use_current')));
  const box=h('section',{class:'pm-roadmap','data-pm-roadmap':''},h('h2',{},t('pm_roadmap_title')),h('p',{class:'muted'},t('pm_roadmap_intro')),
    status,view,editor,h('details',{},h('summary',{},t('pm_roadmap_update_forecast')),forecast.box),
    h('details',{},h('summary',{},t('pm_roadmap_update_observation')),observe.box));
  const historyBody=h('div'),history=h('details',{'data-pm-roadmap-history':''},h('summary',{},t('pm_roadmap_history')),historyBody);box.append(history);
  let historyLoaded=false,historyBusy=false,historyVersion=null;history.addEventListener('toggle',()=>{if(history.open&&!historyLoaded)void loadHistory();});
  async function loadHistory(after=0){
    if(historyBusy)return false;historyBusy=true;
    const expectedVersion=current?.head.version;
    try{
      const page=await api('GET',`${endpoint}/roadmap/revisions?after=${after}&limit=20`);alive();
      if(page.workspace_id!==workspaceId)throw Error(t('pm_scope_mismatch'));
      if(!history.isConnected)return false;
      if(!after)historyBody.replaceChildren();historyLoaded=true;historyVersion=expectedVersion;
      for(const entry of page.revisions||[]){
        const content=h('div'),detail=h('details',{},h('summary',{},t('pm_roadmap_kind_'+entry.kind),' · ',timeStamp(entry.created_at)),content);
        let fetched=false;detail.addEventListener('toggle',async()=>{
          if(!detail.open||fetched)return;fetched=true;
          try{const value=await api('GET',`${endpoint}/roadmap/revisions/${entry.revision_id}`);alive();if(!detail.isConnected)return;
            if(value.workspace_id!==workspaceId||value.revision.revision_id!==entry.revision_id)throw Error(t('pm_scope_mismatch'));
            const doc=value.revision.document;content.replaceChildren(h('p',{},doc.rationale||t('pm_roadmap_quality_unknown')),
              h('pre',{class:'pm-brief-proof'},JSON.stringify(value.revision,null,2)));
          }catch(error){content.replaceChildren(errorBox(error));fetched=false;}
        });historyBody.append(detail);
      }
      if(page.next_cursor!==null&&page.next_cursor!==undefined){
        const more=h('button',{type:'button',class:'secondary',onclick:async()=>{more.disabled=true;if(await loadHistory(page.next_cursor))more.remove();else more.disabled=false;}},t('pm_roadmap_more_history'));historyBody.append(more);
      }
      return true;
    }catch(error){if(history.isConnected)historyBody.append(errorBox(error));return false;}
    finally{historyBusy=false;if(history.open&&current&&expectedVersion!==current.head.version)void loadHistory();}
  }
  function initializeAction(editor,baseField){
    if(current?.baseline&&editor&&!baseField.value&&!editor.locked()){
      editor.set('baseline',current.head.baseline_revision_id);editor.set('version',String(current.head.version));return true;
    }return false;
  }
  function render(){
    const base=current.baseline?.document;
    if(!base){view.replaceChildren(emptyState({title:t('pm_roadmap_empty')}));return;}
    const table=h('table',{class:'pm-roadmap-table'},h('thead',{},h('tr',{},...['milestone','baseline_date','forecast_date','actual'].map(name=>h('th',{scope:'col'},t('pm_roadmap_'+name))))));
    const body=h('tbody');table.append(body);
    for(const milestone of base.milestones){
      const predicted=current.forecast?.document.milestones.find(row=>row.milestone_id===milestone.milestone_id);
      const observed=current.observation?.document.milestones.find(row=>row.milestone_id===milestone.milestone_id);
      const dependencies=milestone.depends_on.map(id=>base.milestones.find(item=>item.milestone_id===id)?.title||id);
      const source=h('details',{},h('summary',{},t('pm_roadmap_sources')),
        h('p',{class:'muted'},t('pm_roadmap_quality_unknown')),h('pre',{class:'pm-brief-proof'},JSON.stringify({plan_revision_id:milestone.plan_revision_id,
          requirement_ids:milestone.requirement_ids,work_binding_ids:milestone.work_binding_ids,
          current_baseline_revision_id:current.head.baseline_revision_id,current_forecast_revision_id:current.head.forecast_revision_id,
          original_baseline_date:milestone.original_baseline_date,first_baseline_revision_id:milestone.first_baseline_revision_id,
          observation_context:current.observation?Object.fromEntries(['baseline_revision_id','forecast_revision_id','snapshot_id','snapshot_digest','as_of','journal_cursor'].map(key=>[key,current.observation.document[key]])):null,
          observation:observed||null},null,2)));
      body.append(h('tr',{'data-pm-milestone':milestone.milestone_id},h('th',{scope:'row'},milestone.title,
        dependencies.length?h('small',{class:'muted pm-source'},t('pm_roadmap_after',{items:dependencies.join(', ')})):''),
        h('td',{},textDate(milestone.baseline_date)),h('td',{},textDate(predicted?.forecast_date)),
        h('td',{},t(observed?'pm_roadmap_quality_unknown':'pm_roadmap_no_observation'),source)));
    }
    view.replaceChildren(h('p',{},h('strong',{},base.title),h('small',{class:'muted pm-source'},base.rationale)),h('div',{class:'pm-roadmap-scroll'},table));
  }
  function options(select,items){
    const selected=select.value;select.replaceChildren(h('option',{value:''},t('pm_roadmap_choose')),
      ...items.map(([value,label])=>h('option',{value},label)));
    if(selected&&!items.some(([value])=>value===selected))select.append(h('option',{value:selected},t('pm_roadmap_saved_choice')));
    select.value=selected;
  }
  async function refresh(){
    const ticket=++serial;historyLoaded=false;historyVersion=null;historyBody.replaceChildren();
    try{
      const [data,plan,snapshots,work]=await Promise.all([api('GET',`${endpoint}/roadmap`),api('GET',`${endpoint}/plans/accepted`),api('GET',`${endpoint}/snapshots?limit=100`),api('GET',`${endpoint}/work?limit=100`)]);alive();if(ticket!==serial)return;
      if(data.workspace_id!==workspaceId||plan.head.workspace_id!==workspaceId||snapshots.workspace_id!==workspaceId||work.workspace_id!==workspaceId)throw Error(t('pm_scope_mismatch'));
      current=data;accepted=plan.plan;planHead=plan.head;workBindings=work.bindings||[];status.replaceChildren();
      for(const projectId of [...new Set(workBindings.map(binding=>binding.project_id))]){
        const items=await api('GET',`/work-items?project_id=${encodeURIComponent(projectId)}&limit=100`);alive();if(ticket!==serial)return;
        for(const item of items.work_items||[])workTitles[item.work_item_id]=item.title;
      }
      if(work.next_cursor!==null&&work.next_cursor!==undefined||snapshots.next_cursor!==null&&snapshots.next_cursor!==undefined)
        status.append(h('p',{class:'muted'},t('pm_partial')));
      for(const [editor,baseField] of [[forecast,forecastBaseline],[observe,observationBaseline]]){
        if(data.baseline&&!baseField.value&&!editor.locked()){editor.set('baseline',data.head.baseline_revision_id);editor.set('version',String(data.head.version));}
      }
      const next=JSON.stringify([data,plan,snapshots,work]);if(signature!==next){signature=next;render();
        options(choice,(data.baseline?.document.milestones||[]).map(row=>[row.milestone_id,row.title]));
        options(snapshot,(snapshots.snapshots||[]).map(row=>[row.snapshot_id,row.title]));initializeEditor();}
      baselineForm.update();forecast.update();observe.update();
      if(historyVersion!==data.head.version){historyLoaded=false;if(history.open)void loadHistory();}
    }catch(error){if(ticket===serial){current=null;view.replaceChildren();historyBody.replaceChildren();historyLoaded=false;status.replaceChildren(errorBox(error));baselineForm.update();forecast.update();observe.update();}}
  }
  return{box,refresh};
}
