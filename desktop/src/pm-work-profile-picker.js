import {h} from './ui/dom.js';
import {button} from './ui/components.js';
import {timeLabel,timeStamp} from './ui/stamps.js';
import {internalFrom} from './work-paths.js';
import {healthLabel} from './health-fact.js';
import {profileStateFacts,profileTimestamp} from './pm-work-profile-state.js';
import {hasEffectivePmRoute,renderEffectivePmRoute} from './pm-effective-route.js';
import {claimObservationRenewal} from './observation-renewal.js';
import {patch} from './ui/patch.js';

const identity = value => typeof value === 'string' && /^wpf_[0-9a-f]{32}$/.test(value);
const states = ['discovered','signed_in_or_available','can_observe','can_start_own','can_continue_own','last_verified_execution'];
const values = ['ready','missing','unknown','unavailable'];
export function checkedWorkProfile(row) {
  if (!row || !identity(row.profile_id) || !Number.isSafeInteger(row.revision) || row.revision < 1
      || !/^[0-9a-f]{64}$/.test(row.digest) || !['claude','codex'].includes(row.agent)
      || row.model !== null && (typeof row.model !== 'string' || !row.model.trim())
      || ![null,'pull_request','reviewable_changes'].includes(row.delivery_goal)
      || ['repository','host','workspace_id'].some(key => typeof row[key] !== 'string' || !row[key])) return null;
  return Object.fromEntries(['profile_id','revision','digest','agent','model','delivery_goal','repository','host','workspace_id'].map(key=>[key,row[key]]));
}

