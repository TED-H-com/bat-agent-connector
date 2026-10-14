import {place} from './ui/dom.js';
import {button, disclosure, emptyState, iconButton, sectionCard, skeleton, statusPill} from './ui/components.js';
// Read only saved central receipts for the selected, explicitly associated work.
export function workspaceWorkDeliveries({h,t,api,alive,workspaceId,errorBox}) {
  let workId=null, sequence=0, busy=false, cursor=null, rows=[], packages=[];
  const status=h('div',{role:'status'}), facts=h('div');
  const refreshButton=iconButton({icon:'refresh-cw', label:t('wd_refresh'), onClick:()=>void refresh()});
  const more=button({variant:'secondary', label:t('wd_more'), hidden:true, onClick:()=>void read(true)});
  const head=h('div',{class:'pm-tool-head'},refreshButton,more);
  const element=h('details',{'data-pm-work-deliveries':'',class:'pm-spec-card',hidden:true},h('summary',{},t('wd_title')),
    sectionCard({body:h('div',{},head,status,facts)}));
  disclosure({element});
  const valid=ticket=>{alive();return ticket===sequence;};
  const controls=()=>{refreshButton.disabled=busy||!workId;more.disabled=busy;more.hidden=cursor===null;};
  const fact=(name,value)=>h('p',{},h('strong',{},t(name)+': '),value===null||value===undefined||value===''?t('wd_unknown'):String(value));
  const boolean=value=>value===true?t('wd_yes'):value===false?t('wd_no'):t('wd_unknown');
  const mono=(name,value)=>value===null||value===undefined||value===''?null:h('p',{class:'mono'},t(name)+': '+String(value));
  const namedStatus=value=>{const key='wd_status_'+value,label=t(key);return label===key?String(value??''):label;};
  const deployTones={verified:'ok',succeeded:'ok',failed:'bad',cancelled:'warn',needs_attention:'warn',uncertain:'warn',drift:'warn',unverified:'neutral',superseded:'neutral'};
  function deployPill(state){
    if(state===null||state===undefined||state==='')return h('span',{class:'ui-pill tone-neutral','data-status':'unknown'},t('wd_unknown'));
    const key='wd_state_'+state,label=t(key);
    if(label===key)return h('span',{class:'ui-pill tone-neutral','data-status':'unknown'},t('wd_unknown'));
    return h('span',{class:`ui-pill tone-${deployTones[state]||'neutral'}`,'data-status':state},label);
  }
  function referenceLine(row){
    const repo=row.target?.repository?String(row.target.repository):'';
    const number=row.target?.pull_number;
    const pr=number===null||number===undefined||number===''?'':`PR #${number}`;
    const text=[repo,pr].filter(Boolean).join(' · ');
    return text?h('p',{},text):null;
  }
  function showHead(ok){head.replaceChildren(...(ok?[statusPill('not_assessed'),more]:[]),refreshButton);}
  function render() {
    const notes=[];
    place(facts,'replaceChildren',
      packages.length?h('p',{class:'muted'},t('wd_packages')):null,
      ...packages.map(ref=>{
        notes.push(mono('wd_package',ref.package_id),mono('wd_digest',ref.document_digest));
        return h('div',{class:'pm-delivery-package'},h('a',{href:`#/op/${encodeURIComponent(ref.operation_id)}`},t('wd_original')));
      }),
      ...rows.map(row=>{
        if(row.integration){
          notes.push(mono('wd_head',row.integration.new_head));
          for(const source of row.integration.sources||[])notes.push(mono('wd_pinned',source.pinned_sha?(source.pinned_sha+' · '+namedStatus(source.status)):null));
        }
        if(row.merge)notes.push(mono('wd_merged_sha',row.merge.merged_sha));
        const state=row.deployment?.state;
        if(row.deployment){
          notes.push(mono('wd_source_sha',row.deployment.source_sha),mono('wd_run',row.deployment.run_id));
          if(state!==null&&state!==undefined&&state!==''&&t('wd_state_'+state)===('wd_state_'+state))notes.push(mono('wd_deploy_state',state));
        }
        return h('article',{class:'pm-delivery'},
          h('div',{class:'pm-tool-head'},h('h4',{},t('wd_action_'+row.action)),row.merge?.merged===true?statusPill('merged'):null,row.deployment?deployPill(state):null),
          fact('wd_status',namedStatus(row.status)),
          h('a',{href:`#/op/${encodeURIComponent(row.operation_id)}`},t('wd_operation')),
          referenceLine(row),
          row.integration?fact('wd_pushed',boolean(row.integration.pushed)):null,
          row.merge?h('div',{},fact('wd_merged',boolean(row.merge.merged)),fact('wd_merge_verified',boolean(row.merge.verified))):null,
          row.deployment?h('div',{},fact('wd_environment',row.deployment.environment),
            fact('wd_deploy_verified',boolean(row.deployment.verified)),fact('wd_current',boolean(row.deployment.is_current))):null);
      }),
      disclosure({label:t('wd_details'),body:h('div',{},...notes,h('p',{class:'muted'},t('wd_boundary')))}),
      !rows.length?emptyState({title:t('wd_empty'),heading:'h3'}):null);
    showHead(true);
  }
  function check(data,id) {
    if(data?.workspace_id!==workspaceId||data.work_item_id!==id||data.contract!=='pm-engineering-delivery/v1'
      ||data.requirement_acceptance!=='not_evaluated_by_delivery'||!Array.isArray(data.original_packages)||!Array.isArray(data.deliveries)
      ||typeof data.source_visibility_filtered!=='boolean'||data.next_after!==null&&(!Number.isSafeInteger(data.next_after)||data.next_after<=0))throw Error(t('wd_invalid'));
    for(const ref of data.original_packages)if(ref.workspace_id!==workspaceId||!/^pmwp_[0-9a-f]{32}$/.test(ref.package_id)
      ||!/^op_[0-9a-f]{32}$/.test(ref.operation_id)||! /^[0-9a-f]{64}$/.test(ref.document_digest))throw Error(t('wd_invalid'));
    for(const row of data.deliveries)if(!/^op_[0-9a-f]{32}$/.test(row.operation_id)||row.requirement_acceptance!=='not_evaluated_by_delivery'
      ||!['integration.preview','integration.apply','integration.handoff','github.pr.merge','delivery.merge_and_deploy','deployment.start','deployment.rollback'].includes(row.action))throw Error(t('wd_invalid'));
    return data;
  }
  async function read(append=false) {
    if(!workId||busy)return;
    const id=workId,after=append?cursor:0,ticket=++sequence;
    if(append&&after===null)return;
    busy=true;if(!append){rows=[];packages=[];cursor=null;facts.replaceChildren();}
    controls();status.replaceChildren(skeleton(2));
    try {
      const data=check(await api('GET',`/pm/workspaces/${encodeURIComponent(workspaceId)}/work/${encodeURIComponent(id)}/deliveries?after=${after}&limit=20`),id);
      if(!valid(ticket)||workId!==id)return;
      if(data.next_after!==null&&data.next_after<=after)throw Error(t('wd_invalid'));
      packages=data.original_packages;rows=append?[...rows,...data.deliveries]:data.deliveries;cursor=data.next_after;
      render();status.replaceChildren(...(data.source_visibility_filtered?[statusPill('partial'),h('p',{class:'muted'},t('wd_filtered'))]:[]));
    }catch(error){try{if(!valid(ticket))return;}catch{return;}
      rows=[];packages=[];cursor=null;facts.replaceChildren();status.replaceChildren();
      head.replaceChildren(h('div',{class:'pm-tool-note'},errorBox(error),h('p',{class:'muted'},t('wd_unavailable'))),refreshButton);
    }finally{if(ticket===sequence){busy=false;controls();}}
  }
  function withdraw(){sequence++;busy=false;rows=[];packages=[];cursor=null;facts.replaceChildren();status.replaceChildren();controls();}
  function setWork(id){withdraw();workId=/^wi_[0-9a-f]{20}$/.test(id||'')?id:null;element.hidden=!workId;controls();if(workId)void read();}
  async function refresh(){withdraw();if(workId)await read();}
  return{element,setWork,refresh,withdraw:()=>setWork(null)};
}
