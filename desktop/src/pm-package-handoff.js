import {checkSpecMaterialization,readSpecMaterializations,fixedSpecSelection} from './pm-spec-materialization.js';

// One fixed PM contract enters the existing repository.continue action.
const contract='pm-engineering-handoff/v1';
const equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
export function workspacePackageHandoff({h,t,api,alive,workspaceId,workspace,caps,permitted,form,input,labelled,errorBox,namespace,onSourceFailure=()=>{},onReviewChange=()=>{}}){
  let active=null,prepared=null,serial=0,reading=false,editor=null;
  const editors=new Map(),facts=h('div'),status=h('div',{role:'status'}),editorView=h('div'),outcome=h('div');
  const summary=h('p',{class:'muted'},t('ph_select')),availabilityNotice=h('p',{class:'muted',hidden:true},t('ph_not_allowed'));
  const review=h('button',{type:'button',class:'secondary',onclick:()=>void inspect()},t('ph_review'));
  const useReview=h('button',{type:'button',class:'secondary',hidden:true,onclick:()=>{
    if(!editor||editor.locked()||!prepared||!write())return;
    editor.set('review',JSON.stringify(prepared.fixed));update();
  }},t('ph_use_review'));
  const controls=h('details',{'data-pm-package-handoff-controls':''},h('summary',{},t('ph_title')),
    h('p',{class:'muted'},t('ph_intro')),summary,h('p',{class:'muted'},t('ph_boundary')),availabilityNotice,review,status,facts,useReview,editorView,outcome);
  const element=h('section',{class:'pm-packages','data-pm-package-handoff':''},controls);
  const available=()=>{const cap=caps()?.features?.pm_workspaces?.package_handoff;return cap?.available===true&&cap.action==='repository.continue'&&cap.contract===contract&&cap.code_basis_required===true&&['bounded_full_prompt','bounded_full_prompt_or_fixed_spec_artifact'].includes(cap.source_materialization);};
  const writer=()=>permitted(false)&&['owner','contributor'].includes(workspace()?.membership?.role)&&['observe','manage','start'].every(scope=>caps()?.scopes?.includes(scope))
    &&caps()?.actions?.some(row=>row.action==='repository.continue'&&row.allowed);
  const write=()=>available()&&writer();
  const fixedArtifacts=()=>caps()?.features?.pm_workspaces?.spec_materialization?.version===1
    &&caps()?.features?.pm_workspaces?.package_handoff?.source_materialization==='bounded_full_prompt_or_fixed_spec_artifact';
  const valid=ticket=>{try{alive();return serial===ticket;}catch{return false;}};
  const endpoint=`/pm/workspaces/${encodeURIComponent(workspaceId)}/work-packages`;
  const target=row=>Object.fromEntries(['repository','host','workspace_id'].map(key=>[key,row.document.code_source[key]]));
  const sameCode=(row,preview)=>{
    const code=row.document.code_source,p=preview;
    return p&&p.exact_ref_head_only===true&&['repository','host','workspace_id'].every(key=>p.target?.[key]===code[key])
      &&p.source_ref===code.source_ref&&p.source_sha===code.source_sha&&p.repository_id===code.repository_id&&p.binding_digest===code.binding_digest
      &&p.preconditions?.repository_id===code.repository_id&&p.preconditions?.binding_digest===code.binding_digest
      &&p.workspace?.workspace_id===code.workspace_id;
  };
  function packageCheck(value,id,digest){
    const row=value?.package,code=row?.document?.code_source,work=row?.document?.context?.work_ref;
    if(value?.version!==1||value.workspace_id!==workspaceId||row?.package_id!==id||row.document_digest!==digest
      ||row.workspace_id!==workspaceId||row.document?.package_id!==id||row.document.workspace_id!==workspaceId
      ||row.document.execution_authorized!==false||row.document.quality_assessment!=='unassessed'
      ||!code||!work||work.work_item_id!==row.work_item_id||!/^prj_[0-9a-f]{20}$/.test(work.project_id)
      ||!/^wi_[0-9a-f]{20}$/.test(work.work_item_id)||!/^([0-9a-f]{64})$/.test(work.work_fingerprint)
      ||!/^([0-9a-f]{40})$/.test(code.source_sha)||!/^([0-9a-f]{64})$/.test(code.binding_digest)
      ||!Number.isSafeInteger(code.repository_id)||code.repository_id<1)throw Error(t('ph_invalid'));
    return row;
  }
  function readinessCheck(value,row){
    const handoff=value?.handoff;
    if(value?.version!==1||value.workspace_id!==workspaceId||value.package_id!==row.package_id||value.document_digest!==row.document_digest
      ||value.ready_for!=='engineering_handoff_review'||value.execution_authorized!==false||value.execution_available!==false
      ||value.dispatch_readiness!=='separate_fixed_handoff_admission'||handoff?.contract!==contract||handoff.action!=='repository.continue'
      ||handoff.code_basis_required!==true||handoff.compatibility!=='unverified'||!['bounded_full_prompt','bounded_full_prompt_or_fixed_spec_artifact'].includes(handoff.source_materialization)||!Array.isArray(value.checks))throw Error(t('ph_invalid'));
    if(value.checks.some(check=>check.status!=='passed'&&check.check!=='spec_code_alignment'))throw Error(t('ph_blocked'));
  }
  const hostReady=row=>caps()?.hosts?.some(host=>host.host===row.document.code_source.host&&host.writes===true&&host.orchestrate===true)
    &&caps()?.features?.repository_sync?.some(binding=>binding.exact_ref_head_only===true&&['repository','host','workspace_id'].every(key=>binding[key]===row.document.code_source[key]));
  const handoffParams=values=>{
    const frozen=JSON.parse(values.review);
    return {...frozen.params,agent:values.agent,prompt:values.prompt,pm_code_basis:{mode:'implement_fixed_spec_from_selected_base',rationale:values.basis},
      ...(values.title?{title:values.title}:{}),...(values.model?{model:values.model}:{})};
  };
  function batchSelection(){
    if(!active||!prepared||!editor||editor.locked()||reading||!write()||!hostReady(active))return null;
    try{const values=editor.values();if(!equal(JSON.parse(values.review),prepared.fixed)||!values.basis.trim()||!values.prompt.trim())return null;
      return structuredClone({title:active.document.title,handoff:{target:target(active),params:handoffParams(values),preconditions:prepared.fixed.preconditions}});
    }catch{return null;}
  }
  function update(){
    review.disabled=!active||reading||!write()||!hostReady(active);
    availabilityNotice.hidden=!active||Boolean(write()&&hostReady(active));
    if(active)summary.textContent=`${active.document.title} · ${t('ph_preparation')} ${active.sequence}`;
    else summary.textContent=t('ph_select');
    if(editor)editor.update();onReviewChange();
  }
  function withdraw(){serial++;reading=false;active=null;prepared=null;facts.replaceChildren();status.replaceChildren();outcome.replaceChildren();editorView.replaceChildren();editor=null;useReview.hidden=true;update();}
  function fail(error){if(error.message?.startsWith('PM_SPEC_'))error=Error(t('ph_invalid'));if([401,403,404].includes(error.status)){withdraw();onSourceFailure();}prepared=null;facts.replaceChildren();outcome.replaceChildren();status.replaceChildren(errorBox(error),h('p',{class:'muted'},t('ph_unavailable')));update();}
  function bindEditor(row){
    if(!editors.has(row.package_id)){
      const fixed=h('input',{type:'hidden'}),basis=input('ph_rationale',{multiline:true,max:4000}),prompt=input('ph_prompt',{multiline:true,max:12000}),
        title=input('start_title',{required:false,max:256}),model=input('start_model',{required:false,max:256});
      const spec=h('select',{'aria-label':t('ph_spec_attachment')},h('option',{value:''},t('ph_inline')));
      const specBox=h('div',{},labelled('ph_spec_attachment',spec),h('p',{class:'muted'},t('ph_spec_help')));
      spec.addEventListener('change',()=>{if(value?.locked())return;prepared=null;facts.replaceChildren();update();});
      const agent=h('select',{'aria-label':t('ph_agent')},h('option',{value:'claude'},'Claude'),h('option',{value:'codex'},'Codex'));agent.dataset.pmDefault='claude';
      const warning=h('p',{class:'muted',hidden:true},t('ph_stale'));
      let value=null,syncing=false;
      const matches=()=>{try{return Boolean(prepared&&active?.package_id===row.package_id&&equal(JSON.parse(fixed.value),prepared.fixed));}catch{return false;}};
      const authorized=()=>write()&&active?.package_id===row.package_id;
      const resultMatches=result=>result?.host===row.document.code_source.host&&result.workspace_id===row.document.code_source.workspace_id
        &&result.repository===row.document.code_source.repository&&result.source_sha===row.document.code_source.source_sha&&typeof result.session_id==='string'&&result.session_id.length>0;
      const guardedApi=async(...args)=>{const ticket=serial;try{const response=await api(...args);
        if(response.operation?.status==='succeeded'&&!resultMatches(response.operation.result))throw Error(t('ph_invalid'));
        return response;}catch(error){if(valid(ticket)&&[401,403,404].includes(error.status))fail(error);throw error;}};
      value=form({namespace:`${namespace}.package-handoff.${row.package_id}`,api:guardedApi,action:'repository.continue',target:target(row),
        fields:{review:fixed,basis,prompt,agent,title,model,spec_selection:spec},params:handoffParams,preconditions:()=>JSON.parse(fixed.value).preconditions,
        permitted:()=>authorized()&&hostReady(active)&&matches()&&!reading,recoverPermitted:authorized,label:'ph_start',
        onStateChange:()=>{if(value&&!syncing)value.update();onReviewChange();},onSaved:result=>{
          if(!resultMatches(result))throw Error(t('ph_invalid'));
          prepared=null;facts.replaceChildren();outcome.replaceChildren(h('p',{},t('ph_started')),
            h('a',{href:`#/session/${encodeURIComponent(result.host)}/${encodeURIComponent(result.session_id)}`},t('ph_open')));update();
        },extra:h('div',{class:'pm-fields'},warning,specBox,labelled('ph_rationale',basis),labelled('ph_prompt',prompt),labelled('ph_agent',agent),h('p',{class:'muted'},t('ph_agent_help')),
          h('details',{},h('summary',{},t('ph_options')),labelled('start_title',title),labelled('start_model',model)))});
      const original=value.update;
      value.update=()=>{if(syncing)return;syncing=true;try{
        if(!value.locked()&&!fixed.value&&prepared&&active?.package_id===row.package_id){fixed.value=JSON.stringify(prepared.fixed);value.changed();}
        original();warning.hidden=value.locked()||!fixed.value||matches();
        specBox.hidden=!fixedArtifacts()&&!spec.value;spec.disabled=value.locked()||!authorized()||!fixedArtifacts()||reading;
        if(!value.locked()&&authorized())for(const field of [basis,prompt,agent,title,model])field.disabled=false;
        if(value===editor)useReview.hidden=value.locked()||!prepared||matches();
      }finally{syncing=false;}};
      value.box.addEventListener('input',onReviewChange);value.box.addEventListener('change',onReviewChange);
      editors.set(row.package_id,value);
    }
    editor=editors.get(row.package_id);editorView.replaceChildren(editor.box);update();
  }
  function setPackage(row){
    withdraw();if(!row)return;
    try{active=packageCheck({version:1,workspace_id:workspaceId,package:row},row.package_id,row.document_digest);bindEditor(active);update();}
    catch(error){active=null;fail(error);}
  }
  async function inspect(){
    if(!active||reading||!write()||!hostReady(active))return;
    const selected=active,ticket=++serial;reading=true;prepared=null;facts.replaceChildren();outcome.replaceChildren();status.replaceChildren(h('p',{class:'muted'},t('ph_reading')));update();
    try{
      const id=selected.package_id,work=selected.document.context.work_ref;
      const [pkg,readiness,projectData,specs]=await Promise.all([api('GET',`${endpoint}/${id}`),api('GET',`${endpoint}/${id}/readiness`),api('GET',`/projects/${encodeURIComponent(work.project_id)}`),
        fixedArtifacts()?readSpecMaterializations(api,workspaceId,selected,()=>valid(ticket)):Promise.resolve({rows:[],filtered:false})]);
      if(!valid(ticket)||active?.package_id!==id)return;
      const row=packageCheck(pkg,id,selected.document_digest);readinessCheck(readiness,row);
      const choice=editor.fields.spec_selection,chosen=choice.value;
      choice.replaceChildren(h('option',{value:''},t('ph_inline')),...specs.rows.map(spec=>h('option',{value:spec.materialization_id},`${t('ph_fixed_attachment')} · ${spec.sequence}`)));
      if(chosen&&!specs.rows.some(spec=>spec.materialization_id===chosen))choice.append(h('option',{value:chosen},t('ph_spec_missing')));
      choice.value=chosen;let fixedSpec=null;
      if(chosen){
        const original=specs.rows.find(spec=>spec.materialization_id===chosen);if(!original)throw Error(t('ph_spec_missing'));
        const data=await api('GET',`/pm/workspaces/${encodeURIComponent(workspaceId)}/spec-materializations/${chosen}`);if(!valid(ticket)||active?.package_id!==id)return;
        if(data?.version!==1||data.workspace_id!==workspaceId||data.materialization?.materialization_id!==chosen||data.materialization.document_digest!==original.document_digest)throw Error(t('ph_invalid'));
        fixedSpec=checkSpecMaterialization(data.materialization,workspaceId,row,{full:true,original});
      }
      const project=projectData.project;
      if(project?.project_id!==work.project_id||project.archived||!Number.isSafeInteger(project.version)||project.version<1
        ||!project.repositories?.some(repository=>repository.toLowerCase()===row.document.code_source.repository.toLowerCase()))throw Error(t('ph_invalid'));
      const code=row.document.code_source;
      const response=await api('POST','/repository-previews',{...target(row),source_ref:code.source_ref});
      if(!valid(ticket)||active?.package_id!==id)return;
      if(!sameCode(row,response.preview))throw Error(t('ph_target_changed'));
      prepared={row,project,fixed:{params:{source_ref:code.source_ref,source_sha:code.source_sha,project_id:work.project_id,work_item_id:work.work_item_id,
        pm_package_ref:{workspace_id:workspaceId,package_id:id,document_digest:row.document_digest},...(fixedSpec?fixedSpecSelection(fixedSpec,workspaceId):{})},
        preconditions:{repository_id:code.repository_id,binding_digest:code.binding_digest,expected_project_version:project.version,expected_work_item_fingerprint:work.work_fingerprint}}};
      const proof=h('details',{},h('summary',{},t('ph_sources')),h('p',{class:'mono'},`${t('ph_base')}: ${code.source_sha}`),
        ...(row.document.sources?.documents||[]).map(source=>h('p',{class:'mono'},`${t('ph_spec')}: ${source.path} · ${source.commit_sha} · SHA-256 ${source.sha256}`)),
        h('p',{class:'mono'},`${code.host} · ${code.workspace_id}`),h('p',{class:'mono'},row.package_id),h('p',{class:'mono'},row.document_digest),h('p',{class:'mono'},work.work_fingerprint));
      facts.replaceChildren(h('h4',{},t('ph_reviewed')),h('p',{},project.name,' · ',code.repository,' · ',code.source_ref.replace(/^refs\/heads\//,'')),
        h('p',{},code.host,' · ',response.preview.workspace.name||t('ph_workspace')),h('p',{class:'muted'},`${t('ph_project_version')} ${project.version}`),
        h('p',{class:'muted'},t('ph_separate')),fixedSpec?h('p',{class:'pm-source'},t('ph_fixed_attachment'),': ',fixedSpec.materialization_id,' · ',fixedSpec.artifact_ref.artifact_id,' · ',fixedSpec.document_digest):null,
        specs.filtered?h('p',{class:'muted'},t('ph_spec_filtered')):null,proof);status.replaceChildren();
    }catch(error){if(valid(ticket))fail(error);}finally{if(valid(ticket)){reading=false;update();}}
  }
  return {element,setPackage,withdraw,batchSelection,refresh:async()=>{prepared=null;facts.replaceChildren();update();}};
}