// This is a stored observation reader. Selecting a profile never probes a host,
// starts an agent, edits the saved profile, or adopts a default implicitly.
export function workProfilePicker({t,api,alive,namespace,workspaceId,workId,caps,field,request,active,onChange,errorBox}) {
  const base=`/pm/workspaces/${encodeURIComponent(workspaceId)}/work-profiles`;
  const select=h('select',{'aria-label':t('wpf_label')}),facts=h('div',{'data-work-profile-readiness':'',tabindex:-1});
  const status=h('div',{role:'status'}),announcement=h('span',{role:'status',class:'pm-work-profile-announcement'}),more=button({label:t('wpf_more'),hidden:true,onClick:()=>void load(true)});
  const box=h('section',{'data-work-profile-picker':''},h('label',{},t('wpf_label'),select),more,status,announcement,facts);
  const returnKey=`batc.pm.${namespace}.profile-return.${workspaceId}.${workId}`;
  let serial=0,loading=false,disposed=false,rows=new Map(),cursor=null,verified=null;
  let observation=null,expiryTimer=null;
  const renewed=new Map();
  const renewalKey=(row,at)=>`${row.profile_id}:${row.revision}:${row.digest}:${at}`;
  const deadline=data=>Math.min(...states.map(name=>profileStateFacts(data.states[name]).fact?.valid_until??Infinity));
  function clearObservation(){clearTimeout(expiryTimer);expiryTimer=null;observation=null;}
  function paintValues(){
    if(!observation||!valid(observation.ticket)||document.hidden)return;
    for(const name of states){
      const state=profileStateFacts(observation.data.states[name],{error:observation.error});
      const node=facts.querySelector(`[data-work-profile-state="${name}"] [data-work-profile-value]`);
      const text=state.fact?healthLabel(state.fact,t):t(state.valueKey);
      if(node&&node.textContent!==text)node.textContent=text;
    }
  }
  function scheduleExpiry(){
    clearTimeout(expiryTimer);expiryTimer=null;
    if(!observation||document.hidden||!valid(observation.ticket))return;
    paintValues();
    const at=deadline(observation.data);
    if(!Number.isFinite(at)||renewed.has(renewalKey(observation.row,at)))return;
    // One deadline wakeup, never a polling/retry timer. Normal reads with a new
    // producer deadline re-arm it; visibility changes reuse the same decision.
    if(Date.now()<at)expiryTimer=setTimeout(scheduleExpiry,Math.min(at-Date.now(),2147483647));
    else void renew(observation,at);
  }
  async function renew(current,at){
    // Retire the deadline before awaiting. A later selection/read of this same
    // deadline must not inherit a Checking label from a discarded ticket.
    const attempt=claimObservationRenewal(renewed,renewalKey(current.row,at),at);
    if(!attempt)return;
    try{await read(current.row,current.ticket,true);}
    catch(error){
      if(!valid(current.ticket)||observation!==current)return;
      attempt.error={code:error.code||error.status||'READ_FAILED'};
      current.error=attempt.error;paintValues();
    }
  }
  // The shell's existing display clock updates ages only. It cannot start GETs.
  document.addEventListener('health-display-tick',paintValues);
  document.addEventListener('visibilitychange',scheduleExpiry);
  const available=()=>caps()?.features?.pm_workspaces?.work_profiles?.version===1
    && caps()?.features?.pm_workspaces?.work_profiles?.available===true && caps()?.scopes?.includes('observe');
  const selected=()=>{try{return checkedWorkProfile(JSON.parse(field.value||'null'));}catch{return null;}};
  const valid=ticket=>{try{alive();return !disposed&&active()&&ticket===serial;}catch{return false;}};
  const label=row=>[row.agent,row.repository,row.host,row.workspace_id,row.is_default?t('wpf_default'):null,row.model,row.delivery_goal?t('wst_delivery_'+(row.delivery_goal==='pull_request'?'pr':'changes')):null].filter(Boolean).join(' · ');
  function options(){
    const value=selected();select.replaceChildren(h('option',{value:''},t('wpf_none')),
      ...[...rows.values()].map(row=>h('option',{value:row.profile_id},label(row))));
    if(value&&!rows.has(value.profile_id))select.append(h('option',{value:value.profile_id},label(value)));
    select.value=value?.profile_id||'';update();
  }
  function update(){const row=selected();select.value=row?.profile_id||'';if(!row){clearObservation();facts.replaceChildren();announcement.textContent='';verified=null;}
    box.hidden=!available();select.disabled=loading||Boolean(request()?.locked())||!available();more.hidden=cursor===null;more.disabled=loading||Boolean(request()?.locked());}
  function forget(message){
    clearObservation();const current=selected();if(current)rows.delete(current.profile_id);verified=null;facts.replaceChildren();announcement.textContent='';
    if(!request()?.locked()){request()?.set('profile','');onChange(null,false);}
    status.replaceChildren(h('p',{class:'muted'},t(message)));options();
  }
  function display(data,row,renewal=false){
    const scroll=[];
    if(renewal)for(let node=facts;node;node=node.parentElement)scroll.push([node,node.scrollLeft,node.scrollTop]);
    const next=data.work_follow_up;
    let nextKey='wpf_next_unknown';
    if(next?.route==='bat_work_start'&&next.agent===row.agent&&next.admission_checked===false){
      if(next.state==='unavailable'&&next.reason==='codex_uncorrelated_turn_boundary'&&row.agent==='codex')nextKey='wpf_next_codex';
      else if(next.state==='unavailable'&&next.reason==='action_unavailable')nextKey='wpf_action_unavailable';
      else if(next.state==='requires_attempt'&&next.reason==='owned_attempt_required')nextKey='wpf_next_attempt';
    }
    const list=h('dl',{class:'pm-work-profile-states'}),shownTimes=new Set();
    for(const name of states){
      const state=profileStateFacts(data.states[name],{error:observation?.error});
      const value=h('dd',{'data-work-profile-state':name},
        h('p',{'data-work-profile-value':''},state.fact?healthLabel(state.fact,t):t(state.valueKey)),
        h('p',{'data-work-profile-reason':''},t(state.reasonKey),' · ',h('code',{},state.reasonCode||t('wpf_reason_absent'))));
      list.append(h('dt',{},t('wpf_'+name)),value);
      const operationLink=op=>h('a',{href:`#/op/${op}`},t('pm_operation'));
      if(state.operationId)value.append(h('p',{},operationLink(state.operationId)));
      if(state.lastSuccessAt!==null){
        value.append(h('p',{'data-work-profile-last-success':''},timeLabel(t,'wpf_last_success',{time:timeStamp(state.lastSuccessAt)})));
        shownTimes.add(state.lastSuccessAt);
      }
      if(state.past){
        if(state.past.at!==null)shownTimes.add(state.past.at);
        const past=h('div',{'data-work-profile-past':''},h('p',{},state.past.at===null?t('wpf_last_verified_unknown')
          :timeLabel(t,'wpf_last_verified',{time:timeStamp(state.past.at)}),' · ',t(state.past.valueKey)));
        if(state.past.operationId&&state.past.operationId!==state.operationId)past.append(h('p',{},operationLink(state.past.operationId)));
        value.append(past);
      }
    }
    const gap=data.smallest_gap,details=[];
    if(gap){
      const action=gap.fix?.action;
      if(['setup.host','setup.repository'].includes(action)&&gap.fix.route==='POST /api/v1/operations'&&gap.fix.ui_hash==='#/settings?section=connection'){
        const from=internalFrom(location.hash);
        details.push(h('a',{href:gap.fix.ui_hash+(from?'&from='+encodeURIComponent(from):''),onclick:()=>{try{sessionStorage.setItem(returnKey,row.profile_id);}catch{}}},t(action==='setup.host'?'wpf_setup_host':'wpf_setup_repository')));
      }else{
        const key=gap.reason==='git_execution_location_changed'?'wpf_git_changed':gap.reason?.startsWith('local_git_')?'wpf_local_git'
          :['host_unreachable','host_not_refreshed'].includes(gap.reason)?'wpf_gap_observation'
          :gap.reason==='action_unavailable'?'wpf_action_unavailable':'wpf_unverified';
        details.push(h('p',{class:'muted'},t(key)));
      }
      const gapAt=profileTimestamp(gap.last_success_at);
      if(gapAt!==null&&!shownTimes.has(gapAt))details.push(timeStamp(gapAt));
    }
    const readAt=profileTimestamp(data.observed_at);
    const content=h('div',{'data-work-profile-readiness':'',tabindex:-1},h('h4',{},t('wpf_coding')),
      ...(readAt===null?[]:[h('p',{'data-work-profile-read-at':''},timeLabel(t,'wpf_read_at',{time:timeStamp(readAt)}))]),
      h('p',{class:'muted','data-work-profile-next-turn':''},h('strong',{},t('wpf_next_turn')+' · '),t(nextKey)),
      h('p',{class:'muted'},t('wpf_locked')),list,
      ...(data.states.can_start_own.value!=='ready'?[h('p',{class:'muted'},t('wpf_unverified'))]:[]),
      ...(gap?[h('div',{'data-work-profile-gap':''},...details)]:[]),
      h('h4',{},t('wpf_pm')),hasEffectivePmRoute(data.pm_analysis)
        ?renderEffectivePmRoute(h('div',{'data-work-profile-pm':''}),{h,t,document:data.pm_analysis,readyKey:'wpf_pm_preflight'})
        :h('p',{},t(data.pm_analysis?.pm_ready===true?'wpf_pm_preflight':'wpf_pm_unverified')));
    if(renewal)patch(facts,content);else facts.replaceChildren(...content.childNodes);
    const gapName=states.find(name=>data.states[name].value!=='ready');
    announcement.textContent=[`${t('wpf_next_turn')}: ${t(nextKey)}`,
      gapName?`${t('wpf_'+gapName)}: ${t('wpf_'+data.states[gapName].value)}. ${t('wpf_unverified')}`:''].filter(Boolean).join(' ');
    verified={profile_id:row.profile_id,revision:row.revision,digest:row.digest};
    request()?.update();
    for(const [node,left,top] of scroll){node.scrollLeft=left;node.scrollTop=top;}
  }
  async function read(row,ticket,renewal=false){
    const data=await api('GET',`${base}/${row.profile_id}/readiness`);if(!valid(ticket))return;
    if(data.contract!=='work-profile-readiness/v1'||data.workspace_id!==workspaceId||data.profile_id!==row.profile_id
      ||data.revision!==row.revision||data.digest!==row.digest||data.calls_bat!==false
      ||states.some(name=>!values.includes(data.states?.[name]?.value)))throw Error(t('wpf_invalid'));
    const at=deadline(data);
    // A failed host attempt can advance observed_at without moving the
    // success-based deadline. Store every validated read; the spent deadline's
    // outcome controls each state's wording without discarding newer evidence.
    const prior=renewed.get(renewalKey(row,at));
    observation={data,row,ticket,error:prior?.error||null};
    display(data,row,Boolean(renewal));scheduleExpiry();
  }
  select.addEventListener('change',async()=>{
    if(request()?.locked()){select.value=selected()?.profile_id||'';return;}
    const ticket=++serial,row=select.value?checkedWorkProfile(rows.get(select.value)):null;
    clearObservation();verified=null;facts.replaceChildren();status.replaceChildren();announcement.textContent='';
    request().set('profile',row?JSON.stringify(row):'');onChange(row,true);
    if(row){try{await read(row,ticket);}catch(error){if(valid(ticket))handle(error);}}
  });
  function handle(error){
    clearObservation();verified=null;facts.replaceChildren();announcement.textContent='';
    if([403,404].includes(error.status)||error.code==='PM_WORK_PROFILE_WITHDRAWN')forget('wpf_withdrawn');
    else status.replaceChildren(errorBox(error,{within:status}));
  }
  async function load(append=false){
    const ticket=++serial;clearObservation();verified=null;facts.replaceChildren();announcement.textContent='';
    if(!available()||!active()){if(!available()&&selected())forget('wpf_withdrawn');update();return;}
    loading=true;update();status.replaceChildren();
    try{
      const data=await api('GET',`${base}?limit=50${append&&cursor!==null?'&cursor='+cursor:''}`);if(!valid(ticket))return;
      if(!Array.isArray(data.profiles)||data.profiles.some(row=>!checkedWorkProfile(row))
        ||data.next_cursor!==null&&(!Number.isSafeInteger(data.next_cursor)||data.next_cursor<0))throw Error(t('wpf_invalid'));
      if(!append)rows.clear();for(const row of data.profiles)rows.set(row.profile_id,row);cursor=data.next_cursor;options();
      const old=selected();if(old){
        let row=rows.get(old.profile_id);if(!row){row=await api('GET',`${base}/${old.profile_id}`);if(!valid(ticket))return;if(!checkedWorkProfile(row)||row.profile_id!==old.profile_id)throw Error(t('wpf_invalid'));rows.set(row.profile_id,row);options();}
        const checked=checkedWorkProfile(row);
        if(JSON.stringify(old)!==JSON.stringify(checked)){
          status.replaceChildren(h('p',{class:'muted'},t('wpf_changed')));
          if(!request()?.locked()){request().set('profile',JSON.stringify(checked));onChange(checked,false);}
        }
        await read(checked,ticket);
      }
    }catch(error){if(valid(ticket))handle(error);}
    finally{if(ticket===serial){loading=false;update();}}
  }
  options();
  return {box,selected,update,load,verified:()=>{const row=selected();return !row||Boolean(verified&&verified.profile_id===row.profile_id&&verified.revision===row.revision&&verified.digest===row.digest);},
    withdraw(){serial++;clearObservation();loading=false;verified=null;rows.clear();facts.replaceChildren();status.replaceChildren();announcement.textContent='';box.hidden=true;},
    dispose(){disposed=true;serial++;clearObservation();document.removeEventListener('health-display-tick',paintValues);document.removeEventListener('visibilitychange',scheduleExpiry);facts.replaceChildren();},
    focusOnReturn(){try{if(verified&&sessionStorage.getItem(returnKey)===verified.profile_id&&facts.isConnected){sessionStorage.removeItem(returnKey);facts.focus();}}catch{}}};
}
