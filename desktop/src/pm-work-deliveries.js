import {place} from './ui/dom.js';
import {button, disclosure, emptyState, iconButton, sectionCard, skeleton, statusPill} from './ui/components.js';
// Read only saved central receipts for the selected, explicitly associated work.
export function workspaceWorkDeliveries({h,t,api,alive,workspaceId,errorBox}) {
  let workId=null, sequence=0, busy=false, cursor=null, rows=[], packages=[];
  const status=h('div',{role:'status'}), facts=h('div');
  const refreshButton=iconButton({icon:'refresh-cw', label:t('wd_refresh'), onClick:()=>void refresh()});
  const more=button({variant:'secondary', label:t('wd_more'), hidden:true, onClick:()=>void read(true)});
  const element=h('details',{'data-pm-work-deliveries':'',class:'pm-spec-card',hidden:true},h('summary',{},t('wd_title')),
    sectionCard({body:h('div',{},h('div',{class:'pm-tool-head'},refreshButton,more),status,facts)}));
  disclosure({element});
  const valid=ticket=>{alive();return ticket===sequence;};
  const controls=()=>{refreshButton.disabled=busy||!workId;more.disabled=busy;more.hidden=cursor===null;};
  const fact=(name,value)=>h('p',{},h('strong',{},t(name)+': '),value===null||value===undefined||value===''?t('wd_unknown'):String(value));
  const boolean=value=>value===true?t('wd_yes'):value===false?t('wd_no'):t('wd_unknown');
  const mono=(name,value)=>value===null||value===undefined||value===''?null:h('p',{class:'mono'},t(name)+': '+String(value));
  const namedStatus=value=>{const key='wd_status_'+value,label=t(key);return label===key?String(value??''):label;};
  const details=(...nodes)=>nodes.some(Boolean)?disclosure({label:t('wd_details'),body:h('div',{},...nodes)}):null;
  function render() {
    place(facts,'replaceChildren',
      h('div',{class:'pm-tool-head'},statusPill('not_assessed')),
      packages.length?h('p',{class:'muted'},t('wd_packages')):null,
      ...packages.map(ref=>h('div',{class:'pm-delivery-package'},
        h('a',{href:`#/op/${encodeURIComponent(ref.operation_id)}`},t('wd_original')),
        details(mono('wd_package',ref.package_id),mono('wd_digest',ref.document_digest)))),
      ...rows.map(row=>{
        const commits=[];
        if(row.integration){
          commits.push(mono('wd_head',row.integration.new_head));
          for(const source of row.integration.sources||[])commits.push(mono('wd_pinned',source.pinned_sha?(source.pinned_sha+' · '+namedStatus(source.status)):null));
        }
        if(row.merge)commits.push(mono('wd_merged_sha',row.merge.merged_sha));
        if(row.deployment){
          commits.push(mono('wd_source_sha',row.deployment.source_sha));
          commits.push(mono('wd_run',row.deployment.run_id));
        }
        return h('article',{class:'pm-delivery'},
          h('div',{class:'pm-tool-head'},h('h4',{},t('wd_action_'+row.action)),row.merge?.merged===true?statusPill('merged'):null),
          fact('wd_status',namedStatus(row.status)),
          h('a',{href:`#/op/${encodeURIComponent(row.operation_id)}`},t('wd_operation')),
          row.target?.repository?h('p',{},String(row.target.repository)):null,
          row.target?.pull_number?fact('wd_pull',row.target.pull_number):null,
          row.integration?fact('wd_pushed',boolean(row.integration.pushed)):null,
          row.merge?h('div',{},fact('wd_merged',boolean(row.merge.merged)),fact('wd_merge_verified',boolean(row.merge.verified))):null,
          row.deployment?h('div',{},fact('wd_environment',row.deployment.environment),fact('wd_deploy_state',row.deployment.state),
            fact('wd_deploy_verified',boolean(row.deployment.verified)),fact('wd_current',boolean(row.deployment.is_current))):null,
          details(...commits));
      }),
      details(h('p',{class:'muted'},t('wd_boundary'))),
      !rows.length?emptyState({title:t('wd_empty'),heading:'h3'}):null);
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
      rows=[];packages=[];cursor=null;facts.replaceChildren();status.replaceChildren(errorBox(error),h('p',{class:'muted'},t('wd_unavailable')));
    }finally{if(ticket===sequence){busy=false;controls();}}
  }
  function withdraw(){sequence++;busy=false;rows=[];packages=[];cursor=null;facts.replaceChildren();status.replaceChildren();controls();}
  function setWork(id){withdraw();workId=/^wi_[0-9a-f]{20}$/.test(id||'')?id:null;element.hidden=!workId;controls();if(workId)void read();}
  async function refresh(){withdraw();if(workId)await read();}
  return{element,setWork,refresh,withdraw:()=>setWork(null)};
}
