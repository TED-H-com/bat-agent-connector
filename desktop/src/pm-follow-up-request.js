import {disclosure} from './ui/components.js';
import {retiredView, viewLeft} from './pm-retired.js';
// The central service checks the explicitly selected runtime against a fixed intake.
import {structuredAnalysis} from './pm-structured-analysis.js';
export function followUpAnalysisRequest({h,t,api,alive,workspaceId,caps,namespace,form,input,labelled,permitted,errorBox,onSaved,onAccessFailure=()=>{}}) {
  const actor=caps()?.actor,endpoint=`/pm/workspaces/${encodeURIComponent(workspaceId)}`;
  let serial=0,intake=null,capability=null,thread=null,request=null,retired=false,next=null,rows=[],targetLocked=false;
  const choices=new Map(),status=h('div',{role:'status'}),body=h('div'),chooser=h('select',{'aria-label':t('pm_fa_thread')});
  const note=key=>h('p',{class:'muted'},t(key));
  const element=disclosure({label:t('pm_fa_analyze_prepare'),body:h('div',{},status,body)}); element.dataset.pmFollowUpAnalyze='';
  const current=ticket=>{alive();if(caps()?.actor!==actor){retired=true;throw Error(t('pm_fa_actor_changed'));}return ticket===serial&&!retired;};
  const check=data=>{if(data?.workspace_id!==workspaceId)throw Error(t('pm_fa_mismatch'));return data;};
  const authorized=()=>Boolean(intake)&&!retired&&actor===caps()?.actor&&permitted(false)&&caps()?.scopes?.includes('start')
    &&thread?.capabilities?.write===true;
  const targetKey=()=>`batc.pm.${namespace}.follow-up-target.${JSON.stringify({actor,workspace_id:workspaceId,intake_id:intake.intake_id})}`;
  const validThread=id=>typeof id==='string'&&/^pmt_[0-9a-f]{32}$/.test(id);
  function saveTarget(id){
    current(serial);
    localStorage.setItem(targetKey(),JSON.stringify({version:1,actor,workspace_id:workspaceId,intake_id:intake.intake_id,thread_id:id}));
    choices.set(intake.intake_id,id);
  }
  function savedTarget(){
    const prefix=`batc.pm.${namespace}.follow-up.${intake.intake_id}.pm.follow_up.analyze.`,pending=new Set(),drafts=new Set();
    let pointer=null,count=0;
    try{
      const raw=localStorage.getItem(targetKey());if(raw!==null){pointer=JSON.parse(raw);
        if(pointer?.version!==1||pointer.actor!==actor||pointer.workspace_id!==workspaceId||pointer.intake_id!==intake.intake_id
          ||pointer.thread_id!==''&&!validThread(pointer.thread_id))throw Error();}
      // Pre-fix drafts already contain an exact target in this same durable namespace.
      // Recover only that original target; never select a latest discussion or another intake.
      for(let index=0;index<localStorage.length;index++){
        const key=localStorage.key(index);if(!key?.startsWith(prefix))continue;if(++count>64)throw Error();
        const target=JSON.parse(key.slice(prefix.length)),raw=localStorage.getItem(key);if(!raw||raw.length>65536)throw Error();
        const value=JSON.parse(raw),id=target?.thread_id;
        if(!validThread(id)||key!==prefix+JSON.stringify({workspace_id:workspaceId,thread_id:id})||value?.version!==1
          ||!value.values||typeof value.values!=='object'||Array.isArray(value.values)||Object.values(value.values).some(item=>typeof item!=='string'))throw Error();
        if(value.intent){const original=value.intent;
          if(typeof original.key!=='string'||!original.key||original.request?.action!=='pm.follow_up.analyze'
            ||JSON.stringify(original.request.target)!==JSON.stringify(target)||original.request.params?.intake_id!==intake.intake_id)throw Error();
          pending.add(id);
        }else if(Object.keys(value.values).length)drafts.add(id);
      }
    }catch{throw Error(t('pm_fa_target_damaged'));}
    if(pending.size>1)throw Error(t('pm_fa_target_ambiguous'));
    return {id:[...pending][0]??pointer?.thread_id??(drafts.size===1?[...drafts][0]:''),locked:pending.size===1};
  }
  function clear(){++serial;intake=null;thread=null;capability=null;request?.dispose();request=null;rows=[];next=null;targetLocked=false;body.replaceChildren();status.replaceChildren();}
  function fail(error){++serial;thread=null;capability=null;request?.dispose();request=null;body.replaceChildren();status.replaceChildren(errorBox(error, {within:status}));onAccessFailure(error);}
  function navigation(){
    const selected=choices.get(intake.intake_id)||intake.document.thread_ref?.thread_id||'';
    chooser.replaceChildren(h('option',{value:''},t('pm_fa_thread_choose')),...rows.map(row=>h('option',{value:row.thread_id},row.title)));
    if(selected&&!rows.some(row=>row.thread_id===selected))chooser.append(h('option',{value:selected},selected));
    chooser.value=selected;chooser.disabled=Boolean(intake.document.thread_ref)||targetLocked||Boolean(request?.locked());
  }
  async function readThread() {
    const ticket=++serial;request?.dispose();request=null;thread=null;body.replaceChildren(labelled('pm_fa_thread',chooser));navigation();
    if(next!=null)body.append(h('button',{type:'button',class:'ui-button secondary ui-sm',onclick:()=>void readThreads(next)},t('pm_fa_more_threads')));
    if(!chooser.value)return;status.replaceChildren(note('pm_fa_loading_thread'));
    try{
      const data=await api('GET',`${endpoint}/threads/${encodeURIComponent(chooser.value)}`);if(!current(ticket))return;
      if(data.thread?.thread_id!==chooser.value||data.thread.workspace_id!==workspaceId||!Number.isSafeInteger(data.thread.version)
        ||!['idea','planning'].includes(data.thread.mode))throw Error(t('pm_fa_mismatch'));
      thread=data;status.replaceChildren();
      const guardedApi=async(...args)=>{try{return await api(...args);}catch(error){
        try{if(current(ticket))onAccessFailure(error);}catch{/* The originating view has already retired. */}throw error;
      }};
      const purpose=input('pm_fa_purpose',{multiline:true,max:4000}),model=input('pm_native_model',{required:false,max:128});model.placeholder=t('pm_native_default_model');
      const version=h('input',{type:'hidden',value:String(thread.thread.version)}),warning=note('pm_fa_thread_stale');
      const runtime=structuredAnalysis({h,t,labelled,action:'pm.follow_up.analyze',capability:()=>capability,authorized,
        legacyEligible:()=>capability?.follow_up_analysis?.authorized===true&&capability?.follow_up_analysis?.eligible_for_preflight===true});
      const stale=()=>version.value!==String(thread?.thread.version);
      const rebase=h('button',{type:'button',class:'ui-button secondary ui-sm',onclick:()=>{if(request&&!request.locked()&&authorized()){request.set('_version',String(thread.thread.version));request.update();}}},t('pm_fa_use_thread_version'));
      const sync=()=>{if(request)targetLocked=request.locked();warning.hidden=!stale();rebase.hidden=!stale();rebase.disabled=Boolean(request?.locked())||!authorized();navigation();runtime.update(request);};
      const eligible=()=>authorized()&&runtime.eligible()&&!stale();
      request=form({api:guardedApi,namespace:`${namespace}.follow-up.${intake.intake_id}`,action:'pm.follow_up.analyze',target:{workspace_id:workspaceId,thread_id:thread.thread.thread_id},
        fields:{purpose,model,_version:version,adapter:runtime.field},params:values=>({intake_id:intake.intake_id,purpose:values.purpose,...(values.model.trim()?{model:values.model.trim()}:{}),...runtime.params(values)}),
        preconditions:()=>({expected_version:Number(version.value),expected_intake_digest:intake.document_digest}),
        permitted:eligible,recoverPermitted:authorized,label:'pm_fa_analyze',onStateChange:sync,onSaved,
        extra:h('div',{class:'pm-fields'},labelled('pm_fa_purpose',purpose),runtime.box,h('details',{},h('summary',{},t('pm_fa_model_optional')),labelled('pm_native_model',model)),version)});
      body.append(warning,rebase,request.box);request.update();
    }catch(error){if(retiredView(error)||viewLeft(alive))return;if(ticket===serial)fail(error);}
  }
  async function readThreads(after=null){
    const ticket=serial;
    try{
      const data=check(await api('GET',`${endpoint}/threads?${after?`after=${encodeURIComponent(after)}&`:''}limit=20`));if(!current(ticket))return;
      if(!Array.isArray(data.threads)||data.next_cursor!=null&&(!/^pmt_[0-9a-f]{32}$/.test(data.next_cursor)||data.next_cursor===after)
        ||data.threads.some(row=>row.workspace_id!==workspaceId||!/^pmt_[0-9a-f]{32}$/.test(row.thread_id)))throw Error(t('pm_fa_mismatch'));
      rows.push(...data.threads.filter(row=>['idea','planning'].includes(row.mode)&&!rows.some(old=>old.thread_id===row.thread_id)));next=data.next_cursor;await readThread();
    }catch(error){if(retiredView(error)||viewLeft(alive))return;if(ticket===serial)fail(error);}
  }
  async function prepare(){
    if(!intake||retired)return;const ticket=++serial;status.replaceChildren(note('pm_fa_loading_thread'));
    try{
      const data=check(await api('GET',`${endpoint}/capabilities`));if(!current(ticket))return;
      capability=data;rows=[];next=null;
      if(intake.document.thread_ref){choices.set(intake.intake_id,intake.document.thread_ref.thread_id);await readThread();}
      else {const saved=savedTarget();targetLocked=saved.locked;saveTarget(saved.id);await readThreads();}
    }catch(error){if(retiredView(error)||viewLeft(alive))return;if(ticket===serial)fail(error);}
  }
  chooser.addEventListener('change',()=>{
    if(!intake||targetLocked||request?.locked()){if(intake)navigation();return;}
    try{
      const saved=savedTarget();
      if(saved.locked){targetLocked=true;saveTarget(saved.id);navigation();}
      else {if(chooser.value&&!validThread(chooser.value))throw Error(t('pm_fa_mismatch'));saveTarget(chooser.value);}
      void readThread();
    }catch(error){fail(error);}
  });
  element.addEventListener('toggle',()=>{if(element.open&&intake&&!request)void prepare();});
  async function load(row){clear();intake=row;if(element.open)await prepare();}
  return {element,clear,load};
}
