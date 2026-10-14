import {button, disclosure, iconButton, inlineError, statusPill} from './ui/components.js';
import {timeStamp, timeText} from './ui/stamps.js';
// Release selection records a fixed source. It does not publish or authorize Maintain.
export function workspaceReleaseBaselines({h,t,api,alive,workspaceId,workspace,caps,permitted,form,input,labelled,errorBox,namespace}) {
  const endpoint=`/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  const action='pm.release_baseline.register', storage=`batc.pm.${namespace}.release-selection.${workspaceId}`;
  let epoch=0, selectionEpoch=0, ready=false, head=null, bindings=[], bindingCursor=null, choices=[], choiceCursor=null, preview=null;
  let currentEditor=null, selectedKey='', historyLoaded=false, historyCursor=null, historyFiltered=false, choicesFiltered=false, choicesIneligible=false, reading=false;
  const editors=new Map();
  const view=h('div',{'data-release-current':''}), history=h('div'), status=h('div',{role:'status'});
  const sourceStatus=h('div',{role:'status'}), sourceView=h('div'), previewView=h('div'), editorView=h('div');
  const repository=h('select',{'aria-label':t('prb_repository')}), kind=h('select',{'aria-label':t('prb_kind')},
    h('option',{value:'github_release'},t('prb_github_release')),h('option',{value:'deployment'},t('prb_deployment')));
  const branch=input('prb_branch',{required:false,max:255});
  let draft={repository:'',kind:'github_release',branch:''};
  try {const saved=JSON.parse(localStorage.getItem(storage));if(saved&&['repository','kind','branch'].every(key=>typeof saved[key]==='string'))draft=saved;}catch{ /* selector preferences are not authority */ }
  repository.value=draft.repository;kind.value=['github_release','deployment'].includes(draft.kind)?draft.kind:'github_release';branch.value=draft.branch;
  const persist=()=>{alive();draft={repository:repository.value,kind:kind.value,branch:branch.value};localStorage.setItem(storage,JSON.stringify(draft));};
  const owner=()=>permitted(true)&&workspace()?.membership?.role==='owner'&&caps()?.scopes?.includes('manage');
  const discovery=()=>caps()?.features?.pm_workspaces?.release_baselines?.source_choices===true&&caps()?.features?.pm_workspaces?.release_baselines?.source_preview===true;
  const allowed=()=>discovery()&&owner()&&caps()?.actions?.some(row=>row.action===action&&row.allowed);
  const valid=ticket=>{try{alive();return ticket===epoch;}catch{return false;}};
  const check=data=>{if(data?.workspace_id!==workspaceId)throw Error(t('prb_invalid'));return data;};
  const stamp=value=>timeStamp(value);
  const query=params=>new URLSearchParams(Object.entries(params).filter(([,value])=>value!==null&&value!==undefined)).toString();
  const bindingKey=(row,name)=>JSON.stringify([row.project_binding_id,name]);
  const selectedBinding=()=>bindings.flatMap(row=>row.repositories.map(name=>({row,name,key:bindingKey(row,name)}))).find(item=>item.key===repository.value);
  const selection=()=>JSON.stringify([repository.value,kind.value,selectedKey,branch.value]);
  const choose=button({variant:'secondary',label:t('prb_load_sources'),onClick:()=>void readChoices(false)});
  const moreBindings=button({variant:'secondary',label:t('prb_more_bindings'),onClick:()=>void readBindings(true)});
  moreBindings.hidden=true;
  const readPreview=button({variant:'secondary',label:t('prb_preview'),onClick:()=>void readProof()});
  const usePreview=button({variant:'secondary',label:t('prb_use_preview'),onClick:()=>{
    if(!currentEditor||currentEditor.locked()||!preview||!allowed())return;
    currentEditor.set('registration',JSON.stringify(preview.registration));currentEditor.update();
  }});
  usePreview.hidden=true;
  const historyDetails=disclosure({element:h('details',{},h('summary',{},t('prb_history')),history)});
  historyDetails.addEventListener('toggle',()=>{if(historyDetails.open&&!historyLoaded&&ready)void readHistory(false);});
  const editorDetails=disclosure({element:h('details',{'data-release-register':''},h('summary',{},t('prb_register')),
    h('p',{class:'muted'},t('prb_source_help')),h('div',{class:'pm-fields'},labelled('prb_repository',repository),moreBindings,labelled('prb_kind',kind)),
    choose,sourceStatus,sourceView,labelled('prb_branch',branch),h('p',{class:'muted'},t('prb_branch_help')),
    readPreview,previewView,usePreview,editorView)});
  const permission=h('p',{class:'muted'},t('prb_owner'));
  const headingState=h('div',{class:'pm-answer'},h('h3',{},t('prb_title')));
  const element=h('section',{class:'pm-brief','data-pm-release-baselines':''},h('header',{class:'pm-heading'},
    headingState,iconButton({icon:'refresh-cw', label:t('prb_refresh'), ...{type:'button',onclick:()=>void refresh()}})),status,view,historyDetails,editorDetails);
  function controls(){
    const can=ready&&allowed();
    for(const field of [repository,kind,branch])field.disabled=!can||reading;
    choose.disabled=!can||reading||!selectedBinding();moreBindings.disabled=!can||reading;
    readPreview.disabled=!can||reading||!selectedKey;
    permission.hidden=Boolean(owner()&&discovery());permission.textContent=t(owner()?'prb_discovery_unavailable':'prb_owner');editorDetails.hidden=!owner()||!discovery();
    for(const editor of editors.values())editor.update();
  }
  function resetSources(){selectionEpoch++;selectedKey='';choices=[];choiceCursor=null;choicesFiltered=false;choicesIneligible=false;preview=null;sourceView.replaceChildren();previewView.replaceChildren();usePreview.hidden=true;}
  function withdraw(){
    epoch++;selectionEpoch++;ready=false;head=null;reading=false;historyLoaded=false;historyCursor=null;
    bindings=[];bindingCursor=null;repository.replaceChildren(h('option',{value:''},t('prb_choose_repository')));moreBindings.hidden=true;
    view.replaceChildren();history.replaceChildren();sourceStatus.replaceChildren();resetSources();headingState.replaceChildren(h('h3',{},t('prb_title')));controls();
  }
  function failure(error){withdraw();status.replaceChildren(inlineError({error:t('prb_unavailable')}),errorBox(error));}
  const editorApi=async(...args)=>{
    const ticket=epoch;
    try{return await api(...args);}catch(error){if(valid(ticket)&&[401,403,404].includes(error.status))failure(error);throw error;}
  };
  function fixedDetails(document,proof,metadata=null){
    const details=h('details',{},h('summary',{},t('prb_sources')));
    const source=proof?.source||{};
    for(const [label,value] of [[t('prb_commit'),document.commit_sha],[t('prb_observed'),stamp(proof?.observed_at)],
      [t('prb_published'),source.published_at?stamp(source.published_at):null],[t('prb_fact_repository'),document.repository],[t('prb_fact_baseline'),metadata?.baseline_id],
      [t('prb_fact_digest'),metadata?.document_digest],[t('prb_fact_source'),source.kind],[t('prb_fact_release'),source.release_id],[t('prb_fact_deployment'),source.deployment_id]]) {
      if(value!==null&&value!==undefined)details.append(h('p',{},h('strong',{},`${label}: `),h('span',{class:'mono'},String(value))));
    }
    if(metadata?.operation_id)details.append(h('a',{href:`#/op/${encodeURIComponent(metadata.operation_id)}`},t('pm_operation')));
    const maintenance=proof?.maintenance_branch;
    details.append(h('p',{class:'muted'},t(maintenance?.status==='verified_at_observation'?'prb_branch_verified':'prb_branch_none')));
    if(maintenance?.ref)details.append(h('p',{class:'mono'},maintenance.ref),h('p',{class:'mono'},maintenance.head_sha));
    disclosure({element:details});
    return details;
  }
  const fact=(label,tone)=>h('span',{class:'pm-baseline-fact'},h('span',{},t(label)),statusPill(tone));
  function baselineRow(row,current=false){
    if(row.workspace_id!==workspaceId||row.document?.workspace_id!==workspaceId)throw Error(t('prb_invalid'));
    const doc=row.document,delivery=doc.delivery_status||{};
    const details=fixedDetails(doc,doc.proof,row);
    details.append(h('p',{},t(delivery.github_release==='published'?'prb_gh_published':'prb_gh_unknown')),
      h('p',{},t('prb_registry')),h('p',{},t(delivery.deployment==='verified'?'prb_dep_verified':'prb_dep_unknown')));
    if(delivery.deployment==='verified')details.append(h('p',{class:'muted'},t('prb_not_live')));
    details.append(h('p',{class:'muted'},t('prb_quality')));
    const facts=()=>h('div',{class:'pm-baseline-facts'},
      fact('prb_github_release',delivery.github_release==='published'?'adopted':'not_assessed'),
      fact('prb_registry_short','not_assessed'),
      fact('prb_dep_name',delivery.deployment==='verified'?'deployed':'not_assessed'),
      fact('prb_acceptance','not_assessed'));
    // Quality stays unassessed until acceptance is verified. Channel receipts do not promote the selection.
    const overall=doc.quality?.functional_acceptance_verified===true?'accepted':doc.quality?.status==='unassessed'||doc.quality?.status==='not_assessed'?'not_assessed':'unknown';
    if(current){details.append(h('p',{class:'muted'},t('prb_intro')),permission);
      headingState.replaceChildren(h('h3',{},t('prb_current')),statusPill(overall));}
    return h('article',{'data-release-baseline':row.baseline_id},
      current?null:h('div',{class:'pm-answer'},h('h3',{},`${t('prb_version')} ${row.version}`)),
      current?facts():null,
      h('p',{},h('strong',{},doc.release_version?.value||doc.commit_sha),' · ',doc.repository,' · ',doc.channel),
      h('p',{class:'muted'},t('prb_version'), ' ', row.version, ' · ', stamp(row.created_at)),
      current?null:facts(),
      doc.intent?.rationale?h('p',{},doc.intent.rationale):null,
      ...(doc.quality?.known_limitations||[]).map(item=>h('p',{},`${t('prb_limit_claim')}: ${item.text}`)),details);
  }
  function renderCurrent(data){
    check(data);if(!Number.isSafeInteger(data.head?.version))throw Error(t('prb_invalid'));
    head=data.head;
    if(data.baseline)view.replaceChildren(baselineRow(data.baseline,true));
    else{const details=disclosure({element:h('details',{},h('summary',{},t('prb_note')),h('p',{class:'muted'},t('prb_intro')),permission)});
      view.replaceChildren(details);headingState.replaceChildren(h('h3',{},t(data.visibility_filtered||head.version>0?'prb_hidden':'prb_empty')));}
  }
  async function readHistory(append){
    const ticket=epoch,after=append?historyCursor:0;
    try{
      const page=check(await api('GET',`${endpoint}/release-baselines?${query({after,limit:20})}`));if(!valid(ticket))return;
      const rows=(page.baselines||[]).map(row=>baselineRow(row));
      if(!append){history.replaceChildren();historyFiltered=false;}else history.querySelector('[data-release-more]')?.remove();
      historyLoaded=true;historyCursor=page.next_cursor;historyFiltered ||= page.visibility_filtered;
      history.append(...rows);history.querySelector('[data-release-coverage]')?.remove();
      history.append(h('p',{'data-release-coverage':'',class:'muted'},t(historyFiltered?'prb_filtered':'prb_partial')));
      if(historyCursor!==null&&historyCursor!==undefined)history.append(button({'data-release-more':'',variant:'secondary',label:t('prb_more'),onClick:event=>{
        event.currentTarget.disabled=true;void readHistory(true);
      }}));
    }catch(error){if(valid(ticket))failure(error);}
  }
  function bindEditor(){
    const chosen=selectedBinding();
    if(!chosen){currentEditor=null;editorView.replaceChildren();return;}
    const repositoryName=chosen.name;
    if(!editors.has(repositoryName)){
      const registration=h('input',{type:'hidden'}),rationale=input('prb_rationale',{multiline:true,max:4000}),limitations=input('prb_limits',{multiline:true,required:false,max:80040});
      const stale=h('p',{class:'muted'},t('prb_stale'));
      let editor=null,syncing=false;
      const matches=()=>{
        if(!preview||!registration.value||!head||!ready)return false;
        try{const saved=JSON.parse(registration.value);return JSON.stringify(saved)===JSON.stringify(preview.registration)
          &&saved.preconditions.expected_baseline_version===head.version&&saved.preconditions.expected_previous_baseline_id===head.baseline_id;}catch{return false;}
      };
      editor=form({h,t,api:editorApi,action,target:{workspace_id:workspaceId,repository:repositoryName},fields:{registration,rationale,limitations},
        params:values=>{
          const frozen=JSON.parse(values.registration),limits=values.limitations.split('\n').map(line=>line.trim()).filter(Boolean);
          if(limits.length>40||limits.some(line=>[...line].length>2000))throw Error(t('prb_limits_help'));
          return {...frozen.params,rationale:values.rationale,known_limitations:limits};
        },preconditions:()=>JSON.parse(registration.value).preconditions,
        permitted:()=>allowed()&&matches(),recoverPermitted:()=>allowed(),label:'prb_save',
        onSaved:()=>refresh(),onStateChange:()=>{if(editor&&!syncing)editor.update();},
        extra:h('div',{class:'pm-fields'},stale,labelled('prb_rationale',rationale),labelled('prb_limits',limitations),h('p',{class:'muted'},t('prb_limits_help')))});
      const baseUpdate=editor.update;
      editor.update=()=>{if(syncing)return;syncing=true;try{
        if(!editor.locked()&&!registration.value&&preview?.registration.target.repository===repositoryName){registration.value=JSON.stringify(preview.registration);editor.changed();}
        baseUpdate();stale.hidden=editor.locked()||!registration.value||matches();
        // Draft text remains editable before a new fixed source has been reviewed.
        if(!editor.locked()&&allowed()){rationale.disabled=false;limitations.disabled=false;}
        if(editor===currentEditor)usePreview.hidden=editor.locked()||!preview||matches();
      }finally{syncing=false;}};
      editors.set(repositoryName,editor);
    }
    currentEditor=editors.get(repositoryName);
    // Preview may arrive between focusing the rationale and entering text.
    // Keep this repository's form connected so that keyboard input retains its target.
    if(currentEditor.box.parentElement!==editorView)editorView.replaceChildren(currentEditor.box);
    currentEditor.update();
  }
  async function readBindings(append){
    const ticket=epoch;reading=true;controls();
    try{
      const page=check(await api('GET',`${endpoint}/release-baseline-sources?${query({after:append?bindingCursor:0,limit:20})}`));if(!valid(ticket))return;
      bindings=append?[...bindings,...page.bindings]:page.bindings;bindingCursor=page.next_cursor;
      const wanted=repository.value||draft.repository;
      repository.replaceChildren(h('option',{value:''},t('prb_choose_repository')));
      for(const row of bindings)for(const name of row.repositories)repository.append(h('option',{value:bindingKey(row,name)},`${row.project_name} · ${name}`));
      repository.value=[...repository.options].some(option=>option.value===wanted)?wanted:'';
      moreBindings.hidden=bindingCursor===null||bindingCursor===undefined;bindEditor();
    }catch(error){if(valid(ticket))failure(error);}finally{if(ticket===epoch){reading=false;controls();}}
  }
  function invalidate(){persist();resetSources();sourceStatus.replaceChildren();bindEditor();controls();}
  repository.addEventListener('change',invalidate);kind.addEventListener('change',invalidate);
  branch.addEventListener('input',()=>{persist();selectionEpoch++;preview=null;previewView.replaceChildren();usePreview.hidden=true;controls();});
  async function readChoices(append){
    const chosen=selectedBinding();if(!ready||!allowed()||!chosen||reading)return;
    const ticket=epoch,selected=++selectionEpoch,mode=kind.value,cursor=append?choiceCursor:mode==='github_release'?1:0;
    reading=true;preview=null;previewView.replaceChildren();selectedKey='';sourceStatus.replaceChildren();controls();
    try{
      const page=check(await api('GET',`${endpoint}/release-baseline-sources/${mode==='github_release'?'github-releases':'deployments'}?${query({project_binding_id:chosen.row.project_binding_id,repository:chosen.name,[mode==='github_release'?'page':'after']:cursor,limit:20})}`));
      if(!valid(ticket)||selected!==selectionEpoch)return;
      if(page.project_binding_id!==chosen.row.project_binding_id||page.repository!==chosen.name)throw Error(t('prb_invalid'));
      const entries=mode==='github_release'?page.releases:page.deployments;
      if(!Array.isArray(entries)||mode==='github_release'&&entries.some(row=>!Number.isSafeInteger(row.release_id)||row.release_id<1))throw Error(t('prb_invalid'));
      choicesFiltered=(append&&choicesFiltered)||Boolean(page.visibility_filtered);choicesIneligible=(append&&choicesIneligible)||Boolean(page.eligibility_filtered);
      choices=append?[...choices,...entries]:entries;choiceCursor=mode==='github_release'?page.next_page:page.next_cursor;
      sourceView.replaceChildren();
      for(const row of choices){
        const id=String(mode==='github_release'?row.release_id:row.deployment_id),label=mode==='github_release'?`${row.tag} · ${row.channel} · ${timeText(row.published_at)}`:`${row.channel} · ${row.version?.value||row.commit_sha} · ${timeText(row.checked_at)}`;
        const radio=h('input',{type:'radio',name:`release-source-${workspaceId}`,value:id,onchange:()=>{selectedKey=id;selectionEpoch++;preview=null;previewView.replaceChildren();controls();}});
        sourceView.append(h('label',{'data-release-choice':id},radio,label));
      }
      if(!entries.length)sourceView.append(h('p',{class:'muted'},t('prb_no_sources')));
      if(choicesFiltered)sourceView.append(h('p',{class:'muted'},t('prb_filtered')));
      if(choicesIneligible)sourceView.append(h('p',{class:'muted'},t('prb_ineligible')));
      if(page.coverage?.page_bound_reached)sourceView.append(h('p',{class:'muted'},t('prb_page_bound')));
      if(choiceCursor!==null&&choiceCursor!==undefined)sourceView.append(button({variant:'secondary',label:t('prb_more_sources'),onClick:()=>void readChoices(true)}));
    }catch(error){if(valid(ticket)&&selected===selectionEpoch)failure(error);}finally{if(ticket===epoch){reading=false;controls();}}
  }
  async function readProof(){
    const chosen=selectedBinding();if(!ready||!allowed()||!chosen||!selectedKey||reading)return;
    const ticket=epoch,selected=selectionEpoch,fixed=selection(),sourceId=selectedKey;
    reading=true;preview=null;previewView.replaceChildren();sourceStatus.replaceChildren();controls();
    try{
      const value=check(await api('GET',`${endpoint}/release-baseline-preview?${query({project_binding_id:chosen.row.project_binding_id,repository:chosen.name,kind:kind.value,
        [kind.value==='github_release'?'release_id':'deployment_id']:sourceId,...(branch.value?{maintenance_branch:branch.value}:{})})}`));
      if(!valid(ticket)||selected!==selectionEpoch||fixed!==selection())return;
      const reg=value.registration,pre=reg?.preconditions,source=reg?.params?.source;
      if(reg?.action!==action||reg.target?.workspace_id!==workspaceId||reg.target.repository!==chosen.name||reg.params.project_binding_id!==chosen.row.project_binding_id
        ||source?.kind!==kind.value||String(source[source.kind==='github_release'?'release_id':'deployment_id'])!==sourceId
        ||reg.params.maintenance_branch!==(branch.value||null)||pre?.expected_baseline_version!==head?.version||pre.expected_previous_baseline_id!==head.baseline_id
        ||!Number.isSafeInteger(pre.expected_repository_id)||pre.expected_repository_id<1||!/^([0-9a-f]{40})$/.test(pre.expected_commit_sha)
        ||value.proof?.selection?.commit_sha!==pre.expected_commit_sha||value.proof.selection.repository_id!==pre.expected_repository_id
        ||value.proof.selection.channel!==reg.params.channel||value.maintenance_authorized!==false)throw Error(t('prb_invalid'));
      preview=value;
      previewView.append(h('h3',{},t('prb_preview_title')),h('p',{},chosen.name,' · ',value.proof.source.tag||pre.expected_commit_sha,' · ',reg.params.channel),
        h('p',{class:'muted'},t('prb_quality')),fixedDetails({repository:chosen.name,commit_sha:pre.expected_commit_sha},value.proof));
      bindEditor();
    }catch(error){if(valid(ticket)&&selected===selectionEpoch)failure(error);}finally{if(ticket===epoch){reading=false;controls();}}
  }
  async function refresh(){
    withdraw();const ticket=epoch;status.replaceChildren();
    try{
      const current=await api('GET',`${endpoint}/release-baselines/current`);if(!valid(ticket))return;
      renderCurrent(current);ready=true;
      if(owner()&&discovery())await readBindings(false);
      if(!valid(ticket))return;
      if(historyDetails.open)await readHistory(false);
      controls();
    }catch(error){if(valid(ticket))failure(error);}
  }
  controls();return {element,refresh,withdraw};
}
