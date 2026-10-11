// The UI selects a fixed intake; only the central native runner performs analysis.
export function followUpAnalysisRequest({h,t,api,alive,workspaceId,caps,namespace,form,input,labelled,permitted,errorBox,onSaved,onAccessFailure=()=>{}}) {
  const actor=caps()?.actor,endpoint=`/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  let serial=0,intake=null,capability=null,thread=null,request=null,retired=false,next=null,rows=[];
  const choices=new Map(),status=h('div',{role:'status'}),body=h('div'),chooser=h('select',{'aria-label':t('pm_fa_thread')});
  const note=key=>h('p',{class:'muted'},t(key));
  const element=h('details',{'data-pm-follow-up-analyze':''},h('summary',{},t('pm_fa_analyze_prepare')),note('pm_fa_analyze_help'),status,body);
  const current=ticket=>{alive();if(caps()?.actor!==actor){retired=true;throw Error(t('pm_fa_actor_changed'));}return ticket===serial&&!retired;};
  const check=data=>{if(data?.workspace_id!==workspaceId)throw Error(t('pm_fa_mismatch'));return data;};
  const authorized=()=>Boolean(intake)&&!retired&&actor===caps()?.actor&&permitted(false)&&caps()?.scopes?.includes('start')
    &&thread?.capabilities?.write===true;
  function clear(){++serial;intake=null;thread=null;capability=null;request?.dispose();request=null;rows=[];next=null;body.replaceChildren();status.replaceChildren();}
  function fail(error){++serial;thread=null;capability=null;request?.dispose();request=null;body.replaceChildren();status.replaceChildren(errorBox(error));onAccessFailure(error);}
  function navigation(){
    const selected=choices.get(intake.intake_id)||intake.document.thread_ref?.thread_id||'';
    chooser.replaceChildren(h('option',{value:''},t('pm_fa_thread_choose')),...rows.map(row=>h('option',{value:row.thread_id},row.title)));
    if(selected&&!rows.some(row=>row.thread_id===selected))chooser.append(h('option',{value:selected},selected));
    chooser.value=selected;chooser.disabled=Boolean(intake.document.thread_ref)||Boolean(request?.locked());
  }
  async function readThread() {
    const ticket=++serial;request?.dispose();request=null;thread=null;body.replaceChildren(labelled('pm_fa_thread',chooser));navigation();
    if(next!=null)body.append(h('button',{type:'button',class:'secondary',onclick:()=>void readThreads(next)},t('pm_fa_more_threads')));
    if(!chooser.value)return;status.replaceChildren(note('pm_fa_loading_thread'));
    try{
      const data=check(await api('GET',`${endpoint}/threads/${encodeURIComponent(chooser.value)}`));if(!current(ticket))return;
      if(data.thread?.thread_id!==chooser.value||data.thread.workspace_id!==workspaceId||!Number.isSafeInteger(data.thread.version)
        ||!['idea','planning'].includes(data.thread.mode))throw Error(t('pm_fa_mismatch'));
      thread=data;status.replaceChildren();
      const guardedApi=async(...args)=>{try{return await api(...args);}catch(error){
        try{if(current(ticket))onAccessFailure(error);}catch{/* The originating view has already retired. */}throw error;
      }};
      const purpose=input('pm_fa_purpose',{multiline:true,max:4000}),model=input('pm_native_model',{required:false,max:128});model.placeholder=t('pm_native_default_model');
      const version=h('input',{type:'hidden',value:String(thread.thread.version)}),warning=note('pm_fa_thread_stale');
      const stale=()=>version.value!==String(thread?.thread.version);
      const rebase=h('button',{type:'button',class:'secondary',onclick:()=>{if(request&&!request.locked()&&authorized()){request.set('_version',String(thread.thread.version));request.update();}}},t('pm_fa_use_thread_version'));
      const sync=()=>{warning.hidden=!stale();rebase.hidden=!stale();rebase.disabled=Boolean(request?.locked())||!authorized();navigation();};
      const eligible=()=>authorized()&&capability?.authorized===true&&capability?.eligible_for_preflight===true&&!stale();
      request=form({api:guardedApi,namespace:`${namespace}.follow-up.${intake.intake_id}`,action:'pm.follow_up.analyze',target:{workspace_id:workspaceId,thread_id:thread.thread.thread_id},
        fields:{purpose,model,_version:version},params:values=>({intake_id:intake.intake_id,purpose:values.purpose,...(values.model.trim()?{model:values.model.trim()}:{})}),
        preconditions:()=>({expected_version:Number(version.value),expected_intake_digest:intake.document_digest}),
        permitted:eligible,recoverPermitted:authorized,label:'pm_fa_analyze',onStateChange:sync,onSaved,
        extra:h('div',{class:'pm-fields'},labelled('pm_fa_purpose',purpose),h('details',{},h('summary',{},t('pm_fa_model_optional')),labelled('pm_native_model',model)),version)});
      const reason=!authorized()?'pm_native_permissions':capability?.eligible_for_preflight?'pm_native_preflight_pending':{
        PM_NATIVE_PLATFORM_UNSUPPORTED:'pm_native_platform',PM_NATIVE_EXECUTABLE_UNAVAILABLE:'pm_native_install',
        PM_NATIVE_MANAGED_CONFIG_UNSUPPORTED:'pm_native_managed',PM_NATIVE_AUTH_UNAVAILABLE:'pm_native_auth',PM_NATIVE_HOME_UNAVAILABLE:'pm_native_auth',
      }[capability?.reason]||'pm_native_unavailable';
      body.append(note(reason),warning,rebase,request.box);request.update();
    }catch(error){if(ticket===serial)fail(error);}
  }
  async function readThreads(after=null){
    const ticket=serial;
    try{
      const data=check(await api('GET',`${endpoint}/threads?${after?`after=${encodeURIComponent(after)}&`:''}limit=20`));if(!current(ticket))return;
      if(!Array.isArray(data.threads)||data.next_cursor!=null&&(!/^pmt_[0-9a-f]{32}$/.test(data.next_cursor)||data.next_cursor===after)
        ||data.threads.some(row=>row.workspace_id!==workspaceId||!/^pmt_[0-9a-f]{32}$/.test(row.thread_id)))throw Error(t('pm_fa_mismatch'));
      rows.push(...data.threads.filter(row=>['idea','planning'].includes(row.mode)&&!rows.some(old=>old.thread_id===row.thread_id)));next=data.next_cursor;await readThread();
    }catch(error){if(ticket===serial)fail(error);}
  }
  async function prepare(){
    if(!intake||retired)return;const ticket=++serial;status.replaceChildren(note('pm_fa_loading_thread'));
    try{
      const data=check(await api('GET',`${endpoint}/capabilities`));if(!current(ticket))return;
      capability=data.follow_up_analysis;rows=[];next=null;
      if(intake.document.thread_ref){choices.set(intake.intake_id,intake.document.thread_ref.thread_id);await readThread();}
      else await readThreads();
    }catch(error){if(ticket===serial)fail(error);}
  }
  chooser.addEventListener('change',()=>{if(!intake||request?.locked())return;choices.set(intake.intake_id,chooser.value);void readThread();});
  element.addEventListener('toggle',()=>{if(element.open&&intake&&!request)void prepare();});
  async function load(row){clear();intake=row;if(element.open)await prepare();}
  return {element,clear,load};
}
