import {h} from './ui/dom.js';
import {button, disclosure, sectionCard} from './ui/components.js';
import {operationStatusLabel} from './global-labels.js';
import {sessionHref} from './session-navigation.js';
import {ADOPT_START,landingPrefix,landingNavigationKey} from './pm-adopt-start.js';

const terminal = status => ['succeeded','failed','cancelled'].includes(status);
const opId = value => typeof value === 'string' && /^op_[0-9a-f]{32}$/.test(value);
const workId = value => typeof value === 'string' && /^wi_[0-9a-f]{20}$/.test(value);
const same = (a,b) => JSON.stringify(a) === JSON.stringify(b);
export function adoptStartLanding({t,api,alive,namespace,workspaceId,caps,errorBox}) {
  const prefix=landingPrefix(namespace,workspaceId), body=h('div'), status=h('div',{role:'status'});
  const element=sectionCard({body:[body,status]});element.dataset.adoptStartLanding='';element.hidden=true;
  let serial=0,current=null,disposed=false,busy=false,shown=null;
  const valid=own=>{try{alive();return !disposed&&serial===own;}catch{return false;}};
  const navigationKey=id=>landingNavigationKey(namespace,workspaceId,id);
  const stopFollowing=id=>{try{sessionStorage.removeItem(navigationKey(id));}catch{/* navigation is optional */}};
  const requestedWork=id=>{try{return sessionStorage.getItem(navigationKey(id));}catch{return null;}};
  const forget=id=>{try{localStorage.removeItem(prefix+id);}catch{/* this is only a local navigation hint */}stopFollowing(id);};
  const selectedWork=()=>{const [path,query]=location.hash.split('?'),params=new URLSearchParams(query);
    return path===`#/pm/${encodeURIComponent(workspaceId)}/board`&&params.getAll('select').length===1?params.get('select'):null;};
  const fail=()=>Error(t('loop_preview_mismatch'));
  function bookmark() {
    const selected=new URLSearchParams(location.hash.split('?')[1]).get('select');
    if(!workId(selected))return null;
    if(current?.work_item_ids.includes(selected))return current;
    for(const key of Object.keys(localStorage).filter(key=>key.startsWith(prefix)).sort().reverse()) {
      try{
        const row=JSON.parse(localStorage.getItem(key));
        if(row?.version!==1||!opId(row.operation_id)||key!==prefix+row.operation_id
            ||!/^pmt_[0-9a-f]{32}$/.test(row.thread_id)||!Array.isArray(row.work_item_ids)
            ||row.work_item_ids.length<1||row.work_item_ids.length>10||row.work_item_ids.some(id=>!workId(id))
            ||new Set(row.work_item_ids).size!==row.work_item_ids.length)continue;
        if(row.work_item_ids.includes(selected))return row;
      }catch{/* malformed local hints confer no authority */}
    }
    return null;
  }
  function checked(op,hint) {
    if(!op||op.operation_id!==hint.operation_id||op.action!==ADOPT_START||op.target?.workspace_id!==workspaceId
        ||op.target?.thread_id!==hint.thread_id||op.actor!==caps()?.actor
        ||!same(op.params?.works?.map(row=>row.work_item_id),hint.work_item_ids))throw fail();
    if(op.result){const result=op.result;
      if(result.workspace_id!==workspaceId||result.thread_id!==hint.thread_id||typeof result.adopted!=='boolean'
          ||!Array.isArray(result.works)||!same(result.works.map(row=>row.work_item_id),hint.work_item_ids)
          ||result.works.some(row=>row.start_operation_id!=null&&!opId(row.start_operation_id)))throw fail();
    }
    return op;
  }
  const boardLink=id=>`#/pm/${encodeURIComponent(workspaceId)}/board?select=${encodeURIComponent(id)}`;
  async function steer(verb,operation) {
    if(busy||!current)return;
    const own=serial;busy=true;
    try{
      alive();
      // Resume is this tab's fresh request to follow the original Work. Its
      // submission hint may have been withdrawn on an earlier visit. Save the
      // intent before the await so a reload can follow an already-ready receipt;
      // render and withdraw still cancel it when the person leaves this landing.
      if(verb==='resume'&&selectedWork()===current.work_item_ids[0]){
        try{sessionStorage.setItem(navigationKey(operation.operation_id),current.work_item_ids[0]);}catch{/* the ordinary Work link remains available */}
      }
      for(const button of body.querySelectorAll('button'))button.disabled=true;
      const value=await api('POST',`/operations/${operation.operation_id}/${verb}`,{});
      if(!valid(own))return;
      checked(value.operation,current);await refresh();
    }catch(error){if(valid(own))status.replaceChildren(errorBox(error,{sourceRefresh:false, within:status}));}
    finally{busy=false;if(valid(own))for(const button of body.querySelectorAll('button'))button.disabled=false;}
  }
  function render(operation,children,names) {
    const result=operation.result;
    const key=!result?'loop_queued':result.stage==='cancelled'&&result.adopted?'loop_cancelled_adopted':'loop_'+result.stage;
    const label=!result&&operation.status!=='accepted'?operationStatusLabel(operation.status):t(key)===key?operationStatusLabel(operation.status):t(key);
    const actions=h('div',{class:'actions'},h('a',{href:`#/op/${operation.operation_id}`},t('pm_operation')),
      h('a',{href:`#/pm/${workspaceId}/discussions/${current.thread_id}`},t('loop_return_discussion')),
      button({size:'sm',label:t('loop_close'),onClick:()=>{forget(operation.operation_id);withdraw();}}));
    const list=h('div');let hasUnknown=false,firstLive=null;
    for(const row of result?.works||[]) {
      const child=children.get(row.work_item_id), unknown=child?child.result?.stage==='start_unknown'||child.status==='uncertain':row.stage==='start_unknown';
      hasUnknown=hasUnknown||unknown;
      const stage=unknown?'loop_work_unknown':child?.status==='succeeded'?'loop_work_started'
        :child&&!terminal(child.status)?'loop_work_starting':'loop_not_started';
      const links=h('div',{class:'actions'},h('a',{href:boardLink(row.work_item_id)},t('wst_open_work')));
      if(row.start_operation_id)links.append(h('a',{href:`#/op/${row.start_operation_id}`},t(unknown?'loop_original':'wst_view_operation')));
      // An unresolved handoff is reconciled at its original operation. Resuming
      // a parent only waits for that proof; it must not promise a fresh start.
      if(unknown&&opId(row.handoff_operation_id))links.append(h('a',{href:`#/op/${row.handoff_operation_id}`},t('loop_reconcile')));
      const session=child?.result?.session,attempt=child?.result?.attempt_id||row.attempt_id;
      if(!unknown&&child?.status==='succeeded'&&typeof session?.host==='string'&&typeof session?.session_id==='string'
          &&/^att_[0-9a-f]{32}$/.test(attempt)){
        const href=sessionHref({host:session.host,sessionId:session.session_id,from:'board',workspaceId,workItemId:row.work_item_id,attemptId:attempt});
        links.append(h('a',{'data-adopt-live':'',href},t('wst_open_live_output')));
        if(row.work_item_id===current.work_item_ids[0])firstLive=href;
      }
      if(result.adopted&&terminal(operation.status)&&!unknown&&(!child||terminal(child.status)&&child.status!=='succeeded'))
        links.append(h('a',{href:boardLink(row.work_item_id),'data-adopt-start-one':''},t('loop_start_work')));
      const code=child?.error_code||row.error_code, reasonKey='wst_error_'+code;
      const section=h('section',{class:'pm-adopt-work'},h('h4',{},names.get(row.work_item_id)||t('ui_work_item')),h('p',{},t(stage)),links);
      if(code){if(t(reasonKey)!==reasonKey)section.append(h('p',{},t(reasonKey)));section.append(disclosure({label:t('pd_details'),body:h('p',{class:'mono'},code)}));}
      list.append(section);
    }
    const authorized=operation.actor===caps()?.actor&&['observe','manage','start'].every(scope=>caps()?.scopes?.includes(scope))
      &&caps()?.actions?.some(row=>row.action===ADOPT_START&&row.allowed===true);
    const unknown=hasUnknown;
    if(authorized&&!terminal(operation.status)) {
      if(operation.status==='needs_attention'&&!unknown&&!operation.cancel_requested)
        actions.append(button({label:t('loop_resume'),onClick:()=>void steer('resume',operation)}));
      if(!operation.cancel_requested)actions.append(button({label:t('loop_cancel'),'aria-label':t(unknown?'loop_cancel_unknown':'loop_cancel'),onClick:()=>void steer('cancel',operation)}));
    }
    const open=[...body.querySelectorAll('details')].map(node=>node.open);body.replaceChildren(h('h3',{},label),
      ...(result?.adopted&&terminal(operation.status)&&operation.status!=='succeeded'?[h('p',{},t('loop_saved_plan'))]:[]),list,actions);
    [...body.querySelectorAll('details')].forEach((node,index)=>node.open=open[index]||false);
    element.hidden=false;shown=operation.operation_id;
    const requested=requestedWork(operation.operation_id),selected=selectedWork();
    const follow=firstLive&&operation.status==='succeeded'&&result?.stage==='started'&&!operation.cancel_requested
      &&requested===current.work_item_ids[0]&&selected===requested;
    if(requested&&selected!==requested)stopFollowing(operation.operation_id);
    if(terminal(operation.status))forget(operation.operation_id);
    if(follow)location.hash=firstLive;
  }
  async function refresh() {
    const hint=bookmark();if(!hint){withdraw();return;}
    const own=++serial;current=hint;status.replaceChildren();
    try{
      const operation=checked((await api('GET',`/operations/${hint.operation_id}`)).operation,hint);if(!valid(own))return;
      const children=new Map(),names=new Map();
      for(const row of operation.result?.works||[]) {
        const value=await api('GET',`/work-items/${row.work_item_id}`);if(!valid(own))return;
        const work=value.work_item;if(work?.work_item_id!==row.work_item_id||typeof work.title!=='string')throw fail();names.set(row.work_item_id,work.title);
        if(row.start_operation_id){const child=(await api('GET',`/operations/${row.start_operation_id}`)).operation;if(!valid(own))return;
          if(child?.operation_id!==row.start_operation_id||child.action!=='work.start'||child.target?.workspace_id!==workspaceId||child.target?.work_item_id!==row.work_item_id||child.actor!==operation.actor)throw fail();
          children.set(row.work_item_id,child);
        }
      }
      if(!valid(own))return;render(operation,children,names);
    }catch(error){if(!valid(own))return;body.replaceChildren();if([403,404].includes(error.status)){forget(hint.operation_id);withdraw();}
      else{status.replaceChildren(errorBox(error,{within:status}),button({label:t('ui_retry'),onClick:()=>void refresh()}));element.hidden=false;}}
  }
  function withdraw({preserveNavigation=false}={}) {
    // An earlier asynchronous view can finish disposing after its replacement
    // mounts on the same selected Work. The tab's destination belongs to that
    // selection, not the retiring instance. Leaving it still cancels following.
    const keep=preserveNavigation&&current&&requestedWork(current.operation_id)===current.work_item_ids[0]
      &&selectedWork()===current.work_item_ids[0];
    serial++;if(current&&!keep)stopFollowing(current.operation_id);
    current=null;shown=null;body.replaceChildren();status.replaceChildren();element.hidden=true;
  }
  return {element,refresh,withdraw,pending:()=>Boolean(current)&&!shown,dispose:()=>{disposed=true;withdraw({preserveNavigation:true});}};
}
